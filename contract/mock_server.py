"""Serves synthetic, schema-valid ticks over the same WebSocket contract as
the real engine -- for iterating on the frontend before/without the LIF
network running. Same port and message shapes as flybreak.engine.server, so
the frontend does not know which one it is talking to.

Run from the repo root with: python -m flybreak.contract.mock_server
"""
from __future__ import annotations

import asyncio
import json
import math
import random

import websockets
from websockets.asyncio.server import ServerConnection

from flybreak.contract.validator import validate_tick
from flybreak.engine.network import REGIONS

TICK_HZ = 20
DT_MS = 1000.0 / TICK_HZ


def _mock_tick(tick: int, sim_time_ms: float, bac_level: float) -> dict:
    t = sim_time_ms / 1000.0
    active_regions = [
        {"region": region, "activity": (math.sin(t * 0.7 + i) + 1) / 2}
        for i, region in enumerate(REGIONS)
    ]
    motor_activity = active_regions[-1]["activity"]
    action = "flying" if motor_activity > 0.7 else "walking" if motor_activity > 0.4 else "idle"
    return {
        "schema_version": "1.0",
        "tick": tick,
        "sim_time_ms": sim_time_ms,
        "stimulus": {"bac_level": bac_level, "other_inputs": {}},
        "activity": {
            "spike_count": random.randint(0, 400),
            "firing_rate_hz": random.uniform(0, 400),
            "active_regions": active_regions,
        },
        "motor_state": {
            "action": action,
            "heading_deg": (t * 20) % 360,
            "speed": motor_activity,
            "wing_state": "buzzing" if action == "flying" else "folded",
        },
        "meta": {"status": "running", "notes": "mock stream"},
    }


async def _handle_client(ws: ServerConnection) -> None:
    tick = 0
    sim_time_ms = 0.0
    bac_level = 0.0

    async def read_controls():
        nonlocal bac_level
        async for raw in ws:
            try:
                message = json.loads(raw)
                if message.get("type") == "set_bac_level":
                    bac_level = max(0.0, min(1.0, float(message["value"])))
                elif message.get("type") == "set_bac_mode" and message.get("value") == "auto":
                    pass  # the mock never auto-ramps; nothing to release to
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                pass

    reader_task = asyncio.create_task(read_controls())
    try:
        while True:
            tick += 1
            sim_time_ms += DT_MS
            payload = _mock_tick(tick, sim_time_ms, bac_level)
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
