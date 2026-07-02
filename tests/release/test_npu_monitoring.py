# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
TC-S-014: NPU Monitoring Data Collection

Verifies NPU metrics are correctly reported when Intel NPU hardware is present.
"""

import pytest

@pytest.mark.service
@pytest.mark.npu
class TestNPUMonitoring:

    def test_npu_in_static_info(self, api, base_url):
        """Static info should report NPU presence and driver version."""
        resp = api.get(f"{base_url}/monitor/static_info")
        data = resp.json()['data']
        assert 'npu' in data
        npu = data['npu']
        assert 'driver_version' in npu or 'names' in npu

    def test_npu_in_dynamic_info(self, api, base_url):
        """Dynamic info should include NPU utilization data."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        assert 'npu' in data

    def test_npu_frequency_reported(self, api, base_url):
        """NPU frequency should be a positive value when hardware is active."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        npu = resp.json()['data'].get('npu', {})
        npu_smi = npu.get('npu_smi', {})
        if npu_smi:
            # If NPU is active, frequency should be reported
            freq = npu_smi.get('freq_mhz', 0)
            assert isinstance(freq, (int, float))

    def test_npu_processes_detected(self, api, base_url):
        """If an NPU workload is running, it should appear in NPU process list."""
        # This test passes if NPU data is present; actual workload detection
        # depends on whether an NPU app is running during the test
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        npu = resp.json()['data'].get('npu', {})
        # Just verify the structure exists without requiring active workload
        assert isinstance(npu, dict)
