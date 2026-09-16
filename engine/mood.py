"""Hybrid mood_level controller.

By default the level ramps up automatically with sim time (the fly grows
progressively happier on its own, unprompted). Three HUD preset buttons
--"feliz", "muy feliz", "plena" (see server.py's MOOD_PRESETS) -- can pin
it to a fixed value at any moment; releasing the override resumes the
ramp from wherever it was left, so letting go of a preset never causes a
visible jump. See flybreak/README.md, "The mood_level stimulus".

This replaced an earlier "ethanol intoxication" (bac_level) stimulus --
same mechanism (this class is otherwise unchanged from that version),
opposite direction and meaning: bac_level ramped toward impairment,
mood_level ramps toward contentment.
"""
from __future__ import annotations


class MoodController:
    def __init__(self, ramp_per_ms: float = 1.0 / 120_000, start: float = 0.0):
        # default ramp: reaches 1.0 ("plena") after ~2 minutes of sim time
        self._ramp_per_ms = ramp_per_ms
        self._auto_level = start
        self._manual_level: float | None = None

    @property
    def mode(self) -> str:
        return "manual" if self._manual_level is not None else "auto"

    @property
    def level(self) -> float:
        value = self._manual_level if self._manual_level is not None else self._auto_level
        return max(0.0, min(1.0, value))

    def advance(self, dt_ms: float) -> None:
        if self._manual_level is None:
            self._auto_level = min(1.0, self._auto_level + self._ramp_per_ms * dt_ms)

    def set_manual(self, value: float) -> None:
        self._manual_level = max(0.0, min(1.0, value))

    def release_to_auto(self) -> None:
        self._auto_level = self.level
        self._manual_level = None
