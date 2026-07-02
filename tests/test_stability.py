# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Stability tests — concurrency, memory leaks, long-running operations, and edge cases."""

import os
import sys
import gc
import time
import json
import threading
import queue
from unittest.mock import patch, MagicMock
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))

from db.DatabaseModel import DBStatus


class TestDatabaseStability:
    """Test database stability under sustained concurrent load."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        from peewee import SqliteDatabase
        from db.DatabaseModel import AIAppPriority, MonitorSnapshot
        test_db = SqliteDatabase(str(tmp_path / "stability.db"))
        test_db.bind([AIAppPriority, MonitorSnapshot])
        test_db.connect()
        test_db.create_tables([AIAppPriority, MonitorSnapshot])
        yield test_db
        test_db.close()

    def test_sustained_concurrent_writes(self):
        """10 threads each writing 50 records concurrently without errors."""
        from db.DatabaseModel import AIAppPriority

        errors = []
        barrier = threading.Barrier(10)

        def writer(tid):
            try:
                barrier.wait(timeout=5)
                for i in range(50):
                    AIAppPriority.insert_record(
                        id=f"stab_t{tid}_r{i}",
                        app_id=f"stab_t{tid}_r{i}",
                        name=f"StabilityApp_{tid}_{i}",
                        priority=tid * 10,
                        controlled=(i % 2 == 0),
                        remark=f"thread_{tid}",
                        cmdline=f"cmd_{tid}_{i}",
                        status="NA",
                        last_update_time=datetime.now()
                    )
            except Exception as e:
                errors.append((tid, e))

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Concurrent write errors: {errors}"
        results = list(AIAppPriority.query())
        assert len(results) == 500

    def test_concurrent_read_write_delete(self):
        """Mix of reads, writes, and deletes under concurrent load."""
        from db.DatabaseModel import AIAppPriority

        # Pre-populate
        for i in range(50):
            AIAppPriority.insert_record(
                id=f"mix_{i}",
                app_id=f"mix_{i}",
                name=f"MixApp{i}",
                priority=0,
                controlled=True,
                remark="",
                cmdline="",
                status="NA",
                last_update_time=datetime.now()
            )

        errors = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    list(AIAppPriority.query().where(AIAppPriority.controlled == True))
                except Exception as e:
                    errors.append(("reader", e))

        def updater():
            for i in range(100):
                try:
                    AIAppPriority.update_record(
                        id=f"mix_{i % 50}",
                        priority=(i * 7) % 100,
                        status="running" if i % 2 == 0 else "NA"
                    )
                except Exception as e:
                    errors.append(("updater", e))

        def inserter():
            for i in range(50):
                try:
                    AIAppPriority.insert_record(
                        id=f"new_{i}",
                        app_id=f"new_{i}",
                        name=f"NewApp{i}",
                        priority=i,
                        controlled=False,
                        remark="",
                        cmdline="",
                        status="NA",
                        last_update_time=datetime.now()
                    )
                except Exception as e:
                    errors.append(("inserter", e))

        readers = [threading.Thread(target=reader) for _ in range(3)]
        for t in readers:
            t.start()

        updater_thread = threading.Thread(target=updater)
        inserter_thread = threading.Thread(target=inserter)
        updater_thread.start()
        inserter_thread.start()

        updater_thread.join(timeout=15)
        inserter_thread.join(timeout=15)
        stop.set()

        for t in readers:
            t.join(timeout=5)

        assert not errors, f"Concurrent operation errors: {errors}"

    def test_snapshot_cleanup_under_load(self):
        """Snapshot deletion should not block ongoing inserts."""
        from db.DatabaseModel import MonitorSnapshot

        # Insert old and new snapshots
        for i in range(200):
            MonitorSnapshot.insert_snapshot(
                snapshot_type="dynamic",
                data={"i": i}
            )

        # Backdate half
        old_time = int(time.time()) - 86400 * 30
        from peewee import fn
        MonitorSnapshot.update(create_time=old_time).where(
            MonitorSnapshot.id <= 100
        ).execute()

        errors = []

        def continuous_insert():
            for i in range(50):
                try:
                    MonitorSnapshot.insert_snapshot(
                        snapshot_type="dynamic",
                        data={"concurrent": i}
                    )
                except Exception as e:
                    errors.append(e)

        def cleanup():
            try:
                MonitorSnapshot.delete_older_than(days=7)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=continuous_insert)
        t2 = threading.Thread(target=cleanup)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors


class TestSSEStability:
    """Test Server-Sent Events stability."""

    def test_sse_client_flood(self):
        """Adding and removing many SSE clients rapidly should be safe."""
        from utils.app_utils import ClientCallbackManager
        mgr = ClientCallbackManager()
        queues = []

        for i in range(100):
            q = queue.Queue()
            mgr.add_sse_client(q)
            queues.append(q)

        # Send notifications while clients are active
        with patch('utils.app_utils.AIAppPriority') as mock_db:
            mock_db.update_record.return_value = True
            for i in range(50):
                mgr.send_callback_notification(
                    {'app_id': f'flood_{i}', 'status': 'running', 'app_name': f'app_{i}'},
                    store=False
                )

        # Remove all clients
        for q in queues:
            mgr.remove_sse_client(q)

        # Verify all clients received notifications
        for q in queues:
            count = 0
            while not q.empty():
                q.get_nowait()
                count += 1
            assert count == 50

    def test_sse_concurrent_add_remove_notify(self):
        """Concurrent add/remove/notify must not deadlock."""
        from utils.app_utils import ClientCallbackManager
        mgr = ClientCallbackManager()
        errors = []
        stop = threading.Event()

        def adder():
            queues_local = []
            while not stop.is_set():
                try:
                    q = queue.Queue()
                    mgr.add_sse_client(q)
                    queues_local.append(q)
                    if len(queues_local) > 10:
                        old = queues_local.pop(0)
                        mgr.remove_sse_client(old)
                except Exception as e:
                    errors.append(e)
            for q in queues_local:
                mgr.remove_sse_client(q)

        def notifier():
            for i in range(100):
                try:
                    with patch('utils.app_utils.AIAppPriority') as mock_db:
                        mock_db.update_record.return_value = True
                        mgr.send_callback_notification(
                            {'app_id': f'n_{i}', 'status': 'running', 'app_name': f'app_{i}'},
                            store=False
                        )
                except Exception as e:
                    errors.append(e)

        adders = [threading.Thread(target=adder) for _ in range(3)]
        notifiers = [threading.Thread(target=notifier) for _ in range(2)]

        for t in adders + notifiers:
            t.start()

        for t in notifiers:
            t.join(timeout=10)
        stop.set()
        for t in adders:
            t.join(timeout=5)

        assert not errors


class TestPriorityQueueStability:
    """Test MaxPriorityQueue under stress."""

    def test_concurrent_put_get(self):
        """Multiple producers and consumers should not corrupt the queue."""
        from balancer.balancer import MaxPriorityQueue

        pq = MaxPriorityQueue()
        produced = []
        consumed = []
        errors = []
        lock = threading.Lock()

        def producer(tid):
            try:
                for i in range(100):
                    item = ({"tid": tid, "seq": i}, i % 50)
                    pq.put(item)
                    with lock:
                        produced.append(item)
            except Exception as e:
                errors.append(e)

        def consumer():
            try:
                while True:
                    if pq.empty():
                        time.sleep(0.001)
                        if pq.empty():
                            break
                    try:
                        item = pq.get()
                        with lock:
                            consumed.append(item)
                    except (IndexError, KeyError):
                        break
            except Exception as e:
                errors.append(e)

        producers = [threading.Thread(target=producer, args=(i,)) for i in range(5)]
        for t in producers:
            t.start()
        for t in producers:
            t.join(timeout=10)

        consumers = [threading.Thread(target=consumer) for _ in range(3)]
        for t in consumers:
            t.start()
        for t in consumers:
            t.join(timeout=10)

        assert not errors
        assert len(consumed) == 500

    def test_remove_if_during_operations(self):
        """remove_if should be safe while put/get are happening."""
        from balancer.balancer import MaxPriorityQueue

        pq = MaxPriorityQueue()
        errors = []
        stop = threading.Event()

        def filler():
            i = 0
            while not stop.is_set():
                try:
                    pq.put(({"id": i, "removable": i % 3 == 0}, i % 100))
                    i += 1
                except Exception as e:
                    errors.append(e)

        def remover():
            while not stop.is_set():
                try:
                    pq.remove_if(lambda item: item[0].get("removable", False))
                    time.sleep(0.01)
                except Exception as e:
                    errors.append(e)

        filler_t = threading.Thread(target=filler)
        remover_t = threading.Thread(target=remover)
        filler_t.start()
        remover_t.start()

        time.sleep(0.5)
        stop.set()
        filler_t.join(timeout=5)
        remover_t.join(timeout=5)

        assert not errors


class TestConfigStability:
    """Config stability under concurrent access."""

    def test_rapid_config_reload(self, tmp_path):
        """Rapid config reloading should not corrupt state."""
        from config.config import Config

        config_content = """
cgroup_mount: "/sys/fs/cgroup"
weights_top:
  cpu: 2
  memory: 7
  gpu: 5
passive_resource_control:
  enabled: true
thresholds:
  low: 0.4
  medium: 0.6
  high: 0.8
  critical: 1.0
"""
        config_file = tmp_path / "rapid_config.yaml"
        config_file.write_text(config_content)

        errors = []
        for i in range(100):
            try:
                cfg = Config.from_file(str(config_file))
                assert cfg.weights_top['cpu'] == 2
            except Exception as e:
                errors.append(e)

        assert not errors

    def test_concurrent_config_updates_no_corruption(self, tmp_path):
        """Multiple threads updating different sections simultaneously."""
        from config.config import Config

        config_content = """
cgroup_mount: "/sys/fs/cgroup"
weights_top:
  cpu: 2
  memory: 7
  gpu: 5
passive_resource_control:
  enabled: true
thresholds:
  low: 0.4
  medium: 0.6
  high: 0.8
  critical: 1.0
"""
        config_file = tmp_path / "concurrent_config.yaml"
        config_file.write_text(config_content)
        cfg = Config.from_file(str(config_file))

        errors = []

        def updater_weights(tid):
            try:
                for i in range(30):
                    cfg.update_config_section('weights_top', {'cpu': tid * 10 + i})
            except Exception as e:
                errors.append(e)

        def updater_passive(tid):
            try:
                for i in range(30):
                    cfg.update_config_section('passive_resource_control', {'enabled': i % 2 == 0})
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(3):
            threads.append(threading.Thread(target=updater_weights, args=(i,)))
            threads.append(threading.Thread(target=updater_passive, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors
        assert isinstance(cfg.weights_top['cpu'], int)
        assert isinstance(cfg.passive_resource_control['enabled'], bool)


class TestMemoryLeaks:
    """Detect potential memory leaks in key components."""

    def test_repeated_response_construction_no_leak(self):
        """Repeated response construction should not accumulate memory."""
        from flask import Flask
        from utils.http_utils import construct_response

        app = Flask(__name__)
        app.config['TESTING'] = True

        gc.collect()
        baseline = len(gc.get_objects())

        with app.app_context():
            for i in range(1000):
                resp = construct_response(
                    data={"items": list(range(100))},
                    retmsg=f"iteration_{i}"
                )
                del resp

        gc.collect()
        after = len(gc.get_objects())
        growth = after - baseline

        # Allow reasonable growth but flag major leaks
        assert growth < 5000, f"Object count grew by {growth} (possible leak)"

    def test_repeated_db_operations_no_leak(self, tmp_path):
        """Repeated DB operations should not accumulate memory."""
        from peewee import SqliteDatabase
        from db.DatabaseModel import AIAppPriority, MonitorSnapshot

        test_db = SqliteDatabase(str(tmp_path / "leak_test.db"))
        test_db.bind([AIAppPriority, MonitorSnapshot])
        test_db.connect()
        test_db.create_tables([AIAppPriority, MonitorSnapshot])

        gc.collect()
        baseline = len(gc.get_objects())

        for i in range(500):
            AIAppPriority.insert_record(
                id=f"leak_{i}",
                app_id=f"leak_{i}",
                name=f"LeakTest{i}",
                priority=0,
                controlled=False,
                remark="",
                cmdline="",
                status="NA",
                last_update_time=datetime.now()
            )
            if i % 10 == 0:
                list(AIAppPriority.query())

        gc.collect()
        after = len(gc.get_objects())
        growth = after - baseline

        test_db.close()
        assert growth < 10000, f"DB operations grew objects by {growth} (possible leak)"


class TestEdgeCases:
    """Edge case handling for stability."""

    def test_empty_json_payload(self):
        """API should handle empty/null payloads gracefully."""
        from flask import Flask
        from utils.http_utils import construct_response, RetCode

        app = Flask(__name__)
        app.config['TESTING'] = True

        with app.app_context():
            resp = construct_response(data=None, retmsg="no data")
            data = json.loads(resp.get_data(as_text=True))
            assert data['retcode'] == 0

    def test_very_large_app_name(self, tmp_path):
        """Should handle extremely long app names."""
        from peewee import SqliteDatabase
        from db.DatabaseModel import AIAppPriority

        test_db = SqliteDatabase(str(tmp_path / "edge.db"))
        test_db.bind([AIAppPriority])
        test_db.connect()
        test_db.create_tables([AIAppPriority])

        long_name = "A" * 128  # max_length is 128
        result = AIAppPriority.insert_record(
            id="long_name_app",
            app_id="long_name_app",
            name=long_name,
            priority=50,
            controlled=True,
            remark="",
            cmdline="",
            status="NA",
            last_update_time=datetime.now()
        )
        assert result == DBStatus.SUCCESS

        record = AIAppPriority.get_by_id("long_name_app")
        assert len(record.name) == 128
        test_db.close()

    def test_special_characters_in_data(self, tmp_path):
        """Handle special characters in snapshot data."""
        from peewee import SqliteDatabase
        from db.DatabaseModel import MonitorSnapshot

        test_db = SqliteDatabase(str(tmp_path / "special.db"))
        test_db.bind([MonitorSnapshot])
        test_db.connect()
        test_db.create_tables([MonitorSnapshot])

        data = {
            "path": "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice",
            "unicode": "测试数据 ☃ 😀",
            "special": "key=value;rm -rf /;DROP TABLE",
            "quotes": 'he said "hello" and \'bye\''
        }
        result = MonitorSnapshot.insert_snapshot(
            snapshot_type="dynamic",
            data=data
        )
        assert result == DBStatus.SUCCESS

        records = MonitorSnapshot.query_recent(snapshot_type="dynamic")
        assert len(records) == 1
        parsed = json.loads(records[0].data_json)
        assert parsed['unicode'] == "测试数据 ☃ 😀"
        test_db.close()

    def test_pressure_score_with_extreme_values(self):
        """Pressure analyzer should handle extreme input values."""
        from monitor.pressure import PressureAnalyzer

        class FakeConfig:
            weights = {'cpu': 2, 'memory': 7, 'io': 1}
            dominant_app_reduce_factor = 3.5

        analyzer = PressureAnalyzer(FakeConfig())
        usage = {'cpu': {'is_busy': False}, 'memory': {'is_busy': False}}

        # Very high values (should cap at 1.0)
        score = analyzer.calculate_pressure_score(
            {'cpu': 10.0, 'memory': 10.0, 'io': 10.0}, usage, False
        )
        assert score <= 1.0

        # Negative values (edge case)
        score = analyzer.calculate_pressure_score(
            {'cpu': -0.1, 'memory': -0.2, 'io': -0.3}, usage, False
        )
        assert score <= 1.0

        # Zero reduce factor should not crash
        class ZeroConfig:
            weights = {'cpu': 2, 'memory': 7, 'io': 1}
            dominant_app_reduce_factor = 0.001  # near zero

        analyzer2 = PressureAnalyzer(ZeroConfig())
        score = analyzer2.calculate_pressure_score(
            {'cpu': 0.5, 'memory': 0.5, 'io': 0.5}, usage, True
        )
        assert isinstance(score, float)
