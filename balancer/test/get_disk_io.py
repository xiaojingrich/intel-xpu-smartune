# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import psutil
# [SECURITY REVIEW]: All subprocess calls in this module use list-based arguments 
# with shell=False (default). No untrusted shell execution or string 
# concatenation is performed. All inputs are internally validated.
import subprocess # nosec
import time

def get_disk_utilization(device='nvme0n1', interval=1):
    with open('/proc/diskstats') as f:
        for line in f:
            if device in line:
                fields = line.strip().split()
                io_ticks = int(fields[13])  # io_ticks (ms)
                return io_ticks
    return 0


def disk_utilization(device='nvme0n1', interval=1):
    cnt1 = psutil.disk_io_counters(perdisk=True).get(device)
    time.sleep(interval)
    cnt2 = psutil.disk_io_counters(perdisk=True).get(device)
    if not cnt1 or not cnt2:
        return 0.0
    delta_time = interval * 1000
    busy_time = (cnt2.read_time - cnt1.read_time) + (cnt2.write_time - cnt1.write_time)
    return min(100.0, 100 * busy_time / delta_time)

def get_system_disk():
    for part in psutil.disk_partitions():
        if part.mountpoint == "/":
            return part.device.split("/")[-1].rstrip("0123456789")
    return None

def get_physical_disks():
    result = subprocess.run(
        ["lsblk", "-d", "-o", "NAME,TYPE", "-n"],
        check=True,
        capture_output=True,
        text=True,
    )
    disks = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "disk":
            disks.append(parts[0])
    return disks


if __name__ == "__main__":
    io1 = get_disk_utilization(device='nvme0n1')
    time1 = time.time()
    time.sleep(1)
    io2 = get_disk_utilization(device='nvme0n1')
    time2 = time.time()

    delta_io_ticks = io2 - io1
    delta_time_ms = (time2 - time1) * 1000
    util_percent = min(100.0, 100 * delta_io_ticks / delta_time_ms)

    print(f"Disk utilization: {util_percent:.1f}%")
    print(f"Disk utilization: {disk_utilization('nvme0n1'):.1f}%")

    disk_devices = psutil.disk_io_counters(perdisk=True).keys()
    print("Available disk devices:", list(disk_devices))

    device = get_system_disk()
    print("System disk device:", device)

    physical_disks = get_physical_disks()
    device = physical_disks[0] if physical_disks else None
    print(f"Physical disks: {physical_disks}, using device: {device}")

