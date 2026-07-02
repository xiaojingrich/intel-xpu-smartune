# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for db/DatabaseModel.py — CRUD operations, thread safety, and migrations."""

import os
import sys
import time
import threading
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))

from peewee import SqliteDatabase
from db.DatabaseModel import (
    AIAppPriority, MonitorSnapshot, DataBaseModel,
    DBStatus, db, db_lock, init_database
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    """Bind models to a temporary SQLite database for each test."""
    test_db_path = str(tmp_path / "test.db")
    test_db = SqliteDatabase(test_db_path)
    test_db.bind([AIAppPriority, MonitorSnapshot])
    test_db.connect()
    test_db.create_tables([AIAppPriority, MonitorSnapshot])
    yield test_db
    test_db.close()


class TestAIAppPriorityInsert:
    def test_insert_new_record(self):
        result = AIAppPriority.insert_record(
            id="app1",
            app_id="app1",
            name="Test App",
            priority=50,
            controlled=False,
            remark="",
            cmdline="test_cmd",
            status="NA",
            last_update_time=datetime.now()
        )
        assert result == DBStatus.SUCCESS

    def test_insert_duplicate_returns_already_existing(self):
        AIAppPriority.insert_record(
            id="app2", app_id="app2", name="App2",
            priority=0, controlled=False, remark="",
            cmdline="", status="NA", last_update_time=datetime.now()
        )
        result = AIAppPriority.insert_record(
            id="app2", app_id="app2", name="App2 Dup",
            priority=0, controlled=False, remark="",
            cmdline="", status="NA", last_update_time=datetime.now()
        )
        assert result == DBStatus.ALREADY_EXISTING

    def test_insert_sets_timestamps(self):
        before = int(time.time())
        AIAppPriority.insert_record(
            id="app3", app_id="app3", name="App3",
            priority=0, controlled=False, remark="",
            cmdline="", status="NA", last_update_time=datetime.now()
        )
        record = AIAppPriority.get_by_id("app3")
        assert record.create_time >= before
        assert record.update_time >= before


class TestAIAppPriorityUpdate:
    def test_update_existing_record(self):
        AIAppPriority.insert_record(
            id="up1", app_id="up1", name="Update App",
            priority=20, controlled=False, remark="",
            cmdline="", status="NA", last_update_time=datetime.now()
        )
        result = AIAppPriority.update_record(id="up1", priority=80, status="running")
        assert result == DBStatus.SUCCESS

        record = AIAppPriority.get_by_id("up1")
        assert record.priority == 80
        assert record.status == "running"

    def test_update_nonexistent_returns_not_found(self):
        result = AIAppPriority.update_record(id="nonexistent", priority=50)
        assert result == DBStatus.NOT_FOUND

    def test_update_all_records(self):
        for i in range(3):
            AIAppPriority.insert_record(
                id=f"batch{i}", app_id=f"batch{i}", name=f"Batch{i}",
                priority=0, controlled=True, remark="",
                cmdline="", status="running", last_update_time=datetime.now()
            )
        count = AIAppPriority.update_all_records(status="NA")
        assert count == 3

        for i in range(3):
            record = AIAppPriority.get_by_id(f"batch{i}")
            assert record.status == "NA"


class TestAIAppPriorityQuery:
    def test_query_all(self):
        for i in range(5):
            AIAppPriority.insert_record(
                id=f"q{i}", app_id=f"q{i}", name=f"Query{i}",
                priority=i * 20, controlled=(i % 2 == 0), remark="",
                cmdline="", status="NA", last_update_time=datetime.now()
            )
        results = AIAppPriority.query()
        assert len(list(results)) == 5

    def test_query_with_filter(self):
        for i in range(5):
            AIAppPriority.insert_record(
                id=f"f{i}", app_id=f"f{i}", name=f"Filter{i}",
                priority=i * 20, controlled=(i % 2 == 0), remark="",
                cmdline="", status="NA", last_update_time=datetime.now()
            )
        results = AIAppPriority.query().where(AIAppPriority.controlled == True)
        assert len(list(results)) == 3


class TestAIAppPriorityDelete:
    def test_delete_existing_record(self):
        AIAppPriority.insert_record(
            id="del1", app_id="del1", name="Delete App",
            priority=0, controlled=False, remark="",
            cmdline="", status="NA", last_update_time=datetime.now()
        )
        count = AIAppPriority.delete_record(id="del1")
        assert count == 1

    def test_delete_nonexistent_record(self):
        count = AIAppPriority.delete_record(id="nonexist")
        assert count == 0


class TestMonitorSnapshot:
    def test_insert_snapshot(self):
        data = {"cpu_usage": 45.2, "memory_usage": 60.1}
        result = MonitorSnapshot.insert_snapshot(
            snapshot_type="dynamic",
            data=data,
            collected_at="2026-01-01T00:00:00"
        )
        assert result == DBStatus.SUCCESS

    def test_query_recent_by_type(self):
        for i in range(5):
            MonitorSnapshot.insert_snapshot(
                snapshot_type="dynamic" if i % 2 == 0 else "static",
                data={"value": i},
                collected_at=f"2026-01-0{i+1}T00:00:00"
            )
        results = MonitorSnapshot.query_recent(snapshot_type="dynamic", limit=10)
        assert len(results) == 3

    def test_query_recent_with_time_range(self):
        now = int(time.time())
        for i in range(5):
            MonitorSnapshot.insert_snapshot(
                snapshot_type="dynamic",
                data={"value": i},
            )
        results = MonitorSnapshot.query_recent(
            snapshot_type="dynamic",
            start_time=now - 10,
            end_time=now + 10
        )
        assert len(results) == 5

    def test_delete_older_than(self):
        MonitorSnapshot.insert_snapshot(
            snapshot_type="dynamic",
            data={"value": "old"},
        )
        # Manually backdate the record
        old_time = int(time.time()) - 86400 * 10
        MonitorSnapshot.update(create_time=old_time).execute()

        deleted = MonitorSnapshot.delete_older_than(days=5)
        assert deleted >= 1

    def test_insert_snapshot_with_complex_data(self):
        data = {
            "cpu": {"count": 8, "usage": [10, 20, 30, 40, 50, 60, 70, 80]},
            "memory": {"total": 16384, "used": 8192},
            "gpu": [{"name": "Intel Arc", "util": 45.5}]
        }
        result = MonitorSnapshot.insert_snapshot(
            snapshot_type="dynamic",
            data=data,
            source="test_module"
        )
        assert result == DBStatus.SUCCESS

        records = MonitorSnapshot.query_recent(snapshot_type="dynamic")
        assert len(records) == 1
        import json
        parsed = json.loads(records[0].data_json)
        assert parsed['cpu']['count'] == 8


class TestDatabaseConcurrency:
    def test_concurrent_inserts(self):
        """Multiple threads inserting simultaneously should not corrupt the DB."""
        errors = []

        def insert_records(thread_id):
            try:
                for i in range(20):
                    AIAppPriority.insert_record(
                        id=f"t{thread_id}_r{i}",
                        app_id=f"t{thread_id}_r{i}",
                        name=f"Thread{thread_id}_Record{i}",
                        priority=thread_id,
                        controlled=False,
                        remark="",
                        cmdline="",
                        status="NA",
                        last_update_time=datetime.now()
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=insert_records, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        results = list(AIAppPriority.query())
        assert len(results) == 100

    def test_concurrent_read_write(self):
        """Reads during writes should not raise."""
        AIAppPriority.insert_record(
            id="rw_test", app_id="rw_test", name="RW",
            priority=0, controlled=True, remark="",
            cmdline="", status="NA", last_update_time=datetime.now()
        )
        errors = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    list(AIAppPriority.query().where(AIAppPriority.controlled == True))
                except Exception as e:
                    errors.append(e)

        def writer():
            for i in range(50):
                AIAppPriority.update_record(id="rw_test", priority=i)

        readers = [threading.Thread(target=reader) for _ in range(3)]
        for t in readers:
            t.start()

        w = threading.Thread(target=writer)
        w.start()
        w.join()
        stop.set()

        for t in readers:
            t.join()
        assert not errors
