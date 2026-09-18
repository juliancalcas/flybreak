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

# The real analog of "another fly is visibly nearby" (see server.py's
# World/_SOCIAL_RANGE -- Part 2 of "multiple flies", README.md's "Where
# this is headed"). FlyWire's own `super_class` values `optic` (the
# compound eye's own optic-lobe circuitry, 77,873 neurons) and
# `visual_projection` (neurons carrying visual information onward into the
# central brain, 7,684 neurons) are the real visual pathway -- already
# discovered and reported per-tick via `regions`/`region_masks` (see
# `active_regions` in server.py's payload), now also driven by another
# fly's proximity (see server.py's World/_SOCIAL_BOOST). Unlike
# FOOD_CLASS/FOOD_SUB_CLASS above (a real contact-taste channel modeled
# as a distance gradient, flagged as a simplification), vision is a
# genuine distance sense in the real fly -- no such caveat applies here.
VISUAL_SUPER_CLASSES = frozenset({"optic", "visual_projection"})

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
    `websockets.serve()`. Also forces `get_sample_graph()`'s one-time BFS
    sample-build now, for the same reason -- measured well under 0.2s for
    the real ~1,000-node sample (see README.md's "The live synapse
    sample"), cheap next to the ~10-15s connectome load itself, but still
    synchronous work that should happen before, not during, the first
    client's connection.
    """
    _load_connectome()
    get_sample_graph()


@dataclass(frozen=True)
class _Connectome:
    n: int
    region_of: np.ndarray          # (n,) str, FlyWire's super_class per neuron
    regions: list                  # sorted distinct values of region_of
    region_masks: dict             # region -> (n,) bool mask, precomputed once
    motor_mask: np.ndarray         # (n,) bool, region_of in MOTOR_SUPER_CLASSES
    food_mask: np.ndarray          # (n,) bool, class==FOOD_CLASS & sub_class==FOOD_SUB_CLASS
    visual_mask: np.ndarray        # (n,) bool, region_of in VISUAL_SUPER_CLASSES
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
    visual_mask = np.isin(region_of, list(VISUAL_SUPER_CLASSES))

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
                        food_mask=food_mask, visual_mask=visual_mask, w=w)


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
        self.visual_mask = connectome.visual_mask
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


# --- Live "synapse map" sample subgraph -------------------------------
#
# 139,255 neurons / ~2.7M synapses cannot be rendered as a literal
# node-link graph in a browser at 20 Hz -- it would be both
# computationally and visually meaningless (a solid black mass of
# edges). get_sample_graph() instead builds one small, REAL, connected
# induced subgraph via snowball/BFS sampling along the actual edges in
# `w`: every node here is a real neuron (a real row/column index into
# the real connectome), every edge is a real `w[post, pre]` entry --
# nothing here is invented, in the same spirit as README.md's "Where
# this is headed" correction of the earlier fabricated
# OLF_ORN_FOOD/OLF_ORN_DANGER names.
#
# Seeds are the first _SAMPLE_SEED_PER_POPULATION real neuron indices
# (ascending by real connectome index, i.e. `np.where(mask)[0]` is
# already sorted -- no RNG anywhere in this function) from each of
# motor_mask/food_mask/visual_mask -- the same three real populations
# server.py already reads every tick for motor_state/food_activity/the
# conspecific-proximity boost, so the sample is centered on neurons this
# project's own behavior already depends on, not an arbitrary corner of
# the data.
#
# From those seeds, BFS expands along REAL structural neighbors in both
# directions -- `w`'s row (this neuron as postsynaptic, i.e. who feeds
# it) and `w`'s column (this neuron as presynaptic, i.e. who it feeds)
# -- until the sample reaches _SAMPLE_TARGET_NODES nodes (hard-capped at
# _SAMPLE_MAX_NODES). Every step is fully deterministic (sorted seeds,
# sorted neighbor traversal order, no `np.random` anywhere here), so
# within one process's lifetime get_sample_graph() always returns the
# identical sample -- see tests/test_engine.py's
# test_sample_graph_is_deterministic_within_a_process. It is only
# guaranteed identical *within* one running process (the same guarantee
# `_load_connectome`'s cache already relies on for `w` itself), not
# across restarts -- which is all server.py needs: one shared World, one
# `w`, so one sample reused for every fly and every client connection
# for that process's lifetime.
#
# Was 9500/10000 (a real measured 10,000 nodes / 253,595 edges / 16.2 MB
# of JSON -- see README.md's original "The live synapse sample" numbers).
# Brought back down to ~950/1000 after actually looking at the rendered
# result: at 10,000 nodes, with the frontend's additive-blending glow on
# every node/edge, the sample reads as a single undifferentiated flare,
# not a legible network -- too dense to tell individual neurons or edges
# apart. 1,000 is small enough to actually look like a network of
# distinguishable points and lines on screen while still being a real,
# connected, BFS-sampled piece of the real connectome (see the module
# comment above), not a fabricated stand-in -- same principle, smaller
# size. See README.md's "The live synapse sample" for the real re-measured
# node/edge/payload-size numbers at this smaller scale.
_SAMPLE_SEED_PER_POPULATION = 20
_SAMPLE_TARGET_NODES = 950
_SAMPLE_MAX_NODES = 1000


@lru_cache(maxsize=1)
def get_sample_graph() -> dict:
    """Real snowball-sampled sub-network of the real connectome -- see the
    module comment above for how and why.

    Returns {"nodes": [...], "edges": [...], "sample_real_index": ndarray}.
    "nodes"/"edges" are exactly what server.py sends to a client as the
    one-time "synapse_graph" message (see _handle_client): each node is
    {"id": <0..N-1 local index>, "region": <real super_class string>,
    "is_food"/"is_motor"/"is_visual": <bool, straight from the real
    net.food_mask/motor_mask/visual_mask>}; each edge is {"source",
    "target": <local ids>, "weight": <real signed w entry, same units as
    network.py's `w`>}. "sample_real_index" (local sample id -> real
    connectome neuron index, kept server-side only, never sent to a
    client) is what server.py uses each tick to look up which of this
    sample's neurons actually spiked -- see Simulation.step()'s
    "sample_spikes".
    """
    c = _load_connectome()
    # Column access for "who does this neuron feed" (successors) -- `w`
    # itself is CSR (row = post, see _load_connectome's comment), which
    # only gives fast row (predecessor) access on its own.
    w_csc = c.w.tocsc()

    def seeds_from(mask: np.ndarray) -> list:
        return np.where(mask)[0][:_SAMPLE_SEED_PER_POPULATION].tolist()

    ordered_seeds = (seeds_from(c.motor_mask) + seeds_from(c.food_mask)
                      + seeds_from(c.visual_mask))
    sample: list = []
    sample_set: set = set()
    for s in ordered_seeds:
        if s not in sample_set:
            sample_set.add(s)
            sample.append(s)

    frontier = list(sample)
    while frontier and len(sample) < _SAMPLE_TARGET_NODES:
        next_frontier = []
        for node in frontier:
            preds = c.w.indices[c.w.indptr[node]:c.w.indptr[node + 1]]        # who feeds `node`
            succs = w_csc.indices[w_csc.indptr[node]:w_csc.indptr[node + 1]]  # who `node` feeds
            for nb in sorted(set(preds.tolist()) | set(succs.tolist())):
                if nb not in sample_set:
                    sample_set.add(nb)
                    sample.append(nb)
                    next_frontier.append(nb)
                    if len(sample) >= _SAMPLE_MAX_NODES:
                        break
            if len(sample) >= _SAMPLE_MAX_NODES:
                break
        frontier = next_frontier

    # Sorted ascending by real connectome index -- local id `i` is simply
    # this array's position, a deterministic, arbitrary-but-fixed
    # relabeling of the sampled real indices, independent of the BFS
    # discovery order above.
    sample_real_index = np.array(sorted(sample), dtype=np.int64)

    nodes = [
        {
            "id": i,
            "region": str(c.region_of[real_i]),
            "is_food": bool(c.food_mask[real_i]),
            "is_motor": bool(c.motor_mask[real_i]),
            "is_visual": bool(c.visual_mask[real_i]),
        }
        for i, real_i in enumerate(sample_real_index.tolist())
    ]

    # The real (source, target, weight) edges among exactly this node
    # set, sliced straight out of the already-computed `w` -- a
    # (<=1000 x <=1000) sub-slice, tiny next to the full (139255 x
    # 139255) `w`, not recomputed from the raw CSVs. `sub[a, b] =
    # w[sample_real_index[a], sample_real_index[b]] =
    # w[post=a, pre=b]` (see `w`'s own row/col convention), so an edge
    # runs from local id b (pre) to local id a (post).
    sub = c.w[sample_real_index, :][:, sample_real_index].tocoo()
    edges = [
        {"source": int(pre_local), "target": int(post_local), "weight": float(weight)}
        for post_local, pre_local, weight in zip(sub.row.tolist(), sub.col.tolist(), sub.data.tolist())
    ]

    return {"nodes": nodes, "edges": edges, "sample_real_index": sample_real_index}
