# synths: WAVE and PHASE, two synths in place of synth models

A patch for Polyend Tracker+ firmware 1.2.0. It adds two synths:
- **WAVE**, a wavetable synth after the PPG Wave and Ensoniq ESQ-1: digital wavetable
  oscillators through analog-style filters;
- **PHASE**, a phase distortion synth after the Casio CZ series (see [PHASE](#phase)).

Per project, each takes the place of one of the five synth models (ACD, FAT, VAP, WTFM or
PERC), and both can run at once on different models:
- the Instrument screen's Model list shows WAVE or PHASE where that model was;
- the instrument title reads "Synth 1 (WAVE)" or "Synth 1 (PHASE)";
- the synth pages show the synth's own controls.

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

Installed on its own, Config shows the firmware version as `1.2.0-sy9`.

**Status:**
- **WAVE** is tested on hardware, alone and in the all bundle (`sy7`, which had WAVE
  only). With 8 WAVE voices playing, an LFO moving Position and full reverb and delay
  sends, the CPU use peaked at 56 %. In `sy8`, WAVE's model and output are the same as
  `sy7`'s, sample for sample, in the emulator. WAVE patches and projects from `sy3` on
  load and play.
- **PHASE** is new in `sy8`, tested on hardware alone and in the all bundle.
- **Save As over an existing project** (`sy9`): the saved project's choices now win
  over the overwritten project's `synths.bin`. Tested on hardware, alone and in the all
  bundle. Otherwise `sy9` is `sy8`.

The design of PHASE is in [PD_SYNTH_PLAN.md](../../docs/PD_SYNTH_PLAN.md), WAVE's in
[SYNTH_ENGINES_PLAN.md](../../docs/SYNTH_ENGINES_PLAN.md).

## Using it

1. Config → Project Settings → **Synth Engines** → **WAVE replaces** and **PHASE
   replaces**: each Off (the default), ACD, FAT, VAP, WTFM or PERC.
2. On the Instrument screen, pick WAVE or PHASE in a synth's Model list (where the
   replaced model was).

Synths already set to the replaced model become WAVE or PHASE at once and start from its
Init patch. Setting the item back to Off gives them the stock model again, from its Init
patch. While a model is replaced, it isn't available in the project.

The two items can't replace the same model: each item's list leaves out the model the
other one replaces. (A project file naming one model in both loads with PHASE Off.)

The choices are saved per project (in `synths.bin` in the project's folder), a moment
after you change them, and Save As takes them along: the project goes on with them under
its new name, and they are written to its new folder (also on a new project's first save,
and on Save As over another project, whose old choices they replace):
- A project without that file plays the stock models.
- Every project plays the stock models in stock firmware. A synth that was WAVE or PHASE
  plays the replaced model's Init patch there (the project keeps its other settings).
- `sy7` (WAVE only) should read a file with PHASE set as damaged (from its code; not
  tested): it plays the stock models, WAVE too, and keeps the file unchanged, so going
  back to `sy8` or later restores both choices.

WAVE's patches save and load like any model's, in `Patches/WAVE/`, and PHASE's in
`Patches/PHASE/`. The Edit Patch screen shows "WAVE" or "PHASE" where the model's logo
would be. Both synths stay silent until the first Pattern screen after a start.

### WAVE's controls

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

## PHASE

PHASE is a phase distortion synth after the Casio CZ series. Each of its two DCOs reads a
cosine through a bent phase: with no bend it plays a sine, and the more the phase is
bent, the brighter the wave. **DCW** (the CZ's name, "digital controlled wave") sets
how far. Sweeping it, by hand or with the DCW envelope, does what a filter sweep does on
an analog synth.

What PHASE has:
- **Two DCOs.** Each plays one of 8 shapes: Saw, Square, Pulse, Double Sine, Saw-Pulse,
  and three resonant waves, Reso I, II and III. A DCO can also play two shapes on
  alternate cycles (a **pair**, as on the CZ), which adds a sub-octave and the CZ's
  hollow, reedy sounds.
- **Three ADSR envelopes:** DCA (the level), DCW (the timbre, with a depth per DCO) and
  DCO (the pitch, ±48 semitones).
- **Ring Mod** and **Noise Mod** on DCO 2, and Key Follow on the DCW.
- WAVE's **two LFOs**, its 6-slot **Mod Matrix** (with PHASE's own sources and
  destinations), glide, and its **Chorus**.

### PHASE's controls

The Edit Patch screen's tiles:

| Tile | Controls |
|---|---|
| DCO | Wave 1, Wave 1+, Wave 2, Wave 2+, Mix, Tune 1, Tune 2, Finetune, Detune, Reset 1, Reset 2, Ring Mod, Noise Mod |
| DCW | DCW 1, DCW 2, Env Amt 1, Env Amt 2, Key Follow |
| DCA | The DCA (level) envelope |
| DCW Env | The DCW (timbre) envelope |
| DCO Env | The DCO (pitch) envelope, and DCO Env Amt |
| LFO 1, LFO 2 | As WAVE's |
| Mod | The Mod Matrix |
| Voice | As WAVE's: Glide Mode, Glide Time, Volume, Effect, Rate, Depth, Mix |

The Synth Parameters screen's tabs are DCO, DCW, Modulation (DCA, DCW Env/LFO 1,
DCO Env/LFO 2), Matrix, FX and Voice.

**DCO:**
- **Wave 1, Wave 2:** each DCO's shape.
- **Wave 1+, Wave 2+:** Off (the default), or a second shape, played on every other
  cycle. As on the CZ, two resonant shapes can't pair: a Reso after a Reso plays as Off.
- **Mix:** −1 DCO 1 only, 0 both, +1 DCO 2 only.
- **Tune 1, Tune 2** (±36 st), **Finetune** (both DCOs, ±100 c), **Detune** (DCO 2 up,
  0–100 c).
- **Reset 1, Reset 2:** each note starts the DCO at phase 0.
- **Ring Mod** (Off / On): DCO 2 plays DCO 1 × DCO 2, and Mix crossfades DCO 1 and that
  product.
- **Noise Mod** (0–100 %): noise jitters DCO 2's pitch, a slight roughness at 10 %,
  noise at 100 %. DCO 1 is untouched.

**DCW:**
- **DCW 1, DCW 2** (0–100 %): each DCO's DCW. They keep VAP's MIDI CCs (74 and 71).
- **Env Amt 1, Env Amt 2** (±100 %): how far the DCW envelope moves each DCO's DCW. Two
  depths let one envelope move the two DCOs by different amounts, or opposite ways.
- **Key Follow** (±100 %): the DCW falls (or rises, below 0) with the note around middle
  C. At 100 % it falls by 100 % four octaves above.

**Envelopes:** Attack, Decay, Sustain and Release, as WAVE's. **DCO Env Amt** (±48 st)
sets how far the DCO envelope moves both DCOs' pitch. The CZ's pitch envelope only
rises, so 0 to +48 is the CZ's.

**Wave** (at DCW 0 and 100 %):

| Shape | DCW 0 | DCW 100 % |
|---|---|---|
| Saw | A sine | A saw-like wave |
| Square | A sine | A square-like wave (odd harmonics) |
| Pulse | A sine | A narrow pulse (10 % wide) |
| Double Sine | A sine | Two humps per cycle: the fundamental and the octave about equal |
| Saw-Pulse | A sine | A saw in the first half of each cycle, flat in the second |
| Reso I | A soft windowed sine | A resonance 24 times the note under a falling-saw window |
| Reso II | The same | The same, under a triangle window |
| Reso III | The same | The same, under a trapezoid window |

On the resonant shapes, DCW moves only the resonance, from the note up to 24 times it,
as on the CZ. To keep the top of the range clean, each shape is eased at high notes:
the bends are made at least 4 samples long, the resonance stays under 11 kHz, and the
Double Sine and Saw-Pulse are eased from about note 87 and 92.

**Matrix:**

| Src | Range |
|---|---|
| DCA Env, DCW Env, DCO Env | 0 to 1 |
| LFO 1, LFO 2 | −1 to 1 (Bipolar), or 0 to 1 (Unipolar) |
| Velocity | 0 to 1 |
| Key | The note against middle C: ±1 over 5 octaves |

| Dest | At Amt 100 % |
|---|---|
| Pitch, Pitch 1, Pitch 2 | An octave |
| DCW, DCW 1, DCW 2 | The whole range (0 → 100 %) |
| Mix | Half its range |
| Volume | The level × (1 + the source) |
| Detune | 100 cents more on DCO 2 |

**The Init patch:**
- two Saws at DCW 50 %, DCO 2 detuned by 8 cents;
- the DCW envelope at 30 % on each DCO (a 0.6 s decay to 30 %);
- a sustained DCA envelope.

Its first two matrix slots are routings to try, both at Amt 0: LFO 1 (a 1 Hz triangle)
→ Pitch, for vibrato, and LFO 2 (a 0.25 Hz sine) → DCW.

**The Macros:**
- a: Attack (the DCA's);
- b: Decay/Release on the DCA and DCW envelopes;
- c: DCW (DCW 1 and 2);
- d: DCW Env Amt (Env Amt 1 and 2);
- e: Detune;
- f: Mix;
- Velocity and Pressure as on the stock models.

## How it works

The firmware keeps each model in a registry (FINDINGS section 23). A model's entry has
three parts:
- its name;
- a builder for its patch: parameters, value displays, screen layout and Macros;
- a factory for its voices.

A project's slot records store a model index (0–4) that a small table maps to a model id.
The patch works with those pieces:

- **WAVE is model 3 and PHASE model 7**, ids no stock model uses. A hook on the
  registry's builder adds their entries at start-up. Because each has an id of its own,
  stock firmware rejects their slots' saved patches and plays the replaced model's Init
  patch. Borrowing ACD's id would make stock firmware load WAVE's values into ACD, and
  that played silence.
- **PHASE is built the way WAVE is**, so what follows for WAVE holds for PHASE too: its
  builder rewrites VAP's patch into PHASE's (the same 60 positions and the same six own
  parameters, 60–65), its voices are VAP voice objects, and it uses the same hooks. The
  differences are listed after WAVE's.
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
- **The swap** sets the table entry of the replaced index to 3 (or 7):
  - At project load, the patch reads `synths.bin` before the stock code applies the slot
    records. So the stock code gives each slot its model, then loads its patch, as the
    project had them.
  - A change in the menu takes effect at the next Config screen update.
  - Records keep stock indices.
- **`synths.bin` and Save As.** The choices are written a moment after a change, from the
  Pattern and Config screen updates, and only while the file manager is idle. A project
  with no folder yet (the new project "New Project", which has no path) writes no file;
  it gets one when it is first saved. The firmware's Save As copies only its own files
  and leaves the folder's others, so the patch writes `synths.bin` to the new folder
  itself. A chained hook on the Save As name copy (`bl strncpy` at `0x60114eaa` in the
  save request `0x60114e34`, Save As only) moves the choices to the new name at once and
  marks them unsaved. So the overwritten project's old `synths.bin`, if the folder has
  one, is never read, and the saved project's choices are written over it. A project
  that changes name in any other way (not through a load or Save As) reads its folder's
  file if it has one. The mark uses the existing state: no RAM is added.
- **The Model list** draws model logos by image id. With a swap, the patch hands the list
  its own id table with the replaced positions marked. The list draws the other logos,
  and the patch draws "WAVE" or "PHASE" as text in the marked rows.
- **The Edit Patch screen** shows the model's logo in a label, picked from a table by the
  model index. For a WAVE or PHASE synth, the patch clears the label's logo and gives it
  the synth's name.
- **WAVE's voices are VAP voice objects** (VAP's factory). Six entries of the VAP voice
  vtable (`0x600667e4`) are hooked, each chained to the stock one. A hooked method
  passes a WAVE voice to WAVE's engine and a PHASE voice to PHASE's:

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
- **The engine's voices:** up to 8, one per WAVE or PHASE voice, from one pool the two
  engines share. That is the stock limit for all three synths together. A slot with more voices (a loaded project can set that) keeps
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

### What PHASE does differently

- **The Edit Patch tiles.** The stock code labels the tiles from keywords in the pages'
  names ("Oscillator" gives Osc, "Filter" Filter, and so on), and DCO, DCW and DCA match
  none. So one more hook, chained, on the call to the tile builder (`0x6011e924`) lets
  the stock builder run, then, for a PHASE synth only, rewrites the labels to DCO, DCW,
  DCA, DCW Env and DCO Env, each with the icon the stock tile in that place has. The
  other tiles and other synths keep the stock labels.
- **The parameters** sit on VAP's positions as listed in
  [PD_SYNTH_PLAN.md](../../docs/PD_SYNTH_PLAN.md) section 4.3. The DCW envelope is VAP's
  filter envelope (Env 1), the DCO envelope VAP's free one (Env 2).
- **`synths.bin`** keeps its layout: byte 4 is WAVE's choice and byte 5 PHASE's.
- **The menu's two lists.** The stock option editor reads an item's count and names each
  time it opens, and its value text is the name at the item's stored position. Each item
  therefore gets a descriptor of its own from the heap (16 bytes) when the menu is built,
  and the patch points it at a list without the other item's model. The menu stores a
  position in that list; the item's apply callback turns it into the choice at once and
  updates the other item's list. The positions are set again whenever the choices change
  (a project load, the Pattern and Config screen updates). If the heap had no room, an
  item keeps the full list, and the old rule applies: set to one model, the item changed
  last keeps it and the other goes to Off.

**The PHASE engine** (`ph_engine.h`) renders mono, 128 samples per block, as WAVE's does:
- **The DCOs:** each has a 32-bit phase. A per-block setup turns the shape, the DCW and
  the pitch into a bend of up to four straight segments. Per sample, the engine finds
  the segment, bends the phase and reads a cosine table with linear interpolation. A
  pair switches between its two shapes' bends at each cycle's end.
- **The resonant shapes:** a sine at the resonance ratio, restarted with each cycle (hard
  synced), times the window, so the restart can't click. DCW sets the ratio, 24 to the
  power of the DCW, capped so the resonance stays under a quarter of the sample rate.
- **Aliasing:** the bends' fast segments last at least 4 samples, and the Double Sine's
  depth and the Saw-Pulse's saw part are limited at high notes. Measured at DCW 100 %,
  notes 60–108: the plain shapes' worst alias is −45 dB; the resonant ones −63 to
  −90 dB at note 60 and −32 to −35 dB at note 108. A step below WAVE's band-limited
  tables at the top; the CZ itself aliased audibly.
- **DC:** the pulse-like and resonant shapes have a DC offset. Each shape's mean is
  worked out from its segments per block and subtracted, ramped across the block, so no
  DC blocker is needed and a note starts without a thump.
- **Ring Mod** multiplies the DCOs' outputs; **Noise Mod** jitters DCO 2's phase step
  each sample by ± the amount × √(the step), so the noise grows with the amount at the
  same rate at every note.
- **Envelopes, LFOs, the Mod Matrix, glide, the level and the limit** work as WAVE's. The
  output gain is 0.25: one Init note is at −20.8 dBFS RMS, and a four-note chord at full
  velocity peaks at 0.51 of full scale.
- **The cosine table** (1,026 floats, 4 KB) comes from `gen_phase_tables.py`, a formula
  run at build time. The stock firmware's own sine table isn't used.

**Costs** (emulator, instructions per voice per 128-sample block):

| | Idle | Playing |
|---|---|---|
| Stock VAP voice | 60,804 | 66,195 |
| WAVE voice | 517 | 7,185 (Digital mode) to 19,413 (OB 24 dB and noise); 15,303 with a ladder |
| WAVE voice, both oscillators on the super saw | 517 | 46,219 (the ladder) to 50,329 (OB 24 dB and noise) |
| PHASE voice | 527 | 13,848 to 15,192 (one shape per DCO); 18,317 to 18,334 with everything on |

"Everything on" is pairs on both DCOs, all six Mod Matrix slots, Ring Mod, Noise Mod and
the DCO envelope.

An idle voice still runs its LFOs. On WAVE, each Mod Matrix slot in use adds about 300.
A WAVE or PHASE voice is also a VAP voice object: 6.5 KB of heap, as VAP.

The effect costs about 840 instructions per synth per block with Effect None (the copy to
the right side) and 8,900 with the Chorus, once per synth, not per voice.

**Sizes:**
- RAM: 2,064 bytes of on-chip patch RAM, for the settings, each slot's parameter copy and
  8 engine voices (WAVE's and PHASE's share the pool); and 6,288 bytes for the effect
  (on-chip on its own, SDRAM in the all bundle).
- Flash: 504 KB, 467 KB of it WAVE's seven banks and saw table, 4 KB PHASE's cosine
  table.
- The `.ptf` grows by about 1.4 MB (Intel HEX).
- With all eleven patches bundled, the image ends at `0x60242585`, 2.3 MB into the
  flash (stock: 1.7 MB). Images reaching 8 MB into the flash install and run on hardware
  ([FINDINGS.md](../../docs/FINDINGS.md) section 8).

**Shared hooks.** The Project Settings folder, the Config menu constructor's last item, and
the Pattern and Config screen update methods are chained, as other patches here chain
them. So are the registry builder, the project's slot records, the Model list's data,
the list control's image draw, the Edit Patch label's text, the Edit Patch tile builder,
the patch storage's load and the Save As name copy. So this patch combines with all of
the others.

## Build

No compiler needed: the compiled patch is in `prebuilt/`. See
[docs/PATCHING.md](../../docs/PATCHING.md) for getting and checking the stock firmware.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/synths/patch.json build/TrackerPlus_sy9.ptf
```

To combine it with other patches, list `../synths/patch.json` in a bundle (see "Choosing
patches" in [docs/PATCHING.md](../../docs/PATCHING.md)).

To build the patch code from source, you need the Arm GNU Toolchain (`arm-none-eabi-gcc` on
`PATH`, or `ARM_GCC_BIN` set to its `bin` folder) and Python with numpy (for the tables,
which `build.sh` generates with `gen_wavetables.py` and `gen_phase_tables.py`):

```bash
patch/synths/build.sh
```

### Emulator test

Needs `pip install unicorn capstone numpy`. There are two tests, one per synth:

```bash
python tools/emu/test_synths.py build/TrackerPlus_sy9.ptf path/to/TrackerPlus_1.2.0.ptf [wav folder] [--stress 20000]
```

```bash
python tools/emu/test_phase.py build/TrackerPlus_sy9.ptf path/to/TrackerPlus_1.2.0.ptf [sy7 .ptf] [--wav folder]
```

Both run the firmware's own synth system: host, slots, VAP voices and the audio graph
(`tools/emu/synth_rig.py`). `tools/run_tests.py` runs both on every build that has
synths.

`test_synths.py` checks WAVE and what the two synths share:

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
  - Save As through the firmware's own save request (`tools/emu/project_save.py`):
    WAVE's and PHASE's choices kept and written to the new folder, on a new project's
    first save too; over a project that has its own `synths.bin`, the saved project's
    choices (Off too) written over it; a plain Save writes nothing; the write waits for
    the file manager; a new project with no folder reads and writes nothing;
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

`test_phase.py` checks PHASE:
- **WAVE unchanged:** given a `sy7` build (the last with WAVE only), WAVE's model and its
  output are the same as `sy7`'s, sample for sample.
- **The PHASE model:** model id 7; 66 parameters in every per-parameter array, also
  after a rebuild; its names, ranges, lists, CCs, value displays, Init values, screen,
  Edit Patch pages and Macros; two channels; the title "Synth 1 (PHASE)"; no heap growth
  over 50 model switches.
- **The swap:** PHASE alone, WAVE and PHASE together, both set to one model (the one
  changed last wins), a slot moving from WAVE to PHASE, 200 swaps with notes held.
- **Menu, Model list, Edit Patch and file:** the "PHASE replaces" item; "PHASE" drawn
  where the replaced logo was, beside "WAVE"; the label; the tiles DCO, DCW, DCA,
  DCW Env and DCO Env with the stock icons, other synths' tiles stock; `synths.bin`'s
  byte 5, damaged files kept, a `sy7` file loading with PHASE Off.
- **The menu's lists**, through the stock menu code (constructor, editor, apply, value
  texts): each item's editor lists Off and the models the other item doesn't replace, at
  the right position; a pick sets the choice at once; positions before and after the
  left-out model; 150 random picks from the lists shown, with the lists, positions,
  texts and every synth's model checked after each; choices set outside the menu; the
  conflict rule as a backstop; full lists when the heap has no room for the descriptors.
- **Patch files:** `PHASE/<name>.patch` with model id 7 and 66 values, loaded back; stock
  firmware rejects it.
- **The engine**, against the shapes' formulas worked out in float64 in the test:
  - every shape sample for sample (within 6.7e-6) at DCW 0, 50 and 100 %, and at notes
    84–108 where the limits act;
  - DCW 0 a pure sine for Saw to Saw-Pulse; harmonics within 0.011 dB of the formula's;
    brightness rising with DCW;
  - aliasing within the limits above; no jumps in the Double Sine and the Resos;
  - pairs, Ring Mod, Noise Mod and the DCO envelope;
  - pitch within 0.002 cents, Tune, Finetune, Detune, bend; the envelopes' times; Env
    Amt 1 and 2; Key Follow; glide;
  - each LFO through each destination; every source to every destination at ±400 %
    finite and bounded;
  - the Init level, velocity, voice allocation as stock VAP, the effect, the cost.
- **Stress:** test_synths.py's, with both items swapped at random.

`--quick` runs everything but the engine's checks (which depend on its code alone) and
plays one note in their place; `--engine` runs only the engine's checks. With `--wav`,
it writes WAV files: each shape's DCW sweep at two notes, four pairs, a Reso I sweep at
three notes, Ring and Noise Mod, a DCO envelope pluck and an Init chord.

## Install and revert

Install as described in [docs/PATCHING.md](../../docs/PATCHING.md): copy the patched `.ptf` to
`/Firmware` on the SD card next to the stock `TrackerPlus_1.2.0.ptf`, then Config →
Firmware → the patched file. Config should show `1.2.0-sy9 Build 1504`.

To revert: Config → Firmware → `TrackerPlus_1.2.0.ptf`.
- Synths that were WAVE or PHASE play the replaced model's Init patch.
- WAVE and PHASE patches stay in `Patches/WAVE/` and `Patches/PHASE/`, and the projects'
  `synths.bin` files stay in their folders. Stock firmware doesn't read any of them
  (delete them if you like).
- To go back to `sy7` (WAVE only), install it the same way. PHASE synths then play the
  replaced model's Init patch too, and a project with PHASE set should play the stock
  models (see "Using it") until it is opened in `sy8` or later again.

If the unit freezes, pull the power, boot, and revert through Config. If it doesn't boot, or
Config itself doesn't work:
1. Put only the stock `TrackerPlus_1.2.0.ptf` in `/Firmware` (from a computer).
2. Power on with the card in.
3. Press the pinhole button next to the USB-C port.

The bootloader reinstalls the stock firmware.
