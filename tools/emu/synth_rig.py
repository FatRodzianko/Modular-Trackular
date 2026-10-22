"""The firmware's own synth system in the emulator: slots, voices, notes, rendering.

  python tools/emu/synth_rig.py <TrackerPlus.ptf> [model ...]

Used by tools/emu/test_synths.py. After the static constructors (seq_timing.make()) the
rig runs the app's two synth setup calls the way its init (0x600aa3c8) does
(docs/FINDINGS.md section 21):
  synth_audio_setup_q(app + 8, hal, fn)          0x600b2118
  audio setup(engine 0x20017b08, hal, app + 8)   0x600b421c
`hal` is a fake object whose vtable +0x4c returns a fake file system and +0x18, +0x1c
and +0x58 fake objects (the codec and two others); every other method returns 0.
`fn` is the std::function the app passes: {0x200170e0, 0, 0x600aa711, 0x600aa791}.
After that the real host, slots, patches and voices exist; the three slots start as
WTFM, ACD and VAP with one voice each.

The rig then:
  - sets a slot's model and polyphony (synth_slot_set_model / _set_polyphony);
  - plays notes, note offs, pitch bend and CCs through the host (the MIDI router that
    seq_note_on_out_q calls; channel = slot + 1);
  - renders blocks through the whole audio graph (0x6003061c) and keeps the synth
    output object's 16-bit blocks per slot, and, when asked, each voice's float output
    as its render (voice vtable +0x10, whatever that word points to) wrote it;
  - counts instructions (Prof: calls of chosen functions, inclusive);
  - measures heap use (sbrk extent minus mallinfo's free bytes, heap_free_q).
Machine's stub slots run out with these fakes, so the rig maps a stub page of its own.

Run as a script it prints the voices, heap and per-block cost of each model given (1
ACD, 2 FAT, 4 VAP, 5 WTFM, 6 PERC; default 4), with slot 0 at 1 voice playing a note.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_SP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seq_timing import make  # noqa: E402

APP = 0x200170E0
S = APP + 8                         # the synth system: slot i's model id at S + 0x30 * i
ENGINE = 0x20017B08                 # the audio engine object
SYNTH_AUDIO_SETUP, AUDIO_SETUP = 0x600B2118, 0x600B421C
APP_FN = (APP, 0, 0x600AA711, 0x600AA791)
GRAPH_UPDATE = 0x6003061C           # one audio block: every graph object's update()
SET_MODEL, SET_POLY, MAX_POLY = 0x600B1440, 0x600B13CC, 0x600B1400
APPLY_RECORDS = 0x600B2080          # (S, records 0x202046c4, reset)
SYNTH_OUT = 0x20019168              # the synth output graph object (update 0x60036634)
TRANSMIT = 0x60030574               # (obj, block, output index)
SLOT_RENDER = 0x1F0D8               # synth_slot_render (ITCM)
MANAGER_RENDER = 0x60125FB8
HEAP_BRK, HEAP_START, HEAP_FREE = 0x2002FB9C, 0x2002FC68, 0x6010C254
BLOCK = 128
RATE = 44100

ACD, FAT, VAP, WTFM, PERC = 1, 2, 4, 5, 6
MODEL_NAMES = {ACD: "ACD", FAT: "FAT", VAP: "VAP", WTFM: "WTFM", PERC: "PERC"}
VOICE_VT = {ACD: 0x600662F0, FAT: 0x600674E4, VAP: 0x600667E4, WTFM: 0x60067814, PERC: 0x60066F90}
# a voice vtable's entries (the same layout for every model)
V_DELETE, V_MIDI, V_SET_PARAM, V_RENDER, V_POST, V_SOUNDING = 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18
V_HOLDS_NOTE, V_HOLDS_ANY = 0x1C, 0x20

# slot object fields
SLOT_VOICES, SLOT_NVOICES, SLOT_MODEL = 0x14, 0x174, 0x178
VOICE_RECORD = 0x2C
VOICE_PATCH = 0x04                  # voice + 4: the slot's patch object

# host (MIDI router interface) vtable
H_CC, H_BEND, H_NOTE_ON, H_NOTE_OFF = 0x0C, 0x1C, 0x20, 0x24

STUB2 = 0x70100000                  # the rig's own stub page


class SynthRig:
    """The stock synth system of one firmware (stock or patched), set up and ready."""

    def __init__(self, ptf: Path, log: bool = False):
        self.m = m = make(Path(ptf))
        m.uc.mem_map(STUB2, 0x10000)
        self._stubs: dict[int, object] = {}
        self._stub_next = STUB2
        m.uc.hook_add(UC_HOOK_CODE, self._on_stub, begin=STUB2, end=STUB2 + 0xFFFF)
        self.log: list[str] | None = [] if log else None
        fs = self.fake("fs")
        codec, h1c, h58 = self.fake("codec"), self.fake("hal1c"), self.fake("hal58")
        self.hal = self.fake("hal", ret={0x4C: fs, 0x18: codec, 0x1C: h1c, 0x58: h58})
        fn = m.alloc(16)
        for i, w in enumerate(APP_FN):
            m.w32(fn + 4 * i, w)
        m.call(SYNTH_AUDIO_SETUP, S, self.hal, fn, max_insns=200_000_000)
        m.call(AUDIO_SETUP, ENGINE, self.hal, S, max_insns=200_000_000)
        self.host = m.r32(S + 0xAC) + 8
        m.add_stub = self.add_stub              # fakes made later (fakefs) use this page too
        self.out: dict[int, np.ndarray] = {}            # output index -> this block's samples
        m.uc.hook_add(UC_HOOK_CODE, self._on_transmit, begin=TRANSMIT, end=TRANSMIT)
        self.capturing = False
        self.voice_out: dict[int, list[np.ndarray]] = {}    # voice -> rendered L blocks
        self.voice_out_r: dict[int, list[np.ndarray]] = {}
        self.render_args: list[tuple] = []              # (voice, args r1..r3, 4 stack words)
        self._pending = None
        self._ret_hooks: set[int] = set()
        self._entry_hooks: set[int] = set()
        self.sentinel: float | None = None

    # -- stubs and fakes ----------------------------------------------------------
    def _on_stub(self, uc, address, size, user):
        f = self._stubs.get(address)
        if f is not None:
            r = f(self.m)
            if r is not None:
                uc.reg_write(UC_ARM_REG_R0, r & 0xFFFFFFFF)

    def add_stub(self, func) -> int:
        """A Thumb address that runs func(machine) and returns (r0 = its result)."""
        a = self._stub_next
        self._stub_next += 4
        assert a < STUB2 + 0x10000, "out of stub slots"
        self.m.uc.mem_write(a, struct.pack("<H", 0x4770))   # bx lr
        self._stubs[a] = func
        return a | 1

    def fake(self, name: str, n: int = 40, ret: dict[int, int] | None = None) -> int:
        """An object whose n virtual methods return ret.get(offset, 0) (and log the call)."""
        m = self.m
        obj, vt = m.alloc(0x40), m.alloc(4 * n)
        m.w32(obj, vt)
        for k in range(n):
            def meth(mm, off=4 * k):
                if self.log is not None:
                    self.log.append(f"{name}.vt+{off:#x}({mm.arg(0):#x}, {mm.arg(1):#x}, {mm.arg(2):#x})")
                return (ret or {}).get(off, 0)
            m.w32(vt + 4 * k, self.add_stub(meth))
        return obj

    # -- slots and voices ---------------------------------------------------------
    def slot(self, i: int) -> int:
        return self.m.r32(S + 4 + 0x30 * i)

    def patch_obj(self, i: int) -> int:
        return self.m.r32(S + 0x0C + 0x30 * i)

    def model(self, i: int) -> int:
        return self.m.r32(S + 0x30 * i)

    def voices(self, i: int) -> list[int]:
        s = self.slot(i)
        return [self.m.r32(s + SLOT_VOICES + VOICE_RECORD * k) for k in range(self.m.r32(s + SLOT_NVOICES))]

    def set_model(self, i: int, model: int) -> None:
        self.m.call(SET_MODEL, S, i, model, max_insns=100_000_000)

    def set_poly(self, i: int, n: int) -> None:
        self.m.call(SET_POLY, S, i, n, max_insns=100_000_000)

    def max_poly(self, i: int) -> int:
        return self.m.call(MAX_POLY, S, i)

    def configure(self, models=(VAP, ACD, ACD), polys=(1, 1, 1)) -> None:
        """Every slot to one voice first (the 8-voice cap), then the models, then the polyphony."""
        for i in range(3):
            self.set_poly(i, 1)
        for i, md in enumerate(models):
            self.set_model(i, md)
        for i, n in enumerate(polys):
            self.set_poly(i, n)

    def vcall(self, voice: int, off: int, *args: int) -> int:
        """Call a voice's virtual method (vtable offset `off`)."""
        return self.m.call(self.m.r32(self.m.r32(voice) + off), voice, *args, max_insns=20_000_000)

    def sounding(self, voice: int) -> int:
        return self.vcall(voice, V_SOUNDING) & 0xFF

    def _entries(self, i: int) -> list[int]:
        p = self.patch_obj(i)
        c = self.m.call(self.m.r32(self.m.r32(p) + 0x50), p)
        a, e = self.m.r32(c), self.m.r32(c + 4)
        return list(range(a, e, 0x38))

    def params(self, i: int) -> list[float]:
        """The current parameter values of slot i's patch: the float vector at patch + 0xc,
        which voices get pushed and patch files save (FINDINGS section 23)."""
        p = self.patch_obj(i)
        a, e = self.m.r32(p + 0x0C), self.m.r32(p + 0x10)
        return list(struct.unpack(f"<{(e - a) // 4}f", bytes(self.m.uc.mem_read(a, e - a))))

    def defaults(self, i: int) -> list[float]:
        """The parameters' Init values (container entries: float at +0x18)."""
        return [struct.unpack("<f", bytes(self.m.uc.mem_read(q + 0x18, 4)))[0] for q in self._entries(i)]

    def ranges(self, i: int) -> list[tuple[float, float]]:
        """Each parameter's min and max (half floats at entry +0x1c, +0x1e)."""
        out = []
        for q in self._entries(i):
            lo, hi = struct.unpack("<2e", bytes(self.m.uc.mem_read(q + 0x1C, 4)))
            out.append((float(lo), float(hi)))
        return out

    def set_param(self, i: int, index: int, value: float, patch: bool = True) -> None:
        """A parameter of slot i: into its patch's current values (so later pushes and a
        patch save keep it; not with patch=False) and to every voice through its set_param
        (vtable +0x0c), as an edit reaches them."""
        if patch:
            self.m.write(self.m.r32(self.patch_obj(i) + 0x0C) + 4 * index, struct.pack("<f", value))
        if not hasattr(self, "_valbuf"):
            self._valbuf = self.m.alloc(8)
        self.m.write(self._valbuf, struct.pack("<f", value))
        for v in self.voices(i):
            self.vcall(v, V_SET_PARAM, index, self._valbuf)

    # -- notes through the host ---------------------------------------------------
    def _host(self, off: int, *args: int) -> int:
        return self.m.call(self.m.r32(self.m.r32(self.host) + off), self.host, *args, max_insns=20_000_000)

    def note_on(self, i: int, note: int, velocity: int = 100) -> None:
        self._host(H_NOTE_ON, note, velocity, i + 1)

    def note_off(self, i: int, note: int, velocity: int = 0) -> None:
        self._host(H_NOTE_OFF, note, velocity, i + 1)

    def bend(self, i: int, value: int) -> None:
        """Pitch bend, 16-bit value as the router takes it."""
        self._host(H_BEND, value & 0xFFFF, i + 1)

    def cc(self, i: int, cc: int, value: int) -> None:
        self._host(H_CC, cc, value, i + 1)

    # -- rendering ----------------------------------------------------------------
    def _on_transmit(self, uc, address, size, user):
        if self.m.arg(0) == SYNTH_OUT:
            blk, idx = self.m.arg(1), self.m.arg(2)
            self.out[idx] = np.frombuffer(bytes(self.m.uc.mem_read(blk + 4, 2 * BLOCK)), dtype="<i2").copy()

    def capture_voices(self, models=tuple(VOICE_VT)) -> None:
        """From now on keep each voice's output as its render method (vtable +0x10, as the
        words read now) wrote it. With `sentinel` set, L and R are filled with it first."""
        for md in models:
            entry = self.m.r32(VOICE_VT[md] + V_RENDER) & ~1
            if entry not in self._entry_hooks:
                self._entry_hooks.add(entry)
                self.m.uc.hook_add(UC_HOOK_CODE, self._on_render_entry, begin=entry, end=entry)
        self.capturing = True

    def _on_render_entry(self, uc, address, size, user):
        if not self.capturing:
            return
        m = self.m
        sp = uc.reg_read(UC_ARM_REG_SP)
        stack = [m.r32(sp + 4 * k) for k in range(4)]
        voice, L, R = m.arg(0), stack[1], stack[2]
        self.render_args.append((voice, m.arg(1), m.arg(2), m.arg(3), *stack))
        if self.sentinel is not None:
            fill = struct.pack("<128f", *([self.sentinel] * BLOCK))
            m.write(L, fill)
            m.write(R, fill)
        self._pending = (voice, L, R)
        lr = uc.reg_read(UC_ARM_REG_LR) & ~1
        if lr not in self._ret_hooks:
            self._ret_hooks.add(lr)
            m.uc.hook_add(UC_HOOK_CODE, self._on_render_return, begin=lr, end=lr)

    def _on_render_return(self, uc, address, size, user):
        if self._pending is None:
            return
        voice, L, R = self._pending
        self._pending = None
        m = self.m
        self.voice_out.setdefault(voice, []).append(np.frombuffer(bytes(m.uc.mem_read(L, 4 * BLOCK)), "<f4").copy())
        self.voice_out_r.setdefault(voice, []).append(np.frombuffer(bytes(m.uc.mem_read(R, 4 * BLOCK)), "<f4").copy())

    def block(self) -> dict[int, tuple[np.ndarray, np.ndarray]]:
        """One audio block through the graph; returns {slot: (left, right)} of the synth
        output's 16-bit blocks (output indices slot and slot + 6; zeros if not sent)."""
        self.out = {}
        self.m.call(GRAPH_UPDATE, max_insns=100_000_000)
        z = np.zeros(BLOCK, dtype="<i2")
        return {i: (self.out.get(i, z), self.out.get(i + 6, z)) for i in range(3)}

    def synth_block(self) -> dict[int, tuple[np.ndarray, np.ndarray]]:
        """One block of the synth output object alone (its update(): the three slots,
        scaled to 16-bit blocks), without the rest of the audio graph; as block()."""
        self.out = {}
        self.m.call(self.m.r32(self.m.r32(SYNTH_OUT)), SYNTH_OUT, max_insns=100_000_000)
        z = np.zeros(BLOCK, dtype="<i2")
        return {i: (self.out.get(i, z), self.out.get(i + 6, z)) for i in range(3)}

    def render(self, n: int, fast: bool = False) -> dict[int, tuple[np.ndarray, np.ndarray]]:
        """n blocks (fast: the synth output object alone); returns {slot: (left, right)}
        concatenated."""
        got = [self.synth_block() if fast else self.block() for _ in range(n)]
        return {i: (np.concatenate([g[i][0] for g in got]), np.concatenate([g[i][1] for g in got]))
                for i in range(3)}

    def render_voice(self, voice: int, n: int = 1) -> np.ndarray:
        """n blocks of one voice alone, through its render method (vtable +0x10) called
        as synth_slot_render calls it: (voice, ctx, 0, 0, [sp] 0, L, R, 2, buffers)."""
        m = self.m
        if not hasattr(self, "_vbufs"):
            ctx, L, R, bufs = m.alloc(0x40), m.alloc(4 * BLOCK), m.alloc(4 * BLOCK), m.alloc(8)
            m.write(ctx, struct.pack("<f", 130.0))
            m.w32(bufs, L)
            m.w32(bufs + 4, R)
            self._vbufs = (ctx, L, R, bufs)
        ctx, L, R, bufs = self._vbufs
        f = m.r32(m.r32(voice) + V_RENDER)
        out = []
        for _ in range(n):
            m.call(f, voice, ctx, 0, 0, 0, L, R, 2, bufs, max_insns=20_000_000)
            out.append(np.frombuffer(bytes(m.uc.mem_read(L, 4 * BLOCK)), "<f4").copy())
        return np.concatenate(out)

    def voice_signal(self, voice: int) -> np.ndarray:
        return np.concatenate(self.voice_out.get(voice, [np.zeros(0, "<f4")]))

    # -- heap -----------------------------------------------------------------------
    def heap_used(self) -> int:
        """Bytes of heap in use: the sbrk extent minus mallinfo's free bytes."""
        return (self.m.r32(HEAP_BRK) - HEAP_START) - self.m.call(HEAP_FREE)


class Prof:
    """Inclusive instruction counts for chosen functions (entry to return to LR)."""

    def __init__(self, m, entries):
        self.m = m
        self.entries = {e & ~1 for e in entries}
        self.n = 0
        self.stack: list[tuple[int, int, int, int]] = []
        self.incl: dict[int, int] = {}
        self.calls: dict[int, int] = {}
        self.h = m.uc.hook_add(UC_HOOK_CODE, self._code)

    def _code(self, uc, a, s, u):
        self.n += 1
        while self.stack and a == self.stack[-1][2] and uc.reg_read(UC_ARM_REG_SP) > self.stack[-1][1] - 4:
            f, sp, lr, n0 = self.stack.pop()
            self.incl[f] = self.incl.get(f, 0) + self.n - n0
        if a in self.entries:
            self.stack.append((a, uc.reg_read(UC_ARM_REG_SP), uc.reg_read(UC_ARM_REG_LR) & ~1, self.n))
            self.calls[a] = self.calls.get(a, 0) + 1

    def reset(self) -> None:
        self.n = 0
        self.stack.clear()
        self.incl.clear()
        self.calls.clear()

    def per_call(self, entry: int) -> float:
        e = entry & ~1
        return self.incl.get(e, 0) / max(1, self.calls.get(e, 0))

    def close(self) -> None:
        self.m.uc.hook_del(self.h)


def voice_cost(rig: SynthRig, model: int, blocks: int = 3) -> tuple[float, int]:
    """Instructions per voice render call for `model`'s render method, and per graph block."""
    entry = rig.m.r32(VOICE_VT[model] + V_RENDER)
    p = Prof(rig.m, [entry])
    for _ in range(blocks):
        p.reset()
        rig.block()
    p.close()
    return p.per_call(entry), p.n


def main() -> int:
    ptf = Path(sys.argv[1])
    models = [int(x) for x in sys.argv[2:]] or [VAP]
    for md in models:
        r = SynthRig(ptf)
        r.configure((md, ACD, ACD), (1, 1, 1))
        idle, _ = voice_cost(r, md)
        h0 = r.heap_used()
        r.set_poly(0, 2)
        per_voice = r.heap_used() - h0
        r.set_poly(0, 1)
        r.note_on(0, 57, 127)
        r.render(2)
        playing, graph = voice_cost(r, md)
        print(f"{MODEL_NAMES[md]:5s} voices {[hex(v) for v in r.voices(0)]}: idle {idle:.0f}, playing {playing:.0f} "
              f"instructions per block; graph {graph}; heap {r.heap_used()} B, {per_voice} B per voice")
    return 0


if __name__ == "__main__":
    sys.exit(main())
