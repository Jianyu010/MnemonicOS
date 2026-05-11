from __future__ import annotations

from datetime import date, datetime
import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
from typing import Any

import yaml

from .config import AppConfig
from .graph import rebuild_note_graph
from .parser import NoteParseError, parse_note, split_frontmatter


logger = logging.getLogger(__name__)

_SKIPPED_LIFECYCLE_STATES = {"review", "contested", "archived"}
_SKIPPED_STATUSES = {"retired", "archived", "superseded"}


@dataclass(slots=True)
class GraphRebuildResult:
    notes_scanned: int
    notes_rebuilt: int
    stale_edges_pruned: int
    orphan_nodes_pruned: int


def rebuild_wiki_graph(
    connection: sqlite3.Connection,
    config: AppConfig,
    *,
    dry_run: bool = False,
) -> GraphRebuildResult:
    rows = connection.execute(
        "SELECT id, body_path FROM notes WHERE status IS NULL OR status NOT IN ('retired', 'archived')"
    ).fetchall()

    notes_scanned = 0
    notes_rebuilt = 0

    parsed_notes = []
    for row in rows:
        notes_scanned += 1
        from pathlib import Path
        path = Path(str(row["body_path"]))
        if not path.exists():
            logger.warning("rebuild_wiki_graph: missing file %s (note %s)", path, row["id"])
            continue
        try:
            note = parse_note(path)
        except NoteParseError as exc:
            logger.warning("rebuild_wiki_graph: parse error %s for %s", exc, path)
            continue
        parsed_notes.append(note)

    if not dry_run:
        with connection:
            for note in parsed_notes:
                rebuild_note_graph(connection, note)
                notes_rebuilt += 1

            stale_edges_cursor = connection.execute(
                "DELETE FROM graph_edges WHERE target_id NOT IN (SELECT id FROM graph_nodes)"
            )
            stale_edges_pruned = stale_edges_cursor.rowcount

            orphan_cursor = connection.execute(
                """
                DELETE FROM graph_nodes
                WHERE node_type = 'entity'
                  AND id NOT IN (SELECT source_note_id FROM graph_edges)
                  AND id NOT IN (SELECT target_id FROM graph_edges)
                """
            )
            orphan_nodes_pruned = orphan_cursor.rowcount
    else:
        notes_rebuilt = len(parsed_notes)
        stale_edges_pruned = 0
        orphan_nodes_pruned = 0

    return GraphRebuildResult(
        notes_scanned=notes_scanned,
        notes_rebuilt=notes_rebuilt,
        stale_edges_pruned=stale_edges_pruned,
        orphan_nodes_pruned=orphan_nodes_pruned,
    )


@dataclass(slots=True)
class LifecycleAuditResult:
    notes_scanned: int
    advanced_to_verified: int
    advanced_to_canonical: int
    drift_flagged: int


def _write_lifecycle_state(path: Path, target_state: str) -> None:
    raw = path.read_text(encoding="utf-8")
    parsed = split_frontmatter(raw)
    metadata = parsed.metadata
    metadata["lifecycle_state"] = target_state
    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=False).strip()
    rendered = f"---\n{frontmatter}\n---\n\n{parsed.body.strip()}\n"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(rendered, encoding="utf-8")
    os.replace(tmp, path)


def _days_since(created_at: str) -> int:
    try:
        created = datetime.fromisoformat(created_at.split("T")[0]).date()
    except (ValueError, AttributeError):
        return 0
    return (date.today() - created).days


def audit_lifecycle_states(
    connection: sqlite3.Connection,
    config: AppConfig,
    *,
    dry_run: bool = False,
) -> LifecycleAuditResult:
    m = config.maintenance
    rows = connection.execute(
        """
        SELECT id, body_path, lifecycle_state, corroboration_count, created_at, status
        FROM notes
        WHERE status IS NULL OR status NOT IN ('retired', 'archived', 'superseded')
        """
    ).fetchall()

    notes_scanned = len(rows)
    advanced_to_verified = 0
    advanced_to_canonical = 0
    drift_flagged = 0

    for row in rows:
        note_id = str(row["id"])
        body_path = Path(str(row["body_path"]))
        lifecycle_state = str(row["lifecycle_state"] or "draft").strip()
        corroboration_count = int(row["corroboration_count"] or 0)
        created_at = str(row["created_at"] or "")

        if lifecycle_state in _SKIPPED_LIFECYCLE_STATES:
            continue

        # Advancement pass
        target_state: str | None = None
        if lifecycle_state == "draft" and corroboration_count >= m.lifecycle_canonical_threshold:
            target_state = "canonical"
        elif lifecycle_state == "draft" and corroboration_count >= m.lifecycle_verified_threshold:
            target_state = "verified"
        elif lifecycle_state == "verified" and corroboration_count >= m.lifecycle_canonical_threshold:
            target_state = "canonical"

        if target_state is not None:
            if not dry_run:
                if not body_path.exists():
                    logger.warning("audit_lifecycle_states: missing file %s (note %s)", body_path, note_id)
                else:
                    try:
                        _write_lifecycle_state(body_path, target_state)
                    except Exception as exc:
                        logger.warning("audit_lifecycle_states: write failed for %s: %s", note_id, exc)
                        continue
                    with connection:
                        connection.execute(
                            "UPDATE notes SET lifecycle_state = ? WHERE id = ?",
                            (target_state, note_id),
                        )
            if target_state == "canonical":
                advanced_to_canonical += 1
            else:
                advanced_to_verified += 1
            continue

        # Drift pass — draft with no corroboration and old created_at
        if lifecycle_state == "draft" and corroboration_count == 0:
            if _days_since(created_at) > m.lifecycle_drift_days:
                if not dry_run:
                    from .review import create_review_item
                    create_review_item(
                        config,
                        review_type="lifecycle_review",
                        suggested_action="investigate or archive stalled draft",
                        reason=f"draft note stalled for >{m.lifecycle_drift_days} days with no corroboration",
                        confidence="low",
                        source_refs=[note_id],
                        slug_seed=f"lifecycle-drift-{note_id}",
                    )
                drift_flagged += 1

    return LifecycleAuditResult(
        notes_scanned=notes_scanned,
        advanced_to_verified=advanced_to_verified,
        advanced_to_canonical=advanced_to_canonical,
        drift_flagged=drift_flagged,
    )


_LIFECYCLE_WEIGHTS: dict[str, float] = {
    "canonical": 1.0,
    "verified": 0.70,
    "draft": 0.40,
    "review": 0.30,
    "contested": 0.20,
    "archived": 0.05,
}


@dataclass(slots=True)
class ImportanceSignal:
    importance_score: float
    in_degree: int
    out_degree: int
    connectivity_score: float
    trust_score: float
    freshness_score: float
    lifecycle_weight: float


def compute_importance_scores(connection: sqlite3.Connection) -> dict[str, ImportanceSignal]:
    from .trust import load_note_trust, load_note_freshness

    degree_rows = connection.execute(
        """
        SELECT
            n.id,
            COALESCE(n.lifecycle_state, 'draft') AS lifecycle_state,
            COUNT(DISTINCT out_e.target_id) AS out_degree,
            COUNT(DISTINCT in_e.source_note_id) AS in_degree
        FROM notes n
        LEFT JOIN graph_edges out_e ON out_e.source_note_id = n.id
        LEFT JOIN graph_edges in_e ON in_e.target_id = n.id
        WHERE n.status IS NULL OR n.status NOT IN ('retired', 'archived', 'superseded')
        GROUP BY n.id
        """
    ).fetchall()

    note_ids = [str(r["id"]) for r in degree_rows]
    trust_map = load_note_trust(connection, note_ids)
    freshness_map = load_note_freshness(connection, note_ids)

    signals: dict[str, ImportanceSignal] = {}
    with connection:
        for row in degree_rows:
            note_id = str(row["id"])
            in_degree = int(row["in_degree"])
            out_degree = int(row["out_degree"])
            lifecycle_state = str(row["lifecycle_state"])

            raw_conn = in_degree + 0.4 * out_degree
            connectivity_score = 2.0 / (1.0 + math.exp(-raw_conn / 5.0)) - 1.0

            trust = trust_map.get(note_id)
            trust_score = trust.usefulness_score if trust else 0.5

            freshness = freshness_map.get(note_id)
            freshness_score = 1.0 - freshness.staleness_score if freshness else 0.5

            lifecycle_weight = _LIFECYCLE_WEIGHTS.get(lifecycle_state, 0.40)

            importance_score = (
                0.35 * connectivity_score
                + 0.25 * trust_score
                + 0.25 * freshness_score
                + 0.15 * lifecycle_weight
            )

            connection.execute(
                """
                INSERT INTO note_importance(
                    note_id, importance_score, in_degree, out_degree,
                    connectivity_score, trust_score, freshness_score,
                    lifecycle_weight, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(note_id) DO UPDATE SET
                    importance_score = excluded.importance_score,
                    in_degree = excluded.in_degree,
                    out_degree = excluded.out_degree,
                    connectivity_score = excluded.connectivity_score,
                    trust_score = excluded.trust_score,
                    freshness_score = excluded.freshness_score,
                    lifecycle_weight = excluded.lifecycle_weight,
                    updated_at = excluded.updated_at
                """,
                (
                    note_id, importance_score, in_degree, out_degree,
                    connectivity_score, trust_score, freshness_score,
                    lifecycle_weight,
                ),
            )
            signals[note_id] = ImportanceSignal(
                importance_score=importance_score,
                in_degree=in_degree,
                out_degree=out_degree,
                connectivity_score=connectivity_score,
                trust_score=trust_score,
                freshness_score=freshness_score,
                lifecycle_weight=lifecycle_weight,
            )
    return signals


@dataclass(slots=True)
class SemanticDuplicatePair:
    note_id_a: str
    note_id_b: str
    similarity: float
    title_a: str
    title_b: str
    note_type: str


@dataclass(slots=True)
class WikiHealthStats:
    total_notes: int
    notes_by_type: dict[str, int]
    notes_by_lifecycle: dict[str, int]
    notes_by_freshness: dict[str, int]
    orphan_note_count: int
    avg_in_degree: float
    avg_out_degree: float
    lifecycle_advanced_this_run: int
    semantic_dedup_candidates: int
    open_review_count: int
    open_relearn_count: int


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def detect_semantic_duplicates(
    connection: sqlite3.Connection,
    config: AppConfig,
    *,
    dry_run: bool = False,
) -> list[SemanticDuplicatePair]:
    if not config.embeddings.enabled or not config.maintenance.semantic_dedup_enabled:
        return []

    from .semantics import encode_text
    from .review import create_review_item
    from .ops import load_open_review_items

    rows = connection.execute(
        """
        SELECT id, type, title, summary, lifecycle_state
        FROM notes
        WHERE (status IS NULL OR status NOT IN ('retired', 'archived', 'superseded'))
          AND (lifecycle_state IS NULL OR lifecycle_state NOT IN ('contested', 'archived'))
        ORDER BY type, id
        """
    ).fetchall()

    by_type: dict[str, list[Any]] = {}
    for row in rows:
        t = str(row["type"])
        by_type.setdefault(t, []).append(row)

    review_dir = config.paths.vault_root / "wiki" / "review"
    existing_merge_slugs: set[str] = set()
    if review_dir.exists():
        for p in review_dir.glob("semantic-dedup*.md"):
            existing_merge_slugs.add(p.stem)

    threshold = config.maintenance.semantic_dedup_threshold
    pairs: list[SemanticDuplicatePair] = []

    for note_type, group in by_type.items():
        if len(group) < 2:
            continue
        vector_cache: dict[str, list[float]] = {}
        for row in group:
            note_id = str(row["id"])
            summary = str(row["summary"] or row["title"] or "")
            vector_cache[note_id] = encode_text(summary, dimensions=config.embeddings.dimensions)

        ids = [str(r["id"]) for r in group]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                id_a, id_b = ids[i], ids[j]
                sim = _cosine(vector_cache[id_a], vector_cache[id_b])
                if sim >= threshold:
                    pair = SemanticDuplicatePair(
                        note_id_a=id_a,
                        note_id_b=id_b,
                        similarity=sim,
                        title_a=str(group[i]["title"]),
                        title_b=str(group[j]["title"]),
                        note_type=note_type,
                    )
                    pairs.append(pair)
                    if not dry_run:
                        slug_seed = f"semantic-dedup-{id_a}-{id_b}"
                        from .review import slugify
                        slug = slugify(slug_seed)
                        if slug not in existing_merge_slugs:
                            create_review_item(
                                config,
                                review_type="merge_review",
                                suggested_action="review and merge if duplicate",
                                reason=f"semantic similarity {sim:.3f} >= threshold {threshold}",
                                confidence="medium",
                                source_refs=[id_a, id_b],
                                slug_seed=slug_seed,
                            )
                            existing_merge_slugs.add(slug)

    return pairs


def compute_wiki_health_stats(
    connection: sqlite3.Connection,
    *,
    audit_result: "LifecycleAuditResult | None" = None,
    dedup_pairs: "list[SemanticDuplicatePair] | None" = None,
    open_review_count: int = 0,
    open_relearn_count: int = 0,
) -> "WikiHealthStats":
    type_rows = connection.execute(
        """
        SELECT type, COUNT(*) AS cnt FROM notes
        WHERE status IS NULL OR status NOT IN ('retired', 'archived')
        GROUP BY type
        """
    ).fetchall()
    notes_by_type = {str(r["type"]): int(r["cnt"]) for r in type_rows}
    total_notes = sum(notes_by_type.values())

    lc_rows = connection.execute(
        """
        SELECT COALESCE(lifecycle_state, 'draft') AS state, COUNT(*) AS cnt
        FROM notes WHERE status IS NULL OR status NOT IN ('retired', 'archived')
        GROUP BY state
        """
    ).fetchall()
    notes_by_lifecycle = {str(r["state"]): int(r["cnt"]) for r in lc_rows}

    fresh_rows = connection.execute(
        "SELECT COALESCE(freshness_state, 'unknown') AS state, COUNT(*) AS cnt FROM note_freshness GROUP BY state"
    ).fetchall()
    notes_by_freshness = {str(r["state"]): int(r["cnt"]) for r in fresh_rows}

    deg_row = connection.execute(
        """
        SELECT
            COALESCE(AVG(out_d.cnt), 0.0) AS avg_out,
            COALESCE(AVG(in_d.cnt), 0.0) AS avg_in
        FROM
            (SELECT source_note_id, COUNT(*) AS cnt FROM graph_edges GROUP BY source_note_id) out_d,
            (SELECT target_id, COUNT(*) AS cnt FROM graph_edges GROUP BY target_id) in_d
        """
    ).fetchone()
    avg_out = float(deg_row["avg_out"] if deg_row else 0.0)
    avg_in = float(deg_row["avg_in"] if deg_row else 0.0)

    orphan_ids = detect_orphan_nodes(connection)

    advanced = 0
    if audit_result is not None:
        advanced = audit_result.advanced_to_verified + audit_result.advanced_to_canonical

    return WikiHealthStats(
        total_notes=total_notes,
        notes_by_type=notes_by_type,
        notes_by_lifecycle=notes_by_lifecycle,
        notes_by_freshness=notes_by_freshness,
        orphan_note_count=len(orphan_ids),
        avg_in_degree=avg_in,
        avg_out_degree=avg_out,
        lifecycle_advanced_this_run=advanced,
        semantic_dedup_candidates=len(dedup_pairs) if dedup_pairs is not None else 0,
        open_review_count=open_review_count,
        open_relearn_count=open_relearn_count,
    )


def detect_orphan_nodes(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT n.id
        FROM notes n
        WHERE (n.status IS NULL OR n.status NOT IN ('retired', 'archived'))
          AND NOT EXISTS (SELECT 1 FROM graph_edges ge WHERE ge.source_note_id = n.id)
          AND NOT EXISTS (SELECT 1 FROM graph_edges ge WHERE ge.target_id = n.id)
        """
    ).fetchall()
    return [str(row["id"]) for row in rows]


# ---------------------------------------------------------------------------
# Phase 6 — Embedding-Based Neighbor Linking
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NeighborMatch:
    note_id: str
    similarity: float


def find_embedding_neighbors(
    connection: sqlite3.Connection,
    config: AppConfig,
    note_id: str,
    *,
    k: int = 10,
    threshold: float = 0.78,
) -> list[NeighborMatch]:
    from .semantics import encode_text

    note_row = connection.execute(
        "SELECT summary, type FROM notes WHERE id = ?", (note_id,)
    ).fetchone()
    if note_row is None:
        return []

    note_type = str(note_row["type"] or "")
    summary = str(note_row["summary"] or "")
    if not summary:
        return []

    query_vec = encode_text(summary, dimensions=config.embeddings.dimensions)

    rows = connection.execute(
        """
        SELECT nv.note_id, nv.vector_json
        FROM note_vectors nv
        JOIN notes n ON n.id = nv.note_id
        WHERE nv.note_id != ?
          AND n.type = ?
          AND (n.status IS NULL OR n.status NOT IN ('retired', 'archived', 'superseded'))
        """,
        (note_id, note_type),
    ).fetchall()

    matches: list[NeighborMatch] = []
    import json as _json
    for row in rows:
        raw = row["vector_json"]
        if raw is None:
            continue
        try:
            vec = _json.loads(str(raw))
        except Exception:
            continue
        if not vec:
            continue
        sim = _cosine(query_vec, vec)
        if sim >= threshold:
            matches.append(NeighborMatch(note_id=str(row["note_id"]), similarity=sim))

    matches.sort(key=lambda m: -m.similarity)
    return matches[:k]


def assert_similar_to_link(
    connection: sqlite3.Connection,
    config: AppConfig,
    src_id: str,
    tgt_id: str,
    *,
    similarity_score: float,
) -> bool:
    from .promotion.worker import apply_relation_to_note

    src_row = connection.execute("SELECT body_path FROM notes WHERE id = ?", (src_id,)).fetchone()
    if src_row is None:
        return False

    src_path = Path(str(src_row["body_path"]))
    if not src_path.exists():
        return False

    added = apply_relation_to_note(src_path, target_note_id=tgt_id, relation_type="similar_to")
    if not added:
        return False

    try:
        note = parse_note(src_path.read_text())
        rebuild_note_graph(connection, note, config=config)
    except Exception:
        pass

    return True


def run_embedding_neighbor_pass(
    connection: sqlite3.Connection,
    config: AppConfig,
    note_id: str,
) -> int:
    if not config.embeddings.enabled or not config.maintenance.embedding_neighbor_enabled:
        return 0

    threshold = config.maintenance.neighbor_similarity_threshold
    dedup_threshold = config.maintenance.semantic_dedup_threshold
    k = config.maintenance.neighbor_k

    try:
        neighbors = find_embedding_neighbors(connection, config, note_id, k=k, threshold=threshold)
    except Exception:
        logger.warning("embedding neighbor pass failed for %s", note_id)
        return 0

    links_added = 0
    review_dir = config.paths.vault_root / "wiki" / "review"
    existing_merge_slugs: set[str] = set()
    if review_dir.exists():
        for p in review_dir.glob("semantic-dedup*.md"):
            existing_merge_slugs.add(p.stem)

    for match in neighbors:
        if match.similarity >= dedup_threshold:
            from .review import create_review_item as _create_review_item, slugify
            slug_seed = f"semantic-dedup-{note_id}-{match.note_id}"
            slug = slugify(slug_seed)
            if slug not in existing_merge_slugs:
                try:
                    _create_review_item(
                        config,
                        review_type="merge_review",
                        suggested_action="review and merge if duplicate",
                        reason=f"semantic similarity {match.similarity:.3f} >= threshold {dedup_threshold}",
                        confidence="medium",
                        source_refs=[note_id, match.note_id],
                        slug_seed=slug_seed,
                    )
                    existing_merge_slugs.add(slug)
                except Exception:
                    logger.warning("failed to create merge review for %s/%s", note_id, match.note_id)

        added = assert_similar_to_link(connection, config, note_id, match.note_id, similarity_score=match.similarity)
        if added:
            links_added += 1

    now = datetime.utcnow().isoformat()
    connection.execute(
        """
        INSERT INTO note_embedding_scans (note_id, last_scanned_at, neighbor_count, scan_threshold)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(note_id) DO UPDATE SET
            last_scanned_at = excluded.last_scanned_at,
            neighbor_count = excluded.neighbor_count,
            scan_threshold = excluded.scan_threshold
        """,
        (note_id, now, links_added, threshold),
    )
    connection.commit()
    return links_added


def run_embedding_neighbor_backfill(
    connection: sqlite3.Connection,
    config: AppConfig,
) -> int:
    if not config.embeddings.enabled or not config.maintenance.embedding_neighbor_enabled:
        return 0

    max_age = config.maintenance.neighbor_scan_max_age_days
    cutoff = (datetime.utcnow().date() - __import__("datetime").timedelta(days=max_age)).isoformat()

    rows = connection.execute(
        """
        SELECT n.id FROM notes n
        LEFT JOIN note_embedding_scans nes ON nes.note_id = n.id
        WHERE (n.status IS NULL OR n.status NOT IN ('retired', 'archived', 'superseded'))
          AND (nes.last_scanned_at IS NULL OR nes.last_scanned_at < ?)
        """,
        (cutoff,),
    ).fetchall()

    total = 0
    for row in rows:
        total += run_embedding_neighbor_pass(connection, config, str(row["id"]))
    return total
