# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
TC-S-007: Application Startup Queue Mechanism

Verifies:
- Under critical pressure, launching a monitored low-priority app is intercepted
- The app appears in the pending queue
- When pressure drops, the app is automatically released
"""

import os
import time
import shutil
import signal
import subprocess

import pytest


# Name of a test binary that the BPF interceptor can catch.
# Override via environment variable if needed.
TEST_BPF_BINARY = os.environ.get("SMARTUNE_TEST_BPF_BINARY", "sleep")
TEST_APP_NAME = f"queue_test_{os.getpid()}"
TEST_APP_ID = f"queue-test-{os.getpid()}-{int(time.time())}"


def _find_test_binary():
    """Locate the test binary that will be intercepted by BPF."""
    path = shutil.which(TEST_BPF_BINARY)
    return path


def _get_pending_apps(api, base_url):
    """Helper to fetch the current pending app list."""
    resp = api.post(f"{base_url}/app/get_pending_app", json={})
    data = resp.json()
    return data


def _register_test_app(api, base_url, app_name, app_id, bpf_name):
    """Register a controlled app with BPF interception configured."""
    payload = {
        "name": app_name,
        "id": app_id,
        "priority": "low",
        "bpf_name": [bpf_name],
        "process_names": [bpf_name],
        "commandline": "",
        "remark": "Automated test app for TC-S-007",
    }
    resp = api.post(f"{base_url}/app/new_controlled_app", json=payload)
    return resp


def _unregister_test_app(api, base_url, app_id):
    """Remove the test app from controlled list (best effort)."""
    try:
        api.post(f"{base_url}/app/set_to_uncontrol", json={"app_id": app_id})
    except Exception:
        pass


def _cancel_pending(api, base_url, app_id):
    """Cancel a pending app relaunch."""
    resp = api.post(
        f"{base_url}/app/cancel_relaunch", json={"app_id": app_id}
    )
    return resp


@pytest.mark.service
@pytest.mark.root
@pytest.mark.stress_tools
class TestAppStartupQueue:
    """TC-S-007: Application Startup Queue Mechanism tests."""

    def test_pending_queue_empty_under_normal_conditions(self, api, base_url):
        """Under low pressure, pending queue should be empty."""
        data = _get_pending_apps(api, base_url)
        # Either retcode=0 with empty list, or retcode=404 (no pending apps)
        assert data["retcode"] in (0, 404), (
            f"Unexpected retcode: {data['retcode']}, msg: {data.get('retmsg')}"
        )
        if data["retcode"] == 0:
            assert isinstance(data.get("data"), list)

    def test_app_intercepted_under_critical_pressure(
        self, api, base_url, stress_cpu, wait_for_pressure
    ):
        """Under critical pressure, launching a controlled app should add it to pending queue."""
        binary_path = _find_test_binary()
        if binary_path is None:
            pytest.skip(
                f"Test binary '{TEST_BPF_BINARY}' not found; "
                f"set SMARTUNE_TEST_BPF_BINARY to an available executable"
            )

        # Derive the comm name (what BPF sees) — basename truncated to 15 chars
        bpf_comm = os.path.basename(binary_path)[:15]

        # Register a test app with BPF interception
        reg_resp = _register_test_app(
            api, base_url, TEST_APP_NAME, TEST_APP_ID, bpf_comm
        )
        reg_data = reg_resp.json()
        if reg_data.get("retcode") not in (0, None):
            # If registration fails due to conflict, the feature may not be
            # configured for this binary — skip gracefully.
            pytest.skip(
                f"Could not register test app for BPF interception: "
                f"{reg_data.get('retmsg', 'unknown error')}"
            )

        test_proc = None
        try:
            # Push system to critical pressure
            stress_cpu(percent=95, duration=60)

            # Wait for pressure to actually reach critical levels
            try:
                wait_for_pressure(level=70, timeout=20)
            except TimeoutError:
                pytest.skip(
                    "System did not reach critical pressure — "
                    "test environment may have too many CPUs for stress to saturate"
                )

            # Launch the test binary (it should be intercepted)
            test_proc = subprocess.Popen(
                [binary_path, "3600"],  # sleep for a long time
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setpgrp,
            )

            # Poll the pending queue — the app should appear within a few seconds
            deadline = time.time() + 15
            found_in_queue = False
            while time.time() < deadline:
                pending_data = _get_pending_apps(api, base_url)
                if pending_data.get("retcode") == 0:
                    pending_list = pending_data.get("data", [])
                    for app in pending_list:
                        if app.get("app_name") == TEST_APP_NAME or app.get(
                            "app_id"
                        ) == TEST_APP_ID:
                            found_in_queue = True
                            break
                if found_in_queue:
                    break
                time.sleep(1)

            assert found_in_queue, (
                f"Test app '{TEST_APP_NAME}' was not found in the pending queue "
                f"after launching under critical pressure. "
                f"Last pending data: {pending_data}"
            )

        finally:
            # Cleanup: terminate test process
            if test_proc and test_proc.poll() is None:
                try:
                    os.killpg(os.getpgid(test_proc.pid), signal.SIGKILL)
                    test_proc.wait(timeout=5)
                except Exception:
                    pass

            # Cancel any pending relaunch
            _cancel_pending(api, base_url, TEST_APP_ID)

            # Unregister the test app
            _unregister_test_app(api, base_url, TEST_APP_ID)

    def test_queue_ordering_by_priority(self, api, base_url):
        """Pending apps should be sorted by priority descending (highest first)."""
        data = _get_pending_apps(api, base_url)

        if data["retcode"] == 404:
            pytest.skip("No pending apps currently in queue to verify ordering")

        assert data["retcode"] == 0, (
            f"Unexpected error fetching pending apps: {data.get('retmsg')}"
        )

        pending_list = data.get("data", [])
        if len(pending_list) <= 1:
            pytest.skip(
                "Need at least 2 pending apps to verify ordering; "
                "only found {0}".format(len(pending_list))
            )

        priorities = [app["priority_value"] for app in pending_list]
        assert priorities == sorted(priorities, reverse=True), (
            f"Pending apps are not sorted by priority descending. "
            f"Got priorities: {priorities}"
        )

    def test_cancel_pending_app(self, api, base_url):
        """Cancelling a pending app removes it from the queue."""
        # Get current pending list
        data = _get_pending_apps(api, base_url)

        if data["retcode"] == 404 or not data.get("data"):
            pytest.skip("No pending apps to cancel — cannot test cancellation")

        pending_list = data["data"]
        target_app = pending_list[0]
        target_id = target_app["app_id"]
        target_name = target_app.get("app_name", target_id)

        # Cancel the pending app
        cancel_resp = _cancel_pending(api, base_url, target_id)
        cancel_data = cancel_resp.json()
        assert cancel_data["retcode"] == 0, (
            f"Failed to cancel pending app '{target_name}': "
            f"{cancel_data.get('retmsg')}"
        )

        # Verify the app no longer appears in the pending queue
        time.sleep(1)  # Brief pause for state propagation
        verify_data = _get_pending_apps(api, base_url)

        if verify_data["retcode"] == 404:
            # Queue is now empty — cancellation succeeded
            return

        remaining = verify_data.get("data", [])
        remaining_ids = [app["app_id"] for app in remaining]
        assert target_id not in remaining_ids, (
            f"App '{target_name}' (id={target_id}) still appears in pending "
            f"queue after cancellation"
        )

    def test_app_released_when_pressure_drops(
        self, api, base_url, stress_cpu, wait_for_pressure
    ):
        """When system pressure drops below critical, pending apps should be released."""
        binary_path = _find_test_binary()
        if binary_path is None:
            pytest.skip(
                f"Test binary '{TEST_BPF_BINARY}' not found; "
                f"set SMARTUNE_TEST_BPF_BINARY to an available executable"
            )

        bpf_comm = os.path.basename(binary_path)[:15]
        release_app_name = f"queue_release_test_{os.getpid()}"
        release_app_id = f"queue-release-{os.getpid()}-{int(time.time())}"

        # Register a test app
        reg_resp = _register_test_app(
            api, base_url, release_app_name, release_app_id, bpf_comm
        )
        reg_data = reg_resp.json()
        if reg_data.get("retcode") not in (0, None):
            pytest.skip(
                f"Could not register test app: {reg_data.get('retmsg')}"
            )

        test_proc = None
        try:
            # Push system to critical pressure with a SHORT duration
            stress_cpu(percent=95, duration=20)

            try:
                wait_for_pressure(level=70, timeout=15)
            except TimeoutError:
                pytest.skip(
                    "System did not reach critical pressure"
                )

            # Launch the test binary
            test_proc = subprocess.Popen(
                [binary_path, "3600"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setpgrp,
            )

            # Wait for it to appear in pending queue
            deadline = time.time() + 10
            found_in_queue = False
            while time.time() < deadline:
                pending_data = _get_pending_apps(api, base_url)
                if pending_data.get("retcode") == 0:
                    for app in pending_data.get("data", []):
                        if app.get("app_id") == release_app_id:
                            found_in_queue = True
                            break
                if found_in_queue:
                    break
                time.sleep(1)

            if not found_in_queue:
                pytest.skip(
                    "App did not enter pending queue — "
                    "BPF interception may not be active for this binary"
                )

            # Now wait for stress to end (duration=20s) and pressure to drop.
            # The app should be automatically released from the queue.
            release_deadline = time.time() + 40
            released = False
            while time.time() < release_deadline:
                check_data = _get_pending_apps(api, base_url)
                if check_data.get("retcode") == 404:
                    released = True
                    break
                if check_data.get("retcode") == 0:
                    pending_ids = [
                        app["app_id"]
                        for app in check_data.get("data", [])
                    ]
                    if release_app_id not in pending_ids:
                        released = True
                        break
                time.sleep(2)

            assert released, (
                f"App '{release_app_name}' was not automatically released from "
                f"the pending queue after pressure dropped"
            )

        finally:
            if test_proc and test_proc.poll() is None:
                try:
                    os.killpg(os.getpgid(test_proc.pid), signal.SIGKILL)
                    test_proc.wait(timeout=5)
                except Exception:
                    pass

            _cancel_pending(api, base_url, release_app_id)
            _unregister_test_app(api, base_url, release_app_id)
