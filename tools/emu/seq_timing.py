"""Run the stock sequencer tick on constructed patterns and print MIDI timing.

  python tools/emu/seq_timing.py <TrackerPlus_1.2.0.ptf>

Builds two pattern buffers in SDRAM, starts playback with the firmware's own
seq_play_from, then calls seq_tick (ITCM 0x35e8) once per tick. The MIDI port
objects (*0x200171c0 USB, *0x200171c4 DIN) are replaced by fakes whose vtable
methods log their arguments, so the output is exactly what the sequencer would
send. Two runs (FINDINGS section 12):

1. Micro-move and CC timing on track 0 (MIDI instrument 48 = channel 1).
2. Song mode with two 4-step patterns: when the next pattern is requested and
   when the play/preload buffers are swapped.

Tick 12 is micro-step 1 of step 0; a step is 576 ticks, a micro-step 12.
"""
import struct
import sys
from pathlib import Path

from unicorn import UC_HOOK_CODE

sys.path.insert(0, str(Path(__file__).resolve().parent))
from machine import Machine  # noqa: E402

SEQ = 0x20017274            # sequencer object
SETTINGS = 0x2002061C       # global settings (+0x2c MIDI out: 0 off, 1 USB, 2 jack, 3 both)
PROJECT = 0x20204000        # project (OCRAM)
SEQ_TICK = 0x35E8
SEQ_PLAY_FROM = 0x1250
FM_REQUEST_PATTERN_LOAD = 0x600BC938
SEQ_SWAP_PATTERN_BUFFERS = 0x600ACC68
PORT_METHODS = {0x0C: "cc", 0x20: "note on", 0x24: "note off"}


def make(ptf: Path) -> Machine:
    m = Machine(ptf)
    m.uc.mem_map(0x40000000, 0x4000000)      # peripherals as plain RAM
    m.uc.mem_map(0xE0000000, 0x100000)
    m.call(0x60166294, max_insns=50_000_000)  # static constructors
    m.ticks = 0
    m.events = []
    for name, ptr in (("USB", 0x200171C0), ("DIN", 0x200171C4)):
        obj, vt = m.alloc(0x40), m.alloc(0x80)
        m.w32(obj, vt)
        for k in range(24):
            def method(mm, off=4 * k, name=name):
                what = PORT_METHODS.get(off, f"vt+{off:#x}")
                mm.events.append((mm.ticks, f"{name} {what} {mm.arg(1)} {mm.arg(2)} ch{mm.arg(3)}"))
                return 0
            m.w32(vt + 4 * k, m.add_stub(method))
        m.w32(ptr, obj)
    for a in (0x7048, 0x4FEC):                 # audio engine resets (engine not set up here)
        m.stub_function(a, lambda mm: 0)
    return m


def pattern(m: Machine, last_step: int) -> int:
    data = bytearray(0x3030)
    for t in range(16):
        base = 0x1C + t * 0x301
        data[base] = last_step
        for s in range(128):
            data[base + 1 + 6 * s] = 0xFF        # empty note
    buf = m.alloc(len(data))
    m.write(buf, data)
    return buf


def set_step(m, buf, track, step, note, instr, fx1=(0, 0), fx2=(0, 0)):
    m.write(buf + 0x1C + track * 0x301 + 1 + 6 * step,
            bytes([note & 0xFF, instr, fx1[0], fx1[1], fx2[0], fx2[1]]))


def play(m, cur, nxt, ticks):
    m.w32(SEQ + 0x14, cur)
    m.w32(SEQ + 0x18, nxt)
    m.w8(SETTINGS + 0x2C, 2)                  # MIDI out: jack
    m.call(SEQ_PLAY_FROM, SEQ, 0)
    m.events.clear()
    for _ in range(ticks):
        m.ticks += 1
        m.call(SEQ_TICK, SEQ, max_insns=2_000_000)


def show(m):
    for tick, what in m.events:
        print(f"  tick {tick:5d}  step {(tick - 12) // 576} micro {(tick - 12) % 576 // 12 + 1:2d}  {what}")


def micro_move(ptf):
    m = make(ptf)
    cur, nxt = pattern(m, 15), pattern(m, 15)
    m.w8(PROJECT + 0xAA0 + 0x10 * 1 + 3, 74)  # MIDI channel 1: CC A = 74
    set_step(m, cur, 0, 0, 60, 48)
    set_step(m, cur, 0, 1, 62, 48, (2, 0))    # m 0
    set_step(m, cur, 0, 2, 64, 48, (2, 50))   # m 50
    set_step(m, cur, 0, 3, 65, 48, (2, 97))   # m 97
    set_step(m, cur, 0, 4, 67, 48, (2, 100))  # m 100
    set_step(m, cur, 0, 5, 69, 48)            # plain note after m 100
    set_step(m, cur, 0, 6, -1, 48, (8, 10))   # CC A 10, no note
    set_step(m, cur, 0, 7, -1, 48, (8, 90))   # CC A 90, no note
    play(m, cur, nxt, 576 * 9)
    print("Micro-move and CC (with all settings zero, notes go out 24 semitones up):")
    show(m)


def song_preload(ptf):
    m = make(ptf)
    a, b = pattern(m, 3), pattern(m, 3)
    set_step(m, a, 0, 0, 60, 48)
    set_step(m, b, 0, 0, 72, 48)
    m.write(PROJECT + 0x5C2, bytes([1, 2, 0]))    # playlist
    m.w8(PROJECT + 0x6C1, 0)                      # song position

    def request(mm):
        mm.events.append((mm.ticks, f"preload request: pattern {mm.arg(1)}"))
        return 1
    m.stub_function(FM_REQUEST_PATTERN_LOAD, request)
    m.uc.hook_add(UC_HOOK_CODE, lambda uc, a_, s, u: m.events.append((m.ticks, "swap play/preload buffers")),
                  begin=SEQ_SWAP_PATTERN_BUFFERS, end=SEQ_SWAP_PATTERN_BUFFERS)
    m.w32(SEQ + 0x14, a)
    m.w32(SEQ + 0x18, b)
    m.w8(SETTINGS + 0x2C, 2)
    m.call(SEQ_PLAY_FROM, SEQ, 0)
    m.w8(SEQ + 0x1C, 1)                           # song mode
    m.events.clear()
    for _ in range(576 * 9):
        m.ticks += 1
        m.call(SEQ_TICK, SEQ, max_insns=2_000_000)
    print("Song mode, two 4-step patterns (the preload stub does not load; buffer b is pre-filled):")
    show(m)


if __name__ == "__main__":
    ptf = Path(sys.argv[1])
    micro_move(ptf)
    song_preload(ptf)
