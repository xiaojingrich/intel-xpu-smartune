# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Performance tests — response times, throughput, and resource usage benchmarks."""

import os
import sys
import time
import json
import hashlib
import threading
import statistics
from unittest.mock import patch, MagicMock
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))


@pytest.fixture
def perf_db(tmp_path):
    """Set up a performance-test database."""
    from peewee import SqliteDatabase
    from db.DatabaseModel import AIAppPriority, MonitorSnapshot

    test_db_path = str(tmp_path / "perf_test.db")
    test_db = SqliteDatabase(test_db_path)
    test_db.bind([AIAppPriority, MonitorSnapshot])
    test_db.connect()
    test_db.create_tables([AIAppPriority, MonitorSnapshot])
    yield test_db
    test_db.close()


class TestDatabasePerformance:
    """Benchmark database operations under load."""

    def test_bulk_insert_throughput(self, perf_db):
        """Measure insert throughput: should handle 100 records within 2 seconds."""
        from db.DatabaseModel import AIAppPriority

        start = time.monotonic()
        for i in range(100):
            AIAppPriority.insert_record(
                id=f"perf_{i}",
                app_id=f"perf_{i}",
                name=f"PerfApp{i}",
                priority=i % 4 * 20,
                controlled=(i % 3 == 0),
                remark="performance test",
                cmdline=f"perf_cmd_{i}",
                status="NA",
                last_update_time=datetime.now()
            )
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, f"Bulk insert of 100 records took {elapsed:.2f}s (expected <2s)"

    def test_query_performance_with_many_records(self, perf_db):
        """Query performance with 500 records should be under 500ms."""
        from db.DatabaseModel import AIAppPriority

        for i in range(500):
            AIAppPriority.insert_record(
                id=f"qp_{i}",
                app_id=f"qp_{i}",
                name=f"QueryPerf{i}",
                priority=i % 4 * 20,
                controlled=(i % 2 == 0),
                remark="",
                cmdline="",
                status="running" if i % 3 == 0 else "NA",
                last_update_time=datetime.now()
            )

        start = time.monotonic()
        results = list(AIAppPriority.query().where(AIAppPriority.controlled == True))
        elapsed = time.monotonic() - start

        assert len(results) == 250
        assert elapsed < 0.5, f"Query of controlled apps took {elapsed:.3f}s (expected <500ms)"

    def test_update_performance(self, perf_db):
        """Updating 100 records individually should be under 2 seconds."""
        from db.DatabaseModel import AIAppPriority

        for i in range(100):
            AIAppPriority.insert_record(
                id=f"up_{i}",
                app_id=f"up_{i}",
                name=f"UpdatePerf{i}",
                priority=0,
                controlled=False,
                remark="",
                cmdline="",
                status="NA",
                last_update_time=datetime.now()
            )

        start = time.monotonic()
        for i in range(100):
            AIAppPriority.update_record(id=f"up_{i}", status="running", priority=50)
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, f"100 individual updates took {elapsed:.2f}s (expected <2s)"

    def test_snapshot_insert_throughput(self, perf_db):
        """Monitor snapshots should handle 200 inserts within 3 seconds."""
        from db.DatabaseModel import MonitorSnapshot

        data = {
            "cpu": {"count": 8, "usage": 45.2},
            "memory": {"total": 16384, "used": 8192},
            "gpu": [{"name": "Intel Arc", "util": 30.0}],
            "disk": {"read_mb": 100, "write_mb": 50}
        }

        start = time.monotonic()
        for i in range(200):
            MonitorSnapshot.insert_snapshot(
                snapshot_type="dynamic",
                data=data,
                collected_at=f"2026-01-01T{i//60:02d}:{i%60:02d}:00"
            )
        elapsed = time.monotonic() - start

        assert elapsed < 3.0, f"200 snapshot inserts took {elapsed:.2f}s (expected <3s)"

    def test_snapshot_query_with_time_range(self, perf_db):
        """Querying snapshots by time range over 1000 records should be fast."""
        from db.DatabaseModel import MonitorSnapshot

        base_time = int(time.time()) - 3600
        for i in range(1000):
            MonitorSnapshot.insert_snapshot(
                snapshot_type="dynamic",
                data={"value": i},
            )

        start = time.monotonic()
        results = MonitorSnapshot.query_recent(
            snapshot_type="dynamic",
            limit=100
        )
        elapsed = time.monotonic() - start

        assert len(results) == 100
        assert elapsed < 0.5, f"Time-range query took {elapsed:.3f}s (expected <500ms)"


class TestPressureAnalyzerPerformance:
    """Benchmark pressure score calculation."""

    def test_score_calculation_latency(self):
        """1000 pressure score calculations should complete in <100ms."""
        from monitor.pressure import PressureAnalyzer

        class FakeConfig:
            weights = {'cpu': 2, 'memory': 7, 'io': 1}
            dominant_app_reduce_factor = 3.5

        analyzer = PressureAnalyzer(FakeConfig())
        psi_data = {'cpu': 0.5, 'memory': 0.3, 'io': 0.2}
        usage_data = {'cpu': {'is_busy': False}, 'memory': {'is_busy': False}}

        start = time.monotonic()
        for _ in range(1000):
            analyzer.calculate_pressure_score(psi_data, usage_data, False)
        elapsed = time.monotonic() - start

        assert elapsed < 0.1, f"1000 score calculations took {elapsed:.3f}s (expected <100ms)"

    def test_level_determination_latency(self):
        """10000 level determinations should complete in <100ms."""
        from monitor.pressure import PressureAnalyzer

        class FakeConfig:
            weights = {'cpu': 2, 'memory': 7, 'io': 1}
            dominant_app_reduce_factor = 3.5

        analyzer = PressureAnalyzer(FakeConfig())
        thresholds = {'low': 0.4, 'medium': 0.6, 'high': 0.8, 'critical': 1.0}

        start = time.monotonic()
        for i in range(10000):
            score = (i % 100) / 100.0
            analyzer.get_pressure_level(score, thresholds)
        elapsed = time.monotonic() - start

        assert elapsed < 0.1, f"10000 level determinations took {elapsed:.3f}s (expected <100ms)"


class TestConfigPerformance:
    """Benchmark config operations."""

    def test_config_load_latency(self, tmp_path):
        """Config loading should be under 50ms."""
        from config.config import Config

        config_content = """
cgroup_mount: "/sys/fs/cgroup"
vendor: "generic"
thresholds:
  low: 0.4
  medium: 0.6
  high: 0.8
  critical: 1.0
weights:
  cpu: 2
  memory: 7
  io: 1
dominant_app_reduce_factor: 3.5
cpu_busy_threshold: 90
memory_busy_threshold: 90
app_priority:
  critical: 100
  high: 80
  medium: 50
  low: 20
limit_policy:
  cpu:
    enabled: true
    rate:
      high: 0.7
      medium: 0.5
      low: 0.4
      undefined: 0.3
  memory:
    enabled: true
    rate:
      high: 0.3
      medium: 0.2
      low: 0.1
      undefined: 0.1
  disk_io:
    enabled: true
    rate:
      high:
        write: 50
        read: 60
      medium:
        write: 40
        read: 50
      low:
        write: 20
        read: 30
      undefined:
        write: 10
        read: 20
passive_resource_control:
  enabled: true
weights_top:
  cpu: 2
  memory: 7
  gpu: 5
controlled_apps: []
blacklist: []
cooldown_time: 15
regular_update_sys_pressure_time: 5
monitor_idle_check_interval: 10
disk_utilization_threshold: 95
disk_iowait_threshold: 10
disk_io_throughput_threshold_kb: 102400
enable_network_control: false
network_thresholds:
  low: 0.3
  medium: 0.5
  high: 0.7
  critical: 0.9
network_interface: "lo"
network_bandwidth_kbit: 100000
config_network_bw:
  system:
    min: 5000
    max: 10000
  critical:
    min: 60000
    max: 90000
  high:
    min: 30000
    max: 80000
  low:
    min: 10000
    max: 80000
network_burst_map:
  critical: "64k"
  high: "32k"
  low: "16k"
  system: "8k"
network_system_ports: []
testing_network_app: []
"""
        config_file = tmp_path / "perf_config.yaml"
        config_file.write_text(config_content)

        latencies = []
        for _ in range(50):
            start = time.monotonic()
            Config.from_file(str(config_file))
            latencies.append(time.monotonic() - start)

        avg = statistics.mean(latencies)
        p95 = sorted(latencies)[int(0.95 * len(latencies))]
        assert avg < 0.05, f"Avg config load: {avg*1000:.1f}ms (expected <50ms)"
        assert p95 < 0.1, f"P95 config load: {p95*1000:.1f}ms (expected <100ms)"

    def test_config_update_throughput(self, tmp_path):
        """50 consecutive config updates should complete in <2 seconds."""
        from config.config import Config

        config_content = """
cgroup_mount: "/sys/fs/cgroup"
weights_top:
  cpu: 2
  memory: 7
  gpu: 5
passive_resource_control:
  enabled: true
"""
        config_file = tmp_path / "update_perf.yaml"
        config_file.write_text(config_content)

        cfg = Config.from_file(str(config_file))

        start = time.monotonic()
        for i in range(50):
            cfg.update_config_section('weights_top', {'cpu': i % 10})
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, f"50 config updates took {elapsed:.2f}s (expected <2s)"


class TestAPIResponsePerformance:
    """Benchmark API response construction."""

    def test_construct_response_latency(self):
        """10000 response constructions should complete in <2s."""
        from flask import Flask
        from utils.http_utils import construct_response, RetCode

        app = Flask(__name__)
        app.config['TESTING'] = True

        with app.app_context():
            start = time.monotonic()
            for i in range(10000):
                construct_response(
                    data={"app_id": f"app_{i}", "status": "running", "priority": i % 100},
                    retmsg="success"
                )
            elapsed = time.monotonic() - start

        assert elapsed < 2.0, f"10000 response constructions took {elapsed:.2f}s (expected <2s)"


class TestPriorityQueuePerformance:
    """Benchmark priority queue operations."""

    def test_queue_put_get_throughput(self):
        """1000 put+get operations should complete in <500ms."""
        from balancer.balancer import MaxPriorityQueue

        pq = MaxPriorityQueue()

        start = time.monotonic()
        for i in range(1000):
            # put() takes a single item tuple: (data, priority)
            pq.put(({"task_id": f"task_{i}"}, i % 100))
        for _ in range(1000):
            if not pq.empty():
                pq.get()
        elapsed = time.monotonic() - start

        assert elapsed < 0.5, f"1000 put+get took {elapsed:.3f}s (expected <500ms)"

    def test_queue_remove_if_performance(self):
        """remove_if on a queue of 500 items should be under 100ms."""
        from balancer.balancer import MaxPriorityQueue

        pq = MaxPriorityQueue()
        for i in range(500):
            pq.put(({"task_id": f"task_{i}", "remove": i % 5 == 0}, i))

        start = time.monotonic()
        # remove_if receives (data, priority) tuple
        pq.remove_if(lambda item: item[0].get("remove", False))
        elapsed = time.monotonic() - start

        assert elapsed < 0.1, f"remove_if on 500 items took {elapsed:.3f}s (expected <100ms)"
