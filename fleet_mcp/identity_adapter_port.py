"""The Hub's ONE seam to the L0 identity authority — versioned, verified, injected.

Why this file exists
--------------------
`fleet_mcp/registry.py` used to import `fleet/transport.py` directly. That is the
right dependency (there is exactly one identity authority: `DeviceRef`,
`DeviceRegistry`, `Device`, `Availability`, `CapabilityAvailability`,
`UnknownDevice`, `canonical_level`), and it was also the reason a Fleet Hub could
not be deployed on its own: the import is a checkout path, not a declared
dependency.

Two answers were rejected before this one:

*   **Vendor the seven classes into `fleet_mcp`.** That is a second
    implementation of device identity. It would agree on the day it was written
    and diverge the first time a device was renamed — and the caller would have
    no way to know which answer it got. This file must never contain a second
    `DeviceRef`, `Device`, `DeviceRegistry` or level mapper.
*   **A small local YAML fallback.** Same defect, one level down: fake fallback
    data is a fleet that exists only inside the Hub.

What is implemented instead
---------------------------
An **explicit, versioned common dependency with a verified payload**:

    ABI        serveros.fleet.transport/v1
    module     fleet.transport
    payload    <dir>/fleet/transport.py
               <dir>/fleet-authority.json  {"abi", "module", "sha256"}

The Hub either (a) imports the canonical package from the running tree — the
source checkout or an installed node, exactly as before — or (b) is given a
payload directory, whose manifest and digest are verified before it is placed on
`sys.path`. In both cases the imported module must expose the seven symbols with
the expected kinds. If neither holds, this raises `AuthorityUnavailable` and
`registry.py` turns that into an `INTERNAL` refusal that names the missing
authority. There is no third path and no fallback data.

The payload contract is the part the release builder (#431) can satisfy later.
This module only DEFINES and CHECKS it; it does not build, fetch or bundle the
authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

#: The ABI this Hub speaks. Bumped when the shape of the identity authority
#: changes in a way a Hub compiled against the old shape cannot survive.
AUTHORITY_ABI = "serveros.fleet.transport/v1"

#: The one module the identity lives in. Named, not derived from a path.
AUTHORITY_MODULE = "fleet.transport"

#: Set to a payload directory to run the Hub without a checkout.
AUTHORITY_PAYLOAD_ENV = "SERVEROS_FLEET_AUTHORITY_PAYLOAD"

#: The manifest a payload directory must carry.
MANIFEST_NAME = "fleet-authority.json"

#: The complete symbol set, with the kind each must be. A missing name or a name
#: whose kind changed is an ABI break, not a warning: a Hub that silently
#: tolerates it would report a fleet through a half-imported authority.
REQUIRED_SYMBOLS: dict[str, str] = {
    "Availability": "class",
    "CapabilityAvailability": "class",
    "Device": "class",
    "DeviceRef": "class",
    "DeviceRegistry": "class",
    "UnknownDevice": "class",
    "canonical_level": "function",
}

#: The Hub's standalone expression, printed by diagnostics and asserted by test.
#: `data_not_bundled` is deliberate: the authority CODE can travel as a verified
#: payload, but `fleet/registry.yaml` is authored data and must be supplied by
#: the operator. Bundling a registry here would be inventing a fleet.
STANDALONE_REQUIREMENTS: dict[str, Any] = {
    "abi": AUTHORITY_ABI,
    "module": AUTHORITY_MODULE,
    "symbols": tuple(REQUIRED_SYMBOLS),
    "payload_manifest": {
        "file": MANIFEST_NAME,
        "fields": ("abi", "module", "files"),
        "files": "relative path -> sha256; every .py/.yaml under the payload "
                 "must be declared, and fleet/transport.py is mandatory",
    },
    "data_not_bundled": "fleet/registry.yaml",
    "resolution_order": ("payload", "importable canonical package"),
    "fallback_authority": None,
}


class AuthorityUnavailable(RuntimeError):
    """The canonical identity authority could not be loaded or verified."""


def _kind(value: Any) -> str:
    if isinstance(value, type):
        return "class"
    if callable(value):
        return "function"
    return type(value).__name__


def verify_authority(module: ModuleType) -> None:
    """Refuse a module that is not the identity authority this ABI names.

    Presence alone is not enough: a name that exists and is no longer the kind
    the Hub uses is exactly the drift a version check is for.
    """
    missing = [name for name in REQUIRED_SYMBOLS if not hasattr(module, name)]
    if missing:
        raise AuthorityUnavailable(
            f"{getattr(module, '__name__', module)!r} does not expose "
            f"{missing}; the Hub requires every symbol in {AUTHORITY_ABI}"
        )
    wrong = [f"{name} is {_kind(getattr(module, name))}, expected {kind}"
             for name, kind in REQUIRED_SYMBOLS.items()
             if _kind(getattr(module, name)) != kind]
    if wrong:
        raise AuthorityUnavailable(
            f"{getattr(module, '__name__', module)!r} breaks {AUTHORITY_ABI}: "
            + "; ".join(wrong)
        )
    declared = getattr(module, "AUTHORITY_ABI", None)
    if declared is not None and declared != AUTHORITY_ABI:
        raise AuthorityUnavailable(
            f"the authority declares ABI {declared!r}; this Hub speaks "
            f"{AUTHORITY_ABI!r}. Two ABIs are two contracts, and a Hub must not "
            f"guess which one it is talking to."
        )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_payload(payload: str | Path) -> Path:
    """Verify a declared payload directory before it reaches `sys.path`.

    Checks, in this order, and refuses without partial trust: the directory and
    manifest exist; the manifest names this ABI and this module; the declared
    digest matches `fleet/transport.py` byte for byte.
    """
    root = Path(payload)
    if not root.is_dir():
        raise AuthorityUnavailable(f"authority payload {root} is not a directory")
    manifest_path = root / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AuthorityUnavailable(
            f"authority payload {root} has no {MANIFEST_NAME}; an undeclared "
            f"payload is refused rather than imported"
        ) from None
    except (OSError, ValueError) as exc:
        raise AuthorityUnavailable(
            f"authority payload {root} has an unreadable manifest "
            f"({type(exc).__name__})"
        ) from None
    if not isinstance(manifest, dict):
        raise AuthorityUnavailable(f"{manifest_path} is not a JSON object")
    abi, module = manifest.get("abi"), manifest.get("module")
    if abi != AUTHORITY_ABI:
        raise AuthorityUnavailable(
            f"payload declares ABI {abi!r}; this Hub speaks {AUTHORITY_ABI!r}")
    if module != AUTHORITY_MODULE:
        raise AuthorityUnavailable(
            f"payload declares module {module!r}; this Hub requires {AUTHORITY_MODULE!r}")
    declared_files = manifest.get("files")
    source_rel = "/".join(AUTHORITY_MODULE.split(".")) + ".py"
    if not isinstance(declared_files, dict) or source_rel not in declared_files:
        raise AuthorityUnavailable(
            f"payload manifest must declare {source_rel!r} in `files`")
    for rel, digest in declared_files.items():
        target = root / rel
        if not target.is_file():
            raise AuthorityUnavailable(f"payload declares {rel!r}, which is absent")
        actual = _digest(target)
        if not isinstance(digest, str) or actual != digest.lower():
            raise AuthorityUnavailable(
                f"payload digest does not match {rel}: declared {digest!r}, "
                f"measured {actual!r}. An unverified payload is refused.")
    # A payload is a directory on `sys.path`. A module or contract file the
    # manifest never named could therefore be imported as if it were part of the
    # authority, so the check is closed: every importable or data file must be
    # declared, not merely the ones that were listed.
    undeclared = sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.suffix in (".py", ".yaml", ".yml")
        and p.relative_to(root).as_posix() not in declared_files
    )
    if undeclared:
        raise AuthorityUnavailable(
            f"payload carries files its manifest does not declare: {undeclared}")
    return root


@dataclass(frozen=True)
class Authority:
    """The verified identity authority, as the Hub consumes it."""

    module: ModuleType
    abi: str
    source: str

    @property
    def symbols(self) -> dict[str, Any]:
        return {name: getattr(self.module, name) for name in REQUIRED_SYMBOLS}


def load_authority(payload: str | Path | None = None) -> Authority:
    """Load, verify and return the one identity authority.

    `payload` (or `SERVEROS_FLEET_AUTHORITY_PAYLOAD`) names a verified payload
    directory; otherwise the canonical package is imported from whatever is on
    `sys.path` — the source checkout or an installed node, unchanged.

    The `from fleet.transport import (...)` below is deliberately a STATIC
    import: the loader closure that #431 measures must still see this edge, so a
    Hub artifact cannot claim to be standalone while quietly acquiring the
    authority through reflection.
    """
    declared = payload if payload is not None else os.environ.get(AUTHORITY_PAYLOAD_ENV)
    if declared:
        root = verify_payload(declared)
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
    try:
        from fleet.transport import (  # noqa: F401 - imported for its side effect
            Availability,
            CapabilityAvailability,
            Device,
            DeviceRef,
            DeviceRegistry,
            UnknownDevice,
            canonical_level,
        )
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        raise AuthorityUnavailable(
            f"the canonical identity layer {AUTHORITY_MODULE!r} could not be "
            f"imported ({type(exc).__name__}: {exc}). This Hub carries no second "
            f"copy of device identity; supply a verified payload through "
            f"{AUTHORITY_PAYLOAD_ENV} or install the {AUTHORITY_ABI} dependency."
        ) from None
    module = sys.modules.get(AUTHORITY_MODULE)
    if module is None:  # pragma: no cover - an import that returns none is impossible
        raise AuthorityUnavailable(f"{AUTHORITY_MODULE!r} did not register a module")
    verify_authority(module)
    return Authority(module=module, abi=AUTHORITY_ABI,
                     source=str(getattr(module, "__file__", "")))
