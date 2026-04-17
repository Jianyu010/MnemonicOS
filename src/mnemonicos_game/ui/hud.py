from __future__ import annotations


def draw_hud_legend(surface, tiny_font, pygame) -> None:
    panel = pygame.Rect(18, 72, 260, 112)
    pygame.draw.rect(surface, (33, 27, 22), panel, border_radius=10)
    pygame.draw.rect(surface, (90, 75, 60), panel, 2, border_radius=10)
    title = tiny_font.render("Trust / Freshness", True, (226, 214, 189))
    surface.blit(title, (panel.x + 12, panel.y + 10))
    items = [
        ((213, 176, 90), "trusted + current"),
        ((100, 140, 190), "historical"),
        ((201, 160, 90), "needs verification"),
        ((176, 76, 68), "contested"),
        ((120, 120, 120), "stale / weak provenance"),
    ]
    for index, (color, label) in enumerate(items):
        cy = panel.y + 32 + index * 15
        pygame.draw.circle(surface, color, (panel.x + 16, cy + 4), 4)
        text = tiny_font.render(label, True, (214, 204, 188))
        surface.blit(text, (panel.x + 28, cy - 2))
