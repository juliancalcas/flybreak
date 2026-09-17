"""FlyBreak engine: ticks the real FlyWire connectome and streams
contract-shaped JSON over WebSocket. One fly (one Simulation) per
connected client.

Run from the repo root with: python -m engine.server
(requires `python -m engine.fetch_connectome` to have been run once on
this machine first -- see network.py).
"""
from __future__ import annotations

import asyncio
import json

import numpy as np
import websockets
from websockets.asyncio.server import ServerConnection

from contract.validator import validate_tick
from engine.mood import MoodController
from engine.network import LIFNetwork, warm_cache

SCHEMA_VERSION = "1.2"
TICK_HZ = 20
DT_MS = 1000.0 / TICK_HZ

# mood_level is one continuous slider, "feliz" (0.0) to "plena" (1.0), with
# "muy feliz" read as its midpoint -- see README.md, "The mood_level
# stimulus". No discrete preset values: the HUD labels the slider's ends
# and middle, the value itself stays continuous.

# Empirically measured on the real connectome (139,255 neurons): across
# the full mood_level 0..1 sweep, the motor+descending pathway's per-tick
# activity fraction ranges from ~0.31 (mood_level 0, unstimulated) to
# ~0.58 (mood_level 1, "plena"), with tick-to-tick noise (std ~0.03)
# comparable in size to that whole span -- a raw per-tick threshold would
# make `action` flicker between bands almost independently of mood_level.
# _MOTOR_EMA_ALPHA smooths that out (~10-tick / 500ms window);
# _MOTOR_ACTIVITY_MIN/MAX rescale the smoothed value to a 0..1
# "activity_norm" the action bands and `speed` are both defined against.
_MOTOR_ACTIVITY_MIN = 0.30
_MOTOR_ACTIVITY_MAX = 0.60
_MOTOR_EMA_ALPHA = 0.1

# The "food is present" static stimulus (see README.md, "Where this is
# headed" -- the smallest real sensory-input step, replacing part of the
# flat noise `drive` with a real, always-on boost to a real, identity-
# labeled taste population instead of exciting everything uniformly).
# net.food_mask is FlyWire's `class == "gustatory"`, `sub_class ==
# "sugar/water"` (129 neurons) -- see network.py's FOOD_CLASS/
# FOOD_SUB_CLASS comment for why this replaces the earlier, fabricated
# `OLF_ORN_FOOD` olfactory label, which did not actually exist in the
# real data.
#
# Empirically measured on the real connectome (500-tick runs, seed 0,
# first 80 ticks dropped as warm-up, at mood_level 0.0/0.5/1.0): with no
# boost, food_mask's per-tick spike fraction sits at ~0.25-0.29 regardless
# of mood_level (mood_level's own effect on it is small, a ~0.03 shift --
# it is not a food signal). A static +_FOOD_BOOST added only to
# food_mask's drive raises that to ~0.62-0.65, a clear, mood_level-
# independent step up from baseline; tick-to-tick noise (std ~0.08-0.09)
# is smoothed with the same EMA pattern as the motor pathway before this
# is reported, so the two effects (baseline mood_level noise vs. food
# presence) stay visually distinguishable downstream.
_FOOD_BOOST = 2.0
_FOOD_EMA_ALPHA = 0.1


class Simulation:
    def __init__(self, seed: int = 0):
        self.net = LIFNetwork(seed=seed)
        self.mood = MoodController()
        self.tick = 0
        self.sim_time_ms = 0.0
        self._heading = 0.0
        self._motor_ema = None
        self._food_ema = None
        self._rng = np.random.default_rng(seed)

    def step(self) -> dict:
        self.mood.advance(DT_MS)
        mood_level = self.mood.level

        # mood_level LOWERS the motor pathway's firing threshold (easier
        # to drive a spike, not harder) and raises baseline sensory drive
        # -- own design choice, not a documented biological effect, though
        # the direction (reward/contentment states increasing motivated
        # activity) is the real dopaminergic pathway's general role, which
        # this model has no separate route for -- see README.md.
        # Same formula shape as the earlier bac_level version, mirrored:
        # 1.0 (easiest, at mood_level=1) to 2.5 (hardest, at mood_level=0).
        motor_threshold_scale = 1.0 + (1.0 - mood_level) * 1.5
        # baseline drive: still uniform noise across all 139,255 neurons,
        # still needed for general spontaneous network activity (this is
        # not a full sensory model, just a noise floor everything sits
        # on). food_mask gets an extra, real, always-on boost on top of
        # it below -- see _FOOD_BOOST's comment.
        drive = self._rng.normal(0.15 + mood_level * 0.15, 0.1, size=self.net.n).astype(np.float32)
        drive[self.net.food_mask] += _FOOD_BOOST

        spikes = self.net.step(DT_MS, drive, motor_threshold_scale=motor_threshold_scale)
        self.tick += 1
        self.sim_time_ms += DT_MS

        region_activity = self.net.region_activity(spikes)
        spike_count = int(spikes.sum())
        firing_rate_hz = spike_count / self.net.n * TICK_HZ

        # the real "motor" super_class is tiny (110 of 139,255 neurons) --
        # the combined motor+descending pathway (net.motor_mask) is what
        # actually drives behavior here, not the single "motor" category
        # active_regions reports it as.
        motor_activity = float(spikes[self.net.motor_mask].mean())
        if self._motor_ema is None:
            self._motor_ema = motor_activity
        else:
            self._motor_ema += _MOTOR_EMA_ALPHA * (motor_activity - self._motor_ema)
        activity_norm = float(np.clip(
            (self._motor_ema - _MOTOR_ACTIVITY_MIN) / (_MOTOR_ACTIVITY_MAX - _MOTOR_ACTIVITY_MIN),
            0.0, 1.0))

        # food_mask's own raw spike fraction (already 0..1, no rescale
        # needed the way activity_norm gets one) -- EMA-smoothed the same
        # way and for the same reason as motor_activity above: noisy
        # enough tick-to-tick (see _FOOD_BOOST's comment) that a raw value
        # would swamp the baseline/boosted distinction it exists to show.
        food_activity_raw = float(spikes[self.net.food_mask].mean())
        if self._food_ema is None:
            self._food_ema = food_activity_raw
        else:
            self._food_ema += _FOOD_EMA_ALPHA * (food_activity_raw - self._food_ema)
        food_activity = float(np.clip(self._food_ema, 0.0, 1.0))

        if activity_norm < 0.2:
            action = "frozen"
        elif activity_norm < 0.4:
            action = "idle"
        elif activity_norm < 0.6:
            action = "grooming"
        elif activity_norm < 0.8:
            action = "walking"
        else:
            action = "flying"

        # steadier heading the happier the fly is, not more erratic --
        # opposite of the impairment-driven wobble the earlier bac_level
        # version had.
        wobble = (1.0 - mood_level) * self._rng.normal(0, 25)
        self._heading = (self._heading + self._rng.normal(2, 5) + wobble) % 360
        speed = activity_norm
        if action == "flying":
            wing_state = "buzzing" if activity_norm > 0.85 else "raised"
        else:
            wing_state = "folded"

        return {
            "schema_version": SCHEMA_VERSION,
            "tick": self.tick,
            "sim_time_ms": self.sim_time_ms,
            "stimulus": {"mood_level": mood_level, "other_inputs": {}},
            "activity": {
                "spike_count": spike_count,
                "firing_rate_hz": firing_rate_hz,
                "active_regions": [{"region": r, "activity": region_activity[r]} for r in self.net.regions],
                "food_activity": food_activity,
            },
            "motor_state": {
                "action": action,
                "heading_deg": self._heading,
                "speed": speed,
                "wing_state": wing_state,
            },
            "meta": {"status": "running", "notes": ""},
        }

    def handle_control(self, message: dict) -> None:
        msg_type = message.get("type")
        if msg_type == "set_mood_level":
            self.mood.set_manual(float(message["value"]))
        elif msg_type == "set_mood_mode" and message.get("value") == "auto":
            self.mood.release_to_auto()


async def _read_controls(ws: ServerConnection, sim: Simulation) -> None:
    async for raw in ws:
        try:
            sim.handle_control(json.loads(raw))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            pass  # a malformed control message from one client must not kill the tick loop


async def _handle_client(ws: ServerConnection) -> None:
    sim = Simulation()
    reader_task = asyncio.create_task(_read_controls(ws, sim))
    try:
        while True:
            payload = sim.step()
            validate_tick(payload)
            await ws.send(json.dumps(payload))
            await asyncio.sleep(DT_MS / 1000.0)
    finally:
        reader_task.cancel()


async def main(host: str = "127.0.0.1", port: int = 8765) -> None:
    print("Loading the real FlyWire connectome (139,255 neurons, ~10-15s, once)...")
    warm_cache()
    async with websockets.serve(_handle_client, host, port):
        print(f"FlyBreak engine listening on ws://{host}:{port}")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
