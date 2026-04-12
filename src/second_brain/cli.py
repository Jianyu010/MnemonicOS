from __future__ import annotations

import argparse

from .config import load_config
from .db import connect_db, run_migrations
from .ingest import ingest_session
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
from .sync import sync_vault


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MnemonicOS local memory system CLI")
    parser.add_argument(
        "--config",
        default="config/memory.example.toml",
        help="Path to the TOML config file.",
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
    jobs_parser.add_argument("job_name", choices=("daily_consolidation", "weekly_hygiene", "sync_vault_incremental"))
    jobs_parser.add_argument("--dry-run", action="store_true")

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
    result = ingest_session(
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
        )
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

    parser.error(f"unknown command: {args.command}")
    return 2
