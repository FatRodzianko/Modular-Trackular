"""Where tools/patch_ptf.py put each patch: reads <firmware>.map.json next to a .ptf."""
from __future__ import annotations

import json
from pathlib import Path


def patch_ram(ptf: Path, name: str) -> int:
    """Start of a patch's RAM in the firmware built at `ptf` (from its .map.json)."""
    map_path = Path(ptf).with_suffix(".map.json")
    if not map_path.exists():
        raise SystemExit(f"{map_path} not found: build the firmware with tools/patch_ptf.py, "
                         "which writes it next to the .ptf")
    for p in json.loads(map_path.read_text())["patches"]:
        if p["name"] == name and p["ram"]:
            return int(p["ram"]["start"], 16)
    raise SystemExit(f"{map_path}: no RAM for {name}")


def patch_code(ptf: Path, name: str) -> tuple[int, int]:
    """Start and end of a patch's code in the firmware built at `ptf` (from its .map.json)."""
    map_path = Path(ptf).with_suffix(".map.json")
    if not map_path.exists():
        raise SystemExit(f"{map_path} not found: build the firmware with tools/patch_ptf.py, "
                         "which writes it next to the .ptf")
    for p in json.loads(map_path.read_text())["patches"]:
        if p["name"] == name:
            lo, hi = (int(x, 16) for x in p["code"])
            return lo, hi
    raise SystemExit(f"{map_path}: no code for {name}")
