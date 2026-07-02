# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for monitor/network.py — WindowDiffHistory and NetworkMonitor."""

import os
import sys
import time
from unittest.mock import patch, MagicMock, mock_open

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))

from monitor.network import WindowDiffHistory, NetworkMonitor


# ---------------------------------------------------------------------------
# WindowDiffHistory tests
# ---------------------------------------------------------------------------

class TestWindowDiffHistory:

    def test_add_stores_entry_with_timestamp(self):
        with patch('monitor.network.time.time', return_value=100.0):
            h = WindowDiffHistory(window_sec=5)
            h.add(10, 20)
        assert len(h._history) == 1
        assert h._history[0] == (100.0, 10, 20)

    def test_add_multiple_entries(self):
        h = WindowDiffHistory(window_sec=10)
        with patch('monitor.network.time.time', return_value=100.0):
            h.add(1)
        with patch('monitor.network.time.time', return_value=101.0):
            h.add(2)
        with patch('monitor.network.time.time', return_value=102.0):
            h.add(3)
        assert len(h._history) == 3

    def test_clean_removes_old_entries(self):
        h = WindowDiffHistory(window_sec=5)
        # Add an old entry
        with patch('monitor.network.time.time', return_value=100.0):
            h.add(1)
        # Add a recent entry — clean should remove the old one
        with patch('monitor.network.time.time', return_value=106.0):
            h.add(2)
        assert len(h._history) == 1
        assert h._history[0] == (106.0, 2)

    def test_clean_keeps_entries_within_window(self):
        h = WindowDiffHistory(window_sec=5)
        with patch('monitor.network.time.time', return_value=100.0):
            h.add(1)
        with patch('monitor.network.time.time', return_value=103.0):
            h.add(2)
        # Both entries are within 5 seconds of t=103
        assert len(h._history) == 2

    def test_diff_rate_empty_history_returns_zero(self):
        h = WindowDiffHistory(window_sec=5)
        assert h.diff_rate(1, 0) == 0.0

    def test_diff_rate_single_entry_returns_zero(self):
        h = WindowDiffHistory(window_sec=5)
        with patch('monitor.network.time.time', return_value=100.0):
            h.add(500)
        assert h.diff_rate(1, 0) == 0.0

    def test_diff_rate_normal_calculation(self):
        h = WindowDiffHistory(window_sec=10)
        # Entry at t=100 with value 1000
        h._history = [(100.0, 1000), (102.0, 3000)]
        # diff_rate(num_idx=1, denom_idx=0): (3000-1000) / (102-100) = 2000/2 = 1000
        rate = h.diff_rate(1, 0)
        assert rate == 1000.0

    def test_diff_rate_zero_denominator_returns_zero(self):
        h = WindowDiffHistory(window_sec=10)
        # Same timestamp means delta_denom = 0
        h._history = [(100.0, 1000), (100.0, 3000)]
        rate = h.diff_rate(1, 0)
        assert rate == 0.0

    def test_diff_rate_multiple_fields(self):
        h = WindowDiffHistory(window_sec=10, fields=["bytes", "time"])
        # (timestamp, bytes, time)
        h._history = [(10.0, 100, 50), (12.0, 500, 150)]
        # diff_rate(1, 2): (500-100)/(150-50) = 400/100 = 4.0
        rate = h.diff_rate(1, 2)
        assert rate == 4.0

    def test_fields_stored(self):
        h = WindowDiffHistory(window_sec=5, fields=["packets", "errors"])
        assert h.fields == ["packets", "errors"]

    def test_fields_default_empty(self):
        h = WindowDiffHistory(window_sec=5)
        assert h.fields == []


# ---------------------------------------------------------------------------
# NetworkMonitor tests
# ---------------------------------------------------------------------------

class TestNetworkMonitorGetNetBytes:

    def test_reads_rx_and_tx_bytes(self):
        monitor = NetworkMonitor(interface="eth0", bandwidth_kbit=1000000)
        rx_data = "123456\n"
        tx_data = "789012\n"

        def mock_file_open(path, *args, **kwargs):
            if "rx_bytes" in path:
                return mock_open(read_data=rx_data)()
            elif "tx_bytes" in path:
                return mock_open(read_data=tx_data)()
            raise FileNotFoundError(path)

        with patch('builtins.open', side_effect=mock_file_open):
            rx, tx = monitor._get_net_bytes()

        assert rx == 123456
        assert tx == 789012

    def test_correct_sysfs_paths(self):
        monitor = NetworkMonitor(interface="enp1s0")
        expected_rx = "/sys/class/net/enp1s0/statistics/rx_bytes"
        expected_tx = "/sys/class/net/enp1s0/statistics/tx_bytes"

        opened_paths = []

        def mock_file_open(path, *args, **kwargs):
            opened_paths.append(path)
            return mock_open(read_data="0\n")()

        with patch('builtins.open', side_effect=mock_file_open):
            monitor._get_net_bytes()

        assert expected_rx in opened_paths
        assert expected_tx in opened_paths

    def test_raises_runtime_error_on_failure(self):
        monitor = NetworkMonitor(interface="nonexistent")
        with patch('builtins.open', side_effect=FileNotFoundError("No such file")):
            with pytest.raises(RuntimeError, match="Failed to read NIC bytes"):
                monitor._get_net_bytes()


class TestNetworkMonitorUpdatePressure:

    def test_first_call_returns_none(self):
        monitor = NetworkMonitor(interface="eth0", bandwidth_kbit=1000000)
        with patch.object(monitor, '_get_net_bytes', return_value=(1000, 2000)):
            with patch('monitor.network.time.time', return_value=100.0):
                rx_p, tx_p = monitor._update_pressure()
        assert rx_p is None
        assert tx_p is None

    def test_second_call_computes_pressure(self):
        monitor = NetworkMonitor(interface="eth0", bandwidth_kbit=1000000)
        # First call: seed baseline
        with patch.object(monitor, '_get_net_bytes', return_value=(0, 0)):
            with patch('monitor.network.time.time', return_value=100.0):
                monitor._update_pressure()

        # Second call: 125000 bytes rx in 1 second = 1000 kbit/s
        # pressure = 1000 / 1000000 = 0.001
        with patch.object(monitor, '_get_net_bytes', return_value=(125000, 0)):
            with patch('monitor.network.time.time', return_value=101.0):
                rx_p, tx_p = monitor._update_pressure()

        # rx: 125000 bytes * 8 / 1000 / 1 sec = 1000 kbit/s
        # pressure = 1000 / 1000000 = 0.001
        assert rx_p == pytest.approx(0.001)
        assert tx_p == pytest.approx(0.0)

    def test_pressure_clamped_to_one(self):
        monitor = NetworkMonitor(interface="eth0", bandwidth_kbit=1000)  # 1 Mbit NIC
        # First call
        with patch.object(monitor, '_get_net_bytes', return_value=(0, 0)):
            with patch('monitor.network.time.time', return_value=100.0):
                monitor._update_pressure()

        # Very high transfer: should exceed bandwidth, gets clamped to 1.0
        # 1,000,000 bytes in 1 second = 8000 kbit/s, bandwidth=1000 kbit/s -> ratio=8.0 -> clamped to 1.0
        with patch.object(monitor, '_get_net_bytes', return_value=(1000000, 1000000)):
            with patch('monitor.network.time.time', return_value=101.0):
                rx_p, tx_p = monitor._update_pressure()

        assert rx_p == 1.0
        assert tx_p == 1.0

    def test_pressure_zero_when_no_traffic(self):
        monitor = NetworkMonitor(interface="eth0", bandwidth_kbit=1000000)
        with patch.object(monitor, '_get_net_bytes', return_value=(5000, 3000)):
            with patch('monitor.network.time.time', return_value=100.0):
                monitor._update_pressure()

        # Same bytes in second call -> zero delta
        with patch.object(monitor, '_get_net_bytes', return_value=(5000, 3000)):
            with patch('monitor.network.time.time', return_value=101.0):
                rx_p, tx_p = monitor._update_pressure()

        assert rx_p == 0.0
        assert tx_p == 0.0

    def test_pressure_zero_when_zero_delta_time(self):
        monitor = NetworkMonitor(interface="eth0", bandwidth_kbit=1000000)
        with patch.object(monitor, '_get_net_bytes', return_value=(0, 0)):
            with patch('monitor.network.time.time', return_value=100.0):
                monitor._update_pressure()

        # Same timestamp -> delta_time = 0
        with patch.object(monitor, '_get_net_bytes', return_value=(5000, 5000)):
            with patch('monitor.network.time.time', return_value=100.0):
                rx_p, tx_p = monitor._update_pressure()

        assert rx_p == 0.0
        assert tx_p == 0.0


class TestNetworkMonitorGetCurrentPressure:

    def test_returns_zero_when_no_history(self):
        monitor = NetworkMonitor(interface="eth0")
        result = monitor.get_current_pressure()
        assert result == {'rx': 0.0, 'tx': 0.0}

    def test_returns_average_of_history(self):
        monitor = NetworkMonitor(interface="eth0")
        # Inject pressure history directly
        monitor._pressure_history_rx = [(100.0, 0.2), (101.0, 0.4), (102.0, 0.6)]
        monitor._pressure_history_tx = [(100.0, 0.1), (101.0, 0.3), (102.0, 0.5)]
        result = monitor.get_current_pressure()
        assert result['rx'] == pytest.approx(0.4)  # (0.2+0.4+0.6)/3
        assert result['tx'] == pytest.approx(0.3)  # (0.1+0.3+0.5)/3

    def test_returns_dict_with_rx_and_tx_keys(self):
        monitor = NetworkMonitor(interface="eth0")
        result = monitor.get_current_pressure()
        assert 'rx' in result
        assert 'tx' in result


class TestNetworkMonitorSamplePressure:

    def test_sample_calls_update_pressure(self):
        monitor = NetworkMonitor(interface="eth0")
        with patch.object(monitor, '_update_pressure', return_value=(0.5, 0.3)) as mock_update:
            monitor.sample_network_pressure()
        mock_update.assert_called_once()


class TestNetworkMonitorTcClassStats:

    def test_get_tc_class_stats_parses_output(self):
        monitor = NetworkMonitor(interface="eth0")
        tc_output = (
            "class htb 1:10 root prio 0 rate 100Mbit ceil 100Mbit burst 1600b cburst 1600b\n"
            " Sent 123456 bytes 100 pkt (dropped 0, overlimits 0 requeues 0)\n"
            "class htb 1:20 root prio 0 rate 200Mbit ceil 200Mbit burst 1600b cburst 1600b\n"
            " Sent 789012 bytes 200 pkt (dropped 0, overlimits 0 requeues 0)\n"
        )
        mock_result = MagicMock()
        mock_result.stdout = tc_output

        with patch('monitor.network.subprocess.run', return_value=mock_result) as mock_run:
            usage = monitor.get_tc_class_stats("eth0", 1, ["1:10", "1:20"], direction="egress")

        mock_run.assert_called_once_with(
            ["tc", "-s", "class", "show", "dev", "eth0", "parent", "1:"],
            capture_output=True,
            text=True,
            check=False
        )
        assert usage == {"1:10": 123456, "1:20": 789012}

    def test_get_tc_class_stats_missing_classid(self):
        monitor = NetworkMonitor(interface="eth0")
        tc_output = (
            "class htb 1:10 root prio 0 rate 100Mbit\n"
            " Sent 5000 bytes 10 pkt\n"
        )
        mock_result = MagicMock()
        mock_result.stdout = tc_output

        with patch('monitor.network.subprocess.run', return_value=mock_result):
            usage = monitor.get_tc_class_stats("eth0", 1, ["1:10", "1:99"], direction="ingress")

        assert usage == {"1:10": 5000}
        assert "1:99" not in usage

    def test_get_tc_class_stats_updates_history(self):
        monitor = NetworkMonitor(interface="eth0")
        tc_output = "class htb 1:10 root\n Sent 1000 bytes 5 pkt\n"
        mock_result = MagicMock()
        mock_result.stdout = tc_output

        with patch('monitor.network.subprocess.run', return_value=mock_result):
            with patch('monitor.network.time.time', return_value=200.0):
                monitor.get_tc_class_stats("eth0", 1, ["1:10"], direction="egress")

        assert hasattr(monitor, '_tc_stats_history_egress')
        assert "1:10" in monitor._tc_stats_history_egress
        assert len(monitor._tc_stats_history_egress["1:10"]._history) == 1


class TestNetworkMonitorTcRateCalculations:

    def test_rate_egress_with_two_samples(self):
        monitor = NetworkMonitor(interface="eth0")
        monitor._init_tc_stats_history(window_sec=10)

        # Simulate two samples: 1000 bytes at t=100, 2000 bytes at t=101
        history = WindowDiffHistory(window_sec=10, fields=["bytes"])
        history._history = [(100.0, 1000), (101.0, 2000)]
        monitor._tc_stats_history_egress["1:10"] = history

        rates = monitor.get_tc_class_stats_rate_egress()
        # delta_bytes=1000, delta_time=1, rate = 1000*8/1000/1 = 8.0 kbit/s
        assert rates["1:10"] == pytest.approx(8.0)

    def test_rate_ingress_with_two_samples(self):
        monitor = NetworkMonitor(interface="eth0")
        monitor._init_tc_stats_history(window_sec=10)

        history = WindowDiffHistory(window_sec=10, fields=["bytes"])
        history._history = [(50.0, 5000), (55.0, 30000)]
        monitor._tc_stats_history_ingress["1:20"] = history

        rates = monitor.get_tc_class_stats_rate_ingress()
        # delta_bytes=25000, delta_time=5, rate = 25000*8/1000/5 = 40.0 kbit/s
        assert rates["1:20"] == pytest.approx(40.0)

    def test_rate_returns_zero_with_single_sample(self):
        monitor = NetworkMonitor(interface="eth0")
        monitor._init_tc_stats_history(window_sec=10)

        history = WindowDiffHistory(window_sec=10, fields=["bytes"])
        history._history = [(100.0, 1000)]
        monitor._tc_stats_history_egress["1:10"] = history

        rates = monitor.get_tc_class_stats_rate_egress()
        assert rates["1:10"] == 0.0

    def test_rate_returns_empty_dict_without_init(self):
        monitor = NetworkMonitor(interface="eth0")
        # No _init_tc_stats_history called, no attribute
        rates = monitor.get_tc_class_stats_rate_egress()
        assert rates == {}

    def test_rate_zero_delta_time(self):
        monitor = NetworkMonitor(interface="eth0")
        monitor._init_tc_stats_history(window_sec=10)

        history = WindowDiffHistory(window_sec=10, fields=["bytes"])
        history._history = [(100.0, 1000), (100.0, 5000)]
        monitor._tc_stats_history_egress["1:10"] = history

        rates = monitor.get_tc_class_stats_rate_egress()
        assert rates["1:10"] == 0.0


class TestNetworkMonitorCleanOldData:

    def test_clean_removes_expired_pressure_entries(self):
        monitor = NetworkMonitor(interface="eth0")
        # Add old entries
        monitor._pressure_history_rx = [(90.0, 0.5), (95.0, 0.3)]
        monitor._pressure_history_tx = [(90.0, 0.4), (95.0, 0.2)]
        monitor._last_rx = 1000  # needed to trigger sentinel append

        with patch('monitor.network.time.time', return_value=100.0):
            monitor._clean_old_data()

        # Entries at t=90 and t=95 are older than cutoff (100-5=95)
        # Only t=95 is >= 95 so it stays
        assert len(monitor._pressure_history_rx) == 1
        assert monitor._pressure_history_rx[0] == (95.0, 0.3)

    def test_clean_adds_sentinel_when_all_expired(self):
        monitor = NetworkMonitor(interface="eth0")
        monitor._pressure_history_rx = [(80.0, 0.9)]
        monitor._pressure_history_tx = [(80.0, 0.8)]
        monitor._last_rx = 1000
        monitor._last_tx = 2000

        with patch('monitor.network.time.time', return_value=100.0):
            monitor._clean_old_data()

        # All entries expired, sentinel added at cutoff+0.1 = 95.1
        assert len(monitor._pressure_history_rx) == 1
        assert monitor._pressure_history_rx[0] == (95.1, 0.0)
        assert len(monitor._pressure_history_tx) == 1
        assert monitor._pressure_history_tx[0] == (95.1, 0.0)


class TestNetworkMonitorInit:

    def test_default_bandwidth(self):
        monitor = NetworkMonitor(interface="eth0")
        assert monitor.bandwidth_kbit == 1000000

    def test_custom_bandwidth(self):
        monitor = NetworkMonitor(interface="eth0", bandwidth_kbit=10000)
        assert monitor.bandwidth_kbit == 10000

    def test_interface_stored(self):
        monitor = NetworkMonitor(interface="enp3s0")
        assert monitor.interface == "enp3s0"

    def test_initial_state_none(self):
        monitor = NetworkMonitor(interface="eth0")
        assert monitor._last_rx is None
        assert monitor._last_tx is None
        assert monitor._last_time is None
        assert monitor._pressure_history_rx == []
        assert monitor._pressure_history_tx == []
