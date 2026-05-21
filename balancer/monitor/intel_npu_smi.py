#!/usr/bin/python3

# Copyright 2025 Intel Corporation.
#
# This software and the related documents are Intel copyrighted materials, and
# your use of them is governed by the express license under which they were
# provided to you ("License"). Unless the License provides otherwise, you may
# not use, modify, copy, publish, distribute, disclose or transmit this
# software or the related documents without Intel's prior written permission.
#
# This software and the related documents are provided as is, with no express
# or implied warranties, other than those that are expressly stated in
# the License.

# pylint: disable=line-too-long,missing-module-docstring,invalid-name,too-many-locals,too-many-statements

import argparse
import os
import sys
import time
from time import sleep
import logging as LOG
import subprocess
import enum

# TODO: Upstream remove internal GUIDs
PMT_GUID_MTL = "0x130670b2"   # Meteor Lake telemetry GUID
PMT_GUID_ARL = "0x1306a0b3"   # Arrow Lake telemetry GUID
PMT_GUID_ARL_H = "0x1306a0b2" # Arrow Lake-H telemetry GUID
PMT_GUID_ARL_S = "0x1306a0b4" # Arrow Lake-S telemetry GUID
PMT_GUID_LNL = "0x3072005"    # Lunar Lake telemetry GUID
PMT_GUID_PTL = "0x3086000"    # Panther Lake telemetry GUID


def get_mtl_regs():
    return {
        'VPU_ENERGY': 0x628,
        'SOC_TEMPERATURES': 0x98,
        'VPU_WORKPOINT': 0x68,
        'VPU_MEMORY_BW': 0x0,
    }

def get_arl_regs():
    return get_mtl_regs()

def get_lnl_regs():
    return {
        'VPU_ENERGY': 0x5d0,
        'SOC_TEMPERATURES': 0x70,
        'VPU_WORKPOINT': 0x18,
        'VPU_MEMORY_BW': 0xc18
    }

def get_ptl_regs():
    return {
        'VPU_ENERGY': 0x670,
        'SOC_TEMPERATURES': 0x78,
        'VPU_WORKPOINT': 0x18,
        'VPU_MEMORY_BW': 0xc18
    }

class CpuGen(enum.IntEnum):
    MTL = 0
    ARL = 1
    LNL = 2
    PTL = 3

    def __str__(self):
        if self == CpuGen.MTL:
            return "Meteor Lake"
        if self == CpuGen.ARL:
            return "Arrow Lake"
        if self == CpuGen.LNL:
            return "Lunar Lake"
        if self == CpuGen.PTL:
            return "Panther Lake"
        return ""

def run_command(command, timeout=None):
    try:
        return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                              check=True, shell=True, encoding='ascii', errors='ignore', cwd=None)
    except subprocess.TimeoutExpired as err:
        if not err.stdout:
            err.stdout = ''
        return subprocess.CompletedProcess(command, 1, err.stdout)
    except subprocess.CalledProcessError as err:
        if not err.stdout:
            err.stdout = ''
        return subprocess.CompletedProcess(command, err.returncode, err.stdout)
    except FileNotFoundError:
        return subprocess.CompletedProcess(command, 0, "Executable not found")

class PmtTelemetry:
    def __init__(self):
        self.pmt_root = "/sys/class/intel_pmt"
        self.buffer = None
        self.regs = None
        self.telemetry_path = None
        self.cpu_gen = None

        self.vpu_energy_reg = None
        self.soc_temperatures_reg = None

        # Check if PMT sysfs exists
        if not os.path.exists(self.pmt_root):
            raise RuntimeError(f'PMT sysfs interface not found at {self.pmt_root}')

        for telem_dir in os.listdir(self.pmt_root):
            if not telem_dir.startswith('telem'):
                continue

            telem_path = os.path.join(self.pmt_root, telem_dir)
            guid_path = os.path.join(telem_path, 'guid')
            telemetry_path = os.path.join(telem_path, 'telem')
            size_path = os.path.join(telem_path, 'size')
            offset_path = os.path.join(telem_path, 'offset')

            if not all(os.path.exists(p) for p in [guid_path, telemetry_path, size_path, offset_path]):
                continue

            guid = fdump(guid_path)
            telem_size = int(fdump(size_path))
            telem_offset = int(fdump(offset_path))

            LOG.debug('Found PMT device %s with GUID %s, size %d, offset %d',
                     telem_dir, guid, telem_size, telem_offset)

            self.telemetry_path = telemetry_path
            if guid == PMT_GUID_MTL:
                self.cpu_gen = CpuGen.MTL
                self.regs = get_mtl_regs()
                break
            if guid in (PMT_GUID_ARL, PMT_GUID_ARL_H, PMT_GUID_ARL_S):
                self.cpu_gen = CpuGen.ARL
                self.regs = get_arl_regs()
                break
            if guid == PMT_GUID_LNL:
                self.cpu_gen = CpuGen.LNL
                self.regs = get_lnl_regs()
                break
            if guid == PMT_GUID_PTL:
                self.cpu_gen = CpuGen.PTL
                self.regs = get_ptl_regs()
                break

        if self.cpu_gen is None:
            raise RuntimeError('No CPU telemetry devices found with known GUIDs')

        LOG.debug('CPU generation detected: %s', self.cpu_gen)

    def read(self, offset, msb, lsb):
        """Function get_telem_sample slices bits from buffer buf at the container offset
        and bit masking specified by sample_spec."""
        buf = self.buffer
        # read 8 bytes from buffer from offset and convert it to 64 bit little endian integer
        data = int.from_bytes(buf[offset:offset + 8],
                              byteorder='little')
        # create mask
        msb_mask = 0xffffffffffffffff & ((2 ** (int(msb) + 1)) - 1)
        lsb_mask = 0xffffffffffffffff & ((2 ** (int(lsb))) - 1)
        mask = msb_mask & (~lsb_mask)
        # apply mask and shift right
        value = (data & mask) >> int(lsb)
        return value

    def update_buffer(self):
        with open(self.telemetry_path, 'rb') as fd:
            self.buffer = fd.read()

    def get_freq(self):
        raw = self.read(self.regs['VPU_WORKPOINT'], 7, 0)
        if self.cpu_gen == CpuGen.MTL:
            return 2 * raw / 3 / 10
        return 0.05 * raw

    def get_voltage(self):
        return self.read(self.regs['VPU_WORKPOINT'], 15, 8)

    def get_tile_config(self):
        return self.read(self.regs['VPU_WORKPOINT'], 23, 16)

    def get_npu_temperature(self):
        return self.read(self.regs['SOC_TEMPERATURES'], 47, 40)

    def get_npu_energy(self):
        # Units: joules, Cast: U32.18.14
        val = self.read(self.regs['VPU_ENERGY'], 63, 0)
        int_part = val >> 14
        float_part = (val & ((1 << 14) - 1)) / (1 << 14)
        return int_part + float_part

    def get_noc_bandwidth(self):
        val = self.read(self.regs['VPU_MEMORY_BW'], 31, 0)
        return val / 1e3

def check_fdinfo_for_intel_vpu(pid, fd_name):
    """Check if a file descriptor belongs to intel_vpu driver and extract memory info."""
    fdinfo_path = f'/proc/{pid}/fdinfo/{fd_name}'
    if not os.path.exists(fdinfo_path):
        return None

    try:
        with open(fdinfo_path, 'r') as f:
            fdinfo_content = f.read()
    except (OSError, PermissionError):
        return None

    LOG.debug('FDInfo for PID %d FD %s: %s', pid, fd_name, fdinfo_content)

    # Check if it's intel_vpu driver
    if 'drm-driver:\tintel_vpu' not in fdinfo_content and 'drm-driver:     intel_vpu' not in fdinfo_content:
        return None

    LOG.debug('Found NPU process PID %d', pid)

    # Extract drm-resident-memory from fdinfo (in KiB)
    memory_kib = 0
    for line in fdinfo_content.split('\n'):
        if line.startswith('drm-resident-memory:'):
            try:
                # Format: "drm-resident-memory:	12345 KiB"
                memory_kib = int(line.split()[1])
            except (ValueError, IndexError):
                memory_kib = 0
            break

    return memory_kib

def get_process_command(pid, fallback_command):
    """Get the full command line for a process."""
    try:
        with open(f'/proc/{pid}/cmdline', 'r') as cmd_file:
            cmdline = cmd_file.read().replace('\0', ' ').strip()
            return cmdline if cmdline else fallback_command
    except (OSError, PermissionError):
        return fallback_command

def process_pid_fds(pid, fallback_command):
    """Process file descriptors for a PID to find intel_vpu usage."""
    proc_fd_dir = f'/proc/{pid}/fd'
    if not os.path.exists(proc_fd_dir):
        return None

    try:
        fd_list = os.listdir(proc_fd_dir)
    except (OSError, PermissionError):
        return None

    for fd_name in fd_list:
        try:
            fd_link = os.readlink(os.path.join(proc_fd_dir, fd_name))
            if '/dev/accel/accel' not in fd_link:
                continue

            memory_kib = check_fdinfo_for_intel_vpu(pid, fd_name)
            if memory_kib is not None:
                command = get_process_command(pid, fallback_command)
                return {'pid': pid, 'command': command, 'memory_kib': memory_kib}
        except (OSError, PermissionError):
            continue

    return None

def get_npu_processes(dev_file):  # pylint: disable=too-many-branches
    """Get list of processes using the NPU by checking fdinfo for intel_vpu driver."""
    processes = []

    # Run lsof to get list of PIDs using /dev/accel/accel[N]
    result = run_command(f'lsof {dev_file} 2>/dev/null')
    if result.returncode != 0:
        return processes

    lines = result.stdout.strip().split('\n')
    if len(lines) <= 1:  # Only header or empty
        return processes

    # Parse lsof output and collect unique PIDs
    seen_pids = set()
    for line in lines[1:]:  # Skip header
        parts = line.split()
        if len(parts) < 2:
            continue

        try:
            pid = int(parts[1])
        except (ValueError, IndexError):
            continue

        if pid in seen_pids:
            continue

        seen_pids.add(pid)
        fallback_command = parts[0]

        proc_info = process_pid_fds(pid, fallback_command)
        if proc_info:
            processes.append(proc_info)

    return processes

def get_terminal_colors():
    """
    Generate ANSI escape codes for terminal's theme colors (0-15)
    and return them as a list of strings.
    """
    colors = [f"\033[38;5;{i}m" for i in range(16)]  # 16-color palette
    return colors

class Printer():
    def __init__(self, colored: bool = True):
        self.colored = colored
        if self.colored:
            self.colors = get_terminal_colors()
            self.print_palette()

    def print_palette(self):
        """
        Print the terminal's 16-color palette for debugging.
        Shows colors 0-7 in the first row and 8-15 in the second row.
        """
        reset = "\033[0m"
        s = ""
        for i in range(8):
            s += f"\033[48;5;{i}m   {reset}"
        LOG.debug("Color palette: %s", s)

        # Second row: colors 8-15
        s = ""
        for i in range(8, 16):
            s += f"\033[48;5;{i}m   {reset}"
        LOG.debug("               %s", s)

    def colored_string(self, text, color_code):
        """
        Print text using the given ANSI color code.
        """
        if not self.colored:
            return text

        reset = "\033[0m"
        return f"{self.colors[color_code]}{text}{reset}"

    def __getattr__(self, name):
        if name.startswith("C") and name[1:].isdigit():
            idx = int(name[1:])
            if 0 <= idx <= 15:
                return lambda s: self.colored_string(s, idx)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")


def logging_setup(args):
    log_f = '%(levelname)s: %(message)s'
    LOG.addLevelName(LOG.DEBUG, "\033[1;36mDEBUG\033[1;0m")
    LOG.addLevelName(LOG.INFO, "\033[1;32mINFO\033[1;0m")

    log_l = LOG.INFO

    if args.verbose:
        log_l = LOG.DEBUG

    LOG.basicConfig(format = log_f, level = log_l)

def format_memory(mem):
    KiB = 1024
    units = ["Bytes", "KiB", "MiB", "GiB", "TiB"]

    for unit in units:
        if mem < KiB:
            return mem, unit
        mem /= KiB

    return mem, units[-1]

def fdump(path):
    with open(path, "r") as f:
        return f.read().strip()

def main(): # pylint: disable=too-many-branches
    parser = argparse.ArgumentParser(
        prog='Intel NPU System Monitoring Tool',
        description="""
        A comprehensive tool for monitoring Intel Neural Processing Unit (NPU) performance metrics.
        """)

    parser.add_argument('-i', '--interval', metavar='<msec>', type=float, help='Probing interval in milliseconds.')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output.')
    parser.add_argument('-c', '--color', action='store_true', help='Colored output for convenience.')
    parser.add_argument('--csv', metavar='<path>', help='Output data in CSV format into the file, must be used together with interval option.')
    args = parser.parse_args()

    logging_setup(args)

    driver_path = "/sys/bus/pci/drivers/intel_vpu/"
    debugfs = "/sys/kernel/debug/accel/"
    pu = PmtTelemetry()
    if not os.path.exists(driver_path):
        print("Intel NPU driver 'intel_vpu' is not loaded.\n", file=sys.stderr)
        parser.print_help()
        sys.exit(1)

    for entry in os.listdir(driver_path):
        if entry.startswith("0000:"):
            dev_path = os.path.join(driver_path, entry)
            debugfs = os.path.join(debugfs, entry)
            accel_path = os.path.join(dev_path, "accel")
            if os.path.exists(accel_path):
                accel_entries = os.listdir(accel_path)
                if accel_entries:
                    accel_name = accel_entries[0]
                    dev_file = os.path.join("/dev/accel", accel_name)
                    LOG.debug('Device file found: %s', dev_file)
            break

    npu_busy = None
    if os.path.exists(os.path.join(dev_path, "npu_busy_time_us")):
        npu_busy = os.path.join(dev_path, "npu_busy_time_us")

    def read_busy_time():
        if npu_busy is None:
            return None
        return int(fdump(npu_busy))

    pciid = fdump(os.path.join(dev_path, "device"))
    fw_version = fdump(os.path.join(debugfs, "fw_version"))

    ver_str = fdump(os.path.join(driver_path, "module", "version"))
    driver_version = ver_str.split(' ')[0] if ver_str else 'N/A'
    LOG.debug('Intel NPU driver version found: %s', driver_version)

    mem_util_path = os.path.join(dev_path, "npu_memory_utilization")

    pu.update_buffer()
    time_1 = read_busy_time()
    e0 = pu.get_npu_energy()
    interval = args.interval if args.interval else 200
    bw0 = pu.get_noc_bandwidth()


    if args.csv:
        with open(args.csv, 'w') as ofile:
            ofile.write("timestamp,power,frequency,bandwidth,tile_config,temperature,utilization,memory_usage\n")

    p = Printer(colored=args.color)

    while True:
        sleep(interval * 1e-3)
        time_2 = read_busy_time()
        if args.interval or args.csv:
            os.system('clear')
        delta = time_2 - time_1
        utilization = int(100 * delta / (interval * 1e-3) / 1e6)

        mem_util, mem_util_unit = format_memory(int(fdump(mem_util_path)))

        pu.update_buffer()

        e1 = pu.get_npu_energy()
        power = (e1 - e0) / (interval * 1e-3)
        e0 = e1
        freq = pu.get_freq()
        tile_config = pu.get_tile_config()

        temp = pu.get_npu_temperature()

        bw = pu.get_noc_bandwidth()
        if bw > 1024:
            bandwidth = (bw - bw0) / 1024
            bw_unit = 'GiB/s'
        else:
            bandwidth = bw - bw0
            bw_unit = 'MiB/s'

        if args.csv:
            timestamp = int(time.time())
            mem_util_mb = mem_util if mem_util_unit == 'MiB' else mem_util * 1024
            with open(args.csv, 'a') as f:
                f.write(f"{timestamp},{power:.3f},{(freq * 1000) / 2:.0f},{bandwidth:.3f},{tile_config},{temp},{utilization},{mem_util_mb:.2f}\n")

        BORDER_COLOR = p.C3
        PLUS = BORDER_COLOR('+')
        VL = BORDER_COLOR('|') # Vertical line
        HL = PLUS + BORDER_COLOR('-----------------------------------------------------------------------------------------------') + PLUS
        HDL = PLUS + BORDER_COLOR('===============================================================================================') + PLUS
        TL = p.C10 # Title color function
        BRC = p.C14 # Bracket color function

        def TX(x):
            return x
        def BR(x):
            return p.C13('[') + BRC(x) + p.C13(']')
        def FNUM(x):
            return TX(str(round(x, 2)))

        def FIELD(val, unit, align, direction=">", color=None):
            if isinstance(val, (int, float)):
                val = FNUM(val)
            raw = len(val)
            func = TX if color is None else color
            if unit:
                raw += len(unit) + 3 # plus space and brackets []
                field = str(func(val)) + ' ' + BR(unit)
            else:
                field = str(func(val))
            final_align = align + (len(field) - raw)
            return f"{field:{direction}{final_align}}"

        def LFIELD(val, unit, align, color=None):
            return FIELD(val, unit, align, direction="<", color=color)

        def SP(x, n): # N spacing left and right n-times
            return ' ' * n + x + ' ' * n

        print(HL)
        print(f'{VL} {p.C14("INTEL NPU")} {TL("Device")}: {TX(pciid):>6} {VL} {TL("Driver version")}: {TX(driver_version):>50} {VL}')
        print(f'{VL} {TL("Firmware version")}: {TX(fw_version[:75]):<75} {VL}')
        if len(fw_version) > 75:
            print(f'{VL} {TX(fw_version[75:]):<94}{VL}')
        print(HDL)
        print(f'{VL}{SP(TL("Power Usage"), 7)} {VL} {SP(TL("DPU Frequency"), 4)}{VL}{SP(TL("NPU DDR Average Bandwidth"), 1)}{VL}{SP(TL("Tile Config"), 3)}{VL}')
        print(f'{VL}{FIELD(power, "W", 25)} {VL} {FIELD((freq * 1000) / 2, "Hz", 20)} {VL} {FIELD(bandwidth, bw_unit, 25)} {VL} {TX(str(tile_config)):>15} {VL}')
        print(HDL)
        print(f'{VL}{SP(TL("NPU Temperature"), 5)} {VL}{SP(TL("NPU Utilization"), 7)}{VL} {FIELD("Memory Usage", None, 36, color=TL)} {VL}')
        print(f'{VL} {FIELD(temp, "°C", 24)} {VL} {FIELD(min(utilization, 100), "%", 27)} {VL} {FIELD(f"{mem_util:.2f}", mem_util_unit, 36)} {VL}')
        print(HDL)

        # Display list of processes using the NPU and fdinfo information
        npu_processes = get_npu_processes(dev_file)
        if npu_processes:
            print(f'{VL}{SP(TL("PID"), 4)}{VL}{SP(TL("Active NPU processes"), 23)} {VL}{SP(TL("Memory"), 4)} {VL}')
            print(HL)
            for proc in npu_processes:
                command = proc.get("command", "N/A")
                pid = proc.get("pid", "N/A")
                proc_mem, proc_mem_unit = format_memory(int(proc.get("memory_kib", 0)) * 1024)

                MAXW = 65 # Maximum process name width

                print(f'{VL} {LFIELD(pid, None, 9, color=p.C13)} {VL} {TX(command[:MAXW]):<{MAXW}} {VL} {FIELD(proc_mem, proc_mem_unit, 13)} {VL}')
                if len(command) > MAXW:
                    # Print continuation lines
                    remaining = command[MAXW:]
                    while remaining:
                        chunk = TX(remaining[:MAXW])
                        remaining = remaining[MAXW:]
                        print(f'{VL} {"":<9} {VL} {chunk:<{MAXW}} {VL} {"":<12}  {VL}')
            print(HL)

        time_1 = time_2
        bw0 = bw

        if not args.interval:
            break

if __name__ == "__main__":
    main()
