from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from second_brain.config import load_config
from second_brain.db import connect_db, run_migrations
from second_brain.models import DecideWritebackRequest, RecordOutcomeRequest
from second_brain.runtime import decide_writeback_runtime, record_outcome
from second_brain.sync import sync_vault


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


class RuntimeWritebackTest(unittest.TestCase):
    def _prepare_workspace(self, workspace: Path) -> object:
        _write(workspace / "brain/system/MEMORY.md", "# MEMORY\n")
        _write(workspace / "brain/system/USER.md", "# USER\n")
        _write(workspace / "brain/system/ACTIVE.md", "# ACTIVE\n")
        _write(workspace / "brain/system/AGENTS.md", "bridges: {}\n")
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

            [embeddings]
            enabled = false

            [graph]
            enabled = false
            """,
        )
        config = load_config(config_path, workspace)
        connection = connect_db(config.paths.db_path)
        run_migrations(connection, ROOT / "migrations")
        connection.close()
        return config

    def test_record_outcome_persists_task_outcome_and_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = self._prepare_workspace(workspace)
            _write(
                workspace / "brain/wiki/concepts/cann-npu.md",
                """
                ---
                id: concept/cann-npu
                type: concept
                title: CANN NPU
                updated_at: 2026-04-13
                created_at: 2026-04-13
                source_refs: [session/2026-04-13-0900]
                confidence: high
                ---

                CANN is Huawei's software stack for Ascend NPUs.
                """,
            )
            sync_vault(config, mode="full")
            connection = connect_db(config.paths.db_path)
            with connection:
                cursor = connection.execute(
                    """
                    INSERT INTO retrieval_queries(query, query_type, classifier_confidence, retrieved, top_k)
                    VALUES ('what is cann npu', 'knowledge', 1.0, ?, 3)
                    """,
                    (json.dumps(["concept/cann-npu"]),),
                )
                query_id = int(cursor.lastrowid)
                connection.execute(
                    """
                    INSERT INTO retrieval_hits(query_id, note_id, rank, channel, raw_score, final_score, selected)
                    VALUES (?, 'concept/cann-npu', 1, 'bm25', 0.9, 0.95, 1)
                    """,
                    (query_id,),
                )
            connection.close()

            result = record_outcome(
                config,
                RecordOutcomeRequest(
                    bridge_id="api-client",
                    session_id="session-6",
                    task_id="task-6",
                    outcome_type="task_result",
                    status="success",
                    summary="The answer reused existing memory successfully.",
                    query_id=query_id,
                    task_kind="knowledge",
                    retrieval_state="sufficient_current",
                    recommendation="reuse",
                    useful_ids=["concept/cann-npu"],
                ),
            )

            self.assertEqual(result.status, "accepted")
            self.assertEqual(result.candidate_note_ids, ["concept/cann-npu"])

            connection = connect_db(config.paths.db_path)
            outcome_row = connection.execute(
                "SELECT status, useful_ids FROM task_outcomes WHERE task_id = 'task-6'"
            ).fetchone()
            query_row = connection.execute(
                "SELECT useful, top1_correct FROM retrieval_queries WHERE id = ?",
                (query_id,),
            ).fetchone()
            hit_row = connection.execute(
                "SELECT useful FROM retrieval_hits WHERE query_id = ? AND note_id = 'concept/cann-npu'",
                (query_id,),
            ).fetchone()
            connection.close()

            self.assertEqual(str(outcome_row["status"]), "success")
            self.assertEqual(json.loads(str(outcome_row["useful_ids"])), ["concept/cann-npu"])
            self.assertEqual(int(query_row["useful"]), 1)
            self.assertEqual(int(query_row["top1_correct"]), 1)
            self.assertEqual(int(hit_row["useful"]), 1)

    def test_decide_writeback_runtime_prefers_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = self._prepare_workspace(workspace)
            _write(
                workspace / "brain/wiki/concepts/cann-npu.md",
                """
                ---
                id: concept/cann-npu
                type: concept
                title: CANN NPU
                updated_at: 2026-04-13
                created_at: 2026-04-13
                source_refs: [session/2026-04-13-0900]
                confidence: high
                ---

                CANN is Huawei's software stack for Ascend NPUs.
                """,
            )
            sync_vault(config, mode="full")

            result = decide_writeback_runtime(
                config,
                DecideWritebackRequest(
                    bridge_id="api-client",
                    session_id="session-7",
                    task_id="task-7",
                    note_type="concept",
                    title="CANN NPU",
                    summary_text="Updated explanation.",
                    task_kind="knowledge",
                    retrieval_state="insufficient_stale",
                    existing_note_candidates=["concept/cann-npu"],
                ),
            )

            self.assertEqual(result.decision, "update")
            self.assertEqual(result.targets, ["concept/cann-npu"])
            self.assertIn("concept/cann-npu", result.candidates)


if __name__ == "__main__":
    unittest.main()
