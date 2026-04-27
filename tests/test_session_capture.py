from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from second_brain.config import load_config
from second_brain.db import connect_db
from second_brain.ingest import project_episode_for_promotion
from second_brain.models import AppendEventRequest, BeginEpisodeRequest, EndEpisodeRequest
from second_brain.server import _POST_ROUTES
from second_brain.session_capture import append_event, begin_episode, end_episode


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


def _config(workspace: Path) -> Path:
    config_path = workspace / "config.toml"
    _write(
        config_path,
        f"""
        [paths]
        vault_root = "{(workspace / 'brain').as_posix()}"
        db_path = "{(workspace / 'brain/data/archive.db').as_posix()}"
        vectors_dir = "{(workspace / 'brain/data/vectors').as_posix()}"
        graph_dir = "{(workspace / 'brain/data/graph').as_posix()}"
        eval_queries = "{(workspace / 'evals/queries.jsonl').as_posix()}"
        """,
    )
    return config_path


def _events(path: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


class SessionCaptureTest(unittest.TestCase):
    def test_begin_episode_creates_raw_layout_and_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)

            result = begin_episode(
                config,
                BeginEpisodeRequest(
                    bridge_id="codex",
                    conversation_id="thread/abc",
                    episode_id="visit-1",
                    cwd=str(workspace),
                    model="gpt-test",
                    agent_runtime="codex",
                ),
            )

            self.assertTrue(Path(result.events_path).exists())
            self.assertTrue(Path(result.transcript_path).exists())
            self.assertTrue(Path(result.artifacts_path).is_dir())
            self.assertEqual(result.conversation_id, "thread-abc")
            conversation_manifest = json.loads((Path(result.conversation_path) / "manifest.json").read_text())
            episode_manifest = json.loads((Path(result.episode_path) / "manifest.json").read_text())
            self.assertEqual(conversation_manifest["episode_ids"], ["visit-1"])
            self.assertEqual(episode_manifest["status"], "active")
            self.assertEqual(episode_manifest["event_count"], 1)
            self.assertEqual(_events(result.events_path)[0]["type"], "episode_started")

    def test_append_messages_writes_jsonl_and_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            begin = begin_episode(
                config,
                BeginEpisodeRequest(bridge_id="codex", conversation_id="thread-a", episode_id="episode-a"),
            )

            user = append_event(
                config,
                AppendEventRequest(
                    bridge_id="codex",
                    conversation_id=begin.conversation_id,
                    episode_id=begin.episode_id,
                    event_type="user_message",
                    payload={"content": "Can you preserve this raw turn?"},
                ),
            )
            assistant = append_event(
                config,
                AppendEventRequest(
                    bridge_id="codex",
                    conversation_id=begin.conversation_id,
                    episode_id=begin.episode_id,
                    event_type="assistant_message",
                    payload={"content": "Yes, this is appended."},
                ),
            )

            self.assertEqual(user.seq, 2)
            self.assertEqual(assistant.seq, 3)
            events = _events(begin.events_path)
            self.assertEqual([event["seq"] for event in events], [1, 2, 3])
            transcript = Path(begin.transcript_path).read_text()
            self.assertIn("## User", transcript)
            self.assertIn("Can you preserve this raw turn?", transcript)
            self.assertIn("## Assistant", transcript)
            self.assertIn("Yes, this is appended.", transcript)

    def test_large_payload_is_externalized_to_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            begin = begin_episode(
                config,
                BeginEpisodeRequest(bridge_id="codex", conversation_id="thread-large", episode_id="episode-large"),
            )
            old_threshold = os.environ.get("MNEMOS_ARTIFACT_THRESHOLD_BYTES")
            os.environ["MNEMOS_ARTIFACT_THRESHOLD_BYTES"] = "1024"
            try:
                result = append_event(
                    config,
                    AppendEventRequest(
                        bridge_id="codex",
                        conversation_id=begin.conversation_id,
                        episode_id=begin.episode_id,
                        event_type="tool_result",
                        payload={"call_id": "call-1", "tool_name": "shell", "output": "x" * 4096},
                    ),
                )
            finally:
                if old_threshold is None:
                    os.environ.pop("MNEMOS_ARTIFACT_THRESHOLD_BYTES", None)
                else:
                    os.environ["MNEMOS_ARTIFACT_THRESHOLD_BYTES"] = old_threshold

            self.assertEqual(len(result.artifact_paths), 1)
            artifact_path = Path(result.artifact_paths[0])
            self.assertTrue(artifact_path.exists())
            event = _events(begin.events_path)[-1]
            self.assertTrue(event["payload"]["externalized_payload"])
            self.assertEqual(event["payload"]["artifact"], f"artifacts/{artifact_path.name}")
            manifest = json.loads((Path(begin.episode_path) / "manifest.json").read_text())
            self.assertEqual(manifest["artifact_count"], 1)

    def test_reopening_conversation_creates_new_episode_without_mutating_old_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            first = begin_episode(
                config,
                BeginEpisodeRequest(bridge_id="codex", conversation_id="thread-reopen", episode_id="episode-1"),
            )
            first_events_before = Path(first.events_path).read_text()
            second = begin_episode(
                config,
                BeginEpisodeRequest(bridge_id="codex", conversation_id="thread-reopen", episode_id="episode-2"),
            )

            self.assertEqual(Path(first.events_path).read_text(), first_events_before)
            self.assertNotEqual(first.episode_id, second.episode_id)
            manifest = json.loads((Path(second.conversation_path) / "manifest.json").read_text())
            self.assertEqual(manifest["episode_ids"], ["episode-1", "episode-2"])

    def test_end_episode_records_status_count_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            begin = begin_episode(
                config,
                BeginEpisodeRequest(bridge_id="codex", conversation_id="thread-end", episode_id="episode-end"),
            )
            append_event(
                config,
                AppendEventRequest(
                    bridge_id="codex",
                    conversation_id=begin.conversation_id,
                    episode_id=begin.episode_id,
                    event_type="failure",
                    payload={"message": "example", "recoverable": True},
                ),
            )
            result = end_episode(
                config,
                EndEpisodeRequest(
                    bridge_id="codex",
                    conversation_id=begin.conversation_id,
                    episode_id=begin.episode_id,
                    status="partial",
                ),
            )

            self.assertEqual(result.status, "partial")
            self.assertEqual(result.event_count, 3)
            self.assertEqual(len(result.content_hash), 64)
            manifest = json.loads(Path(result.manifest_path).read_text())
            self.assertEqual(manifest["status"], "partial")
            self.assertEqual(manifest["event_count"], 3)

            db = connect_db(config.paths.db_path)
            try:
                row = db.execute(
                    """
                    SELECT status, event_count, content_hash, projection_status
                    FROM raw_episodes
                    WHERE conversation_id = ? AND episode_id = ?
                    """,
                    (begin.conversation_id, begin.episode_id),
                ).fetchone()
                self.assertEqual(row["status"], "partial")
                self.assertEqual(int(row["event_count"]), 3)
                self.assertEqual(row["content_hash"], result.content_hash)
                self.assertEqual(row["projection_status"], "not_projected")
            finally:
                db.close()

    def test_project_episode_promotes_without_archive_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            begin = begin_episode(
                config,
                BeginEpisodeRequest(bridge_id="codex", conversation_id="thread-promote", episode_id="episode-promote"),
            )
            append_event(
                config,
                AppendEventRequest(
                    bridge_id="codex",
                    conversation_id=begin.conversation_id,
                    episode_id=begin.episode_id,
                    event_type="assistant_message",
                    payload={
                        "content": textwrap.dedent(
                            """
                            ```memory
                            - type: concept
                              title: Captured episode projection works
                              confidence: high
                              summary: Captured raw episodes can promote from in-memory projection.
                            ```
                            """
                        ).strip()
                    },
                ),
            )
            end_episode(
                config,
                EndEpisodeRequest(
                    bridge_id="codex",
                    conversation_id=begin.conversation_id,
                    episode_id=begin.episode_id,
                    status="success",
                ),
            )

            result = project_episode_for_promotion(
                config,
                conversation_id=begin.conversation_id,
                episode_id=begin.episode_id,
            )

            self.assertEqual(result.archive_id, "episode/thread-promote/episode-promote")
            self.assertEqual(result.chunk_count, 0)
            self.assertIn("concept/captured-episode-projection-works", result.promoted_notes)
            db = connect_db(config.paths.db_path)
            try:
                archive_row = db.execute("SELECT id FROM archive WHERE id = ?", (result.archive_id,)).fetchone()
                self.assertIsNone(archive_row)
                chunks = db.execute("SELECT COUNT(*) AS n FROM archive_chunks WHERE archive_id = ?", (result.archive_id,)).fetchone()
                self.assertEqual(int(chunks["n"]), 0)
                episode = db.execute(
                    """
                    SELECT projection_status, promoted_count, review_count
                    FROM raw_episodes
                    WHERE conversation_id = ? AND episode_id = ?
                    """,
                    (begin.conversation_id, begin.episode_id),
                ).fetchone()
                self.assertEqual(episode["projection_status"], "projected")
                self.assertEqual(int(episode["promoted_count"]), 1)
                self.assertEqual(int(episode["review_count"]), 0)
            finally:
                db.close()

    def test_cli_capture_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config_path = _config(workspace)
            begin = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "second_brain",
                    "--config",
                    str(config_path),
                    "capture",
                    "begin-episode",
                    "--bridge-id",
                    "codex",
                    "--conversation-id",
                    "cli-thread",
                    "--episode-id",
                    "cli-episode",
                    "--json",
                ],
                cwd=ROOT,
                env={**os.environ, "PYTHONPATH": str(SRC)},
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(begin.returncode, 0, begin.stderr)
            payload = json.loads(begin.stdout)
            append = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "second_brain",
                    "--config",
                    str(config_path),
                    "capture",
                    "append-event",
                    "--bridge-id",
                    "codex",
                    "--conversation-id",
                    payload["conversation_id"],
                    "--episode-id",
                    payload["episode_id"],
                    "--type",
                    "user_message",
                    "--content",
                    "hello from cli",
                    "--json",
                ],
                cwd=ROOT,
                env={**os.environ, "PYTHONPATH": str(SRC)},
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(append.returncode, 0, append.stderr)
            self.assertEqual(json.loads(append.stdout)["seq"], 2)
            self.assertIn("hello from cli", Path(payload["transcript_path"]).read_text())

    def test_http_routes_are_registered(self) -> None:
        self.assertIn("/v1/capture/begin-episode", _POST_ROUTES)
        self.assertIn("/v1/capture/append-event", _POST_ROUTES)
        self.assertIn("/v1/capture/end-episode", _POST_ROUTES)
        self.assertIn("/v1/capture/project-episode", _POST_ROUTES)


if __name__ == "__main__":
    unittest.main()
