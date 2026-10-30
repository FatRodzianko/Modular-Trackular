"""The firmware's save request in the emulator, and the Save As checks shared by the
patches that keep a settings file per project (swing, humanize, interpolate, masterfx;
test_slicethru.py uses save() too).

The save request 0x60114e34 (fm + 0x8c, path, name) is Save As with a path and a name:
it copies the path, then the name (bl strncpy at 0x60114eaa, Save As only), and starts
the save (file manager busy, state 0x0d). With neither it is a plain Save. A new
project's first save is a Save As. The firmware copies only its own files to the new
folder (FINDINGS section 25), so a patch must write its own file there.
"""
from __future__ import annotations

from pathlib import Path

from fakefs import FILEMAN, PROJECT_NAME

SAVE_REQUEST = 0x60114E34
FM_STATE, FM_OP = FILEMAN + 0x13D, FILEMAN + 0x13F
WHERE = "Projects/User"


def save(m, tick, name: str | None, where: str = WHERE, settle: int = 30) -> int:
    """The firmware's save request: Save As with a path and a name, Save with neither;
    then screen updates while the save runs (file manager busy), and `settle` more once
    it is done. Returns the request's result."""
    p = n = 0
    if name is not None:
        p, n = m.alloc(256), m.alloc(64)
        m.write(p, where.encode() + b"\0")
        m.write(n, name.encode() + b"\0")
    m.w8(FM_STATE, 0)
    m.w8(FM_OP, 0)
    ok = m.call(SAVE_REQUEST, FILEMAN + 0x8C, p, n, max_insns=50_000_000)
    tick(3)
    m.w8(FM_STATE, 0)
    m.w8(FM_OP, 0)
    tick(settle)
    return ok


def save_as_checks(check, m, fs, tick, fname: str, get, put, default, a, b, encode, delay: int) -> None:
    """Save As and new projects through the firmware's own save request, for a patch whose
    settings file is <project folder>/<fname>. get() returns the settings, put(v) sets
    them (as the Config menu does); default is a new project's, a and b two others;
    encode(v) is the file a patch writes for v; delay is its SAVE_DELAY."""
    def path(name):
        return f"{WHERE}/{name}/{fname}"

    def io(mode):
        return len([x for x in fs.log if f"{fname} mode {mode}" in x])

    print(f"Save As and new projects ({fname}, the firmware's save request 0x60114e34)")
    settle = delay * 2 + 5
    fs.set_project(Path("."), "New Project", "")    # as the firmware's new project 0x60117c8c
    tick()
    check(get() == default, "a new project (\"New Project\", no path): the defaults")
    put(a)
    log0 = len(fs.log)
    tick(settle)
    check(not any(fname in x for x in fs.log[log0:]), "while it has no folder: nothing read or written")
    ok = save(m, tick, "SA1", settle=settle)
    check(ok and m.cstr(PROJECT_NAME) == "SA1" and get() == a and fs.overlay.get(path("SA1")) == encode(a),
          "its first save (a Save As): the settings kept and written to its folder")
    ok = save(m, tick, "SA2", settle=settle)
    check(ok and get() == a and fs.overlay.get(path("SA2")) == encode(a) and fs.overlay.get(path("SA1")) == encode(a),
          "Save As from a saved project: the settings kept, written to the new folder, the old file left")
    fs.put(path("SA3"), encode(b))
    ok = save(m, tick, "SA3", settle=settle)
    check(ok and get() == a and fs.overlay.get(path("SA3")) == encode(a),
          "Save As over a project that has its own file: the saved project's settings, written over it")
    n = io(3)
    ok = save(m, tick, None, settle=settle)
    check(ok and m.cstr(PROJECT_NAME) == "SA3" and get() == a and io(3) == n,
          "a plain Save: the same project, the settings kept, nothing written")
    long_name = "L" * 40
    ok = save(m, tick, long_name, settle=settle)
    kept = m.cstr(PROJECT_NAME)
    check(ok and get() == a and fs.overlay.get(path(kept)) == encode(a),
          f"Save As to a {len(long_name)}-character name (the firmware keeps {len(kept)}): the settings kept and written")
    fs.put(path("SB"), encode(b))
    m.w8(FM_STATE, 5)
    r = io(0)
    fs.set_project(Path("."), "SB", WHERE)
    tick()
    check(get() == default and io(0) == r,
          "another project opened while the file manager is busy: the defaults at once, its file not read yet")
    m.w8(FM_STATE, 0)
    tick()
    check(get() == b, "once it is idle: its file read")
    m.write(PROJECT_NAME, b"\0")                    # the name cleared while Workspace loads
    tick()
    fs.set_project(Path("."), "SA2", WHERE)
    tick(2)
    check(get() == a, "a project reopened, its name cleared and restored on the way: its own file's settings")


PROJECT_UPDATE_SLOT, PROJECT_UPDATE = 0x6004193C, 0x600EAD58    # project screen vtable: update 0x600ead59


def new_project_checks(check, m, fs, tick, fname: str, get, default, a, encode, delay: int) -> None:
    """New Project, then Save As at once, both on the project screen: the new project
    saves the defaults, not the old project's settings (resample rs6's bug). Only the
    project screen's update (stubbed stock method, any patch hook chained in front) runs
    from New Project until the save is done; then tick() (the patch's usual screen)."""
    def path(name):
        return f"{WHERE}/{name}/{fname}"

    print(f"New Project then Save As on the project screen ({fname})")
    m.stub_function(PROJECT_UPDATE, lambda mm: 0)
    hook, screen = m.r32(PROJECT_UPDATE_SLOT), m.alloc(64)

    def project_tick(n=1):
        for _ in range(n):
            m.call(hook, screen, max_insns=50_000_000)

    settle = delay * 2 + 5
    fs.put(path("NPA"), encode(a))
    fs.set_project(Path("."), "NPA", WHERE)
    tick(2)
    check(get() == a, "a project with its own file: its settings")
    fs.set_project(Path("."), "New Project", "")    # as the firmware's new project 0x60117c8c
    project_tick()
    check(get() == default, "New Project, only the project screen's update since: the defaults")
    ok = save(m, project_tick, "NP1", settle=settle)
    tick(settle)
    check(ok and m.cstr(PROJECT_NAME) == "NP1" and get() == default and fs.overlay.get(path("NP1")) == encode(default),
          "then Save As at once, then the patch's own screen: the defaults, written to the new folder "
          f"(now {get()}, file {'none' if fs.overlay.get(path('NP1')) is None else fs.overlay.get(path('NP1')).hex()})")
