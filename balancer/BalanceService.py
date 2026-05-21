# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import json
import os
import hashlib
import queue as _queue
import signal
from datetime import datetime
from threading import Lock

from flask import Flask, request, Response, stream_with_context

from balancer.balancer import DynamicBalancer
from db.DatabaseModel import AIAppPriority, DBStatus, init_database
from monitor.monitor_api import monitor_bp, register_system_pressure_monitor, _start_snapshot_cleanup_task
from monitor.system_info import preload_static_info, shutdown_gpu_usage
from utils.app_utils import adjust_oom_priority, callback_manager, check_app_running_status, fetch_all_apps, get_priority_value
from utils.http_utils import RetCode, construct_response
from utils.logger import logger

app = Flask(__name__)
app.register_blueprint(monitor_bp)
_start_snapshot_cleanup_task()

CERT_FILE = './b_server.crt'
KEY_FILE = './b_server.key'

_service_lock = Lock()
_service = None  # Singleton service instance
_shutdown_lock = Lock()
_shutdown_started = False


class DynamicService:
    """Encapsulates the core balancer logic as a managed service."""

    def __init__(self):
        self.balancer = DynamicBalancer()
        # Share the controller's SystemPressureMonitor with the monitor API so that
        # both use the same instance (including is_limited_app_dominant state).
        register_system_pressure_monitor(self.balancer.controlManager.system_pressure_monitor)
        self.rebuild_controlled_map()
        self.secret_hash = self._generate_secret_hash()  # Generate and store the hash
        logger.info("Service secret hash generated.")

    def _generate_secret_hash(self):
        """Generate a random number and hash it using SHA256."""
        random_number = os.urandom(16)  # Generate a secure random number
        return hashlib.sha256(random_number).hexdigest()

    def get_secret_hash(self):
        """Return the stored hash."""
        return self.secret_hash

    def start(self):
        self.balancer.start()

    def add_workload(self, priority, payload):
        """Delegate directly to the balancer."""
        self.balancer.add_workload(priority, payload)

    def cancel_relaunch(self, app_id):
        return self.balancer.cancel_relaunch_by_app_id(app_id)

    def resource_limit(self, app_id, app_name, priority, limit_overrides=None):
        return self.balancer.set_resource_limit(app_id, app_name, priority, limit_overrides=limit_overrides)

    def resource_limit_profile(self, app_id, app_name, priority):
        return self.balancer.get_resource_limit_profile(app_id, app_name, priority)

    def restore_resource(self, app_id):
        return self.balancer.set_restore_resource(app_id)

    def add_control(self, app_name):
        self.balancer.bpf_monitor.add_to_monitorlist(app_name)

    def remove_control(self, app_name):
        self.balancer.bpf_monitor.remove_from_monitorlist(app_name)

    def get_controlled_list(self):
        return self.balancer.bpf_monitor.get_monitored_apps()

    def rebuild_controlled_map(self):
        self.balancer.bpf_monitor.rebuild_controlled_map()

    def check_running_apps(self):
        return self.balancer.bpf_monitor.scan_already_running_apps()

    def shutdown(self):
        self.balancer.shutdown()
        shutdown_gpu_usage()


def start_service():
    """Initialize the service and register OS signal handlers."""
    global _service
    with _service_lock:
        if _service is None:
            logger.info("Initializing DynamicService for the first time")
            _service = DynamicService()
            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)
            _service.start()
        else:
            logger.debug("DynamicService already initialized, skipping")
    return _service


def _handle_signal(signum, frame):
    # Keep signal handler minimal and async-signal-safe: no logging/subprocess here.
    # Actual shutdown is handled in main() finally block.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise KeyboardInterrupt


def _shutdown_service_once():
    global _shutdown_started
    with _shutdown_lock:
        if _shutdown_started:
            return
        _shutdown_started = True

    try:
        if _service:
            _service.shutdown()
    except Exception as exc:
        logger.error(f"Service shutdown failed: {exc}")

    try:
        reset_app_status()
    except Exception as exc:
        logger.error(f"Reset app status failed during shutdown: {exc}")


def reset_app_status():
    """Reset all application statuses to 'NA'."""
    try:
        updated_count = AIAppPriority.update_all_records(
            status="NA",
            up_time=datetime.now()
        )
        if updated_count == 0:
            logger.warning("No records were updated currently.")
        else:
            logger.info(f"Reset {updated_count} app statuses to 'NA'")
    except Exception as e:
        logger.error(f"Failed to reset app statuses: {str(e)}")


@app.route('/auth/login', methods=['POST'])
def login():
    """Validate the user-provided token against the stored hash."""
    try:
        data = request.get_json()
        token = data.get('pwd')

        if not token:
            return construct_response(
                data={"authenticated": False},
                retcode=RetCode.ARGUMENT_ERROR,
                retmsg="Token is required"
            )

        # Hash the provided token and compare it with the stored hash
        hashed_token = hashlib.sha256(token.encode()).hexdigest()
        if hashed_token == _service.get_secret_hash():
            return construct_response(
                data={"authenticated": True},
                retmsg="Authentication successful"
            )
        else:
            return construct_response(
                data={"authenticated": False},
                retmsg="Invalid token"
            )
    except Exception as e:
        logger.error(f"Login failed: {str(e)}")
        return construct_response(
            data={"authenticated": False},
            retcode=RetCode.EXCEPTION_ERROR,
            retmsg=str(e)
        )


@app.route('/task/add_workload', methods=['POST'])
def add_workload():
    """Add a workload with the given priority."""
    try:
        data = request.json
        _service.add_workload(
            priority=data['priority'],
            payload=data['payload']
        )
        return construct_response(
            retmsg="Workload added successfully",
            data={"status": "success"}
        )
    except Exception as e:
        logger.error(f"API error: {str(e)}")
        return construct_response(
            data={},
            retcode=RetCode.ARGUMENT_ERROR,
            retmsg=f"Invalid request: {str(e)}"
        )


@app.route('/app/get_apps', methods=['GET', 'POST'])
def get_apps():
    """Retrieve all system application entries and optionally sync them to the database."""
    try:
        data = request.get_json()
        store = data.get('store', False)
        app_list = fetch_all_apps()
        for app in app_list:
            if store:
                app_id = app["app_id"]
                existing_app = None

                try:
                    existing_app = AIAppPriority.query().where(AIAppPriority.app_id == app_id).get()
                except Exception:
                    pass

                if not existing_app:
                    AIAppPriority.insert_record(
                        id=app_id,
                        app_id=app_id,
                        name=app["name"],
                        priority=0,
                        controlled=False,
                        remark="",
                        cmdline=app["commandline"],
                        status="NA",
                        last_update_time=datetime.now()
                    )

        return construct_response(
            data=app_list,
            retmsg="Successfully retrieved app list"
        )
    except Exception as e:
        return construct_response(
            retcode=RetCode.EXCEPTION_ERROR,
            retmsg=str(e),
            data={}
        )


@app.route('/app/set_priority', methods=['POST'])
def set_priority():
    """Set the priority of an application and update the database."""
    try:
        data = request.get_json()
        app_id = data.get('app_id')
        priority = data.get('priority')

        if not all([app_id, priority]):
            return construct_response(
                data={},
                retcode=RetCode.ARGUMENT_ERROR,
                retmsg="Missing required parameters"
            )

        result = AIAppPriority.update_record(
            id=app_id,
            priority=priority,
            up_time=datetime.now()
        )

        logger.info(f"Set priority result for app_id={app_id}: {result}")

        if result == DBStatus.NOT_FOUND:
            return construct_response(
                data={},
                retcode=RetCode.NOT_EXISTING,
                retmsg="Application record not found in database"
            )

        _service.rebuild_controlled_map()
        app_record = AIAppPriority.query().where(AIAppPriority.app_id == app_id).get()
        if app_record:
            logger.debug(f"Updating OOM priority for app_id={app_id}, name={app_record.name}, priority={priority}, "
                         f"cmdline={app_record.cmdline}")
            adjust_oom_priority(app_id, app_record.name, priority, app_record.cmdline)

        return construct_response(
            data={},
            retmsg="Priority updated successfully"
        )
    except Exception as e:
        return construct_response(
            data={},
            retcode=RetCode.EXCEPTION_ERROR,
            retmsg=str(e)
        )


@app.route('/app/get_priority_data', methods=['POST'])
def get_priority_data():
    """Retrieve the priority settings for an app by app_id or name."""

    try:
        data = request.get_json()
        app_id = data.get('app_id', "")
        name = data.get('app_name', "")


        query = AIAppPriority.query()
        conditions = []
        if app_id:
            conditions.append(AIAppPriority.app_id == app_id)
        if name:
            conditions.append(AIAppPriority.name == name)

        query = query.where(conditions[0])
        record = query.first()

        if not record:
            not_found_msg = "No matching application found"
            if app_id and name:
                not_found_msg = f"No application found with app_id={app_id} or name={name}"
            elif app_id:
                not_found_msg = f"No application found with app_id={app_id}"
            elif name:
                not_found_msg = f"No application found with name={name}"

            return construct_response(
                data={},
                retcode=RetCode.NOT_EXISTING,
                retmsg=not_found_msg
            )


        priority_data = {
            "id": record.id,
            "app_id": record.app_id,
            "name": record.name,
            "priority": record.priority,
            "cgroup": record.cgroup,
            "remark": record.remark,
            "cmdline": record.cmdline,
            "up_time": record.up_time.isoformat() if record.up_time else None,
            "status": record.status
        }

        return construct_response(
            data=priority_data,
            retmsg="Successfully retrieved priority data"
        )
    except Exception as e:
        return construct_response(
            data={},
            retcode=RetCode.EXCEPTION_ERROR,
            retmsg=str(e)
        )


@app.route('/app/set_to_control', methods=['POST'])
def set_to_control():
    """Enable or disable control for an application and register it with the monitor."""
    try:
        data = request.get_json()
        app_name = data.get('app_name', "")
        app_id = data.get('app_id', "")
        controlled = data.get('controlled', True)
        cgroup = data.get('cgroup', '')
        priority = data.get('priority', 0)
        remark = data.get('remark', '')
        cmdline = data.get('cmdline', '')

        _service.add_control(app_name)


        update_fields = dict(
            controlled=controlled,
            priority=priority,
            cgroup=cgroup,
            remark=remark,
        )
        # Only persist name when a valid value was provided; never overwrite with an empty string
        if app_name and app_name.strip():
            update_fields["name"] = app_name
        result = AIAppPriority.update_record(id=app_id, **update_fields)

        if result == DBStatus.NOT_FOUND:
            AIAppPriority.insert_record(
                id=app_id,
                app_id=app_id,
                name=app_name,
                priority=priority,
                controlled=controlled,
                cgroup=cgroup,
                remark=remark,
                cmdline=cmdline,
                status="NA",
                last_update_time=datetime.now()
            )

        _service.rebuild_controlled_map()
        adjust_oom_priority(app_id, app_name, priority, cmdline)

        # After registering the app, probe whether it is already running so the
        # UI reflects the correct status immediately (without waiting for the next
        # BPF exec event).
        if controlled and app_id:
            status = check_app_running_status(app_id, app_name, cmdline)
            logger.info(f"set_to_control: initial status check for '{app_name}' → {status}")
            callback_manager.send_callback_notification({
                'app_id': app_id,
                'app_name': app_name,
                'status': status,
                'purpose': "app"
            }, store=True)

        return construct_response(
            data={
                "app_name": app_name,
                "controlled": controlled,
            },
            retmsg=f"App control {'enabled' if controlled else 'disabled'} and added to monitor"
        )
    except Exception as e:
        logger.error(f"Control set failed: {str(e)}")
        return construct_response(
            data={},
            retcode=RetCode.EXCEPTION_ERROR,
            retmsg=str(e)
        )


@app.route('/app/remove_from_control', methods=['POST'])
def remove_from_control():
    """Remove an application from the control list."""
    try:
        data = request.get_json()
        app_id = data.get('app_id', "")
        app_name = data.get('app_name', "")

        if not app_id and not app_name:
            return construct_response(
                data={},
                retcode=RetCode.ARGUMENT_ERROR,
                retmsg="Either app_id or app_name must be provided"
            )

        _service.remove_control(app_name if app_name else "")

        app_info = AIAppPriority.query().filter(AIAppPriority.app_id == app_id).first()

        logger.debug(f"remove_from_control: app_info: {app_info}")
        # restore oom score
        adjust_oom_priority(app_id, app_name, app_info.priority, app_info.cmdline, restore=True)

        AIAppPriority.update_record(
            id=app_id if app_id else "",
            controlled=False
        )

        _service.rebuild_controlled_map()
        return construct_response(
            data={
                "app_id": app_id,
                "app_name": app_name,
                "controlled": False
            },
            retmsg="App removed from control successfully"
        )
    except Exception as e:
        logger.error(f"Remove control failed: {str(e)}")
        return construct_response(
            data={},
            retcode=RetCode.EXCEPTION_ERROR,
            retmsg=str(e)
        )


@app.route('/app/get_controlled_app', methods=['POST'])
def get_controlled_app():
    """Return all controlled applications along with their current metadata."""
    try:
        controlled_apps = AIAppPriority.query().filter(AIAppPriority.controlled == True)

        if not controlled_apps:
            return construct_response(
                retcode=RetCode.NOT_EXISTING,
                retmsg="No controlled apps found",
                data=[]
            )

        # Build a lookup map from config/system apps so we can fill in metadata
        config_app_map = {a["app_id"]: a for a in fetch_all_apps()}

        result_data = []
        for app in controlled_apps:
            # Prefer the DB name, or fall back to the config-derived human-readable name
            cfg_app = config_app_map.get(app.app_id, {})
            app_name = app.name if app.name and app.name.strip() else (cfg_app.get("app_name") or cfg_app.get("name") or "")
            result_data.append({
                "app_id": app.app_id,
                "app_name": app_name,
                "controlled": app.controlled,
                "priority": app.priority,
                "oom_score": app.oom_score,
                "cmdline": app.cmdline,
                "cgroup": app.cgroup,
                "process_names": cfg_app.get("process_names", []) or [],
                "remark": app.remark,
                "status": app.status
            })

        return construct_response(
            data=result_data,
            retmsg=f"Found {len(result_data)} controlled apps"
        )
    except Exception as e:
        logger.error(f"Get controlled apps failed: {str(e)}")
        return construct_response(
            data={},
            retcode=RetCode.EXCEPTION_ERROR,
            retmsg=str(e)
        )


@app.route('/app/check_running_apps', methods=['POST'])
def check_running_apps():
    """Scan currently running processes to find managed apps that started before the balancer.

    This endpoint is called once when the UI balancer tab is first opened.  It
    uses psutil to inspect live processes and registers any monitored app that is
    already running so its status is reflected correctly in the UI.  Ongoing
    detection after this initial scan is handled by BPF as usual.
    """
    try:
        detected = _service.check_running_apps()
        return construct_response(
            data=detected,
            retmsg=f"Startup scan complete, detected {len(detected)} pre-existing monitored app(s)"
        )
    except Exception as e:
        logger.error(f"check_running_apps failed: {str(e)}")
        return construct_response(
            data=[],
            retcode=RetCode.EXCEPTION_ERROR,
            retmsg=str(e)
        )


@app.route('/app/get_pending_app', methods=['POST'])
def get_pending_app():
    """Return all applications currently in pending state, ordered by priority."""
    try:
        pending_apps = AIAppPriority.query().filter(AIAppPriority.status == "pending")

        if not pending_apps:
            return construct_response(
                retcode=RetCode.NOT_EXISTING,
                retmsg="No pending apps found",
                data=[]
            )

        logger.debug(f"Found {len(pending_apps)} pending apps in database, pending_apps: {pending_apps}")

        result_data = []
        for app in pending_apps:
            result_data.append({
                "app_id": app.app_id,
                "app_name": app.name,
                "controlled": app.controlled,
                "priority": app.priority,
                "oom_score": app.oom_score,
                "priority_value": get_priority_value(app.priority),
                "cgroup": app.cgroup,
                "remark": app.remark,
                "status": app.status
            })


        sorted_data = sorted(result_data, key=lambda x: -x["priority_value"])
        logger.debug(f"Sorted pending apps: {sorted_data}")

        return construct_response(
            data=sorted_data,
            retmsg=f"Found {len(sorted_data)} pending apps (sorted by priority DESC)"
        )
    except Exception as e:
        logger.error(f"Get pending apps failed: {str(e)}")
        return construct_response(
            data={},
            retcode=RetCode.EXCEPTION_ERROR,
            retmsg=str(e)
        )


@app.route('/app/set_oom_score', methods=['POST'])
def set_oom_score():
    """Set the OOM score for an application to protect it from the OOM killer."""
    try:
        data = request.get_json()
        app_id = data.get('app_id', "")

        if not app_id:
            return construct_response(
                data={},
                retcode=RetCode.ARGUMENT_ERROR,
                retmsg="app_id must be provided"
            )

        app_info = AIAppPriority.query().filter(AIAppPriority.app_id == app_id).first()

        logger.debug(f"set_oom_score: app_info: {app_info}")
        adjust_oom_priority(app_id, app_info.name, app_info.priority, app_info.cmdline)

        return construct_response(
            data={},
            retmsg="App OOM score set successfully"
        )
    except Exception as e:
        logger.error(f"Set OOM score failed: {str(e)}")
        return construct_response(
            data={},
            retcode=RetCode.EXCEPTION_ERROR,
            retmsg=str(e)
        )


@app.route('/app/cancel_relaunch', methods=['POST'])
def cancel_relaunch_app():
    """ Cancel relaunch for a specific app by app_id. """
    try:
        data = request.get_json()
        app_id = data.get('app_id', "")

        if not app_id:
            return construct_response(
                data={},
                retcode=RetCode.ARGUMENT_ERROR,
                retmsg="Either app_id must be provided"
            )

        result = _service.cancel_relaunch(app_id)

        try:
            update_db_result = AIAppPriority.update_record(
                id=app_id,
                status="stopped",
                up_time=datetime.now()
            )
        except Exception as db_error:
            logger.error(f"Update database failed for {app_id}: {str(db_error)}")
            update_db_result = False

        if result and update_db_result:
            return construct_response(
                data={"app_id": app_id},
                retmsg="Successfully found and canceled relaunch"
            )
        else:
            return construct_response(
                data={"app_id": app_id},
                retcode=RetCode.OPERATING_ERROR,
                retmsg="No matching app found or failed to cancel relaunch it"
            )
    except Exception as e:
        logger.error(f"Cancel relaunch failed: {str(e)}")
        return construct_response(
            data={},
            retcode=RetCode.EXCEPTION_ERROR,
            retmsg=str(e)
        )


@app.route('/app/resource_limit', methods=['POST'])
def app_resource_limit():
    """ Set resource limit for a specific app by app_id. """
    try:
        data = request.get_json()
        app_id = data.get('app_id', "")
        app_name = data.get('app_name', "")
        priority = data.get('priority', "")
        limit_overrides = data.get('limit_overrides')

        if not app_id and not app_name and not priority:
            return construct_response(
                data={},
                retcode=RetCode.ARGUMENT_ERROR,
                retmsg="app_id, app_name and priority must be provided"
            )

        result = _service.resource_limit(app_id, app_name, priority, limit_overrides=limit_overrides)

        if result:
            return construct_response(
                data={},
                retmsg="Successfully found and set resource limit"
            )
        else:
            return construct_response(
                data={},
                retcode=RetCode.OPERATING_ERROR,
                retmsg="No matching app found or failed to set resource limit"
            )
    except Exception as e:
        logger.error(f"Set resource limit failed: {str(e)}")
        return construct_response(
            data={},
            retcode=RetCode.EXCEPTION_ERROR,
            retmsg=str(e)
        )


@app.route('/app/resource_limit_profile', methods=['POST'])
def app_resource_limit_profile():
    """Get editable resource-limit profile (defaults + bounds) for UI."""
    try:
        data = request.get_json()
        app_id = data.get('app_id', "")
        app_name = data.get('app_name', "")
        priority = data.get('priority', "")

        if not app_id and not app_name:
            return construct_response(
                data={},
                retcode=RetCode.ARGUMENT_ERROR,
                retmsg="app_id or app_name must be provided"
            )

        profile = _service.resource_limit_profile(app_id, app_name, priority or "undefined")
        return construct_response(
            data=profile,
            retmsg="Successfully fetched resource limit profile"
        )
    except Exception as e:
        logger.error(f"Get resource limit profile failed: {str(e)}")
        return construct_response(
            data={},
            retcode=RetCode.EXCEPTION_ERROR,
            retmsg=str(e)
        )


@app.route('/app/resource_restore', methods=['POST'])
def app_resource_restore():
    """ Restore resource for a specific app by app_id. """
    try:
        data = request.get_json()
        app_id = data.get('app_id', "")

        if not app_id:
            return construct_response(
                data={},
                retcode=RetCode.ARGUMENT_ERROR,
                retmsg="app_id and app_name must be provided"
            )

        result = _service.restore_resource(app_id)

        if result:
            return construct_response(
                data={},
                retmsg="Successfully found and restored resource"
            )
        else:
            return construct_response(
                data={},
                retcode=RetCode.OPERATING_ERROR,
                retmsg="No matching app found or failed to restore resource"
            )
    except Exception as e:
        logger.error(f"Restore resource failed: {str(e)}")
        return construct_response(
            data={},
            retcode=RetCode.EXCEPTION_ERROR,
            retmsg=str(e)
        )


_SSE_HEARTBEAT_TIMEOUT = 30  # seconds between keep-alive comments when no events arrive


@app.route('/app/events', methods=['GET'])
def app_events():
    """Server-Sent Events stream for app status changes."""
    q = _queue.Queue()
    callback_manager.add_sse_client(q)

    def generate():
        try:
            yield "data: {\"type\": \"connected\"}\n\n"
            while True:
                try:
                    data = q.get(timeout=_SSE_HEARTBEAT_TIMEOUT)
                    yield f"data: {json.dumps(data)}\n\n"
                except _queue.Empty:
                    # Keep-alive comment
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            callback_manager.remove_sse_client(q)

    response = Response(
        stream_with_context(generate()),
        content_type='text/event-stream',
    )
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


def main():
    logger.info("Starting Balance Service...")
    if not os.path.exists(CERT_FILE) or not os.path.exists(KEY_FILE):
        logger.error(f"Certificate files not found: {CERT_FILE}, {KEY_FILE}, "
                     f"please check 'start_balancer.sh' to generate them.")
        return

    init_database()
    try:
        preload_static_info()
    except Exception as exc:
        logger.warning(f"Preload static info failed, will retry on first static request: {exc}")

    if not hasattr(app, "_service_initialized"):  # Make sure the service is only initialized once
        start_service()
        app._service_initialized = True

    ssl_context = (CERT_FILE, KEY_FILE)
    try:
        app.run(host="0.0.0.0", port=9001, debug=False, use_reloader=False, ssl_context=ssl_context)
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown_service_once()


if __name__ == "__main__":
    main()
