#!/usr/bin/env python3
"""Who may be acted on. The gate between a nameable owner and a mutation.

Why this file exists
--------------------
`network-contract.yaml` declares an `ownership` input to its compose step and
writes down what it means:

    an EXTERNAL or UNKNOWN owner on the subject is a refusal, and a PROTECTED
    subject is REQUIRE_APPROVAL

The parameter existed. `compose()` read it. **Nothing produced it.**
`network_mutation.run()` calls `preflight(plan_record, policy=policy)` with the
argument absent, and the only caller that ever filled it in was a test. That is
the pathology this repository keeps recording — a declaration running ahead of
the code that would satisfy it, in a position where nothing fails when it is
missing. `ownership-contract.yaml` is the rule. This module is the producer.

What it is not
--------------
It is not a second attribution system. It does not read labels, consult the
registry, or decide what "managed" means. §11
(`containers.management_class`), §13 (`hygiene`), `protection.resolve` and the
registry all answer those questions already, and a second implementation is how
one question acquires two answers that then disagree. Every fact this module
uses is read from one of them, and the disagreement between them — a container
that is MANAGED with no nameable owner and no protection declaration — is
preserved rather than smoothed over.

It is also not a permission. The field naming its strongest outcome is
`eligible`, deliberately: ALLOW means ownership did not refuse, and every
policy the action carries still gets to refuse afterwards. Only `eligible:
false` ends the conversation, because a later policy that can be reached is a
later policy that can overrule this one.

Read-only. It imports no mutating verb and has no execute path. `hygiene.py`'s
sentence applies unchanged: the caller that executes is a different component,
deliberately, so that "what should happen" and "do it" stay reviewable
separately.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
CONTRACT = _HERE / "ownership-contract.yaml"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# The names are the ones `cli.py` and `daemon.py` already load these modules
# under. Sharing the registration is the point: two objects for one module
# means two `CLASSES` tuples and two `ACTIONABLE_CLASSES` frozensets, which
# compare equal today and stop doing so the first time one of them is edited.
hygiene_mod = _load("serveros_runtime_hygiene", "hygiene.py")
registry_mod = _load("serveros_runtime_registry", "registry.py")
containers = _load("serveros_runtime_containers", "containers.py")
protection_mod = _load("serveros_runtime_protection", "protection.py")
probes_mod = _load("serveros_runtime_probes", "probes.py")
paths_mod = _load("serveros_runtime_paths", "paths.py")
inventory_mod = _load("serveros_runtime_inventory", "inventory.py")


class OwnershipError(registry_mod.RegistryError):
    """An ownership question could not be put."""


class Rejected(ValueError):
    """The subject or action cannot be evaluated at all, as opposed to a
    subject that was evaluated and refused — which is an answer and has a
    code. `network_policy` draws the same line for the same reason."""


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------

def contract() -> dict:
    try:
        import yaml

        data = yaml.safe_load(CONTRACT.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise OwnershipError(f"{CONTRACT} could not be read: {exc}") from exc
    if not isinstance(data, dict):
        raise OwnershipError(f"{CONTRACT} must contain a mapping")
    return data


def decisions() -> tuple:
    return tuple((contract().get("decisions") or {}).keys())


def code_precedence() -> tuple:
    """The order refusals are reported in, read from the contract.

    Read rather than restated. This list is the order of the caller's next
    move, and a second copy in Python would agree with it until the day one of
    them was reordered and the other was not.
    """
    return tuple(contract().get("code_precedence") or ())


def code_rule(name: str) -> dict:
    return (contract().get("codes") or {}).get(name) or {}


def decisive_evidence() -> tuple:
    """What may decide an owner. Copied into the contract from
    `attribution-contract.yaml` rather than referenced, because a gate that
    reads its rule from a file the scanned code can also write is a gate the
    scanned code can widen."""
    return tuple(contract().get("decisive_evidence") or ())


def locator_evidence() -> tuple:
    return tuple((contract().get("locators") or {}).keys())


def resource_kinds() -> dict:
    return contract().get("resource_kinds") or {}


def kind_decisive_source(kind: str) -> str:
    """PRESENT or ABSENT, per kind. ABSENT is not "unimplemented" — it is a
    kind for which no decisive source exists in this product, so every subject
    of that kind is UNKNOWN and every mutation of one is DENY."""
    return ((resource_kinds().get(kind) or {}).get("decisive_source")
            or "ABSENT")


# ---------------------------------------------------------------------------
# §C2.2 — what may decide an owner, and what may only corroborate
# ---------------------------------------------------------------------------

def classify_evidence(items) -> dict:
    """Split offered evidence into what may decide an owner and what may not.

    The rule the contract states, implemented once: an owner conclusion whose
    evidence is entirely locators is UNKNOWN, and UNKNOWN is DENY.
    Corroboration may raise confidence in an owner a decisive source already
    established. It may not establish one.

    A source that is neither decisive nor a declared locator is NOT decisive
    either. It lands in `unrecognised` and counts toward the weak side, because
    the alternative — treating an unknown string as proof because it was not on
    a list of non-proofs — is how a name becomes a title.
    """
    decisive, locator, unrecognised = [], [], []
    for item in items or []:
        entry = item or {}
        record = {"source": entry.get("source"), "value": entry.get("value")}
        if record["source"] in decisive_evidence():
            decisive.append(record)
        elif record["source"] in locator_evidence():
            locator.append(record)
        else:
            unrecognised.append(record)

    if decisive:
        strength = "decisive"
        because = (
            f"{len(decisive)} decisive source(s): "
            f"{', '.join(sorted({r['source'] for r in decisive}))}"
        )
    elif locator or unrecognised:
        strength = "weak"
        because = (
            "every source offered is a locator or is unrecognised "
            f"({', '.join(sorted({str(r['source']) for r in locator + unrecognised}))}). "
            "A locator says where a thing is and never whose it is"
        )
    else:
        strength = "none"
        because = "no evidence was offered at all"

    return {
        "decisive": decisive,
        "locator": locator,
        "unrecognised": unrecognised,
        "strength": strength,
        "because": because,
    }


# ---------------------------------------------------------------------------
# The decision core. Pure: nothing here reads the host.
# ---------------------------------------------------------------------------

_OWNER_CLASSES = ("MANAGED", "EXTERNAL", "UNKNOWN")


def _code(*, decision: str, code, reason: str, codes: list) -> dict:
    return {"decision": decision, "code": code, "codes": codes, "reason": reason}


def decide(*, subject, action, context, owner, protection,
           classification=None, evidence=None, registry_present=True) -> dict:
    """The verdict, from facts that were already established.

    Pure on purpose: every caller that needs a host reaches it in `resolve`,
    and this function stays runnable from a literal — which is what lets the
    C2 mutation tests drive it to red without a daemon, a Docker socket, or a
    single fixture container.

    `protection` is the tri-state `protection.resolve` returns, and it stays
    three-valued all the way through: `None` and `False` reach the same
    DECISION and are NOT the same record, because an undeclared protection
    reported as unprotected is a bypass performed by a default value.
    """
    if decisions() and "ALLOW" not in decisions():  # pragma: no cover - guard
        raise OwnershipError("the contract declares no ALLOW decision")

    precedence = list(code_precedence())

    def verdict(entry: dict, **extra) -> dict:
        ordered = [c for c in precedence if c in entry["codes"]] + [
            c for c in entry["codes"] if c not in precedence
        ]
        out = {
            "decision": entry["decision"],
            "eligible": entry["decision"] != "DENY",
            # `verdict.required` says `code` is "the first code", so it is read
            # off the ordered list rather than taken from the branch that
            # returned. The two differ in exactly one case and the contract
            # argues for this one in its own words: a subject whose evidence is
            # all locators is reported as `WEAK_EVIDENCE_ONLY` and not as
            # `OWNER_UNKNOWN`, because the second "would report a name-only
            # subject as though no evidence had been offered at all, which is a
            # different and less useful fact".
            "code": ordered[0] if ordered else None,
            "codes": ordered,
            "reason": entry["reason"],
            "subject": subject,
            "owner": owner,
            "protection": {
                "protected": protection,
                "declared": protection is not None,
            },
            "action": action,
            "registry_present": bool(registry_present),
        }
        out.update(extra)
        return out

    # -- the subject has to be a subject before anything else is asked ------
    kind = (subject or {}).get("kind")
    if not kind or kind not in resource_kinds():
        return verdict(_code(
            decision="DENY", code="SUBJECT_MALFORMED", codes=["SUBJECT_MALFORMED"],
            reason=(
                f"the subject names kind {kind!r}, which is not one of "
                f"{sorted(resource_kinds())}. A subject that cannot be "
                "classified cannot be refused for a nameable reason either"
            ),
        ))

    # -- and somebody has to be asking --------------------------------------
    #
    # `network-contract.yaml` documents `candidate.owner_ref` as "who is
    # asking, resolved from the trusted context; never a name", and
    # `approval.py` refuses on the same rule. A mutation with no context has
    # no requester, so there is nobody to attribute the act to and nobody for
    # an approval to belong to.
    ctx = context or {}
    if not (ctx.get("context_ref") and ctx.get("actor_ref")):
        return verdict(_code(
            decision="DENY", code="NO_TRUSTED_CONTEXT",
            codes=["NO_TRUSTED_CONTEXT"],
            reason=(
                "no trusted context was presented, so the requester cannot be "
                "named. `owner_ref` is resolved from the trusted context and "
                "never from an argument"
            ),
        ))

    # -- can this kind of thing have an owner at all -------------------------
    source = kind_decisive_source(kind)
    if source == "ABSENT":
        return verdict(_code(
            decision="DENY", code="NO_DECISIVE_SOURCE",
            codes=["NO_DECISIVE_SOURCE"],
            reason=(
                f"no decisive owner source exists for a subject of kind {kind!r} "
                "on this node, so every subject of this kind is UNKNOWN. Not "
                "unread — unanswerable with what exists"
            ),
        ), kind_decisive_source=source)

    # -- evidence, if any was offered, before the conclusion it supports -----
    strength = None
    if evidence:
        classified = classify_evidence(evidence)
        strength = classified["strength"]
        if strength != "decisive":
            owner = {
                "class": "UNKNOWN",
                "confidence": "unknown",
                "because": classified["because"],
                "decided_by": None,
            }
        else:
            # Corroboration may raise confidence in an owner a decisive source
            # already established. It is reported, and it does not change the
            # class.
            owner = {**owner, "corroborated_by": classified["decisive"]}

    owner = owner or {}
    owner_class = owner.get("class") or "UNKNOWN"

    weak = (["WEAK_EVIDENCE_ONLY"] if strength == "weak" else [])

    if owner_class not in _OWNER_CLASSES:
        return verdict(_code(
            decision="DENY", code="SUBJECT_MALFORMED",
            codes=["SUBJECT_MALFORMED"],
            reason=(
                f"the owner record claims class {owner_class!r}, which is not "
                f"one of {list(_OWNER_CLASSES)}"
            ),
        ))

    if owner_class == "UNKNOWN":
        return verdict(_code(
            decision="DENY", code="OWNER_UNKNOWN", codes=weak + ["OWNER_UNKNOWN"],
            reason=(
                "no decisive evidence names an owner for this subject. The "
                "subject may be ours, and that is not the same as it being "
                "ours" + (f". {owner.get('because')}" if owner.get("because") else "")
            ),
        ), evidence_strength=strength)

    if owner_class == "EXTERNAL":
        return verdict(_code(
            decision="DENY", code="OWNER_EXTERNAL", codes=weak + ["OWNER_EXTERNAL"],
            reason=(
                "the subject's decisive evidence places it under another actor. "
                f"{owner.get('because') or ''}".strip()
            ),
        ), evidence_strength=strength)

    # -- MANAGED. Ownership has not refused; protection and the tier still can.
    action_kind = (action or {}).get("kind")
    if action_kind not in ("destructive", "additive"):
        raise Rejected(
            f"action kind {action_kind!r} is neither 'destructive' nor "
            "'additive'. A gate cannot weigh an action it cannot weigh"
        )
    if action_kind == "destructive":
        tier = (action or {}).get("tier")
        if tier not in hygiene_mod.RISK_TIERS:
            raise Rejected(
                f"destructive action names tier {tier!r}; the tiers are "
                f"{list(hygiene_mod.RISK_TIERS)}. An unnamed tier is how a "
                "removal slips past a policy written about removals"
            )

    if protection is True and action_kind == "destructive":
        return verdict(_code(
            decision="DENY", code="PROTECTED_SUBJECT", codes=["PROTECTED_SUBJECT"],
            reason=(
                "protection is declared for this subject and the action removes "
                "or stops. `hygiene.TIER_POLICY` and `admission`'s "
                "DENY_PROTECTED: protection is not a hint, and no automatic "
                "path reaches it on any trigger. Nor does a requested one"
            ),
        ), evidence_strength=strength)

    if protection is True and action_kind == "additive":
        return verdict(_code(
            decision="REQUIRE_APPROVAL", code="PROTECTED_SUBJECT",
            codes=["PROTECTED_SUBJECT"],
            reason=(
                "protection is declared for this subject. Creating something on "
                "it changes the subject and destroys nothing, which is why this "
                "is an approval and not a refusal — `network-contract.yaml`'s "
                "compose block names it"
            ),
        ), evidence_strength=strength)

    return verdict(_code(
        decision="ALLOW", code=None, codes=weak,
        reason=(
            "ownership admitted the subject: MANAGED, self-owned on decisive "
            "evidence, and not protected"
            + (", with protection undeclared rather than denied"
               if protection is None else "")
            + ". This is not a permission — the action's own policy still "
              "decides"
        ),
    ), evidence_strength=strength, classification=classification)


# ---------------------------------------------------------------------------
# Resolving a subject into the facts `decide` takes
# ---------------------------------------------------------------------------

def _roots() -> dict:
    """The declared roots, spelled as `view._declared_roots` spells them.

    An unreadable root contract yields no roots rather than an exception: a
    locator that cannot be resolved is a locator that resolves to nothing, and
    §11 already treats "nothing matched" as UNKNOWN. Raising here would turn a
    missing declaration into a crash instead of a refusal.
    """
    try:
        paths = paths_mod.Paths()
        return {name: str(paths.root(name)) for name in paths.names}
    except Exception:  # noqa: BLE001
        return {}


def build_registry_index(path=None) -> dict:
    """The registry index, from the canonical location when none is named.

    `containers.load_registry(None)` means "this node has no registry" and
    answers `registry_present: false` with an empty index. That is the right
    answer to the question it is asked and the wrong answer to this one: the
    default location is not the same thing as absent, and `registry_present`
    is exactly the field a reader uses to tell "nothing is registered" from
    "nobody looked". Resolving the path first is what keeps the difference.

    On this host the difference was total: `load_registry()` returned an empty
    index for every container while the registry sat at
    `registry.default_path()`, so every subject was UNKNOWN and every mutation
    DENY — a gate that refuses everything and reports the reason as an
    absence rather than as a look that did not happen.
    """
    if path is None:
        target = Path(registry_mod.default_path())
        path = str(target) if target.exists() else None
    return containers.load_registry(path)


def resolve_container(*, subject, host=None, registry=None, roots=None,
                      hygiene=None, inspected=None) -> dict:
    """§11 + §8 + §13 for one container, composed rather than re-derived.

    `inspected` lets a caller that already ran `docker inspect` hand the record
    in, which is what keeps the CLI from inspecting the host twice per check.
    """
    registry = registry if registry is not None else build_registry_index()
    host = host if host is not None else getattr(probes_mod, "LiveHost", lambda: None)()
    if host is None and inspected is None:
        raise containers.DockerUnavailable("no host to inspect containers with")

    records = [inspected] if inspected is not None else containers.inspect_all(host)
    name = subject.get("name") or subject.get("container")
    container_id = subject.get("container_id") or subject.get("id")

    record = None
    for candidate in records:
        cid = candidate.get("Id") or candidate.get("ID")
        cname = (candidate.get("Name") or "").lstrip("/")
        if name and cname == str(name).lstrip("/"):
            record = candidate
            break
        if container_id and cid and (cid == container_id or cid.startswith(container_id)):
            record = candidate
            break
    if record is None:
        return {
            "found": False,
            "why": (
                f"no container matching name={name!r} id={container_id!r} was "
                "found in the docker inventory. A subject that is not there "
                "cannot be attributed, and absence is not ownership"
            ),
            "owner": {"class": "UNKNOWN", "confidence": "unknown",
                      "because": "not found on this host", "decided_by": None},
            "protection": None,
            "classification": None,
            "host": host,
        }

    evidence_record = containers.evidence(record)
    attributed = containers.attribute(
        evidence_record, registry=registry,
        roots=roots if roots is not None else _roots(),
    )

    # §13's class, from the classifier that owns it. `classify_container` takes
    # docker-ps's shape, which the inspect record is not; the two fields it
    # reads are copied across explicitly rather than by reshaping the record,
    # so that a field this module invents cannot be mistaken for one docker
    # sent.
    classification = None
    if hygiene is not None:
        try:
            ps_row = {
                "Names": (record.get("Name") or "").lstrip("/"),
                "Labels": ((record.get("Config") or {}).get("Labels") or {}),
                "State": (record.get("State") or {}).get("Status"),
                "Image": (record.get("Config") or {}).get("Image"),
                "ID": record.get("Id"),
            }
            classification = hygiene.classify_container(ps_row).get("classification")
        except Exception as exc:  # noqa: BLE001 - a classifier that refused is
            classification = None  # reported as no classification, not as a class

    management = attributed.get("management") or {}
    # §8's field is `protected`, and it is three-valued: True, False, or None
    # for "neither the container nor its registration declared anything".
    # Reading a name that does not exist would have produced None for a
    # registered-protected container — which is not False, so nothing refuses,
    # and the protection silently stops existing at this layer.
    protected = ((attributed.get("runtime") or {}).get("protected"))

    # NOTE the absent `evidence` key, which is a decision and not an omission.
    #
    # §11's ladder already stops at the first DECISIVE source and leaves
    # everything else in a locator field; that is the same rule C2.2 states,
    # implemented where the evidence is read. Offering §11's conclusion back to
    # this module's `classify_evidence` would re-decide it from a second
    # vocabulary — §11's provenance names (`container-name`,
    # `compose-project-name-normalised`, the `from` of whatever locator fired)
    # are not this contract's locator keys — and the mismatch fails in the
    # expensive direction: a genuinely MANAGED container whose provenance is
    # spelled differently would be forced to UNKNOWN and refused. A false DENY
    # on our own resources is not the safe side of that error; it is just the
    # other error, wearing safety's coat.
    #
    # So: §11 decides the class for containers, and this module takes the class
    # as given. `classify_evidence` applies to evidence a caller offers
    # directly, which is the path C2.2's rule is written for.
    return {
        "found": True,
        "ref": attributed.get("ref"),
        "management": management,
        "owner": {
            "class": management.get("class") or "UNKNOWN",
            "confidence": management.get("confidence"),
            "because": management.get("because"),
            "decided_by": management.get("decided_by"),
        },
        "attributed_owner": attributed.get("owner") or {},
        "attribution": attributed.get("attribution") or {},
        "disposition": attributed.get("disposition"),
        "disposition_because": attributed.get("disposition_because"),
        "protection": protected,
        "protection_sources": (
            protection_mod.resolve(
                identities=[i for i in (
                    {"kind": "container-name", "value": str(name)} if name else None,
                    {"kind": "container-id", "value": str(container_id)}
                    if container_id else None,
                ) if i],
                labels=((record.get("Config") or {}).get("Labels") or {}),
            ) if protected is None else {"protected": protected, "sources": [
                {"source": "attribution", "why": "§8's protected_state"}
            ]}
        ),
        "classification": classification,
        "record": record,
        "host": host,
    }


def resolve_owner_ref(*, owner_ref, registry, kind: str) -> dict:
    """A subject that does not exist yet, asked about by the ref that will own it.

    This is the resolver for creation. `network create` proposes a network that
    has no labels, no cgroup and no unit — every decisive source on this node
    reads something that is not there yet. What IS there is the requester: the
    contract's `candidate.required` makes `owner_ref` mandatory and defines it
    as "who is asking, resolved from the trusted context; never a name". So the
    question "can an owner be named" is asked of that ref, and the registry
    answers it by whether it has a registration under it.

    Note what is decisive here and what is not: the registration IS a decisive
    source (`registry-match` in `attribution-contract.yaml`). The string in
    `owner_ref` is not, and a ref the registry has never heard of does not
    become an owner because it was spelled plausibly.
    """
    registry = registry or {}
    if not owner_ref:
        return {
            "found": True,
            "owner": {"class": "UNKNOWN", "confidence": "unknown",
                      "because": "the candidate names no owner_ref at all",
                      "decided_by": None},
            "protection": None, "classification": None, "lifecycle": None,
        }

    entry = (registry.get("by_ref") or {}).get(owner_ref)
    protected_refs = registry.get("protected_refs") or {}
    deferred = registry.get("defer_refs") or {}

    if not entry:
        return {
            "found": True,
            "owner": {
                "class": "UNKNOWN", "confidence": "unknown",
                "because": (
                    f"the registry has no registration under {owner_ref!r}"
                    + (" and no registry was readable at all"
                       if not registry.get("registry_present") else "")
                    + ". A ref the registry has never heard of is a string, "
                      "and a string is not an owner"
                ),
                "decided_by": None,
            },
            "protection": (True if owner_ref in protected_refs
                           else False if registry.get("registry_present")
                           else None),
            "protection_sources": {"protected": owner_ref in protected_refs,
                                   "sources": []},
            "classification": None,
            "lifecycle": None,
        }

    owner_type = entry.get("owner_type")
    owner_class = "UNKNOWN" if (not owner_type or owner_type == "unknown") else "MANAGED"
    protected = (entry.get("protection") == "protected") or (owner_ref in protected_refs)

    return {
        "found": True,
        "owner": {
            "class": owner_class,
            "confidence": "high",
            "because": (
                f"{owner_ref!r} is registered"
                + (f" as {entry.get('resource_id')!r}" if entry.get("resource_id") else "")
                + (f", deferred: {deferred[owner_ref]}" if owner_ref in deferred else "")
            ),
            "decided_by": "registry-match",
        },
        "attributed_owner": {"ref": owner_ref, "type": owner_type},
        "protection": True if protected else (
            False if registry.get("registry_present") else None),
        "protection_sources": {"protected": protected, "sources": (
            [{"source": "registration", "why": f"{entry.get('resource_id')} is "
                                                "protected"}] if protected else [])},
        "classification": None,
        "lifecycle": entry.get("lifecycle"),
        "kind": kind,
    }


def unresolvable(kind: str) -> dict:
    return {
        "found": True,
        "owner": {"class": "UNKNOWN", "confidence": "unknown",
                  "because": ((resource_kinds().get(kind) or {}).get("through")
                              or "no decisive source exists for this kind"),
                  "decided_by": None},
        "protection": None,
        "classification": None,
        "evidence": [],
    }


def resolve(*, subject, host=None, registry=None, roots=None, hygiene=None,
            inspected=None) -> dict:
    """The facts about a subject, from whichever existing module owns them."""
    kind = (subject or {}).get("kind")
    if kind == "container":
        return resolve_container(subject=subject, host=host, registry=registry,
                                 roots=roots, hygiene=hygiene, inspected=inspected)
    if kind in resource_kinds():
        if kind_decisive_source(kind) == "ABSENT":
            return unresolvable(kind)
        # network, volume, process. A decisive source is declared to exist for
        # each, and the one they share is the registry — so a subject that
        # names an `owner_ref` is answered from it rather than left UNKNOWN.
        #
        # Asking for live evidence instead (docker labels for an existing
        # network, a cgroup for a process) is the right answer for a subject
        # that already exists and is not what this entry does yet. Reporting
        # UNKNOWN for a subject with no owner_ref is the fail-closed direction,
        # and it is reported as UNKNOWN rather than as EXTERNAL: not-yet-asked
        # and somebody-else's are different answers and only one of them is a
        # statement about the host.
        if subject.get("owner_ref"):
            return resolve_owner_ref(owner_ref=subject["owner_ref"],
                                     registry=registry, kind=kind)
        return {
            "found": True,
            "owner": {"class": "UNKNOWN", "confidence": "unknown",
                      "because": (
                          f"no evidence was gathered for this {kind} on this "
                          "call and the subject names no owner_ref, so there is "
                          "nothing the registry could be asked about. UNKNOWN "
                          "rather than EXTERNAL: not-yet-asked and "
                          "somebody-else's are different answers"
                      ),
                      "decided_by": None},
            "protection": None,
            "classification": None,
        }
    return {"found": False, "why": f"{kind!r} is not a resource kind",
            "owner": {"class": "UNKNOWN", "confidence": "unknown",
                      "because": "not a declared kind", "decided_by": None},
            "protection": None, "classification": None, "evidence": []}


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------

_STRICTNESS = {"DENY": 0, "REQUIRE_APPROVAL": 1, "ALLOW": 2}


def evaluate(*, subject, action, context=None, host=None, registry=None,
             roots=None, hygiene=None, inspected=None, resolved=None,
             tier_policy=True, approved=False, owner_evidence=None) -> dict:
    """Ownership's verdict on a subject, and the tier's verdict on the action.

    The two are reported separately and folded once, because they answer
    different questions and the fold has to be visible: `ownership` is this
    module's answer, `tier_policy` is `hygiene.admission`'s answer verbatim,
    and `decision` is the stricter of the two.

    The fold exists because C2.1 asks the gate to hand a destructive action to
    the action's own policy, and `hygiene.admission` IS that policy for every
    tier in `RISK_TIERS`. Reimplementing its table here would be the second
    answer to one question that this whole module is written against.
    """
    resolved = resolved if resolved is not None else resolve(
        subject=subject, host=host, registry=registry, roots=roots,
        hygiene=hygiene, inspected=inspected)

    ownership = decide(
        subject=subject, action=action, context=context,
        owner=resolved.get("owner"), protection=resolved.get("protection"),
        classification=resolved.get("classification"),
        evidence=resolved.get("evidence") or None,
    )
    ownership["resolved"] = {
        "found": resolved.get("found"),
        "why": resolved.get("why"),
        "management": resolved.get("management"),
        "disposition": resolved.get("disposition"),
        "disposition_because": resolved.get("disposition_because"),
        "classification": resolved.get("classification"),
        "protection_sources": resolved.get("protection_sources"),
    }

    outcome = dict(ownership)
    tier = None

    if (tier_policy and ownership["decision"] == "ALLOW"
            and (action or {}).get("kind") == "destructive"):
        tier = hygiene_mod.admission(
            action.get("tier"), resolved.get("classification"),
            lifecycle=resolved.get("lifecycle"),
            protection=("protected" if resolved.get("protection") is True else None),
            owner_evidence=owner_evidence, approved=approved,
        )
        outcome["tier_policy"] = tier
        if not tier["allowed"]:
            downgrade = (
                "REQUIRE_APPROVAL" if tier["decision"] == "NEEDS_APPROVAL" else "DENY"
            )
            if _STRICTNESS[downgrade] < _STRICTNESS[ownership["decision"]]:
                outcome["decision"] = downgrade
                outcome["eligible"] = downgrade != "DENY"
            outcome["reason"] = (
                f"{ownership['reason']}. The tier policy then refused it: "
                f"{tier['decision']} — {tier['because']}"
            )
    elif (action or {}).get("kind") == "destructive":
        outcome["tier_policy"] = None

    return outcome


# ---------------------------------------------------------------------------
# Disclosure
# ---------------------------------------------------------------------------

LEVELS = ("L0", "L1", "L2", "L3")


def disclose(verdict: dict, level: str = "L1") -> str:
    """The ladder. L0 is a count, L1 is the decision, L2 adds the evidence,
    L3 adds the resolved record verbatim.

    Same four levels as `disclosure-contract.yaml`; this is a rendering of one
    record and not a second coordinate system.
    """
    if level not in LEVELS:
        raise Rejected(f"unknown disclosure level {level!r}; known: {list(LEVELS)}")

    subject = verdict.get("subject") or {}
    head = f"{verdict['decision']}  {subject.get('kind')}:{subject.get('ref') or subject.get('name')}"
    if level == "L0":
        return f"{verdict['decision']}"

    lines = [head]
    if verdict.get("code"):
        lines.append(f"  code    {verdict['code']}")
    lines.append(f"  because {verdict['reason']}")
    if level == "L1":
        return "\n".join(lines)

    owner = verdict.get("owner") or {}
    protection = verdict.get("protection") or {}
    lines.append(f"  owner   {owner.get('class')} "
                 f"(confidence {owner.get('confidence')}, via {owner.get('decided_by')})")
    lines.append(
        "  protect "
        + ("yes" if protection.get("protected") is True else
           "no" if protection.get("protected") is False else
           "undeclared")
        + ("" if protection.get("declared") else
           " — nobody on this node declared anything, which is not the same "
           "as somebody saying no")
    )
    if verdict.get("evidence_strength"):
        lines.append(f"  evidence {verdict['evidence_strength']}")
    if verdict.get("codes"):
        lines.append(f"  codes   {', '.join(verdict['codes'])}")
    tier = verdict.get("tier_policy")
    if tier:
        lines.append(f"  tier    {tier['tier']} {tier['decision']} — {tier['because']}")
    if level == "L2":
        return "\n".join(lines)

    lines.append("  resolved")
    lines.append(json.dumps(verdict.get("resolved") or {}, indent=4, default=str))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _subject_from_args(args) -> dict:
    if args.kind == "container":
        return {"kind": "container", "name": args.name, "ref": args.name}
    return {"kind": args.kind, "name": args.name, "ref": args.name}


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="serverosctl ownership",
        description=(
            "Decide whether a subject may be acted on: ALLOW, REQUIRE_APPROVAL "
            "or DENY. Read-only — nothing here stops, removes or creates "
            "anything, and there is no execute flag to add"
        ),
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    check = sub.add_parser("check", help="evaluate one subject and say why")
    check.add_argument("--kind", required=True, choices=sorted(resource_kinds()),
                       help="the kind of subject being asked about")
    check.add_argument("--name", required=True, help="the subject's name")
    check.add_argument("--action", default="additive",
                       choices=("additive", "destructive"))
    check.add_argument("--tier", choices=hygiene_mod.RISK_TIERS,
                       help="required for a destructive action")
    check.add_argument("--context", help="a trusted context document "
                                         "({context_ref, actor_ref})")
    check.add_argument("--registry", help="path to the managed-runtime registry")
    check.add_argument("--level", default="L1", choices=LEVELS)
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=_cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


def _read_document(path, what: str) -> dict:
    if not path:
        return {}
    try:
        import yaml

        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(text) if not path.endswith(".json") else json.loads(text)
    except (OSError, ValueError) as exc:
        raise Rejected(f"{what} at {path} could not be read: {exc}") from exc
    if not isinstance(data, dict):
        raise Rejected(f"{what} at {path} must be a mapping")
    return data


EXIT_ALLOW = 0
EXIT_DENY = 10
EXIT_REQUIRE_APPROVAL = 11
EXIT_UNASKABLE = 2


def exit_code(verdict: dict) -> int:
    """The verdict as a process exit code. A policy, and a pure function.

    Three outcomes, three codes, because they have three repairs:

        0   ALLOW. The action's own policy has not been consulted yet — this
            says ownership admitted the subject, not that anything may run.
        11  REQUIRE_APPROVAL. Nothing refused it and nobody may take it on the
            requester's own authority.
        10  DENY.
        2   is not reachable from here: a question that could not be put never
            produced a verdict.

    11 rather than 0 because `eligible` is True for an approval — that is what
    `eligible` means, and it is why the field is not called `allowed`. Mapping
    `eligible` to the exit code would report "somebody must approve this" as
    success to every caller that only reads `$?`, and the callers that read
    `$?` are the ones that then do the next thing.

    Separated from `_cmd_check` so the mapping can be tested without a host:
    the verdict it maps is a record, and a test of the mapping should not need
    a running container to produce one.
    """
    if not verdict.get("eligible"):
        return EXIT_DENY
    if verdict.get("decision") == "REQUIRE_APPROVAL":
        return EXIT_REQUIRE_APPROVAL
    return EXIT_ALLOW


def _cmd_check(args) -> int:
    context = _read_document(args.context, "context") if args.context else None
    registry_index = build_registry_index(args.registry)

    # §13's class, from the classifier that owns it. A registry that cannot be
    # read is a valid state rather than an error, and the consequence is an
    # absent classification — which for a destructive action is already a
    # refusal, because `admission` will not call an unclassified subject
    # actionable.
    hygiene = None
    if args.kind == "container":
        try:
            hygiene = hygiene_mod.Hygiene(
                registry_mod.load_default(args.registry), inventory_mod.Inventory())
        except Exception:  # noqa: BLE001
            hygiene = None

    action = {"kind": args.action}
    if args.action == "destructive":
        action["tier"] = args.tier

    try:
        verdict = evaluate(subject=_subject_from_args(args), action=action,
                           context=context, registry=registry_index, hygiene=hygiene)
    except (Rejected, containers.DockerUnavailable) as exc:
        print(f"ownership: {exc}", file=sys.stderr)
        return EXIT_UNASKABLE

    if args.json:
        print(json.dumps(verdict, indent=2, default=str))
    else:
        print(disclose(verdict, args.level))

    return exit_code(verdict)


def main() -> int:
    try:
        return run()
    except (Rejected, OwnershipError) as exc:
        print(f"ownership: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
