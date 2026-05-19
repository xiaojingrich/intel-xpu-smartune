# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import json
import os
import requests
import threading
import time
from typing import Optional, Dict, Any

from apis.multiapps_bridge import MABridge
from apis.systools import SingletonMeta

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3

MULTIAPPS_URL = "https://127.0.0.1:9001"
B_CERT_FILE = os.getenv('B_CERT_FILE')
B_CERT_KEY = os.getenv('B_CERT_KEY')


# 全局共享状态
class CallbackManager(metaclass=SingletonMeta):
    def __init__(self):
        self._last_callback: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()
        self._app_handlers = set()

    def add_to_handler(self, handler):
        """注册UI更新函数"""
        with self._lock:
            self._app_handlers.add(handler)

    def handle_callback(self, data: Dict[str, Any]):
        """处理回调并通知UI"""
        with self._lock:
            self._last_callback = data
            for handler in self._app_handlers:
                try:
                    handler(data)  # 触发所有注册的UI更新
                except Exception as e:
                    print(f"UI handler failed: {str(e)}")


callback_manager = CallbackManager()


_SSE_EVENT_TYPE_CONNECTED = "connected"


class Client_multiapps_api(metaclass=SingletonMeta):
    def __init__(self):
        self.ma_bridge = MABridge()
        self._callback_thread = None

        # Multi-Apps Startup
        self.app_get_controlled_url = MULTIAPPS_URL + '/app/get_controlled_app'
        self.app_set_controlled_url = MULTIAPPS_URL + '/app/set_to_control'
        self.app_remove_controlled_url = MULTIAPPS_URL + '/app/remove_from_control'
        self.app_get_priority_url = MULTIAPPS_URL + '/app/get_priority_data'
        self.app_set_priority_url = MULTIAPPS_URL + '/app/set_priority'
        self.app_set_oom_score_url = MULTIAPPS_URL + '/app/set_oom_score'
        self.app_cancel_relaunch_url = MULTIAPPS_URL + '/app/cancel_relaunch'
        self.app_resource_limit_url = MULTIAPPS_URL + '/app/resource_limit'
        self.app_resource_limit_profile_url = MULTIAPPS_URL + '/app/resource_limit_profile'
        self.app_resource_restore_url = MULTIAPPS_URL + '/app/resource_restore'
        self.app_get_pending_url = MULTIAPPS_URL + '/app/get_pending_app'
        self.app_obtain_url = MULTIAPPS_URL + '/app/get_apps'
        self.app_workload_url = MULTIAPPS_URL + '/task/add_workload'
        self.app_events_url = MULTIAPPS_URL + '/app/events'

        self.session = self._create_session()

    def _create_session(self):
        """Create a requests session with retry strategy and SSL configuration."""
        session = requests.Session()

        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["POST", "GET"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)

        if not B_CERT_FILE:
            raise EnvironmentError(
                "B_CERT_FILE environment variable is not set. "
                "TLS certificate verification cannot be enabled."
            )
        if not os.path.exists(B_CERT_FILE):
            raise FileNotFoundError(
                f"Certificate file '{B_CERT_FILE}' not found. "
                "TLS certificate verification cannot be enabled. "
                "Please check 'start_webui_env.sh' to export B_CERT_FILE."
            )
        session.verify = B_CERT_FILE
        print(f"TLS certificate verification enabled using: {B_CERT_FILE}")

        return session

# Multi-apps API:
    def get_controlled_apps(self):
        """
        :return: Get all the controlled apps.
        """

        return self.ma_bridge.get_controlled_apps(self.app_get_controlled_url, self.session)

    def set_controlled_apps(self, app_data):
        """
        :param app_data: Dictionary containing app control data.
        :return: Set the control status of an app.
        """
        res_data = self.ma_bridge.set_controlled_apps(self.app_set_controlled_url, app_data, self.session)
        return res_data

    def remove_controlled_apps(self, app_data):
        """
        :param app_data: Dictionary containing app control data.
        :return: Remove the control status of an app.
        """
        return self.ma_bridge.remove_controlled_apps(self.app_remove_controlled_url, app_data, self.session)

    def get_priority_data(self, query_data):
        """
        :param query_data: Dictionary containing app_id or app_name.
        :return: Get priority data for a specific app.
        """
        return self.ma_bridge.get_priority_data(self.app_get_priority_url, query_data, self.session)

    def set_priority(self, priority_data):
        """
        :param priority_data: Dictionary containing app_id, priority, and optional cgroup.
        :return: Set the priority of an app.
        """
        return self.ma_bridge.set_priority(self.app_set_priority_url, priority_data, self.session)

    def keep_alive_app(self, app_id):
        """
        :param app_id: used to find the app to keep alive.
        :return:
        """
        return self.ma_bridge.keep_alive_app(self.app_set_oom_score_url, app_id, self.session)

    def cancel_relaunch(self, app_id):
        """
        :param app_id: according to app_id to cancel relaunch.
        :return: success or not
        """
        return self.ma_bridge.cancel_relaunch(self.app_cancel_relaunch_url, app_id, self.session)

    def resource_limit(self, app_id, app_name, priority, limit_overrides=None):
        """
        :param app_id: according to app_id to do the resource limit.
        :return:
        """
        return self.ma_bridge.resource_limit(
            self.app_resource_limit_url, app_id, app_name, priority, self.session, limit_overrides=limit_overrides
        )

    def resource_limit_profile(self, app_id, app_name, priority):
        data = {"app_id": app_id, "app_name": app_name, "priority": priority}
        try:
            response = self.session.post(self.app_resource_limit_profile_url, json=data, timeout=5)
            response.raise_for_status()
            response_data = response.json()
            if "retcode" in response_data and response_data["retcode"] == 0:
                return response_data.get("data", {})
            return {}
        except requests.exceptions.SSLError as e:
            print(f"SSL verification failed: {e}, please check your SSL configuration.")
            return {}
        except requests.exceptions.RequestException as e:
            print('resource_limit_profile request error: ', e)
            return {}

    def restore_resource(self, app_id):
        """
        :param app_id: according to app_id to do the resource restore.
        :return:
        """
        return self.ma_bridge.restore_resource(self.app_resource_restore_url, app_id, self.session)

    def get_pending_apps(self):
        """
        :return: Get all the pending apps.
        """

        return self.ma_bridge.get_pending_apps(self.app_get_pending_url, self.session)

    def get_apps(self, store=False):
        """
        :return: Get the list of all apps.
        """
        return self.ma_bridge.get_apps(self.app_obtain_url, store, self.session)


    def start_client_callback(self) -> bool:
        """启动SSE客户端（确保线程单例）"""
        if self._callback_thread is not None and self._callback_thread.is_alive():
            print("[Callback] SSE client is already running")
            return True

        try:
            self._callback_thread = threading.Thread(
                target=self._run_sse_client,
                daemon=True
            )
            self._callback_thread.start()
            print("[Callback] SSE client started")
            return True
        except Exception as e:
            print(f"[Callback] Failed to start SSE client: {str(e)}")
            return False

    def _run_sse_client(self):
        """连接SSE服务器并处理事件"""
        retry_delay = 5
        max_retry_delay = 60
        while True:
            try:
                response = self.session.get(self.app_events_url, stream=True, timeout=(10, None))
                retry_delay = 5  # reset on successful connection
                for line in response.iter_lines():
                    if line:
                        decoded = line.decode('utf-8') if isinstance(line, bytes) else line
                        if decoded.startswith('data: '):
                            try:
                                data = json.loads(decoded[6:])
                                if data.get('type') != _SSE_EVENT_TYPE_CONNECTED:
                                    callback_manager.handle_callback(data)
                            except Exception as e:
                                print(f"[Callback] Error parsing SSE data: {e}")
            except Exception as e:
                print(f"[Callback] SSE connection error ({self.app_events_url}): {e}, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
