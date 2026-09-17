# FlyBreak

Simulates the real FlyWire connectome of *Drosophila* -- 139,255 neurons,
the actual FAFB v783 wiring diagram, not a synthetic stand-in -- under an
artificial contentment stimulus (`mood_level`, "feliz" to "plena"),
visualized in 3D: a fly over a wireframe city, with a live HUD of the
simulation's metrics.

The eventual goal is an embodied fly in a calm environment with food and
water sources and other flies of its own species to encounter -- never a
fear/threat stimulus. Most of the pieces now exist: a real, authoritative
position, a food landmark and a water landmark the fly actually moves
toward, and a shared world of 3 flies (one controlled, two autonomous)
that can sense each other and end up nearby. See "Where this is headed"
below for exactly what's built, what's still simplified, and what's
genuinely not built yet.

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
- `server.py` -- `Simulation` ticks one fly's network at 20 Hz, applies
  `mood_level` as a *lowered* firing threshold on the motor+descending
  pathway (easier to spike, not harder -- the opposite direction of the
  project's earlier "ethanol intoxication" stimulus this replaced; a
  deliberate design choice, not a documented biological effect, though
  the direction mirrors the real dopaminergic reward pathway's general
  role, which this model has no separate route for), derives
  `motor_state` from the resulting activity, and validates every tick
  against `contract/schema_v1.json` before sending it. `World` (see
  "Where this is headed" -- "Multiple flies") owns a fixed `N_FLIES = 3`
  of these `Simulation`s as ONE shared space, ticked together by a single
  background task regardless of client count; a connecting browser
  controls `world.flies[0]` (mood_level set/override, same control
  messages as before, over the same socket) and streams back fly 0's own
  tick plus `world.other_flies` (the other two flies' minimal renderable
  state) each tick interval.

Run it standalone (`python __main__.py`, see "Quick start" above, does
this and the frontend together in one command -- use this form instead
when iterating on the engine alone, e.g. against a different frontend or
a raw WebSocket client):

```bash
python -m engine.fetch_connectome  # once per machine, ~50 MB, ~10-15s to load after
python -m engine.server            # ws://127.0.0.1:8765, one shared World (3 flies), any number of viewers
```

### `contract/` -- the data contract

- `schema_v1.json` -- JSON Schema for one tick, matching the shape agreed
  in the project brief (`schema_version`, `tick`, `sim_time_ms`,
  `stimulus`, `activity`, `motor_state`, `meta`, `world`). Currently
  `"1.5"` -- bumped from `"1.4"` when `activity.sample_spikes` (array of
  local sample-graph ids that spiked this tick) was added, alongside the
  new, separate `schema_graph_v1.json` for the one-time `synapse_graph`
  message (see "The live synapse sample" below). `"1.4"` was bumped from
  `"1.3"` when top-level `world.other_flies`
  (array of `{id, position, heading_deg, action, wing_state}`) was added,
  giving a viewer visibility into the other flies sharing its fly's world
  (see "Where this is headed" -- "Multiple flies"); the existing
  `stimulus`/`activity`/`motor_state`/`meta` fields keep describing "your"
  fly exactly as before, unchanged. `"1.3"` was bumped from `"1.2"` when
  `motor_state.position` (`{"x", "z"}`) was added, giving the engine
  authoritative fly position for the first time (see "Where this is
  headed"); `"1.2"` was bumped from `"1.1"` when `activity.food_activity`
  was added, which itself was bumped from `"1.0"` when
  `stimulus.bac_level` was renamed to `stimulus.mood_level` (see "The
  mood_level stimulus").
- `validator.py` -- `validate_tick()` / `is_valid_tick()` for a tick, and
  `validate_graph()` / `is_valid_graph()` for the one-time `synapse_graph`
  message, used by both the real engine and the mock stream so neither can
  silently drift from either schema.
- `mock_server.py` -- serves synthetic but schema-valid ticks (plus one
  synthetic `synapse_graph` message per connection) on the same port/
  message shape as the real engine, for frontend-only iteration
  (its region names are hardcoded to match the real engine's actual
  `super_class` categories, and its `synapse_graph` is a small fixed
  ring-plus-chords graph, clearly not real connectivity -- see its own
  comments -- since the mock has no connectome data to discover or sample
  from):

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
strongest at/near whichever of `FOOD_POSITION`/`WATER_POSITION` the fly
is currently closer to, falling off with the fly's own distance from it
-- on top of the baseline noise every neuron still gets, reported as
`activity.food_activity` -- see "Where this is headed" below for why
this is one population driven by two landmarks, not two populations,
and `server.py`'s `_FOOD_RANGE` comment for the measured near-vs-far
numbers and the falloff shape.

`net.visual_mask` (FlyWire's `super_class` again: `optic` +
`visual_projection`, 85,557 of 139,255 neurons -- vision is most of the
real fly's brain, and is here too) gets a smaller, distance-scaled boost
when another fly is nearby (`World`'s shared multi-fly space -- see
"Where this is headed" -- "Multiple flies"), reusing the same falloff
shape at `server.py`'s own `_SOCIAL_RANGE`/`_SOCIAL_BOOST`. Unlike the
food/water gradient, this needs no contact-vs-distance caveat: vision is
a genuine distance sense in the real fly.

## The live synapse sample

139,255 neurons and ~2.7M synapses cannot be rendered as a literal
node-link graph in a browser at 20 Hz -- both computationally (a fresh
139,255-node force layout every tick is nowhere near real-time) and
visually (that many edges drawn at once is an undifferentiated black
mass, not a picture anyone could read). So the frontend instead gets a
real, small, connected piece of the real connectome, not the whole thing
and not a fabricated stand-in.

`network.get_sample_graph()` builds it once per process (`@lru_cache`,
same pattern as `_load_connectome`), via real snowball/BFS sampling: seed
neurons are the first 20 real neuron indices (sorted ascending, no RNG)
from each of `net.motor_mask`/`net.food_mask`/`net.visual_mask` -- the
same three real populations `server.py` already reads every tick -- then
BFS expands outward along the real edges in `w`, in both directions
(who feeds a node, and who a node feeds), until the sample reaches a
target size, hard-capped at 250 nodes. Measured on the real connectome as
it stands today: **250 nodes, 986 directed edges** among them (the BFS
frontier reached the 250-node cap before naturally running out of real
neighbors to add). Every node is a real neuron with a real `super_class`
region and the real `is_food`/`is_motor`/`is_visual` flags straight from
those same masks; every edge's `weight` is a real, unmodified entry read
directly out of the already-computed `w` matrix (`w[post, pre]`, same
units/sign convention as the rest of this codebase -- see "The real
connectome" above), not recomputed from the raw CSVs and not invented.

Sent to a connecting client exactly once, right when it connects (before
the per-tick loop starts), as `{"type": "synapse_graph", "nodes": [...],
"edges": [...]}` -- structurally tagged with `"type"` so the frontend can
tell it apart from a regular tick (which has no `"type"` field). Since
every fly in the shared `World` reads the same cached connectome/`w` (see
`LIFNetwork.__init__`/`_load_connectome` -- only each fly's own membrane
state differs), this one sample is identical for every fly and every
client for the lifetime of the process; it is computed once and reused,
never rebuilt per connection or per tick. Validated against its own
schema, `contract/schema_graph_v1.json`, via `validate_graph()`/
`is_valid_graph()` -- the same pattern as `validate_tick()`/
`is_valid_tick()`, kept as a separate schema because a graph message and
a tick message are structurally different things sent at different
cadences, not two shapes of the same thing.

Every tick after that, fly 0's payload additionally carries
`activity.sample_spikes` -- the LOCAL sample ids (matching the
`synapse_graph` message's node `id`s, so the frontend never has to
translate) that actually spiked *this specific tick*, read straight out
of the real per-tick spike vector `LIFNetwork.step()` already computes,
via a server-side-only mapping from local sample id back to real
connectome index (never sent to the client). Typically a handful to a
few dozen ids out of the ~250 sampled neurons, matching this project's
own measured firing rates elsewhere -- never the full 139,255-length
spike vector. Same "no neural introspection into a fly you don't
control" boundary the rest of this project already follows for
`activity`/`stimulus`: only fly 0 (the one a connecting browser controls)
ever has its `sample_spikes` actually forwarded to a client, via the same
`_build_client_payload` that already keeps `world.other_flies` minimal.

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

The actual goal: an embodied fly in a simple, calm environment with food
and water sources, encountering other flies of its own species, that
never experiences a fear/threat stimulus. A world, food and water
sources, real sensory input, and a shared multi-fly space are now all
built, in the narrow, specific ways described below; what each one still
leaves out, honestly, follows each.

- **A world**: `Simulation` (`server.py`) owns real, authoritative
  `(self._x, self._z)` position -- previously position existed only
  client-side, as `frontend/index.html`'s own dead-reckoning integration
  of `heading_deg`/`speed`; the engine itself had zero concept of where
  the fly was. Position advances every tick the same way that client-side
  code always did (`x += sin(heading) * speed * STEP`), reported as
  `motor_state.position` (`{"x", "z"}`, schema `"1.3"`+). Still narrow:
  no obstacles or collision (between flies or landmarks), no boundaries
  at all (a fly can wander arbitrarily far if it hasn't picked up either
  landmark's scent yet).
- **Food and water sources**: `FOOD_POSITION` (`(6, 5)`, matching the
  frontend's existing prop) and `WATER_POSITION` (`(-6, -5)`, the
  diagonally opposite quadrant, same ~7.81-unit distance from spawn) are
  both real fixed points in that same space. Both drive the SAME
  `net.food_mask` population through the SAME distance-scaled gradient
  (near either one ~0.63-0.67, far from both ~0.22-0.24 -- see
  `server.py`'s `_FOOD_RANGE` comment) -- deliberately one channel, not
  two: FlyWire's own classification has exactly one real, identity-
  labeled appetitive taste channel (`class == "gustatory"`, `sub_class ==
  "sugar/water"`, 129 neurons -- see "Real sensory input" below), not a
  separate sugar-sensing and water-sensing population, so `WATER_POSITION`
  reuses `FOOD_POSITION`'s exact mechanism instead of inventing a second,
  fake channel with no real data behind it (see `server.py`'s
  `WATER_POSITION` comment). The two proximities combine via `max()`, not
  a sum -- a real receptor population saturates on the stronger of two
  simultaneous stimuli, it doesn't get double-activated by two distant
  attractants at once. The chemotaxis steering bias retargets each tick
  to whichever landmark currently has the higher proximity, so the fly
  reliably finds and orbits whichever one it's actually closer to (see
  `tests/test_engine.py`'s empirical trajectory tests for both
  directions). Still narrow: still the one gustatory channel for both,
  no other food type, and both landmarks are static, non-depleting
  points -- nothing is "eaten."
- **Real sensory input, not a flat noise `drive`**: `network.py`'s
  `drive` array used to be uniform random noise across all 139,255
  neurons, with nothing food-, water-, or danger-specific in it. An
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
  sugar/water population) gets a distance-scaled drive boost in
  `server.py`'s `step()`, off the fly's own position relative to
  whichever landmark is closer -- "an attractant is present, and more so
  the closer the fly gets to it" -- flagged honestly where it's
  implemented: real gustatory (taste) sensing is contact-based in the
  actual fly, not a distance gradient -- that's really an olfactory
  mechanism this codebase has no separate channel for, so ramping taste
  up with proximity is a modeling simplification, same kind as the
  ACH/DA/SER/OCT-as-excitatory one already made in `network.py`. The
  visual pathway (`optic` + `visual_projection`, `net.visual_mask`, see
  "Multiple flies" below) is now driven too, by conspecific proximity --
  and needs no such caveat, since vision genuinely is a distance sense in
  the real fly, unlike taste.
- **Multiple flies**: `World` (`server.py`) holds a fixed `N_FLIES = 3`
  `Simulation`s as one shared, continuously-ticked space -- previously
  each connecting browser got its own private `Simulation`, so two tabs'
  flies could never be near each other (no shared coordinate space at
  all). A background `asyncio` task ticks all 3 together at the existing
  20 Hz regardless of client count, so the two autonomous flies (indices
  1-2, whose `MoodController`s just auto-ramp forever, nothing ever calls
  `set_manual` on them) keep living with nobody watching; a connecting
  browser controls `world.flies[0]` exactly as the single-fly version
  always worked, and additionally sees `world.other_flies` (schema
  `"1.4"`) -- the other two flies' `id`/`position`/`heading_deg`/`action`/
  `wing_state`, enough to render them moving realistically, deliberately
  not their full `activity`/`stimulus` (a viewer has no neural
  introspection into flies it doesn't control). Each tick is two-pass:
  every fly's position is snapshotted at the END of the previous tick
  before any fly moves, so "how close is my nearest peer" reads a single
  consistent world-state for all 3 flies, not one that's already half-
  updated. When another fly is within `_SOCIAL_RANGE` (real numbers in
  `server.py`'s comment), `net.visual_mask` gets a small, distance-scaled
  boost, same gradient shape as food/water. Deliberately, there is NO
  fly-to-fly steering bias: since all 3 flies are independently drawn
  toward the same two fixed landmarks, correlated resource-seeking
  brings them into proximity over time on its own, without an engineered
  "flocking" force that wasn't asked for and would be harder to justify
  as grounded in anything real. Verified empirically, not assumed: across
  5 seed sets, 2500 ticks each, at least one pair of the 3 flies always
  ends up within ~0.004-0.4 units of each other well before tick 500 and
  stays that close for the rest of the run -- sometimes all 3 converge on
  the same landmark (seeds `[0,1,2]`: peak separation ever only ~2.2
  units, all three end up together), sometimes two do while the third
  settles at the other landmark and ends up genuinely far off instead
  (seeds `[10,11,12]`: one pair stays within ~0.004 units of each other
  while the third fly ends up ~16 units away) -- either way, real
  proximity between at least two flies happens in every seed tested,
  purely from shared landmark-seeking (see
  `tests/test_engine.py`'s
  `test_flies_end_up_near_each_other_without_fly_to_fly_steering`). Still
  narrow, and still not the real thing: a
  fixed count of 3 flies, not a dynamic population; no breeding or
  reproduction; no collision between flies; and no actual "meeting"
  behavior beyond proximity and the neutral visual-sensing boost above --
  no grooming, courtship, or any other real conspecific interaction
  `solomonsealed/flybrain`'s own richer simulation models.
- **No fear stimulus, ever**: `class == "gustatory"`, `sub_class ==
  "bitter"` (65 neurons) is the real aversive analog of the fabricated
  `OLF_ORN_DANGER` above -- it exists in the real data and could be
  driven the way `solomonsealed/flybrain`'s own walled-garden simulation
  drives its spiderweb-fear stimulus. Deliberately never masked, read, or
  wired to anything anywhere in this codebase, by design, not by
  omission -- same commitment as always, now pointed at the real label
  instead of an invented one. The new conspecific-proximity boost above
  is, and must stay, a neutral "notices a peer" signal: nothing about
  another fly's presence ever lowers `mood_level`, adds an aversive
  drive, or affects anything negatively.

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

Since then: real position and chemotaxis toward a single food landmark,
then a second (water) landmark reusing the same one real channel, and
then a shared multi-fly world with real (if neutral, non-steering)
conspecific sensing -- see the five bullets above for what each of those
actually does and doesn't cover. Still genuinely open: no obstacles or
collision anywhere, no world boundary at all, only one real sensory
channel behind both landmarks, a fixed 3-fly population with no breeding
and no real "meeting" behavior beyond proximity -- and the fear channel
stays unwired regardless.

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
