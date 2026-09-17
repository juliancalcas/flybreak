"""Loads the real FlyWire FAFB v783 connectome and runs it as a sparse
leaky integrate-and-fire network.

Data: engine/data/{connections,classification}.csv.gz -- fetch once per
machine with `python -m engine.fetch_connectome` (see that module's
docstring for source, license and citation). Not vendored in git, see
.gitignore: at ~50 MB combined this is exactly the kind of external
binary liveries/CLAUDE.md's own "What git versions and what it does not"
keeps out of the repo, for the same reason.

139,255 neurons. connections.csv ships ~3.9M per-synapse-annotation rows;
building the sparse matrix below collapses them onto ~2.7M unique (pre,
post) pairs (csr_matrix sums duplicate (row, col) entries on
construction, which is exactly the collapse wanted: several synapse rows
between the same two neurons should add, not overwrite).

Load time is ~10-15s (pure csv+gzip, no pandas dependency) and happens
once per process via _load_connectome's cache -- NOT once per client
connection, which is why LIFNetwork.__init__ reads from that cache
instead of re-parsing the CSVs itself. Tested at ~5ms/tick on the full
network, comfortably inside the 50ms budget of a 20 Hz tick.
"""
from __future__ import annotations

import csv
import gzip
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy import sparse

_DATA_DIR = Path(__file__).parent / "data"
_CLASSIFICATION = _DATA_DIR / "classification.csv.gz"
_CONNECTIONS = _DATA_DIR / "connections.csv.gz"

_TAU_MS = 20.0
_V_RESET = 0.0
_V_THRESH = 1.0
_V_REST = 0.0

# The real analog of "motor region" for bac_level's effect (see
# server.py): FlyWire's own super_class annotation for neurons whose
# output IS a motor command, or that descend from the brain toward the
# motor system in the ventral nerve cord.
MOTOR_SUPER_CLASSES = frozenset({"motor", "descending"})

# The real analog of "food is present" (see server.py's static food
# boost). Not `super_class` this time -- FlyWire's finer-grained
# `class`/`sub_class` columns are what actually carry appetitive/aversive
# taste *identity* in this data. `class == "gustatory"`, `sub_class ==
# "sugar/water"` is a real, labeled appetitive taste channel (129
# neurons); `sub_class == "bitter"` (65 neurons) is the real aversive one
# and is deliberately never masked or read anywhere in this codebase --
# see README.md, "Where this is headed", "No fear stimulus, ever".
# (`class == "olfactory"`'s two sub-populations -- 1851 neurons with
# `sub_class == ""` and 430 with `sub_class == "pheromone"` -- carry no
# food/danger identity in the real data; an earlier work cycle's
# `OLF_ORN_FOOD`/`OLF_ORN_DANGER` names for them do not actually exist in
# this dataset and have been corrected here and in README.md.)
FOOD_CLASS = "gustatory"
FOOD_SUB_CLASS = "sugar/water"

# GABA is the fly CNS's primary fast inhibitory transmitter; glutamate
# acts through inhibitory glutamate-gated chloride channels in insects
# (unlike its excitatory role in vertebrates) -- both well-established,
# not invented for this project. ACH (the dominant fast excitatory
# transmitter) plus the three neuromodulators present in the data (DA,
# SER, OCT) are treated as excitatory here, a real simplification for
# those three: at LIF timescales they modulate rather than directly drive
# spiking, and this model has no separate neuromodulatory pathway to
# route them through.
_INHIBITORY_NT = frozenset({"GABA", "GLUT"})

# Caps synapse-count weight (median 6, mean 8.8, max 2405 in the real
# data) so a handful of outlier connections cannot dominate a tick.
_SYN_COUNT_CAP = 30.0


def data_available() -> bool:
    return _CLASSIFICATION.is_file() and _CONNECTIONS.is_file()


def warm_cache() -> None:
    """Forces the one-time ~10-15s CSV parse now, rather than lazily on the
    first `LIFNetwork()`. That first construction is fully synchronous
    (plain csv/gzip/numpy, no I/O yielding), so if it happens inside an
    asyncio event loop instead of before it starts serving, it blocks
    every connection -- not just the one that triggered it -- for the
    whole load. See server.py's `main()`, which calls this before
    `websockets.serve()`.
    """
    _load_connectome()


@dataclass(frozen=True)
class _Connectome:
    n: int
    region_of: np.ndarray          # (n,) str, FlyWire's super_class per neuron
    regions: list                  # sorted distinct values of region_of
    region_masks: dict             # region -> (n,) bool mask, precomputed once
    motor_mask: np.ndarray         # (n,) bool, region_of in MOTOR_SUPER_CLASSES
    food_mask: np.ndarray          # (n,) bool, class==FOOD_CLASS & sub_class==FOOD_SUB_CLASS
    w: sparse.csr_matrix           # (n, n), w[post, pre] = signed synapse weight


def _read_classification() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    ids, supers, classes, sub_classes = [], [], [], []
    with gzip.open(_CLASSIFICATION, "rt", newline="") as f:
        reader = csv.reader(f)
        next(reader)  # header
        for row in reader:
            ids.append(int(row[0]))
            supers.append(row[2] or "unclassified")
            classes.append(row[3])
            sub_classes.append(row[4])
    return (np.array(ids, dtype=np.int64), np.array(supers, dtype=object),
            np.array(classes, dtype=object), np.array(sub_classes, dtype=object))


def _read_connections() -> tuple[list, list, list, list]:
    pre, post, syn, nt = [], [], [], []
    with gzip.open(_CONNECTIONS, "rt", newline="") as f:
        reader = csv.reader(f)
        next(reader)  # header
        for row in reader:
            pre.append(row[0])
            post.append(row[1])
            syn.append(row[3])
            nt.append(row[4])
    return pre, post, syn, nt


@lru_cache(maxsize=1)
def _load_connectome() -> _Connectome:
    if not data_available():
        raise FileNotFoundError(
            "engine/data/ is missing the connectome CSVs -- run "
            "`python -m engine.fetch_connectome` once on this machine"
        )
    root_ids, region_of, class_of, sub_class_of = _read_classification()
    n = len(root_ids)
    regions = sorted(set(region_of.tolist()))
    region_masks = {r: (region_of == r) for r in regions}
    motor_mask = np.isin(region_of, list(MOTOR_SUPER_CLASSES))
    food_mask = (class_of == FOOD_CLASS) & (sub_class_of == FOOD_SUB_CLASS)

    index_of_id = {int(v): i for i, v in enumerate(root_ids.tolist())}
    pre_ids, post_ids, syn_strs, nt_types = _read_connections()

    pre = np.fromiter((index_of_id.get(int(x), -1) for x in pre_ids),
                       dtype=np.int64, count=len(pre_ids))
    post = np.fromiter((index_of_id.get(int(x), -1) for x in post_ids),
                        dtype=np.int64, count=len(post_ids))
    valid = (pre >= 0) & (post >= 0)
    pre = pre[valid].astype(np.int32)
    post = post[valid].astype(np.int32)

    syn_count = np.array(syn_strs, dtype=np.float32)[valid]
    nt_type = np.array(nt_types, dtype=object)[valid]
    sign = np.where(np.isin(nt_type, list(_INHIBITORY_NT)), -1.0, 1.0).astype(np.float32)
    weight = np.clip(syn_count, 1.0, _SYN_COUNT_CAP) / _SYN_COUNT_CAP * sign

    # row = postsynaptic neuron (who receives this tick's input), col =
    # presynaptic (who spiked last tick) -- so `w @ spikes` sums each
    # neuron's real synaptic input.
    w = sparse.csr_matrix((weight, (post, pre)), shape=(n, n))

    return _Connectome(n=n, region_of=region_of, regions=regions,
                        region_masks=region_masks, motor_mask=motor_mask,
                        food_mask=food_mask, w=w)


class LIFNetwork:
    """The real FlyWire connectome (139,255 neurons) as a sparse LIF
    population, grouped by FlyWire's own `super_class` annotation.

    Requires `python -m engine.fetch_connectome` to have been run once on
    this machine. The expensive, shared parts (connectivity, region
    grouping) are loaded once per process via `_load_connectome`'s cache;
    each instance only owns its own membrane potential / spike state, so
    creating one per client connection is cheap.
    """

    def __init__(self, seed: int = 0):
        connectome = _load_connectome()
        self.n = connectome.n
        self.region_of = connectome.region_of
        self.regions = connectome.regions
        self._region_masks = connectome.region_masks
        self.motor_mask = connectome.motor_mask
        self.food_mask = connectome.food_mask
        self.w = connectome.w

        self.v = np.full(self.n, _V_REST, dtype=np.float32)
        self.last_spikes = np.zeros(self.n, dtype=bool)
        self._rng = np.random.default_rng(seed)

    def step(self, dt_ms: float, drive: np.ndarray, motor_threshold_scale: float = 1.0) -> np.ndarray:
        """Advances one tick given an external `drive` per neuron.

        Returns the boolean spike vector for this tick.
        """
        leak = -self.v / _TAU_MS * dt_ms
        recurrent = self.w @ self.last_spikes.astype(np.float32)
        noise = self._rng.normal(0, 0.05, size=self.n).astype(np.float32)
        self.v = self.v + leak + recurrent + drive + noise

        threshold = np.where(self.motor_mask, _V_THRESH * motor_threshold_scale, _V_THRESH)
        spikes = self.v >= threshold
        self.v = np.where(spikes, _V_RESET, self.v)
        self.last_spikes = spikes
        return spikes

    def region_activity(self, spikes: np.ndarray) -> dict:
        """Fraction of each region's neurons that spiked this tick."""
        return {r: float(spikes[mask].mean()) if mask.any() else 0.0
                for r, mask in self._region_masks.items()}
