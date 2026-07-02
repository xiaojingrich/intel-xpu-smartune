# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Acceptance tests: Long-Running Stability
Verify service stability over extended periods.
These tests are marked 'stress' and skipped by default (run with -m stress).
"""

import time
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
import requests


def unique_app_id():
    return f"stress_{uuid.uuid4().hex[:8]}"


@pytest.mark.service
@pytest.mark.stress
class TestAPIStability:
    """Verify API remains stable under sustained load."""

    def test_repeated_requests_no_degradation(self, api, base_url):
        """100 sequential requests should all succeed without latency degradation."""
        latencies = []
        failures = 0

        for _ in range(100):
            start = time.monotonic()
            try:
                resp = api.get(f"{base_url}/monitor/dynamic_info")
                elapsed = time.monotonic() - start
                latencies.append(elapsed)
                if resp.status_code != 200:
                    failures += 1
            except Exception:
                failures += 1

        assert failures < 5, f"Too many failures: {failures}/100"

        # Check no significant latency increase over time
        first_quarter = sum(latencies[:25]) / 25
        last_quarter = sum(latencies[75:]) / max(1, len(latencies[75:]))
        # Last quarter shouldn't be more than 3x first quarter
        assert last_quarter < first_quarter * 3 + 1.0

    def test_concurrent_requests(self, base_url):
        """20 concurrent requests should all complete successfully."""
        results = []

        def make_request():
            try:
                resp = requests.get(
                    f"{base_url}/monitor/static_info",
                    verify=False, timeout=30
                )
                return resp.status_code
            except Exception as e:
                return str(e)

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(make_request) for _ in range(20)]
            for future in as_completed(futures):
                results.append(future.result())

        successes = sum(1 for r in results if r == 200)
        assert successes >= 18, f"Only {successes}/20 succeeded: {results}"

    def test_rapid_endpoint_cycling(self, api, base_url):
        """Hit different endpoints in rapid succession."""
        endpoints = [
            ('GET', '/monitor/static_info'),
            ('GET', '/monitor/dynamic_info'),
            ('GET', '/monitor/app_resource_stats'),
            ('GET', '/monitor/processes'),
            ('POST', '/app/get_apps'),
            ('POST', '/app/get_controlled_app'),
            ('POST', '/app/get_pending_app'),
            ('POST', '/app/check_running_apps'),
        ]

        failures = 0
        for _ in range(10):
            for method, path in endpoints:
                try:
                    if method == 'GET':
                        resp = api.get(f"{base_url}{path}")
                    else:
                        resp = api.post(f"{base_url}{path}", json={})
                    if resp.status_code not in (200, 400):
                        failures += 1
                except Exception:
                    failures += 1

        # Allow some failures but service should stay responsive
        total = 10 * len(endpoints)
        assert failures < total * 0.1, f"Too many failures: {failures}/{total}"


@pytest.mark.service
@pytest.mark.stress
class TestSSEStability:
    """Verify SSE connections remain stable."""

    def test_sse_sustained_connection(self, base_url):
        """SSE connection should stay alive for 30 seconds with heartbeats."""
        events_received = []
        start = time.monotonic()

        try:
            with requests.get(f"{base_url}/app/events", stream=True,
                             verify=False, timeout=35) as resp:
                assert resp.status_code == 200
                for line in resp.iter_lines(decode_unicode=True):
                    elapsed = time.monotonic() - start
                    if elapsed > 30:
                        break
                    if line.startswith('data:'):
                        events_received.append(line)
                    elif line.startswith(':'):
                        events_received.append('heartbeat')
        except requests.exceptions.ReadTimeout:
            pass

        # Should get at least the connected event
        assert len(events_received) >= 1

    def test_multiple_sse_clients(self, base_url):
        """Multiple simultaneous SSE connections should all work."""
        results = []

        def connect_sse(client_id):
            try:
                with requests.get(f"{base_url}/app/events", stream=True,
                                 verify=False, timeout=10) as resp:
                    if resp.status_code != 200:
                        return False
                    for line in resp.iter_lines(decode_unicode=True):
                        if line.startswith('data:'):
                            data = json.loads(line[5:].strip())
                            return data.get('type') == 'connected'
                    return False
            except Exception:
                return False

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(connect_sse, i) for i in range(10)]
            for future in as_completed(futures, timeout=15):
                results.append(future.result())

        successes = sum(1 for r in results if r is True)
        assert successes >= 8, f"Only {successes}/10 SSE clients connected"


@pytest.mark.service
@pytest.mark.stress
class TestMemoryStability:
    """Verify service doesn't leak memory over time."""

    def _get_service_rss_kb(self):
        """Get SmartTune service RSS memory in KB."""
        import subprocess
        result = subprocess.run(
            ['pgrep', '-f', 'BalanceService'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None
        pid = result.stdout.strip().split('\n')[0]
        try:
            with open(f'/proc/{pid}/status') as f:
                for line in f:
                    if line.startswith('VmRSS:'):
                        return int(line.split()[1])
        except (FileNotFoundError, ValueError):
            return None
        return None

    def test_no_memory_leak_under_load(self, api, base_url):
        """RSS should not grow significantly after 200 requests."""
        rss_before = self._get_service_rss_kb()
        if rss_before is None:
            pytest.skip("Cannot determine service PID/RSS")

        # Generate load
        for _ in range(200):
            api.get(f"{base_url}/monitor/dynamic_info")
            api.post(f"{base_url}/app/get_apps", json={'store': False})

        # Allow GC
        time.sleep(2)

        rss_after = self._get_service_rss_kb()
        if rss_after is None:
            pytest.skip("Cannot read RSS after load")

        growth_kb = rss_after - rss_before
        growth_pct = (growth_kb / rss_before) * 100

        # RSS should not grow more than 20% from 200 requests
        assert growth_pct < 20, \
            f"Memory grew {growth_pct:.1f}% ({growth_kb}KB): {rss_before}KB -> {rss_after}KB"


@pytest.mark.service
@pytest.mark.stress
class TestDatabaseStability:
    """Verify database operations remain stable under load."""

    def test_bulk_app_registration_and_cleanup(self, api, base_url):
        """Register 50 apps, verify all present, then remove all."""
        app_ids = [unique_app_id() for _ in range(50)]

        # Register all
        for app_id in app_ids:
            resp = api.post(f"{base_url}/app/set_to_control", json={
                'app_id': app_id,
                'app_name': f'StressApp_{app_id[-6:]}',
                'controlled': True,
                'priority': 'medium',
                'cmdline': f'stress_cmd_{app_id[-6:]}',
            })
            assert resp.json()['retcode'] == 0

        # Verify all present
        resp = api.post(f"{base_url}/app/get_controlled_app", json={})
        controlled = resp.json()['data']
        controlled_ids = {a.get('app_id', a.get('id', '')) for a in controlled}
        registered = sum(1 for aid in app_ids if aid in controlled_ids)
        assert registered == 50

        # Remove all
        for app_id in app_ids:
            api.post(f"{base_url}/app/remove_from_control",
                    json={'app_id': app_id, 'app_name': f'StressApp_{app_id[-6:]}'})

        # Verify cleaned
        resp = api.post(f"{base_url}/app/get_controlled_app", json={})
        controlled = resp.json()['data']
        controlled_ids = {a.get('app_id', a.get('id', '')) for a in controlled}
        remaining = sum(1 for aid in app_ids if aid in controlled_ids)
        assert remaining == 0

    def test_concurrent_priority_updates(self, api, base_url):
        """Concurrent priority updates to different apps should not corrupt."""
        app_ids = [unique_app_id() for _ in range(10)]

        # Register
        for app_id in app_ids:
            api.post(f"{base_url}/app/set_to_control", json={
                'app_id': app_id,
                'app_name': f'ConcApp_{app_id[-6:]}',
                'controlled': True,
                'priority': 'medium',
                'cmdline': f'conc_cmd_{app_id[-6:]}',
            })

        # Concurrent updates
        errors = []

        def update_priority(app_id, priority):
            try:
                resp = requests.post(
                    f"{base_url}/app/set_priority",
                    json={'app_id': app_id, 'priority': priority},
                    verify=False, timeout=10
                )
                if resp.json()['retcode'] != 0:
                    errors.append(f"{app_id}: {resp.json()['retmsg']}")
            except Exception as e:
                errors.append(f"{app_id}: {e}")

        labels = ['low', 'medium', 'high', 'critical']
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = []
            for i, app_id in enumerate(app_ids):
                futures.append(pool.submit(update_priority, app_id,
                                           labels[i % len(labels)]))
            for f in as_completed(futures):
                f.result()

        # Cleanup
        for app_id in app_ids:
            api.post(f"{base_url}/app/remove_from_control",
                    json={'app_id': app_id, 'app_name': f'ConcApp_{app_id[-6:]}'})

        assert len(errors) == 0, f"Concurrent update errors: {errors}"


@pytest.mark.service
@pytest.mark.stress
class TestServiceRecovery:
    """Verify service handles error conditions gracefully."""

    def test_malformed_json(self, base_url):
        """Service should handle malformed JSON gracefully."""
        resp = requests.post(
            f"{base_url}/app/set_priority",
            data='{"broken json',
            headers={'Content-Type': 'application/json'},
            verify=False, timeout=10
        )
        # Should not crash - either 400 or 200 with error retcode
        assert resp.status_code in (200, 400, 415, 500)

    def test_oversized_payload(self, base_url):
        """Service should handle oversized payloads."""
        large_payload = {'data': 'x' * 100000}
        resp = requests.post(
            f"{base_url}/app/set_to_control",
            json=large_payload,
            verify=False, timeout=10
        )
        # Should not crash
        assert resp.status_code in (200, 400, 413, 500)

        # Service should still be responsive after
        resp = requests.get(
            f"{base_url}/monitor/static_info",
            verify=False, timeout=10
        )
        assert resp.status_code == 200

    def test_rapid_connect_disconnect(self, base_url):
        """Rapidly opening and closing connections should not exhaust resources."""
        for _ in range(50):
            try:
                resp = requests.get(
                    f"{base_url}/monitor/static_info",
                    verify=False, timeout=5
                )
                assert resp.status_code == 200
            except Exception:
                pass

        # Service should still work after rapid connect/disconnect
        time.sleep(1)
        resp = requests.get(
            f"{base_url}/monitor/static_info",
            verify=False, timeout=10
        )
        assert resp.status_code == 200
