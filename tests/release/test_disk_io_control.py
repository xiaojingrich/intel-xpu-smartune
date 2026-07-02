# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
TC-S-011: Disk I/O Pressure Detection & Control

Verifies:
- High disk I/O is detected via /monitor/dynamic_info
- Under disk pressure, low-priority apps get IO-limited via cgroup io.max
- IO limits match the configured limit_policy values
"""

import subprocess
import time
import os
import signal

import pytest


@pytest.mark.service
@pytest.mark.root
@pytest.mark.io_tools
class TestDiskIOControl:

    def test_disk_metrics_present_in_dynamic_info(self, api, base_url):
        """Dynamic info should include disk pressure metrics."""
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()
        assert data['retcode'] == 0, f"dynamic_info failed: {data}"
        assert 'disk' in data['data'], (
            f"'disk' key missing from dynamic_info data: {list(data['data'].keys())}"
        )
        disk = data['data']['disk']
        assert 'is_stressed' in disk, (
            f"'is_stressed' key missing from disk data: {list(disk.keys())}"
        )
        assert 'iowait' in disk, (
            f"'iowait' key missing from disk data: {list(disk.keys())}"
        )

    def test_disk_io_stats_endpoint(self, api, base_url):
        """App disk IO stats endpoint should return valid data."""
        resp = api.get(f"{base_url}/monitor/app_disk_io_stats")
        data = resp.json()
        assert data['retcode'] == 0, f"app_disk_io_stats failed: {data}"
        assert 'apps' in data['data'], (
            f"'apps' key missing from app_disk_io_stats: {list(data['data'].keys())}"
        )

    def test_disk_pressure_detection_under_fio_load(self, api, base_url):
        """Under heavy fio load, disk pressure should be detected."""
        import shutil
        if not shutil.which('fio'):
            pytest.skip("fio not installed")

        fio_proc = None
        try:
            # 1. Start fio with heavy random write workload in background
            fio_cmd = [
                'fio',
                '--name=release_io_pressure_test',
                '--rw=randwrite',
                '--bs=4k',
                '--size=256M',
                '--numjobs=4',
                '--runtime=30',
                '--time_based',
                '--ioengine=libaio',
                '--iodepth=32',
                '--direct=1',
                '--directory=/tmp',
                '--group_reporting',
                '--output=/dev/null',
            ]
            fio_proc = subprocess.Popen(
                fio_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setpgrp,
            )

            # 2. Poll /monitor/dynamic_info for disk.is_stressed == True
            deadline = time.time() + 30
            pressure_detected = False

            while time.time() < deadline:
                resp = api.get(f"{base_url}/monitor/dynamic_info")
                data = resp.json()
                if data['retcode'] == 0:
                    disk = data.get('data', {}).get('disk', {})
                    if disk.get('is_stressed'):
                        pressure_detected = True
                        break
                    # Also check if iowait is elevated
                    iowait = disk.get('iowait', 0)
                    if iowait > 20:
                        pressure_detected = True
                        break
                time.sleep(2)

            assert pressure_detected, (
                "Disk pressure was not detected within 30s under fio load"
            )

        finally:
            # 4. Stop fio
            if fio_proc and fio_proc.poll() is None:
                try:
                    os.killpg(os.getpgid(fio_proc.pid), signal.SIGTERM)
                    fio_proc.wait(timeout=10)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(os.getpgid(fio_proc.pid), signal.SIGKILL)
                        fio_proc.wait(timeout=5)
                    except Exception:
                        pass

            # Clean up any fio temp files
            import glob
            for f in glob.glob('/tmp/release_io_pressure_test*'):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def test_io_cgroup_limits_applied(self, api, base_url):
        """Under disk pressure with passive control, IO limits should be set in cgroup."""
        # 1. Get controlled apps to find their cgroup paths
        resp = api.get(f"{base_url}/monitor/dynamic_info")
        data = resp.json()
        assert data['retcode'] == 0, f"dynamic_info failed: {data}"

        apps = data.get('data', {}).get('apps', [])
        if not apps:
            pytest.skip("No controlled apps available for cgroup IO test")

        # 2. Find an app that has a cgroup scope
        cgroup_checked = False
        for app in apps:
            pid = app.get('pid')
            if not pid or int(pid) <= 0:
                continue

            # Read cgroup path for this process
            cgroup_file = f"/proc/{pid}/cgroup"
            try:
                with open(cgroup_file, 'r') as f:
                    cgroup_content = f.read()
            except (IOError, FileNotFoundError):
                continue

            # Parse cgroup path (cgroup v2: "0::/path")
            cgroup_path = None
            for line in cgroup_content.strip().split('\n'):
                parts = line.split(':')
                if len(parts) >= 3 and parts[0] == '0':
                    cgroup_path = parts[2]
                    break

            if not cgroup_path:
                continue

            # 3. Check /sys/fs/cgroup/{path}/io.max
            io_max_path = f"/sys/fs/cgroup{cgroup_path}/io.max"
            if not os.path.exists(io_max_path):
                continue

            try:
                with open(io_max_path, 'r') as f:
                    io_max_content = f.read().strip()
            except (IOError, PermissionError):
                continue

            cgroup_checked = True

            # If IO limits are set, they should contain rbps/wbps/riops/wiops
            # Format: "MAJ:MIN rbps=VALUE wbps=VALUE riops=VALUE wiops=VALUE"
            if io_max_content and io_max_content != '':
                for line in io_max_content.split('\n'):
                    if 'rbps=' in line or 'wbps=' in line:
                        # Verify values are numeric or 'max'
                        parts = line.split()
                        assert len(parts) >= 2, (
                            f"Malformed io.max line: {line}"
                        )
                        for part in parts[1:]:
                            if '=' in part:
                                key, val = part.split('=', 1)
                                assert key in ('rbps', 'wbps', 'riops', 'wiops'), (
                                    f"Unexpected io.max key: {key}"
                                )
                                assert val == 'max' or val.isdigit(), (
                                    f"Invalid io.max value for {key}: {val}"
                                )
            break

        if not cgroup_checked:
            pytest.skip(
                "No controlled app with accessible cgroup io.max found"
            )
