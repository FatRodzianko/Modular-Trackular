"""Whole-bundle cost: what a bundle adds to the audio block's work over stock firmware, in
the worst-case project of the timing headroom runs (docs/TIMING_HEADROOM_PLAN.md
sections 13 and 14).

  python tools/emu/test_bundle_cost.py <bundle.ptf> <stock TrackerPlus_1.2.0.ptf> [--quick]

The scenario is tools/make_headroom_project.py's project, built in RAM: its two patterns
(all 16 tracks busy on every step: sample tracks with micro-moves, Chance conditions,
rolls and arps; synth tracks with chords and CCs; MIDI tracks with CCs and G), played in
song mode (1 2 1 2) at 800 BPM, Synth 1-3 at Polyphony 3/3/2, every track unmuted. The
mods in the bundle are switched on in RAM as the hardware runs had them: Global Swing 66,
FX Interpolation MIDI CC and Synths On, the polymeter lengths. condtrig and legato play
from the patterns' FX.

1. Sequencer side (both modes): instructions inside the bl seq_advance at 0x600aa7f8 per
   audio block (every hook chained there, and the ITCM hooks inside seq_advance), the
   engine stubbed, over four passes of the song. The bundle's worst block minus stock's
   worst block is at most SEQ_LIMIT.
2. Whole block (full mode only): the real render too, Synth 1-3 sounding, and masterfx's
   heaviest reverb and delay (Shimmer, "Gated") when the bundle has masterfx. The engine
   side (a block's work outside the bl seq_advance) over 16 blocks around stock's worst
   sequencer block, the same blocks on both firmwares. The bundle's sequencer-side extra
   plus its engine-side extra (worst block minus stock's worst block, each) is at most
   BLOCK_LIMIT. Added this way, the two worsts count as if they fell in one block.

The limits are in instructions; K converts them to the device's cycles. The hardware
figures they rest on are below. Not covered: humanize (it acts while recording),
slicethru (the emulator has no sample data), resample (only while recording).
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
import tempfile
from pathlib import Path

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R3

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import make_headroom_project as hp  # noqa: E402
import seq_timing as st  # noqa: E402
from layout import patch_ram  # noqa: E402
from reverb_rig import ram_map  # noqa: E402

# Hardware figures (timing headroom runs, 2026-10-27; TIMING_HEADROOM_PLAN.md section 14).
# A block is 128 / 44,100 s, about 1,532,500 cycles at 528 MHz. Patch code costs about
# K cycles per instruction (3.7 on the sequencer side, 4.5 for masterfx's effects: the
# no-synth runs, patch/all minus stock, against this scenario's instruction counts). With
# no synth voices, stock's worst block was 46.0 % and patch/all's (every feature on) 66.5 %.
K = 4
BLOCK_CYCLES = 1_532_500
SEQ_LIMIT = 20_000                  # about 5 % of a block
BLOCK_LIMIT = 60_000                # about 16 %: with stock's 46 %, under 65 %

APP, APP_AUDIO_BLOCK = 0x200170E0, 0x600AA7C9
SEQ_CALL = 0x600AA7F8               # app_audio_block: bl seq_advance
QUIET = (0x6010C2C0, 0x6010C2EC, 0x600AB89C)   # stock meter (no hal timer here), MIDI in
ENGINE = (0x600AA848, 0x600AA850)   # the engine's calls in app_audio_block
PASSES = 4
WINDOW = 16                         # whole blocks counted, around the worst sequencer block
WARMUP = 4                          # blocks after the engine starts that carry its catch-up work
CLEAR_MAX = 64                      # blocks for masterfx's switch to clear its buffers
SW_MAGIC = 0x32534D47               # swing.c: state magic; +8 on, +9 amount - 25
IP_MAGIC = 0x36504D4D               # interpolate.c: +8 MIDI CC, +16 synths
PM_MAGIC = 0x314D504D               # polymeter.c: +4 len[pattern][track]
MX_MAGIC, MX_READY = 0x3558464D, 0x59444552    # masterfx.c struct mfx_state (test_masterfx.py):
MX_REVERB, MX_LOAD, MX_DELAY, MX_ON, MX_ACTIVE, MX_DL_ACTIVE = 8, 11, 13, 16, 20, 36
REVERB_GATED, DELAY_BBD = 5, 2      # the worst pair since mf7 ("Gated" moved to the reverbs)
STOCK_CACHE = Path(__file__).resolve().parent.parent.parent / "build" / "bundle_cost_stock.json"
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def contained(ptf: Path) -> set[str]:
    m = ptf.with_suffix(".map.json")
    return {p["name"] for p in json.loads(m.read_text())["patches"]} if m.is_file() else set()


def share(insns: int) -> str:
    return f"{100 * insns * K / BLOCK_CYCLES:.1f} % of a block at {K} cycles each"


class Scenario:
    """The headroom project playing on one firmware; blocks through app_audio_block."""

    def __init__(self, ptf: Path, workspace: Path):
        self.ptf, self.mods = ptf, contained(ptf)
        self.rig = hp.Rig(ptf, workspace)
        m = self.m = self.rig.m
        for a in QUIET:
            m.stub_function(a, lambda mm: 0)
        self.engine = False
        for a in ENGINE:
            m.uc.hook_add(UC_HOOK_CODE, self._skip_engine, begin=a, end=a)
        m.write(hp.TEMPO, struct.pack("<f", hp.TEMPO_MAX))
        for a, n in hp.MUTES:
            m.write(a, bytes(n))
        self.pats = [hp.pattern_data(0), hp.pattern_data(1)]
        m.stub_function(st.FM_REQUEST_PATTERN_LOAD, self._load)
        # counters
        self.n = 0
        self.seq_from = None
        self.seq: list[int] = []
        self.notes = {"sample": 0, "synth": 0}
        m.uc.hook_add(UC_HOOK_CODE, self._count)
        m.uc.hook_add(UC_HOOK_CODE, self._seq_in, begin=SEQ_CALL, end=SEQ_CALL)
        m.uc.hook_add(UC_HOOK_CODE, self._seq_out, begin=SEQ_CALL + 4, end=SEQ_CALL + 4)
        m.uc.hook_add(UC_HOOK_CODE, self._sample, begin=hp.VOICE_NOTE_ON, end=hp.VOICE_NOTE_ON)
        m.uc.hook_add(UC_HOOK_CODE, self._out, begin=hp.NOTE_ON_OUT, end=hp.NOTE_ON_OUT)

    def _skip_engine(self, uc, address, size, user):
        if not self.engine:
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def _load(self, mm):
        mm.write(mm.r32(st.SEQ + 0x18), self.pats[(mm.arg(1) - 1) % 2])
        return 1

    def _count(self, uc, address, size, user):
        self.n += 1

    def _seq_in(self, uc, address, size, user):
        self.seq_from = self.n

    def _seq_out(self, uc, address, size, user):
        if self.seq_from is not None:
            self.seq.append(self.n - self.seq_from)
            self.seq_from = None

    def _sample(self, uc, address, size, user):
        self.notes["sample"] += 1

    def _out(self, uc, address, size, user):
        if uc.reg_read(UC_ARM_REG_R3):          # (note, velocity, channel, synth)
            self.notes["synth"] += 1

    def block(self) -> int:
        """One audio block; its instructions."""
        n0 = self.n
        self.m.call(APP_AUDIO_BLOCK, APP, 0, 0, 128, max_insns=50_000_000)
        return self.n - n0

    def mods_on(self, masterfx: bool) -> str:
        """The bundle's mods as the hardware runs had them. Returns what masterfx chose."""
        m, ptf = self.m, self.ptf
        self.block()                            # stopped: swing sets up its state
        if "swing" in self.mods:
            sw = patch_ram(ptf, "swing")
            check(m.r32(sw) == SW_MAGIC, "swing's state is set up")
            m.w8(sw + 8, 1)
            m.w8(sw + 9, hp.SWING - 25)
        if "interpolate" in self.mods:
            ip = patch_ram(ptf, "interpolate")
            m.w32(ip, IP_MAGIC)
            m.w8(ip + 8, 1)
            m.w8(ip + 16, 1)
        if "polymeter" in self.mods:
            pm = patch_ram(ptf, "polymeter")
            m.write(pm, struct.pack("<I", PM_MAGIC) + bytes(256 * 16))
            for pattern in (1, 2):
                for t, n in hp.POLY_LENGTHS.items():
                    m.w8(pm + 4 + 16 * pattern + t, n)
        if not (masterfx and "masterfx" in self.mods):
            return ""
        mx = ram_map(m, ptf)["mx"]              # masterfx's state isn't at the start of its RAM
        m.w32(mx, MX_MAGIC)
        m.w8(mx + MX_REVERB, REVERB_GATED)
        m.w8(mx + MX_DELAY, DELAY_BBD)
        m.w8(mx + MX_LOAD, 0)
        m.w32(mx + MX_ON, MX_READY)             # as after the Pattern screen's first update
        self.engine = True
        for blocks in range(1, CLEAR_MAX + 1):  # stopped: the switch clears the buffers
            self.block()
            if self.active(mx) == (REVERB_GATED, DELAY_BBD):
                break
        self.engine = False
        ok = self.active(mx) == (REVERB_GATED, DELAY_BBD)
        check(ok, f"masterfx runs \"Gated\" and BBD after {blocks} blocks of clearing")
        return " with \"Gated\" and BBD"

    def active(self, mx: int) -> tuple[int, int]:
        """The reverb and the delay masterfx runs."""
        return self.m.uc.mem_read(mx + MX_ACTIVE, 1)[0], self.m.uc.mem_read(mx + MX_DL_ACTIVE, 1)[0]

    def play(self) -> None:
        m = self.m
        cur, nxt = m.alloc(hp.PAT_SIZE), m.alloc(hp.PAT_SIZE)
        m.write(cur, self.pats[0])
        m.write(nxt, self.pats[1])
        m.write(hp.PLAYLIST, bytes([1, 2, 0]))
        m.w8(hp.PROJECT + 0x6C1, 0)
        m.w8(hp.CURRENT, 1)
        m.w8(st.SEQ + 0x1C, 1)                  # song mode
        m.w32(st.SEQ + 0x14, cur)
        m.w32(st.SEQ + 0x18, nxt)
        m.w8(st.SETTINGS + 0x2C, 2)             # MIDI out: jack
        m.call(st.SEQ_PLAY_FROM, st.SEQ, 0)
        self.seq.clear()


def blocks_per_song() -> int:
    return round(2 * hp.STEPS * 60 / hp.TEMPO_MAX / 4 * 44100 / 128)


def sequencer_side(ptf: Path, workspace: Path) -> tuple[int, int, dict]:
    """The worst block's instructions inside the bl seq_advance, and its index."""
    s = Scenario(ptf, workspace)
    s.mods_on(masterfx=False)
    s.play()
    for _ in range(PASSES * blocks_per_song()):
        s.block()
    worst = max(s.seq)
    return worst, s.seq.index(worst), s.notes


def engine_side(ptf: Path, workspace: Path, at: int) -> tuple[int, str]:
    """The worst block's work outside the bl seq_advance, in WINDOW blocks around block `at`
    (the engine stubbed before them, then WARMUP blocks not counted)."""
    s = Scenario(ptf, workspace)
    fx = s.mods_on(masterfx=True)
    s.play()
    for _ in range(max(0, at - WINDOW // 2 - WARMUP)):
        s.block()
    s.engine = True
    for _ in range(WARMUP):
        s.block()
    worst = 0
    for _ in range(WINDOW):
        whole = s.block()
        worst = max(worst, whole - s.seq[-1])
    return worst, fx


def stock_figures(stock: Path, ws: Path, quick: bool) -> dict:
    """Stock firmware's figures, kept in STOCK_CACHE for the same stock file and test code
    (every job of the test runner needs them)."""
    here = Path(__file__).resolve()
    key = hashlib.sha256(b"".join(f.read_bytes() for f in (stock, here, here.parent.parent / "make_headroom_project.py",
                                                            *sorted(here.parent.glob("*.py"))))).hexdigest()
    try:
        cache = json.loads(STOCK_CACHE.read_text())
    except (OSError, ValueError):
        cache = {}
    got = cache.get(key, {})
    if "seq" not in got:
        worst, at, notes = sequencer_side(stock, ws)
        got.update(seq=worst, at=at, notes=notes)
    if not quick and "engine" not in got:
        got["engine"] = engine_side(stock, ws, got["at"])[0]
    try:                                    # parallel jobs: write whole, then move in place
        STOCK_CACHE.parent.mkdir(parents=True, exist_ok=True)
        part = STOCK_CACHE.with_name(f"{STOCK_CACHE.name}.{os.getpid()}")
        part.write_text(json.dumps({key: got}, indent=1))
        os.replace(part, STOCK_CACHE)
    except OSError:
        pass
    return got


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__)
        return 2
    ptf, stock = Path(args[0]), Path(args[1])
    quick = "--quick" in sys.argv[1:]
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        print(f"sequencer side: worst block over {PASSES} passes of the song, engine stubbed")
        sf = stock_figures(stock, ws, quick)
        s_worst, s_at, s_notes = sf["seq"], sf["at"], sf["notes"]
        b_worst, b_at, b_notes = sequencer_side(ptf, ws)
        check(s_notes["sample"] > 1000 and s_notes["synth"] > 1000 and b_notes["sample"] > 1000
              and b_notes["synth"] > 1000, f"the scenario plays (stock {s_notes}, bundle {b_notes})")
        extra = b_worst - s_worst
        check(extra <= SEQ_LIMIT, f"at most {SEQ_LIMIT} more than stock: {extra} ({share(extra)}; "
              f"bundle {b_worst} at block {b_at}, stock {s_worst} at block {s_at})")
        if not quick:
            print(f"whole block: the engine side over {WINDOW} blocks around stock's worst sequencer block, "
                  f"Synth 1-3 sounding")
            s_engine = sf["engine"]
            b_engine, fx = engine_side(ptf, ws, s_at)
            engine_extra = b_engine - s_engine
            total = extra + engine_extra
            check(total <= BLOCK_LIMIT, f"at most {BLOCK_LIMIT} more than stock{fx}: {total} ({share(total)}; "
                  f"sequencer side {extra}, engine side {engine_extra}: bundle {b_engine}, stock {s_engine})")
    print(f"{len(failures)} failure(s)" + (" (--quick: whole block skipped)" if quick else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
