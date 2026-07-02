# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Acceptance tests: Service Lifecycle
Verify service startup, health, and graceful shutdown behavior.
"""

import time
import subprocess

import pytest
import requests

from conftest import BASE_URL, TIMEOUT


@pytest.mark.service
class TestServiceHealth:
    """Verify the service is running and responsive."""

    def test_service_reachable(self, api, base_url):
        """Service HTTPS endpoint should be reachable."""
        resp = api.get(f"{base_url}/monitor/static_info")
        assert resp.status_code == 200

    def test_response_format(self, api, base_url):
        """All responses should follow {retcode, retmsg, data} format."""
        resp = api.get(f"{base_url}/monitor/static_info")
        data = resp.json()
        assert 'retcode' in data
        assert 'retmsg' in data
        assert data['retcode'] == 0

    def test_cors_headers(self, api, base_url):
        """Responses should include CORS headers."""
        resp = api.get(f"{base_url}/monitor/static_info")
        assert resp.headers.get('Access-Control-Allow-Origin') == '*'

    def test_ssl_certificate(self, base_url):
        """Service should serve over HTTPS (self-signed cert accepted)."""
        resp = requests.get(f"{base_url}/monitor/static_info", verify=False, timeout=5)
        assert resp.status_code == 200

    def test_invalid_endpoint_returns_404(self, api, base_url):
        """Non-existent endpoints should return 404."""
        resp = api.get(f"{base_url}/nonexistent/path")
        assert resp.status_code == 404


@pytest.mark.service
class TestAuthentication:
    """Verify authentication endpoint behavior."""

    def test_login_with_invalid_token(self, api, base_url):
        """Login with wrong token should fail gracefully."""
        resp = api.post(f"{base_url}/auth/login", json={'pwd': 'wrong_token'})
        data = resp.json()
        assert data['retcode'] == 0
        assert data['data']['authenticated'] is False

    def test_login_missing_token(self, api, base_url):
        """Login without token should return argument error."""
        resp = api.post(f"{base_url}/auth/login", json={})
        data = resp.json()
        assert data['retcode'] == 101

    def test_login_empty_body(self, api, base_url):
        """Login with no JSON body should handle gracefully."""
        resp = api.post(f"{base_url}/auth/login",
                       data='', headers={'Content-Type': 'application/json'})
        # Should not crash the service
        assert resp.status_code in (200, 400, 415)


@pytest.mark.service
class TestSSEEvents:
    """Verify Server-Sent Events endpoint."""

    def test_sse_connection(self, base_url):
        """SSE endpoint should establish and send connected event."""
        with requests.get(f"{base_url}/app/events", stream=True,
                         verify=False, timeout=5) as resp:
            assert resp.status_code == 200
            assert 'text/event-stream' in resp.headers.get('Content-Type', '')

            # Read the first event
            for line in resp.iter_lines(decode_unicode=True):
                if line.startswith('data:'):
                    import json
                    data = json.loads(line[5:].strip())
                    assert data.get('type') == 'connected'
                    break

    def test_sse_headers(self, base_url):
        """SSE should have proper no-cache headers."""
        with requests.get(f"{base_url}/app/events", stream=True,
                         verify=False, timeout=5) as resp:
            assert resp.headers.get('Cache-Control') == 'no-cache'
            assert resp.headers.get('X-Accel-Buffering') == 'no'
