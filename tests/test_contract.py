import jsonschema
import pytest

from contract.validator import is_valid_tick, validate_tick

VALID_TICK = {
    "schema_version": "1.2",
    "tick": 4821,
    "sim_time_ms": 300,
    "stimulus": {"mood_level": 0.72, "other_inputs": {}},
    "activity": {
        "spike_count": 2069,
        "firing_rate_hz": 311,
        "active_regions": [
            {"region": "central", "activity": 0.72},
            {"region": "descending", "activity": 0.41},
            {"region": "optic", "activity": 0.15},
        ],
        "food_activity": 0.63,
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


def test_mood_level_out_of_range_rejected():
    tick = {**VALID_TICK, "stimulus": {"mood_level": 1.5, "other_inputs": {}}}
    assert not is_valid_tick(tick)


def test_food_activity_out_of_range_rejected():
    tick = {**VALID_TICK, "activity": {**VALID_TICK["activity"], "food_activity": 1.2}}
    assert not is_valid_tick(tick)


def test_food_activity_missing_rejected():
    activity = {k: v for k, v in VALID_TICK["activity"].items() if k != "food_activity"}
    tick = {**VALID_TICK, "activity": activity}
    assert not is_valid_tick(tick)


def test_unknown_field_rejected():
    tick = {**VALID_TICK, "extra_field": True}
    assert not is_valid_tick(tick)


def test_wrong_schema_version_rejected():
    tick = {**VALID_TICK, "schema_version": "2.0"}
    assert not is_valid_tick(tick)
