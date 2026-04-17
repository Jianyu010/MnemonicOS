from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pygame


PALETTE = {
    "bg": "#2C1810",
    "wall": "#7B4A2D",
    "floor": "#D4956A",
    "parchment": "#F7E8C0",
    "blue": "#4A90D9",
    "orange": "#E8A020",
    "green": "#5CB85C",
    "shadow": "#1C120C",
    "text": "#F7E8C0",
    "muted": "#C8B089",
    "vault_floor": "#2C344C",
    "vault_wall": "#1F2638",
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
