#
#  Copyright (C) 2025 Intel Corporation
#
#  This software and the related documents are Intel copyrighted materials,
#  and your use of them is governed by the express license under which they
#  were provided to you ("License"). Unless the License provides otherwise,
#  you may not use, modify, copy, publish, distribute, disclose or transmit
#  his software or the related documents without Intel's prior written permission.
#
#  This software and the related documents are provided as is, with no express
#  or implied warranties, other than those that are expressly stated in the License.
#

"""NPU subsystem: discovery, device info, frequency bounds, and SMI telemetry."""

import glob
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from monitor.metrics.utils import safe_read, run_cmd
from monitor.intel_npu_smi import PmtTelemetry, get_npu_processes
from utils.logger import logger


def get_npu_names() -> List[str]:
    output = run_cmd(["lspci", "-nn"])
    if not output:
        return []
    matches = []
    for line in output.splitlines():
        if re.search(r"npu|vpu|accelerator", line, re.IGNORECASE):
            matches.append(line.strip())
    return matches


def _get_intel_vpu_pci_devices() -> List[str]:
    devices: List[str] = []
    for entry in glob.glob("/sys/bus/pci/drivers/intel_vpu/*"):
        name = os.path.basename(entry)
        if re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]", name):
            devices.append(name)
    return sorted(set(devices))


def get_npu_fw_version() -> str:
    for pci_dev in _get_intel_vpu_pci_devices():
        fw_path = f"/sys/kernel/debug/accel/{pci_dev}/fw_version"
        fw = safe_read(fw_path)
        if fw:
            return fw
    return "NA"


def get_npu_device_info() -> Dict[str, Optional[str]]:
    driver_path = "/sys/bus/pci/drivers/intel_vpu/"
    pciid: Optional[str] = None
    driver_version: Optional[str] = None
    for pci_dev in _get_intel_vpu_pci_devices():
        dev_path = os.path.join("/sys/bus/pci/devices", pci_dev)
        raw_id = safe_read(os.path.join(dev_path, "device"))
        if raw_id:
            pciid = raw_id.strip()
        break
    module_version = safe_read(os.path.join(driver_path, "module", "version"))
    if module_version:
        driver_version = module_version.split(" ")[0]
    return {
        "pciid": pciid,
        "driver_version": driver_version,
    }


def get_npu_freq_bounds() -> Dict[str, Dict[str, Optional[float]]]:
    result: Dict[str, Dict[str, Optional[float]]] = {}
    for pci_dev in _get_intel_vpu_pci_devices():
        max_path = os.path.join("/sys/bus/pci/devices", pci_dev, "npu_max_frequency_mhz")
        try:
            with open(max_path, "r", encoding="utf-8") as f:
                max_val = f.read().strip()
            result[pci_dev] = {
                "max_mhz": float(max_val) if max_val.isdigit() else None,
            }
        except FileNotFoundError:
            result[pci_dev] = {"max_mhz": None}
        except Exception as exc:
            logger.debug("Read failed for %s: %s", max_path, exc)
            result[pci_dev] = {"max_mhz": None}
    return result


def _collect_npu_smi_once() -> Dict[str, Any]:
    driver_path = "/sys/bus/pci/drivers/intel_vpu/"
    debugfs_root = "/sys/kernel/debug/accel/"
    if not os.path.exists(driver_path):
        return {"available": False, "raw": None, "error": "Intel NPU driver 'intel_vpu' is not loaded"}

    dev_path: Optional[str] = None
    debugfs_path: Optional[str] = None
    dev_file: Optional[str] = None

    for entry in os.listdir(driver_path):
        if not entry.startswith("0000:"):
            continue
        dev_path = os.path.join(driver_path, entry)
        debugfs_path = os.path.join(debugfs_root, entry)
        accel_path = os.path.join(dev_path, "accel")
        if os.path.exists(accel_path):
            accel_entries = os.listdir(accel_path)
            if accel_entries:
                dev_file = os.path.join("/dev/accel", accel_entries[0])
        break

    if dev_path is None:
        return {"available": False, "raw": None, "error": "No Intel NPU PCI device found"}

    try:
        telemetry = PmtTelemetry()
    except (SystemExit, RuntimeError) as exc:
        return {"available": False, "raw": None, "error": f"PmtTelemetry init failed: {exc}"}
    except Exception as exc:
        return {"available": False, "raw": None, "error": f"PmtTelemetry init failed: {exc}"}

    npu_busy_path = os.path.join(dev_path, "npu_busy_time_us")

    def read_busy_time() -> Optional[int]:
        if not os.path.exists(npu_busy_path):
            return None
        raw_text = safe_read(npu_busy_path)
        if not raw_text:
            return None
        try:
            return int(raw_text)
        except ValueError:
            return None

    interval_ms = 200.0
    telemetry.update_buffer()
    busy_start = read_busy_time()
    t_start = time.monotonic()
    energy_start = telemetry.get_npu_energy()
    bandwidth_start = telemetry.get_noc_bandwidth()

    time.sleep(interval_ms * 1e-3)

    telemetry.update_buffer()
    busy_end = read_busy_time()
    t_end = time.monotonic()
    energy_end = telemetry.get_npu_energy()
    bandwidth_end = telemetry.get_noc_bandwidth()

    utilization_percent: Optional[float] = None
    if busy_start is not None and busy_end is not None:
        busy_delta = busy_end - busy_start
        elapsed_us = (t_end - t_start) * 1e6
        if elapsed_us > 0:
            utilization_percent = max(0.0, min(100.0, 100.0 * busy_delta / elapsed_us))

    power_w: Optional[float] = None
    if energy_start is not None and energy_end is not None:
        power_w = (energy_end - energy_start) / (interval_ms * 1e-3)

    memory_bytes: Optional[int] = None
    memory_path = os.path.join(dev_path, "npu_memory_utilization")
    try:
        with open(memory_path, "r", encoding="utf-8") as f:
            memory_raw = f.read().strip()
        if memory_raw:
            try:
                memory_bytes = int(memory_raw)
            except ValueError:
                memory_bytes = None
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.debug("Read failed for %s: %s", memory_path, exc)

    fw_version = safe_read(os.path.join(debugfs_path, "fw_version")) if debugfs_path else None
    pciid = safe_read(os.path.join(dev_path, "device"))
    module_version = safe_read(os.path.join(driver_path, "module", "version"))
    driver_version = module_version.split(" ")[0] if module_version else None

    processes = []
    if dev_file:
        try:
            processes = get_npu_processes(dev_file)
        except Exception:
            processes = []

    payload = {
        "timestamp": int(time.time()),
        "pciid": pciid,
        "driver_version": driver_version,
        "fw_version": fw_version,
        "utilization_percent": round(utilization_percent, 3) if utilization_percent is not None else None,
        "power_w": round(power_w, 6) if power_w is not None else None,
        "frequency_mhz": telemetry.get_freq(),
        "tile_config": telemetry.get_tile_config(),
        "temperature_c": telemetry.get_npu_temperature(),
        "noc_bandwidth_mib_per_s": round(bandwidth_end - bandwidth_start, 6),
        "memory_bytes": memory_bytes,
        "processes": processes,
    }

    return {
        "available": True,
        "raw": json.dumps(payload, ensure_ascii=False),
        "error": None,
    }


def get_intel_npu_smi_output() -> Dict[str, Any]:
    try:
        return _collect_npu_smi_once()
    except Exception as exc:
        return {"available": False, "raw": None, "error": f"Failed to collect NPU metrics: {exc}"}
