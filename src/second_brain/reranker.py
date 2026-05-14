from __future__ import annotations

import math

from .config import AppConfig
from .models import (
    RankedHits,
    RerankFeatureRow,
    RetrieveHit,
    ScoredArchiveCandidate,
    ScoredCandidate,
    SignalEnrichedCandidatePool,
)
from .trust import RerankModel, load_rerank_model

# Maximum historical bonus a note can receive (used for quality normalization).
_HISTORICAL_BONUS_MAX = 0.20


def _bm25_abs(bm25_score: float | None, saturation: float) -> float:
    """Absolute BM25 relevance, normalized to [0, 1] via saturation point."""
    if bm25_score is None:
        return 0.0
    return min(abs(bm25_score) / max(saturation, 1e-6), 1.0)


def _raw_score(candidate) -> float | None:
    if (
        candidate.graph_score is not None
        and candidate.semantic_score is None
        and candidate.learned_score is None
        and candidate.bm25_score is None
    ):
        return candidate.graph_score
    if candidate.learned_score is not None:
        return candidate.learned_score
    if candidate.semantic_score is not None:
        return candidate.semantic_score
    return candidate.bm25_score


def _historical_bonus(*, temporality: str, freshness_state: str) -> float:
    if temporality != "historical":
        return 0.0
    if freshness_state == "stale":
        return 0.20
    if freshness_state == "suspect":
        return 0.12
    if freshness_state == "aging":
        return 0.06
    return 0.0


def _excerpt(candidate, freshness_state: str) -> str:
    excerpt = candidate.summary or candidate.body[:240]
    if candidate.relation_types:
        relation_summary = ", ".join(candidate.relation_types[:3])
        if relation_summary:
            excerpt = f"{excerpt} | graph: {relation_summary}".strip(" |")
    if freshness_state not in {"fresh", ""}:
        excerpt = f"{excerpt} | freshness: {freshness_state}".strip(" |")
    return excerpt


def _weights(config: AppConfig, model: RerankModel) -> dict[str, float]:
    weights = {
        "bm25_weight": config.retrieval.bm25_weight,
        "semantic_weight": config.retrieval.semantic_weight,
        "graph_weight": config.graph.graph_weight,
        "type_prior_weight": config.retrieval.type_prior_weight,
        "freshness_weight": config.retrieval.freshness_weight,
        "trust_weight": config.retrieval.memory_strength_weight,
        "importance_weight": config.retrieval.importance_weight,
        "quality_weight": config.retrieval.quality_weight,
    }
    if not model.fallback:
        weights.update(model.weights)
    return weights


def _compute_relevance_score(
    candidate,
    *,
    weights: dict[str, float],
    graph_max_score: float,
    bm25_saturation: float,
) -> float:
    """Query-dependent sub-score [0, 1]. Uses absolute (not rank-based) BM25."""
    bm25_abs = _bm25_abs(candidate.bm25_score, bm25_saturation)
    semantic_abs = max(
        candidate.learned_score if candidate.learned_score is not None else (candidate.semantic_score or 0.0),
        0.0,
    )
    graph_abs = max(candidate.graph_score or 0.0, 0.0) / graph_max_score

    relevance_weight_sum = weights["bm25_weight"] + weights["semantic_weight"] + weights["graph_weight"]
    if relevance_weight_sum < 1e-6:
        return 0.0

    relevance_raw = (
        weights["bm25_weight"] * bm25_abs
        + weights["semantic_weight"] * semantic_abs
        + weights["graph_weight"] * graph_abs
    )
    return relevance_raw / relevance_weight_sum


def _compute_quality_score(
    signals,
    *,
    weights: dict[str, float],
    importance_norm: float,
    hist_bonus: float,
) -> float:
    """Node-intrinsic sub-score [0, 1]. Independent of the query."""
    quality_weight_sum_max = (
        weights["type_prior_weight"]
        + weights["freshness_weight"]
        + weights["trust_weight"]
        + weights["importance_weight"]
        + weights["quality_weight"]
        + _HISTORICAL_BONUS_MAX
    )
    if quality_weight_sum_max < 1e-6:
        return 0.0

    quality_raw = (
        weights["type_prior_weight"] * signals.type_prior_score
        + weights["freshness_weight"] * signals.freshness_score
        + weights["trust_weight"] * signals.trust_score
        + weights["importance_weight"] * importance_norm
        + weights["quality_weight"] * (1.0 if signals.quality_pass else 0.0)
        + hist_bonus
    )
    return quality_raw / quality_weight_sum_max


def _score_wiki_candidates(
    config: AppConfig,
    enriched_pool: SignalEnrichedCandidatePool,
    *,
    rerank_model: RerankModel,
) -> list[ScoredCandidate]:
    parsed_query = enriched_pool.parsed_query
    query_type = parsed_query.query_type
    classifier_confidence = parsed_query.classifier_confidence
    temporality = parsed_query.temporality
    wiki_items = enriched_pool.wiki_candidates
    graph_max_score = max(((item.candidate.graph_score or 0.0) for item in wiki_items), default=0.0) or 1.0
    importance_max = max(((item.signals.importance_score or 0.0) for item in wiki_items), default=0.0) or 1.0
    weights = _weights(config, rerank_model)

    # Adjust policy-driven weight overrides.
    freshness_weight = weights["freshness_weight"]
    type_prior_weight = weights["type_prior_weight"]
    if query_type is not None and (classifier_confidence or 0.0) >= 1.0:
        type_prior_weight = max(type_prior_weight, 0.25)
    if query_type == "what_changed":
        freshness_weight = max(freshness_weight, 0.25)
    weights = dict(weights, freshness_weight=freshness_weight, type_prior_weight=type_prior_weight)

    scored: list[ScoredCandidate] = []

    for item in wiki_items:
        candidate = item.candidate
        signals = item.signals
        freshness_state = signals.freshness_state
        importance_norm = max(signals.importance_score or 0.0, 0.0) / importance_max
        hist_bonus = _historical_bonus(temporality=temporality, freshness_state=freshness_state)

        if candidate.exact_rank is not None:
            # Exact matches bypass composite scoring for rank column but still
            # emit a 2D vector for observability.
            relevance_score = 1.0
            quality_score = _compute_quality_score(
                signals,
                weights=weights,
                importance_norm=importance_norm,
                hist_bonus=hist_bonus,
            )
            final_score = 1_000_000.0 - candidate.exact_rank
            raw_score: float | None = 1.0
        else:
            relevance_score = _compute_relevance_score(
                candidate,
                weights=weights,
                graph_max_score=graph_max_score,
                bm25_saturation=config.retrieval.bm25_saturation,
            )
            quality_score = _compute_quality_score(
                signals,
                weights=weights,
                importance_norm=importance_norm,
                hist_bonus=hist_bonus,
            )
            final_score = relevance_score * quality_score
            raw_score = _raw_score(candidate)

        hit = RetrieveHit(
            id=candidate.id,
            title=candidate.title,
            type=candidate.type,
            status=candidate.status,
            chain_id=candidate.chain_id,
            freshness_state=freshness_state,
            lifecycle_state=candidate.lifecycle_state,
            body_path=candidate.body_path,
            summary=candidate.summary,
            channels=sorted(candidate.channels),
            raw_score=raw_score,
            final_score=final_score,
            excerpt=_excerpt(candidate, freshness_state),
            relation_types=list(candidate.relation_types or []),
            relevance_score=relevance_score,
            quality_score=quality_score,
        )
        feature_row = RerankFeatureRow(
            note_id=candidate.id,
            exact_hit=1 if candidate.exact_rank is not None else 0,
            bm25_rank=candidate.bm25_rank,
            bm25_score=candidate.bm25_score,
            semantic_score=candidate.learned_score if candidate.learned_score is not None else candidate.semantic_score,
            graph_score=candidate.graph_score,
            freshness_score=signals.freshness_score,
            type_prior_score=signals.type_prior_score,
            trust_score=signals.trust_score,
            importance_score=signals.importance_score,
            quality_pass=signals.quality_pass,
            quality_score=quality_score,
            passive_feedback_state=signals.passive_feedback_state,
            passive_feedback_confidence=signals.passive_feedback_confidence,
        )
        scored.append(ScoredCandidate(candidate=candidate, signals=signals, hit=hit, feature_row=feature_row))

    return sorted(scored, key=lambda item: (-item.hit.final_score, item.hit.title.casefold(), item.hit.id))


def _apply_relevance_gate(
    scored: list[ScoredCandidate],
    min_relevance_score: float,
) -> tuple[list[ScoredCandidate], int]:
    """Filter candidates below relevance threshold. Exact matches always pass.
    Returns (passing, suppressed_count)."""
    if min_relevance_score <= 0.0:
        return scored, 0
    passing: list[ScoredCandidate] = []
    suppressed = 0
    for item in scored:
        if item.candidate.exact_rank is not None or item.hit.relevance_score >= min_relevance_score:
            passing.append(item)
        else:
            suppressed += 1
    return passing, suppressed


def _score_archive_candidates(
    enriched_pool: SignalEnrichedCandidatePool,
    *,
    top_k: int,
) -> list[ScoredArchiveCandidate]:
    scored: list[ScoredArchiveCandidate] = []
    for item in enriched_pool.archive_candidates:
        candidate = item.candidate
        score = 1.0 - (((candidate.bm25_rank or 1) - 1) / max(top_k, 1))
        scored.append(
            ScoredArchiveCandidate(
                candidate=candidate,
                signals=item.signals,
                hit=RetrieveHit(
                    id=candidate.id,
                    title=f"Archive chunk {candidate.chunk_index}",
                    type="archive_chunk",
                    status=None,
                    chain_id=None,
                    freshness_state=None,
                    lifecycle_state=None,
                    body_path=candidate.archive_path,
                    summary=candidate.summary,
                    channels=["archive"],
                    raw_score=candidate.bm25_score,
                    final_score=score,
                    excerpt=candidate.excerpt,
                    relation_types=[],
                ),
            )
        )
    return sorted(scored, key=lambda item: (-item.hit.final_score, item.candidate.archive_id, item.candidate.chunk_index))[:top_k]


def _apply_top_k_with_chain_diversity(scored: list[ScoredCandidate], top_k: int) -> list[ScoredCandidate]:
    if top_k <= 0:
        return []
    selected: list[ScoredCandidate] = []
    deferred: list[ScoredCandidate] = []
    chain_counts: dict[str, int] = {}
    chain_cap = max(2, math.ceil(top_k / 3))

    for item in scored:
        chain_id = item.candidate.chain_id
        if chain_id and chain_counts.get(chain_id, 0) >= chain_cap:
            deferred.append(item)
            continue
        selected.append(item)
        if chain_id:
            chain_counts[chain_id] = chain_counts.get(chain_id, 0) + 1
        if len(selected) >= top_k:
            break

    if len(selected) < top_k:
        selected.extend(deferred[: top_k - len(selected)])
    return selected[:top_k]


def _dynamic_top_k(
    config: AppConfig,
    pool_size: int,
    caller_top_k: int | None,
) -> int:
    """Compute emission count from pool size. caller_top_k acts as an upper cap."""
    target = max(config.retrieval.min_hits, math.ceil(pool_size * config.retrieval.pool_fraction))
    target = min(target, config.retrieval.max_hits)

    # Token budget backstop (rarely binding vs max_hits, but enforced).
    available_tokens = config.retrieval.total_context_budget - config.retrieval.pinned_token_budget
    budget_k = max(1, available_tokens // max(config.retrieval.tokens_per_hit_estimate, 1))
    target = min(target, budget_k)

    # Explicit caller override is a hard cap.
    if caller_top_k is not None and caller_top_k > 0:
        target = min(target, caller_top_k)

    return max(target, 1)


def rerank(
    config: AppConfig,
    enriched_pool: SignalEnrichedCandidatePool,
    *,
    connection,
    top_k: int | None = None,
) -> RankedHits:
    # Resolve caller top_k: explicit arg overrides parsed_query default.
    explicit_top_k = top_k if top_k is not None else enriched_pool.parsed_query.top_k

    rerank_model = load_rerank_model(connection)
    scored_wiki = _score_wiki_candidates(config, enriched_pool, rerank_model=rerank_model)

    # Apply relevance gate (exact matches are exempt).
    passing, suppressed_count = _apply_relevance_gate(scored_wiki, config.retrieval.min_relevance_score)
    passed_count = len(passing)

    # Dynamic top-k from passing pool.
    final_k = _dynamic_top_k(config, passed_count, explicit_top_k)

    wiki_hits = _apply_top_k_with_chain_diversity(passing, final_k)
    archive_hits = _score_archive_candidates(enriched_pool, top_k=max(final_k, 3))

    return RankedHits(
        wiki_hits=wiki_hits,
        archive_hits=archive_hits,
        suppressed_by_gate_count=suppressed_count,
        passed_gate_count=passed_count,
    )
