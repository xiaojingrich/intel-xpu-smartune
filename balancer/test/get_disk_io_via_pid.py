# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

# [SECURITY REVIEW]: All subprocess calls in this module use list-based arguments
# with shell=False (default). No untrusted shell execution or string
# concatenation is performed. All inputs are internally validated.
import subprocess # nosec
import re
from typing import List, Dict, Optional


def _check_pids_io_usage(running_pids: List[int], threshold_mb: float = 10.0) -> Dict[str, any]:
    """
    Batch-sample disk IO usage for multiple PIDs (same app) and check against threshold.

    :param running_pids: Target PID list (multiple processes of the same app)
    :param threshold_mb: Disk IO alert threshold in MB/s (default 10)
    :return: Structured dict with sample results, total IO rate, and busy flag
    """
    if not isinstance(running_pids, List) or len(running_pids) == 0:
        raise ValueError("running_pids must be a non-empty list of integers")
    for pid in running_pids:
        if not isinstance(pid, int) or pid <= 0:
            raise ValueError(f"PID {pid} is invalid, must be a positive integer")

    if not isinstance(threshold_mb, (int, float)) or threshold_mb < 0:
        raise ValueError("threshold_mb must be a non-negative number (MB/s)")

    sample_times: int = 3
    sample_interval: float = 0.2
    kb_to_mb: float = 1024.0

    iotop_cmd = [
        "sudo",
        "iotop",
        "-b",  # batch mode
        "-o",  # only show processes with IO activity
        "-k",  # KB units
        "-n", str(sample_times),
        "-d", str(sample_interval)
    ]

    for pid in running_pids:
        iotop_cmd.extend(["-p", str(pid)])

    # Pattern example: 79075  ...  0.00 K/s  1250.00 K/s  ...  gnome-calculator
    io_pattern = re.compile(
        r"(?P<pid>\d+)\s+.+?\s+(?P<read_kb>\d+\.\d+)\s+K/s\s+(?P<write_kb>\d+\.\d+)\s+K/s"
    )

    try:
        result = subprocess.run(
            iotop_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip()
            if "no such file or directory" in error_msg.lower():
                raise Exception("iotop not installed, run: sudo apt install iotop")
            elif "permission denied" in error_msg.lower():
                raise Exception("Missing sudo permission")
            else:
                raise Exception(f"iotop command failed: {error_msg}")

        pid_io_data: Dict[int, Dict[str, List[float]]] = {
            pid: {"read_kb_list": [], "write_kb_list": []} for pid in running_pids
        }
        output_lines = result.stdout.strip().split("\n")

        for line in output_lines:
            line = line.strip()
            if not line or "PID" in line or "DISK READ" in line:
                continue

            match = io_pattern.search(line)
            if match:
                pid = int(match.group("pid"))
                read_kb = float(match.group("read_kb"))
                write_kb = float(match.group("write_kb"))

                if pid in pid_io_data:
                    pid_io_data[pid]["read_kb_list"].append(read_kb)
                    pid_io_data[pid]["write_kb_list"].append(write_kb)

        pid_avg_io: Dict[int, Dict[str, float]] = {}
        total_read_mb_per_sec: float = 0.0
        total_write_mb_per_sec: float = 0.0

        for pid, io_data in pid_io_data.items():
            read_list = io_data["read_kb_list"]
            write_list = io_data["write_kb_list"]

            avg_read_kb = sum(read_list) / len(read_list) if read_list else 0.0
            avg_write_kb = sum(write_list) / len(write_list) if write_list else 0.0

            avg_read_mb = round(avg_read_kb / kb_to_mb, 4)
            avg_write_mb = round(avg_write_kb / kb_to_mb, 4)

            pid_avg_io[pid] = {
                "avg_read_mb_per_sec": avg_read_mb,
                "avg_write_mb_per_sec": avg_write_mb,
                "total_io_mb_per_sec": round(avg_read_mb + avg_write_mb, 4)
            }

            total_read_mb_per_sec += avg_read_mb
            total_write_mb_per_sec += avg_write_mb

        app_total_io_mb_per_sec = round(total_read_mb_per_sec + total_write_mb_per_sec, 4)
        is_disk_busy = app_total_io_mb_per_sec > threshold_mb

        return {
            "app_io_summary": {
                "total_read_mb_per_sec": round(total_read_mb_per_sec, 4),
                "total_write_mb_per_sec": round(total_write_mb_per_sec, 4),
                "total_io_mb_per_sec": app_total_io_mb_per_sec,
                "threshold_mb_per_sec": threshold_mb,
                "is_disk_busy": is_disk_busy,
                "sample_config": {
                    "sample_times": sample_times,
                    "sample_interval_sec": sample_interval,
                    "total_sample_duration_sec": round(sample_times * sample_interval, 2)
                }
            },
            "individual_pid_io": pid_avg_io,
            "input_params": {
                "running_pids": running_pids,
                "threshold_mb": threshold_mb
            }
        }

    except Exception as e:
        print(f"Error: failed to get multi-PID IO usage - {str(e)}")
        return {
            "app_io_summary": {"is_disk_busy": False, "error": str(e)},
            "individual_pid_io": {},
            "input_params": {"running_pids": running_pids, "threshold_mb": threshold_mb}
        }


if __name__ == "__main__":
    target_pids = [79074, 79075, 79076, 79077, 79078, 79079, 79080]

    io_check_result = _check_pids_io_usage(
        running_pids=target_pids,
        threshold_mb=10.0
    )

    print("=" * 80)
    print("App Disk IO Usage Check Result")
    print("=" * 80)

    summary = io_check_result["app_io_summary"]
    if "error" not in summary:
        print(f"\n[Summary]")
        print(f"  Total disk read rate: {summary['total_read_mb_per_sec']} MB/s")
        print(f"  Total disk write rate: {summary['total_write_mb_per_sec']} MB/s")
        print(f"  Total disk IO rate: {summary['total_io_mb_per_sec']} MB/s")
        print(f"  Alert threshold: {summary['threshold_mb_per_sec']} MB/s")
        print(f"  Disk status: {'BUSY (over threshold)' if summary['is_disk_busy'] else 'IDLE (below threshold)'}")
        print(
            f"  Sample config: {summary['sample_config']['sample_times']} samples, "
            f"interval {summary['sample_config']['sample_interval_sec']}s, "
            f"total {summary['sample_config']['total_sample_duration_sec']}s")

        print(f"\n[Per-PID IO Data]")
        for pid, pid_io in io_check_result["individual_pid_io"].items():
            print(f"  PID {pid}:")
            print(f"    Avg read rate: {pid_io['avg_read_mb_per_sec']} MB/s")
            print(f"    Avg write rate: {pid_io['avg_write_mb_per_sec']} MB/s")
            print(f"    Avg total IO rate: {pid_io['total_io_mb_per_sec']} MB/s")
    else:
        print(f"\n[Error]: {summary['error']}")

    print("\n" + "=" * 80)
