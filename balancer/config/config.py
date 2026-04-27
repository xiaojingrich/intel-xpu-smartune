# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import yaml
from dataclasses import dataclass

@dataclass
class Config:
    cgroup_mount: str = "/sys/fs/cgroup"
    vendor: str = "generic"
    thresholds: dict = None
    weights: dict = None
    weights_top: dict = None
    dominant_app_reduce_factor: float = 3.0
    workloads: dict = None
    app_priority: dict = None
    limit_policy: dict = None
    blacklist: list = None
    cooldown_time: float = 15
    cpu_busy_threshold: float = 90
    memory_busy_threshold: float = 90
    disk_utilization_threshold: float = 95
    disk_iowait_threshold: float = 10
    disk_io_throughput_threshold_kb: float = 102400  # KB/s, i.e. 100 MB/s
    regular_update_sys_pressure_time: float = 5
    network_thresholds: dict = None
    network_interface: dict = None
    network_bandwidth_kbit: int = 1000000 #kbit/s
    enable_network_control: bool = True
    config_network_bw: dict = None
    testing_network_app: list = None
    network_burst_map: dict = None
    network_system_ports: list = None
    monitor_apps: dict = None
    all_apps: dict = None

    @classmethod
    def from_file(cls, path: str):
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**data)


b_config = Config.from_file("config/config.yaml")

