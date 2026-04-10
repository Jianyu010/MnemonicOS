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
    review_type: str
    reason: str
    suggested_action: str
    confidence: str | None
    severity: int
    check_count: int
    created_at: str | None
    context_excerpt: str


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
class RelearnTaskSummary:
    task_id: str
    note_id: str
    stage: str
    reason: str
    status: str
    created_at: str | None


@dataclass(slots=True)
class EvalCandidateSyncResult:
    added: int
    open_candidates: list[EvalCandidateSummary]


def _eval_candidates_path(config: AppConfig) -> Path:
    return config.paths.eval_queries.parent / "candidates.jsonl"


def _maintenance_overview_path(config: AppConfig) -> Path:
    return config.paths.vault_root / "wiki" / "overviews" / "maintenance.md"


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
    context_excerpt = parsed.body.strip().splitlines()
    return ReviewItemSummary(
        id=str(metadata.get("id", f"review/{path.stem}")).strip(),
        review_type=str(metadata.get("type", "review")).strip(),
        reason=str(metadata.get("reason", "")).strip(),
        suggested_action=str(metadata.get("suggested_action", "")).strip(),
        confidence=str(metadata.get("confidence", "")).strip() or None,
        severity=int(metadata.get("severity", 1) or 1),
        check_count=int(metadata.get("check_count", 0) or 0),
        created_at=str(metadata.get("created_at", "")).strip() or None,
        context_excerpt=context_excerpt[1].strip() if len(context_excerpt) > 1 else (context_excerpt[0].strip() if context_excerpt else ""),
    )


def load_open_review_items(config: AppConfig) -> list[ReviewItemSummary]:
    review_dir = config.paths.vault_root / "wiki" / "review"
    items: list[ReviewItemSummary] = []
    for path in sorted(review_dir.glob("*.md")):
        item = _load_review_item(path)
        if item is not None:
            items.append(item)
    items.sort(key=lambda item: (-item.severity, -item.check_count, item.created_at or "", item.id))
    return items


def load_open_relearn_tasks(connection) -> list[RelearnTaskSummary]:
    rows = connection.execute(
        """
        SELECT task_id, note_id, stage, reason, status, created_at
        FROM relearn_tasks
        WHERE status = 'open'
        ORDER BY
            CASE stage
                WHEN 'full_relearn_task' THEN 4
                WHEN 'targeted_relearn_task' THEN 3
                WHEN 'crosscheck' THEN 2
                ELSE 1
            END DESC,
            created_at ASC
        """
    ).fetchall()
    return [
        RelearnTaskSummary(
            task_id=str(row["task_id"]),
            note_id=str(row["note_id"]),
            stage=str(row["stage"]),
            reason=str(row["reason"]),
            status=str(row["status"]),
            created_at=str(row["created_at"] or "") or None,
        )
        for row in rows
    ]


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


def _render_limited_list(lines: list[str], *, limit: int, empty_message: str) -> str:
    filtered = [line for line in lines if line.strip()]
    if not filtered:
        return f"- {empty_message}"
    visible = filtered[:limit]
    if len(filtered) > limit:
        visible.append(f"- ... {len(filtered) - limit} more item(s) not shown.")
    return "\n".join(visible)


def _render_auto_loops(
    *,
    review_count: int,
    contradiction_count: int,
    stale_count: int,
    relearn_count: int,
    eval_count: int,
) -> str:
    lines: list[str] = []
    if review_count:
        lines.append(f"- [ ] Resolve {review_count} open review item(s).")
    if contradiction_count:
        lines.append(f"- [ ] Resolve {contradiction_count} contradiction item(s).")
    if stale_count:
        lines.append(f"- [ ] Re-verify {stale_count} stale note(s).")
    if relearn_count:
        lines.append(f"- [ ] Triage {relearn_count} relearn task(s).")
    if eval_count:
        lines.append(f"- [ ] Label {eval_count} retrieval miss candidate(s) in `evals/candidates.jsonl`.")
    return _render_limited_list(lines, limit=6, empty_message="No system-generated action items right now.")


def _render_review_items(items: list[ReviewItemSummary], *, limit: int) -> str:
    lines = [
        f"- `{item.id}` [{item.review_type}]: {item.reason or 'No reason recorded.'} Suggested action: {item.suggested_action or 'review manually'}."
        + (f" severity={item.severity}" if item.severity else "")
        + (f" excerpt: {item.context_excerpt}" if item.context_excerpt else "")
        for item in items
    ]
    return _render_limited_list(lines, limit=limit, empty_message="No open review items.")


def _render_filtered_review_items(items: list[ReviewItemSummary], *, review_type: str, limit: int, empty_message: str) -> str:
    filtered = [item for item in items if item.review_type == review_type]
    return _render_review_items(filtered, limit=limit) if filtered else f"- {empty_message}"


def _render_stale_rows(rows: list[dict[str, str]], *, limit: int) -> str:
    lines = [
        f"- `{row['id']}`: {row['title']} (state: {row.get('freshness_state', 'unknown')}, last verified: {row['last_verified_at']})."
        for row in rows
    ]
    return _render_limited_list(lines, limit=limit, empty_message="No stale notes detected.")


def _render_eval_candidates(items: list[EvalCandidateSummary], *, limit: int) -> str:
    lines = [
        f"- `{item.id}`: \"{item.query}\" ({item.reason}{', ' + item.query_type if item.query_type else ''})."
        for item in items
    ]
    return _render_limited_list(lines, limit=limit, empty_message="No open retrieval miss candidates.")


def _render_relearn_tasks(items: list[RelearnTaskSummary], *, limit: int) -> str:
    lines = [
        f"- `{item.task_id}` [{item.stage}] on `{item.note_id}`: {item.reason}."
        for item in items
    ]
    return _render_limited_list(lines, limit=limit, empty_message="No open relearn tasks.")


def refresh_active_file(
    config: AppConfig,
    *,
    review_items: list[ReviewItemSummary],
    stale_rows: list[dict[str, str]],
    contradiction_rows: list[ReviewItemSummary],
    relearn_tasks: list[RelearnTaskSummary],
    eval_candidates: list[EvalCandidateSummary],
) -> Path:
    active_path = config.paths.vault_root / "system" / "ACTIVE.md"
    active_path.parent.mkdir(parents=True, exist_ok=True)

    existing_sections = _read_existing_sections(active_path)
    current_focus = existing_sections.get("Current Focus", DEFAULT_CURRENT_FOCUS).strip() or DEFAULT_CURRENT_FOCUS
    open_loops = existing_sections.get("Open Loops", DEFAULT_OPEN_LOOPS).strip() or DEFAULT_OPEN_LOOPS
    limit = config.trust.section_limit

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
            _render_auto_loops(
                review_count=len(review_items),
                contradiction_count=len(contradiction_rows),
                stale_count=len(stale_rows),
                relearn_count=len(relearn_tasks),
                eval_count=len(eval_candidates),
            ),
            "",
            "## Needs Human Review",
            _render_review_items(review_items, limit=limit),
            "",
            "## Contradictions",
            _render_review_items(contradiction_rows, limit=limit),
            "",
            "## Stale Facts",
            _render_stale_rows(stale_rows, limit=limit),
            "",
            "## Relearn Queue",
            _render_relearn_tasks(relearn_tasks, limit=limit),
            "",
            "## Eval Backlog",
            _render_eval_candidates(eval_candidates, limit=limit),
            "",
        ]
    )
    active_path.write_text(content, encoding="utf-8")
    return active_path


def write_maintenance_overview(
    config: AppConfig,
    *,
    stats: dict[str, int | float],
    review_items: list[ReviewItemSummary],
    contradiction_rows: list[ReviewItemSummary],
    stale_rows: list[dict[str, str]],
    relearn_tasks: list[RelearnTaskSummary],
    eval_candidates: list[EvalCandidateSummary],
) -> Path:
    path = _maintenance_overview_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    stage_counts: dict[str, int] = {}
    for task in relearn_tasks:
        stage_counts[task.stage] = stage_counts.get(task.stage, 0) + 1

    lines = [
        "---",
        "id: overview/maintenance",
        "type: overview",
        "title: Maintenance Overview",
        f"updated_at: {today}",
        f"created_at: {today}",
        "source_refs: []",
        "confidence: high",
        "---",
        "",
        "# Maintenance Overview",
        "",
        "## Retrieval Snapshot",
        "",
        f"- Open review items: {len(review_items)}",
        f"- Contradiction items: {len(contradiction_rows)}",
        f"- Stale notes: {len(stale_rows)}",
        f"- Open relearn tasks: {len(relearn_tasks)}",
        f"- Eval backlog: {len(eval_candidates)}",
    ]
    if "rerank_training_samples" in stats:
        lines.append(f"- Rerank training samples: {stats['rerank_training_samples']}")
    if "rerank_fallback" in stats:
        lines.append(f"- Rerank fallback active: {stats['rerank_fallback']}")
    lines.extend(
        [
            "",
            "## Relearn Queue by Stage",
            "",
            f"- reverify: {stage_counts.get('reverify', 0)}",
            f"- crosscheck: {stage_counts.get('crosscheck', 0)}",
            f"- targeted_relearn_task: {stage_counts.get('targeted_relearn_task', 0)}",
            f"- full_relearn_task: {stage_counts.get('full_relearn_task', 0)}",
            "",
            "## Hotspots",
            "",
            _render_review_items(review_items, limit=config.trust.section_limit),
            "",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
