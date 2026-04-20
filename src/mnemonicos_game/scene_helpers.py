from __future__ import annotations

import math

from mnemonicos_game.assets import color
from mnemonicos_game.entities import draw_scroll, entry_color, lifecycle_color
from mnemonicos_game.entities.marker import freshness_label
from mnemonicos_game.entities.daemon_npc import daemon_effective_state, daemon_state_label, daemon_status_line, review_slip_count
from mnemonicos_game.entities.scroll import scroll_is_fresh
from mnemonicos_game.ui import draw_hud_legend, draw_minimap
from mnemonicos_game.world.routing import related_section_keys


def update_player(runtime, dt: float, pygame) -> None:
    keys = pygame.key.get_pressed()
    dx = 0.0
    dy = 0.0
    speed = runtime.player.speed
    if keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]:
        speed *= 1.9
    if keys[pygame.K_a] or keys[pygame.K_LEFT]:
        dx -= speed * dt
    if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
        dx += speed * dt
    if keys[pygame.K_w] or keys[pygame.K_UP]:
        dy -= speed * dt
    if keys[pygame.K_s] or keys[pygame.K_DOWN]:
        dy += speed * dt

    runtime.player_is_moving = bool(dx or dy)
    if abs(dx) > abs(dy):
        runtime.player_facing = "east" if dx > 0 else "west"
    elif abs(dy) > 0:
        runtime.player_facing = "south" if dy > 0 else "north"
    runtime.player.x, runtime.player.y = move_with_collision(runtime, runtime.player.x, runtime.player.y, dx, dy, pygame)


def move_with_collision(runtime, x: float, y: float, dx: float, dy: float, pygame) -> tuple[float, float]:
    scene = runtime.scenes[runtime.current_scene]
    next_x = min(max(20, x + dx), scene.width - runtime.player.width - 20)
    next_y = min(max(20, y + dy), scene.height - runtime.player.height - 20)
    player_rect = pygame.Rect(int(next_x), int(y), runtime.player.width, runtime.player.height)
    if any(player_rect.colliderect(pygame.Rect(*obj.rect_tuple)) for obj in scene.objects if obj.solid):
        next_x = x
    player_rect = pygame.Rect(int(next_x), int(next_y), runtime.player.width, runtime.player.height)
    if any(player_rect.colliderect(pygame.Rect(*obj.rect_tuple)) for obj in scene.objects if obj.solid):
        next_y = y
    return next_x, next_y


def update_focus(runtime) -> None:
    scene = runtime.scenes[runtime.current_scene]
    nearest = None
    px = runtime.player.x + runtime.player.width / 2
    py = runtime.player.y + runtime.player.height / 2
    interactive_kinds = {"shelf", "vault-shelf", "bulletin-wall", "stairs", "daemon", "board"}
    for obj in scene.objects:
        if obj.kind not in interactive_kinds:
            continue
        distance = rect_edge_distance(px, py, obj.rect_tuple)
        focus_range = 110 if obj.kind == "bulletin-wall" else 72
        weighted_distance = distance - 22 if obj.kind == "bulletin-wall" else distance
        if distance > focus_range:
            continue
        if nearest is None or weighted_distance < nearest[0]:
            nearest = (weighted_distance, obj)
    runtime.nearest_object_id = nearest[1].object_id if nearest else None


def rect_edge_distance(px: float, py: float, rect: tuple[int, int, int, int]) -> float:
    x, y, width, height = rect
    nearest_x = min(max(px, x), x + width)
    nearest_y = min(max(py, y), y + height)
    return ((nearest_x - px) ** 2 + (nearest_y - py) ** 2) ** 0.5


def draw_scene(surface, runtime, font, small_font, tiny_font, pygame) -> None:
    colors = {
        "bg": color("bg"),
        "parchment": color("parchment"),
        "text": color("text"),
        "muted": color("muted"),
        "player": color("blue"),
        "portal": color("orange"),
        "orange": color("orange"),
        "board": color("wall"),
        "highlight": color("green"),
        "wall": color("wall"),
        "floor": color("floor"),
        "shadow": color("shadow"),
        "vault_floor": color("vault_floor"),
        "vault_wall": color("vault_wall"),
    }
    scene = runtime.scenes[runtime.current_scene]
    camera_x = max(0, min(int(runtime.player.x - runtime.settings.window_width / 2), scene.width - runtime.settings.window_width))
    camera_y = max(0, min(int(runtime.player.y - runtime.settings.window_height / 2), scene.height - runtime.settings.window_height))

    surface.fill(colors["bg"])
    draw_storybook_floor(surface, runtime, camera_x, camera_y, colors, pygame)
    draw_wall_strips(surface, runtime, camera_x, camera_y, colors, pygame)
    draw_related_threads(surface, runtime, camera_x, camera_y, colors, pygame)

    for obj in scene.objects:
        rect = pygame.Rect(obj.x - camera_x, obj.y - camera_y, obj.width, obj.height)
        if obj.kind == "shelf":
            if obj.reserved:
                draw_construction_shelf(surface, rect, colors, pygame)
            else:
                draw_risk_halo(surface, rect, section_entries(runtime, obj.section_key), colors, pygame)
                draw_bookshelf(surface, rect, colors, pygame)
        elif obj.kind == "vault-shelf":
            if obj.reserved:
                draw_construction_shelf(surface, rect, colors, pygame)
            else:
                draw_risk_halo(surface, rect, [], colors, pygame)
                draw_vault_shelf(surface, rect, colors, pygame)
        elif obj.kind == "bulletin-wall":
            draw_risk_halo(surface, rect, section_entries(runtime, obj.section_key), colors, pygame)
            draw_bulletin_board(surface, rect, colors, pygame)
        elif obj.kind == "entry-pile":
            entries = [
                entry
                for entry in runtime.snapshot.note_entries
                if entry.section_key == obj.section_key and entry.note_id != runtime.carried_entry_id
            ]
            draw_scroll_pile(surface, rect, entries, runtime, pygame)
        elif obj.kind == "bulletin-note":
            draw_bulletin_post(surface, rect, pygame)
        elif obj.kind == "book":
            if obj.session_slug == runtime.carried_session_slug:
                continue
            book_colors = [(100, 72, 44), (80, 60, 38), (112, 80, 50)]
            fill = book_colors[hash(obj.session_slug or "") % 3] if obj.session_slug else book_colors[0]
            if runtime.highlighted_session_slug == obj.session_slug:
                fill = colors["highlight"]
            pygame.draw.rect(surface, fill, rect, border_radius=2)
            # Spine line
            pygame.draw.line(surface, (40, 26, 14), (rect.x + 3, rect.y + 2), (rect.x + 3, rect.bottom - 2), 1)
        elif obj.kind == "marker":
            pygame.draw.circle(surface, colors["highlight"], rect.center, max(4, rect.width // 2))
        elif obj.kind == "daemon":
            draw_daemon_npc(surface, rect, runtime, colors, tiny_font, pygame)
        elif obj.kind == "board":
            draw_notice_board(surface, rect, colors, pygame)
        elif obj.kind == "stairs":
            draw_stairs(surface, rect, runtime.current_scene, runtime.anim_tick, colors, pygame)
        elif obj.kind == "entrance":
            pygame.draw.rect(surface, colors["wall"], rect, border_radius=14)
            pygame.draw.rect(surface, colors["parchment"], rect, 3, border_radius=14)
        elif obj.kind == "anchor":
            continue

        if obj.kind in {"shelf", "vault-shelf", "bulletin-wall", "entrance"}:
            label = tiny_font.render(obj.label, True, colors["text"])
            surface.blit(label, (rect.x + 8, rect.y + 8))

        # Stale fog — semi-transparent grey overlay on heavily stale shelves
        if obj.kind in {"shelf", "vault-shelf"} and not obj.reserved and obj.section_key:
            stale_ratio = section_stale_ratio(obj.section_key, runtime.snapshot)
            if stale_ratio > 0.6:
                fog = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
                fog.fill((80, 80, 80, int(stale_ratio * 55)))
                surface.blit(fog, rect.topleft)

        # Follow-thread trail — warm amber rings showing the daemon-guided route
        if runtime.follow_thread_sections and obj.section_key in runtime.follow_thread_sections:
            priority = runtime.follow_thread_sections.index(obj.section_key)
            thread_colors = [(230, 168, 42), (196, 128, 24), (160, 96, 18)]
            thread_widths = [3, 2, 2]
            pygame.draw.rect(surface, thread_colors[priority], rect.inflate(10, 10), thread_widths[priority], border_radius=9)

        # Focus highlight — green, drawn last so it is always on top
        if obj.object_id == runtime.nearest_object_id:
            pygame.draw.rect(surface, colors["highlight"], rect.inflate(8, 8), 2, border_radius=6)

    player_bob = 0 if not runtime.player_is_moving else ((runtime.anim_frame % 2) * 2)
    player_rect = pygame.Rect(int(runtime.player.x - camera_x), int(runtime.player.y - camera_y - player_bob), runtime.player.width, runtime.player.height)
    player_frames = runtime.assets.player_frames if runtime.assets is not None else {}
    facing_frames = player_frames.get(runtime.player_facing, [])
    if facing_frames:
        frame_index = runtime.anim_frame % len(facing_frames) if runtime.player_is_moving else 0
        surface.blit(pygame.transform.smoothscale(facing_frames[frame_index], player_rect.size), player_rect.topleft)
    else:
        pygame.draw.rect(surface, colors["player"], player_rect, border_radius=8)
        draw_player_facing(surface, runtime, player_rect, colors, pygame)
    draw_carried_scroll(surface, runtime, player_rect, pygame)

    header = font.render(f"MenomicOS - {scene.name.title()}", True, colors["text"])
    surface.blit(header, (20, 16))
    if runtime.anim_tick < 3.0:
        hint = tiny_font.render(
            "WASD/Arrows move, Shift sprint, Space interact, J jump-source, B carry, TAB bookmarks",
            True,
            (255, 255, 255),
        )
        surface.blit(hint, (20, 48))
    elif runtime.overlay.mode == "none" and runtime.nearest_object_id == "daemon:counter" and (runtime.carried_entry_id or runtime.carried_session_slug):
        handoff = tiny_font.render("B: handover to daemon", True, (255, 255, 255))
        surface.blit(handoff, (20, 48))
    if runtime.player_is_moving:
        draw_minimap(surface, runtime, tiny_font, pygame)
    if runtime.overlay.mode == "inspect" and runtime.focused_entry_id:
        draw_hud_legend(surface, tiny_font, pygame)
    draw_scene_ambient(surface, runtime, colors, pygame)


def draw_storybook_floor(surface, runtime, camera_x: int, camera_y: int, colors, pygame) -> None:
    tile = 32
    floor_color = colors["floor"] if runtime.current_scene == "library" else colors["vault_floor"]
    accent = colors["wall"] if runtime.current_scene == "library" else colors["parchment"]
    width = runtime.settings.window_width + tile
    height = runtime.settings.window_height + tile
    start_x = -(camera_x % tile)
    start_y = -(camera_y % tile)
    tile_surface = runtime.assets.tile_floor if runtime.assets is not None else None
    for x in range(start_x, width, tile):
        for y in range(start_y, height, tile):
            if tile_surface is not None and runtime.current_scene == "library":
                surface.blit(tile_surface, (x, y))
            else:
                pygame.draw.rect(surface, floor_color, (x, y, tile, tile))
                center_x = x + tile // 2
                center_y = y + tile // 2
                pygame.draw.polygon(
                    surface,
                    accent,
                    [(center_x, center_y - 3), (center_x + 3, center_y), (center_x, center_y + 3), (center_x - 3, center_y)],
                )


def draw_wall_strips(surface, runtime, camera_x: int, camera_y: int, colors, pygame) -> None:
    strip_color = colors["wall"] if runtime.current_scene == "library" else colors["vault_wall"]
    wall_tile = runtime.assets.tile_wall if runtime.assets is not None else None
    top_rect = pygame.Rect(0 - camera_x, 0 - camera_y, runtime.scenes[runtime.current_scene].width, 32)
    left_rect = pygame.Rect(0 - camera_x, 0 - camera_y, 32, runtime.scenes[runtime.current_scene].height)
    if wall_tile is not None and runtime.current_scene == "library":
        for x in range(top_rect.x, top_rect.x + top_rect.width, 32):
            surface.blit(wall_tile, (x, top_rect.y))
        for y in range(left_rect.y, left_rect.y + left_rect.height, 32):
            surface.blit(wall_tile, (left_rect.x, y))
    else:
        pygame.draw.rect(surface, strip_color, top_rect)
        pygame.draw.rect(surface, strip_color, left_rect)


def draw_player_facing(surface, runtime, player_rect, colors, pygame) -> None:
    marker = pygame.Rect(0, 0, 8, 4)
    if runtime.player_facing == "north":
        marker.midtop = (player_rect.centerx, player_rect.y + 4)
    elif runtime.player_facing == "south":
        marker.midbottom = (player_rect.centerx, player_rect.bottom - 4)
    elif runtime.player_facing == "west":
        marker.midleft = (player_rect.x + 4, player_rect.centery)
        marker.width, marker.height = 4, 8
    else:
        marker.midright = (player_rect.right - 4, player_rect.centery)
        marker.width, marker.height = 4, 8
    pygame.draw.rect(surface, colors["parchment"], marker, border_radius=3)


def draw_carried_scroll(surface, runtime, player_rect, pygame) -> None:
    if not (runtime.carried_entry_id or runtime.carried_session_slug):
        return
    scroll = pygame.Rect(player_rect.right - 5, player_rect.y + 10, 9, 12)
    pygame.draw.rect(surface, (247, 232, 192), scroll, border_radius=3)
    pygame.draw.rect(surface, (124, 92, 56), scroll, 1, border_radius=3)
    pygame.draw.line(surface, (124, 92, 56), (scroll.x + 2, scroll.y + 4), (scroll.right - 2, scroll.y + 4), 1)
    pygame.draw.line(surface, (124, 92, 56), (scroll.x + 2, scroll.y + 7), (scroll.right - 3, scroll.y + 7), 1)


def draw_scene_ambient(surface, runtime, colors, pygame) -> None:
    overlay = pygame.Surface((runtime.settings.window_width, runtime.settings.window_height), pygame.SRCALPHA)
    if runtime.current_scene == "library":
        flicker_alpha = int(12 + 5 * (1 + math.sin(runtime.ambient_tick * 0.785)))
        overlay.fill((*colors["bg"], flicker_alpha))
    else:
        overlay.fill((0, 0, 0, 90))
    surface.blit(overlay, (0, 0))
    risk = scene_risk_profile(runtime)
    if runtime.current_scene == "library":
        mood_alpha = min(56, risk["review"] * 3 + risk["stale"] * 2 + risk["unknown"] + risk["contested"] * 6)
        if mood_alpha:
            mood_color = (150, 68, 64) if risk["contested"] else (168, 122, 64)
            mood = pygame.Surface((runtime.settings.window_width, runtime.settings.window_height), pygame.SRCALPHA)
            mood.fill((*mood_color, mood_alpha))
            surface.blit(mood, (0, 0))
    if runtime.assets is not None and getattr(runtime.assets, "vignette", None) is not None:
        vignette = runtime.assets.vignette
        if vignette.get_size() != (runtime.settings.window_width, runtime.settings.window_height):
            vignette = pygame.transform.smoothscale(vignette, (runtime.settings.window_width, runtime.settings.window_height))
        surface.blit(vignette, (0, 0))


def draw_related_threads(surface, runtime, camera_x: int, camera_y: int, colors, pygame) -> None:
    if not runtime.follow_thread_source:
        return
    entry = runtime.indexes.note_by_id.get(runtime.follow_thread_source)
    if entry is None:
        return
    source = next(
        (
            obj
            for obj in runtime.scenes[runtime.current_scene].objects
            if obj.section_key == entry.section_key and obj.kind in {"shelf", "bulletin-wall", "vault-shelf"}
        ),
        None,
    )
    if source is None:
        return
    source_center = (int(source.center[0] - camera_x), int(source.center[1] - camera_y))
    targets = []
    for index, section_key in enumerate(runtime.follow_thread_sections):
        target = next(
            (
                obj
                for obj in runtime.scenes[runtime.current_scene].objects
                if obj.section_key == section_key and obj.kind in {"shelf", "bulletin-wall", "vault-shelf"}
            ),
            None,
        )
        if target is None:
            continue
        targets.append(target)
        target_center = (int(target.center[0] - camera_x), int(target.center[1] - camera_y))
        line_color = colors["highlight"] if index == 0 else colors["parchment"]
        pygame.draw.line(surface, line_color, source_center, target_center, 3 if index == 0 else 2)
        pygame.draw.circle(surface, line_color, target_center, 5)
        pulse = 14 + ((runtime.anim_frame + index) % 3) * 3
        pygame.draw.circle(surface, line_color, target_center, pulse, 1)
    if targets:
        primary = choose_primary_thread_target(runtime, targets)
        player_center = (
            int(runtime.player.x + runtime.player.width / 2 - camera_x),
            int(runtime.player.y + runtime.player.height / 2 - camera_y),
        )
        primary_center = (int(primary.center[0] - camera_x), int(primary.center[1] - camera_y))
        draw_thread_breadcrumbs(surface, player_center, primary_center, colors, runtime.anim_frame, pygame)


def section_entries(runtime, section_key: str | None):
    if section_key is None:
        return []
    return [entry for entry in runtime.snapshot.note_entries if entry.section_key == section_key]


def choose_primary_thread_target(runtime, targets):
    px = runtime.player.x + runtime.player.width / 2
    py = runtime.player.y + runtime.player.height / 2
    return min(targets, key=lambda obj: (obj.center[0] - px) ** 2 + (obj.center[1] - py) ** 2)


def draw_thread_breadcrumbs(surface, start, end, colors, anim_frame: int, pygame) -> None:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    distance = max(1.0, (dx * dx + dy * dy) ** 0.5)
    dots = max(4, int(distance // 40))
    phase = anim_frame % 6
    for index in range(1, dots + 1):
        ratio = index / dots
        x = int(start[0] + dx * ratio)
        y = int(start[1] + dy * ratio)
        radius = 5 if (index + phase) % 3 == 0 else 3
        pygame.draw.circle(surface, colors["highlight"], (x, y), radius)


def scene_risk_profile(runtime) -> dict[str, int]:
    if runtime.current_scene != "library":
        return {"review": 0, "contested": 0, "stale": 0, "unknown": 0}
    contested = sum(1 for entry in runtime.snapshot.note_entries if entry.lifecycle_state == "contested")
    stale = sum(1 for entry in runtime.snapshot.note_entries if entry_color(entry) == (120, 120, 120))
    unknown = sum(1 for entry in runtime.snapshot.note_entries if not entry.updated_at or not entry.confidence)
    return {
        "review": review_slip_count(runtime.snapshot),
        "contested": contested,
        "stale": stale,
        "unknown": unknown,
    }


def draw_risk_halo(surface, rect, entries, colors, pygame) -> None:
    if not entries:
        return
    stale = sum(1 for entry in entries if entry_color(entry) == (120, 120, 120))
    contested = sum(1 for entry in entries if entry.lifecycle_state == "contested")
    unknown = sum(1 for entry in entries if not entry.updated_at or not entry.confidence)
    if contested:
        halo = (176, 76, 68)
    elif stale:
        halo = (120, 120, 120)
    elif unknown:
        halo = (201, 160, 90)
    else:
        halo = (92, 184, 92)
    halo_rect = rect.inflate(12, 12)
    pygame.draw.rect(surface, halo, halo_rect, 3, border_radius=12)
    if contested or stale:
        haze = pygame.Surface((halo_rect.width, halo_rect.height), pygame.SRCALPHA)
        alpha = 38 if contested else 24
        haze.fill((*halo, alpha))
        surface.blit(haze, halo_rect.topleft)


def draw_scroll_pile(surface, rect, entries, runtime, pygame) -> None:
    total = len(entries)
    if total <= 0:
        return
    pile_bases = decompose_capped_triangular_piles(total, max_base=3)
    index = 0
    scroll_w = 14
    scroll_h = 20
    col_step = 11
    row_step = 6
    pile_gap = 16
    pile_widths = [(base - 1) * col_step + scroll_w for base in pile_bases]
    total_width = sum(pile_widths) + pile_gap * max(0, len(pile_widths) - 1)
    pile_x = rect.x + max(0, (rect.width - total_width) // 2)
    shelf_y = rect.bottom - 8
    for pile_index, base in enumerate(pile_bases):
        this_width = pile_widths[pile_index]
        pile_origin_x = pile_x
        for row_index, row_count in enumerate(range(base, 0, -1)):
            row_width = (row_count - 1) * col_step + scroll_w
            row_x = pile_origin_x + max(0, (this_width - row_width) // 2)
            y = shelf_y - scroll_h - row_index * row_step
            for col in range(row_count):
                entry = entries[index]
                fill = entry_color(entry)
                outline = lifecycle_color(entry.lifecycle_state)
                slot_rect = pygame.Rect(row_x + col * col_step, y, scroll_w, scroll_h)
                sprite = None
                if runtime.assets is not None and getattr(runtime.assets, "scroll_sprites", None):
                    sprite = runtime.assets.scroll_sprites.get(str(entry.lifecycle_state))
                draw_scroll(
                    surface,
                    slot_rect,
                    fill,
                    outline,
                    entry.lifecycle_state,
                    pygame,
                    sprite=sprite,
                    is_fresh=scroll_is_fresh(entry),
                    anim_frame=runtime.anim_frame + index,
                )
                index += 1
        pile_x += this_width + pile_gap


def decompose_capped_triangular_piles(total: int, *, max_base: int) -> list[int]:
    if total <= 0:
        return []
    bases: list[int] = []
    remaining = total
    while remaining > 0:
        base = min(max_base, _smallest_triangular_base(remaining))
        while triangular_number(base) > remaining and base > 1:
            base -= 1
        if remaining > triangular_number(max_base):
            base = max_base
        bases.append(base)
        remaining -= triangular_number(base)
    return bases


def draw_stairs(surface, rect, scene_name: str, anim_tick: float, colors, pygame) -> None:
    """Compact staircase: top-down perspective with receding steps."""
    wall_color = colors["wall"] if scene_name == "library" else colors["vault_wall"]
    floor_color = colors["floor"] if scene_name == "library" else colors["vault_floor"]
    shadow_color = colors["shadow"]
    pulse = portal_pulse_strength(scene_name, anim_tick)
    step_count = 5
    step_h = max(6, rect.height // step_count)

    # Outer stone frame
    pygame.draw.rect(surface, wall_color, rect, border_radius=6)

    # Steps receding from bottom (widest, lightest) to top (narrowest, darkest)
    for i in range(step_count):
        inset_x = i * 3
        step_y = rect.bottom - (i + 1) * step_h
        step_w = rect.width - inset_x * 2
        shade_factor = i / max(step_count - 1, 1)
        step_color = tuple(int(c * (1.0 - shade_factor * 0.45)) for c in floor_color)
        step_rect = pygame.Rect(rect.x + inset_x, step_y, step_w, step_h - 1)
        pygame.draw.rect(surface, step_color, step_rect, border_radius=2)
        # Step edge (riser shadow)
        pygame.draw.line(surface, shadow_color, (rect.x + inset_x, step_y), (rect.x + inset_x + step_w, step_y), 1)

    # Direction arrow
    arrow_color = tuple(min(255, int(component + pulse * boost)) for component, boost in zip(colors["parchment"], (12, 10, 0)))
    cx, cy = rect.centerx, rect.centery
    if scene_name == "library":   # pointing down (into vault)
        pts = [(cx, cy + 6), (cx - 5, cy - 2), (cx + 5, cy - 2)]
    else:                          # pointing up (back to library)
        pts = [(cx, cy - 6), (cx - 5, cy + 2), (cx + 5, cy + 2)]
    pygame.draw.polygon(surface, arrow_color, pts)
    glow = pygame.Surface((rect.width + 18, rect.height + 18), pygame.SRCALPHA)
    glow_alpha = int(18 + pulse * 24)
    glow_color = colors["orange"] if scene_name == "library" else colors["parchment"]
    pygame.draw.rect(glow, (*glow_color, glow_alpha), (0, 0, glow.get_width(), glow.get_height()), 2, border_radius=10)
    surface.blit(glow, (rect.x - 9, rect.y - 9))

    # Outer border
    pygame.draw.rect(surface, shadow_color, rect, 2, border_radius=6)


def draw_bookshelf(surface, rect, colors, pygame) -> None:
    """Library bookshelf: warm wood case with a clearly readable dark-oak interior."""
    wood_dark = (52, 30, 12)
    wood_med = (118, 76, 38)
    wood_light = (164, 112, 62)
    # back_panel must be noticeably lighter than the scene bg (44,24,16) so the
    # shelf interior is visible.  (64,40,18) was too close to bg — use a warmer mid-brown.
    back_panel = (92, 62, 30)
    top_depth = 10
    side_w = 12
    inner = pygame.Rect(rect.x + side_w, rect.y + top_depth + 4, rect.width - 2 * side_w, rect.height - top_depth - 14)

    top = [
        (rect.x, rect.y + top_depth),
        (rect.right, rect.y + top_depth),
        (rect.right - 8, rect.y),
        (rect.x + 8, rect.y),
    ]
    # Fill the body first, then draw the 3-D top face so it isn't overwritten.
    pygame.draw.rect(surface, wood_med, rect, border_radius=4)
    pygame.draw.polygon(surface, wood_light, top)
    pygame.draw.rect(surface, back_panel, inner, border_radius=3)

    # Shelf boards — slightly taller (10 px) for better readability.
    shelf_levels = [inner.y + 26, inner.y + 56, inner.y + 86]
    for by in shelf_levels:
        pygame.draw.rect(surface, wood_light, (inner.x - 2, by, inner.width + 4, 10), border_radius=2)
        pygame.draw.line(surface, wood_dark, (inner.x - 2, by + 10), (inner.right + 2, by + 10), 2)

    pygame.draw.rect(surface, wood_med, (rect.x, rect.y + top_depth, side_w, rect.height - top_depth), border_radius=3)
    pygame.draw.rect(surface, wood_med, (rect.right - side_w, rect.y + top_depth, side_w, rect.height - top_depth), border_radius=3)
    pygame.draw.line(surface, wood_light, (rect.x + 3, rect.y + top_depth + 4), (rect.x + 3, rect.bottom - 8), 2)
    pygame.draw.line(surface, wood_dark, (rect.right - 2, rect.y + top_depth + 6), (rect.right - 2, rect.bottom - 8), 2)
    pygame.draw.rect(surface, wood_dark, rect, 2, border_radius=4)


def draw_vault_shelf(surface, rect, colors, pygame) -> None:
    """Vault archive shelf: stone-framed case with dark interior."""
    frame_dark = (24, 28, 36)
    frame_med = (48, 56, 70)
    frame_light = (68, 80, 98)
    interior = (14, 18, 26)
    side_w = 12
    board_h = 10

    # Dark interior
    inner = pygame.Rect(rect.x + side_w, rect.y + board_h, rect.width - 2 * side_w, rect.height - 2 * board_h)
    pygame.draw.rect(surface, interior, inner)

    # Mid divider (visual only — doesn't align to book rows)
    mid_y = rect.y + rect.height // 2 - 3
    pygame.draw.rect(surface, frame_med, (rect.x + side_w, mid_y, rect.width - 2 * side_w, 6))
    pygame.draw.line(surface, frame_light, (rect.x + side_w, mid_y), (rect.x + rect.width - side_w, mid_y), 1)

    # Side panels
    pygame.draw.rect(surface, frame_med, (rect.x, rect.y, side_w, rect.height))
    pygame.draw.rect(surface, frame_med, (rect.x + rect.width - side_w, rect.y, side_w, rect.height))
    # Depth highlight
    pygame.draw.line(surface, frame_light, (rect.x + 2, rect.y + board_h), (rect.x + 2, rect.y + rect.height - board_h), 2)
    # Top beam
    pygame.draw.rect(surface, frame_light, (rect.x, rect.y, rect.width, board_h))
    # Bottom base
    pygame.draw.rect(surface, frame_med, (rect.x, rect.y + rect.height - board_h, rect.width, board_h))
    # Outer frame
    pygame.draw.rect(surface, frame_dark, rect, 2, border_radius=2)


def draw_bulletin_board(surface, rect, colors, pygame) -> None:
    """System wall: cork bulletin board with wooden frame and corner pins."""
    frame_color = (82, 52, 24)
    frame_light = (112, 74, 36)
    cork_color = (210, 174, 112)
    cork_dark = (170, 132, 82)
    pin_color = (196, 56, 56)
    frame_w = 10

    # Wooden frame
    pygame.draw.rect(surface, frame_color, rect, border_radius=6)
    # Frame highlight (top and left edges)
    pygame.draw.rect(surface, frame_light, (rect.x + 2, rect.y + 2, rect.width - 4, frame_w - 2))
    pygame.draw.rect(surface, frame_light, (rect.x + 2, rect.y + 2, frame_w - 2, rect.height - 4))

    # Cork interior
    inner = pygame.Rect(rect.x + frame_w, rect.y + frame_w, rect.width - 2 * frame_w, rect.height - 2 * frame_w)
    pygame.draw.rect(surface, cork_color, inner, border_radius=4)
    # Subtle horizontal cork grain lines
    for i in range(0, inner.height, 5):
        pygame.draw.line(surface, cork_dark, (inner.x, inner.y + i), (inner.right, inner.y + i), 1)

    # Corner pins
    for px, py in [
        (rect.x + frame_w - 1, rect.y + frame_w - 1),
        (rect.right - frame_w + 1, rect.y + frame_w - 1),
        (rect.x + frame_w - 1, rect.bottom - frame_w + 1),
        (rect.right - frame_w + 1, rect.bottom - frame_w + 1),
    ]:
        pygame.draw.circle(surface, pin_color, (px, py), 4)
        pygame.draw.circle(surface, (240, 100, 100), (px, py), 2)

    # Outer shadow border
    pygame.draw.rect(surface, (22, 12, 6), rect, 3, border_radius=6)


def draw_bulletin_post(surface, rect, pygame) -> None:
    paper = pygame.Rect(rect.x + 1, rect.y + 2, rect.width - 2, rect.height - 3)
    pygame.draw.rect(surface, (236, 223, 198), paper, border_radius=3)
    pygame.draw.rect(surface, (141, 112, 82), paper, 1, border_radius=3)
    fold = [(paper.right - 7, paper.y), (paper.right, paper.y), (paper.right, paper.y + 7)]
    pygame.draw.polygon(surface, (214, 198, 170), fold)
    pygame.draw.line(surface, (164, 136, 102), (paper.right - 7, paper.y), (paper.right - 1, paper.y + 6), 1)
    pygame.draw.circle(surface, (188, 54, 50), (paper.centerx, paper.y + 2), 2)
    line_left = paper.x + 4
    line_right = paper.right - 5
    for offset in (9, 13, 17):
        pygame.draw.line(surface, (116, 96, 78), (line_left, paper.y + offset), (line_right, paper.y + offset), 1)


def draw_notice_board(surface, rect, colors, pygame) -> None:
    """Daemon notice board: narrow side-perspective board viewed from the top-right."""
    frame_dark = (44, 28, 14)
    frame_light = (72, 48, 22)
    felt_color = (44, 76, 56)
    felt_line = (56, 94, 68)
    top_depth = 8
    side_depth = 10
    front = pygame.Rect(rect.x, rect.y + top_depth, rect.width - side_depth, rect.height - top_depth)
    top = [
        (front.x, front.y),
        (front.right, front.y),
        (front.right + side_depth, front.y - top_depth),
        (front.x + side_depth, front.y - top_depth),
    ]
    side = [
        (front.right, front.y),
        (front.right + side_depth, front.y - top_depth),
        (front.right + side_depth, front.bottom - top_depth),
        (front.right, front.bottom),
    ]

    pygame.draw.rect(surface, frame_dark, front, border_radius=4)
    pygame.draw.polygon(surface, frame_light, top)
    pygame.draw.polygon(surface, (58, 36, 18), side)

    inner = pygame.Rect(front.x + 6, front.y + 6, front.width - 12, front.height - 12)
    pygame.draw.rect(surface, felt_color, inner, border_radius=3)
    for i in range(0, inner.height, 8):
        pygame.draw.line(surface, felt_line, (inner.x + 3, inner.y + i), (inner.right - 3, inner.y + i), 1)
    pygame.draw.rect(surface, (18, 10, 5), front, 2, border_radius=4)


def draw_daemon_npc(surface, rect, runtime, colors, tiny_font, pygame) -> None:
    """Reception counter with a small girl librarian NPC behind it."""
    shake_x = int(round(math.sin(runtime.anim_tick * math.pi * 5.5) * 0.8))
    shake_y = int(round(math.cos(runtime.anim_tick * math.pi * 6.2) * 0.8))
    # Use cached socket state — avoids 3–4 blocking socket calls per frame (was ~20 ms/frame).
    socket_state = getattr(runtime, "daemon_socket_state", "unconfigured")
    daemon_online = socket_state in {"online", "starting"}
    daemon_state = daemon_effective_state(runtime)

    # ── Counter / desk (lower 34px) ──────────────────────────────────────
    desk_h = 34
    desk_y = rect.y + rect.height - desk_h
    desk_top = (136, 98, 60)
    desk_face = (102, 68, 36)
    desk_shadow = (56, 34, 16)

    pygame.draw.rect(surface, desk_face, (rect.x, desk_y, rect.width, desk_h), border_radius=4)
    # Desk top surface
    pygame.draw.rect(surface, desk_top, (rect.x, desk_y, rect.width, 7), border_radius=2)
    pygame.draw.line(surface, desk_shadow, (rect.x, desk_y + 7), (rect.right, desk_y + 7), 1)
    pygame.draw.rect(surface, desk_shadow, (rect.x, desk_y, rect.width, desk_h), 2, border_radius=4)
    # Small item on the desk top (a scroll stub)
    pygame.draw.rect(surface, colors["parchment"], (rect.right - 38, desk_y + 2, 22, 5), border_radius=1)

    # Desk label
    lbl = tiny_font.render("Daemon Counter", True, colors["parchment"])
    surface.blit(lbl, (rect.x + 8, desk_y + 12))

    # ── Girl NPC (behind the counter) ───────────────────────────────────
    player_near_daemon = daemon_greeting_active(runtime, rect)
    cx = rect.centerx + shake_x

    skin  = (238, 198, 162)
    hair  = (68, 42, 20)
    dress_map = {
        "dormant": (108, 106, 108),
        "guiding": (78, 114, 166),
        "consulting": (162, 90, 132),
        "reviewing": (108, 138, 94),
        "sorting": (186, 126, 72),
        "reloading": (118, 116, 156),
        "idle": (162, 90, 132),
    }
    dress = dress_map.get(daemon_state, (162, 90, 132)) if daemon_online else (108, 106, 108)
    eye   = (48, 32, 22)
    body_w, body_h = 16, 26
    body_top = desk_y - 24 + shake_y
    body_bottom = desk_y + 10
    pygame.draw.rect(surface, dress, (cx - body_w // 2, body_top, body_w, body_h), border_radius=5)
    pygame.draw.rect(surface, (238, 228, 208), (cx - 3, body_top + 4, 6, 14), border_radius=2)

    # Arms — pose driven by daemon state and player proximity
    arm_y = body_top + 9
    if daemon_state == "dormant":
        # Slumped: arms hanging limp at sides
        pygame.draw.line(surface, dress, (cx - 7, arm_y), (cx - 10, desk_y + 4), 3)
        pygame.draw.line(surface, dress, (cx + 7, arm_y), (cx + 10, desk_y + 4), 3)
    elif daemon_state == "sorting":
        carry_dx, carry_dy = sorting_carry_offset(runtime.anim_tick)
        pygame.draw.line(surface, dress, (cx - 7, arm_y), (cx - 12, desk_y + 1), 4)
        pygame.draw.line(surface, dress, (cx + 7, arm_y), (cx + 13, arm_y - 6), 4)
        carry_rect = pygame.Rect(cx + 9 + carry_dx, arm_y - 9 + carry_dy, 16, 10)
        pygame.draw.rect(surface, colors["parchment"], carry_rect, border_radius=2)
        pygame.draw.line(surface, desk_shadow, (carry_rect.x + 3, carry_rect.y + 3), (carry_rect.right - 3, carry_rect.y + 3), 1)
        pygame.draw.line(surface, desk_shadow, (carry_rect.x + 3, carry_rect.y + 6), (carry_rect.right - 5, carry_rect.y + 6), 1)
    elif daemon_state == "consulting" or (daemon_state == "idle" and player_near_daemon):
        # Stamping or waving: one arm raised — greeting visitor or stamping a scroll
        pygame.draw.line(surface, dress, (cx - 7, arm_y), (cx - 11, desk_y + 2), 4)
        pygame.draw.line(surface, dress, (cx + 7, arm_y), (cx + 15, arm_y - 9), 4)
        pygame.draw.rect(surface, desk_face, (cx + 13, arm_y - 14, 8, 7), border_radius=2)
    elif daemon_state in {"reloading", "reviewing"}:
        # Reading: both arms in, holding a scroll between hands
        pygame.draw.line(surface, dress, (cx - 7, arm_y), (cx - 16, arm_y + 7), 4)
        pygame.draw.line(surface, dress, (cx + 7, arm_y), (cx + 16, arm_y + 7), 4)
        pygame.draw.rect(surface, colors["parchment"], (cx - 14, arm_y + 5, 28, 10), border_radius=2)
    elif daemon_state == "guiding":
        # Pointing: left arm at side, right arm extended with glowing tip
        pygame.draw.line(surface, dress, (cx - 7, arm_y), (cx - 11, desk_y + 2), 4)
        pygame.draw.line(surface, dress, (cx + 7, arm_y), (cx + 18, arm_y - 4), 4)
        pygame.draw.circle(surface, colors["highlight"], (cx + 21, arm_y - 5), 4)
    else:
        # Idle reviewing: scroll on desk
        pygame.draw.line(surface, dress, (cx - 7, arm_y), (cx - 5, desk_y + 5), 4)
        pygame.draw.line(surface, dress, (cx + 7, arm_y), (cx + 5, desk_y + 5), 4)
        pygame.draw.rect(surface, colors["parchment"], (cx - 9, desk_y - 1, 18, 9), border_radius=2)

    # Head
    head_r = 10
    head_cy = body_top - head_r + 8
    pygame.draw.circle(surface, skin, (cx, head_cy), head_r)
    # Hair (arc over top half of head)
    pygame.draw.ellipse(surface, hair, pygame.Rect(cx - head_r, head_cy - head_r, head_r * 2, head_r))
    # Side strands
    pygame.draw.rect(surface, hair, (cx - head_r, head_cy - 3, 4, head_r + 2), border_radius=1)
    pygame.draw.rect(surface, hair, (cx + head_r - 4, head_cy - 3, 4, head_r + 2), border_radius=1)
    # Eyes
    pygame.draw.circle(surface, eye, (cx - 3, head_cy + 2), 2)
    pygame.draw.circle(surface, eye, (cx + 3, head_cy + 2), 2)
    pygame.draw.circle(surface, (255, 255, 255), (cx - 2, head_cy + 1), 1)


def daemon_greeting_active(runtime, daemon_rect) -> bool:
    px = runtime.player.x + runtime.player.width / 2
    py = runtime.player.y + runtime.player.height / 2
    distance = rect_edge_distance(px, py, (daemon_rect.x, daemon_rect.y, daemon_rect.width, daemon_rect.height))
    return distance <= 128


def sorting_carry_offset(anim_tick: float) -> tuple[int, int]:
    return (
        int(round(math.sin(anim_tick * math.pi * 1.8) * 5)),
        int(round(math.cos(anim_tick * math.pi * 1.3) * 2)),
    )


def portal_pulse_strength(scene_name: str, anim_tick: float) -> float:
    wave = (math.sin(anim_tick * math.pi * 1.35) + 1.0) / 2.0
    base = 0.46 if scene_name == "library" else 0.28
    amplitude = 0.34 if scene_name == "library" else 0.24
    return base + wave * amplitude
    pygame.draw.circle(surface, (255, 255, 255), (cx + 4, head_cy + 1), 1)
    # Smile — flat/neutral when dormant, curved when active
    if daemon_state == "dormant":
        pygame.draw.line(surface, (172, 114, 84), (cx - 3, head_cy + 5), (cx + 3, head_cy + 5), 1)
    else:
        pygame.draw.arc(surface, (172, 114, 84),
                        pygame.Rect(cx - 4, head_cy + 2, 8, 5),
                        math.pi, 2 * math.pi, 1)

    # Repaint counter front so the daemon reads as standing behind it up to the waist.
    pygame.draw.rect(surface, desk_face, (rect.x, desk_y, rect.width, desk_h), border_radius=4)
    pygame.draw.rect(surface, desk_top, (rect.x, desk_y, rect.width, 7), border_radius=2)
    pygame.draw.line(surface, desk_shadow, (rect.x, desk_y + 7), (rect.right, desk_y + 7), 1)
    pygame.draw.rect(surface, desk_shadow, (rect.x, desk_y, rect.width, desk_h), 2, border_radius=4)
    pygame.draw.rect(surface, colors["parchment"], (rect.right - 38, desk_y + 2, 22, 5), border_radius=1)
    surface.blit(lbl, (rect.x + 8, desk_y + 12))

    focused_title = None
    if runtime.focused_entry_id:
        focused = runtime.indexes.note_by_id.get(runtime.focused_entry_id)
        focused_title = focused.title if focused is not None else None
    status = daemon_status_line(runtime.settings.workspace_root, runtime.snapshot, focused_title, socket_state=socket_state)
    status = daemon_state_label(runtime, status)
    bubble = tiny_font.render(status[:30], True, colors["shadow"])
    bubble_rect = bubble.get_rect()
    bubble_rect.midbottom = (cx, rect.y - 6)
    bubble_bg = bubble_rect.inflate(12, 10)
    pygame.draw.rect(surface, colors["parchment"], bubble_bg, border_radius=8)
    pygame.draw.rect(surface, colors["shadow"], bubble_bg, 1, border_radius=8)
    surface.blit(bubble, bubble_rect.topleft)


def section_trust_aura(section_key: str, snapshot) -> tuple[int, int, int] | None:
    """Return a border color for a shelf based on its dominant trust/freshness state, or None."""
    entries = [e for e in snapshot.note_entries if e.section_key == section_key]
    if not entries:
        return None
    if any(e.lifecycle_state == "contested" for e in entries):
        return (176, 76, 68)   # any contested → red warning ring
    total = len(entries)
    current_count = sum(1 for e in entries if freshness_label(e.updated_at) == "current")
    stale_count = sum(1 for e in entries if freshness_label(e.updated_at) == "stale")
    if stale_count / total > 0.6:
        return (100, 100, 100)   # mostly stale → gray ring
    if current_count / total > 0.5:
        return (210, 185, 110)   # mostly current + trusted → warm gold ring
    return None   # mixed / unknown: no ring


def section_stale_ratio(section_key: str, snapshot) -> float:
    """Return the fraction of entries in a section that are stale or archived."""
    entries = [e for e in snapshot.note_entries if e.section_key == section_key]
    if not entries:
        return 0.0
    stale = sum(
        1 for e in entries
        if freshness_label(e.updated_at) == "stale" or e.lifecycle_state == "archived"
    )
    return stale / len(entries)


def draw_construction_shelf(surface, rect, colors, pygame) -> None:
    pygame.draw.rect(surface, (86, 82, 78), rect, border_radius=8)
    pygame.draw.rect(surface, colors["shadow"], rect, 2, border_radius=8)
    fence_y = rect.y + rect.height // 2
    post_positions = [rect.x + 18, rect.x + rect.width // 2, rect.x + rect.width - 18]
    for post_x in post_positions:
        pygame.draw.rect(surface, colors["parchment"], (post_x - 3, fence_y - 18, 6, 36), border_radius=2)
    pygame.draw.line(surface, colors["orange"], (rect.x + 8, fence_y - 10), (rect.x + rect.width - 8, fence_y + 10), 5)
    pygame.draw.line(surface, colors["orange"], (rect.x + 8, fence_y + 10), (rect.x + rect.width - 8, fence_y - 10), 5)
    pygame.draw.line(surface, colors["shadow"], (rect.x + 8, fence_y - 10), (rect.x + rect.width - 8, fence_y + 10), 2)
    pygame.draw.line(surface, colors["shadow"], (rect.x + 8, fence_y + 10), (rect.x + rect.width - 8, fence_y - 10), 2)


def pile_slots(total: int) -> list[int]:
    if total <= 0:
        return []
    return list(range(_smallest_triangular_base(total), 0, -1))


def decompose_triangular_piles(total: int) -> list[int]:
    if total <= 0:
        return []
    triangulars = []
    base = 1
    while triangular_number(base) <= total:
        triangulars.append((base, triangular_number(base)))
        base += 1
    best: list[int] | None = None

    def search(remaining: int, start_index: int, picked: list[int]) -> None:
        nonlocal best
        if remaining == 0:
            candidate = list(picked)
            if best is None or len(candidate) < len(best) or (len(candidate) == len(best) and candidate > best):
                best = candidate
            return
        if best is not None and len(picked) >= len(best):
            return
        for index in range(start_index, -1, -1):
            base_value, tri_value = triangulars[index]
            if tri_value > remaining:
                continue
            picked.append(base_value)
            search(remaining - tri_value, index, picked)
            picked.pop()

    search(total, len(triangulars) - 1, [])
    return best or [_smallest_triangular_base(total)]


def triangular_number(base: int) -> int:
    return base * (base + 1) // 2


def _smallest_triangular_base(total: int) -> int:
    base = 1
    while triangular_number(base) < total:
        base += 1
    return base
