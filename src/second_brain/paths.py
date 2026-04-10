from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


SYSTEM_FILES = ("MEMORY.md", "USER.md", "ACTIVE.md", "AGENTS.md")
EXCLUDED_WIKI_FILES = {"index.md", "log.md"}
EXCLUDED_WIKI_DIRS = {"review"}


@dataclass(slots=True)
class VaultPaths:
    workspace_root: Path
    vault_root: Path

    @property
    def system_dir(self) -> Path:
        return self.vault_root / "system"

    @property
    def wiki_dir(self) -> Path:
        return self.vault_root / "wiki"

    @property
    def raw_dir(self) -> Path:
        return self.vault_root / "raw"

    def pinned_files(self) -> list[Path]:
        return [self.system_dir / name for name in ("MEMORY.md", "USER.md", "ACTIVE.md")]

    def system_files(self) -> list[Path]:
        return [self.system_dir / name for name in SYSTEM_FILES]

    def note_files(self) -> list[Path]:
        files: list[Path] = []
        for path in self.wiki_dir.rglob("*.md"):
            relative = path.relative_to(self.wiki_dir)
            if relative.parts and relative.parts[0] in EXCLUDED_WIKI_DIRS:
                continue
            if relative.as_posix() in EXCLUDED_WIKI_FILES:
                continue
            if path.name in EXCLUDED_WIKI_FILES:
                continue
            files.append(path)
        return sorted(files)
