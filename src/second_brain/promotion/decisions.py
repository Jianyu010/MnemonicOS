from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..bridge import find_existing_candidates
from ..review import slugify
from .models import PromotionDecision

if TYPE_CHECKING:
    from ..config import AppConfig

_NEAR_DUPLICATE_STATES = {"canonical_duplicate", "lsh_duplicate"}

# Body diff threshold: if existing note body shares > this fraction of tokens
# with candidate body, treat as confirm rather than update.
_CONFIRM_OVERLAP_THRESHOLD = 0.70

_CONFIDENCE_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
_CONFIDENCE_LEVELS = ["low", "medium", "high"]


def decide(
    candidate: dict[str, Any],
    *,
    duplicate_state: str | None,
    conn: sqlite3.Connection,
    config: "AppConfig",
) -> PromotionDecision:
    """Assign a deterministic promotion decision to a gate-passing candidate.

    Rules are evaluated in order; first match wins.

    Phase 6 changes:
    - low_confidence → 'rejected' (not 'review'); no wiki lookup needed.
    - type_collision removed: note IDs include type prefix; no actual conflict.
    - contested_lifecycle removed: handled by time-weighted resolution.
    - wiki_conflict replaced by _time_weighted_conflict().
    """
    candidate_type = str(candidate.get("type") or "").strip()
    title = str(candidate.get("title") or "").strip()
    confidence = str(candidate.get("confidence") or "").strip()
    candidate_body = str(candidate.get("body") or "").strip()
    candidate_summary = str(candidate.get("summary") or "").strip()

    # Rule 1 — Low-confidence guard: auto-reject, no wiki lookup needed.
    promo_cfg = getattr(config, "promotion", None)
    min_confidence = getattr(promo_cfg, "min_confidence_for_direct_write", "medium") if promo_cfg is not None else "medium"
    if confidence == "low" or (min_confidence == "high" and confidence != "high"):
        return PromotionDecision(
            decision="rejected",
            target_note_id=None,
            reason="low_confidence",
        )

    # Look up existing wiki notes matching type + title
    matches = find_existing_candidates(
        conn,
        config,
        note_type=candidate_type,
        title=title,
        summary=candidate_summary or candidate_body,
    )

    # Rule 4 — No existing note
    if not matches:
        return PromotionDecision(
            decision="new",
            target_note_id=None,
            reason="no_near_match",
        )

    top = matches[0]
    top_note_id = top.note_id

    # Rule 0 — Near-duplicate reinforcement prior: bias toward confirm.
    # canonical/lsh duplicate means same source bytes — skip body novelty check.
    near_dup = duplicate_state in _NEAR_DUPLICATE_STATES
    if near_dup and top.score >= 0.90:
        return PromotionDecision(
            decision="confirm",
            target_note_id=top_note_id,
            reason=f"near_duplicate_corroboration:{duplicate_state}",
        )

    # Rule 2 — Multiple plausible matches without clear winner: time-weighted resolution.
    # Replaces wiki_conflict → review.
    auto_review = getattr(promo_cfg, "auto_review_on_conflict", True) if promo_cfg is not None else True
    if auto_review and top.score < 0.90 and len(matches) > 1:
        return _time_weighted_conflict(candidate, top, conn, config, confidence)

    # Rules 5 & 6 — Strong match → confirm or update based on score + body diff
    if top.score >= 0.98:
        if _body_is_novel(candidate_body, top_note_id, conn):
            return PromotionDecision(
                decision="update",
                target_note_id=top_note_id,
                reason=f"strong_match_with_new_content:{top.reason}",
            )
        return PromotionDecision(
            decision="confirm",
            target_note_id=top_note_id,
            reason=f"strong_match:{top.reason}",
        )

    if top.score >= 0.55:
        return PromotionDecision(
            decision="update",
            target_note_id=top_note_id,
            reason=f"near_match:{top.reason}",
        )

    # Fallback — similarity too weak, treat as new
    return PromotionDecision(
        decision="new",
        target_note_id=None,
        reason="similarity_too_weak",
    )


def _time_weighted_conflict(
    candidate: dict[str, Any],
    top_match: Any,
    conn: sqlite3.Connection,
    config: "AppConfig",
    new_confidence: str,
) -> PromotionDecision:
    """Deterministic time-weighted conflict resolution.

    Replaces wiki_conflict → human review. No LLM required.

    Resolution table (reason encodes the action):
      high/high, gap>stale   → update  conflict_supersedes_stale
      high/high, glitch<gap  → update  conflict_supersedes_contested
      high/high, gap≤glitch  → confirm conflict_corroborates
      high/medium, any       → update  conflict_supersedes_contested
      high/low, any          → update  conflict_supersedes_archive
      medium/high, any       → confirm conflict_corroborates
      medium/medium, gap>stale→ update  conflict_supersedes_stale
      medium/medium, gap≤stale→ confirm conflict_corroborates
      medium/low, any        → update  conflict_supersedes_archive
      low/any, any           → new     conflict_low_confidence_new
    """
    new_rank = _CONFIDENCE_RANK.get(new_confidence, 1)
    top_note_id = top_match.note_id

    row = conn.execute(
        "SELECT confidence, updated_at FROM notes WHERE id = ?",
        (top_note_id,),
    ).fetchone()

    if row is None:
        # Can't find note — promote as new
        return PromotionDecision(
            decision="new",
            target_note_id=None,
            reason="conflict_note_missing",
        )

    old_confidence = str(row["confidence"] or "medium").strip()
    old_rank = _CONFIDENCE_RANK.get(old_confidence, 1)
    time_gap_days = _calc_time_gap_days(str(row["updated_at"] or ""))

    promo_cfg = getattr(config, "promotion", None)
    stale_threshold = int(getattr(promo_cfg, "conflict_stale_threshold_days", 30) if promo_cfg is not None else 30)
    glitch_threshold = int(getattr(promo_cfg, "conflict_glitch_threshold_days", 1) if promo_cfg is not None else 1)

    suffix = top_match.reason  # e.g. "semantic" or "exact_title"

    # Low confidence new: promote as standalone, mark contested downstream
    if new_rank == 0:
        return PromotionDecision(
            decision="new",
            target_note_id=None,
            reason=f"conflict_low_confidence_new:{suffix}",
        )

    # High new
    if new_rank == 2:
        if old_rank == 2:  # high/high
            if time_gap_days > stale_threshold:
                return PromotionDecision(
                    decision="update",
                    target_note_id=top_note_id,
                    reason=f"conflict_supersedes_stale:{suffix}",
                )
            if time_gap_days > glitch_threshold:
                return PromotionDecision(
                    decision="update",
                    target_note_id=top_note_id,
                    reason=f"conflict_supersedes_contested:{suffix}",
                )
            return PromotionDecision(
                decision="confirm",
                target_note_id=top_note_id,
                reason=f"conflict_corroborates:{suffix}",
            )
        if old_rank == 1:  # high/medium
            return PromotionDecision(
                decision="update",
                target_note_id=top_note_id,
                reason=f"conflict_supersedes_contested:{suffix}",
            )
        # high/low
        return PromotionDecision(
            decision="update",
            target_note_id=top_note_id,
            reason=f"conflict_supersedes_archive:{suffix}",
        )

    # Medium new (new_rank == 1)
    if old_rank == 2:  # medium/high
        return PromotionDecision(
            decision="confirm",
            target_note_id=top_note_id,
            reason=f"conflict_corroborates:{suffix}",
        )
    if old_rank == 1:  # medium/medium
        if time_gap_days > stale_threshold:
            return PromotionDecision(
                decision="update",
                target_note_id=top_note_id,
                reason=f"conflict_supersedes_stale:{suffix}",
            )
        return PromotionDecision(
            decision="confirm",
            target_note_id=top_note_id,
            reason=f"conflict_corroborates:{suffix}",
        )
    # medium/low
    return PromotionDecision(
        decision="update",
        target_note_id=top_note_id,
        reason=f"conflict_supersedes_archive:{suffix}",
    )


def _calc_time_gap_days(updated_at: str) -> float:
    """Return fractional days since updated_at ISO string. Inf if unparseable."""
    if not updated_at:
        return float("inf")
    try:
        clean = updated_at.replace("Z", "+00:00")
        if "T" not in clean and " " in clean:
            clean = clean.replace(" ", "T")
        if "+" not in clean and "-" not in clean[10:]:
            clean += "+00:00"
        old_dt = datetime.fromisoformat(clean)
        diff = datetime.now(timezone.utc) - old_dt
        return max(0.0, diff.total_seconds() / 86400)
    except (ValueError, TypeError):
        return float("inf")


def _body_is_novel(candidate_body: str, note_id: str, conn: sqlite3.Connection) -> bool:
    """Return True if the candidate body contains enough new content vs. existing note."""
    if not candidate_body.strip():
        return False
    row = conn.execute(
        "SELECT body_path FROM notes WHERE id = ?",
        (note_id,),
    ).fetchone()
    if row is None:
        return True
    body_path_str = str(row["body_path"] or "")
    if not body_path_str:
        return True
    from pathlib import Path

    body_path = Path(body_path_str)
    if not body_path.exists():
        return True
    existing_body = body_path.read_text()
    existing_tokens = _token_set(existing_body)
    candidate_tokens = _token_set(candidate_body)
    if not existing_tokens:
        return True
    union = existing_tokens | candidate_tokens
    overlap = len(existing_tokens & candidate_tokens) / len(union)
    return overlap < _CONFIRM_OVERLAP_THRESHOLD


def _token_set(text: str) -> set[str]:
    import re
    return {t for t in re.findall(r"[\w]+", text.casefold()) if len(t) > 3}
