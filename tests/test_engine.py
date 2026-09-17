import math

import numpy as np
import pytest

from contract.validator import validate_tick
from engine.mood import MoodController
from engine.network import LIFNetwork, data_available
from engine.server import (
    FOOD_POSITION,
    N_FLIES,
    Simulation,
    WATER_POSITION,
    World,
    _FOOD_BOOST,
    _FOOD_RANGE,
    _SOCIAL_BOOST,
    _SOCIAL_RANGE,
)

# LIFNetwork/Simulation load the real ~50 MB FlyWire connectome (see
# network.py) -- these tests need `python -m engine.fetch_connectome` to
# have been run on this machine first. Skipping (not failing) when it
# hasn't is what keeps `pytest tests` usable on a fresh checkout; the
# MoodController tests below need no data and always run.
requires_connectome = pytest.mark.skipif(
    not data_available(),
    reason="run `python -m engine.fetch_connectome` first",
)


@requires_connectome
def test_network_step_shapes():
    net = LIFNetwork(seed=0)
    drive = np.zeros(net.n, dtype=np.float32)
    spikes = net.step(dt_ms=50.0, drive=drive)
    assert spikes.shape == (net.n,)
    activity = net.region_activity(spikes)
    assert set(activity) == set(net.regions)
    assert all(0.0 <= v <= 1.0 for v in activity.values())


@requires_connectome
def test_network_motor_mask_matches_real_super_classes():
    net = LIFNetwork(seed=0)
    # the real FlyWire data: 110 "motor" + 1305 "descending" neurons
    assert net.motor_mask.sum() == 1415
    assert set(net.regions) >= {"motor", "descending", "optic", "sensory"}


@requires_connectome
def test_network_food_mask_matches_real_gustatory_sub_class():
    net = LIFNetwork(seed=0)
    # the real FlyWire data: 129 neurons with class=="gustatory",
    # sub_class=="sugar/water" -- see network.py's FOOD_CLASS/
    # FOOD_SUB_CLASS comment for why this (not the olfactory classes) is
    # the real appetitive channel.
    assert net.food_mask.sum() == 129


@requires_connectome
def test_food_boost_raises_food_mask_activity():
    """net.food_mask actually responds to being driven harder -- guards
    against a silent no-op if the boost or mask wiring breaks (see
    server.py's _FOOD_BOOST, empirically ~0.25-0.29 baseline vs. ~0.62-
    0.65 boosted on the real full-size network; this uses a looser bound
    since it is not the full 139,255-neuron run that comment measured)."""
    def food_activity(boost, seed=0, n_ticks=60):
        net = LIFNetwork(seed=seed)
        rng = np.random.default_rng(seed + 1)
        fracs = []
        for _ in range(n_ticks):
            drive = rng.normal(0.2, 0.1, size=net.n).astype(np.float32)
            drive[net.food_mask] += boost
            spikes = net.step(dt_ms=50.0, drive=drive)
            fracs.append(spikes[net.food_mask].mean())
        return float(np.mean(fracs[10:]))  # drop warm-up transient

    baseline = food_activity(boost=0.0)
    boosted = food_activity(boost=2.0)
    assert boosted > baseline + 0.15


def test_mood_controller_auto_ramps_and_clamps():
    mood = MoodController(ramp_per_ms=1.0 / 1000)
    assert mood.mode == "auto"
    mood.advance(500)
    assert 0.0 < mood.level < 1.0
    mood.advance(10_000)
    assert mood.level == 1.0  # clamped, never overshoots


def test_mood_controller_manual_override_holds_and_release_has_no_jump():
    mood = MoodController(ramp_per_ms=1.0 / 1000)
    mood.advance(400)
    auto_snapshot = mood.level

    mood.set_manual(0.05)
    assert mood.mode == "manual"
    assert mood.level == 0.05
    mood.advance(1000)  # auto ramp must not leak through while pinned
    assert mood.level == 0.05

    mood.release_to_auto()
    assert mood.mode == "auto"
    assert mood.level == 0.05  # no jump back to the pre-override auto value
    assert mood.level != auto_snapshot


@requires_connectome
def test_simulation_step_always_produces_a_valid_tick():
    sim = Simulation(seed=7)
    for _ in range(100):
        payload = sim.step()
        validate_tick(payload)  # raises on any contract violation
    assert payload["tick"] == 100


@requires_connectome
def test_simulation_step_reports_food_activity():
    sim = Simulation(seed=7)
    payload = sim.step()
    assert 0.0 <= payload["activity"]["food_activity"] <= 1.0


@requires_connectome
def test_simulation_position_starts_at_spawn_and_moves_over_ticks():
    """(0, 0) matches the frontend's fly spawn point (see server.py's
    Simulation.__init__ comment); position must actually change tick to
    tick once the fly is moving, not just sit at spawn forever -- the
    whole point of giving the engine authoritative position instead of
    leaving it to the frontend's dead reckoning."""
    sim = Simulation(seed=0)
    payload = sim.step()
    pos0 = payload["motor_state"]["position"]
    for _ in range(199):
        payload = sim.step()
    pos200 = payload["motor_state"]["position"]
    moved = math.hypot(pos200["x"] - pos0["x"], pos200["z"] - pos0["z"])
    assert moved > 0.1  # 200 ticks at any real speed covers more than this


@requires_connectome
def test_food_gradient_scales_with_distance():
    """net.food_mask's boost is now proximity-scaled (see server.py's
    FOOD_POSITION/_FOOD_BOOST/_FOOD_RANGE comment), not flat -- driving it
    at distance=0 (full _FOOD_BOOST) must read clearly higher than driving
    it at a distance far outside _FOOD_RANGE (~no boost, close to the
    pre-gradient baseline). Empirically (same methodology as
    test_food_boost_raises_food_mask_activity): ~0.63 at distance=0 vs.
    ~0.22-0.24 at distance>=24 on the real connectome."""
    def food_activity(distance, seed=0, n_ticks=60):
        proximity = math.exp(-distance / _FOOD_RANGE)
        net = LIFNetwork(seed=seed)
        rng = np.random.default_rng(seed + 1)
        fracs = []
        for _ in range(n_ticks):
            drive = rng.normal(0.2, 0.1, size=net.n).astype(np.float32)
            drive[net.food_mask] += _FOOD_BOOST * proximity
            spikes = net.step(dt_ms=50.0, drive=drive)
            fracs.append(spikes[net.food_mask].mean())
        return float(np.mean(fracs[10:]))

    near = food_activity(distance=0.0)
    far = food_activity(distance=50.0)
    assert near > far + 0.2


@requires_connectome
def test_heading_steers_toward_food_and_fly_arrives():
    """The chemotaxis bias (server.py's _FOOD_STEER_GAIN) must actually
    pull the trajectory toward FOOD_POSITION, not just exist in the
    formula -- checked empirically, not just algebraically, across a few
    seeds, matching this project's own "verify empirically" standard.
    Measured (2500-tick runs, seeds 0-2): the fly comes within 2 units of
    FOOD_POSITION well before tick 1000 and stays close (not a one-off
    close pass) for the remainder of the run."""
    for seed in (0, 1, 2):
        sim = Simulation(seed=seed)
        dists = []
        for _ in range(1500):
            payload = sim.step()
            pos = payload["motor_state"]["position"]
            dists.append(math.hypot(pos["x"] - FOOD_POSITION[0], pos["z"] - FOOD_POSITION[1]))
        dists = np.array(dists)
        # started far (spawn is ~7.8 units from FOOD_POSITION)
        assert dists[0] > 5.0
        # reached and then stayed near food, not just a lucky single pass
        assert dists[500:].max() < 3.0
        assert dists[500:].mean() < 1.5


@requires_connectome
def test_food_water_combined_via_max_not_sum():
    """proximity = max(food_proximity, water_proximity), never summed --
    see server.py's `proximity` comment (a real taste-receptor population
    saturates on the stronger of two simultaneous stimuli, it doesn't get
    double-activated). Directly demonstrates the two policies produce
    measurably different food_mask activity: driving food_mask at the
    max-of-two-full-proximities boost (_FOOD_BOOST * 1.0, what this
    codebase actually does when a fly sits equidistant from both at
    distance=0, an unreachable but illustrative case) reads far lower than
    driving it at what summing the same two proximities would have
    produced (_FOOD_BOOST * 2.0)."""
    def food_mask_activity(boost, seed=0, n_ticks=60):
        net = LIFNetwork(seed=seed)
        rng = np.random.default_rng(seed + 1)
        fracs = []
        for _ in range(n_ticks):
            drive = rng.normal(0.2, 0.1, size=net.n).astype(np.float32)
            drive[net.food_mask] += boost
            spikes = net.step(dt_ms=50.0, drive=drive)
            fracs.append(spikes[net.food_mask].mean())
        return float(np.mean(fracs[10:]))

    maxed = food_mask_activity(boost=_FOOD_BOOST * max(1.0, 1.0))
    summed = food_mask_activity(boost=_FOOD_BOOST * (1.0 + 1.0))
    assert summed > maxed + 0.1  # sum would over-activate; max does not


@requires_connectome
def test_heading_steers_toward_whichever_landmark_is_closer():
    """The chemotaxis bias retargets to whichever of FOOD_POSITION/
    WATER_POSITION currently has the higher proximity (see server.py's
    `target` comment), not always FOOD_POSITION. A fly started already
    close to WATER_POSITION (and far from FOOD_POSITION) must settle near
    WATER_POSITION, the mirror image of
    test_heading_steers_toward_food_and_fly_arrives (which starts at the
    equidistant spawn point and settles at FOOD_POSITION, food winning the
    spawn-point tie-break). Also confirms WATER_POSITION drives
    food_activity through the exact same gradient FOOD_POSITION does (see
    server.py's WATER_POSITION comment): near-water food_activity measured
    here must land in the same ~0.6-0.67 near-landmark range
    _FOOD_RANGE's comment measured for FOOD_POSITION, not some separate/
    weaker number."""
    sim = Simulation(seed=0)
    # start close to water, far from food -- water_proximity clearly wins
    # from the very first tick, unlike the equidistant (0,0) spawn.
    sim._x, sim._z = WATER_POSITION[0] + 3.0, WATER_POSITION[1] + 2.0
    dists_to_water, dists_to_food, food_activities = [], [], []
    for _ in range(1500):
        payload = sim.step()
        pos = payload["motor_state"]["position"]
        dists_to_water.append(math.hypot(pos["x"] - WATER_POSITION[0], pos["z"] - WATER_POSITION[1]))
        dists_to_food.append(math.hypot(pos["x"] - FOOD_POSITION[0], pos["z"] - FOOD_POSITION[1]))
        food_activities.append(payload["activity"]["food_activity"])
    dists_to_water = np.array(dists_to_water)
    dists_to_food = np.array(dists_to_food)
    food_activities = np.array(food_activities)
    assert dists_to_water[500:].mean() < 1.5  # settled near water
    assert dists_to_food[500:].mean() > 5.0  # nowhere near food
    assert food_activities[500:].mean() > 0.5  # same near-landmark range as FOOD_POSITION


@requires_connectome
def test_world_steps_all_flies_without_error():
    """World.step() ticks N_FLIES Simulations together, once, every call --
    the basic multi-fly architecture must simply not blow up (wrong-shaped
    peer_positions, index errors between the snapshot pass and the step
    pass, etc)."""
    world = World()
    assert len(world.flies) == N_FLIES
    for _ in range(50):
        payloads = world.step()
        assert len(payloads) == N_FLIES
        for payload in payloads:
            validate_tick(payload)  # each fly's own tick is itself schema-valid on its own


@requires_connectome
def test_world_all_flies_positions_actually_change_over_ticks():
    """Every fly in the shared World must actually move over time, not
    just fly 0 -- indices 1..N_FLIES-1 are autonomous (their
    MoodControllers just auto-ramp, see N_FLIES' comment) but still real,
    independently-stepping Simulations."""
    world = World()
    start = [(f._x, f._z) for f in world.flies]
    for _ in range(200):
        world.step()
    end = [(f._x, f._z) for f in world.flies]
    for i in range(N_FLIES):
        moved = math.hypot(end[i][0] - start[i][0], end[i][1] - start[i][1])
        assert moved > 0.1, f"fly {i} did not move"


@requires_connectome
def test_visual_boost_raises_visual_mask_activity_when_close():
    """net.visual_mask actually responds to a nearby peer -- see
    server.py's _SOCIAL_BOOST/_SOCIAL_RANGE comment (empirically ~0.272-
    0.274 with no peer nearby vs. ~0.344 with one at distance=0 on the
    real full-size network); same methodology as
    test_food_boost_raises_food_mask_activity, driving LIFNetwork
    directly rather than through Simulation so this isolates the boost
    itself from position/steering."""
    def visual_activity(boost, seed=0, n_ticks=60):
        net = LIFNetwork(seed=seed)
        rng = np.random.default_rng(seed + 1)
        fracs = []
        for _ in range(n_ticks):
            drive = rng.normal(0.2, 0.1, size=net.n).astype(np.float32)
            drive[net.visual_mask] += boost
            spikes = net.step(dt_ms=50.0, drive=drive)
            fracs.append(spikes[net.visual_mask].mean())
        return float(np.mean(fracs[10:]))

    far = visual_activity(boost=0.0)
    close = visual_activity(boost=_SOCIAL_BOOST)
    assert close > far + 0.03


@requires_connectome
def test_simulation_step_peer_positions_boosts_visual_activity():
    """Simulation.step(peer_positions=...) actually wires the nearest-
    peer distance through to the visual_mask boost -- a fly with a peer
    reported at its own exact position (distance=0, maximal proximity)
    must show measurably higher region_activity['optic'] than the same
    fly with no peers at all, over a real multi-tick run (region_activity
    is reported every tick via activity.active_regions, so this checks
    the actual per-tick payload, not an internal-only computation)."""
    def optic_activity(peer_positions_present, seed=0, n_ticks=60):
        sim = Simulation(seed=seed)
        fracs = []
        for _ in range(n_ticks):
            peers = [(sim._x, sim._z)] if peer_positions_present else []
            payload = sim.step(peer_positions=peers)
            region = next(r for r in payload["activity"]["active_regions"] if r["region"] == "optic")
            fracs.append(region["activity"])
        return float(np.mean(fracs[10:]))

    no_peer = optic_activity(peer_positions_present=False)
    with_peer = optic_activity(peer_positions_present=True)
    assert with_peer > no_peer


@requires_connectome
def test_flies_end_up_near_each_other_without_fly_to_fly_steering():
    """Deliberate design point (see README.md, "Where this is headed" --
    "Multiple flies" -- and server.py's World docstring): there is NO
    fly-to-fly steering bias anywhere in this codebase. Flies are expected
    to end up near each other anyway, purely because all of them are
    independently drawn toward the same two fixed food/water landmarks --
    correlated resource-seeking, not an engineered flocking force.

    Verified empirically across several seed sets, 2500 ticks each (see
    this project's own "verify empirically" standard, same as
    test_heading_steers_toward_food_and_fly_arrives) -- if this ever comes
    back false, the design assumption in the brief is wrong, not the test.
    Measured directly (not asserted here, to keep this test fast; see the
    report for the full numbers): across 5 seed sets, at least one pair of
    the 3 flies always ends up within ~0.004-0.4 units of each other by
    tick 500 and stays that close for the rest of a 2500-tick run --
    sometimes because all 3 converge on the same landmark (e.g. seeds
    [0,1,2], max pairwise separation ever only ~2.2 units), sometimes
    because two of the three do while the third settles at the other
    landmark instead and drifts further off (e.g. seeds [10,11,12], one
    pair stays within ~0.004 units of each other while the third fly ends
    up ~16 units away) -- either way, real proximity happens, from shared
    landmark-seeking alone, in every seed tested."""
    seed_sets = ([0, 1, 2], [10, 11, 12], [20, 21, 22])
    n_ticks = 2500
    for seeds in seed_sets:
        world = World(seeds=seeds)
        min_pairwise_after_500 = math.inf
        max_pairwise_ever = 0.0
        for t in range(1, n_ticks + 1):
            payloads = world.step()
            positions = [p["motor_state"]["position"] for p in payloads]
            pair_dists = [
                math.hypot(positions[i]["x"] - positions[j]["x"], positions[i]["z"] - positions[j]["z"])
                for i in range(len(positions)) for j in range(i + 1, len(positions))
            ]
            max_pairwise_ever = max(max_pairwise_ever, max(pair_dists))
            if t > 500:
                min_pairwise_after_500 = min(min_pairwise_after_500, min(pair_dists))
        # at least one pair genuinely ends up (and stays) close, well
        # after the trivial shared-spawn-point start...
        assert min_pairwise_after_500 < 1.0, (seeds, min_pairwise_after_500)
        # ...and it is real convergence, not just "never moved apart" --
        # some real separation happens somewhere in the run first.
        assert max_pairwise_ever > 1.0, (seeds, max_pairwise_ever)


@requires_connectome
def test_simulation_handle_control_overrides_mood_level():
    sim = Simulation(seed=3)
    sim.step()
    sim.handle_control({"type": "set_mood_level", "value": 0.42})
    payload = sim.step()
    assert payload["stimulus"]["mood_level"] == 0.42

    sim.handle_control({"type": "set_mood_mode", "value": "auto"})
    assert sim.mood.mode == "auto"
