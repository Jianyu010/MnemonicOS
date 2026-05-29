from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .palette import color

if TYPE_CHECKING:
    import pygame


# Default skin colours — used at first load and when no skin is set.
DEFAULT_CLOAK: tuple[int, int, int] = (72, 120, 192)
DEFAULT_TUNIC: tuple[int, int, int] = (242, 231, 214)

# Preset skin options exposed to the skin-picker overlay.
SKIN_CLOAK_PRESETS: list[tuple[str, tuple[int, int, int]]] = [
    ("Blue",    (72,  120, 192)),
    ("Crimson", (180,  48,  40)),
    ("Forest",  ( 48, 140,  72)),
    ("Violet",  (128,  64, 160)),
    ("Gold",    (180, 148,  40)),
    ("Slate",   (100, 110, 120)),
]

SKIN_TUNIC_PRESETS: list[tuple[str, tuple[int, int, int]]] = [
    ("Cream",   (242, 231, 214)),
    ("Leather", (162, 108,  64)),
    ("White",   (240, 240, 232)),
]


@dataclass(slots=True)
class AssetBundle:
    player_frames: dict[str, list["pygame.Surface"]] = field(default_factory=dict)
    daemon_frames: list["pygame.Surface"] = field(default_factory=list)
    portal_frames: list["pygame.Surface"] = field(default_factory=list)
    torch_frames: list["pygame.Surface"] = field(default_factory=list)
    scroll_sprites: dict[str, "pygame.Surface"] = field(default_factory=dict)
    tile_floor: "pygame.Surface | None" = None
    tile_wall: "pygame.Surface | None" = None
    sounds: dict[str, "pygame.mixer.Sound"] = field(default_factory=dict)
    music: dict[str, str] = field(default_factory=dict)
    vignette: "pygame.Surface | None" = None


def load_assets(workspace_root: Path, pygame) -> AssetBundle:
    base = workspace_root / "src" / "mnemonicos_game" / "assets"
    bundle = AssetBundle()
    bundle.tile_floor = _build_floor_tile(pygame)
    bundle.tile_wall = _build_wall_tile(pygame)
    bundle.scroll_sprites = _build_scroll_surfaces(pygame)
    bundle.player_frames = build_player_frames_for_skin(pygame, DEFAULT_CLOAK, DEFAULT_TUNIC)
    bundle.daemon_frames = _build_daemon_frames(pygame)
    bundle.portal_frames = _build_portal_frames(pygame)
    bundle.torch_frames = _build_torch_frames(pygame)
    bundle.sounds = _load_sounds(base / "sounds", pygame)
    bundle.music = {
        "library": str(base / "sounds" / "library_theme.ogg"),
        "vault": str(base / "sounds" / "vault_theme.ogg"),
    }
    bundle.vignette = _build_vignette_surface(pygame, 1280, 800)
    return bundle


# ── Public: called at runtime to rebuild player sprites after skin change ────

def build_player_frames_for_skin(
    pygame,
    cloak: tuple[int, int, int],
    tunic: tuple[int, int, int],
) -> dict[str, list["pygame.Surface"]]:
    frames: dict[str, list["pygame.Surface"]] = {}
    for facing in ("north", "south", "east", "west"):
        frames[facing] = [_build_player_frame(pygame, facing, step, cloak, tunic) for step in range(2)]
    return frames


# ── Vignette ─────────────────────────────────────────────────────────────────

def _build_vignette_surface(pygame, width: int, height: int):
    surface = pygame.Surface((width, height), pygame.SRCALPHA)
    for inset in range(0, 72, 8):
        alpha = min(120, 12 + inset)
        pygame.draw.rect(
            surface,
            (20, 12, 8, alpha),
            (inset // 2, inset // 2, width - inset, height - inset),
            width=8,
            border_radius=18,
        )
    return surface


# ── Floor / wall tiles ───────────────────────────────────────────────────────

def _build_floor_tile(pygame):
    """32×32 horizontal plank tile for the library floor."""
    surface = pygame.Surface((32, 32), pygame.SRCALPHA)
    floor_light = color("floor_light")
    floor_dark  = color("floor_dark")
    shadow      = color("shadow")
    plank_h = 8
    for row in range(4):
        y = row * plank_h
        base = floor_light if row % 2 == 0 else floor_dark
        # Subtle per-plank jitter
        jitter = ((row * 13) % 16) - 8
        plank_color = tuple(max(0, min(255, c + jitter)) for c in base)
        pygame.draw.rect(surface, plank_color, (0, y, 32, plank_h))
        # Mortar line
        pygame.draw.line(surface, shadow, (0, y), (32, y), 1)
    # Light grain stroke
    pygame.draw.line(surface, floor_light, (8, 1), (8, 31), 1)
    pygame.draw.line(surface, floor_light, (22, 1), (22, 31), 1)
    return surface


def _build_wall_tile(pygame):
    surface = pygame.Surface((32, 32), pygame.SRCALPHA)
    surface.fill(color("wall"))
    pygame.draw.rect(surface, color("shadow"), (0, 24, 32, 8))
    pygame.draw.rect(surface, color("parchment"), (0, 0, 32, 4))
    pygame.draw.line(surface, color("floor_dark"), (0, 14), (32, 14), 2)
    return surface


# ── Scroll sprites ────────────────────────────────────────────────────────────

def _build_scroll_surfaces(pygame):
    mapping = {}
    for lifecycle in [None, "draft", "review", "verified", "canonical", "contested", "archived"]:
        surface = pygame.Surface((26, 34), pygame.SRCALPHA)
        surface.fill((0, 0, 0, 0))
        body = pygame.Rect(2, 2, 22, 30)
        pygame.draw.rect(surface, color("parchment"), body, border_radius=4)
        pygame.draw.rect(surface, color("wall"), body, 2, border_radius=4)
        if lifecycle == "draft":
            pygame.draw.line(surface, color("wall"), (5, 8), (20, 5), 2)
        elif lifecycle == "review":
            pygame.draw.line(surface, color("orange"), (13, 4), (13, 30), 3)
        elif lifecycle == "verified":
            pygame.draw.circle(surface, color("green"), (19, 8), 4)
        elif lifecycle == "canonical":
            pygame.draw.circle(surface, color("orange"), (19, 8), 5)
        elif lifecycle == "contested":
            pygame.draw.line(surface, (170, 70, 70), (5, 7), (20, 28), 2)
            pygame.draw.line(surface, (170, 70, 70), (5, 28), (20, 7), 2)
        elif lifecycle == "archived":
            for x in (6, 11, 16):
                pygame.draw.circle(surface, (130, 120, 100), (x, 6), 1)
        mapping[str(lifecycle)] = surface
    return mapping


# ── Player sprite (32×48) ─────────────────────────────────────────────────────

def _build_player_frame(
    pygame,
    facing: str,
    step: int,
    cloak: tuple[int, int, int] = DEFAULT_CLOAK,
    tunic: tuple[int, int, int] = DEFAULT_TUNIC,
):
    """32×48 RPG character with distinct head / torso / legs."""
    surface = pygame.Surface((32, 48), pygame.SRCALPHA)

    skin  = (230, 188, 152)
    hair  = (78, 50, 24)
    boots = (72, 48, 28)
    belt  = (60, 38, 18)
    cloak_dark = tuple(max(0, c - 40) for c in cloak)
    stride = 3 if step % 2 else 0

    cx = 16  # horizontal centre

    # ── Legs ────────────────────────────────────────────────────────────────
    if facing == "south":
        pygame.draw.rect(surface, cloak_dark, (cx - 8, 32, 7, 10 + stride), border_radius=2)
        pygame.draw.rect(surface, cloak_dark, (cx + 1, 32, 7, 10 - stride), border_radius=2)
        pygame.draw.rect(surface, boots, (cx - 8, 40 + stride, 7, 6 - stride), border_radius=1)
        pygame.draw.rect(surface, boots, (cx + 1, 40 - stride, 7, 6 + stride), border_radius=1)
    elif facing == "north":
        pygame.draw.rect(surface, cloak_dark, (cx - 7, 32, 6, 10 - stride), border_radius=2)
        pygame.draw.rect(surface, cloak_dark, (cx + 1, 32, 6, 10 + stride), border_radius=2)
        pygame.draw.rect(surface, boots, (cx - 7, 40 - stride, 6, 6 + stride), border_radius=1)
        pygame.draw.rect(surface, boots, (cx + 1, 40 + stride, 6, 6 - stride), border_radius=1)
    else:  # east / west
        pygame.draw.rect(surface, cloak_dark, (cx - 5, 32, 6, 10), border_radius=2)
        pygame.draw.rect(surface, cloak_dark, (cx + 1, 32, 6, 10), border_radius=2)
        pygame.draw.rect(surface, boots, (cx - 5, 40, 6, 6), border_radius=1)
        pygame.draw.rect(surface, boots, (cx + 1, 40 + stride, 6, 6 - stride), border_radius=1)

    # ── Belt ─────────────────────────────────────────────────────────────────
    pygame.draw.rect(surface, belt, (cx - 8, 31, 16, 3))

    # ── Torso / cloak ─────────────────────────────────────────────────────────
    pygame.draw.rect(surface, cloak, (cx - 9, 16, 18, 16), border_radius=4)
    # Tunic panel (front)
    pygame.draw.rect(surface, tunic, (cx - 4, 17, 8, 11), border_radius=2)
    # Collar
    pygame.draw.rect(surface, tunic, (cx - 3, 15, 6, 4), border_radius=1)

    # ── Arms ──────────────────────────────────────────────────────────────────
    if facing == "south":
        pygame.draw.line(surface, cloak, (cx - 9, 18), (cx - 13, 27 - stride), 4)
        pygame.draw.line(surface, cloak, (cx + 9, 18), (cx + 13, 27 + stride), 4)
        pygame.draw.circle(surface, skin, (cx - 13, 27 - stride), 3)
        pygame.draw.circle(surface, skin, (cx + 13, 27 + stride), 3)
    elif facing == "north":
        pygame.draw.line(surface, cloak, (cx - 9, 18), (cx - 12, 28 + stride), 4)
        pygame.draw.line(surface, cloak, (cx + 9, 18), (cx + 12, 28 - stride), 4)
    elif facing == "east":
        pygame.draw.line(surface, cloak, (cx - 9, 20), (cx - 11, 28), 3)
        pygame.draw.line(surface, cloak, (cx + 9, 18), (cx + 14, 24 - stride), 4)
        pygame.draw.circle(surface, skin, (cx + 14, 24 - stride), 3)
    else:  # west
        pygame.draw.line(surface, cloak, (cx + 9, 20), (cx + 11, 28), 3)
        pygame.draw.line(surface, cloak, (cx - 9, 18), (cx - 14, 24 - stride), 4)
        pygame.draw.circle(surface, skin, (cx - 14, 24 - stride), 3)

    # ── Head ──────────────────────────────────────────────────────────────────
    pygame.draw.circle(surface, skin, (cx, 9), 8)
    # Hair
    pygame.draw.ellipse(surface, hair, pygame.Rect(cx - 8, 1, 16, 10))
    # Side hair strands
    pygame.draw.rect(surface, hair, (cx - 8, 5, 4, 7), border_radius=1)
    pygame.draw.rect(surface, hair, (cx + 4, 5, 4, 7), border_radius=1)
    # Eyes (direction-dependent)
    eye_y = 10
    if facing == "south":
        pygame.draw.circle(surface, (48, 32, 20), (cx - 3, eye_y), 2)
        pygame.draw.circle(surface, (48, 32, 20), (cx + 3, eye_y), 2)
        pygame.draw.circle(surface, (255, 255, 255), (cx - 2, eye_y - 1), 1)
        pygame.draw.circle(surface, (255, 255, 255), (cx + 4, eye_y - 1), 1)
        # Mouth
        pygame.draw.line(surface, (160, 100, 80), (cx - 2, 13), (cx + 2, 13), 1)
    elif facing == "north":
        # Only hair visible from behind
        pass
    elif facing == "east":
        pygame.draw.circle(surface, (48, 32, 20), (cx + 4, eye_y), 2)
        pygame.draw.circle(surface, (255, 255, 255), (cx + 5, eye_y - 1), 1)
    else:  # west
        pygame.draw.circle(surface, (48, 32, 20), (cx - 4, eye_y), 2)
        pygame.draw.circle(surface, (255, 255, 255), (cx - 5, eye_y - 1), 1)

    return surface


# ── Daemon NPC frames ─────────────────────────────────────────────────────────

def _build_daemon_frames(pygame):
    frames = []
    for index in range(4):
        surface = pygame.Surface((28, 36), pygame.SRCALPHA)
        pygame.draw.rect(surface, (165, 125, 75), (6, 8, 16, 20), border_radius=8)
        pygame.draw.rect(surface, color("orange"), (7, 6 + (index % 2), 14, 6), border_radius=3)
        pygame.draw.rect(surface, color("parchment"), (8, 14, 12, 3), border_radius=2)
        if index >= 2:
            pygame.draw.line(surface, color("parchment"), (20, 16), (25, 11 + index), 2)
        frames.append(surface)
    return frames


# ── Portal frames ──────────────────────────────────────────────────────────────

def _build_portal_frames(pygame):
    frames = []
    for index in range(6):
        surface = pygame.Surface((36, 36), pygame.SRCALPHA)
        outer = [(18, 2 + index % 2), (33, 18), (18, 34 - index % 2), (3, 18)]
        inner = [(18, 8), (28 - index % 2, 18), (18, 28), (8 + index % 2, 18)]
        pygame.draw.polygon(surface, color("orange"), outer, 3)
        pygame.draw.polygon(surface, color("parchment"), inner, 2)
        pygame.draw.circle(surface, color("green"), (18, 18), 2 + (index % 2))
        frames.append(surface)
    return frames


# ── Torch frames (4-frame flicker animation) ──────────────────────────────────

def _build_torch_frames(pygame):
    """Wall torch: 16×28 surface with animated flame on a stick."""
    frames = []
    for frame_index in range(4):
        surface = pygame.Surface((16, 28), pygame.SRCALPHA)
        stick_color = (100, 64, 28)
        # Stick
        pygame.draw.rect(surface, stick_color, (6, 12, 4, 16), border_radius=1)
        # Torch head holder
        pygame.draw.rect(surface, (80, 52, 20), (4, 9, 8, 6), border_radius=2)

        # Flame layers — flicker via frame_index
        flicker = [0, 1, -1, 0][frame_index]
        flame_h = 10 + flicker
        # Outer glow (large, transparent)
        glow_surf = pygame.Surface((14, flame_h + 4), pygame.SRCALPHA)
        glow_surf.fill((0, 0, 0, 0))
        pygame.draw.ellipse(glow_surf, (255, 120, 20, 60), (0, 0, 14, flame_h + 4))
        surface.blit(glow_surf, (1, 8 - flame_h // 2))
        # Mid flame
        pygame.draw.ellipse(surface, (255, 160, 40), (4, 9 - flame_h, 8, flame_h))
        # Inner core
        pygame.draw.ellipse(surface, (255, 230, 100), (5, 10 - flame_h, 6, flame_h - 2))
        # Tip spark
        pygame.draw.circle(surface, (255, 255, 180), (8, 9 - flame_h), 2)
        frames.append(surface)
    return frames


# ── Sound loader ──────────────────────────────────────────────────────────────

def _load_sounds(base: Path, pygame):
    sounds = {}
    for name in (
        "inspect_open",
        "inspect_close",
        "bookmark",
        "menu_move",
        "menu_confirm",
        "portal_enter",
        "daemon_greet",
        "step",
        "jump_source",
    ):
        path = base / f"{name}.ogg"
        if not path.exists():
            continue
        try:
            sounds[name] = pygame.mixer.Sound(str(path))
        except Exception:
            continue
    return sounds
