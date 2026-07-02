#!/bin/bash
# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# Acceptance test runner for SmartTune release validation.
#
# Prerequisites:
#   - SmartTune service running on localhost:9001
#   - Root/sudo for cgroup and network tests
#   - Intel GPU/NPU hardware for GPU tests
#   - stress-ng for pressure tests
#
# Usage:
#   ./run_acceptance.sh              # Run all acceptance tests (excluding stress)
#   ./run_acceptance.sh all          # Run everything including stress tests
#   ./run_acceptance.sh service      # Only service-level tests
#   ./run_acceptance.sh gpu          # Only GPU tests
#   ./run_acceptance.sh stress       # Only stress/stability tests
#   ./run_acceptance.sh quick        # Service + monitor (no root needed)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="${PYTHON:-python3}"

echo "============================================"
echo "  SmartTune Release Acceptance Tests"
echo "============================================"
echo ""
echo "Target: ${SMARTUNE_HOST:-127.0.0.1}:${SMARTUNE_PORT:-9001}"
echo "User:   $(whoami)"
echo ""

case "${1:-default}" in
    all)
        echo "=== Running ALL Acceptance Tests (including stress) ==="
        $PYTHON -m pytest -v --tb=short -m "" --timeout=120
        ;;
    service)
        echo "=== Running Service Tests ==="
        $PYTHON -m pytest test_service_lifecycle.py test_monitor_api.py -v --timeout=30
        ;;
    gpu)
        echo "=== Running GPU/NPU Tests ==="
        $PYTHON -m pytest test_gpu_monitoring.py -v --timeout=30
        ;;
    network)
        echo "=== Running Network Control Tests ==="
        $PYTHON -m pytest test_network_control.py -v --timeout=30
        ;;
    pressure)
        echo "=== Running Pressure Response Tests ==="
        $PYTHON -m pytest test_pressure_response.py -v --timeout=60
        ;;
    resource)
        echo "=== Running Resource Control Tests ==="
        $PYTHON -m pytest test_resource_control.py -v --timeout=60
        ;;
    app)
        echo "=== Running App Management Tests ==="
        $PYTHON -m pytest test_app_management.py -v --timeout=60
        ;;
    stress)
        echo "=== Running Stress/Stability Tests ==="
        $PYTHON -m pytest test_long_running.py -v -m "stress" --timeout=180
        ;;
    quick)
        echo "=== Running Quick Validation (no root needed) ==="
        $PYTHON -m pytest test_service_lifecycle.py test_monitor_api.py -v -m "service" --timeout=30
        ;;
    default)
        echo "=== Running Standard Acceptance Suite (excluding stress) ==="
        $PYTHON -m pytest -v --tb=short --timeout=60
        ;;
    *)
        echo "Usage: $0 {all|service|gpu|network|pressure|resource|app|stress|quick}"
        exit 1
        ;;
esac

EXIT_CODE=$?
echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "✓ All tests passed!"
elif [ $EXIT_CODE -eq 5 ]; then
    echo "⊘ No tests collected (requirements not met - check markers)"
else
    echo "✗ Some tests failed (exit code: $EXIT_CODE)"
fi

exit $EXIT_CODE
