from __future__ import annotations

from dataclasses import dataclass

from mnemonicos_game.scene_helpers import draw_scene, update_focus, update_player
from .base import BaseScene


@dataclass(slots=True)
class DungeonScene(BaseScene):
    name: str = "vault"

    def update(self, runtime, dt: float, pygame) -> None:
        update_player(runtime, dt, pygame)
        update_focus(runtime)

    def draw(self, surface, runtime, font, small_font, tiny_font, pygame) -> None:
        draw_scene(surface, runtime, font, small_font, tiny_font, pygame)
