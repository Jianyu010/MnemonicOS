from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from .config import default_config_path, load_config


def socket_send(socket_path: os.PathLike[str] | str, payload: dict[str, object]) -> dict[str, object] | None:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2.0)
            client.connect(str(socket_path))
            client.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            response = b""
            while b"\n" not in response:
                chunk = client.recv(4096)
                if not chunk:
                    break
                response += chunk
        return json.loads(response.decode("utf-8").strip() or "{}")
    except OSError:
        return None


def read_pid(pid_path: os.PathLike[str] | str) -> int | None:
    try:
        with open(pid_path, encoding="utf-8") as handle:
            raw = handle.read().strip()
    except FileNotFoundError:
        return None
    return int(raw) if raw else None


def pid_is_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def daemon_reachability(workspace_root: Path) -> tuple[str, int | None]:
    try:
        config = load_config(default_config_path(workspace_root), workspace_root)
    except FileNotFoundError:
        legacy_pid = workspace_root / "brain" / "data" / "mnemonic.pid"
        legacy_sock = workspace_root / "brain" / "data" / "mnemonic.sock"
        pid = read_pid(legacy_pid)
        if legacy_sock.exists():
            return ("starting", pid)
        if pid is not None:
            return ("starting", pid)
        return ("unconfigured", None)
    if config.daemon is None:
        return ("unconfigured", None)
    response = socket_send(config.daemon.socket_path, {"cmd": "status"})
    pid = read_pid(config.daemon.pid_path)
    if response is not None:
        return ("online", pid)
    if config.daemon.socket_path.exists() or pid is not None or pid_is_alive(pid):
        return ("starting", pid)
    return ("offline", pid)


def format_bytes(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "unknown"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num_bytes)
    unit = units[0]
    for candidate in units:
        unit = candidate
        if value < 1024.0 or candidate == units[-1]:
            break
        value /= 1024.0
    if unit == "B":
        return f"{int(value)} {unit}"
    return f"{value:.1f} {unit}"


def format_minutes(age_minutes: int | None) -> str:
    if age_minutes is None:
        return "none"
    if age_minutes < 60:
        return f"{age_minutes} min"
    hours, minutes = divmod(age_minutes, 60)
    if hours < 24:
        if minutes == 0:
            return f"{hours} hr"
        return f"{hours} hr {minutes} min"
    days, hours = divmod(hours, 24)
    if hours == 0:
        return f"{days} d"
    return f"{days} d {hours} hr"


def daemon_status_summary(response: dict[str, object]) -> str:
    status = str(response.get("status") or "unknown")
    service = response.get("service") if isinstance(response.get("service"), dict) else {}
    health = response.get("health") if isinstance(response.get("health"), dict) else {}
    queue = health.get("queue") if isinstance(health, dict) and isinstance(health.get("queue"), dict) else {}
    storage = health.get("storage") if isinstance(health, dict) and isinstance(health.get("storage"), dict) else {}
    recent_runs = health.get("recent_job_runs") if isinstance(health, dict) and isinstance(health.get("recent_job_runs"), list) else []
    scheduled_jobs = service.get("scheduled_jobs") if isinstance(service, dict) and isinstance(service.get("scheduled_jobs"), list) else []

    pending_count = int(queue.get("pending_count", 0) or 0) if isinstance(queue, dict) else 0
    error_count = int(queue.get("error_count", 0) or 0) if isinstance(queue, dict) else 0
    oldest_age = queue.get("oldest_pending_age_minutes") if isinstance(queue, dict) else None
    running_job = service.get("running_job") if isinstance(service, dict) else None
    warnings = queue.get("warnings") if isinstance(queue, dict) and isinstance(queue.get("warnings"), list) else []
    db_bytes = storage.get("db_bytes") if isinstance(storage, dict) else None
    vault_bytes = storage.get("vault_bytes") if isinstance(storage, dict) else None
    vectors_bytes = storage.get("vectors_bytes") if isinstance(storage, dict) else None
    graph_bytes = storage.get("graph_bytes") if isinstance(storage, dict) else None

    next_promotion_run = None
    if isinstance(scheduled_jobs, list):
        for item in scheduled_jobs:
            if isinstance(item, dict) and item.get("name") == "process_promotion_queue":
                next_promotion_run = item.get("next_run_utc")
                break

    last_job_line = "none"
    if isinstance(recent_runs, list) and recent_runs:
        first = recent_runs[0]
        if isinstance(first, dict):
            last_job_line = f"{first.get('job_name', 'unknown')} ({first.get('status', 'unknown')}) at {first.get('finished_at', 'unknown')}"

    lines = [
        f"Daemon: {status}",
        f"Running job: {running_job or 'idle'}",
        "",
        "Promotion Queue",
        f"  pending: {pending_count} item(s)",
        f"  errors: {error_count} item(s)",
        f"  oldest pending age: {format_minutes(oldest_age if isinstance(oldest_age, int) else None)}",
        f"  next promotion run: {next_promotion_run or 'unknown'}",
        "",
        "Storage",
        f"  db: {format_bytes(db_bytes if isinstance(db_bytes, int) else None)}",
        f"  vault: {format_bytes(vault_bytes if isinstance(vault_bytes, int) else None)}",
        f"  vectors: {format_bytes(vectors_bytes if isinstance(vectors_bytes, int) else None)}",
        f"  graph: {format_bytes(graph_bytes if isinstance(graph_bytes, int) else None)}",
        "",
        "Recent Activity",
        f"  last completed job: {last_job_line}",
    ]

    if isinstance(warnings, list) and warnings:
        lines.extend(["", "Warnings"])
        for warning in warnings:
            lines.append(f"  - {warning}")

    return "\n".join(lines)


def daemon_unreachable_summary(pid: int | None) -> str:
    return "\n".join(
        [
            "Daemon: unreachable",
            f"PID file: {pid if pid is not None else 'none'}",
        ]
    )


def daemon_reachability_summary(workspace_root: Path) -> str:
    state, pid = daemon_reachability(workspace_root)
    if state == "online":
        return f"Daemon: online\nPID file: {pid if pid is not None else 'none'}"
    if state == "starting":
        return f"Daemon: starting\nPID file: {pid if pid is not None else 'none'}"
    if state == "offline":
        return daemon_unreachable_summary(pid)
    return "Daemon status is unavailable because no MnemonicOS config file was found."


def load_daemon_status_report(workspace_root: Path) -> str:
    try:
        config = load_config(default_config_path(workspace_root), workspace_root)
    except FileNotFoundError:
        return "Daemon status is unavailable because no MnemonicOS config file was found."
    if config.daemon is None:
        return "Daemon status is unavailable because daemon configuration is missing."
    response = socket_send(config.daemon.socket_path, {"cmd": "status"})
    if response is None:
        return daemon_unreachable_summary(read_pid(config.daemon.pid_path))
    return daemon_status_summary(response)


def run_daemon_maintenance_job(workspace_root: Path, job_name: str) -> str:
    try:
        config = load_config(default_config_path(workspace_root), workspace_root)
    except FileNotFoundError:
        return "Daemon maintenance is unavailable because no MnemonicOS config file was found."
    if config.daemon is None:
        return "Daemon maintenance is unavailable because daemon configuration is missing."
    response = socket_send(config.daemon.socket_path, {"cmd": "run-job", "job": job_name})
    if response is None:
        return daemon_unreachable_summary(read_pid(config.daemon.pid_path))
    status = str(response.get("status") or "unknown")
    if status == "ok":
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        stats = result.get("stats") if isinstance(result, dict) else {}
        lines = [
            f"Maintenance job: {job_name}",
            f"Status: {result.get('status', 'ok') if isinstance(result, dict) else 'ok'}",
        ]
        if isinstance(stats, dict) and stats:
            lines.extend(["", "Stats"])
            for key, value in sorted(stats.items()):
                lines.append(f"  {key}: {value}")
        return "\n".join(lines)
    if status == "busy":
        return f"Maintenance job: {job_name}\nStatus: busy\nRunning job: {response.get('running_job', 'unknown')}"
    return f"Maintenance job: {job_name}\nStatus: {status}\nError: {response.get('error', 'unknown')}"
