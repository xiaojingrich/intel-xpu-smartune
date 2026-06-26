# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

# [SECURITY REVIEW]: All subprocess calls in this module use list-based arguments 
# with shell=False (default). No untrusted shell execution or string 
# concatenation is performed. All inputs are internally validated.
import subprocess # nosec
import re
from gi.repository import Gio

try:
    desktop_apps = {app.get_id(): app for app in Gio.AppInfo.get_all()}
    print(f"Loaded {len(desktop_apps)} desktop applications")
except Exception as e:
    print(f"Could not load desktop apps: {str(e)}")
    desktop_apps = {}


def _find_systemd_unit(pid):
    """Find the systemd scope/service owning a process via systemd-cgls."""
    try:
        result = subprocess.run(
            ['systemd-cgls', '--no-page'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )

        lines = result.stdout.split('\n')
        for i, line in enumerate(lines):
            if f'─{pid} ' in line or f'─{pid}\n' in line:
                for j in range(i, -1, -1):
                    line_content = lines[j]
                    if '.scope' in line_content or '.service' in line_content:
                        unit_match = re.search(r"─(.*?\.(?:scope|service))", line_content)
                        if unit_match:
                            return unit_match.group(1)

                        unit_match = re.search(r"\b([\w-]+\.(?:scope|service))\b", line_content)
                        if unit_match:
                            return unit_match.group(1)
    except Exception as e:
        print(f"Failed to find systemd unit: {str(e)}")
    return None

def _try_match_app(process_info):
    """Match a process to a desktop app or systemd scope."""
    if desktop_apps:
        for app_id, app in desktop_apps.items():
            try:
                cmd = app.get_commandline()
                if cmd and process_info['exe'] and process_info['exe'] in cmd:
                    return {
                        'type': 'desktop',
                        'id': app_id,
                        'name': app.get_display_name()
                    }

                if app.get_name().lower() in process_info['name'].lower():
                    return {
                        'type': 'desktop',
                        'id': app_id,
                        'name': app.get_display_name()
                    }
            except Exception:
                continue

    # Fallback: look up systemd scope/service
    unit = _find_systemd_unit(process_info['pids'][0])  # the first PID
    if unit:
        return {
            'type': 'systemd',
            'id': unit,
            'name': f"Systemd cgroup: {unit}"
        }

    return None

if __name__ == "__main__":
    process_info = {
        'pids': [659456, 659457, 659458, 659459, 659460, 659461, 659462, 659434, 659435, 659436,
                 659437, 659438, 659439, 659440, 659441, 659442, 659443, 659444, 659445, 659446,
                 659447, 659448, 659449, 659450, 659451, 659452, 659453, 659454, 659455],
        'name': 'stress',
        'cmdline': 'stress --cpu 22 --io 3 --vm 3 --vm-bytes 20G',
        'exe': '/usr/bin/stress',
        'score': 88.38,
        'cpu_avg': 1256.4,
        'mem_avg': 57.928,
        'mem_rss': 38800795238.4,
        'io_read_rate': 24576.0
    }
    process_info2 = {
        'pids': [2594],
        'name': 'filemanager',
        'cmdline': '/usr/sbin/filemanager',
        'exe': '/usr/sbin/filemanager',
        'score': 88.38,
        'cpu_avg': 1256.4,
        'mem_avg': 57.928,
        'mem_rss': 38800795238.4,
        'io_read_rate': 24576.0
    }

    app_info = _try_match_app(process_info2)
    print(f"Matched app info: {app_info}")


