"""Reproduce the Dell/fjd host without reading another user's private checkout.

The private legacy Work Engine checkout is absent on Dell/fjd.  On Tokyo it is
present, so the two portability suites used to pass here and setup-error there.
This pytest plugin makes Tokyo behave like the portable host for a bounded run:
any `open`, `os.*` or `subprocess.*` audit event whose path/argv touches a
configured private root raises `PRIVATE_LEGACY_SOURCE_ACCESS`.

The roots are supplied by the operator, never stored in this repository:

    SERVEROS_PORTABILITY_GUARD=1 \
    SERVEROS_PRIVATE_LEGACY_ROOTS=/owner/private/root:/second/root \
    PYTHONPATH=tests/v1_portability \
    python -m pytest -p private_path_guard tests/test_...

The guard does not delete, move or read anything.  It only refuses access, so a
suite that is genuinely public-safe runs unchanged under it.
"""
from __future__ import annotations

import os
import sys

GUARD_ENV = "SERVEROS_PORTABILITY_GUARD"
ROOTS_ENV = "SERVEROS_PRIVATE_LEGACY_ROOTS"

_WATCHED = frozenset({
    "open",
    "os.open",
    "os.listdir",
    "os.scandir",
    "os.system",
    "os.exec",
    "subprocess.Popen",
})


def _normalise(value) -> str | None:
    try:
        raw = os.fsdecode(os.fspath(value))
    except (TypeError, ValueError):
        return None
    if not raw:
        return None
    return os.path.normcase(os.path.abspath(raw))


def _configured_roots() -> tuple[str, ...]:
    raw = os.environ.get(ROOTS_ENV, "")
    return tuple(
        root for root in (_normalise(part) for part in raw.split(os.pathsep))
        if root
    )


def _values(event: str, args: tuple) -> list[str]:
    """The path/argv/cwd values the audited call was actually handed.

    An environment mapping is deliberately NOT scanned: the guard itself is
    configured through `SERVEROS_PRIVATE_LEGACY_ROOTS`, so the private root's
    own value would otherwise be mistaken for a path the child is opening.
    """
    values: list[str] = []

    def visit(value) -> None:
        if isinstance(value, (str, bytes, os.PathLike)):
            values.append(os.fsdecode(os.fspath(value)))
        elif isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                visit(item)

    if event == "open":
        candidates = args[:1]
    elif event in {"os.open", "os.listdir", "os.scandir", "os.system"}:
        candidates = args[:1]
    elif event == "os.exec":
        candidates = list(args[:1])
        if len(args) > 1:
            candidates.extend(args[1:2])
    elif event == "subprocess.Popen":
        # `(executable, args, cwd, env)` — the executable, argv and cwd.
        candidates = list(args[:1])
        if len(args) > 1:
            candidates.extend(args[1:2])
        if len(args) > 2:
            candidates.extend(args[2:3])
    else:
        candidates = []

    for candidate in candidates:
        visit(candidate)
    return values


def _inside(value: str, root: str) -> bool:
    candidate = _normalise(value)
    if candidate is None:
        return False
    return candidate == root or candidate.startswith(root + os.sep)


def install_hook() -> None:
    roots = _configured_roots()
    if not roots:
        raise RuntimeError(f"{ROOTS_ENV} names no private root")

    def hook(event: str, args: tuple) -> None:
        if event not in _WATCHED:
            return
        for value in _values(event, args):
            for root in roots:
                if _inside(value, root):
                    raise PermissionError(
                        "PRIVATE_LEGACY_SOURCE_ACCESS: "
                        f"{event} touched an owner-private legacy checkout. "
                        "The public portability suite must never read it; use "
                        "the in-tree workengine or a pinned public fixture.")

    sys.addaudithook(hook)


def pytest_configure(config) -> None:
    if os.environ.get(GUARD_ENV) != "1":
        return
    import pytest

    if not _configured_roots():
        raise pytest.UsageError(
            f"{GUARD_ENV}=1 requires {ROOTS_ENV} with at least one private root")
    install_hook()
