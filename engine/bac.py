"""Hybrid bac_level controller.

By default the level ramps up automatically with sim time (the fly gets
progressively drunker). A HUD slider on the frontend can override it at any
moment; releasing the override resumes the ramp from wherever it was left,
so letting go of the slider never causes a visible jump. See
flybreak/README.md, "bac_level: decision".
"""
from __future__ import annotations


class BacController:
    def __init__(self, ramp_per_ms: float = 1.0 / 120_000, start: float = 0.0):
        # default ramp: reaches 1.0 after ~2 minutes of sim time
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
