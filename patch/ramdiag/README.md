# ramdiag: which RAM is really free

A diagnostic patch for Polyend Tracker+ firmware 1.2.0. It checks on the device
whether RAM regions that no firmware code refers to stay untouched while you use the
Tracker+, and how far the heap grows. The answers decide where future patches can keep
their state ([docs/FINDINGS.md](../../docs/FINDINGS.md), section 15). Config shows the
firmware version as `1.2.0-RD1`.

It's a tool, not a feature: install it on its own on top of stock firmware, run one
test session, then go back to your usual firmware. It can't be combined with humanize
(both hook the same places).

**Status:** tested in the emulator only, not yet on hardware.

## What it checks

| Region | Range | Size |
|---|---|---|
| DTCM above the heap limit | `0x20051c68`–`0x20052fff` | 5,016 B |
| SDRAM top | `0x83ff9bf0`–`0x83ffffff` | 25,616 B |
| ITCM tail | `0x0001fb38`–`0x0001ffff` | 1,224 B |
| DTCM after the audio pool | `0x200094b8`–`0x200097ff` | 840 B |

Plus the heap's high-water mark: how far the heap has grown above its start, out of
139,264 bytes (the heap never shrinks, so its current end is its high-water mark).

## Running a test session

1. Back up the SD card. Test with copies of your projects.
2. Put the stock `TrackerPlus_1.2.0.ptf` and the diagnostic build in `/Firmware`, and
   install the diagnostic through Config → Firmware. Config shows `1.2.0-RD1`.
3. Power on, open a project and go to the Pattern screen. That fills the regions with
   the test pattern.
4. In the same session (no power cycle), use as much of the Tracker+ as you can:
   playing projects and song mode, saving, switching projects, loading and recording
   samples, the sample editor, rendering and export, performance mode, MIDI, the
   synths, USB storage, the games.
5. Open Config → Project Settings → **RAM diag**. It shows, per region, the bytes
   still untouched out of its size (`25616/25616` means nothing touched it), the heap
   high-water mark, and how many reports were written. The same numbers, plus free
   heap and max stack, are in `ramdiag.txt` in the card's root.
6. Switch back to your usual firmware through Config → Firmware.

If the unit freezes, note what you were doing, pull the power, boot, and look at
`ramdiag.txt`: it holds the last report (written when Config opens and about once a
minute on the Pattern and Config screens).

A region that shows its full size after a thorough session is very likely free. A
region with fewer bytes is used by something, so patches can't use it.

## How it works

| Hook | Where | What |
|---|---|---|
| `mt_rd_settings_folder` | `bl cfg_menu_folder` at `0x60118b00` | Creates Project Settings with room for 11 items instead of 10 |
| `mt_rd_last_item` | the menu constructor's last `bl cfg_menu_item` (`0x601193e6`) | Resets ramdiag's state (at power-on), and adds the RAM diag folder with 6 stock text items |
| `mt_rd_pattern_update` | Pattern screen vtable slot `0x600403dc` | Runs the stock update; the first time, fills the regions; then writes a report every minute |
| `mt_rd_config_update` | Config screen vtable slot `0x6003b5f8` | Writes a report when Config opens and every minute; recounts every second; then runs the stock update |

Nothing happens at power-on except resetting its own state, so even if a region turns
out to be in use, Config stays reachable to switch back. Each region gets a pattern
that depends on the address (`address XOR 0x5ac3a53c`), so zeroed, copied or shifted
data all show as changed. The ITCM tail is written and read with aligned 4-byte
accesses only (the MPU makes it Device memory). Its own state (under 256 bytes) is at
`0x20205000`, in the OCRAM tail, which stock firmware doesn't use. The report is
written in pieces of at most 256 bytes, from the screens' update methods, like
colorsong and humanize do. The code is 1,553 bytes and moves wherever the patcher
places it (`prebuilt/ramdiag.rel`).

## Build

No compiler needed (the compiled patch is in `prebuilt/`):

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/ramdiag/patch.json build/TrackerPlus_RD1.ptf
```

From source, with the Arm GNU Toolchain: `patch/ramdiag/build.sh`.

Emulator test (`pip install unicorn capstone numpy`):

```bash
python tools/emu/test_ramdiag.py build/TrackerPlus_RD1.ptf
```

It builds the Config menu with the stock constructor, checks the fill (every region,
nothing next to them), and with a fake clock and SD card checks the counts after
some words are changed, the heap high-water mark, the menu text and the report file.

## Risks and reverting

It only writes RAM, which every power cycle clears, and never touches the
bootloader, flash or any hardware setting. If a region is in use, the likely result is
a freeze or glitch: pull the power, boot, and switch back through Config. A crash
while a file is being saved could damage that file, hence the backup. If the unit
doesn't boot, put only the stock `TrackerPlus_1.2.0.ptf` in `/Firmware`, power on with
the card in, and press the pinhole button next to the USB-C port.
