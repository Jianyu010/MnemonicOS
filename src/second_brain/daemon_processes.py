from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import signal
import subprocess
import threading
import time
from typing import Any

_log = logging.getLogger("mnemonicos.daemon.processes")


@dataclass(slots=True)
class RegisteredProcess:
    process: subprocess.Popen[Any]
    label: str
    process_group: bool


class DaemonProcessRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: dict[int, RegisteredProcess] = {}

    def register(self, process: subprocess.Popen[Any], *, label: str, process_group: bool = False) -> None:
        with self._lock:
            self._processes[process.pid] = RegisteredProcess(process, label, process_group)

    def unregister(self, process: subprocess.Popen[Any]) -> None:
        with self._lock:
            self._processes.pop(process.pid, None)

    def running(self) -> list[RegisteredProcess]:
        with self._lock:
            rows = list(self._processes.values())
        return [row for row in rows if row.process.poll() is None]

    def stop_all(self, *, grace_seconds: float = 5.0, kill_seconds: float = 5.0) -> bool:
        running = self.running()
        for row in running:
            _send(row, signal.SIGTERM)
        _wait_until_stopped(running, grace_seconds)

        remaining = [row for row in running if row.process.poll() is None]
        for row in remaining:
            _send(row, signal.SIGKILL)
        _wait_until_stopped(remaining, kill_seconds)

        with self._lock:
            for pid in [pid for pid, row in self._processes.items() if row.process.poll() is not None]:
                self._processes.pop(pid, None)
        return not self.running()


def _send(row: RegisteredProcess, sig: signal.Signals) -> None:
    try:
        if row.process_group:
            os.killpg(row.process.pid, sig)
        else:
            row.process.send_signal(sig)
    except ProcessLookupError:
        return
    except OSError as exc:
        _log.warning("failed to signal %s pid=%s: %s", row.label, row.process.pid, exc)


def _wait_until_stopped(rows: list[RegisteredProcess], timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if all(row.process.poll() is not None for row in rows):
            return
        time.sleep(0.05)
