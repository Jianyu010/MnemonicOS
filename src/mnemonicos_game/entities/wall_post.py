from __future__ import annotations

from dataclasses import dataclass

from mnemonicos_game.data.models import GameEntry


@dataclass(slots=True)
class WallPostEntity:
    entry: GameEntry
