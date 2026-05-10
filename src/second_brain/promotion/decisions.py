from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from ..bridge import find_existing_candidates
from ..parser import LIFECYCLE_STATES
from ..review import slugify
from .models import PromotionDecision

if TYPE_CHECKING:
    from ..config import AppConfig

_NEAR_DUPLICATE_STATES = {"canonical_duplicate", "lsh_duplicate"}

# Body diff threshold: if existing note body shares > this fraction of tokens
# with candidate body, treat as confirm rather than update.
_CONFIRM_OVERLAP_THRESHOLD = 0.70


def decide(
    candidate: dict[str, Any],
    *,
    duplicate_state: str | None,
    conn: sqlite3.Connection,
    config: "AppConfig",
) -> PromotionDecision:
    """Assign a deterministic promotion decision to a gate-passing candidate.

    Rules are evaluated in order; first match wins.
    """
    candidate_type = str(candidate.get("type") or "").strip()
    title = str(candidate.get("title") or "").strip()
    confidence = str(candidate.get("confidence") or "").strip()
    candidate_body = str(candidate.get("body") or "").strip()
    candidate_summary = str(candidate.get("summary") or "").strip()

    # Rule 1 — Low-confidence guard (fires before any wiki lookup)
    promo_cfg = getattr(config, "promotion", None)
    min_confidence = getattr(promo_cfg, "min_confidence_for_direct_write", "medium") if promo_cfg is not None else "medium"
    if confidence == "low" or (min_confidence == "high" and confidence != "high"):
        return PromotionDecision(
            decision="review",
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

    # Rule 3 — Title slug collision across types
    candidate_slug = slugify(title)
    all_type_rows = conn.execute(
        "SELECT id, type FROM notes WHERE type != ? AND status != 'archived'",
        (candidate_type,),
    ).fetchall()
    conflict_ids: list[str] = []
    for row in all_type_rows:
        row_id = str(row["id"])
        if row_id.endswith(f"/{candidate_slug}") or slugify(row_id.split("/", 1)[-1]) == candidate_slug:
            conflict_ids.append(row_id)
    if conflict_ids:
        return PromotionDecision(
            decision="review",
            target_note_id=None,
            reason="type_collision",
            conflict_note_ids=conflict_ids,
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

    # Rule 7 — Contested lifecycle state (check before conflict/confirm/update)
    lc_row = conn.execute(
        "SELECT lifecycle_state FROM notes WHERE id = ?",
        (top_note_id,),
    ).fetchone()
    if lc_row is not None:
        lc = str(lc_row["lifecycle_state"] or "").strip()
        if lc == "contested":
            return PromotionDecision(
                decision="review",
                target_note_id=top_note_id,
                reason="contested_lifecycle",
                conflict_note_ids=[top_note_id],
            )

    # Rule 2 — Conflict check
    auto_review = getattr(promo_cfg, "auto_review_on_conflict", True) if promo_cfg is not None else True
    if auto_review and top.score < 0.90 and len(matches) > 1:
        # Multiple plausible matches without a clear winner = conflict
        candidate_ids = [m.note_id for m in matches]
        return PromotionDecision(
            decision="review",
            target_note_id=top_note_id,
            reason="wiki_conflict",
            conflict_note_ids=candidate_ids,
        )

    # Rule 0 — Near-duplicate reinforcement prior: bias toward confirm
    # canonical/lsh duplicate means same source bytes — skip body novelty check.
    near_dup = duplicate_state in _NEAR_DUPLICATE_STATES
    if near_dup and top.score >= 0.90:
        return PromotionDecision(
            decision="confirm",
            target_note_id=top_note_id,
            reason=f"near_duplicate_corroboration:{duplicate_state}",
        )

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
