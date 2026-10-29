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


def patch_late_ram(ptf: Path, name: str) -> tuple[int, int]:
    """Start and end of a patch's late RAM block ("late_ram") in the firmware built at `ptf`."""
    map_path = Path(ptf).with_suffix(".map.json")
    if not map_path.exists():
        raise SystemExit(f"{map_path} not found: build the firmware with tools/patch_ptf.py, "
                         "which writes it next to the .ptf")
    for p in json.loads(map_path.read_text())["patches"]:
        if p["name"] == name and p.get("late_ram"):
            return int(p["late_ram"]["start"], 16), int(p["late_ram"]["end"], 16)
    raise SystemExit(f"{map_path}: no late RAM for {name}")


def patch_function(ptf: Path, name: str, symbol: str) -> tuple[int, int]:
    """Start and end of one function of patch/<name> in the firmware built at `ptf`: its
    symbol up to the next one, from the patch's build (or prebuilt, as patch_ptf.py
    picks), moved to where the patch landed."""
    d = Path(__file__).resolve().parent.parent.parent / "patch" / name
    spec = json.loads((d / "patch.json").read_text())
    pre = "" if (d / spec["bin"]).exists() else "prebuilt_"
    syms = {}
    for line in (d / spec[pre + "sym"]).read_text().splitlines():
        parts = line.split()
        if len(parts) == 3:
            syms[parts[2]] = int(parts[0], 16)
    link = next(int(line.split()[1], 16) for line in (d / spec[pre + "rel"]).read_text().splitlines()
                if line.startswith("link "))
    lo, _ = patch_code(ptf, name)
    start = syms[symbol]
    end = min(v for v in syms.values() if v > start)
    return start - link + lo, end - link + lo
