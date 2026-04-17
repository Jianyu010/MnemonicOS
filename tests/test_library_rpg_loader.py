from __future__ import annotations

from pathlib import Path
import io
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mnemonicos_game.data.loader import BrainLoader
from mnemonicos_game.main import main
from mnemonicos_game.services.digest import build_indexes


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


class LibraryRpgLoaderTest(unittest.TestCase):
    def test_loader_builds_procedure_wing_and_reserved_zones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _write(workspace / "brain/system/MEMORY.md", "# MEMORY\n")
            _write(workspace / "brain/system/USER.md", "# USER\n")
            _write(workspace / "brain/system/ACTIVE.md", "# ACTIVE\n")
            _write(workspace / "brain/system/AGENTS.md", "bridges: {}\n")
            _write(
                workspace / "brain/wiki/concepts/library-rpg.md",
                """
                ---
                id: concept/library-rpg
                type: concept
                title: Library RPG
                updated_at: 2026-04-16
                created_at: 2026-04-16
                source_refs: []
                confidence: medium
                ---

                Exploration-first interface.
                """,
            )
            _write(
                workspace / "brain/wiki/procedures/active/open-daemon-counter.md",
                """
                ---
                id: procedure/open-daemon-counter
                type: procedure
                title: Open Daemon Counter
                updated_at: 2026-04-16
                created_at: 2026-04-16
                source_refs: [session/2026-04-16-120000-codex-task]
                confidence: high
                lifecycle_state: canonical
                ---

                Open the counter.
                """,
            )
            _write(
                workspace / "brain/wiki/procedures/drafts/daemon-digest.md",
                """
                ---
                id: procedure/daemon-digest
                type: procedure
                title: Daemon Digest Draft
                updated_at: 2026-04-16
                created_at: 2026-04-16
                source_refs: []
                confidence: medium
                lifecycle_state: draft
                ---

                Draft digest workflow.
                """,
            )
            _write(
                workspace / "brain/wiki/procedures/retired/old-index.md",
                """
                ---
                id: procedure/old-index
                type: procedure
                title: Old Index Routine
                updated_at: 2026-04-16
                created_at: 2026-04-16
                source_refs: []
                confidence: low
                lifecycle_state: archived
                ---

                Retired flow.
                """,
            )
            _write(
                workspace / "brain/raw/sessions/2026-04-16-120000-codex-task-build-library.md",
                """
                # Session

                Build the library RPG shell.
                """,
            )

            snapshot = BrainLoader(workspace).load()

            procedure_zone = snapshot.zone("procedures")
            labels = [section.label for section in procedure_zone.sections]
            self.assertEqual(
                labels,
                [
                    "Procedures Wing: Active",
                    "Procedures Wing: Drafts",
                    "Procedures Wing: Retired",
                ],
            )

            reserved_zone = snapshot.zone("reserved")
            reserved_labels = [section.label for section in reserved_zone.sections]
            self.assertIn("Decisions (Under Construction)", reserved_labels)
            self.assertIn("Sources (Under Construction)", reserved_labels)

            vault_zone = snapshot.zone("vault")
            vault_labels = [section.label for section in vault_zone.sections]
            self.assertEqual(
                vault_labels,
                [
                    "codex / 2026-04",
                    "Raw Assets",
                    "Raw Docs",
                    "Raw Sessions",
                    "Web Clips",
                ],
            )
            self.assertEqual(snapshot.zone("concepts").item_count, 1)
            indexes = build_indexes(snapshot)
            self.assertIn("system/memory", indexes.note_by_id)

    def test_cli_check_outputs_world_summary_without_pygame(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _write(workspace / "brain/system/MEMORY.md", "# MEMORY\n")
            _write(workspace / "brain/system/USER.md", "# USER\n")
            _write(workspace / "brain/system/ACTIVE.md", "# ACTIVE\n")
            _write(workspace / "brain/system/AGENTS.md", "bridges: {}\n")

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(["--root", str(workspace), "--check"])

            output = buffer.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("MenomicOS", output)
            self.assertIn("System Wall", output)
