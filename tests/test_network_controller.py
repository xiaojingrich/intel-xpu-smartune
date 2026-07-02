# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the NetworkController class in controller/network.py."""

import os
import sys
from unittest.mock import patch, MagicMock, call
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))


def _make_mock_config(enable=True, interface="eth0", bandwidth=1000000):
    """Create a mock b_config with network settings."""
    cfg = MagicMock()
    cfg.enable_network_control = enable
    cfg.network_interface = interface
    cfg.network_bandwidth_kbit = bandwidth
    cfg.config_network_bw = None
    cfg.network_burst_map = None
    cfg.network_thresholds = {"critical": 0.9, "high": 0.7, "low": 0.5}
    cfg.network_system_ports = [22, 53, 80, 443, 123]
    return cfg


class TestNoopNetworkMonitor:
    """Test the _NoopNetworkMonitor fallback class."""

    def test_enabled_is_false(self):
        from controller.network import _NoopNetworkMonitor
        mon = _NoopNetworkMonitor()
        assert mon.enabled is False

    def test_sample_network_pressure_returns_none(self):
        from controller.network import _NoopNetworkMonitor
        mon = _NoopNetworkMonitor()
        assert mon.sample_network_pressure() is None

    def test_get_current_pressure_returns_zero(self):
        from controller.network import _NoopNetworkMonitor
        mon = _NoopNetworkMonitor()
        result = mon.get_current_pressure()
        assert result == {"rx": 0.0, "tx": 0.0}


class TestNetworkControllerInit:
    """Test NetworkController initialization."""

    @pytest.fixture
    def net_ctl_enabled(self):
        """Create a NetworkController with network control enabled (interface exists)."""
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor:
            mock_monitor.return_value = MagicMock()
            from controller.network import NetworkController
            ctl = NetworkController()
            return ctl

    @pytest.fixture
    def net_ctl_disabled(self):
        """Create a NetworkController with network control disabled (interface missing)."""
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=False), \
             patch('controller.network.NetworkMonitor') as mock_monitor:
            from controller.network import NetworkController
            ctl = NetworkController()
            return ctl

    def test_init_with_existing_interface(self, net_ctl_enabled):
        assert net_ctl_enabled.enable_network_control is True
        assert net_ctl_enabled.dev == "eth0"
        assert net_ctl_enabled.handle_id == 50
        assert net_ctl_enabled.total_bw == 1000000

    def test_init_with_missing_interface_disables_control(self, net_ctl_disabled):
        assert net_ctl_disabled.enable_network_control is False

    def test_init_with_missing_interface_uses_noop_monitor(self, net_ctl_disabled):
        from controller.network import _NoopNetworkMonitor
        assert isinstance(net_ctl_disabled.network, _NoopNetworkMonitor)

    def test_init_sets_default_bandwidth_config(self, net_ctl_enabled):
        """When config_network_bw is None, defaults are computed from total_bw."""
        bw = net_ctl_enabled.config_network_bw
        assert "critical" in bw
        assert "high" in bw
        assert "low" in bw
        assert "system" in bw
        # Check critical min/max are derived from total_bw
        assert bw["critical"]["min"] == int(1000000 * 0.6)
        assert bw["critical"]["max"] == int(1000000 * 0.9)

    def test_init_mark_pool_size(self, net_ctl_enabled):
        """Mark pool should contain 0x1000 entries (0x1000 to 0x1FFF)."""
        assert len(net_ctl_enabled.mark_pool) == 0x1000

    def test_init_default_interface_fallback(self):
        """When network_interface is None, default to enp1s0."""
        mock_cfg = _make_mock_config(enable=True, interface=None, bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor:
            mock_monitor.return_value = MagicMock()
            from controller.network import NetworkController
            ctl = NetworkController()
            assert ctl.dev == "enp1s0"


class TestMarkAllocation:
    """Test mark pool allocation and release."""

    @pytest.fixture
    def net_ctl(self):
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor:
            mock_monitor.return_value = MagicMock()
            from controller.network import NetworkController
            ctl = NetworkController()
            return ctl

    def test_allocate_mark_returns_hex_string(self, net_ctl):
        mark = net_ctl._allocate_mark()
        # Should be a hex string in the pool range
        assert mark.startswith("0x")
        val = int(mark, 16)
        assert 0x1000 <= val <= 0x1FFF

    def test_allocate_mark_decreases_pool(self, net_ctl):
        initial_size = len(net_ctl.mark_pool)
        net_ctl._allocate_mark()
        assert len(net_ctl.mark_pool) == initial_size - 1

    def test_release_mark_increases_pool(self, net_ctl):
        mark = net_ctl._allocate_mark()
        size_after_alloc = len(net_ctl.mark_pool)
        net_ctl._release_mark(mark)
        assert len(net_ctl.mark_pool) == size_after_alloc + 1

    def test_release_mark_returns_to_pool(self, net_ctl):
        mark = net_ctl._allocate_mark()
        assert mark not in net_ctl.mark_pool
        net_ctl._release_mark(mark)
        assert mark in net_ctl.mark_pool

    def test_allocate_mark_when_pool_empty(self, net_ctl):
        """When pool is exhausted, should generate marks beyond 0x2000."""
        net_ctl.mark_pool = set()
        net_ctl.app_mark_map = {"app1": "0x1000", "app2": "0x1001"}
        mark = net_ctl._allocate_mark()
        val = int(mark, 16)
        # 0x2000 + len(app_mark_map) = 0x2002
        assert val == 0x2000 + 2


class TestGetClassid:
    """Test _get_classid mapping."""

    @pytest.fixture
    def net_ctl(self):
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor:
            mock_monitor.return_value = MagicMock()
            from controller.network import NetworkController
            ctl = NetworkController()
            return ctl

    def test_critical_priority(self, net_ctl):
        assert net_ctl._get_classid(50, "critical") == "50:10"

    def test_high_priority(self, net_ctl):
        assert net_ctl._get_classid(50, "high") == "50:20"

    def test_low_priority(self, net_ctl):
        assert net_ctl._get_classid(50, "low") == "50:30"

    def test_system_priority(self, net_ctl):
        assert net_ctl._get_classid(50, "system") == "50:5"

    def test_unknown_priority_defaults_to_30(self, net_ctl):
        assert net_ctl._get_classid(50, "unknown") == "50:30"

    def test_different_handle(self, net_ctl):
        assert net_ctl._get_classid(51, "critical") == "51:10"
        assert net_ctl._get_classid(99, "high") == "99:20"


class TestGetClassBandwidth:
    """Test _get_class_bandwidth returns correct min/max from config."""

    @pytest.fixture
    def net_ctl(self):
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor:
            mock_monitor.return_value = MagicMock()
            from controller.network import NetworkController
            ctl = NetworkController()
            return ctl

    def test_critical_bandwidth(self, net_ctl):
        min_bw, max_bw = net_ctl._get_class_bandwidth("critical")
        assert min_bw == int(1000000 * 0.6)
        assert max_bw == int(1000000 * 0.9)

    def test_high_bandwidth(self, net_ctl):
        min_bw, max_bw = net_ctl._get_class_bandwidth("high")
        assert min_bw == int(1000000 * 0.3)
        assert max_bw == int(1000000 * 0.8)

    def test_low_bandwidth(self, net_ctl):
        min_bw, max_bw = net_ctl._get_class_bandwidth("low")
        assert min_bw == int(1000000 * 0.1)
        assert max_bw == int(1000000 * 0.3)

    def test_system_bandwidth(self, net_ctl):
        min_bw, max_bw = net_ctl._get_class_bandwidth("system")
        assert min_bw == 50000
        assert max_bw == 100000

    def test_unknown_priority_returns_zeros(self, net_ctl):
        min_bw, max_bw = net_ctl._get_class_bandwidth("nonexistent")
        assert min_bw == 0
        assert max_bw == 0


class TestGetAllClassids:
    """Test _get_all_classids for both directions."""

    @pytest.fixture
    def net_ctl(self):
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor:
            mock_monitor.return_value = MagicMock()
            from controller.network import NetworkController
            ctl = NetworkController()
            return ctl

    def test_egress_direction_uses_handle_as_is(self, net_ctl):
        result = net_ctl._get_all_classids(50, direction="egress")
        assert result == ["50:10", "50:20", "50:30", "50:5"]

    def test_ingress_direction_increments_handle(self, net_ctl):
        result = net_ctl._get_all_classids(50, direction="ingress")
        assert result == ["51:10", "51:20", "51:30", "51:5"]

    def test_custom_priorities(self, net_ctl):
        result = net_ctl._get_all_classids(50, priorities=["high", "low"], direction="egress")
        assert result == ["50:20", "50:30"]

    def test_default_priorities_include_all(self, net_ctl):
        result = net_ctl._get_all_classids(50, direction="egress")
        assert len(result) == 4


class TestGetRates:
    """Test get_rates combines egress and ingress rates correctly."""

    @pytest.fixture
    def net_ctl(self):
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor:
            mock_monitor.return_value = MagicMock()
            from controller.network import NetworkController
            ctl = NetworkController()
            return ctl

    def test_get_rates_with_matching_data(self, net_ctl):
        egress_rates = {
            "50:10": 500000,  # critical
            "50:20": 300000,  # high
            "50:30": 100000,  # low
            "50:5": 50000,    # system
        }
        ingress_rates = {
            "51:10": 450000,  # critical
            "51:20": 250000,  # high
            "51:30": 80000,   # low
            "51:5": 40000,    # system
        }
        result = net_ctl.get_rates(50, egress_rates, ingress_rates)
        assert result["egress_critical"] == 500000
        assert result["egress_high"] == 300000
        assert result["egress_low"] == 100000
        assert result["egress_system"] == 50000
        assert result["ingress_critical"] == 450000
        assert result["ingress_high"] == 250000
        assert result["ingress_low"] == 80000
        assert result["ingress_system"] == 40000

    def test_get_rates_with_missing_data_returns_zero(self, net_ctl):
        result = net_ctl.get_rates(50, {}, {})
        assert result["egress_critical"] == 0
        assert result["egress_high"] == 0
        assert result["egress_low"] == 0
        assert result["egress_system"] == 0
        assert result["ingress_critical"] == 0
        assert result["ingress_high"] == 0
        assert result["ingress_low"] == 0
        assert result["ingress_system"] == 0


class TestCanSwitch:
    """Test _can_switch cooldown logic."""

    @pytest.fixture
    def net_ctl(self):
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor:
            mock_monitor.return_value = MagicMock()
            from controller.network import NetworkController
            ctl = NetworkController()
            return ctl

    def test_can_switch_when_both_cooldowns_elapsed(self, net_ctl):
        now = time.time()
        cooldown = 10
        last_limit_time = now - 20   # 20 seconds ago
        last_recover_time = now - 20  # 20 seconds ago
        assert net_ctl._can_switch(cooldown, last_limit_time, last_recover_time) is True

    def test_cannot_switch_when_limit_too_recent(self, net_ctl):
        now = time.time()
        cooldown = 10
        last_limit_time = now - 5    # only 5 seconds ago
        last_recover_time = now - 20  # 20 seconds ago
        assert net_ctl._can_switch(cooldown, last_limit_time, last_recover_time) is False

    def test_cannot_switch_when_recover_too_recent(self, net_ctl):
        now = time.time()
        cooldown = 10
        last_limit_time = now - 20   # 20 seconds ago
        last_recover_time = now - 5   # only 5 seconds ago
        assert net_ctl._can_switch(cooldown, last_limit_time, last_recover_time) is False

    def test_cannot_switch_when_both_too_recent(self, net_ctl):
        now = time.time()
        cooldown = 10
        last_limit_time = now - 3
        last_recover_time = now - 3
        assert net_ctl._can_switch(cooldown, last_limit_time, last_recover_time) is False

    def test_can_switch_with_zero_timestamps(self, net_ctl):
        """When last times are 0 (initial state), cooldown is always exceeded."""
        cooldown = 10
        assert net_ctl._can_switch(cooldown, 0, 0) is True


class TestSetupTcClassesAndFilters:
    """Test setup_tc_classes_and_filters."""

    def test_disabled_network_control_returns_early(self):
        """When enable_network_control=False, no subprocess calls should be made."""
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=False), \
             patch('controller.network.NetworkMonitor'), \
             patch('controller.network.subprocess.run') as mock_run:
            from controller.network import NetworkController
            ctl = NetworkController()
            # Interface doesn't exist, so enable_network_control = False
            assert ctl.enable_network_control is False
            ctl.setup_tc_classes_and_filters()
            mock_run.assert_not_called()

    def test_enabled_network_control_calls_subprocess(self):
        """When enabled, subprocess.run should be called for TC setup."""
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor, \
             patch('controller.network.subprocess.run') as mock_run:
            mock_monitor.return_value = MagicMock()
            mock_run.return_value = MagicMock(returncode=0)
            from controller.network import NetworkController
            ctl = NetworkController()
            ctl.setup_tc_classes_and_filters()
            # There should be many subprocess calls for TC setup
            assert mock_run.call_count > 10

    def test_setup_creates_root_qdisc(self):
        """Verify root HTB qdisc is created on the device."""
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor, \
             patch('controller.network.subprocess.run') as mock_run:
            mock_monitor.return_value = MagicMock()
            mock_run.return_value = MagicMock(returncode=0)
            from controller.network import NetworkController
            ctl = NetworkController()
            ctl.setup_tc_classes_and_filters()
            # Check that 'tc qdisc add dev eth0 root handle 50: htb' was called
            all_calls = [c[0][0] for c in mock_run.call_args_list]
            root_qdisc_calls = [c for c in all_calls if "qdisc" in c and "add" in c and "root" in c and "eth0" in c]
            assert len(root_qdisc_calls) >= 1

    def test_setup_creates_ifb_device(self):
        """Verify IFB device is set up for ingress redirect."""
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor, \
             patch('controller.network.subprocess.run') as mock_run:
            mock_monitor.return_value = MagicMock()
            mock_run.return_value = MagicMock(returncode=0)
            from controller.network import NetworkController
            ctl = NetworkController()
            ctl.setup_tc_classes_and_filters()
            all_calls = [c[0][0] for c in mock_run.call_args_list]
            # modprobe ifb should be called
            modprobe_calls = [c for c in all_calls if "modprobe" in c and "ifb" in c]
            assert len(modprobe_calls) == 1
            # ip link set ifb0 up should be called
            link_up_calls = [c for c in all_calls if "ip" in c and "link" in c and "set" in c and "ifb0" in c]
            assert len(link_up_calls) == 1

    def test_setup_populates_classid_lists(self):
        """After setup, egress_classids and ingress_classids should be populated."""
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor, \
             patch('controller.network.subprocess.run') as mock_run:
            mock_monitor.return_value = MagicMock()
            mock_run.return_value = MagicMock(returncode=0)
            from controller.network import NetworkController
            ctl = NetworkController()
            ctl.setup_tc_classes_and_filters()
            assert len(ctl.egress_classids) == 4
            assert len(ctl.ingress_classids) == 4
            assert "50:10" in ctl.egress_classids
            assert "51:10" in ctl.ingress_classids


class TestAddAppNetworkRules:
    """Test _add_app_network_rules."""

    @pytest.fixture
    def net_ctl(self):
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor:
            mock_monitor.return_value = MagicMock()
            from controller.network import NetworkController
            ctl = NetworkController()
            return ctl

    def test_system_priority_no_mark_allocated(self, net_ctl):
        """For system priority, no mark should be allocated from the pool."""
        initial_pool_size = len(net_ctl.mark_pool)
        app = {"app_id": "sys_app", "priority": "system", "cgroup_path": "/sys/fs/cgroup/system.scope"}
        with patch('controller.network.subprocess.run'):
            net_ctl._add_app_network_rules(app, 0)
        # Pool size should not decrease
        assert len(net_ctl.mark_pool) == initial_pool_size
        # app_mark_map should not have an entry for this app
        assert "sys_app" not in net_ctl.app_mark_map
        # But filter_info should still be recorded
        assert "sys_app" in net_ctl.app_filter_info
        assert net_ctl.app_filter_info["sys_app"]["mark"] is None

    def test_non_system_priority_allocates_mark(self, net_ctl):
        """For non-system priorities, a mark should be allocated."""
        initial_pool_size = len(net_ctl.mark_pool)
        app = {"app_id": "user_app", "priority": "high", "cgroup_path": "/sys/fs/cgroup/user.scope"}
        with patch('controller.network.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            net_ctl._add_app_network_rules(app, 0)
        assert len(net_ctl.mark_pool) == initial_pool_size - 1
        assert "user_app" in net_ctl.app_mark_map

    def test_non_system_priority_calls_iptables_and_tc(self, net_ctl):
        """For non-system priority with cgroup_path, iptables and tc filter calls are made."""
        app = {"app_id": "user_app", "priority": "low", "cgroup_path": "/sys/fs/cgroup/user.scope"}
        with patch('controller.network.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            net_ctl._add_app_network_rules(app, 0)
        # Should have 3 subprocess calls: iptables mark, tc filter egress, tc filter ifb
        assert mock_run.call_count == 3
        all_calls = [c[0][0] for c in mock_run.call_args_list]
        # First call should be iptables
        assert "iptables" in all_calls[0]
        # Second and third should be tc filter add
        assert "tc" in all_calls[1] and "filter" in all_calls[1]
        assert "tc" in all_calls[2] and "filter" in all_calls[2]

    def test_non_system_without_cgroup_skips_iptables(self, net_ctl):
        """Without cgroup_path, iptables call should be skipped."""
        app = {"app_id": "user_app", "priority": "high", "cgroup_path": None}
        with patch('controller.network.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            net_ctl._add_app_network_rules(app, 0)
        # Only 2 calls: tc filter for egress and ifb (no iptables)
        assert mock_run.call_count == 2

    def test_filter_info_stored_correctly(self, net_ctl):
        """Verify filter info is stored with correct classids and prio values."""
        app = {"app_id": "my_app", "priority": "critical", "cgroup_path": "/cgroup/test"}
        with patch('controller.network.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            net_ctl._add_app_network_rules(app, 3)
        info = net_ctl.app_filter_info["my_app"]
        assert info["classid_egress"] == "50:10"
        assert info["classid_ifb"] == "51:10"
        assert info["prio_egress"] == 13  # 10 + idx
        assert info["prio_ifb"] == 24     # 21 + idx
        assert info["priority"] == "critical"
        assert info["cgroup_path"] == "/cgroup/test"


class TestRemoveAppNetworkRules:
    """Test _remove_app_network_rules."""

    @pytest.fixture
    def net_ctl_with_app(self):
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor:
            mock_monitor.return_value = MagicMock()
            from controller.network import NetworkController
            ctl = NetworkController()
            # Add an app first
            app = {"app_id": "test_app", "priority": "high", "cgroup_path": "/cgroup/app"}
            with patch('controller.network.subprocess.run'):
                ctl._add_app_network_rules(app, 0)
            return ctl

    def test_remove_releases_mark(self, net_ctl_with_app):
        mark = net_ctl_with_app.app_mark_map["test_app"]
        assert mark not in net_ctl_with_app.mark_pool
        with patch('controller.network.subprocess.run'):
            net_ctl_with_app._remove_app_network_rules("test_app")
        assert mark in net_ctl_with_app.mark_pool

    def test_remove_clears_filter_info(self, net_ctl_with_app):
        with patch('controller.network.subprocess.run'):
            net_ctl_with_app._remove_app_network_rules("test_app")
        assert "test_app" not in net_ctl_with_app.app_filter_info
        assert "test_app" not in net_ctl_with_app.app_mark_map

    def test_remove_calls_iptables_delete(self, net_ctl_with_app):
        with patch('controller.network.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            net_ctl_with_app._remove_app_network_rules("test_app")
        all_calls = [c[0][0] for c in mock_run.call_args_list]
        # Should have iptables -D call
        iptables_calls = [c for c in all_calls if "iptables" in c and "-D" in c]
        assert len(iptables_calls) == 1

    def test_remove_calls_tc_filter_del(self, net_ctl_with_app):
        with patch('controller.network.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            net_ctl_with_app._remove_app_network_rules("test_app")
        all_calls = [c[0][0] for c in mock_run.call_args_list]
        tc_del_calls = [c for c in all_calls if "tc" in c and "filter" in c and "del" in c]
        assert len(tc_del_calls) == 2  # one for dev, one for ifb

    def test_remove_nonexistent_app_does_nothing(self, net_ctl_with_app):
        with patch('controller.network.subprocess.run') as mock_run:
            net_ctl_with_app._remove_app_network_rules("nonexistent_app")
        mock_run.assert_not_called()


class TestUpdateAppNetworkControl:
    """Test update_app_network_control adds/removes rules as controlled apps change."""

    @pytest.fixture
    def net_ctl(self):
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor:
            mock_monitor.return_value = MagicMock()
            from controller.network import NetworkController
            ctl = NetworkController()
            return ctl

    def test_adds_new_app(self, net_ctl):
        """New apps should get network rules added."""
        controlled_apps = [
            {"app_id": "app1", "priority": "high", "cgroup_path": "/cgroup/app1"}
        ]
        with patch('controller.network.app_utils.get_controlled_apps_net', return_value=controlled_apps), \
             patch('controller.network.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            net_ctl.update_app_network_control()
        assert "app1" in net_ctl.app_filter_info

    def test_removes_old_app(self, net_ctl):
        """Apps no longer in controlled list should be removed."""
        # First add an app
        app = {"app_id": "old_app", "priority": "low", "cgroup_path": "/cgroup/old"}
        with patch('controller.network.subprocess.run'):
            net_ctl._add_app_network_rules(app, 0)
        assert "old_app" in net_ctl.app_filter_info
        # Now update with empty list
        with patch('controller.network.app_utils.get_controlled_apps_net', return_value=[]), \
             patch('controller.network.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            net_ctl.update_app_network_control()
        assert "old_app" not in net_ctl.app_filter_info

    def test_priority_change_triggers_remove_and_readd(self, net_ctl):
        """If an app's priority changes, it should be removed and re-added."""
        app = {"app_id": "changing_app", "priority": "low", "cgroup_path": "/cgroup/app"}
        with patch('controller.network.subprocess.run'):
            net_ctl._add_app_network_rules(app, 0)
        assert net_ctl.app_filter_info["changing_app"]["priority"] == "low"
        # Now update with different priority
        controlled_apps = [
            {"app_id": "changing_app", "priority": "high", "cgroup_path": "/cgroup/app"}
        ]
        with patch('controller.network.app_utils.get_controlled_apps_net', return_value=controlled_apps), \
             patch('controller.network.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            net_ctl.update_app_network_control()
        # Should now have "high" priority
        assert net_ctl.app_filter_info["changing_app"]["priority"] == "high"


class TestClearNetworkRulesOnExit:
    """Test clear_network_rules_on_exit."""

    def test_disabled_control_skips_cleanup(self):
        """When disabled, no subprocess calls for cleanup."""
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=False), \
             patch('controller.network.NetworkMonitor'), \
             patch('controller.network.subprocess.run') as mock_run:
            from controller.network import NetworkController
            ctl = NetworkController()
            ctl.clear_network_rules_on_exit()
            mock_run.assert_not_called()

    def test_enabled_control_deletes_qdiscs(self):
        """When enabled, should delete TC qdiscs."""
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor, \
             patch('controller.network.subprocess.run') as mock_run:
            mock_monitor.return_value = MagicMock()
            mock_run.return_value = MagicMock(returncode=0)
            from controller.network import NetworkController
            ctl = NetworkController()
            ctl.clear_network_rules_on_exit()
            all_calls = [c[0][0] for c in mock_run.call_args_list]
            qdisc_del_calls = [c for c in all_calls if "qdisc" in c and "del" in c]
            assert len(qdisc_del_calls) == 3  # dev root, ifb root, dev ingress

    def test_enabled_control_cleans_iptables_marks(self):
        """When apps have marks, iptables rules should be cleaned up."""
        mock_cfg = _make_mock_config(enable=True, interface="eth0", bandwidth=1000000)
        with patch('controller.network.b_config', mock_cfg), \
             patch('controller.network.os.path.exists', return_value=True), \
             patch('controller.network.NetworkMonitor') as mock_monitor, \
             patch('controller.network.subprocess.run') as mock_run:
            mock_monitor.return_value = MagicMock()
            mock_run.return_value = MagicMock(returncode=0)
            from controller.network import NetworkController
            ctl = NetworkController()
            # Simulate an app with mark
            ctl.app_filter_info["app1"] = {
                "mark": "0x1000",
                "cgroup_path": "/cgroup/app1"
            }
            ctl.clear_network_rules_on_exit()
            all_calls = [c[0][0] for c in mock_run.call_args_list]
            iptables_del = [c for c in all_calls if "iptables" in c and "-D" in c]
            assert len(iptables_del) == 1
