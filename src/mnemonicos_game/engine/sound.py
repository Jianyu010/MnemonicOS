from __future__ import annotations


class SoundManager:
    def __init__(self, pygame, assets) -> None:
        self._pygame = pygame
        self._assets = assets
        self._enabled = False
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            self._enabled = True
        except Exception:
            self._enabled = False

    def play(self, name: str) -> None:
        if not self._enabled:
            return
        sound = self._assets.sounds.get(name)
        if sound is not None:
            sound.play()

    def music_start(self, scene_name: str) -> None:
        if not self._enabled:
            return
        path = self._assets.music.get(scene_name)
        if not path:
            return
        try:
            self._pygame.mixer.music.fadeout(300)
            self._pygame.mixer.music.load(path)
            self._pygame.mixer.music.play(-1, fade_ms=500)
        except Exception:
            return
