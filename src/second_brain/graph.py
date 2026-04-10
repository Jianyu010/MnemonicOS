from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from .models import NoteRecord


SUPPORTED_NOTE_KINDS = {
    "person",
    "project",
    "repo",
    "decision",
    "procedure",
    "concept",
    "incident",
    "source",
    "journal",
    "overview",
}

RELATION_WEIGHTS = {
    "owner": 1.0,
    "repo": 0.9,
    "active_decision": 0.9,
    "linked_decision": 0.8,
    "linked_procedure": 0.8,
    "linked_project": 0.7,
    "linked_note": 0.7,
    "entity": 0.6,
}


@dataclass(slots=True)
class GraphHit:
    id: str
    title: str
    type: str
    status: str | None
    body_path: str
    summary: str
    body: str
    updated_at: str | None
    created_at: str | None
    last_verified_at: str | None
    graph_score: float
    relation_types: list[str]


def _infer_kind(identifier: str) -> str:
    if "/" in identifier:
        prefix = identifier.split("/", 1)[0]
        if prefix in SUPPORTED_NOTE_KINDS:
            return prefix
    return "entity"


def _default_label(identifier: str) -> str:
    if "/" in identifier:
        identifier = identifier.split("/", 1)[1]
    return identifier.replace("-", " ").replace("_", " ").strip() or identifier


def _upsert_graph_node(
    connection: sqlite3.Connection,
    *,
    node_id: str,
    node_type: str,
    kind: str,
    label: str,
    note_id: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO graph_nodes(id, node_type, kind, label, note_id, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            node_type = excluded.node_type,
            kind = excluded.kind,
            label = excluded.label,
            note_id = excluded.note_id,
            updated_at = excluded.updated_at
        """,
        (node_id, node_type, kind, label, note_id),
    )


def rebuild_note_graph(connection: sqlite3.Connection, note: NoteRecord) -> None:
    _upsert_graph_node(
        connection,
        node_id=note.id,
        node_type="note",
        kind=note.type,
        label=note.title,
        note_id=note.id,
    )
    connection.execute("DELETE FROM graph_edges WHERE source_note_id = ?", (note.id,))

    for target_id, relation_type in note.entity_relations:
        if target_id == note.id:
            continue
        existing_note = connection.execute(
            "SELECT id, type, title FROM notes WHERE id = ?",
            (target_id,),
        ).fetchone()
        if existing_note is not None:
            target_kind = str(existing_note["type"])
            target_label = str(existing_note["title"])
            target_note_id: str | None = str(existing_note["id"])
            node_type = "note"
        else:
            target_kind = _infer_kind(target_id)
            target_label = _default_label(target_id)
            target_note_id = None
            node_type = "entity"

        _upsert_graph_node(
            connection,
            node_id=target_id,
            node_type=node_type,
            kind=target_kind,
            label=target_label,
            note_id=target_note_id,
        )

        connection.execute(
            """
            INSERT INTO graph_edges(source_note_id, target_id, target_kind, relation_type, weight, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(source_note_id, target_id, relation_type) DO UPDATE SET
                target_kind = excluded.target_kind,
                weight = excluded.weight,
                updated_at = excluded.updated_at
            """,
            (
                note.id,
                target_id,
                target_kind,
                relation_type,
                RELATION_WEIGHTS.get(relation_type, 0.5),
            ),
        )


def demote_or_delete_note_node(connection: sqlite3.Connection, note_id: str) -> None:
    inbound_count = connection.execute(
        "SELECT COUNT(*) AS count FROM graph_edges WHERE target_id = ?",
        (note_id,),
    ).fetchone()
    if inbound_count is not None and int(inbound_count["count"] or 0) > 0:
        connection.execute(
            """
            UPDATE graph_nodes
            SET node_type = 'entity',
                kind = ?,
                label = ?,
                note_id = NULL,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (_infer_kind(note_id), _default_label(note_id), note_id),
        )
        return
    connection.execute("DELETE FROM graph_nodes WHERE id = ?", (note_id,))


def graph_expand(
    connection: sqlite3.Connection,
    *,
    focal_ids: list[str],
    limit: int,
) -> list[GraphHit]:
    if not focal_ids or limit <= 0:
        return []

    focal_placeholders = ",".join("?" for _ in focal_ids)
    params = [*focal_ids, *focal_ids, *focal_ids, limit]
    rows = connection.execute(
        f"""
        WITH directed AS (
            SELECT
                ge.target_id AS neighbor_id,
                ge.relation_type AS relation_type,
                ge.weight AS weight
            FROM graph_edges ge
            WHERE ge.source_note_id IN ({focal_placeholders})
            UNION ALL
            SELECT
                ge.source_note_id AS neighbor_id,
                'reverse_' || ge.relation_type AS relation_type,
                ge.weight AS weight
            FROM graph_edges ge
            WHERE ge.target_id IN ({focal_placeholders})
        )
        SELECT
            n.id,
            n.title,
            n.type,
            n.status,
            n.body_path,
            n.summary,
            ns.body,
            n.updated_at,
            n.created_at,
            n.last_verified_at,
            SUM(directed.weight) AS graph_score,
            GROUP_CONCAT(DISTINCT directed.relation_type) AS relation_types
        FROM directed
        JOIN notes n ON n.id = directed.neighbor_id
        JOIN notes_search ns ON ns.note_id = n.id
        WHERE n.id NOT IN ({focal_placeholders})
          AND (n.status IS NULL OR n.status != 'retired')
          AND (n.valid_to IS NULL OR n.valid_to > date('now'))
        GROUP BY n.id, n.title, n.type, n.status, n.body_path, n.summary, ns.body,
                 n.updated_at, n.created_at, n.last_verified_at
        ORDER BY graph_score DESC, n.updated_at DESC, n.title ASC
        LIMIT ?
        """,
        params,
    ).fetchall()

    hits: list[GraphHit] = []
    for row in rows:
        relation_payload = str(row["relation_types"] or "")
        relation_types = [part for part in relation_payload.split(",") if part]
        hits.append(
            GraphHit(
                id=str(row["id"]),
                title=str(row["title"]),
                type=str(row["type"]),
                status=str(row["status"]) if row["status"] is not None else None,
                body_path=str(row["body_path"]),
                summary=str(row["summary"] or ""),
                body=str(row["body"] or ""),
                updated_at=str(row["updated_at"] or ""),
                created_at=str(row["created_at"] or ""),
                last_verified_at=str(row["last_verified_at"] or ""),
                graph_score=float(row["graph_score"] or 0.0),
                relation_types=relation_types,
            )
        )
    return hits
