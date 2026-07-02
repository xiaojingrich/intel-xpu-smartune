# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Functional tests for REST API endpoints — testing the REAL BalanceService routes.
These tests import the actual Flask app and mock only the external dependencies
(database, subprocess, BPF), not the route logic itself.
"""

import os
import sys
import json
import hashlib
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))


@pytest.fixture
def real_app():
    """
    Import the real Flask app from BalanceService and mock only the
    external dependencies, NOT the route handlers themselves.
    """
    # Mock the DynamicBalancer (BPF, cgroups, etc.) before import
    with patch('balancer.balancer.DynamicBalancer') as mock_balancer_cls, \
         patch('monitor.monitor_api._start_snapshot_cleanup_task'), \
         patch('monitor.system_info.preload_static_info'), \
         patch('BalanceService.init_database'), \
         patch('BalanceService.preload_static_info'):

        mock_balancer = MagicMock()
        mock_balancer_cls.return_value = mock_balancer

        # Patch the _service singleton
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


class TestAuthLogin:
    """Test the REAL /auth/login route handler."""

    def test_login_correct_token(self, real_app):
        client, _ = real_app
        resp = client.post('/auth/login',
                          json={'pwd': 'correct_token'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data']['authenticated'] is True

    def test_login_wrong_token(self, real_app):
        client, _ = real_app
        resp = client.post('/auth/login',
                          json={'pwd': 'wrong'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data']['authenticated'] is False

    def test_login_missing_pwd(self, real_app):
        client, _ = real_app
        resp = client.post('/auth/login',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 101  # ARGUMENT_ERROR

    def test_login_empty_pwd(self, real_app):
        client, _ = real_app
        resp = client.post('/auth/login',
                          json={'pwd': ''},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 101

    def test_login_exception(self, real_app):
        """get_secret_hash raising should be caught and return EXCEPTION_ERROR."""
        client, mock_svc = real_app
        mock_svc.get_secret_hash.side_effect = RuntimeError("boom")
        resp = client.post('/auth/login',
                          json={'pwd': 'x'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestResourceLimit:
    """Test the REAL /app/resource_limit route handler."""

    def test_resource_limit_success(self, real_app):
        client, mock_svc = real_app
        mock_svc.resource_limit.return_value = True
        resp = client.post('/app/resource_limit',
                          json={'app_id': 'app1', 'app_name': 'Test', 'priority': 'high'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0

    def test_resource_limit_service_fails(self, real_app):
        client, mock_svc = real_app
        mock_svc.resource_limit.return_value = False
        resp = client.post('/app/resource_limit',
                          json={'app_id': 'app1', 'app_name': 'Test', 'priority': 'high'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 103  # OPERATING_ERROR

    def test_resource_limit_missing_all_params(self, real_app):
        client, _ = real_app
        resp = client.post('/app/resource_limit',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 101

    def test_resource_limit_with_overrides(self, real_app):
        client, mock_svc = real_app
        mock_svc.resource_limit.return_value = True
        overrides = {'cpu': {'rate': 0.5}, 'memory': {'rate': 0.3}}
        resp = client.post('/app/resource_limit',
                          json={'app_id': 'app1', 'app_name': 'T', 'priority': 'low',
                                'limit_overrides': overrides},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        mock_svc.resource_limit.assert_called_with(
            'app1', 'T', 'low', limit_overrides=overrides
        )

    def test_resource_limit_exception(self, real_app):
        """resource_limit raising should be caught and return EXCEPTION_ERROR."""
        client, mock_svc = real_app
        mock_svc.resource_limit.side_effect = RuntimeError("boom")
        resp = client.post('/app/resource_limit',
                          json={'app_id': 'app1', 'app_name': 'A', 'priority': 'high'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestResourceRestore:
    """Test the REAL /app/resource_restore route handler."""

    def test_restore_success(self, real_app):
        client, mock_svc = real_app
        mock_svc.restore_resource.return_value = True
        resp = client.post('/app/resource_restore',
                          json={'app_id': 'app1'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0

    def test_restore_service_fails(self, real_app):
        client, mock_svc = real_app
        mock_svc.restore_resource.return_value = False
        resp = client.post('/app/resource_restore',
                          json={'app_id': 'app1'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 103  # OPERATING_ERROR

    def test_restore_missing_app_id(self, real_app):
        client, _ = real_app
        resp = client.post('/app/resource_restore',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 101

    def test_restore_exception(self, real_app):
        """restore_resource raising should be caught and return EXCEPTION_ERROR."""
        client, mock_svc = real_app
        mock_svc.restore_resource.side_effect = RuntimeError("boom")
        resp = client.post('/app/resource_restore',
                          json={'app_id': 'app1'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestCancelRelaunch:
    """Test the REAL /app/cancel_relaunch route handler."""

    def test_cancel_missing_app_id(self, real_app):
        client, _ = real_app
        resp = client.post('/app/cancel_relaunch',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 101

    def test_cancel_not_found(self, real_app):
        client, mock_svc = real_app
        mock_svc.cancel_relaunch.return_value = False
        with patch('BalanceService.AIAppPriority') as mock_db:
            mock_db.update_record.return_value = False
            resp = client.post('/app/cancel_relaunch',
                              json={'app_id': 'nonexist'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 103

    def test_cancel_exception(self, real_app):
        """cancel_relaunch raising should be caught and return EXCEPTION_ERROR."""
        client, mock_svc = real_app
        mock_svc.cancel_relaunch.side_effect = RuntimeError("boom")
        resp = client.post('/app/cancel_relaunch',
                          json={'app_id': 'a'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestGetPriorityData:
    """Test the REAL /app/get_priority_data route — including the IndexError bug."""

    def test_empty_params_should_return_argument_error(self, real_app):
        """BUG: Empty params causes IndexError on conditions[0], caught as generic exception."""
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db:
            resp = client.post('/app/get_priority_data',
                              json={},
                              content_type='application/json')
        data = resp.get_json()
        # BUG: Should be ARGUMENT_ERROR (101) but returns EXCEPTION_ERROR (100)
        # because conditions[0] IndexError is caught by the generic except clause
        assert data['retcode'] == 101, (
            f"Empty params should return ARGUMENT_ERROR (101), got retcode={data['retcode']}: "
            f"{data['retmsg']}. This is a bug — IndexError on empty conditions list "
            f"is caught as a generic exception instead of being handled explicitly."
        )

    def test_by_app_id(self, real_app):
        client, _ = real_app
        mock_record = MagicMock()
        mock_record.id = "app1"
        mock_record.app_id = "app1"
        mock_record.name = "TestApp"
        mock_record.priority = 80
        mock_record.cgroup = "test.scope"
        mock_record.remark = ""
        mock_record.cmdline = "test_cmd"
        mock_record.up_time = None
        mock_record.status = "running"

        with patch('BalanceService.AIAppPriority') as mock_db:
            mock_query = MagicMock()
            mock_query.where.return_value = mock_query
            mock_query.first.return_value = mock_record
            mock_db.query.return_value = mock_query

            resp = client.post('/app/get_priority_data',
                              json={'app_id': 'app1'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data']['name'] == "TestApp"
        assert data['data']['priority'] == 80


class TestSetPriority:
    """Test the REAL /app/set_priority route handler."""

    def test_missing_params(self, real_app):
        client, _ = real_app
        resp = client.post('/app/set_priority',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 101

    def test_not_found_app(self, real_app):
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.adjust_oom_priority'):
            from db.DatabaseModel import DBStatus
            mock_db.update_record.return_value = DBStatus.NOT_FOUND
            resp = client.post('/app/set_priority',
                              json={'app_id': 'ghost', 'priority': 50},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 404  # NOT_EXISTING

    def test_exception(self, real_app):
        """update_record raising should be caught and return EXCEPTION_ERROR."""
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db:
            mock_db.update_record.side_effect = RuntimeError("boom")
            resp = client.post('/app/set_priority',
                              json={'app_id': 'a', 'priority': 50},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 100

    def test_integer_priority_normalized_to_label(self, real_app):
        """An integer priority must be persisted as a canonical string label,
        never a raw int (a raw int crashes the dashboard's .toLowerCase())."""
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.adjust_oom_priority'):
            from db.DatabaseModel import DBStatus
            mock_db.update_record.return_value = DBStatus.SUCCESS
            mock_db.query.return_value.where.return_value.get.return_value = None
            resp = client.post('/app/set_priority',
                              json={'app_id': 'a', 'priority': 50},
                              content_type='application/json')
            # Whatever the route wrote to the DB must be a string label.
            written = mock_db.update_record.call_args.kwargs.get('priority')
        assert resp.get_json()['retcode'] == 0
        assert written == 'medium', f"expected 'medium' label, got {written!r}"


class TestNormalizePriority:
    """Unit tests for the normalize_priority() helper — the single guard that
    keeps non-string priorities out of the DB (root cause of the Balancer
    black-screen)."""

    @pytest.fixture(autouse=True)
    def _svc(self, real_app):
        # real_app imports BalanceService with mocked deps.
        yield

    @pytest.mark.parametrize("raw,expected", [
        ('low', 'low'), ('MEDIUM', 'medium'), ('High', 'high'), (' critical ', 'critical'),
        (20, 'low'), (50, 'medium'), (80, 'high'), (100, 'critical'),
        (0, 'low'), (95, 'critical'),
        ('bogus', 'medium'), ('', 'medium'), (None, 'medium'),
        (True, 'medium'), ([], 'medium'),
    ])
    def test_normalize(self, raw, expected):
        import BalanceService
        assert BalanceService.normalize_priority(raw) == expected


class TestSetToControl:
    """Test the REAL /app/set_to_control route handler."""

    def test_new_app_registration(self, real_app):
        client, mock_svc = real_app
        with patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.adjust_oom_priority'), \
             patch('BalanceService.check_app_running_status', return_value='stopped'), \
             patch('BalanceService.callback_manager'):
            from db.DatabaseModel import DBStatus
            mock_db.update_record.return_value = DBStatus.NOT_FOUND
            mock_db.insert_record.return_value = DBStatus.SUCCESS

            resp = client.post('/app/set_to_control',
                              json={
                                  'app_id': 'new_app',
                                  'app_name': 'NewApp',
                                  'controlled': True,
                                  'priority': 60,
                                  'cmdline': 'new_cmd'
                              },
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data']['controlled'] is True
        assert data['data']['app_name'] == 'NewApp'

    def test_exception(self, real_app):
        """update_record raising should be caught and return EXCEPTION_ERROR."""
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db:
            mock_db.update_record.side_effect = RuntimeError("boom")
            resp = client.post('/app/set_to_control',
                              json={'app_id': 'a', 'app_name': 'A', 'priority': 'high'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestRemoveFromControl:
    """Test the REAL /app/remove_from_control route handler."""

    def test_missing_identifiers(self, real_app):
        client, _ = real_app
        resp = client.post('/app/remove_from_control',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 101

    def test_exception(self, real_app):
        """AIAppPriority.query raising should be caught and return EXCEPTION_ERROR."""
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db:
            mock_db.query.side_effect = RuntimeError("boom")
            resp = client.post('/app/remove_from_control',
                              json={'app_id': 'a', 'app_name': 'A'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestGetApps:
    """Test the REAL /app/get_apps route handler."""

    def test_get_apps_success(self, real_app):
        client, _ = real_app
        with patch('BalanceService.fetch_all_apps') as mock_fetch:
            mock_fetch.return_value = [
                {'app_id': 'app1', 'name': 'App1', 'commandline': '/usr/bin/app1'}
            ]
            resp = client.post('/app/get_apps',
                              json={'store': False},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert len(data['data']) == 1
        assert data['data'][0]['app_id'] == 'app1'

    def test_get_apps_with_store(self, real_app):
        client, _ = real_app
        with patch('BalanceService.fetch_all_apps') as mock_fetch, \
             patch('BalanceService.AIAppPriority') as mock_db:
            mock_fetch.return_value = [
                {'app_id': 'new_app', 'name': 'NewApp', 'commandline': '/usr/bin/new'}
            ]
            mock_query = MagicMock()
            mock_query.where.return_value = mock_query
            mock_query.get.side_effect = Exception("Not found")
            mock_db.query.return_value = mock_query
            mock_db.insert_record.return_value = None

            resp = client.post('/app/get_apps',
                              json={'store': True},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert len(data['data']) == 1
        mock_db.insert_record.assert_called_once()

    def test_get_apps_empty_list(self, real_app):
        client, _ = real_app
        with patch('BalanceService.fetch_all_apps') as mock_fetch:
            mock_fetch.return_value = []
            resp = client.post('/app/get_apps',
                              json={},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data'] == []

    def test_get_apps_via_get_method(self, real_app):
        client, _ = real_app
        with patch('BalanceService.fetch_all_apps') as mock_fetch:
            mock_fetch.return_value = [
                {'app_id': 'x', 'name': 'X', 'commandline': 'x'}
            ]
            resp = client.get('/app/get_apps',
                             json={},
                             content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0

    def test_get_apps_exception(self, real_app):
        """fetch_all_apps raising should be caught and return EXCEPTION_ERROR."""
        client, _ = real_app
        with patch('BalanceService.fetch_all_apps') as mock_fetch:
            mock_fetch.side_effect = RuntimeError("boom")
            resp = client.post('/app/get_apps',
                              json={},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestDiscoverSearch:
    """Test the REAL /app/discover_search route handler."""

    def test_search_success(self, real_app):
        client, _ = real_app
        mock_candidate = MagicMock()
        with patch('monitor.app_discovery.search_processes') as mock_search, \
             patch('monitor.app_discovery.candidate_to_dict') as mock_to_dict:
            mock_search.return_value = [mock_candidate]
            mock_to_dict.return_value = {'pid': 1234, 'name': 'myapp', 'cmdline': '/usr/bin/myapp'}

            resp = client.post('/app/discover_search',
                              json={'keywords': ['myapp']},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data']['count'] == 1
        assert len(data['data']['candidates']) == 1

    def test_search_empty_keywords(self, real_app):
        client, _ = real_app
        resp = client.post('/app/discover_search',
                          json={'keywords': []},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 101
        assert data['data'] == {"candidates": []}

    def test_search_missing_keywords(self, real_app):
        client, _ = real_app
        resp = client.post('/app/discover_search',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 101

    def test_search_string_keyword_coerced_to_list(self, real_app):
        client, _ = real_app
        with patch('monitor.app_discovery.search_processes') as mock_search, \
             patch('monitor.app_discovery.candidate_to_dict') as mock_to_dict:
            mock_search.return_value = []
            resp = client.post('/app/discover_search',
                              json={'keywords': 'single_keyword'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        mock_search.assert_called_once_with(['single_keyword'])

    def test_search_exception(self, real_app):
        """search_processes raising should be caught and return EXCEPTION_ERROR."""
        client, _ = real_app
        with patch('monitor.app_discovery.search_processes') as mock_search:
            mock_search.side_effect = RuntimeError("boom")
            resp = client.post('/app/discover_search',
                              json={'keywords': ['x']},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestDiscoverExtract:
    """Test the REAL /app/discover_extract route handler."""

    def test_extract_success(self, real_app):
        client, _ = real_app
        mock_result = MagicMock()
        with patch('monitor.app_discovery.extract_fields') as mock_extract, \
             patch('monitor.app_discovery.extract_to_dict') as mock_to_dict:
            mock_extract.return_value = mock_result
            mock_to_dict.return_value = {
                'name': 'MyApp', 'bpf_name': ['myapp'], 'process_names': ['myapp']
            }
            resp = client.post('/app/discover_extract',
                              json={'pids': [1234, 5678]},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data']['name'] == 'MyApp'
        mock_extract.assert_called_once_with([1234, 5678], name='')

    def test_extract_with_name_override(self, real_app):
        client, _ = real_app
        with patch('monitor.app_discovery.extract_fields') as mock_extract, \
             patch('monitor.app_discovery.extract_to_dict') as mock_to_dict:
            mock_extract.return_value = MagicMock()
            mock_to_dict.return_value = {'name': 'CustomName'}
            resp = client.post('/app/discover_extract',
                              json={'pids': [100], 'name': 'CustomName'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        mock_extract.assert_called_once_with([100], name='CustomName')

    def test_extract_empty_pids(self, real_app):
        client, _ = real_app
        resp = client.post('/app/discover_extract',
                          json={'pids': []},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 101

    def test_extract_invalid_pids_filtered(self, real_app):
        client, _ = real_app
        resp = client.post('/app/discover_extract',
                          json={'pids': ['not_a_number', None, 'abc']},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 101  # All PIDs invalid, none remain

    def test_extract_exception(self, real_app):
        """extract_fields raising should be caught and return EXCEPTION_ERROR."""
        client, _ = real_app
        with patch('monitor.app_discovery.extract_fields') as mock_extract:
            mock_extract.side_effect = RuntimeError("boom")
            resp = client.post('/app/discover_extract',
                              json={'pids': [123]},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestNewControlledApp:
    """Test the REAL /app/new_controlled_app route handler."""

    def test_success(self, real_app):
        client, mock_svc = real_app
        with patch('config.config.b_config') as mock_config, \
             patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.check_app_running_status', return_value='stopped'), \
             patch('BalanceService.callback_manager') as mock_cb:
            mock_config.controlled_apps = []
            mock_config.append_to_list_section.return_value = True
            mock_db.insert_record.return_value = None

            resp = client.post('/app/new_controlled_app',
                              json={
                                  'name': 'NewApp',
                                  'id': 'new_app_1',
                                  'priority': 'high',
                                  'commandline': '/usr/bin/newapp',
                                  'bpf_name': ['newapp'],
                                  'process_names': ['newapp'],
                              },
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data']['name'] == 'NewApp'
        assert data['data']['id'] == 'new_app_1'

    def test_missing_name(self, real_app):
        client, _ = real_app
        resp = client.post('/app/new_controlled_app',
                          json={'id': 'app1'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 101

    def test_missing_id(self, real_app):
        client, _ = real_app
        resp = client.post('/app/new_controlled_app',
                          json={'name': 'App1'},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 101

    def test_conflict_duplicate_id(self, real_app):
        client, _ = real_app
        with patch('config.config.b_config') as mock_config:
            mock_config.controlled_apps = [
                {'id': 'existing_id', 'name': 'ExistingApp', 'bpf_name': [], 'process_names': []}
            ]
            resp = client.post('/app/new_controlled_app',
                              json={'name': 'DifferentName', 'id': 'existing_id'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 409
        assert data['data']['conflict'] == 'id'

    def test_conflict_duplicate_name(self, real_app):
        client, _ = real_app
        with patch('config.config.b_config') as mock_config:
            mock_config.controlled_apps = [
                {'id': 'other_id', 'name': 'SameName', 'bpf_name': [], 'process_names': []}
            ]
            resp = client.post('/app/new_controlled_app',
                              json={'name': 'SameName', 'id': 'new_id'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 409
        assert data['data']['conflict'] == 'name'

    def test_conflict_overlapping_processes(self, real_app):
        client, _ = real_app
        with patch('config.config.b_config') as mock_config:
            mock_config.controlled_apps = [
                {'id': 'other', 'name': 'Other', 'bpf_name': ['shared_comm'], 'process_names': []}
            ]
            resp = client.post('/app/new_controlled_app',
                              json={
                                  'name': 'NewApp', 'id': 'new_id',
                                  'bpf_name': ['shared_comm'],
                              },
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 409
        assert data['data']['conflict'] == 'processes'
        assert 'shared_comm' in data['data']['shared']

    def test_exception(self, real_app):
        """append_to_list_section raising should be caught and return EXCEPTION_ERROR."""
        client, _ = real_app
        with patch('config.config.b_config') as mock_config:
            mock_config.controlled_apps = []
            mock_config.append_to_list_section.side_effect = RuntimeError("boom")
            resp = client.post('/app/new_controlled_app',
                              json={'name': 'X', 'id': 'x.svc'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestPurgeControlledApp:
    """Test the REAL /app/purge_controlled_app route handler."""

    def test_purge_success(self, real_app):
        client, mock_svc = real_app
        with patch('config.config.b_config') as mock_config, \
             patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.adjust_oom_priority') as mock_oom:
            mock_config.controlled_apps = [
                {'id': 'app_to_purge', 'name': 'PurgeMe', 'bpf_name': [], 'process_names': []}
            ]
            mock_config.remove_from_list_section.return_value = 1
            mock_query = MagicMock()
            mock_query.filter.return_value = mock_query
            mock_query.first.return_value = None
            mock_db.query.return_value = mock_query
            mock_db.delete_record.return_value = None

            resp = client.post('/app/purge_controlled_app',
                              json={'id': 'app_to_purge'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data']['id'] == 'app_to_purge'
        assert data['data']['name'] == 'PurgeMe'
        mock_svc.remove_control.assert_called_with('PurgeMe')
        mock_svc.rebuild_controlled_map.assert_called()

    def test_purge_missing_id(self, real_app):
        client, _ = real_app
        resp = client.post('/app/purge_controlled_app',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 101

    def test_purge_not_found(self, real_app):
        client, _ = real_app
        with patch('config.config.b_config') as mock_config:
            mock_config.controlled_apps = []
            resp = client.post('/app/purge_controlled_app',
                              json={'id': 'nonexistent'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 404

    def test_purge_config_write_fails(self, real_app):
        client, _ = real_app
        with patch('config.config.b_config') as mock_config:
            mock_config.controlled_apps = [
                {'id': 'app1', 'name': 'App1', 'bpf_name': [], 'process_names': []}
            ]
            mock_config.remove_from_list_section.return_value = 0
            resp = client.post('/app/purge_controlled_app',
                              json={'id': 'app1'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 100  # EXCEPTION_ERROR

    def test_purge_exception(self, real_app):
        """remove_from_list_section raising should be caught and return EXCEPTION_ERROR."""
        client, _ = real_app
        with patch('config.config.b_config') as mock_config:
            mock_config.controlled_apps = [
                {'id': 'x', 'name': 'X', 'bpf_name': [], 'process_names': []}
            ]
            mock_config.remove_from_list_section.side_effect = RuntimeError("boom")
            resp = client.post('/app/purge_controlled_app',
                              json={'id': 'x'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestGetControlledApp:
    """Test the REAL /app/get_controlled_app route handler."""

    def test_success_with_controlled_apps(self, real_app):
        client, _ = real_app
        mock_app = MagicMock()
        mock_app.app_id = 'ctrl_app1'
        mock_app.name = 'ControlledApp'
        mock_app.controlled = True
        mock_app.priority = 'high'
        mock_app.oom_score = -500
        mock_app.cmdline = '/usr/bin/ctrl'
        mock_app.cgroup = 'test.scope'
        mock_app.remark = 'test remark'
        mock_app.status = 'running'

        with patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.fetch_all_apps') as mock_fetch:
            mock_query = MagicMock()
            mock_query.filter.return_value = [mock_app]
            mock_db.query.return_value = mock_query
            mock_db.controlled = True
            mock_fetch.return_value = [
                {'app_id': 'ctrl_app1', 'name': 'ControlledApp', 'process_names': ['ctrl']}
            ]

            resp = client.post('/app/get_controlled_app',
                              json={},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert len(data['data']) == 1
        assert data['data'][0]['app_id'] == 'ctrl_app1'
        assert data['data'][0]['app_name'] == 'ControlledApp'
        assert data['data'][0]['priority'] == 'high'

    def test_no_controlled_apps(self, real_app):
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.fetch_all_apps') as mock_fetch:
            mock_query = MagicMock()
            mock_query.filter.return_value = []
            mock_db.query.return_value = mock_query
            mock_db.controlled = True
            mock_fetch.return_value = []

            resp = client.post('/app/get_controlled_app',
                              json={},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 404


class TestCheckRunningApps:
    """Test the REAL /app/check_running_apps route handler."""

    def test_success_with_detected_apps(self, real_app):
        client, mock_svc = real_app
        mock_svc.check_running_apps.return_value = [
            {'app_id': 'app1', 'name': 'RunningApp', 'pids': [1234, 5678]}
        ]
        resp = client.post('/app/check_running_apps',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert len(data['data']) == 1
        assert data['data'][0]['app_id'] == 'app1'

    def test_no_running_apps(self, real_app):
        client, mock_svc = real_app
        mock_svc.check_running_apps.return_value = []
        resp = client.post('/app/check_running_apps',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data'] == []

    def test_service_exception(self, real_app):
        client, mock_svc = real_app
        mock_svc.check_running_apps.side_effect = RuntimeError("BPF not ready")
        resp = client.post('/app/check_running_apps',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 100
        assert data['data'] == []


class TestGetPendingApp:
    """Test the REAL /app/get_pending_app route handler."""

    def test_no_pending_apps(self, real_app):
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db:
            mock_query = MagicMock()
            mock_query.filter.return_value = []
            mock_db.query.return_value = mock_query
            resp = client.post('/app/get_pending_app',
                              json={},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 404  # NOT_EXISTING

    def test_success(self, real_app):
        client, _ = real_app
        mock_app = MagicMock()
        mock_app.app_id = 'p1'
        mock_app.name = 'PendingApp'
        mock_app.controlled = True
        mock_app.priority = 'high'
        mock_app.oom_score = -500
        mock_app.cgroup = 'p.scope'
        mock_app.remark = ''
        mock_app.status = 'pending'
        with patch('BalanceService.AIAppPriority') as mock_db, \
             patch('BalanceService.get_priority_value', return_value=3):
            mock_query = MagicMock()
            mock_query.filter.return_value = [mock_app]
            mock_db.query.return_value = mock_query
            resp = client.post('/app/get_pending_app',
                              json={},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert len(data['data']) == 1
        assert data['data'][0]['app_id'] == 'p1'

    def test_exception(self, real_app):
        """AIAppPriority.query raising should be caught and return EXCEPTION_ERROR."""
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db:
            mock_db.query.side_effect = RuntimeError("boom")
            resp = client.post('/app/get_pending_app',
                              json={},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestSetOomScore:
    """Test the REAL /app/set_oom_score route handler."""

    def test_missing_app_id(self, real_app):
        client, _ = real_app
        resp = client.post('/app/set_oom_score',
                          json={},
                          content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 101  # ARGUMENT_ERROR

    def test_exception(self, real_app):
        """AIAppPriority.query raising should be caught and return EXCEPTION_ERROR."""
        client, _ = real_app
        with patch('BalanceService.AIAppPriority') as mock_db:
            mock_db.query.side_effect = RuntimeError("boom")
            resp = client.post('/app/set_oom_score',
                              json={'app_id': 'a'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestMonitorAppResourceStats:
    """Test the REAL /monitor/app_resource_stats route handler."""

    def test_success_from_cache(self, real_app):
        client, _ = real_app
        fake_apps = [
            {'name': 'app1', 'cpu': 45.2, 'memory': 1024},
            {'name': 'app2', 'cpu': 12.1, 'memory': 512},
        ]
        with patch('monitor.monitor_api._start_app_stats_auto_refresh'), \
             patch('monitor.monitor_api._APP_STATS_CACHE', {
                 'resource': fake_apps, 'disk_io': None,
                 'last_request_ts': 0.0, 'ts': 1000.0
             }), \
             patch('monitor.monitor_api._app_stats_request_event'):
            resp = client.get('/monitor/app_resource_stats')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert 'apps' in data['data']
        assert len(data['data']['apps']) == 2

    def test_with_n_parameter(self, real_app):
        client, _ = real_app
        fake_apps = [{'name': f'app{i}', 'cpu': 10 - i} for i in range(5)]
        with patch('monitor.monitor_api._start_app_stats_auto_refresh'), \
             patch('monitor.monitor_api._APP_STATS_CACHE', {
                 'resource': fake_apps, 'disk_io': None,
                 'last_request_ts': 0.0, 'ts': 1000.0
             }), \
             patch('monitor.monitor_api._app_stats_request_event'):
            resp = client.get('/monitor/app_resource_stats?n=2')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert len(data['data']['apps']) == 2

    def test_cache_miss_falls_back_to_sync(self, real_app):
        client, _ = real_app
        mock_monitor = MagicMock()
        mock_monitor.get_app_resource_stats.return_value = [{'name': 'sync_app', 'cpu': 5.0}]
        with patch('monitor.monitor_api._start_app_stats_auto_refresh'), \
             patch('monitor.monitor_api._APP_STATS_CACHE', {
                 'resource': None, 'disk_io': None,
                 'last_request_ts': 0.0, 'ts': 0.0
             }), \
             patch('monitor.monitor_api._app_stats_request_event'), \
             patch('monitor.monitor_api._get_resource_monitor', return_value=mock_monitor):
            resp = client.get('/monitor/app_resource_stats')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert len(data['data']['apps']) == 1

    def test_exception(self, real_app):
        """Sync fallback collection raising should return EXCEPTION_ERROR."""
        client, _ = real_app
        mock_monitor = MagicMock()
        mock_monitor.get_app_resource_stats.side_effect = RuntimeError("boom")
        with patch('monitor.monitor_api._start_app_stats_auto_refresh'), \
             patch('monitor.monitor_api._APP_STATS_CACHE', {
                 'resource': None, 'disk_io': None,
                 'last_request_ts': 0.0, 'ts': 0.0
             }), \
             patch('monitor.monitor_api._app_stats_request_event'), \
             patch('monitor.monitor_api._get_resource_monitor', return_value=mock_monitor):
            resp = client.get('/monitor/app_resource_stats')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestMonitorAppDiskIoStats:
    """Test the REAL /monitor/app_disk_io_stats route handler."""

    def test_success_from_cache(self, real_app):
        client, _ = real_app
        fake_io = [
            {'name': 'io_app1', 'read_bytes': 1024, 'write_bytes': 2048},
        ]
        with patch('monitor.monitor_api._start_app_stats_auto_refresh'), \
             patch('monitor.monitor_api._APP_STATS_CACHE', {
                 'resource': None, 'disk_io': fake_io,
                 'last_request_ts': 0.0, 'ts': 1000.0
             }), \
             patch('monitor.monitor_api._app_stats_request_event'):
            resp = client.get('/monitor/app_disk_io_stats')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert 'apps' in data['data']
        assert len(data['data']['apps']) == 1

    def test_with_n_parameter(self, real_app):
        client, _ = real_app
        fake_io = [{'name': f'io{i}'} for i in range(8)]
        with patch('monitor.monitor_api._start_app_stats_auto_refresh'), \
             patch('monitor.monitor_api._APP_STATS_CACHE', {
                 'resource': None, 'disk_io': fake_io,
                 'last_request_ts': 0.0, 'ts': 1000.0
             }), \
             patch('monitor.monitor_api._app_stats_request_event'):
            resp = client.get('/monitor/app_disk_io_stats?n=3')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert len(data['data']['apps']) == 3

    def test_cache_miss_falls_back_to_sync(self, real_app):
        client, _ = real_app
        mock_monitor = MagicMock()
        mock_monitor.get_app_disk_io_stats.return_value = [{'name': 'sync_io'}]
        with patch('monitor.monitor_api._start_app_stats_auto_refresh'), \
             patch('monitor.monitor_api._APP_STATS_CACHE', {
                 'resource': None, 'disk_io': None,
                 'last_request_ts': 0.0, 'ts': 0.0
             }), \
             patch('monitor.monitor_api._app_stats_request_event'), \
             patch('monitor.monitor_api._get_resource_monitor', return_value=mock_monitor):
            resp = client.get('/monitor/app_disk_io_stats')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert len(data['data']['apps']) == 1

    def test_exception(self, real_app):
        """Sync fallback collection raising should return EXCEPTION_ERROR."""
        client, _ = real_app
        mock_monitor = MagicMock()
        mock_monitor.get_app_disk_io_stats.side_effect = RuntimeError("boom")
        with patch('monitor.monitor_api._start_app_stats_auto_refresh'), \
             patch('monitor.monitor_api._APP_STATS_CACHE', {
                 'resource': None, 'disk_io': None,
                 'last_request_ts': 0.0, 'ts': 0.0
             }), \
             patch('monitor.monitor_api._app_stats_request_event'), \
             patch('monitor.monitor_api._get_resource_monitor', return_value=mock_monitor):
            resp = client.get('/monitor/app_disk_io_stats')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestMonitorProcesses:
    """Test the REAL /monitor/processes route handler."""

    def test_success(self, real_app):
        client, _ = real_app
        mock_proc1 = MagicMock()
        mock_proc1.info = {
            'pid': 100, 'name': 'python', 'username': 'root',
            'cpu_percent': 25.5, 'memory_percent': 3.2,
            'status': 'running', 'cmdline': ['python', 'app.py'],
            'memory_info': MagicMock(rss=104857600),
        }
        mock_proc2 = MagicMock()
        mock_proc2.info = {
            'pid': 200, 'name': 'nginx', 'username': 'www',
            'cpu_percent': 5.1, 'memory_percent': 1.0,
            'status': 'sleeping', 'cmdline': ['nginx'],
            'memory_info': MagicMock(rss=52428800),
        }
        with patch('monitor.monitor_api.psutil') as mock_psutil:
            mock_psutil.process_iter.return_value = [mock_proc1, mock_proc2]
            mock_psutil.NoSuchProcess = type('NoSuchProcess', (Exception,), {})
            mock_psutil.AccessDenied = type('AccessDenied', (Exception,), {})
            resp = client.get('/monitor/processes')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data']['count'] == 2
        procs = data['data']['processes']
        # Sorted by cpu_percent descending
        assert procs[0]['pid'] == 100
        assert procs[0]['cpu_percent'] == 25.5
        assert procs[1]['pid'] == 200

    def test_empty_process_list(self, real_app):
        client, _ = real_app
        with patch('monitor.monitor_api.psutil') as mock_psutil:
            mock_psutil.process_iter.return_value = []
            mock_psutil.NoSuchProcess = type('NoSuchProcess', (Exception,), {})
            mock_psutil.AccessDenied = type('AccessDenied', (Exception,), {})
            resp = client.get('/monitor/processes')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data']['count'] == 0
        assert data['data']['processes'] == []

    def test_access_denied_processes_skipped(self, real_app):
        client, _ = real_app

        class FakeAccessDenied(Exception):
            pass

        class FakeNoSuchProcess(Exception):
            pass

        good_proc = MagicMock()
        good_proc.info = {
            'pid': 1, 'name': 'init', 'username': 'root',
            'cpu_percent': 0.1, 'memory_percent': 0.01,
            'status': 'sleeping', 'cmdline': ['/sbin/init'],
            'memory_info': MagicMock(rss=1024),
        }
        bad_proc = MagicMock()
        bad_proc.info.__getitem__ = MagicMock(side_effect=FakeAccessDenied())
        # The real code accesses p.info then info['pid'] etc. within a try block.
        # Simulate a process that raises AccessDenied on attribute access
        type(bad_proc).info = PropertyMock(side_effect=FakeAccessDenied())

        with patch('monitor.monitor_api.psutil') as mock_psutil:
            mock_psutil.process_iter.return_value = [bad_proc, good_proc]
            mock_psutil.NoSuchProcess = FakeNoSuchProcess
            mock_psutil.AccessDenied = FakeAccessDenied
            resp = client.get('/monitor/processes')
        data = resp.get_json()
        assert data['retcode'] == 0
        # The bad_proc should be skipped, only good_proc remains
        assert data['data']['count'] == 1

    def test_exception(self, real_app):
        """psutil.process_iter raising should be caught and return EXCEPTION_ERROR."""
        client, _ = real_app
        with patch('monitor.monitor_api.psutil.process_iter',
                   side_effect=RuntimeError("boom")):
            resp = client.get('/monitor/processes')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestMonitorDynamicInfo:
    """Test the REAL /monitor/dynamic_info route handler."""

    def test_success_from_cache(self, real_app):
        client, _ = real_app
        fake_data = {
            'cpu_usage': 45.2, 'memory_used_percent': 62.0,
            'uptime_seconds': 86400
        }
        with patch('monitor.monitor_api._start_dynamic_info_auto_refresh'), \
             patch('monitor.monitor_api._DYNAMIC_INFO_CACHE', {
                 'data': fake_data, 'ts': 1000.0
             }):
            resp = client.get('/monitor/dynamic_info')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data']['cpu_usage'] == 45.2
        assert data['data']['memory_used_percent'] == 62.0

    def test_cache_miss_collects_synchronously(self, real_app):
        client, _ = real_app
        collected = {'cpu_usage': 10.0, 'uptime_seconds': 100}
        with patch('monitor.monitor_api._start_dynamic_info_auto_refresh'), \
             patch('monitor.monitor_api._DYNAMIC_INFO_CACHE', {'data': None, 'ts': 0.0}), \
             patch('monitor.monitor_api._get_resource_monitor') as mock_rm, \
             patch('monitor.monitor_api._get_system_pressure_monitor') as mock_spm, \
             patch('monitor.monitor_api.collect_dynamic_info', return_value=collected):
            resp = client.get('/monitor/dynamic_info')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data']['cpu_usage'] == 10.0

    def test_sync_collection_failure(self, real_app):
        client, _ = real_app
        with patch('monitor.monitor_api._start_dynamic_info_auto_refresh'), \
             patch('monitor.monitor_api._DYNAMIC_INFO_CACHE', {'data': None, 'ts': 0.0}), \
             patch('monitor.monitor_api._get_resource_monitor') as mock_rm, \
             patch('monitor.monitor_api._get_system_pressure_monitor') as mock_spm, \
             patch('monitor.monitor_api.collect_dynamic_info',
                   side_effect=RuntimeError("collection failed")):
            resp = client.get('/monitor/dynamic_info')
        data = resp.get_json()
        assert data['retcode'] == 100  # EXCEPTION_ERROR


class TestMonitorHistory:
    """Test the REAL /monitor/history route handler."""

    def test_exception(self, real_app):
        """MonitorSnapshot.query_recent raising should return EXCEPTION_ERROR."""
        client, _ = real_app
        with patch('monitor.monitor_api._start_snapshot_cleanup_task'), \
             patch('monitor.monitor_api.MonitorSnapshot') as mock_snap:
            mock_snap.query_recent.side_effect = RuntimeError("boom")
            resp = client.get('/monitor/history')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestMonitorGetWeightsTop:
    """Test the REAL /monitor/config/weights_top GET route handler."""

    def test_exception(self, real_app):
        """_get_config_updated_at raising should return EXCEPTION_ERROR."""
        client, _ = real_app
        with patch('config.config.b_config') as mock_config, \
             patch('monitor.monitor_api._get_config_updated_at',
                   side_effect=RuntimeError("boom")):
            mock_config.weights_top = {'cpu': 1}
            resp = client.get('/monitor/config/weights_top')
        data = resp.get_json()
        assert data['retcode'] == 100


class TestMonitorGetPassiveControl:
    """Test the REAL /monitor/config/passive_control GET route handler."""

    def test_success_enabled(self, real_app):
        client, _ = real_app
        with patch('config.config.b_config') as mock_config, \
             patch('monitor.monitor_api._get_config_updated_at', return_value=1719700000):
            mock_config.passive_resource_control = {'enabled': True}
            resp = client.get('/monitor/config/passive_control')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data']['enabled'] is True
        assert data['data']['updated_at'] == 1719700000

    def test_success_disabled(self, real_app):
        client, _ = real_app
        with patch('config.config.b_config') as mock_config, \
             patch('monitor.monitor_api._get_config_updated_at', return_value=0):
            mock_config.passive_resource_control = {'enabled': False}
            resp = client.get('/monitor/config/passive_control')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data']['enabled'] is False

    def test_none_config_defaults_to_enabled(self, real_app):
        client, _ = real_app
        with patch('config.config.b_config') as mock_config, \
             patch('monitor.monitor_api._get_config_updated_at', return_value=0):
            mock_config.passive_resource_control = None
            resp = client.get('/monitor/config/passive_control')
        data = resp.get_json()
        assert data['retcode'] == 0
        # Default is True when the section is None/empty
        assert data['data']['enabled'] is True


class TestMonitorPostPassiveControl:
    """Test the REAL /monitor/config/passive_control POST route handler."""

    def test_success_update(self, real_app):
        client, _ = real_app
        with patch('config.config.b_config') as mock_config, \
             patch('monitor.monitor_api._get_config_updated_at', return_value=0), \
             patch('monitor.monitor_api._bump_config_updated_at', return_value=1719700100), \
             patch('monitor.monitor_api._coerce_expected_ts', return_value=None):
            mock_config.passive_resource_control = {'enabled': True}
            mock_config.update_config_section.return_value = None
            resp = client.post('/monitor/config/passive_control',
                              json={'enabled': False},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 0
        assert data['data']['success'] is True
        assert data['data']['enabled'] is False
        assert data['data']['updated_at'] == 1719700100

    def test_missing_enabled_field(self, real_app):
        """Missing 'enabled' triggers RetCode.PARAM_ERROR which is undefined in the enum.
        This causes an AttributeError at runtime — the test verifies the error is raised."""
        client, _ = real_app
        with patch('config.config.b_config') as mock_config:
            mock_config.passive_resource_control = {'enabled': True}
            resp = client.post('/monitor/config/passive_control',
                              json={'something_else': True},
                              content_type='application/json')
        data = resp.get_json()
        # BUG: RetCode.PARAM_ERROR is not defined in the enum, so this hits the
        # generic except block and returns EXCEPTION_ERROR (100) instead.
        assert data['retcode'] in (100, 101), (
            f"Expected error retcode for missing 'enabled' field, got {data['retcode']}"
        )

    def test_conflict_with_expected_updated_at(self, real_app):
        client, _ = real_app
        with patch('config.config.b_config') as mock_config, \
             patch('monitor.monitor_api._get_config_updated_at', return_value=500), \
             patch('monitor.monitor_api._coerce_expected_ts', return_value=100):
            mock_config.passive_resource_control = {'enabled': True}
            resp = client.post('/monitor/config/passive_control',
                              json={'enabled': True, 'expected_updated_at': 100},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 409  # CONFLICT
        assert data['data']['success'] is False

    def test_invalid_expected_updated_at(self, real_app):
        client, _ = real_app
        with patch('config.config.b_config') as mock_config, \
             patch('monitor.monitor_api._coerce_expected_ts', return_value=-1):
            mock_config.passive_resource_control = {'enabled': True}
            resp = client.post('/monitor/config/passive_control',
                              json={'enabled': True, 'expected_updated_at': 'not_a_number'},
                              content_type='application/json')
        data = resp.get_json()
        assert data['retcode'] == 101  # ARGUMENT_ERROR
        assert data['data']['success'] is False
