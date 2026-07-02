# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for controller modules — testing real logic, not just mocks."""

import os
import sys
from unittest.mock import patch, MagicMock, mock_open, call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))


class TestCPUController:
    @pytest.fixture
    def cpu_ctl(self):
        from controller.cpu import CPUController
        return CPUController("/sys/fs/cgroup")

    def test_controller_type(self, cpu_ctl):
        assert cpu_ctl.controller_type() == "cpu"

    def test_set_weight(self, cpu_ctl):
        with patch('builtins.open', mock_open()) as m:
            cpu_ctl.set_weight("test.scope", 150)
            m.assert_called_once_with("/sys/fs/cgroup/test.scope/cpu.weight", 'w')
            m().write.assert_called_once_with("150")

    def test_set_affinity(self, cpu_ctl):
        with patch('builtins.open', mock_open()) as m:
            cpu_ctl.set_affinity("test.scope", "0-3")
            m.assert_called_once_with("/sys/fs/cgroup/test.scope/cpuset.cpus", 'w')
            m().write.assert_called_once_with("0-3")


class TestMemoryController:
    @pytest.fixture
    def mem_ctl(self):
        from controller.memory import MemoryController
        return MemoryController("/sys/fs/cgroup")

    def test_controller_type(self, mem_ctl):
        assert mem_ctl.controller_type() == "memory"

    def test_set_limit(self, mem_ctl):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = mem_ctl.set_limit("test.scope", 1073741824)
            assert result is True
            mock_run.assert_called_once()


class TestIOController:
    """Test IOController with real logic, mocking only subprocess calls."""

    @pytest.fixture
    def io_ctl(self):
        with patch('subprocess.run') as mock_run, \
             patch('subprocess.check_output') as mock_check:
            mock_check.return_value = "user-1000.slice  loaded active\n"
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr=b"")
            with patch('os.path.exists', return_value=True):
                from controller.io import IOController
                ctl = IOController()
                return ctl

    def test_get_disk_id_parses_lsblk(self, io_ctl):
        """Verify get_disk_id correctly parses lsblk output."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout="NAME    TYPE MAJ:MIN  SIZE ROTA\n"
                       "sda     disk   8:0  500G    0\n"
                       "nvme0n1 disk 259:0    1T    0\n"
                       "sda1    part   8:1  499G    0\n",
                returncode=0
            )
            result = io_ctl.get_disk_id()
            assert "sda" in result
            assert "nvme0n1" in result
            assert result["sda"] == "8:0"
            assert result["nvme0n1"] == "259:0"
            # Partitions should NOT be included
            assert "sda1" not in result

    def test_get_disk_id_with_filter(self, io_ctl):
        """Verify disk name filtering works."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout="NAME    TYPE MAJ:MIN  SIZE ROTA\n"
                       "sda     disk   8:0  500G    0\n"
                       "nvme0n1 disk 259:0    1T    0\n",
                returncode=0
            )
            result = io_ctl.get_disk_id(disk_filter="nvme")
            assert "nvme0n1" in result
            assert "sda" not in result

    def test_get_disk_id_empty_on_failure(self, io_ctl):
        """On lsblk failure, return empty dict."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = Exception("command not found")
            result = io_ctl.get_disk_id()
            assert result == {}

    def test_set_disk_io_throttle_constructs_correct_command(self, io_ctl):
        """Verify throttle writes the correct io.max content."""
        with patch('subprocess.run') as mock_run, \
             patch.object(io_ctl, 'get_disk_id', return_value={"sda": "8:0"}), \
             patch.object(io_ctl, '_get_full_cgroup_path', return_value="/sys/fs/cgroup/test.scope/io.max"), \
             patch('os.path.exists', return_value=True):
            mock_run.return_value = MagicMock(returncode=0)

            result = io_ctl.set_disk_io_throttle(
                "test.scope",
                {"default": {"wbps": 50000000, "rbps": 100000000}}
            )
            assert result is True
            # Verify the echo command contains the disk ID and limits
            cmd_args = mock_run.call_args[0][0]
            cmd_str = ' '.join(cmd_args) if isinstance(cmd_args, list) else str(cmd_args)
            assert "8:0" in cmd_str
            assert "wbps=50000000" in cmd_str
            assert "rbps=100000000" in cmd_str

    def test_set_disk_io_throttle_restore(self, io_ctl):
        """Restore mode should set all limits to 'max'."""
        with patch('subprocess.run') as mock_run, \
             patch.object(io_ctl, 'get_disk_id', return_value={"sda": "8:0"}), \
             patch.object(io_ctl, '_get_full_cgroup_path', return_value="/sys/fs/cgroup/test.scope/io.max"), \
             patch('os.path.exists', return_value=True):
            mock_run.return_value = MagicMock(returncode=0)

            result = io_ctl.set_disk_io_throttle(
                "test.scope", {}, is_restore=True
            )
            assert result is True
            cmd_args = mock_run.call_args[0][0]
            cmd_str = ' '.join(cmd_args) if isinstance(cmd_args, list) else str(cmd_args)
            assert "rbps=max" in cmd_str
            assert "wbps=max" in cmd_str

    def test_set_disk_io_throttle_no_disks_returns_false(self, io_ctl):
        """If no disks found, return False."""
        with patch.object(io_ctl, 'get_disk_id', return_value={}):
            result = io_ctl.set_disk_io_throttle("test.scope", {"default": {"wbps": 1000}})
            assert result is False


class TestGovernorController:
    @pytest.fixture
    def gov_ctl(self):
        from controller.governor import GovernorController
        return GovernorController()

    def test_set_performance(self, gov_ctl):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="performance")
            gov_ctl.set_performance()
            mock_run.assert_called()
            call_args = mock_run.call_args[0][0]
            assert "performance" in call_args

    def test_set_powersave(self, gov_ctl):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="powersave")
            gov_ctl.set_powersave()
            mock_run.assert_called()
            call_args = mock_run.call_args[0][0]
            assert "powersave" in call_args


class TestControllerBase:
    def test_abstract_methods_required(self):
        from controller.base import ControllerBase
        with pytest.raises(TypeError):
            ControllerBase("/sys/fs/cgroup/test.scope")

    def test_concrete_subclass(self):
        from controller.base import ControllerBase

        class TestCtl(ControllerBase):
            def controller_type(self):
                return "test"
            def set_parameter(self, name, value):
                pass
            def get_parameter(self, name):
                return "test_value"

        ctl = TestCtl("/sys/fs/cgroup/test.scope")
        assert ctl.controller_type() == "test"
        assert ctl.get_parameter("any") == "test_value"

    def test_get_full_path(self):
        from controller.base import ControllerBase

        class TestCtl(ControllerBase):
            def controller_type(self):
                return "test"
            def set_parameter(self, name, value):
                pass
            def get_parameter(self, name):
                return ""

        ctl = TestCtl("/sys/fs/cgroup")
        assert ctl.get_full_path("my.scope") == "/sys/fs/cgroup/test/my.scope"


class TestControllerResourceQuota:
    """Test the Controller._set_resource_quota logic — the core limiting path."""

    @pytest.fixture
    def controller(self):
        with patch('subprocess.check_output') as mock_check, \
             patch('os.cpu_count', return_value=8):
            mock_check.return_value = "user-1000.slice  loaded active\n"
            from controller.controller import Controller
            ctl = Controller()
            ctl.uid = "1000"
            ctl.cpus = 8
            return ctl

    def test_cpu_quota_validation_rejects_zero(self, controller):
        """cpu_quota=0 should be rejected (valid range 1-100)."""
        with patch.object(controller, 'get_user_scopes', return_value=[]), \
             patch.object(controller, 'get_app_services', return_value=[]):
            # With cpu_quota=0 and no other params, nothing to apply → returns True
            result = controller._set_resource_quota("test.scope", cpu_quota=0)
            assert result is True  # Skips because no valid params

    def test_cpu_quota_validation_rejects_over_100(self, controller):
        """cpu_quota=200 should be rejected."""
        with patch.object(controller, 'get_user_scopes', return_value=[]), \
             patch.object(controller, 'get_app_services', return_value=[]):
            result = controller._set_resource_quota("test.scope", cpu_quota=200)
            assert result is True  # Skips because invalid param is set to None

    def test_mem_high_validation_rejects_negative(self, controller):
        """mem_high=-100 should be rejected."""
        with patch.object(controller, 'get_user_scopes', return_value=[]), \
             patch.object(controller, 'get_app_services', return_value=[]):
            result = controller._set_resource_quota("test.scope", mem_high=-100)
            assert result is True  # Skips

    def test_io_weight_validation_rejects_out_of_range(self, controller):
        """io_weight=99999 should be rejected."""
        with patch.object(controller, 'get_user_scopes', return_value=[]), \
             patch.object(controller, 'get_app_services', return_value=[]):
            result = controller._set_resource_quota("test.scope", io_weight=99999)
            assert result is True  # Skips

    def test_matching_scope_unit(self, controller):
        """A .scope app_id should be found in scopes list."""
        with patch.object(controller, 'get_user_scopes', return_value=["test.scope", "other.scope"]), \
             patch.object(controller, 'get_app_services', return_value=[]), \
             patch('utils.app_utils.get_dbus_address', return_value='unix:path=/run/user/1000/bus'), \
             patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = controller._set_resource_quota("test.scope", cpu_quota=50)
            assert result is True
            # Verify systemctl was called with correct CPUQuota
            cmd = mock_run.call_args[0][0]
            cmd_str = ' '.join(cmd)
            assert "CPUQuota=" in cmd_str

    def test_no_matching_unit_returns_false(self, controller):
        """If app_id doesn't match any scope/service, return False."""
        with patch.object(controller, 'get_user_scopes', return_value=[]), \
             patch.object(controller, 'get_app_services', return_value=[]):
            # A .desktop app with no matching unit
            result = controller._set_resource_quota(
                "org.gnome.Calculator.desktop", cpu_quota=50
            )
            assert result is False


class TestControlManager:
    @pytest.fixture
    def ctrl_mgr(self):
        with patch('controller.controlManager.SystemPressureMonitor') as mock_spm, \
             patch('controller.controlManager.CPUController'), \
             patch('controller.controlManager.MemoryController'), \
             patch('controller.controlManager.GovernorController'), \
             patch('controller.controlManager.Controller'):
            mock_spm_instance = MagicMock()
            mock_spm.return_value = mock_spm_instance
            from controller.controlManager import ControlManager
            mgr = ControlManager()
            return mgr

    def test_has_system_pressure_monitor(self, ctrl_mgr):
        assert ctrl_mgr.system_pressure_monitor is not None

    def test_has_config(self, ctrl_mgr):
        assert ctrl_mgr.config is not None
