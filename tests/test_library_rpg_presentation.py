from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import textwrap
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mnemonicos_game.data.loader import BrainLoader
from mnemonicos_game.runtime import GameRuntime, PlayerState
from mnemonicos_game.scene_helpers import move_with_collision, update_focus
from mnemonicos_game.services.digest import build_indexes
from mnemonicos_game.settings import GameSettings
from mnemonicos_game.ui.minimap import compute_minimap_rects
from mnemonicos_game.world.tilemap import build_dungeon_layout, build_library_layout
from mnemonicos_game.main import copy_overlay, find_anchor, open_shelf_browser, shelf_item_rects


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


class LibraryRpgPresentationTest(unittest.TestCase):
    def test_library_starts_just_inside_bottom_entrance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            layout = build_library_layout(snapshot)
            entrance = next(obj for obj in layout.objects if obj.kind == "entrance")
            self.assertGreater(layout.player_start[1], 900)
            self.assertGreater(entrance.y, 990)
            self.assertLess(abs(layout.player_start[0] - entrance.x), 120)

    def test_system_zone_uses_bulletin_wall_near_entrance_daemon_cluster(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            layout = build_library_layout(snapshot)
            entrance = next(obj for obj in layout.objects if obj.object_id == "entrance:library")
            daemon = next(obj for obj in layout.objects if obj.object_id == "daemon:counter")
            board = next(obj for obj in layout.objects if obj.object_id == "daemon:board")
            system_wall = next(obj for obj in layout.objects if obj.section_key == "system-wall")
            self.assertEqual(system_wall.kind, "bulletin-wall")
            self.assertLess(system_wall.x, daemon.x)
            self.assertGreater(system_wall.height, system_wall.width)
            self.assertGreater(daemon.y, 760)
            self.assertGreater(board.y, 700)
            self.assertEqual((board.width, board.height), (64, 128))
            self.assertLess(abs(daemon.x - entrance.x), 420)
            self.assertLess(abs(board.x - daemon.x), 320)

    def test_library_shelves_form_three_aligned_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            layout = build_library_layout(snapshot)
            shelves = [obj for obj in layout.objects if obj.kind == "shelf"]
            row_levels = sorted({obj.y for obj in shelves})
            self.assertEqual(len(row_levels), 3)
            self.assertTrue(all(obj.x >= 560 for obj in shelves))
            self.assertTrue(any(obj.reserved for obj in shelves))
            self.assertTrue(all(not obj.label for obj in shelves if obj.reserved))
            self.assertTrue(all(obj.width == 240 for obj in shelves if obj.reserved))

    def test_library_shelf_columns_do_not_horizontally_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            layout = build_library_layout(snapshot)
            shelves = [obj for obj in layout.objects if obj.kind == "shelf"]
            by_row: dict[int, list[object]] = {}
            for shelf in shelves:
                by_row.setdefault(shelf.y, []).append(shelf)
            for row_shelves in by_row.values():
                ordered = sorted(row_shelves, key=lambda shelf: shelf.x)
                for left, right in zip(ordered, ordered[1:]):
                    self.assertLessEqual(left.x + left.width + 40, right.x)

    def test_vault_stairs_are_behind_daemon_counter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            layout = build_library_layout(snapshot)
            daemon = next(obj for obj in layout.objects if obj.object_id == "daemon:counter")
            stairs = next(obj for obj in layout.objects if obj.object_id == "portal:vault")
            self.assertEqual(stairs.kind, "stairs")
            self.assertLess(stairs.y, daemon.y)
            self.assertLess(abs(stairs.x - daemon.x), 80)

    def test_minimap_rects_include_player_and_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            runtime = GameRuntime(
                settings=GameSettings(workspace_root=workspace),
                snapshot=snapshot,
                indexes=build_indexes(snapshot),
                scenes={"library": build_library_layout(snapshot), "vault": build_dungeon_layout(snapshot)},
                current_scene="library",
                player=PlayerState(150, 110),
            )
            rects = compute_minimap_rects(runtime, (0, 0, 160, 100))
            self.assertIn("player", rects)
            self.assertTrue(any(key.startswith("shelf:") for key in rects))

    def test_hud_state_defaults_to_not_moving(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            runtime = GameRuntime(
                settings=GameSettings(workspace_root=workspace),
                snapshot=snapshot,
                indexes=build_indexes(snapshot),
                scenes={"library": build_library_layout(snapshot), "vault": build_dungeon_layout(snapshot)},
                current_scene="library",
                player=PlayerState(140, 82),
            )
            self.assertFalse(runtime.player_is_moving)

    def test_spawn_is_not_inside_solid_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            layout = build_library_layout(snapshot)
            runtime = GameRuntime(
                settings=GameSettings(workspace_root=workspace),
                snapshot=snapshot,
                indexes=build_indexes(snapshot),
                scenes={"library": layout, "vault": build_dungeon_layout(snapshot)},
                current_scene="library",
                player=PlayerState(*layout.player_start),
            )
            fake_pygame = types.SimpleNamespace(Rect=_rect_factory)
            moved_x, moved_y = move_with_collision(runtime, runtime.player.x, runtime.player.y, 20, 0, fake_pygame)
            self.assertNotEqual((moved_x, moved_y), layout.player_start)

    def test_system_bulletin_wall_can_receive_focus_from_nearby_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            layout = build_library_layout(snapshot)
            wall = next(obj for obj in layout.objects if obj.section_key == "system-wall")
            runtime = GameRuntime(
                settings=GameSettings(workspace_root=workspace),
                snapshot=snapshot,
                indexes=build_indexes(snapshot),
                scenes={"library": layout, "vault": build_dungeon_layout(snapshot)},
                current_scene="library",
                player=PlayerState(wall.x + wall.width + 22, wall.y + 32),
            )
            update_focus(runtime)
            self.assertEqual(runtime.nearest_object_id, wall.object_id)

    def test_anchor_markers_are_tiny_and_not_world_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            layout = build_library_layout(snapshot)
            anchors = [obj for obj in layout.objects if obj.kind == "anchor"]
            self.assertTrue(anchors)
            self.assertTrue(all(obj.width <= 24 and obj.height <= 24 for obj in anchors))

    def test_portals_target_stair_anchors_not_scene_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            library = build_library_layout(snapshot)
            vault = build_dungeon_layout(snapshot)
            runtime = GameRuntime(
                settings=GameSettings(workspace_root=workspace),
                snapshot=snapshot,
                indexes=build_indexes(snapshot),
                scenes={"library": library, "vault": vault},
                current_scene="library",
                player=PlayerState(*library.player_start),
            )
            down = next(obj for obj in library.objects if obj.object_id == "portal:vault")
            up = next(obj for obj in vault.objects if obj.object_id == "portal:library")
            self.assertEqual(down.target_anchor_id, "anchor:vault-return")
            self.assertEqual(up.target_anchor_id, "anchor:library-return")
            runtime.current_scene = "vault"
            vault_anchor = find_anchor(runtime, "anchor:vault-return")
            self.assertIsNotNone(vault_anchor)
            self.assertEqual(vault_anchor.kind, "anchor")
            self.assertLessEqual(vault_anchor.width, 24)
            self.assertLessEqual(vault_anchor.x, 96)
            self.assertGreaterEqual(vault_anchor.y, vault.height - 120)
            runtime.current_scene = "library"
            library_anchor = find_anchor(runtime, "anchor:library-return")
            self.assertIsNotNone(library_anchor)
            self.assertLessEqual(library_anchor.width, 24)

    def test_vault_start_is_near_visible_archive_area(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            vault = build_dungeon_layout(snapshot)
            self.assertLess(vault.player_start[1], 1100)

    def test_vault_stairs_sit_in_bottom_left_corner_with_tile_margin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            vault = build_dungeon_layout(snapshot)
            stairs = next(obj for obj in vault.objects if obj.object_id == "portal:library")
            self.assertEqual(stairs.x, 32)
            self.assertEqual(stairs.y, vault.height - 32 - stairs.height)
            self.assertEqual(stairs.label, "")

    def test_opening_shelf_browser_lists_section_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace)
            snapshot = BrainLoader(workspace).load()
            runtime = GameRuntime(
                settings=GameSettings(workspace_root=workspace),
                snapshot=snapshot,
                indexes=build_indexes(snapshot),
                scenes={"library": build_library_layout(snapshot), "vault": build_dungeon_layout(snapshot)},
                current_scene="library",
                player=PlayerState(150, 110),
            )
            open_shelf_browser(runtime, "concepts")
            self.assertEqual(runtime.overlay.mode, "shelf")
            self.assertTrue(runtime.overlay.browser_items)
            self.assertEqual(runtime.overlay.browser_items[0].kind, "entry")

    def test_shelf_browser_scroll_limits_visible_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_workspace(workspace, extra_concepts=12)
            snapshot = BrainLoader(workspace).load()
            runtime = GameRuntime(
                settings=GameSettings(workspace_root=workspace),
                snapshot=snapshot,
                indexes=build_indexes(snapshot),
                scenes={"library": build_library_layout(snapshot), "vault": build_dungeon_layout(snapshot)},
                current_scene="library",
                player=PlayerState(150, 110),
            )
            open_shelf_browser(runtime, "concepts")
            runtime.overlay.browser_scroll = 4
            fake_pygame = types.SimpleNamespace(Rect=_rect_factory)
            rows = shelf_item_rects(runtime, fake_pygame.Rect(120, 80, 1000, 500), fake_pygame)
            self.assertLessEqual(len(rows), 8)
            self.assertEqual(rows[0][0].label, runtime.overlay.browser_items[4].label)

    def test_copy_overlay_preserves_daemon_state_for_back_navigation(self) -> None:
        original = copy_overlay(
            type("O", (), {
                "mode": "daemon",
                "title": "Daemon Counter",
                "body": "Choose a bounded service.",
                "scroll": 0,
                "menu_index": 2,
                "picker_index": 0,
                "picker_candidates": [],
                "browser_items": [],
                "browser_section_key": None,
                "browser_section_label": "",
            })()
        )
        self.assertEqual(original.mode, "daemon")
        self.assertEqual(original.menu_index, 2)


def _seed_workspace(workspace: Path, *, extra_concepts: int = 0) -> None:
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
    for index in range(extra_concepts):
        _write(
            workspace / f"brain/wiki/concepts/extra-{index}.md",
            f"""
            ---
            id: concept/extra-{index}
            type: concept
            title: Extra Concept {index}
            updated_at: 2026-04-16
            created_at: 2026-04-16
            source_refs: []
            confidence: medium
            ---

            Extra concept {index}.
            """,
        )


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
