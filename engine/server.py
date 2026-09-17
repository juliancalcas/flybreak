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
import math

import numpy as np
import websockets
from websockets.asyncio.server import ServerConnection

from contract.validator import validate_tick
from engine.mood import MoodController
from engine.network import LIFNetwork, warm_cache

SCHEMA_VERSION = "1.3"
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

# The "food is present" stimulus (see README.md, "Where this is headed"
# -- the smallest real sensory-input step, replacing part of the flat
# noise `drive` with a real boost to a real, identity-labeled taste
# population instead of exciting everything uniformly). net.food_mask is
# FlyWire's `class == "gustatory"`, `sub_class == "sugar/water"` (129
# neurons) -- see network.py's FOOD_CLASS/FOOD_SUB_CLASS comment for why
# this replaces the earlier, fabricated `OLF_ORN_FOOD` olfactory label,
# which did not actually exist in the real data.
#
# FOOD_POSITION is the one fixed food source in the (as-yet wall-less,
# single-fly) world -- (x, z) on the same ground plane the fly's own
# (self._x, self._z) live on. It must match frontend/index.html's food
# prop exactly (`food.position.set(6, 1.4, 5)`; the frontend's y=1.4 is
# just the prop's height off the ground and has no engine-side analog).
# This constant is the source of truth for that number; the frontend's
# literal is the one that must be kept in lockstep with it, not the
# reverse.
FOOD_POSITION = (6.0, 5.0)  # (x, z)

# _FOOD_BOOST is now the boost's FULL strength, only reached at/very near
# FOOD_POSITION -- see the proximity scaling below. Real gustatory
# (taste) sensing in the actual fly is contact-based, not a distance
# gradient -- that "smell it before touching it" behavior is really an
# olfactory mechanism, and this codebase has no olfactory-gradient
# subsystem (see README.md's "OLF_ORN_FOOD does not exist" correction).
# Ramping the taste channel up with proximity is a modeling
# simplification, the same kind already made for ACH/DA/SER/OCT-as-
# excitatory in network.py: the smallest honest step toward "the fly's
# food-drive grows as it nears food" without inventing a whole separate
# sensory pathway that would need its own real, identity-labeled neuron
# population to attach to (this dataset's `class == "olfactory"`
# sub-populations carry no food identity -- see README.md).
#
# _FOOD_RANGE sets the falloff: an exponential (proximity =
# exp(-distance / _FOOD_RANGE)), chosen over e.g. inverse-square because
# it has no singularity at distance=0 and decays to "effectively zero"
# over a bounded, tunable distance rather than a long inverse-square
# tail. 8.0 is picked to match the actual geometry here: FOOD_POSITION is
# ~7.8 units from the fly's (0,0) spawn (frontend's `fly.position.set(0,
# 3, 0)`), so at spawn the fly already senses roughly 1/e (~37%) of full
# strength -- it hasn't arrived, but it's not scent-blind either -- and
# by ~3x that distance (~24 units, beyond the nearest ring of city
# buildings at radius 15+, see frontend/index.html) proximity is under
# 5%, indistinguishable from no boost at all.
#
# Empirically measured (same methodology as the original flat-boost
# number this replaces: 60-tick runs, seed 0, first 10 ticks dropped as
# warm-up, LIFNetwork driven directly at a fixed distance): food_mask's
# per-tick spike fraction is ~0.63 at distance=0 (proximity=1.0, full
# _FOOD_BOOST), ~0.47 at distance=4 (proximity~0.61, already close),
# ~0.30 at distance=7.8 (proximity~0.38, the fly's own spawn distance),
# and flattens out to ~0.22-0.24 by distance>=16-50 (proximity <0.14) --
# indistinguishable from the ~0.25-0.29 no-boost/no-food baseline this
# same channel had before the gradient existed. So a full trajectory
# (see tests/test_engine.py) sees food_activity swing across roughly that
# same ~0.22-0.65 span as the fly moves toward and orbits FOOD_POSITION,
# not a step function -- see test_food_gradient_scales_with_distance.
_FOOD_BOOST = 2.0
_FOOD_RANGE = 8.0
_FOOD_EMA_ALPHA = 0.1

# Advances (self._x, self._z) each tick exactly the way the frontend's
# own client-side dead reckoning always has (frontend/index.html's
# animate() loop: `fly.position.x/z += sin/cos(heading) * speed * 0.05`,
# run once per rendered frame at ~60fps) -- so the engine's new
# authoritative position accrues distance at the same real-world rate
# the frontend already made familiar: 0.05 units/frame * ~60 frames/s =
# ~3.0 units/s at speed=1 (full activity_norm); a 20 Hz tick needs
# 3.0/20 = 0.15 units/tick to match that. Empirically, typical unboosted
# `speed` early in a run (mood_level still ramping) is ~0.3, i.e. ~0.9
# units/s -- the ~7.8-unit straight-line spawn-to-FOOD_POSITION distance
# would take ~170 ticks (~8.7s) walked dead straight. With the chemotaxis
# steering bias below actually engaged, measured over seeds 0-2 (2500
# ticks each, see tests/test_engine.py): the fly first comes within 2
# units of FOOD_POSITION at tick ~309/349/534 (~15-27s of sim time) and
# stays orbiting close to it (median distance <0.2) for the rest of the
# run -- not instant, not never.
_POSITION_STEP = 0.15

# Chemotaxis-like steering bias (see README.md, "Where this is headed" --
# "A world"): nudges heading toward the true bearing to FOOD_POSITION
# each tick, on top of (not instead of) the existing stochastic wander
# and mood-driven wobble below. Strength scales with food_activity, which
# is itself now proximity-gated (see _FOOD_RANGE above) -- so the pull is
# only strong once the fly has genuinely picked up food-drive, and stays
# small at the ~0.22-0.24 baseline food_activity sits at with no food
# nearby (see _FOOD_RANGE's comment for the measured distance sweep),
# letting the fly wander close to undirected until it is actually within
# scent range. 0.35 is picked so that at food_activity's near-food peak
# (~0.6-0.65) and a maximal 180 degree heading/bearing mismatch, the bias
# contributes up to ~40 degrees/tick -- comparable to or larger than the
# existing wander/wobble terms, enough to reliably win out and turn the
# fly toward food once it is close, without ever fully overriding them
# (it is added to, not substituted for, the stochastic terms).
_FOOD_STEER_GAIN = 0.35


class Simulation:
    def __init__(self, seed: int = 0):
        self.net = LIFNetwork(seed=seed)
        self.mood = MoodController()
        self.tick = 0
        self.sim_time_ms = 0.0
        self._heading = 0.0
        # (0.0, 0.0) matches the frontend's fly spawn point exactly
        # (frontend/index.html: `fly.position.set(0, 3, 0)`, y irrelevant
        # here) -- this is the engine's first-ever notion of where the fly
        # actually is; previously position existed only client-side, as
        # the frontend's own dead-reckoning integration.
        self._x = 0.0
        self._z = 0.0
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
        # on). food_mask gets an extra, proximity-scaled boost on top of
        # it below -- see FOOD_POSITION/_FOOD_BOOST/_FOOD_RANGE's comment.
        # Uses (self._x, self._z) as of the END of the previous tick (this
        # tick hasn't moved yet -- position advances further down, after
        # the heading update) so "how food-driven is this tick" and "how
        # far did that food-drive just steer the heading" both read the
        # same position.
        food_distance = math.hypot(self._x - FOOD_POSITION[0], self._z - FOOD_POSITION[1])
        food_proximity = math.exp(-food_distance / _FOOD_RANGE)
        drive = self._rng.normal(0.15 + mood_level * 0.15, 0.1, size=self.net.n).astype(np.float32)
        drive[self.net.food_mask] += _FOOD_BOOST * food_proximity

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

        # Chemotaxis-like steering bias toward FOOD_POSITION -- see
        # _FOOD_STEER_GAIN's comment. bearing_to_food uses the same
        # sin(heading)=dx, cos(heading)=dz convention the position update
        # below moves in, via atan2(dx, dz) (not the more usual
        # atan2(dz, dx)) so a heading exactly equal to this bearing walks
        # straight at the food. steer is a signed nudge toward that
        # bearing along the shorter arc (wrapped to [-180, 180)) and is
        # added alongside, never instead of, the stochastic terms above.
        food_dx = FOOD_POSITION[0] - self._x
        food_dz = FOOD_POSITION[1] - self._z
        bearing_to_food = math.degrees(math.atan2(food_dx, food_dz)) % 360
        angular_diff = ((bearing_to_food - self._heading + 180) % 360) - 180
        steer = angular_diff * _FOOD_STEER_GAIN * food_activity

        self._heading = (self._heading + self._rng.normal(2, 5) + wobble + steer) % 360
        speed = activity_norm
        if action == "flying":
            wing_state = "buzzing" if activity_norm > 0.85 else "raised"
        else:
            wing_state = "folded"

        # Position advances from the just-updated heading/speed, the same
        # way the frontend's own client-side dead reckoning always has --
        # see _POSITION_STEP's comment for why this constant matches that
        # existing per-second rate.
        heading_rad = math.radians(self._heading)
        self._x += math.sin(heading_rad) * speed * _POSITION_STEP
        self._z += math.cos(heading_rad) * speed * _POSITION_STEP

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
                "position": {"x": self._x, "z": self._z},
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
