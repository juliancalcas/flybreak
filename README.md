# FlyBreak

Simulates the FlyWire connectome of *Drosophila* under an artificial
"ethanol intoxication" (BAC) stimulus, visualized in 3D: a fly over a
wireframe city, with a live HUD of the simulation's metrics.

**Unrelated to the livery generator.** This folder shares the repo (and the
`.venv`/`requirements.txt`) with `livery_creator/` only so that git already
gives it the same two-machine sync this project uses (see the root
`CLAUDE.md`, "Setup: two machines, one private remote" and "Messages
between machines: HANDOFF.md") — `git pull` at the start of a session,
`git push` at the end, `HANDOFF.md` for anything the other machine needs to
know before continuing. Nothing in `flybreak/` is imported by
`livery_creator/` or vice versa, and `pytest.ini`'s `testpaths = tests`
keeps the livery CI suite from ever collecting `flybreak/tests`.

## Architecture: three decoupled layers

```
flybreak/engine/    Capa 1 -- Python: LIF-lite network + WebSocket server
flybreak/contract/  Capa 2 -- the JSON schema both sides speak, + a mock stream
flybreak/frontend/  Capa 3 -- a self-contained HTML page (Three.js + HUD)
```

Decoupled so the simulation and the visualization can be iterated on
independently: the frontend can run against `contract/mock_server.py`
without the real engine, and the engine's contract is enforced by
`contract/validator.py` on every tick it sends.

### `engine/` -- the simulation

- `network.py` -- `LIFNetwork`: a small leaky-integrate-and-fire population
  (450 neurons), region-tagged (`mushroom_body`, `central_complex`,
  `optic_lobe`, `antennal_lobe`, `motor`), with feedforward-biased random
  connectivity. **This is a synthetic stand-in, not the real FlyWire
  connectome** -- see "Swapping in the real connectome" below.
- `bac.py` -- `BacController`: the hybrid `bac_level` (see "Decisions
  already made").
- `server.py` -- ticks the network at 20 Hz, applies `bac_level` as an
  increased firing threshold on the `motor` region (a deliberate design
  choice, **not a documented biological effect of alcohol on *Drosophila*
  motor neurons** -- there is no such literature this was validated
  against), derives `motor_state` from the resulting activity, validates
  every tick against `contract/schema_v1.json` before sending it, and
  listens for control messages on the same socket.

Run it:

```bash
python -m flybreak.engine.server        # ws://127.0.0.1:8765, one Simulation per client
```

### `contract/` -- the data contract

- `schema_v1.json` -- JSON Schema for one tick, matching the shape agreed
  in the project brief (`schema_version`, `tick`, `sim_time_ms`,
  `stimulus`, `activity`, `motor_state`, `meta`).
- `validator.py` -- `validate_tick()` / `is_valid_tick()`, used by both the
  real engine and the mock stream so neither can silently drift from the
  schema.
- `mock_server.py` -- serves synthetic but schema-valid ticks on the same
  port/message shape as the real engine, for frontend-only iteration:

```bash
python -m flybreak.contract.mock_server  # ws://127.0.0.1:8765, no LIF sim involved
```

`motor_state.action` is `idle | walking | grooming | flying | frozen`,
`wing_state` is `folded | raised | buzzing` -- an initial enum, not fixed;
extend `schema_v1.json` and the engine/frontend together if a new state is
needed.

### `frontend/` -- the visualization

`index.html` -- one self-contained page (Three.js from a CDN, everything
else inline), the same "no build step" convention `web/movil.html` already
uses in this repo. Connects to `ws://<host>:8765` (override with
`window.FLYBREAK_WS_URL` before the script runs, or open it through any
static server), renders a wireframe city + the fly, and a HUD (tick,
sim_time, spike count, firing rate, per-region activity bars, action, wing
state, and the `bac_level` control -- see below). Open it directly in a
browser, or serve it:

```bash
python -m http.server 8080 --directory flybreak/frontend
```

## Decisions already made

- **`bac_level` is hybrid.** It ramps up automatically in the engine
  (`BacController`, ~2 minutes of sim time from 0 to 1 by default) so the
  fly gets progressively drunker on its own, but the HUD slider can pin it
  to any value at any moment (`{"type": "set_bac_level", "value": 0.42}`
  over the same WebSocket). Releasing the override
  (`{"type": "set_bac_mode", "value": "auto"}`, the HUD's "mode" button)
  resumes the automatic ramp from exactly where the override left it --
  never a visible jump. `bac_level` therefore lives in the engine (it is
  the one source of truth sent downstream every tick), and the frontend
  only ever *requests* a value, it never computes one itself.

## Swapping in the real connectome

`engine/network.py`'s `LIFNetwork` is a synthetic placeholder so the whole
pipeline (engine -> contract -> frontend) runs today, on any machine,
without downloading anything. Wiring in the real FlyWire connectome
(Dorkenwald et al., *Nature* 634, 2024) means replacing its random
adjacency matrix with the real synapse-weight matrix from a reference
implementation (`flybrain` / `solomonsealed/flybrain` fork, or
`fly-brain-interactive`) while keeping the same `region_of` / `step()` /
`region_activity()` interface `server.py` already depends on -- **not yet
done, see `HANDOFF.md` if still open, or the git log for how it was
resolved.**

## Attribution

`frontend/index.html` does not currently vendor `fly-connectome-template`
(Mert Cobanov) -- see `HANDOFF.md` if still open, or the git log for how
that was resolved. If/when its MaleCNS atlas or Flybody mesh is pulled in,
its source-available license requires crediting the template both in this
project's UI and in this README; re-check the exact license text before
that happens, and before anything from this project is ever made public.
