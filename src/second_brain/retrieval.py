from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
import sqlite3

from .config import AppConfig
from .db import connect_db, run_migrations
from .models import RetrieveHit, RetrieveResult
from .paths import VaultPaths


TOKEN_PATTERN = re.compile(r"[\w/-]+", re.UNICODE)


def _query_terms(query: str) -> str:
    terms = TOKEN_PATTERN.findall(query)
    if not terms:
        escaped = query.replace('"', ' ')
        return f'"{escaped}"'
    unique_terms: list[str] = []
    seen: set[str] = set()
    for term in terms:
        lowered = term.casefold()
        if lowered in seen:
            continue
        unique_terms.append(term)
        seen.add(lowered)
    return " OR ".join(f'"{term.replace(chr(34), " ")}"' for term in unique_terms)


def _load_pinned_paths(config: AppConfig) -> list[str]:
    vault_paths = VaultPaths(config.paths.workspace_root, config.paths.vault_root)
    return [str(path.resolve()) for path in vault_paths.pinned_files() if path.exists()]


def _exact_hits(connection: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT DISTINCT n.id, n.title, n.type, n.status, n.body_path, n.summary
        FROM notes n
        LEFT JOIN aliases a ON a.note_id = n.id
        WHERE (
               a.alias = ?
            OR n.id = ?
            OR lower(n.title) = lower(?)
        )
          AND (n.status IS NULL OR n.status != 'retired')
          AND (n.valid_to IS NULL OR n.valid_to > date('now'))
        ORDER BY n.updated_at DESC, n.title ASC
        """,
        (query, query, query),
    ).fetchall()
    return rows


def _bm25_hits(connection: sqlite3.Connection, query: str, limit: int) -> list[sqlite3.Row]:
    match_query = _query_terms(query)
    rows = connection.execute(
        """
        SELECT
            n.id,
            n.title,
            n.type,
            n.status,
            n.body_path,
            n.summary,
            ns.body,
            bm25(notes_fts) AS bm25_score
        FROM notes_fts
        JOIN notes_search ns ON ns.rowid = notes_fts.rowid
        JOIN notes n ON n.id = ns.note_id
        WHERE notes_fts MATCH ?
          AND (n.status IS NULL OR n.status != 'retired')
          AND (n.valid_to IS NULL OR n.valid_to > date('now'))
        ORDER BY bm25_score
        LIMIT ?
        """,
        (match_query, limit),
    ).fetchall()
    return rows


def retrieve(
    config: AppConfig,
    query: str,
    *,
    top_k: int | None = None,
    query_type_hint: str | None = None,
) -> RetrieveResult:
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")

    requested_top_k = top_k or config.retrieval.top_k
    candidates: dict[str, RetrieveHit] = {}

    exact_rows = _exact_hits(connection, query)
    for rank, row in enumerate(exact_rows, start=1):
        candidates[str(row["id"])] = RetrieveHit(
            id=str(row["id"]),
            title=str(row["title"]),
            type=str(row["type"]),
            status=str(row["status"]) if row["status"] is not None else None,
            body_path=str(row["body_path"]),
            summary=str(row["summary"] or ""),
            channels=["exact"],
            raw_score=1.0,
            final_score=10_000.0 - rank,
            excerpt=str(row["summary"] or ""),
        )

    bm25_rows = _bm25_hits(connection, query, requested_top_k * 4)
    for row in bm25_rows:
        note_id = str(row["id"])
        bm25_score = float(row["bm25_score"])
        final_score = -bm25_score
        excerpt = str(row["summary"] or row["body"][:240])
        if note_id in candidates:
            hit = candidates[note_id]
            if "bm25" not in hit.channels:
                hit.channels.append("bm25")
            hit.raw_score = hit.raw_score if hit.raw_score is not None else bm25_score
            hit.excerpt = hit.excerpt or excerpt
            continue
        candidates[note_id] = RetrieveHit(
            id=note_id,
            title=str(row["title"]),
            type=str(row["type"]),
            status=str(row["status"]) if row["status"] is not None else None,
            body_path=str(row["body_path"]),
            summary=str(row["summary"] or ""),
            channels=["bm25"],
            raw_score=bm25_score,
            final_score=final_score,
            excerpt=excerpt,
        )

    ranked_hits = sorted(
        candidates.values(),
        key=lambda hit: (-hit.final_score, hit.title.casefold()),
    )[:requested_top_k]

    with connection:
        cursor = connection.execute(
            """
            INSERT INTO retrieval_queries(query, query_type, classifier_confidence, retrieved, top_k)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                query,
                query_type_hint,
                None,
                json.dumps([hit.id for hit in ranked_hits]),
                requested_top_k,
            ),
        )
        query_id = int(cursor.lastrowid)

        for rank, hit in enumerate(ranked_hits, start=1):
            primary_channel = "exact" if "exact" in hit.channels else "bm25"
            connection.execute(
                """
                INSERT INTO retrieval_hits(query_id, note_id, rank, channel, raw_score, final_score, selected)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (query_id, hit.id, rank, primary_channel, hit.raw_score, hit.final_score),
            )

    connection.close()
    return RetrieveResult(
        query_id=query_id,
        query_type=query_type_hint,
        classifier_confidence=None,
        hits=ranked_hits,
        pinned_paths=_load_pinned_paths(config),
    )


def result_to_json(result: RetrieveResult) -> str:
    payload = {
        "query_id": result.query_id,
        "query_type": result.query_type,
        "classifier_confidence": result.classifier_confidence,
        "pinned_paths": result.pinned_paths,
        "hits": [asdict(hit) for hit in result.hits],
    }
    return json.dumps(payload, indent=2)
