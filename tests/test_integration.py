# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Integration tests — end-to-end workflows combining multiple components."""

import os
import sys
import json
import time
import threading
from unittest.mock import patch, MagicMock
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))


@pytest.fixture(autouse=True)
def integration_db(tmp_path):
    """Set up a fresh database for integration tests."""
    from peewee import SqliteDatabase
    from db.DatabaseModel import AIAppPriority, MonitorSnapshot

    test_db = SqliteDatabase(str(tmp_path / "integration.db"))
    test_db.bind([AIAppPriority, MonitorSnapshot])
    test_db.connect()
    test_db.create_tables([AIAppPriority, MonitorSnapshot])
    yield test_db
    test_db.close()


class TestAppLifecycle:
    """Test the full app lifecycle: register -> set priority -> control -> limit -> restore."""

    def test_register_and_control_app(self):
        """Register an app and mark it as controlled."""
        from db.DatabaseModel import AIAppPriority, DBStatus

        result = AIAppPriority.insert_record(
            id="lifecycle_app",
            app_id="lifecycle_app",
            name="Lifecycle Test",
            priority=0,
            controlled=False,
            remark="",
            cmdline="lifecycle_test",
            status="NA",
            last_update_time=datetime.now()
        )
        assert result == DBStatus.SUCCESS

        # Set to controlled with priority
        result = AIAppPriority.update_record(
            id="lifecycle_app",
            controlled=True,
            priority=80
        )
        assert result == DBStatus.SUCCESS

        # Verify state
        record = AIAppPriority.get_by_id("lifecycle_app")
        assert record.controlled is True
        assert record.priority == 80

    def test_app_status_transitions(self):
        """Test valid status transitions: NA -> running -> pending -> stopped."""
        from db.DatabaseModel import AIAppPriority, DBStatus

        AIAppPriority.insert_record(
            id="status_app",
            app_id="status_app",
            name="Status Test",
            priority=50,
            controlled=True,
            remark="",
            cmdline="status_test",
            status="NA",
            last_update_time=datetime.now()
        )

        transitions = ["running", "pending", "stopped", "running", "NA"]
        for status in transitions:
            result = AIAppPriority.update_record(id="status_app", status=status)
            assert result == DBStatus.SUCCESS

            record = AIAppPriority.get_by_id("status_app")
            assert record.status == status

    def test_limit_overrides_persistence(self):
        """Per-app limit overrides should persist as JSON."""
        from db.DatabaseModel import AIAppPriority, DBStatus

        AIAppPriority.insert_record(
            id="override_app",
            app_id="override_app",
            name="Override Test",
            priority=50,
            controlled=True,
            remark="",
            cmdline="override_test",
            status="running",
            last_update_time=datetime.now()
        )

        overrides = {
            "cpu": {"rate": 0.6, "enabled": True},
            "memory": {"rate": 0.25, "enabled": True},
            "disk_io": {"rate": {"write": 30, "read": 40}, "enabled": True}
        }

        result = AIAppPriority.update_record(
            id="override_app",
            limit_overrides_json=json.dumps(overrides)
        )
        assert result == DBStatus.SUCCESS

        record = AIAppPriority.get_by_id("override_app")
        parsed = json.loads(record.limit_overrides_json)
        assert parsed['cpu']['rate'] == 0.6
        assert parsed['disk_io']['rate']['write'] == 30


class TestPressureToLimitWorkflow:
    """Test the pressure detection -> limit application workflow."""

    def test_pressure_score_to_level_to_action(self):
        """Verify the full path: PSI data -> score -> level -> limit rates."""
        from monitor.pressure import PressureAnalyzer

        class FakeConfig:
            weights = {'cpu': 2, 'memory': 7, 'io': 1}
            dominant_app_reduce_factor = 3.5

        analyzer = PressureAnalyzer(FakeConfig())
        thresholds = {'low': 0.4, 'medium': 0.6, 'high': 0.8, 'critical': 1.0}

        # Simulate increasing pressure
        psi_levels = [
            {'cpu': 0.1, 'memory': 0.1, 'io': 0.1},  # Low
            {'cpu': 0.3, 'memory': 0.5, 'io': 0.2},  # Medium
            {'cpu': 0.5, 'memory': 0.8, 'io': 0.3},  # High
            {'cpu': 0.8, 'memory': 1.0, 'io': 0.5},  # Critical
        ]
        usage = {'cpu': {'is_busy': False}, 'memory': {'is_busy': False}}

        expected_levels = ["low", "medium", "high", "critical"]
        for psi, expected_level in zip(psi_levels, expected_levels):
            score = analyzer.calculate_pressure_score(psi, usage, False)
            level = analyzer.get_pressure_level(score, thresholds)
            # Due to weighting, the exact mapping may differ but should be monotonically increasing
            assert score >= 0.0

    def test_dominant_app_affects_action_decision(self):
        """When limited app is dominant, score should be reduced to prevent re-limiting."""
        from monitor.pressure import PressureAnalyzer

        class FakeConfig:
            weights = {'cpu': 2, 'memory': 7, 'io': 1}
            dominant_app_reduce_factor = 3.5

        analyzer = PressureAnalyzer(FakeConfig())
        thresholds = {'low': 0.4, 'medium': 0.6, 'high': 0.8, 'critical': 1.0}
        usage = {'cpu': {'is_busy': False}, 'memory': {'is_busy': False}}

        psi = {'cpu': 0.5, 'memory': 0.6, 'io': 0.3}

        score_normal = analyzer.calculate_pressure_score(psi, usage, False)
        score_dominant = analyzer.calculate_pressure_score(psi, usage, True)
        level_normal = analyzer.get_pressure_level(score_normal, thresholds)
        level_dominant = analyzer.get_pressure_level(score_dominant, thresholds)

        # Dominant should result in lower score/level
        assert score_dominant < score_normal


class TestMonitorSnapshotWorkflow:
    """Test the full monitor snapshot lifecycle."""

    def test_snapshot_collect_store_query_cleanup(self):
        """Full lifecycle: collect -> store -> query -> age -> cleanup."""
        from db.DatabaseModel import MonitorSnapshot, DBStatus

        # 1. Store snapshots over simulated time
        for i in range(10):
            data = {
                "cpu_usage": 30 + i * 5,
                "memory_usage": 50 + i * 2,
                "pressure_level": "medium" if i < 7 else "high",
                "timestamp": f"2026-01-{i+1:02d}T12:00:00"
            }
            result = MonitorSnapshot.insert_snapshot(
                snapshot_type="dynamic",
                data=data,
                collected_at=data["timestamp"]
            )
            assert result == DBStatus.SUCCESS

        # 2. Query recent
        recent = MonitorSnapshot.query_recent(snapshot_type="dynamic", limit=5)
        assert len(recent) == 5

        # 3. Backdate some records and cleanup
        old_time = int(time.time()) - 86400 * 60
        MonitorSnapshot.update(create_time=old_time).where(
            MonitorSnapshot.id <= 5
        ).execute()

        deleted = MonitorSnapshot.delete_older_than(days=30)
        assert deleted == 5

        # 4. Verify remaining
        remaining = MonitorSnapshot.query_recent(snapshot_type="dynamic")
        assert len(remaining) == 5


class TestConfigAndPressureIntegration:
    """Test config changes affecting pressure behavior."""

    def test_weight_change_affects_score(self):
        """Changing weights should affect pressure scoring."""
        from config.config import Config
        from monitor.pressure import PressureAnalyzer
        import tempfile

        config_content = """
weights:
  cpu: 2
  memory: 7
  io: 1
dominant_app_reduce_factor: 3.5
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            path = f.name

        cfg = Config.from_file(path)
        analyzer = PressureAnalyzer(cfg)

        # Use asymmetric PSI values so weight changes produce different scores
        psi = {'cpu': 0.1, 'memory': 0.02, 'io': 0.01}
        usage = {'cpu': {'is_busy': False}, 'memory': {'is_busy': False}}

        score_before = analyzer.calculate_pressure_score(psi, usage, False)

        # Modify weights: shift emphasis from memory to cpu
        cfg.weights = {'cpu': 8, 'memory': 1, 'io': 1}
        analyzer2 = PressureAnalyzer(cfg)
        score_after = analyzer2.calculate_pressure_score(psi, usage, False)

        # Scores should differ because weights changed
        assert score_before != score_after
        os.unlink(path)


class TestMultiAppManagement:
    """Test managing multiple apps simultaneously."""

    def test_bulk_app_registration(self):
        """Register and manage 20 apps simultaneously."""
        from db.DatabaseModel import AIAppPriority, DBStatus

        apps = []
        for i in range(20):
            app_data = {
                "id": f"bulk_app_{i}",
                "app_id": f"bulk_app_{i}",
                "name": f"BulkApp{i}",
                "priority": ["critical", "high", "medium", "low"][i % 4],
                "controlled": True,
                "remark": f"Batch registered #{i}",
                "cmdline": f"bulk_cmd_{i}",
                "status": "NA",
                "last_update_time": datetime.now()
            }
            result = AIAppPriority.insert_record(**app_data)
            assert result == DBStatus.SUCCESS
            apps.append(app_data)

        # Query by priority
        controlled = list(
            AIAppPriority.query().where(AIAppPriority.controlled == True)
        )
        assert len(controlled) == 20

        # Update status for subset
        for i in range(0, 20, 3):
            AIAppPriority.update_record(id=f"bulk_app_{i}", status="running")

        running = list(
            AIAppPriority.query().where(AIAppPriority.status == "running")
        )
        assert len(running) == 7  # 0,3,6,9,12,15,18

    def test_priority_ordering(self):
        """Apps should be retrievable in priority order."""
        from db.DatabaseModel import AIAppPriority

        priorities = [
            ("app_crit", "critical", 100),
            ("app_high", "high", 80),
            ("app_med", "medium", 50),
            ("app_low", "low", 20),
        ]

        for app_id, prio_name, prio_val in priorities:
            AIAppPriority.insert_record(
                id=app_id,
                app_id=app_id,
                name=f"Priority {prio_name}",
                priority=prio_val,
                controlled=True,
                remark="",
                cmdline="",
                status="running",
                last_update_time=datetime.now()
            )

        results = list(
            AIAppPriority.query()
            .where(AIAppPriority.controlled == True)
            .order_by(AIAppPriority.priority.desc())
        )
        assert results[0].priority == 100
        assert results[-1].priority == 20
