#!/usr/bin/env python3
"""Fleet Next V1 — the release captain's state machine, and nothing that deploys.

What this module is
-------------------
One deterministic function over one machine-readable manifest. It answers the
only question a release captain may answer before other people's work is
touched: *given these exact facts, is this exact tree allowed to reach this
exact state on this exact device — and if not, which fact is missing?*

It is not an installer, a service manager, an OAuth client or a rollout tool.
It holds no clock, opens no socket, reads no credential and writes nothing. The
`rollback` half produces a plan and can be driven against an injected executor;
the production executor that would address systemd is deliberately absent, and
its absence is reported rather than papered over with a `--force`.

The four rules this file exists to keep
---------------------------------------
1. **An UNKNOWN is not a green.** Every gate is tri-state: GREEN, RED, UNKNOWN.
   A field that was never observed produces ``BLOCKED_UNKNOWN`` and the target
   state is not reached. Silence is the most common way a release gate turns
   green by accident.
2. **Evidence belongs to a tree.** A green CI run and a green QA verdict count
   only when the SHA they name is the SHA being released. A green result for
   somebody else's commit is a different fact wearing the same word.
3. **The two tracks never share a resource.** Service unit, ports, socket,
   plugin id, backend id, OAuth audience, state root, lock, runtime root and
   config path are compared pair by pair. Any overlap is one
   ``BLOCKED_LEGACY_COLLISION`` naming the field, never a rename-aside.
4. **Rollback is Next-only and idempotent.** A plan step either targets the Next
   namespace or reads Legacy; there is no third kind. Applying a plan twice
   changes nothing the first application did not already change.

Exit codes
----------
0   the requested target is reached, or the dry run printed its plan
2   the target is blocked (the refusal is on stdout as JSON)
3   the manifest itself is malformed, or a rollback was refused
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from pathlib import Path

SCHEMA = "serveros/fleet-next-v1-release-captain.v1"

STATES = ("PRE_FLIGHT", "READY", "CANARY", "PROMOTE", "ROLLBACK", "BLOCKED")
TARGETS = ("READY", "CANARY", "PROMOTE")

UNKNOWN = "UNKNOWN"

#: Every reason this module can give. A closed tuple, because a release gate
#: that can invent a new word for "no" is a release gate you cannot audit.
REASONS = (
    "BLOCKED_UNKNOWN",
    "BLOCKED_SOURCE_IDENTITY",
    "BLOCKED_CI",
    "BLOCKED_QA",
    "BLOCKED_ARTIFACT",
    "BLOCKED_PROVENANCE",
    "BLOCKED_LEGACY_COLLISION",
    "BLOCKED_LEGACY_SNAPSHOT",
    "BLOCKED_DEVICE",
    "BLOCKED_DEVICE_GRANT",
    "BLOCKED_DEVICE_ORDER",
    "BLOCKED_RESOURCE",
    "BLOCKED_OWNER",
    "BLOCKED_LEGACY_PARITY",
)

#: Actions the owner's 2026-10-28 decision explicitly did NOT grant. Asking the
#: manifest for one of these does not fail a sub-gate; it fails the release.
FORBIDDEN_ACTIONS = frozenset({
    "main_merge",
    "legacy_takeover",
    "legacy_plugin_url_replace",
    "legacy_state_migration",
    "production_auth_secret_oauth_provider_change",
    "external_feishu_send",
    "tailscale_mutation",
    "irreversible_delete",
    "paid_model_trial",
})

#: Fields whose identity must be known before READY. `plugin_id` is absent on
#: purpose: the reviewed two-stage bootstrap cannot know it until a Next
#: endpoint has served a public origin once. PROMOTE still requires it, and
#: `promote` below is where that is enforced — the exemption is one state wide,
#: not a permanent hole.
REQUIRED_NEXT_IDENTITY = (
    "service_unit",
    "ports",
    "socket",
    "state_root",
    "lock_path",
    "backend_id",
    "oauth_audience",
    "runtime_root",
    "config_path",
)

IDENTITY_FIELDS = ("service_unit", "plugin_id", "backend_id", "oauth_audience")
PATH_FIELDS = ("state_root", "runtime_root", "config_path", "oauth_state_root",
               "lock_path", "socket")

NEXT_ONLY_KINDS = frozenset({"next-only", "read-only-legacy", "evidence-write"})

_TOP_LEVEL = frozenset({
    "schema", "release_id", "generated_at", "authority", "legacy", "next",
    "source", "ci", "qa", "artifacts", "devices", "owner_approval",
    "independent_acceptance", "notes",
})

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Small predicates. Each one is written so that the absence of a value is a
# distinct answer from a negative one.
# ---------------------------------------------------------------------------

def is_unknown(value: object) -> bool:
    """True when a fact was not observed.

    ``None``, the empty string and the literal ``"UNKNOWN"`` all mean "nobody
    looked". ``False`` and ``0`` do not: those are answers.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == "" or value.strip().upper() == UNKNOWN
    return False


def is_sha(value: object, length: int = 40) -> bool:
    pattern = _HEX40 if length == 40 else _HEX64
    return isinstance(value, str) and bool(pattern.match(value))


def _norm(path: str) -> str:
    return posixpath.normpath(path.strip())


def paths_overlap(first: object, second: object) -> bool:
    """True when one path is the other, or is nested under it.

    A prefix-sibling is not an overlap: ``/srv/ingress`` and ``/srv/ingress-next``
    are two directories. ``/srv/ingress/next`` is nested under ``/srv/ingress``
    and is one directory pretending to be two.
    """
    if is_unknown(first) or is_unknown(second):
        return False
    left, right = _norm(str(first)), _norm(str(second))
    if left == right:
        return True
    if not (left.startswith("/") and right.startswith("/")):
        # A URL or a bare name is compared exactly and never as a directory.
        return False
    try:
        common = posixpath.commonpath([left, right])
    except ValueError:  # different anchors; nothing to share
        return False
    return common in (left, right)


class ManifestRefused(ValueError):
    """The document is not a manifest this module can read."""


class RollbackRefused(RuntimeError):
    """A rollback was asked for that would not be a rollback."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class ExecutionRefused(RuntimeError):
    """An execution was asked for without the authority to perform it."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Manifest shape.
# ---------------------------------------------------------------------------

def load_manifest(path: str | Path) -> dict:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_manifest(document)


def validate_manifest(document: object) -> dict:
    """Refuse a document that is not the schema it claims to be.

    Only the envelope is checked here. The point is to fail on a typo at the
    edge rather than to read a half-understood document as a series of UNKNOWNs
    — the second one looks exactly like a release that is merely early.
    """
    if not isinstance(document, dict):
        raise ManifestRefused("manifest: expected a JSON object")
    if document.get("schema") != SCHEMA:
        raise ManifestRefused(f"manifest: schema must be {SCHEMA!r}")
    extra = set(document) - _TOP_LEVEL
    if extra:
        # Never echo values: a misplaced token must not reach a log.
        raise ManifestRefused(f"manifest: unsupported field(s) {', '.join(sorted(extra))}")
    for key in ("next", "legacy", "source"):
        if not isinstance(document.get(key), dict):
            raise ManifestRefused(f"manifest: {key} must be an object")
    devices = document.get("devices", [])
    if not isinstance(devices, list):
        raise ManifestRefused("manifest: devices must be a list")
    for index, device in enumerate(devices):
        if not isinstance(device, dict) or is_unknown(device.get("device_id")):
            raise ManifestRefused(f"manifest: devices[{index}] needs a device_id")
    return document


# ---------------------------------------------------------------------------
# Ledger: one place that remembers why a gate is not green.
# ---------------------------------------------------------------------------

class Ledger:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.checked: set[str] = set()

    def observe(self, gate: str) -> None:
        self.checked.add(gate)

    def add(self, gate: str, code: str, detail: str) -> None:
        self.checked.add(gate)
        self.items.append({"gate": gate, "code": code, "detail": detail})

    def unknown(self, gate: str, detail: str) -> None:
        self.add(gate, "BLOCKED_UNKNOWN", detail)

    def gate_state(self, gate: str) -> str:
        relevant = [item for item in self.items if item["gate"] == gate]
        if any(item["code"] != "BLOCKED_UNKNOWN" for item in relevant):
            return "RED"
        if relevant:
            return "UNKNOWN"
        return "GREEN" if gate in self.checked else "UNKNOWN"

    def codes(self) -> list[str]:
        seen: list[str] = []
        for item in self.items:
            if item["code"] not in seen:
                seen.append(item["code"])
        return seen

    def blocked(self) -> list[dict]:
        return list(self.items)


# ---------------------------------------------------------------------------
# Gates.
# ---------------------------------------------------------------------------

def _authority(manifest: dict, ledger: Ledger) -> None:
    gate = "authority"
    authority = manifest.get("authority")
    if not isinstance(authority, dict):
        ledger.unknown(gate, "no authority block: the scope of this release is unstated")
        return
    ledger.observe(gate)
    requested = authority.get("requested") or []
    if not isinstance(requested, list):
        ledger.add(gate, "BLOCKED_OWNER", "authority.requested is not a list")
        return
    for action in requested:
        if action in FORBIDDEN_ACTIONS:
            ledger.add(gate, "BLOCKED_OWNER",
                       f"{action} is outside the owner's grant and needs a separate decision")


def _source(manifest: dict, ledger: Ledger) -> None:
    gate = "source"
    source = manifest["source"]
    head = source.get("head_sha")
    tree = source.get("tree_sha")
    if is_unknown(head) or is_unknown(tree):
        ledger.unknown(gate, "composed head_sha/tree_sha not recorded")
    else:
        ledger.observe(gate)
        if not is_sha(head, 40):
            ledger.add(gate, "BLOCKED_SOURCE_IDENTITY", f"head_sha {head!r} is not a commit sha")
        if not is_sha(tree, 40):
            ledger.add(gate, "BLOCKED_SOURCE_IDENTITY", f"tree_sha {tree!r} is not a tree sha")
    components = source.get("components")
    if not isinstance(components, list) or not components:
        ledger.unknown(gate, "no source components pinned")
        return
    for component in components:
        if not isinstance(component, dict):
            ledger.add(gate, "BLOCKED_SOURCE_IDENTITY", "a component entry is not an object")
            continue
        name = component.get("pr") or component.get("name") or "<unnamed>"
        sha = component.get("sha")
        if is_unknown(sha):
            ledger.unknown(gate, f"component {name} has no pinned sha")
        elif not is_sha(sha, 40):
            ledger.add(gate, "BLOCKED_SOURCE_IDENTITY",
                       f"component {name} sha {sha!r} is not a commit sha")
        expected = component.get("expected_sha")
        if not is_unknown(expected) and expected != sha:
            ledger.add(gate, "BLOCKED_SOURCE_IDENTITY",
                       f"component {name} moved: pinned {expected} observed {sha}")


def _ci(manifest: dict, ledger: Ledger) -> None:
    gate = "ci"
    ci = manifest.get("ci")
    if not isinstance(ci, dict):
        ledger.unknown(gate, "no CI block")
        return
    head = ci.get("head_sha")
    if is_unknown(head):
        ledger.unknown(gate, "CI head_sha not recorded")
        return
    ledger.observe(gate)
    release_head = manifest["source"].get("head_sha")
    if not is_unknown(release_head) and head != release_head:
        ledger.add(gate, "BLOCKED_SOURCE_IDENTITY",
                   f"CI evidence is for {head}, release is {release_head}")
        return
    if is_unknown(ci.get("run_id")):
        # A conclusion with no run to open is a sentence, not a receipt.
        ledger.unknown(gate, "CI run id not recorded")
    required = ci.get("required_jobs")
    jobs = ci.get("jobs")
    if not isinstance(required, int) or required < 1:
        ledger.unknown(gate, "CI required_jobs is not declared")
        return
    if not isinstance(jobs, list) or not jobs:
        ledger.unknown(gate, "CI jobs were not enumerated")
        return
    if len(jobs) < required:
        ledger.add(gate, "BLOCKED_CI",
                   f"{len(jobs)} job(s) recorded, {required} required on this head")
    for job in jobs:
        name = job.get("name", "<unnamed>") if isinstance(job, dict) else "<unnamed>"
        conclusion = job.get("conclusion") if isinstance(job, dict) else None
        status = job.get("status") if isinstance(job, dict) else None
        if is_unknown(conclusion):
            if isinstance(status, str) and status in ("queued", "in_progress", "pending"):
                ledger.add(gate, "BLOCKED_CI", f"job {name} is {status}")
            else:
                ledger.unknown(gate, f"job {name} has no conclusion")
        elif conclusion != "success":
            ledger.add(gate, "BLOCKED_CI", f"job {name} concluded {conclusion}")


def _qa(manifest: dict, ledger: Ledger) -> None:
    gate = "qa"
    qa = manifest.get("qa")
    if not isinstance(qa, dict):
        ledger.unknown(gate, "no independent QA verdict")
        return
    head = qa.get("head_sha")
    verdict = qa.get("verdict")
    if is_unknown(head) or is_unknown(verdict):
        ledger.unknown(gate, "QA head_sha/verdict not recorded")
        return
    ledger.observe(gate)
    release_head = manifest["source"].get("head_sha")
    if not is_unknown(release_head) and head != release_head:
        ledger.add(gate, "BLOCKED_SOURCE_IDENTITY",
                   f"QA verdict is for {head}, release is {release_head}")
    if qa.get("independent") is not True:
        ledger.add(gate, "BLOCKED_QA", "QA was not independent of the source author")
    if verdict != "GREEN":
        ledger.add(gate, "BLOCKED_QA", f"QA verdict is {verdict}")


def _artifacts(manifest: dict, ledger: Ledger) -> None:
    gate = "artifacts"
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        ledger.unknown(gate, "no release artifacts recorded")
        return
    ledger.observe(gate)
    tree = manifest["source"].get("tree_sha")
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            ledger.add(gate, "BLOCKED_ARTIFACT", "an artifact entry is not an object")
            continue
        name = artifact.get("name", "<unnamed>")
        digest = artifact.get("sha256")
        if is_unknown(digest):
            ledger.unknown(gate, f"artifact {name} has no sha256")
            continue
        if not is_sha(str(digest), 64):
            ledger.add(gate, "BLOCKED_ARTIFACT", f"artifact {name} sha256 is not 64 hex")
        provenance = artifact.get("provenance")
        if not isinstance(provenance, dict) or not provenance:
            ledger.add(gate, "BLOCKED_PROVENANCE", f"artifact {name} has no build provenance")
            continue
        for field in ("builder", "command", "generated_at"):
            if is_unknown(provenance.get(field)):
                ledger.add(gate, "BLOCKED_PROVENANCE",
                           f"artifact {name} provenance has no {field}")
        built_tree = provenance.get("source_tree")
        if is_unknown(built_tree):
            ledger.unknown(gate, f"artifact {name} provenance has no source_tree")
        elif is_unknown(tree):
            ledger.unknown(gate, f"artifact {name} cannot be matched to an unrecorded tree")
        elif built_tree != tree:
            ledger.add(gate, "BLOCKED_PROVENANCE",
                       f"artifact {name} was built from {built_tree}, release tree is {tree}")


def _legacy_pre(manifest: dict, ledger: Ledger) -> None:
    gate = "legacy_pre"
    legacy = manifest["legacy"]
    ledger.observe(gate)
    if legacy.get("must_remain_available") is not True:
        # Not a snapshot problem: asking for a release in which Legacy does not
        # survive is asking to retire it, which is not what was approved.
        ledger.add(gate, "BLOCKED_LEGACY_PARITY",
                   "legacy.must_remain_available is not true")
    if legacy.get("identity_observed") is not True:
        # Without a measured Legacy identity, "the namespaces are disjoint" is
        # an assertion about a set one side never enumerated.
        ledger.unknown(gate, "Legacy identity was never observed; disjointness is unprovable")
    snapshot = legacy.get("snapshot_pre")
    if not isinstance(snapshot, dict):
        ledger.unknown(gate, "no Legacy pre-release snapshot")
        return
    if is_unknown(snapshot.get("digest")):
        ledger.unknown(gate, "Legacy pre snapshot has no digest")
    elif not is_sha(str(snapshot["digest"]), 64):
        ledger.add(gate, "BLOCKED_LEGACY_SNAPSHOT", "Legacy pre digest is not 64 hex")
    if is_unknown(snapshot.get("observed_at")):
        ledger.unknown(gate, "Legacy pre snapshot has no observed_at")
    for field in ("service_unit", "version"):
        if is_unknown(snapshot.get(field)):
            ledger.unknown(gate, f"Legacy pre snapshot has no {field}")


def _namespace(manifest: dict, ledger: Ledger) -> list[dict]:
    gate = "namespace"
    nxt = manifest["next"]
    legacy = manifest["legacy"]
    ledger.observe(gate)
    collisions: list[dict] = []
    for field in REQUIRED_NEXT_IDENTITY:
        value = nxt.get(field)
        # An empty list is not an identity either: a Next service that binds no
        # port has not said which port it is not sharing.
        if isinstance(value, list) and not value:
            ledger.unknown(gate, f"Next identity has no {field}")
        elif is_unknown(value):
            ledger.unknown(gate, f"Next identity has no {field}")
    for field in IDENTITY_FIELDS:
        left, right = nxt.get(field), legacy.get(field)
        if is_unknown(left) or is_unknown(right):
            continue
        if left == right:
            collisions.append({"field": field, "detail": f"both tracks use {left}"})
    for field in PATH_FIELDS:
        if paths_overlap(nxt.get(field), legacy.get(field)):
            collisions.append({"field": field,
                               "detail": f"nested or identical: {nxt.get(field)}"})
    next_ports = nxt.get("ports") or []
    legacy_ports = legacy.get("ports") or []
    if isinstance(next_ports, list) and isinstance(legacy_ports, list):
        shared = sorted({port for port in next_ports if port in legacy_ports})
        if shared:
            collisions.append({"field": "ports", "detail": f"both tracks bind {shared}"})
    for collision in collisions:
        ledger.add(gate, "BLOCKED_LEGACY_COLLISION",
                   f"{collision['field']}: {collision['detail']}")
    return collisions


def _device(manifest: dict, device: dict, ledger: Ledger, *, target: str) -> None:
    """Gate one device.

    The order rule is not a nicety: Tokyo acceptance is what makes Dell and FJ
    a rollout rather than three simultaneous first attempts.
    """
    order = device.get("order")
    earlier = [d for d in manifest.get("devices", [])
               if isinstance(d.get("order"), int) and isinstance(order, int)
               and d["order"] < order]

    gate = "device_order"
    ledger.observe(gate)
    for prior in earlier:
        if prior.get("accepted") is not True:
            ledger.add(gate, "BLOCKED_DEVICE_ORDER",
                       f"{prior['device_id']} has not accepted a canary yet")
        elif prior.get("independent_acceptance") is not True:
            ledger.add(gate, "BLOCKED_DEVICE_ORDER",
                       f"{prior['device_id']} accepted without independent acceptance")

    gate = "device_grant"
    ledger.observe(gate)
    grant = device.get("grant")
    if not isinstance(grant, dict):
        ledger.unknown(gate, "no local operator grant recorded")
    elif grant.get("present") is None:
        ledger.unknown(gate, "local operator grant was not observed")
    elif grant.get("present") is not True:
        ledger.add(gate, "BLOCKED_DEVICE_GRANT", "no local operator grant")
    else:
        if grant.get("source") != "local-operator":
            ledger.add(gate, "BLOCKED_DEVICE_GRANT",
                       f"grant source is {grant.get('source')!r}, not a local operator")
        if is_unknown(grant.get("approved_at")):
            ledger.add(gate, "BLOCKED_DEVICE_GRANT", "grant has no approved_at")
        scope = grant.get("scope")
        if not isinstance(scope, list) or not scope:
            ledger.add(gate, "BLOCKED_DEVICE_GRANT", "grant names no scope")

    gate = "device_reachability"
    ledger.observe(gate)
    reachability = device.get("reachability")
    if not isinstance(reachability, dict):
        ledger.unknown(gate, "device reachability was not observed")
    elif reachability.get("reachable") is None:
        ledger.unknown(gate, "device reachability is unknown")
    elif reachability.get("reachable") is not True:
        error = reachability.get("error") or "unreachable"
        ledger.add(gate, "BLOCKED_DEVICE", f"device unreachable: {error}")
    elif is_unknown(reachability.get("observed_at")):
        ledger.unknown(gate, "reachability has no observed_at")

    gate = "device_resource"
    ledger.observe(gate)
    resource = device.get("resource")
    if not isinstance(resource, dict):
        ledger.unknown(gate, "resource headroom was not measured")
    elif resource.get("checked") is not True:
        ledger.unknown(gate, "resource headroom was not checked")
    elif resource.get("conflict") is True:
        ledger.add(gate, "BLOCKED_RESOURCE", "another actor holds a conflicting resource")
    elif resource.get("conflict") is None:
        ledger.unknown(gate, "resource conflict is unknown")

    gate = "device_rollback"
    ledger.observe(gate)
    rollback = device.get("rollback")
    if not isinstance(rollback, dict):
        ledger.unknown(gate, "no rollback manifest for this device")
    elif is_unknown(rollback.get("manifest")):
        ledger.unknown(gate, "rollback manifest is not recorded")
    elif rollback.get("verified") is not True:
        ledger.unknown(gate, "rollback has not been dry-run and verified")

    if target == "PROMOTE":
        gate = "device_canary"
        ledger.observe(gate)
        canary = device.get("canary")
        if not isinstance(canary, dict):
            ledger.unknown(gate, "no canary result")
        else:
            probes = canary.get("probes")
            if not isinstance(probes, list) or not probes:
                ledger.unknown(gate, "canary ran no probes")
            else:
                for probe in probes:
                    verdict = probe.get("verdict") if isinstance(probe, dict) else None
                    name = probe.get("name", "<unnamed>") if isinstance(probe, dict) else "<unnamed>"
                    if is_unknown(verdict):
                        ledger.unknown(gate, f"probe {name} has no verdict")
                    elif verdict != "PASS":
                        ledger.add(gate, "BLOCKED_DEVICE", f"probe {name} is {verdict}")
            if canary.get("legacy_during") is not True:
                ledger.add(gate, "BLOCKED_LEGACY_PARITY",
                           "Legacy was not observed serving during the canary")


def _promote(manifest: dict, ledger: Ledger) -> None:
    gate = "plugin_registration"
    ledger.observe(gate)
    plugin_id = manifest["next"].get("plugin_id")
    legacy_plugin = manifest["legacy"].get("plugin_id")
    if is_unknown(plugin_id):
        ledger.unknown(gate, "Next plugin_id is not registered")
    elif plugin_id == legacy_plugin:
        ledger.add(gate, "BLOCKED_LEGACY_COLLISION", "Next would reuse the Legacy plugin id")

    gate = "legacy_post"
    ledger.observe(gate)
    legacy = manifest["legacy"]
    pre, post = legacy.get("snapshot_pre"), legacy.get("snapshot_post")
    if not isinstance(post, dict):
        ledger.unknown(gate, "no Legacy post-release snapshot")
    elif is_unknown(post.get("digest")):
        ledger.unknown(gate, "Legacy post snapshot has no digest")
    elif isinstance(pre, dict) and not is_unknown(pre.get("digest")) \
            and post.get("digest") != pre.get("digest"):
        ledger.add(gate, "BLOCKED_LEGACY_SNAPSHOT",
                   "Legacy identity drifted across the canary")

    gate = "independent_acceptance"
    ledger.observe(gate)
    acceptance = manifest.get("independent_acceptance")
    if not isinstance(acceptance, dict):
        ledger.unknown(gate, "no independent acceptance record")
    elif acceptance.get("verdict") != "PASS":
        ledger.add(gate, "BLOCKED_QA",
                   f"independent acceptance is {acceptance.get('verdict')!r}")

    gate = "owner_approval"
    ledger.observe(gate)
    approval = manifest.get("owner_approval")
    if not isinstance(approval, dict):
        ledger.unknown(gate, "no owner approval record")
    elif approval.get("granted") is not True:
        ledger.add(gate, "BLOCKED_OWNER", "owner approval was not granted")
    else:
        if is_unknown(approval.get("reference")):
            ledger.add(gate, "BLOCKED_OWNER", "owner approval names no reference")
        if is_unknown(approval.get("granted_at")):
            ledger.add(gate, "BLOCKED_OWNER", "owner approval has no granted_at")


# ---------------------------------------------------------------------------
# Evaluation.
# ---------------------------------------------------------------------------

def evaluate(manifest: dict, target: str = "READY", device_id: str | None = None) -> dict:
    """Answer one target state. ``BLOCKED`` carries every reason, not the first."""
    if target not in TARGETS:
        raise ValueError(f"target must be one of {TARGETS}, not {target!r}")
    ledger = Ledger()
    _authority(manifest, ledger)
    _source(manifest, ledger)
    _ci(manifest, ledger)
    _qa(manifest, ledger)
    _artifacts(manifest, ledger)
    _legacy_pre(manifest, ledger)
    _namespace(manifest, ledger)

    device = None
    if target in ("CANARY", "PROMOTE"):
        devices = sorted(manifest.get("devices", []), key=lambda d: d.get("order") or 0)
        if not devices:
            ledger.unknown("device_order", "no devices declared")
        else:
            if device_id is None:
                device = devices[0]
            else:
                matches = [d for d in devices if d.get("device_id") == device_id]
                if not matches:
                    raise ManifestRefused(f"unknown device_id {device_id!r}")
                device = matches[0]
            _device(manifest, device, ledger, target=target)

    if target == "PROMOTE":
        _promote(manifest, ledger)

    gates = sorted(ledger.checked)
    state = target if not ledger.items else "BLOCKED"
    return {
        "schema": SCHEMA,
        "release_id": manifest.get("release_id", UNKNOWN),
        "target": target,
        "device": device.get("device_id") if device else None,
        "state": state,
        "codes": ledger.codes(),
        "blocked": ledger.blocked(),
        "gates": {gate: ledger.gate_state(gate) for gate in gates},
        "next_actions": [
            f"{item['gate']}: {item['detail']}" for item in ledger.blocked()
        ],
    }


def release_state(manifest: dict) -> dict:
    """The furthest state the recorded facts actually reach.

    ``PRE_FLIGHT`` is not a synonym for ``BLOCKED``. It means collection has
    begun and every open item is a *missing* observation — nobody has said no
    yet. The moment any gate is red, the state is ``BLOCKED``, because a red is
    a fact and an absent is not.
    """
    ready = evaluate(manifest, "READY")
    targets = {"READY": ready}
    if ready["state"] == "BLOCKED":
        state = _blocked_or_preflight(ready)
    else:
        first = sorted(manifest.get("devices", []), key=lambda d: d.get("order") or 0)
        canary_results = []
        for device in first:
            result = evaluate(manifest, "CANARY", device["device_id"])
            canary_results.append(result)
        targets["CANARY"] = canary_results
        if canary_results and all(r["state"] == "CANARY" for r in canary_results):
            promote = [evaluate(manifest, "PROMOTE", d["device_id"]) for d in first]
            targets["PROMOTE"] = promote
            state = "PROMOTE" if all(r["state"] == "PROMOTE" for r in promote) else "CANARY"
        else:
            state = "CANARY" if canary_results and canary_results[0]["state"] == "CANARY" else "READY"
    return {
        "schema": SCHEMA,
        "release_id": manifest.get("release_id", UNKNOWN),
        "state": state,
        "targets": targets,
    }


def _blocked_or_preflight(result: dict) -> str:
    if result["codes"] and all(code == "BLOCKED_UNKNOWN" for code in result["codes"]):
        return "PRE_FLIGHT"
    return "BLOCKED"


def plan(manifest: dict) -> dict:
    """The ordered steps a release would take, as text. Executes nothing."""
    devices = sorted(manifest.get("devices", []), key=lambda d: d.get("order") or 0)
    steps = [
        "collect: pin composed head_sha/tree_sha for every component",
        "collect: run hosted CI on that exact head; require 5/5",
        "collect: independent QA verdict on the same tree",
        "build: reproducible artifacts with sha256 and provenance from that tree",
        "collect: Legacy pre snapshot (unit, version, digest, observed_at)",
        "check: Next namespace disjoint from Legacy on all 10 resources",
    ]
    for device in devices:
        steps.append(f"gate: local operator grant on {device['device_id']}")
        steps.append(f"gate: reachability and resource headroom on {device['device_id']}")
        steps.append(f"dry-run: Next-only rollback for {device['device_id']}")
    for device in devices:
        steps.append(f"canary: start Next only on {device['device_id']}, probe, observe Legacy")
        steps.append(f"accept: independent acceptance for {device['device_id']}")
    steps.append("promote: owner approval; register Next plugin id; never replace Legacy")
    return {"schema": SCHEMA, "mode": "DRY_RUN", "writes": [], "executes": [],
            "steps": steps}


# ---------------------------------------------------------------------------
# Rollback: a plan, a refusal, and an idempotent simulator.
# ---------------------------------------------------------------------------

def build_rollback_plan(manifest: dict, device_id: str | None = None) -> dict:
    """Build a Next-only rollback plan for one device.

    Two modes, because "roll back" means two different things depending on
    whether this device has ever run Next:

    * ``revert`` — a previous Next revision exists; repoint ``current`` at it.
    * ``withdraw`` — this was the first Next install; stop and remove the Next
      unit and the Next state root, and leave Legacy untouched.

    A ``revert`` whose previous revision equals the installed one is refused:
    that is a no-op wearing the word rollback.
    """
    devices = sorted(manifest.get("devices", []), key=lambda d: d.get("order") or 0)
    if device_id is None:
        if not devices:
            raise RollbackRefused("ROLLBACK_NO_DEVICE", "no devices declared")
        device = devices[0]
    else:
        matches = [d for d in devices if d.get("device_id") == device_id]
        if not matches:
            raise RollbackRefused("ROLLBACK_NO_DEVICE", f"unknown device_id {device_id!r}")
        device = matches[0]

    nxt = manifest["next"]
    installed = nxt.get("installed_revision")
    previous = nxt.get("previous_revision")
    if is_unknown(installed):
        raise RollbackRefused("ROLLBACK_NOTHING_INSTALLED",
                              "Next has no recorded installed revision")
    if not is_unknown(previous) and previous == installed:
        raise RollbackRefused("ROLLBACK_SAME_REVISION",
                              f"previous and installed are both {installed}")

    mode = "withdraw" if is_unknown(previous) else "revert"
    unit = nxt.get("service_unit")
    state_root = nxt.get("state_root")
    runtime_root = nxt.get("runtime_root")
    steps: list[dict] = [
        {"id": "stop-next", "kind": "next-only", "idempotent": True,
         "action": "systemctl --user stop", "target": unit},
        {"id": "disable-next", "kind": "next-only", "idempotent": True,
         "action": "systemctl --user disable", "target": unit},
    ]
    if mode == "revert":
        steps.append({"id": "repoint-current", "kind": "next-only", "idempotent": True,
                      "action": "symlink current -> previous", "target": runtime_root,
                      "from": installed, "to": previous})
        steps.append({"id": "start-next", "kind": "next-only", "idempotent": True,
                      "action": "systemctl --user start", "target": unit})
    else:
        steps.append({"id": "remove-next-unit", "kind": "next-only", "idempotent": True,
                      "action": "remove unit file", "target": unit})
        steps.append({"id": "remove-next-state", "kind": "next-only", "idempotent": True,
                      "action": "remove Next state root", "target": state_root})
    steps.extend([
        {"id": "probe-next", "kind": "next-only", "idempotent": True,
         "action": "confirm Next endpoint is not serving", "target": nxt.get("public_mcp_url")},
        {"id": "probe-legacy", "kind": "read-only-legacy", "idempotent": True,
         "action": "confirm Legacy still answers", "target": manifest["legacy"].get("service_unit")},
        {"id": "preserve-evidence", "kind": "evidence-write", "idempotent": True,
         "action": "write rollback receipt", "target": "evidence"},
    ])
    document = {
        "schema": SCHEMA,
        "device_id": device["device_id"],
        "mode": mode,
        "installed_revision": installed,
        "previous_revision": previous,
        "steps": steps,
    }
    assert_next_only(document, manifest)
    return document


def assert_next_only(rollback: dict, manifest: dict) -> None:
    """Refuse a plan that would touch the Legacy track.

    Reading Legacy is allowed and necessary — the point of a Next-only rollback
    is that Legacy keeps answering across it. Writing it is not.
    """
    legacy = manifest["legacy"]
    forbidden_targets = {legacy.get("service_unit")} - {None}
    legacy_paths = [legacy.get(field) for field in PATH_FIELDS if not is_unknown(legacy.get(field))]
    for step in rollback.get("steps", []):
        kind = step.get("kind")
        if kind not in NEXT_ONLY_KINDS:
            raise RollbackRefused("ROLLBACK_UNKNOWN_STEP_KIND", f"step {step.get('id')} is {kind!r}")
        target = step.get("target")
        if kind == "read-only-legacy":
            continue
        if target in forbidden_targets:
            raise RollbackRefused("ROLLBACK_TOUCHES_LEGACY",
                                  f"step {step.get('id')} targets {target}")
        for path in legacy_paths:
            if paths_overlap(target, path):
                raise RollbackRefused("ROLLBACK_TOUCHES_LEGACY",
                                      f"step {step.get('id')} targets {target}")


def rollback_dry_run(manifest: dict, device_id: str | None = None,
                     applied: list[str] | None = None) -> dict:
    """Show the plan without doing any of it.

    ``applied`` is the list of step ids a previous run claims to have done. The
    simulator reports them as skipped and re-computes the same final state,
    which is what makes a second rollback call harmless rather than a second
    mutation.
    """
    already = list(applied or [])
    plan_document = build_rollback_plan(manifest, device_id)
    done = [step["id"] for step in plan_document["steps"] if step["id"] in already]
    pending = [step["id"] for step in plan_document["steps"] if step["id"] not in already]
    return {
        "schema": SCHEMA,
        "mode": "DRY_RUN",
        "device_id": plan_document["device_id"],
        "rollback_mode": plan_document["mode"],
        "writes": [],
        "executes": [],
        "applied": done,
        "pending": pending,
        "changed": bool(pending),
        "steps": plan_document["steps"],
    }


def execute_plan(rollback: dict, executor, *, manifest: dict,
                 owner_approval: dict | None = None, evidence_dir: str | None = None,
                 environment: str = "canary", applied: list[str] | None = None) -> dict:
    """Drive an injected executor through a plan, under the release gates.

    The production caller passes no executor, and this function is not a place
    to grow one: it refuses ``environment`` anything but ``canary``, refuses an
    execution with no owner approval, and refuses an execution with no evidence
    directory. Every refusal is by name.
    """
    if environment != "canary":
        raise ExecutionRefused("BLOCKED_OWNER",
                               f"environment {environment!r} is not the approved canary")
    if not isinstance(owner_approval, dict) or owner_approval.get("granted") is not True:
        raise ExecutionRefused("BLOCKED_OWNER", "no owner approval for execution")
    if is_unknown(evidence_dir):
        raise ExecutionRefused("BLOCKED_OWNER", "no evidence directory for the receipt")
    assert_next_only(rollback, manifest)
    already = set(applied or ())
    executed: list[str] = []
    skipped: list[str] = []
    for step in rollback["steps"]:
        if step["id"] in already:
            skipped.append(step["id"])
            continue
        executor(step)
        executed.append(step["id"])
    return {"schema": SCHEMA, "mode": "EXECUTE", "environment": environment,
            "executed": executed, "skipped": skipped,
            "writes": [evidence_dir], "executes": executed}


# ---------------------------------------------------------------------------
# Receipt: the compact document that goes into an issue.
# ---------------------------------------------------------------------------

def receipt(manifest: dict) -> dict:
    state = release_state(manifest)
    devices = []
    for device in sorted(manifest.get("devices", []), key=lambda d: d.get("order") or 0):
        result = evaluate(manifest, "CANARY", device["device_id"])
        devices.append({
            "device_id": device["device_id"],
            "order": device.get("order"),
            "canary_target": result["state"],
            "codes": result["codes"],
        })
    return {
        "schema": SCHEMA,
        "release_id": manifest.get("release_id", UNKNOWN),
        "state": state["state"],
        "source": {
            "head_sha": manifest["source"].get("head_sha", UNKNOWN),
            "tree_sha": manifest["source"].get("tree_sha", UNKNOWN),
            "components": [
                {"pr": c.get("pr"), "sha": c.get("sha")}
                for c in manifest["source"].get("components", [])
            ],
        },
        "devices": devices,
        "deploys_nothing": True,
    }


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def _emit(document: dict) -> None:
    json.dump(document, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("evaluate", "state", "plan", "receipt"):
        sub = subparsers.add_parser(name)
        if name == "evaluate":
            sub.add_argument("--target", default="READY", choices=TARGETS)
            sub.add_argument("--device")

    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--device")
    rollback.add_argument("--applied", default="")
    rollback.add_argument("--execute", action="store_true")
    rollback.add_argument("--owner-approved", default="")
    rollback.add_argument("--evidence", default="")

    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
    except ManifestRefused as error:
        _emit({"schema": SCHEMA, "state": "BLOCKED", "error": "MANIFEST_REFUSED",
               "detail": str(error)})
        return 3

    try:
        if args.command == "evaluate":
            document = evaluate(manifest, args.target, args.device)
        elif args.command == "state":
            document = release_state(manifest)
        elif args.command == "plan":
            document = plan(manifest)
        elif args.command == "receipt":
            document = receipt(manifest)
        else:  # rollback
            applied = [item for item in args.applied.split(",") if item]
            if args.execute:
                # The only executor that exists is the caller's. This CLI has
                # none, and says so rather than pretending a dry run is a run.
                _emit({"schema": SCHEMA, "state": "BLOCKED",
                       "error": "EXECUTOR_ABSENT",
                       "detail": "this tool plans; execution belongs to the device owner",
                       "owner_approved": bool(args.owner_approved),
                       "evidence": args.evidence or None})
                return 2
            document = rollback_dry_run(manifest, args.device, applied)
    except (RollbackRefused, ExecutionRefused) as error:
        _emit({"schema": SCHEMA, "state": "BLOCKED", "error": error.code,
               "detail": error.detail})
        return 3

    _emit(document)
    return 2 if document.get("state") == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
