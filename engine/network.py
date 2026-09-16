"""Minimal LIF-lite network standing in for the real FlyWire connectome.

This is NOT the real connectome. It is a small, synthetic, region-tagged
graph sized to tick instantly on any machine, so the WebSocket contract and
the frontend can be built and iterated on before the real ~130k-neuron
FlyWire matrix is wired in -- see flybreak/README.md, "Swapping in the real
connectome".
"""
from __future__ import annotations

import numpy as np

REGIONS = ["mushroom_body", "central_complex", "optic_lobe", "antennal_lobe", "motor"]

# neurons per region -- small enough that a tick costs microseconds
_REGION_SIZE = {
    "mushroom_body": 120,
    "central_complex": 80,
    "optic_lobe": 150,
    "antennal_lobe": 60,
    "motor": 40,
}

_TAU_MS = 20.0
_V_RESET = 0.0
_V_THRESH = 1.0
_V_REST = 0.0


class LIFNetwork:
    """A leaky integrate-and-fire population, grouped into named regions."""

    def __init__(self, seed: int = 0):
        rng = np.random.default_rng(seed)
        region_of = []
        for region in REGIONS:
            region_of += [region] * _REGION_SIZE[region]
        self.region_of = np.array(region_of)
        self.n = len(self.region_of)

        # sparse random connectivity, biased feedforward along REGIONS
        # order (sensory -> integration -> motor).
        w = rng.normal(0, 1.0, size=(self.n, self.n)) * (rng.random((self.n, self.n)) < 0.02)
        rank_of_region = {region: i for i, region in enumerate(REGIONS)}
        region_rank = np.array([rank_of_region[r] for r in self.region_of])
        feedforward_mask = region_rank[None, :] <= region_rank[:, None]
        w *= feedforward_mask
        np.fill_diagonal(w, 0.0)
        self.w = w.astype(np.float32)

        self.v = np.full(self.n, _V_REST, dtype=np.float32)
        self.last_spikes = np.zeros(self.n, dtype=bool)
        self._rng = rng

    def step(self, dt_ms: float, drive: np.ndarray, motor_threshold_scale: float = 1.0) -> np.ndarray:
        """Advances one tick given an external `drive` per neuron.

        Returns the boolean spike vector for this tick.
        """
        leak = -self.v / _TAU_MS * dt_ms
        recurrent = self.last_spikes.astype(np.float32) @ self.w
        noise = self._rng.normal(0, 0.05, size=self.n).astype(np.float32)
        self.v = self.v + leak + recurrent * 0.05 + drive + noise

        threshold = np.where(self.region_of == "motor", _V_THRESH * motor_threshold_scale, _V_THRESH)
        spikes = self.v >= threshold
        self.v = np.where(spikes, _V_RESET, self.v)
        self.last_spikes = spikes
        return spikes

    def region_activity(self, spikes: np.ndarray) -> dict[str, float]:
        """Fraction of each region's neurons that spiked this tick."""
        activity = {}
        for region in REGIONS:
            mask = self.region_of == region
            activity[region] = float(spikes[mask].mean()) if mask.any() else 0.0
        return activity
