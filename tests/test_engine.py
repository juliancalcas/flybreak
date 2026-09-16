from flybreak.contract.validator import validate_tick
from flybreak.engine.bac import BacController
from flybreak.engine.network import REGIONS, LIFNetwork
from flybreak.engine.server import Simulation


def test_network_step_shapes():
    net = LIFNetwork(seed=0)
    import numpy as np

    drive = np.zeros(net.n, dtype=np.float32)
    spikes = net.step(dt_ms=50.0, drive=drive)
    assert spikes.shape == (net.n,)
    activity = net.region_activity(spikes)
    assert set(activity) == set(REGIONS)
    assert all(0.0 <= v <= 1.0 for v in activity.values())


def test_bac_controller_auto_ramps_and_clamps():
    bac = BacController(ramp_per_ms=1.0 / 1000)
    assert bac.mode == "auto"
    bac.advance(500)
    assert 0.0 < bac.level < 1.0
    bac.advance(10_000)
    assert bac.level == 1.0  # clamped, never overshoots


def test_bac_controller_manual_override_holds_and_release_has_no_jump():
    bac = BacController(ramp_per_ms=1.0 / 1000)
    bac.advance(400)
    auto_snapshot = bac.level

    bac.set_manual(0.05)
    assert bac.mode == "manual"
    assert bac.level == 0.05
    bac.advance(1000)  # auto ramp must not leak through while pinned
    assert bac.level == 0.05

    bac.release_to_auto()
    assert bac.mode == "auto"
    assert bac.level == 0.05  # no jump back to the pre-override auto value
    assert bac.level != auto_snapshot


def test_simulation_step_always_produces_a_valid_tick():
    sim = Simulation(seed=7)
    for _ in range(100):
        payload = sim.step()
        validate_tick(payload)  # raises on any contract violation
    assert payload["tick"] == 100


def test_simulation_handle_control_overrides_bac_level():
    sim = Simulation(seed=3)
    sim.step()
    sim.handle_control({"type": "set_bac_level", "value": 0.42})
    payload = sim.step()
    assert payload["stimulus"]["bac_level"] == 0.42

    sim.handle_control({"type": "set_bac_mode", "value": "auto"})
    assert sim.bac.mode == "auto"
