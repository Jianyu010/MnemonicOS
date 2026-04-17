from __future__ import annotations


def compute_minimap_rects(runtime, panel_rect: tuple[int, int, int, int]) -> dict[str, tuple[int, int, int, int]]:
    x, y, width, height = panel_rect
    scene = runtime.scenes[runtime.current_scene]
    rects: dict[str, tuple[int, int, int, int]] = {}
    for obj in scene.objects:
        mx = x + int((obj.x / scene.width) * width)
        my = y + int((obj.y / scene.height) * height)
        mw = max(3, int((obj.width / scene.width) * width))
        mh = max(3, int((obj.height / scene.height) * height))
        rects[obj.object_id] = (mx, my, mw, mh)
    rects["player"] = (
        x + int((runtime.player.x / scene.width) * width),
        y + int((runtime.player.y / scene.height) * height),
        max(4, int((runtime.player.width / scene.width) * width)),
        max(4, int((runtime.player.height / scene.height) * height)),
    )
    return rects


def draw_minimap(surface, runtime, tiny_font, pygame) -> None:
    panel_width = 190 if runtime.settings.window_width >= 1200 else 160
    panel_height = 150 if runtime.settings.window_height >= 720 else 126
    panel = pygame.Rect(runtime.settings.window_width - panel_width - 30, 18, panel_width, panel_height)
    pygame.draw.rect(surface, (33, 27, 22), panel, border_radius=10)
    pygame.draw.rect(surface, (90, 75, 60), panel, 2, border_radius=10)
    title = tiny_font.render("Minimap", True, (226, 214, 189))
    surface.blit(title, (panel.x + 12, panel.y + 10))

    map_rect = (panel.x + 12, panel.y + 32, panel.width - 24, panel.height - 44)
    mx, my, mw, mh = map_rect
    pygame.draw.rect(surface, (22, 18, 14), pygame.Rect(mx, my, mw, mh), border_radius=6)
    rects = compute_minimap_rects(runtime, map_rect)

    scene = runtime.scenes[runtime.current_scene]
    for obj in scene.objects:
        color = (122, 92, 66)
        if obj.reserved:
            color = (78, 78, 78)
        elif obj.kind == "portal":
            color = (96, 126, 184)
        elif obj.kind == "entrance":
            color = (190, 166, 120)
        elif obj.kind == "daemon":
            color = (176, 132, 83)
        elif obj.kind == "marker":
            color = (255, 214, 102)
        elif obj.kind == "entry":
            color = (211, 181, 110)
        pygame.draw.rect(surface, color, pygame.Rect(*rects[obj.object_id]), border_radius=3)

    pygame.draw.rect(surface, (120, 180, 230), pygame.Rect(*rects["player"]), border_radius=3)
