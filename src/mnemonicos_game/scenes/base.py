from __future__ import annotations

from dataclasses import dataclass

from mnemonicos_game.runtime import GameRuntime


@dataclass(slots=True)
class BaseScene:
    name: str

    def update(self, runtime: GameRuntime, dt: float, pygame) -> None:
        raise NotImplementedError

    def draw(self, surface, runtime: GameRuntime, font, small_font, tiny_font, pygame) -> None:
        raise NotImplementedError
