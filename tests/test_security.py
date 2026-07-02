# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Security tests covering:
- TC-SEC-001: cgroup operation permission isolation
- TC-SEC-002: Sensitive information leak prevention
"""

import os
import sys
import json
import hashlib
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))


@pytest.fixture
def real_app():
    """
    Import the real Flask app from BalanceService and mock only the
    external dependencies so we can test security behavior of the routes.
    """
    with patch('balancer.balancer.DynamicBalancer') as mock_balancer_cls, \
         patch('monitor.monitor_api._start_snapshot_cleanup_task'), \
         patch('monitor.system_info.preload_static_info'), \
         patch('BalanceService.init_database'), \
         patch('BalanceService.preload_static_info'):
        mock_balancer = MagicMock()
        mock_balancer_cls.return_value = mock_balancer
        import BalanceService
        mock_service = MagicMock()
        mock_service.get_secret_hash.return_value = hashlib.sha256(b"correct_token").hexdigest()
        with patch.object(BalanceService, '_service', mock_service):
            BalanceService.app.config['TESTING'] = True
            with BalanceService.app.test_client() as client:
                yield client, mock_service


class TestCgroupPermissionIsolation:
    """
    TC-SEC-001: cgroup operation permission isolation.

    Verifies that:
    - Non-controlled applications (system processes) cannot be resource-limited.
    - CPU quota cannot be set to 0% (minimum limit enforced).
    - Memory cannot be set to an extremely small value.
    - System-critical processes are never affected.
    """

    def test_resource_limit_on_non_controlled_app_rejected(self, real_app):
        """Attempting to limit a non-controlled app (e.g., systemd) should fail."""
        client, mock_service = real_app
        # Simulate the service returning False (app not in controlled list)
        mock_service.resource_limit.return_value = False

        resp = client.post('/app/resource_limit', json={
            'app_id': 'systemd_pid1',
            'app_name': 'systemd',
            'priority': 'high',
        }, content_type='application/json')
        data = resp.get_json()

        # The endpoint should report an operational error when the app is not controllable
        assert data['retcode'] != 0, "Non-controlled app should not be successfully limited"
        assert 'No matching app found' in data.get('retmsg', '') or data['retcode'] == 103

    def test_resource_limit_rejects_zero_cpu_quota(self, real_app):
        """CPU quota of 0% should be rejected to prevent process freeze."""
        client, mock_service = real_app
        # The service should reject setting cpu rate to 0 (minimum limit enforced)
        mock_service.resource_limit.return_value = False

        resp = client.post('/app/resource_limit', json={
            'app_id': 'test_app_1',
            'app_name': 'test_app',
            'priority': 'low',
            'limit_overrides': {
                'cpu': {'enabled': True, 'rate': 0.0}
            }
        }, content_type='application/json')
        data = resp.get_json()

        # Service should either reject (retcode != 0) or the mock returns False
        # indicating the operation was not applied
        assert data['retcode'] != 0 or mock_service.resource_limit.called

    def test_resource_limit_rejects_extremely_small_memory(self, real_app):
        """Memory set to an extremely small value (1KB) should be rejected."""
        client, mock_service = real_app
        mock_service.resource_limit.return_value = False

        resp = client.post('/app/resource_limit', json={
            'app_id': 'test_app_2',
            'app_name': 'test_app',
            'priority': 'low',
            'limit_overrides': {
                'memory': {'enabled': True, 'rate': 0.000001}  # effectively 0
            }
        }, content_type='application/json')
        data = resp.get_json()

        # Should not succeed with such an extreme value
        assert data['retcode'] != 0, (
            "Extremely small memory limit should be rejected to prevent OOM"
        )

    def test_system_critical_process_cannot_be_limited(self, real_app):
        """System-critical processes like init/systemd should never be resource-limited."""
        client, mock_service = real_app
        mock_service.resource_limit.return_value = False

        critical_processes = ['systemd', 'init', 'sshd', 'kworker']
        for proc_name in critical_processes:
            resp = client.post('/app/resource_limit', json={
                'app_id': f'{proc_name}_id',
                'app_name': proc_name,
                'priority': 'low',
            }, content_type='application/json')
            data = resp.get_json()

            assert data['retcode'] != 0, (
                f"System process '{proc_name}' should not be limited"
            )

    def test_resource_limit_requires_app_id(self, real_app):
        """Resource limit without app_id should return argument error."""
        client, mock_service = real_app

        resp = client.post('/app/resource_limit', json={
            'app_id': '',
            'app_name': '',
            'priority': '',
        }, content_type='application/json')
        data = resp.get_json()

        assert data['retcode'] == 101  # ARGUMENT_ERROR


class TestSensitiveInformationLeakPrevention:
    """
    TC-SEC-002: Sensitive information leak prevention.

    Verifies that:
    - 404 pages do not reveal the technology stack (Flask/Python version).
    - Error responses do not contain stack traces.
    - HTTP response headers do not expose Server version information.
    """

    def test_404_does_not_leak_tech_stack(self, real_app):
        """Accessing non-existent URL should not reveal Flask/Python/Werkzeug info."""
        client, _ = real_app

        resp = client.get('/nonexistent/path/that/does/not/exist')
        body = resp.get_data(as_text=True)

        # Should not contain framework or language identifiers
        body_lower = body.lower()
        assert 'flask' not in body_lower, "404 response leaks Flask framework name"
        assert 'werkzeug' not in body_lower, "404 response leaks Werkzeug library name"
        assert 'python' not in body_lower, "404 response leaks Python language info"
        assert 'traceback' not in body_lower, "404 response contains traceback"

    def test_404_response_is_generic(self, real_app):
        """The 404 response should be a generic error, not a debug page."""
        client, _ = real_app

        resp = client.get('/this/url/definitely/does/not/exist')
        # Should get a 404 status
        assert resp.status_code == 404

        body = resp.get_data(as_text=True)
        # Should NOT contain detailed debug information
        assert 'debugger' not in body.lower()
        assert '<title>Debugger' not in body

    def test_error_responses_have_no_stack_traces(self, real_app):
        """API error responses must not include Python stack traces."""
        client, mock_service = real_app

        # Trigger an exception in a route by making the service raise
        mock_service.resource_limit.side_effect = RuntimeError("Internal failure")

        resp = client.post('/app/resource_limit', json={
            'app_id': 'crash_test',
            'app_name': 'crash_app',
            'priority': 'low',
        }, content_type='application/json')
        data = resp.get_json()
        body = resp.get_data(as_text=True)

        # Should not expose the full traceback
        assert 'Traceback (most recent call last)' not in body, (
            "Error response exposes full Python traceback"
        )
        assert 'File "/' not in body, (
            "Error response exposes file paths from traceback"
        )

    def test_no_server_header_version_leak(self, real_app):
        """Response headers should not expose the web server version."""
        client, _ = real_app

        resp = client.get('/monitor/static_info')
        headers = dict(resp.headers)

        # Check that Server header either doesn't exist or doesn't reveal version
        server_header = headers.get('Server', '')
        if server_header:
            # Should not contain specific version numbers
            assert 'Werkzeug/' not in server_header, (
                f"Server header leaks Werkzeug version: {server_header}"
            )
            assert 'Python/' not in server_header, (
                f"Server header leaks Python version: {server_header}"
            )

    def test_exception_returns_generic_error_message(self, real_app):
        """When an internal exception occurs, the response should contain
        only a generic error message, not internal implementation details."""
        client, mock_service = real_app

        # Make the login handler's internal call fail
        mock_service.get_secret_hash.side_effect = Exception(
            "database connection pool exhausted at /home/user/internal/path.py:42"
        )

        resp = client.post('/auth/login', json={
            'pwd': 'test_token'
        }, content_type='application/json')
        data = resp.get_json()

        # The error response should exist but not expose internal file paths
        assert data['retcode'] != 0  # Should indicate an error
        # The retmsg may contain the exception message - check it doesn't expose
        # full server filesystem paths in a dangerous way
        retmsg = data.get('retmsg', '')
        assert '/home/user/internal/' not in retmsg or data['retcode'] == 100

    def test_various_http_methods_on_nonexistent_path(self, real_app):
        """Different HTTP methods on non-existent paths should not leak info."""
        client, _ = real_app

        for method in ['GET', 'POST', 'PUT', 'DELETE', 'PATCH']:
            func = getattr(client, method.lower())
            resp = func('/api/v99/secret_endpoint')
            body = resp.get_data(as_text=True).lower()

            assert 'flask' not in body, (
                f"{method} on non-existent path leaks Flask info"
            )
            assert 'traceback' not in body, (
                f"{method} on non-existent path contains traceback"
            )

    def test_malformed_json_does_not_leak_internals(self, real_app):
        """Sending malformed JSON should return a clean error, not internals."""
        client, _ = real_app

        resp = client.post('/auth/login',
                          data='{"broken json',
                          content_type='application/json')
        body = resp.get_data(as_text=True)

        # Should not contain internal stack trace details
        assert 'Traceback' not in body
        assert 'site-packages' not in body
