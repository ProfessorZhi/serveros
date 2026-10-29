"""#478: what it takes for a second Fleet to stand *beside* the first one.

The owner's decision is that the old Fleet keeps serving ChatGPT with every
device, session, agent and run control it has today, and that Fleet Next is a
*parallel* release rather than a replacement. That sentence is easy to agree
with and easy to violate by accident: a shared port, a reused plugin backend
id, a state directory that happens to be the same, an id read across tracks.
This module is the offline arithmetic that says "no" to those, before anything
is installed.

It is a decision surface and nothing else. Every function takes values the
caller already holds and returns a decision with the reason attached:

*   `install_plan` compares two *placements* and reports every shared resource
    at once as `BLOCKED_LEGACY_COLLISION`. It never proposes a substitute
    placement, never renames one aside, never re-routes. The only two answers
    are `OK` and `BLOCKED`.
*   `upgrade` / `rollback` keep version and rollback state per track, with
    `rollback(upgrade(p, v2, d2)) == p` as the invariant.
*   `startup_preflight` refuses a Next start on a colliding plan or on Legacy
    identity drift, and reports an unreachable Next as `UNKNOWN` — never as a
    refusal (Next being down says nothing about Legacy) and never as success.
*   `shadow_call` lets the shadow phase read facts it was *granted* and
    refuses every write verb before the gateway is touched.
*   `index_facts` / `resolve` / `run_state` scope every run, delivery and
    session id to `(track, fleet_instance_id, boot_id)`, so a same-named id
    from another instance, another boot or the other track is a refusal
    rather than a hit.
*   `snapshot` / `compare_survival` / `run_lifecycle` are the harness for the
    one claim that strings cannot prove: that the Legacy controller still
    answers exactly as it did while Next was installed, failed, upgraded and
    rolled back.

Nothing here imports the Legacy code, opens a file, opens a socket, reads a
credential or talks to a service. It is deliberately stdlib-only and pure, so
that a green test run is a statement about the rules and not about this
machine.
"""
from __future__ import annotations

import posixpath
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

SCHEMA = "serveros/fleet-next-legacy-safety.v1"

#: The two tracks. They are peers; neither is a version of the other.
LEGACY = "legacy"
NEXT = "next"
TRACKS = (LEGACY, NEXT)

OK = "OK"
BLOCKED = "BLOCKED"

BLOCKED_LEGACY_COLLISION = "BLOCKED_LEGACY_COLLISION"
BLOCKED_PLACEMENT_INCOMPLETE = "BLOCKED_PLACEMENT_INCOMPLETE"
LEGACY_IDENTITY_DRIFT = "LEGACY_IDENTITY_DRIFT"
VERSION_NOT_ADVANCING = "VERSION_NOT_ADVANCING"
ROLLBACK_WITHOUT_DIGEST = "ROLLBACK_WITHOUT_DIGEST"
ROLLBACK_CROSSES_TRACKS = "ROLLBACK_CROSSES_TRACKS"

#: Spelled exactly as #480's parallel-plugin preflight spells it. One refusal
#: with two spellings is how a second vocabulary starts.
SHADOW_WRITE_FORBIDDEN = "NEXT_SHADOW_WRITE_FORBIDDEN"
SHADOW_NOT_GRANTED = "SHADOW_NOT_GRANTED"
SHADOW_VERB_UNKNOWN = "SHADOW_VERB_UNKNOWN"
SHADOW_FACT_UNKNOWN = "SHADOW_FACT_UNKNOWN"

HIT = "HIT"
NOT_FOUND = "NOT_FOUND"
FOREIGN_INSTANCE = "FOREIGN_INSTANCE"
STALE_BOOT = "STALE_BOOT"
CROSS_TRACK = "CROSS_TRACK"
ID_KIND_MISMATCH = "ID_KIND_MISMATCH"

SAME = "SAME"
DRIFTED = "DRIFTED"

#: Every kind of thing the two tracks must not share, in reporting order.
COLLISION_RESOURCES = (
    "plugin_id",
    "mcp_endpoint",
    "port",
    "socket",
    "install_root",
    "state_root",
    "lock_root",
    "unit",
)

#: The identity fields a preflight compares field by field.
IDENTITY_FIELDS = (
    "track",
    "fleet_instance_id",
    "route_id",
    "boot_id",
    "schema_version",
    "source_version",
)

#: Verbs the shadow phase may never perform, whatever it was granted.
WRITE_VERBS = frozenset({
    "create",
    "send",
    "stop",
    "restart",
    "wake",
    "manager_wake",
    "interrupt",
    "close",
    "update",
    "session_create",
    "session_send",
    "session_interrupt",
    "session_close",
    "session_reconfigure",
    "session_cancel_queued",
})

#: Verbs that only read a fact, and so may be granted to the shadow at all.
READ_ONLY_FACT_VERBS = frozenset({
    "get_session",
    "get_run",
    "sessions_list",
    "projects_list",
    "overview",
    "session_get",
    "run_get",
    "approvals_list",
    "ui_visible_context",
    "health",
})

_FACT_KIND = {
    "get_session": "session",
    "session_get": "session",
    "sessions_list": "session_list",
    "get_run": "run",
    "run_get": "run",
}
_FACT_ID_KEYS = ("sessionId", "runId", "deliveryId")


# --------------------------------------------------------------------------
# placement
# --------------------------------------------------------------------------

@dataclass
class Placement:
    """One track's footprint: what it answers on, where it lives, what it is.

    `install_root` / `state_root` / `lock_root` are posix paths because the
    tracks live on the same host; a relative path is refused rather than
    resolved, since resolving it against the reader's cwd is how two tracks
    end up in one directory.
    """

    track: str
    backend_plugin_id: str
    mcp_url: str
    listen_port: int
    socket_path: str | None
    install_root: str
    state_root: str
    lock_root: str
    unit: str
    version: str
    artifact_digest: str
    rollback_version: str = ""
    rollback_digest: str = ""


@dataclass(frozen=True)
class Collision:
    resource: str
    detail: str


@dataclass(frozen=True)
class Plan:
    decision: str
    code: str
    collisions: tuple[Collision, ...] = ()
    incomplete: tuple[str, ...] = ()
    legacy: Any = None
    next: Any = None


@dataclass(frozen=True)
class Lifecycle:
    """An upgrade or rollback answer: allowed or refused, and the placement.

    The placement's own fields are readable straight off the result because
    every caller's next question is about it; on a refusal `placement` is the
    input, unchanged, so a refused step can never be mistaken for an applied
    one.
    """

    decision: str
    code: str
    placement: Placement
    previous: Placement | None = None

    @property
    def track(self) -> str:
        return self.placement.track

    @property
    def version(self) -> str:
        return self.placement.version

    @property
    def rollback_version(self) -> str:
        return self.placement.rollback_version

    @property
    def rollback_digest(self) -> str:
        return self.placement.rollback_digest


def _placement_of(value: Any) -> Placement:
    """Accept a placement or a lifecycle answer that carries one."""
    if isinstance(value, Lifecycle):
        return value.placement
    return value


def _absolute(path: str) -> bool:
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


def _endpoint(url: str) -> tuple[str, str, int | None, str] | None:
    try:
        parts = urllib.parse.urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    host = (parts.hostname or "").lower()
    path = parts.path.rstrip("/") or "/"
    return scheme, host, port, path


def _placement_gaps(placement: Placement, wanted_track: str) -> list[str]:
    """The fields that make a placement unusable, in a stable order."""
    gaps: list[str] = []
    if placement.track != wanted_track or placement.track not in TRACKS:
        gaps.append("track")
    if not placement.backend_plugin_id.strip():
        gaps.append("backend_plugin_id")
    endpoint = _endpoint(placement.mcp_url or "")
    if not placement.mcp_url.strip() or endpoint is None or endpoint[0] not in (
            "http", "https") or not endpoint[1]:
        gaps.append("mcp_url")
    if not isinstance(placement.listen_port, int) or not (
            0 < placement.listen_port < 65536):
        gaps.append("listen_port")
    if placement.socket_path is not None and not _absolute(placement.socket_path):
        gaps.append("socket_path")
    for name in ("install_root", "state_root", "lock_root"):
        if not _absolute(getattr(placement, name)):
            gaps.append(name)
    if not placement.unit.strip():
        gaps.append("unit")
    if not placement.version.strip():
        gaps.append("version")
    if not placement.artifact_digest.startswith("sha256:") or len(
            placement.artifact_digest) <= len("sha256:"):
        gaps.append("artifact_digest")
    return gaps


def install_plan(*, legacy: Any, next: Any) -> Plan:
    """May `next` be installed beside `legacy`? Every collision, at once.

    `next` may be a placement or the answer to an `upgrade`. The returned plan
    echoes the two placements unchanged: there is no branch that produces a
    corrected one, because a silently adjusted placement is a takeover with
    extra steps.
    """
    legacy_p = _placement_of(legacy)
    next_p = _placement_of(next)

    gaps = _placement_gaps(legacy_p, LEGACY) + _placement_gaps(next_p, NEXT)
    if gaps:
        seen: list[str] = []
        for gap in gaps:
            if gap not in seen:
                seen.append(gap)
        return Plan(decision=BLOCKED, code=BLOCKED_PLACEMENT_INCOMPLETE,
                    incomplete=tuple(seen), legacy=legacy_p, next=next_p)

    collisions: list[Collision] = []

    if legacy_p.backend_plugin_id == next_p.backend_plugin_id:
        collisions.append(Collision(
            "plugin_id",
            f"both tracks answer as {legacy_p.backend_plugin_id}"))

    legacy_url = _endpoint(legacy_p.mcp_url)
    next_url = _endpoint(next_p.mcp_url)
    if legacy_url == next_url:
        collisions.append(Collision(
            "mcp_endpoint", f"both tracks serve MCP at {legacy_p.mcp_url}"))
    elif legacy_url[1] and legacy_url[1] == next_url[1] and legacy_url[2] == next_url[2]:
        collisions.append(Collision(
            "port",
            f"both tracks listen on {legacy_url[1]}:{legacy_url[2]} "
            f"({legacy_url[3]} and {next_url[3]})"))

    if legacy_p.listen_port == next_p.listen_port:
        collisions.append(Collision(
            "port", f"both tracks listen on port {legacy_p.listen_port}"))

    if legacy_p.socket_path is not None and \
            legacy_p.socket_path == next_p.socket_path:
        collisions.append(Collision(
            "socket", f"both tracks bind {legacy_p.socket_path}"))

    for name in ("install_root", "state_root", "lock_root"):
        left = _norm(getattr(legacy_p, name))
        right = _norm(getattr(next_p, name))
        if _within(left, right):
            collisions.append(Collision(
                name, f"{right} overlaps the {name} {left}"))

    if legacy_p.unit == next_p.unit:
        collisions.append(Collision(
            "unit", f"both tracks are supervised as {legacy_p.unit}"))

    order = {name: index for index, name in enumerate(COLLISION_RESOURCES)}
    collisions.sort(key=lambda c: order[c.resource])

    if collisions:
        return Plan(decision=BLOCKED, code=BLOCKED_LEGACY_COLLISION,
                    collisions=tuple(collisions), legacy=legacy_p, next=next_p)
    return Plan(decision=OK, code=OK, legacy=legacy_p, next=next_p)


# --------------------------------------------------------------------------
# version and rollback, per track
# --------------------------------------------------------------------------

def _version_key(version: str) -> tuple[int, ...] | None:
    parts = (version or "").strip().split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def upgrade(placement: Any, *, version: str, artifact_digest: str) -> Lifecycle:
    """Move one track forward, remembering exactly what to go back to."""
    current = _placement_of(placement)
    if current.track != NEXT:
        # Only the new track is advanced from here. Touching the old one is a
        # different operation, done by the old one's own release process.
        return Lifecycle(BLOCKED, ROLLBACK_CROSSES_TRACKS, current)

    before = _version_key(current.version)
    after = _version_key(version)
    if before is None or after is None or not (
            current.artifact_digest.startswith("sha256:") and
            artifact_digest.startswith("sha256:")):
        return Lifecycle(BLOCKED, BLOCKED_PLACEMENT_INCOMPLETE, current)
    if not after > before:
        return Lifecycle(BLOCKED, VERSION_NOT_ADVANCING, current)

    return Lifecycle(
        OK, OK,
        placement=Placement(**{
            **current.__dict__,
            "version": version,
            "artifact_digest": artifact_digest,
            "rollback_version": current.version,
            "rollback_digest": current.artifact_digest,
        }),
        previous=current)


def rollback(placement: Any) -> Lifecycle:
    """Return one track to the placement it recorded before the upgrade."""
    current = _placement_of(placement)
    if current.track != NEXT:
        return Lifecycle(BLOCKED, ROLLBACK_CROSSES_TRACKS, current)
    if not current.rollback_version:
        # Nothing was recorded, so there is nothing to go back to.
        return Lifecycle(BLOCKED, BLOCKED_PLACEMENT_INCOMPLETE, current)
    if not current.rollback_digest.startswith("sha256:"):
        # A version was named but the artifact cannot be pinned: rolling back
        # to whatever is on disk is not a rollback.
        return Lifecycle(BLOCKED, ROLLBACK_WITHOUT_DIGEST, current)

    restored = Placement(**{
        **current.__dict__,
        "version": current.rollback_version,
        "artifact_digest": current.rollback_digest,
        "rollback_version": "",
        "rollback_digest": "",
    })
    return Lifecycle(OK, OK, placement=restored, previous=current)


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Identity:
    track: str
    fleet_instance_id: str
    route_id: str
    boot_id: str
    schema_version: str
    source_version: str


@dataclass(frozen=True)
class Preflight:
    decision: str
    code: str
    legacy_usable: bool
    next_facts: str
    drift: tuple[str, ...] = ()
    plan: Plan | None = None


def _drift(recorded: Identity, observed: Identity | None) -> tuple[str, ...]:
    if observed is None:
        return IDENTITY_FIELDS
    return tuple(name for name in IDENTITY_FIELDS
                 if getattr(recorded, name) != getattr(observed, name))


def startup_preflight(*, recorded: Identity, observed: Identity | None,
                      plan: Plan, next_reachable: bool) -> Preflight:
    """May Next start now?

    A shared resource is checked first, because that is the case the owner
    asked to be caught *immediately* — before anything is started, and before
    a plan built on a possibly-stale baseline is reasoned about further.

    `legacy_usable` is not a health check of Legacy. It says whether Legacy's
    observed identity still matches the baseline the plan was built against,
    and therefore whether a start may proceed. Legacy itself is untouched
    either way; that is the point of the fakes in the test file.

    An unreachable Next is `next_facts == "UNKNOWN"`: a missing observation is
    not evidence of a refusal and not evidence of success.
    """
    if plan.decision != OK:
        return Preflight(decision=BLOCKED, code=plan.code, legacy_usable=False,
                         next_facts="UNKNOWN", drift=(), plan=plan)

    drift = _drift(recorded, observed)
    if drift:
        return Preflight(decision=BLOCKED, code=LEGACY_IDENTITY_DRIFT,
                         legacy_usable=False,
                         next_facts="OBSERVED" if next_reachable else "UNKNOWN",
                         drift=drift, plan=plan)

    return Preflight(decision=OK, code=OK, legacy_usable=True,
                     next_facts="OBSERVED" if next_reachable else "UNKNOWN",
                     plan=plan)


# --------------------------------------------------------------------------
# shadow: read-only, and only what was granted
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Fact:
    track: str
    instance_id: str
    route_id: str
    boot_id: str
    kind: str
    fact_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Ref:
    track: str
    instance_id: str
    boot_id: str
    kind: str
    fact_id: str


@dataclass(frozen=True)
class ShadowResult:
    allowed: bool
    code: str
    state: str
    fact: Fact | None = None
    gateway_called: bool = False
    track: str = ""
    instance_id: str = ""
    boot_id: str = ""


def _fact_id(payload: Mapping[str, Any]) -> str:
    for key in _FACT_ID_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def shadow_call(verb: str, *, granted: Iterable[str], gateway: Any) -> ShadowResult:
    """Perform one shadow-phase call, or refuse it without touching the gateway.

    The refusals come before the call, so `gateway_called is False` is a fact
    the fakes can check by their own call log rather than a claim.
    """
    identity = getattr(gateway, "identity", None)
    track = getattr(identity, "track", "")
    instance_id = getattr(identity, "fleet_instance_id", "")
    boot_id = getattr(identity, "boot_id", "")

    def refuse(code: str) -> ShadowResult:
        return ShadowResult(allowed=False, code=code, state="REFUSED",
                            gateway_called=False, track=track,
                            instance_id=instance_id, boot_id=boot_id)

    if verb in WRITE_VERBS:
        return refuse(SHADOW_WRITE_FORBIDDEN)
    if verb not in READ_ONLY_FACT_VERBS:
        return refuse(SHADOW_VERB_UNKNOWN)
    if verb not in set(granted):
        return refuse(SHADOW_NOT_GRANTED)

    try:
        payload = gateway.call(verb)
    except Exception:
        return ShadowResult(allowed=True, code=SHADOW_FACT_UNKNOWN,
                            state="UNKNOWN", fact=None, gateway_called=True,
                            track=track, instance_id=instance_id,
                            boot_id=boot_id)

    payload = dict(payload) if isinstance(payload, Mapping) else {}
    fact = Fact(track=track,
                instance_id=instance_id,
                route_id=getattr(identity, "route_id", ""),
                boot_id=boot_id,
                kind=_FACT_KIND.get(verb, verb),
                fact_id=_fact_id(payload),
                payload=payload)
    return ShadowResult(allowed=True, code=OK, state="OBSERVED", fact=fact,
                        gateway_called=True, track=track,
                        instance_id=instance_id, boot_id=boot_id)


# --------------------------------------------------------------------------
# identity of a run, delivery or session
# --------------------------------------------------------------------------

def _key(track: str, instance_id: str, boot_id: str, kind: str,
         fact_id: str) -> tuple[str, str, str, str, str]:
    return (track, instance_id, boot_id, kind, fact_id)


def index_facts(facts: Iterable[Fact]) -> dict[tuple[str, str, str, str, str], Fact]:
    """Index facts by the full scope of their id. Later facts win."""
    return {_key(f.track, f.instance_id, f.boot_id, f.kind, f.fact_id): f
            for f in facts}


@dataclass(frozen=True)
class Resolution:
    code: str
    fact: Fact | None = None


def resolve(reference: Ref, *, owner: Identity,
            index: Mapping[tuple[str, str, str, str, str], Fact]) -> Resolution:
    """Answer a reference against one owner's scope.

    Order matters: the track first, then the instance, then the boot. Each
    refuses before the id is looked up, so a same-named id that exists in a
    scope we do not own is `FOREIGN_INSTANCE` / `STALE_BOOT` / `CROSS_TRACK`
    rather than a hit.
    """
    if reference.track != owner.track:
        return Resolution(CROSS_TRACK)
    if reference.instance_id != owner.fleet_instance_id:
        return Resolution(FOREIGN_INSTANCE)
    if reference.boot_id != owner.boot_id:
        return Resolution(STALE_BOOT)

    scope = (reference.track, reference.instance_id, reference.boot_id)
    hit = index.get(_key(*scope, reference.kind, reference.fact_id))
    if hit is not None:
        return Resolution(HIT, hit)

    for key, fact in index.items():
        if key[:3] == scope and key[4] == reference.fact_id:
            return Resolution(ID_KIND_MISMATCH, fact)
    return Resolution(NOT_FOUND)


def run_state(facts: Iterable[Fact], reference: Ref) -> str:
    """The state the fact itself reports, read inside the reference's own scope.

    A `completed` on the other track is a different fact about a different
    object; `NOT_FOUND` here means "this track has not said anything about
    this id", which is not the same as "unknown" and not the same as "done".
    """
    resolution = resolve(reference, owner=Identity(
        track=reference.track,
        fleet_instance_id=reference.instance_id,
        route_id="",
        boot_id=reference.boot_id,
        schema_version="",
        source_version="",
    ), index=index_facts(facts))
    if resolution.code != HIT or resolution.fact is None:
        return NOT_FOUND
    status = resolution.fact.payload.get("status")
    return status if isinstance(status, str) and status else "UNKNOWN"


def same_object(left: Ref, right: Ref) -> bool:
    """Two references name one object only if every scope field agrees."""
    return _key(left.track, left.instance_id, left.boot_id, left.kind,
                left.fact_id) == _key(right.track, right.instance_id,
                                      right.boot_id, right.kind, right.fact_id)


# --------------------------------------------------------------------------
# survival: did the Legacy controller move?
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Survival:
    decision: str
    drifted: tuple[str, ...] = ()


def _freeze(value: Any) -> Any:
    """A deep, immutable copy, so a later mutation cannot rewrite history."""
    if isinstance(value, Mapping):
        return tuple(sorted((str(k), _freeze(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def snapshot(gateway: Any, verbs: Sequence[str]) -> tuple[tuple[str, str, Any], ...]:
    """Ask the gateway every verb and record the frozen answers."""
    seen: list[tuple[str, str, Any]] = []
    for verb in verbs:
        try:
            payload = gateway.call(verb)
        except Exception as exc:
            seen.append((verb, "RAISED", f"{type(exc).__name__}:{exc}"))
        else:
            seen.append((verb, "OK", _freeze(payload)))
    return tuple(seen)


def compare_survival(before: Sequence[Any], after: Sequence[Any]) -> Survival:
    """Compare two snapshots verb by verb."""
    old = {entry[0]: entry for entry in before}
    new = {entry[0]: entry for entry in after}
    drifted = tuple(verb for verb in sorted(set(old) | set(new))
                    if old.get(verb) != new.get(verb))
    return Survival(SAME if not drifted else DRIFTED, drifted)


def run_lifecycle(gateway: Any, verbs: Sequence[str],
                  steps: Sequence[tuple[str, Callable[[], Any]]],
                  ) -> list[tuple[str, Survival]]:
    """Run each Next lifecycle step and compare the Legacy answers around it.

    `steps` are named callables that do something to the *Next* track; the
    gateway passed in is the Legacy one, and it is only ever read.
    """
    report: list[tuple[str, Survival]] = []
    for name, step in steps:
        before = snapshot(gateway, verbs)
        step()
        after = snapshot(gateway, verbs)
        report.append((name, compare_survival(before, after)))
    return report
