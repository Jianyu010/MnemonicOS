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

from second_brain.bridge import decide_upsert, parse_summary
from second_brain.config import load_config
from second_brain.db import connect_db, run_migrations
from second_brain.ingest import ingest_session
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

            ingest_session(config, bridge_id="automation", session_id="first-cann", content=textwrap.dedent(first_summary).strip() + "\n")
            ingest_session(config, bridge_id="automation", session_id="second-cann", content=textwrap.dedent(second_summary).strip() + "\n")

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


if __name__ == "__main__":
    unittest.main()
