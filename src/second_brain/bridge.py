from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

import yaml

from .config import AppConfig
from .embedding_providers import build_embedding_provider, embedding_model_key
from .models import CandidateMatch, SummaryEnvelope, UpsertDecision
from .review import slugify
from .runtime import assess_retrieval, assess_retrieval_payload
from .semantics import cosine_similarity, vector_from_json


SECTION_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
LIST_ITEM_PATTERN = re.compile(r"^\s*[-*]\s+(.+?)\s*$")
FIELD_PATTERN = re.compile(r"^([a-z_]+):\s*(.+?)\s*$")
MEMORY_BLOCK_PATTERN = re.compile(r"```(?:memory|mnemonic)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
CHAIN_EXTRACTION_STATES = {"explicit_chain", "inferred_chain", "no_chain"}


def parse_summary(text: str) -> SummaryEnvelope:
    metadata: dict[str, str] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        if not line:
            index += 1
            break
        if line.startswith("#"):
            break
        match = FIELD_PATTERN.match(line)
        if not match:
            break
        key, value = match.groups()
        metadata[key] = value.strip()
        index += 1

    sections: dict[str, str] = {}
    current_name: str | None = None
    buffer: list[str] = []
    for line in lines[index:]:
        match = SECTION_PATTERN.match(line)
        if match:
            if current_name is not None:
                sections[current_name] = "\n".join(buffer).strip()
            current_name = match.group(1).strip()
            buffer = []
            continue
        buffer.append(line)
    if current_name is not None:
        sections[current_name] = "\n".join(buffer).strip()

    memory_items: list[dict[str, Any]] = []
    for block in MEMORY_BLOCK_PATTERN.findall(text):
        parsed = yaml.safe_load(block) or []
        if isinstance(parsed, dict) and "items" in parsed:
            parsed = parsed["items"]
        if isinstance(parsed, dict):
            parsed = [parsed]
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    memory_items.append(dict(item))
    return SummaryEnvelope(metadata=metadata, sections=sections, memory_items=memory_items)


def infer_summary_mode(summary: SummaryEnvelope) -> str:
    task_kind = summary.metadata.get("task_kind", "").strip().casefold()
    if task_kind in {"knowledge", "research"}:
        return "knowledge"
    failures = summary.sections.get("Failures", "")
    if failures and failures.strip() and failures.strip().lower() not in {"none", "n/a"}:
        return "failure"
    return "success"


def _extract_section_items(section_text: str) -> list[str]:
    results: list[str] = []
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("```") or line == "items:":
            continue
        match = LIST_ITEM_PATTERN.match(line)
        if match:
            value = match.group(1).strip()
            if value.startswith("```"):
                continue
            if re.match(r"^(type|title|body|confidence|tags):", value):
                continue
            results.append(value)
        else:
            if re.match(r"^[a-z_]+:\s*", line) and not line.lower().startswith(("http:", "https:")):
                continue
            results.append(line)
    return [item for item in results if item]


def candidate_queries_from_summary(summary: SummaryEnvelope) -> list[str]:
    queries: list[str] = []
    task = summary.sections.get("Task")
    goal = summary.sections.get("Goal")
    if task:
        queries.append(task)
    if goal:
        queries.append(goal)
    for item in _extract_section_items(summary.sections.get("Anchors", "")):
        queries.append(item)
    for item in _extract_section_items(summary.sections.get("Suggested Memory Items", "")):
        queries.append(item)
    for item in summary.memory_items:
        title = str(item.get("title", "")).strip()
        if title:
            queries.append(title)
    if summary.metadata.get("update_target_ids"):
        queries.extend(
            [part.strip() for part in summary.metadata["update_target_ids"].split(",") if part.strip()]
        )

    ordered: list[str] = []
    seen: set[str] = set()
    for item in queries:
        normalized = " ".join(item.split())
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    return ordered[:8]


def infer_chain_context(summary: SummaryEnvelope, *, session_id: str, task_id: str | None = None) -> dict[str, str]:
    metadata = summary.metadata
    explicit_chain_id = metadata.get("chain_id", "").strip()
    explicit_question = metadata.get("chain_question", "").strip()
    explicit_summary = metadata.get("chain_summary", "").strip()
    explicit_problem_class = metadata.get("problem_class", "").strip()

    if explicit_chain_id:
        return {
            "chain_extraction": "explicit_chain",
            "chain_reason": "explicit_metadata",
            "chain_id": explicit_chain_id,
            "chain_confidence": metadata.get("chain_confidence", "").strip().casefold() or "high",
            "chain_question": explicit_question or _first_non_empty_section(summary, "Task", "Goal"),
            "chain_summary": explicit_summary or _default_chain_summary(summary),
            "problem_class": explicit_problem_class or _default_problem_class(summary),
        }

    task_kind = metadata.get("task_kind", "").strip().casefold()
    if task_kind not in {"debugging", "architecture", "research"}:
        return {"chain_extraction": "no_chain", "chain_reason": "unsupported_task_kind"}

    question = _first_non_empty_section(summary, "Task", "Goal")
    if not question:
        return {"chain_extraction": "no_chain", "chain_reason": "missing_question"}

    attempts = _extract_section_items(summary.sections.get("Attempts", "")) or _extract_section_items(summary.sections.get("Retry Chain", ""))
    failures = _extract_section_items(summary.sections.get("Failures", ""))
    outcomes = _extract_section_items(summary.sections.get("Outcome", "")) or _extract_section_items(summary.sections.get("Reusable Takeaways", ""))
    memory_items = summary.memory_items

    signal_count = 0
    if attempts:
        signal_count += 1
    if failures:
        signal_count += 1
    if outcomes:
        signal_count += 1
    if len(memory_items) >= 2:
        signal_count += 1

    if signal_count < 2:
        return {"chain_extraction": "no_chain", "chain_reason": "insufficient_signal"}

    slug_source = task_id or session_id or question
    chain_id = f"trace/{slugify(slug_source)}"
    return {
        "chain_extraction": "inferred_chain",
        "chain_reason": "problem_solving_signal",
        "chain_id": chain_id,
        "chain_confidence": "medium",
        "chain_question": question,
        "chain_summary": _default_chain_summary(summary),
        "problem_class": _default_problem_class(summary),
    }


def _first_non_empty_section(summary: SummaryEnvelope, *names: str) -> str:
    for name in names:
        value = summary.sections.get(name, "").strip()
        if value:
            return value
    return ""


def _default_chain_summary(summary: SummaryEnvelope) -> str:
    for name in ("Reusable Takeaways", "Outcome", "Failures"):
        items = _extract_section_items(summary.sections.get(name, ""))
        if items:
            return items[0]
    for item in summary.memory_items:
        text = str(item.get("summary") or item.get("body") or item.get("title") or "").strip()
        if text:
            return " ".join(text.split())[:240]
    return ""


def _default_problem_class(summary: SummaryEnvelope) -> str:
    anchors = _extract_section_items(summary.sections.get("Anchors", ""))
    if anchors:
        return slugify(anchors[0])
    question = _first_non_empty_section(summary, "Task", "Goal")
    return slugify(question) if question else ""


def _token_set(text: str) -> set[str]:
    return {token for token in re.findall(r"[\w/-]+", text.casefold()) if len(token) > 2}


def _semantic_score(config: AppConfig, connection: sqlite3.Connection, note_id: str, query_text: str) -> float:
    if not config.embeddings.enabled:
        return 0.0
    row = connection.execute(
        "SELECT vector_json FROM note_vectors WHERE note_id = ? AND model = ?",
        (note_id, embedding_model_key(config)),
    ).fetchone()
    if row is None:
        return 0.0
    query_vector = build_embedding_provider(config).embed_texts([query_text])[0]
    return cosine_similarity(query_vector, vector_from_json(str(row["vector_json"])))


def find_existing_candidates(
    connection: sqlite3.Connection,
    config: AppConfig,
    *,
    note_type: str,
    title: str,
    summary: str = "",
    limit: int | None = None,
) -> list[CandidateMatch]:
    slug = slugify(title)
    query_text = summary or title
    rows = connection.execute(
        """
        SELECT n.id, n.title, n.type, n.summary, GROUP_CONCAT(a.alias, '||') AS aliases
        FROM notes n
        LEFT JOIN aliases a ON a.note_id = n.id
        WHERE (? = '' OR n.type = ?)
        GROUP BY n.id, n.title, n.type, n.summary
        """,
        (note_type, note_type),
    ).fetchall()

    bm = config.bridge_matching
    _limit = limit if limit is not None else bm.candidate_limit
    title_tokens = _token_set(title)
    matches: list[CandidateMatch] = []
    for row in rows:
        note_id = str(row["id"])
        note_title = str(row["title"])
        aliases = [alias for alias in str(row["aliases"] or "").split("||") if alias]
        title_slug = slugify(note_title)
        score = 0.0
        reason = "weak"
        if title.casefold() == note_title.casefold():
            score = bm.exact_title_score
            reason = "exact_title"
        elif note_id.casefold().endswith(f"/{slug}"):
            score = bm.id_slug_score
            reason = "id_slug"
        elif slug and slug == title_slug:
            score = bm.slug_score
            reason = "slug_match"
        else:
            alias_hit = next((alias for alias in aliases if alias.casefold() == title.casefold()), None)
            if alias_hit:
                score = bm.alias_exact_score
                reason = "alias_exact"
            else:
                note_tokens = _token_set(note_title)
                union = title_tokens | note_tokens
                if union:
                    token_overlap = len(title_tokens & note_tokens) / len(union)
                    if token_overlap > score:
                        score = token_overlap
                        reason = "token_overlap"
                semantic = _semantic_score(config, connection, note_id, query_text)
                if semantic > score:
                    score = semantic
                    reason = "semantic"
        if score >= bm.min_candidate_score:
            matches.append(
                CandidateMatch(
                    note_id=note_id,
                    title=note_title,
                    note_type=str(row["type"]),
                    score=score,
                    reason=reason,
                )
            )

    matches.sort(key=lambda item: (-item.score, item.title.casefold()))
    return matches[:_limit]


def decide_upsert(
    connection: sqlite3.Connection,
    config: AppConfig,
    *,
    summary: SummaryEnvelope,
    note_type: str,
    title: str,
    summary_text: str,
    existing_candidates: list[CandidateMatch] | None = None,
) -> UpsertDecision:
    explicit_intent = summary.metadata.get("writeback_intent", "").strip().casefold()
    retrieval_state = summary.metadata.get("mnemos_retrieval_state", "").strip().casefold()
    update_targets = [
        part.strip()
        for part in summary.metadata.get("update_target_ids", "").split(",")
        if part.strip()
    ]
    if explicit_intent == "skip":
        return UpsertDecision("skip_duplicate", update_targets, update_targets, "explicit writeback_intent=skip")
    if update_targets:
        return UpsertDecision("update_existing", update_targets, update_targets, "explicit update_target_ids")

    candidates = existing_candidates if existing_candidates is not None else find_existing_candidates(
        connection,
        config,
        note_type=note_type,
        title=title,
        summary=summary_text,
    )
    candidate_ids = [candidate.note_id for candidate in candidates]
    if not candidates:
        return UpsertDecision("new_note", [], [], "no near-match candidate")

    top = candidates[0]
    if explicit_intent == "confirm":
        return UpsertDecision("confirm_existing", [top.note_id], candidate_ids, "explicit writeback_intent=confirm")
    if explicit_intent in {"update", "procedure_refinement", "incident_only"}:
        return UpsertDecision("update_existing", [top.note_id], candidate_ids, f"explicit writeback_intent={explicit_intent}")

    if top.score >= config.bridge_matching.slug_score:
        if retrieval_state in {"sufficient_current", "sufficient_historical"}:
            return UpsertDecision("confirm_existing", [top.note_id], candidate_ids, f"strong match ({top.reason}) with sufficient retrieval state")
        return UpsertDecision("update_existing", [top.note_id], candidate_ids, f"strong match ({top.reason})")

    if top.score >= 0.90:
        if retrieval_state in {"sufficient_current", "sufficient_historical"} and note_type in {"concept", "source"}:
            return UpsertDecision("confirm_existing", [top.note_id], candidate_ids, "high-confidence concept/source match")
        return UpsertDecision("needs_review", [top.note_id], candidate_ids, "high semantic/alias match but not exact")

    if retrieval_state in {"insufficient_stale", "insufficient_contested", "partial"} and top.score >= 0.70:
        return UpsertDecision("update_existing", [top.note_id], candidate_ids, "existing note likely needs refresh")

    return UpsertDecision("new_note", [], candidate_ids, "candidate exists but similarity is too weak")


def decision_to_json(decision: UpsertDecision) -> str:
    return json.dumps(
        {
            "decision": decision.decision,
            "targets": decision.targets,
            "candidates": decision.candidates,
            "reason": decision.reason,
        },
        indent=2,
    )
