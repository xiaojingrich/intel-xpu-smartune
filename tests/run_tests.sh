#!/bin/bash
# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# Test runner script for intel-xpu-smartune
# Usage:
#   ./run_tests.sh              # Run all tests
#   ./run_tests.sh functional   # Run only functional tests
#   ./run_tests.sh performance  # Run only performance tests
#   ./run_tests.sh stability    # Run only stability tests
#   ./run_tests.sh quick        # Run fast unit tests only
#   ./run_tests.sh release      # Pre-release E2E tests (requires live service + root)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$SCRIPT_DIR"

export PYTHONPATH="$PROJECT_ROOT/balancer:$PYTHONPATH"
PYTHON="${PYTHON:-python3}"

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/test_results_${TIMESTAMP}.log"

case "${1:-all}" in
    functional|func)
        echo "=== Running Functional Tests ==="
        $PYTHON -m pytest test_config.py test_database.py test_pressure.py test_http_utils.py test_app_utils.py test_controller.py test_balancer_logic.py test_api_endpoints.py -v --color=yes 2>&1 | tee "$LOG_FILE"
        ;;
    performance|perf)
        echo "=== Running Performance Tests ==="
        $PYTHON -m pytest test_performance.py -v --color=yes 2>&1 | tee "$LOG_FILE"
        ;;
    stability|stab)
        echo "=== Running Stability Tests ==="
        $PYTHON -m pytest test_stability.py -v --timeout=60 --color=yes 2>&1 | tee "$LOG_FILE"
        ;;
    integration|integ)
        echo "=== Running Integration Tests ==="
        $PYTHON -m pytest test_integration.py -v --color=yes 2>&1 | tee "$LOG_FILE"
        ;;
    quick)
        echo "=== Running Quick Unit Tests ==="
        $PYTHON -m pytest test_config.py test_pressure.py test_http_utils.py -v --timeout=10 --color=yes 2>&1 | tee "$LOG_FILE"
        ;;
    all)
        echo "=== Running All Tests ==="
        $PYTHON -m pytest --ignore=acceptance --ignore=release -v --timeout=60 --color=yes 2>&1 | tee "$LOG_FILE"
        ;;
    acceptance|accept)
        echo "=== Running Acceptance Tests (requires live service + root) ==="
        $PYTHON -m pytest acceptance/ -v --timeout=60 --color=yes 2>&1 | tee "$LOG_FILE"
        ;;
    release|rel)
        echo "=== Running Pre-Release Functional Tests (requires live service + root) ==="
        echo "Prerequisites: SmartTune service running, root privileges, stress-ng/fio installed"
        $PYTHON -m pytest release/ -v --timeout=120 --color=yes 2>&1 | tee "$LOG_FILE"
        ;;
    *)
        echo "Usage: $0 {all|functional|performance|stability|integration|acceptance|release|quick}"
        exit 1
        ;;
esac

RESULT=$?
echo ""
echo "Test results saved to: $LOG_FILE"
ln -sf "test_results_${TIMESTAMP}.log" "$LOG_DIR/test_results_latest.log"
exit $RESULT
