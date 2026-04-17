from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .runtime import GamePrefs, GameRuntime


@dataclass(slots=True)
class SavedGameState:
    scene: str
    x: float
    y: float
    bookmarks: list[str]


def game_state_dir(workspace_root: Path) -> Path:
    return workspace_root / "brain" / "game"


def bookmarks_path(workspace_root: Path) -> Path:
    return game_state_dir(workspace_root) / "bookmarks.json"


def state_path(workspace_root: Path) -> Path:
    return game_state_dir(workspace_root) / "state.json"


def prefs_path(workspace_root: Path) -> Path:
    return game_state_dir(workspace_root) / "prefs.json"


def load_bookmarks(workspace_root: Path, *, valid_refs: set[str], fallback_refs: list[str] | None = None) -> list[str]:
    refs = _read_bookmarks_file(bookmarks_path(workspace_root))
    if refs is None:
        refs = fallback_refs or []
    return [ref for ref in refs if ref in valid_refs]


def save_bookmarks(workspace_root: Path, bookmarks: list[str]) -> None:
    path = bookmarks_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bookmarks, indent=2) + "\n", encoding="utf-8")


def load_prefs(workspace_root: Path) -> GamePrefs:
    path = prefs_path(workspace_root)
    if not path.exists():
        return GamePrefs()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return GamePrefs()
    if not isinstance(payload, dict):
        return GamePrefs()
    return GamePrefs(onboarding_dismissed=bool(payload.get("onboarding_dismissed", False)))


def save_prefs(workspace_root: Path, prefs: GamePrefs) -> None:
    path = prefs_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "onboarding_dismissed": prefs.onboarding_dismissed,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_saved_state(workspace_root: Path, *, valid_refs: set[str]) -> SavedGameState | None:
    path = state_path(workspace_root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    scene = payload.get("scene")
    x = payload.get("x")
    y = payload.get("y")
    if not isinstance(scene, str) or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    raw_bookmarks = payload.get("bookmarks", [])
    bookmarks = [ref for ref in raw_bookmarks if isinstance(ref, str) and ref in valid_refs]
    return SavedGameState(scene=scene, x=float(x), y=float(y), bookmarks=bookmarks)


def save_runtime_state(runtime: GameRuntime) -> None:
    path = state_path(runtime.settings.workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "scene": runtime.current_scene,
        "x": round(runtime.player.x, 2),
        "y": round(runtime.player.y, 2),
        "bookmarks": list(runtime.bookmarks),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def restore_runtime_state(runtime: GameRuntime) -> bool:
    valid_refs = _valid_refs(runtime)
    saved = load_saved_state(runtime.settings.workspace_root, valid_refs=valid_refs)
    if saved is None:
        return False
    scene = runtime.scenes.get(saved.scene)
    if scene is None:
        return False
    max_x = max(0.0, scene.width - runtime.player.width)
    max_y = max(0.0, scene.height - runtime.player.height)
    if not (0.0 <= saved.x <= max_x and 0.0 <= saved.y <= max_y):
        return False
    runtime.current_scene = saved.scene
    runtime.player.x = saved.x
    runtime.player.y = saved.y
    runtime.bookmarks = saved.bookmarks
    return True


def sync_bookmarks(runtime: GameRuntime) -> bool:
    valid_refs = _valid_refs(runtime)
    filtered = [ref for ref in runtime.bookmarks if ref in valid_refs]
    if filtered == runtime.bookmarks:
        return False
    runtime.bookmarks = filtered
    save_bookmarks(runtime.settings.workspace_root, runtime.bookmarks)
    return True


def _valid_refs(runtime: GameRuntime) -> set[str]:
    return set(runtime.indexes.note_by_id) | set(runtime.indexes.session_by_slug)


def _read_bookmarks_file(path: Path) -> list[str] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, list):
        return [ref for ref in payload if isinstance(ref, str)]
    if isinstance(payload, dict) and isinstance(payload.get("bookmarks"), list):
        return [ref for ref in payload["bookmarks"] if isinstance(ref, str)]
    return None
