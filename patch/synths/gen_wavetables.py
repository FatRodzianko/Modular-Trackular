"""Generate the wavetable banks for patch/synths from formulas (no third-party tables).

  python patch/synths/gen_wavetables.py <out wt_tables.h>

The banks, in the order PW -0.5..0.5 selects them (eighths of the range; the eighth
slot is the engine's super saw, which has no bank but plays the saw written here):

  Formant    vowel-like spectra: a-e-i-o-u
  Resonant   a saw with a resonant peak sweeping up its spectrum
  Harmonic   harmonics added one after another: sine -> organ-like -> bright
  PWM        a pulse narrowing from 50 % to 3 %
  Basic      sine -> triangle -> saw -> square (PW 0, VAP's default, falls here)
  Sync       a hard-synced saw, the slave's ratio sweeping 1 -> 6.5
  Digital    two-operator FM spectra growing brighter and bell-like

Each bank has 32 frames, a morph from the first to the last. Each frame is defined by
its harmonics 1..64 (amplitude and phase), computed from a formula (an additive
spectrum, or a waveform whose spectrum is taken with an FFT and cut at harmonic 64),
and is stored as band-limited levels of 16-bit samples:

  level      0    1    2    3    4    5    6
  harmonics  64   32   16   8    4    2    1
  samples    352  240  168  120  80   56   40   (plus one guard sample: a copy of the first)

The engine plays level L while the pitch times its harmonic count stays below 20 kHz,
so nothing folds over. The engine interpolates linearly inside a level, which leaves
images of each harmonic h near (N - h) times the pitch at about
(1/h-amplitude) * sin^2(pi h/N) / (pi^2 (1 - h/N)^2); the sample counts N are the
smallest multiples of 8 that keep those of a saw below -63 dB. They aren't powers of
two: the engine scales the phase by N.

Each frame is scaled so its RMS is the same (and no level clips), so the level hardly
changes along a bank or from one bank to another.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

FRAMES = 32
HARMONICS = 64
LEVELS = [(64, 352), (32, 240), (16, 168), (8, 120), (4, 80), (2, 56), (1, 40)]   # (harmonics, samples)
TARGET_RMS = 9000.0
PEAK = 32000


def additive(amps: np.ndarray, phases: np.ndarray | None = None) -> np.ndarray:
    """Harmonics 1..64 as complex amplitudes (sine phase)."""
    ph = np.zeros(HARMONICS) if phases is None else phases
    return amps * np.exp(1j * (ph - math.pi / 2))


def from_wave(wave: np.ndarray) -> np.ndarray:
    """Harmonics 1..64 of one period of a waveform (any length)."""
    spec = np.fft.rfft(wave) / (len(wave) / 2)
    return spec[1:HARMONICS + 1]


H = np.arange(1, HARMONICS + 1)
SINE = (H == 1).astype(float)
TRIANGLE = np.where(H % 2 == 1, (-1.0) ** ((H - 1) // 2) / H ** 2, 0.0)
SAW = 1.0 / H
SQUARE = np.where(H % 2 == 1, 1.0 / H, 0.0)


def morph(keys: list[np.ndarray], t: float) -> np.ndarray:
    """Amplitudes blended linearly between key spectra spread evenly over t = 0..1."""
    pos = t * (len(keys) - 1)
    k = min(int(pos), len(keys) - 2)
    m = pos - k
    return keys[k] * (1 - m) + keys[k + 1] * m


def bank_basic(t: float) -> np.ndarray:
    """Sine -> triangle (t = 1/3) -> saw (2/3) -> square."""
    return additive(morph([SINE, TRIANGLE, SAW, SQUARE], t))


def bank_harmonic(t: float) -> np.ndarray:
    """Harmonics added one after another (each fading in) at 1/sqrt(h): from a sine to 32
    harmonics."""
    n = 1 + t * 31
    return additive(np.clip(n - (H - 1), 0.0, 1.0) / np.sqrt(H))


def bank_resonant(t: float) -> np.ndarray:
    """A saw with a resonant peak (about +20 dB) sweeping from the 1st to the 48th harmonic."""
    p = 1 + t * 47
    return additive(SAW * (1 + 10.0 / (1 + ((H - p) / 1.2) ** 2)))


def bank_pwm(t: float) -> np.ndarray:
    """A pulse whose width narrows from 50 % to 3 %."""
    width = 0.5 - 0.47 * t
    x = (np.arange(4096) + 0.5) / 4096
    return from_wave(np.where(x < width, 1.0, -1.0) - (2 * width - 1))


def bank_formant(t: float) -> np.ndarray:
    """Vowel-like spectra: a saw-like source through two formants that move a-e-i-o-u."""
    vowels = [(800, 1200), (500, 1900), (300, 2300), (500, 900), (330, 700)]   # F1, F2 at 110 Hz
    pos = t * (len(vowels) - 1)
    k = min(int(pos), len(vowels) - 2)
    m = pos - k
    f1 = vowels[k][0] * (1 - m) + vowels[k + 1][0] * m
    f2 = vowels[k][1] * (1 - m) + vowels[k + 1][1] * m
    f = H * 110.0
    env = 1.0 / (1 + ((f - f1) / 90) ** 2) + 0.6 / (1 + ((f - f2) / 130) ** 2) + 0.02
    return additive(env / np.sqrt(H))


def bank_sync(t: float) -> np.ndarray:
    """A hard-synced saw: the slave's ratio sweeps from 1 to 6.5 (ending between whole
    ratios: at a whole ratio n a synced saw is a saw n times higher, with nothing below)."""
    ratio = 1 + 5.5 * t
    x = (np.arange(4096) + 0.5) / 4096
    return from_wave(2 * ((x * ratio) % 1.0) - 1)


def bank_digital(t: float) -> np.ndarray:
    """Two-operator FM, the modulator at twice the carrier, its index rising 0.3 -> 7."""
    index = 0.3 + 6.7 * t
    x = 2 * np.pi * (np.arange(4096) + 0.5) / 4096
    return from_wave(np.sin(x + index * np.sin(2 * x)))


# in the order PW -0.5..0.5 selects them; the engine's super saw takes the eighth slot,
# so PW 0 (slot 4) is Basic
BANKS = [("Formant", bank_formant), ("Resonant", bank_resonant), ("Harmonic", bank_harmonic),
         ("PWM", bank_pwm), ("Basic", bank_basic), ("Sync", bank_sync), ("Digital", bank_digital)]


def render(spec: np.ndarray, harmonics: int, samples: int) -> np.ndarray:
    full = np.zeros(samples // 2 + 1, dtype=complex)
    full[1:harmonics + 1] = spec[:harmonics]
    return np.fft.irfft(full, n=samples) * (samples / 2)


def frame_levels(spec: np.ndarray) -> list[np.ndarray]:
    lv = [render(spec, h, n) for h, n in LEVELS]
    rms = math.sqrt(float(np.mean(lv[0] ** 2)))
    scale = TARGET_RMS / max(rms, 1e-9)
    peak = max(float(np.abs(x).max()) for x in lv) * scale
    if peak > PEAK:
        scale *= PEAK / peak
    return [np.round(x * scale).astype(np.int16) for x in lv]


def frame_data(spec: np.ndarray) -> list[int]:
    data: list[int] = []
    for x in frame_levels(spec):
        data += [int(v) for v in x] + [int(x[0])]      # guard sample
    return data


def c_array(name: str, size: str, data: list[int]) -> list[str]:
    lines = [f"static const int16_t {name}[{size}] = {{"]
    for i in range(0, len(data), 24):
        lines.append("    " + ",".join(str(v) for v in data[i:i + 24]) + ",")
    return lines + ["};"]


def main() -> int:
    out = Path(sys.argv[1])
    data: list[int] = []
    for _, fn in BANKS:
        for f in range(FRAMES):
            data += frame_data(fn(f / (FRAMES - 1)))
    saw = frame_data(additive(SAW))
    offs, o = [], 0
    for _, n in LEVELS:
        offs.append(o)
        o += n + 1
    lines = [
        "/* Generated by patch/synths/gen_wavetables.py: do not edit. */",
        f"#define WT_BANKS {len(BANKS)}",
        f"#define WT_FRAMES {FRAMES}",
        f"#define WT_LEVELS {len(LEVELS)}",
        f"#define WT_FRAME_STRIDE {o}          /* samples per frame, all levels with their guard samples */",
        f"/* banks: {', '.join(name for name, _ in BANKS)} */",
        "static const uint16_t wt_level_len[WT_LEVELS] = {%s};" % ", ".join(str(n) for _, n in LEVELS),
        "static const uint16_t wt_level_off[WT_LEVELS] = {%s};" % ", ".join(str(x) for x in offs),
        "static const float wt_level_harm[WT_LEVELS] = {%s};" % ", ".join(f"{h}.0f" for h, _ in LEVELS),
    ]
    lines += c_array("wt_data", "WT_BANKS * WT_FRAMES * WT_FRAME_STRIDE", data)
    lines += ["/* the super saw's saw: one frame, all levels */"]
    lines += c_array("wt_saw", "WT_FRAME_STRIDE", saw)
    out.write_text("\n".join(lines) + "\n")
    print(f"{out}: {len(BANKS)} banks ({', '.join(name for name, _ in BANKS)}) and a saw, "
          f"{(len(data) + len(saw)) * 2} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
