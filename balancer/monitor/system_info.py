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


import glob
import json
import os
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Set
import threading

import psutil

from config.config import b_config
from db.DatabaseModel import MonitorSnapshot
from monitor.intel_npu_smi import PmtTelemetry, get_npu_processes
from monitor import PSIMonitor
from utils.logger import logger

_STATIC_CACHE: Dict[str, Any] = {"data": None, "ts": 0.0}
_STATIC_CACHE_LOCK = threading.Lock()

_NET_RUNTIME_STATE: Dict[str, Any] = {"ts": None, "bytes": {}}
_QMASSA_STATE: Dict[str, Any] = {
    "process": None,
    "json_path": None,
    "last_start": 0.0,
    "last_parsed": None,
}
_QMASSA_LOCK = threading.Lock()
_CORE_CLASS_CACHE: Dict[str, Any] = {"cpu_count": None, "result": None}
_DYNAMIC_SNAPSHOT_LOCK = threading.Lock()
_DYNAMIC_SNAPSHOT_STATE: Dict[str, Any] = {"last_persist_ts": 0.0}
_DYNAMIC_SNAPSHOT_MIN_INTERVAL_SEC = 5.0


def _safe_read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as exc:
        logger.debug("Read failed for %s: %s", path, exc)
        return None


def _run_cmd(cmd: List[str], timeout: int = 3) -> Optional[str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            stderr = res.stderr.strip() or res.stdout.strip()
            logger.debug("Command failed (%s): %s", " ".join(cmd), stderr)
            return None
        return res.stdout.strip()
    except Exception as exc:
        logger.debug("Command error (%s): %s", " ".join(cmd), exc)
        return None


def _parse_os_version() -> Optional[str]:
    content = _safe_read("/etc/os-release")
    if not content:
        return None
    for line in content.splitlines():
        if line.startswith("VERSION="):
            return line.split("=", 1)[1].strip().strip('"')
    for line in content.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def _parse_bios_version() -> Optional[str]:
    output = _run_cmd(["dmidecode", "-t", "bios"])
    if not output:
        return None
    for line in output.splitlines():
        if "Version:" in line:
            return line.split("Version:", 1)[1].strip()
    return None


def _parse_ddr_speeds() -> List[str]:
    output = _run_cmd(["dmidecode", "-t", "memory"])
    if not output:
        return []
    speeds: List[str] = []
    for line in output.splitlines():
        line = line.strip()
        if line.lower().startswith("speed:"):
            value = line.split(":", 1)[1].strip()
            if value and value not in speeds:
                speeds.append(value)
    return speeds


def _parse_cpu_model() -> Optional[str]:
    content = _safe_read("/proc/cpuinfo")
    if not content:
        return None
    for line in content.splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()
    return None


def _get_dpkg_version(pkg_name: str) -> Dict[str, Any]:
    cmd = ["dpkg", "-l", pkg_name]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
    except Exception:
        return {"installed": False, "version": "NA", "raw": "NA"}

    if res.returncode != 0:
        return {"installed": False, "version": "NA", "raw": "NA"}

    output = res.stdout.strip()
    if not output:
        return {"installed": False, "version": "NA", "raw": "NA"}

    for line in output.splitlines():
        if line.startswith("ii") and pkg_name in line:
            parts = line.split()
            version = parts[2] if len(parts) > 2 else "NA"
            return {"installed": True, "version": version, "raw": line.strip()}

    return {"installed": False, "version": "NA", "raw": "NA"}


def _get_cpu_freq_summary() -> Dict[str, Any]:
    freqs = psutil.cpu_freq(percpu=True)
    per_core = []
    min_vals = []
    max_vals = []
    for freq in freqs or []:
        if freq is None:
            per_core.append(None)
            continue
        per_core.append(round(freq.current, 1))
        if freq.min is not None:
            min_vals.append(freq.min)
        if freq.max is not None:
            max_vals.append(freq.max)
    min_freq = round(min(min_vals), 1) if min_vals else None
    max_freq = round(max(max_vals), 1) if max_vals else None
    return {
        "min_mhz": min_freq,
        "max_mhz": max_freq,
        "per_core_mhz": per_core,
    }


def _read_first_existing(paths: List[str]) -> Optional[str]:
    for path in paths:
        if os.path.exists(path):
            return _safe_read(path)
    return None


def _get_gpu_cards() -> List[str]:
    cards = sorted(glob.glob("/sys/class/drm/card[0-9]"))
    return [c for c in cards if os.path.isdir(c)]


def _get_gpu_names() -> List[str]:
    output = _run_cmd(["lspci", "-nn"])
    if not output:
        return []
    matches = []
    for line in output.splitlines():
        if re.search(r"VGA|3D|Display", line, re.IGNORECASE):
            matches.append(line.strip())
    return matches


def _get_gpu_engines(cards: List[str]) -> Dict[str, List[str]]:
    engines: Dict[str, List[str]] = {}
    for card in cards:
        engine_root = os.path.join(card, "engine")
        if not os.path.isdir(engine_root):
            continue
        names: List[str] = []
        for entry in sorted(os.listdir(engine_root)):
            name_path = os.path.join(engine_root, entry, "name")
            name = _safe_read(name_path)
            names.append(name or entry)
        if names:
            engines[os.path.basename(card)] = names
    return engines


def _get_gpu_freq_bounds(cards: List[str]) -> Dict[str, Dict[str, Optional[float]]]:
    result: Dict[str, Dict[str, Optional[float]]] = {}
    for card in cards:
        min_paths = [
            os.path.join(card, "gt_min_freq_mhz"),
            os.path.join(card, "device", "gt_min_freq_mhz"),
            os.path.join(card, "gt", "gt0", "rps_min_freq_mhz"),
            os.path.join(card, "gt", "gt0", "min_freq_mhz"),
        ]
        max_paths = [
            os.path.join(card, "gt_max_freq_mhz"),
            os.path.join(card, "device", "gt_max_freq_mhz"),
            os.path.join(card, "gt", "gt0", "rps_max_freq_mhz"),
            os.path.join(card, "gt", "gt0", "max_freq_mhz"),
        ]
        min_val = _read_first_existing(min_paths)
        max_val = _read_first_existing(max_paths)
        if min_val or max_val:
            result[os.path.basename(card)] = {
                "min_mhz": float(min_val) if min_val and min_val.isdigit() else None,
                "max_mhz": float(max_val) if max_val and max_val.isdigit() else None,
            }
    return result


def _parse_debugfs_vram_mm(card_index: int) -> Dict[str, Optional[float]]:
    path = f"/sys/kernel/debug/dri/{card_index}/vram0_mm"
    content = _safe_read(path)
    if not content:
        return {}

    size_match = re.search(r"(?m)^\s*size:\s*(\d+)\s*$", content)
    if not size_match:
        size_match = re.search(r"(?m)^\s*man\s+size\s*:\s*(\d+)\s*$", content)
    usage_match = re.search(r"(?m)^\s*usage:\s*(\d+)\s*$", content)

    total_bytes = float(size_match.group(1)) if size_match else None
    used_bytes = float(usage_match.group(1)) if usage_match else None
    usage_percent = None
    if total_bytes is not None and total_bytes > 0 and used_bytes is not None:
        usage_percent = round((used_bytes / total_bytes) * 100, 2)

    if total_bytes is None and used_bytes is None:
        return {}

    return {
        "total_bytes": total_bytes,
        "used_bytes": used_bytes,
        "usage_percent": usage_percent,
    }


def _parse_i915_gem_objects_vram(card_index: int) -> Dict[str, Optional[float]]:
    path = f"/sys/kernel/debug/dri/{card_index}/i915_gem_objects"
    content = _safe_read(path)
    if not content:
        return {}

    if not re.search(r"(?m)^local0:\s*", content):
        return {}

    total_bytes: Optional[float] = None
    used_bytes: Optional[float] = None

    chunk_match = re.search(
        r"(?mi)^chunk_size:.*?total:\s*([0-9]+)\s*MiB,\s*free:\s*([0-9]+)\s*MiB",
        content,
    )
    if chunk_match:
        total_mib = float(chunk_match.group(1))
        free_mib = float(chunk_match.group(2))
        total_bytes = total_mib * 1024 * 1024
        used_bytes = max(0.0, total_mib - free_mib) * 1024 * 1024

    local_block_match = re.search(r"(?ms)^local0:\s.*?(?=^\S|\Z)", content)
    if local_block_match:
        block = local_block_match.group(0)
        size_match = re.search(r"(?m)^\s*size:\s*(\d+)\s*$", block)
        usage_match = re.search(r"(?m)^\s*usage:\s*(\d+)\s*$", block)
        size_bytes = float(size_match.group(1)) if size_match else None
        usage_bytes = float(usage_match.group(1)) if usage_match else None
        if total_bytes is None and size_bytes is not None:
            total_bytes = size_bytes
        if used_bytes is None and usage_bytes is not None:
            used_bytes = usage_bytes

    if total_bytes is None and used_bytes is None:
        return {}

    usage_percent = None
    if total_bytes is not None and total_bytes > 0 and used_bytes is not None:
        usage_percent = round((used_bytes / total_bytes) * 100, 2)

    return {
        "total_bytes": total_bytes,
        "used_bytes": used_bytes,
        "usage_percent": usage_percent,
    }


def _get_gpu_driver_name(card: str) -> Optional[str]:
    driver_path = os.path.join(card, "device", "driver")
    if not os.path.exists(driver_path):
        return None
    try:
        resolved = os.path.realpath(driver_path)
    except Exception:
        return None
    name = os.path.basename(resolved)
    return name or None


def _get_system_memory_usage_bytes() -> Dict[str, Optional[float]]:
    mem = psutil.virtual_memory()
    total_bytes = float(mem.total) if mem.total else None
    used_bytes = float(mem.used) if mem.used else None
    usage_percent = None
    if total_bytes is not None and total_bytes > 0 and used_bytes is not None:
        usage_percent = round((used_bytes / total_bytes) * 100, 2)
    return {
        "total_bytes": total_bytes,
        "used_bytes": used_bytes,
        "usage_percent": usage_percent,
    }


def _get_gpu_vram(cards: List[str]) -> Dict[str, Dict[str, Optional[float]]]:
    result: Dict[str, Dict[str, Optional[float]]] = {}
    system_memory_stats = _get_system_memory_usage_bytes()

    for card in cards:
        card_name = os.path.basename(card)
        driver_name = _get_gpu_driver_name(card)

        match = re.match(r"^card(\d+)$", card_name)
        if driver_name == "i915":
            if match:
                card_index = int(match.group(1))
                i915_vram = _parse_i915_gem_objects_vram(card_index)
                if i915_vram:
                    result[card_name] = i915_vram
                    continue

            result[card_name] = {
                "total_bytes": system_memory_stats.get("total_bytes"),
                "used_bytes": system_memory_stats.get("used_bytes"),
                "usage_percent": system_memory_stats.get("usage_percent"),
            }
            continue

        if driver_name == "xe" and match:
            card_index = int(match.group(1))
            debugfs_vram = _parse_debugfs_vram_mm(card_index)
            if debugfs_vram:
                result[card_name] = debugfs_vram
                continue

        total_paths = [
            os.path.join(card, "device", "mem_info_vram_total"),
            os.path.join(card, "device", "local_mem_total_bytes"),
            os.path.join(card, "device", "vram_total"),
        ]
        used_paths = [
            os.path.join(card, "device", "mem_info_vram_used"),
            os.path.join(card, "device", "local_mem_used_bytes"),
            os.path.join(card, "device", "vram_used"),
        ]
        total_raw = _read_first_existing(total_paths)
        used_raw = _read_first_existing(used_paths)
        total = float(total_raw) if total_raw and total_raw.isdigit() else None
        used = float(used_raw) if used_raw and used_raw.isdigit() else None
        usage_percent = round((used / total) * 100, 2) if used is not None and total else None
        if total or used:
            result[card_name] = {
                "total_bytes": total,
                "used_bytes": used,
                "usage_percent": usage_percent,
            }
        else:
            result[card_name] = {
                "total_bytes": system_memory_stats.get("total_bytes"),
                "used_bytes": system_memory_stats.get("used_bytes"),
                "usage_percent": system_memory_stats.get("usage_percent"),
            }
    return result


def _get_gpu_pcie(cards: List[str]) -> Dict[str, Dict[str, Optional[str]]]:
    result: Dict[str, Dict[str, Optional[str]]] = {}
    for card in cards:
        base = os.path.join(card, "device")
        current_speed = _safe_read(os.path.join(base, "current_link_speed"))
        current_width = _safe_read(os.path.join(base, "current_link_width"))
        max_speed = _safe_read(os.path.join(base, "max_link_speed"))
        max_width = _safe_read(os.path.join(base, "max_link_width"))
        if current_speed or max_speed:
            result[os.path.basename(card)] = {
                "current_speed": current_speed,
                "current_width": current_width,
                "max_speed": max_speed,
                "max_width": max_width,
            }
    return result


def _get_npu_names() -> List[str]:
    output = _run_cmd(["lspci", "-nn"])
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


def _get_npu_fw_version() -> str:
    for pci_dev in _get_intel_vpu_pci_devices():
        fw_path = f"/sys/kernel/debug/accel/{pci_dev}/fw_version"
        fw = _safe_read(fw_path)
        if fw:
            return fw
    return "NA"


def _get_npu_freq_bounds() -> Dict[str, Dict[str, Optional[float]]]:
    result: Dict[str, Dict[str, Optional[float]]] = {}
    for pci_dev in _get_intel_vpu_pci_devices():
        device = os.path.join("/sys/bus/pci/devices", pci_dev)
        min_path = os.path.join(device, "npu_min_frequency_mhz")
        max_path = os.path.join(device, "npu_max_frequency_mhz")
        min_val = _safe_read(min_path)
        max_val = _safe_read(max_path)
        if min_val or max_val:
            result[pci_dev] = {
                "min_mhz": float(min_val) if min_val and min_val.isdigit() else None,
                "max_mhz": float(max_val) if max_val and max_val.isdigit() else None,
            }
    return result


def _expand_cpu_ranges(spec: Optional[str]) -> Set[int]:
    if not spec:
        return set()
    result: Set[int] = set()
    for token in re.split(r"[,\s]+", spec.strip()):
        if not token:
            continue
        match = re.match(r"^(\d+)-(\d+)$", token)
        if match:
            start = int(match.group(1))
            end = int(match.group(2))
            if end >= start:
                result.update(range(start, end + 1))
            continue
        if token.isdigit():
            result.add(int(token))
    return result


def _parse_lscpu_cpu_cache_entries() -> List[Dict[str, Any]]:
    output = _run_cmd(["lscpu", "--all", "--extended"])
    if not output:
        return []
    entries: List[Dict[str, Any]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not re.match(r"^\d+", stripped):
            continue
        parts = re.split(r"\s+", stripped)
        if len(parts) < 5 or not parts[0].isdigit():
            continue
        entries.append({"cpu": int(parts[0]), "cache": parts[4]})
    return entries


def _is_multi_socket() -> bool:
    cpuinfo = _safe_read("/proc/cpuinfo")
    if not cpuinfo:
        return False
    sockets = set()
    for line in cpuinfo.splitlines():
        match = re.match(r"^physical id\s*:\s*(\d+)", line.strip(), re.IGNORECASE)
        if match:
            sockets.add(match.group(1))
    return len(sockets) > 1


def _normalize_unique_core_ids(core_ids: List[int]) -> List[int]:
    return sorted(set(core_ids))


def _detect_core_groups() -> Dict[str, Any]:
    entries = _parse_lscpu_cpu_cache_entries()
    if not entries:
        return {"p_cores": [], "e_cores": [], "lpe_cores": [], "source": "unknown"}

    cpu_cache = {item["cpu"]: item["cache"] for item in entries}
    all_core_ids = [item["cpu"] for item in entries]
    remaining_core_ids = list(all_core_ids)
    p_cores: List[int] = []
    e_cores: List[int] = []
    lpe_cores: List[int] = []
    source_chain: List[str] = []

    def remove_remaining(to_remove: List[int]) -> None:
        remove_set = set(to_remove)
        nonlocal remaining_core_ids
        remaining_core_ids = [cid for cid in remaining_core_ids if cid not in remove_set]

    if _is_multi_socket():
        return {
            "p_cores": _normalize_unique_core_ids(all_core_ids),
            "e_cores": [],
            "lpe_cores": [],
            "source": "multi-socket",
        }

    cpuid_bin = shutil.which("cpuid")
    taskset_bin = shutil.which("taskset")
    if cpuid_bin and taskset_bin and remaining_core_ids:
        assigned: List[int] = []
        for core_id in list(remaining_core_ids):
            cache_pattern = cpu_cache.get(core_id, "")
            if cache_pattern.count(":") == 2:
                lpe_cores.append(core_id)
                assigned.append(core_id)
                continue

            output = _run_cmd([taskset_bin, "-c", str(core_id), cpuid_bin, "-1", "-l", "0x1a"], timeout=2)
            if not output:
                continue
            match = re.search(r"core type\s*=\s*([^\n]+)", output, re.IGNORECASE)
            if not match:
                continue
            core_type = match.group(1).strip().lower()
            if "intel core" in core_type:
                p_cores.append(core_id)
                assigned.append(core_id)
            elif "intel atom" in core_type:
                e_cores.append(core_id)
                assigned.append(core_id)

        if assigned:
            remove_remaining(assigned)
            source_chain.append("cpuid")

    if remaining_core_ids:
        core_set = _expand_cpu_ranges(_safe_read("/sys/devices/cpu_core/cpus"))
        atom_set = _expand_cpu_ranges(_safe_read("/sys/devices/cpu_atom/cpus"))
        lowpower_set = _expand_cpu_ranges(_safe_read("/sys/devices/cpu_lowpower/cpus"))
        assigned: List[int] = []
        atom_candidates: List[int] = []

        if core_set or atom_set or lowpower_set:
            for core_id in list(remaining_core_ids):
                if core_id in core_set:
                    p_cores.append(core_id)
                    assigned.append(core_id)
                elif core_id in lowpower_set:
                    lpe_cores.append(core_id)
                    assigned.append(core_id)
                elif core_id in atom_set:
                    atom_candidates.append(core_id)

            for core_id in atom_candidates:
                cache_pattern = cpu_cache.get(core_id, "")
                if cache_pattern.count(":") == 2:
                    lpe_cores.append(core_id)
                else:
                    e_cores.append(core_id)
                assigned.append(core_id)

        if assigned:
            remove_remaining(assigned)
            source_chain.append("sysfs")

    if remaining_core_ids:
        assigned: List[int] = []
        non_lpe_cores: List[int] = []

        for core_id in list(remaining_core_ids):
            cache_pattern = cpu_cache.get(core_id, "")
            if cache_pattern.count(":") == 2:
                lpe_cores.append(core_id)
                assigned.append(core_id)
            else:
                non_lpe_cores.append(core_id)

        def parse_l1d(cache_pattern: str) -> Optional[int]:
            if not cache_pattern:
                return None
            head = cache_pattern.split(":", 1)[0]
            return int(head) if head.isdigit() else None

        drop_index: Optional[int] = None
        prev_l1d: Optional[int] = None
        for i, core_id in enumerate(non_lpe_cores):
            l1d = parse_l1d(cpu_cache.get(core_id, ""))
            if l1d is None:
                prev_l1d = l1d
                continue
            if prev_l1d is not None and l1d < prev_l1d:
                drop_index = i
                break
            prev_l1d = l1d

        if drop_index is not None:
            for i, core_id in enumerate(non_lpe_cores):
                if i >= drop_index:
                    e_cores.append(core_id)
                else:
                    p_cores.append(core_id)
                assigned.append(core_id)

        if assigned:
            remove_remaining(assigned)
            source_chain.append("lscpu")

    if remaining_core_ids:
        assigned: List[int] = []
        processed: Set[int] = set()
        remaining_set = set(remaining_core_ids)
        has_smt_pairs = False

        for core_id in list(remaining_core_ids):
            if core_id in processed:
                continue
            pair_id = core_id + 1
            cache_pattern = cpu_cache.get(core_id, "")
            if pair_id in remaining_set and pair_id not in processed and cpu_cache.get(pair_id, "") == cache_pattern:
                p_cores.extend([core_id, pair_id])
                assigned.extend([core_id, pair_id])
                processed.update({core_id, pair_id})
                has_smt_pairs = True
            else:
                if has_smt_pairs:
                    e_cores.append(core_id)
                else:
                    p_cores.append(core_id)
                assigned.append(core_id)
                processed.add(core_id)

        if assigned:
            remove_remaining(assigned)
            source_chain.append("smt")

    if remaining_core_ids:
        p_cores.extend(remaining_core_ids)
        source_chain.append("default")

    source = "+".join(source_chain) if source_chain else "unknown"
    return {
        "p_cores": _normalize_unique_core_ids(p_cores),
        "e_cores": _normalize_unique_core_ids(e_cores),
        "lpe_cores": _normalize_unique_core_ids(lpe_cores),
        "source": source,
    }


def _classify_cores(freqs: List[Optional[float]]) -> Dict[str, Any]:
    cpu_count = len(freqs)
    cached_count = _CORE_CLASS_CACHE.get("cpu_count")
    cached_result = _CORE_CLASS_CACHE.get("result")
    if cached_result and cached_count == cpu_count:
        return cached_result

    detected = _detect_core_groups()
    if detected["p_cores"] or detected["e_cores"]:
        result = {
            "p_cores": detected["p_cores"],
            "e_cores": detected["e_cores"],
            "source": detected["source"],
        }
        _CORE_CLASS_CACHE["cpu_count"] = cpu_count
        _CORE_CLASS_CACHE["result"] = result
        return result

    valid_values = [f for f in freqs if f is not None]
    if not valid_values:
        result = {"p_cores": [], "e_cores": [], "source": "unknown"}
        _CORE_CLASS_CACHE["cpu_count"] = cpu_count
        _CORE_CLASS_CACHE["result"] = result
        return result

    freq_span = max(valid_values) - min(valid_values)
    if freq_span < 150:
        result = {"p_cores": [], "e_cores": [], "source": "single-cluster"}
        _CORE_CLASS_CACHE["cpu_count"] = cpu_count
        _CORE_CLASS_CACHE["result"] = result
        return result

    max_values = [f or 0 for f in freqs]
    sorted_idx = sorted(range(cpu_count), key=lambda i: max_values[i], reverse=True)
    split = max(1, cpu_count // 2)
    p_cores = sorted_idx[:split]
    e_cores = sorted_idx[split:]
    result = {"p_cores": p_cores, "e_cores": e_cores, "source": "heuristic"}
    _CORE_CLASS_CACHE["cpu_count"] = cpu_count
    _CORE_CLASS_CACHE["result"] = result
    return result


def _avg(values: List[Optional[float]], indices: List[int]) -> Optional[float]:
    picked = [values[i] for i in indices if i < len(values) and values[i] is not None]
    if not picked:
        return None
    return round(sum(picked) / len(picked), 2)


def _get_cpu_dynamic() -> Dict[str, Any]:
    usage_per_core = psutil.cpu_percent(interval=0.2, percpu=True)
    total_usage = psutil.cpu_percent(interval=None)
    freqs = psutil.cpu_freq(percpu=True)
    per_core_freq = [round(f.current, 1) if f else None for f in freqs or []]

    core_class = _classify_cores([f.max if f else None for f in freqs or []])
    p_cores = core_class["p_cores"]
    e_cores = core_class["e_cores"]

    return {
        "usage_total": round(total_usage, 2),
        "per_core_usage": [round(v, 2) for v in usage_per_core],
        "per_core_freq_mhz": per_core_freq,
        "p_core_usage": _avg(usage_per_core, p_cores),
        "e_core_usage": _avg(usage_per_core, e_cores),
        "p_core_freq_mhz": _avg(per_core_freq, p_cores),
        "e_core_freq_mhz": _avg(per_core_freq, e_cores),
        "p_core_indices": p_cores,
        "e_core_indices": e_cores,
        "core_type_source": core_class["source"],
    }


def _get_memory_dynamic() -> Dict[str, Any]:
    mem = psutil.virtual_memory()
    return {
        "usage_percent": round(mem.percent, 2),
        "total_gb": round(mem.total / (1024 ** 3), 2),
        "available_gb": round(mem.available / (1024 ** 3), 2),
    }


def _get_network_runtime_bw() -> Dict[str, Any]:
    stats = psutil.net_io_counters(pernic=True)
    now = time.time()
    prev_ts = _NET_RUNTIME_STATE.get("ts")
    prev_bytes = _NET_RUNTIME_STATE.get("bytes", {})

    interfaces: Dict[str, Dict[str, float]] = {}
    total_rx = 0.0
    total_tx = 0.0

    for name, counters in stats.items():
        last = prev_bytes.get(name)
        if prev_ts and last:
            delta_time = max(now - prev_ts, 0.0001)
            rx_rate = (counters.bytes_recv - last[0]) / delta_time
            tx_rate = (counters.bytes_sent - last[1]) / delta_time
        else:
            rx_rate = 0.0
            tx_rate = 0.0
        interfaces[name] = {
            "rx_bytes_per_sec": round(rx_rate, 2),
            "tx_bytes_per_sec": round(tx_rate, 2),
        }
        total_rx += rx_rate
        total_tx += tx_rate

    _NET_RUNTIME_STATE["ts"] = now
    _NET_RUNTIME_STATE["bytes"] = {k: (v.bytes_recv, v.bytes_sent) for k, v in stats.items()}

    return {
        "interfaces": interfaces,
        "total": {
            "rx_bytes_per_sec": round(total_rx, 2),
            "tx_bytes_per_sec": round(total_tx, 2),
        },
    }


def _get_network_static_info() -> Dict[str, Any]:
    stats = psutil.net_if_stats()
    network_speeds_mbps: Dict[str, int] = {}
    peak_speed: Optional[int] = None

    for name, nic in stats.items():
        raw_speed = getattr(nic, "speed", 0)
        try:
            speed = int(raw_speed)
        except (TypeError, ValueError):
            speed = 0
        network_speeds_mbps[name] = speed
        if speed > 0:
            peak_speed = speed if peak_speed is None else max(peak_speed, speed)

    return {
        "nic_count": len(stats),
        "network_speeds_mbps": network_speeds_mbps,
        "network_peak_mbps": peak_speed,
        "primary_interface": b_config.network_interface,
    }


def _get_disk_static_info() -> Dict[str, Any]:
    devices: List[Dict[str, Any]] = []

    output = _run_cmd(["lsblk", "-b", "-J", "-o", "NAME,SIZE,TYPE"])
    if output:
        try:
            payload = json.loads(output)
            for dev in payload.get("blockdevices", []):
                if dev.get("type") != "disk":
                    continue
                size_raw = dev.get("size")
                try:
                    size_bytes = int(size_raw)
                except (TypeError, ValueError):
                    size_bytes = None
                devices.append({
                    "name": dev.get("name") or "unknown",
                    "size_bytes": size_bytes,
                    "size_gb": round(size_bytes / (1024 ** 3), 2) if size_bytes else None,
                })
        except Exception as exc:
            logger.debug("Failed to parse lsblk output: %s", exc)

    if not devices:
        fallback: Dict[str, int] = {}
        for partition in psutil.disk_partitions(all=False):
            if partition.device in fallback:
                continue
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                fallback[partition.device] = usage.total
            except Exception:
                continue

        for device, size_bytes in fallback.items():
            devices.append({
                "name": os.path.basename(device) or device,
                "size_bytes": size_bytes,
                "size_gb": round(size_bytes / (1024 ** 3), 2) if size_bytes else None,
            })

    total_size_bytes = sum(d["size_bytes"] for d in devices if isinstance(d.get("size_bytes"), int))

    return {
        "device_count": len(devices),
        "total_size_bytes": total_size_bytes if total_size_bytes > 0 else None,
        "total_size_gb": round(total_size_bytes / (1024 ** 3), 2) if total_size_bytes > 0 else None,
        "devices": devices,
    }


def _tool_output(tool: str, args: Optional[List[str]] = None) -> Dict[str, Any]:
    args = args or []
    try:
        res = subprocess.run([tool] + args, capture_output=True, text=True, timeout=3)
    except FileNotFoundError:
        return {"available": False, "raw": None, "error": f"{tool} not found"}
    except Exception as exc:
        return {"available": False, "raw": None, "error": str(exc)}

    if res.returncode != 0:
        return {
            "available": False,
            "raw": res.stdout.strip() or None,
            "error": res.stderr.strip() or f"{tool} failed with code {res.returncode}",
        }
    return {"available": True, "raw": res.stdout.strip() or None, "error": None}


def _run_cmd_output(cmd: List[str], timeout: int = 3) -> Dict[str, Any]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"available": False, "raw": None, "error": f"{cmd[0]} not found"}
    except Exception as exc:
        return {"available": False, "raw": None, "error": str(exc)}

    if res.returncode != 0:
        return {
            "available": False,
            "raw": res.stdout.strip() or None,
            "error": res.stderr.strip() or f"{cmd[0]} failed with code {res.returncode}",
        }
    return {"available": True, "raw": res.stdout.strip() or None, "error": None}


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
    except SystemExit as exc:
        return {"available": False, "raw": None, "error": f"PmtTelemetry init failed: {exc}"}
    except Exception as exc:
        return {"available": False, "raw": None, "error": f"PmtTelemetry init failed: {exc}"}

    npu_busy_path = os.path.join(dev_path, "npu_busy_time_us")

    def read_busy_time() -> Optional[int]:
        if not os.path.exists(npu_busy_path):
            return None
        raw_text = _safe_read(npu_busy_path)
        if not raw_text:
            return None
        try:
            return int(raw_text)
        except ValueError:
            return None

    interval_ms = 200.0
    telemetry.update_buffer()
    busy_start = read_busy_time()
    energy_start = telemetry.get_npu_energy()
    bandwidth_start = telemetry.get_noc_bandwidth()

    time.sleep(interval_ms * 1e-3)

    telemetry.update_buffer()
    busy_end = read_busy_time()
    energy_end = telemetry.get_npu_energy()
    bandwidth_end = telemetry.get_noc_bandwidth()

    utilization_percent: Optional[float] = None
    if busy_start is not None and busy_end is not None:
        busy_delta = busy_end - busy_start
        utilization_percent = max(0.0, min(100.0, 100.0 * busy_delta / (interval_ms * 1e-3) / 1e6))

    power_w: Optional[float] = None
    if energy_start is not None and energy_end is not None:
        power_w = (energy_end - energy_start) / (interval_ms * 1e-3)

    memory_bytes: Optional[int] = None
    memory_path = os.path.join(dev_path, "npu_memory_utilization")
    memory_raw = _safe_read(memory_path)
    if memory_raw:
        try:
            memory_bytes = int(memory_raw)
        except ValueError:
            memory_bytes = None

    fw_version = _safe_read(os.path.join(debugfs_path, "fw_version")) if debugfs_path else None
    pciid = _safe_read(os.path.join(dev_path, "device"))
    module_version = _safe_read(os.path.join(driver_path, "module", "version"))
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


def _get_intel_npu_smi_output() -> Dict[str, Any]:
    try:
        return _collect_npu_smi_once()
    except Exception as exc:
        return {"available": False, "raw": None, "error": f"Failed to collect NPU metrics: {exc}"}


def _get_qmassa_binary() -> Optional[str]:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    candidate = os.path.join(repo_root, "tools", "qmassa")
    if os.path.exists(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return None


def _ensure_qmassa_running() -> Optional[str]:
    with _QMASSA_LOCK:
        proc = _QMASSA_STATE.get("process")
        if proc and proc.poll() is None:
            return _QMASSA_STATE.get("json_path")

        qmassa_bin = _get_qmassa_binary()
        if not qmassa_bin:
            return None

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        json_path = os.path.join("/tmp", "qmassa-metrics.json")
        try:
            proc = subprocess.Popen(
                [qmassa_bin, "-t", json_path],
                cwd=repo_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            _QMASSA_STATE["process"] = proc
            _QMASSA_STATE["json_path"] = json_path
            _QMASSA_STATE["last_start"] = time.time()
            return json_path
        except Exception as exc:
            logger.debug("Failed to start qmassa: %s", exc)
            return None


def shutdown_qmassa() -> None:
    proc = None
    json_path = None

    with _QMASSA_LOCK:
        proc = _QMASSA_STATE.get("process")
        json_path = _QMASSA_STATE.get("json_path")
        _QMASSA_STATE["process"] = None
        _QMASSA_STATE["json_path"] = None
        _QMASSA_STATE["last_start"] = 0.0
        _QMASSA_STATE["last_parsed"] = None

    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=1)
            except Exception as exc:
                logger.debug("Failed to stop qmassa process: %s", exc)

    if json_path and os.path.exists(json_path):
        try:
            os.remove(json_path)
        except Exception as exc:
            logger.debug("Failed to remove qmassa output file %s: %s", json_path, exc)


def _get_qmassa_output() -> Dict[str, Any]:
    json_path = _ensure_qmassa_running()
    if not json_path:
        return {"available": False, "raw": None, "parsed": None, "error": "qmassa not available"}
    if not os.path.exists(json_path):
        return {"available": False, "raw": None, "parsed": None, "error": f"{json_path} not found"}

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = f.read().strip()
        if not data:
            return {"available": False, "raw": None, "parsed": None, "error": "qmassa output empty"}
        parsed = _parse_qmassa_json(data)
        with _QMASSA_LOCK:
            cached = _QMASSA_STATE.get("last_parsed")
            if parsed is None:
                logger.debug("qmassa output is not valid JSON yet")
                if cached:
                    return {
                        "available": True,
                        "raw": data,
                        "parsed": cached,
                        "error": "qmassa output invalid, using cached",
                    }
                return {"available": True, "raw": data, "parsed": None, "error": "qmassa output invalid"}
            parsed = _merge_qmassa_with_cache(parsed, cached)
            _QMASSA_STATE["last_parsed"] = parsed
        return {"available": True, "raw": data, "parsed": parsed, "error": None}
    except Exception as exc:
        return {"available": False, "raw": None, "parsed": None, "error": str(exc)}


def _parse_qmassa_json(raw: str) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None

    states = payload.get("states") or []
    if not states:
        return None

    last_state = states[-1]
    timestamps = last_state.get("timestamps") or []
    timestamp = timestamps[-1] if timestamps else None

    devices = []
    for dev in last_state.get("devs_state") or []:
        dev_stats = dev.get("dev_stats") or {}
        freq_limits = dev.get("freq_limits") or []

        def _safe_float(val: Any) -> Optional[float]:
            if val is None:
                return None
            try:
                return float(val)
            except (ValueError, TypeError):
                return None

        freqs: List[Dict[str, Any]] = []
        freq_samples = dev_stats.get("freqs") or []
        latest_freqs = freq_samples[-1] if freq_samples else []
        for idx, freq_info in enumerate(latest_freqs or []):
            limit = freq_limits[idx] if idx < len(freq_limits) else {}
            name = (limit.get("name") or f"gt{idx}").lower()
            throttle = freq_info.get("throttle_reasons") or {}
            throttle_reasons = [k for k, v in throttle.items() if k != "status" and v]
            throttled = bool(throttle.get("status")) or bool(throttle_reasons)
            freqs.append({
                "name": name,
                "min_mhz": _safe_float(freq_info.get("min_freq")),
                "cur_mhz": _safe_float(freq_info.get("cur_freq")),
                "act_mhz": _safe_float(freq_info.get("act_freq")),
                "max_mhz": _safe_float(freq_info.get("max_freq")),
                "throttled": throttled,
                "throttle_reasons": throttle_reasons,
            })

        power_samples = dev_stats.get("power") or []
        power = power_samples[-1] if power_samples else {}

        eng_usage = dev_stats.get("eng_usage") or {}
        engine_util = {}
        eng_map = {
            "render": "rcs",
            "video": "vcs",
            "video-enhance": "vecs",
            "compute": "ccs",
            "copy": "bcs",
        }
        for engine, values in eng_usage.items():
            if isinstance(values, list):
                latest = values[-1] if values else None
            else:
                latest = values
            target = eng_map.get(engine, engine)
            existing = engine_util.get(target)
            if existing is None and latest is not None:
                engine_util[target] = _safe_float(latest)
            elif target not in engine_util:
                engine_util[target] = _safe_float(latest)

        devices.append({
            "pci_dev": dev.get("pci_dev"),
            "dev_type": dev.get("dev_type"),
            "drv_name": dev.get("drv_name"),
            "engines": dev.get("eng_names") or list(engine_util.keys()),
            "freqs": freqs,
            "power_w": {
                "gpu": _safe_float(power.get("gpu_cur_power")),
                "pkg": _safe_float(power.get("pkg_cur_power")),
            },
            "engine_util": engine_util,
        })

    return {
        "timestamp": timestamp,
        "version": payload.get("version"),
        "devices": devices,
    }


def _engine_util_missing(engine_util: Optional[Dict[str, Any]]) -> bool:
    if not engine_util:
        return True
    return all(value is None for value in engine_util.values())


def _merge_qmassa_with_cache(parsed: Dict[str, Any], cached: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not cached:
        return parsed
    cache_map = {dev.get("pci_dev"): dev for dev in cached.get("devices", [])}
    for dev in parsed.get("devices", []):
        cached_dev = cache_map.get(dev.get("pci_dev"))
        if not cached_dev:
            continue
        if _engine_util_missing(dev.get("engine_util")) and cached_dev.get("engine_util"):
            dev["engine_util"] = cached_dev["engine_util"]
        if not dev.get("engines") and cached_dev.get("engines"):
            dev["engines"] = cached_dev["engines"]
    return parsed


def _persist_monitor_snapshot(snapshot_type: str, data: Dict[str, Any]) -> None:
    try:
        collected_at = str(data.get("collected_at") or "")
        result = MonitorSnapshot.insert_snapshot(
            snapshot_type=snapshot_type,
            data=data,
            source="monitor.system_info",
            collected_at=collected_at,
        )
        if result.value != "SUCCESS":
            logger.debug("Persist %s snapshot failed: %s", snapshot_type, result.value)
    except Exception as exc:
        logger.debug("Persist %s snapshot exception: %s", snapshot_type, exc)


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_qmassa_history(qmassa: Dict[str, Any]) -> Dict[str, Any]:
    parsed = qmassa.get("parsed") or {}
    devices = parsed.get("devices") or []

    summarized_devices: List[Dict[str, Any]] = []
    for dev in devices:
        freqs = dev.get("freqs") or []
        summarized_freqs = []
        for freq in freqs:
            summarized_freqs.append({
                "name": freq.get("name"),
                "cur_mhz": _to_float(freq.get("cur_mhz")),
                "act_mhz": _to_float(freq.get("act_mhz")),
                "max_mhz": _to_float(freq.get("max_mhz")),
                "throttled": bool(freq.get("throttled")),
                "throttle_reasons": freq.get("throttle_reasons") or [],
            })

        engine_util = dev.get("engine_util") or {}
        summarized_engine_util = {
            key: _to_float(val)
            for key, val in engine_util.items()
        }

        summarized_devices.append({
            "pci_dev": dev.get("pci_dev"),
            "dev_type": dev.get("dev_type"),
            "drv_name": dev.get("drv_name"),
            "freqs": summarized_freqs,
            "power_w": {
                "gpu": _to_float((dev.get("power_w") or {}).get("gpu")),
                "pkg": _to_float((dev.get("power_w") or {}).get("pkg")),
            },
            "engine_util": summarized_engine_util,
        })

    return {
        "available": bool(qmassa.get("available")),
        "error": qmassa.get("error"),
        "parsed": {
            "devices": summarized_devices,
        },
    }


def _build_npu_history(npu_smi: Dict[str, Any]) -> Dict[str, Any]:
    raw = npu_smi.get("raw")
    parsed = None
    utilization_percent = None

    def _parse_util_from_raw(raw_text: str) -> Optional[float]:
        lines = raw_text.splitlines()
        for idx, line in enumerate(lines):
            if "NPU Utilization" not in line:
                continue
            for probe in lines[idx + 1: idx + 5]:
                match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:\[%\]|%)", probe)
                if match:
                    value = _to_float(match.group(1))
                    if value is None:
                        continue
                    return max(0.0, min(value, 100.0))

        fallback = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:\[%\]|%)", raw_text)
        if fallback:
            value = _to_float(fallback.group(1))
            if value is not None:
                return max(0.0, min(value, 100.0))
        return None

    if isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                parsed = loaded
                utilization_percent = _to_float(
                    loaded.get("utilization_percent", loaded.get("utilization"))
                )
        except Exception:
            parsed = None

        if utilization_percent is None:
            utilization_percent = _parse_util_from_raw(raw)

    return {
        "available": bool(npu_smi.get("available")),
        "error": npu_smi.get("error"),
        "raw_present": bool(raw),
        "utilization_percent": round(utilization_percent, 3) if utilization_percent is not None else None,
        "parsed": parsed,
    }


def _build_disk_history(disk: Dict[str, Any]) -> Dict[str, Optional[float]]:
    disk_io = disk.get("disk_io") if isinstance(disk, dict) else None
    if not isinstance(disk_io, dict):
        return {
            "utilization": None,
            "read_kb_per_sec": None,
            "write_kb_per_sec": None,
        }

    max_util: Optional[float] = None
    total_read_kb = 0.0
    total_write_kb = 0.0

    for item in disk_io.values():
        if not isinstance(item, dict):
            continue
        util_val = _to_float(item.get("utilization"))
        if util_val is not None:
            if util_val <= 1:
                util_val *= 100
            util_val = max(0.0, min(util_val, 100.0))
            max_util = util_val if max_util is None else max(max_util, util_val)

        read_kb = _to_float(item.get("read_kb_per_sec"))
        write_kb = _to_float(item.get("write_kb_per_sec"))
        total_read_kb += read_kb if read_kb is not None else 0.0
        total_write_kb += write_kb if write_kb is not None else 0.0

    return {
        "utilization": round(max_util, 2) if max_util is not None else None,
        "read_kb_per_sec": round(total_read_kb, 2),
        "write_kb_per_sec": round(total_write_kb, 2),
    }


def _build_network_history(network: Dict[str, Any]) -> Dict[str, Any]:
    total = network.get("total") if isinstance(network, dict) else None
    if not isinstance(total, dict):
        total = {}

    rx_bytes = _to_float(total.get("rx_bytes_per_sec"))
    tx_bytes = _to_float(total.get("tx_bytes_per_sec"))

    total_mbps = None
    if rx_bytes is not None or tx_bytes is not None:
        total_mbps = ((rx_bytes or 0.0) + (tx_bytes or 0.0)) * 8.0 / 1_000_000.0

    bw_kbit = _to_float(getattr(b_config, "network_bandwidth_kbit", None))
    max_mbps = (bw_kbit / 1000.0) if bw_kbit and bw_kbit > 0 else None

    utilization_percent = None
    if total_mbps is not None and max_mbps and max_mbps > 0:
        utilization_percent = max(0.0, min((total_mbps / max_mbps) * 100.0, 100.0))

    return {
        "total": {
            "rx_bytes_per_sec": rx_bytes,
            "tx_bytes_per_sec": tx_bytes,
        },
        "total_mbps": round(total_mbps, 3) if total_mbps is not None else None,
        "utilization_percent": round(utilization_percent, 3) if utilization_percent is not None else None,
    }


def _build_dynamic_history_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    cpu = data.get("cpu") or {}
    memory = data.get("memory") or {}
    pressure = data.get("pressure") or {}
    network = data.get("network") or {}
    disk = data.get("disk") or {}
    gpu = data.get("gpu") or {}
    npu = data.get("npu") or {}

    return {
        "collected_at": data.get("collected_at"),
        "cpu": {
            "usage_total": _to_float(cpu.get("usage_total")),
        },
        "memory": {
            "usage_percent": _to_float(memory.get("usage_percent")),
        },
        "pressure": {
            "cpu": _to_float(pressure.get("cpu")),
            "memory": _to_float(pressure.get("memory")),
            "io": _to_float(pressure.get("io")),
        },
        "disk": _build_disk_history(disk),
        "network": _build_network_history(network),
        "gpu": {
            "vram": gpu.get("vram") or {},
            "qmassa": _build_qmassa_history(gpu.get("qmassa") or {}),
        },
        "npu": {
            "npu_smi": _build_npu_history(npu.get("npu_smi") or {}),
        },
    }


def _persist_dynamic_snapshot_if_due(data: Dict[str, Any]) -> None:
    now = time.time()
    with _DYNAMIC_SNAPSHOT_LOCK:
        last_ts = float(_DYNAMIC_SNAPSHOT_STATE.get("last_persist_ts") or 0.0)
        if now - last_ts < _DYNAMIC_SNAPSHOT_MIN_INTERVAL_SEC:
            return
        _DYNAMIC_SNAPSHOT_STATE["last_persist_ts"] = now

    payload = _build_dynamic_history_payload(data)
    _persist_monitor_snapshot("dynamic", payload)


def collect_static_info(force_refresh: bool = False) -> Dict[str, Any]:
    cached = _STATIC_CACHE.get("data")
    if cached is not None and not force_refresh:
        return cached

    with _STATIC_CACHE_LOCK:
        cached = _STATIC_CACHE.get("data")
        if cached is not None and not force_refresh:
            return cached

        now = time.time()

        cpu_freqs = _get_cpu_freq_summary()
        mem = psutil.virtual_memory()
        cards = _get_gpu_cards()
        network_static = _get_network_static_info()
        disk_static = _get_disk_static_info()

        data = {
            "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "bios": {"version": _parse_bios_version()},
            "os": {"version": _parse_os_version()},
            "driver": {
                "kernel_version": _safe_read("/proc/sys/kernel/osrelease"),
                "kernel_cmdline": _safe_read("/proc/cmdline"),
                "mesa": _get_dpkg_version("mesa-common-dev"),
                "opencl": _get_dpkg_version("intel-opencl-icd"),
                "level_zero": _get_dpkg_version("intel-level-zero-gpu"),
                "media": _get_dpkg_version("intel-media-va-driver-non-free"),
                "npu_fw": _get_npu_fw_version(),
            },
            "cpu": {
                "model_name": _parse_cpu_model(),
                "core_count": {
                    "logical": psutil.cpu_count(logical=True),
                    "physical": psutil.cpu_count(logical=False),
                },
                "freq_mhz": cpu_freqs,
            },
            "memory": {
                "ddr_speeds": _parse_ddr_speeds(),
                "total_gb": round(mem.total / (1024 ** 3), 2),
            },
            "io": network_static,
            "disk": disk_static,
            "gpu": {
                "names": _get_gpu_names(),
                "count": len(cards),
                "engines": _get_gpu_engines(cards),
                "freq_bounds_mhz": _get_gpu_freq_bounds(cards),
                "vram": _get_gpu_vram(cards),
                "pcie": _get_gpu_pcie(cards),
            },
            "npu": {
                "names": _get_npu_names(),
                "freq_bounds_mhz": _get_npu_freq_bounds(),
            },
        }

        _persist_monitor_snapshot("static", data)

        _STATIC_CACHE["data"] = data
        _STATIC_CACHE["ts"] = now
        return data

def preload_static_info() -> Dict[str, Any]:
    data = collect_static_info()
    logger.info("Static system info preloaded at startup: %s", data.get("collected_at"))
    return data


def collect_dynamic_info(resource_monitor=None) -> Dict[str, Any]:
    psi = PSIMonitor().get_current_pressure()
    disk_stats = resource_monitor.get_disk_stats() if resource_monitor else {}
    gpu_cards = _get_gpu_cards()

    data = {
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cpu": _get_cpu_dynamic(),
        "memory": _get_memory_dynamic(),
        "pressure": psi,
        "network": _get_network_runtime_bw(),
        "disk": disk_stats,
        "gpu": {
            "vram": _get_gpu_vram(gpu_cards),
            "qmassa": _get_qmassa_output(),
        },
        "npu": {
            "npu_smi": _get_intel_npu_smi_output(),
        },
    }

    _persist_dynamic_snapshot_if_due(data)

    return data
