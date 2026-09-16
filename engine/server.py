"""FlyBreak engine: ticks the LIF-lite network and streams contract-shaped
JSON over WebSocket. One fly (one Simulation) per connected client.

Run from the repo root with: python -m flybreak.engine.server
"""
from __future__ import annotations

import asyncio
import json

import numpy as np
import websockets
from websockets.asyncio.server import ServerConnection

from flybreak.contract.validator import validate_tick
from flybreak.engine.bac import BacController
from flybreak.engine.network import REGIONS, LIFNetwork

SCHEMA_VERSION = "1.0"
TICK_HZ = 20
DT_MS = 1000.0 / TICK_HZ


class Simulation:
    def __init__(self, seed: int = 0):
        self.net = LIFNetwork(seed=seed)
        self.bac = BacController()
        self.tick = 0
        self.sim_time_ms = 0.0
        self._heading = 0.0
        self._rng = np.random.default_rng(seed)

    def step(self) -> dict:
        self.bac.advance(DT_MS)
        bac_level = self.bac.level

        # bac_level raises the motor region's firing threshold (harder to
        # drive a spike) and adds sensory drive noise. Own design choice,
        # not a documented biological effect -- see flybreak/README.md.
        motor_threshold_scale = 1.0 + bac_level * 1.5
        drive = self._rng.normal(0.15, 0.1 + bac_level * 0.2, size=self.net.n).astype(np.float32)

        spikes = self.net.step(DT_MS, drive, motor_threshold_scale=motor_threshold_scale)
        self.tick += 1
        self.sim_time_ms += DT_MS

        region_activity = self.net.region_activity(spikes)
        spike_count = int(spikes.sum())
        firing_rate_hz = spike_count / self.net.n * TICK_HZ

        motor_activity = region_activity["motor"]
        if bac_level > 0.85 and motor_activity < 0.15:
            action = "frozen"
        elif motor_activity < 0.15:
            action = "idle"
        elif motor_activity < 0.35:
            action = "grooming"
        elif motor_activity < 0.65:
            action = "walking"
        else:
            action = "flying"

        wobble = bac_level * self._rng.normal(0, 25)
        self._heading = (self._heading + self._rng.normal(2, 5) + wobble) % 360
        speed = max(0.0, motor_activity * (1.0 - 0.6 * bac_level))
        if action == "flying":
            wing_state = "buzzing" if motor_activity > 0.8 else "raised"
        else:
            wing_state = "folded"

        return {
            "schema_version": SCHEMA_VERSION,
            "tick": self.tick,
            "sim_time_ms": self.sim_time_ms,
            "stimulus": {"bac_level": bac_level, "other_inputs": {}},
            "activity": {
                "spike_count": spike_count,
                "firing_rate_hz": firing_rate_hz,
                "active_regions": [{"region": r, "activity": region_activity[r]} for r in REGIONS],
            },
            "motor_state": {
                "action": action,
                "heading_deg": self._heading,
                "speed": speed,
                "wing_state": wing_state,
            },
            "meta": {"status": "running", "notes": ""},
        }

    def handle_control(self, message: dict) -> None:
        msg_type = message.get("type")
        if msg_type == "set_bac_level":
            self.bac.set_manual(float(message["value"]))
        elif msg_type == "set_bac_mode" and message.get("value") == "auto":
            self.bac.release_to_auto()


async def _read_controls(ws: ServerConnection, sim: Simulation) -> None:
    async for raw in ws:
        try:
            sim.handle_control(json.loads(raw))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            pass  # a malformed control message from one client must not kill the tick loop


async def _handle_client(ws: ServerConnection) -> None:
    sim = Simulation()
    reader_task = asyncio.create_task(_read_controls(ws, sim))
    try:
        while True:
            payload = sim.step()
            validate_tick(payload)
            await ws.send(json.dumps(payload))
            await asyncio.sleep(DT_MS / 1000.0)
    finally:
        reader_task.cancel()


async def main(host: str = "127.0.0.1", port: int = 8765) -> None:
    async with websockets.serve(_handle_client, host, port):
        print(f"FlyBreak engine listening on ws://{host}:{port}")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
