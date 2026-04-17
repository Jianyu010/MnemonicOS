from __future__ import annotations

from dataclasses import dataclass

from mnemonicos_game.data.models import GameBook


@dataclass(slots=True)
class BookEntity:
    book: GameBook
