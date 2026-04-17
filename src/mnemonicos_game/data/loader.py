from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from second_brain.parser import NoteParseError, parse_note, split_frontmatter
from second_brain.paths import SYSTEM_FILES, VaultPaths

from .models import GameBook, GameEntry, WorldSnapshot, WorldZone, ZoneSection


CANONICAL_ZONE_ORDER = (
    "concepts",
    "procedures",
    "incidents",
    "review",
    "journals",
    "system",
    "vault",
    "reserved",
)

RESERVED_WIKI_CATEGORIES = ("decisions", "people", "projects", "repos", "sources")

DEFAULT_ZONE_SECTIONS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "concepts": (
        ("concepts", "Concepts Shelf", "wiki/concepts"),
    ),
    "procedures": (
        ("procedures:active", "Procedures Wing: Active", "wiki/procedures/active"),
        ("procedures:drafts", "Procedures Wing: Drafts", "wiki/procedures/drafts"),
        ("procedures:retired", "Procedures Wing: Retired", "wiki/procedures/retired"),
    ),
    "incidents": (
        ("incidents", "Incidents Shelf", "wiki/incidents"),
    ),
    "review": (
        ("review", "Review Desk", "wiki/review"),
    ),
    "journals": (
        ("journals", "Journal Corner", "wiki/journals"),
        ("overviews", "Overview Alcove", "wiki/overviews"),
    ),
    "system": (
        ("system-wall", "System Wall", "system"),
    ),
    "vault": (
        ("vault:assets", "Raw Assets", "raw/assets"),
        ("vault:docs", "Raw Docs", "raw/docs"),
        ("vault:sessions", "Raw Sessions", "raw/sessions"),
        ("vault:web-clips", "Web Clips", "raw/web_clips"),
    ),
}


class BrainLoader:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.vault_paths = VaultPaths(self.workspace_root, self.workspace_root / "brain")

    def load(self) -> WorldSnapshot:
        note_entries = self._load_notes()
        session_books = self._load_sessions()
        system_files = [path for path in self.vault_paths.system_files() if path.exists()]
        system_entries = self._load_system_entries(system_files)
        note_entries = sorted(
            [*note_entries, *system_entries],
            key=lambda item: (CANONICAL_ZONE_ORDER.index(item.zone_key), item.section_key, item.title.casefold()),
        )
        zones = self._build_zones(note_entries=note_entries, session_books=session_books, system_files=system_files)
        return WorldSnapshot(
            workspace_root=self.workspace_root,
            brain_root=self.vault_paths.vault_root,
            note_entries=note_entries,
            session_books=session_books,
            system_files=system_files,
            zones=zones,
        )

    def _load_notes(self) -> list[GameEntry]:
        entries: list[GameEntry] = []
        for path in sorted(self.vault_paths.wiki_dir.rglob("*.md")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.vault_paths.wiki_dir)
            top_dir = relative.parts[0] if relative.parts else ""
            if path.name in {"index.md", "log.md"}:
                continue
            if top_dir in RESERVED_WIKI_CATEGORIES:
                continue
            if top_dir == "review":
                review_entry = self._load_review_entry(path, relative)
                if review_entry is not None:
                    entries.append(review_entry)
                continue
            try:
                note = parse_note(path)
            except NoteParseError:
                continue
            zone_key, section_key, section_label = self._classify_note(relative, note.type)
            entries.append(
                GameEntry(
                    note_id=note.id,
                    note_type=note.type,
                    title=note.title,
                    body_path=note.body_path,
                    zone_key=zone_key,
                    section_key=section_key,
                    section_label=section_label,
                    source_dir=relative.parent.as_posix(),
                    confidence=note.confidence,
                    lifecycle_state=note.lifecycle_state,
                    updated_at=note.updated_at,
                    source_refs=list(note.source_refs),
                    tags=list(note.tags),
                    summary=note.summary,
                )
            )
        return sorted(entries, key=lambda item: (CANONICAL_ZONE_ORDER.index(item.zone_key), item.section_key, item.title.casefold()))

    def _load_review_entry(self, path: Path, relative: Path) -> GameEntry | None:
        try:
            parsed = split_frontmatter(path.read_text())
        except NoteParseError:
            return None
        metadata = parsed.metadata
        note_id = str(metadata.get("id", "")).strip()
        title = str(metadata.get("title", "")).strip()
        updated_at = str(metadata.get("updated_at", "")).strip()
        created_at = str(metadata.get("created_at", "")).strip()
        if not (note_id and title and updated_at and created_at):
            return None
        source_refs = _coerce_list(metadata.get("source_refs"))
        tags = _coerce_list(metadata.get("tags"))
        confidence = str(metadata.get("confidence", "")).strip() or None
        lifecycle_state = str(metadata.get("lifecycle_state", "")).strip() or "review"
        summary = str(metadata.get("summary", "")).strip() or parsed.body.strip().split("\n\n", 1)[0][:400]
        return GameEntry(
            note_id=note_id,
            note_type="review",
            title=title,
            body_path=path.resolve(),
            zone_key="review",
            section_key="review",
            section_label="Review Desk",
            source_dir=relative.parent.as_posix(),
            confidence=confidence,
            lifecycle_state=lifecycle_state,
            updated_at=updated_at,
            source_refs=source_refs,
            tags=tags,
            summary=summary,
        )

    def _load_system_entries(self, system_files: list[Path]) -> list[GameEntry]:
        entries: list[GameEntry] = []
        for path in system_files:
            entries.append(
                GameEntry(
                    note_id=f"system/{path.stem.casefold()}",
                    note_type="system",
                    title=path.stem,
                    body_path=path.resolve(),
                    zone_key="system",
                    section_key="system-wall",
                    section_label="System Wall",
                    source_dir="system",
                    confidence=None,
                    lifecycle_state=None,
                    updated_at=None,
                    source_refs=[],
                    tags=[],
                    summary="",
                )
            )
        return entries

    def _load_sessions(self) -> list[GameBook]:
        sessions_dir = self.vault_paths.raw_dir / "sessions"
        if not sessions_dir.exists():
            return []
        grouped: list[GameBook] = []
        for path in sorted(sessions_dir.glob("*.md")):
            session_slug = path.stem
            agent_label, month_label = self._classify_session(session_slug)
            section_key = f"{agent_label}:{month_label}"
            grouped.append(
                GameBook(
                    title=session_slug,
                    body_path=path.resolve(),
                    zone_key="vault",
                    section_key=section_key,
                    section_label=f"{agent_label} / {month_label}",
                    source_dir="raw/sessions",
                    agent_label=agent_label,
                    month_label=month_label,
                    session_slug=session_slug,
                )
            )
        return grouped

    def _classify_note(self, relative: Path, note_type: str) -> tuple[str, str, str]:
        top_dir = relative.parts[0] if relative.parts else note_type
        if top_dir == "concepts":
            return "concepts", "concepts", "Concepts Shelf"
        if top_dir == "incidents":
            return "incidents", "incidents", "Incidents Shelf"
        if top_dir == "review":
            return "review", "review", "Review Desk"
        if top_dir == "journals":
            return "journals", "journals", "Journal Corner"
        if top_dir == "overviews":
            return "journals", "overviews", "Overview Alcove"
        if top_dir == "procedures":
            state = relative.parts[1] if len(relative.parts) > 1 else "drafts"
            if state == "active":
                return "procedures", "procedures:active", "Procedures Wing: Active"
            if state == "retired":
                return "procedures", "procedures:retired", "Procedures Wing: Retired"
            return "procedures", "procedures:drafts", "Procedures Wing: Drafts"
        return "journals", top_dir, top_dir.replace("-", " ").title()

    def _classify_session(self, session_slug: str) -> tuple[str, str]:
        parts = session_slug.split("-")
        if len(parts) >= 4:
            month_label = f"{parts[0]}-{parts[1]}"
        else:
            month_label = "unknown-month"
        if len(parts) >= 5:
            agent_label = parts[4]
        else:
            agent_label = "unknown-agent"
        return agent_label, month_label

    def _build_zones(
        self,
        *,
        note_entries: list[GameEntry],
        session_books: list[GameBook],
        system_files: list[Path],
    ) -> list[WorldZone]:
        zone_sections: dict[str, list[ZoneSection]] = defaultdict(list)
        note_groups: dict[tuple[str, str, str], list[GameEntry]] = defaultdict(list)
        for entry in note_entries:
            note_groups[(entry.zone_key, entry.section_key, entry.section_label)].append(entry)

        for (zone_key, section_key, section_label), entries in sorted(note_groups.items()):
            source_dir = entries[0].source_dir if entries else ""
            zone_sections[zone_key].append(
                ZoneSection(
                    key=section_key,
                    label=section_label,
                    source_dir=source_dir,
                    entries=entries,
                )
            )

        book_groups: dict[tuple[str, str, str], list[GameBook]] = defaultdict(list)
        for book in session_books:
            book_groups[(book.zone_key, book.section_key, book.section_label)].append(book)
        for (zone_key, section_key, section_label), books in sorted(book_groups.items()):
            zone_sections[zone_key].append(
                ZoneSection(
                    key=section_key,
                    label=section_label,
                    source_dir="raw/sessions",
                    books=books,
                )
            )

        for zone_key, defaults in DEFAULT_ZONE_SECTIONS.items():
            existing = {section.key for section in zone_sections[zone_key]}
            for section_key, section_label, source_dir in defaults:
                if section_key in existing:
                    continue
                zone_sections[zone_key].append(
                    ZoneSection(
                        key=section_key,
                        label=section_label,
                        source_dir=source_dir,
                        under_construction=True,
                    )
                )

        reserved_sections = [
            ZoneSection(
                key=f"reserved:{name}",
                label=f"{name.title()} (Under Construction)",
                source_dir=f"wiki/{name}",
                reserved=True,
                under_construction=True,
            )
            for name in RESERVED_WIKI_CATEGORIES
        ]
        zone_sections["reserved"].extend(reserved_sections)

        zones: list[WorldZone] = []
        for zone_key in CANONICAL_ZONE_ORDER:
            label = _zone_label(zone_key)
            sections = sorted(zone_sections.get(zone_key, []), key=lambda item: item.label.casefold())
            zones.append(WorldZone(key=zone_key, label=label, sections=sections))
        return zones


def _zone_label(zone_key: str) -> str:
    return {
        "concepts": "Concepts Shelf",
        "procedures": "Procedure Wing",
        "incidents": "Incidents Shelf",
        "review": "Review Desk",
        "journals": "Journal Corner",
        "system": "System Wall",
        "vault": "Dungeon Vault",
        "reserved": "Reserved Wing",
    }[zone_key]


def _coerce_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []
