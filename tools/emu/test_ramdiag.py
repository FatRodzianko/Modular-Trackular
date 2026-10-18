"""Check the ramdiag patch on the patched firmware.

  python tools/emu/test_ramdiag.py <patched.ptf>

1. Startup: runs the stock Config menu constructor. Project Settings must hold its 10
   stock items and a "RAM diag" folder of 6 stock text items, and ramdiag's state must
   be reset (nothing filled, texts "-"). Nothing outside its own state is written.
2. Fill: the first Pattern screen update (hooked; the stock one is stubbed and must
   run every time) fills each region with the address pattern, and writes nothing
   just outside the regions.
3. Scan and report: with a fake clock and SD card, opening Config writes ramdiag.txt
   at once; touched words show up in the counts, both on the menu (stock value
   formatting) and in the file; the heap high-water mark follows the break and never
   goes down; a report follows every minute; going back to the Pattern screen and
   reopening Config writes a new one.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fakefs import FakeFS  # noqa: E402
from machine import Machine  # noqa: E402

MENU_CTOR, ATEXIT = 0x60118ACC, 0x600A3594
MENU_FORMAT = 0x6011870C
PROJECT_SETTINGS = 0x20028714
PATTERN_UPDATE_SLOT, PATTERN_UPDATE = 0x600403DC, 0x600E1F98
CONFIG_UPDATE_SLOT, CONFIG_UPDATE = 0x6003B5F8, 0x600C6474
CLOCK = 0x20027D18
HEAP_BREAK, HEAP_START = 0x2002FB9C, 0x2002FC68
RD = 0x20205000
RD_MAGIC = 0x31444152
REGIONS = [("DTCM above heap", 0x20051C68, 0x20053000), ("SDRAM top", 0x83FF9BF0, 0x84000000),
           ("ITCM tail", 0x0001FB38, 0x00020000), ("DTCM after audio", 0x200094B8, 0x20009800)]
LABELS = ["DTCM above heap", "SDRAM top", "ITCM tail", "DTCM after audio", "Heap high-water", "Reports written"]

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def pattern(a: int) -> int:
    return a ^ 0x5AC3A53C


def main() -> int:
    m = Machine(Path(sys.argv[1]))
    fs = FakeFS(m, Path("."))
    clock_ms = [0]
    calls = {"pattern": 0, "config": 0}
    clock = m.alloc(0x10)
    vt = m.alloc(0x10)
    m.w32(vt, m.add_stub(lambda mm: clock_ms[0]))
    m.w32(clock, vt)
    m.w32(CLOCK, clock)
    m.stub_function(ATEXIT, lambda mm: 0)
    m.stub_function(PATTERN_UPDATE, lambda mm: calls.__setitem__("pattern", calls["pattern"] + 1))
    m.stub_function(CONFIG_UPDATE, lambda mm: calls.__setitem__("config", calls["config"] + 1))
    for _, s, e in REGIONS:                          # sentinels just outside each region
        m.w32(s - 4, 0x11223344)
        if e < 0x84000000 and e != 0x20000:
            m.w32(e, 0x55667788)

    print("startup: Config menu constructor")
    m.call(MENU_CTOR, max_insns=50_000_000)
    ps = PROJECT_SETTINGS
    count = m.r32(ps + 0x1C) >> 8 & 0xFF
    kids = [m.r32(m.r32(ps + 0xC) + 4 * i) for i in range(count)]
    check(count == 11 and m.r32(ps + 8) & 0xFF == 11, f"Project Settings holds 11 ({count})")
    folder = kids[-1] if kids else 0
    check(folder and m.cstr(m.r32(folder + 0x18)) == "RAM diag", "item 11 is the folder 'RAM diag'")
    items = [m.r32(m.r32(folder + 0xC) + 4 * i) for i in range(m.r32(folder + 0x1C) >> 8 & 0xFF)]
    check([m.cstr(m.r32(i + 4)) for i in items] == LABELS and all(m.r32(i + 0xC) & 0xFF == 5 for i in items),
          "its 6 items are stock text items with the expected labels")
    check(m.r32(RD) == RD_MAGIC and m.r32(RD + 4) == 0, "state reset: nothing filled yet")

    def menu_texts():
        m.call(MENU_FORMAT, folder)
        return [m.cstr(m.r32(m.r32(folder + 0x14) + 4 * i)) for i in range(len(items))]
    check(menu_texts() == ["-"] * 6, "menu shows '-' before the fill")
    check(all(m.r32(s) != pattern(s) for _, s, _ in REGIONS), "regions untouched at startup")

    pat_hook, cfg_hook = m.r32(PATTERN_UPDATE_SLOT), m.r32(CONFIG_UPDATE_SLOT)
    print("fill: first Pattern screen update")
    m.w32(HEAP_BREAK, HEAP_START + 40_000)
    clock_ms[0] = 1000
    m.call(pat_hook, 0x20021770)
    filled = all(m.r32(a) == pattern(a) for _, s, e in REGIONS for a in range(s, e, 4))
    check(filled, "every word of every region holds the pattern")
    outside = all(m.r32(s - 4) == 0x11223344 for _, s, _ in REGIONS) and \
        all(m.r32(e) == 0x55667788 for _, _, e in REGIONS if e < 0x84000000 and e != 0x20000)
    check(outside, "nothing written just outside the regions")
    check(fs.writes_opened == 0, "no file written at the fill")

    print("scan and report")
    m.write(0x83FFA000, b"\0" * 12)                 # 3 words of SDRAM top
    m.w32(0x0001FC00, 0)                              # 1 word of the ITCM tail
    clock_ms[0] = 1500
    m.call(cfg_hook, 0)                               # Config opens: report at once
    data = fs.overlay.get("ramdiag.txt", b"").decode()
    check(fs.writes_opened == 1 and "SDRAM top 0x83ff9bf0-0x83ffffff: 25604 of 25616 bytes untouched" in data
          and "ITCM tail 0x0001fb38-0x0001ffff: 1220 of 1224 bytes untouched" in data
          and "DTCM above heap 0x20051c68-0x20052fff: 5016 of 5016 bytes untouched" in data
          and "high-water 40000 of 139264" in data, "opening Config writes ramdiag.txt with the counts")
    check(max(fs.write_sizes or [0]) <= 256, "writes of at most 256 bytes")
    check(menu_texts()[:4] == ["5016/5016", "25604/25616", "1220/1224", "840/840"]
          and menu_texts()[4] == "40000 B", f"menu shows the counts {menu_texts()}")
    m.w32(0x20051D00, 0)
    m.w32(HEAP_BREAK, HEAP_START + 52_000)
    clock_ms[0] = 2600
    m.call(cfg_hook, 0)
    check(menu_texts()[0] == "5012/5016" and menu_texts()[4] == "52000 B" and fs.writes_opened == 1,
          "a second later: counts and high-water updated, no new file")
    m.w32(HEAP_BREAK, HEAP_START + 30_000)
    clock_ms[0] = 3700
    m.call(cfg_hook, 0)
    check(menu_texts()[4] == "52000 B", "the high-water mark never goes down")
    clock_ms[0] = 62_000
    m.call(cfg_hook, 0)
    check(fs.writes_opened == 2 and "report 2" in fs.overlay["ramdiag.txt"].decode(), "a report every minute")
    clock_ms[0] = 63_000
    m.call(pat_hook, 0x20021770)
    m.call(cfg_hook, 0)
    check(fs.writes_opened == 3 and menu_texts()[5] == "3", "reopening Config writes a new report")
    clock_ms[0] = 125_000
    m.call(pat_hook, 0x20021770)
    check(fs.writes_opened == 4, "a report every minute on the Pattern screen too")
    check(calls["pattern"] == 3 and calls["config"] == 5, f"stock update methods ran every time {calls}")
    print(fs.overlay["ramdiag.txt"].decode())
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
