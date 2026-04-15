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
from second_brain.ingest import ingest_session
from second_brain.jobs import run_job
from second_brain.sync import sync_vault


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


def _ingest_and_process(config, *, bridge_id: str, session_id: str, content: str):
    result = ingest_session(config, bridge_id=bridge_id, session_id=session_id, content=content)
    queue_run = run_job(config, job_name="process_promotion_queue", dry_run=False)
    return result, queue_run


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

    def test_repeated_knowledge_ingest_confirms_existing_instead_of_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config_path = self._prepare_workspace(workspace)
            config = load_config(config_path, workspace)

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            first_summary = """
            bridge_id: automation
            session_date: 2026-04-11
            task_kind: knowledge
            mnemos_retrieval_state: insufficient_no_hit
            writeback_intent: new

            ## Task
            Explain CANN NPU

            ## Goal
            Store knowledge about CANN NPU.

            ## Anchors
            - CANN NPU

            ## Reusable Takeaways
            - CANN is Huawei's software stack for Ascend NPUs.

            ## Suggested Memory Items
            - concept

            ```memory
            items:
              - type: concept
                title: "CANN NPU"
                body: "CANN is Huawei's software stack for Ascend NPUs."
                confidence: high
                tags: [npu, cann]
            ```
            """
            second_summary = """
            bridge_id: automation
            session_date: 2026-04-11
            task_kind: knowledge
            mnemos_retrieval_state: sufficient_current
            writeback_intent: new

            ## Task
            Explain CANN NPU again

            ## Goal
            Re-answer what CANN NPU is.

            ## Anchors
            - CANN NPU

            ## MnemonicOS Hits
            - concept/cann-npu

            ## Reusable Takeaways
            - CANN is Huawei's software stack for Ascend NPUs.

            ## Suggested Memory Items
            - concept

            ```memory
            items:
              - type: concept
                title: "CANN NPU"
                body: "CANN is Huawei's software stack for Ascend NPUs."
                confidence: high
                tags: [npu, cann]
            ```
            """

            _ingest_and_process(
                config,
                bridge_id="automation",
                session_id="first-cann",
                content=textwrap.dedent(first_summary).strip() + "\n",
            )
            _ingest_and_process(
                config,
                bridge_id="automation",
                session_id="second-cann",
                content=textwrap.dedent(second_summary).strip() + "\n",
            )

            sync_vault(config, mode="full")
            concept_files = list((workspace / "brain/wiki/concepts").glob("*.md"))
            self.assertEqual(len(concept_files), 1)
            concept_text = concept_files[0].read_text()
            self.assertIn("last_observed_at", concept_text)
            self.assertIn("session/", concept_text)

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

    def test_ingest_session_applies_inferred_chain_only_to_problem_solving_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config_path = self._prepare_workspace(workspace)
            config = load_config(config_path, workspace)

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            debugging_summary = """
            task_kind: debugging

            ## Task
            Recover auth rollout after failed migration

            ## Failures
            - Initial rollout retry failed.

            ## Reusable Takeaways
            - Reconcile schema state before rerunning deploy.

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
            _ingest_and_process(
                config,
                bridge_id="automation",
                session_id="session-debug",
                content=textwrap.dedent(debugging_summary).strip() + "\n",
            )

            sync_vault(config, mode="full")
            incident_text = (workspace / "brain/wiki/incidents/auth-rollout-failed-on-schema-drift.md").read_text()
            procedure_paths = list((workspace / "brain/wiki/procedures").rglob("recover-auth-rollout-after-schema-drift.md"))
            self.assertTrue(procedure_paths)
            procedure_text = procedure_paths[0].read_text()
            self.assertIn("chain_id:", incident_text)
            self.assertIn("chain_id:", procedure_text)
            self.assertIn("chain_confidence: medium", incident_text)

            knowledge_summary = """
            task_kind: knowledge

            ## Task
            Explain CANN NPU

            ```memory
            items:
              - type: concept
                title: "CANN NPU"
                body: "CANN is Huawei's software stack for Ascend NPUs."
                confidence: high
            ```
            """
            _ingest_and_process(
                config,
                bridge_id="automation",
                session_id="session-knowledge",
                content=textwrap.dedent(knowledge_summary).strip() + "\n",
            )
            sync_vault(config, mode="full")
            concept_text = (workspace / "brain/wiki/concepts/cann-npu.md").read_text()
            self.assertNotIn("chain_id:", concept_text)

    def test_bounded_chain_promotion_limits_duplicate_core_types_and_prefers_same_chain_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config_path = self._prepare_workspace(workspace)
            config = load_config(config_path, workspace)

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            first_summary = """
            task_kind: debugging
            task_id: auth-recovery
            chain_id: trace/auth-recovery
            chain_confidence: high
            chain_question: How do we recover the auth rollout?
            chain_summary: Use schema reconciliation before retrying deploy.

            ## Task
            Recover auth rollout

            ```memory
            items:
              - type: incident
                title: "Auth rollout failed on schema drift"
                body: "Initial retry failed before schema reconciliation."
                confidence: high
              - type: procedure
                title: "Recover auth rollout"
                body: "Reconcile schema state before rerunning deploy."
                confidence: high
            ```
            """
            _ingest_and_process(
                config,
                bridge_id="automation",
                session_id="session-first",
                content=textwrap.dedent(first_summary).strip() + "\n",
            )
            sync_vault(config, mode="full")

            second_summary = """
            task_kind: debugging
            task_id: auth-recovery
            chain_id: trace/auth-recovery
            chain_confidence: high
            chain_question: How do we recover the auth rollout?
            chain_summary: Use schema reconciliation before retrying deploy.

            ## Task
            Recover auth rollout

            ```memory
            items:
              - type: incident
                title: "Auth rollout retry still failed"
                body: "Another failed retry before verifying schema state."
                confidence: high
              - type: incident
                title: "Auth rollout failed after stale migration cache"
                body: "A second incident candidate from the same chain."
                confidence: high
            ```
            """
            result, queue_run = _ingest_and_process(
                config,
                bridge_id="automation",
                session_id="session-second",
                content=textwrap.dedent(second_summary).strip() + "\n",
            )
            sync_vault(config, mode="full")

            incident_files = list((workspace / "brain/wiki/incidents").glob("*.md"))
            self.assertEqual(len(incident_files), 1)
            incident_text = incident_files[0].read_text()
            self.assertIn("chain_id: trace/auth-recovery", incident_text)
            self.assertIn("Another failed retry", incident_text)
            self.assertEqual(result.review_items, [])
            self.assertTrue(any(review_id.startswith("review/") for review_id in queue_run.review_items_created))
            connection = connect_db(config.paths.db_path)
            metrics_row = connection.execute(
                """
                SELECT chain_extraction, promoted_count, review_count, new_count, update_count, note_delta
                FROM session_metrics
                WHERE session_id = 'session-second'
                """
            ).fetchone()
            connection.close()
            self.assertEqual(str(metrics_row["chain_extraction"]), "explicit_chain")
            self.assertEqual(int(metrics_row["promoted_count"]), 1)
            self.assertEqual(int(metrics_row["review_count"]), 1)
            self.assertEqual(int(metrics_row["new_count"]), 0)
            self.assertEqual(int(metrics_row["update_count"]), 1)
            self.assertEqual(int(metrics_row["note_delta"]), 0)


if __name__ == "__main__":
    unittest.main()
