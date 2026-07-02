# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Acceptance test configuration.

These tests run against a LIVE SmartTune service on the target machine.
Prerequisites:
  - SmartTune service is running (BalanceService.py)
  - Tests are run with sudo or root privileges
  - Intel GPU/NPU hardware is available
  - Network interface is configured in config.yaml

Environment variables:
  SMARTUNE_HOST    - Service host (default: 127.0.0.1)
  SMARTUNE_PORT    - Service port (default: 9001)
  SMARTUNE_IFACE   - Network interface for TC tests (default: from config)
  SMARTUNE_TIMEOUT - Request timeout in seconds (default: 30)
"""

import os
import sys
import time
import subprocess

import pytest
import requests
from urllib3.exceptions import InsecureRequestWarning

# Suppress SSL warnings for self-signed certs
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

BALANCER_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'balancer')
sys.path.insert(0, BALANCER_DIR)

HOST = os.environ.get('SMARTUNE_HOST', '127.0.0.1')
PORT = int(os.environ.get('SMARTUNE_PORT', '9001'))
TIMEOUT = int(os.environ.get('SMARTUNE_TIMEOUT', '30'))
BASE_URL = f"https://{HOST}:{PORT}"


# Bypass any ambient http(s)_proxy env vars so a corporate proxy doesn't
# swallow localhost requests and make the service look "down".
_NO_PROXIES = {"http": None, "https": None}


def is_service_running():
    """Check if the SmartTune service is reachable."""
    try:
        resp = requests.get(f"{BASE_URL}/monitor/static_info",
                           verify=False, timeout=5, proxies=_NO_PROXIES)
        return resp.status_code == 200
    except Exception:
        return False


def is_root():
    return os.geteuid() == 0


def has_gpu():
    """Check if Intel GPU is available."""
    try:
        result = subprocess.run(['ls', '/sys/class/drm/card0'],
                               capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def has_npu():
    """Check if Intel NPU is available."""
    try:
        result = subprocess.run(['ls', '/sys/class/intel_pmt/'],
                               capture_output=True, timeout=5)
        return result.returncode == 0 and result.stdout.strip()
    except Exception:
        return False


def has_cgroup_v2():
    """Check if cgroup v2 is available."""
    return os.path.exists('/sys/fs/cgroup/cgroup.controllers')


# ─── Pytest markers ──────────────────────────────────────────────────────────

def pytest_configure(config):
    config.addinivalue_line("markers", "service: requires live SmartTune service")
    config.addinivalue_line("markers", "root: requires root/sudo privileges")
    config.addinivalue_line("markers", "gpu: requires Intel GPU hardware")
    config.addinivalue_line("markers", "npu: requires Intel NPU hardware")
    config.addinivalue_line("markers", "network: requires network TC capabilities")
    config.addinivalue_line("markers", "cgroup: requires cgroup v2")
    config.addinivalue_line("markers", "stress: long-running stress/stability tests")


def pytest_collection_modifyitems(config, items):
    """Auto-skip tests whose hardware/environment requirements aren't met."""
    skip_service = pytest.mark.skip(reason="SmartTune service not running")
    skip_root = pytest.mark.skip(reason="Requires root privileges")
    skip_gpu = pytest.mark.skip(reason="No Intel GPU detected")
    skip_npu = pytest.mark.skip(reason="No Intel NPU detected")
    skip_cgroup = pytest.mark.skip(reason="cgroup v2 not available")

    service_ok = is_service_running()
    root_ok = is_root()
    gpu_ok = has_gpu()
    npu_ok = has_npu()
    cgroup_ok = has_cgroup_v2()

    for item in items:
        if "service" in item.keywords and not service_ok:
            item.add_marker(skip_service)
        if "root" in item.keywords and not root_ok:
            item.add_marker(skip_root)
        if "gpu" in item.keywords and not gpu_ok:
            item.add_marker(skip_gpu)
        if "npu" in item.keywords and not npu_ok:
            item.add_marker(skip_npu)
        if "cgroup" in item.keywords and not cgroup_ok:
            item.add_marker(skip_cgroup)


# ─── Shared fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def api(base_url):
    """Return a configured requests.Session for the SmartTune API."""
    session = requests.Session()
    session.verify = False
    session.headers.update({'Content-Type': 'application/json'})
    session.timeout = TIMEOUT
    # Bypass corporate proxy for localhost requests.
    session.trust_env = False
    session.proxies = _NO_PROXIES
    return session


@pytest.fixture(scope="session")
def service_info(api, base_url):
    """Fetch and cache static system info from the service."""
    resp = api.get(f"{base_url}/monitor/static_info")
    if resp.status_code == 200:
        return resp.json().get('data', {})
    return {}
