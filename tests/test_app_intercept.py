# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for monitor/appIntercept.py — AppIntercept class."""

import os
import sys
from unittest.mock import patch, MagicMock, mock_open
from threading import Event

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))


@pytest.fixture(autouse=True)
def clear_singleton():
    """Clear SingletonMeta._instances before and after each test to ensure fresh instances."""
    from monitor.appIntercept import SingletonMeta
    SingletonMeta._instances.clear()
    yield
    SingletonMeta._instances.clear()


@pytest.fixture
def mock_bpf():
    """Mock BPF so no kernel interaction occurs."""
    with patch('monitor.appIntercept.BPF') as mock:
        mock.return_value = MagicMock()
        yield mock


@pytest.fixture
def mock_control_manager():
    """Mock ControlManager to avoid real controller initialization."""
    with patch('monitor.appIntercept.ControlManager') as mock:
        instance = MagicMock()
        instance.get_current_pressure_level.return_value = ("normal", 0.0, False)
        instance.config = MagicMock()
        instance.config.controlled_apps = []
        mock.return_value = instance
        yield instance


@pytest.fixture
def mock_app_utils():
    """Mock app_utils module functions."""
    with patch('monitor.appIntercept.app_utils') as mock:
        mock.get_controlled_apps.return_value = []
        mock.callback_manager = MagicMock()
        yield mock


@pytest.fixture
def app_intercept(mock_bpf, mock_control_manager, mock_app_utils):
    """Create a fresh AppIntercept instance with all external deps mocked."""
    from monitor.appIntercept import AppIntercept
    instance = AppIntercept()
    return instance


class TestSingletonMeta:
    """Test that SingletonMeta enforces a single instance per class."""

    def test_same_instance_returned(self, mock_bpf, mock_control_manager, mock_app_utils):
        from monitor.appIntercept import AppIntercept
        instance1 = AppIntercept()
        instance2 = AppIntercept()
        assert instance1 is instance2

    def test_constructor_called_only_once(self, mock_bpf, mock_control_manager, mock_app_utils):
        from monitor.appIntercept import AppIntercept
        _ = AppIntercept()
        _ = AppIntercept()
        # BPF constructor should only be called once despite two AppIntercept() calls
        assert mock_bpf.call_count == 1


class TestRebuildControlledMap:
    """Test rebuild_controlled_map() fetches apps and rebuilds internal structures."""

    def test_rebuilds_from_get_controlled_apps(self, app_intercept, mock_app_utils):
        mock_app_utils.get_controlled_apps.return_value = [
            {"app_id": "1", "app_name": "Firefox", "priority": "low", "cmdline": "firefox"},
            {"app_id": "2", "app_name": "Calculator", "priority": "critical", "cmdline": "calc"},
        ]
        app_intercept.rebuild_controlled_map()

        assert len(app_intercept.controlled_app_map) == 2
        assert "firefox" in app_intercept._app_map_index
        assert "calculator" in app_intercept._app_map_index

    def test_rebuilds_index_and_cache(self, app_intercept, mock_app_utils):
        mock_app_utils.get_controlled_apps.return_value = [
            {"app_id": "1", "app_name": "MyApp", "priority": "low", "cmdline": "myapp"},
        ]
        app_intercept.rebuild_controlled_map()
        assert "myapp" in app_intercept._app_map_index


class TestRebuildIndex:
    """Test _rebuild_index() builds _app_map_index correctly."""

    def test_builds_lowercase_index(self, app_intercept):
        app_intercept.controlled_app_map = [
            {"app_id": "1", "app_name": "Firefox"},
            {"app_id": "2", "app_name": "Calculator"},
        ]
        app_intercept._rebuild_index()

        assert "firefox" in app_intercept._app_map_index
        assert "calculator" in app_intercept._app_map_index
        assert app_intercept._app_map_index["firefox"]["app_id"] == "1"

    def test_skips_empty_app_name(self, app_intercept):
        app_intercept.controlled_app_map = [
            {"app_id": "1", "app_name": ""},
            {"app_id": "2", "app_name": "  "},
            {"app_id": "3"},
            {"app_id": "4", "app_name": "ValidApp"},
        ]
        app_intercept._rebuild_index()

        assert len(app_intercept._app_map_index) == 1
        assert "validapp" in app_intercept._app_map_index

    def test_handles_empty_controlled_map(self, app_intercept):
        app_intercept.controlled_app_map = []
        app_intercept._rebuild_index()
        assert app_intercept._app_map_index == {}

    def test_handles_none_controlled_map(self, app_intercept):
        app_intercept.controlled_app_map = None
        app_intercept._rebuild_index()
        assert app_intercept._app_map_index == {}


class TestRebuildMatchCache:
    """Test _rebuild_match_cache() builds lookup dicts from config."""

    def test_builds_comm_and_filename_maps(self, app_intercept, mock_control_manager):
        mock_control_manager.config.controlled_apps = [
            {"name": "Firefox", "bpf_name": ["firefox", "firefox-bin"]},
            {"name": "Calculator", "bpf_name": ["gnome-calculator"]},
        ]
        app_intercept.monitored_apps = {"Firefox", "Calculator"}
        app_intercept._rebuild_match_cache()

        assert "firefox" in app_intercept._comm_to_app
        assert "firefox-bin" in app_intercept._comm_to_app
        assert "gnome-calculator" in app_intercept._comm_to_app
        assert app_intercept._comm_to_app["firefox"] == "Firefox"
        assert app_intercept._comm_to_app["gnome-calculator"] == "Calculator"

    def test_builds_quick_filter(self, app_intercept, mock_control_manager):
        mock_control_manager.config.controlled_apps = [
            {"name": "MyApp", "bpf_name": ["myapp", "myapp-server"]},
        ]
        app_intercept.monitored_apps = {"MyApp"}
        app_intercept._rebuild_match_cache()

        assert "myapp" in app_intercept._quick_filter
        assert "myapp-server" in app_intercept._quick_filter

    def test_only_monitored_apps_included(self, app_intercept, mock_control_manager):
        mock_control_manager.config.controlled_apps = [
            {"name": "Firefox", "bpf_name": ["firefox"]},
            {"name": "Calculator", "bpf_name": ["gnome-calculator"]},
        ]
        # Only Firefox is monitored
        app_intercept.monitored_apps = {"Firefox"}
        app_intercept._rebuild_match_cache()

        assert "firefox" in app_intercept._comm_to_app
        assert "gnome-calculator" not in app_intercept._comm_to_app

    def test_empty_monitored_apps(self, app_intercept, mock_control_manager):
        mock_control_manager.config.controlled_apps = [
            {"name": "Firefox", "bpf_name": ["firefox"]},
        ]
        app_intercept.monitored_apps = set()
        app_intercept._rebuild_match_cache()

        assert app_intercept._comm_to_app == {}
        assert app_intercept._filename_exe_to_app == {}
        assert app_intercept._quick_filter == frozenset()

    def test_missing_bpf_name_defaults_to_empty(self, app_intercept, mock_control_manager):
        mock_control_manager.config.controlled_apps = [
            {"name": "NoBpf"},
        ]
        app_intercept.monitored_apps = {"NoBpf"}
        app_intercept._rebuild_match_cache()

        assert app_intercept._comm_to_app == {}


class TestGetMainProcess:
    """Test get_main_process() matching logic."""

    def test_comm_exact_match(self, app_intercept):
        app_intercept._comm_to_app = {"firefox": "Firefox"}
        app_intercept._filename_exe_to_app = {"firefox": "Firefox"}

        result = app_intercept.get_main_process("firefox", "/usr/bin/python")
        assert result == (True, "Firefox")

    def test_comm_match_case_insensitive(self, app_intercept):
        app_intercept._comm_to_app = {"firefox": "Firefox"}
        app_intercept._filename_exe_to_app = {"firefox": "Firefox"}

        result = app_intercept.get_main_process("FireFox", "/usr/bin/python")
        assert result == (True, "Firefox")

    def test_filename_match_under_bin(self, app_intercept):
        app_intercept._comm_to_app = {}
        app_intercept._filename_exe_to_app = {"llama-server": "LlamaServer"}

        result = app_intercept.get_main_process("wrapper", "/usr/bin/llama-server")
        assert result == (True, "LlamaServer")

    def test_filename_match_under_snap_bin(self, app_intercept):
        app_intercept._comm_to_app = {}
        app_intercept._filename_exe_to_app = {"firefox": "Firefox"}

        result = app_intercept.get_main_process("wrapper", "/snap/bin/firefox")
        assert result == (True, "Firefox")

    def test_filename_match_with_bash_comm(self, app_intercept):
        app_intercept._comm_to_app = {}
        app_intercept._filename_exe_to_app = {"myapp": "MyApp"}

        result = app_intercept.get_main_process("bash", "/home/user/myapp")
        assert result == (True, "MyApp")

    def test_no_match_non_bin_path(self, app_intercept):
        """Non-bin paths should not match unless comm is bash."""
        app_intercept._comm_to_app = {}
        app_intercept._filename_exe_to_app = {"myapp": "MyApp"}

        result = app_intercept.get_main_process("wrapper", "/home/user/myapp")
        assert result == (False, "")

    def test_no_match_at_all(self, app_intercept):
        app_intercept._comm_to_app = {}
        app_intercept._filename_exe_to_app = {}

        result = app_intercept.get_main_process("unknown", "/usr/bin/unknown")
        assert result == (False, "")

    def test_no_match_empty_maps(self, app_intercept):
        app_intercept._comm_to_app = {}
        app_intercept._filename_exe_to_app = {}

        result = app_intercept.get_main_process("anything", "/bin/anything")
        assert result == (False, "")


class TestAddToMonitorlist:
    """Test add_to_monitorlist() for single and batch additions."""

    def test_add_single_string(self, app_intercept):
        app_intercept.add_to_monitorlist("Firefox")
        assert "Firefox" in app_intercept.monitored_apps

    def test_add_list_of_names(self, app_intercept):
        app_intercept.add_to_monitorlist(["Firefox", "Calculator"])
        assert "Firefox" in app_intercept.monitored_apps
        assert "Calculator" in app_intercept.monitored_apps

    def test_case_insensitive_dedup(self, app_intercept):
        app_intercept.add_to_monitorlist("Firefox")
        app_intercept.add_to_monitorlist("firefox")
        # Only one entry should exist (the original casing)
        assert len(app_intercept.monitored_apps) == 1

    def test_skips_empty_names(self, app_intercept):
        app_intercept.add_to_monitorlist(["", "  ", "Firefox"])
        assert len(app_intercept.monitored_apps) == 1
        assert "Firefox" in app_intercept.monitored_apps

    def test_skips_none_input(self, app_intercept):
        app_intercept.add_to_monitorlist(None)
        assert len(app_intercept.monitored_apps) == 0

    def test_rebuilds_match_cache_on_add(self, app_intercept, mock_control_manager):
        mock_control_manager.config.controlled_apps = [
            {"name": "Firefox", "bpf_name": ["firefox"]},
        ]
        app_intercept.add_to_monitorlist("Firefox")
        # After adding, the cache should be rebuilt
        assert "firefox" in app_intercept._comm_to_app


class TestRemoveFromMonitorlist:
    """Test remove_from_monitorlist() removes apps correctly."""

    def test_remove_existing_app(self, app_intercept):
        app_intercept.monitored_apps.add("Firefox")
        app_intercept.remove_from_monitorlist("Firefox")
        assert "Firefox" not in app_intercept.monitored_apps

    def test_remove_nonexistent_app_no_error(self, app_intercept):
        # Should not raise even if app is not in the set
        app_intercept.remove_from_monitorlist("NonExistent")
        assert "NonExistent" not in app_intercept.monitored_apps

    def test_rebuilds_match_cache_on_remove(self, app_intercept, mock_control_manager):
        mock_control_manager.config.controlled_apps = [
            {"name": "Firefox", "bpf_name": ["firefox"]},
        ]
        app_intercept.monitored_apps.add("Firefox")
        app_intercept._rebuild_match_cache()
        assert "firefox" in app_intercept._comm_to_app

        app_intercept.remove_from_monitorlist("Firefox")
        assert "firefox" not in app_intercept._comm_to_app


class TestHandleExitEvent:
    """Test handle_exit_event() process exit logic."""

    def test_process_still_alive_does_nothing(self, app_intercept, mock_app_utils):
        """If the process is still alive, do not send callback or clean up."""
        app_intercept.monitored_app_launched[1234] = ("app1", "Firefox", "firefox", "/usr/bin/firefox")
        app_intercept.app_live_pids["Firefox"] = {1234}

        with patch.object(app_intercept, 'is_process_alive', return_value=True):
            app_intercept.handle_exit_event(1234, "app1", "Firefox", "firefox", "/usr/bin/firefox")

        # Should not have sent a callback
        mock_app_utils.callback_manager.send_callback_notification.assert_not_called()
        # Should still be in monitored_app_launched
        assert 1234 in app_intercept.monitored_app_launched

    def test_last_pid_sends_stopped_callback(self, app_intercept, mock_app_utils):
        """When the last PID for an app exits, send 'stopped' callback."""
        app_intercept.monitored_app_launched[1234] = ("app1", "Firefox", "firefox", "/usr/bin/firefox")
        app_intercept.app_live_pids["Firefox"] = {1234}

        with patch.object(app_intercept, 'is_process_alive', return_value=False):
            app_intercept.handle_exit_event(1234, "app1", "Firefox", "firefox", "/usr/bin/firefox")

        mock_app_utils.callback_manager.send_callback_notification.assert_called_once_with({
            'app_id': "app1",
            'app_name': "Firefox",
            'status': "stopped",
            'purpose': "app"
        }, True)
        assert 1234 not in app_intercept.monitored_app_launched
        assert "Firefox" not in app_intercept.app_live_pids

    def test_other_pids_still_alive_no_callback(self, app_intercept, mock_app_utils):
        """If other PIDs for the same app are still alive, do not send 'stopped'."""
        app_intercept.monitored_app_launched[1234] = ("app1", "Firefox", "firefox", "/usr/bin/firefox")
        app_intercept.monitored_app_launched[5678] = ("app1", "Firefox", "firefox-child", "/usr/bin/firefox")
        app_intercept.app_live_pids["Firefox"] = {1234, 5678}

        with patch.object(app_intercept, 'is_process_alive', return_value=False):
            app_intercept.handle_exit_event(1234, "app1", "Firefox", "firefox", "/usr/bin/firefox")

        # Should NOT send stopped callback since PID 5678 is still tracked
        mock_app_utils.callback_manager.send_callback_notification.assert_not_called()
        assert 5678 in app_intercept.app_live_pids["Firefox"]
        assert 1234 not in app_intercept.app_live_pids["Firefox"]

    def test_cleans_up_pending_exit_events(self, app_intercept, mock_app_utils):
        """Pending exit events for the PID should be cleaned up."""
        app_intercept.monitored_app_launched[1234] = ("app1", "Firefox", "firefox", "/usr/bin/firefox")
        app_intercept.app_live_pids["Firefox"] = {1234}
        app_intercept.pending_exit_events[1234] = MagicMock()

        with patch.object(app_intercept, 'is_process_alive', return_value=False):
            app_intercept.handle_exit_event(1234, "app1", "Firefox", "firefox", "/usr/bin/firefox")

        assert 1234 not in app_intercept.pending_exit_events

    def test_no_live_pids_entry_sends_stopped(self, app_intercept, mock_app_utils):
        """If app_live_pids has no entry for this app, treat as last PID."""
        app_intercept.monitored_app_launched[1234] = ("app1", "Firefox", "firefox", "/usr/bin/firefox")
        # No entry in app_live_pids at all

        with patch.object(app_intercept, 'is_process_alive', return_value=False):
            app_intercept.handle_exit_event(1234, "app1", "Firefox", "firefox", "/usr/bin/firefox")

        mock_app_utils.callback_manager.send_callback_notification.assert_called_once_with({
            'app_id': "app1",
            'app_name': "Firefox",
            'status': "stopped",
            'purpose': "app"
        }, True)


class TestOnCriticalStateChanged:
    """Test _on_critical_state_changed() sets/clears the event flag."""

    def test_sets_event_on_critical(self, app_intercept):
        app_intercept._system_critical.clear()
        app_intercept._on_critical_state_changed(True)
        assert app_intercept._system_critical.is_set()

    def test_clears_event_on_non_critical(self, app_intercept):
        app_intercept._system_critical.set()
        app_intercept._on_critical_state_changed(False)
        assert not app_intercept._system_critical.is_set()


class TestIsProcessAlive:
    """Test is_process_alive() checks /proc/{pid}/status."""

    def test_returns_true_when_proc_exists(self, app_intercept):
        m = mock_open(read_data="Name:\tpython\n")
        with patch('builtins.open', m):
            assert app_intercept.is_process_alive(1234) is True
        m.assert_called_once_with("/proc/1234/status")

    def test_returns_false_when_proc_missing(self, app_intercept):
        with patch('builtins.open', side_effect=FileNotFoundError):
            assert app_intercept.is_process_alive(9999) is False
