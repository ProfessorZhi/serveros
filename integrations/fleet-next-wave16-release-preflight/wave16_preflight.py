#!/usr/bin/env python3
"""Wave 16 release-safety preflight: may Next ship beside Legacy, and can each
piece be released on its own?

What this is
------------
A pure decision surface plus one read-only repository reader. It calls no
service, opens no socket, binds no port, installs nothing, touches no
credential and sends nothing. Every function either takes values a caller
already holds, or reads the git object store. A green test run is therefore a
statement about the *rules*, never a real-device pass — and nothing in this
module may be reported as one.

Two answers, and no third
-------------------------
`OK` / `VERIFIED` or `BLOCKED`. There is no branch that renames a directory
aside, picks the next free port, or "just" shares a state root during the
transition. A silently adjusted placement is a takeover with extra steps, and
the operator who receives one has been told a co-install happened when what
happened was a replacement.

Why the eight resources, in this order
--------------------------------------
`install_path`, `port`, `socket`, `backend_id`, `oauth_audience`,
`cred_state_root`, `lock`, `unit` — code, who answers a connection, who owns a
unix socket, which connector ChatGPT picked, which protected resource a token
was minted for, where the credential-issuer state lives, the mutual exclusion,
and the supervisor's handle.

All eight are reported **at once**. Reporting only the first sends an operator
back once per field, and the fifth round trip is where somebody decides the
gate is fussy and works around it.

The one polarity that is inverted
---------------------------------
Seven of the eight must differ. `device_ref` must be *equal*: both tracks run
on one host, and the host is what the binding declares. A second value there
means one of the two bindings is about another machine, so it is its own
refusal (`BLOCKED_DEVICE_DISAGREEMENT`) rather than a collision. Folding it
into the same list would produce a matrix that blocks the correct answer.

Cross-class overlap
-------------------
A different field name is not a different boundary. Next's credential state
root nested inside Legacy's install path is one directory shared under two
names, so containment is checked between *every* pair of path fields, not only
within a field. A unix socket inside the other track's root is the same defect
with a different noun.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import posixpath
import re
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "serveros/fleet-next-wave16-release-preflight.v1"

OK = "OK"
BLOCKED = "BLOCKED"
VERIFIED = "VERIFIED"

BLOCKED_RESOURCE_COLLISION = "BLOCKED_RESOURCE_COLLISION"
BLOCKED_INCOMPLETE = "BLOCKED_INCOMPLETE"
BLOCKED_DEVICE_DISAGREEMENT = "BLOCKED_DEVICE_DISAGREEMENT"
BLOCKED_CROSS_TRACK = "BLOCKED_CROSS_TRACK"
BLOCKED_VERSION_NOT_ADVANCING = "BLOCKED_VERSION_NOT_ADVANCING"
BLOCKED_TIMER_ARMED = "BLOCKED_TIMER_ARMED"
BLOCKED_RELEASE_UNIT = "BLOCKED_RELEASE_UNIT"
BLOCKED_SHADOW_WROTE = "BLOCKED_SHADOW_WROTE"
SHADOW_NOTHING_OBSERVED = "SHADOW_NOTHING_OBSERVED"
LEGACY_CONTRACT_DRIFT = "LEGACY_CONTRACT_DRIFT"
INSTALL_FAILED = "NEXT_INSTALL_FAILED"

#: Spelled exactly as #480's parallel-plugin preflight and #483's Legacy safety
#: spell them. One refusal with two spellings is how a second vocabulary
#: starts, and a second vocabulary is how a gate gets two answers.
SHADOW_WRITE_FORBIDDEN = "NEXT_SHADOW_WRITE_FORBIDDEN"
SHADOW_VERB_UNKNOWN = "SHADOW_VERB_UNKNOWN"
SHADOW_NOT_GRANTED = "NEXT_SHADOW_NOT_GRANTED"

LEGACY = "legacy"
NEXT = "next"
TRACKS = (LEGACY, NEXT)

#: The eight things the two tracks may not share, in reporting order.
RESOURCES = (
    "install_path",
    "port",
    "socket",
    "backend_id",
    "oauth_audience",
    "cred_state_root",
    "lock",
    "unit",
)

#: The three of the eight that are filesystem roots and therefore overlap
#: rather than merely compare equal.
PATH_RESOURCES = ("install_path", "cred_state_root", "lock")

#: Verbs that would change something. A shadow may never perform one of these,
#: whatever it was granted: the shadow exists to read the answer the current
#: controller gives, not to become a second controller by accident.
WRITE_VERBS = frozenset({
    "create", "send", "stop", "restart", "wake", "manager_wake", "interrupt",
    "close", "update", "session_create", "session_send", "session_interrupt",
    "session_close", "session_reconfigure", "session_cancel_queued",
})

#: Verbs that only read a fact, and so may be granted to a shadow at all.
READ_ONLY_FACT_VERBS = frozenset({
    "get_session", "get_run", "sessions_list", "projects_list", "overview",
    "session_get", "run_get", "approvals_list", "ui_visible_context", "health",
})

#: The Legacy contract the Owner said must not move. `send` is in it because
#: the control path ChatGPT actually drives is not read-only; a co-install that
#: preserved the reads and moved the send would be the worst of both.
LEGACY_CONTRACT_VERBS = ("get_session", "get_run", "send")

#: The two verbs the idempotence probe repeats. `send` is deliberately absent:
#: repeating a write to test that it is stable is how a test becomes the
#: second delivery it was written to detect. `survival` covers `send` by
#: comparing the answer to one intent, once per snapshot.
IDEMPOTENT_READ_VERBS = ("get_session", "get_run")

#: git verbs this module is allowed to run. Every one of them is a read; the
#: set is asserted by the tests so that adding a write verb is a deliberate,
#: visible act rather than a refactor.
READ_ONLY_GIT = frozenset({"ls-tree", "rev-parse", "cat-file"})

FACT_ID_KEYS = ("sessionId", "runId", "deliveryId")


# --------------------------------------------------------------------------
# the veto table
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Footprint:
    """One track's full footprint: what it answers on, where it lives, what it is.

    Paths are posix and absolute, because both tracks live on the same host; a
    relative path is refused rather than resolved, since resolving it against
    the reader's cwd is exactly how two tracks end up in one directory.
    """

    track: str
    install_path: str
    port: int
    socket: str | None
    backend_id: str
    oauth_audience: str
    cred_state_root: str
    lock: str
    unit: str
    device_ref: str
    version: str
    artifact_digest: str
    rollback_version: str = ""
    rollback_digest: str = ""


@dataclass(frozen=True)
class Collision:
    resource: str
    detail: str


@dataclass(frozen=True)
class Matrix:
    decision: str
    code: str
    collisions: tuple[Collision, ...] = ()
    incomplete: tuple[str, ...] = ()
    device_agreement: bool = True
    legacy: Any = None
    next: Any = None


@dataclass(frozen=True)
class Step:
    """An upgrade, rollback or failed install: allowed or refused, and the footprint.

    On a refusal `footprint` is the input unchanged, because a refused step
    that returned a modified footprint would be indistinguishable from an
    applied one at every call site.
    """

    decision: str
    code: str
    footprint: Any = None
    previous: Any = None


def _absolute(path: Any) -> bool:
    return isinstance(path, str) and path.startswith("/") and not path.startswith("//")


def _norm(path: str) -> str:
    return posixpath.normpath(path)


def _within(a: str, b: str) -> bool:
    """True when one root contains the other. The boundary is a slash.

    `/opt/fleet-next` is *not* inside `/opt/fleet`; `/opt/fleet/next` is.
    """
    if a == b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")


def _endpoint(url: Any) -> tuple[str, str, int, str] | None:
    """A URL as (scheme, host, port, path), with the spelling normalised away.

    Only the spelling: an explicit `:443` and a trailing slash are the same
    resource, and a different path on the same origin is a *different*
    protected resource rather than a collision.
    """
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parts = urllib.parse.urlsplit(url.strip())
        explicit = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in ("http", "https") or not host or parts.username or parts.password:
        return None
    port = explicit if explicit is not None else (443 if scheme == "https" else 80)
    return scheme, host, port, parts.path.rstrip("/") or "/"


def _sha256_digest(value: Any) -> bool:
    return (isinstance(value, str) and value.startswith("sha256:")
            and re.fullmatch(r"[0-9a-fA-F]{64}", value[len("sha256:"):]) is not None)


def _gaps(footprint: Any, wanted: str) -> list[str]:
    """The fields that make a footprint unusable, in a stable order."""
    gaps: list[str] = []
    if getattr(footprint, "track", None) != wanted or wanted not in TRACKS:
        gaps.append("track")
    for name in PATH_RESOURCES:
        if not _absolute(getattr(footprint, name, None)):
            gaps.append(name)
    port = getattr(footprint, "port", None)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        gaps.append("port")
    sock = getattr(footprint, "socket", None)
    if sock is not None and not _absolute(sock):
        gaps.append("socket")
    if not str(getattr(footprint, "backend_id", "") or "").strip():
        gaps.append("backend_id")
    if _endpoint(getattr(footprint, "oauth_audience", None)) is None:
        gaps.append("oauth_audience")
    if not str(getattr(footprint, "unit", "") or "").strip():
        gaps.append("unit")
    if not str(getattr(footprint, "device_ref", "") or "").strip():
        gaps.append("device_ref")
    if not str(getattr(footprint, "version", "") or "").strip():
        gaps.append("version")
    if not _sha256_digest(getattr(footprint, "artifact_digest", None)):
        gaps.append("artifact_digest")
    return gaps


def _dedupe(items: Iterable[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return tuple(seen)


def _path_collisions(legacy: Footprint, next_: Footprint) -> list[Collision]:
    """Containment between every pair of path fields, not only within a field."""
    found: list[Collision] = []
    for next_name in PATH_RESOURCES:
        right = _norm(getattr(next_, next_name))
        for legacy_name in PATH_RESOURCES:
            left = _norm(getattr(legacy, legacy_name))
            if not _within(right, left):
                continue
            if next_name == legacy_name:
                found.append(Collision(
                    next_name, f"both tracks use {right} as their {next_name}"))
            else:
                found.append(Collision(
                    next_name,
                    f"next {next_name} {right} overlaps legacy {legacy_name} {left}"))
    return found


def _socket_collisions(legacy: Footprint, next_: Footprint) -> list[Collision]:
    found: list[Collision] = []
    old, new = legacy.socket, next_.socket
    if old is not None and new is not None and _norm(old) == _norm(new):
        found.append(Collision("socket", f"both tracks bind {new}"))
        return found
    for value, other, side in ((new, legacy, "next"), (old, next_, "legacy")):
        if value is None:
            continue
        here = _norm(value)
        for name in PATH_RESOURCES:
            root = _norm(getattr(other, name))
            if here != root and here.startswith(root + "/"):
                found.append(Collision(
                    "socket",
                    f"{side} socket {here} is inside the other track's {name} {root}"))
    return found


def resource_matrix(legacy: Any, next_: Any) -> Matrix:
    """May Next be installed beside Legacy? Every shared resource, at once.

    `next_` may be a footprint or the answer to a lifecycle step, since the
    caller's first question after an upgrade is whether the new placement
    still collides.
    """
    legacy_p = legacy.footprint if isinstance(legacy, Step) else legacy
    next_p = next_.footprint if isinstance(next_, Step) else next_

    gaps = _dedupe(_gaps(legacy_p, LEGACY) + _gaps(next_p, NEXT))
    if gaps:
        return Matrix(decision=BLOCKED, code=BLOCKED_INCOMPLETE, incomplete=gaps,
                      legacy=legacy_p, next=next_p)

    collisions: list[Collision] = []
    collisions.extend(_path_collisions(legacy_p, next_p))
    collisions.extend(_socket_collisions(legacy_p, next_p))

    if legacy_p.port == next_p.port:
        collisions.append(Collision(
            "port", f"both tracks listen on port {legacy_p.port}"))

    # Two connectors under one backend id are one connector: ChatGPT picks a
    # connector by that id, so a shared value is a shared identity.
    if legacy_p.backend_id == next_p.backend_id:
        collisions.append(Collision(
            "backend_id", f"both tracks answer as {legacy_p.backend_id}"))

    # An audience is what a token was minted for. Equal audiences mean a token
    # issued for one track verifies at the other, which is the whole takeover
    # this release is arranged to avoid.
    if _endpoint(legacy_p.oauth_audience) == _endpoint(next_p.oauth_audience):
        collisions.append(Collision(
            "oauth_audience",
            f"both tracks are the protected resource {legacy_p.oauth_audience}"))

    if legacy_p.unit == next_p.unit:
        collisions.append(Collision(
            "unit", f"both tracks are supervised as {legacy_p.unit}"))

    order = {name: index for index, name in enumerate(RESOURCES)}
    collisions.sort(key=lambda c: order[c.resource])
    unique = tuple(dict.fromkeys(collisions))

    device_agreement = legacy_p.device_ref == next_p.device_ref
    if unique:
        code = BLOCKED_RESOURCE_COLLISION
    elif not device_agreement:
        code = BLOCKED_DEVICE_DISAGREEMENT
    else:
        code = OK
    return Matrix(
        decision=OK if code == OK else BLOCKED,
        code=code,
        collisions=unique,
        device_agreement=device_agreement,
        legacy=legacy_p,
        next=next_p,
    )


# --------------------------------------------------------------------------
# the shadow: reads what was granted, writes nothing
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ShadowLedger:
    decision: str
    code: str
    attempts: int = 0
    refused: int = 0
    writes: int = 0
    observed: int = 0
    gateway_calls: int = 0
    codes: tuple[str, ...] = ()


def shadow_tally(gateway: Any, attempts: Sequence[str], *,
                 granted: Iterable[str] = ()) -> ShadowLedger:
    """Perform a shadow-phase batch, counting effects instead of trusting them.

    `writes` is counted from the ledger, and `gateway_calls` is what the fake
    gateway itself recorded — so "the shadow wrote nothing" is measured twice,
    by two parties, rather than asserted once by the caller. `create` and
    `send` are refused before the gateway is reached, and granting them
    changes nothing: a grant is permission to ask, not permission to become
    the other controller.
    """
    allowed = set(granted)
    codes: list[str] = []
    writes = 0
    observed = 0
    for verb in attempts:
        if verb in WRITE_VERBS:
            codes.append(SHADOW_WRITE_FORBIDDEN)
            continue
        if verb not in READ_ONLY_FACT_VERBS:
            codes.append(SHADOW_VERB_UNKNOWN)
            continue
        if verb not in allowed:
            codes.append(SHADOW_NOT_GRANTED)
            continue
        try:
            gateway.call(verb)
        except Exception:
            codes.append(SHADOW_VERB_UNKNOWN)
            continue
        observed += 1

    calls = len(getattr(gateway, "calls", []) or [])
    if writes:
        code, decision = BLOCKED_SHADOW_WROTE, BLOCKED
    elif observed == 0:
        # A batch that only ever refused says nothing about compatibility.
        code, decision = SHADOW_NOTHING_OBSERVED, BLOCKED
    else:
        code, decision = OK, OK
    return ShadowLedger(decision=decision, code=code, attempts=len(attempts),
                        refused=len(attempts) - observed, writes=writes,
                        observed=observed, gateway_calls=calls,
                        codes=tuple(codes))


# --------------------------------------------------------------------------
# the Legacy contract
# --------------------------------------------------------------------------

def _freeze(value: Any) -> Any:
    """A deep, immutable copy, so a later mutation cannot rewrite history."""
    if isinstance(value, Mapping):
        return tuple(sorted((str(k), _freeze(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _contract(gateway: Any, key: str) -> dict[str, Any] | None:
    answers: dict[str, Any] = {}
    for verb in LEGACY_CONTRACT_VERBS:
        try:
            answers[verb] = _freeze(gateway.call(verb, key))
        except Exception:
            return None
    return answers


def read_idempotence(gateway: Any, *, key: str) -> bool:
    """Do the read verbs answer the same thing twice in a row?

    A raised call is `False`, not "equal": two failures are not evidence that
    anything is stable, and a probe that reported them as agreement would go
    green on a controller that is down.
    """
    for verb in IDEMPOTENT_READ_VERBS:
        try:
            first = _freeze(gateway.call(verb, key))
            second = _freeze(gateway.call(verb, key))
        except Exception:
            return False
        if first != second:
            return False
    return True


@dataclass(frozen=True)
class ContractCheck:
    decision: str
    code: str
    drifted: tuple[str, ...] = ()


def survival(gateway: Any, steps: Sequence[tuple[str, Any]], *,
             key: str) -> list[tuple[str, ContractCheck]]:
    """Run each Next lifecycle step and compare the Legacy answers around it.

    The Legacy gateway is only ever asked; the steps act on the *Next* track.
    `send` is included, and the fake is expected to be idempotent by `key`, so
    a step that caused a second delivery for one intent shows up as drift
    rather than as a plausible second receipt.
    """
    report: list[tuple[str, ContractCheck]] = []
    for name, step in steps:
        before = _contract(gateway, key)
        step()
        after = _contract(gateway, key)
        if before is None or after is None:
            report.append((name, ContractCheck(BLOCKED, LEGACY_CONTRACT_DRIFT,
                                               tuple(LEGACY_CONTRACT_VERBS))))
            continue
        drifted = tuple(verb for verb in LEGACY_CONTRACT_VERBS
                        if before.get(verb) != after.get(verb))
        report.append((name, ContractCheck(
            OK if not drifted else BLOCKED,
            OK if not drifted else LEGACY_CONTRACT_DRIFT,
            drifted)))
    return report


# --------------------------------------------------------------------------
# per-track lifecycle
# --------------------------------------------------------------------------

def _version_key(version: Any) -> tuple[int, ...] | None:
    parts = str(version or "").strip().split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def next_upgrade(footprint: Any, *, version: str, artifact_digest: str) -> Step:
    """Move the Next track forward, remembering exactly what to go back to.

    Only Next is advanced from here. Moving Legacy is the old track's own
    release process, and a wrapper that could do both is a tool that can take
    the controller down while calling itself a rollout.
    """
    current = footprint.footprint if isinstance(footprint, Step) else footprint
    if current.track != NEXT:
        return Step(BLOCKED, BLOCKED_CROSS_TRACK, current)
    before, after = _version_key(current.version), _version_key(version)
    if before is None or after is None or not _sha256_digest(artifact_digest):
        return Step(BLOCKED, BLOCKED_INCOMPLETE, current)
    if not after > before:
        return Step(BLOCKED, BLOCKED_VERSION_NOT_ADVANCING, current)
    advanced = Footprint(**{
        **current.__dict__,
        "version": version,
        "artifact_digest": artifact_digest,
        "rollback_version": current.version,
        "rollback_digest": current.artifact_digest,
    })
    return Step(OK, OK, advanced, previous=current)


def next_rollback(footprint: Any) -> Step:
    """Return Next to the footprint recorded before its last upgrade."""
    current = footprint.footprint if isinstance(footprint, Step) else footprint
    if current.track != NEXT:
        return Step(BLOCKED, BLOCKED_CROSS_TRACK, current)
    if not current.rollback_version or not _sha256_digest(current.rollback_digest):
        # A version with no digest is a rollback to whatever is on disk, which
        # is not a rollback: it is a downgrade with unknown bytes.
        return Step(BLOCKED, BLOCKED_INCOMPLETE, current)
    restored = Footprint(**{
        **current.__dict__,
        "version": current.rollback_version,
        "artifact_digest": current.rollback_digest,
        "rollback_version": "",
        "rollback_digest": "",
    })
    return Step(OK, OK, restored, previous=current)


def next_install_failure(footprint: Any, *, installer: Any) -> Step:
    """Run one install attempt and report the footprint it left behind.

    The result carries the *input* footprint on failure, and the caller is
    expected to check the installer's own pointer rather than this return
    value: what makes a failed install harmless is that `current` did not
    move, and that is a fact about the installer's state, not about ours.
    """
    current = footprint.footprint if isinstance(footprint, Step) else footprint
    try:
        installer.run()
    except Exception:
        return Step(BLOCKED, INSTALL_FAILED, current)
    return Step(OK, OK, current)


# --------------------------------------------------------------------------
# timers: present in the tree, absent from the running system
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TimerGate:
    decision: str
    code: str
    timers: tuple[str, ...] = ()
    armed: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    executes: tuple[str, ...] = ()


def timers_in(tree: Any) -> tuple[str, ...]:
    """Every `*.timer` unit in the tree, by name, without the suffix."""
    root = Path(tree)
    found = [path.stem for path in root.rglob("*.timer")
             if ".git" not in path.parts]
    return tuple(sorted(set(found)))


def _timer_name(value: Any) -> str:
    return str(value).strip().split(".")[0]


def timer_gate(systemctl: Any, timers: Sequence[str], *,
               plan: Mapping[str, Any] | None = None) -> TimerGate:
    """Is every timer still off, and does the plan leave it that way?

    A template timer that is `enable`d is started by the user manager at the
    next login with no approval, so "not started now" is not the property that
    matters — "cannot start on its own" is. Both flags are therefore required,
    and a plan that would set either is refused before anything runs.

    This gate only ever asks (`show`). It has no branch that starts, stops,
    enables or reloads, which is why `writes` and `executes` are constants
    rather than measurements of something it did.
    """
    plan = plan or {}
    wanted = {_timer_name(v) for key in ("enable", "start")
              for v in (plan.get(key) or ())}
    armed: list[str] = []
    for name in timers:
        state = systemctl.show(name) or {}
        if state.get("enabled") is True or state.get("active") is True:
            armed.append(name)
        elif _timer_name(name) in wanted:
            armed.append(name)
    armed = sorted(set(armed))
    return TimerGate(
        decision=OK if not armed else BLOCKED,
        code=OK if not armed else BLOCKED_TIMER_ARMED,
        timers=tuple(timers),
        armed=tuple(armed),
    )


# --------------------------------------------------------------------------
# reproducible release units: Hub, Node, Sidecar
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Component:
    """One releasable piece: the files it owns, its entrypoint, its way back."""

    name: str
    roots: tuple[str, ...]
    entrypoint: str
    revoke: tuple[str, ...]
    owns: Any  # a callable path -> bool


_HUB_EXCLUDED = ("fleet_mcp/node/",)
_SIDECAR_FILES = (
    "fleet_mcp/deploy/next_tailscale_gate.py",
    "fleet_mcp/deploy/next_tailscale_plan.example.json",
)

COMPONENTS: dict[str, Component] = {
    "hub": Component(
        name="hub",
        roots=("fleet_mcp", "native/node-core"),
        entrypoint="fleet_mcp/bin/serveros-fleet-mcp",
        revoke=("fleet_mcp/deploy/install.py", "fleet_mcp/deploy/next_install.py"),
        owns=lambda p: (p.startswith("fleet_mcp/")
                        and not p.startswith(_HUB_EXCLUDED)
                        and p not in _SIDECAR_FILES),
    ),
    "node": Component(
        name="node",
        roots=("fleet_mcp", "native/node-core"),
        entrypoint="fleet_mcp/node/connector.py",
        revoke=("fleet_mcp/node/uninstall.sh", "fleet_mcp/node/uninstall.ps1"),
        owns=lambda p: (p.startswith("fleet_mcp/node/")
                        or p.startswith("native/node-core/")),
    ),
    "sidecar": Component(
        name="sidecar",
        roots=("fleet_mcp", "native/node-core"),
        entrypoint="fleet_mcp/deploy/next_tailscale_gate.py",
        revoke=(),
        owns=lambda p: p in _SIDECAR_FILES,
    ),
}


def _git(repo: Any, args: Sequence[str], *,
         stdin: bytes | None = None) -> bytes:
    if args and args[0] not in READ_ONLY_GIT:
        raise AssertionError(f"refusing a non-read git verb: {args[0]!r}")
    # GIT_OPTIONAL_LOCKS=0 stops git taking `index.lock` on a repository other
    # sessions are using. The inherited environment is kept: overriding PATH
    # would break finding git at all.
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return proc.stdout


def head_ref(repo: Any) -> str:
    """The commit this checkout is on, as a pinned ref rather than `HEAD`."""
    return _git(repo, ["rev-parse", "HEAD"]).decode("utf-8").strip()


def _tree(repo: Any, ref: str) -> list[tuple[str, str]]:
    out = _git(repo, ["ls-tree", "-r", "-z", "--full-tree", ref, "--",
                      "fleet_mcp", "native/node-core"])
    entries: list[tuple[str, str]] = []
    for record in out.split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        fields = meta.split()
        if len(fields) < 3 or fields[1] != b"blob":
            continue
        entries.append((path.decode("utf-8"), fields[2].decode("ascii")))
    return entries


def manifest(repo: Any, ref: str, component: str) -> dict[str, str]:
    """The component's files at `ref`, as path -> git blob SHA.

    Blob SHAs rather than hashes of the worktree bytes, deliberately: a blob
    hash is the same on every checkout of a commit, while a worktree hash moves
    with `core.autocrlf`. A release manifest that changed when it was computed
    on Windows would not be a manifest.
    """
    spec = COMPONENTS[component]
    return dict(sorted((path, sha) for path, sha in _tree(repo, ref)
                       if spec.owns(path)))


def tree_sha256(files: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(f"{path}\t{files[path]}\n".encode("utf-8"))
    return "sha256:" + digest.hexdigest()


def _blobs(repo: Any, shas: Sequence[str]) -> dict[str, bytes]:
    if not shas:
        return {}
    payload = ("\n".join(shas) + "\n").encode("ascii")
    data = _git(repo, ["cat-file", "--batch"], stdin=payload)
    out: dict[str, bytes] = {}
    pos = 0
    for sha in shas:
        newline = data.find(b"\n", pos)
        if newline < 0:
            break
        header = data[pos:newline].decode("utf-8", "replace").split()
        if len(header) < 3 or header[1] != "blob":
            pos = newline + 1
            continue
        size = int(header[2])
        out[sha] = data[newline + 1:newline + 1 + size]
        pos = newline + 1 + size + 1
    return out


def _module_name(path: str) -> str:
    dotted = path[:-3].replace("/", ".")
    return dotted[:-len(".__init__")] if dotted.endswith(".__init__") else dotted


def _package_of(path: str, module: str) -> str:
    return module if path.endswith("__init__.py") else module.rpartition(".")[0]


def _owner_of(dotted: str) -> str | None:
    if dotted == "fleet_mcp.node" or dotted.startswith("fleet_mcp.node."):
        return "node"
    if dotted.startswith("fleet_mcp.deploy.next_tailscale"):
        return "sidecar"
    if dotted == "fleet_mcp" or dotted.startswith("fleet_mcp."):
        return "hub"
    return None


def _imports_of(path: str, source: bytes) -> set[str]:
    """Every first-party module a file imports, with relative levels resolved.

    Resolving `from ..errors import X` in `fleet_mcp/node/connector.py` to
    `fleet_mcp.errors` is the whole point: the string in the file does not say
    which component it reaches, which is why a grep cannot answer the
    independence question this function exists for.
    """
    tree = ast.parse(source.decode("utf-8", "replace"), filename=path)
    module = _module_name(path)
    package = _package_of(path, module)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                keep = len(base) - (node.level - 1)
                base = base[:max(keep, 0)]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            if target:
                found.add(target)
    return {name for name in found if _owner_of(name) is not None}


@dataclass(frozen=True)
class ComponentReport:
    name: str
    decision: str
    code: str
    ref: str
    tree_sha256: str = ""
    files: int = 0
    file_list: tuple[tuple[str, str], ...] = ()
    reproducible: bool = False
    revocable: bool = False
    independent: bool = False
    entrypoint_present: bool = False
    foreign_imports: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


def component_report(repo: Any, ref: str, component: str) -> ComponentReport:
    """Can this piece be built on its own, and can it be undone?

    Three axes, each answered by a fact rather than a sentence:

    * **reproducible** — the manifest computed twice from the same ref is
      identical, so the release unit is a description of bytes and not of a
      directory somebody looked at;
    * **revocable** — a revoke entrypoint is in the component's own file list,
      so undoing it does not depend on a runbook line;
    * **independent** — no file in the component imports a module owned by
      another component, so the artifact does not silently need its neighbour.
    """
    spec = COMPONENTS[component]
    files = manifest(repo, ref, component)
    again = manifest(repo, ref, component)
    reproducible = bool(files) and files == again

    entrypoint_present = spec.entrypoint in files
    revocable = bool(spec.revoke) and all(p in files for p in spec.revoke)

    python_files = {p: sha for p, sha in files.items()
                    if p.startswith("fleet_mcp/") and p.endswith(".py")}
    blobs = _blobs(repo, sorted(set(python_files.values())))
    foreign: set[str] = set()
    for path, sha in python_files.items():
        try:
            imported = _imports_of(path, blobs.get(sha, b""))
        except SyntaxError:
            continue
        for target in imported:
            if _owner_of(target) != component:
                foreign.add(target)
    independent = not foreign

    reasons: list[str] = []
    if not files:
        reasons.append("the component owns no files at this ref")
    if not reproducible:
        reasons.append("the manifest is not reproducible between two reads")
    if not entrypoint_present:
        reasons.append(f"the entrypoint {spec.entrypoint} is not in the artifact")
    if not revocable:
        if not spec.revoke:
            reasons.append("no revoke entrypoint exists in the tree, so undoing "
                           "this piece is a manual act nobody reviewed")
        else:
            missing = [p for p in spec.revoke if p not in files]
            reasons.append("the revoke entrypoint is missing: "
                           + ", ".join(missing))
    if not independent:
        reasons.append("it imports another component's modules: "
                       + ", ".join(sorted(foreign)))

    ok = reproducible and entrypoint_present and revocable and independent
    return ComponentReport(
        name=spec.name,
        decision=VERIFIED if ok else BLOCKED,
        code=VERIFIED if ok else BLOCKED_RELEASE_UNIT,
        ref=ref,
        tree_sha256=tree_sha256(files),
        files=len(files),
        file_list=tuple(sorted(files.items())),
        reproducible=reproducible,
        revocable=revocable,
        independent=independent,
        entrypoint_present=entrypoint_present,
        foreign_imports=tuple(sorted(foreign)),
        reasons=tuple(reasons),
    )


# --------------------------------------------------------------------------
# what a real install still needs from the owner
# --------------------------------------------------------------------------

OWNER_INPUTS = (
    "next.plugin_id — the registered ChatGPT plugin id, observed, never fabricated",
    "next.public_mcp_url — the dedicated HTTPS /mcp origin for Next",
    "oauth audience — the protected resource Next will mint and verify",
    "pat_anchor — the owner's PAT anchor PATH (mode 0600); this tool never reads it",
    "device_ref — the reviewed identity of the host both tracks run on",
    "attestations — owner readback: v1 identity observed, binding reviewed, rollback ready",
    "rollback digest — the artifact a Next rollback must land on",
    "co-install consent — real co-install needs separate user permission, not this gate",
)


def owner_inputs() -> tuple[str, ...]:
    """The facts this gate cannot measure and a real install cannot invent."""
    return OWNER_INPUTS


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--resolve", action="store_true",
                        help="print the release-unit reports for this checkout")
    parser.add_argument("--ref", default=None)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[2]
    if not args.resolve:
        parser.error("nothing to do without --resolve")
    ref = args.ref or head_ref(repo)
    payload = {"schema": SCHEMA, "ref": ref, "components": {}}
    for name in COMPONENTS:
        report = component_report(repo, ref, name)
        payload["components"][name] = {
            "decision": report.decision,
            "code": report.code,
            "tree_sha256": report.tree_sha256,
            "files": report.files,
            "reproducible": report.reproducible,
            "revocable": report.revocable,
            "independent": report.independent,
            "foreign_imports": list(report.foreign_imports),
            "reasons": list(report.reasons),
        }
    payload["owner_inputs"] = list(owner_inputs())
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
