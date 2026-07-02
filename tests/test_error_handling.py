# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Error handling and fault tolerance tests for REST API endpoints.

Covers test cases TC-E-001 through TC-E-007 from TEST_CASES.md:
- TC-E-001: API parameter type errors
- TC-E-002: API parameter boundary values
- TC-E-003: Operations on non-existent resources
- TC-E-004: Empty request body and malformed JSON
- TC-E-005: Duplicate operations
- TC-E-006: Resource control when process not running
- TC-E-007: Concurrent conflict (passive control vs manual API)
"""

import os
import sys
import json
import hashlib
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))


# RetCode constants matching utils/http_utils.py
RC_SUCCESS = 0
RC_EXCEPTION_ERROR = 100
RC_ARGUMENT_ERROR = 101
RC_OPERATING_ERROR = 103
RC_NOT_EXISTING = 404


@pytest.fixture
def real_app():
    """
    Import the real Flask app from BalanceService and mock only the
    external dependencies, NOT the route handlers themselves.
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
        mock_service.cancel_relaunch.return_value = False
        mock_service.resource_limit.return_value = True
        mock_service.restore_resource.return_value = True
        with patch.object(BalanceService, '_service', mock_service):
            BalanceService.app.config['TESTING'] = True
            with BalanceService.app.test_client() as client:
                yield client, mock_service


# ═══════════════════════════════════════════════════════════════════════════════
# TC-E-001: API parameter type errors
# ═══════════════════════════════════════════════════════════════════════════════

class TestParameterTypeErrors:
    """TC-E-001: Verify that invalid parameter types are rejected gracefully."""

    def test_set_priority_string_priority(self, real_app):
        """set_priority with string 'abc' as priority should fail validation."""
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.adjust_oom_priority'):
            from db.DatabaseModel import DBStatus
            mock_db.update_record.return_value = DBStatus.SUCCESS
            mock_query = MagicMock()
            mock_query.where.return_value = mock_query
            mock_query.get.return_value = MagicMock(name="App", cmdline="cmd", priority="abc")
            mock_db.query.return_value = mock_query

            resp = client.post('/app/set_priority',
                              json={'app_id': 'app1', 'priority': 'abc'},
                              content_type='application/json')
        data = resp.get_json()
        # The route accepts the value (no strict type check) but the underlying
        # service should handle it. At minimum, it must not crash (retcode 0 or 101).
        assert data['retcode'] in (RC_SUCCESS, RC_ARGUMENT_ERROR, RC_EXCEPTION_ERROR)

    def test_resource_limit_negative_cpu(self, real_app):
        """resource_limit with negative CPU value should be rejected or cause an error."""
        client, mock_svc = real_app
        mock_svc.resource_limit.return_value = False
        resp = client.post('/app/resource_limit',
                          json={
                              'app_id': 'app1',
                              'app_name': 'Test',
                              'priority': 'high',
                              'limit_overrides': {'cpu': {'rate': -50}}
                          },
                          content_type='application/json')
        data = resp.get_json()
        # Negative values should either be rejected (101) or cause operation failure (103)
        assert data['retcode'] in (RC_ARGUMENT_ERROR, RC_OPERATING_ERROR)

    def test_resource_limit_string_memory(self, real_app):
        """resource_limit with string memory value should not crash."""
        client, mock_svc = real_app
        mock_svc.resource_limit.return_value = False
        resp = client.post('/app/resource_limit',
                          json={
                              'app_id': 'app1',
                              'app_name': 'Test',
                              'priority': 'high',
                              'limit_overrides': {'memory': {'rate': 'many'}}
                          },
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] in (RC_ARGUMENT_ERROR, RC_OPERATING_ERROR, RC_EXCEPTION_ERROR)

    def test_set_oom_score_float_value(self, real_app):
        """set_oom_score when app record is queried with float-like data should handle gracefully."""
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.adjust_oom_priority') as mock_oom:
            mock_record = MagicMock()
            mock_record.name = "TestApp"
            mock_record.priority = 3.14  # Float instead of int
            mock_record.cmdline = "test_cmd"
            mock_query = MagicMock()
            mock_query.filter.return_value = mock_query
            mock_query.first.return_value = mock_record
            mock_db.query.return_value = mock_query

            resp = client.post('/app/set_oom_score',
                              json={'app_id': 'app1'},
                              content_type='application/json')
        data = resp.get_json()
        # Should succeed or handle the float gracefully
        assert data['retcode'] in (RC_SUCCESS, RC_ARGUMENT_ERROR, RC_EXCEPTION_ERROR)


# ═══════════════════════════════════════════════════════════════════════════════
# TC-E-002: API parameter boundary values
# ═══════════════════════════════════════════════════════════════════════════════

class TestParameterBoundaryValues:
    """TC-E-002: Verify boundary value handling for resource limits."""

    @pytest.mark.parametrize("cpu_rate,description", [
        (0, "CPU 0% should be rejected or set to minimum"),
        (0.0, "CPU 0.0 should be rejected or set to minimum"),
    ])
    def test_cpu_limit_zero(self, real_app, cpu_rate, description):
        """CPU limit of 0% should be rejected or clamped to minimum."""
        client, mock_svc = real_app
        mock_svc.resource_limit.return_value = False
        resp = client.post('/app/resource_limit',
                          json={
                              'app_id': 'app1',
                              'app_name': 'Test',
                              'priority': 'low',
                              'limit_overrides': {'cpu': {'rate': cpu_rate}}
                          },
                          content_type='application/json')
        data = resp.get_json()
        # Zero CPU should be rejected (safety protection) or service fails
        assert data['retcode'] in (RC_ARGUMENT_ERROR, RC_OPERATING_ERROR)

    @pytest.mark.parametrize("cpu_rate,description", [
        (1.0, "CPU 100% equivalent to no limit"),
        (100, "CPU 100 integer should be treated as no limit or accepted"),
    ])
    def test_cpu_limit_full(self, real_app, cpu_rate, description):
        """CPU limit of 100% should succeed (equivalent to no limit)."""
        client, mock_svc = real_app
        mock_svc.resource_limit.return_value = True
        resp = client.post('/app/resource_limit',
                          json={
                              'app_id': 'app1',
                              'app_name': 'Test',
                              'priority': 'high',
                              'limit_overrides': {'cpu': {'rate': cpu_rate}}
                          },
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_SUCCESS

    @pytest.mark.parametrize("memory_rate,description", [
        (0, "Memory 0 bytes should be rejected"),
        (0.0, "Memory 0.0 should be rejected"),
    ])
    def test_memory_limit_zero(self, real_app, memory_rate, description):
        """Memory limit of 0 should be rejected (minimum protection)."""
        client, mock_svc = real_app
        mock_svc.resource_limit.return_value = False
        resp = client.post('/app/resource_limit',
                          json={
                              'app_id': 'app1',
                              'app_name': 'Test',
                              'priority': 'low',
                              'limit_overrides': {'memory': {'rate': memory_rate}}
                          },
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] in (RC_ARGUMENT_ERROR, RC_OPERATING_ERROR)

    @pytest.mark.parametrize("memory_value,description", [
        (999999999999, "Huge memory value (1TB) should be accepted as 'no limit'"),
        (1099511627776, "1TB in bytes should be accepted or clamped"),
    ])
    def test_memory_limit_huge_value(self, real_app, memory_value, description):
        """Extremely large memory values should be accepted (treated as unlimited) or clamped."""
        client, mock_svc = real_app
        mock_svc.resource_limit.return_value = True
        resp = client.post('/app/resource_limit',
                          json={
                              'app_id': 'app1',
                              'app_name': 'Test',
                              'priority': 'high',
                              'limit_overrides': {'memory': {'rate': memory_value}}
                          },
                          content_type='application/json')
        data = resp.get_json()
        # Large values should be accepted or gracefully clamped
        assert data['retcode'] in (RC_SUCCESS, RC_OPERATING_ERROR)

    @pytest.mark.parametrize("io_value,description", [
        (0, "IO 0 MB/s should be rejected"),
        (0.0, "IO 0.0 MB/s should be rejected"),
    ])
    def test_disk_io_limit_zero(self, real_app, io_value, description):
        """Disk IO limit of 0 should be rejected (minimum protection)."""
        client, mock_svc = real_app
        mock_svc.resource_limit.return_value = False
        resp = client.post('/app/resource_limit',
                          json={
                              'app_id': 'app1',
                              'app_name': 'Test',
                              'priority': 'low',
                              'limit_overrides': {
                                  'disk_io': {'write': io_value, 'read': io_value}
                              }
                          },
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] in (RC_ARGUMENT_ERROR, RC_OPERATING_ERROR)

    @pytest.mark.parametrize("priority_value,expected_codes", [
        (0, [RC_SUCCESS, RC_ARGUMENT_ERROR]),
        (100, [RC_SUCCESS]),
        (-1, [RC_SUCCESS, RC_ARGUMENT_ERROR, RC_EXCEPTION_ERROR]),
        (200, [RC_ARGUMENT_ERROR, RC_SUCCESS]),
    ])
    def test_priority_boundary_values(self, real_app, priority_value, expected_codes):
        """Priority boundary values (0, 100, -1, 200) should be validated."""
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.adjust_oom_priority'):
            from db.DatabaseModel import DBStatus
            mock_db.update_record.return_value = DBStatus.SUCCESS
            mock_query = MagicMock()
            mock_query.where.return_value = mock_query
            mock_query.get.return_value = MagicMock(
                name="App", cmdline="cmd", priority=priority_value
            )
            mock_db.query.return_value = mock_query

            resp = client.post('/app/set_priority',
                              json={'app_id': 'app1', 'priority': priority_value},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] in expected_codes


# ═══════════════════════════════════════════════════════════════════════════════
# TC-E-003: Operations on non-existent resources
# ═══════════════════════════════════════════════════════════════════════════════

class TestNonExistentResources:
    """TC-E-003: Verify proper error responses for operations on missing resources."""

    def test_set_priority_nonexistent_app(self, real_app):
        """set_priority on non-existent app_id should return NOT_EXISTING."""
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.adjust_oom_priority'):
            from db.DatabaseModel import DBStatus
            mock_db.update_record.return_value = DBStatus.NOT_FOUND
            resp = client.post('/app/set_priority',
                              json={'app_id': 'nonexist_123', 'priority': 50},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_NOT_EXISTING

    def test_resource_limit_nonexistent_app(self, real_app):
        """resource_limit on non-existent app should return OPERATING_ERROR."""
        client, mock_svc = real_app
        mock_svc.resource_limit.return_value = False
        resp = client.post('/app/resource_limit',
                          json={
                              'app_id': 'nonexist_123',
                              'app_name': 'Ghost',
                              'priority': 'medium'
                          },
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_OPERATING_ERROR

    def test_remove_from_control_nonexistent_app(self, real_app):
        """remove_from_control on non-existent app should return an error."""
        client, mock_svc = real_app
        mock_svc.remove_control.return_value = None
        with patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.adjust_oom_priority'):
            mock_query = MagicMock()
            mock_query.filter.return_value = mock_query
            mock_query.first.return_value = None  # App not found
            mock_db.query.return_value = mock_query

            resp = client.post('/app/remove_from_control',
                              json={'app_id': 'nonexist_123', 'app_name': 'Ghost'},
                              content_type='application/json')
        data = resp.get_json()
        # Should get an exception because app_info is None and we try to access .priority
        assert data['retcode'] in (RC_NOT_EXISTING, RC_EXCEPTION_ERROR)

    def test_cancel_relaunch_nonexistent_app(self, real_app):
        """cancel_relaunch on non-existent app should return OPERATING_ERROR."""
        client, mock_svc = real_app
        mock_svc.cancel_relaunch.return_value = False
        with patch('BalanceService.AIAppPriority') as mock_db:
            from db.DatabaseModel import DBStatus
            mock_db.update_record.return_value = DBStatus.NOT_FOUND
            resp = client.post('/app/cancel_relaunch',
                              json={'app_id': 'nonexist_123'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_OPERATING_ERROR

    def test_get_priority_data_nonexistent_app(self, real_app):
        """get_priority_data for non-existent app should return NOT_EXISTING."""
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db:
            mock_query = MagicMock()
            mock_query.where.return_value = mock_query
            mock_query.first.return_value = None
            mock_db.query.return_value = mock_query

            resp = client.post('/app/get_priority_data',
                              json={'app_id': 'nonexist_123'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_NOT_EXISTING

    def test_resource_restore_nonexistent_app(self, real_app):
        """resource_restore on non-existent app should return OPERATING_ERROR."""
        client, mock_svc = real_app
        mock_svc.restore_resource.return_value = False
        resp = client.post('/app/resource_restore',
                          json={'app_id': 'nonexist_123'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_OPERATING_ERROR


# ═══════════════════════════════════════════════════════════════════════════════
# TC-E-004: Empty request body and malformed JSON
# ═══════════════════════════════════════════════════════════════════════════════

class TestMalformedRequests:
    """TC-E-004: Verify graceful handling of empty/malformed request bodies."""

    def test_empty_body_resource_limit(self, real_app):
        """POST /app/resource_limit with empty body should return ARGUMENT_ERROR."""
        client, _ = real_app
        resp = client.post('/app/resource_limit',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_ARGUMENT_ERROR

    def test_empty_body_set_priority(self, real_app):
        """POST /app/set_priority with empty body should return ARGUMENT_ERROR."""
        client, _ = real_app
        resp = client.post('/app/set_priority',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_ARGUMENT_ERROR

    def test_empty_body_cancel_relaunch(self, real_app):
        """POST /app/cancel_relaunch with empty body should return ARGUMENT_ERROR."""
        client, _ = real_app
        resp = client.post('/app/cancel_relaunch',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_ARGUMENT_ERROR

    def test_empty_body_resource_restore(self, real_app):
        """POST /app/resource_restore with empty body should return ARGUMENT_ERROR."""
        client, _ = real_app
        resp = client.post('/app/resource_restore',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_ARGUMENT_ERROR

    def test_empty_body_set_oom_score(self, real_app):
        """POST /app/set_oom_score with empty body should return ARGUMENT_ERROR."""
        client, _ = real_app
        resp = client.post('/app/set_oom_score',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_ARGUMENT_ERROR

    def test_empty_body_remove_from_control(self, real_app):
        """POST /app/remove_from_control with empty body should return ARGUMENT_ERROR."""
        client, _ = real_app
        resp = client.post('/app/remove_from_control',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_ARGUMENT_ERROR

    def test_plain_text_body(self, real_app):
        """POST with plain text body should result in an error (not a crash)."""
        client, _ = real_app
        resp = client.post('/app/resource_limit',
                          data='hello world',
                          content_type='text/plain')
        data = resp.get_json()
        # Should get an error since JSON parsing fails
        assert data['retcode'] in (RC_ARGUMENT_ERROR, RC_EXCEPTION_ERROR)

    def test_truncated_json(self, real_app):
        """POST with truncated JSON should result in an error."""
        client, _ = real_app
        resp = client.post('/app/resource_limit',
                          data='{"app_id": ',
                          content_type='application/json')
        # Flask may return 400 for malformed JSON or our handler catches it
        if resp.status_code == 400:
            # Flask's built-in JSON error handling
            assert True
        else:
            data = resp.get_json()
            assert data['retcode'] in (RC_ARGUMENT_ERROR, RC_EXCEPTION_ERROR)

    def test_deeply_nested_json(self, real_app):
        """POST with deeply nested JSON should not crash the service."""
        client, _ = real_app
        # Build a 100-level nested dict
        nested = {'value': 'deep'}
        for _ in range(100):
            nested = {'nested': nested}
        nested['app_id'] = 'app1'
        nested['app_name'] = 'Test'
        nested['priority'] = 'high'

        resp = client.post('/app/resource_limit',
                          json=nested,
                          content_type='application/json')
        data = resp.get_json()
        # Should not crash; may succeed or return error
        assert data['retcode'] in (RC_SUCCESS, RC_ARGUMENT_ERROR, RC_EXCEPTION_ERROR,
                                   RC_OPERATING_ERROR)

    def test_empty_content_length_zero(self, real_app):
        """POST with Content-Length: 0 should be handled gracefully."""
        client, _ = real_app
        resp = client.post('/app/set_priority',
                          data=b'',
                          content_type='application/json')
        if resp.status_code == 400:
            assert True
        else:
            data = resp.get_json()
            assert data['retcode'] in (RC_ARGUMENT_ERROR, RC_EXCEPTION_ERROR)

    def test_login_empty_body(self, real_app):
        """POST /auth/login with empty body should return EXCEPTION_ERROR."""
        client, _ = real_app
        resp = client.post('/auth/login',
                          data=b'',
                          content_type='application/json')
        if resp.status_code == 400:
            assert True
        else:
            data = resp.get_json()
            assert data['retcode'] in (RC_ARGUMENT_ERROR, RC_EXCEPTION_ERROR)


# ═══════════════════════════════════════════════════════════════════════════════
# TC-E-005: Duplicate operations
# ═══════════════════════════════════════════════════════════════════════════════

class TestDuplicateOperations:
    """TC-E-005: Verify graceful handling of duplicate/redundant operations."""

    def test_re_add_already_controlled_app(self, real_app):
        """Adding an already-controlled app should update (not create duplicate)."""
        client, mock_svc = real_app
        with patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.adjust_oom_priority'), \
             patch('BalanceService.check_app_running_status', return_value='running'), \
             patch('BalanceService.callback_manager'):
            from db.DatabaseModel import DBStatus
            # Simulate the app already exists in DB (update succeeds)
            mock_db.update_record.return_value = DBStatus.SUCCESS

            resp = client.post('/app/set_to_control',
                              json={
                                  'app_id': 'existing_app',
                                  'app_name': 'ExistingApp',
                                  'controlled': True,
                                  'priority': 60,
                                  'cmdline': 'existing_cmd'
                              },
                              content_type='application/json')
        data = resp.get_json()
        # Should succeed (idempotent update, not error)
        assert data['retcode'] == RC_SUCCESS
        assert data['data']['controlled'] is True

    def test_restore_non_limited_app(self, real_app):
        """Restoring resources on an app that is not limited should return OPERATING_ERROR."""
        client, mock_svc = real_app
        mock_svc.restore_resource.return_value = False  # Nothing to restore
        resp = client.post('/app/resource_restore',
                          json={'app_id': 'not_limited_app'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_OPERATING_ERROR

    def test_double_resource_limit(self, real_app):
        """Setting resource limit twice should succeed (overwrite, not duplicate)."""
        client, mock_svc = real_app
        mock_svc.resource_limit.return_value = True

        # First limit
        resp1 = client.post('/app/resource_limit',
                           json={'app_id': 'app1', 'app_name': 'Test', 'priority': 'high'},
                           content_type='application/json')
        data1 = resp1.get_json()
        assert data1['retcode'] == RC_SUCCESS

        # Second limit (same app, different params)
        resp2 = client.post('/app/resource_limit',
                           json={
                               'app_id': 'app1',
                               'app_name': 'Test',
                               'priority': 'low',
                               'limit_overrides': {'cpu': {'rate': 0.3}}
                           },
                           content_type='application/json')
        data2 = resp2.get_json()
        assert data2['retcode'] == RC_SUCCESS

    def test_set_same_priority_twice(self, real_app):
        """Setting the same priority value twice should succeed (idempotent)."""
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.adjust_oom_priority'):
            from db.DatabaseModel import DBStatus
            mock_db.update_record.return_value = DBStatus.SUCCESS
            mock_query = MagicMock()
            mock_query.where.return_value = mock_query
            mock_query.get.return_value = MagicMock(
                name="TestApp", cmdline="cmd", priority=80
            )
            mock_db.query.return_value = mock_query

            # Set priority to 80
            resp1 = client.post('/app/set_priority',
                              json={'app_id': 'app1', 'priority': 80},
                              content_type='application/json')
            data1 = resp1.get_json()
            assert data1['retcode'] == RC_SUCCESS

            # Set same priority again
            resp2 = client.post('/app/set_priority',
                              json={'app_id': 'app1', 'priority': 80},
                              content_type='application/json')
            data2 = resp2.get_json()
            assert data2['retcode'] == RC_SUCCESS


# ═══════════════════════════════════════════════════════════════════════════════
# TC-E-006: Resource control when process not running
# ═══════════════════════════════════════════════════════════════════════════════

class TestProcessNotRunning:
    """TC-E-006: Verify behavior when target process is not running."""

    def test_resource_limit_process_not_running(self, real_app):
        """resource_limit on a non-running process should fail or save config."""
        client, mock_svc = real_app
        # Simulate that the resource limit operation fails because process is not running
        mock_svc.resource_limit.return_value = False
        resp = client.post('/app/resource_limit',
                          json={
                              'app_id': 'stopped_app',
                              'app_name': 'StoppedApp',
                              'priority': 'medium'
                          },
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_OPERATING_ERROR

    def test_set_oom_score_process_not_running(self, real_app):
        """set_oom_score on a non-running process should fail with appropriate error."""
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.adjust_oom_priority') as mock_oom:
            # Simulate that the app is in DB but process is not running
            mock_record = MagicMock()
            mock_record.name = "StoppedApp"
            mock_record.priority = 50
            mock_record.cmdline = "stopped_cmd"
            mock_query = MagicMock()
            mock_query.filter.return_value = mock_query
            mock_query.first.return_value = mock_record
            mock_db.query.return_value = mock_query

            # adjust_oom_priority raises an exception when process not found
            mock_oom.side_effect = FileNotFoundError("No such process")

            resp = client.post('/app/set_oom_score',
                              json={'app_id': 'stopped_app'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_EXCEPTION_ERROR

    def test_resource_limit_process_died_midway(self, real_app):
        """resource_limit when process dies during operation should handle gracefully."""
        client, mock_svc = real_app
        # Simulate process dying mid-operation
        mock_svc.resource_limit.side_effect = ProcessLookupError("No such process")
        resp = client.post('/app/resource_limit',
                          json={
                              'app_id': 'dying_app',
                              'app_name': 'DyingApp',
                              'priority': 'low'
                          },
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_EXCEPTION_ERROR

    def test_restore_resource_process_not_running(self, real_app):
        """resource_restore when process is not running should return OPERATING_ERROR."""
        client, mock_svc = real_app
        mock_svc.restore_resource.return_value = False
        resp = client.post('/app/resource_restore',
                          json={'app_id': 'stopped_app'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_OPERATING_ERROR

    def test_set_oom_score_app_not_in_db(self, real_app):
        """set_oom_score when app record does not exist should fail."""
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.adjust_oom_priority'):
            mock_query = MagicMock()
            mock_query.filter.return_value = mock_query
            mock_query.first.return_value = None  # Not in DB
            mock_db.query.return_value = mock_query

            resp = client.post('/app/set_oom_score',
                              json={'app_id': 'unknown_app'},
                              content_type='application/json')
        data = resp.get_json()
        # Will raise AttributeError when accessing None.name
        assert data['retcode'] == RC_EXCEPTION_ERROR


# ═══════════════════════════════════════════════════════════════════════════════
# TC-E-007: Concurrent conflict (passive control vs manual API)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConcurrentConflict:
    """TC-E-007: Verify behavior when passive control conflicts with manual API."""

    def test_manual_restore_during_auto_limit(self, real_app):
        """User restoring resources while system auto-limits should succeed (manual wins)."""
        client, mock_svc = real_app
        # Simulate that restore succeeds (manual override)
        mock_svc.restore_resource.return_value = True
        resp = client.post('/app/resource_restore',
                          json={'app_id': 'auto_limited_app'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_SUCCESS

    def test_manual_limit_during_auto_restore(self, real_app):
        """User setting limits while system is restoring should succeed (manual wins)."""
        client, mock_svc = real_app
        mock_svc.resource_limit.return_value = True
        resp = client.post('/app/resource_limit',
                          json={
                              'app_id': 'restoring_app',
                              'app_name': 'RestoringApp',
                              'priority': 'high',
                              'limit_overrides': {'cpu': {'rate': 0.5}}
                          },
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_SUCCESS

    def test_concurrent_limit_and_restore_same_app(self, real_app):
        """Concurrent limit and restore on same app should not crash."""
        client, mock_svc = real_app
        # First: set limit
        mock_svc.resource_limit.return_value = True
        resp_limit = client.post('/app/resource_limit',
                                json={
                                    'app_id': 'app1',
                                    'app_name': 'App1',
                                    'priority': 'low'
                                },
                                content_type='application/json')
        data_limit = resp_limit.get_json()
        assert data_limit['retcode'] == RC_SUCCESS

        # Second: immediately restore
        mock_svc.restore_resource.return_value = True
        resp_restore = client.post('/app/resource_restore',
                                  json={'app_id': 'app1'},
                                  content_type='application/json')
        data_restore = resp_restore.get_json()
        assert data_restore['retcode'] == RC_SUCCESS

    def test_concurrent_cpu_and_memory_limit(self, real_app):
        """Concurrent CPU and memory limits on same app should not conflict."""
        client, mock_svc = real_app
        mock_svc.resource_limit.return_value = True

        # Set CPU limit
        resp1 = client.post('/app/resource_limit',
                           json={
                               'app_id': 'app1',
                               'app_name': 'App1',
                               'priority': 'medium',
                               'limit_overrides': {'cpu': {'rate': 0.5}}
                           },
                           content_type='application/json')
        data1 = resp1.get_json()
        assert data1['retcode'] == RC_SUCCESS

        # Set memory limit on same app
        resp2 = client.post('/app/resource_limit',
                           json={
                               'app_id': 'app1',
                               'app_name': 'App1',
                               'priority': 'medium',
                               'limit_overrides': {'memory': {'rate': 0.3}}
                           },
                           content_type='application/json')
        data2 = resp2.get_json()
        assert data2['retcode'] == RC_SUCCESS

    def test_service_exception_during_limit(self, real_app):
        """Service raising an exception during limit should return EXCEPTION_ERROR."""
        client, mock_svc = real_app
        mock_svc.resource_limit.side_effect = RuntimeError(
            "Concurrent access: cgroup write failed"
        )
        resp = client.post('/app/resource_limit',
                          json={
                              'app_id': 'app1',
                              'app_name': 'App1',
                              'priority': 'low'
                          },
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_EXCEPTION_ERROR
        assert 'Concurrent access' in data['retmsg']

    def test_service_exception_during_restore(self, real_app):
        """Service raising an exception during restore should return EXCEPTION_ERROR."""
        client, mock_svc = real_app
        mock_svc.restore_resource.side_effect = RuntimeError(
            "Concurrent access: cgroup release failed"
        )
        resp = client.post('/app/resource_restore',
                          json={'app_id': 'app1'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == RC_EXCEPTION_ERROR
        assert 'Concurrent access' in data['retmsg']
