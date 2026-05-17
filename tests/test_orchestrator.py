"""
Orchestrator integration tests — Phase 1 shared commonplace.

Tests are organised into six groups:
  1. TestDefaultInference         — unit tests for _infer_* helpers
  2. TestContextMarkdownFormat    — unit tests for context_markdown output shape
  3. TestBeginTask                — begin_task integration (Python layer)
  4. TestMarkFailure              — mark_failure integration (Python layer)
  5. TestFinishTask               — finish_task integration (Python layer)
  6. TestFullOrchestrationLoop    — end-to-end begin → mark_failure → finish
  7. TestCLIOrchestrator          — CLI transport layer
  8. TestHTTPServer               — HTTP transport layer (parity with Python)
"""

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from second_brain.config import load_config
from second_brain.db import connect_db, run_migrations
from second_brain.orchestrator import (
    OrchestratorFailureRequest,
    OrchestratorOutcomeRequest,
    OrchestratorTaskRequest,
    _infer_task_kind,
    _infer_temporality,
    _infer_token_budget,
    begin_task,
    finish_task,
    mark_failure,
)
from second_brain.sync import sync_vault


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


def _prepare_workspace(workspace: Path):
    """Create a minimal MnemonicOS workspace and return (config_path, config)."""
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

        [retrieval]
            min_relevance_score = 0.0

        [embeddings]
        enabled = false

        [graph]
        enabled = false
        """,
    )
    config = load_config(config_path, workspace)
    conn = connect_db(config.paths.db_path)
    run_migrations(conn, ROOT / "migrations")
    conn.close()
    return config_path, config


def _write_procedure(workspace: Path, note_id: str, title: str, body: str, aliases: list[str] | None = None) -> None:
    """Write a procedure note with well-formed YAML frontmatter."""
    slug = note_id.split("/")[-1]
    alias_list = aliases or [title]
    # Build frontmatter as explicit lines to avoid textwrap.dedent indentation issues.
    frontmatter_lines = [
        "---",
        f"id: {note_id}",
        "type: procedure",
        f"title: {title}",
        f"aliases: {json.dumps(alias_list)}",
        "status: active",
        "tags: [test]",
        "updated_at: 2026-04-15",
        "created_at: 2026-04-15",
        "source_refs: [session/test]",
        "confidence: high",
        "---",
        "",
        body,
        "",
    ]
    content = "\n".join(frontmatter_lines)
    target = workspace / f"brain/wiki/procedures/active/{slug}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def _write_concept(workspace: Path, note_id: str, title: str, body: str) -> None:
    """Write a concept note with well-formed YAML frontmatter."""
    slug = note_id.split("/")[-1]
    frontmatter_lines = [
        "---",
        f"id: {note_id}",
        "type: concept",
        f"title: {title}",
        f'aliases: ["{title}"]',
        "status: active",
        "tags: [test]",
        "updated_at: 2026-04-15",
        "created_at: 2026-04-15",
        "source_refs: [session/test]",
        "confidence: high",
        "---",
        "",
        body,
        "",
    ]
    content = "\n".join(frontmatter_lines)
    target = workspace / f"brain/wiki/concepts/{slug}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


# ---------------------------------------------------------------------------
# 1. TestDefaultInference
# ---------------------------------------------------------------------------

class TestDefaultInference(unittest.TestCase):

    # task_kind inference

    def test_infer_task_kind_debugging(self) -> None:
        self.assertEqual(_infer_task_kind("fix this traceback error"), "debugging")

    def test_infer_task_kind_debugging_from_bug(self) -> None:
        self.assertEqual(_infer_task_kind("there is a bug in auth"), "debugging")

    def test_infer_task_kind_coding(self) -> None:
        self.assertEqual(_infer_task_kind("implement the new login flow"), "coding")

    def test_infer_task_kind_coding_from_build(self) -> None:
        self.assertEqual(_infer_task_kind("build a cache layer"), "coding")

    def test_infer_task_kind_architecture(self) -> None:
        self.assertEqual(_infer_task_kind("design the microservice schema"), "architecture")

    def test_infer_task_kind_research(self) -> None:
        self.assertEqual(_infer_task_kind("explain how redis expires keys"), "research")

    def test_infer_task_kind_research_what_is(self) -> None:
        self.assertEqual(_infer_task_kind("what is the jwt rotation strategy"), "research")

    def test_infer_task_kind_general_fallback(self) -> None:
        # A prompt that matches none of the keyword sets
        self.assertEqual(_infer_task_kind("summarise the sprint"), "general")

    def test_infer_task_kind_debugging_takes_priority_over_coding(self) -> None:
        # "fix" is a debugging keyword and "implement" is a coding keyword;
        # debugging should win because it is checked first.
        self.assertEqual(_infer_task_kind("fix and implement the new flow"), "debugging")

    # temporality inference

    def test_infer_temporality_current_for_coding(self) -> None:
        self.assertEqual(_infer_temporality("coding"), "current")

    def test_infer_temporality_current_for_debugging(self) -> None:
        self.assertEqual(_infer_temporality("debugging"), "current")

    def test_infer_temporality_current_for_testing(self) -> None:
        self.assertEqual(_infer_temporality("testing"), "current")

    def test_infer_temporality_mixed_for_research(self) -> None:
        self.assertEqual(_infer_temporality("research"), "mixed")

    def test_infer_temporality_mixed_for_architecture(self) -> None:
        self.assertEqual(_infer_temporality("architecture"), "mixed")

    def test_infer_temporality_mixed_for_general(self) -> None:
        self.assertEqual(_infer_temporality("general"), "mixed")

    # token_budget inference

    def test_infer_token_budget_uses_explicit_value(self) -> None:
        self.assertEqual(_infer_token_budget("coding", 1234), 1234)

    def test_infer_token_budget_large_for_research(self) -> None:
        budget = _infer_token_budget("research", None)
        self.assertGreaterEqual(budget, 4000)

    def test_infer_token_budget_large_for_architecture(self) -> None:
        budget = _infer_token_budget("architecture", None)
        self.assertGreaterEqual(budget, 4000)

    def test_infer_token_budget_default_for_coding(self) -> None:
        budget = _infer_token_budget("coding", None)
        self.assertGreater(budget, 0)
        self.assertLess(budget, 4000)  # coding uses the moderate default

    def test_infer_token_budget_ignores_zero_explicit(self) -> None:
        # explicit=0 is treated as "not set"
        budget = _infer_token_budget("coding", 0)
        self.assertGreater(budget, 0)


# ---------------------------------------------------------------------------
# 2. TestContextMarkdownFormat
# ---------------------------------------------------------------------------

class TestContextMarkdownFormat(unittest.TestCase):

    def test_markdown_contains_retrieval_state_and_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            _write_procedure(workspace, "procedure/deploy", "Deploy Service", "Deploy steps.")
            sync_vault(config, mode="full")

            result = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="how do we deploy service",
                ),
            )

            md = result.context_markdown
            self.assertIn("## Memory Context", md)
            self.assertIn("retrieval_state:", md)
            self.assertIn("recommendation:", md)
            self.assertIn("task_id:", md)

    def test_markdown_includes_primary_memory_titles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            _write_procedure(workspace, "procedure/deploy", "Deploy Service", "Deploy steps.")
            sync_vault(config, mode="full")

            result = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="deploy service",
                ),
            )

            self.assertIn("Deploy Service", result.context_markdown)

    def test_markdown_no_notes_message_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            # No notes synced — retrieval returns nothing.

            result = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="xyz completely unknown topic abc123",
                ),
            )

            self.assertIn("No relevant MnemonicOS notes found", result.context_markdown)

    def test_markdown_includes_warnings_for_stale_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            _write_concept(workspace, "concept/old-api", "Old API Design", "Legacy API design.")
            sync_vault(config, mode="full")
            conn = connect_db(config.paths.db_path)
            with conn:
                conn.execute(
                    "INSERT INTO note_freshness(note_id, staleness_score, freshness_state) "
                    "VALUES ('concept/old-api', 0.9, 'stale')"
                )
            conn.close()

            result = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="old API design",
                ),
            )

            self.assertIn("### Warnings", result.context_markdown)

    def test_markdown_has_orchestrator_comment_delimiters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            result = begin_task(
                config,
                OrchestratorTaskRequest(bridge_id="test", task_text="anything"),
            )

            self.assertIn("<!-- MnemonicOS orchestrator context", result.context_markdown)
            self.assertIn("<!-- end MnemonicOS orchestrator context -->", result.context_markdown)


# ---------------------------------------------------------------------------
# 3. TestBeginTask
# ---------------------------------------------------------------------------

class TestBeginTask(unittest.TestCase):

    def test_begin_task_returns_reuse_for_current_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            _write_procedure(workspace, "procedure/auth-deploy", "Auth Deploy", "Deploy auth service.")
            sync_vault(config, mode="full")

            result = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="how do we deploy auth service",
                    task_kind="coding",
                    temporality="current",
                ),
            )

            self.assertEqual(result.retrieval_state, "sufficient_current")
            self.assertEqual(result.recommendation, "reuse")
            self.assertGreater(len(result.memory_packet.primary_memories), 0)
            self.assertEqual(result.memory_packet.primary_memories[0].id, "procedure/auth-deploy")

    def test_begin_task_infers_task_kind_from_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            result = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="debug the traceback in auth",
                ),
            )

            self.assertEqual(result.task_kind, "debugging")

    def test_begin_task_infers_temporality_from_task_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            result = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="debug this issue",
                ),
            )

            # debugging → "current"
            self.assertEqual(result.temporality, "current")

    def test_begin_task_research_task_gets_mixed_temporality(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            result = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="explain how redis expires keys",
                ),
            )

            self.assertEqual(result.temporality, "mixed")

    def test_begin_task_autogenerates_task_id_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            result = begin_task(
                config,
                OrchestratorTaskRequest(bridge_id="test", task_text="anything"),
            )

            self.assertTrue(result.task_id.startswith("task-"))
            self.assertGreater(len(result.task_id), 5)

    def test_begin_task_autogenerates_session_id_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            result = begin_task(
                config,
                OrchestratorTaskRequest(bridge_id="test", task_text="anything"),
            )

            self.assertTrue(result.session_id.startswith("session-"))

    def test_begin_task_preserves_provided_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            result = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="anything",
                    task_id="my-fixed-task-id",
                    session_id="my-fixed-session",
                ),
            )

            self.assertEqual(result.task_id, "my-fixed-task-id")
            self.assertEqual(result.session_id, "my-fixed-session")

    def test_begin_task_no_hit_returns_research_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            result = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="zzz unknown irrelevant xyzzy123",
                ),
            )

            self.assertEqual(result.retrieval_state, "insufficient_no_hit")
            self.assertEqual(result.recommendation, "research_fresh")
            self.assertEqual(result.memory_packet.primary_memories, [])

    def test_begin_task_stale_memory_surfaces_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            _write_concept(workspace, "concept/legacy-api", "Legacy API", "Old API details.")
            sync_vault(config, mode="full")
            conn = connect_db(config.paths.db_path)
            with conn:
                conn.execute(
                    "INSERT INTO note_freshness(note_id, staleness_score, freshness_state) "
                    "VALUES ('concept/legacy-api', 0.95, 'stale')"
                )
            conn.close()

            result = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="legacy API",
                    temporality="current",
                ),
            )

            self.assertGreater(len(result.memory_packet.warnings), 0)

    def test_begin_task_records_metrics_in_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            _write_procedure(workspace, "procedure/test-proc", "Test Proc", "Test procedure.")
            sync_vault(config, mode="full")

            result = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="test proc",
                    task_id="metrics-task",
                    session_id="metrics-session",
                ),
            )

            conn = connect_db(config.paths.db_path)
            row = conn.execute(
                "SELECT task_id, session_id, task_kind, retrieval_state, primary_memory_count "
                "FROM runtime_packet_metrics WHERE query_id = ?",
                (result.query_id,),
            ).fetchone()
            conn.close()
            self.assertIsNotNone(row)
            self.assertEqual(str(row["task_id"]), "metrics-task")
            self.assertEqual(str(row["session_id"]), "metrics-session")

    def test_begin_task_two_calls_produce_independent_task_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            r1 = begin_task(config, OrchestratorTaskRequest(bridge_id="t", task_text="a"))
            r2 = begin_task(config, OrchestratorTaskRequest(bridge_id="t", task_text="b"))
            self.assertNotEqual(r1.task_id, r2.task_id)


# ---------------------------------------------------------------------------
# 4. TestMarkFailure
# ---------------------------------------------------------------------------

class TestMarkFailure(unittest.TestCase):

    def test_mark_failure_preserves_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            r1 = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="implement auth service",
                    task_id="stable-task-001",
                    session_id="stable-session-001",
                ),
            )

            r2 = mark_failure(
                config,
                OrchestratorFailureRequest(
                    bridge_id="test",
                    session_id="stable-session-001",
                    task_id="stable-task-001",
                    failure_text="ModuleNotFoundError: no module named auth_service",
                    task_kind="coding",
                ),
            )

            self.assertEqual(r2.task_id, "stable-task-001")
            self.assertEqual(r2.session_id, "stable-session-001")

    def test_mark_failure_records_intermediate_outcome_in_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            mark_failure(
                config,
                OrchestratorFailureRequest(
                    bridge_id="test",
                    session_id="session-fail-record",
                    task_id="task-fail-record",
                    failure_text="AttributeError on deploy",
                    task_kind="debugging",
                ),
            )

            conn = connect_db(config.paths.db_path)
            row = conn.execute(
                "SELECT task_id, status, outcome_type FROM task_outcomes WHERE task_id = ?",
                ("task-fail-record",),
            ).fetchone()
            conn.close()
            self.assertIsNotNone(row)
            self.assertEqual(str(row["status"]), "failure")
            self.assertEqual(str(row["outcome_type"]), "intermediate_failure")

    def test_mark_failure_returns_context_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            _write_procedure(workspace, "procedure/rollback", "Rollback Procedure", "How to rollback.")
            sync_vault(config, mode="full")

            result = mark_failure(
                config,
                OrchestratorFailureRequest(
                    bridge_id="test",
                    session_id="session-mf",
                    task_id="task-mf",
                    failure_text="rollback failed on migration step",
                    task_kind="debugging",
                ),
            )

            # Must look like a begin_task result
            self.assertIn(result.retrieval_state, {
                "sufficient_current", "sufficient_historical", "partial",
                "insufficient_no_hit", "insufficient_stale", "insufficient_contested",
            })
            self.assertIn("## Memory Context", result.context_markdown)

    def test_mark_failure_re_resolution_focuses_on_failure_text(self) -> None:
        """The refreshed context query is the failure text, not the original task text."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            _write_procedure(workspace, "procedure/db-rollback", "DB Rollback", "Database rollback steps.")
            sync_vault(config, mode="full")

            result = mark_failure(
                config,
                OrchestratorFailureRequest(
                    bridge_id="test",
                    session_id="session-focus",
                    task_id="task-focus",
                    failure_text="database rollback failed on migration",
                    task_kind="debugging",
                ),
            )

            # The primary memory should relate to the failure_text (db rollback),
            # not some unrelated original task.
            if result.memory_packet.primary_memories:
                self.assertIn(
                    "rollback",
                    result.memory_packet.primary_memories[0].title.lower(),
                )


# ---------------------------------------------------------------------------
# 5. TestFinishTask
# ---------------------------------------------------------------------------

class TestFinishTask(unittest.TestCase):

    def test_finish_task_records_final_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            result = finish_task(
                config,
                OrchestratorOutcomeRequest(
                    bridge_id="test",
                    session_id="session-ft",
                    task_id="task-ft-001",
                    status="success",
                    outcome_type="coding",
                    summary="Implemented the auth service deploy procedure.",
                ),
            )

            self.assertEqual(result.outcome_status, "accepted")
            conn = connect_db(config.paths.db_path)
            row = conn.execute(
                "SELECT status, outcome_type FROM task_outcomes WHERE task_id = ?",
                ("task-ft-001",),
            ).fetchone()
            conn.close()
            self.assertIsNotNone(row)
            self.assertEqual(str(row["status"]), "success")

    def test_finish_task_returns_writeback_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            result = finish_task(
                config,
                OrchestratorOutcomeRequest(
                    bridge_id="test",
                    session_id="session-wb",
                    task_id="task-wb-001",
                    status="success",
                    outcome_type="research",
                    summary="Researched redis expiry semantics.",
                ),
            )

            self.assertIn(result.decision, {
                "skip", "confirm", "update", "new",
                "incident_only", "procedure_refinement",
            })
            self.assertIsInstance(result.reason, str)

    def test_finish_task_parses_note_type_from_memory_block(self) -> None:
        summary_with_memory_block = textwrap.dedent("""\
            agent: test
            session_date: 2026-04-15

            ## Task
            Research redis eviction.

            ## Reusable Takeaways
            - Redis uses lazy + active expiry.

            ```memory
            items:
              - type: concept
                title: "Redis Expiry Semantics"
                body: "Redis expires keys lazily and actively."
                confidence: high
                tags: [redis, caching]
            ```
        """)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            result = finish_task(
                config,
                OrchestratorOutcomeRequest(
                    bridge_id="test",
                    session_id="session-parse",
                    task_id="task-parse-001",
                    status="success",
                    outcome_type="research",
                    summary=summary_with_memory_block,
                ),
            )

            # decide_writeback_runtime should have been called with note_type=concept
            # and title="Redis Expiry Semantics"; the decision may be "new" or "confirm"
            # depending on what's in the DB.
            self.assertIn(result.decision, {"new", "confirm", "update", "skip", "procedure_refinement", "incident_only"})

    def test_finish_task_handles_bare_summary_gracefully(self) -> None:
        """A plain-text summary without a memory block must not raise."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            result = finish_task(
                config,
                OrchestratorOutcomeRequest(
                    bridge_id="test",
                    session_id="session-bare",
                    task_id="task-bare-001",
                    status="success",
                    outcome_type="general",
                    summary="Just some plain text with no YAML block.",
                ),
            )

            self.assertEqual(result.outcome_status, "accepted")
            self.assertIsNotNone(result.decision)

    def test_finish_task_with_explicit_note_type_overrides_parse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            # Provide explicit note_type/title — should be used even if parse would differ.
            result = finish_task(
                config,
                OrchestratorOutcomeRequest(
                    bridge_id="test",
                    session_id="session-explicit",
                    task_id="task-explicit-001",
                    status="success",
                    outcome_type="coding",
                    summary="Some summary about deploy.",
                    note_type="procedure",
                    title="Explicit Deploy Procedure",
                ),
            )

            self.assertEqual(result.outcome_status, "accepted")

    def test_finish_task_second_call_same_content_does_not_create_duplicate(self) -> None:
        """
        Two finish_task calls with identical note content should not both return "new".

        After the first call the agent is expected to write the note to the vault and
        sync it — simulated here by _write_concept + sync_vault.  The second call then
        sees the existing note and should return "confirm" or "update", not "new".
        """
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            kwargs: dict = dict(
                bridge_id="test",
                outcome_type="concept",
                status="success",
                summary="Redis uses lazy expiry.",
                note_type="concept",
                title="Redis Expiry Concept",
            )

            r1 = finish_task(
                config,
                OrchestratorOutcomeRequest(
                    session_id="session-dedup-1",
                    task_id="task-dedup-1",
                    **kwargs,
                ),
            )

            # Simulate the agent acting on the writeback decision: write the concept
            # note to the vault and sync so the second finish_task can find it.
            _write_concept(
                workspace,
                "concept/redis-expiry-concept",
                "Redis Expiry Concept",
                "Redis uses lazy expiry.",
            )
            sync_vault(config, mode="full")

            r2 = finish_task(
                config,
                OrchestratorOutcomeRequest(
                    session_id="session-dedup-2",
                    task_id="task-dedup-2",
                    **kwargs,
                ),
            )

            # First call may be "new"; second must not be "new" (dedup should trigger).
            if r1.decision == "new":
                self.assertNotEqual(r2.decision, "new",
                    "Second call with identical content should not produce another 'new' decision.")


# ---------------------------------------------------------------------------
# 6. TestFullOrchestrationLoop
# ---------------------------------------------------------------------------

class TestFullOrchestrationLoop(unittest.TestCase):

    def test_full_loop_begin_mark_failure_finish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            _write_procedure(workspace, "procedure/deploy-svc", "Deploy Svc", "Deploy steps.")
            sync_vault(config, mode="full")

            # Step 1: begin
            ctx = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="deploy service",
                    task_id="loop-task-001",
                    session_id="loop-session-001",
                ),
            )
            self.assertEqual(ctx.task_id, "loop-task-001")

            # Step 2: mark failure
            ctx2 = mark_failure(
                config,
                OrchestratorFailureRequest(
                    bridge_id="test",
                    session_id="loop-session-001",
                    task_id="loop-task-001",
                    failure_text="deploy failed: missing env variable",
                    task_kind=ctx.task_kind,
                    query_id=ctx.query_id,
                    retrieval_state=ctx.retrieval_state,
                ),
            )
            self.assertEqual(ctx2.task_id, "loop-task-001")  # identity preserved

            # Step 3: finish
            result = finish_task(
                config,
                OrchestratorOutcomeRequest(
                    bridge_id="test",
                    session_id="loop-session-001",
                    task_id="loop-task-001",
                    status="success",
                    outcome_type="coding",
                    summary="Fixed missing env variable for deploy.",
                    query_id=ctx.query_id,
                    retrieval_state=ctx.retrieval_state,
                    recommendation=ctx.recommendation,
                ),
            )

            self.assertEqual(result.task_id, "loop-task-001")
            self.assertEqual(result.outcome_status, "accepted")

    def test_task_id_stable_across_multiple_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)

            ctx = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="implement rate limiter",
                    task_id="multi-fail-task",
                    session_id="multi-fail-session",
                ),
            )
            r1 = mark_failure(
                config,
                OrchestratorFailureRequest(
                    bridge_id="test",
                    session_id="multi-fail-session",
                    task_id="multi-fail-task",
                    failure_text="first error: import failed",
                ),
            )
            r2 = mark_failure(
                config,
                OrchestratorFailureRequest(
                    bridge_id="test",
                    session_id="multi-fail-session",
                    task_id="multi-fail-task",
                    failure_text="second error: test assertion failed",
                ),
            )

            self.assertEqual(r1.task_id, "multi-fail-task")
            self.assertEqual(r2.task_id, "multi-fail-task")

            conn = connect_db(config.paths.db_path)
            row = conn.execute(
                "SELECT task_id, status FROM task_outcomes WHERE task_id = ?",
                ("multi-fail-task",),
            ).fetchone()
            conn.close()
            # UPSERT means only one row per task_id; status should be from last write
            self.assertIsNotNone(row)
            self.assertEqual(str(row["status"]), "failure")

    def test_auto_retry_runs_focused_second_pass_and_returns_both_emissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            _write(
                workspace / "brain/wiki/incidents/auth-rollout-failure.md",
                """
                ---
                id: incident/auth-rollout-failure
                type: incident
                title: Auth Rollout Failure
                aliases: ["Auth Rollout Failure"]
                status: open
                tags: [test]
                updated_at: 2026-04-15
                created_at: 2026-04-15
                source_refs: [session/test]
                confidence: high
                chain_id: trace/auth-rollout-recovery
                ---

                The auth rollout failed with TokenRefreshError. The successful
                recovery role is missing from this incident.
                """,
            )
            _write(
                workspace / "brain/wiki/concepts/auth-rollout-diagnosis.md",
                """
                ---
                id: concept/auth-rollout-diagnosis
                type: concept
                title: Auth Rollout Diagnosis
                aliases: ["Auth Rollout Diagnosis"]
                status: active
                tags: [test]
                updated_at: 2026-04-15
                created_at: 2026-04-15
                source_refs: [session/test]
                confidence: high
                chain_id: trace/auth-rollout-recovery
                ---

                Diagnosis: TokenRefreshError was caused by a stale auth token
                cache during the rollout.
                """,
            )
            sync_vault(config, mode="full")

            result = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="debug TokenRefreshError auth rollout failure",
                    task_kind="debugging",
                    temporality="current",
                    auto_retry=True,
                    max_focused_retries=1,
                    session_id="retry-session",
                    task_id="retry-task",
                ),
            )

            self.assertTrue(result.auto_retry_summary["attempted"])
            self.assertEqual(len(result.retrieval_passes), 2)
            self.assertEqual(result.final_pass_index, 2)
            self.assertIn("retrieval_passes", result.to_dict())
            self.assertIn("### Retry Trace", result.context_markdown)
            self.assertEqual(result.auto_retry_summary["cutoff_reason"], "no_new_ids")
            self.assertEqual(result.retrieval_passes[0]["pass_index"], 1)
            self.assertEqual(result.retrieval_passes[1]["pass_index"], 2)


# ---------------------------------------------------------------------------
# 7. TestCLIOrchestrator
# ---------------------------------------------------------------------------

class TestCLIOrchestrator(unittest.TestCase):

    def _run_cli(self, args: list[str], config_path: Path) -> tuple[int, str]:
        """Run the CLI and capture stdout."""
        import io
        from contextlib import redirect_stdout
        from second_brain.cli import main

        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                code = main(["--config", str(config_path)] + args)
        except SystemExit as exc:
            code = int(exc.code) if exc.code is not None else 0
        return code, buf.getvalue()

    def test_cli_begin_task_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config_path, config = _prepare_workspace(workspace)
            _write_procedure(workspace, "procedure/deploy", "Deploy Proc", "Steps.")
            sync_vault(config, mode="full")

            code, out = self._run_cli(
                [
                    "orchestrator", "begin-task",
                    "how do we deploy",
                    "--bridge-id", "test",
                    "--session-id", "cli-session",
                    "--task-id", "cli-task",
                    "--json",
                ],
                config_path,
            )

            self.assertEqual(code, 0, msg=f"CLI exited {code}: {out}")
            data = json.loads(out)
            self.assertIn("retrieval_state", data)
            self.assertIn("context_markdown", data)
            self.assertEqual(data["task_id"], "cli-task")
            self.assertEqual(data["session_id"], "cli-session")

    def test_cli_begin_task_human_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config_path, config = _prepare_workspace(workspace)

            code, out = self._run_cli(
                [
                    "orchestrator", "begin-task",
                    "some task",
                    "--bridge-id", "test",
                ],
                config_path,
            )

            self.assertEqual(code, 0)
            self.assertIn("retrieval_state=", out)
            self.assertIn("recommendation=", out)

    def test_cli_mark_failure_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config_path, config = _prepare_workspace(workspace)

            code, out = self._run_cli(
                [
                    "orchestrator", "mark-failure",
                    "error: module not found",
                    "--bridge-id", "test",
                    "--session-id", "cli-mf-session",
                    "--task-id", "cli-mf-task",
                    "--json",
                ],
                config_path,
            )

            self.assertEqual(code, 0, msg=f"CLI exited {code}: {out}")
            data = json.loads(out)
            self.assertEqual(data["task_id"], "cli-mf-task")
            self.assertIn("retrieval_state", data)

    def test_cli_finish_task_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config_path, config = _prepare_workspace(workspace)

            code, out = self._run_cli(
                [
                    "orchestrator", "finish-task",
                    "--bridge-id", "test",
                    "--session-id", "cli-ft-session",
                    "--task-id", "cli-ft-task",
                    "--status", "success",
                    "--outcome-type", "coding",
                    "--summary", "Fixed the deploy issue.",
                    "--json",
                ],
                config_path,
            )

            self.assertEqual(code, 0, msg=f"CLI exited {code}: {out}")
            data = json.loads(out)
            self.assertEqual(data["task_id"], "cli-ft-task")
            self.assertIn("decision", data)
            self.assertEqual(data["outcome_status"], "accepted")

    def test_cli_begin_task_autogenerates_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config_path, config = _prepare_workspace(workspace)

            code, out = self._run_cli(
                [
                    "orchestrator", "begin-task",
                    "some task",
                    "--bridge-id", "test",
                    "--json",
                ],
                config_path,
            )

            self.assertEqual(code, 0)
            data = json.loads(out)
            self.assertTrue(data["task_id"].startswith("task-"))
            self.assertTrue(data["session_id"].startswith("session-"))


# ---------------------------------------------------------------------------
# 8. TestHTTPServer
# ---------------------------------------------------------------------------

def _start_server(config) -> tuple[any, int]:
    from second_brain.server import build_server
    server = build_server(config, host="127.0.0.1", port=0)
    _, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Brief settle
    time.sleep(0.05)
    return server, port


def _stop_server(server: any) -> None:
    server.shutdown()
    server.server_close()


_HTTP_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_get(port: int, path: str) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    with _HTTP_OPENER.open(url, timeout=5) as resp:
        return json.loads(resp.read().decode())


def _http_post(port: int, path: str, payload: dict) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with _HTTP_OPENER.open(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def _http_post_expect_error(port: int, path: str, payload: dict) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with _HTTP_OPENER.open(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


class TestHTTPServer(unittest.TestCase):

    def test_health_endpoint_returns_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            server, port = _start_server(config)
            try:
                data = _http_get(port, "/v1/health")
                self.assertEqual(data["status"], "ok")
                self.assertIn("db_ready", data)
            finally:
                _stop_server(server)

    def test_health_db_ready_true_when_db_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            server, port = _start_server(config)
            try:
                data = _http_get(port, "/v1/health")
                self.assertTrue(data["db_ready"])
            finally:
                _stop_server(server)

    def test_unknown_route_returns_404(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            server, port = _start_server(config)
            try:
                code, data = _http_post_expect_error(port, "/v1/no-such-route", {})
                self.assertEqual(code, 404)
                self.assertIn("error", data)
            finally:
                _stop_server(server)

    def test_begin_task_missing_required_field_returns_400(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            server, port = _start_server(config)
            try:
                # Missing task_text
                code, data = _http_post_expect_error(
                    port, "/v1/orchestrator/begin-task", {"bridge_id": "test"}
                )
                self.assertEqual(code, 400)
                self.assertIn("error", data)
            finally:
                _stop_server(server)

    def test_http_begin_task_returns_context_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            _write_procedure(workspace, "procedure/http-deploy", "HTTP Deploy", "Steps.")
            sync_vault(config, mode="full")
            server, port = _start_server(config)
            try:
                data = _http_post(
                    port,
                    "/v1/orchestrator/begin-task",
                    {
                        "bridge_id": "test",
                        "task_text": "http deploy procedure",
                        "session_id": "http-session",
                        "task_id": "http-task",
                    },
                )
                self.assertEqual(data["task_id"], "http-task")
                self.assertEqual(data["session_id"], "http-session")
                self.assertIn("retrieval_state", data)
                self.assertIn("context_markdown", data)
                self.assertIn("## Memory Context", data["context_markdown"])
            finally:
                _stop_server(server)

    def test_http_begin_task_parity_with_python_call(self) -> None:
        """HTTP and Python calls with the same inputs should return the same retrieval_state."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            _write_procedure(workspace, "procedure/parity", "Parity Proc", "Parity test.")
            sync_vault(config, mode="full")

            # Python call
            py_result = begin_task(
                config,
                OrchestratorTaskRequest(
                    bridge_id="test",
                    task_text="parity proc",
                    session_id="parity-py-session",
                    task_id="parity-py-task",
                ),
            )

            # HTTP call
            server, port = _start_server(config)
            try:
                http_result = _http_post(
                    port,
                    "/v1/orchestrator/begin-task",
                    {
                        "bridge_id": "test",
                        "task_text": "parity proc",
                        "session_id": "parity-http-session",
                        "task_id": "parity-http-task",
                    },
                )
            finally:
                _stop_server(server)

            self.assertEqual(py_result.retrieval_state, http_result["retrieval_state"])
            self.assertEqual(py_result.recommendation, http_result["recommendation"])

    def test_http_mark_failure_preserves_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            server, port = _start_server(config)
            try:
                data = _http_post(
                    port,
                    "/v1/orchestrator/mark-failure",
                    {
                        "bridge_id": "test",
                        "session_id": "http-mf-session",
                        "task_id": "http-mf-task",
                        "failure_text": "error: missing dependency",
                    },
                )
                self.assertEqual(data["task_id"], "http-mf-task")
                self.assertIn("retrieval_state", data)
            finally:
                _stop_server(server)

    def test_http_finish_task_returns_writeback_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            server, port = _start_server(config)
            try:
                data = _http_post(
                    port,
                    "/v1/orchestrator/finish-task",
                    {
                        "bridge_id": "test",
                        "session_id": "http-ft-session",
                        "task_id": "http-ft-task",
                        "status": "success",
                        "outcome_type": "coding",
                        "summary": "Completed the deploy implementation.",
                    },
                )
                self.assertEqual(data["task_id"], "http-ft-task")
                self.assertEqual(data["outcome_status"], "accepted")
                self.assertIn("decision", data)
                self.assertIn("reason", data)
            finally:
                _stop_server(server)

    def test_http_retrieve_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            _write_procedure(workspace, "procedure/http-retrieve", "HTTP Retrieve", "Steps.")
            sync_vault(config, mode="full")
            server, port = _start_server(config)
            try:
                data = _http_post(
                    port,
                    "/v1/retrieve",
                    {"query": "http retrieve procedure"},
                )
                self.assertIn("hits", data)
                self.assertIsInstance(data["hits"], list)
                self.assertNotIn("body", data["hits"][0])
                hydrated = _http_post(
                    port,
                    "/v1/retrieve",
                    {"query": "http retrieve procedure", "include_body": True, "body_chars": 40},
                )
                self.assertIn("body", hydrated["hits"][0])
                self.assertTrue(hydrated["hits"][0]["body_truncated"])
            finally:
                _stop_server(server)

    def test_http_ingest_session_then_begin_task_surfaces_archive_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            config.ingest.auto_trigger_pipeline = False
            server, port = _start_server(config)
            try:
                ingest = _http_post(
                    port,
                    "/v1/ingest/session",
                    {
                        "bridge_id": "codex",
                        "session_id": "http-archive-session",
                        "content": """
                        # Runtime Session

                        The cobalt deploy timeout was fixed by clearing the
                        staged release lock and rerunning the deploy.
                        """,
                    },
                )
                self.assertGreater(ingest["chunk_count"], 0)

                context = _http_post(
                    port,
                    "/v1/orchestrator/begin-task",
                    {
                        "bridge_id": "test",
                        "task_text": "cobalt deploy timeout staged release lock",
                        "session_id": "http-archive-context",
                        "task_id": "http-archive-task",
                        "task_kind": "debugging",
                    },
                )
            finally:
                _stop_server(server)

            packet = context["memory_packet"]
            self.assertEqual(packet["primary_memories"], [])
            self.assertEqual(len(packet["archive_context"]), 1)
            self.assertTrue(packet["assembly_notes"]["archive_only"])
            self.assertIn("source:raw_session", packet["archive_context"][0]["provenance"])

    def test_http_sync_vault_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _, config = _prepare_workspace(workspace)
            server, port = _start_server(config)
            try:
                data = _http_post(port, "/v1/sync/vault", {"mode": "incremental"})
                self.assertIn("scanned_paths", data)
                self.assertIn("synced_notes", data)
            finally:
                _stop_server(server)


if __name__ == "__main__":
    unittest.main()
