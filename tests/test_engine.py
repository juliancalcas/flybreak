import numpy as np
import pytest

from contract.validator import validate_tick
from engine.mood import MoodController
from engine.network import LIFNetwork, data_available
from engine.server import Simulation

# LIFNetwork/Simulation load the real ~50 MB FlyWire connectome (see
# network.py) -- these tests need `python -m flybreak.engine.fetch_connectome`
# to have been run on this machine first. Skipping (not failing) when it
# hasn't is what keeps `pytest flybreak/tests` usable on a fresh checkout;
# the MoodController tests below need no data and always run.
requires_connectome = pytest.mark.skipif(
    not data_available(),
    reason="run `python -m flybreak.engine.fetch_connectome` first",
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
def test_simulation_handle_control_overrides_mood_level():
    sim = Simulation(seed=3)
    sim.step()
    sim.handle_control({"type": "set_mood_level", "value": 0.42})
    payload = sim.step()
    assert payload["stimulus"]["mood_level"] == 0.42

    sim.handle_control({"type": "set_mood_mode", "value": "auto"})
    assert sim.mood.mode == "auto"
