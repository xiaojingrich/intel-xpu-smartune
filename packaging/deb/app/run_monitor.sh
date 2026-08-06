#!/bin/bash
# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# systemd ExecStart for smartune-monitor.service. Runs as root (needed for the
# full telemetry: msr modprobe, cgroup/fdinfo reads, cpupower, ...). Ensures the
# self-signed TLS certificate exists, then launches the monitor.
#
# The venv is built at install time by postinst (ensure_venv.sh), NOT here — this
# script only runs it. If the venv is missing the install is broken, so we fail
# fast with a clear message instead of silently trying to rebuild at launch.

set -e

INSTALL_DIR="/opt/intel/smartune"
cd "$INSTALL_DIR"

mkdir -p "$INSTALL_DIR/logs"

# 1. The venv must already exist (built by postinst). Don't self-heal here.
if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
    echo "[smartune] ERROR: venv missing at $INSTALL_DIR/venv — reinstall the package." >&2
    exit 1
fi

# 2. Self-signed cert the Flask server needs (monitor refuses to start without it).
KEY_DIR="$INSTALL_DIR/key"
CERT_FILE="$KEY_DIR/b_server.crt"
KEY_FILE="$KEY_DIR/b_server.key"
mkdir -p "$KEY_DIR"
if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
    echo "[smartune] Generating self-signed certificate..."
    openssl req -x509 -newkey rsa:4096 \
        -keyout "$KEY_FILE" -out "$CERT_FILE" \
        -days 365 -nodes -subj "/CN=localhost" \
        -addext "subjectAltName=IP:127.0.0.1"
    chmod 644 "$CERT_FILE"
    chmod 600 "$KEY_FILE"
fi

# 3. UI-lease auto-shutdown: on demand launch means nothing stops this root
# service when the user closes the dashboard. With this flag the monitor tracks
# open-UI leases (heartbeats from each tab) and exits cleanly once the last one
# is gone; exit code 0 means systemd's Restart=on-failure won't revive it. The
# grace windows have sensible defaults (see utils/ui_lease.py) and can be tuned
# via SMARTUNE_UI_*_GRACE env vars if needed.
export SMARTUNE_UI_LEASE=1

# 4. Launch the monitor (port 9001, serves both the dashboard UI and its /api).
exec "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/smartune.py" -m
