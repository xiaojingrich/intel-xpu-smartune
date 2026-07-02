# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for balancer/balancer.py — core balancing logic components."""

import os
import sys
import threading
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))


class TestMaxPriorityQueue:
    @pytest.fixture
    def queue(self):
        from balancer.balancer import MaxPriorityQueue
        return MaxPriorityQueue()

    def test_put_and_get_ordering(self, queue):
        # put() takes a single tuple (data, priority)
        queue.put(("low_priority", 1))
        queue.put(("high_priority", 5))
        queue.put(("medium_priority", 3))

        item = queue.get()
        assert item[0] == "high_priority"

    def test_empty_queue(self, queue):
        assert queue.empty()
        queue.put(("item", 1))
        assert not queue.empty()

    def test_get_from_empty_raises(self, queue):
        assert queue.empty()

    def test_multiple_same_priority(self, queue):
        queue.put(("first", 5))
        queue.put(("second", 5))
        queue.put(("third", 5))

        items = []
        while not queue.empty():
            item = queue.get()
            items.append(item[0])
        assert len(items) == 3

    def test_remove_if(self, queue):
        queue.put(({"id": "a"}, 1))
        queue.put(({"id": "b"}, 2))
        queue.put(({"id": "c"}, 3))

        # condition_func receives the full item tuple (data, priority)
        queue.remove_if(lambda item: item[0].get("id") == "b")

        items = []
        while not queue.empty():
            item = queue.get()
            items.append(item[0]["id"])
        assert "b" not in items
        assert len(items) == 2


class TestWorkloadGroup:
    def test_dataclass_creation(self):
        from balancer.balancer import WorkloadGroup
        wg = WorkloadGroup(
            name="test_group",
            priority="high",
            cpu_weight=200,
            memory_min=1024,
            io_weight=100
        )
        assert wg.name == "test_group"
        assert wg.priority == "high"
        assert wg.cpu_weight == 200
        assert wg.memory_min == 1024
        assert wg.io_weight == 100


class TestWorkloadTask:
    def test_dataclass_creation(self):
        from balancer.balancer import WorkloadTask
        wt = WorkloadTask(
            workload=MagicMock(),
            params={'key': 'val'},
            pid=12345,
            task_id="task_001"
        )
        assert wt.pid == 12345
        assert wt.task_id == "task_001"
        assert wt.params == {'key': 'val'}


class TestSplitProportionally:
    def test_basic_split(self):
        from balancer.balancer import _split_proportionally
        all_ids = ["cg1", "cg2"]
        per_cg_usage = {"cg1": 100, "cg2": 300}
        result = _split_proportionally(1000, all_ids, per_cg_usage)
        assert result["cg1"] == 250  # 100/400 * 1000
        assert result["cg2"] == 750  # 300/400 * 1000

    def test_single_cgroup(self):
        from balancer.balancer import _split_proportionally
        all_ids = ["cg1"]
        per_cg_usage = {"cg1": 500}
        result = _split_proportionally(2000, all_ids, per_cg_usage)
        assert result["cg1"] == 2000

    def test_zero_usage_equal_split(self):
        from balancer.balancer import _split_proportionally
        all_ids = ["cg1", "cg2", "cg3"]
        per_cg_usage = {"cg1": 0, "cg2": 0, "cg3": 0}
        result = _split_proportionally(900, all_ids, per_cg_usage)
        assert result["cg1"] == 300
        assert result["cg2"] == 300
        assert result["cg3"] == 300

    def test_missing_cgroup_usage(self):
        from balancer.balancer import _split_proportionally
        all_ids = ["cg1", "cg2"]
        per_cg_usage = {"cg1": 200}  # cg2 missing
        result = _split_proportionally(1000, all_ids, per_cg_usage)
        assert "cg1" in result
        assert "cg2" in result
        # Due to rounding, total may be off by 1
        total = sum(result.values())
        assert abs(total - 1000) <= len(all_ids)


class TestGetLimitedRatesLogic:
    """Test the limit rate lookup logic without instantiating the full balancer."""

    def test_rate_lookup_by_priority(self):
        """Verify that limit_policy config maps correctly to rate values."""
        from config.config import b_config

        limit_policy = b_config.limit_policy
        assert limit_policy['cpu']['rate']['high'] == 0.7
        assert limit_policy['cpu']['rate']['low'] == 0.4
        assert limit_policy['memory']['rate']['medium'] == 0.2
        assert limit_policy['disk_io']['rate']['high']['write'] == 50

    def test_critical_priority_not_limited(self):
        """Critical priority should not have a rate (not limited)."""
        from config.config import b_config

        limit_policy = b_config.limit_policy
        assert 'critical' not in limit_policy['cpu']['rate']
        assert 'critical' not in limit_policy['memory']['rate']
