from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..parser import NoteParseError, split_frontmatter
from ..review import slugify
from .decisions import PromotionDecision
from .models import PromotionRunResult

if TYPE_CHECKING:
    from ..config import AppConfig

# Directory under wiki/ for review-routed items
_REVIEW_SUBDIR = "review"

# Log entry format: matches brain/wiki/log.md convention
#   ISO-8601 UTC | bridge_id | decision | note_id | reason


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _log_entry(bridge_id: str, decision: str, note_id: str, reason: str) -> str:
    return f"{_now_iso()} | {bridge_id} | {decision} | {note_id} | {reason}\n"


def _target_path(
    vault_root: Path,
    note_type: str,
    slug: str,
    *,
    decision: str,
    timestamp: str,
) -> Path:
    from ..ingest import TYPE_TO_DIRECTORY

    if decision == "review":
        return vault_root / "wiki" / _REVIEW_SUBDIR / f"{slug}-{timestamp.replace(':', '-').replace('T', '-')[:19]}.md"
    if note_type == "procedure":
        return vault_root / "wiki" / "procedures" / "drafts" / f"{slug}.md"
    subdir = TYPE_TO_DIRECTORY.get(note_type, note_type + "s")
    return vault_root / "wiki" / subdir / f"{slug}.md"


def write_promotion(
    rendered_markdown: str,
    *,
    metadata: dict[str, Any],
    decision: PromotionDecision,
    candidate_id: int,
    archive_id: str,
    bridge_id: str,
    conn: sqlite3.Connection,
    vault_root: Path,
    retrieval_attribution_json: str | None = None,
) -> None:
    """Atomically write a promoted note to wiki and record in the ledger.

    Protocol:
    1. Idempotency check — skip if candidate_id already has a ledger row.
    2. Compute target path.
    3. Write .tmp file.
    4. Re-parse .tmp (smoke check).
    5. os.rename() to final path (atomic on POSIX).
    6. Append to wiki/log.md.
    7. Insert promotion_decisions row.
    8. Update extraction_candidates.promotion_state.

    On any exception after step 3: delete .tmp, mark candidate rejected, raise.
    """
    # Step 1 — Idempotency
    existing = conn.execute(
        "SELECT id FROM promotion_decisions WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if existing is not None:
        return  # Already processed; no-op.

    note_type = str(metadata.get("type") or "").strip()
    title = str(metadata.get("title") or "").strip()
    slug = slugify(title)
    timestamp = _now_iso()
    note_id = str(metadata.get("id") or f"{note_type}/{slug}")

    final_path = _target_path(
        vault_root,
        note_type,
        slug,
        decision=decision.decision,
        timestamp=timestamp,
    )
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = final_path.with_suffix(".tmp")

    # Step 3 — Write .tmp
    try:
        tmp_path.write_text(rendered_markdown, encoding="utf-8")
    except OSError as exc:
        _mark_rejected(conn, candidate_id, [f"tmp_write_error:{exc}"], bridge_id)
        raise

    # Step 4 — Smoke-check parse
    try:
        split_frontmatter(rendered_markdown)
    except NoteParseError as exc:
        tmp_path.unlink(missing_ok=True)
        _mark_rejected(conn, candidate_id, [f"parse_error:{exc}"], bridge_id)
        raise

    # Step 5 — Atomic rename
    try:
        os.rename(tmp_path, final_path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        _mark_rejected(conn, candidate_id, [f"rename_error:{exc}"], bridge_id)
        raise

    # Step 6 — Append to wiki/log.md
    _append_log(vault_root, bridge_id, decision.decision, note_id, decision.reason)

    # Step 7 — Insert ledger row
    promotion_state = "review_routed" if decision.decision == "review" else "promoted"
    conn.execute(
        """
        INSERT INTO promotion_decisions
            (candidate_id, decision, target_note_id, archive_id,
             check_errors_json, promotion_state, bridge_id, retrieval_attribution_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate_id,
            decision.decision,
            decision.target_note_id or note_id,
            archive_id or "",
            "[]",
            promotion_state,
            bridge_id,
            retrieval_attribution_json,
        ),
    )

    # Step 8 — Update extraction_candidates.promotion_state
    conn.execute(
        "UPDATE extraction_candidates SET promotion_state = ? WHERE id = ?",
        (promotion_state, candidate_id),
    )


def write_rejected(
    conn: sqlite3.Connection,
    candidate_id: int,
    gate_errors: list[str],
    bridge_id: str,
) -> None:
    """Record a gate-rejected candidate in the ledger without touching wiki."""
    existing = conn.execute(
        "SELECT id FROM promotion_decisions WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if existing is not None:
        return
    _mark_rejected(conn, candidate_id, gate_errors, bridge_id)


def _mark_rejected(
    conn: sqlite3.Connection,
    candidate_id: int,
    errors: list[str],
    bridge_id: str,
) -> None:
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO promotion_decisions
                (candidate_id, decision, target_note_id, archive_id,
                 check_errors_json, promotion_state, bridge_id)
            VALUES (?, 'rejected', NULL, NULL, ?, 'rejected', ?)
            """,
            (candidate_id, json.dumps(errors), bridge_id),
        )
        conn.execute(
            "UPDATE extraction_candidates SET promotion_state = 'rejected' WHERE id = ?",
            (candidate_id,),
        )
    except Exception:
        pass  # Never let ledger errors mask the original exception


def _append_log(vault_root: Path, bridge_id: str, decision: str, note_id: str, reason: str) -> None:
    log_path = vault_root / "wiki" / "log.md"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = _log_entry(bridge_id, decision, note_id, reason)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(entry)
    except OSError:
        pass  # Log append failure is non-fatal
