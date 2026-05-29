from __future__ import annotations

from dataclasses import dataclass, field

from mnemonicos_game.data.models import GameBook, GameEntry, WorldSnapshot, ZoneSection
from mnemonicos_game.world.routing import related_note_ids


@dataclass(slots=True)
class LayoutObject:
    object_id: str
    label: str
    kind: str
    x: int
    y: int
    width: int
    height: int
    scene_name: str
    section_key: str | None = None
    note_id: str | None = None
    session_slug: str | None = None
    target_scene: str | None = None
    target_anchor_id: str | None = None
    solid: bool = False
    reserved: bool = False
    bulk_count: int = 0   # entries represented by a crate object (kind="crate")

    @property
    def rect_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)


@dataclass(slots=True)
class SceneLayout:
    name: str
    width: int
    height: int
    player_start: tuple[int, int]
    objects: list[LayoutObject] = field(default_factory=list)


def build_library_layout(snapshot: WorldSnapshot) -> SceneLayout:
    width = 1880
    height = 1120
    objects: list[LayoutObject] = []
    shelf_row_y = [320, 570, 820]
    system_section: ZoneSection | None = None

    hall_sections: list[tuple[str, ZoneSection]] = []
    for zone_key in ("concepts", "procedures", "incidents", "review", "journals", "system", "reserved"):
        zone = snapshot.zone(zone_key)
        for section in zone.sections:
            if zone_key == "system":
                system_section = section
            else:
                hall_sections.append((zone_key, section))

    for index, (zone_key, section) in enumerate(hall_sections):
        col = index // len(shelf_row_y)
        row = index % len(shelf_row_y)
        x = 560 + col * 330
        y = shelf_row_y[row]
        shelf_width = 240
        shelf_height = 132
        under_construction = section.reserved or section.under_construction
        object_kind = "shelf"
        objects.append(
            LayoutObject(
                object_id=f"shelf:{section.key}",
                label="" if under_construction else f"{section.label} ({len(section.entries)})",
                kind=object_kind,
                x=x,
                y=y,
                width=shelf_width,
                height=shelf_height,
                scene_name="library",
                section_key=section.key,
                reserved=under_construction,
                solid=True,
            )
        )
        if section.entries:
            # Span the full shelf inner area so draw_scroll_pile can fill
            # all three board levels (matches draw_bookshelf geometry:
            # side_w=12, top_depth=10+4=14, bottom_margin=14).
            inner_x = x + 12
            inner_y = y + 14
            inner_w = shelf_width - 24
            inner_h = shelf_height - 28
            objects.append(
                LayoutObject(
                    object_id=f"entry-pile:{section.key}",
                    label=section.label,
                    kind="entry-pile",
                    x=inner_x,
                    y=inner_y,
                    width=inner_w,
                    height=inner_h,
                    scene_name="library",
                    section_key=section.key,
                )
            )
            if any(related_note_ids(entry, snapshot) for entry in section.entries):
                objects.append(
                    LayoutObject(
                        object_id=f"marker:{section.key}",
                        label="Links",
                        kind="marker",
                        x=inner_x + inner_w - 18,
                        y=inner_y - 10,
                        width=12,
                        height=12,
                        scene_name="library",
                        section_key=section.key,
                    )
                )

            # ── Overflow crates (1 crate = 1 000 entries) ────────────────────
            # Arranged in a 2-col × 3-row grid in the gap to the right of the
            # shelf (columns are 330 px apart, shelf_width=240 → 90 px gap).
            _CRATE_SIZE = 100   # entries per crate
            n_crates = min(6, len(section.entries) // _CRATE_SIZE)
            if n_crates:
                crate_w, crate_h = 32, 32
                gap = 6
                crate_col_step = crate_w + gap          # 38 px
                crate_row_step = crate_h + gap          # 38 px
                crate_origin_x = x + shelf_width + 8   # just right of shelf
                crate_origin_y = y + shelf_height - crate_h  # bottom-align
                for ci in range(n_crates):
                    col = ci % 2
                    row = ci // 2
                    cx = crate_origin_x + col * crate_col_step
                    cy = crate_origin_y - row * crate_row_step
                    objects.append(
                        LayoutObject(
                            object_id=f"crate:{section.key}:{ci}",
                            label="×100",
                            kind="crate",
                            x=cx,
                            y=cy,
                            width=crate_w,
                            height=crate_h,
                            scene_name="library",
                            section_key=section.key,
                            bulk_count=_CRATE_SIZE,
                        )
                    )

    if system_section is not None:
        # Keep the system wall in the left-side daemon cluster so it remains visible
        # near spawn and distinct from the daemon notice board.
        wall_x = 44
        wall_y = 670
        wall_width = 88
        wall_height = 206
        objects.append(
            LayoutObject(
                object_id=f"shelf:{system_section.key}",
                label="RULES",
                kind="bulletin-wall",
                x=wall_x,
                y=wall_y,
                width=wall_width,
                height=wall_height,
                scene_name="library",
                section_key=system_section.key,
                solid=True,
            )
        )
        note_positions = [
            (wall_x + 12, wall_y + 36),
            (wall_x + 44, wall_y + 30),
            (wall_x + 14, wall_y + 92),
            (wall_x + 42, wall_y + 98),
        ]
        for note_index, (note_x, note_y) in enumerate(note_positions):
            objects.append(
                LayoutObject(
                    object_id=f"bulletin-note:{system_section.key}:{note_index}",
                    label="",
                    kind="bulletin-note",
                    x=note_x,
                    y=note_y,
                    width=24,
                    height=28,
                    scene_name="library",
                    section_key=system_section.key,
                )
            )

    # ── Stone pillars (4) — flank the shelf hall ─────────────────────────────
    pillar_w, pillar_h = 24, 120
    for pillar_index, (px, py) in enumerate([
        (486, 290), (486, 560), (486, 830),      # left column
        (1758, 290), (1758, 560), (1758, 830),   # right column
    ]):
        objects.append(
            LayoutObject(
                object_id=f"pillar:{pillar_index}",
                label="",
                kind="pillar",
                x=px,
                y=py,
                width=pillar_w,
                height=pillar_h,
                scene_name="library",
                solid=True,
            )
        )

    # ── Wall torches (6) — evenly spaced on back wall ─────────────────────────
    torch_w, torch_h = 16, 28
    torch_y = 52
    for torch_index, tx in enumerate([220, 540, 860, 1100, 1420, 1740]):
        objects.append(
            LayoutObject(
                object_id=f"torch:{torch_index}",
                label="",
                kind="torch",
                x=tx,
                y=torch_y,
                width=torch_w,
                height=torch_h,
                scene_name="library",
                solid=False,
            )
        )

    # ── Far-back decorative shelves (depth illusion) ──────────────────────────
    for fs_index, (fsx, fsy) in enumerate([
        (300, 90), (700, 80), (1100, 92), (1500, 85),
    ]):
        objects.append(
            LayoutObject(
                object_id=f"far-shelf:{fs_index}",
                label="",
                kind="far-shelf",
                x=fsx,
                y=fsy,
                width=110,
                height=56,
                scene_name="library",
                solid=False,
            )
        )

    objects.extend(
        [
            LayoutObject(
                object_id="entrance:library",
                label="Grand Entrance",
                kind="entrance",
                x=80,
                y=1020,
                width=220,
                height=80,
                scene_name="library",
            ),
            LayoutObject(
                object_id="anchor:library-return",
                label="Library Stair Landing",
                kind="anchor",
                x=240,
                y=726,
                width=16,
                height=16,
                scene_name="library",
            ),
            LayoutObject(
                object_id="daemon:counter",
                label="Daemon Counter",
                kind="daemon",
                x=150,
                y=810,
                width=230,
                height=86,
                scene_name="library",
                solid=True,
            ),
            LayoutObject(
                object_id="daemon:board",
                label="Daemon Notice Board",
                kind="board",
                x=402,
                y=734,
                width=64,
                height=128,
                scene_name="library",
                solid=True,
            ),
            LayoutObject(
                object_id="portal:vault",
                label="",
                kind="stairs",
                x=220,
                y=642,
                width=64,
                height=72,
                scene_name="library",
                target_scene="vault",
                target_anchor_id="anchor:vault-return",
            ),
        ]
    )
    return SceneLayout(name="library", width=width, height=height, player_start=(150, 955), objects=objects)


def build_dungeon_layout(snapshot: WorldSnapshot) -> SceneLayout:
    width = 1600
    height = 1050
    objects: list[LayoutObject] = []
    section_x = [100, 420, 740, 1060]
    top_y = 360
    section_height = 200
    vault_zone = snapshot.zone("vault")

    for index, section in enumerate(vault_zone.sections):
        col = index % len(section_x)
        row = index // len(section_x)
        x = section_x[col]
        y = top_y + row * 220
        under_construction = section.reserved or section.under_construction
        objects.append(
            LayoutObject(
                object_id=f"vault-shelf:{section.key}",
                label="" if under_construction else section.label,
                kind="vault-shelf",
                x=x,
                y=y,
                width=260,
                height=section_height,
                scene_name="vault",
                section_key=section.key,
                reserved=under_construction,
                solid=True,
            )
        )
        item_x = x + 14
        item_y = y + 40
        for item_index, book in enumerate(section.books[:12]):
            book_x = item_x + (item_index % 6) * 36
            book_y = item_y + (item_index // 6) * 54
            objects.append(
                LayoutObject(
                    object_id=f"book:{book.session_slug}",
                    label=book.title,
                    kind="book",
                    x=book_x,
                    y=book_y,
                    width=24,
                    height=40,
                    scene_name="vault",
                    section_key=section.key,
                    session_slug=book.session_slug,
                )
            )

    objects.append(
        LayoutObject(
            object_id="anchor:vault-return",
            label="Vault Stair Landing",
            kind="anchor",
            x=64,
            y=972,
            width=16,
            height=16,
            scene_name="vault",
        )
    )

    objects.append(
        LayoutObject(
            object_id="portal:library",
            label="",
            kind="stairs",
            x=32,
            y=946,
            width=64,
            height=72,
            scene_name="vault",
            target_scene="library",
            target_anchor_id="anchor:library-return",
        )
    )
    return SceneLayout(name="vault", width=width, height=height, player_start=(240, 836), objects=objects)
