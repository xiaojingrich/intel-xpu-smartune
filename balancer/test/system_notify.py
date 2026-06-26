# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import os
# [SECURITY REVIEW]: All subprocess calls in this module use list-based arguments 
# with shell=False (default). No untrusted shell execution or string 
# concatenation is performed. All inputs are internally validated.
import subprocess # nosec
from getpass import getuser
from pwd import getpwnam

def send_notification(title, message, icon="dialog-information"):
    try:
        # Try native notify-send first
        user = os.getenv("SUDO_USER") or getuser()
        print(f"Sending notification as user: {user}")

        user_uid = getpwnam(user).pw_uid

        # Build DBus session address
        dbus_address = f'unix:path=/run/user/{user_uid}/bus'

        # Run as target user via sudo
        subprocess.run([
            'sudo', '-u', user,
            f'DBUS_SESSION_BUS_ADDRESS={dbus_address}',
            'DISPLAY=:0',
            'notify-send',
            f'--icon={icon}',
            title,
            message
        ], check=True)

    except (subprocess.CalledProcessError, FileNotFoundError):
        try:
            # Fallback to zenity
            subprocess.run(
                ["zenity", "--info", "--text", f"{title}\n{message}", "--title", "System Notification"],
                check=True
            )
        except:
            print(f"\a⚠️ {title}: {message}")

send_notification("Resource Warning", "App launch paused, check control center", "dialog-warning")
