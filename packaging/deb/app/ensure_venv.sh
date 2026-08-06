#!/bin/bash
# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# Build the Python virtualenv the monitor runs in, installing the pinned deps
# OFFLINE from the wheels bundled in the .deb (see build_deb.sh). No PyPI access
# is needed or attempted. Idempotent: a stamp records the requirements hash so a
# reconfigure with unchanged pins is a no-op, and an upgrade with changed pins
# rebuilds from the new bundled wheels.
#
# Called ONLY from the deb postinst — venv creation is an install-time step, not
# a launch-time one. If it fails, postinst fails and apt reports the install as
# failed (fail-fast), rather than deferring a fragile pip run to first launch.

set -e

INSTALL_DIR="/opt/intel/smartune"
VENV="$INSTALL_DIR/venv"
REQ="$INSTALL_DIR/requirements.txt"
WHEELS="$INSTALL_DIR/wheels"
STAMP="$VENV/.deps_ok"

# requirements.txt hash — rebuild deps when the pins change across upgrades.
req_hash="$(sha256sum "$REQ" 2>/dev/null | cut -d' ' -f1)"

if [ -x "$VENV/bin/python" ] && [ -f "$STAMP" ] && [ "$(cat "$STAMP" 2>/dev/null)" = "$req_hash" ]; then
    exit 0
fi

if [ ! -d "$WHEELS" ]; then
    echo "[smartune] ERROR: bundled wheels missing at $WHEELS; the package is incomplete." >&2
    exit 1
fi

echo "[smartune] Building Python environment from bundled wheels..."
if [ ! -x "$VENV/bin/python" ]; then
    python3 -m venv "$VENV"
fi

# The bundled native wheels are built for one CPython minor (see build_deb.sh).
# If this host's Python differs, the --no-index install below would fail with a
# cryptic "no matching distribution"; catch it here with an actionable message.
host_py="$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
bundle_py="$(cat "$WHEELS/.python_version" 2>/dev/null || true)"
if [ -n "$bundle_py" ] && [ "$host_py" != "$bundle_py" ]; then
    echo "[smartune] ERROR: this package bundles wheels for CPython $bundle_py but this system has $host_py." >&2
    echo "[smartune]        Install the build for this OS release (one .deb is built per release)." >&2
    exit 1
fi

# --no-index + --find-links: install strictly from the bundled wheels, never
# reaching the network. Fails loudly if a required wheel is absent.
"$VENV/bin/python" -m pip install --no-index --find-links="$WHEELS" -r "$REQ"

echo "$req_hash" > "$STAMP"
echo "[smartune] Python environment ready."
