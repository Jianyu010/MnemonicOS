from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from second_brain.config import load_config
from second_brain.db import connect_db, run_migrations
from second_brain.promotion.review_ops import (
    ReviewItem,
    approve_review_item,
    get_review_item,
    list_review_items,
    reject_review_item,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


def _config(workspace: Path):
    _write(workspace / "brain/system/MEMORY.md", "# MEMORY\n")
    _write(workspace / "brain/system/USER.md", "# USER\n")
    _write(workspace / "brain/system/ACTIVE.md", "# ACTIVE\n")
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

        [extraction]
        vlm_provider = "fixture"
        vlm_model = "fixture-vlm"
        llm_provider = "fixture"
        llm_model = "fixture-llm"
        max_queue_batch = 5
        """,
    )
    cfg = load_config(config_path, workspace)
    conn = connect_db(cfg.paths.db_path)
    run_migrations(conn, ROOT / "migrations")
    conn.close()
    return cfg


_REVIEW_FILE_TMPL = """\
---
id: {note_id}
type: {note_type}
title: {title}
lifecycle_state: review
created_at: '{created_at}'
updated_at: '{created_at}'
corroboration_count: 1
source_refs:
- candidate:1
---

## Promotion Review

Reason: {reason}

## Summary

{body}
"""


def _make_review_file(
    review_dir: Path,
    *,
    slug: str,
    note_id: str,
    note_type: str = "source",
    title: str = "Test Note",
    reason: str = "wiki_conflict",
    body: str = "Some content here.",
    timestamp: str = "2026-05-08T09:00:00Z",
) -> Path:
    review_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{slug}-2026-05-08-09-00-00.md"
    path = review_dir / filename
    content = _REVIEW_FILE_TMPL.format(
        note_id=note_id,
        note_type=note_type,
        title=title,
        reason=reason,
        body=body,
        created_at=timestamp,
    )
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class ReviewOpsTest(unittest.TestCase):

    def test_list_empty_review_dir(self):
        """Empty review dir returns empty list."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            items = list_review_items(cfg)
            self.assertEqual(items, [])

    def test_list_returns_parsed_items(self):
        """Two planted review files → two ReviewItems with correct fields."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            _make_review_file(
                review_dir,
                slug="first-note",
                note_id="source/first-note",
                title="First Note",
                reason="wiki_conflict",
            )
            _make_review_file(
                review_dir,
                slug="second-note",
                note_id="concept/second-note",
                note_type="concept",
                title="Second Note",
                reason="low_confidence",
            )
            items = list_review_items(cfg)
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0].title, "First Note")
            self.assertEqual(items[0].reason, "wiki_conflict")
            self.assertEqual(items[0].note_type, "source")
            self.assertEqual(items[1].title, "Second Note")
            self.assertEqual(items[1].reason, "low_confidence")
            self.assertEqual(items[1].note_type, "concept")

    def test_get_review_item_exact_filename(self):
        """Exact filename match (with .md) resolves correctly."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            path = _make_review_file(
                review_dir,
                slug="my-note",
                note_id="source/my-note",
                title="My Note",
            )
            item = get_review_item(cfg, path.name)
            self.assertEqual(item.title, "My Note")

    def test_get_review_item_prefix_match(self):
        """Prefix match (no .md, partial slug) resolves correctly."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            _make_review_file(
                review_dir,
                slug="my-special-note",
                note_id="source/my-special-note",
                title="My Special Note",
            )
            item = get_review_item(cfg, "my-special")
            self.assertEqual(item.title, "My Special Note")

    def test_get_review_item_not_found_raises(self):
        """Non-existent name raises FileNotFoundError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            with self.assertRaises(FileNotFoundError):
                get_review_item(cfg, "does-not-exist")

    def test_approve_moves_file_to_final_path(self):
        """Approve → note at wiki/sources/{slug}.md, review file deleted."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            review_path = _make_review_file(
                review_dir,
                slug="my-concept-note",
                note_id="source/my-concept-note",
                note_type="source",
                title="My Concept Note",
            )
            final = approve_review_item(cfg, review_path.name)
            self.assertTrue(final.exists())
            self.assertFalse(review_path.exists())
            expected = cfg.paths.vault_root / "wiki" / "sources" / "my-concept-note.md"
            self.assertEqual(final.resolve(), expected.resolve())

    def test_approve_strips_promotion_header(self):
        """Approved note body has no '## Promotion Review' header."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            review_path = _make_review_file(
                review_dir,
                slug="strip-header-note",
                note_id="source/strip-header-note",
                title="Strip Header Note",
                reason="wiki_conflict",
                body="Actual content.",
            )
            final = approve_review_item(cfg, review_path.name)
            content = final.read_text(encoding="utf-8")
            self.assertNotIn("## Promotion Review", content)
            self.assertNotIn("Reason:", content)
            self.assertIn("Actual content.", content)

    def test_approve_sets_lifecycle_state_default(self):
        """Lifecycle state defaults to LIFECYCLE_DEFAULTS[type]."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            review_path = _make_review_file(
                review_dir,
                slug="lifecycle-default-note",
                note_id="source/lifecycle-default-note",
                note_type="source",
                title="Lifecycle Default Note",
            )
            final = approve_review_item(cfg, review_path.name)
            content = final.read_text(encoding="utf-8")
            # source → LIFECYCLE_DEFAULTS["source"] = "verified"
            self.assertIn("lifecycle_state: verified", content)

    def test_approve_lifecycle_state_override(self):
        """Explicit --lifecycle-state overrides default."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            review_path = _make_review_file(
                review_dir,
                slug="override-lifecycle-note",
                note_id="source/override-lifecycle-note",
                note_type="source",
                title="Override Lifecycle Note",
            )
            final = approve_review_item(cfg, review_path.name, lifecycle_state="canonical")
            content = final.read_text(encoding="utf-8")
            self.assertIn("lifecycle_state: canonical", content)

    def test_approve_dry_run_no_changes(self):
        """dry_run=True → no files moved, no DB changes."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            review_path = _make_review_file(
                review_dir,
                slug="dry-run-note",
                note_id="source/dry-run-note",
                title="Dry Run Note",
            )
            approve_review_item(cfg, review_path.name, dry_run=True)
            # Review file still exists
            self.assertTrue(review_path.exists())
            # Final path was NOT created
            expected = cfg.paths.vault_root / "wiki" / "sources" / "dry-run-note.md"
            self.assertFalse(expected.exists())

    def test_reject_deletes_file(self):
        """Reject → review file gone."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            review_path = _make_review_file(
                review_dir,
                slug="reject-me",
                note_id="source/reject-me",
                title="Reject Me",
            )
            reject_review_item(cfg, review_path.name)
            self.assertFalse(review_path.exists())

    def test_reject_dry_run_no_changes(self):
        """dry_run=True → review file not deleted."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            review_path = _make_review_file(
                review_dir,
                slug="dry-reject",
                note_id="source/dry-reject",
                title="Dry Reject",
            )
            reject_review_item(cfg, review_path.name, dry_run=True)
            self.assertTrue(review_path.exists())

    def test_list_filters_non_promotion_review_files(self):
        """Files without lifecycle_state:review or ## Promotion Review are excluded."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"

            # Valid promotion review
            _make_review_file(
                review_dir,
                slug="real-review",
                note_id="source/real-review",
                title="Real Review",
            )

            # Maintenance verification-review file (wrong type, no lifecycle_state, no header)
            verification_file = review_dir / "stale-concept-something-2026-05-08.md"
            verification_file.write_text(
                "---\nid: concept/something\ntype: verification_review\ntitle: Something\n---\n\n## Context\n\nSome stale check.\n",
                encoding="utf-8",
            )

            # Promotion-review-like file but wrong lifecycle_state
            wrong_state = review_dir / "wrong-state-2026-05-08.md"
            wrong_state.write_text(
                "---\nid: source/wrong\ntype: source\ntitle: Wrong\nlifecycle_state: verified\n---\n\n## Promotion Review\n\nReason: wiki_conflict\n",
                encoding="utf-8",
            )

            items = list_review_items(cfg)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].title, "Real Review")

    def test_approve_db_failure_cleans_tmp_and_preserves_review_file(self):
        """DB failure during approve → tmp deleted, review file intact."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            review_path = _make_review_file(
                review_dir,
                slug="atomicity-test",
                note_id="source/atomicity-test",
                title="Atomicity Test",
            )

            failing_conn = MagicMock()
            failing_conn.__enter__ = MagicMock(return_value=failing_conn)
            failing_conn.__exit__ = MagicMock(return_value=False)
            failing_conn.execute.side_effect = RuntimeError("simulated DB failure")

            with patch("second_brain.promotion.review_ops.connect_db", return_value=failing_conn):
                with patch("second_brain.promotion.review_ops.run_migrations"):
                    with self.assertRaises(RuntimeError):
                        approve_review_item(cfg, review_path.name)

            # Review file must still exist
            self.assertTrue(review_path.exists())
            # No .tmp file left behind
            expected_final = cfg.paths.vault_root / "wiki" / "sources" / "atomicity-test.md"
            self.assertFalse(expected_final.with_suffix(".tmp").exists())
            self.assertFalse(expected_final.exists())


if __name__ == "__main__":
    unittest.main()
