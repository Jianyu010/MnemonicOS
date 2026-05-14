from __future__ import annotations

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


def _normalize_bm25(rank: int | None, max_rank: int) -> float:
    if rank is None:
        return 0.0
    return max(0.0, 1.0 - ((rank - 1) / max(max_rank, 1)))


def _quality_score(quality_pass: bool) -> float:
    return 1.0 if quality_pass else 0.0


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
    bm25_max_rank = max(((item.candidate.bm25_rank or 0) for item in wiki_items), default=0) or 1
    graph_max_score = max(((item.candidate.graph_score or 0.0) for item in wiki_items), default=0.0) or 1.0
    importance_max = max(((item.signals.importance_score or 0.0) for item in wiki_items), default=0.0) or 1.0
    weights = _weights(config, rerank_model)
    scored: list[ScoredCandidate] = []

    for item in wiki_items:
        candidate = item.candidate
        signals = item.signals
        freshness_state = signals.freshness_state
        quality_score = _quality_score(signals.quality_pass)
        importance_norm = max(signals.importance_score or 0.0, 0.0) / importance_max

        if candidate.exact_rank is not None:
            final_score = 1_000_000.0 - candidate.exact_rank
            raw_score = 1.0
        else:
            bm25_norm = _normalize_bm25(candidate.bm25_rank, bm25_max_rank)
            semantic_norm = max(
                candidate.learned_score if candidate.learned_score is not None else (candidate.semantic_score or 0.0),
                0.0,
            )
            graph_norm = max(candidate.graph_score or 0.0, 0.0) / graph_max_score
            freshness_weight = weights["freshness_weight"]
            type_prior_weight = weights["type_prior_weight"]
            if query_type is not None and (classifier_confidence or 0.0) >= 1.0:
                type_prior_weight = max(type_prior_weight, 0.25)
            if query_type == "what_changed":
                freshness_weight = max(freshness_weight, 0.25)

            final_score = (
                weights["bm25_weight"] * bm25_norm
                + weights["semantic_weight"] * semantic_norm
                + weights["graph_weight"] * graph_norm
                + type_prior_weight * signals.type_prior_score
                + freshness_weight * signals.freshness_score
                + weights["trust_weight"] * signals.trust_score
                + weights["importance_weight"] * importance_norm
                + weights["quality_weight"] * quality_score
                + config.retrieval.exact_alias_weight * (1.0 if "exact" in candidate.channels else 0.0)
                + _historical_bonus(temporality=temporality, freshness_state=freshness_state)
            ) * signals.freshness_multiplier
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
    chain_cap = 2

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


def rerank(
    config: AppConfig,
    enriched_pool: SignalEnrichedCandidatePool,
    *,
    connection,
    top_k: int | None = None,
) -> RankedHits:
    requested_top_k = top_k or enriched_pool.parsed_query.top_k
    rerank_model = load_rerank_model(connection)
    scored_wiki = _score_wiki_candidates(config, enriched_pool, rerank_model=rerank_model)
    wiki_hits = _apply_top_k_with_chain_diversity(scored_wiki, requested_top_k)
    archive_hits = _score_archive_candidates(enriched_pool, top_k=requested_top_k)
    return RankedHits(wiki_hits=wiki_hits, archive_hits=archive_hits)
