# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for monitor/pressure.py — PressureAnalyzer scoring and level determination."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))

from monitor.pressure import PressureAnalyzer


class FakeConfig:
    def __init__(self, weights=None, dominant_app_reduce_factor=3.5):
        self.weights = weights or {'cpu': 2, 'memory': 7, 'io': 1}
        self.dominant_app_reduce_factor = dominant_app_reduce_factor


class TestPressureAnalyzer:
    @pytest.fixture
    def analyzer(self):
        config = FakeConfig()
        return PressureAnalyzer(config)

    @pytest.fixture
    def thresholds(self):
        return {'low': 0.4, 'medium': 0.6, 'high': 0.8, 'critical': 1.0}

    def _usage(self, cpu_busy=False, mem_busy=False):
        return {
            'cpu': {'is_busy': cpu_busy},
            'memory': {'is_busy': mem_busy}
        }

    def test_zero_psi_gives_zero_score(self, analyzer):
        psi = {'cpu': 0, 'memory': 0, 'io': 0}
        score = analyzer.calculate_pressure_score(psi, self._usage(), False)
        assert score == 0.0

    def test_max_psi_gives_score_capped_at_one(self, analyzer):
        psi = {'cpu': 1.0, 'memory': 1.0, 'io': 1.0}
        score = analyzer.calculate_pressure_score(psi, self._usage(), False)
        assert score <= 1.0

    def test_score_weighted_correctly(self, analyzer):
        psi = {'cpu': 0.5, 'memory': 0.5, 'io': 0.5}
        score = analyzer.calculate_pressure_score(psi, self._usage(), False)
        expected = (2 * 0.5 + 7 * 0.5 + 1 * 0.5)
        assert score == min(round(expected, 2), 1.0)

    def test_dominant_app_reduces_score_when_system_not_busy(self, analyzer):
        psi = {'cpu': 0.3, 'memory': 0.3, 'io': 0.3}
        score_normal = analyzer.calculate_pressure_score(psi, self._usage(), False)
        score_dominant = analyzer.calculate_pressure_score(psi, self._usage(), True)
        assert score_dominant < score_normal

    def test_dominant_app_no_reduction_when_system_busy(self, analyzer):
        psi = {'cpu': 0.3, 'memory': 0.3, 'io': 0.3}
        score_normal = analyzer.calculate_pressure_score(
            psi, self._usage(cpu_busy=True), False
        )
        score_dominant = analyzer.calculate_pressure_score(
            psi, self._usage(cpu_busy=True), True
        )
        assert score_dominant == score_normal

    def test_memory_busy_prevents_reduction(self, analyzer):
        psi = {'cpu': 0.3, 'memory': 0.3, 'io': 0.3}
        score_dominant = analyzer.calculate_pressure_score(
            psi, self._usage(mem_busy=True), True
        )
        score_normal = analyzer.calculate_pressure_score(
            psi, self._usage(mem_busy=True), False
        )
        assert score_dominant == score_normal

    def test_only_memory_psi(self, analyzer):
        psi = {'cpu': 0, 'memory': 0.8, 'io': 0}
        score = analyzer.calculate_pressure_score(psi, self._usage(), False)
        expected = round(7 * 0.8, 2)
        assert score == min(expected, 1.0)

    def test_only_io_psi(self, analyzer):
        psi = {'cpu': 0, 'memory': 0, 'io': 0.9}
        score = analyzer.calculate_pressure_score(psi, self._usage(), False)
        expected = round(1 * 0.9, 2)
        assert score == expected


class TestPressureLevel:
    @pytest.fixture
    def analyzer(self):
        config = FakeConfig()
        return PressureAnalyzer(config)

    @pytest.fixture
    def thresholds(self):
        return {'low': 0.4, 'medium': 0.6, 'high': 0.8, 'critical': 1.0}

    def test_critical_level(self, analyzer, thresholds):
        assert analyzer.get_pressure_level(1.0, thresholds) == "critical"

    def test_high_level(self, analyzer, thresholds):
        assert analyzer.get_pressure_level(0.85, thresholds) == "high"

    def test_medium_level(self, analyzer, thresholds):
        assert analyzer.get_pressure_level(0.65, thresholds) == "medium"

    def test_low_level(self, analyzer, thresholds):
        assert analyzer.get_pressure_level(0.45, thresholds) == "low"

    def test_below_low_threshold(self, analyzer, thresholds):
        assert analyzer.get_pressure_level(0.1, thresholds) == "low"

    def test_exact_threshold_boundary_medium(self, analyzer, thresholds):
        assert analyzer.get_pressure_level(0.6, thresholds) == "medium"

    def test_exact_threshold_boundary_high(self, analyzer, thresholds):
        assert analyzer.get_pressure_level(0.8, thresholds) == "high"

    def test_zero_score(self, analyzer, thresholds):
        assert analyzer.get_pressure_level(0.0, thresholds) == "low"


class TestPressureAnalyzerCustomWeights:
    def test_io_heavy_weights(self):
        config = FakeConfig(weights={'cpu': 1, 'memory': 1, 'io': 8})
        analyzer = PressureAnalyzer(config)
        psi = {'cpu': 0.1, 'memory': 0.1, 'io': 0.5}
        score = analyzer.calculate_pressure_score(psi, {'cpu': {'is_busy': False}, 'memory': {'is_busy': False}}, False)
        expected = round(1 * 0.1 + 1 * 0.1 + 8 * 0.5, 2)
        assert score == min(expected, 1.0)

    def test_reduce_factor_impact(self):
        config_low = FakeConfig(dominant_app_reduce_factor=2.0)
        config_high = FakeConfig(dominant_app_reduce_factor=10.0)
        analyzer_low = PressureAnalyzer(config_low)
        analyzer_high = PressureAnalyzer(config_high)

        psi = {'cpu': 0.3, 'memory': 0.3, 'io': 0.3}
        usage = {'cpu': {'is_busy': False}, 'memory': {'is_busy': False}}

        score_low = analyzer_low.calculate_pressure_score(psi, usage, True)
        score_high = analyzer_high.calculate_pressure_score(psi, usage, True)
        assert score_high < score_low
