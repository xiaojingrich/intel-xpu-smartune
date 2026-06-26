# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import os
import select
import time
import datetime
from collections import defaultdict

class WeightedPSIMonitor:
    CPU_PRESSURE_FILE = "/proc/pressure/cpu"
    MEMORY_PRESSURE_FILE = "/proc/pressure/memory"
    IO_PRESSURE_FILE = "/proc/pressure/io"

    TRIGGER_CONFIG = {
        'cpu': (100, 5),      # 100ms stall within 5s triggers
        'memory': (1, 5),     # 1ms stall within 5s triggers
        'io': (100, 5)        # 100ms stall within 5s triggers
    }

    STATUS_LEVELS = {
        'low': 0.4,
        'medium': 0.6,
        'high': 0.8,
        'critical': 1.0
    }

    WEIGHTS = (2, 7, 1)
    WINDOW_SECS = 5

    def __init__(self):
        self.fds = {}
        self.last_total = {}
        self.pressure_history = defaultdict(list)
        # Last known pressure per resource — used to fill empty windows
        self.last_pressure = {'cpu': 0.0, 'memory': 0.0, 'io': 0.0}

    def _setup_trigger(self, fd, resource):
        some_ms, window_sec = self.TRIGGER_CONFIG[resource]
        trigger = f"some {some_ms * 1000} {window_sec * 1000000}\n"  # ms→us, sec→us
        try:
            os.write(fd, trigger.encode())
            os.lseek(fd, 0, os.SEEK_SET)
        except OSError as e:
            print(f"Failed to setup {resource} trigger: {e}")

    def setup_polling(self):
        self.fds = {
            'cpu': os.open(self.CPU_PRESSURE_FILE, os.O_RDWR | os.O_NONBLOCK),
            'memory': os.open(self.MEMORY_PRESSURE_FILE, os.O_RDWR | os.O_NONBLOCK),
            'io': os.open(self.IO_PRESSURE_FILE, os.O_RDWR | os.O_NONBLOCK)
        }
        for resource, fd in self.fds.items():
            self._setup_trigger(fd, resource)

    def _parse_total(self, data):
        for line in data.split('\n'):
            if line.startswith('some'):
                return int(line.split('total=')[-1])
        return 0

    def _get_pressure_ratio(self, resource, fd):
        now = time.time()
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            data = os.read(fd, 1024).decode()
            print(f"\n{resource} data: {data.strip()}")
        except OSError as e:
            print(f"\n{resource} read error: {e}")
            return 0.0
        current_total = self._parse_total(data)

        if resource not in self.last_total:
            self.last_total[resource] = (now, current_total)
            return 0.0

        last_time, last_total = self.last_total[resource]
        time_delta = now - last_time
        total_delta = current_total - last_total

        ratio = (total_delta / 1_000_000) / time_delta if time_delta > 0 else 0.0
        ratio = max(0.0, min(ratio, 1.0))

        self.last_total[resource] = (now, current_total)
        self.last_pressure[resource] = ratio
        return ratio

    def _clean_old_data(self):
        cutoff = time.time() - self.WINDOW_SECS
        for resource in self.pressure_history:
            self.pressure_history[resource] = [
                (t, p) for t, p in self.pressure_history[resource] if t >= cutoff
            ]
            # Fill with last known value to avoid sudden zero when window is empty
            if not self.pressure_history[resource] and self.last_pressure[resource] > 0:
                self.pressure_history[resource].append((cutoff + 0.1, self.last_pressure[resource]))

    def _window_average(self, resource):
        history = self.pressure_history[resource]
        return sum(p for _, p in history) / len(history) if history else 0.0

    def calculate_score(self):
        """Weighted pressure score calculation."""
        cpu_avg = self._window_average('cpu')
        mem_avg = self._window_average('memory')
        io_avg = self._window_average('io')

        # Sum weighted scores without normalizing by weight sum to amplify range
        total = (cpu_avg * self.WEIGHTS[0] +
                 mem_avg * self.WEIGHTS[1] +
                 io_avg * self.WEIGHTS[2])

        print(f"total... ={total}, cpu_avg={cpu_avg}, mem_avg={mem_avg}, io_avg={io_avg}")
        # Clamp to [0, 1]
        return min(total, 1.0)

    def _get_status(self, score):
        """Map score to status level."""
        if score >= self.STATUS_LEVELS['critical']:
            return 'CRITICAL'
        elif score >= self.STATUS_LEVELS['high']:
            return 'HIGH'
        elif score >= self.STATUS_LEVELS['medium']:
            return 'MEDIUM'
        elif score >= self.STATUS_LEVELS['low']:
            return 'LOW'
        else:
            return 'LOW'

    def run(self):
        poller = select.poll()
        for fd in self.fds.values():
            poller.register(fd, select.POLLPRI)

        try:
            while True:
                events = poller.poll(1000)
                now = time.time()

                for fd, _ in events:
                    resource = next(k for k, v in self.fds.items() if v == fd)
                    ratio = self._get_pressure_ratio(resource, fd)
                    self.pressure_history[resource].append((now, ratio))
                    print(f"\n{resource.upper()} pressure: {ratio:.2f}")

                self._clean_old_data()
                score = self.calculate_score()
                status = self._get_status(score)

                print(f"\r{datetime.datetime.now()} status: {status} | score: {score:.2f} | pressure - "
                      f"CPU: {self._window_average('cpu'):.2f} | "
                      f"MEM: {self._window_average('memory'):.2f} | "
                      f"IO: {self._window_average('io'):.2f}",
                      )

        except KeyboardInterrupt:
            print("\nMonitoring stopped")

    def cleanup(self):
        for fd in self.fds.values():
            os.close(fd)


if __name__ == "__main__":
    monitor = WeightedPSIMonitor()
    try:
        monitor.setup_polling()
        monitor.run()
    finally:
        monitor.cleanup()
