from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mnemonicos_game.assets.loader import load_assets
from mnemonicos_game.engine.animation import AnimationClock
from mnemonicos_game.main import build_ui_fonts, update_overlay_slide
from mnemonicos_game.runtime import GameRuntime, OverlayState, PlayerState
from mnemonicos_game.services.digest import build_indexes
from mnemonicos_game.settings import GameSettings
from mnemonicos_game.data.loader import BrainLoader
from mnemonicos_game.world.tilemap import build_library_layout, build_dungeon_layout


class LibraryRpgPhase10Test(unittest.TestCase):
    def test_animation_clock_advances_runtime_fields(self) -> None:
        runtime = type("Runtime", (), {"anim_tick": 0.0, "ambient_tick": 0.0, "anim_frame": 0})()
        clock = AnimationClock(frame_duration=0.1)
        clock.update(runtime, 0.35)
        self.assertGreater(runtime.anim_tick, 0.3)
        self.assertGreater(runtime.ambient_tick, 0.3)
        self.assertEqual(runtime.anim_frame, 3)

    def test_overlay_slide_moves_toward_target(self) -> None:
        runtime = type("Runtime", (), {"overlay": OverlayState(mode="inspect"), "overlay_slide": 0.0})()
        update_overlay_slide(runtime, 0.15)
        self.assertGreater(runtime.overlay_slide, 0.0)
        runtime.overlay = OverlayState()
        update_overlay_slide(runtime, 0.15)
        self.assertLessEqual(runtime.overlay_slide, 0.5)

    def test_asset_loader_builds_fallback_surfaces(self) -> None:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        import pygame

        pygame.init()
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                workspace = Path(tmp_dir)
                bundle = load_assets(workspace, pygame)
                self.assertIsNotNone(bundle.tile_floor)
                self.assertIsNotNone(bundle.tile_wall)
                self.assertTrue(bundle.scroll_sprites)
                self.assertTrue(bundle.player_frames)
                self.assertTrue(bundle.daemon_frames)
                self.assertTrue(bundle.portal_frames)
                self.assertIsNotNone(bundle.vignette)
        finally:
            pygame.quit()

    def test_runtime_accepts_phase10_asset_bundle(self) -> None:
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
                player=PlayerState(100, 100),
            )
            self.assertEqual(runtime.anim_frame, 0)
            self.assertEqual(runtime.player_facing, "south")
            self.assertIsNone(runtime.assets)

    def test_build_ui_fonts_scales_with_window_height(self) -> None:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        import pygame

        pygame.init()
        try:
            small = GameSettings(workspace_root=Path("/tmp"), window_height=720)
            large = GameSettings(workspace_root=Path("/tmp"), window_height=1080)
            _font1, small_font1, tiny_font1 = build_ui_fonts(small, pygame)
            _font2, small_font2, tiny_font2 = build_ui_fonts(large, pygame)
            self.assertGreater(small_font2.get_height(), small_font1.get_height())
            self.assertGreater(tiny_font2.get_height(), tiny_font1.get_height())
        finally:
            pygame.quit()


def _seed_workspace(workspace: Path) -> None:
    (workspace / "brain/system").mkdir(parents=True, exist_ok=True)
    (workspace / "brain/wiki/concepts").mkdir(parents=True, exist_ok=True)
    (workspace / "brain/raw/sessions").mkdir(parents=True, exist_ok=True)
    (workspace / "brain/system/MEMORY.md").write_text("# MEMORY\n")
    (workspace / "brain/system/USER.md").write_text("# USER\n")
    (workspace / "brain/system/ACTIVE.md").write_text("# ACTIVE\n")
    (workspace / "brain/system/AGENTS.md").write_text("bridges: {}\n")
    (workspace / "brain/wiki/concepts/library-rpg.md").write_text(
        "---\n"
        "id: concept/library-rpg\n"
        "type: concept\n"
        "title: Library RPG\n"
        "updated_at: 2026-04-16\n"
        "created_at: 2026-04-16\n"
        "source_refs: []\n"
        "confidence: medium\n"
        "---\n\n"
        "Exploration-first interface.\n"
    )
    (workspace / "brain/raw/sessions/2026-04-16-120000-codex-task-build-library.md").write_text("# Session\n")
