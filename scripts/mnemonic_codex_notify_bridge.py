#!/usr/bin/env python3
"""Capture Codex Desktop turns through the `notify` callback.

Codex Desktop passes a structured `agent-turn-complete` payload to the notify
command on recent builds.  Use that payload as the authoritative turn boundary
when it is available, and keep transcript tailing as a fallback for older or
manually invoked notifications.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from second_brain.config import load_config  # noqa: E402
from second_brain.models import AppendEventRequest, BeginEpisodeRequest, EndEpisodeRequest  # noqa: E402
from second_brain.session_capture import append_event, begin_episode, end_episode  # noqa: E402


CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
STATE_PATH = Path(os.environ.get("MNEMO_CODEX_NOTIFY_STATE", "/tmp/mnemo_codex_notify_state.json"))
LOG_PATH = Path(os.environ.get("MNEMO_CODEX_NOTIFY_LOG", "/tmp/mnemo_codex_notify.log"))


def main(argv: list[str]) -> int:
    _log({"phase": "start", "argv": argv})
    notify_payload = _notify_payload(argv)
    if notify_payload is not None:
        return _capture_notify_payload(argv, notify_payload)

    return _capture_transcript_delta(argv)


def _capture_notify_payload(argv: list[str], notify_payload: dict[str, Any]) -> int:
    events = list(_capture_notify_events(notify_payload))
    if not events:
        _log({"phase": "skip", "reason": "no notify payload messages"})
        return 0

    os.environ.setdefault("MNEMOS_AUTO_TRIGGER_PIPELINE", "0")
    config_path = os.environ.get("MNEMO_CONFIG") or str(ROOT / "config" / "memory.toml")
    if not Path(config_path).exists():
        config_path = str(ROOT / "config" / "memory.example.toml")
    config = load_config(config_path)

    conversation_id = str(notify_payload.get("thread-id") or notify_payload.get("thread_id") or "").strip()
    turn_id = str(notify_payload.get("turn-id") or notify_payload.get("turn_id") or "").strip()
    cwd = notify_payload.get("cwd") if isinstance(notify_payload.get("cwd"), str) else None

    begun = begin_episode(
        config,
        BeginEpisodeRequest(
            bridge_id="codex",
            conversation_id=conversation_id,
            episode_id=turn_id or None,
            cwd=cwd,
            agent_runtime="codex-desktop-notify",
            platform_thread_id=conversation_id or None,
            metadata={
                "source": "codex_notify_payload",
                "client": notify_payload.get("client"),
                "turn_id": turn_id or None,
                "argv": argv,
            },
        ),
    )

    for event_type, payload, timestamp in events:
        append_event(
            config,
            AppendEventRequest(
                bridge_id="codex",
                conversation_id=begun.conversation_id,
                episode_id=begun.episode_id,
                event_type=event_type,
                payload=payload,
                timestamp=timestamp,
            ),
        )

    ended = end_episode(
        config,
        EndEpisodeRequest(
            bridge_id="codex",
            conversation_id=begun.conversation_id,
            episode_id=begun.episode_id,
            status="success",
            metadata={
                "source": "codex_notify_payload",
                "event_count_imported": len(events),
                "turn_id": turn_id or None,
            },
        ),
    )

    _log(
        {
            "phase": "captured",
            "source": "notify_payload",
            "conversation_id": begun.conversation_id,
            "episode_id": begun.episode_id,
            "events": len(events),
            "manifest": ended.manifest_path,
        }
    )
    return 0


def _capture_transcript_delta(argv: list[str]) -> int:
    transcript = _latest_transcript()
    if transcript is None:
        _log({"phase": "skip", "reason": "no transcript"})
        return 0

    state = _read_json(STATE_PATH, {})
    key = str(transcript)
    previous_offset = int(state.get(key, {}).get("offset", 0))
    size = transcript.stat().st_size
    if previous_offset > size:
        previous_offset = 0

    with transcript.open("r", encoding="utf-8") as handle:
        handle.seek(previous_offset)
        raw = handle.read()
        next_offset = handle.tell()

    if not raw.strip():
        _log({"phase": "skip", "reason": "no new bytes", "transcript": key, "offset": next_offset})
        state.setdefault(key, {})["offset"] = next_offset
        _write_json(STATE_PATH, state)
        return 0

    entries = [_parse_line(line) for line in raw.splitlines() if line.strip()]
    entries = [entry for entry in entries if entry is not None]
    all_entries = _read_all_entries(transcript)
    meta = _session_meta(all_entries)
    conversation_id = _conversation_id(transcript, meta)
    relevant = list(_capture_events(entries))

    if not relevant:
        state.setdefault(key, {})["offset"] = next_offset
        _write_json(STATE_PATH, state)
        _log({"phase": "skip", "reason": "no capturable entries", "transcript": key})
        return 0

    os.environ.setdefault("MNEMOS_AUTO_TRIGGER_PIPELINE", "0")
    config_path = os.environ.get("MNEMO_CONFIG") or str(ROOT / "config" / "memory.toml")
    if not Path(config_path).exists():
        config_path = str(ROOT / "config" / "memory.example.toml")
    config = load_config(config_path)

    begun = begin_episode(
        config,
        BeginEpisodeRequest(
            bridge_id="codex",
            conversation_id=conversation_id,
            cwd=meta.get("cwd"),
            model=meta.get("model"),
            agent_runtime="codex-desktop-notify",
            platform_thread_id=conversation_id,
            metadata={
                "source": "codex_notify",
                "transcript_path": key,
                "offset_start": previous_offset,
                "offset_end": next_offset,
                "argv": argv,
            },
        ),
    )

    for event_type, payload, timestamp in relevant:
        append_event(
            config,
            AppendEventRequest(
                bridge_id="codex",
                conversation_id=begun.conversation_id,
                episode_id=begun.episode_id,
                event_type=event_type,
                payload=payload,
                timestamp=timestamp,
            ),
        )

    ended = end_episode(
        config,
        EndEpisodeRequest(
            bridge_id="codex",
            conversation_id=begun.conversation_id,
            episode_id=begun.episode_id,
            status="success",
            metadata={
                "source": "codex_notify",
                "transcript_path": key,
                "event_count_imported": len(relevant),
            },
        ),
    )

    state.setdefault(key, {})["offset"] = next_offset
    state[key]["conversation_id"] = conversation_id
    state[key]["last_episode_id"] = begun.episode_id
    _write_json(STATE_PATH, state)
    _log(
        {
            "phase": "captured",
            "transcript": key,
            "conversation_id": conversation_id,
            "episode_id": begun.episode_id,
            "events": len(relevant),
            "manifest": ended.manifest_path,
        }
    )
    return 0


def _notify_payload(argv: list[str]) -> dict[str, Any] | None:
    for arg in argv:
        try:
            payload = json.loads(arg)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("type") == "agent-turn-complete":
            return payload
    return None


def _capture_notify_events(payload: dict[str, Any]):
    turn_id = payload.get("turn-id") or payload.get("turn_id")
    input_messages = payload.get("input-messages")
    if isinstance(input_messages, list):
        for message in input_messages:
            if isinstance(message, str) and message.strip():
                yield "user_message", {
                    "content": message.strip(),
                    "source": "codex_notify_payload",
                    "turn_id": turn_id,
                }, None

    assistant = payload.get("last-assistant-message") or payload.get("last_assistant_message")
    if isinstance(assistant, str) and assistant.strip():
        yield "assistant_message", {
            "content": assistant.strip(),
            "source": "codex_notify_payload",
            "turn_id": turn_id,
        }, None

    for embedded in _embedded_notify_entries(payload):
        for event_type, event_payload, timestamp in _capture_events([embedded]):
            event_payload.setdefault("turn_id", turn_id)
            yield event_type, event_payload, timestamp


def _embedded_notify_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return structured turn events carried inside an agent-turn-complete payload.

    Codex notify payload shapes have changed over time.  The importer accepts a
    small set of explicit event-array keys so wrappers can pass full transcript
    entries without losing the stable turn boundary from agent-turn-complete.
    """
    entries: list[dict[str, Any]] = []
    for key in ("events", "transcript-events", "transcript_events", "response-items", "response_items"):
        raw_entries = payload.get(key)
        if not isinstance(raw_entries, list):
            continue
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            if key in {"response-items", "response_items"}:
                entries.append(
                    {
                        "type": "response_item",
                        "timestamp": raw.get("timestamp") or payload.get("timestamp"),
                        "payload": raw,
                    }
                )
            else:
                entries.append(raw)
    return entries


def _latest_transcript() -> Path | None:
    sessions = CODEX_HOME / "sessions"
    if not sessions.exists():
        return None
    files = [path for path in sessions.rglob("*.jsonl") if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def _read_all_entries(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            parsed = _parse_line(line)
            if parsed is not None:
                entries.append(parsed)
    return entries


def _parse_line(line: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _session_meta(entries: list[dict[str, Any]]) -> dict[str, Any]:
    for entry in entries:
        if entry.get("type") == "session_meta" and isinstance(entry.get("payload"), dict):
            return dict(entry["payload"])
    for entry in entries:
        if entry.get("type") == "turn_context" and isinstance(entry.get("payload"), dict):
            return dict(entry["payload"])
    return {}


def _conversation_id(path: Path, meta: dict[str, Any]) -> str:
    if isinstance(meta.get("id"), str) and meta["id"]:
        return meta["id"]
    match = re.search(r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-([0-9a-f-]+)\.jsonl$", path.name)
    if match:
        return match.group(1)
    return path.stem


def _capture_events(entries: list[dict[str, Any]]):
    for entry in entries:
        timestamp = entry.get("timestamp")
        entry_type = entry.get("type")
        payload = entry.get("payload")
        if entry_type in {
            "retrieval_packet",
            "skill_use",
            "web_fetch",
            "file_changed",
            "file_diff",
            "artifact_created",
            "conclusion",
            "plan_update",
            "agent_delegation",
            "failure",
            "branch_started",
            "branch_completed",
            "conversation_reference",
        }:
            event_payload = payload if isinstance(payload, dict) else {
                k: v for k, v in entry.items() if k not in {"type", "timestamp"}
            }
            event_payload.setdefault("source", "codex_structured_event")
            yield str(entry_type), event_payload, timestamp
            continue
        if not isinstance(payload, dict):
            continue

        if entry_type == "response_item":
            item_type = payload.get("type")
            if item_type == "message":
                role = payload.get("role")
                content = _message_text(payload.get("content"))
                if not content:
                    continue
                if role == "user":
                    yield "user_message", {"content": content, "source": "codex_transcript"}, timestamp
                elif role == "assistant":
                    yield "assistant_message", {"content": content, "source": "codex_transcript"}, timestamp
            elif item_type in {"function_call", "custom_tool_call"}:
                yield "tool_call", _tool_call_payload(payload, source="codex_transcript"), timestamp
            elif item_type in {"function_call_output", "custom_tool_call_output"}:
                yield "tool_result", _tool_result_payload(payload, source="codex_transcript"), timestamp
            continue

        if entry_type == "event_msg":
            event_kind = payload.get("type")
            if event_kind == "agent_message":
                content = payload.get("message")
                if isinstance(content, str) and content.strip():
                    yield "assistant_message", {
                        "source": "codex_event_msg",
                        "event_type": event_kind,
                        "content": content.strip(),
                    }, timestamp
                continue
            if event_kind == "user_message":
                content = payload.get("message")
                if isinstance(content, str) and content.strip():
                    yield "user_message", {
                        "source": "codex_event_msg",
                        "event_type": event_kind,
                        "content": content.strip(),
                    }, timestamp
                continue
            mapped = _event_msg_mapping(str(event_kind or ""))
            if mapped is not None:
                event_payload = _event_msg_payload(payload, source="codex_event_msg", event_type=str(event_kind or ""))
                if mapped == "tool_call":
                    event_payload = _tool_call_payload(payload, source="codex_event_msg")
                if mapped == "tool_result":
                    event_payload = _tool_result_payload(payload, source="codex_event_msg")
                yield mapped, event_payload, timestamp


def _event_msg_mapping(event_kind: str) -> str | None:
    if event_kind in {"exec_command_end", "exec_command_start"}:
        return "tool_result" if event_kind.endswith("_end") else "tool_call"
    if event_kind in {"patch_apply_end", "patch_apply_start"}:
        return "file_diff" if event_kind.endswith("_end") else "tool_call"
    if event_kind in {"web_search_end", "web_open_end", "web_fetch_end"}:
        return "web_fetch"
    if event_kind in {"task_started", "task_complete"}:
        return "conclusion" if event_kind == "task_complete" else "plan_update"
    if event_kind in {"skill_use", "retrieval_packet", "artifact_created", "file_changed"}:
        return event_kind
    return None


def _tool_call_payload(payload: dict[str, Any], *, source: str) -> dict[str, Any]:
    name = payload.get("name") or payload.get("tool_name") or payload.get("call_name") or payload.get("type")
    call_id = payload.get("call_id") or payload.get("id") or payload.get("callId")
    raw_input = payload.get("arguments")
    if raw_input is None:
        raw_input = payload.get("input") if "input" in payload else payload.get("tool_input")
    return {
        "source": source,
        "tool_name": str(name or ""),
        "call_id": str(call_id or ""),
        "input": _jsonish(raw_input),
        "raw_payload": payload,
    }


def _event_msg_payload(payload: dict[str, Any], *, source: str, event_type: str) -> dict[str, Any]:
    normalized = {k: v for k, v in payload.items() if k != "type"}
    normalized["source"] = source
    normalized["event_type"] = event_type
    normalized["raw_payload"] = payload
    return normalized


def _tool_result_payload(payload: dict[str, Any], *, source: str) -> dict[str, Any]:
    name = payload.get("name") or payload.get("tool_name") or payload.get("call_name") or payload.get("type")
    call_id = payload.get("call_id") or payload.get("id") or payload.get("callId")
    output = payload.get("output")
    if output is None:
        output = payload.get("result") if "result" in payload else payload.get("content")
    if output is None and isinstance(payload.get("tool_response"), dict):
        output = payload["tool_response"].get("output") or payload["tool_response"].get("result")
    is_error = bool(payload.get("is_error") or payload.get("error") or payload.get("status") == "error")
    return {
        "source": source,
        "tool_name": str(name or ""),
        "call_id": str(call_id or ""),
        "is_error": is_error,
        "output": _stringify_output(output),
        "raw_payload": payload,
    }


def _jsonish(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
    return value if value is not None else {}


def _stringify_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"input_text", "output_text", "text"} and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(part for part in parts if part).strip()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _log(payload: dict[str, Any]) -> None:
    try:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
