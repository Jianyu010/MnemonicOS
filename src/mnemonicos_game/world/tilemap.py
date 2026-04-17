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
        object_kind = "shelf"
        objects.append(
            LayoutObject(
                object_id=f"shelf:{section.key}",
                label="" if section.reserved else f"{section.label} ({len(section.entries)})",
                kind=object_kind,
                x=x,
                y=y,
                width=shelf_width,
                height=shelf_height,
                scene_name="library",
                section_key=section.key,
                reserved=section.reserved,
                solid=True,
            )
        )
        if section.entries:
            pile_x = x + 18
            pile_y = y + shelf_height - 82
            objects.append(
                LayoutObject(
                    object_id=f"entry-pile:{section.key}",
                    label=section.label,
                    kind="entry-pile",
                    x=pile_x,
                    y=pile_y,
                    width=shelf_width - 36,
                    height=64,
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
                        x=pile_x + 62,
                        y=pile_y - 10,
                        width=12,
                        height=12,
                        scene_name="library",
                        section_key=section.key,
                    )
                )

    if system_section is not None:
        wall_x = 36
        wall_y = 670
        wall_width = 132
        wall_height = 224
        objects.append(
            LayoutObject(
                object_id=f"shelf:{system_section.key}",
                label=f"{system_section.label} ({len(system_section.entries)})",
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
        for note_index, entry in enumerate(system_section.entries[:5]):
            note_x = wall_x + 12
            note_y = wall_y + 28 + note_index * 28
            objects.append(
                LayoutObject(
                    object_id=f"bulletin-note:{system_section.key}:{note_index}",
                    label=entry.title,
                    kind="bulletin-note",
                    x=note_x,
                    y=note_y,
                    width=76,
                    height=22,
                    scene_name="library",
                    section_key=system_section.key,
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
                label="Stairs Down",
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
        objects.append(
            LayoutObject(
                object_id=f"vault-shelf:{section.key}",
                label=section.label,
                kind="vault-shelf",
                x=x,
                y=y,
                width=260,
                height=section_height,
                scene_name="vault",
                section_key=section.key,
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
            x=234,
            y=824,
            width=16,
            height=16,
            scene_name="vault",
        )
    )

    objects.append(
        LayoutObject(
            object_id="portal:library",
            label="Stairs Up",
            kind="stairs",
            x=178,
            y=782,
            width=64,
            height=72,
            scene_name="vault",
            target_scene="library",
            target_anchor_id="anchor:library-return",
        )
    )
    return SceneLayout(name="vault", width=width, height=height, player_start=(240, 836), objects=objects)
