"""Compile owner-authored access and plan projects below named local roots.

This helper grants nothing, creates nothing and reads no credentials. Its input
must come from the owner configuration, never from a tool caller. Lexical mode
can inspect remote Windows configuration from Linux. Local checks are only an
observation: the actual project creator must enforce the boundary atomically.
"""
from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any

MAX_PERMISSION_MODE = "bypassPermissions"
_ROOT_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_DRIVE = re.compile(r"[A-Za-z]:\Z")
_RESERVED = re.compile(
    r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]|CONIN\$|CONOUT\$)\Z", re.I,
)


class OwnerAccessError(ValueError):
    """Owner configuration or a proposed project path failed validation."""


def _native_component(value: str) -> None:
    if (not value or value in {".", ".."} or value[-1] in ". "
            or any(c in '<>:"/\\|?*' or ord(c) < 32 or ord(c) == 127 for c in value)
            or _RESERVED.fullmatch(value.split(".", 1)[0].rstrip(" "))):
        raise OwnerAccessError("path contains an unsafe Windows component")


def _root_path(value: Any) -> PurePosixPath | PureWindowsPath:
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise OwnerAccessError("project root must be an absolute path string")
    if re.match(r"[A-Za-z]:", value) or value.startswith("\\"):
        normalized = value.replace("/", "\\")
        path = PureWindowsPath(normalized)
        if (not _DRIVE.fullmatch(path.drive) or not path.is_absolute()
                or normalized[:3] != path.anchor):
            raise OwnerAccessError("Windows project root requires an absolute drive path")
        tail = normalized[3:]
        components = tail[:-1].split("\\") if tail.endswith("\\") else tail.split("\\")
        if tail:
            for component in components:
                _native_component(component)
        return path
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        raise OwnerAccessError("project root must be an unambiguous absolute path")
    tail = value[1:]
    components = tail[:-1].split("/") if tail.endswith("/") else tail.split("/")
    if tail and any(component in {"", ".", ".."} for component in components):
        raise OwnerAccessError("project root contains relative or empty components")
    return PurePosixPath(value)


def _validated_roots(raw: Any) -> Mapping[str, str]:
    if not isinstance(raw, Mapping) or not raw or len(raw) > 128:
        raise OwnerAccessError("project_roots must be a nonempty mapping of at most 128 roots")
    result: dict[str, str] = {}
    seen: list[tuple[bool, tuple[str, ...]]] = []
    for ref, value in raw.items():
        if not isinstance(ref, str) or not _ROOT_REF.fullmatch(ref):
            raise OwnerAccessError("root_ref must be a safe ASCII slug of at most 64 characters")
        path = _root_path(value)
        windows = isinstance(path, PureWindowsPath)
        parts = tuple(p.casefold() for p in path.parts) if windows else path.parts
        for prior_windows, prior_parts in seen:
            if windows == prior_windows and (
                    parts[:len(prior_parts)] == prior_parts
                    or prior_parts[:len(parts)] == parts):
                raise OwnerAccessError("project roots must not duplicate or overlap")
        seen.append((windows, parts))
        result[ref] = str(path)
    return MappingProxyType(result)


@dataclass(frozen=True)
class OwnerAccessProfile:
    owner_approved: bool
    project_roots: Mapping[str, str]
    permission_mode: str = MAX_PERMISSION_MODE

    def __post_init__(self) -> None:
        if self.owner_approved is not True:
            raise OwnerAccessError("owner approval must be exactly true")
        if self.permission_mode != MAX_PERMISSION_MODE:
            raise OwnerAccessError("owner access requires bypassPermissions")
        object.__setattr__(self, "project_roots", _validated_roots(self.project_roots))


def parse_owner_access(raw: Any) -> OwnerAccessProfile | None:
    """Compile trusted local config; absence preserves existing account policy."""
    if raw is None:
        return None
    required = {"owner_approved", "project_roots"}
    if (not isinstance(raw, Mapping) or not required.issubset(raw)
            or set(raw) - required - {"permission_mode"}):
        raise OwnerAccessError("owner_access has missing or unknown fields")
    return OwnerAccessProfile(
        owner_approved=raw["owner_approved"],
        project_roots=raw["project_roots"],
        permission_mode=raw.get("permission_mode", MAX_PERMISSION_MODE),
    )


def _check_local_destination(root: PurePosixPath | PureWindowsPath, target: str) -> None:
    if isinstance(root, PureWindowsPath) != (os.name == "nt"):
        raise OwnerAccessError("local check requires a path for this host platform")
    local_root = Path(str(root))
    for ancestor in reversed((local_root, *local_root.parents)):
        try:
            observed = os.lstat(ancestor)
        except OSError as exc:
            raise OwnerAccessError("project root and all ancestors must be accessible directories") from exc
        if (stat.S_ISLNK(observed.st_mode)
                or getattr(observed, "st_file_attributes", 0) & 0x400):
            raise OwnerAccessError("project root ancestors must not be symlinks or reparse points")
        if not stat.S_ISDIR(observed.st_mode):
            raise OwnerAccessError("project root and all ancestors must be directories")
    try:
        os.lstat(target)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise OwnerAccessError("new project destination cannot be checked") from exc
    raise OwnerAccessError("new project destination already exists")


def build_project_path(profile: OwnerAccessProfile, root_ref: str, name: str,
                       *, local_check: bool) -> str:
    """Plan one new directory; never create, reuse, delete or resolve symlinks.

    `local_check=False` is lexical only; the remote Node must check locally.
    A successful local check does not guarantee a later create is race-free.
    """
    if not isinstance(profile, OwnerAccessProfile):
        raise OwnerAccessError("a validated owner profile is required")
    if type(local_check) is not bool:
        raise OwnerAccessError("local_check must be exactly true or false")
    if not isinstance(root_ref, str) or root_ref not in profile.project_roots:
        raise OwnerAccessError("root_ref is not configured for this runtime")
    if not isinstance(name, str) or not _PROJECT_NAME.fullmatch(name):
        raise OwnerAccessError("project name must be one safe ASCII slug component")
    _native_component(name)
    root = _root_path(profile.project_roots[root_ref])
    target = str(root / name)
    if local_check:
        _check_local_destination(root, target)
    return target
