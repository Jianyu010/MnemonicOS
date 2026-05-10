from __future__ import annotations

import io
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
from second_brain.promotion.review_tui import (
    ReviewSessionResult,
    _find_conflict,
    _reason_label,
    run_interactive_review,
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


_REVIEW_TMPL = """\
---
id: {note_id}
type: {note_type}
title: {title}
lifecycle_state: review
created_at: '2026-05-08T09:00:00Z'
updated_at: '2026-05-08T09:00:00Z'
corroboration_count: 1
source_refs:
- candidate:1
---

## Promotion Review

Reason: {reason}

## Summary

{body}
"""


def _plant_review(review_dir: Path, slug: str, **kwargs) -> Path:
    review_dir.mkdir(parents=True, exist_ok=True)
    defaults = dict(
        note_id=f"source/{slug}",
        note_type="source",
        title=slug.replace("-", " ").title(),
        reason="wiki_conflict",
        body="Some content.",
    )
    defaults.update(kwargs)
    path = review_dir / f"{slug}-2026-05-08-09-00-00.md"
    path.write_text(_REVIEW_TMPL.format(**defaults).strip() + "\n", encoding="utf-8")
    return path


def _make_key_sequence(*keys: str):
    """Return a _read_key mock that returns keys in sequence then 'q'."""
    sequence = list(keys) + ["q"]
    call_count = [0]

    def _mock_read_key():
        idx = call_count[0]
        call_count[0] += 1
        if idx < len(sequence):
            return sequence[idx]
        return "q"

    return _mock_read_key


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class ReviewTUITest(unittest.TestCase):

    def test_reason_label_wiki_conflict(self):
        icon, label, style = _reason_label("wiki_conflict")
        self.assertEqual(icon, "⚠")
        self.assertIn("WIKI", label)

    def test_reason_label_unknown_defaults(self):
        icon, label, style = _reason_label("something_weird")
        self.assertEqual(icon, "?")

    def test_find_conflict_returns_none_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = _config(Path(tmp_dir))
            result = _find_conflict(cfg, "source/nonexistent")
            self.assertIsNone(result)

    def test_interactive_approve_removes_item(self):
        """Keypress 'a' approves the item; file moves out of review dir."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            review_path = _plant_review(review_dir, "test-approve-note")

            with (
                patch("sys.stdin") as mock_stdin,
                patch(
                    "second_brain.promotion.review_tui._read_key",
                    side_effect=_make_key_sequence("a"),
                ),
                patch("sys.stdin.isatty", return_value=True),
            ):
                mock_stdin.isatty.return_value = True
                result = run_interactive_review(cfg)

            self.assertEqual(result.approved, 1)
            self.assertEqual(result.rejected, 0)
            self.assertFalse(review_path.exists(), "review file should be gone after approve")
            final = cfg.paths.vault_root / "wiki" / "sources" / "test-approve-note.md"
            self.assertTrue(final.exists(), "promoted file should be in wiki/sources/")

    def test_interactive_reject_removes_item(self):
        """Keypress 'r' rejects the item; review file deleted."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            review_path = _plant_review(review_dir, "test-reject-note")

            with patch(
                "second_brain.promotion.review_tui._read_key",
                side_effect=_make_key_sequence("r"),
            ), patch("sys.stdin.isatty", return_value=True):
                result = run_interactive_review(cfg)

            self.assertEqual(result.rejected, 1)
            self.assertFalse(review_path.exists(), "review file should be deleted after reject")

    def test_interactive_skip_preserves_item(self):
        """Keypress 's' skips item; file remains; then 'q' exits."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            review_path = _plant_review(review_dir, "test-skip-note")

            with patch(
                "second_brain.promotion.review_tui._read_key",
                side_effect=_make_key_sequence("s", "q"),
            ), patch("sys.stdin.isatty", return_value=True):
                result = run_interactive_review(cfg)

            self.assertEqual(result.skipped, 1)
            self.assertTrue(review_path.exists(), "skipped item should remain")

    def test_interactive_quit_immediately(self):
        """Keypress 'q' with no action → all items skipped."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            _plant_review(review_dir, "test-quit-note")
            _plant_review(review_dir, "test-quit-note-2", note_id="source/test-quit-note-2")

            with patch(
                "second_brain.promotion.review_tui._read_key",
                side_effect=_make_key_sequence("q"),
            ), patch("sys.stdin.isatty", return_value=True):
                result = run_interactive_review(cfg)

            self.assertEqual(result.approved, 0)
            self.assertEqual(result.rejected, 0)

    def test_interactive_sequence_approve_reject_skip(self):
        """Process 3 items: approve, reject, skip."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            cfg = _config(workspace)
            review_dir = cfg.paths.vault_root / "wiki" / "review"
            p1 = _plant_review(review_dir, "ars-note-alpha",
                               note_id="source/ars-note-alpha", title="ARS Note Alpha")
            p2 = _plant_review(review_dir, "ars-note-beta",
                               note_id="source/ars-note-beta", title="ARS Note Beta")
            p3 = _plant_review(review_dir, "ars-note-gamma",
                               note_id="source/ars-note-gamma", title="ARS Note Gamma")

            with patch(
                "second_brain.promotion.review_tui._read_key",
                side_effect=_make_key_sequence("a", "r", "s"),
            ), patch("sys.stdin.isatty", return_value=True):
                result = run_interactive_review(cfg)

            self.assertEqual(result.approved, 1)
            self.assertEqual(result.rejected, 1)
            self.assertEqual(result.skipped, 1)
            self.assertFalse(p1.exists())  # approved → moved
            self.assertFalse(p2.exists())  # rejected → deleted
            self.assertTrue(p3.exists())   # skipped → intact

    def test_empty_queue_returns_immediately(self):
        """No review items → returns empty result without error."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = _config(Path(tmp_dir))
            with patch("sys.stdin.isatty", return_value=True):
                result = run_interactive_review(cfg)
            self.assertEqual(result.approved, 0)
            self.assertEqual(result.rejected, 0)
            self.assertEqual(result.skipped, 0)


if __name__ == "__main__":
    unittest.main()
