from __future__ import annotations

from dataclasses import dataclass


def ease_out_quad(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) * (1.0 - t)


@dataclass(slots=True)
class AnimationClock:
    frame_duration: float = 0.15
    accumulator: float = 0.0

    def update(self, runtime, dt: float) -> None:
        runtime.anim_tick += dt
        runtime.ambient_tick += dt
        self.accumulator += dt
        while self.accumulator >= self.frame_duration:
            runtime.anim_frame += 1
            self.accumulator -= self.frame_duration


@dataclass(slots=True)
class WalkCycle:
    stride: float = 10.0
    distance_accumulator: float = 0.0
    frame_index: int = 0

    def update(self, runtime, dx: float, dy: float) -> None:
        distance = abs(dx) + abs(dy)
        if distance <= 0:
            return
        self.distance_accumulator += distance
        while self.distance_accumulator >= self.stride:
            self.distance_accumulator -= self.stride
            self.frame_index = (self.frame_index + 1) % 2
        if abs(dx) > abs(dy):
            runtime.player_facing = "east" if dx > 0 else "west"
        elif abs(dy) > 0:
            runtime.player_facing = "south" if dy > 0 else "north"
