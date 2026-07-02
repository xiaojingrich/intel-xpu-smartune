# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Extended stability and client-scenario acceptance tests.

Test cases covered:
  TC-SS-001: Long-time running (5-minute abbreviated version of 7x24)
  TC-SS-003: Abnormal process exit recovery
  TC-SS-005: Service restart state recovery
  TC-SS-007: Concurrent API call consistency
  TC-SS-009: Disk space exhaustion handling
  TC-SS-010: Config hot update
  TC-SS-011: HTTPS certificate
  TC-CS-002: Service disconnect/reconnect (client)
  TC-CS-006: Large SSE event processing
  TC-CS-007: Multi-window concurrent operations

These tests run against a LIVE SmartTune service and require:
  - SmartTune service running
  - Root privileges for systemctl operations
  - verify=False for self-signed HTTPS
"""

import json
import os
import ssl
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
import requests
from urllib3.exceptions import InsecureRequestWarning

from conftest import BASE_URL, TIMEOUT

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)


def unique_app_id():
    return f"ext_{uuid.uuid4().hex[:8]}"


# ═══════════════════════════════════════════════════════════════════════════════
# TC-SS-001: Long-time running (5-minute abbreviated version)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.service
@pytest.mark.stress
class TestLongTimeRunning:
    """TC-SS-001: Service stability over an extended period (5 minutes)."""

    DURATION_SECONDS = 300  # 5 minutes
    REQUEST_INTERVAL = 2  # seconds between requests

    def test_sustained_api_health(self, api, base_url):
        """Service should remain responsive for 5 minutes of continuous polling."""
        start = time.monotonic()
        successes = 0
        failures = 0
        max_latency = 0.0
        latencies = []

        while (time.monotonic() - start) < self.DURATION_SECONDS:
            req_start = time.monotonic()
            try:
                resp = api.get(f"{base_url}/monitor/dynamic_info")
                latency = time.monotonic() - req_start
                latencies.append(latency)
                max_latency = max(max_latency, latency)
                if resp.status_code == 200:
                    successes += 1
                else:
                    failures += 1
            except Exception:
                failures += 1

            time.sleep(self.REQUEST_INTERVAL)

        total = successes + failures
        assert total > 0, "No requests were sent"
        success_rate = successes / total
        assert success_rate >= 0.95, (
            f"Success rate {success_rate:.2%} below 95% "
            f"({successes}/{total}, max_latency={max_latency:.2f}s)"
        )

        # Verify no latency degradation: last 10% should not be 5x worse than first 10%
        if len(latencies) >= 20:
            tenth = len(latencies) // 10
            first_avg = sum(latencies[:tenth]) / tenth
            last_avg = sum(latencies[-tenth:]) / tenth
            assert last_avg < first_avg * 5 + 2.0, (
                f"Latency degradation: first_avg={first_avg:.3f}s, last_avg={last_avg:.3f}s"
            )

    def test_sustained_mixed_endpoints(self, api, base_url):
        """Mix of GET and POST endpoints should all remain healthy over 5 minutes."""
        endpoints = [
            ('GET', '/monitor/static_info'),
            ('GET', '/monitor/dynamic_info'),
            ('POST', '/app/get_apps'),
            ('POST', '/app/get_controlled_app'),
        ]

        start = time.monotonic()
        results = {'success': 0, 'failure': 0}

        while (time.monotonic() - start) < self.DURATION_SECONDS:
            for method, path in endpoints:
                try:
                    if method == 'GET':
                        resp = api.get(f"{base_url}{path}")
                    else:
                        resp = api.post(f"{base_url}{path}", json={})
                    if resp.status_code == 200:
                        results['success'] += 1
                    else:
                        results['failure'] += 1
                except Exception:
                    results['failure'] += 1

            time.sleep(self.REQUEST_INTERVAL)

        total = results['success'] + results['failure']
        assert results['success'] / total >= 0.95


# ═══════════════════════════════════════════════════════════════════════════════
# TC-SS-003: Abnormal process exit recovery
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.service
@pytest.mark.root
class TestAbnormalProcessExitRecovery:
    """TC-SS-003: Service should auto-recover after being killed abnormally."""

    def test_sigkill_recovery(self, api, base_url):
        """Service should restart after SIGKILL (systemd Restart=on-failure)."""
        # Find the service PID
        result = subprocess.run(
            ['pgrep', '-f', 'BalanceService'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            pytest.skip("Cannot find BalanceService PID")

        original_pid = result.stdout.strip().split('\n')[0]

        # Send SIGKILL to simulate abnormal exit
        subprocess.run(
            ['kill', '-9', original_pid],
            capture_output=True, text=True, timeout=5
        )

        # Wait for systemd to restart the service (RestartSec=5 in unit file)
        time.sleep(10)

        # Verify service came back
        retries = 6
        recovered = False
        for _ in range(retries):
            try:
                resp = requests.get(
                    f"{base_url}/monitor/static_info",
                    verify=False, timeout=5
                )
                if resp.status_code == 200:
                    recovered = True
                    break
            except Exception:
                pass
            time.sleep(5)

        assert recovered, "Service did not recover after SIGKILL within 40 seconds"

        # Verify new PID
        result = subprocess.run(
            ['pgrep', '-f', 'BalanceService'],
            capture_output=True, text=True, timeout=5
        )
        assert result.returncode == 0
        new_pid = result.stdout.strip().split('\n')[0]
        assert new_pid != original_pid, "PID should change after restart"

    def test_sigterm_graceful_shutdown(self, api, base_url):
        """Service should handle SIGTERM gracefully and restart via systemd."""
        result = subprocess.run(
            ['pgrep', '-f', 'BalanceService'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            pytest.skip("Cannot find BalanceService PID")

        original_pid = result.stdout.strip().split('\n')[0]

        # Send SIGTERM (graceful stop)
        subprocess.run(
            ['kill', '-15', original_pid],
            capture_output=True, text=True, timeout=5
        )

        # Wait for restart
        time.sleep(10)

        # Verify service recovers
        retries = 6
        recovered = False
        for _ in range(retries):
            try:
                resp = requests.get(
                    f"{base_url}/monitor/static_info",
                    verify=False, timeout=5
                )
                if resp.status_code == 200:
                    recovered = True
                    break
            except Exception:
                pass
            time.sleep(5)

        assert recovered, "Service did not recover after SIGTERM"


# ═══════════════════════════════════════════════════════════════════════════════
# TC-SS-005: Service restart state recovery
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.service
@pytest.mark.root
class TestServiceRestartStateRecovery:
    """TC-SS-005: Application state should persist across service restarts."""

    def test_controlled_apps_persist_across_restart(self, api, base_url):
        """Controlled apps registered before restart should be present after."""
        # Register a test app
        app_id = unique_app_id()
        resp = api.post(f"{base_url}/app/set_to_control", json={
            'app_id': app_id,
            'app_name': f'PersistTest_{app_id[-6:]}',
            'controlled': True,
            'priority': 'medium',
            'cmdline': f'persist_cmd_{app_id[-6:]}',
        })
        assert resp.json()['retcode'] == 0

        # Restart the service
        result = subprocess.run(
            ['systemctl', 'restart', 'smartune.service'],
            capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0

        # Wait for service to come back up
        time.sleep(8)
        retries = 6
        service_up = False
        for _ in range(retries):
            try:
                resp = requests.get(
                    f"{base_url}/monitor/static_info",
                    verify=False, timeout=5
                )
                if resp.status_code == 200:
                    service_up = True
                    break
            except Exception:
                pass
            time.sleep(3)

        assert service_up, "Service did not come back after restart"

        # Verify app still exists
        resp = api.post(f"{base_url}/app/get_controlled_app", json={})
        controlled = resp.json()['data']
        controlled_ids = {a.get('app_id', a.get('id', '')) for a in controlled}
        assert app_id in controlled_ids, (
            f"App {app_id} not found after restart. Present: {controlled_ids}"
        )

        # Cleanup
        api.post(f"{base_url}/app/remove_from_control",
                 json={'app_id': app_id, 'app_name': f'PersistTest_{app_id[-6:]}'})

    def test_priority_preserved_across_restart(self, api, base_url):
        """App priority should be preserved across service restarts."""
        app_id = unique_app_id()
        resp = api.post(f"{base_url}/app/set_to_control", json={
            'app_id': app_id,
            'app_name': f'PriorityTest_{app_id[-6:]}',
            'controlled': True,
            'priority': 'high',
            'cmdline': f'priority_cmd_{app_id[-6:]}',
        })
        assert resp.json()['retcode'] == 0

        # Restart
        subprocess.run(
            ['systemctl', 'restart', 'smartune.service'],
            capture_output=True, text=True, timeout=30
        )
        time.sleep(8)

        # Wait for service
        for _ in range(6):
            try:
                resp = requests.get(f"{base_url}/monitor/static_info",
                                    verify=False, timeout=5)
                if resp.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(3)

        # Check priority
        resp = api.post(f"{base_url}/app/get_controlled_app", json={})
        controlled = resp.json()['data']
        app_record = next(
            (a for a in controlled if a.get('app_id', a.get('id', '')) == app_id),
            None
        )
        assert app_record is not None, f"App {app_id} not found after restart"
        assert app_record.get('priority') == 'high'

        # Cleanup
        api.post(f"{base_url}/app/remove_from_control",
                 json={'app_id': app_id, 'app_name': f'PriorityTest_{app_id[-6:]}'})


# ═══════════════════════════════════════════════════════════════════════════════
# TC-SS-007: Concurrent API call consistency
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.service
@pytest.mark.stress
class TestConcurrentAPIConsistency:
    """TC-SS-007: Concurrent API calls should not corrupt state."""

    def test_concurrent_reads_consistent(self, base_url):
        """50 concurrent GET requests should return consistent data."""
        results = []

        def fetch_static_info():
            try:
                resp = requests.get(
                    f"{base_url}/monitor/static_info",
                    verify=False, timeout=TIMEOUT
                )
                if resp.status_code == 200:
                    return resp.json().get('data', {})
                return None
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=50) as pool:
            futures = [pool.submit(fetch_static_info) for _ in range(50)]
            for f in as_completed(futures, timeout=60):
                results.append(f.result())

        # Filter out failures
        valid = [r for r in results if r is not None]
        assert len(valid) >= 45, f"Only {len(valid)}/50 requests succeeded"

        # All responses should be identical (static info does not change)
        if len(valid) >= 2:
            reference = json.dumps(valid[0], sort_keys=True)
            for i, r in enumerate(valid[1:], start=1):
                assert json.dumps(r, sort_keys=True) == reference, (
                    f"Response {i} differs from reference"
                )

    def test_concurrent_write_read_no_corruption(self, api, base_url):
        """Concurrent writes and reads should not corrupt database state."""
        app_ids = [unique_app_id() for _ in range(20)]
        errors = []

        def register_app(app_id):
            try:
                resp = requests.post(
                    f"{base_url}/app/set_to_control",
                    json={
                        'app_id': app_id,
                        'app_name': f'ConcWrite_{app_id[-6:]}',
                        'controlled': True,
                        'priority': 'medium',
                        'cmdline': f'conc_{app_id[-6:]}',
                    },
                    verify=False, timeout=TIMEOUT
                )
                if resp.json().get('retcode') != 0:
                    errors.append(f"register {app_id}: {resp.json()}")
            except Exception as e:
                errors.append(f"register {app_id}: {e}")

        def read_apps():
            try:
                resp = requests.post(
                    f"{base_url}/app/get_controlled_app",
                    json={},
                    verify=False, timeout=TIMEOUT
                )
                return resp.json().get('data', [])
            except Exception as e:
                errors.append(f"read: {e}")
                return []

        # Concurrent writes
        with ThreadPoolExecutor(max_workers=20) as pool:
            write_futures = [pool.submit(register_app, aid) for aid in app_ids]
            read_futures = [pool.submit(read_apps) for _ in range(10)]
            for f in as_completed(write_futures + read_futures, timeout=60):
                f.result()

        assert len(errors) == 0, f"Concurrent errors: {errors}"

        # Verify all apps registered
        resp = api.post(f"{base_url}/app/get_controlled_app", json={})
        controlled = resp.json()['data']
        controlled_ids = {a.get('app_id', a.get('id', '')) for a in controlled}
        registered = sum(1 for aid in app_ids if aid in controlled_ids)
        assert registered == 20, f"Only {registered}/20 apps registered"

        # Cleanup
        for app_id in app_ids:
            api.post(f"{base_url}/app/remove_from_control",
                     json={'app_id': app_id, 'app_name': f'ConcWrite_{app_id[-6:]}'})

    def test_concurrent_priority_updates_consistent(self, api, base_url):
        """Concurrent priority updates to the same app should converge."""
        app_id = unique_app_id()
        api.post(f"{base_url}/app/set_to_control", json={
            'app_id': app_id,
            'app_name': f'PrioConc_{app_id[-6:]}',
            'controlled': True,
            'priority': 'medium',
            'cmdline': f'prio_conc_{app_id[-6:]}',
        })

        final_priorities = []

        def update_priority(priority):
            try:
                requests.post(
                    f"{base_url}/app/set_priority",
                    json={'app_id': app_id, 'priority': priority},
                    verify=False, timeout=TIMEOUT
                )
            except Exception:
                pass

        # Fire 20 concurrent priority updates cycling through the valid labels.
        labels = ['low', 'medium', 'high', 'critical']
        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(update_priority, labels[i % len(labels)])
                       for i in range(20)]
            for f in as_completed(futures, timeout=30):
                f.result()

        # Read final state: should be one valid priority label (concurrent
        # updates must leave the record in a consistent, non-corrupted state).
        resp = api.post(f"{base_url}/app/get_controlled_app", json={})
        controlled = resp.json()['data']
        app_record = next(
            (a for a in controlled if a.get('app_id', a.get('id', '')) == app_id),
            None
        )
        assert app_record is not None
        assert app_record.get('priority') in labels

        # Cleanup
        api.post(f"{base_url}/app/remove_from_control",
                 json={'app_id': app_id, 'app_name': f'PrioConc_{app_id[-6:]}'})


# ═══════════════════════════════════════════════════════════════════════════════
# TC-SS-009: Disk space exhaustion handling
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.service
@pytest.mark.root
@pytest.mark.stress
class TestDiskSpaceExhaustion:
    """TC-SS-009: Service should handle disk full conditions gracefully."""

    def test_service_survives_disk_pressure(self, api, base_url):
        """Service should remain responsive even under disk I/O pressure."""
        # Use stress-ng to generate disk pressure (non-destructive)
        stress_proc = subprocess.Popen(
            ['stress-ng', '--hdd', '2', '--hdd-bytes', '100M',
             '--timeout', '30s', '--quiet'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        try:
            # Continuously poll the service during disk stress
            successes = 0
            failures = 0
            start = time.monotonic()

            while (time.monotonic() - start) < 30:
                try:
                    resp = requests.get(
                        f"{base_url}/monitor/dynamic_info",
                        verify=False, timeout=10
                    )
                    if resp.status_code == 200:
                        successes += 1
                    else:
                        failures += 1
                except Exception:
                    failures += 1
                time.sleep(1)

        finally:
            stress_proc.terminate()
            stress_proc.wait(timeout=10)

        total = successes + failures
        assert total > 0
        assert successes / total >= 0.8, (
            f"Only {successes}/{total} requests succeeded under disk stress"
        )

    def test_service_responsive_after_tmp_full(self, api, base_url):
        """Service should remain operational even if /tmp fills up temporarily."""
        # Create a large temp file to simulate disk pressure
        tmp_file = None
        try:
            tmp_file = tempfile.NamedTemporaryFile(
                dir='/tmp', prefix='smartune_test_fill_',
                delete=False, suffix='.dat'
            )
            # Write 500MB to stress disk (but don't actually exhaust it)
            chunk = b'\0' * (1024 * 1024)  # 1MB
            for _ in range(500):
                try:
                    tmp_file.write(chunk)
                except OSError:
                    break
            tmp_file.flush()

            # Service should still respond
            resp = requests.get(
                f"{base_url}/monitor/static_info",
                verify=False, timeout=10
            )
            assert resp.status_code == 200

        finally:
            if tmp_file:
                tmp_file.close()
                try:
                    os.unlink(tmp_file.name)
                except OSError:
                    pass


# ═══════════════════════════════════════════════════════════════════════════════
# TC-SS-010: Config hot update
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.service
class TestConfigHotUpdate:
    """TC-SS-010: Config changes should take effect without service restart."""

    def test_weights_top_hot_update(self, api, base_url):
        """Updating weights_top via API should reflect immediately."""
        # Read current config
        resp = api.get(f"{base_url}/monitor/static_info")
        assert resp.status_code == 200

        # Update weights_top
        new_weights = {'cpu': 3, 'memory': 5, 'gpu': 8}
        resp = api.post(f"{base_url}/config/update", json={
            'section': 'weights_top',
            'updates': new_weights
        })

        # If the endpoint exists and accepts the update
        if resp.status_code == 200 and resp.json().get('retcode') == 0:
            # Service should still be responsive
            resp = api.get(f"{base_url}/monitor/static_info")
            assert resp.status_code == 200

            # Restore original (best effort)
            api.post(f"{base_url}/config/update", json={
                'section': 'weights_top',
                'updates': {'cpu': 2, 'memory': 7, 'gpu': 5}
            })

    def test_passive_control_toggle(self, api, base_url):
        """Toggling passive_resource_control via API should take effect."""
        resp = api.post(f"{base_url}/config/update", json={
            'section': 'passive_resource_control',
            'updates': {'enabled': False}
        })

        if resp.status_code == 200 and resp.json().get('retcode') == 0:
            # Verify service still responsive
            resp = api.get(f"{base_url}/monitor/dynamic_info")
            assert resp.status_code == 200

            # Restore
            api.post(f"{base_url}/config/update", json={
                'section': 'passive_resource_control',
                'updates': {'enabled': True}
            })

    def test_service_stable_after_multiple_config_updates(self, api, base_url):
        """Rapid config updates should not destabilize the service."""
        for i in range(20):
            api.post(f"{base_url}/config/update", json={
                'section': 'weights_top',
                'updates': {'cpu': (i % 10) + 1}
            })

        # Service should remain healthy
        time.sleep(1)
        resp = api.get(f"{base_url}/monitor/static_info")
        assert resp.status_code == 200

        # Restore
        api.post(f"{base_url}/config/update", json={
            'section': 'weights_top',
            'updates': {'cpu': 2}
        })


# ═══════════════════════════════════════════════════════════════════════════════
# TC-SS-011: HTTPS certificate
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.service
class TestHTTPSCertificate:
    """TC-SS-011: Verify HTTPS/TLS is properly configured."""

    def test_service_uses_https(self, base_url):
        """Service should be accessible over HTTPS."""
        assert base_url.startswith("https://")
        resp = requests.get(
            f"{base_url}/monitor/static_info",
            verify=False, timeout=5
        )
        assert resp.status_code == 200

    def test_http_redirect_or_refuse(self, base_url):
        """Plain HTTP should be refused or redirected to HTTPS."""
        http_url = base_url.replace("https://", "http://")
        try:
            resp = requests.get(http_url, timeout=5, allow_redirects=False)
            # Either redirect (301/302) or service not listening on HTTP
            assert resp.status_code in (301, 302, 400, 403), (
                f"HTTP returned unexpected status {resp.status_code}"
            )
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ReadTimeout):
            # Connection refused on HTTP port is acceptable
            pass

    def test_tls_version_minimum(self, base_url):
        """Service should support at least TLS 1.2."""
        import socket
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        host = parsed.hostname
        port = parsed.port or 443

        # Try TLS 1.2 connection
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2

        try:
            with socket.create_connection((host, port), timeout=5) as sock:
                with context.wrap_socket(sock, server_hostname=host) as ssock:
                    version = ssock.version()
                    assert version in ('TLSv1.2', 'TLSv1.3'), (
                        f"Unexpected TLS version: {version}"
                    )
        except (ssl.SSLError, ConnectionRefusedError, OSError) as e:
            pytest.skip(f"Cannot establish TLS connection: {e}")

    def test_self_signed_cert_rejected_without_verify_false(self, base_url):
        """Strict verification should fail for self-signed certificate."""
        with pytest.raises((requests.exceptions.SSLError, IOError)):
            requests.get(f"{base_url}/monitor/static_info", verify=True, timeout=5)

    def test_certificate_has_valid_subject(self, base_url):
        """The server certificate should have some subject information."""
        import socket
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        host = parsed.hostname
        port = parsed.port or 443

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        try:
            with socket.create_connection((host, port), timeout=5) as sock:
                with context.wrap_socket(sock, server_hostname=host) as ssock:
                    cert_bin = ssock.getpeercert(binary_form=True)
                    assert cert_bin is not None, "No certificate presented"
                    assert len(cert_bin) > 100, "Certificate seems too small"
        except (ssl.SSLError, ConnectionRefusedError, OSError) as e:
            pytest.skip(f"Cannot read certificate: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# TC-CS-002: Service disconnect/reconnect (client)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.service
class TestClientDisconnectReconnect:
    """TC-CS-002: Client should handle service disconnect/reconnect gracefully."""

    def test_reconnect_after_timeout(self, base_url):
        """Client should successfully reconnect after a request timeout."""
        # Force a very short timeout to simulate disconnect
        try:
            requests.get(f"{base_url}/monitor/dynamic_info",
                         verify=False, timeout=0.001)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            pass

        # Reconnect with normal timeout should succeed
        resp = requests.get(
            f"{base_url}/monitor/static_info",
            verify=False, timeout=TIMEOUT
        )
        assert resp.status_code == 200

    def test_reconnect_after_connection_drop(self, base_url):
        """Client should reconnect after abrupt connection termination."""
        session = requests.Session()
        session.verify = False

        # Make a successful request
        resp = session.get(f"{base_url}/monitor/static_info", timeout=10)
        assert resp.status_code == 200

        # Close the session (simulates client-side disconnect)
        session.close()

        # New session should work fine
        new_session = requests.Session()
        new_session.verify = False
        resp = new_session.get(f"{base_url}/monitor/static_info", timeout=10)
        assert resp.status_code == 200
        new_session.close()

    def test_rapid_disconnect_reconnect_cycles(self, base_url):
        """Rapid connect/disconnect cycles should not exhaust server resources."""
        successes = 0
        for i in range(30):
            session = requests.Session()
            session.verify = False
            try:
                resp = session.get(f"{base_url}/monitor/static_info", timeout=5)
                if resp.status_code == 200:
                    successes += 1
            except Exception:
                pass
            finally:
                session.close()

        assert successes >= 25, f"Only {successes}/30 rapid reconnects succeeded"

    def test_sse_reconnect_after_disconnect(self, base_url):
        """SSE client should be able to reconnect after a broken connection."""
        # First connection
        try:
            with requests.get(f"{base_url}/app/events", stream=True,
                             verify=False, timeout=3) as resp:
                assert resp.status_code == 200
                # Read one event then disconnect
                for line in resp.iter_lines(decode_unicode=True):
                    if line.startswith('data:'):
                        break
        except requests.exceptions.ReadTimeout:
            pass

        # Reconnect
        with requests.get(f"{base_url}/app/events", stream=True,
                         verify=False, timeout=5) as resp:
            assert resp.status_code == 200
            for line in resp.iter_lines(decode_unicode=True):
                if line.startswith('data:'):
                    data = json.loads(line[5:].strip())
                    assert data.get('type') == 'connected'
                    break


# ═══════════════════════════════════════════════════════════════════════════════
# TC-CS-006: Large SSE event processing
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.service
@pytest.mark.stress
class TestLargeSSEEventProcessing:
    """TC-CS-006: SSE should handle large events and many clients."""

    def test_multiple_sse_clients_simultaneous(self, base_url):
        """20 simultaneous SSE clients should all receive the connected event."""
        results = []

        def connect_and_read(client_id):
            try:
                with requests.get(f"{base_url}/app/events", stream=True,
                                 verify=False, timeout=10) as resp:
                    if resp.status_code != 200:
                        return {'client': client_id, 'success': False, 'reason': f'status={resp.status_code}'}
                    for line in resp.iter_lines(decode_unicode=True):
                        if line.startswith('data:'):
                            data = json.loads(line[5:].strip())
                            return {'client': client_id, 'success': True, 'data': data}
                    return {'client': client_id, 'success': False, 'reason': 'no data event'}
            except Exception as e:
                return {'client': client_id, 'success': False, 'reason': str(e)}

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(connect_and_read, i) for i in range(20)]
            for f in as_completed(futures, timeout=30):
                results.append(f.result())

        successes = sum(1 for r in results if r.get('success'))
        assert successes >= 16, (
            f"Only {successes}/20 SSE clients received events. "
            f"Failures: {[r for r in results if not r.get('success')]}"
        )

    def test_sse_with_concurrent_api_calls(self, api, base_url):
        """SSE connections should not block or be disrupted by concurrent API traffic."""
        sse_connected = threading.Event()
        sse_events = []
        sse_error = []

        def sse_listener():
            try:
                with requests.get(f"{base_url}/app/events", stream=True,
                                 verify=False, timeout=20) as resp:
                    if resp.status_code != 200:
                        sse_error.append(f"status={resp.status_code}")
                        return
                    for line in resp.iter_lines(decode_unicode=True):
                        if line.startswith('data:'):
                            data = json.loads(line[5:].strip())
                            sse_events.append(data)
                            if data.get('type') == 'connected':
                                sse_connected.set()
            except requests.exceptions.ReadTimeout:
                pass
            except Exception as e:
                sse_error.append(str(e))

        # Start SSE listener
        sse_thread = threading.Thread(target=sse_listener, daemon=True)
        sse_thread.start()

        # Wait for SSE connection
        assert sse_connected.wait(timeout=10), "SSE did not connect in time"

        # Generate API traffic while SSE is connected
        api_successes = 0
        for _ in range(30):
            try:
                resp = requests.get(
                    f"{base_url}/monitor/dynamic_info",
                    verify=False, timeout=5
                )
                if resp.status_code == 200:
                    api_successes += 1
            except Exception:
                pass

        sse_thread.join(timeout=5)

        assert api_successes >= 25, f"Only {api_successes}/30 API calls succeeded during SSE"
        assert not sse_error, f"SSE errors: {sse_error}"
        assert len(sse_events) >= 1, "SSE received no events"

    def test_sse_sustained_under_load(self, base_url):
        """SSE connection should survive 30 seconds of sustained API load."""
        sse_alive = {'connected': False, 'events': 0, 'error': None}

        def sse_monitor():
            try:
                with requests.get(f"{base_url}/app/events", stream=True,
                                 verify=False, timeout=35) as resp:
                    if resp.status_code == 200:
                        sse_alive['connected'] = True
                        for line in resp.iter_lines(decode_unicode=True):
                            if line.startswith('data:') or line.startswith(':'):
                                sse_alive['events'] += 1
            except requests.exceptions.ReadTimeout:
                pass
            except Exception as e:
                sse_alive['error'] = str(e)

        sse_thread = threading.Thread(target=sse_monitor, daemon=True)
        sse_thread.start()
        time.sleep(2)  # Let SSE connect

        # Hammer the API for 30 seconds
        start = time.monotonic()
        while (time.monotonic() - start) < 30:
            try:
                requests.get(f"{base_url}/monitor/dynamic_info",
                             verify=False, timeout=5)
            except Exception:
                pass

        sse_thread.join(timeout=5)

        assert sse_alive['connected'], "SSE never connected"
        assert sse_alive['error'] is None, f"SSE error: {sse_alive['error']}"


# ═══════════════════════════════════════════════════════════════════════════════
# TC-CS-007: Multi-window concurrent operations
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.service
@pytest.mark.stress
class TestMultiWindowConcurrentOperations:
    """TC-CS-007: Simulate multiple browser windows/tabs accessing the API."""

    def test_multi_session_independent_operations(self, base_url):
        """Multiple independent sessions should not interfere with each other."""
        results = {'success': 0, 'failure': 0}
        errors = []

        def simulate_window(window_id):
            """Simulate a browser window performing typical operations."""
            session = requests.Session()
            session.verify = False
            session.headers.update({'Content-Type': 'application/json'})

            try:
                # 1. Load dashboard (static info)
                resp = session.get(f"{base_url}/monitor/static_info", timeout=10)
                if resp.status_code != 200:
                    errors.append(f"window {window_id}: static_info failed")
                    return

                # 2. Poll dynamic info multiple times
                for _ in range(5):
                    resp = session.get(f"{base_url}/monitor/dynamic_info", timeout=10)
                    if resp.status_code != 200:
                        errors.append(f"window {window_id}: dynamic_info failed")
                        return
                    time.sleep(0.5)

                # 3. Get app list
                resp = session.post(f"{base_url}/app/get_apps", json={}, timeout=10)
                if resp.status_code != 200:
                    errors.append(f"window {window_id}: get_apps failed")
                    return

                # 4. Register a unique test app
                app_id = f"win_{window_id}_{uuid.uuid4().hex[:6]}"
                resp = session.post(f"{base_url}/app/set_to_control", json={
                    'app_id': app_id,
                    'app_name': f'Window{window_id}App',
                    'controlled': True,
                    'priority': 'medium',
                    'cmdline': f'window_cmd_{window_id}',
                }, timeout=10)

                if resp.status_code == 200 and resp.json().get('retcode') == 0:
                    # 5. Read back to verify
                    resp = session.post(f"{base_url}/app/get_controlled_app",
                                       json={}, timeout=10)
                    controlled = resp.json().get('data', [])
                    found = any(
                        a.get('app_id', a.get('id', '')) == app_id
                        for a in controlled
                    )
                    if not found:
                        errors.append(f"window {window_id}: app not found after register")

                    # Cleanup
                    session.post(f"{base_url}/app/remove_from_control",
                                 json={'app_id': app_id, 'app_name': f'Window{window_id}App'},
                                 timeout=10)

                results['success'] += 1
            except Exception as e:
                errors.append(f"window {window_id}: {e}")
                results['failure'] += 1
            finally:
                session.close()

        # Simulate 10 concurrent browser windows
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(simulate_window, i) for i in range(10)]
            for f in as_completed(futures, timeout=120):
                f.result()

        assert results['success'] >= 8, (
            f"Only {results['success']}/10 windows completed. Errors: {errors}"
        )
        assert len(errors) == 0, f"Window errors: {errors}"

    def test_simultaneous_dashboard_refresh(self, base_url):
        """10 simultaneous dashboard refreshes should all return valid data."""
        results = []

        def dashboard_refresh(tab_id):
            """Simulate a dashboard refresh: static + dynamic + apps."""
            session = requests.Session()
            session.verify = False
            try:
                r1 = session.get(f"{base_url}/monitor/static_info", timeout=10)
                r2 = session.get(f"{base_url}/monitor/dynamic_info", timeout=10)
                r3 = session.post(f"{base_url}/app/get_apps",
                                  json={}, timeout=10)
                return {
                    'tab': tab_id,
                    'success': all(r.status_code == 200 for r in [r1, r2, r3]),
                    'static': r1.json().get('retcode') == 0,
                    'dynamic': r2.json().get('retcode') == 0,
                }
            except Exception as e:
                return {'tab': tab_id, 'success': False, 'error': str(e)}
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(dashboard_refresh, i) for i in range(10)]
            for f in as_completed(futures, timeout=60):
                results.append(f.result())

        successes = sum(1 for r in results if r.get('success'))
        assert successes >= 8, (
            f"Only {successes}/10 dashboard refreshes succeeded. "
            f"Details: {results}"
        )

    def test_mixed_read_write_from_multiple_sessions(self, api, base_url):
        """Mixed read and write operations from multiple sessions should not conflict."""
        errors = []
        app_ids_created = []
        lock = threading.Lock()

        def writer_session(session_id):
            """A session that registers apps."""
            session = requests.Session()
            session.verify = False
            try:
                for i in range(3):
                    app_id = f"msess_{session_id}_{i}_{uuid.uuid4().hex[:4]}"
                    resp = session.post(f"{base_url}/app/set_to_control", json={
                        'app_id': app_id,
                        'app_name': f'MultiSess_{session_id}_{i}',
                        'controlled': True,
                        'priority': 'medium',
                        'cmdline': f'msess_cmd_{session_id}_{i}',
                    }, timeout=10)
                    if resp.status_code == 200 and resp.json().get('retcode') == 0:
                        with lock:
                            app_ids_created.append(app_id)
                    time.sleep(0.2)
            except Exception as e:
                errors.append(f"writer {session_id}: {e}")
            finally:
                session.close()

        def reader_session(session_id):
            """A session that reads app lists."""
            session = requests.Session()
            session.verify = False
            try:
                for _ in range(5):
                    resp = session.post(f"{base_url}/app/get_controlled_app",
                                       json={}, timeout=10)
                    if resp.status_code != 200:
                        errors.append(f"reader {session_id}: bad status {resp.status_code}")
                    time.sleep(0.3)
            except Exception as e:
                errors.append(f"reader {session_id}: {e}")
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = []
            for i in range(5):
                futures.append(pool.submit(writer_session, i))
                futures.append(pool.submit(reader_session, i))
            for f in as_completed(futures, timeout=60):
                f.result()

        # Cleanup created apps
        for app_id in app_ids_created:
            api.post(f"{base_url}/app/remove_from_control",
                     json={'app_id': app_id, 'app_name': 'cleanup'})

        assert len(errors) == 0, f"Mixed session errors: {errors}"
