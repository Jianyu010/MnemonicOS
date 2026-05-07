"""
MnemonicOS HTTP server — stdlib-only, no external dependencies.

Implements api/openapi.yaml v0.5.0 plus the orchestrator extension:
  GET  /v1/health
  POST /v1/retrieve
  POST /v1/ingest/session
  POST /v1/ingest/artifact
  POST /v1/reprocess/artifact
  POST /v1/capture/begin-episode
  POST /v1/capture/append-event
  POST /v1/capture/end-episode
  POST /v1/extraction/queue
  POST /v1/extraction/process
  GET  /v1/extraction/status
  POST /v1/sync/vault
  POST /v1/jobs/run
  POST /v1/orchestrator/begin-task
  POST /v1/orchestrator/mark-failure
  POST /v1/orchestrator/finish-task

Start via CLI:  mnemonicos server [--host 127.0.0.1] [--port 8000]
Or in-process: server = build_server(config); server.serve_forever()
"""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import AppConfig

logger = logging.getLogger("mnemonicos.server")


# ---------------------------------------------------------------------------
# Request / response helpers
# ---------------------------------------------------------------------------

class _BadRequest(ValueError):
    """Raised by route handlers when the caller supplied invalid input."""


def _require(body: dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if k not in body or body[k] is None]
    if missing:
        raise _BadRequest(f"missing required field(s): {', '.join(missing)}")


# ---------------------------------------------------------------------------
# Route handlers — each takes (config, body) → dict
# ---------------------------------------------------------------------------

def _handle_retrieve(config: AppConfig, body: dict[str, Any]) -> dict[str, Any]:
    _require(body, "query")
    from .retrieval import result_to_json, retrieve
    result = retrieve(
        config,
        body["query"],
        top_k=body.get("top_k"),
        query_type_hint=body.get("query_type_hint"),
        include_archive_fallback=body.get("include_archive_fallback"),
    )
    return json.loads(result_to_json(result))


def _handle_ingest_session(config: AppConfig, body: dict[str, Any]) -> dict[str, Any]:
    _require(body, "bridge_id", "session_id", "content")
    from .ingest import ingest_session
    result = ingest_session(
        config,
        bridge_id=body["bridge_id"],
        session_id=body["session_id"],
        content=body["content"],
        tags=body.get("tags") or [],
        source_refs=body.get("source_refs") or [],
    )
    return {
        "archive_id": result.archive_id,
        "raw_path": result.raw_path,
        "chunk_count": result.chunk_count,
        "promoted_notes": result.promoted_notes,
        "review_items": result.review_items,
    }


def _handle_ingest_artifact(config: AppConfig, body: dict[str, Any]) -> dict[str, Any]:
    _require(body, "path")
    from .ingest import ingest_artifact
    result = ingest_artifact(
        config,
        path=body["path"],
        tags=body.get("tags") or [],
        attachments=body.get("attachments") or [],
    )
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


def _artifact_response(result: Any) -> dict[str, Any]:
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


def _handle_reprocess_artifact(config: AppConfig, body: dict[str, Any]) -> dict[str, Any]:
    from .ingest import reprocess_artifact, reprocess_failed_artifacts
    if body.get("failed"):
        return {"results": [_artifact_response(result) for result in reprocess_failed_artifacts(config)]}
    _require(body, "archive_id")
    return _artifact_response(reprocess_artifact(config, archive_id=body["archive_id"]))


def _handle_capture_begin_episode(config: AppConfig, body: dict[str, Any]) -> dict[str, Any]:
    _require(body, "bridge_id")
    from .models import BeginEpisodeRequest
    from .session_capture import begin_episode
    result = begin_episode(
        config,
        BeginEpisodeRequest(
            bridge_id=body["bridge_id"],
            conversation_id=body.get("conversation_id"),
            episode_id=body.get("episode_id"),
            parent_conversation_id=body.get("parent_conversation_id"),
            related_conversation_ids=body.get("related_conversation_ids") or [],
            task_id=body.get("task_id"),
            cwd=body.get("cwd"),
            repo=body.get("repo"),
            branch=body.get("branch"),
            model=body.get("model"),
            agent_runtime=body.get("agent_runtime"),
            platform_thread_id=body.get("platform_thread_id"),
            metadata=body.get("metadata") or {},
        ),
    )
    return result.to_dict()


def _handle_capture_append_event(config: AppConfig, body: dict[str, Any]) -> dict[str, Any]:
    _require(body, "bridge_id", "conversation_id", "episode_id", "type")
    from .models import AppendEventRequest
    from .session_capture import append_event
    result = append_event(
        config,
        AppendEventRequest(
            bridge_id=body["bridge_id"],
            conversation_id=body["conversation_id"],
            episode_id=body["episode_id"],
            event_type=body["type"],
            payload=body.get("payload") or {},
            task_id=body.get("task_id"),
            branch_id=body.get("branch_id") or "main",
            timestamp=body.get("timestamp"),
        ),
    )
    return result.to_dict()


def _handle_capture_end_episode(config: AppConfig, body: dict[str, Any]) -> dict[str, Any]:
    _require(body, "bridge_id", "conversation_id", "episode_id")
    from .models import EndEpisodeRequest
    from .session_capture import end_episode
    result = end_episode(
        config,
        EndEpisodeRequest(
            bridge_id=body["bridge_id"],
            conversation_id=body["conversation_id"],
            episode_id=body["episode_id"],
            status=body.get("status") or "success",
            metadata=body.get("metadata") or {},
        ),
    )
    return result.to_dict()


def _handle_sync_vault(config: AppConfig, body: dict[str, Any]) -> dict[str, Any]:
    _require(body, "mode")
    from .sync import sync_vault
    result = sync_vault(config, mode=body["mode"], selected_paths=body.get("paths") or [])
    return {
        "scanned_paths": result.scanned_paths,
        "synced_notes": result.synced_notes,
        "deleted_notes": result.deleted_notes,
        "parse_errors": result.parse_errors,
    }


def _handle_jobs_run(config: AppConfig, body: dict[str, Any]) -> dict[str, Any]:
    _require(body, "job_name")
    from .jobs import run_job
    result = run_job(config, job_name=body["job_name"], dry_run=bool(body.get("dry_run")))
    return {
        "job_name": result.job_name,
        "status": result.status,
        "stats": result.stats,
        "review_items_created": result.review_items_created,
    }


def _handle_extraction_queue(config: AppConfig, body: dict[str, Any]) -> dict[str, Any]:
    _require(body, "source_kind")
    from .extraction import enqueue_extraction
    item = enqueue_extraction(
        config,
        source_kind=body["source_kind"],
        archive_id=body.get("archive_id"),
        conversation_id=body.get("conversation_id"),
        episode_id=body.get("episode_id"),
        bridge_id=body.get("bridge_id"),
        session_id=body.get("session_id"),
        task_id=body.get("task_id"),
    )
    return item.to_dict()


def _handle_extraction_process(config: AppConfig, body: dict[str, Any]) -> dict[str, Any]:
    from .extraction import process_extraction_queue
    result = process_extraction_queue(config, dry_run=bool(body.get("dry_run")))
    return result.to_stats()


def _handle_extraction_status(config: AppConfig, queue_id: int | None = None) -> dict[str, Any]:
    from .extraction import extraction_status
    return extraction_status(config, queue_id=queue_id)


def _handle_orchestrator_begin_task(config: AppConfig, body: dict[str, Any]) -> dict[str, Any]:
    _require(body, "bridge_id", "task_text")
    import uuid as _uuid
    from .orchestrator import OrchestratorTaskRequest, begin_task
    req = OrchestratorTaskRequest(
        bridge_id=body["bridge_id"],
        task_text=body["task_text"],
        session_id=body.get("session_id") or f"session-{_uuid.uuid4().hex[:12]}",
        task_id=body.get("task_id") or f"task-{_uuid.uuid4().hex[:12]}",
        task_kind=body.get("task_kind") or "",
        temporality=body.get("temporality"),
        token_budget=body.get("token_budget"),
        workspace=body.get("workspace"),
        project=body.get("project"),
        repo=body.get("repo"),
        tags=body.get("tags") or [],
    )
    result = begin_task(config, req)
    return result.to_dict()


def _handle_orchestrator_mark_failure(config: AppConfig, body: dict[str, Any]) -> dict[str, Any]:
    _require(body, "bridge_id", "session_id", "task_id", "failure_text")
    from .orchestrator import OrchestratorFailureRequest, mark_failure
    result = mark_failure(
        config,
        OrchestratorFailureRequest(
            bridge_id=body["bridge_id"],
            session_id=body["session_id"],
            task_id=body["task_id"],
            failure_text=body["failure_text"],
            task_kind=body.get("task_kind") or "general",
            query_id=body.get("query_id"),
            retrieval_state=body.get("retrieval_state"),
        ),
    )
    return result.to_dict()


def _handle_orchestrator_finish_task(config: AppConfig, body: dict[str, Any]) -> dict[str, Any]:
    _require(body, "bridge_id", "session_id", "task_id", "status", "outcome_type", "summary")
    from .orchestrator import OrchestratorOutcomeRequest, finish_task
    req = OrchestratorOutcomeRequest(
        bridge_id=body["bridge_id"],
        session_id=body["session_id"],
        task_id=body["task_id"],
        status=body["status"],
        outcome_type=body["outcome_type"],
        summary=body["summary"],
        query_id=body.get("query_id"),
        retrieval_state=body.get("retrieval_state"),
        recommendation=body.get("recommendation"),
        useful_ids=body.get("useful_ids") or [],
        misleading_ids=body.get("misleading_ids") or [],
        fresh_sources_checked=body.get("fresh_sources_checked") or [],
        note_type=body.get("note_type"),
        title=body.get("title"),
        writeback_intent=body.get("writeback_intent"),
    )
    result = finish_task(config, req)
    if result.decision != "skip":
        from .ingest import ingest_session
        ingest_session(
            config,
            bridge_id=body["bridge_id"],
            session_id=body["session_id"],
            content=body["summary"],
        )
    return result.to_dict()


_POST_ROUTES: dict[str, Any] = {
    "/v1/retrieve": _handle_retrieve,
    "/v1/ingest/session": _handle_ingest_session,
    "/v1/ingest/artifact": _handle_ingest_artifact,
    "/v1/reprocess/artifact": _handle_reprocess_artifact,
    "/v1/capture/begin-episode": _handle_capture_begin_episode,
    "/v1/capture/append-event": _handle_capture_append_event,
    "/v1/capture/end-episode": _handle_capture_end_episode,
    "/v1/extraction/queue": _handle_extraction_queue,
    "/v1/extraction/process": _handle_extraction_process,
    "/v1/sync/vault": _handle_sync_vault,
    "/v1/jobs/run": _handle_jobs_run,
    "/v1/orchestrator/begin-task": _handle_orchestrator_begin_task,
    "/v1/orchestrator/mark-failure": _handle_orchestrator_mark_failure,
    "/v1/orchestrator/finish-task": _handle_orchestrator_finish_task,
}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class _MnemonicOSHandler(BaseHTTPRequestHandler):
    """Per-request handler.  config is injected via build_server's factory."""

    config: AppConfig  # set on the subclass by build_server

    # Silence the default access log to stderr.
    def log_message(self, fmt: str, *args: object) -> None:  # type: ignore[override]
        logger.debug(fmt, *args)

    def _read_json(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _respond(self, code: int, data: dict[str, Any]) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/v1/health":
            from .db import connect_db
            db_ready = False
            try:
                conn = connect_db(self.config.paths.db_path)
                conn.execute("SELECT 1").fetchone()
                conn.close()
                db_ready = True
            except Exception:
                pass
            self._respond(200, {"status": "ok", "db_ready": db_ready})
        elif path == "/v1/extraction/status":
            query = parse_qs(parsed.query)
            queue_id_raw = (query.get("queue_id") or [None])[0]
            queue_id = int(queue_id_raw) if queue_id_raw else None
            try:
                self._respond(200, _handle_extraction_status(self.config, queue_id=queue_id))
            except (FileNotFoundError, ValueError) as exc:
                self._respond(400, {"error": "bad_request", "detail": str(exc)})
        else:
            self._respond(404, {"error": "not_found", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        body = self._read_json()
        if body is None:
            self._respond(400, {"error": "invalid_json"})
            return

        path = self.path.split("?")[0]
        handler = _POST_ROUTES.get(path)
        if handler is None:
            self._respond(404, {"error": "not_found", "path": path})
            return

        try:
            result = handler(self.config, body)
            self._respond(200, result)
        except _BadRequest as exc:
            self._respond(400, {"error": "bad_request", "detail": str(exc)})
        except (FileNotFoundError, ValueError) as exc:
            self._respond(400, {"error": "bad_request", "detail": str(exc)})
        except Exception as exc:
            logger.exception("POST %s failed: %s", path, exc)
            self._respond(500, {"error": "internal_error", "detail": str(exc)})


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def build_server(
    config: AppConfig,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> HTTPServer:
    """
    Create an HTTPServer bound to host:port with config injected.

    Returns the HTTPServer instance.  Caller is responsible for
    calling serve_forever() (or handle_request() in tests).
    """
    # Inject config into the handler class via a dedicated subclass.
    bound_handler = type(
        "_BoundHandler",
        (_MnemonicOSHandler,),
        {"config": config},
    )
    server = HTTPServer((host, port), bound_handler)
    return server
