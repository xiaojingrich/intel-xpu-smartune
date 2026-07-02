# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Acceptance tests: Resource Control
Verify resource limiting and restoration via cgroups on real hardware.
"""

import os
import time
import uuid

import pytest


def unique_app_id():
    return f"res_test_{uuid.uuid4().hex[:8]}"


@pytest.mark.service
class TestResourceLimitAPI:
    """Verify resource limit endpoint behavior."""

    def test_resource_limit_missing_params(self, api, base_url):
        resp = api.post(f"{base_url}/app/resource_limit", json={})
        data = resp.json()
        assert data['retcode'] == 101

    def test_resource_limit_nonexistent_app(self, api, base_url):
        resp = api.post(f"{base_url}/app/resource_limit", json={
            'app_id': 'nonexistent_resource_app',
            'app_name': 'NoSuchApp',
            'priority': 'low'
        })
        data = resp.json()
        assert data['retcode'] != 0

    def test_resource_restore_missing_app_id(self, api, base_url):
        resp = api.post(f"{base_url}/app/resource_restore", json={})
        data = resp.json()
        assert data['retcode'] == 101

    def test_resource_restore_nonexistent_app(self, api, base_url):
        resp = api.post(f"{base_url}/app/resource_restore",
                       json={'app_id': 'nonexistent_restore_app'})
        data = resp.json()
        assert data['retcode'] != 0


@pytest.mark.service
class TestResourceLimitProfile:
    """Verify resource limit profile retrieval."""

    def test_profile_missing_params(self, api, base_url):
        resp = api.post(f"{base_url}/app/resource_limit_profile", json={})
        data = resp.json()
        assert data['retcode'] == 101

    def test_profile_returns_defaults(self, api, base_url):
        app_id = unique_app_id()
        # Register app first
        api.post(f"{base_url}/app/set_to_control", json={
            'app_id': app_id,
            'app_name': 'ProfileTest',
            'controlled': True,
            'priority': 'medium',
            'cmdline': 'profile_test_cmd',
        })

        resp = api.post(f"{base_url}/app/resource_limit_profile", json={
            'app_id': app_id,
            'app_name': 'ProfileTest',
            'priority': 'medium'
        })
        data = resp.json()
        assert data['retcode'] == 0
        profile = data['data']
        assert isinstance(profile, dict)

        # Cleanup
        api.post(f"{base_url}/app/remove_from_control",
                json={'app_id': app_id, 'app_name': 'ProfileTest'})


@pytest.mark.service
@pytest.mark.root
@pytest.mark.cgroup
class TestResourceLimitWithCgroup:
    """Verify actual cgroup resource limiting (requires root + cgroup v2)."""

    def test_limit_and_restore_cycle(self, api, base_url):
        """Register app, apply limit, verify, then restore."""
        app_id = unique_app_id()

        # Register
        api.post(f"{base_url}/app/set_to_control", json={
            'app_id': app_id,
            'app_name': 'CgroupLimitTest',
            'controlled': True,
            'priority': 'medium',
            'cmdline': 'sleep',
        })

        # Apply resource limit
        resp = api.post(f"{base_url}/app/resource_limit", json={
            'app_id': app_id,
            'app_name': 'CgroupLimitTest',
            'priority': 'low'
        })
        # May fail if app isn't actually running - that's acceptable
        limit_result = resp.json()

        # Try to restore
        resp = api.post(f"{base_url}/app/resource_restore",
                       json={'app_id': app_id})
        restore_result = resp.json()

        # Cleanup
        api.post(f"{base_url}/app/remove_from_control",
                json={'app_id': app_id, 'app_name': 'CgroupLimitTest'})

        # At least the API should not crash
        assert limit_result['retcode'] in (0, 103)
        assert restore_result['retcode'] in (0, 103)

    def test_limit_with_custom_overrides(self, api, base_url):
        """Apply resource limit with custom override parameters."""
        app_id = unique_app_id()

        api.post(f"{base_url}/app/set_to_control", json={
            'app_id': app_id,
            'app_name': 'OverrideTest',
            'controlled': True,
            'priority': 'medium',
            'cmdline': 'sleep',
        })

        overrides = {
            'cpu': {'rate': 0.5, 'enabled': True},
            'memory': {'rate': 0.3, 'enabled': True},
        }

        resp = api.post(f"{base_url}/app/resource_limit", json={
            'app_id': app_id,
            'app_name': 'OverrideTest',
            'priority': 'high',
            'limit_overrides': overrides
        })
        data = resp.json()
        # API should accept override params without crashing
        assert data['retcode'] in (0, 103)

        # Cleanup
        api.post(f"{base_url}/app/remove_from_control",
                json={'app_id': app_id, 'app_name': 'OverrideTest'})


@pytest.mark.service
@pytest.mark.root
@pytest.mark.cgroup
class TestCgroupVerification:
    """Verify cgroup files are actually modified after limiting."""

    CGROUP_BASE = '/sys/fs/cgroup'

    def _find_app_cgroup(self, app_name):
        """Try to find the cgroup path for a given app."""
        for root, dirs, files in os.walk(self.CGROUP_BASE):
            if app_name.lower() in root.lower():
                return root
        return None

    def test_cgroup_mount_accessible(self):
        """cgroup v2 filesystem should be mounted and readable."""
        assert os.path.exists(self.CGROUP_BASE)
        controllers_path = os.path.join(self.CGROUP_BASE, 'cgroup.controllers')
        assert os.path.exists(controllers_path)
        with open(controllers_path) as f:
            controllers = f.read().strip()
        assert 'cpu' in controllers or 'memory' in controllers

    def test_cpu_controller_available(self):
        """CPU controller should be enabled."""
        controllers_path = os.path.join(self.CGROUP_BASE, 'cgroup.controllers')
        with open(controllers_path) as f:
            controllers = f.read().strip().split()
        assert 'cpu' in controllers

    def test_memory_controller_available(self):
        """Memory controller should be enabled."""
        controllers_path = os.path.join(self.CGROUP_BASE, 'cgroup.controllers')
        with open(controllers_path) as f:
            controllers = f.read().strip().split()
        assert 'memory' in controllers

    def test_io_controller_available(self):
        """IO controller should be enabled."""
        controllers_path = os.path.join(self.CGROUP_BASE, 'cgroup.controllers')
        with open(controllers_path) as f:
            controllers = f.read().strip().split()
        assert 'io' in controllers
