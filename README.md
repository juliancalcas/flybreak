# FlyBreak

Simulates the real FlyWire connectome of *Drosophila* -- 139,255 neurons,
the actual FAFB v783 wiring diagram, not a synthetic stand-in -- under an
artificial contentment stimulus (`mood_level`, "feliz" to "plena"),
visualized in 3D: a fly over a wireframe city, with a live HUD of the
simulation's metrics.

The eventual goal is an embodied fly in a calm environment with food and
water sources and other flies of its own species to encounter -- never a
fear/threat stimulus. Not built yet: today's engine is a single
disembodied fly with one scalar stimulus and no world to move through.
See "Where this is headed" below for what that actually requires and the
smallest first step toward it.

## Quick start

```bash
pip install -r requirements.txt
python __main__.py                # fetches the connectome on first run (~50 MB),
                                   # starts engine + frontend, opens your browser
```

On Windows, double-clicking `run.bat` does the same thing without
opening a terminal at all. Either way this is one process launching both
servers in background threads and opening `http://localhost:8080` for you
-- Ctrl+C in that terminal stops both. See "Architecture" below for the two
servers separately (useful when iterating on just one side), and
`.claude/skills/recover-stuck-git-pull/` if a `git pull` before any of this
gets stuck on Windows with a "Deletion of directory ... failed" loop.

## Setup: two machines, one private remote

This project lives on two machines, coordinated by one single private
GitHub remote (`https://github.com/juliancalcas/flybreak`) -- split out
from `forja-de-libreas` (the DCS livery generator this repo used to share
space with) into its own repo, its own history, its own sync, because it
is a genuinely unrelated project. `git pull` at the start of a session,
`git push` at the end. `HANDOFF.md` (create it when there's something the
other machine needs to know before continuing) is read automatically on
the next `pull` if `githooks/` is enabled once per machine:
`git config core.hooksPath githooks`.

## Architecture: three decoupled layers

```
engine/    Capa 1 -- Python: the real connectome as a sparse LIF network + WebSocket server
contract/  Capa 2 -- the JSON schema both sides speak, + a mock stream
frontend/  Capa 3 -- a self-contained HTML page (Three.js + HUD)
```

Decoupled so the simulation and the visualization can be iterated on
independently: the frontend can run against `contract/mock_server.py`
without the real engine, and the engine's contract is enforced by
`contract/validator.py` on every tick it sends.

### `engine/` -- the simulation

- `network.py` -- `LIFNetwork`: the real FlyWire FAFB v783 connectome
  (Dorkenwald et al., *Nature* 634, 2024), 139,255 neurons and ~2.7M
  synaptic connections, run as a sparse leaky-integrate-and-fire
  population. Region-tagged by FlyWire's own `super_class` annotation
  (`optic`, `central`, `sensory`, `visual_projection`, `ascending`,
  `descending`, `sensory_ascending`, `visual_centrifugal`, `motor`,
  `endocrine` -- discovered at runtime from the data, not a fixed list).
  Requires `python -m engine.fetch_connectome` to have been run
  once on this machine first (see "The real connectome" below) -- the
  ~50 MB data is never in git.
- `mood.py` -- `MoodController`: the hybrid `mood_level` (see "The
  mood_level stimulus" below).
- `server.py` -- ticks the network at 20 Hz, applies `mood_level` as a
  *lowered* firing threshold on the motor+descending pathway (easier to
  spike, not harder -- the opposite direction of the project's earlier
  "ethanol intoxication" stimulus this replaced; a deliberate design
  choice, not a documented biological effect, though the direction
  mirrors the real dopaminergic reward pathway's general role, which this
  model has no separate route for), derives `motor_state` from the
  resulting activity, validates every tick against
  `contract/schema_v1.json` before sending it, and listens for control
  messages on the same socket.

Run it standalone (`python __main__.py`, see "Quick start" above, does
this and the frontend together in one command -- use this form instead
when iterating on the engine alone, e.g. against a different frontend or
a raw WebSocket client):

```bash
python -m engine.fetch_connectome  # once per machine, ~50 MB, ~10-15s to load after
python -m engine.server            # ws://127.0.0.1:8765, one Simulation per client
```

### `contract/` -- the data contract

- `schema_v1.json` -- JSON Schema for one tick, matching the shape agreed
  in the project brief (`schema_version`, `tick`, `sim_time_ms`,
  `stimulus`, `activity`, `motor_state`, `meta`). Currently `"1.3"` --
  bumped from `"1.2"` when `motor_state.position` (`{"x", "z"}`) was
  added, giving the engine authoritative fly position for the first time
  (see "Where this is headed"); `"1.2"` was bumped from `"1.1"` when
  `activity.food_activity` was added, which itself was bumped from
  `"1.0"` when `stimulus.bac_level` was renamed to `stimulus.mood_level`
  (see "The mood_level stimulus").
- `validator.py` -- `validate_tick()` / `is_valid_tick()`, used by both the
  real engine and the mock stream so neither can silently drift from the
  schema.
- `mock_server.py` -- serves synthetic but schema-valid ticks on the same
  port/message shape as the real engine, for frontend-only iteration
  (its region names are hardcoded to match the real engine's actual
  `super_class` categories, since the mock has no connectome data to
  discover them from):

```bash
python -m contract.mock_server  # ws://127.0.0.1:8765, no LIF sim involved
```

`motor_state.action` is `idle | walking | grooming | flying | frozen`,
`wing_state` is `folded | raised | buzzing` -- an initial enum, not fixed;
extend `schema_v1.json` and the engine/frontend together if a new state is
needed.

### `frontend/` -- the visualization

`index.html` -- one self-contained page (Three.js from a CDN, everything
else inline), no build step. Connects to
`ws://<host>:8765` (override with
`window.FLYBREAK_WS_URL` before the script runs, or open it through any
static server), renders a wireframe city + the fly, and a HUD (tick,
sim_time, spike count, firing rate, per-region activity bars, action, wing
state, and the `mood_level` slider -- see below). Open it directly in a
browser, or serve it:

```bash
python -m http.server 8080 --directory frontend
```

## The real connectome

`network.py` loads real FlyWire FAFB v783 data (139,255 neurons, ~2.7M
unique synaptic connections after collapsing `connections.csv`'s ~3.9M
per-synapse-annotation rows onto (pre, post) pairs), sourced from
`solomonsealed/flybrain` (MIT license) -- one of the two reference repos
the original project brief named, whose `data/` folder packages the raw
FlyWire Codex export as plain CSVs. `fetch_connectome.py` downloads just
the two files needed (`connections.csv.gz`, `classification.csv.gz`, ~50
MB combined) from that repo directly; never vendored in git, see
`.gitignore`.

Synapse sign: GABA and glutamate are treated as inhibitory (GABA is the
fly CNS's primary fast inhibitory transmitter; glutamate acts through
inhibitory glutamate-gated chloride channels in insects, unlike its
excitatory role in vertebrates -- both well-established, not invented for
this project). Acetylcholine plus the three neuromodulators present in the
data (dopamine, serotonin, octopamine) are treated as excitatory, a real
simplification for those three: at LIF timescales they modulate rather
than directly drive spiking, and this model has no separate neuromodulatory
pathway to route them through. Synapse-count weight is capped (median 6,
mean 8.8, max 2405 in the real data) so a handful of outlier connections
cannot dominate a tick.

Load takes ~10-15s (pure `csv`+`gzip`+`numpy`, no pandas dependency) and
happens once per process, cached (`network.warm_cache()`, called by
`server.main()` before it starts accepting connections -- not lazily on
first client, which would otherwise block the asyncio event loop for the
full load on every process's first connection). Measured at ~5-13ms/tick
on the full network, comfortably inside a 20 Hz tick's 50ms budget.

The motor+descending pathway (`net.motor_mask`, FlyWire's own `super_class`
values `motor` and `descending` -- 110 + 1305 = 1415 neurons) is what
`server.py` actually reads for `motor_state`, not the single `motor`
category `active_regions` reports on its own. Its per-tick activity
ranges roughly 0.31–0.58 across the full `mood_level` sweep, with
tick-to-tick noise (std ~0.03) large enough relative to that span that
`server.py` smooths it with an EMA before deriving `action`/`speed` --
see its own comments for the measured numbers.

`net.food_mask` (FlyWire's finer-grained `class`/`sub_class` columns this
time, not `super_class`: `class == "gustatory"`, `sub_class ==
"sugar/water"` -- 129 neurons, a real, identity-labeled appetitive taste
channel) gets a distance-scaled drive boost in `server.py`'s `step()` --
strongest at/near `FOOD_POSITION`, falling off with the fly's own
distance from it -- on top of the baseline noise every neuron still
gets, reported as `activity.food_activity` -- see "Where this is headed"
below for why this population, and `server.py`'s `_FOOD_RANGE` comment
for the measured near-vs-far numbers and the falloff shape.

## The mood_level stimulus

`stimulus.mood_level` replaced an earlier "ethanol intoxication"
(`bac_level`) stimulus -- same underlying mechanism (`MoodController` is
`BacController` unchanged except for naming), opposite direction and
meaning. One continuous value, hybrid control: it ramps up automatically
in the engine (`MoodController`, ~2 minutes of sim time from 0 to 1 by
default) so the fly grows progressively happier on its own, but the HUD
slider can pin it to any value at any moment
(`{"type": "set_mood_level", "value": 0.72}` over the same WebSocket).
Releasing the override (`{"type": "set_mood_mode", "value": "auto"}`, the
HUD's "mode" button) resumes the automatic ramp from exactly where the
override left it -- never a visible jump. `mood_level` therefore lives in
the engine (it is the one source of truth sent downstream every tick), and
the frontend only ever *requests* a value, it never computes one itself.

The slider is presented as one continuous range, "feliz" (0.0) to "plena"
(1.0), with "muy feliz" labeled at its midpoint -- not three discrete
presets. There is no "sad" or negative end: the scale starts at "feliz",
not neutral.

## Where this is headed

The actual goal (not built yet): an embodied fly in a simple, calm
environment with food and water sources, encountering other flies of its
own species, that never experiences a fear/threat stimulus. That is a
different kind of system from what exists today -- a disembodied fly with
one scalar stimulus and no world to move through -- and it means at least:

- **A world** (first step now done): some spatial representation the fly
  can approach a food/water source *toward*, not just an abstract
  stimulus value. `Simulation` (`server.py`) now owns real, authoritative
  `(self._x, self._z)` position -- previously position existed only
  client-side, as `frontend/index.html`'s own dead-reckoning integration
  of `heading_deg`/`speed`; the engine itself had zero concept of where
  the fly was. Position advances every tick the same way that client-side
  code always did (`x += sin(heading) * speed * STEP`), reported as
  `motor_state.position` (`{"x", "z"}`, schema `"1.3"`). The one food
  source is now a real fixed point in that same space (`FOOD_POSITION`,
  `(6, 5)`, matching the frontend's existing prop), `food_activity`'s
  boost is now a distance-scaled gradient off that point instead of a
  flat always-on value (near food ~0.63-0.67, far from it ~0.22-0.24 --
  see `server.py`'s `_FOOD_RANGE` comment), and the heading update has a
  chemotaxis-like bias that nudges toward the food's true bearing,
  strength scaling with how much food-drive is actually present, so the
  fly reliably finds and orbits the food from spawn without the wander
  ever being fully overridden (see `_FOOD_STEER_GAIN`'s comment and
  `tests/test_engine.py`'s empirical trajectory tests). This is still a
  small, deliberately narrow slice of "a world," not the real thing: one
  fixed food object, no water source or any other food type, no
  obstacles or collision, no boundaries (the fly can wander arbitrarily
  far if it hasn't picked up the food's scent yet), and still only one
  fly with nothing else in the space to sense.
- **Real sensory input, not a flat noise `drive`** (first step now done):
  `network.py`'s `drive` array used to be uniform random noise across all
  139,255 neurons, with nothing food- or danger-specific in it. An
  earlier work cycle believed FlyWire's classification named a food-odor
  channel (`OLF_ORN_FOOD`, 1851 neurons) and a danger-odor channel
  (`OLF_ORN_DANGER`, 430 neurons) to route drive through -- **verified
  against the real `classification.csv.gz` and this was wrong**: those
  names do not exist anywhere in the data. `class == "olfactory"` does
  split into two sub-populations of exactly those sizes, but neither is
  labeled by odor identity -- their real `sub_class` is blank (1851
  neurons) or `"pheromone"` (430 neurons), never `"food"` or `"danger"`.
  What *is* genuinely identity-labeled, under `class == "gustatory"`
  (taste on contact, 408 neurons total), is a real appetitive channel
  (`sub_class == "sugar/water"`, 129 neurons) and a real aversive one
  (`sub_class == "bitter"`, 65 neurons). `net.food_mask` (the gustatory
  sugar/water population) now gets a distance-scaled drive boost in
  `server.py`'s `step()`, off the fly's own position relative to
  `FOOD_POSITION` (see "A world" above) -- "food is present, and more so
  the closer the fly gets," the smallest real step described below, now
  taken a step further than the original static version. Flagged
  honestly where it's implemented: real gustatory (taste) sensing is
  contact-based in the actual fly, not a distance gradient -- that's
  really an olfactory mechanism this codebase has no separate channel
  for, so ramping taste up with proximity is a modeling simplification,
  same kind as the ACH/DA/SER/OCT-as-excitatory one already made in
  `network.py`. The visual pathway (`optic`, `visual_projection`) is
  already modeled but not yet driven by anything food-specific.
- **Multiple flies**: more than one `Simulation` (or one shared world
  serving several), with some way for them to sense each other.
- **No fear stimulus, ever**: `class == "gustatory"`, `sub_class ==
  "bitter"` (65 neurons) is the real aversive analog of the fabricated
  `OLF_ORN_DANGER` above -- it exists in the real data and could be
  driven the way `solomonsealed/flybrain`'s own walled-garden simulation
  drives its spiderweb-fear stimulus. Deliberately never masked, read, or
  wired to anything anywhere in this codebase, by design, not by
  omission -- same commitment as always, now pointed at the real label
  instead of an invented one.

`solomonsealed/flybrain` (already vendored here as the connectome data
source) has already built almost exactly this world -- a walled orchard
with fruit trees, food/odor/taste/touch senses, and up to 48 flies
breeding -- minus the fear stimulus (it has spiderwebs) and running in a
browser Web Worker, not this project's Python engine. The smallest real
step toward "the goal" was not a full world simulation from scratch: it
was wiring a single, static, always-present food source (no spatial
navigation yet, just "food is present" as a real sensory drive instead of
uniform noise) to `net.food_mask` and watching whether `mood_level`'s
effect and genuine food-seeking activity are distinguishable in the real
data -- one new sensory population, not a world engine. That step is done;
mood_level's own effect on `food_activity` is small (~0.03 across the full
sweep, see `server.py`'s `_FOOD_BOOST` comment) next to the boost's own
effect, so the two are in fact distinguishable in the real data.

The step after that -- giving the engine real position and making the one
food source something the fly actually moves toward, instead of a fixed
prop the frontend happened to draw at a coincidental spot -- is also now
done (see "A world" above): `Simulation` tracks `(x, z)`, `food_activity`
is a real function of distance to `FOOD_POSITION` instead of a flat value,
and the heading update has a chemotaxis-like bias toward it. Verified
empirically (`tests/test_engine.py`), not just algebraically: across
several seeds the fly reliably finds and then orbits close to the food
from its `(0, 0)` spawn. Still open, and still a large gap from "the
goal": a water source and any food type beyond the one gustatory channel,
multiple flies able to sense each other, obstacles/collision, and any
actual boundary to the world at all (right now the fly can wander
unboundedly far before it ever picks up the food's scent) -- the fear
channel stays unwired regardless.

## Attribution

`frontend/index.html` does not currently vendor `fly-connectome-template`
(Mert Cobanov) -- see `HANDOFF.md` if still open, or the git log for how
that was resolved. If/when its MaleCNS atlas or Flybody mesh is pulled in,
its source-available license requires crediting the template both in this
project's UI and in this README; re-check the exact license text before
that happens, and before anything from this project is ever made public.

The connectome data itself (`engine/data/`, fetched not vendored) is MIT
licensed via `solomonsealed/flybrain`, built on the FlyWire Consortium's
public dataset:

> Dorkenwald, S., Matsliah, A., Sterling, A.R. *et al.* Neuronal wiring
> diagram of an adult brain. *Nature* **634**, 124–138 (2024).
> https://doi.org/10.1038/s41586-024-07558-y
