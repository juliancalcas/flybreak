import jsonschema
import pytest

from flybreak.contract.validator import is_valid_tick, validate_tick

VALID_TICK = {
    "schema_version": "1.0",
    "tick": 4821,
    "sim_time_ms": 300,
    "stimulus": {"bac_level": 0.20, "other_inputs": {}},
    "activity": {
        "spike_count": 2069,
        "firing_rate_hz": 311,
        "active_regions": [
            {"region": "mushroom_body", "activity": 0.72},
            {"region": "central_complex", "activity": 0.41},
            {"region": "optic_lobe", "activity": 0.15},
        ],
    },
    "motor_state": {"action": "walking", "heading_deg": 187, "speed": 0.3, "wing_state": "folded"},
    "meta": {"status": "decompressing", "notes": ""},
}


def test_valid_tick_passes():
    validate_tick(VALID_TICK)
    assert is_valid_tick(VALID_TICK)


def test_missing_field_rejected():
    tick = {k: v for k, v in VALID_TICK.items() if k != "motor_state"}
    with pytest.raises(jsonschema.ValidationError):
        validate_tick(tick)
    assert not is_valid_tick(tick)


def test_bac_level_out_of_range_rejected():
    tick = {**VALID_TICK, "stimulus": {"bac_level": 1.5, "other_inputs": {}}}
    assert not is_valid_tick(tick)


def test_unknown_field_rejected():
    tick = {**VALID_TICK, "extra_field": True}
    assert not is_valid_tick(tick)


def test_wrong_schema_version_rejected():
    tick = {**VALID_TICK, "schema_version": "2.0"}
    assert not is_valid_tick(tick)
