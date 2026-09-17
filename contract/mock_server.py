"""Serves synthetic, schema-valid ticks over the same WebSocket contract as
the real engine -- for iterating on the frontend before/without the LIF
network running. Same port and message shapes as engine.server, so the
frontend does not know which one it is talking to.

Run from the repo root with: python -m contract.mock_server
"""
from __future__ import annotations

import asyncio
import json
import math
import random

import websockets
from websockets.asyncio.server import ServerConnection

from contract.validator import validate_graph, validate_tick

TICK_HZ = 20
DT_MS = 1000.0 / TICK_HZ

# The real engine's regions are FlyWire's own super_class categories,
# discovered at runtime from the connectome data (see network.py) -- the
# mock has no data to discover them from, so it hardcodes the same ten
# names purely so a frontend built against this mock sees realistic
# region labels before ever touching the real engine.
_MOCK_REGIONS = [
    "ascending", "central", "descending", "endocrine", "motor",
    "optic", "sensory", "sensory_ascending", "visual_centrifugal", "visual_projection",
]

# Synthetic stand-in for network.get_sample_graph()'s real snowball-
# sampled subgraph (see network.py's own comment for what the real thing
# is: a real BFS-connected induced subgraph of the real ~2.7M-synapse
# connectome, ~200-250 nodes). The mock has no connectome and no real `w`
# matrix to sample from, so this is NOT real connectivity of any kind --
# just a small fixed ring-plus-chords graph, deterministic and clearly
# synthetic, purely so a frontend built against the mock has some
# `synapse_graph`/`sample_spikes` shape to render before ever touching the
# real engine. Node count deliberately sits inside the real sample's
# ~150-250 range so a frontend tuned against this mock isn't tuned to a
# wildly different scale than the real one.
_MOCK_GRAPH_NODE_COUNT = 180


def _build_mock_graph() -> dict:
    nodes = [
        {
            "id": i,
            "region": _MOCK_REGIONS[i % len(_MOCK_REGIONS)],
            # every 9th/13th/23rd node flagged, purely to give a frontend
            # some real mix of true/false to style against -- not derived
            # from anything, unlike the real engine's actual food/motor/
            # visual masks.
            "is_food": i % 23 == 0,
            "is_motor": i % 9 == 0,
            "is_visual": i % 13 == 0,
        }
        for i in range(_MOCK_GRAPH_NODE_COUNT)
    ]
    edges = []
    for i in range(_MOCK_GRAPH_NODE_COUNT):
        # ring (every node connects to its next neighbor) plus a few
        # longer chords, so the mock graph is connected and has some
        # visual structure beyond a bare ring -- still purely synthetic,
        # weight magnitude picked to land in the same rough range the
        # real engine's capped synapse-count weight does (see network.py's
        # _SYN_COUNT_CAP comment).
        target = (i + 1) % _MOCK_GRAPH_NODE_COUNT
        weight = 0.2 + 0.6 * ((i * 37) % 100) / 100.0
        if i % 3 == 0:
            weight = -weight  # some synthetic inhibitory-looking edges too
        edges.append({"source": i, "target": target, "weight": weight})
        if i % 5 == 0:
            chord_target = (i + _MOCK_GRAPH_NODE_COUNT // 2) % _MOCK_GRAPH_NODE_COUNT
            edges.append({"source": i, "target": chord_target, "weight": 0.4})
    return {"type": "synapse_graph", "nodes": nodes, "edges": edges}


_MOCK_GRAPH = _build_mock_graph()


def _mock_tick(tick: int, sim_time_ms: float, mood_level: float) -> dict:
    t = sim_time_ms / 1000.0
    active_regions = [
        {"region": region, "activity": (math.sin(t * 0.7 + i) + 1) / 2}
        for i, region in enumerate(_MOCK_REGIONS)
    ]
    motor_activity = active_regions[-1]["activity"]
    action = "flying" if motor_activity > 0.7 else "walking" if motor_activity > 0.4 else "idle"
    # plausible stand-in for the real engine's food_mask EMA (see
    # server.py's _FOOD_RANGE comment for the measured real range: ~0.22-
    # 0.24 far from food, ~0.63-0.67 at/near FOOD_POSITION under the
    # distance gradient) -- oscillates across roughly that same span so a
    # frontend built against the mock sees a realistic value before ever
    # touching the real engine.
    food_activity = 0.3 + 0.35 * ((math.sin(t * 0.3 + 3.0) + 1) / 2)
    # plausible stand-in for the real engine's activity.sample_spikes (see
    # server.py's Simulation.step()) -- the mock has no LIF network or
    # real sample to look real spikes up in, so this just draws a handful
    # of ids at random from the synthetic mock graph's own node range each
    # tick, same "realistic-looking, not modeling anything" spirit as
    # `position` above.
    sample_spikes = random.sample(range(_MOCK_GRAPH_NODE_COUNT), k=random.randint(0, 8))
    # plausible stand-in for the real engine's now-authoritative
    # (self._x, self._z) (see server.py's FOOD_POSITION/_POSITION_STEP
    # comments) -- the mock has no LIF network or chemotaxis steering to
    # actually walk the fly toward FOOD_POSITION (6, 5), so it just drifts
    # in a slow circle around the origin, big enough (radius 5, close to
    # the real food distance of ~7.8) to look like real wandering to a
    # frontend built against this mock, without pretending to model
    # anything.
    position = {"x": math.cos(t * 0.05) * 5.0, "z": math.sin(t * 0.05) * 5.0}
    # plausible stand-in for World.other_flies (see server.py's World/
    # N_FLIES=3 -- schema "1.4") -- the mock has no World/Simulation
    # instances for the other two flies either, so these two just wander
    # independently in their own slow circles (different phase/radius/
    # speed each, so they visibly don't move in lockstep with fly 0 or
    # each other), same spirit as `position` above: realistic-looking, not
    # modeling anything.
    other_fly_1_action = "walking" if (math.sin(t * 0.4) + 1) / 2 > 0.4 else "idle"
    other_fly_2_action = "flying" if (math.sin(t * 0.25 + 1.0) + 1) / 2 > 0.65 else "walking"
    other_flies = [
        {
            "id": 1,
            "position": {"x": math.cos(t * 0.08 + 2.0) * 4.0, "z": math.sin(t * 0.08 + 2.0) * 4.0},
            "heading_deg": (t * 15 + 90) % 360,
            "action": other_fly_1_action,
            "wing_state": "buzzing" if other_fly_1_action == "flying" else "folded",
        },
        {
            "id": 2,
            "position": {"x": math.cos(t * 0.04 + 4.5) * 6.0, "z": math.sin(t * 0.04 + 4.5) * 6.0},
            "heading_deg": (t * 25 + 200) % 360,
            "action": other_fly_2_action,
            "wing_state": "raised" if other_fly_2_action == "flying" else "folded",
        },
    ]
    return {
        "schema_version": "1.5",
        "tick": tick,
        "sim_time_ms": sim_time_ms,
        "stimulus": {"mood_level": mood_level, "other_inputs": {}},
        "activity": {
            "spike_count": random.randint(0, 400),
            "firing_rate_hz": random.uniform(0, 400),
            "active_regions": active_regions,
            "food_activity": food_activity,
            "sample_spikes": sample_spikes,
        },
        "motor_state": {
            "action": action,
            "heading_deg": (t * 20) % 360,
            "speed": motor_activity,
            "wing_state": "buzzing" if action == "flying" else "folded",
            "position": position,
        },
        "meta": {"status": "running", "notes": "mock stream"},
        "world": {"other_flies": other_flies},
    }


async def _handle_client(ws: ServerConnection) -> None:
    # Same one-time-per-connection "synapse_graph" message the real engine
    # sends (see server.py's _handle_client) -- here it's the synthetic
    # _MOCK_GRAPH built above, not real connectivity of any kind.
    validate_graph(_MOCK_GRAPH)
    await ws.send(json.dumps(_MOCK_GRAPH))

    tick = 0
    sim_time_ms = 0.0
    mood_level = 0.0

    async def read_controls():
        nonlocal mood_level
        async for raw in ws:
            try:
                message = json.loads(raw)
                if message.get("type") == "set_mood_level":
                    mood_level = max(0.0, min(1.0, float(message["value"])))
                elif message.get("type") == "set_mood_mode" and message.get("value") == "auto":
                    pass  # the mock never auto-ramps; nothing to release to
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                pass

    reader_task = asyncio.create_task(read_controls())
    try:
        while True:
            tick += 1
            sim_time_ms += DT_MS
            payload = _mock_tick(tick, sim_time_ms, mood_level)
            validate_tick(payload)
            await ws.send(json.dumps(payload))
            await asyncio.sleep(DT_MS / 1000.0)
    finally:
        reader_task.cancel()


async def main(host: str = "127.0.0.1", port: int = 8765) -> None:
    async with websockets.serve(_handle_client, host, port):
        print(f"FlyBreak mock stream listening on ws://{host}:{port}")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
