# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Logging and observability tests:
- TC-LOG-001: Key operation logging
- TC-LOG-002: Error log completeness
- TC-LOG-003: Log level control
- TC-LOG-005: Pressure event audit trail

These tests exercise the REAL logging statements emitted by the production
code paths (route handlers in BalanceService.py and AppIntercept callbacks),
not a stubbed-out logger.

Note on caplog and the conftest stub
------------------------------------
conftest.py replaces ``utils.logger`` with a fake module whose ``logger``
attribute is ``logging.getLogger("smartune_test")`` (carrying only a
NullHandler).  All balancer modules import that singleton, so every
``logger.info``/``logger.error``/... call in production code is emitted on the
logger named ``smartune_test``.  pytest's ``caplog`` fixture attaches its own
capturing handler; to capture from a *named* logger we must explicitly pass
``logger="smartune_test"`` to ``caplog.set_level`` / ``caplog.at_level`` so the
handler is attached there and the level is lowered for the duration of the test.
"""

import os
import sys
import logging
import importlib.util
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))

# Name of the logger the conftest stub installs as utils.logger.logger.
TEST_LOGGER_NAME = "smartune_test"


# ──────────────────────────────────────────────────────────────────────────────
# Shared fixture: the real Flask app with only external deps mocked.
# Mirrors the `real_app` fixture in tests/test_api_endpoints.py.
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def real_app():
    """Import the real Flask app from BalanceService, mocking only external deps."""
    import hashlib

    with patch('balancer.balancer.DynamicBalancer') as mock_balancer_cls, \
         patch('monitor.monitor_api._start_snapshot_cleanup_task'), \
         patch('monitor.system_info.preload_static_info'), \
         patch('BalanceService.init_database'), \
         patch('BalanceService.preload_static_info'):

        mock_balancer = MagicMock()
        mock_balancer_cls.return_value = mock_balancer

        import BalanceService
        mock_service = MagicMock()
        mock_service.get_secret_hash.return_value = hashlib.sha256(b"correct_token").hexdigest()
        mock_service.cancel_relaunch.return_value = False
        mock_service.resource_limit.return_value = True
        mock_service.restore_resource.return_value = True

        with patch.object(BalanceService, '_service', mock_service):
            BalanceService.app.config['TESTING'] = True
            with BalanceService.app.test_client() as client:
                yield client, mock_service


# ──────────────────────────────────────────────────────────────────────────────
# TC-LOG-001: Key operation logging
# Verify that key operations (set_priority, set_to_control) produce log entries
# with the expected level and content (operation type, app name, result).
# ──────────────────────────────────────────────────────────────────────────────
class TestKeyOperationLogging:
    """TC-LOG-001: key operations produce informative log records."""

    def test_set_priority_logs_result(self, real_app, caplog):
        client, _ = real_app
        from db.DatabaseModel import DBStatus

        with caplog.at_level(logging.DEBUG, logger=TEST_LOGGER_NAME):
            with patch('BalanceService.AIAppPriority') as mock_db, \
                 patch('BalanceService.adjust_oom_priority'):
                mock_db.update_record.return_value = DBStatus.SUCCESS
                # rebuild_controlled_map is on the mocked _service; the follow-up
                # query() returns a MagicMock record (truthy) which is fine.
                resp = client.post('/app/set_priority',
                                   json={'app_id': 'app123', 'priority': 80},
                                   content_type='application/json')

        assert resp.get_json()['retcode'] == 0

        # An INFO record describing the operation, app id and result must exist.
        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        matches = [r for r in info_records
                   if 'Set priority result' in r.getMessage() and 'app123' in r.getMessage()]
        assert matches, (
            f"Expected an INFO log for the set_priority operation mentioning the "
            f"app_id; captured: {[r.getMessage() for r in caplog.records]}"
        )

    def test_set_to_control_logs_status_check(self, real_app, caplog):
        client, _ = real_app
        from db.DatabaseModel import DBStatus

        with caplog.at_level(logging.DEBUG, logger=TEST_LOGGER_NAME):
            with patch('BalanceService.AIAppPriority') as mock_db, \
                 patch('BalanceService.adjust_oom_priority'), \
                 patch('BalanceService.check_app_running_status', return_value='stopped'), \
                 patch('BalanceService.callback_manager'):
                mock_db.update_record.return_value = DBStatus.NOT_FOUND
                mock_db.insert_record.return_value = DBStatus.SUCCESS

                resp = client.post('/app/set_to_control',
                                   json={
                                       'app_id': 'new_app',
                                       'app_name': 'NewApp',
                                       'controlled': True,
                                       'priority': 60,
                                       'cmdline': 'new_cmd',
                                   },
                                   content_type='application/json')

        assert resp.get_json()['retcode'] == 0

        # The set_to_control flow logs an INFO line that includes the app name
        # and the operation type ("initial status check").
        matches = [r for r in caplog.records
                   if r.levelno == logging.INFO
                   and 'set_to_control' in r.getMessage()
                   and 'NewApp' in r.getMessage()]
        assert matches, (
            f"Expected an INFO log mentioning the controlled app name 'NewApp'; "
            f"captured: {[r.getMessage() for r in caplog.records]}"
        )

    def test_log_record_carries_level_and_logger_name(self, real_app, caplog):
        """The captured record must carry a real level and the production logger name."""
        client, _ = real_app
        from db.DatabaseModel import DBStatus

        with caplog.at_level(logging.DEBUG, logger=TEST_LOGGER_NAME):
            with patch('BalanceService.AIAppPriority') as mock_db, \
                 patch('BalanceService.adjust_oom_priority'):
                mock_db.update_record.return_value = DBStatus.SUCCESS
                client.post('/app/set_priority',
                            json={'app_id': 'app123', 'priority': 80},
                            content_type='application/json')

        rec = next(r for r in caplog.records if 'Set priority result' in r.getMessage())
        assert rec.name == TEST_LOGGER_NAME
        assert rec.levelname == "INFO"


# ──────────────────────────────────────────────────────────────────────────────
# TC-LOG-002: Error log completeness
# Trigger error conditions and verify error logs contain the error message/type.
# ──────────────────────────────────────────────────────────────────────────────
class TestErrorLogCompleteness:
    """TC-LOG-002: error conditions emit ERROR records with the error detail."""

    def test_check_running_apps_exception_logged(self, real_app, caplog):
        client, mock_svc = real_app
        mock_svc.check_running_apps.side_effect = RuntimeError("BPF not ready")

        with caplog.at_level(logging.DEBUG, logger=TEST_LOGGER_NAME):
            resp = client.post('/app/check_running_apps',
                               json={},
                               content_type='application/json')

        assert resp.get_json()['retcode'] == 100  # EXCEPTION_ERROR

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        matches = [r for r in error_records
                   if 'check_running_apps failed' in r.getMessage()
                   and 'BPF not ready' in r.getMessage()]
        assert matches, (
            f"Expected an ERROR log containing the operation and the underlying "
            f"error message; captured: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )

    def test_set_to_control_db_failure_logged(self, real_app, caplog):
        client, _ = real_app

        with caplog.at_level(logging.DEBUG, logger=TEST_LOGGER_NAME):
            with patch('BalanceService.AIAppPriority') as mock_db:
                # Force the update_record call to raise so the generic except
                # clause runs `logger.error("Control set failed: ...")`.
                mock_db.update_record.side_effect = RuntimeError("database is locked")

                resp = client.post('/app/set_to_control',
                                   json={
                                       'app_id': 'app1',
                                       'app_name': 'App1',
                                       'controlled': True,
                                       'priority': 50,
                                       'cmdline': 'cmd',
                                   },
                                   content_type='application/json')

        assert resp.get_json()['retcode'] == 100  # EXCEPTION_ERROR

        matches = [r for r in caplog.records
                   if r.levelno == logging.ERROR
                   and 'Control set failed' in r.getMessage()
                   and 'database is locked' in r.getMessage()]
        assert matches, (
            f"Expected an ERROR log with the failure context and error message; "
            f"captured: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )

    def test_error_record_has_error_level(self, real_app, caplog):
        """Error logs must actually be emitted at ERROR level, not INFO/WARNING."""
        client, mock_svc = real_app
        mock_svc.check_running_apps.side_effect = ValueError("boom")

        with caplog.at_level(logging.DEBUG, logger=TEST_LOGGER_NAME):
            client.post('/app/check_running_apps', json={},
                        content_type='application/json')

        rec = next(r for r in caplog.records if 'check_running_apps failed' in r.getMessage())
        assert rec.levelno == logging.ERROR
        assert rec.levelname == "ERROR"
        assert 'boom' in rec.getMessage()


# ──────────────────────────────────────────────────────────────────────────────
# TC-LOG-003: Log level control
# Test the real Logger class from balancer/utils/logger.py for level filtering.
# conftest stubs `utils.logger`, so load the real module directly from its file
# path under a private module name to avoid disturbing the stub.
# ──────────────────────────────────────────────────────────────────────────────
def _load_real_logger_module():
    """Import balancer/utils/logger.py directly, bypassing the conftest stub."""
    real_path = os.path.join(
        os.path.dirname(__file__), '..', 'balancer', 'utils', 'logger.py'
    )
    spec = importlib.util.spec_from_file_location("_real_smartune_logger", real_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestLogLevelControl:
    """TC-LOG-003: log level controls which messages are emitted."""

    @pytest.fixture
    def make_logger(self, caplog):
        """Factory yielding a fresh real Logger whose records flow into caplog.

        The real Logger sets ``propagate = False`` and registers itself under
        its own module name, so caplog's named-logger machinery can't reach it.
        Instead we attach caplog's capturing handler directly to the underlying
        logger object.  Because caplog's handler has level 0, the only filter in
        play is the logger's own ``setLevel`` — which is exactly what TC-LOG-003
        exercises.
        """
        real_mod = _load_real_logger_module()
        created = []

        def _factory(level):
            inst = real_mod.Logger(log_file=None, log_level=level)
            log = inst.get_logger()
            log.addHandler(caplog.handler)
            created.append(log)
            return log

        yield _factory

        for log in created:
            log.removeHandler(caplog.handler)

    def test_warning_level_filters_info_and_debug(self, make_logger, caplog):
        log = make_logger(logging.WARNING)

        log.debug("debug-msg")
        log.info("info-msg")
        log.warning("warning-msg")
        log.error("error-msg")

        messages = [r.getMessage() for r in caplog.records]
        assert "debug-msg" not in messages
        assert "info-msg" not in messages
        assert "warning-msg" in messages
        assert "error-msg" in messages

    def test_info_level_filters_debug_only(self, make_logger, caplog):
        log = make_logger(logging.INFO)

        log.debug("debug-msg")
        log.info("info-msg")
        log.warning("warning-msg")
        log.error("error-msg")

        messages = [r.getMessage() for r in caplog.records]
        assert "debug-msg" not in messages
        assert "info-msg" in messages
        assert "warning-msg" in messages
        assert "error-msg" in messages

    def test_debug_level_captures_everything(self, make_logger, caplog):
        log = make_logger(logging.DEBUG)

        log.debug("debug-msg")
        log.info("info-msg")
        log.warning("warning-msg")
        log.error("error-msg")

        messages = [r.getMessage() for r in caplog.records]
        assert "debug-msg" in messages
        assert "info-msg" in messages
        assert "warning-msg" in messages
        assert "error-msg" in messages

    def test_critical_errors_logged_at_all_levels(self, make_logger, caplog):
        """A critical message must surface even when the level is raised to WARNING."""
        for level in (logging.DEBUG, logging.INFO, logging.WARNING):
            log = make_logger(level)
            log.critical("critical-msg")
            assert any("critical-msg" == r.getMessage() for r in caplog.records), (
                f"critical message should be logged at level {level}"
            )
            caplog.clear()


# ──────────────────────────────────────────────────────────────────────────────
# TC-LOG-005: Pressure event audit trail
# Verify that system pressure level transitions (entering/leaving critical) are
# logged, so a critical→non-critical timeline can be reconstructed from logs.
# Uses the AppIntercept._on_critical_state_changed callback with mocked BPF,
# mirroring tests/test_app_intercept.py.
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=False)
def _clear_intercept_singleton():
    from monitor.appIntercept import SingletonMeta
    SingletonMeta._instances.clear()
    yield
    SingletonMeta._instances.clear()


@pytest.fixture
def app_intercept(_clear_intercept_singleton):
    """A fresh AppIntercept with BPF, ControlManager and app_utils mocked out."""
    with patch('monitor.appIntercept.BPF') as mock_bpf, \
         patch('monitor.appIntercept.ControlManager') as mock_cm, \
         patch('monitor.appIntercept.app_utils') as mock_utils:
        mock_bpf.return_value = MagicMock()
        cm_instance = MagicMock()
        cm_instance.get_current_pressure_level.return_value = ("normal", 0.0, False)
        cm_instance.config = MagicMock()
        cm_instance.config.controlled_apps = []
        mock_cm.return_value = cm_instance
        mock_utils.get_controlled_apps.return_value = []
        mock_utils.callback_manager = MagicMock()

        from monitor.appIntercept import AppIntercept
        yield AppIntercept()


class TestPressureEventAuditTrail:
    """TC-LOG-005: pressure transitions produce audit log entries."""

    def test_entering_critical_is_logged(self, app_intercept, caplog):
        with caplog.at_level(logging.DEBUG, logger=TEST_LOGGER_NAME):
            app_intercept._on_critical_state_changed(True)

        assert app_intercept._system_critical.is_set()
        matches = [r for r in caplog.records
                   if r.levelno == logging.INFO
                   and 'critical' in r.getMessage().lower()
                   and 'entered' in r.getMessage().lower()]
        assert matches, (
            f"Entering critical pressure must be logged; "
            f"captured: {[r.getMessage() for r in caplog.records]}"
        )

    def test_leaving_critical_is_logged(self, app_intercept, caplog):
        app_intercept._system_critical.set()
        with caplog.at_level(logging.DEBUG, logger=TEST_LOGGER_NAME):
            app_intercept._on_critical_state_changed(False)

        assert not app_intercept._system_critical.is_set()
        matches = [r for r in caplog.records
                   if r.levelno == logging.INFO
                   and 'critical' in r.getMessage().lower()
                   and 'left' in r.getMessage().lower()]
        assert matches, (
            f"Leaving critical pressure must be logged; "
            f"captured: {[r.getMessage() for r in caplog.records]}"
        )

    def test_full_transition_timeline_reconstructable(self, app_intercept, caplog):
        """A low→critical→low cycle should leave an ordered, reconstructable trail."""
        with caplog.at_level(logging.DEBUG, logger=TEST_LOGGER_NAME):
            app_intercept._on_critical_state_changed(True)   # enter critical
            app_intercept._on_critical_state_changed(False)  # recover

        pressure_logs = [r.getMessage() for r in caplog.records
                         if 'critical' in r.getMessage().lower()
                         and r.levelno == logging.INFO]
        # Two ordered events: the entry must precede the recovery.
        assert len(pressure_logs) >= 2, f"Expected >=2 pressure logs, got {pressure_logs}"
        entered_idx = next(i for i, m in enumerate(pressure_logs) if 'entered' in m.lower())
        left_idx = next(i for i, m in enumerate(pressure_logs) if 'left' in m.lower())
        assert entered_idx < left_idx, (
            f"Audit trail order is wrong (entered must precede left): {pressure_logs}"
        )
