import math

import numpy as np
import pytest

from contract.validator import validate_tick
from engine.mood import MoodController
from engine.network import LIFNetwork, data_available
from engine.server import FOOD_POSITION, Simulation, _FOOD_BOOST, _FOOD_RANGE

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
def test_simulation_handle_control_overrides_mood_level():
    sim = Simulation(seed=3)
    sim.step()
    sim.handle_control({"type": "set_mood_level", "value": 0.42})
    payload = sim.step()
    assert payload["stimulus"]["mood_level"] == 0.42

    sim.handle_control({"type": "set_mood_mode", "value": "auto"})
    assert sim.mood.mode == "auto"
