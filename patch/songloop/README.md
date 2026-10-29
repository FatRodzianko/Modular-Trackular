# songloop: loop a selection of Song positions

A patch for Polyend Tracker+ firmware 1.2.0. In the Song screen you select a block of
song positions and play it in a loop, **Play Selection**, while you jam over it or
decide what comes next; **Continue Song** plays the block out and goes on with the song.
Installed on its own, Config shows the firmware version as `1.2.0-sl4`.

**Status:** tested on hardware on its own (`1.2.0-sl4`) and in `patch/all` (`1.2.0-SLA`);
emulator-tested on its own and in `patch/all` (both orders).

## Using it

1. Open **Song** and move to the pattern column (the column with the pattern numbers).
2. Turn **Rec** on (red frame): only then can the cursor move through the song while it
   plays.
3. Put the cursor on the block's first position. **Shift + Down** selects downwards from
   there; **Shift + Up** takes the bottom back (the block never grows upwards, so you always
   know which edge moves). Up/Down without Shift, Rec off or another column clears the
   selection (not during the loop).
4. The left softkey now reads **Play Selection**:
   - stopped: the song plays from the block's first position and repeats the block;
   - playing, with the block ahead: the song plays on and repeats the block once it reaches
     the block's end. Until then the status bar shows the opening sign **|:** (armed);
   - playing, with the block behind the play position: the song goes to the block's start
     at the end of the current pattern (one pattern later if you press it on the pattern's
     last row, when the next pattern is already loading).
5. While the block repeats, the status bar and the Song list show the repeat signs
   **|: :|** in place of the play icon, and the softkey reads **Continue Song**: the
   block plays out, then the song goes on, the selection is gone and the cursor (red
   frame) goes to the position playing.

During the loop:

- **Shift + Down/Up** still resizes the block at its bottom, down to one position that
  repeats.
- **Up/Down** and the jog wheel without Shift move the whole block, at its size, within
  the song (a 4-position block stops with its bottom on the song's last position). If the
  play position ends up outside the block, the song goes to the block's start at the end
  of the pattern, so you can queue any part of the song.
- With the cursor in the grid, a dark grey frame shows the repeating block.
- **Stop**, on any screen, ends the loop.
- **Shift + Play** in Song does the same as the left softkey.

With a block selected, or during the loop, **Undo**, **Add Slot**, **Delete Slot** (also
the Delete key) and **<< Pattern >>** (also Shift + jog) do nothing and are greyed out,
so the song can't change under the selection.

The loop goes on in the Pattern and Performance screens. Performance works as in stock
firmware (Loop Performance, Shift + Play in Performance), and the selection loop is
**Loop Performance for a block**:

- stock firmware keeps Performance on while one pattern loops (Loop Performance) or while
  you're in the Performance screen; leave it while the song plays and Performance goes
  off at the next pattern end;
- while the selection loops, Performance stays on for all of the block's patterns: its
  effects keep playing, and the status bar keeps the Performance icon (the crossed
  arrows) next to the repeat signs;
- Continue Song works as stock's: the song goes on, and Performance goes off at the next
  pattern end;
- a running pattern loop (Loop Pattern, Loop Performance) first needs Continue Song, as in
  stock firmware; then Play Selection loops the block;
- with a Performance play mode other than Fwd on track 1 (Bwd, PingPong, Rnd, …), stock
  firmware loads the next pattern too late or at random, so the song hangs on a pattern
  or goes back to the previous one. This patch then loads it on time (see below). This
  also fixes the song without a selection.

Nothing is saved: the selection lives until you clear it or power off.

## How it works

The sequencer preloads the next pattern on the last row of track 1 by calling
`song_peek_next` (ITCM `0x7030`) from two places (`0x23c4`, `0x2abe`); at the pattern
end `song_next_position` (`0x600c3970`, called at `0x600ad2d0`) moves the play position.
The patch hooks both: at the block's end the preload asks for the block's first
pattern and the pattern end goes to the block's start, so the loaded pattern and the
position always match.

The Song screen's inputs are callbacks registered in its init (the literal pool at
`0x600fd948`–`0x600fd990`): jog, Play, Up, Down, softkeys 0, 2, 3, 4 and the Delete key.
The patch wraps these. The softkey labels come from `0x600fedac`, called from the Song
update at `0x600fdd82`; the hook draws Play Selection / Continue Song, and otherwise calls
through to stock's labels. No firmware instruction is changed.

At a pattern end in song mode, the sequencer calls a scheduled action (seq+0x6e8,
`0x600ad2fc`) right after `song_next_position`. Leaving Performance while the song plays
schedules Performance off there (`0x7091`). While the selection loops, the pattern-end
hook replaces it with a no-op (`0x600abf9d`), as stock Loop Performance does; Continue
Song calls stock's Continue Song (`0x600e6658`), which schedules it again.

The block frame is drawn by two hooks on `eve_line_width` in the song control's drawing
(`0x601086fa`, `0x601088a4`), which move the cursor frame's y range onto the block. The
repeat signs replace two unused glyphs of the status bar's font (`` ` ^ `` and `{ }`,
uploaded after every font load: `0x60101ade`, `0x600c5f82`, `0x600cbede`) and add two
20x20 bitmaps for the Song list at RAM_G `0x1f000` and `0x1f100`. The status bar text
(`0x600cf9f0`) and the list icon (four calls of `0x60108310`) show them.

Every stop goes through `seq_stop`; its first call (`0x600acd98`) is hooked to end the
loop. Once per main loop (`popup_update` in `ui_update`, `0x600cc5ec`) the patch checks
the Performance case above: with track 1 not on Fwd, it requests the next pattern on the
last two rows of track 16, which ends the pattern, once per pattern end.

The state is 16 bytes in `.mt_ram`. Every site is chained (`mt_sl_next_*`).

## Building

```bash
patch/songloop/build.sh                     # -> patch/songloop/build/songloop.{bin,sym,rel}
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/songloop/patch.json build/TrackerPlus_sl4.ptf
python tools/emu/test_songloop.py build/TrackerPlus_sl4.ptf path/to/TrackerPlus_1.2.0.ptf
```

## Reverting

Config → Firmware → `TrackerPlus_1.2.0.ptf`. Nothing is stored in projects.
