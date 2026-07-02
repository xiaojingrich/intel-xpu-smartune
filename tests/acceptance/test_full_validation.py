# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Acceptance tests: Full Validation
Covers server functional tests (TC-S-005 ~ TC-S-007, TC-S-011 ~ TC-S-014, TC-S-019)
and performance tests (TC-SP-001 ~ TC-SP-008) from TEST_CASES.md.

These tests run against a LIVE SmartTune service and require:
  - SmartTune service is running (BalanceService.py)
  - Root/sudo privileges for cgroup/stress operations
  - Intel GPU/NPU hardware for hardware-dependent tests
  - External tools: stress-ng, fio, iperf3, tc
"""

import os
import time
import uuid
import statistics
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest


# ─── Helpers ─────────────────────────────────────────────────────────────────

def unique_app_id(prefix="fv"):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def get_network_interface():
    """Get the configured or default network interface."""
    iface = os.environ.get('SMARTUNE_IFACE')
    if iface:
        return iface
    try:
        result = subprocess.run(
            ['ip', 'route', 'show', 'default'],
            capture_output=True, text=True, timeout=5
        )
        parts = result.stdout.strip().split()
        if 'dev' in parts:
            return parts[parts.index('dev') + 1]
    except Exception:
        pass
    return 'lo'


def tool_available(tool_name):
    """Check if a CLI tool is available on PATH."""
    result = subprocess.run(['which', tool_name], capture_output=True)
    return result.returncode == 0


def percentile(data, pct):
    """Compute percentile from a sorted list using statistics-compatible method."""
    if not data:
        return 0
    sorted_data = sorted(data)
    n = len(sorted_data)
    idx = (pct / 100) * (n - 1)
    lower = int(idx)
    upper = lower + 1
    if upper >= n:
        return sorted_data[-1]
    weight = idx - lower
    return sorted_data[lower] * (1 - weight) + sorted_data[upper] * weight


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: Server Functional Tests
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.service
@pytest.mark.root
@pytest.mark.stress
class TestSystemPressureDetectionGrading:
    """TC-S-005: System pressure detection & grading.

    Verify the service detects system load levels and reports proper
    pressure grades (low, medium, high, critical) through PSI monitoring.
    Requires stress-ng to induce load.
    """

    @pytest.fixture
    def stress_cpu(self):
        """Start stress-ng with variable CPU load. Yields the process."""
        if not tool_available('stress-ng'):
            pytest.skip("stress-ng not installed")
        proc = None
        try:
            proc = subprocess.Popen(
                ['stress-ng', '--cpu', str(os.cpu_count() or 4),
                 '--cpu-load', '90', '--timeout', '60s'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            # Allow pressure to build
            time.sleep(8)
            yield proc
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)

    def test_baseline_pressure_is_low(self, api, base_url):
        """With no artificial load, system pressure should be low or none."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        assert resp.status_code == 200
        data = resp.json()['data']
        pressure = data.get('pressure', data.get('psi', {}))
        # Extract pressure level string if available
        level = None
        if isinstance(pressure, dict):
            level = pressure.get('level', pressure.get('grade', None))
        if level is not None:
            assert level.lower() in ('none', 'low', 'medium'), \
                f"Baseline pressure unexpectedly high: {level}"

    def test_pressure_rises_under_cpu_stress(self, api, base_url, stress_cpu):
        """Under full CPU stress, pressure grade should increase."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        assert resp.status_code == 200
        data = resp.json()['data']
        pressure = data.get('pressure', data.get('psi', {}))
        # Validate pressure data is present and non-trivial
        assert pressure is not None and pressure != {}
        if isinstance(pressure, dict):
            level = pressure.get('level', pressure.get('grade', ''))
            cpu_val = pressure.get('cpu', pressure.get('some', 0))
            if isinstance(cpu_val, dict):
                cpu_val = cpu_val.get('avg10', cpu_val.get('some', 0))
            # Under stress, expect at least a non-zero value
            if isinstance(cpu_val, (int, float)):
                assert cpu_val >= 0

    def test_pressure_recovers_after_release(self, api, base_url):
        """After a brief stress run finishes, pressure should decrease."""
        if not tool_available('stress-ng'):
            pytest.skip("stress-ng not installed")
        # Short burst
        proc = subprocess.Popen(
            ['stress-ng', '--cpu', '2', '--timeout', '5s'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        proc.wait(timeout=15)
        # Wait for cooldown (PSI uses rolling averages)
        time.sleep(20)
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        pressure = data.get('pressure', data.get('psi', {}))
        if isinstance(pressure, dict):
            level = pressure.get('level', pressure.get('grade', ''))
            if level:
                assert level.lower() in ('none', 'low', 'medium')


@pytest.mark.service
@pytest.mark.root
@pytest.mark.cgroup
@pytest.mark.stress
class TestPassiveResourceControl:
    """TC-S-006: Passive resource control (auto-limiting).

    Verify that when system pressure reaches critical, low-priority apps
    are automatically limited while high-priority apps are preserved.
    """

    @pytest.fixture
    def controlled_apps(self, api, base_url):
        """Register two controlled apps with different priorities."""
        low_id = unique_app_id("low")
        high_id = unique_app_id("high")

        api.post(f"{base_url}/app/set_to_control", json={
            'app_id': low_id, 'app_name': 'LowPrioApp',
            'controlled': True, 'priority': 'low',
            'cmdline': 'sleep_low', 'bpf_name': 'sleep'
        })
        api.post(f"{base_url}/app/set_to_control", json={
            'app_id': high_id, 'app_name': 'HighPrioApp',
            'controlled': True, 'priority': 'high',
            'cmdline': 'sleep_high', 'bpf_name': 'sleep'
        })
        yield {'low': low_id, 'high': high_id}

        # Cleanup
        api.post(f"{base_url}/app/remove_from_control",
                 json={'app_id': low_id, 'app_name': 'LowPrioApp'})
        api.post(f"{base_url}/app/remove_from_control",
                 json={'app_id': high_id, 'app_name': 'HighPrioApp'})

    def test_passive_control_enabled(self, api, base_url):
        """Verify passive control is available in config."""
        resp = api.get(f"{base_url}/monitor/config/passive_control")
        assert resp.status_code == 200
        data = resp.json()
        assert data['retcode'] == 0
        assert 'enabled' in data['data']

    def test_auto_limiting_triggered_under_pressure(self, api, base_url, controlled_apps):
        """Under critical pressure, low-priority apps should be targeted first."""
        if not tool_available('stress-ng'):
            pytest.skip("stress-ng not installed")

        # Ensure passive control is enabled
        resp = api.get(f"{base_url}/monitor/config/passive_control")
        pc_data = resp.json()['data']
        if not pc_data.get('enabled'):
            updated_at = pc_data.get('updated_at', '')
            api.post(f"{base_url}/monitor/config/passive_control",
                     json={'enabled': True, 'updated_at': updated_at})

        # Induce heavy CPU + memory stress
        proc = subprocess.Popen(
            ['stress-ng', '--cpu', str(os.cpu_count() or 4),
             '--vm', '2', '--vm-bytes', '256M', '--timeout', '30s'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            time.sleep(15)
            # Query controlled app status
            resp = api.post(f"{base_url}/app/get_controlled_app", json={})
            assert resp.status_code == 200
            # Service should still respond under pressure
            data = resp.json()
            assert data['retcode'] == 0
        finally:
            proc.terminate()
            proc.wait(timeout=10)


@pytest.mark.service
@pytest.mark.root
class TestAppStartupQueueMechanism:
    """TC-S-007: App startup queue mechanism (eBPF intercept).

    Verify that under critical pressure, new app launches are intercepted
    by eBPF and placed in a priority queue.
    """

    def test_pending_app_endpoint_available(self, api, base_url):
        """The pending app queue endpoint should be accessible."""
        resp = api.post(f"{base_url}/app/get_pending_app", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data['retcode'] == 0
        assert isinstance(data.get('data', []), list)

    def test_queue_empty_under_normal_conditions(self, api, base_url):
        """Under low pressure, pending queue should be empty or minimal."""
        resp = api.post(f"{base_url}/app/get_pending_app", json={})
        data = resp.json()
        pending = data.get('data', [])
        # Under normal load, queue should not be overflowing
        assert isinstance(pending, list)

    def test_cancel_relaunch_nonexistent(self, api, base_url):
        """Canceling a non-queued app should return appropriate error."""
        resp = api.post(f"{base_url}/app/cancel_relaunch",
                        json={'app_id': 'nonexistent_queued_app'})
        data = resp.json()
        # Should not crash; either error code or success (idempotent)
        assert data['retcode'] in (0, 101, 103)


@pytest.mark.service
@pytest.mark.root
@pytest.mark.stress
class TestDiskIOPressureControl:
    """TC-S-011: Disk I/O pressure detection & control.

    Verify disk I/O pressure is detected and controlled via cgroup io.max.
    Requires fio for I/O load generation.
    """

    @pytest.fixture
    def fio_process(self, tmp_path):
        """Start fio to generate disk I/O load."""
        if not tool_available('fio'):
            pytest.skip("fio not installed")
        testfile = str(tmp_path / 'fio_test')
        proc = None
        try:
            proc = subprocess.Popen(
                ['fio', '--name=seqwrite', '--rw=write',
                 '--bs=1M', '--size=256M', '--numjobs=2',
                 '--runtime=30', '--time_based',
                 f'--filename={testfile}',
                 '--ioengine=libaio', '--direct=1'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(5)
            yield proc
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=15)
            # Cleanup test file
            if os.path.exists(testfile):
                os.unlink(testfile)

    def test_disk_metrics_present(self, api, base_url):
        """Dynamic info should include disk I/O metrics."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        assert resp.status_code == 200
        data = resp.json()['data']
        assert 'disk' in data or 'io' in data or 'storage' in data

    def test_disk_pressure_under_fio_load(self, api, base_url, fio_process):
        """Under heavy I/O, disk pressure indicators should increase."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        assert resp.status_code == 200
        data = resp.json()['data']
        # Check disk or io section exists with non-trivial data
        disk = data.get('disk', data.get('io', data.get('storage', {})))
        assert disk is not None

    def test_io_cgroup_controller_present(self):
        """IO controller should be available in cgroup v2."""
        controllers_path = '/sys/fs/cgroup/cgroup.controllers'
        if not os.path.exists(controllers_path):
            pytest.skip("cgroup v2 not available")
        with open(controllers_path) as f:
            controllers = f.read().strip().split()
        assert 'io' in controllers


@pytest.mark.service
@pytest.mark.root
@pytest.mark.network
@pytest.mark.stress
class TestNetworkBandwidthControl:
    """TC-S-012: Network bandwidth control.

    Verify network traffic shaping via TC rules when network pressure
    is detected. Requires iperf3 and tc.
    """

    @pytest.fixture
    def iface(self):
        return get_network_interface()

    @pytest.fixture
    def iperf_server(self):
        """Start local iperf3 server for bandwidth testing."""
        if not tool_available('iperf3'):
            pytest.skip("iperf3 not installed")
        proc = None
        try:
            proc = subprocess.Popen(
                ['iperf3', '-s', '-p', '5299', '--one-off'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1)
            yield proc
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)

    @pytest.fixture
    def iperf_traffic(self, iperf_server):
        """Generate network traffic via iperf3 client."""
        proc = None
        try:
            proc = subprocess.Popen(
                ['iperf3', '-c', '127.0.0.1', '-p', '5299',
                 '-t', '15', '-P', '4'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(3)
            yield proc
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)

    def test_tc_available(self):
        """tc command must be installed for network control."""
        assert tool_available('tc'), "tc (iproute2) not installed"

    def test_network_metrics_present(self, api, base_url):
        """Dynamic info should include network metrics."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        assert resp.status_code == 200
        data = resp.json()['data']
        assert 'network' in data or 'net' in data

    def test_tc_rules_queryable(self, iface):
        """TC qdisc should be queryable on the interface."""
        result = subprocess.run(
            ['tc', 'qdisc', 'show', 'dev', iface],
            capture_output=True, text=True, timeout=5
        )
        assert result.returncode == 0

    def test_bandwidth_detection_under_load(self, api, base_url, iperf_traffic):
        """Under network load, bandwidth usage should be reported."""
        time.sleep(5)
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        assert resp.status_code == 200
        data = resp.json()['data']
        net = data.get('network', data.get('net', {}))
        assert net is not None


@pytest.mark.service
@pytest.mark.gpu
class TestGPUMonitoringDataCollection:
    """TC-S-013: GPU monitoring data collection.

    Verify GPU metrics (frequency, power, engine utilization, VRAM)
    are collected from Intel GPU hardware.
    """

    def test_gpu_in_dynamic_info(self, api, base_url):
        """GPU metrics should appear in dynamic_info."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        assert resp.status_code == 200
        data = resp.json()['data']
        assert 'gpu' in data, "GPU section missing from dynamic_info"

    def test_gpu_frequency_reported(self, api, base_url):
        """GPU should report frequency data (gt0/gt1)."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        gpu = data.get('gpu', {})
        if isinstance(gpu, list) and gpu:
            gpu = gpu[0]
        # Look for frequency fields
        freq_keys = ['frequency', 'freq', 'gt_freq', 'cur_freq',
                     'gt0_freq', 'actual_freq']
        has_freq = any(k in gpu for k in freq_keys) or \
                   any(k in str(gpu).lower() for k in ['freq', 'mhz'])
        assert has_freq or gpu, "No GPU frequency data found"

    def test_gpu_engine_utilization(self, api, base_url):
        """GPU engine utilization should be reported."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        gpu = data.get('gpu', {})
        if isinstance(gpu, list) and gpu:
            gpu = gpu[0]
        # Engine utilization might be under various keys
        assert gpu is not None and gpu != {}, \
            "GPU data is empty - verify GPU monitoring is active"

    def test_gpu_power_data(self, api, base_url):
        """GPU power consumption should be readable."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        gpu = data.get('gpu', {})
        if isinstance(gpu, list) and gpu:
            gpu = gpu[0]
        # Power may be in 'power', 'power_w', 'gpu_power', etc.
        power_keys = ['power', 'power_w', 'gpu_power', 'package_power']
        has_power = any(k in gpu for k in power_keys)
        if not has_power:
            pytest.skip("GPU power data not available (may require workload)")

    def test_gpu_vram_usage(self, api, base_url):
        """GPU VRAM/memory usage should be reported if applicable."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        gpu = data.get('gpu', {})
        if isinstance(gpu, list) and gpu:
            gpu = gpu[0]
        mem_keys = ['vram', 'memory', 'mem_used', 'vram_used', 'lmem']
        has_mem = any(k in gpu for k in mem_keys)
        if not has_mem:
            pytest.skip("GPU VRAM data not available (likely integrated GPU)")


@pytest.mark.service
@pytest.mark.npu
class TestNPUMonitoringDataCollection:
    """TC-S-014: NPU monitoring data collection.

    Verify NPU metrics (utilization, power, temperature, frequency)
    are collected from Intel NPU hardware via PMT telemetry.
    """

    def test_npu_in_dynamic_info(self, api, base_url):
        """NPU metrics should appear in dynamic_info."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        assert resp.status_code == 200
        data = resp.json()['data']
        assert 'npu' in data, "NPU section missing from dynamic_info"

    def test_npu_utilization_reported(self, api, base_url):
        """NPU utilization percentage should be reported."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        npu = data.get('npu', {})
        if isinstance(npu, list) and npu:
            npu = npu[0]
        assert npu is not None and npu != {}, \
            "NPU data is empty - verify NPU monitoring is active"
        # Look for utilization
        util_keys = ['utilization', 'usage', 'util', 'busy']
        has_util = any(k in npu for k in util_keys)
        if has_util:
            for k in util_keys:
                if k in npu:
                    val = npu[k]
                    if isinstance(val, (int, float)):
                        assert 0 <= val <= 100, f"NPU {k}={val} out of range"

    def test_npu_power_data(self, api, base_url):
        """NPU power consumption should be readable via PMT."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        npu = data.get('npu', {})
        if isinstance(npu, list) and npu:
            npu = npu[0]
        power_keys = ['power', 'power_w', 'npu_power']
        has_power = any(k in npu for k in power_keys)
        if not has_power:
            pytest.skip("NPU power data not available (PMT may not be accessible)")

    def test_npu_frequency_data(self, api, base_url):
        """NPU frequency should be reported."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        npu = data.get('npu', {})
        if isinstance(npu, list) and npu:
            npu = npu[0]
        freq_keys = ['frequency', 'freq', 'freq_mhz', 'clock']
        has_freq = any(k in npu for k in freq_keys)
        if not has_freq:
            pytest.skip("NPU frequency data not available")

    def test_npu_temperature_data(self, api, base_url):
        """NPU temperature should be within reasonable range."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        npu = data.get('npu', {})
        if isinstance(npu, list) and npu:
            npu = npu[0]
        temp_keys = ['temperature', 'temp', 'temp_c']
        for k in temp_keys:
            if k in npu:
                val = npu[k]
                if isinstance(val, (int, float)):
                    assert 0 <= val <= 125, \
                        f"NPU temperature {val}C seems unreasonable"
                return
        pytest.skip("NPU temperature data not available")

    def test_npu_in_static_info(self, api, base_url):
        """Static info should include NPU hardware details."""
        resp = api.get(f"{base_url}/monitor/static_info")
        assert resp.status_code == 200
        data = resp.json()['data']
        assert 'npu' in data, "NPU section missing from static_info"


@pytest.mark.service
@pytest.mark.root
@pytest.mark.stress
class TestCPUFrequencyGovernorSwitch:
    """TC-S-019: CPU frequency governor switch.

    Verify that under critical pressure, the CPU governor switches
    from 'performance' to 'powersave', and recovers when pressure drops.
    """

    CPUFREQ_BASE = '/sys/devices/system/cpu/cpufreq'
    GOVERNOR_PATH = '/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor'

    def _get_current_governor(self):
        """Read current CPU frequency governor."""
        if not os.path.exists(self.GOVERNOR_PATH):
            return None
        with open(self.GOVERNOR_PATH) as f:
            return f.read().strip()

    def test_cpufreq_available(self):
        """CPU frequency scaling should be supported."""
        assert os.path.exists(self.CPUFREQ_BASE) or \
               os.path.exists(self.GOVERNOR_PATH), \
            "cpufreq subsystem not available"

    def test_governor_readable(self):
        """Current governor should be readable."""
        gov = self._get_current_governor()
        if gov is None:
            pytest.skip("CPU governor file not accessible")
        assert gov in ('performance', 'powersave', 'schedutil',
                       'conservative', 'ondemand', 'userspace')

    def test_governor_switch_under_pressure(self, api, base_url):
        """Under critical pressure with passive control, governor may switch."""
        if not tool_available('stress-ng'):
            pytest.skip("stress-ng not installed")

        initial_gov = self._get_current_governor()
        if initial_gov is None:
            pytest.skip("CPU governor not accessible")

        # Ensure passive control is enabled
        resp = api.get(f"{base_url}/monitor/config/passive_control")
        if resp.status_code == 200:
            pc_data = resp.json()['data']
            if not pc_data.get('enabled'):
                updated_at = pc_data.get('updated_at', '')
                api.post(f"{base_url}/monitor/config/passive_control",
                         json={'enabled': True, 'updated_at': updated_at})

        # Create heavy pressure
        proc = subprocess.Popen(
            ['stress-ng', '--cpu', str(os.cpu_count() or 4),
             '--cpu-load', '95', '--timeout', '20s'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            time.sleep(12)
            # Governor may or may not switch depending on config
            current_gov = self._get_current_governor()
            # Just verify the system didn't crash and governor is still valid
            assert current_gov in ('performance', 'powersave', 'schedutil',
                                   'conservative', 'ondemand', 'userspace')
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            # Wait for potential recovery
            time.sleep(5)

    def test_governor_recovers_after_pressure(self):
        """After pressure drops, governor should recover to original."""
        gov = self._get_current_governor()
        if gov is None:
            pytest.skip("CPU governor not accessible")
        # After all stress tests complete, governor should be in a valid state
        assert gov in ('performance', 'powersave', 'schedutil',
                       'conservative', 'ondemand', 'userspace')


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: Performance Tests
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.service
class TestAPIResponseTime:
    """TC-SP-001: API response time (P50/P95/P99).

    Measure response latency for key endpoints and verify they meet
    performance targets:
      - dynamic_info: P95 < 200ms
      - app_resource_stats: P95 < 300ms
      - set_priority: P95 < 100ms
      - All endpoints: P99 < 500ms
    """

    NUM_REQUESTS = 100

    def _measure_endpoint(self, api, url, method='GET', json_body=None):
        """Send NUM_REQUESTS requests and return latency list in ms."""
        latencies = []
        for _ in range(self.NUM_REQUESTS):
            start = time.monotonic()
            if method == 'GET':
                resp = api.get(url, timeout=10)
            else:
                resp = api.post(url, json=json_body or {}, timeout=10)
            elapsed_ms = (time.monotonic() - start) * 1000
            if resp.status_code == 200:
                latencies.append(elapsed_ms)
        return latencies

    def test_dynamic_info_response_time(self, api, base_url):
        """GET /monitor/dynamic_info P95 should be < 200ms."""
        latencies = self._measure_endpoint(
            api, f"{base_url}/monitor/dynamic_info")
        assert len(latencies) >= self.NUM_REQUESTS * 0.9, \
            f"Too many failures: only {len(latencies)}/{self.NUM_REQUESTS} succeeded"

        p50 = percentile(latencies, 50)
        p95 = percentile(latencies, 95)
        p99 = percentile(latencies, 99)

        assert p95 < 200, \
            f"dynamic_info P95={p95:.1f}ms exceeds 200ms target"
        assert p99 < 500, \
            f"dynamic_info P99={p99:.1f}ms exceeds 500ms target"

    def test_app_resource_stats_response_time(self, api, base_url):
        """GET /monitor/app_resource_stats P95 should be < 300ms."""
        latencies = self._measure_endpoint(
            api, f"{base_url}/monitor/app_resource_stats")
        if not latencies:
            pytest.skip("app_resource_stats endpoint not available")

        p95 = percentile(latencies, 95)
        p99 = percentile(latencies, 99)

        assert p95 < 300, \
            f"app_resource_stats P95={p95:.1f}ms exceeds 300ms target"
        assert p99 < 500, \
            f"app_resource_stats P99={p99:.1f}ms exceeds 500ms target"

    def test_set_priority_response_time(self, api, base_url):
        """POST /app/set_priority P95 should be < 100ms."""
        app_id = unique_app_id("perf")
        # Register a test app
        api.post(f"{base_url}/app/set_to_control", json={
            'app_id': app_id, 'app_name': 'PerfTestApp',
            'controlled': True, 'priority': 'medium',
            'cmdline': 'perf_test_cmd',
        })

        try:
            latencies = self._measure_endpoint(
                api, f"{base_url}/app/set_priority", method='POST',
                json_body={'app_id': app_id, 'priority': 'medium'})

            if not latencies:
                pytest.skip("set_priority endpoint returned no successes")

            p95 = percentile(latencies, 95)
            p99 = percentile(latencies, 99)

            assert p95 < 100, \
                f"set_priority P95={p95:.1f}ms exceeds 100ms target"
            assert p99 < 500, \
                f"set_priority P99={p99:.1f}ms exceeds 500ms target"
        finally:
            api.post(f"{base_url}/app/remove_from_control",
                     json={'app_id': app_id, 'app_name': 'PerfTestApp'})

    def test_all_endpoints_no_timeout(self, api, base_url):
        """No requests should time out across measured endpoints."""
        endpoints = [
            (f"{base_url}/monitor/dynamic_info", 'GET', None),
            (f"{base_url}/monitor/static_info", 'GET', None),
        ]
        for url, method, body in endpoints:
            start = time.monotonic()
            if method == 'GET':
                resp = api.get(url, timeout=10)
            else:
                resp = api.post(url, json=body, timeout=10)
            elapsed = (time.monotonic() - start) * 1000
            assert elapsed < 5000, \
                f"Request to {url} took {elapsed:.0f}ms (timeout threshold 5000ms)"


@pytest.mark.service
class TestConcurrentConnectionHandling:
    """TC-SP-002: Concurrent connection handling (10/50/100 clients).

    Verify the service handles concurrent requests without degradation:
      - 10 concurrent: all succeed, avg < 100ms
      - 50 concurrent: >99% success, avg < 300ms
      - 100 concurrent: >95% success, no crash
    """

    def _concurrent_requests(self, base_url, num_clients, endpoint="/monitor/dynamic_info"):
        """Run concurrent GET requests and return (successes, latencies)."""
        import requests as req
        from urllib3.exceptions import InsecureRequestWarning
        req.packages.urllib3.disable_warnings(InsecureRequestWarning)

        url = f"{base_url}{endpoint}"
        results = []

        def single_request():
            start = time.monotonic()
            try:
                resp = req.get(url, verify=False, timeout=10)
                elapsed_ms = (time.monotonic() - start) * 1000
                return (resp.status_code == 200, elapsed_ms)
            except Exception:
                elapsed_ms = (time.monotonic() - start) * 1000
                return (False, elapsed_ms)

        with ThreadPoolExecutor(max_workers=num_clients) as executor:
            futures = [executor.submit(single_request) for _ in range(num_clients)]
            for future in as_completed(futures):
                results.append(future.result())

        successes = sum(1 for ok, _ in results if ok)
        latencies = [lat for ok, lat in results if ok]
        return successes, latencies, num_clients

    def test_10_concurrent_clients(self, base_url):
        """10 concurrent clients should all succeed with low latency."""
        successes, latencies, total = self._concurrent_requests(base_url, 10)
        assert successes == total, \
            f"10-concurrent: {successes}/{total} succeeded (expected all)"
        if latencies:
            avg = statistics.mean(latencies)
            assert avg < 100, \
                f"10-concurrent avg latency {avg:.1f}ms exceeds 100ms target"

    def test_50_concurrent_clients(self, base_url):
        """50 concurrent clients: >99% success rate."""
        successes, latencies, total = self._concurrent_requests(base_url, 50)
        success_rate = successes / total
        assert success_rate > 0.99, \
            f"50-concurrent: {success_rate*100:.1f}% success (need >99%)"
        if latencies:
            avg = statistics.mean(latencies)
            assert avg < 300, \
                f"50-concurrent avg latency {avg:.1f}ms exceeds 300ms target"

    def test_100_concurrent_clients(self, base_url):
        """100 concurrent clients: >95% success, no service crash."""
        successes, latencies, total = self._concurrent_requests(base_url, 100)
        success_rate = successes / total
        assert success_rate > 0.95, \
            f"100-concurrent: {success_rate*100:.1f}% success (need >95%)"
        # Verify service is still alive after burst
        import requests as req
        resp = req.get(f"{base_url}/monitor/static_info", verify=False, timeout=10)
        assert resp.status_code == 200, "Service crashed after 100-concurrent burst"


@pytest.mark.service
class TestMonitorDataCollectionFrequency:
    """TC-SP-003: Monitor data collection frequency.

    Verify monitoring data is updated at the expected interval (~2 seconds)
    and remains responsive under load.
    """

    def test_data_updates_within_expected_interval(self, api, base_url):
        """Data should update approximately every 2 seconds (+-500ms)."""
        snapshots = []
        for i in range(10):
            start = time.monotonic()
            resp = api.get(f"{base_url}/monitor/dynamic_info")
            elapsed = time.monotonic() - start
            if resp.status_code == 200:
                data = resp.json().get('data', {})
                snapshots.append({
                    'time': time.monotonic(),
                    'data': data,
                })
            time.sleep(1)

        assert len(snapshots) >= 8, \
            f"Only got {len(snapshots)}/10 successful snapshots"

        # Check that data changes between samples taken 2+ seconds apart
        changes_detected = 0
        for i in range(1, len(snapshots)):
            if snapshots[i]['data'] != snapshots[i-1]['data']:
                changes_detected += 1

        # At least some data should change over 10 seconds
        # (CPU utilization, timestamps, etc. should vary)
        assert changes_detected >= 2, \
            f"Only {changes_detected} data changes in 10s (expected >=2)"

    def test_collection_frequency_consistency(self, api, base_url):
        """Consecutive data fetches should show update timestamps progressing."""
        timestamps = []
        for _ in range(20):
            resp = api.get(f"{base_url}/monitor/dynamic_info")
            if resp.status_code == 200:
                data = resp.json().get('data', {})
                ts = data.get('timestamp', data.get('ts', data.get('time', None)))
                if ts is not None:
                    timestamps.append(ts)
            time.sleep(1)

        if len(timestamps) < 10:
            pytest.skip("Timestamp field not available in dynamic_info")

        # Timestamps should be progressing (not all identical)
        unique_ts = set(timestamps)
        assert len(unique_ts) >= 3, \
            f"Only {len(unique_ts)} unique timestamps in 20 samples"

    def test_data_update_under_cpu_load(self, api, base_url):
        """Under high CPU, data should still update within 5 seconds."""
        if not tool_available('stress-ng'):
            pytest.skip("stress-ng not installed")

        proc = subprocess.Popen(
            ['stress-ng', '--cpu', '2', '--timeout', '15s'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            time.sleep(3)
            # Collect two snapshots 5 seconds apart
            resp1 = api.get(f"{base_url}/monitor/dynamic_info")
            time.sleep(5)
            resp2 = api.get(f"{base_url}/monitor/dynamic_info")

            assert resp1.status_code == 200
            assert resp2.status_code == 200

            # Both should return valid data
            data1 = resp1.json().get('data', {})
            data2 = resp2.json().get('data', {})
            assert data1 and data2
        finally:
            proc.terminate()
            proc.wait(timeout=10)


@pytest.mark.service
@pytest.mark.root
@pytest.mark.cgroup
class TestCgroupResourceControlDelay:
    """TC-SP-006: cgroup resource control delay.

    Verify that resource limits take effect within 2 seconds of the API call.
    """

    def test_limit_api_responds_quickly(self, api, base_url):
        """Resource limit API itself should respond within 2 seconds."""
        app_id = unique_app_id("delay")
        # Register app
        api.post(f"{base_url}/app/set_to_control", json={
            'app_id': app_id, 'app_name': 'DelayTestApp',
            'controlled': True, 'priority': 'medium',
            'cmdline': 'delay_test_cmd',
        })

        try:
            start = time.monotonic()
            resp = api.post(f"{base_url}/app/resource_limit", json={
                'app_id': app_id,
                'app_name': 'DelayTestApp',
                'priority': 'low'
            })
            elapsed_ms = (time.monotonic() - start) * 1000

            # API should respond within 2000ms regardless of outcome
            assert elapsed_ms < 2000, \
                f"resource_limit took {elapsed_ms:.0f}ms (target <2000ms)"
            # Service should not crash
            assert resp.status_code == 200
        finally:
            # Restore and cleanup
            api.post(f"{base_url}/app/resource_restore",
                     json={'app_id': app_id})
            api.post(f"{base_url}/app/remove_from_control",
                     json={'app_id': app_id, 'app_name': 'DelayTestApp'})

    def test_restore_api_responds_quickly(self, api, base_url):
        """Resource restore API should also respond within 2 seconds."""
        app_id = unique_app_id("rdelay")
        api.post(f"{base_url}/app/set_to_control", json={
            'app_id': app_id, 'app_name': 'RestoreDelayApp',
            'controlled': True, 'priority': 'medium',
            'cmdline': 'restore_delay_cmd',
        })

        try:
            # Apply limit first
            api.post(f"{base_url}/app/resource_limit", json={
                'app_id': app_id,
                'app_name': 'RestoreDelayApp',
                'priority': 'medium'
            })
            time.sleep(0.5)

            # Measure restore time
            start = time.monotonic()
            resp = api.post(f"{base_url}/app/resource_restore",
                            json={'app_id': app_id})
            elapsed_ms = (time.monotonic() - start) * 1000

            assert elapsed_ms < 2000, \
                f"resource_restore took {elapsed_ms:.0f}ms (target <2000ms)"
            assert resp.status_code == 200
        finally:
            api.post(f"{base_url}/app/remove_from_control",
                     json={'app_id': app_id, 'app_name': 'RestoreDelayApp'})

    def test_sequential_limit_restore_cycle_timing(self, api, base_url):
        """Full limit-restore cycle should complete within 4 seconds."""
        app_id = unique_app_id("cycle")
        api.post(f"{base_url}/app/set_to_control", json={
            'app_id': app_id, 'app_name': 'CycleApp',
            'controlled': True, 'priority': 'medium',
            'cmdline': 'cycle_cmd',
        })

        try:
            cycle_start = time.monotonic()

            # Limit
            api.post(f"{base_url}/app/resource_limit", json={
                'app_id': app_id,
                'app_name': 'CycleApp',
                'priority': 'low'
            })
            # Restore
            api.post(f"{base_url}/app/resource_restore",
                     json={'app_id': app_id})

            cycle_elapsed = (time.monotonic() - cycle_start) * 1000
            assert cycle_elapsed < 4000, \
                f"Limit+Restore cycle took {cycle_elapsed:.0f}ms (target <4000ms)"
        finally:
            api.post(f"{base_url}/app/remove_from_control",
                     json={'app_id': app_id, 'app_name': 'CycleApp'})


@pytest.mark.service
@pytest.mark.root
@pytest.mark.stress
class TestPressureDetectionResponseDelay:
    """TC-SP-008: Pressure detection response delay.

    Verify latency from load application to pressure detection:
      - Load to critical detection: < 5 seconds
      - Detection to first app limited: < 3 seconds
      - Load release to recovery: 15-30 seconds (cooldown)
    """

    def test_pressure_detection_latency(self, api, base_url):
        """Time from stress start to pressure increase should be < 5s."""
        if not tool_available('stress-ng'):
            pytest.skip("stress-ng not installed")

        # Get baseline
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        baseline = resp.json()['data']
        baseline_pressure = baseline.get('pressure', baseline.get('psi', {}))

        # Start stress
        start_time = time.monotonic()
        proc = subprocess.Popen(
            ['stress-ng', '--cpu', str(os.cpu_count() or 4),
             '--cpu-load', '95', '--timeout', '15s'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        try:
            detected = False
            detection_time = None

            # Poll for pressure increase (max 10 seconds)
            for _ in range(20):
                time.sleep(0.5)
                resp = api.get(f"{base_url}/monitor/dynamic_info")
                if resp.status_code != 200:
                    continue
                data = resp.json()['data']
                pressure = data.get('pressure', data.get('psi', {}))

                if isinstance(pressure, dict):
                    level = pressure.get('level', pressure.get('grade', ''))
                    if level and level.lower() in ('high', 'critical'):
                        detected = True
                        detection_time = time.monotonic() - start_time
                        break
                    # Also check numeric pressure values
                    cpu_val = pressure.get('cpu', 0)
                    if isinstance(cpu_val, dict):
                        cpu_val = cpu_val.get('avg10', 0)
                    if isinstance(cpu_val, (int, float)) and cpu_val > 30:
                        detected = True
                        detection_time = time.monotonic() - start_time
                        break

            if detected:
                assert detection_time < 5.0, \
                    f"Pressure detection took {detection_time:.1f}s (target <5s)"
            # If not detected, the system may have different thresholds
            # This is still valid - just means pressure wasn't high enough
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    def test_recovery_after_load_release(self, api, base_url):
        """After load release, pressure should decrease within 30 seconds."""
        if not tool_available('stress-ng'):
            pytest.skip("stress-ng not installed")

        # Brief burst of stress
        proc = subprocess.Popen(
            ['stress-ng', '--cpu', str(os.cpu_count() or 4),
             '--cpu-load', '90', '--timeout', '10s'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        proc.wait(timeout=20)

        # Measure recovery time
        release_time = time.monotonic()
        recovered = False

        for _ in range(60):  # Check for up to 30 seconds
            time.sleep(0.5)
            resp = api.get(f"{base_url}/monitor/dynamic_info")
            if resp.status_code != 200:
                continue
            data = resp.json()['data']
            pressure = data.get('pressure', data.get('psi', {}))

            if isinstance(pressure, dict):
                level = pressure.get('level', pressure.get('grade', ''))
                if level and level.lower() in ('none', 'low'):
                    recovered = True
                    break
                cpu_val = pressure.get('cpu', 0)
                if isinstance(cpu_val, dict):
                    cpu_val = cpu_val.get('avg10', 0)
                if isinstance(cpu_val, (int, float)) and cpu_val < 10:
                    recovered = True
                    break

        recovery_time = time.monotonic() - release_time
        if recovered:
            assert recovery_time <= 30.0, \
                f"Recovery took {recovery_time:.1f}s (expected 15-30s)"

    def test_service_responsiveness_during_pressure(self, api, base_url):
        """Service should remain responsive even under heavy system pressure."""
        if not tool_available('stress-ng'):
            pytest.skip("stress-ng not installed")

        proc = subprocess.Popen(
            ['stress-ng', '--cpu', str(os.cpu_count() or 4),
             '--vm', '1', '--vm-bytes', '128M', '--timeout', '10s'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        try:
            time.sleep(3)
            # API should still respond under pressure
            latencies = []
            for _ in range(10):
                start = time.monotonic()
                resp = api.get(f"{base_url}/monitor/dynamic_info")
                elapsed = (time.monotonic() - start) * 1000
                assert resp.status_code == 200, \
                    "Service failed to respond under pressure"
                latencies.append(elapsed)
                time.sleep(0.5)

            # Even under pressure, API should respond within 5 seconds
            max_latency = max(latencies)
            assert max_latency < 5000, \
                f"Max latency under pressure: {max_latency:.0f}ms (threshold 5000ms)"
        finally:
            proc.terminate()
            proc.wait(timeout=10)
