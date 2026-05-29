from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pygame


PALETTE = {
    # ── Library (warm amber) ─────────────────────────────────────────────────
    "bg":            "#2A1A0E",   # deep warm brown background
    "wall_deep":     "#140C04",   # far back wall — darkest receding plane
    "wall_mid":      "#1E1008",   # mid back wall
    "wall":          "#7B4A2D",   # side wall / general wood tone
    "floor_light":   "#B8824A",   # plank highlight band
    "floor_dark":    "#8C5E30",   # plank shadow band
    "floor":         "#A07040",   # kept for compatibility
    "pillar_light":  "#E0C490",   # pillar highlight face
    "pillar_mid":    "#C8A870",   # pillar centre face
    "pillar_dark":   "#9A7040",   # pillar shadow face
    "pillar_cap":    "#D4B880",   # pillar cap/base slab
    "parchment":     "#F7E8C0",
    "blue":          "#4A90D9",
    "orange":        "#E8A020",
    "green":         "#5CB85C",
    "shadow":        "#1C120C",
    "text":          "#F7E8C0",
    "muted":         "#C8B089",
    "candle_glow":   "#FFA028",   # torch / candle flame tint
    # ── Vault (cool slate — unchanged) ───────────────────────────────────────
    "vault_floor":   "#2C344C",
    "vault_wall":    "#1F2638",
    # ── Constellation ray system ─────────────────────────────────────────────
    "ray_primary":   "#A0C8FF",   # main ray line
    "ray_secondary": "#7AAAE0",   # secondary ray line
    "ray_star":      "#C0E0FF",   # star node fill
    "ray_dot":       "#E8F4FF",   # trail dot fill
}


def color(name: str) -> tuple[int, int, int]:
    return hex_to_rgb(PALETTE[name])


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def recolor_surface(surface: "pygame.Surface", color_map: dict[tuple[int, int, int], tuple[int, int, int]]) -> "pygame.Surface":
    out = surface.copy().convert_alpha()
    try:
        import pygame.surfarray
    except ModuleNotFoundError:
        return out
    pixels = pygame.surfarray.pixels3d(out)
    for source, target in color_map.items():
        mask = (pixels[:, :, 0] == source[0]) & (pixels[:, :, 1] == source[1]) & (pixels[:, :, 2] == source[2])
        pixels[mask] = target
    del pixels
    return out
