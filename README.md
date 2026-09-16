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
  `stimulus`, `activity`, `motor_state`, `meta`). Currently `"1.1"` --
  bumped from `"1.0"` when `stimulus.bac_level` was renamed to
  `stimulus.mood_level` (see "The mood_level stimulus").
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

- **A world**: some spatial representation the fly can approach a food/
  water source *toward*, not just an abstract stimulus value.
- **Real sensory input, not a flat noise `drive`**: `network.py`'s current
  `drive` array is uniform random noise across all 139,255 neurons.
  FlyWire's own classification already names the real channels this
  should route through instead -- `OLF_ORN_FOOD` (food odor, 1851
  neurons), gustatory (taste on contact, 408 neurons), and the visual
  pathway already modeled (`optic`, `visual_projection`) -- so driving
  *those specific populations* when the fly is near food, rather than
  exciting everything uniformly, is the real next step and is buildable
  directly on today's `region_of`/`_region_masks` machinery.
- **Multiple flies**: more than one `Simulation` (or one shared world
  serving several), with some way for them to sense each other.
- **No fear stimulus, ever**: `OLF_ORN_DANGER` (430 neurons) exists in the
  real data and could be driven the way `solomonsealed/flybrain`'s own
  walled-garden simulation drives it with spiderwebs -- deliberately never
  wired to anything, by design, not by omission.

`solomonsealed/flybrain` (already vendored here as the connectome data
source) has already built almost exactly this world -- a walled orchard
with fruit trees, food/odor/taste/touch senses, and up to 48 flies
breeding -- minus the fear stimulus (it has spiderwebs) and running in a
browser Web Worker, not this project's Python engine. The smallest real
step toward "the goal" is not a full world simulation from scratch: it is
wiring `OLF_ORN_FOOD` to a single, static, always-present food source (no
spatial navigation yet, just "food is present" as a real sensory drive
instead of uniform noise) and watching whether `mood_level`'s effect and
genuine food-seeking activity are distinguishable in the real data -- one
new sensory population, not a world engine.

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
