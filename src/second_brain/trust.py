from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import math
import sqlite3

from .config import AppConfig


CURRENT_QUERY_HINTS = {
    "current",
    "current-state",
    "current state",
    "now",
    "latest",
    "today",
    "currently",
}

HISTORICAL_QUERY_HINTS = {
    "before",
    "previous",
    "historical",
    "history",
    "used to",
    "earlier",
    "then",
    "prior",
}

DEFAULT_RERANK_WEIGHTS = {
    "bm25_weight": 0.30,
    "semantic_weight": 0.25,
    "graph_weight": 0.20,
    "type_prior_weight": 0.15,
    "freshness_weight": 0.10,
    "trust_weight": 0.15,
}


@dataclass(slots=True)
class TrustSignal:
    usefulness_score: float
    selected_count: int
    useful_count: int
    not_useful_count: int
    failure_count: int


@dataclass(slots=True)
class FreshnessSignal:
    staleness_score: float
    freshness_state: str
    contradiction_count: int
    linked_incident_count: int
    failure_signal_count: int
    miss_signal_count: int
    superseded_flag: bool
    newer_evidence_count: int
    relearn_stage: str | None


@dataclass(slots=True)
class RerankModel:
    sample_count: int
    weights: dict[str, float]
    fallback: bool


def query_temporality(query: str) -> str:
    lowered = query.casefold()
    if any(hint in lowered for hint in HISTORICAL_QUERY_HINTS):
        return "historical"
    if any(hint in lowered for hint in CURRENT_QUERY_HINTS):
        return "current"
    return "current"


def freshness_multiplier(config: AppConfig, *, state: str, temporality: str) -> float:
    if temporality == "historical":
        if state == "contested":
            return 0.75
        if state == "stale":
            return max(0.6, 1.0 - config.trust.historical_query_relief)
        if state == "suspect":
            return max(0.75, 1.0 - config.trust.historical_query_relief / 2.0)
        return 1.0

    if state == "contested":
        return max(0.05, 1.0 - config.trust.current_query_contested_penalty)
    if state == "stale":
        return max(0.10, 1.0 - config.trust.current_query_stale_penalty)
    if state == "suspect":
        return 0.70
    if state == "aging":
        return 0.88
    return 1.0


def compute_note_trust_stats(connection: sqlite3.Connection) -> dict[str, TrustSignal]:
    rows = connection.execute(
        """
        SELECT
            rh.note_id AS note_id,
            COUNT(*) AS selected_count,
            SUM(CASE WHEN rq.top1_correct = 1 AND rh.rank = 1 THEN 1 ELSE 0 END) AS successful_top1_count,
            SUM(CASE WHEN rq.useful = 1 AND rh.rank <= 5 THEN 1 ELSE 0 END) AS successful_top5_count,
            SUM(CASE WHEN COALESCE(rh.useful, rq.useful) = 1 THEN 1 ELSE 0 END) AS useful_count,
            SUM(CASE WHEN COALESCE(rh.useful, rq.useful) = 0 THEN 1 ELSE 0 END) AS not_useful_count,
            SUM(CASE WHEN rq.top1_correct = 0 AND rh.rank = 1 THEN 1 ELSE 0 END) AS failure_count,
            MAX(rq.created_at) AS last_used_at
        FROM retrieval_hits rh
        JOIN retrieval_queries rq ON rq.id = rh.query_id
        GROUP BY rh.note_id
        """
    ).fetchall()

    signals: dict[str, TrustSignal] = {}
    with connection:
        for row in rows:
            note_id = str(row["note_id"])
            selected_count = int(row["selected_count"] or 0)
            successful_top1_count = int(row["successful_top1_count"] or 0)
            successful_top5_count = int(row["successful_top5_count"] or 0)
            useful_count = int(row["useful_count"] or 0)
            not_useful_count = int(row["not_useful_count"] or 0)
            failure_count = int(row["failure_count"] or 0)
            raw_score = (
                1.4 * successful_top1_count
                + 0.8 * successful_top5_count
                + 0.5 * useful_count
                - 0.9 * not_useful_count
                - 1.1 * failure_count
            )
            usefulness_score = 1.0 / (1.0 + math.exp(-(raw_score / max(selected_count, 1))))
            connection.execute(
                """
                INSERT INTO note_trust(
                    note_id, usefulness_score, successful_top1_count, successful_top5_count,
                    selected_count, useful_count, not_useful_count, failure_count, last_used_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(note_id) DO UPDATE SET
                    usefulness_score = excluded.usefulness_score,
                    successful_top1_count = excluded.successful_top1_count,
                    successful_top5_count = excluded.successful_top5_count,
                    selected_count = excluded.selected_count,
                    useful_count = excluded.useful_count,
                    not_useful_count = excluded.not_useful_count,
                    failure_count = excluded.failure_count,
                    last_used_at = excluded.last_used_at,
                    updated_at = excluded.updated_at
                """,
                (
                    note_id,
                    usefulness_score,
                    successful_top1_count,
                    successful_top5_count,
                    selected_count,
                    useful_count,
                    not_useful_count,
                    failure_count,
                    row["last_used_at"],
                ),
            )
            signals[note_id] = TrustSignal(
                usefulness_score=usefulness_score,
                selected_count=selected_count,
                useful_count=useful_count,
                not_useful_count=not_useful_count,
                failure_count=failure_count,
            )
    return signals


def load_note_trust(connection: sqlite3.Connection, note_ids: list[str]) -> dict[str, TrustSignal]:
    if not note_ids:
        return {}
    placeholders = ",".join("?" for _ in note_ids)
    rows = connection.execute(
        f"""
        SELECT note_id, usefulness_score, selected_count, useful_count, not_useful_count, failure_count
        FROM note_trust
        WHERE note_id IN ({placeholders})
        """,
        note_ids,
    ).fetchall()
    return {
        str(row["note_id"]): TrustSignal(
            usefulness_score=float(row["usefulness_score"] or 0.0),
            selected_count=int(row["selected_count"] or 0),
            useful_count=int(row["useful_count"] or 0),
            not_useful_count=int(row["not_useful_count"] or 0),
            failure_count=int(row["failure_count"] or 0),
        )
        for row in rows
    }


def load_note_freshness(connection: sqlite3.Connection, note_ids: list[str]) -> dict[str, FreshnessSignal]:
    if not note_ids:
        return {}
    placeholders = ",".join("?" for _ in note_ids)
    rows = connection.execute(
        f"""
        SELECT note_id, staleness_score, freshness_state, contradiction_count, linked_incident_count,
               failure_signal_count, miss_signal_count, superseded_flag, newer_evidence_count, relearn_stage
        FROM note_freshness
        WHERE note_id IN ({placeholders})
        """,
        note_ids,
    ).fetchall()
    return {
        str(row["note_id"]): FreshnessSignal(
            staleness_score=float(row["staleness_score"] or 0.0),
            freshness_state=str(row["freshness_state"] or "fresh"),
            contradiction_count=int(row["contradiction_count"] or 0),
            linked_incident_count=int(row["linked_incident_count"] or 0),
            failure_signal_count=int(row["failure_signal_count"] or 0),
            miss_signal_count=int(row["miss_signal_count"] or 0),
            superseded_flag=bool(int(row["superseded_flag"] or 0)),
            newer_evidence_count=int(row["newer_evidence_count"] or 0),
            relearn_stage=str(row["relearn_stage"]) if row["relearn_stage"] is not None else None,
        )
        for row in rows
    }


def load_rerank_model(connection: sqlite3.Connection) -> RerankModel:
    row = connection.execute(
        "SELECT sample_count, weights_json FROM rerank_weights WHERE model_name = 'phase4-local-linear'",
    ).fetchone()
    if row is None:
        return RerankModel(sample_count=0, weights=dict(DEFAULT_RERANK_WEIGHTS), fallback=True)
    try:
        weights = json.loads(str(row["weights_json"]))
    except json.JSONDecodeError:
        weights = dict(DEFAULT_RERANK_WEIGHTS)
    merged = dict(DEFAULT_RERANK_WEIGHTS)
    for key, value in weights.items():
        merged[str(key)] = float(value)
    return RerankModel(sample_count=int(row["sample_count"] or 0), weights=merged, fallback=False)


def train_rerank_model(connection: sqlite3.Connection, config: AppConfig) -> RerankModel:
    rows = connection.execute(
        """
        SELECT
            rhf.exact_hit,
            rhf.bm25_score,
            rhf.semantic_score,
            rhf.graph_score,
            rhf.freshness_score,
            rhf.type_prior_score,
            rhf.trust_score,
            COALESCE(rh.useful, rq.useful) AS useful_label,
            rq.top1_correct,
            rh.rank
        FROM retrieval_hit_features rhf
        JOIN retrieval_hits rh ON rh.query_id = rhf.query_id AND rh.note_id = rhf.note_id
        JOIN retrieval_queries rq ON rq.id = rhf.query_id
        WHERE COALESCE(rh.useful, rq.useful) IS NOT NULL OR rq.top1_correct IS NOT NULL
        """
    ).fetchall()

    if len(rows) < config.trust.min_training_samples:
        return RerankModel(sample_count=len(rows), weights=dict(DEFAULT_RERANK_WEIGHTS), fallback=True)

    feature_totals = {
        "bm25_weight": [0.0, 0.0],
        "semantic_weight": [0.0, 0.0],
        "graph_weight": [0.0, 0.0],
        "freshness_weight": [0.0, 0.0],
        "type_prior_weight": [0.0, 0.0],
        "trust_weight": [0.0, 0.0],
    }
    counts = {"positive": 0, "negative": 0}

    for row in rows:
        positive = False
        useful_label = row["useful_label"]
        if useful_label is not None and int(useful_label) == 1:
            positive = True
        elif row["top1_correct"] is not None and int(row["top1_correct"]) == 1 and int(row["rank"] or 99) == 1:
            positive = True

        bucket = 0 if positive else 1
        counts["positive" if positive else "negative"] += 1
        feature_totals["bm25_weight"][bucket] += max(float(row["bm25_score"] or 0.0), 0.0)
        feature_totals["semantic_weight"][bucket] += max(float(row["semantic_score"] or 0.0), 0.0)
        feature_totals["graph_weight"][bucket] += max(float(row["graph_score"] or 0.0), 0.0)
        feature_totals["freshness_weight"][bucket] += max(float(row["freshness_score"] or 0.0), 0.0)
        feature_totals["type_prior_weight"][bucket] += max(float(row["type_prior_score"] or 0.0), 0.0)
        feature_totals["trust_weight"][bucket] += max(float(row["trust_score"] or 0.0), 0.0)

    weights = dict(DEFAULT_RERANK_WEIGHTS)
    positive_count = max(counts["positive"], 1)
    negative_count = max(counts["negative"], 1)
    for key, (pos_total, neg_total) in feature_totals.items():
        delta = (pos_total / positive_count) - (neg_total / negative_count)
        weights[key] = max(0.02, min(0.60, DEFAULT_RERANK_WEIGHTS[key] + delta * 0.25))

    with connection:
        connection.execute(
            """
            INSERT INTO rerank_weights(model_name, sample_count, weights_json, updated_at)
            VALUES ('phase4-local-linear', ?, ?, datetime('now'))
            ON CONFLICT(model_name) DO UPDATE SET
                sample_count = excluded.sample_count,
                weights_json = excluded.weights_json,
                updated_at = excluded.updated_at
            """,
            (len(rows), json.dumps(weights, sort_keys=True)),
        )
    return RerankModel(sample_count=len(rows), weights=weights, fallback=False)


def _days_since(raw_date: str | None) -> int:
    if not raw_date:
        return 365
    try:
        year, month, day = (int(part) for part in raw_date.split("-")[:3])
        return max((date.today() - date(year, month, day)).days, 0)
    except Exception:
        return 365


def _freshness_state_for(score: float, contradiction_count: int) -> str:
    if contradiction_count > 0 or score >= 0.90:
        return "contested"
    if score >= 0.75:
        return "stale"
    if score >= 0.55:
        return "suspect"
    if score >= 0.35:
        return "aging"
    return "fresh"


def _relearn_stage_for(score: float, contradiction_count: int) -> str | None:
    if contradiction_count > 0 or score >= 0.90:
        return "full_relearn_task"
    if score >= 0.75:
        return "targeted_relearn_task"
    if score >= 0.55:
        return "crosscheck"
    if score >= 0.35:
        return "reverify"
    return None


def compute_note_freshness(
    connection: sqlite3.Connection,
    *,
    contradiction_counts_override: dict[str, int] | None = None,
) -> dict[str, FreshnessSignal]:
    rows = connection.execute(
        """
        SELECT
            n.id,
            n.type,
            n.status,
            n.updated_at,
            n.created_at,
            n.last_verified_at,
            COALESCE(nt.failure_count, 0) AS failure_count,
            COALESCE(nt.not_useful_count, 0) AS not_useful_count,
            COALESCE(nt.usefulness_score, 0.0) AS usefulness_score,
            CASE WHEN n.status IN ('superseded', 'reversed') OR n.valid_to IS NOT NULL THEN 1 ELSE 0 END AS superseded_flag,
            COALESCE(ic.linked_incident_count, 0) AS linked_incident_count,
            COALESCE(ic.newer_evidence_count, 0) AS newer_evidence_count
        FROM notes n
        LEFT JOIN note_trust nt ON nt.note_id = n.id
        LEFT JOIN (
            SELECT
                ge.target_id AS note_id,
                COUNT(*) AS linked_incident_count,
                SUM(CASE WHEN i.updated_at > p.updated_at THEN 1 ELSE 0 END) AS newer_evidence_count
            FROM graph_edges ge
            JOIN notes i ON i.id = ge.source_note_id AND i.type = 'incident'
            JOIN notes p ON p.id = ge.target_id
            WHERE ge.relation_type IN ('linked_procedure', 'linked_decision')
            GROUP BY ge.target_id
        ) ic ON ic.note_id = n.id
        """
    ).fetchall()

    contradiction_rows = connection.execute(
        """
        SELECT target_id AS note_id, COUNT(*) AS contradiction_count
        FROM graph_edges ge
        JOIN notes src ON src.id = ge.source_note_id
        WHERE src.type = 'incident' AND ge.relation_type IN ('linked_decision', 'linked_procedure')
        GROUP BY target_id
        """
    ).fetchall()
    contradiction_counts = {str(row["note_id"]): int(row["contradiction_count"] or 0) for row in contradiction_rows}
    if contradiction_counts_override:
        for note_id, count in contradiction_counts_override.items():
            contradiction_counts[note_id] = contradiction_counts.get(note_id, 0) + int(count)

    signals: dict[str, FreshnessSignal] = {}
    with connection:
        for row in rows:
            note_id = str(row["id"])
            note_type = str(row["type"])
            last_reference = str(row["last_verified_at"] or row["updated_at"] or row["created_at"] or "")
            age_days = _days_since(last_reference)
            age_score = min(1.0, age_days / 365.0)
            note_weight = 1.0 if note_type in {"decision", "concept", "source"} else 0.75 if note_type == "procedure" else 0.50
            contradiction_count = contradiction_counts.get(note_id, 0)
            failure_signal_count = int(row["failure_count"] or 0)
            miss_signal_count = int(row["not_useful_count"] or 0)
            linked_incident_count = int(row["linked_incident_count"] or 0)
            newer_evidence_count = int(row["newer_evidence_count"] or 0)
            superseded_flag = bool(int(row["superseded_flag"] or 0))
            usefulness_score = float(row["usefulness_score"] or 0.0)

            staleness_score = (
                0.55 * age_score
                + 0.10 * min(linked_incident_count / 3.0, 1.0)
                + 0.10 * min(newer_evidence_count / 3.0, 1.0)
                + 0.10 * min(failure_signal_count / 3.0, 1.0)
                + 0.10 * min(miss_signal_count / 3.0, 1.0)
                + 0.05 * (1.0 if superseded_flag else 0.0)
                + 0.05 * (1.0 - usefulness_score)
            ) * note_weight
            if contradiction_count > 0:
                staleness_score = max(staleness_score, 0.90)

            freshness_state = _freshness_state_for(staleness_score, contradiction_count)
            relearn_stage = _relearn_stage_for(staleness_score, contradiction_count)

            connection.execute(
                """
                INSERT INTO note_freshness(
                    note_id, staleness_score, freshness_state, contradiction_count,
                    linked_incident_count, failure_signal_count, miss_signal_count,
                    superseded_flag, newer_evidence_count, relearn_stage, last_computed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(note_id) DO UPDATE SET
                    staleness_score = excluded.staleness_score,
                    freshness_state = excluded.freshness_state,
                    contradiction_count = excluded.contradiction_count,
                    linked_incident_count = excluded.linked_incident_count,
                    failure_signal_count = excluded.failure_signal_count,
                    miss_signal_count = excluded.miss_signal_count,
                    superseded_flag = excluded.superseded_flag,
                    newer_evidence_count = excluded.newer_evidence_count,
                    relearn_stage = excluded.relearn_stage,
                    last_computed_at = excluded.last_computed_at
                """,
                (
                    note_id,
                    staleness_score,
                    freshness_state,
                    contradiction_count,
                    linked_incident_count,
                    failure_signal_count,
                    miss_signal_count,
                    1 if superseded_flag else 0,
                    newer_evidence_count,
                    relearn_stage,
                ),
            )
            signals[note_id] = FreshnessSignal(
                staleness_score=staleness_score,
                freshness_state=freshness_state,
                contradiction_count=contradiction_count,
                linked_incident_count=linked_incident_count,
                failure_signal_count=failure_signal_count,
                miss_signal_count=miss_signal_count,
                superseded_flag=superseded_flag,
                newer_evidence_count=newer_evidence_count,
                relearn_stage=relearn_stage,
            )
    return signals
