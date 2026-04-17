from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class GameEntry:
    note_id: str
    note_type: str
    title: str
    body_path: Path
    zone_key: str
    section_key: str
    section_label: str
    source_dir: str
    confidence: str | None
    lifecycle_state: str | None
    updated_at: str | None
    source_refs: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass(slots=True)
class GameBook:
    title: str
    body_path: Path
    zone_key: str
    section_key: str
    section_label: str
    source_dir: str
    agent_label: str
    month_label: str
    session_slug: str


@dataclass(slots=True)
class ZoneSection:
    key: str
    label: str
    source_dir: str
    entries: list[GameEntry] = field(default_factory=list)
    books: list[GameBook] = field(default_factory=list)
    reserved: bool = False
    under_construction: bool = False

    @property
    def item_count(self) -> int:
        return len(self.entries) + len(self.books)


@dataclass(slots=True)
class WorldZone:
    key: str
    label: str
    sections: list[ZoneSection] = field(default_factory=list)

    @property
    def item_count(self) -> int:
        return sum(section.item_count for section in self.sections)


@dataclass(slots=True)
class WorldSnapshot:
    workspace_root: Path
    brain_root: Path
    note_entries: list[GameEntry]
    session_books: list[GameBook]
    system_files: list[Path]
    zones: list[WorldZone]

    def zone(self, key: str) -> WorldZone:
        for zone in self.zones:
            if zone.key == key:
                return zone
        raise KeyError(key)
