# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
TC-S-010: OOM Protection Setting

Verifies that critical-priority apps can be protected from the OOM killer
by setting a low oom_score_adj value.
"""

import os
import pytest


@pytest.mark.service
@pytest.mark.root
class TestOOMProtection:

    def test_set_oom_score_for_critical_app(self, api, base_url):
        """Setting OOM score for a critical app adjusts /proc/pid/oom_score_adj."""
        # 1. Get a controlled app that is currently running
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()
        assert data['retcode'] == 0, f"Failed to get dynamic info: {data}"

        apps = data.get('data', {}).get('apps', [])
        running_app = None
        for app in apps:
            if app.get('pid') and int(app['pid']) > 0:
                running_app = app
                break

        if running_app is None:
            pytest.skip("No running controlled app found for OOM test")

        app_id = running_app.get('app_id') or running_app.get('name')
        pid = int(running_app['pid'])

        # 2. Call POST /app/set_oom_score with its app_id
        resp = api.post(f"{base_url}/app/set_oom_score",
                        json={'app_id': app_id})
        data = resp.json()
        assert data['retcode'] == 0, f"set_oom_score failed: {data}"

        # 3. Read /proc/{pid}/oom_score_adj and verify it's a negative value
        oom_path = f"/proc/{pid}/oom_score_adj"
        try:
            with open(oom_path, 'r') as f:
                score_adj = int(f.read().strip())
        except (IOError, FileNotFoundError):
            pytest.skip(f"Cannot read {oom_path} (process may have exited)")

        assert score_adj < 0, (
            f"Expected negative oom_score_adj for critical app, got {score_adj}"
        )

    def test_oom_score_requires_running_process(self, api, base_url):
        """OOM score setting on a non-running app should handle gracefully."""
        resp = api.post(f"{base_url}/app/set_oom_score",
                        json={'app_id': 'nonexistent_release_test'})
        data = resp.json()
        # Should either succeed silently or return an error, not crash
        assert data['retcode'] in (0, 100, 103, 404)

    def test_oom_score_persists_across_limit_restore(self, api, base_url):
        """OOM protection should persist even after resource limit/restore cycles."""
        # 1. Get a running controlled app
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()
        assert data['retcode'] == 0

        apps = data.get('data', {}).get('apps', [])
        running_app = None
        for app in apps:
            if app.get('pid') and int(app['pid']) > 0:
                running_app = app
                break

        if running_app is None:
            pytest.skip("No running controlled app found for OOM persistence test")

        app_id = running_app.get('app_id') or running_app.get('name')
        pid = int(running_app['pid'])

        # 2. Set OOM protection
        resp = api.post(f"{base_url}/app/set_oom_score",
                        json={'app_id': app_id})
        data = resp.json()
        assert data['retcode'] == 0, f"set_oom_score failed: {data}"

        # 3. Read initial oom_score_adj
        oom_path = f"/proc/{pid}/oom_score_adj"
        try:
            with open(oom_path, 'r') as f:
                initial_score = int(f.read().strip())
        except (IOError, FileNotFoundError):
            pytest.skip(f"Cannot read {oom_path} (process may have exited)")

        # 4. Perform a limit/restore cycle
        try:
            api.post(f"{base_url}/app/limit",
                     json={'app_id': app_id})
            api.post(f"{base_url}/app/restore",
                     json={'app_id': app_id})
        except Exception:
            pass  # Best-effort; the cycle itself is not what we're testing

        # 5. Verify OOM score is still set
        try:
            with open(oom_path, 'r') as f:
                final_score = int(f.read().strip())
        except (IOError, FileNotFoundError):
            pytest.skip(f"Cannot read {oom_path} after limit/restore cycle")

        assert final_score <= initial_score, (
            f"OOM protection weakened after limit/restore: "
            f"was {initial_score}, now {final_score}"
        )
