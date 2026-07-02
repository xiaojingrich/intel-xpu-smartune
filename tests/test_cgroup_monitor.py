# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for balancer/monitor/cgroup.py — CgroupMonitor class."""

import os
import sys
from unittest.mock import patch, MagicMock, mock_open

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))


from monitor.cgroup import CgroupMonitor


class TestGetAllPids:
    def setup_method(self):
        self.monitor = CgroupMonitor(mount_point="/sys/fs/cgroup")

    @patch('os.listdir')
    def test_normal_case(self, mock_listdir):
        mock_listdir.return_value = ['1', '23', '456', 'cpuinfo', 'meminfo', 'self']
        pids = self.monitor.get_all_pids()
        assert pids == [1, 23, 456]
        mock_listdir.assert_called_once_with("/proc")

    @patch('os.listdir')
    def test_permission_error(self, mock_listdir):
        mock_listdir.side_effect = PermissionError("Permission denied")
        pids = self.monitor.get_all_pids()
        assert pids == []

    @patch('os.listdir')
    def test_file_not_found(self, mock_listdir):
        mock_listdir.side_effect = FileNotFoundError("/proc not found")
        pids = self.monitor.get_all_pids()
        assert pids == []

    @patch('os.listdir')
    def test_empty_proc(self, mock_listdir):
        mock_listdir.return_value = []
        pids = self.monitor.get_all_pids()
        assert pids == []


class TestGetProcessInfo:
    def setup_method(self):
        self.monitor = CgroupMonitor(mount_point="/sys/fs/cgroup")

    @patch('builtins.open', new_callable=mock_open)
    def test_normal_case(self, mock_file):
        status_content = "Name:\tpython3\nPid:\t1234\nUid:\t1000\n"
        cmdline_content = "/usr/bin/python3\x00script.py\x00--flag"
        stat_content = "1234 (python3) S 1 1234 1234 0 -1 4194304"

        def open_side_effect(path, *args, **kwargs):
            m = MagicMock()
            if 'status' in path:
                m.__enter__ = MagicMock(return_value=iter(status_content.splitlines(True)))
                m.__exit__ = MagicMock(return_value=False)
                # Support iteration in for loop
                m.__enter__.return_value = status_content.splitlines(True)
            elif 'cmdline' in path:
                m.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=cmdline_content)))
                m.__exit__ = MagicMock(return_value=False)
            elif 'stat' in path:
                m.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=stat_content)))
                m.__exit__ = MagicMock(return_value=False)
            return m

        mock_file.side_effect = open_side_effect

        info = self.monitor.get_process_info(1234)
        assert info['Name'] == 'python3'
        assert info['Pid'] == '1234'
        assert info['Cmdline'] == '/usr/bin/python3 script.py --flag'
        assert info['State'] == 'S'
        assert info['PPid'] == '1'

    @patch('builtins.open')
    def test_process_not_found(self, mock_file):
        mock_file.side_effect = FileNotFoundError("No such process")
        info = self.monitor.get_process_info(99999)
        assert info == {}

    @patch('builtins.open')
    def test_permission_error(self, mock_file):
        mock_file.side_effect = PermissionError("Permission denied")
        info = self.monitor.get_process_info(1)
        assert info == {}


class TestGetMemoryStats:
    def setup_method(self):
        self.monitor = CgroupMonitor(mount_point="/sys/fs/cgroup")

    @patch('builtins.open')
    def test_v2_files_exist(self, mock_file):
        """Test cgroup v2 memory files (memory.current, memory.max, memory.events)."""
        def open_side_effect(path, *args, **kwargs):
            m = MagicMock()
            if path.endswith("memory.current"):
                m.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value="104857600")))
                m.__exit__ = MagicMock(return_value=False)
            elif path.endswith("memory.max"):
                m.__enter__ = MagicMock(return_value=MagicMock(
                    read=MagicMock(return_value="209715200\n")
                ))
                m.__exit__ = MagicMock(return_value=False)
            elif path.endswith("memory.events"):
                lines = ["low 0\n", "high 0\n", "max 0\n", "oom 0\n", "oom_kill 3\n"]
                m.__enter__ = MagicMock(return_value=iter(lines))
                m.__exit__ = MagicMock(return_value=False)
            else:
                raise FileNotFoundError(f"No such file: {path}")
            return m

        mock_file.side_effect = open_side_effect

        stats = self.monitor._get_memory_stats("test_group")
        assert stats['usage'] == 104857600
        assert stats['limit'] == 209715200
        assert stats['oom_kills'] == 3

    @patch('builtins.open')
    def test_v1_fallback(self, mock_file):
        """Test cgroup v1 fallback (memory.usage_in_bytes, memory.limit_in_bytes)."""
        call_count = {'current': 0, 'max': 0}

        def open_side_effect(path, *args, **kwargs):
            m = MagicMock()
            if path.endswith("memory.current"):
                raise FileNotFoundError("v2 file not available")
            elif path.endswith("memory.usage_in_bytes"):
                m.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value="52428800")))
                m.__exit__ = MagicMock(return_value=False)
            elif path.endswith("memory.max"):
                raise FileNotFoundError("v2 file not available")
            elif path.endswith("memory.limit_in_bytes"):
                m.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value="104857600")))
                m.__exit__ = MagicMock(return_value=False)
            elif path.endswith("memory.events"):
                raise FileNotFoundError("v2 file not available")
            elif path.endswith("memory.oom_control"):
                lines = ["oom_kill_disable 0\n", "under_oom 0\n"]
                m.__enter__ = MagicMock(return_value=iter(lines))
                m.__exit__ = MagicMock(return_value=False)
            else:
                raise FileNotFoundError(f"No such file: {path}")
            return m

        mock_file.side_effect = open_side_effect

        stats = self.monitor._get_memory_stats("test_group")
        assert stats['usage'] == 52428800
        assert stats['limit'] == 104857600

    @patch('builtins.open')
    def test_max_limit_means_unlimited(self, mock_file):
        """Test that 'max' in memory.max translates to 1<<64."""
        def open_side_effect(path, *args, **kwargs):
            m = MagicMock()
            if path.endswith("memory.current"):
                m.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value="1024")))
                m.__exit__ = MagicMock(return_value=False)
            elif path.endswith("memory.max"):
                m.__enter__ = MagicMock(return_value=MagicMock(
                    read=MagicMock(return_value="max\n")
                ))
                m.__exit__ = MagicMock(return_value=False)
            elif path.endswith("memory.events"):
                lines = ["oom_kill 0\n"]
                m.__enter__ = MagicMock(return_value=iter(lines))
                m.__exit__ = MagicMock(return_value=False)
            else:
                raise FileNotFoundError(f"No such file: {path}")
            return m

        mock_file.side_effect = open_side_effect

        stats = self.monitor._get_memory_stats("test_group")
        assert stats['limit'] == (1 << 64)


class TestGetIoStats:
    def setup_method(self):
        self.monitor = CgroupMonitor(mount_point="/sys/fs/cgroup")

    @patch('builtins.open')
    def test_with_io_stat_data(self, mock_file):
        """Test parsing io.stat with rbps and wbps values."""
        io_stat_lines = [
            "8:0 rbps=1024 wbps=2048 rios=10 wios=20\n",
            "8:16 rbps=512 wbps=256 rios=5 wios=8\n",
        ]
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=iter(io_stat_lines))
        m.__exit__ = MagicMock(return_value=False)
        mock_file.return_value = m

        stats = self.monitor._get_io_stats("test_group")
        # rbps: 1024 + 512 = 1536, wbps: 2048 + 256 = 2304, total = 3840
        assert stats['bps'] == 3840
        assert stats['iops'] == 0

    @patch('builtins.open')
    def test_missing_io_stat_file(self, mock_file):
        """Test graceful handling when io.stat does not exist."""
        mock_file.side_effect = FileNotFoundError("No such file")
        stats = self.monitor._get_io_stats("test_group")
        assert stats == {'bps': 0, 'iops': 0}


class TestGetCgroupPids:
    def setup_method(self):
        self.monitor = CgroupMonitor(mount_point="/sys/fs/cgroup")

    @patch('builtins.open')
    def test_normal_case(self, mock_file):
        """Test reading cgroup.procs with valid PIDs."""
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value="100\n200\n300\n")))
        m.__exit__ = MagicMock(return_value=False)
        mock_file.return_value = m

        pids = self.monitor._get_cgroup_pids("test_group")
        assert pids == [100, 200, 300]
        mock_file.assert_called_once_with("/sys/fs/cgroup/test_group/cgroup.procs")

    @patch('builtins.open')
    def test_file_not_found(self, mock_file):
        """Test returns empty list when cgroup.procs does not exist."""
        mock_file.side_effect = FileNotFoundError("No such file")
        pids = self.monitor._get_cgroup_pids("nonexistent_group")
        assert pids == []


class TestGetCpuStats:
    def setup_method(self):
        self.monitor = CgroupMonitor(mount_point="/sys/fs/cgroup")

    @patch('builtins.open')
    def test_normal_parsing(self, mock_file):
        """Test parsing cpu.stat key-value lines."""
        cpu_stat_lines = [
            "usage_usec 123456789\n",
            "user_usec 100000000\n",
            "system_usec 23456789\n",
            "nr_periods 1000\n",
            "nr_throttled 50\n",
            "throttled_usec 5000000\n",
        ]
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=iter(cpu_stat_lines))
        m.__exit__ = MagicMock(return_value=False)
        mock_file.return_value = m

        stats = self.monitor.get_cpu_stats("test_group")
        assert stats['usage_usec'] == 123456789
        assert stats['user_usec'] == 100000000
        assert stats['system_usec'] == 23456789
        assert stats['nr_periods'] == 1000
        assert stats['nr_throttled'] == 50
        assert stats['throttled_usec'] == 5000000
        mock_file.assert_called_once_with("/sys/fs/cgroup/test_group/cpu.stat")

    @patch('builtins.open')
    def test_file_missing(self, mock_file):
        """Test returns empty dict when cpu.stat does not exist."""
        mock_file.side_effect = FileNotFoundError("No such file")
        stats = self.monitor.get_cpu_stats("test_group")
        assert stats == {}


class TestGetMemoryUsage:
    def setup_method(self):
        self.monitor = CgroupMonitor(mount_point="/sys/fs/cgroup")

    @patch('builtins.open')
    def test_normal_case(self, mock_file):
        """Test reading memory.current successfully."""
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value="67108864")))
        m.__exit__ = MagicMock(return_value=False)
        mock_file.return_value = m

        usage = self.monitor.get_memory_usage("test_group")
        assert usage == 67108864
        mock_file.assert_called_once_with("/sys/fs/cgroup/test_group/memory.current")

    @patch('builtins.open')
    def test_file_missing(self, mock_file):
        """Test returns 0 when memory.current does not exist."""
        mock_file.side_effect = FileNotFoundError("No such file")
        usage = self.monitor.get_memory_usage("test_group")
        assert usage == 0


class TestGetGroupStats:
    def setup_method(self):
        self.monitor = CgroupMonitor(mount_point="/sys/fs/cgroup")

    @patch.object(CgroupMonitor, '_get_cgroup_pids')
    @patch.object(CgroupMonitor, '_get_io_stats')
    @patch.object(CgroupMonitor, '_get_memory_stats')
    @patch.object(CgroupMonitor, 'get_cpu_stats')
    def test_integration(self, mock_cpu, mock_memory, mock_io, mock_pids):
        """Test that get_group_stats aggregates all sub-calls correctly."""
        mock_cpu.return_value = {'usage_usec': 1000, 'user_usec': 800, 'system_usec': 200}
        mock_memory.return_value = {'usage': 104857600, 'limit': 209715200, 'oom_kills': 0}
        mock_io.return_value = {'bps': 5000, 'iops': 0}
        mock_pids.return_value = [100, 200, 300]

        stats = self.monitor.get_group_stats("my_cgroup")

        assert stats['cpu'] == {'usage_usec': 1000, 'user_usec': 800, 'system_usec': 200}
        assert stats['memory'] == {'usage': 104857600, 'limit': 209715200, 'oom_kills': 0}
        assert stats['io'] == {'bps': 5000, 'iops': 0}
        assert stats['pids'] == 3

        mock_cpu.assert_called_once_with("my_cgroup")
        mock_memory.assert_called_once_with("my_cgroup")
        mock_io.assert_called_once_with("my_cgroup")
        mock_pids.assert_called_once_with("my_cgroup")

    @patch.object(CgroupMonitor, '_get_cgroup_pids')
    @patch.object(CgroupMonitor, '_get_io_stats')
    @patch.object(CgroupMonitor, '_get_memory_stats')
    @patch.object(CgroupMonitor, 'get_cpu_stats')
    def test_empty_cgroup(self, mock_cpu, mock_memory, mock_io, mock_pids):
        """Test get_group_stats with no PIDs and empty stats."""
        mock_cpu.return_value = {}
        mock_memory.return_value = {'usage': 0, 'limit': (1 << 64), 'oom_kills': 0}
        mock_io.return_value = {'bps': 0, 'iops': 0}
        mock_pids.return_value = []

        stats = self.monitor.get_group_stats("empty_cgroup")

        assert stats['cpu'] == {}
        assert stats['memory']['usage'] == 0
        assert stats['io']['bps'] == 0
        assert stats['pids'] == 0
