from __future__ import annotations

from pathlib import Path
from time import monotonic

from mnemonicos_game.data.models import WorldSnapshot
from second_brain.daemon_status import daemon_reachability, load_daemon_status_report, run_daemon_maintenance_job


def daemon_is_available(workspace_root: Path) -> bool:
    state, _pid = daemon_reachability(workspace_root)
    return state in {"online", "starting"}


def daemon_rect_color(workspace_root: Path) -> tuple[int, int, int]:
    state, _pid = daemon_reachability(workspace_root)
    if state == "online":
        return (165, 125, 75)
    if state == "starting":
        return (184, 146, 86)
    return (95, 95, 95)


def read_daemon_log(workspace_root: Path) -> str:
    path = workspace_root / "brain" / "logs" / "daemon.log"
    if not path.exists():
        return "Daemon log not found."
    lines = path.read_text().splitlines()
    return "\n".join(lines[-20:]) or "Daemon log is empty."


def read_daemon_status_report(workspace_root: Path) -> str:
    return load_daemon_status_report(workspace_root)


def run_daemon_status_report_job(workspace_root: Path) -> str:
    return run_daemon_maintenance_job(workspace_root, "status_report")


def review_slip_count(snapshot: WorldSnapshot) -> int:
    try:
        return snapshot.zone("review").item_count
    except KeyError:
        return 0


def daemon_effective_state(runtime) -> str:
    daemon_state_until = getattr(runtime, "daemon_state_until", 0.0)
    if getattr(runtime, "daemon_state", "idle") != "idle" and daemon_state_until and daemon_state_until > monotonic():
        return runtime.daemon_state
    # Use cached socket state — refreshed once every 5 s by maybe_refresh_daemon_socket()
    # rather than calling daemon_reachability() on every frame.
    state = getattr(runtime, "daemon_socket_state", "unconfigured")
    if getattr(runtime, "follow_thread_sections", []):
        return "guiding"
    if review_slip_count(runtime.snapshot):
        return "reviewing"
    if state == "offline" or state == "unconfigured":
        return "dormant"
    if state == "starting":
        return "starting"
    return "idle"


def daemon_status_line(
    workspace_root: Path,
    snapshot: WorldSnapshot,
    focused_entry_title: str | None = None,
    *,
    socket_state: str | None = None,
) -> str:
    if socket_state is not None:
        state = socket_state
    else:
        state, _pid = daemon_reachability(workspace_root)
    if state == "offline" or state == "unconfigured":
        return "Dormant"
    review_count = review_slip_count(snapshot)
    if focused_entry_title:
        return f"Consulting: {focused_entry_title}"
    if review_count:
        return f"Reviewing {review_count} slips"
    if state == "starting":
        return "Starting up"
    return "Maintaining the stacks"


def daemon_state_label(runtime, default_line: str) -> str:
    effective_state = daemon_effective_state(runtime)
    if effective_state == "dormant":
        return "Daemon dormant"
    if effective_state == "starting":
        return "Daemon starting"
    if effective_state == "sorting":
        return "Sorting fresh material"
    if effective_state == "guiding":
        return "Tracing a thread"
    if effective_state == "consulting":
        return "Welcoming a visitor"
    if effective_state == "reviewing":
        return "Reviewing unresolved slips"
    if effective_state == "reloading":
        return "Re-shelving the stacks"
    return default_line
