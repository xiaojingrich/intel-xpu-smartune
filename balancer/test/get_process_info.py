# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import psutil
import time
from typing import Dict, Any


def get_process_info(pid: int) -> Dict[str, Any]:
    """Get detailed info for a single process."""
    try:
        p = psutil.Process(pid)
        with p.oneshot():
            return {
                "pid": pid,
                "name": p.name(),
                "exe": p.exe(),
                "status": p.status(),
                "cpu_percent": p.cpu_percent(interval=0.1),
                "cpu_times": p.cpu_times(),
                "memory_info": p.memory_info(),
                "memory_percent": p.memory_percent(),
                "io_counters": p.io_counters(),
                "num_threads": p.num_threads(),
                "create_time": p.create_time()
            }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return {}


def monitor_processes(top_n: int = 5) -> None:
    """Monitor processes and print top N by resource usage."""
    while True:
        processes = []
        for proc in psutil.process_iter(['pid', 'name']):
            info = get_process_info(proc.pid)
            if info:
                processes.append(info)

        processes.sort(key=lambda x: x.get('cpu_percent', 0), reverse=True)

        print("\033c", end="")
        print(f"{'PID':<8}{'Name':<20}{'CPU%':<8}{'MEM%':<8}{'RSS':<12}{'IO Read':<12}{'IO Write':<12}")
        print("-" * 80)

        for p in processes[:top_n]:
            io = p.get('io_counters', psutil._common.sio(0, 0, 0, 0))
            print(
                f"{p['pid']:<8}"
                f"{p['name'][:20]:<20}"
                f"{p['cpu_percent']:<8.1f}"
                f"{p['memory_percent']:<8.1f}"
                f"{p['memory_info'].rss // 1024 // 1024:<6}MB "
                f"{io.read_bytes // 1024:<8}KB "
                f"{io.write_bytes // 1024:<8}KB"
            )

        time.sleep(2)


if __name__ == "__main__":
    test_pid = psutil.Process().pid
    print(f"\nProcess info for PID={test_pid}:")
    print(get_process_info(test_pid))

    print("\nMonitoring processes (sorted by CPU, TOP 5):")
    try:
        monitor_processes(top_n=5)
    except KeyboardInterrupt:
        print("\nMonitoring stopped")