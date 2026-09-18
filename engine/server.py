"""FlyBreak engine: ticks the real FlyWire connectome and streams
contract-shaped JSON over WebSocket. One shared World of N_FLIES flies
(see World's docstring), ticked once regardless of client count; each
connecting client controls world.flies[0] and watches all of them.

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

from contract.validator import validate_graph, validate_tick
from engine.mood import MoodController
from engine.network import LIFNetwork, get_sample_graph, warm_cache

SCHEMA_VERSION = "1.5"
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
# FOOD_POSITION is one of the two fixed attractant sources in the
# (as-yet wall-less, boundary-less) shared world every fly lives in (see
# World below) -- (x, z) on the same ground plane every fly's own
# (self._x, self._z) live on. It must match frontend/index.html's food
# prop exactly (`food.position.set(6, 1.4, 5)`; the frontend's y=1.4 is
# just the prop's height off the ground and has no engine-side analog).
# This constant is the source of truth for that number; the frontend's
# literal is the one that must be kept in lockstep with it, not the
# reverse.
FOOD_POSITION = (6.0, 5.0)  # (x, z)

# WATER_POSITION: the second static landmark in the world, driving the
# SAME net.food_mask population as FOOD_POSITION, through the SAME
# distance-gradient shape -- not a second, independent "water" channel.
# This is a deliberate, honest constraint, not a shortcut: FlyWire's own
# classification has exactly ONE real, identity-labeled appetitive taste
# channel in this dataset (`class == "gustatory"`, `sub_class ==
# "sugar/water"`, 129 neurons -- see network.py's FOOD_CLASS/
# FOOD_SUB_CLASS comment). It does not separately distinguish sugar- from
# water-sensing at this level of the real data. Inventing a second,
# independent "water" neuron population to attach WATER_POSITION to would
# be exactly the kind of fabrication this project already corrected once
# (README.md's OLF_ORN_FOOD/OLF_ORN_DANGER story -- names that sounded
# plausible but did not exist in the real classification.csv.gz). The
# honest design instead: both landmarks are real "the fly is near a real
# attractant" signals, and both drive the one real channel the data
# actually supports (see `proximity = max(...)` below, not summed).
#
# Chosen ~7.8 units from spawn (0, 0) -- the same distance as
# FOOD_POSITION (sqrt(6^2 + 5^2) = 7.81, sqrt((-6)^2 + (-5)^2) = 7.81) --
# so neither landmark starts closer to the fly than the other, and in the
# diagonally opposite quadrant (-x, -z vs. FOOD_POSITION's +x, +z) so the
# two are visually distinct and never overlap. Both stay well inside
# frontend/index.html's nearest ring of wireframe buildings (radius 15+).
WATER_POSITION = (-6.0, -5.0)  # (x, z)

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
# sub-populations carry no food identity -- see README.md). WATER_POSITION
# reuses this exact same boost/range pair -- see its own comment above.
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
# same ~0.22-0.65 span as the fly moves toward and orbits whichever
# landmark it's closest to, not a step function -- see
# test_food_gradient_scales_with_distance. Since WATER_POSITION reuses
# the exact same boost/range/mask, a fly spawned equidistant-ish from
# both landmarks (as it is at (0,0) -- both are ~7.81 units away) sees
# this same range regardless of which one it ends up approaching.
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
# "A world"): nudges heading toward the true bearing to whichever of
# FOOD_POSITION/WATER_POSITION currently has the higher proximity (see
# `target` below, chosen fresh each tick) -- on top of (not instead of)
# the existing stochastic wander and mood-driven wobble below. Strength
# scales with food_activity, which is itself now proximity-gated (see
# _FOOD_RANGE above) -- so the pull is only strong once the fly has
# genuinely picked up food-drive, and stays small at the ~0.22-0.24
# baseline food_activity sits at with no attractant nearby (see
# _FOOD_RANGE's comment for the measured distance sweep), letting the fly
# wander close to undirected until it is actually within scent range of
# one of the two. 0.35 is picked so that at food_activity's near-food peak
# (~0.6-0.65) and a maximal 180 degree heading/bearing mismatch, the bias
# contributes up to ~40 degrees/tick -- comparable to or larger than the
# existing wander/wobble terms, enough to reliably win out and turn the
# fly toward whichever landmark it's closer to once it is close, without
# ever fully overriding them (it is added to, not substituted for, the
# stochastic terms).
_FOOD_STEER_GAIN = 0.35

# Conspecific ("another fly is visibly nearby") boost -- see network.py's
# VISUAL_SUPER_CLASSES comment for why this is legitimately a distance
# sense, unlike the food/water gradient above. net.visual_mask is huge
# (85,557 of 139,255 neurons -- optic is most of the fly's brain, as in
# the real animal), so a boost anywhere near _FOOD_BOOST's magnitude
# (2.0) would saturate it and distort the rest of the network's dynamics;
# _SOCIAL_BOOST is picked much smaller than _FOOD_BOOST for exactly that
# reason.
#
# Empirically measured (same methodology as _FOOD_BOOST/_FOOD_RANGE: 60-
# tick runs, seed 0, first 10 ticks dropped as warm-up, LIFNetwork driven
# directly at a fixed peer distance): visual_mask's per-tick spike
# fraction is ~0.272-0.274 with no peer nearby (distance>=15, proximity
# <0.05) -- the same ~0.27-0.29 baseline this channel already sits at
# unboosted -- rising to ~0.289 at distance=5 (proximity=0.37, one
# _SOCIAL_RANGE away), ~0.309 at distance=2 (proximity=0.67), and ~0.344
# at distance=0 (proximity=1.0, full _SOCIAL_BOOST) -- a small but real
# and monotonic gradient, not a no-op. Confirmed separately that this
# boost does not meaningfully perturb the motor+descending pathway's own
# calibration (net.motor_mask activity measured at ~0.425-0.431 across
# _SOCIAL_BOOST=0.0 to 1.0, well inside the tick-to-tick noise
# _MOTOR_EMA_ALPHA already smooths over -- see its comment), so
# _MOTOR_ACTIVITY_MIN/MAX needed no retuning for this.
#
# _SOCIAL_RANGE=5.0 (shorter than _FOOD_RANGE=8.0, deliberately -- "notice
# a conspecific" is meant to read as closer-range than "smell a landmark
# from across the world," not identical to it) uses the same exponential
# falloff shape as food/water for the same reason (bounded, no
# singularity at distance=0).
_SOCIAL_BOOST = 0.6
_SOCIAL_RANGE = 5.0

# Minimum-separation collision response for World.step() -- basic
# physics ("no two flies occupy the same point"), the OPPOSITE kind of
# rule from _SOCIAL_BOOST/_SOCIAL_RANGE above: that one is a small
# ATTRACTION-flavored sensory boost with no ceiling on range (a fly
# senses a peer from arbitrarily far away, just weakly); this is a
# REPULSION rule that only ever acts at short range, and never pulls two
# flies together -- see World.step()'s own comment for why the two must
# not be confused. It also does not touch, replace, or weaken the
# deliberate "no fly-to-fly steering" design decision (README.md,
# "Where this is headed" -- "Multiple flies"; World's own docstring):
# flies still aren't drawn toward each other by anything in this
# codebase, they just can't end up on top of one another once they do
# happen to end up close, from food/water-seeking alone.
#
# _MIN_FLY_SEPARATION is picked from the real rendered fly's own scale
# (frontend/index.html's real flybody mesh -- see
# frontend/assets/flybody/SOURCE.md), not guessed: taking the real
# vendored thorax/abdomen/head .obj files (the fly's actual body core,
# not the thin legs/wings that splay out well beyond it) through the
# exact same axis-remap + FLYBODY_RECENTER + FLYBODY_SCALE=0.85 transform
# `remapFlybodyGeometry` applies there, the assembled body's bounding box
# measures ~0.77 units side-to-side, ~1.08 units top-to-bottom, and ~2.50
# units front-to-back, in this same (x, z) ground-plane's units. Treating
# each fly as roughly a ~0.75-unit-radius body (matching its real
# side-to-side/top-to-bottom cross-section, the axes that actually matter
# when two flies approach each other at arbitrary headings -- nose-to-
# tail alignment, the one axis that runs to ~2.5, is not the common case),
# two such bodies need their centers at least ~1.5 units apart before
# they stop visually intersecting. 1.5 is comfortably above that
# cross-section (leaves real daylight between the bodies, not just
# touching) while staying small next to _FOOD_RANGE/_SOCIAL_RANGE (8.0/
# 5.0) so it does not meaningfully fight the food/water attraction that
# already, deliberately, brings flies this close together in the first
# place (see test_flies_end_up_near_each_other_without_fly_to_fly_steering).
_MIN_FLY_SEPARATION = 1.5

# Spring stiffness for the collision response below: 1.0 means each
# violated pair is pushed fully back out to exactly _MIN_FLY_SEPARATION
# apart, in a single tick's correction pass, proportional to how deep the
# overlap is (a real linear spring: displacement = overlap * stiffness,
# split evenly between the two flies) -- not eased in over several ticks.
# Still not a hard "wall" in the sense of clamping positions directly:
# it only ever nudges by the measured overlap amount, so a fly involved
# in more than one overlap at once (e.g. squeezed between two others --
# possible but rare at N_FLIES=3) can still end up marginally under
# _MIN_FLY_SEPARATION from one of its neighbors after both corrections
# are summed and applied together, exactly the "soft" case
# tests/test_engine.py's own separation test allows for.
_SEPARATION_SPRING = 1.0


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
        # local sample id -> real connectome neuron index, for
        # "sample_spikes" below -- see network.get_sample_graph's own
        # docstring. Fetched once here (get_sample_graph is itself
        # lru_cache(maxsize=1), so this is just a dict lookup, not a
        # recompute) rather than every tick.
        self._sample_real_index = get_sample_graph()["sample_real_index"]

    def step(self, peer_positions: list[tuple[float, float]] | None = None) -> dict:
        """Advances one tick.

        `peer_positions` is an optional list of OTHER flies' (x, z)
        positions, as of the end of the previous tick (see World.step()'s
        two-pass comment) -- used only to compute this fly's own
        conspecific-proximity drive boost (see _SOCIAL_BOOST/
        _SOCIAL_RANGE). A standalone Simulation (no World, the existing
        single-fly usage this project has always had) passes nothing, so
        this fly senses no peers -- Simulation remains independently
        constructible/steppable/testable exactly as before.
        """
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
        water_distance = math.hypot(self._x - WATER_POSITION[0], self._z - WATER_POSITION[1])
        food_proximity = math.exp(-food_distance / _FOOD_RANGE)
        water_proximity = math.exp(-water_distance / _FOOD_RANGE)
        # max(), not summed -- see WATER_POSITION's comment. net.food_mask
        # is one real taste-receptor population; a real receptor
        # population saturates on whichever stimulus is currently
        # strongest, it does not sum two simultaneous, independently-
        # sensed distant sources into a stronger-than-either signal. This
        # also keeps the boost's own range identical to the single-
        # landmark version (still exp(-d/_FOOD_RANGE) in [0,1]) -- the
        # near/far numbers measured in _FOOD_RANGE's comment above still
        # hold unchanged with two landmarks instead of one.
        proximity = max(food_proximity, water_proximity)
        drive = self._rng.normal(0.15 + mood_level * 0.15, 0.1, size=self.net.n).astype(np.float32)
        drive[self.net.food_mask] += _FOOD_BOOST * proximity

        # Conspecific ("another fly nearby") boost -- see _SOCIAL_BOOST/
        # _SOCIAL_RANGE's comment. Uses the nearest peer only (min, not
        # sum/mean across all peers), same saturating-receptor logic as
        # food/water's max() above -- one visual system responding to
        # "is there a conspecific close by," not one signal per peer
        # stacking additively. peer_positions is the previous tick's
        # snapshot (see the docstring above), never this tick's -- so this
        # boost, like food/water's, reads a position from strictly before
        # any of this tick's movement.
        if peer_positions:
            nearest_peer_distance = min(
                math.hypot(self._x - px, self._z - pz) for px, pz in peer_positions
            )
            social_proximity = math.exp(-nearest_peer_distance / _SOCIAL_RANGE)
        else:
            social_proximity = 0.0
        drive[self.net.visual_mask] += _SOCIAL_BOOST * social_proximity

        spikes = self.net.step(DT_MS, drive, motor_threshold_scale=motor_threshold_scale)
        self.tick += 1
        self.sim_time_ms += DT_MS

        region_activity = self.net.region_activity(spikes)
        spike_count = int(spikes.sum())
        firing_rate_hz = spike_count / self.net.n * TICK_HZ

        # Real, live spikes for just the "synapse_graph" sample (see
        # network.get_sample_graph) -- the LOCAL sample ids (0..N_sample-1,
        # matching that one-time message's node ids) that actually spiked
        # THIS tick, looked up from the real per-tick `spikes` vector above
        # via the sample's real-neuron-index mapping. Typically ~400-550
        # out of the ~1,000 sampled neurons once the network reaches its
        # steady-state firing rate (measured directly; this network's own
        # steady-state firing fraction runs ~40-45% of a given population
        # generally, sample included, measured slightly higher -- ~47% --
        # at this particular smaller sample -- see README.md's
        # live-synapse-sample section) -- never the full 139,255-length
        # vector.
        sample_spikes = np.nonzero(spikes[self._sample_real_index])[0].tolist()

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

        # Chemotaxis-like steering bias toward whichever of FOOD_POSITION/
        # WATER_POSITION is currently winning (higher proximity) -- see
        # _FOOD_STEER_GAIN's comment. Ties (exact at spawn, where both
        # landmarks sit at the same ~7.81-unit distance) favor food, an
        # arbitrary but deterministic tie-break so the very first tick's
        # bias doesn't depend on floating-point noise; whichever landmark
        # the fly drifts toward first then stays strictly closer (its own
        # proximity keeps rising as the other's falls), so it keeps
        # winning on every subsequent tick -- no oscillation between the
        # two once broken. target_bearing uses the same sin(heading)=dx,
        # cos(heading)=dz convention the position update below moves in,
        # via atan2(dx, dz) (not the more usual atan2(dz, dx)) so a
        # heading exactly equal to this bearing walks straight at the
        # target. steer is a signed nudge toward that bearing along the
        # shorter arc (wrapped to [-180, 180)) and is added alongside,
        # never instead of, the stochastic terms above.
        target = FOOD_POSITION if food_proximity >= water_proximity else WATER_POSITION
        target_dx = target[0] - self._x
        target_dz = target[1] - self._z
        bearing_to_target = math.degrees(math.atan2(target_dx, target_dz)) % 360
        angular_diff = ((bearing_to_target - self._heading + 180) % 360) - 180
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
                "sample_spikes": sample_spikes,
            },
            "motor_state": {
                "action": action,
                "heading_deg": self._heading,
                "speed": speed,
                "wing_state": wing_state,
                "position": {"x": self._x, "z": self._z},
            },
            "meta": {"status": "running", "notes": ""},
            # A standalone Simulation has no visibility into other flies'
            # full render state (action/wing_state/etc -- it only ever
            # receives bare peer *positions*, for the visual-proximity
            # drive boost above). World assembles the real other_flies
            # list for the client-facing payload from the sibling
            # Simulations' own step() outputs -- see World.step() and
            # server.py's _handle_client. Schema "1.4" requires this key
            # to be present on every tick regardless, so it defaults to
            # empty here rather than being conditionally omitted.
            "world": {"other_flies": []},
        }

    def handle_control(self, message: dict) -> None:
        msg_type = message.get("type")
        if msg_type == "set_mood_level":
            self.mood.set_manual(float(message["value"]))
        elif msg_type == "set_mood_mode" and message.get("value") == "auto":
            self.mood.release_to_auto()


# Fixed, small population of flies sharing ONE world (see README.md,
# "Where this is headed" -- "Multiple flies"). Index 0 is the one a
# connecting browser controls (its mood_level set/override routes to
# world.flies[0], exactly as the single-fly version always worked);
# indices 1..N_FLIES-1 are fully autonomous -- nothing ever calls
# set_manual on their MoodControllers, they just auto-ramp forever. This
# deliberately avoids needing any multi-viewer claiming/allocation system
# (out of scope, not asked for) while still giving the controlled fly real
# company: autonomous flies are still genuinely "otras de su especie,"
# just not player-controlled ones. 3 is small enough that every fly's
# step() (peer-distance loop over N_FLIES-1 others) stays negligible next
# to the ~5-13ms/tick the underlying LIFNetwork.step() already costs per
# fly (see README.md's "The real connectome" -- N_FLIES-1 hypot() calls
# per fly per tick is nothing next to a 139,255-neuron sparse matmul).
N_FLIES = 3


class World:
    """The one shared simulated space every fly and every connected
    viewer lives in together (see README.md, "Where this is headed" --
    "Multiple flies"). Ticked once, continuously, by a single background
    task in main() -- independent of how many browser tabs are connected,
    so autonomous flies keep living with nobody watching. Owns N_FLIES
    independent `Simulation`s; `Simulation` itself stays the well-tested,
    independently-usable single-fly building block it already was -- World
    only adds the shared tick, the peer-position exchange between them
    (used only for the small conspecific-proximity sensory boost, see
    _SOCIAL_BOOST/_SOCIAL_RANGE), and a minimum-separation collision
    response (_MIN_FLY_SEPARATION/_apply_min_separation) that keeps two
    flies from ending up (nearly) on top of each other once resource-
    seeking brings them close. Deliberately, there is still NO fly-to-fly
    STEERING/attraction anywhere in this codebase -- the collision
    response only ever pushes flies apart at close range, it never pulls
    them together at any range; see _MIN_FLY_SEPARATION's own comment for
    why these are opposite kinds of rules, not the same one.
    """

    def __init__(self, n_flies: int = N_FLIES, seeds: list[int] | None = None):
        # distinct seeds so the N_FLIES flies don't all move identically
        # despite sharing a spawn point -- see Simulation.__init__'s
        # (0.0, 0.0) spawn comment; all flies born at the same point, like
        # siblings, then wander apart under their own independent rng.
        # `seeds` defaults to 0..n_flies-1 (what main() actually runs);
        # tests pass other seed sets to check the "flies do end up near
        # each other" empirical claim isn't a one-seed fluke.
        seeds = list(range(n_flies)) if seeds is None else seeds
        self.flies = [Simulation(seed=s) for s in seeds]
        self.latest_payloads: list[dict] | None = None

    def step(self) -> list[dict]:
        """Ticks every fly once, together. Two-pass, as README.md/the
        design brief requires: (a) snapshot every fly's position as of
        the END of the previous tick, BEFORE any fly moves this tick,
        then (b) give each fly that fixed snapshot (all peers, i.e. every
        OTHER fly) so its own drive/steering for this tick is computed
        against a single consistent world-state, not one that's already
        half-updated by whichever fly happened to step first.

        A third pass then runs _apply_min_separation on the freshly
        stepped positions -- basic collision response, not sensing: see
        _MIN_FLY_SEPARATION's own comment for why this is a distinct,
        opposite-direction rule from the peer-sensing pass above (that
        one only ever biases THIS tick's drive/steering computation
        toward attraction at any range; this one only ever pushes
        AFTER-the-fact overlapping positions apart, at close range).
        """
        snapshot = [(f._x, f._z) for f in self.flies]
        payloads = []
        for i, fly in enumerate(self.flies):
            peers = [pos for j, pos in enumerate(snapshot) if j != i]
            payloads.append(fly.step(peer_positions=peers))
        self._apply_min_separation(payloads)
        self.latest_payloads = payloads
        return payloads

    def _apply_min_separation(self, payloads: list[dict]) -> None:
        """Basic "can't occupy the same space" physics: pushes any pair of
        flies closer than _MIN_FLY_SEPARATION apart back out along the
        line between their centers, proportional to how deep the overlap
        is (a simple linear repulsion spring -- see _SEPARATION_SPRING's
        comment). Runs once per tick, AFTER every fly has already moved
        under its own attraction/steering/wander for this tick (see
        step()'s docstring) -- a collision RESPONSE to wherever food/water
        attraction and wander already put the flies, never an input to
        that movement itself, and never a pull toward another fly at any
        range (see _MIN_FLY_SEPARATION's comment for why this must not be
        confused with the _SOCIAL_BOOST/_SOCIAL_RANGE sensory boost, or
        read as the "fly-to-fly steering" this project deliberately does
        not have -- README.md, "Where this is headed", "Multiple flies").

        Mutates both each Simulation's own (_x, _z) (so the correction
        carries into next tick's peer-position snapshot too, not just
        this tick's outgoing payload) and this tick's `payloads` in place
        (so the client-facing positions -- fly 0's own "motor_state" and
        the other flies' "world.other_flies" entries built from these
        same payloads in _build_client_payload -- reflect the corrected,
        not the pre-correction, positions).
        """
        positions = [p["motor_state"]["position"] for p in payloads]
        n = len(payloads)
        dx_push = [0.0] * n
        dz_push = [0.0] * n
        for i in range(n):
            for j in range(i + 1, n):
                dx = positions[i]["x"] - positions[j]["x"]
                dz = positions[i]["z"] - positions[j]["z"]
                dist = math.hypot(dx, dz)
                if dist >= _MIN_FLY_SEPARATION:
                    continue
                overlap = _MIN_FLY_SEPARATION - dist
                if dist < 1e-9:
                    # Exact (or float-precision-exact) coincidence: no real
                    # direction to push along -- fall back to a
                    # deterministic direction from the pair's own indices
                    # (no RNG needed, same "determinism where it's free"
                    # style as get_sample_graph's BFS) so the two still
                    # separate instead of sitting locked together forever.
                    angle = (2.0 * math.pi * (i + 1)) / n + j
                    ux, uz = math.sin(angle), math.cos(angle)
                else:
                    ux, uz = dx / dist, dz / dist
                push = overlap * _SEPARATION_SPRING * 0.5
                dx_push[i] += ux * push
                dz_push[i] += uz * push
                dx_push[j] -= ux * push
                dz_push[j] -= uz * push
        for i, fly in enumerate(self.flies):
            if dx_push[i] == 0.0 and dz_push[i] == 0.0:
                continue
            fly._x += dx_push[i]
            fly._z += dz_push[i]
            positions[i]["x"] = fly._x
            positions[i]["z"] = fly._z

    def handle_control(self, message: dict) -> None:
        """Routes a connecting client's control message to the ONE fly it
        controls (index 0) -- see N_FLIES' comment. Flies 1..N_FLIES-1
        never receive control messages; their MoodControllers just
        auto-ramp forever.
        """
        self.flies[0].handle_control(message)


def _build_client_payload(payloads: list[dict]) -> dict:
    """Builds the tick a connecting client actually receives: fly 0's own
    full payload (stimulus/activity/motor_state/meta -- unchanged shape,
    see README.md's "minimize churn to the existing single-fly contract"),
    with `world.other_flies` filled in from the OTHER flies' own most
    recent payloads -- id, position, heading_deg, action, wing_state only
    (see schema_v1.json's "world" definition -- a viewer isn't meant to
    have neural introspection into flies it doesn't control, just enough
    to render them moving around realistically).
    """
    payload = dict(payloads[0])
    payload["world"] = {
        "other_flies": [
            {
                "id": i,
                "position": p["motor_state"]["position"],
                "heading_deg": p["motor_state"]["heading_deg"],
                "action": p["motor_state"]["action"],
                "wing_state": p["motor_state"]["wing_state"],
            }
            for i, p in enumerate(payloads) if i != 0
        ]
    }
    return payload


async def _read_controls(ws: ServerConnection, world: World) -> None:
    async for raw in ws:
        try:
            world.handle_control(json.loads(raw))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            pass  # a malformed control message from one client must not kill the tick loop


async def _tick_world(world: World) -> None:
    """The single background task that steps the shared World, at the
    same TICK_HZ the engine has always run at -- runs continuously from
    the moment main() starts it, regardless of whether any client is
    connected, so the autonomous flies (indices 1..N_FLIES-1) keep living
    with nobody watching. Client connections (_handle_client below) only
    ever READ world.latest_payloads; they never step the world themselves
    anymore -- see README.md, "Where this is headed".
    """
    while True:
        world.step()
        await asyncio.sleep(DT_MS / 1000.0)


async def _handle_client(ws: ServerConnection, world: World) -> None:
    # The real snowball-sampled synapse subgraph (see network.py's
    # get_sample_graph) is identical for every fly and every client --
    # the underlying connectome/weights `w` are shared, cached once per
    # process (see LIFNetwork.__init__ / _load_connectome; only each
    # fly's own membrane state differs). So it is sent exactly once, right
    # here, before the per-tick loop below -- not re-sent every tick like
    # a regular payload, and structurally tagged with "type":
    # "synapse_graph" so the frontend can tell it apart from a tick
    # (which has no "type" field).
    graph = get_sample_graph()
    graph_message = {"type": "synapse_graph", "nodes": graph["nodes"], "edges": graph["edges"]}
    validate_graph(graph_message)
    await ws.send(json.dumps(graph_message))

    reader_task = asyncio.create_task(_read_controls(ws, world))
    last_tick_sent = -1
    try:
        while True:
            payloads = world.latest_payloads
            if payloads is not None and payloads[0]["tick"] != last_tick_sent:
                out = _build_client_payload(payloads)
                validate_tick(out)
                await ws.send(json.dumps(out))
                last_tick_sent = payloads[0]["tick"]
            # Polls faster than the tick rate so a client picks up each
            # new tick promptly without resending a stale/duplicate one
            # while waiting on the background task above -- the world's
            # own stepping cadence (DT_MS) is what actually paces ticks,
            # this is just how often a client re-checks for a new one.
            await asyncio.sleep(DT_MS / 1000.0 / 4)
    finally:
        reader_task.cancel()


# websockets' own default max_size is 1 MiB. The one-time synapse_graph
# message at the current ~1,000-node sample size (down from an earlier
# 10,000-node/16.2 MB version -- see README.md's "The live synapse
# sample" for why it was shrunk) is a real measured ~0.8 MB (1,000 nodes,
# 12,307 real edges), which would actually fit under that 1 MiB default
# now -- but 32 MiB is kept anyway as real, deliberate headroom (not
# sized to the current payload) rather than something that would need
# raising again by surprise the next time the sample size changes.
_MAX_WS_MESSAGE_BYTES = 32 * 1024 * 1024


async def main(host: str = "127.0.0.1", port: int = 8765) -> None:
    print("Loading the real FlyWire connectome (139,255 neurons, ~10-15s, once)...")
    warm_cache()
    world = World()
    tick_task = asyncio.create_task(_tick_world(world))
    try:
        async with websockets.serve(lambda ws: _handle_client(ws, world), host, port,
                                     max_size=_MAX_WS_MESSAGE_BYTES):
            print(f"FlyBreak engine listening on ws://{host}:{port} "
                  f"({N_FLIES} flies sharing one world)")
            await asyncio.Future()
    finally:
        tick_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
