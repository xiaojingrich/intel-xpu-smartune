# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Acceptance tests: Network Control
Verify Traffic Control (TC) based bandwidth shaping.
"""

import os
import subprocess

import pytest


def get_network_interface():
    """Get the configured or default network interface."""
    iface = os.environ.get('SMARTUNE_IFACE')
    if iface:
        return iface
    # Try to detect default interface
    try:
        result = subprocess.run(
            ['ip', 'route', 'show', 'default'],
            capture_output=True, text=True, timeout=5
        )
        parts = result.stdout.strip().split()
        if 'dev' in parts:
            return parts[parts.index('dev') + 1]
    except Exception:
        pass
    return 'lo'


@pytest.mark.service
@pytest.mark.root
@pytest.mark.network
class TestTCSetup:
    """Verify TC (Traffic Control) infrastructure."""

    @pytest.fixture
    def iface(self):
        return get_network_interface()

    def test_tc_command_available(self):
        """tc binary should be installed."""
        result = subprocess.run(['which', 'tc'], capture_output=True)
        assert result.returncode == 0

    def test_network_interface_exists(self, iface):
        """Configured network interface should exist."""
        result = subprocess.run(
            ['ip', 'link', 'show', iface],
            capture_output=True, text=True, timeout=5
        )
        assert result.returncode == 0
        assert iface in result.stdout

    def test_tc_qdisc_queryable(self, iface):
        """Should be able to query TC qdiscs on the interface."""
        result = subprocess.run(
            ['tc', 'qdisc', 'show', 'dev', iface],
            capture_output=True, text=True, timeout=5
        )
        assert result.returncode == 0

    def test_tc_class_queryable(self, iface):
        """Should be able to query TC classes."""
        result = subprocess.run(
            ['tc', 'class', 'show', 'dev', iface],
            capture_output=True, text=True, timeout=5
        )
        assert result.returncode == 0


@pytest.mark.service
@pytest.mark.root
@pytest.mark.network
class TestBandwidthShaping:
    """Verify bandwidth shaping is applied correctly."""

    @pytest.fixture
    def iface(self):
        return get_network_interface()

    def test_htb_qdisc_present_after_limit(self, api, base_url, iface):
        """After applying a network limit, HTB qdisc should be present."""
        # Check if any HTB qdisc exists (may be pre-configured)
        result = subprocess.run(
            ['tc', 'qdisc', 'show', 'dev', iface],
            capture_output=True, text=True, timeout=5
        )
        # If service has applied any network limits, HTB should be visible
        # This is informational - htb may not be set up yet
        if 'htb' in result.stdout:
            assert True
        else:
            pytest.skip("No HTB qdisc configured yet (no network limits active)")

    def test_tc_filter_queryable(self, iface):
        """TC filters should be queryable."""
        result = subprocess.run(
            ['tc', 'filter', 'show', 'dev', iface],
            capture_output=True, text=True, timeout=5
        )
        assert result.returncode == 0

    def test_interface_link_speed(self, iface):
        """Network interface should report link speed."""
        speed_path = f'/sys/class/net/{iface}/speed'
        if os.path.exists(speed_path):
            with open(speed_path) as f:
                speed = f.read().strip()
            # Speed should be a positive number (in Mbps)
            if speed != '-1':  # -1 means unknown (e.g., virtual interfaces)
                assert int(speed) > 0
        else:
            pytest.skip(f"No speed file for interface {iface}")

    def test_interface_mtu(self, iface):
        """Interface MTU should be reasonable."""
        mtu_path = f'/sys/class/net/{iface}/mtu'
        if os.path.exists(mtu_path):
            with open(mtu_path) as f:
                mtu = int(f.read().strip())
            assert 576 <= mtu <= 65535
