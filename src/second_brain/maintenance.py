from __future__ import annotations

from datetime import date, datetime
import json
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


# ---------------------------------------------------------------------------
# Phase 7 — LLM Cluster Validation + LLM-free Pattern Discovery
# ---------------------------------------------------------------------------

_ROLE_VOCABULARY = [
    "source_of_truth",
    "derived_artifact",
    "sync_mechanism",
    "failure_mode",
    "dependency",
    "output",
    "tradeoff",
    "scope_marker",
    "negation_marker",
]

_FAILURE_TOKENS = frozenset([
    "fail", "error", "broke", "crash", "rollback", "incident", "outage", "bug",
    "exception", "wrong", "corrupt", "timeout", "deadlock", "regression",
])

_RESOLUTION_TOKENS = frozenset([
    "fix", "resolve", "repair", "restore", "recover", "undo", "revert", "patch",
    "workaround", "mitigation", "solution", "corrected", "prevented",
])


@dataclass(slots=True)
class Archetype:
    archetype_id: str
    label: str
    layer_a_description: str
    layer_b_role_schema: dict[str, str]
    occurrence_count: int
    score: float
    relation_type: str
    source_channel: int


@dataclass(slots=True)
class ArchetypeCandidate:
    source_channel: int
    note_pairs: list[tuple[str, str]]
    role_signature: dict[str, float]
    failure_token: str | None
    resolution_token: str | None
    candidate_score: float


@dataclass(slots=True)
class ClusterValidationResult:
    clusters_processed: int = 0
    links_confirmed: int = 0
    links_removed: int = 0
    merge_reviews_created: int = 0
    patterns_named: int = 0


@dataclass(slots=True)
class PatternDiscoveryResult:
    channel_2_candidates: int = 0
    channel_3_candidates: int = 0
    archetypes_confirmed: int = 0
    archetypes_rejected: int = 0
    edges_updated: int = 0
    layer_2b_triggered: bool = False
    layer_3_eager_triggered: bool = False


def _union_find_clusters(pairs: list[tuple[str, str]]) -> list[list[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        root = x
        while root in parent:
            root = parent[root]
        # path compression
        while x in parent:
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    all_nodes: set[str] = set()
    for a, b in pairs:
        all_nodes.add(a)
        all_nodes.add(b)
        union(a, b)

    groups: dict[str, list[str]] = {}
    for node in all_nodes:
        root = find(node)
        groups.setdefault(root, []).append(node)
    return list(groups.values())


def _role_vector(text: str) -> list[float]:
    lower = text.casefold()
    return [float(lower.count(term.replace("_", " "))) for term in _ROLE_VOCABULARY]


def _upsert_archetype_graph_edge(
    connection: sqlite3.Connection,
    *,
    source_note_id: str,
    target_id: str,
    relation_type: str,
    weight: float,
    archetype_id: str,
    archetype_score: float,
    instance_confidence: float,
    updated_at: str,
) -> None:
    target = connection.execute("SELECT type FROM notes WHERE id = ?", (target_id,)).fetchone()
    target_kind = str(target["type"]) if target is not None else "note"
    connection.execute(
        """
        INSERT INTO graph_edges(
            source_note_id, target_id, target_kind, relation_type, weight, updated_at,
            archetype_id, archetype_score, instance_confidence
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_note_id, target_id, relation_type) DO UPDATE SET
            target_kind = excluded.target_kind,
            weight = excluded.weight,
            updated_at = excluded.updated_at,
            archetype_id = excluded.archetype_id,
            archetype_score = excluded.archetype_score,
            instance_confidence = excluded.instance_confidence
        """,
        (
            source_note_id,
            target_id,
            target_kind,
            relation_type,
            weight,
            updated_at,
            archetype_id,
            archetype_score,
            instance_confidence,
        ),
    )


def _call_llm_json(prompt: str, llm_fn: "Any") -> "dict | None":
    import json as _json
    try:
        raw = llm_fn(prompt)
        if not isinstance(raw, str):
            return None
        # strip markdown code fences if present
        stripped = raw.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            stripped = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return _json.loads(stripped)
    except Exception:
        return None


def _add_pattern_tag(config: AppConfig, note_id: str, tag: str) -> bool:
    row = None
    try:
        from .db import connect_db
        conn = connect_db(config.paths.db_path)
        row = conn.execute("SELECT body_path FROM notes WHERE id = ?", (note_id,)).fetchone()
        conn.close()
    except Exception:
        pass
    if row is None:
        return False
    note_path = Path(str(row["body_path"]))
    if not note_path.exists():
        return False
    try:
        text = note_path.read_text(encoding="utf-8")
        parsed = split_frontmatter(text)
        metadata = dict(parsed.metadata)
        tags = list(metadata.get("tags") or [])
        if tag not in tags:
            tags.append(tag)
            metadata["tags"] = tags
            new_fm = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False, default_flow_style=False)
            new_text = f"---\n{new_fm}---\n\n{parsed.body}\n"
            tmp = note_path.with_suffix(".tmp")
            tmp.write_text(new_text, encoding="utf-8")
            os.rename(tmp, note_path)
            return True
    except Exception:
        pass
    return False


def run_cluster_validation(
    connection: sqlite3.Connection,
    config: AppConfig,
    llm_fn: "Any | None" = None,
    *,
    dry_run: bool = False,
) -> ClusterValidationResult:
    result = ClusterValidationResult()
    if not config.maintenance.cluster_validation_enabled:
        return result

    rows = connection.execute(
        """
        SELECT source_note_id, target_id
        FROM graph_edges
        WHERE relation_type = 'similar_to'
          AND target_kind = 'note'
        """
    ).fetchall()
    if not rows:
        return result

    pairs = [(str(r["source_note_id"]), str(r["target_id"])) for r in rows]
    clusters = _union_find_clusters(pairs)
    min_size = config.maintenance.cluster_validation_min_cluster_size
    max_size = config.maintenance.cluster_validation_max_size
    clusters = [c for c in clusters if len(c) >= min_size]

    # Cap large clusters by splitting into chunks
    capped: list[list[str]] = []
    for cluster in clusters:
        while len(cluster) > max_size:
            capped.append(cluster[:max_size])
            cluster = cluster[max_size:]
        if cluster:
            capped.append(cluster)
    clusters = capped

    now = datetime.utcnow().isoformat()

    for cluster in clusters:
        result.clusters_processed += 1

        if llm_fn is None:
            # No LLM: confirm all existing edges as-is
            result.links_confirmed += len(cluster)
            continue

        # Gather note info for LLM prompt
        id_list = ",".join("?" * len(cluster))
        note_rows = connection.execute(
            f"SELECT id, title, summary FROM notes WHERE id IN ({id_list})",
            cluster,
        ).fetchall()
        note_info = {str(r["id"]): {"title": str(r["title"] or ""), "summary": str(r["summary"] or "")} for r in note_rows}

        # Build edge similarity map for this cluster
        edge_pairs_in_cluster = [(a, b) for (a, b) in pairs if a in note_info and b in note_info]

        prompt = (
            "You are a knowledge graph curator. Review the following group of notes linked by semantic similarity.\n"
            "For each pair, decide: valid=true (keep link), valid=false (remove link), "
            "merge_candidate=true (near-duplicate).\n"
            "Return JSON: {\"cluster_valid\": bool, \"abstract_pattern\": str, \"pattern_tag\": str, "
            "\"relations\": [{\"src\": str, \"tgt\": str, \"valid\": bool, \"merge_candidate\": bool, "
            "\"refined_relation_type\": str, \"merge_rationale\": str|null}], "
            "\"invalid_pairs\": [{\"src\": str, \"tgt\": str, \"reason\": str}]}\n\n"
        )
        for nid, info in note_info.items():
            prompt += f"- {nid}: {info['title']} — {info['summary'][:120]}\n"

        response = _call_llm_json(prompt, llm_fn)
        if response is None:
            logger.warning("cluster validation LLM call failed for cluster %s", cluster[:2])
            continue

        pattern_tag = str(response.get("pattern_tag") or "").strip()

        for rel in (response.get("relations") or []):
            src = str(rel.get("src") or rel.get("src_note_id") or "")
            tgt = str(rel.get("tgt") or rel.get("tgt_note_id") or "")
            valid = bool(rel.get("valid", True))
            merge = bool(rel.get("merge_candidate", False))
            refined = str(rel.get("refined_relation_type") or "similar_to").strip()

            if not src or not tgt:
                continue

            if valid:
                result.links_confirmed += 1
                if not dry_run:
                    if pattern_tag:
                        _add_pattern_tag(config, src, pattern_tag)
                        _add_pattern_tag(config, tgt, pattern_tag)
                    if refined != "similar_to":
                        connection.execute(
                            """
                            UPDATE graph_edges SET relation_type = ?, updated_at = ?
                            WHERE source_note_id = ? AND target_id = ? AND relation_type = 'similar_to'
                            """,
                            (refined, now, src, tgt),
                        )
                    # record positive training pair
                    connection.execute(
                        "INSERT INTO maintenance_training_pairs (note_a_id, note_b_id, label, source, created_at) VALUES (?, ?, 1, 'cluster_validation', ?)",
                        (src, tgt, now),
                    )
                if merge:
                    result.merge_reviews_created += 1
                    if not dry_run:
                        from .review import create_review_item as _cri, slugify as _slugify
                        _slug = _slugify(f"cluster-merge-{src}-{tgt}")
                        review_dir = config.paths.vault_root / "wiki" / "review"
                        if not (review_dir / f"{_slug}.md").exists():
                            _cri(
                                config,
                                review_type="merge_review",
                                suggested_action="review and merge if duplicate",
                                reason=str(rel.get("merge_rationale") or "cluster validation: near-duplicate"),
                                confidence="medium",
                                source_refs=[src, tgt],
                                slug_seed=f"cluster-merge-{src}-{tgt}",
                            )
            else:
                result.links_removed += 1
                if not dry_run:
                    connection.execute(
                        "DELETE FROM graph_edges WHERE source_note_id = ? AND target_id = ? AND relation_type = 'similar_to'",
                        (src, tgt),
                    )
                    connection.execute(
                        "INSERT INTO maintenance_training_pairs (note_a_id, note_b_id, label, source, created_at) VALUES (?, ?, 0, 'cluster_validation', ?)",
                        (src, tgt, now),
                    )

        if pattern_tag:
            result.patterns_named += 1

    if not dry_run:
        connection.commit()
    return result


def run_pattern_discovery(
    connection: sqlite3.Connection,
    config: AppConfig,
    llm_fn: "Any | None" = None,
    *,
    dry_run: bool = False,
) -> PatternDiscoveryResult:
    result = PatternDiscoveryResult()
    if not config.maintenance.pattern_discovery_enabled:
        return result

    # --- Channel 2: role-vector cosine clustering ---
    rows = connection.execute(
        """
        SELECT id, type, summary, title
        FROM notes
        WHERE status IS NULL OR status NOT IN ('retired', 'archived', 'superseded')
        """
    ).fetchall()

    vecs: dict[str, list[float]] = {}
    types: dict[str, str] = {}
    for row in rows:
        nid = str(row["id"])
        text = str(row["summary"] or row["title"] or "")
        vecs[nid] = _role_vector(text)
        types[nid] = str(row["type"])

    ch2_pairs: list[tuple[str, str, float]] = []
    ids = list(vecs.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            if types[a] != types[b]:
                continue
            sim = _cosine(vecs[a], vecs[b])
            if sim >= 0.85:
                ch2_pairs.append((a, b, sim))

    if ch2_pairs:
        result.channel_2_candidates = len(ch2_pairs)

    # --- Channel 3: failure/resolution symmetry ---
    incident_rows = connection.execute(
        "SELECT id, summary, title FROM notes WHERE type = 'incident' AND (status IS NULL OR status NOT IN ('retired','archived','superseded'))"
    ).fetchall()
    procedure_rows = connection.execute(
        "SELECT id, summary, title FROM notes WHERE type = 'procedure' AND (status IS NULL OR status NOT IN ('retired','archived','superseded'))"
    ).fetchall()

    def _token_match(text: str, token_set: "frozenset[str]") -> "str | None":
        lower = text.casefold()
        for tok in token_set:
            if tok in lower:
                return tok
        return None

    ch3_candidates: list[ArchetypeCandidate] = []
    for inc in incident_rows:
        fail_tok = _token_match(str(inc["summary"] or inc["title"] or ""), _FAILURE_TOKENS)
        if fail_tok is None:
            continue
        for proc in procedure_rows:
            res_tok = _token_match(str(proc["summary"] or proc["title"] or ""), _RESOLUTION_TOKENS)
            if res_tok is None:
                continue
            ch3_candidates.append(ArchetypeCandidate(
                source_channel=3,
                note_pairs=[(str(inc["id"]), str(proc["id"]))],
                role_signature={},
                failure_token=fail_tok,
                resolution_token=res_tok,
                candidate_score=0.7,
            ))

    result.channel_3_candidates = len(ch3_candidates)

    now = datetime.utcnow().isoformat()
    import json as _json

    # --- Persist Channel 3 archetypes (heuristic, no LLM needed) ---
    for cand in ch3_candidates:
        for inc_id, proc_id in cand.note_pairs:
            fail_tok = cand.failure_token or "failure"
            res_tok = cand.resolution_token or "resolution"
            archetype_id = f"archetype/ch3-{fail_tok}-{res_tok}"
            existing = connection.execute(
                "SELECT occurrence_count, score FROM archetypes WHERE archetype_id = ?",
                (archetype_id,),
            ).fetchone()
            if existing is None:
                if not dry_run:
                    connection.execute(
                        """
                        INSERT INTO archetypes
                            (archetype_id, label, layer_a_description, layer_b_role_schema,
                             occurrence_count, score, relation_type, source_channel, created_at, updated_at)
                        VALUES (?, ?, ?, ?, 1, 0.7, 'evidence_for', 3, ?, ?)
                        """,
                        (
                            archetype_id,
                            f"{fail_tok.title()} / {res_tok.title()} Symmetry",
                            f"Failure-resolution pair: incident has '{fail_tok}', procedure has '{res_tok}'",
                            _json.dumps({"incident": "failure_mode", "procedure": "sync_mechanism"}),
                            now, now,
                        ),
                    )
                result.archetypes_confirmed += 1
            else:
                if not dry_run:
                    new_count = int(existing["occurrence_count"]) + 1
                    connection.execute(
                        "UPDATE archetypes SET occurrence_count = ?, updated_at = ? WHERE archetype_id = ?",
                        (new_count, now, archetype_id),
                    )

            if not dry_run:
                _upsert_archetype_graph_edge(
                    connection,
                    source_note_id=inc_id,
                    target_id=proc_id,
                    relation_type="evidence_for",
                    weight=0.7,
                    archetype_id=archetype_id,
                    archetype_score=0.7,
                    instance_confidence=0.7,
                    updated_at=now,
                )
                result.edges_updated += 1

    # --- LLM validation of channel 2 candidates ---
    if llm_fn is not None and ch2_pairs:
        top = ch2_pairs[:20]
        prompt = (
            "Review these note pairs for abstract structural role similarity "
            "(same role in different domains). "
            "Return JSON: {\"candidates\": [{\"src\": str, \"tgt\": str, "
            "\"valid\": bool, \"archetype_label\": str|null, \"role_schema\": object|null}]}\n\n"
        )
        for a, b, sim in top:
            prompt += f"- {a} ↔ {b} (role-sim={sim:.2f})\n"
        resp = _call_llm_json(prompt, llm_fn)
        if resp is not None:
            for cand in (resp.get("candidates") or []):
                src = str(cand.get("src") or "")
                tgt = str(cand.get("tgt") or "")
                valid = bool(cand.get("valid", False))
                label = str(cand.get("archetype_label") or "").strip()
                role_schema = cand.get("role_schema") or {}

                if valid and src and tgt:
                    archetype_id = f"archetype/ch2-{_slugify_id(label or f'{src}-{tgt}')}"
                    existing = connection.execute(
                        "SELECT occurrence_count FROM archetypes WHERE archetype_id = ?",
                        (archetype_id,),
                    ).fetchone()
                    if not dry_run:
                        if existing is None:
                            connection.execute(
                                """
                                INSERT INTO archetypes
                                    (archetype_id, label, layer_a_description, layer_b_role_schema,
                                     occurrence_count, score, relation_type, source_channel, created_at, updated_at)
                                VALUES (?, ?, ?, ?, 1, 0.8, 'similar_to', 2, ?, ?)
                                """,
                                (
                                    archetype_id,
                                    label or f"Role-similar: {src} / {tgt}",
                                    "Role-vector cosine cluster (Channel 2)",
                                    _json.dumps(role_schema) if isinstance(role_schema, dict) else "{}",
                                    now, now,
                                ),
                            )
                        else:
                            connection.execute(
                                "UPDATE archetypes SET occurrence_count = occurrence_count + 1, updated_at = ? WHERE archetype_id = ?",
                                (now, archetype_id),
                            )
                        _upsert_archetype_graph_edge(
                            connection,
                            source_note_id=src,
                            target_id=tgt,
                            relation_type="similar_to",
                            weight=0.8,
                            archetype_id=archetype_id,
                            archetype_score=0.8,
                            instance_confidence=0.8,
                            updated_at=now,
                        )
                        result.edges_updated += 1
                    result.archetypes_confirmed += 1
                elif not valid:
                    result.archetypes_rejected += 1

    if not dry_run and (result.archetypes_confirmed > 0 or result.edges_updated > 0):
        connection.commit()
    return result


def _slugify_id(text: str) -> str:
    import re as _re
    return _re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:60] or "unknown"


# ---------------------------------------------------------------------------
# Phase 8 — Raw Re-Visitation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RevisitationResult:
    sources_requeued: int = 0
    sources_skipped: int = 0
    context_injected_count: int = 0


def _revisit_context_payload(source_ref: str, related: Any) -> dict[str, Any]:
    items = []
    for item in related or []:
        note_id = getattr(item, "note_id", None) or getattr(item, "id", None)
        title = getattr(item, "title", "")
        summary = getattr(item, "summary", "")
        if isinstance(item, dict):
            note_id = item.get("note_id") or item.get("id") or note_id
            title = item.get("title", title)
            summary = item.get("summary", summary)
        if note_id:
            items.append({"note_id": str(note_id), "title": str(title or ""), "summary": str(summary or "")})
    return {"source_ref": source_ref, "related_notes": items}


def enqueue_revisit_extraction(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    reason: str,
    context: dict[str, Any] | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO extraction_queue(
            source_kind, archive_id, conversation_id, episode_id,
            bridge_id, session_id, task_id, status, content_hash, last_error,
            revisit_of_queue_id, revisit_reason, revisit_context_json
        )
        VALUES (?, ?, ?, ?, COALESCE(?, 'job:raw-revisitation'), ?, ?, 'pending', ?, NULL, ?, ?, ?)
        """,
        (
            str(row["source_kind"]),
            row["archive_id"],
            row["conversation_id"],
            row["episode_id"],
            row["bridge_id"],
            row["session_id"],
            row["task_id"],
            row["content_hash"],
            int(row["id"]),
            reason,
            json.dumps(context or {}, sort_keys=True),
        ),
    )
    return int(cursor.lastrowid)


def run_raw_revisitation(
    connection: sqlite3.Connection,
    config: AppConfig,
    *,
    dry_run: bool = False,
    retrieve_fn: "Any | None" = None,
) -> RevisitationResult:
    result = RevisitationResult()
    if not config.maintenance.requeue_enabled:
        return result

    from .extraction.queue import CURRENT_SCHEMA_VERSION

    min_age = config.maintenance.requeue_min_age_days
    cutoff = (datetime.utcnow().date() - __import__("datetime").timedelta(days=min_age)).isoformat()
    current_model = config.extraction.llm_model

    rows = connection.execute(
        """
        SELECT id, source_kind, archive_id, conversation_id, episode_id,
               bridge_id, session_id, task_id, content_hash,
               extraction_schema_version, extraction_model_version,
               finished_at
        FROM extraction_queue
        WHERE status = 'processed'
          AND finished_at IS NOT NULL
          AND finished_at < ?
          AND revisit_of_queue_id IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM extraction_queue revisit
              WHERE revisit.revisit_of_queue_id = extraction_queue.id
                AND revisit.status IN ('pending', 'processing', 'processed')
          )
          AND (
              extraction_schema_version IS NULL
              OR extraction_schema_version != ?
              OR extraction_model_version IS NULL
              OR extraction_model_version != ?
          )
        ORDER BY finished_at ASC
        LIMIT ?
        """,
        (cutoff, CURRENT_SCHEMA_VERSION, current_model, config.maintenance.requeue_max_per_run),
    ).fetchall()

    for row in rows:
        if result.sources_requeued >= config.maintenance.requeue_max_per_run:
            result.sources_skipped += 1
            continue

        # Context injection
        context_payload: dict[str, Any] | None = None
        if config.maintenance.requeue_context_enabled and retrieve_fn is not None:
            source_kind = str(row["source_kind"])
            source_ref = str(row["archive_id"] or row["conversation_id"] or row["episode_id"] or "")
            if source_ref:
                try:
                    related = retrieve_fn(
                        source_ref,
                        config=config,
                        top_k=config.maintenance.requeue_context_top_k,
                    )
                    if related:
                        context_payload = _revisit_context_payload(source_ref, related)
                        result.context_injected_count += 1
                except Exception:
                    pass

        if not dry_run:
            try:
                enqueue_revisit_extraction(
                    connection,
                    row,
                    reason="schema_or_model_version_drift",
                    context=context_payload,
                )
                result.sources_requeued += 1
            except Exception:
                result.sources_skipped += 1
        else:
            result.sources_requeued += 1

    if not dry_run and result.sources_requeued > 0:
        connection.commit()
    return result
