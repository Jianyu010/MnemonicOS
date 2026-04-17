from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class WorldWatcher:
    workspace_root: Path
    last_signature: tuple[tuple[str, int], ...] = ()

    def snapshot_signature(self) -> tuple[tuple[str, int], ...]:
        brain_root = self.workspace_root / "brain"
        paths: list[tuple[str, int]] = []
        if not brain_root.exists():
            return ()
        for path in sorted(brain_root.rglob("*.md")):
            if path.is_file():
                paths.append((str(path.relative_to(self.workspace_root)), path.stat().st_mtime_ns))
        log_path = brain_root / "logs" / "daemon.log"
        if log_path.exists():
            paths.append((str(log_path.relative_to(self.workspace_root)), log_path.stat().st_mtime_ns))
        return tuple(paths)

    def prime(self) -> tuple[tuple[str, int], ...]:
        self.last_signature = self.snapshot_signature()
        return self.last_signature

    def poll_changed(self) -> bool:
        current = self.snapshot_signature()
        changed = current != self.last_signature
        self.last_signature = current
        return changed
