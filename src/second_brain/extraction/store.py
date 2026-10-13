from __future__ import annotations

import json
import sqlite3
from typing import Any

from .models import ModelCandidate, VisualSnippet


def replace_visual_snippets(
    connection: sqlite3.Connection,
    *,
    queue_id: int,
    snippets: list[VisualSnippet],
    model_provider: str,
    model_name: str,
) -> None:
    connection.execute("DELETE FROM extraction_visual_snippets WHERE queue_id = ?", (queue_id,))
    for snippet in snippets:
        connection.execute(
            """
            INSERT INTO extraction_visual_snippets(
                queue_id, artifact_id, marker, source_locator, snippet_json,
                model_provider, model_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                queue_id,
                snippet.artifact_id,
                snippet.marker,
                snippet.source_locator,
                json.dumps(snippet.to_payload(), sort_keys=True),
                model_provider,
                model_name,
            ),
        )


def upsert_visual_snippet(
    connection: sqlite3.Connection,
    *,
    queue_id: int,
    snippet: VisualSnippet,
    model_provider: str,
    model_name: str,
) -> None:
    """Store one snippet as soon as the VLM returns it (per-artifact checkpoint).

    A kill mid-VLM-phase keeps every snippet already stored, so the next
    attempt only describes the remaining artifacts (issue #35).
    """
    connection.execute(
        "DELETE FROM extraction_visual_snippets WHERE queue_id = ? AND artifact_id = ?",
        (queue_id, snippet.artifact_id),
    )
    connection.execute(
        """
        INSERT INTO extraction_visual_snippets(
            queue_id, artifact_id, marker, source_locator, snippet_json,
            model_provider, model_name
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            queue_id,
            snippet.artifact_id,
            snippet.marker,
            snippet.source_locator,
            json.dumps(snippet.to_payload(), sort_keys=True),
            model_provider,
            model_name,
        ),
    )


def load_visual_snippets(connection: sqlite3.Connection, *, queue_id: int) -> dict[str, VisualSnippet]:
    """Return snippets already stored for ``queue_id`` keyed by artifact_id."""
    rows = connection.execute(
        "SELECT * FROM extraction_visual_snippets WHERE queue_id = ? ORDER BY id",
        (queue_id,),
    ).fetchall()
    snippets: dict[str, VisualSnippet] = {}
    for row in rows:
        try:
            payload = json.loads(str(row["snippet_json"] or "{}"))
        except json.JSONDecodeError:
            payload = {}
        artifact_id = str(row["artifact_id"])
        snippets[artifact_id] = VisualSnippet(
            artifact_id=artifact_id,
            marker=str(row["marker"] or payload.get("marker") or ""),
            source_locator=str(row["source_locator"] or payload.get("source_locator") or ""),
            visual_description=str(payload.get("visual_description") or ""),
            visible_text=str(payload.get("visible_text") or ""),
            warnings=[str(item) for item in (payload.get("warnings") or [])],
            model_metadata={"provider": str(row["model_provider"]), "model": str(row["model_name"])},
        )
    return snippets


def replace_candidates(
    connection: sqlite3.Connection,
    *,
    queue_id: int,
    candidates: list[ModelCandidate],
    model_provider: str,
    model_name: str,
) -> None:
    connection.execute("DELETE FROM extraction_candidates WHERE queue_id = ?", (queue_id,))
    for index, candidate in enumerate(candidates):
        connection.execute(
            """
            INSERT INTO extraction_candidates(
                queue_id, candidate_index, candidate_type, title, payload_json,
                validation_status, validation_errors_json, promotion_state,
                model_provider, model_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                queue_id,
                index,
                candidate.candidate_type,
                candidate.title,
                json.dumps(candidate.payload, sort_keys=True),
                candidate.validation_status,
                json.dumps(candidate.validation_errors, sort_keys=True),
                "promotion_ready" if candidate.validation_status == "promotion_ready" else "not_ready",
                model_provider,
                model_name,
            ),
        )


def append_candidates(
    connection: sqlite3.Connection,
    *,
    queue_id: int,
    candidates: list[ModelCandidate],
    model_provider: str,
    model_name: str,
    index_offset: int = 0,
) -> None:
    """Insert candidates without deleting existing rows. Used for resume runs."""
    for index, candidate in enumerate(candidates):
        connection.execute(
            """
            INSERT INTO extraction_candidates(
                queue_id, candidate_index, candidate_type, title, payload_json,
                validation_status, validation_errors_json, promotion_state,
                model_provider, model_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                queue_id,
                index_offset + index,
                candidate.candidate_type,
                candidate.title,
                json.dumps(candidate.payload, sort_keys=True),
                candidate.validation_status,
                json.dumps(candidate.validation_errors, sort_keys=True),
                "promotion_ready" if candidate.validation_status == "promotion_ready" else "not_ready",
                model_provider,
                model_name,
            ),
        )


def candidate_counts(candidates: list[ModelCandidate]) -> dict[str, int]:
    invalid = sum(1 for candidate in candidates if candidate.validation_status == "invalid")
    promotion_ready = sum(1 for candidate in candidates if candidate.validation_status == "promotion_ready")
    return {
        "candidate_count": len(candidates),
        "validation_error_count": invalid,
        "promotion_ready": promotion_ready,
    }


def rows_for_queue(connection: sqlite3.Connection, *, queue_id: int) -> dict[str, list[dict[str, Any]]]:
    snippets = connection.execute(
        "SELECT * FROM extraction_visual_snippets WHERE queue_id = ? ORDER BY id",
        (queue_id,),
    ).fetchall()
    candidates = connection.execute(
        "SELECT * FROM extraction_candidates WHERE queue_id = ? ORDER BY candidate_index",
        (queue_id,),
    ).fetchall()
    return {
        "visual_snippets": [dict(row) for row in snippets],
        "candidates": [dict(row) for row in candidates],
    }
