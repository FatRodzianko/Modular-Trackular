# chords: more MIDI chords (inversions)

A patch for Polyend Tracker+ firmware 1.2.0 that adds chords to the MIDI Chord FX
(`0`), starting with the inversions stock firmware lacks. Turning the encoder past the
last stock chord (`57B`) goes on to the major and minor triad inversions (` 38`, ` 59`,
` 49`, ` 58`), then the maj7, min7 and dom7 inversions, then every other chord within
an octave. The Arp (`A`) plays them too. Installed on its own, Config shows the firmware
version as `1.2.0-ch2`.
[docs/CHORDS_PLAN.md](../../docs/CHORDS_PLAN.md) has the investigation and the plan.

**Status:** tested on hardware (`1.2.0-ch2`, Arp included; the chords also together with
legato and condtrig). Emulator-tested on its own, and in bundles with legato and condtrig
in both orders and with all the other mods.

## Using it

1. Put a MIDI Chord FX (`0`) on a MIDI or synth note, in FX1 or FX2, as in stock
   firmware.
2. Turn the encoder on its value. Past `57B` (value 29) it goes on into the new chords,
   and stops at the last one. Turning left goes back into the stock list.
3. The cell shows the chord the way stock firmware does: the intervals above the step's
   note in semitones, as hex digits (`A` = 10, `B` = 11). So ` 59` on G3 plays G3, C4
   and E4: C major in its 2nd inversion.

Only the encoder enters the new chords. The pads, Fill, random fill and the Random FX
value (`x`) stay within the stock chords, as in stock firmware.

## The chords

The chord value is saved in the pattern, so these numbers never change meaning.

| Values | Shows | Chords |
|---|---|---|
| 0–29 | `---` … `57B` | the stock chords, unchanged |
| 30–33 | ` 38` ` 59` ` 49` ` 58` | major 1st and 2nd inversion, minor 1st and 2nd inversion |
| 34–36 | `378` `459` `158` | maj7, 1st to 3rd inversion (`378` is also stock value 18) |
| 37–39 | `479` `358` `259` | min7, 1st to 3rd inversion (`479` is also stock value 22) |
| 40–42 | `368` `359` `269` | dom7, 1st to 3rd inversion |
| 43–69 | ` 39` ` 69` … `168` | the other inversions of stock chords: diminished, sus2♯5, sus♯4, m7♭5, mMaj7, aug7, augMaj7, 7sus4, sus2♭7, sus2maj7, sus4maj7 |
| 70–236 | `  1` … `9AB` | every other chord of one to three notes above the step's note within an octave: single intervals, then pairs, then three, each in digit order |
| 237–255 | | not used: played as stock firmware plays them (the note alone) |

Sus2 and sus4 need no new values: their inversions are stock chords (` 57`, ` 5A`,
` 27`).

Notes are sent as stock firmware sends a chord's notes: lowest first, all at once, with
the step's velocity and the instrument's channel. Notes above 127 are left out.

**The Arp** (`A`) in the other FX column arpeggiates a new chord's notes, folded into
one octave above the step's note, exactly as it does a stock chord's: the same
directions, rates and random order. Values 237–255 under an Arp play chromatic, as stock
firmware plays any value above 29.

**In stock firmware**, a project with new chords still opens and plays: those steps play
the note alone and show odd text in the cell. Touching such a value with the encoder
makes it `57B`.

## How it works

A new chord is played as one stock note per chord note: the hooks call the stock
function once for each interval, lowest first, on a copy of the step's record with the
note raised and the chord value 0 (the note alone). The stock code then sends each note
with its own velocity, channel, synth and render handling, so a new chord sends exactly
what a stock chord with the same notes sends (checked against stock firmware in the
emulator). The copy keeps its Chord type, so other patches still see a chord.

| Hook | Where | What |
|---|---|---|
| `mt_ch_trigger_a`, `_b`, `_c` | ITCM `0x22f8`, `0x285c`, `0x2fd8`: the three `bl seq_trigger_note` (performance mode's step repeat; Roll's repeats; the step's note, which legato hooks too), through ITCM stubs, chained | For a MIDI or synth note with a new chord (FX1 if it is Chord, else FX2, and not with Arp in the other column, as stock decides), calls `seq_trigger_note` once per chord note; anything else goes on unchanged |
| `mt_ch_arp_a`, `_b` | ITCM `0x2428`, `0x32ce`: the two `bl arp_next_note_q` (the arp's next note; its first), through ITCM stubs, chained | For a new chord, points `arp_next_note_q`'s pointer to the chord table (a word in ITCM at `0xcc0`) at a 2-entry table on the hook's stack whose entry 1 is the new chord, sets the arp's chord value to 1, calls it, and puts both back. The stock code does the rest. Only the audio interrupt calls it, so nothing else sees the borrowed pointer |
| `mt_ch_note_off` | the word at `0x601acd80` (chained): the ITCM veneer's jump from `seq_note_off_q` to `seq_chord_note_off_q` | The same for note-offs, so the next note, OFF, CUT, FADE, the gate end, mute and stop end exactly the notes that were sent |
| `mt_ch_cell_max` | `bl fx_max` at `0x600af1b2` in the encoder edit of FX values (chained; condtrig is there too) | Chord's highest value is 236 instead of 29 |
| `mt_ch_fx1_text`, `mt_ch_fx2_text` | `bl fx_value_text_cell` at `0x600df748` (FX1) and `0x600df77c` (FX2) in the pattern grid (chained; condtrig is there too) | A new chord shows its intervals; everything else as stock |

The chord list is a 414-byte table in the patch's flash. The patch uses no RAM. Each
MIDI or synth note costs a few compares; a new chord adds about 30 instructions per
note to what a stock chord costs, and an arp note on a new chord about 20.

The Arp hook is the only place any patch writes into ITCM while the firmware runs. The
word it changes is data, which the MPU maps read-write (docs/FINDINGS.md section 8),
and it is back to stock's value after every call.

## Building and checking it

```bash
patch/chords/build.sh                       # optional: prebuilt/ is used without a build
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/chords/patch.json build/TrackerPlus_ch2.ptf
python tools/emu/test_chords.py build/TrackerPlus_ch2.ptf path/to/TrackerPlus_1.2.0.ptf
```

The test checks what every value 30–255 plays, compares new chords with stock chords
message for message (random patterns, Micro-move, delay, Roll, gate, OFF/CUT/FADE,
Chance, mute, stop, song mode, synth slots), the Arp (against stock chords under every Arp
value, and every new chord's notes), the encoder and the cell text, stock firmware with
such patterns, and the cost.

## Testing it on the device

Set up MIDI out to a synth, or a DAW with a MIDI monitor. Use a MIDI instrument on
channel 1, and for step 8 a synth slot with Polyphony 4.

1. Back up the SD card, and use a copy of a project. Keep the stock
   `TrackerPlus_1.2.0.ptf` in `/Firmware`.
2. Install `TrackerPlus_ch2.ptf` through Config → Firmware. Config shows `1.2.0-ch2`.
3. Step 1: C4 with chord ` 47`. You hear C E G, as before.
4. Turn the encoder right past `57B`: the cell shows ` 38`, then ` 59`. Put G3 with
   ` 59` on step 5: you hear G3 C4 E4. Put an OFF on step 9: all three stop (the MIDI
   monitor shows three note-offs).
5. A 4-note inversion: E3 with `368` (C7 over E). Turn left from ` 38`: back to `57B`.
6. Press Stop while a chord sounds: nothing keeps ringing.
7. Save the project, load another, load it back: the cells still show ` 59` and `368`,
   and play the same.
8. The same ` 59` step on the synth slot: three voices sound.
9. The Arp: G3 with ` 59` in FX1 and an Arp (`A`) in FX2, at any value. You hear G C E
   arpeggiated, not a run through all 12 notes. Try a few Arp values (up, down,
   random).
10. The same with the columns swapped: Arp in FX1, ` 59` in FX2. Then a stock chord
    under the Arp (C4, ` 47`): C E G arpeggiated, as before.
11. Let an arp on a new chord play for a minute, change the chord value while it plays,
    then press Stop: no hang, nothing keeps ringing.

## Reverting

Install the stock `TrackerPlus_1.2.0.ptf` (or your usual firmware) through Config →
Firmware. Steps with new chords then play the note alone.
