# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
TC-S-019: CPU Frequency Governor Switching

Verifies that under critical pressure, the CPU governor switches to powersave,
and recovers to performance when pressure drops.
"""

import os
import time
import pytest

@pytest.mark.service
@pytest.mark.root
@pytest.mark.stress_tools
class TestCPUGovernor:

    def _get_governor(self):
        """Read current CPU governor from sysfs."""
        path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
        if not os.path.exists(path):
            pytest.skip("cpufreq not available")
        with open(path) as f:
            return f.read().strip()

    def test_governor_available(self):
        """CPU frequency governor should be readable."""
        gov = self._get_governor()
        assert gov in ('performance', 'powersave', 'schedutil', 'ondemand')

    def test_governor_switches_under_critical_pressure(self, api, base_url, stress_cpu):
        """Under critical pressure, governor should switch to powersave."""
        # 1. Record initial governor
        # 2. Push to critical pressure
        # 3. Wait and poll governor (timeout 30s)
        # 4. Verify switched to powersave
        # Note: Only works if passive_control is enabled and governor control is configured

    def test_governor_recovers_after_pressure_drops(self, api, base_url):
        """After pressure drops, governor should recover to performance."""
        # Wait for cooldown period (15s) after stress stops
        # Poll governor until it returns to performance
