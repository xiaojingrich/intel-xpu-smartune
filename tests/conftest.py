# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Shared pytest configuration and fixtures.

This conftest patches module-level initializations that depend on runtime
files (config.yaml, log directories, /proc, etc.) so tests can run from
any directory without requiring root or a live system.
"""

import os
import sys
import types
import logging
import tempfile

# ──────────────────────────────────────────────────────────────────────────────
# Path setup: ensure balancer/ is importable
# ──────────────────────────────────────────────────────────────────────────────
BALANCER_DIR = os.path.join(os.path.dirname(__file__), '..', 'balancer')
sys.path.insert(0, BALANCER_DIR)

# ──────────────────────────────────────────────────────────────────────────────
# Stub utils.logger BEFORE any balancer module imports it.
# The real logger tries to create a file under logs/ which may not be writable.
# ──────────────────────────────────────────────────────────────────────────────
_test_logger = logging.getLogger("smartune_test")
_test_logger.addHandler(logging.NullHandler())

_fake_logger_mod = types.ModuleType("utils.logger")
_fake_logger_mod.logger = _test_logger

# Ensure 'utils' package entry exists so 'utils.logger' resolves
if 'utils' not in sys.modules:
    _fake_utils = types.ModuleType("utils")
    _fake_utils.__path__ = [os.path.join(BALANCER_DIR, 'utils')]
    sys.modules['utils'] = _fake_utils
sys.modules['utils.logger'] = _fake_logger_mod

# ──────────────────────────────────────────────────────────────────────────────
# Stub config.config module-level b_config with a Config loaded from the real
# config.yaml (if reachable from balancer/) or a minimal temp file.
# ──────────────────────────────────────────────────────────────────────────────
_real_config_path = os.path.join(BALANCER_DIR, 'config', 'config.yaml')
if not os.path.exists(_real_config_path):
    # Create a minimal config for environments where the real one isn't available
    _minimal_config = """
cgroup_mount: "/sys/fs/cgroup"
vendor: "generic"
thresholds: {low: 0.4, medium: 0.6, high: 0.8, critical: 1.0}
weights: {cpu: 2, memory: 7, io: 1}
dominant_app_reduce_factor: 3.5
cpu_busy_threshold: 90
memory_busy_threshold: 90
disk_utilization_threshold: 95
disk_iowait_threshold: 10
disk_io_throughput_threshold_kb: 102400
weights_top: {cpu: 2, memory: 7, gpu: 5}
passive_resource_control: {enabled: true}
blacklist: [systemd, kworker]
cooldown_time: 15
regular_update_sys_pressure_time: 5
monitor_idle_check_interval: 10
app_priority: {critical: 100, high: 80, medium: 50, low: 20}
limit_policy:
  policy: combined
  cpu: {enabled: true, rate: {high: 0.7, medium: 0.5, low: 0.4, undefined: 0.3}}
  memory: {enabled: true, rate: {high: 0.3, medium: 0.2, low: 0.1, undefined: 0.1}}
  disk_io:
    enabled: true
    rate:
      high: {write: 50, read: 60, write_iops: 2200, read_iops: 20000}
      medium: {write: 40, read: 50, write_iops: 1600, read_iops: 15000}
      low: {write: 20, read: 30, write_iops: 1200, read_iops: 11000}
      undefined: {write: 10, read: 20, write_iops: 1000, read_iops: 8000}
controlled_apps: []
enable_network_control: false
network_thresholds: {low: 0.3, medium: 0.5, high: 0.7, critical: 0.9}
network_interface: "lo"
network_bandwidth_kbit: 100000
config_network_bw:
  system: {min: 5000, max: 10000}
  critical: {min: 60000, max: 90000}
  high: {min: 30000, max: 80000}
  low: {min: 10000, max: 80000}
network_burst_map: {critical: "64k", high: "32k", low: "16k", system: "8k"}
network_system_ports: [22, 53]
testing_network_app: []
"""
    _tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
    _tmp.write(_minimal_config)
    _tmp.flush()
    _real_config_path = _tmp.name

# Patch config.config so it loads from our resolved path
import importlib.util as _ilu

_config_src = os.path.join(BALANCER_DIR, 'config', 'config.py')
with open(_config_src) as _f:
    _config_code = _f.read()
# Replace the hardcoded relative path with our resolved absolute path
_config_code = _config_code.replace(
    'b_config = Config.from_file("config/config.yaml")',
    f'b_config = Config.from_file("{_real_config_path}")'
)
_config_spec = _ilu.spec_from_file_location("config.config", _config_src)
_config_mod = _ilu.module_from_spec(_config_spec)
exec(compile(_config_code, _config_src, 'exec'), _config_mod.__dict__)

# Register in sys.modules so subsequent imports resolve correctly
if 'config' not in sys.modules:
    _fake_config_pkg = types.ModuleType("config")
    _fake_config_pkg.__path__ = [os.path.join(BALANCER_DIR, 'config')]
    sys.modules['config'] = _fake_config_pkg
sys.modules['config.config'] = _config_mod

# ──────────────────────────────────────────────────────────────────────────────
# Stub gi.repository for environments without GTK (CI, containers)
# ──────────────────────────────────────────────────────────────────────────────
try:
    from gi.repository import Gio  # noqa: F401
except (ImportError, ValueError):
    _fake_gi = types.ModuleType("gi")
    _fake_gi.__path__ = []
    _fake_gi_repo = types.ModuleType("gi.repository")

    class _FakeGio:
        class AppInfo:
            @staticmethod
            def get_all():
                return []

    _fake_gi_repo.Gio = _FakeGio
    sys.modules.setdefault('gi', _fake_gi)
    sys.modules.setdefault('gi.repository', _fake_gi_repo)
