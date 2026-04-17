from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from .data.models import GameBook, WorldSnapshot
from .data.watcher import WorldWatcher
from .services.digest import GameIndexes
from .settings import GameSettings
from .world.tilemap import SceneLayout


DAEMON_MENU = [
    "Summarize linked materials",
    "Guide me through this topic",
    "Show trust and freshness",
    "Trace provenance",
    "Offer a scroll",
]

BOARD_MENU = [
    "View daemon status",
    "View recent log",
    "Run maintenance job",
]

BOARD_MAINTENANCE_MENU = [
    "Run status report",
]


@dataclass(slots=True)
class PlayerState:
    x: float
    y: float
    width: int = 28
    height: int = 28
    speed: float = 260.0


@dataclass(slots=True)
class BrowserItem:
    item_id: str
    kind: str
    label: str
    subtitle: str = ""


@dataclass(slots=True)
class OverlayState:
    mode: str = "none"
    title: str = ""
    body: str = ""
    scroll: int = 0
    menu_index: int = 0
    picker_index: int = 0
    picker_candidates: list[GameBook] = field(default_factory=list)
    browser_items: list[BrowserItem] = field(default_factory=list)
    browser_section_key: str | None = None
    browser_section_label: str = ""
    browser_scroll: int = 0
    browser_cursor: int = 0


@dataclass(slots=True)
class GameRuntime:
    settings: GameSettings
    snapshot: WorldSnapshot
    indexes: GameIndexes
    scenes: dict[str, SceneLayout]
    current_scene: str
    player: PlayerState
    overlay: OverlayState = field(default_factory=OverlayState)
    focused_entry_id: str | None = None
    nearest_object_id: str | None = None
    bookmarks: list[str] = field(default_factory=list)
    status_line: str = "Explore the library. Press Space to inspect, TAB for bookmarks, ESC to quit."
    selected_daemon_target: str | None = None
    highlighted_session_slug: str | None = None
    watcher: WorldWatcher | None = None
    last_reload_poll_at: float = field(default_factory=monotonic)
    player_is_moving: bool = False
    scroll_hold_direction: int = 0
    scroll_hold_elapsed: float = 0.0
    overlay_return: OverlayState | None = None
    anim_tick: float = 0.0
    anim_frame: int = 0
    player_facing: str = "south"
    overlay_slide: float = 0.0
    step_timer: float = 0.0
    ambient_tick: float = 0.0
    assets: object | None = None
    follow_thread_sections: list[str] = field(default_factory=list)
    follow_thread_source: str | None = None
    daemon_state: str = "idle"
    daemon_state_until: float = 0.0
    overlay_live_refreshed_at: float = 0.0
    daemon_socket_state: str = "unconfigured"
    daemon_socket_checked_at: float = 0.0


def switch_scene(runtime: GameRuntime, scene_name: str) -> None:
    runtime.current_scene = scene_name
    scene = runtime.scenes[scene_name]
    runtime.player.x, runtime.player.y = scene.player_start
    runtime.overlay = OverlayState()
    runtime.overlay_slide = 0.0
    runtime.status_line = f"Moved to {scene.name.title()}."
