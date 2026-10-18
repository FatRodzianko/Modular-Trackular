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
| DTCM `0x2002fbe4`–`0x20053000` | `_end` / heap start used by the allocator; the stack sits above | Heap, not free |
| OCRAM `0x20204f4c`–`0x20207fff` (tail of the 32 KB bank) | No address constants, no MOVW/MOVT | **Patch RAM goes here** (colorsong: `0x20205000`, 12,208 bytes) |
| `0x20208000`–`0x202fffff` | Doesn't exist on this unit (reserved address space) | **Never use.** An access to `0x20280000` froze the unit (section 11) |
| SDRAM `0x80000000`–`0x83ffffff` | Sample memory and buffers all the way to `0x83ff5bf0` | Not free |

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
the OCRAM tail at `0x20205000` (region 7, Normal memory).

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
  and calls the stock `song_grid_draw`. For `User/acid camp` it produces the same grid
  layout and colors as the device (579 display-list words).

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
  emulator: for `acid camp` all 14 used patterns match what the stock
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
