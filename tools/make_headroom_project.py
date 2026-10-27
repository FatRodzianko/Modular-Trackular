"""Write the worst-case test project for the headroom run (docs/TIMING_HEADROOM_PLAN.md
section 13) into a copy of a project made on the device.

  python tools/make_headroom_project.py <TrackerPlus .ptf> <project folder> <out folder>

The project folder is one copied from the card's Projects folder: a new project with
sample instruments 1-8 and synths S1-S3 set up (the samples are your own; this script
writes no instrument). The out folder must not exist; the script copies the project
there and changes, through the firmware's own code in the emulator:
  - patterns/pattern_01.mtp and pattern_02.mtp: every step of all 16 tracks busy
    (pattern_write_file 0x600c1fb4; the file is read back with pattern_read_file
    0x600c2350 and must come back identical);
  - project.mt: the song (pattern 1, pattern 2), the tempo, the current pattern, no
    muted track, and the two patterns' Song screen masks (pattern_update_track_usage 0x600c3998), written by
    project_serialize 0x600c2600. Before any change, the base project.mt must come back
    byte for byte through the same path, so its layout is the one this script knows;
  - masterfx.bin, swing.bin, interpolate.bin and polymeter.bin, in the formats the
    patches write (stock firmware and the builds without a patch ignore its file).
Then the sequencer plays both patterns in song mode on the given firmware, and the script
reports what went out. Use the MH2 build there: it has every patch the project uses.

Every CRC the firmware writes is 0 on the device (170 files checked: project.mt and
.mtp), so the CRC object the firmware sets up at boot is stood in by one returning 0.
"""
from __future__ import annotations

import shutil
import struct
import sys
from pathlib import Path

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R3

sys.path.insert(0, str(Path(__file__).resolve().parent / "emu"))
import seq_timing as st  # noqa: E402
import synth_rig as sr  # noqa: E402
from fakefs import FILEMAN, PROJECT_NAME, PROJECT_PATH, FakeFS  # noqa: E402

FM = FILEMAN
FM_CRC = FM + 0x1104
WRITE_PATTERN, READ_PATTERN = 0x600C1FB4, 0x600C2350
TRACK_USAGE = 0x600C3998
SERIALIZE = 0x600C2600
PROJECT_BASE = 0x20200000           # project_serialize's second argument
PROJECT = st.PROJECT                # 0x20204000
PLAYLIST, BLOCK = PROJECT + 0x5C2, PROJECT + 0x6C4
TEMPO = PROJECT + 0x774             # float, BPM
CURRENT = PROJECT + 0x6C8
MASK_LO, MASK_HI = PROJECT + 0x8CC, PROJECT + 0xBF6
MUTES = ((PROJECT + 0x6E0, 16), (PROJECT + 0xCF6, 8))     # mute flags, cleared: every track plays
MT_SIZE, PAT_SIZE, TRACK = 0x914, 0x3030, 0x301
VOICE_NOTE_ON = 0x5C04                # ITCM: a sample voice's note-on (tracks 1-8)
NOTE_ON_OUT = 0x600ABB44             # seq_note_on_out_q(note, velocity, channel, synth): MIDI and synth notes
TEMPO_MAX = 800.0                   # the tempo edit's clamp (0x600fd6b0: 40..800)

STEPS = 16
SAMPLE_TRACKS = range(0, 8)         # instruments 1-8 (0-7)
SYNTH_TRACKS = range(8, 14)         # S1 S1 S2 S2 S3 S3 (64-66)
MIDI_TRACKS = range(14, 16)         # M01, M02 (48, 49)
SYNTH_VOICES = (3, 3, 2)           # Polyphony of S1-S3: the three slots share 8 voices (0x600b1400)
POLY_LENGTHS = {2: 15, 5: 13, 10: 11, 13: 7}    # track (0-based) -> length, polymeter only

FX_MICRO, FX_ROLL, FX_CHANCE, FX_CHORD, FX_GLIDE, FX_ARP = 2, 3, 4, 14, 19, 21
FX_CC_A, FX_CC_B, FX_DELAY_SEND, FX_REVERB_SEND = 8, 9, 30, 32
COND_FIRST = 103                    # condtrig: 103.. = 1:2, 2:2, 1:3 ... 8:8 (up to 137)

REVERB_SHIMMER, DELAY_GATED = 4, 3  # masterfx's lists
SWING = 66                          # Global Swing amount, on


def fnv1a(data: bytes) -> int:
    h = 2166136261
    for b in data:
        h = (h ^ b) * 16777619 & 0xFFFFFFFF
    return h


def settings_file(magic: bytes, data: bytes) -> bytes:
    """masterfx, swing and interpolate: magic, 4 bytes, FNV-1a of the 4 bytes"""
    return magic + data + struct.pack("<I", fnv1a(data))


def polymeter_file(lengths: dict[int, list[int]]) -> bytes:
    entries = b"".join(bytes([p]) + bytes(v) for p, v in sorted(lengths.items()))
    count = struct.pack("<I", len(entries) // 17)
    return b"PMT1" + count + entries + struct.pack("<I", fnv1a(count + entries))


def pattern_data(variant: int) -> bytes:
    """A 16-step pattern with a note and both FX on every step of every track. variant
    shifts the notes and values, so pattern 2 differs from pattern 1."""
    d = bytearray(PAT_SIZE)
    for t in range(16):
        base = 0x1C + TRACK * t
        d[base] = STEPS - 1
        for s in range(128):
            d[base + 1 + 6 * s] = 0xFF
    for t in range(16):
        for s in range(STEPS):
            note = 36 + (5 * s + 7 * t + 2 * variant) % 36
            up = (s + variant) % 2 == 0
            if t in SAMPLE_TRACKS:
                instr = t
                if s % 2 == 0:
                    fx1 = (FX_MICRO, 40)
                elif t < 4:
                    fx1 = (FX_CHANCE, COND_FIRST + (s // 2 + t + variant) % 35)
                elif t < 6:
                    fx1 = (FX_ROLL, 6)
                else:
                    fx1 = (FX_ARP, 4)
                fx2 = (FX_REVERB_SEND, 90) if s % 2 == 0 else (FX_DELAY_SEND, 90)
            elif t in SYNTH_TRACKS:
                instr = 64 + (t - 8) // 2
                fx1 = (FX_CHORD, 1 + (s + t + variant) % 29)
                fx2 = (FX_CC_A if t % 2 == 0 else FX_CC_B, 127 if up else 0)   # macro 1 / 2, ramps
            else:
                instr = 48 + t - 14
                fx1 = (FX_CC_A, 127 if up else 0)                               # ramps
                fx2 = (FX_GLIDE, 0) if s % 2 == 0 else (FX_CC_B, 8 * s)         # legato on G
            d[0x1D + TRACK * t + 6 * s: 0x1D + TRACK * t + 6 * s + 6] = bytes(
                [note, instr, fx1[0], fx1[1], fx2[0], fx2[1]])
    return bytes(d)


class CardFS(FakeFS):
    """FakeFS with one shared stub for the methods nobody should call (the sequencer's
    fake MIDI ports take most of the emulator's stub slots), as test_copypattern.py's."""

    def __init__(self, m, workspace: Path):
        self.m, self.workspace = m, Path(workspace)
        self.files, self.next_handle, self.log = {}, 0x100, []
        self.overlay, self.read_sizes, self.write_sizes = {}, [], []
        self.writes_opened, self.removed, self.paths = 0, [], {}
        self.obj, vt = m.alloc(0x10), m.alloc(0x80)
        bad = m.add_stub(self._unexpected(-1))
        for off in range(0, 0x80, 4):
            m.w32(vt + off, bad)
        for off, f in ((0x08, self._exists), (0x14, self._remove), (0x2C, self._open),
                       (0x30, self._read), (0x34, self._write), (0x40, self._close)):
            m.w32(vt + off, m.add_stub(f))
        m.w32(self.obj, vt)
        m.w32(FILEMAN, self.obj)


class Rig:
    def __init__(self, ptf: Path, workspace: Path):
        self.synths = sr.SynthRig(ptf)          # the synth system, so synth notes play
        self.synths.configure(polys=SYNTH_VOICES)
        self.m = m = self.synths.m
        self.fs = CardFS(m, workspace)
        crc_obj, crc_vt = m.alloc(8), m.alloc(8)
        m.w32(crc_obj, crc_vt)
        m.w32(crc_vt, m.add_stub(lambda mm: 0))
        m.w32(FM_CRC, crc_obj)
        self.path, self.buf, self.out = m.alloc(256), m.alloc(PAT_SIZE), m.alloc(MT_SIZE)

    def write_pattern(self, fw_path: str, data: bytes) -> bytes:
        m = self.m
        m.write(self.buf, data)
        m.write(self.path, fw_path.encode() + b"\0")
        if m.call(WRITE_PATTERN, FM, self.path, self.buf) != 1:
            raise SystemExit(f"pattern_write_file failed for {fw_path}")
        written = self.fs.overlay[fw_path]
        m.write(self.buf, bytes(PAT_SIZE))
        m.call(READ_PATTERN, FM, self.path, self.buf)
        if bytes(m.uc.mem_read(self.buf, PAT_SIZE))[:0x302C] != written[:0x302C]:
            raise SystemExit(f"pattern_read_file doesn't read {fw_path} back identical")
        return written

    def load_project(self, mt: bytes) -> None:
        m = self.m
        m.write(PLAYLIST, mt[0x10:0x110])
        m.write(BLOCK, mt[0x110:0x810])
        m.write(PROJECT_NAME, mt[0x810:0x831])
        m.write(PROJECT_PATH, mt[0x831:0x90F])

    def serialize(self, under: bytes = bytes(MT_SIZE)) -> bytes:
        """project_serialize into a buffer holding `under` (the bytes it leaves alone)"""
        m = self.m
        m.write(self.out, under)
        if m.call(SERIALIZE, FM, PROJECT_BASE, self.out) != 1:
            raise SystemExit("project_serialize failed")
        return bytes(m.uc.mem_read(self.out, MT_SIZE))


def song_run(rig: Rig, pats: list[bytes], passes: int) -> dict:
    """Plays pattern 1, 2, 1, 2... in song mode, loading the requested pattern into the
    preload buffer as the main loop would. Returns what played."""
    m = rig.m
    count = {"sample": 0, "synth": 0}

    def sample(uc, a, s, u):
        count["sample"] += 1

    def out(uc, a, s, u):
        if uc.reg_read(UC_ARM_REG_R3):            # (note, velocity, channel, synth)
            count["synth"] += 1
    m.uc.hook_add(UC_HOOK_CODE, sample, begin=VOICE_NOTE_ON, end=VOICE_NOTE_ON)
    m.uc.hook_add(UC_HOOK_CODE, out, begin=NOTE_ON_OUT, end=NOTE_ON_OUT)
    cur, nxt = m.alloc(PAT_SIZE), m.alloc(PAT_SIZE)
    m.write(cur, pats[0])
    m.write(nxt, pats[1])
    m.write(PLAYLIST, bytes([1, 2, 0]))
    m.w8(PROJECT + 0x6C1, 0)
    m.w8(CURRENT, 1)
    played = set()

    def request(mm):
        n = mm.arg(1)
        played.add(n)
        mm.write(mm.r32(st.SEQ + 0x18), pats[(n - 1) % 2])
        return 1
    m.stub_function(st.FM_REQUEST_PATTERN_LOAD, request)
    m.w8(st.SEQ + 0x1C, 1)                         # song mode (as seq_play_song_from)
    st.play(m, cur, nxt, 576 * STEPS * passes)
    count["midi"] = sum(1 for _, e in m.events if "note on" in e)
    count["cc"] = sum(1 for _, e in m.events if " cc " in e)
    count["patterns"] = sorted(played)
    return count


def junk(mt: bytes) -> set[int]:
    """Bytes of a project.mt that project_serialize doesn't write: the device leaves
    whatever its buffer held there (seen in 10 projects from the card): +0xe..+0xf after
    the header's size, and the name's and the path's fields after their terminator."""
    name_end, path_end = mt.index(b"\0", 0x810), mt.index(b"\0", 0x831)
    return {0xE, 0xF} | set(range(name_end + 1, 0x831)) | set(range(path_end + 1, 0x910))


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    ptf, base, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    mt_path = base / "project.mt"
    if not mt_path.is_file():
        raise SystemExit(f"{base} has no project.mt: give a project folder copied from the card")
    if out.exists():
        raise SystemExit(f"{out} exists; give a new folder")
    mt = mt_path.read_bytes()
    if len(mt) != MT_SIZE or mt[:4] != b"MT\0\0":
        raise SystemExit(f"{mt_path} isn't a 1.2.0 project.mt ({len(mt)} bytes)")

    missing = [n for n in range(1, 9) if not list((base / "instruments").glob(f"{n} *.pti"))]
    synths = [n for n in range(3) if not (base / f"{n}.patch").is_file()]
    if missing:
        print(f"note: no instrument file for slot(s) {missing}: those tracks will be silent")
    if synths:
        print(f"note: no {', '.join(f'{n}.patch' for n in synths)}: those synth slots play as a new project's")

    rig = Rig(ptf, out)
    m = rig.m
    rig.load_project(mt)
    if any(i not in junk(mt) for i, (a, b) in enumerate(zip(rig.serialize(), mt)) if a != b):
        raise SystemExit("the base project.mt doesn't come back identical through project_serialize; "
                         "its layout isn't the one this script knows")

    pats = [pattern_data(0), pattern_data(1)]
    files = {}
    for n, data in enumerate(pats, 1):
        fw = f"Workspace/pattern_{n:02d}.mtp"
        files[f"patterns/pattern_{n:02d}.mtp"] = rig.write_pattern(fw, data)
        m.write(rig.buf, data)
        m.call(TRACK_USAGE, FM, n, rig.buf)
    lo = [m.uc.mem_read(MASK_LO + n, 1)[0] for n in (1, 2)]
    hi = [m.uc.mem_read(MASK_HI + n, 1)[0] for n in (1, 2)]
    if lo != [0xFF, 0xFF] or hi != [0xFF, 0xFF]:
        raise SystemExit(f"Song screen masks aren't all tracks: lo {lo} hi {hi}")
    m.write(PLAYLIST, bytes([1, 2]) + bytes(0xFE))
    m.write(TEMPO, struct.pack("<f", TEMPO_MAX))
    m.w8(CURRENT, 1)
    muted = sum(any(m.uc.mem_read(a, n)) for a, n in MUTES)
    for a, n in MUTES:
        m.write(a, bytes(n))
    new_mt = rig.serialize(mt)
    changed = [i for i in range(MT_SIZE) if new_mt[i] != mt[i]]
    files["project.mt"] = new_mt

    files["masterfx.bin"] = settings_file(b"HMF1", bytes([REVERB_SHIMMER, DELAY_GATED, 0, 0]))
    files["swing.bin"] = settings_file(b"HSW1", bytes([1, SWING - 25, 0, 0]))
    files["interpolate.bin"] = settings_file(b"HIP1", bytes([1, 1, 0, 0]))
    lengths = [POLY_LENGTHS.get(t, 0) for t in range(16)]
    files["polymeter.bin"] = polymeter_file({1: lengths, 2: lengths})

    run = song_run(rig, pats, 6)

    shutil.copytree(base, out)
    (out / "patterns").mkdir(exist_ok=True)
    for rel, data in files.items():
        (out / rel).write_bytes(data)

    print(f"wrote {out}")
    print(f"  patterns 1 and 2: {STEPS} steps, all 16 tracks busy, read back identical by the firmware")
    print(f"  project.mt: song 1 2, tempo {TEMPO_MAX:g}, Song screen masks set "
          f"({len(changed)} bytes changed, offsets {changed[0]:#x}..{changed[-1]:#x})")
    if muted:
        print("  the base project had muted tracks: unmuted")
    print(f"  masterfx Shimmer + \"Gated\", Global Swing {SWING}, FX Interpolation MIDI CC and Synths On,")
    print(f"  polymeter lengths {', '.join(f'track {t + 1}: {n}' for t, n in POLY_LENGTHS.items())}")
    print(f"  song mode in the emulator ({ptf.name}), 6 patterns: {run['sample']} sample note-ons, "
          f"{run['synth']} synth notes, {run['midi']} MIDI note-ons, {run['cc']} MIDI CCs, "
          f"patterns requested {run['patterns']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
