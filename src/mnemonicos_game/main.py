from __future__ import annotations

import argparse
import sys
from time import monotonic
from pathlib import Path

from .assets import load_assets
from .data.loader import BrainLoader
from .data.models import GameBook, GameEntry, WorldSnapshot
from .data.watcher import WorldWatcher
from .engine import AnimationClock, SoundManager, ease_out_quad
from .entities.daemon_npc import read_daemon_log, read_daemon_status_report, run_daemon_status_report_job
from .entities import daemon_is_available
from .entities.marker import entry_color, freshness_label
from .runtime import BOARD_MAINTENANCE_MENU, BOARD_MENU, BrowserItem, DAEMON_MENU, GameRuntime, OverlayState, PlayerState, switch_scene
from .scenes import DungeonScene, LibraryScene
from .services.digest import build_guidance, build_indexes, build_linked_digest, build_provenance, resolve_jump_candidates
from .settings import GameSettings
from .ui.markdown_renderer import render_lines
from .world.routing import related_section_keys
from .world.tilemap import build_dungeon_layout, build_library_layout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MnemonicOS Library RPG")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Workspace root containing brain/")
    parser.add_argument("--dump-world", action="store_true", help="Print a world summary and exit")
    parser.add_argument("--check", action="store_true", help="Validate the game data wiring and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = GameSettings(workspace_root=args.root.resolve())
    snapshot = BrainLoader(settings.workspace_root).load()

    if args.dump_world or args.check:
        print(format_summary(snapshot))
        return 0

    return run_game(settings, snapshot)


def run_game(settings: GameSettings, snapshot: WorldSnapshot) -> int:
    try:
        import pygame
    except ModuleNotFoundError:
        print(
            "pygame-ce is required to launch the MnemonicOS Library RPG window.\n"
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
        font = pygame.font.Font(None, 32)
        small_font = pygame.font.Font(None, 24)
        tiny_font = pygame.font.Font(None, 20)
        clock = pygame.time.Clock()
        running = True
        while running:
            dt = clock.tick(settings.fps) / 1000.0
            animation_clock.update(runtime, dt)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE and runtime.overlay.mode == "none":
                        running = False
                    else:
                        handle_keydown(runtime, event.key, pygame, sound_manager)
                if event.type == pygame.MOUSEWHEEL:
                    handle_mouse_wheel(runtime, event.y)
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    handle_mouse_down(runtime, event.pos, pygame)
            update_overlay_hold(runtime, dt, pygame)
            update_overlay_slide(runtime, dt)
            maybe_reload_world(runtime)
            maybe_refresh_live_overlay(runtime)
            if runtime.overlay.mode == "none":
                scenes[runtime.current_scene].update(runtime, dt, pygame)
            else:
                runtime.player_is_moving = False
                scenes[runtime.current_scene].update(runtime, 0.0, pygame)
            update_step_audio(runtime, dt, sound_manager)
            scenes[runtime.current_scene].draw(screen, runtime, font, small_font, tiny_font, pygame)
            if runtime.overlay.mode != "none":
                draw_overlay(screen, runtime, small_font, tiny_font, pygame)
            pygame.display.flip()
    finally:
        pygame.quit()
    return 0


def format_summary(snapshot: WorldSnapshot) -> str:
    lines = [
        "MnemonicOS Library RPG",
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
            marker = " [reserved]" if section.reserved else ""
            lines.append(f"  - {section.label}: {section.item_count}{marker}")
    return "\n".join(lines)


def handle_keydown(runtime: GameRuntime, key: int, pygame, sound_manager: SoundManager | None = None) -> None:
    if key == pygame.K_ESCAPE:
        if runtime.overlay_return is not None:
            runtime.overlay = runtime.overlay_return
            runtime.overlay_return = None
            runtime.status_line = "Returned."
            runtime.overlay_slide = 0.0
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
        if key == pygame.K_TAB:
            runtime.overlay = OverlayState()
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
    if key == pygame.K_SPACE:
        interact(runtime, sound_manager)
    elif key == pygame.K_TAB:
        runtime.overlay = OverlayState(mode="bookmarks", title="Bookmarks", body="\n".join(runtime.bookmarks) or "No bookmarks yet.")


def handle_inspector_keys(runtime: GameRuntime, key: int, pygame, sound_manager: SoundManager | None = None) -> None:
    if key in (pygame.K_DOWN, pygame.K_s):
        runtime.overlay.scroll += 1
        runtime.scroll_hold_direction = 1
        runtime.scroll_hold_elapsed = 0.0
    elif key in (pygame.K_UP, pygame.K_w):
        runtime.overlay.scroll = max(0, runtime.overlay.scroll - 1)
        runtime.scroll_hold_direction = -1
        runtime.scroll_hold_elapsed = 0.0
    elif key == pygame.K_b and runtime.focused_entry_id:
        if runtime.focused_entry_id not in runtime.bookmarks:
            runtime.bookmarks.append(runtime.focused_entry_id)
        runtime.status_line = f"Bookmarked {runtime.focused_entry_id}."
        if sound_manager is not None:
            sound_manager.play("bookmark")
    elif key == pygame.K_j and runtime.focused_entry_id:
        entry = runtime.indexes.note_by_id.get(runtime.focused_entry_id)
        if entry is not None:
            candidates, reason = resolve_jump_candidates(entry, runtime.snapshot, runtime.indexes)
            if len(candidates) == 1:
                jump_to_session(runtime, candidates[0])
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
                runtime.follow_thread_source = None
                runtime.follow_thread_sections.clear()
                runtime.status_line = "Thread trail cleared."
                runtime.daemon_state = "idle"
                runtime.daemon_state_until = 0.0
            else:
                sections = related_section_keys(entry, runtime.snapshot)[:3]
                if not sections:
                    runtime.status_line = "No nearby related sections are available for a room trail."
                else:
                    runtime.follow_thread_source = entry.note_id
                    runtime.follow_thread_sections = sections
                    runtime.status_line = f"Thread trail projected toward {section_display_name(runtime, sections[0])}."
                    set_daemon_state(runtime, "guiding", 5.0)


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


def maybe_reload_world(runtime: GameRuntime) -> None:
    if runtime.watcher is None:
        return
    now = monotonic()
    if now - runtime.last_reload_poll_at < 1.0:
        return
    runtime.last_reload_poll_at = now
    if not runtime.watcher.poll_changed():
        return
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
    if current_slug and current_slug in runtime.indexes.session_by_slug:
        runtime.highlighted_session_slug = current_slug
    if runtime.follow_thread_source and runtime.follow_thread_source in runtime.indexes.note_by_id:
        follow_entry = runtime.indexes.note_by_id[runtime.follow_thread_source]
        runtime.follow_thread_sections = related_section_keys(follow_entry, runtime.snapshot)[:3]
    elif runtime.follow_thread_source:
        runtime.follow_thread_source = None
        runtime.follow_thread_sections.clear()
    runtime.status_line = "The library shifted as the vault changed."
    set_daemon_state(runtime, "reloading", 4.0)


def interact(runtime: GameRuntime, sound_manager: SoundManager | None = None) -> None:
    obj = current_object(runtime)
    if obj is None:
        runtime.status_line = "Nothing nearby to inspect."
        return
    if obj.kind == "stairs":
        switch_scene(runtime, obj.target_scene or runtime.current_scene)
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
        if not daemon_is_available(runtime.settings.workspace_root):
            runtime.overlay = OverlayState(
                mode="inspect",
                title="Daemon Counter",
                body="The daemon is dormant right now. The service menu is unavailable until the background process wakes.",
            )
            runtime.status_line = "The daemon is offline."
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


def jump_to_session(runtime: GameRuntime, book: GameBook) -> None:
    runtime.current_scene = "vault"
    scene = runtime.scenes["vault"]
    target = next((obj for obj in scene.objects if obj.session_slug == book.session_slug), None)
    if target is not None:
        runtime.player.x = max(20, target.x - 60)
        runtime.player.y = max(20, target.y - 20)
        runtime.highlighted_session_slug = book.session_slug
    else:
        runtime.player.x, runtime.player.y = scene.player_start
    runtime.overlay = OverlayState()
    runtime.status_line = f"Jumped to source session {book.session_slug}."


def run_daemon_action(runtime: GameRuntime) -> None:
    selected = DAEMON_MENU[runtime.overlay.menu_index]
    entry = runtime.indexes.note_by_id.get(runtime.selected_daemon_target or runtime.focused_entry_id or "")
    if selected == "Offer a scroll":
        body = (
            "Offer a scroll\n\n"
            "This ritual is intentionally bounded. In the roadmap it archives first, "
            "routes through MnemonicOS intake, and avoids direct canonical editing.\n\n"
            "Implementation note: write-path UI is not enabled in this vertical slice."
        )
        runtime.overlay_return = copy_overlay(runtime.overlay)
        runtime.overlay = OverlayState(mode="inspect", title="Offer A Scroll", body=body)
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

    if runtime.overlay.mode == "picker":
        for index, item in enumerate(runtime.overlay.picker_candidates):
            color = (30, 20, 18) if index == runtime.overlay.picker_index else (90, 80, 70)
            prefix = "> " if index == runtime.overlay.picker_index else "  "
            line = small_font.render(prefix + item.session_slug, True, color)
            surface.blit(line, (panel.x + 36, panel.y + 80 + index * 30))
        help_line = tiny_font.render("Choose a raw session source. ENTER or Space to jump, ESC to cancel.", True, (90, 80, 70))
        surface.blit(help_line, (panel.x + 24, panel.bottom - 36))
        return

    lines = render_lines(runtime.overlay.body, 92)
    start = runtime.overlay.scroll
    visible = lines[start : start + 24]
    for index, line in enumerate(visible):
        text = _render_markdown_line(line, small_font, tiny_font)
        surface.blit(text, (panel.x + 24, panel.y + 64 + index * 22))
    footer_text = "ESC close"
    if runtime.overlay.mode == "inspect":
        footer_text += " | hold UP/DOWN scroll"
        if runtime.focused_entry_id:
            footer_text += " | J jump-source | F follow-thread | B bookmark"
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
        runtime.selected_daemon_target = entry.note_id
        runtime.overlay_return = copy_overlay(runtime.overlay)
        runtime.overlay = OverlayState(mode="inspect", title=entry.title, body=entry_body(entry))
        runtime.overlay_slide = 0.0
        runtime.status_line = f"Inspecting {entry.title}."
        if entry.section_key.startswith("review"):
            set_daemon_state(runtime, "reviewing", 4.0)
    elif hit.kind == "book":
        book = runtime.indexes.session_by_slug[hit.item_id]
        runtime.overlay_return = copy_overlay(runtime.overlay)
        runtime.overlay = OverlayState(mode="inspect", title=book.title, body=book.body_path.read_text())
        runtime.overlay_slide = 0.0
        runtime.status_line = f"Inspecting {book.title}."


def handle_mouse_down(runtime: GameRuntime, pos: tuple[int, int], pygame) -> None:
    if runtime.overlay.mode != "shelf":
        return
    hit = shelf_item_at_pos(runtime, pos, pygame)
    if hit is None:
        return
    _open_browser_item(runtime, hit)


def handle_mouse_wheel(runtime: GameRuntime, delta_y: int) -> None:
    if runtime.overlay.mode == "inspect":
        if delta_y < 0:
            runtime.overlay.scroll += 3
        elif delta_y > 0:
            runtime.overlay.scroll = max(0, runtime.overlay.scroll - 3)
        return
    if runtime.overlay.mode == "shelf":
        if delta_y < 0:
            runtime.overlay.browser_scroll += 2
        elif delta_y > 0:
            runtime.overlay.browser_scroll = max(0, runtime.overlay.browser_scroll - 2)


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


def draw_shelf_overlay(surface, runtime: GameRuntime, panel, small_font, tiny_font, pygame) -> None:
    info = tiny_font.render("UP/DOWN navigate, ENTER open, click to open, ESC returns.", True, (90, 80, 70))
    surface.blit(info, (panel.x + 24, panel.y + 52))
    for vis_index, (item, rect) in enumerate(shelf_item_rects(runtime, panel, pygame)):
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
        end = min(total, runtime.overlay.browser_scroll + _shelf_page_size(panel))
        footer = tiny_font.render(f"Showing {start}-{end} of {total}", True, (90, 80, 70))
        surface.blit(footer, (panel.x + 24, panel.bottom - 36))


def _shelf_page_size(panel) -> int:
    """Number of items that fit between the header hint and the footer line."""
    item_slot = 62  # height(52) + gap(10)
    bottom = getattr(panel, "bottom", panel.y + panel.height)
    available = (bottom - 36) - (panel.y + 84)
    return max(1, available // item_slot)


def shelf_item_rects(runtime: GameRuntime, panel, pygame) -> list[tuple[BrowserItem, object]]:
    start_y = panel.y + 84
    height = 52
    gap = 10
    page = _shelf_page_size(panel)
    rects: list[tuple[BrowserItem, object]] = []
    visible_items = runtime.overlay.browser_items[runtime.overlay.browser_scroll : runtime.overlay.browser_scroll + page]
    for index, item in enumerate(visible_items):
        rect = pygame.Rect(panel.x + 24, start_y + index * (height + gap), panel.width - 48, height)
        rects.append((item, rect))
    return rects


def shelf_item_at_pos(runtime: GameRuntime, pos: tuple[int, int], pygame):
    panel = overlay_panel_rect(runtime, pygame)
    for item, rect in shelf_item_rects(runtime, panel, pygame):
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
