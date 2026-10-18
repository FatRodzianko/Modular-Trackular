"""A fake Tracker+ file system object for the emulator.

Implements the methods the firmware (and patches) call on the FS object that
*(0x2001e488) points to (vtable layout of class 0x60038224):
  +0x08 exists(fs, path)                       -> bool
  +0x14 remove(fs, path)
  +0x2c open(&res, fs, path, mode, x)          res = {u32 handle, u8 ok}; mode 0 read,
                                               mode 3 create/truncate for writing
  +0x30 read(fs, buf, size, handle)            -> bytes read, -1 on error
  +0x34 write(fs, buf, size, handle)           -> bytes written
  +0x40 close(fs, handle, x)
Paths are firmware paths ("Workspace/patterns/pattern_01.mtp"); `Workspace/` maps to
a host folder, normally a project folder from the SD card. Every write (and `put`)
goes to an in-memory overlay that reads check first, so host files are never
modified. Paths under Projects/ exist only in the overlay.
"""
from __future__ import annotations

import struct
from pathlib import Path

FILEMAN = 0x2001E488            # global: *FILEMAN = FS object
PROJECT_NAME = FILEMAN + 0x340
PROJECT_PATH = FILEMAN + 0x241


class FakeFS:
    def __init__(self, m, workspace: Path):
        self.m = m
        self.workspace = Path(workspace)
        self.files: dict[int, tuple[bytes, int]] = {}   # handle -> (data, position)
        self.next_handle = 0x100
        self.log: list[str] = []
        self.overlay: dict[str, bytes] = {}             # firmware path -> contents
        self.read_sizes: list[int] = []
        self.write_sizes: list[int] = []
        self.writes_opened = 0
        self.removed: list[str] = []
        self.paths: dict[int, str | None] = {}
        self.obj = m.alloc(0x10)
        vt = m.alloc(0x80)
        for off in range(0, 0x80, 4):
            m.w32(vt + off, m.add_stub(self._unexpected(off)))
        m.w32(vt + 0x08, m.add_stub(self._exists))
        m.w32(vt + 0x14, m.add_stub(self._remove))
        m.w32(vt + 0x34, m.add_stub(self._write))
        m.w32(vt + 0x2C, m.add_stub(self._open))
        m.w32(vt + 0x30, m.add_stub(self._read))
        m.w32(vt + 0x40, m.add_stub(self._close))
        m.w32(self.obj, vt)
        m.w32(FILEMAN, self.obj)

    def set_project(self, workspace: Path, name: str, path: str):
        self.workspace = Path(workspace)
        self.m.write(PROJECT_NAME, name.encode()[:63] + b"\0")
        self.m.write(PROJECT_PATH, path.encode()[:254] + b"\0")

    def put(self, fw_path: str, data: bytes) -> None:
        self.overlay[fw_path] = bytes(data)

    def read_any(self, path: str) -> bytes | None:
        """Contents of a firmware path (overlay first), a Workspace/ file of the host
        project folder, or a host path given by a test. Projects/... exists only in
        the overlay."""
        if path in self.overlay:
            return self.overlay[path]
        if path.startswith("Workspace/"):
            p = self._host(path)
        elif path.startswith("Projects/"):
            return None                     # the fake card's project folders: overlay only
        else:
            p = Path(path)                  # a host path given by a test (e.g. a copy source)
        return p.read_bytes() if p and p.exists() else None

    def _host(self, fw_path: str) -> Path | None:
        if not fw_path.startswith("Workspace/"):
            return None
        return self.workspace / fw_path[len("Workspace/"):]

    def _unexpected(self, off):
        def f(m):
            raise RuntimeError(f"unexpected FS method +{off:#x} called")
        return f

    def _exists(self, m):
        return int(self.read_any(m.cstr(m.arg(1))) is not None)

    def _remove(self, m):
        path = m.cstr(m.arg(1))
        self.removed.append(path)
        self.overlay.pop(path, None)

    def _open(self, m):
        res, path, mode = m.arg(0), m.cstr(m.arg(2)), m.arg(3)
        handle = 0
        if mode == 0:
            data = self.read_any(path)
            ok = data is not None
        elif mode == 3:
            data, ok = b"", True
            self.overlay[path] = b""
            self.writes_opened += 1
        else:
            data, ok = None, False
        if ok:
            handle = self.next_handle
            self.next_handle += 0x10
            self.files[handle] = (data, 0)
            self.paths[handle] = path if mode == 3 else None
        self.log.append(f"open {path} mode {mode} -> {'ok' if ok else 'fail'}")
        m.write(res, struct.pack("<IB", handle, int(ok)))
        return res

    def _read(self, m):
        buf, size, handle = m.arg(1), m.arg(2), m.arg(3)
        self.read_sizes.append(size)
        if handle not in self.files:
            return -1
        data, pos = self.files[handle]
        chunk = data[pos:pos + size]
        m.write(buf, chunk)
        self.files[handle] = (data, pos + len(chunk))
        return len(chunk)

    def _write(self, m):
        buf, size, handle = m.arg(1), m.arg(2), m.arg(3)
        self.write_sizes.append(size)
        if handle not in self.files or not self.paths.get(handle):
            return -1
        data, pos = self.files[handle]
        data = data + bytes(self.m.uc.mem_read(buf, size))
        self.files[handle] = (data, len(data))
        return size

    def _close(self, m):
        handle = m.arg(1)
        path = self.paths.pop(handle, None)
        if path is not None and handle in self.files:
            self.overlay[path] = self.files[handle][0]
        self.files.pop(handle, None)
