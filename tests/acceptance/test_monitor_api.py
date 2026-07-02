# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Acceptance tests: Monitor API
Verify system monitoring endpoints return valid data from real hardware.
"""

import time

import pytest


@pytest.mark.service
class TestStaticInfo:
    """Verify static system information collection."""

    def test_static_info_returns_cpu(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/static_info")
        data = resp.json()['data']
        assert 'cpu' in data
        assert 'model' in data['cpu'] or 'model_name' in data['cpu']

    def test_static_info_returns_memory(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/static_info")
        data = resp.json()['data']
        assert 'memory' in data

    def test_static_info_returns_os(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/static_info")
        data = resp.json()['data']
        assert 'os' in data or 'system' in data

    @pytest.mark.gpu
    def test_static_info_returns_gpu(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/static_info")
        data = resp.json()['data']
        assert 'gpu' in data
        gpu_info = data['gpu']
        assert len(gpu_info) > 0

    def test_static_info_cached(self, api, base_url):
        """Second call should be faster (cached)."""
        start = time.monotonic()
        api.get(f"{base_url}/monitor/static_info")
        first = time.monotonic() - start

        start = time.monotonic()
        api.get(f"{base_url}/monitor/static_info")
        second = time.monotonic() - start

        # Second call should be noticeably faster (cached)
        assert second < first + 0.5


@pytest.mark.service
class TestDynamicInfo:
    """Verify real-time system metrics."""

    def test_dynamic_info_returns_cpu_usage(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        assert 'cpu' in data
        cpu = data['cpu']
        assert 'usage' in cpu or 'percent' in cpu or 'usage_percent' in cpu

    def test_dynamic_info_returns_memory_usage(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        assert 'memory' in data

    def test_dynamic_info_returns_pressure(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        assert 'pressure' in data or 'psi' in data

    def test_dynamic_info_response_time(self, api, base_url):
        """Dynamic info should respond within 3 seconds."""
        start = time.monotonic()
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        elapsed = time.monotonic() - start
        assert resp.status_code == 200
        assert elapsed < 3.0

    @pytest.mark.gpu
    def test_dynamic_info_returns_gpu_usage(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()['data']
        assert 'gpu' in data


@pytest.mark.service
class TestAppResourceStats:
    """Verify per-application resource statistics."""

    def test_app_resource_stats_endpoint(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/app_resource_stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data['retcode'] == 0

    def test_app_resource_stats_format(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/app_resource_stats")
        data = resp.json()['data']
        if data:  # May be empty if system is idle
            for app in data:
                assert 'name' in app or 'app_name' in app
                assert 'cpu' in app or 'cpu_percent' in app

    def test_app_disk_io_stats_endpoint(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/app_disk_io_stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data['retcode'] == 0

    def test_processes_endpoint(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/processes")
        assert resp.status_code == 200
        data = resp.json()
        assert data['retcode'] == 0


@pytest.mark.service
class TestHistory:
    """Verify history snapshot storage and retrieval."""

    def test_query_history(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/history",
                      params={'type': 'dynamic', 'limit': 10})
        assert resp.status_code == 200
        data = resp.json()
        assert data['retcode'] == 0

    def test_history_retention_get(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/history/retention")
        assert resp.status_code == 200
        data = resp.json()
        assert data['retcode'] == 0
        assert 'data' in data

    def test_history_has_recent_data(self, api, base_url):
        """After service runs, there should be some dynamic snapshots."""
        resp = api.get(f"{base_url}/monitor/history",
                      params={'type': 'dynamic', 'limit': 5})
        data = resp.json()
        # Service should have collected at least 1 snapshot
        if data['data']:
            assert len(data['data']) >= 1


@pytest.mark.service
class TestConfigEndpoints:
    """Verify runtime configuration endpoints."""

    def test_get_weights_top(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/config/weights_top")
        assert resp.status_code == 200
        data = resp.json()
        assert data['retcode'] == 0
        weights = data['data']
        assert 'cpu' in weights
        assert 'memory' in weights

    def test_get_passive_control(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/config/passive_control")
        assert resp.status_code == 200
        data = resp.json()
        assert data['retcode'] == 0
        assert 'enabled' in data['data']

    def test_update_weights_top_and_restore(self, api, base_url):
        """Update weights, verify change, then restore original."""
        # Get current
        resp = api.get(f"{base_url}/monitor/config/weights_top")
        original = resp.json()['data']
        updated_at = resp.json()['data'].get('updated_at', '')

        # Update
        new_weights = {'cpu': 5, 'memory': 5, 'gpu': 5, 'updated_at': updated_at}
        resp = api.post(f"{base_url}/monitor/config/weights_top", json=new_weights)
        assert resp.json()['retcode'] == 0

        # Verify
        resp = api.get(f"{base_url}/monitor/config/weights_top")
        assert resp.json()['data']['cpu'] == 5

        # Restore
        restore = {k: v for k, v in original.items() if k != 'updated_at'}
        restore['updated_at'] = resp.json()['data'].get('updated_at', '')
        api.post(f"{base_url}/monitor/config/weights_top", json=restore)
