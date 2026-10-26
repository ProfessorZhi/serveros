"""One local migration actuator across per-step Work transactions.

Native kernel locks release on process exit. The persistent lock file is only
a rendezvous, never an authority registry. Its inode must not be replaced or
moved; migration rejects every operation overlapping the control state root.
"""
from contextlib import contextmanager
import os
from pathlib import Path


NAME = ".migration-actuator.lock"


def initialize(state_dir):
    """Called under Work BEGIN IMMEDIATE during writable Store setup only."""
    path = Path(state_dir) / NAME
    try:
        with path.open("xb") as handle:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        pass
    if path.is_symlink() or not path.is_file() or path.stat().st_size < 1:
        raise ValueError("MIGRATION_LOCK_INVALID: existing lock requires explicit reconciliation")


@contextmanager
def actuator(store):
    path = store.state_dir / NAME
    if path.is_symlink() or not path.is_file():
        raise ValueError("MIGRATION_LOCK_INVALID: initialized control lock required")
    with path.open("r+b") as handle:
        locked = False
        try:
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise ValueError("MIGRATION_BUSY: another migration actuator owns this store") from exc
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise ValueError("MIGRATION_BUSY: another migration actuator owns this store") from exc
            locked = True
            current, opened = path.stat(), os.fstat(handle.fileno())
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError("MIGRATION_LOCK_INVALID: lock inode changed")
            yield
        finally:
            if locked:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
