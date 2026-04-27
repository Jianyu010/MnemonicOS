from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from typing import Any

from .config import default_config_path, load_config
from .daemon_status import daemon_status_summary, daemon_unreachable_summary, read_pid, socket_send
from .db import connect_db, run_migrations
from .ingest import (
    ingest_artifact,
    ingest_session,
    ingest_session_promote,
    project_episode_for_promotion,
    reprocess_artifact,
    reprocess_failed_artifacts,
)
from .jobs import run_job
from .models import ResolveTaskContextRequest
from .retrieval import result_to_json, retrieve
from .runtime import (
    decide_writeback_runtime,
    decide_writeback_to_json,
    record_outcome,
    record_outcome_to_json,
    resolve_task_context,
    resolve_task_context_to_json,
)
from .session_capture import append_event, begin_episode, end_episode
from .sync import sync_vault
from .orchestrator import (
    OrchestratorFailureRequest,
    OrchestratorOutcomeRequest,
    OrchestratorTaskRequest,
    begin_task,
    context_result_to_json,
    finish_task,
    mark_failure,
    writeback_result_to_json,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MnemonicOS local memory system CLI")
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Path to the TOML config file. "
            "Defaults to config/memory.toml when present, otherwise config/memory.example.toml."
        ),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Apply SQLite migrations.")

    sync_parser = subparsers.add_parser("sync", help="Synchronize vault notes into SQLite.")
    sync_parser.add_argument("--mode", choices=("incremental", "full"), default="incremental")
    sync_parser.add_argument("paths", nargs="*", help="Optional subset of files to sync.")

    ingest_parser = subparsers.add_parser("ingest-session", help="Ingest a raw session and promote explicit memory items.")
    ingest_parser.add_argument("--bridge-id")
    ingest_parser.add_argument("--session-id")
    ingest_parser.add_argument("--agent", help="Deprecated alias for --bridge-id.")
    ingest_parser.add_argument("--slug", help="Deprecated alias for --session-id.")
    ingest_parser.add_argument("--file", help="Path to a markdown file with session content.")
    ingest_parser.add_argument("--content", help="Inline session content.")
    ingest_parser.add_argument("--tag", action="append", default=[])
    ingest_parser.add_argument("--source-ref", action="append", default=[])
    ingest_parser.add_argument("--promote-now", action="store_true", help="Bypass the deferred queue and promote immediately.")

    artifact_parser = subparsers.add_parser("ingest-artifact", help="Ingest a true raw artifact without persisting extracted content.")
    artifact_parser.add_argument("--file", required=True, help="Path to the artifact file.")
    artifact_parser.add_argument("--tag", action="append", default=[])
    artifact_parser.add_argument("--attachment", action="append", default=[], help="Source-provided companion image/file to archive with the artifact.")
    artifact_parser.add_argument("--json", action="store_true", help="Emit JSON output.")

    reprocess_parser = subparsers.add_parser("reprocess-artifact", help="Rerun artifact extraction against preserved true raw.")
    reprocess_group = reprocess_parser.add_mutually_exclusive_group(required=True)
    reprocess_group.add_argument("--archive-id", help="Artifact archive id to reprocess.")
    reprocess_group.add_argument("--failed", action="store_true", help="Reprocess all failed artifact ingests.")
    reprocess_parser.add_argument("--json", action="store_true", help="Emit JSON output.")

    capture_parser = subparsers.add_parser("capture", help="Comprehensive raw bridge capture operations.")
    capture_subparsers = capture_parser.add_subparsers(dest="capture_command", required=True)

    begin_capture = capture_subparsers.add_parser("begin-episode", help="Create or reopen a raw conversation and begin an episode.")
    begin_capture.add_argument("--bridge-id", required=True)
    begin_capture.add_argument("--conversation-id", default=None)
    begin_capture.add_argument("--episode-id", default=None)
    begin_capture.add_argument("--parent-conversation-id", default=None)
    begin_capture.add_argument("--related-conversation-id", action="append", default=[])
    begin_capture.add_argument("--task-id", default=None)
    begin_capture.add_argument("--cwd", default=None)
    begin_capture.add_argument("--repo", default=None)
    begin_capture.add_argument("--branch", default=None)
    begin_capture.add_argument("--model", default=None)
    begin_capture.add_argument("--agent-runtime", default=None)
    begin_capture.add_argument("--platform-thread-id", default=None)
    begin_capture.add_argument("--metadata-json", default=None)
    begin_capture.add_argument("--json", action="store_true", help="Emit JSON output.")

    append_capture = capture_subparsers.add_parser("append-event", help="Append one raw event to an episode.")
    append_capture.add_argument("--bridge-id", required=True)
    append_capture.add_argument("--conversation-id", required=True)
    append_capture.add_argument("--episode-id", required=True)
    append_capture.add_argument("--type", required=True, dest="event_type")
    append_capture.add_argument("--payload-json", default=None)
    append_capture.add_argument("--payload-file", default=None)
    append_capture.add_argument("--content", default=None, help="Shortcut payload for user/assistant message content.")
    append_capture.add_argument("--task-id", default=None)
    append_capture.add_argument("--branch-id", default="main")
    append_capture.add_argument("--timestamp", default=None)
    append_capture.add_argument("--json", action="store_true", help="Emit JSON output.")

    end_capture = capture_subparsers.add_parser("end-episode", help="Close an active raw capture episode.")
    end_capture.add_argument("--bridge-id", required=True)
    end_capture.add_argument("--conversation-id", required=True)
    end_capture.add_argument("--episode-id", required=True)
    end_capture.add_argument("--status", default="success", choices=("success", "failure", "partial", "abandoned"))
    end_capture.add_argument("--metadata-json", default=None)
    end_capture.add_argument("--json", action="store_true", help="Emit JSON output.")

    project_capture = capture_subparsers.add_parser("project-episode", help="Project a captured episode in memory and run promotion.")
    project_capture.add_argument("--conversation-id", required=True)
    project_capture.add_argument("--episode-id", required=True)
    project_capture.add_argument("--json", action="store_true", help="Emit JSON output.")

    retrieve_parser = subparsers.add_parser("retrieve", help="Run hybrid retrieval across exact, BM25, semantic, graph, and archive channels.")
    retrieve_parser.add_argument("query", help="Query text.")
    retrieve_parser.add_argument("--top-k", type=int, default=None)
    retrieve_parser.add_argument("--query-type-hint", default=None)
    retrieve_parser.add_argument("--json", action="store_true", help="Emit JSON output.")

    resolve_parser = subparsers.add_parser("resolve-task-context", help="Resolve a unified Phase 1 runtime context packet.")
    resolve_parser.add_argument("task_text", help="Task text.")
    resolve_parser.add_argument("--bridge-id", required=True)
    resolve_parser.add_argument("--session-id", required=True)
    resolve_parser.add_argument("--task-id", required=True)
    resolve_parser.add_argument("--task-kind", default="general")
    resolve_parser.add_argument("--temporality", choices=("current", "historical", "mixed", "unknown"), default=None)
    resolve_parser.add_argument("--token-budget", type=int, default=None)
    resolve_parser.add_argument("--workspace", default=None)
    resolve_parser.add_argument("--project", default=None)
    resolve_parser.add_argument("--repo", default=None)
    resolve_parser.add_argument("--tag", action="append", default=[])
    resolve_parser.add_argument("--top-k", type=int, default=None)
    resolve_parser.add_argument("--query-type-hint", default=None)
    resolve_parser.add_argument("--no-archive-fallback", action="store_true")
    resolve_parser.add_argument("--json", action="store_true", help="Emit JSON output.")

    outcome_parser = subparsers.add_parser("record-outcome", help="Record a unified runtime task outcome.")
    outcome_parser.add_argument("--bridge-id", required=True)
    outcome_parser.add_argument("--session-id", required=True)
    outcome_parser.add_argument("--task-id", required=True)
    outcome_parser.add_argument("--outcome-type", required=True)
    outcome_parser.add_argument("--status", required=True)
    outcome_parser.add_argument("--summary", required=True)
    outcome_parser.add_argument("--task-kind", default="general")
    outcome_parser.add_argument("--query-id", type=int, default=None)
    outcome_parser.add_argument("--retrieval-state", default=None)
    outcome_parser.add_argument("--recommendation", default=None)
    outcome_parser.add_argument("--fresh-research-needed", action="store_true")
    outcome_parser.add_argument("--fresh-source", action="append", default=[])
    outcome_parser.add_argument("--useful-id", action="append", default=[])
    outcome_parser.add_argument("--misleading-id", action="append", default=[])
    outcome_parser.add_argument("--json", action="store_true", help="Emit JSON output.")

    writeback_parser = subparsers.add_parser("decide-writeback", help="Compute a unified runtime writeback decision.")
    writeback_parser.add_argument("--bridge-id", required=True)
    writeback_parser.add_argument("--session-id", required=True)
    writeback_parser.add_argument("--task-id", required=True)
    writeback_parser.add_argument("--note-type", required=True)
    writeback_parser.add_argument("--title", required=True)
    writeback_parser.add_argument("--summary-text", required=True)
    writeback_parser.add_argument("--task-kind", default="general")
    writeback_parser.add_argument("--retrieval-state", default=None)
    writeback_parser.add_argument("--writeback-intent", default=None)
    writeback_parser.add_argument("--existing-note-candidate", action="append", default=[])
    writeback_parser.add_argument("--update-target-id", action="append", default=[])
    writeback_parser.add_argument("--fresh-research-needed", action="store_true")
    writeback_parser.add_argument("--fresh-source", action="append", default=[])
    writeback_parser.add_argument("--json", action="store_true", help="Emit JSON output.")

    jobs_parser = subparsers.add_parser("run-job", help="Run a maintenance job.")
    jobs_parser.add_argument(
        "job_name",
        choices=(
            "daily_consolidation",
            "weekly_hygiene",
            "sync_vault_incremental",
            "process_promotion_queue",
            "backfill_promotion_queue",
            "embed_backfill",
            "status_report",
        ),
    )
    jobs_parser.add_argument("--dry-run", action="store_true")

    daemon_parser = subparsers.add_parser("daemon", help="Manage the local MnemonicOS daemon.")
    daemon_subparsers = daemon_parser.add_subparsers(dest="daemon_command", required=True)
    daemon_subparsers.add_parser("start", help="Start the daemon.")
    daemon_subparsers.add_parser("stop", help="Stop the daemon.")
    daemon_status = daemon_subparsers.add_parser("status", help="Show daemon status.")
    daemon_status.add_argument("--json", action="store_true", help="Emit raw JSON output.")
    daemon_run_job = daemon_subparsers.add_parser("run-job", help="Ask the daemon to run a job immediately.")
    daemon_run_job.add_argument("job_name")

    # orchestrator sub-commands
    orch_parser = subparsers.add_parser("orchestrator", help="Shared orchestrator facade (begin-task, mark-failure, finish-task).")
    orch_subs = orch_parser.add_subparsers(dest="orchestrator_command", required=True)

    bt = orch_subs.add_parser("begin-task", help="Resolve context before an LLM call.")
    bt.add_argument("task_text", help="Task text to resolve context for.")
    bt.add_argument("--bridge-id", required=True, help="Agent identifier (e.g. 'claude', 'codex').")
    bt.add_argument("--session-id", default=None, help="Session identifier (auto-generated when absent).")
    bt.add_argument("--task-id", default=None, help="Task identifier (auto-generated when absent).")
    bt.add_argument("--task-kind", default="", help="Task kind: coding, debugging, architecture, research, general.")
    bt.add_argument("--temporality", choices=("current", "historical", "mixed", "unknown"), default=None)
    bt.add_argument("--token-budget", type=int, default=None)
    bt.add_argument("--workspace", default=None)
    bt.add_argument("--project", default=None)
    bt.add_argument("--repo", default=None)
    bt.add_argument("--tag", action="append", default=[])
    bt.add_argument("--json", action="store_true", help="Emit JSON output.")

    mf = orch_subs.add_parser("mark-failure", help="Record failure and re-resolve context with the same task_id.")
    mf.add_argument("failure_text", help="Description of the failure.")
    mf.add_argument("--bridge-id", required=True)
    mf.add_argument("--session-id", required=True)
    mf.add_argument("--task-id", required=True)
    mf.add_argument("--task-kind", default="general")
    mf.add_argument("--query-id", type=int, default=None)
    mf.add_argument("--retrieval-state", default=None)
    mf.add_argument("--json", action="store_true", help="Emit JSON output.")

    ft = orch_subs.add_parser("finish-task", help="Record final outcome and compute writeback decision.")
    ft.add_argument("--bridge-id", required=True)
    ft.add_argument("--session-id", required=True)
    ft.add_argument("--task-id", required=True)
    ft.add_argument("--status", required=True, choices=("success", "failure", "partial"))
    ft.add_argument("--outcome-type", required=True, help="e.g. coding, debugging, research, general.")
    ft.add_argument("--summary", default=None, help="Inline summary text.")
    ft.add_argument("--summary-file", default=None, help="Path to a markdown summary file.")
    ft.add_argument("--query-id", type=int, default=None)
    ft.add_argument("--retrieval-state", default=None)
    ft.add_argument("--recommendation", default=None)
    ft.add_argument("--useful-id", action="append", default=[])
    ft.add_argument("--misleading-id", action="append", default=[])
    ft.add_argument("--fresh-source", action="append", default=[])
    ft.add_argument("--note-type", default=None)
    ft.add_argument("--title", default=None)
    ft.add_argument("--writeback-intent", default=None)
    ft.add_argument("--json", action="store_true", help="Emit JSON output.")

    # HTTP server command
    srv_parser = subparsers.add_parser("server", help="Start the MnemonicOS HTTP server.")
    srv_parser.add_argument("--host", default="127.0.0.1")
    srv_parser.add_argument("--port", type=int, default=8000)

    return parser


def _init_db(config_path: str) -> int:
    config = load_config(config_path)
    connection = connect_db(config.paths.db_path)
    versions = run_migrations(connection, config.paths.workspace_root / "migrations")
    connection.close()
    if versions:
        print(f"applied migrations: {', '.join(versions)}")
    else:
        print("database already up to date")
    return 0


def _sync(config_path: str, mode: str, paths: list[str]) -> int:
    config = load_config(config_path)
    result = sync_vault(config, mode=mode, selected_paths=paths)
    print(f"scanned_paths={result.scanned_paths}")
    print(f"synced_notes={result.synced_notes}")
    print(f"deleted_notes={result.deleted_notes}")
    print(f"parse_errors={result.parse_errors}")
    return 0 if result.parse_errors == 0 else 1


def _retrieve(config_path: str, query: str, top_k: int | None, query_type_hint: str | None, emit_json: bool) -> int:
    config = load_config(config_path)
    result = retrieve(config, query, top_k=top_k, query_type_hint=query_type_hint)
    if emit_json:
        print(result_to_json(result))
        return 0

    print(f"query_id={result.query_id}")
    if result.pinned_paths:
        print("pinned_paths:")
        for path in result.pinned_paths:
            print(f"  - {path}")
    if not result.hits:
        print("no hits")
        return 0

    for index, hit in enumerate(result.hits, start=1):
        channels = ",".join(hit.channels)
        print(f"{index}. {hit.id} [{hit.type}] channels={channels} score={hit.final_score:.3f}")
        print(f"   title: {hit.title}")
        print(f"   path: {hit.body_path}")
        if hit.summary:
            print(f"   summary: {hit.summary}")
        elif hit.excerpt:
            print(f"   excerpt: {hit.excerpt}")
    return 0


def _resolve_task_context(
    config_path: str,
    task_text: str,
    bridge_id: str,
    session_id: str,
    task_id: str,
    task_kind: str,
    temporality: str | None,
    token_budget: int | None,
    workspace: str | None,
    project: str | None,
    repo: str | None,
    tags: list[str],
    top_k: int | None,
    query_type_hint: str | None,
    emit_json: bool,
    no_archive_fallback: bool,
) -> int:
    config = load_config(config_path)
    request = ResolveTaskContextRequest(
        bridge_id=bridge_id,
        session_id=session_id,
        task_id=task_id,
        task_text=task_text,
        task_kind=task_kind,
        temporality=temporality,
        token_budget=token_budget,
        workspace=workspace,
        project=project,
        repo=repo,
        tags=tags,
        include_archive_fallback=not no_archive_fallback,
        top_k=top_k,
        query_type_hint=query_type_hint,
    )
    result = resolve_task_context(config, request)
    if emit_json:
        print(resolve_task_context_to_json(result))
        return 0

    print(f"task_id={result.task_id}")
    print(f"session_id={result.session_id}")
    print(f"retrieval_state={result.retrieval_state}")
    print(f"recommendation={result.recommendation}")
    print(f"verification_action={result.verification_action}")
    print(f"primary_memories={len(result.memory_packet.primary_memories)}")
    for memory in result.memory_packet.primary_memories:
        print(f"  memory={memory.id} [{memory.type}] score={memory.final_score:.3f}")
    for warning in result.memory_packet.warnings:
        print(f"warning={warning}")
    return 0


def _record_outcome(
    config_path: str,
    bridge_id: str,
    session_id: str,
    task_id: str,
    outcome_type: str,
    status: str,
    summary: str,
    task_kind: str,
    query_id: int | None,
    retrieval_state: str | None,
    recommendation: str | None,
    fresh_research_needed: bool,
    fresh_sources_checked: list[str],
    useful_ids: list[str],
    misleading_ids: list[str],
    emit_json: bool,
) -> int:
    from .models import RecordOutcomeRequest

    config = load_config(config_path)
    result = record_outcome(
        config,
        RecordOutcomeRequest(
            bridge_id=bridge_id,
            session_id=session_id,
            task_id=task_id,
            outcome_type=outcome_type,
            status=status,
            summary=summary,
            task_kind=task_kind,
            query_id=query_id,
            retrieval_state=retrieval_state,
            recommendation=recommendation,
            fresh_research_needed=fresh_research_needed,
            fresh_sources_checked=fresh_sources_checked,
            useful_ids=useful_ids,
            misleading_ids=misleading_ids,
        ),
    )
    if emit_json:
        print(record_outcome_to_json(result))
        return 0
    print(f"task_id={result.task_id}")
    print(f"session_id={result.session_id}")
    print(f"status={result.status}")
    for note_id in result.candidate_note_ids:
        print(f"candidate_note_id={note_id}")
    return 0


def _decide_writeback(
    config_path: str,
    bridge_id: str,
    session_id: str,
    task_id: str,
    note_type: str,
    title: str,
    summary_text: str,
    task_kind: str,
    retrieval_state: str | None,
    writeback_intent: str | None,
    existing_note_candidates: list[str],
    update_target_ids: list[str],
    fresh_research_needed: bool,
    fresh_sources_checked: list[str],
    emit_json: bool,
) -> int:
    from .models import DecideWritebackRequest

    config = load_config(config_path)
    result = decide_writeback_runtime(
        config,
        DecideWritebackRequest(
            bridge_id=bridge_id,
            session_id=session_id,
            task_id=task_id,
            note_type=note_type,
            title=title,
            summary_text=summary_text,
            task_kind=task_kind,
            retrieval_state=retrieval_state,
            writeback_intent=writeback_intent,
            existing_note_candidates=existing_note_candidates,
            update_target_ids=update_target_ids,
            fresh_research_needed=fresh_research_needed,
            fresh_sources_checked=fresh_sources_checked,
        ),
    )
    if emit_json:
        print(decide_writeback_to_json(result))
        return 0
    print(f"task_id={result.task_id}")
    print(f"session_id={result.session_id}")
    print(f"decision={result.decision}")
    for target in result.targets:
        print(f"target={target}")
    for candidate in result.candidates:
        print(f"candidate={candidate}")
    print(f"reason={result.reason}")
    return 0


def _ingest_session(
    config_path: str,
    bridge_id: str | None,
    session_id: str | None,
    agent: str | None,
    slug: str | None,
    file_path: str | None,
    content: str | None,
    tags: list[str],
    source_refs: list[str],
    promote_now: bool,
) -> int:
    if bool(file_path) == bool(content):
        raise SystemExit("provide exactly one of --file or --content")
    resolved_bridge_id = bridge_id or agent
    resolved_session_id = session_id or slug
    if not resolved_bridge_id or not resolved_session_id:
        raise SystemExit("provide bridge identity via --bridge-id/--session-id or legacy --agent/--slug")
    payload = content
    if file_path is not None:
        payload = open(file_path, encoding="utf-8").read()
    config = load_config(config_path)
    ingest_fn = ingest_session_promote if promote_now else ingest_session
    result = ingest_fn(
        config,
        bridge_id=resolved_bridge_id,
        session_id=resolved_session_id,
        content=payload or "",
        tags=tags,
        source_refs=source_refs,
    )
    print(f"archive_id={result.archive_id}")
    print(f"raw_path={result.raw_path}")
    print(f"chunk_count={result.chunk_count}")
    print(f"promoted_notes={len(result.promoted_notes)}")
    for note_id in result.promoted_notes:
        print(f"  note={note_id}")
    print(f"review_items={len(result.review_items)}")
    for review_id in result.review_items:
        print(f"  review={review_id}")
    return 0


def _ingest_artifact(config_path: str, file_path: str, tags: list[str], attachments: list[str], emit_json: bool) -> int:
    config = load_config(config_path)
    result = ingest_artifact(config, path=file_path, tags=tags, attachments=attachments)
    payload = {
        "archive_id": result.archive_id,
        "raw_path": result.raw_path,
        "manifest_path": result.manifest_path,
        "ingest_status": result.ingest_status,
        "source_format": result.source_format,
        "figure_count": result.figure_count,
        "warnings": result.warnings,
        "failure_reason": result.failure_reason,
        "needs_visual_pass": result.needs_visual_pass,
        "needs_manual_projection": result.needs_manual_projection,
        "image_only_pdf": result.image_only_pdf,
    }
    if emit_json:
        print(json.dumps(payload, sort_keys=True))
        return 0
    for key, value in payload.items():
        if isinstance(value, list):
            if not value:
                print(f"{key}=[]")
            for item in value:
                print(f"{key}={item}")
        else:
            print(f"{key}={value}")
    return 0


def _artifact_payload(result: Any) -> dict[str, object]:
    return {
        "archive_id": result.archive_id,
        "raw_path": result.raw_path,
        "manifest_path": result.manifest_path,
        "ingest_status": result.ingest_status,
        "source_format": result.source_format,
        "figure_count": result.figure_count,
        "warnings": result.warnings,
        "failure_reason": result.failure_reason,
        "needs_visual_pass": result.needs_visual_pass,
        "needs_manual_projection": result.needs_manual_projection,
        "image_only_pdf": result.image_only_pdf,
    }


def _reprocess_artifact(config_path: str, archive_id: str | None, failed: bool, emit_json: bool) -> int:
    config = load_config(config_path)
    results = reprocess_failed_artifacts(config) if failed else [reprocess_artifact(config, archive_id=archive_id or "")]
    payloads = [_artifact_payload(result) for result in results]
    if emit_json:
        print(json.dumps(payloads if failed else payloads[0], sort_keys=True))
        return 0
    if failed:
        print(f"reprocessed={len(payloads)}")
    for payload in payloads:
        print(f"archive_id={payload['archive_id']}")
        print(f"ingest_status={payload['ingest_status']}")
        print(f"raw_path={payload['raw_path']}")
        if payload.get("failure_reason"):
            print(f"failure_reason={payload['failure_reason']}")
        for warning in payload.get("warnings", []):
            print(f"warning={warning}")
    return 0


def _json_arg(value: str | None, *, field_name: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{field_name} must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"{field_name} must be a JSON object")
    return parsed


def _capture_payload(payload_json: str | None, payload_file: str | None, content: str | None) -> dict[str, Any]:
    provided = sum(1 for item in (payload_json, payload_file, content) if item is not None)
    if provided > 1:
        raise SystemExit("provide only one of --payload-json, --payload-file, or --content")
    if payload_json is not None:
        return _json_arg(payload_json, field_name="--payload-json")
    if payload_file is not None:
        return _json_arg(open(payload_file, encoding="utf-8").read(), field_name="--payload-file")
    if content is not None:
        return {"content": content}
    return {}


def _print_or_json(payload: dict[str, Any], emit_json: bool) -> int:
    if emit_json:
        print(json.dumps(payload, sort_keys=True))
        return 0
    for key, value in payload.items():
        if isinstance(value, list):
            if not value:
                print(f"{key}=[]")
            for item in value:
                print(f"{key}={item}")
        else:
            print(f"{key}={value}")
    return 0


def _capture_begin_episode(
    config_path: str,
    bridge_id: str,
    conversation_id: str | None,
    episode_id: str | None,
    parent_conversation_id: str | None,
    related_conversation_ids: list[str],
    task_id: str | None,
    cwd: str | None,
    repo: str | None,
    branch: str | None,
    model: str | None,
    agent_runtime: str | None,
    platform_thread_id: str | None,
    metadata_json: str | None,
    emit_json: bool,
) -> int:
    from .models import BeginEpisodeRequest

    config = load_config(config_path)
    result = begin_episode(
        config,
        BeginEpisodeRequest(
            bridge_id=bridge_id,
            conversation_id=conversation_id,
            episode_id=episode_id,
            parent_conversation_id=parent_conversation_id,
            related_conversation_ids=related_conversation_ids,
            task_id=task_id,
            cwd=cwd,
            repo=repo,
            branch=branch,
            model=model,
            agent_runtime=agent_runtime,
            platform_thread_id=platform_thread_id,
            metadata=_json_arg(metadata_json, field_name="--metadata-json"),
        ),
    )
    return _print_or_json(result.to_dict(), emit_json)


def _capture_append_event(
    config_path: str,
    bridge_id: str,
    conversation_id: str,
    episode_id: str,
    event_type: str,
    payload_json: str | None,
    payload_file: str | None,
    content: str | None,
    task_id: str | None,
    branch_id: str,
    timestamp: str | None,
    emit_json: bool,
) -> int:
    from .models import AppendEventRequest

    config = load_config(config_path)
    result = append_event(
        config,
        AppendEventRequest(
            bridge_id=bridge_id,
            conversation_id=conversation_id,
            episode_id=episode_id,
            event_type=event_type,
            payload=_capture_payload(payload_json, payload_file, content),
            task_id=task_id,
            branch_id=branch_id,
            timestamp=timestamp,
        ),
    )
    return _print_or_json(result.to_dict(), emit_json)


def _capture_end_episode(
    config_path: str,
    bridge_id: str,
    conversation_id: str,
    episode_id: str,
    status: str,
    metadata_json: str | None,
    emit_json: bool,
) -> int:
    from .models import EndEpisodeRequest

    config = load_config(config_path)
    result = end_episode(
        config,
        EndEpisodeRequest(
            bridge_id=bridge_id,
            conversation_id=conversation_id,
            episode_id=episode_id,
            status=status,
            metadata=_json_arg(metadata_json, field_name="--metadata-json"),
        ),
    )
    return _print_or_json(result.to_dict(), emit_json)


def _capture_project_episode(config_path: str, conversation_id: str, episode_id: str, emit_json: bool) -> int:
    config = load_config(config_path)
    result = project_episode_for_promotion(
        config,
        conversation_id=conversation_id,
        episode_id=episode_id,
    )
    payload = {
        "archive_id": result.archive_id,
        "raw_path": result.raw_path,
        "chunk_count": result.chunk_count,
        "promoted_notes": result.promoted_notes,
        "review_items": result.review_items,
    }
    return _print_or_json(payload, emit_json)


def _orchestrator_begin_task(
    config_path: str,
    task_text: str,
    bridge_id: str,
    session_id: str | None,
    task_id: str | None,
    task_kind: str,
    temporality: str | None,
    token_budget: int | None,
    workspace: str | None,
    project: str | None,
    repo: str | None,
    tags: list[str],
    emit_json: bool,
) -> int:
    import uuid as _uuid
    config = load_config(config_path)
    req = OrchestratorTaskRequest(
        bridge_id=bridge_id,
        task_text=task_text,
        session_id=session_id or f"session-{_uuid.uuid4().hex[:12]}",
        task_id=task_id or f"task-{_uuid.uuid4().hex[:12]}",
        task_kind=task_kind,
        temporality=temporality,
        token_budget=token_budget,
        workspace=workspace,
        project=project,
        repo=repo,
        tags=tags,
    )
    result = begin_task(config, req)
    if emit_json:
        print(context_result_to_json(result))
        return 0
    print(f"task_id={result.task_id}")
    print(f"session_id={result.session_id}")
    print(f"task_kind={result.task_kind}")
    print(f"temporality={result.temporality}")
    print(f"retrieval_state={result.retrieval_state}")
    print(f"recommendation={result.recommendation}")
    print(f"verification_action={result.verification_action}")
    print(f"primary_memories={len(result.memory_packet.primary_memories)}")
    for m in result.memory_packet.primary_memories:
        print(f"  memory={m.id} [{m.type}] score={m.final_score:.3f}")
    for w in result.memory_packet.warnings:
        print(f"warning={w}")
    return 0


def _orchestrator_mark_failure(
    config_path: str,
    failure_text: str,
    bridge_id: str,
    session_id: str,
    task_id: str,
    task_kind: str,
    query_id: int | None,
    retrieval_state: str | None,
    emit_json: bool,
) -> int:
    config = load_config(config_path)
    result = mark_failure(
        config,
        OrchestratorFailureRequest(
            bridge_id=bridge_id,
            session_id=session_id,
            task_id=task_id,
            failure_text=failure_text,
            task_kind=task_kind,
            query_id=query_id,
            retrieval_state=retrieval_state,
        ),
    )
    if emit_json:
        print(context_result_to_json(result))
        return 0
    print(f"task_id={result.task_id}")
    print(f"session_id={result.session_id}")
    print(f"retrieval_state={result.retrieval_state}")
    print(f"recommendation={result.recommendation}")
    print(f"primary_memories={len(result.memory_packet.primary_memories)}")
    return 0


def _orchestrator_finish_task(
    config_path: str,
    bridge_id: str,
    session_id: str,
    task_id: str,
    status: str,
    outcome_type: str,
    summary: str | None,
    summary_file: str | None,
    query_id: int | None,
    retrieval_state: str | None,
    recommendation: str | None,
    useful_ids: list[str],
    misleading_ids: list[str],
    fresh_sources: list[str],
    note_type: str | None,
    title: str | None,
    writeback_intent: str | None,
    emit_json: bool,
) -> int:
    if bool(summary) == bool(summary_file):
        raise SystemExit("provide exactly one of --summary or --summary-file")
    resolved_summary = summary
    if summary_file is not None:
        resolved_summary = open(summary_file, encoding="utf-8").read()
    config = load_config(config_path)
    req = OrchestratorOutcomeRequest(
        bridge_id=bridge_id,
        session_id=session_id,
        task_id=task_id,
        status=status,
        outcome_type=outcome_type,
        summary=resolved_summary or "",
        query_id=query_id,
        retrieval_state=retrieval_state,
        recommendation=recommendation,
        useful_ids=useful_ids,
        misleading_ids=misleading_ids,
        fresh_sources_checked=fresh_sources,
        note_type=note_type,
        title=title,
        writeback_intent=writeback_intent,
    )
    result = finish_task(config, req)
    if result.decision != "skip":
        from .ingest import ingest_session
        ingest_session(
            config,
            bridge_id=bridge_id,
            session_id=session_id,
            content=resolved_summary or "",
        )
    if emit_json:
        print(writeback_result_to_json(result))
        return 0
    print(f"task_id={result.task_id}")
    print(f"session_id={result.session_id}")
    print(f"outcome_status={result.outcome_status}")
    print(f"decision={result.decision}")
    for t in result.targets:
        print(f"target={t}")
    for c in result.candidates:
        print(f"candidate={c}")
    print(f"reason={result.reason}")
    return 0


def _server_start(config_path: str, host: str, port: int) -> int:
    from .server import build_server
    config = load_config(config_path)
    server = build_server(config, host=host, port=port)
    actual_host, actual_port = server.server_address
    print(f"MnemonicOS server listening on http://{actual_host}:{actual_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def _daemon_is_alive(pid_path: os.PathLike[str] | str) -> bool:
    pid = read_pid(pid_path)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _daemon_start(config_path: str) -> int:
    config = load_config(config_path)
    if config.daemon is None:
        raise SystemExit("daemon configuration is not available")
    if _daemon_is_alive(config.daemon.pid_path):
        print("daemon already running")
        return 1
    command = [sys.executable, "-m", "second_brain.daemon", "--config", str(config.config_path or config_path)]
    subprocess.Popen(
        command,
        cwd=str(config.paths.workspace_root),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(20):
        time.sleep(0.1)
        if _daemon_is_alive(config.daemon.pid_path):
            print(f"daemon_started pid={read_pid(config.daemon.pid_path)}")
            return 0
    print("failed_to_start daemon")
    return 1


def _daemon_stop(config_path: str) -> int:
    config = load_config(config_path)
    if config.daemon is None:
        raise SystemExit("daemon configuration is not available")
    pid = read_pid(config.daemon.pid_path)
    if pid is None or not _daemon_is_alive(config.daemon.pid_path):
        print("daemon_not_running")
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        time.sleep(0.1)
        if not _daemon_is_alive(config.daemon.pid_path):
            print("daemon_stopped")
            return 0
    print("daemon_stop_timeout")
    return 1


def _daemon_status(config_path: str, emit_json: bool) -> int:
    config = load_config(config_path)
    if config.daemon is None:
        raise SystemExit("daemon configuration is not available")
    response = socket_send(config.daemon.socket_path, {"cmd": "status"})
    if response is None:
        pid = read_pid(config.daemon.pid_path)
        error_response = {"status": "error", "error": "daemon_unreachable", "pid": pid}
        if emit_json:
            print(json.dumps(error_response, sort_keys=True))
        else:
            print(daemon_unreachable_summary(pid))
        return 1
    if emit_json:
        print(json.dumps(response, sort_keys=True))
    else:
        print(daemon_status_summary(response))
    return 0 if response.get("status") == "ok" else 1


def _daemon_run_job(config_path: str, job_name: str) -> int:
    config = load_config(config_path)
    if config.daemon is None:
        raise SystemExit("daemon configuration is not available")
    response = socket_send(config.daemon.socket_path, {"cmd": "run-job", "job": job_name})
    if response is None:
        result = run_job(config, job_name=job_name, dry_run=False)
        print(json.dumps({"status": "ok", "job": result.job_name, "result": result.stats}, sort_keys=True))
        return 0
    print(json.dumps(response, sort_keys=True))
    return 0 if response.get("status") in {"ok", "busy"} else 1


def _run_job(config_path: str, job_name: str, dry_run: bool) -> int:
    config = load_config(config_path)
    result = run_job(config, job_name=job_name, dry_run=dry_run)
    print(f"job_name={result.job_name}")
    print(f"status={result.status}")
    for key, value in sorted(result.stats.items()):
        print(f"{key}={value}")
    for review_id in result.review_items_created:
        print(f"review={review_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "config", None) is None:
        args.config = str(default_config_path())

    if args.command == "init-db":
        return _init_db(args.config)
    if args.command == "sync":
        return _sync(args.config, args.mode, args.paths)
    if args.command == "ingest-session":
        return _ingest_session(
            args.config,
            args.bridge_id,
            args.session_id,
            args.agent,
            args.slug,
            args.file,
            args.content,
            args.tag,
            args.source_ref,
            args.promote_now,
        )
    if args.command == "ingest-artifact":
        return _ingest_artifact(args.config, args.file, args.tag, args.attachment, args.json)
    if args.command == "reprocess-artifact":
        return _reprocess_artifact(args.config, args.archive_id, args.failed, args.json)
    if args.command == "capture":
        if args.capture_command == "begin-episode":
            return _capture_begin_episode(
                args.config,
                args.bridge_id,
                args.conversation_id,
                args.episode_id,
                args.parent_conversation_id,
                args.related_conversation_id,
                args.task_id,
                args.cwd,
                args.repo,
                args.branch,
                args.model,
                args.agent_runtime,
                args.platform_thread_id,
                args.metadata_json,
                args.json,
            )
        if args.capture_command == "append-event":
            return _capture_append_event(
                args.config,
                args.bridge_id,
                args.conversation_id,
                args.episode_id,
                args.event_type,
                args.payload_json,
                args.payload_file,
                args.content,
                args.task_id,
                args.branch_id,
                args.timestamp,
                args.json,
            )
        if args.capture_command == "end-episode":
            return _capture_end_episode(
                args.config,
                args.bridge_id,
                args.conversation_id,
                args.episode_id,
                args.status,
                args.metadata_json,
                args.json,
            )
        if args.capture_command == "project-episode":
            return _capture_project_episode(args.config, args.conversation_id, args.episode_id, args.json)
    if args.command == "retrieve":
        return _retrieve(args.config, args.query, args.top_k, args.query_type_hint, args.json)
    if args.command == "resolve-task-context":
        return _resolve_task_context(
            args.config,
            args.task_text,
            args.bridge_id,
            args.session_id,
            args.task_id,
            args.task_kind,
            args.temporality,
            args.token_budget,
            args.workspace,
            args.project,
            args.repo,
            args.tag,
            args.top_k,
            args.query_type_hint,
            args.json,
            args.no_archive_fallback,
        )
    if args.command == "record-outcome":
        return _record_outcome(
            args.config,
            args.bridge_id,
            args.session_id,
            args.task_id,
            args.outcome_type,
            args.status,
            args.summary,
            args.task_kind,
            args.query_id,
            args.retrieval_state,
            args.recommendation,
            args.fresh_research_needed,
            args.fresh_source,
            args.useful_id,
            args.misleading_id,
            args.json,
        )
    if args.command == "decide-writeback":
        return _decide_writeback(
            args.config,
            args.bridge_id,
            args.session_id,
            args.task_id,
            args.note_type,
            args.title,
            args.summary_text,
            args.task_kind,
            args.retrieval_state,
            args.writeback_intent,
            args.existing_note_candidate,
            args.update_target_id,
            args.fresh_research_needed,
            args.fresh_source,
            args.json,
        )
    if args.command == "run-job":
        return _run_job(args.config, args.job_name, args.dry_run)
    if args.command == "daemon":
        if args.daemon_command == "start":
            return _daemon_start(args.config)
        if args.daemon_command == "stop":
            return _daemon_stop(args.config)
        if args.daemon_command == "status":
            return _daemon_status(args.config, args.json)
        if args.daemon_command == "run-job":
            return _daemon_run_job(args.config, args.job_name)
    if args.command == "orchestrator":
        if args.orchestrator_command == "begin-task":
            return _orchestrator_begin_task(
                args.config,
                args.task_text,
                args.bridge_id,
                args.session_id,
                args.task_id,
                args.task_kind,
                args.temporality,
                args.token_budget,
                args.workspace,
                args.project,
                args.repo,
                args.tag,
                args.json,
            )
        if args.orchestrator_command == "mark-failure":
            return _orchestrator_mark_failure(
                args.config,
                args.failure_text,
                args.bridge_id,
                args.session_id,
                args.task_id,
                args.task_kind,
                args.query_id,
                args.retrieval_state,
                args.json,
            )
        if args.orchestrator_command == "finish-task":
            return _orchestrator_finish_task(
                args.config,
                args.bridge_id,
                args.session_id,
                args.task_id,
                args.status,
                args.outcome_type,
                args.summary,
                args.summary_file,
                args.query_id,
                args.retrieval_state,
                args.recommendation,
                args.useful_id,
                args.misleading_id,
                args.fresh_source,
                args.note_type,
                args.title,
                args.writeback_intent,
                args.json,
            )
    if args.command == "server":
        return _server_start(args.config, args.host, args.port)

    parser.error(f"unknown command: {args.command}")
    return 2
