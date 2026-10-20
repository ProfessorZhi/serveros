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
import posixpath
import re
import sys

GUARD_ENV = "SERVEROS_PORTABILITY_GUARD"
ROOTS_ENV = "SERVEROS_PRIVATE_LEGACY_ROOTS"

_WINDOWS_DRIVE = re.compile(r"^([A-Za-z]):(?:[\\/](.*))?$")
_UNC = re.compile(r"^(?:\\\\|//)([^\\/]+)[\\/]([^\\/]+)(?:[\\/](.*))?$")
_DEVICE_PREFIXES = ("\\\\.\\", "//./")

_WATCHED = frozenset({
    "open",
    "os.open",
    "os.listdir",
    "os.scandir",
    "os.system",
    "os.exec",
    "subprocess.Popen",
})


def _windows_form(slash: str) -> tuple[str, str] | None:
    """Canonical form for a drive, UNC or device path written with `/`."""
    if slash.startswith("//"):
        unc = _UNC.match(slash)
        if unc is None:
            return ("device", slash.casefold())
        server, share, rest = unc.group(1), unc.group(2), unc.group(3) or ""
        path = posixpath.normpath(
            "/" + "/".join(part for part in (share, rest) if part))
        return ("unc", f"//{server}{path}".casefold())
    drive = _WINDOWS_DRIVE.match(slash)
    if drive is None:
        return None
    letter = drive.group(1).lower()
    rest = drive.group(2)
    if rest is None:
        # `D:` or `D:relative` is drive-relative, never the absolute root.
        return ("drive-relative", f"{letter}:")
    return ("win", f"{letter}:{posixpath.normpath('/' + rest)}".casefold())


def _normalise(value) -> tuple[str, str] | None:
    """A comparison key that does not depend on the host's separator style.

    A drive/UNC path is compared case-insensitively and with `/` separators, on
    every host.  A POSIX path keeps the host's `normcase` semantics (identity
    on Linux, case-folded on Windows).  Resolution is lexical (`normpath`), so
    a path string under the private root is refused even if a symlink would
    later resolve elsewhere: the access still names the private root.
    """
    try:
        raw = os.fsdecode(os.fspath(value))
    except (TypeError, ValueError):
        return None
    if not raw:
        return None
    # The long-path prefix names the same file; a device namespace does not.
    if raw.startswith("\\\\?\\"):
        raw = raw[4:]
    elif raw.startswith("//?/"):
        raw = raw[4:]
    elif raw.startswith(_DEVICE_PREFIXES):
        return ("device", raw.casefold().replace("\\", "/"))

    slash = raw.replace("\\", "/")
    form = _windows_form(slash)
    if form is not None:
        return form

    absolute = os.path.abspath(raw)
    slash = absolute.replace("\\", "/")
    form = _windows_form(slash)
    if form is not None:
        return form
    if os.name == "nt":
        return ("win-native", os.path.normcase(absolute).replace("\\", "/").casefold())
    return ("posix", os.path.normcase(absolute))


def _configured_roots() -> tuple[str, ...]:
    """Raw roots from the environment; `_inside` normalizes both sides.

    The list is split on the NATIVE separator (`;` on Windows, `:` elsewhere).
    The synthetic Windows-path tests call `_inside` directly instead of asking
    a Linux host to split a `D:\\...` root on `:`.
    """
    raw = os.environ.get(ROOTS_ENV, "")
    return tuple(part for part in raw.split(os.pathsep) if part)


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
    """Whether `value` names `root` or a descendant, in the same path family."""
    candidate = _normalise(value)
    expected = _normalise(root)
    if candidate is None or expected is None:
        return False
    if candidate[0] != expected[0]:
        return False
    c_path, r_path = candidate[1], expected[1]
    if c_path == r_path:
        return True
    return c_path.startswith(r_path.rstrip("/") + "/")


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
