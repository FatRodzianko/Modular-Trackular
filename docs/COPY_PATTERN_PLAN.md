# Copy Pattern to New Pattern: plan (firmware 1.2.0)

A Pattern screen button (record mode, third page of the bottom bar, next to Humanize)
that copies the current pattern, as it is in RAM, into the next pattern that has
nothing in it. The copy keeps every track, every step and each track's length. The
screen stays on the current pattern, playing or not.

All addresses are firmware 1.2.0. `fm` = file manager `0x2001e488`, `seq` = sequencer
`0x20017274`, `project` = `0x20204000`.

## 1. Patterns in memory and on the card

- **The pattern data** (`.mtp` layout, FINDINGS sections 6 and 12): a 0x1c-byte
  header, then 16 tracks of 0x301 bytes (the track's last-step byte, then 128 steps
  of note, instrument, FX1 type, FX1 value, FX2 type, FX2 value), then a 4-byte CRC:
  0x3030 bytes. Track 0's last-step byte is the pattern length − 1; in the current
  file version (5) every track has its own. The header is
  `{u32 0x0002534b, u32 0x01020901, u8 version 5, 3 × 5, u16 size 0x3030, …}`, set
  by `pattern_set_header` (`0x600ae98c`). There is no tempo or swing in a pattern
  file: those are project settings and step FX.
- **In RAM** there is one copy of the current pattern: the sequencer's play buffer
  `*(seq+0x14)`. The editor edits it in place (humanize, Invert and the other
  editing buttons do), so it holds the unsaved edits. `*(seq+0x18)` is the preload
  buffer: the next pattern in song mode, or the pattern being switched to. The current
  pattern number is `project+0x6c8`.
- **On the card** each pattern is `Workspace/patterns/pattern_NN.mtp` (`%02d`, so
  `pattern_100.mtp` for 100), N = 1..255. A pattern nobody has edited has no file.
  - Save: `pattern_save_now(fm, n)` (`0x600c205c`) runs when `fm+0x4ca+n` (the
    pattern's changed flag) is set: it writes the play buffer with
    `pattern_write_file(fm, path, data)` (`0x600c1fb4`: remove, set the header,
    CRC over 0x302c bytes into `data+0x302c` through the CRC object at
    `*(fm+0x1104)`, open mode 3, one 0x3030-byte write, close), sets
    `project+0x6f4` bit 0 and `fm+0x469` (unsaved changes), and updates the
    track-usage masks (`pattern_update_usage_current`).
  - Load: `pattern_load_now(fm, n)` (`0x600c23d4`) reads the file into the preload
    buffer with `pattern_read_file` (`0x600c2350`: clear, open, read 0x3030, close,
    upgrade older file versions with `0x600c2284`; no CRC check). Without a file it
    fills the buffer with an empty 32-step pattern (`pattern_init`, `0x600ae9b0`:
    every track's last step 31, notes −1, instrument and FX types 0).
  - A project's save copies every `Workspace/patterns/pattern_NN.mtp` (1..255) to
    the project folder (`project_save_patterns`, `0x60114c7c`); opening a project
    copies them back (`0x6011458c`). So a new file in `Workspace/patterns` becomes
    part of the project on its next save, like any edited pattern.
- **Track-usage masks** (Song screen blocks): `lo[n]` = `project+0x8cc+n`,
  `hi[n]` = `project+0xbf6+n`, from `pattern_update_track_usage(fm, n, data)`
  (`0x600c3998`; returns 1 if the mask changed). A track counts as used if a step up
  to the pattern length has a note, an FX type, or an instrument above 47.
- **Unsaved-changes flags:** `fm+0x469` (`0x600bcd30` sets it) and `project+0x6f4`
  bit 0 (kept in `project.mt`); `0x600bcc58` ORs them with the per-pattern and
  per-instrument flags for the "unsaved changes" checks.
- **File-manager busy state:** `fm+0x13d` / `+0x13f`. The stock "can start" check
  (`0x600bc990`) is `+0x13d` ∈ {0, 0x0c} and `(+0x13f & 0xfd) == 0`.
- **Messages:** `show_message(0x20020880, 0, text, 0)` (`0x600cdf28`) shows one line
  in the stock message bar ("No undo data :(", "Pattern is too short to make this
  operation."). It keeps the text pointer, so the text must stay in memory.

### Stock functions that copy patterns

None copies a pattern to a free one. Page 2's Copy/Paste/Duplicate work on steps. The
Song screen has file-based track operations (`0x600c3a8c`, `0x600c3b58`: read a
pattern file into SDRAM `0x83f5cf6c`/`0x83f5ff9c`, copy or clear tracks, update the
masks, write the file) with their own 10-slot undo of whole pattern files
(`0x600c3e30` push, `0x600c3eac` pop). They read the source from its file, so they
would miss unsaved edits, and they use their own SDRAM buffers. The patch uses
`pattern_write_file` and `pattern_update_track_usage` directly instead.

## 2. Which pattern is "empty"

Pattern n (not the current one) is a target if all of these hold:

1. **Mask 0** (`lo[n] | hi[n] << 8 == 0`). No I/O. A non-zero mask means used.
2. **Not in the song** (not anywhere in the playlist, `project+0x5c2`, 255 bytes)
   and **not queued** (`seq+0x26` while `seq+0x28` is set). Song mode reads the next
   pattern from its file shortly before it plays; a pattern the song or the queue is
   about to load could otherwise be read just before the copy is written and then
   saved back over it. This goes slightly beyond the request (an empty pattern used
   as a silent bar in the song is left alone); see the README.
3. **No file**, or a file whose steps are all empty: `exists`, then a read in pieces
   of 256 bytes into a stack buffer. A step is not empty if its note is not −1, its
   instrument is not 0, or either FX type is not 0 (FX values are ignored: an empty
   FX keeps its old value). This is all 128 steps of every track, past the pattern
   length too. A file that can't be read, is shorter than 0x3030 bytes, or has
   another header (an older file version) counts as used.

Search order: n = current + 1 … 255, then 1 … current − 1 (it wraps).

**Cost.** Most patterns are rejected by 1 or 2 without touching the card. Each
candidate that needs the card costs one `exists` and at most one 12 KB read, and the
patch handles one such candidate per Pattern screen update, so a project with many
cleared-but-saved patterns takes a few updates longer. The update that finds a pattern
empty also writes the copy into it, so nothing can change the target between the check
and the write (it's still one pattern file per update). 255 is the highest pattern
number (the masks, the playlist, and the project save and open loops all stop there).

## 3. What a correct copy keeps

- **Everything in the pattern data:** the copy is the whole 0x3030-byte buffer,
  written by the stock writer. Every track's last-step byte comes with it, so the
  target gets the source's length and per-track lengths, whatever it had before.
  The stock writer writes the file in one 0x3030-byte write straight from the play
  buffer, as the stock pattern save does from the main loop. That's the one
  exception to the 256-byte pieces the other patches use (they write from small
  stack buffers); a copy into a stack buffer would only add risk.
- **Unsaved edits:** the source is the play buffer, not the source's file.
- **Bookkeeping:** after the write, `pattern_update_track_usage(fm, target, data)`
  (Song screen blocks), then the two unsaved-changes flags, as `pattern_save_now`
  does. The target's changed flag (`fm+0x4ca+n`) is not set: it means "the play
  buffer must be written as pattern n", which would be wrong for a pattern not in
  the play buffer.
- **colorsong:** it fingerprints patterns in its hook on the six `bl
  pattern_update_track_usage` sites; the patch calls the function directly, so
  colorsong has no fingerprint for the target. That's what colorsong already
  handles: when the target appears in the song, its mask differs from the one of any
  old fingerprint (the target had mask 0), and colorsong rereads it from its file
  on the next Song screen visit.
- **Playback:** nothing in the sequencer is written. The play buffer's header and
  CRC field are rewritten with the same values the stock save writes there. The
  screen stays on the current pattern; in song mode the song plays on as before.
- **The source must still be current when the copy is written.** The press only
  records the source number (and the project's name and folder); the scan takes one
  or more updates. Each update first checks that the current pattern is still the
  source ("Pattern changed - try again" otherwise) and that no other project was
  opened (then the copy is dropped silently). Just before the write the patch reads
  the current pattern number and the play-buffer pointer with interrupts off (the sequencer interrupt swaps buffers and sets the number
  together). If the current pattern is no longer the source (song mode moved on, or
  the user switched), the copy is dropped with "Pattern changed - try again". Within
  one update the buffer can't change: the sequencer only swaps pointers, and only
  the main loop refills the preload buffer.
- **The file manager must be idle** for each step that touches the card, so the
  copy never runs alongside a project save or open, or a pattern load; otherwise it
  waits for the next update (and gives up after about 2,000 updates with "Busy - try
  again").
- **Undo:** not added. The current pattern doesn't change, so the Pattern screen's
  undo ring has nothing to revert; the target was empty, so clearing it undoes the
  copy. The Song screen's file undo (`0x600c3e30`) could hold the target's old file,
  but an undo there for an action on the Pattern screen would be surprising.
- **Pattern names** (`patternsMetadata`) are not touched: the target keeps its name,
  normally none.

## 4. The button

Page 3 of the bottom bar (`pat_bind_page3` `0x600e29a4`, labels `0x600e584c`) binds
and labels each button with its own call. The patch takes button 2, the first unused
one, unless another patch already labels it: it reads where the call that sets button
2's text (`0x600e5884`) goes, and when that is a patch's hook (Humanize's) it takes
button 3. Its label hooks run after button 2's calls, so it can set button 2's labels
from button 3's sites without touching any of humanize's four:

| Hook | Site | What |
|---|---|---|
| `mt_cp_bind` | `bl btn_bind` at `0x600e29c6` (button 1, Track Name) | Binds button 1, then in record mode its button (2 or 3) to `mt_cp_button` |
| `mt_cp_label` | `bl disp_ctrl_set_text` at `0x600e5890` (button 3's empty text) | "Copy to" on its button |
| `mt_cp_label2` | `bl disp_ctrl_set_ptr38` at `0x600e58f0` (button 3's empty second line) | "New" on its button |
| `mt_cp_colors` | `bl disp_ctrl_set_colors` at `0x600e592e` (button 0's) | Button 0's colors, then its button: active in record mode, greyed out otherwise |
| `mt_cp_data` | `bl disp_ctrl_set_data` at `0x600e5958` (button 0's) | Button 0's data, also given to its button |
| `mt_cp_pattern_update` | Pattern screen vtable slot `0x600403dc` | The scan and the write, then the next update in the chain |

All six are chained (`next`), so another patch can hook the same sites later. The
update slot is already shared (humanize, interpolate, masterfx, swing). Humanize
and copypattern work alone and together, in either order.

The press (`mt_cp_button`) does no I/O: it records the source and starts the scan.
Messages: "Copied to pattern N", "No empty pattern left", "Pattern changed - try
again", "Copy failed - SD card" (after a failed write the partial file is removed,
so the target stays empty), "Busy - try again".

## 5. RAM

48 bytes of state in `.mt_ram` (magic, project session, copy flag, source, next
candidate, count left, wait counter, and the 32-byte message, which the message bar keeps
pointing at). Not used before `main`, so the patcher may place it in SDRAM.

## 6. Tests (`tools/emu/test_copypattern.py`)

With the static constructors run (`seq_timing.make`, for the CRC object) and a fake
SD card:

- page-3 binding and labels, in and out of record mode; humanize's button 2 still
  there and unchanged in bundles;
- copy while stopped and while playing in song mode (sequencer ticking between
  updates): play buffer, preload buffer, song position and current pattern unchanged;
- lengths 16, 32, 64 and 128, and different per-track lengths;
- unsaved edits (play buffer differs from the source's file) are what gets copied;
- used patterns skipped: by mask, by file content (a note, an instrument, an FX type
  past the pattern length), in the playlist, queued; an empty file and a missing file
  both count as empty; wrap-around;
- no empty pattern left: message, nothing written;
- the source changing during the scan: dropped with its message, nothing written;
  another project opened during the scan: dropped silently;
- the written file byte-for-byte (stock header and CRC, as `pattern_write_file`
  writes it for the same buffer), the stock loader (`pattern_load_now`) reads it back
  identical to the source, the masks are updated and both unsaved flags set;
- I/O rules: nothing on the press, at most one file per update, reads in pieces of
  at most 256 bytes, nothing while the file manager is busy, the stock update runs
  on every call.
