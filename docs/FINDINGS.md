# Tracker+ firmware findings

Target: stock **TrackerPlus_1.2.0.ptf**. Addresses are 1.2.0 unless stated. Names in
`code` match `ghidra/names_1.2.0.tsv`; a `_q` suffix means "best guess".

## 1. Image format

- `.ptf` is plain Intel HEX with CRLF line endings. It is **not encrypted, compressed
  or signed** as far as the file itself goes (no header, no trailer, no checksum block).
- Records: type 04 (extended linear address), variable-length 00 data records that
  follow the linker sections, one 05 (entry `0x60030319`), one 01.
- One contiguous range `0x60030000`–`0x601bab78` (1,616,760 bytes) in 1.2.0.
  1.0.0 is 1,448,096 bytes and 1.0.3 is 1,588,520, with the same base and entry.
- `tools/ptf.py check` re-encodes each stock file byte for byte (all three pass).
- The `.ptf` does **not** contain anything below `0x60030000`. Flash
  `0x60000000`–`0x6002ffff` holds the bootloader (and the i.MX boot header it needs),
  which we do not have.
- Original Tracker note (Tracker-Firmware-Mod): its updater failed on gaps in the
  data, and file names longer than 31 characters showed garbage. Unverified on Tracker+,
  but patches should keep the image gap-free and use short names until tested.

## 2. Chip and memory map

NXP **i.MX RT10xx, most likely RT1050/1052** (Cortex-M7). The Tracker-Firmware-Mod
project says RT106x, but the firmware uses none of the RT106x-only peripherals
(GPIO6-9, FlexSPI2, FlexIO3, ENET2, CAN3), and RAM beyond the 32 KB FlexRAM OCRAM
bank doesn't exist on the unit: an access to `0x20280000` froze it (section 11). Built with NXP MCUXpresso SDK + GCC 10 (newlib
paths from Arm's GCC-10 2021.10 build). No Arduino/Teensy core.

The reset handler (`0x60030318`) does:

1. `IOMUXC_GPR->GPR17 = 0xaaaaabfd`, `GPR16 |= 4`: FlexRAM = 128 KB ITCM,
   352 KB DTCM, 32 KB OCRAM. Stack top `0x20058000` = end of DTCM.
2. `SystemInit` (`0x60094b50`).
3. Data copy table at `0x600302a0` ({load, exec, len}):

   | load | exec | len | what |
   |---|---|---|---|
   | `0x601ad21c` | `0x20000000` | `0x4378` | DTCM .data |
   | `0x6018d6ec` | `0x00000008` | `0x1fb30` | **ITCM code** (~127 KB of RAM functions) |
   | — | `0x20200000` | 0 | OCRAM |
   | — | `0x80000000` | 0 | SDRAM |

4. BSS table at `0x600302d0`: `0x20009800`+`0x263e4` (DTCM), `0x20200000`+`0x4f4c` (OCRAM).
5. Fills `0x20053000`..stack with `0xAA` (stack watermark; the debug screen shows
   "max stack").
6. `libc_init_array` (`0x60166294`), then `main` (`0x600a1edc`).

Ghidra memory blocks (`SetupTrackerPlus.java`): FLEXSPI image, ITCM `0x0`, DTCM
`0x20000000`, OCRAM `0x20200000` (32 KB: the one FlexRAM bank configured as OCRAM),
SDRAM `0x80000000`
(64 MB), peripherals `0x40000000`, PPB `0xE0000000`.

## 3. Firmware update path (safety net)

`fw_request_update` (`0x600a1d94`), called after "Do you want to flash new firmware?":

1. Deletes `/Firmware/_fwinfo` if it exists, then writes the chosen `.ptf` file name into it.
2. Writes the 5 bytes `start` into `/Firmware/_turnon`.
3. Calls `system_reset` (`0x600a1d68`): `SCB->AIRCR = 0x05FA0004`, then spins.

`fw_clear_turnon` (`0x600a1d3c`) deletes `/Firmware/_turnon`.

What this means:

- **The application never writes flash.** There are no references to the FlexSPI
  controller registers (`0x402A8000`) anywhere in the image. The two hits for the ROM
  API tree address `0x0020001c` are at odd (non-literal) offsets, so they are data
  noise, not calls.
- **The bootloader does the flashing**, after reading `_turnon` / `_fwinfo` from the SD card.
- A `.ptf` only covers `0x60030000`+, so flashing a broken image should leave the
  bootloader intact and able to flash stock again.
- **Emergency reflash confirmed on Tracker+.** With the
  unit on and the SD card in, pressing the pinhole next to USB-C starts "Updating..."
  from the bootloader. It installed the `.ptf` in `/Firmware` (only stock
  `TrackerPlus_1.2.0.ptf` was there), then the unit booted into the last project.
  Version shown afterwards: "1.2.0 Build 1504". Sample playback and MIDI out worked.
  This is the recovery path if a modified build doesn't boot: put only the stock
  `.ptf` in `/Firmware` (from a PC) and press the pinhole.
- **Modified images are accepted (`patch/versiontest`).** Installed
  `TrackerPlus_MT0.ptf` through Config → Firmware: the update ran, the unit booted
  and showed "1.2.0-MT0 Build 1504", and samples and MIDI out played normally.
  Updating back to `TrackerPlus_1.2.0.ptf` through Config restored "1.2.0 Build 1504".
  So the bootloader takes a `.ptf` whose records differ from Polyend's, takes an image
  longer than stock (1,429 extra bytes), and programs a flash sector the stock image
  never uses (`0x601bb000`). No checksum or signature over the whole image is
  enforced beyond the per-record Intel HEX checksums.
- Config shows the version as the `sprintf` buffer at `0x200205d4` followed by a
  separate "Build" field (string referenced from the config screen `0x60118acc`).

Still unknown: the bootloader's maximum image size, and exactly what it does with
`_turnon` at power-on. Those can only be learned from the
bootloader itself, which we cannot read without a debug probe.

## 4. Display

- **EVE graphics controller (FT81x / BT81x family) over LPSPI**, same family as the
  original Tracker. No LCDIF/PXP use. 140 `CMD_DLSTART` constants, plus `RAM_DL`,
  `REG_DLSWAP`, `REG_CMD_WRITE`.
- Display-list helpers (`0x6011f6b0`…`0x6011fef8`), each appends one 32-bit word:
  `eve_color_rgb` = `0x04000000 | rgb` (full 24-bit color), `eve_vertex2f` =
  `0x40000000 | x<<15 | y`, `eve_begin` = `0x1F000000 | prim` (9 = RECTS,
  4 = LINE_STRIP, 3 = LINES), `eve_end`, `eve_line_width`, `eve_blend_func`,
  scissor and context save/restore, `eve_cmd_gradient`, `eve_cmd_text_q`.
- Global display struct at `0x20027d64`: `[0]` = display-list writer object (vtable
  `+0x14` advance, `+0x18` write one word), `[8]` = style table. The static
  constructors set `[8]` = `0x6005a4c0`, the default style table in flash. Each control
  class takes its colors from a fixed offset in it (bar `+0x124`, song control `+0x31c`).
- Song control colors (default theme, `0x6005a4c0+0x31c`): `+0x04` filled `#ffffff`,
  `+0x14` selection `#cd1818`, `+0x1c` filled/flagged track `#444344`, `+0x20` empty
  `#121212`, `+0x24` `#30cb3f`.
- **All `eve_color_rgb` callers are display-library control classes**
  (`0x60101000`–`0x6010f000`, plus `0x6011ffd8` and `0x60120350`). Screens never
  draw directly. They create controls and set text, values, colors and data.

### Control library

- A display object holds up to 0x41 (65) control pointers at `display + 0x10 + 4*i`.
  Screens keep the returned byte index.
- Control object layout (common): vtable at `+0x00`, flags at `+0x0c`, x/y at
  `+0x1c/+0x1e`, w/h at `+0x20/+0x22`, color table pointer at `+0x28`.
- Virtual methods (offset in vtable): `+0x00` draw/update, `+0x04`…`+0x10` shared
  base (`0x6010105c`…), `+0x14` setStyle, `+0x18` setText, `+0x1c` setValue,
  `+0x20` setColors (array of `0x00RRGGBB`, values above `0xFFFFFF` are rejected),
  `+0x24` setData.
- Wrappers: `disp_ctrl_set_*` at `0x60101c6c`…`0x60101e10`, `disp_ctrl_refresh`
  `0x60101e84`.
- 25 control classes share the base methods (vtables `0x60043090`…`0x60046e00`,
  `0x60061860`, `0x60061908`). `work/vt_ctor_refs.txt` and `work/ctor_callers.txt` map
  each one to its constructor and to the screens that create it.
- Identified so far: text list (`0x60105140`), two-column list (`0x60106d6c`), list
  (`0x60108b7c`), bar (`0x601010c4`), on-screen keyboard (`0x60120350`), text edit
  (`0x6011ffd8`). All use 27-px rows.

## 5. Screens

Screens are classes with vtables (`0x6003b5f0`, `0x6003e1f0`, …, `0x60042dd0`).
Slot 3 is the create-controls method. Modules, identified by the strings their code uses:

| Module | Content |
|---|---|
| `0x600c6000` | Config / firmware update / debug info / SD + USB messages |
| `0x600d0000`–`0x600d1000` | Manual viewer, instrument parameters |
| `0x600d8000` | Mixer, master FX, limiter, EQ |
| `0x600de000` | Pattern editor (fill, track/pattern names, render selection) |
| `0x600e5000` | Pattern actions (length, step jump, invert, duplicate, expand) |
| `0x600e6800` | Performance mode |
| `0x600e9000` | File browser (open/save/import mod/export) |
| `0x600ec800` | Sample editor |
| `0x600f0000` | Sample loader |
| `0x600f3c00` | Sample playback (loops, slices) |
| `0x600f8000` | Sample recorder |
| `0x600fd000` | **Song screen** (confirmed against a photo of the device) |
| `0x6010e000` | Factory test procedure |

## 6. Song screen and the track-usage grid

The Song screen (photo: 16 rotated track names across the top, one row per song slot,
white block = "this track has content in this slot's pattern", pattern list on the
right) is module `0x600fd000`.

### Drawing: `song_grid_draw` (`0x60108eac`)

Called from `song_ctrl_draw_grid` (`0x60109038`), a method of the song control
(`song_ctrl_ctor` `0x6010827c`, 0x5c bytes, created by `scr_song_create_controls`).
Data pointer `g = ctrl+0x34` (filled by `scr_song_set_grid_data`, `0x600fe25c`,
which points it at `screen+0xcc`):

| `g` offset | meaning |
|---|---|
| `+0x00` (u8) | number of used song slots |
| `+0x04` | → playlist (255 × u8 pattern numbers, 1-based; = `project+0x5c2`) |
| `+0x0d..+0x11` | selection active / first row / row count / first col / col count |
| `+0x1c` / `+0x1d` | track counts (8 + 8) |
| `+0x20` | → `lo[]`: per-pattern track-used bits 0–7 |
| `+0x24` | → `hi[]`: per-pattern track-used bits 8–15 |
| `+0x28` / `+0x2c` | → per-track flag bytes (tracks 0–7 / 8–15), pick the alternate fill color |

Per visible row (max 12, `ctrl+0x32` = scroll, row pitch 27 px) and per column
(pitch 30 px):

```
p    = playlist[row + scroll] - 1
mask = hi[p] << 8 | lo[p]
if mask & (1 << t):  color = flag[t] ? theme[+0x1c] : theme[+0x04]
                     RECT (x+5, y+0x41) .. (x+30, y+0x56)
else:                color = theme[+0x20]
                     RECT (x+5, y+0x40) .. (x+31, y+0x57)
```

All blocks are inside one `BEGIN(RECTS)` … `END`, so changing a color is just a
different `eve_color_rgb` argument. **This is the hook point for colored blocks.**

### Data: project track-usage masks

- Project struct in OCRAM at `0x20204000` (literal base `0x20200000` + `0x4000`).
  Playlist at `+0x5c2`. `lo[p]` = byte at `+0x8cc + p` and `hi[p]` = `+0xbf6 + p`,
  for p = 1..255 (absolute `0x202048cc+p`, `0x20204bf6+p`).
- **Writer: `pattern_update_track_usage` (`0x600c3998`)** `(ctx, patternNo, patternData)`.
  Pattern data in RAM has the `.mtp` layout: 0x1c-byte header, then 16 tracks × 0x301
  (1 length byte + 128 steps × 6 bytes: note (i8, −1 = empty), instrument, fx1 type,
  fx1 value, fx2 type, fx2 value). The pattern length comes from track 0's length byte
  (`data+0x1c`). A track is "used" if any step up to that length has note ≠ −1,
  fx1 type ≠ 0, fx2 type ≠ 0, or instrument > 47. Returns true if the mask changed.
- Callers: `pattern_update_usage_current` (`0x600c3b30`, after every pattern save),
  pattern load (`0x600c11e0`), and `0x600c3b58`, `0x600c3a8c`, `0x600c3eac`, `0x60117730`.
- Readers (only read): `0x600c6bd8`, `project_collect_used_patterns_q` (`0x600e1798`),
  `0x600e24f4`, `0x600fe100`, and the project-copy size estimate in `0x60114a60`.

## 7. Colored song blocks (`patch/colorsong`)

See `patch/colorsong/README.md` for the design and its limits. Hook
sites: the six `bl pattern_update_track_usage` at `0x600c1360`, `0x600c3b04`,
`0x600c3b3e`, `0x600c3c8e`, `0x600c3efc`, `0x60117740`, and the filled-block
`bl eve_color_rgb` at `0x60108fd2` (normal) and `0x60108f7c` (flagged track). At
the color sites `r9` = song control, `r10` = visible row, `r5` = track. None of the
usage callers loops over a whole project: masks are loaded with the project, and
the firmware only recomputes them for the pattern being loaded, saved or copied.

How the patch uses them:

1. **Fingerprints.** The usage hook runs the original, then fingerprints each track of
   the pattern: the set of instruments used (sorted, so order doesn't matter) picks the
   color family, and everything else (step positions and notes, both FX types and
   values, the pattern length) picks the shade.
2. **Drawing.** The two color sites call the patch, which turns the block's
   fingerprint into RGB, or keeps the theme color when no fingerprint exists yet.
3. **Coverage.** Masks come with the project, but fingerprints only exist for patterns
   loaded or saved since boot, so the Song screen's update method reads the missing
   pattern files, one per update (section 11 explains why there).
4. **Space** (section 8): code appended after the image end at `0x601bab80`, the
   table in the free OCRAM tail at `0x20205000`.
5. **Emulator first:** `tools/emu/song_grid.py` (section 9) renders the grid from a
   real project with the stock code, so a patched build can be checked before it
   goes near hardware.

## 8. Free space for patches

Address constants actually loaded by code (`ldr rX, [pc, #…]`), plus a MOVW/MOVT
sweep, per region:

| Region | Used by firmware? | Verdict |
|---|---|---|
| Flash after image end `0x601bab78` | Only the GCC crtstuff end-of-image symbol (`0x600908a4`, `0x600908c8`). No self-check of the image | **Patch code goes here**, appended with no gap |
| Flash `0x601b1594`–`0x601bab78` | 0x95e4 bytes, mostly zeros, inside the image and not in the copy tables | Leave alone (may be referenced const data) |
| DTCM `0x2002fc68`–`0x20051c67` | The heap (`_sbrk`, section 15); above it 5 KB, then the stack | Heap, not free; the 5 KB gap is a candidate (section 15) |
| OCRAM `0x20204f4c`–`0x20207fff` (tail of the 32 KB bank) | No address constants, no MOVW/MOVT | **Patch RAM goes here**: the patcher's first region (section 15); colorsong's 12,208-byte table and humanize's 32 bytes of settings both fit |
| `0x20208000`–`0x202fffff` | Doesn't exist on this unit (reserved address space) | **Never use.** An access to `0x20280000` froze the unit (section 11) |
| SDRAM `0x80000000`–`0x83ff9bef` | Sample memory and fixed buffers, the last two at `0x83ff5bf0`–`0x83ff9bef` | Not free; the 25 KB above is a candidate (section 15) |

Caveat: the bootloader's maximum image size is unknown (1.2.0 is 1.6 MB and grew
28 KB over 1.0.3). `patch/versiontest` exists to test exactly that on hardware.

### MPU (`BOARD_ConfigMPU`, `0x600932a4`, decoded by running it in the emulator)

`MPU_CTRL = 5` (enabled, privileged default map as background). Regions:

| # | Base | Size | Access | Type |
|---|---|---|---|---|
| 0 | `0x00000000` | 4 GB | none, XN | background deny |
| 1 | `0x80000000` | 512 MB | RW | Device |
| 2 | `0x60000000` | 512 MB | RW | Device |
| 3 | `0x60000000` | 16 MB | RO, exec | Normal WB (FlexSPI flash) |
| 4 | `0x00000000` | 1 GB | RW, exec | Device |
| 5 | `0x00000000` | 64 KB | RW | Normal WB (ITCM) |
| 6 | `0x20000000` | 512 KB | RW | Normal WB (DTCM) |
| 7 | `0x20200000` | 64 KB | RW | Normal WB (covers the 32 KB OCRAM) |
| 8 | `0x80000000` | 64 MB | RW | Normal WB (SDRAM) |
| 10 | `0x40000000` | 4 MB | RW | Device (peripherals) |

Region 4 allows access to `0x20280000` ("FlexRAM OCRAM" on an RT106x) as Device
memory, but there is no RAM behind that address on this unit, so the access
bus-faults. The MPU says what is *allowed*, not what *exists*. Patch RAM belongs in
RAM that exists, such as the OCRAM tail at `0x20205000` (region 7, Normal memory).

`SystemInit` (`0x60094b50`) itself only enables the FPU, sets `VTOR = 0x60030000`,
disables WDOG1/WDOG2/RTWDOG and SysTick, and enables the I-cache.

### Firmware list and version text

- Config's firmware list accepts names that start with `TrackerPlus` and end with
  `.ptf` (case-sensitive), skipping macOS `._` files (`0x6009ded0`, case 4).
- The version text is built at boot by `0x600cbe88`: `sprintf(0x200205d4, fmt, 1, 2, 0)`,
  where `fmt` is loaded from the literal at `0x600cbf9c` (`"%d.%d.%d"`, or the beta
  format via `0x600cbf7c`). Patches repoint that literal to a string in their own
  blob, so the device shows which build is running (`1.2.0-MT0`, `1.2.0-c10`). Keep the
  text to 9 characters: a 10-character one didn't show in Config.
  `tools/emu/check_version.py` prints it from any `.ptf` with the firmware's own
  `sprintf`.

## 9. Emulator

`tools/emu/machine.py` loads the stock `.ptf` into Unicorn with the unit's memory map
(only the 32 KB OCRAM at `0x20200000`, so a stray access to non-existent RAM stops the
emulation the way it faults on hardware)
and the startup copies, and calls functions directly. The display object at
`0x20027d64` is replaced with a fake whose write method records display-list words.
`tools/emu/eve.py` decodes those words and renders RECTS/LINES/POINTS to a PNG.

- `libc_init_array` (all static constructors) runs to completion with peripherals
  mapped as plain RAM. That's how the default style table pointer was found.
- `tools/emu/song_grid.py <ptf> <project dir> <out.png>` loads the project's
  playlist, runs every used pattern through the stock `pattern_update_track_usage`,
  and calls the stock `song_grid_draw`. For a real project it produces the same grid
  layout and colors as the device (579 display-list words).
- `tools/emu/seq_timing.py <ptf>` plays constructed patterns through the stock
  `seq_tick` with logging fake MIDI ports and prints when each note, CC, preload request
  and buffer swap happens (section 12).

## 10. File system, project file, Song screen entry

- **FS object**: `*(0x2001e488)` (the file-manager object's first field), class vtable
  `0x60038224` (embedded in the app object at `+0x33c`). Methods: `+0x08` exists(fs,
  path), `+0x14` remove, `+0x2c` open(&res, fs, path, mode, 0) with `res = {u32 handle,
  u8 ok}` and mode 0 = read (allocates a 0x104-byte handle), `+0x30` read(fs, buf, size,
  handle) → bytes read or −1 (FatFs `f_read`), `+0x34` write(fs, buf, size, handle) →
  bytes written (FatFs `f_write` via `0x6009e7ec`), `+0x40` close(fs, handle, 1).
  Open modes 1-3 map through the table at `0x60038214` to FatFs flags `0x13`, `0x33`,
  `0x0b`. `pattern_write_file` uses remove(fs, path), then open mode 3 (`0x0b` =
  create-always + read/write), then write, then close.
  Paths are relative to the SD root, e.g. `Workspace/patterns/pattern_01.mtp` (format
  string `0x6003b294`, built with `snprintf` `0x6016712c`).
- **File manager object** `0x2001e488`: project name string at `+0x340`, project path
  at `+0x241` (both are saved into `project.mt` at `+0x810` / `+0x831`).
- **Current pattern**: number = byte `0x202046c8`, data buffer = `*(0x20017274+0x14)`.
- **project.mt** (`project_serialize` `0x600c2600`, saved by `project_save_workspace`
  `0x600c2690`, 0x914 bytes): header, playlist RAM `0x202045c2` → file `+0x10`
  (0x100 bytes), then RAM `0x202046c4` → file `+0x110` (0x700 bytes). That block holds
  the track masks: `lo[p]` at file `0x318+p`, `hi[p]` at file `0x642+p`. Checked in the
  emulator: for a real project, all 14 used patterns match what the stock
  `pattern_update_track_usage` computes from the `.mtp` files. So masks are loaded
  with the project and never rebuilt by a scan.
- **Project open copies patterns into Workspace** with `project_copy_patterns`
  (`0x6011458c`): `"%s/%s/patterns/pattern_%02d.mtp"` (project path `0x2001e6c9`,
  name `0x2001e7c8`) → `"Workspace/patterns/pattern_%02d.mtp"`, one pattern per
  state-machine step, through the chunked copier `file_copy_step` (`0x600c4578`,
  copier object `0x2001f730`). The copier returns 4 immediately if the source is
  missing; otherwise it deletes the destination, then copies 0x7f80-byte chunks across
  calls. Its other callers copy instruments (`0x60113d40`, `0x60114634`, `0x60114d1c`),
  `project.mt` (`0x60114c38`) and patterns back to the project on save
  (`0x60114c7c`). colorsong hooks the call at `0x601145ce`.
- **Song screen entry** `scr_song_start` (`0x600fe3d8`): `pattern_update_usage_current`
  for the current pattern, then `bl scr_song_set_grid_data` at `0x600fe402`. colorsong
  hooks that BL to check the session and start its rescan (no file I/O there).

## 11. Hardware lessons

**RAM the MPU allows may not exist.** A color table at `0x20280000` froze the unit as
soon as the Song screen opened, even in a build with no SD I/O at all. There is no RAM
at that address on this unit (section 2), so the first access bus-faulted, and the
fault handler spins forever. Playback never touched the table, so everything else
kept working. Lessons: the MPU tells you what is allowed, not what exists; check RAM
claims against the RAM the firmware actually uses; and the emulator must not map more
memory than the device has (it maps only the real 32 KB OCRAM and reproduces the
fault).

**SD I/O only where the stock code does it (a precaution).** The stock Song screen
does SD I/O only from its update method `scr_song_update` (`0x600fdd38`, vtable slot
`0x60042dd8`, e.g. song copy via `0x600c3b58` → `pattern_read_file` /
`pattern_write_file`). `SD_ReadBlocks` (`0x6009f680`, card object `0x2000b118`) takes
the card lock with an infinite wait (`0x6009de84(card+0xae, -1)`) and handles
unaligned buffers itself. FatFs `f_read` (`0x6009c398`) reads full sectors straight
into the caller's buffer for requests of 512 bytes or more at a sector boundary, and
otherwise copies from the file's own sector buffer (`FIL+0x50`). Rule for patches:
only do SD I/O where the stock code does, keep requests small, and spread work over
update calls. (Not proven necessary, but it costs nothing.)

## 12. Sequencer and FX timing

The sequencer object is at `0x20017274` (`seq` below). Its time-critical code is in ITCM
(copied from flash at boot, section 2); step advance, song handling and FX editing are in
flash around `0x600aa000`–`0x600b0000`. It follows the original Tracker closely (the
OpenTracker behavioural model matches it nearly function for function), with 16 tracks.
`tools/emu/seq_timing.py` runs it in the emulator.

### Where it runs

All of it runs in the audio interrupt, once per 128-frame block (about 2.9 ms):

| Function | Address | What it does |
|---|---|---|
| `dma0_dma16_irq` | `0x600959d4` | IRQ 0: NXP eDMA handler for the audio-out DMA channel |
| `audio_dma_cb` | `0x60091954` | picks the free half of the ping-pong buffer, calls `*(0x2000a804)` |
| `audio_out_cb` | `0x600a1e48` | calls the app object's vtable `+8` with 128 frames |
| `app_audio_block` | `0x600aa7c8` | app object `0x200170e0` (vtable `0x6003871c`): `midi_poll_input`, then `bl seq_advance` at `0x600aa7f8` with frames / 44122.65 seconds, then `engine_block_update` |
| `seq_advance` | ITCM `0x3774` | internal clock: adds the elapsed µs to a float at `0x20017270` and runs `seq_tick` while it holds a whole tick period. External clock: 96 ticks per received MIDI clock pulse |
| `seq_tick` | ITCM `0x35e8` | count-in, else `++seq+0x10`; every 12th tick one micro-step |

A micro-step is `seq_master_micro` (ITCM `0x1de8`), then for t = 0..15:
`seq_process_track(seq, t)` (ITCM `0x203c`), `micro++`, and past 48 micro = 1 and
`seq_advance_step(seq, t)` (`0x600ad010`).

- **Same grid as the original Tracker:** 12 ticks per micro-step, 48 micro-steps per
  step, 576 ticks per step. `seq_tick_period_us` (ITCM `0x1084`) = 312500 / bpm / 12
  (217.0 µs at 120 BPM). With swing on it is (s + 50) × 3125 / bpm / 12, with s and
  100 − s alternating per step. `seq_step_ms` (ITCM `0x1014`) = 125 ms at 120 BPM. Tempo
  source: Tempo FX override (`seq+0x58`/`+0x5c`), external clock BPM (`+0x7d4`) or the
  project tempo (`0x20204774`).
- **The real resolution is the audio block.** All ticks due in a block are processed in
  one burst at its start: about 13 ticks (1.1 micro-steps) per block at 120 BPM, 2.8
  micro-steps at 300 BPM. MIDI goes out immediately, sample voices start with the next
  rendered block. With an external clock each pulse runs 8 micro-steps at once.
- **Cost** (emulator): 22 instructions for a tick without a micro-step; about 2,900 per
  micro-step with all 16 tracks busy (MIDI notes, a CC on every step, Micro-move on every
  other step), 9,100 at the worst step start.

### Sequencer object

| `seq` offset | Meaning |
|---|---|
| `+0x10` | tick counter |
| `+0x14` / `+0x18` | play pattern buffer / preload buffer (`.mtp` layout, section 6) |
| `+0x1c` / `+0x1d` | song mode / performance mode |
| `+0x1e`–`+0x24` | selection playback: once / loop, first and last step (q) |
| `+0x26` / `+0x28` / `+0x29` | queued pattern number / queue active / queued pattern requested |
| `+0x2a` / `+0x2b` | playing / recording |
| `+0x2e` / `+0x30` | master micro-step 1..48 / master step |
| `+0x32` / `+0x33` | Break FX pending / pattern end (set when track 15 wraps) |
| `+0x36`, `+0x38` | pattern length override (−1 = none), per-track lengths (performance mode) |
| `+0x58` / `+0x59` / `+0x5c` | Tempo FX override on / stop at step end (TSTP) / override BPM (float) |
| `+0x60` / `+0x61` / `+0x64` | swing on / swing phase / swing % (float) |
| `+0x68 + 0x68·t` | track t, 0x68 bytes (next table) |
| `+0x6e8` / `+0x6ec` / `+0x6f0` | callbacks: pattern end, pattern advanced, step changed (q) |
| `+0x6f4` / `+0x6f8` / `+0x6fc` | count-in: step, active, callback |
| `+0x700` / `+0x704` / `+0x70e` | external clock: pending pulses, leftover, sync correction |
| `+0x7d4` | external clock BPM (float) |
| `+0x7dc` | rendering (export); MIDI out is suppressed (`seq_is_rendering` `0x600ad4b0`) |

Track t, at `seq + 0x68·t + offset` (the code's own addressing):

| Offset | Meaning |
|---|---|
| `+0x68`–`+0x6e` | last triggered record: note, instrument, FX1 type, FX1 value, FX2 type, FX2 value, volume |
| `+0x6f`–`+0x75` | work record of the current step, same layout (volume `0xff` = none). Filled from the pattern at micro-step 1 |
| `+0x76`–`+0x7c` | record used by roll and arp retriggers |
| `+0x7d` | track enabled |
| `+0x7f` / `+0x80` | triggered / gate open (note-off pending) (q) |
| `+0x86` / `+0x88` | micro-step 1..48 / current step |
| `+0x8a` | steps played since the loop start (random and table play modes) |
| `+0x8c` / `+0x8e` | performance FX injected into an empty FX column |
| `+0x90` / `+0x92` | micro-steps since the trigger / limit |
| `+0x94` / `+0x96` | gate counter / gate length (q FX: v × 47 / 100; −1 = none) |
| `+0x98` | Chance failed this step |
| `+0x9a`–`+0xa3` / `+0xa4`–`+0xae` | roll state / arp state |
| `+0xb0` / `+0xb1` / `+0xb4` | performance transpose on / amount / velocity offset |
| `+0xb8` / `+0xb9` | performance step repeat / play mode (0 forward, 1 backward, 2 random, ≥ 3 pattern tables) |
| `+0xbc` | queued per-track pattern (performance mode; −1 = none) |
| `+0xc0` / `+0xc1` | delayed note pending / its record captured |
| `+0xc2` / `+0xc4` | delay / counter, in micro-steps |
| `+0xc6`–`+0xcc` | delayed record |

Other state it uses: settings `0x2002061c` (`+0x26` clock in, `+0x27` clock out, `+0x29`
transport out, `+0x2c` MIDI out: 0 off, 1 USB, 2 jack, 3 both; `+0xce` recording
options); project `0x20204000` (`+0x5c2` playlist, `+0x6c1` song position, `+0x6c8`
current pattern, mute flags `+0x6e0 + t` and `+0xcf6 + (t & 7)`, MIDI instruments at
`+0xaa0 + 0x10·channel` with `+3`–`+8` = CC numbers for FX a–f and `+9` = MIDI delay);
8 sample players of 0x848 bytes at `0x2001a138`; audio engine `0x20017b08`.

### Step advance, pattern end, song mode

`seq_advance_step(seq, t)` (`0x600ad010`) runs right after track t's micro-step 48:

- The last step index is track 0's length byte in the play buffer, or `seq+0x36`, or the
  track's own length in performance mode; a looping selection narrows it.
- Forward: step + 1, and past the last step (or with Break pending) back to the loop
  start. Backward, random and table modes (performance mode, `+0xb9`; tables at
  `0x600ad3d0`) choose the next step here. A queued per-track pattern (`+0xbc`) switches
  when its track wraps.
- Track 15 also toggles the swing phase, handles TSTP, and if it wrapped ends the
  pattern: a queued pattern leaves song mode and switches; song mode calls
  `song_advance_position` (`0x600c3970`, wraps at the first empty playlist slot); both
  then `seq_reset_positions` (ITCM `0xf50`) and `seq_swap_pattern_buffers` (`0x600acc68`:
  swaps `+0x14`/`+0x18`, clears roll, arp and the Tempo FX override, sets the current
  pattern number). In pattern mode each track has already wrapped on its own.

**The next pattern is loaded asynchronously.** In `seq_process_track`, track 0 at
micro-step 1 of the last step calls `fm_request_pattern_load(0x2001e488, n)`
(`0x600bc938`) with `song_next_pattern()` (ITCM `0x7030`) in song mode, or with `+0x26`
for a queued pattern (and sets `+0x29`). That only sets file-manager state (`+0x12c` =
pattern, `+0x13d`/`+0x13f` = 0xe). The main loop then saves the current pattern if it was
edited, reads `Workspace/patterns/pattern_NN.mtp` (0x3030 bytes) into SDRAM `0x83f58bb4`
(`fm_preload_pattern_step` `0x600c1e1c`) and copies it into the preload buffer
(`seq_load_preload_buffer` `0x600ad570`). Nothing waits for that: the buffers are swapped
after micro-step 48 of the last step either way. In the emulator the request comes at
micro-step 1 of the last step and the swap 47 micro-steps later. So the next pattern's
data exists only during the last step, and only once the SD read is done.

### Per-track step processing and the FX switch

`seq_process_track(seq, t)` (ITCM `0x203c`), every micro-step, for every track:

1. At micro-step 1, copy the 6-byte step from `*(seq+0x14) + 0x1d + 0x301·t + 6·step`
   into the work record (`memcpy_small` ITCM `0x1f514`, called at ITCM `0x2170`), inject
   performance FX, clear the volume.
2. Counters: since-trigger, gate (note-off when it reaches the gate length), delay
   counter, arp. Track 0 also sends MIDI clock (`seq_send_midi_clock` ITCM `0xa7c`: 0xF8
   every 8 micro-steps, 6 per step, when clock out is on) and makes the preload request
   above (compares at ITCM `0x23b4` and `0x2578`, request through the veneer at
   `0x1fa70`).
3. Disabled or muted (`seq_track_muted_q` ITCM `0x3994`): stop here.
4. At micro-step 1: Random FX value pre-pass (16), MIDI-instrument delay ((b + 1) × 45 /
   100 + 1 micro-steps), Chance pre-pass (4), then the FX pass, FX2 first, then FX1:
   `tbh` at ITCM `0x2686` on type − 1 for types 1..37 (higher types take the default).
   Cases: 1 Off `0x2dde`, 2 Micro-move `0x2d80`, 7 Random volume `0x2d30`, 8–12 and 37
   CC a–f `0x26d8`, 13 Break `0x2eda`, 15 Tempo `0x2e42`, 17 Swing `0x2f10`, 18 Volume
   `0x2e96`, 20 Gate `0x2d0e`, 34 Micro-tune (MIDI pitch bend) `0x2c66`. On a step
   without a note the default forwards the FX to the sounding voice (`voice_apply_fx`,
   tracks 0–7) or to the synth engine (instruments 64–66, `synth_apply_fx_q` ITCM
   `0x5070`). A second pass handles Roll (3), Random note (5), Random instrument (6) and
   Arp (21).
5. A note (unless delayed) goes to `seq_trigger_note` (ITCM `0x149c`). Instruments 0–47
   on tracks 0–7 → `voice_note_on_q(player, instrument, note, volume, FX1, FX2)` (ITCM
   `0x5c04`): both FX travel with the note-on. Instruments 48–63 (MIDI channels 1–16) and
   64–66 (synths) → `seq_note_on_out_q` (`0x600abb44`). The work record becomes the last
   record.
6. Every micro-step: delayed-note check, roll retriggers, arp, performance step repeat.

FX type ids (the UI list order maps to ids through the 43-byte table at `0x60039ca4`;
names at `0x6003bd88`). Stored ranges from `fx_min`/`fx_max`, shown values from
`fx_display_value`, run in the emulator:

| id | FX | stored | id | FX | stored |
|---|---|---|---|---|---|
| 1 | ! Off | 0 | 22 | p Position | 0–100 |
| 2 | m Micro-move | 0–100 | 23 | g Volume LFO | 0–23 |
| 3 | R Roll | 0–53 | 24 | h Panning LFO | 0–28 |
| 4 | C Chance | 0–100 | 25 | S Slice | 0–47 (shown 1–48) |
| 5 | n Random note | 0–100 | 26 | r Reverse | 0–1 |
| 6 | i Random instrument | 0–47 | 27 / 28 / 29 | L / H / B filter cutoff | 0–100 |
| 7 | v Random volume | 0–100 | 30 | s Delay send | 0–100 |
| 8–12 | a–e CC A–E / Macro 1–5 | 0–127 | 31 | P Panning | 0–100 (shown −50..50) |
| 13 | x Break pattern | 1 | 32 | t Reverb send | 0–100 |
| 14 | 0 MIDI Chord | 0–29 | 33 | l Finetune LFO | 0–28 |
| 15 | T Tempo | 4–200 (shown ×2; 4 = TSTP) | 34 | M Micro-tune / pitch bend | 0–198 (shown −99..99) |
| 16 | x Random FX value | 0–255 | 35 / 36 | j Filter LFO / k Position LFO | 0–28 |
| 17 | I Swing | 25–75 | 37 | f CC F / Macro 6 | 0–127 |
| 18 | V Volume / velocity | 0–100 | 38 | D Overdrive | 0–100 |
| 19 | G Glide | 0–100 | 39 | E Bit depth | 4–16 |
| 20 | q Gate length | 0–100 | 40 | U Tune | 0–48 (shown −24..24) |
| 21 | A Arp | 0–33 | 41 / 42 | F Slide up / J Slide down | 0–255 |

### Micro-move

Case 2 (ITCM `0x2d80`): delay d = 1 + (m + 1) × 47 / 100 + MIDI delay, capped at 96
micro-steps. It sets `+0xc0` = 1, `+0xc1` = 0, `+0xc2` = d, `+0xc4` = 0; later in the
same call the work record is captured into `+0xc6` (`+0xc1` = 1) instead of being
played. Every following call first increments `+0xc4`; when it equals `+0xc2`, the
record is copied back into the work record and goes through normal note handling
(note-off of the previous MIDI note, trigger, gate, last record). The step's other FX
(CC, volume and so on) still act at the step start.

Emulator, MIDI instrument on track 0:

| Step FX | Note sent at |
|---|---|
| none | micro-step 1 |
| m 0 | micro-step 2 (1 late) |
| m 50 | micro-step 25 (24 late) |
| m 97 | micro-step 48 (47 late) |
| m 100 | micro-step 1 of the next step (48 late) |

- m 0 still delays by one micro-step; m 99 and m 100 both give 48.
- **While a delayed note is pending, the note of a new step is dropped.** At the next
  step start the pending record is still captured, so the new note is ignored and the
  delayed one fires in its place. In the emulator, m 100 on step 4 followed by a plain
  note on step 5 sends only the step-4 note (at step 5's start); the step-5 note never
  goes out. The same happens when a MIDI-instrument delay pushes a note past the next
  step start. Not checked on hardware.
- Live recording with Microtiming writes FX1 = m, (master micro-step − 1) × 100 / 47
  clamped by `fx_min`/`fx_max`; with Velocity, FX2 = V (`seq_record_live_note` ITCM
  `0x15c8`, Micro-move store at `0x1b26`).

### MIDI out

- `midi_send_cc(cc, value, channel, synth)` (ITCM `0xacc`): synth → engine. Otherwise
  nothing while rendering, else by `settings+0x2c` to the USB port object
  `*(0x200171c0)` and/or the jack port object `*(0x200171c4)`, vtable `+0x0c`. The ports
  are set at init by `midi_set_ports` (`0x600ab808`) from the platform object
  `0x2000bedc` (`+0x320` USB, `+0x338` jack).
- Port vtable (jack class `0x60038478`, USB class `0x600384e8`): `+0x08` realtime byte,
  `+0x0c` CC, `+0x10` program change, `+0x14` channel pressure, `+0x20` note on, `+0x24`
  note off.
- CC FX need a MIDI instrument on the step. CC number = the MIDI instrument's byte
  `+3 + letter`; values 0x80/0x81/0x82 there mean program change / channel pressure /
  poly pressure (`0x600ab944`, `0x600ab9b8`, `0x600aba2c`). M (34) sends pitch bend
  (`midi_send_pitchbend_q` `0x600abad0`). A CC is sent once, at micro-step 1, with or
  without a note (emulator: CC steps 6 and 7 at their micro-step 1), and never again
  during the step.
- The jack is LPUART5 (`0x40194000`, set up by `din_midi_init` `0x600a2804`).
  `din_midi_send_cc` (`0x600a28d8`) writes status, number and value (3 bytes, no
  running status) with `serial_tx_byte` (`0x600a08e4`): a 512-byte ring drained by
  non-blocking LPUART transfers, **with no check for a full ring** (the write index just
  wraps). At 31,250 baud the jack carries 3,125 bytes/s, about 1,040 CCs/s in total; the
  ring holds about 160 ms of data.
- MIDI input is polled at the start of every audio block (`midi_poll_input` `0x600ab89c`).

### Voice FX and existing per-block updates

- `voice_apply_fx(player, type, value, column)` (ITCM `0x6244`) calls per-FX setters in
  `0x600b8000`–`0x600bb200` and remembers type and value per column
  (`player+0x791`/`+0x793`). It masks IRQ 0 while it works (`audio_irq_disable`
  `0x600303d4` / `audio_irq_enable` `0x600303c8`, NVIC bit 0), because the main loop
  calls it too.
- Filter cutoff (27/28/29): `voice_fx_filter_cutoff` (`0x600bb098`) stores
  (v − min) / (max − min) as a float at `player+0x30` and the filter type at `+0x34`,
  then `voice_update_cutoff_q` (`0x600bb020`) → `voice_set_cutoff` (ITCM `0x46cc`): a
  table lookup and a fixed-point coefficient written into each filter object of the
  voice. It takes effect at once, without smoothing.
- `engine_block_update` (ITCM `0x41c8`) runs right after `seq_advance` in the same
  interrupt: a 4-block tempo average, then `voice_block_update_q` (ITCM `0x6430`) for the
  8 players (envelopes, LFO FX, automation).
- What already moves between steps: roll, arp, gate, delayed notes and step repeat
  (sequencer, per micro-step); envelopes, tempo-synced LFO FX (g h j k l), glide and
  slides (voices, per block). No FX value is interpolated, and no CC is sent between
  step starts.

### FX value entry and display

- `fx_max(seq, type)` ITCM `0x3824`, `fx_min(seq, type, flag)` ITCM `0x38ac`,
  `fx_display_value(seq, type, v)` ITCM `0x3910`. Micro-move is 0..100 with no offset.
- `seq_change_selection_fx_value(selection, column, delta)` (`0x600af0f8`) is the
  encoder edit for a cell or a selection (called from `0x600e0ac0` and `0x600d47e0`).
  It clamps value + delta to [min, `fx_max`], with min hard-coded (Break 1, Tempo, Swing
  25, Bit depth 4, otherwise 0), not taken from `fx_min`.
- Other writers of FX values: the Fill popup (`0x600e01c8`, from/to `0x600e3e30` /
  `0x600e4038`, which use `fx_min`/`fx_max`), random fill (`0x600aef38`, `0x600af8e8`),
  `0x600af4f0`, `0x600af6a8`, and live recording (above).
- `fx_value_text(seq, buf, size, type, value, compact)` (`0x600afb48`) has its own text
  for Off, Roll, Break, Chord, Tempo, Arp, the LFOs and Reverse, offsets for Slice,
  Panning, Micro-tune and Tune, and `"%3i"` (`"%i"` when compact) for everything else,
  Micro-move included. The pattern grid (`patview_fill_rows_q` `0x600df3a0`) calls it
  through `0x600afdc8` with a 4-byte buffer, so a value cell has 3 characters: "-99"
  fits, "-100" does not.

### Feasibility

#### Negative Micro-move

Plan that reuses the stock delayed-note code:

1. **Value and UI.** Keep FX type 2 and store negative values in a range the stock code
   never writes (for example 101..199 for −1..−99). Hook
   `seq_change_selection_fx_value` (signed arithmetic for type 2), `fx_max`/`fx_min` for
   the Fill popup and random fill, and `fx_value_text` (signed text). Stopping at −99
   loses nothing: mirroring the stock formula, −99 already means a whole step (48
   micro-steps) early, just as +99 and +100 both mean 48 late, and it fits the
   3-character cell. Optionally live recording (ITCM `0x15c8`) could record late hits as
   negative m on the next step.
2. **Keep the stock code from delaying them.** Case 2 (ITCM `0x2d80`) would turn
   101..199 into a 48–95 micro-step delay. Either patch the case to skip values above 100
   or clear the type in the work record at the step copy (step 5).
3. **Look ahead.** When track t starts step k (the copy at ITCM `0x2170`), read step
   k + 1 in play order. If it has a note with m < 0, arm a per-track slot (patch RAM) to
   fire at micro-step 49 − a, where a = 1..48 is the advance.
4. **Fire.** Right before `seq_process_track(seq, t)` at that micro-step, borrow the
   stock slot: write the record of step k + 1 into `+0xc6`, set `+0xc0` and `+0xc1` to 1,
   `+0xc2` = 1 and `+0xc4` = 0. The stock code then fires it in that same call with all
   its normal note handling. If the stock slot is busy (a delayed note of step k is
   pending), fire it some other way or drop it (a design choice).
5. **Skip it at its own step.** At step k + 1's copy, empty the work record's note (the
   step's FX still run and act on the sounding voice) and clear type 2.

Hook points. ITCM code is copied from flash at boot, so these patches change the copy
source (`0x6018d6ec + address − 8`), something no patch here has done yet. ITCM can't
`bl` to patch code in flash (out of range), and all 192 ITCM long-call veneers
(`0x1f530`–`0x1fb28`) are in use, none duplicated. One way: point the literal of the
veneer at ITCM `0x1f818` (→ `seq_advance_step`, used only from `0x1e82` and `0x3742`)
at a flash shim that dispatches on LR. Return addresses `0x1e86`/`0x3746` go on to
`seq_advance_step`; the new callers go to patch code. Then rewrite two ITCM BLs to
`bl 0x1f818`: `0x2170` (step copy) and `0x372a` (`seq_process_track`, per track per
micro-step). `tools/patch_ptf.py` would need ITCM-aware BL and word patches.

Boundaries and limits:

- Pattern loop, last step: look ahead to the loop start in the same buffer. Fine.
- Song mode, queued pattern, Break FX: step 0 of the next pattern is in the preload
  buffer only once the main loop has finished the SD read, which starts at micro-step 1
  of the last step. A negative m on a pattern's first step needs the preload requested
  earlier (the last-step compares at ITCM `0x23b4` and `0x2578`); otherwise play such
  notes on time.
- An advance of a full step (a = 48) fires at micro-step 1 of step k, together with
  step k's own note; on one track only one of them can sound.
- Random and table play modes pick the next step at advance time: no look-ahead, ignore
  negative m. Backward: look at k − 1.
- Starting playback: a note before the first played step can't happen; play it on time.
- External clock: early notes are quantized like everything else (8 micro-steps per
  pulse).
- Stock firmware with such a project shows 101..199 and plays those notes late (up to
  the 96 micro-step cap). No crash expected.
- Patch RAM: about 16 × 8 bytes. colorsong already uses 12,208 of the 12,468 free OCRAM
  bytes, so a build with both needs a RAM plan.

#### FX interpolation between steps

The simplest hook touches no ITCM code: retarget the flash `bl` at `0x600aa7f8`
(`app_audio_block` → `seq_advance`) to a function that calls `seq_advance` and then runs
a ramp pass, once per block (about 345 times a second), in the same interrupt, after
all of the block's ticks. Per track and FX column:

- Start value: the current step's FX value, which the stock code has just sent or
  applied at micro-step 1. Target: the next step's value of the same FX (next step
  only, or the next step that has that FX, holding through empty steps: a design
  choice). Position: (micro − 1 + fraction of the tick accumulator) / 48.
- **MIDI CC (a–f on MIDI instruments).** Send through `midi_send_cc` (ITCM `0xacc`, a
  long call) so the MIDI-out setting and rendering are respected. Only when the 7-bit
  value changes, and rate-limited for the jack: for example at most one ramp CC per
  track every 4 micro-steps, a global cap per block, and none while the jack ring is
  more than half full. Stock already sends the notes, plus 6 clock bytes per step when
  clock out is on. USB has no such limit (its buffering is not mapped).
- **Pitch bend** (M on MIDI instruments) could ramp the same way, in 14 bits
  (`0x600abad0`).
- **Sample voices (tracks 0–7).** Call the stock setters with interpolated values:
  `voice_apply_fx`, or for filters write the float at `player+0x30` and call
  `voice_update_cutoff_q`, which gives finer steps than the 0..100 FX value. Filter
  ramps per block are probably smooth enough; volume may zipper. Cost per call not
  measured.
- **Synth instruments (64–66).** Macro FX go to the synth engine (`synth_apply_fx_q`);
  same idea, not mapped further.
- At a pattern boundary the next pattern's values exist only after the preload (above);
  hold the value through the last step if it isn't loaded.
- Opt-in: interpolating every FX would change how existing songs sound, so it needs a
  switch (a project setting, a step marker or a global option). Not decided.

#### Risks and unknowns

- Everything runs in the audio interrupt: no SD access, no allocation, bounded work
  per block. Stock costs about 3,000 instructions per micro-step; a ramp pass over 32
  columns is of the same order. Measure patched builds in the emulator first.
- An overfull jack ring corrupts MIDI output silently, so the CC limiter has to be
  conservative.
- `voice_apply_fx` and the setters are written for once-per-step calls; per-block calls
  are untested for cost and clicks.
- The stock pending-delay quirk (a pending delayed note drops the next step's note)
  interacts with negative Micro-move; the patch should keep or fix it on purpose.
- Nothing in this section has been checked on hardware. Checked in the emulator:
  Micro-move timing, CC send timing, preload request and buffer swap.

#### Implemented: `patch/interpolate` (MIDI CCs and synth Macros)

The plan above, for CC a-f on MIDI channel instruments and Macros on the synths, behind
two per-project Off/On items in a Project Settings folder, FX Interpolation (MIDI CC,
Synths). Details found on the
way:

- `app_audio_block` passes `seq` in r0 and the block length in seconds in s0 (hard-float
  ABI) to `seq_advance` through the veneer at `0x600aa858`, and ignores the result, so
  a C hook `void f(void *seq, float s)` can call it and then do its own work.
- The CC case (ITCM `0x26d8`) calls `midi_send_cc(cc, value, channel, 0)` at `0x3264`
  with channel 1..16 (instrument - 47). The CC number for letter L on channel c is the
  byte at `0x20204ab3 + 0x10 * (c - 1) + L`; 0x80-0x82 there mean program change,
  channel pressure and poly pressure.
- `seq_track_muted_q(seq, track)` (ITCM `0x3994`) ignores its first argument.
- The jack's send ring object is `*(0x2000c69c)` (`din_midi_send_cc` loads it from
  `0x2000c648 + 0x54`): `+0` write index, `+4` start of what hasn't gone out yet, `+8`
  the 512-byte buffer, `+0x3c` busy flag. `serial_tx_byte` (`0x600a08e4`) stores the
  byte and advances the write index without comparing it to the read side.
- In normal (not performance) mode the step advance uses `seq+0x36` (int16) as the last
  step when it is >= 0, otherwise track 0's length byte; a looping selection narrows it.
- In the audio block a track's `+0x88` is its current step and `+0x86` the next
  micro-step to process; the stock CC goes out while `+0x86` is still 1.
- The Pattern screen's Preview (`pat_preview_button_q` `0x600e2b18`) copies the
  selection into `seq[0..3]` and calls `seq_play_selection(seq, loop)` (`0x600ac3d0`,
  loop = 0 from Preview): it copies the selection to `+0x20`..`+0x23` (first step, first
  track, last step, last track), puts every track on the first step at micro-step 1,
  clears every track's enabled flag `+0x7d` and sets it again for the selected tracks,
  then sets `+0x1e` = 1 (selection playback), `+0x1f` = loop, song mode off and playing
  on. The ramp pass at first skipped selection playback; now it ramps there too, and
  the enabled flag keeps the unselected tracks quiet.
- Synths (instruments 64-66): at micro-step 1 the FX pass asks ITCM `0x3c30` (engine,
  type) whether the synth takes an FX type: Off (1), CC/Macro a-f (8-12, 37), delay
  send (30), panning (31) and reverb send (32). Those go to `synth_apply_fx_q(engine
  0x20017b08, type, value, column, synth 0..2)` (ITCM `0x5070`; column 0 = FX1, 1 =
  FX2), with or without a note. It records the column's type, looks the FX type up in a
  std::map at `0x20017ad8` (FX type -> parameter), scales the value from
  `fx_min`..`fx_max` to the parameter's float range (table `0x6003aa8c`), and stores it
  as a pending parameter with a dirty bit (`engine + 0x692 + synth`), inside an IRQ-off
  section. Off (1) resets the column's parameter (`0x4ec8`). Synth note-on and note-off
  go out through `0x600abb44` / `0x600abbe8`.
- The render loop (`0x600bc220`, tail-called from the render state machine at
  `0x600bc38e`) runs `seq_advance` (its own veneer at `0x600bc2d8`, called at
  `0x600bc27a`) 69 times per call, interleaved with the engine's rendering and file
  writes; MIDI is muted then (`seq_is_rendering`).

Cost: a look-up through the whole pattern for all 16 tracks at once (they change step
together) took 555,000 instructions in one block; moving ramps along incrementally and
doing at most 2 full look-ups per block brought the worst block to 23,670. Not checked
on hardware yet.

## 13. Pattern screen: bottom bar, selection, undo (`patch/humanize`)

The Pattern screen object is `patview` = `0x20021770`. Its editor state is `ed` =
`patview + 0x1000` = `0x20022770`.

### Bottom bar

- Eight buttons sit under the screen. Their label controls are int8 control indices at
  `ed+0x292`..`+0x299`. The bar has 3 pages (`ed+0x4e2` = current, `ed+0x4e1` = count).
  More (button 7, `pat_more_button` `0x600e2a44`) moves to the next page when no other
  button is held and calls `pat_show_page` (`0x600e29ec`), which binds the page's
  buttons and draws its labels.
- Bindings live in a button manager at `*(0x200209e0)`: entry count at `+5`, 20-byte
  entries at `*(+0x1c)` (`+0` kind, `+4` handler, `+0x10` flag).
  `btn_bind(manager, index, flag, handler)` (`0x600cf6ec`) sets kind 1 and the
  handler, `0x600cf74c` sets kind 3, and `btn_clear(manager, first, last)`
  (`0x600cf53c`) zeroes entries. The stock handlers ignore their arguments.

| Page | Bind / labels | Buttons |
|---|---|---|
| 1 | `0x600e2858` / `0x600e52ac` | Pattern, Length, Step jump, Fill (`0x600e22b0`), Preview (`0x600e2b18`), Undo, Redo, More. Buttons 0–2, 5 and 6 are bound as kind 3 |
| 2 | `0x600e2900` / `0x600e559c` | Render Selection `0x600e379c`, Invert `0x600e0e3c`, Duplicate `0x600df9f4`, Expand `0x600df9a8`, Shrink `0x600df960`, Copy `0x600e11a4`, Paste `0x600e12a0`, More |
| 3 | `0x600e29a4` / `0x600e584c` | Pattern Name `0x600dfe64`, Track Name `0x600dfdd0`, buttons 2–6 unused, More |

- Each label gets `disp_ctrl_set_text` (top line), `disp_ctrl_set_ptr38` (second
  line), `disp_ctrl_set_colors` (a color set at `+0x24`..`+0x64` of `*(display+8)`)
  and `disp_ctrl_set_data` (`patview+0x12ac`, `+0x12cc` or `+0x12ec`), then all 8 get
  style, value and refresh. Page 3 sets colors and data only for buttons 0, 1 and 7;
  the unused buttons keep page 2's.
- **Record mode** is `ed+0x254`. The editing buttons only work in it: page 2 binds
  its buttons only when it is set, and its labels use color set `+0x44` (active) or
  `+0x54` (greyed out); page 1 does the same for Fill, Undo and Redo (`+0x24`/`+0x34`,
  `+0x44`/`+0x54`). The record button (`pat_record_button`, `0x600e2c28`, acts when
  r0 = 1) toggles it, and `pat_edit_mode_changed` (`0x600e2a94`, also called from the
  other record-flag writer at `0x600c83fe`) rebuilds the rows and redraws the bar
  through `pat_show_page`, unless an overlay is open (`ed+0x398`, `*(0x20020880+0x75)`).

### Selection and editing

- Cursor: track `ed+0x1e4`, step `ed+0x1e6`. Selection: mode `ed+0x1f4` (2 = a
  selection is active), corners `ed+0x1f7`/`+0x1f8` and `ed+0x1fa`/`+0x1fc` (track,
  step), whole tracks `ed+0x1fe`.
- `ed_selection_to_seq()` (`0x600e0b58`) writes the rectangle into the first 4 bytes of
  the sequencer object (`0x20017274`): first step, first track, last step, last track,
  sorted by `seq_set_selection` (`0x600aeee4`). Without a selection it is the cursor
  cell. Whole tracks run from step 0 to the last-step byte (`data+0x1c`, the pattern
  length − 1).
- `ed_selected_column_q()` (`0x600def3c`) picks the part of each step an edit applies
  to (for Invert: 0 the whole step, 2/3 note and instrument, 4 FX1, 5 FX2).
- Invert (`0x600e0e3c`) shows the sequence the editing buttons follow:
  `undo_push(fm)` (`0x600c3d90`), `ed_selection_to_seq()`, the edit
  (`seq_invert_selection(seq, column)`, `0x600ad918`), `patview_fill_rows_q(patview)`,
  `fm_mark_pattern_changed(fm)` (`0x600bccd8`). `fm` is the file manager `0x2001e488`.
- Undo: `undo_push` copies the whole current pattern (0x3030 bytes) into a 20-slot ring
  in SDRAM at `0x83f811cc`, its pattern number to `0x83f62fcc + slot`, and the cursor
  and selection (`undo_save_cursor`, `0x600c3cf0`) to 14-byte records at `0x2001f618`.
  Ring state at `0x2001f610`: next slot, count, redo count.
- `fm_mark_pattern_changed(fm)` sets `fm + 0x4ca + current pattern` to 1, so the pattern
  is saved with the project.
- Random numbers: `rand_range_q(lo, hi)` (ITCM `0x3af4`, veneer `0x600af0e8`) returns
  lo..hi − 1 from a Park–Miller state. The random fill and the random FX use it.

`patch/humanize` binds page 3's button 2 in record mode and labels it "Humanize"
(greyed out outside record mode; its README lists the hooks). Its handler follows
the Invert sequence above, so stock Undo reverts it, including large edits. Tested
on hardware. Its settings use the Config menu (section 14).

## 14. Config menu (`patch/humanize` settings)

The Config screen (vtable `0x6003b5f0`, update method `0x600c6474` in slot
`0x6003b5f8`) shows a tree of statically allocated nodes that `cfg_menu_build`
(`0x60118acc`, a static constructor) builds at startup. Run in the emulator, it gives
root → Project Settings, General, MIDI, USB, Metronome, Firmware, Info, each with its
items (`tools/emu/test_humanize.py` walks it).

- **Folder** (kind 1), `cfg_menu_folder(node, parent, label, capacity)`
  (`0x60118394`): `+0` kind, `+4` parent, `+8` capacity, `+0xc` child array, `+0x10`
  child labels, `+0x14` value texts, `+0x18` label, `+0x1c` selected child, `+0x1d`
  child count, `+0x1e` index in the parent. The arrays are allocated with the
  capacity; a child past it is counted but not stored. Root holds 7 and Project
  Settings 10, both full.
- **Item** (kind 2), `cfg_menu_item(node, parent, label, help, type, descriptor)`
  (`0x601183e8`): `+4` label, `+8` help text (shown by the editor), `+0xc` type,
  `+0x10` descriptor, `+0x14` value text buffer. Types: 1/2 number, descriptor
  {value pointer, min, max, step, display offset}; 3 options, descriptor {value
  pointer, count, names}; 4 options with an apply callback, descriptor {value
  pointer, count, names, callback}; 5/6 text; 7 action (function pointer). The constructor
  registers every node with `atexit` (`0x600a3594`).
- The number editor (`0x600cada0`, opened by `cfg_menu_open_editor` `0x601185d0`)
  lists min..max and stores the chosen **index** in the value byte, while the list
  shows value + display offset. Stock number items all use min 0: a 1..100 setting is
  stored as 0..99 with offset 1.
- Apply: `cfg_menu_apply(screen, value)` (`0x60118658`, from the editor) stores the
  choice in the selected item's value byte (numbers: value + min; options: if below
  the count), calls a type 4 item's callback **without checking it**, and closes the
  editor (`0x600cab60`). The stock callbacks apply the setting (Anti-aliasing
  `0x600c6054`); Metronome State's (`0x600c6128`) is empty. A type 4 descriptor
  without the callback word makes Apply jump into whatever follows it.
- Navigation: the current folder is `*(0x20028180)`. `cfg_menu_select`
  (`0x60118928`) opens a child folder (any depth) or an item's editor (types 1..4) or
  runs an action (7); `cfg_menu_back` (`0x60118904`) goes to the parent, or returns
  0 at a root category; `cfg_menu_redraw` (`0x60118828`) draws the root list and the
  current folder, whose header reads "Back" when its parent isn't the root.
  `cfg_menu_format_values` (`0x6011870c`) fills a folder's value texts ("%d" of
  value + offset, or the option name).
- Project Settings values are bytes in the project struct (`0x202046c9`,
  `0x20204aac`, `0x20204bb4`, …), saved in `project.mt`. Ghidra's references can't
  show which struct bytes are free: arrays are only referenced at their first byte,
  and one "unreferenced" range (`0x20204bbf`–`0x20204bf7`) holds data in real
  project files. `ghidra/scripts/RefsInRange.java` lists the references to a range.

`patch/humanize` creates Project Settings with room for 11 items, appends a
"Humanize" folder with four number items and an Off/On item after the constructor's
last item, in nodes from the firmware's heap, and keeps the values in its own RAM
(32 bytes, placed by the patcher, section 15) and in `<project folder>/humanize.bin`, read and written from the
Pattern and Config screens' update methods. Tested on hardware.

## 15. RAM map and RAM for patches

A survey of all RAM, to find room for patch state beyond the OCRAM tail. Sources: the
reset handler's FlexRAM setup and copy tables (section 2), the allocator, Ghidra's
references (`ghidra/scripts/RefsInRange.java` for small ranges,
`RefsSummary.java` for large ones), and the functions behind each candidate. None of
the candidates below has been tested on hardware yet.

### Layout

| Range | Size | What |
|---|---|---|
| ITCM `0x00000008`–`0x0001fb37` | 127 KB | Fast RAM code, copied from flash at reset |
| ITCM `0x0001fb38`–`0x0001ffff` | 1,224 B | **Candidate.** After the copied code; no references (the one Ghidra lists, `0x1ffff` in `0x600acac8`, is an unrelated constant). Above 64 KB the MPU maps ITCM as Device memory (region 4): aligned accesses only |
| DTCM `0x20000000`–`0x20004377` | 17 KB | `.data` |
| DTCM `0x20004378`–`0x200094b7` | 20 KB | Audio block pool: `audio_memory_init(0x20004378, 80)` (`0x600ff704`, from the audio setup `0x600b421c`) makes 80 blocks of 0x104 bytes, the same design as the Teensy Audio Library's `AudioMemory()` |
| DTCM `0x200094b8`–`0x200097ff` | 840 B | **Candidate.** Between the pool and `.bss`; no references |
| DTCM `0x20009800`–`0x2002fbe3` | 153 KB | `.bss` |
| DTCM `0x2002fbe4`–`0x2002fc67` | 132 B | Variables outside the `.bss` table (`0x2002fb9c` region holds the heap pointer); not free |
| DTCM `0x2002fc68`–`0x20051c67` | 136 KB | **Heap** (newlib nano). `_sbrk` (`0x60188780`) starts at `0x2002fc68` and refuses to pass `0x20051c68` (`heap_limit_check`, `0x60188840`). `malloc_or_halt` (`0x600a1ec0`, behind `new`) **loops forever** when an allocation fails (if a flag is set), so running out of heap freezes the unit |
| DTCM `0x20051c68`–`0x20052fff` | 5,016 B | **Candidate.** Between the heap limit and the stack; only the limit constant refers to it. A stack overflow past 20 KB would reach it first |
| DTCM `0x20053000`–`0x20057fff` | 20 KB | Stack (filled with `0xAA` at reset for the high-water mark) |
| OCRAM `0x20200000`–`0x20204f4b` | 20 KB | `.bss` |
| OCRAM `0x20205000`–`0x20207fff` | 12 KB | **Used by patches**: the patcher's first region (`ocram_tail`); colorsong and humanize together use 12,240 B of it |
| SDRAM `0x80000000`–`0x83ff9bef` | 64 MB | Sample memory and fixed buffers. The top holds a stack of buffers: pattern copies, undo ring (`0x83f811cc`), audio and display buffers, ending with two 8 KB buffers at `0x83ff5bf0` (`0x601225a4`) |
| SDRAM `0x83ff9bf0`–`0x83ffffff` | 25 KB | **Candidate.** After the last buffer; no references. Cached (MPU region 8, Normal write-back). Whether it is ready before `main` (patch constructors run earlier) is unchecked |

### Measuring use on the device

With an (empty) file named `.develop` in the root of the SD card, the firmware sets a
developer flag at boot (`0x600aa62e`: `/.develop` exists → `*(0x2001fa58+0xb99)`), and
the Config screen shows, once a second (`cfg_debug_info`, `0x600cb548`):

- CPU load: min, max, average and last, in %;
- **free heap**: newlib `mallinfo().fordblks` (`heap_free_q`, `0x6010c254`), the free
  bytes inside the part of the heap claimed so far. It leaves out the room the heap
  hasn't grown into, so the real headroom is this plus (`0x20051c68` − the current
  break, `*(0x2002fb9c)`);
- **max stack**: the deepest stack use since boot (`stack_max_used_q`, `0x6010c268`),
  out of 20,480 bytes.

Measured on stock 1.2.0 (no patches), one session:

| After | free heap | max stack | CPU max |
|---|---|---|---|
| Power-on | 69,848 B | 7,057 B | 42 % |
| Playing a project, loading samples (large loops), recording and loading a 12 s sample | 68,092 B | 7,129 B | 47 % |
| A pattern with Synth 1–3 on every step, two FX sending random values to their macros | 68,092 B | 7,129 B | 60 % |
| Loading and playing several projects, then back | 67,816 B | 8,033 B | 61 % |

So about 12.4 KB of the stack was never used, which keeps a stack overflow well away
from the DTCM gap above the heap limit, and at least ~67.8 KB of the heap stayed free
(the real headroom is that plus whatever the heap hasn't grown into). Heap use hardly
changes after power-on, so most allocations happen at startup. Not yet measured:
rendering and export, USB storage, the sample editor, performance mode, the games.

### Options for patch RAM

1. **Fixed regions, assigned by the patcher.** A patch states how much RAM it needs;
   the patcher hands out space from the regions above, in `include` order, and moves
   the patch's RAM addresses the same way it moves its code (a second relocation
   table: link with the RAM origin shifted, and compare). Patch code would declare its
   state as ordinary variables in a no-init RAM section instead of fixed addresses.
   Usable pools, best first: the OCRAM tail (12 KB, proven), the DTCM gap above the
   heap (4.9 KB, fast), the SDRAM top (25 KB, large but cached and slower), the ITCM
   tail (1.2 KB, aligned access only), the DTCM gap after the audio pool (840 B).
2. **Heap at startup.** A patch allocates its state from the firmware's heap in a
   startup hook (as humanize does for its menu entries) and keeps one pointer in a
   fixed slot. No free region needed, but it reduces the heap headroom, and a failed
   allocation later in the stock code freezes the unit. Needs the headroom measured
   first.

`patch/ramdiag` checks the candidates on hardware: once the Pattern screen runs, it
fills each with an address pattern, then reports how much of it is still untouched,
and the heap's high-water mark (the newlib break, which the debug screen leaves out),
in Config and in `ramdiag.txt`.

Result of one session on stock 1.2.0 + ramdiag (5.6 minutes: playing patterns with
MIDI notes, loading and copying samples, sample editing including a timestretch,
recording from the radio, rendering 128 steps of Synth 1–3 modulating on every step
and loading the result, entering and leaving USB storage mode):

| Region | Untouched |
|---|---|
| DTCM above the heap limit | 5,016 of 5,016 B |
| SDRAM top | 25,616 of 25,616 B |
| ITCM tail | 1,224 of 1,224 B |
| DTCM after the audio pool | 840 of 840 B |
| Heap | break `0x20051c5c`: grew to 12 bytes below its limit; 42,468 B free inside |

So all four regions (32,696 bytes) look free, next to the OCRAM tail. The heap does
not: rendering drives it to its limit (free heap fell from about 68 KB to about 42 KB
and stayed there), so it has no room to grow, and a failed allocation freezes the
unit. Patch RAM should come from the fixed regions, not the heap; even small startup
allocations (humanize's menu entries, a few hundred bytes) take from a thin margin.
Max stack was 8,033 B again; CPU peaked at 88 % while rendering. Not covered yet:
performance mode and the games. The games run on a built-in NES emulator; if their
buffers (and any fast-RAM code) are used only while a game runs, disabling the games
could free more. Unchecked.

**Implemented: option 1.** `tools/patch_ptf.py` assigns patch RAM from these regions
(`RAM_REGIONS`: `ocram_tail`, `dtcm_above_heap` from `0x20051c70`, `sdram_top`,
`dtcm_after_audio` from `0x200094c0`, and `itcm_tail` from `0x0001fb40` only when a spec
names it; starts rounded up to 16 bytes). Patches declare their state in a `.mt_ram`
(NOLOAD) section; the build links a third time with the RAM origin moved 64 KB, and
`tools/mkrel.py` lists the RAM-address words next to the code ones. A spec whose
state is used before `main` sets `"ram_startup": true` and never gets SDRAM (whether
SDRAM is ready that early is unchecked). The patcher writes the result to
`<output>.map.json`, which the emulator tests read. In the emulator, colorsong's table
works from `sdram_top` and humanize's settings from `dtcm_above_heap` too; on hardware
only the OCRAM tail is in use so far.

## 16. The games (NES emulator), and what disabling them would free

The Tracker+ plays NES games (`.nes` files from the card's `Games` folder) with a
built-in port of **Nofrendo**, an open-source NES emulator (its messages: "`DiskDude!`
found in ROM header", "Failed to create NES instance.", "setting up mapper %d", and
~30 mapper names such as "Tengen RAMBO-1"). No games are built into the firmware: the
`NES\x1a` bytes in the image are the header check's constant. Found with a whole-program
reference graph (`ghidra/scripts/ExportRefGraph.java`, 9,206 functions) and the code
around each use. Static analysis only; nothing here is checked on hardware.

### Code

| What | Where | Size |
|---|---|---|
| Emulator (Nofrendo: ROM loader, mappers, PPU/APU glue, video setup) | `0x6008105c`–`0x6008e977` | ~54 KB |
| — of which the 6502 CPU core: one function with a 256-entry opcode table (`0x60083c34`, Ghidra stops at its table) | `0x60083c34`–`0x60089513` | ~22 KB |
| Emulator strings (loader, video, mapper names) | `0x60061d7c`–`0x60062f00` | ~4.5 KB |
| Games screen (vtable `0x6003e1f0`), game start (`game_start_q`, `0x600d0aa0`), file helpers (`0x600c5a84`–`0x600c5e84`), frame copy (`0x60109918`) | scattered | a few KB |

The double-precision math helpers right after the emulator (`0x6008e978` on) are used
everywhere and stay. None of the emulator runs from ITCM (fast RAM): disabling the
games frees no fast-RAM code space.

Ways in: everything goes through `game_start_q` (`0x600d0aa0`), the only caller of the
NES setup (`0x60082c94` → `nes_load_q` `0x60082ec8`); it is called from the Games screen
and from `0x600c8940` / `0x600d0c64`. Two hooks reach the emulator outside a game: the
audio DMA callback calls `nes_audio_q` (`0x60082b28`) for game sound, and a static
constructor (`0x60082954`) initialises emulator globals at startup.

### RAM

| What | Where | Size | Used when |
|---|---|---|---|
| Game screen buffer, 256 × 240 at one byte per pixel | SDRAM `0x83fd99e0`–`0x83fe89df` | 61,440 B | Only by the game start (`0x6010985c`) and the frame copy (`0x60109918`) |
| ROM buffer, NES machine | heap (`malloc`) | depends on the game | Only while a game runs |
| Emulator globals | DTCM `.bss`, ~36 fields between `0x2002a529` and `0x2002c88e` | small (under 1 KB) | Startup constructor and games |

### What disabling the games would free

- **SDRAM: the 60 KB game screen buffer.** By far the most useful part: as large as all
  other confirmed free regions together. Needs the same hardware check as section 15
  (fill it, use the device, count) with the games disabled.
- **Flash: ~60 KB** (emulator code, strings, game screens). Flash isn't short (patches
  are appended after the image), but a patcher that knows about free flash ranges could
  put patch code there instead, keeping the image the same size, in case the
  bootloader has a size limit (unknown, section 8).
- **Heap safety:** a game allocates its ROM and machine from the heap, which stock
  firmware already drives to its limit (section 15). Without games that can't happen.
- **Not freed:** fast RAM (none of the emulator is in ITCM), meaningful DTCM (the
  globals are small and scattered), CPU time at idle (the audio hook only checks
  whether a game runs).

### How to disable them

Make `game_start_q` (`0x600d0aa0`) return without starting anything, the single path
to the NES setup, and optionally hide the Games entries. The audio hook and the
startup constructor can stay: without a game they do nothing. Not built yet.
