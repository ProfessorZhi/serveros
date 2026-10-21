#!/usr/bin/env python3
"""One writer per surface, ACROSS processes — a real file lock, not a hope.

WHY ATOMIC REPLACE IS NOT ENOUGH
--------------------------------
`SurfaceStore._write` writes a document atomically (write-temp, rename). That
makes a single WRITE indivisible; it does NOT make a read-modify-write indivisible.
Two writers — a half-hour timer and an alert path — can both read the same
document, both decide, and the second rename silently discards the first one's
pending intent. Atomic replace is not compare-and-swap, and calling it
«concurrency-safe» is how a live card gets a second message.

WHAT THIS IS
------------
An `flock(2)` advisory lock on a per-surface file, held for the WHOLE
intent → sender → ACK → commit sequence. `flock` is the right primitive here for
one reason that matters: **the kernel releases it when the holder dies**, so a
crashed writer cannot wedge the surface — no stale lock file to reap, no pid
liveness guess.

    owner-private    the directory is 0700 and the file 0600, under this user's \
                     own state root; another account cannot open it at all
    non-blocking     `LOCK_EX | LOCK_NB`; a second writer is told BUSY rather \
                     than queued, because queueing a 30-minute tick behind an \
                     alert is a latency decision nobody made
    degraded, not dead   a filesystem that cannot do `flock` raises, and the \
                     caller must treat that as «cannot prove exclusivity» — it \
                     is NOT a licence to proceed unlocked

WINDOWS IS NOT COVERED. `fcntl` does not exist there; the platform that needs
`LockFileEx` is a separate implementation, and pretending otherwise would be the
same class of claim this module exists to refuse.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path

try:  # POSIX only; the absence is reported rather than papered over
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None


class LockUnavailable(RuntimeError):
    """The platform cannot provide an exclusive file lock."""


class SurfaceLock:
    """An exclusive, per-surface lock held by ONE process at a time.

    Usage is a context manager, because the whole point is the SCOPE: the lock
    must span the decision, the send and the commit, and a lock released between
    two of those is a lock that bought nothing.

        with SurfaceLock(path) as lock:
            if not lock.acquired:
                return lock.busy_verdict()
            ...intent -> send -> ack -> commit...
    """

    def __init__(self, path) -> None:
        if fcntl is None:  # pragma: no cover - Windows
            raise LockUnavailable(
                "exclusive surface locking needs `fcntl`; this platform does not "
                "provide it, and proceeding without a lock is not an option")
        self.path = Path(path)
        self._handle = None
        self.acquired = False

    # -- the lock -----------------------------------------------------------

    def acquire(self, *, blocking: bool = False) -> bool:
        """Take the lock. NON-BLOCKING by default: contention is reported.

        A blocking default would let a half-hour tick wait behind an alert with
        nobody having decided that was acceptable, so the caller has to ask for
        it. The return value is the answer either way.
        """
        if self.acquired:
            return True
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass  # an existing directory we cannot chmod is still ours to use
        flags = os.O_RDWR | os.O_CREAT
        descriptor = os.open(self.path, flags, 0o600)
        handle = os.fdopen(descriptor, "r+")
        try:
            fcntl.flock(handle.fileno(),
                        fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError as exc:
            handle.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                return False          # somebody else holds it: BUSY, not an error
            raise
        self._handle = handle
        self.acquired = True
        return True

    def release(self) -> None:
        """Release the lock. The kernel also does this if the process dies."""
        if self._handle is not None:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._handle.close()
                self._handle = None
        self.acquired = False

    def __enter__(self) -> "SurfaceLock":
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()

    # -- the verdict a caller reports when it could not get in ---------------

    def busy_verdict(self, *, surface: str, at: str) -> dict:
        """What the surface says when another writer holds the lock.

        A SKIP with its own reason and NOT a send: the other writer is already
        doing this work, and a second send is the outcome the lock exists to
        prevent. It is not an error — the tick simply did not own the surface.
        """
        return {"schema": "serveros/surface-lock.v1", "surface": surface,
                "action": "SKIP", "reason": "BUSY",
                "at": at, "lock": str(self.path),
                "says": ("另一个进程正持有该卡面的独占锁：本拍不决策、不发送。"
                         "这不是失败——那一个进程正在做这件事")}


def lock_path(state_path, *, surface: str = "live") -> Path:
    """Where a surface's lock lives, given the ledger's own anchor.

    A sibling of the card documents under `<state>/cards/locks/`, so the lock
    that guards a file sits beside it and not in a root of its own.
    """
    return Path(state_path).parent.parent / "cards" / "locks" / f"{surface}.lock"
