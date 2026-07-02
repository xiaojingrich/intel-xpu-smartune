# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for balancer/monitor/npu_monitor.py — NPU telemetry and process monitoring."""

import os
import sys
import struct
import subprocess
from unittest.mock import patch, MagicMock, mock_open

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))

from monitor.npu_monitor import (
    PMT_GUID_MTL, PMT_GUID_ARL, PMT_GUID_LNL, PMT_GUID_PTL,
    get_mtl_regs, get_arl_regs, get_lnl_regs, get_ptl_regs,
    CpuGen, run_command, PmtTelemetry,
    check_fdinfo_for_intel_vpu, get_process_command,
    process_pid_fds, get_npu_processes,
)


class TestConstants:
    """Verify PMT GUID constants are correct strings."""

    def test_pmt_guid_mtl(self):
        assert PMT_GUID_MTL == "0x130670b2"

    def test_pmt_guid_arl(self):
        assert PMT_GUID_ARL == "0x1306a0b3"

    def test_pmt_guid_lnl(self):
        assert PMT_GUID_LNL == "0x3072005"

    def test_pmt_guid_ptl(self):
        assert PMT_GUID_PTL == "0x3086000"


class TestRegisterFunctions:
    """Verify register offset dictionaries returned by each platform."""

    def test_get_mtl_regs(self):
        regs = get_mtl_regs()
        assert regs == {
            'VPU_ENERGY': 0x628,
            'SOC_TEMPERATURES': 0x98,
            'VPU_WORKPOINT': 0x68,
            'VPU_MEMORY_BW': 0x0,
        }

    def test_get_arl_regs_same_as_mtl(self):
        assert get_arl_regs() == get_mtl_regs()

    def test_get_lnl_regs(self):
        regs = get_lnl_regs()
        assert regs == {
            'VPU_ENERGY': 0x5d0,
            'SOC_TEMPERATURES': 0x70,
            'VPU_WORKPOINT': 0x18,
            'VPU_MEMORY_BW': 0xc18,
        }

    def test_get_ptl_regs(self):
        regs = get_ptl_regs()
        assert regs == {
            'VPU_ENERGY': 0x670,
            'SOC_TEMPERATURES': 0x78,
            'VPU_WORKPOINT': 0x18,
            'VPU_MEMORY_BW': 0xc18,
        }


class TestCpuGenEnum:
    """Verify CpuGen enum values and string representation."""

    def test_enum_values(self):
        assert CpuGen.MTL == 0
        assert CpuGen.ARL == 1
        assert CpuGen.LNL == 2
        assert CpuGen.PTL == 3

    def test_str_mtl(self):
        assert str(CpuGen.MTL) == "Meteor Lake"

    def test_str_arl(self):
        assert str(CpuGen.ARL) == "Arrow Lake"

    def test_str_lnl(self):
        assert str(CpuGen.LNL) == "Lunar Lake"

    def test_str_ptl(self):
        assert str(CpuGen.PTL) == "Panther Lake"

    def test_is_intenum(self):
        assert int(CpuGen.MTL) == 0
        assert CpuGen.PTL > CpuGen.MTL


class TestRunCommand:
    """Test run_command with mocked subprocess.run."""

    @patch('monitor.npu_monitor.subprocess.run')
    def test_success(self, mock_run):
        completed = subprocess.CompletedProcess(['echo', 'hello'], 0, 'hello\n')
        mock_run.return_value = completed

        result = run_command(['echo', 'hello'])
        assert result.returncode == 0
        assert result.stdout == 'hello\n'
        mock_run.assert_called_once()

    @patch('monitor.npu_monitor.subprocess.run')
    def test_timeout_expired(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=['sleep', '100'], timeout=1, output='partial')

        result = run_command(['sleep', '100'], timeout=1)
        assert result.returncode == 1
        assert result.stdout == 'partial'

    @patch('monitor.npu_monitor.subprocess.run')
    def test_timeout_expired_no_stdout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=['sleep', '100'], timeout=1)

        result = run_command(['sleep', '100'], timeout=1)
        assert result.returncode == 1
        assert result.stdout == ''

    @patch('monitor.npu_monitor.subprocess.run')
    def test_called_process_error(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=2, cmd=['false'], output='error output')

        result = run_command(['false'])
        assert result.returncode == 2
        assert result.stdout == 'error output'

    @patch('monitor.npu_monitor.subprocess.run')
    def test_called_process_error_no_stdout(self, mock_run):
        err = subprocess.CalledProcessError(returncode=127, cmd=['missing'])
        err.stdout = None
        mock_run.side_effect = err

        result = run_command(['missing'])
        assert result.returncode == 127
        assert result.stdout == ''

    @patch('monitor.npu_monitor.subprocess.run')
    def test_file_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()

        result = run_command(['nonexistent_binary'])
        assert result.returncode == 0
        assert result.stdout == "Executable not found"

    @patch('monitor.npu_monitor.subprocess.run')
    def test_string_command_split_by_shlex(self, mock_run):
        completed = subprocess.CompletedProcess(['ls', '-la', '/tmp'], 0, 'output')
        mock_run.return_value = completed

        run_command('ls -la /tmp')
        args, kwargs = mock_run.call_args
        assert args[0] == ['ls', '-la', '/tmp']


class TestPmtTelemetryRead:
    """Test PmtTelemetry.read() bit extraction logic."""

    def _make_pmt(self):
        """Create a PmtTelemetry instance bypassing __init__."""
        pmt = object.__new__(PmtTelemetry)
        pmt.buffer = None
        pmt.regs = None
        pmt.cpu_gen = None
        return pmt

    def test_read_full_byte(self):
        pmt = self._make_pmt()
        # Place value 0xAB at offset 0 in an 8-byte buffer (little-endian)
        pmt.buffer = struct.pack('<Q', 0xAB)
        # Read bits 7:0 (full first byte)
        result = pmt.read(0, 7, 0)
        assert result == 0xAB

    def test_read_upper_bits(self):
        pmt = self._make_pmt()
        # Value: 0x00000000_0000FF00 at offset 0
        pmt.buffer = struct.pack('<Q', 0xFF00)
        # Read bits 15:8
        result = pmt.read(0, 15, 8)
        assert result == 0xFF

    def test_read_at_offset(self):
        pmt = self._make_pmt()
        # 16 bytes: first 8 are zeros, next 8 hold 0x12345678
        pmt.buffer = b'\x00' * 8 + struct.pack('<Q', 0x12345678)
        # Read bits 31:0 at offset 8
        result = pmt.read(8, 31, 0)
        assert result == 0x12345678

    def test_read_single_bit(self):
        pmt = self._make_pmt()
        # Value with bit 5 set: 0x20 = 0b00100000
        pmt.buffer = struct.pack('<Q', 0x20)
        assert pmt.read(0, 5, 5) == 1
        assert pmt.read(0, 4, 4) == 0

    def test_read_specific_range(self):
        pmt = self._make_pmt()
        # Value 0xDEAD at offset 0 — bits 15:0 = 0xDEAD
        pmt.buffer = struct.pack('<Q', 0xDEAD)
        # Extract bits 11:4 => (0xDEAD >> 4) & 0xFF = 0xEA
        result = pmt.read(0, 11, 4)
        assert result == 0xEA


class TestPmtTelemetryGetFreq:
    """Test PmtTelemetry.get_freq() formula per CPU generation."""

    def _make_pmt_with_workpoint(self, cpu_gen, raw_value):
        """Create PmtTelemetry with a buffer that yields raw_value at VPU_WORKPOINT bits 7:0."""
        pmt = object.__new__(PmtTelemetry)
        pmt.cpu_gen = cpu_gen
        if cpu_gen == CpuGen.MTL:
            pmt.regs = get_mtl_regs()
        elif cpu_gen == CpuGen.LNL:
            pmt.regs = get_lnl_regs()
        elif cpu_gen == CpuGen.PTL:
            pmt.regs = get_ptl_regs()
        else:
            pmt.regs = get_arl_regs()

        # Build buffer large enough to hold data at VPU_WORKPOINT offset
        offset = pmt.regs['VPU_WORKPOINT']
        buf = bytearray(offset + 8)
        struct.pack_into('<Q', buf, offset, raw_value)
        pmt.buffer = bytes(buf)
        return pmt

    def test_mtl_freq_formula(self):
        # For MTL: freq = 2 * raw / 3 / 10
        pmt = self._make_pmt_with_workpoint(CpuGen.MTL, 150)
        freq = pmt.get_freq()
        assert freq == pytest.approx(2 * 150 / 3 / 10)

    def test_lnl_freq_formula(self):
        # For non-MTL: freq = 0.05 * raw
        pmt = self._make_pmt_with_workpoint(CpuGen.LNL, 200)
        freq = pmt.get_freq()
        assert freq == pytest.approx(0.05 * 200)

    def test_ptl_freq_formula(self):
        pmt = self._make_pmt_with_workpoint(CpuGen.PTL, 100)
        freq = pmt.get_freq()
        assert freq == pytest.approx(0.05 * 100)

    def test_arl_freq_formula(self):
        # ARL uses MTL regs but different CPU gen, so formula is non-MTL path
        pmt = self._make_pmt_with_workpoint(CpuGen.ARL, 120)
        freq = pmt.get_freq()
        assert freq == pytest.approx(0.05 * 120)


class TestCheckFdinfoForIntelVpu:
    """Test check_fdinfo_for_intel_vpu with mocked filesystem."""

    @patch('monitor.npu_monitor.os.path.exists', return_value=False)
    def test_returns_none_if_file_missing(self, mock_exists):
        result = check_fdinfo_for_intel_vpu(1234, '5')
        assert result is None

    @patch('monitor.npu_monitor.os.path.exists', return_value=True)
    def test_returns_none_if_not_intel_vpu(self, mock_exists):
        fdinfo_content = "drm-driver:\ti915\ndrm-pdev:\t0000:00:02.0\n"
        with patch('builtins.open', mock_open(read_data=fdinfo_content)):
            result = check_fdinfo_for_intel_vpu(1234, '5')
        assert result is None

    @patch('monitor.npu_monitor.os.path.exists', return_value=True)
    def test_returns_memory_kib_for_intel_vpu(self, mock_exists):
        fdinfo_content = (
            "drm-driver:\tintel_vpu\n"
            "drm-pdev:\t0000:00:0b.0\n"
            "drm-resident-memory:\t4096 KiB\n"
        )
        with patch('builtins.open', mock_open(read_data=fdinfo_content)):
            result = check_fdinfo_for_intel_vpu(1234, '5')
        assert result == 4096

    @patch('monitor.npu_monitor.os.path.exists', return_value=True)
    def test_returns_zero_kib_when_no_memory_line(self, mock_exists):
        fdinfo_content = "drm-driver:\tintel_vpu\ndrm-pdev:\t0000:00:0b.0\n"
        with patch('builtins.open', mock_open(read_data=fdinfo_content)):
            result = check_fdinfo_for_intel_vpu(1234, '5')
        assert result == 0

    @patch('monitor.npu_monitor.os.path.exists', return_value=True)
    def test_returns_none_on_os_error(self, mock_exists):
        with patch('builtins.open', side_effect=OSError("Permission denied")):
            result = check_fdinfo_for_intel_vpu(1234, '5')
        assert result is None


class TestGetProcessCommand:
    """Test get_process_command with mocked /proc/pid/cmdline."""

    def test_returns_cmdline_with_nulls_replaced(self):
        cmdline_content = "python\0inference.py\0--model\0resnet50\0"
        with patch('builtins.open', mock_open(read_data=cmdline_content)):
            result = get_process_command(1234, 'fallback')
        assert result == "python inference.py --model resnet50"

    def test_returns_fallback_on_os_error(self):
        with patch('builtins.open', side_effect=OSError("No such file")):
            result = get_process_command(9999, 'my_fallback')
        assert result == 'my_fallback'

    def test_returns_fallback_on_permission_error(self):
        with patch('builtins.open', side_effect=PermissionError("denied")):
            result = get_process_command(9999, 'denied_fallback')
        assert result == 'denied_fallback'

    def test_returns_fallback_on_empty_cmdline(self):
        with patch('builtins.open', mock_open(read_data='')):
            result = get_process_command(1234, 'fallback_cmd')
        assert result == 'fallback_cmd'


class TestProcessPidFds:
    """Test process_pid_fds with mocked os.listdir and os.readlink."""

    @patch('monitor.npu_monitor.os.path.exists', return_value=False)
    def test_returns_none_if_proc_fd_missing(self, mock_exists):
        result = process_pid_fds(1234, 'app')
        assert result is None

    @patch('monitor.npu_monitor.check_fdinfo_for_intel_vpu', return_value=2048)
    @patch('monitor.npu_monitor.get_process_command', return_value='inference --model bert')
    @patch('monitor.npu_monitor.os.readlink', return_value='/dev/accel/accel0')
    @patch('monitor.npu_monitor.os.listdir', return_value=['0', '1', '3'])
    @patch('monitor.npu_monitor.os.path.exists', return_value=True)
    def test_returns_dict_when_accel_found(self, mock_exists, mock_listdir,
                                           mock_readlink, mock_get_cmd, mock_fdinfo):
        result = process_pid_fds(5678, 'app')
        assert result is not None
        assert result['pid'] == 5678
        assert result['command'] == 'inference --model bert'
        assert result['memory_kib'] == 2048

    @patch('monitor.npu_monitor.os.readlink', return_value='/dev/null')
    @patch('monitor.npu_monitor.os.listdir', return_value=['0', '1'])
    @patch('monitor.npu_monitor.os.path.exists', return_value=True)
    def test_returns_none_when_no_accel_link(self, mock_exists, mock_listdir, mock_readlink):
        result = process_pid_fds(5678, 'app')
        assert result is None

    @patch('monitor.npu_monitor.os.listdir', side_effect=PermissionError("denied"))
    @patch('monitor.npu_monitor.os.path.exists', return_value=True)
    def test_returns_none_on_permission_error(self, mock_exists, mock_listdir):
        result = process_pid_fds(5678, 'app')
        assert result is None


class TestGetNpuProcesses:
    """Test get_npu_processes with mocked run_command and process_pid_fds."""

    @patch('monitor.npu_monitor.run_command')
    def test_returns_empty_on_lsof_failure(self, mock_run_cmd):
        mock_run_cmd.return_value = subprocess.CompletedProcess(
            ['lsof', '/dev/accel/accel0'], 1, '')
        result = get_npu_processes('/dev/accel/accel0')
        assert result == []

    @patch('monitor.npu_monitor.process_pid_fds')
    @patch('monitor.npu_monitor.run_command')
    def test_parses_lsof_output(self, mock_run_cmd, mock_process_fds):
        lsof_output = (
            "COMMAND    PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
            "infer    12345 user    3u   CHR  511,0      0t0  123 /dev/accel/accel0\n"
            "infer    12345 user    4u   CHR  511,0      0t0  123 /dev/accel/accel0\n"
            "worker   67890 user    5u   CHR  511,0      0t0  123 /dev/accel/accel0\n"
        )
        mock_run_cmd.return_value = subprocess.CompletedProcess(
            ['lsof', '/dev/accel/accel0'], 0, lsof_output)

        mock_process_fds.side_effect = [
            {'pid': 12345, 'command': 'infer --model bert', 'memory_kib': 1024},
            {'pid': 67890, 'command': 'worker --batch 8', 'memory_kib': 2048},
        ]

        result = get_npu_processes('/dev/accel/accel0')
        assert len(result) == 2
        assert result[0]['pid'] == 12345
        assert result[1]['pid'] == 67890
        # Verify deduplication: PID 12345 appears twice in lsof but process_pid_fds called once
        assert mock_process_fds.call_count == 2

    @patch('monitor.npu_monitor.process_pid_fds', return_value=None)
    @patch('monitor.npu_monitor.run_command')
    def test_skips_pids_with_no_vpu_fds(self, mock_run_cmd, mock_process_fds):
        lsof_output = (
            "COMMAND    PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
            "bash     11111 user    3u   CHR  511,0      0t0  123 /dev/accel/accel0\n"
        )
        mock_run_cmd.return_value = subprocess.CompletedProcess(
            ['lsof', '/dev/accel/accel0'], 0, lsof_output)

        result = get_npu_processes('/dev/accel/accel0')
        assert result == []

    @patch('monitor.npu_monitor.run_command')
    def test_returns_empty_on_header_only(self, mock_run_cmd):
        lsof_output = "COMMAND    PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
        mock_run_cmd.return_value = subprocess.CompletedProcess(
            ['lsof', '/dev/accel/accel0'], 0, lsof_output)

        result = get_npu_processes('/dev/accel/accel0')
        assert result == []
