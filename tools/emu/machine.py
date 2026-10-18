"""Run individual Tracker+ firmware functions under Unicorn (Cortex-M7, Thumb).

Loads the stock image, maps the i.MX RT10xx memories the firmware uses, applies the
startup data-copy table (ITCM code, DTCM .data), and calls functions with a fake
EVE display object that records every display-list word instead of sending it
over SPI. Nothing here runs the real boot path: callers set up the RAM state a
function needs and call it directly.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from unicorn import (UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_MEM_INVALID, UC_MODE_MCLASS,
                     UC_MODE_THUMB, Uc, UcError)
from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
                               UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ptf import read_ptf  # noqa: E402

FLASH_BASE = 0x60030000
DATA_TABLE = (0x600302A0, 0x600302D0)   # {load, exec, len} triples
BSS_TABLE = (0x600302D0, 0x600302F0)    # {addr, len} pairs

DISPLAY_PTR = 0x20027D64                # global: pointer to the display / EVE driver object

# Our own region for stubs and scratch objects (unused by the real chip map).
STUB_BASE = 0x70000000
STUB_RET = STUB_BASE + 0x0FF0           # functions "return" here
STUB_DL_ADVANCE = STUB_BASE + 0x100     # display vtable +0x14: advance write pointer
STUB_DL_WRITE = STUB_BASE + 0x200       # display vtable +0x18: write one 32-bit word
FAKE_DISPLAY = STUB_BASE + 0x800
FAKE_DISPLAY_VT = STUB_BASE + 0x400
SCRATCH = 0x81000000                    # SDRAM area for structures we build
STACK_TOP = 0x20058000

MAPS = [  # (base, size)
    (0x00000000, 0x20000),      # ITCM
    (0x20000000, 0x58000),      # DTCM
    (0x20200000, 0x8000),       # OCRAM: the one FlexRAM bank set up as OCRAM (32 KB).
                                # Nothing else exists here on this unit: an access to
                                # 0x20280000 froze the device.
    (0x60000000, 0x200000),     # FlexSPI flash window
    (0x80000000, 0x4000000),    # SDRAM 64 MB
    (STUB_BASE, 0x1000),
]


class Machine:
    def __init__(self, ptf_path: Path):
        ptf = read_ptf(ptf_path)
        assert ptf.base == FLASH_BASE, f"unexpected base {ptf.base:#x}"
        self.image = bytes(ptf.image)
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        for base, size in MAPS:
            self.uc.mem_map(base, size)
        self.uc.mem_write(FLASH_BASE, self.image)
        self._startup_copies()
        self.display_list: list[int] = []
        self.trace_unmapped = True
        self._install_stubs()
        self.uc.hook_add(UC_HOOK_MEM_INVALID, self._on_invalid)
        self.scratch_next = SCRATCH

    # -- memory helpers -------------------------------------------------------
    def r32(self, a: int) -> int:
        return struct.unpack("<I", self.uc.mem_read(a, 4))[0]

    def w32(self, a: int, v: int) -> None:
        self.uc.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))

    def w16(self, a: int, v: int) -> None:
        self.uc.mem_write(a, struct.pack("<H", v & 0xFFFF))

    def w8(self, a: int, v: int) -> None:
        self.uc.mem_write(a, bytes([v & 0xFF]))

    def write(self, a: int, data: bytes) -> None:
        self.uc.mem_write(a, bytes(data))

    def alloc(self, size: int, align: int = 8) -> int:
        a = (self.scratch_next + align - 1) & ~(align - 1)
        self.scratch_next = a + size
        self.uc.mem_write(a, b"\0" * size)
        return a

    # -- setup ----------------------------------------------------------------
    def _startup_copies(self) -> None:
        for t in range(*DATA_TABLE, 12):
            load, exe, n = struct.unpack_from("<III", self.image, t - FLASH_BASE)
            if n:
                self.uc.mem_write(exe, self.image[load - FLASH_BASE:load - FLASH_BASE + n])
        # BSS is already zero: Unicorn maps fresh memory as zeros.

    def _install_stubs(self) -> None:
        bx_lr = struct.pack("<H", 0x4770)
        for a in (STUB_DL_ADVANCE, STUB_DL_WRITE, STUB_RET):
            self.uc.mem_write(a, bx_lr)
        self.w32(FAKE_DISPLAY, FAKE_DISPLAY_VT)
        self.w32(FAKE_DISPLAY_VT + 0x14, STUB_DL_ADVANCE | 1)
        self.w32(FAKE_DISPLAY_VT + 0x18, STUB_DL_WRITE | 1)
        self.w32(DISPLAY_PTR, FAKE_DISPLAY)
        self.uc.hook_add(UC_HOOK_CODE, self._on_dl_write, begin=STUB_DL_WRITE, end=STUB_DL_WRITE)

    def add_stub(self, func) -> int:
        """Return a Thumb address that runs `func(machine)` in Python and returns.

        `func` reads arguments with `arg(i)` and its return value (if not None) goes
        to r0. Used to stand in for firmware methods (e.g. file system calls).
        """
        if not hasattr(self, "_stub_next"):
            self._stub_next = STUB_BASE + 0x300
        a = self._stub_next
        self._stub_next += 4
        assert a < FAKE_DISPLAY_VT, "out of stub slots"
        self.uc.mem_write(a, struct.pack("<H", 0x4770))      # bx lr

        def hook(uc, address, size, user):
            r = func(self)
            if r is not None:
                uc.reg_write(UC_ARM_REG_R0, r & 0xFFFFFFFF)
        self.uc.hook_add(UC_HOOK_CODE, hook, begin=a, end=a)
        return a | 1

    def stub_function(self, addr: int, func) -> None:
        """Replace a firmware function: run `func(machine)` at `addr` and return."""
        def hook(uc, address, size, user):
            r = func(self)
            if r is not None:
                uc.reg_write(UC_ARM_REG_R0, r & 0xFFFFFFFF)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        self.uc.hook_add(UC_HOOK_CODE, hook, begin=addr & ~1, end=addr & ~1)

    def arg(self, i: int) -> int:
        """Argument i of the current call (r0-r3, then the stack)."""
        if i < 4:
            return self.uc.reg_read((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)[i])
        return self.r32(self.uc.reg_read(UC_ARM_REG_SP) + 4 * (i - 4))

    def cstr(self, a: int, limit: int = 512) -> str:
        return bytes(self.uc.mem_read(a, limit)).split(b"\0")[0].decode("latin-1")

    def _on_dl_write(self, uc, address, size, user):
        self.display_list.append(uc.reg_read(UC_ARM_REG_R1))

    def _on_invalid(self, uc, access, address, size, value, user):
        pc = uc.reg_read(UC_ARM_REG_PC)
        print(f"invalid memory access {address:#010x} (size {size}) at pc={pc:#010x}", file=sys.stderr)
        return False

    # -- calls ----------------------------------------------------------------
    def call(self, func: int, *args: int, max_insns: int = 5_000_000) -> int:
        regs = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
        sp = STACK_TOP - 0x100
        stack_args = list(args[4:])
        sp -= 4 * len(stack_args)
        for i, v in enumerate(stack_args):
            self.w32(sp + 4 * i, v)
        for r, v in zip(regs, args[:4]):
            self.uc.reg_write(r, v & 0xFFFFFFFF)
        self.uc.reg_write(UC_ARM_REG_SP, sp)
        self.uc.reg_write(UC_ARM_REG_LR, STUB_RET | 1)
        try:
            self.uc.emu_start(func | 1, STUB_RET, count=max_insns)
        except UcError as e:
            pc = self.uc.reg_read(UC_ARM_REG_PC)
            raise RuntimeError(f"emulation stopped at pc={pc:#010x}: {e}") from e
        pc = self.uc.reg_read(UC_ARM_REG_PC)
        if pc != STUB_RET:
            raise RuntimeError(f"function did not return (pc={pc:#010x}); raise max_insns?")
        return self.uc.reg_read(UC_ARM_REG_R0)
