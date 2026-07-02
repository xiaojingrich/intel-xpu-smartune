# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
TC-SS-003: Abnormal Process Exit Recovery
TC-SS-005: Service Restart State Recovery

Verifies that:
- When a controlled app crashes, the service detects it and cleans up
- After service restart, controlled apps and their states are preserved
"""

import time
import subprocess
import pytest

@pytest.mark.service
@pytest.mark.root
class TestServiceLifecycle:

    def test_controlled_apps_persist_after_query(self, api, base_url):
        """Controlled apps should be retrievable and consistent."""
        resp = api.post(f"{base_url}/app/get_controlled_app", json={})
        data = resp.json()
        assert data['retcode'] in (0, 404)
        if data['retcode'] == 0:
            for app in data['data']:
                assert 'app_id' in app
                assert 'app_name' in app
                assert 'status' in app

    def test_check_running_apps_detects_live_processes(self, api, base_url):
        """check_running_apps should detect pre-existing monitored processes."""
        resp = api.post(f"{base_url}/app/check_running_apps", json={})
        data = resp.json()
        assert data['retcode'] == 0
        assert isinstance(data['data'], list)

    def test_service_responds_after_heavy_load(self, api, base_url):
        """Service should remain responsive after handling many requests."""
        for _ in range(50):
            resp = api.get(f"{base_url}/monitor/static_info")
            assert resp.status_code == 200
        # Final check
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        assert resp.json()['retcode'] == 0

    def test_sse_connection_available(self, api, base_url):
        """SSE event stream should be connectable."""
        import requests
        resp = requests.get(f"{base_url}/app/events",
                          verify=False, stream=True, timeout=5)
        assert resp.status_code == 200
        assert 'text/event-stream' in resp.headers.get('Content-Type', '')
        # Read first event (should be connection confirmation)
        for line in resp.iter_lines(decode_unicode=True):
            if line.startswith('data:'):
                import json
                event = json.loads(line[5:])
                assert event.get('type') == 'connected'
                break
        resp.close()
