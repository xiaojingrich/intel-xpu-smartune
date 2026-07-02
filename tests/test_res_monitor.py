# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for monitor/res_monitor.py — fdinfo parsing, engine delta accumulation,
and ResourceMonitor initialization."""

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))

from monitor.res_monitor import (
    _parse_fdinfo_mem_bytes,
    _parse_fdinfo_engines,
    _accumulate_engine_delta,
)


# ---------------------------------------------------------------------------
# Tests for _parse_fdinfo_mem_bytes
# ---------------------------------------------------------------------------

class TestParseFdinfoMemBytes:
    """Tests for _parse_fdinfo_mem_bytes(line, is_xe)."""

    # --- Lines that should return 0 (non-matching prefixes) ---

    def test_non_drm_line_returns_zero(self):
        assert _parse_fdinfo_mem_bytes("pos: 0", is_xe=False) == 0
        assert _parse_fdinfo_mem_bytes("flags: 02", is_xe=True) == 0

    def test_empty_line_returns_zero(self):
        assert _parse_fdinfo_mem_bytes("", is_xe=False) == 0

    def test_drm_driver_line_returns_zero(self):
        assert _parse_fdinfo_mem_bytes("drm-driver: xe", is_xe=True) == 0

    def test_drm_client_id_line_returns_zero(self):
        assert _parse_fdinfo_mem_bytes("drm-client-id: 42", is_xe=False) == 0

    # --- Cycle-counter lines are always excluded ---

    def test_cycles_line_excluded_xe(self):
        line = "drm-total-cycles-rcs0: 12345"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 0

    def test_cycles_line_excluded_i915(self):
        line = "drm-total-cycles-rcs0: 12345"
        assert _parse_fdinfo_mem_bytes(line, is_xe=False) == 0

    # --- GTT semantics: xe includes, i915 excludes ---

    def test_gtt_line_included_for_xe(self):
        line = "drm-total-gtt: 2048 KiB"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 2048 * 1024

    def test_gtt_line_excluded_for_i915(self):
        line = "drm-total-gtt: 2048 KiB"
        assert _parse_fdinfo_mem_bytes(line, is_xe=False) == 0

    def test_memory_gtt_line_excluded_for_i915(self):
        line = "drm-memory-gtt: 512 MiB"
        assert _parse_fdinfo_mem_bytes(line, is_xe=False) == 0

    def test_memory_gtt_line_included_for_xe(self):
        line = "drm-memory-gtt: 512 MiB"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 512 * 1024 * 1024

    # --- Unit conversions ---

    def test_kib_unit(self):
        line = "drm-total-vram0: 1024 KiB"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 1024 * 1024

    def test_mib_unit(self):
        line = "drm-total-vram0: 256 MiB"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 256 * 1024 * 1024

    def test_gib_unit(self):
        line = "drm-total-vram0: 2 GiB"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 2 * 1024 * 1024 * 1024

    def test_bytes_unit(self):
        line = "drm-total-vram0: 4096 B"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 4096

    def test_kb_unit_alias(self):
        line = "drm-total-vram0: 100 KB"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 100 * 1024

    def test_mb_unit_alias(self):
        line = "drm-total-vram0: 10 MB"
        assert _parse_fdinfo_mem_bytes(line, is_xe=False) == 10 * 1024 * 1024

    def test_gb_unit_alias(self):
        line = "drm-total-vram0: 1 GB"
        assert _parse_fdinfo_mem_bytes(line, is_xe=False) == 1 * 1024 * 1024 * 1024

    def test_k_unit_alias(self):
        line = "drm-memory-vram0: 512 K"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 512 * 1024

    def test_m_unit_alias(self):
        line = "drm-memory-vram0: 8 M"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 8 * 1024 * 1024

    def test_g_unit_alias(self):
        line = "drm-memory-vram0: 4 G"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 4 * 1024 * 1024 * 1024

    # --- Unknown unit returns 0 ---

    def test_unknown_unit_returns_zero(self):
        line = "drm-total-vram0: 1024 TiB"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 0

    # --- Malformed lines ---

    def test_no_colon_returns_zero(self):
        line = "drm-total-vram0 1024 KiB"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 0

    def test_missing_value_returns_zero(self):
        line = "drm-total-vram0:"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 0

    def test_single_token_after_colon_returns_zero(self):
        # Only one token (the value), no unit
        line = "drm-total-vram0: 1024"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 0

    def test_non_numeric_value_returns_zero(self):
        line = "drm-total-vram0: abc KiB"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 0

    # --- drm-memory- prefix works the same as drm-total- ---

    def test_drm_memory_prefix_kib(self):
        line = "drm-memory-vram0: 512 KiB"
        assert _parse_fdinfo_mem_bytes(line, is_xe=False) == 512 * 1024

    def test_drm_memory_prefix_mib(self):
        line = "drm-memory-stolen: 64 MiB"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 64 * 1024 * 1024

    # --- Case insensitivity of unit parsing (upper-cased internally) ---

    def test_lowercase_kib(self):
        line = "drm-total-vram0: 128 kib"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 128 * 1024

    def test_mixed_case_mib(self):
        line = "drm-total-vram0: 64 Mib"
        assert _parse_fdinfo_mem_bytes(line, is_xe=True) == 64 * 1024 * 1024


# ---------------------------------------------------------------------------
# Tests for _parse_fdinfo_engines
# ---------------------------------------------------------------------------

class TestParseFdinfoEngines:
    """Tests for _parse_fdinfo_engines(content)."""

    def test_empty_content_returns_empty_dict(self):
        assert _parse_fdinfo_engines("") == {}

    def test_no_engine_lines_returns_empty_dict(self):
        content = "drm-driver: xe\ndrm-client-id: 5\npos: 0\n"
        assert _parse_fdinfo_engines(content) == {}

    def test_parse_drm_engine_time_ns(self):
        content = "drm-engine-rcs0: 500000 ns\n"
        result = _parse_fdinfo_engines(content)
        assert "rcs0" in result
        assert result["rcs0"]["time_ns"] == 500000
        assert result["rcs0"]["cycles"] == 0
        assert result["rcs0"]["total_cycles"] == 0

    def test_parse_drm_total_cycles(self):
        content = "drm-total-cycles-compute0: 1000000\n"
        result = _parse_fdinfo_engines(content)
        assert "compute0" in result
        assert result["compute0"]["total_cycles"] == 1000000
        assert result["compute0"]["cycles"] == 0
        assert result["compute0"]["time_ns"] == 0

    def test_parse_drm_cycles(self):
        content = "drm-cycles-render: 750000\n"
        result = _parse_fdinfo_engines(content)
        assert "render" in result
        assert result["render"]["cycles"] == 750000
        assert result["render"]["total_cycles"] == 0
        assert result["render"]["time_ns"] == 0

    def test_multiple_engines(self):
        content = (
            "drm-engine-rcs0: 100 ns\n"
            "drm-engine-bcs0: 200 ns\n"
            "drm-engine-vcs0: 300 ns\n"
        )
        result = _parse_fdinfo_engines(content)
        assert len(result) == 3
        assert result["rcs0"]["time_ns"] == 100
        assert result["bcs0"]["time_ns"] == 200
        assert result["vcs0"]["time_ns"] == 300

    def test_combined_xe_fields_same_engine(self):
        content = (
            "drm-cycles-rcs0: 500\n"
            "drm-total-cycles-rcs0: 1000\n"
        )
        result = _parse_fdinfo_engines(content)
        assert result["rcs0"]["cycles"] == 500
        assert result["rcs0"]["total_cycles"] == 1000
        assert result["rcs0"]["time_ns"] == 0

    def test_mixed_xe_and_i915_fields(self):
        content = (
            "drm-engine-rcs0: 999 ns\n"
            "drm-cycles-compute0: 400\n"
            "drm-total-cycles-compute0: 800\n"
        )
        result = _parse_fdinfo_engines(content)
        assert result["rcs0"]["time_ns"] == 999
        assert result["compute0"]["cycles"] == 400
        assert result["compute0"]["total_cycles"] == 800

    def test_malformed_engine_time_ns_ignored(self):
        content = "drm-engine-rcs0: not_a_number ns\n"
        result = _parse_fdinfo_engines(content)
        assert "rcs0" in result
        # Value should remain at default 0 since parsing failed
        assert result["rcs0"]["time_ns"] == 0

    def test_malformed_cycles_ignored(self):
        content = "drm-cycles-rcs0: abc\n"
        result = _parse_fdinfo_engines(content)
        assert "rcs0" in result
        assert result["rcs0"]["cycles"] == 0

    def test_malformed_total_cycles_ignored(self):
        content = "drm-total-cycles-rcs0: xyz\n"
        result = _parse_fdinfo_engines(content)
        assert "rcs0" in result
        assert result["rcs0"]["total_cycles"] == 0

    def test_lines_without_colon_skipped(self):
        content = "this line has no colon\ndrm-engine-rcs0: 42 ns\n"
        result = _parse_fdinfo_engines(content)
        assert len(result) == 1
        assert result["rcs0"]["time_ns"] == 42

    def test_whitespace_handling(self):
        content = "  drm-engine-rcs0 :  12345 ns  \n"
        result = _parse_fdinfo_engines(content)
        # The key is stripped so leading whitespace in key is preserved until split
        # The function splits on ':', then strips both sides
        assert "rcs0" in result
        assert result["rcs0"]["time_ns"] == 12345

    def test_engine_time_ns_only_first_token_used(self):
        # "drm-engine-rcs0: 100 ns extra_stuff"
        content = "drm-engine-rcs0: 100 ns extra_stuff\n"
        result = _parse_fdinfo_engines(content)
        assert result["rcs0"]["time_ns"] == 100

    def test_empty_value_after_colon(self):
        content = "drm-engine-rcs0:\n"
        result = _parse_fdinfo_engines(content)
        # Empty val.split() -> IndexError is caught
        assert "rcs0" in result
        assert result["rcs0"]["time_ns"] == 0


# ---------------------------------------------------------------------------
# Tests for _accumulate_engine_delta
# ---------------------------------------------------------------------------

class TestAccumulateEngineDelta:
    """Tests for _accumulate_engine_delta(out, t0_engines, t1_engines)."""

    def test_basic_delta_computation(self):
        out = {}
        t0 = {"rcs0": {"cycles": 100, "total_cycles": 1000, "time_ns": 500}}
        t1 = {"rcs0": {"cycles": 150, "total_cycles": 1100, "time_ns": 800}}
        _accumulate_engine_delta(out, t0, t1)
        assert out["rcs0"]["cycles"] == 50
        assert out["rcs0"]["total_cycles"] == 100
        assert out["rcs0"]["time_ns"] == 300

    def test_accumulates_into_existing_out(self):
        out = {"rcs0": {"cycles": 10, "total_cycles": 20, "time_ns": 30}}
        t0 = {"rcs0": {"cycles": 100, "total_cycles": 1000, "time_ns": 500}}
        t1 = {"rcs0": {"cycles": 200, "total_cycles": 1500, "time_ns": 900}}
        _accumulate_engine_delta(out, t0, t1)
        assert out["rcs0"]["cycles"] == 10 + 100
        assert out["rcs0"]["total_cycles"] == 20 + 500
        assert out["rcs0"]["time_ns"] == 30 + 400

    def test_counter_wrap_uses_max_zero(self):
        """When t1 < t0 (counter wrap), delta is clamped to 0."""
        out = {}
        t0 = {"rcs0": {"cycles": 500, "total_cycles": 2000, "time_ns": 1000}}
        t1 = {"rcs0": {"cycles": 100, "total_cycles": 500, "time_ns": 200}}
        _accumulate_engine_delta(out, t0, t1)
        assert out["rcs0"]["cycles"] == 0
        assert out["rcs0"]["total_cycles"] == 0
        assert out["rcs0"]["time_ns"] == 0

    def test_new_engine_in_t1_not_in_t0(self):
        """Engine appears in t1 but not t0; t0 defaults to zeros."""
        out = {}
        t0 = {}
        t1 = {"compute0": {"cycles": 300, "total_cycles": 600, "time_ns": 900}}
        _accumulate_engine_delta(out, t0, t1)
        assert out["compute0"]["cycles"] == 300
        assert out["compute0"]["total_cycles"] == 600
        assert out["compute0"]["time_ns"] == 900

    def test_engine_in_t0_not_in_t1_not_included(self):
        """Engines only in t0 (disappeared) produce no output."""
        out = {}
        t0 = {"rcs0": {"cycles": 100, "total_cycles": 1000, "time_ns": 500}}
        t1 = {}
        _accumulate_engine_delta(out, t0, t1)
        assert out == {}

    def test_multiple_engines(self):
        out = {}
        t0 = {
            "rcs0": {"cycles": 100, "total_cycles": 1000, "time_ns": 0},
            "bcs0": {"cycles": 50, "total_cycles": 500, "time_ns": 0},
        }
        t1 = {
            "rcs0": {"cycles": 200, "total_cycles": 1200, "time_ns": 0},
            "bcs0": {"cycles": 80, "total_cycles": 700, "time_ns": 0},
        }
        _accumulate_engine_delta(out, t0, t1)
        assert out["rcs0"]["cycles"] == 100
        assert out["rcs0"]["total_cycles"] == 200
        assert out["bcs0"]["cycles"] == 30
        assert out["bcs0"]["total_cycles"] == 200

    def test_zero_delta(self):
        """No change between snapshots yields zero deltas."""
        out = {}
        t0 = {"rcs0": {"cycles": 500, "total_cycles": 1000, "time_ns": 2000}}
        t1 = {"rcs0": {"cycles": 500, "total_cycles": 1000, "time_ns": 2000}}
        _accumulate_engine_delta(out, t0, t1)
        assert out["rcs0"]["cycles"] == 0
        assert out["rcs0"]["total_cycles"] == 0
        assert out["rcs0"]["time_ns"] == 0

    def test_partial_wrap_mixed_fields(self):
        """One field wraps while others increment normally."""
        out = {}
        t0 = {"rcs0": {"cycles": 900, "total_cycles": 1000, "time_ns": 100}}
        t1 = {"rcs0": {"cycles": 100, "total_cycles": 1500, "time_ns": 400}}
        _accumulate_engine_delta(out, t0, t1)
        assert out["rcs0"]["cycles"] == 0  # wrapped -> clamped
        assert out["rcs0"]["total_cycles"] == 500  # normal increment
        assert out["rcs0"]["time_ns"] == 300  # normal increment

    def test_multiple_accumulations(self):
        """Calling _accumulate_engine_delta multiple times accumulates correctly."""
        out = {}
        t0_a = {"rcs0": {"cycles": 0, "total_cycles": 0, "time_ns": 0}}
        t1_a = {"rcs0": {"cycles": 10, "total_cycles": 100, "time_ns": 50}}
        _accumulate_engine_delta(out, t0_a, t1_a)

        t0_b = {"rcs0": {"cycles": 10, "total_cycles": 100, "time_ns": 50}}
        t1_b = {"rcs0": {"cycles": 25, "total_cycles": 250, "time_ns": 120}}
        _accumulate_engine_delta(out, t0_b, t1_b)

        assert out["rcs0"]["cycles"] == 10 + 15
        assert out["rcs0"]["total_cycles"] == 100 + 150
        assert out["rcs0"]["time_ns"] == 50 + 70


# ---------------------------------------------------------------------------
# Tests for ResourceMonitor.__init__
# ---------------------------------------------------------------------------

class TestResourceMonitorInit:
    """Tests for ResourceMonitor.__init__ with mocked dependencies."""

    @patch('monitor.res_monitor.fetch_all_apps')
    @patch('monitor.res_monitor.psutil')
    @patch('monitor.res_monitor.b_config')
    def test_cpu_cores_from_os_cpu_count(self, mock_config, mock_psutil, mock_fetch):
        mock_config.controlled_apps = []
        mock_config.blacklist = []
        mock_fetch.return_value = []
        mock_psutil.disk_io_counters.return_value = {}

        with patch('os.cpu_count', return_value=8):
            from monitor.res_monitor import ResourceMonitor
            monitor = ResourceMonitor()
            assert monitor.cpu_cores == 8

    @patch('monitor.res_monitor.fetch_all_apps')
    @patch('monitor.res_monitor.psutil')
    @patch('monitor.res_monitor.b_config')
    def test_cpu_cores_fallback_when_none(self, mock_config, mock_psutil, mock_fetch):
        mock_config.controlled_apps = []
        mock_config.blacklist = []
        mock_fetch.return_value = []
        mock_psutil.disk_io_counters.return_value = {}

        with patch('os.cpu_count', return_value=None):
            from monitor.res_monitor import ResourceMonitor
            monitor = ResourceMonitor()
            assert monitor.cpu_cores == 16

    @patch('monitor.res_monitor.fetch_all_apps')
    @patch('monitor.res_monitor.psutil')
    @patch('monitor.res_monitor.b_config')
    def test_desktop_apps_loaded(self, mock_config, mock_psutil, mock_fetch):
        mock_config.controlled_apps = []
        mock_config.blacklist = []
        mock_fetch.return_value = [
            {"app_id": "firefox", "display_name": "Firefox"},
            {"app_id": "chrome", "display_name": "Chrome"},
        ]
        mock_psutil.disk_io_counters.return_value = {}

        from monitor.res_monitor import ResourceMonitor
        monitor = ResourceMonitor()
        assert len(monitor.desktop_apps) == 2
        assert "firefox" in monitor.desktop_apps
        assert "chrome" in monitor.desktop_apps

    @patch('monitor.res_monitor.fetch_all_apps')
    @patch('monitor.res_monitor.psutil')
    @patch('monitor.res_monitor.b_config')
    def test_fetch_all_apps_exception_handled(self, mock_config, mock_psutil, mock_fetch):
        mock_config.controlled_apps = []
        mock_config.blacklist = []
        mock_fetch.side_effect = RuntimeError("dbus unavailable")
        mock_psutil.disk_io_counters.return_value = {}

        from monitor.res_monitor import ResourceMonitor
        monitor = ResourceMonitor()
        assert monitor.desktop_apps == {}


# ---------------------------------------------------------------------------
# Tests for ResourceMonitor._load_multiprocess_config
# ---------------------------------------------------------------------------

class TestLoadMultiprocessConfig:
    """Tests for ResourceMonitor._load_multiprocess_config."""

    @patch('monitor.res_monitor.fetch_all_apps')
    @patch('monitor.res_monitor.psutil')
    @patch('monitor.res_monitor.b_config')
    def test_populates_lookup_maps(self, mock_config, mock_psutil, mock_fetch):
        mock_config.controlled_apps = [
            {
                'id': 'blender',
                'name': 'Blender',
                'process_names': ['blender', 'blender-softwaregl'],
            },
            {
                'id': 'firefox',
                'name': 'Firefox',
                'process_names': ['firefox', 'firefox-esr', 'Web Content'],
            },
        ]
        mock_config.blacklist = []
        mock_fetch.return_value = []
        mock_psutil.disk_io_counters.return_value = {}

        from monitor.res_monitor import ResourceMonitor
        monitor = ResourceMonitor()

        assert monitor._proc_name_to_app['blender'] == 'blender'
        assert monitor._proc_name_to_app['blender-softwaregl'] == 'blender'
        assert monitor._proc_name_to_app['firefox'] == 'firefox'
        assert monitor._proc_name_to_app['firefox-esr'] == 'firefox'
        assert monitor._proc_name_to_app['web content'] == 'firefox'

        assert 'blender' in monitor._multiprocess_apps
        assert monitor._multiprocess_apps['blender']['name'] == 'Blender'
        assert 'blender-softwaregl' in monitor._multiprocess_apps['blender']['process_names_lower']

    @patch('monitor.res_monitor.fetch_all_apps')
    @patch('monitor.res_monitor.psutil')
    @patch('monitor.res_monitor.b_config')
    def test_skips_apps_without_process_names(self, mock_config, mock_psutil, mock_fetch):
        mock_config.controlled_apps = [
            {'id': 'nonames', 'name': 'No Names App', 'process_names': []},
            {'id': 'nullnames', 'name': 'Null Names App'},
        ]
        mock_config.blacklist = []
        mock_fetch.return_value = []
        mock_psutil.disk_io_counters.return_value = {}

        from monitor.res_monitor import ResourceMonitor
        monitor = ResourceMonitor()

        assert monitor._proc_name_to_app == {}
        assert monitor._multiprocess_apps == {}

    @patch('monitor.res_monitor.fetch_all_apps')
    @patch('monitor.res_monitor.psutil')
    @patch('monitor.res_monitor.b_config')
    def test_controlled_apps_none(self, mock_config, mock_psutil, mock_fetch):
        mock_config.controlled_apps = None
        mock_config.blacklist = []
        mock_fetch.return_value = []
        mock_psutil.disk_io_counters.return_value = {}

        from monitor.res_monitor import ResourceMonitor
        monitor = ResourceMonitor()

        assert monitor._proc_name_to_app == {}
        assert monitor._multiprocess_apps == {}
