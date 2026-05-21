from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
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


def _format_lane_progress(lane: dict) -> str:
    """One-line summary of a single dual-lane extraction progress dict."""
    name = str(lane.get("item_name") or "—")
    phase = str(lane.get("phase") or "")
    item_index = int(lane.get("item_index") or 0)
    total_items = int(lane.get("total_items") or 0)
    window = int(lane.get("window") or 0)
    total = int(lane.get("total_windows") or 0)
    artifact = int(lane.get("artifact") or 0)
    total_artifacts = int(lane.get("total_artifacts") or 0)
    item_part = f"  item {item_index + 1}/{total_items}" if total_items > 0 else ""
    artifact_part = f"  artifact {artifact}/{total_artifacts}" if total_artifacts > 0 else ""
    if total > 0:
        if phase in {"llm", "vlm"} and window < total:
            active_window = min(total, window + 1)
            return f"{name}{item_part}  window {active_window}/{total} active{artifact_part}  {phase}"
        return f"{name}{item_part}  window {window}/{total}{artifact_part}  {phase}"
    return f"{name}{item_part}{artifact_part}  {phase}" if phase else f"{name}{item_part}{artifact_part}"


def daemon_status_summary(response: dict[str, object]) -> str:
    status = str(response.get("status") or "unknown")
    service = response.get("service") if isinstance(response.get("service"), dict) else {}
    health = response.get("health") if isinstance(response.get("health"), dict) else {}
    queue = health.get("queue") if isinstance(health, dict) and isinstance(health.get("queue"), dict) else {}
    promo = health.get("promotion") if isinstance(health, dict) and isinstance(health.get("promotion"), dict) else {}
    storage = health.get("storage") if isinstance(health, dict) and isinstance(health.get("storage"), dict) else {}
    recent_runs = health.get("recent_job_runs") if isinstance(health, dict) and isinstance(health.get("recent_job_runs"), list) else []
    scheduled_jobs = service.get("scheduled_jobs") if isinstance(service, dict) and isinstance(service.get("scheduled_jobs"), list) else []
    pending_count = int(queue.get("pending_count", 0) or 0) if isinstance(queue, dict) else 0
    error_count = int(queue.get("error_count", 0) or 0) if isinstance(queue, dict) else 0
    oldest_age = queue.get("oldest_pending_age_minutes") if isinstance(queue, dict) else None
    running_job = service.get("running_job") if isinstance(service, dict) else None
    job_progress = service.get("job_progress") if isinstance(service, dict) and isinstance(service.get("job_progress"), dict) else {}
    warnings = queue.get("warnings") if isinstance(queue, dict) and isinstance(queue.get("warnings"), list) else []
    db_bytes = storage.get("db_bytes") if isinstance(storage, dict) else None
    vault_bytes = storage.get("vault_bytes") if isinstance(storage, dict) else None
    note_vectors_count = storage.get("note_vectors_count") if isinstance(storage, dict) else None
    chunk_vectors_count = storage.get("chunk_vectors_count") if isinstance(storage, dict) else None
    graph_nodes_count = storage.get("graph_nodes_count") if isinstance(storage, dict) else None
    graph_edges_count = storage.get("graph_edges_count") if isinstance(storage, dict) else None
    next_extraction_run = None
    next_promotion_run = None
    if isinstance(scheduled_jobs, list):
        for item in scheduled_jobs:
            if isinstance(item, dict):
                if item.get("name") == "process_extraction_queue":
                    next_extraction_run = item.get("next_run_utc")
                elif item.get("name") == "process_promotion_queue":
                    next_promotion_run = item.get("next_run_utc")

    promo_ready = int(promo.get("promotion_ready", 0) or 0) if isinstance(promo, dict) else 0
    promo_promoted = int(promo.get("promoted", 0) or 0) if isinstance(promo, dict) else 0
    promo_review = int(promo.get("review_routed", 0) or 0) if isinstance(promo, dict) else 0
    promo_rejected = int(promo.get("rejected", 0) or 0) if isinstance(promo, dict) else 0
    promo_last_run = str(promo.get("last_run_at") or "never") if isinstance(promo, dict) else "unknown"

    last_job_line = "none"
    if isinstance(recent_runs, list) and recent_runs:
        first = recent_runs[0]
        if isinstance(first, dict):
            last_job_line = f"{first.get('job_name', 'unknown')} ({first.get('status', 'unknown')}) at {first.get('finished_at', 'unknown')}"

    lines = [
        f"Daemon: {status}",
        f"Running job: {running_job or 'idle'}",
    ]

    # Dual-lane extraction progress
    if running_job and isinstance(job_progress, dict) and str(job_progress.get("mode")) == "dual_lane":
        quick_p = job_progress.get("quick") if isinstance(job_progress.get("quick"), dict) else {}
        slow_p = job_progress.get("slow") if isinstance(job_progress.get("slow"), dict) else {}
        lines.append(f"  quick lane: {_format_lane_progress(quick_p)}")
        lines.append(f"  slow lane:  {_format_lane_progress(slow_p)}")

    lines += [
        "",
        "Extraction Queue",
        f"  pending: {pending_count} item(s)",
        f"  errors: {error_count} item(s)",
        f"  oldest pending age: {format_minutes(oldest_age if isinstance(oldest_age, int) else None)}",
        f"  next extraction run: {next_extraction_run or 'unknown'}",
        "",
        "Promotion Queue",
        f"  ready: {promo_ready} item(s)",
        f"  promoted: {promo_promoted}  review: {promo_review}  rejected: {promo_rejected}",
        f"  last run: {promo_last_run}",
        f"  next promotion run: {next_promotion_run or 'unknown'}",
        "",
        "Storage",
        f"  db: {format_bytes(db_bytes if isinstance(db_bytes, int) else None)}",
        f"  vault: {format_bytes(vault_bytes if isinstance(vault_bytes, int) else None)}",
        f"  vectors: {note_vectors_count if isinstance(note_vectors_count, int) else '—'} notes · {chunk_vectors_count if isinstance(chunk_vectors_count, int) else '—'} chunks",
        f"  graph: {graph_nodes_count if isinstance(graph_nodes_count, int) else '—'} nodes · {graph_edges_count if isinstance(graph_edges_count, int) else '—'} edges",
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


# ---------------------------------------------------------------------------
# Live watch display
# ---------------------------------------------------------------------------

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_CYAN = "\033[36m"
_BLUE = "\033[34m"
_CLEAR_SCREEN = "\033[2J\033[H"


def _bar(fraction: float, width: int = 32) -> str:
    filled = min(width, int(fraction * width))
    empty = width - filled
    pct = int(fraction * 100)
    return f"[{_GREEN}{'█' * filled}{_DIM}{'░' * empty}{_RESET}] {pct:3d}%"


def _elapsed(started_at_str: str | None) -> str:
    if not started_at_str:
        return ""
    try:
        started = datetime.fromisoformat(started_at_str).replace(tzinfo=timezone.utc)
        delta = int((datetime.now(timezone.utc) - started).total_seconds())
    except ValueError:
        return ""
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        return f"{delta // 60}m{delta % 60:02d}s"
    return f"{delta // 3600}h{(delta % 3600) // 60:02d}m"


def _elapsed_seconds(started_at_str: str | None) -> int | None:
    if not started_at_str:
        return None
    try:
        started = datetime.fromisoformat(started_at_str).replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    except ValueError:
        return None


def _eta_str(elapsed_seconds: int | None, fraction: float) -> str:
    """Return formatted ETA like 'ETA 4m32s', or '' if not computable."""
    if elapsed_seconds is None or elapsed_seconds < 5 or fraction <= 0.02 or fraction >= 1.0:
        return ""
    remaining = int(elapsed_seconds * (1.0 - fraction) / fraction)
    if remaining < 60:
        return f"ETA {remaining}s"
    if remaining < 3600:
        return f"ETA {remaining // 60}m{remaining % 60:02d}s"
    return f"ETA {remaining // 3600}h{(remaining % 3600) // 60:02d}m"


def _short_archive_id(archive_id: str | None) -> str:
    if not archive_id:
        return "unknown"
    parts = str(archive_id).split("/")
    name = parts[-1] if parts else archive_id
    # strip leading timestamp prefix (YYYYMMDD-HHMMSS-NNNNNN-)
    segments = name.split("-")
    if len(segments) > 3 and segments[0].isdigit() and segments[1].isdigit():
        name = "-".join(segments[3:])
    return name[:52]


_TEXT_ARTIFACT_FORMATS = {"md", "markdown", "txt", "text", "plain_text", "log"}


def _item_lane(item: dict) -> str:
    source_kind = str(item.get("source_kind") or "")
    if source_kind in {"episode", "session"}:
        return "quick"
    source_format = str(item.get("source_format") or "").lstrip(".").lower()
    if source_kind == "artifact" and source_format in _TEXT_ARTIFACT_FORMATS:
        return "quick"
    return "slow"


def _read_progress_file(item_id: object) -> dict:
    try:
        path = Path(tempfile.gettempdir()) / f"mnemo_progress_{int(item_id)}.json"
        if not path.exists():
            return {}
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _processing_item_to_lane_progress(item: dict, *, item_index: int = 0, total_items: int = 0) -> dict:
    progress = _read_progress_file(item.get("id"))
    archive_id = item.get("archive_id")
    name = (
        str(item.get("original_filename") or "")[:52]
        if item.get("original_filename")
        else (_short_archive_id(str(archive_id)) if archive_id else f"episode/{item.get('episode_id', '?')}")
    )
    lane = {
        "item_id": item.get("id"),
        "item_name": name,
        "item_index": item_index,
        "total_items": total_items,
        "phase": progress.get("phase") or "processing",
        "window": progress.get("window") or item.get("completed_window_count") or 0,
        "total_windows": progress.get("total_windows") or item.get("window_count") or 0,
        "artifact": progress.get("artifact") or 0,
        "total_artifacts": progress.get("total_artifacts") or 0,
        "started_at": item.get("started_at"),
    }
    return lane


def _fill_empty_lane_from_processing(
    lane_label: str,
    lane_progress: object,
    processing_items: list[dict],
    represented_ids: set[str],
) -> dict:
    if isinstance(lane_progress, dict) and lane_progress:
        return lane_progress
    lane_items = [
        item for item in processing_items
        if _item_lane(item) == lane_label and str(item.get("id")) not in represented_ids
    ]
    if not lane_items:
        return {}
    return _processing_item_to_lane_progress(lane_items[0], item_index=0, total_items=len(lane_items))


def _query_processing_items(db_path: Path) -> list[dict]:
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT q.id, q.source_kind, q.archive_id, q.conversation_id, q.episode_id,
                   q.started_at, q.attempt_count, q.window_count,
                   q.completed_window_count, q.candidate_count,
                   a.source_format, a.original_filename
            FROM extraction_queue q
            LEFT JOIN archive a ON a.id = q.archive_id
            WHERE q.status='processing'
            ORDER BY q.started_at
            """
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _render_watch(
    response: dict | None,
    processing_items: list[dict],
    now: datetime,
    interval: int,
) -> str:
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    width = min(120, os.get_terminal_size().columns if sys.stdout.isatty() else 80)

    lines: list[str] = []
    lines.append(
        f"{_BOLD}MnemonicOS Daemon{_RESET}  "
        f"{_DIM}{ts}  refresh {interval}s  Ctrl+C to exit{_RESET}"
    )
    lines.append("─" * width)

    if response is None:
        lines.append(f"{_RED}● UNREACHABLE{_RESET}  daemon not responding")
        return "\n".join(lines)

    service = response.get("service") or {}
    health = response.get("health") or {}
    queue = health.get("queue") or {}
    promo = health.get("promotion") or {}
    recent = health.get("recent_job_runs") or []

    running_job = service.get("running_job")
    running_jobs: list[str] = service.get("running_jobs") or ([running_job] if running_job else [])
    job_progress: dict = service.get("job_progress") or {}
    counts = queue.get("queue_counts") or {}
    n_processed = int(counts.get("processed", 0))
    n_pending = int(counts.get("pending", 0))
    n_processing = int(counts.get("processing", 0))
    n_error = int(counts.get("error", 0))
    n_total = n_processed + n_pending + n_processing + n_error
    fraction = n_processed / n_total if n_total > 0 else 0.0

    status_dot = f"{_GREEN}●{_RESET}" if response.get("status") == "ok" else f"{_YELLOW}●{_RESET}"
    if running_jobs:
        job_str = ", ".join(f"{_CYAN}{j}{_RESET}" for j in running_jobs)
    else:
        job_str = f"{_DIM}idle{_RESET}"
    lines.append(f"{status_dot} ONLINE   running: {job_str}")
    lines.append("")

    # Extraction queue
    lines.append(f"{_BOLD}EXTRACTION QUEUE{_RESET}")
    lines.append(
        f"  pending {_YELLOW}{n_pending:4d}{_RESET}  "
        f"processing {_CYAN}{n_processing:2d}{_RESET}  "
        f"error {_RED}{n_error:4d}{_RESET}  "
        f"done {_GREEN}{n_processed:5d}{_RESET}"
    )

    oldest_age = queue.get("oldest_pending_age_minutes")
    if oldest_age is not None:
        lines.append(f"  oldest pending: {format_minutes(int(oldest_age))}")

    # Dual-lane mode: show quick + slow lanes side by side
    is_dual_lane = (
        "process_extraction_queue" in running_jobs
        and isinstance(job_progress, dict)
        and str(job_progress.get("mode")) == "dual_lane"
    )
    if is_dual_lane:
        lines.append(f"  {_BOLD}Mode: dual-lane{_RESET}")
        represented_ids = {
            str(lane.get("item_id"))
            for lane in (job_progress.get("quick"), job_progress.get("slow"))
            if isinstance(lane, dict) and lane.get("item_id") is not None
        }
        for lane_label in ("quick", "slow"):
            lane_p = _fill_empty_lane_from_processing(
                lane_label,
                job_progress.get(lane_label),
                processing_items,
                represented_ids,
            )
            if not lane_p:
                lines.append(f"  {lane_label} lane: {_DIM}idle{_RESET}")
                continue
            name = str(lane_p.get("item_name") or "—")
            phase = str(lane_p.get("phase") or "preparing")
            item_index = int(lane_p.get("item_index") or 0)
            total_items = int(lane_p.get("total_items") or 0)
            window = int(lane_p.get("window") or 0)
            total = int(lane_p.get("total_windows") or 0)
            artifact = int(lane_p.get("artifact") or 0)
            total_artifacts = int(lane_p.get("total_artifacts") or 0)

            # Resolve started_at from lane dict or by matching processing_items by id
            started_at = lane_p.get("started_at")
            if not started_at:
                item_id = lane_p.get("item_id")
                for pi in processing_items:
                    if item_id is not None and pi.get("id") == item_id:
                        started_at = pi.get("started_at")
                        break
            elapsed_secs = _elapsed_seconds(started_at)
            elapsed_str = _elapsed(started_at)
            elapsed_part = f"  {_DIM}{elapsed_str} elapsed{_RESET}" if elapsed_str else ""

            lines.append(
                f"  {_CYAN}{lane_label} lane{_RESET}: {name}  {_DIM}{phase}…{_RESET}{elapsed_part}"
            )
            if total_items > 0:
                item_frac = (item_index + 1) / total_items
                lines.append(
                    f"    item {_CYAN}{item_index + 1}/{total_items}{_RESET}  "
                    f"{_bar(item_frac, 28)}"
                )
            if total_artifacts > 0:
                art_frac = artifact / total_artifacts
                eta = _eta_str(elapsed_secs, art_frac)
                eta_part = f"  {_DIM}{eta}{_RESET}" if eta else ""
                lines.append(
                    f"    artifact {_CYAN}{artifact}/{total_artifacts}{_RESET}  "
                    f"{_bar(art_frac, 28)}  {_DIM}{phase}{_RESET}{eta_part}"
                )
            if total > 0:
                if phase in {"llm", "vlm"} and window < total:
                    active_window = min(total, window + 1)
                    win_frac = (active_window - 1) / total
                    eta = _eta_str(elapsed_secs, win_frac)
                    eta_part = f"  {_DIM}{eta}{_RESET}" if eta else ""
                    lines.append(
                        f"    window {_CYAN}{active_window}/{total}{_RESET}  "
                        f"{_DIM}active {phase}{_RESET}{eta_part}"
                    )
                else:
                    frac = window / total
                    eta = _eta_str(elapsed_secs, frac)
                    eta_part = f"  {_DIM}{eta}{_RESET}" if eta else ""
                    lines.append(
                        f"    window {_CYAN}{window}/{total}{_RESET}  "
                        f"{_bar(frac, 28)}  {_DIM}{phase}{_RESET}{eta_part}"
                    )
    elif processing_items:
        lines.append(f"  {_BOLD}Currently processing:{_RESET}")
        for item in processing_items:
            elapsed = _elapsed(item.get("started_at"))
            archive_id = item.get("archive_id")
            name = _short_archive_id(archive_id) if archive_id else f"episode/{item.get('episode_id', '?')}"
            attempt = item.get("attempt_count") or 1
            # Per-window LLM progress from live job_progress dict
            if job_progress and str(job_progress.get("item_id")) == str(item["id"]):
                jp_window = int(job_progress.get("window") or 0)
                jp_total = int(job_progress.get("total_windows") or 0)
                jp_phase = str(job_progress.get("phase") or "")
                jp_index = int(job_progress.get("item_index") or 0)
                jp_items_total = int(job_progress.get("total_items") or 0)
                elapsed_secs = _elapsed_seconds(item.get("started_at"))
                batch_str = f"  batch {jp_index + 1}/{jp_items_total}" if jp_items_total > 0 else ""
                lines.append(
                    f"    {_CYAN}{name}{_RESET}  attempt={attempt}  "
                    f"elapsed={elapsed if elapsed else '—'}{batch_str}"
                )
                if jp_total > 0:
                    win_frac = jp_window / jp_total
                    eta = _eta_str(elapsed_secs, win_frac)
                    eta_part = f"  {_DIM}{eta}{_RESET}" if eta else ""
                    lines.append(
                        f"    window {_CYAN}{jp_window}/{jp_total}{_RESET}  "
                        f"{_bar(win_frac, 32)}  {_DIM}{jp_phase}{_RESET}{eta_part}"
                    )
                elif jp_phase:
                    lines.append(f"    {_DIM}{jp_phase}…{_RESET}")
            else:
                wins = item.get("window_count") or 0
                cands = item.get("candidate_count") or 0
                lines.append(
                    f"    {_CYAN}{name}{_RESET}  "
                    f"windows={wins}  candidates={cands}  attempt={attempt}  "
                    f"elapsed={elapsed if elapsed else '—'}"
                )
    elif running_job == "process_extraction_queue":
        if job_progress:
            jp_index = int(job_progress.get("item_index") or 0)
            jp_total = int(job_progress.get("total_items") or 0)
            jp_name = str(job_progress.get("item_name") or "")
            jp_phase = str(job_progress.get("phase") or "")
            lines.append(
                f"  item {jp_index + 1}/{jp_total}  {_CYAN}{jp_name}{_RESET}  "
                f"{_DIM}{jp_phase}…{_RESET}"
            )
        else:
            lines.append(f"  {_DIM}(fetching item…){_RESET}")

    lines.append("")

    # Promotion
    promo_ready = int(promo.get("promotion_ready") or 0)
    promo_promoted = int(promo.get("promoted") or 0)
    promo_review = int(promo.get("review_routed") or 0)
    promo_last = str(promo.get("last_run_at") or "never")
    lines.append(f"{_BOLD}PROMOTION{_RESET}")
    lines.append(
        f"  ready {_YELLOW}{promo_ready:3d}{_RESET}  "
        f"promoted {_GREEN}{promo_promoted:5d}{_RESET}  "
        f"review {promo_review}  "
        f"last run: {_DIM}{promo_last}{_RESET}"
    )
    lines.append("")

    # Recent jobs
    lines.append(f"{_BOLD}RECENT JOBS{_RESET}")
    for run in recent[:6]:
        name = str(run.get("job_name") or "unknown")
        status = str(run.get("status") or "?")
        finished = str(run.get("finished_at") or "—")
        if status == "ok":
            icon = f"{_GREEN}✓{_RESET}"
        elif status == "error":
            icon = f"{_RED}✗{_RESET}"
        else:
            icon = f"{_YELLOW}…{_RESET}"
        lines.append(f"  {icon}  {name:<38}  {_DIM}{finished}{_RESET}")

    return "\n".join(lines)


def watch_daemon(config_path: str, interval: int = 3) -> None:
    from .config import load_config as _load
    config = _load(config_path)
    if config.daemon is None:
        print("Daemon configuration missing.")
        return

    try:
        while True:
            now = datetime.now(timezone.utc).astimezone()
            response = socket_send(config.daemon.socket_path, {"cmd": "status"})
            processing = _query_processing_items(config.paths.db_path)
            frame = _render_watch(response, processing, now, interval)
            sys.stdout.write(_CLEAR_SCREEN + frame + "\n")
            sys.stdout.flush()
            time.sleep(interval)
    except (KeyboardInterrupt, SystemExit):
        sys.stdout.write("\n")
