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

import yaml

from second_brain.config import load_config
from second_brain.db import connect_db, run_migrations
from second_brain.jobs import run_job
from second_brain.maintenance import (
    GraphRebuildResult,
    ImportanceSignal,
    LifecycleAuditResult,
    SemanticDuplicatePair,
    WikiHealthStats,
    audit_lifecycle_states,
    compute_importance_scores,
    compute_wiki_health_stats,
    detect_orphan_nodes,
    detect_semantic_duplicates,
    rebuild_wiki_graph,
)
from second_brain.retrieval import retrieve
from second_brain.sync import sync_vault


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


def _minimal_config(workspace: Path) -> Path:
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
        top_k = 5
        include_archive_fallback = true

        [ingest]
        chunk_tokens = 80
        chunk_overlap_tokens = 10
        default_archive_type = "session"
        explicit_markers_only = false

        [embeddings]
        enabled = false
        provider = "hash"
        model = "hash-128-v1"
        dimensions = 128
        reembed_on_summary_change_only = true
        """,
    )
    return config_path


def _system_files(workspace: Path) -> None:
    for name in ("MEMORY.md", "USER.md", "ACTIVE.md"):
        _write(workspace / "brain/system" / name, f"# {name}\n")
    _write(
        workspace / "brain/system/AGENTS.md",
        """
        bridges:
          local_cli:
            scope: [wiki]
            can_promote: false
            can_write_canonical: true
        """,
    )


def _note(workspace: Path, rel_path: str, content: str) -> None:
    _write(workspace / "brain/wiki" / rel_path, content)


class GraphRebuildTest(unittest.TestCase):
    def _setup(self, workspace: Path):
        _system_files(workspace)
        config_path = _minimal_config(workspace)
        config = load_config(config_path, workspace)
        connection = connect_db(config.paths.db_path)
        run_migrations(connection, ROOT / "migrations")
        connection.close()
        return config

    def test_rebuild_repairs_drift_entity_node(self) -> None:
        """Delete a linked note from DB; rebuild restores the edge via an entity node."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)

            _note(
                workspace,
                "concepts/active/note-a.md",
                """
                ---
                id: concept/note-a
                type: concept
                title: Note A
                entities: [concept/note-b]
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Body of note A.
                """,
            )
            _note(
                workspace,
                "concepts/active/note-b.md",
                """
                ---
                id: concept/note-b
                type: concept
                title: Note B
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Body of note B.
                """,
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            # Simulate drift: delete note B from DB (keep file on disk)
            with connection:
                connection.execute("DELETE FROM notes WHERE id = 'concept/note-b'")
                connection.execute("DELETE FROM graph_nodes WHERE id = 'concept/note-b'")
                connection.execute("DELETE FROM graph_edges WHERE source_note_id = 'concept/note-a'")

            result = rebuild_wiki_graph(connection, config)
            connection.close()

            self.assertEqual(result.notes_rebuilt, 1)  # only note-a in DB; note-b was deleted
            connection = connect_db(config.paths.db_path)
            edge = connection.execute(
                "SELECT * FROM graph_edges WHERE source_note_id = 'concept/note-a' AND target_id = 'concept/note-b'"
            ).fetchone()
            # node B re-created as entity node since note was deleted from DB
            node = connection.execute(
                "SELECT node_type FROM graph_nodes WHERE id = 'concept/note-b'"
            ).fetchone()
            connection.close()
            self.assertIsNotNone(edge)
            self.assertIsNotNone(node)
            self.assertEqual(node["node_type"], "entity")

    def test_rebuild_prunes_stale_edges(self) -> None:
        """Stale graph_edges pointing to nonexistent graph_nodes are deleted.

        Uses a retired-status note as source so it is excluded from per-note
        rebuild (which would otherwise delete all its edges first). The explicit
        prune step must fire to remove the stale edge.
        """
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)

            _note(
                workspace,
                "concepts/active/lone.md",
                """
                ---
                id: concept/lone
                type: concept
                title: Lone Note
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Solo note.
                """,
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            # Retire the note so rebuild_wiki_graph excludes it from per-note rebuild.
            # Then insert a stale edge from it to a ghost target with no graph_node.
            # The explicit prune step must catch it.
            with connection:
                connection.execute("UPDATE notes SET status = 'retired' WHERE id = 'concept/lone'")
                connection.execute(
                    """
                    INSERT INTO graph_edges(source_note_id, target_id, target_kind, relation_type, weight, updated_at)
                    VALUES ('concept/lone', 'ghost/nonexistent', 'concept', 'entity', 0.5, datetime('now'))
                    """
                )

            stale_before = connection.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE target_id = 'ghost/nonexistent'"
            ).fetchone()[0]
            self.assertEqual(stale_before, 1)

            result = rebuild_wiki_graph(connection, config)
            connection.close()

            connection = connect_db(config.paths.db_path)
            stale_after = connection.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE target_id = 'ghost/nonexistent'"
            ).fetchone()[0]
            connection.close()

            self.assertEqual(stale_after, 0)
            self.assertGreaterEqual(result.stale_edges_pruned, 1)

    def test_detect_orphan_nodes(self) -> None:
        """Note with no entity_relations has no edges; detect_orphan_nodes returns its ID."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)

            _note(
                workspace,
                "concepts/active/island.md",
                """
                ---
                id: concept/island
                type: concept
                title: Island Note
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                No links anywhere.
                """,
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            rebuild_wiki_graph(connection, config)

            orphans = detect_orphan_nodes(connection)
            connection.close()

            self.assertIn("concept/island", orphans)

    def test_dry_run_no_writes(self) -> None:
        """dry_run=True produces no DB changes."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)

            _note(
                workspace,
                "concepts/active/stable.md",
                """
                ---
                id: concept/stable
                type: concept
                title: Stable Note
                entities: [concept/other]
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Has a link.
                """,
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")

            # Capture edge count before dry run
            edges_before = connection.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
            nodes_before = connection.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]

            # Insert stale edge to verify it is NOT pruned in dry_run
            with connection:
                connection.execute(
                    """
                    INSERT INTO graph_edges(source_note_id, target_id, target_kind, relation_type, weight, updated_at)
                    VALUES ('concept/stable', 'ghost/dry', 'concept', 'entity', 0.5, datetime('now'))
                    """
                )

            result = rebuild_wiki_graph(connection, config, dry_run=True)

            # Stale edge should still be there
            stale = connection.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE target_id = 'ghost/dry'"
            ).fetchone()[0]
            connection.close()

            self.assertEqual(stale, 1)  # not pruned
            self.assertEqual(result.stale_edges_pruned, 0)
            self.assertEqual(result.orphan_nodes_pruned, 0)
            self.assertGreater(result.notes_rebuilt, 0)

    def test_wiki_graph_rebuild_job(self) -> None:
        """wiki_graph_rebuild job runs dry-run without error and includes stats keys."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)
            _note(
                workspace,
                "concepts/active/test-note.md",
                """
                ---
                id: concept/test-note
                type: concept
                title: Test Note
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Content.
                """,
            )
            sync_vault(config, mode="full")

            result = run_job(config, job_name="wiki_graph_rebuild", dry_run=True)

            self.assertEqual(result.status, "ok")
            self.assertIn("notes_scanned", result.stats)
            self.assertIn("notes_rebuilt", result.stats)
            self.assertIn("stale_edges_pruned", result.stats)
            self.assertIn("orphan_nodes_pruned", result.stats)


class ImportanceScoringTest(unittest.TestCase):
    def _setup(self, workspace: Path):
        _system_files(workspace)
        config_path = _minimal_config(workspace)
        config = load_config(config_path, workspace)
        connection = connect_db(config.paths.db_path)
        run_migrations(connection, ROOT / "migrations")
        connection.close()
        return config

    def test_canonical_high_degree_scores_higher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)

            _note(
                workspace,
                "concepts/active/hub.md",
                """
                ---
                id: concept/hub
                type: concept
                title: Hub Note
                lifecycle_state: canonical
                entities: [concept/spoke-a, concept/spoke-b]
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Central hub note.
                """,
            )
            _note(
                workspace,
                "concepts/active/spoke-a.md",
                """
                ---
                id: concept/spoke-a
                type: concept
                title: Spoke A
                lifecycle_state: draft
                entities: [concept/hub]
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Spoke A links to hub.
                """,
            )
            _note(
                workspace,
                "concepts/active/spoke-b.md",
                """
                ---
                id: concept/spoke-b
                type: concept
                title: Spoke B
                lifecycle_state: draft
                entities: [concept/hub]
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Spoke B links to hub.
                """,
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            rebuild_wiki_graph(connection, config)
            signals = compute_importance_scores(connection)
            connection.close()

            self.assertIn("concept/hub", signals)
            self.assertIn("concept/spoke-a", signals)
            hub = signals["concept/hub"]
            spoke = signals["concept/spoke-a"]
            self.assertGreater(hub.importance_score, spoke.importance_score)
            self.assertGreater(hub.lifecycle_weight, spoke.lifecycle_weight)
            self.assertGreater(hub.in_degree, spoke.in_degree)

    def test_missing_trust_freshness_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)
            _note(
                workspace,
                "concepts/active/lone2.md",
                """
                ---
                id: concept/lone2
                type: concept
                title: Lone2
                lifecycle_state: draft
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                No trust or freshness rows.
                """,
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            signals = compute_importance_scores(connection)
            connection.close()

            self.assertIn("concept/lone2", signals)
            s = signals["concept/lone2"]
            self.assertEqual(s.trust_score, 0.5)
            self.assertEqual(s.freshness_score, 0.5)

    def test_importance_persisted_to_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)
            _note(
                workspace,
                "concepts/active/persist.md",
                """
                ---
                id: concept/persist
                type: concept
                title: Persist
                lifecycle_state: verified
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Persistence test.
                """,
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            compute_importance_scores(connection)
            row = connection.execute(
                "SELECT importance_score FROM note_importance WHERE note_id = 'concept/persist'"
            ).fetchone()
            connection.close()

            self.assertIsNotNone(row)
            self.assertGreater(float(row["importance_score"]), 0.0)

    def test_retrieval_hit_features_includes_importance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)

            _write(
                workspace / "brain/wiki/concepts/active/searchable.md",
                textwrap.dedent("""
                ---
                id: concept/searchable
                type: concept
                title: Searchable Concept
                lifecycle_state: verified
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                This note is about authentication tokens.
                """).strip() + "\n",
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            compute_importance_scores(connection)
            connection.close()

            result = retrieve(config, "authentication tokens", top_k=3)
            self.assertTrue(result.hits)

            connection = connect_db(config.paths.db_path)
            feat_row = connection.execute(
                "SELECT importance_score FROM retrieval_hit_features WHERE note_id = 'concept/searchable'"
            ).fetchone()
            connection.close()
            self.assertIsNotNone(feat_row)
            # importance_score column exists (may be 0.0 if note was scored)

    def test_wiki_importance_scoring_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)
            _note(workspace, "concepts/active/job-test.md", """
                ---
                id: concept/job-test
                type: concept
                title: Job Test
                lifecycle_state: draft
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Job test note.
                """)
            sync_vault(config, mode="full")

            result = run_job(config, job_name="wiki_importance_scoring", dry_run=False)
            self.assertEqual(result.status, "ok")
            self.assertIn("importance_notes_scored", result.stats)
            self.assertGreater(result.stats["importance_notes_scored"], 0)


class WikiHealthTest(unittest.TestCase):
    def _setup(self, workspace: Path):
        _system_files(workspace)
        config_path = _minimal_config(workspace)
        config = load_config(config_path, workspace)
        connection = connect_db(config.paths.db_path)
        run_migrations(connection, ROOT / "migrations")
        connection.close()
        return config

    def test_wiki_health_counts_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)

            for i in range(3):
                _note(workspace, f"concepts/active/c{i}.md", f"""
                    ---
                    id: concept/c{i}
                    type: concept
                    title: Concept {i}
                    lifecycle_state: draft
                    updated_at: 2026-01-01
                    created_at: 2026-01-01
                    ---
                    Content {i}.
                    """)
            _note(workspace, "decisions/active/d0.md", """
                ---
                id: decision/d0
                type: decision
                title: Decision 0
                lifecycle_state: verified
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Decision content.
                """)
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            health = compute_wiki_health_stats(connection)
            connection.close()

            self.assertEqual(health.total_notes, 4)
            self.assertEqual(health.notes_by_type.get("concept", 0), 3)
            self.assertEqual(health.notes_by_type.get("decision", 0), 1)
            self.assertEqual(health.notes_by_lifecycle.get("draft", 0), 3)
            self.assertEqual(health.notes_by_lifecycle.get("verified", 0), 1)

    def test_detect_semantic_duplicates_skips_when_embeddings_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)
            # embeddings disabled in _minimal_config
            self.assertFalse(config.embeddings.enabled)

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            pairs = detect_semantic_duplicates(connection, config)
            connection.close()

            self.assertEqual(pairs, [])

    def test_detect_semantic_duplicates_finds_near_identical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            # Enable embeddings with hash provider
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
                top_k = 5
                include_archive_fallback = false

                [ingest]
                chunk_tokens = 80
                chunk_overlap_tokens = 10
                default_archive_type = "session"
                explicit_markers_only = false

                [embeddings]
                enabled = true
                provider = "hash"
                model = "hash-128-v1"
                dimensions = 128
                reembed_on_summary_change_only = true

                [maintenance]
                semantic_dedup_threshold = 0.01
                """,
            )
            _system_files(workspace)
            config = load_config(config_path, workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            # Two notes with identical summary → same hash vector → cosine=1.0
            for i in ("x", "y"):
                _note(workspace, f"concepts/active/dup{i}.md", f"""
                    ---
                    id: concept/dup{i}
                    type: concept
                    title: Dup {i}
                    summary: identical summary text for dedup testing
                    updated_at: 2026-01-01
                    created_at: 2026-01-01
                    ---
                    Body {i}.
                    """)
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            pairs = detect_semantic_duplicates(connection, config, dry_run=True)
            connection.close()

            self.assertTrue(len(pairs) >= 1)
            ids = {(p.note_id_a, p.note_id_b) for p in pairs}
            self.assertIn(("concept/dupx", "concept/dupy"), ids)

    def test_semantic_dedup_slug_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
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
                top_k = 5
                include_archive_fallback = false

                [ingest]
                chunk_tokens = 80
                chunk_overlap_tokens = 10
                default_archive_type = "session"
                explicit_markers_only = false

                [embeddings]
                enabled = true
                provider = "hash"
                model = "hash-128-v1"
                dimensions = 128
                reembed_on_summary_change_only = true

                [maintenance]
                semantic_dedup_threshold = 0.01
                """,
            )
            _system_files(workspace)
            config = load_config(config_path, workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            for i in ("p", "q"):
                _note(workspace, f"concepts/active/idem{i}.md", f"""
                    ---
                    id: concept/idem{i}
                    type: concept
                    title: Idem {i}
                    summary: same summary for idempotency test
                    updated_at: 2026-01-01
                    created_at: 2026-01-01
                    ---
                    Body {i}.
                    """)
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            detect_semantic_duplicates(connection, config)  # first run creates review item
            review_dir = config.paths.vault_root / "wiki" / "review"
            count_after_first = len(list(review_dir.glob("semantic-dedup*.md"))) if review_dir.exists() else 0
            detect_semantic_duplicates(connection, config)  # second run must not duplicate
            count_after_second = len(list(review_dir.glob("semantic-dedup*.md"))) if review_dir.exists() else 0
            connection.close()

            self.assertEqual(count_after_first, count_after_second)

    def test_wiki_health_report_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)
            _note(workspace, "concepts/active/h0.md", """
                ---
                id: concept/h0
                type: concept
                title: Health0
                lifecycle_state: draft
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Health test.
                """)
            sync_vault(config, mode="full")

            result = run_job(config, job_name="wiki_health_report", dry_run=True)
            self.assertEqual(result.status, "ok")
            self.assertIn("wiki_total_notes", result.stats)
            self.assertIn("wiki_orphan_notes", result.stats)
            self.assertIn("semantic_dedup_candidates", result.stats)

    def test_maintenance_overview_includes_wiki_health(self) -> None:
        from second_brain.ops import write_maintenance_overview
        from second_brain.maintenance import WikiHealthStats

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)

            health = WikiHealthStats(
                total_notes=10,
                notes_by_type={"concept": 5, "decision": 5},
                notes_by_lifecycle={"canonical": 3, "verified": 4, "draft": 3},
                notes_by_freshness={"fresh": 8, "stale": 2},
                orphan_note_count=1,
                avg_in_degree=2.5,
                avg_out_degree=1.8,
                lifecycle_advanced_this_run=2,
                semantic_dedup_candidates=0,
                open_review_count=0,
                open_relearn_count=0,
            )
            path = write_maintenance_overview(
                config,
                stats={},
                review_items=[],
                contradiction_rows=[],
                stale_rows=[],
                relearn_tasks=[],
                eval_candidates=[],
                wiki_health=health,
            )
            content = path.read_text()
            self.assertIn("Wiki Health", content)
            self.assertIn("Total notes | 10", content)
            self.assertIn("Orphan notes | 1", content)


class LifecycleAuditTest(unittest.TestCase):
    def _setup(self, workspace: Path):
        _system_files(workspace)
        config_path = _minimal_config(workspace)
        config = load_config(config_path, workspace)
        connection = connect_db(config.paths.db_path)
        run_migrations(connection, ROOT / "migrations")
        connection.close()
        return config

    def _read_lifecycle(self, path: Path) -> str:
        import yaml as _yaml
        text = path.read_text()
        fm = text.split("---")[1]
        return str(_yaml.safe_load(fm).get("lifecycle_state", ""))

    def _note_with_corroboration(self, workspace: Path, rel_path: str, note_id: str,
                                  lifecycle_state: str, corroboration_count: int) -> Path:
        path = workspace / "brain/wiki" / rel_path
        _write(
            path,
            f"""
            ---
            id: {note_id}
            type: concept
            title: Test Note {note_id}
            lifecycle_state: {lifecycle_state}
            corroboration_count: {corroboration_count}
            updated_at: 2026-01-01
            created_at: 2026-01-01
            ---
            Body content.
            """,
        )
        return path

    def test_draft_advances_to_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)
            path = self._note_with_corroboration(
                workspace, "concepts/active/note-v.md", "concept/note-v", "draft", 2
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            result = audit_lifecycle_states(connection, config)
            connection.close()

            self.assertEqual(result.advanced_to_verified, 1)
            self.assertEqual(result.advanced_to_canonical, 0)
            self.assertEqual(self._read_lifecycle(path), "verified")

            connection = connect_db(config.paths.db_path)
            db_state = connection.execute(
                "SELECT lifecycle_state FROM notes WHERE id = 'concept/note-v'"
            ).fetchone()
            connection.close()
            self.assertEqual(db_state["lifecycle_state"], "verified")

    def test_draft_advances_directly_to_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)
            path = self._note_with_corroboration(
                workspace, "concepts/active/note-c.md", "concept/note-c", "draft", 4
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            result = audit_lifecycle_states(connection, config)
            connection.close()

            self.assertEqual(result.advanced_to_canonical, 1)
            self.assertEqual(result.advanced_to_verified, 0)
            self.assertEqual(self._read_lifecycle(path), "canonical")

    def test_verified_advances_to_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)
            path = self._note_with_corroboration(
                workspace, "concepts/active/note-vc.md", "concept/note-vc", "verified", 4
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            result = audit_lifecycle_states(connection, config)
            connection.close()

            self.assertEqual(result.advanced_to_canonical, 1)
            self.assertEqual(self._read_lifecycle(path), "canonical")

    def test_contested_note_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)
            path = self._note_with_corroboration(
                workspace, "concepts/active/note-x.md", "concept/note-x", "contested", 5
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            result = audit_lifecycle_states(connection, config)
            connection.close()

            self.assertEqual(result.advanced_to_verified, 0)
            self.assertEqual(result.advanced_to_canonical, 0)
            self.assertEqual(self._read_lifecycle(path), "contested")

    def test_drift_flagged_via_review_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)
            # Old note: created 60 days ago, no corroboration
            path = workspace / "brain/wiki/concepts/active/stale-draft.md"
            _write(
                path,
                """
                ---
                id: concept/stale-draft
                type: concept
                title: Stale Draft
                lifecycle_state: draft
                corroboration_count: 0
                updated_at: 2025-01-01
                created_at: 2025-01-01
                ---
                Very old content.
                """,
            )
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            result = audit_lifecycle_states(connection, config)
            connection.close()

            self.assertEqual(result.drift_flagged, 1)
            review_dir = config.paths.vault_root / "wiki" / "review"
            review_files = list(review_dir.glob("*.md")) if review_dir.exists() else []
            self.assertTrue(any("lifecycle" in f.name for f in review_files))

    def test_dry_run_no_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)
            path = self._note_with_corroboration(
                workspace, "concepts/active/note-d.md", "concept/note-d", "draft", 4
            )
            old_text = path.read_text()
            sync_vault(config, mode="full")

            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            result = audit_lifecycle_states(connection, config, dry_run=True)
            connection.close()

            self.assertEqual(result.advanced_to_canonical, 1)
            # File unchanged
            self.assertEqual(path.read_text(), old_text)
            # DB unchanged
            connection = connect_db(config.paths.db_path)
            db_state = connection.execute(
                "SELECT lifecycle_state FROM notes WHERE id = 'concept/note-d'"
            ).fetchone()
            connection.close()
            self.assertEqual(db_state["lifecycle_state"], "draft")

    def test_wiki_lifecycle_audit_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup(workspace)
            self._note_with_corroboration(
                workspace, "concepts/active/note-j.md", "concept/note-j", "draft", 0
            )
            sync_vault(config, mode="full")

            result = run_job(config, job_name="wiki_lifecycle_audit", dry_run=True)

            self.assertEqual(result.status, "ok")
            self.assertIn("notes_scanned", result.stats)
            self.assertIn("advanced_to_verified", result.stats)
            self.assertIn("advanced_to_canonical", result.stats)
            self.assertIn("drift_flagged", result.stats)


class EmbeddingNeighborTest(unittest.TestCase):
    def _setup_with_embeddings(self, workspace: Path):
        """Config with embeddings enabled (hash provider)."""
        _system_files(workspace)
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
            enabled = true
            provider = "hash"
            model = "hash-128-v1"
            dimensions = 128

            [maintenance]
            embedding_neighbor_enabled = true
            neighbor_similarity_threshold = 0.0
            neighbor_k = 10
            """,
        )
        config = load_config(config_path, workspace)
        connection = connect_db(config.paths.db_path)
        run_migrations(connection, ROOT / "migrations")
        connection.close()
        return config

    def _seed_concept(self, workspace: Path, note_id: str, summary: str) -> None:
        slug = note_id.split("/", 1)[-1]
        _write(
            workspace / "brain/wiki" / f"concepts/active/{slug}.md",
            f"""
            ---
            id: {note_id}
            type: concept
            title: {slug.replace("-", " ").title()}
            summary: {summary}
            updated_at: 2026-01-01
            created_at: 2026-01-01
            ---
            Body.
            """,
        )

    def test_similar_to_link_asserted(self) -> None:
        """Two concept notes with identical summaries get a similar_to link."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup_with_embeddings(workspace)
            self._seed_concept(workspace, "concept/alpha", "shared identical summary text")
            self._seed_concept(workspace, "concept/beta", "shared identical summary text")
            sync_vault(config, mode="full")

            from second_brain.maintenance import run_embedding_neighbor_pass
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            # populate embeddings table for both notes
            from second_brain.semantics import encode_text
            import json
            for nid, summary in [("concept/alpha", "shared identical summary text"),
                                  ("concept/beta", "shared identical summary text")]:
                vec = encode_text(summary, dimensions=128)
                connection.execute(
                    "INSERT OR REPLACE INTO note_vectors (note_id, model, dimensions, vector_json, source_hash, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (nid, "hash-128-v1", 128, json.dumps(vec), "testhash", "2026-01-01"),
                )
            connection.commit()

            links = run_embedding_neighbor_pass(connection, config, "concept/alpha")
            connection.close()

            self.assertGreaterEqual(links, 1)

            # verify similar_to appears in frontmatter
            import yaml as _yaml
            note_path = workspace / "brain/wiki/concepts/active/alpha.md"
            text = note_path.read_text()
            fm_end = text.index("---", 3)
            fm = _yaml.safe_load(text[3:fm_end])
            relations = fm.get("relations", [])
            self.assertTrue(
                any(r.get("type") == "similar_to" and r.get("target") == "concept/beta" for r in relations),
                f"Expected similar_to beta in relations: {relations}",
            )

    def test_type_boundary_not_crossed(self) -> None:
        """concept and procedure notes must not be linked even at threshold=0."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup_with_embeddings(workspace)

            _write(
                workspace / "brain/wiki/concepts/active/c1.md",
                """
                ---
                id: concept/c1
                type: concept
                title: C1
                summary: identical text boundary test
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Body.
                """,
            )
            _write(
                workspace / "brain/wiki/procedures/p1.md",
                """
                ---
                id: procedure/p1
                type: procedure
                title: P1
                summary: identical text boundary test
                updated_at: 2026-01-01
                created_at: 2026-01-01
                ---
                Body.
                """,
            )
            sync_vault(config, mode="full")

            from second_brain.maintenance import run_embedding_neighbor_pass
            from second_brain.semantics import encode_text
            import json
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            for nid in ("concept/c1", "procedure/p1"):
                vec = encode_text("identical text boundary test", dimensions=128)
                connection.execute(
                    "INSERT OR REPLACE INTO note_vectors (note_id, model, dimensions, vector_json, source_hash, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (nid, "hash-128-v1", 128, json.dumps(vec), "testhash", "2026-01-01"),
                )
            connection.commit()

            links = run_embedding_neighbor_pass(connection, config, "concept/c1")
            connection.close()

            self.assertEqual(links, 0, "concept must not link to procedure note")

    def test_dedup_threshold_creates_merge_review(self) -> None:
        """Similarity >= dedup_threshold triggers a merge_review item."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup_with_embeddings(workspace)
            # override dedup threshold to 0.0 so hash-identical notes always trigger
            config.maintenance.semantic_dedup_threshold = 0.0
            self._seed_concept(workspace, "concept/dup-a", "exact duplicate summary")
            self._seed_concept(workspace, "concept/dup-b", "exact duplicate summary")
            sync_vault(config, mode="full")

            from second_brain.maintenance import run_embedding_neighbor_pass
            from second_brain.semantics import encode_text
            import json
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            vec = encode_text("exact duplicate summary", dimensions=128)
            for nid in ("concept/dup-a", "concept/dup-b"):
                connection.execute(
                    "INSERT OR REPLACE INTO note_vectors (note_id, model, dimensions, vector_json, source_hash, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (nid, "hash-128-v1", 128, json.dumps(vec), "testhash", "2026-01-01"),
                )
            connection.commit()

            run_embedding_neighbor_pass(connection, config, "concept/dup-a")
            connection.close()

            review_dir = config.paths.vault_root / "wiki" / "review"
            merge_files = list(review_dir.glob("semantic-dedup*.md")) if review_dir.exists() else []
            self.assertGreaterEqual(len(merge_files), 1, "Expected merge_review item for dedup pair")

    def test_backfill_creates_scan_records(self) -> None:
        """run_embedding_neighbor_backfill creates note_embedding_scans rows."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup_with_embeddings(workspace)
            self._seed_concept(workspace, "concept/back-a", "backfill test summary")
            self._seed_concept(workspace, "concept/back-b", "different backfill summary")
            sync_vault(config, mode="full")

            from second_brain.maintenance import run_embedding_neighbor_backfill
            from second_brain.semantics import encode_text
            import json
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            for nid, summary in [("concept/back-a", "backfill test summary"),
                                  ("concept/back-b", "different backfill summary")]:
                vec = encode_text(summary, dimensions=128)
                connection.execute(
                    "INSERT OR REPLACE INTO note_vectors (note_id, model, dimensions, vector_json, source_hash, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (nid, "hash-128-v1", 128, json.dumps(vec), "testhash", "2026-01-01"),
                )
            connection.commit()

            run_embedding_neighbor_backfill(connection, config)
            scan_count = connection.execute("SELECT COUNT(*) AS cnt FROM note_embedding_scans").fetchone()["cnt"]
            connection.close()

            self.assertGreaterEqual(scan_count, 1)

    def test_neighbor_backfill_job(self) -> None:
        """wiki_embedding_neighbor_backfill job runs and returns expected stat keys."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = self._setup_with_embeddings(workspace)
            self._seed_concept(workspace, "concept/job-test", "job backfill test")
            sync_vault(config, mode="full")

            result = run_job(config, job_name="wiki_embedding_neighbor_backfill", dry_run=False)
            self.assertEqual(result.status, "ok")
            self.assertIn("neighbor_links_asserted", result.stats)


if __name__ == "__main__":
    unittest.main()
