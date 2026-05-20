from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..bridge import find_existing_candidates
from ..db import connect_db, run_migrations
from ..parser import split_frontmatter
from .decisions import decide
from .gate import run_gate
from .review_ops import ReviewItem, approve_review_item, list_review_items, reject_review_item

if TYPE_CHECKING:
    from ..config import AppConfig

_CONFIDENCE_THRESHOLD = 0.75
_LLM_ANALYSIS_HEADER = "## LLM Analysis"


@dataclass
class AutoResolveRunResult:
    approved: int = 0
    rejected: int = 0
    auto_promoted: int = 0
    human_fallback: int = 0
    errors: list[str] = field(default_factory=list)

    def to_stats(self) -> dict[str, int]:
        return {
            "approved": self.approved,
            "rejected": self.rejected,
            "auto_promoted": self.auto_promoted,
            "human_fallback": self.human_fallback,
            "errors": len(self.errors),
        }


def run_auto_resolve(
    config: "AppConfig",
    *,
    llm_fn: Callable[[str], str] | None = None,
    dry_run: bool = False,
) -> AutoResolveRunResult:
    """Drain wiki/review/ items using LLM arbitration.

    For each review-routed candidate:
    1. Re-run gate — if now failing, reject immediately.
    2. Re-run decide() — if wiki changed and decision is no longer review,
       auto-promote.
    3. Dispatch to reason-specific LLM resolver.
    4. confidence >= 0.75 → approve/reject; otherwise append LLM Analysis
       section to review file for human.
    """
    result = AutoResolveRunResult()
    if llm_fn is None:
        try:
            llm_fn = _make_llm_fn(config)
        except Exception as exc:
            result.errors.append(f"llm_fn_build_error:{exc}")
            return result

    conn = connect_db(config.paths.db_path)
    run_migrations(conn, config.paths.workspace_root / "migrations")
    try:
        items = list_review_items(config)
        for item in items:
            try:
                _process_one(item, conn, config, llm_fn, dry_run, result)
            except Exception as exc:
                result.errors.append(f"{item.filename}:{exc}")
    finally:
        conn.close()
    return result


def _process_one(
    item: ReviewItem,
    conn: sqlite3.Connection,
    config: "AppConfig",
    llm_fn: Callable[[str], str],
    dry_run: bool,
    result: AutoResolveRunResult,
) -> None:
    loaded = _load_candidate_payload(item, conn)
    if loaded is None:
        result.errors.append(f"{item.filename}:candidate_payload_not_found")
        return
    candidate_id, payload = loaded

    # Step 1: re-run gate
    gate = run_gate(payload, config=config)
    if not gate.ok:
        if not dry_run:
            _safe_reject(config, item.filename)
        result.rejected += 1
        return

    # Step 2: re-run decide — wiki may have changed since review routing
    row = conn.execute(
        """
        SELECT eq.duplicate_state
        FROM extraction_candidates ec
        JOIN extraction_queue eq ON ec.queue_id = eq.id
        WHERE ec.id = ?
        """,
        (candidate_id,),
    ).fetchone()
    duplicate_state = str(row["duplicate_state"] or "") if row else None

    decision = decide(payload, duplicate_state=duplicate_state, conn=conn, config=config)

    # Step 2b: decide() returned rejected directly (e.g. low_confidence) → auto-reject
    if decision.decision == "rejected":
        if not dry_run:
            _safe_reject(config, item.filename, reason=decision.reason)
        result.rejected += 1
        return

    if decision.decision != "review":
        if not dry_run:
            try:
                approve_review_item(config, item.filename)
            except FileExistsError:
                _safe_reject(config, item.filename)
            except FileNotFoundError:
                pass
        result.auto_promoted += 1
        return

    # Step 3: LLM resolution — skip in dry_run to avoid blocking on real LLM calls
    if dry_run:
        # Under Phase 6, human_fallback is gone; anything left here auto-rejects.
        result.rejected += 1
        return

    # Short-circuit low_confidence legacy items before LLM
    if str(decision.reason or "").split(":")[0] == "low_confidence":
        _safe_reject(config, item.filename, reason="low_confidence_extraction")
        result.rejected += 1
        return

    llm_result = _resolve_with_llm(item, payload, decision, conn, config, llm_fn)

    if float(llm_result.get("confidence", 0.0)) >= _CONFIDENCE_THRESHOLD and llm_result.get("action") != "human":
        if llm_result["action"] == "approve":
            try:
                approve_review_item(
                    config,
                    item.filename,
                    target_note_id=llm_result.get("target_note_id") or None,
                )
            except FileExistsError:
                _safe_reject(config, item.filename, reason="file_exists_conflict")
            except FileNotFoundError:
                pass
            result.approved += 1
        else:
            _safe_reject(config, item.filename, reason=f"llm_rejected:{decision.reason}")
            result.rejected += 1
    else:
        # LLM uncertain — auto-reject instead of human fallback
        _safe_reject(config, item.filename, reason=f"{decision.reason}_llm_uncertain")
        result.rejected += 1


def _safe_reject(config: "AppConfig", filename: str, *, reason: str | None = None) -> None:
    try:
        reject_review_item(config, filename, rejection_reason=reason)
    except FileNotFoundError:
        pass


def _load_candidate_payload(
    item: ReviewItem,
    conn: sqlite3.Connection,
) -> tuple[int, dict[str, Any]] | None:
    try:
        text = item.path.read_text(encoding="utf-8")
        parsed = split_frontmatter(text)
    except OSError:
        return None
    source_refs: list[Any] = list(parsed.metadata.get("source_refs") or [])
    candidate_id: int | None = None
    for ref in source_refs:
        if isinstance(ref, str) and ref.startswith("candidate:"):
            try:
                candidate_id = int(ref.split(":", 1)[1])
            except ValueError:
                pass
            break
    if candidate_id is None:
        return None
    row = conn.execute(
        "SELECT id, payload_json FROM extraction_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(str(row["payload_json"]))
    except (json.JSONDecodeError, TypeError):
        return None
    return candidate_id, payload


def _resolve_with_llm(
    item: ReviewItem,
    payload: dict[str, Any],
    decision: Any,
    conn: sqlite3.Connection,
    config: "AppConfig",
    llm_fn: Callable[[str], str],
) -> dict[str, Any]:
    # decision.reason may have suffix like "strong_match:semantic" — strip it
    reason = str(decision.reason or "").split(":")[0]
    if reason == "low_confidence":
        return _resolve_low_confidence(payload, conn, config, llm_fn)
    if reason == "type_collision":
        return _resolve_type_collision(payload, decision, conn, llm_fn)
    if reason == "wiki_conflict":
        return _resolve_wiki_conflict(payload, decision, conn, config, llm_fn)
    if reason == "contested_lifecycle":
        return _resolve_contested_lifecycle(payload, decision, conn, llm_fn)
    return {"action": "human", "confidence": 0.0, "reasoning": f"unknown_reason:{reason}"}


def _load_note_body(note_id: str, conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT body_path FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        return "(body unavailable)"
    body_path_str = str(row["body_path"] or "")
    if not body_path_str:
        return "(body unavailable)"
    try:
        return Path(body_path_str).read_text(encoding="utf-8")
    except OSError:
        return "(body unavailable)"


def _parse_llm_response(raw: str) -> dict[str, Any]:
    try:
        stripped = raw.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            end = -1 if lines and lines[-1].strip() == "```" else len(lines)
            stripped = "\n".join(lines[1:end])
        result = json.loads(stripped)
        if not isinstance(result, dict):
            raise ValueError("not a dict")
        return result
    except Exception:
        return {"action": "human", "confidence": 0.0, "reasoning": "parse_error"}


def _resolve_low_confidence(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    config: "AppConfig",
    llm_fn: Callable[[str], str],
) -> dict[str, Any]:
    note_type = str(payload.get("type") or "")
    title = str(payload.get("title") or "")
    body = str(payload.get("body") or "")
    summary = str(payload.get("summary") or "")

    matches = find_existing_candidates(
        conn, config, note_type=note_type, title=title, summary=summary or body, limit=1
    )
    wiki_context = "(none)"
    if matches:
        top_body = _load_note_body(matches[0].note_id, conn)
        wiki_context = f"{matches[0].note_id} (score {matches[0].score:.2f}):\n{top_body[:600]}"

    prompt = (
        "You are a knowledge-quality reviewer for MnemonicOS.\n"
        "A candidate was flagged low-confidence. Decide whether to approve or reject it.\n\n"
        f"CANDIDATE:\ntype: {note_type}\ntitle: {title}\nbody: {body[:800]}\n\n"
        f"MOST SIMILAR WIKI NOTE:\n{wiki_context}\n\n"
        'Respond ONLY with raw JSON (no markdown fences):\n'
        '{"action": "approve|reject|human", "reasoning": "<one sentence>", "confidence": 0.0-1.0}\n\n'
        "Rules:\n"
        "- approve: candidate contains verifiable, non-trivial knowledge not fully covered by wiki\n"
        "- reject: candidate is vague, duplicative, or unsupported\n"
        "- human: you cannot tell from the text alone"
    )
    try:
        return _parse_llm_response(llm_fn(prompt))
    except Exception as exc:
        return {"action": "human", "confidence": 0.0, "reasoning": f"llm_error:{exc}"}


def _resolve_type_collision(
    payload: dict[str, Any],
    decision: Any,
    conn: sqlite3.Connection,
    llm_fn: Callable[[str], str],
) -> dict[str, Any]:
    candidate_type = str(payload.get("type") or "")
    title = str(payload.get("title") or "")
    body = str(payload.get("body") or "")
    conflict_ids: list[str] = list(decision.conflict_note_ids or [])

    conflict_parts: list[str] = []
    for cid in conflict_ids[:2]:
        row = conn.execute("SELECT type FROM notes WHERE id = ?", (cid,)).fetchone()
        ctype = str(row["type"]) if row else "unknown"
        cbody = _load_note_body(cid, conn)
        conflict_parts.append(f"{cid} (type: {ctype}):\n{cbody[:500]}")
    conflict_text = "\n\n".join(conflict_parts) or "(not found)"

    prompt = (
        "A promotion candidate has the same slug as an existing wiki note of a different type.\n\n"
        f"CANDIDATE:\ntype: {candidate_type}\ntitle: {title}\nbody: {body[:800]}\n\n"
        f"CONFLICTING NOTE(S):\n{conflict_text}\n\n"
        'Respond ONLY with raw JSON (no markdown fences):\n'
        '{"action": "approve|reject|human", "reasoning": "<one sentence>", "confidence": 0.0-1.0}\n\n'
        "Rules:\n"
        "- approve: candidate represents a genuinely distinct concept despite the slug overlap\n"
        "- reject: candidate is a near-duplicate or lower-quality version of the existing note\n"
        "- human: the distinction is ambiguous"
    )
    try:
        return _parse_llm_response(llm_fn(prompt))
    except Exception as exc:
        return {"action": "human", "confidence": 0.0, "reasoning": f"llm_error:{exc}"}


def _resolve_wiki_conflict(
    payload: dict[str, Any],
    decision: Any,
    conn: sqlite3.Connection,
    config: "AppConfig",
    llm_fn: Callable[[str], str],
) -> dict[str, Any]:
    note_type = str(payload.get("type") or "")
    title = str(payload.get("title") or "")
    body = str(payload.get("body") or "")
    summary = str(payload.get("summary") or "")
    conflict_ids: list[str] = list(decision.conflict_note_ids or [])

    matches = find_existing_candidates(
        conn, config, note_type=note_type, title=title, summary=summary or body
    )
    score_map = {m.note_id: m.score for m in matches}

    conflict_parts: list[str] = []
    for i, cid in enumerate(conflict_ids[:3], 1):
        score = score_map.get(cid, 0.0)
        cbody = _load_note_body(cid, conn)
        conflict_parts.append(f"[{i}] {cid} (score {score:.2f}):\n{cbody[:500]}")
    conflict_text = "\n\n".join(conflict_parts) or "(not found)"

    prompt = (
        "Multiple wiki notes match this candidate with no clear winner (top score < 0.90).\n\n"
        f"CANDIDATE:\ntype: {note_type}\ntitle: {title}\nbody: {body[:800]}\n\n"
        f"CONFLICT NOTES:\n{conflict_text}\n\n"
        'Respond ONLY with raw JSON (no markdown fences):\n'
        '{"action": "approve|reject|human", "target_note_id": "<note_id or null>", '
        '"reasoning": "<one sentence>", "confidence": 0.0-1.0}\n\n'
        "Rules:\n"
        "- approve: candidate adds new knowledge; set target_note_id=best match to update, "
        "or null for a new note\n"
        "- reject: candidate is redundant given existing notes\n"
        "- human: you cannot determine the best match"
    )
    try:
        return _parse_llm_response(llm_fn(prompt))
    except Exception as exc:
        return {"action": "human", "confidence": 0.0, "reasoning": f"llm_error:{exc}"}


def _resolve_contested_lifecycle(
    payload: dict[str, Any],
    decision: Any,
    conn: sqlite3.Connection,
    llm_fn: Callable[[str], str],
) -> dict[str, Any]:
    note_type = str(payload.get("type") or "")
    title = str(payload.get("title") or "")
    body = str(payload.get("body") or "")
    top_note_id = decision.target_note_id or (
        decision.conflict_note_ids[0] if decision.conflict_note_ids else None
    )
    contested_body = _load_note_body(top_note_id, conn) if top_note_id else "(unknown)"

    prompt = (
        'A wiki note has lifecycle_state = "contested", meaning its content is under dispute.\n'
        "A new candidate was extracted that matches this note.\n\n"
        f"CANDIDATE:\ntype: {note_type}\ntitle: {title}\nbody: {body[:800]}\n\n"
        f"CONTESTED NOTE ({top_note_id}):\n{contested_body[:800]}\n\n"
        'Respond ONLY with raw JSON (no markdown fences):\n'
        '{"action": "approve|reject|human", "reasoning": "<one sentence>", "confidence": 0.0-1.0}\n\n'
        "Rules:\n"
        "- approve: candidate corroborates or clearly supersedes the contested note\n"
        "- reject: candidate does not add new evidence relative to the existing contested note\n"
        "- human: the relationship is genuinely ambiguous"
    )
    try:
        return _parse_llm_response(llm_fn(prompt))
    except Exception as exc:
        return {"action": "human", "confidence": 0.0, "reasoning": f"llm_error:{exc}"}


def _append_llm_analysis(item: ReviewItem, llm_result: dict[str, Any]) -> None:
    try:
        text = item.path.read_text(encoding="utf-8")
    except OSError:
        return
    if _LLM_ANALYSIS_HEADER in text:
        return  # idempotent
    action = str(llm_result.get("action") or "human")
    confidence = float(llm_result.get("confidence") or 0.0)
    reasoning = str(llm_result.get("reasoning") or "")
    block = (
        f"\n\n{_LLM_ANALYSIS_HEADER}\n\n"
        f"**Action suggested:** {action}\n"
        f"**Confidence:** {confidence:.2f}\n"
        f"**Reasoning:** {reasoning}\n"
    )
    tmp = item.path.with_suffix(".tmp")
    try:
        tmp.write_text(text.rstrip() + block, encoding="utf-8")
        os.rename(tmp, item.path)
    except OSError:
        tmp.unlink(missing_ok=True)


def _make_llm_fn(config: "AppConfig") -> Callable[[str], str]:
    from ..extraction.providers import (
        _auth_headers,
        _json_request,
        _ollama_generate,
        build_llm_provider,
    )

    provider = build_llm_provider(config)

    if provider.provider_name == "fixture":
        def _fixture(prompt: str) -> str:  # noqa: ARG001
            return '{"action": "human", "reasoning": "fixture", "confidence": 0.0}'
        return _fixture

    if provider.provider_name == "ollama":
        base_url = (config.extraction.llm_base_url or config.extraction.ollama_base_url or "").rstrip("/")
        model_name = provider.model_name
        timeout_seconds = config.extraction.llm_timeout_seconds

        def _ollama(prompt: str) -> str:
            return _ollama_generate(base_url, model=model_name, prompt=prompt, timeout_seconds=timeout_seconds)
        return _ollama

    if provider.provider_name in {"openai", "openai_compatible"}:
        base_url = (config.extraction.llm_base_url or "").rstrip("/")
        model_name = provider.model_name
        api_key_env = config.extraction.llm_api_key_env or ""
        timeout_seconds = config.extraction.llm_timeout_seconds

        def _openai(prompt: str) -> str:
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": "You emit raw JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
            }
            data = _json_request(
                f"{base_url}/chat/completions",
                payload,
                headers=_auth_headers(api_key_env),
                timeout_seconds=timeout_seconds,
            )
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices:
                raise RuntimeError("missing choices in response")
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if not isinstance(message, dict):
                raise RuntimeError("missing message in response")
            return str(message.get("content") or "").strip()
        return _openai

    if provider.provider_name == "cli_command":
        import shlex
        import subprocess

        command = config.extraction.llm_command or ""
        timeout_seconds = config.extraction.llm_timeout_seconds or 180

        def _cli(prompt: str) -> str:
            completed = subprocess.run(
                [*shlex.split(command), prompt],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    completed.stderr.strip() or f"cli_command exited {completed.returncode}"
                )
            return completed.stdout.strip()
        return _cli

    raise ValueError(f"unsupported provider for auto-resolver: {provider.provider_name}")
