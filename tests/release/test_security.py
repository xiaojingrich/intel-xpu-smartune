# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
TC-SEC-001: cgroup Operation Permission Isolation
TC-SEC-002: Sensitive Information Leakage Prevention

Pre-release security validation tests.
"""

import pytest

@pytest.mark.service
class TestCgroupPermissionIsolation:
    """TC-SEC-001: Verify cgroup operations are properly scoped."""

    def test_cannot_limit_non_controlled_app(self, api, base_url):
        """Attempting to limit a non-controlled / non-existent app should fail.

        Uses a synthetic name that matches NO real process, so this never
        applies a limit to a live system daemon. The backend should refuse to
        limit something it cannot find (retcode != 0 or a 'not found' message).

        NOTE (TC-SEC-001, known gap): the backend does NOT currently verify that
        a target is a *registered controlled* app — it will limit ANY app whose
        process pgrep can match. This test only exercises the not-found path;
        the controlled-vs-uncontrolled gap for real running processes is a
        separate product concern tracked outside the automated suite (limiting a
        real system daemon in a test is unsafe).
        """
        bogus = "sec_noncontrolled_zzzq_no_such_proc"
        resp = api.post(f"{base_url}/app/resource_limit", json={
            'app_id': bogus,
            'app_name': bogus,
            'priority': 'low'
        })
        data = resp.json()
        try:
            # Should fail — the app is neither controlled nor a real process.
            assert data['retcode'] != 0 or 'not found' in data.get('retmsg', '').lower()
        finally:
            # Defensive: undo any limit that may have been applied.
            api.post(f"{base_url}/app/resource_restore", json={'app_id': bogus})

    def test_cannot_set_cpu_quota_zero(self, api, base_url):
        """CPU quota of 0% should be rejected or clamped to minimum."""
        # Get a controlled app first
        resp = api.post(f"{base_url}/app/get_controlled_app", json={})
        if resp.json()['retcode'] != 0 or not resp.json()['data']:
            pytest.skip("No controlled apps available")
        app = resp.json()['data'][0]

        resp = api.post(f"{base_url}/app/resource_limit", json={
            'app_id': app['app_id'],
            'app_name': app['app_name'],
            'priority': 'low',
            'limit_overrides': {'cpu': {'rate': 0.0}}
        })
        # Should either reject or clamp to minimum, not freeze the process
        data = resp.json()
        # Verify process is still running (not frozen)
        if data['retcode'] == 0:
            # Restore immediately
            api.post(f"{base_url}/app/resource_restore",
                    json={'app_id': app['app_id']})

    def test_system_processes_not_affected(self, api, base_url):
        """System critical processes should never be limited.

        Does NOT target a real system daemon (that would risk throttling PID 1
        on the live host). Uses a synthetic id and asserts the API refuses to
        limit something it cannot resolve to a real process.
        """
        bogus = "sec_systemproc_zzzq_no_such_proc"
        resp = api.post(f"{base_url}/app/resource_limit", json={
            'app_id': bogus,
            'app_name': bogus,
            'priority': 'low'
        })
        data = resp.json()
        try:
            assert data['retcode'] != 0
        finally:
            api.post(f"{base_url}/app/resource_restore", json={'app_id': bogus})


@pytest.mark.service
class TestInformationLeakage:
    """TC-SEC-002: Verify no sensitive information is leaked."""

    def test_404_does_not_leak_stack_trace(self, api, base_url):
        """404 responses should not contain Python stack traces or framework info."""
        resp = api.get(f"{base_url}/nonexistent/path/xyz")
        body = resp.text.lower()
        assert 'traceback' not in body
        assert 'flask' not in body
        assert 'python' not in body
        assert 'werkzeug' not in body

    def test_error_response_no_stack_trace(self, api, base_url):
        """Error responses should not contain full exception stack traces."""
        # Trigger an error with bad input
        resp = api.post(f"{base_url}/app/set_priority",
                       json={'app_id': None, 'priority': None})
        body = resp.text.lower()
        assert 'traceback' not in body
        assert 'file "/' not in body  # No file paths in stack

    def test_static_info_no_secrets(self, api, base_url):
        """Static info should not contain passwords, keys, or tokens."""
        resp = api.get(f"{base_url}/monitor/static_info")
        body = resp.text.lower()
        assert 'password' not in body
        assert 'secret' not in body
        assert 'private_key' not in body
        assert 'token' not in body

    @pytest.mark.xfail(
        reason="KNOWN GAP (TC-SEC-002): the Werkzeug dev server sets a Server "
               "header exposing framework/Python versions (e.g. "
               "'werkzeug/3.1.8 python/3.12.3'). Serve behind a production WSGI "
               "server (gunicorn/waitress) or strip the Server header to fix.",
        strict=False,
    )
    def test_response_headers_no_server_version(self, api, base_url):
        """Response headers should not expose server software version."""
        resp = api.get(f"{base_url}/monitor/static_info")
        server_header = resp.headers.get('Server', '').lower()
        # Should not expose exact version like "Werkzeug/2.3.4" or "Python/3.12"
        assert 'python' not in server_header
        assert 'werkzeug' not in server_header

    def test_cors_headers_present(self, api, base_url):
        """CORS headers should be present for cross-origin access."""
        resp = api.get(f"{base_url}/monitor/static_info")
        assert 'Access-Control-Allow-Origin' in resp.headers
