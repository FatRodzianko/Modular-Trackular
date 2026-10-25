"""Check PHASE, the synths patch's phase distortion synth (docs/PD_SYNTH_PLAN.md): the model,
the menu, the tiles, the files and the swaps, and the engine (ph_engine.h).

  python tools/emu/test_phase.py <patched.ptf> <stock TrackerPlus_1.2.0.ptf> [<sy7 .ptf>]
         [--quick | --engine] [--wav <dir>]

--quick (tools/run_tests.py, on mixes): everything but the engine's checks, which depend
on its code alone, and one note in their place. --engine runs the engine's checks alone.
--wav writes listening files (each shape's DCW sweep, pairs, a resonant sweep, Ring and
Noise Mod, a DCO envelope pluck, an Init chord).

Uses tools/emu/test_synths.py's rig and helpers. With a firmware built from synths sy7 (the
last WAVE-only build), WAVE is also checked to be unchanged by PHASE: the same model and
the same output, sample for sample.

- Hooks: the Edit Patch tiles' call (0x6011e924) goes to the patch; the registry names
  model 7 "PHASE" and model 3 "WAVE", the others as stock.
- The PHASE model: a patch with model id 7, 66 parameters (every per-parameter array, also
  after a rebuild), 2 channels; its names, ranges, stepped lists, CCs, value displays,
  Init values, the screen (DCO, DCW, Modulation: DCA, DCW Env/LFO 1, DCO Env/LFO 2), the
  Edit Patch pages and the Macros; the title "Synth 1 (PHASE)"; no heap growth over 50
  model switches.
- The parameter copy: set_param reaches PHASE's places (ph_engine.h's map).
- The swap: PHASE alone, WAVE and PHASE together, the same model in both (the item
  changed last wins; WAVE when both change at once), a slot moving from WAVE to PHASE,
  200 swaps with notes held (heap and engine voices).
- Config menu: "PHASE replaces" after "WAVE replaces", on its own value.
- The Model list: "PHASE" as text where the replaced logo was, beside "WAVE".
- The Edit Patch screen: the logo label reads "PHASE"; the tiles read DCO, DCW, DCA,
  DCW Env, DCO Env with the stock tiles' icons in those places; other synths' tiles stock.
- synths.bin: byte 5 holds PHASE's choice; one model in both bytes loads PHASE Off; out of
  range or byte 6 set: the file counts as missing and is kept; an sy7 file loads PHASE Off.
- Patch files: PHASE/<name>.patch with model id 7 and 66 values, loaded back into a PHASE
  slot; stock firmware rejects it on an ACD slot.
- The engine, against the plan's formulas worked out in float64 (ref_shape: the
  repository's own, as ph_engine.h's): every shape sample for sample from phase 0 at
  DCW 0, 50 and 100 %, also where the knee and resonance limits act; DCW 0 a pure sine
  (shapes 1-5); harmonics within 0.1 dB of the formula's; brightness rising with DCW;
  aliasing; no jumps (Double Sine, Reso I-III); pairs (alternate cycles, the sub-octave,
  no two resonant ones); Ring and Noise Mod; the DCO envelope; pitch, Tune, Finetune,
  Detune, bend; the envelopes' times; Env Amt 1 and 2; Key Follow; glide; each LFO through
  each destination, every source to every destination at +-400 % bounded; the Init
  level; velocity; voice allocation as stock VAP; the effect; the cost; "sounding".
- Stress (300 blocks): test_synths.py's, with the swaps setting both items.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import synth_rig as sr  # noqa: E402
import test_synths as ts  # noqa: E402
from fakefs import FakeFS  # noqa: E402
from layout import patch_code, patch_late_ram  # noqa: E402
from machine import Machine  # noqa: E402
from patch_ptf import bl_target  # noqa: E402

ACD, FAT, VAP, WTFM, PERC = sr.ACD, sr.FAT, sr.VAP, sr.WTFM, sr.PERC
WAVE, PHASE = ts.WAVE, ts.PHASE
INDEX, MODELS = ts.INDEX, ts.MODELS
check = ts.check
Rig = ts.Rig
sstr = ts.sstr
MAP_FIELDS = ts.SY8_FIELDS          # sy8's map: PHASE's and the menu positions at the end
SY7_FIELDS = ts.MAP_FIELDS
LIST_TEXT_PHASE = 0x7E
BL_TILES, TILE_BUILDER = 0x6011E924, 0x6011D628
SELECTED = 0x202046C5               # the selected instrument: 64-66, S1-S3

SHAPES = ["Saw", "Square", "Pulse", "Double Sine", "Saw-Pulse", "Reso I", "Reso II", "Reso III"]
PAIRS = ["Off"] + SHAPES
SRC_NAMES = ["DCA Env", "DCW Env", "DCO Env", "LFO 1", "LFO 2", "Velocity", "Key"]
DEST_NAMES = ["Pitch", "Pitch 1", "Pitch 2", "DCW", "DCW 1", "DCW 2", "Mix", "Volume", "Detune"]
PHASE_INIT = {0: ("Wave 1+", 0.0), 1: ("Wave 2+", 0.0), 2: ("Wave 1", 0.0), 3: ("Wave 2", 0.0), 4: ("Mix", 0.0),
              5: ("Tune 1", 0.0), 6: ("Tune 2", 0.0), 7: ("Detune", 8.0), 8: ("Finetune", 0.0), 9: ("Reset 1", 0.0),
              10: ("Reset 2", 0.0), 11: ("Ring Mod", 0.0), 12: ("DCW 1", 0.5), 13: ("DCW 2", 0.5),
              14: ("Env Amt 1", 0.3), 15: ("Attack", 0.005), 16: ("Decay", 0.3), 17: ("Sustain", 0.0),
              18: ("Release", 0.3), 19: ("Attack", 0.005), 20: ("Decay", 0.5), 21: ("Sustain", 0.8),
              22: ("Release", 0.4), 23: ("Attack", 0.005), 24: ("Decay", 0.6), 25: ("Sustain", 0.3),
              26: ("Release", 0.5), 27: ("Ratio", 1.0), 28: ("Waveform", 0.0), 29: ("Retrigger", 2.0),
              30: ("Sync", 0.0), 31: ("Ratio", 0.25), 32: ("Waveform", 1.0), 33: ("Retrigger", 2.0), 34: ("Sync", 0.0),
              35: ("Glide Mode", 0.0), 36: ("Glide Time", 0.0), 37: ("Key Follow", 0.0), 38: ("Env Amt 2", 0.3),
              40: ("Noise Mod", 0.0), 41: ("DCO Env Amt", 0.0), 42: ("Src 1", 3.0), 43: ("Dest 1", 0.0),
              44: ("Amt 1", 0.0), 45: ("Src 2", 4.0), 46: ("Dest 2", 3.0), 47: ("Amt 2", 0.0),
              60: ("Polarity", 0.0), 61: ("Polarity", 0.0), 62: ("Effect", 0.0), 63: ("Rate", 0.5),
              64: ("Depth", 0.5), 65: ("Mix", 0.5)}
PHASE_GROUPS = [("DCO", [("(1/2)", [2, 3, 4, 0, 1, 11, 5, 6, 7, -1]), ("(2/2)", [9, 10, 40, -1, -1, 8, -1, -1])]),
                ("DCW", [("", [12, 13, -1, 14, 38, 37, -1, -1, -1])]),
                ("Modulation", [("DCA", [19, 20, 21, 22, -1, -1]),
                                ("DCW Env/LFO 1", [23, 24, 25, 26, 60, 30, 27, 28, 29]),
                                ("DCO Env/LFO 2", [15, 16, 17, 18, 41, 61, 34, 31, 32, 33])]),
                ("Matrix", [("(1/2)", list(range(42, 51))), ("(2/2)", list(range(51, 60)) + [-1])]),
                ("FX", [("", [])]),
                ("Voice", [("", [35, 36, 39, 62, 63, 64, 65])])]
PHASE_PAGES = [("DCO", "DCO", [2, 0, 3, 1, 4, 5, 6, 8, 7, 9, 10, 11, 40]), ("DCW", "", [12, 13, 14, 38, 37]),
               ("DCA", "DCA", [19, 20, 21, 22]), ("DCW Envelope", "DCW Env", [23, 24, 25, 26]),
               ("DCO Envelope", "DCO Env", [15, 16, 17, 18, 41]), ("LFO 1", "", [30, 27, 28, 29, 60]),
               ("LFO 2", "", [34, 31, 32, 33, 61]), ("Modulation", "Mod", list(range(42, 60))),
               ("Voice", "", [35, 36, 39, 62, 63, 64, 65])]
PHASE_MACROS = [("Attack", [19]), ("Decay/Release", [20, 24, 22, 26]), ("DCW", [12, 13]), ("DCW Env Amt", [14, 38]),
                ("Detune", [7]), ("Mix", [4]), ("Velocity", [39]), ("Pressure", [])]
STOCK_TILES = ["Osc", "Filter", "Amp Env", "Env 1", "Env 2", "LFO 1", "LFO 2", "Mod", "Voice"]
PHASE_TILES = ["DCO", "DCW", "DCA", "DCW Env", "DCO Env", "LFO 1", "LFO 2", "Mod", "Voice"]
# ph_engine.h's map: set_param index -> its place in the slot's copy (39 Volume: none; 62-65: the effect's)
PH_PLACE = {i: i for i in range(39)} | {i: i - 1 for i in range(40, 62)}
P_DCW1, P_WAVE1, P_PENV_AMT, P_NOISEMOD, P_DCA_REL, P_VOLUME, P_LFO2_POL = 12, 2, 41, 40, 22, 39, 61


# ---- helpers ------------------------------------------------------------------------------

def ph_swap(r: Rig, model: int | None, apply: bool = True) -> None:
    """The menu's "PHASE replaces" (None: Off), applied by a Config update."""
    r.m.w8(r.st("ph_menu"), 0 if model is None else 1 + INDEX[model])
    if apply:
        r.config()


def ph_menu(r: Rig) -> int:
    return r.m.uc.mem_read(r.st("ph_menu"), 1)[0]


def ph_applied(r: Rig) -> int:
    return r.m.uc.mem_read(r.st("ph_applied"), 1)[0]


def entries(r: Rig) -> list[tuple[int, int, int]]:
    """The engine voices in use: (VAP voice, slot, engine: 0 WAVE, 1 PHASE)."""
    out = []
    for k in range(8):
        a = r.st("voice") + k * ts.WHERE["voice_size"]
        owner, slot, engine = struct.unpack("<IHB", bytes(r.m.uc.mem_read(a, 7)))
        if owner:
            out.append((owner, slot, engine))
    return out


def phase_rig(ptf: Path, models=(VAP, PERC, PERC), polys=(1, 1, 1), boot: bool = True) -> Rig:
    """PHASE replacing VAP."""
    r = Rig(ptf)
    r.configure(models, polys)
    if boot:
        r.pattern()
    ph_swap(r, VAP)
    return r


def use(where: dict, code: tuple, late: tuple) -> None:
    """test_synths' helpers on one build's patch (its RAM layout and code)."""
    ts.WHERE, ts.CODE, ts.LATE = where, code, late


def layout_of(ptf: Path) -> tuple[dict, tuple, tuple]:
    where = ts.ram_map(Machine(ptf), ptf)
    return where, patch_code(ptf, "synths"), patch_late_ram(ptf, "synths")


# ---- 1. hooks -----------------------------------------------------------------------------

def hook_checks(ptf: Path, stock: Path) -> None:
    print("hooks")
    p = Machine(ptf)
    image = bytes(p.uc.mem_read(0x60000000, 0x800000))
    t = bl_target(image, 0x60000000, BL_TILES)
    s_image = bytes(Machine(stock).uc.mem_read(0x60000000, 0x800000))
    check(ts.CODE[0] <= t < ts.CODE[1] and bl_target(s_image, 0x60000000, BL_TILES) == TILE_BUILDER,
          f"the Edit Patch tiles' call ({BL_TILES:#x}, stock {TILE_BUILDER:#x}) goes to the patch ({t:#x})")
    names = ts.model_names(sr.SynthRig(ptf))
    stock_names = ts.model_names(sr.SynthRig(stock))
    check(names[7] == "PHASE" and names[3] == "WAVE" and stock_names[7] == stock_names[3] == ""
          and all(names[i] == stock_names[i] for i in (0, 1, 2, 4, 5, 6)),
          f"the registry: model 7 is PHASE, model 3 WAVE (stock firmware has neither), the others as stock ({names})")


# ---- 2. the PHASE model ---------------------------------------------------------------------

def model_checks(ptf: Path) -> None:
    print("the PHASE model (model 7: VAP's builder, rewritten)")
    r = Rig(ptf)
    m = r.m
    r.configure((VAP, PERC, PERC), (2, 1, 1))
    vap_ranges = r.ranges(0)
    vap_flags = [m.r32(q + 0x20) for q in r._entries(0)]
    ph_swap(r, VAP)
    p = r.patch_obj(0)
    c = p + 0x54
    ents = r._entries(0)
    check(r.model(0) == PHASE and m.r32(c + 0x24) == PHASE and len(ents) == 66 and m.r32(c + 0x28) == 2
          and all(m.r32(v) == sr.VOICE_VT[VAP] for v in r.voices(0)),
          f"slot 0 on model {r.model(0)}: a patch with model id {m.r32(c + 0x24)}, {len(ents)} parameters, "
          f"{m.r32(c + 0x28)} output channels, VAP voice objects")
    vals, defaults = r.params(0), r.defaults(0)
    bad = [(i, sstr(m, ents[i]), vals[i], defaults[i]) for i, (n, v) in PHASE_INIT.items()
           if sstr(m, ents[i]) != n or abs(vals[i] - v) > 1e-6 * max(1.0, abs(v)) or defaults[i] != vals[i]]
    check(not bad, f"PHASE's names and Init values, as the patch's values and its defaults ({len(PHASE_INIT)} "
                   f"parameters; wrong: {bad})")
    ranges = r.ranges(0)
    flags = [m.r32(q + 0x20) for q in ents]
    cc = [m.uc.mem_read(q + 0x23, 1)[0] for q in ents]
    srcs, dests = range(42, 60, 3), range(43, 60, 3)
    want = {0: (0.0, 8.0), 1: (0.0, 8.0), 2: (0.0, 7.0), 3: (0.0, 7.0), 11: (0.0, 1.0), 12: (0.0, 1.0),
            13: (0.0, 1.0), 37: (-1.0, 1.0), 38: (-1.0, 1.0), 40: (0.0, 1.0), 41: (-48.0, 48.0)}
    want |= {i: (0.0, 6.0) for i in srcs} | {i: (0.0, 8.0) for i in dests}
    wrong = {i: ranges[i] for i in range(60) if ranges[i] != want.get(i, vap_ranges[i])}
    check(not wrong, f"ranges: Wave 1+/2+ 0-8, Wave 1/2 0-7, Ring Mod 0-1, DCW 1/2 0-1, Key Follow and Env Amt 2 "
                     f"+-1, Noise Mod 0-1, DCO Env Amt +-48, Src 0-6, Dest 0-8; every other as VAP's (wrong: {wrong})")
    stepped = [i for i in range(66) if (flags[i] >> 16) & 1]
    check(all(flags[i] == vap_flags[11] for i in (0, 1, 2, 3, 11, 41)) and {0, 1, 2, 3, 11, 41} <= set(stepped)
          and flags[40] == vap_flags[41] and flags[12] == (vap_flags[12] & 0xFF000000) | (vap_flags[41] & 0xFFFFFF)
          and flags[13] == (vap_flags[13] & 0xFF000000) | (vap_flags[41] & 0xFFFFFF)
          and flags[38] == 0xFF000000 | (vap_flags[14] & 0xFFFFFF) and flags[14] == vap_flags[14],
          f"flags: the waves, Ring Mod and DCO Env Amt stepped as VAP's Type ({vap_flags[11]:#x}); DCW 1/2 plain "
          f"with VAP's CCs; Noise Mod plain as VAP's Noise; Env Amt 2 as Env Amt 1 without a CC (stepped: {stepped})")
    check(cc[12] == 74 and cc[13] == 71 and cc[14] == 77 and cc[38] == cc[40] == cc[41] == 0xFF,
          f"CCs: DCW 1 {cc[12]}, DCW 2 {cc[13]}, Env Amt 1 {cc[14]} (VAP's Cutoff, Resonance, Env Amt); Env Amt 2, "
          f"Noise Mod, DCO Env Amt none")
    d = ts.display
    shown = {"Wave 1": [d(m, ents[2], float(k)) for k in range(8)],
             "Wave 2+": [d(m, ents[1], float(k)) for k in range(9)],
             "Src": [d(m, ents[42], float(k)) for k in range(7)],
             "Dest": [d(m, ents[58], float(k)) for k in range(9)],
             "Ring Mod": [d(m, ents[11], v) for v in (0.0, 1.0)]}
    check(shown == {"Wave 1": SHAPES, "Wave 2+": PAIRS, "Src": SRC_NAMES, "Dest": DEST_NAMES,
                    "Ring Mod": ["Off", "On"]} and d(m, ents[28], 4.0) == "Square",
          f"value displays: {shown}; the LFOs keep VAP's")
    clamped = [d(m, ents[3], v) for v in (-3.0, 19.0, float("nan"))] + [d(m, ents[0], 99.0)]
    check(clamped == ["Saw", "Reso III", "Saw", "Reso III"], f"out-of-range values show the nearest name ({clamped})")
    pct = [d(m, ents[i], v) for i in (12, 13, 40) for v in (0.0, 0.5, 1.0)]
    semis = [d(m, ents[41], v) for v in (-48.0, -12.4, 0.0, 7.6, 99.0, float("nan"))]
    check(pct == ["0 %", "50 %", "100 %"] * 3 and semis == ["-48 st", "-12 st", "0 st", "8 st", "48 st", "-48 st"],
          f"DCW 1, DCW 2, Noise Mod as percentages ({pct[:3]}); DCO Env Amt in semitones ({semis})")
    rebuild = [m.uc.mem_read(q + 0x24, 1)[0] for q in ents]
    check([i for i, x in enumerate(rebuild) if x] == [30, 34], "only the LFOs' Sync rebuilds the patch, as VAP's")
    check(ts.arrays(m, p) == {k: 66 for k in ts.PATCH_ARRAYS} and (m.r32(c + 8) - m.r32(c)) // 0x38 == 66,
          f"the patch's per-parameter arrays: {ts.arrays(m, p)}; the parameter vector's room 66")
    caps = {k: m.r32(p + off + 8) for k, (off, _) in ts.PATCH_ARRAYS.items()}
    setv = m.r32(m.r32(p) + 0x10)
    val = m.alloc(8)
    for v in (1.0, 0.0):
        m.write(val, struct.pack("<f", v))
        m.call(setv, p, 30, val, 2, max_insns=50_000_000)
    check(len(r._entries(0)) == 66 and ts.arrays(m, p) == {k: 66 for k in ts.PATCH_ARRAYS}
          and {k: m.r32(p + off + 8) for k, (off, _) in ts.PATCH_ARRAYS.items()} == caps and m.r32(c + 0x24) == PHASE
          and ts.container_layout(m, c)[1] == PHASE_PAGES and d(m, r._entries(0)[41], -5.0) == "-5 st",
          "a rebuild (set_value on Sync): still 66 parameters, the arrays as they were, model 7, the pages and "
          "displays as before")
    groups, pages, macros = ts.container_layout(m, c)
    check(groups == PHASE_GROUPS, f"the screen: {[(g, [n for n, _ in s]) for g, s in groups]}")
    check(pages == PHASE_PAGES, f"the Edit Patch pages: {[(q[0], q[1]) for q in pages]}")
    check([(n, t) for n, t, _ in macros] == PHASE_MACROS and all(a == 1.0 for _, _, am in macros for a in am),
          f"the Macros: {[(n, t) for n, t, _ in macros]}")
    check(r.title(0) == "Synth 1 (PHASE)" and r.title(1) == "Synth 2 (PERC)",
          f"the instrument title: {r.title(0)!r}, the next slot {r.title(1)!r}")
    heap = []
    for k in range(50):
        r.set_model(0, VAP if k % 2 == 0 else PHASE)
        if k % 10 == 9:
            heap.append(r.heap_used())
    check(ts.no_growth(heap) and r.model(0) == PHASE,
          f"50 switches between VAP and PHASE: heap use {heap} (no growth)")


def copy_checks(ptf: Path) -> None:
    print("the slot's parameter copy (ph_engine.h's map)")
    r = phase_rig(ptf)
    vals = r.params(0)
    pc = r.param_copy(0)
    bad = [i for i, place in PH_PLACE.items() if abs(pc[place] - vals[i]) > 1e-6]
    check(not bad, f"the copy holds PHASE's Init values at their places (wrong: {bad})")
    r.set_param(0, P_PENV_AMT, -12.0)
    r.set_param(0, P_NOISEMOD, 0.25)
    r.set_param(0, P_LFO2_POL, 1.0)
    r.set_param(0, P_DCW1, 0.75)
    pc = r.param_copy(0)
    check(pc[40] == -12.0 and pc[39] == 0.25 and pc[60] == 1.0 and pc[12] == 0.75,
          f"set_param: DCO Env Amt (41) to place 40 ({pc[40]}), Noise Mod (40) to 39 ({pc[39]}), LFO 2's Polarity "
          f"(61) to 60 ({pc[60]}), DCW 1 (12) to 12 ({pc[12]})")


# ---- 3. the swap ----------------------------------------------------------------------------

def swap_checks(ptf: Path) -> None:
    print("the swap (Config update): PHASE, and WAVE and PHASE together")
    r = Rig(ptf)
    r.configure((VAP, ACD, PERC), (3, 2, 1))
    r.pattern()
    r.created.clear()
    r.pushes.clear()
    ph_swap(r, VAP)
    v0 = r.voices(0)
    e = entries(r)
    check(r.table() == [1, 2, PHASE, 5, 6] and ph_applied(r) == INDEX[VAP] and r.applied() == 0xFF
          and r.model(0) == PHASE and r.model(1) == ACD and r.patch_model(0) == PHASE
          and sorted(x[0] for x in e) == sorted(v0) and all(x[1:] == (0, 1) for x in e)
          and not any(x in v0 for x, _ in r.pushes),
          f"PHASE replacing VAP: the table {r.table()}, slot 0 PHASE with an engine voice (PHASE's) per voice {e}, "
          "no set_param reaching VAP")
    r.swap(ACD)
    e = entries(r)
    check(r.table() == [WAVE, 2, PHASE, 5, 6] and r.model(1) == WAVE and r.model(0) == PHASE
          and sorted((s, g) for _, s, g in e) == [(0, 1)] * 3 + [(1, 0)] * 2,
          f"and WAVE replacing ACD: the table {r.table()}, both engines' voices {sorted((s, g) for _, s, g in e)}")
    r.swap(VAP)                             # WAVE changed to PHASE's model: WAVE wins, PHASE Off
    e = entries(r)
    check(r.menu() == 1 + INDEX[VAP] and ph_menu(r) == 0 and r.table() == [1, 2, WAVE, 5, 6]
          and r.model(0) == WAVE and r.model(1) == ACD and sorted((s, g) for _, s, g in e) == [(0, 0)] * 3,
          f"WAVE set to VAP too (the item changed last): WAVE replaces VAP, PHASE Off (menus {r.menu()}, "
          f"{ph_menu(r)}), the table {r.table()}, slot 0's engine voices WAVE's")
    ph_swap(r, VAP)                         # now PHASE changed: PHASE wins
    e = entries(r)
    check(r.menu() == 0 and ph_menu(r) == 1 + INDEX[VAP] and r.table() == [1, 2, PHASE, 5, 6]
          and r.model(0) == PHASE and sorted((s, g) for _, s, g in e) == [(0, 1)] * 3,
          f"then PHASE set to VAP: PHASE replaces VAP, WAVE Off (menus {r.menu()}, {ph_menu(r)}), slot 0 from WAVE "
          "to PHASE, its engine voices PHASE's")
    ph_swap(r, None)
    r.m.w8(r.st("menu"), 1 + INDEX[FAT])
    ph_swap(r, FAT)                         # both changed at once: WAVE wins
    check(r.menu() == 1 + INDEX[FAT] and ph_menu(r) == 0 and r.table() == [1, WAVE, 4, 5, 6],
          f"both set to FAT in one Config update: WAVE wins, PHASE Off (the table {r.table()})")
    r.swap(None)
    check(r.table() == [1, 2, 4, 5, 6] and entries(r) == [] and r.model(0) == VAP,
          "both Off: the stock table, no engine voices")
    ph_swap(r, ACD)
    r.pick(0, ACD)
    check(r.model(0) == PHASE and r.model(1) == PHASE and len(entries(r)) == 5,
          "PHASE replacing ACD: picking ACD in the Model list gives PHASE")
    r.pick(0, VAP)
    check(r.model(0) == VAP and len(entries(r)) == 2, "and VAP again: VAP, its engine voices freed")
    ph_swap(r, VAP)
    r.swap(ACD)
    r.note_on(0, 60, 100)
    r.render(4)
    heap, e0 = [r.heap_used()], len(entries(r))
    for k in range(201):                    # the heap sampled in one state, the third (as before the loop)
        if k % 3 == 0:
            r.m.w8(r.st("menu"), 1 + INDEX[VAP])
            ph_swap(r, ACD)                 # PHASE changed last onto WAVE's model: PHASE wins next time
        elif k % 3 == 1:
            r.swap(None)
            ph_swap(r, None)
        else:
            r.swap(ACD)
            ph_swap(r, VAP)
        r.note_on(0, 48 + k % 24, 100)
        r.note_on(1, 50 + k % 20, 90)
        r.render(2)
        r.note_off(1, 50 + k % 20)
        if k % 30 == 29:
            heap.append(r.heap_used())
    r.render(4)
    check(ts.no_growth(heap) and len(entries(r)) == e0 and r.model(0) == PHASE and r.model(1) == WAVE,
          f"200 swaps through WAVE, PHASE and Off with notes held: heap use {heap} (no growth), engine voices "
          f"{e0} -> {len(entries(r))}")


# ---- 4. Config menu ---------------------------------------------------------------------------

def menu_checks(ptf: Path) -> None:
    print("Config menu: Project Settings > Synth Engines > PHASE replaces")
    m = Machine(ptf)
    m.stub_function(ts.ATEXIT, lambda mm: 0)
    m.call(ts.MENU_CTOR, max_insns=50_000_000)
    ps = ts.PROJECT_SETTINGS
    cap, count = m.r32(ps + 8) & 0xFF, m.r32(ps + 0x1C) >> 8 & 0xFF
    kids = [m.r32(m.r32(ps + 0xC) + 4 * i) for i in range(min(cap, count))]
    labels = [m.cstr(m.r32(k + (0x18 if m.r32(k) & 0xFF == 1 else 4))) for k in kids]
    if "Synth Engines" not in labels:
        check(False, f"a 'Synth Engines' folder ({labels})")
        return
    folder = kids[labels.index("Synth Engines")]
    items = [m.r32(m.r32(folder + 0xC) + 4 * i) for i in range(2)]
    wave, item = items
    desc = m.r32(item + 0x10)
    addr = ts.WHERE["state"] + ts.WHERE["ph_menu_pos"]
    cb = m.r32(desc + 12)
    opts = [m.cstr(m.r32(m.r32(desc + 8) + 4 * i)) for i in range(m.r32(desc + 4))]
    help_ = m.cstr(m.r32(item + 8))
    check(m.cstr(m.r32(item + 4)) == "PHASE replaces" and m.r32(item + 0xC) & 0xFF == 4 and m.r32(desc) == addr
          and opts == ts.MENU_NAMES and ts.CODE[0] <= cb < ts.CODE[1] and cb & 1 and "PHASE" in help_
          and help_ != m.cstr(m.r32(wave + 8)) and m.uc.mem_read(addr, 1)[0] == 0,
          f"'PHASE replaces': an option item {opts} on PHASE's own menu position, with its own help text "
          f"({help_!r}), starting at Off")
    closed = []
    m.stub_function(ts.MENU_EDITOR_CLOSE, lambda mm: closed.append(1))
    m.stub_function(ts.MENU_REDRAW, lambda mm: 0)
    m.w32(ts.MENU_CURRENT, folder)
    m.w8(folder + 0x1C, 1)
    m.call(ts.MENU_APPLY, 0, 4, max_insns=2_000_000)
    w_addr = ts.WHERE["state"] + ts.WHERE["menu"]
    p_addr = ts.WHERE["state"] + ts.WHERE["ph_menu"]
    check(m.uc.mem_read(addr, 1)[0] == 4 and m.uc.mem_read(p_addr, 1)[0] == 4
          and m.uc.mem_read(w_addr, 1)[0] == 0 and closed,
          "Apply WTFM on the second item: PHASE's position and choice stored, WAVE's untouched")


# ---- 4b. the Config menu's lists, through the stock menu code ---------------------------------

EDITOR_OPEN = 0x601186CC            # (folder): its selected item's editor (as cfg_menu_select opens it)
EDITOR_LIST = 0x600CACE8            # (editor, 2, initial, count, names on the stack): the option list
EDITOR_STUBS = (0x600CB1A0, 0x600CAEF4, 0x600CAF68, 0x600CACBC)     # the editor's set-up and buttons
MENU_FORMAT = 0x6011870C            # (folder): its value texts
WHO = ("WAVE", "PHASE")


class MenuRig:
    """A synth rig whose Config menu the stock constructor built: items opened with the
    stock editor code (the list it is given is captured), chosen by name, applied with the
    stock cfg_menu_apply, then a Config update."""

    def __init__(self, ptf: Path, models=(FAT, VAP, ACD), garbage: bool = False, no_heap: bool = False):
        self.r = r = Rig(ptf)
        m = self.m = r.m
        if garbage:                         # the positions hold garbage at power-on
            m.w8(r.st("menu_pos"), 0xEE)
            m.w8(r.st("ph_menu_pos"), 0xEE)
        m.stub_function(ts.ATEXIT, lambda mm: 0)
        self.refused = []
        if no_heap:                         # no heap for the patch's two descriptors: the const ones
            def small(uc, a, size_, user):
                if m.arg(0) == 16 and ts.CODE[0] <= uc.reg_read(UC_ARM_REG_LR) < ts.CODE[1]:
                    self.refused.append(1)
                    uc.reg_write(UC_ARM_REG_R0, 0)
                    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
            hook = m.uc.hook_add(UC_HOOK_CODE, small, begin=ts.FW_MALLOC, end=ts.FW_MALLOC)
        m.call(ts.MENU_CTOR, max_insns=50_000_000)
        if no_heap:
            m.uc.hook_del(hook)
        self.after_ctor = (r.m.uc.mem_read(r.st("menu_pos"), 1)[0], r.m.uc.mem_read(r.st("ph_menu_pos"), 1)[0])
        ps = ts.PROJECT_SETTINGS
        count = m.r32(ps + 0x1C) >> 8 & 0xFF
        kids = [m.r32(m.r32(ps + 0xC) + 4 * i) for i in range(count)]
        labels = [m.cstr(m.r32(k + (0x18 if m.r32(k) & 0xFF == 1 else 4))) for k in kids]
        self.folder = kids[labels.index("Synth Engines")]
        self.items = [m.r32(m.r32(self.folder + 0xC) + 4 * i) for i in range(2)]
        m.stub_function(ts.MENU_EDITOR_CLOSE, lambda mm: 0)
        m.stub_function(ts.MENU_REDRAW, lambda mm: 0)
        for a in EDITOR_STUBS:
            m.stub_function(a, lambda mm: 0)
        self.shown: list = []

        def editor_list(mm):
            names = mm.arg(4)
            self.shown.append((mm.arg(2), [mm.cstr(mm.r32(names + 4 * i)) for i in range(mm.arg(3))]))
        m.stub_function(EDITOR_LIST, editor_list)
        m.w32(ts.MENU_CURRENT, self.folder)
        self.models = models
        r.configure(models, (1, 1, 1))
        r.pattern()

    def open(self, item: int) -> tuple[int, list[str]]:
        """The editor's initial position and its list."""
        self.m.w8(self.folder + 0x1C, item)
        self.shown.clear()
        self.m.call(EDITOR_OPEN, self.folder, max_insns=2_000_000)
        return self.shown[-1] if self.shown else (-1, [])

    def pick(self, item: int, name: str, update: bool = True) -> bool:
        """Open the item, choose `name` from its list and apply; False if it isn't listed."""
        _, names = self.open(item)
        if name not in names:
            return False
        self.m.call(ts.MENU_APPLY, 0, names.index(name), max_insns=2_000_000)
        if update:
            self.r.config()
        return True

    def texts(self) -> list[str]:
        self.m.call(MENU_FORMAT, self.folder, max_insns=2_000_000)
        return [self.m.cstr(self.m.r32(self.m.r32(self.folder + 0x14) + 4 * i)) for i in range(2)]

    def choices(self) -> list[str]:
        return [ts.MENU_NAMES[self.r.menu()], ts.MENU_NAMES[ph_menu(self.r)]]

    def slots_ok(self) -> tuple[bool, list[str]]:
        """Each synth's title names the model the table gives its record (no engine on a model
        the menu doesn't name)."""
        titles = [self.r.title(i) for i in range(3)]
        table = self.r.table()
        name = {**{md: sr.MODEL_NAMES[md] for md in MODELS}, WAVE: "WAVE", PHASE: "PHASE"}
        want = [f"Synth {i + 1} ({name[table[INDEX[md]]]})" for i, md in enumerate(self.models)]
        return titles == want and self.r.model(0) == table[INDEX[self.models[0]]], titles


def menu_list_checks(ptf: Path) -> None:
    print("the Config menu's lists (the stock menu code): each item leaves out the other's model")
    full = ts.MENU_NAMES
    mr = MenuRig(ptf, garbage=True)
    m, r = mr.m, mr.r
    descs = [m.r32(i + 0x10) for i in mr.items]
    check(mr.after_ctor == (0, 0) and all(not ts.CODE[0] <= d < ts.CODE[1] for d in descs),
          f"the menu constructor: both positions set (from garbage 0xEE: {mr.after_ctor}); the items' "
          "descriptors from the heap, not the patch's const ones")
    check([mr.open(i) for i in (0, 1)] == [(0, full), (0, full)] and mr.texts() == ["Off", "Off"],
          "both Off: each editor lists Off and the five models, at Off")
    ok = mr.pick(0, "FAT", update=False)
    shown = [mr.open(i) for i in (0, 1)]
    check(ok and mr.choices() == ["FAT", "Off"] and mr.texts() == ["FAT", "Off"]
          and shown == [(2, full), (0, ["Off", "ACD", "VAP", "WTFM", "PERC"])],
          f"WAVE replaces FAT (applied; no Config update yet): the choice at once, PHASE's list without FAT "
          f"({shown})")
    check(not mr.pick(1, "FAT"), "PHASE's editor has no FAT to pick")
    ok = mr.pick(1, "PERC")
    shown = [mr.open(i) for i in (0, 1)]
    check(ok and mr.choices() == ["FAT", "PERC"] and mr.texts() == ["FAT", "PERC"]
          and shown == [(2, ["Off", "ACD", "FAT", "VAP", "WTFM"]), (4, ["Off", "ACD", "VAP", "WTFM", "PERC"])],
          f"PHASE replaces PERC (after the left-out FAT: position 4): {mr.choices()}, the lists {shown}")
    ok = mr.pick(1, "ACD")
    check(ok and mr.choices() == ["FAT", "ACD"] and mr.open(1)[0] == 1 and mr.texts() == ["FAT", "ACD"],
          f"PHASE replaces ACD (before FAT: position 1): {mr.choices()}")
    good, titles = mr.slots_ok()
    check(good and r.table() == [PHASE, WAVE, 4, 5, 6] and titles[0] == "Synth 1 (WAVE)"
          and titles[2] == "Synth 3 (PHASE)",
          f"after the Config update: the table {r.table()}, the synths {titles}")
    ok = mr.pick(0, "ACD") if "ACD" in mr.open(0)[1] else None
    check(ok is None and mr.open(0)[1] == ["Off", "FAT", "VAP", "WTFM", "PERC"],
          "WAVE's editor has no ACD (PHASE's) to pick")
    ok = mr.pick(1, "Off") and mr.pick(0, "ACD")
    good, titles = mr.slots_ok()
    check(ok and mr.choices() == ["ACD", "Off"] and mr.open(1)[1] == ["Off", "FAT", "VAP", "WTFM", "PERC"]
          and good and r.table() == [WAVE, 2, 4, 5, 6],
          f"PHASE Off, then WAVE to ACD: {mr.choices()}, PHASE's list without ACD, the synths {titles}")

    # the user's report: both on one model, then back to the instruments
    seq = [(0, "FAT"), (1, "VAP"), (1, "Off"), (0, "Off"), (1, "FAT"), (0, "VAP"), (0, "FAT")]
    bad = []
    for item, name in seq:
        listed = mr.pick(item, name)
        good, titles = mr.slots_ok()
        c = mr.choices()
        if not good or (c[0] != "Off" and c[0] == c[1]) or mr.texts() != c:
            bad.append((WHO[item], name, listed, c, titles))
    check(not bad, f"a sequence of picks ({seq}): the two never on one model, the menu shows the choices, and "
                   f"every synth plays the model its record and the table give (wrong: {bad})")

    rnd = np.random.default_rng(5)
    bad = []
    for k in range(150):
        item = int(rnd.integers(0, 2))
        _, names = mr.open(item)
        name = names[int(rnd.integers(0, len(names)))]
        mr.pick(item, name, update=bool(k % 3))
        c = mr.choices()
        lists = [mr.open(i)[1] for i in (0, 1)]
        want = [[n for n in full if n == "Off" or n != c[1 - i]] for i in (0, 1)]
        if (c[0] != "Off" and c[0] == c[1]) or mr.texts() != c or lists != want \
                or [mr.open(i)[0] for i in (0, 1)] != [lists[i].index(c[i]) for i in (0, 1)]:
            bad.append((k, WHO[item], name, c, lists))
        if k % 3 and not mr.slots_ok()[0]:
            bad.append((k, "synths", mr.slots_ok()[1]))
    check(not bad, f"150 random picks from the lists shown (a Config update after two in three): the lists, "
                   f"positions, texts and synths always agree (wrong: {bad[:3]})")

    r.m.w8(r.st("menu"), 1 + INDEX[VAP])    # choices set outside the menu (synths.bin, a project load)
    ph_swap(r, PERC, apply=False)
    r.pattern()
    check(mr.texts() == ["VAP", "PERC"] and mr.open(0) == (3, ["Off", "ACD", "FAT", "VAP", "WTFM"])
          and mr.open(1) == (4, ["Off", "ACD", "FAT", "WTFM", "PERC"]),
          f"choices set outside the menu: the next update shows them, each list without the other's "
          f"({mr.texts()})")
    r.m.w8(r.st("menu"), 1 + INDEX[FAT])
    ph_swap(r, FAT, apply=False)
    r.config()
    c = mr.choices()
    check(c == ["FAT", "Off"] and mr.texts() == c and mr.open(1)[1] == ["Off", "ACD", "VAP", "WTFM", "PERC"],
          f"both set to FAT outside the menu: the conflict rule (WAVE wins), then the lists ({c})")

    mr = MenuRig(ptf, no_heap=True)
    m, r = mr.m, mr.r
    consts = [m.r32(i + 0x10) for i in mr.items]
    ok = mr.pick(0, "VAP") and mr.pick(1, "VAP")
    check(len(mr.refused) == 2 and all(ts.CODE[0] <= d < ts.CODE[1] for d in consts)
          and mr.open(0)[1] == full and mr.open(1)[1] == full and ok and mr.choices() == ["Off", "VAP"]
          and mr.texts() == ["Off", "VAP"] and mr.slots_ok()[0],
          f"no heap for the descriptors: the const ones, full lists, and the conflict rule (PHASE changed "
          f"last: {mr.choices()})")


# ---- 5. the Model list, the logo label, the tiles ---------------------------------------------

def model_list_checks(ptf: Path) -> None:
    print("the Model list (synth settings screen)")
    r = Rig(ptf)
    m = r.m
    r.configure((VAP, ACD, PERC), (1, 1, 1))
    r.pattern()
    ts.screen_globals(r)
    stock_verts, _ = ts.list_draw(r, ts.STOCK_LIST_IDS)
    ids = [9, 10, 11, 12, 24]
    for md in MODELS:
        ph_swap(r, md)
        m.call(ts.SCREEN_SETUP, ts.SCREEN, max_insns=50_000_000)
        want = [LIST_TEXT_PHASE if k == INDEX[md] else i for k, i in enumerate(ids)]
        verts, texts = ts.list_draw(r, m.r32(ts.SCREEN + 0x114))
        check(m.r32(ts.SCREEN + 0x114) == r.st("model_ids") and r.model_ids() == want and len(verts) == 4
              and [v for k, v in enumerate(stock_verts) if k != INDEX[md]] == verts
              and [t[2] for t in texts] == ["PHASE"],
              f"PHASE replacing {sr.MODEL_NAMES[md]}: the ids {r.model_ids()}, 4 logos as stock, the text {texts}")
    ph_swap(r, PERC)
    r.swap(FAT)
    m.call(ts.SCREEN_SETUP, ts.SCREEN, max_insns=50_000_000)
    verts, texts = ts.list_draw(r, m.r32(ts.SCREEN + 0x114))
    check(r.model_ids() == [9, ts.LIST_TEXT_ID, 11, 12, LIST_TEXT_PHASE] and len(verts) == 3
          and [t[2] for t in texts] == ["WAVE", "PHASE"] and texts[0][1] < texts[1][1],
          f"WAVE replacing FAT and PHASE replacing PERC: 3 logos, 'WAVE' in FAT's row and 'PHASE' in PERC's ({texts})")
    verts, texts = ts.list_draw(r, r.st("model_ids"), scroll=2, rows=3)
    check(len(verts) == 2 and [t[2] for t in texts] == ["PHASE"],
          f"scrolled by 2 with 3 rows (VAP, WTFM, PERC shown): 2 logos and 'PHASE' in the last row ({texts})")
    r.swap(None)
    ph_swap(r, None)
    m.call(ts.SCREEN_SETUP, ts.SCREEN, max_insns=50_000_000)
    check(m.r32(ts.SCREEN + 0x114) == ts.STOCK_LIST_IDS, "both Off: the stock ids")


def label_checks(ptf: Path) -> None:
    print("the Edit Patch screen's model logo")
    r = Rig(ptf)
    m = r.m
    r.configure((VAP, ACD, PERC), (1, 1, 1))
    r.pattern()
    ts.screen_globals(r)
    mgr = 0x20027D64
    image = bytes(m.uc.mem_read(0x60000000, 0x800000))
    hook = bl_target(image, 0x60000000, 0x6011EA58)
    params = m.alloc(0x40)
    m.write(params, struct.pack("<5I", 0x60038660, 0, 0, 0x100, m.r32(m.r32(mgr) + 8) + 0xC4 if m.r32(mgr) else 0))
    cid = m.call(0x600CB64C, mgr, params, max_insns=2_000_000) & 0xFF
    icons = m.alloc(0x20)

    def draw():
        m.write(icons, struct.pack("<hhBBhhBB", 116, 49, 13, 0, 0, 0, 0, 0))
        m.call(0x60101DF8, mgr, cid, icons)
        m.call(0x60101CAC, mgr, cid, 0x20)
        m.call(hook | 1, mgr, cid, 0x60038660, max_insns=2_000_000)
        m.call(0x60101C6C, mgr, cid, 0x32, 0x2D)
        m.call(0x60101C8C, mgr, cid, 0x6C, 0x28)
        ctrl = m.r32(mgr + 0x10 + 4 * cid)
        m.display_list.clear()
        m.call(m.r32(m.r32(ctrl)), ctrl, max_insns=5_000_000)
        dl = list(m.display_list)
        texts = []
        for k, w_ in enumerate(dl):
            if w_ == 0xFFFFFF0C:
                raw = b"".join(struct.pack("<I", x) for x in dl[k + 3:k + 8])
                texts.append(raw.split(b"\0")[0].decode("latin-1"))
        return m.uc.mem_read(icons + 4, 1)[0], sum(1 for w_ in dl if w_ == 0x1F000001), texts
    ph_swap(r, VAP)
    got = draw()
    check(got[0] == 0 and got[1] == 0 and "PHASE" in got[2] and "WAVE" not in got[2],
          f"S1 on PHASE: no logo (icon {got[0]}, {got[1]} bitmaps) and the text {got[2]}")
    m.w8(SELECTED, 65)
    got = draw()
    check(got[0] == 13 and got[1] >= 1 and "PHASE" not in got[2], f"S2 (ACD) selected: its logo, no text ({got})")


def tiles(r: Rig, fn: int) -> tuple[list[str], list[int]]:
    """The Edit Patch tiles' labels and icon kinds, from `fn` (the hooked call or the stock
    builder) on a fake screen object, for the selected synth."""
    m = r.m
    scr = m.alloc(0x460)
    m.write(scr, bytes(0x460))
    m.write(scr + 0x120, bytes([0xFF]))     # no control
    m.call(fn | 1, scr, 1, max_insns=20_000_000)
    n = m.uc.mem_read(scr + 0x450, 1)[0]
    ptrs = m.r32(scr + 0x448)
    return ([m.cstr(m.r32(ptrs + 4 * i)) for i in range(n)],
            list(m.uc.mem_read(scr + 0x42C, n)))


def tile_checks(ptf: Path) -> None:
    print("the Edit Patch screen's tiles (labels and icons)")
    r = Rig(ptf)
    m = r.m
    r.configure((VAP, VAP, PERC), (1, 1, 1))
    r.pattern()
    m.w32(0x200209E4, sr.S)
    m.w8(SELECTED, 64)
    image = bytes(m.uc.mem_read(0x60000000, 0x800000))
    hook = bl_target(image, 0x60000000, BL_TILES)
    vap_labels, vap_kinds = tiles(r, hook)
    check(vap_labels == STOCK_TILES and (vap_labels, vap_kinds) == tiles(r, TILE_BUILDER),
          f"S1 on VAP: the stock tiles {vap_labels}, kinds {[hex(k) for k in vap_kinds]}, as the stock builder's")
    r.swap(VAP)
    wave = tiles(r, hook)
    check(wave == tiles(r, TILE_BUILDER), f"S1 on WAVE: as the stock builder gives them ({wave[0]})")
    r.swap(None)
    ph_swap(r, VAP)
    raw = tiles(r, TILE_BUILDER)
    got = tiles(r, hook)
    want_kinds = [0x17, 0x12, 0x11, 0x11, 0x11] + vap_kinds[5:]
    check(got == (PHASE_TILES, want_kinds) and got[1][:5] == vap_kinds[:5],
          f"S1 on PHASE: {got[0]}, kinds {[hex(k) for k in got[1]]} (the stock tiles' icons in each place; the stock "
          f"builder alone gives {raw[0][:5]}, kinds {[hex(k) for k in raw[1][:5]]})")
    check(all(len(t) <= 7 for t in got[0]), "every label fits its 8-byte buffer")
    m.w8(SELECTED, 66)
    check(tiles(r, hook) == tiles(r, TILE_BUILDER) and tiles(r, hook)[0][0] != "DCO",
          f"S3 (PERC) selected while S1 is PHASE: PERC's tiles, stock ({tiles(r, hook)[0]})")


# ---- 6. synths.bin --------------------------------------------------------------------------

def hsy2(wave: int, phase: int, b6: int = 0) -> bytes:
    data = bytes([wave, phase, b6, 0])
    return ts.FILE_MAGIC + data + struct.pack("<I", ts.fnv1a(data))


def file_checks(ptf: Path) -> None:
    print("settings file (synths.bin): byte 5, PHASE's choice")
    r = Rig(ptf)
    m = r.m
    fs = FakeFS(m, Path("."))
    r.configure((VAP, ACD, PERC), (1, 1, 1))
    applied_with = []
    m.stub_function(ts.APPLY_RECORDS, lambda mm: applied_with.append(r.table()))
    image = bytes(m.uc.mem_read(0x60000000, 0x800000))
    project_hook = bl_target(image, 0x60000000, ts.BL_RECORDS)

    def load(name):
        fs.set_project(Path("."), name, "Projects/User")
        applied_with.clear()
        m.call(project_hook | 1, sr.S, ts.RECORDS, 1, max_insns=50_000_000)

    def tick(n=1, slot=ts.CONFIG_UPDATE_SLOT):
        for _ in range(n):
            m.call(m.r32(slot), 0x20021770, max_insns=200_000_000)

    def path(name):
        return f"Projects/User/{name}/synths.bin"

    def writes():
        return len([x for x in fs.log if "synths.bin mode 3" in x])

    load("A")
    check(r.menu() == 0 and ph_menu(r) == 0 and applied_with == [[1, 2, 4, 5, 6]], "no file: both Off")
    ph_swap(r, VAP, apply=False)
    tick(ts.SAVE_DELAY + 5)
    check(writes() == 1 and fs.overlay.get(path("A")) == hsy2(0, 3) and r.model(0) == PHASE,
          f"PHASE replacing VAP: applied, and written once a while later (bytes 4-7 "
          f"{list(fs.overlay.get(path('A'), bytes(8))[4:8])})")
    r.swap(ACD, apply=False)
    tick(ts.SAVE_DELAY + 5)
    check(writes() == 2 and fs.overlay.get(path("A")) == hsy2(1, 3), "and WAVE replacing ACD: both in the file")
    tick(ts.SAVE_DELAY * 2)
    check(writes() == 2, "no rewrite while nothing changes")
    load("B")
    check(r.menu() == 0 and ph_menu(r) == 0 and applied_with[0] == [1, 2, 4, 5, 6], "another project: both Off")
    load("A")
    check(r.menu() == 1 and ph_menu(r) == 3 and applied_with[0] == [WAVE, 2, PHASE, 5, 6],
          f"back to the first: both, in the table before its records are applied ({applied_with[0]})")
    fs.overlay[path("S")] = hsy2(3, 3)
    n = writes()
    load("S")
    tick(ts.SAVE_DELAY * 2)
    check(r.menu() == 3 and ph_menu(r) == 0 and applied_with[0] == [1, 2, WAVE, 5, 6] and writes() == n
          and fs.overlay[path("S")] == hsy2(3, 3),
          "one model in both bytes: WAVE replaces it, PHASE Off; the file kept")
    for label, data in (("byte 5 out of range", hsy2(2, 6)), ("byte 6 set", hsy2(2, 1, 1))):
        fs.overlay[path("D")] = data
        n = writes()
        load("D")
        tick(ts.SAVE_DELAY * 2)
        check(r.menu() == 0 and ph_menu(r) == 0 and writes() == n and fs.overlay[path("D")] == data,
              f"{label}: both Off, and the file is kept")
    fs.overlay[path("E")] = ts.hsy2(2)
    load("E")
    check(r.menu() == 2 and ph_menu(r) == 0 and applied_with[0] == [1, WAVE, 4, 5, 6],
          "an sy7 file (WAVE replacing FAT): loads as before, PHASE Off")
    ph_swap(r, PERC, apply=False)
    fs.set_project(Path("."), "H", "Projects/User")        # Save As
    tick(ts.SAVE_DELAY + 5)
    check(fs.overlay.get(path("H")) == hsy2(2, 5) and r.model(2) == PHASE,
          "Save As keeps both choices and writes them to the new folder")


# ---- 7. patch files -----------------------------------------------------------------------------

def patch_file_checks(ptf: Path, stock: Path) -> None:
    print("patch files (protocol buffers: field 1 is the model id)")
    r = phase_rig(ptf, boot=False)
    m = r.m
    r.set_param(0, P_DCW1, 0.25)
    r.set_param(0, P_WAVE1, 3.0)
    r.set_param(0, P_PENV_AMT, -12.0)
    r.set_param(0, P_LFO2_POL, 1.0)
    r.set_param(0, 62, 1.0)
    pfs = ts.PatchFS(r)
    w = m.r32(sr.S + 0x9C)
    ok = m.call(m.r32(m.r32(w) + 0x10), w, sr.S + 0x14, r.patch_obj(0), max_insns=50_000_000)
    data = pfs.files.get("PHASE/Init.patch", b"")
    saved = ts.pb_values(data)
    check(ok == 1 and data[:2] == bytes([0x08, PHASE]) and len(saved) == 66 and saved[P_DCW1] == 0.25
          and saved[P_PENV_AMT] == -12.0 and saved[60:63] == [0.0, 1.0, 1.0],
          f"Save: PHASE/Init.patch, {len(data)} bytes, field 1 = {data[1] if len(data) > 1 else None}, "
          f"{len(saved)} values")
    r2 = phase_rig(ptf, boot=False)
    pfs2 = ts.PatchFS(r2)
    pfs2.files["0.patch"] = data
    out = r2.m.alloc(0x40)
    got = r2.m.call(ts.SLOT_LOAD, sr.S, 0, out, max_insns=50_000_000)
    vals = r2.params(0)
    check(got == 1 and r2.patch_model(0) == PHASE and vals[P_DCW1] == 0.25 and vals[P_WAVE1] == 3.0
          and vals[P_PENV_AMT] == -12.0 and vals[60:63] == [0.0, 1.0, 1.0] and r2.param_copy(0)[40] == -12.0,
          f"a PHASE slot loads it back (DCW 1 {vals[P_DCW1]}, Wave 1 {SHAPES[int(vals[P_WAVE1])]}, DCO Env Amt "
          f"{vals[P_PENV_AMT]}, the own six {vals[60:]}; the engine's copy too)")
    r2.set_param(0, 60, 1.0)
    st = r2.m.r32(sr.S + 0xA4)
    path = r2.m.alloc(0x18)
    r2.m.write(path, struct.pack("<II", path + 8, 12) + b"nofile.patch\0\0\0\0")
    got = r2.m.call(r2.m.r32(r2.m.r32(st) + 0x0C), st, path, r2.patch_obj(0), 1, max_insns=50_000_000)
    check(got == 0 and r2.params(0)[60:63] == [1.0, 1.0, 1.0],
          f"a load that fails: the own six as they were ({r2.params(0)[60:]})")
    s = Rig(stock, patched=False)
    sfs = ts.PatchFS(s)
    s.configure((ACD, PERC, PERC), (1, 1, 1))
    sfs.files["0.patch"] = data
    got = s.m.call(ts.SLOT_LOAD, sr.S, 0, s.m.alloc(0x40), max_insns=50_000_000)
    s.note_on(0, 60, 100)
    y = s.render(4, fast=True)[0][0]
    check(got == 0 and s.model(0) == ACD and s.patch_model(0) == ACD and int(np.abs(y).max()) > 1000,
          f"stock firmware, the same file on an ACD slot: rejected, ACD's Init patch plays (peak {int(np.abs(y).max())})")


# ---- 8. the engine ------------------------------------------------------------------------------

RATE, BLOCK = ts.RATE, sr.BLOCK
PI = dict(wave1b=0, wave2b=1, wave1=2, wave2=3, mix=4, tune1=5, tune2=6, detune=7, fine=8, reset1=9, reset2=10,
          ring=11, dcw1=12, dcw2=13, envamt1=14, penv_att=15, penv_dec=16, penv_sus=17, penv_rel=18, dca_att=19,
          dca_dec=20, dca_sus=21, dca_rel=22, wenv_att=23, wenv_dec=24, wenv_sus=25, wenv_rel=26, lfo1_rate=27,
          lfo1_wave=28, lfo1_retrig=29, lfo1_sync=30, lfo2_rate=31, lfo2_wave=32, lfo2_retrig=33, lfo2_sync=34,
          glide_mode=35, glide_time=36, keyfollow=37, envamt2=38, noisemod=40, penv_amt=41, lfo1_pol=60, lfo2_pol=61,
          effect=62, fx_rate=63, fx_depth=64, fx_mix=65)
PI |= {f"{k}{s + 1}": 42 + 3 * s + j for s in range(6) for j, k in enumerate(("src", "dest", "amt"))}
S_DCA, S_DCW, S_DCO, S_LFO1, S_LFO2, S_VEL, S_KEY = range(7)                         # Src
D_PITCH, D_PITCH1, D_PITCH2, D_DCW, D_DCW1, D_DCW2, D_MIX, D_VOLUME, D_DETUNE = range(9)    # Dest
SAW, SQUARE, PULSE, DSINE, SAWPULSE, RESO1, RESO2, RESO3 = range(8)                 # Wave 1 / 2 (Wave k+: + 1)
# DCO 1 alone (Mix -1), the DCA held at 1 from the first block, Release 0, no envelope on the
# DCW, no detune, Reset on: each note starts both DCOs at phase 0, so the output can be
# worked out sample for sample (dco_sim)
CLEAN = dict(dca_att=0.0, dca_sus=1.0, dca_rel=0.0, envamt1=0.0, envamt2=0.0, detune=0.0, reset1=1.0, reset2=1.0,
             mix=-1.0)
GAIN = 0.25                         # ph_engine.h's PH_LEVEL_GAIN
KNEE, R_MAX, R_CAP, DS_EASE = 4.0, 24.0, 0.25, 0.0085    # ... PH_KNEE, PH_R_MAX, PH_R_CAP, PH_DS_EASE
PV_PHASE = 56                       # struct ph_voice: the DCOs' phases
ENV_DT = BLOCK / RATE
note_hz, zc_freq, cents = ts.note_hz, ts.zc_freq, ts.cents


def clampf(x: float, lo: float, hi: float) -> float:
    return lo if not x >= lo else min(x, hi)


def ref_shape(shape: int, d: float, hz: float) -> tuple:
    """A shape at DCW d for a pitch of hz, in float64 from docs/PD_SYNTH_PLAN.md 3.1-3.3 (as
    ph_engine.h's shape_setup): its segments [(x0, W at x0, slope)], resonant or not, r."""
    kmin = clampf(KNEE * hz / RATE, 0.0, 0.5)
    r = 1.0
    if shape == SAW:
        k = clampf(0.5 * (1 - d), kmin, 0.5)
        segs = [(0, 0, 0.5 / k), (k, 0.5, 0.5 / (1 - k))]
    elif shape == SQUARE:
        k = clampf(0.5 * (1 - d), kmin, 0.5)
        segs = [(0, 0, 0.5 / k), (k, 0.5, 0), (0.5, 0.5, 0.5 / k), (0.5 + k, 1, 0)]
    elif shape == PULSE:
        k = clampf(1 - 0.9 * d, 2 * kmin, 1)
        segs = [(0, 0, 1 / k), (k, 1, 0)]
    elif shape == DSINE:
        a = min(0.6 * d, DS_EASE / kmin ** 2) if kmin else 0.6 * d
        segs = [(0, 0, 1 + a), (0.5, 0.5 - a / 2, 1 + a)]
    elif shape == SAWPULSE:
        e = clampf(1 - 0.5 * d, min(3 * kmin, 1.0), 1.0)
        k = clampf(0.5 * e * (1 - d), min(kmin, 0.5 * e), 0.5 * e)
        segs = [(0, 0, 0.5 / k), (k, 0.5, 0.5 / (e - k)), (e, 1, 0)]
    else:
        r = clampf(R_MAX ** d, 1.0, clampf(R_CAP * RATE / hz, 1.0, R_MAX))
        segs = {RESO1: [(0, 1, -1)], RESO2: [(0, 0, 2), (0.5, 1, -2)], RESO3: [(0, 1, 0), (0.5, 1, -2)]}[shape]
    return segs, shape >= RESO1, r


def ref_eval(sh: tuple, p: np.ndarray) -> np.ndarray:
    segs, reso, r = sh
    x0 = np.array([s[0] for s in segs], float)
    i = np.searchsorted(x0, p, side="right") - 1
    w = np.array([s[1] for s in segs], float)[i] + np.array([s[2] for s in segs], float)[i] * (p - x0[i])
    return w * (1 - np.cos(2 * np.pi * r * p)) - 1 if reso else np.cos(2 * np.pi * w)


def ref_mean(sh: tuple) -> float:
    return float(ref_eval(sh, (np.arange(1 << 16) + 0.5) / (1 << 16)).mean())


def ref_harmonics(sh: tuple, count: int) -> np.ndarray:
    """Harmonics 1..count of one cycle (amplitudes)."""
    n = 1 << 16
    return np.abs(np.fft.rfft(ref_eval(sh, np.arange(n) / n)))[1:count + 1] * 2 / n


def dco_sim(inc: int, shapes: list, start: int, n: int) -> np.ndarray:
    """One DCO as ph_engine.h plays it from phase 0 at the note on (Reset): samples start + 1
    .. start + n after it, with a phase step of inc; `shapes` one shape or a pair (the first
    from the note on, then alternate cycles); its mean taken off."""
    t = inc * np.arange(start + 1, start + n + 1, dtype=np.int64)
    p = ((t & 0xFFFFFFFF) >> 8) / 16777216.0
    if len(shapes) == 1:
        return ref_eval(shapes[0], p) - ref_mean(shapes[0])
    y = np.where((t >> 32) & 1, ref_eval(shapes[1], p), ref_eval(shapes[0], p))
    return y - 0.5 * (ref_mean(shapes[0]) + ref_mean(shapes[1]))


def harm_amps(y: np.ndarray, f0: float, count: int, first: float = 1.0) -> np.ndarray:
    """Amplitudes at first x f0, (first + 1) x f0, ... (count of them): Blackman-Harris
    windowed projections, so a harmonic between two FFT bins isn't read low."""
    n = np.arange(len(y))
    w = (0.35875 - 0.48829 * np.cos(2 * np.pi * n / (len(y) - 1)) + 0.14128 * np.cos(4 * np.pi * n / (len(y) - 1))
         - 0.01168 * np.cos(6 * np.pi * n / (len(y) - 1)))
    yw = y * w
    return np.array([abs(np.dot(yw, np.exp(-2j * np.pi * (first + k) * f0 * n / RATE))) * 2 / w.sum()
                     for k in range(count)])


def db(x: np.ndarray) -> np.ndarray:
    return 20 * np.log10(np.maximum(x, 1e-12))


def incoherent(y: np.ndarray, f0: float, cycles: int = 8) -> float:
    """The share of y's power not in its harmonics (up to 20, below 22,050 Hz) as steady
    partials over `cycles` cycles at a time (each through a Blackman-Harris window),
    averaged over y's stretches of that length. Over a set number of cycles it reads a
    pitch spread that is a set share of the pitch alike at any note."""
    seg = int(cycles * RATE / f0)
    n = np.arange(seg)
    w = (0.35875 - 0.48829 * np.cos(2 * np.pi * n / (seg - 1)) + 0.14128 * np.cos(4 * np.pi * n / (seg - 1))
         - 0.01168 * np.cos(6 * np.pi * n / (seg - 1)))
    count = min(20, int(RATE / 2 / f0))
    out = []
    for i in range(0, len(y) - seg + 1, seg):
        x = y[i:i + seg]
        total = float(np.sum((x * w) ** 2) / np.sum(w ** 2))
        out.append(max(0.0, 1 - float((harm_amps(x, f0, count) ** 2).sum() / 2) / total))
    return float(np.mean(out))


class PH:
    """A PHASE slot 0 (PHASE replacing VAP, `poly` voices; the other slots idle PERC), booted;
    its first voice rendered alone through its render method. Parameters: the dicts given
    (CLEAN, say), then the named ones."""

    def __init__(self, ptf: Path, *bases: dict, poly: int = 1, **params):
        self.r = phase_rig(ptf, polys=(poly, 1, 1))
        self.t = 0
        self.set(**{k: v for b in bases + (params,) for k, v in b.items()})

    def set(self, **params) -> None:
        for k, v in params.items():
            self.r.set_param(0, PI[k], float(v))

    @property
    def voice(self) -> int:
        return self.r.voices(0)[0]

    def on(self, note: int, velocity: int = 127) -> None:
        self.r.note_on(0, note, velocity)
        self.t = 0                          # samples since the note on

    def run(self, blocks: int) -> np.ndarray:
        self.t += blocks * BLOCK
        return self.r.render_voice(self.voice, blocks)

    def off(self, note: int, blocks: int = 0) -> np.ndarray:
        self.r.note_off(0, note)
        return self.run(blocks) if blocks else np.zeros(0)

    def phases(self) -> tuple[int, int]:
        e = self.r.engine_voice(self.voice)
        return struct.unpack("<2I", bytes(self.r.m.uc.mem_read(e + PV_PHASE, 8)))

    def steps(self, hz: tuple[float, float]) -> list[int]:
        """Each DCO's phase step, from its phase across one block: 128 steps, mod 2^32, so
        the step is known up to 2^25 (345 Hz); `hz`, each DCO's pitch roughly, picks it."""
        a = self.phases()
        self.run(1)
        b = self.phases()
        out = []
        for x, y, f in zip(a, b, hz):
            low = ((y - x) & 0xFFFFFFFF) // BLOCK
            out.append(min((low + j * (1 << 25) for j in range(128)), key=lambda c: abs(c - f * 2 ** 32 / RATE)))
        return out

    def held(self, note: int, blocks: int, hz: tuple | None = None, velocity: int = 127):
        """A note on, 2 blocks dropped, 1 to measure the steps (steps()), then `blocks` blocks:
        (the output, the steps, the samples since the note on where it starts)."""
        self.on(note, velocity)
        self.run(2)
        inc = self.steps(hz or (note_hz(note),) * 2)
        start = self.t
        return self.run(blocks), inc, start


def f0_of(inc: int) -> float:
    return inc * RATE / 2 ** 32


def sim_err(y: np.ndarray, inc: int, shapes: list, start: int, gain: float = GAIN) -> float:
    return float(np.abs(y - gain * dco_sim(inc, shapes, start, len(y))).max())


def shape_checks(ptf: Path) -> None:
    print("the shapes (DCO 1 alone, from phase 0, worked out sample for sample in float64)")
    w = PH(ptf, CLEAN)
    errs, pure, refs, worst_ref = [], [], [], (0.0, "")
    for shape in range(8):
        for d in (0.0, 0.5, 1.0):
            w.set(wave1=shape, dcw1=d)
            y, inc, start = w.held(45, 64)
            w.off(45, 1)
            f0 = f0_of(inc[0])
            sh = ref_shape(shape, d, f0)
            errs.append((sim_err(y, inc[0], [sh], start), SHAPES[shape], d))
            got = harm_amps(y, f0, 40)
            if d == 0.0 and shape < RESO1:
                pure.append((float(db(got[1:10] / got[0]).max()), SHAPES[shape]))
            if d == 1.0 or shape >= RESO1:
                want = ref_harmonics(sh, 40)
                top, wtop = got.max(), want.max()
                keep = db(want / wtop) > -50
                diff = float(np.abs(db(got[keep] / top) - db(want[keep] / wtop)).max())
                refs.append(diff)
                if diff >= worst_ref[0]:
                    worst_ref = (diff, f"{SHAPES[shape]} at DCW {d:.0%}")
        for n in (84, 96, 108):
            w.set(wave1=shape, dcw1=1.0)
            y, inc, start = w.held(n, 16)
            w.off(n, 1)
            errs.append((sim_err(y, inc[0], [ref_shape(shape, 1.0, f0_of(inc[0]))], start), SHAPES[shape], n))
    worst = max(errs)
    check(worst[0] < 5e-4, f"every shape at DCW 0, 50 and 100 % (note 45) and at 100 % at notes 84, 96, 108 (the knee "
                           f"and resonance limits): the formula's output, the mean taken off, x {GAIN}, within "
                           f"{worst[0]:.1e} ({worst[1]}, {worst[2]}; under 5e-4)")
    wp = max(pure)
    check(wp[0] < -90, f"DCW 0: shapes 1-5 are a pure sine (harmonics 2-10 at most {wp[0]:.1f} dB, {wp[1]}; under -90)")
    check(worst_ref[0] < 0.1, f"DCW 100 % (and 0 and 50 % for the resonant shapes, the windowed sine at 0): "
                              f"harmonics 1-40 within 50 dB of the strongest match the float64 formula's within "
                              f"{worst_ref[0]:.3f} dB ({worst_ref[1]}; under 0.1)")
    # brightness with DCW
    rising, peaks = [], {}
    for shape in range(8):
        cs, pk = [], []
        for d in np.linspace(0.0, 1.0, 11):
            w.set(wave1=shape, dcw1=float(d))
            y, inc, _ = w.held(45, 32)
            w.off(45, 1)
            cs.append(ts.centroid(y))
            pk.append(int(np.argmax(harm_amps(y, f0_of(inc[0]), 40))) + 1)
        if shape < RESO1:
            rising.append((min(b / a for a, b in zip(cs, cs[1:])), SHAPES[shape], cs[0], cs[-1]))
        else:
            peaks[SHAPES[shape]] = pk
    wr = min(rising)
    check(wr[0] >= 0.999, "DCW 0 to 100 % in steps of 10 %: shapes 1-5 never get duller (spectral centroid; " +
          ", ".join(f"{s} {a:.0f} to {b:.0f} Hz" for _, s, a, b in rising) + f"; the smallest step {wr[0]:.4f})")
    check(all(all(b >= a for a, b in zip(pk, pk[1:])) and pk[0] <= 2 and pk[-1] >= 20 for pk in peaks.values()),
          "the resonant shapes: the strongest harmonic moves up with DCW (r = 24^DCW): " +
          "; ".join(f"{s} {pk}" for s, pk in peaks.items()))


# the 3.3 table's resonant limits (r f <= 0.3 rate there; 0.25 here), anything under -60 dB passing
RESO_ALIAS = {RESO1: (-64.0, -43.7, -35.0, -29.5, -27.5), RESO2: (-90.4, -62.4, -48.4, -38.0, -26.9),
              RESO3: (-67.5, -47.2, -39.5, -31.4, -28.8)}


def alias_checks(ptf: Path) -> None:
    print("aliasing (DCW 100 %, notes 60-108)")
    w = PH(ptf, CLEAN, dcw1=1.0)
    plain, reso = [], []
    for shape in range(8):
        got = []
        for n in (60, 72, 84, 96, 108):
            w.set(wave1=shape)
            y, inc, _ = w.held(n, 64)
            w.off(n, 1)
            got.append(ts.worst_alias_db(y, f0_of(inc[0])))
        if shape < RESO1:
            plain.append((max(got), SHAPES[shape], got))
        else:
            lim = [max(x + 1.0, -60.0) for x in RESO_ALIAS[shape]]
            reso.append((all(a < b for a, b in zip(got, lim)), SHAPES[shape], got, lim))
    wp = max(plain)
    check(wp[0] < -45, f"shapes 1-5: nothing above -45 dB that isn't a harmonic (worst {wp[0]:.1f} dB, {wp[1]}): " +
          "; ".join(f"{s} " + "/".join(f"{x:.0f}" for x in g) for _, s, g in plain))
    check(all(ok for ok, *_ in reso), "the resonant shapes under the plan's limits (notes 60/72/84/96/108; the limit "
          "+1 dB, or -60): " + "; ".join(f"{s} " + "/".join(f"{x:.0f}" for x in g) + " (limits " +
                                         "/".join(f"{x:.0f}" for x in lim) + ")" for _, s, g, lim in reso))


def continuity_checks(ptf: Path) -> None:
    print("continuity (no clicks at the cycle's end or the double sine's jump)")
    w = PH(ptf, CLEAN)
    worst = (0.0, "")
    for shape in (DSINE, RESO1, RESO2, RESO3):
        for n in (45, 72):
            for d in (0.0, 0.25, 0.5, 0.75, 1.0):
                w.set(wave1=shape, dcw1=d)
                y, inc, _ = w.held(n, 16)
                w.off(n, 1)
                sh = ref_shape(shape, d, f0_of(inc[0]))
                dense = ref_eval(sh, np.arange(1 << 18) / (1 << 18))
                slope = np.abs(np.diff(np.append(dense, dense[0]))).max() * (1 << 18)    # per cycle, the wrap too
                ratio = float(np.abs(np.diff(y)).max() / (GAIN * slope * inc[0] / 2 ** 32))
                if ratio >= worst[0]:
                    worst = (ratio, f"{SHAPES[shape]}, note {n}, DCW {d:.0%}")
    check(worst[0] < 1.05, f"Double Sine and Reso I-III at DCW 0-100 %, notes 45 and 72: the largest step between "
                           f"samples is the formula's steepest slope's x {worst[0]:.3f} at most ({worst[1]}; under "
                           "1.05: no jump anywhere in the cycle)")


def pair_checks(ptf: Path) -> None:
    print("pairs (Wave 1+: a second shape on alternate cycles)")
    w = PH(ptf, CLEAN, dcw1=1.0)
    got = {}
    for label, a, b in (("Saw + Square", SAW, SQUARE), ("Pulse + Double Sine", PULSE, DSINE),
                        ("Saw + Reso I", SAW, RESO1), ("Reso II + Saw-Pulse", RESO2, SAWPULSE)):
        w.set(wave1=a, wave1b=b + 1)
        y, inc, start = w.held(45, 64)
        w.off(45, 1)
        f0 = f0_of(inc[0])
        err = sim_err(y, inc[0], [ref_shape(a, 1.0, f0), ref_shape(b, 1.0, f0)], start)
        h = harm_amps(y, f0, 8, first=0.5)          # 0.5, 1.5, ... x f0: the sub-octave's odd partials
        full = harm_amps(y, f0, 8)
        got[label] = (err, float(db(h.max() / max(full.max(), h.max()))))
    w.set(wave1=SAW, wave1b=0)
    y, inc, _ = w.held(45, 64)
    w.off(45, 1)
    alone = float(db(harm_amps(y, f0_of(inc[0]), 8, first=0.5).max() / harm_amps(y, f0_of(inc[0]), 8).max()))
    check(all(e < 5e-4 and s > -20 for e, s in got.values()) and alone < -80,
          "the shapes alternate cycle by cycle from the note on (worked out sample for sample) and the sub-octave is "
          "there: " + "; ".join(f"{k}: within {e:.1e}, sub-octave partials up to {s:.1f} dB" for k, (e, s) in got.items())
          + f"; a saw alone: {alone:.1f} dB")
    runs = []
    for b in (0, RESO3 + 1, RESO1 + 1):
        w.set(wave1=RESO2, wave1b=b)
        runs.append(w.held(45, 16)[0])
        w.off(45, 1)
    check(np.array_equal(runs[0], runs[1]) and np.array_equal(runs[0], runs[2]),
          "a resonant shape after a resonant one plays as Off (Reso II with Reso III or Reso I: Reso II alone, sample "
          "for sample)")
    w.set(wave1=SAW, wave1b=SQUARE + 1)
    w.on(45)
    w.run(2)
    inc = w.steps((note_hz(45),) * 2)
    while not (inc[0] * w.t >> 32) & 1:     # a block ending on the pair's second shape
        w.run(1)
    w.set(wave1b=0)
    w.run(1)                                # the mean's ramp
    start = w.t
    y = w.run(4)
    w.off(45, 1)
    e = sim_err(y, inc[0], [ref_shape(SAW, 1.0, f0_of(inc[0]))], start)
    check(e < 5e-4, f"Wave 1+ set Off while the pair is on its second shape: the first shape alone from the next block "
                    f"(within {e:.1e})")


def extra_checks(ptf: Path) -> None:
    print("Ring Mod, Noise Mod, the DCO envelope")
    f1, f2 = note_hz(57), note_hz(64)
    w = PH(ptf, **dict(CLEAN, dcw1=0.0, dcw2=0.0, tune2=7.0, ring=1.0, mix=1.0))
    y, inc, start = w.held(57, 64, hz=(f1, f2))
    w.off(57, 1)
    sim = dco_sim(inc[0], [ref_shape(SAW, 0.0, f1)], start, len(y)) * dco_sim(inc[1], [ref_shape(SAW, 0.0, f2)], start,
                                                                                 len(y))
    e1 = float(np.abs(y - GAIN * sim).max())
    a, b = f0_of(inc[0]), f0_of(inc[1])
    parts = db(np.array([harm_amps(y, f, 1)[0] for f in (b - a, b + a, a, b)]))
    check(e1 < 5e-4 and abs(parts[0] - parts[1]) < 0.2 and max(parts[2:]) - parts[0] < -60,
          f"Ring Mod, Mix +1 (sines at notes 57 and 64): DCO 1 x DCO 2 (within {e1:.1e}): the difference and sum "
          f"({b - a:.1f} and {b + a:.1f} Hz) at {parts[0]:.1f} and {parts[1]:.1f} dB, the DCOs' own pitches at "
          f"{parts[2] - parts[0]:.1f} and {parts[3] - parts[0]:.1f} dB to them")
    w.set(mix=0.0)
    y, inc, start = w.held(57, 32, hz=(f1, f2))
    w.off(57, 1)
    s1 = dco_sim(inc[0], [ref_shape(SAW, 0.0, f1)], start, len(y))
    s2 = dco_sim(inc[1], [ref_shape(SAW, 0.0, f2)], start, len(y))
    e0 = float(np.abs(y - GAIN * 0.5 * s1 * (1 + s2)).max())
    w.set(mix=-1.0)
    ring = w.held(57, 16, hz=(f1, f2))[0]
    w.off(57, 1)
    w.set(ring=0.0)
    plain = w.held(57, 16, hz=(f1, f2))[0]
    w.off(57, 1)
    dr = float(np.abs(ring - plain).max())
    check(e0 < 5e-4 and dr < 1e-5,
          f"Mix 0: DCO 1 x (1 + DCO 2) / 2 (within {e0:.1e}); Mix -1: DCO 1 alone, as with Ring Mod off (within "
          f"{dr:.1e})")
    # Noise Mod: DCO 2 alone, a sine (and a saw: a single shape through the noise's loop)
    w = PH(ptf, CLEAN, dcw2=0.0, mix=1.0)
    amts = (0.0, 0.1, 0.3, 0.6, 1.0)
    share = {}
    for n in (45, 81):
        blocks = int(96 * RATE / note_hz(n) / BLOCK) + 1    # 12 stretches of 8 cycles
        share[n] = []
        for amt in amts:
            w.set(noisemod=amt)
            w.on(n)
            w.run(3)
            share[n].append(incoherent(w.run(blocks), note_hz(n)))
            w.off(n, 1)
    w.set(noisemod=0.0)
    y, inc, start = w.held(57, 16)
    w.off(57, 1)
    e = float(np.abs(y - GAIN * dco_sim(inc[1], [ref_shape(SAW, 0.0, f0_of(inc[1]))], start, len(y))).max())
    w.set(noisemod=0.01, dcw2=1.0)
    w.on(45)
    w.run(3)
    y = w.run(200)
    w.off(45, 1)
    # 8 cycles at a time, the median of each harmonic: over the whole run the phase's wander
    # (from the noise, the voice's own) smears harmonic 4 by up to ~1 dB
    seg = int(8 * RATE / note_hz(45))
    h = np.median([harm_amps(y[i:i + seg], note_hz(45), 4) for i in range(0, len(y) - seg + 1, seg)], axis=0)
    saw = db(h / h[0])
    want = db(ref_harmonics(ref_shape(SAW, 1.0, note_hz(45)), 4))
    want -= want[0]
    dmean = float(abs(y.mean()))
    w.set(mix=-1.0, noisemod=1.0)
    a1 = w.held(57, 16)[0]
    w.off(57, 1)
    w.set(noisemod=0.0)
    a0 = w.held(57, 16)[0]
    w.off(57, 1)
    # rising to 60 %; from there the share levels off near 80 %, where the noise's own spread
    # (a few points from seed to seed) can put 100 % a little under 60 %. The two notes alike:
    # over 40 seeds their means agree within a point, one run's gap scatters by ~6 points (sd)
    ok = all(all(b > a for a, b in zip(s[:4], s[1:4])) and s[0] < 1e-6 and s[1] < 0.15 and s[-1] > 0.7
             for s in share.values())
    alike = max(abs(a - b) for a, b in zip(*share.values()))
    check(ok and alike < 0.2 and e < 5e-4 and np.array_equal(a0, a1) and np.abs(saw - want).max() < 0.5
          and dmean < 0.01,
          "Noise Mod (DCO 2 alone, a sine): the power no longer in its harmonics (8 cycles at a time) at "
          f"{', '.join(f'{a:.0%}' for a in amts)}: " +
          "; ".join(f"note {n} " + ", ".join(f"{x:.1%}" for x in s) for n, s in share.items()) +
          f" (rising to 60 %, under 15 % at 10 %, over 70 % at 100 %, the two notes within {alike:.1%}; under 20 %); "
          f"at 0 the sine itself (within {e:.1e}); a saw at 1 % keeps its harmonics (8 cycles at a time, "
          f"2-4 off the formula's by at most {np.abs(saw - want).max():.2f} dB, mean "
          f"{dmean:.4f}); DCO 1 untouched by it")
    # the DCO envelope
    w = PH(ptf, CLEAN, dcw1=0.0, penv_att=0.0, penv_sus=1.0)
    got = []
    for label, params, want in (("Amount +12", dict(penv_amt=12.0), 1200), ("Amount -12", dict(penv_amt=-12.0), -1200),
                                ("Amount +12, Sustain 50 %", dict(penv_amt=12.0, penv_sus=0.5, penv_dec=0.0), 600),
                                ("Amount 0, the Mod Matrix's DCO Env -> Pitch at 50 %",
                                 dict(penv_amt=0.0, penv_sus=1.0, src1=S_DCO, dest1=D_PITCH, amt1=0.5), 600),
                                ("Amount +48, Release (the note off)", dict(penv_amt=48.0, amt1=0.0, penv_rel=0.0), 0)):
        w.set(**params)
        w.on(57)
        w.run(8)
        if "Release" in label:
            w.set(dca_rel=1.0)
            w.r.note_off(0, 57)
            w.run(8)
        c = cents(zc_freq(w.run(64)), note_hz(57))
        w.set(dca_rel=0.0)
        w.off(57, 2)
        w.run(400)
        got.append((label, c, want))
    check(all(abs(c - want) < 1.0 for _, c, want in got),
          "the DCO envelope (a sine at note 57, Attack 0): " + "; ".join(f"{k}: {c:+.1f} cents (want {want:+d})"
                                                                        for k, c, want in got))


def gains(w: PH, y: np.ndarray, inc: int, start: int, shape: tuple) -> np.ndarray:
    """The level of each block of y against DCO 1's own output (the sine, worked out)."""
    s = dco_sim(inc, [shape], start, len(y)).reshape(-1, BLOCK)
    return (y.reshape(-1, BLOCK) * s).sum(1) / (s * s).sum(1) / GAIN


def pitch_env_checks(ptf: Path) -> None:
    print("pitch, envelopes, Key Follow, glide")
    w = PH(ptf, CLEAN, dcw1=0.0, dcw2=0.0)
    errs = []
    for n in range(24, 109, 6):
        w.on(n)
        w.run(2)
        y = w.run(256 if n < 48 else 96)
        w.off(n, 1)
        errs.append((n, cents(zc_freq(y), note_hz(n))))
    worst = max(errs, key=lambda e: abs(e[1]))
    check(abs(worst[1]) < 1.0, f"pitch within 1 cent from note 24 to 108 (DCW 0, a sine; worst {worst[1]:+.3f} c at "
                               f"note {worst[0]})")
    got = []
    for label, params, want in (("Tune 1 +7 (DCO 1 alone)", dict(mix=-1.0, tune1=7.0), 700),
                                ("Tune 2 -12 (DCO 2 alone)", dict(mix=1.0, tune1=0.0, tune2=-12.0), -1200),
                                ("Finetune +50", dict(mix=-1.0, tune2=0.0, fine=50.0), 50),
                                ("Finetune -100 (DCO 2)", dict(mix=1.0, fine=-100.0), -100),
                                ("Detune 25 (DCO 2)", dict(mix=1.0, fine=0.0, detune=25.0), 25),
                                ("Detune 25 (DCO 1 unchanged)", dict(mix=-1.0, detune=25.0), 0),
                                ("Mod Matrix Detune 100 % (Velocity, DCO 2)",
                                 dict(mix=1.0, detune=0.0, src1=S_VEL, dest1=D_DETUNE, amt1=1.0), 100)):
        w.set(**params)
        w.on(57)
        w.run(2)
        got.append((label, cents(zc_freq(w.run(96)), note_hz(57)), want))
        w.off(57, 1)
    w.set(amt1=0.0, mix=-1.0)
    for value, want in ((8192, 200), (-8192, -200), (0, 0)):
        w.on(57)
        w.r.bend(0, value)
        w.run(2)
        got.append((f"bend {value:+d}", cents(zc_freq(w.run(96)), note_hz(57)), want))
        w.off(57, 1)
    check(all(abs(c - want) < 1.0 for _, c, want in got),
          "; ".join(f"{k}: {c:+.2f} c (want {want:+d})" for k, c, want in got))
    # the envelopes' times, from the level of a sine block by block (the DCW and DCO
    # envelopes through the Mod Matrix's Volume: x (1 + level))
    sine = ref_shape(SAW, 0.0, note_hz(69))
    times = []
    w.set(dca_att=0.5)
    y, inc, start = w.held(69, 200)
    g = gains(w, y, inc[0], start, sine)
    k = np.where((g > 0.1) & (g < 0.9))[0]
    times.append(("DCA Attack 0.5 s", 1 / np.polyfit(k * ENV_DT, g[k], 1)[0], 0.5))
    w.set(dca_rel=1.0)
    w.r.note_off(0, 69)
    y = w.run(120)
    g = gains(w, y, inc[0], start + 200 * BLOCK, sine)
    k = np.arange(5, 120)
    times.append(("DCA Release 1 s (to -60 dB)", 9.9657843 / -np.polyfit(k * ENV_DT, np.log2(g[k]), 1)[0], 1.0))
    w.run(400)
    w.set(dca_att=0.0, dca_rel=0.0)
    for label, params in (("DCW Env Attack 0.3 s", dict(src1=S_DCW, wenv_att=0.3, wenv_sus=1.0)),
                          ("DCO Env Attack 0.3 s", dict(src1=S_DCO, penv_att=0.3, penv_sus=1.0, penv_amt=0.0))):
        w.set(dest1=D_VOLUME, amt1=1.0, **params)
        y, inc, start = w.held(69, 120)
        w.off(69, 1)
        g = gains(w, y, inc[0], start, sine) - 1
        k = np.where((g > 0.1) & (g < 0.9))[0]
        times.append((label, 1 / np.polyfit(k * ENV_DT, g[k], 1)[0], 0.3))
    w.set(amt1=0.0)
    check(all(abs(t / want - 1) < 0.05 for _, t, want in times),
          "envelope times within 5 %: " + "; ".join(f"{k} {t:.3f} s" for k, t, _ in times))
    w = PH(ptf, CLEAN, dcw1=0.0, dcw2=0.0, wenv_att=0.0, wenv_dec=0.0, wenv_sus=0.5)
    got = []
    for a1, a2 in ((1.0, 0.0), (0.0, 1.0), (1.0, -1.0)):
        w.set(envamt1=a1, envamt2=a2)
        for mix, k in ((-1.0, 0), (1.0, 1)):
            w.set(mix=mix)
            w.on(57)
            w.run(10)
            inc = w.steps((note_hz(57),) * 2)
            start = w.t
            y = w.run(16)
            w.off(57, 1)
            d = clampf(0.5 * (a1, a2)[k], 0.0, 1.0)
            got.append((sim_err(y, inc[k], [ref_shape(SAW, d, f0_of(inc[k]))], start), a1, a2, k + 1, d))
    check(all(e < 5e-4 for e, *_ in got),
          "Env Amt 1 and 2 (DCW Env held at 50 %, DCW 0): each DCO's DCW is its own amount x the envelope: " +
          "; ".join(f"amounts {a:+.0%}/{b:+.0%}: DCO {k} at DCW {d:.0%} (within {e:.1e})" for e, a, b, k, d in got))
    w = PH(ptf, CLEAN, keyfollow=1.0)
    got = []
    for n, dcw, want in ((84, 1.0, 0.5), (36, 0.5, 1.0), (60, 0.7, 0.7), (96, 0.6, 0.0)):
        w.set(dcw1=dcw)
        y, inc, start = w.held(n, 16)
        w.off(n, 1)
        got.append((sim_err(y, inc[0], [ref_shape(SAW, want, f0_of(inc[0]))], start), n, dcw, want))
    w.set(keyfollow=-0.5, dcw1=0.25)
    y, inc, start = w.held(84, 16)
    w.off(84, 1)
    got.append((sim_err(y, inc[0], [ref_shape(SAW, 0.5, f0_of(inc[0]))], start), 84, 0.25, 0.5))
    check(all(e < 5e-4 for e, *_ in got), "Key Follow (100 %: DCW -100 % four octaves above note 60): " +
          "; ".join(f"note {n}, DCW {d:.0%}: plays at {want:.0%} (within {e:.1e})" for e, n, d, want in got[:-1]) +
          f"; -50 % at note 84, DCW 25 %: at 50 % (within {got[-1][0]:.1e})")
    w = PH(ptf, CLEAN, dcw1=0.0, glide_time=0.5, glide_mode=0.0, dca_rel=0.3)
    w.on(48)
    w.run(10)
    w.off(48, 200)
    w.r.note_on(0, 60, 127)
    track = [12 * np.log2(zc_freq(w.run(8)) / 440) + 69 for _ in range(30)]
    t_mid = (14 * 8 + 4) * 128 / RATE
    check(abs(track[0] - 48.5) < 1.0 and abs(track[14] - (48 + 12 * t_mid / 0.5)) < 0.5 and abs(track[-1] - 60) < 0.05,
          f"glide Always, 0.5 s: from note 48 ({track[0]:.2f}) through {track[14]:.2f} at {t_mid:.2f} s to "
          f"{track[-1]:.2f}")


def matrix_checks(ptf: Path) -> None:
    print("the LFOs and the Mod Matrix")
    base = dict(CLEAN, mix=0.0, tune2=7.0, dcw1=0.3, dcw2=0.3, lfo1_rate=5.0, lfo2_rate=5.0, lfo2_wave=0.0)
    w = PH(ptf, **base)
    w.on(57)
    w.run(4)
    ref = w.run(40)
    w.off(57, 1)
    moved = []
    for src in (S_LFO1, S_LFO2):
        for dest in range(9):
            w.set(src1=src, dest1=dest, amt1=1.0 if dest != D_VOLUME else -0.5)
            w.on(57)
            w.run(4)
            y = w.run(40)
            w.off(57, 1)
            moved.append((float(np.abs(y - ref).max()), src, dest, bool(np.all(np.isfinite(y))) and np.abs(y).max() <= 2))
    w.set(amt1=0.0)
    check(all(dif > 0.01 and ok for dif, _, _, ok in moved),
          "each LFO (5 Hz) through each destination moves the output, finite and within +-2: " +
          ", ".join(f"{SRC_NAMES[s]}->{DEST_NAMES[d]} {dif:.2f}" for dif, s, d, _ in moved))
    a = PH(ptf)
    b = PH(ptf, **{f"dest{s + 1}": (s * 2) % 9 for s in range(6)}, **{f"src{s + 1}": s for s in range(6)})
    ya = [a.held(57, 30)[0], a.off(57, 40)]
    yb = [b.held(57, 30)[0], b.off(57, 40)]
    check(all(np.array_equal(x, z) for x, z in zip(ya, yb)) and np.abs(ya[0]).max() > 0.05,
          "the Init patch: its routings (LFO 1 -> Pitch, LFO 2 -> DCW) at Amt 0 change nothing (the same output with "
          "all 6 slots routed elsewhere at Amt 0)")
    w = PH(ptf, **dict(base, dca_rel=0.2, penv_amt=5.0))
    bad = []
    for src in range(7):
        for dest in range(9):
            for amt in (4.0, -4.0):
                w.set(src1=src, dest1=dest, amt1=amt)
                w.on(60, 90)
                y = np.concatenate([w.run(6), w.off(60, 3)])
                if not (np.all(np.isfinite(y)) and np.abs(y).max() <= 2.0):
                    bad.append((SRC_NAMES[src], DEST_NAMES[dest], amt))
                w.run(60)
    check(not bad, f"every source to every destination at +-400 % (63 x 2): finite and within +-2 ({bad[:5]})")
    w.set(amt1=0.0)


def level_checks(ptf: Path, stock: Path) -> None:
    print("the level, voices, the effect, the cost")
    r = phase_rig(ptf, polys=(4, 1, 1))
    # the Init's Detune beats DCO 2 against DCO 1 (1.2 Hz at middle C), and the DCOs start where
    # the voice's address puts them: the level is read over two whole beats from block 80
    beat = int(RATE / (note_hz(60) * (2 ** (PHASE_INIT[7][1] / 1200) - 1)))
    got = {}
    for notes, vel in (([60], 100), ([48, 55, 60, 64], 127)):
        for n in notes:
            r.note_on(0, n, vel)
        y = r.render(80 + 2 * beat // BLOCK + 1 if len(notes) == 1 else 160, fast=True)[0][0].astype(float) / 32768
        for n in notes:
            r.note_off(0, n)
        r.render(200, fast=True)
        got[len(notes)] = (20 * np.log10(np.sqrt(np.mean(y[80 * BLOCK:80 * BLOCK + 2 * beat] ** 2))),
                           float(np.abs(y).max()))
    check(-26 < got[1][0] < -17 and got[4][1] < 0.8,
          f"the Init patch: one note at velocity 100 {got[1][0]:.1f} dBFS RMS (held, over two of its Detune's beats; "
          "-26 to -17, WAVE's check: -26 to "
          f"-18); a four-note chord at full velocity peaks at {got[4][1]:.2f} of full scale (under 0.8; the slot clips "
          "at 0.998)")
    w = PH(ptf, CLEAN, dcw1=0.0)
    lv = {}
    for vel in (127, 64):
        y, inc, start = w.held(69, 16, velocity=vel)
        w.off(69, 1)
        lv[vel] = float(np.median(gains(w, y, inc[0], start, ref_shape(SAW, 0.0, note_hz(69)))))
    check(abs(lv[127] - 1) < 1e-3 and abs(lv[64] - 64 / 127) < 1e-3,
          f"velocity sets the level: {lv[127]:.4f} at 127, {lv[64]:.4f} at 64 (64/127 = {64 / 127:.4f})")
    pluck = dict(dca_att=0.0, dca_dec=0.5, dca_sus=0.0, dca_rel=0.5)
    for label, params in (("a pluck", pluck), ("sustained, Release 1.0", dict(pluck, dca_sus=1.0, dca_rel=1.0))):
        a, b = allocation(stock, False, params), allocation(ptf, True, params)
        same = sum(1 for x, y in zip(a, b) if x == y)
        check(a == b, f"voice allocation, {label}: the same voice for every note on as stock VAP ({same} of {len(a)})")
    r = phase_rig(ptf, polys=(2, 1, 1))
    r.note_on(0, 57, 127)
    r.note_on(0, 64, 127)
    dry = ts.graph_lr(r, 20)
    r.set_param(0, PI["effect"], 1.0)
    r.set_param(0, PI["fx_mix"], 1.0)
    ts.graph_lr(r, 30)
    wet = ts.graph_lr(r, 20)
    check(np.array_equal(*dry) and np.abs(dry[0]).max() > 1000 and np.abs(wet[0].astype(int) - wet[1]).max() > 500,
          f"the effect on a PHASE slot: None: R = L; Chorus at Mix 100 %: the sides differ (by up to "
          f"{np.abs(wet[0].astype(int) - wet[1]).max()})")
    w = PH(ptf, dca_rel=0.0)
    v = w.voice
    before = w.r.sounding(v)
    w.on(60)
    on = w.r.sounding(v)
    w.run(10)
    held = w.r.sounding(v)
    w.off(60, 1)
    check(not before and on and held and not w.r.sounding(v),
          "sounding: false before, true from the note on while held, false a block after the note off (Release 0)")
    idle = ts.render_cost(w.r, v, 4)
    plain = []
    for shape in range(8):
        w.set(wave1=shape, wave2=7 - shape, dcw1=1.0, dcw2=0.5)
        w.on(60)
        w.run(1)
        plain.append(ts.render_cost(w.r, v, 4))
        w.off(60, 1)
    w.set(wave1=SAW, wave1b=RESO1 + 1, wave2=RESO3, wave2b=SQUARE + 1, ring=1.0, noisemod=0.5, penv_amt=12.0,
          lfo1_wave=5.0, lfo2_wave=1.0, glide_time=0.2, keyfollow=0.5,
          **{f"src{s + 1}": s for s in range(6)}, **{f"dest{s + 1}": s + 2 for s in range(6)},
          **{f"amt{s + 1}": 0.3 for s in range(6)})
    full = []
    for n in (36, 72, 96):
        w.on(n)
        w.run(1)
        full.append(ts.render_cost(w.r, v, 4))
        w.off(n, 1)
    check(idle < 1000 and max(plain) < 17000 and max(full) < 20000,
          f"cost per block: idle {idle:.0f} (under 1,000); one shape per DCO {min(plain):.0f}-{max(plain):.0f} "
          f"(under 17,000); everything on (pairs, Ring and Noise Mod, the DCO envelope, 6 routings, glide) "
          f"{', '.join(f'{x:.0f}' for x in full)} (under 20,000)")
    r2 = phase_rig(ptf, boot=False)
    v2 = r2.voices(0)[0]
    r2.note_on(0, 60, 100)
    y = r2.render_voice(v2, 4)
    check(not np.any(y) and not r2.sounding(v2), "before the unit has booted: zeros, not sounding")


def allocation(path: Path, patched: bool, params: dict) -> list[tuple[int, int]]:
    """Which voice (index in the slot) each note on of a fixed sequence goes to: the first
    half of test_synths' sequence, on PHASE (patched) or stock VAP."""
    r = Rig(path, patched)
    r.configure((VAP, PERC, PERC), (4, 1, 1))
    if patched:
        r.pattern()
        ph_swap(r, VAP)
    for k, x in params.items():
        r.set_param(0, PI[k], x)
    got: list[tuple[int, int]] = []
    voices = r.voices(0)
    midi_word = r.m.r32(sr.VOICE_VT[VAP] + sr.V_MIDI) & ~1

    def on_midi(uc, a, s, u):
        msg = bytes(r.m.uc.mem_read(r.m.arg(2), 4))
        if msg[0] == 4:
            got.append((voices.index(r.m.arg(0)), msg[2]))
    r.m.uc.hook_add(ts.UC_HOOK_CODE, on_midi, begin=midi_word, end=midi_word)
    rnd = np.random.default_rng(7)
    held: list[int] = []
    for step in range(120):
        x = rnd.random()
        if x < 0.35 and len(held) < 6:
            n = int(rnd.integers(40, 80))
            if n not in held:
                r.note_on(0, n, 100)
                held.append(n)
        elif x < 0.7 and held:
            r.note_off(0, held.pop(int(rnd.integers(len(held)))))
        r.render(int(rnd.integers(1, 40)), fast=True)
    return got


def write_wav(path: Path, y: np.ndarray) -> None:
    import wave
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(int(RATE))
        f.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())


def wav_files(ptf: Path, out: Path) -> None:
    """Listening files (not checks): each shape's DCW sweep, pairs, a resonant sweep at
    three notes, Ring and Noise Mod, a DCO envelope pluck and an Init chord."""
    out.mkdir(parents=True, exist_ok=True)
    w = PH(ptf, **dict(CLEAN, dca_rel=0.3))

    def sweep(n: int, seconds: float = 3.0, key: str = "dcw1") -> np.ndarray:
        w.on(n)
        blocks = int(seconds * RATE / BLOCK)
        ys = []
        for b in range(blocks):
            w.set(**{key: b / (blocks - 1)})
            ys.append(w.run(1))
        ys.append(w.off(n, 120))
        return np.concatenate(ys)
    for shape in range(8):
        w.set(wave1=shape)
        write_wav(out / f"shape{shape + 1}_{SHAPES[shape].replace(' ', '').replace('-', '')}_dcw_sweep.wav",
                  np.concatenate([sweep(45), sweep(69)]))
    for a, b in ((SAW, SQUARE), (PULSE, DSINE), (SAW, RESO1), (SQUARE, RESO3)):
        w.set(wave1=a, wave1b=b + 1)
        write_wav(out / f"pair_{SHAPES[a]}+{SHAPES[b]}.wav".replace(" ", ""), sweep(45))
    w.set(wave1b=0, wave1=RESO1)
    write_wav(out / "reso1_sweep_notes_36_60_84.wav", np.concatenate([sweep(n) for n in (36, 60, 84)]))
    w.set(wave1=SAW, mix=1.0, ring=1.0, tune2=7.0, dcw2=0.4)
    write_wav(out / "ring_mod_dcw1_sweep.wav", sweep(57))
    w.set(ring=0.0, tune2=0.0)
    write_wav(out / "noise_mod_sweep.wav", sweep(57, key="noisemod"))
    w = PH(ptf, wave1=SAW, wave2=RESO2, penv_amt=12.0, penv_att=0.0, penv_dec=0.08, penv_sus=0.0)
    ys = []
    for n in (45, 52, 57, 64):
        w.on(n)
        ys.append(w.run(60))
        ys.append(w.off(n, 60))
    write_wav(out / "dco_env_pluck.wav", np.concatenate(ys))
    r = phase_rig(ptf, polys=(4, 1, 1))
    for n in (48, 55, 60, 64):
        r.note_on(0, n, 110)
    y = r.render(400, fast=True)[0][0].astype(float) / 32768
    for n in (48, 55, 60, 64):
        r.note_off(0, n)
    y = np.concatenate([y, r.render(200, fast=True)[0][0].astype(float) / 32768])
    write_wav(out / "init_chord.wav", y)
    print(f"  listening files in {out}")


# ---- 9. WAVE unchanged ---------------------------------------------------------------------------

def wave_model(ptf: Path) -> tuple:
    r = Rig(ptf)
    m = r.m
    r.configure((VAP, PERC, PERC), (2, 1, 1))
    r.swap(VAP)
    ents = r._entries(0)
    p = r.patch_obj(0)
    entries_ = [(sstr(m, q), bytes(m.uc.mem_read(q + 0x18, 0x10))) for q in ents]
    shown = [ts.display(m, ents[i], v) for i in (2, 11, 38, 42, 43, 60, 62, 63) for v in (0.0, 1.0, 3.0)]
    return (r.model(0), m.r32(p + 0x54 + 0x28), entries_, ts.container_layout(m, p + 0x54), shown,
            r.params(0), r.defaults(0), r.title(0))


def wave_checks(ptf: Path, ref: Path, here: tuple) -> None:
    print(f"WAVE unchanged (against {ref.name})")
    there = layout_of(ref)
    use(*there)
    old = wave_model(ref)
    use(*here)
    new = wave_model(ptf)
    check(old == new, f"WAVE's model: names, values, ranges, flags, displays, screen, pages, Macros, title "
                      f"identical ({len(new[2])} parameters)")
    runs = []
    for path, where in ((ptf, here), (ref, there)):
        use(*where)
        r = Rig(path)
        r.configure((VAP, VAP, ACD), (1, 4, 1))
        r.pattern()
        r.swap(VAP)
        runs.append(ts.script(r, 150, seed=5))
    use(*here)
    ds, dv = ts.compare(*runs)
    check(ds == 0 and dv == 0 and ts.peak(runs[0]) > 1000,
          f"WAVE replacing VAP (1 and 4 voices) beside ACD, notes, bends, CCs and Macros: the same output, "
          f"sample for sample (largest differences {ds}, {dv}; peak {ts.peak(runs[0])})")


def engine_smoke(ptf: Path) -> None:
    """--quick: the engine's checks depend on its code alone, not on where a mix puts it;
    one note against the formula, and the effect, check it plays here."""
    print("the engine (--quick: one note)")
    w = PH(ptf, CLEAN, wave1=SQUARE, dcw1=0.7, wave1b=RESO2 + 1)
    y, inc, start = w.held(57, 16)
    w.off(57, 1)
    f0 = f0_of(inc[0])
    e = sim_err(y, inc[0], [ref_shape(SQUARE, 0.7, f0), ref_shape(RESO2, 0.7, f0)], start)
    check(e < 5e-4 and np.abs(y).max() > 0.1, f"a Square + Reso II pair at DCW 70 %: the formula's output, sample for "
                                               f"sample (within {e:.1e})")
    r = phase_rig(ptf, polys=(2, 1, 1))
    r.set_param(0, PI["effect"], 1.0)
    r.set_param(0, PI["fx_mix"], 1.0)
    r.note_on(0, 57, 127)
    r.note_on(0, 64, 127)
    ts.graph_lr(r, 30)
    lr = ts.graph_lr(r, 20)
    check(np.abs(lr[0]).max() > 1000 and np.abs(lr[0].astype(int) - lr[1]).max() > 500,
          "two notes through the slot's output with the Chorus: sound on both sides, the sides differ")


def engine_checks(ptf: Path, stock: Path) -> None:
    shape_checks(ptf)
    alias_checks(ptf)
    continuity_checks(ptf)
    pair_checks(ptf)
    extra_checks(ptf)
    pitch_env_checks(ptf)
    matrix_checks(ptf)
    level_checks(ptf, stock)


def main() -> int:
    args = sys.argv[1:]
    wav = None
    if "--wav" in args:
        k = args.index("--wav")
        wav = Path(args[k + 1])
        del args[k:k + 2]
    only_engine, quick = "--engine" in args, "--quick" in args
    args = [a for a in args if a not in ("--engine", "--quick")]
    ptf, stock = Path(args[0]), Path(args[1])
    ref = Path(args[2]) if len(args) > 2 else None
    ts.MAP_FIELDS = MAP_FIELDS
    here = layout_of(ptf)
    use(*here)
    if only_engine:
        engine_checks(ptf, stock)
        if wav:
            wav_files(ptf, wav)
        print(f"{len(ts.failures)} failure(s)")
        return 1 if ts.failures else 0
    hook_checks(ptf, stock)
    model_checks(ptf)
    copy_checks(ptf)
    swap_checks(ptf)
    menu_checks(ptf)
    menu_list_checks(ptf)
    model_list_checks(ptf)
    label_checks(ptf)
    tile_checks(ptf)
    file_checks(ptf)
    patch_file_checks(ptf, stock)
    if quick:
        engine_smoke(ptf)
    else:
        engine_checks(ptf, stock)
    if wav:
        wav_files(ptf, wav)
    if ref:
        ts.MAP_FIELDS = SY7_FIELDS          # sy7's map ends before PHASE's
        wave_checks(ptf, ref, here)
        ts.MAP_FIELDS = MAP_FIELDS
    else:
        print("WAVE unchanged: skipped (no sy7 firmware given)")
    use(*here)
    ts.stress_checks(ptf, 300, phase=True)
    print(f"{len(ts.failures)} failure(s)" + (" (--quick: the engine's checks skipped but one note)" if quick else ""))
    return 1 if ts.failures else 0


if __name__ == "__main__":
    sys.exit(main())
