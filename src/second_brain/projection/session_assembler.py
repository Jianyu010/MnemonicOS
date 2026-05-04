from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from .pipeline_models import SourceRef


def assemble_session_projection(connection: sqlite3.Connection, source_ref: SourceRef) -> tuple[str, dict[str, object], list[str]]:
    if not source_ref.conversation_id or not source_ref.episode_id:
        raise ValueError("session projection requires conversation_id and episode_id")
    row = connection.execute(
        """
        SELECT events_path
        FROM raw_episodes
        WHERE conversation_id = ? AND episode_id = ?
        """,
        (source_ref.conversation_id, source_ref.episode_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"raw episode not found: {source_ref.conversation_id}/{source_ref.episode_id}")
    events_path = Path(str(row["events_path"]))
    events = [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
    retained: list[str] = []
    deduplicated = 0
    previous = ""
    for event in events:
        payload = event.get("payload") if isinstance(event, dict) else {}
        text = ""
        if isinstance(payload, dict):
            text = str(payload.get("content") or payload.get("text") or payload.get("message") or "")
        if not text.strip():
            continue
        if previous and text.startswith(previous):
            suffix = text[len(previous) :].strip()
            if suffix:
                retained.append(suffix)
                previous = text
            else:
                deduplicated += 1
            continue
        retained.append(text)
        previous = text
    metadata = {
        "deduplicated_repeated_prefix": deduplicated > 0,
        "cumulative_prefix_collapsed": deduplicated > 0,
        "deduplicated_event_count": deduplicated,
        "episode_event_count": len(events),
        "unique_event_count": len(retained),
    }
    warnings = ["cumulative_prefix_collapsed"] if deduplicated else []
    return "\n\n".join(retained).strip(), metadata, warnings
