"""One-time, per-machine fetch of the real flybody anatomical mesh that
frontend/index.html loads and assembles into each fly's on-screen model.

Not committed to git: 49 real .obj files, ~81 MB combined -- the exact
same reasoning as fetch_connectome.py's connectome data (too heavy for
git, a third-party download with its own provenance, not this project's
own work), just for a different asset serving a different layer
(frontend rendering, not the engine's simulation).

Source: google-deepmind/mujoco_menagerie, path flybody/assets/ (Apache
License 2.0) -- an anatomically-detailed body model of Drosophila
melanogaster built by Google DeepMind and HHMI Janelia Research Campus.
See frontend/assets/flybody/SOURCE.md (tracked in git, unlike the mesh
files themselves) for the full citation, license details, and exactly
which files are fetched here and why -- upstream ships 85 files/~134 MB;
this fetches a curated 49-file/~81 MB subset (real structural anatomy
only, decorative bristle/pigment overlay layers dropped, bilaterally
symmetric parts fetched once and mirrored at runtime in
frontend/index.html).

Run from the repo root with: python -m engine.fetch_flybody
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

_BASE_URL = "https://raw.githubusercontent.com/google-deepmind/mujoco_menagerie/main/flybody"
_LICENSE_FILE = "LICENSE"
_ASSET_FILES = [
    "abdomen_1_body.obj", "abdomen_1_lower.obj",
    "abdomen_2_body.obj", "abdomen_2_lower.obj",
    "abdomen_3_body.obj", "abdomen_3_lower.obj",
    "abdomen_4_body.obj", "abdomen_4_lower.obj",
    "abdomen_5_body.obj", "abdomen_5_lower.obj",
    "abdomen_6_body.obj", "abdomen_6_lower.obj",
    "abdomen_7_body.obj", "abdomen_7_lower.obj",
    "abdomen_8_body.obj",
    "antenna_left_body.obj",
    "coxa_T1_left_body.obj", "coxa_T2_left_body.obj", "coxa_T3_left_body.obj",
    "femur_T1_left_body.obj", "femur_T2_left_body.obj", "femur_T3_left_body.obj",
    "haltere_left_body.obj",
    "haustellum_body.obj",
    "head_body.obj", "head_ocelli.obj", "head_red.obj",
    "labrum_left_lower.obj",
    "rostrum_body.obj",
    "tarsal_claw_T1_left_brown.obj", "tarsal_claw_T2_left_brown.obj", "tarsal_claw_T3_left_brown.obj",
    "tarsus_T1_1_left_body.obj", "tarsus_T1_2_left_body.obj", "tarsus_T1_3_left_body.obj", "tarsus_T1_4_left_body.obj",
    "tarsus_T2_1_left_body.obj", "tarsus_T2_2_left_body.obj", "tarsus_T2_3_left_body.obj", "tarsus_T2_4_left_body.obj",
    "tarsus_T3_1_left_body.obj", "tarsus_T3_2_left_body.obj", "tarsus_T3_3_left_body.obj", "tarsus_T3_4_left_body.obj",
    "thorax_body.obj",
    "tibia_T1_left_body.obj", "tibia_T2_left_body.obj", "tibia_T3_left_body.obj",
    "wing_left_membrane.obj",
]
_DEST_DIR = Path(__file__).parent.parent / "frontend" / "assets" / "flybody"


def fetch(force: bool = False) -> None:
    _DEST_DIR.mkdir(parents=True, exist_ok=True)

    dest = _DEST_DIR / _LICENSE_FILE
    if dest.exists() and not force:
        print(f"already have {dest}, skipping")
    else:
        url = f"{_BASE_URL}/{_LICENSE_FILE}"
        print(f"fetching {url} -> {dest}")
        urllib.request.urlretrieve(url, dest)

    for name in _ASSET_FILES:
        dest = _DEST_DIR / name
        if dest.exists() and not force:
            print(f"already have {dest} ({dest.stat().st_size:,} bytes), skipping")
            continue
        url = f"{_BASE_URL}/assets/{name}"
        print(f"fetching {url} -> {dest}")
        urllib.request.urlretrieve(url, dest)
        print(f"  {dest.stat().st_size:,} bytes")


def flybody_available() -> bool:
    return (_DEST_DIR / _LICENSE_FILE).is_file() and all(
        (_DEST_DIR / name).is_file() for name in _ASSET_FILES
    )


if __name__ == "__main__":
    import sys
    fetch(force="--force" in sys.argv)
