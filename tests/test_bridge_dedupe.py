from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from second_brain.bridge import decide_upsert, infer_chain_context, parse_summary
from second_brain.config import load_config
from second_brain.db import connect_db, run_migrations
from second_brain.sync import sync_vault


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")



class BridgeDedupeTest(unittest.TestCase):
    def _prepare_workspace(self, workspace: Path) -> Path:
        _write(workspace / "brain/system/MEMORY.md", "# MEMORY\n")
        _write(workspace / "brain/system/USER.md", "# USER\n")
        _write(workspace / "brain/system/ACTIVE.md", "# ACTIVE\n")
        _write(
            workspace / "brain/system/AGENTS.md",
            """
            bridges:
              local_cli:
                scope: [raw/sessions, wiki]
                can_promote: false
                can_write_canonical: true
            """,
        )
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
        return config_path

    def test_stale_knowledge_prefers_update_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config_path = self._prepare_workspace(workspace)
            config = load_config(config_path, workspace)

            _write(
                workspace / "brain/wiki/concepts/cann-npu.md",
                """
                ---
                id: concept/cann-npu
                type: concept
                title: CANN NPU
                aliases: []
                tags: [npu, cann]
                source_refs: [session/2026-04-10-0900]
                confidence: high
                updated_at: '2026-04-10'
                created_at: '2026-04-10'
                ---

                CANN is Huawei's software stack for Ascend NPUs.
                """,
            )

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()
            sync_vault(config, mode="full")

            summary = parse_summary(
                textwrap.dedent(
                    """
                    bridge_id: local_cli
                    session_date: 2026-04-11
                    task_kind: knowledge
                    mnemos_retrieval_state: insufficient_stale

                    ## Task
                    Refresh CANN NPU

                    ## Anchors
                    - CANN NPU

                    ## Reusable Takeaways
                    - Updated with fresher details.

                    ```memory
                    items:
                      - type: concept
                        title: "CANN NPU"
                        body: "Updated explanation."
                        confidence: high
                    ```
                    """
                ).strip()
            )

            connection = connect_db(config.paths.db_path)
            decision = decide_upsert(
                connection,
                config,
                summary=summary,
                note_type="concept",
                title="CANN NPU",
                summary_text="Updated explanation.",
            )
            connection.close()
            self.assertEqual(decision.decision, "update_existing")
            self.assertEqual(decision.targets, ["concept/cann-npu"])

    def test_infer_chain_context_supports_explicit_inferred_and_no_chain_modes(self) -> None:
        explicit = parse_summary(
            textwrap.dedent(
                """
                task_kind: debugging
                chain_id: trace/auth-debug
                chain_confidence: high
                chain_question: Why is auth deploy failing?
                chain_summary: Recovered by reconciling schema state.
                problem_class: auth-deploy

                ## Task
                Debug auth deploy
                """
            ).strip()
        )
        explicit_context = infer_chain_context(explicit, session_id="session-explicit", task_id="task-explicit")
        self.assertEqual(explicit_context["chain_extraction"], "explicit_chain")
        self.assertEqual(explicit_context["chain_id"], "trace/auth-debug")

        inferred = parse_summary(
            textwrap.dedent(
                """
                task_kind: debugging

                ## Task
                Debug auth rollout after failed migration

                ## Failures
                - Retry deploy failed again before schema reconciliation.

                ## Reusable Takeaways
                - Reconcile schema state before retrying deploy.

                ```memory
                items:
                  - type: incident
                    title: "Auth rollout failed on schema drift"
                    confidence: high
                  - type: procedure
                    title: "Recover auth rollout after schema drift"
                    confidence: high
                ```
                """
            ).strip()
        )
        inferred_context = infer_chain_context(inferred, session_id="session-inferred", task_id="task-inferred")
        self.assertEqual(inferred_context["chain_extraction"], "inferred_chain")
        self.assertTrue(inferred_context["chain_id"].startswith("trace/"))
        self.assertEqual(inferred_context["chain_confidence"], "medium")

        flat = parse_summary(
            textwrap.dedent(
                """
                task_kind: knowledge

                ## Task
                Explain CANN NPU
                """
            ).strip()
        )
        flat_context = infer_chain_context(flat, session_id="session-flat", task_id="task-flat")
        self.assertEqual(flat_context["chain_extraction"], "no_chain")

if __name__ == "__main__":
    unittest.main()
