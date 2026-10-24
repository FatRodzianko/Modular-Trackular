# synths: WAVE, a wavetable synth in place of one synth model

A patch for Polyend Tracker+ firmware 1.2.0. It adds **WAVE**, a wavetable synth after the
PPG Wave and Ensoniq ESQ-1: digital wavetable oscillators through analog-style filters.
Per project, WAVE takes the place of one of the five synth models (ACD, FAT, VAP, WTFM or
PERC):
- the Instrument screen's Model list shows WAVE where that model was;
- the instrument title reads "Synth 1 (WAVE)";
- the synth pages show WAVE's own controls.

What WAVE has:
- **Two oscillators.** Each plays one of 9 waves:
  - 7 wavetable banks of 32 frames, morphing between frames: Formant, Resonant,
    Harmonic, Square/PWM, Sin/Tri/Saw/Sqr, Sync and FM, all generated from formulas;
  - a super saw;
  - a plain saw.
- **Digital mode**, which plays the banks the way early digital wavetable synths did: the
  frame steps instead of morphing, the samples are 8-bit, and there is no interpolation.
- **Filters:**
  - a 24 dB transistor-ladder low-pass that self-oscillates;
  - a saturating state-variable filter (OB), 12 or 24 dB;
  - a clean state-variable filter (SVF), 12 or 24 dB.

  Each comes as low-pass, high-pass, band-pass or notch, with Drive: saturation into the
  filter.
- **Three ADSR envelopes** (amp, filter and mod), **two LFOs** (free or tempo-synced,
  bipolar or unipolar), and a 6-slot **Mod Matrix**. The matrix can modulate Position, Pitch, Mix, Cutoff,
  Resonance, Volume and Noise.
- Glide and noise.
- **An effect per synth:** None or a stereo **Chorus** after the Roland Juno's, with Rate,
  Depth and Mix.

Installed on its own, Config shows the firmware version as `1.2.0-sy7`.

**Status:** `sy7` was tested on hardware, alone and in the all bundle: it installs and
plays, and the effect works. It is `sy6` with the effect. With Effect None (the default,
and what every patch saved before `sy7` loads with) its output is `sy6`'s on both sides. `sy6` was tested on hardware, alone and in the all bundle: it
installs; `sy5` patches load with Drive at 0 % and sound the same; Drive changes the sound
audibly and stays under control with Resonance above 50. It is `sy5` with Drive; with
Drive at 0 it plays sample for sample as `sy5`, which was tested on hardware, alone and in
the all bundle.
`sy5` is `sy4` with the filter envelope on the
Edit Patch screen's Env 1 tile (as VAP's) and the mod envelope on Env 2; the sound and
the patch files are unchanged. `sy4` was tested on hardware, alone and bundled with all
the other patches: it installs and plays; `sy3` patches load and play; the LFOs' Polarity
switches show on the Edit Patch screen and work. With 8 WAVE voices playing, an LFO
moving Position and full reverb and delay sends, the CPU use peaked at 56 %.
Earlier builds were tested on hardware:
- `sy3` (this one without the LFOs' Polarity switches): the sound and the controls.
- `sy2` (also without the LFOs, the Mod Matrix and the Saw): swapping, and saving and
  loading WAVE patches across projects.

The first build (`sy1`, a different design) was replaced; see
[SYNTH_ENGINES_PLAN.md](../../docs/SYNTH_ENGINES_PLAN.md).

## Using it

1. Config → Project Settings → **Synth Engines** → **WAVE replaces**: Off (the default),
   ACD, FAT, VAP, WTFM or PERC.
2. On the Instrument screen, pick WAVE in a synth's Model list (where the replaced model
   was).

Synths already set to the replaced model become WAVE at once and start from WAVE's Init
patch. Setting "WAVE replaces" back to Off gives them the stock model again, from its Init
patch. While WAVE replaces a model, that model isn't available in the project.

The choice is saved per project (in `synths.bin` in the project's folder), a moment after
you change it, and Save As takes it along:
- A project without that file plays the stock models.
- Every project plays the stock models in stock firmware. A synth that was WAVE plays
  the replaced model's Init patch there (the project keeps its other settings).

WAVE's patches save and load like any model's, in `Patches/WAVE/`. The Edit Patch screen
shows "WAVE" where the model's logo would be. A WAVE synth stays silent until the first
Pattern screen after a start.

### The controls

The synth pages have VAP's tabs, and WAVE's controls sit where VAP's do. On the Edit Patch
screen, the tiles open them as lists:

| Tile | Controls |
|---|---|
| Osc | Wave, Position, Mix, Tune, Detune, Finetune, Noise, Digital, Reset |
| Filter | Cutoff, Resonance, Type, Env Amt, Key Track, Drive |
| Amp Env | The amp envelope |
| Env 1 | The filter envelope (as on VAP) |
| Env 2 | The mod envelope |
| LFO 1, LFO 2 | Sync, Ratio, Waveform, Retrigger, Polarity |
| Mod | The Mod Matrix |
| Voice | Glide Mode, Glide Time, Volume, Effect, Rate, Depth, Mix |

**Oscillator (1/2):**

| | Oscillator 1 | Oscillator 2 | Both |
|---|---|---|---|
| | Wave 1 | Wave 2 | Mix (−1 oscillator 1 only, 0 both, +1 oscillator 2 only) |
| | Position 1 | Position 2 | Digital (Off / On) |
| | Tune 1 (±36 st) | Tune 2 (±36 st) | Detune (oscillator 2 up, 0–100 c) |

**Oscillator (2/2):** Reset 1 and Reset 2 (each note starts the oscillator at phase 0),
Noise (white noise into the filter), and Finetune (both oscillators, ±100 c).

**Filter:** Cutoff, Resonance (0–150 %), Type, Env Amt (±100 % is ±8 octaves), Key Track
(cutoff follows the note around middle C; 100 % is full tracking), and Drive (0–100 %:
saturation into the filter, up to +24 dB into a soft clip, with the level kept about the
same; 0 is off).

**Modulation:**
- Amp Env.
- Flt Env/LFO 1: the filter envelope, and LFO 1.
- Mod Env/LFO 2: the mod envelope, and LFO 2.

Each envelope has Attack, Decay, Sustain and Release. Attack is the time to full level;
Decay and Release are the time to fall 60 dB.

Each LFO has:
- **Sync:** Off, and Ratio is the rate in Hz (0–100). On, and Ratio picks a division of the
  tempo (1, 1/2 D, 1/2, 1/4 D, 1 T, 1/4, 1/8 D, 1/2 T, 1/8, 1/16 D, 1/4 T, 1/16, 1/8 T,
  1/32, 1/16 T, 1/32 T): one cycle per division.
- **Waveform:** Triangle, Sin, Ramp Up, Ramp Down, Square, Random S&H.
- **Polarity:** Bipolar (−1 to 1, the default) or Unipolar (0 to 1). On the Edit Patch
  screen it is the last item of the LFO 1 and LFO 2 pages, after Retrigger.
- **Retrigger:**
  - Note: each note starts the LFO from the beginning.
  - One Shot: the same, but the LFO runs one cycle, then holds its last value.
  - Free Running: the LFO never restarts.

  Each voice has its own LFOs.

**Matrix (1/2, 2/2):** 6 slots of Src, Dest and Amt (±400 %). Each slot adds Amt × the
source to the destination.

| Src | Range |
|---|---|
| Amp Env, Filter Env, Mod Env | 0 to 1 |
| LFO 1, LFO 2 | −1 to 1 (Bipolar), or 0 to 1 (Unipolar) |
| Velocity | 0 to 1 |
| Key | The note against middle C: ±1 over 5 octaves |

| Dest | At Amt 100 % |
|---|---|
| Position, Position 1, Position 2 | The whole range (0 → 1) |
| Pitch, Pitch 1, Pitch 2 | An octave |
| Mix | Half its range |
| Cutoff | 4 octaves |
| Resonance | 100 % |
| Volume | The level × (1 + the source) |
| Noise | Full noise |

Each destination stops at the ends of its range. So from Position 0, a Bipolar LFO at
50 % swings −50 % to +50 %, and Position rests at 0 for half of each cycle. A Unipolar LFO
at 50 % sweeps 0 to 50 % and back.

For example:
- LFO 1 → Position at 20 % gives a slow wavetable sweep around where Position sits.
- LFO 1, Unipolar, → Position at 50 % from Position 0 sweeps the first half of the table.
- Mod Env → Position at 100 % sweeps the wave once per note.
- LFO 2 → Pitch at 2 % gives vibrato.

**Voice:** Glide Mode, Glide Time (seconds for the whole glide), Volume (set from velocity).
The glide modes are:
- Always: every note glides from the last.
- Overlap: only a note played while the last is held glides.
- Legato: as Always, and a note played while the last is held doesn't restart the
  envelopes.
- Legato O.: both, only while the last is held.

The effect follows on the same page and tab:
- **Effect:** None (the default) or Chorus. It works on the synth's whole output, after
  all its voices, and makes the synth stereo.
- **Rate:** how fast the chorus moves, 0.05–5 Hz (0.50 Hz at the default, 50 %).
- **Depth:** how far it moves. 50 % (the default) is about the Juno's Chorus I, 100 %
  its Chorus II.
- **Mix:** 0 % is dry, 100 % the chorus alone; at 50 % (the default) the two are equal.
  The loudness stays about the same at any setting.

Switching the effect on or off fades it in or out over about 60 ms.

**FX:** the stock instrument effects.

**Wave** picks what the oscillator plays:

| Wave | Position 0 → 1 |
|---|---|
| Formant | A vowel-like "a", through e, i and o, to "u" (two formants on a bright source) |
| Resonant | A saw with a resonant peak swept from its fundamental to the 48th harmonic |
| Harmonic | A sine, with harmonics added one at a time up to 32: organ-like, then bright |
| Square/PWM | A square, narrowing to a pulse 3 % wide |
| Sin/Tri/Saw/Sqr | A sine, a triangle (1/3), a saw (2/3), a square (1) |
| Sync | A saw, to a hard-synced saw whose slave runs 6.5 times faster |
| FM | Mild two-operator FM, to bright, bell-like FM |
| Super Saw | 7 saws in unison (0), spreading to the full detune (1), finer near 0 |
| Saw | A plain saw (Position does nothing) |

The super saw's 7 saws are one on the note and six around it, at 0.6 of its level. Their
relative detunes follow published analyses of the Roland JP-8000's super saw.

The tables are band-limited at every pitch. The highest notes keep only the lowest
harmonics, so frames whose sound sits in high harmonics (some Sync, Resonant and Formant
frames) get quieter at the very top of the range.

**Type:**

| Type | Filter |
|---|---|
| LP Ladder 24 | The transistor ladder: 24 dB, self-oscillating near 150 % |
| LP OB 24, LP OB 12, HP OB 24, HP OB 12, BP OB 24, BP OB 12 | The saturating SVF: a loud resonance compresses |
| LP SVF 24, LP SVF 12, HP SVF 24, HP SVF 12, BP SVF 24, BP SVF 12, Notch 24, Notch 12 | The clean SVF |

For OB and SVF, a Resonance of about 44 % gives a flat response.

**Starting points:**

| Sound | Wave | Position | Other |
|---|---|---|---|
| Saw | Saw | | Or Sin/Tri/Saw/Sqr at 0.67 |
| Square | Square/PWM | 0 | Or Sin/Tri/Saw/Sqr at 1 |
| PWM square | Square/PWM | 0.3 | Matrix: LFO 1 → Position, Amt 30 % |
| Super saw | Super Saw | 0.4–0.8 | Both oscillators on it for 14 saws |
| PPG-like | Any bank | Sweep it | Digital On, LP Ladder 24, some Resonance, Mod Env → Position |

**The Init patch:**
- two Sin/Tri/Saw/Sqr saws, slightly detuned;
- the ladder filter at 2.4 kHz with low resonance;
- a sustained amp envelope and a short filter envelope.

Its first two matrix slots are routings to try: LFO 1 (a 1 Hz triangle) → Position, and
Mod Env (a 0.5 s rise and a 1 s fall) → Position. Both start at Amt 0, so raise the Amt
to hear them. LFO 2 is a 0.25 Hz sine.

**The Macros:**
- a: Attack;
- b: Decay/Release on the amp and filter envelopes;
- c: Wave Position (Position 1 and 2);
- d: Filter Cutoff;
- e: Filter Resonance;
- f: Filter Env Amount;
- Velocity and Pressure as on the stock models.

The pitch bend range is ±2 semitones.

## How it works

The firmware keeps each model in a registry (FINDINGS section 23). A model's entry has
three parts:
- its name;
- a builder for its patch: parameters, value displays, screen layout and Macros;
- a factory for its voices.

A project's slot records store a model index (0–4) that a small table maps to a model id.
The patch works with those pieces:

- **WAVE is model 3**, an id no stock model uses. A hook on the registry's builder adds
  WAVE's entry at start-up. Because WAVE has an id of its own, stock firmware rejects a
  WAVE slot's saved patch and plays the replaced model's Init patch. Borrowing ACD's id
  would make stock firmware load WAVE's values into ACD, and that played silence.
- **WAVE's builder** runs VAP's builder, then rewrites the patch it made:
  - the parameter names, ranges and value displays;
  - the screen layout and the Edit Patch screen's pages;
  - the Macros (through the container's Macro-defaults function, which the firmware
    calls once the Macros exist);
  - the Init values and the model id;
  - six parameters of WAVE's own after VAP's 60: the LFOs' Polarity (60, 61) and the
    effect's Effect, Rate, Depth and Mix (62–65);
  - two output channels instead of one, so the synth plays in stereo (as PERC does).

  VAP's 60 parameters stay where VAP has them, so patch files keep their positions. If
  anything about VAP's patch isn't as expected, the builder leaves it VAP's.
- **WAVE's own parameters** (60–65) are added with the firmware's own vector code:
  - The firmware sizes each of a patch's per-parameter arrays from the count the builder
    leaves. VAP's builder sets the count to 60 first on every build, and WAVE's then adds
    its six, so a WAVE patch has 66 every time.
  - A new patch's vector is made 66 long before VAP's builder runs. VAP's builder trims it
    to 60 and keeps the room, so adding the six needs no new allocation. Growing VAP's 60
    to 66 would double the vector's room to 120 entries (6.7 KB per WAVE patch).
  - Patch files store values by position, and loading sets as many as the file holds.
    So a hook on the patch storage's load sets all six to their defaults (Bipolar, Effect
    None) before a WAVE patch loads: a patch saved without them (`sy3`: 60 values;
    `sy4`–`sy6`: 62) loads as it played. If the load fails, they go back to what they
    were.
  - An older build loading a patch saved with more of them ignores the extra values.
- **The swap** sets the table entry of the replaced index to 3:
  - At project load, the patch reads `synths.bin` before the stock code applies the slot
    records. So the stock code gives each slot its model, then loads its patch, as the
    project had them.
  - A change in the menu takes effect at the next Config screen update.
  - Records keep stock indices.
- **The Model list** draws model logos by image id. With a swap, the patch hands the list
  its own id table with the replaced position marked. The list draws the other four
  logos, and the patch draws "WAVE" as text in the marked row.
- **The Edit Patch screen** shows the model's logo in a label, picked from a table by the
  model index. For a WAVE synth, the patch clears the label's logo and gives it the text
  "WAVE".
- **WAVE's voices are VAP voice objects** (VAP's factory). Six entries of the VAP voice
  vtable (`0x600667e4`) are hooked, each chained to the stock one:

  | Entry | A WAVE voice | Any other voice |
  |---|---|---|
  | `+0x04` delete | Frees its engine voice, then VAP's | VAP's |
  | `+0x08` MIDI | Notes, note offs and bend to the engine, then always to VAP (its note bookkeeping) | VAP's |
  | `+0x0c` set parameter | Into the slot's parameter copy only: WAVE's values don't fit VAP's ranges | VAP's |
  | `+0x10` render | The engine renders 128 samples (silence before the unit has booted) | VAP's |
  | `+0x14` after the voices | Voice 0's, once per block with the synth's summed output: the effect, which writes both sides | VAP's (nothing) |
  | `+0x18` sounding | The engine's amp envelope | VAP's |

  The hooks are naked trampolines. For any other voice, the jump to VAP's method leaves
  every register and stack word as it was (the render call has five stack arguments).
- **The engine's voices:** up to 8, one per WAVE voice. That is the stock limit for all
  three synths together. A slot with more voices (a loaded project can set that) keeps
  the extra ones silent until an engine voice is free.
- **Start-up.** Running heavy DSP while the unit boots hung it before
  ([FINDINGS.md](../../docs/FINDINGS.md) section 19). So a WAVE voice renders silence until
  the Pattern screen's update has run once.
- **The effect's memory** (6,288 bytes: a delay line and the state for each of the three
  synths) is a second RAM block that the patcher gives out after every patch's own
  ("late_ram" in `patch.json`). In the all bundle it is in SDRAM. SDRAM may not be ready
  before the firmware's `main` runs, so the block is only touched once the unit has
  booted. It holds garbage at power-on: the first block after a start clears it and takes
  each synth's effect values from its patch.

**The engine** (`wt_engine.h`) renders mono, 128 samples per block, into the left buffer, as
VAP does:
- **Oscillators:** per block, the band-limited level and the wave. Per sample, linear
  interpolation within the frame and between two frames, with the position ramped across
  the block. Digital mode takes the nearest frame and the nearest sample, and drops each
  sample's low 8 bits. The super saw plays one saw table at 7 phase increments.
- **Filters:** all topology-preserving, and set per block.
  - SVF: a state-variable filter, stable at any setting. The 24 dB types put an
    unresonant stage before it.
  - OB: the same, with its band-pass state through a soft clip.
  - Ladder: four one-pole low-passes in a feedback loop, solved for each sample, with
    its input through a soft clip, which bounds the self-oscillation.
  - Drive: before any of them, the input times 1 to 16 through a soft clip, then scaled so
    a held note at full velocity keeps its peak. At 0 the stage isn't run. It costs
    about 2,200 instructions per voice per block. It uses VAP's parameter 38 (Mix Env
    Amt), which WAVE never showed and every WAVE patch holds as 0, so saved patches play
    as before.
- **Envelopes:** three, advanced per block; the amp envelope is ramped per sample.
- **LFOs:** two per voice, advanced per block. A synced LFO takes its rate from the
  tempo. A Free Running LFO also runs while its voice is silent.
- **The Mod Matrix:** per block, each slot's Amt × its source is added to its
  destination, and the sums are applied: offsets to Position and Mix, semitones to
  Pitch, octaves to Cutoff, a factor to Volume.
- **Start phases:** each voice's oscillators start at a random phase. Saws that start
  together peak together, and a chord would clip.

**The effect** (`wt_fx.h`) runs once per block on the synth's output, after all its
voices. With Effect None it copies the left side to the right. The chorus writes each
sample into a delay line of 512 samples and reads it at two points, one per side. A
triangle LFO moves them in opposite directions around 3.5 ms; Depth 100 % swings them
±1.85 ms (1.65–5.35 ms). Each point is read with linear interpolation and goes through a
one-pole low-pass at 9 kHz, as the Juno's bucket-brigade delay dulls its copy. Mix
crossfades the dry signal and the delayed one, each scaled by
1 / √((1 − Mix)² + Mix²), so the loudness of a full sound stays the same. Mix and Depth
move at most 5 % per block, and the delay line starts empty each time the chorus starts.

Every parameter is clamped (NaN counts as the bottom of the range), and the output is
limited to ±2. The output level is set so a four-note chord of the Init patch at full
velocity peaks at 0.74–0.88 of full scale, depending on the voices' random start
phases. The slot itself clips at 0.998, and `sy1`
played about 8 dB louder.

**The tables** come from `gen_wavetables.py`: additive spectra, or a waveform's FFT spectrum
cut at harmonic 64. There are no third-party tables. Each frame is stored at 7 levels,
from 64 harmonics down to 1, with enough samples per level that a saw's interpolation
images stay below −63 dB. Each bank of 32 frames takes 66.5 KB of flash.

**Costs** (emulator, instructions per voice per 128-sample block):

| | Idle | Playing |
|---|---|---|
| Stock VAP voice | 60,804 | 66,195 |
| WAVE voice | 517 | 7,185 (Digital mode) to 19,413 (OB 24 dB and noise); 15,303 with a ladder |
| WAVE voice, both oscillators on the super saw | 517 | 46,219 (the ladder) to 50,329 (OB 24 dB and noise) |

An idle WAVE voice still runs its LFOs. Each Mod Matrix slot in use adds about 300.
A WAVE voice is also a VAP voice object: 6.5 KB of heap, as VAP.

The effect costs about 840 instructions per synth per block with Effect None (the copy to
the right side) and 8,900 with the Chorus, once per synth, not per voice.

**Sizes:**
- RAM: 2,064 bytes of on-chip patch RAM, for the settings, each slot's parameter copy and
  8 engine voices; and 6,288 bytes for the effect (on-chip on its own, SDRAM in the all
  bundle).
- Flash: 483 KB, 467 KB of it the seven banks and the saw table (Super Saw and Saw).
- The `.ptf` grows by about 1.3 MB (Intel HEX).
- With all seven patches here bundled, the image ends at `0x6023c11c`, 2.2 MB into the
  flash (stock: 1.7 MB). Images reaching 8 MB into the flash install and run on hardware
  ([FINDINGS.md](../../docs/FINDINGS.md) section 8).

**Shared hooks.** The Project Settings folder, the Config menu constructor's last item, and
the Pattern and Config screen update methods are chained, as other patches here chain
them. So are the registry builder, the project's slot records, the Model list's data,
the list control's image draw, the Edit Patch label's text and the patch storage's
load. So this patch combines with all of the others.

## Build

No compiler needed: the compiled patch is in `prebuilt/`. See
[docs/PATCHING.md](../../docs/PATCHING.md) for getting and checking the stock firmware.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/synths/patch.json build/TrackerPlus_sy7.ptf
```

To combine it with other patches, list `../synths/patch.json` in a bundle (see "Choosing
patches" in [docs/PATCHING.md](../../docs/PATCHING.md)).

To build the patch code from source, you need the Arm GNU Toolchain (`arm-none-eabi-gcc` on
`PATH`, or `ARM_GCC_BIN` set to its `bin` folder) and Python with numpy (for the tables):

```bash
patch/synths/build.sh
```

### Emulator test

Needs `pip install unicorn capstone numpy`:

```bash
python tools/emu/test_synths.py build/TrackerPlus_sy7.ptf path/to/TrackerPlus_1.2.0.ptf [wav folder] [--stress 20000]
```

It runs the firmware's own synth system: host, slots, VAP voices and the audio graph
(`tools/emu/synth_rig.py`). It checks:

- **Stock:** with WAVE off, or replacing a model no synth uses, the output is identical
  to stock firmware, sample for sample. With WAVE replacing ACD, the VAP synths still
  are.
- **The WAVE model:**
  - model id 3;
  - its names, ranges, value displays, Init values, screen layout, Macro pages and
    Macros (all on parameters the engine reads);
  - no heap growth over 50 model switches;
  - the title "Synth 1 (WAVE)".
- **The swap:**
  - the replaced model's synths become WAVE, and Off brings them back;
  - no WAVE parameter reaches VAP;
  - the audio interrupt stays off inside the stock code's critical sections;
  - no heap growth over 200 swaps with notes held.
- **Menu, Model list, Edit Patch and file:**
  - the menu item;
  - "WAVE" drawn where the replaced logo was, scrolled too;
  - on the Edit Patch screen, "WAVE" for a WAVE synth and no logo; other models keep
    theirs;
  - `synths.bin` read at project load, before the slot records apply, and saved a moment
    after a change;
  - damaged and `sy1` files count as Off and are kept;
  - Save As takes the choice along;
  - stock firmware never opens the file.
- **Patch files:**
  - a WAVE patch saves to `WAVE/<name>.patch` with model id 3 and its 66 values, and
    loads back, the Polarity switches and the effect included;
  - a file of 60 values (as `sy3` saved them) loads with both switches Bipolar and Effect
    None, and one of 62 (`sy4`–`sy6`) with Effect None;
  - a load that fails leaves them as they were;
  - stock firmware rejects a WAVE patch, and the ACD Init patch plays.
- **WAVE's own parameters:** 66 parameters, and 66 entries in each of the patch's
  per-parameter arrays, also after a rebuild; their ranges, displays and flags; two
  output channels.
- **Oscillators, filters, super saw, Saw, Digital mode and envelopes:**
  - pitch within 1 cent;
  - no aliasing above −60 dB on a saw at notes 96–108;
  - each filter's response, slopes and resonance;
  - the ladder's self-oscillation, and the OB filter's saturation;
  - the super saw's seven partials;
  - the Saw's harmonics, unchanged by Position;
  - Digital mode's steps;
  - envelope times within 5 %;
  - glide.
- **LFOs:** each waveform's shape, the rate in Hz and synced to the tempo, the three
  Retrigger modes, Random S&H holding within each cycle, and LFO 2 as LFO 1.
- **Polarity:** Unipolar gives 0 to 1 (a square steps between 0 and the full amount;
  Random S&H stays above 0), each LFO its own. LFO 1 Unipolar → Position at 50 % from
  Position 0 sweeps 0 to 0.5 without resting at 0; Bipolar rests there half the time.
- **Mod Matrix:** LFO 1 → Position, the Mod Env, Volume, Velocity → Cutoff and Key →
  Pitch 1 by their amounts; the Init patch's routings silent at Amt 0; every source to
  every destination at ±400 % finite and bounded.
- **The Init patch's level:** one note at about −22 dBFS, and a full-velocity four-note
  chord below the slot's clip.
- **Voice allocation:** the same voice for every note as stock VAP.
- **Banks:** every table band-limited; the 9 waves within 3 dB of each other.
- **More voices, Macros, export.**
- **The effect:**
  - Effect None: the right side a copy of the left, sample for sample; Mix 0: dry;
  - the chorus's two delays, measured from clicks: around 3.5 ms, moving in opposite
    directions by Depth × 1.85 ms at Rate;
  - switching it on and off fades, with no step;
  - nothing touched in its RAM before boot; cleared after a start, whatever the RAM held,
    with each synth's values from its patch;
  - through the whole synth output with 4 voices: the loudness within 1.5 dB of dry, the
    two sides different;
  - its cost.
- **Stress:** random notes, parameters (garbage too, the effect's included), swaps,
  Model list picks and restarts, with a watch on every memory write and the effect's
  state checked.

With a folder given, it also writes WAV files of test phrases:
- VAP's Init patch, and WAVE's ending on a chord;
- a position sweep through each wave, and the Saw;
- a ladder filter sweep;
- a pad;
- super saw chords;
- a resonant sweep in Digital mode;
- chords dry, then with the chorus at its defaults, then at Depth 100 % (stereo).

## Install and revert

Install as described in [docs/PATCHING.md](../../docs/PATCHING.md): copy the patched `.ptf` to
`/Firmware` on the SD card next to the stock `TrackerPlus_1.2.0.ptf`, then Config →
Firmware → the patched file. Config should show `1.2.0-sy7 Build 1504`.

To revert: Config → Firmware → `TrackerPlus_1.2.0.ptf`.
- Synths that were WAVE play the replaced model's Init patch.
- WAVE patches stay in `Patches/WAVE/`, and the projects' `synths.bin` files stay in their
  folders. Stock firmware doesn't read either (delete them if you like).

If the unit freezes, pull the power, boot, and revert through Config. If it doesn't boot, or
Config itself doesn't work:
1. Put only the stock `TrackerPlus_1.2.0.ptf` in `/Firmware` (from a computer).
2. Power on with the card in.
3. Press the pinhole button next to the USB-C port.

The bootloader reinstalls the stock firmware.
