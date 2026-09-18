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
python __main__.py                # fetches the connectome on first run (~52 MB),
                                   # starts engine + frontend, opens your browser
```

On Windows, double-clicking `run.bat` does the same thing without
opening a terminal at all. Either way this is one process launching both
servers in background threads and opening `http://localhost:8080` for you
-- Ctrl+C in that terminal stops both. `launcher.hta` is the same
one-click launch with a small styled "Launch FlyBreak" button instead of
a bare console window -- double-click it directly (it must run as its
own trusted local application via Windows' `mshta.exe`, never opened
*inside* a browser tab -- no web page, opened any way, is ever allowed
to launch a local process; that is a universal browser security rule an
`.hta` sits outside of, not a workaround of it). See "Architecture"
below for the two servers separately (useful when iterating on just one
side), and
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
  `endocrine` -- discovered at runtime from the data, not a fixed list),
  plus four further real, finer-grained named regions derived from
  FlyWire's `group` annotation (`mushroom_body`, `antennal_lobe`,
  `lateral_horn`, `ellipsoid_body` -- see "The real connectome" below).
  Requires `python -m engine.fetch_connectome` to have been run
  once on this machine first (see "The real connectome" below) -- the
  ~52 MB data is never in git.
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
python -m engine.fetch_connectome  # once per machine, ~52 MB, ~10-15s to load after
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
else inline except the real fly mesh under `assets/flybody/`, fetched
not vendored -- see "The flybody mesh" and Attribution below), no build
step. Connects to
`ws://<host>:8765` (override with
`window.FLYBREAK_WS_URL` before the script runs, or open it through any
static server), renders a wireframe city + a real, anatomically-detailed
fly mesh (region-activity-driven coloring on the controlled fly), and a
HUD (tick, sim_time, spike count, firing rate, per-region activity bars,
action, wing state, the `mood_level` slider, and a glowing volumetric
synapse-map panel -- see below). Open it directly in a browser, or serve it:

```bash
python -m http.server 8080 --directory frontend
```

## The real connectome

`network.py` loads real FlyWire FAFB v783 data (139,255 neurons, ~2.7M
unique synaptic connections after collapsing `connections.csv`'s ~3.9M
per-synapse-annotation rows onto (pre, post) pairs), sourced from
`solomonsealed/flybrain` (MIT license) -- one of the two reference repos
the original project brief named, whose `data/` folder packages the raw
FlyWire Codex export as plain CSVs. `fetch_connectome.py` downloads the
three files needed (`connections.csv.gz`, `classification.csv.gz`,
`neurons.csv.gz`, ~52 MB combined) from that repo directly; never
vendored in git, see `.gitignore`.

`neurons.csv` (verified directly: 139,255 rows, `root_id` 100%
overlapping the other two files' neuron IDs -- the same dataset, not a
second one needing reconciliation) adds two real things this project
uses: a much finer-grained `group` neuropil label (629 distinct real
values, vs. `classification.csv`'s ~10 broad `super_class` categories),
and a real PER-NEURON dominant-neurotransmitter classification `nt_type`
(ACH/GABA/GLUT/SER/DA/OCT, empty for the real ~14% of neurons FlyWire
leaves unclassified at this granularity) -- distinct from
`connections.csv`'s PER-SYNAPSE-ROW `nt_type` column. (It also carries
`nt_type_score` and per-NT confidence averages `da_avg`/`ser_avg`/
`gaba_avg`/`glut_avg`/`ach_avg`/`oct_avg`; nothing in this codebase reads
those yet.)

**Four new, real, narratively meaningful named regions**, built from
`group` and folded into the same `regions`/`region_masks` mechanism
`active_regions` already reports every tick (no schema change, no
frontend change -- `frontend/index.html`'s region-list rendering loop
iterates `activity.active_regions` generically and has no region-name-
specific code; only its separate, already-existing `driveFlyRegions`
body-part-coloring function looks up specific super_class names by
name, and it safely defaults to 0 for any name it doesn't recognize, so
these additions don't affect it either way): `mushroom_body` (any
`group` starting with `"MB_"` -- the real associative learning/memory
center, **5,038 neurons**, previously invisible, folded entirely into the
generic `central` super_class), `antennal_lobe` (`group == "AL"` exactly,
**2,762 neurons** -- the real primary olfactory processing center, a
particularly good anatomical match for the existing antennae glow on the
fly's own body), `lateral_horn` (`group == "LH"` exactly, **1,132
neurons** -- the antennal lobe's other major olfactory output pathway,
the real "innate" odor-response route, contrasted with the mushroom
body's "learned" one), and `ellipsoid_body` (`group == "EB"` exactly,
**355 neurons** -- a core component of the real central complex, the
fly's well-studied heading-direction "compass"). These overlap with the
existing super_class-derived regions (a mushroom-body neuron is also
`central`) -- expected and fine: `active_regions` was never a disjoint
partition, each entry independently reports "fraction of THIS named
population currently spiking."

Synapse sign is assigned PER PRESYNAPTIC NEURON, using `neurons.csv`'s
own `nt_type` (Dale's principle: a real neuron releases one dominant
transmitter at essentially all its synapses, making a per-neuron
classification a legitimate, arguably more biologically principled
alternative to `connections.csv`'s per-synapse-row copy this codebase
used before `neurons.csv` existed) -- GABA and glutamate are inhibitory
(GABA is the fly CNS's primary fast inhibitory transmitter; glutamate
acts through inhibitory glutamate-gated chloride channels in insects,
unlike its excitatory role in vertebrates -- both well-established, not
invented for this project); acetylcholine plus the three neuromodulators
present in the data (dopamine, serotonin, octopamine) are excitatory, a
real simplification for those three: at LIF timescales they modulate
rather than directly drive spiking, and this model has no separate
neuromodulatory pathway to route them through. Where the presynaptic
neuron has no per-neuron classification (the real ~14% of neurons, ~4.5%
of synapse rows once weighted by out-degree), sign falls back to that
row's own `connections.csv` `nt_type`.

This switch was measured, not assumed: across `connections.csv`'s real
3,869,878 synapse rows, a presynaptic-neuron `nt_type` from `neurons.csv`
exists for 3,696,438 of them (95.5%); where both a per-synapse-row and a
per-neuron classification exist, they agree on inhibitory-vs-excitatory
96.9% of the time (3,582,425 / 3,696,438) -- a real, strong agreement.
Switching changes the network's overall inhibitory-synapse fraction only
marginally (39.26% per-synapse-row -> 39.15% per-neuron; 2.9% of all rows
flip sign). Post-switch sanity check on the full network (60-tick runs,
seed 0, same methodology as the boost measurements below): motor+
descending activity ~0.48 (within the previously measured 0.31-0.58
mood-sweep range), food_mask baseline ~0.21 rising to ~0.58 boosted
(consistent with the previously measured ~0.22-0.29 baseline / ~0.62-0.65
boosted range), visual_mask baseline ~0.273 rising to ~0.337 with a peer
nearby (matching the previously measured ~0.272-0.274 baseline / ~0.344
boosted numbers almost exactly) -- nothing broke.

Synapse-count weight is capped (median 6, mean 8.8, max 2405 in the real
data) so a handful of outlier connections cannot dominate a tick.

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
is currently closer to, falling off with the fly's own distance from it,
and further scaled by that landmark's own current supply fraction (see
"Where this is headed" below -- landmarks deplete while a fly eats and
respawn after a cooldown, so this boost is not indefinitely available) --
on top of the baseline noise every neuron still gets, reported as
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
target size, hard-capped at **1,000 nodes** (`_SAMPLE_TARGET_NODES`/
`_SAMPLE_MAX_NODES` in `network.py`). This was originally 9,500/10,000 --
shrunk after actually looking at the running app: at 10,000 nodes, with
the frontend's additive-blending glow on every node/edge, the sample
read as one undifferentiated flare, not a legible network. Measured on
the real connectome as it stands today: **1,000 nodes, 12,307 directed
edges** among them (still dense relative to the sample size -- the BFS
frontier reaches the cap well before naturally running out of real
neighbors to add). Building it takes well under 0.2s (cheap next to the
~10-15s full connectome load it happens alongside, in `warm_cache()`).
Every node is a real neuron with a real `super_class`
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

At 1,000 nodes the one-time `synapse_graph` message itself is a real
**~0.8 MB** of JSON (down from 16.2 MB at the earlier 10,000-node size).
That's actually just under `websockets`' own default `max_size` (1 MiB)
now, but `main()` still raises it to 32 MiB (`_MAX_WS_MESSAGE_BYTES`) --
real, deliberate headroom kept regardless of the current payload size,
not sized to it, so a future change to the sample size doesn't need to
rediscover this ceiling by surprise (a silently rejected connection,
code 1009 "message too big").

Every tick after that, fly 0's payload additionally carries
`activity.sample_spikes` -- the LOCAL sample ids (matching the
`synapse_graph` message's node `id`s, so the frontend never has to
translate) that actually spiked *this specific tick*, read straight out
of the real per-tick spike vector `LIFNetwork.step()` already computes,
via a server-side-only mapping from local sample id back to real
connectome index (never sent to the client). At this sample size,
typically **~400-550 of the 1,000** (~45-50%) once the network reaches
its steady-state firing rate (this network's own steady-state firing
fraction runs ~40-45% of a given population generally, sample included,
measured slightly higher -- ~47% -- at this particular smaller sample) --
still never the full 139,255-length spike vector. Same "no neural
introspection into a fly you don't control" boundary the rest of this
project already follows for `activity`/`stimulus`: only fly 0 (the one a
connecting browser controls) ever has its `sample_spikes` actually
forwarded to a client, via the same `_build_client_payload` that already
keeps `world.other_flies` minimal.

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
  no other food type.

  Each landmark now genuinely depletes and respawns (`World`'s
  `food_supply`/`water_supply`, `_EATING_RANGE`/
  `_SUPPLY_CONSUMPTION_PER_TICK`/`_SUPPLY_RESPAWN_TICKS`/
  `_advance_supply` in `server.py`) -- a fly used to be able to sit on a
  landmark forever and keep drawing the same maximum boost indefinitely,
  which does not read as real eating ("no pueden comer eternamente, no
  tiene sentido," reported directly from watching the running app).
  Supply starts at 1.0 (full); while ANY fly is within `_EATING_RANGE`
  (2.0 units -- deliberately much smaller than `_FOOD_RANGE`'s 8.0, since
  `_FOOD_RANGE` is "can smell it from a distance" while this is "close
  enough to actually be eating it," real near-contact feeding, not the
  sensing range), supply drains by `_SUPPLY_CONSUMPTION_PER_TICK` (0.0025)
  each tick, floored at 0; once at 0 a `_SUPPLY_RESPAWN_TICKS` (300)
  cooldown starts, after which supply resets to 1.0. This is shared
  `World` state -- one food source, one water source, not per-fly --
  advanced once per tick alongside `_apply_min_separation`, the same
  "World owns shared resource state" pattern that collision response
  already established. It rides entirely on the existing
  `activity.food_activity` field (no schema change): each landmark's raw
  distance-gradient proximity is scaled by its own current supply
  fraction (`effective_proximity = raw_proximity * supply_fraction`)
  before it drives `net.food_mask`'s boost, so a depleted landmark's
  contribution genuinely fades toward the ~0.22-0.29 no-boost baseline
  even while a fly sits right on top of it, and genuinely climbs back
  once the landmark respawns. `Simulation.step()` takes optional
  `food_supply`/`water_supply` keyword args (each defaulting to 1.0,
  same "standalone-usage default" spirit `peer_positions` already
  follows) so `Simulation` stays independently constructible/testable
  with no `World` around it; `World.step()` reads its own
  `food_supply`/`water_supply` and passes them to every fly each tick.

  Empirically measured (`World(n_flies=1)`, one fly held continuously at
  `FOOD_POSITION`, TICK_HZ=20): supply hits 0 at tick 401 (~20.1s of sim
  time -- matches `1.0/_SUPPLY_CONSUMPTION_PER_TICK` = 400 to within the
  float-accumulation slop of 400 successive subtractions) and respawns
  to 1.0 at tick 701 (300 ticks / 15.0s of cooldown later, exactly
  `_SUPPLY_RESPAWN_TICKS`). Over that same run, `activity.food_activity`
  (EMA-smoothed) sagged from its settled near-landmark range (~0.56-0.58)
  down to ~0.24-0.29 while depleted -- indistinguishable from the
  existing no-boost baseline -- and climbed back to ~0.57-0.58 within a
  few ticks of respawn (see `tests/test_engine.py`'s
  `test_world_food_supply_floors_at_zero_and_respawns_after_cooldown`
  and `test_food_activity_differs_between_full_and_depleted_supply`).
  Still narrow: linear depletion at a fixed rate and a fixed cooldown,
  no partial/probabilistic regrowth, and eating range is a simple
  distance check with no notion of how much of the supply a fly actually
  "consumes" versus another fly at the same landmark.
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
  `test_flies_end_up_near_each_other_without_fly_to_fly_steering`). Since
  then, `World.step()` also runs a minimum-separation collision response
  (`_MIN_FLY_SEPARATION`/`_apply_min_separation`, see their own comments)
  after every tick's movement, so that "proximity" no longer means
  literal overlap the way it briefly did (one pair measured ~0.004 units
  apart -- coincident, given the real flybody mesh's own ~0.8-1.1 unit
  body cross-section, see `_MIN_FLY_SEPARATION`'s comment for the real
  measured mesh size this was picked from). This is a basic physics
  RESPONSE, not a sensing/steering rule -- it never pulls flies together,
  it only pushes already-close flies apart at short range, proportional
  to the overlap (a simple linear spring), so the "no fly-to-fly
  steering" design point above still holds exactly as stated. Re-measured
  with the collision response in place (same seeds, same 2500-tick
  methodology): the minimum pairwise distance across a full run now
  floors at `_MIN_FLY_SEPARATION` (1.5 units) instead of dipping to
  ~0.004, while flies still end up genuinely close to whichever landmark
  they're drawn to (within ~0.6 units of it across all 9 flies measured,
  most well under ~0.02) and close to each other where landmark-seeking
  brings them together (pinned at the 1.5-unit floor rather than
  closer). Still narrow, and still not the real thing: a fixed count of
  3 flies, not a dynamic population; no breeding or reproduction; and
  no actual "meeting" behavior beyond
  proximity, the neutral visual-sensing boost, and now not-overlapping --
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

## The flybody mesh

`frontend/index.html`'s fly mesh is the real anatomically-detailed
*Drosophila melanogaster* body model built by Google DeepMind and HHMI
Janelia Research Campus, sourced directly from
[`google-deepmind/mujoco_menagerie`](https://github.com/google-deepmind/mujoco_menagerie/tree/main/flybody)
(itself taken with permission from the official
[`TuragaLab/flybody`](https://github.com/TuragaLab/flybody) repository) --
**not** through the `fly-connectome-template` (Mert Cobanov) wrapper that
an earlier version of this note flagged as a possible source. That
distinction matters licensing-wise: `fly-connectome-template`'s own
source-available license is more restrictive and covers only Cobanov's UI/
template code, not the flybody mesh itself, which is independently
licensed Apache-2.0 by its own authors regardless of who redistributes it.

Same reasoning as the connectome data below: 49 real `.obj` files, ~81 MB
combined, real third-party binaries with their own provenance -- too
heavy for git and not this project's own work, so `fetch_flybody.py`
downloads them once per machine (`python -m engine.fetch_flybody`,
also run automatically by `python __main__.py`/`run.bat` on first launch
if missing) into `frontend/assets/flybody/`, gitignored except
`SOURCE.md` and this section. `frontend/assets/flybody/SOURCE.md`
documents exactly which of the upstream 85 `.obj` files (~134 MB) were
kept, which were left out and why (decorative bristle/pigment overlay
layers, confirmed pure-black in the source MJCF's own material table,
not guessed from filenames), and how the left/right mirroring works
(bilaterally symmetric parts are fetched once and mirrored at runtime via
negative X-scale, not duplicated). Licensed Apache-2.0 (`LICENSE`,
fetched alongside the mesh, vendored unmodified as the license requires)
-- credited in the app's own UI (the small credit line at the
bottom-left of the HUD) as well as here.

> Vaxenburg, R., Siwanowicz, I., Merel, J. *et al.* Whole-body physics
> simulation of fruit fly locomotion. *Nature* **643**, 1312-1320 (2025).
> https://doi.org/10.1038/s41586-025-09029-4

## Attribution

The connectome data itself (`engine/data/`, fetched not vendored) is MIT
licensed via `solomonsealed/flybrain`, built on the FlyWire Consortium's
public dataset:

> Dorkenwald, S., Matsliah, A., Sterling, A.R. *et al.* Neuronal wiring
> diagram of an adult brain. *Nature* **634**, 124–138 (2024).
> https://doi.org/10.1038/s41586-024-07558-y
