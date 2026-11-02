"""Results of stock-firmware runs that come out the same for every build, shared between
the jobs of one test run.

The deep tier runs synths' and PHASE's full tests on 11 builds, and each works out the
same stock-firmware figures (stock VAP's voice allocation, the stock half of synths'
output comparisons). tools/run_tests.py names an empty folder in MT_STOCK_SHARE at the
start of every run; the first job to need a figure works it out and leaves it there,
while jobs needing it at the same time wait for it (an OS file lock, released if a job
dies), and later ones read it. Nothing is kept from one run to the next. Without
MT_STOCK_SHARE (a test run by hand), every job works its figures out itself.

MT_STOCK_SHARE_VERIFY=1: work every shared figure out again anyway and stop with an
error if it differs from the one shared (the check that sharing changes no result).
"""
from __future__ import annotations

import hashlib
import inspect
import os
import pickle
import time
from contextlib import contextmanager
from pathlib import Path


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextmanager
def _lock(path: Path):
    """Hold an OS lock on `path` (waiting for it); the OS drops it if the process dies."""
    with open(path, "a+b") as f:
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.5)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _read(path: Path):
    try:
        return True, pickle.loads(path.read_bytes())
    except (OSError, EOFError, pickle.UnpicklingError):
        return False, None


def shared(stock: Path, what: str, compute):
    """compute() on the stock firmware, or the result another job of this run got for the
    same stock file, calling test file and `what` (which must name every input)."""
    folder = os.environ.get("MT_STOCK_SHARE")
    if not folder:
        return compute()
    caller = Path(inspect.stack()[1].filename)
    key = hashlib.sha256(f"{_sha(stock)}|{_sha(caller)}|{caller.name}|{what}".encode()).hexdigest()
    path = Path(folder) / f"{key}.pickle"
    Path(folder).mkdir(parents=True, exist_ok=True)
    with _lock(path.with_suffix(".lock")):
        found, got = _read(path)
        if not found:
            got = compute()
            part = path.with_name(f"{path.name}.{os.getpid()}")
            part.write_bytes(pickle.dumps(got))
            os.replace(part, path)
            return got
    if os.environ.get("MT_STOCK_SHARE_VERIFY"):
        fresh = compute()
        if not _same(fresh, got):
            raise SystemExit(f"stock_share: {caller.name}: {what}: the shared result differs from a fresh one")
        print(f"    (stock_share: {what}: the shared result equals a fresh one)")
    return got


def _same(a, b) -> bool:
    """Equal, value for value (numpy arrays included)."""
    import numpy as np
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return isinstance(a, np.ndarray) and isinstance(b, np.ndarray) and a.dtype == b.dtype and \
            a.shape == b.shape and np.array_equal(a, b)
    if isinstance(a, dict):
        return isinstance(b, dict) and a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        return type(a) is type(b) and len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b
