"""E2E test: feedback-type session raw → extraction → promotion → passive feedback.

Scenario: a session raw contains a '## Memory Context' block listing retrieved notes.
The fixture LLM recognizes the block and emits retrieval_attribution in the candidate.
Promotion stores the attribution in the ledger.
derive_passive_feedback() reads it and writes note_passive_feedback rows.
"""
from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from second_brain.config import load_config
from second_brain.db import connect_db, run_migrations
from second_brain.extraction import enqueue_extraction, process_extraction_queue
from second_brain.ingest import ingest_session
from second_brain.maintenance import derive_passive_feedback
from second_brain.promotion.worker import process_promotion_queue


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


def _config(workspace: Path):
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
        provider = "hash"
        model = "hash-128-v1"
        dimensions = 128
        reembed_on_summary_change_only = true

        [graph]
        enabled = false

        [ingest]
        auto_trigger_pipeline = false

        [extraction]
        vlm_provider = "fixture"
        vlm_model = "fixture-vlm"
        llm_provider = "fixture"
        llm_model = "fixture-llm"
        max_queue_batch = 10

        [maintenance]
        passive_feedback_enabled = true
        passive_feedback_inline_only = false
        """,
    )
    config = load_config(config_path, workspace)
    conn = connect_db(config.paths.db_path)
    run_migrations(conn, ROOT / "migrations")
    conn.close()
    return config


# The fixture LLM provider emits retrieval_attribution when the packet contains
# retrieval_context_note_ids. The note_id used in attribution matches the one in
# the Memory Context block below.
_RETRIEVED_NOTE_ID = "concept/mnemonic-retrieval-design"

_FEEDBACK_SESSION_CONTENT = textwrap.dedent(f"""
    ---
    type: session
    session_type: retrieval_feedback
    feedback_target: retrieval_use
    feedback_schema: retrieval_feedback.v1
    retrieved_note_ids:
      - {_RETRIEVED_NOTE_ID}
    expected_feedback:
      - note_id: {_RETRIEVED_NOTE_ID}
        state: passive_used_likely
        evidence_type: semantic_reliance
    ---

    ## Memory Context
    current_hits: {_RETRIEVED_NOTE_ID}
    stale_hits:
    contested_hits:

    ## Retrieval Use Feedback
    Feedback Target: retrieval_use
    Retrieved Notes: {_RETRIEVED_NOTE_ID}
    Observed Use: The agent relied on the retrieved retrieval-design note.
    Expected Label: passive_used_likely

    ## Session Transcript

    User: How does MnemonicOS retrieval work?

    Agent: Based on the retrieved note about retrieval design, MnemonicOS uses
    a multi-stage retrieval pipeline with BM25 and semantic search.
    The retrieved note explained that the system scores notes by trust and freshness.

    The agent used the retrieved knowledge to answer accurately.
    This confirms the retrieval design note was useful and relevant.

    ## Outcome
    The user's question was answered correctly using the retrieved note content.
""").strip()


class PassiveFeedbackE2ETest(unittest.TestCase):
    def test_feedback_session_produces_passive_feedback_row(self) -> None:
        """Full pipeline: ingest → extract → promote → derive_passive_feedback.

        The session raw contains '## Memory Context' listing a retrieved note.
        The fixture LLM detects retrieval_context_note_ids in the packet and emits
        retrieval_attribution. Promotion stores it in the ledger. Maintenance derives
        passive_used_likely for the retrieved note.
        """
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = _config(workspace)

            # Step 1: Ingest feedback-type session
            session = ingest_session(
                config,
                bridge_id="local_cli",
                session_id="feedback-e2e-001",
                content=_FEEDBACK_SESSION_CONTENT,
            )
            self.assertIsNotNone(session.archive_id)

            # Step 2: Enqueue and run extraction
            item = enqueue_extraction(
                config, source_kind="session", archive_id=session.archive_id
            )
            extraction_result = process_extraction_queue(config, dry_run=False)
            self.assertEqual(extraction_result.processed, 1, "Extraction should process 1 item")
            self.assertGreaterEqual(extraction_result.promotion_ready, 1, "Should have promotion-ready candidates")

            # Step 3: Check that retrieval_attribution was emitted by fixture LLM
            conn = connect_db(config.paths.db_path)
            rows = conn.execute(
                """
                SELECT payload_json
                FROM extraction_candidates
                WHERE queue_id = ? AND validation_status = 'promotion_ready'
                ORDER BY candidate_index
                """,
                (item.id,),
            ).fetchall()
            conn.close()

            self.assertGreater(len(rows), 0, "Should have at least one promotion-ready candidate")
            # The fixture provider adds attribution when retrieval_context_note_ids is present.
            # At least one candidate should carry retrieval_attribution.
            payloads = [json.loads(r["payload_json"]) for r in rows]
            attributed = [p for p in payloads if p.get("retrieval_attribution")]
            self.assertGreater(
                len(attributed), 0,
                f"Expected retrieval_attribution in at least one candidate. Payloads: "
                f"{[list(p.keys()) for p in payloads]}"
            )

            first_attr = attributed[0]["retrieval_attribution"]
            attr_note_ids = {entry["note_id"] for entry in first_attr}
            self.assertIn(
                _RETRIEVED_NOTE_ID, attr_note_ids,
                f"Expected {_RETRIEVED_NOTE_ID!r} in attribution. Got: {attr_note_ids}"
            )

            # Step 4: Run promotion
            promo_result = process_promotion_queue(config, dry_run=False)
            self.assertGreater(
                promo_result.promoted + promo_result.review_routed, 0,
                "At least one candidate should be promoted"
            )

            # Step 5: Verify retrieval_attribution_json stored in promotion_decisions
            conn = connect_db(config.paths.db_path)
            pd_rows = conn.execute(
                "SELECT retrieval_attribution_json FROM promotion_decisions WHERE archive_id = ?",
                (session.archive_id,),
            ).fetchall()
            attributed_pd = [
                r for r in pd_rows if r["retrieval_attribution_json"] not in (None, "null", "[]")
            ]
            conn.close()

            self.assertGreater(
                len(attributed_pd), 0,
                "At least one promotion_decisions row should have retrieval_attribution_json"
            )

            # Step 6: Run derive_passive_feedback (inline path).
            # Note: process_promotion_queue already triggers an inline derive_passive_feedback
            # pass immediately after promoting. The explicit call here may be a no-op
            # (rows already written). We verify correctness via the DB, not the result counter.
            conn = connect_db(config.paths.db_path)
            derive_passive_feedback(conn, config, dry_run=False)
            conn.close()

            # Step 7: Verify note_passive_feedback row for the retrieved note
            conn = connect_db(config.paths.db_path)
            feedback_rows = conn.execute(
                """
                SELECT * FROM note_passive_feedback
                WHERE note_id = ?
                """,
                (_RETRIEVED_NOTE_ID,),
            ).fetchall()
            conn.close()

            self.assertGreater(
                len(feedback_rows), 0,
                f"Expected note_passive_feedback row for {_RETRIEVED_NOTE_ID!r}"
            )
            states = {r["feedback_state"] for r in feedback_rows}
            self.assertIn(
                "passive_used_likely", states,
                f"Expected passive_used_likely. Got: {states}"
            )
            methods = {r["method"] for r in feedback_rows}
            self.assertIn(
                "extraction_inline", methods,
                f"Expected extraction_inline method. Got: {methods}"
            )

    def test_session_without_memory_context_produces_no_inline_attribution(self) -> None:
        """Session with no Memory Context block → no retrieval_attribution in candidates."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = _config(workspace)

            plain_session = ingest_session(
                config,
                bridge_id="local_cli",
                session_id="plain-session-001",
                content=textwrap.dedent("""
                    User: What is the capital of France?
                    Agent: Paris.
                    The agent answered correctly from training knowledge.
                """).strip(),
            )
            enqueue_extraction(config, source_kind="session", archive_id=plain_session.archive_id)
            process_extraction_queue(config, dry_run=False)

            conn = connect_db(config.paths.db_path)
            rows = conn.execute(
                "SELECT payload_json FROM extraction_candidates WHERE validation_status = 'promotion_ready'"
            ).fetchall()
            conn.close()

            for row in rows:
                payload = json.loads(row["payload_json"])
                # Without Memory Context, fixture LLM should not add attribution
                attribution = payload.get("retrieval_attribution") or []
                self.assertEqual(
                    attribution, [],
                    f"No attribution expected without Memory Context. Got: {attribution}"
                )


if __name__ == "__main__":
    unittest.main()
