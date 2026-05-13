from __future__ import annotations

from datetime import date
import sqlite3

from .config import AppConfig
from .models import (
    ArchiveCandidate,
    ArchiveSignals,
    CandidatePool,
    NoteSignals,
    RecallCandidate,
    SignalEnrichedArchiveCandidate,
    SignalEnrichedCandidate,
    SignalEnrichedCandidatePool,
)
from .query_parser import TYPE_PRIORS, ParsedQuery
from .trust import freshness_multiplier, load_note_freshness, load_note_trust


CONTENT_MARKERS = frozenset({"WHEN", "DO", "Steps", "Cause", "Fix", "Avoid", "NOT", "->", "→"})
PLACEHOLDER_SUMMARIES = frozenset({"todo", "tbd", "none", "n/a", "placeholder", "summary"})
PASSIVE_FEEDBACK_PRIORITY = {
    "passive_used_likely": 1,
    "passive_useful_likely": 2,
    "passive_misleading_likely": 3,
}


def type_prior_score(query_type: str | None, note_type: str) -> float:
    if query_type is None:
        return 0.0
    return TYPE_PRIORS.get(query_type, {}).get(note_type, 0.0)


def date_freshness_score(candidate: RecallCandidate) -> float:
    raw_date = candidate.last_verified_at or candidate.updated_at or candidate.created_at
    if not raw_date:
        return 0.2
    try:
        year, month, day = (int(part) for part in raw_date.split("-")[:3])
        delta = (date.today() - date(year, month, day)).days
    except Exception:
        return 0.2
    return max(0.0, 1.0 - min(delta, 365) / 365.0)


def _text_words(text: str) -> list[str]:
    return [word for word in text.replace("|", " ").split() if word.strip()]


def classify_summary_quality(*, title: str, summary: str, excerpt: str = "") -> tuple[str, list[str], bool]:
    stripped_title = title.strip()
    stripped_summary = summary.strip()
    stripped_excerpt = excerpt.strip()
    reasons: list[str] = []

    if not stripped_summary:
        reasons.append("missing_summary")
        if _excerpt_has_signal(stripped_excerpt):
            return "weak", reasons, True
        reasons.append("empty_content_signal")
        return "none", reasons, False

    if stripped_summary.casefold() in PLACEHOLDER_SUMMARIES:
        reasons.append("placeholder_summary")
        return "none", reasons, False

    if stripped_summary.casefold() == stripped_title.casefold():
        reasons.append("title_only_summary")
        if _excerpt_has_signal(stripped_excerpt):
            return "title_only", reasons, True
        reasons.append("empty_content_signal")
        return "title_only", reasons, False

    summary_words = _text_words(stripped_summary)
    if len(summary_words) >= 8 or any(marker in stripped_summary for marker in CONTENT_MARKERS):
        return "useful", reasons, True

    if _excerpt_has_signal(stripped_excerpt):
        reasons.append("weak_actionability")
        return "weak", reasons, True

    reasons.append("weak_actionability")
    return "weak", reasons, False


def _excerpt_has_signal(excerpt: str) -> bool:
    if not excerpt:
        return False
    if len(_text_words(excerpt)) >= 20:
        return True
    return any(marker in excerpt for marker in CONTENT_MARKERS)


def _importance_scores(connection: sqlite3.Connection, note_ids: list[str]) -> dict[str, float]:
    if not note_ids:
        return {}
    placeholders = ",".join("?" * len(note_ids))
    try:
        rows = connection.execute(
            f"SELECT note_id, importance_score FROM note_importance WHERE note_id IN ({placeholders})",
            note_ids,
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(row["note_id"]): float(row["importance_score"] or 0.0) for row in rows}


def _confidence(value: object) -> float:
    try:
        return max(0.0, min(float(value or 0.0), 1.0))
    except (TypeError, ValueError):
        return 0.0


def _passive_feedback_signals(connection: sqlite3.Connection, note_ids: list[str]) -> dict[str, tuple[str, float]]:
    if not note_ids:
        return {}
    placeholders = ",".join("?" * len(note_ids))
    try:
        rows = connection.execute(
            f"""
            SELECT note_id, feedback_state, confidence
            FROM note_passive_feedback
            WHERE note_id IN ({placeholders})
            """,
            note_ids,
        ).fetchall()
    except sqlite3.OperationalError:
        return {}

    selected: dict[str, tuple[str, float]] = {}
    for row in rows:
        note_id = str(row["note_id"])
        state = str(row["feedback_state"] or "")
        if state not in PASSIVE_FEEDBACK_PRIORITY:
            continue
        confidence = _confidence(row["confidence"])
        current = selected.get(note_id)
        current_key = (
            current[1],
            PASSIVE_FEEDBACK_PRIORITY[current[0]],
        ) if current is not None else None
        candidate_key = (confidence, PASSIVE_FEEDBACK_PRIORITY[state])
        if current is None or candidate_key > current_key:
            selected[note_id] = (state, confidence)
    return selected


def _candidate_excerpt(candidate: RecallCandidate) -> str:
    return candidate.summary or candidate.body[:240]


def _archive_excerpt(candidate: ArchiveCandidate) -> str:
    return candidate.summary or candidate.excerpt


def integrate_signals(
    config: AppConfig,
    parsed_query: ParsedQuery,
    candidate_pool: CandidatePool,
    *,
    connection: sqlite3.Connection,
) -> SignalEnrichedCandidatePool:
    candidates = {candidate.id: candidate for candidate in candidate_pool.wiki_candidates}
    note_ids = list(candidates)
    trust_signals = load_note_trust(connection, note_ids)
    freshness_signals = load_note_freshness(connection, note_ids)
    importance_scores = _importance_scores(connection, note_ids)
    passive_feedback = _passive_feedback_signals(connection, note_ids)

    enriched_wiki: list[SignalEnrichedCandidate] = []
    for candidate in candidate_pool.wiki_candidates:
        trust_signal = trust_signals.get(candidate.id)
        freshness_signal = freshness_signals.get(candidate.id)
        passive_feedback_state, passive_feedback_confidence = passive_feedback.get(candidate.id, ("none", 0.0))

        trust_score = trust_signal.usefulness_score if trust_signal is not None else 0.0
        freshness_score = date_freshness_score(candidate)
        freshness_state = "fresh"
        if freshness_signal is not None:
            freshness_state = freshness_signal.freshness_state
            freshness_score = max(0.0, 1.0 - freshness_signal.staleness_score)

        summary_quality, quality_reasons, quality_pass = classify_summary_quality(
            title=candidate.title,
            summary=candidate.summary,
            excerpt=_candidate_excerpt(candidate),
        )
        if freshness_state == "fresh" and not quality_pass:
            quality_reasons = [*quality_reasons, "fresh_but_empty"]

        signals = NoteSignals(
            trust_score=trust_score,
            freshness_score=freshness_score,
            freshness_state=freshness_state,
            importance_score=importance_scores.get(candidate.id, 0.0),
            type_prior_score=type_prior_score(parsed_query.query_type, candidate.type),
            summary_quality=summary_quality,
            quality_pass=quality_pass,
            quality_reasons=quality_reasons,
            freshness_multiplier=freshness_multiplier(
                config,
                state=freshness_state,
                temporality=parsed_query.temporality,
            ),
            passive_feedback_state=passive_feedback_state,
            passive_feedback_confidence=passive_feedback_confidence,
        )

        # Backward compatibility until Stage 4 owns all score consumption.
        candidate.trust_score = signals.trust_score
        candidate.freshness_score = signals.freshness_score
        candidate.freshness_state = signals.freshness_state
        candidate.type_prior_score = signals.type_prior_score

        enriched_wiki.append(SignalEnrichedCandidate(candidate=candidate, signals=signals))

    enriched_archive: list[SignalEnrichedArchiveCandidate] = []
    for archive_candidate in candidate_pool.archive_candidates:
        summary_quality, quality_reasons, quality_pass = classify_summary_quality(
            title=f"Archive chunk {archive_candidate.chunk_index}",
            summary=archive_candidate.summary,
            excerpt=_archive_excerpt(archive_candidate),
        )
        enriched_archive.append(
            SignalEnrichedArchiveCandidate(
                candidate=archive_candidate,
                signals=ArchiveSignals(
                    summary_quality=summary_quality,
                    quality_pass=quality_pass,
                    quality_reasons=quality_reasons,
                ),
            )
        )

    return SignalEnrichedCandidatePool(
        parsed_query=parsed_query,
        wiki_candidates=enriched_wiki,
        archive_candidates=enriched_archive,
        recall_metrics=candidate_pool.recall_metrics,
        archive_policy=candidate_pool.archive_policy,
    )
