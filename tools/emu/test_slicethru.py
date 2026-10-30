"""Check the slicethru patch: Beat Slice notes that play from their slice to the sample end.

  python tools/emu/test_slicethru.py <patched.ptf> <stock TrackerPlus_1.2.0.ptf>

Works with the slicethru-only build and with bundles that include it. The voice checks
call the firmware's play-mode dispatcher (0x60032abc) and the sample voice's render
(0x600327e4) directly, on a voice object the test builds, in the patched and the stock
firmware side by side. The screen checks run the Sample Playback screen (its create and
start methods, the Play Mode list's draw and the input callbacks) on the real synth
system's app objects (tools/emu/synth_rig.py).

1. Sites: the dispatcher's Beat Slice call (0x60032b3a) and the Beat Slice note-on's end
   point (0x60032c62) branch into the patch; its version text.
2. Flag off: for every play mode, notes 0..127 and several instruments, the voice object
   after a note-on is byte-identical to stock. The same with the patch's RAM full of
   garbage (it initializes itself), and with other instruments flagged.
3. Flag on: each note starts where stock starts it and ends at the sample end; the
   frames to play, the sample pointer and the reverse start follow. 1, 4 and 48 slices,
   no slices, notes below 24 and past the last slice.
4. Render: a play-through voice plays to the sample end, and every block costs what
   stock's does.
5. The Slice FX (S), reverse, tune and finetune: as stock, with the end at the sample end.
6. Stereo: the right voice (+0x118 set) copies the left one's extended end.
6b. Loop (the render's end, 0x60035522, through the mixer's render 0x600357b4): with
    Thru and Loop on, notes from slices 1, 2 and 4 play to the end and then the sample
    from frame 0, sample-exact for three passes, also at pitch 2 and 3; stereo voices in
    step. No loop with Thru or Loop off, the flag on another instrument, a 1-Shot
    instrument, a note started before the flags were set, or reverse; Loop off during a
    pass stops at that pass's end. OFF, CUT and FADE (the real voice note-off, amp
    envelope on and off) end a looping note; a pad preview loops while the pad is held
    and stops when it is released. A block that doesn't reach the end costs
    what stock's does, and the end's code runs only at the end.
7. Re-entry: a note-on made while another is in progress (as when the audio interrupt
   cuts into a pad preview) uses its own flag and leaves the other's.
8. The patch writes nothing outside its RAM and the stack; cost of a note-on.
9. The Play Mode list: 9 names, the highlight on the instrument's row, no scroll bar.
10. The inputs (encoder, +1, -1, buttons 7 and 8) from every row: the row, mode and flag
    move by the step; steps that don't touch Beat Slice Thru leave the instrument as
    stock's same step does; Beat Slice <-> Thru changes only the flag.
11. Off the list (another control focused, a pop-up open): the inputs as stock.
11b. Loop's button: page 2 of a Beat Slice Thru instrument reads Loop Off/On, a press
    toggles it and relabels. Beat Slice, Slice, page 1 and a pop-up: as stock, no
    toggle. Beat Slice Thru -> Beat Slice clears Loop and goes back to page 1.
12. From the list to the voice: Beat Slice Thru chosen with a button plays to the end.
13. The settings file (slicethru.bin, the fake SD card of tools/emu/fakefs.py), through
    the Sample Playback and Pattern screens' update hooks: written once (BTS2), a
    moment after a change; Loop saved and restored, a BTS1 file loads with Loop off, a
    Loop bit without Thru ignored; read on a project change and the list redrawn; a damaged or short
    file, or bits above instrument 48: none; nothing read while the file manager is
    busy, and the old project's flags cleared at once; stale flags (an instrument no
    longer an active Beat Slice instrument) cleared only while it is idle; a new project
    as the firmware makes it ("New Project", no path: nothing written); Save As through
    the firmware's save request 0x60114e34, as a new project's first save and from a
    saved project: the flags kept and written to the new folder, the old file left; a
    plain save; a project opened afterwards and the name cleared while Workspace loads:
    the project's own file; stock firmware plays the instrument as Beat Slice.
"""
from __future__ import annotations

import json
import random
import struct
import sys
from pathlib import Path

from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_PC

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from patch_ptf import bl_target  # noqa: E402
from layout import patch_code, patch_ram  # noqa: E402
import seq_timing as st  # noqa: E402
import synth_rig as sr  # noqa: E402
from machine import FAKE_DISPLAY_VT  # noqa: E402
from fakefs import PROJECT_NAME, FakeFS  # noqa: E402
import project_save  # noqa: E402

INS = 0x20200000                    # 48 instruments, 0x174 bytes each
INS_SIZE = 0x174
SAMPLE_TABLE = INS + 0x4DC4         # per instrument: left and right sample addresses
DISPATCH = 0x60032ABC               # (voice, instrument, note): note-on setup by play mode
BS_SITE, END_SITE = 0x60032B3A, 0x60032C62
BS_NOTE_ON, TO_FRAMES = 0x60032B50, 0x60100694
UPDATE = 0x600327E4                 # the sample voice's render (one 128-frame block)
AUDIO_MEM_INIT = 0x600FF704         # (pool, blocks)
SPRINTF, FMT_LITERAL = 0x60167194, 0x600CBF9C
STACK = (0x20050000, 0x20058000)
# the Sample Playback screen (FINDINGS section 27)
SCR = 0x200254B4
SCR_CREATE, SCR_START, SCR_SHOW_MODE = 0x600F8390, 0x600F57F0, 0x600F7C54
SCR_POPUP, SCR_FOCUS, SCR_LIST_ID, SCR_LIST_DATA = 0x70, 0xD6, 0xCC, 0xB58
FOCUS_PLAY_MODE = 6
CONTROLS = 0x20027D64
LIST_DATA_SITE, BUTTON_PLUS_SITE, BUTTON_MINUS_SITE = 0x600F7CAC, 0x600F5E80, 0x600F5E8A
ENCODER_WORD, PLUS_WORD, MINUS_WORD = 0x600F5178, 0x600F5144, 0x600F5140
BUTTONS = 0x600F5E1C                # (button): 6 = the 7th (-1), 7 = the 8th (+1)
PAD_LIGHTS, APP_GET = 0x600F5508, 0x600AA860
SCR_INSTRUMENT = 0xF4
CUR_INS = INS + 0x46C5
PLAYBACK_UPDATE_WORD, PATTERN_UPDATE_WORD, PATTERN_UPDATE = 0x60042698, 0x600403DC, 0x600E1F98
FM_STATE = 0x2001E488 + 0x13D
SAVE_NAME_SITE = 0x60114EAA         # bl strncpy(fm + 0x340, name, 0x21) in it, Save As only
WRAP_SITE = 0x60035522              # the forward 1-shot render's end (two instructions, not a BL)
RENDER, BLOCK_FREE = 0x600357B4, 0x60030524     # (voice) -> a 128-frame block (samples at +4); (block)
VOICE_NOTE_ON, NOTE_OFF, PLAYER = 0x60030D50, 0x5678, 0x2001A138   # NOTE_OFF: ITCM (player, mode)
LOOP_MASK = 32                      # the Loop mask's offset in the patch's RAM
PAD_PRESS, PAD_RELEASE = 0x600C73C8, 0x600AC700  # (seq, track, note, velocity, pad): a pad's note on / off
SEQ, PREVIEW_TRACK = 0x20017274, 0x14
PAGE2_LABEL_SITE = 0x600F820A       # bl disp_ctrl_set_text(4th button, "") on page 2
BUTTON4_WORD = 0x600F5158           # the 4th button's callback word (0x600f4b55)
SCR_PAGE = 0xF0                     # nonzero: page 2 of the slice buttons
FILE_MAGIC = 0x32535442             # "BTS2": Thru and Loop masks
FILE_MAGIC_V1 = 0x31535442          # "BTS1" (bt4 and older): the Thru mask
SAVE_DELAY = 25
STOCK_NAMES = ["1-Shot", "Forward Loop", "Backward Loop", "Pingpong Loop", "Slice", "Beat Slice",
               "Wavetable", "Granular"]
NAMES = STOCK_NAMES[:6] + ["Beat Slice Thru"] + STOCK_NAMES[6:]
BT_MAGIC = 0x3542544D                # "MTB5"
VOICE_SIZE = 0x140
BEAT_SLICE = 5
FRAMES = 44100
QUARTERS = (0, 0x4000, 0x8000, 0xC000)
# voice fields the note-on sets (FINDINGS section 27)
V_START, V_END, V_FRAMES, V_PTR, V_LEFT, V_REV = 0x34, 0x38, 0x50, 0x18, 0x1C, 0x7C
V_PLAYING, V_ADDR, V_CHANNEL, V_LEN = 0x20, 0x24, 0x28, 0x68
V_PITCH, V_SLICE_FX, V_REVERSE, V_FX_SLICE, V_PARTNER = 0x74, 0xAE, 0xB0, 0xC4, 0x118
V_TUNE, V_FINETUNE = 0x90, 0x91

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


class Rig:
    """One firmware (patched or stock): instruments, sample buffers and voices."""

    def __init__(self, ptf: Path, patched: bool):
        self.m = m = st.make(ptf)
        self.patched = patched
        self.ram = patch_ram(ptf, "slicethru") if patched else None
        self.code = patch_code(ptf, "slicethru") if patched else None
        self.all_code = [tuple(int(x, 16) for x in q["code"]) for q in json.loads(
            ptf.with_suffix(".map.json").read_text())["patches"]] if patched else []
        self.samples: dict[tuple[int, int], int] = {}
        m.call(AUDIO_MEM_INIT, 0x20004378, 80)

    def instrument(self, i: int, mode: int = BEAT_SLICE, frames: int = FRAMES, slices=QUARTERS,
                   tune: int = 0, finetune: int = 0) -> None:
        m = self.m
        a = INS + INS_SIZE * i
        m.write(a, bytes(INS_SIZE))
        m.w8(a, 1)
        m.w32(a + 0x2C, frames)
        m.w8(a + 0x3C, mode)
        m.w16(a + 0x40, 1)
        m.w16(a + 0x42, 0xFFFE)
        m.w16(a + 0x44, 0xFFFF)
        m.w8(a + 0xFE, tune & 0xFF)
        m.w8(a + 0xFF, finetune & 0xFF)
        m.w8(a + 0x100, 50)
        for k, s in enumerate(slices):
            m.w16(a + 0x108 + 2 * k, s)
        m.w8(a + 0x168, len(slices))
        key = (i, frames)
        if key not in self.samples:
            buf = m.alloc(frames * 2 + 64)
            m.write(buf, b"".join(struct.pack("<h", (k % 2000) + 1) for k in range(frames)))
            self.samples[key] = buf
        m.w32(SAMPLE_TABLE + 8 * i, self.samples[key])
        m.w32(SAMPLE_TABLE + 8 * i + 4, self.samples[key])

    def voice(self, channel: int = 0, partner: int = 0) -> int:
        v = self.m.alloc(VOICE_SIZE)
        self.m.w8(v + V_CHANNEL, channel)
        self.m.w32(v + V_PARTNER, partner)
        return v

    # -- the patch's RAM ----------------------------------------------------------------
    def flags(self, *instruments: int) -> None:
        if not self.patched:
            return
        lo = hi = 0
        for i in instruments:
            if i < 32:
                lo |= 1 << i
            else:
                hi |= 1 << (i - 32)
        m = self.m
        m.w32(self.ram, BT_MAGIC)
        m.w32(self.ram + 4, lo)
        m.w32(self.ram + 8, hi)
        m.w8(self.ram + 12, 0)

    def loop(self, *instruments: int) -> None:
        mask = sum(1 << i for i in instruments)
        self.m.w32(self.ram + LOOP_MASK, mask & 0xFFFFFFFF)
        self.m.w32(self.ram + LOOP_MASK + 4, mask >> 32)

    def cur(self) -> int:
        return self.m.uc.mem_read(self.ram + 12, 1)[0]

    def note_on(self, v: int, i: int, note: int) -> int:
        return self.m.call(DISPATCH, v, i, note)

    def fields(self, v: int) -> dict[str, int]:
        r = self.m.r32
        return {"start": r(v + V_START), "end": r(v + V_END), "frames": r(v + V_FRAMES),
                "ptr": r(v + V_PTR) - r(v + V_ADDR), "left": r(v + V_LEFT), "rev": r(v + V_REV),
                "playing": self.m.uc.mem_read(v + V_PLAYING, 1)[0]}

    def blob(self, v: int) -> bytes:
        """The voice object, with its two sample pointers made relative to the sample
        (the two firmwares' buffers are at different scratch addresses)."""
        b = bytearray(self.m.uc.mem_read(v, VOICE_SIZE))
        addr = self.m.r32(v + V_ADDR)
        if addr:
            struct.pack_into("<I", b, V_PTR, (self.m.r32(v + V_PTR) - addr) & 0xFFFFFFFF)
            struct.pack_into("<I", b, V_ADDR, 0)
        return bytes(b)


def frames_of(point: int, frames: int = FRAMES) -> int:
    return int(frames / 65535.0 * point)       # as 0x60100694 (float32, truncated); exact for these


# ---- 1. sites and version -----------------------------------------------------------

def site_checks(p: Rig) -> None:
    print("sites and version")
    img = p.m.image
    lo, hi = p.code
    for at, name in ((BS_SITE, "the dispatcher's Beat Slice call"), (END_SITE, "the Beat Slice end point"),
                     (LIST_DATA_SITE, "the Play Mode list's data"), (BUTTON_PLUS_SITE, "the 8th button's step"),
                     (BUTTON_MINUS_SITE, "the 7th button's step"),
                     (WRAP_SITE, "the forward 1-shot render's end"),
                     (PAGE2_LABEL_SITE, "the 4th button's label on page 2")):
        t = bl_target(img, 0x60030000, at)
        check(lo <= t < hi, f"{name} ({at:#x}) branches into the patch ({t:#x})")
    # shared with the patches that keep a settings file (chained): the last one's hook;
    # the Save As checks below show the chain reaches this patch
    t = bl_target(img, 0x60030000, SAVE_NAME_SITE)
    check(any(a <= t < b for a, b in p.all_code),
          f"Save As's name copy ({SAVE_NAME_SITE:#x}) branches into a patch ({t:#x})")
    for at, name in ((ENCODER_WORD, "encoder"), (PLUS_WORD, "+1"), (MINUS_WORD, "-1"),
                     (BUTTON4_WORD, "4th button")):
        w = p.m.r32(at)
        check(bool(w & 1) and lo <= w - 1 < hi, f"the {name} callback word ({at:#x}) points into the patch ({w:#x})")
    m = p.m
    buf = m.alloc(64)
    m.call(SPRINTF, buf, m.r32(FMT_LITERAL), 1, 2, 0)
    text = bytes(m.uc.mem_read(buf, 64)).split(b"\0")[0].decode()
    check(text.startswith("1.2.0-") and len(text) <= 9, f"version text {text!r}")


# ---- 2. flag off --------------------------------------------------------------------

def off_checks(p: Rig, s: Rig) -> None:
    print("flag off: the voice as stock")
    notes = list(range(0, 128, 3)) + [24, 25, 26, 27, 71, 127]
    for setup in ("garbage RAM", "zero flags", "other instruments flagged"):
        bad = []
        for mode in range(8):
            for i in (0, 7, 47):
                for r in (p, s):
                    r.instrument(i, mode=mode)
                if setup == "garbage RAM":
                    p.m.write(p.ram, bytes([0xA5]) * 16)
                elif setup == "zero flags":
                    p.flags()
                else:
                    p.flags(*[k for k in range(48) if k != i])
                for note in notes:
                    vp, vs = p.voice(), s.voice()
                    rp, rs = p.note_on(vp, i, note), s.note_on(vs, i, note)
                    if rp != rs or p.blob(vp) != s.blob(vs):
                        bad.append((mode, i, note))
        check(not bad, f"{setup}: modes 0-7, instruments 0, 7, 47, {len(notes)} notes each: "
                       f"every voice byte-identical to stock{'' if not bad else f' (differ: {bad[:4]})'}")
    check(p.m.r32(p.ram) == BT_MAGIC and p.cur() == 0, "the patch initialized its RAM, cur is 0 after the calls")


# ---- 3. flag on ---------------------------------------------------------------------

def on_checks(p: Rig, s: Rig) -> None:
    print("flag on: from the slice to the sample end")
    for slices, label in ((QUARTERS, "4 slices"), ((0,), "1 slice"),
                          (tuple(k * 1365 for k in range(48)), "48 slices"), ((), "no slices")):
        for r in (p, s):
            r.instrument(3, slices=slices)
        p.flags(3)
        bad = []
        n = max(len(slices), 1)
        for note in [0, 12, 23] + list(range(24, 24 + n + 2)) + [100, 127]:
            vp, vs = p.voice(), s.voice()
            p.note_on(vp, 3, note)
            s.note_on(vs, 3, note)
            a, b = p.fields(vp), s.fields(vs)
            want = dict(b, end=FRAMES, frames=FRAMES - b["start"], rev=0)
            if a != want:
                bad.append((note, a, want))
        check(not bad, f"{label}: every note starts where stock does, ends at frame {FRAMES}, "
                       f"frames to play = {FRAMES} - start{'' if not bad else f' (first: {bad[0]})'}")
    for r in (p, s):
        r.instrument(3, slices=QUARTERS)
    p.flags(3)
    vp = p.voice()
    p.note_on(vp, 3, 25)
    f = p.fields(vp)
    check((f["start"], f["end"], f["frames"]) == (frames_of(0x4000), FRAMES, FRAMES - frames_of(0x4000)),
          f"note 25 (slice 2 of 4): frames {f['start']}..{f['end']} ({f['frames']} to play)")


# ---- 4. render ----------------------------------------------------------------------

def render(r: Rig, v: int, limit: int = 1000) -> tuple[int, int]:
    """Render blocks until the voice stops: (blocks, instructions per block)."""
    m = r.m
    m.write(v + V_PITCH, struct.pack("<f", 1.0))     # pitch ratio 1 (the rig builds no note tables)
    count = {"n": 0}

    def counter(uc, a, sz, u):
        count["n"] += 1
    h = m.uc.hook_add(UC_HOOK_CODE, counter)
    n = 0
    while m.uc.mem_read(v + V_PLAYING, 1)[0] and n < limit:
        m.call(UPDATE, v)
        n += 1
    m.uc.hook_del(h)
    return n, count["n"] // max(n, 1)


def render_checks(p: Rig, s: Rig) -> None:
    print("render to the end")
    for r in (p, s):
        r.instrument(3, slices=QUARTERS)
    p.flags(3)
    for note in (24, 25, 27):
        vp, vs = p.voice(), s.voice()
        p.note_on(vp, 3, note)
        s.note_on(vs, 3, note)
        want = p.fields(vp)["frames"]
        n, cost = render(p, vp)
        ns, cost_s = render(s, vs)
        check(n == -(-want // 128) and ns == -(-s.fields(vs)["frames"] // 128),
              f"note {note}: plays {n} blocks for {want} frames (stock {ns} blocks, its slice)")
        vq, vt = p.voice(), s.voice()                    # cost: the same number of blocks, both playing
        p.note_on(vq, 3, note)
        s.note_on(vt, 3, 24 if note != 27 else 27)
        p.m.write(vq + V_PITCH, struct.pack("<f", 1.0))
        s.m.write(vt + V_PITCH, struct.pack("<f", 1.0))
        cp = cs = 0
        for _ in range(10):
            cp += steps(p, vq)
            cs += steps(s, vt)
        check(cp == cs, f"note {note}: {cp // 10} instructions per block, stock {cs // 10}")


def steps(r: Rig, v: int) -> int:
    count = {"n": 0}

    def counter(uc, a, sz, u):
        count["n"] += 1
    h = r.m.uc.hook_add(UC_HOOK_CODE, counter)
    r.m.call(UPDATE, v)
    r.m.uc.hook_del(h)
    return count["n"]


# ---- 5. S FX, reverse, tune ---------------------------------------------------------

def fx_checks(p: Rig, s: Rig) -> None:
    print("the Slice FX, reverse, tune and finetune")
    for r in (p, s):
        r.instrument(3, slices=QUARTERS, tune=-7, finetune=20)
    p.flags(3)
    for label, setup in (("S FX slice 3", {V_SLICE_FX: 1, V_FX_SLICE: 2}), ("reverse", {V_REVERSE: 1}),
                         ("S FX slice 4 and reverse", {V_SLICE_FX: 1, V_FX_SLICE: 3, V_REVERSE: 1})):
        vp, vs = p.voice(), s.voice()
        for off, val in setup.items():
            p.m.w8(vp + off, val)
            s.m.w8(vs + off, val)
        p.note_on(vp, 3, 24)
        s.note_on(vs, 3, 24)
        a, b = p.fields(vp), s.fields(vs)
        rev = FRAMES - b["start"] - 1 if V_REVERSE in setup else 0
        want = dict(b, end=FRAMES, frames=FRAMES - b["start"], rev=rev)
        same = (p.m.uc.mem_read(vp + V_TUNE, 2) == s.m.uc.mem_read(vs + V_TUNE, 2)
                and p.m.r32(vp + 0x23) & 0xFF == s.m.r32(vs + 0x23) & 0xFF)
        check(a == want and same, f"{label}: start {a['start']} as stock, end {a['end']}, reverse start "
                                  f"{a['rev']}; tune, finetune and the fixed pitch as stock")


# ---- 6. stereo ----------------------------------------------------------------------

def stereo_checks(p: Rig, s: Rig) -> None:
    print("stereo: the right voice copies the left one")
    for r in (p, s):
        r.instrument(3, slices=QUARTERS)
    p.flags(3)
    left = p.voice(0)
    right = p.voice(1, partner=left)
    p.note_on(left, 3, 26)
    p.note_on(right, 3, 26)
    a, b = p.fields(left), p.fields(right)
    check(a["end"] == b["end"] == FRAMES and a["start"] == b["start"] == frames_of(0x8000),
          f"both voices: frames {b['start']}..{b['end']}")


# ---- 6b. Loop: a Beat Slice Thru note loops the whole sample -------------------------

LOOP_FRAMES = 10000                 # a short sample, each frame's value = its frame + 1


def loop_rig(ptf: Path, patched: bool) -> Rig:
    r = Rig(ptf, patched)
    r.instrument(3, frames=LOOP_FRAMES, slices=QUARTERS)
    r.m.write(r.samples[(3, LOOP_FRAMES)], b"".join(struct.pack("<h", k + 1) for k in range(LOOP_FRAMES)))
    return r


def output(r: Rig, v: int, blocks: int, pitch: float = 1.0) -> list[int]:
    """The voice's next `blocks` blocks of samples, from the render the mixer calls."""
    r.m.write(v + V_PITCH, struct.pack("<f", pitch))
    out = []
    for _ in range(blocks):
        blk = r.m.call(RENDER, v)
        if not blk:
            out += [0] * 128
            continue
        out += list(struct.unpack("<128h", bytes(r.m.uc.mem_read(blk + 4, 256))))
        r.m.call(BLOCK_FREE, blk)
    return out


def ramp(start: int, n: int, step: int = 1) -> list[int]:
    """The sample from frame `start` on, `step` frames at a time, wrapping to frame 0."""
    out, pos = [], start
    while len(out) < n:
        out.append(pos + 1)
        pos += step
        if pos >= LOOP_FRAMES:
            pos -= LOOP_FRAMES
    return out


def playing(r: Rig, v: int) -> int:
    return r.m.uc.mem_read(v + V_PLAYING, 1)[0]


def looping_rig(ptf: Path) -> Rig:
    r = loop_rig(ptf, True)
    r.flags(3)
    r.loop(3)
    return r


def loop_checks(ptf: Path, stock: Path) -> None:
    print("Loop: a Beat Slice Thru note loops the whole sample")
    for note in (24, 25, 27):
        p = looping_rig(ptf)
        v = p.voice()
        p.note_on(v, 3, note)
        start = p.fields(v)["start"]
        n = LOOP_FRAMES - start + 3 * LOOP_FRAMES
        got = output(p, v, -(-n // 128))[:n]
        check(got == ramp(start, n) and playing(p, v),
              f"note {note} (from frame {start}): to the end, then the sample from frame 0 three times, "
              f"sample-exact, still playing")
    for note, ratio in ((25, 2.0), (25, 3.0), (27, 2.0)):
        p = looping_rig(ptf)
        v = p.voice()
        p.note_on(v, 3, note)
        start = p.fields(v)["start"]
        got = output(p, v, 300, ratio)
        check(got == ramp(start, len(got), int(ratio)),
              f"note {note} at pitch {ratio}: sample-exact over {len(got)} samples, the wraps included")
    p = looping_rig(ptf)
    left = p.voice(0)
    right = p.voice(1, partner=left)
    p.note_on(left, 3, 25)
    p.note_on(right, 3, 25)
    check(output(p, left, 250) == output(p, right, 250), "stereo: both voices loop in step over 250 blocks")

    print("Loop: when it doesn't loop")
    for label, setup in (
            ("Loop on, Beat Slice Thru off", lambda r: (r.flags(), r.loop(3))),
            ("Beat Slice Thru on, Loop off", lambda r: (r.flags(3), r.loop())),
            ("Loop on another instrument", lambda r: (r.flags(3), r.loop(4))),
            ("both flags on a 1-Shot instrument",
             lambda r: (r.m.w8(INS + INS_SIZE * 3 + 0x3C, 0), r.flags(3), r.loop(3)))):
        p = loop_rig(ptf, True)
        setup(p)
        v = p.voice()
        p.note_on(v, 3, 25)
        f = p.fields(v)
        got = output(p, v, -(-(f["frames"] + 256) // 128))
        check(got[:f["frames"]] == ramp(f["start"], f["frames"]) and not any(got[f["frames"]:])
              and not playing(p, v), f"{label}: plays frames {f['start']}..{f['end']} once and stops")
    p = loop_rig(ptf, True)
    p.flags()
    v = p.voice()
    p.note_on(v, 3, 25)                         # a plain Beat Slice note: slice 2 only
    p.flags(3)
    p.loop(3)                                   # both chosen while it plays
    got = output(p, v, 40)
    check(got[:2500] == ramp(2500, 2500) and not any(got[2500:]) and not playing(p, v),
          "a Beat Slice note started before Thru and Loop were chosen: stops at its slice end")
    outs = []
    for looping in (False, True):
        p = loop_rig(ptf, True)
        p.flags(3)
        p.loop(*((3,) if looping else ()))
        v = p.voice()
        p.m.w8(v + V_REVERSE, 1)
        p.note_on(v, 3, 25)
        outs.append((output(p, v, 70), playing(p, v)))
    (off, off_playing), (on, on_playing) = outs
    check(on == off and on[0] == LOOP_FRAMES and not any(on[LOOP_FRAMES - 2500:]) and not on_playing,
          "reverse with Loop on: plays once from the sample end towards the slice start and stops, "
          "exactly as with Loop off")
    p = looping_rig(ptf)
    v = p.voice()
    p.note_on(v, 3, 25)
    first = output(p, v, 70)                    # 8,960 frames: into the second pass
    p.loop()                                    # Loop off while it plays
    got = first + output(p, v, 90)
    n = 7500 + LOOP_FRAMES
    check(got[:n] == ramp(2500, n) and not any(got[n:]) and not playing(p, v),
          "Loop off during the second pass: that pass plays to the sample end, then the note stops")

    print("Loop: what ends a looping note")
    for env_off in (0, 1):
        for mode, name in ((0, "OFF"), (-3 & 0xFFFFFFFF, "CUT"), (-2 & 0xFFFFFFFF, "FADE")):
            p = looping_rig(ptf)
            p.m.stub_function(0x6010D530, lambda mm: 0)     # objects the rig doesn't build
            p.m.stub_function(0x6010D59C, lambda mm: 0)
            v = p.m.r32(p.m.r32(PLAYER + 0x73C))           # player 0's first voice
            p.m.call(VOICE_NOTE_ON, v, 3, 25)
            p.m.w8(v + 0xEE, env_off)
            output(p, v, 200)                              # 25,600 frames: looping
            was = playing(p, v)
            p.m.call(NOTE_OFF, PLAYER, mode, max_insns=5_000_000)
            stopped = None
            for k in range(400):
                output(p, v, 1)
                if not playing(p, v):
                    stopped = k
                    break
            check(was and stopped is not None,
                  f"{name}, amp envelope {'off' if env_off else 'on'}: still looping after 200 blocks, "
                  f"stopped {stopped} blocks after the {name}")

    print("Loop: a pad preview")
    p = looping_rig(ptf)
    for stub in (0x6010D530, 0x6010D544, 0x6010D59C):   # player+0x77c objects the rig doesn't build
        p.m.stub_function(stub, lambda mm: 0)
    p.m.w8(INS + 0x46C5, 3)                     # the instrument the pads play
    p.m.call(PAD_PRESS, SEQ, PREVIEW_TRACK, 25, 0xFFFFFFFF, 1, max_insns=20_000_000)
    pl = PLAYER
    pad_voices = [p.m.r32(a) for a in range(p.m.r32(pl + 0x73C), p.m.r32(pl + 0x740), 4)]
    pad_voices = [v for v in pad_voices if playing(p, v) and p.m.uc.mem_read(v + 0x6F, 1)[0] == 3]
    for v in pad_voices:
        output(p, v, 200)                       # 25,600 frames, past the 7,500 to the end
    held = bool(pad_voices) and all(playing(p, v) for v in pad_voices)
    p.m.call(PAD_RELEASE, SEQ, PREVIEW_TRACK, 25, 0, 1, max_insns=20_000_000)
    for v in pad_voices:
        output(p, v, 1)
    check(held and not any(playing(p, v) for v in pad_voices),
          f"a pad held (0x600c73c8, preview track {PREVIEW_TRACK}): {len(pad_voices)} voices still looping after "
          f"200 blocks; the pad released (0x600ac700): all stopped")

    print("Loop: cost")
    p, s = looping_rig(ptf), loop_rig(stock, False)
    vp, vs = p.voice(), s.voice()
    p.note_on(vp, 3, 24)
    s.note_on(vs, 3, 24)
    for r, v in ((p, vp), (s, vs)):
        r.m.write(v + V_PITCH, struct.pack("<f", 1.0))
    cp = sum(steps(p, vp) for _ in range(10))
    cs = sum(steps(s, vs) for _ in range(10))
    check(cp == cs, f"a block without the end: {cp // 10} instructions, as stock ({cs // 10})")
    hits = []
    h = p.m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, sz, u: hits.append(1), begin=WRAP_SITE, end=WRAP_SITE)
    output(p, vp, 60)                           # up to 8,960 of the 10,000 frames
    p.m.uc.hook_del(h)
    check(not hits, "the end's code runs only at the end (not in the 60 blocks before it)")


# ---- 7. re-entry --------------------------------------------------------------------

def reentry_checks(p: Rig, s: Rig) -> None:
    print("re-entry: a note-on inside another")
    for r in (p, s):
        r.instrument(3, slices=QUARTERS)
        r.instrument(4, slices=QUARTERS)
    p.flags(3)                          # 3 plays through, 4 doesn't
    m = p.m
    for outer, inner in ((1, 4), (0, 3)):
        m.w8(p.ram + 12, outer)         # cur as an outer note-on in progress leaves it
        v, vs = p.voice(), s.voice()
        p.note_on(v, inner, 25)
        s.note_on(vs, inner, 25)
        end = p.fields(v)["end"]
        want = FRAMES if inner == 3 else s.fields(vs)["end"]
        check(end == want and p.cur() == outer,
              f"cur {outer} in progress, instrument {inner}'s note-on: end {end} (its own flag), "
              f"cur back to {p.cur()}")
    m.w8(p.ram + 12, 0)


# ---- 8. writes and cost -------------------------------------------------------------

def write_checks(p: Rig, s: Rig) -> None:
    print("writes and cost")
    m = p.m
    lo, hi = p.code
    stray = []

    def on_write(uc, access, a, size, value, user):
        pc = uc.reg_read(UC_ARM_REG_PC)
        if lo <= pc < hi and not (p.ram <= a < p.ram + 16 or STACK[0] <= a < STACK[1]):
            stray.append((hex(pc), hex(a)))
    h = m.uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
    rnd = random.Random(7)
    for r in (p, s):
        r.instrument(3, slices=QUARTERS)
        r.instrument(9, mode=0)
    for k in range(300):
        p.flags(*rnd.sample(range(48), rnd.randint(0, 48)))
        p.note_on(p.voice(), rnd.choice((3, 9)), rnd.randint(0, 127))
    m.uc.hook_del(h)
    check(not stray, f"300 random note-ons: no write by the patch outside its RAM and the stack {stray[:3]}")
    p.flags(3)
    costs = []
    for r in (p, s):
        c = {"n": 0}

        def counter(uc, a, sz, u, c=c):
            c["n"] += 1
        hk = r.m.uc.hook_add(UC_HOOK_CODE, counter)
        r.note_on(r.voice(), 3, 25)
        r.m.uc.hook_del(hk)
        costs.append(c["n"])
    check(costs[0] - costs[1] < 60, f"a Beat Slice note-on: {costs[0]} instructions, stock {costs[1]} "
                                    f"(+{costs[0] - costs[1]})")


# ---- the Sample Playback screen -------------------------------------------------------

class ScreenRig:
    """The Sample Playback screen of one firmware, on the real synth system's app objects
    (tools/emu/synth_rig.py), with a Beat Slice instrument 0. Pad lights (0x600f5508) need
    a pad LED object the rig doesn't have and are skipped; the text list's marquee clock
    (0x600aa860 -> +0x14 -> +0x30) is a fake at time 0."""

    def __init__(self, ptf: Path, patched: bool):
        self.r = r = sr.SynthRig(ptf)
        self.m = m = r.m
        self.patched = patched
        self.ram = patch_ram(ptf, "slicethru") if patched else None
        m.w32(0x20027D18, r.fake("clock", 8))
        title = m.alloc(0x100)
        m.w8(title + 2, 0x7F)                   # no control: its disp_ctrl_* calls do nothing
        m.w32(title + 0x60, sr.S)
        m.w32(0x200209F0, title)
        m.w32(FAKE_DISPLAY_VT + 0x40, r.add_stub(self._dl_string))
        m.stub_function(PAD_LIGHTS, lambda mm: None)
        clock = r.fake("time", 4)
        app = r.fake("app", 8, ret={0x14: r.fake("app14", 16, ret={0x30: clock})})
        m.stub_function(APP_GET, lambda mm: app)
        self.buf = m.alloc(FRAMES * 2 + 64)
        self.instrument(0)
        m.call(SCR_CREATE, SCR, max_insns=50_000_000)
        m.call(SCR_START, SCR, max_insns=50_000_000)
        self.list = m.r32(CONTROLS + 4 * ((m.r32(SCR + SCR_LIST_ID) & 0xFF) + 4))

    @staticmethod
    def _dl_string(mm):                         # the display's string write (vtable +0x40)
        raw = mm.cstr(mm.arg(1)).encode("latin-1") + bytes(1)
        raw += bytes(-len(raw) % 4)
        for k in range(0, len(raw), 4):
            mm.display_list.append(struct.unpack_from("<I", raw, k)[0])
        return len(raw)

    def instrument(self, i: int, mode: int = BEAT_SLICE) -> None:
        m = self.m
        a = INS + INS_SIZE * i
        m.write(a, bytes(INS_SIZE))
        m.w8(a, 1)
        m.w32(a + 0x2C, FRAMES)
        m.w32(a + 0x28, self.buf)
        m.w8(a + 0x3C, mode)
        m.w16(a + 0x40, 1)
        m.w16(a + 0x42, 0xFFFE)
        m.w16(a + 0x44, 0xFFFF)
        m.w8(a + 0x100, 50)
        for k, q in enumerate(QUARTERS):
            m.w16(a + 0x108 + 2 * k, q)
        m.w8(a + 0x168, len(QUARTERS))
        m.w32(SAMPLE_TABLE + 8 * i, self.buf)
        m.w32(SAMPLE_TABLE + 8 * i + 4, self.buf)

    def set(self, mode: int, flag: bool, loop: bool = False) -> None:
        """Instrument 0's mode and flags, shown on the screen (as after a step)."""
        m = self.m
        m.w8(INS + 0x3C, mode)
        m.w8(INS + 4, 1 if mode == 6 else 0)
        if self.patched:
            m.w32(self.ram, BT_MAGIC)
            m.w32(self.ram + 4, 1 if flag else 0)
            m.w32(self.ram + 8, 0)
            m.w32(self.ram + LOOP_MASK, 1 if loop else 0)
            m.w32(self.ram + LOOP_MASK + 4, 0)
        m.w8(SCR + SCR_POPUP, 0)
        m.w8(SCR + SCR_FOCUS, FOCUS_PLAY_MODE)
        m.call(SCR_SHOW_MODE, SCR, max_insns=50_000_000)

    def label(self, k: int) -> tuple[str, str]:
        """Button k's (1-based) two lines: its control's text (+0x10) and second line (+0x38)."""
        m = self.m
        cid = m.uc.mem_read(SCR + 0xC4 + k - 1, 1)[0]
        ctl = m.r32(CONTROLS + 0x10 + 4 * cid)
        a, b = m.r32(ctl + 0x10), m.r32(ctl + 0x38)
        return (m.cstr(a) if a else "", m.cstr(b) if b else "")

    def press4(self) -> None:
        """The 4th button pressed and released (its callback word, r0 = 1, then 0)."""
        for state in (1, 0):
            self.m.call(self.m.r32(BUTTON4_WORD) & ~1, state, max_insns=50_000_000)

    def loop(self) -> int:
        return self.m.r32(self.ram + LOOP_MASK) | self.m.r32(self.ram + LOOP_MASK + 4) << 32

    def state(self) -> tuple[int, bool]:
        mode = self.m.uc.mem_read(INS + 0x3C, 1)[0]
        flag = bool(self.m.r32(self.ram + 4) & 1) if self.patched else False
        return mode, flag

    def ins(self) -> bytes:
        return bytes(self.m.uc.mem_read(INS, INS_SIZE))

    def draw(self) -> tuple[list[str], int, int, bool]:
        """The list's names, its highlighted row, item count and whether it drew a scroll bar."""
        m = self.m
        m.display_list.clear()
        m.call(m.r32(m.r32(self.list)), self.list, max_insns=5_000_000)
        dl = list(m.display_list)
        names = []
        for k, w in enumerate(dl):
            if w == 0xFFFFFF0C:
                raw = b"".join(struct.pack("<I", x) for x in dl[k + 3:k + 9])
                names.append(raw.split(b"\0")[0].decode("latin-1"))
        row, scroll = struct.unpack("<hh", bytes(m.uc.mem_read(self.list + 0x36, 4)))
        count, rows = struct.unpack("<HH", bytes(m.uc.mem_read(SCR + SCR_LIST_DATA + 2, 4)))
        return names, row + scroll, count, rows < count

    def step(self, how: str, n: int = 0) -> None:
        m = self.m
        if how == "encoder":
            m.call(m.r32(ENCODER_WORD) & ~1, n & 0xFFFFFFFF, max_insns=50_000_000)
        elif how == "+1":
            m.call(m.r32(PLUS_WORD) & ~1, 0, max_insns=50_000_000)
        elif how == "-1":
            m.call(m.r32(MINUS_WORD) & ~1, 0, max_insns=50_000_000)
        elif how == "8th button":
            m.call(BUTTONS, 7, max_insns=50_000_000)
        elif how == "7th button":
            m.call(BUTTONS, 6, max_insns=50_000_000)


def row_of(mode: int, flag: bool) -> int:
    if mode == BEAT_SLICE and flag:
        return 6
    return mode + 1 if mode > BEAT_SLICE else mode


def of_row(row: int) -> tuple[int, bool]:
    return (BEAT_SLICE, True) if row == 6 else (row - 1 if row > 6 else row, False)


def list_checks(q: ScreenRig, t: ScreenRig) -> None:
    print("the Play Mode list")
    names, row, count, bar = t.draw()
    check(names == STOCK_NAMES and row == 5 and count == 8, f"stock: 8 names, Beat Slice highlighted ({row})")
    bad = []
    for mode in range(8):
        for flag in (False, True):
            q.set(mode, flag)
            names, row, count, bar = q.draw()
            if names != NAMES or row != row_of(mode, flag) or count != 9 or bar:
                bad.append((mode, flag, row, count, bar, names))
    check(not bad, "every mode, flag off and on: 9 names in order, no scroll bar, the highlight on "
                   f"its row (Beat Slice Thru only for Beat Slice with the flag){'' if not bad else f' {bad[:2]}'}")


STEPS = [("encoder", n) for n in (1, -1, 2, -2, 3, -3, 4, -4, 9, -9)] + \
        [("+1", 1), ("-1", -1), ("8th button", 1), ("7th button", -1)]


def input_checks(q: ScreenRig, t: ScreenRig) -> None:
    print("the inputs")
    bad, stock_diff = [], []
    for row in range(9):
        for how, n in STEPS:
            mode, flag = of_row(row)
            q.set(mode, flag)
            q.step(how, n)
            s = max(-4, min(4, n))
            to = max(0, min(8, row + s))
            got = q.state()
            if got != of_row(to) or q.draw()[1] != to:
                bad.append((row, how, n, got, of_row(to)))
            if row != 6 and to != 6 and (row < 6) == (to < 6):
                t.set(mode, False)
                t.step(how, n)
                if q.ins() != t.ins():
                    stock_diff.append((row, how, n))
    check(not bad, f"from every row, {len(STEPS)} steps (encoder +-1..+-4 and +-9, +1, -1, buttons 7 and 8): "
                   f"the row, mode and flag move by the step, clamped to 0..8{'' if not bad else f' {bad[:3]}'}")
    check(not stock_diff, "steps that don't touch Beat Slice Thru: the instrument as stock after the same step"
                          f"{'' if not stock_diff else f' {stock_diff[:3]}'}")
    q.set(BEAT_SLICE, False)
    before = q.ins()
    q.step("encoder", 1)
    on = q.ins()
    q.step("encoder", -1)
    check(q.state() == (BEAT_SLICE, False) and before == on == q.ins(),
          "Beat Slice -> Thru -> Beat Slice: only the flag changes, the instrument stays byte-identical")
    q.set(6, False)                         # Wavetable
    q.step("7th button")
    check(q.state() == (BEAT_SLICE, True) and q.ins()[4] == 0,
          "from Wavetable one row up: Beat Slice Thru (mode 5, sample type 0, as stock's step to Beat Slice)")


def off_list_checks(q: ScreenRig, t: ScreenRig) -> None:
    print("the inputs off the list")
    bad = []
    for popup, focus in ((0, 0), (0, 1), (0, 2), (0, 3), (1, FOCUS_PLAY_MODE)):
        for how, n in (("encoder", 1), ("encoder", -2), ("+1", 1), ("-1", -1)):
            for r in (q, t):
                r.instrument(0)             # the same instrument in both, whatever ran before
                r.set(BEAT_SLICE, False)
                r.m.w8(SCR + SCR_POPUP, popup)
                r.m.w8(SCR + SCR_FOCUS, focus)
                try:
                    r.step(how, n)
                except RuntimeError as e:
                    bad.append((popup, focus, how, "faulted", str(e)[:60]))
            if q.ins() != t.ins() or q.state()[0] != t.state()[0]:
                bad.append((popup, focus, how, n))
    check(not bad, "another control focused, or a pop-up open: the encoder, +1 and -1 do what stock does "
                   f"(the same instrument afterwards){'' if not bad else f' {bad[:3]}'}")


def ui_play_checks(q: ScreenRig) -> None:
    print("from the list to the voice")
    q.set(BEAT_SLICE, False)
    q.step("8th button")
    m = q.m
    m.call(AUDIO_MEM_INIT, 0x20004378, 80)
    v = m.alloc(VOICE_SIZE)
    m.call(DISPATCH, v, 0, 25)
    end = m.r32(v + V_END)
    q.step("7th button")
    w = m.alloc(VOICE_SIZE)
    m.call(DISPATCH, w, 0, 25)
    check(end == FRAMES and m.r32(w + V_END) == frames_of(0x8000),
          f"Beat Slice Thru chosen with the 8th button: note 25 ends at {end}; back to Beat Slice: "
          f"{m.r32(w + V_END)}")


def loop_ui_checks(q: ScreenRig, t: ScreenRig) -> None:
    print("Loop: the 4th button on page 2")
    m = q.m

    def page2(r: ScreenRig, mode: int, flag: bool = False, loop: bool = False) -> None:
        r.set(mode, flag and r.patched, loop and r.patched)
        r.m.w8(SCR + SCR_PAGE, 1)
        r.m.call(SCR_SHOW_MODE, SCR, max_insns=50_000_000)

    def snapshot(r: ScreenRig) -> tuple[bytes, bytes]:
        return bytes(r.m.uc.mem_read(SCR, 0xC00)), r.ins()

    page2(q, BEAT_SLICE, True)
    check(q.label(4) == ("Loop", "Off"), f"Beat Slice Thru, page 2: the 4th button reads {q.label(4)}")
    q.press4()
    page = m.uc.mem_read(SCR + SCR_PAGE, 1)[0]
    check(q.loop() == 1 and q.label(4) == ("Loop", "On") and page and q.state() == (BEAT_SLICE, True),
          f"a press: Loop on, the button reads {q.label(4)}, still page 2, still Beat Slice Thru")
    q.press4()
    check(q.loop() == 0 and q.label(4) == ("Loop", "Off"), f"another press: Loop off, {q.label(4)}")
    for label, mode in (("Beat Slice", BEAT_SLICE), ("Slice", 4)):
        page2(q, mode)
        page2(t, mode)
        same_label = q.label(4) == t.label(4) == ("", "")
        before = snapshot(q), snapshot(t)
        q.press4()
        t.press4()
        after = snapshot(q), snapshot(t)
        changed = [[k for k in range(len(b[0])) if b[0][k] != a[0][k]] for b, a in zip(before, after)]
        check(same_label and changed[0] == changed[1] and after[0][1] == before[0][1] and q.loop() == 0,
              f"{label}, page 2: the 4th button blank as stock, a press does what stock's does "
              f"(screen bytes changed {changed[0]}), no Loop")
    page2(q, BEAT_SLICE, True)
    m.w8(SCR + SCR_POPUP, 1)
    q.press4()
    check(q.loop() == 0, "a pop-up open: a press doesn't toggle Loop")
    m.w8(SCR + SCR_POPUP, 0)
    for r in (q, t):                            # page 1 with Loop on: the stock 4th button
        r.m.w8(SCR + SCR_PAGE, 0)
    q.set(BEAT_SLICE, True, True)
    t.set(BEAT_SLICE, False)
    check(q.label(4) == t.label(4), f"page 1: the 4th button as stock {q.label(4)}")
    before = snapshot(q), snapshot(t)
    q.press4()
    t.press4()
    after = snapshot(q), snapshot(t)
    changed = [[k for k in range(len(b[0])) if b[0][k] != a[0][k]] for b, a in zip(before, after)]
    check(changed[0] == changed[1] and q.loop() == 1,
          f"page 1: a press does what stock's does (screen bytes changed {changed[0]}), Loop unchanged")
    page2(q, BEAT_SLICE, True, True)
    q.m.w8(SCR + SCR_FOCUS, FOCUS_PLAY_MODE)
    q.step("7th button")                        # Beat Slice Thru -> Beat Slice, on page 2
    page = m.uc.mem_read(SCR + SCR_PAGE, 1)[0]
    check(q.state() == (BEAT_SLICE, False) and q.loop() == 0 and not page and q.label(4)[1] == "",
          f"Beat Slice Thru -> Beat Slice on page 2: Loop cleared with Thru, back to page 1, "
          f"the 4th button's second line {q.label(4)[1]!r}")
    page2(q, BEAT_SLICE, True, True)
    for r in (q, t):
        r.m.w8(SCR + SCR_PAGE, 0)
        r.m.call(SCR_SHOW_MODE, SCR, max_insns=50_000_000)
    check(q.label(4) == t.label(4), f"from page 2 (Loop On) back to page 1: the 4th button as stock {q.label(4)}")


# ---- the settings file -----------------------------------------------------------------

def fnv1a(data: bytes) -> int:
    h = 2166136261
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def bts1(mask: int) -> bytes:
    body = struct.pack("<Q", mask)
    return struct.pack("<I", FILE_MAGIC_V1) + body + struct.pack("<I", fnv1a(body))


def bts2(thru: int, loop: int) -> bytes:
    body = struct.pack("<QQ", thru, loop)
    return struct.pack("<I", FILE_MAGIC) + body + struct.pack("<I", fnv1a(body))


def file_checks(ptf: Path, stock: Path) -> None:
    print("the settings file (slicethru.bin), through the screens' update hooks")
    q = ScreenRig(ptf, True)
    m = q.m
    fs = FakeFS(m, Path("."))
    m.stub_function(PATTERN_UPDATE, lambda mm: 0)       # the Pattern screen's own update: not needed here
    play_hook, pat_hook = m.r32(PLAYBACK_UPDATE_WORD) & ~1, m.r32(PATTERN_UPDATE_WORD) & ~1
    for i in (1, 2, 40):
        q.instrument(i)                     # more Beat Slice instruments

    def tick(n=1, hook=None):
        for _ in range(n):
            m.call(hook or play_hook, SCR, max_insns=50_000_000)

    def mask() -> int:
        return m.r32(q.ram + 4) | m.r32(q.ram + 8) << 32

    def path(name):
        return f"Projects/User/{name}/slicethru.bin"

    def writes():
        return len([x for x in fs.log if "slicethru.bin mode 3" in x])

    def on_screen(i: int = 0) -> None:      # instrument i on the Sample Playback screen
        m.w8(CUR_INS, i)
        m.w32(SCR + SCR_INSTRUMENT, INS + INS_SIZE * i)
        m.call(SCR_SHOW_MODE, SCR, max_insns=50_000_000)

    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(mask() == 0, "a project without slicethru.bin: no Beat Slice Thru instruments")
    tick(SAVE_DELAY + 5)
    check(writes() == 0, "nothing written while nothing changed")
    m.w8(SCR + SCR_FOCUS, FOCUS_PLAY_MODE)
    m.w8(SCR + SCR_POPUP, 0)
    on_screen(0)
    q.step("8th button")
    on_screen(40)
    q.step("8th button")
    check(mask() == 1 | 1 << 40, f"Beat Slice Thru chosen for instruments 1 and 41 (mask {mask():#x})")
    tick(SAVE_DELAY - 2)
    check(writes() == 0, "no write right after a change")
    tick(5)
    check(writes() == 1 and fs.overlay.get(path("A")) == bts2(1 | 1 << 40, 0),
          "the change is written once, a moment later (BTS2, the Thru and Loop masks, the checksum: 24 bytes)")
    check(max(fs.read_sizes + fs.write_sizes, default=0) <= 256, "every read and write is at most 256 bytes")

    fs.set_project(Path("."), "B", "Projects/User")
    tick()
    check(mask() == 0, "another project without the file: none")
    on_screen(0)
    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    names, row, count, bar = q.draw()
    check(mask() == 1 | 1 << 40 and row == 6,
          f"back to the first project: instruments 1 and 41 again, and the list redrawn on Beat Slice Thru ({row})")

    good = fs.overlay[path("A")]
    for label, bad in (("a wrong checksum", good[:-1] + bytes([good[-1] ^ 1])),
                       ("a wrong magic", b"X" + good[1:]),
                       ("Thru bits above instrument 48", bts2(1 | 1 << 50, 0)),
                       ("Loop bits above instrument 48", bts2(1, 1 | 1 << 50)),
                       ("a BTS1 file with bits above instrument 48", bts1(1 | 1 << 50)),
                       ("a BTS2 file 16 bytes long", good[:16]),
                       ("a short file", good[:12])):
        fs.overlay[path("A")] = bad
        fs.set_project(Path("."), "B", "Projects/User")
        tick()
        fs.set_project(Path("."), "A", "Projects/User")
        tick()
        check(mask() == 0, f"{label}: none")
    fs.overlay[path("A")] = good

    print("Loop in the file")

    def loops() -> int:
        return m.r32(q.ram + LOOP_MASK) | m.r32(q.ram + LOOP_MASK + 4) << 32

    def reopen() -> None:
        fs.set_project(Path("."), "B", "Projects/User")
        tick()
        fs.set_project(Path("."), "A", "Projects/User")
        tick()

    reopen()
    m.w32(q.ram + LOOP_MASK, 1)                 # Loop on for instrument 1 (Beat Slice Thru)
    tick(SAVE_DELAY + 5)
    check(fs.overlay.get(path("A")) == bts2(1 | 1 << 40, 1), "Loop on: written a moment later, in the Loop mask")
    reopen()
    check(mask() == 1 | 1 << 40 and loops() == 1, "the project reopened: Thru and Loop as saved")
    fs.overlay[path("A")] = bts1(1 | 1 << 40)
    reopen()
    check(mask() == 1 | 1 << 40 and loops() == 0, "a BTS1 file (bt4 and older): its Thru flags, every Loop off")
    fs.overlay[path("A")] = bts2(1, 1 | 1 << 5)
    reopen()
    check(mask() == 1 and loops() == 1, "a Loop bit on an instrument without Thru: ignored")
    fs.overlay[path("A")] = good
    reopen()
    check(mask() == 1 | 1 << 40 and loops() == 0, "the file put back: Thru only")

    fs.set_project(Path("."), "B", "Projects/User")
    tick()
    m.w8(FM_STATE, 5)                       # the file manager busy (a project being opened)
    fs.set_project(Path("."), "A", "Projects/User")
    reads = len([x for x in fs.log if "slicethru.bin mode 0" in x])
    tick(3)
    after = len([x for x in fs.log if "slicethru.bin mode 0" in x])
    check(after == reads and mask() == 0, "while the file manager is busy: the file isn't read yet (no flags)")
    m.w8(FM_STATE, 0)
    tick()
    check(mask() == 1 | 1 << 40, "once it is idle: read")
    m.w8(FM_STATE, 5)
    fs.set_project(Path("."), "B", "Projects/User")
    tick()
    check(mask() == 0, "another project opened while the file manager is busy: the flags cleared at once")
    m.w8(FM_STATE, 0)
    fs.set_project(Path("."), "A", "Projects/User")
    tick()

    print("stale flags")
    m.w32(q.ram + LOOP_MASK + 4, 1 << 8)    # Loop on for instrument 41
    m.w8(INS + INS_SIZE * 40 + 0x3C, 0)     # instrument 41 is now a 1-Shot instrument
    m.w8(FM_STATE, 5)
    tick()
    check(mask() & 1 << 40, "an instrument's mode changed while the file manager is busy: its flag kept for now")
    m.w8(FM_STATE, 0)
    tick()
    check(mask() == 1 and loops() == 0,
          "once it is idle: the flag of the instrument that isn't Beat Slice any more is cleared, its Loop too")
    m.w8(INS + INS_SIZE * 40 + 0x3C, BEAT_SLICE)
    tick(SAVE_DELAY + 5)
    check(mask() == 1 and fs.overlay.get(path("A")) == bts2(1, 0),
          "it stays cleared when the instrument becomes Beat Slice again, and the file follows")
    m.w32(q.ram + 4, 1 | 1 << 2)
    m.w8(INS + INS_SIZE * 2, 0)             # instrument 3 deleted
    tick()
    check(mask() == 1, "a flag on a deleted instrument is cleared")

    print("a new project and Save As")

    def save(name: str | None) -> int:
        return project_save.save(m, tick, name, settle=SAVE_DELAY + 5)

    fs.set_project(Path("."), "New Project", "")    # as the firmware's new project 0x60117c8c
    tick()
    check(mask() == 0, "a new project (\"New Project\", no path): none")
    m.w32(q.ram + 4, 1 << 1)
    m.w32(q.ram + LOOP_MASK, 1 << 1)
    n = writes()
    tick(SAVE_DELAY * 2)
    check(writes() == n and not any("New Project" in x for x in fs.log),
          "while it has no folder: nothing written or read")
    ok = save("C")
    check(ok and m.cstr(PROJECT_NAME) == "C" and mask() == 1 << 1 and loops() == 1 << 1
          and fs.overlay.get(path("C")) == bts2(1 << 1, 1 << 1),
          "its first save (Save As through the firmware's save request): Thru and Loop kept and written to its folder")
    ok = save("D")
    check(ok and mask() == 1 << 1 and loops() == 1 << 1 and fs.overlay.get(path("D")) == bts2(1 << 1, 1 << 1)
          and fs.overlay.get(path("C")) == bts2(1 << 1, 1 << 1),
          "Save As from a saved project: the flags kept, written to the new folder, the old file left")
    n = writes()
    ok = save(None)
    check(ok and m.cstr(PROJECT_NAME) == "D" and mask() == 1 << 1 and writes() == n,
          "a plain save: the same project, the flags kept, nothing written")
    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(mask() == 1, "a project opened after a Save As: its own file's flags")
    m.write(PROJECT_NAME, b"\0")                # the name cleared while Workspace loads
    tick()
    fs.set_project(Path("."), "D", "Projects/User")
    tick()
    check(mask() == 1 << 1, "a project reopened, its name cleared and restored on the way: its own file's flags")
    project_save.new_project_checks(check, m, fs, tick, "slicethru.bin", lambda: (mask(), loops()), (0, 0),
                                    (1 << 1, 1 << 1), lambda v: bts2(*v), SAVE_DELAY)

    print("stock firmware with the same project")
    s = Rig(stock, False)
    s.instrument(0)
    v = s.voice()
    s.note_on(v, 0, 25)
    check(s.fields(v)["end"] == frames_of(0x8000),
          "stock firmware never reads slicethru.bin: the instrument plays Beat Slice (note 25 ends at its slice end)")

    tick(1, pat_hook)
    check(True, "the Pattern screen's update hook runs the same tick")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    ptf, stock = Path(sys.argv[1]), Path(sys.argv[2])
    p, s = Rig(ptf, True), Rig(stock, False)
    site_checks(p)
    off_checks(p, s)
    on_checks(p, s)
    render_checks(p, s)
    fx_checks(p, s)
    stereo_checks(p, s)
    loop_checks(ptf, stock)
    reentry_checks(p, s)
    write_checks(p, s)
    q, t = ScreenRig(ptf, True), ScreenRig(stock, False)
    list_checks(q, t)
    input_checks(q, t)
    ui_play_checks(q)
    off_list_checks(ScreenRig(ptf, True), ScreenRig(stock, False))     # fresh: the same history in both
    loop_ui_checks(ScreenRig(ptf, True), ScreenRig(stock, False))
    file_checks(ptf, stock)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
