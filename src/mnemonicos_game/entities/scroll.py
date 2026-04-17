from __future__ import annotations

from dataclasses import dataclass

from mnemonicos_game.data.models import GameEntry
from mnemonicos_game.entities.marker import freshness_label


@dataclass(slots=True)
class ScrollEntity:
    entry: GameEntry


def draw_scroll(
    surface,
    rect,
    fill_color,
    outline_color,
    lifecycle_state: str | None,
    pygame,
    *,
    sprite=None,
    is_fresh: bool = False,
    is_selected: bool = False,
    anim_frame: int = 0,
) -> None:
    if sprite is not None:
        scaled = pygame.transform.smoothscale(sprite, rect.size)
        surface.blit(scaled, rect.topleft)
        pygame.draw.rect(surface, outline_color, rect, 1 if not is_selected else 2, border_radius=4)
    else:
        pygame.draw.rect(surface, fill_color, rect, border_radius=4)
        pygame.draw.rect(surface, outline_color, rect, 2, border_radius=4)

    if lifecycle_state == "draft":
        pygame.draw.line(surface, outline_color, (rect.left + 3, rect.top + 6), (rect.right - 3, rect.top + 2), 2)
        pygame.draw.line(surface, outline_color, (rect.left + 3, rect.bottom - 4), (rect.right - 5, rect.bottom - 8), 2)
    elif lifecycle_state == "review":
        pygame.draw.line(surface, (140, 70, 120), (rect.centerx, rect.top + 3), (rect.centerx, rect.bottom - 3), 3)
    elif lifecycle_state == "verified":
        pygame.draw.circle(surface, (88, 124, 88), (rect.right - 7, rect.top + 7), 4)
    elif lifecycle_state == "canonical":
        pygame.draw.circle(surface, (210, 185, 110), (rect.right - 7, rect.top + 7), 5)
    elif lifecycle_state == "contested":
        pygame.draw.line(surface, (150, 60, 60), (rect.left + 5, rect.top + 5), (rect.right - 5, rect.bottom - 5), 2)
        pygame.draw.line(surface, (150, 60, 60), (rect.left + 5, rect.bottom - 5), (rect.right - 5, rect.top + 5), 2)
    elif lifecycle_state == "archived":
        for offset in (4, 10, 16):
            pygame.draw.circle(surface, (140, 130, 110), (rect.left + offset, rect.top + 5), 1)
    if is_fresh and anim_frame % 4 < 2:
        pygame.draw.rect(surface, (92, 184, 92), (rect.left + 2, rect.top + 2, 4, 4), border_radius=1)
    if is_selected:
        pygame.draw.rect(surface, (74, 144, 217), rect.inflate(2, 2), 2, border_radius=5)


def scroll_is_fresh(entry: GameEntry | None) -> bool:
    return freshness_label(entry.updated_at if entry else None) == "current"
