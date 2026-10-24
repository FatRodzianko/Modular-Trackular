# Custom synth engines: feasibility and plan

Can a patch add a custom synth engine that a project can choose in place of a stock one,
the way `patch/masterfx` swaps the master reverb and delay? This document answers that for
Tracker+ firmware 1.2.0 and plans the first engine: a wavetable synth with two
oscillators, one filter and two ADSRs.

This is research and planning only; nothing here is built yet. The firmware facts behind
it are in [FINDINGS.md](FINDINGS.md) sections 17 and 21. Names such as
`synth_slot_render` are in `ghidra/names_1.2.0.tsv`.

## Verdict

**It is feasible, and a wavetable engine fits.** No fallback to a virtual-analog design is
needed, though one stays ready (see "Fallback" at the end).

The evidence:

1. **The stock synths run in the emulator.** After the static constructors, the app's two
   synth setup calls (`0x600b2118`, then `0x600b421c`) run with a fake HAL object. The
   real host, slots, patches and voices then exist, notes play, and voices render inside
   the real audio graph. Engine work can therefore be tested the way masterfx was, before
   any hardware.
2. **A synth voice is one small C++ object with a 12-entry vtable in flash.**
   - The methods are: MIDI message, set parameter by index, render 128 samples, "still
     sounding", note queries, and destructors.
   - The VAP model's voice vtable (`0x600667e4`) is used only by the VAP factory.
   - Its entries are aligned flash words, so the patcher's existing word hooks (with
     `next` chaining) can wrap them, as masterfx wraps the reverb's `update()`.
3. **VAP's parameters already cover the wavetable layout.** VAP has 60 parameters: two
   oscillators (Shape, Tune, Mix), a filter (Type, Cutoff, Resonance, Env Amt), and three
   ADSRs (amp, filter, Envelope 2). The Instrument screen's synth pages, the Macros,
   patch saving and the project record all work from that list. A wavetable engine that
   reads VAP's parameters therefore gets its UI, Macros and storage for free.
4. **The CPU budget is easy.**
   - Stock voices render every block whether they play or not. Per 128-sample block a
     VAP voice costs 60,804 emulator instructions idle and 66,195 playing.
   - A prototype wavetable voice compiled with the patch build flags costs 14,209 to
     14,246 instructions playing, and 395 when idle.
   - All three synth slots together are capped at 8 voices (`0x600b1400`). So the worst
     case is 8 × 14.2k ≈ 114k instructions, against 486k–528k for 8 VAP voices.
5. **Memory is not a problem.**
   - The wrapped voices stay the stock VAP objects (same heap as stock).
   - The engine's own state is about 1.3 KB of patch RAM. In the current six-patch
     bundle (`TrackerPlus_VZ6.map.json`), `ocram_tail` has 9,360 bytes free and
     `dtcm_above_heap` 5,008.
   - The wavetables live in flash: about 32 KB per bank of 32 frames with octave levels.

The real unknowns are hardware ones, and each has a test that retires it (see "Risks and
unknowns"):
- the cost of reading wavetables from QSPI flash through the D-cache;
- the bootloader's maximum image size, which matters once the tables are added;
- the boot-time rule that masterfx found (no heavy DSP before the Pattern screen).

## How the stock synths work

### Objects

The synth system lives in the app object: `S` = app + 8 = `0x200170e8`. Each of the three
slots (S1–S3, instruments 64–66) owns three things:
- a slot object (class vtable `0x6006441c`, methods in ITCM, 0x5bc bytes);
- a patch object, which holds the parameters, at `S + 0x0c + 0x30 × slot`;
- a model id at `S + 0x30 × slot`: 1 ACD, 2 FAT, 4 VAP, 5 WTFM, 6 PERC. The project
  stores the model as an index 0..4, mapped to these ids through the DTCM table
  `0x200006b4`.

Two more pieces sit on `S`. The MIDI router `S + 0xac` (the "host" that
`seq_note_on_out_q` calls) turns note on, note off, pitch bend and CC into 4-byte
messages for the slots. The synth manager `S + 0x90` renders a slot (vtable `+0x4c`).

### Models and voices

Each model is a descriptor in a registry (`synth_models_register` `0x60140150`) with two
`std::function`s:
- a **parameter builder**, which makes the patch's parameter list and the editor pages
  (VAP `0x6014dea0`);
- a **voice factory** (VAP `vap_voice_new` `0x6014a958`, through the literal
  `0x6014dca8`).

A slot creates one voice per unit of polyphony with the factory (`synth_slot_set_voices`
`0x1e484`). It deletes them all when the model changes. The polyphony of the three slots
together is capped at 8 (`synth_slot_max_polyphony` `0x600b1400`), and each model can
cap itself further (PERC: 1).

| Model | Voice vtable | Voice object | Heap per voice | Instructions per block, idle / playing |
|---|---|---|---|---|
| ACD | `0x600662f0` | 0x26c | 3,140 B | 47,746 / 48,923 |
| FAT | `0x600674e4` | 0x344 | 3,028 B | 51,751 / 55,249 |
| VAP | `0x600667e4` | 0x398 | 6,472 B | 60,804 / 66,195 |
| WTFM | `0x60067814` | 0x3a4 | 6,484 B | 57,024 / 60,571 |
| PERC | `0x60066f90` | 0x17ac | 6,068 B | 4,333 / 34,905 |

### The voice interface

All models share this layout. The addresses are VAP's (vtable `0x600667e4`).

| Entry | Word at | Stock target | Called by the slot for |
|---|---|---|---|
| `+0x04` deleting destructor | `0x600667e8` | `0x6014b725` | Voices removed (polyphony down, model change) |
| `+0x08` `midi(voice, ctx, msg)` | `0x600667ec` | `0x6014a9a9` | Message `{type, 0, data1, data2}`: 4 note on (note, velocity), 5 note off, 8 pitch bend (16-bit, ±2 semitones), 0 and 1 (not yet identified, see the risks) |
| `+0x0c` `set_param(voice, index, float *v)` | `0x600667f0` | `0x6014a8f1` | Every parameter at voice creation (`synth_slot_push_params` `0x1e40c`), each edit or Macro change (`synth_slot_send_param` `0x1e2ac`), and Volume (index 39) = velocity / 127 before each note on |
| `+0x10` `render(voice, ctx, 0, 0, 0, float *L, float *R, 2)` | `0x600667f4` | `0x6014a8b1` | Every voice, every block, playing or not. `ctx[0]` is the tempo as a float. The voice overwrites 128 floats of L at 44,100 Hz, full scale about ±1.0. VAP is mono and leaves R alone |
| `+0x14` post | `0x600667f8` | `0x6014a811` | Voice 0 after the others; nothing for VAP |
| `+0x18` sounding | `0x600667fc` | `0x6014a81d` | Choosing a free voice for a note on. Set by a note on; cleared by VAP's render when the amp release ends |
| `+0x1c` / `+0x20` | | `0x6014a829` / `0x6014a851` | "Holds this note" / "holds any note" (held-note list, kept by `midi`) |
| `+0x24` / `+0x28` | | `0x6014a85d` / `0x6014a869` | Note queries for mono and legato (held-note list) |
| `+0x2c` | | `0x6014a8a1` | Note-list cleanup |

`voice + 4` points to the slot's patch object, so a voice's slot is the `i` for which
`S + 0x0c + 0x30 × i` equals it.

### The audio path

1. `app_audio_block` → `seq_advance` → `engine_block_update` → the engine render
   (ITCM `0x3bac`) → the graph update `0x6003061c`.
2. One graph object, the synth output (`synth_output_update` `0x60036634`), renders the
   three slots in turn: synth manager → `synth_slot_render` (`0x1f0d8`).
3. That calls every voice's `render`. With more than one voice, each voice renders into
   the slot's scratch (`slot + 0x17c` / `+0x37c`), and the slot adds the scratch into the
   output.
4. The output is scaled by 32767 into 16-bit audio blocks. For a mono model (patch
   container `+0x28` = 1) L feeds both sides.

So the format is **float, mono, 128 samples per block, 44,100 Hz, written (not added)**,
in the audio interrupt.

### Parameters and Macros

The patch object's container (patch vtable `+0x50`) holds the parameter entries:
- 0x38 bytes each: name, float value at `+0x18`, half-float min and max at `+0x1c`,
  MIDI CC at `+0x23`, display formatter;
- then 8 Macro entries.

Edits and Macros (`synth_apply_fx_q`, the FX path that `patch/interpolate` ramps) end up
as `set_param(index, value)` on every voice of the slot. VAP's 60 parameters are listed in
FINDINGS section 21; the ones the wavetable engine uses are in "The parameter mapping"
below.

### Where it is stored

- **The project record.** Each slot has a 0x40-byte record at project `+0xd00 + 0x40 ×
  slot` (`0x20204d00`), with gain, polyphony (`+4`), model index (`+8`), name, sends and
  Macro states. `synth_slot_record_check` (`0x600b1858`) resets a record whose model index
  is 5 or more.
- **The parameter values** are saved as patches: the manager registers a "Patches" library
  folder and the "Workspace". The strings and the nanopb (protobuf) code point to
  `.patch` files.
- The exact file names are not mapped yet. The plan doesn't need them, because it doesn't
  change what the stock code saves (see the risks).

### The Instrument screen

The synth editor (around `0x6011a000`–`0x6011f000`: "Edit Patch: %s", "Synth Parameters
1/2", "Synth Macros 2/2") is data-driven. It shows the pages the model's parameter builder
laid out, with each parameter's name and formatter. Choosing VAP shows VAP's pages, whatever
engine renders the sound.

## Hook design

### Options considered

1. **A sixth model (a new id).** Rejected:
   - stock firmware resets any slot record with a model index of 5 or more, so the
     project would lose that slot's settings;
   - the model list, the index-to-id table (5 entries in DTCM `.data`) and the editor
     would all need new entries.
2. **Replace VAP's factory** (literal `0x6014dca8`), so wavetable voices are new objects.
   Workable, and it would save VAP's 6.5 KB of heap per voice. But:
   - the patch would have to be a correct C++ object: the deleting destructor, and the
     `shared_ptr` at `voice + 4/+8` that the slot fills in;
   - the factory is only told the voice index, not the slot, so per-slot choices would
     need more hooks on the slot vtable.

   Kept as an option for a later version if heap matters.
3. **Wrap VAP's voice methods** (chosen). The stock VAP voices stay exactly as they are.
   The patch hooks five of their vtable words:
   - it watches the MIDI messages and parameters and still passes them on, so VAP's own
     note bookkeeping (which the slot's voice allocation reads) stays right;
   - for voices of a slot set to Wavetable, it replaces `render` and `sounding`.

   This is the masterfx pattern: one vtable word per method, `next`-chained, with stock
   behaviour whenever the choice is Stock.

### The hooks

New patch `patch/synths` (working name). Version text `1.2.0-sy1`.

| Hook | Site (old word) | What it does |
|---|---|---|
| `mt_syn_voice_render` | VAP voice vtable `+0x10`, word `0x600667f4` (`0x6014a8b1`) | Wavetable slot and booted: run the engine into L (R untouched, as VAP); otherwise `next`. Passes all eight arguments through, so the stack arguments reach the stock render unchanged |
| `mt_syn_voice_sounding` | `+0x18`, word `0x600667fc` (`0x6014a81d`) | Wavetable slot: the engine's amp envelope is not idle; otherwise `next` |
| `mt_syn_voice_midi` | `+0x08`, word `0x600667ec` (`0x6014a9a9`) | Wavetable slot: note on / off / pitch bend (and types 0 and 1 once identified) to the engine's voice state. Then always `next`, so VAP's held-note list stays right |
| `mt_syn_voice_set_param` | `+0x0c`, word `0x600667f0` (`0x6014a8f1`) | Wavetable slot: store the value (the slot's parameter copy, or the voice's velocity for index 39). Then always `next` |
| `mt_syn_voice_delete` | `+0x04`, word `0x600667e8` (`0x6014b725`) | Free the engine's entry for this voice, then `next` |
| `mt_syn_settings_folder` / `mt_syn_last_item` | Config menu constructor BL sites `0x60118B00` / `0x601193E6` (chained, as masterfx, swing, humanize and interpolate) | Project Settings → "Synth Engines": Synth 1, Synth 2, Synth 3 = Stock / Wavetable |
| `mt_syn_pattern_update` / `mt_syn_config_update` | Pattern screen vtable word `0x600403DC`, Config screen `0x6003B5F8` (chained) | Read and write `synths.bin`; the first Pattern update marks the unit booted and applies the choices |
| `mt_version_fmt` | `0x600CBF9C` | Version text |

**Chaining.** No other patch hooks the synth voice vtables. The menu and screen sites are
already chained by four patches, so the new hooks use `next` like theirs, and
`test_layout.py` checks every bundle order.

**Which slot.** Every hook finds the voice's slot by comparing `voice + 4` with the three
patch pointers at `S + 0x0c + 0x30 × i`. That is three loads and compares, done fresh on
every call, so a voice that moves to another slot or model can't be misread. Only
VAP voices reach these hooks; other models have their own vtables.

**Switching.** When a slot's choice changes (menu, project load, or the first Pattern
update after boot), the patch rebuilds that slot's voices with the audio interrupt off
(`audio_irq_disable` `0x600303d4` / `audio_irq_enable` `0x600303c8`): slot vtable
`+0x1c` with 0, then with the old count.
- The stock code deletes the VAP voices and makes fresh ones.
- It pushes every parameter to the new voices, which fills the engine's parameter copy.
- So a voice keeps one engine for its whole life. Going back to Stock always starts from
  freshly constructed VAP voices, never from ones whose render was skipped.
- Notes sounding at that moment stop: the same kind of short cut as masterfx's switch.

**Start-up.** Running heavy DSP while the unit boots hung it (masterfx, FINDINGS
section 19). So the hooks only `next` until the Pattern screen's first update, whatever
the project chose. Then the slots set to Wavetable are rebuilt and the engine takes over.

**Stock projects.** Nothing in the project changes. A project without `synths.bin`, or
with a damaged one, uses Stock for all three slots, and the hooks pass everything to VAP.
A Wavetable project opened in stock firmware plays VAP with the same parameter values: it
sounds different, but nothing is lost or reset.

## Data and settings

### `synths.bin` (per project, in the project folder)

12 bytes, the masterfx scheme:

| Bytes | What |
|---|---|
| `HSY1` | Magic |
| 3 bytes | Synth 1, 2, 3 engine: 0 Stock, 1 Wavetable (room for later engines) |
| 1 byte | Zero |
| 4 bytes | Checksum (FNV-1a over the first 8, as masterfx) |

- A missing or damaged file, or an unknown engine, means Stock, and the file is left as
  it is.
- A project without a name (not saved yet) writes no file.
- Reads and writes happen only from the screens' update methods, in pieces of at most
  256 bytes, like the other patches' files.

The choice only matters while the slot's model is VAP. With any other model the setting
is kept but has no effect. The README says to pick VAP for Wavetable.

### The parameter mapping

The engine reads VAP's own parameters, so they are saved with the patch by the stock
code, show on VAP's pages with VAP's units, and take Macros and FX.

| VAP parameter (index, range) | Wavetable meaning |
|---|---|
| Shape 1, Shape 2 (0, 1; 0..1) | Oscillator 1 / 2 wavetable position (frame morph) |
| PW 1, PW 2 (2, 3; −0.5..0.5) | Oscillator 1 / 2 wavetable bank (the range split into the shipped banks) |
| Mix (4; −1..1) | Oscillator mix, −1 only osc 1 … +1 only osc 2 |
| Tune 1, Tune 2 (5, 6; ±36 st) | Oscillator tuning in semitones |
| Detune (7; 0..100) | Oscillator 2 detune in cents |
| Finetune (8; ±100) | Both oscillators, in cents |
| Reset 1, Reset 2 (9, 10) | Restart the oscillator phase at each note |
| Type (11; 0..14) | Filter type: low-pass, band-pass or high-pass, 12 or 24 dB, by groups of the 15 values |
| Cutoff (12; 20..20000 Hz), Resonance (13; 0..1.5), Env Amt (14; ±1) | Filter cutoff, resonance, filter envelope amount (±8 octaves at the ends) |
| Attack, Decay, Sustain, Release (15–18) | Amp envelope |
| Attack, Decay, Sustain, Release (19–22) | Filter envelope |
| Glide Mode, Glide Time (35, 36) | Portamento |
| Note Track (37; ±2) | Filter key tracking |
| Volume (39) | Level and velocity (the slot sets it per note) |
| Noise (41) | White noise into the filter |
| Envelope 2, LFOs, matrix (23–34, 38, 40, 42–59) | Not used in the first version, stated in the README. Envelope 2 → wavetable position is the first candidate after it |

Which of 15–18 / 19–22 is the amp envelope is taken from their defaults (sustain 1.0
and 0). Phase 3 confirms it against VAP's own output.

## DSP design and budgets

### Engine

- **Oscillators** (×2). Wavetable banks of 32 frames × 256 16-bit samples, each frame
  with band-limited octave levels (256, 128, … 4 samples, together about twice the
  frame).
  - Per block, the level is chosen from the pitch, so the highest harmonic stays below
    about 20 kHz.
  - Per sample: linear interpolation inside the frame, and between the two frames around
    the position.
  - The position is ramped across the block, so a changing Shape or Macro doesn't step.
  - The phase is a 32-bit accumulator. Pitch = note + tune + fine + detune + pitch bend
    (±2 semitones, as VAP) + glide, computed per block with a fast 2^x.
- **Filter.** A 2-pole topology-preserving state-variable filter (stable at any setting),
  low-, band- and high-pass. 24 dB is two in series.
  - The coefficients are computed per block from Cutoff × 2^(Env Amt × filter envelope ×
    8 + Note Track × (note − 60) / 12), clamped to 20 Hz–18 kHz.
  - Resonance 0..1.5 maps to damping 2..0.02.
- **Envelopes** (×2, ADSR). Advanced once per block, with an exponential decay and
  release (times 0..10 s, as VAP's ranges). The amp envelope is ramped per sample, so
  there is no zipper noise.
  - The amp envelope's end makes the voice idle: `sounding` returns false, and the render
    writes 128 zeros (about 400 instructions).
- **Output.** Mono into L, × Volume × amp envelope, scaled to sit within 3 dB of a stock
  VAP Init patch at the same velocity.
- **Safety.** Every parameter is clamped, and NaN maps to the bottom of its range (as
  masterfx does). Filter states are flushed when the voice goes idle.

### CPU

Measured in the emulator: instructions per voice per 128-sample block. The prototype is a
throwaway build of the oscillators, filter and envelopes above with the patch compiler
flags (`-O2`, Cortex-M7 hard float). It is not in the repository.

| | Idle | Playing | 8 voices, worst case |
|---|---|---|---|
| Stock VAP | 60,804 | 66,195 | about 528,000 |
| Prototype wavetable | 395 | 14,209 (note 36) – 14,246 (note 84) | about 114,000 |

The hooks add a slot lookup (about 10 instructions) to each voice call.

For scale, from masterfx: Freeverb costs about 54,000 instructions per block, against
34,000 for the stock reverb. On the device that was about 5 points of CPU on average.

Instruction counts don't capture memory speed, and that is the one CPU risk here. The
stock voice code runs from ITCM. The engine runs from flash (cached), as masterfx's
effects do, and reads its wavetables from QSPI flash through the D-cache.
- The prototype touches at most 66 cache lines (2.1 KB) of table per voice per block, so
  8 voices touch about 17 KB.
- That is about half the D-cache if every line missed.
- Phase 2 measures this on the device before the engine grows.

### RAM

| What | Where | Size |
|---|---|---|
| Settings, file state, menu values, booted flag | patch RAM (on-chip, `ram_startup`, like masterfx) | about 64 B |
| Per-slot parameter copy (about 24 floats × 3 slots) | patch RAM | about 300 B |
| Per-voice engine state (phases, positions, filter, 2 envelopes, note, velocity, glide; 8 entries, keyed by voice) | patch RAM | 8 × about 112 B ≈ 900 B |
| VAP voices themselves | stock heap, unchanged | as stock |

Total about 1.3 KB, in `ocram_tail` or `dtcm_above_heap`. Nothing new comes from the heap.

If more than 8 VAP voices ever exist at once (see the risks), the extra ones fall back to
the stock render rather than fail.

### Flash

- **Code:** about 6–8 KB for the engine, the hooks, the menu and the file.
- **Wavetables:** about 32.5 KB per bank (32 frames × 508 samples × 2 bytes, with octave
  levels). The first version ships 4 banks (130 KB); 8 banks would be 260 KB.
- **The `.ptf` grows** about 2.8 times the binary size: Intel HEX, 16 bytes per
  45-character line. So 4 banks add about 370 KB.
- **Table source.** The banks are generated by a script in the repository from formulas
  (additive harmonics: sine, saw, square, formant-like and inharmonic sweeps). No
  third-party wavetables, so there are no licence questions.

## UI

**First version: no new screen.** The slot is set to VAP, so the Instrument screen shows
VAP's pages, which edit the parameters the engine reads:
- Shape 1/2 move the wavetable position;
- Cutoff and Resonance set the filter;
- the first two envelope pages are the amp and filter ADSRs.

The labels are VAP's ("Shape 1", "PW 1"), so the README has the mapping table above. The
engine choice itself is a Project Settings item, like Master FX.

**Optional later step: relabel.** While a slot is on Wavetable, the patch could rename the
parameter names in that slot's patch object (for example "Shape 1" → "Wave 1", "PW 1" →
"Bank 1"). They are short `std::string`s in heap entries (FINDINGS 21). Unknowns:
- whether the editor caches the names;
- whether a patch reload rebuilds them.

`tools/emu/eve.py` can render the editor's display list to check this in the emulator
before trying it.

## Risks and unknowns

| # | Risk or unknown | How to retire it |
|---|---|---|
| 1 | The render call's stack arguments beyond the four known words | Pass-through build (phase 1): every hook only chains; VAP's output must match stock sample for sample (poly 1 and 4, notes, pitch bend, Macros) |
| 2 | Message types 0 and 1, and when the slot sends them (transport stop, all notes off?) | Emulator: log `midi` while calling every host method, `seq_stop` and pattern playback; give the engine the same meaning (most likely "release all" and "kill") |
| 3 | The voice allocation depends on `sounding` | Emulator: the same note sequence (more notes than voices, overlapping releases) on stock VAP and on Wavetable gives the same voice choices |
| 4 | Rebuilding voices while notes play (interrupt off, heap churn) | Emulator: 200 switches with notes held and blocks rendering; heap use and free-list size before equal after; a write watch (as `stress_masterfx.py`) allows writes only to the patch RAM, the audio buffers and the stack inside the hooks |
| 5 | Heavy DSP while booting | Engine idle until the Pattern screen's first update (emulator test: hooks only chain before it); then a hardware boot test |
| 6 | Flash and D-cache cost of the tables | Phase 2 hardware build: 8 voices, each on a different bank and position, high notes; Config's debug CPU readout (FINDINGS 15) against 8 stock VAP voices. Fallbacks in order: fewer octave levels, 8-bit tables, VA oscillators |
| 7 | Bootloader limit on image size | Before shipping tables: a hardware test of a padded build (versiontest plus 512 KB of zeros); start with 1 bank if in doubt |
| 8 | More than 8 VAP voices from a loaded project (`synth_slot_set_polyphony` has no cap) | Emulator: apply slot records with 8/8/8 through `0x600b2080`; the engine's 8-entry table falls back to stock for extra voices |
| 9 | NaN or garbage parameters | Clamp everything; a randomized stress run (20,000 blocks of random notes, parameters including NaN and infinities, switches) with the write watch and a bounded output |
| 10 | Which envelope VAP uses as the amp envelope, and VAP's units (Detune, Env Amt, Note Track) | Emulator: set one at a time on stock VAP and watch the output; give the wavetable engine meanings that match the units the page shows |
| 11 | Export / render to file | Emulator: the render loop (`0x600bc220`) through a short synth export, so the engine works there too |
| 12 | Patch save and load unaffected | Emulator with `fakefs.py`: save and reload a project with Wavetable chosen; the `.patch` / Workspace files are byte-identical to stock firmware's |
| 13 | Interaction with `patch/interpolate`'s Macro ramps | Bundle test: a ramped Macro on a Wavetable slot reaches `set_param` every block and moves the sound |

## Build phases

Each phase ends with its emulator tests passing, `test_layout.py` passing, and the bundle
tests where noted, before anything goes to hardware.

### Phase 0: the synth rig

- Turn the emulator experiments into `tools/emu/synth_rig.py`:
  - run the two synth setups after `seq_timing.make()`;
  - set a slot's model and polyphony;
  - play notes through the host;
  - render blocks and collect each voice's output, or the synth output object's 16-bit
    blocks;
  - count instructions (a profiler that counts a function's calls inclusively);
  - measure heap use (`mallinfo` through `heap_free_q`).
- Machine's stub slots run out with these fakes, so the rig adds its own stub page.
- **Test:** the FINDINGS 21 numbers come out again, and two runs give identical stock
  output.

### Phase 1: pass-through patch

`patch/synths` with every hook installed, the menu items, `synths.bin`, the rebuild on
switch, and an engine that is a pass-through: the Wavetable choice calls `next` too.

Emulator tests (`tools/emu/test_synths.py`):
- Stock VAP output is identical through the hooks, sample for sample: poly 1 and 4,
  notes, note offs, pitch bend, Macros.
- Hooks only chain before the Pattern screen.
- Switching rebuilds voices (voice pointers change, all 60 parameters re-pushed), and
  heap use is unchanged after 100 switches.
- The menu items come from the stock menu constructor, and the stock Apply stores the
  choices.
- `synths.bin`: per project, one write after a change, damaged and unknown files, a
  project without a name.
- Other models (ACD, FAT, WTFM, PERC) are untouched: their vtables are unhooked, and
  their output is identical.

Optional hardware check for the user: boot, play VAP, switch the setting, save and reload.

### Phase 2: oscillators

The engine plays the two oscillators from one generated bank (sine → saw → square), with
a gate-only envelope.

Emulator tests:
- Pitch within 1 cent from note 24 to 108 (peak of the spectrum).
- Tune, Finetune, Detune and pitch bend move it as stated.
- Aliasing: a saw at notes 96–108 has no folded partial above −60 dB.
- Position: the spectrum moves from sine to saw to square as Shape goes 0 → 1.
- Mix crossfades.
- Reset restarts the phase.
- `sounding` is true from note on until the gate ends; an idle voice costs under 500
  instructions.
- Under 16,000 instructions per playing voice.

Hardware build for the CPU risk (#6): 8 voices worst case, Config's debug readout against
8 stock VAP voices.

### Phase 3: filter and envelopes

Emulator tests:
- Filter: the response measured with noise (the −3 dB point within 5 % of Cutoff for
  low-pass; band-pass and high-pass shapes; a resonance peak that rises with Resonance;
  stable at Resonance 1.5 and Cutoff 18 kHz).
- Env Amt sweeps up and down; Note Track follows the note.
- Envelope times: attack, decay and release times within 5 % of the parameter, at
  several settings; sustain level.
- `sounding` clears when the release ends.
- Level within 3 dB of stock VAP's Init patch at the same velocity.
- Voice allocation matches stock (risk 3).
- Glide.
- The stress run (risk 9).
- WAV files of the test notes for listening, as masterfx does.

### Phase 4: UI and settings

- Final parameter mapping, and a README with the table.
- Project load order: a project with `synths.bin` loads, the Pattern screen's first
  update rebuilds the Wavetable slots, and the sound matches a fresh Wavetable slot.
- A Wavetable project in stock firmware: the file is ignored, and the slot plays VAP
  with the same parameters (test against the stock `.ptf`).
- Optional: relabelling, checked with `eve.py` (see UI).

### Phase 5: wavetable data

- A generator script for the banks.
- Bank select through PW 1/2.
- Flash size and `.ptf` size reported by the build.
- The hardware image-size test (risk 7) before the bigger build is offered.
- Tests: every bank and frame band-limited (no octave level has partials above Nyquist
  at its pitch range), and the level matched across banks within 3 dB.

### Phase 6: bundles

- `test_layout.py` with the new patch alone and in bundles, in both orders.
- Every patch's emulator test against the bundle `.ptf`, including masterfx and
  interpolate (Macro ramps on a Wavetable slot, risk 13).
- The export render (risk 11).
- The stress run on the bundle.

### Phase 7: hardware

For the user, with the usual install and revert steps in the README:
- boot;
- play every engine choice;
- switch while playing;
- save and reload;
- export;
- the CPU readout with a heavy pattern;
- revert to stock.

## Fallback: virtual analog

If phase 2's hardware measurement shows that the table reads cost too much CPU, and
neither fewer octave levels nor 8-bit tables bring it down, the oscillators become
virtual-analog: PolyBLEP saw and pulse, with Shape morphing saw → pulse width.
- No tables, no flash growth, and no D-cache traffic.
- The filter, envelopes, hooks, settings and UI stay exactly as planned.

Nothing found so far points that way: the instruction budget has more than four times
headroom over the stock VAP voice it replaces.

## Status: built and emulator-tested (`patch/synths`, `1.2.0-sy1`)

Phases 0 to 6 are done in the emulator: `tools/emu/synth_rig.py`, the patch with its
menu, file and switching, the engine, the generated banks, and the bundle tests. The
patch's README has the user-facing mapping. Nothing has run on hardware yet.

### What differs from the plan above

- **The amp envelope is parameters 19–22**, not 15–18 (their defaults misled): 15–18 are
  the filter envelope. VAP's Init patch is a pluck (amp Sustain 0). See FINDINGS
  section 21, "Found while building `patch/synths`".
- **The render call has five stack arguments** (a buffer count and a pointer to the
  buffers after L and R). So the five voice hooks are naked trampolines rather than C
  functions. They save r0–r3, call an observer with a pointer to the saved registers and
  the caller's stack words, and either return its result or jump on through the chain
  word with every register and stack word untouched. The Phase 1 pass-through build (the
  Wavetable choice chaining too, and the VAP slots rebuilt) was identical to stock firmware
  sample for sample, poly 1 to 4, with notes, bends, CC edits and Macros. That retires
  risk 1.
- **Tables:** 7 levels of 64, 32 … 1 harmonics, but not 256 … 4 samples. The engine
  interpolates linearly, which leaves images of a harmonic h at about
  (1/h) sin²(πh/N) / (π²(1−h/N)²) for a saw. With 4 samples per harmonic that came to
  −57 dB, short of the −60 dB target. The levels now have 352, 240, 168, 120, 80, 56 and
  40 samples, the smallest multiples of 8 that keep a saw below −63 dB. Not powers of
  two: the engine scales the phase by N, one multiply per sample. That makes 66.5 KB a
  bank.
- **7 banks and a super saw**, modelled on the PPG Wave and ESQ-1 (digital wavetables,
  analog-style filters). The banks are Formant, Resonant, Harmonic, PWM, Basic (sine,
  triangle, saw, square), Sync and Digital (two-operator FM). Their tables take 467 KB,
  the whole patch 476 KB, and the `.ptf` grows by 1.3 MB. The seven-patch image ends at
  `0x6023a2c8`, 2.2 MB into the flash (stock 1.7 MB), inside the 8 MB tested on
  hardware (risk 7). For that, `tools/emu/machine.py` now maps 8 MB of flash, not 2.
  The first build had 3 banks only, to fit the old 2 MB map.
- **Bank select:** PW −0.5 … 0.5 is split into eighths: the 7 banks in the order above,
  then the super saw. PW 0, VAP's default, falls on Basic.
- **The super saw:** 7 saws from one band-limited saw table, each with its own phase
  increment. The six side saws are at 0.6 of the centre's level, with relative detunes
  from published analyses of the JP-8000's; Shape² scales the detune. At Shape 0 the
  increments are exactly equal (an integer step, not a float product), so it is one
  steady pitch.
- **Digital mode** (VAP's Sync, parameter 40, which the engine had left unused): the
  nearest frame, the nearest sample, the low 8 bits dropped, as the early digital
  wavetable synths played their tables. Not for the super saw.
- **The filter flavours:** VAP's type names now pick the model.
  - MG (type 0 only, LP 24 dB) is a ladder: four TPT one-poles in a loop, solved as if
    linear, with a soft clip on the loop's input. That bounds the self-oscillation at
    Resonance 150 % (k 4.2).
  - OB is the SVF with its band-pass state through the same soft clip, so a loud
    resonance compresses.
  - SVF stays the clean one.
  The ladder loses passband level as k rises; 85 % of it is made up, which keeps VAP's
  Init patch (Type 0) at VAP's level.
- **`synths.bin`'s checksum** is FNV-1a over bytes 4–7, exactly as masterfx's (the
  "first 8" above meant the same data). An unknown engine makes the whole file count as
  missing (Stock), as masterfx does.
- **Engine voices:** a free engine voice also goes, at the next screen update, to a VAP
  voice of a Wavetable slot that had none (one made while all 8 were taken, playing stock
  VAP), once VAP has it silent.
- **Envelope units:** Attack is the time to full level; Decay and Release the time to
  fall 60 dB. That matches VAP's release up to about 1 s (VAP's longer releases end
  sooner). The engine's voice ends at −60 dB, as VAP's "sounding" does, so voice choices
  match.
- **Level:** the engine's output gain is set so VAP's Init patch and the Wavetable
  engine with the same parameters are within 1.2 dB at notes 36–84 (measured +6.4 dB of
  gain needed). With the ladder on Type 0 they are within +0.6 / −1.0 dB.
- **CPU:** the engine renders in passes: each oscillator into the output (its two frames
  fixed for the block when the position stays between them), the noise when Noise is
  above 0, then the filter and amp in place, with fused multiply-adds
  (`-ffp-contract=fast`). A playing voice costs 9,641 instructions per block in Digital
  mode, 11,817 with the clean 12 dB filter, 14,813 with VAP's Init patch (the ladder)
  and 17,894 with OB 24 dB. Both oscillators on the super saw cost 45,715 to 49,825. An
  idle voice costs 285. A first single-pass loop cost 20,000.

### Risks

| # | State |
|---|---|
| 1 Render stack arguments | Retired (emulator): trampolines, pass-through identical to stock |
| 2 Message types 0 and 1 | Retired: MIDI clock and start, from the router's realtime broadcast (host `+0x08`), for VAP's synced LFO. The engine ignores them and VAP still gets them |
| 3 Voice allocation | Retired (emulator): the same voice for every note on as stock VAP, 77 of 77, with the Init patch and sustained patches at Release 0.5 and 1.0 |
| 4 Rebuilding while notes play | Retired (emulator): heap use and engine voices the same after 200 switches with notes held; the stress run's write watch over switches, polyphony and model changes and restarts |
| 5 Heavy DSP while booting | Emulator: every hook only chains before the Pattern screen's first update (identical output with Wavetable chosen). Hardware boot test open |
| 6 Flash and D-cache cost | Open (hardware). In the emulator a playing voice costs 9,641 to 17,894 instructions per block, 45,715 to 49,825 with both oscillators on the super saw (idle 285), against VAP's 60,804–66,195. The tables are now 467 KB, so cache misses on the table reads matter more than with 3 banks |
| 7 Image size | Retired up to 8 MB (hardware): the flash is 16 MB (the boot header and the flash controller both say so), and padded images reaching 2, 4 and 8 MB of flash installed and read back whole (FINDINGS section 8). The seven-patch image with all 7 banks ends at `0x6023a2c8` (2.2 MB of flash, stock 1.7 MB). The emulator now maps 8 MB of flash (`tools/emu/machine.py`), the size tested |
| 8 More than 8 VAP voices | Retired (emulator): 13 VAP voices (as many as the heap holds; 8/8/8 runs it out in stock firmware too) give 8 engine voices, the rest play stock VAP |
| 9 NaN and garbage parameters | Retired (emulator): the stress run (2,000 blocks in every test run, 20,000 on the seven-patch bundle; garbage to Wavetable voices, in-range values to stock ones, as stock VAP itself hangs or faults on some garbage) finds no fault and no write by the patch outside its RAM, the output buffers and the stack; all 60 parameters NaN give bounded output, and the Init values make it play again. Two failures on the way were the rig's: an emulated restart must also give the slots fresh voices, as a real start does, and must not take the patches' menu nodes from the heap again (19 nodes a restart in the bundle filled the heap, and stock VAP then corrupted it) |
| 10 Which envelope, VAP's units | Retired: see above |
| 11 Export | Retired (emulator): the render loop (`0x600bc220`) renders a Wavetable slot (bundle tests) |
| 12 Patch save and load | Not tested (the emulator doesn't run the patch save path). By construction: the patch never writes the patch objects, their parameters or the `.patch` files; it only reads parameters as `set_param` passes them, and writes `synths.bin` |
| 13 Macro ramps | Retired (emulator): a Macro stepped every block reaches `set_param` on the Wavetable voice every block and moves the sound |

Open for the hardware session: boot (risk 5), and the CPU readout with 8 Wavetable
voices against 8 stock VAP voices, including super saw voices (risk 6). Patch save and
load (risk 12) is untested but safe by construction.

### First hardware test (`1.2.0-sy1`)

- **Sound:** it booted and played. Holding two or more notes gave loud screeching, from
  the pads and from an external MIDI keyboard alike. The emulator doesn't reproduce it:
  its renders of the same chords are clean. Ruled out in the emulator:
  - Wraparound in the 16-bit conversion: `0x60036750` multiplies by 32767 and stores the
    low 16 bits with no saturation, but an earlier stage clips the slot's output at
    about 0.998 (peaks 32,694), for stock VAP and the engine alike.
  - Host `+0x18` (message type 9) and `+0x14` (type 10): the first only asks the voices
    "any key held", and the second reaches no voice.
  Still open: timing on the device, which the emulator doesn't model (code and tables
  read from flash; the render racing a note on).
- **A bug found on the way:** `entry_new` enables the audio interrupt again when it
  returns, even inside `rebuild` or a stock path that disabled it. That gives a window
  where a block renders a half-built slot. It should restore the previous state.
- **The selection is the wrong design.** On the Instrument screen nothing shows that the
  engine changed, the controls keep VAP's names, and picking a bank through PW can't be
  discovered. The user wants swappable engines instead:
  - Config lists the engines, and the user picks which of the five stock models a
    custom engine replaces.
  - The model picker then shows the new engine's name ("WAVE") in that model's place,
    with the new engine's own controls.
  A rework along those lines replaces this design. Section 17's descriptor swap is the
  way in.

## Redesign: a swappable WAVE engine

The user's requirement after the first hardware test:
- A custom engine replaces one of the five stock models, chosen per project (or
  globally if per-project can't be done).
- The Model list then shows the engine's own name ("WAVE") in that model's place.
- The engine shows its own controls.

FINDINGS section 23 maps what this needs; per-project works.

### How

1. **WAVE gets its own model id, 3** (free in the registry). A chained hook on the
   registry builder (`0x60140150`) inserts one more descriptor at start-up, named
   "WAVE". A model id of its own is required: stock firmware rejects a Workspace patch
   whose id it doesn't know and falls back to the stock model's Init. With the replaced
   model's id, the stock model would parse WAVE's values (section 23).
2. **WAVE's builder** calls VAP's builder and then rewrites the container:
   - parameter names, ranges, defaults, flags and value displays (the formatter
     `std::function`s become the patch's own, for example bank and filter names);
   - the pages (WAVE's pages, the extra ones destroyed);
   - the Macros;
   - the model id (3).
   The entry count stays 60, so the index positions in patch files stay fixed.
3. **The voices stay VAP voice objects.** WAVE's factory is VAP's. The existing
   trampolines run the engine for a voice whose patch has model id 3, not for a slot
   chosen in a menu. For those voices `set_param` stops chaining to VAP: WAVE's values
   don't fit VAP's ranges, and VAP faults on some out-of-range values. The note
   bookkeeping still chains.
4. **The swap is per project.**
   - Project Settings gets one item: "Wavetable replaces: Off, ACD, FAT, VAP, WTFM,
     PERC". It is saved in the project's `synths.bin`.
   - At project load, before the slot records are applied (the BL to `0x600b2080` in
     `0x60116350`), the patch reads that file and sets the replaced index's entry in
     the index table (`0x200006b4`) to 3. A different project restores the entry.
   - A slot whose model changes is forced past `synth_slot_set_model`'s same-id early
     return, so it gets a fresh patch.
   - A change in the menu does the same at once, and the affected slots get WAVE's
     Init patch.
5. **The Model list** switches to text items, with "WAVE" in the replaced position.
   A draw hook that keeps the stock logos and draws text only for WAVE is the nicer
   variant.
6. **Where things land:**
   - The instrument title becomes "Synth 1 (WAVE)" with no extra work.
   - WAVE patches save to `Patches/WAVE/`.
   - In a project, the slot's `<slot>.patch` holds model 3.
   - Stock firmware sees the stock index and the stock model's Init patch.
7. **The old per-slot Stock/Wavetable items go.**

### WAVE's controls (proposal)

| Page | Controls |
|---|---|
| Oscillators | Wave 1, Position 1, Wave 2, Position 2, Mix, Tune 2, Detune, Digital (Off/On). The wave names are Formant, Resonant, Harmonic, PWM, Basic, Sync, Digital and Super Saw; on the super saw, Position is its spread |
| Filter | Type by name (Ladder 24, OB LP 24 / 12, SVF LP 24 / 12, …), Cutoff, Resonance, Env Amt, Key Track |
| Amp Envelope | Attack, Decay, Sustain, Release |
| Filter Envelope | Attack, Decay, Sustain, Release |
| Voice | Glide Mode, Glide Time, Noise |

**The sound:** the user found the first build loud and harsh. WAVE's Init patch will
be a sustained Basic saw through a moderately open ladder with low resonance, at a
lower output level than the VAP-matched one.

### Still to map while building

- The parameter flags for stepped and list parameters (copy VAP's Type entry).
- The format of a Macro's targets.
- Who reads the group vector (container `+0x44`).
- Whether model id 3 is safe everywhere a model id indexes something.
- An emulator harness for the instrument screens: their controls are created outside
  `0x6011ae60`, so they can be rendered and checked before hardware.

### Tests (emulator first)

- The descriptor is registered, and the swap remaps the index table (the Model list,
  set_model, the title).
- WAVE's container: its names, ranges, pages and value displays.
- A patch save and load round trip under model 3.
- Projects: load with and without the swap, and switching between them. Stock
  firmware opening a WAVE project falls back to the stock model's Init patch.
- All of the engine tests above, plus the bundles and `test_layout.py`.

### Built: `1.2.0-sy2` (emulator-tested; not on hardware yet)

The redesign is built as planned. These parts differ from the plan, or were mapped on
the way (FINDINGS section 23, "Found while building WAVE"):

- **The Macros** are filled after the builder, by a `std::function` the builder leaves
  at container `+0x2c`. WAVE's builder replaces it with one that runs VAP's and then
  sets WAVE's Macros:
  - a: Attack;
  - b: Decay/Release on both envelopes;
  - c: Wave Position;
  - d–f, Velocity and Pressure: as VAP's.
- **Values and defaults:** a patch's current values are a `vector<float>` at patch
  `+0x0c`, and the entries' `+0x18` hold the defaults. The builder sets the defaults,
  and the current values start as copies of them.
- **The screen layout** (groups) uses VAP's cell positions, three to a row (oscillator
  1, oscillator 2, both). The tabs are Oscillator (1/2, 2/2), Filter, Amp Env,
  Filter Env, FX and Voice. The Macro editor's pages are Oscillators, Filter, Amp
  Envelope, Filter Envelope and Voice.
- **The Model list keeps the stock logos.** The patch draws "WAVE" as text in the
  replaced row (the nicer variant). The text is centred where the replaced logo was,
  scrolled too.
- **Extra voices are silent.** A WAVE voice beyond the 8 engine voices renders silence
  until one is free. It no longer falls back to VAP, because WAVE's values never
  reach VAP.
- **The interrupt fix:** the patch turns the audio interrupt off only when it is on,
  and restores it only if it turned it off. Inside stock code that has turned it off
  (synth_slot_set_model), the patch enables nothing.
- **Level:** the output gain is 8 dB lower than sy1's. One held note of the Init patch
  plays at about −22 dBFS, and a four-note chord at full velocity peaks at 0.78 of
  full scale. The slot clips at 0.998; sy1 clipped on three-note chords.
- **The Init patch:** two Basic saws (Position 0.67), Detune 8 c, through LP Ladder 24
  at 2,400 Hz, Resonance 20 %, Env Amt 15 % and Key Track 50 %.
  - Filter envelope: A 0.005, D 0.8, S 0.35, R 0.5.
  - Amp envelope: A 0.005, D 0.5, S 0.8, R 0.4.
- **Model id 3:** checked in the emulator in the title, the synth settings screen,
  patch save and load (Patches and Workspace), set_model, the voices and project
  records. No stock code turns a live model id back into an index.
- **Robustness:** a screen update before the synth system exists does nothing. Some
  other patches' tests run the screens without it.

Still open (hardware only):
- **Boot:** a project with WAVE loads at start-up, and WAVE stays silent until the first
  Pattern screen.
- **The screens:** the emulator checks the data the Instrument screen's synth pages are
  drawn from, and the Model list's drawing, but not the pages themselves.
- **CPU** with 8 WAVE voices against 8 stock VAP voices, super saw voices included.
- **The sound** after the level change.

### Built: `1.2.0-sy3` (emulator-tested; not on hardware yet)

`sy2` worked on hardware: it installed, swapping worked, and WAVE patches saved and loaded
across projects. From that test, the user asked for:
- the Edit Patch screen without the replaced model's logo;
- LFOs (two);
- modulation of the wavetable position by LFO and envelope, on a page like VAP's Matrix;
- a plain saw.

`sy3` adds:
- **Two LFOs per voice**, with VAP's controls and VAP's slots (27–34): Ratio (Hz, or a
  tempo division with Sync on, VAP's Ratio display), Waveform (Triangle, Sin, Ramp Up,
  Ramp Down, Square, Random S&H), Retrigger (Note, One Shot, Free Running) and Sync.
- **A 6-slot Mod Matrix** on VAP's slots 42–59:
  - sources Amp Env, Filter Env, Mod Env, LFO 1, LFO 2, Velocity and Key;
  - destinations Position (both, 1 or 2), Pitch (both, 1 or 2), Mix, Cutoff, Resonance,
    Volume and Noise;
  - Amt ±400 %.

  The Init patch has LFO 1 → Position and Mod Env → Position at Amt 0, ready to raise.
- **A mod envelope** on VAP's Envelope 1 slots (23–26). The user didn't ask for one; it is
  an extra source, and the matrix takes the amp and filter envelopes as well.
- **Saw**, a 9th wave: the super saw's centre saw alone. Position does nothing on it.
- **VAP's tabs:** Oscillator, Filter, Modulation (Amp Env, Mod Env/LFO 1, Flt Env/LFO 2),
  Matrix, FX and Voice. The Macro editor has 9 pages.
- **The Edit Patch screen** shows "WAVE" instead of the replaced model's logo. The logo is
  a label control's icon, and a hook on the call that sets the label's text clears the
  icon and sets "WAVE" (FINDINGS section 23).

Changed on the way:
- **Random start phases.** Each voice's oscillators start at a phase from its address.
  Saws starting in step made a four-note chord on fresh voices peak at 0.94–0.99 of full
  scale; now 0.75–0.76.
- **Off writes no file.** Save As or a new project with WAVE Off no longer writes
  `synths.bin` (the humanize tests on the bundles caught the write).
- **Free Running LFOs run while the voice is silent.** An idle voice costs 505
  instructions per block (sy2: 285).
- RAM 1,440 → 2,032 bytes: the LFO and mod envelope state, and the parameter copies'
  new entries.
- The emulator tests that compare spectra start both oscillators at phase 0 (Reset on),
  and the filter response is measured against the engine's own noise, regenerated per
  block. With random phases and noise seeded from the voice's address, both depended
  on where a bundle placed the patch's RAM.

Still open (hardware only):
- **Boot:** a project with WAVE loads at start-up, and WAVE stays silent until the first
  Pattern screen.
- **The screens:** the Matrix, LFO and Mod Env pages, and the Edit Patch label. The
  emulator checks the data they are drawn from and the label's drawing, not the pages.
- **CPU** with 8 WAVE voices against 8 stock VAP voices, super saw voices and matrix
  slots included.
- **The sound** of the LFOs and the matrix.

### Built: `1.2.0-sy4` (emulator-tested; not on hardware yet)

`sy3` worked on hardware: the user found the sound good and the controls clear. They
noticed that an LFO swings both ways, so from Position 0 half of each cycle rests at 0.
They asked for a Bipolar/Unipolar switch on each LFO page.

- **Polarity** on each LFO: Bipolar (−1 to 1, the default) or Unipolar (0 to 1). It is
  the last item of the Edit Patch screen's LFO 1 and LFO 2 pages, and also sits in the
  empty cell before each LFO's Sync in the groups (container `+0x44`).
- **Found on hardware:** the first `sy4` put Polarity only in the groups, and the user
  couldn't find it. The Edit Patch screen (tiles Osc, Filter, Amp Env, Env 1, Env 2,
  LFO 1, LFO 2, Mod, Voice, M1-M7) lists the *pages* (container `+0x50`), skipping empty
  cells: its LFO 1 page shows Sync, Ratio, Waveform, Retrigger. The pages had been taken
  for the Macro editor's. LFO 2's page gets a fifth cell (a 40-byte heap buffer).
- **Two parameters past VAP's 60** (60 and 61), the first parameters WAVE adds rather
  than renames. Before building, the emulator mapped every use of the parameter count
  (FINDINGS section 23, "Adding parameters to a model"):
  - The patch's per-parameter arrays are sized from the builder's count.
  - VAP's builder resets the vector to 60 on every build, and WAVE's appends its two
    through the firmware's own vector code. So a WAVE patch has 62 every time.
  - Patch files keep values by position. A file with more values loads in `sy3` without
    the extras; a file with fewer leaves the rest as they were. A hook on the patch
    storage's load sets both switches to Bipolar before a WAVE patch loads, so a `sy3`
    patch loads as it played.
  - A guard keeps an index past 59 from reaching VAP's set_param (its handler table has 60).
  - The 20,000-block stress run caught an allocation failure: growing the vector from 60
    to 62 doubled its room to 120 entries (6.7 KB). A new WAVE patch's vector is now made
    62 long before VAP's builder runs, which keeps the room and needs no reallocation.
- RAM 2,032 → 2,048 bytes (each slot's copy holds the two switches).

Still open (hardware only):
- **What the groups (container `+0x44`) drive.** The Edit Patch screen lists the pages;
  which screen shows the groups isn't known yet. The emulator can't draw these screens.
- Boot with a WAVE project, and the CPU with 8 WAVE voices (as for `sy3`).

`sy4` on hardware (user, alone and in the MTV bundle): it installs and plays, `sy3` patches
load and play, and the Polarity switches show at the end of the Edit Patch screen's LFO
pages and work as expected. CPU (user): 8 WAVE voices playing, an LFO moving Position,
full reverb and delay sends: the CPU use peaked at 56 %. Still open: boot with a
WAVE project.

### Built: `1.2.0-sy5` (emulator-tested; not on hardware yet)

The user found WAVE's filter envelope on the Edit Patch screen's Env 2 tile, while stock
VAP has its filter envelope on Env 1. Measured in the emulator, VAP's filter envelope is
parameters 23–26 (Env 1). WAVE had taken 15–18 for its filter envelope from an inference
in FINDINGS that turned out wrong (FINDINGS section 23, "VAP's envelopes, measured").

The user chose the display-only fix: the Env 1 tile and the "Flt Env/LFO 1" tab show WAVE's
filter envelope (15–18), and Env 2 and "Mod Env/LFO 2" its mod envelope (23–26). The engine,
the Init patch, the Macros and saved patches are unchanged. The one difference from VAP left:
WAVE's filter envelope answers MIDI CCs 46–49, where VAP's answers 80–83.

`sy5` on hardware (user): alone and in the all bundle (`1.2.0-MS5`), the filter envelope on Env 1; every mod in MS5 works.

### Built: `1.2.0-sy6`, Drive (tested on hardware)

The first of the effects the user asked to explore. Drive (0–100 %) on the Filter page and
tab, after Key Track: the filter's input times 1 to 16 (+24 dB) through the soft clip the
filters already use, scaled so a held note at full velocity keeps its peak. At 0 the stage
isn't run: the output is sample for sample `sy5`'s (emulator, four filter types). It takes
VAP's parameter 38 (Mix Env Amt), never shown by WAVE and 0 in every WAVE patch, so no
parameter is added and saved patches are unchanged. Cost about 2,200 instructions per voice
per block; RAM 2,048 → 2,064 bytes, which leaves 16 bytes in DTCM in the all bundle.

`sy6` on hardware (user): alone and in the all bundle (`1.2.0-MS6`): old WAVE patches sound
the same and load at Drive 0 %; Drive is audible, and stays under control with Resonance
above 50 %.

### Built: `1.2.0-sy7`, the effect with a Chorus (tested on hardware)

The user chose to build the effect selector first, with None and a Chorus, and to add more
effects to it once that works. Parameters 62–65 of WAVE's own: Effect (None, Chorus), Rate
(0.05–5 Hz), Depth and Mix, on the Voice page and tab after Volume. The effect runs once
per slot on its summed output (voice 0's `+0x14`), and a WAVE patch has 2 output channels;
with Effect None, R is a copy of L, so the outputs are `sy6`'s. The chorus is after the
Juno's: one delay line, two taps a triangle LFO moves in opposite directions around
3.5 ms (±1.85 ms at Depth 100 %), each through a 9 kHz one-pole low-pass, mixed with
equal-power gains. Its state is in a late RAM block (a patcher change: `"late_ram"`,
also in the web patcher, the layout test and the test runner's cover set): 6,288 bytes,
in SDRAM in the all bundle, touched only after boot. Patch files saved before `sy7` (62
values) load with Effect None. Cost about 8,900 instructions per slot per block with the
Chorus, 840 with None.

Emulator: release and deep tiers passed. `sy7` on hardware (user): alone and in the all
bundle (`1.2.0-MS7`), both load and work, every mod in MS7 too.

Next (planned, not built): Flanger and Phaser as more Effect choices, on the same
parameters and delay line (512 samples, 11.6 ms).
