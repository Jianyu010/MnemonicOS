from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from typing import Any, Iterator

from .config import AppConfig
from .db import connect_db, run_migrations
from .models import (
    AppendEventRequest,
    AppendEventResult,
    BeginEpisodeRequest,
    BeginEpisodeResult,
    EndEpisodeRequest,
    EndEpisodeResult,
)


EVENT_TYPES = {
    "episode_started",
    "user_message",
    "assistant_message",
    "tool_call",
    "tool_result",
    "file_changed",
    "file_diff",
    "artifact_created",
    "retrieval_packet",
    "skill_use",
    "web_fetch",
    "conclusion",
    "plan_update",
    "agent_delegation",
    "failure",
    "branch_started",
    "branch_completed",
    "conversation_reference",
    "episode_ended",
}
END_STATUSES = {"success", "failure", "partial", "abandoned"}
DEFAULT_INLINE_THRESHOLD_BYTES = 32 * 1024
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def begin_episode(config: AppConfig, request: BeginEpisodeRequest) -> BeginEpisodeResult:
    bridge_id = _safe_component(request.bridge_id or "unknown")
    conversation_id = _resolve_conversation_id(config, request, bridge_id)
    episode_id = _safe_component(request.episode_id or _timestamp_id())
    now = _now()

    conversation_dir = _conversation_dir(config, conversation_id)
    episode_dir = conversation_dir / "episodes" / episode_id
    artifacts_dir = episode_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    events_path = episode_dir / "events.jsonl"
    transcript_path = episode_dir / "transcript.md"
    events_path.touch(exist_ok=True)
    transcript_path.touch(exist_ok=True)

    conversation_manifest_path = conversation_dir / "manifest.json"
    conversation_manifest = _read_json_file(conversation_manifest_path, default={})
    if not conversation_manifest:
        conversation_manifest = {
            "conversation_id": conversation_id,
            "bridge_id": bridge_id,
            "created_at": now,
            "last_active_at": now,
            "episode_count": 0,
            "episode_ids": [],
            "related_conversation_ids": [],
            "parent_conversation_id": request.parent_conversation_id,
        }
    conversation_manifest["last_active_at"] = now
    conversation_manifest["bridge_id"] = conversation_manifest.get("bridge_id") or bridge_id
    if request.parent_conversation_id:
        conversation_manifest["parent_conversation_id"] = request.parent_conversation_id
    related = list(conversation_manifest.get("related_conversation_ids") or [])
    for item in request.related_conversation_ids:
        if item not in related:
            related.append(item)
    conversation_manifest["related_conversation_ids"] = related
    episode_ids = list(conversation_manifest.get("episode_ids") or [])
    if episode_id not in episode_ids:
        episode_ids.append(episode_id)
    conversation_manifest["episode_ids"] = episode_ids
    conversation_manifest["episode_count"] = len(episode_ids)
    _write_json_file(conversation_manifest_path, conversation_manifest)

    episode_manifest = {
        "conversation_id": conversation_id,
        "episode_id": episode_id,
        "bridge_id": bridge_id,
        "task_id": request.task_id,
        "status": "active",
        "started_at": now,
        "ended_at": None,
        "event_count": _count_events(events_path),
        "artifact_count": _count_artifacts(artifacts_dir),
        "content_hash": None,
        "cwd": request.cwd,
        "repo": request.repo,
        "branch": request.branch,
        "model": request.model,
        "agent_runtime": request.agent_runtime,
        "platform_thread_id": request.platform_thread_id,
        "metadata": dict(request.metadata),
    }
    _write_json_file(episode_dir / "manifest.json", episode_manifest)

    if _count_events(events_path) == 0:
        append_event(
            config,
            AppendEventRequest(
                bridge_id=bridge_id,
                conversation_id=conversation_id,
                episode_id=episode_id,
                event_type="episode_started",
                task_id=request.task_id,
                payload={
                    "cwd": request.cwd,
                    "repo": request.repo,
                    "branch": request.branch,
                    "model": request.model,
                    "agent_runtime": request.agent_runtime,
                    "platform_thread_id": request.platform_thread_id,
                    "metadata": dict(request.metadata),
                },
            ),
        )
    else:
        _upsert_raw_episode_index(config, conversation_id, episode_id)

    return BeginEpisodeResult(
        conversation_id=conversation_id,
        episode_id=episode_id,
        conversation_path=str(conversation_dir),
        episode_path=str(episode_dir),
        events_path=str(events_path),
        transcript_path=str(transcript_path),
        artifacts_path=str(artifacts_dir),
    )


def append_event(config: AppConfig, request: AppendEventRequest) -> AppendEventResult:
    if request.event_type not in EVENT_TYPES:
        raise ValueError(f"unsupported event type: {request.event_type}")
    conversation_id = _safe_component(request.conversation_id)
    episode_id = _safe_component(request.episode_id)
    episode_dir = _episode_dir(config, conversation_id, episode_id)
    if not episode_dir.exists():
        raise FileNotFoundError(f"episode does not exist: {conversation_id}/{episode_id}")

    events_path = episode_dir / "events.jsonl"
    transcript_path = episode_dir / "transcript.md"
    artifacts_dir = episode_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    lock_path = episode_dir / "events.lock"
    artifact_paths: list[str] = []

    with _append_lock(lock_path):
        seq = _latest_seq(events_path) + 1
        payload, written = _externalize_large_payload(
            request.payload,
            artifacts_dir=artifacts_dir,
            seq=seq,
            threshold=_artifact_threshold(),
        )
        artifact_paths.extend(str(path) for path in written)
        event = {
            "seq": seq,
            "ts": request.timestamp or _now(),
            "conversation_id": conversation_id,
            "episode_id": episode_id,
            "bridge_id": _safe_component(request.bridge_id or "unknown"),
            "task_id": request.task_id,
            "branch_id": request.branch_id or "main",
            "type": request.event_type,
            "payload": payload,
        }
        line = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        tmp_path = events_path.with_name(f"events.jsonl.{os.getpid()}.{seq}.tmp")
        _write_and_fsync(tmp_path, line.encode("utf-8"))
        with events_path.open("ab") as handle:
            handle.write(tmp_path.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.unlink(missing_ok=True)

    _append_transcript(transcript_path, request.event_type, payload)
    _update_manifests_after_append(config, conversation_id, episode_id, artifact_delta=len(written))
    _upsert_raw_episode_index(config, conversation_id, episode_id)
    return AppendEventResult(
        conversation_id=conversation_id,
        episode_id=episode_id,
        seq=seq,
        events_path=str(events_path),
        transcript_path=str(transcript_path),
        artifact_paths=artifact_paths,
    )


def end_episode(config: AppConfig, request: EndEpisodeRequest) -> EndEpisodeResult:
    if request.status not in END_STATUSES:
        raise ValueError(f"unsupported episode status: {request.status}")
    append_event(
        config,
        AppendEventRequest(
            bridge_id=request.bridge_id,
            conversation_id=request.conversation_id,
            episode_id=request.episode_id,
            event_type="episode_ended",
            payload={"status": request.status, "metadata": dict(request.metadata)},
        ),
    )
    conversation_id = _safe_component(request.conversation_id)
    episode_id = _safe_component(request.episode_id)
    episode_dir = _episode_dir(config, conversation_id, episode_id)
    manifest_path = episode_dir / "manifest.json"
    manifest = _read_json_file(manifest_path, default={})
    events_path = episode_dir / "events.jsonl"
    content_hash = _sha256_path(events_path)
    manifest.update(
        {
            "status": request.status,
            "ended_at": _now(),
            "event_count": _count_events(events_path),
            "artifact_count": _count_artifacts(episode_dir / "artifacts"),
            "content_hash": content_hash,
            "end_metadata": dict(request.metadata),
        }
    )
    _write_json_file(manifest_path, manifest)
    conversation_manifest_path = _conversation_dir(config, conversation_id) / "manifest.json"
    conversation_manifest = _read_json_file(conversation_manifest_path, default={})
    conversation_manifest["last_active_at"] = manifest["ended_at"]
    _write_json_file(conversation_manifest_path, conversation_manifest)
    _upsert_raw_episode_index(config, conversation_id, episode_id)
    from .extraction.queue import enqueue_extraction as _enqueue_extraction
    _enqueue_extraction(
        config,
        source_kind="episode",
        conversation_id=conversation_id,
        episode_id=episode_id,
        bridge_id=request.bridge_id,
        session_id=request.episode_id,
        content_hash=content_hash,
    )
    if config.ingest.auto_trigger_pipeline:
        from .pipeline import trigger_pipeline as _trigger_pipeline
        _trigger_pipeline(config)
    return EndEpisodeResult(
        conversation_id=conversation_id,
        episode_id=episode_id,
        status=request.status,
        event_count=int(manifest["event_count"]),
        content_hash=content_hash,
        manifest_path=str(manifest_path),
    )


def _resolve_conversation_id(config: AppConfig, request: BeginEpisodeRequest, bridge_id: str) -> str:
    if request.conversation_id:
        conversation_id = _safe_component(request.conversation_id)
        _write_bridge_state(config, bridge_id, request, conversation_id)
        return conversation_id

    state_path = _bridge_state_path(config, bridge_id, request.cwd)
    state = _read_json_file(state_path, default={})
    platform_thread_id = request.platform_thread_id
    if state and (not platform_thread_id or state.get("platform_thread_id") in {None, platform_thread_id}):
        conversation_id = _safe_component(str(state["conversation_id"]))
    else:
        seed = platform_thread_id or uuid.uuid4().hex
        conversation_id = _safe_component(f"{bridge_id}-{seed}")
    _write_bridge_state(config, bridge_id, request, conversation_id)
    return conversation_id


def _write_bridge_state(config: AppConfig, bridge_id: str, request: BeginEpisodeRequest, conversation_id: str) -> None:
    state_path = _bridge_state_path(config, bridge_id, request.cwd)
    now = _now()
    previous = _read_json_file(state_path, default={})
    payload = {
        "bridge_id": bridge_id,
        "conversation_id": conversation_id,
        "cwd_hash": _cwd_hash(request.cwd),
        "created_at": previous.get("created_at") or now,
        "last_seen_at": now,
        "platform_thread_id": request.platform_thread_id,
    }
    _write_json_file(state_path, payload)


def _conversation_dir(config: AppConfig, conversation_id: str) -> Path:
    return config.paths.vault_root / "raw" / "sessions" / _safe_component(conversation_id)


def _episode_dir(config: AppConfig, conversation_id: str, episode_id: str) -> Path:
    return _conversation_dir(config, conversation_id) / "episodes" / _safe_component(episode_id)


def _bridge_state_path(config: AppConfig, bridge_id: str, cwd: str | None) -> Path:
    return config.paths.vault_root / "raw" / "sessions" / ".bridge_state" / _safe_component(bridge_id) / f"{_cwd_hash(cwd)}.json"


def _migrations_dir(config: AppConfig) -> Path:
    configured = config.paths.workspace_root / "migrations"
    return configured if configured.exists() else _PACKAGE_ROOT / "migrations"


def _cwd_hash(cwd: str | None) -> str:
    raw = str(Path(cwd).resolve() if cwd else Path.cwd().resolve())
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _safe_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._=@+-]+", "-", str(value).strip()).strip("-")
    return normalized[:180] or uuid.uuid4().hex


def _timestamp_id() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H%M%S%z")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _artifact_threshold() -> int:
    raw_value = os.environ.get("MNEMOS_ARTIFACT_THRESHOLD_BYTES")
    if not raw_value:
        return DEFAULT_INLINE_THRESHOLD_BYTES
    try:
        return max(1024, int(raw_value))
    except ValueError:
        return DEFAULT_INLINE_THRESHOLD_BYTES


def _externalize_large_payload(
    payload: dict[str, Any],
    *,
    artifacts_dir: Path,
    seq: int,
    threshold: int,
) -> tuple[dict[str, Any], list[Path]]:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) <= threshold:
        return payload, []
    artifact_path = artifacts_dir / f"event-{seq:06d}-payload.json"
    _write_and_fsync(artifact_path, encoded + b"\n")
    relative = f"artifacts/{artifact_path.name}"
    return {
        "artifact": relative,
        "byte_size": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "externalized_payload": True,
    }, [artifact_path]


@contextmanager
def _append_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _latest_seq(events_path: Path) -> int:
    if not events_path.exists():
        return 0
    latest = 0
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            latest = max(latest, int(json.loads(line).get("seq") or 0))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return latest


def _count_events(events_path: Path) -> int:
    if not events_path.exists():
        return 0
    return sum(1 for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip())


def _count_artifacts(artifacts_dir: Path) -> int:
    if not artifacts_dir.exists():
        return 0
    return sum(1 for path in artifacts_dir.rglob("*") if path.is_file())


def _append_transcript(transcript_path: Path, event_type: str, payload: dict[str, Any]) -> None:
    if event_type not in {"user_message", "assistant_message"}:
        return
    content = payload.get("content")
    if isinstance(content, list):
        # Extract text blocks from raw API content array; skip thinking/tool_use
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        text = "\n".join(p for p in parts if p).strip()
        if not text:
            return
    elif isinstance(content, str):
        text = content.strip()
    else:
        return
    heading = "User" if event_type == "user_message" else "Assistant"
    with transcript_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## {heading}\n\n{text}\n")


def _update_manifests_after_append(config: AppConfig, conversation_id: str, episode_id: str, *, artifact_delta: int) -> None:
    now = _now()
    conversation_manifest_path = _conversation_dir(config, conversation_id) / "manifest.json"
    conversation_manifest = _read_json_file(conversation_manifest_path, default={})
    conversation_manifest["last_active_at"] = now
    _write_json_file(conversation_manifest_path, conversation_manifest)

    episode_dir = _episode_dir(config, conversation_id, episode_id)
    episode_manifest_path = episode_dir / "manifest.json"
    episode_manifest = _read_json_file(episode_manifest_path, default={})
    episode_manifest["event_count"] = _count_events(episode_dir / "events.jsonl")
    episode_manifest["artifact_count"] = int(episode_manifest.get("artifact_count") or 0) + artifact_delta
    _write_json_file(episode_manifest_path, episode_manifest)


def _upsert_raw_episode_index(config: AppConfig, conversation_id: str, episode_id: str) -> None:
    episode_dir = _episode_dir(config, conversation_id, episode_id)
    manifest_path = episode_dir / "manifest.json"
    manifest = _read_json_file(manifest_path, default={})
    conversation_dir = _conversation_dir(config, conversation_id)
    events_path = episode_dir / "events.jsonl"
    transcript_path = episode_dir / "transcript.md"
    artifacts_path = episode_dir / "artifacts"

    connection = connect_db(config.paths.db_path)
    run_migrations(connection, _migrations_dir(config))
    try:
        with connection:
            _upsert_raw_episode_index_with_connection(
                connection,
                conversation_id=conversation_id,
                episode_id=episode_id,
                bridge_id=str(manifest.get("bridge_id") or "unknown"),
                task_id=manifest.get("task_id"),
                status=str(manifest.get("status") or "active"),
                conversation_path=str(conversation_dir.resolve()),
                episode_path=str(episode_dir.resolve()),
                events_path=str(events_path.resolve()),
                transcript_path=str(transcript_path.resolve()),
                artifacts_path=str(artifacts_path.resolve()),
                event_count=_count_events(events_path),
                artifact_count=_count_artifacts(artifacts_path),
                content_hash=manifest.get("content_hash"),
                started_at=manifest.get("started_at"),
                ended_at=manifest.get("ended_at"),
            )
    finally:
        connection.close()


def _upsert_raw_episode_index_with_connection(
    connection: sqlite3.Connection,
    *,
    conversation_id: str,
    episode_id: str,
    bridge_id: str,
    task_id: str | None,
    status: str,
    conversation_path: str,
    episode_path: str,
    events_path: str,
    transcript_path: str,
    artifacts_path: str,
    event_count: int,
    artifact_count: int,
    content_hash: str | None,
    started_at: str | None,
    ended_at: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO raw_episodes(
            conversation_id, episode_id, bridge_id, task_id, status,
            conversation_path, episode_path, events_path, transcript_path, artifacts_path,
            event_count, artifact_count, content_hash, started_at, ended_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(conversation_id, episode_id) DO UPDATE SET
            bridge_id = excluded.bridge_id,
            task_id = excluded.task_id,
            status = excluded.status,
            conversation_path = excluded.conversation_path,
            episode_path = excluded.episode_path,
            events_path = excluded.events_path,
            transcript_path = excluded.transcript_path,
            artifacts_path = excluded.artifacts_path,
            event_count = excluded.event_count,
            artifact_count = excluded.artifact_count,
            content_hash = excluded.content_hash,
            started_at = excluded.started_at,
            ended_at = excluded.ended_at,
            updated_at = excluded.updated_at
        """,
        (
            conversation_id,
            episode_id,
            bridge_id,
            task_id,
            status,
            conversation_path,
            episode_path,
            events_path,
            transcript_path,
            artifacts_path,
            event_count,
            artifact_count,
            content_hash,
            started_at,
            ended_at,
        ),
    )


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    _write_and_fsync(tmp_path, data)
    os.replace(tmp_path, path)
    directory_fd = os.open(str(path.parent), getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_json_file(path: Path, *, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else dict(default)
    except (json.JSONDecodeError, OSError):
        return dict(default)


def _write_and_fsync(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
