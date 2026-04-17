from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .palette import color

if TYPE_CHECKING:
    import pygame


@dataclass(slots=True)
class AssetBundle:
    player_frames: dict[str, list["pygame.Surface"]] = field(default_factory=dict)
    daemon_frames: list["pygame.Surface"] = field(default_factory=list)
    portal_frames: list["pygame.Surface"] = field(default_factory=list)
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
    bundle.player_frames = _build_player_frames(pygame)
    bundle.daemon_frames = _build_daemon_frames(pygame)
    bundle.portal_frames = _build_portal_frames(pygame)
    bundle.sounds = _load_sounds(base / "sounds", pygame)
    bundle.music = {
        "library": str(base / "sounds" / "library_theme.ogg"),
        "vault": str(base / "sounds" / "vault_theme.ogg"),
    }
    bundle.vignette = _build_vignette_surface(pygame, 1280, 800)
    return bundle


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


def _build_floor_tile(pygame):
    surface = pygame.Surface((32, 32), pygame.SRCALPHA)
    surface.fill(color("floor"))
    pygame.draw.polygon(surface, color("wall"), [(16, 10), (21, 16), (16, 22), (11, 16)])
    pygame.draw.rect(surface, (255, 255, 255, 20), (0, 0, 32, 32), 1)
    return surface


def _build_wall_tile(pygame):
    surface = pygame.Surface((32, 32), pygame.SRCALPHA)
    surface.fill(color("wall"))
    pygame.draw.rect(surface, color("shadow"), (0, 24, 32, 8))
    pygame.draw.rect(surface, color("parchment"), (0, 0, 32, 4))
    pygame.draw.line(surface, color("floor"), (0, 14), (32, 14), 2)
    return surface


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


def _build_player_frames(pygame):
    frames: dict[str, list["pygame.Surface"]] = {}
    for facing in ("north", "south", "east", "west"):
        frames[facing] = [_build_player_frame(pygame, facing, step) for step in range(2)]
    return frames


def _build_player_frame(pygame, facing: str, step: int):
    surface = pygame.Surface((28, 28), pygame.SRCALPHA)
    pygame.draw.rect(surface, color("blue"), (4, 4, 20, 20), border_radius=8)
    pygame.draw.rect(surface, color("parchment"), (8, 8 + (step % 2), 12, 6), border_radius=3)
    if facing == "north":
        marker = pygame.Rect(10, 4, 8, 4)
    elif facing == "south":
        marker = pygame.Rect(10, 20, 8, 4)
    elif facing == "west":
        marker = pygame.Rect(4, 10, 4, 8)
    else:
        marker = pygame.Rect(20, 10, 4, 8)
    pygame.draw.rect(surface, color("parchment"), marker, border_radius=2)
    return surface


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
