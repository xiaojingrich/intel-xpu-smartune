#!/bin/bash

# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# Systemd entry point: called by balancer.service, already running as root.
# No sudo usage or trap installation needed; lifecycle is managed by systemd.

set -e
cd "$(dirname "$(readlink -f "$0")")"

export CERT_FILE="./b_server.crt"
export KEY_FILE="./b_server.key"

if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
    echo "Certificate and key already exist. Skipping generation."
else
    echo "Generating certificate..."
    openssl req -x509 -newkey rsa:4096 \
        -keyout "$KEY_FILE" -out "$CERT_FILE" \
        -days 365 -nodes -subj "/CN=localhost" \
        -addext "subjectAltName=IP:127.0.0.1"
    chmod 644 "$CERT_FILE"
    chmod 600 "$KEY_FILE"
    echo "Certificate generated successfully"
fi

PYTHON_BIN="$(command -v python3)"
exec "$PYTHON_BIN" BalanceService.py
