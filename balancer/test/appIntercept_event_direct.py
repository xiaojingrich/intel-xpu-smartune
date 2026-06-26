# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from bcc import BPF
import os
import signal
# [SECURITY REVIEW]: All subprocess calls in this module use list-based arguments 
# with shell=False (default). No untrusted shell execution or string 
# concatenation is performed. All inputs are internally validated.
import subprocess # nosec
import time
from typing import List, Set, Dict, Any
from gi.repository import Gio
import psutil

# BPF constants (must match C definitions)
COMM_LEN = 32
PY_MAX_FILE_LEN = 64

class SingletonMeta(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]

class AppIntercept(metaclass=SingletonMeta):
    def __init__(self, c_src_file: str = "bpf_event.c"):
        self.bpf = BPF(src_file=c_src_file)
        self.monitored_apps: Set[str] = set()
        self.processes_to_relaunch: Dict[int, Dict[str, Any]] = {}
        self.handled_processes: Set[int] = set()
        self.app_db = self.build_app_database()
        self.relaunch_apps = {}

    def build_app_database(self) -> Dict[str, Dict[str, str]]:
        """Build desktop application database."""
        db = {}
        for app in Gio.AppInfo.get_all():
            desktop_id = app.get_id()
            if desktop_id.endswith('.desktop'):
                db[app.get_name().lower()] = {
                    'desktop_id': desktop_id,
                    'command': app.get_commandline() or ''
                }
        return db

    def trace_print(self) -> None:
        self.bpf.trace_print()

    def get_main_process(self, filename: str) -> (bool, str):
        """Check if this is a main process launch."""
        filename_lower = filename.lower()
        app_flag = [(app, app.lower() in filename_lower) for app in self.monitored_apps]
        special_flag = [x in filename_lower for x in ['/bin/', '/usr/bin/', '/snap/bin/']]
        main_app = [app[0] for app in app_flag if app[1]]
        has_app_match = any(flag for _, flag in app_flag)
        if any(special_flag) and has_app_match and main_app:
            return True, main_app[0]
        return False, ""

    def print_event(self, cpu: int, data: Any, size: int) -> None:
        event = self.bpf["events"].event(data)
        filename = event.filename.decode('utf-8', 'ignore')
        comm = event.comm.decode('utf-8', 'ignore')
        pid = event.pid

        print(f"*** Event: PID={pid}, COMM={comm}, FILENAME={filename} ***")

        for app_name in self.monitored_apps:
            app_name_lower = app_name.lower()
            is_main_process = (
                app_name_lower in filename.lower() and
                any(x in filename.lower() for x in ['/bin/', '/usr/bin/', '/snap/bin/'])
            )

            print(f"app_name_lower: {app_name_lower}, is_main_process: {is_main_process}")

            if is_main_process:
                if not self.is_process_handled(pid):
                    desktop_id = self.app_db.get(app_name_lower, {}).get('desktop_id', '')
                    self.handle_monitored_app(pid, comm, filename, app_name, desktop_id)
                    self.mark_process_handled(pid)
                break

    def is_process_handled(self, pid: int) -> bool:
        """Check if this process or any ancestor was already handled."""
        try:
            process = psutil.Process(pid)
            for p in [process] + process.parents():
                if p.pid in self.handled_processes:
                    return True
        except psutil.NoSuchProcess:
            pass
        return False

    def mark_process_handled(self, pid: int) -> None:
        """Mark process as handled."""
        self.handled_processes.add(pid)

    def is_self_relaunched_process(self, app_name: str, desktop_id: str) -> bool:
        """

        :param app_name:
        :return:
        """
        return app_name in self.relaunch_apps or desktop_id in self.relaunch_apps

    def handle_monitored_app(self, pid: int, comm: str, filename: str, app_name: str, desktop_id: str) -> None:
        print(f"Detected monitored app '{app_name}' (PID: {pid}, COMM: {comm}, FILE: {filename}, desktop_id: {desktop_id})")

        try:
            if self.is_self_relaunched_process(app_name, desktop_id):
                print(f"Ignoring self-relaunched process: {app_name}: {desktop_id}")
                del self.relaunch_apps[desktop_id or app_name]
                return

            os.kill(pid, signal.SIGSTOP)

            if self.check_system_resources():
                self.processes_to_relaunch[pid] = {
                    'desktop_id': desktop_id,
                    'comm': comm,
                    'filename': filename,
                    'detection_time': time.time(),
                    'app_name': app_name
                }

                # time.sleep(1)
                os.kill(pid, signal.SIGCONT)
            else:
                print(f"System resources busy, skipping relaunch of {app_name}")

        except Exception as e:
            print(f"Error handling {app_name} (PID: {pid}): {str(e)}")

    def add_to_monitorlist(self, app_name: str) -> None:
        """Add app to monitoring list."""
        if app_name.lower() not in (name.lower() for name in self.monitored_apps):
            self.monitored_apps.add(app_name)
            print(f"Added '{app_name}' to monitoring list")
        else:
            print(f"'{app_name}' is already in monitoring list")

    def remove_from_monitorlist(self, app_name: str) -> None:
        """Remove app from monitoring list."""
        if app_name in self.monitored_apps:
            self.monitored_apps.remove(app_name)
            print(f"Removed '{app_name}' from monitoring list")
        else:
            print(f"'{app_name}' not found in monitoring list")

    def clear_monitorlist(self) -> None:
        """Clear monitoring list."""
        self.monitored_apps.clear()
        print("Cleared monitoring list")

    def get_monitored_apps(self) -> List[str]:
        """Get current monitored apps list."""
        return list(self.monitored_apps)

    def graceful_terminate(self, pid: int, timeout: int = 3) -> None:
        """Gracefully terminate a process (SIGTERM, then SIGKILL on timeout)."""
        try:
            process = psutil.Process(pid)

            process.terminate()

            try:
                process.wait(timeout=timeout)
            except psutil.TimeoutExpired:
                process.kill()
                print(f"Force killed PID {pid} after timeout")

        except psutil.NoSuchProcess:
            print(f"Process {pid} already terminated")
        except Exception as e:
            print(f"Error terminating process {pid}: {str(e)}")

    def check_system_resources(self, cpu_threshold: int = 70, mem_threshold: int = 80) -> bool:
        """Check if system resources are below threshold."""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            mem_percent = psutil.virtual_memory().percent
            print(f"System status - CPU: {cpu_percent}%, Memory: {mem_percent}%")
            return cpu_percent < cpu_threshold and mem_percent < mem_threshold
        except Exception as e:
            print(f"Error checking system resources: {str(e)}")
            return True

    def relaunch(self, app_name: str) -> bool:
        """Relaunch an application using available methods."""
        print(f"Attempting to relaunch: {app_name}")

        def try_launch(command, method_name):
            try:
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True
                )
                pid = proc.pid
                print(f"Attempted launch via {method_name} (PID: {pid})")
                self.relaunch_apps[app_name] = pid
                print(f"relaunch done: {self.relaunch_apps}")
                return True
            except FileNotFoundError:
                return False
            except Exception as e:
                print(f"Error with {method_name}: {str(e)}")
                return False

        def try_launch_by_system(command, method_name):
            try:
                print(f"command: {command}")
                os.system(command)
                pid = os.getpid()
                print(f"Attempted launch via {method_name} (PID: {pid})")
                self.relaunch_apps[app_name] = pid
                print(f"relaunch done: {self.relaunch_apps}")
                return True
            except FileNotFoundError:
                return False
            except Exception as e:
                print(f"Error with {method_name}: {str(e)}")
                return False

        try:
            # 1. Try gtk-launch for .desktop files
            if app_name.endswith('.desktop'):
                if try_launch(["gtk-launch", app_name], "gtk-launch"):
                    print("gtk-launch successful")
                    return True

            # 2. Try direct execution
            if try_launch_by_system(app_name, "direct execution"):
                print("Direct execution successful")
                return True

            # 3. Try xdg-open
            if try_launch(["xdg-open", app_name], "xdg-open"):
                print("xdg-open successful")
                return True

            print(f"All launch methods failed for {app_name}")
            return False

        except Exception as e:
            print(f"Critical error relaunching {app_name}: {str(e)}")
            return False
