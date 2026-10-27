#!/usr/bin/env python3
"""#434 L3 — the device Ability package, installed and read back locally.

WHAT THIS IS
------------
`scripts/build.py --face plugin` (#413) makes the device Ability face a separate
artifact. `scripts/runtime_truth.py` (#421) can recompute that artifact's bytes.
Neither answers the question an owner actually asks after an install:

    which version is installed, is it the bytes that were built, does the
    authority's catalog still support it, and is this device's ability READY,
    GRANTED and EFFECTIVE — or is the answer UNKNOWN?

This module answers that, on one device, without a second catalog:

  * `PackageStore.build` composes the plugin face through `build.py` and freezes
    a `PACKAGE.json` beside it (version, face/kind, the pinned catalog revision,
    and a SHA-256 for every member).
  * `install` / `rollback` move a **local pointer** (`current.json`) only after
    the revision verifies. Revisions are immutable; a tampered one is refused.
  * `readback` re-hashes the payload, checks face/kind, and reports catalog
    drift against the authority revision the caller supplies.
  * `states` reuses the existing G1 projection's `terms()`/`effective()`
    (#421's sibling contract) and adds the one fact only a local install can
    know: the bytes are actually here and hash-clean. `installed`, `supported`,
    `ready`, `granted`, `effective` are reported separately; `effective` is
    null unless the device reported every decisive term.
  * `caretaker_text` is presentation only: it accepts a closed facts envelope,
    renders a deterministic sentence, and cannot change a state, a Run or a
    grant.

WHAT IT DOES NOT DO
-------------------
No network, no `lark-cli`, no credential, no service, no global plugin install,
no `release.py` (owned by #419/#433), no `runtime/**` edit, no second ability
catalog. It does not build the node runtime: a Skill change produces a new
package revision, never an L1/L2 artifact. The authority remains
`ability-management` at the revision the caller pinned; a different catalog
revision is reported as DRIFT, never silently resolved.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parents[1]

SCHEMA_PACKAGE = "serveros/device-ability-package.v1"
SCHEMA_STATE = "serveros/device-ability-local-state.v1"
SCHEMA_POINTER = "serveros/device-ability-local-pointer.v1"
SCHEMA_READBACK = "serveros/device-ability-readback.v1"
PACKAGE_MANIFEST = "PACKAGE.json"
POINTER = "current.json"
PAYLOAD = "payload"

#: The states this module may report. They are the projection's words plus the
#: two facts a local store owns: NOT_INSTALLED and TAMPERED. UNKNOWN is a state,
#: never a soft yes.
STATES = ("EFFECTIVE", "DEGRADED", "UNKNOWN", "NOT_INSTALLED", "TAMPERED",
          "INSTALLED", "UNSUPPORTED", "NOT_READY", "DENIED", "DRIFT")

#: Facts a caretaker may be shown. A closed set: an authority-looking key such
#: as `grant` or `run_state` is refused rather than rendered.
CARETAKER_FACTS = frozenset({
    "status", "installed", "supported", "ready", "granted", "effective",
    "version", "catalog_revision", "catalog_state", "why",
})


class Refused(Exception):
    """The request cannot be answered safely. 'No' is a refusal, not a state."""


def _load(name: str, path: Path):
    module = sys.modules.get(name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _build():
    return _load("serveros_build_device_ability_package", ROOT / "scripts" / "build.py")


def _atomic():
    """The already-declared atomic JSON writer (`runtime/ability_projection.py`).

    Reusing it keeps this package out of the actuator contract's undeclared set
    and gives the pointer the same torn-write protection every other state file
    in this tree has.
    """
    return _load("serveros_ability_projection",
                 ROOT / "runtime" / "ability_projection.py").write_atomic


def _projection():
    return _load("device_ability_effective",
                 ROOT / "integrations" / "device-ability-projection" / "effective.py")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 128), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _members(payload: Path) -> dict[str, str]:
    # POSIX separators on every platform: a Windows store that wrote `a\\b`
    # would read every member back as a different path and call a correct
    # package TAMPERED.
    return {path.relative_to(payload).as_posix(): _sha256(path)
            for path in sorted(payload.rglob("*")) if path.is_file()}


def _json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _text(value: Any, field: str, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Refused(f"{field} must be a non-empty string")
    return value.strip()[:maximum]


def _now(clock: Callable[[], Any]) -> str:
    value = clock()
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    raise Refused("clock must return an ISO string, datetime or epoch seconds")


class PackageStore:
    """One device's local Ability package store.

    Layout under `root`:

        revisions/<version>/payload/<the plugin artifact>   (immutable)
        revisions/<version>/PACKAGE.json                    (frozen facts)
        current.json                                        (the one pointer)

    There is deliberately no symlink and no `previous` directory: rollback is the
    same atomic pointer write, and the previous version stays on disk because
    nothing here deletes. That is also what lets the store work on Windows,
    where a symlink needs a privilege the product must not demand.
    """

    def __init__(self, root, *, catalog_revision: str,
                 clock: Callable[[], Any] | None = None,
                 source: Mapping | None = None):
        self.root = Path(root)
        self.catalog_revision = _text(catalog_revision, "catalog_revision", 64)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        # Optional recomposition source. When supplied, `readback` rebuilds the
        # plugin face from the authority pin and compares, so rewriting the
        # payload AND its member hashes is still TAMPERED. Without it the
        # readback is manifest-relative and says `source_verified: false`.
        self.source = None
        if source is not None:
            if not isinstance(source, Mapping):
                raise Refused("source must be a mapping")
            missing = {"ability_repo", "ability_rev"} - set(source)
            if missing:
                raise Refused(f"source is missing {sorted(missing)}")
            self.source = {
                "ability_repo": Path(source["ability_repo"]),
                "ability_rev": _text(source["ability_rev"], "ability_rev", 64),
            }

    # -- paths -------------------------------------------------------------

    def revision_dir(self, version: str) -> Path:
        version = _text(version, "version", 256)
        if "/" in version or "\\" in version or version in (".", ".."):
            raise Refused("version must not be a path")
        return self.root / "revisions" / version

    def _pointer(self) -> dict:
        doc = _json(self.root / POINTER)
        if doc is None:
            return {"schema": SCHEMA_POINTER, "current": None, "previous": None}
        if doc.get("schema") != SCHEMA_POINTER:
            raise Refused(f"{POINTER} is not {SCHEMA_POINTER}")
        return doc

    def _write_pointer(self, current: str | None, previous: str | None) -> dict:
        doc = {"schema": SCHEMA_POINTER, "current": current, "previous": previous,
               "catalog_revision": self.catalog_revision,
               "written_at": _now(self.clock)}
        _atomic()(self.root / POINTER, doc)
        return doc

    # -- build -------------------------------------------------------------

    def build(self, *, bundle: Path, ability_repo: Path, ability_rev: str,
              pack_contract: Path, marketplace: Path) -> dict:
        """Compose the plugin face once and freeze its facts.

        No node module is copied: `face="plugin"` is the whole separation. The
        catalog revision is the build's pin, which is the authority this package
        is reproducible against.
        """
        revision = _text(ability_rev, "ability_rev", 64)
        if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
            raise Refused("ability_rev must be a full 40-hex revision")
        build = _build()
        # The face version is `bundle.version + bundle.cachebuster + "." + face`
        # (`scripts/build.py#plan`). Computing it from the composition lets the
        # build write DIRECTLY into the immutable revision directory: a staged
        # directory renamed into place would be a new, undeclared filesystem
        # mutation in this tree, and the revision name is a fact of the
        # composition rather than of a scratch path.
        composition = build.load_composition(Path(bundle))
        expected = (f"{composition['version']}+{composition['cachebuster']}"
                    f".plugin")
        target = self.revision_dir(expected)
        if (target / PACKAGE_MANIFEST).exists():
            raise Refused(f"revision {expected} already exists; revisions are immutable")
        # A directory without PACKAGE.json is a failed/incomplete attempt from a
        # previous build. Rebuilding it is safe because `build.build` prunes its
        # own output directory; this module never deletes and never renames.
        market = Path(marketplace)
        try:
            code = build.build(Path(bundle), Path(ability_repo), revision,
                               target / PAYLOAD, market, True, Path(pack_contract),
                               face="plugin")
        except (OSError, ValueError) as exc:
            raise Refused(f"the plugin face failed to build: {exc}") from None
        if code != 0:
            raise Refused(f"the plugin face failed to build (exit {code})")
        artifact = _json(target / PAYLOAD / "ARTIFACT.json")
        if artifact is None:
            raise Refused("the build produced no ARTIFACT.json")
        version = _text(artifact.get("version"), "version", 256)
        if artifact.get("face") != "plugin" or artifact.get("kinds") != ["plugin"]:
            raise Refused(
                "the build is not the plugin face; refusing to package a node "
                f"artifact as a device Ability (face={artifact.get('face')!r}, "
                f"kinds={artifact.get('kinds')!r})")
        if version != expected:
            raise Refused(
                f"the build says {version!r} but the composition names {expected!r}")
        contract = build.release_contract()
        payload = target / PAYLOAD
        missing = [rel for rel in contract.REQUIRED_MEMBERS[contract.PLUGIN]
                   if not (payload / rel).is_file()]
        if missing:
            raise Refused(f"the plugin face is missing its required members: {missing}")
        leaked = [rel for rel in contract.REQUIRED_MEMBERS[contract.NODE_RUNTIME]
                  if (payload / rel).exists()]
        if leaked:
            raise Refused(f"the plugin face carries node members: {leaked}")
        manifest = {
            "schema": SCHEMA_PACKAGE,
            "name": artifact.get("name"),
            "version": version,
            "face": "plugin",
            "kinds": ["plugin"],
            "catalog_revision": revision,
            "source": artifact.get("source") or {},
            "skills": artifact.get("skills") or [],
            "mcp_servers": artifact.get("mcp_servers") or [],
            "members": _members(target / PAYLOAD),
            "built_at": _now(self.clock),
        }
        if manifest["mcp_servers"]:
            raise Refused(
                "a device Ability package declares no node MCP surface; that "
                f"surface belongs to the node artifact ({manifest['mcp_servers']})")
        _atomic()(target / PACKAGE_MANIFEST, manifest)
        return manifest

    # -- readback ----------------------------------------------------------

    def _read(self, version, installed, state, why, **extra) -> dict:
        return {"schema": SCHEMA_READBACK, "version": version,
                "installed": installed, "state": state,
                "catalog_drift": False, "why": why, **extra}

    def _recomposition(self, payload: Path, manifest: dict) -> tuple[str, str]:
        """Rebuild from the authority pin and compare. `(status, why)`.

        status is OK, MISMATCH or UNAVAILABLE. A manifest is a claim; when the
        caller supplies the source, the claim is checked against a fresh build
        rather than against a file an attacker can edit beside it.
        """
        if self.source is None:
            return "UNAVAILABLE", "no source was supplied; hash check only"
        try:
            truth = _load("serveros_runtime_truth_local",
                          ROOT / "scripts" / "runtime_truth.py")
            got = truth.expected_artifact(
                _build(), self.source["ability_repo"], self.source["ability_rev"],
                payload, self.root / ".agents" / "plugins" / "marketplace.json",
                layout_at=payload, face="plugin")
        except Exception as exc:  # noqa: BLE001 - unavailable is not a pass
            return "UNAVAILABLE", f"recomposition failed: {type(exc).__name__}"
        if got is None:
            return "UNAVAILABLE", "the supplied source does not rebuild this payload"
        expected, version = got
        if version != manifest.get("version"):
            return "MISMATCH", (f"recomposition names {version!r}, the package "
                                f"names {manifest.get('version')!r}")
        drift = truth.tree_drift(expected, payload, ignore=())
        if drift:
            return "MISMATCH", f"recomposition differs in {drift[:3]}"
        return "OK", "payload equals a fresh build from the recorded pin"

    def readback(self, version: str | None = None) -> dict:
        """What is actually installed, by bytes. Never raises for 'not there'."""
        pointer = self._pointer()
        version = version or pointer.get("current")
        if not version:
            return self._read(None, False, "NOT_INSTALLED",
                              "no version is installed")
        target = self.revision_dir(version)
        manifest = _json(target / PACKAGE_MANIFEST)
        if manifest is None:
            return self._read(version, False, "TAMPERED",
                              f"{version} has no readable {PACKAGE_MANIFEST}")
        if manifest.get("schema") != SCHEMA_PACKAGE:
            return self._read(version, False, "TAMPERED",
                              "package manifest schema is unknown")
        if manifest.get("face") != "plugin" or manifest.get("kinds") != ["plugin"]:
            return self._read(version, False, "TAMPERED",
                              "the package manifest is not the plugin face")
        payload = target / PAYLOAD
        contract = _build().release_contract()
        missing = [rel for rel in contract.REQUIRED_MEMBERS[contract.PLUGIN]
                   if not (payload / rel).is_file()]
        if missing:
            return self._read(version, False, "TAMPERED",
                              f"the plugin face is missing {missing}")
        leaked = [rel for rel in contract.REQUIRED_MEMBERS[contract.NODE_RUNTIME]
                  if (payload / rel).exists()]
        if leaked:
            return self._read(
                version, False, "TAMPERED",
                f"the installed tree carries node members ({leaked}); this is "
                "not the device Ability face")
        actual = _members(payload) if payload.is_dir() else {}
        declared = manifest.get("members")
        if not isinstance(declared, dict) or not declared:
            return self._read(version, False, "TAMPERED",
                              "the package records no member hashes")
        changed = sorted(rel for rel, digest in declared.items()
                         if actual.get(rel) != digest)
        extra = sorted(set(actual) - set(declared))
        if changed or extra:
            return self._read(
                version, False, "TAMPERED",
                f"payload differs from the frozen manifest "
                f"(changed={changed[:5]}, extra={extra[:5]})")
        recomposed, recompose_why = self._recomposition(payload, manifest)
        if recomposed == "MISMATCH":
            return self._read(version, False, "TAMPERED", recompose_why)
        drift = manifest.get("catalog_revision") != self.catalog_revision
        verified = recomposed == "OK"
        why = ("installed bytes hash-match their manifest"
               + ("; and equal a fresh build from the recorded pin" if verified else
                  f"; source not verified ({recompose_why})")
               + ("; the authority catalog has moved" if drift else ""))
        return self._read(
            version, True, "DRIFT" if drift else "INSTALLED", why,
            face="plugin", catalog_revision=manifest.get("catalog_revision"),
            authority_revision=self.catalog_revision, catalog_drift=drift,
            skills=list(manifest.get("skills") or []),
            source=manifest.get("source") or {},
            members=len(declared), source_verified=verified,
            recomposition=recomposed)

    # -- install / rollback ------------------------------------------------

    def install(self, version: str, *, allow_drift: bool = False) -> dict:
        """Move the local pointer, after verifying this revision is installable."""
        read = self.readback(version)
        if not read["installed"]:
            raise Refused(f"{version} is not installable: {read['why']}")
        if read["catalog_drift"] and not allow_drift:
            raise Refused(
                "CATALOG_DRIFT: the package was built at "
                f"{read['catalog_revision']}, the authority is at "
                f"{self.catalog_revision}; resolve or pass allow_drift explicitly")
        pointer = self._pointer()
        previous = pointer.get("current")
        if previous == version:
            return {"changed": False, **read}
        self._write_pointer(version, previous if previous != version else None)
        return {"changed": True, "previous": previous, **self.readback(version)}

    def rollback(self, *, allow_drift: bool = False) -> dict:
        """Point back at the previous verified revision. Nothing is deleted."""
        pointer = self._pointer()
        previous = pointer.get("previous")
        if not previous:
            raise Refused("no previous revision to roll back to")
        read = self.readback(previous)
        if not read["installed"]:
            raise Refused(f"the previous revision no longer verifies: {read['why']}")
        if read["catalog_drift"] and not allow_drift:
            raise Refused(
                "CATALOG_DRIFT: the previous revision was built at "
                f"{read['catalog_revision']}, the authority is at "
                f"{self.catalog_revision}; pass allow_drift explicitly")
        self._write_pointer(previous, pointer.get("current"))
        return {"changed": True, "rolled_back_to": previous, **self.readback(previous)}


# ---------------------------------------------------------------------------
# the state contract
# ---------------------------------------------------------------------------

def states(*, package: Mapping, resolved: Mapping, observed: Iterable[str],
           ability: str | None = None) -> dict:
    """`installed / supported / ready / granted / effective`, separately.

    `resolved` is the existing resolver's record for this Ability; `observed`
    lists the device axes that were actually reported. The projection's own
    `terms()` decides supported/ready/grant and refuses to infer an unreported
    axis — this function does not re-spell that rule. It adds the local fact the
    projection cannot know: `installed` comes from the package's hashes.
    """
    if not isinstance(package, Mapping) or not isinstance(resolved, Mapping):
        raise Refused("states needs the package readback and a resolver record")
    if package.get("schema") != SCHEMA_READBACK:
        raise Refused(
            "states needs a readback produced by PackageStore.readback; a "
            "hand-built {'installed': True} is not evidence")
    if not isinstance(package.get("installed"), bool):
        raise Refused("the package readback carries no boolean installed fact")
    projection = _projection()
    words = projection.terms(frozenset(observed), dict(resolved))
    installed = bool(package.get("installed"))
    drift = bool(package.get("catalog_drift"))
    if drift:
        # A package built from another catalog revision cannot be called
        # supported by THIS authority, even if the resolver had an old answer.
        words = {**words, "supported": "UNKNOWN"}
    effective_provider, why = projection.effective(dict(resolved), words)

    if not installed:
        status = "TAMPERED" if package.get("state") == "TAMPERED" else "NOT_INSTALLED"
    elif drift:
        status = "DRIFT"
    elif "UNKNOWN" in words.values():
        status = "UNKNOWN"
    elif words["supported"] != "SUPPORTED":
        status = "UNSUPPORTED"
    elif words["ready"] != "READY":
        status = "NOT_READY"
    elif words["grant"] != "ALLOWED":
        status = "DENIED"
    elif effective_provider is None:
        status = "UNKNOWN"
    elif resolved.get("status") == "DEGRADED":
        # Callable, but the resolver reported degradation. Reporting EFFECTIVE
        # would drop that word; the provider is still reported beside it.
        status = "DEGRADED"
    else:
        status = "EFFECTIVE"

    # The projection answers "the resolver named a provider and every term was
    # known"; a NOT_READY or DENIED term is still a KNOWN term to it. This
    # package answers the stricter install question, so `effective` is the
    # provider only in the EFFECTIVE rollup — never beside NOT_READY/DENIED.
    effective = (effective_provider
                 if status in ("EFFECTIVE", "DEGRADED") else None)

    return {
        "schema": SCHEMA_STATE,
        "ability": ability,
        "version": package.get("version"),
        "installed": installed,
        "supported": words["supported"],
        "ready": words["ready"],
        "granted": words["grant"],
        "effective": effective,
        "status": status,
        "catalog_state": "DRIFT" if drift else "CURRENT",
        "catalog_revision": package.get("catalog_revision"),
        "authority_revision": package.get("authority_revision"),
        "source_verified": bool(package.get("source_verified")),
        "recomposition": package.get("recomposition"),
        "why": {"resolver": why, "package": package.get("why")},
    }


# ---------------------------------------------------------------------------
# caretaker: explain facts, not govern them
# ---------------------------------------------------------------------------

def _closed(facts: Mapping) -> dict:
    if not isinstance(facts, Mapping):
        raise Refused("caretaker facts must be a mapping")
    extra = sorted(set(facts) - CARETAKER_FACTS)
    if extra:
        raise Refused(
            f"caretaker facts carry keys it may not interpret: {extra}. "
            "A grant, a Run state or an authorization belongs to its authority")
    return dict(facts)


def caretaker_text(facts: Mapping,
                   *, summarizer: Callable[[dict], str] | None = None) -> str:
    """One deterministic sentence about facts, plus an optional sanitized gloss.

    The caretaker cannot make an ability effective, cannot read a Run, and
    cannot grant anything: it is given a closed facts envelope and returns text.
    A model failure only drops the gloss.
    """
    payload = _closed(facts)
    status = _text(payload.get("status"), "status", 32)
    if status not in STATES:
        raise Refused(f"{status!r} is not a Device Ability state")
    if not isinstance(payload.get("installed"), bool):
        raise Refused("caretaker facts need a boolean installed")
    allowed = {
        "supported": {"SUPPORTED", "DENIED", "UNKNOWN", None},
        "ready": {"READY", "NOT_READY", "UNKNOWN", None},
        "granted": {"ALLOWED", "DENIED", "UNKNOWN", None},
        "catalog_state": {"CURRENT", "DRIFT", None},
    }
    for key, values in allowed.items():
        if payload.get(key) not in values:
            raise Refused(f"{key} has a word this state contract does not use")
    effective = payload.get("effective")
    if effective is not None and not isinstance(effective, str):
        raise Refused("effective must be a provider id or null")
    if status == "EFFECTIVE" and (not payload.get("installed")
                                  or payload.get("supported") != "SUPPORTED"
                                  or payload.get("ready") != "READY"
                                  or payload.get("granted") != "ALLOWED"
                                  or not effective):
        raise Refused(
            "contradictory facts: EFFECTIVE requires installed, SUPPORTED, "
            "READY, ALLOWED and a provider id")
    version = payload.get("version") or "an unversioned package"
    parts = [
        f"Device Ability {status}: {version}.",
        f"installed={payload.get('installed')} supported={payload.get('supported')} "
        f"ready={payload.get('ready')} granted={payload.get('granted')} "
        f"effective={payload.get('effective')}.",
    ]
    if payload.get("catalog_state") == "DRIFT":
        parts.append("The authority catalog has moved; this is DRIFT, not success.")
    if status == "UNKNOWN":
        parts.append("Not every decisive term was observed, so nothing is claimed.")
    text = " ".join(parts)
    if summarizer is not None:
        try:
            gloss = summarizer(dict(payload))
        except Exception:
            gloss = None
        if isinstance(gloss, str) and gloss.strip():
            text += "\nCaretaker gloss: " + gloss.strip()[:300]
    return text


__all__ = [
    "PackageStore", "Refused", "states", "caretaker_text",
    "SCHEMA_PACKAGE", "SCHEMA_STATE", "SCHEMA_POINTER", "PACKAGE_MANIFEST",
    "POINTER", "PAYLOAD", "STATES", "CARETAKER_FACTS",
]
