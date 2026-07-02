# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Release validation test configuration.

Pre-ship functional tests that run against a LIVE SmartTune service.
These validate end-to-end behavior from the user's perspective.

Prerequisites:
  - SmartTune service is running
  - Tests are run with sudo/root privileges
  - Required tools installed: stress-ng, fio
  - Intel GPU/NPU hardware available (for hardware tests)

Usage:
  ./run_tests.sh release
"""

import os
import sys
import time
import shutil
import signal
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


# ─── Helper functions ────────────────────────────────────────────────────────

# The service listens on localhost over HTTPS with a self-signed cert. Force
# requests to bypass any ambient http(s)_proxy env vars, otherwise a corporate
# proxy can swallow the localhost request and the service looks "down".
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
    """Check if running with root privileges."""
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


def has_stress_ng():
    """Check if stress-ng is installed and available."""
    return shutil.which('stress-ng') is not None


def has_fio():
    """Check if fio (Flexible I/O Tester) is installed and available."""
    return shutil.which('fio') is not None


def has_iperf3():
    """Check if iperf3 is installed and available."""
    return shutil.which('iperf3') is not None


def has_cgroup_v2():
    """Check if cgroup v2 (unified hierarchy) is mounted."""
    return os.path.exists('/sys/fs/cgroup/cgroup.controllers')


# ─── Pytest markers ──────────────────────────────────────────────────────────

def pytest_configure(config):
    config.addinivalue_line("markers", "service: requires live SmartTune service")
    config.addinivalue_line("markers", "root: requires root/sudo privileges")
    config.addinivalue_line("markers", "gpu: requires Intel GPU hardware")
    config.addinivalue_line("markers", "npu: requires Intel NPU hardware")
    config.addinivalue_line("markers", "stress_tools: requires stress-ng")
    config.addinivalue_line("markers", "io_tools: requires fio")
    config.addinivalue_line("markers", "cgroup: requires cgroup v2")


def pytest_collection_modifyitems(config, items):
    """Auto-skip tests whose hardware/environment requirements aren't met."""
    skip_service = pytest.mark.skip(reason="SmartTune service not running")
    skip_root = pytest.mark.skip(reason="Requires root privileges")
    skip_gpu = pytest.mark.skip(reason="No Intel GPU detected")
    skip_npu = pytest.mark.skip(reason="No Intel NPU detected")
    skip_stress_tools = pytest.mark.skip(reason="stress-ng not installed")
    skip_io_tools = pytest.mark.skip(reason="fio not installed")
    skip_cgroup = pytest.mark.skip(reason="cgroup v2 not available")

    service_ok = is_service_running()
    root_ok = is_root()
    gpu_ok = has_gpu()
    npu_ok = has_npu()
    stress_ok = has_stress_ng()
    io_ok = has_fio()
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
        if "stress_tools" in item.keywords and not stress_ok:
            item.add_marker(skip_stress_tools)
        if "io_tools" in item.keywords and not io_ok:
            item.add_marker(skip_io_tools)
        if "cgroup" in item.keywords and not cgroup_ok:
            item.add_marker(skip_cgroup)


# ─── Shared fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def base_url():
    """Base URL for the SmartTune service."""
    return BASE_URL


@pytest.fixture(scope="session")
def api(base_url):
    """Return a configured requests.Session for the SmartTune API."""
    session = requests.Session()
    session.verify = False
    session.headers.update({'Content-Type': 'application/json'})
    session.timeout = TIMEOUT
    # Bypass corporate proxy for localhost; trust_env=False stops requests from
    # reading http(s)_proxy / no_proxy from the environment entirely.
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


# ─── Release-specific fixtures ───────────────────────────────────────────────

@pytest.fixture
def controlled_app(api, base_url):
    """
    Create a temporary controlled application and clean it up after the test.

    Yields the app name so the test can interact with it.
    """
    app_name = f"release_test_app_{os.getpid()}_{int(time.time())}"

    # Register the app for control
    resp = api.post(f"{base_url}/control/add", json={"name": app_name})
    assert resp.status_code == 200, (
        f"Failed to register controlled app '{app_name}': {resp.text}"
    )

    yield app_name

    # Cleanup: uncontrol the app
    try:
        api.post(f"{base_url}/control/delete", json={"name": app_name})
    except Exception:
        pass  # Best-effort cleanup


@pytest.fixture
def wait_for_pressure():
    """
    Return a helper that waits until system pressure reaches the specified level.

    Args:
        level: Target pressure percentage (0-100).
        timeout: Maximum seconds to wait (default: 30).

    Raises:
        TimeoutError if pressure doesn't reach the target level in time.
    """
    def _wait(level, timeout=30):
        """Wait until CPU pressure reaches the given level."""
        psi_path = '/proc/pressure/cpu'
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                with open(psi_path, 'r') as f:
                    content = f.read()
                # Parse "some avg10=X.XX avg60=X.XX avg300=X.XX total=XXXX"
                for line in content.strip().split('\n'):
                    if line.startswith('some'):
                        parts = line.split()
                        for part in parts:
                            if part.startswith('avg10='):
                                current = float(part.split('=')[1])
                                if current >= level:
                                    return current
            except (IOError, ValueError):
                pass
            time.sleep(0.5)

        raise TimeoutError(
            f"CPU pressure did not reach {level}% within {timeout}s"
        )

    return _wait


@pytest.fixture
def stress_cpu():
    """
    Start stress-ng in the background to generate CPU load.

    Returns a factory function that starts stress and returns a cleanup handle.
    The stress process is automatically killed when the test finishes.

    Usage in tests:
        def test_under_load(stress_cpu):
            stress_cpu(percent=80, duration=30)
            # ... test logic while CPU is stressed ...
    """
    processes = []

    def _stress(percent=80, duration=60):
        """
        Start stress-ng with the given CPU load percentage.

        Args:
            percent: Target CPU utilization (0-100).
            duration: How long stress-ng should run (seconds).
        """
        if not has_stress_ng():
            pytest.skip("stress-ng is not installed")

        num_cpus = os.cpu_count() or 1
        cmd = [
            'stress-ng',
            '--cpu', str(num_cpus),
            '--cpu-load', str(percent),
            '--timeout', f'{duration}s',
            '--quiet',
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setpgrp,
        )
        processes.append(proc)
        # Give stress-ng a moment to ramp up
        time.sleep(2)
        return proc

    yield _stress

    # Cleanup: kill all stress processes
    for proc in processes:
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    proc.wait(timeout=3)
                except Exception:
                    pass
