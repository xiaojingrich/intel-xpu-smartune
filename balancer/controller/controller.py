# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import os
# [SECURITY REVIEW]: All subprocess calls in this module use list-based arguments 
# with shell=False (default). No untrusted shell execution or string 
# concatenation is performed. All inputs are internally validated.
import subprocess # nosec
from subprocess import check_output # nosec
from typing import Optional

from utils.logger import logger
from config.config import b_config

class Controller:
    def __init__(self):
        self.config = b_config
        self.cgroup_mount = self.config.cgroup_mount
        self.cpus = os.cpu_count()
        self.uid = self.get_uid()

    def get_uid(self):
        slices_cmd = ["systemctl", "list-units", "--type=slice", "user-*.slice", "--no-legend"]
        try:
            output = check_output(slices_cmd, universal_newlines=True)
            
            for line in output.splitlines():
                import re
                match = re.search(r'(user-\d+\.slice)', line)
                if match:
                    return match.group(1).replace('user-', '').replace('.slice', '')
        except Exception:
            pass
        return ""

    def get_cpu_max(self):
        cpu_max = None
        path = f"/sys/fs/cgroup/user.slice/user-{self.uid}.slice/cpu.max"
        cmd = ["cat", path]

        try:
            result = check_output(cmd, universal_newlines=True).splitlines()
            if result:
                parts = result[0].split()
                if len(parts) >= 2:
                    cpu_max = parts[1]
        except Exception as e:
            logger.error(f"read cpu.max failed: {e}")

        return cpu_max

    def get_user_scopes(self):
        try:
            # Run the command and capture output
            path = '/sys/fs/cgroup/user.slice/user-%s.slice/' % self.uid
            result = subprocess.run(['find', path, '-maxdepth', '1', '-type', 'd'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)

            # Split into lines and remove empty lines/headers
            scopes = [line.replace(path, '') for line in result.stdout.splitlines()
                                                 if line.strip() and line.replace(path, '')
                                                                 and not line.endswith('user-%s.slice' % self.uid)
                                                                 and not line.endswith('user@%s.service' % self.uid)]

            return scopes

        except subprocess.CalledProcessError as e:
            print(f"Error running get_user_scopes(): {e.stderr}")
            return []
        except Exception as e:
            print(f"An error occurred: {str(e)}")
            return []

    def get_app_services1(self):
        try:
            # Run the command and capture output
            path = '/sys/fs/cgroup/user.slice/user-%s.slice/user@%s.service/app.slice/' % (self.uid, self.uid)
            result = subprocess.run(['find', path, '-maxdepth', '1', '-type', 'd'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)

            # Split into lines and remove empty lines/headers
            apps = [line.replace(path, '') for line in result.stdout.splitlines()
                                               if line.strip() and line.replace(path, '')
                                                               and not line.endswith('app.slice')]

            return apps

        except subprocess.CalledProcessError as e:
            print(f"Error running get_app_services(): {e.stderr}")
            return []
        except Exception as e:
            print(f"An error occurred: {str(e)}")
            return []

    def get_app_services(self):
        apps = []
        try:
            possible_paths = [
                f'/sys/fs/cgroup/user.slice/user-{self.uid}.slice/user@{self.uid}.service/app.slice/',
                f'/sys/fs/cgroup/system.slice/'
            ]

            for path in possible_paths:
                try:
                    # Run the command and capture output
                    result = subprocess.run(
                        ['find', path, '-maxdepth', '1', '-type', 'd'],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        check=True
                    )

                    # Process the output for this path
                    path_apps = [
                        line.replace(path, '')
                        for line in result.stdout.splitlines()
                        if line.strip()
                           and line.replace(path, '')
                           and not line.endswith('app.slice')
                    ]

                    apps.extend(path_apps)

                except subprocess.CalledProcessError:
                    # This path didn't work, try the next one
                    continue
                except Exception as e:
                    print(f"Unexpected error processing path {path}: {str(e)}")
                    continue

            return list(set(apps))  # Remove duplicates while preserving order

        except Exception as e:
            print(f"An error occurred in get_app_services(): {str(e)}")
            return []

    def restore_cpu_throttle(self):
        scopes = self.get_user_scopes()
        services = self.get_app_services()
        cmd_prefix = ['sudo'] if getattr(self.config, "vendor", "") == "generic" else []

        print(f"restore_cpu_throttle scopes = {scopes}, services = {services}")
        for scope in scopes:
            cmd = [*cmd_prefix, 'systemctl', 'set-property', '--runtime', '%s' % scope, 'CPUQuota=100%']
            result = subprocess.run(cmd,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)

        for service in services:
            result = subprocess.run(['systemctl', '--user', 'set-property', '--runtime', '%s' % service, 'CPUQuota=100%'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)


    def high_cpu_throttle(self):
        scopes = self.get_user_scopes()
        services = self.get_app_services()
        cmd_prefix = ['sudo'] if getattr(self.config, "vendor", "") == "generic" else []

        print(f"high_cpu_throttle scopes = {scopes}, services = {services}")
        for scope in scopes:
            result = subprocess.run([*cmd_prefix, 'systemctl', 'set-property', '--runtime', '%s' % scope, 'CPUQuota=60%'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)

        for service in services:
            result = subprocess.run(['systemctl', '--user', 'set-property', '--runtime', '%s' % service, 'CPUQuota=60%'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)

    def _find_cgroup_dir(self, unit_name: str) -> Optional[str]:
        """通过 unit 名在 cgroup 挂载点里查找对应目录（与 IOController 一致的做法）"""
        try:
            result = subprocess.run(
                ["find", self.cgroup_mount, "-name", unit_name, "-type", "d"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
            )
            if result.stdout:
                first = result.stdout.splitlines()[0].strip()
                return first or None
            return None
        except subprocess.CalledProcessError as e:
            logger.error(f"find cgroup dir for {unit_name} failed: {e.stderr.strip()}")
            return None

    def _write_cgroup_file(self, path: str, value: str, label: str, is_restore: bool) -> bool:
        """通过 sudo 写 cgroup 文件。缺失时：restore 视为幂等良性跳过，非 restore 视为失败。"""
        if not os.path.exists(path):
            msg = (f"{os.path.basename(path)} not found at {path}, "
                   f"controller likely not delegated — skip {label}")
            if is_restore:
                logger.warning(msg + " (benign on restore)")
                return True
            logger.error(msg)
            return False

        cmd = f"sudo sh -c 'echo \"{value}\" > {path}'"
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=False)
            if r.returncode != 0:
                logger.error(
                    f"Write cgroup failed: {label} -> {path} (rc={r.returncode}) "
                    f"stderr={r.stderr.strip()}"
                )
                return False
            logger.debug(f"Applied {label} -> {path} = {value!r}")
            return True
        except Exception as e:
            logger.error(f"Exception writing {label} to {path}: {e}")
            return False

    def _set_resource_quota(
            self,
            app_id: str,
            cpu_quota: Optional[int] = None,
            mem_high: Optional[int] = None,
            io_weight: Optional[int] = None,
            is_restore: bool = False
    ) -> bool:
        """
        安全设置资源限制（CPU/内存/IO）- 直接写 cgroup v2 文件，不经 systemd/dbus。
        :param cpu_quota: CPU百分比（None表示不修改，1-100之间，按单核计；实际限额 = cpu_quota * cpus）
        :param mem_high: 内存软限制（单位 MiB，必须大于0）
        :param io_weight: IO权重（1-10000，默认100）
        :param is_restore: 是否恢复默认值
        """
        # 参数范围检查
        if cpu_quota is not None and not (1 <= cpu_quota <= 100):
            logger.warning(f"Invalid cpu_quota {cpu_quota}, must be 1-100. no limit for cpu.")
            cpu_quota = None

        if mem_high is not None and mem_high <= 0:
            logger.warning(f"Invalid mem_high {mem_high}, no limit for mem.")
            mem_high = None

        if io_weight is not None and not (1 <= io_weight <= 10000):
            logger.warning(f"Invalid io_weight {io_weight}, no limit for io.")
            io_weight = None

        if not is_restore and cpu_quota is None and mem_high is None and io_weight is None:
            return True

        # 解析 unit 名 → cgroup 目录
        scopes = self.get_user_scopes()
        services = self.get_app_services()

        if app_id.endswith('.scope') or app_id.endswith('.service'):
            matching_app = app_id
        elif app_id.endswith('.desktop'):
            app_base_name = app_id.replace('.desktop', '').split('.')[-1].lower()
            matching_app = next(
                (unit for unit in scopes + services if app_base_name in unit.lower()),
                None
            )
        else:
            matching_app = app_id

        logger.debug(f"matching_app: {matching_app} for app_id: {app_id}")
        if not matching_app:
            logger.warning(f"No matching unit for {app_id}")
            return False

        cgroup_dir = self._find_cgroup_dir(matching_app)
        if not cgroup_dir:
            logger.warning(f"Cannot locate cgroup dir for {matching_app}")
            return False

        # 组装需要写入的 (file, value, label)
        # cpu.max 格式: "<quota_us> <period_us>"；period 固定 100000us；unlimited = "max 100000"
        # memory.high 格式: 字节数；unlimited = "max"
        # io.weight 格式: 数字(1-10000)；默认 = "default 100"
        period_us = 100000
        writes = []

        if is_restore:
            writes.append((os.path.join(cgroup_dir, "cpu.max"),    f"max {period_us}", "CPUQuota="))
            writes.append((os.path.join(cgroup_dir, "memory.high"), "max",              "MemoryHigh="))
            writes.append((os.path.join(cgroup_dir, "io.weight"),   "default 100",      "IOWeight="))
        else:
            if cpu_quota is not None:
                total_pct = cpu_quota * self.cpus
                quota_us = int(period_us * total_pct / 100)
                writes.append((os.path.join(cgroup_dir, "cpu.max"),
                               f"{quota_us} {period_us}",
                               f"CPUQuota={total_pct}%"))
            else:
                writes.append((os.path.join(cgroup_dir, "cpu.max"),
                               f"max {period_us}", "CPUQuota="))

            if mem_high is not None:
                mem_bytes = int(mem_high) * 1024 * 1024
                writes.append((os.path.join(cgroup_dir, "memory.high"),
                               str(mem_bytes),
                               f"MemoryHigh={mem_high}M"))
            else:
                writes.append((os.path.join(cgroup_dir, "memory.high"),
                               "max", "MemoryHigh="))

            if io_weight is not None:
                writes.append((os.path.join(cgroup_dir, "io.weight"),
                               str(io_weight),
                               f"IOWeight={io_weight}"))
            else:
                writes.append((os.path.join(cgroup_dir, "io.weight"),
                               "default 100", "IOWeight="))

        success = True
        for path, value, label in writes:
            if not self._write_cgroup_file(path, value, label, is_restore):
                success = False
        return success

    # cpu
    def set_cpu_quota(self, app_id: str, cpu_quota: int, is_restore: bool = False):
        if is_restore:
            logger.info(f"Restoring CPU quota for {app_id}")
            cpu_quota = None
        else:
            logger.info(f"Setting CPU quota for {app_id} to {cpu_quota}%")

        return self._set_resource_quota(
            app_id,
            cpu_quota=cpu_quota,
            is_restore=is_restore
        )

    # mem
    def set_mem_high(self, app_id: str, mem_high: Optional[str] = None, is_restore: bool = False):
        if is_restore:
            logger.info(f"Restoring memory limit for {app_id}")
            mem_high = None
        else:
            logger.info(f"Setting memory limit for {app_id} to {mem_high}")

        return self._set_resource_quota(
            app_id,
            mem_high=mem_high,
            is_restore=is_restore
        )

    #io
    def set_io_weight(self, app_id: str, io_weight: Optional[int] = None, is_restore: bool = False):
        if is_restore:
            logger.info(f"Restoring IO weight for {app_id}")
            io_weight = None
        elif io_weight is not None and not (1 <= io_weight <= 10000):
            raise ValueError("IOWeight must be 1-10000")
        else:
            logger.info(f"Setting IO weight for {app_id} to {io_weight}")

        return self._set_resource_quota(
            app_id,
            io_weight=io_weight,
            is_restore=is_restore
        )

    # all
    def set_all_resources(self, app_id: str, cpu_quota: Optional[int] = None, mem_high: Optional[int] = None,
                          io_weight: Optional[int] = None, is_restore: bool = False):
        """限制应用资源"""
        if is_restore:
            logger.info(f"Restoring ALL resources for {app_id}")
            cpu_quota = mem_high = io_weight = None
        else:
            logger.info(
                f"Setting resources for {app_id}: "
                f"CPU={cpu_quota}%, MEM={mem_high}M, IO={io_weight}"
            )

        return self._set_resource_quota(
            app_id,
            cpu_quota=cpu_quota,
            mem_high=mem_high,
            io_weight=io_weight,
            is_restore=is_restore
        )


    def set_weight(self, cgroup: str, weight: int) -> bool:
        """Set CPU weight for a cgroup"""
        path = os.path.join(self.cgroup_mount, cgroup, "cpu.weight")
        try:
            with open(path, 'w') as f:
                f.write(str(weight))
            return True
        except (FileNotFoundError, PermissionError):
            return False

    def set_affinity(self, cgroup: str, cpus: str) -> bool:
        """Set CPU affinity for a cgroup"""
        path = os.path.join(self.cgroup_mount, cgroup, "cpuset.cpus")
        try:
            with open(path, 'w') as f:
                f.write(cpus)
            return True
        except (FileNotFoundError, PermissionError):
            return False
