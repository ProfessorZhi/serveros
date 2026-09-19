#!/usr/bin/env python3
"""From "something is running" to a registration a person authorized.

The hole this module fills
--------------------------
Two questions about managed resources were already answered, one on each side of
this one:

    registry.validate()      is this registration well-formed?
    runtime_hygiene          is what is registered still what is running?

Neither answers the question in the middle — *should this be registered at all,
and by whose authority* — and that is the question that produced the incident.
A resource appears, and there were exactly two ways to get it registered: edit
`resources.yaml` by hand, or leave the thing unmanaged. The first is
unauditable, the second is how a resource runs at 407% CPU for a day and eleven
hours with nothing on the machine able to say whose work it was.

So the middle is a pipeline, and every stage of it is a place to stop:

    evidence  →  candidate  →  a person's scope  →  plan  →  registration
                    ↑                                    ↓
              NOT a registration                   validated, not written

The rules this module is built around
-------------------------------------
**Noticing is not registering, and the gap is not a formality.** A raw process
observation produces a *discovery note*. It does not produce a candidate, and
for a kind whose contract demands `decisive_owner` it cannot, because "a process
called `mcp-server` is listening on 8080" is a statement about a name and a port
and says nothing about what the thing is or whose work it is. Only a *trusted
launch record* — the client that started it saying so — carries the owner, and
without an owner there is nothing to ask a person about.

**A candidate is a question, not a resource.** It becomes a resource only after
a person answers, and the answer is one of five things, not a boolean. The
authorization is remembered, so the same candidate is not re-asked on every
launch: a prompt that fires every time is a prompt people learn to dismiss, and
a dismissed prompt authorizes nothing.

**Registration grants nothing but recognition.** In particular it does not grant
auto-restart. `recovery` is derived from the scope a person chose, never from
the fact that something was registered — a resource that is registered and a
resource that may be restarted unattended are different states, and conflating
them is how a correct registration becomes an incorrect action.

**An owner that cannot be confirmed is DEFER.** It goes to `defer_refs` with a
reason, which is a recorded decision rather than an omission. §16 outranks every
other classification here.

**Losing sight of a resource is not the same as the resource ending.** A source
that stops reporting keeps its last approved `desired` and gets a freshness
reading; it does not get retired, and a recoverer does not get to overrule a
`desired` that a person set.

What this module cannot do
--------------------------
It has no `write`, no `apply`, no `register` method, and it imports no mutating
verb. `plan()` returns a plan a person or an approved writer applies; the
validation it performs is `registry.validate`, so an illegal plan is refused at
plan time rather than at write time — but refused *as a plan*, still unwritten.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


registry = _load("serveros_runtime_registry", "registry.py")
graph = _load("serveros_runtime_graph", "graph.py")

RegistryInvalid = registry.RegistryInvalid

SCHEMA = "serveros/registration-candidate.v1"

# The five answers a person can give. The design named them in this vocabulary
# and they are not a boolean on purpose: `session` and `node` differ in how long
# the thing is allowed to live and therefore in whether anything may restart it,
# and `external` and `ignore` both mean "not ours" while differing in whether
# the dependency is worth recording. Two of the five write nothing to
# `resources`, and a yes/no would have collapsed them into "no".
SCOPE_SESSION = "session"      # 仅本次: this run only; the client that started it ends it
SCOPE_PROJECT = "project"      # 项目登记: belongs to a project's work
SCOPE_NODE = "node"            # 节点持久登记: part of this node's steady state
SCOPE_EXTERNAL = "external"    # 外部依赖: someone else's; record the dependency, manage nothing
SCOPE_IGNORE = "ignore"        # 忽略: not worth anything; remember the answer so we stop asking
SCOPES = (SCOPE_SESSION, SCOPE_PROJECT, SCOPE_NODE, SCOPE_EXTERNAL, SCOPE_IGNORE)

# Where each answer lands. Registration is the EXCEPTION, not the default: two
# of the five scopes write no resource at all.
#
# Each scope names an ordered set rather than one class, because a kind may
# constrain which lifetimes are honest for it and the contract is the authority
# on that. `mcp-server` accepts only `persistent` and `session-scoped`, so a
# session-scoped MCP is `session-scoped` and not `disposable`; picking the
# class here instead of reading it would produce a plan the registry refuses
# for a reason that has nothing to do with the person's answer.
_SCOPE_LIFECYCLE = {
    SCOPE_SESSION: ("session-scoped", "disposable"),
    SCOPE_PROJECT: ("task-scoped",),
    SCOPE_NODE: ("persistent",),
}

# The scope a person chose decides what the recovery model may be, and the
# answer is never "restart" as a consequence of having been registered.
#
#   `disposable`  — the owning client ends it. Restarting it from outside
#                   produces a second copy the owner does not know about.
#   `task-scoped` — the work item's business. A restart is a decision about
#                   someone's work, not about a process.
#   `persistent`  — a person may still authorize `restart`, but as an explicit
#                   separate act (`authorize(..., recovery=...)`), not as the
#                   automatic companion of registering.
_SCOPE_RECOVERY_DEFAULT = {
    SCOPE_SESSION: "none",
    SCOPE_PROJECT: "redeclare",
    SCOPE_NODE: "redeclare",
}

# A recovery model that says "this cannot be repaired automatically" has to say
# WHY, and the reason is not the same for two scopes that arrive at the same
# word. `redeclare` for a session-scoped thing and `redeclare` for a node
# service are the same instruction about two different situations, and a reader
# six months later can only tell them apart from the sentence.
_SCOPE_RECOVERY_BECAUSE = {
    SCOPE_SESSION: (
        "the client that started this owns its lifetime and ends it. A restart "
        "from outside would produce a second copy the owner does not know "
        "about, which is worse than the absence it was trying to fix."),
    SCOPE_PROJECT: (
        "the declaration is what ages here, not the process. If the thing is "
        "not running, the question is whether the project still needs it — and "
        "that is a question about someone's work, not about a process to "
        "bounce."),
    SCOPE_NODE: (
        "registration recognises the resource; it does not authorise repairing "
        "it. Automatic repair is a separate decision with its own scope, and "
        "this plan did not make it. Pass `recovery='restart'` to `authorize()` "
        "to ask for it explicitly."),
}

# Evidence sources, weakest first. The ordering is the whole point of the first
# rule: a source may only be used to assert a thing it can actually see.
SOURCE_OBSERVATION = "observation"        # a scan saw a process / a port
SOURCE_LAUNCH_RECORD = "launch-record"    # the client that started it said so
SOURCE_DECLARATION = "declaration"        # an approved document asked for it
_SOURCE_RANK = {SOURCE_OBSERVATION: 0, SOURCE_LAUNCH_RECORD: 1, SOURCE_DECLARATION: 2}

# What a candidate's owner can be. `UNKNOWN` is not a failure to try harder; it
# is the answer when no decisive evidence names an owner, and it is terminal.
OWNER_CONFIRMED = "CONFIRMED"
OWNER_UNKNOWN = "UNKNOWN"

# Reasons that keep a candidate out of `resources` and put it in `defer_refs`.
DEFER_OWNER_UNKNOWN = "OWNER_UNKNOWN"
DEFER_EXTERNAL = "EXTERNAL_DEPENDENCY"
DEFER_IGNORED = "IGNORED_BY_USER"


def _digest(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class RegistrationError(Exception):
    """A candidate or a decision that cannot be acted on as stated."""


def candidate_identity(*, device_ref: str, kind: str, owner_ref: str | None,
                       evidence_key: str) -> str:
    """A stable id for a candidate, derived from what it IS rather than from
    when it was seen.

    A counter would make the same resource a new candidate on every scan, which
    is the same defect as re-asking a question the person already answered. The
    407%-CPU process was the same candidate for thirty-five hours; it must have
    one identity for all of them, or "have we already asked about this" has no
    answer.
    """
    if not evidence_key:
        raise RegistrationError(
            "a candidate needs an evidence key; without one its identity would "
            "change with every observation and dedup could never fire")
    return _digest({"device": device_ref, "kind": kind,
                    "owner": owner_ref or OWNER_UNKNOWN, "evidence": evidence_key})


class Evidence:
    """What is known about the thing, and how it came to be known.

    `source` is kept because it bounds what may be claimed. A scan is not a
    launch record, and a candidate that forgets which one it had can assert an
    owner it never had evidence for.
    """

    __slots__ = ("source", "key", "observed_at", "detail")

    def __init__(self, *, source: str, key: str, observed_at: str | None = None,
                 detail: dict | None = None):
        if source not in _SOURCE_RANK:
            raise RegistrationError(
                f"{source!r} is not an evidence source this module knows; it "
                f"knows {sorted(_SOURCE_RANK)}. An unrecognised source would be "
                "ranked nowhere and could then be treated as decisive.")
        if not key:
            raise RegistrationError("evidence needs a key, which is what makes "
                                    "two sightings the same candidate")
        self.source = source
        self.key = key
        self.observed_at = observed_at
        self.detail = dict(detail or {})

    @property
    def rank(self) -> int:
        return _SOURCE_RANK[self.source]

    def as_dict(self) -> dict:
        return {"source": self.source, "key": self.key,
                "observed_at": self.observed_at, "detail": self.detail}

    @classmethod
    def from_dict(cls, payload: dict) -> "Evidence":
        """The inverse of `as_dict`, in the same class so the two cannot drift.

        A document one direction produces and the other cannot read is a schema
        with a missing half: `discover` writes the candidate and `plan` reads it
        back, and if the two halves lived in different files the day they
        disagreed would be the day a person's answer was applied to a different
        thing than the one they were shown.
        """
        return cls(source=payload["source"], key=payload["key"],
                   observed_at=payload.get("observed_at"),
                   detail=payload.get("detail"))


class Candidate:
    """A question about a thing that is running, awaiting a person's answer.

    Constructed through `observe()` or `from_launch_record()`. It holds a
    *proposed* registration and does not hold a registration.
    """

    __slots__ = ("id", "device_ref", "kind", "owner", "owner_state",
                 "evidence", "proposed", "discovery_note", "why")

    def __init__(self, *, device_ref: str, kind: str, owner: dict | None,
                 owner_state: str, evidence: Evidence, proposed: dict,
                 discovery_note: str | None = None, why: str = ""):
        self.device_ref = device_ref
        self.kind = kind
        self.owner = dict(owner) if owner else None
        self.owner_state = owner_state
        self.evidence = evidence
        self.proposed = dict(proposed)
        self.discovery_note = discovery_note
        self.why = why
        self.id = candidate_identity(
            device_ref=device_ref, kind=kind, owner_ref=self.owner_ref,
            evidence_key=evidence.key)

    @property
    def owner_ref(self) -> str | None:
        """The typed ref, `<type>:<id>`. Kept separate from `owner.type`: the
        registry validates the two fields independently and a single string
        carrying both (`serveros:session:abc123`) is not a typed ref at all."""
        return (self.owner or {}).get("ref")

    @property
    def deferrable(self) -> bool:
        """Whether this candidate must go to `defer_refs` instead of
        `resources`. Owner unknown is the §16 case and outranks everything."""
        return self.owner_state == OWNER_UNKNOWN

    def as_dict(self) -> dict:
        return {"schema": SCHEMA, "id": self.id, "device_ref": self.device_ref,
                "kind": self.kind, "owner": self.owner,
                "owner_state": self.owner_state, "evidence": self.evidence.as_dict(),
                "proposed": self.proposed,
                "discovery_note": self.discovery_note, "why": self.why,
                "deferrable": self.deferrable}

    @classmethod
    def from_dict(cls, payload: dict) -> "Candidate":
        """Read back what `as_dict` wrote, and re-derive the identity.

        The id is rebuilt rather than trusted. It is a hash of what the thing
        is, so a document that carried an id the evidence does not produce is a
        document about a different thing — and `plan()` matches a decision to a
        candidate by exactly that id, which means trusting the stored one is how
        a person's answer lands on something they were never shown.
        """
        candidate = cls(device_ref=payload["device_ref"], kind=payload["kind"],
                        owner=payload.get("owner"),
                        owner_state=payload["owner_state"],
                        evidence=Evidence.from_dict(payload["evidence"]),
                        proposed=payload.get("proposed") or {},
                        discovery_note=payload.get("discovery_note"),
                        why=payload.get("why") or "")
        declared = payload.get("id")
        if declared and declared != candidate.id:
            raise RegistrationError(
                f"candidate {declared} does not re-derive from its own evidence "
                f"(it derives as {candidate.id}). An id that is not what the "
                "evidence says is a question about a different thing.")
        return candidate


def observe(*, device_ref: str, kind: str, evidence_key: str, observed_at: str | None = None,
            owner: dict | None = None, detail: dict | None = None,
            asserted_purpose: str | None = None) -> Candidate:
    """A scan saw something. This is a DISCOVERY, and usually it stops here.

    Two refusals, and both are the reason this function is not called
    `candidate()`:

      * A kind that the contract says needs `decisive_owner` cannot be proposed
        from an observation at all, because the observation has no owner in it.
        The 407%-CPU process is that case: the machine could see it perfectly
        and could not say whose it was, and the honest output there is a
        discovery note naming the gap, not a candidate with the owner guessed.
      * A process name or an open port is not evidence of purpose. `mcp-server`
        in a command line, or a listener on 8080, does not establish that the
        thing is an MCP server, still less which toolset it serves.
    """
    evidence = Evidence(source=SOURCE_OBSERVATION, key=evidence_key,
                        observed_at=observed_at, detail=detail)

    if asserted_purpose is not None:
        raise RegistrationError(
            f"an observation may not assert a purpose ({asserted_purpose!r}). A "
            "scan can see that a process exists and which port is open; it "
            "cannot see what the thing is for. Claiming a toolset or an "
            "intended use from a process name is how a scratch helper gets "
            "registered as infrastructure.")

    required = _decisive_owner_required(kind)
    if required:
        return Candidate(
            device_ref=device_ref, kind=kind, owner=None,
            owner_state=OWNER_UNKNOWN, evidence=evidence,
            proposed={}, discovery_note=_DISCOVERY_NOTE.format(kind=kind),
            why=(f"kind {kind!r} requires a decisive owner and an observation "
                 f"carries none, so this is a discovery and not a candidate. "
                 f"The contract says: {required}"))

    if not owner:
        return Candidate(
            device_ref=device_ref, kind=kind, owner=None,
            owner_state=OWNER_UNKNOWN, evidence=evidence, proposed={},
            discovery_note=_DISCOVERY_NOTE.format(kind=kind),
            why=("no owner was named and none can be established from an "
                 "observation; an owner that cannot be confirmed is DEFER"))

    return _propose(device_ref=device_ref, kind=kind, owner=owner,
                    owner_state=OWNER_CONFIRMED, evidence=evidence, detail=detail)


def from_launch_record(*, device_ref: str, kind: str, owner: dict | None,
                       launched_by: str, evidence_key: str,
                       observed_at: str | None = None, detail: dict | None = None) -> Candidate:
    """The client that started the thing said so. This is the one source that
    can carry an owner.

    `launched_by` is the subject that started it — a session, a project, an
    item. It is what makes the candidate answerable: "may this live only as long
    as your session" is a question with a subject, and a question without a
    subject is a notice.
    """
    if not launched_by:
        raise RegistrationError(
            "a launch record names the thing that launched it; without that "
            "there is no subject to ask about and this is just an observation "
            "with a longer name")
    evidence = Evidence(source=SOURCE_LAUNCH_RECORD, key=evidence_key,
                        observed_at=observed_at, detail=detail)
    state = OWNER_CONFIRMED if owner else OWNER_UNKNOWN
    return _propose(device_ref=device_ref, kind=kind, owner=owner,
                    owner_state=state, evidence=evidence, detail=detail,
                    launched_by=launched_by)


def _propose(*, device_ref: str, kind: str, owner: dict | None, owner_state: str,
             evidence: Evidence, detail: dict | None, launched_by: str | None = None) -> Candidate:
    proposed = {
        "kind": kind,
        "owner": dict(owner) if owner else None,
        "scope": "user",
        "desired": {"state": "running"},
        "protection": {"level": "normal"},
    }
    if launched_by:
        proposed["launched_by"] = launched_by
    note = None
    if detail and detail.get("session_scoped"):
        # A stdio MCP is started and ended by its client. It is not a shared
        # HTTP service, and registering it as one produces a resource whose
        # liveness the node is wrong about at both ends of the day.
        #
        # The class is read from the contract rather than named here.
        # `disposable` is the obvious word and it is the wrong one for
        # `mcp-server`, which admits only `persistent` and `session-scoped`:
        # a proposal naming a class the registry refuses describes a resource
        # that cannot exist, and the person answering the question would be
        # told to choose between two words neither of which is on offer.
        lifecycle, why_not = _lifecycle_for(kind, SCOPE_SESSION)
        if lifecycle:
            proposed["lifecycle"] = {"class": lifecycle}
            note = (f"the launch record says this is session-scoped, so the only "
                    f"honest lifecycle is {lifecycle!r}: the client that started "
                    f"it ends it, and nothing on the node may keep it alive")
        else:
            note = (f"the launch record says this is session-scoped, and kind "
                    f"{kind!r} admits no such lifetime. {why_not}")
    return Candidate(device_ref=device_ref, kind=kind, owner=owner,
                     owner_state=owner_state, evidence=evidence,
                     proposed=proposed, discovery_note=note,
                     why="a launch record names an owner, so this is a candidate")


def _decisive_owner_required(kind: str) -> str | None:
    """The contract's reason, when a kind demands a decisive owner."""
    entry = (registry.contract().get("kind_requirements") or {}).get(kind) or {}
    for requirement in entry.get("requires") or ():
        if requirement.get("decisive_owner"):
            return (requirement.get("because") or "").strip()
    return None


_DISCOVERY_NOTE = (
    "discovery only: {kind} was seen running, and nothing in this observation "
    "says whose work it is or what it is for. It is reported, it is not "
    "registered, and no automatic path reaches it.")


# --------------------------------------------------------------------------
# The person's answer
# --------------------------------------------------------------------------

class Decision:
    """One answer about one candidate, with the subject that gave it."""

    __slots__ = ("candidate_id", "scope", "actor", "at", "note", "recovery")

    def __init__(self, *, candidate_id: str, scope: str, actor: str, at: str,
                 note: str = "", recovery: str | None = None):
        if scope not in SCOPES:
            raise RegistrationError(
                f"{scope!r} is not one of the scopes a person may choose "
                f"({', '.join(SCOPES)}). A scope this module does not know "
                "would fall through every rule below.")
        if scope in (SCOPE_SESSION, SCOPE_PROJECT, SCOPE_NODE) and not actor:
            raise RegistrationError(
                f"scope {scope!r} writes a resource, and a resource without an "
                "authorizing subject is unattributable")
        self.candidate_id = candidate_id
        self.scope = scope
        self.actor = actor
        self.at = at
        self.note = note
        self.recovery = recovery

    @property
    def registers(self) -> bool:
        return self.scope in _SCOPE_LIFECYCLE

    def as_dict(self) -> dict:
        return {"schema": SCHEMA, "candidate_id": self.candidate_id,
                "scope": self.scope, "actor": self.actor, "at": self.at,
                "note": self.note, "recovery": self.recovery,
                "registers": self.registers}


def authorize(candidate: Candidate, scope: str, *, actor: str, at: str,
              note: str = "", recovery: str | None = None) -> Decision:
    """Record a person's answer. Refuses the two answers that would be a lie.

    `recovery` is a separate parameter for a reason: registering a node-scoped
    resource does not authorize restarting it, and an API where the two arrive
    together is an API where nobody notices they were the same act. Passing it
    explicitly is the only way it happens, and it is never the default.
    """
    if candidate.deferrable and scope in _SCOPE_LIFECYCLE:
        raise RegistrationError(
            f"candidate {candidate.id} has no confirmable owner "
            f"({candidate.owner_state}), so it cannot be authorized into "
            f"`resources` under scope {scope!r}. §16 makes an unknown owner DEFER, "
            f"and DEFER registrations belong in defer_refs. {candidate.why}")

    # The session-owns-a-durable-resource rule is NOT restated here. It lives in
    # the contract's `ownership` block and is executed by `registry.validate`,
    # which `plan()` runs on every record this decision produces — so a plan for
    # a session-owned persistent MCP is refused there, by the one implementation
    # both moments share. A second copy in this file would be a rule that agrees
    # today and drifts later, which is the defect `test_composition_authority`
    # exists to catch.

    if recovery is not None and recovery != "restart" and scope != SCOPE_NODE:
        raise RegistrationError(
            "an explicit recovery model other than 'restart' belongs to the "
            "node scope; the narrower scopes derive theirs")

    return Decision(candidate_id=candidate.id, scope=scope, actor=actor, at=at,
                    note=note, recovery=recovery)


def already_asked(candidate: Candidate, decisions) -> Decision | None:
    """The answer given earlier for this same candidate, if there was one.

    Identity is evidence-derived, so the same resource seen again is the same
    candidate and does not re-ask. This is the whole reason `candidate_identity`
    is a hash of what the thing is rather than a sequence number: a question
    that reappears on every launch gets dismissed on every launch.
    """
    for decision in decisions:
        if decision.candidate_id == candidate.id:
            return decision
    return None


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------

class RegistrationPlan:
    """What would be written, and what it would mean. Nothing here writes.

    `records` are fully validated resources — `registry.validate` has already
    run on every one, so a plan that would fail at write time fails here
    instead, where the caller still has a chance to change the answer rather
    than discover the refusal in a half-applied file.
    """

    __slots__ = ("candidate", "decision", "records", "defer", "refusal", "because")

    def __init__(self, *, candidate: Candidate, decision: Decision,
                 records: list, defer: list | None = None,
                 refusal: str | None = None, because: str = ""):
        self.candidate = candidate
        self.decision = decision
        self.records = records
        self.defer = list(defer or [])
        self.refusal = refusal
        self.because = because

    @property
    def admissible(self) -> bool:
        return self.refusal is None

    @property
    def writes_a_resource(self) -> bool:
        return bool(self.records)

    def as_dict(self) -> dict:
        return {"schema": SCHEMA, "candidate_id": self.candidate.id,
                "scope": self.decision.scope, "admissible": self.admissible,
                "refusal": self.refusal, "because": self.because,
                "records": [r.record if hasattr(r, "record") else r for r in self.records],
                "defer": self.defer}


def plan(candidate: Candidate, decision: Decision, *, resource_id: str | None = None,
         probes: list | None = None) -> RegistrationPlan:
    """Turn an answer into a plan, or into a named refusal.

    Every path out of here is explicit. A refusal is a result: it says which
    rule stopped it and what would have to change.

    `resource_id` is an INPUT and does not default. A candidate knows the
    evidence and does not know what to call the thing, and a resource id is a
    name a person reads in an alert at three in the morning — which is why the
    registry refuses ids that encode a host, a port or a run. Deriving one from
    a hash would satisfy the validator and produce a registry nobody can read,
    so a scope that writes a resource refuses without a name rather than
    inventing an opaque one.
    """
    if decision.candidate_id != candidate.id:
        raise RegistrationError(
            f"decision is about candidate {decision.candidate_id} and the plan "
            f"is about {candidate.id}")

    if decision.scope == SCOPE_IGNORE:
        return RegistrationPlan(
            candidate=candidate, decision=decision,
            records=[], defer=[{"ref": _defer_ref(candidate),
                                "because": DEFER_IGNORED,
                                "note": decision.note or "ignored by the user"}],
            because=("the person said this is not worth anything; the answer is "
                     "remembered so the same candidate is not asked again"))

    if decision.scope == SCOPE_EXTERNAL:
        return RegistrationPlan(
            candidate=candidate, decision=decision,
            records=[],
            defer=[{"ref": _defer_ref(candidate),
                    "because": DEFER_EXTERNAL,
                    "note": decision.note or "someone else's dependency"}],
            because=("this is another actor's resource. The dependency is worth "
                     "recording and the resource is not ours to manage — "
                     "registering it would claim a liveness this node cannot see"))

    if candidate.deferrable:
        return RegistrationPlan(
            candidate=candidate, decision=decision, records=[],
            defer=[{"ref": _defer_ref(candidate),
                    "because": DEFER_OWNER_UNKNOWN, "note": candidate.why}],
            refusal=DEFER_OWNER_UNKNOWN,
            because=("no decisive evidence names an owner, so no scope that "
                     "writes a resource is available"))

    if not resource_id:
        return RegistrationPlan(
            candidate=candidate, decision=decision, records=[],
            refusal="RESOURCE_ID_REQUIRED",
            because=("scope " + repr(decision.scope) + " writes a resource, and a "
                     "resource needs a name a person will read. The candidate "
                     "holds the evidence and not the name, and deriving one from "
                     "the evidence digest would produce a legal id that nobody "
                     "can recognise in an alert."))

    lifecycle, why_not = _lifecycle_for(candidate.kind, decision.scope)
    if lifecycle is None:
        return RegistrationPlan(
            candidate=candidate, decision=decision, records=[],
            refusal="LIFECYCLE_NOT_AVAILABLE_FOR_KIND", because=why_not)

    record = dict(candidate.proposed)
    record["id"] = resource_id
    record["lifecycle"] = {"class": lifecycle}
    if probes:
        record["probes"] = list(probes)
    recovery = decision.recovery or _SCOPE_RECOVERY_DEFAULT[decision.scope]
    record["recovery"] = {"model": recovery,
                          "because": _SCOPE_RECOVERY_BECAUSE[decision.scope]}
    # Registration admits a resource; it does not turn on automatic repair.
    # The reconcile mode an admitted resource gets is `observe` — which is the
    # registry's own default (registry.py) and is written here explicitly so
    # that a reader of the plan can see it was chosen rather than inherited.
    record.setdefault("reconcile", {"mode": "observe"})

    try:
        validated = registry.validate(record)
    except RegistryInvalid as refusal:
        return RegistrationPlan(
            candidate=candidate, decision=decision, records=[],
            refusal="REGISTRY_REFUSED", because=str(refusal))

    if recovery == "restart" and validated.protection == "protected":
        return RegistrationPlan(
            candidate=candidate, decision=decision, records=[],
            refusal="PROTECTED_MAY_NOT_RESTART",
            because=("a protected resource may be registered and may not be "
                     "restarted automatically; the two were asked for together"))

    return RegistrationPlan(
        candidate=candidate, decision=decision, records=[validated],
        because=(f"scope {decision.scope!r} → lifecycle {lifecycle!r}, "
                 f"recovery {recovery!r}, reconcile observe"))


def _kind_lifecycles(kind: str) -> tuple[str, ...] | None:
    """The lifetimes this kind's contract admits, or None when it declares none.

    None means "unconstrained by the kind", not "anything goes" — the ownership
    rule is checked separately and still applies.
    """
    entry = (registry.contract().get("kind_requirements") or {}).get(kind) or {}
    for requirement in entry.get("requires") or ():
        if requirement.get("field") == "lifecycle.class":
            return tuple(requirement.get("in") or ())
    return None


def _lifecycle_for(kind: str, scope: str) -> tuple[str | None, str]:
    """The one lifetime this scope and this kind both accept.

    Both sides may constrain it and neither may be overridden: the scope says
    how long the person wants the thing to live, the kind says which lifetimes
    are honest for it, and the answer is the intersection. An empty
    intersection is a refusal that names both sets, because "no" without both
    halves is a caller who cannot tell which side to change.
    """
    preference = _SCOPE_LIFECYCLE[scope]
    allowed = _kind_lifecycles(kind)
    for lifecycle in preference:
        if allowed is None or lifecycle in allowed:
            return lifecycle, ""
    return None, (
        f"scope {scope!r} offers {list(preference)} and kind {kind!r} admits "
        f"{list(allowed)}; they do not intersect, so there is no lifetime that "
        f"is both what the person asked for and what the kind can be.")


def _defer_ref(candidate: Candidate) -> str:
    """A ref for `defer_refs`, which is NOT a resource id and must not look
    like one. A deferred thing is precisely the thing that has no registration,
    so giving it an id shaped like a registration's would put it back in the
    namespace this decision kept it out of."""
    return f"deferred:{candidate.kind}:{candidate.id[:12]}"


# --------------------------------------------------------------------------
# After the plan: re-resolve
# --------------------------------------------------------------------------

# The sources a re-resolution can read freshness from. Declared here because
# `graph.freshness_of` returns UNKNOWN for a source it has no window for, and
# an undeclared source is the same as no freshness reading at all.
RESOLVE_SOURCE = "registry"


def resolve(resources, observations: dict, *, now: str,
            source: str = RESOLVE_SOURCE) -> dict:
    """Join what is registered against what is observed, after a registration.

    Four readings, and the last two are the ones that go wrong:

      ABSENT     registered, and the observation says it is not there
      OBSERVED   registered, and the observation saw it
      UNREPORTED registered, and no observation covers it at all
      STALE      registered, observed, and the observation is older than its
                 source's window

    **`UNREPORTED` is not `ABSENT`.** A source that stopped reporting has not
    told us the resource ended; it has told us nothing. The last approved
    `desired` stands, the reading is `UNKNOWN`, and nothing is retired. A
    recoverer reading this output has no path from `UNREPORTED` to an action,
    which is the point.
    """
    out: list[dict] = []
    for resource in resources:
        seen = observations.get(resource.id)
        if seen is None:
            out.append(_reading(resource, "UNREPORTED", now=now, source=source,
                                why=("no observation covers this resource, so "
                                     "whether it is running is unknown. The "
                                     "approved desired state stands.")))
            continue
        freshness = graph.freshness_of(seen.get("observed_at"), now=now, source=source)
        if freshness["state"] == graph.UNKNOWN:
            out.append(_reading(resource, "UNREPORTED", now=now, source=source,
                                freshness=freshness,
                                why=("the observation carries no time this "
                                     "module can parse, so its age is unknown "
                                     "rather than assumed fresh")))
        elif freshness["state"] != "FRESH":
            out.append(_reading(resource, "STALE", now=now, source=source,
                                freshness=freshness,
                                why="the observation is older than its source window"))
        elif seen.get("running") is False:
            out.append(_reading(resource, "ABSENT", now=now, source=source,
                                freshness=freshness,
                                why="observed, and not running"))
        else:
            out.append(_reading(resource, "OBSERVED", now=now, source=source,
                                freshness=freshness, why="observed and running"))
    return {"schema": SCHEMA, "at": now, "readings": out,
            "retired": [], "note": (
                "this module never retires. Losing sight of a resource is not "
                "evidence that it ended, and a desired state a person set is "
                "not a recoverer's to overrule.")}


def _reading(resource, state: str, *, now: str, source: str,
             freshness: dict | None = None, why: str = "") -> dict:
    return {
        "resource_ref": resource.id,
        "kind": resource.kind,
        "state": state,
        "desired": resource.desired,
        "freshness": freshness or graph.freshness_of(None, now=now, source=source),
        "actionable": False,
        "why": why,
    }
