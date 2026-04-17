from __future__ import annotations

from dataclasses import dataclass

from mnemonicos_game.data.models import GameBook, GameEntry, WorldSnapshot


@dataclass(slots=True)
class GameIndexes:
    note_by_id: dict[str, GameEntry]
    session_by_slug: dict[str, GameBook]
    session_by_ref: dict[str, GameBook]


def build_indexes(snapshot: WorldSnapshot) -> GameIndexes:
    session_by_slug = {book.session_slug: book for book in snapshot.session_books}
    session_by_ref = {f"session/{book.session_slug}": book for book in snapshot.session_books}
    return GameIndexes(
        note_by_id={entry.note_id: entry for entry in snapshot.note_entries},
        session_by_slug=session_by_slug,
        session_by_ref=session_by_ref,
    )


def build_linked_digest(entry: GameEntry, snapshot: WorldSnapshot, indexes: GameIndexes | None = None) -> str:
    indexes = indexes or build_indexes(snapshot)
    related = _related_entries(entry, snapshot)
    lines = [
        f"Digest for {entry.title}",
        "",
        f"Type: {entry.note_type}",
        f"Section: {entry.section_label}",
        f"Updated: {entry.updated_at or 'unknown'}",
    ]
    if entry.summary:
        lines.extend(["", "Summary:", entry.summary])
    if entry.source_refs:
        lines.extend(["", "Source refs:"])
        lines.extend(f"- {ref}" for ref in entry.source_refs[:5])
    if related:
        lines.extend(["", "Nearby linked materials:"])
        lines.extend(f"- {item.title} ({item.note_type})" for item in related[:5])
    else:
        lines.extend(["", "Nearby linked materials:", "- none found from tags/source refs in local snapshot"])
    return "\n".join(lines)


def build_guidance(entry: GameEntry, snapshot: WorldSnapshot, indexes: GameIndexes | None = None) -> str:
    related = _related_entries(entry, snapshot)
    lines = [
        f"Guided path for {entry.title}",
        "",
        "Suggested route:",
        f"1. Start with {entry.title}.",
    ]
    for step, item in enumerate(related[:3], start=2):
        lines.append(f"{step}. Visit {item.title} ({item.note_type}).")
    if len(lines) == 3:
        lines.append("2. No strong neighboring note found; consult the daemon for provenance.")
    return "\n".join(lines)


def build_provenance(entry: GameEntry, snapshot: WorldSnapshot, indexes: GameIndexes | None = None) -> str:
    indexes = indexes or build_indexes(snapshot)
    lines = [
        f"Provenance for {entry.title}",
        "",
        f"Confidence: {entry.confidence or 'unknown'}",
        f"Lifecycle: {entry.lifecycle_state or 'unknown'}",
    ]
    if entry.source_refs:
        lines.extend(["", "Known source refs:"])
        for ref in entry.source_refs:
            suffix = ""
            if ref in indexes.session_by_ref:
                suffix = " -> raw session"
            lines.append(f"- {ref}{suffix}")
    else:
        lines.extend(["", "Known source refs:", "- none"])
    if entry.tags:
        lines.extend(["", "Tags:"])
        lines.extend(f"- {tag}" for tag in entry.tags)
    return "\n".join(lines)


def resolve_jump_candidates(entry: GameEntry, snapshot: WorldSnapshot, indexes: GameIndexes | None = None) -> tuple[list[GameBook], str | None]:
    indexes = indexes or build_indexes(snapshot)
    candidates = [indexes.session_by_ref[ref] for ref in entry.source_refs if ref in indexes.session_by_ref]
    if len(candidates) == 1:
        return candidates, None
    if len(candidates) > 1:
        return candidates, "multiple"
    if entry.source_refs:
        return [], "non_session_only"
    return [], "missing"


def _related_entries(entry: GameEntry, snapshot: WorldSnapshot) -> list[GameEntry]:
    matches: list[tuple[int, GameEntry]] = []
    tag_set = set(entry.tags)
    source_ref_set = set(entry.source_refs)
    for candidate in snapshot.note_entries:
        if candidate.note_id == entry.note_id:
            continue
        score = 0
        if source_ref_set and source_ref_set.intersection(candidate.source_refs):
            score += 3
        if tag_set and tag_set.intersection(candidate.tags):
            score += 2
        if candidate.section_key == entry.section_key:
            score += 1
        if score:
            matches.append((score, candidate))
    matches.sort(key=lambda item: (-item[0], item[1].title.casefold()))
    return [item for _, item in matches]
