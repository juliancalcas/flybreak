"""One-time, per-machine fetch of the real FlyWire connectome data that
flybreak.engine.network loads at startup.

Not run automatically and not part of any test: the data is a real
third-party download (~50 MB), so pulling it is a deliberate step, same
spirit as liveries/CLAUDE.md's Google Drive symlinks for its own heavy
binaries -- nothing this large belongs in git (see flybreak/.gitignore).

Source: solomonsealed/flybrain (MIT license), whose data/ folder packages
the FlyWire FAFB v783 connectome (Dorkenwald et al., Nature 634, 2024) as
plain CSVs -- connections.csv.gz (pre/post neuron IDs, synapse count,
neurotransmitter type) and classification.csv.gz (neuron ID -> FlyWire's
own super_class/class annotation). See flybreak/README.md, "The real
connectome" for what network.py does with them and why.

Run from the repo root with: python -m flybreak.engine.fetch_connectome
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

_BASE_URL = "https://raw.githubusercontent.com/solomonsealed/flybrain/main/data"
_FILES = ["connections.csv.gz", "classification.csv.gz"]
_DATA_DIR = Path(__file__).parent / "data"


def fetch(force: bool = False) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name in _FILES:
        dest = _DATA_DIR / name
        if dest.exists() and not force:
            print(f"already have {dest} ({dest.stat().st_size:,} bytes), skipping")
            continue
        url = f"{_BASE_URL}/{name}"
        print(f"fetching {url} -> {dest}")
        urllib.request.urlretrieve(url, dest)
        print(f"  {dest.stat().st_size:,} bytes")


if __name__ == "__main__":
    import sys
    fetch(force="--force" in sys.argv)
