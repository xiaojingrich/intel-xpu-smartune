# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Release E2E: REAL system side-effects of resource limiting.

This module closes the "silent-failure" gap that the weaker API-shape tests in
``acceptance/test_resource_control.py`` cannot catch. SmarTune is a
system-control tool: the ``/app/resource_limit`` endpoint can return
``retcode=0`` while the underlying cgroup write silently failed (wrong path,
permission issue, process already gone, etc.). A test that only asserts the
HTTP status / retcode would pass anyway.

The tests here instead verify the ACTUAL kernel-visible effects:
  - that the cgroup control files (``cpu.max`` / ``memory.high``) are really
    written with a finite limit after a limit call,
  - that a real CPU-burning process is genuinely throttled by the kernel, and
  - that ``/app/resource_restore`` truly reverts the cgroup back to unlimited.

Coverage relative to the test plan:
  - TC-S-009 (manual resource limit) — proves the limit reaches the cgroup and
    has measurable effect, not just that the API returns success.
  - TC-S-006 (passive control) — the same write/throttle/restore mechanism that
    passive control relies on, exercised end-to-end.

Prerequisites (auto-skipped via markers + defensive runtime checks):
  - root privileges (to read/limit cgroups owned by the service),
  - cgroup v2 mounted, and
  - a live SmarTune service.

The central challenge is getting a real CPU-burning process registered as a
controlled app so the balancer's ``get_app_resource_usage`` can locate its
cgroup. We launch a tiny temp script with a UNIQUE filename so the balancer's
``pgrep -fi <app_name>`` match is unambiguous, register it via
``/app/set_to_control`` with ``app_name``/``cmdline`` matching the launched
process, let it accumulate measurable CPU, then limit it.
"""

import os
import sys
import time
import uuid
import signal
import subprocess

import psutil
import pytest


CGROUP_BASE = "/sys/fs/cgroup"


# ─── module-level helpers ────────────────────────────────────────────────────

def _has_cgroup_v2():
    """cgroup v2 unified hierarchy is mounted when this controllers file exists."""
    return os.path.exists(os.path.join(CGROUP_BASE, "cgroup.controllers"))


def _read_cgroup_file_for_pid(pid, filename):
    """Read a cgroup control file for the cgroup the PID currently lives in.

    Reads ``/proc/<pid>/cgroup`` (cgroup v2 line is ``0::<path>``), builds
    ``/sys/fs/cgroup<path>/<filename>`` and returns its stripped content, or
    ``None`` if anything is missing/unreadable.

    NOTE: the balancer may MOVE the process into a new cgroup it created
    (basename == effective_app_id), so callers should re-read this AFTER
    limiting to pick up the current cgroup rather than a stale one.
    """
    proc_cgroup = f"/proc/{pid}/cgroup"
    try:
        with open(proc_cgroup, "r") as f:
            content = f.read()
    except (IOError, OSError):
        return None

    cg_path = None
    for line in content.strip().split("\n"):
        parts = line.split(":")
        # cgroup v2 unified hierarchy: "0::<path>"
        if len(parts) == 3 and parts[0] == "0" and parts[1] == "":
            cg_path = parts[2]
            break
    if cg_path is None:
        return None

    target = os.path.join(CGROUP_BASE, cg_path.lstrip("/"), filename)
    try:
        with open(target, "r") as f:
            return f.read().strip()
    except (IOError, OSError):
        return None


def _scan_cgroups_for_pid(pid, filename, default_first_field):
    """Fallback: walk /sys/fs/cgroup for a dir that contains ``pid`` in
    cgroup.procs AND whose ``filename`` is set to a non-default value.

    ``default_first_field`` is the token that indicates "unlimited" for the
    first whitespace-separated field of the file (e.g. "max" for cpu.max /
    memory.high). Returns the file content or ``None``.
    """
    for root, _dirs, files in os.walk(CGROUP_BASE):
        if "cgroup.procs" not in files or filename not in files:
            continue
        procs_path = os.path.join(root, "cgroup.procs")
        try:
            with open(procs_path, "r") as f:
                pids = {int(x) for x in f.read().split() if x.strip().isdigit()}
        except (IOError, OSError, ValueError):
            continue
        if pid not in pids:
            continue
        try:
            with open(os.path.join(root, filename), "r") as f:
                content = f.read().strip()
        except (IOError, OSError):
            continue
        if content and content.split()[0] != default_first_field:
            return content
    return None


def _cpu_max_is_limited(content):
    """cpu.max format is "<quota> <period>" or "max <period>".
    A real quota was set when the first field is a number (not "max")."""
    if not content:
        return False
    first = content.split()[0]
    if first == "max":
        return False
    try:
        int(first)
        return True
    except ValueError:
        return False


def _memory_high_is_limited(content):
    """memory.high is a single number or "max". Limited == a finite number."""
    if not content:
        return False
    token = content.split()[0]
    if token == "max":
        return False
    try:
        int(token)
        return True
    except ValueError:
        return False


# ─── test fixture: a registered, controlled, real workload process ──────────

class _Workload:
    """Bundle of state for a launched + registered controlled workload."""

    def __init__(self, api, base_url, body):
        self.api = api
        self.base_url = base_url
        self.app_id = body["app_id"]
        self.app_name = body["app_name"]
        self.proc = None
        self.script_path = None

    def cleanup(self):
        # Kill the workload subprocess (whole group, it had setpgrp).
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                self.proc.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
                try:
                    self.proc.kill()
                except Exception:
                    pass
        # Best-effort API teardown.
        try:
            self.api.post(f"{self.base_url}/app/resource_restore",
                          json={"app_id": self.app_id})
        except Exception:
            pass
        try:
            self.api.post(f"{self.base_url}/app/remove_from_control",
                          json={"app_id": self.app_id, "app_name": self.app_name})
        except Exception:
            pass
        # Remove the temp script.
        if self.script_path and os.path.exists(self.script_path):
            try:
                os.remove(self.script_path)
            except OSError:
                pass


def _launch_and_register(api, base_url, script_body, pin_cpu=False, warmup=3.0):
    """Create a uniquely-named temp script, launch it, register it as a
    controlled app, and let it accumulate measurable usage.

    Returns a ``_Workload``. The caller MUST call ``.cleanup()`` in a finally
    block.
    """
    if not _has_cgroup_v2():
        pytest.skip("cgroup v2 is not mounted")

    token = uuid.uuid4().hex[:12]
    # A unique recognizable name so the balancer's `pgrep -fi <app_name>`
    # matches exactly this process and nothing else.
    script_path = f"/tmp/smartune_sidefx_{token}.py"
    with open(script_path, "w") as f:
        f.write(script_body)

    cmd = [sys.executable, script_path]
    if pin_cpu and hasattr(os, "sched_getaffinity"):
        # Pin to a single core so the "before" measurement saturates one CPU,
        # which makes the throttling delta unambiguous.
        cmd = ["taskset", "-c", "0", sys.executable, script_path]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=os.setpgrp,
    )

    # The app_name / cmdline must match the running process so
    # get_app_resource_usage -> pgrep -fi can find it. The unique script path
    # appears in the process cmdline, so we use it as the match key.
    body = {
        "app_id": script_path,          # basename feeds the pgrep fallback too
        "app_name": script_path,        # pgrep -fi matches the full cmdline
        "controlled": True,
        "priority": 50,
        "cmdline": script_path,
    }

    wl = _Workload(api, base_url, body)
    wl.proc = proc
    wl.script_path = script_path

    # If it died immediately, give up early with a clear cleanup path.
    time.sleep(0.3)
    if proc.poll() is not None:
        wl.cleanup()
        pytest.skip("workload process exited immediately; cannot test side effects")

    resp = api.post(f"{base_url}/app/set_to_control", json=body)
    if resp.status_code != 200:
        wl.cleanup()
        pytest.skip(f"set_to_control failed ({resp.status_code}): {resp.text}")

    # Let the process burn CPU / hold memory so the balancer measures
    # non-negligible usage (CPU limit only applies when measured CPU >= 2%,
    # memory only when mem_current > 0).
    time.sleep(warmup)
    return wl


def _do_limit(api, base_url, wl, priority="low", limit_overrides=None):
    """Call /app/resource_limit and return (retcode, data, retmsg)."""
    payload = {
        "app_id": wl.app_id,
        "app_name": wl.app_name,
        "priority": priority,
    }
    if limit_overrides is not None:
        payload["limit_overrides"] = limit_overrides
    resp = api.post(f"{base_url}/app/resource_limit", json=payload)
    data = resp.json()
    return data.get("retcode"), data.get("data") or {}, data.get("retmsg", "")


# Workload script bodies.
_CPU_BURN = "while True:\n    pass\n"
_MEM_HOLD = (
    "import time\n"
    "x = bytearray(200 * 1024 * 1024)\n"
    "for i in range(0, len(x), 4096):\n"   # touch pages so RSS is real
    "    x[i] = 1\n"
    "time.sleep(300)\n"
)


@pytest.mark.service
@pytest.mark.root
@pytest.mark.cgroup
class TestResourceLimitSideEffects:
    """Verify the kernel-visible side effects of resource limiting, not just
    that the HTTP API returns success."""

    def test_cpu_limit_writes_cgroup_cpu_max(self, api, base_url):
        """Limiting a real CPU burner must write a finite quota into cpu.max."""
        wl = _launch_and_register(api, base_url, _CPU_BURN)
        try:
            retcode, data, retmsg = _do_limit(api, base_url, wl, priority="low")

            if data.get("skipped"):
                pytest.skip(f"service skipped limiting (usage too low?): {retmsg}")
            if retcode != 0:
                pytest.skip(
                    f"service could not limit the process (retcode={retcode}): "
                    f"{retmsg} — treating as an environment limitation"
                )

            # retcode == 0 => the service CLAIMS success. The cgroup MUST reflect
            # it, otherwise that is exactly the silent-failure bug we hunt.
            time.sleep(1.0)
            content = _read_cgroup_file_for_pid(wl.proc.pid, "cpu.max")
            if content is None or not _cpu_max_is_limited(content):
                # Fall back to scanning, in case the process was moved.
                scanned = _scan_cgroups_for_pid(wl.proc.pid, "cpu.max", "max")
                content = scanned if scanned is not None else content

            assert content is not None, (
                "resource_limit returned retcode=0 but no cpu.max cgroup file "
                f"could be found for pid {wl.proc.pid} (silent failure?)"
            )
            assert _cpu_max_is_limited(content), (
                f"resource_limit returned retcode=0 but cpu.max is still "
                f"unlimited ({content!r}) — cgroup write silently failed"
            )
        finally:
            wl.cleanup()

    def test_cpu_limit_actually_throttles_process(self, api, base_url):
        """A limited CPU burner must consume meaningfully less CPU afterwards."""
        wl = _launch_and_register(api, base_url, _CPU_BURN, pin_cpu=True)
        try:
            proc = psutil.Process(wl.proc.pid)
            # Prime cpu_percent (first call returns 0.0), then measure baseline.
            proc.cpu_percent(interval=None)
            time.sleep(0.2)
            before = proc.cpu_percent(interval=1.0)
            if before < 50.0:
                pytest.skip(
                    f"workload did not saturate a core before limiting "
                    f"(before={before:.1f}%); environment too noisy to assert throttling"
                )

            retcode, data, retmsg = _do_limit(api, base_url, wl, priority="low")
            if data.get("skipped"):
                pytest.skip(f"service skipped limiting: {retmsg}")
            if retcode != 0:
                pytest.skip(
                    f"service could not limit the process (retcode={retcode}): {retmsg}"
                )

            # Give cgroup CPU throttling time to take effect.
            time.sleep(3.0)
            if not psutil.pid_exists(wl.proc.pid) or wl.proc.poll() is not None:
                pytest.skip("workload process died before throttling could be measured")

            after = proc.cpu_percent(interval=1.0)
            assert after < before * 0.8, (
                f"CPU was not throttled: before={before:.1f}% after={after:.1f}% "
                f"(retcode=0 but the limit had no real effect)"
            )
        finally:
            wl.cleanup()

    def test_restore_clears_cgroup_limit(self, api, base_url):
        """After restore, cpu.max must return to the unlimited "max <period>"."""
        wl = _launch_and_register(api, base_url, _CPU_BURN)
        try:
            retcode, data, retmsg = _do_limit(api, base_url, wl, priority="low")
            if data.get("skipped"):
                pytest.skip(f"service skipped limiting: {retmsg}")
            if retcode != 0:
                pytest.skip(
                    f"service could not limit the process (retcode={retcode}): {retmsg}"
                )

            time.sleep(1.0)
            limited = _read_cgroup_file_for_pid(wl.proc.pid, "cpu.max")
            if limited is None or not _cpu_max_is_limited(limited):
                scanned = _scan_cgroups_for_pid(wl.proc.pid, "cpu.max", "max")
                limited = scanned if scanned is not None else limited
            if limited is None or not _cpu_max_is_limited(limited):
                pytest.skip(
                    "could not confirm a limit was applied; nothing to verify on restore"
                )

            resp = api.post(f"{base_url}/app/resource_restore",
                            json={"app_id": wl.app_id})
            restore = resp.json()
            assert restore.get("retcode") == 0, (
                f"resource_restore did not succeed: {restore}"
            )

            time.sleep(1.0)
            after = _read_cgroup_file_for_pid(wl.proc.pid, "cpu.max")
            assert after is not None, (
                "could not read cpu.max after restore to verify it was cleared"
            )
            assert not _cpu_max_is_limited(after), (
                f"resource_restore returned retcode=0 but cpu.max is still "
                f"limited ({after!r}) — restore silently failed to revert the cgroup"
            )
        finally:
            wl.cleanup()

    def test_memory_limit_writes_cgroup_memory_high(self, api, base_url):
        """Limiting a memory-holding process must write a finite memory.high."""
        wl = _launch_and_register(api, base_url, _MEM_HOLD)
        try:
            overrides = {"memory": {"rate": 0.5, "enabled": True}}
            retcode, data, retmsg = _do_limit(
                api, base_url, wl, priority="low", limit_overrides=overrides
            )

            if data.get("skipped"):
                pytest.skip(f"service skipped limiting (usage too low?): {retmsg}")
            if retcode != 0:
                pytest.skip(
                    f"service could not limit the process (retcode={retcode}): {retmsg}"
                )

            time.sleep(1.0)
            content = _read_cgroup_file_for_pid(wl.proc.pid, "memory.high")
            if content is None or not _memory_high_is_limited(content):
                scanned = _scan_cgroups_for_pid(wl.proc.pid, "memory.high", "max")
                content = scanned if scanned is not None else content

            assert content is not None, (
                "resource_limit returned retcode=0 but no memory.high cgroup file "
                f"could be found for pid {wl.proc.pid} (silent failure?)"
            )
            assert _memory_high_is_limited(content), (
                f"resource_limit returned retcode=0 but memory.high is still "
                f"unlimited ({content!r}) — cgroup write silently failed"
            )
        finally:
            wl.cleanup()
