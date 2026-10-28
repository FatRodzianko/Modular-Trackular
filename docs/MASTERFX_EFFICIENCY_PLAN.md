# Master FX efficiency

A profile of all six masterfx effects (the reverbs Freeverb, Plate, Spring, Shimmer and
the delays Tape, BBD, "Gated") and a plan to make them cheaper, following the timing
headroom work (`docs/TIMING_HEADROOM_PLAN.md` section 14, FINDINGS section 31). Nothing
here is implemented yet.

## 1. Why

On hardware, patch/all with every feature on peaks at 66.5 % of an audio block against
stock's 46.0 %, and about 18 of those ~20 points are masterfx with the two heaviest
effects chosen (Shimmer about +7 over the stock reverb, "Gated" about +10 over the
stock delay). Each allocated synth voice adds ~5 % even when silent, so synth-heavy
projects reach dropouts sooner than stock. The cost model from the headroom runs:
masterfx code costs about **k = 4.5 cycles per emulated instruction** (code runs from
flash, the delay lines are in SDRAM, and the synth render evicts both caches every
block), and **1 % of a block is about 15,300 cycles, so about 3,400 instructions**.

The emulator numbers below reproduce the hardware delta exactly: (59,326 − 34,152) +
(43,434 − 8,040) = 60,568 more instructions than the stock pair for Shimmer + "Gated",
and the headroom runs read about 274,000 cycles ≈ 17.9 points for that swap — 4.5
cycles each.

The project-wide worst case is the heaviest reverb plus the heaviest delay. Today that
is Shimmer + "Gated" (+60.6k), but the next pairs are close behind (Freeverb + BBD
PingPong: +51.3k), so capping the worst case means treating every effect, not just the
top two.

## 2. How the profile was taken

Unicorn, on a masterfx-only build, with the test rigs the release tier uses
(`tools/emu/reverb_rig.py`: `Rig(reverb=…)`, `DelayRig(delay=…)`), noise-burst input,
6 warm-up blocks, then 4 profiled blocks averaged:

- a code hook counting instructions per PC, mapped to stages through
  `prebuilt/masterfx.sym` and the loop structure in the disassembly;
- memory hooks counting every load and store, classified by region (DTCM, OCRAM, flash,
  SDRAM) and by buffer (the effect's own lines, read from its state struct).

One Unicorn pitfall for anyone repeating this: add the memory hooks **before** any
emulation (or flush the TB cache after adding them) — translation blocks compiled
without hooks keep running without them, and reads come out undercounted 10–100×.

## 3. The numbers

Instructions per 128-sample block (delays also with PingPong, which costs extra only
there), the delta against the stock effect in that slot, and that delta on the device
at k = 4.5:

| effect | insns/block | over stock | device |
|---|---|---|---|
| stock reverb | 34,152 | — | — |
| **Shimmer** | **59,326** | +25,174 | **+7.4 pts** |
| **Freeverb** | **53,862** | +19,710 | **+5.8 pts** |
| **Plate** | **49,014** | +14,862 | **+4.4 pts** |
| **Spring** | **42,082** | +7,930 | **+2.3 pts** |
| stock delay | 8,040 (PingPong 7,912) | — | — |
| **"Gated"** | **43,434** | +35,394 | **+10.4 pts** |
| **BBD** | 25,386 / **39,571 PingPong** | +17,346 / +31,659 | +5.1 / **+9.3 pts** |
| **Tape** | 20,381 / **31,005 PingPong** | +12,341 / +23,093 | +3.6 / **+6.8 pts** |

Where the time goes, per effect (from the PC histogram; loop figures are per block of
128 samples):

- **"Gated"** — tap table rebuilt every block 1,180; send diffusers 7,050 (55/sample);
  the 2×20-tap burst 20,350 (159/sample, already integer Q14, 19 insns per 5-tap MAC);
  6 smear allpasses + tone 14,730 (115/sample, float). SDRAM: ~13 KB reads/block
  (stock delay ~1 KB).
- **Shimmer** — one ~460-insn/sample loop: output taps ~95, tank ~145, pitch shifter
  ~75, input diffusers ~60, predelay ~25, mix ~40. 1,555 flash literal loads (6.2 KB)
  per block. ~34 ring accesses per sample, each paying ~3 insns of int16↔float
  conversion and ~3 of `movw/add/ubfx` address masking — the overhead, not the DSP, is
  most of the cost.
- **Freeverb** — predelay/mono loop 4,992; the 8 combs 30,976 (57 % of the total: two
  4-comb group passes per channel, ~15 insns per comb per sample, float damping +
  `sat16` each); the 4 allpasses 11,520; mix 4,860. Already splits its passes at the
  line wraps (no per-sample masking) — the float conversions are what is left.
- **Plate** — Shimmer minus the shifter: main loop 36,736 (287/sample) + output taps
  7,680 + predelay/mix ~4,600. Same conversion-and-masking tax (28 ring accesses per
  sample), 662 flash literal loads.
- **Spring** — the dispersion chain is the bulk: 2 springs × 32 allpass stages × 32
  steps/block = 2,048 iterations at 11 insns = 22,528 (54 %); the spring step's
  remainder ~6,300; the per-sample 44.1 kHz section (predelay, send filters,
  interpolation, mix) ~11,600 (91/sample). Already quarter-rate. Its allpass states
  are float pairs in SDRAM: 18 KB read + 17 KB written per block, the largest SDRAM
  traffic of any effect.
- **Tape** — ~155 insns/sample mono, ~235 with PingPong (a second playback head +
  filters). 4-point Hermite playback, float filters; 1,571 (mono) / 2,595 (PingPong)
  flash literal loads per block — 12–20 constants re-fetched per sample.
- **BBD** — ~200 insns/sample mono, ~310 PingPong: per sample and line it runs two
  float biquads (anti-alias + reconstruction), the sample-and-hold, the Hermite read,
  saturation and two one-pole filters. 1,431 / 2,327 flash literal loads per block.

## 4. The two levers, and what they're worth

The same two findings repeat in every effect:

1. **Fixed-point inner loops.** Every line already stores int16, but the math is
   float, so every access pays `ldrsh/vmov/vcvt` in and `vcvt/ssat/strh` out, and
   every coefficient is a float literal re-fetched from flash (up to 6–10 KB per
   block) because the render evicted the cache. Q14/Q15 integer math removes the
   conversions and the literal traffic. Rounding sits at or below the existing
   int16 line quantization, so the sound is unchanged in practice; filters that
   carry float state across blocks (saturation, glide, the shifter's limiter) can
   stay float where precision matters.
2. **Pointer walks split at the wrap, instead of per-access masking.** Plate, Shimmer
   and Gated's stages 1/3 mask every access (`movw/add/ubfx`, ~3 insns each, ~28–34
   accesses per sample). Freeverb and Gated's stage 2 already show the fix: compute
   each line's wrap point once per block and run plain pointer loops between wraps.
   Bit-exact, no sound change at all.

### Why the stock effects are cheaper (read 2026-10-27, emulator-profiled)

Disassembly of the stock pair (`master_delay_process` 0x600322B0,
`master_reverb_process` 0x600FF800), to settle where their prices come from:

- **The stock delay (7,994/block, ~62/sample) is a far smaller algorithm, in coarse
  integers.** One echo tap into three 2^18-sample rings plus a one-pole, all integer:
  feedback is Q8 (`x 255`), the filter Q10, saturation and shift fused into single
  `ssat rd, #16, rn, asr #k` instructions. Gated buys three diffusion allpasses x two
  stages, 40 tap-MACs and ducking per sample — the gap is the effect, not the
  patching. And the coarse math isn't free: the stock delay's floor-rounded Q8
  feedback holds a **permanent ±4 limit cycle** (measured: 20 s after a burst its
  output still peaks at 4) — exactly the artifact our tests forbid and the toward-zero
  quantizers exist to prevent.
- **The stock reverb (33,220/block, one straight 255-insn/sample loop) is the same
  kind of algorithm as Plate — and float inside, like ours.** Per sample it does 15
  line reads, 13 writes, 48 float mul/mla, 35 vcvt, 11 ssat, 23 ubfx ring masks: a
  Dattorro-style tank with LFO-modulated taps, in one 16K-sample ring. It is cheaper
  than our Plate (326/sample) mainly by being smaller — fewer lines and far fewer
  output taps — not by any technique we lack. Its heavy vcvt count shows Polyend pays
  the same int16-line conversion tax we do.
- **The patching process costs nothing measurable.** Our hooks replace a vtable
  call; the per-block entry/exit overhead is a handful of instructions in both
  profiles. All the cost difference lives in the inner loops.

What transfers: nothing structural we don't already do (block passes, int16 lines,
single-insn saturation) — the stock price points come from smaller networks and from
accepting floor-rounding artifacts. Our held-back audible options (fewer taps,
half-rate tank) are precisely the stock trade, available if the user ever wants them.

## 5. The plan, effect by effect

Estimates ±20 %, device points at k = 4.5 (conservative — less SDRAM and flash traffic
should also pull k down, which only the hardware `headroom` diagnostic can confirm).

### Gated (DONE 2026-10-27; 43,434 → 38,891, → 38,596 with FMA fusion)

- **G1** cache the tap table between blocks (a 332-byte `late_ram` block; rebuilt when
  length, Feedback or PingPong move): **−1,090** measured, byte-identical renders.
- **G2** integer diffuser/smear stages (0.6 = Q15 19661, 0.5 = a divide; the Filter
  lowpass stays float): **nearly a wash** once done right. The first version used
  cheap shift quantizers and measured −5,275, but an allpass that quantizes inside
  its feedback must round **toward zero** (magnitude truncation), or a ±1 circulates
  forever: the fast version failed `silence after the burst` (peak 3), and a variant
  with plain `asr` in the smear held a ±1 output for 8+ seconds of silence. With
  toward-zero quantizers everywhere (`/ 32768`, `/ 2` — C division), tails decay to
  exactly 0 and the stages cost about what the float ones did (float stage 3 was
  115/sample; clean integer is 110). The win that remains is the float-literal
  traffic.
- **G3** paired tap MACs: every tap forced to an even number of samples back (the
  write position is always even, so 32-bit reads stay aligned under
  `-mno-unaligned-access`), one word = two samples, `smlabb` per gain. **−3,400**
  measured. The parity rounding shifts each tap ≤ 1 sample: worst 10 ms envelope
  difference 0.57 dB.
- Also: the 9 allpass lines moved from the full 512 KB ring to their own 4 KB ring
  (`GT_AP_RING`) — output-identical, zero instruction change, but the device working
  set shrinks by ~500 KB of cache-hostile SDRAM walking.
- Result: 43,434 → **38,891 measured** (+10.4 → **+9.1 pts**); long-silence output
  exactly 0 at every knob setting tried. Well short of the ~24,500 estimate: the
  addressing/conversion savings largely vanished once the quantizers were correct,
  and what remains of stage 2 (11.5k) is tap count. The cache-side wins (4 KB ring,
  halved tap loads, fewer flash literals) don't show in instruction counts; the
  hardware `headroom` run will say what they're worth.
- **Lesson for the remaining effects**: any integer quantizer inside a feedback loop
  (Freeverb's combs and allpasses, Shimmer/Plate's tank, Spring's dispersion chain)
  must round toward zero, and the expected saving there is the conversion/literal
  traffic only — feedforward paths (output taps, input sums) are where integer math
  pays cleanly.
- Held back (audible): fewer taps, 20→12 a side, about −4,600 ≈ 1.4 pts more.

### Shimmer — DONE, 59,326 → 52,134 (rewrite) → 48,916 (with FMA fusion)

- **S1** the 13 far output taps as integer block passes (gated-style backward walks
  split at the wrap, signs as ±1 int16 gains); the 121-sample tap, the only one that
  can see the current block's writes, stays in the per-sample loop.
- **S2** integer predelay and pitch shifter (triangle window exact in Q15; the tank
  send quantizer rounds toward zero). The tank and diffusers stay float — the Gated
  lesson: correct feedback quantizers cost what the float they replace costs, so the
  planned "fixed-point tank" was dropped.
- Measured: 59,326 → **52,134** (−12.1 %, +7.4 → +5.3 pts). Render vs float: peaks
  within 1 LSB, worst envelope divergence 0.08 dB across knob moves; 30 s silence
  decays to exactly 0 at all four worst-case settings. Device-side extras not in the
  insn count: tap reads are sequential int16 streams instead of 13 scattered float
  reads into a 128 KB ring (SDRAM-friendly).
- Held back (audible): half-rate tank, −5.6 pts more, duller tail.

### Plate — DONE, 49,014 → 41,771 (rewrite) → 39,201 (with FMA fusion)

- **P1 + P2** — Shimmer's S1 + S2, minus the shifter: all 14 output taps (offsets
  ≥ 145, so none sees the current block's writes) run as integer block passes through
  shimmer_dsp.h's helpers (plate_dsp.h now includes after it; the rings are the same
  size), and the predelay goes integer. The tank and diffusers stay float — the
  pre-lesson ~30,500 assumed a fixed-point tank, dropped for the same reason as
  Shimmer's.
- Measured: 49,014 → **41,771** (−14.8 %, +4.4 → +2.2 pts). Render vs float:
  bit-identical (peaks equal, 0.00 dB envelope divergence — the integer tap sums are
  exact in float); 30 s silence decays to exactly 0 at all four worst-case settings.

### Freeverb — DONE, 53,862 → 50,908 (rewrite) → 46,552 (with FMA fusion)

- **F1, F2 dropped** (the Gated lesson, by arithmetic this time): the combs' one-pole
  and the allpasses are feedback, so their quantizers must round toward zero, and on
  the M7 a correctly rounded integer one-pole (smull + sign fix per multiply) costs
  *more* than the float it replaces (vmul/vmla are single-cycle). The pre-lesson
  ~34,000 assumed cheap integer feedback; it does not exist.
- **F3** the predelay loop is the real find: 4,992 insns (39/sample), nearly all of it
  the two branchy `pre_at()` calls per sample. Rewritten as integer walks split at the
  ring's buffer seam and its end, like every other pass.
- Measured: 53,862 → **50,908** (−5.5 %, +5.8 → +4.9 pts). Render vs float:
  bit-identical; 30 s silence decays to exactly 0 at all four worst-case settings.

### BBD — DONE via FMA fusion alone, 39,571 → 37,329 (PingPong)

- **B1** fixed-point biquads (anti-alias + reconstruction, Q14 coefficients recomputed
  per delay change as now) and integer Hermite playback (int16 taps, Q15 `frac`,
  int32 Horner): the four biquad evaluations and two reads per PingPong sample are
  most of the loop.
- **B2** integer tone/low-cut one-poles.
- Keep float: the glide, the drift oscillator, the soft clip (`tape_sat` — audibly
  load-dependent, and cheap).
- Result: 39,571 PingPong → **~28,000** (+9.3 → ~+5.9 pts), mono 25,386 → ~19,000.
  More conservative than the reverbs because the float that remains is entangled with
  the character (saturation, swept filters).
- *Revised after the Gated lesson:* the biquads and one-poles (B2) are feedback and
  stay float; only the Hermite playback reads (feedforward) go integer.
- *Outcome 2026-10-28:* the integer Hermite was priced from the disassembly before
  writing it — 3 smmulr stages + masked ldrsh ≈ the float Horner it replaces — a
  wash, so B1 was dropped too. The measured gain (−2,242) is all FMA fusion, which
  reaches exactly the biquads and one-poles the hand-rewrite couldn't.

### Tape — DONE via FMA fusion alone, 31,005 → 29,446 (PingPong)

- **T1** integer Hermite playback and integer tone/low-cut, as B1/B2; keep the glide,
  wow/flutter oscillators and record-head saturation float.
- Result: 31,005 PingPong → **~22,500** (+6.8 → ~+4.3 pts), mono 20,381 → ~15,500.
- *Revised after the Gated lesson:* integer tone/low-cut dropped (feedback); the
  Hermite playback stands.
- *Outcome 2026-10-28:* the Hermite rewrite priced out as a wash (see BBD) and was
  dropped; the measured gain (−1,559) is FMA fusion.

### Spring — DONE, 42,082 → 35,724 (Sp2 + FMA fusion)

- **Sp1** fixed-point dispersion chain: the 32 allpass stages per spring in Q13 with
  int16 state pairs — 11 → ~7 insns per stage (−8,000 ≈ 2.4 pts) and the SDRAM state
  traffic halves (36 KB → 18 KB per block, the biggest pure-traffic win available).
  The chirp is sensitive to coefficient precision, so Q13 minimum and a careful
  listen to the drip.
- **Sp2** integer 44.1 kHz section (predelay, interpolation, mix): −3,000 ≈ 0.9 pt.
- Result: 42,082 → **~31,000** (+2.3 pts → below the stock reverb).
- *Revised after the Gated lesson:* the dispersion chain (Sp1) is 32 allpasses of
  feedback — insn-neutral at best in integers, and 32 limit-cycle-prone quantizers.
  Dropped; only Sp2 (the 44.1 kHz feedforward section) stands, ~−3,000.
- *Outcome 2026-10-28:* Sp2 landed (integer predelay write + the per-sample spring
  state hoisted to locals), and FMA fusion reached the dispersion chain the integer
  rewrite couldn't: 42,082 → 35,724, measured — already below the ~39,000 estimate
  and within 0.5 pt of the stock reverb.

## 6. Order and expected outcome

Shared machinery and worst-case impact set the order:

1. **G1, S1, G2, G3, S2** (approved) — the current worst pair.
2. **P1, P2** — reuses S1/S2's pattern directly.
3. **F1–F3** — heaviest remaining reverb.
4. **B1, B2** — heaviest remaining delay.
5. **T1** — same machinery as B1.
6. **Sp1, Sp2** — smallest delta, touchiest sound.

| | now | after (measured 2026-10-28, rewrites + FMA fusion) | pts over stock |
|---|---|---|---|
| Shimmer | 59,326 | **48,916** | 7.4 → 4.3 |
| Freeverb | 53,862 | **46,552** | 5.8 → 3.6 |
| Plate | 49,014 | **39,201** | 4.4 → 1.5 |
| Spring | 42,082 | **35,724** | 2.3 → 0.5 |
| "Gated" | 43,434 | **38,596** | 10.4 → 9.0 |
| BBD (PingPong) | 39,571 | **37,329** | 9.3 → 8.7 |
| Tape (PingPong) | 31,005 | **29,446** | 6.8 → 6.3 |

The "after" figures were revised once the Gated lesson was priced into every plan:
integer feedback with correct (toward-zero) quantizers is no cheaper than the M7's
single-cycle float, so only the feedforward work (taps, interpolated reads,
predelays) converts. On top of the rewrites, one build change reached every float
effect at once: `-std=c11` sets `-ffp-contract=off`, so masterfx had been paying a
separate vmul + vadd for every `a*b+c` while the stock reverb fuses them (24
vfma/sample). `-ffp-contract=fast` in build.sh turns 193 multiply-add pairs into
single vfma/vfms — that is the whole BBD/Tape/Spring-dispersion gain (their bespoke
integer rewrites priced out as washes and were dropped) and another 2.5–4.4k off
each already-rewritten reverb. The fused result is singly rounded (slightly more
precise), so renders shift by at most an LSB. Worst reverb + delay pair:
+60.6k insns (~17.9 pts) at the start → **+45.4k (~13.4 pts)** (Shimmer + Gated),
and every other pairing lands lower still — the sweeping pre-lesson ~6-pt figure
is not reachable without audible changes (fewer taps, half-rate tanks), which stay
held back as options. Afterwards the ceilings came down (2026-10-28):
`test_bundle_cost.py`'s BLOCK_LIMIT 120,000 → **60,000** over stock, and
`test_masterfx.py`'s reverb/delay ceilings 90,000/60,000 → **55,000/45,000**
(worst measured: Shimmer 48,979, Gated ~38,900 in harness context), so the gains
can't silently erode.

## 6b. Hardware outcome (2026-10-28): the gain did not appear on the device

The headroom runs on the rebuilt MH1 (new masterfx confirmed flashed, file of
08:00): run A blk 130.9 % / 1s 126.9 % / late 6,919 against 130.3 / 130.3 / 6,809;
the no-synth run blk 66.3 % / 1s 64.5 % / late 0 against 66.5 / 64.7 / 0. Within
run-to-run noise: the ~4.5-point prediction did not materialize; the real gain is
at most ~1 point.

**Why, in hindsight.** k ≈ 4.5 cycles per emulated instruction is ~4× the M7's
arithmetic throughput — most of masterfx's device time is SDRAM/cache stalls, not
instructions. The rewrites and FMA fusion removed arithmetic (conversions, separate
vmul/vadd) while leaving the number of memory touches roughly unchanged, so the
instructions removed were worth ~1 cycle each, not 4.5: ~15k cycles ≈ 1 % of a
block. The k = 4.5 calibration priced the *average* instruction of the whole pair
(arithmetic + its share of stalls); it does not price a *removed* instruction
unless the removal also removes its memory traffic.

**What this revalues.** The device's currency is bytes touched per block, not
instructions. The options dropped as "washes" by instruction count — int16 state
in the feedback paths (Freeverb's combs, Spring's dispersion chain, BBD/Tape's
lines) — halve their SDRAM traffic, which is exactly what the device is starved
by, and the extra correct-rounding arithmetic they need is nearly free there.
The Gated lesson (toward-zero rounding or limit cycles) still applies to their
sound; only their cost model changes. Any further masterfx work should measure a
traffic proxy (bytes read+written per block), confirm with one targeted on-device
experiment, and only then rewrite.

The emulator-side results above stand as measured (instructions are what the
emulator counts, and the tightened ceilings hold them), but no device-time claim
should be made for mf6 beyond ~1 point.

### The traffic investigation (2026-10-28, emulator)

Bytes/block turned out to be the wrong refinement too: Tape/BBD/Spring's big
flash and SDRAM byte counts are re-reads of a few lines (literal pools; Spring's
~2 KB dispersion state walked 128 times), which the D-cache serves. The metric
that fits the device is **distinct 32-byte lines touched per block** (sample
streaming evicts the cache between masterfx calls, so each block starts cold):

| effect | lines/block (SDRAM) | delta vs stock | device model: insns×1 + Δlines×~350 |
|---|---|---|---|
| stock reverb | 137 (124) | — | — |
| Shimmer | 361 (304) | +224 | 25.2k + 78k ≈ 6.8 pts (measured 7.4) |
| Plate | 323 (268) | +186 | ≈ 4.6 pts |
| Freeverb | 302 (241) | +165 | ≈ 4.6 pts |
| Spring | 85 (63) | −52 | ≈ stock or below |
| stock delay | 86 (76) | — | — |
| Gated | 458 (392) | +372 | 35.5k + 130k ≈ 10.8 pts (measured 9.7) |
| Tape | 78 (54) | −8 | ≈ stock |
| BBD | 70 (48) | −16 | ≈ stock |

One fit (~350 cycles per cold line) lands within ~15 % of both device-measured
effects, and retro-predicts the mf6 null exactly: the rewrites changed zero SDRAM
lines (304/392 on both mf5 and mf6), so they saved only the ~1-cycle instructions.

**Consequences.** ~75 % of Shimmer's and Gated's device cost is cold-miss latency
on their scattered tap reads, and the tap set *is* the sound — no sound-preserving
rewrite (int16, FMA, block passes, and now also int16-state conversions) reduces
it. Spring, Tape and BBD are already at or below the stock effects' stall cost;
nothing to win there either. The sound-preserving ideas left are (a) software
prefetch (`pld`) in the block passes, which hides miss latency instead of removing
misses — plausible on the M7 but unproven on this chip, needs its own one-run
probe; (b) nothing else. Tap-count reduction works and stays held back as the
audible option. The big headroom lever on the device remains the synth voices.

**Zero-flash validation — CONFIRMED on the device (2026-10-28).** No-synth
project on the flashed MH1, Master FX Spring + Stock, 800 BPM: **blk 50.8 % /
seq 20.0 % / 1s 48.3 % / late 0** against the model's ~48–49 % and the byte
model's ~60 %. Spring costs ~2 points on hardware (the insn model said 0.5, the
byte model said 11–20). The line-miss model stands on five device points.

## 7. Verification

Each numbered step lands alone and must pass before the next starts. Per step, the
full masterfx test plus a render diff (~10 minutes); the **deep tier runs once per
effect**, after that effect's last step (the user chose this cadence 2026-10-27: a
deep run per step would re-test masterfx's bundles ~11 times for the same coverage).
To fit that cadence, each effect's steps run together: G1–G3, then S1–S2, and so on.

1. **Emulator, deep tier** (audio code changed): the existing checks — each reverb's
   tail level within its tolerance of stock, decay times, "Gated" burst length and
   reverse shape, tape/BBD echoes and pitch behavior, spring drip, no NaN,
   startup-from-garbage, switching between all effects — plus the cost checks, whose
   ceilings come down step by step. The runner prints the measured time estimate
   before anything long.
2. **Render diff**: `compare_masterfx.py` / the WAV renders before and after each
   step. G1, G3, S1 and P1 must be byte-identical; the fixed-point steps within
   rounding (spot-check the renders by ear for zipper or limit-cycle artifacts, the
   one thing the level checks can miss — especially Spring's chirp and Tape/BBD
   feedback at high Intensity).
3. **Hardware**: after each effect pair lands, re-run the `headroom` diagnostic
   (`patch/headroom`, bundle `all_headroom.json`) as in the headroom plan's section
   13, and compare the engine-side points against its table (MH1 run A); a full pass
   at the end with the worst remaining pair chosen.

## 8. Numbers to hold me to

- Baselines measured here: stock delay 8,040 (PingPong 7,912), stock reverb 34,152
  instructions per block (matching the headroom plan).
- Gated: 38,891 measured (target was ≤ 26,000; see the lesson above — the remainder
  is tap count, the held-back audible option). The open targets, written before that
  lesson, are optimistic for the feedback-heavy effects: Shimmer ≤ 37,000;
  Plate ≤ 33,000; Freeverb ≤ 36,000; BBD PingPong ≤ 30,000; Tape PingPong ≤ 24,000;
  Spring ≤ 33,000.
- No existing sound check loosened; `expected.json` echoes and tails unchanged within
  present tolerances.

## 9. DONE: "Gated" moved to the Reverb slot (approved and implemented 2026-10-28, mf7)

The user's idea, and the one lever left that respects every constraint: no effect
sounds different, nothing synth-side is touched — the worst case falls because the
two heaviest effects become mutually exclusive. Today Reverb=Shimmer plus
Delay="Gated" can run at once (~17 device points over stock, measured); with Gated
in the reverb list the worst pair is Gated + BBD at **~11 points**, and every
pairing without Gated lands at or under ~9.

**Feasibility, checked.** The reverb object owns two 32 KB buffers (+0x24/+0x28);
the delay object's three 512 KB buffers stay with the stock delay. Gated fits the
reverb side with its tap line in one buffer (16,384 samples) and its 2,048-sample
diffuser ring in the other — which caps the gate at **371 ms** instead of 2 s.
That is the classic gated-reverb range (the 80s units ran ~100–400 ms); only the
long reverse-swell settings are lost. (A 696 ms cap is possible by splitting the
line across both buffers, at the cost of seam-split tap reads — held as an option
if 371 ms feels short.)

**The plan.**
1. Menu: the reverb list becomes Stock / Freeverb / Plate / Spring / Shimmer /
   **Gated** / **Reverse** — reverse mode (today the PingPong flag) as its own
   entry, since the reverb slot has no toggle. The delay list becomes Stock /
   Tape / BBD.
2. Knobs: Size → gate length 10–371 ms; Damping → the lowpass (today Filter);
   Diffusion → the release shape (today Feedback); Predelay → a new predelay on
   the tap offsets (cheap, and it had none to lose).
3. State: gated_state joins the reverb-side layout (rv switch clears both 32 KB
   buffers already); the gated_cache late-RAM block is unchanged. The delay-side
   clearing loses its Gated case.
4. Settings migration: a project that saved Delay = Gated loads as Delay = Stock;
   the reverb choice stays as saved (never auto-select an effect the user didn't
   pick). Version text mf7; patch/all bumps.
5. Verification, in this order: emulator cost + lines for Gated-as-reverb (expect
   ~unchanged, ~10 points when chosen), torture + render character checks (render
   can't be compared sample-for-sample to the delay-slot version — the send path
   differs — so the checks are the gate's own: burst length vs Size, release
   shape, reverse shape, silence decay to zero), full masterfx, one deep tier,
   PATCHING.md/README updates, then the user's by-ear pass on hardware.

Worth saying plainly: this buys ~6 points of worst case purely by exclusivity;
Gated itself still costs ~10 when chosen, which its menu entry now prices against
the other reverbs honestly.

*As implemented (mf7):* Gated in the reverb slot measures 41,747 insns/block (the
stereo input, Amount mix and level field add ~3.1k over its delay-slot cost); all
other effects byte-unchanged. Full masterfx test 0 failures; Gated and Reverse
tortures decay to exactly 0 inside the first second at every setting. The worst
pair selections in test_bundle_cost.py and make_headroom_project.py moved to
"Gated" + BBD; stress_masterfx.py's gated state checks moved to the reverb side.
