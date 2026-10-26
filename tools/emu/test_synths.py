"""Check the synths patch (WAVE, a wavetable synth that stands in for one synth model,
chosen per project).

  python tools/emu/test_synths.py <patched.ptf> <stock TrackerPlus_1.2.0.ptf> [<wav folder>] [--stress N] [--quick]

--quick runs only what can differ between bundles (the hooks, the WAVE model, the swap,
the menus and screens, the settings and patch files, Macros, the render loop, the effect
(its state is in the late RAM block, placed per bundle), and 200 stress blocks) and skips the rig, stock-output, sound, voice-allocation and bank checks,
which depend only on synths' own code (tools/run_tests.py).

Works with the synths-only build and with bundles that include it. The firmware's own
synth system runs in the emulator (tools/emu/synth_rig.py): host, slots, patches, VAP
voices, rendered through the audio graph (or the synth output object alone).

- The rig (stock firmware): the per-voice heap and cost of docs/FINDINGS.md section 21
  come out again, and two runs of the same notes give identical output.
- Hooks: the six VAP voice vtable words and the four call sites (the registry builder,
  the project's slot records, the Model list's data and its images) point into the
  patch; the other models' voice vtables are stock; the registry names model 3 "WAVE".
- Stock output: with WAVE off, or replacing a model no slot uses, the synth output and
  every voice's own output are identical to stock firmware's, sample for sample, booted
  or not (VAP at 1 and 4 voices, ACD, FAT, WTFM, PERC); with WAVE replacing ACD, the VAP
  slots still are.
- The WAVE model: a patch with model id 3; its names, ranges, stepped lists, value
  displays (wave and filter names, out-of-range values clamped), Init values, screen
  layout (Oscillator, Filter, Amp Env, Filter Env, FX, Voice), the Macro editor's pages
  and the Macros; every parameter they show is one the engine reads; no heap leak over
  50 model switches; the instrument title "Synth 1 (WAVE)".
- The swap: the menu's choice reaches the model table at the next Config update; the
  slots on the replaced model become WAVE (VAP voice objects, an engine voice each,
  no set_param reaching VAP), the others stay; Off brings the stock model back; the
  patch adds no audio interrupt enable inside the stock code's own critical section;
  heap and engine voices unchanged after 200 swaps with notes held.
- Config menu: Project Settings keeps its 10 stock items and gets "Synth Engines" with
  "WAVE replaces" (Off, ACD, FAT, VAP, WTFM, PERC) and "PHASE replaces" (checked in
  tools/emu/test_phase.py); the stock Apply stores each value.
- The Model list: the synth settings screen gives the list the patch's image ids with
  the replaced position marked; the list draws the four other logos and "WAVE" as
  text centred where the replaced logo was, and the stock list is untouched.
- Settings file: synths.bin per project (fake SD card): read at project load, before
  the stock code applies the slot records (the table set by then); one write a while
  after a change; damaged, sy1 and out-of-range files count as Off and are kept; a new
  name without a file keeps the choice and writes it; a project without a name writes
  nothing; stock firmware never opens the file.
- Save As (the firmware's save request 0x60114e34): a new project ("New Project", no
  path) reads and writes nothing; its first save, a Save As from a saved project and
  one to a 40-character name keep WAVE's and PHASE's choices and write them to the new
  folder, leaving the old file; a Save As over a project that has its own synths.bin
  writes the saved project's choices over it (Off too), and they stay when it is
  opened again; a plain Save writes nothing; the write waits for the file manager.
- Patch files: a WAVE patch saves to WAVE/<name>.patch with model id 3 and its 66
  values, and loads back into a WAVE slot; a file of 60 (saved before the Polarity
  switches) loads with both Bipolar and Effect None, one of 62 (before the effect) with
  Effect None; a load that fails leaves them as they were; stock firmware rejects a
  WAVE file on an ACD slot (the ACD Init patch plays).
- Polarity: WAVE's first two parameters after VAP's 60. Every per-parameter array of the
  patch has 66 entries, also after a rebuild (Sync changed); Unipolar LFOs give 0..1
  (a square steps 0 and +100 cents, Random S&H stays above 0), each LFO its own; LFO 1
  Unipolar at 50 % from Position 0 sweeps 0 to 0.5 with no stop at 0.
- Oscillators: pitch within 1 cent (notes 24-108); Tune, Finetune, Detune, pitch bend;
  aliasing of a saw at notes 96-108 below -60 dB; the Basic bank's morph (sine,
  triangle, saw, square); Mix; Reset; "sounding"; under 500 instructions idle and
  16,000 playing per block.
- Filter: -3 dB at Cutoff (LP 12 dB, Q 0.707), the 12 and 24 dB slopes, HP, BP, Notch,
  the resonance peak, every type stable at Resonance 1.5 and 18 kHz.
- Analog filters: the ladder (MG) falls 24 dB per octave, its resonance peak rises, and
  at Resonance 150 % it self-oscillates near Cutoff, bounded; OB at a low level is the
  SVF's response, and at high resonance its peak saturates.
- Super saw (Wave 7): at Position 0 seven saws at one pitch, a steady harmonic sound;
  spread, seven separate partials that beat; the level within 3 dB of one saw; the cost
  of both oscillators on it.
- Digital mode: Position steps from frame to frame, and a frame sounds different from
  the smooth mode's (8-bit, no interpolation).
- Envelopes: Env Amt both ways, Key Track, attack, decay and release times within 5 %,
  sustain, the end of "sounding"; glide (Always, Overlap).
- WAVE's Init patch: one note at a moderate level, a four-note chord at full velocity
  below the slot's clip.
- Voice allocation: the same voice for every note on as stock VAP with the same
  envelopes (4 voices, more notes than voices, overlapping releases).
- Banks: every table level band-limited, guard samples, levels within 3 dB; Wave picks
  one of 8 slots (the 7 banks and the super saw), each within 3 dB of the others.
- More than 8 voices: 8 engine voices, the rest silent until one is free; freed engine
  voices go to voices that had none.
- Macros: a Macro ramp reaches set_param every block and moves the sound.
- The effect (62-65: Effect, Rate, Depth, Mix), on a WAVE slot's output in stereo: with
  Effect None R is L; the chorus's two taps, measured from a click, sit at 3.5 ms and
  move in opposite directions by Depth x 1.85 ms at Rate's speed; Mix 0 is dry, 100 %
  the taps alone; switching on and off fades (no step); its state starts cleared after a
  start whatever the late RAM held; the level within 1.5 dB of dry; L and R differ
  through the whole audio graph; with 4 voices too; its cost per block.
- Export: the render loop (0x600bc220) renders the WAVE voices.
- Stress (N blocks, default 2000): random notes, parameters (garbage too), swaps, Model
  list picks, polyphony changes, restarts, with a write watch over all RAM; then all
  parameters NaN, and the Init values again.
- WAV files (if a folder is given): VAP's Init patch and WAVE's, a position sweep
  through each bank and the super saw, a ladder filter sweep, a pad, super saw chords,
  a resonant sweep in Digital mode, chords dry and with the chorus (stereo).
"""
from __future__ import annotations

import struct
import sys
import time
from pathlib import Path

import numpy as np
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import synth_rig as sr  # noqa: E402
from fakefs import PROJECT_NAME, FakeFS  # noqa: E402
from layout import patch_code, patch_late_ram  # noqa: E402
from machine import FAKE_DISPLAY_VT, Machine  # noqa: E402
from patch_ptf import bl_target  # noqa: E402
import project_save  # noqa: E402

ACD, FAT, VAP, WTFM, PERC = sr.ACD, sr.FAT, sr.VAP, sr.WTFM, sr.PERC
WAVE = 3                            # WAVE's model id
PHASE = 7                           # PHASE's (tools/emu/test_phase.py)
MODELS = (ACD, FAT, VAP, WTFM, PERC)  # the Model list, index 0-4
INDEX = {md: k for k, md in enumerate(MODELS)}
MENU_NAMES = ["Off", "ACD", "FAT", "VAP", "WTFM", "PERC"]
MODEL_TABLE = 0x200006B4            # the project's model index -> model id
RECORD_MODEL = 0x20204D08           # slot record +8 (model index); + 0x40 per slot
RECORDS = 0x202046C4                # synth_slots_apply_project_q's records argument
APPLY_RECORDS = 0x600B2080
TITLE = 0x600B16B8                  # (ret string, S, slot): "Synth 1 (VAP)"
SCREEN, SCREEN_SETUP, STOCK_LIST_IDS = 0x20020C74, 0x6011AE60, 0x60061488
LIST_CTOR, IMAGE_TABLE_LIT = 0x60105FAC, 0x60106528
BL_REGISTRY, BL_RECORDS, BL_LIST_DATA, BL_LIST_IMAGES = 0x60126B30, 0x60116382, 0x6011B0BA, 0x60106606
IRQ_ENABLE, IRQ_DISABLE = 0x600303C8, 0x600303D4
NVIC_ISER, NVIC_ICER = 0xE000E100, 0xE000E180
LIST_TEXT_ID = 0x7F
PATTERN_UPDATE_SLOT, PATTERN_UPDATE = 0x600403DC, 0x600E1F98
CONFIG_UPDATE_SLOT, CONFIG_UPDATE = 0x6003B5F8, 0x600C6474
CFG_MENU_ITEM, CFG_MENU_FOLDER = 0x601183E8, 0x60118394
MENU_CTOR, ATEXIT = 0x60118ACC, 0x600A3594
PROJECT_SETTINGS = 0x20028714
MENU_CURRENT, MENU_SELECT = 0x20028180, 0x60118928
MENU_APPLY, MENU_EDITOR_CLOSE, MENU_REDRAW = 0x60118658, 0x600CAB60, 0x60118828
VAP_NEW = 0x6014A958                # the VAP factory invoker
VAP_DELETE = 0x6014B724             # the stock deleting destructor
VAP_SET_PARAM = 0x6014A8F0
SLOT_LOAD = 0x600B1270              # (S, slot, out): the slot's Workspace patch
STORAGE_VT = 0x60062FD4             # the patch storages' class: +0x0c load into a patch (hooked)
FILE_MAGIC = b"HSY2"
SAVE_DELAY = 25
STOCK_ITEMS = ["Performance presets", "Pattern divider", "Notes scale", "Root note", "Notes layout",
               "Anti-aliasing", "Limiter mode", "Sends mode", "Extra headroom", "Snap to zero"]
MAP_MARKER = 0x50414D52             # "RMAP"
MAP_FIELDS = ("state", "menu", "applied", "ready", "switches", "loads", "model_ids", "param", "voice",
              "voice_size", "params", "size", "tables", "banks", "frames", "stride", "level_off", "level_len",
              "levels", "builder", "fx_ready", "fx_size", "fx_p", "fx_running")
SY8_FIELDS = MAP_FIELDS + ("ph_menu", "ph_applied", "ph_builder", "menu_pos", "ph_menu_pos")   # sy8's map
SY_READY = 0x59444552
CC_CUTOFF, CC_RESONANCE, CC_MACRO = 74, 71, 102     # Macro a-f arrive as CCs 102-107
ENGINE_READS = set(range(66))                               # the parameters wt_engine.h and wt_fx.h read

WHERE: dict[str, int] = {}          # set in main(): the patch's state and its layout
CODE = (0, 0)                       # set in main(): the patch's code, start and end
LATE = (0, 0)                       # set in main(): the patch's late RAM block (the effect's state)
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def ram_map(m, ptf: Path, fields: tuple[str, ...] | None = None) -> dict[str, int]:
    """Where the patch's state is (mt_syn_ram_map: a marker, the state's address, its layout)."""
    fields = fields or MAP_FIELDS
    lo, hi = patch_code(ptf, "synths")
    code = bytes(m.uc.mem_read(lo, hi - lo))
    for i in range(0, len(code) - 4 * (len(fields) + 1), 4):
        if struct.unpack_from("<I", code, i)[0] == MAP_MARKER:
            return dict(zip(fields, struct.unpack_from(f"<{len(fields)}I", code, i + 4)))
    raise SystemExit(f"{ptf}: the synths patch's RAM map not found")


def sstr(m, a: int) -> str:
    """A libstdc++ std::string at a."""
    n = m.r32(a + 4)
    return bytes(m.uc.mem_read(m.r32(a), n)).decode("latin-1") if n < 256 else "?"


class Rig(sr.SynthRig):
    """The synth system of one firmware; with `patched`, the patch's state and hooks too.
    The NVIC's audio interrupt bit is modelled (ICER clears the bit ISER sets), and starts on."""

    def __init__(self, ptf: Path, patched: bool = True):
        super().__init__(ptf)
        m = self.m
        m.stub_function(PATTERN_UPDATE, lambda mm: 0)
        m.stub_function(CONFIG_UPDATE, lambda mm: 0)
        self.patched = patched
        self.created: list[int] = []
        self.deleted: list[int] = []
        self.pushes: list[tuple[int, int]] = []         # VAP's own set_param: (voice, index)
        self.sets: list[tuple[int, int]] = []           # the voice vtable's set_param word: (voice, index)
        self.irq: list[str] = []
        m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: self.created.append(1), begin=VAP_NEW, end=VAP_NEW)
        m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: self.deleted.append(m.arg(0)), begin=VAP_DELETE,
                      end=VAP_DELETE)
        m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: self.pushes.append((m.arg(0), m.arg(1))),
                      begin=VAP_SET_PARAM, end=VAP_SET_PARAM)
        sp = m.r32(sr.VOICE_VT[VAP] + sr.V_SET_PARAM) & ~1
        m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: self.sets.append((m.arg(0), m.arg(1))), begin=sp, end=sp)
        m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: self.irq.append("E"), begin=IRQ_ENABLE, end=IRQ_ENABLE)
        m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: self.irq.append("D"), begin=IRQ_DISABLE, end=IRQ_DISABLE)

        def icer(uc, access, a, size, value, user):
            if value & 1:
                m.w32(NVIC_ISER, m.r32(NVIC_ISER) & ~1)
        m.uc.hook_add(UC_HOOK_MEM_WRITE, icer, begin=NVIC_ICER, end=NVIC_ICER + 3)
        m.w32(NVIC_ISER, 1)

    # -- slots as the Model list sets them ------------------------------------------
    def configure(self, models=(VAP, ACD, ACD), polys=(1, 1, 1)) -> None:
        super().configure(models, polys)
        for i, md in enumerate(models):
            self.m.w32(RECORD_MODEL + 0x40 * i, INDEX[md])

    def pick(self, slot: int, model: int) -> None:
        """The Model list's choice (0x6011b63c): the record's index, then the table's model."""
        self.m.w32(RECORD_MODEL + 0x40 * slot, INDEX[model])
        self.set_model(slot, self.table()[INDEX[model]])

    def table(self) -> list[int]:
        return [self.m.r32(MODEL_TABLE + 4 * k) for k in range(5)]

    def patch_model(self, i: int) -> int:
        return self.m.r32(self.patch_obj(i) + 0x78)

    # -- the patch's state ----------------------------------------------------------
    def st(self, field: str) -> int:
        return WHERE["state"] + WHERE[field]

    def swap(self, model: int | None, apply: bool = True) -> None:
        """The menu's "WAVE replaces" (None: Off), applied by a Config update."""
        self.m.w8(self.st("menu"), 0 if model is None else 1 + INDEX[model])
        if apply:
            self.config()

    def menu(self) -> int:
        return self.m.uc.mem_read(self.st("menu"), 1)[0]

    def applied(self) -> int:
        return self.m.uc.mem_read(self.st("applied"), 1)[0]

    def ready(self) -> bool:
        return self.m.r32(self.st("ready")) == SY_READY

    def switches(self) -> int:
        return self.m.r32(self.st("switches"))

    def loads(self) -> int:
        return self.m.r32(self.st("loads"))

    def model_ids(self) -> list[int]:
        return list(struct.unpack("<5i", bytes(self.m.uc.mem_read(self.st("model_ids"), 20))))

    def engine_voice(self, voice: int) -> int:
        """The struct wt_voice tied to a VAP voice object."""
        for k in range(8):
            a = self.st("voice") + k * WHERE["voice_size"]
            if self.m.r32(a) == voice:
                return a
        raise LookupError(f"no engine voice for {voice:#x}")

    def entries(self) -> list[tuple[int, int]]:
        """The engine voices in use: (VAP voice, slot)."""
        out = []
        for k in range(8):
            a = self.st("voice") + k * WHERE["voice_size"]
            if self.m.r32(a):
                out.append((self.m.r32(a), self.m.r32(a + 4)))
        return out

    def param_copy(self, slot: int) -> list[float]:
        a = self.st("param") + slot * 4 * WHERE["params"]
        return list(struct.unpack(f"<{WHERE['params']}f", bytes(self.m.uc.mem_read(a, 4 * WHERE["params"]))))

    # -- screens ------------------------------------------------------------------------
    def pattern(self) -> None:
        if self.patched:
            self.m.call(self.m.r32(PATTERN_UPDATE_SLOT), 0x20021770, max_insns=200_000_000)

    def config(self) -> None:
        if self.patched:
            self.m.call(self.m.r32(CONFIG_UPDATE_SLOT), 0x20021770, max_insns=200_000_000)

    def stock_rebuild(self, slot: int) -> None:
        """A slot's voices made again (as a start makes them)."""
        m = self.m
        s = self.slot(slot)
        n = m.r32(s + sr.SLOT_NVOICES)
        f = m.r32(m.r32(s) + 0x1C)
        m.call(f, s, 0, max_insns=100_000_000)
        m.call(f, s, n, max_insns=100_000_000)

    def title(self, slot: int) -> str:
        out = self.m.alloc(0x40)
        self.m.write(out, bytes(0x40))
        self.m.call(TITLE, out, sr.S, slot, max_insns=5_000_000)
        return sstr(self.m, out)


def script(r: Rig, blocks: int = 150, seed: int = 1) -> tuple[dict, dict]:
    """The same notes, bends, CC edits and Macros on every slot; returns the outputs per
    slot and each voice's own output (by voice order in its slot)."""
    rnd = np.random.default_rng(seed)
    r.capture_voices()
    order = {v: (i, k) for i in range(3) for k, v in enumerate(r.voices(i))}
    held: dict[int, list[int]] = {0: [], 1: [], 2: []}
    outs = []
    for b in range(blocks):
        for slot in range(3):
            x = rnd.random()
            if x < 0.08 and len(held[slot]) < 6:
                n = int(rnd.integers(36, 90))
                r.note_on(slot, n, int(rnd.integers(20, 128)))
                held[slot].append(n)
            elif x < 0.14 and held[slot]:
                r.note_off(slot, held[slot].pop(int(rnd.integers(len(held[slot])))))
            elif x < 0.16:
                r.bend(slot, int(rnd.integers(-32768, 32768)))
            elif x < 0.18:
                r.cc(slot, CC_CUTOFF, int(rnd.integers(0, 128)))
            elif x < 0.19:
                r.cc(slot, CC_RESONANCE, int(rnd.integers(0, 128)))
            elif x < 0.20:
                r.cc(slot, CC_MACRO + int(rnd.integers(0, 6)), int(rnd.integers(0, 128)))
        outs.append(r.block())
    per_slot = {i: np.concatenate([np.concatenate([o[i][0], o[i][1]]) for o in outs]) for i in range(3)}
    per_voice = {order[v]: r.voice_signal(v) for v in order if v in r.voice_out}
    return per_slot, per_voice


def compare(a, b, slots=(0, 1, 2)) -> tuple[int, float]:
    """Slots: largest sample difference; voices: largest float difference."""
    ds = max(int(np.abs(a[0][i].astype(int) - b[0][i].astype(int)).max()) for i in slots)
    keys = {k for k in set(a[1]) | set(b[1]) if k[0] in slots}
    dv = 0.0
    for k in keys:
        x, y = a[1].get(k), b[1].get(k)
        if x is None or y is None or len(x) != len(y):
            return ds, float("inf")
        dv = max(dv, float(np.abs(x - y).max()) if len(x) else 0.0)
    return ds, dv


def peak(out, slots=(0, 1, 2)) -> int:
    return max(int(np.abs(out[0][i]).max()) for i in slots)


def no_growth(heap: list[int]) -> bool:
    """Heap use sampled along a run: within 96 bytes all along (the allocator moves a few
    dozen bytes either way, stock firmware too: 1,000 swaps on sy4 wander within 68 and end
    lower than they start); a leak grows with every step."""
    return max(heap) - min(heap) <= 96


# ---- 1. the rig -------------------------------------------------------------------------

def rig_checks(stock: Path) -> None:
    print("the synth rig (stock firmware)")
    want = {ACD: (3140, 47746), VAP: (6472, 60804), PERC: (6068, 4333)}
    for md, (heap, idle) in want.items():
        r = sr.SynthRig(stock)
        r.configure((md, ACD, ACD), (1, 1, 1))
        cost, _ = sr.voice_cost(r, md)
        h0 = r.heap_used()
        r.set_poly(0, 2)
        per_voice = r.heap_used() - h0
        check(per_voice == heap and abs(cost - idle) / idle < 0.01,
              f"{sr.MODEL_NAMES[md]}: {per_voice} B of heap per voice (FINDINGS {heap}), {cost:.0f} instructions "
              f"per idle voice per block (FINDINGS {idle})")
    runs = []
    for _ in range(2):
        r = Rig(stock, patched=False)
        r.configure((VAP, VAP, ACD), (1, 4, 1))
        runs.append(script(r, 60))
    ds, dv = compare(*runs)
    check(ds == 0 and dv == 0 and peak(runs[0]) > 1000,
          f"two runs of the same notes: identical output (peak {peak(runs[0])})")


# ---- 2. hooks ---------------------------------------------------------------------------

def model_names(r: sr.SynthRig) -> dict[int, str]:
    """Model id -> name, as the synth manager gives it (vtable +0x34, the registry)."""
    m = r.m
    mgr = m.r32(sr.S + 0x90)
    out = {}
    for i in range(8):
        buf = m.alloc(0x20)
        m.write(buf, bytes(0x20))
        m.call(m.r32(m.r32(mgr) + 0x34), buf, mgr, i, max_insns=2_000_000)
        out[i] = sstr(m, buf)
    return out


def hook_checks(ptf: Path, stock: Path) -> None:
    print("hooks")
    p, s = Machine(ptf), Machine(stock)
    words = [p.r32(sr.VOICE_VT[VAP] + off) for off in (0x04, 0x08, 0x0C, 0x10, 0x14, 0x18)]
    check(all(CODE[0] <= w < CODE[1] and w & 1 for w in words),
          "VAP voice vtable +0x04, +0x08, +0x0c, +0x10, +0x14, +0x18 point into the patch")
    same = all(p.r32(sr.VOICE_VT[VAP] + off) == s.r32(sr.VOICE_VT[VAP] + off)
               for off in range(0, 0x30, 4) if off not in (0x04, 0x08, 0x0C, 0x10, 0x14, 0x18))
    check(same, "the VAP vtable's other words are stock")
    others = all(p.r32(sr.VOICE_VT[md] + off) == s.r32(sr.VOICE_VT[md] + off)
                 for md in (ACD, FAT, WTFM, PERC) for off in range(0, 0x30, 4))
    check(others, "the ACD, FAT, WTFM and PERC voice vtables are stock")
    load = p.r32(STORAGE_VT + 0x0C)
    check(CODE[0] <= load < CODE[1] and load & 1 and s.r32(STORAGE_VT + 0x0C) == 0x60124F39
          and all(p.r32(STORAGE_VT + off) == s.r32(STORAGE_VT + off) for off in range(0, 0x20, 4) if off != 0x0C),
          "the patch storage's load (vtable 0x60062fd4 +0x0c) goes to the patch, its other words stock")
    image = bytes(p.uc.mem_read(0x60000000, 0x800000))
    sites = {a: bl_target(image, 0x60000000, a) for a in (BL_REGISTRY, BL_RECORDS, BL_LIST_DATA, BL_LIST_IMAGES)}
    check(all(CODE[0] <= t < CODE[1] for t in sites.values()),
          "the registry builder, project records, Model list data and list image calls go to the patch "
          f"({', '.join(f'{a:#x}' for a in sites)})")
    names = model_names(sr.SynthRig(ptf))
    stock_names = model_names(sr.SynthRig(stock))
    check(names[3] == "WAVE" and stock_names[3] == "" and all(names[i] == stock_names[i] for i in (0, 1, 2, 4, 5, 6)),
          f"the registry: model 3 is WAVE (stock firmware has none), the others as stock ({names})")


# ---- 3. stock output --------------------------------------------------------------------

def run_pair(ptf: Path, stock: Path, models, polys, setup, blocks=150, seed=1):
    out = []
    for path, patched in ((ptf, True), (stock, False)):
        r = Rig(path, patched)
        r.configure(models, polys)
        setup(r)
        out.append((script(r, blocks, seed), r))
    return out


def stock_output_checks(ptf: Path, stock: Path) -> None:
    print("stock output through the hooks")
    models, polys = (VAP, VAP, ACD), (1, 4, 1)
    (a, ra), (b, _) = run_pair(ptf, stock, models, polys, lambda r: None)
    ds, dv = compare(a, b)
    check(ds == 0 and dv == 0 and peak(a) > 1000 and ra.entries() == [] and ra.switches() == 0,
          f"WAVE off, before the Pattern screen: identical to stock (VAP 1 and 4 voices, ACD; peak {peak(a)}), "
          "no engine voices")

    def booted(r):
        r.pattern()
    (a, ra), (b, _) = run_pair(ptf, stock, models, polys, booted, seed=2)
    ds, dv = compare(a, b)
    check(ra.ready() and ds == 0 and dv == 0 and peak(a) > 1000 and ra.entries() == [],
          f"WAVE off, booted: identical to stock (largest differences {ds}, {dv})")
    for models2, absent in (((VAP, VAP, ACD), WTFM), ((FAT, WTFM, PERC), VAP), ((ACD, PERC, FAT), WTFM)):
        def booted_swap(r, absent=absent):
            r.pattern()
            if r.patched:
                r.swap(absent)
        (a, ra), (b, _) = run_pair(ptf, stock, models2, (1, 2, 1), booted_swap, blocks=80, seed=3)
        ds, dv = compare(a, b)
        names = ", ".join(sr.MODEL_NAMES[x] for x in models2)
        check(ds == 0 and dv == 0 and peak(a) > 100 and ra.entries() == [] and ra.switches() == 0
              and ra.applied() == INDEX[absent],
              f"{names} with WAVE replacing {sr.MODEL_NAMES[absent]} (no slot on it): identical to stock, nothing "
              "switched")

    def booted_acd(r):
        r.pattern()
        if r.patched:
            r.swap(ACD)
    (a, ra), (b, _) = run_pair(ptf, stock, models, polys, booted_acd, seed=4)
    ds, dv = compare(a, b, slots=(0, 1))
    check(ds == 0 and dv == 0 and peak(a, (0, 1)) > 1000 and ra.model(2) == WAVE and ra.switches() == 1,
          f"WAVE replacing ACD: the ACD slot became WAVE, the two VAP slots identical to stock "
          f"(largest differences {ds}, {dv})")


# ---- 4. the WAVE model --------------------------------------------------------------------

WAVE_NAMES = ["Formant", "Resonant", "Harmonic", "Square/PWM", "Sin/Tri/Saw/Sqr", "Sync", "FM", "Super Saw", "Saw"]
SRC_NAMES = ["Amp Env", "Filter Env", "Mod Env", "LFO 1", "LFO 2", "Velocity", "Key"]
DEST_NAMES = ["Position", "Position 1", "Position 2", "Pitch", "Pitch 1", "Pitch 2", "Mix", "Cutoff", "Resonance",
              "Volume", "Noise"]
TYPE_NAMES = ["LP Ladder 24", "LP OB 24", "LP OB 12", "LP SVF 24", "LP SVF 12", "HP OB 24", "HP OB 12",
              "HP SVF 24", "HP SVF 12", "BP OB 24", "BP OB 12", "BP SVF 24", "BP SVF 12", "Notch 24", "Notch 12"]
WAVE_INIT = {0: ("Position 1", 0.6667), 1: ("Position 2", 0.6667), 2: ("Wave 1", 4.0), 3: ("Wave 2", 4.0),
             4: ("Mix", 0.0), 5: ("Tune 1", 0.0), 6: ("Tune 2", 0.0), 7: ("Detune", 8.0), 8: ("Finetune", 0.0),
             9: ("Reset 1", 0.0), 10: ("Reset 2", 0.0), 11: ("Type", 0.0), 12: ("Cutoff", 2400.0),
             13: ("Resonance", 0.2), 14: ("Env Amt", 0.15), 15: ("Attack", 0.005), 16: ("Decay", 0.8),
             17: ("Sustain", 0.35), 18: ("Release", 0.5), 19: ("Attack", 0.005), 20: ("Decay", 0.5),
             21: ("Sustain", 0.8), 22: ("Release", 0.4), 23: ("Attack", 0.5), 24: ("Decay", 1.0),
             25: ("Sustain", 0.0), 26: ("Release", 0.5), 27: ("Ratio", 1.0), 28: ("Waveform", 0.0),
             29: ("Retrigger", 2.0), 30: ("Sync", 0.0), 31: ("Ratio", 0.25), 32: ("Waveform", 1.0),
             33: ("Retrigger", 2.0), 34: ("Sync", 0.0), 35: ("Glide Mode", 0.0), 36: ("Glide Time", 0.0),
             37: ("Key Track", 0.5), 38: ("Drive", 0.0), 40: ("Digital", 0.0), 41: ("Noise", 0.0), 42: ("Src 1", 3.0),
             43: ("Dest 1", 0.0), 44: ("Amt 1", 0.0), 45: ("Src 2", 2.0), 46: ("Dest 2", 0.0), 47: ("Amt 2", 0.0),
             60: ("Polarity", 0.0), 61: ("Polarity", 0.0), 62: ("Effect", 0.0), 63: ("Rate", 0.5),
             64: ("Depth", 0.5), 65: ("Mix", 0.5)}
WAVE_GROUPS = [("Oscillator", [("(1/2)", [2, 3, 4, 0, 1, 40, 5, 6, 7, -1]), ("(2/2)", [9, 10, 41, -1, -1, 8, -1, -1])]),
               ("Filter", [("", [12, 13, 11, 14, -1, 37, 38, -1, -1])]),
               ("Modulation", [("Amp Env", [19, 20, 21, 22, -1, -1]),
                               ("Flt Env/LFO 1", [15, 16, 17, 18, 60, 30, 27, 28, 29]),
                               ("Mod Env/LFO 2", [23, 24, 25, 26, -1, 61, 34, 31, 32, 33])]),
               ("Matrix", [("(1/2)", list(range(42, 51))), ("(2/2)", list(range(51, 60)) + [-1])]),
               ("FX", [("", [])]),
               ("Voice", [("", [35, 36, 39, 62, 63, 64, 65])])]
WAVE_PAGES = [("Oscillators", "Osc", [2, 0, 3, 1, 4, 5, 6, 8, 7, 41, 40, 9, 10]), ("Filter", "", [12, 13, 11, 14, 37, 38]),
              ("Amp Envelope", "Amp Env", [19, 20, 21, 22]), ("Filter Envelope", "Flt Env", [15, 16, 17, 18]),
              ("Mod Envelope", "Mod Env", [23, 24, 25, 26]), ("LFO 1", "", [30, 27, 28, 29, 60]),
              ("LFO 2", "", [34, 31, 32, 33, 61]), ("Modulation", "Mod", list(range(42, 60))),
              ("Voice", "", [35, 36, 39, 62, 63, 64, 65])]
WAVE_MACROS = [("Attack", [19]), ("Decay/Release", [20, 16, 22, 18]), ("Wave Position", [0, 1]),
               ("Filter Cutoff", [12]), ("Filter Resonance", [13]), ("Filter Env Amount", [14]), ("Velocity", [39]),
               ("Pressure", [])]


def container_layout(m, c: int):
    groups = []
    for g in range(m.r32(c + 0x44), m.r32(c + 0x48), 0x48):
        subs = []
        for s_ in range(m.r32(g + 0x30), m.r32(g + 0x34), 0x24):
            cells = [struct.unpack("<ii", bytes(m.uc.mem_read(x, 8)))[1] for x in range(m.r32(s_ + 0x18), m.r32(s_ + 0x1C), 8)]
            subs.append((sstr(m, s_), cells))
        groups.append((sstr(m, g), subs))
    pages = []
    for q in range(m.r32(c + 0x50), m.r32(c + 0x54), 0x48):
        cells = [struct.unpack("<ii", bytes(m.uc.mem_read(x, 8)))[1] for x in range(m.r32(q + 0x3C), m.r32(q + 0x40), 8)]
        pages.append((sstr(m, q), sstr(m, q + 0x18), cells))
    macros = []
    for k in range(m.r32(c + 0x0C), m.r32(c + 0x10), 0x28):
        t = [struct.unpack("<if", bytes(m.uc.mem_read(x, 8))) for x in range(m.r32(k + 0x18), m.r32(k + 0x1C), 8)]
        macros.append((sstr(m, k), [i for i, _ in t], [a for _, a in t]))
    return groups, pages, macros


# the patch's per-parameter arrays (patch offset, bytes per parameter), sized when the patch
# is made from the count its builder leaves (ITCM 0x1d77a)
PATCH_ARRAYS = {"parameters": (0x54, 0x38), "values": (0x0C, 4), "+0x30": (0x30, 4), "+0x3c": (0x3C, 32),
                "+0x48": (0x48, 1)}


def arrays(m, p: int) -> dict[str, int]:
    return {k: (m.r32(p + off + 4) - m.r32(p + off)) // n for k, (off, n) in PATCH_ARRAYS.items()}


def pb_fields(b: bytes) -> list[tuple[int, bytes]]:
    """A protocol buffer's top-level fields: (number, raw bytes)."""
    def varint(i):
        v = sh = 0
        while True:
            c = b[i]
            i += 1
            v |= (c & 0x7F) << sh
            sh += 7
            if not c & 0x80:
                return v, i
    i, out = 0, []
    while i < len(b):
        st = i
        tag, i = varint(i)
        if tag & 7 == 0:
            _, i = varint(i)
        elif tag & 7 == 2:
            n, i = varint(i)
            i += n
        else:
            i += 4 if tag & 7 == 5 else 8
        out.append((tag >> 3, b[st:i]))
    return out


def pb_values(b: bytes) -> list[float]:
    """A patch file's values (field 6, {1: float} each)."""
    return [struct.unpack("<f", raw[-4:])[0] for f, raw in pb_fields(b) if f == 6]


def pb_with_values(b: bytes, vals: list[float]) -> bytes:
    """The file with its values replaced by vals."""
    fs = pb_fields(b)
    first = next(k for k, (f, _) in enumerate(fs) if f == 6)
    six = b"".join(bytes([0x32, 5, 0x0D]) + struct.pack("<f", v) for v in vals)
    return (b"".join(raw for f, raw in fs[:first] if f != 6) + six
            + b"".join(raw for f, raw in fs[first:] if f != 6))


def display(m, entry: int, value: float) -> str:
    buf, val = m.alloc(0x40), m.alloc(8)
    m.write(buf, bytes(0x40))
    m.write(val, struct.pack("<f", value))
    m.call(m.r32(entry + 0x34), buf, entry + 0x28, 0, val, max_insns=2_000_000)
    return sstr(m, buf)


def wave_model_checks(ptf: Path) -> None:
    print("the WAVE model (model 3: VAP's builder, rewritten)")
    r = Rig(ptf)
    m = r.m
    r.configure((VAP, PERC, PERC), (2, 1, 1))
    vap_ranges = r.ranges(0)
    r.swap(VAP)
    p = r.patch_obj(0)
    c = p + 0x54
    ents = r._entries(0)
    check(r.model(0) == WAVE and m.r32(c + 0x24) == WAVE and len(ents) == 66 and m.r32(c + 0x28) == 2
          and m.r32(c + 0x40) == 0x7FFFFFFF and all(m.r32(v) == sr.VOICE_VT[VAP] for v in r.voices(0)),
          f"slot 0 on model {r.model(0)}: a patch with model id {m.r32(c + 0x24)}, {len(ents)} parameters, "
          f"{m.r32(c + 0x28)} output channels (stereo, as PERC), no polyphony cap, VAP voice objects")
    vals, defaults = r.params(0), r.defaults(0)
    bad = [(i, sstr(m, ents[i]), vals[i], defaults[i]) for i, (n, v) in WAVE_INIT.items()
           if sstr(m, ents[i]) != n or abs(vals[i] - v) > 1e-6 * max(1.0, abs(v)) or defaults[i] != vals[i]]
    check(not bad, f"WAVE's names and Init values, as the patch's values and its defaults ({len(WAVE_INIT)} "
                   f"parameters; wrong: {bad})")
    ranges = r.ranges(0)
    flags = [m.r32(q + 0x20) for q in ents]
    srcs, dests = range(42, 60, 3), range(43, 60, 3)
    check(ranges[2] == ranges[3] == (0.0, 8.0) and flags[2] == flags[3] == flags[11] and (flags[2] >> 16) & 1
          and all(ranges[i] == (0.0, 6.0) for i in srcs) and all(ranges[i] == (0.0, 10.0) for i in dests)
          and all(ranges[i] == vap_ranges[i] for i in range(60) if i not in (2, 3, 38, *srcs, *dests)),
          f"Wave 1 and 2: 0-8, stepped, as Type; Src 0-6, Dest 0-10; every other range as VAP's "
          f"(Wave {ranges[2]}, flags {flags[2]:#x})")
    shown_src = [display(m, ents[42], float(k)) for k in range(7)]
    shown_dest = [display(m, ents[58], float(k)) for k in range(11)]
    check(shown_src == SRC_NAMES and shown_dest == DEST_NAMES and display(m, ents[28], 4.0) == "Square"
          and display(m, ents[33], 1.0) == "One Shot",
          f"the Mod Matrix's displays: Src {shown_src}, Dest {shown_dest}; the LFOs keep VAP's")
    shown_wave = [display(m, ents[2], float(k)) for k in range(9)]
    shown_type = [display(m, ents[11], float(k)) for k in range(15)]
    clamped = [display(m, ents[2], v) for v in (-3.0, 19.0, float("nan"))] + [display(m, ents[11], 99.0)]
    check(shown_wave == WAVE_NAMES and shown_type == TYPE_NAMES
          and [display(m, ents[40], v) for v in (0.0, 1.0)] == ["Off", "On"],
          f"value displays: Wave {shown_wave}, Type {shown_type[:3]}..., Digital Off/On")
    check(clamped == ["Formant", "Saw", "Formant", "Notch 12"],
          f"out-of-range values show the nearest name ({clamped})")
    drive = [display(m, ents[P_DRIVE], v) for v in (0.0, 0.35, 1.0, 7.0, -1.0, float("nan"))]
    check(ranges[P_DRIVE] == (0.0, 1.0) and drive == ["0 %", "35 %", "100 %", "100 %", "0 %", "0 %"]
          and m.uc.mem_read(ents[P_DRIVE] + 0x23, 1)[0] == 0xFF,
          f"Drive (38, VAP's unused Mix Env Amt): 0-1, shown as a percentage ({drive}), no CC")
    pol = [[display(m, ents[i], v) for v in (0.0, 1.0, 5.0, float("nan"))] for i in (P_LFO1_POL, P_LFO2_POL)]
    rebuild = [m.uc.mem_read(q + 0x24, 1)[0] for q in ents]
    check(ranges[60] == ranges[61] == (0.0, 1.0) and flags[60] == flags[61] == flags[11]
          and pol == [["Bipolar", "Unipolar", "Unipolar", "Bipolar"]] * 2 and rebuild[60] == rebuild[61] == 0,
          f"Polarity (60, 61): 0-1, stepped with no CC as Type, Bipolar/Unipolar ({pol[0]}), no rebuild on change")
    eff = [display(m, ents[P_EFFECT], v) for v in (0.0, 1.0, 5.0, -2.0, float("nan"))]
    rate = [display(m, ents[P_FX_RATE], v) for v in (0.0, 0.5, 1.0, 2.0, float("nan"))]
    pct = [[display(m, ents[i], v) for v in (0.0, 0.5, 1.0)] for i in (P_FX_DEPTH, P_FX_MIX)]
    ccs = [m.uc.mem_read(ents[i] + 0x23, 1)[0] for i in range(P_EFFECT, P_FX_MIX + 1)]
    check(all(ranges[i] == (0.0, 1.0) for i in range(P_EFFECT, P_FX_MIX + 1)) and flags[P_EFFECT] == flags[11]
          and flags[P_FX_RATE] == flags[P_FX_DEPTH] == flags[P_FX_MIX] == flags[P_DRIVE]
          and not any(rebuild[P_EFFECT:P_FX_MIX + 1]) and ccs == [0xFF] * 4
          and eff == ["None", "Chorus", "Chorus", "None", "None"]
          and rate == ["0.05 Hz", "0.50 Hz", "5.00 Hz", "5.00 Hz", "0.05 Hz"]
          and pct == [["0 %", "50 %", "100 %"]] * 2,
          f"the effect (62-65): 0-1 each; Effect stepped as Type ({eff}); Rate, Depth, Mix plain as Drive "
          f"(Rate {rate}, Depth/Mix {pct[0]}); no CC, no rebuild on change")
    check(arrays(m, p) == {k: 66 for k in PATCH_ARRAYS},
          f"the patch's per-parameter arrays, sized from the builder's count: {arrays(m, p)}")
    room = (m.r32(c + 8) - m.r32(c)) // 0x38
    check(room == 66, f"the parameter vector's room: {room} entries (66: no doubling to 120, 6.7 KB, when "
                      "WAVE's six are added)")
    caps = {k: m.r32(p + off + 8) for k, (off, _) in PATCH_ARRAYS.items()}
    sync = [i for i, x in enumerate(rebuild) if x]
    r.set_param(0, P_LFO1_SYNC, 1.0)
    setv = m.r32(m.r32(p) + 0x10)
    val = m.alloc(8)
    for v in (1.0, 0.0):                        # Sync: the patch is rebuilt (VAP's builder, then WAVE's)
        m.write(val, struct.pack("<f", v))
        m.call(setv, p, P_LFO1_SYNC, val, 2, max_insns=50_000_000)
    check(sync == [30, 34] and len(r._entries(0)) == 66 and arrays(m, p) == {k: 66 for k in PATCH_ARRAYS}
          and {k: m.r32(p + off + 8) for k, (off, _) in PATCH_ARRAYS.items()} == caps and m.r32(c + 0x24) == WAVE
          and display(m, r._entries(0)[60], 1.0) == "Unipolar" and container_layout(m, c)[1] == WAVE_PAGES,
          f"a rebuild (set_value on Sync, the entries {sync} that rebuild): still 66 parameters, the arrays as "
          "they were (the per-parameter loops stay inside them), model 3, the Edit Patch pages as before")
    groups, pages, macros = container_layout(m, c)
    check(groups == WAVE_GROUPS, f"the screen: {[g for g, _ in groups]} with WAVE's cells")
    check(pages == WAVE_PAGES, f"the Edit Patch screen's pages (each LFO's Polarity last on its page, the effect "
                               f"on Voice): "
                               f"{[(q[0], q[1]) for q in pages]}")
    check(pages[3][2] == [P_FENV_ATT, P_FENV_DEC, P_FENV_SUS, P_FENV_REL]
          and pages[4][2] == [P_MENV_ATT, P_MENV_DEC, P_MENV_SUS, P_MENV_REL],
          f"the Env 1 tile (page 4) holds the filter envelope the engine follows ({pages[3][2]}), as VAP's Env 1 "
          f"holds VAP's; Env 2 the mod envelope ({pages[4][2]})")
    check([(n, t) for n, t, _ in macros] == WAVE_MACROS and all(a == 1.0 for _, _, am in macros for a in am),
          f"the Macros: {[(n, t) for n, t, _ in macros]}")
    used = {x for _, subs in groups for _, cells in subs for x in cells if x >= 0} | \
           {x for *_, cells in pages for x in cells if x >= 0} | {x for _, t, _ in macros for x in t}
    check(used <= ENGINE_READS, f"every parameter the screen, the pages and the Macros show is one the engine or the "
                                f"effect reads "
                                f"({sorted(used - ENGINE_READS) or 'none other'})")
    check(r.title(0) == "Synth 1 (WAVE)" and r.title(1) == "Synth 2 (PERC)",
          f"the instrument title: {r.title(0)!r}, the next slot {r.title(1)!r}")
    heap = []
    for k in range(50):
        r.set_model(0, VAP if k % 2 == 0 else WAVE)
        if k % 10 == 9:
            heap.append(r.heap_used())
    check(no_growth(heap) and r.model(0) == WAVE,
          f"50 switches between VAP and WAVE: heap use {heap} (no growth; stock moves a few bytes either way)")


# ---- 5. the swap --------------------------------------------------------------------------

def swap_checks(ptf: Path) -> None:
    print("the swap (Config update)")
    r = Rig(ptf)
    m = r.m
    r.configure((VAP, ACD, VAP), (3, 1, 2))
    r.pattern()
    v0, acd = r.voices(0) + r.voices(2), r.voices(1)
    r.created.clear()
    r.deleted.clear()
    r.pushes.clear()
    r.sets.clear()
    r.irq.clear()
    r.swap(VAP)
    v1 = r.voices(0) + r.voices(2)
    check(r.table() == [1, 2, WAVE, 5, 6] and r.applied() == INDEX[VAP] and r.model(0) == r.model(2) == WAVE
          and r.model(1) == ACD and r.voices(1) == acd and r.switches() == 2,
          f"WAVE replacing VAP: the table {r.table()}, the two VAP slots now WAVE, the ACD slot untouched, "
          f"{r.switches()} switches")
    check(sorted(r.deleted) == sorted(v0) and len(r.created) == 5 and r.patch_model(0) == r.patch_model(2) == WAVE,
          f"5 VAP voices deleted, 5 made with WAVE patches ({len(r.created)} made)")
    check(sorted(e[0] for e in r.entries()) == sorted(v1)
          and sorted(e[1] for e in r.entries()) == [0, 0, 0, 2, 2], "an engine voice for each WAVE voice")
    sets = {v: sum(1 for x, _ in r.sets if x == v) for v in v1}
    check(all(n == 66 for n in sets.values()) and not any(x in v1 for x, _ in r.pushes),
          f"every parameter pushed to each new voice reaches the patch ({sorted(set(sets.values()))}) and none "
          "reaches VAP's set_param")
    pc = r.param_copy(0)
    check(abs(pc[12] - 2400.0) < 1e-3 and abs(pc[21] - 0.8) < 1e-6 and abs(pc[2] - 4.0) < 1e-6,
          f"the slot's parameter copy holds WAVE's Init values (Cutoff {pc[12]:.0f}, amp Sustain {pc[21]:.2f}, "
          f"Wave 1 {pc[2]:.0f})")
    calls = r.irq.count("D")
    check(r.irq.count("E") == calls and m.r32(NVIC_ISER) & 1 and all(r.irq[k] != r.irq[k + 1] for k in range(len(r.irq) - 1)),
          f"the audio interrupt: off and on once per stock critical section, never on inside one, on at the end "
          f"({''.join(r.irq)})")
    n = r.switches()
    r.config()
    r.pattern()
    check(r.switches() == n, "nothing switches while the choice stays")
    r.created.clear()
    r.swap(None)
    check(r.table() == [1, 2, 4, 5, 6] and r.model(0) == r.model(2) == VAP and r.patch_model(0) == VAP
          and r.entries() == [] and len(r.created) == 5,
          "Off: the table stock, both slots VAP again (fresh voices), engine voices freed")
    r.swap(ACD)
    check(r.model(1) == WAVE and r.model(0) == r.model(2) == VAP and len(r.entries()) == 1,
          "WAVE replacing ACD: only the ACD slot becomes WAVE")
    r.pick(0, ACD)
    check(r.model(0) == WAVE and len(r.entries()) == 4, "picking ACD in the Model list now gives WAVE")
    r.pick(0, VAP)
    check(r.model(0) == VAP and len(r.entries()) == 1, "and VAP again: VAP, its engine voices freed")
    r.swap(None)
    r.swap(VAP)
    r.note_on(0, 60, 100)
    r.render(4)
    heap, e0 = [r.heap_used()], len(r.entries())
    for k in range(200):
        r.swap(None if k % 2 == 0 else VAP)
        r.note_on(0, 48 + k % 24, 100)
        r.note_on(1, 50 + k % 20, 90)
        r.render(2)
        r.note_off(1, 50 + k % 20)
        if k % 40 == 39:
            heap.append(r.heap_used())
    r.render(4)
    check(no_growth(heap) and len(r.entries()) == e0,
          f"200 swaps with notes held: heap use {heap} (no growth), engine voices {e0} -> {len(r.entries())}")


# ---- 6. Config menu ---------------------------------------------------------------------

def menu_checks(ptf: Path) -> None:
    print("Config menu: Project Settings > Synth Engines > WAVE replaces")
    m = Machine(ptf)
    m.stub_function(ATEXIT, lambda mm: 0)
    m.call(MENU_CTOR, max_insns=50_000_000)
    ps = PROJECT_SETTINGS
    cap, count = m.r32(ps + 8) & 0xFF, m.r32(ps + 0x1C) >> 8 & 0xFF
    kids = [m.r32(m.r32(ps + 0xC) + 4 * i) for i in range(min(cap, count))]
    labels = [m.cstr(m.r32(k + (0x18 if m.r32(k) & 0xFF == 1 else 4))) for k in kids]
    check(cap == count and count >= 11, f"Project Settings: room for {cap}, holds {count}")
    check(labels[:10] == STOCK_ITEMS, "the 10 stock items are unchanged, in order")
    if "Synth Engines" not in labels[10:]:
        check(False, f"a 'Synth Engines' folder after the stock items ({labels[10:]})")
        return
    at = labels.index("Synth Engines", 10)
    folder = kids[at]
    fcap, fcount = m.r32(folder + 8) & 0xFF, m.r32(folder + 0x1C) >> 8 & 0xFF
    items = [m.r32(m.r32(folder + 0xC) + 4 * i) for i in range(min(fcap, fcount))]
    names = [m.cstr(m.r32(i + 4)) for i in items]
    check(m.r32(folder) & 0xFF == 1 and fcap == fcount == 2 and names == ["WAVE replaces", "PHASE replaces"],
          f"item {at + 1} is the folder 'Synth Engines' with {names} (patch entries: {labels[10:]})")
    m.stub_function(MENU_REDRAW, lambda mm: 0)
    m.w32(MENU_CURRENT, ps)
    m.w8(ps + 0x1C, at)
    m.call(MENU_SELECT)
    check(m.r32(MENU_CURRENT) == folder, "the stock select opens the folder")
    closed = []
    m.stub_function(MENU_EDITOR_CLOSE, lambda mm: closed.append(1))
    item = items[0]
    sy8 = ram_map(m, ptf, SY8_FIELDS)
    addr = WHERE["state"] + WHERE["menu"]
    pos = sy8["state"] + sy8["menu_pos"]
    desc = m.r32(item + 0x10)
    cb = m.r32(desc + 12)
    opts = [m.cstr(m.r32(m.r32(desc + 8) + 4 * i)) for i in range(m.r32(desc + 4))]
    check(m.r32(item + 0xC) & 0xFF == 4 and m.r32(desc) == pos and opts == MENU_NAMES
          and CODE[0] <= cb < CODE[1] and cb & 1 and m.cstr(m.r32(item + 8)) != ""
          and m.uc.mem_read(addr, 1)[0] == 0 and m.uc.mem_read(pos, 1)[0] == 0,
          f"'WAVE replaces': an option item {opts} on the patch's menu position, with help text and an apply "
          "callback in the patch, starting at Off")
    for value in (3, 5, 0):
        m.w8(folder + 0x1C, 0)
        n = len(closed)
        m.call(MENU_APPLY, 0, value, max_insns=2_000_000)
        check(m.uc.mem_read(pos, 1)[0] == value and m.uc.mem_read(addr, 1)[0] == value and len(closed) == n + 1,
              f"Apply {MENU_NAMES[value]} (PHASE Off: the full list): stored, the choice set, editor closed")


# ---- 7. the Model list ------------------------------------------------------------------

def screen_globals(r: Rig) -> None:
    """The app objects the synth settings screen reads (FINDINGS section 23)."""
    m = r.m
    m.w32(0x20027D18, r.fake("clock", 8))
    m.w32(0x200209E4, sr.S)
    title = m.alloc(0x100)
    m.write(title, bytes(0x100))
    m.w8(title + 2, 0x7F)                   # no control: its disp_ctrl_* calls do nothing
    m.w32(title + 0x60, sr.S)
    m.w32(0x200209F0, title)
    m.w8(0x20204000 + 0x6C5, 64)            # the selected instrument: S1

    def dl_string(mm):                      # the display's string write (vtable +0x40)
        raw = mm.cstr(mm.arg(1)).encode("latin-1") + bytes(1)
        raw += bytes(-len(raw) % 4)
        for k in range(0, len(raw), 4):
            mm.display_list.append(struct.unpack_from("<I", raw, k)[0])
        return len(raw)
    m.w32(FAKE_DISPLAY_VT + 0x40, r.add_stub(dl_string))


def list_draw(r: Rig, ids: int, scroll: int = 0, rows: int = 5) -> tuple[list, list]:
    """A list control (0x60105fac) drawing 5 items from `ids`: the BITMAPS vertices and the
    texts (CMD_TEXT: x, y, string)."""
    m = r.m
    ctrl, params, data = m.alloc(0x60), m.alloc(0x40), m.alloc(0x40)
    for a, n in ((ctrl, 0x60), (params, 0x40), (data, 0x40)):
        m.write(a, bytes(n))
    m.call(LIST_CTOR, ctrl, params, max_insns=1_000_000)
    m.write(data, struct.pack("<HHHH", 0x32, 5, rows, 0))
    m.w32(data + 0x18, ids)
    m.call(m.r32(m.r32(ctrl) + 0x24), ctrl, data, max_insns=1_000_000)     # set_data
    m.write(ctrl + 0x1C, struct.pack("<hhhh", 10, 40, 100, 200))
    m.w32(ctrl + 0x3C, scroll)
    m.display_list.clear()
    m.call(m.r32(m.r32(ctrl)), ctrl, max_insns=5_000_000)                    # draw
    dl = list(m.display_list)
    verts, texts = [], []
    for k, w in enumerate(dl):
        if w == 0x1F000001 and k + 1 < len(dl) and dl[k + 1] >> 30 == 1:      # BEGIN(BITMAPS), VERTEX2F
            v = dl[k + 1]
            verts.append(((v >> 15) & 0x7FFF, v & 0x7FFF))
        if w == 0xFFFFFF0C:
            raw = b"".join(struct.pack("<I", x) for x in dl[k + 3:k + 8])
            texts.append((dl[k + 1] & 0xFFFF, dl[k + 1] >> 16, raw.split(b"\0")[0].decode("latin-1")))
    return verts, texts


def model_list_checks(ptf: Path) -> None:
    print("the Model list (synth settings screen)")
    r = Rig(ptf)
    m = r.m
    r.configure((VAP, ACD, PERC), (1, 1, 1))
    r.pattern()
    screen_globals(r)
    m.call(SCREEN_SETUP, SCREEN, max_insns=50_000_000)
    check(m.r32(SCREEN + 0x114) == STOCK_LIST_IDS, "WAVE off: the list gets the stock logo ids (0x60061488)")
    stock_verts, stock_texts = list_draw(r, STOCK_LIST_IDS)
    check(len(stock_verts) == 5 and stock_texts == [], "the stock list draws 5 logos, no text")
    table = m.r32(IMAGE_TABLE_LIT)
    ids = [9, 10, 11, 12, 24]
    centres = []
    for (x, y), i in zip(stock_verts, ids):
        w, h = struct.unpack("<HH", bytes(m.uc.mem_read(table + 0x14 * i + 0xC, 4)))
        centres.append((x + w // 2, y + h // 2))
    for md in MODELS:
        r.swap(md)
        m.call(SCREEN_SETUP, SCREEN, max_insns=50_000_000)
        want = [LIST_TEXT_ID if k == INDEX[md] else i for k, i in enumerate(ids)]
        got_ptr = m.r32(SCREEN + 0x114)
        verts, texts = list_draw(r, got_ptr)
        cx, cy = centres[INDEX[md]]
        ok = (got_ptr == r.st("model_ids") and r.model_ids() == want and len(verts) == 4
              and [v for k, v in enumerate(stock_verts) if k != INDEX[md]] == verts
              and len(texts) == 1 and texts[0][2] == "WAVE" and abs(texts[0][0] - cx) <= 1 and abs(texts[0][1] - cy) <= 1)
        check(ok, f"WAVE replacing {sr.MODEL_NAMES[md]}: the patch's ids {r.model_ids()}, 4 logos as stock and "
                  f"'WAVE' at {texts[0][:2] if texts else None} (the logo's centre {cx, cy})")
    r.swap(PERC)
    verts, texts = list_draw(r, r.st("model_ids"), scroll=2, rows=3)
    check(len(verts) == 2 and len(texts) == 1 and texts[0][2] == "WAVE",
          f"scrolled by 2 with 3 rows (VAP, WTFM, PERC shown): 2 logos and 'WAVE' in the last row ({texts})")
    r.swap(None)
    m.call(SCREEN_SETUP, SCREEN, max_insns=50_000_000)
    check(m.r32(SCREEN + 0x114) == STOCK_LIST_IDS, "Off again: the stock ids")


# ---- 8. settings file -------------------------------------------------------------------

def fnv1a(data: bytes) -> int:
    h = 2166136261
    for b in data:
        h = (h ^ b) * 16777619 & 0xFFFFFFFF
    return h


def hsy2(value: int, magic: bytes = FILE_MAGIC) -> bytes:
    data = bytes([value, 0, 0, 0])
    return magic + data + struct.pack("<I", fnv1a(data))


def file_checks(ptf: Path, stock: Path) -> None:
    print("settings file (synths.bin): project load and the screens' update hooks")
    r = Rig(ptf)
    m = r.m
    fs = FakeFS(m, Path("."))
    r.configure((VAP, ACD, PERC), (1, 1, 1))
    applied_with = []
    m.stub_function(APPLY_RECORDS, lambda mm: applied_with.append((mm.arg(0), mm.arg(1), mm.arg(2), r.table())))
    image = bytes(m.uc.mem_read(0x60000000, 0x800000))
    project_hook = bl_target(image, 0x60000000, BL_RECORDS)

    def load(name):
        fs.set_project(Path("."), name, "Projects/User")
        applied_with.clear()
        m.call(project_hook | 1, sr.S, RECORDS, 1, max_insns=50_000_000)

    def tick(n=1, slot=PATTERN_UPDATE_SLOT):
        for _ in range(n):
            m.call(m.r32(slot), 0x20021770, max_insns=200_000_000)

    def path(name):
        return f"Projects/User/{name}/synths.bin"

    def writes():
        return len([x for x in fs.log if "synths.bin mode 3" in x])

    load("A")
    check(r.menu() == 0 and r.table() == [1, 2, 4, 5, 6] and r.loads() == 1
          and applied_with == [(sr.S, RECORDS, 1, [1, 2, 4, 5, 6])],
          "a project without synths.bin: Off; the stock record apply runs with the same arguments")
    tick(SAVE_DELAY + 5)
    check(writes() == 0, "nothing written while nothing changed")
    r.swap(VAP, apply=False)
    tick(SAVE_DELAY - 2, CONFIG_UPDATE_SLOT)
    check(writes() == 0 and r.model(0) == WAVE, "the choice applies at once; no write right after it")
    tick(5, CONFIG_UPDATE_SLOT)
    check(writes() == 1 and fs.overlay.get(path("A")) == hsy2(3),
          "the change is written once, a while later (HSY2, 12 bytes: VAP)")
    tick(SAVE_DELAY * 2)
    check(writes() == 1, "no rewrite while nothing changes")
    load("B")
    check(r.menu() == 0 and applied_with and applied_with[0][3] == [1, 2, 4, 5, 6],
          "another project without the file: Off, the stock table when its records are applied")
    load("A")
    check(r.menu() == 3 and applied_with and applied_with[0][3] == [1, 2, WAVE, 5, 6],
          "back to the first project: VAP replaced, in the table before its records are applied")
    good = fs.overlay[path("A")]
    for label, data in (("a damaged file", good[:11] + bytes([good[11] ^ 1])), ("an sy1 file (HSY1)", hsy2(1, b"HSY1")),
                        ("a value out of range", hsy2(6)), ("a short file", good[:8])):
        fs.overlay[path("D")] = data
        n = writes()
        load("D")
        tick(SAVE_DELAY * 2)
        check(r.menu() == 0 and writes() == n and fs.overlay[path("D")] == data, f"{label}: Off, and the file is kept")
    fs.overlay[path("G")] = hsy2(5)
    load("G")
    check(r.menu() == 5 and applied_with[0][3] == [1, 2, 4, 5, WAVE], "a file with PERC replaced")
    fs.set_project(Path("."), "H", "Projects/User")        # a new name, no load, no save request
    tick(SAVE_DELAY + 5)
    check(r.menu() == 5 and fs.overlay.get(path("H")) == hsy2(5),
          "a new name without a file (no load): the choice kept and written to its folder")
    fs.overlay[path("K")] = hsy2(2)
    fs.set_project(Path("."), "K", "Projects/User")        # a project the patch didn't see load
    n = writes()
    tick(SAVE_DELAY * 2)
    check(r.menu() == 2 and writes() == n and fs.overlay[path("K")] == hsy2(2),
          "a project with its own file that the patch didn't see load: its file wins, nothing overwritten")
    m.write(PROJECT_NAME, b"\0")
    tick()
    r.swap(FAT, apply=False)
    n = writes()
    tick(SAVE_DELAY * 2)
    check(writes() == n, "project without a name: nothing written")
    fs.set_project(Path("."), "C", "Projects/User")
    tick(SAVE_DELAY + 5)
    check(r.menu() == 2 and fs.overlay.get(path("C"), b"") == hsy2(2), "first save of a new project writes its choice")
    sizes = fs.read_sizes + fs.write_sizes
    check(max(sizes) <= 256, f"file reads and writes of at most 256 bytes ({sorted(set(sizes))})")
    s = Rig(stock, patched=False)
    sfs = FakeFS(s.m, Path("."))
    s.configure((VAP, PERC, PERC), (1, 1, 1))
    sfs.put(path("P"), hsy2(3))
    sfs.set_project(Path("."), "P", "Projects/User")
    s.m.call(s.m.r32(PATTERN_UPDATE_SLOT), 0x20021770, max_insns=200_000_000)
    s.m.call(s.m.r32(CONFIG_UPDATE_SLOT), 0x20021770, max_insns=200_000_000)
    check([x for x in sfs.log if "synths.bin" in x] == [] and s.model(0) == VAP,
          "stock firmware: a WAVE project's synths.bin is never opened, and the slot is VAP")


def hsy2_both(wave: int, phase: int) -> bytes:
    data = bytes([wave, phase, 0, 0])
    return FILE_MAGIC + data + struct.pack("<I", fnv1a(data))


def save_as_checks(ptf: Path) -> None:
    print("Save As (synths.bin, the firmware's save request 0x60114e34)")
    r = Rig(ptf)
    m = r.m
    fs = FakeFS(m, Path("."))
    r.configure((VAP, ACD, PERC), (1, 1, 1))
    m.stub_function(APPLY_RECORDS, lambda mm: 0)
    image = bytes(m.uc.mem_read(0x60000000, 0x800000))
    project_hook = bl_target(image, 0x60000000, BL_RECORDS)
    ph_menu = ram_map(m, ptf, SY8_FIELDS)["ph_menu"] + WHERE["state"]
    settle = SAVE_DELAY * 2 + 5

    def load(name):
        fs.set_project(Path("."), name, project_save.WHERE)
        m.call(project_hook | 1, sr.S, RECORDS, 1, max_insns=50_000_000)

    def tick(n=1):
        for _ in range(n):
            m.call(m.r32(PATTERN_UPDATE_SLOT), 0x20021770, max_insns=200_000_000)

    def path(name):
        return f"{project_save.WHERE}/{name}/synths.bin"

    def get():
        return r.menu(), m.uc.mem_read(ph_menu, 1)[0]

    def put(wave, phase):
        m.w8(r.st("menu"), wave)
        m.w8(ph_menu, phase)

    def writes():
        return len([x for x in fs.log if "synths.bin mode 3" in x])

    def save(name, settle=settle):
        return project_save.save(m, tick, name, settle=settle)

    a, b = (1, 5), (2, 4)                   # WAVE replaces ACD, PHASE PERC; FAT and WTFM
    load("A0")
    put(*a)
    fs.set_project(Path("."), "New Project", "")    # as the firmware's new project 0x60117c8c
    log0 = len(fs.log)
    tick(settle)
    check(get() == a and not any("synths" in x for x in fs.log[log0:]),
          "a new project (\"New Project\", no path): the choices kept, nothing read or written")
    ok = save("SA1")
    check(ok and m.cstr(PROJECT_NAME) == "SA1" and get() == a and fs.overlay.get(path("SA1")) == hsy2_both(*a),
          "its first save (a Save As): the choices kept and written to its folder")
    ok = save("SA2")
    check(ok and get() == a and fs.overlay.get(path("SA2")) == hsy2_both(*a)
          and fs.overlay.get(path("SA1")) == hsy2_both(*a),
          "Save As from a saved project: the choices kept, written to the new folder, the old file left")
    fs.put(path("SA3"), hsy2_both(*b))
    ok = save("SA3")
    check(ok and get() == a and fs.overlay.get(path("SA3")) == hsy2_both(*a),
          "Save As over a project that has its own synths.bin: the saved project's choices, written over it")
    n = writes()
    ok = save(None)
    check(ok and m.cstr(PROJECT_NAME) == "SA3" and get() == a and writes() == n,
          "a plain Save: the same project, the choices kept, nothing written")
    load("SA1")
    load("SA3")
    check(get() == a, "the overwritten project opened again: the saved project's choices")
    put(0, 0)
    tick(settle)
    fs.put(path("SA4"), hsy2_both(*b))
    ok = save("SA4")
    check(ok and get() == (0, 0) and fs.overlay.get(path("SA4")) == hsy2_both(0, 0),
          "Save As with both Off over a project that has its own synths.bin: Off written over it")
    put(*a)
    tick(settle)
    long_name = "L" * 40
    ok = save(long_name)
    kept = m.cstr(PROJECT_NAME)
    check(ok and get() == a and fs.overlay.get(path(kept)) == hsy2_both(*a),
          f"Save As to a {len(long_name)}-character name (the firmware keeps {len(kept)}): the choices kept and written")
    fs.put(path("SA5"), hsy2_both(*b))
    n = writes()
    save("SA5", settle=0)
    m.w8(project_save.FM_STATE, 0x0D)       # the save still running
    tick(settle)
    check(get() == a and writes() == n and fs.overlay.get(path("SA5")) == hsy2_both(*b),
          "after a Save As, while the file manager is busy: nothing read or written")
    m.w8(project_save.FM_STATE, 0)
    tick()
    check(get() == a and writes() == n + 1 and fs.overlay.get(path("SA5")) == hsy2_both(*a),
          "once it is idle: the choices written to the new folder")
    sizes = fs.read_sizes + fs.write_sizes
    check(max(sizes) <= 256, f"file reads and writes of at most 256 bytes ({sorted(set(sizes))})")


# ---- 9. patch files -----------------------------------------------------------------------

class PatchFS:
    """The patch storages' file system (storage + 0xc): open +0x04, close +0x08, write +0x0c,
    read +0x10, make a folder +0x18; files kept in memory."""

    def __init__(self, r: sr.SynthRig):
        self.m = m = r.m
        self.files: dict[str, bytes] = {}
        self.open_paths: list[tuple[str, int]] = []
        self.pos: dict[int, list] = {}
        self.next = 0x300
        obj, vt = m.alloc(0x40), m.alloc(160)
        m.w32(obj, vt)
        for k in range(40):
            m.w32(vt + 4 * k, r.add_stub(lambda mm, off=4 * k: self.call(mm, off)))
        self.obj = obj
        for st in (m.r32(sr.S + 0x9C), m.r32(sr.S + 0xA4)):     # Patches, Workspace
            m.w32(st + 0xC, obj)

    def call(self, mm, off):
        a = [mm.arg(i) for i in range(4)]
        if off == 0x04:                     # open(&{handle, ok}, fs, &path, mode)
            p = sstr(mm, a[2])
            self.open_paths.append((p, a[3]))
            h = self.next
            self.next += 1
            ok = a[3] != 0 or p in self.files
            if a[3]:
                self.files[p] = b""
            self.pos[h] = [p, 0]
            mm.w32(a[0], h)
            mm.w8(a[0] + 4, 1 if ok else 0)
            return a[0]
        if off == 0x0C:                     # write(fs, handle, n, buf)
            p = self.pos[a[1]][0]
            self.files[p] += bytes(mm.uc.mem_read(a[3], a[2]))
            return a[2]
        if off == 0x10:                     # read(fs, handle, n, buf)
            p, i = self.pos[a[1]]
            chunk = self.files.get(p, b"")[i:i + a[2]]
            mm.write(a[3], chunk)
            self.pos[a[1]][1] = i + len(chunk)
            return len(chunk)
        return 0


def patch_file_checks(ptf: Path, stock: Path) -> None:
    print("patch files (protocol buffers: field 1 is the model id)")
    r = Rig(ptf)
    m = r.m
    r.configure((VAP, PERC, PERC), (1, 1, 1))
    r.swap(VAP)
    r.set_param(0, P_CUTOFF, 1234.0)
    r.set_param(0, P_WAVE1, float(SUPERSAW))
    r.set_param(0, P_LFO2_POL, 1.0)
    r.set_param(0, P_EFFECT, 1.0)
    r.set_param(0, P_FX_MIX, 0.8)
    pfs = PatchFS(r)
    w = m.r32(sr.S + 0x9C)
    ok = m.call(m.r32(m.r32(w) + 0x10), w, sr.S + 0x14, r.patch_obj(0), max_insns=50_000_000)
    data = pfs.files.get("WAVE/Init.patch", b"")
    check(ok == 1 and data[:2] == bytes([0x08, WAVE]) and len(data) > 500,
          f"Save: WAVE/Init.patch, {len(data)} bytes, field 1 = {data[1] if len(data) > 1 else None} (WAVE's id)")
    r2 = Rig(ptf)
    pfs2 = PatchFS(r2)
    r2.configure((VAP, PERC, PERC), (1, 1, 1))
    r2.swap(VAP)
    pfs2.files["0.patch"] = data
    out = r2.m.alloc(0x40)
    got = r2.m.call(SLOT_LOAD, sr.S, 0, out, max_insns=50_000_000)
    vals = r2.params(0)
    saved = pb_values(data)
    fx_saved = [1.0, 0.5, 0.5, 0.8]
    check(len(saved) == 66 and saved[P_LFO1_POL] == 0.0 and saved[P_LFO2_POL] == 1.0
          and np.allclose(saved[62:], fx_saved),
          f"the file holds 66 values, LFO 2's Polarity Unipolar, Chorus at Mix 80 % ({len(saved)}; {saved[60:]})")
    check(got == 1 and r2.patch_model(0) == WAVE and abs(vals[P_CUTOFF] - 1234.0) < 1e-3
          and vals[P_WAVE1] == float(SUPERSAW) and vals[60:62] == [0.0, 1.0] and r2.param_copy(0)[-2:] == [0.0, 1.0]
          and np.allclose(vals[62:], fx_saved),
          f"a WAVE slot loads it back from the project (0.patch): Cutoff {vals[P_CUTOFF]:.0f}, Wave 1 "
          f"{WAVE_NAMES[int(vals[P_WAVE1])]}, Polarity {vals[60:62]} (the engine's copy too), the effect {vals[62:]}")
    for n, what in ((62, "saved before the effect (sy4-sy6)"), (60, "saved before the Polarity switches (sy3)")):
        r2.set_param(0, P_LFO1_POL, 1.0)
        r2.set_param(0, P_EFFECT, 1.0)
        r2.set_param(0, P_FX_RATE, 0.9)
        pfs2.files["0.patch"] = pb_with_values(data, saved[:n])
        got = r2.m.call(SLOT_LOAD, sr.S, 0, out, max_insns=50_000_000)
        vals = r2.params(0)
        pol = [0.0, 1.0] if n == 62 else [0.0, 0.0]
        check(got == 1 and vals[P_CUTOFF] > 1233.0 and vals[60:62] == pol and r2.param_copy(0)[-2:] == pol
              and vals[62:] == FX_DEFAULTS,
              f"a file of {n} values ({what}), into a patch with LFO 1 Unipolar and the Chorus on: Polarity "
              f"{vals[60:62]} ({'from the file' if n == 62 else 'Bipolar'}), the effect {vals[62:]} (None, the "
              "defaults), the rest from the file")
    r2.set_param(0, P_LFO1_POL, 1.0)
    r2.set_param(0, P_EFFECT, 1.0)
    st = r2.m.r32(sr.S + 0xA4)
    path = r2.m.alloc(0x18)
    r2.m.write(path, struct.pack("<II", path + 8, 12) + b"nofile.patch\0\0\0\0")
    got = r2.m.call(r2.m.r32(r2.m.r32(st) + 0x0C), st, path, r2.patch_obj(0), 1, max_insns=50_000_000)
    check(got == 0 and r2.params(0)[60:62] == [1.0, 0.0] and r2.param_copy(0)[-2:] == [1.0, 0.0]
          and r2.params(0)[62:] == [1.0] + FX_DEFAULTS[1:],
          f"a load that fails (no such file): the Polarity switches and the effect as they were "
          f"({r2.params(0)[60:]})")
    s = Rig(stock, patched=False)
    sfs = PatchFS(s)
    s.configure((ACD, PERC, PERC), (1, 1, 1))
    sfs.files["0.patch"] = data
    got = s.m.call(SLOT_LOAD, s.S if hasattr(s, "S") else sr.S, 0, s.m.alloc(0x40), max_insns=50_000_000)
    s.note_on(0, 60, 100)
    y = s.render(4, fast=True)[0][0]
    check(got == 0 and s.model(0) == ACD and s.patch_model(0) == ACD and int(np.abs(y).max()) > 1000
          and ("ACD/Init.patch", 0) in sfs.open_paths,
          f"stock firmware, the same file on an ACD slot: rejected, ACD's Init patch plays (peak {int(np.abs(y).max())})")


# ---- 10. the oscillators ----------------------------------------------------------------

RATE = sr.RATE
P_POS1, P_POS2, P_WAVE1, P_WAVE2, P_MIX, P_TUNE1, P_TUNE2, P_DETUNE, P_FINE, P_RESET1, P_RESET2 = range(11)
P_TYPE, P_CUTOFF, P_RES, P_ENVAMT = 11, 12, 13, 14
P_FENV_ATT, P_FENV_DEC, P_FENV_SUS, P_FENV_REL = range(15, 19)       # the filter envelope
P_AENV_ATT, P_AENV_DEC, P_AENV_SUS, P_AENV_REL = range(19, 23)       # the amp envelope
P_GLIDE_MODE, P_GLIDE_TIME, P_TRACK, P_DRIVE, P_VOLUME, P_DIGITAL, P_NOISE = 35, 36, 37, 38, 39, 40, 41
FORMANT, RESONANT, HARMONIC, PWM, BASIC, SYNC, FM, SUPERSAW, SAW = range(9)    # Wave 1 / 2
P_MENV_ATT, P_MENV_DEC, P_MENV_SUS, P_MENV_REL = range(23, 27)       # the mod envelope
P_LFO1_RATE, P_LFO1_WAVE, P_LFO1_RETRIG, P_LFO1_SYNC = range(27, 31)
P_LFO2_RATE, P_LFO2_WAVE, P_LFO2_RETRIG, P_LFO2_SYNC = range(31, 35)
P_LFO1_POL, P_LFO2_POL = 60, 61                                      # WAVE's own: Bipolar 0, Unipolar 1
P_EFFECT, P_FX_RATE, P_FX_DEPTH, P_FX_MIX = range(62, 66)            # ... and the effect's: None 0, Chorus 1
FX_DEFAULTS = [0.0, 0.5, 0.5, 0.5]
P_SRC1, P_DEST1, P_AMT1, P_SRC2, P_DEST2, P_AMT2 = range(42, 48)
S_AMP, S_FILTER, S_MOD, S_LFO1, S_LFO2, S_VEL, S_KEY = range(7)                     # Src
D_POS, D_POS1, D_POS2, D_PITCH, D_PITCH1, D_PITCH2, D_MIX, D_CUTOFF, D_RES, D_VOL, D_NOISE = range(11)   # Dest


WV_NOISE = 76                       # struct wt_voice (wt_engine.h): the noise generator's state
WV_POS1 = 20                        # ... oscillator 1's frame position (0..WT_FRAMES - 1)
WT_FRAMES = 32


class WT:
    """A WAVE slot 0 (WAVE replacing VAP, one voice; the other slots idle PERC), booted;
    its voice rendered alone through its render method."""

    def __init__(self, ptf: Path, poly: int = 1, **params):
        self.r = r = Rig(ptf)
        r.configure((VAP, PERC, PERC), (poly, 1, 1))
        r.swap(VAP)
        r.pattern()
        self.set(**params)

    def set(self, **params) -> None:
        for k, v in params.items():
            self.r.set_param(0, globals()["P_" + k.upper()], v)

    @property
    def voice(self) -> int:
        return self.r.voices(0)[0]

    def note(self, note: int, blocks: int, velocity: int = 127, skip: int = 2) -> np.ndarray:
        """Note on, `skip` blocks dropped, then `blocks` blocks of the voice."""
        self.r.note_on(0, note, velocity)
        if skip:
            self.r.render_voice(self.voice, skip)
        return self.r.render_voice(self.voice, blocks)

    def off(self, note: int, blocks: int = 0) -> np.ndarray:
        self.r.note_off(0, note)
        return self.r.render_voice(self.voice, blocks) if blocks else np.zeros(0)


def zc_freq(y: np.ndarray) -> float:
    """Frequency of a sine from its interpolated upward zero crossings."""
    z = np.where((y[:-1] < 0) & (y[1:] >= 0))[0]
    t = z + (-y[z] / (y[z + 1] - y[z]))
    return (len(t) - 1) / (t[-1] - t[0]) * RATE


def cents(f: float, ref: float) -> float:
    return 1200 * np.log2(f / ref)


def note_hz(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def bh_spectrum(y: np.ndarray) -> np.ndarray:
    """Magnitude spectrum with a 4-term Blackman-Harris window (sidelobes below -92 dB)."""
    n = np.arange(len(y))
    w = (0.35875 - 0.48829 * np.cos(2 * np.pi * n / (len(y) - 1)) + 0.14128 * np.cos(4 * np.pi * n / (len(y) - 1))
         - 0.01168 * np.cos(6 * np.pi * n / (len(y) - 1)))
    return np.abs(np.fft.rfft(y * w))


def harmonics_db(y: np.ndarray, f0: float, count: int = 10) -> list[float]:
    """Levels of harmonics 1..count in dB relative to the first."""
    s = bh_spectrum(y)
    hz = RATE / len(y)
    lv = [s[max(0, int(k * f0 / hz) - 6):int(k * f0 / hz) + 7].max() for k in range(1, count + 1)]
    return [20 * np.log10(max(x, 1e-12) / lv[0]) for x in lv]


def worst_alias_db(y: np.ndarray, f0: float) -> float:
    """The strongest component that isn't a harmonic of f0 (or DC), in dB below the
    strongest harmonic."""
    s = bh_spectrum(y)
    hz = RATE / len(y)
    mask = np.ones(len(s), bool)
    mask[:8] = False
    k = 1
    while k * f0 < RATE / 2:
        c = int(round(k * f0 / hz))
        mask[max(0, c - 8):c + 9] = False
        k += 1
    return float(20 * np.log10(s[mask].max() / s[~mask].max()))


def render_cost(r: Rig, voice: int, blocks: int) -> float:
    """Instructions per block of a voice's render method (the hook, the engine, all)."""
    n = [0]
    h = r.m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: n.__setitem__(0, n[0] + 1))
    r.render_voice(voice, blocks)
    r.m.uc.hook_del(h)
    return n[0] / blocks


# Basic bank, LP 12 dB wide open, no envelope or key tracking on the filter, held notes, no detune.
# Reset on: both oscillators start each note at phase 0. A voice's start phases are random
# otherwise (from its address), and two oscillators at one pitch would sum differently in
# each build.
OPEN = dict(cutoff=18000.0, envamt=0.0, track=0.0, res=0.0, type=4.0, aenv_att=0.0, aenv_sus=1.0, detune=0.0,
            wave1=float(BASIC), wave2=float(BASIC), reset1=1.0, reset2=1.0)


def osc_checks(ptf: Path) -> None:
    print("the oscillators")
    w = WT(ptf, **OPEN, pos1=0.0, pos2=0.0)
    errs = []
    for n in range(24, 109, 6):
        y = w.note(n, 256 if n < 48 else 96)
        w.off(n, 2)
        errs.append((n, cents(zc_freq(y), note_hz(n))))
    worst = max(errs, key=lambda e: abs(e[1]))
    check(abs(worst[1]) < 1.0, f"pitch within 1 cent from note 24 to 108 (sine; worst {worst[1]:+.3f} c at note {worst[0]})")
    for label, params, want in (("Tune 1 +7 (osc 1 alone)", dict(mix=-1.0, tune1=7.0), 700),
                                ("Tune 2 -12 (osc 2 alone)", dict(mix=1.0, tune2=-12.0), -1200),
                                ("Finetune +50", dict(mix=-1.0, fine=50.0), 50),
                                ("Finetune -100", dict(mix=1.0, fine=-100.0), -100),
                                ("Detune 25 (osc 2)", dict(mix=1.0, detune=25.0), 25),
                                ("Detune 25 (osc 1 unchanged)", dict(mix=-1.0, detune=25.0), 0)):
        w = WT(ptf, **dict(OPEN, pos1=0.0, pos2=0.0, **params))
        c = cents(zc_freq(w.note(57, 96)), note_hz(57))
        check(abs(c - want) < 1.0, f"{label}: {c:+.2f} cents (want {want:+d})")
    w = WT(ptf, **OPEN, pos1=0.0, pos2=0.0)
    for value, want in ((8192, 200), (-8192, -200), (4096, 100), (0, 0)):
        w.r.note_on(0, 57, 127)
        w.r.bend(0, value)
        w.r.render_voice(w.voice, 2)
        c = cents(zc_freq(w.r.render_voice(w.voice, 96)), note_hz(57))
        w.off(57, 2)
        check(abs(c - want) < 1.0, f"pitch bend {value:+d}: {c:+.2f} cents (want {want:+d}, as VAP: +-8192 is +-2 st)")
    w = WT(ptf, **OPEN, pos1=2 / 3, pos2=2 / 3)
    worst = []
    for n in range(96, 109, 2):
        y = w.note(n, 128)
        w.off(n, 2)
        worst.append((n, worst_alias_db(y, note_hz(n))))
    wn = max(worst, key=lambda e: e[1])
    check(wn[1] < -60, f"aliasing: a saw (Basic, Position 2/3) at notes 96-108 has nothing above -60 dB "
                       f"that isn't a harmonic (worst {wn[1]:.1f} dB at note {wn[0]})")
    w = WT(ptf, **OPEN, mix=-1.0)          # oscillator 1 alone (each starts at its own phase)
    shapes = {}
    for pos in (0.0, 1 / 3, 2 / 3, 1.0):
        w.set(pos1=pos, pos2=pos)
        shapes[pos] = harmonics_db(w.note(45, 64), note_hz(45))
        w.off(45, 2)
    s0, s3, s5, s1 = shapes[0.0], shapes[1 / 3], shapes[2 / 3], shapes[1.0]
    check(max(s0[1:]) < -50, f"Basic, Position 0: a sine (harmonics 2-10 at most {max(s0[1:]):.1f} dB)")
    check(max(s3[1], s3[3]) < -30 and abs(s3[2] + 19.1) < 2.0,
          f"Position 1/3: a triangle (even harmonics at most {max(s3[1], s3[3]):.1f} dB, the 3rd at {s3[2]:.1f}; "
          "a triangle's is -19.1)")
    check(abs(s5[1] + 6.0) < 1.5 and abs(s5[2] + 9.5) < 1.5 and abs(s5[3] + 12.0) < 1.5,
          f"Position 2/3: a saw (harmonics 2, 3, 4 at {s5[1]:.1f}, {s5[2]:.1f}, {s5[3]:.1f} dB; a saw has -6.0, -9.5, -12.0)")
    check(max(s1[1], s1[3], s1[5]) < -40 and abs(s1[2] + 9.5) < 1.5,
          f"Position 1: a square (even harmonics at most {max(s1[1], s1[3], s1[5]):.1f} dB, the 3rd at {s1[2]:.1f})")
    w = WT(ptf, **OPEN, pos1=0.0, pos2=1.0)
    mixes = {}
    for mix in (-1.0, 0.0, 1.0):
        w.set(mix=mix)
        mixes[mix] = harmonics_db(w.note(45, 64), note_hz(45))[2]
        w.off(45, 2)
    check(mixes[-1.0] < -50 and -25 < mixes[0.0] < -11 and abs(mixes[1.0] + 9.5) < 1.5,
          f"Mix (osc 1 sine, osc 2 square): 3rd harmonic at {mixes[-1.0]:.1f} dB with -1, {mixes[0.0]:.1f} with 0, "
          f"{mixes[1.0]:.1f} with +1")
    w = WT(ptf, **OPEN, pos1=0.5, pos2=0.5, tune2=7.0, aenv_rel=0.0)
    a = w.note(52, 2, skip=0)
    w.off(52, 3)
    w.r.render_voice(w.voice, 5)
    b = w.note(52, 2, skip=0)
    w.off(52, 3)
    w.set(reset1=0.0, reset2=0.0)
    c = w.note(52, 2, skip=0)
    check(np.abs(a - b).max() == 0 and np.abs(a - c).max() > 0.01,
          "Reset 1 and 2: each note starts its oscillators at phase 0 (without Reset they run on)")
    w = WT(ptf, **OPEN, aenv_rel=0.0)      # Release 0: the gate alone
    v = w.voice
    before = w.r.sounding(v)
    w.r.note_on(0, 60, 100)
    on = w.r.sounding(v)
    w.r.render_voice(v, 10)
    held = w.r.sounding(v)
    w.r.note_off(0, 60)
    w.r.render_voice(v, 1)
    after = w.r.sounding(v)
    check(not before and on and held and not after,
          "sounding: false before, true from the note on while the note is held, false once the gate has ended")
    idle = render_cost(w.r, v, 4)
    playing = []
    for n, pos in ((36, 0.0), (60, 0.3), (96, 0.7), (108, 1.0)):
        w.set(pos1=pos, pos2=1.0 - pos, tune2=7.0, type=3.0, noise=0.5)   # 24 dB and noise: the most work
        w.r.note_on(0, n, 127)
        w.r.render_voice(v, 1)
        w.set(pos1=1.0 - pos)              # a moving position
        playing.append(render_cost(w.r, v, 4))
        w.off(n, 2)
    check(idle < 1000, f"an idle voice costs {idle:.0f} instructions per block (under 1,000: Free Running LFOs run on; "
                       "stock VAP idles at 60,804)")
    check(max(playing) < 16000, f"a playing voice (moving Position, 24 dB filter, noise) costs at most "
                                f"{max(playing):.0f} instructions per block (under 16,000; "
                                f"{', '.join(f'{x:.0f}' for x in playing)})")


# ---- 11. filter and envelopes -----------------------------------------------------------

LCG_A, LCG_C = 1664525, 1013904223            # the engine's noise generator (wt_engine.h)
LCG_A_INV = pow(LCG_A, -1, 1 << 32)


def block_noise(after: int) -> np.ndarray:
    """The noise a block added, from the generator's state after the block: its last 128
    states, run backwards."""
    out = np.empty(128)
    st = after
    for n in range(127, -1, -1):
        out[n] = st - (1 << 32) if st & 0x80000000 else st
        st = ((st - LCG_C) * LCG_A_INV) & 0xFFFFFFFF
    return out


def response(w: WT, blocks: int = 1024) -> tuple[np.ndarray, np.ndarray]:
    """The filter's response to the engine's noise (the oscillators parked at note 0, 8 Hz):
    frequencies and the gain in dB. The noise going in is regenerated for each block (the
    LFOs draw from the same generator, once a cycle, before the block's noise), and the
    gain is the output's cross-spectrum with it over its own spectrum (4096-point Hann
    segments, smoothed). So the result doesn't depend on which stretch of noise a build's
    voice plays."""
    w.r.note_on(0, 0, 127)
    w.r.render_voice(w.voice, 8)
    e = w.r.engine_voice(w.voice)
    ys, xs = [], []
    for _ in range(blocks):
        ys.append(w.r.render_voice(w.voice, 1))
        xs.append(block_noise(w.r.m.r32(e + WV_NOISE)))
    w.off(0, 2)
    y, x = np.concatenate(ys), np.concatenate(xs)
    seg, hop = 4096, 2048
    win = np.hanning(seg)
    pxy = np.zeros(seg // 2 + 1, complex)
    pxx = np.zeros(seg // 2 + 1)
    for i in range(0, len(y) - seg + 1, hop):
        xs = np.fft.rfft(x[i:i + seg] * win)
        pxy += np.fft.rfft(y[i:i + seg] * win) * np.conj(xs)
        pxx += np.abs(xs) ** 2
    power = np.convolve((np.abs(pxy) / pxx) ** 2, np.ones(7) / 7, mode="same")
    return np.fft.rfftfreq(seg, 1 / RATE), 10 * np.log10(power + 1e-30)


def level_at(f: np.ndarray, db: np.ndarray, hz: float) -> float:
    return float(np.interp(hz, f, db))


def block_env(y: np.ndarray) -> np.ndarray:
    return np.array([np.abs(y[i:i + 128]).max() for i in range(0, len(y) - 127, 128)])


def centroid(y: np.ndarray) -> float:
    s = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    f = np.fft.rfftfreq(len(y), 1 / RATE)
    return float((s * f).sum() / s.sum())


def filter_checks(ptf: Path) -> None:
    print("the filter")
    w = WT(ptf, **OPEN)
    w.set(noise=1.0, pos1=0.0, pos2=0.0, cutoff=1000.0, res=0.4444)   # Q 0.707
    f, db = response(w)
    ref = float(np.mean(db[(f > 80) & (f < 250)]))
    f3 = float(f[np.argmax((f > 300) & (db < ref - 3.0))])
    check(abs(f3 / 1000.0 - 1) < 0.05, f"LP 12 dB at Q 0.707: -3 dB at {f3:.0f} Hz (Cutoff 1000, within 5 %)")
    lp12_4k = level_at(f, db, 4000) - ref
    w.set(type=3.0)                                                       # LP SVF 24
    f, db = response(w)
    lp24_4k = level_at(f, db, 4000) - float(np.mean(db[(f > 80) & (f < 250)]))
    check(-28 < lp12_4k < -20 and lp24_4k < lp12_4k - 18,
          f"slopes: two octaves above Cutoff LP 12 dB at {lp12_4k:.1f} dB, LP 24 dB at {lp24_4k:.1f} dB")
    w.set(type=8.0)                                                       # HP SVF 12
    f, db = response(w)
    top = float(np.mean(db[(f > 6000) & (f < 12000)]))
    hp_low = level_at(f, db, 250) - top
    check(hp_low < -20 and abs(level_at(f, db, 1000) - top + 3) < 1.5,
          f"HP 12 dB: {hp_low:.1f} dB two octaves below Cutoff, {level_at(f, db, 1000) - top:.1f} dB at Cutoff")
    w.set(type=12.0, res=1.0)                                             # BP SVF 12
    f, db = response(w)
    pk = level_at(f, db, 1000)
    check(level_at(f, db, 250) < pk - 10 and level_at(f, db, 4000) < pk - 10
          and abs(float(f[np.argmax(np.where((f > 300) & (f < 4000), db, -999))]) / 1000 - 1) < 0.1,
          f"BP 12 dB: the peak at Cutoff, {level_at(f, db, 250) - pk:.1f} and {level_at(f, db, 4000) - pk:.1f} dB "
          "two octaves either side")
    w.set(type=14.0, res=0.4444)                                          # Notch 12
    f, db = response(w)
    top = float(np.mean(db[(f > 80) & (f < 250)]))
    check(level_at(f, db, 1000) < top - 20 and abs(level_at(f, db, 6000) - top) < 2,
          f"Notch 12 dB: {level_at(f, db, 1000) - top:.1f} dB at Cutoff, flat away from it")
    peaks = []
    for res in (0.0, 0.5, 1.0, 1.4):
        w.set(type=4.0, res=res)
        f, db = response(w, 512)
        peaks.append(float(np.max(db[(f > 500) & (f < 2000)]) - np.mean(db[(f > 80) & (f < 250)])))
    check(all(b > a + 1 for a, b in zip(peaks, peaks[1:])),
          f"the resonance peak rises with Resonance 0, 0.5, 1.0, 1.4: {', '.join(f'{x:+.1f}' for x in peaks)} dB")
    w = WT(ptf, cutoff=18000.0, res=1.5, envamt=1.0, type=0.0, noise=1.0, pos1=0.5, pos2=1.0, aenv_sus=1.0)
    y = np.concatenate([w.note(n, 60) for n in (24, 60, 96, 108)])
    check(bool(np.isfinite(y).all()) and np.abs(y).max() <= 2.0,
          f"stable at Resonance 1.5 and Cutoff 18 kHz, every type up to 24 dB (peak {np.abs(y).max():.2f}, bounded)")
    for typ in range(15):
        w.set(type=float(typ))
        y = w.note(72, 30)
        if not np.isfinite(y).all() or np.abs(y).max() > 2.0:
            check(False, f"type {typ} at Resonance 1.5, Cutoff 18 kHz: not finite or above 2")
            break
    else:
        check(True, "all 15 types at Resonance 1.5, Cutoff 18 kHz: finite and bounded")
    print("the envelopes")
    for amt, label in ((1.0, "+1"), (-1.0, "-1")):
        w = WT(ptf, cutoff=800.0, res=0.3, envamt=amt, track=0.0, type=4.0, pos1=0.5, pos2=0.5, aenv_sus=1.0,
               fenv_att=0.0, fenv_dec=1.0, fenv_sus=0.0, detune=0.0, reset1=1.0, reset2=1.0)
        y = w.note(48, 200, skip=0)
        early, late = centroid(y[:4096]), centroid(y[-8192:])
        ok = early > 2 * late if amt > 0 else early < late / 1.5
        check(ok, f"Env Amt {label}: the filter envelope sweeps the cutoff "
                  f"({'down' if amt > 0 else 'up'}: centroid {early:.0f} Hz early, {late:.0f} Hz late)")
    ratios = {}
    for track in (1.0, 0.0):
        w = WT(ptf, cutoff=500.0, res=0.3, envamt=0.0, track=track, type=4.0, pos1=0.5, pos2=0.5, aenv_sus=1.0,
               detune=0.0, reset1=1.0, reset2=1.0)
        a = centroid(w.note(48, 64))
        w.off(48, 2)
        b = centroid(w.note(72, 64))
        ratios[track] = b / a
    check(abs(ratios[1.0] / 4 - 1) < 0.1 and ratios[0.0] < 3.0,
          f"Key Track: two octaves up moves the spectrum x{ratios[1.0]:.2f} at 100 % (x4 is full tracking), "
          f"x{ratios[0.0]:.2f} at 0 %")
    for att in (0.1, 0.5, 2.0):
        w = WT(ptf, **dict(OPEN, pos1=0.0, pos2=0.0, aenv_att=att))
        e = block_env(w.note(81, int(att * RATE / 128) + 40, skip=0))
        t99 = np.argmax(e >= 0.99 * e[-10:].mean()) * 128 / RATE
        check(abs(t99 / att - 1) < 0.05, f"Attack {att} s: full level after {t99:.3f} s")
    for dec in (0.2, 1.0):
        w = WT(ptf, **dict(OPEN, pos1=0.0, pos2=0.0, aenv_dec=dec, aenv_sus=0.5))
        e = block_env(w.note(81, int(dec * RATE / 128) + 60, skip=0))
        top = e.max()
        d = (e / top - 0.5) / 0.5
        t20 = (np.argmax(d < 0.1) - np.argmax(e)) * 128 / RATE     # from the end of the attack
        check(abs(t20 / (dec / 3) - 1) < 0.05 and abs(e[-10:].mean() / top - 0.5) < 0.03,
              f"Decay {dec} s to Sustain 0.5: -20 dB of the way after {t20:.3f} s (a third of it), "
              f"then holds at {e[-10:].mean() / top:.3f}")
    for rel in (0.3, 1.0, 3.0):
        w = WT(ptf, **dict(OPEN, pos1=0.0, pos2=0.0, aenv_rel=rel))
        e0 = block_env(w.note(81, 20)).max()
        w.r.note_off(0, 81)
        ys, n = [], 0
        while w.r.sounding(w.voice) and n < 2000:
            ys.append(w.r.render_voice(w.voice, 1))
            n += 1
        e = block_env(np.concatenate(ys))
        t20 = np.argmax(e < e0 * 0.1) * 128 / RATE
        t_end = n * 128 / RATE
        check(abs(t20 / (rel / 3) - 1) < 0.05 and abs(t_end / rel - 1) < 0.05,
              f"Release {rel} s: -20 dB after {t20:.3f} s, sounding ends after {t_end:.3f} s (-60 dB)")


def init_level_checks(ptf: Path) -> None:
    print("WAVE's Init patch: level")
    r = Rig(ptf)
    r.configure((VAP, PERC, PERC), (4, 1, 1))
    r.swap(VAP)
    r.pattern()
    got = {}
    for notes, vel in (([60], 100), ([48, 55, 60, 64], 127)):
        for n in notes:
            r.note_on(0, n, vel)
        y = r.render(160, fast=True)[0][0].astype(float) / 32768
        for n in notes:
            r.note_off(0, n)
        r.render(200, fast=True)
        got[len(notes)] = (20 * np.log10(np.sqrt(np.mean(y[len(y) // 2:] ** 2))), float(np.abs(y).max()))
    check(-26 < got[1][0] < -18, f"one note at velocity 100: {got[1][0]:.1f} dBFS RMS (held; sy1 played about 8 dB louder)")
    check(got[4][1] < 0.9, f"a four-note chord at full velocity peaks at {got[4][1]:.2f} of full scale (the slot clips at "
                           "0.998)")


def allocation(path: Path, patched: bool, params: dict) -> list[tuple[int, int]]:
    """Which voice (index in the slot) each note on of a fixed sequence goes to."""
    r = Rig(path, patched)
    r.configure((VAP, PERC, PERC), (4, 1, 1))
    if patched:
        r.swap(VAP)
        r.pattern()
    for k, x in params.items():
        r.set_param(0, globals()["P_" + k.upper()], x)
    got: list[tuple[int, int]] = []
    voices = r.voices(0)
    midi_word = r.m.r32(sr.VOICE_VT[VAP] + sr.V_MIDI) & ~1

    def on_midi(uc, a, s, u):
        msg = bytes(r.m.uc.mem_read(r.m.arg(2), 4))
        if msg[0] == 4:
            got.append((voices.index(r.m.arg(0)), msg[2]))
    r.m.uc.hook_add(UC_HOOK_CODE, on_midi, begin=midi_word, end=midi_word)
    rnd = np.random.default_rng(7)
    held: list[int] = []
    for step in range(240):
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


def allocation_checks(ptf: Path, stock: Path) -> None:
    print("voice allocation (4 voices, more notes than voices, overlapping releases)")
    pluck = dict(aenv_att=0.0, aenv_dec=0.5, aenv_sus=0.0, aenv_rel=0.5)      # VAP's Init amp envelope
    for label, params in (("a pluck", pluck), ("sustained, Release 0.5", dict(pluck, aenv_sus=1.0)),
                          ("sustained, Release 1.0", dict(pluck, aenv_sus=1.0, aenv_rel=1.0))):
        a, b = allocation(stock, False, params), allocation(ptf, True, params)
        same = sum(1 for x, y in zip(a, b) if x == y)
        check(a == b, f"{label}: the same voice for every note on as stock VAP ({same} of {len(a)} the same)")


def glide_checks(ptf: Path) -> None:
    print("glide")
    w = WT(ptf, pos1=0.0, pos2=0.0, glide_time=0.5, glide_mode=0.0, aenv_sus=1.0, detune=0.0, wave1=float(BASIC),
           wave2=float(BASIC), cutoff=18000.0, envamt=0.0)
    w.note(48, 10)
    w.off(48, 200)
    w.r.note_on(0, 60, 127)
    track = []
    for _ in range(30):
        y = w.r.render_voice(w.voice, 8)
        track.append(12 * np.log2(zc_freq(y) / 440) + 69)
    t_mid = (14 * 8 + 4) * 128 / RATE
    check(abs(track[0] - 48.5) < 1.0 and abs(track[14] - (48 + 12 * t_mid / 0.5)) < 0.5 and abs(track[-1] - 60) < 0.05,
          f"Always, 0.5 s: from note 48 ({track[0]:.2f}) through {track[14]:.2f} at {t_mid:.2f} s to {track[-1]:.2f}")
    w = WT(ptf, pos1=0.0, pos2=0.0, glide_time=0.5, glide_mode=1.0, aenv_sus=1.0, detune=0.0, cutoff=18000.0,
           envamt=0.0)
    w.note(48, 10)
    w.off(48, 200)
    y = w.note(60, 8, skip=0)
    detached = 12 * np.log2(zc_freq(y) / 440) + 69
    w.off(60, 200)
    w.note(48, 10)
    w.r.note_on(0, 60, 127)                 # 48 still held: overlapping
    y = w.r.render_voice(w.voice, 8)
    overlapped = 12 * np.log2(zc_freq(y) / 440) + 69
    check(abs(detached - 60) < 0.05 and overlapped < 50,
          f"Overlap: a detached note starts on pitch ({detached:.2f}), an overlapping one glides ({overlapped:.2f})")


# ---- 12. the wavetable banks -------------------------------------------------------------

def bank_checks(ptf: Path) -> None:
    print("the wavetable banks")
    m = Machine(ptf)
    banks, frames, stride, levels = WHERE["banks"], WHERE["frames"], WHERE["stride"], WHERE["levels"]
    offs = struct.unpack(f"<{levels}H", bytes(m.uc.mem_read(WHERE["level_off"], 2 * levels)))
    lens = struct.unpack(f"<{levels}H", bytes(m.uc.mem_read(WHERE["level_len"], 2 * levels)))
    data = np.frombuffer(bytes(m.uc.mem_read(WHERE["tables"], 2 * banks * frames * stride)), "<i2")
    data = data.reshape(banks, frames, stride).astype(float)
    lo, hi = CODE
    print(f"    {banks} banks of {frames} frames, {stride * 2} bytes a frame: {data.nbytes // 4 // 1024} KB of "
          f"tables; the patch's code is {(hi - lo) // 1024} KB, the firmware file {ptf.stat().st_size // 1024} KB")
    worst, guard_ok, pk, silent = -999.0, True, 0, 0
    for b in range(banks):
        for f in range(frames):
            loud = None                     # the frame's strongest harmonic, per sample
            for lv in range(levels):
                x = data[b, f, offs[lv]:offs[lv] + lens[lv]]
                guard_ok &= data[b, f, offs[lv] + lens[lv]] == x[0]
                s = np.abs(np.fft.rfft(x)) / lens[lv]
                harm = 64 >> lv
                if loud is None:
                    loud = s[1:harm + 1].max()
                if s[1:harm + 1].max() * lens[lv] < 1:
                    silent += 1
                    continue
                worst = max(worst, 20 * np.log10(max(s[harm + 1:].max(), 1e-12) / loud))
                pk = max(pk, int(np.abs(x).max()))
    check(worst < -70 and guard_ok and pk <= 32767 and silent == 0,
          f"every bank, frame and level band-limited: nothing above its harmonic count stronger than {worst:.1f} dB "
          f"below the frame's strongest harmonic (16-bit rounding), guard samples right, peak {pk}, "
          f"{silent} silent levels")
    rms = [20 * np.log10(np.sqrt(np.mean(data[b, :, :lens[0]] ** 2))) for b in range(banks)]
    check(max(rms) - min(rms) < 3.0,
          f"the banks' levels within 3 dB: {', '.join(f'{x - max(rms):+.1f}' for x in rms)} dB")
    w = WT(ptf, **dict(OPEN, pos1=0.5, pos2=0.5, aenv_rel=0.0))
    slots = banks + 2                       # the banks, then the super saw and the saw
    outs = []
    for b in range(slots):
        got = []
        for v in (b - 0.4, b + 0.4):        # within a slot's rounding
            w.set(wave1=v, wave2=v)
            got.append(w.note(48, 16))
            w.off(48, 3)
        outs.append(got)
    inside = all(np.array_equal(a, b) for a, b in outs[1:-1])
    distinct = all(not np.array_equal(outs[i][1], outs[j][1]) for i in range(slots) for j in range(i + 1, slots))
    lv = [20 * np.log10(np.sqrt(np.mean(o[1] ** 2))) for o in outs]
    check(inside and distinct, f"Wave picks the slot: {slots} values ({banks} banks, the super saw, the saw), the same sound "
                               "within each one's rounding, a different one in each")
    check(max(lv) - min(lv) < 3.0, f"the engine plays every slot within 3 dB: {', '.join(f'{x - max(lv):+.1f}' for x in lv)} dB")


# ---- 13. Macros ---------------------------------------------------------------------------

def macro_checks(ptf: Path) -> None:
    print("Macros (Macro FX a-f reach the synth as CCs 102-107, as a ramp of patch/interpolate does)")
    w = WT(ptf, cutoff=300.0, envamt=0.0, aenv_sus=1.0, res=0.3)
    r = w.r
    moved = {}
    for k in range(6):
        before = r.param_copy(0)
        r.cc(0, CC_MACRO + k, 127)
        after = r.param_copy(0)
        moved[k] = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        r.cc(0, CC_MACRO + k, 0)
    check(moved.get(2) == [0, 1] and moved.get(3) == [12],
          f"Macro c moves Position 1 and 2, Macro d Cutoff, in the engine's copy (copy indices moved: {moved})")
    r.note_on(0, 48, 100)
    r.render(4, fast=True)
    per_block, cents_ = [], []
    v = r.voices(0)[0]
    for b in range(64):
        n0 = sum(1 for x, _ in r.sets if x == v)
        r.cc(0, CC_MACRO + 3, 2 * b + 1)
        per_block.append(sum(1 for x, _ in r.sets if x == v) - n0)
        y = r.render(1, fast=True)[0][0].astype(float)
        if b in (2, 60):
            cents_.append(centroid(np.concatenate([y, r.render(7, fast=True)[0][0].astype(float)])))
    check(min(per_block) >= 1 and cents_[1] > 1.5 * cents_[0],
          f"a Macro ramp, one step per block: set_param reaches the WAVE voice every block, and the sound "
          f"brightens (centroid {cents_[0]:.0f} -> {cents_[1]:.0f} Hz)")


# ---- 14. export ---------------------------------------------------------------------------

RENDER_LOOP = 0x600BC220            # (render state): 69 blocks of seq_advance, engine, graph, file writes
RENDER_WRITE, RENDER_FM = 0x600BBF80, 0x600BCA88


def export_checks(ptf: Path) -> None:
    print("export: the render loop (0x600bc220), its file writes stubbed")
    r = Rig(ptf)
    m = r.m
    r.configure((VAP, PERC, PERC), (2, 1, 1))
    r.swap(VAP)
    r.pattern()
    writes = []
    m.stub_function(RENDER_WRITE, lambda mm: writes.append(mm.arg(1)) or 0)
    m.stub_function(RENDER_FM, lambda mm: 0)
    state = m.alloc(0x100)
    r.note_on(0, 57, 100)
    r.capture_voices((VAP,))
    v = r.voices(0)
    try:
        m.call(RENDER_LOOP, state, max_insns=500_000_000)
        err = None
    except RuntimeError as e:
        err = str(e)
    calls = [a for a in r.render_args if a[0] in v]
    y = np.concatenate(r.voice_out.get(v[0], [np.zeros(1, "<f4")]))
    check(err is None and len(calls) == 69 * len(v) and np.abs(y).max() > 0.02 and bool(np.isfinite(y).all()),
          f"one call renders 69 blocks: the WAVE voices' render ran {len(calls)} times (69 x {len(v)}), the held "
          f"note sounds through the engine (peak {np.abs(y).max():.2f}){'; ' + err if err else ''}")


# ---- 15. more than 8 voices --------------------------------------------------------------

def voices_checks(ptf: Path) -> None:
    print("more than 8 voices, as a loaded project can set (synth_slot_set_polyphony has no cap)")
    r = Rig(ptf)
    r.configure((VAP, VAP, VAP), (1, 1, 1))
    r.pattern()
    r.swap(VAP)
    # a project's slot records set each slot's polyphony through synth_slot_set_polyphony, which
    # has no cap (applying records with 0x600b2080 itself crashes in this rig, stock firmware too).
    # 13 VAP voices: past the 8-voice cap, and as many as the heap holds here (6.5 KB a voice;
    # one more runs it out, stock firmware too)
    for i, n in enumerate((8, 4, 1)):
        r.set_poly(i, n)
    counts = [len(r.voices(i)) for i in range(3)]
    r.config()
    ent = r.entries()
    with_entry = {e[0] for e in ent}
    for i in range(3):
        for n in range(counts[i]):
            r.note_on(i, 40 + 3 * n + i, 100)
    r.capture_voices((VAP,))
    out = r.render(20, fast=True)
    pk = [int(np.abs(out[i][0]).max()) for i in range(3)]
    silent = [v for i in range(3) for v in r.voices(i) if v not in with_entry]
    quiet = all(np.abs(r.voice_signal(v)).max() == 0 for v in silent)
    check(counts == [8, 4, 1] and len(ent) == 8 and all(p > 200 for p in pk) and len(silent) == 5 and quiet,
          f"{sum(counts)} WAVE voices ({counts}): 8 engine voices, the other {len(silent)} silent; all three slots sound "
          f"(peaks {pk})")
    for i in range(3):
        for n in range(counts[i]):
            r.note_off(i, 40 + 3 * n + i)
    r.render(250, fast=True)
    for i in range(3):
        r.set_poly(i, 2)
    r.config()
    owners = sorted(e[0] for e in r.entries())
    check(owners == sorted(v for i in range(3) for v in r.voices(i)),
          f"back to 2 voices each: {len(owners)} engine voices, one for each voice (the screen update gives a "
          "freed engine voice to a voice that had none)")


# ---- 16. super saw, Digital mode, the analog filters ---------------------------------------

def block_rms(y: np.ndarray, n: int = 1024) -> np.ndarray:
    return np.array([np.sqrt(np.mean(y[i:i + n] ** 2)) for i in range(0, len(y) - n + 1, n)])


def supersaw_checks(ptf: Path) -> None:
    print("super saw (Wave 7)")
    w = WT(ptf, **dict(OPEN, wave1=float(SUPERSAW), mix=-1.0, pos1=0.0))
    still = w.note(45, 160)
    w.off(45, 3)
    w.set(pos1=0.7)
    moving = w.note(45, 160)
    w.off(45, 3)
    n10 = int(round(10 * RATE / note_hz(45)))  # windows of 10 cycles
    cv0 = float(np.std(block_rms(still, n10)) / np.mean(block_rms(still, n10)))
    cv1 = float(np.std(block_rms(moving, n10)) / np.mean(block_rms(moving, n10)))
    check(cv0 < 0.02 and cv1 > 0.05,
          f"Position 0: 7 saws at one pitch, a steady sound (level varies {cv0:.1%}); Position 0.7: spread, they beat "
          f"({cv1:.1%})")
    a = worst_alias_db(still, note_hz(45))
    check(a < -50, f"at Position 0 every partial is a harmonic of the note (anything else at most {a:.1f} dB)")
    w.set(pos1=0.7)
    y = w.note(81, 320)                     # 880 Hz: the 7 saws' fundamentals 9 to 48 Hz apart
    w.off(81, 3)
    s = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    f = np.arange(len(s)) * RATE / len(y)
    near = (f > note_hz(81) * 0.85) & (f < note_hz(81) * 1.15)
    top = s[near].max()
    peaks = int(np.sum((s[1:-1] > s[:-2]) & (s[1:-1] > s[2:]) & (s[1:-1] > 0.2 * top) & near[1:-1]))
    check(peaks == 7, f"Position 0.7: {peaks} separate partials around the fundamental (7 saws)")
    w2 = WT(ptf, **dict(OPEN, wave1=float(BASIC), mix=-1.0, pos1=2 / 3))
    saw = w2.note(45, 64)
    d = 20 * np.log10(np.sqrt(np.mean(moving ** 2)) / np.sqrt(np.mean(saw ** 2)))
    check(abs(d) < 3.0, f"its level within 3 dB of one saw ({d:+.1f} dB)")
    w = WT(ptf, wave1=float(SUPERSAW), wave2=float(SUPERSAW), pos1=1.0, pos2=1.0, noise=0.5, type=1.0)
    w.r.note_on(0, 96, 127)
    w.r.render_voice(w.voice, 2)
    c = render_cost(w.r, w.voice, 4)
    check(c < 60804, f"both oscillators on super saw, noise, OB 24 dB: {c:.0f} instructions per block (stock VAP "
                     "idles at 60,804)")


def digital_checks(ptf: Path) -> None:
    print("Digital mode")
    outs = {}
    for digital in (0.0, 1.0):
        for pos in (15.0 / 31, 15.3 / 31):
            w = WT(ptf, **dict(OPEN, wave1=float(RESONANT), wave2=float(RESONANT), reset1=1.0, reset2=1.0,
                               pos1=pos, pos2=pos, digital=digital))
            outs[(digital, pos)] = w.note(60, 8, skip=0)
    stepped = np.array_equal(outs[(1.0, 15.0 / 31)], outs[(1.0, 15.3 / 31)])
    smooth = not np.array_equal(outs[(0.0, 15.0 / 31)], outs[(0.0, 15.3 / 31)])
    check(stepped and smooth, "on: Position steps from frame to frame (between two frames it plays the nearest); "
                              "off: it morphs")
    d = float(np.abs(outs[(1.0, 15.0 / 31)] - outs[(0.0, 15.0 / 31)]).max())
    check(d > 1e-3, f"on: the same frame sounds different (8-bit, no interpolation; largest difference {d:.4f})")


def drive_checks(ptf: Path) -> None:
    print("Drive (the filter's input through a soft clip)")
    h3, peaks = {}, {}
    for d in (0.0, 0.25, 0.5, 1.0):                 # a sine at 110 Hz through the open filter
        w = WT(ptf, **OPEN, pos1=0.0, pos2=0.0, drive=d)
        y = w.note(45, 64)
        h3[d] = harmonics_db(y, note_hz(45))[2]
        peaks[d] = float(np.abs(y).max())
    lv = [20 * np.log10(peaks[d] / peaks[0.0]) for d in (0.25, 0.5, 1.0)]
    check(h3[0.0] < -90 and h3[0.0] < h3[0.25] < h3[0.5] < h3[1.0] and h3[1.0] > -15,
          f"a sine's 3rd harmonic grows with Drive 0, 25, 50, 100 %: {', '.join(f'{h3[d]:.0f}' for d in h3)} dB")
    check(all(-2.0 < x < 1.5 for x in lv),
          f"the level stays close: a full-velocity note's peak at Drive 25, 50, 100 % is "
          f"{', '.join(f'{x:+.1f}' for x in lv)} dB from Drive 0's")
    hi = {}
    for cut in (18000.0, 400.0):                    # the 7th harmonic, 770 Hz: above a 400 Hz LP 24 dB
        w = WT(ptf, **dict(OPEN, pos1=0.0, pos2=0.0, drive=1.0, cutoff=cut, type=3.0))
        hi[cut] = harmonics_db(w.note(45, 64), note_hz(45))[6]
    check(hi[400.0] < hi[18000.0] - 20,
          f"before the filter: the harmonics Drive makes above Cutoff are filtered ({hi[18000.0]:.0f} dB open, "
          f"{hi[400.0]:.0f} dB with LP 24 dB at 400 Hz)")
    w = WT(ptf, cutoff=18000.0, res=1.5, envamt=1.0, noise=1.0, pos1=0.5, pos2=1.0, aenv_sus=1.0, drive=1.0)
    bad = []
    for typ in range(15):
        w.set(type=float(typ))
        y = w.note(72, 30)
        if not np.isfinite(y).all() or np.abs(y).max() > 2.0:
            bad.append(typ)
    check(not bad, f"Drive 100 % with Resonance 150 %: all 15 filter types finite and bounded ({bad or 'none out'})")
    costs = []
    for d in (0.0, 1.0):
        w = WT(ptf, **OPEN, drive=d)
        w.r.note_on(0, 60, 100)
        w.r.render_voice(w.voice, 2)
        costs.append(render_cost(w.r, w.voice, 4))
    check(costs[1] - costs[0] < 3000, f"Drive's cost: {costs[1] - costs[0]:.0f} instructions per voice per block "
                                      f"({costs[0]:.0f} -> {costs[1]:.0f})")


FX_CENTRE = 3.5e-3 * RATE           # wt_fx.h: the chorus taps' centre, in samples
FX_SWING = 1.85e-3 * RATE           # ... their swing at Depth 100 %
FX_LP_DELAY = (1 - 0.7226) / 0.7226  # ... the taps' one-pole low-pass: its mean delay in samples
FX_OUT = 3                          # the synth output object's R block: output index slot + 3
BLOCK = sr.BLOCK


def fx_rate(v: float) -> float:
    return 0.05 * 100.0 ** v


def fx_value(hz: float) -> float:
    return float(np.log(hz / 0.05) / np.log(100.0))


class FX:
    """WAVE slot 0's effect alone: blocks through voice 0's +0x14 (as synth_slot_render calls it
    after the voices: (voice, ctx, L, R), the slot's output) with L filled from `x`."""

    def __init__(self, w: WT):
        self.w, m = w, w.r.m
        self.ctx, self.L, self.R = m.alloc(0x40), m.alloc(4 * BLOCK), m.alloc(4 * BLOCK)
        m.write(self.ctx, struct.pack("<f", 120.0))

    def run(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        m, v = self.w.r.m, self.w.voice
        f = m.r32(m.r32(v) + sr.V_POST)
        outl, outr = [], []
        for k in range(0, len(x), BLOCK):
            m.write(self.L, x[k:k + BLOCK].astype("<f4").tobytes())
            m.write(self.R, struct.pack("<128f", *([7.0] * BLOCK)))        # R must be written
            m.call(f, v, self.ctx, self.L, self.R, max_insns=5_000_000)
            outl.append(np.frombuffer(bytes(m.uc.mem_read(self.L, 4 * BLOCK)), "<f4").copy())
            outr.append(np.frombuffer(bytes(m.uc.mem_read(self.R, 4 * BLOCK)), "<f4").copy())
        return np.concatenate(outl), np.concatenate(outr)

    def cost(self, blocks: int = 4) -> float:
        n = [0]
        h = self.w.r.m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: n.__setitem__(0, n[0] + 1))
        self.run(np.zeros(blocks * BLOCK, np.float32))
        self.w.r.m.uc.hook_del(h)
        return n[0] / blocks


def tap_delays(fx: FX, seconds: float, every: int = 512) -> tuple[np.ndarray, np.ndarray]:
    """A click every `every` samples, Mix 100 % (the taps alone): each side's delay per click,
    as the centroid of its response, less the low-pass's mean delay. (A click comes out
    where n - d(n) is its time: the delay as it is then, d samples later. With the taps
    moving at s samples per sample, the two sides' mean is off by s x (dl - dr) / 2 at
    most 4 Rate swing^2 / 44,100.)"""
    n = int(seconds * RATE) // every * every
    x = np.zeros(n, np.float32)
    x[::every] = 1.0
    L, R = fx.run(x)
    k = np.arange(every // 2)

    def delays(y):
        seg = y.reshape(-1, every)[:, :every // 2]
        return (seg * k).sum(1) / seg.sum(1) - FX_LP_DELAY
    return delays(L), delays(R)


def graph_lr(r: Rig, blocks: int, slot: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """The synth output object's 16-bit L and R blocks of a slot (output indices slot, slot + 3)."""
    z = np.zeros(BLOCK, "<i2")
    ls, rs = [], []
    for _ in range(blocks):
        r.synth_block()
        ls.append(r.out.get(slot, z))
        rs.append(r.out.get(slot + FX_OUT, z))
    return np.concatenate(ls), np.concatenate(rs)


def fx_checks(ptf: Path) -> None:
    print("the effect (62-65: Effect, Rate, Depth, Mix): the chorus, on a WAVE slot's output, in stereo")
    rnd = np.random.default_rng(5)
    noise = rnd.uniform(-0.5, 0.5, 40 * BLOCK).astype(np.float32)
    fx = FX(WT(ptf))
    L, R = fx.run(noise)
    check(np.array_equal(L, noise) and np.array_equal(R, noise),
          "Effect None (the Init patch): L as it came, R a copy of it, sample for sample")
    fx.w.set(effect=1.0, fx_mix=0.0)
    L, R = fx.run(noise)
    check(np.array_equal(L, noise) and np.array_equal(R, noise), "Chorus at Mix 0: dry, both sides")

    # the taps, from clicks: centre, swing with Depth, opposite directions, Rate
    got = {}
    for depth, hz in ((1.0, 2.0), (0.5, 2.0), (1.0, 0.5)):
        fx = FX(WT(ptf, effect=1.0, fx_mix=1.0, fx_depth=depth, fx_rate=fx_value(hz)))
        fx.run(np.zeros(30 * BLOCK, np.float32))        # faded in
        dl, dr = tap_delays(fx, 2.5 if hz >= 1 else 4.5)
        mid, half = (dl + dr) / 2, (dl - dr) / 2
        up = np.where((half[:-1] < 0) & (half[1:] >= 0))[0]
        t = (up + half[up] / (half[up] - half[up + 1])) * 512
        period = float(np.diff(t).mean()) / RATE if len(t) > 1 else 0.0
        lag = 4 * hz * (depth * FX_SWING) ** 2 / RATE      # the measurement's own lag (tap_delays)
        got[(depth, hz)] = (float(np.abs(mid - FX_CENTRE).max()), lag, float(np.abs(half).max()) / FX_SWING,
                            1.0 / period if period else 0.0)
    ok = all(c < lag + 0.1 and abs(sw - d) < 0.03 * d and abs(f - hz) < 0.02 * hz
             for (d, hz), (c, lag, sw, f) in got.items())
    check(ok, "the chorus's taps, measured from clicks at Mix 100 %: centred on 3.5 ms, L and R moved in opposite "
              "directions by Depth x 1.85 ms at Rate: " +
              "; ".join(f"Depth {d:.0%} Rate {hz} Hz: centre off by {c:.2f} samples (the measurement lags "
                        f"{lag:.2f}), swing {sw:.3f} x 1.85 ms, {f:.3f} Hz" for (d, hz), (c, lag, sw, f) in got.items()))

    # fades: a sine switched on and off moves smoothly, R = L again once off
    sine = (0.8 * np.sin(2 * np.pi * 220 * np.arange(70 * BLOCK) / RATE)).astype(np.float32)
    fx = FX(WT(ptf, fx_mix=1.0, fx_depth=1.0))
    step_dry = float(np.abs(np.diff(sine)).max())
    fx.run(sine[:10 * BLOCK])
    fx.w.set(effect=1.0)
    on_l, on_r = fx.run(sine[10 * BLOCK:40 * BLOCK])
    fx.w.set(effect=0.0)
    off_l, off_r = fx.run(sine[40 * BLOCK:])
    jl = np.concatenate([sine[10 * BLOCK - 1:10 * BLOCK], on_l, off_l])
    jr = np.concatenate([sine[10 * BLOCK - 1:10 * BLOCK], on_r, off_r])
    jump = max(float(np.abs(np.diff(jl)).max()), float(np.abs(np.diff(jr)).max()))
    first = on_l[0] == sine[10 * BLOCK] and on_r[0] == sine[10 * BLOCK]
    after = np.array_equal(off_l[-5 * BLOCK:], sine[-5 * BLOCK:]) and np.array_equal(off_r[-5 * BLOCK:], sine[-5 * BLOCK:])
    wet = float(np.abs(on_l[-BLOCK:] - sine[40 * BLOCK - BLOCK:40 * BLOCK]).max())
    check(first and after and jump < 1.5 * step_dry and wet > 0.05,
          f"switching on and off fades (a 220 Hz sine at Mix and Depth 100 %): the first sample dry, the largest step "
          f"{jump:.4f} (the sine's own {step_dry:.4f}; halfway through the fade dry and taps are each at 0.71, so "
          f"up to 1.41 times it where they are in phase), wet after the fade (differs by {wet:.2f}), dry and R = L "
          "again once faded out")

    # its state: untouched before boot; cleared and seeded from the patch at the first block after it
    r = Rig(ptf)
    r.configure((VAP, PERC, PERC), (1, 1, 1))
    r.swap(VAP)
    r.set_param(0, P_EFFECT, 1.0)
    r.set_param(0, P_FX_MIX, 0.7)
    late0, late1 = LATE
    junk = struct.pack("<I", 0x7FC00001) * ((late1 - late0) // 4)    # NaNs
    r.m.write(late0, junk)
    w = WT.__new__(WT)
    w.r = r
    fx = FX(w)
    L, R = fx.run(noise[:4 * BLOCK])
    untouched = bytes(r.m.uc.mem_read(late0, late1 - late0)) == junk and r.m.uc.mem_read(r.st("fx_ready"), 1)[0] == 0
    check(not r.ready() and untouched and np.array_equal(L, noise[:4 * BLOCK]) and np.array_equal(R, L),
          "before the unit has booted: R = L, and the late RAM block (maybe SDRAM, not ready yet) is not touched")
    r.pattern()
    L, R = fx.run(noise)
    p0 = struct.unpack("<4f", bytes(r.m.uc.mem_read(late0 + WHERE["fx_p"], 16)))
    fresh = struct.unpack("<5f", bytes(r.m.uc.mem_read(late0 + WHERE["fx_p"] + WHERE["fx_size"], 20)))
    check(np.isfinite(L).all() and np.isfinite(R).all() and np.abs(L).max() < 1.0 and not np.array_equal(L, R)
          and np.allclose(p0, [1.0, 0.5, 0.5, 0.7]) and np.allclose(fresh[:4], FX_DEFAULTS)
          and r.m.uc.mem_read(r.st("fx_ready"), 1)[0] == 1,
          f"booted, the late RAM full of NaNs: cleared at the first block, the slot's values from its patch "
          f"({[round(v, 2) for v in p0]}), the others' the defaults (no WAVE patch there); the chorus plays, finite")
    r.set_param(0, P_FX_DEPTH, 0.25)
    p1 = struct.unpack("<4f", bytes(r.m.uc.mem_read(late0 + WHERE["fx_p"], 16)))
    check(p1[2] == 0.25, f"after that, set_param keeps the slot's values (Depth {p1[2]})")

    # through the whole synth output: level, stereo; 4 voices
    lv, lr = {}, {}
    for eff in (0.0, 1.0):
        r = wave_rig(ptf, 4, effect=eff)
        for n in (48, 55, 60, 64):
            r.note_on(0, n, 100)
        graph_lr(r, 30)
        L, R = graph_lr(r, 160)
        lv[eff] = 10 * np.log10(np.mean(np.concatenate([L, R]).astype(float) ** 2))
        lr[eff] = (L.astype(float), R.astype(float))
    diff = 10 * np.log10(np.mean((lr[1.0][0] - lr[1.0][1]) ** 2) / np.mean(lr[1.0][0] ** 2))
    corr = float(np.corrcoef(lr[1.0][0], lr[1.0][1])[0, 1])
    check(np.array_equal(*lr[0.0]) and abs(lv[1.0] - lv[0.0]) < 1.5 and diff > -20 and np.abs(lr[1.0]).max() < 32767,
          f"through the synth output (WAVE's Init patch, a 4-note chord on 4 voices): Effect None gives L = R; the "
          f"Chorus's level {lv[1.0] - lv[0.0]:+.1f} dB from dry, L and R differ ({diff:.0f} dB, correlation "
          f"{corr:.2f}), unclipped")

    fx = FX(WT(ptf))
    fx.run(np.zeros(BLOCK, np.float32))             # the first block after a start sets the state up
    c0 = fx.cost()
    fx.w.set(effect=1.0)
    fx.run(np.zeros(4 * BLOCK, np.float32))
    c1 = fx.cost()
    check(c0 < 1000 and c1 < 12000, f"the effect's cost per slot per block (voice 0's +0x14): Effect None {c0:.0f} "
                                   f"instructions (R = L), Chorus {c1:.0f}")


def analog_filter_checks(ptf: Path) -> None:
    print("the analog filters (MG: the ladder; OB: the saturating SVF)")
    w = WT(ptf, **OPEN)
    # quiet noise: the ladder's input stage saturates at full level, as an analog one does
    w.set(noise=0.05, pos1=0.0, pos2=0.0, cutoff=1000.0, res=0.0, type=0.0)    # LP Ladder 24
    f, db = response(w)
    ref = float(np.mean(db[(f > 80) & (f < 250)]))
    two_up = level_at(f, db, 4000) - ref
    check(-56 < two_up < -40, f"ladder: 24 dB per octave ({two_up:.1f} dB two octaves above Cutoff)")
    peaks = []
    for res in (0.0, 0.75, 1.3):
        w.set(res=res)
        f, db = response(w, 512)
        peaks.append(float(np.max(db[(f > 500) & (f < 2000)]) - np.mean(db[(f > 80) & (f < 250)])))
    check(all(b > a + 3 for a, b in zip(peaks, peaks[1:])),
          f"ladder: the resonance peak rises with Resonance 0, 0.75, 1.3: {', '.join(f'{x:+.1f}' for x in peaks)} dB")
    w = WT(ptf, **dict(OPEN, type=0.0, res=1.5, cutoff=1500.0, pos1=0.0, pos2=0.0))
    y = w.note(0, 300, skip=40)                # the oscillators at 8 Hz, far below Cutoff
    s = bh_spectrum(y) ** 2
    f = np.arange(len(s)) * RATE / len(y)
    above = f > 200
    fpk = float(f[above][np.argmax(s[above])])
    share = float(s[above & (np.abs(f - fpk) < 50)].sum() / s[above].sum())
    check(abs(fpk / 1500 - 1) < 0.15 and share > 0.5 and np.abs(y).max() <= 2.0,
          f"ladder at Resonance 150 %: it self-oscillates near Cutoff ({fpk:.0f} Hz for 1500, {share:.0%} of "
          f"everything above 200 Hz), bounded")
    w = WT(ptf, **OPEN)
    w.set(noise=1.0, pos1=0.0, pos2=0.0, cutoff=1000.0, res=0.4444, type=2.0)  # LP OB 12
    f, db = response(w)
    ref = float(np.mean(db[(f > 80) & (f < 250)]))
    f3 = float(f[np.argmax((f > 300) & (db < ref - 3.0))])
    check(abs(f3 / 1000.0 - 1) < 0.1, f"OB 12 dB at a low level: -3 dB at {f3:.0f} Hz (Cutoff 1000)")
    loud = {}
    for typ in (4.0, 2.0):                      # LP SVF 12, LP OB 12, high resonance, loud
        w = WT(ptf, **dict(OPEN, type=typ, res=1.45, cutoff=800.0, pos1=2 / 3, pos2=2 / 3))
        loud[typ] = float(np.abs(w.note(45, 100)).max())
    check(loud[2.0] < 0.8 * loud[4.0], f"OB: at high resonance the peak saturates (peak {loud[2.0]:.2f} against "
                                       f"the clean SVF's {loud[4.0]:.2f})")



# ---- 16b. the Saw, the LFOs, the mod envelope and the Mod Matrix -------------------------

def pitch_track(y: np.ndarray, hz: float, win: int = 512) -> np.ndarray:
    """Cents against `hz`, one value per window of `win` samples (zero crossings)."""
    out = []
    for i in range(0, len(y) - win + 1, win):
        try:
            out.append(cents(zc_freq(y[i:i + win]), hz))
        except Exception:
            out.append(float("nan"))
    return np.array(out)


def saw_checks(ptf: Path) -> None:
    print("the Saw wave (Wave 8)")
    w = WT(ptf, **dict(OPEN, wave1=float(SAW), mix=-1.0, pos1=0.0, reset1=1.0, aenv_rel=0.0))
    h = harmonics_db(w.note(45, 64), note_hz(45))
    w.off(45, 3)
    check(abs(h[1] + 6.0) < 1.5 and abs(h[2] + 9.5) < 1.5 and abs(h[3] + 12.0) < 1.5,
          f"a saw: harmonics 2, 3, 4 at {h[1]:.1f}, {h[2]:.1f}, {h[3]:.1f} dB (a saw has -6.0, -9.5, -12.0)")
    a = w.note(57, 16, skip=0)
    w.off(57, 3)
    w.set(pos1=1.0)
    b = w.note(57, 16, skip=0)
    w.off(57, 3)
    check(np.array_equal(a, b), "Position doesn't change it")
    y = w.note(102, 128)
    w.off(102, 2)
    al = worst_alias_db(y, note_hz(102))
    check(al < -60, f"band-limited: at note 102 nothing above -60 dB that isn't a harmonic (worst {al:.1f} dB)")


def lfo_rig(ptf: Path, **params) -> WT:
    """Oscillator 1 alone, a sine at note 69 (440 Hz), with Mod Matrix slot 1 set by params."""
    base = dict(OPEN, wave1=float(BASIC), pos1=0.0, mix=-1.0, reset1=1.0, amt2=0.0)
    base.update(params)
    return WT(ptf, **base)


def transitions(track: np.ndarray, level: float = 0.0) -> int:
    s = np.sign(track[np.isfinite(track)] - level)
    return int(np.sum(s[1:] != s[:-1]))


def lfo_checks(ptf: Path) -> None:
    print("the LFOs (Mod Matrix: LFO 1 -> Pitch 1)")
    sec = 512 / RATE
    w = lfo_rig(ptf, src1=float(S_LFO1), dest1=float(D_PITCH1), amt1=1 / 12, lfo1_rate=2.0, lfo1_wave=4.0,
                lfo1_retrig=0.0, lfo1_sync=0.0)
    t = pitch_track(w.note(69, 520, skip=0), 440.0)
    w.off(69, 3)
    hi, lo = t[t > 0], t[t < 0]
    rate = transitions(t[2:]) / 2 / (len(t[2:]) * sec)
    check(abs(np.median(hi) - 100) < 6 and abs(np.median(lo) + 100) < 6 and abs(rate / 2.0 - 1) < 0.06,
          f"Square, 2 Hz, Amt 8.3 %: the pitch steps between {np.median(lo):+.0f} and {np.median(hi):+.0f} cents "
          f"(+-100 for an octave at 100 %), {rate:.2f} Hz")
    w.set(lfo1_wave=0.0)
    t = pitch_track(w.note(69, 300, skip=0), 440.0)
    w.off(69, 3)
    check(t[0] < -85 and np.nanmax(t) > 90 and np.nanmin(t) < -90 and np.sum(np.abs(t) < 50) > len(t) // 4,
          f"Triangle: starts at the bottom ({t[0]:+.0f} c) and sweeps through the middle to +-100 c")
    w = lfo_rig(ptf, src1=float(S_LFO1), dest1=float(D_PITCH1), amt1=1 / 12, lfo1_rate=5.0, lfo1_wave=4.0,
                lfo1_retrig=0.0, lfo1_sync=1.0)     # Sync on: Ratio 5 is "1/4"
    t = pitch_track(w.note(69, 1200, skip=0), 440.0)
    w.off(69, 3)
    rate = transitions(t[2:]) / 2 / (len(t[2:]) * sec)
    want = 130.0 / 60.0                          # the rig's render context: 130 BPM; a cycle per beat
    check(abs(rate / want - 1) < 0.04, f"Sync on, 1/4 at 130 BPM: {rate:.3f} Hz (a cycle a beat: {want:.3f} Hz)")
    starts = {}
    for retrig, label in ((0.0, "Note"), (2.0, "Free Running")):
        w = lfo_rig(ptf, src1=float(S_LFO1), dest1=float(D_PITCH1), amt1=1 / 12, lfo1_rate=0.7, lfo1_wave=2.0,
                    lfo1_retrig=retrig, aenv_rel=0.0)
        firsts = []
        for gap in (150, 97):                    # silent between the notes: the voice is idle
            firsts.append(pitch_track(w.note(69, 8, skip=0), 440.0)[0])
            w.off(69, gap)
        starts[label] = firsts
    # Free Running: by the second note the ramp has run (8 + 150) blocks at 0.7 Hz: a third of a cycle
    want = -100 + 200 * (0.7 * 158 * 128 / RATE % 1.0)
    check(all(abs(x + 100) < 8 for x in starts["Note"]) and abs(starts["Free Running"][1] - want) < 8,
          f"Retrigger Note: each note starts the Ramp Up at the bottom ({', '.join(f'{x:+.0f}' for x in starts['Note'])} c); "
          f"Free Running: it runs on, silent voice or not ({', '.join(f'{x:+.0f}' for x in starts['Free Running'])} c; "
          f"{want:+.0f} expected for the second)")
    w = lfo_rig(ptf, src1=float(S_LFO1), dest1=float(D_PITCH1), amt1=1 / 12, lfo1_rate=2.0, lfo1_wave=2.0,
                lfo1_retrig=1.0)
    t = pitch_track(w.note(69, 400, skip=0), 440.0)
    tail = t[len(t) // 2:]
    check(np.nanmax(np.abs(tail - 100)) < 6 and t[0] < -85,
          f"One Shot: one Ramp Up from {t[0]:+.0f} c, then it holds at the top ({np.nanmedian(tail):+.0f} c)")
    w = lfo_rig(ptf, src1=float(S_LFO1), dest1=float(D_PITCH1), amt1=1 / 12, lfo1_rate=4.0, lfo1_wave=5.0,
                lfo1_retrig=0.0)
    t = pitch_track(w.note(69, 800, skip=0), 440.0)
    cyc = RATE / 4                               # samples per cycle; the windows wholly inside one
    inside = [t[int(np.ceil(k * cyc / 512)) + 1:int((k + 1) * cyc // 512) - 1] for k in range(int(len(t) * 512 // cyc))]
    steps = [float(np.nanmedian(x)) for x in inside]
    spread = [float(np.nanmax(x) - np.nanmin(x)) for x in inside]
    check(max(spread) < 8 and np.std(steps) > 20,
          f"Random S&H, 4 Hz: holds within each cycle (at most {max(spread):.1f} c) and moves between them "
          f"({', '.join(f'{x:+.0f}' for x in steps[:5])} ...)")
    w = lfo_rig(ptf, mix=1.0, wave2=float(BASIC), pos2=0.0, reset2=1.0, src1=float(S_LFO2), dest1=float(D_PITCH2),
                amt1=1 / 12, lfo2_rate=2.0, lfo2_wave=4.0, lfo2_retrig=0.0)
    t = pitch_track(w.note(69, 300, skip=0), 440.0)
    check(abs(np.median(t[t > 0]) - 100) < 6 and abs(np.median(t[t < 0]) + 100) < 6,
          "LFO 2 -> Pitch 2 on oscillator 2: the same steps of +-100 cents")


def polarity_checks(ptf: Path) -> None:
    print("the LFOs' Polarity (Bipolar -1..1, Unipolar 0..1)")
    w = lfo_rig(ptf, src1=float(S_LFO1), dest1=float(D_PITCH1), amt1=1 / 12, lfo1_rate=2.0, lfo1_wave=4.0,
                lfo1_retrig=0.0, lfo1_pol=1.0)
    t = pitch_track(w.note(69, 520, skip=0), 440.0)
    w.off(69, 3)
    hi, lo = t[t > 50], t[t < 50]
    check(abs(np.median(hi) - 100) < 6 and abs(np.median(lo)) < 6 and len(lo) > len(t) // 3,
          f"Square, 2 Hz, Amt 8.3 %, Unipolar: the pitch steps between {np.median(lo):+.0f} and "
          f"{np.median(hi):+.0f} cents (0 and +100)")
    w = lfo_rig(ptf, src1=float(S_LFO1), dest1=float(D_PITCH1), amt1=1 / 12, lfo1_rate=4.0, lfo1_wave=5.0,
                lfo1_retrig=0.0, lfo1_pol=1.0)
    t = pitch_track(w.note(69, 800, skip=0), 440.0)
    cyc = RATE / 4
    inside = [t[int(np.ceil(k * cyc / 512)) + 1:int((k + 1) * cyc // 512) - 1] for k in range(int(len(t) * 512 // cyc))]
    steps = [float(np.nanmedian(x)) for x in inside]
    check(min(steps) > -3 and max(steps) < 103 and np.std(steps) > 10,
          f"Random S&H, Unipolar: every held value between 0 and +100 cents ({', '.join(f'{x:+.0f}' for x in steps[:6])} ...)")
    w = lfo_rig(ptf, mix=1.0, wave2=float(BASIC), pos2=0.0, reset2=1.0, src1=float(S_LFO2), dest1=float(D_PITCH2),
                amt1=1 / 12, lfo2_rate=2.0, lfo2_wave=4.0, lfo2_retrig=0.0, lfo1_pol=1.0)
    t = pitch_track(w.note(69, 300, skip=0), 440.0)
    w.off(69, 3)
    lo2 = float(np.median(t[t < 0])) if np.any(t < 0) else 0.0
    w.set(lfo2_pol=1.0, lfo1_pol=0.0)
    t2 = pitch_track(w.note(69, 300, skip=0), 440.0)
    check(abs(lo2 + 100) < 6 and float(np.nanmin(t2)) > -8,
          f"each LFO its own: LFO 1 Unipolar leaves LFO 2 Bipolar (down to {lo2:+.0f} c); LFO 2 Unipolar goes no "
          f"lower than {np.nanmin(t2):+.0f} c")
    # LFO 1 (Triangle, 1 Hz) -> Position at 50 % from Position 0: the engine voice's frame position
    frac, top = {}, {}
    for pol in (0.0, 1.0):
        w = WT(ptf, **dict(OPEN, wave1=float(BASIC), pos1=0.0, mix=-1.0, src1=float(S_LFO1), dest1=float(D_POS1),
                           amt1=0.5, lfo1_rate=1.0, lfo1_wave=0.0, lfo1_retrig=0.0, lfo1_pol=pol))
        w.r.note_on(0, 45, 127)
        ev = w.r.engine_voice(w.voice)
        pos = []
        for _ in range(690):                     # two cycles
            w.r.render_voice(w.voice, 1)
            pos.append(struct.unpack("<f", bytes(w.r.m.uc.mem_read(ev + WV_POS1, 4)))[0] / (WT_FRAMES - 1))
        pos = np.array(pos)
        frac[pol], top[pol] = float(np.mean(pos < 0.005)), float(pos.max())
    check(0.4 < frac[0.0] < 0.6 and frac[1.0] < 0.03 and abs(top[0.0] - 0.5) < 0.02 and abs(top[1.0] - 0.5) < 0.02,
          f"LFO 1 (triangle) -> Position 1 at 50 % from Position 0: Bipolar sits at 0 for {frac[0.0]:.0%} of the time, "
          f"Unipolar sweeps 0 to {top[1.0]:.2f} and is at 0 {frac[1.0]:.0%} of the time")


def matrix_checks(ptf: Path) -> None:
    print("the mod envelope and the Mod Matrix")
    w = WT(ptf, **dict(OPEN, wave1=float(BASIC), pos1=0.0, mix=-1.0, src1=float(S_MOD), dest1=float(D_POS1), amt1=1.0,
                       menv_att=1.0, menv_dec=1.0, menv_sus=1.0, menv_rel=0.3))
    y = w.note(69, 400, skip=0)
    w.off(69, 3)
    at = {lbl: harmonics_db(y[int(t * RATE):int(t * RATE) + n], note_hz(69))[2] for lbl, t, n in
          (("start", 0.0, 1024), ("rising", 0.5, 1024), ("held", 1.1, 4096))}
    check(at["start"] < -40 and -30 < at["rising"] < -12 and abs(at["held"] + 9.5) < 1.5,
          f"Mod Env (Attack 1 s, Sustain 100 %) -> Position 1 (Sin/Tri/Saw/Sqr): a sine at the start (3rd harmonic "
          f"{at['start']:.0f} dB), between at 0.5 s ({at['rising']:.0f}), a square once it has risen ({at['held']:.1f}; "
          "a square's is -9.5)")
    w = WT(ptf, **dict(OPEN, wave1=float(BASIC), pos1=0.0, mix=-1.0, src1=float(S_LFO1), dest1=float(D_POS), amt1=1.0,
                       lfo1_rate=1.0, lfo1_wave=0.0, lfo1_retrig=0.0))
    y = w.note(45, 400, skip=0)
    w.off(45, 3)
    n = 4096
    h3 = [harmonics_db(y[int(t * RATE):int(t * RATE) + n], note_hz(45))[2] for t in (0.0, 0.5, 1.0)]
    check(h3[0] < -40 and h3[1] > -15 and h3[2] < -40,
          f"LFO 1 (triangle, 1 Hz) -> Position: sine, square, sine again over a cycle (3rd harmonic "
          f"{', '.join(f'{x:.0f}' for x in h3)} dB)")
    lv = {}
    for amt in (0.0, 0.5):
        w = lfo_rig(ptf, src1=float(S_LFO1), dest1=float(D_VOL), amt1=amt, lfo1_rate=2.0, lfo1_wave=4.0,
                    lfo1_retrig=0.0)
        e = block_env(w.note(69, 240, skip=0))
        lv[amt] = (e[4:230].max(), e[4:230].min())
    ratio = 20 * np.log10(lv[0.5][0] / lv[0.5][1])
    check(abs(ratio - 20 * np.log10(3)) < 1.0 and abs(lv[0.5][0] / lv[0.0][0] - 1.5) < 0.05,
          f"LFO 1 (square) -> Volume at 50 %: the level steps x1.5 and x0.5 ({ratio:.1f} dB apart, 9.5 expected)")
    cents_ = {}
    for vel in (30, 127):
        w = WT(ptf, **dict(OPEN, cutoff=500.0, wave1=float(BASIC), pos1=2 / 3, src1=float(S_VEL), dest1=float(D_CUTOFF),
                           amt1=1.0))
        cents_[vel] = centroid(w.note(48, 64, velocity=vel))
    check(cents_[127] > 1.8 * cents_[30],
          f"Velocity -> Cutoff: brighter with velocity 127 than 30 (centroid {cents_[30]:.0f} -> {cents_[127]:.0f} Hz)")
    w = lfo_rig(ptf, src1=float(S_KEY), dest1=float(D_PITCH1), amt1=1.0)
    c = cents(zc_freq(w.note(72, 64)), note_hz(72))
    check(abs(c - 240) < 3, f"Key -> Pitch 1 at 100 %: note 72 plays {c:+.0f} cents high (12 x (72 - 60) / 60 st = +240)")
    w = WT(ptf, **dict(OPEN, wave1=float(BASIC), pos1=2 / 3, src1=float(S_AMP), dest1=float(D_CUTOFF), amt1=0.0,
                       reset1=1.0, reset2=1.0, aenv_rel=0.0))
    a = w.note(57, 30, skip=0)
    w.off(57, 3)
    w.set(src1=float(S_LFO1), dest1=float(D_POS), src2=float(S_MOD), dest2=float(D_POS))
    b = w.note(57, 30, skip=0)
    check(np.array_equal(a, b), "the Init patch's two routings (LFO 1 and Mod Env -> Position) do nothing at Amt 0")
    worst, ok = 0.0, True
    for src in range(7):
        for dest in range(11):
            for amt in (4.0, -4.0):
                w.set(src1=float(src), dest1=float(dest), amt1=amt, lfo1_rate=5.0)
                y = w.note(60, 6)
                w.off(60, 2)
                ok &= bool(np.isfinite(y).all())
                worst = max(worst, float(np.abs(y).max()))
    check(ok and worst <= 2.0, f"every source to every destination at +-400 %: finite, bounded (peak {worst:.2f})")


def edit_patch_checks(ptf: Path) -> None:
    print("the Edit Patch screen's model logo")
    r = Rig(ptf)
    m = r.m
    r.configure((VAP, ACD, PERC), (1, 1, 1))
    r.pattern()
    screen_globals(r)
    mgr = sr.SynthRig.__dict__ and 0x20027D64
    image = bytes(m.uc.mem_read(0x60000000, 0x800000))
    hook = bl_target(image, 0x60000000, 0x6011EA58)
    params = m.alloc(0x40)
    m.write(params, struct.pack("<5I", 0x60038660, 0, 0, 0x100, m.r32(m.r32(mgr) + 8) + 0xC4 if m.r32(mgr) else 0))
    cid = m.call(0x600CB64C, mgr, params, max_insns=2_000_000) & 0xFF
    icons = m.alloc(0x20)

    def draw(model_slot_wave: bool):
        m.write(icons, struct.pack("<hhBBhhBB", 116, 49, 13, 0, 0, 0, 0, 0))
        m.call(0x60101DF8, mgr, cid, icons)                       # set_data, as the screen does
        m.call(0x60101CAC, mgr, cid, 0x20)                        # set_style
        m.call(hook | 1, mgr, cid, 0x60038660, max_insns=2_000_000)  # set_text "" (the hooked call)
        m.call(0x60101C6C, mgr, cid, 0x32, 0x2D)
        m.call(0x60101C8C, mgr, cid, 0x6C, 0x28)
        ctrl = m.r32(mgr + 0x10 + 4 * cid)
        m.display_list.clear()
        m.call(m.r32(m.r32(ctrl)), ctrl, max_insns=5_000_000)
        dl = list(m.display_list)
        bitmaps = sum(1 for w_ in dl if w_ == 0x1F000001)
        texts = []
        for k, w_ in enumerate(dl):
            if w_ == 0xFFFFFF0C:
                raw = b"".join(struct.pack("<I", x) for x in dl[k + 3:k + 8])
                texts.append(raw.split(b"\0")[0].decode("latin-1"))
        return m.uc.mem_read(icons + 4, 1)[0], bitmaps, texts
    got_vap = draw(False)
    r.swap(VAP)
    got_wave = draw(True)
    check(got_vap[0] == 13 and got_vap[1] >= 1 and "WAVE" not in got_vap[2],
          f"S1 on VAP: the label keeps its logo (icon {got_vap[0]}, {got_vap[1]} bitmap), no text")
    check(got_wave[0] == 0 and got_wave[1] == 0 and "WAVE" in got_wave[2],
          f"S1 on WAVE: no logo (icon {got_wave[0]}, {got_wave[1]} bitmaps) and the text {got_wave[2]}")

# ---- 17. stress ---------------------------------------------------------------------------

GARBAGE = [float("nan"), float("inf"), float("-inf"), -1e9, 1e9, -1.0, 1e-40, 3.4e38]
SLOT_SCRATCH = (0x17C, 0x57C)       # a slot's scratch L and R (renders of 2+ voices)
FW_MALLOC = 0x600A3588              # the allocation the patches' menu hooks use (veneer to 0x600a1ec0)
HEAP = (0x2002FC68, 0x20051C68)     # the heap (section 15): WAVE's builder rewrites the objects it holds
BLOCK_LIMIT = 5_000_000             # instructions a stress block may take before it counts as hung
SAMPLE = 200                        # the stress run counts the instructions of every 200th block
OUT_BUFS = (0x200278E4, 0x20027AE4)  # the synth output's float buffers (renders of 1 voice)


def builder_ranges(ptf: Path) -> list[tuple[int, int]]:
    """The patch's code that may write the heap (WAVE's and PHASE's builders and their
    helpers, the Macro defaults; the Config menu's two descriptors, made by the menu hook and
    pointed at their lists by menu_refresh), from the build's symbol table: (start, end) as
    placed in `ptf`."""
    here = Path(__file__).resolve().parent.parent.parent / "patch" / "synths"
    sym = next((p for p in (here / "build" / "synths.sym", here / "prebuilt" / "synths.sym") if p.exists()), None)
    if sym is None:
        return [CODE]
    syms = sorted((int(a, 16), n) for a, _, n in (ln.split()[:3] for ln in sym.read_text().splitlines() if ln.strip()))
    base = min(a for a, _ in syms) & ~0xFFF
    link = 0x601C4000 if base <= 0x601C4000 else base
    names = {"wave_builder", "wave_rewrite", "wave_macros", "str_set", "str_init", "str_free", "set_display",
             "set_cells", "sub_free", "page_free", "vec_free", "append_own", "phase_builder", "phase_rewrite",
             "phase_macros", "menu_refresh", "mt_syn_last_item"}
    out = []
    for k, (a, n) in enumerate(syms):
        if n.split(".")[0] in names:        # GCC's clones keep the name before a dot (set_cells.isra.0)
            end = syms[k + 1][0] if k + 1 < len(syms) else a + 0x400
            out.append((a - link + CODE[0], end - link + CODE[0]))
    return out or [CODE]


def stress_checks(ptf: Path, blocks: int = 2000, seed: int = 11, phase: bool = False) -> None:
    """With `phase` (tools/emu/test_phase.py), the swaps set "PHASE replaces" too."""
    print(f"stress: {blocks} blocks of random notes, parameters (garbage too), swaps{' (WAVE and PHASE)' if phase else ''}, "
          "Model list picks, polyphony changes, restarts")
    rnd = np.random.default_rng(seed)
    r = Rig(ptf)
    m = r.m
    r.configure((VAP, VAP, ACD), (3, 3, 2))
    r.pattern()
    r.swap(VAP)
    image = bytes(m.uc.mem_read(0x60000000, 0x800000))
    restart = bl_target(image, 0x60000000, 0x601193E6)
    project_hook = bl_target(image, 0x60000000, BL_RECORDS)
    m.stub_function(CFG_MENU_ITEM, lambda mm: 0)         # the restart hook chains to it
    m.stub_function(CFG_MENU_FOLDER, lambda mm: 0)
    m.stub_function(ATEXIT, lambda mm: 0)
    m.stub_function(APPLY_RECORDS, lambda mm: 0)         # the restart applies the records itself
    # On a real start the menu is built once; here every emulated restart runs every patch's
    # menu hook again, so their menu nodes come from scratch memory, not the heap (they
    # would leak it full, and stock VAP writes past a failed allocation)
    restarting = [False]
    menu_blocks: list[tuple[int, int]] = []     # the scratch stand-ins for the heap (the menu hook may write them)

    def menu_malloc(uc, a, size_, user):
        if restarting[0]:
            n = max(8, m.arg(0))
            p = m.alloc(n)
            menu_blocks.append((p, p + n))
            uc.reg_write(UC_ARM_REG_R0, p)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
    m.uc.hook_add(UC_HOOK_CODE, menu_malloc, begin=FW_MALLOC, end=FW_MALLOC)
    lo, hi = CODE
    writers = builder_ranges(ptf)
    ram = WHERE["state"], WHERE["state"] + WHERE["size"]
    late = LATE
    errors: list[str] = []
    state = {"block": 0}
    from machine import STACK_TOP

    def allowed(a: int, pc: int) -> bool:
        if ram[0] <= a < ram[1] or late[0] <= a < late[1] or STACK_TOP - 0x4000 <= a < STACK_TOP \
                or MODEL_TABLE <= a < MODEL_TABLE + 20:
            return True
        if any(b <= a < b + 512 for b in OUT_BUFS):
            return True
        if (HEAP[0] <= a < HEAP[1] or any(b <= a < e for b, e in menu_blocks)) \
                and any(s_ <= pc < e_ for s_, e_ in writers):
            return True
        for i in range(3):
            s = r.slot(i)
            if s + SLOT_SCRATCH[0] <= a < s + SLOT_SCRATCH[1] + 512:
                return True
        return False

    # The write watch reads the PC (slow, through Unicorn's Python API) only while the patch
    # runs: `inside` counts the trampolines entered and not yet left (code hooks at their
    # entries and at their two exits: the return and the jump on to VAP), plus the screen
    # updates and restarts the loop calls. Stock code's own writes, most of them, cost a
    # cheap early return.
    inside, checked, watch = [0], [0], []

    def on_write(uc, access, a, size, value, user):
        pc = uc.reg_read(UC_ARM_REG_PC)
        if lo <= pc < hi:
            checked[0] += 1
            if not allowed(a, pc):
                errors.append(f"block {state['block']}: write of {size} bytes at {a:#x} from {pc:#x}")

    def watch_on():
        for a0, a1 in ((0x20000000, 0x20058000), (0x20200000, 0x20208000), (0x80000000, 0x84000000), (0, 0x20000)):
            watch.append(m.uc.hook_add(UC_HOOK_MEM_WRITE, on_write, begin=a0, end=a1 - 1))

    def watch_off():
        while watch:
            m.uc.hook_del(watch.pop())

    def enter(uc, a, s_, u):
        inside[0] += 1
        if inside[0] == 1:
            watch_on()

    def leave(uc, a, s_, u):
        if inside[0]:
            inside[0] -= 1
            if not inside[0]:
                watch_off()
    for off in (sr.V_DELETE, sr.V_MIDI, sr.V_SET_PARAM, sr.V_RENDER, sr.V_POST, sr.V_SOUNDING):
        tramp = m.r32(sr.VOICE_VT[VAP] + off) & ~1
        code_ = bytes(m.uc.mem_read(tramp, 0x24))
        exits = [tramp + i for i in range(0, 0x24, 2)
                 if struct.unpack_from("<H", code_, i)[0] in (0xBD1E, 0x4760)]   # pop {r1-r4, pc}; bx ip
        if len(exits) != 2:
            errors.append(f"trampoline at {tramp:#x}: exits not found ({[hex(x) for x in exits]})")
        m.uc.hook_add(UC_HOOK_CODE, enter, begin=tramp, end=tramp)
        for x_ in exits:
            m.uc.hook_add(UC_HOOK_CODE, leave, begin=x_, end=x_)

    def watched(f, *a):
        inside[0] += 1
        if inside[0] == 1:
            watch_on()
        try:
            return f(*a)
        finally:
            inside[0] -= 1
            if not inside[0]:
                watch_off()

    def start():
        """A start: the menu constructor's reset, the table fresh (.data), the project's records
        applied by the stock code (here: each slot's model from the table), fresh voices."""
        restarting[0] = True
        watched(m.call, restart | 1, 0, 0, 0, 0, 0, 0)
        restarting[0] = False
        for k, md in enumerate(MODELS):
            m.w32(MODEL_TABLE + 4 * k, md)
        watched(m.call, project_hook | 1, sr.S, RECORDS, 1)
        for i in range(3):
            want = r.table()[m.r32(RECORD_MODEL + 0x40 * i)]
            if r.model(i) != want:
                r.set_model(i, want)
            elif r.model(i) in (VAP, WAVE, PHASE):
                r.stock_rebuild(i)
    update = m.r32(m.r32(sr.SYNTH_OUT))
    held = {0: [], 1: [], 2: []}
    events: dict[str, int] = {}
    most = 0

    def note(what):
        events[what] = events.get(what, 0) + 1

    # struct wt_voice (wt_engine.h): owner, slot, volume, phase[2], pos[2], note, pitch,
    # glide_step, bend, aenv {stage, level}, fenv {stage, level}, ic[4], noise, 4 flag bytes
    floats = {"volume": 8, "pos0": 20, "pos1": 24, "note": 28, "pitch": 32, "glide": 36, "bend": 40,
              "amp level": 48, "filter level": 56, "ic0": 60, "ic1": 64, "ic2": 68, "ic3": 72}
    stages = {"amp stage": 44, "filter stage": 52}
    # struct ph_voice (ph_engine.h): owner, slot (16 bits), engine (1), pad, volume, note, bend,
    # aenv {stage, level}, gate
    ph_floats = {"volume": 8, "note": 12, "bend": 16, "amp level": 24}

    def bad_state() -> str | None:
        vs = WHERE["voice_size"]
        if vs != 160:
            return f"struct wt_voice is {vs} bytes: the test's layout is out of date"
        for k in range(8):
            raw = bytes(m.uc.mem_read(WHERE["state"] + WHERE["voice"] + k * vs, vs))
            owner, slot, engine = struct.unpack_from("<IHB", raw, 0)
            if not owner:
                continue
            if engine == 1:
                f = {n: struct.unpack_from("<f", raw, o)[0] for n, o in ph_floats.items()}
                stage = struct.unpack_from("<I", raw, 20)[0]
                if slot > 2 or not all(np.isfinite(x) for x in f.values()) or stage > 4 \
                        or not 0 <= f["amp level"] <= 1.0001 or raw[28] > 1:
                    return f"entry {k} (PHASE): slot {slot}, {f}, stage {stage}, gate {raw[28]}"
                continue
            if engine != 0:
                return f"entry {k}: engine {engine}"
            f = {n: struct.unpack_from("<f", raw, o)[0] for n, o in floats.items()}
            st = {n: struct.unpack_from("<I", raw, o)[0] for n, o in stages.items()}
            levels_ok = 0 <= f["amp level"] <= 1.0001 and 0 <= f["filter level"] <= 1.0001
            if slot > 2 or not all(np.isfinite(x) for x in f.values()) or any(x > 4 for x in st.values()) \
                    or not levels_ok:
                return f"entry {k}: slot {slot}, {f}, {st}"
        if m.uc.mem_read(r.st("fx_ready"), 1)[0]:     # struct wt_fx (wt_fx.h): the delay line, then its fields
            for i in range(3):
                a = LATE[0] + i * WHERE["fx_size"]
                buf = np.frombuffer(bytes(m.uc.mem_read(a, 4 * 512)), "<f4")
                running, pos, phase, lpl, lpr, mix, depth = struct.unpack(
                    "<IIfffff", bytes(m.uc.mem_read(a + WHERE["fx_running"], 28)))
                ok = np.isfinite(buf).all() and np.abs(buf).max() <= 4.0 and running <= 1 and pos < 512 \
                    and 0.0 <= phase < 1.0 and 0.0 <= mix <= 1.0 and 0.0 <= depth <= 1.0 \
                    and np.isfinite([lpl, lpr]).all() and abs(lpl) <= 4.0 and abs(lpr) <= 4.0
                if not ok:
                    return f"effect {i}: running {running}, pos {pos}, phase {phase}, taps {lpl} {lpr}, mix {mix}, " \
                           f"depth {depth}, delay line peak {np.abs(buf).max()}"
        return None

    # Every block is bounded by the call's instruction limit (a block that doesn't return
    # within it is an error); instructions are counted, a Python call per instruction, only
    # on every SAMPLE-th block, which keeps 20,000 blocks to minutes rather than hours
    n_count = [0]
    t0 = time.time()
    for b in range(blocks):
        state["block"] = b
        x = rnd.random()
        slot = int(rnd.integers(0, 3))
        if x < 0.25 and len(held[slot]) < 6:
            n = int(rnd.integers(0, 128))
            if n not in held[slot]:
                r.note_on(slot, n, int(rnd.integers(1, 128)))
                held[slot].append(n)
                note("note on")
        elif x < 0.45 and held[slot]:
            r.note_off(slot, held[slot].pop(int(rnd.integers(len(held[slot])))))
            note("note off")
        elif x < 0.5:
            r.bend(slot, int(rnd.choice([-32768, -8192, 0, 8191, 32767, int(rnd.integers(-32768, 32768))])))
            note("bend")
        elif x < 0.62 and r.model(slot) in (VAP, WAVE, PHASE):
            idx = int(rnd.integers(0, 60 if r.model(slot) == VAP else 66))
            if r.model(slot) != VAP and rnd.random() < 0.5:
                # garbage reaches the WAVE and PHASE voices only: stock VAP's own render hangs on some (NaN LFO
                # settings, stock firmware too), and the UI keeps the patch in range
                v = float(rnd.choice(GARBAGE)) if rnd.random() < 0.6 else float(rnd.uniform(-1e4, 1e4))
                r.set_param(slot, idx, v, patch=False)
                note("garbage parameter")
            else:
                lo_, hi_ = r.ranges(slot)[idx]
                r.set_param(slot, idx, float(rnd.uniform(lo_, hi_)))
                note("parameter")
        elif x < 0.64:
            r.cc(slot, int(rnd.choice([CC_CUTOFF, CC_RESONANCE, 102, 103, 104, 105, 106, 107])), int(rnd.integers(0, 128)))
            note("CC")
        elif x < 0.66:
            m.w8(r.st("menu"), int(rnd.integers(0, 8)))     # 6, 7: out of range (counts as Off)
            if phase:
                m.w8(r.st("ph_menu"), int(rnd.integers(0, 8)))
            watched(r.config)
            held[slot].clear()
            note("swap")
        elif x < 0.67:
            others = sum(m.r32(r.slot(i) + sr.SLOT_NVOICES) for i in range(3) if i != slot)
            r.set_poly(slot, int(rnd.integers(1, max(1, 8 - others) + 1)))
            held[slot].clear()
            note("polyphony")
        elif x < 0.675:
            r.pick(slot, MODELS[int(rnd.choice([0, 2, 2]))])  # ACD or VAP (either may be WAVE)
            held[slot].clear()
            note("Model list pick")
        elif x < 0.677:
            start()
            for i in range(3):
                held[i].clear()
            note("restart")
        elif x < 0.70:
            watched(r.pattern)
            note("pattern update")
        counting = b % SAMPLE == 0
        if counting:
            n_count[0] = 0
            cnt = m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: n_count.__setitem__(0, n_count[0] + 1))
        try:
            m.call(update, sr.SYNTH_OUT, max_insns=BLOCK_LIMIT)
        except RuntimeError as e:
            errors.append(f"block {b}: {e}")
            break
        finally:
            if counting:
                m.uc.hook_del(cnt)
        if counting:
            most = max(most, n_count[0])
        if (b + 1) % 1000 == 0:
            print(f"    block {b + 1} of {blocks}: {len(errors)} problems, {time.time() - t0:.0f} s", flush=True)
        if b % 50 == 0:
            what = bad_state()
            if what:
                errors.append(f"block {b}: {what}")
        if len(errors) > 10:
            break
    check(not errors and checked[0] > blocks * 100,
          f"no fault, no write outside the patch's RAM, the output buffers, the model table, the stack and (from "
          f"WAVE's builder) the heap ({checked[0]:,} writes by the patch checked), engine states finite "
          f"({len(errors)} problems{': ' + errors[0] if errors else ''})")
    check(most < 2_000_000, f"the synth output's update stays bounded: every block within {BLOCK_LIMIT:,} "
                            f"instructions, at most {most:,} in the {blocks // SAMPLE} blocks counted")
    print("    events: " + ", ".join(f"{k} {v}" for k, v in sorted(events.items())))
    # normal settings again: a WAVE slot plays
    w = WT(ptf, pos1=0.5)
    init = w.r.params(0)
    for idx in range(len(init)):
        w.r.set_param(0, idx, float("nan"))
    w.r.note_on(0, 60, 100)
    y_nan = w.r.render_voice(w.voice, 10)
    for idx, val in enumerate(init):
        w.r.set_param(0, idx, val)
    w.off(60, 100)
    y = w.note(60, 20)
    check(bool(np.isfinite(y_nan).all()) and np.abs(y_nan).max() <= 2.0 and np.abs(y).max() > 0.02,
          f"all {len(init)} parameters NaN: bounded output (peak {np.abs(y_nan).max():.2f}); Init values again: it plays "
          f"(peak {np.abs(y).max():.2f})")


# ---- 18. WAV files ------------------------------------------------------------------------

def write_wav(path: Path, left: np.ndarray, right: np.ndarray | None = None) -> None:
    import wave
    right = left if right is None else right
    data = np.stack([left, right], 1).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(data)


def phrase(r: Rig, slot: int, notes, step_blocks: int, gate: float, tail: int) -> np.ndarray:
    """Notes (a list of chords) through the host, rendered by the synth output object."""
    out = []
    for chord in notes:
        for n in chord:
            r.note_on(slot, n, 100)
        on = int(step_blocks * gate)
        out.append(r.render(on, fast=True)[slot][0])
        for n in chord:
            r.note_off(slot, n)
        out.append(r.render(step_blocks - on, fast=True)[slot][0])
    out.append(r.render(tail, fast=True)[slot][0])
    return np.concatenate(out)


def wave_rig(ptf: Path, poly: int, **params) -> Rig:
    r = Rig(ptf)
    r.configure((VAP, PERC, PERC), (poly, 1, 1))
    r.swap(VAP)
    r.pattern()
    for k, val in params.items():
        r.set_param(0, globals()["P_" + k.upper()], val)
    return r


def wav_checks(ptf: Path, stock: Path, folder: Path) -> None:
    print(f"WAV files in {folder}")
    folder.mkdir(parents=True, exist_ok=True)
    line = [[48], [55], [60], [63], [67], [72], [67], [63]]
    r = Rig(stock, False)
    r.configure((VAP, PERC, PERC), (4, 1, 1))
    write_wav(folder / "init-vap.wav", phrase(r, 0, line, 60, 0.6, 200))
    r = wave_rig(ptf, 4)
    write_wav(folder / "init-wave.wav", phrase(r, 0, line + [[48, 55, 60, 64]], 60, 0.6, 200))
    r = wave_rig(ptf, 1, cutoff=12000.0, envamt=0.0, aenv_sus=1.0, aenv_rel=0.2, track=0.0, type=4.0, res=0.2,
                 detune=0.0)
    out = []
    for bank in range(WHERE["banks"] + 2):    # Wave 0-8, Position swept 0 -> 1
        r.set_param(0, P_WAVE1, float(bank))
        r.set_param(0, P_WAVE2, float(bank))
        r.note_on(0, 45, 100)
        for b in range(260):
            if b % 4 == 0:
                r.set_param(0, P_POS1, min(1.0, b / 240))
                r.set_param(0, P_POS2, min(1.0, b / 240))
            out.append(r.render(1, fast=True)[0][0])
        r.note_off(0, 45)
        out.append(r.render(60, fast=True)[0][0])
    write_wav(folder / "banks-position-sweep.wav", np.concatenate(out))
    r = wave_rig(ptf, 1, wave1=float(BASIC), wave2=float(BASIC), pos1=2 / 3, pos2=2 / 3, tune2=7.0, type=0.0, res=1.2,
                 envamt=0.0, aenv_sus=1.0, track=0.0)
    out = []
    r.note_on(0, 36, 110)
    for b in range(600):
        if b % 4 == 0:
            r.set_param(0, P_CUTOFF, float(100 * 2 ** (7 * (1 - abs(b / 300 - 1)))))
        out.append(r.render(1, fast=True)[0][0])
    r.note_off(0, 36)
    out.append(r.render(100, fast=True)[0][0])
    write_wav(folder / "filter-sweep.wav", np.concatenate(out))
    r = wave_rig(ptf, 6, pos1=0.3, pos2=0.6, detune=12.0, tune2=12.0, cutoff=1500.0, envamt=0.3, fenv_att=1.0,
                 fenv_dec=2.0, fenv_sus=0.3, aenv_att=0.8, aenv_dec=1.0, aenv_sus=0.8, aenv_rel=2.0, track=0.5)
    write_wav(folder / "pad.wav", phrase(r, 0, [[48, 55, 60, 64], [45, 52, 57, 60], [41, 48, 53, 57]], 400, 0.7, 700))
    r = wave_rig(ptf, 4, wave1=float(SUPERSAW), wave2=float(SUPERSAW), pos1=0.6, pos2=0.5, tune2=12.0, type=0.0,
                 cutoff=2500.0, res=0.4, envamt=0.2, aenv_att=0.05, aenv_sus=0.9, aenv_rel=0.8, track=0.5)
    write_wav(folder / "super-saw.wav", phrase(r, 0, [[57, 60, 64], [55, 59, 62], [53, 57, 60], [52, 55, 59]],
                                               180, 0.8, 300))
    r = wave_rig(ptf, 1, wave1=float(RESONANT), wave2=float(RESONANT), digital=1.0, type=0.0, cutoff=6000.0, res=0.6,
                 envamt=0.0, aenv_sus=1.0, aenv_rel=0.3, track=0.0)
    out = []
    r.note_on(0, 45, 100)
    for b in range(700):                      # the Resonant bank, Digital mode: Position steps frame by frame
        if b % 4 == 0:
            v = float(min(1.0, b / 640))
            r.set_param(0, P_POS1, v)
            r.set_param(0, P_POS2, v)
        out.append(r.render(1, fast=True)[0][0])
    r.note_off(0, 45)
    out.append(r.render(80, fast=True)[0][0])
    write_wav(folder / "digital-resonant-sweep.wav", np.concatenate(out))
    # the chorus, in stereo: the same chords dry (Effect None), then with the Chorus at its defaults, then at Depth
    # 100 % and Rate 0.86 Hz (about the Juno's Chorus II)
    r = wave_rig(ptf, 4, aenv_att=0.3, aenv_sus=0.9, aenv_rel=1.0, cutoff=3000.0, envamt=0.1)
    ls, rs = [], []
    for eff, depth, rate in ((0.0, 0.5, 0.5), (1.0, 0.5, 0.5), (1.0, 1.0, fx_value(0.86))):
        r.set_param(0, P_EFFECT, eff)
        r.set_param(0, P_FX_DEPTH, depth)
        r.set_param(0, P_FX_RATE, rate)
        for chord in ([48, 55, 60, 64], [45, 52, 57, 60]):
            for n in chord:
                r.note_on(0, n, 100)
            a, b = graph_lr(r, 300)
            for n in chord:
                r.note_off(0, n)
            c, d = graph_lr(r, 150)
            ls += [a, c]
            rs += [b, d]
    write_wav(folder / "chorus.wav", np.concatenate(ls), np.concatenate(rs))
    check(True, "wrote init-vap.wav, init-wave.wav (ending on a chord), banks-position-sweep.wav, filter-sweep.wav "
                "(the ladder), pad.wav, super-saw.wav, digital-resonant-sweep.wav, chorus.wav (stereo: dry, Chorus, "
                "Chorus at Depth 100 %)")


def main() -> int:
    global WHERE, CODE, LATE
    args = sys.argv[1:]
    stress_blocks = 2000
    if "--stress" in args:
        i = args.index("--stress")
        stress_blocks = int(args[i + 1])
        del args[i:i + 2]
    quick = "--quick" in args
    if quick:
        args.remove("--quick")
        stress_blocks = min(stress_blocks, 200)
    ptf, stock = Path(args[0]), Path(args[1])
    wav_dir = Path(args[2]) if len(args) > 2 else None
    CODE = patch_code(ptf, "synths")
    LATE = patch_late_ram(ptf, "synths")
    WHERE = ram_map(Machine(ptf), ptf)
    if quick:                               # the mix-dependent parts only
        hook_checks(ptf, stock)
        wave_model_checks(ptf)
        swap_checks(ptf)
        menu_checks(ptf)
        model_list_checks(ptf)
        edit_patch_checks(ptf)
        file_checks(ptf, stock)
        save_as_checks(ptf)
        patch_file_checks(ptf, stock)
        macro_checks(ptf)
        export_checks(ptf)
        fx_checks(ptf)
        stress_checks(ptf, stress_blocks)
        print(f"{len(failures)} failure(s) (--quick: rig, stock output, sound, allocation and bank checks skipped)")
        return 1 if failures else 0
    rig_checks(stock)
    hook_checks(ptf, stock)
    stock_output_checks(ptf, stock)
    wave_model_checks(ptf)
    swap_checks(ptf)
    menu_checks(ptf)
    model_list_checks(ptf)
    edit_patch_checks(ptf)
    file_checks(ptf, stock)
    save_as_checks(ptf)
    patch_file_checks(ptf, stock)
    osc_checks(ptf)
    filter_checks(ptf)
    drive_checks(ptf)
    fx_checks(ptf)
    analog_filter_checks(ptf)
    supersaw_checks(ptf)
    saw_checks(ptf)
    digital_checks(ptf)
    lfo_checks(ptf)
    polarity_checks(ptf)
    matrix_checks(ptf)
    glide_checks(ptf)
    init_level_checks(ptf)
    allocation_checks(ptf, stock)
    bank_checks(ptf)
    voices_checks(ptf)
    macro_checks(ptf)
    export_checks(ptf)
    stress_checks(ptf, stress_blocks)
    if wav_dir:
        wav_checks(ptf, stock, wav_dir)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
