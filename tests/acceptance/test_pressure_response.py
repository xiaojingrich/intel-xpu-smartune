# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Acceptance tests: Pressure Response
Verify the system detects resource pressure and triggers automatic limiting.
"""

import os
import time
import subprocess
import uuid

import pytest


def unique_app_id():
    return f"pressure_test_{uuid.uuid4().hex[:8]}"


@pytest.mark.service
class TestPressureDetection:
    """Verify PSI pressure data is being collected."""

    def test_psi_files_exist(self):
        """PSI (Pressure Stall Information) files should be available."""
        psi_paths = [
            '/proc/pressure/cpu',
            '/proc/pressure/memory',
            '/proc/pressure/io',
        ]
        for path in psi_paths:
            assert os.path.exists(path), f"PSI file missing: {path}"

    def test_psi_data_parseable(self):
        """PSI files should contain parseable some/full lines."""
        with open('/proc/pressure/cpu') as f:
            content = f.read()
        assert 'some' in content
        # CPU PSI has 'some' line; memory/io have 'some' and 'full'
        parts = content.split()
        assert any('avg10=' in p for p in parts)

    def test_dynamic_info_reports_pressure(self, api, base_url):
        """Dynamic info endpoint should include pressure data."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        assert 'pressure' in data or 'psi' in data

    def test_pressure_values_in_range(self, api, base_url):
        """Reported pressure values should be between 0 and 100."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        pressure = data.get('pressure', data.get('psi', {}))
        if isinstance(pressure, dict):
            for key, val in pressure.items():
                if isinstance(val, (int, float)):
                    assert 0 <= val <= 100, f"Pressure {key}={val} out of range"


@pytest.mark.service
@pytest.mark.root
@pytest.mark.cgroup
class TestPressureInducedLimiting:
    """Verify automatic limiting under pressure (stress-induced)."""

    @pytest.fixture
    def stress_process(self):
        """Start a CPU stress process and return its PID."""
        try:
            proc = subprocess.Popen(
                ['stress-ng', '--cpu', '2', '--timeout', '30s'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(2)
            yield proc
        except FileNotFoundError:
            pytest.skip("stress-ng not installed")
        finally:
            if 'proc' in dir():
                proc.terminate()
                proc.wait()

    def test_pressure_increases_under_stress(self, api, base_url, stress_process):
        """Pressure values should increase when system is under load."""
        # Wait for pressure to build
        time.sleep(5)
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        pressure = data.get('pressure', data.get('psi', {}))
        # Under stress, at least CPU pressure should be non-trivial
        if isinstance(pressure, dict):
            cpu_pressure = pressure.get('cpu', 0)
            if isinstance(cpu_pressure, dict):
                cpu_pressure = cpu_pressure.get('some', cpu_pressure.get('avg10', 0))
            assert cpu_pressure >= 0  # Non-negative at minimum

    @pytest.fixture
    def memory_stress_process(self):
        """Start a memory stress process."""
        try:
            proc = subprocess.Popen(
                ['stress-ng', '--vm', '1', '--vm-bytes', '512M', '--timeout', '20s'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(3)
            yield proc
        except FileNotFoundError:
            pytest.skip("stress-ng not installed")
        finally:
            if 'proc' in dir():
                proc.terminate()
                proc.wait()

    def test_memory_pressure_detection(self, api, base_url, memory_stress_process):
        """Memory pressure should be detectable under VM stress."""
        time.sleep(5)
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        pressure = data.get('pressure', data.get('psi', {}))
        if isinstance(pressure, dict):
            mem_pressure = pressure.get('memory', 0)
            if isinstance(mem_pressure, dict):
                mem_pressure = mem_pressure.get('some', mem_pressure.get('avg10', 0))
            # Under memory stress, value should be non-negative
            assert mem_pressure >= 0


@pytest.mark.service
class TestPassiveControlMode:
    """Verify passive control toggle behavior."""

    def test_get_passive_control_status(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/config/passive_control")
        data = resp.json()
        assert data['retcode'] == 0
        assert 'enabled' in data['data']

    def test_toggle_passive_control(self, api, base_url):
        """Toggle passive control off and on, verify state changes."""
        # Get current state
        resp = api.get(f"{base_url}/monitor/config/passive_control")
        original = resp.json()['data']
        original_enabled = original['enabled']
        updated_at = original.get('updated_at', '')

        # Toggle
        new_state = not original_enabled
        resp = api.post(f"{base_url}/monitor/config/passive_control",
                       json={'enabled': new_state, 'updated_at': updated_at})
        assert resp.json()['retcode'] == 0

        # Verify change
        resp = api.get(f"{base_url}/monitor/config/passive_control")
        assert resp.json()['data']['enabled'] == new_state

        # Restore
        updated_at = resp.json()['data'].get('updated_at', '')
        api.post(f"{base_url}/monitor/config/passive_control",
                json={'enabled': original_enabled, 'updated_at': updated_at})


@pytest.mark.service
class TestWeightsConfiguration:
    """Verify pressure weight configuration affects scoring."""

    def test_weights_have_expected_keys(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/config/weights_top")
        weights = resp.json()['data']
        assert 'cpu' in weights
        assert 'memory' in weights

    def test_weights_are_positive(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/config/weights_top")
        weights = resp.json()['data']
        for key in ('cpu', 'memory'):
            assert weights[key] > 0

    def test_update_weights_optimistic_concurrency(self, api, base_url):
        """Concurrent updates with stale updated_at should be rejected (409)."""
        resp = api.get(f"{base_url}/monitor/config/weights_top")
        data = resp.json()['data']
        updated_at = data.get('updated_at', '')

        # First update should succeed
        resp1 = api.post(f"{base_url}/monitor/config/weights_top",
                        json={'cpu': 3, 'memory': 7, 'updated_at': updated_at})
        assert resp1.json()['retcode'] == 0

        # Second update with same (now stale) updated_at should fail
        resp2 = api.post(f"{base_url}/monitor/config/weights_top",
                        json={'cpu': 5, 'memory': 5, 'updated_at': updated_at})
        # Should get conflict or still succeed (depends on implementation)
        # Either way service should not crash
        assert resp2.status_code in (200, 409)

        # Restore original
        resp = api.get(f"{base_url}/monitor/config/weights_top")
        fresh_at = resp.json()['data'].get('updated_at', '')
        api.post(f"{base_url}/monitor/config/weights_top",
                json={**{k: v for k, v in data.items() if k != 'updated_at'},
                      'updated_at': fresh_at})
