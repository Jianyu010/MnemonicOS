from __future__ import annotations

import argparse
import sys
from time import monotonic
from pathlib import Path
from datetime import datetime

from second_brain.config import default_config_path, load_config
from second_brain.review import slugify

from .assets import load_assets
from .assets.loader import SKIN_CLOAK_PRESETS, SKIN_TUNIC_PRESETS
from .data.loader import BrainLoader
from .data.models import GameBook, GameEntry, WorldSnapshot
from .data.watcher import WorldWatcher
from .engine import AnimationClock, SoundManager, ease_out_quad
from .entities.daemon_npc import read_daemon_log, read_daemon_status_report, run_daemon_status_report_job
from .entities.marker import entry_color, freshness_label
from .persistence import load_bookmarks, load_prefs, restore_runtime_state, save_bookmarks, save_prefs, save_runtime_state, sync_bookmarks
from .runtime import BOARD_MAINTENANCE_MENU, BOARD_MENU, BrowserItem, COUNTER_MENU, DAEMON_MENU, GameRuntime, INTAKE_TYPES, IntakeDraft, OverlayState, PlayerState, switch_scene
from .scenes import DungeonScene, LibraryScene
from .services.digest import build_guidance, build_indexes, build_linked_digest, build_provenance, resolve_jump_candidates
from .settings import GameSettings
from .ui.markdown_renderer import render_lines
from .world.routing import related_note_ids, related_section_keys
from .world.tilemap import build_dungeon_layout, build_library_layout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MenomicOS")
    parser.add_argument("--root", type=Path, default=None, help="Workspace root containing brain/")
    parser.add_argument("--dump-world", action="store_true", help="Print a world summary and exit")
    parser.add_argument("--check", action="store_true", help="Validate the game data wiring and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = GameSettings(workspace_root=_resolve_game_root(args.root))
    snapshot = BrainLoader(settings.workspace_root).load()

    if args.dump_world or args.check:
        print(format_summary(snapshot))
        return 0

    return run_game(settings, snapshot)


def _resolve_game_root(explicit_root: Path | None) -> Path:
    if explicit_root is not None:
        return explicit_root.resolve()
    try:
        config_path = default_config_path()
        return load_config(config_path).paths.workspace_root.resolve()
    except Exception:
        return Path.cwd().resolve()


def run_game(settings: GameSettings, snapshot: WorldSnapshot) -> int:
    try:
        import pygame
    except ModuleNotFoundError:
        print(
            "pygame-ce is required to launch the MenomicOS window.\n"
            "Install project dependencies, then rerun `mnemonicos-game`.",
            file=sys.stderr,
        )
        return 1

    scenes = {
        "library": LibraryScene(),
        "vault": DungeonScene(),
    }
    layouts = {
        "library": build_library_layout(snapshot),
        "vault": build_dungeon_layout(snapshot),
    }
    runtime = GameRuntime(
        settings=settings,
        snapshot=snapshot,
        indexes=build_indexes(snapshot),
        scenes=layouts,
        current_scene="library",
        player=PlayerState(*layouts["library"].player_start),
        watcher=WorldWatcher(settings.workspace_root),
    )
    runtime.prefs = load_prefs(settings.workspace_root)
    valid_refs = set(runtime.indexes.note_by_id) | set(runtime.indexes.session_by_slug)
    saved_state = restore_runtime_state(runtime)
    runtime.bookmarks = load_bookmarks(
        settings.workspace_root,
        valid_refs=valid_refs,
        fallback_refs=list(runtime.bookmarks if saved_state else []),
    )
    if not runtime.prefs.onboarding_dismissed:
        runtime.overlay = build_onboarding_overlay()
        runtime.overlay_slide = 1.0
        runtime.status_line = "Welcome to MenomicOS."
    maybe_refresh_daemon_socket(runtime, force=True)
    if runtime.watcher is not None:
        runtime.watcher.prime()

    pygame.init()
    try:
        screen = pygame.display.set_mode((settings.window_width, settings.window_height))
        pygame.display.set_caption(settings.title)
        runtime.assets = load_assets(settings.workspace_root, pygame)
        animation_clock = AnimationClock()
        sound_manager = SoundManager(pygame, runtime.assets)
        sound_manager.music_start(runtime.current_scene)
        font, small_font, tiny_font = build_ui_fonts(settings, pygame)
        clock = pygame.time.Clock()
        running = True
        save_on_exit = False
        while running:
            dt = clock.tick(settings.fps) / 1000.0
            animation_clock.update(runtime, dt)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE and runtime.overlay.mode == "none":
                        save_on_exit = True
                        running = False
                    else:
                        handle_keydown(runtime, event.key, pygame, sound_manager, getattr(event, "unicode", ""))
                if event.type == pygame.MOUSEWHEEL:
                    handle_mouse_wheel(runtime, event.y)
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    handle_mouse_down(runtime, event.pos, pygame)
            update_overlay_hold(runtime, dt, pygame)
            update_overlay_slide(runtime, dt)
            update_portal_flash(runtime, dt)
            maybe_reload_world(runtime)
            maybe_refresh_daemon_socket(runtime)
            maybe_refresh_live_overlay(runtime)
            if runtime.overlay.mode == "none":
                scenes[runtime.current_scene].update(runtime, dt, pygame)
            else:
                runtime.player_is_moving = False
                scenes[runtime.current_scene].update(runtime, 0.0, pygame)
            maybe_show_landmark_hint(runtime)
            update_step_audio(runtime, dt, sound_manager)
            scenes[runtime.current_scene].draw(screen, runtime, font, small_font, tiny_font, pygame)
            if runtime.overlay.mode != "none":
                draw_overlay(screen, runtime, small_font, tiny_font, pygame)
            pygame.display.flip()
        if save_on_exit:
            save_bookmarks(settings.workspace_root, runtime.bookmarks)
            save_runtime_state(runtime)
    finally:
        pygame.quit()
    return 0


def format_summary(snapshot: WorldSnapshot) -> str:
    lines = [
        "MenomicOS",
        f"Workspace: {snapshot.workspace_root}",
        f"Brain root: {snapshot.brain_root}",
        f"Notes: {len(snapshot.note_entries)}",
        f"Sessions: {len(snapshot.session_books)}",
        f"System files: {len(snapshot.system_files)}",
        "Zones:",
    ]
    for zone in snapshot.zones:
        lines.append(f"- {zone.label}: {zone.item_count} items / {len(zone.sections)} sections")
        for section in zone.sections:
            marker = " [under construction]" if section.under_construction else ""
            lines.append(f"  - {section.label}: {section.item_count}{marker}")
    return "\n".join(lines)


def build_ui_fonts(settings: GameSettings, pygame):
    scale = max(0.9, min(1.25, settings.window_height / 720.0))
    title_size = max(28, int(round(32 * scale)))
    small_size = max(22, int(round(24 * scale)))
    tiny_size = max(18, int(round(20 * scale)))
    return (
        pygame.font.Font(None, title_size),
        pygame.font.Font(None, small_size),
        pygame.font.Font(None, tiny_size),
    )


def handle_keydown(runtime: GameRuntime, key: int, pygame, sound_manager: SoundManager | None = None, text: str = "") -> None:
    if runtime.overlay.mode == "skin-picker":
        handle_skin_picker_keys(runtime, key, pygame, sound_manager)
        return
    if runtime.overlay.mode == "onboarding":
        dismiss_onboarding(runtime)
        if sound_manager is not None:
            sound_manager.play("inspect_close")
        return
    if runtime.overlay.mode == "counter-menu" and key == pygame.K_ESCAPE:
        close_counter_overlay(runtime)
        if sound_manager is not None:
            sound_manager.play("inspect_close")
        return
    if runtime.overlay.mode == "counter-links" and key == pygame.K_ESCAPE:
        runtime.overlay = build_counter_menu_overlay(runtime)
        runtime.status_line = "The daemon waits for the next instruction."
        if sound_manager is not None:
            sound_manager.play("inspect_close")
        return
    if runtime.overlay.mode == "none" and key == pygame.K_f and runtime.follow_thread_source:
        clear_follow_thread(runtime)
        if sound_manager is not None:
            sound_manager.play("inspect_close")
        return
    if runtime.overlay.mode == "intake":
        handled = handle_intake_keys(runtime, key, pygame, sound_manager, text)
        if handled:
            return
    if key == pygame.K_ESCAPE:
        if runtime.overlay_return is not None:
            runtime.overlay = runtime.overlay_return
            runtime.overlay_return = None
            runtime.status_line = "Returned."
            runtime.overlay_slide = 1.0
        else:
            runtime.overlay = OverlayState()
            runtime.status_line = "Closed overlay."
        if sound_manager is not None:
            sound_manager.play("inspect_close")
        return
    if runtime.overlay.mode == "inspect":
        handle_inspector_keys(runtime, key, pygame, sound_manager)
        return
    if runtime.overlay.mode == "bookmarks":
        items = runtime.overlay.browser_items
        if key == pygame.K_TAB:
            runtime.overlay = OverlayState()
        elif key in (pygame.K_DOWN, pygame.K_s) and items:
            runtime.overlay.browser_cursor = min(runtime.overlay.browser_cursor + 1, len(items) - 1)
            if runtime.overlay.browser_cursor >= runtime.overlay.browser_scroll + _shelf_page_size_for_runtime(runtime):
                runtime.overlay.browser_scroll += 1
            if sound_manager is not None:
                sound_manager.play("menu_move")
        elif key in (pygame.K_UP, pygame.K_w) and items:
            runtime.overlay.browser_cursor = max(runtime.overlay.browser_cursor - 1, 0)
            if runtime.overlay.browser_cursor < runtime.overlay.browser_scroll:
                runtime.overlay.browser_scroll = runtime.overlay.browser_cursor
            if sound_manager is not None:
                sound_manager.play("menu_move")
        elif key in (pygame.K_RETURN, pygame.K_SPACE) and items:
            open_bookmark_item(runtime, items[runtime.overlay.browser_cursor])
            if sound_manager is not None:
                sound_manager.play("menu_confirm")
        elif key in (pygame.K_d, pygame.K_DELETE, pygame.K_BACKSPACE) and items:
            bookmark = items[runtime.overlay.browser_cursor].item_id
            remove_bookmark(runtime, bookmark)
            runtime.overlay = build_bookmarks_overlay(runtime)
            if sound_manager is not None:
                sound_manager.play("inspect_close")
        return
    if runtime.overlay.mode == "counter-menu":
        if key in (pygame.K_DOWN, pygame.K_s):
            runtime.overlay.menu_index = (runtime.overlay.menu_index + 1) % len(COUNTER_MENU)
            if sound_manager is not None:
                sound_manager.play("menu_move")
        elif key in (pygame.K_UP, pygame.K_w):
            runtime.overlay.menu_index = (runtime.overlay.menu_index - 1) % len(COUNTER_MENU)
            if sound_manager is not None:
                sound_manager.play("menu_move")
        elif key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_b):
            run_counter_action(runtime)
            if sound_manager is not None:
                sound_manager.play("menu_confirm")
        return
    if runtime.overlay.mode == "counter-links":
        items = runtime.overlay.browser_items
        n = len(items)
        _panel_h = runtime.settings.window_height - 160
        _page = max(1, (_panel_h - 120) // 62)
        if key in (pygame.K_DOWN, pygame.K_s) and n:
            runtime.overlay.browser_cursor = min(runtime.overlay.browser_cursor + 1, n - 1)
            if runtime.overlay.browser_cursor >= runtime.overlay.browser_scroll + _page:
                runtime.overlay.browser_scroll += 1
            if sound_manager is not None:
                sound_manager.play("menu_move")
        elif key in (pygame.K_UP, pygame.K_w) and n:
            runtime.overlay.browser_cursor = max(runtime.overlay.browser_cursor - 1, 0)
            if runtime.overlay.browser_cursor < runtime.overlay.browser_scroll:
                runtime.overlay.browser_scroll = runtime.overlay.browser_cursor
            if sound_manager is not None:
                sound_manager.play("menu_move")
        elif key in (pygame.K_RETURN, pygame.K_SPACE) and n:
            _open_browser_item(runtime, items[runtime.overlay.browser_cursor])
            if sound_manager is not None:
                sound_manager.play("menu_confirm")
        elif key == pygame.K_b:
            runtime.overlay = build_counter_menu_overlay(runtime)
            runtime.status_line = "The daemon is ready to act on the carried scroll."
            if sound_manager is not None:
                sound_manager.play("daemon_greet")
        return
    if runtime.overlay.mode == "shelf":
        items = runtime.overlay.browser_items
        n = len(items)
        _panel_h = runtime.settings.window_height - 160
        _page = max(1, (_panel_h - 120) // 62)
        if key in (pygame.K_DOWN, pygame.K_s) and n:
            runtime.overlay.browser_cursor = min(runtime.overlay.browser_cursor + 1, n - 1)
            if runtime.overlay.browser_cursor >= runtime.overlay.browser_scroll + _page:
                runtime.overlay.browser_scroll += 1
            if sound_manager is not None:
                sound_manager.play("menu_move")
        elif key in (pygame.K_UP, pygame.K_w) and n:
            runtime.overlay.browser_cursor = max(runtime.overlay.browser_cursor - 1, 0)
            if runtime.overlay.browser_cursor < runtime.overlay.browser_scroll:
                runtime.overlay.browser_scroll = runtime.overlay.browser_cursor
            if sound_manager is not None:
                sound_manager.play("menu_move")
        elif key in (pygame.K_RETURN, pygame.K_SPACE) and n:
            _open_browser_item(runtime, items[runtime.overlay.browser_cursor])
            if sound_manager is not None:
                sound_manager.play("menu_confirm")
        return
    if runtime.overlay.mode == "daemon":
        if key in (pygame.K_DOWN, pygame.K_s):
            runtime.overlay.menu_index = (runtime.overlay.menu_index + 1) % len(DAEMON_MENU)
            if sound_manager is not None:
                sound_manager.play("menu_move")
        elif key in (pygame.K_UP, pygame.K_w):
            runtime.overlay.menu_index = (runtime.overlay.menu_index - 1) % len(DAEMON_MENU)
            if sound_manager is not None:
                sound_manager.play("menu_move")
        elif key in (pygame.K_RETURN, pygame.K_SPACE):
            run_daemon_action(runtime)
            if sound_manager is not None:
                sound_manager.play("menu_confirm")
        return
    if runtime.overlay.mode == "board":
        if key in (pygame.K_DOWN, pygame.K_s):
            runtime.overlay.menu_index = (runtime.overlay.menu_index + 1) % len(BOARD_MENU)
            if sound_manager is not None:
                sound_manager.play("menu_move")
        elif key in (pygame.K_UP, pygame.K_w):
            runtime.overlay.menu_index = (runtime.overlay.menu_index - 1) % len(BOARD_MENU)
            if sound_manager is not None:
                sound_manager.play("menu_move")
        elif key in (pygame.K_RETURN, pygame.K_SPACE):
            run_board_action(runtime)
            if sound_manager is not None:
                sound_manager.play("menu_confirm")
        return
    if runtime.overlay.mode == "board-maintenance":
        if key in (pygame.K_DOWN, pygame.K_s):
            runtime.overlay.menu_index = (runtime.overlay.menu_index + 1) % len(BOARD_MAINTENANCE_MENU)
            if sound_manager is not None:
                sound_manager.play("menu_move")
        elif key in (pygame.K_UP, pygame.K_w):
            runtime.overlay.menu_index = (runtime.overlay.menu_index - 1) % len(BOARD_MAINTENANCE_MENU)
            if sound_manager is not None:
                sound_manager.play("menu_move")
        elif key in (pygame.K_RETURN, pygame.K_SPACE):
            run_board_maintenance_action(runtime)
            if sound_manager is not None:
                sound_manager.play("menu_confirm")
        return
    if runtime.overlay.mode == "picker":
        if key in (pygame.K_DOWN, pygame.K_s) and runtime.overlay.picker_candidates:
            runtime.overlay.picker_index = (runtime.overlay.picker_index + 1) % len(runtime.overlay.picker_candidates)
            if sound_manager is not None:
                sound_manager.play("menu_move")
        elif key in (pygame.K_UP, pygame.K_w) and runtime.overlay.picker_candidates:
            runtime.overlay.picker_index = (runtime.overlay.picker_index - 1) % len(runtime.overlay.picker_candidates)
            if sound_manager is not None:
                sound_manager.play("menu_move")
        elif key in (pygame.K_RETURN, pygame.K_SPACE) and runtime.overlay.picker_candidates:
            jump_to_session(runtime, runtime.overlay.picker_candidates[runtime.overlay.picker_index])
            if sound_manager is not None:
                sound_manager.play("menu_confirm")
        return
    if key == pygame.K_p and runtime.overlay.mode == "none":
        open_skin_picker(runtime)
        return
    if key == pygame.K_SPACE:
        interact(runtime, sound_manager)
    elif key == pygame.K_b and runtime.overlay.mode == "none" and runtime.nearest_object_id == "daemon:counter" and (runtime.carried_entry_id or runtime.carried_session_slug):
        runtime.overlay = build_counter_menu_overlay(runtime)
        runtime.status_line = f"Handover complete: {carried_item_label(runtime)} rests on the counter."
        set_daemon_state(runtime, "consulting", 4.0)
        if sound_manager is not None:
            sound_manager.play("daemon_greet")
    elif key == pygame.K_TAB:
        runtime.overlay = build_bookmarks_overlay(runtime)


def handle_inspector_keys(runtime: GameRuntime, key: int, pygame, sound_manager: SoundManager | None = None) -> None:
    if key in (pygame.K_DOWN, pygame.K_s):
        runtime.overlay.scroll += 1
        runtime.scroll_hold_direction = 1
        runtime.scroll_hold_elapsed = 0.0
    elif key in (pygame.K_UP, pygame.K_w):
        runtime.overlay.scroll = max(0, runtime.overlay.scroll - 1)
        runtime.scroll_hold_direction = -1
        runtime.scroll_hold_elapsed = 0.0
    elif key == pygame.K_b and (runtime.focused_entry_id or runtime.focused_session_slug):
        carry_current_item(runtime)
        if sound_manager is not None:
            sound_manager.play("bookmark")
    elif key == pygame.K_j and runtime.focused_entry_id:
        entry = runtime.indexes.note_by_id.get(runtime.focused_entry_id)
        if entry is not None:
            candidates, reason = resolve_jump_candidates(entry, runtime.snapshot, runtime.indexes)
            if len(candidates) == 1:
                jump_to_session(runtime, candidates[0], open_overlay=True)
                set_daemon_state(runtime, "guiding", 4.0)
                if sound_manager is not None:
                    sound_manager.play("jump_source")
            elif reason == "multiple":
                runtime.overlay.mode = "picker"
                runtime.overlay.title = "Jump To Source"
                runtime.overlay.body = "Select a source session."
                runtime.overlay.picker_index = 0
                runtime.overlay.picker_candidates = candidates
            elif reason == "non_session_only":
                runtime.status_line = "Source refs exist, but none resolve to raw sessions."
            else:
                runtime.status_line = "No raw session source is available for this note."
    elif key == pygame.K_f and runtime.focused_entry_id:
        entry = runtime.indexes.note_by_id.get(runtime.focused_entry_id)
        if entry is not None:
            if runtime.follow_thread_source == entry.note_id:
                clear_follow_thread(runtime)
            else:
                sections = related_section_keys(entry, runtime.snapshot)[:3]
                if not sections:
                    runtime.status_line = "No nearby related sections are available for a room trail."
                else:
                    runtime.follow_thread_source = entry.note_id
                    runtime.follow_thread_sections = sections
                    runtime.status_line = f"Thread trail projected toward {section_display_name(runtime, sections[0])}."
                    set_daemon_state(runtime, "guiding", 5.0)
                    runtime.overlay_return = copy_overlay(runtime.overlay)
                    runtime.overlay = OverlayState(
                        mode="inspect",
                        title="Thread Trail",
                        body=build_follow_thread_report(entry, runtime),
                    )
                    runtime.overlay_slide = 1.0


def clear_follow_thread(runtime: GameRuntime) -> None:
    runtime.follow_thread_source = None
    runtime.follow_thread_sections.clear()
    runtime.status_line = "Thread trail cleared."
    runtime.daemon_state = "idle"
    runtime.daemon_state_until = 0.0


def update_overlay_hold(runtime: GameRuntime, dt: float, pygame) -> None:
    if runtime.overlay.mode != "inspect":
        runtime.scroll_hold_direction = 0
        runtime.scroll_hold_elapsed = 0.0
        return
    keys = pygame.key.get_pressed()
    direction = 0
    if keys[pygame.K_DOWN] or keys[pygame.K_s]:
        direction = 1
    elif keys[pygame.K_UP] or keys[pygame.K_w]:
        direction = -1
    if direction == 0:
        runtime.scroll_hold_direction = 0
        runtime.scroll_hold_elapsed = 0.0
        return
    if runtime.scroll_hold_direction != direction:
        runtime.scroll_hold_direction = direction
        runtime.scroll_hold_elapsed = 0.0
        return
    runtime.scroll_hold_elapsed += dt
    repeat_delay = 0.24
    repeat_rate = 0.045
    if runtime.scroll_hold_elapsed < repeat_delay:
        return
    while runtime.scroll_hold_elapsed >= repeat_delay + repeat_rate:
        if direction > 0:
            runtime.overlay.scroll += 1
        else:
            runtime.overlay.scroll = max(0, runtime.overlay.scroll - 1)
        runtime.scroll_hold_elapsed -= repeat_rate


def maybe_refresh_daemon_socket(runtime: GameRuntime, *, force: bool = False) -> None:
    """Refresh the cached daemon socket state without hammering the daemon socket.

    This prevents draw_daemon_npc() from calling daemon_reachability() (a blocking
    socket round-trip taking ~5 ms) on every render frame, which was costing ~20 ms/frame
    and collapsing the game to ~1–3 fps.
    """
    now = monotonic()
    refresh_interval = 5.0 if runtime.daemon_socket_state == "online" else 1.0
    if not force and now - runtime.daemon_socket_checked_at < refresh_interval:
        return
    from second_brain.daemon_status import daemon_reachability
    state, _pid = daemon_reachability(runtime.settings.workspace_root)
    runtime.daemon_socket_state = state
    runtime.daemon_socket_checked_at = now


def maybe_reload_world(runtime: GameRuntime) -> None:
    if runtime.watcher is None:
        return
    now = monotonic()
    if now - runtime.last_reload_poll_at < 1.0:
        return
    runtime.last_reload_poll_at = now
    if not runtime.pending_world_reload and runtime.watcher.poll_changed():
        runtime.pending_world_reload = True
    if not runtime.pending_world_reload:
        return
    runtime.pending_world_reload = False
    loader = BrainLoader(runtime.settings.workspace_root)
    snapshot = loader.load()
    current_scene = runtime.current_scene
    current_entry = runtime.focused_entry_id
    current_slug = runtime.highlighted_session_slug
    runtime.snapshot = snapshot
    runtime.indexes = build_indexes(snapshot)
    runtime.scenes = {
        "library": build_library_layout(snapshot),
        "vault": build_dungeon_layout(snapshot),
    }
    if current_scene in runtime.scenes:
        runtime.current_scene = current_scene
    if current_entry and current_entry in runtime.indexes.note_by_id:
        runtime.focused_entry_id = current_entry
    else:
        runtime.focused_entry_id = None
    if current_slug and current_slug in runtime.indexes.session_by_slug:
        runtime.focused_session_slug = current_slug
    if current_slug and current_slug in runtime.indexes.session_by_slug:
        runtime.highlighted_session_slug = current_slug
    else:
        runtime.focused_session_slug = None
    if runtime.follow_thread_source and runtime.follow_thread_source in runtime.indexes.note_by_id:
        follow_entry = runtime.indexes.note_by_id[runtime.follow_thread_source]
        runtime.follow_thread_sections = related_section_keys(follow_entry, runtime.snapshot)[:3]
    elif runtime.follow_thread_source:
        runtime.follow_thread_source = None
        runtime.follow_thread_sections.clear()
    sync_bookmarks(runtime)
    runtime.status_line = "The library shifted as the vault changed."
    set_daemon_state(runtime, "reloading", 4.0)


def interact(runtime: GameRuntime, sound_manager: SoundManager | None = None) -> None:
    obj = current_object(runtime)
    if obj is None:
        runtime.status_line = "Nothing nearby to inspect."
        return
    if obj.kind == "stairs":
        switch_scene(runtime, obj.target_scene or runtime.current_scene)
        runtime.portal_flash = 1.0
        if obj.target_anchor_id:
            anchor = find_anchor(runtime, obj.target_anchor_id)
            if anchor is not None:
                runtime.player.x = anchor.x - runtime.player.width / 2
                runtime.player.y = anchor.y - runtime.player.height / 2
        if sound_manager is not None:
            sound_manager.play("portal_enter")
            sound_manager.music_start(runtime.current_scene)
        return
    if obj.kind == "daemon":
        if runtime.daemon_socket_state not in {"online", "starting"}:
            runtime.overlay = OverlayState(
                mode="inspect",
                title="Daemon Counter",
                body="The daemon is dormant right now. The service menu is unavailable until the background process wakes.",
            )
            runtime.status_line = "The daemon is offline."
        else:
            if runtime.carried_entry_id or runtime.carried_session_slug:
                runtime.overlay = build_counter_menu_overlay(runtime)
                runtime.status_line = f"Handover complete: {carried_item_label(runtime)} rests on the counter."
                set_daemon_state(runtime, "consulting", 4.0)
                if sound_manager is not None:
                    sound_manager.play("daemon_greet")
            else:
                runtime.overlay = OverlayState(mode="daemon", title="Daemon Counter", body="Choose a bounded service.", menu_index=0)
                runtime.status_line = "Consulting the daemon."
                set_daemon_state(runtime, "consulting", 4.0)
                if sound_manager is not None:
                    sound_manager.play("daemon_greet")
        return
    if obj.kind == "board":
        runtime.overlay = OverlayState(mode="board", title="Daemon Notice Board", body="Choose what to inspect.", menu_index=0)
        runtime.overlay_return = None
        runtime.overlay_slide = 0.0
        runtime.status_line = "Reading the daemon notice board."
        if sound_manager is not None:
            sound_manager.play("inspect_open")
        return
    if obj.kind in {"shelf", "vault-shelf", "bulletin-wall"}:
        open_shelf_browser(runtime, obj.section_key or "")
        if sound_manager is not None:
            sound_manager.play("inspect_open")
        return
    if obj.kind == "entry" and obj.note_id:
        open_shelf_browser(runtime, obj.section_key or "")
        return
    if obj.kind == "book" and obj.session_slug:
        open_shelf_browser(runtime, obj.section_key or "")
        return
    runtime.status_line = f"{obj.label} is here."


def current_object(runtime: GameRuntime):
    scene = runtime.scenes[runtime.current_scene]
    for obj in scene.objects:
        if obj.object_id == runtime.nearest_object_id:
            return obj
    return None


def find_anchor(runtime: GameRuntime, anchor_id: str):
    scene = runtime.scenes[runtime.current_scene]
    for obj in scene.objects:
        if obj.object_id == anchor_id:
            return obj
    return None


def jump_to_session(runtime: GameRuntime, book: GameBook, *, open_overlay: bool = False) -> None:
    runtime.portal_flash = 1.0
    runtime.current_scene = "vault"
    scene = runtime.scenes["vault"]
    target = next((obj for obj in scene.objects if obj.session_slug == book.session_slug), None)
    runtime.focused_entry_id = None
    runtime.focused_session_slug = book.session_slug
    if target is not None:
        runtime.player.x = max(20, target.x - 60)
        runtime.player.y = max(20, target.y - 20)
        runtime.highlighted_session_slug = book.session_slug
    else:
        runtime.player.x, runtime.player.y = scene.player_start
    if open_overlay:
        runtime.overlay = OverlayState(mode="inspect", title=book.title, body=book.body_path.read_text())
        runtime.overlay_slide = 1.0
        runtime.status_line = f"Jumped to source session {book.session_slug} and opened it."
    else:
        runtime.overlay = OverlayState()
        runtime.status_line = f"Jumped to source session {book.session_slug}."


def run_daemon_action(runtime: GameRuntime) -> None:
    selected = DAEMON_MENU[runtime.overlay.menu_index]
    entry = runtime.indexes.note_by_id.get(runtime.selected_daemon_target or runtime.focused_entry_id or "")
    if selected == "Offer a scroll":
        runtime.overlay_return = copy_overlay(runtime.overlay)
        runtime.intake = IntakeDraft()
        runtime.overlay = OverlayState(mode="intake", title="Offer A Scroll", body="")
        runtime.status_line = "The daemon waits for the source of the offered scroll."
        return
    if entry is None:
        runtime.overlay_return = copy_overlay(runtime.overlay)
        runtime.overlay = OverlayState(mode="inspect", title=selected, body="Select a note first, then consult the daemon to act on that topic.")
        return
    if selected == "Summarize linked materials":
        body = build_linked_digest(entry, runtime.snapshot, runtime.indexes)
    elif selected == "Guide me through this topic":
        body = build_guidance(entry, runtime.snapshot, runtime.indexes)
        sections = related_section_keys(entry, runtime.snapshot)[:3]
        if sections:
            runtime.follow_thread_source = entry.note_id
            runtime.follow_thread_sections = sections
            runtime.status_line = f"The daemon highlighted a route toward {section_display_name(runtime, sections[0])}."
            set_daemon_state(runtime, "guiding", 5.0)
        else:
            runtime.status_line = "The daemon found no nearby related shelves to project."
    elif selected == "Show trust and freshness":
        body = trust_explanation(entry)
    else:
        body = build_provenance(entry, runtime.snapshot, runtime.indexes)
    runtime.overlay_return = copy_overlay(runtime.overlay)
    runtime.overlay = OverlayState(mode="inspect", title=selected, body=body)


def run_board_action(runtime: GameRuntime) -> None:
    selected = BOARD_MENU[runtime.overlay.menu_index]
    if selected == "View daemon status":
        runtime.overlay_return = copy_overlay(runtime.overlay)
        runtime.overlay = OverlayState(
            mode="inspect",
            title="Daemon Status",
            body=read_daemon_status_report(runtime.settings.workspace_root),
        )
        runtime.status_line = "Opened the daemon status report."
        runtime.overlay_live_refreshed_at = 0.0
    elif selected == "View recent log":
        runtime.overlay_return = copy_overlay(runtime.overlay)
        runtime.overlay = OverlayState(
            mode="inspect",
            title="Daemon Recent Log",
            body=read_daemon_log(runtime.settings.workspace_root),
        )
        runtime.status_line = "Opened the daemon log tail."
    else:
        runtime.overlay_return = copy_overlay(runtime.overlay)
        runtime.overlay = OverlayState(
            mode="board-maintenance",
            title="Board Maintenance",
            body="Choose a safe maintenance action.",
            menu_index=0,
        )
        runtime.status_line = "Opened the board maintenance menu."


def run_board_maintenance_action(runtime: GameRuntime) -> None:
    selected = BOARD_MAINTENANCE_MENU[runtime.overlay.menu_index]
    runtime.overlay_return = copy_overlay(runtime.overlay)
    if selected == "Run status report":
        runtime.overlay = OverlayState(
            mode="inspect",
            title="Run Status Report",
            body=run_daemon_status_report_job(runtime.settings.workspace_root),
        )
        runtime.status_line = "Ran the daemon status report."


def entry_body(entry: GameEntry) -> str:
    header = [
        f"{entry.title}",
        "",
        f"Type: {entry.note_type}",
        f"Confidence: {entry.confidence or 'unknown'}",
        f"Lifecycle: {entry.lifecycle_state or 'unknown'}",
        f"Updated: {entry.updated_at or 'unknown'}",
        f"Source refs: {', '.join(entry.source_refs) if entry.source_refs else 'none'}",
        "",
    ]
    return "\n".join(header) + entry.body_path.read_text()


def trust_explanation(entry: GameEntry) -> str:
    freshness = freshness_label(entry.updated_at)
    return (
        f"Trust and freshness for {entry.title}\n\n"
        f"Confidence: {entry.confidence or 'unknown'}\n"
        f"Lifecycle: {entry.lifecycle_state or 'unknown'}\n"
        f"Freshness: {freshness}\n\n"
        "Visual cues in the game use color for trust/freshness and physical scroll condition "
        "for lifecycle state. Unknown metadata stays neutral rather than pretending to be safe."
    )


def draw_overlay(surface, runtime: GameRuntime, small_font, tiny_font, pygame) -> None:
    panel = overlay_panel_rect(runtime, pygame)
    pygame.draw.rect(surface, (245, 236, 214), panel, border_radius=12)
    pygame.draw.rect(surface, (60, 45, 35), panel, 3, border_radius=12)
    title = small_font.render(runtime.overlay.title, True, (45, 35, 30))
    surface.blit(title, (panel.x + 24, panel.y + 20))

    if runtime.overlay.mode == "skin-picker":
        draw_skin_picker_overlay(surface, runtime, panel, small_font, tiny_font, pygame)
        return
    if runtime.overlay.mode == "daemon":
        for index, item in enumerate(DAEMON_MENU):
            color = (30, 20, 18) if index == runtime.overlay.menu_index else (90, 80, 70)
            prefix = "> " if index == runtime.overlay.menu_index else "  "
            line = small_font.render(prefix + item, True, color)
            surface.blit(line, (panel.x + 36, panel.y + 80 + index * 34))
        help_line = tiny_font.render("ENTER or Space to choose, ESC to close", True, (90, 80, 70))
        surface.blit(help_line, (panel.x + 24, panel.bottom - 36))
        return
    if runtime.overlay.mode == "board":
        for index, item in enumerate(BOARD_MENU):
            color = (30, 20, 18) if index == runtime.overlay.menu_index else (90, 80, 70)
            prefix = "> " if index == runtime.overlay.menu_index else "  "
            line = small_font.render(prefix + item, True, color)
            surface.blit(line, (panel.x + 36, panel.y + 80 + index * 34))
        help_line = tiny_font.render("ENTER or Space to choose, ESC to close", True, (90, 80, 70))
        surface.blit(help_line, (panel.x + 24, panel.bottom - 36))
        return
    if runtime.overlay.mode == "board-maintenance":
        for index, item in enumerate(BOARD_MAINTENANCE_MENU):
            color = (30, 20, 18) if index == runtime.overlay.menu_index else (90, 80, 70)
            prefix = "> " if index == runtime.overlay.menu_index else "  "
            line = small_font.render(prefix + item, True, color)
            surface.blit(line, (panel.x + 36, panel.y + 80 + index * 34))
        help_line = tiny_font.render("ENTER or Space to choose, ESC returns.", True, (90, 80, 70))
        surface.blit(help_line, (panel.x + 24, panel.bottom - 36))
        return

    if runtime.overlay.mode == "shelf":
        draw_shelf_overlay(surface, runtime, panel, small_font, tiny_font, pygame)
        return
    if runtime.overlay.mode == "bookmarks":
        draw_bookmarks_overlay(surface, runtime, panel, small_font, tiny_font, pygame)
        return
    if runtime.overlay.mode in {"counter-menu", "counter-links"}:
        draw_counter_overlay(surface, runtime, panel, small_font, tiny_font, pygame)
        return

    if runtime.overlay.mode == "picker":
        for index, item in enumerate(runtime.overlay.picker_candidates):
            color = (30, 20, 18) if index == runtime.overlay.picker_index else (90, 80, 70)
            prefix = "> " if index == runtime.overlay.picker_index else "  "
            line = small_font.render(prefix + item.session_slug, True, color)
            surface.blit(line, (panel.x + 36, panel.y + 80 + index * 30))
        help_line = tiny_font.render("Choose a raw session source. ENTER or Space to jump, ESC to cancel.", True, (90, 80, 70))
        surface.blit(help_line, (panel.x + 24, panel.bottom - 36))
        return
    if runtime.overlay.mode == "intake":
        draw_intake_overlay(surface, runtime, panel, small_font, tiny_font, pygame)
        return

    lines = render_lines(runtime.overlay.body, 92)
    start = runtime.overlay.scroll
    visible = lines[start : start + _overlay_body_line_capacity(panel)]
    for index, line in enumerate(visible):
        text = _render_markdown_line(line, small_font, tiny_font)
        surface.blit(text, (panel.x + 24, panel.y + 64 + index * 22))
    footer_text = "ESC close"
    if runtime.overlay.mode == "inspect":
        footer_text += " | hold UP/DOWN scroll"
        if runtime.focused_entry_id:
            footer_text += " | J jump-source | F follow-thread | B carry-to-daemon"
    footer = tiny_font.render(footer_text, True, (90, 80, 70))
    surface.blit(footer, (panel.x + 24, panel.bottom - 36))


def _render_markdown_line(line, small_font, tiny_font):
    style_colors = {
        "heading1": ((40, 28, 20), small_font),
        "heading2": ((50, 35, 24), small_font),
        "heading3": ((60, 45, 30), tiny_font),
        "bullet": ((60, 45, 35), tiny_font),
        "code": ((68, 58, 78), tiny_font),
        "code-delimiter": ((100, 90, 110), tiny_font),
        "blank": ((50, 40, 35), tiny_font),
        "body": ((50, 40, 35), tiny_font),
    }
    color, font = style_colors.get(line.style, ((50, 40, 35), tiny_font))
    return font.render(line.text, True, color)


def _overlay_body_line_capacity(panel) -> int:
    top = panel.y + 64
    footer_y = panel.bottom - 36
    line_height = 22
    reserved_gap = 16
    available = max(0, footer_y - top - reserved_gap)
    return max(1, available // line_height)


def open_shelf_browser(runtime: GameRuntime, section_key: str) -> None:
    scene = runtime.scenes[runtime.current_scene]
    section_obj = next((obj for obj in scene.objects if obj.section_key == section_key and obj.kind in {"shelf", "vault-shelf", "bulletin-wall"}), None)
    if section_obj is None:
        runtime.status_line = "This shelf cannot be opened."
        return
    items: list[BrowserItem] = []
    if runtime.current_scene == "vault":
        for book in [book for book in runtime.snapshot.session_books if book.section_key == section_key]:
            items.append(BrowserItem(item_id=book.session_slug, kind="book", label=book.title, subtitle=book.section_label))
    else:
        for entry in [entry for entry in runtime.snapshot.note_entries if entry.section_key == section_key]:
            items.append(BrowserItem(item_id=entry.note_id, kind="entry", label=entry.title, subtitle=entry.note_type))
    runtime.overlay = OverlayState(
        mode="shelf",
        title=section_obj.label,
        body="Choose an item.",
        browser_items=items,
        browser_section_key=section_key,
        browser_section_label=section_obj.label,
    )
    runtime.overlay_return = None
    runtime.overlay_slide = 0.0
    runtime.status_line = f"Opened {section_obj.label}."
    if section_key.startswith("review"):
        set_daemon_state(runtime, "reviewing", 4.0)


def _open_browser_item(runtime: GameRuntime, hit: BrowserItem) -> None:
    if hit.kind == "entry":
        entry = runtime.indexes.note_by_id[hit.item_id]
        runtime.focused_entry_id = entry.note_id
        runtime.focused_session_slug = None
        runtime.selected_daemon_target = entry.note_id
        runtime.overlay_return = copy_overlay(runtime.overlay)
        runtime.overlay = OverlayState(mode="inspect", title=entry.title, body=entry_body(entry))
        runtime.overlay_slide = 0.0
        runtime.status_line = f"Inspecting {entry.title}."
        if entry.section_key.startswith("review"):
            set_daemon_state(runtime, "reviewing", 4.0)
    elif hit.kind == "book":
        book = runtime.indexes.session_by_slug[hit.item_id]
        runtime.focused_entry_id = None
        runtime.focused_session_slug = book.session_slug
        runtime.overlay_return = copy_overlay(runtime.overlay)
        runtime.overlay = OverlayState(mode="inspect", title=book.title, body=book.body_path.read_text())
        runtime.overlay_slide = 0.0
        runtime.status_line = f"Inspecting {book.title}."


def handle_mouse_down(runtime: GameRuntime, pos: tuple[int, int], pygame) -> None:
    if runtime.overlay.mode not in {"shelf", "bookmarks", "counter"}:
        return
    hit = shelf_item_at_pos(runtime, pos, pygame)
    if hit is None:
        return
    if runtime.overlay.mode == "bookmarks":
        open_bookmark_item(runtime, hit)
    elif runtime.overlay.mode == "counter":
        _open_browser_item(runtime, hit)
    else:
        _open_browser_item(runtime, hit)


def handle_mouse_wheel(runtime: GameRuntime, delta_y: int) -> None:
    if runtime.overlay.mode == "inspect":
        if delta_y < 0:
            runtime.overlay.scroll += 3
        elif delta_y > 0:
            runtime.overlay.scroll = max(0, runtime.overlay.scroll - 3)
        return
    if runtime.overlay.mode in {"shelf", "bookmarks", "counter"}:
        if delta_y < 0:
            runtime.overlay.browser_scroll += 2
        elif delta_y > 0:
            runtime.overlay.browser_scroll = max(0, runtime.overlay.browser_scroll - 2)


def update_portal_flash(runtime: GameRuntime, dt: float) -> None:
    if runtime.portal_flash > 0.0:
        runtime.portal_flash = max(0.0, runtime.portal_flash - dt / 0.35)


def update_overlay_slide(runtime: GameRuntime, dt: float) -> None:
    target = 0.0 if runtime.overlay.mode == "none" else 1.0
    if target > runtime.overlay_slide:
        runtime.overlay_slide = min(1.0, runtime.overlay_slide + dt / 0.3)
    elif target < runtime.overlay_slide:
        runtime.overlay_slide = max(0.0, runtime.overlay_slide - dt / 0.2)
    daemon_state_until = getattr(runtime, "daemon_state_until", 0.0)
    if daemon_state_until and monotonic() >= daemon_state_until:
        runtime.daemon_state = "idle"
        runtime.daemon_state_until = 0.0


def maybe_refresh_live_overlay(runtime: GameRuntime) -> None:
    if runtime.overlay.mode != "inspect" or runtime.overlay.title != "Daemon Status":
        return
    now = monotonic()
    if now - runtime.overlay_live_refreshed_at < 1.0:
        return
    runtime.overlay.body = read_daemon_status_report(runtime.settings.workspace_root)
    runtime.overlay_live_refreshed_at = now


def update_step_audio(runtime: GameRuntime, dt: float, sound_manager: SoundManager | None) -> None:
    if sound_manager is None:
        return
    if runtime.player_is_moving and runtime.overlay.mode == "none":
        runtime.step_timer += dt
        if runtime.step_timer >= 0.35:
            sound_manager.play("step")
            runtime.step_timer = 0.0
    else:
        runtime.step_timer = 0.35


def set_daemon_state(runtime: GameRuntime, state: str, duration: float) -> None:
    runtime.daemon_state = state
    runtime.daemon_state_until = monotonic() + duration


def section_display_name(runtime: GameRuntime, section_key: str) -> str:
    for zone in runtime.snapshot.zones:
        for section in zone.sections:
            if section.key == section_key:
                return section.label
    return section_key


def build_bookmarks_overlay(runtime: GameRuntime) -> OverlayState:
    items = []
    stale_found = False
    for bookmark in runtime.bookmarks:
        if bookmark in runtime.indexes.note_by_id:
            entry = runtime.indexes.note_by_id[bookmark]
            items.append(BrowserItem(item_id=bookmark, kind="entry", label=entry.title, subtitle=f"{entry.note_type} • {bookmark}"))
        elif bookmark in runtime.indexes.session_by_slug:
            book = runtime.indexes.session_by_slug[bookmark]
            items.append(BrowserItem(item_id=bookmark, kind="book", label=book.title, subtitle=f"session • {bookmark}"))
        else:
            stale_found = True
    if stale_found:
        sync_bookmarks(runtime)
    body = "Saved routes through the stacks." if items else "No bookmarks yet."
    return OverlayState(mode="bookmarks", title="Bookmarks", body=body, browser_items=items)


def carry_current_item(runtime: GameRuntime) -> None:
    entry_id = runtime.focused_entry_id
    session_slug = runtime.focused_session_slug
    if entry_id is not None:
        if runtime.carried_entry_id == entry_id:
            clear_carried_item(runtime)
            return
        runtime.carried_entry_id = entry_id
        runtime.carried_session_slug = None
        add_bookmark(runtime, entry_id)
        entry = runtime.indexes.note_by_id.get(entry_id)
        title = entry.title if entry is not None else entry_id
        runtime.overlay = OverlayState()
        runtime.overlay_return = None
        runtime.overlay_slide = 0.0
        runtime.status_line = f"Carrying {title}. Walk to the daemon counter and press B to hand it over."
        return
    if session_slug is not None:
        if runtime.carried_session_slug == session_slug:
            clear_carried_item(runtime)
            return
        runtime.carried_entry_id = None
        runtime.carried_session_slug = session_slug
        add_bookmark(runtime, session_slug)
        book = runtime.indexes.session_by_slug.get(session_slug)
        title = book.title if book is not None else session_slug
        runtime.overlay = OverlayState()
        runtime.overlay_return = None
        runtime.overlay_slide = 0.0
        runtime.status_line = f"Carrying {title}. Walk to the daemon counter and press B to hand it over."


def clear_carried_item(runtime: GameRuntime) -> None:
    runtime.carried_entry_id = None
    runtime.carried_session_slug = None
    runtime.status_line = "Set the carried scroll back on its shelf."


def carried_item_label(runtime: GameRuntime) -> str:
    if runtime.carried_entry_id and runtime.carried_entry_id in runtime.indexes.note_by_id:
        return runtime.indexes.note_by_id[runtime.carried_entry_id].title
    if runtime.carried_session_slug and runtime.carried_session_slug in runtime.indexes.session_by_slug:
        return runtime.indexes.session_by_slug[runtime.carried_session_slug].title
    return "the carried scroll"


def build_counter_menu_overlay(runtime: GameRuntime) -> OverlayState:
    if runtime.carried_entry_id and runtime.carried_entry_id in runtime.indexes.note_by_id:
        runtime.selected_daemon_target = runtime.carried_entry_id
    elif runtime.carried_session_slug:
        runtime.selected_daemon_target = None
    body = f"{carried_item_label(runtime)} is open on the counter. What should the daemon do?"
    return OverlayState(mode="counter-menu", title="Daemon Counter", body=body, menu_index=0)


def build_counter_links_overlay(runtime: GameRuntime) -> OverlayState:
    items: list[BrowserItem] = []
    body = "Summoned materials:"
    if runtime.carried_entry_id and runtime.carried_entry_id in runtime.indexes.note_by_id:
        entry = runtime.indexes.note_by_id[runtime.carried_entry_id]
        runtime.selected_daemon_target = entry.note_id
        items.append(BrowserItem(item_id=entry.note_id, kind="entry", label=entry.title, subtitle="carried scroll"))
        seen_ids = {entry.note_id}
        for related_id in related_note_ids(entry, runtime.snapshot)[:5]:
            candidate = runtime.indexes.note_by_id.get(related_id)
            if candidate is None or candidate.note_id in seen_ids:
                continue
            items.append(BrowserItem(item_id=candidate.note_id, kind="entry", label=candidate.title, subtitle=f"linked {candidate.note_type}"))
            seen_ids.add(candidate.note_id)
        candidates, _reason = resolve_jump_candidates(entry, runtime.snapshot, runtime.indexes)
        for book in candidates[:3]:
            items.append(BrowserItem(item_id=book.session_slug, kind="book", label=book.title, subtitle="source session"))
    elif runtime.carried_session_slug and runtime.carried_session_slug in runtime.indexes.session_by_slug:
        book = runtime.indexes.session_by_slug[runtime.carried_session_slug]
        items.append(BrowserItem(item_id=book.session_slug, kind="book", label=book.title, subtitle="carried session"))
        session_ref = f"session/{book.session_slug}"
        for candidate in runtime.snapshot.note_entries:
            if session_ref in candidate.source_refs:
                items.append(BrowserItem(item_id=candidate.note_id, kind="entry", label=candidate.title, subtitle=f"linked {candidate.note_type}"))
    if len(items) <= 1:
        body = "The daemon studies the carried scroll, but no linked materials answered the summons."
    return OverlayState(mode="counter-links", title="Summoned Materials", body=body, browser_items=items)


def run_counter_action(runtime: GameRuntime) -> None:
    choice = COUNTER_MENU[runtime.overlay.menu_index]
    if choice == "Summon all linked wikis":
        runtime.overlay = build_counter_links_overlay(runtime)
        runtime.status_line = "The daemon summoned linked materials to the counter."
        set_daemon_state(runtime, "guiding", 4.0)
        return
    if choice == "Summarize the scroll":
        body = summarize_carried_scroll(runtime)
        runtime.overlay_return = build_counter_menu_overlay(runtime)
        runtime.overlay = OverlayState(mode="inspect", title="Counter Summary", body=body)
        runtime.overlay_slide = 1.0
        runtime.status_line = "The daemon summarizes the scroll."
        return
    if choice == "Teleport to the raw material":
        teleport_to_carried_source(runtime)
        return
    close_counter_overlay(runtime)


def summarize_carried_scroll(runtime: GameRuntime) -> str:
    if runtime.carried_entry_id and runtime.carried_entry_id in runtime.indexes.note_by_id:
        entry = runtime.indexes.note_by_id[runtime.carried_entry_id]
        return build_linked_digest(entry, runtime.snapshot, runtime.indexes)
    if runtime.carried_session_slug and runtime.carried_session_slug in runtime.indexes.session_by_slug:
        book = runtime.indexes.session_by_slug[runtime.carried_session_slug]
        return f"Summary for {book.title}\n\nThis is a raw session book currently carried to the counter.\n\nOpen it from the counter to inspect the full contents."
    return "The daemon has nothing to summarize."


def teleport_to_carried_source(runtime: GameRuntime) -> None:
    if runtime.carried_entry_id and runtime.carried_entry_id in runtime.indexes.note_by_id:
        entry = runtime.indexes.note_by_id[runtime.carried_entry_id]
        candidates, reason = resolve_jump_candidates(entry, runtime.snapshot, runtime.indexes)
        if len(candidates) == 1:
            jump_to_session(runtime, candidates[0], open_overlay=True)
            runtime.status_line = "The daemon teleported you to the raw source."
            return
        if len(candidates) > 1:
            runtime.overlay = OverlayState(mode="picker", title="Jump To Source", body="Select a source session.")
            runtime.overlay.picker_index = 0
            runtime.overlay.picker_candidates = candidates
            runtime.status_line = "Choose which raw source book to open."
            return
        if reason == "non_session_only":
            runtime.status_line = "The carried scroll has source refs, but none resolve to raw session books."
            return
    if runtime.carried_session_slug and runtime.carried_session_slug in runtime.indexes.session_by_slug:
        jump_to_session(runtime, runtime.indexes.session_by_slug[runtime.carried_session_slug], open_overlay=True)
        runtime.status_line = "The daemon returned you to the carried raw session."
        return
    runtime.status_line = "No raw source is available for the carried scroll."


def close_counter_overlay(runtime: GameRuntime) -> None:
    clear_carried_item(runtime)
    runtime.selected_daemon_target = None
    runtime.overlay = OverlayState()
    runtime.overlay_return = None
    runtime.overlay_slide = 0.0
    runtime.status_line = "The daemon returned the summoned materials to their shelves."
    set_daemon_state(runtime, "sorting", 2.5)


def build_onboarding_overlay() -> OverlayState:
    body = (
        "Welcome to MenomicOS\n\n"
        "Walk: WASD or arrow keys move through the library.\n"
        "Inspect: Space opens the nearby shelf, board, stair, or note.\n"
        "Trace: J jumps to a source session, and F projects a follow-thread trail.\n"
        "Consult: Step to the daemon counter and press Space for bounded services.\n"
        "Present: B carries the current note or session to the daemon, and TAB opens saved routes.\n\n"
        "Press any key to begin."
    )
    return OverlayState(mode="onboarding", title="Welcome", body=body)


# ── Skin picker ───────────────────────────────────────────────────────────────

def open_skin_picker(runtime: GameRuntime) -> None:
    runtime.overlay = OverlayState(
        mode="skin-picker",
        title="Player Appearance",
        body="",
        menu_index=0,          # 0 = cloak row, 1 = tunic row
        browser_cursor=_skin_cloak_index(runtime),
        browser_scroll=_skin_tunic_index(runtime),
    )
    runtime.overlay_slide = 0.0


def _skin_cloak_index(runtime: GameRuntime) -> int:
    for i, (_, c) in enumerate(SKIN_CLOAK_PRESETS):
        if c == runtime.player_skin_cloak:
            return i
    return 0


def _skin_tunic_index(runtime: GameRuntime) -> int:
    for i, (_, t) in enumerate(SKIN_TUNIC_PRESETS):
        if t == runtime.player_skin_tunic:
            return i
    return 0


def handle_skin_picker_keys(runtime: GameRuntime, key: int, pygame, sound_manager) -> None:
    if key == pygame.K_ESCAPE:
        runtime.overlay = OverlayState()
        runtime.overlay_slide = 0.0
        if sound_manager:
            sound_manager.play("inspect_close")
        return
    if key in (pygame.K_UP, pygame.K_w):
        runtime.overlay.menu_index = max(0, runtime.overlay.menu_index - 1)
        if sound_manager:
            sound_manager.play("menu_move")
        return
    if key in (pygame.K_DOWN, pygame.K_s):
        runtime.overlay.menu_index = min(1, runtime.overlay.menu_index + 1)
        if sound_manager:
            sound_manager.play("menu_move")
        return
    if key in (pygame.K_LEFT, pygame.K_a):
        if runtime.overlay.menu_index == 0:
            runtime.overlay.browser_cursor = (runtime.overlay.browser_cursor - 1) % len(SKIN_CLOAK_PRESETS)
        else:
            runtime.overlay.browser_scroll = (runtime.overlay.browser_scroll - 1) % len(SKIN_TUNIC_PRESETS)
        _apply_skin_preview(runtime)
        if sound_manager:
            sound_manager.play("menu_move")
        return
    if key in (pygame.K_RIGHT, pygame.K_d):
        if runtime.overlay.menu_index == 0:
            runtime.overlay.browser_cursor = (runtime.overlay.browser_cursor + 1) % len(SKIN_CLOAK_PRESETS)
        else:
            runtime.overlay.browser_scroll = (runtime.overlay.browser_scroll + 1) % len(SKIN_TUNIC_PRESETS)
        _apply_skin_preview(runtime)
        if sound_manager:
            sound_manager.play("menu_move")
        return
    if key in (pygame.K_RETURN, pygame.K_SPACE):
        _apply_skin_preview(runtime)
        runtime.overlay = OverlayState()
        runtime.overlay_slide = 0.0
        runtime.status_line = "Appearance saved."
        if sound_manager:
            sound_manager.play("menu_confirm")


def _apply_skin_preview(runtime: GameRuntime) -> None:
    cloak_i = runtime.overlay.browser_cursor % len(SKIN_CLOAK_PRESETS)
    tunic_i = runtime.overlay.browser_scroll % len(SKIN_TUNIC_PRESETS)
    runtime.player_skin_cloak = SKIN_CLOAK_PRESETS[cloak_i][1]
    runtime.player_skin_tunic = SKIN_TUNIC_PRESETS[tunic_i][1]
    runtime.player_skin_dirty = True


def draw_skin_picker_overlay(surface, runtime: GameRuntime, panel, small_font, tiny_font, pygame) -> None:
    cloak_i = runtime.overlay.browser_cursor % len(SKIN_CLOAK_PRESETS)
    tunic_i = runtime.overlay.browser_scroll % len(SKIN_TUNIC_PRESETS)
    row     = runtime.overlay.menu_index

    y = panel.y + 70

    # Cloak row
    row_color = (30, 20, 18) if row == 0 else (90, 80, 70)
    cloak_label = small_font.render("Cloak:", True, row_color)
    surface.blit(cloak_label, (panel.x + 24, y))
    px = panel.x + 110
    for i, (name, cloak_c) in enumerate(SKIN_CLOAK_PRESETS):
        swatch = pygame.Rect(px, y, 22, 22)
        pygame.draw.rect(surface, cloak_c, swatch, border_radius=4)
        if i == cloak_i:
            pygame.draw.rect(surface, (240, 200, 80), swatch, 2, border_radius=4)
        name_s = tiny_font.render(name, True, (150, 130, 110) if i != cloak_i else (240, 200, 80))
        surface.blit(name_s, (px, y + 26))
        px += 90
    y += 62

    # Tunic row
    row_color = (30, 20, 18) if row == 1 else (90, 80, 70)
    tunic_label = small_font.render("Tunic:", True, row_color)
    surface.blit(tunic_label, (panel.x + 24, y))
    px = panel.x + 110
    for i, (name, tunic_c) in enumerate(SKIN_TUNIC_PRESETS):
        swatch = pygame.Rect(px, y, 22, 22)
        pygame.draw.rect(surface, tunic_c, swatch, border_radius=4)
        if i == tunic_i:
            pygame.draw.rect(surface, (240, 200, 80), swatch, 2, border_radius=4)
        name_s = tiny_font.render(name, True, (150, 130, 110) if i != tunic_i else (240, 200, 80))
        surface.blit(name_s, (px, y + 26))
        px += 90
    y += 62

    # Row selector arrows
    arrow = "▶  " if row == 0 else "   "
    surface.blit(tiny_font.render(arrow + "UP/DOWN switch row", True, (90, 80, 70)), (panel.x + 24, y))
    y += 22
    surface.blit(tiny_font.render("LEFT/RIGHT change colour, ENTER confirm, ESC cancel", True, (90, 80, 70)), (panel.x + 24, y))


def dismiss_onboarding(runtime: GameRuntime) -> None:
    runtime.prefs.onboarding_dismissed = True
    save_prefs(runtime.settings.workspace_root, runtime.prefs)
    runtime.overlay = OverlayState()
    runtime.overlay_slide = 0.0
    runtime.status_line = "Explore the library."


def maybe_show_landmark_hint(runtime: GameRuntime) -> None:
    if runtime.overlay.mode != "none":
        return
    obj = current_object(runtime)
    if obj is None:
        return
    hint_key = landmark_hint_key(obj)
    if hint_key is None or hint_key in runtime.seen_landmark_hints:
        return
    runtime.seen_landmark_hints.add(hint_key)
    runtime.status_line = landmark_hint_text(hint_key)


def landmark_hint_key(obj) -> str | None:
    if obj.kind == "shelf":
        return "shelf"
    if obj.kind == "bulletin-wall":
        return "system-wall"
    if obj.kind == "daemon":
        return "daemon"
    if obj.kind == "stairs":
        return "portal"
    if obj.kind == "vault-shelf":
        return "vault-shelf"
    return None


def landmark_hint_text(hint_key: str) -> str:
    hints = {
        "shelf": "Shelf: press Space to browse notes in this section.",
        "system-wall": "Rules wall: press Space to read the posted system memory.",
        "daemon": "Daemon counter: press Space to ask for a bounded service.",
        "portal": "Stairs: press Space to move between library and vault.",
        "vault-shelf": "Archive shelf: press Space to browse raw session books.",
    }
    return hints.get(hint_key, "Press Space to inspect.")


def handle_intake_keys(runtime: GameRuntime, key: int, pygame, sound_manager: SoundManager | None, text: str) -> bool:
    draft = runtime.intake
    if key == pygame.K_ESCAPE:
        if draft.step == 0:
            runtime.overlay = runtime.overlay_return or OverlayState()
            runtime.overlay_return = None
            runtime.status_line = "Cancelled the intake ritual."
        elif draft.step >= 5:
            draft.step = 4
            draft.status = "draft"
            draft.error = ""
            runtime.status_line = "Returned to confirmation."
        else:
            draft.step -= 1
            runtime.status_line = "Stepped back in the intake ritual."
        runtime.overlay_slide = 1.0 if runtime.overlay.mode != "none" else 0.0
        if sound_manager is not None:
            sound_manager.play("inspect_close")
        return True
    if draft.step in {0, 1, 2}:
        field_name = ("source", "title", "summary")[draft.step]
        current = getattr(draft, field_name)
        if key == pygame.K_BACKSPACE:
            setattr(draft, field_name, current[:-1])
            return True
        if key in (pygame.K_RETURN, pygame.K_SPACE) and key != pygame.K_SPACE:
            if not current.strip():
                runtime.status_line = "This field cannot be empty."
                return True
            draft.step += 1
            runtime.status_line = intake_step_prompt(draft.step)
            if sound_manager is not None:
                sound_manager.play("menu_confirm")
            return True
        if key == pygame.K_RETURN:
            return True
        if text and text.isprintable() and text not in {"\r", "\n", "\t"}:
            if len(current) < 160:
                setattr(draft, field_name, current + text)
            return True
        return key in {pygame.K_RETURN, pygame.K_BACKSPACE}
    if draft.step == 3:
        current_index = INTAKE_TYPES.index(draft.item_type)
        if key in (pygame.K_DOWN, pygame.K_s, pygame.K_RIGHT, pygame.K_d):
            draft.item_type = INTAKE_TYPES[(current_index + 1) % len(INTAKE_TYPES)]
            if sound_manager is not None:
                sound_manager.play("menu_move")
            return True
        if key in (pygame.K_UP, pygame.K_w, pygame.K_LEFT, pygame.K_a):
            draft.item_type = INTAKE_TYPES[(current_index - 1) % len(INTAKE_TYPES)]
            if sound_manager is not None:
                sound_manager.play("menu_move")
            return True
        if key in (pygame.K_RETURN, pygame.K_SPACE):
            draft.step = 4
            runtime.status_line = "The daemon reads the draft back for confirmation."
            if sound_manager is not None:
                sound_manager.play("menu_confirm")
            return True
        return False
    if draft.step == 4:
        if key in (pygame.K_RETURN, pygame.K_SPACE):
            path = confirm_intake(runtime)
            if path is not None:
                runtime.status_line = f"Archived scroll to {path.name}."
                if sound_manager is not None:
                    sound_manager.play("menu_confirm")
            return True
        return False
    if draft.step == 5:
        if key in (pygame.K_RETURN, pygame.K_SPACE):
            runtime.overlay = OverlayState()
            runtime.overlay_return = None
            runtime.status_line = "Closed the intake ritual."
            if sound_manager is not None:
                sound_manager.play("inspect_close")
            return True
        return False


def intake_step_prompt(step: int) -> str:
    prompts = {
        1: "The daemon asks for a working title.",
        2: "The daemon asks for a short summary.",
        3: "Choose what kind of scroll this is.",
        4: "Review the draft and confirm if it should be archived.",
    }
    return prompts.get(step, "Continue the intake ritual.")


def draw_intake_overlay(surface, runtime: GameRuntime, panel, small_font, tiny_font, pygame) -> None:
    draft = runtime.intake
    surface.blit(tiny_font.render(intake_step_label(draft), True, (90, 80, 70)), (panel.x + 24, panel.y + 54))
    if draft.step in {0, 1, 2}:
        prompt = {
            0: "Where did this material come from?",
            1: "What is the working title?",
            2: "Give a 2-4 sentence summary.",
        }[draft.step]
        current = {
            0: draft.source,
            1: draft.title,
            2: draft.summary,
        }[draft.step]
        lines = render_lines(prompt, 88)
        for index, line in enumerate(lines):
            text = _render_markdown_line(line, small_font, tiny_font)
            surface.blit(text, (panel.x + 24, panel.y + 92 + index * 22))
        input_rect = pygame.Rect(panel.x + 24, panel.y + 170, panel.width - 48, 58)
        pygame.draw.rect(surface, (255, 248, 232), input_rect, border_radius=8)
        pygame.draw.rect(surface, (110, 88, 62), input_rect, 2, border_radius=8)
        caret = "_" if int(monotonic() * 2) % 2 == 0 else ""
        input_text = tiny_font.render((current or "") + caret, True, (45, 35, 30))
        surface.blit(input_text, (input_rect.x + 12, input_rect.y + 18))
        help_line = tiny_font.render("Type to fill the field. ENTER continues, ESC steps back.", True, (90, 80, 70))
        surface.blit(help_line, (panel.x + 24, panel.bottom - 36))
        return
    if draft.step == 3:
        for index, item_type in enumerate(INTAKE_TYPES):
            color = (30, 20, 18) if item_type == draft.item_type else (90, 80, 70)
            prefix = "> " if item_type == draft.item_type else "  "
            line = small_font.render(prefix + item_type, True, color)
            surface.blit(line, (panel.x + 36, panel.y + 96 + index * 34))
        help_line = tiny_font.render("UP/DOWN choose type. ENTER confirms, ESC steps back.", True, (90, 80, 70))
        surface.blit(help_line, (panel.x + 24, panel.bottom - 36))
        return
    if draft.step in {4, 5}:
        markdown = render_intake_markdown(draft)
        lines = render_lines(markdown, 92)
        for index, line in enumerate(lines[: _overlay_body_line_capacity(panel)]):
            text = _render_markdown_line(line, small_font, tiny_font)
            surface.blit(text, (panel.x + 24, panel.y + 82 + index * 21))
        footer_text = "ENTER archives | ESC steps back" if draft.step == 4 else "ENTER closes | ESC steps back"
        if draft.status == "archived" and draft.last_written_path:
            footer_text = "ENTER closes | archived to raw/sessions"
        elif draft.status == "error":
            footer_text = "ENTER retries | ESC steps back"
        footer = tiny_font.render(footer_text, True, (90, 80, 70))
        surface.blit(footer, (panel.x + 24, panel.bottom - 36))


def intake_step_label(draft: IntakeDraft) -> str:
    labels = {
        0: "Step 1 of 5 • Source",
        1: "Step 2 of 5 • Title",
        2: "Step 3 of 5 • Summary",
        3: "Step 4 of 5 • Type",
        4: "Step 5 of 5 • Confirmation",
        5: "Archived",
    }
    if draft.status == "error":
        return "Archive Error"
    return labels.get(draft.step, "Offer A Scroll")


def render_intake_markdown(draft: IntakeDraft) -> str:
    if draft.status == "archived":
        return (
            "Archived\n\n"
            f"Source: {draft.source}\n"
            f"Title: {draft.title}\n"
            f"Type: {draft.item_type}\n\n"
            f"The daemon archived this scroll to `{draft.last_written_path or 'raw/sessions'}` and turns toward the vault stairs."
        )
    if draft.status == "error":
        return (
            "Error\n\n"
            f"The daemon could not archive this scroll.\n\n{draft.error}\n\n"
            "No partial file was kept. Adjust the draft or retry."
        )
    preview = render_intake_session_markdown(draft, "PREVIEW-TIMESTAMP")
    return f"Confirmation\n\nThe daemon reads back the draft that will be archived:\n\n```md\n{preview}\n```"


def confirm_intake(runtime: GameRuntime) -> Path | None:
    draft = runtime.intake
    try:
        path = write_intake_session(runtime.settings.workspace_root, draft)
    except OSError as exc:
        draft.status = "error"
        draft.error = str(exc)
        draft.step = 5
        return None
    draft.status = "archived"
    draft.last_written_path = str(path.relative_to(runtime.settings.workspace_root))
    draft.step = 5
    set_daemon_state(runtime, "sorting", 5.0)
    return path


def write_intake_session(workspace_root: Path, draft: IntakeDraft) -> Path:
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d-%H%M%S")
    slug = slugify(draft.title) or "untitled-scroll"
    path = workspace_root / "brain" / "raw" / "sessions" / f"{timestamp}-claude-intake-{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = render_intake_session_markdown(draft, timestamp)
    path.write_text(body + "\n", encoding="utf-8")
    return path


def render_intake_session_markdown(draft: IntakeDraft, timestamp: str) -> str:
    created_at = timestamp[:10]
    return (
        "---\n"
        f"title: {draft.title}\n"
        "type: session\n"
        f"created_at: {created_at}\n"
        f"intake_type: {draft.item_type}\n"
        f"intake_source: {draft.source}\n"
        "---\n\n"
        "# Intake Scroll\n\n"
        f"Source: {draft.source}\n\n"
        f"Working title: {draft.title}\n\n"
        f"Suggested type: {draft.item_type}\n\n"
        "Summary:\n"
        f"{draft.summary}\n"
    )


def build_follow_thread_report(entry: GameEntry, runtime: GameRuntime) -> str:
    lines = [
        f"Thread trail for {entry.title}",
        "",
        "The daemon found related sections and marked them in the room.",
        "",
    ]
    for index, section_key in enumerate(runtime.follow_thread_sections, start=1):
        section_name = section_display_name(runtime, section_key)
        related_titles = [
            candidate.title
            for candidate in runtime.snapshot.note_entries
            if candidate.section_key == section_key
        ][:3]
        lines.append(f"{index}. {section_name}")
        lines.append(f"   Example notes: {', '.join(related_titles) if related_titles else 'none visible in this section'}")
    lines.append("")
    lines.append("Close this panel and look for the amber trail/highlight in the room.")
    return "\n".join(lines)


def add_bookmark(runtime: GameRuntime, item_id: str) -> None:
    if item_id in runtime.bookmarks:
        return
    runtime.bookmarks.append(item_id)
    save_bookmarks(runtime.settings.workspace_root, runtime.bookmarks)


def remove_bookmark(runtime: GameRuntime, item_id: str) -> None:
    runtime.bookmarks = [bookmark for bookmark in runtime.bookmarks if bookmark != item_id]
    save_bookmarks(runtime.settings.workspace_root, runtime.bookmarks)
    runtime.status_line = f"Removed bookmark {item_id}."


def open_bookmark_item(runtime: GameRuntime, item: BrowserItem) -> None:
    if item.kind == "entry":
        entry = runtime.indexes.note_by_id.get(item.item_id)
        if entry is None:
            remove_bookmark(runtime, item.item_id)
            runtime.overlay = build_bookmarks_overlay(runtime)
            return
        runtime.current_scene = "library"
        _move_player_to_section(runtime, entry.section_key)
        runtime.focused_entry_id = entry.note_id
        runtime.focused_session_slug = None
        runtime.selected_daemon_target = entry.note_id
        runtime.overlay = OverlayState(mode="inspect", title=entry.title, body=entry_body(entry))
        runtime.overlay_slide = 0.0
        runtime.status_line = f"Opened bookmark {entry.title}."
        return
    book = runtime.indexes.session_by_slug.get(item.item_id)
    if book is None:
        remove_bookmark(runtime, item.item_id)
        runtime.overlay = build_bookmarks_overlay(runtime)
        return
    jump_to_session(runtime, book)
    runtime.overlay = OverlayState(mode="inspect", title=book.title, body=book.body_path.read_text())
    runtime.overlay_slide = 0.0
    runtime.status_line = f"Opened bookmark {book.title}."


def _move_player_to_section(runtime: GameRuntime, section_key: str) -> None:
    scene = runtime.scenes[runtime.current_scene]
    target = next((obj for obj in scene.objects if obj.section_key == section_key and obj.kind in {"shelf", "vault-shelf", "bulletin-wall"}), None)
    if target is None:
        runtime.player.x, runtime.player.y = scene.player_start
        return
    runtime.player.x = max(20, target.x - 72)
    runtime.player.y = max(20, target.y + 16)


def draw_bookmarks_overlay(surface, runtime: GameRuntime, panel, small_font, tiny_font, pygame) -> None:
    if not runtime.overlay.browser_items:
        lines = render_lines(runtime.overlay.body, 92)
        for index, line in enumerate(lines[:8]):
            text = _render_markdown_line(line, small_font, tiny_font)
            surface.blit(text, (panel.x + 24, panel.y + 84 + index * 22))
        help_line = tiny_font.render("TAB or ESC close.", True, (90, 80, 70))
        surface.blit(help_line, (panel.x + 24, panel.bottom - 36))
        return
    _draw_browser_overlay(
        surface,
        runtime,
        panel,
        small_font,
        tiny_font,
        pygame,
        help_text="UP/DOWN navigate, ENTER jump, D remove, click to open, ESC closes.",
        content_top=panel.y + 84,
    )


def draw_counter_overlay(surface, runtime: GameRuntime, panel, small_font, tiny_font, pygame) -> None:
    if runtime.overlay.mode == "counter-menu":
        body_lines = render_lines(runtime.overlay.body, 92)
        for index, line in enumerate(body_lines[:3]):
            text = _render_markdown_line(line, small_font, tiny_font)
            surface.blit(text, (panel.x + 24, panel.y + 56 + index * 20))
        scroll_rect = pygame.Rect(panel.x + 28, panel.y + 122, 72, 92)
        pygame.draw.rect(surface, (247, 236, 205), scroll_rect, border_radius=8)
        pygame.draw.rect(surface, (126, 94, 62), scroll_rect, 2, border_radius=8)
        pygame.draw.line(surface, (126, 94, 62), (scroll_rect.x + 10, scroll_rect.y + 22), (scroll_rect.right - 10, scroll_rect.y + 22), 2)
        pygame.draw.line(surface, (126, 94, 62), (scroll_rect.x + 10, scroll_rect.y + 38), (scroll_rect.right - 14, scroll_rect.y + 38), 2)
        for index, item in enumerate(COUNTER_MENU):
            color = (30, 20, 18) if index == runtime.overlay.menu_index else (90, 80, 70)
            prefix = "> " if index == runtime.overlay.menu_index else "  "
            line = small_font.render(prefix + f"{index + 1}. {item}", True, color)
            surface.blit(line, (panel.x + 132, panel.y + 124 + index * 38))
        help_line = tiny_font.render("UP/DOWN choose, ENTER or B confirm, ESC place back.", True, (90, 80, 70))
        surface.blit(help_line, (panel.x + 24, panel.bottom - 36))
        return
    header = tiny_font.render(runtime.overlay.body, True, (90, 80, 70))
    surface.blit(header, (panel.x + 24, panel.y + 58))
    if runtime.overlay.browser_items:
        _draw_browser_overlay(
            surface,
            runtime,
            panel,
            small_font,
            tiny_font,
            pygame,
            help_text="UP/DOWN navigate, ENTER inspect, B back to actions, ESC close.",
            content_top=panel.y + 96,
        )
    else:
        help_line = tiny_font.render("B returns to actions. ESC places the scroll back.", True, (90, 80, 70))
        surface.blit(help_line, (panel.x + 24, panel.bottom - 36))


def draw_shelf_overlay(surface, runtime: GameRuntime, panel, small_font, tiny_font, pygame) -> None:
    _draw_browser_overlay(
        surface,
        runtime,
        panel,
        small_font,
        tiny_font,
        pygame,
        help_text="UP/DOWN navigate, ENTER open, click to open, ESC returns.",
        content_top=panel.y + 84,
    )


def _draw_browser_overlay(surface, runtime, panel, small_font, tiny_font, pygame, *, help_text: str, content_top: int) -> None:
    info = tiny_font.render(help_text, True, (90, 80, 70))
    surface.blit(info, (panel.x + 24, panel.y + 52))
    for vis_index, (item, rect) in enumerate(shelf_item_rects(runtime, panel, pygame, start_y=content_top)):
        item_index = runtime.overlay.browser_scroll + vis_index
        is_cursor = item_index == runtime.overlay.browser_cursor
        bg = (255, 240, 200) if is_cursor else (232, 224, 205)
        border = (80, 60, 40) if is_cursor else (120, 105, 88)
        pygame.draw.rect(surface, bg, rect, border_radius=8)
        pygame.draw.rect(surface, border, rect, 2 if is_cursor else 1, border_radius=8)
        title = tiny_font.render(item.label[:52], True, (40, 32, 26))
        subtitle = tiny_font.render(item.subtitle[:30], True, (95, 85, 75))
        surface.blit(title, (rect.x + 12, rect.y + 10))
        surface.blit(subtitle, (rect.x + 12, rect.y + 30))
        if item.kind == "entry":
            entry = runtime.indexes.note_by_id.get(item.item_id)
            if entry is not None:
                dot_color = entry_color(entry)
                dot_cx = rect.right - 22
                dot_cy = rect.y + rect.height // 2
                pygame.draw.circle(surface, dot_color, (dot_cx, dot_cy), 7)
                pygame.draw.circle(surface, (40, 32, 26), (dot_cx, dot_cy), 7, 1)
    if runtime.overlay.browser_items:
        total = len(runtime.overlay.browser_items)
        start = min(runtime.overlay.browser_scroll, max(0, total - 1)) + 1
        end = min(total, runtime.overlay.browser_scroll + _shelf_page_size(panel, start_y=content_top))
        footer = tiny_font.render(f"Showing {start}-{end} of {total}", True, (90, 80, 70))
        surface.blit(footer, (panel.x + 24, panel.bottom - 36))


def _shelf_page_size(panel, *, start_y: int | None = None) -> int:
    """Number of items that fit between the header hint and the footer line."""
    item_slot = 62  # height(52) + gap(10)
    bottom = getattr(panel, "bottom", panel.y + panel.height)
    content_top = start_y if start_y is not None else panel.y + 84
    available = (bottom - 36) - content_top
    return max(1, available // item_slot)


def _shelf_page_size_for_runtime(runtime: GameRuntime) -> int:
    usable_height = runtime.settings.window_height - 160
    item_slot = 62
    available = usable_height - 120
    return max(1, available // item_slot)


def shelf_item_rects(runtime: GameRuntime, panel, pygame, *, start_y: int = None) -> list[tuple[BrowserItem, object]]:
    start_y = start_y if start_y is not None else panel.y + 84
    height = 52
    gap = 10
    page = _shelf_page_size(panel, start_y=start_y)
    rects: list[tuple[BrowserItem, object]] = []
    visible_items = runtime.overlay.browser_items[runtime.overlay.browser_scroll : runtime.overlay.browser_scroll + page]
    for index, item in enumerate(visible_items):
        rect = pygame.Rect(panel.x + 24, start_y + index * (height + gap), panel.width - 48, height)
        rects.append((item, rect))
    return rects


def shelf_item_at_pos(runtime: GameRuntime, pos: tuple[int, int], pygame):
    panel = overlay_panel_rect(runtime, pygame)
    start_y = panel.y + 106 if runtime.overlay.mode == "counter" else panel.y + 84
    for item, rect in shelf_item_rects(runtime, panel, pygame, start_y=start_y):
        if rect.collidepoint(*pos):
            return item
    return None


def overlay_panel_rect(runtime: GameRuntime, pygame):
    panel_width = runtime.settings.window_width - 240
    eased = ease_out_quad(runtime.overlay_slide)
    panel_x = runtime.settings.window_width - int(eased * panel_width) - 24
    return pygame.Rect(panel_x, 80, panel_width, runtime.settings.window_height - 160)


def copy_overlay(overlay: OverlayState) -> OverlayState:
    return OverlayState(
        mode=overlay.mode,
        title=overlay.title,
        body=overlay.body,
        scroll=overlay.scroll,
        menu_index=overlay.menu_index,
        picker_index=overlay.picker_index,
        picker_candidates=list(getattr(overlay, "picker_candidates", [])),
        browser_items=list(getattr(overlay, "browser_items", [])),
        browser_section_key=getattr(overlay, "browser_section_key", None),
        browser_section_label=getattr(overlay, "browser_section_label", ""),
        browser_scroll=getattr(overlay, "browser_scroll", 0),
        browser_cursor=getattr(overlay, "browser_cursor", 0),
    )


if __name__ == "__main__":
    raise SystemExit(main())
