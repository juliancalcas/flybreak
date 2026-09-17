import jsonschema
import pytest

from contract.validator import is_valid_tick, validate_tick

VALID_TICK = {
    "schema_version": "1.4",
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
    "motor_state": {
        "action": "walking", "heading_deg": 187, "speed": 0.3, "wing_state": "folded",
        "position": {"x": 3.4, "z": 2.1},
    },
    "meta": {"status": "decompressing", "notes": ""},
    "world": {
        "other_flies": [
            {"id": 1, "position": {"x": -1.2, "z": 4.5}, "heading_deg": 42, "action": "idle", "wing_state": "folded"},
            {"id": 2, "position": {"x": 5.0, "z": -2.3}, "heading_deg": 301, "action": "flying", "wing_state": "buzzing"},
        ]
    },
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


def test_position_missing_rejected():
    motor_state = {k: v for k, v in VALID_TICK["motor_state"].items() if k != "position"}
    tick = {**VALID_TICK, "motor_state": motor_state}
    assert not is_valid_tick(tick)


def test_world_missing_rejected():
    tick = {k: v for k, v in VALID_TICK.items() if k != "world"}
    assert not is_valid_tick(tick)


def test_other_fly_missing_field_rejected():
    other_flies = [{k: v for k, v in VALID_TICK["world"]["other_flies"][0].items() if k != "wing_state"}]
    tick = {**VALID_TICK, "world": {"other_flies": other_flies}}
    assert not is_valid_tick(tick)


def test_unknown_field_rejected():
    tick = {**VALID_TICK, "extra_field": True}
    assert not is_valid_tick(tick)


def test_wrong_schema_version_rejected():
    tick = {**VALID_TICK, "schema_version": "2.0"}
    assert not is_valid_tick(tick)
