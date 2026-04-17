from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import textwrap
import time
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mnemonicos_game.data.loader import BrainLoader
from mnemonicos_game.data.watcher import WorldWatcher
from mnemonicos_game.entities.daemon_npc import daemon_effective_state, daemon_state_label, daemon_status_line
from mnemonicos_game.runtime import GameRuntime, PlayerState
from mnemonicos_game.scene_helpers import choose_primary_thread_target, move_with_collision, scene_risk_profile
from mnemonicos_game.services.digest import build_indexes
from mnemonicos_game.main import handle_inspector_keys, maybe_refresh_live_overlay, run_board_action, run_board_maintenance_action, set_daemon_state
from mnemonicos_game.ui.markdown_renderer import render_lines
from mnemonicos_game.world.tilemap import LayoutObject, build_library_layout
from mnemonicos_game.world.routing import related_section_keys
from mnemonicos_game.scene_helpers import decompose_triangular_piles


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


class LibraryRpgMechanicsTest(unittest.TestCase):
    def test_watcher_detects_markdown_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            watcher = WorldWatcher(workspace)
            watcher.prime()
            time.sleep(0.002)
            _write(workspace / "brain/wiki/concepts/library-rpg.md", """
            ---
            id: concept/library-rpg
            type: concept
            title: Library RPG Updated
            updated_at: 2026-04-16
            created_at: 2026-04-16
            source_refs: []
            confidence: medium
            ---

            Exploration-first interface.
            """)
            self.assertTrue(watcher.poll_changed())

    def test_library_layout_adds_in_world_link_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            layout = build_library_layout(snapshot)
            marker_objects = [obj for obj in layout.objects if obj.kind == "marker"]
            self.assertTrue(marker_objects)
            self.assertTrue(any(obj.section_key == "procedures:active" for obj in marker_objects))

    def test_markdown_renderer_keeps_headings_bullets_and_code(self) -> None:
        lines = render_lines(
            "# Title\n\n- first bullet\n```py\nprint('hi')\n```\nBody text",
            40,
        )
        self.assertEqual(lines[0].style, "heading1")
        self.assertTrue(any(line.style == "bullet" for line in lines))
        self.assertTrue(any(line.style == "code" for line in lines))

    def test_collision_blocks_player_from_solid_shelf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            layout = build_library_layout(snapshot)
            scenes = {"library": layout}
            target_shelf = next(obj for obj in layout.objects if obj.kind == "shelf" and not obj.reserved)
            start_x = target_shelf.x - 40
            start_y = target_shelf.y + 10
            runtime = GameRuntime(
                settings=types.SimpleNamespace(window_width=1280, window_height=720, workspace_root=workspace),
                snapshot=snapshot,
                indexes=build_indexes(snapshot),
                scenes=scenes,
                current_scene="library",
                player=PlayerState(start_x, start_y),
            )
            fake_pygame = types.SimpleNamespace(Rect=_rect_factory)
            new_x, new_y = move_with_collision(runtime, runtime.player.x, runtime.player.y, 80, 0, fake_pygame)
            self.assertEqual((new_x, new_y), (start_x, start_y))

    def test_related_section_keys_prioritize_neighboring_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            entry = next(item for item in snapshot.note_entries if item.note_id == "procedure/open-daemon-counter")
            sections = related_section_keys(entry, snapshot)
            self.assertIn("concepts", sections)
            self.assertIn("incidents", sections)

    def test_daemon_status_line_reflects_review_and_focus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            _write(
                workspace / "brain/wiki/review/recheck-daemon.md",
                """
                ---
                id: review/recheck-daemon
                type: review
                title: Recheck Daemon
                updated_at: 2026-04-16
                created_at: 2026-04-16
                source_refs: []
                confidence: medium
                ---

                Review the daemon routing.
                """,
            )
            (workspace / "brain/data").mkdir(parents=True, exist_ok=True)
            (workspace / "brain/data/mnemonic.pid").write_text("123\n")
            snapshot = BrainLoader(workspace).load()
            self.assertIn("Reviewing", daemon_status_line(workspace, snapshot))
            self.assertIn("Library RPG", daemon_status_line(workspace, snapshot, "Library RPG"))

    def test_follow_thread_key_projects_related_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            runtime = GameRuntime(
                settings=types.SimpleNamespace(window_width=1280, window_height=720, workspace_root=workspace),
                snapshot=snapshot,
                indexes=build_indexes(snapshot),
                scenes={"library": build_library_layout(snapshot)},
                current_scene="library",
                player=PlayerState(100, 100),
            )
            runtime.focused_entry_id = "procedure/open-daemon-counter"
            fake_pygame = types.SimpleNamespace(K_DOWN=-1, K_s=-1, K_UP=-1, K_w=-1, K_b=-1, K_j=-1, K_f=70)
            handle_inspector_keys(runtime, fake_pygame.K_f, fake_pygame)
            self.assertEqual(runtime.follow_thread_source, "procedure/open-daemon-counter")
            self.assertTrue(runtime.follow_thread_sections)
            self.assertEqual(runtime.daemon_state, "guiding")

    def test_daemon_state_label_prefers_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "brain/data").mkdir(parents=True, exist_ok=True)
            (workspace / "brain/data/mnemonic.pid").write_text("123\n")
            runtime = types.SimpleNamespace(
                daemon_state="guiding",
                daemon_state_until=time.time() + 10,
                follow_thread_sections=[],
                snapshot=types.SimpleNamespace(zone=lambda _key: types.SimpleNamespace(item_count=0)),
                settings=types.SimpleNamespace(workspace_root=workspace),
            )
            self.assertEqual(daemon_state_label(runtime, "Maintaining the stacks"), "Tracing a thread")
            set_daemon_state(runtime, "consulting", 1.0)
            self.assertEqual(runtime.daemon_state, "consulting")

    def test_notice_board_status_action_opens_formatted_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            _write(
                workspace / "config/memory.toml",
                f"""
                [paths]
                vault_root = "{(workspace / 'brain').as_posix()}"
                db_path = "{(workspace / 'brain/data/archive.db').as_posix()}"
                vectors_dir = "{(workspace / 'brain/data/vectors').as_posix()}"
                graph_dir = "{(workspace / 'brain/data/graph').as_posix()}"
                eval_queries = "{(workspace / 'evals/queries.jsonl').as_posix()}"

                [daemon]
                socket_path = "{(workspace / 'brain/data/mnemonic.sock').as_posix()}"
                pid_path = "{(workspace / 'brain/data/mnemonic.pid').as_posix()}"
                log_path = "{(workspace / 'brain/logs/daemon.log').as_posix()}"
                """,
            )
            snapshot = BrainLoader(workspace).load()
            runtime = GameRuntime(
                settings=types.SimpleNamespace(window_width=1280, window_height=720, workspace_root=workspace),
                snapshot=snapshot,
                indexes=build_indexes(snapshot),
                scenes={"library": build_library_layout(snapshot)},
                current_scene="library",
                player=PlayerState(100, 100),
            )
            runtime.overlay.mode = "board"
            runtime.overlay.menu_index = 0
            run_board_action(runtime)
            self.assertEqual(runtime.overlay.title, "Daemon Status")
            self.assertIn("Daemon:", runtime.overlay.body)

    def test_notice_board_maintenance_menu_runs_status_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            _write(
                workspace / "config/memory.toml",
                f"""
                [paths]
                vault_root = "{(workspace / 'brain').as_posix()}"
                db_path = "{(workspace / 'brain/data/archive.db').as_posix()}"
                vectors_dir = "{(workspace / 'brain/data/vectors').as_posix()}"
                graph_dir = "{(workspace / 'brain/data/graph').as_posix()}"
                eval_queries = "{(workspace / 'evals/queries.jsonl').as_posix()}"

                [daemon]
                socket_path = "{(workspace / 'brain/data/mnemonic.sock').as_posix()}"
                pid_path = "{(workspace / 'brain/data/mnemonic.pid').as_posix()}"
                log_path = "{(workspace / 'brain/logs/daemon.log').as_posix()}"
                """,
            )
            snapshot = BrainLoader(workspace).load()
            runtime = GameRuntime(
                settings=types.SimpleNamespace(window_width=1280, window_height=720, workspace_root=workspace),
                snapshot=snapshot,
                indexes=build_indexes(snapshot),
                scenes={"library": build_library_layout(snapshot)},
                current_scene="library",
                player=PlayerState(100, 100),
            )
            runtime.overlay.mode = "board"
            runtime.overlay.menu_index = 2
            run_board_action(runtime)
            self.assertEqual(runtime.overlay.mode, "board-maintenance")
            run_board_maintenance_action(runtime)
            self.assertEqual(runtime.overlay.title, "Run Status Report")
            self.assertIn("Daemon:", runtime.overlay.body)

    def test_live_overlay_refresh_updates_daemon_status_panel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            _write(
                workspace / "config/memory.toml",
                f"""
                [paths]
                vault_root = "{(workspace / 'brain').as_posix()}"
                db_path = "{(workspace / 'brain/data/archive.db').as_posix()}"
                vectors_dir = "{(workspace / 'brain/data/vectors').as_posix()}"
                graph_dir = "{(workspace / 'brain/data/graph').as_posix()}"
                eval_queries = "{(workspace / 'evals/queries.jsonl').as_posix()}"

                [daemon]
                socket_path = "{(workspace / 'brain/data/mnemonic.sock').as_posix()}"
                pid_path = "{(workspace / 'brain/data/mnemonic.pid').as_posix()}"
                log_path = "{(workspace / 'brain/logs/daemon.log').as_posix()}"
                """,
            )
            snapshot = BrainLoader(workspace).load()
            runtime = GameRuntime(
                settings=types.SimpleNamespace(window_width=1280, window_height=720, workspace_root=workspace),
                snapshot=snapshot,
                indexes=build_indexes(snapshot),
                scenes={"library": build_library_layout(snapshot)},
                current_scene="library",
                player=PlayerState(100, 100),
            )
            runtime.overlay = types.SimpleNamespace(mode="inspect", title="Daemon Status", body="stale body")
            runtime.overlay_live_refreshed_at = 0.0
            maybe_refresh_live_overlay(runtime)
            self.assertIn("Daemon:", runtime.overlay.body)

    def test_triangular_pile_decomposition_uses_minimum_piles(self) -> None:
        self.assertEqual(decompose_triangular_piles(10), [4])
        self.assertEqual(decompose_triangular_piles(11), [4, 1])
        self.assertEqual(decompose_triangular_piles(20), [4, 4])

    def test_daemon_effective_state_falls_back_to_reviewing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            _write(
                workspace / "brain/wiki/review/recheck-daemon.md",
                """
                ---
                id: review/recheck-daemon
                type: review
                title: Recheck Daemon
                updated_at: 2026-04-16
                created_at: 2026-04-16
                source_refs: []
                confidence: medium
                ---

                Review the daemon routing.
                """,
            )
            (workspace / "brain/data").mkdir(parents=True, exist_ok=True)
            (workspace / "brain/data/mnemonic.pid").write_text("123\n")
            snapshot = BrainLoader(workspace).load()
            runtime = GameRuntime(
                settings=types.SimpleNamespace(window_width=1280, window_height=720, workspace_root=workspace),
                snapshot=snapshot,
                indexes=build_indexes(snapshot),
                scenes={"library": build_library_layout(snapshot)},
                current_scene="library",
                player=PlayerState(100, 100),
            )
            self.assertEqual(daemon_effective_state(runtime), "reviewing")

    def test_scene_risk_profile_counts_review_and_contested_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            _write(
                workspace / "brain/wiki/incidents/contested-memory.md",
                """
                ---
                id: incident/contested-memory
                type: incident
                title: Contested Memory
                updated_at: 2024-01-01
                created_at: 2024-01-01
                source_refs: []
                confidence:
                lifecycle_state: contested
                ---

                This memory is disputed.
                """,
            )
            _write(
                workspace / "brain/wiki/review/recheck-daemon.md",
                """
                ---
                id: review/recheck-daemon
                type: review
                title: Recheck Daemon
                updated_at: 2026-04-16
                created_at: 2026-04-16
                source_refs: []
                confidence: medium
                ---

                Review the daemon routing.
                """,
            )
            snapshot = BrainLoader(workspace).load()
            runtime = GameRuntime(
                settings=types.SimpleNamespace(window_width=1280, window_height=720, workspace_root=workspace),
                snapshot=snapshot,
                indexes=build_indexes(snapshot),
                scenes={"library": build_library_layout(snapshot)},
                current_scene="library",
                player=PlayerState(100, 100),
            )
            profile = scene_risk_profile(runtime)
            self.assertGreaterEqual(profile["review"], 1)
            self.assertGreaterEqual(profile["contested"], 1)
            self.assertGreaterEqual(profile["unknown"], 1)

    def test_choose_primary_thread_target_prefers_nearest_section(self) -> None:
        runtime = types.SimpleNamespace(player=PlayerState(100, 100))
        far = LayoutObject("a", "Far", "shelf", 600, 600, 100, 100, "library")
        near = LayoutObject("b", "Near", "shelf", 150, 140, 100, 100, "library")
        choice = choose_primary_thread_target(runtime, [far, near])
        self.assertEqual(choice.object_id, "b")


class _Rect:
    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self.x = x
        self.y = y
        self.width = width
        self.height = height

    def colliderect(self, other: "_Rect") -> bool:
        return not (
            self.x + self.width <= other.x
            or other.x + other.width <= self.x
            or self.y + self.height <= other.y
            or other.y + other.height <= self.y
        )


def _rect_factory(x: int, y: int, width: int, height: int) -> _Rect:
    return _Rect(x, y, width, height)


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

        ## Steps

        - Open the counter.
        ```py
        print("daemon")
        ```
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
