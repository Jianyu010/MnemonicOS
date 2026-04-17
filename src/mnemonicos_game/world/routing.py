from __future__ import annotations

from mnemonicos_game.data.models import GameEntry, WorldSnapshot


def related_note_ids(entry: GameEntry, snapshot: WorldSnapshot) -> list[str]:
    related: list[tuple[int, str]] = []
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
            related.append((score, candidate.note_id))
    related.sort(key=lambda item: (-item[0], item[1]))
    return [note_id for _, note_id in related]


def related_section_keys(entry: GameEntry, snapshot: WorldSnapshot) -> list[str]:
    section_scores: dict[str, int] = {}
    for note_id in related_note_ids(entry, snapshot):
        candidate = next((item for item in snapshot.note_entries if item.note_id == note_id), None)
        if candidate is None or candidate.section_key == entry.section_key:
            continue
        section_scores[candidate.section_key] = section_scores.get(candidate.section_key, 0) + 1
    ordered = sorted(section_scores.items(), key=lambda item: (-item[1], item[0]))
    return [section_key for section_key, _ in ordered]
