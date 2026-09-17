import jsonschema
import pytest

from contract.validator import is_valid_graph, is_valid_tick, validate_graph, validate_tick

VALID_TICK = {
    "schema_version": "1.5",
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
        "sample_spikes": [3, 17, 42],
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


def test_sample_spikes_missing_rejected():
    activity = {k: v for k, v in VALID_TICK["activity"].items() if k != "sample_spikes"}
    tick = {**VALID_TICK, "activity": activity}
    assert not is_valid_tick(tick)


def test_sample_spikes_empty_array_accepted():
    """A tick where nothing in the sample spiked this tick is still a
    valid tick -- see server.py's Simulation.step(), which sends an empty
    list rather than omitting the field."""
    tick = {**VALID_TICK, "activity": {**VALID_TICK["activity"], "sample_spikes": []}}
    assert is_valid_tick(tick)


def test_sample_spikes_negative_id_rejected():
    tick = {**VALID_TICK, "activity": {**VALID_TICK["activity"], "sample_spikes": [-1]}}
    assert not is_valid_tick(tick)


def test_sample_spikes_non_integer_rejected():
    tick = {**VALID_TICK, "activity": {**VALID_TICK["activity"], "sample_spikes": [1.5]}}
    assert not is_valid_tick(tick)


# --- synapse_graph (schema_graph_v1.json) ----------------------------------

VALID_GRAPH = {
    "type": "synapse_graph",
    "nodes": [
        {"id": 0, "region": "optic", "is_food": False, "is_motor": False, "is_visual": True},
        {"id": 1, "region": "motor", "is_food": False, "is_motor": True, "is_visual": False},
        {"id": 2, "region": "gustatory", "is_food": True, "is_motor": False, "is_visual": False},
    ],
    "edges": [
        {"source": 0, "target": 1, "weight": 0.4},
        {"source": 1, "target": 2, "weight": -0.2},
    ],
}


def test_valid_graph_passes():
    validate_graph(VALID_GRAPH)
    assert is_valid_graph(VALID_GRAPH)


def test_graph_missing_type_rejected():
    graph = {k: v for k, v in VALID_GRAPH.items() if k != "type"}
    with pytest.raises(jsonschema.ValidationError):
        validate_graph(graph)
    assert not is_valid_graph(graph)


def test_graph_wrong_type_rejected():
    graph = {**VALID_GRAPH, "type": "tick"}
    assert not is_valid_graph(graph)


def test_graph_node_missing_field_rejected():
    nodes = [{k: v for k, v in VALID_GRAPH["nodes"][0].items() if k != "region"}] + VALID_GRAPH["nodes"][1:]
    graph = {**VALID_GRAPH, "nodes": nodes}
    assert not is_valid_graph(graph)


def test_graph_edge_missing_field_rejected():
    edges = [{k: v for k, v in VALID_GRAPH["edges"][0].items() if k != "weight"}]
    graph = {**VALID_GRAPH, "edges": edges}
    assert not is_valid_graph(graph)


def test_graph_unknown_field_rejected():
    graph = {**VALID_GRAPH, "extra_field": True}
    assert not is_valid_graph(graph)


def test_graph_edge_negative_source_rejected():
    graph = {**VALID_GRAPH, "edges": [{"source": -1, "target": 0, "weight": 0.1}]}
    assert not is_valid_graph(graph)


def test_tick_and_graph_schemas_are_distinguishable():
    """A regular tick (no "type" field) must not validate as a graph
    message, and vice versa -- this is exactly what lets the frontend
    tell the two message shapes apart on the same socket."""
    assert not is_valid_graph(VALID_TICK)
    assert not is_valid_tick(VALID_GRAPH)
