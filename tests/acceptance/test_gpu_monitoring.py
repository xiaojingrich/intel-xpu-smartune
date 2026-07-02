# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Acceptance tests: GPU Monitoring
Verify Intel GPU/NPU metrics collection on real hardware.
"""

import os
import subprocess

import pytest


@pytest.mark.service
@pytest.mark.gpu
class TestGPUDetection:
    """Verify Intel GPU hardware detection."""

    def test_drm_device_exists(self):
        """Intel DRM device should be present."""
        assert os.path.exists('/sys/class/drm/card0')

    def test_gpu_driver_loaded(self):
        """Intel GPU driver (i915 or xe) should be loaded."""
        result = subprocess.run(
            ['lsmod'],
            capture_output=True, text=True, timeout=5
        )
        modules = result.stdout
        assert 'i915' in modules or 'xe' in modules

    def test_render_device_accessible(self):
        """Render device node should be accessible."""
        render_nodes = [f'/dev/dri/renderD{n}' for n in range(128, 136)]
        found = any(os.path.exists(node) for node in render_nodes)
        assert found, "No DRI render device found"

    def test_gpu_frequency_file_readable(self):
        """GPU frequency info should be readable from sysfs."""
        freq_paths = [
            '/sys/class/drm/card0/gt_cur_freq_mhz',
            '/sys/class/drm/card0/gt/gt0/rps_cur_freq_mhz',
        ]
        readable = False
        for path in freq_paths:
            if os.path.exists(path):
                with open(path) as f:
                    freq = f.read().strip()
                assert int(freq) >= 0
                readable = True
                break
        if not readable:
            pytest.skip("No readable GPU frequency file found")


@pytest.mark.service
@pytest.mark.gpu
class TestGPUMetricsViaAPI:
    """Verify GPU metrics are reported through the SmartTune API."""

    def test_static_info_includes_gpu(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/static_info")
        data = resp.json()['data']
        assert 'gpu' in data
        gpu = data['gpu']
        assert gpu is not None

    def test_gpu_static_has_device_info(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/static_info")
        gpu = resp.json()['data'].get('gpu', {})
        if isinstance(gpu, list) and gpu:
            device = gpu[0]
            assert 'name' in device or 'device' in device or 'model' in device
        elif isinstance(gpu, dict):
            assert len(gpu) > 0

    def test_dynamic_info_includes_gpu(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        assert 'gpu' in data

    def test_gpu_utilization_in_range(self, api, base_url):
        """GPU utilization should be between 0 and 100%."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        gpu = resp.json()['data'].get('gpu', {})
        if isinstance(gpu, list) and gpu:
            for device in gpu:
                usage = device.get('usage', device.get('utilization', -1))
                if isinstance(usage, (int, float)):
                    assert 0 <= usage <= 100
        elif isinstance(gpu, dict):
            usage = gpu.get('usage', gpu.get('utilization', -1))
            if isinstance(usage, (int, float)):
                assert 0 <= usage <= 100


@pytest.mark.service
@pytest.mark.gpu
class TestGPUFirmware:
    """Verify GPU firmware (GuC/HuC) status."""

    def test_guc_huc_status(self):
        """GuC/HuC firmware should be loaded."""
        guc_paths = [
            '/sys/kernel/debug/dri/0/gt0/uc/guc_info',
            '/sys/kernel/debug/dri/0/i915_guc_info',
        ]
        for path in guc_paths:
            if os.path.exists(path):
                with open(path) as f:
                    content = f.read()
                # GuC should report as running/loaded
                assert 'status' in content.lower() or 'version' in content.lower()
                return
        pytest.skip("GuC info not accessible (may need debugfs mount)")

    def test_gpu_memory_info(self):
        """GPU memory regions should be reportable."""
        mem_paths = [
            '/sys/class/drm/card0/lmem_total_bytes',
            '/sys/kernel/debug/dri/0/i915_gem_objects',
        ]
        for path in mem_paths:
            if os.path.exists(path):
                with open(path) as f:
                    content = f.read().strip()
                assert len(content) > 0
                return
        # Integrated GPUs may not have dedicated LMEM
        pytest.skip("No GPU memory info file found (likely integrated GPU)")


@pytest.mark.service
@pytest.mark.npu
class TestNPUDetection:
    """Verify Intel NPU hardware detection."""

    def test_npu_device_exists(self):
        """Intel NPU device should be present in sysfs."""
        npu_paths = [
            '/sys/class/accel/',
            '/dev/accel/',
        ]
        found = any(os.path.exists(p) for p in npu_paths)
        assert found, "No NPU device path found"

    def test_npu_driver_loaded(self):
        """Intel NPU driver should be loaded."""
        result = subprocess.run(
            ['lsmod'],
            capture_output=True, text=True, timeout=5
        )
        modules = result.stdout
        assert 'intel_vpu' in modules or 'ivpu' in modules or 'npu' in modules.lower()


@pytest.mark.service
@pytest.mark.gpu
class TestIntelGPUTools:
    """Verify intel_gpu_top or equivalent tools are available."""

    def test_intel_gpu_top_installed(self):
        """intel_gpu_top should be available for monitoring."""
        result = subprocess.run(
            ['which', 'intel_gpu_top'],
            capture_output=True, timeout=5
        )
        if result.returncode != 0:
            pytest.skip("intel_gpu_top not installed")

    def test_xpu_smi_available(self):
        """xpu-smi or similar tool should be available."""
        tools = ['xpu-smi', 'xpumcli', 'intel_gpu_top']
        found = False
        for tool in tools:
            result = subprocess.run(
                ['which', tool],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                found = True
                break
        if not found:
            pytest.skip("No Intel GPU monitoring tool found")
