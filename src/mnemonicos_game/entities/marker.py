from __future__ import annotations

from datetime import datetime

from mnemonicos_game.data.models import GameEntry


def freshness_label(updated_at: str | None) -> str:
    if not updated_at:
        return "unknown"
    try:
        delta = (datetime.now() - datetime.strptime(updated_at, "%Y-%m-%d")).days
    except ValueError:
        return "unknown"
    if delta <= 30:
        return "current"
    if delta <= 365:
        return "historical"
    return "stale"


def entry_color(entry: GameEntry | None) -> tuple[int, int, int]:
    if entry is None:
        return (190, 175, 150)
    freshness = freshness_label(entry.updated_at)
    confidence = (entry.confidence or "").casefold()
    if entry.lifecycle_state == "contested":
        return (176, 76, 68)
    if freshness == "current" and confidence == "high":
        return (213, 176, 90)
    if freshness == "historical":
        return (100, 140, 190)
    if freshness == "stale":
        return (120, 120, 120)
    return (201, 160, 90)


def lifecycle_color(lifecycle_state: str | None) -> tuple[int, int, int]:
    mapping = {
        None: (130, 110, 90),
        "draft": (150, 120, 100),
        "review": (120, 80, 130),
        "verified": (88, 124, 88),
        "canonical": (210, 185, 110),
        "contested": (150, 60, 60),
        "archived": (118, 118, 118),
    }
    return mapping.get(lifecycle_state, (130, 110, 90))
