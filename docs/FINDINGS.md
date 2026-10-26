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

**The flash is 16 MB** (read on hardware with a read-only probe build): the FlexSPI
configuration block at the start of flash (tag `FCFB` at `0x60000000`, `sflashA1Size`
at `+0x50`) and the FlexSPI controller (`FLSHA1CR0`, `0x402a8060`, in KB) both say
16,384 KB, as the MPU's 16 MB flash region does. So an image may reach `0x61000000`:
about 15.8 MB from `0x60030000`, against 1.6 MB for stock. Whether the bootloader writes
an image that large is a separate question, tested with padded builds that check their
own contents.

**An image up to 8 MB of flash is written whole (hardware).** Padded builds whose images
end at `0x601fe3b4`, `0x603fe3b4` and `0x607fe3b4` (2, 4 and 8 MB of flash; `.ptf` files
of 5.1, 10.7 and 22 MB) each installed through Config → Firmware, booted, and read back
their whole padding (269, 2,317 and 6,413 KB of pseudo-random words) without a wrong
word. The 8 MB one took a few minutes to install. Larger images (up to the chip's 16 MB)
are untested.

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
  100 − s alternating per step. The Swing FX (`0x2f10`) only sets swing on (`+0x60`)
  and the amount (`+0x64`); `seq_advance_step` toggles the pair (`+0x61`) when track
  16 ends a step. Nothing resets them when playback starts (`seq_play_from` leaves
  them), so a take starts with the swing that played last, and a Swing FX on a muted
  track is skipped (emulator). `patch/swing` sets them at every take and pattern start.
  `seq_tick` reaches `seq_advance_step` through a veneer in ITCM (`0x1f818`) that jumps
  through a word at ITCM `0x1f81c`, copied at boot from flash `0x601acf00` (the ITCM
  image is copied from `0x6018d6e4` + offset), so a patch can hook it by replacing that
  flash word. `seq_step_ms` (ITCM `0x1014`) = 125 ms at 120 BPM. Tempo
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
- The plans in this section were made from the emulator (Micro-move timing, CC send
  timing, preload request and buffer swap). FX interpolation has since been built and
  tested on hardware (below); negative Micro-move has not.

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
doing at most 2 full look-ups per block brought the worst block to 23,670. Tested on
hardware in both bundle orders (MIDI CC and synth Macro ramps, Preview, rendering).

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
works from `sdram_top` and humanize's settings from `dtcm_above_heap` too. On hardware
three regions are now in use and tested: the OCRAM tail (colorsong and humanize in
`patch/all`), `dtcm_above_heap` (interpolate's 1,504 bytes in `patch/all`, used from
the audio interrupt) and `sdram_top` (colorsong's 12,208-byte table when the bundle is
listed the other way round, so interpolate and humanize take the OCRAM tail).

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

## 17. Synth engines: how models are built, and adding or swapping one

Static analysis plus the slot-record check below; no engine has been run in the
emulator (the synth host is created at runtime, after the static constructors).
Section 21 now runs the synth system in the emulator, with the voice interface, the
parameters and the per-voice costs.

### Models and the registry

Tracker+ has 3 synth slots (instruments 64-66, S1-S3); each slot runs one of 5 models
(manual: ACD, FAT, VAP, WTFM, PERC) and loads a patch. Each model is a C++ class.
`0x60140150` registers them at startup into a `std::unordered_map<unsigned,
shared_ptr<descriptor>>` (insert `0x6012fd78`), keyed by model id:

| Id | Model | Descriptor built by |
|---|---|---|
| 0 | "Midi out" | `0x60144b30` |
| 1 | ACD | `0x60147d80` |
| 2 | FAT | `0x6015c810` |
| 3 | (not registered) | |
| 4 | VAP | `0x6014dbf8` |
| 5 | WTFM | `0x60161c38` |
| 6 | PERC | `0x60155f48` |

A descriptor is 0x38 bytes: the name (`std::string`) and two `std::function`s. For ACD
the first holds the plain function `0x601482e0`, a very large function that builds the
model's parameter descriptors (names such as "Saw Mix", value formatting through the
fmt library, "{:.0f} st"); the second is a lambda (`0x60145e80`). The registry lives in
a 0xb0-byte synth manager (`0x60126a28`, three hash maps) created by the audio setup
`0x600b2118`, which also creates the synth host and stores it at app object
`0x200170e0` + 8 + `0xac`.

Notes reach the host through `seq_note_on_out_q` / `seq_note_off_out_q`
(`0x600abb44` / `0x600abbe8`): with the synth flag set they call the host's virtual
method `+0x20` (note on) with (note, velocity, slot). Macros and the other synth FX go
through `synth_apply_fx_q` (section 12).

### The slot record, and what stock firmware does with an unknown model

The project holds one 0x40-byte record per slot at project `+0xd00`, `+0xd40`, `+0xd80`
(`0x20204d00`...): the patch name (0x20 bytes at `+0x10`), the six Macro states
(`+0x38`) and floats and indexes for the slot settings. `0x600b175c` copies a slot's
live state into it. On project load `0x600b1858` (called from `0x600bce14`) checks each
record: word `+8` below 5 (very likely the model: 5 models), word `+4` below 9 (very
likely polyphony), gain at most 12 dB, panning and the two sends in range, Macro bytes
not negative. A record that fails is replaced with the slot's defaults
(`0x600b175c`). So a project saved with a sixth model, opened in stock 1.2.0, gets that
slot reset to defaults: no crash, but the slot's settings are lost.

### Adding an engine

- **Registering it:** hook `0x60140150` (chained) and insert one more descriptor (id 3
  or 7) with the patch's own factory. Or, to swap, give an existing id a descriptor
  whose factory builds the new engine instead.
- **The interface:** the new engine has to be a C++ object the host can drive: the
  host's virtual calls (note on/off at `+0x20`, CC, pitch bend, the per-block render,
  voice allocation, patch load/save) and the parameter descriptors the editor pages
  read. None of that is mapped yet; it is the bulk of the work. The firmware was
  built with GCC 10 and libstdc++ (`std::string`, `std::function`, `shared_ptr`,
  `unordered_map`), whose layouts a patch would have to match.
- **Flash:** not the limit. The MPU maps 16 MB of flash (section 8); the image is
  1.6 MB.
- **RAM:** the model factories allocate with `malloc_or_halt` from the heap, which
  reaches its limit in heavy use (section 15). A new engine should keep its voice
  state in a patch RAM region (`sdram_top`, 25 KB) or be no larger than the engine it
  replaces, and its tables in flash.
- **CPU:** the Config debug readout showed 53 % average and 62 % peak while playing a
  pattern (section 15 has the method). An engine that replaces a stock one gives back
  that model's share; one that adds a model adds to the total.
- **Testing:** the emulator doesn't create the synth host, so engine work would need a
  bigger harness or hardware tests from the start.

### Swapping instead of adding

A per-project setting (like FX Interpolation) that makes one stock model, or one slot,
use the new engine keeps the project file a normal 1.2.0 project: the slot record
still names a stock model, so stock firmware loads that stock model with its saved
patch (it sounds different, but nothing is reset). The new engine's own patch (for an
FM engine, a voice from a `.syx` bank) goes in a file in the project folder. The model
list on the instrument page needs no new entry. The price: while swapped, that stock
model isn't available in the project.

### An FM engine (Dexed)

Dexed's FM core comes from Google's music-synthesizer-for-android ("msfa",
Apache-2.0); Dexed itself is GPL-3.0. Ports of the msfa core run on 600 MHz Cortex-M7
boards (Teensy 4.x). For this repository (MIT, patches applied to Polyend's
proprietary firmware) the Apache-2.0 core is the usable one; GPL code can't be
combined with the stock firmware and distributed.

## 18. Custom step FX

### How FX ids flow

- Storage: one byte of type and one of value per FX column, in the pattern (section
  6). Types 1-42 are used (section 12).
- The FX list: the 43-byte table at `0x60039ca4` maps list positions to types; names
  are 0x24-byte entries at `0x6003be34`, by list position. 17 code sites use the list
  table (literals at `0x600af0dc` ... `0x600e42bc`) and 3 the names (one is the data
  pointer at `0x6003bd88`). Several loop to 0x2b (43).
- Range and text: `fx_max` / `fx_min` (ITCM `0x3824` / `0x38ac`) and
  `fx_value_text` (`0x600afb48`) switch on the type with a bounds check.
- Playback: the FX pass in `seq_process_track` (ITCM) switches on types 1..37, other
  types take the default path (section 12).

### Stock firmware with a type it doesn't know (emulator, stock 1.2.0)

Types 43, 60, 128 and 255, in FX1 and FX2, with and without a note, on a MIDI track, a
synth slot and a sample track, through the real audio block for about 6 steps:

| Where | Result |
|---|---|
| MIDI track | Ignored; the other FX on the step still sent |
| Synth slot | Passed to `synth_apply_fx_q`, which ignores types it doesn't handle |
| Sample track (no note) | Passed to `voice_apply_fx`, which falls through its bounds-checked switch |
| `fx_max` / `fx_min` | 100 / 0 (the defaults) |
| `fx_value_text` | The plain number ("  0") |
| Pattern grid (`patview_fill_rows_q`) | The type isn't in the list table, so the cell shows `----`, like an empty one |

So a pattern with a new FX type plays on stock firmware as if that FX weren't there,
and nothing crashed. The pattern is saved as raw bytes, so the type survives a save in
stock firmware. Not checked: what the stock FX picker does when the cursor is on such a
cell.

### Ways to add one

- **New type ids (43 and up):** safe on stock (above). The patch would extend the list
  and name tables (copies in patch flash, the 20 references repointed, the 43-entry
  loop bounds raised), give the new types a range (`fx_max` / `fx_min` are in ITCM, so
  either an ITCM-aware patcher or the defaults 0..100), and implement what they do.
- **What they can do from a per-block hook** (as `patch/interpolate` does): anything
  that reads the step's FX and acts on MIDI, synth Macros or sample-voice setters
  between steps, for example LFOs or slides on a CC.
- **What needs the sequencer itself:** FX that change when or whether a note plays
  (ratchets, strums, conditional triggers) have to hook `seq_process_track` in ITCM,
  as planned for negative Micro-move (section 12): not built yet.

## 19. Master reverb and delay: the audio graph, and swapping an effect

### The audio graph

The audio engine is built like the Teensy Audio Library: audio objects with a virtual
`update()`, linked in a list from `0x20026e50`, exchanging blocks of 128 16-bit
samples (a block is 4 + 256 bytes). Once per audio block the engine's render (ITCM
`0x3bac`, called from `app_audio_block`) stores the output buffers and runs
`0x6003061c`, which calls `update()` on every active object in the list (`+4` active,
`+0x10` next). The block helpers next to it: allocate `0x60030478`, release
`0x60030524`, transmit `0x60030574`, receive read-only `0x600305bc`, receive
writable `0x600305d4`. There are 182 objects, all built by the static constructors
(the emulator has the whole graph); most are members of the engine object
`0x20017b08`. The build has no RTTI, so the classes were told apart by what their
`update()` does.

### The two send effects

| | Delay | Reverb |
|---|---|---|
| Object | `0x20018fc0` (engine + `0x14b8`) | `0x20019014` (engine + `0x150c`) |
| Vtable / `update()` | `0x60042fec` / `0x60032404` | `0x60042f94` / `0x60031e5c` |
| DSP | `0x600322b0` (integer) | `0x600ff800` (float) |
| Inputs / outputs | 1 (the send) / 2 (stereo return) | 2 / 2, processed in place |
| Algorithm | three ring buffers of 2^18 16-bit samples (about 5.9 s), feedback (`+0x2c`), ping-pong (`+0x2e`), a one-pole filter (`+0x30`..`+0x38`) | a plate-style network: fixed taps in a 16K-sample 16-bit ring buffer, some taps modulated by an LFO, damping filters |
| Memory | the three buffers (about 1.5 MB of SDRAM), allocated at runtime | buffers in SDRAM at `0x83fc258c` and `0x80262000` |
| Bypass | `+0x2d` | `+0x80` |

The reverb's float settings sit at object `+0x50`..`+0x80` (defaults 0.5, 0.8, 0.1,
0.7, 0.625, 0.2, 5.0).

**The stock reverb is (derived from) Mutable Instruments' reverb.** Its taps in the
16K-sample ring buffer sit at `0x95`, `0x97`, `0x16c`, `0x16e`, `0x2ac`, `0x2ae`, `0x4bc`,
`0x4be`, `0xd43`, `0xd45`, `0x17c6`: exactly the delay-line boundaries of the reverb in
Mutable Instruments' Rings, Elements and Beads (`dsp/fx/reverb.h`, MIT): input allpasses
of 150, 214, 319 and 527 samples, then loop allpasses of 2182 and 2690, laid out one after
another by MI's `FxEngine`. Its default diffusion 0.625 and damping 0.7 are that
reverb's `Init()` values, and like it, it stores 16-bit samples, processes in float and
modulates taps with an LFO. So swapping in one of MI's reverbs would change little; a
swap that sounds different needs a different algorithm. The Master page's labels are
Size, Damping, Predelay and Diffusion for the reverb and PingPong, Sync, Rate, Time,
Feedback and Filter for the delay (manual 10.3, 10.4); the fields they write are below.

### Swapping one

Each effect is a single object with its own vtable in flash, so a patch can replace
its `update()` with a word hook on the vtable slot (`0x60042f94` or `0x60042fec`,
chained), no ITCM patching needed. The replacement does what a Teensy-style object
does (receive the input blocks, process 128 samples, transmit, release) with its own
DSP, or calls the stock `update()` when the swap is off (for example a per-project
setting, as for FX Interpolation). This keeps everything else as it is:

- **Project files:** nothing changes in them; stock firmware opens the project with
  the stock effect.
- **Controls:** the Master page still writes the stock object's settings. The new
  effect can read them and give them its own meaning (Size, Damping, Predelay,
  Diffusion for a reverb; Time, Feedback, Filter for a delay) once the fields are
  mapped.
- **Memory:** while swapped the stock DSP doesn't run, so its buffers (1.5 MB of SDRAM
  for the delay, the reverb's ring buffers) can hold the new effect's state; no heap
  needed.
- **CPU:** the new effect replaces the stock one's cost.
- **Testing:** the emulator has both objects, so a replacement can be fed test blocks
  through the real block helpers and its output written to a WAV file for listening,
  before it goes near hardware.

**Boot hang with RAM in `sdram_top`.** The first Freeverb build kept 10 KB of
state (mostly the predelay line) in `sdram_top`, written from the audio interrupt, and
the unit stopped at the boot logo, before the project's progress bar, every time (with
all reverb and delay sends at 0 as well); the pinhole recovery brought it back. The
emulator tests (which start from the static constructors, not a real boot) passed. The
same region holds colorsong's table in the reversed bundle without trouble, but colorsong
only touches it once the project is loaded; ramdiag only checked it after the Pattern
screen ran. A second build with the state on-chip (288 bytes in the OCRAM tail) and the predelay
line in the stock reverb's own buffers hung the same way, while a build whose hook only
calls the stock `update()` booted and played its reverb. So the hang comes from running
Freeverb (its one-time 64 KB clear of the buffers, or its DSP) while the unit boots, not
from the hook or `sdram_top`. Two diagnostics settled which part: the stock reverb plus
the one-time 64 KB clear during boot booted fine; Freeverb's DSP running from boot with
its output muted (no clear) hung (the screen faded to white after the update reached
100 %, and the unit needed the pinhole to power on again). So running the heavier DSP in
the audio interrupt while the unit boots breaks the boot; the emulator's stress runs
(random settings including garbage, missing blocks, moved buffers, restarts) found no
stray memory writes or runaway loops, which points at timing (about 76,000 instructions
per block against the stock reverb's 34,000). The build that runs the stock reverb until
the Pattern screen's update first runs, then clears the buffers over 8 blocks and
switches, boots and plays (one unexplained crash during heavy editing, not reproduced).
CPU on the device (Config's debug readout) with a pattern built to load it (every drum
hit and all three synths, 8 voices each, sending fully to the reverb): 61 % average and
84 % peak with the stock reverb, 66 % and 86 % with Freeverb. So Freeverb's extra cost is
about 5 points on average and leaves headroom during playback; what breaks the boot is
specific to running it while the unit starts, not steady load.

The mixer's reverb mute (Master page 3) stops the reverb's input, not its return: on
stock 1.2.0 a long tail keeps ringing after the channel is muted.

`patch/masterfx` does this for the reverb: Freeverb (public domain) in place of the
stock reverb when the project chooses it, reading Size (`+0x54`), Damping (`+0x5c` = 1 - damping), Diffusion
(`+0x58`, the setter clamps it to 0..0.85) and Predelay (`+0x6c`, 0..5000 samples) as
the Master Reverb setters (`0x600b3618`, `0x600b3628`, `0x600b3660`, `0x600b3640`)
store them, and mixing its return like the stock reverb: send + (wet - send) * amount
(`+0x4c`, 0.8). The project keeps the knobs as floats 0..1 at `0x202049cc` (Size),
`0x202049d0` (Damping), `0x202049d4` (Predelay) and `0x202049d8` (Diffusion).

Open-source algorithms with licenses that fit (MIT or public domain) include Freeverb,
the Dattorro plate, CloudSeed, and the reverbs and delays in Mutable Instruments'
eurorack code.

### Choosing the effect per project

Keeping the stock reverb and offering Freeverb (or others) as a choice works, tested in
the emulator by letting the stock `update()` and Freeverb take turns on the same object
(`patch/masterfx` does this, with a Project Settings item and a file per project):

- **What the stock reverb keeps:** a write watch over 40 blocks of its `update()` shows
  writes only to the audio blocks, its two 32 KB buffers (`+0x24`, `+0x28`, all of
  both), and object fields `+0x1c`/`+0x20` (positions), `+0x3c`/`+0x40` (LFO),
  `+0x60`..`+0x68` (filter states) and `+0x70` (level). Nothing else.
- **Freeverb to stock:** the stock reverb run on Freeverb's leftover buffers plays them
  back (peak 4,932 with silence in), so the switch has to clear both buffers and zero
  `+0x60`..`+0x68` first. After that it is silent with silence in, and its impulse
  response matches a stock-only unit's (same level and decay; samples differ by up to
  31 because its LFO is at another point).
- **Stock to Freeverb:** Freeverb's own start (state reset, buffers cleared over 8
  silent blocks) gives output identical to the bit to a fresh Freeverb.
- **Back and forth:** 20 switches with noise going through, every tail dies out to
  zero, as on a stock-only unit.

**A NaN setting silences the stock reverb for good.** In stock firmware, a NaN in
Size (`+0x54`), the damping field (`+0x5c`) or Diffusion (`+0x58`) makes its filter
states (`+0x64`, `+0x68`, with an infinite damping field also `+0x60`) NaN, and they stay
NaN after the setting is normal again: no reverb until a restart (emulator). The Master
page setters clamp their values, so the knobs can't do it; only a damaged value from
elsewhere could. Freeverb clamps NaN to the bottom of each range, and a switch to the
stock reverb zeroes those states.

Switching costs at most 8 blocks (23 ms) of silent reverb return (the dry signal is
not touched), and the rule from the boot hang stays: the stock reverb runs until the
Pattern screen has updated once, whatever the project chose.

**The delay** is swapped the same way. Its `update()` (`0x60032404`) receives the mono
send read-only, allocates two blocks, runs `master_delay_process(obj, in, out_l,
out_r, 128)` and transmits them as the stereo return. Its three 2^18-sample buffers
(512 KB each) are at object `+0x14`, `+0x18`, `+0x1c`; they are allocated at runtime
(zero after the static constructors), so a swapped delay must check them before use.
The other fields read by the DSP: `+0x20`, `+0x28` (positions), `+0x2c` (feedback),
`+0x2d` (bypass), `+0x2e` (ping-pong), `+0x30`..`+0x50` (filter coefficients and
states).

**The Master Delay knobs.** The engine's setters (`0x600b35b4`..`0x600b3614`, on the
delay object at engine `+0x2620`, next to the reverb's at `+0x261c`) call the delay
object's own (they block the audio interrupt while they write, `0x600303d4` /
`0x600303c8`):

| Knob | Setter | Field |
|---|---|---|
| Feedback | `0x60031f20` (0..0.99) | byte `+0x2c` = value × 255 (0..252) |
| Time | `0x60031f80` (ms, up to 3500) | `+0x28` = ms × 44.1: the delay in samples |
| Rate (with Sync) | `0x60031fe0` (index) | only with `+0x2f` set: `+0x28` = (15000 / BPM) ms (a 16th note) × a table at `0x60042f98` (8, 6, 4, 3, 2, 1.5, 1, 0.75, 0.5, 0.375, 1/3, 0.25, 0.1875, 1/6, 0.125, 1/12, 0.0625, 1/24, 1/32), up to 3500 ms |
| PingPong | `0x60032060` | byte `+0x2e` |
| Sync | `0x60032068` | byte `+0x2f` |
| Filter | `0x60032070` (Hz) | a one-pole lowpass: w = 2π f / 44100, p = cos w / (1 + sin w); `+0x30` = `+0x34` = (1 − p) / 2 × 1023, `+0x38` = −p × 1023 (Q10); its states `+0x3c`..`+0x50` are zeroed |

The project keeps PingPong, Sync and Rate in one byte (`+0x6d2` of the project
structure: bit 7, bit 6, bits 0..5), which `0x601912e4` and `0x6019197a` hand to the
three setters (through veneers at `0x601acdd4`, `0x601ad0e4`, `0x601acc44`). `0x600322a0`
and `0x600322a8` set and clear the bypass byte (`+0x2d`, set after the constructors).

Measured in the emulator (buffers given): the return is the echoes only; an impulse
comes back after exactly Time, then every Time again, each repeat about 0.43 × the one
before at Feedback 0.5 with the Filter open; the Filter is on every echo, the first one
included; with PingPong the first echo is on the right, then left, then right. It costs
about 8,000 instructions per block. `patch/masterfx`'s Tape reads these same fields
(`+0x28` for the time, synced or not), so the knobs keep working.

**The choice** fits the Config menu's option item (type 4: value pointer, count, names,
apply callback; section 14), so a "Reverb" item in Project Settings can list Stock,
Freeverb and others, and it can be kept per project in a file next to the project, as
`interpolate.bin` is. Stock firmware ignores the file and plays the stock effects.

**Effects loaded from the SD card.** The MPU (decoded in the emulator) leaves every RAM
region executable (XN = 0 for ITCM, DTCM, OCRAM and SDRAM), so code read from a file can
run. What it needs: a fixed binary format (header with magic, interface version, name,
code size, entry points for init and process, and a relocation table, which the patcher's
`.rel` format already is); a loader that reads it outside the audio interrupt (from the
Pattern or Config screen's update, as the settings files are); code memory (on-chip RAM
is small: the free regions are 1.2 KB of ITCM, 5 KB of DTCM and 12 KB of OCRAM, shared
with the other patches; the SDRAM top has 25 KB but is cached, so after loading, the
D-cache must be cleaned and the I-cache invalidated); and state memory (the reverb's
64 KB of buffers, the delay's 1.5 MB). A bad file can crash the unit, so the loader
should check the header and size, the effect should start only after boot, and taking
the file off the card should bring back the stock effect.

## 20. Pad lights and the Pattern screen's column buttons (`patch/visualizer`)

**The pad LED path.** The pads are single colour: one LED per pad on the IS31FL3731
(I2C `0x74`), 48 of its 144 PWM slots.
- **Driver.** Object at board + `0x454` (board `0x2000bedc`; vtable `0x600373b8`, one
  method, the flush `0x60092930`). PWM frame at +8, LED enables at +`0x14e`, dirty flag
  at +`0x14d`.
- **Writing a pad.** `0x60092630(driver, pad, on, level)` maps the level (0..31) through
  the gamma table at `0x60037390` (the same table as the original Tracker's) into slot
  `pad + (pad / 12) * 4`, sets or clears the pad's enable bit, and marks the frame dirty.
- **Flushing.** The main loop's I2C service scheduler (`0x600a0d70` on the list at
  `0x2000ba4c`, every 500 timer ticks) calls the flush, which sends the frame when dirty.
  It blocks the main loop while it runs. It first checks 13 function-page registers
  (`0x600927f8`: for each, select page `0x0B`, read the register, rewrite it if it differs
  from the copy at +`0x160`), then selects the frame page (+`0x99`) and writes all 180
  bytes from register 0 (enables, blink, PWM): about 28 transactions and 260 bytes on the
  400 kHz bus (the LPI2C master configs at `0x600373e0` hold 400000 for both buses).
  Bus writes are the bus object's vtable +`0xc` (bus, address, register, 1, buffer,
  length), reads +`0x10`; both return 0 when done. If the page select fails, the flush
  re-initialises the chip (`0x600928f8`). Updating the pads every frame this way made the
  screen lag, so `patch/visualizer` sends only the changed PWM bytes while it shows.
- **The app's interface.** The pad LED object at board + `0x450` (`0x2000c32c`) has a
  vtable with one entry, the word at `0x60038298` = setPad `0x600a1bf1` (obj, pad, on,
  level). It passes the call to the driver. Board vtable slot +`0x28` (`0x600a1c8d`)
  returns this object.
- **The pad manager.** Object `0x200171d0`, set up by `0x600abda4(mgr, 500, leds)` in the
  app's init. It holds the interface pointer at +`0x98` and three 48-byte layers: A at
  +4, B at +`0x34`, blink at +`0x64`. A pad shows the larger of its A and B levels.
  - Setters: `0x600abdfc` for layer A and `0x600abdc8` for layer B, both
    (mgr, on, level, pad).
  - Clear: `0x600abea4`.
  - The blink service: `0x600abe30`.
- **What the screens use.** Screens light pads through the manager's setters. The
  Pattern screen's pad refresh (`0x600e1310`) shows either the notes on the cursor row
  (record off, by the "pads show notes" setting at settings + `0xd1`) or the selected
  column's values (record on). It uses the pads brightness settings at settings +
  `0x34` / `0x35` (0..31).

**Column buttons.** In the Pattern screen, `0x600df1e4` registers the button callbacks
through `0x600cf74c(*0x200209e0, id, callback)`. Each callback is a single flash literal:

| Button | Id | Callback | Literal |
|---|---|---|---|
| Note | `0x12` | `0x600d20ad` | `0x600df2dc` |
| Instrument | `0x13` | `0x600d4495` | `0x600df2e0` |
| FX1 | `0x14` | `0x600e308d` | `0x600df2e4` |
| FX2 | `0x15` | `0x600e2fad` | `0x600df2e8` |

- **Arguments.** A callback gets the state: 1 pressed, 0 released.
- **A press.** It selects the column (Pattern screen + `0x1255`: 0 Note, 1 Instrument,
  2 FX1, 3 FX2). Through `0x600df168` it also marks the button "held" (+`0x14c7` +
  column, with the press time at +`0x14cc` + 4 × column), but only if no other column
  button is marked.
- **Long press.** The screen's update fires a column's long-press action once its
  button has been held for more than 180 ms (`0x600e1ff4`). A release clears the mark.
- **Other buttons.** REC (`0x1c`, `0x600e2c29`) toggles the record flag at +`0x1254`.
  Play is `0x17` (`0x600c83a9`).
- **Pad mode.** +`0x1354` (int8) is −1 normally. While a bottom-row button that shows its
  own pads is held, it holds that view: 0 while Pattern is held (the patterns that exist
  are lit, the current one brighter; set by `0x600e24f5`), 1 and 2 for two other views
  (`0x600e249d`, `0x600e2445`). The pad refresh (`0x600e1310`) lights that view first.
- **Held query.** `0x600cf90c(*0x200209e8, id)` says whether a button is held. It goes
  through a vtable.

**The timer.** `*0x20027d18` is the app's millisecond timer: vtable[0] returns ms. It is
set in the app's init, not by static constructors.

## 21. Synth engines at runtime: system, slots, voices (emulator)

Section 17 worked from static analysis. Here the synth system runs in the emulator. After
the static constructors, `synth_audio_setup_q(app + 8, hal, fn)` (`0x600b2118`) and then
the audio setup `0x600b421c(0x20017b08, hal, app + 8)` are called the way the app's init
(`0x600aa3c8`) calls them:
- `hal` is a fake object whose vtable `+0x4c` returns a fake file system, and whose
  `+0x18`, `+0x1c` and `+0x58` return fake objects (the codec and two others).
- `fn` is the `std::function` the app passes: `{0x200170e0, 0, 0x600aa711,
  0x600aa791}`.
- The audio setup also runs `audio_memory_init`.

After that, the real synth host, slots and voices exist. Notes can be played through the
host, and the synth output object renders them inside the audio graph (`0x6003061c`). The
setup takes about 16 KB of heap in the emulator. The plan built on this is in
[SYNTH_ENGINES_PLAN.md](SYNTH_ENGINES_PLAN.md).

### The synth system object (`S` = app + 8 = `0x200170e8`)

| Field | What |
|---|---|
| `S + 0x30 × i` (slot i = 0..2) | Model id of slot i (1 ACD, 2 FAT, 4 VAP, 5 WTFM, 6 PERC). The project record's model index 0..4 maps to these ids through the table at DTCM `0x200006b4` (`.data`) |
| `+ 0x04 / 0x08` | The slot object, and its `shared_ptr` control block |
| `+ 0x0c / 0x10` | The slot's patch object (the parameters), and its control block |
| `+ 0x14` | The patch name (`std::string`, "Init" at start) |
| `S + 0x90` | The synth manager (vtable `0x600631b4`). `+0x4c` renders one slot (`0x60125fb9`); `+0x2c` registers a storage folder (id 2 "Patches", id 0 "Workspace") |
| `S + 0x9c` | The model registry wrapper. vtable[0] creates a patch object for a model id |
| `S + 0xac` | The MIDI router (0x48 bytes, vtable `0x6005ed5c`). Its interface at `+8` (vtable `0x6005ed98`) is the "host" that `seq_note_on_out_q` uses |
| `S + 0xb0` | The file system object (hal `+0x4c`) |

The functions on `S`:
- `synth_slot_set_model(S, slot, id)` (`0x600b1440`) returns early if the id is
  unchanged. Otherwise it makes a new patch object (named "Init"), caps the polyphony,
  and calls the slot's `setPatch` with the audio interrupt off.
- `synth_slot_set_polyphony(S, slot, n)` (`0x600b13cc`) calls the slot's vtable `+0x1c`
  with the interrupt off. It does no cap check.
- `synth_slot_max_polyphony(S, slot)` (`0x600b1400`) returns min(the model's maximum,
  container `+0x40`, 8 − the other two slots' voices). **All three slots share 8
  voices.**
- `0x600b2080(S, 0x202046c4, reset)` applies the project's three slot records: polyphony
  `+4`, model index `+8`, and the name.

### The router (host)

The host's methods turn calls into 4-byte messages: `{type, channel − 1, data1, data2}`.
Each message goes to every subscribed slot.

| Host vtable | Function | Message |
|---|---|---|
| `+0x20` | note on (note, velocity, channel), `0x60111234` | type 9 |
| `+0x24` | note off, `0x60111450` | |
| `+0x1c` | pitch bend, `0x601110d0` | type 8, 16-bit value |
| `+0x0c` | CC, `0x60110f14` | type 6. CC 110 and 111 are kept in the router itself |
| `+0x18` | `0x601112e8` | Asks every voice "any key held" |

The channel is the slot + 1.

### The slot (class vtable `0x6006441c`, methods in ITCM, 0x5bc bytes)

| Offset | What |
|---|---|
| `+0x14` | 8 voice records of 0x2c bytes. Word 0 is the voice object |
| `+0x174` | Number of voices |
| `+0x178` | The model id the voices were made for |
| `+0x17c` / `+0x37c` | Scratch L / R: 128 floats each |
| `+0x590` | The model registry |
| `+0x5a4` / `+0x5a8` | The patch (`shared_ptr`) |

The slot's vtable:
- `+0x00` `synth_slot_set_patch` (`0x1e649`).
- `+0x18` voice count (`0x1dc6d`).
- `+0x1c` `synth_slot_set_voices` (`0x1e645` → `0x1e484`).

How the slot manages voices:
- `synth_slot_set_voices(slot, n)` looks up the model's descriptor for the patch's model
  id.
  - If the id differs from `+0x178`, it first deletes every voice.
  - It then creates voices with the descriptor's factory (`std::function` at descriptor
    `+0x28`, invoker `+0x34`, called with the voice index), or deletes the extra ones.
  - Each new voice gets `voice + 4/+8` = the patch's `shared_ptr`. So **`voice + 4` is the
    slot's patch object**, which is `S + 0x0c + 0x30 × slot`.
  - Last, it pushes every parameter to every voice: `synth_slot_push_params` (`0x1e40c`)
    → `synth_slot_send_param` (`0x1e2ac`) → voice `+0x0c`.
- Note on: the slot asks voices `+0x18` in order and gives the note to the first one
  that is not sounding. It sets parameter 39 (Volume) to velocity / 127 on that voice,
  then sends it message `04 00 note velocity`.
- Note off: the slot asks `+0x1c(note)` on each voice. The one holding the note gets
  `05 00 note velocity`, then `+0x28`.

### The audio path

1. The synth output object's `update()` (`0x60036634`, a graph object whose `+0x14` is
   `S`) handles each slot whose voice count is not 0. It calls the manager's render
   (`0x60125fb8`) into float buffers, scales by 32767 (`0x60036970`) into 16-bit blocks,
   and transmits them. When the patch's channel count (container `+0x28`) is 1, the left
   buffer is used for both sides.
2. `synth_slot_render` (`0x1f0d8`) calls **every voice's render (`+0x10`) every block,
   playing or not**.
   - With one voice, the voice writes straight into the output (`0x200278e4` /
     `0x20027ae4`).
   - With more, each voice writes into the slot's scratch, which the slot copies and adds
     into the output (a 512-byte `memcpy_small` per buffer per voice).
   - Last, it calls voice 0's `+0x14`.
3. The render call is `render(voice, ctx, 0, 0, [sp] 0, [sp+4] L, [sp+8] R, [sp+0xc] 2)`:
   - `ctx[0]` is a float tempo (130.0 in the emulator).
   - L and R hold 128 floats at 44,100 Hz.
   - A voice **overwrites** its 128 samples, at about ±1.0 full scale.
   - The mono models write only L. The VAP voice leaves R untouched.

### The voice interface (VAP: vtable `0x600667e4`, used only by the VAP factory)

The other models' voice vtables have the same layout: ACD `0x600662f0`, FAT `0x600674e4`,
WTFM `0x60067814`, PERC `0x60066f90`.

| Slot | VAP | What |
|---|---|---|
| `+0x00` / `+0x04` | `0x6014b5e5` / `0x6014b725` | Destructor / deleting destructor |
| `+0x08` | `0x6014a9a9` `vap_voice_midi` | `(voice, ctx, msg)`. Message type 0 and 1 go to the core (`0xbf71` / `0xbf69`, on core + `0x1c8`); 4 note on (note, velocity / 127); 5 note off; 8 pitch bend (±2 semitones) |
| `+0x0c` | `0x6014a8f1` `vap_voice_set_param` | `(voice, index, float *value)`, through a per-parameter `std::function` table at `0x2002d1a4` |
| `+0x10` | `0x6014a8b1` `vap_voice_render` | Calls the ITCM core `0xc078` on voice + `0xc` |
| `+0x14` | `0x6014a811` | Nothing (VAP) |
| `+0x18` | `0x6014a81d` `vap_voice_sounding` | Byte `+0x114`. Set by a note on; cleared by the render when the amp release ends (about 0.5 s after note off at the Init patch), not by the note off |
| `+0x1c` / `+0x20` | `0x6014a829` / `0x6014a851` | Holds this note / holds any note: a held-note list of 8-byte entries at `+0x18`, count `+0x58` |
| `+0x24` / `+0x28` | `0x6014a85d` / `0x6014a869` | Note queries the slot uses for mono and legato |
| `+0x2c` | `0x6014a8a1` | Core `0xe445` (note-list cleanup) |

The VAP factory invoker is `0x6014a959` (literal `0x6014dca8` in the descriptor builder).
It allocates 0x398 bytes and runs the ITCM constructor `0xba50`. The construction-time
vtable `0x600667ac` lists the same methods; finished voices carry `0x600667e4`.

### The parameters

The patch object (VAP: vtable `0x6006307c`, ITCM methods) returns its parameter container
from vtable `+0x50`:
- `+0x00` a vector of 0x38-byte parameter entries. Each entry has a `std::string` name,
  a float value at `+0x18`, the min and max as half floats at `+0x1c` / `+0x1e`, flags
  at `+0x20` (byte 3: the MIDI CC, or 0xff), and a display formatter (`std::function`)
  at `+0x28`.
- `+0x0c` 8 Macro entries of 0x28 bytes.
- `+0x24` the model id.
- `+0x28` the output channels (1, or 2 for PERC).
- `+0x40` the maximum polyphony (unlimited, or 1 for PERC).

The voice's `set_param` index is the entry's position. VAP has 60:

| Index | Parameters |
|---|---|
| 0–11 | Shape 1, Shape 2 (0..1), PW 1, PW 2 (−0.5..0.5), Mix (−1..1), Tune 1, Tune 2 (−36..36 st), Detune (0..100), Finetune (−100..100), Reset 1, Reset 2, Type (filter, 0..14) |
| 12–14 | Cutoff (20..20000 Hz, default 400), Resonance (0..1.5), Env Amt (−1..1) |
| 15–18, 19–22, 23–26 | Three ADSRs: Attack, Decay, Release 0..10, Sustain 0..1. Measured (see "VAP's envelopes, measured" below): 19–22 is the amp envelope, 23–26 the filter's (with Env Amt; the Edit Patch screen's Env 1) and 15–18 a free envelope (Env 2). (Read from the defaults alone, this table first said 15–18 amp and 19–22 filter.) |
| 27–34 | Two LFOs: Ratio, Waveform, Retrigger, Sync |
| 35–41 | Glide Mode, Glide Time, Note Track, Mix Env Amt, Volume (0..2: velocity, set per note), Sync, Noise |
| 42–59 | A 6-slot matrix: Src, Dest, Amt |

The other models' parameter builders are the descriptor's first `std::function`:
- ACD `0x601482e0` (section 17 called it the factory). Its factory is the lambda
  `0x60145e80`.
- VAP `0x6014dea0`, FAT `0x6015cab8`, WTFM `0x60161ed8`.

They also lay out the editor's pages ("Oscillators", "Envelope 1", "Matrix", "(1/2)"...),
so the Instrument screen's synth pages follow the parameter list.

### Cost and memory per voice (emulator)

Instructions per voice per 128-sample block (rendered through the real graph). Heap is
measured with `mallinfo` around raising a slot's polyphony.

| Model | Voice object | Heap per voice | Instructions, idle | Instructions, playing |
|---|---|---|---|---|
| ACD | 0x26c | 3,140 B | 47,746 | 48,923 |
| FAT | 0x344 | 3,028 B | 51,751 | 55,249 |
| VAP | 0x398 | 6,472 B | 60,804 | 66,195 |
| WTFM | 0x3a4 | 6,484 B | 57,024 | 60,571 |
| PERC | 0x17ac | 6,068 B | 4,333 | 34,905 |

- The four synth models cost the same idle as playing. Only PERC skips silent voices.
- A whole graph block with 8 VAP voices playing and two idle ACD voices took 883,878
  instructions. With three idle ACD voices it took 356,619.
- In the emulator the heap is at `0x20041f38` after the two setups (64,816 bytes below
  its limit).

### Found while building `patch/synths` (emulator)

- **VAP's amp envelope is parameters 19–22, not 15–18.** An Attack on 19 slows the
  onset, Sustain 21 holds the note, and Release 22 sets the tail. 15–18 change nothing
  with the filter open. (This note went on to call them the filter envelope; that was
  wrong: VAP's filter envelope is 23–26. See "VAP's envelopes, measured" in section 23.) VAP's default
  patch is a pluck: amp Sustain 0, Decay 0.5. The Init patch's Macros agree: Macro a
  ("Attack") moves 19 and Macro b ("Decay/Release") moves 20 and 22; c is empty, d moves
  Cutoff (12), e Resonance (13), f Env Amt (14).
- **VAP's envelope times** (open filter, velocity 127): the attack reaches full level at
  about Attack seconds. With Sustain 1, Release R falls 20 dB after about 0.37 R and
  60 dB after 0.97 R (R = 0.5), 0.85 R (R = 1) or 0.64 R (R = 3); "sounding" clears just
  after. Decay is timed the same way. The output is linear in velocity.
- **The render call has five stack arguments**, not four: `render(voice, ctx, a1, a2,
  [sp] a3, [sp+4] L, [sp+8] R, [sp+0xc] count, [sp+0x10] buffers)`, where `buffers` points
  at the output buffer pointers and `count` is 2 (`synth_slot_render`, `0x1f1a4`–`0x1f1de`).
  No float register arguments. VAP's own render reads only voice, ctx and L.
- **Message types 0 and 1 are MIDI realtime messages.** The host's `+0x08` (thunk
  `0x60111514`) is `0x60111450`: it takes a realtime status byte and broadcasts, to every
  slot, `0xF8` clock as type 0, `0xFA` start as type 1, `0xFC` stop as type 2 and `0xFB`
  continue as type 3. The slot passes types 0, 1 and 7 to all its voices. VAP sends
  0 and 1 to its tempo-synced LFO (`0x781c` counts clock ticks, `0x7928` restarts the
  count); they change nothing audible on the Init patch. Neither the sequencer's play and
  stop nor the host's other methods sent them in the emulator. The note off is
  `0x6011139c` (thunk `0x60111448`); the table above and `names_1.2.0.tsv` give
  `0x60111450` for it.
- **Pitch bend:** VAP plays the message's signed 16-bit value as value × 2 ×
  1.0173768e-5 octaves, so ±8192 is ±2 semitones (and ±32767 about ±8).
- **Macros reach a synth as CCs 102–107.** `synth_apply_fx_q` only marks the FX pending;
  `0x416c` (from the engine's block update) applies it through `0x40e8`, which sends a
  Macro FX a–f to the router as CC 102–107 on the synth's channel (`midi_send_cc` `0xacc`).
  Delay send, panning and reverb send go to `0x3e68`, `0x3c68` and `0x3fa8`. CC 74 is
  Cutoff, 71 Resonance (the parameter entries' CC bytes).
- **VAP's display names** (the formatters, called as `invoker(ret string, functor, 0,
  float *value)`): Type 0–14 are LP MG 24dB, LP OB 24dB, LP OB 12dB, LP SVF 24dB, LP SVF
  12dB, HP OB 24dB, HP OB 12dB, HP SVF 24dB, HP SVF 12dB, BP OB 24dB, BP OB 12dB, BP SVF
  24dB, BP SVF 12dB, Notch SVF 24dB, Notch SVF 12dB (table at DTCM `0x20003ea8`). Glide
  Mode 0–3: Always, Overlap, Legato, Legato O. Times show as seconds, Detune as cents,
  Env Amt, Note Track, Resonance, Noise and Volume as percent.
- **Stock VAP faults on some garbage values**, in stock firmware too: a NaN, infinite or
  huge LFO Ratio (27, 31) makes its render loop for ever (`0x7744`), and a huge matrix
  Dest (43, 46, …, 58) makes its `set_param` read unmapped memory (`0xc5f8`). The UI
  keeps them in range.
- **The heap holds about 13 VAP voices** in the emulator after the setups (8 + 4 + 1;
  one more runs it out, stock firmware too), more than the 8-voice cap, which only
  `synth_slot_set_model` applies. With the heap run (nearly) full, a VAP voice made by a
  model change left the heap corrupt after its next block (stock code at `0x7400` writing
  near the heap top, then `mallinfo` faulting), so a full heap is not survived.
- The synth output object is `0x20019168` (vtable `0x60043078`). It sends each slot's
  block to outputs `slot` and `slot + 3`, and its right block to `slot + 6` and
  `slot + 9`. Its `update()` alone costs about 191,000 instructions with 8 Wavetable
  voices playing (3 + 3 + 2): about 96,000 of it the voices, the rest the slots' scratch
  copies and the 16-bit conversion, which 8 stock VAP voices pay on top of their
  roughly 530,000.

## 22. Chance FX, loop counting and ITCM hooks (conditional trigs, emulator)

Found while planning conditional trigs (`docs/CONDITIONAL_TRIGS_PLAN.md`).

### The Chance pre-pass

- **The loop.** In `seq_process_track` at micro-step 1, a loop at ITCM `0x2604`–`0x2616`
  checks FX2 and then FX1:
  - r7 = seq + 0x68·t, minus 2 for FX1, so the column's type is at `r7 + 0x73` and its
    value at `r7 + 0x74`;
  - r8 = t and r4 = seq.
- **A Chance column** (type 4) branches to `0x3094`:
  1. The value goes to `[sp, #0x24]`.
  2. `fx_max(seq, 4)` is called at `0x30c6`.
  3. `rand_range_q(0, max)` is called at `0x30ce` (`bl 0x3af4`).
  4. `cmp r0, value; ble` passes (`0x3180`). Otherwise track `+0x98` = 1.
- **Track `+0x98`** (Chance failed) is cleared at micro-step 1 right after the step copy
  (`0x21b4`). When it is set:
  - the FX pass skips the column (`0x2650` → `0x270a`);
  - the note is skipped (`0x29b2`).

  Two Chance FX on one step must both pass.
- **Values above 100**, stock 1.2.0, emulator: values 101, 102, 107, 137, 200 and 255, in
  FX1 and FX2, played on all 20 of 20 loops. `fx_value_text` shows them as plain
  numbers, compact or not. `seq_change_selection_fx_value` turns any edit of such a cell
  (±1, ±10) into 100.
- **Scratch proof.** The BL at `0x30ce` was pointed at a 12-byte stub in the ITCM tail:
  `mov r2, r7; mov r3, r8; ldr.w pc, [pc]; .word hook`. The hook returned 0 (pass) or
  255 (fail).
  - That decided the whole step: a failed step dropped its note and the CC in its other
    column.
  - The stub received r7 offsets −2 (FX1) and 0 (FX2), and r8 = track.

### FX value editing and text

- **The encoder's max.** `seq_change_selection_fx_value` (`0x600af0f8`) takes a cell's
  max from `bl 0x600af2c8` at `0x600af1b2`, a flash veneer to `fx_max` (literal `0x3825`)
  with no other caller.
  - Arguments: r0 = selection, r1 = type, r2 = value + delta.
  - Result: the cell gets min(max, value + delta), or 0 when that is negative.
  - Types 13..39 have their own cases (a `tbb` at `0x600af18a`). Type 4 takes the
    default.
- **The `fx_max` veneers.** 43 flash veneers lead to `fx_max`, one per caller function
  (the voice FX setters, the Fill popup, randomizers and others). A hook on one of them
  doesn't affect the rest.
- **`fx_value_text` callers:**
  - the tail call in `fx_value_text_cell` (`0x600afe04`);
  - the Fill popup texts at `0x600e3fd0` and `0x600e425c`.
- **`fx_value_text_cell(seq, buf, size, column, track, step)`** (`0x600afdc8`):
  - track and step are stack arguments, and a negative step means no cell;
  - the pattern grid calls it with a 4-byte buffer at `0x600df748` (column = r5, FX1 (q))
    and at `0x600df77c` (column 1, FX2).
- **`seq_set_selection_fx_type_q(selection, column, type)`** (`0x600af4f0`):
  - It stores the type. Then, unless `0x600e3288` returns the same type, or
    `0x600e12d4` returns nonzero, it sets the value to the type's default, a byte at
    `0x60039cd0 + type − 3`.
  - Callers: `0x600c6e46`, `0x600d48f4`, `0x600e2e22`.
- **`seq_set_selection_fx_scaled_q(selection, column, position)`** (`0x600af8e8`) maps a
  position linearly into min..`fx_max` (through veneer `0x600afb20`), for every cell of
  the selection. It is called from `0x600c6ff0`.

### Starting playback

- **`seq_play_from`** (ITCM `0x1250`) only sets up: after it, every track is at step 0
  with micro-step 1 still to process, and nothing has been sent (emulator). The first
  step is processed by `seq_tick`.
- **Its callers:**
  - flash code, through 4 veneers (`ldr.w pc, [pc]`, literal `0x1251` at +4):
    `0x600ac600`, `0x600ac690`, `0x600acd30` and `0x600ad540`, reached by `b.w` from
    `0x600ac5fc`, `0x600ac66a`, `0x600acd16` and `0x600ad518`;
  - ITCM, once: at `0x3752` in `seq_tick`, at the end of a count-in. That code clears
    the count-in flag `+0x6f8` and song mode `+0x1c`, calls `seq_play_from(seq, 0)` and
    goes on processing ticks in the same call.
- **`seq_play_selection`** (`0x600ac3d0`) is reached by `bl` from `0x600c8490` and
  `0x600fe6ce`, and by `b.w` from `0x600ae070`, `0x600ae5b0`, `0x600ae716`,
  `0x600ae8c2`, `0x600af25a`, `0x600afa5e`, `0x600bb98a`, `0x600d48b6` and `0x600e2b42`.
- **Pattern end.** In `seq_advance_step`, the code from `0x600ad1ea` runs once track 16
  has wrapped (`+0x33` set): it clears `+0x32` (Break pending) and checks `+0x28`
  (queue active).

### ITCM code from a patch

- **The startup copy table** (section 2) has two unused entries:
  - entry 2 at `0x600302b8`: `0x601ad21c` → `0x20200000`, length 0;
  - entry 3 at `0x600302c4`: `0x601ad21c` → `0x80000000`, length 0.

  The reset handler (`0x60030332`–`0x60030358`) copies whole words while the offset is
  below the length. Nothing else references the table's entries; there are literals
  only for its start and end. An entry pointed at patch flash and the ITCM tail
  (`0x1fb40`–`0x1ffff`, free, section 15) would load patch code into ITCM at boot,
  together with the stock ITCM code. `machine.py` runs the table from the image.
- **MPU.** ITCM above 64 KB, which includes the stock veneers at `0x1f530`–`0x1fb38`,
  lies outside region 5. It falls under region 4 (RW, exec, section 8), and the veneers
  execute there on hardware. Code in the ITCM tail would run under the same
  attributes.
- **Space.** The copied ITCM image ends with the last veneer, at `0x1fb30`–`0x1fb38`,
  and has no spare bytes inside. The stock application image ends at `0x601bab78`.
- **`rand_range_q` callers.** ITCM code calls it with direct `bl`s from 25 sites,
  among them `0x0ba2`, `0x0de8`–`0x0f1a` (15 sites), `0x2b4c`, `0x2c50`, `0x2d68`,
  `0x30ce` (Chance), `0x3174`, `0x31e2`, `0x3242`, `0x33d0` and `0x354e`. Hooking the
  function itself would affect all of them, so a hook belongs on the call site.
- **Tested on hardware** (`patch/itcmtest`): a build with stubs in the ITCM tail, loaded
  by copy-table entry 3, boots. With the Chance call (`0x30ce`) and the count-in's
  `seq_play_from` (`0x3752`) going through them, C 50 steps skip about half the time
  in normal play, with record on, and after a count-in (Record held, then Play).

### Counting a track's passes

- **Track `+0x8a`.** `seq_advance_step` counts a track's steps since its loop started
  in `+0x8a` (int16), in every play mode, and sets it back to 0 when the loop starts
  again:
  - forward, when it passes the last step or a Break FX is pending;
  - in performance mode (tracks 1–8 only), the backward, random and pattern-table
    modes compare it with the track's own length.

  The step itself (`+0x88`) follows the play mode. So "`+0x8a` is 0 after
  `seq_advance_step(seq, t)`" means "track t starts a new pass" in every mode.
  `seq_play_from(seq, step)` sets `+0x8a` = step, so starting from a later step keeps
  the two in step (emulator: from step 3 of 4, `+0x88`/`+0x8a` go 2/2 3/3 0/0).
- **Performance-mode lengths.** For tracks 1–8 the last step comes from `seq+0x36`
  (int16, used when ≥ 0) or the track's own `seq + 0x38 + 2·t`. Tracks 9–16 always
  take the normal path.
- **The playing pattern's number.** `seq_swap_pattern_buffers` (`0x600acc68`) sets
  `project+0x6c8` from the file manager's byte `+0x130` (`0x600c2510` reads it),
  which the main loop's preload fills. In the emulator, with the preload request
  stubbed, it reads 255 unless the stub sets it.
- **Random FX value (x, 16) leaves a Chance column alone.** Next to C 105 the step
  played 61 of 61 times, and next to C 0 it never played (stock, emulator).
- **Synth notes in the emulator.** A synth step's note-off goes through `0x600abbe8`
  into the synth engine, which `seq_timing.make()` doesn't set up. Without a stub for
  it, emulation jumps to 0, in stock firmware too.

### Entering and showing FX values

- **Where values are shown.** Step FX values reach the screen only through
  `fx_value_text`. Its callers are the pattern grid (through `fx_value_text_cell`, for
  FX1 at `0x600df748` and FX2 at `0x600df77c`) and the Fill popup's from and to texts.
  - `fx_display_value` (ITCM `0x3910`) has one caller, in the FX pass (`0x2e70`,
    Tempo).
  - The other users of the FX name table (`0x600e3982`, which copies a name to show,
    and the FX list's data at `0x600cdd9c`) show names only.
- **The grid's value calls.**
  - The grid passes `column` = 0 for FX1 (`r5`, cleared at `0x600df716`) and 1 for
    FX2.
  - It ignores the result.
  - `fx_value_text_cell` reads track and step as bytes, the step signed, and finds the
    cell in `*(seq + 0x14)` at `+0x1f + 0x301·track + 6·step + 2·column` (type, then
    value).
- **Pad entry** (`seq_set_selection_fx_scaled_q`, `0x600af8e8`, `(selection, column,
  position)`) maps position 0..47 to min..`fx_max` (emulator: the last pad gives 100
  on a C 105 cell, pad 23 gives 49), through its own `fx_max` veneer.
- **The encoder hook stays local.** Hooking `0x600af1b2` changes no other writer's
  range. Pad entry, Fill and random fill use other `fx_max` veneers.

## 23. Synth models end to end: registry, patches, files and the Instrument screen (emulator)

Mapped for a swappable engine: a custom engine that replaces one of the five stock
models in the Instrument screen's Model list and shows its own name and controls.
Sections 17 and 21 cover the registry and the voices. Everything here is from the
synth rig (`tools/emu/synth_rig.py`) and Ghidra, on stock 1.2.0.

### The descriptor and the model id

A descriptor is 0x38 bytes:
- `+0x00`: the name (`std::string`, "VAP").
- `+0x18`: the parameter builder (`std::function`). The functor is a plain function
  (VAP `0x6014dea1`), and the invoker `0x60144955` calls `builder(a, b)`. `b` is the
  container's parameter vector; the builder registers each entry at its index.
- `+0x28`: the voice factory (`std::function`). The invoker (VAP `0x6014a959`) takes
  `(unique_ptr<voice> *ret, …)`, mallocs the voice (VAP 0x398 bytes), and leaves
  `voice + 4/+8` (the patch's `shared_ptr`) empty for the slot to fill.

`0x6012fd2c(&shared_ptr, registry, id)` is the registry's `find`. The manager's vtable
`+0x34` returns a model's name by id.

The project's slot record stores a model **index** 0–4 (record `+8`), not an id. The
table at DTCM `0x200006b4` (`.data`, writable) maps an index to an id: `{1, 2, 4, 5,
6}`. It is read in four places:

| Where | What |
|---|---|
| `0x600b20b2` | Applying a project's records (`synth_slots_apply_project_q`) |
| `0x600b23de` | The synth system's start-up models |
| `0x600d400e`, `0x600d40a2` | The UI's PERC special case (`table[index] == 6`) |
| `0x6011b63c` | The Model list: a selection calls `synth_slot_set_model(S, slot, table[index])` and writes the index into the record |

So changing one table entry swaps that index's model everywhere, and the record keeps a
stock index. Id 3 is free (no registered model).

### The patch object (one class for every model)

Every model's patch object has vtable `0x6006307c`. Its container sits at patch
`+0x54` and is returned by vtable `+0x50`. A builder fills it in:

| Container | What |
|---|---|
| `+0x00` | Parameters: a vector of 0x38-byte entries (VAP 60, ACD 29, FAT 25) |
| `+0x0c` | Macros: 8 entries of 0x28 bytes, each a `std::string` name, a vector of targets at `+0x18`, and flags at `+0x24`. VAP's are Attack, Decay/Release, (empty), Filter Cutoff, Filter Resonance, Filter Env Amount, Velocity, Pressure |
| `+0x24` / `+0x28` / `+0x40` | Model id; output channels; maximum polyphony |
| `+0x2c` | A `std::function` |
| `+0x44` | Groups: 0x48-byte entries, each a name, an empty short name, and a vector of 0x24-byte sub-entries (a name and three words). VAP's are Oscillator, Filter, Modulation, Matrix, FX, Voice. Their consumer isn't mapped yet |
| `+0x50` | Pages: 0x48-byte entries, each a name (`+0x00`), a short name (`+0x18`, "Osc", "Amp Env"; empty when the name fits) and an item vector at `+0x3c` |

**Page items** are 8 bytes each: `{1, parameter index}`, or `{0, −1}` for an empty
cell. VAP has nine pages:

| Page | Items |
|---|---|
| Oscillators | 0, 1, 2, 3, 5, 6, 8, 7, 41, 40, 9, 10 |
| Filter | 12, 13, 11, 14, 37 |
| Amp Envelope | 19–22 |
| Envelope 1 | 23–26 |
| Envelope 2 | 15–18 |
| LFO 1 | (empty), 30, 27, 28, 29 |
| LFO 2 | 34, 31, 32, 33 |
| Modulation | 42–59 |
| Voice | 35, 36, 39 |

ACD has five pages (Oscillator, Filter, Amplifier, Modulation, Voice). FAT's include
Amp Envelope and Filter Envelope.

**A parameter entry** is 0x38 bytes:
- `+0x00`: the name (`std::string`).
- `+0x18`: the value.
- `+0x1c` / `+0x1e`: the minimum and maximum, as half floats.
- `+0x20`: flags. Byte 3 is the MIDI CC (0xff for none); byte 2 is 1 on stepped
  parameters such as Tune.
- `+0x28`: the display formatter (`std::function`). Its invoker runs as
  `(std::string *ret, functor, 0, float *value)`. Some functors are malloc'd
  `{kind, format string, scale}` records, for example `{9, "{:.0f} st", 1.0}`.

The VAP builder (`0x6014dea0`) fills one temporary entry for each parameter: it sets
the name with `0x60145ff0`, then the default, range, flags and formatter. It registers
the entry with `0x6014dcb0`, which also takes VAP's per-parameter set handler (the
`std::function` table at `0x2002d1a4` that `vap_voice_set_param` calls).

### Patch files: protocol buffers (nanopb), values by position

A patch file is a nanopb protocol buffer: tags and varints are decoded by `0x6013cf60`
and `0x6013cebc`. `VAP/Init.patch` as the firmware writes it is 2,033 bytes:
- field 1: the model id (varint; 4 for VAP);
- fields 2, 4 and 5: varints whose values look like uninitialized words;
- field 6, repeated: a message `{1: float value}`, one per parameter, in parameter
  order, with no names;
- then the Macros and the rest.

Parsing and writing run in ITCM: `0x18790` loads a file into an existing patch,
`0x1c398` writes one, and `0x177f4` reads only field 1. The storage class (vtable
`0x60062fd4`) has `+0x10` save (`0x60124fc9`), load by name (`0x60124b39`) and load
into a patch (`0x60124f39`). Its file system object, at storage `+0xc`, has:
- `+0x04` open(`&{handle, ok}`, fs, `&path`, mode: 0 read, 1 write);
- `+0x08` close;
- `+0x0c` write(fs, handle, n, buf);
- `+0x10` read(fs, handle, n, buf);
- `+0x18` make a folder.

There are two storages:
- **Patches** (`S + 0x9c`, the registry wrapper): `<model name>/<patch name>.patch`,
  for example `VAP/Init.patch`. The folder is the descriptor's name, created when
  missing. The UI's Save goes through `0x600b1018(S, slot)` to its `+0x10`.
- **Workspace** (`S + 0xa4`): the project's own copy of each slot's patch, as
  `0.patch`, `1.patch` and `2.patch`, with no model folder.

`0x600b1270(S, slot, out)` loads a slot's patch:
1. It opens `<slot>.patch` from the Workspace.
2. When the file's model id is not the slot's (or the file is missing), it loads
   `<model>/<name>.patch` from Patches instead, falls back to Init, and writes the
   result back to `<slot>.patch`.

**Stock firmware is safe with an unknown model id** (emulator, an ACD slot):
- A `0.patch` whose field 1 is 7 (not registered), or 4 (VAP), is rejected. The slot
  plays ACD's Init patch.
- A `0.patch` claiming 1 (ACD) but holding VAP's 60 values is accepted, and the slot
  plays silence.

So a custom engine must save its patches under a model id of its own, never the id of
the model it stands in for.

### Loading a project (`0x60116350`)

1. `synth_slots_apply_project_q(S, records, 1)` (`0x600b2080`): polyphony, model (from
   the index table) and patch name for each slot.
   `synth_slot_set_model` returns early when the id is unchanged, so a slot on the same
   id keeps its patch object.
2. For each slot, `0x600b1270` loads its Workspace patch. When that fails, `0x600b17ec`
   resets the slot.
3. `0x600bce14` loads the rest of the project.

A per-project setting read before step 1 can swap a model before the project's
patches are parsed.

### The synth settings screen (Model, Polyphony, …)

`0x6011ae60` sets up the screen object `0x20020c74`. Its columns are Model, Polyphony,
Gain, Panning, Reverb send, Delay send, and Load and Edit for the patch.

**The Model list** (screen `+0xf8`) is a list control. It is created by `0x600d85e0`,
constructed by `0x60105fac` (vtable `0x60046a2c`) and drawn by `0x6010652c`. Its data
(screen `+0xfc`) has:
- the item count (5) at `+4` and the selection at `+6`;
- text items (`char **`) at `+0xc`;
- image ids at `+0x18`. The screen sets `+0xc` to 0 and `+0x18` to `0x60061488` =
  `{9, 10, 11, 12, 24}`.

With ids, `0x6010645c` draws each item as a bitmap from the image table at
`DAT_60106528` (0x14 bytes per image, ids below 0x24). The model names in that list are
logos. With text items, `0x601062d4` draws them with `eve_cmd_text_q`. So a sixth name
needs that list in text mode, or a draw hook that mixes the logos with text.

**Model names elsewhere** are text from the registry: `0x600b16b8` builds the
instrument title "Synth {} ({})" (for example "Synth 1 (VAP)") with the manager's
`+0x34`, or "Off" when the slot has no voices.

The instrument screens' headers include "Synth Parameters 1/2", "Synth Macros 2/2" and
"Edit Patch: %s" (`0x600d5ffc`).

Running these screens in the emulator needs the app's globals:
- `*0x20027d18`: a clock object, whose vtable `+0` is called;
- `*0x200209e4`: S;
- `*0x200209f0`: the title bar object (its `+0x60` holds S).

Their controls are created elsewhere, so nothing is drawn yet.

### Found while building WAVE (`patch/synths` sy2, emulator)

- **A patch's current values are a separate `vector<float>` at patch `+0x0c`.** The
  entries' `+0x18` floats are the defaults (the Init values). The current values are
  what new voices are pushed and what a patch file saves. They start as copies of the
  defaults when the patch is made. Writing an entry's `+0x18` changes neither.
- **The Macros come after the builder.** VAP's builder leaves the Macro vector empty
  and puts a `std::function` at container `+0x2c` (manager `0x6014a7dd`, invoker
  `0x6014d161`). ITCM code (return address `0x1d945`) makes the 8 Macro entries, then
  calls it with `(the function's own storage, &Macros)`. It fills in the model's Macro
  defaults: VAP's Attack → 19 and 23, Decay/Release → 20, 24, 22, 26, and so on. A
  model with other Macros replaces that `std::function`.
- **Registering a model:** `0x6012fd78(registry, id, &descriptor)` takes the pointer and
  clears it. When the id already exists, it leaves the pointer, and the caller frees the
  descriptor. The builder's `std::function` holds a plain function pointer: manager
  `0x6014495d`, invoker `0x60144955`, called as `builder(patch, container)`. A
  descriptor's `std::function`s use the firmware's managers, and an empty functor's
  manager only answers "functor pointer" (operation 1).
- **A value display returns its text as an `std::string`.** Up to 15 characters fit the
  local buffer, so a display needs no allocation. Stock displays index their name
  tables without bounds checks: Glide Mode with 14 hangs.
- **Model id 3 in stock code:** the instrument title, the synth settings screen's setup
  (`0x6011ae60`), patch save and load (Patches and Workspace), set_model and voice
  creation all handle it. The record's model index is written only by the Model list
  (`0x6011b634`). Applying records reads the index table, and nothing turns a live id
  back into an index.
- **`0x600b175c` resets a record; it doesn't copy the live slot.** It copies the slot's
  default record (flash `0x6003a098` + 0x40 × slot), takes the Macro states from the
  patch's Macros (entry `+0x24` → 0x3f or 0), and sets the name "Init". Section 17
  calls it a copy of the live state, and `names_1.2.0.tsv` names it
  `synth_slot_record_store`.
- **Drawing text:** `eve_cmd_text_q` (`0x6011fa88` → `0x6011fa20`) writes `CMD_TEXT`
  (`0xffffff0c`), `x | y << 16`, `font | options << 16` through the display's vtable
  `+0x18`. It then hands the string to vtable `+0x40` (which returns the bytes written)
  and advances by that plus 12 (`+0x14`). The emulator's fake display needs a `+0x40`
  for text.
- **The list control** (vtable `0x60046a2c`):
  - `+0x24` (`0x60105edd`) takes the data.
  - `+0` (`0x6010652d`) draws it. With image ids, the items are drawn by `0x6010645c`
    (called at `0x60106606`); ids of `0x24` and up draw nothing.
  - An item's image is centred in the column and on the row's centre line.

### Found while building WAVE's LFOs and Edit Patch label (`patch/synths` sy3, emulator)

- **VAP's LFO Ratio display reads another parameter.** Its invoker (`0x6014d008`) runs
  as `(std::string *ret, functor, accessor, float *value)`. The functor holds Sync's
  parameter index, and the accessor's vtable `+0` returns that parameter's value.
  - Sync 0 (or no float back): the rate in Hz, `{:.2f} Hz` below 10, `{:.1f} Hz` from 10.
  - Sync not 0: the value rounded and clamped to 0–15 picks a name from the table at
    `0x60066cb0`: 1, 1/2 D, 1/2, 1/4 D, 1 T, 1/4, 1/8 D, 1/2 T, 1/8, 1/16 D, 1/4 T,
    1/16, 1/8 T, 1/32, 1/16 T, 1/32 T.
- **The Edit Patch screen's model logo** (set up by `0x6011e764`) is a label control
  (`disp_create_label_q`, vtable `0x6004694c`), whose id the screen keeps at `+0x93`:
  - Its data is a list of 6-byte icons: x and y (16-bit each), then the image id at
    `+4`. An id of 0 draws nothing.
  - The icon's id comes from the table `0x600615d8` = `{13, 14, 15, 16, 26}`, indexed
    by the slot's model index (as the Model list's `0x60061488`).
  - The label's text is set to `""` by `disp_ctrl_set_text` at `0x6011ea58`. The label
    draws its text with `CMD_TEXT`, so a hook on that call can give it a name instead
    of the logo.

### Adding parameters to a model (`patch/synths` sy4, emulator)

WAVE adds two parameters after VAP's 60 (the LFOs' Polarity). Everything that handles a
patch's parameters works from the live count, with one constraint: the count must be the
same after every build of a patch.

- **The patch's per-parameter arrays** are sized once, when the patch is made, from the
  count the builder leaves (ITCM, right after the first build at `0x1d776`):
  - the values (`vector<float>`, patch `+0x0c`);
  - `+0x30`: a float per parameter;
  - `+0x3c`: 32 bytes (8 floats) per parameter;
  - `+0x48`: a byte per parameter.

  Loops over the parameters later (`0x16f14`, patch vtable `+0x38`, and the voice
  pushes) take the count from the parameter vector. So a build that left more entries
  than the first would make them write past these arrays.
- **The builder runs more than once.** `0x1d2d0` runs the descriptor's builder, swaps in
  the new Macros and frees the old ones. It runs twice while a patch is made (`0x1d776`,
  `0x1db5e`), and from set_value on an entry whose byte `+0x24` is set (VAP: Sync, 30 and
  34, as the Ratio display depends on it). Loading a patch file sets those too.
- **VAP's builder resizes the vector to its 60 first:**
  - a shorter one grows through `0x60148028(vector, n)` (`_M_default_append`);
  - a longer one shrinks: the extra entries are destroyed (`0x60150362`: the display's
    manager with "destroy", then the name).

  It then fills each entry by index through `0x6014dcb0`, which checks the index against
  the count and also writes VAP's per-parameter set handler. That table, at
  `0x2002d1a4`, has 60 entries of 16 bytes, with a guard word at `+0x3c0`.
  `vap_voice_set_param` indexes the same table, so no index past 59 may reach a VAP
  voice.
- **Growing the vector doubles its room** (libstdc++): 60 entries to 62 allocates 120
  (6.7 KB). When that allocation fails, the append writes the entries from address 0 on,
  as VAP's builder does after a failed allocation. In the 20,000-block stress run (busy
  heap) it failed. WAVE's builder therefore makes a new patch's vector 62 long before
  VAP's builder runs. VAP's then trims it to 60, which keeps the room.
- **set_value** is patch vtable `+0x10` (`0x1d39d`): `(patch, index, float *value,
  source)`. It stores `values[index]` without a bounds check. It rebuilds when the entry's
  byte `+0x24` is set, and then hands the value to the voices (`0x17944`) unless patch
  byte `+0xe0` is set. The loader passes source 2.
- **Loading a patch file** goes through the patch storage's vtable `+0x0c` (`0x60124f39`):
  `(storage, &path, patch, 1)`, which returns 1 when loaded.
  - It is called for a slot's Workspace `<slot>.patch` (`0x600b12d2`) and for
    `Patches/<model>/<name>.patch` (`0x600b0e9c`).
  - The parser (`0x18790`) then calls set_value for the first min(file values, the
    patch's count) values (`0x18324`).
  - Extra values in the file are ignored (stock firmware: 62 values into VAP's 60).
  - The patch's last values keep what they were before the load when the file holds
    fewer.
- **The pages (container `+0x50`) are the Edit Patch screen's** (hardware, `sy4`): its
  tiles (Osc, Filter, Amp Env, Env 1, Env 2, LFO 1, LFO 2, Mod, Voice, then the Macros
  M1-M7) open lists of each page's cells, empty cells skipped. VAP's LFO 1 page
  `{-1, 30, 27, 28, 29}` shows Sync, Ratio, Waveform, Retrigger. (Earlier notes here call
  them the Macro editor's pages.) The tile labels don't come from the pages' names: they
  read Env 1 and Env 2 where WAVE names the pages Mod Envelope and Filter Envelope.
- **An entry's flag bytes:** `+0x21` picks the starting value of the patch's `+0x30`
  array (1.0 when set; `0x16f14`). `+0x22` is "stepped" and `+0x23` the MIDI CC.
  `+0x24` is "rebuild on change".

### Found while building PHASE (`patch/synths` sy8, emulator)

- **Model id 7 is free and works like 3.** WAVE built with id 7 passed every
  `test_synths.py --quick` check. A trace of stock reads of the id found the same 10
  sites with either id, each one comparing, copying or hashing it:
  - in ITCM: the patch file's parse (`0x187d6`) and write (`0x1c006`), and the slot's
    voice setup (`0x1e4a4`–`0x1e4ec`);
  - in flash: `set_model` (`0x600b1450`), the title (`0x600b16de`), load by name
    (`0x60124cb8`) and the registry's find (`0x6012fd42`).
- **The Edit Patch tiles' labels come from keywords in the page names** (this corrects
  the note above). `0x6011d628` (one caller, `bl` at `0x6011e924` in the Edit Patch
  setup, `r0` = the screen, `r1` = 1) walks the selected synth's pages. For each one it
  copies the full name (24-byte buffers at screen `+0x444`) and makes a label of at most
  7 characters (8-byte buffers, their pointers in an array at `[screen + 0x448]`) and an
  icon kind (bytes at `+0x42c`), with the tile count at byte `+0x450`. The keywords,
  tried in this order:

  | Page name contains | Label | Kind |
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
  | anything else | (empty) | 0xff (no icon) |

  So WAVE's "Filter Envelope" reads Env 1: "Envelope" is tried before "Filter". Right
  after the call the setup stores the label pointer array and the kinds into the tiles
  control's data (`+0x130`, `+0x134`). The control (constructor `0x60104828`, vtable
  `0x60046914`) holds a pointer to that data and reads the buffers each time it draws, so
  labels and kinds rewritten after the call are what it shows.
- **A Unicorn memory-write hook over all of RAM** makes the synth rig's boot fault (at
  `0x601665d2`). Narrower write hooks, or read hooks, work.

## 24. MIDI note-off order and legato hook points (emulator)

Found while investigating legato (`docs/LEGATO_ANALYSIS.md`).

### What stock sends

- **Old note-off, then new note-on.** When a track plays a note while its last note
  (a MIDI instrument) is still on, `seq_process_track` first calls
  `seq_note_off_q(seq, t, last record, -4)` at ITCM `0x24da`, then
  `seq_trigger_note(seq, t, work record)` at `0x2fd8`. Both messages go out at the same
  tick, in the same audio block. USB and DIN get the same order; with both ports on,
  each message goes to USB, then DIN.
- **A MIDI note has no length of its own.** It sounds through empty steps until the
  track's next note, an OFF/CUT/FADE step (note values −2/−3/−4, note-off at that
  step's micro-step 1 from `0x296e`), a gate end, a mute or stop.
- **No note-off across tracks.** Two tracks on one channel never end each other's
  notes. Two note-ons of one pitch from two tracks get only one note-off.
- **Micro-move and the instrument's MIDI delay** move the old note-off together with
  the new note-on (both from the same two sites, at the delayed micro-step).
- **Gate `q`:** note-off at the gate end from `0x20ca` (the gate, roll and mute path),
  then a second note-off for the same note at the next note (`0x24da`). The gate length
  is capped at 47 micro-steps: `q` 101–255 behaves like `q` 100.
- **Chance fails:** neither `0x24da` nor `0x2fd8` runs; the old note keeps sounding.
- **Glide `G` (type 19) on a MIDI note is ignored**, for every value 0–255.
- **Stop:** `seq_stop` (`0x600acd90`) calls `seq_track_note_off_q` (`0x600ac5a8`) for
  each of the 16 tracks, which sends the off for the track's last record.
- **Mute** (the project's mute flag set while a note is held): note-off at the track's
  next micro-step 1, from `0x20ca`.

### The functions

- **`seq_note_off_q(seq, t, rec, mode)`** (ITCM `0x11a4`): for instruments 48–66, a
  record with Chord (14) in either FX column (and no Arp, 21) goes to
  `seq_chord_note_off_q` (`0x600ac508`, through the veneer `0x1f698`), otherwise
  `seq_note_off_out_q(note, 0, channel, synth)` through the veneer `0x1f608`. Sample
  instruments on tracks 0–7 go to the voice note-off (`0x5678`). ITCM callers: `0x20ca`,
  `0x24da`, `0x296e`, `0x33f2` and `0x1242` (a loop over the 16 tracks).
- **`seq_trigger_note`** (ITCM `0x149c`) for instruments 48–66: Chord goes to
  `seq_trigger_chord_q` (`0x138c`, note-ons through `0x1438`). Otherwise the velocity is
  the `V` value × 127 / 100, or the instrument's default from `instr_default_velocity_q`
  (`0x600acb50`, through the veneer `0x1f5c8`; byte `+2` of the MIDI instrument record
  at project `+0xaa0 + 0x10·channel`), plus the performance velocity offset (`+0xb4`),
  then a tail call to `seq_note_on_out_q` through the veneer `0x1f788`. ITCM callers:
  `0x22f8`, `0x285c` and `0x2fd8` (step notes and delayed notes).
- **`seq_note_on_out_q(note, velocity, channel, synth)`** (`0x600abb44`) and
  **`seq_note_off_out_q(note, ?, channel, synth)`** (`0x600abbe8`): synth → the synth
  engine; otherwise nothing while rendering, else note + (2 − settings `+0x2f`) × 12,
  clamped to 0–127, to the USB and/or jack port object (vtable `+0x20` note on, `+0x24`
  note off, velocity 0) by settings `+0x2c`. Settings `+0x2f` is the MIDI octave;
  with 0, as in a zeroed emulator, notes go out 24 semitones up.
- **The ITCM veneers' target words are in flash** (ITCM image base `0x6018d6e4`):
  `0x601ace70` (`0x1f788`, note on), `0x601accf0` (`0x1f608`, note off) and
  `0x601ad1e8` (`0x1fb00`, `seq_track_note_off_q`), like `0x601acf00` for
  `seq_advance_step`. A `words` hook there sees every MIDI/synth note on or off, but
  without the track.

### Reordering for legato (emulator scratch proof)

With unicorn hooks in place of two ITCM BL hooks (skip `0x24da` and keep a copy of the
last record when the new note should be legato; at `0x2fdc`, where r0–r3 are dead,
call `seq_note_off_q` with the copy), the new note-on goes out before the old note-off
in every case tried: adjacent steps, after empty and OFF steps, Micro-move, Roll,
Random note, after a gate, channel change, two tracks on one channel, a song-mode
pattern change and stop. A Chance-failed step makes no copy. Random note (`n`) changes
the work note after `0x24da`, so a same-pitch check belongs at `0x2fd8`.

### Synth instruments (emulator)

- **Same order for synths.** For instruments 64–66, `seq_note_off_q` and
  `seq_trigger_note` reach the synth host (`seq_note_off_out_q` / `seq_note_on_out_q`
  with the synth flag set, host vtable `+0x24` / `+0x20`) from the same two sites:
  note-off of the old note, then note-on of the new one, at the same tick. Velocity 100
  when the step has no `V`.
- **Synth legato needs the reverse order.** With Polyphony 1 and Glide Mode 2 (Legato)
  or 3 (Legato O), a note-on that arrives while the voice holds a note doesn't restart
  the envelopes, in ACD, FAT, VAP and WTFM. With "note-off, then note-on", with Glide
  Mode 0 (Always) or 1 (Overlap), or with Polyphony above 1 (the new note takes another
  voice), they restart. Glide Mode is parameter 26 in ACD, 22 in FAT, 35 in VAP and 40
  in WTFM.
- **`G` on a synth step does nothing in stock firmware.** `synth_apply_fx_q` (ITCM
  `0x5070`, `tbh` on type − 1 for types 1–37) handles Off (1), CC a–e (8–12), delay
  send, panning and reverb send (30–32) and CC f (37); type 19 takes the default. No
  host message and no voice parameter change in the emulator.
- **PERC ignores note-offs.** Its voice's MIDI method (vtable `0x60066f90` `+0x08`,
  `0x60152598`) acts only on message type 4 (note on), through `perc_voice_trigger_q`
  (ITCM `0x108a4`, `(core, &{44100.0, tempo}, note)`, velocity scaled in s0). Its `+0x18`, `+0x1c` and `+0x20` (sounding,
  holds this note, holds any note) always return 1. The note picks the element: 60–61
  kick, 62–63 snare, 64–65 closed and 66–67 open hihat, 68–69 cymbal, 70–71 perc, any
  other note a tom at that pitch (notes above 71 an octave lower). The parameters
  (84) are the six elements' (kick 0–8, tom 9–17, snare 18–27, hihat 28–35, cymbal 36–44,
  perc 45–48), then a level, panning, delay, reverb and output per element (49–78), a
  waveshaper and a 3-band EQ.

## 25. Pattern files, the play buffer and copying a pattern (`patch/copypattern`, emulator)

Details and the full design: [COPY_PATTERN_PLAN.md](COPY_PATTERN_PLAN.md).

- **The editor edits the play buffer** `*(seq+0x14)` in place; it is the only RAM copy
  of the current pattern, unsaved edits included. A pattern nobody edited has no
  `Workspace/patterns/pattern_NN.mtp` (N = 1..255, `%02d`).
- `pattern_write_file(fm, path, data)` (`0x600c1fb4`): remove, `pattern_set_header`
  (`0x600ae98c`: `{0x0002534b, 0x01020901, 5,5,5,5, u16 0x3030}`), CRC of 0x302c bytes
  into `data+0x302c` through the object at `*(fm+0x1104)` (vtable +0: `(obj, data,
  len)`; set up at boot, not by the static constructors), open mode 3, one 0x3030-byte
  write, close. Returns 1 on success.
- `pattern_read_file` (`0x600c2350`) doesn't check the CRC; `0x600c2284` upgrades file
  versions 1-4 (byte +8) and other sizes (+0xc) on load. `pattern_init` (`0x600ae9b0`):
  every track 32 steps, notes -1, instrument and FX types 0. `step_clear` (`0x600ae940`)
  clears note, instrument and both FX types (values stay).
- Unsaved changes: `fm+0x469` (`fm_mark_unsaved`, `0x600bcd30`), `project+0x6f4` bit 0
  (kept in `project.mt`), and the per-pattern `fm+0x4ca+n` (the play buffer must be
  saved as pattern n). `0x600bcc58` ORs them all (and per-instrument flags at
  `fm+0x46a`, `fm+0x49a`).
- File manager busy: `fm+0x13d` (state) and `+0x13f`. The stock check `0x600bc990`
  allows a new operation when the state is 0 or 0x0c and `(+0x13f & 0xfd) == 0`.
- The main loop's file manager step `0x600bca88` runs the operation in `+0x13f` (1 to
  0x16, a jump table) one state at a time. Operation 3 (`0x60114a04`) copies a project
  into Workspace; operation 4 (`0x60114ff8`) saves Workspace to the project folder;
  operation 1 (`0x601166d4`) loads Workspace into RAM.
- **A new project is named "New Project" with an empty path** (`0x60117c8c`, called
  from the project screen and at boot when Workspace can't be loaded; run in the
  emulator). So "no name yet" is the empty path, not an empty name. The mods that keep
  a per-project file test the name, so on a real new project their "keep the settings
  for the first save" path never runs, and the first Save As counts as another
  project. Shown for slicethru bt3 in the emulator: two Thru flags set on "New
  Project", 0 after Save As, no file written. Its test 4.3 had modelled the new project
  with an empty name. synths is the exception: it keeps its choices for any project
  without its file, so they survive.
- **Open** (`0x6011483c`, through `0x600bc780`): writes the new name and path at once,
  then state 9, operation 3. When the copy ends (`0x601147a8`), `0x60116430` starts
  operation 1, which clears the name and path and later restores them from
  `project.mt` (`0x60116400`), all while busy. **Save** (`0x60114e34`, through
  `0x600bc8c8` from the project screen's Save As `0x600e9a78`, Save `0x600ea6bc` and
  overwrite `0x600eaa58`): with a path and name it is Save As (the path, then the name
  copied by `bl strncpy` at `0x60114eaa`, which only Save As runs; slicethru bt4 hooks
  it); with neither it needs a current name. Then state 0x0d with operation 4, or
  operation 2 (`0x60116820`, the synths' Workspace data first) when `0x600bc758` starts
  one; run in the emulator, Save As on a new project returned 1 with state 0x0d,
  operation 2. It creates the project's `patterns`, `instruments` and `samples`
  folders and copies files into them; no sign of it deleting the folder's other files.
- **Save As doesn't carry the mods' files** (hardware, 2026-10-25): the new folder had
  only the firmware's files and `synths.bin`. The folder saved from also had
  `colorsong.bin`, `humanize.bin`, `interpolate.bin`, `masterfx.bin`, `swing.bin` and
  `slicethru.bin`. The new project had no Beat Slice Thru instruments (bt3); a plain
  Save and a reopen keep them. In the emulator the same happens: Save As writes the new
  name and path at once, slicethru takes it for another project, clears the flags and
  finds no file. bt4 marks the Save As at its name copy and keeps the flags for the new
  name; on hardware (bt4 and MT4), Save As and a new project's first save keep Beat
  Slice Thru.
  swing, humanize, interpolate and masterfx had the same bug: their files were left in
  the old folder, and a new project's settings were lost on its first save. swing sw2,
  humanize hz9, interpolate ip6 and masterfx mf5 (all: MTS) chain the same name copy,
  keep their values for the new name and write their file to the new folder; they read
  and write only while the file manager is idle. On hardware (2026-10-26), Save As and a
  new project's first save left every mod's file in the new folder, and reopening the
  projects loaded the settings. colorsong is still not carried (cosmetic: its colors
  are worked out again). synths keeps its choices; Save As over an existing project
  used to leave that folder's `synths.bin` in effect. synths sy9 (2026-10-26) moves its
  choices to the new name at the Save As and writes them over that file, so the saved
  project's choices win (emulator and hardware; SAVE_AS_FIX_PLAN.md section 8).
  The buffer swap (`0x600acc68`) sets the current pattern from `fm+0x130` (the last
  pattern `pattern_load_now` loaded, `0x600c2510`).
- Song screen file operations work on pattern files, not RAM: `0x600c3a8c` and
  `0x600c3b58` read patterns into SDRAM `0x83f5cf6c` / `0x83f5ff9c`, copy or clear
  tracks, update the masks and write the file, with a 10-slot undo of whole files
  (`0x600c3e30` push, `0x600c3eac` pop; ring `0x83f62fec`, state `0x2001f614`).
- Message bar: `show_message(0x20020880, 0, text, time)` (`0x600cdf28`; time 0 = the
  default) shows one line ("No undo data :(") and keeps the text pointer; two lines:
  `0x600cdf44`. It needs boot-time objects the emulator doesn't build.
- Page 3 of the Pattern screen's bottom bar binds and labels each button with its own
  call (binds `0x600e29ba`, `0x600e29c6`; text `0x600e586c`..`0x600e58c0`, second
  lines `0x600e58cc`..`0x600e5920`, one call per button; colors and data only for
  buttons 0, 1 and 7), so two patches can each add a button without sharing a site:
  humanize takes button 2; copypattern takes button 2 when the call at `0x600e5884`
  still goes to the stock `disp_ctrl_set_text`, and button 3 otherwise.

## 26. The bootloader doesn't erase a sector whose data stop in its first page (hardware)

Found when `TrackerPlus_cp1.ptf` (copypattern alone) boot-looped while the same code
worked inside a bundle. Its image ended at `0x601bb0d5`: only `0xd5` bytes into the
4 KB flash sector `0x601bb000`, all inside that sector's first 256-byte page. On boot
the version text (in those last bytes) showed as just "v", and the hooks jumped
through chain words stored there, so the unit reset in a loop.

- **Test:** `TrackerPlus_cpV.ptf`, a pass-through build (every hook only calls the
  original) with all code before `0x601bb000` and only its version text in page 0 of
  that sector (image end `0x601bb01a`). It booted, and the version text came out
  garbled. The text was plain `1.2.0-cpV` (no `%` conversions), so stale bytes couldn't
  make sprintf read memory.
- **Every image that worked** ends either inside the stock image's last sector
  (`0x601ba000`), or at least one full page into a new sector: versiontest (`MT0`,
  `0x601bb10d`) and ramdiag (`RD2`, `0x601bb191`) end in page 1, the others further in.
- **What it means:** the bootloader programs 256-byte pages and erases a sector only on
  the way through a full page; a last, partial first page is programmed without an
  erase, so it comes out ANDed with what an earlier firmware left in that sector (flash
  programming only clears bits). The exact rule inside the bootloader is unknown; the
  observed boundary is "ends in page 0: broken, ends in page 1 or later: fine".
- **Fix:** `tools/patch_ptf.py` and `web/patcher.js` pad an image that would end within
  the first 256 bytes of a sector with `0xFF` up to 16 bytes into the second page (like
  `MT0` and `RD2`). `tools/test_layout.py` checks every output. No hardware-tested build
  ended in a first page, so their outputs are unchanged.

### VAP's envelopes, measured (`patch/synths` sy5, emulator; the user's hardware check)

Stock VAP, Cutoff 150 Hz, Resonance 0, Env Amt 100 %, a note held 2.3 s. Each envelope in
turn gets a 2 s attack (Sustain 1), the others an instant one:

| Slow attack on | Brightness (spectral centroid), early → late | Level (RMS), early → late |
|---|---|---|
| 15–18 | 2351 → 2329 Hz | 15237 → 15215 |
| 19–22 | 2237 → 2329 Hz | 1571 → 15215 |
| 23–26 | 522 → 2329 Hz | 12046 → 15215 |

- **19–22 is the amp envelope, 23–26 the filter envelope, and 15–18 a free envelope** that
  drives neither on its own (a Mod Matrix source). VAP's own Macros agree: Attack moves
  19 and 23, Decay/Release 20, 24, 22 and 26.
- On the Edit Patch screen, VAP's Env 1 tile (page "Envelope 1") holds 23–26, its filter
  envelope, and Env 2 holds 15–18. The user checked this on hardware.
- The MIDI CCs (entry byte `+0x23`): Cutoff 74, Resonance 71, Env Amt 77, 15–18 CCs 46–49,
  19–22 CCs 75, 72, 76, 73, and 23–26 CCs 80–83.
- WAVE (`patch/synths`) took 15–18 as its filter envelope and 23–26 as its mod envelope,
  from the earlier inference. Its pages and tabs now show 15–18 on Env 1 and 23–26 on
  Env 2, so the filter envelope sits where VAP's does; the slots and saved patches are
  unchanged. Its filter envelope answers CCs 46–49, not VAP's 80–83.

### A per-slot hook point and room for WAVE's effects (`patch/synths` sy6, sy7, emulator)

- **Voice 0's `+0x14`** is called once per block, after every voice has rendered, as
  `(voice, ctx, L, R)` with the slot's output buffers (`synth_slot_render`, ITCM
  `0x1f24e`; not called when the word is the ITCM no-op `0x1dc55`). With one voice, the
  voice renders straight into them. With more, the slot first clears both, renders each
  voice into its scratch (`+0x17c` / `+0x37c`) and adds both scratch buffers in, R too
  (a VAP voice leaves its scratch R as it was). Either way `+0x14` gets the slot's final
  L and R. It is the place for an effect on a slot's summed output. VAP's does nothing.
- **The output channels** (patch container `+0x28`) are read by the synth output object's
  update (`0x60036634`) every block, not kept from setup: with 1, L is scaled into both of
  the slot's 16-bit outputs (indices slot and slot + 3); with 2, L and R are scaled each
  into its own, and the sends get (L + R) / 2. With R a copy of L, 2 gives the same
  outputs as 1. A patch whose builder sets 2 (PERC's; WAVE's from `sy7`) is stereo.
- **Room:** in the all bundle (`1.2.0-MS6`), `dtcm_above_heap` keeps 16 bytes after synths
  and `ocram_tail` is full. `sdram_top` has about 25 KB free. Whether SDRAM is ready before
  `main` is still unchecked: the startup copy and zero tables list SDRAM sections, but empty,
  and no early start-up code sets up the SDRAM controller (SEMC, `0x402f0000`, referenced
  only from `0x60093c60` and `0x600989a0`).
- **VAP's parameter 38** (Mix Env Amt, −2..2, no CC, no rebuild flag) is 0 in every WAVE
  patch; WAVE uses it for Drive.
- **WAVE's effect (`sy7`)** uses all three: voice 0's `+0x14` (VAP voice vtable word
  `0x600667f8`, `0x6014a811`), 2 output channels, and its state (6,288 bytes) in a second
  RAM block the patcher places after every patch's own ("late_ram"): on-chip with synths
  alone, `sdram_top` in the all bundle. It touches the block only after the Pattern
  screen's first update, so SDRAM's readiness before `main` doesn't matter to it.

## 27. Sample play modes and Beat Slice (emulator and static)

Found while planning Beat Slice (Play Through), `docs/BEAT_SLICE_THROUGH_PLAN.md`.
`ins(i)` = instrument i (0..47) at `0x20200000 + 0x174·i`.

### The instrument struct and the `.pti`

- Tracker+ keeps the original Tracker's 0x174-byte instrument layout:
  - `+0` active;
  - `+4` sample type;
  - `+0x28` sample address;
  - `+0x2c` length in frames;
  - `+0x3c` play mode;
  - `+0x3e`/`+0x40`/`+0x42`/`+0x44` start, loop 1, loop 2 and end (`u16`,
    0..65535 of the sample);
  - `+0x108` 48 slice starts (`u16`), `+0x168` slice count, `+0x169` selected slice.
- Bytes the original Tracker left free are used here: `+0x173` is a pitch offset added
  to the tune (`0x60100b50`; set to `0xf4` by a loader at `0x60115af0`), and `+0x102`
  is read as a `u16` into `voice+0x80`.
- **`.pti` files.** `0x600c19d8` writes a 16-byte header (two magic words,
  `0x09090909`, `u16 0x174`), the struct as it is in RAM, and a CRC at `+0x184`: 0x188
  bytes, then the sample. `0x600c1990` copies 0x174 bytes back, after upgrading
  header versions below 9 (`0x600c5a48`). Copying an instrument (`0x60113c80`) copies
  the whole struct too.

### Play modes

- 0 1-Shot, 1 Forward Loop, 2 Backward Loop, 3 Pingpong Loop, 4 Slice, 5 Beat Slice,
  6 Wavetable, 7 Granular.
- The names: 15-byte slots at `0x60042618`. Their one reader is the screen's start
  method `0x600f57f0` (literal `0x600f5b24`), which puts 8 pointers at `scr+0xb70`
  (`scr` = `0x200254b4`).
- **Note-on:** `0x60030d50(voice, instrument, note)` starts the amp envelope and calls
  the dispatcher `0x60032abc`, a switch on `ins+0x3c`:
  - 0 → `0x60035310`;
  - 1–3 → `0x60034888` (2 and 3 through veneers `0x60034358`, `0x60034dd8`);
  - 4 → `0x600357c4`;
  - 5 → `0x60032b50` (one call site, `0x60032b3a`);
  - 6 → `0x60035904`;
  - 7 → `0x60032d40`;
  - any other value returns 0 without touching the voice (emulator, mode 8).

  The other callers: the restart after a fade (`0x600313a2`) and the preview
  `0x60100ed4`.
- **Latching** (`0x60100b50`, called by each note-on):
  - the mode into `voice+0x21`;
  - `ins+0x2c` into `voice+0x68`;
  - the sample address into `voice+0x24`, from `0x20204dc4 + 8·i + 4·channel`
    (`voice+0x28` = channel).
- **Render:** the voice update `0x600327e4` switches on `voice+0x21` (bounded at 7;
  higher values give no block):
  - 0, 4 and 5 → `0x600357b4` (1-shot: forward `0x600353fc`, reverse `0x600355d4`, by
    `voice+0xb0`);
  - 1 → `0x60034dc8`, 2 → `0x60034878`, 3 → `0x60035300`;
  - 6 → `0x60035a2c`;
  - 7 → `0x60034318`, by grain loop type `voice+0x9c`.

  Each per-mode entry picks forward or reverse, so these render functions have no
  direct references.
- **Voice fields set by a 1-shot/slice note-on:**

  | Field | Value |
  |---|---|
  | `+0x34`, `+0x38` | start and end frames |
  | `+0x50` | frames to play |
  | `+0x18` | sample address + 2·start |
  | `+0x1c` | length − start |
  | `+0x7c` | reverse start |
  | `+0x20` | 1, playing |

  The render stops at `+0x50` frames and clears `+0x20`. A right-channel voice with
  `voice+0x118` set copies `+0x34`/`+0x38` from its partner. Frames come from
  `0x60100694(point, length, address)`.
- **Players:** `0x2001a138 + 0x848·t`, sample voices in the vector at
  `player+0x73c`..`+0x740`.

### Beat Slice (emulator, `0x60032b50`)

- **Slice choice:** note n → slice n − 24, clamped to 0..count − 1 (notes below 24 →
  slice 0). With the Slice FX set (`voice+0xae`), the slice is `voice+0xc4`. No slices:
  start 0, end `0xffff`.
- **End:** the next slice's start, or `0xffff` (sample end, not the instrument's End
  point) for the last slice. The end point goes through `bl 0x60100694` at
  `0x60032c62` (r0 = the point).
- **Measured:** 4 equal slices of 44,100 frames give 11,025 frames per slice. With r0
  forced to `0xffff` at `0x60032c62`, notes 24, 25 and 27 play 44,100, 33,075 and 11,025
  frames to the sample end. Reverse and the Slice FX work from the new end. The render
  costs 4,916 instructions per block either way.
- **Pitch:** fixed at note 60 (`*(voice+0x23) = 60`; `0x601001bc` tests
  `voice+0x21 == 5` for the same reason).
- **Note-off** (ITCM `0x5678`):
  - −3 cut (`0x60030f9c` + `0x60031040`);
  - −2 fade (`0x60030c80`);
  - otherwise `0x54b4` → `0x60030f9c`: the amp envelope's release, or an immediate stop
    with the envelope off (`voice+0xee`, read from the code).
  - A new note on a busy voice fades it first (envelope state 7), then starts the
    pending note.
- **Code that tests "mode == 5" or "mode 4..5" directly** (static scan of
  `ldrb [r, #0x3c]` + compare, flash and ITCM, mirrored ITCM image excluded): 31 sites
  in 26 functions. Among them:
  - pads and live recording (`0x600c6a4c`, `0x600c6b1c`, `0x600c6bd8`, `0x600c72ec`,
    with pad → note `0x6010eea8`);
  - pad lights per slice (`0x600d3f08`, `0x600e1310`);
  - the Sample Playback screen's functions `0x600f3dd4`–`0x600f7c54`.

  The voice's copy `voice+0x21` is tested at 7 more places.

### A 1-shot voice's end, and looping it (emulator and static)

Found while planning the Beat Slice Thru Loop, `docs/SLICETHRU_LOOP_PLAN.md`.

- **The end in the forward 1-shot render** (`0x600353fc`):
  - Per frame, the render advances the position `voice+0x7c`, counted from the start
    frame `+0x34`.
  - When the position reaches the frames to play (`+0x50`, in `r1`), the 4 bytes at
    `0x60035522` (`ldr r3, [r4, #0x1c]; str r3, [r4, #0x7c]`, `e369e367`) set it to
    `+0x1c`, so the next frame is silent and `+0x20` is cleared.
  - Those bytes run only then: 0 times in 60 blocks of a voice short of its end. No
    branch lands on them (`0x60034000`..`0x60037000`), and `lr` is free there.
- **Forcing a wrap there** (emulator, a Unicorn hook on a bt3 Beat Slice Thru voice):
  setting `r3` and `+0x7c` to position − (`+0x50` + `+0x34`), then going on at
  `0x60035526`, loops the whole sample from frame 0.
  - Sample-exact for three passes from slices 1, 2 and 4 of 4.
  - Also at pitch 2× and 3×, where the overshoot carries over.
  - Stereo partners stay in step.
  - The indexed read is signed, so a position below the start frame is fine.
- **The block buffer:** the render returns its block from `0x60030478` (samples at
  `+4`); `0x60030524` releases it. The voice update `0x600327e4` doesn't return the
  block.
- **What ends a looping voice** (emulator, the real voice note-off ITCM `0x5678`
  on player 0's voice `0x2001a258`):
  - Every case ends it: OFF (`0x54b4`), CUT (−3, ITCM `0x5420`) and FADE (−2), with
    the amp envelope on and off (`voice+0xee`).
  - With the envelope on, OFF and FADE release, and the voice stops when the release
    ends.
  - CUT runs, for each of the player's voices, `0x60030f9c` (release), `0x60031040`
    (envelope reset), the five objects at `player+0x77c` (`0x6010d530`, `0x6010d59c`),
    then `0x601000b0`, which clears `+0x20`.
  - Stop sends each track's note-off down the same path as OFF (above).
- **The instrument is in the voice:** every play mode's note-on writes the
  instrument index to `voice+0x6f` (the Beat Slice note-on at `0x60032b90`; emulator,
  modes 0–7, instruments 3, 17 and 47). `0x60100b48` sets it, and `0x60100b0c` reads
  `ins(+0x6f)+0x168`.
- **The playhead:** `0x60100ab0(voice)` returns (`+0x34` + `+0x7c`) / `+0x68` × 65535
  (caller `0x600b4036`). After a wrap, that is the true frame from 0 (emulator: 50776
  just before the wrap, 2018 after).
- **Page 2 of the slice buttons** (`0x600f7c54` with `scr+0xf0` ≠ 0, Slice and Beat
  Slice; emulator): Slice/Auto, Slice/Evenly, No of Slices (`8`), blank, blank, More.
  - The 4th and 5th labels are set to "" (`0x60038660`) by `disp_ctrl_set_text` at
    `0x600f820a` and `0x600f8216`. The second lines are set by `disp_ctrl_set_ptr38`
    (`0x60101d74`).
  - This page also moves the focus `scr+0xd6` to 2 (No of Slices), so the Play Mode
    list isn't focused there.
- **The screen buttons' callbacks** (registered by `0x600f5018`, words in its pool):

  | Button | Callback | Pool word |
  |---|---|---|
  | 1st | `0x600f5761` | `0x600f514c` |
  | 2nd | `0x600f5751` | `0x600f5150` |
  | 3rd | `0x600f4c89` | `0x600f5154` |
  | 4th | `0x600f4b55` | `0x600f5158` |
  | 5th | `0x600f4e15` | `0x600f515c` |
  | 6th | `0x600f3dd5` | `0x600f5160` |
  | 7th, 8th | `0x600f5e1d` | `0x600f5164` |

  - Each callback takes the button state (1 press, 0 release).
  - On page 2 of Slice and Beat Slice, the 4th and 5th callbacks change no RAM but
    the stack (emulator, press and release).
- **A mode step reruns `0x600f7c54` and returns to page 1** (`scr+0xf0` = 0), from
  either page, in stock and in bt3 (emulator: +1, −1, buttons 7 and 8, the encoder).
- **A button's two lines** are fields of its display control
  (`*(0x20027d64 + 0x10 + 4·id)`): the first line at `+0x10` (`disp_ctrl_set_text`
  `0x60101d5c` stores the pointer through the control's vtable `+0x18`); the second
  line at `+0x38` (`0x60101d74` → `0x601049b4`).
  - `0x600f7c54` clears every button's second line first (the call from `0x600f7d62`),
    then labels the mode's buttons. So a second line one page sets never stays on
    another page or mode (emulator: modes 0, 1, 4–7, both pages).
  - Stock leaves the 4th button's second line empty (`0x60038660`) on both pages of
    Slice and Beat Slice. Its first line on page 1 of Beat Slice is "Remove".
- **The 4th button's callback** (`0x600f4b55`) passes a state of 0, 1 or 0xff to
  `0x600f4a1c`, which, in Slice and Beat Slice, acts only on a press on page 1
  (`0x600f4b1a`).
- **A pad preview** goes through `0x600c6a4c(pad, state)`, which is called from
  `0x600d477e`. A press starts the note with `0x600c73c8(0x20017274, 20, note, −1, pad)`
  on player 0 (two voices), with the note from `0x6010eea8` in Beat Slice. A release
  sends `0x600ac700(0x20017274, 20, note, 0, pad)` in every play mode. Emulator
  (slicethru bt5, Loop on): the preview loops while the pad is held and stops on the
  release. The rig needs `0x6010d530`, `0x6010d544` and `0x6010d59c` stubbed, because
  the `player+0x77c` objects aren't built.

### The slice editor (a slicer object; the instrument is the second argument)

| Function | Address | What |
|---|---|---|
| Add | `0x6010f000` | after the selected slice, half-way to the next start or `0xffff`; at most 48 |
| Remove | `0x6010f094` | the selected slice |
| Adjust | `0x6010f100` | bounded by the neighbours |
| Auto | `0x6010f1b8` | clears the table, then transient detection `0x600aff4c` |
| Evenly | `0x6010f1f0` | n starts at i × 65535 / n |
| Confirmation | `0x600f76e8` | "rewrite the current slices" |

### The Sample Playback screen (`scr` = `0x200254b4`)

- **Vtable** at `0x60042690`: update `0x600f6071` at word `0x60042698` (the screen's
  periodic refresh: dirty flags `scr+0xd7`..`+0xdd`), start `0x600f57f0`,
  create-controls `0x600f8390`. In the emulator, create-controls and start run on a
  Beat Slice instrument, and the update runs after them. The start's last call,
  `0x600f5508` (pad lights), needs the pad LED object and is stubbed in the rig.
- **The Play Mode list** is a text list control (0x48 bytes, constructor `0x60105a3c`,
  vtable `0x600469bc`: draw `0x60105140`, set-value `+0x1c` = `0x6010510c`, set-data
  `+0x24` = `0x60105054`). It sits at (601, 29), 197 × 394 px, with 27-pixel rows, text
  at x = 614 and the first row at y = 46 (emulator). Its data block `scr+0xb58`
  (`0x600f7c54`), passed by `bl disp_ctrl_set_data` at `0x600f7cac`:

  | Offset | Field | Play Mode list |
  |---|---|---|
  | `+0` | `i16` selected row (set-data copies it into the control's target `+0x34`; the draw moves `+0` towards the target, 27 px at a time) | the mode |
  | `+2` | `u16` item count | 8 |
  | `+4` | `u16` visible rows (the scissor height is rows × 27; a scroll bar only when rows < count) | 9 (8 when created) |
  | `+8` | per-item colour flags, or 0 | 0 |
  | `+0x10`, `+0x12` | marquee width (0: no marquee), text option | 0, 0 |
  | `+0x14` | `char **` the names | `scr+0xb70` |

  - Set-value clamps to 0..count − 1. The handler's
    `disp_ctrl_set_value(list, mode + ins+4)` (7 for Wavetable) is overridden by the
    set-data in `0x600f7c54` right after it, so stock always shows the mode's row with
    no animation (emulator: data `+0`, target and drawn row stay equal after every
    step).
  - With the data block rewritten at `0x600f7cac` (count 9, a 9-name table, `+0` = 6),
    the list draws nine rows (y 46..262) with the highlight on the seventh and no scroll
    bar (emulator, simulated with a Unicorn hook).
  - **Text widths:** the five UI fonts are run-length coded in flash. `0x601018e8`
    decodes them at boot (`0x6011ff44`, pairs of value and count) from the tables at
    `0x60043100` and `0x60043128` (two sets of 5 {address, length}), and keeps the
    0x94-byte EVE metric blocks (128 widths first) at `0x83fcd374`, 0xac bytes apart.
    `0x601024b4(font, text, n)` sums the widths. Without a scroll bar the list has 178
    px for text. "Pingpong Loop" takes at most 130 px, "Beat Slice Thru" at most 150
    (the 22-pixel fonts).
- **The only UI writer of `ins+0x3c`:** `0x600f5b98(step)`, clamped to 0..7. Its
  callers, all with the list focused (`scr+0xd6 == 6`, `scr+0x70 == 0`):
  - the encoder callback `0x600c92e0` (tail call `0x600c9322`);
  - +1 `0x600c9d84` (`0x600c9dc6`);
  - −1 `0x600ca7a4` (`0x600ca7e6`);
  - buttons 7/8 `0x600f5e1c` (`bl` at `0x600f5e8a` −1, `0x600f5e80` +1): argument 6
    (7th button) steps −1, 7 (8th) steps +1, and a press with the list not focused
    only focuses it.

  The encoder's step is clamped to ±4 (emulator: −4 from Granular gives Pingpong
  Loop, −9 from there gives 1-Shot). Mode 6 sets the sample type `ins+4` to 1, and
  any other mode sets it to 0. A step of 0 on a Beat Slice instrument leaves the
  instrument byte-identical (emulator), and so does 5 → 6 → 5 for an instrument with
  no wavetable window set.
- **The callbacks' pointers** are in the literal pool of `0x600f5018` (`0x600f512c`,
  21 entries): `0x600f5140` −1, `0x600f5144` +1, `0x600f5164` buttons, `0x600f5178`
  encoder. Those are their only references.

## 28. The Sample Recorder: why the engine is silent, the record path (emulator and static)

The plan built on these facts is [LIVE_RESAMPLING_PLAN.md](LIVE_RESAMPLING_PLAN.md).
`E` = the audio engine object `0x20017b08`, `seq` = `0x20017274`.

- **The screen object** is `0x20026100` (pointer at `0x200006b0`), vtable `0x60042c4c`:
  update `0x600fafe8` (word `0x60042c54`), start `0x600fa228`, leave `0x600f8b74`.
  Fields: `+0x76` mode (0 record setup, 1 crop/edit, 2 name/save), `+0x78` source
  (0 Line In, 1 Radio, 2 Mic LG, 3 Mic HG, 4 USB (q)), `+0x7e` Monitor (0 = on),
  `+0xb4d` recording or counting in, `+0xbf8`/`+0xbf9` count-in done/pending, `+0xbba`
  the "stop the pattern?" pop-up, `+0xbec` codec object, `+0xbf0` radio object.
- **Not a mute.** The output chain and codec keep running during a recording. Entering
  the recorder:
  - `0x600b39cc(E)` sets 48 per-instrument "blocked" bytes at `0x2001e3a8`..`d7`;
    `voice_note_on_q` (ITCM `0x5c04`) plays only an instrument that is loaded
    (`ins+0` == 1) and not blocked. `0x600b39e4(E)` clears them.
  - `0x600ad600(seq, v)` sets `seq+0x7dd`. `seq_trigger_note` drops synth note-ons
    (instruments 64..66) while it is 1 (`ldrb` at ITCM `0x14b0`). MIDI instruments
    (48..63) take another branch with no such test, so MIDI still plays.
  - Also: `seq+0x34` = 4 (`0x600ad418`); the count-in callback `seq+0x6fc` =
    `0x600f8605`; delay bypass on (`0x600b2dd8`, `(E+0x2620)+0x2d` = 1); delay/reverb
    sends to 0 (`0x600b4058`, `0x600b40c0`); 11 project mix bytes saved to `E+0x29..`
    and set to `0x56` (`0x600b3a88`: the mix levels, below); line-in passthrough muted
    (`0x600b3440`: bus input-3 gain `+0x24` and the line-in send gains `+0x38`/`+0x3c`,
    saved; `0x600b3484` restores); limiter side-chain and selectors to 0
    (`0x600b36dc(E, 0)`); Monitor level (`0x600b30ec(E, level)`: the bus input-3 gain
    from the table at `0x6003a690`, level `0x56` for Monitor on), so **monitoring goes
    through the master bus**. The codec volume is muted for 500 ms (`0x600fa1ec`).
  - Leaving (`0x600f8b74`) undoes all of it and clears `seq+0x34` and `seq+0x7dd`.
  - Emulator: synth peak at the master 21406 normally, 0 after the stock entry, 17866
    with only the two gate calls undone (delay/reverb still bypassed). Sample voice 7998
    / 0 / 7998. MIDI note-ons sent in all three.
- **Why: the recording overwrites the samples.** `0x600f9074` (start recording) calls
  `0x6010cb90(rec 0x20027f28, buf, size, ch, ch, flag)` at `0x600f90d2` with buf
  `0x8026a000` (literal `0x600f9120`) and size `0x3c00000` (`mov.w` at `0x600f909e`).
  That is the sample bank: base `0x8026a000` (`*0x200006ac`), size at `0x20204f44`,
  bytes used at `0x20204f48`, instrument sample address `ins+0x28`. The loaders add
  each sample's frames × 2 to the bytes used (`0x600c0c62`, `0x600c183c`) and check
  room as size − used (`0x600c17e0`). Clearing the project sets size `0x3c00000` and
  used 0 (`0x600c2a66`..`7a`). So the bank's free space starts at base + used. `0x6010cb90` keeps
  `size / (2·ch)` frames per channel at `0x20027f24`. Every other reader gets the
  channel pointers from `0x6010ca68(rec, ch)` (length `0x6010ca8c`); among the
  recorder's functions only `0x600f9074` loads the base address. That includes the
  recorder screen's own code and the save of a recording (`0x601150e0`: length, both
  channel pointers, then `0x600c1b50`). For a mono recording `0x6010ca68` returns
  channel 0 for both indexes. Channel k starts at buf + 2·k·frames. How stock gets the
  overwritten samples back isn't traced (candidate: the job `0x600bc894` → `0x60115298`).
- **The record path.** After `0x6010cb90`, `0x6010c890(rec)` (BL at `0x600f90d8`)
  starts the record queues and sets `rec+0x2c` (recording). The copy loop `0x6010c5a8`
  moves blocks into SDRAM **in the audio interrupt**: `engine_block_update` (ITCM
  `0x41c8`) calls it first, through the thunk `0x1f548` → `0x6010c938` (copies only
  while `rec+0x2c`). Stop is `0x6010c944`: it clears both queues' `+0x46`, copies what
  they still hold, clears `rec+0x2c` and sets the line-in channel mode back
  (`0x6003662c`, settings `+0xd4`). Its callers are `0x600faf74` (the recorder) and
  `0x60110760`. Nothing is written to the SD card while recording. Queue sources: sources 0..3 the input queues
  `E+0x108`/`E+0x150` (accessor `0x60111884`, BLs at `0x6010c916`/`0x6010c926`), USB
  `E+0x1e8`/`E+0x230` (`0x60111890`). No source records the internal output; the master
  queues `E+0x1e58`/`E+0x2248` (render/export) carry it (emulator).
- **The record queue class** (`0x600364a1`): a 10-slot ring at `+0x18` (9 usable),
  `+0x44` head, `+0x45` tail, `+0x46` active, drops a block when full. Begin
  `0x60036504`, available `0x600364ec`, read `0x60036548`, free `0x6003657c`.
- **Rec** (button `0x1c`): handler `0x600d0620`, bound by `0x600f8e3c` from the pool
  word `0x600f9000`. In mode 0 (or mode 1 while recording) it calls `0x600fb3cc`. In
  mode 0 with Pre Roll on (`0x600ad400`: settings `+0xe7` == 1) and the source not Radio:
  `+0xbf8` = 0x100, `+0xb4d` = 1, count-in `0x600ac2c0`. Otherwise `0x600f9074` at once.
  No Shift test.
- **The count-in** runs in `seq_tick` while `+0xb4d` is set. At its end it calls
  `seq+0x6fc` (`0x600f8605`, sets `+0xbf8`) and doesn't start the pattern (the Pattern
  screen's count-in, `seq+0x6f8`, is the one that calls `seq_play_from`). The update
  sees `+0xbf9 && +0xbf8` and calls `0x600f9074` (BL at `0x600fb1fa`).
- **Play** (button `0x17`): handler `0x600d07b4`, bound by the recorder's start from
  the pool word `0x600fa4ac`: playing → `seq_stop`; else with `0x1d` (Shift, q) held →
  continue `0x600acd38`; else `0x600ac5f8(seq)` (song mode off, `seq_play_from`).
  Both are bound with `btn_bind(.., flag 1, ..)`. The dispatch (`0x600cf7c0`) calls a
  kind-1 handler only when the button's state equals that flag, so 1 = press only.
  `0x1d` is Shift: the Play handler's held-`0x1d` branch is the manual's Shift+Play.
- **While recording, `engine_block_update` stops after the copy.** After the copy it
  asks the recorder "recording?" (`bl` at ITCM `0x41d8` → thunk `0x1f540` →
  `0x6010c888`, which returns `rec+0x2c`) and returns at once if so. That skips:
  - the tempo average;
  - `voice_block_update_q` for the 8 players: envelopes, LFO FX, automation, and the
    re-applying of the sends (below);
  - `synth_fx_apply_pending_q` (`0x416c`).

  The thunk's literal is ITCM `0x1f544`, loaded at boot from flash `0x601acc28`
  (value `0x6010c889`). `0x41d8` is the thunk's only caller.
- **What the recorder's entry changes in the sound, and how to undo each:**
  - **Mix levels.** `0x600b3a88` sets eleven project levels to `0x56` and saves them
    to `E+0x29..0x33`: the 8 sample-track volumes `0x20204a84..8b`, the reverb level
    `0x20204a8c` (scales the reverb sends, ITCM `0x3fa8`), the delay level `0x20204a8d`
    (scales the delay sends, ITCM `0x4984`) and `0x20204a91` (multiplies every track
    volume at ITCM `0x45c0`; probably Dry Mix). These are levels on the gain table
    `0x6003a690`: index 86 is 1.0 (0 dB), 100 is +6 dB. So the recorder plays
    everything at 0 dB, for its preview. The Mixer screen's pointer table to the same
    eleven bytes is `0x6003fcb0` (used by `0x600db550`). `0x600b3ae4` puts them back.
  - **The delay and reverb.**
    - `0x600b4058` resets the delay: `0x60032114` clears its buffers and sets bypass
      `+0x2d` = 1. It also zeroes each player's delay send (ITCM `0x4984`, veneer
      `0x600b40b8`) and each synth's (ITCM `0x3e68`, veneer `0x600b40b0`, value 0.0).
    - `0x600b40c0` does the same for the reverb: `0x60031ebc` clears its buffers and
      `+0x80`, then the player sends (ITCM `0x4bc4`) and synth sends (ITCM `0x3fa8`).
    - `0x600b2dd8`/`0x600b2de4` set and clear the delay bypass.
    - The sends come back only when marked:
      - **players:** `player+0x798` bit 8 (delay) and bit 20 (reverb).
        `voice_block_update_q` (`0x669a`, thunk `0x1f950`) calls `0x600bb6e8` and
        `0x600bb770`. They recompute the send from `ins+0x106` / `ins+0x170`, or the
        step's FX, or 0 for a muted track.
      - **synths:** the pending mask `E+0x1a48 + 4·synth`, bits 0 panning, 1 delay,
        2 reverb, 3 (`0x3db4`), 4..9 Macros a–f (the switch in `0x40e8`). The value
        comes from `0x600b3904(E, synth, type)`: the 32-byte entries at
        `E+0x1688 + 32·(10·synth + type)`, whose layers are added and clamped by the
        table `0x6003aa8c`.
  - **The limiter.** `0x600b36dc(E, 0)` sets the limiter stage's side-chain mode
    (`0x60031b4c`) and both selectors (`0x600327c8`) to 0. `0x600b4128(E)` sets them
    again from the project (`0x20204aae` mode, `0x20204aaf` source).
  - **The leave** (`+0xbba` clear): stop the pattern if it plays, then call
    `0x600b4058`, `0x600b40c0`, `0x600b2de4`, `0x600b3ae4`, `0x600b3484` and
    `0x600b39e4`; then, in either case, `0x600b4128`, `0x600b3c90`, project
    `0x202046c5` = `scr+0xbe8`, `seq+0x34` = 0, `seq+0x7dd` = 0, and `0x600ce1bc`.
- **Emulator, an engine-on recording.**
  - **Setup:**
    - the whole graph from `synth_rig`, with `engine_block_update` run before each
      graph update;
    - a real recording started with `0x6010cb90`/`0x6010c890`;
    - a stems sink object faked at `0x20026ec8`, as after boot.
  - **Undo steps:** after the stock entry, the gate calls, `0x600b2de4`, `0x600b3ae4`,
    `0x600b4128`, the send marks above, and `0x1f540` answering 0.
  - **Output:** 120 blocks of two sample tracks and two synths (sends on) are
    bit-identical to normal playback of the same notes with the recorder closed (input
    silent, Monitor off). The recording holds the input. (The rig's sample instruments
    had bit depth 0, so the sample tracks were silent at the master here: this covered
    the synths only. See "Live resampling, built" below for the full check.)
    - Without the `0x1f540` change the reverb is missing (the synth sends are never
      re-applied).
    - Without `0x600b3ae4` the levels differ (by up to 701).
  - **Not exercised:** the delay's return was silent in the rig in both cases, so only
    its bypass flag and the send setter calls are checked.
  - **Cost and pool** (8 sample tracks and 3 synths, 24 blocks):

    | Case | Instructions per block | Pool peak |
    |---|---|---|
    | normal playback | 539,000 | 74 |
    | stock recording (silent) | 451,000 | 76 |
    | engine-on recording | 532,000–536,000 | 76 |

    The same 30 allocation failures appeared in every case. Even with the stems sink
    the emulator's pool isn't the device's, so only the comparison counts.
- **Graph notes** (Teensy-style objects, FINDINGS section 19): bus mixers
  `E+0x1248`/`E+0x1290` (inputs: 0 track mix, 1 delay return, 2 reverb return, 3 line
  in from `E+0x2c8`); limiter stage `E+0x241c`/`E+0x2478` (input 1 = selector
  `E+0x2290`); master `E+0x2508`/`E+0x2574` (7 inputs: 0 limiter, 5 preview player
  `E+0xdb4`, 6 metronome `E+0xed0`); output sink `E+0x24d4` (class `0x600306cc`)
  copies into the DMA buffers at `+0x24`/`+0x28`. Codec input object `E+0x98`, USB in
  `E+0xb0` (q), class `0x60030649`. The stems mixer `E+0x2328` returns without receiving
  while `*(0x20026ec8)` == 0, so in the emulator (no project mix) it holds 28 blocks:
  the emulator's block-pool numbers aren't representative.

### Live resampling, built (`patch/resample` rs2 and rs3, emulator and static)

- **Recorder object fields:** `rec+0x00` buffer, `+0x04` size, `+0x08` channels, `+0x0c`
  ch2, `+0x10` flag (`0x6010cb90` copies its arguments there); `+0x14`/`+0x18` the channel
  vector (8-byte entries, pointer at `+4`; `0x6010ca68` reads entry 1's pointer only when
  the vector holds 2 entries). `0x6010c888(rec)` is `ldrb r0, [r0, #0x2c]`.
- **The Play handler** `0x600d07b4` takes no arguments. "Playing" is `seq+0x2d` == 0 and
  (`seq+0x1d` or `seq+0x2a`): then stop `0x600acd90(seq)`. Not playing: Shift
  (`0x600cf90c(*0x200209e8, 0x1d)`) → continue `0x600acd38`, else play `0x600ac5f8`. In
  the recorder before Play, `seq+0x2d` = 1; after play, `+0x2d` = 0 and `+0x2a` = 1.
- **The Rec handler** `0x600d0620` takes no arguments: mode 0 → `0x600fb3cc`; mode 1 only
  while `+0xb4d`. In `0x600fb3cc`, past the pop-up checks (`+0x75`, `+0xbba`, `+0xbbc`,
  `+0xbbd`, `+0xbbf`, `+0xbb8`/`9`), mode 0 tests Pre Roll (`0x600ad400`: settings
  `0x2002061c+0xe7` == 1) and the source (not Radio): count-in (`+0xbf8` = 0x100,
  `+0xb4d` = 1, `0x600ac2c0`), else `0x600f9074` at once. The mode stays 0 while
  recording.
- **`scr+0xb4d`** has three writers: set at `0x600fb528` (count-in) and `0x600f9080`
  (record start), cleared at `0x600faf2e` in the update's stop path, which then calls
  `0x600b4058`, `0x600b40c0` and the stop `0x6010c944`. So it is set from the count-in
  to the stop.
- **A second recording path:** a factory self-test around `0x601106f0`..`0x601107f4`
  records `/line_in_test.wav` and `/mic_test.wav` with its own buffers (`*0x200006a8` +
  `0x61a80`, size `0x3c0000`; BLs to `0x6010cb90` at `0x60110734`/`0x601107ea`, queue
  start at `0x6011073a`/`0x601107f0`, stop at `0x60110760`). It doesn't go through
  `0x600f9074`.
- **New-instrument defaults:** `0x600c1b50(_, ins)` clears the 0x174 bytes and sets a
  new instrument's defaults (`ins+0` = 1, mode `+0x3c` = 0, volume `+0x100` = 50, bit
  depth `+0x172` = 16, ...). The save of a recording calls it (`0x601150e0`). Emulator
  rigs should build sample instruments with it: with bit depth 0 a sample track is
  silent at the master.
- **The per-track bit crusher:** each player has one (vtable `0x60042eb0`, update
  `0x6003080c`, which ANDs every sample with the mask at `+0x18`). `0x6003084c(obj,
  bits)` sets the depth (clamped to 4..16, into `+0x14`) and the mask; the per-block
  player update calls it.
- **The sends come back one block late:** after `0x600b4058`/`0x600b40c0` zero the
  sends, the marks (`player+0x798`, `E+0x1a48`) recompute them in the next engine block,
  so a note in the first block after reopening plays with the old sends. Stock's leave
  has the same order. Measured against normal playback: at most 27 of a 32268 peak
  (-61 dB), for about 20 blocks.
- **The bypassed delay keeps writing:** after `0x600b4058` clears its buffer and bypasses
  it, the delay object (`0x6003235a`/`0x60032366`) still writes small values from its
  state into its buffer (`0x800e2000`, 0x188000 bytes) every block. Stock does this too
  when the recorder is entered right after playing (max 33 there); the leave clears the
  buffer again.
- **Gain ramps:** the bytes from `E+0xf00` (the sends' gains, as far as traced) move every
  block in the audio objects' updates (`0x60036d44`, `0x60036e50`), so a state check
  compares them after the same number of blocks, not right after a call.
- **The line-in during an engine-on recording:** with Monitor off, an input of ±1000
  changes no output sample (the entry's `0x600b3440` mute holds); with Monitor on it
  reaches the master at 1.0, as in the stock recorder.
- **Count-in timing in the rig:** with `seq+0x6fc` = `0x600f8605`, the count-in ends after
  577 sequencer ticks at the rig's settings; the first note after `0x600ac5f8` comes 12
  ticks (one micro-step) later.
- **Rec during the count-in** (mode 0, `+0xb4d` set) goes through `0x600fb3cc` again. In
  the rig a second press right after the first leaves the count-in running and stock
  records at its end (577 ticks; whether a later press restarts the count is not
  checked). On hardware too there is no way to cancel the count-in.

## 29. Track lengths, the play position and the Pattern grid (polymeter, emulator and static)

Found while planning a per-track length (polymeter) patch (`docs/POLYMETER_PLAN.md`).
`ed` = `patview + 0x1000` = `0x20022770` (section 13).

### One position per track, one pattern length

- **Every track has its own step** (`+0x88`, section 12), but in pattern and song mode
  `seq_advance_step` (`0x600ad010`) moves all 16 with the same rule and the same last
  step (track 0's length byte, `seq+0x36`, or a looping selection's end), so they stay
  equal. Only performance mode (tracks 1–8: own lengths `seq+0x38`, play modes `+0xb9`)
  makes them differ.
- **The forward rule** (`0x600ad0ec`): `+0x8a` + 1 and `+0x88` + 1. The track wraps
  when either passes the last step, or when a Break is pending. On a wrap through
  `+0x8a` (`0x600ad10e`), `+0x8a` = 0, but `+0x88` only goes back to the loop start if it
  has also passed the last step; otherwise it keeps counting.
- **The pattern ends only when track 15 wraps** (`+0x33` set at `0x600ad112` or
  `0x600ad16e`); track 15 by either counter. Nothing else ends a pattern.
- **Track 0's `+0x88` is also the master position for three other readers.** The step
  copy of every track reads that track's own `+0x88`, but these read track 0's:
  - the metronome (`0x2346`: step mod (beats × bars)) (q for the settings);
  - the next-pattern preload request at micro-step 1 of the last step (`0x2396`,
    `0x2574`: `+0x88 == data+0x1c`);
  - the Pattern screen's play row: `ed+0x1f0` = `seq+0x88` at `0x600e208e`
    (`scr_pattern_update`), `0x600e2f44` and `0x600e17f2`.

  So shortening a track by changing its `+0x88` would break song mode (no preload
  request), the metronome and the play row whenever track 0 is short.
- `seq_master_micro` (ITCM `0x1de8`) also keeps a master step at `seq+0x30`.

### Where step data is read during playback

- **`seq_process_track` reads a step's data at one place:** the 6-byte copy into the
  work record at micro-step 1, `bl memcpy_small` at ITCM `0x2170`. There:
  - r0 = the work record;
  - r1 = `*(seq+0x14) + 0x1d + 0x301·t + 6·step`, with step = the track's `+0x88`;
  - r2 = 6, r4 = seq, r5 = t.

  It runs for every track, muted or not, before the mute check.
- **The only other reads are Roll's look-aheads.** `seq_distance_to_next_note_q(seq, t,
  step)` (ITCM `0x3958`, called at `0x33a2` and `0x3520` with step = `+0x88`) counts the
  steps to the next note, stopping at the pattern's last step. It returns 1 when the next
  step has a note or the pattern ends there.
- **Emulator proof** (scratch: a Python hook at `0x2170` rewrote r1 to use
  step mod L(t); everything else stock):
  - without lengths, the 194 MIDI events of 3 passes were identical to stock;
  - in a 16-step pattern, track 1 with L = 6 played steps 0–5 0–5 0–3, then all tracks
    started again together, for 3 passes; track 16 with L = 5 played 0–4 ×3 then 0;
  - with track 16 short, the pattern still ended after 16 steps: `+0x8a` still
    reaches 15. `+0x88` of every track stayed 0..15, so the play row, the preload and
    the metronome were unchanged;
  - playing from step 9, the L = 6 track started on step 3;
  - in song mode the preload request still came on step 15, and the next pattern
    started on its step 0 for every track;
  - a looping selection 8..11 played steps (8..11) mod 6 = 2..5;
  - performance mode, track 1 backward with a performance length of 16, played
    (0, 15, 14, …) mod 6.

### Pattern lengths in the pattern data

- `pat_change_length(patview, delta)` (`0x600e0024`, the jog while Length is held)
  clamps to 1..128. It writes the last-step byte of tracks 0..7 only (`+0x1c`,
  `+0x31d`, …, `+0x1523`), then `ed+0x1ea` = length, marks the pattern changed and
  refreshes. The other length writers set the same bytes from track 0's:
  - Length + Left/Right (`0x600e00a8`, preset lengths at `0x6004013c`);
  - Length + a pad (`0x600e0158`, from `0x600c6d48`, the pad scaled over 1..128);
  - Duplicate (`0x600ada8c`), which writes all 16.

  Playback reads only track 0's byte, except performance mode's backward wrap, which
  reads track t's (`0x600ad0aa`). Tracks 8..15's bytes are never updated by a length
  edit, so per-track bytes in the `.mtp` can't hold a mod's lengths: stock rewrites or
  ignores them.

### Pattern screen inputs

- The Pattern screen registers its input callbacks from flash literals next to the
  column buttons' (section 20):
  - Left `0x600df2cc` → `0x600c7571`;
  - Right `0x600df2d0` → `0x600c7d71`;
  - jog `0x600df2f0` → `0x600c90a1`;
  - pads `0x600df2f8` → `0x600c6bd9`.

  Each word is the only reference.
- **The jog** (`pat_jog`, `0x600c90a0`, r0 = delta) goes by the pad mode `ed+0x354`.
  While a bottom-bar button that has its own view is held:
  - 0 (Pattern held): pattern select;
  - 1 (Length held): `bl pat_change_length` at `0x600c9172`;
  - 2 (Step jump held): `project+0x6df`.

  Otherwise it edits or moves the cursor.
- **The Length button** (`pat_length_button`, `0x600e249c`, bound as kind 3 on page 1):
  a press sets `ed+0x354` = 1 and redraws the pads and the bar. A release goes to
  `pat_hold_view_release` (`0x600e2250`, `ed+0x354` = −1).
- **Shift** (button `0x1d`) plus a screen button mutes that track on the Pattern screen
  (manual 1.2.0, quick reference), so Shift isn't free as a modifier for screen buttons
  there.
- **Page 3** binds only buttons 0 and 1 by `bl` (`0x600e29ba` and `0x600e29c6`, chained
  by humanize and copypattern) and More by a tail `b.w`. The labels' text and second line
  for button 6 are `bl`s that no patch uses yet: `0x600e58b4` and `0x600e5914`.

### The Pattern grid

- **Row texts.** `patview_fill_rows_q` (`0x600df3a0`) writes 15 rows × 0x13 bytes per
  track at `patview + 0x14 + 0x11d·t + 0x13·row` (note, instrument, FX1, FX2 texts) and
  the mute bytes at `patview+0x1200+t`. It sets:
  - `ed+0x1ea` = length;
  - `ed+0x1e8` = the centre row (the cursor `ed+0x1e6`, or the play row `ed+0x1f0`
    while following).

  Row r shows step `ed+0x1e8 − 7 + r`. A row outside 0..length−1 shows the step wrapped
  round when `settings+0xd2` is 0, and is blank otherwise.
  Each row is 0x13 bytes: note text at `+0` (5 bytes), instrument `+5` (4), FX1 `+9`
  (5), FX2 `+0xe` (5).
- **The grid control** (0x50 bytes, vtable `0x60046dc8`). It is built by `patgrid_ctor`
  (`0x6010a3d4`, `(ctrl, desc)`) through `disp_create_patgrid_q` (`0x600e5ec8`), called
  at `0x600e624e` in the Pattern screen's create-controls (`0x600e5f68`, vtable slot
  `0x600403e8`). The descriptor:

  | desc | ctrl | Value |
  |---|---|---|
  | `+4` | `+0x18` | `ed+0x4dc` |
  | `+8` | `+0x2c` | the row texts, `patview+0x14` ("data") |
  | `+0xc` | `+0xc` | flags `0x1000400` or `0x400` (font in bits 8–11) |
  | `+0x10` | `+0x28` | the color table, `*(display+8) + 0x2b8` |
  | `+0x14`..`+0x1a` | `+0x1c`..`+0x22` | x 0, y 0 or 0x1c, w 800, h 0x1a7 or 0x18b |

  The constructor also reads a font table in SDRAM (`0x83fcd374`), which only affects
  positions.
- **Its draw**: vtable methods 1–4 each draw one column:
  - `patgrid_draw_note` `0x6010b320`;
  - `patgrid_draw_instr` `0x6010b4d4`;
  - `patgrid_draw_fx1` `0x6010b700`;
  - `patgrid_draw_fx2` `0x6010b908`.

  Each runs when ctrl `+0x39` is 0 or has its bit (bits 0–3). The control draws
  `+0x3c` tracks from the first visible track (`ed+0x1ec`), 15 rows each (`+0x3d`), so
  `t` below is the absolute track.

  For each cell it picks a color into ctrl `+0x44` from the color table at `+0x28`. In
  order:
  - the highlighted color `+0x38`: for the cursor cell (row 7 of the cursor track
    `ed+0x1e4`, while `ed+0x1e0` and `ed+0x1e1` are set) or inside the selection
    rectangle (ctrl `+0x38`, `+0x30`..`+0x36`);
  - else the muted color `+0x30` when `patview+0x1200+t` is set;
  - else the column's empty or normal color, by whether the text starts with "-".

  | Column | Normal | Empty | Stock colors (normal / empty) |
  |---|---|---|---|
  | note | `+0x08` | `+0x18` | `0x00e8be` / `0x003933` |
  | instrument | `+0x0c` | `+0x1c` | `0xfef749` / `0x453b1b` |
  | FX1 | `+0x10` | `+0x20` | `0xbb58f1` / `0x321f3a` |
  | FX2 | `+0x14` | `+0x24` | `0x57f1ff` / `0x183744` |

  Muted is `0x171717` and highlighted `0xffffff` (table `0x6005a778`). Then
  `patgrid_draw_cell` (`0x6010b204`, text pointer = first stack argument) draws the
  text. It emits `eve_color_rgb` only when `+0x44` differs from the last color
  (`+0x48`).
- **The 5 `bl 0x6010b204` sites**, one per column:
  - `0x6010b3de` note;
  - `0x6010b62c` instrument, 2-character text;
  - `0x6010b5ae` instrument, 3-character text (smaller font);
  - `0x6010b7be` FX1;
  - `0x6010b9c4` FX2.

  The text pointer gives the cell: data + 0x11d·t + 0x13·row + the column's offset.
- **Emulator** (scratch rig):
  - stock `patview_fill_rows_q`, the constructor with the descriptor above, and
    methods 0–4 drew 240 cells (4 tracks × 15 rows × 4 columns) through 4 of the sites;
  - the 3-character instrument text goes through the fifth;
  - every text pointer decoded to the expected track, row and column;
  - row r showed step `ed+0x1e8 − 7 + r` in all three `settings+0xd2` modes, also
    scrolled to tracks 13–16:
    - 0: wrapped round, so row "16" is step 0;
    - 1: blank outside the pattern;
    - 2: the centre moves only when the cursor nears an edge.

### Live recording reads the step too

- `seq_record_live_note` (ITCM `0x15c8`) is reached through 8 flash veneers whose
  literal is `0x15c9` (`0x600ab69c`, `0x600c73cc`, `0x600cd554`, `0x600d4b34`,
  `0x600f1924`, `0x600f54ec`, `0x600fad84`, `0x6011a0fc`). It loops over the tracks
  and reads each track's `+0x88` at `0x18b8`. Past micro-step 24 it records on the next
  step, (step + 1) mod length, unless a flag on its stack says otherwise.
- `0x600ac700` (live note-off recording (q), from the pads handler `0x600c6cde`) reads
  `+0x88` at `0x600ac75e` with the same rule, and moves one step further when that step
  equals `+0x82` (the step the note was recorded on (q)).
- **Both take** `(seq, source, note, r3, a 16-bit stack argument)`. Source is `0x14`
  from the pads; the note-off recorder treats `0x16` separately. They record only while
  `settings+0xce` is 2 or 3.
- **No single place picks the row.** `seq_record_live_note` computes the step inline
  (`sdiv`/`mls` at ITCM `0x18d8`, against track 1's last-step byte
  `*(seq+0x14)+0x1c`). It loops over the 16 tracks and writes the row at several stores
  (`0x1736`, `0x1966`, `0x1aa4`, `0x1ad6`, among others). The note-off recorder also
  writes from several places (`0x600ac8b4`, `0x600ac8d8`, …). A change to the row has to
  wrap the whole call.
- **The callers are a closed set:**
  - `seq_record_live_note`: only through the 8 veneers above (`ldr.w pc, [pc]`; no other
    reference to `0x15c9` in flash or ITCM). Their 14 callers: `0x600ab614` (sequencer
    module, `0x600ab5da`), the Pattern pads (`0x600c6e7c`, `0x600c6faa`, `0x600c6fca` in
    `pat_pads_q`; `0x600c6abe`, `0x600c6bb6`, `0x600c7374`), `0x600cd504`, `0x600d47a8`,
    `0x600d497e`, `0x600f18f0`, `0x600f5478`, `0x600fabf6` (a `b.w` to the veneer) and
    `0x6011a0da`.
  - `seq_record_note_off_q` (`0x600ac700`): 18 direct callers, no pointer to it. These
    16 are `bl`: `0x600ab514`, `0x600c6afc`, `0x600c6b78`, `0x600c6cde`, `0x600c6ece`,
    `0x600c6f96`, `0x600c71ee`, `0x600c7348`, `0x600cd4a6`, `0x600d4792`, `0x600d491e`,
    `0x600d4adc`, `0x600f0396`, `0x600f1904`, `0x600f57aa`, `0x6011a0a8`. And 2 are tail
    calls (`b.w`): `0x600f5500` and `0x600f9418`.

### Page 3's button 6 and the encoder

- **Button dispatch** (`btn_dispatch_q`, `0x600cf7b4`, `(manager, button, state)`; skipped
  while the manager's `+0xc` byte for that button is set). It acts by the entry's kind:

  | Kind | Bound by | Handler | Called |
  |---|---|---|---|
  | 1 | `btn_bind` | `+4` | when the state equals the flag `+0x10` (1 = press only) |
  | 2 | | `+8` | when the state equals the flag, with r0 = the button |
  | 3 | `btn_bind_kind3` (`0x600cf74c`, flag `0xff`) | `+8` | on every press and release, with r0 = the state |
  | 4 | `0x600cf778` | `+0xc` | with (button, state) |
- **Emulator** (scratch):
  - a kind-3 bind of button 6 on page 3 called its handler with r0 = 1, 0, 1, 0 for
    press, release, press, release;
  - the stock binds of pages 1, 2 and 3 each replace or clear it (page 3 clears buttons
    0–6 first), so a button-6 binding never outlives page 3.
- **Page-3 sites already hooked:**
  - humanize, unchained: bind `0x600e29ba`, text `0x600e5884`, colors `0x600e593c`,
    data `0x600e5966`;
  - copypattern, chained: bind `0x600e29c6`, text `0x600e5890`, second line
    `0x600e58f0`, colors `0x600e592e`, data `0x600e5958`.

  Button 6's text (`0x600e58b4`) and second line (`0x600e5914`) are free. Page 3 has no
  colors or data call for button 6, but button 7's (colors `0x600e594a`, data
  `0x600e5976`) are free to chain.
- **The encoder**: `pat_jog(delta)` has r0 = the signed delta. Its word `0x600df2f0` is
  the literal loaded at `0x600df292`. It does nothing while `0x6010e0f8(0x200209ac)` is
  nonzero or an overlay is open (`ed+0x398`). Then it acts by `ed+0x354`.
