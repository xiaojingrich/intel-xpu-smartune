# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for utils/app_utils.py — app utility functions."""

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))


class TestGetExecutableName:
    @pytest.fixture(autouse=True)
    def setup(self):
        from utils.app_utils import _get_executable_name
        self.func = _get_executable_name

    def test_snap_app(self):
        result = self.func("Firefox", "/snap/bin/firefox %u")
        assert result == "firefox"

    def test_flatpak_with_command(self):
        result = self.func("Mission Center", "flatpak run --command=missioncenter io.missioncenter.MissionCenter")
        assert result == "missioncenter"

    def test_flatpak_without_command(self):
        result = self.func("App", "flatpak run io.github.SomeApp")
        assert result == "someapp"

    def test_generic_path(self):
        result = self.func("Calculator", "/usr/bin/gnome-calculator")
        assert result == "gnome-calculator"

    def test_no_path(self):
        result = self.func("Firefox", "firefox")
        assert result == "firefox"

    def test_empty_cmdline(self):
        result = self.func("MyApp", "")
        assert result == "myapp"

    def test_none_cmdline(self):
        result = self.func("MyApp", None)
        assert result == "myapp"

    def test_cmdline_with_flags(self):
        result = self.func("Server", "/usr/bin/myserver --port 8080 --debug")
        assert result == "myserver"

    def test_env_var_should_be_skipped(self):
        """BUG: KEY=VALUE env vars are not skipped — returns 'display=:0' instead of 'app'."""
        result = self.func("App", "env DISPLAY=:0 /usr/bin/app")
        # Correct behavior would be "app", but current code returns "display=:0"
        assert result == "app", (
            f"_get_executable_name should skip KEY=VALUE env vars, got '{result}'"
        )

    def test_multiple_env_vars_should_be_skipped(self):
        """BUG: Multiple KEY=VALUE pairs confuse the parser."""
        result = self.func("App", "env HOME=/tmp XDG_RUNTIME_DIR=/run/user/1000 /opt/app/binary")
        assert result == "binary", (
            f"_get_executable_name should skip env vars, got '{result}'"
        )

    def test_env_var_without_env_prefix(self):
        """Bare KEY=VALUE without 'env' prefix should also be skipped."""
        result = self.func("App", "DISPLAY=:0 /usr/bin/myapp --arg")
        assert result == "myapp", (
            f"_get_executable_name should skip bare KEY=VALUE, got '{result}'"
        )


class TestGetPriorityValue:
    @pytest.fixture(autouse=True)
    def setup(self):
        with patch('utils.app_utils.b_config') as mock_config:
            mock_config.app_priority = {
                'critical': 100,
                'high': 80,
                'medium': 50,
                'low': 20
            }
            from utils.app_utils import get_priority_value
            self.func = get_priority_value
            yield

    def test_critical_priority(self):
        assert self.func("critical") == 100

    def test_high_priority(self):
        assert self.func("high") == 80

    def test_medium_priority(self):
        assert self.func("medium") == 50

    def test_low_priority(self):
        assert self.func("low") == 20

    def test_case_insensitive(self):
        assert self.func("CRITICAL") == 100
        assert self.func("High") == 80

    def test_invalid_priority_raises(self):
        with pytest.raises(ValueError):
            self.func("unknown")


class TestClientCallbackManager:
    def test_singleton_pattern(self):
        from utils.app_utils import ClientCallbackManager
        mgr1 = ClientCallbackManager()
        mgr2 = ClientCallbackManager()
        assert mgr1 is mgr2

    def test_add_remove_sse_client(self):
        import queue
        from utils.app_utils import ClientCallbackManager
        mgr = ClientCallbackManager()
        q = queue.Queue()

        initial_count = len(mgr._sse_queues)
        mgr.add_sse_client(q)
        assert len(mgr._sse_queues) == initial_count + 1

        mgr.remove_sse_client(q)
        assert len(mgr._sse_queues) == initial_count

    def test_remove_nonexistent_client(self):
        import queue
        from utils.app_utils import ClientCallbackManager
        mgr = ClientCallbackManager()
        q = queue.Queue()
        # Should not raise
        mgr.remove_sse_client(q)

    def test_send_notification_to_clients(self):
        import queue
        from utils.app_utils import ClientCallbackManager
        mgr = ClientCallbackManager()
        q1 = queue.Queue()
        q2 = queue.Queue()
        mgr.add_sse_client(q1)
        mgr.add_sse_client(q2)

        try:
            with patch('utils.app_utils.AIAppPriority') as mock_db:
                from db.DatabaseModel import DBStatus
                mock_db.update_record.return_value = DBStatus.SUCCESS
                mgr.send_callback_notification(
                    {'app_id': 'test', 'status': 'running', 'app_name': 'test'},
                    store=True
                )

            assert not q1.empty()
            assert not q2.empty()
            data1 = q1.get_nowait()
            assert data1['app_id'] == 'test'
            assert data1['status'] == 'running'
        finally:
            mgr.remove_sse_client(q1)
            mgr.remove_sse_client(q2)

    def test_send_notification_store_not_found_should_warn(self):
        """BUG: When DB record doesn't exist, warning should fire but doesn't."""
        import queue
        from utils.app_utils import ClientCallbackManager
        mgr = ClientCallbackManager()
        q = queue.Queue()
        mgr.add_sse_client(q)

        try:
            with patch('utils.app_utils.AIAppPriority') as mock_db, \
                 patch('utils.app_utils.logger') as mock_logger:
                from db.DatabaseModel import DBStatus
                mock_db.update_record.return_value = DBStatus.NOT_FOUND

                mgr.send_callback_notification(
                    {'app_id': 'ghost', 'status': 'running', 'app_name': 'ghost'},
                    store=True
                )
                # Should warn that record was not found
                mock_logger.warning.assert_called(), (
                    "send_callback_notification should log a warning when DB record NOT_FOUND"
                )
        finally:
            mgr.remove_sse_client(q)

    def test_send_notification_without_store(self):
        import queue
        from utils.app_utils import ClientCallbackManager
        mgr = ClientCallbackManager()
        q = queue.Queue()
        mgr.add_sse_client(q)

        try:
            with patch('utils.app_utils.AIAppPriority') as mock_db:
                mgr.send_callback_notification(
                    {'app_id': 'test', 'status': 'stopped', 'app_name': 'test'},
                    store=False
                )
                mock_db.update_record.assert_not_called()

            assert not q.empty()
        finally:
            mgr.remove_sse_client(q)


class TestBuildSudoCmd:
    def test_generic_vendor_adds_sudo(self):
        with patch('utils.app_utils.b_config') as mock_config:
            mock_config.vendor = "generic"
            from utils.app_utils import build_sudo_cmd
            result = build_sudo_cmd(["ls", "-la"])
            assert result == ["sudo", "ls", "-la"]

    def test_non_generic_vendor_no_sudo(self):
        with patch('utils.app_utils.b_config') as mock_config:
            mock_config.vendor = "admin"
            from utils.app_utils import build_sudo_cmd
            result = build_sudo_cmd(["ls", "-la"])
            assert result == ["ls", "-la"]


class TestGetAppProcessNames:
    def test_finds_matching_app_by_id(self):
        with patch('utils.app_utils.b_config') as mock_config:
            mock_config.controlled_apps = [
                {'id': 'multi.service', 'name': 'Multi App', 'process_names': ['worker1', 'worker2']}
            ]
            from utils.app_utils import _get_app_process_names
            result = _get_app_process_names(app_id='multi.service')
            assert result == ['worker1', 'worker2']

    def test_finds_matching_app_by_name(self):
        with patch('utils.app_utils.b_config') as mock_config:
            mock_config.controlled_apps = [
                {'id': 'multi.service', 'name': 'Multi App', 'process_names': ['w1']}
            ]
            from utils.app_utils import _get_app_process_names
            result = _get_app_process_names(app_name='Multi App')
            assert result == ['w1']

    def test_returns_empty_when_no_match(self):
        with patch('utils.app_utils.b_config') as mock_config:
            mock_config.controlled_apps = [
                {'id': 'other.service', 'name': 'Other', 'process_names': ['p1']}
            ]
            from utils.app_utils import _get_app_process_names
            result = _get_app_process_names(app_id='nonexist')
            assert result == []

    def test_returns_empty_when_no_controlled_apps(self):
        with patch('utils.app_utils.b_config') as mock_config:
            mock_config.controlled_apps = None
            from utils.app_utils import _get_app_process_names
            result = _get_app_process_names(app_id='test')
            assert result == []


class TestGetAppControlInfo:
    """Test get_app_control_info — especially the case sensitivity bug."""

    @patch('utils.app_utils.get_controlled_apps')
    def test_find_by_app_id(self, mock_get):
        from utils.app_utils import get_app_control_info
        mock_get.return_value = [
            {'app_id': 'org.mozilla.Firefox', 'app_name': 'Firefox',
             'controlled': True, 'priority': 80, 'cmdline': 'firefox'}
        ]
        is_controlled, data = get_app_control_info(app_id='org.mozilla.Firefox')
        assert is_controlled is True
        assert data['app_name'] == 'Firefox'

    @patch('utils.app_utils.get_controlled_apps')
    def test_find_by_name_case_sensitive_bug(self, mock_get):
        """BUG: name_map keys are .lower()'d but lookup doesn't lower the input."""
        from utils.app_utils import get_app_control_info
        mock_get.return_value = [
            {'app_id': 'org.mozilla.Firefox', 'app_name': 'Firefox',
             'controlled': True, 'priority': 80, 'cmdline': 'firefox'}
        ]
        is_controlled, data = get_app_control_info(app_name='Firefox')
        assert is_controlled is True, (
            "get_app_control_info should find 'Firefox' by name, "
            "but name_map keys are lowered while lookup is not"
        )

    @patch('utils.app_utils.get_controlled_apps')
    def test_find_by_name_lowercase_works(self, mock_get):
        """Lowercase input matches the lowered name_map key."""
        from utils.app_utils import get_app_control_info
        mock_get.return_value = [
            {'app_id': 'calc', 'app_name': 'calculator',
             'controlled': True, 'priority': 50, 'cmdline': 'calc'}
        ]
        is_controlled, data = get_app_control_info(app_name='calculator')
        assert is_controlled is True

    @patch('utils.app_utils.get_controlled_apps')
    def test_not_found(self, mock_get):
        from utils.app_utils import get_app_control_info
        mock_get.return_value = [
            {'app_id': 'calc', 'app_name': 'Calculator',
             'controlled': True, 'priority': 50, 'cmdline': 'calc'}
        ]
        is_controlled, data = get_app_control_info(app_name='NonExistentApp')
        assert is_controlled is False


class TestUpdateAppStatus:
    """Test update_app_status — especially the DBStatus truthiness bug."""

    @patch('utils.app_utils.AIAppPriority')
    def test_success_returns_true(self, mock_db):
        from utils.app_utils import update_app_status
        from db.DatabaseModel import DBStatus
        mock_db.update_record.return_value = DBStatus.SUCCESS
        assert update_app_status("app1", "running") is True

    @patch('utils.app_utils.AIAppPriority')
    def test_not_found_should_return_false(self, mock_db):
        """BUG: NOT_FOUND is truthy, so `if not result` doesn't catch it."""
        from utils.app_utils import update_app_status
        from db.DatabaseModel import DBStatus
        mock_db.update_record.return_value = DBStatus.NOT_FOUND
        result = update_app_status("nonexist", "running")
        assert result is False, (
            "update_app_status should return False for NOT_FOUND, "
            "but `if not result` doesn't catch truthy enum values"
        )

    @patch('utils.app_utils.AIAppPriority')
    def test_failed_should_return_false(self, mock_db):
        """BUG: FAILED is truthy, so `if not result` doesn't catch it."""
        from utils.app_utils import update_app_status
        from db.DatabaseModel import DBStatus
        mock_db.update_record.return_value = DBStatus.FAILED
        result = update_app_status("app1", "running")
        assert result is False, (
            "update_app_status should return False for FAILED"
        )

    @patch('utils.app_utils.AIAppPriority')
    def test_none_exception_returns_false(self, mock_db):
        from utils.app_utils import update_app_status
        mock_db.update_record.return_value = None
        assert update_app_status("app1", "running") is False


class TestUpdateAppOomScoreAdj:
    """Test _update_app_oom_score_adj — same DBStatus truthiness bug."""

    @patch('utils.app_utils.AIAppPriority')
    def test_success(self, mock_db):
        from utils.app_utils import _update_app_oom_score_adj
        from db.DatabaseModel import DBStatus
        mock_db.update_record.return_value = DBStatus.SUCCESS
        assert _update_app_oom_score_adj("app1", -1000) is True

    @patch('utils.app_utils.AIAppPriority')
    def test_not_found_should_return_false(self, mock_db):
        """BUG: Same as update_app_status — NOT_FOUND is truthy."""
        from utils.app_utils import _update_app_oom_score_adj
        from db.DatabaseModel import DBStatus
        mock_db.update_record.return_value = DBStatus.NOT_FOUND
        result = _update_app_oom_score_adj("nonexist", -1000)
        assert result is False, (
            "_update_app_oom_score_adj should return False for NOT_FOUND"
        )
