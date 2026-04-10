from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re

from .config import AppConfig
from .parser import NoteParseError, split_frontmatter


SECTION_HEADING_PATTERN = re.compile(r"^##\s+(.+?)\s*$")

DEFAULT_CURRENT_FOCUS = "- Add current projects and active workstreams here."
DEFAULT_OPEN_LOOPS = "- [ ] Add manual open loops here."


@dataclass(slots=True)
class ReviewItemSummary:
    id: str
    reason: str
    suggested_action: str
    confidence: str | None
    check_count: int
    created_at: str | None


@dataclass(slots=True)
class EvalCandidateSummary:
    id: str
    query: str
    query_type: str | None
    reason: str
    status: str
    query_id: int
    created_at: str | None


@dataclass(slots=True)
class EvalCandidateSyncResult:
    added: int
    open_candidates: list[EvalCandidateSummary]


def _eval_candidates_path(config: AppConfig) -> Path:
    return config.paths.eval_queries.parent / "candidates.jsonl"


def _split_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current_heading: str | None = None
    current_lines: list[str] = []

    for raw_line in text.splitlines():
        match = SECTION_HEADING_PATTERN.match(raw_line)
        if match:
            if current_heading is not None:
                sections[current_heading] = current_lines[:]
            current_heading = match.group(1).strip()
            current_lines = []
            continue
        if current_heading is not None:
            current_lines.append(raw_line)

    if current_heading is not None:
        sections[current_heading] = current_lines

    return {heading: "\n".join(lines).strip() for heading, lines in sections.items()}


def _read_existing_sections(active_path: Path) -> dict[str, str]:
    if not active_path.exists():
        return {}
    return _split_sections(active_path.read_text())


def _load_review_item(path: Path) -> ReviewItemSummary | None:
    try:
        parsed = split_frontmatter(path.read_text())
    except NoteParseError:
        return None
    metadata = parsed.metadata
    if str(metadata.get("status", "open")).strip().casefold() != "open":
        return None
    return ReviewItemSummary(
        id=str(metadata.get("id", f"review/{path.stem}")).strip(),
        reason=str(metadata.get("reason", "")).strip(),
        suggested_action=str(metadata.get("suggested_action", "")).strip(),
        confidence=str(metadata.get("confidence", "")).strip() or None,
        check_count=int(metadata.get("check_count", 0) or 0),
        created_at=str(metadata.get("created_at", "")).strip() or None,
    )


def load_open_review_items(config: AppConfig) -> list[ReviewItemSummary]:
    review_dir = config.paths.vault_root / "wiki" / "review"
    items: list[ReviewItemSummary] = []
    for path in sorted(review_dir.glob("*.md")):
        item = _load_review_item(path)
        if item is not None:
            items.append(item)
    items.sort(key=lambda item: (-item.check_count, item.created_at or "", item.id))
    return items


def sync_eval_candidates(config: AppConfig, rows: list[dict[str, object]], *, dry_run: bool) -> EvalCandidateSyncResult:
    candidates_path = _eval_candidates_path(config)
    candidates_path.parent.mkdir(parents=True, exist_ok=True)

    existing_entries: list[dict[str, object]] = []
    existing_query_ids: set[int] = set()
    existing_queries: set[str] = set()
    if candidates_path.exists():
        for line in candidates_path.read_text().splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            existing_entries.append(entry)
            query_id = entry.get("query_id")
            if isinstance(query_id, int):
                existing_query_ids.add(query_id)
            if str(entry.get("status", "open")).strip().casefold() == "open":
                existing_queries.add(str(entry.get("query", "")).strip().casefold())

    additions: list[dict[str, object]] = []
    for row in rows:
        query_id = int(row["query_id"])
        query = str(row["query"]).strip()
        normalized_query = query.casefold()
        reasons = list(row["reasons"])
        if query_id in existing_query_ids or normalized_query in existing_queries:
            continue
        additions.append(
            {
                "id": f"candidate/retrieval-{query_id}",
                "status": "open",
                "query": query,
                "query_type": row["query_type"],
                "source": "retrieval_miss",
                "reason": reasons[0],
                "reasons": reasons,
                "query_id": query_id,
                "created_at": row["created_at"],
                "observed_hits": row["observed_hits"],
            }
        )

    if additions and not dry_run:
        with candidates_path.open("a", encoding="utf-8") as handle:
            for entry in additions:
                handle.write(json.dumps(entry, sort_keys=False) + "\n")
        existing_entries.extend(additions)
    elif additions:
        existing_entries.extend(additions)

    open_candidates: list[EvalCandidateSummary] = []
    for entry in existing_entries:
        if str(entry.get("status", "open")).strip().casefold() != "open":
            continue
        query_id = entry.get("query_id", 0)
        try:
            query_id_value = int(query_id)
        except (TypeError, ValueError):
            query_id_value = 0
        open_candidates.append(
            EvalCandidateSummary(
                id=str(entry.get("id", f"candidate/retrieval-{query_id_value}")).strip(),
                query=str(entry.get("query", "")).strip(),
                query_type=str(entry.get("query_type", "")).strip() or None,
                reason=str(entry.get("reason", "")).strip(),
                status=str(entry.get("status", "open")).strip() or "open",
                query_id=query_id_value,
                created_at=str(entry.get("created_at", "")).strip() or None,
            )
        )
    open_candidates.sort(key=lambda item: (item.created_at or "", item.query_id))
    return EvalCandidateSyncResult(added=len(additions), open_candidates=open_candidates)


def _bullet_list(items: list[str], empty_message: str) -> str:
    filtered = [item for item in items if item.strip()]
    if not filtered:
        return f"- {empty_message}"
    return "\n".join(filtered)


def _render_auto_loops(review_count: int, stale_count: int, eval_count: int) -> str:
    lines: list[str] = []
    if review_count:
        lines.append(f"- [ ] Resolve {review_count} open review item(s).")
    if stale_count:
        lines.append(f"- [ ] Re-verify {stale_count} stale decision/procedure note(s).")
    if eval_count:
        lines.append(f"- [ ] Label {eval_count} retrieval miss candidate(s) in `evals/candidates.jsonl`.")
    return _bullet_list(lines, "No system-generated action items right now.")


def _render_review_items(items: list[ReviewItemSummary], limit: int = 5) -> str:
    if not items:
        return "- No open review items."
    lines = [
        f"- `{item.id}`: {item.reason or 'No reason recorded.'} Suggested action: {item.suggested_action or 'review manually'}."
        + (
            f" confidence={item.confidence} checks={item.check_count}"
            if item.confidence
            else f" checks={item.check_count}"
        )
        for item in items[:limit]
    ]
    if len(items) > limit:
        lines.append(f"- ... {len(items) - limit} more open review item(s) not shown.")
    return "\n".join(lines)


def _render_stale_rows(rows: list[dict[str, str]], limit: int = 5) -> str:
    if not rows:
        return "- No stale decision or procedure notes detected."
    lines = [
        f"- `{row['id']}`: {row['title']} (last verified: {row['last_verified_at']})."
        for row in rows[:limit]
    ]
    if len(rows) > limit:
        lines.append(f"- ... {len(rows) - limit} more stale note(s) not shown.")
    return "\n".join(lines)


def _render_eval_candidates(items: list[EvalCandidateSummary], limit: int = 5) -> str:
    if not items:
        return "- No open retrieval miss candidates."
    lines = [
        f"- `{item.id}`: \"{item.query}\" ({item.reason}{', ' + item.query_type if item.query_type else ''})."
        for item in items[:limit]
    ]
    if len(items) > limit:
        lines.append(f"- ... {len(items) - limit} more eval candidate(s) not shown.")
    return "\n".join(lines)


def refresh_active_file(
    config: AppConfig,
    *,
    review_items: list[ReviewItemSummary],
    stale_rows: list[dict[str, str]],
    eval_candidates: list[EvalCandidateSummary],
) -> Path:
    active_path = config.paths.vault_root / "system" / "ACTIVE.md"
    active_path.parent.mkdir(parents=True, exist_ok=True)

    existing_sections = _read_existing_sections(active_path)
    current_focus = existing_sections.get("Current Focus", DEFAULT_CURRENT_FOCUS).strip() or DEFAULT_CURRENT_FOCUS
    open_loops = existing_sections.get("Open Loops", DEFAULT_OPEN_LOOPS).strip() or DEFAULT_OPEN_LOOPS

    today = datetime.now().strftime("%Y-%m-%d")
    content = "\n".join(
        [
            "# ACTIVE",
            f"updated_at: {today}",
            "",
            "## Current Focus",
            current_focus,
            "",
            "## Open Loops",
            open_loops,
            "",
            "## Auto Loops",
            _render_auto_loops(len(review_items), len(stale_rows), len(eval_candidates)),
            "",
            "## Needs Human Review",
            _render_review_items(review_items),
            "",
            "## Stale Facts",
            _render_stale_rows(stale_rows),
            "",
            "## Eval Backlog",
            _render_eval_candidates(eval_candidates),
            "",
        ]
    )
    active_path.write_text(content, encoding="utf-8")
    return active_path
