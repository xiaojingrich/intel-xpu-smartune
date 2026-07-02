# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Acceptance tests: App Management
Full lifecycle of application registration, priority setting, and control.
"""

import uuid

import pytest


def unique_app_id():
    return f"acceptance_test_{uuid.uuid4().hex[:8]}"


@pytest.mark.service
class TestGetApps:
    """Verify app discovery endpoint."""

    def test_get_apps_returns_list(self, api, base_url):
        resp = api.post(f"{base_url}/app/get_apps", json={'store': False})
        assert resp.status_code == 200
        data = resp.json()
        assert data['retcode'] == 0
        assert isinstance(data['data'], list)

    def test_get_apps_with_store(self, api, base_url):
        resp = api.post(f"{base_url}/app/get_apps", json={'store': True})
        assert resp.status_code == 200
        data = resp.json()
        assert data['retcode'] == 0
        assert len(data['data']) > 0

    def test_get_apps_entry_has_required_fields(self, api, base_url):
        resp = api.post(f"{base_url}/app/get_apps", json={'store': False})
        apps = resp.json()['data']
        if apps:
            app = apps[0]
            assert 'app_id' in app
            assert 'name' in app
            assert 'commandline' in app or 'cmdline' in app


@pytest.mark.service
class TestSetPriority:
    """Verify priority setting for apps."""

    def test_set_priority_missing_params(self, api, base_url):
        resp = api.post(f"{base_url}/app/set_priority", json={})
        data = resp.json()
        assert data['retcode'] == 101

    def test_set_priority_nonexistent_app(self, api, base_url):
        resp = api.post(f"{base_url}/app/set_priority",
                       json={'app_id': 'nonexistent_xyz', 'priority': 'medium'})
        data = resp.json()
        assert data['retcode'] == 404


@pytest.mark.service
class TestSetToControl:
    """Verify adding apps to control list."""

    def test_set_to_control_new_app(self, api, base_url):
        app_id = unique_app_id()
        resp = api.post(f"{base_url}/app/set_to_control", json={
            'app_id': app_id,
            'app_name': 'AcceptanceTestApp',
            'controlled': True,
            'priority': 'medium',
            'cmdline': 'acceptance_test_cmd',
            'remark': 'automated acceptance test'
        })
        data = resp.json()
        assert data['retcode'] == 0
        assert data['data']['controlled'] is True

        # Cleanup
        api.post(f"{base_url}/app/remove_from_control",
                json={'app_id': app_id, 'app_name': 'AcceptanceTestApp'})

    def test_set_to_control_and_verify_in_controlled_list(self, api, base_url):
        app_id = unique_app_id()
        api.post(f"{base_url}/app/set_to_control", json={
            'app_id': app_id,
            'app_name': 'ControlListVerify',
            'controlled': True,
            'priority': 'medium',
            'cmdline': 'verify_cmd',
        })

        resp = api.post(f"{base_url}/app/get_controlled_app", json={})
        data = resp.json()
        assert data['retcode'] == 0
        controlled_ids = [a.get('app_id', a.get('id', '')) for a in data['data']]
        assert app_id in controlled_ids

        # Cleanup
        api.post(f"{base_url}/app/remove_from_control",
                json={'app_id': app_id, 'app_name': 'ControlListVerify'})


@pytest.mark.service
class TestRemoveFromControl:
    """Verify removing apps from control list."""

    def test_remove_requires_identifier(self, api, base_url):
        resp = api.post(f"{base_url}/app/remove_from_control", json={})
        data = resp.json()
        assert data['retcode'] == 101

    def test_remove_controlled_app(self, api, base_url):
        app_id = unique_app_id()
        api.post(f"{base_url}/app/set_to_control", json={
            'app_id': app_id,
            'app_name': 'RemoveTest',
            'controlled': True,
            'priority': 'low',
            'cmdline': 'remove_test_cmd',
        })

        resp = api.post(f"{base_url}/app/remove_from_control",
                       json={'app_id': app_id, 'app_name': 'RemoveTest'})
        data = resp.json()
        assert data['retcode'] == 0
        assert data['data']['controlled'] is False


@pytest.mark.service
class TestGetControlledApp:
    """Verify controlled app listing."""

    def test_get_controlled_app_format(self, api, base_url):
        resp = api.post(f"{base_url}/app/get_controlled_app", json={})
        data = resp.json()
        assert data['retcode'] == 0
        assert isinstance(data['data'], list)

    def test_controlled_app_has_fields(self, api, base_url):
        resp = api.post(f"{base_url}/app/get_controlled_app", json={})
        apps = resp.json()['data']
        if apps:
            app = apps[0]
            assert 'app_id' in app or 'id' in app
            assert 'name' in app or 'app_name' in app


@pytest.mark.service
class TestCheckRunningApps:
    """Verify running app detection."""

    def test_check_running_apps(self, api, base_url):
        resp = api.post(f"{base_url}/app/check_running_apps", json={})
        data = resp.json()
        assert data['retcode'] == 0


@pytest.mark.service
class TestGetPendingApp:
    """Verify pending app listing."""

    def test_get_pending_app(self, api, base_url):
        resp = api.post(f"{base_url}/app/get_pending_app", json={})
        data = resp.json()
        assert data['retcode'] == 0
        assert isinstance(data['data'], list)


@pytest.mark.service
class TestCancelRelaunch:
    """Verify relaunch cancellation."""

    def test_cancel_relaunch_missing_app_id(self, api, base_url):
        resp = api.post(f"{base_url}/app/cancel_relaunch", json={})
        data = resp.json()
        assert data['retcode'] == 101

    def test_cancel_relaunch_nonexistent(self, api, base_url):
        resp = api.post(f"{base_url}/app/cancel_relaunch",
                       json={'app_id': 'nonexistent_abc'})
        data = resp.json()
        assert data['retcode'] != 0


@pytest.mark.service
class TestFullLifecycle:
    """End-to-end app lifecycle: discover -> control -> priority -> remove."""

    def test_full_app_lifecycle(self, api, base_url):
        app_id = unique_app_id()

        # 1. Register for control
        resp = api.post(f"{base_url}/app/set_to_control", json={
            'app_id': app_id,
            'app_name': 'LifecycleTest',
            'controlled': True,
            'priority': 'medium',
            'cmdline': 'lifecycle_test_bin',
        })
        assert resp.json()['retcode'] == 0

        # 2. Update priority
        resp = api.post(f"{base_url}/app/set_priority",
                       json={'app_id': app_id, 'priority': 'critical'})
        assert resp.json()['retcode'] == 0

        # 3. Verify in controlled list
        resp = api.post(f"{base_url}/app/get_controlled_app", json={})
        controlled = resp.json()['data']
        app_entry = next((a for a in controlled
                         if a.get('app_id', a.get('id')) == app_id), None)
        assert app_entry is not None

        # 4. Remove from control
        resp = api.post(f"{base_url}/app/remove_from_control",
                       json={'app_id': app_id, 'app_name': 'LifecycleTest'})
        assert resp.json()['retcode'] == 0

        # 5. Verify removed
        resp = api.post(f"{base_url}/app/get_controlled_app", json={})
        controlled = resp.json()['data']
        app_entry = next((a for a in controlled
                         if a.get('app_id', a.get('id')) == app_id), None)
        assert app_entry is None or app_entry.get('controlled') is False
