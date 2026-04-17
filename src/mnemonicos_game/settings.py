from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_TILE_SIZE = 32
DEFAULT_FPS = 60
DEFAULT_WINDOW_WIDTH = 1280
DEFAULT_WINDOW_HEIGHT = 720


@dataclass(slots=True)
class GameSettings:
    workspace_root: Path
    tile_size: int = DEFAULT_TILE_SIZE
    fps: int = DEFAULT_FPS
    window_width: int = DEFAULT_WINDOW_WIDTH
    window_height: int = DEFAULT_WINDOW_HEIGHT

    @property
    def brain_root(self) -> Path:
        return self.workspace_root / "brain"

    @property
    def title(self) -> str:
        return "MnemonicOS Library RPG"
