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

from mnemonicos_game.data.loader import BrainLoader
from mnemonicos_game.services.digest import build_guidance, build_indexes, build_linked_digest, build_provenance, resolve_jump_candidates
from mnemonicos_game.world.tilemap import build_dungeon_layout, build_library_layout


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


class LibraryRpgWorldTest(unittest.TestCase):
    def test_layout_contains_daemon_portals_and_procedure_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()

            library = build_library_layout(snapshot)
            vault = build_dungeon_layout(snapshot)

            library_kinds = {obj.kind for obj in library.objects}
            self.assertIn("daemon", library_kinds)
            self.assertIn("stairs", library_kinds)
            self.assertTrue(any(obj.section_key == "procedures:active" for obj in library.objects))
            self.assertTrue(any(obj.section_key == "procedures:drafts" for obj in library.objects))
            self.assertTrue(any(obj.section_key == "procedures:retired" for obj in library.objects))
            self.assertTrue(any(obj.kind == "book" for obj in vault.objects))

    def test_digest_guidance_and_jump_resolution_use_local_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            indexes = build_indexes(snapshot)
            entry = indexes.note_by_id["procedure/open-daemon-counter"]

            digest = build_linked_digest(entry, snapshot, indexes)
            guidance = build_guidance(entry, snapshot, indexes)
            provenance = build_provenance(entry, snapshot, indexes)
            candidates, reason = resolve_jump_candidates(entry, snapshot, indexes)

            self.assertIn("Digest for Open Daemon Counter", digest)
            self.assertIn("Daemon Counter Incident", digest)
            self.assertIn("Suggested route", guidance)
            self.assertIn("session/2026-04-16-120000-codex-task-build-library", provenance)
            self.assertIsNone(reason)
            self.assertEqual(len(candidates), 1)


def _seed_workspace(workspace: Path) -> None:
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
        source_refs: [session/2026-04-16-120000-codex-task-build-library]
        confidence: medium
        tags: [library, daemon]
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
        source_refs: [session/2026-04-16-120000-codex-task-build-library]
        confidence: high
        lifecycle_state: canonical
        tags: [daemon, library]
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
        tags: [daemon]
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
        tags: [archive]
        ---

        Retired flow.
        """,
    )
    _write(
        workspace / "brain/wiki/incidents/daemon-counter-incident.md",
        """
        ---
        id: incident/daemon-counter-incident
        type: incident
        title: Daemon Counter Incident
        updated_at: 2026-04-16
        created_at: 2026-04-16
        source_refs: [session/2026-04-16-120000-codex-task-build-library]
        confidence: medium
        tags: [daemon, library]
        ---

        Counter blocked until cleared.
        """,
    )
    _write(
        workspace / "brain/raw/sessions/2026-04-16-120000-codex-task-build-library.md",
        """
        # Session

        Build the library RPG shell.
        """,
    )
