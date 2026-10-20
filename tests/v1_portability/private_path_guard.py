r"""Reproduce the Dell/fjd host without reading another user's private checkout.

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

SCOPE AND LIMITS (explicit)
---------------------------
This is a TEST guard, not a filesystem sandbox.

* A path is compared in every form that can name the same file: drive/UNC/
  device spellings (`\\?\D:`, `\\?\UNC\`, `\\.\D:`), lexical normalization, and
  `os.path.realpath` for a symlink alias.  `realpath` is resolved at CHECK time;
  a racing rename between the check and the access can still escape (TOCTOU).
* A device namespace that is not a plain drive (`\\.\PhysicalDrive0`,
  `\\?\Volume{...}`, `\\?\GLOBALROOT\...`) cannot be proven equivalent to a
  root.  It is refused conservatively rather than treated as unrestricted.
* An environment mapping is never scanned; only the actual audited path, argv
  and CWD are.  The guard is configured through an environment variable, so
  scanning the environment would make the guard refuse itself.
"""
from __future__ import annotations

import ntpath
import os
import posixpath
import re
import sys

GUARD_ENV = "SERVEROS_PORTABILITY_GUARD"
ROOTS_ENV = "SERVEROS_PRIVATE_LEGACY_ROOTS"

_DRIVE = re.compile(r"^([A-Za-z]):(?:[\\/](.*))?$")
_WINDOWS_HINT = re.compile(r"^(?:[A-Za-z]:|\\\\|//)")

_WATCHED = frozenset({
    "open",
    "os.open",
    "os.listdir",
    "os.scandir",
    "os.system",
    "os.exec",
    "subprocess.Popen",
})


def _slashes(value: str) -> str:
    return value.replace("\\", "/")


def _looks_windows(raw: str) -> bool:
    """Whether the string uses a Windows drive, UNC or device prefix."""
    return bool(_WINDOWS_HINT.match(raw))


def _drive_form(slash: str) -> tuple[str, str] | None:
    match = _DRIVE.match(slash)
    if match is None:
        return None
    letter = match.group(1).lower()
    rest = match.group(2)
    if rest is None:
        # `D:` or `D:relative` is drive-relative, never the absolute root.
        return ("drive-relative", f"{letter}:")
    return ("win", f"{letter}:{posixpath.normpath('/' + rest)}".casefold())


def _unc_form(rest: str) -> tuple[str, str] | None:
    """`server/share/rest` -> `//server/share/rest`, case-folded."""
    parts = [part for part in rest.split("/") if part not in ("", ".")]
    if len(parts) < 2:
        # `\\server` alone names no share; treat as a device/ambiguous form.
        return None
    server, share = parts[0], parts[1]
    tail = parts[2:]
    path = posixpath.normpath("/" + "/".join([share, *tail]))
    return ("unc", f"//{server}{path}".casefold())


def _windows_forms(raw: str) -> tuple[list[tuple[str, str]], bool]:
    """Every Windows spelling that can name the same file, plus ambiguity."""
    slash = _slashes(raw)
    folded = slash.casefold()
    forms: list[tuple[str, str]] = []
    ambiguous = False

    if folded.startswith("//?/unc/"):
        # `\\?\UNC\server\share\...` is the long-path form of the UNC root.
        unc = _unc_form(slash[8:])
        if unc is None:
            ambiguous = True
        else:
            forms.append(unc)
    elif folded.startswith("//?/"):
        # `\\?\D:\...` is the drive path; anything else is a device namespace.
        drive = _drive_form(slash[4:])
        if drive is None:
            ambiguous = True
        else:
            forms.append(drive)
    elif folded.startswith("//./"):
        # `\\.\D:\...` is the same drive; `\\.\PhysicalDrive0` is not.
        drive = _drive_form(slash[4:])
        if drive is None:
            ambiguous = True
        else:
            forms.append(drive)
    elif folded.startswith("//"):
        unc = _unc_form(slash[2:])
        if unc is None:
            ambiguous = True
        else:
            forms.append(unc)
    else:
        drive = _drive_form(slash)
        if drive is not None:
            forms.append(drive)

    return forms, ambiguous


def _posix_forms(raw: str) -> list[tuple[str, str]]:
    """POSIX-family forms, independent of the host's native path rules.

    `posixpath` is used explicitly so the same assertion is meaningful on a
    Windows host: a POSIX-family path is case-sensitive there too, and is not
    silently reinterpreted as the current drive.
    """
    try:
        absolute = posixpath.abspath(raw)
    except (OSError, ValueError):
        return []
    forms: list[tuple[str, str]] = []

    def add(path: str) -> None:
        # POSIX permits `//name` but it names the same path as `/name`.
        forms.append(("posix", re.sub(r"^/+", "/", posixpath.normcase(path))))

    add(absolute)
    try:
        real = posixpath.realpath(absolute)
    except (OSError, ValueError):
        real = None
    if real and real != absolute:
        add(real)
    return forms


def _native_windows_forms(raw: str) -> list[tuple[str, str]]:
    """Drive/UNC forms under Windows semantics (also testable on Linux)."""
    try:
        absolute = ntpath.abspath(raw)
    except (OSError, ValueError):
        return []
    windows, _ = _windows_forms(absolute.replace("\\", "/"))
    return windows


def _forms(value, *, windows: bool | None = None
           ) -> tuple[list[tuple[str, str]], bool]:
    """All comparable forms of one value, and whether it is device-ambiguous.

    `windows=None` means the native host.  An explicit `windows=False` keeps
    POSIX case sensitivity on any host; `windows=True` applies Windows
    drive/UNC semantics and case-folding.  Drive/UNC/device spellings always
    produce their Windows forms, because they are unambiguous.
    """
    if windows is None:
        windows = os.name == "nt"
    try:
        raw = os.fsdecode(os.fspath(value))
    except (TypeError, ValueError):
        return [], False
    if not raw:
        return [], False
    forms: list[tuple[str, str]] = []
    ambiguous = False
    if _looks_windows(raw):
        windows_forms, ambiguous = _windows_forms(raw)
        forms.extend(windows_forms)
    if windows:
        forms.extend(_native_windows_forms(raw))
    else:
        forms.extend(_posix_forms(raw))
    # Preserve order while dropping duplicates (e.g. the realpath is lexical).
    return list(dict.fromkeys(forms)), ambiguous


def _configured_roots() -> tuple[str, ...]:
    """Raw roots from the environment; comparison normalizes both sides.

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


def _inside(value: str, root: str, *, windows: bool | None = None) -> bool:
    """Whether ANY comparable form of `value` is `root` or a descendant."""
    candidate, _ = _forms(value, windows=windows)
    expected, _ = _forms(root, windows=windows)
    for c_family, c_path in candidate:
        for r_family, r_path in expected:
            if c_family != r_family:
                continue
            if c_path == r_path:
                return True
            if c_path.startswith(r_path.rstrip("/") + "/"):
                return True
    return False


def _must_refuse(value: str, roots, *, windows: bool | None = None) -> bool:
    """Inside a private root, or a device path that cannot be proven safe."""
    _, ambiguous = _forms(value, windows=windows)
    if ambiguous:
        return True
    return any(_inside(value, root, windows=windows) for root in roots)


def install_hook() -> None:
    roots = _configured_roots()
    if not roots:
        raise RuntimeError(f"{ROOTS_ENV} names no private root")

    def hook(event: str, args: tuple) -> None:
        if event not in _WATCHED:
            return
        for value in _values(event, args):
            if _must_refuse(value, roots):
                raise PermissionError(
                    "PRIVATE_LEGACY_SOURCE_ACCESS: "
                    f"{event} touched an owner-private legacy checkout. "
                    "The public portability suite must never read it; use the "
                    "in-tree workengine or a pinned public fixture.")

    sys.addaudithook(hook)


def pytest_configure(config) -> None:
    if os.environ.get(GUARD_ENV) != "1":
        return
    import pytest

    if not _configured_roots():
        raise pytest.UsageError(
            f"{GUARD_ENV}=1 requires {ROOTS_ENV} with at least one private root")
    install_hook()
