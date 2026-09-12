#!/usr/bin/env python3
"""The Managed Runtime Registry — what this node manages, and on whose behalf.

The rule the module enforces, in one line:

    Nothing is managed by being noticed.

A container that exists is an observation. A container ServerOS may stop is a
*registration*, with an owner, a scope, a lifecycle class and a protection
level, written down before anything touches it. Without that distinction the
first low-disk evening ends with someone's baseline database pruned, and the
post-mortem has no record to check the decision against.

Two vocabularies are load-bearing and neither is invented here:

  `scope: user | system`
      Everything on this node is `user` today. `system` is declarable and is
      NOT actionable in v1 — ServerOS performs no elevation, so a system-scope
      resource that is down reports NEEDS_HUMAN rather than reaching for
      sudo. Recording the scope now means the privilege story is a fact in the
      registry instead of an assumption made at the call site.

  `protection: protected | normal | disposable`
      `protected` is not "important"; it is "the automatic path does not
      reach it, ever". The distinction matters because the tempting version of
      resource management is a policy that is *usually* right, and the failures
      that hurt are the ones where it was right on average.

Validation rejects rather than coerces. A resource with an unknown `kind`, an
untyped owner ref or a lifecycle class outside the closed set is a hard error,
because the alternative is a registry that is silently a slightly different
registry than the one its author believed they wrote.
"""
from __future__ import annotations

import functools
import importlib.util
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guarantee
    sys.stderr.write("ERROR: PyYAML is required to read runtime contracts\n")
    raise SystemExit(2)

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent

CONTRACT = _HERE / "managed-runtime-contract.yaml"

# The typed-ref shape used across ServerOS. Same convention as `skill:<id>`
# and `device:<id>`; kept as a pattern rather than a parser because it must
# agree with `fleet/transport.py`, not replace it.
TYPED_REF = re.compile(r"^(?P<type>[a-z][a-z0-9-]*):(?P<id>[A-Za-z0-9][A-Za-z0-9._-]*)$")

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")

# Keys that must never appear anywhere in the registry. A registry is a
# declaration, and declarations get committed, mailed and pasted into issues.
# The scan is recursive and by key name (case-insensitive) — it is a
# structural check, not a search for something that looks like a secret.
FORBIDDEN_KEYS = frozenset(
    {
        "token", "access_token", "refresh_token", "id_token", "secret",
        "client_secret", "password", "passwd", "credential", "credentials",
        "api_key", "apikey", "private_key", "cookie", "session_key",
        "salt_key", "litellm_salt_key", "ui_password", "auth_value",
    }
)

# Suffixes that mark a value as a stored half of a credential rather than a
# name. `*_env` and `*_ref` are the permitted forms: a NAME or a locator, never
# the value. This is what lets the registry say "this container needs
# LITELLM_SALT_KEY" without the registry being where the salt lives.
_CREDENTIAL_KEY_SUFFIXES = ("_value", "_secret", "_token", "_password", "_key")


class RegistryError(Exception):
    """Base class for managed-runtime registry failures."""


class RegistryInvalid(RegistryError):
    """A registration is malformed. A bug in the author, not a runtime fact."""


class UnknownResource(RegistryError):
    """No registration carries this id.

    Distinct from "the resource is down". A caller asking for an unregistered
    id has made a mistake; a caller asking for a registered one has a fact.
    """


def _NAME_MATCH(slug: str, name: str) -> bool:
    return re.search(rf"(?:^|[^a-z0-9]){re.escape(slug)}(?:$|[^a-z0-9])", name, re.I) is not None


def _parse_ref_list(raw, where: str) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RegistryInvalid(f"{where}: expected a list of {{ref, because}} entries")
    out = []
    seen = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict) or not entry.get("ref"):
            raise RegistryInvalid(f"{where}[{i}]: each entry needs a typed `ref`")
        ref = entry["ref"]
        if not TYPED_REF.match(ref):
            raise RegistryInvalid(
                f"{where}[{i}]: {ref!r} is not a typed ref. `{{type, id}}` is the "
                f"identity contract; a bare name is how one workstream becomes "
                f"another workstream's cleanup candidate."
            )
        if ref in seen:
            raise RegistryInvalid(f"{where}[{i}]: duplicate ref {ref!r}")
        seen.add(ref)
        out.append({
            "ref": ref,
            "because": entry.get("because") or "",
            "state": entry.get("state"),
        })
    return out


@functools.lru_cache(maxsize=1)
def contract() -> dict:
    """The schema, parsed once per process.

    This started uncached, and the cost was not obvious until the test suite
    ran: validating one observation reads the contract, and `Health.state`
    reads it once per rule, so evaluating all 512 axis combinations re-parsed
    the same YAML thousands of times and took 72 seconds.

    A daemon evaluating health on a timer would have paid that on every tick.
    The contract is a compiled-in schema, not mutable state, so reading it once
    is correct as well as fast — a running process should NOT silently pick up
    an edited schema file, because that would make two running components
    disagree about the vocabulary with nothing to detect it.

    Returns the parsed document itself, not a copy. Callers treat it as
    read-only; `test_an_uncovered_combination_raises...` monkeypatches this
    function rather than mutating what it returns, which is the pattern to
    follow.
    """
    with CONTRACT.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _values(section: str) -> tuple[str, ...]:
    c = contract()[section]
    if isinstance(c, dict):
        return tuple(c)
    return tuple(c)


def _axis_names() -> tuple[str, ...]:
    return tuple(contract()["health_axes"])


def _axis_values(axis: str) -> tuple[str, ...]:
    return tuple(contract()["health_axes"][axis]["values"])


def _walk_keys(node, path="") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.append((str(k), path))
            out.extend(_walk_keys(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            out.extend(_walk_keys(item, f"{path}[{i}]"))
    return out


def _reject_credentials(record: dict, where: str) -> None:
    """A registry entry is a declaration of intent, never a credential store."""
    for key, path in _walk_keys(record):
        low = key.lower().strip("-")
        if low in FORBIDDEN_KEYS:
            raise RegistryInvalid(
                f"{where}: {path or key!r} carries a credential-shaped key. "
                f"Registrations name credentials; they never hold them. Use "
                f"the `*_env` form (the variable's NAME) or a typed ref."
            )
        if low.endswith(_CREDENTIAL_KEY_SUFFIXES) and low not in {
            "salt_key_name", "key_name", "key_ref",
        }:
            raise RegistryInvalid(
                f"{where}: {path or key!r} looks like a stored credential half. "
                f"Use `*_env` (the name) or `*_ref` (a locator)."
            )


class Resource:
    """One registered managed runtime.

    Frozen: a registration is read from disk and then reasoned about, and a
    mutable registration would let a caller "fix" a resource in memory and
    report a state the registry does not contain.
    """

    __slots__ = ("_r",)

    def __init__(self, record: dict) -> None:
        self._r = record

    # -- identity ----------------------------------------------------------
    @property
    def id(self) -> str:
        return self._r["id"]

    @property
    def kind(self) -> str:
        return self._r["kind"]

    @property
    def owner(self) -> dict:
        return self._r["owner"]

    @property
    def owner_ref(self) -> str:
        return self._r["owner"]["ref"]

    @property
    def owner_type(self) -> str:
        return self._r["owner"]["type"]

    @property
    def scope(self) -> str:
        return self._r["scope"]

    @property
    def lifecycle(self) -> str:
        return self._r["lifecycle"]["class"]

    @property
    def desired(self) -> str:
        return self._r["desired"]["state"]

    @property
    def protection(self) -> str:
        return self._r["protection"]["level"]

    @property
    def observe(self) -> dict:
        return self._r.get("observe") or {}

    @property
    def probes(self) -> tuple[dict, ...]:
        return tuple(self._r.get("probes") or ())

    @property
    def note(self) -> str | None:
        return self._r.get("note")

    @property
    def record(self) -> dict:
        return dict(self._r)

    # -- derived questions -------------------------------------------------

    @property
    def is_automatable(self) -> bool:
        """May the reconcile path touch this at all?

        Four independent reasons a resource is hands-off, and each is checked
        separately so the report can say *which* one applied. A single
        `if protected` would collapse "we must not" into "we chose not to".
        """
        return (
            self.protection != "protected"
            and self.scope == "user"
            and self.owner_type != "unknown"
            and self.desired == "running"
        )

    def why_not_automatable(self) -> list[str]:
        reasons = []
        if self.protection == "protected":
            reasons.append("protection=protected")
        if self.scope != "user":
            reasons.append(f"scope={self.scope} (ServerOS does not elevate)")
        if self.owner_type == "unknown":
            reasons.append("owner=unknown (fails safe)")
        if self.desired != "running":
            reasons.append(f"desired={self.desired}")
        return reasons

    def show(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "owner": dict(self.owner),
            "scope": self.scope,
            "lifecycle": self.lifecycle,
            "desired": self.desired,
            "protection": self.protection,
            "automatable": self.is_automatable,
            "not_automatable_because": self.why_not_automatable(),
            "probes": [p.get("id") for p in self.probes],
            "note": self.note,
        }


def validate(record: dict, where: str = "resource") -> Resource:
    """Reject a malformed registration rather than coercing it."""
    required = ("id", "kind", "owner", "scope", "lifecycle", "desired", "protection")
    for field in required:
        if field not in record:
            raise RegistryInvalid(f"{where}: missing required field {field!r}")

    rid = record["id"]
    if not isinstance(rid, str) or not ID_PATTERN.match(rid):
        raise RegistryInvalid(
            f"{where}: id {rid!r} is not a stable id. Ids are lowercase, "
            f"dash-separated, and must not encode a host, a port or a run."
        )

    kind = record["kind"]
    if kind not in _values("kinds"):
        raise RegistryInvalid(
            f"{where} ({rid}): unknown kind {kind!r}. Known: {list(_values('kinds'))}"
        )

    owner = record["owner"]
    if not isinstance(owner, dict) or "type" not in owner or "ref" not in owner:
        raise RegistryInvalid(f"{where} ({rid}): owner needs {{type, ref}}")
    if owner["type"] not in _values("owner_types"):
        raise RegistryInvalid(
            f"{where} ({rid}): unknown owner.type {owner['type']!r}. "
            f"Known: {list(_values('owner_types'))}"
        )
    m = TYPED_REF.match(str(owner["ref"]))
    if not m:
        raise RegistryInvalid(
            f"{where} ({rid}): owner.ref {owner['ref']!r} is not a typed ref. "
            f"Expected `<type>:<id>` — a ref that cannot be resolved is a "
            f"dependency nobody can check."
        )

    if record["scope"] not in _values("scopes"):
        raise RegistryInvalid(f"{where} ({rid}): unknown scope {record['scope']!r}")

    lc = record["lifecycle"]
    if not isinstance(lc, dict) or lc.get("class") not in _values("lifecycle_classes"):
        raise RegistryInvalid(
            f"{where} ({rid}): lifecycle.class must be one of "
            f"{list(_values('lifecycle_classes'))}"
        )

    ds = record["desired"]
    if not isinstance(ds, dict) or ds.get("state") not in _values("desired_states"):
        raise RegistryInvalid(
            f"{where} ({rid}): desired.state must be one of "
            f"{list(_values('desired_states'))}"
        )

    pl = record["protection"]
    if not isinstance(pl, dict) or pl.get("level") not in _values("protection_levels"):
        raise RegistryInvalid(
            f"{where} ({rid}): protection.level must be one of "
            f"{list(_values('protection_levels'))}"
        )

    claimed: dict[str, str] = {}
    for probe in record.get("probes") or ():
        if not isinstance(probe, dict):
            raise RegistryInvalid(f"{where} ({rid}): a probe must be a mapping")
        if probe.get("kind") not in _values("probe_kinds"):
            raise RegistryInvalid(
                f"{where} ({rid}): unknown probe kind {probe.get('kind')!r}. "
                f"Known: {list(_values('probe_kinds'))}"
            )
        # `reason` is not a health axis: it is the refinement a diagnostic-back
        # probe is allowed to contribute to an axis that some other probe
        # already decided. Allowing it here keeps that split explicit rather
        # than letting a diagnostic quietly declare itself an axis.
        if probe.get("axis") not in _axis_names() + ("reason",):
            raise RegistryInvalid(
                f"{where} ({rid}): probe {probe.get('id')!r} answers unknown axis "
                f"{probe.get('axis')!r}. Known: {list(_axis_names())} + ['reason']"
            )
        # One probe per axis, checked at REGISTRATION rather than at
        # observation time. Two probes answering `liveness` means one of them
        # silently wins, and which one depends on iteration order — a health
        # report that changes with dictionary ordering is worse than one that
        # refuses to load.
        if probe.get("axis") in claimed:
            raise RegistryInvalid(
                f"{where} ({rid}): probes {claimed[probe['axis']]!r} and "
                f"{probe.get('id')!r} both answer axis {probe.get('axis')!r}. "
                f"One probe per axis: two answers to one question means one of "
                f"them is discarded, and which one is not a decision this "
                f"registry makes by accident."
            )
        claimed[probe.get("axis")] = probe.get("id")

    _reject_credentials(record, f"{where} ({rid})")
    return Resource(record)


class Registry:
    """Every registration on this node, loaded from one document.

    Injectable path so a test can exercise a registry without one existing.
    """

    def __init__(self, document: dict | None = None, *, path: str | Path | None = None):
        if document is None:
            if path is None:
                raise RegistryError("either `document` or `path` is required")
            document = load_yaml(Path(path))
        if document.get("schema") != "serveros/managed-runtime-registry.v1":
            raise RegistryInvalid(
                f"unexpected registry schema {document.get('schema')!r}; expected "
                f"'serveros/managed-runtime-registry.v1'"
            )
        seen: dict[str, Resource] = {}
        for i, record in enumerate(document.get("resources") or ()):
            resource = validate(record, where=f"resources[{i}]")
            if resource.id in seen:
                raise RegistryInvalid(
                    f"duplicate resource id {resource.id!r}: two registrations for "
                    f"one thing is how one of them silently wins"
                )
            seen[resource.id] = resource
        self._by_id = seen
        self._order = [r["id"] for r in (document.get("resources") or ())]
        # Refs that are protected or deferred WITHOUT being registered.
        #
        # §3 of the brief protects LIT-0103, LIT-0104 and the active business
        # worktrees. LIT-0104 has registrations below; LIT-0103 has none, and
        # until now its protection existed only as a sentence in a comment —
        # which is how a classifier written by someone reading quickly came to
        # bucket `lit-0103-r3-*` as a reclaim candidate on this very host.
        #
        # So the protection list is a document, not prose. It is deliberately
        # separate from `resources` because it is a different kind of claim:
        # `resources` says "this is ours and here is what it should do";
        # `protected_refs` says "do not touch this, whatever you think it is".
        self._protected_refs = _parse_ref_list(document.get("protected_refs"), "protected_refs")
        self._defer_refs = _parse_ref_list(document.get("defer_refs"), "defer_refs")

    def protected_refs(self) -> list[dict]:
        """Refs no automatic path may touch, registered or not."""
        return list(self._protected_refs)

    def defer_refs(self) -> list[dict]:
        """Refs carrying an unknown owner or an active writer. DEFER, always."""
        return list(self._defer_refs)

    def refs_matching(self, name: str) -> dict:
        """Which protected/deferred refs a free-text name looks like.

        Name matching is used in exactly ONE direction and this is it: a hit
        means "do not touch", never "this is yours". A name is not an owner —
        but the cost of a false positive here is that something harmless is
        left alone, and the cost of a false negative is a protected parallel
        workstream behind a disk-pressure trigger. Those are not symmetric, so
        the loose direction is the safe one.

        The match is on the ref's id, delimited by non-alphanumerics, so
        `lit-0103-r3-litellm` matches `work-item:LIT-0103` and `lit-01030`
        does not.
        """
        hits = {"protected": [], "defer": []}
        for bucket, refs in (("protected", self._protected_refs), ("defer", self._defer_refs)):
            for entry in refs:
                slug = entry["ref"].split(":", 1)[-1]
                if _NAME_MATCH(slug, name):
                    hits[bucket].append(entry)
        return hits

    def all(self) -> list[Resource]:
        return [self._by_id[i] for i in self._order]

    def get(self, resource_id: str) -> Resource:
        try:
            return self._by_id[resource_id]
        except KeyError:
            raise UnknownResource(
                f"{resource_id!r} is not a registered managed runtime. "
                f"Registered: {sorted(self._by_id)}. A resource is managed by "
                f"being registered, never by being noticed."
            ) from None

    def by_kind(self, kind: str) -> list[Resource]:
        return [r for r in self.all() if r.kind == kind]

    def by_owner(self, ref: str) -> list[Resource]:
        return [r for r in self.all() if r.owner_ref == ref]

    def by_lifecycle(self, lifecycle_class: str) -> list[Resource]:
        return [r for r in self.all() if r.lifecycle == lifecycle_class]

    def protected(self) -> list[Resource]:
        return [r for r in self.all() if r.protection == "protected"]

    def automatable(self) -> list[Resource]:
        return [r for r in self.all() if r.is_automatable]

    def __len__(self) -> int:
        return len(self._by_id)
