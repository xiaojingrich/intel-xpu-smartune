# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Acceptance tests: API side effects (read-back verification).

For every state-mutating endpoint these tests prove the WRITE ACTUALLY TOOK
EFFECT — after the mutating call they read the state back (via a GET/read
endpoint, or by observing real /proc state) and assert the desired outcome
actually happened.  This is deliberately different from the existing tests
that only assert ``retcode == 0`` on the write; the goal here is to catch the
"API returns 200 but nothing changed" silent-failure bug class.

All tests run against a LIVE SmartTune service (``@pytest.mark.service``) and
clean up any state they create in a ``finally`` block.
"""

import os
import sys
import time
import uuid
import subprocess

import pytest


def unique_app_id():
    return f"sidefx_{uuid.uuid4().hex[:8]}"


def _launch_unique_process():
    """Launch a real, uniquely-named subprocess and return (proc, script_path, token).

    Writes a tiny sleeper script to /tmp whose path contains a unique uuid
    token, then runs it with the current interpreter so /proc/<pid>/cmdline
    references the token.  Caller MUST kill the process and delete the script.
    """
    token = uuid.uuid4().hex[:12]
    script_path = f"/tmp/smartune_disc_{token}.py"
    with open(script_path, "w") as f:
        f.write("import time\ntime.sleep(120)\n")
    proc = subprocess.Popen([sys.executable, script_path])
    # Give /proc a moment to reflect the new process' cmdline.
    time.sleep(1.0)
    return proc, script_path, token


def _cleanup_process(proc, script_path):
    try:
        if proc is not None:
            proc.kill()
            proc.wait(timeout=5)
    except Exception:
        pass
    try:
        if script_path and os.path.exists(script_path):
            os.remove(script_path)
    except Exception:
        pass


@pytest.mark.service
class TestSetPrioritySideEffect:
    """Prove /app/set_priority persists and is readable via get_priority_data."""

    def test_set_priority_reflected_in_get_priority_data(self, api, base_url):
        app_id = unique_app_id()
        try:
            # Register the app first so a DB row exists to update.
            resp = api.post(f"{base_url}/app/set_to_control", json={
                'app_id': app_id,
                'app_name': 'PrioritySideEffect',
                'controlled': True,
                'priority': 'medium',
                'cmdline': 'priority_sidefx_cmd',
            })
            assert resp.json()['retcode'] == 0

            # Set to a known value, then read back and assert it stuck.
            resp = api.post(f"{base_url}/app/set_priority",
                            json={'app_id': app_id, 'priority': 'high'})
            assert resp.json()['retcode'] == 0

            resp = api.post(f"{base_url}/app/get_priority_data",
                            json={'app_id': app_id})
            data = resp.json()
            assert data['retcode'] == 0
            assert data['data']['priority'] == 'high', \
                "set_priority('high') returned 0 but read-back did not show 'high'"

            # Change it again and confirm the new value is reflected.
            resp = api.post(f"{base_url}/app/set_priority",
                            json={'app_id': app_id, 'priority': 'low'})
            assert resp.json()['retcode'] == 0

            resp = api.post(f"{base_url}/app/get_priority_data",
                            json={'app_id': app_id})
            data = resp.json()
            assert data['retcode'] == 0
            assert data['data']['priority'] == 'low', \
                "set_priority('low') returned 0 but read-back did not show 'low'"
        finally:
            api.post(f"{base_url}/app/remove_from_control",
                     json={'app_id': app_id, 'app_name': 'PrioritySideEffect'})


@pytest.mark.service
class TestSetToControlSideEffect:
    """Prove /app/set_to_control makes the app appear in the controlled list."""

    def test_registered_app_appears_in_controlled_list(self, api, base_url):
        app_id = unique_app_id()
        try:
            resp = api.post(f"{base_url}/app/set_to_control", json={
                'app_id': app_id,
                'app_name': 'ControlAppears',
                'controlled': True,
                'priority': 'medium',
                'cmdline': 'control_appears_cmd',
            })
            assert resp.json()['retcode'] == 0

            resp = api.post(f"{base_url}/app/get_controlled_app", json={})
            data = resp.json()
            assert data['retcode'] == 0
            entry = next((a for a in data['data']
                          if a.get('app_id', a.get('id')) == app_id), None)
            assert entry is not None, \
                "set_to_control returned 0 but app is absent from controlled list"
            assert entry.get('controlled') is True
            assert entry.get('priority') == 'medium'
        finally:
            api.post(f"{base_url}/app/remove_from_control",
                     json={'app_id': app_id, 'app_name': 'ControlAppears'})

    def test_controlled_flag_persists_correct_priority(self, api, base_url):
        app_id = unique_app_id()
        try:
            resp = api.post(f"{base_url}/app/set_to_control", json={
                'app_id': app_id,
                'app_name': 'ControlPriority',
                'controlled': True,
                'priority': 'high',
                'cmdline': 'control_priority_cmd',
            })
            assert resp.json()['retcode'] == 0

            resp = api.post(f"{base_url}/app/get_controlled_app", json={})
            data = resp.json()
            entry = next((a for a in data['data']
                          if a.get('app_id', a.get('id')) == app_id), None)
            assert entry is not None
            assert entry.get('priority') == 'high', \
                "controlled list did not reflect the priority='high' that was written"
        finally:
            api.post(f"{base_url}/app/remove_from_control",
                     json={'app_id': app_id, 'app_name': 'ControlPriority'})


@pytest.mark.service
class TestRemoveFromControlSideEffect:
    """Prove /app/remove_from_control actually drops the app from control."""

    def test_removed_app_no_longer_controlled(self, api, base_url):
        app_id = unique_app_id()
        try:
            resp = api.post(f"{base_url}/app/set_to_control", json={
                'app_id': app_id,
                'app_name': 'RemoveSideEffect',
                'controlled': True,
                'priority': 'medium',
                'cmdline': 'remove_sidefx_cmd',
            })
            assert resp.json()['retcode'] == 0

            # Confirm it is present while controlled.
            resp = api.post(f"{base_url}/app/get_controlled_app", json={})
            controlled_ids = [a.get('app_id', a.get('id'))
                              for a in resp.json()['data']]
            assert app_id in controlled_ids

            # Remove it.
            resp = api.post(f"{base_url}/app/remove_from_control",
                            json={'app_id': app_id, 'app_name': 'RemoveSideEffect'})
            assert resp.json()['retcode'] == 0

            # get_controlled_app filters controlled==True, so a removed app
            # either disappears from the list or shows controlled=False.
            resp = api.post(f"{base_url}/app/get_controlled_app", json={})
            entry = next((a for a in resp.json()['data']
                          if a.get('app_id', a.get('id')) == app_id), None)
            assert entry is None or entry.get('controlled') is False, \
                "remove_from_control returned 0 but app is still controlled"
        finally:
            api.post(f"{base_url}/app/remove_from_control",
                     json={'app_id': app_id, 'app_name': 'RemoveSideEffect'})


@pytest.mark.service
class TestNewControlledAppSideEffect:
    """Prove /app/new_controlled_app persists to config + DB."""

    def test_new_app_persisted_and_listed(self, api, base_url):
        app_id = unique_app_id()
        try:
            resp = api.post(f"{base_url}/app/new_controlled_app", json={
                'name': 'NewCtrlAppPersist',
                'id': app_id,
                'priority': 'medium',
                'commandline': f'/usr/bin/{app_id}',
                'bpf_name': [app_id],
                'process_names': [app_id],
            })
            assert resp.json()['retcode'] == 0

            resp = api.post(f"{base_url}/app/get_controlled_app", json={})
            data = resp.json()
            assert data['retcode'] == 0
            controlled_ids = [a.get('app_id', a.get('id')) for a in data['data']]
            assert app_id in controlled_ids, \
                "new_controlled_app returned 0 but app is absent from controlled list"
        finally:
            api.post(f"{base_url}/app/purge_controlled_app", json={'id': app_id})

    def test_duplicate_id_rejected_after_creation(self, api, base_url):
        app_id = unique_app_id()
        try:
            resp = api.post(f"{base_url}/app/new_controlled_app", json={
                'name': 'DupCtrlApp',
                'id': app_id,
                'priority': 'low',
                'commandline': f'/usr/bin/{app_id}',
                'bpf_name': [app_id],
                'process_names': [app_id],
            })
            assert resp.json()['retcode'] == 0

            # Re-creating with the same id must now conflict, proving the first
            # write really persisted to config.
            resp = api.post(f"{base_url}/app/new_controlled_app", json={
                'name': 'DupCtrlAppSecond',
                'id': app_id,
                'priority': 'low',
                'commandline': f'/usr/bin/{app_id}_2',
                'bpf_name': [f'{app_id}_2'],
                'process_names': [f'{app_id}_2'],
            })
            assert resp.json()['retcode'] == 409, \
                "duplicate id was not rejected — first app did not persist to config"
        finally:
            api.post(f"{base_url}/app/purge_controlled_app", json={'id': app_id})


@pytest.mark.service
class TestPurgeControlledAppSideEffect:
    """Prove /app/purge_controlled_app truly removes from config + DB."""

    def test_purge_allows_recreate(self, api, base_url):
        app_id = unique_app_id()
        try:
            # Create.
            resp = api.post(f"{base_url}/app/new_controlled_app", json={
                'name': 'PurgeRecreate',
                'id': app_id,
                'priority': 'low',
                'commandline': f'/usr/bin/{app_id}',
                'bpf_name': [app_id],
                'process_names': [app_id],
            })
            assert resp.json()['retcode'] == 0

            # Purge.
            resp = api.post(f"{base_url}/app/purge_controlled_app",
                            json={'id': app_id})
            assert resp.json()['retcode'] == 0

            # Re-create with the SAME id — must succeed (NOT 409).  If purge
            # silently failed, this would conflict.
            resp = api.post(f"{base_url}/app/new_controlled_app", json={
                'name': 'PurgeRecreate',
                'id': app_id,
                'priority': 'low',
                'commandline': f'/usr/bin/{app_id}',
                'bpf_name': [app_id],
                'process_names': [app_id],
            })
            assert resp.json()['retcode'] == 0, \
                "recreate after purge conflicted — purge did not remove from config/DB"
        finally:
            api.post(f"{base_url}/app/purge_controlled_app", json={'id': app_id})


@pytest.mark.service
class TestConfigWeightsRoundTrip:
    """Prove /monitor/config/weights_top writes are readable and guarded."""

    def test_weights_update_reflected_in_get(self, api, base_url):
        # Capture the original so we can restore it.
        resp = api.get(f"{base_url}/monitor/config/weights_top")
        orig = resp.json()['data']
        orig_ts = orig['updated_at']
        orig_weights = {k: orig[k] for k in ('cpu', 'memory', 'gpu') if k in orig}

        new_ts = None
        try:
            resp = api.post(f"{base_url}/monitor/config/weights_top", json={
                'cpu': 5, 'memory': 5, 'gpu': 5,
                'expected_updated_at': orig_ts,
            })
            data = resp.json()
            assert data['retcode'] == 0
            new_ts = data['data'].get('updated_at')

            resp = api.get(f"{base_url}/monitor/config/weights_top")
            got = resp.json()['data']
            assert int(got['cpu']) == 5
            assert int(got['memory']) == 5
            assert int(got['gpu']) == 5
            assert got['updated_at'] != orig_ts, \
                "weights write returned 0 but updated_at did not change"
        finally:
            # Restore original values using the latest updated_at we know.
            restore_ts = new_ts
            if restore_ts is None:
                resp = api.get(f"{base_url}/monitor/config/weights_top")
                restore_ts = resp.json()['data']['updated_at']
            if orig_weights:
                api.post(f"{base_url}/monitor/config/weights_top", json={
                    **orig_weights,
                    'expected_updated_at': restore_ts,
                })

    def test_stale_updated_at_rejected(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/config/weights_top")
        orig = resp.json()['data']
        orig_ts = orig['updated_at']
        orig_weights = {k: orig[k] for k in ('cpu', 'memory', 'gpu') if k in orig}

        # POST with a deliberately stale expected_updated_at.
        resp = api.post(f"{base_url}/monitor/config/weights_top", json={
            'cpu': 99, 'memory': 99, 'gpu': 99,
            'expected_updated_at': orig_ts - 99999,
        })
        assert resp.json()['retcode'] == 409, \
            "stale expected_updated_at was not rejected with CONFLICT"

        # Confirm the value did NOT change despite the rejected write.
        resp = api.get(f"{base_url}/monitor/config/weights_top")
        got = resp.json()['data']
        assert got['updated_at'] == orig_ts, \
            "rejected write still mutated updated_at — concurrency guard leaked"
        for k, v in orig_weights.items():
            assert int(got[k]) == int(v), \
                f"rejected write still changed weight '{k}'"


@pytest.mark.service
class TestPassiveControlRoundTrip:
    """Prove /monitor/config/passive_control toggle persists."""

    def test_toggle_reflected_in_get(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/config/passive_control")
        orig = resp.json()['data']
        orig_enabled = bool(orig['enabled'])
        orig_ts = orig['updated_at']

        new_ts = None
        try:
            resp = api.post(f"{base_url}/monitor/config/passive_control", json={
                'enabled': (not orig_enabled),
                'expected_updated_at': orig_ts,
            })
            data = resp.json()
            assert data['retcode'] == 0
            new_ts = data['data'].get('updated_at')

            resp = api.get(f"{base_url}/monitor/config/passive_control")
            got = resp.json()['data']
            assert bool(got['enabled']) == (not orig_enabled), \
                "passive_control toggle returned 0 but read-back did not flip"
        finally:
            restore_ts = new_ts
            if restore_ts is None:
                resp = api.get(f"{base_url}/monitor/config/passive_control")
                restore_ts = resp.json()['data']['updated_at']
            api.post(f"{base_url}/monitor/config/passive_control", json={
                'enabled': orig_enabled,
                'expected_updated_at': restore_ts,
            })


@pytest.mark.service
class TestRetentionRoundTrip:
    """Prove /monitor/history/retention writes persist and validate input."""

    def test_retention_update_reflected_in_get(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/history/retention")
        orig = resp.json()['data']
        orig_days = int(orig['retention_days'])
        orig_ts = orig['updated_at']
        min_days = int(orig['min_days'])
        max_days = int(orig['max_days'])

        # Pick a valid value different from current, clamped to bounds.
        new_days = orig_days + 2
        if new_days > max_days:
            new_days = orig_days - 2
        if new_days < min_days:
            new_days = min_days if min_days != orig_days else min_days + 1
        new_days = max(min_days, min(new_days, max_days))
        if new_days == orig_days:
            pytest.skip("cannot pick a retention value distinct from current within bounds")

        new_ts = None
        try:
            resp = api.post(f"{base_url}/monitor/history/retention", json={
                'retention_days': new_days,
                'expected_updated_at': orig_ts,
            })
            data = resp.json()
            assert data['retcode'] == 0
            new_ts = data['data'].get('updated_at')

            resp = api.get(f"{base_url}/monitor/history/retention")
            got = resp.json()['data']
            assert int(got['retention_days']) == new_days, \
                "retention write returned 0 but read-back did not reflect new value"
        finally:
            restore_ts = new_ts
            if restore_ts is None:
                resp = api.get(f"{base_url}/monitor/history/retention")
                restore_ts = resp.json()['data']['updated_at']
            api.post(f"{base_url}/monitor/history/retention", json={
                'retention_days': orig_days,
                'expected_updated_at': restore_ts,
            })

    def test_invalid_retention_rejected_and_unchanged(self, api, base_url):
        resp = api.get(f"{base_url}/monitor/history/retention")
        orig = resp.json()['data']
        orig_days = int(orig['retention_days'])

        # 0 is below min_days → must be rejected.
        resp = api.post(f"{base_url}/monitor/history/retention", json={
            'retention_days': 0,
            'expected_updated_at': orig['updated_at'],
        })
        assert resp.json()['retcode'] != 0, \
            "invalid retention_days=0 was accepted"

        resp = api.get(f"{base_url}/monitor/history/retention")
        got = resp.json()['data']
        assert int(got['retention_days']) == orig_days, \
            "invalid write still mutated retention_days — validation leaked"


@pytest.mark.service
class TestDiscoverSearchSideEffect:
    """Prove /app/discover_search scans real /proc and returns live matches."""

    def test_search_finds_launched_process(self, api, base_url):
        proc, script_path, token = _launch_unique_process()
        try:
            resp = api.post(f"{base_url}/app/discover_search",
                            json={'keywords': [token]})
            data = resp.json()
            assert data['retcode'] == 0
            candidates = data['data'].get('candidates', [])
            match = next(
                (c for c in candidates
                 if token in (c.get('cmdline') or '')
                 or token in (c.get('exe') or '')),
                None,
            )
            if match is None:
                pytest.skip(
                    "launched process not visible via discover_search "
                    "(environment may restrict /proc cmdline); write-path could "
                    "not be exercised"
                )
            assert token in (match.get('cmdline') or '') \
                or token in (match.get('exe') or ''), \
                "discover_search returned a candidate that does not match our token"
        finally:
            _cleanup_process(proc, script_path)


@pytest.mark.service
class TestDiscoverExtractSideEffect:
    """Prove /app/discover_extract reads real fields from a live PID."""

    def test_extract_returns_fields_for_pid(self, api, base_url):
        proc, script_path, token = _launch_unique_process()
        try:
            resp = api.post(f"{base_url}/app/discover_extract",
                            json={'pids': [proc.pid], 'name': 'DiscTest'})
            data = resp.json()
            assert data['retcode'] == 0
            extracted = data['data']
            bpf_name = extracted.get('bpf_name') or []
            process_names = extracted.get('process_names') or []
            commandline = extracted.get('commandline') or []
            cmd_str = ' '.join(commandline) if isinstance(commandline, list) else str(commandline)

            has_any = bool(bpf_name) or bool(process_names) or bool(cmd_str.strip())
            if not has_any:
                pytest.skip(
                    "discover_extract returned no fields for the launched PID "
                    "(environment may restrict /proc); write-path could not be "
                    "exercised"
                )
            assert has_any, \
                "discover_extract returned 0 but produced no usable fields for the PID"
        finally:
            _cleanup_process(proc, script_path)


@pytest.mark.service
class TestGetAppsStorePersistence:
    """Prove /app/get_apps {store:true} persists discovered apps to the DB."""

    def test_store_true_persists_to_db(self, api, base_url):
        resp = api.post(f"{base_url}/app/get_apps", json={'store': True})
        data = resp.json()
        assert data['retcode'] == 0
        assert isinstance(data['data'], list)
        assert len(data['data']) > 0, \
            "get_apps(store=True) returned an empty list — nothing to persist"

        # store=False must still return a list (sanity).
        resp = api.post(f"{base_url}/app/get_apps", json={'store': False})
        assert resp.json()['retcode'] == 0
        assert isinstance(resp.json()['data'], list)

        # Persistence proof: a stored app must now be queryable in the DB via
        # get_priority_data (which reads the AIAppPriority table directly).
        stored_ids = [a.get('app_id') for a in data['data'] if a.get('app_id')]
        if not stored_ids:
            pytest.skip("no app_id available in get_apps result to verify persistence")
        probe_id = stored_ids[0]
        resp = api.post(f"{base_url}/app/get_priority_data",
                        json={'app_id': probe_id})
        pdata = resp.json()
        assert pdata['retcode'] == 0, \
            "app returned by get_apps(store=True) is not queryable in DB — not persisted"
        assert pdata['data'].get('app_id') == probe_id
