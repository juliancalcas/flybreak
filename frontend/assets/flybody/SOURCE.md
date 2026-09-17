# flybody mesh assets -- provenance

Fetched per machine with `python -m engine.fetch_flybody`, not vendored
in git (the `.obj` files and `LICENSE` in this directory are gitignored
-- only this file is tracked; see `.gitignore` and `engine/fetch_flybody.py`
for why, same reasoning as `engine/data/`'s connectome CSVs). Source:
`google-deepmind/mujoco_menagerie`, path `flybody/assets/`
(commit fetched 2026-09-17): https://github.com/google-deepmind/mujoco_menagerie/tree/main/flybody

An anatomically-detailed body model of *Drosophila melanogaster*, built by
Google DeepMind and HHMI Janelia Research Campus, taken with permission from
the official https://github.com/TuragaLab/flybody repository.

**License**: Apache License 2.0 -- see `LICENSE` in this directory (the
license file from the source directory, vendored unmodified as required).
flybody's own license applies to these mesh files regardless of which repo
redistributes them; it is unrelated to and more permissive than any custom
license on third-party wrapper/template projects that also redistribute
this mesh (e.g. `cobanov/fly-connectome-template`), which cover only their
own UI/template code, not the underlying flybody assets themselves.

**Citation**:
> Vaxenburg, R., Siwanowicz, I., Merel, J. *et al.* Whole-body physics
> simulation of fruit fly locomotion. *Nature* **643**, 1312-1320 (2025).
> https://doi.org/10.1038/s41586-025-09029-4

## What's vendored here vs. the real upstream `assets/` directory

Upstream `flybody/assets/` is 85 `.obj` files, ~134 MB total -- far more
than this browser app (no build step, loads every mesh once at startup via
Three.js's `OBJLoader`, plain synchronous text parsing) can reasonably
fetch/parse on load. What's kept here is a curated subset, chosen by two
non-destructive rules, not by editing or simplifying any individual mesh:

1. **Dropped the fine bristle/pigment overlay layers that are pure black
   in `fruitfly.xml`'s own material table** (`<material name="black"
   rgba="0 0 0 1"/>`, `"bristle-brown" rgba="0 0 0 1"`): `head_black.obj`,
   `thorax_black.obj`, `antenna_*_black.obj`, `haustellum_black.obj`,
   `rostrum_bristle-brown.obj`, and the wing vein layer
   `wing_*_brown.obj`. Each is a *separate geom rendered at the exact same
   position/orientation as its corresponding `_body`/`_lower`/`_membrane`
   mesh* -- i.e. a thin fuzz/vein-pattern layer on top of the real
   structural mesh, confirmed by checking both the MJCF geom placements
   and each material's actual color before deciding it was droppable, not
   by name alone. **`head_red.obj` was checked the same way and kept**:
   its material is `rgba="0.8 0.0279 0.00154"` (genuine red) and its
   bounding box bulges laterally past `head_body.obj`'s own surface --
   this is the fly's actual compound eyes (each apparently modeled down
   to individual ommatidium facets, hence its size), not bristle fuzz, and
   the region-activity mapping needs it as the real `eyes` mesh. Dropping
   the confirmed-decorative layers removes ~63 MB while every real body
   part (head incl. compound eyes and ocelli, thorax, abdomen, legs,
   wings, antennae, mouth parts) keeps its actual structural mesh.
2. **Vendored only the `_left_*` file of every bilaterally-symmetric part**
   (antenna, coxa/femur/tibia/tarsus/tarsal_claw per leg, haltere, labrum,
   wing membrane) and mirror it in `frontend/index.html` via a negative
   X-scale, exactly the same technique this file's own primitive fly mesh
   already used for its wings (`wingL`/`wingR`). Verified first that every
   left/right pair has byte-identical vertex counts (true mirror
   geometry) before relying on this -- see git history / session notes.
   This halves the vendored byte count for every paired part with zero
   loss of real anatomy (the "right" side rendered at runtime is the same
   real geometry, just mirrored, not a fabricated approximation).

Result: 49 `.obj` files, ~81 MB, ~262k triangles per fully-assembled fly
(measured at runtime, not estimated: 78 meshes, 261,600 triangles per fly)
(head incl. compound eyes and ocelli + thorax + full abdomen + both
antennae + 6 legs each with real coxa/femur/tibia/4 tarsus segments/claw +
both wings + haltere + mouth parts) -- still "tens of MB", reported as
such rather than silently trimmed further, and dominated by one file
(`head_red.obj`, the compound eyes, ~31 MB alone -- kept because it is
real anatomy the region-activity mapping needs, not because it's cheap);
not vendored: the fine bristle/pigment overlay layers listed above, and
everything outside `flybody/assets/` (the MJCF files, PNG renders,
per-part collision primitives -- none of that is needed for a
visual-only Three.js mesh).

Coordinate frame: every kept file's vertices share one common frame (the
model's assembled rest pose) rather than being centered on their own part,
confirmed by comparing bounding-box centers across parts before writing
any loading code -- so the parts compose into a correct fly with no
additional per-part offset/rotation, only one shared axis remap + uniform
scale + recenter applied once in `frontend/index.html`.
