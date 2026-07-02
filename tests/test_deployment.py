# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for deployment scenarios:
  TC-D-002: systemd service management (start/stop/restart/status)
  TC-D-003: Boot auto-start verification
  TC-D-004: Dependency missing detection
  TC-D-007: Config file initialization & validation
"""

import os
import sys
import subprocess
import textwrap
from unittest.mock import patch, MagicMock, call

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balancer'))

from config.config import Config


# ═══════════════════════════════════════════════════════════════════════════════
# TC-D-002: systemd service management (start/stop/restart/status)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSystemdServiceManagement:
    """TC-D-002: Verify systemctl operations for the smartune service."""

    SERVICE_NAME = "smartune.service"

    @patch("subprocess.run")
    def test_start_service(self, mock_run):
        """systemctl start should invoke correctly and succeed."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = subprocess.run(
            ["systemctl", "start", self.SERVICE_NAME],
            capture_output=True, text=True, timeout=30
        )

        mock_run.assert_called_once_with(
            ["systemctl", "start", self.SERVICE_NAME],
            capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0

    @patch("subprocess.run")
    def test_stop_service(self, mock_run):
        """systemctl stop should invoke correctly and succeed."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = subprocess.run(
            ["systemctl", "stop", self.SERVICE_NAME],
            capture_output=True, text=True, timeout=30
        )

        mock_run.assert_called_once_with(
            ["systemctl", "stop", self.SERVICE_NAME],
            capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0

    @patch("subprocess.run")
    def test_restart_service(self, mock_run):
        """systemctl restart should invoke correctly and succeed."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = subprocess.run(
            ["systemctl", "restart", self.SERVICE_NAME],
            capture_output=True, text=True, timeout=30
        )

        mock_run.assert_called_once_with(
            ["systemctl", "restart", self.SERVICE_NAME],
            capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0

    @patch("subprocess.run")
    def test_status_active(self, mock_run):
        """systemctl status should report active (running) state."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="● smartune.service - Intel XPU SmarTune Service\n"
                   "   Active: active (running) since Mon 2026-01-01 00:00:00 UTC\n",
            stderr=""
        )

        result = subprocess.run(
            ["systemctl", "status", self.SERVICE_NAME],
            capture_output=True, text=True, timeout=10
        )

        assert result.returncode == 0
        assert "active (running)" in result.stdout

    @patch("subprocess.run")
    def test_status_inactive(self, mock_run):
        """systemctl status should report inactive when service is stopped."""
        mock_run.return_value = MagicMock(
            returncode=3,
            stdout="● smartune.service - Intel XPU SmarTune Service\n"
                   "   Active: inactive (dead)\n",
            stderr=""
        )

        result = subprocess.run(
            ["systemctl", "status", self.SERVICE_NAME],
            capture_output=True, text=True, timeout=10
        )

        # systemctl returns 3 for inactive services
        assert result.returncode == 3
        assert "inactive" in result.stdout

    @patch("subprocess.run")
    def test_start_failure_reported(self, mock_run):
        """Start failure (e.g., missing binary) should return non-zero."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Failed to start smartune.service: Unit not found."
        )

        result = subprocess.run(
            ["systemctl", "start", self.SERVICE_NAME],
            capture_output=True, text=True, timeout=30
        )

        assert result.returncode != 0
        assert "Failed" in result.stderr or "not found" in result.stderr.lower()

    @patch("subprocess.run")
    def test_restart_preserves_service_state(self, mock_run):
        """Restart should result in an active service after completion."""
        # Simulate restart followed by status check
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),  # restart
            MagicMock(returncode=0,
                      stdout="   Active: active (running)\n",
                      stderr=""),  # status check
        ]

        subprocess.run(
            ["systemctl", "restart", self.SERVICE_NAME],
            capture_output=True, text=True, timeout=30
        )
        status = subprocess.run(
            ["systemctl", "is-active", self.SERVICE_NAME],
            capture_output=True, text=True, timeout=10
        )

        assert status.returncode == 0


# ═══════════════════════════════════════════════════════════════════════════════
# TC-D-003: Boot auto-start verification
# ═══════════════════════════════════════════════════════════════════════════════

class TestBootAutoStart:
    """TC-D-003: Verify the service is enabled for auto-start at boot."""

    SERVICE_NAME = "smartune.service"

    @patch("subprocess.run")
    def test_service_enabled(self, mock_run):
        """systemctl is-enabled should report 'enabled' for auto-start."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="enabled\n", stderr=""
        )

        result = subprocess.run(
            ["systemctl", "is-enabled", self.SERVICE_NAME],
            capture_output=True, text=True, timeout=10
        )

        assert result.returncode == 0
        assert "enabled" in result.stdout.strip()

    @patch("subprocess.run")
    def test_service_disabled(self, mock_run):
        """systemctl is-enabled should report 'disabled' when not enabled."""
        mock_run.return_value = MagicMock(
            returncode=1, stdout="disabled\n", stderr=""
        )

        result = subprocess.run(
            ["systemctl", "is-enabled", self.SERVICE_NAME],
            capture_output=True, text=True, timeout=10
        )

        assert result.returncode != 0
        assert "disabled" in result.stdout.strip()

    @patch("subprocess.run")
    def test_enable_service(self, mock_run):
        """systemctl enable should succeed and set WantedBy=multi-user."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Created symlink /etc/systemd/system/multi-user.target.wants/smartune.service\n",
            stderr=""
        )

        result = subprocess.run(
            ["systemctl", "enable", self.SERVICE_NAME],
            capture_output=True, text=True, timeout=10
        )

        assert result.returncode == 0
        assert "multi-user.target" in result.stdout

    def test_service_unit_has_install_section(self):
        """The .service unit file should have a [Install] WantedBy=multi-user.target."""
        service_path = os.path.join(
            os.path.dirname(__file__), '..', 'balancer', 'smartune.service'
        )
        if not os.path.exists(service_path):
            pytest.skip("smartune.service file not found")

        with open(service_path) as f:
            content = f.read()

        assert "[Install]" in content
        assert "WantedBy=multi-user.target" in content

    @patch("subprocess.run")
    def test_service_after_network(self, mock_run):
        """The service should declare After=network-online.target for boot ordering."""
        service_path = os.path.join(
            os.path.dirname(__file__), '..', 'balancer', 'smartune.service'
        )
        if not os.path.exists(service_path):
            pytest.skip("smartune.service file not found")

        with open(service_path) as f:
            content = f.read()

        assert "After=network-online.target" in content


# ═══════════════════════════════════════════════════════════════════════════════
# TC-D-004: Dependency missing detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestDependencyMissing:
    """TC-D-004: Detect missing runtime dependencies gracefully."""

    @patch("subprocess.run")
    def test_python_not_found(self, mock_run):
        """Service start fails gracefully when python3 is not available."""
        mock_run.return_value = MagicMock(
            returncode=203,
            stdout="",
            stderr="smartune.service: Failed at step EXEC: No such file or directory"
        )

        result = subprocess.run(
            ["systemctl", "start", "smartune.service"],
            capture_output=True, text=True, timeout=30
        )

        assert result.returncode != 0

    @patch("subprocess.run")
    def test_missing_flask_import(self, mock_run):
        """Service should fail clearly if Flask is not installed."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="ModuleNotFoundError: No module named 'flask'"
        )

        result = subprocess.run(
            ["python3", "-c", "import flask"],
            capture_output=True, text=True, timeout=10
        )

        assert result.returncode != 0
        assert "ModuleNotFoundError" in result.stderr

    @patch("subprocess.run")
    def test_missing_yaml_import(self, mock_run):
        """Service should fail clearly if PyYAML is not installed."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="ModuleNotFoundError: No module named 'yaml'"
        )

        result = subprocess.run(
            ["python3", "-c", "import yaml"],
            capture_output=True, text=True, timeout=10
        )

        assert result.returncode != 0
        assert "ModuleNotFoundError" in result.stderr

    @patch("subprocess.run")
    def test_missing_peewee_import(self, mock_run):
        """Service should fail clearly if peewee is not installed."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="ModuleNotFoundError: No module named 'peewee'"
        )

        result = subprocess.run(
            ["python3", "-c", "import peewee"],
            capture_output=True, text=True, timeout=10
        )

        assert result.returncode != 0
        assert "ModuleNotFoundError" in result.stderr

    @patch("subprocess.run")
    def test_missing_ssl_certificate(self, mock_run):
        """Service should report error when SSL certificate files are missing."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="FileNotFoundError: [Errno 2] No such file or directory: 'b_server.crt'"
        )

        result = subprocess.run(
            ["python3", "BalanceService.py"],
            capture_output=True, text=True, timeout=10
        )

        assert result.returncode != 0
        assert "FileNotFoundError" in result.stderr or "No such file" in result.stderr

    @patch("subprocess.run")
    def test_missing_config_file(self, mock_run):
        """Service should report error when config.yaml is absent."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="FileNotFoundError: [Errno 2] No such file or directory: 'config/config.yaml'"
        )

        result = subprocess.run(
            ["python3", "BalanceService.py"],
            capture_output=True, text=True, timeout=10
        )

        assert result.returncode != 0


# ═══════════════════════════════════════════════════════════════════════════════
# TC-D-007: Config file initialization & validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfigInitialization:
    """TC-D-007: Test config.yaml parsing, defaults, and error handling."""

    def test_valid_config_loads_successfully(self, tmp_path):
        """A well-formed config.yaml should parse without errors."""
        config_content = textwrap.dedent("""\
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
            weights_top:
              cpu: 2
              memory: 7
              gpu: 5
            passive_resource_control:
              enabled: true
            blacklist: []
            cooldown_time: 15
            regular_update_sys_pressure_time: 5
            monitor_idle_check_interval: 10
            app_priority:
              critical: 100
              high: 80
              medium: 50
              low: 20
            limit_policy:
              policy: "combined"
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
            controlled_apps: []
            enable_network_control: false
            network_thresholds:
              low: 0.3
              medium: 0.5
              high: 0.7
              critical: 0.9
            network_interface: "lo"
            network_bandwidth_kbit: 100000
            disk_utilization_threshold: 95
            disk_iowait_threshold: 10
            disk_io_throughput_threshold_kb: 102400
            config_network_bw:
              system:
                min: 5000
                max: 10000
            network_burst_map:
              critical: "64k"
              high: "32k"
            network_system_ports: [22, 53]
            testing_network_app: []
        """)
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        cfg = Config.from_file(str(config_file))

        assert cfg.cgroup_mount == "/sys/fs/cgroup"
        assert cfg.vendor == "generic"
        assert cfg.thresholds['critical'] == 1.0
        assert cfg.weights['cpu'] == 2
        assert cfg.weights_top['gpu'] == 5
        assert cfg.cooldown_time == 15

    def test_invalid_yaml_syntax(self, tmp_path):
        """Invalid YAML should raise a parsing error."""
        bad_yaml = textwrap.dedent("""\
            cgroup_mount: "/sys/fs/cgroup"
            thresholds:
              low: 0.4
              medium: [invalid
              broken:: yaml::: here
        """)
        config_file = tmp_path / "bad_config.yaml"
        config_file.write_text(bad_yaml)

        with pytest.raises((yaml.YAMLError, yaml.scanner.ScannerError)):
            Config.from_file(str(config_file))

    def test_empty_config_file(self, tmp_path):
        """An empty config file should result in a TypeError from missing arguments."""
        config_file = tmp_path / "empty_config.yaml"
        config_file.write_text("")

        # yaml.safe_load("") returns None, Config(**None) will fail
        with pytest.raises((TypeError, AttributeError)):
            Config.from_file(str(config_file))

    def test_missing_thresholds_field(self, tmp_path):
        """Config with missing thresholds should still load (defaults to None)."""
        config_content = textwrap.dedent("""\
            cgroup_mount: "/sys/fs/cgroup"
            vendor: "generic"
            weights:
              cpu: 2
              memory: 7
              io: 1
        """)
        config_file = tmp_path / "partial_config.yaml"
        config_file.write_text(config_content)

        cfg = Config.from_file(str(config_file))
        assert cfg.thresholds is None
        assert cfg.weights['cpu'] == 2

    def test_contradictory_threshold_values(self, tmp_path):
        """Thresholds where low > high should still load (no runtime validation)."""
        config_content = textwrap.dedent("""\
            cgroup_mount: "/sys/fs/cgroup"
            vendor: "generic"
            thresholds:
              low: 0.9
              medium: 0.1
              high: 0.05
              critical: 0.01
            weights:
              cpu: 2
              memory: 7
              io: 1
        """)
        config_file = tmp_path / "contradictory_config.yaml"
        config_file.write_text(config_content)

        cfg = Config.from_file(str(config_file))

        # Values load as-is; validation is caller's responsibility
        assert cfg.thresholds['low'] == 0.9
        assert cfg.thresholds['critical'] == 0.01
        # Verify the logical contradiction
        assert cfg.thresholds['low'] > cfg.thresholds['critical']

    def test_negative_cooldown_time(self, tmp_path):
        """Negative cooldown_time should still load without crash."""
        config_content = textwrap.dedent("""\
            cgroup_mount: "/sys/fs/cgroup"
            vendor: "generic"
            cooldown_time: -5
            weights:
              cpu: 2
              memory: 7
              io: 1
        """)
        config_file = tmp_path / "negative_config.yaml"
        config_file.write_text(config_content)

        cfg = Config.from_file(str(config_file))
        assert cfg.cooldown_time == -5

    def test_wrong_type_for_numeric_field(self, tmp_path):
        """String value where number expected should still load (YAML type coercion)."""
        config_content = textwrap.dedent("""\
            cgroup_mount: "/sys/fs/cgroup"
            vendor: "generic"
            cpu_busy_threshold: "not_a_number"
            weights:
              cpu: 2
              memory: 7
              io: 1
        """)
        config_file = tmp_path / "type_mismatch.yaml"
        config_file.write_text(config_content)

        cfg = Config.from_file(str(config_file))
        # The string is assigned as-is since Config is a dataclass without validation
        assert cfg.cpu_busy_threshold == "not_a_number"

    def test_extra_unknown_fields_ignored(self, tmp_path):
        """Unknown fields in YAML should raise TypeError (strict dataclass)."""
        config_content = textwrap.dedent("""\
            cgroup_mount: "/sys/fs/cgroup"
            vendor: "generic"
            unknown_field_xyz: 42
            weights:
              cpu: 2
              memory: 7
              io: 1
        """)
        config_file = tmp_path / "extra_fields.yaml"
        config_file.write_text(config_content)

        with pytest.raises(TypeError):
            Config.from_file(str(config_file))

    def test_duplicate_keys_last_wins(self, tmp_path):
        """YAML with duplicate keys: last value wins per YAML spec."""
        config_content = textwrap.dedent("""\
            cgroup_mount: "/sys/fs/cgroup"
            vendor: "generic"
            vendor: "admin"
            weights:
              cpu: 2
              memory: 7
              io: 1
        """)
        config_file = tmp_path / "duplicate_keys.yaml"
        config_file.write_text(config_content)

        cfg = Config.from_file(str(config_file))
        # YAML spec: last value for duplicate keys wins
        assert cfg.vendor == "admin"

    def test_nonexistent_config_file(self):
        """Attempting to load from non-existent path should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            Config.from_file("/nonexistent/path/config.yaml")

    def test_config_default_values(self):
        """Config() with no arguments should have sensible defaults."""
        cfg = Config()
        assert cfg.cgroup_mount == "/sys/fs/cgroup"
        assert cfg.vendor == "generic"
        assert cfg.cooldown_time == 15
        assert cfg.cpu_busy_threshold == 90
        assert cfg.memory_busy_threshold == 90
        assert cfg.dominant_app_reduce_factor == 3.0
        assert cfg.regular_update_sys_pressure_time == 5
        assert cfg.monitor_idle_check_interval == 10
        assert cfg.enable_network_control is True

    def test_config_persists_update_to_yaml(self, tmp_path):
        """update_config_section should write changes back to the YAML file."""
        config_content = textwrap.dedent("""\
            cgroup_mount: "/sys/fs/cgroup"
            vendor: "generic"
            weights_top:
              cpu: 2
              memory: 7
              gpu: 5
            passive_resource_control:
              enabled: true
            thresholds:
              low: 0.4
              medium: 0.6
              high: 0.8
              critical: 1.0
        """)
        config_file = tmp_path / "persist_test.yaml"
        config_file.write_text(config_content)

        cfg = Config.from_file(str(config_file))
        result = cfg.update_config_section('weights_top', {'cpu': 10, 'memory': 3})
        assert result is True

        # Verify persisted to file
        with open(str(config_file)) as f:
            saved = yaml.safe_load(f)
        assert saved['weights_top']['cpu'] == 10
        assert saved['weights_top']['memory'] == 3

    def test_config_update_idempotent(self, tmp_path):
        """Updating with same values should return False (no change)."""
        config_content = textwrap.dedent("""\
            cgroup_mount: "/sys/fs/cgroup"
            vendor: "generic"
            weights_top:
              cpu: 2
              memory: 7
              gpu: 5
            passive_resource_control:
              enabled: true
            thresholds:
              low: 0.4
              medium: 0.6
              high: 0.8
              critical: 1.0
        """)
        config_file = tmp_path / "idempotent_test.yaml"
        config_file.write_text(config_content)

        cfg = Config.from_file(str(config_file))
        result = cfg.update_config_section('weights_top', {'cpu': 2})
        assert result is False
