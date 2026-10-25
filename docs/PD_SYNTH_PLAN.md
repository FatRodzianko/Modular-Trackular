# PHASE: a phase distortion engine for `patch/synths`: plan (firmware 1.2.0)

A second custom engine in `patch/synths`, next to WAVE. It works the way the Casio CZ
series makes sound: two DCOs whose sine waves are bent by phase distortion, and a
DCW (digital controlled wave) that sets how far the bending goes, in place of a
filter. Like WAVE, it takes the place of one stock synth model per project and shows
its own name and controls.

Its name is **PHASE**:
- the Model list shows "PHASE";
- the instrument title reads "Synth 1 (PHASE)";
- its patches go in `Patches/PHASE/`.

All addresses are firmware 1.2.0. The engine plumbing is described in FINDINGS
sections 17, 21 and 23 and in `patch/synths/README.md`. The DSP formulas below are this
repository's own. They were designed from published descriptions of what the CZ
shapes do, not from anyone's code. A scratch prototype was built and measured in the
emulator (outside the repo; section 6 has its numbers).

Status: phase 0 (the checks before code) is done and passed (section 8). Nothing is
built in the repository. The user's answers are in section 9.

## 1. Verdict

**Feasible, inside the existing synths patch, with one new hook site, no patcher
changes and no RAM growth beyond 4 bytes.** The evidence:

1. **WAVE's plumbing is almost all engine-neutral.** Its hooks recognise a custom
   synth by its model id, keep a per-slot copy of the parameters, and hand each voice
   to an engine. These hooks are the registry builder, the six VAP voice vtable words,
   the Model list, the Edit Patch label, the patch load, the menu and `synths.bin`. A
   second engine needs four things: a second model id (7, see point 5), a second
   descriptor, a dispatch on the id, and a second menu item.
2. **The Edit Patch tiles can show CZ terms, through one new hook** (section 4.1).
   The tile labels come from one stock function, `0x6011d628`. It derives each tile's
   label and icon from its page's name by keywords: a name with "Envelope" becomes
   "Env 1", "Env 2"… and a name with "Filter" becomes "Filter". It has a single
   caller, the `bl` at `0x6011e924` in the Edit Patch screen's setup. A chained hook
   there rewrites the labels of a PHASE synth: DCO, DCW, DCA, DCW Env, DCO Env. No
   other patch hooks that site. The web patcher already handles chained branches, so
   `web/patcher.js` needs nothing.
3. **The DSP is cheap.** The prototype has two DCOs, three envelopes, two LFOs, a
   6-slot matrix, ring mod and noise mod, all measured in the emulator:

   | | Prototype | WAVE (README) |
   |---|---|---|
   | A plain shape (saw) | 13,100 | 15,303 (ladder filter) |
   | A resonant shape | 15,700 | |
   | Worst case: everything on at once | 18,100 | 19,413 (50,329 with super saws) |

   Everything on at once means mixed shape pairs, all six matrix slots, ring mod, noise
   mod and the pitch envelope. All figures are instructions per voice per 128-sample
   block. Stock VAP takes 66,195, and 8 WAVE voices peaked at 56 % CPU on hardware.
4. **It fits in RAM, just.** The settings, the parameter copies and the 8 engine voices
   share one startup block. In the all bundle that block is 2,064 bytes in
   `dtcm_above_heap`, and the region has **16 bytes left** after it (`build/*_MBT.map.json`:
   synths `0x200527e0`–`0x20052ff0`, region end `0x20053000`). `struct syn_state` is
   2,060 bytes today (compiled). PHASE therefore has to share WAVE's storage:
   - PHASE's engine voice (about 110 bytes) shares the 8-entry voice pool with WAVE's
     (160 bytes each) as a union. The stock cap is 8 voices across all three slots
     anyway.
   - PHASE's 61 parameters fill the slot's 61-float copy **exactly**. A slot runs one
     engine at a time.

   That leaves 3–4 new bytes (PHASE's menu value, saved value and applied index), which
   fit in the block's padding. The block stays 2,064 bytes. **After this, synths has
   no startup RAM left in the all bundle, and PHASE has no free parameter slot.** Any
   later addition must use the late RAM block (SDRAM, after boot) or replace something.
5. **Model id 7 is free and works in stock code (phase 0, emulator).** The registry
   has ids 0–2 and 4–6, and WAVE uses 3. Stock firmware rejects a patch file whose
   model id is 7 (not registered), which keeps PHASE patches away from stock models.
   Phase 0 rebuilt WAVE at id 7 (scratch) and ran all 96 of `test_synths.py --quick`'s
   checks on it, with no failures. They cover the title, the settings screen, the
   swaps, set_model, voice creation, the Model list, Edit Patch, patch save and load,
   stock's rejection of the file, export, the effect and the stress run. A trace of
   every stock read of the id over the same runs gives the same 10 code sites at id 7
   as at id 3. Each one compares the id, copies it, or looks it up in the registry's
   hash map; none of them indexes a table with it. Paths those runs don't reach are
   not covered by the trace.

What makes this harder than WAVE was: nothing structural. The risks are WAVE
regressions from the shared-code refactor, aliasing at high notes, how faithful two of
the shapes are, and running out of room (section 7).

## 2. How WAVE is built, and what PHASE shares

### 2.1 Shared plumbing (made engine-neutral, used by both)

| Piece | Today (WAVE only) | With PHASE |
|---|---|---|
| Registry hook `mt_syn_registry` | Inserts the WAVE descriptor (id 3, VAP's voice factory) | Also inserts PHASE's (id 7, VAP's factory, PHASE's builder) |
| Builder | `wave_builder`: VAP's builder, then `wave_rewrite` (names, ranges, displays, cells, pages, Macros, Init values, 6 own params, 2 channels, model id) | `phase_builder`: the same steps with PHASE's tables. The checks on VAP's layout, the own-parameter append and the 66-entry rule (FINDINGS 23, "Adding parameters") are shared helpers |
| Voice hooks (+0x04 … +0x18) | `is_wave(voice)`: model id 3 | `engine_of(voice)`: 3 → WAVE, 7 → PHASE, else stock. The trampolines don't change |
| `set_param` | `wt_param_slot[]` maps an index to the slot copy | Per engine: `wt_param_slot[]` or `ph_param_slot[]`, same copy |
| Engine voices | `struct wt_voice voice[8]`, `.owner` | A union `{wt, ph}` with `owner` and `slot` first in both. `entry_new` / `sweep` / `delete` are unchanged |
| Effect (`wt_fx.h`, params 62–65) | On a WAVE slot | On a PHASE slot too: the same per-slot state, keyed by slot (section 4.6) |
| Patch load hook | Sets WAVE's own 6 to defaults before a load | The same for a PHASE patch (`wave_values` becomes "a custom patch's values") |
| Model list | One row marked `LIST_TEXT_ID` (0x7F), drawn as "WAVE" | Two marks: 0x7F "WAVE", 0x7E "PHASE" |
| Edit Patch label | "WAVE" for a WAVE slot | "PHASE" for a PHASE slot |
| Edit Patch tiles | Stock | **New:** PHASE's labels and icons (4.1) |
| Menu | Synth Engines → WAVE replaces | Synth Engines → WAVE replaces, **PHASE replaces** (folder capacity 1 → 2) |
| `synths.bin` | Byte 4: WAVE's choice; bytes 5–7 must be 0 | Byte 5: PHASE's choice (section 5.2) |
| Start-up gate | Silence until the Pattern screen has run | The same |

### 2.2 WAVE-only (unchanged)

The wavetables (`wt_tables.h`, 467 KB), the oscillators, Digital mode, the super saw,
the filters, Drive and WAVE's parameter map. The envelope, LFO, matrix, glide and
`exp2f_fast` code in `wt_engine.h` is written against WAVE's parameter enum. PHASE gets
its own small copies driven by its own enum, so WAVE's code and output don't change at
all (section 8, phase 1 checks this sample for sample). Sharing those functions
properly is a later cleanup, once both engines are tested.

## 3. The DSP design

### 3.1 Phase distortion

Each DCO has a 32-bit phase accumulator, `p` = phase / 2³² in [0, 1). A **warp**
`W(p)` maps it to a new phase, and the output is `cos(2π W(p))`. With `W(p) = p` that is
a pure sine. The DCW amount `d` (0..1) moves `W` from the identity (d = 0) to the
shape's full warp (d = 1).

Every warp here is **piecewise linear with up to four segments**, `W = b_i + m_i p` on
`[x_i, x_i+1)`. Per block, `shape_setup` turns (shape, d, pitch) into the breakpoints
and segment coefficients. Per sample, the engine finds the segment with three compares,
does one multiply-add and one table lookup. Each shape is just a different set of
breakpoints, so pairs (section 3.4) and the resonant windows (3.2) use the same
evaluator.

The full warp (d = 1) for each shape. `k` is a knee, the length of the fast segment
(at least `k_min`, see 3.3):

| # | Shape (list name) | Warp at DCW d (own formulas) | At d = 1 |
|---|---|---|---|
| 1 | Saw | knee `k = ½(1 − d)`: W rises 0 → ½ over [0, k], then ½ → 1 over [k, 1] | A fast fall, slow rise: harmonics −7.4, −11.5, −14.0 dB (2nd–4th), like a saw |
| 2 | Square | `k = ½(1 − d)`: 0 → ½ over [0, k], hold ½, ½ → 1 over [½, ½ + k], hold 1 | Odd harmonics only (even ones under −87 dB in float32) |
| 3 | Pulse | `k = 1 − 0.9 d`: 0 → 1 over [0, k], then hold 1 | One cosine cycle in the first 10 % of the period: a narrow pulse. At least 10 % wide, because a 0 % pulse is silence |
| 4 | Double Sine (the user's "resonant pulse") | Two segments of slope `1 + a` (a = 0.6 d), the second starting at W = ½ − a/2 | Two cosine humps per period, the fundamental and the octave about equal (−1.3 / 0 dB). At every d the output is continuous: the jump in W lands on a symmetric point of the cosine |
| 5 | Saw-Pulse | Saw-like 0 → ½ → 1 over [0, e] with `e = 1 − ½ d`, then hold 1 | A saw in the first half of the period, flat in the second |
| 6–8 | Reso I–III | Section 3.2 | A synced "resonance" sine under a saw, triangle or trapezoid window |

Measured in float64 and again through the prototype in the emulator (harmonics 1–8,
note 45; the prototype matches the reference within 0.01 dB):
- **For shapes 1–5, DCW 0 is a pure sine.** Harmonics 2–10 are at most −106.6 dB,
  through a 1,024-point cosine table with linear interpolation.
- **DCW rises smoothly.** For example the saw's 2nd harmonic is −11.1 dB at d = 0.5
  and −7.4 at d = 1.

### 3.2 The resonant waves (as the CZ does them)

`y = w(p) · (1 − cos(2π r p)) − 1`
- `w` is the window: saw `1 − p`, triangle `1 − |2p − 1|`, trapezoid `min(1, 2(1 − p))`.
- `r` is the resonance ratio. The resonance sine restarts with the main phase, which
  makes it hard-synced.

**As on the CZ (the user's choice):**
- The window is always on.
- DCW moves only the resonant frequency: `r = 24^d`, from 1 at DCW 0 to 24 at DCW 1.
  This is the CZ's "filter sweep".
- So at DCW 0 a resonant wave is a soft windowed sine, not a pure one. Its 2nd
  harmonic is at −19.8 dB (saw window), −11.5 (triangle) and −17.4 (trapezoid).

All three windows end at 0, so the restart at the cycle end can't click at any `r`.
The top of `r` (24) and the curve are a guess, to be tuned by ear against the user's
CZ-3000 (phase 4).

The window is just another piecewise-linear map (one or two segments) through the same
evaluator.

### 3.3 Aliasing

Phase distortion makes corners: a fast segment is nearly a step at d = 1. Two limits,
both per block, keep the corners within the sample rate:
- **Knee length:** a fast segment lasts at least `KNEE_SAMPLES` = 4 samples
  (`k_min = 4 f / 44,100`). At low notes this changes nothing. At high notes it eases
  the DCW, which is what the CZ's own DCW key follow does musically.
- **Resonance:** `r f` stays below 0.25–0.3 × 44,100 Hz (11–13 kHz). Phase 2 picks the
  value.

Worst non-harmonic component at d = 1, in dB below the strongest harmonic:

| Note | 60 | 72 | 84 | 96 | 108 |
|---|---|---|---|---|---|
| Saw, no knee limit (float64) | −40.6 | −35.4 | −29.0 | −23.4 | −18.1 |
| Saw, 4-sample knee (prototype, emulator) | −65.4 | −59.5 | −54.3 | −48.7 | −53.7 |
| Square, 4-sample knee (float64) | −61.8 | −57.1 | −53.4 | −45.3 | −50.4 |
| Pulse, 4-sample knee (float64) | −65.2 | −48.5 | −48.0 | −48.9 | −45.9 |
| Reso I, saw window, r f ≤ 0.3 rate (prototype) | −64.0 | −43.7 | −35.0 | −29.5 | −27.5 |
| Reso II, triangle window (prototype) | −90.4 | −62.4 | −48.4 | −38.0 | −26.9 |
| Reso III, trapezoid window (prototype) | −67.5 | −47.2 | −39.5 | −31.4 | −28.8 |

This is a step below WAVE's band-limited tables (−60 dB at notes 96–108).
- **Plain shapes:** the target is −45 dB from note 60 to 108.
- **Resonant waves:** they alias more above note 84. A cap of 0.25 instead of 0.3
  gains about 5–10 dB there; the cost is a lower highest resonance.

The CZ itself aliased audibly. If the user hears too much at the top, two fixes are
possible:
- a longer knee (6–8 samples), which dulls the top octave;
- 2× oversampling with a half-band decimator, which costs about +5,000 instructions
  per voice.

The plan doesn't include either one.

### 3.4 Two shapes per DCO

On the CZ, a DCO can take two shapes, played on alternate cycles. That gives a
sub-harmonic an octave down and the CZ's typical hollow, reedy pairs. The CZ allows
two non-resonant shapes, or one non-resonant and one resonant, but not two resonant
ones.

**Included.** It costs one compare per sample (the phase wrap toggles a bit and the
segment pointer), about +1,700 instructions per block with mixed kinds. It needs two
list parameters ("Wave 1+", "Wave 2+", Off by default). The same rule as the CZ is
applied: a resonant second shape after a resonant first plays as Off.

Whether the CZ also shifts the pitch so the pair still sounds at the note is to be
checked by ear on the user's CZ-3000 (phase 4). The default is no shift: the pair's
period is two cycles, as described.

### 3.5 The extras (all included, at the user's request)

- **Ring Mod** (Off/On). DCO 2's output becomes DCO 1 × DCO 2, and Mix crossfades DCO 1
  and that product. This is how the CZ's ring modulation sounds: one line's output
  sets the other's level. Measured: +770 instructions per block.
- **Noise Mod** (0–100 %). DCO 2's phase step is jittered by noise each sample, which
  turns DCO 2 into the CZ's digital noise at full amount. Measured: +1,540.
- **DCO Env** (the CZ's pitch envelope). An ADSR with an amount in semitones on both
  DCOs' pitch. It is also a Mod Matrix source, so it can move DCW, Mix or Volume
  instead (section 4.3). Measured: +10 with the amount at 0, about +250 in use.
  - The CZ's pitch envelope only raises the pitch.
  - Here the amount is ±48 semitones, so 0 to +48 behaves as the CZ does.
  - A one-way range is one number in the builder (section 9).

### 3.6 The cosine, numbers and per-voice state

- **The cosine:** an own 1,026-entry float table in flash (4 KB, generated by formula
  at build time like WAVE's tables), with linear interpolation. Error below −106 dB.
  The stock firmware's sine table isn't used: that would tie the engine to Polyend
  data at an undocumented address, and a sine table is cheap. A polynomial would save
  the 4 KB but cost about 6 more instructions per sample.
- **Float, not fixed point:** the M7's FPU does a single-precision multiply-add per
  cycle, and WAVE, VAP and the effect are all float. The phase stays a 32-bit integer
  for an exact wrap; the warp and the output are float. Fixed point would save nothing
  on this core and would make the warp's divisions harder.
- **DC:** the pulse and resonant shapes carry a DC offset. The prototype uses a
  one-pole DC blocker (about 5 Hz, 5 instructions per sample, in the numbers above).
  The build may instead subtract each shape's mean, worked out analytically per block
  from its segments. That leaves no thump at note start, and phase 2 picks whichever
  measures cleaner.
- **Level:** as WAVE: velocity → Volume (39), an output gain set so a four-note chord
  of the Init patch stays under the slot's clip, and a ±2 limit (`vmaxnm` / `vminnm`,
  NaN-safe).
- **Per-voice state** (`struct ph_voice`, about 110 bytes, under WAVE's 160):
  - owner, slot, volume;
  - two phases, two pair toggles;
  - note, pitch, glide step, bend;
  - the DCA, DCW and DCO envelopes;
  - two LFO phases, holds and one-shot flags;
  - the DC state;
  - the noise word (noise mod, Random S&H);
  - the gate, started and fresh flags.

## 4. UI and controls

Like WAVE, PHASE's builder runs VAP's builder and then rewrites the patch. VAP's 60
entries keep their positions; their names, ranges and displays change, and six own
entries follow (60–65).

### 4.1 The Edit Patch tiles (new hook)

**How stock firmware labels the tiles** (static reading of `0x6011d628`, confirmed in
the emulator by running it on a fake screen object):
- For each page with a name, in order, it copies the full name (24-byte buffers at
  screen `+0x444`).
- It makes a label of at most 7 characters (8-byte buffers, pointers at `+0x448`) and
  an icon kind (`+0x42c + i`) from keywords in the name.

The keyword rules, tried in this order:

| Page name contains | Label | Kind (icon) |
|---|---|---|
| "Amp Envelope" | Amp Env | 0x11 |
| "Amplifier" | Amp | 0x11 |
| "Envelope" | Env 1, Env 2, … (counted) | 0x11 |
| "Filter" | Filter | 0x12 |
| "Voice" | Voice | 0x13 |
| "LFO" | LFO 1, LFO 2, … (counted) | 0x14 |
| "Modulation" | Mod | 0x16 |
| "Oscillator" | Osc | 0x17 |
| Kick, Tom, Snare, Hihat, Cymbal, "Percussion" (Perc), Mixer, FX | the same | 0x1f, 0x23, 0x22, 0x1e, 0x1d, 0x21, 0x20, 0x1c |
| Anything else | (empty) | 0xff (none) |

So a page called "DCW" gets an empty tile with no icon (emulator), and **renaming the
pages alone can't do it**. This also explains what was seen on hardware: WAVE's
"Filter Envelope" page reads "Env 1", because "Envelope" is tried before "Filter".

**The hook.** The function's one caller is `bl 0x6011d628` at `0x6011e924` in
`scr_edit_patch_setup_q` (`r0` = the screen, `r1` = 1). A chained branch hook calls
through and then, if the selected synth (`0x202046c5` − 64) is a PHASE slot, rewrites
each tile's label and kind by page index:

| Page | Label | Icon kind |
|---|---|---|
| 0 | DCO | 0x17 (Osc's) |
| 1 | DCW | 0x12 (Filter's) |
| 2 | DCA | 0x11 (envelope) |
| 3 | DCW Env | 0x11 |
| 4 | DCO Env | 0x11 |
| 5, 6, 7, 8 | LFO 1, LFO 2, Mod, Voice | stock |

PHASE's first page names (DCO, DCW, DCA) match no keyword, so the stock function gives
those tiles an empty label and no icon. The hook therefore sets both, giving each tile
the same icon as the stock tile in that position.

**Checked in the emulator (phase 0):** right after the call, the setup stores the
label pointer array (`+0x448`) and the icon bytes (`+0x42c`) into the tiles control's
data (`+0x130`, `+0x134`). The control (constructor `0x60104828`, vtable `0x60046914`)
was made with a pointer to that data, so it reads the buffers each time it draws. Built
that way in the emulator and drawn, the stock tiles read Osc, Filter, Amp Env, Env 1,
Env 2, LFO 1, LFO 2, Mod, Voice and the Macro tiles M1–M7. With PHASE's page names the
stock builder leaves the first three tiles empty with no icon (kind 0xff) and calls the
envelopes Env 1 and Env 2. After the simulated hook (labels and kinds rewritten after
the call), the tiles read DCO, DCW, DCA, DCW Env, DCO Env, LFO 1, LFO 2, Mod, Voice,
M1–M7. The display list is word for word the stock one except for the label strings, so
the icons and the layout are unchanged.

- **Fit:** "DCW Env" is 7 characters and at most 74 px in the firmware's widest UI
  font. Stock "Amp Env" takes 70 px.
- **Other synths** are untouched. The function runs every time the screen opens, so
  switching synths re-labels.
- **In patch.json:** a 1-word chain and about 150 bytes of code.
- **Optional, not planned:** the same hook could give WAVE's tiles "Flt Env" and
  "Mod Env". That would change WAVE's UI, so it is the user's call, not part of this
  build.

The page names (shown when a tile is opened) are DCO, DCW, DCA, DCW Envelope and DCO
Envelope. The **Synth Parameters tabs** use the groups' names, which the builder sets
directly, as WAVE does: "DCO", "DCW", and the Modulation screens "DCA",
"DCW Env/LFO 1", "DCO Env/LFO 2".

### 4.2 Envelopes

Three ADSRs:
- **DCA** (amplitude) on VAP's amp envelope, 19–22: the DCA tile.
- **DCW** (timbre) on VAP's filter envelope, 23–26: the DCW Env tile. This is VAP's
  Env 1 slot, which the user checked on hardware. Each DCO has its own depth, Env Amt 1
  and Env Amt 2, so one envelope can move the two DCOs' timbres by different amounts or
  in opposite directions.
- **DCO** (pitch) on VAP's free envelope, 15–18: the DCO Env tile, the CZ's pitch
  envelope (3.5). Its Amount sits on the same page. It is a Mod Matrix source too.

Why one DCW envelope rather than one per DCO: on the CZ, the DCW and DCA envelopes
shape different things. One timbre envelope with two depths, plus one level envelope,
covers the classic CZ sounds: plucks, brass swells, sweeping resonances.

### 4.3 Parameters (index = VAP's position)

| Index | Name | Range / display | Engine |
|---|---|---|---|
| 0, 1 | Wave 1+, Wave 2+ | Off, the 8 shapes (list) | The second shape of a pair (3.4) |
| 2, 3 | Wave 1, Wave 2 | Saw, Square, Pulse, Double Sine, Saw-Pulse, Reso I, Reso II, Reso III | The DCO's shape |
| 4 | Mix | −1 DCO 1 only … +1 DCO 2 only | As WAVE |
| 5, 6 | Tune 1, Tune 2 | ±36 st | |
| 7 | Detune | DCO 2 up 0–100 c | |
| 8 | Finetune | ±100 c | |
| 9, 10 | Reset 1, Reset 2 | Off / On | Each note from phase 0 |
| 11 | Ring Mod | Off / On | 3.5 |
| 40 | Noise Mod | 0–100 % | 3.5 |
| 12, 13 | DCW 1, DCW 2 | 0–100 % | Each DCO's DCW. 12 keeps CC 74, 13 keeps CC 71 |
| 14 | Env Amt 1 | ±100 % | DCW envelope → DCO 1 |
| 38 | Env Amt 2 | ±100 % | DCW envelope → DCO 2 (VAP's unused Mix Env Amt, as WAVE's Drive) |
| 37 | Key Follow | ±100 % | DCW falls (or rises) with the note around middle C, as the CZ's DCW key follow |
| 15–18 | DCO Env: Attack, Decay, Sustain, Release | As WAVE's envelopes | 4.2 |
| 41 | DCO Env Amt | ±48 st | 3.5 |
| 19–22 | DCA: Attack, Decay, Sustain, Release | | |
| 23–26 | DCW Env: Attack, Decay, Sustain, Release | | |
| 27–34, 60, 61 | LFO 1, LFO 2: Ratio, Waveform, Retrigger, Sync, Polarity | As WAVE | |
| 35, 36 | Glide Mode, Glide Time | As WAVE | |
| 39 | Volume | Velocity / 127 | Per voice, not in the slot copy |
| 42–59 | Matrix: 6 × Src, Dest, Amt | ±400 % | 4.4 |
| 62–65 | Effect, Rate, Depth, Mix | As WAVE | 4.6 |

The engine reads 61 values, which fill the slot copy's 61 exactly.

### 4.4 The LFOs and the Mod Matrix (included)

Two LFOs with WAVE's exact controls: Ratio in Hz or synced to the tempo, six
waveforms, Note, One Shot or Free Running, and Bipolar or Unipolar. They are routed
through **WAVE's 6-slot Mod Matrix with PHASE's own lists:**
- **Sources (7):** DCA Env, DCW Env, DCO Env, LFO 1, LFO 2, Velocity, Key.
- **Destinations (9):** Pitch, Pitch 1, Pitch 2, DCW, DCW 1, DCW 2, Mix, Volume,
  Detune.

The **Init patch** routes LFO 1 → Pitch (vibrato) and LFO 2 → DCW, both at Amt 0, so
the user raises them to hear them, as on WAVE.

### 4.5 Pages and Macros

**Edit Patch pages:**

| Tile | Items |
|---|---|
| DCO | Wave 1, Wave 1+, Wave 2, Wave 2+, Mix, Tune 1, Tune 2, Finetune, Detune, Reset 1, Reset 2, Ring Mod, Noise Mod |
| DCW | DCW 1, DCW 2, Env Amt 1, Env Amt 2, Key Follow |
| DCA | the DCA ADSR |
| DCW Env | the DCW ADSR |
| DCO Env | the DCO ADSR, DCO Env Amt |
| LFO 1, LFO 2 | as WAVE (Polarity last) |
| Mod | the matrix |
| Voice | Glide Mode, Glide Time, Volume, Effect, Rate, Depth, Mix |

The groups' cells are rewritten the way WAVE does it, in place where they fit and from
the heap where they don't.

**Macros:**
- a: Attack (DCA);
- b: Decay/Release (DCA and DCW Env);
- c: DCW (12 and 13);
- d: DCW Env Amt (14 and 38);
- e: Detune;
- f: Mix;
- Velocity and Pressure: as stock.

These are a proposal; any parameter can go on any Macro.

### 4.6 The effect (included)

A PHASE slot can use WAVE's stereo chorus (62–65), which costs no new RAM: the per-slot
state exists for all three slots. The CZ-3000 has a built-in stereo chorus. Effect None
is the default, so a PHASE synth is mono and plain until the chorus is turned on.

### 4.7 The menu

Project Settings → Synth Engines:
- **WAVE replaces**: Off, ACD, FAT, VAP, WTFM, PERC (unchanged).
- **PHASE replaces**: Off, ACD, FAT, VAP, WTFM, PERC (new).

Both engines can run in one project, on different models. If the two items name the
same model, **the item changed last wins and the other goes to Off**. The check
happens at the next Config screen update, where the choice is applied anyway. The
menu shows the reset the next time it draws; phase 1 verifies that the option item
reads its value when it draws.

## 5. Saving and bundles

### 5.1 PHASE patches

PHASE patches save the way WAVE's do:
- **Where:** `Patches/PHASE/<name>.patch` (the folder is the descriptor's name). A
  project's own copy goes in `Workspace/<slot>.patch`.
- **Contents:** model id 7 and 66 values by position. The 66 is enforced the same way
  as WAVE's: the vector is made 66 long before VAP's builder, VAP's builder trims it to
  60, and PHASE's builder appends its 6.
- **Loading:** the patch-load hook sets PHASE's 6 own values to their defaults before
  a load, as it does for WAVE.
- **Stock firmware** rejects id 7, so a PHASE slot plays the replaced model's Init
  patch and keeps the project's other settings.

Because PHASE is new, no older PHASE files exist to stay compatible with.

### 5.2 `synths.bin`

The file keeps its 12-byte layout and magic: byte 4 is WAVE's choice and **byte 5
PHASE's**, and the checksum already covers bytes 4–7.
- A `sy7` file (byte 5 = 0) loads as PHASE Off. Nothing to migrate.
- `sy7` firmware reading a file with PHASE set sees byte 5 ≠ 0 and treats the file as
  damaged. It plays the stock models (WAVE too) and **keeps the file**: it only writes
  after a change. Going back to the newer build restores both choices. This is
  documented for the user.

### 5.3 The name and the version text

**"PHASE" fits everywhere "WAVE" is drawn.** In the firmware's five UI fonts (both
sets, widths decoded from the fonts' run-length tables, FINDINGS 27):

| | Width | Where it goes |
|---|---|---|
| "PHASE" | 35–56 px | Model list: logos are 80 × 29 px |
| "WAVE" | 28–50 px | Edit Patch label: logos are 108 × 40 px |

The title is "Synth 1 (PHASE)", one character longer than "Synth 1 (WTFM)". The name
and the folder are well inside a 15-character string.

**Version text:**
- synths alone: `sy7` → **`sy8`** (3 characters).
- `patch/all`: `MBT` → a new 3-character text, chosen when it is built.
- Web mixes: `M` + 2 hex digits (unchanged scheme).

All stay within 3 characters after `1.2.0-`.

### 5.4 `patch/all`, the web patcher and the layout

- **`patch/synths/patch.json`:** gains one branch, `0x6011E924` (old bytes checked)
  → `mt_syn_tiles`, chained (`next`). Its description changes too. The new prebuilt
  changes every bundle containing synths.
- **`patch/all`:** gets the new version text. RAM placement doesn't change (the block
  stays 2,064 bytes). Flash grows by about 16 KB:
  - about 4.5 KB of PHASE code;
  - 4 KB of the cosine table;
  - 4–6 KB of builder, tile and display tables.

  The all bundle's image ends near `0x60242000` (2.2 MB into the flash; 8 MB has been
  proven on hardware).
- **The web patcher:** the code is unchanged, as chained branches are already
  supported.
  - `python tools/test_web_patcher.py <stock> build/webtest --update` rewrites
    `web/expected.json`.
  - The hardware-tested hashes go into `web/manifest.json` `tested_builds` and the
    PATCHING.md table after the hardware check, as for every synths release.
  - The synths entry's summary in the manifest gains "and PHASE".
- **`tools/test_layout.py`:** already covers synths alone, in the bundles and
  reordered. It also checks the new hook's chain and old bytes. It is re-run because
  the block size is at its limit in the all order.

## 6. Costs

| | PHASE | WAVE today | Source |
|---|---|---|---|
| CPU, a plain shape | ~13,100 + ~500 hook overhead | 7,185–19,413 | Prototype in the emulator; WAVE's README |
| CPU, a resonant shape | ~15,700 + overhead | | Prototype |
| CPU, ring mod / noise mod / DCO Env | +770 / +1,540 / +10 to ~250 | | Prototype |
| CPU, worst (everything on) | ~18,100 + overhead | 19,413 (50,329 with super saws) | Prototype |
| CPU, idle voice | ~400–500 | 517 | Prototype |
| Startup RAM | +4 B, inside today's 2,064 B block | 2,064 B | `sizeof(struct syn_state)` = 2,060 (compiled); MBT map |
| Late RAM (effect) | 0 | 6,288 B | Shared |
| Heap | 1 descriptor (0x38 B) at start; per PHASE patch build, the rewritten cells as WAVE (a few hundred bytes) | Same | `wave_rewrite` |
| Flash | ~14–18 KB | 483 KB | Prototype: 8.7 KB code + table, before the UI code |

The worst case is mixed shape pairs, all six matrix slots, ring mod, noise mod and the
DCO envelope at once.

The prototype's numbers include two DCOs, the three envelopes, both LFOs, the matrix,
the extras, the DC blocker, the amp ramp and the limit. They exclude glide, bend and
the LFO waveforms other than the triangle, a few hundred instructions in all.

Proposed test budgets for a playing PHASE voice:
- **under 17,000** for a single shape with no extras;
- **under 20,000** with everything on.

For comparison, WAVE's oscillator test uses 16,000, and WAVE's worst non-super-saw
case is 19,413. 8 PHASE voices cost about what 8 WAVE voices do, which peaked at 56 %
on hardware with an LFO and full reverb and delay sends.

## 7. Risks and open points

1. **WAVE regressions from the refactor** (the dispatch, the union pool, the shared
   load/label/list code). Guard: every existing synths check, plus a new one that WAVE
   renders sample for sample as `sy7` on a fixed note/parameter script. The stock-output
   identity checks already exist.
2. **Model id 7 in stock code** (point 5 of the verdict). Phase 0 passed in the
   emulator. What is left is stock code the test runs don't reach (the trace only sees
   what runs); the hardware check covers the screens and saves.
3. **No room left.** The all bundle's startup RAM is at its limit, and PHASE uses all
   61 values of the slot copy. Guards in the source:
   - `_Static_assert(sizeof(struct syn_state) <= 2064)`;
   - `sizeof(union) == sizeof(struct wt_voice)`;
   - `PH_PARAMS <= WT_PARAMS`;
   - plus test_layout on every order.

   Any further PHASE parameter needs the slot copy moved to late RAM first.
4. **The tile hook** writes into buffers that the stock function has just filled, and
   only for a PHASE slot. Phase 0 showed the tiles control draws from those buffers
   (4.1). The emulator still can't run the whole Edit Patch screen, so the screen as a
   whole is a hardware check (section 10, step 4). If it fails there, the fallback is to
   hook the two label writes inside the function instead (`0x6011d8e0` strncpy and
   `0x6011da68` snprintf), which changes the text as it is written.
5. **Aliasing** at the top of the range (3.3):
   - about −45 to −50 dB on the plain shapes;
   - about −27 to −38 dB on the resonant waves above note 96.

   This is audible on bright patches up there. The knee and the resonance cap are one
   constant each.
6. **Faithfulness:**
   - The double sine and saw-pulse are drawn from the CZ's published waveform pictures
     and descriptions. Some sources call shape 5 "half-sine".
   - The resonance range is a guess (r up to 24).
   - Whether pairs shift the pitch is unknown.

   Each of these is a few lines in `shape_setup` and can be tuned by ear against the
   user's CZ-3000 in phase 4.
7. **Both items set to one model** (4.7): the menu shows the reset only on its next
   draw. Phase 1 checks this.
8. **Heap.** No new large allocations. The cell vectors that grow come from the heap
   as WAVE's do. The heap is thin under heavy use (FINDINGS 15), so the stress test
   runs PHASE and WAVE builds together.
9. **Test time.** The full synths run takes about 23 minutes (runner estimate
   1,400 s). PHASE's checks go into a separate `tools/emu/test_phase.py`, run by the
   runner in parallel with `test_synths.py`, so the release tier grows little. Its own
   time is measured in phase 2 and stated before any run over 10 minutes.

## 8. Build phases

Each phase ends with its emulator tests passing. Nothing is committed unless the user
says so.

**Phase 0: checks before code (emulator, scratch only). Done 2026-10-24, all passed.**
- **Model id 7:** WAVE rebuilt with its model id at 7 (a scratch copy, one `#define`
  changed) and `test_synths.py --quick` (its WAVE constant set to 7) run on it: 96
  checks passed, 0 failed, in 3 minutes. This covers:
  - the title "Synth 1 (WAVE)";
  - the settings screen;
  - swaps, set_model and voice creation;
  - the Model list and Edit Patch;
  - Patches and Workspace save and load, the file's field 1 = 7, and stock's rejection
    of it;
  - `synths.bin`, export, the effect, and the stress run.
- **Where the id goes:** a trace of stock reads of RAM holding 3 or 7, run on an id-3
  build and on the id-7 build. Both runs read the id at the same 10 sites:
  - in ITCM: the patch file's parse (`0x187d6`) and write (`0x1c006`), and the slot's
    voice setup (`0x1e4a4`–`0x1e4ec`);
  - in flash: `set_model` (`0x600b1450`), the title (`0x600b16de`), load by name
    (`0x60124cb8`) and the registry's find (`0x6012fd42`).

  Each one compares, copies or hashes the id. One more site read a 7 only in the id-7
  run: `0x6014f3c4`, VAP's builder copying a `std::function`. It read a 3 in both runs
  too, so the 7 there is a coincidence, not the id.
- **The tiles:** the stock tile builder, a simulated hook and the stock tiles control
  drawn in the emulator (section 4.1). The labels come out as planned, and the display
  list matches stock apart from the label strings.
- **RAM:** with PHASE's three state bytes and `_Static_assert(sizeof(struct syn_state)
  <= 2064)` added, `struct syn_state` is 2,064 bytes and the startup block is still
  2,064 (`.mt_ram` is 16-byte aligned). The all bundle's placement doesn't change.
- **An emulator quirk to note:** a Unicorn memory-write hook over all of RAM makes the
  synth rig's boot fault (at `0x601665d2`), so the trace used read hooks only. With the
  read hooks, two WAVE patch-file checks failed in *both* runs (id 3 and id 7). Without
  hooks they pass, so the failures come from the instrumentation, not from the id.
- FINDINGS 23 gets "Model id 7" and "The Edit Patch tiles" (the keyword table in 4.1,
  the control's data) in phase 1, with the first tracked change.

**Phase 1: the shared plumbing, PHASE rendering silence.**
- Engine dispatch by model id, the union voice pool, `ph_param_slot[]`.
- The second descriptor and `phase_builder`: names, ranges, displays, pages, tabs,
  Macros, Init values, 6 own entries, 2 channels, id 7.
- The tile hook.
- Menu item 2 and the conflict rule; `synths.bin` byte 5.
- The Model list's second text; the Edit Patch label "PHASE".
- The patch-load hook and the effect for PHASE slots.
- The engine renders silence, answers "sounding" from its DCA envelope and handles
  notes.

Tests (`test_phase.py`, plus all of `test_synths.py` unchanged):
- **WAVE unchanged:** WAVE sample for sample as `sy7`; stock output identical with
  both engines Off.
- **The PHASE model:** id 7; its 66 entries in every per-parameter array, also after a
  rebuild; names, ranges, displays, Init values, pages and cells, Macros; 2 channels;
  title "Synth 1 (PHASE)".
- **Tiles:** DCO, DCW, DCA, DCW Env, DCO Env with their icons on a PHASE slot; stock
  labels on VAP and WAVE slots.
- **Swaps:** PHASE alone, WAVE and PHASE together on different models, and the
  conflict rule both ways. No PHASE parameter reaches VAP; no heap growth over 200
  swaps.
- **The Model list:** both texts at their rows, scrolled too. The label reads "PHASE"
  and other models keep their logos.
- **`synths.bin`:** byte 5 saved and loaded. A `sy7` file loads with PHASE Off; a
  damaged file counts as Off and is kept; Save As takes both along.
- **PHASE patch files:** save to `PHASE/<name>.patch` with id 7 and 66 values, and load
  back; a failed load restores; stock rejects them and plays the Init patch.

**Phase 2: the engine (`ph_engine.h`).**
- The 8 shapes; DCW with key follow and per-DCO envelope depth; the knee and resonance
  limits; pairs.
- Ring mod, noise mod, the DCO envelope.
- DCA and DCW envelopes, LFOs, the matrix, glide, bend, DC removal, level and limit.
- The cosine table generated at build time.

Tests:
- **DCW 0:** a pure sine for shapes 1–5 (harmonics 2–10 below −90 dB); the windowed
  sine for 6–8, matching its formula.
- **DCW 1:** each shape's harmonics match the formula's float64 reference within
  0.1 dB.
- **DCW sweep:** for shapes 1–5, brightness (spectral centroid) never falls as DCW
  rises. For 6–8, the resonance peak moves up with DCW.
- **Aliasing:** below −45 dB on shapes 1–5 at notes 60–108 at DCW 1; resonant waves
  below the limits measured in 3.3.
- **Continuity:** no sample step above a sine's, times a set factor, at any DCW for
  the resonant and double-sine shapes (no clicks at the cycle end).
- **Pairs:** the alternation per cycle, the sub-octave present, the
  resonant-after-resonant rule.
- **Extras:**
  - Ring Mod: DCO 1 × DCO 2 (sum and difference partials);
  - Noise Mod: noise rises with the amount, and 0 is identical to off;
  - DCO Env: pitch by Amount × envelope, and as a matrix source.
- **Pitch and envelopes:** pitch within 1 cent; envelope times within 5 %; Env Amt 1
  and 2 independent; Key Follow.
- **LFOs and matrix:** each LFO through each destination; the Init routings silent at
  Amt 0; every source to every destination at ±400 % finite and bounded.
- **Level:** an Init chord under the clip.
- **Voices:** voice allocation as stock VAP.
- **Cost:** within the budgets in section 6; under 1,000 idle.
- **Stress:** random notes, garbage parameters (NaN, ±inf, huge), swaps, restarts,
  with the memory-write watch.
- **The effect** on a PHASE slot.
- **Listening:** WAV files of each shape's DCW sweep, pairs, a resonant sweep, ring and
  noise mod, a DCO Env pluck and chords.

**Phase 3: bundles and release tier.**
- `patch/all` with the new version text.
- `run_tests.py --tier release`. synths' audio code changes, so `--tier deep` runs
  too, with its time stated first and asked about if it is over 45 minutes.
- `test_web_patcher.py --update`.
- The README, PATCHING.md, the root README and FINDINGS updated.

**Phase 4: the user's hardware check** (section 10), then the tested hashes into the
manifest and PATCHING.md.

## 9. The user's answers (first round), and what is still open

| Question | Answer | In the plan |
|---|---|---|
| Name | PD or PHASE, if it fits | **PHASE**: it fits (5.3) |
| "Resonant pulse" | The double sine | Shape 4 (3.1) |
| Env 2 tile | A matrix source, or the CZ's pitch envelope | **Both:** the DCO Env with its own Amount, and a matrix source (4.2) |
| Resonant waves at DCW 0 | As the CZ | Windowed sine; DCW moves only the resonance (3.2) |
| Extras | All of them | Mod Matrix, chorus, Ring Mod, Noise Mod, DCO Env (3.5, 4.4, 4.6) |
| Tile labels | Rename them if possible | **Possible, with one new hook** (4.1) |

Second round:
1. **The DCO Env amount:** ±48 st, as planned.
2. **WAVE's tiles:** not needed now. The hook labels PHASE's tiles only.

## 10. Hardware check (the user, after phase 3; about 25 minutes)

1. Install `sy8` alone. Config shows `1.2.0-sy8`.
2. Project Settings → Synth Engines:
   - Set WAVE replaces VAP and PHASE replaces FAT. The Model list shows WAVE and PHASE
     in those rows, and Synth 2's title reads "Synth 2 (PHASE)".
   - Set both to FAT: the one changed last stays, the other shows Off.
3. On the PHASE synth's Init patch, for each Wave:
   - DCW 1 at 0 is a plain sine for Saw to Saw-Pulse, and a soft windowed sine for the
     Resos;
   - sweeping it to 100 % brightens smoothly to the shape, or sweeps the resonance;
   - compare with the CZ-3000 by ear, especially Double Sine, Saw-Pulse, a pair
     (Wave 1+), and a Reso sweep.
4. The Edit Patch tiles read DCO, DCW, DCA, DCW Env, DCO Env, LFO 1, LFO 2, Mod, Voice,
   with icons. On a VAP or WAVE synth they read as before. Then switch the screen
   between a PHASE synth and a VAP synth a few times.
5. Check the modulation:
   - DCW Env shapes the DCW over each note, and Env Amt 1 / 2 move each DCO.
   - DCA is the level.
   - DCO Env with an Amount gives a pitch sweep.
   - Ring Mod and Noise Mod change DCO 2.
   - Raise the Init patch's two matrix slots: vibrato from LFO 1, a DCW wobble from
     LFO 2.
6. Save a PHASE patch, load it in another project. Save and reload the project. Load a
   WAVE `sy7` patch and project: they sound as before.
7. Hold an 8-voice PHASE chord on a bright shape with full reverb and delay sends, with
   `/.develop` on the card: note the CPU peak (WAVE's was 56 %). Listen to the top
   octave for aliasing.
8. Revert to stock: the PHASE synth plays FAT's Init patch, nothing else changes.
9. Then the all bundle: steps 1–2, 4 and 7 again.
