# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for config/config.py — Config loading, updating, and YAML persistence."""

import os
import sys
import tempfile
import threading

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))

from config.config import Config


@pytest.fixture
def config_file(tmp_path):
    content = """
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
        write_iops: 2200
        read_iops: 20000
      medium:
        write: 40
        read: 50
        write_iops: 1600
        read_iops: 15000
      low:
        write: 20
        read: 30
        write_iops: 1200
        read_iops: 11000
      undefined:
        write: 10
        read: 20
        write_iops: 1000
        read_iops: 8000
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
    path = tmp_path / "config.yaml"
    path.write_text(content)
    return str(path)


class TestConfigLoading:
    def test_load_from_file(self, config_file):
        cfg = Config.from_file(config_file)
        assert cfg.cgroup_mount == "/sys/fs/cgroup"
        assert cfg.vendor == "generic"
        assert cfg.cpu_busy_threshold == 90
        assert cfg.memory_busy_threshold == 90

    def test_thresholds_loaded(self, config_file):
        cfg = Config.from_file(config_file)
        assert cfg.thresholds['critical'] == 1.0
        assert cfg.thresholds['high'] == 0.8
        assert cfg.thresholds['medium'] == 0.6
        assert cfg.thresholds['low'] == 0.4

    def test_weights_loaded(self, config_file):
        cfg = Config.from_file(config_file)
        assert cfg.weights['cpu'] == 2
        assert cfg.weights['memory'] == 7
        assert cfg.weights['io'] == 1

    def test_limit_policy_loaded(self, config_file):
        cfg = Config.from_file(config_file)
        assert cfg.limit_policy['cpu']['enabled'] is True
        assert cfg.limit_policy['cpu']['rate']['high'] == 0.7
        assert cfg.limit_policy['memory']['rate']['low'] == 0.1
        assert cfg.limit_policy['disk_io']['rate']['medium']['write'] == 40

    def test_app_priority_loaded(self, config_file):
        cfg = Config.from_file(config_file)
        assert cfg.app_priority['critical'] == 100
        assert cfg.app_priority['high'] == 80
        assert cfg.app_priority['medium'] == 50
        assert cfg.app_priority['low'] == 20

    def test_default_values_when_missing(self):
        cfg = Config()
        assert cfg.cgroup_mount == "/sys/fs/cgroup"
        assert cfg.cooldown_time == 15
        assert cfg.dominant_app_reduce_factor == 3.0


class TestConfigUpdate:
    def test_update_config_section_weights_top(self, config_file):
        cfg = Config.from_file(config_file)
        result = cfg.update_config_section('weights_top', {'cpu': 5, 'memory': 3})
        assert result is True
        assert cfg.weights_top['cpu'] == 5
        assert cfg.weights_top['memory'] == 3

    def test_update_config_section_persists_to_yaml(self, config_file):
        cfg = Config.from_file(config_file)
        cfg.update_config_section('weights_top', {'cpu': 10})

        with open(config_file) as f:
            data = yaml.safe_load(f)
        assert data['weights_top']['cpu'] == 10

    def test_update_config_section_no_change(self, config_file):
        cfg = Config.from_file(config_file)
        result = cfg.update_config_section('weights_top', {'cpu': 2})
        assert result is False

    def test_update_config_section_invalid_input(self, config_file):
        cfg = Config.from_file(config_file)
        assert cfg.update_config_section('', {'cpu': 5}) is False
        assert cfg.update_config_section('weights_top', None) is False

    def test_update_config_section_creates_new_section(self, config_file):
        cfg = Config.from_file(config_file)
        cfg.some_new_section = None
        result = cfg.update_config_section('some_new_section', {'key1': 'value1'})
        assert result is True
        assert cfg.some_new_section == {'key1': 'value1'}

    def test_update_passive_resource_control(self, config_file):
        cfg = Config.from_file(config_file)
        result = cfg.update_config_section('passive_resource_control', {'enabled': False})
        assert result is True
        assert cfg.passive_resource_control['enabled'] is False


class TestConfigConcurrency:
    def test_concurrent_updates(self, config_file):
        """Test that concurrent config updates don't corrupt state."""
        cfg = Config.from_file(config_file)
        errors = []

        def update_weights(thread_id):
            try:
                for i in range(20):
                    cfg.update_config_section('weights_top', {'cpu': thread_id * 100 + i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=update_weights, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert isinstance(cfg.weights_top['cpu'], int)

    def test_concurrent_reads_during_write(self, config_file):
        """Reads should not raise while writes are in progress."""
        cfg = Config.from_file(config_file)
        errors = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    _ = cfg.weights_top.get('cpu')
                except Exception as e:
                    errors.append(e)

        def writer():
            for i in range(50):
                cfg.update_config_section('weights_top', {'cpu': i})

        reader_threads = [threading.Thread(target=reader) for _ in range(3)]
        for t in reader_threads:
            t.start()

        writer_thread = threading.Thread(target=writer)
        writer_thread.start()
        writer_thread.join()
        stop.set()

        for t in reader_threads:
            t.join()
        assert not errors
