#!/usr/bin/env python3
"""Decide what one MeetingBundle contributes to work, and what may be done to it.

    python3 meeting_relevance.py --request relevance.json
    python3 meeting_relevance.py --request cleanup.json --kind cleanup

This is the ServerOS **Meeting relevance / retention kernel**. It is the
counterpart of the Space Resolver: Space answers "where does this object
canonically belong"; this module answers "what does this meeting contribute to
the user's work, and what may be done to it afterwards".

What this module is
-------------------
A deterministic decision kernel over an explicit vocabulary. It models the
pipeline of SPACE-AND-RETENTION section 7:

    metadata/scope -> policy match -> bounded content when allowed
     -> RELEVANT | MIXED | IRRELEVANT | UNKNOWN
     -> INCLUDE_SEGMENTS | EXCLUDE_FROM_WORK | REVIEW

and, as a separate question with separate inputs, the four auto-cleanup levels
of section 8:

    CONTENT_ISOLATION | REBUILDABLE_ARTIFACT_CLEANUP
     | CLOUD_RECOVERABLE_DISPOSAL | IRREVERSIBLE_DELETE

Five axes are kept apart and this module is built so they cannot be collapsed:
relevance, ownership, retention, legal/organizational hold and deletability.
`classify()` reports deletability as `NOT_EVALUATED` and returns `cleanup: null`
for every input, so **relevance cannot reach a deletion**; `plan_cleanup()`
never reads a relevance verdict, and a request that supplies one is told it was
recorded and not consulted.

Three more properties are structural rather than promised:

  * A signal is not a judgement. Audio duration, the user-muted boolean,
    language, title, the participant list and the automatic summary are
    *recorded*, always as insufficient, and **no branch of this module reads
    one**. Setting all six to their most damning values changes nothing.
  * No rule is not a default. A stage with no input produces UNKNOWN, never
    RELEVANT and never IRRELEVANT; an unmatched bundle is reviewed, never
    excluded; a null or wildcard selector matches nothing at all.
  * IRREVERSIBLE_DELETE is unreachable from classification. It needs a
    per-operation authorization supplied *in the request*, and while the user's
    declared protected exception (`support:marek-open-mic`) is UNBOUND every
    level that removes material fails closed with a refusal rather than a plan.
    With the file that ships, only CONTENT_ISOLATION can plan at all.

What this module is NOT
-----------------------
It does not talk to Feishu, a database, a language model, or a provider. It
reads no clock: expiry is evaluated against an explicit `as_of` the caller
supplies, and an expiry that cannot be evaluated fails closed. It has no title
or name matcher, so "marek", Polish and a participant list cannot select an
exception even by accident. It does not copy transcript text into a record.
Applying a plan belongs to the surface adapters — the receipt store, the
recycle bin and the index rebuild are theirs.

Exit status: 0 for a decision, 2 for REJECTED or a malformed request. A decision
of REVIEW, REFUSAL or PROPOSAL_REQUIRED is still a successful determination, so
it exits 0.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SCHEMA = "serveros/meeting-relevance-decision.v1"
CLEANUP_SCHEMA = "serveros/meeting-cleanup-plan.v1"
VOCABULARY_SCHEMA = "serveros/meeting-vocabulary.v1"

DECISIONS = (
    "INCLUDE_SEGMENTS",
    "EXCLUDE_FROM_WORK",
    "REVIEW",
    "REVIEW_NO_DELETE",
    "REVIEW_RELEVANT_SEGMENTS",
    "REVIEW_PENDING",
    "PLAN",
    "PARTIAL",
    "PROPOSAL_REQUIRED",
    "REFUSAL",
    "UNSUPPORTED",
    "UNKNOWN",
    "REJECTED",
)

RELEVANCE_STATES = ("RELEVANT", "MIXED", "IRRELEVANT", "UNKNOWN")

STAGES = ("metadata/scope", "policy_match", "bounded_content", "relevance", "action")

CLEANUP_LEVELS = (
    "CONTENT_ISOLATION",
    "REBUILDABLE_ARTIFACT_CLEANUP",
    "CLOUD_RECOVERABLE_DISPOSAL",
    "IRREVERSIBLE_DELETE",
)

# The levels that take something away. Everything that guards them is keyed on
# this tuple rather than on a level name repeated in a branch.
DESTRUCTIVE_LEVELS = ("CLOUD_RECOVERABLE_DISPOSAL", "IRREVERSIBLE_DELETE")

# The levels that remove material this system owns. They need an approved
# retention in the way CONTENT_ISOLATION does not: isolation deletes nothing.
REMOVAL_LEVELS = ("REBUILDABLE_ARTIFACT_CLEANUP",) + DESTRUCTIVE_LEVELS

# The only delete_mode each level may run under. This is section 8's ladder made
# mechanical: a policy that authorises permanent deletion cannot plan an
# isolation, and a policy that authorises no deletion cannot plan a disposal.
LEVEL_DELETE_MODE = {
    "CONTENT_ISOLATION": "NO_DELETE",
    "REBUILDABLE_ARTIFACT_CLEANUP": "NO_DELETE",
    "CLOUD_RECOVERABLE_DISPOSAL": "RECYCLE_BIN",
    "IRREVERSIBLE_DELETE": "PERMANENT",
}

# A policy is written for one operation. A classification policy cannot be
# re-read as a disposal policy, because the operation no longer agrees.
LEVEL_OPERATION = {
    "CONTENT_ISOLATION": "CONTENT_ISOLATION",
    "REBUILDABLE_ARTIFACT_CLEANUP": "REBUILDABLE_ARTIFACT_CLEANUP",
    "CLOUD_RECOVERABLE_DISPOSAL": "CLOUD_RECOVERABLE_DISPOSAL",
    "IRREVERSIBLE_DELETE": "IRREVERSIBLE_DELETE",
}

SCOPE_KINDS = ("tenant_ref", "chat_ref", "meeting_ref")

# A selector is a real stable identifier or nothing. These are the spellings
# that would silently turn it into "everything".
WILDCARDS = ("*", "?", "all", "any", "any-chat", "all-meetings", "%", ".*")

OBSERVED_STATUSES = (
    "NOT_ATTEMPTED",
    "EXECUTED",
    "RESTORED",
    "PERMISSION_DENIED",
    "FAILED",
)

# Intents that change something this system controls.
ACTING_INTENTS = ("ISOLATE", "CLEAN_REBUILDABLE", "DISPOSE")


class Rejected(ValueError):
    """The request cannot be answered — as opposed to "the answer is no"."""


def default_vocabulary_path() -> Path:
    return Path(__file__).resolve().parent / "meeting-vocabulary.json"


def load_vocabulary(path: Path | str | None = None) -> dict:
    source = Path(path) if path else default_vocabulary_path()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise Rejected(f"cannot read meeting vocabulary: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise Rejected(f"meeting vocabulary is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != VOCABULARY_SCHEMA:
        raise Rejected(f"unsupported meeting vocabulary schema: {data.get('schema')!r}")
    return data


# --------------------------------------------------------------------------
# vocabulary access
# --------------------------------------------------------------------------

def _block(vocabulary: dict, name: str):
    block = vocabulary.get(name)
    if isinstance(block, dict) and "values" in block:
        return block["values"]
    return block


def _values(vocabulary: dict, dimension: str) -> dict:
    raw = ((vocabulary.get("dimensions") or {}).get(dimension) or {}).get("values")
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, list):
        return {value: "" for value in raw}
    return {}


def _required_policy_fields(vocabulary: dict) -> list[str]:
    raw = _block(vocabulary, "policy_required_fields")
    if not isinstance(raw, list) or not raw:
        raise Rejected("meeting vocabulary declares no policy_required_fields")
    return [_require(field, "policy_required_fields[]") for field in raw]


def _relevance_rules(vocabulary: dict) -> dict:
    block = (vocabulary.get("relevance_rules") or {}).get("values")
    if not isinstance(block, dict) or not block:
        raise Rejected("meeting vocabulary declares no relevance_rules")
    return block


def _reference_checks(vocabulary: dict) -> dict:
    raw = _block(vocabulary, "reference_checks")
    if not isinstance(raw, list) or not raw:
        raise Rejected("meeting vocabulary declares no reference_checks")
    return {check: "" for check in raw}


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------

def _require(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Rejected(f"{label} must be a non-empty string")
    return value.strip()


def _declared(value, dimension: str, vocabulary: dict, label: str):
    values = _values(vocabulary, dimension)
    text = _require(value, label)
    if text not in values:
        raise Rejected(f"unknown {label}: {text!r}")
    return text


def _stable_id(value, label: str) -> str:
    """A real stable identifier. A wildcard is refused here.

    Section 7 requires that a null or wildcard selector never match everything.
    Refusing it where it is read means a policy cannot acquire "all meetings" by
    leaving a value out.
    """
    text = _require(value, label)
    if text.strip().lower() in WILDCARDS or "*" in text or "?" in text:
        raise Rejected(f"{label} is a wildcard ({text!r}); a wildcard must not match everything")
    return text


def _iso8601(value, label: str) -> str:
    """Validate a fixed-format UTC instant without importing a clock.

    This kernel reads no clock. Expiry is evaluated against an `as_of` the
    caller states, and an instant that cannot be read is not "not expired".
    """
    text = _require(value, label)
    shape = "2026-09-20T00:00:00Z"
    if len(text) != len(shape):
        raise Rejected(f"{label} must be an ISO-8601 UTC instant like {shape}, got {text!r}")
    for index, character in enumerate(shape):
        if character in "-T:Z":
            if text[index] != character:
                raise Rejected(f"{label} must be an ISO-8601 UTC instant like {shape}, got {text!r}")
        elif not text[index].isdigit():
            raise Rejected(f"{label} must be an ISO-8601 UTC instant like {shape}, got {text!r}")
    return text


def _seconds(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Rejected(f"{label} must be a number of seconds")
    if value < 0:
        raise Rejected(f"{label} must not be negative")
    return value


def _scope(value, label: str) -> dict:
    """The stable identifiers that scope a bundle. An absent scope is empty."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise Rejected(f"{label} must be an object")
    out: dict[str, str] = {}
    for kind in SCOPE_KINDS:
        item = value.get(kind)
        if item is None:
            continue
        out[kind] = _stable_id(item, f"{label}.{kind}")
    return out


def _joined(scope: dict) -> str:
    return ", ".join(f"{kind}={scope[kind]}" for kind in SCOPE_KINDS if kind in scope)


def _stage(name: str, state: str, detail: str) -> dict:
    return {"stage": name, "state": state, "detail": detail}


def _assert_reason(reasons: list[str]) -> None:
    if not reasons or not all(isinstance(r, str) and r.strip() for r in reasons):
        raise AssertionError("every decision record carries a non-empty reason")


# --------------------------------------------------------------------------
# the protected exception
# --------------------------------------------------------------------------

def _is_bound(declaration: dict) -> bool:
    return declaration.get("binding_state") == "BOUND"


def exception_action(declaration: dict) -> str:
    """The action a protected exception authorises right now.

    `action_when_bound` has exactly one reader, here, behind exactly one
    condition: the declaration being BOUND. While a declaration is UNBOUND there
    is no code path that produces it — the vocabulary's Marek declaration is
    UNBOUND, so REVIEW_RELEVANT_SEGMENTS is unreachable for it.
    """
    if _is_bound(declaration):
        return declaration["action_when_bound"]
    return declaration["action_when_unbound"]


def exception_applies(declaration: dict, scope: dict) -> bool:
    """Whether a declaration selects the bundle described by `scope`.

    Only stable identifiers are consulted, and `scope` is the only input, so a
    title, a language and a participant list are not merely insufficient here —
    they are unreachable. A wildcard or absent selector selects nothing.
    """
    if not _is_bound(declaration):
        return False
    selector = declaration.get("meeting_selector")
    if not isinstance(selector, dict):
        return False
    kind = selector.get("kind")
    if kind not in SCOPE_KINDS:
        return False
    identifier = selector.get("id")
    if identifier is None:
        return False
    try:
        wanted = _stable_id(identifier, f"{declaration.get('ref')}.meeting_selector.id")
    except Rejected:
        return False
    if scope.get(kind) != wanted:
        return False
    tenant = declaration.get("tenant_ref")
    if tenant is not None and scope.get("tenant_ref") != tenant:
        return False
    return True


def _exceptions(vocabulary: dict) -> dict:
    """Index the declared protected exceptions, rejecting a malformed one."""
    table: dict[str, dict] = {}
    raw = vocabulary.get("protected_exceptions") or []
    if not isinstance(raw, list):
        raise Rejected("vocabulary.protected_exceptions must be a list")
    for number, declaration in enumerate(raw, 1):
        if not isinstance(declaration, dict):
            raise Rejected(f"protected exception {number} is not an object")
        ref = _require(declaration.get("ref"), f"protected exception {number} ref")
        if ref in table:
            raise Rejected(f"duplicate protected exception ref: {ref}")
        state = _declared(declaration.get("binding_state"), "binding_state", vocabulary,
                          f"{ref}.binding_state")
        for action_field in ("action_when_bound", "action_when_unbound"):
            action = _require(declaration.get(action_field), f"{ref}.{action_field}")
            if action not in DECISIONS:
                raise Rejected(f"{ref}.{action_field} is not a declared decision: {action!r}")
        if declaration.get("title_or_name_matching_allowed"):
            raise Rejected(
                f"{ref} asks for title or name matching; this kernel has no title or name "
                "matcher, so a declaration that needs one cannot be honoured")
        if state == "UNBOUND" and any(declaration.get(key) is not None
                                      for key in ("tenant_ref", "chat_ref", "meeting_selector")):
            raise Rejected(
                f"{ref} is UNBOUND but declares a selector; an unbound declaration selects "
                "nothing and must not carry one")
        if state == "BOUND":
            for key in ("tenant_ref", "chat_ref"):
                if declaration.get(key) is not None:
                    _stable_id(declaration[key], f"{ref}.{key}")
            selector = declaration.get("meeting_selector")
            if not isinstance(selector, dict):
                raise Rejected(
                    f"{ref} is BOUND but declares no meeting_selector; a bound exception must "
                    "name by stable identifier what it selects, or it selects nothing and "
                    "permits a destructive plan it cannot justify")
            if selector.get("kind") not in SCOPE_KINDS:
                raise Rejected(f"{ref}.meeting_selector.kind is not one of {list(SCOPE_KINDS)}")
            _stable_id(selector.get("id"), f"{ref}.meeting_selector.id")
        table[ref] = declaration
    return table


def _exception_view(declaration: dict, scope: dict) -> dict:
    """What a declaration does to THIS bundle.

    `action` is the action that applies here, not the action the declaration
    would take if it applied: a bound declaration that does not select this
    bundle reports REVIEW_NO_DELETE, so nothing in the record can be misread as
    an admission that did not happen.
    """
    bound = _is_bound(declaration)
    applied = exception_applies(declaration, scope)
    view = {
        "ref": declaration["ref"],
        "binding_state": declaration.get("binding_state"),
        "applied": applied,
        "action": exception_action(declaration) if applied
                  else declaration["action_when_unbound"],
        "action_when_bound_reachable": bound,
    }
    if bound and applied:
        view["action_when_bound"] = declaration.get("action_when_bound")
    return view


# --------------------------------------------------------------------------
# policy shape
# --------------------------------------------------------------------------

def _selector(policy: dict):
    """The policy's stable selector, or None when it can select nothing.

    None is a real answer and is reported: a policy whose selector is absent or
    a wildcard is inert rather than universal.
    """
    raw = policy.get("source_selector")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise Rejected("policy.source_selector must be an object")
    kind = _require(raw.get("kind"), "policy.source_selector.kind")
    if kind not in SCOPE_KINDS:
        raise Rejected(f"unknown policy.source_selector.kind: {kind!r}")
    identifier = raw.get("id")
    if identifier is None:
        return None
    if not isinstance(identifier, str) or not identifier.strip():
        raise Rejected("policy.source_selector.id must be a non-empty string")
    try:
        return kind, _stable_id(identifier, "policy.source_selector.id")
    except Rejected:
        return None


def _policy_shape(policy, vocabulary: dict) -> None:
    """Reject a policy whose *syntax* is wrong. A caller bug, not a refusal."""
    if not isinstance(policy, dict):
        raise Rejected("policy must be an object")
    if policy.get("policy_revision") is not None:
        _require(policy["policy_revision"], "policy.policy_revision")
    if policy.get("operation") is not None:
        _declared(policy["operation"], "operation", vocabulary, "policy.operation")
    if policy.get("allowed_content_depth") is not None:
        _declared(policy["allowed_content_depth"], "content_depth", vocabulary,
                  "policy.allowed_content_depth")
    if policy.get("delete_mode") is not None:
        _declared(policy["delete_mode"], "delete_mode", vocabulary, "policy.delete_mode")
    if policy.get("approval") is not None:
        _declared(policy["approval"], "approval_mode", vocabulary, "policy.approval")
    if policy.get("owner_constraint") is not None:
        _declared(policy["owner_constraint"], "owner_constraint", vocabulary,
                  "policy.owner_constraint")
    if policy.get("binding_state") is not None:
        _declared(policy["binding_state"], "binding_state", vocabulary, "policy.binding_state")
    if policy.get("relevance_rule") is not None:
        rule = _require(policy["relevance_rule"], "policy.relevance_rule")
        if rule not in _relevance_rules(vocabulary):
            raise Rejected(f"unknown policy.relevance_rule: {rule!r}")
    if policy.get("source_selector") is not None:
        _selector(policy)
    if policy.get("scope") is not None:
        _scope(policy["scope"], "policy.scope")
    if policy.get("expires_at") is not None:
        _iso8601(policy["expires_at"], "policy.expires_at")
    if policy.get("retention_window") is not None:
        retention = policy["retention_window"]
        if not isinstance(retention, dict):
            raise Rejected("policy.retention_window must be an object")
        if retention.get("approved_policy_ref") is not None:
            _require(retention["approved_policy_ref"], "retention_window.approved_policy_ref")
        if retention.get("days") is not None:
            days = retention["days"]
            if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
                raise Rejected("retention_window.days must be a positive integer or null")
    if policy.get("protected_exceptions") is not None:
        refs = policy["protected_exceptions"]
        if not isinstance(refs, list):
            raise Rejected("policy.protected_exceptions must be a list of exception refs")
        for ref in refs:
            _require(ref, "policy.protected_exceptions[]")
    if policy.get("references_holds_checks") is not None:
        checks = policy["references_holds_checks"]
        if not isinstance(checks, list) or not checks:
            raise Rejected("policy.references_holds_checks must be a non-empty list")
        declared = _reference_checks(vocabulary)
        for check in checks:
            name = _require(check, "policy.references_holds_checks[]")
            if name not in declared:
                raise Rejected(f"unknown policy.references_holds_checks entry: {name!r}")
    if policy.get("restore_capability") is not None:
        capability = policy["restore_capability"]
        if not isinstance(capability, dict) or not capability:
            raise Rejected("policy.restore_capability must be a non-empty object")
    if policy.get("receipt_store") is not None:
        _require(policy["receipt_store"], "policy.receipt_store")
    if policy.get("unmatched") is not None:
        allowed = _block(vocabulary, "review_actions") or []
        action = _require(policy["unmatched"], "policy.unmatched")
        if action not in allowed:
            raise Rejected(
                f"policy.unmatched must be one of {list(allowed)}, got {action!r}: unmatched "
                "content is reviewed, never excluded and never deleted")


def _policy_problems(policy: dict, vocabulary: dict) -> list[str]:
    """Required policy fields that are absent, null or empty. Each is a REFUSAL."""
    problems: list[str] = []
    for field in _required_policy_fields(vocabulary):
        if field not in policy or policy[field] is None:
            problems.append(field)
    if "scope" in problems or "retention_window" in problems:
        return problems
    if not _scope(policy.get("scope"), "policy.scope"):
        problems.append("scope")
    retention = policy["retention_window"]
    if not isinstance(retention, dict):
        raise Rejected("policy.retention_window must be an object")
    if retention.get("days") is None:
        problems.append("retention_window.days")
    return problems


def _policy_list(policies, vocabulary: dict) -> list[dict]:
    if policies is None:
        return []
    if not isinstance(policies, list):
        raise Rejected("policies must be a list")
    out = []
    for policy in policies:
        _policy_shape(policy, vocabulary)
        out.append(policy)
    return out


# --------------------------------------------------------------------------
# content
# --------------------------------------------------------------------------

def _segments(bundle: dict, depth: str, rule: dict):
    """The bundle's time-located segments: (admitted, dropped, tied_count).

    Text is never carried out of this function. Section 9 forbids committing a
    real transcript, and this kernel decides nothing from text anyway: a tie to
    a support request or a decision must be **declared** by the producer that
    read the content. This module does not infer one, and has no language model
    with which to try.
    """
    if depth == "METADATA_ONLY":
        return [], [], 0
    content = bundle.get("content")
    if content is None:
        return [], [], 0
    if not isinstance(content, dict):
        raise Rejected("bundle.content must be an object")
    raw = content.get("transcript_segments") or []
    if not isinstance(raw, list):
        raise Rejected("bundle.content.transcript_segments must be a list")

    admit_untied = rule.get("incidental_audio_to_understanding") != "EXCLUDE"
    admitted: list[dict] = []
    dropped: list[dict] = []
    tied_count = 0
    for number, segment in enumerate(raw, 1):
        if not isinstance(segment, dict):
            raise Rejected(f"bundle.content.transcript_segments[{number}] is not an object")
        start = _seconds(segment.get("start_s"), f"segment {number} start_s")
        end = _seconds(segment.get("end_s"), f"segment {number} end_s")
        if end < start:
            raise Rejected(f"segment {number} ends before it starts")
        located = {
            "start_s": start,
            "end_s": end,
            "provenance": _require(segment.get("provenance"),
                                   f"segment {number} provenance"),
        }
        tie = segment.get("tie")
        if tie is None:
            if admit_untied:
                located["tie"] = None
                admitted.append(located)
            else:
                dropped.append(located)
            continue
        if not isinstance(tie, dict):
            raise Rejected(f"segment {number} tie must be an object")
        located["tie"] = {
            "kind": _require(tie.get("kind"), f"segment {number} tie.kind"),
            "ref": _require(tie.get("ref"), f"segment {number} tie.ref"),
        }
        tied_count += 1
        admitted.append(located)
    return admitted, dropped, tied_count


def _dependants(bundle: dict):
    raw = bundle.get("dependants") or []
    if not isinstance(raw, list):
        raise Rejected("bundle.dependants must be a list")
    approved, recomputable = [], []
    for number, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise Rejected(f"bundle.dependants[{number}] is not an object")
        entry = {
            "kind": _require(item.get("kind"), f"bundle.dependants[{number}].kind"),
            "ref": _require(item.get("ref"), f"bundle.dependants[{number}].ref"),
        }
        if item.get("human_approved") is True or item.get("published") is True:
            entry["human_approved"] = True
            entry["required_action"] = "REVIEW"
            approved.append(entry)
        else:
            entry["human_approved"] = False
            entry["required_action"] = "REBUILD"
            recomputable.append(entry)
    return approved, recomputable


def _references(bundle: dict) -> list:
    relation = bundle.get("work_relation")
    if relation is None:
        relation = {}
    if not isinstance(relation, dict):
        raise Rejected("bundle.work_relation must be an object")
    references = relation.get("references") or []
    if not isinstance(references, list):
        raise Rejected("bundle.work_relation.references must be a list")
    for number, reference in enumerate(references, 1):
        if not isinstance(reference, dict):
            raise Rejected(f"bundle.work_relation.references[{number}] is not an object")
        _require(reference.get("ref"), f"bundle.work_relation.references[{number}].ref")
    return references


def _signals(bundle: dict, vocabulary: dict) -> dict:
    """Every signal, recorded, and recorded as insufficient.

    Nothing downstream reads a signal. That is the point of section 7: a
    duration, a mute boolean, a language, a title, a participant list and an
    automatic summary each cannot prove that content has no business value.
    """
    raw = bundle.get("signals")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise Rejected("bundle.signals must be an object")
    recorded = {name: raw.get(name) for name in (_block(vocabulary, "signals") or [])}
    recorded["sufficiency"] = "RECORDED_INSUFFICIENT"
    recorded["note"] = ("recorded, and insufficient on its own: no signal here can establish "
                        "that this bundle has no business value, and no branch of this kernel "
                        "reads one")
    return recorded


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

def classify(*, bundle, policies=None, vocabulary=None, as_of=None) -> dict:
    """Return the relevance decision record for one MeetingBundle.

    `bundle.scope` carries the stable identifiers; `bundle.signals` the
    recorded-and-insufficient signals; `bundle.content` the time-located
    segments with their declared ties; `bundle.work_relation.references` the work
    that names this bundle; `bundle.dependants` the work that used it; and
    `bundle.ownership` / `bundle.hold` two axes that are reported and never
    consulted for relevance.
    """
    vocab = vocabulary if vocabulary is not None else load_vocabulary()
    if not isinstance(bundle, dict):
        raise Rejected("bundle must be an object")
    bundle_ref = _require(bundle.get("bundle_ref"), "bundle_ref")
    policy_list = _policy_list(policies, vocab)
    if as_of is not None:
        as_of = _iso8601(as_of, "as_of")

    axes = _axes(None, bundle, vocab)
    signals = _signals(bundle, vocab)

    def finish(*, decision, relevance, stages, reasons, policy=None, segments=(),
               excluded=None, exceptions=(), delta=None, confidence, approval):
        return _relevance_record(
            bundle_ref=bundle_ref, decision=decision, relevance=relevance, stages=stages,
            reasons=reasons, axes=_axes(policy, bundle, vocab), segments=list(segments),
            excluded=excluded, exclusions=_exclusions(policy, vocab), work_evidence=(
                _work_evidence(_relevance_rules(vocab)[policy["relevance_rule"]])
                if policy is not None else _work_evidence(None)),
            audio_fetch=_audio_fetch(policy, vocab), signals=signals,
            exceptions=list(exceptions), delta=delta, confidence=confidence, approval=approval)

    # One policy shape for every operation. A policy that cannot say what it
    # retains cannot be read as one that may act, so an unreadable policy set is
    # a refusal rather than a licence.
    incomplete = [(index, field)
                  for index, policy in enumerate(policy_list, 1)
                  for field in _policy_problems(policy, vocab)]
    if incomplete:
        reasons = [
            f"policy {index} has no usable {field}: an executable policy needs it, and an "
            "absent value is not a permissive default"
            for index, field in incomplete]
        stages = [_stage(name, "NO_INPUT", "the policy set could not be read") for name in STAGES]
        return finish(decision="REFUSAL", relevance="UNKNOWN", stages=stages, reasons=reasons,
                      confidence="high", approval="explicit")

    # ---- stage 1: metadata/scope
    scope = _scope(bundle.get("scope"), "bundle.scope")
    stages: list[dict] = []
    if scope:
        stages.append(_stage("metadata/scope", "PRODUCED", f"scope: {_joined(scope)}"))
    else:
        stages.append(_stage(
            "metadata/scope", "NO_INPUT",
            "no tenant/chat/meeting identity was supplied, so no rule can be matched and "
            "nothing below is a classification"))

    exceptions = _exceptions(vocab)
    views = [_exception_view(declaration, scope) for declaration in exceptions.values()]
    applied = [view for view in views if view["applied"]]

    # ---- stage 2: policy match
    matched: list[int] = []
    inert: list[int] = []
    for index, policy in enumerate(policy_list, 1):
        selector = _selector(policy)
        if selector is None:
            inert.append(index)
            continue
        kind, identifier = selector
        if scope.get(kind) == identifier:
            matched.append(index)

    if not scope:
        stages.append(_stage("policy_match", "NO_INPUT", "there was no scope to match against"))
    elif len(matched) > 1:
        stages.append(_stage("policy_match", "AMBIGUOUS",
                             f"{len(matched)} policies match {_joined(scope)} by stable id"))
    elif not matched:
        detail = f"no policy matches {_joined(scope)} by stable id"
        if inert:
            detail += (f"; policy {inert} declares a selector that selects nothing (absent or "
                       "wildcard), which is reported rather than treated as everything")
        stages.append(_stage("policy_match", "NO_MATCH", detail))
    else:
        stages.append(_stage("policy_match", "PRODUCED",
                             f"policy {matched[0]} matches {_joined(scope)} by stable id"))

    unmatched_default = vocab.get("unmatched_default", "REVIEW_NO_DELETE")
    unbound_views = [view for view in views if view["binding_state"] == "UNBOUND"]

    if len(matched) != 1:
        for name in ("bounded_content",):
            stages.append(_stage(name, "NO_INPUT",
                                 "no single policy governs this bundle, so this stage was not "
                                 "reached"))
        stages.append(_stage("relevance", "NO_INPUT",
                             "no single rule governs this bundle, so the verdict is UNKNOWN"))
        stages.append(_stage("action", "PRODUCED", f"unmatched default: {unmatched_default}"))
        reasons = ["no single confirmed source rule governs this bundle; an unknown source is "
                   "reviewed, never excluded and never deleted"]
        if inert:
            reasons.append(
                f"policy {inert} declares a selector that selects nothing (absent, null or a "
                "wildcard); it is reported as inert rather than read as 'everything'")
        if len(matched) > 1:
            reasons.append(
                f"{len(matched)} policies match the same stable id; an ambiguous policy set is "
                "a question for a human")
        if unbound_views:
            reasons.append(
                "the user's declared exception is UNBOUND, so a bundle that cannot be matched "
                "by stable id might be its group; deletion is forbidden while that is open")
        return finish(decision=unmatched_default, relevance="UNKNOWN", stages=stages,
                      reasons=reasons, exceptions=views, confidence="low", approval="explicit")

    policy = policy_list[matched[0] - 1]
    rule = _relevance_rules(vocab)[policy["relevance_rule"]]
    depth = policy["allowed_content_depth"]

    # ---- stage 3: bounded content
    admitted, dropped, tied_count = _segments(bundle, depth, rule)
    if depth == "METADATA_ONLY":
        stages.append(_stage(
            "bounded_content", "NOT_PERMITTED",
            "allowed_content_depth is METADATA_ONLY: the policy permits reading identity and "
            "durations, and no transcript is read"))
    elif admitted or dropped:
        stages.append(_stage(
            "bounded_content", "PRODUCED",
            f"depth={depth}; {tied_count} of {len(admitted) + len(dropped)} time-located "
            "segment(s) are declared as tied to a support request or a decision"))
    else:
        stages.append(_stage("bounded_content", "NO_INPUT",
                             f"depth={depth} permits reading, and the bundle carries no segment"))

    references = _references(bundle)

    # ---- stage 4: relevance
    if not rule.get("source_confirmed"):
        relevance = "UNKNOWN"
        stages.append(_stage(
            "relevance", "NO_INPUT",
            f"relevance_rule={policy['relevance_rule']} does not confirm the source, so "
            "nothing may be excluded and the verdict is UNKNOWN"))
    elif tied_count and dropped:
        relevance = "MIXED"
        stages.append(_stage(
            "relevance", "PRODUCED",
            f"{tied_count} tied segment(s) are kept and {len(dropped)} untied segment(s) are "
            "excluded"))
    elif tied_count:
        relevance = "RELEVANT"
        stages.append(_stage("relevance", "PRODUCED",
                             f"{tied_count} tied segment(s) are kept"))
    elif references:
        relevance = "RELEVANT"
        stages.append(_stage(
            "relevance", "PRODUCED",
            f"the bundle is named by {len(references)} work reference(s): the tie is the "
            "reference, not a segment"))
    elif rule.get("incidental_audio_to_understanding") == "EXCLUDE":
        relevance = "IRRELEVANT"
        stages.append(_stage(
            "relevance", "PRODUCED",
            f"relevance_rule={policy['relevance_rule']} confirms this source and excludes "
            "incidental audio from understanding, the board and reports"))
    else:
        relevance = "UNKNOWN"
        stages.append(_stage(
            "relevance", "NO_INPUT",
            "no tie, no work reference and no excluding rule were available, so there is "
            "nothing to classify"))

    # ---- stage 5: action
    reasons = []
    if relevance == "IRRELEVANT":
        decision, approval = "EXCLUDE_FROM_WORK", "policy-approved"
        reasons.append(
            f"relevance_rule={policy['relevance_rule']} confirms the source and excludes "
            "incidental audio; the original is untouched, and the exclusion is a "
            "content-isolation record with a reason and a way back rather than a deletion")
    elif relevance == "MIXED":
        decision, approval = "INCLUDE_SEGMENTS", "policy-approved"
        reasons.append(
            "only the time-located segments tied to a support request or a decision are kept, "
            "with their provenance; other people's chatter is not turned into the user's "
            "responsibilities, todos or completed outcomes")
    elif relevance == "RELEVANT":
        decision, approval = "INCLUDE_SEGMENTS", "policy-approved"
        reasons.append(
            "the bundle is tied to work — by a declared segment tie or by a reference that "
            "names it — so it enters work with its provenance intact")
    else:
        decision = policy.get("unmatched") or unmatched_default
        approval = "explicit"
        reasons.append(
            "the pipeline could not establish relevance, and an unestablished answer is "
            "reviewed rather than guessed")

    if applied:
        exception = applied[0]
        decision = exception["action"]
        approval = "explicit"
        reasons.append(f"protected exception {exception['ref']} is {exception['binding_state']} "
                       "and selects this bundle by stable id, so its action applies")
    elif unbound_views:
        reasons.append(
            "the user's declared exception is UNBOUND and therefore selects nothing here; no "
            "title, language or participant name was consulted to reach that conclusion")

    # ---- human-approved work that used a now-excluded source
    delta = None
    if decision == "EXCLUDE_FROM_WORK":
        approved, recomputable = _dependants(bundle)
        if approved:
            decision = "REVIEW_PENDING"
            delta = {
                "kind": "SOURCE_EXCLUDED",
                "source_ref": bundle_ref,
                "mutation": "NONE",
                "affected": approved,
                "recomputable": recomputable,
                "note": ("human-approved work that used this source is marked for review and "
                         "given a delta; it is never silently rewritten or deleted"),
            }
            reasons.append(
                f"{len(approved)} human-approved dependant(s) used this source; they are "
                "marked REVIEW_PENDING with a delta, and nothing is mutated")
        elif recomputable:
            delta = {
                "kind": "SOURCE_EXCLUDED",
                "source_ref": bundle_ref,
                "mutation": "REBUILD_ALLOWED",
                "affected": [],
                "recomputable": recomputable,
                "note": ("machine-generated drafts are recomputable: they may be rebuilt from "
                         "the filtered result, keeping their revision and their source"),
            }

    stages.append(_stage("action", "PRODUCED", f"decision={decision}"))

    if rule.get("incidental_audio_to_understanding") == "EXCLUDE":
        reasons.append(
            "the content is excluded from understanding, the board and reports; the non-audio "
            "evidence path stays open, so support work is still evidenced by chat, "
            "configuration, tests and result confirmation")
    elif not rule.get("source_confirmed"):
        reasons.append(
            "nothing is excluded from an unconfirmed source: the fail-closed direction is "
            "review, not exclusion")

    return finish(decision=decision, relevance=relevance, stages=stages, reasons=reasons,
                  policy=policy, segments=admitted,
                  excluded={"count": len(dropped),
                            "reason": ("incidental audio excluded by a confirmed source rule"
                                       if dropped else None)},
                  exceptions=views, delta=delta,
                  confidence=("high" if decision in ("INCLUDE_SEGMENTS", "REVIEW_PENDING")
                              else "medium"),
                  approval=approval)


def _audio_fetch(policy, vocabulary: dict) -> str:
    if policy is None:
        return "DENY"
    rule = _relevance_rules(vocabulary).get(policy["relevance_rule"]) or {}
    return rule.get("audio_fetch") or "DENY"


def _axes(policy, bundle: dict, vocabulary: dict) -> dict:
    """The five axes, kept apart.

    `deletability` is NOT_EVALUATED here for every input, including an
    IRRELEVANT verdict with every signal set against the bundle. Nothing in
    `classify` can produce another value, so relevance cannot reach a deletion.
    """
    ownership = bundle.get("ownership")
    hold = bundle.get("hold")
    retention = {"policy_ref": None, "days": None}
    if policy is not None:
        window = policy.get("retention_window")
        if isinstance(window, dict):
            retention = {"policy_ref": window.get("approved_policy_ref"),
                         "days": window.get("days")}
    return {
        "relevance": None,
        "ownership": _declared_or("UNKNOWN" if ownership is None else ownership,
                                  "ownership", vocabulary),
        "retention": retention,
        "hold": _declared_or("UNKNOWN" if hold is None else hold, "hold", vocabulary),
        "deletability": "NOT_EVALUATED",
    }


def _declared_or(value, dimension: str, vocabulary: dict) -> str:
    if value in _values(vocabulary, dimension):
        return value
    known = sorted(_values(vocabulary, dimension))
    raise Rejected(f"unknown {dimension}: {value!r}; declared values are {known}")


def _exclusions(policy, vocabulary: dict) -> dict:
    if policy is None:
        return {"understanding": False, "board": False, "report": False,
                "basis": "no confirmed source rule", "restore_entry": None}
    rule = _relevance_rules(vocabulary).get(policy["relevance_rule"]) or {}
    excluding = rule.get("incidental_audio_to_understanding") == "EXCLUDE"
    return {
        "understanding": excluding,
        "board": rule.get("incidental_audio_to_board") == "EXCLUDE",
        "report": rule.get("incidental_audio_to_report") == "EXCLUDE",
        "basis": f"relevance_rule={policy['relevance_rule']}",
        "restore_entry": ("content-isolation record carrying the reason and the way back"
                          if excluding else None),
    }


def _work_evidence(rule) -> dict:
    """The non-audio path, first-class.

    Section 7: support work is evidenced by chat, the request, configuration and
    test results, and by the counterparty confirming the outcome. Excluding
    incidental audio must not delete the work, so the path is declared beside
    the exclusion rather than inferred from it.
    """
    if not rule or not rule.get("source_confirmed"):
        return {"paths": [], "audio_required": True, "authority": "REVIEW",
                "note": "no confirmed source rule opens an evidence path"}
    paths = [path for path in (rule.get("evidence_paths") or []) if path != "MEETING_AUDIO"]
    return {
        "paths": paths,
        "audio_required": False,
        "authority": rule.get("support_evidence_authority"),
        "note": ("audio is not required: the work is evidenced by " + ", ".join(paths))
        if paths else "no non-audio path was declared for this rule",
    }


def _relevance_record(*, bundle_ref, decision, relevance, stages, reasons, axes, segments,
                      excluded, exclusions, work_evidence, audio_fetch, signals, exceptions,
                      delta, confidence, approval) -> dict:
    if decision not in DECISIONS:
        raise AssertionError(f"unreachable decision: {decision}")
    if relevance not in RELEVANCE_STATES:
        raise AssertionError(f"unreachable relevance: {relevance}")
    _assert_reason(reasons)
    seen = [entry["stage"] for entry in stages]
    if seen != list(STAGES):
        raise AssertionError(f"the pipeline must report all five stages in order, saw {seen}")
    axes = dict(axes or {})
    axes["relevance"] = relevance
    return {
        "schema": SCHEMA,
        "bundle_ref": bundle_ref,
        "decision": decision,
        "relevance": relevance,
        "stages": stages,
        "axes": axes,
        "segments": segments,
        "excluded_segments": excluded,
        "exclusions": exclusions,
        "work_evidence": work_evidence,
        "audio_fetch": audio_fetch,
        "signals": signals,
        "protected_exceptions": exceptions,
        # The relevance kernel never produces a cleanup level. Section 7 makes
        # deletability an axis of its own, and this is what that looks like in a
        # record: there is no field a caller could read as permission to delete.
        "cleanup": None,
        "delta": delta,
        "reason": reasons,
        "confidence": confidence,
        "approval": approval,
    }


# --------------------------------------------------------------------------
# cleanup
# --------------------------------------------------------------------------

def plan_cleanup(request: dict, vocabulary: dict | None = None) -> dict:
    """Plan — or refuse — one cleanup level for a set of explicit objects.

    The eligibility inputs are the object's own revision, owner and permission,
    its references and holds, whether a human edited it, the policy's retention
    and binding state, the provider's real recovery semantics, and a
    per-operation authorization for IRREVERSIBLE_DELETE. A relevance verdict is
    not among them, and a request that supplies one is told so.
    """
    vocab = vocabulary if vocabulary is not None else load_vocabulary()
    if not isinstance(request, dict):
        raise Rejected("cleanup request must be an object")

    policy = request.get("policy")
    if policy is None:
        raise Rejected("cleanup request.policy is required")
    _policy_shape(policy, vocab)
    level = _declared(request.get("level"), "cleanup_level", vocab, "level")
    as_of = _iso8601(request["as_of"], "as_of") if request.get("as_of") is not None else None

    objects = request.get("objects")
    if objects is None:
        raise Rejected("cleanup request.objects is required")
    if not isinstance(objects, list) or not objects:
        raise Rejected("cleanup request.objects must be a non-empty list")

    retry_budget = request.get("retry_budget", 0)
    if isinstance(retry_budget, bool) or not isinstance(retry_budget, int) or retry_budget < 0:
        raise Rejected("cleanup request.retry_budget must be a non-negative integer")

    provider = request.get("provider") or {}
    if not isinstance(provider, dict):
        raise Rejected("cleanup request.provider must be an object")

    reasons: list[str] = []
    ignored: list[str] = []

    def refuse(decision: str, deletability: str, more: list[str] | None = None) -> dict:
        return _cleanup_record(
            decision=decision, level=level, policy=policy, objects=[],
            reasons=reasons + list(more or []), deletability=deletability,
            recorded_only=ignored, partial=False)

    # A relevance verdict may be supplied for the record and is never consulted.
    if request.get("relevance") is not None:
        ignored.append("relevance")
        reasons.append(
            "relevance was supplied and is recorded only: it is not an eligibility input, "
            "because section 7 makes deletability an axis of its own")

    problems = _policy_problems(policy, vocab)
    if problems:
        return refuse("REFUSAL", "BLOCKED", [
            f"policy.{field} is absent or null: an executable policy needs it, and a missing "
            "field is a refusal rather than a permissive default" for field in problems])

    # A selector that names nothing is not a licence. The required field being
    # present is not enough: it has to be usable.
    if _selector(policy) is None:
        return refuse("REFUSAL", "BLOCKED", [
            "policy.source_selector does not name a source by stable identifier: absent, null "
            "and wildcard all select nothing, and a policy that cannot name its source may not "
            "act on anything"])

    # Retention. A day count nobody approved is not consent.
    if level in REMOVAL_LEVELS and policy["retention_window"].get("approved_policy_ref") is None:
        return refuse("PROPOSAL_REQUIRED", "NOT_A_CANDIDATE", [
            "retention_window.approved_policy_ref is null: no approved retention policy exists "
            "for this operation, and a human must author one"])

    # Operation and delete mode must agree with the level: a policy written for
    # one rung of section 8's ladder is not reusable on another.
    if policy["operation"] != LEVEL_OPERATION[level]:
        return refuse("REFUSAL", "BLOCKED", [
            f"policy.operation is {policy['operation']!r} and level {level} requires "
            f"{LEVEL_OPERATION[level]!r}; a policy is written for one operation"])
    if policy["delete_mode"] != LEVEL_DELETE_MODE[level]:
        return refuse("REFUSAL", "BLOCKED", [
            f"policy.delete_mode is {policy['delete_mode']!r} and level {level} requires "
            f"{LEVEL_DELETE_MODE[level]!r}: the levels are a ladder, and no rung implies the "
            "next"])

    # The checks the policy declares it performs. Two of them are not optional
    # for a level that takes something away: an object may not be acted on under
    # a policy that never reads its revision or its ownership. CONTENT_ISOLATION
    # is not gated on them, because it deletes nothing and touches nobody's
    # object.
    declared_checks = list(policy["references_holds_checks"])
    if level in REMOVAL_LEVELS:
        for check in ("CURRENT_OBJECT_REVISION", "CONFIRMED_OWNER_AND_PERMISSION"):
            if check not in declared_checks:
                return refuse("REFUSAL", "BLOCKED", [
                    f"policy.references_holds_checks omits {check}: an object may not be acted "
                    "on under a policy that does not check it"])

    if level in REMOVAL_LEVELS and policy["owner_constraint"] == "NONE":
        return refuse("REFUSAL", "BLOCKED", [
            "policy.owner_constraint is NONE: a policy that names no owner may not act on any "
            "object"])

    # A level that takes something away may not run under a policy that is not
    # bound to a real source, or whose expiry cannot be read or has passed. An
    # unbound policy fails closed: a refusal is not a plan.
    if level in REMOVAL_LEVELS:
        if policy.get("binding_state") != "BOUND":
            return refuse("REFUSAL", "BLOCKED", [
                f"policy.binding_state is {policy.get('binding_state')!r}: an unbound policy "
                "fails closed, and a refusal is not a plan"])
        expiry = _expiry_problem(policy, as_of)
        if expiry:
            return refuse("REFUSAL", "BLOCKED", [expiry])
        unresolved = _unresolved_exceptions(policy, vocab)
        if unresolved:
            return refuse("REFUSAL", "BLOCKED", [
                "protected exceptions are not resolved: " + "; ".join(unresolved) +
                ". A plan that removes material cannot show that its objects are not the "
                "exception's, so it refuses rather than guesses"])

    if level in DESTRUCTIVE_LEVELS:
        if level == "IRREVERSIBLE_DELETE":
            verdict = _authorization_problem(request.get("authorization"),
                                             LEVEL_OPERATION[level], as_of)
            if verdict:
                return refuse("REFUSAL", "BLOCKED", [verdict])
            reasons.append(
                "a per-operation authorization was supplied in this request; the relevance "
                "classifier can never supply one, so no classification reaches this level")

        if level == "CLOUD_RECOVERABLE_DISPOSAL":
            recycle = provider.get("recycle_bin")
            restore = provider.get("restore")
            if recycle is None or restore is None:
                return refuse("UNKNOWN", "BLOCKED", [
                    "the provider's recycle and restore support was not established: an "
                    "unknown answer is refused, and a permanent delete is never dressed as a "
                    "recycle"])
            if not recycle or not restore:
                return refuse("UNSUPPORTED", "BLOCKED", [
                    f"the provider supports recycle_bin={recycle!r} restore={restore!r}: this "
                    "level requires both, and the objects are left where they are"])

    policy_scope = _scope(policy["scope"], "policy.scope")
    exceptions = _exceptions(vocab)

    planned = [_plan_object(level, policy_scope, item, number, retry_budget, vocab, exceptions)
               for number, item in enumerate(objects, 1)]

    intents = [entry["intent"] for entry in planned]
    acting = [intent for intent in intents if intent in ACTING_INTENTS]
    refused = [intent for intent in intents if intent == "REFUSED"]
    blocked = [intent for intent in intents
               if intent in ("BLOCKED_PERMISSION_DENIED", "SKIP", "REFUSED")]

    # Why each object went the way it did belongs on the record, not only on the
    # object: a refusal carries no plan, so the object reasons would otherwise be
    # lost exactly when they matter most.
    details = [f"{entry['object_ref']} ({entry['object_type']}): {entry['reason'][0]}"
               for entry in planned]

    if refused and not acting:
        decision = "REFUSAL"
        deletability = "NOT_A_CANDIDATE"
        reasons.append(
            "no object in the request is eligible: the plan refuses rather than acting on the "
            "subset that happened to pass")
        reasons.extend(details)
    elif blocked:
        decision = "PARTIAL"
        deletability = "BLOCKED"
        reasons.append(
            f"{len(blocked)} of {len(planned)} object(s) did not proceed; each object carries "
            "its own intent, result and reason, and a re-entry reads them back rather than "
            "retrying work that already succeeded")
        reasons.extend(details)
    else:
        decision = "PLAN"
        deletability = "ELIGIBLE"
        reasons.append(
            f"all {len(planned)} object(s) are eligible under {level} at policy revision "
            f"{policy['policy_revision']!r}, within the scope "
            f"{_joined(policy_scope)} and no wider")
        reasons.append(
            "nothing has been acted on: this is a plan, and each object's receipt is written "
            "by the adapter that carries it out")

    already = [entry["object_ref"] for entry in planned if entry["intent"] == "ALREADY_EXECUTED"]
    restored = [entry["object_ref"] for entry in planned if entry["intent"] == "REINDEX_REQUIRED"]
    # Suppression is about read-back state, not about intent: an index can still
    # remember an object this system has already acted on, and re-proposing it
    # would dispose of it twice. An object nobody has acted on yet is not on this
    # list, because the receipt is what will say so next time.
    suppressed = already + restored
    if already:
        reasons.append(
            f"{len(already)} object(s) were read back as already executed and are not retried: "
            "a deletion that already succeeded is not repeated")
    if restored:
        reasons.append(
            f"{len(restored)} object(s) were restored, so derived indexes must be rebuilt and "
            "the candidate must not regrow; the suppression list is part of the plan")
    if suppressed:
        reasons.append(
            "suppress_candidate_regeneration lists the objects an index must stop proposing: "
            + ", ".join(suppressed))
    if any(entry["intent"] == "BLOCKED_PERMISSION_DENIED" for entry in planned):
        reasons.append(
            "a permission denial is recorded as blocked and is not retried under another "
            "identity")

    return _cleanup_record(
        decision=decision, level=level, policy=policy, objects=planned, reasons=reasons,
        deletability=deletability, recorded_only=ignored, partial=(decision == "PARTIAL"),
        already_executed=already, reindex_required=restored, suppressed=suppressed,
        policy_scope=policy_scope)


def _authorization_problem(authorization, operation: str, as_of) -> str | None:
    if authorization is None:
        return ("IRREVERSIBLE_DELETE requires this operation's own explicit authorization, "
                "supplied in the request; the relevance classifier can never supply one")
    if not isinstance(authorization, dict):
        raise Rejected("cleanup request.authorization must be an object")
    kind = authorization.get("kind")
    if kind != "PER_OPERATION":
        return (f"authorization.kind is {kind!r}: only a per-operation authorization permits "
                "an irreversible delete")
    for field in ("operation_ref", "approved_by", "expires_at"):
        if authorization.get(field) is None:
            return f"authorization.{field} is absent: an authorization must name what it authorises"
    if _require(authorization["operation_ref"], "authorization.operation_ref") != operation:
        return ("authorization.operation_ref does not name this operation: an approval for one "
                "operation is not an approval for another")
    _require(authorization["approved_by"], "authorization.approved_by")
    expires = _iso8601(authorization["expires_at"], "authorization.expires_at")
    if as_of is None:
        return ("as_of was not supplied, so the authorization's expiry cannot be evaluated and "
                "the destructive path fails closed")
    if as_of > expires:
        return f"the authorization expired at {expires} and as_of is {as_of}"
    return None


def _expiry_problem(policy: dict, as_of) -> str | None:
    expires = policy["expires_at"]
    if as_of is None:
        return ("as_of was not supplied, so policy.expires_at cannot be evaluated; the "
                "destructive path fails closed rather than treating an unreadable expiry as no "
                "expiry")
    if as_of > expires:
        return f"policy.expires_at is {expires} and as_of is {as_of}: an expired policy is refused"
    return None


def _unresolved_exceptions(policy: dict, vocabulary: dict) -> list[str]:
    out = []
    declared_by_policy = list(policy.get("protected_exceptions") or [])
    for ref, declaration in _exceptions(vocabulary).items():
        if ref not in declared_by_policy:
            out.append(f"{ref} is not declared by this policy")
        if not _is_bound(declaration):
            out.append(
                f"{ref} is {declaration.get('binding_state')} and has no stable selector, so it "
                "cannot be resolved against these objects")
    return out


def _plan_object(level, policy_scope, item, number, retry_budget, vocabulary,
                 exceptions=None) -> dict:
    if not isinstance(item, dict):
        raise Rejected(f"objects[{number}] is not an object")
    ref = _require(item.get("object_ref"), f"objects[{number}].object_ref")
    object_type = _require(item.get("object_type"), f"objects[{number}].object_type")
    if object_type not in _values(vocabulary, "object_type"):
        raise Rejected(f"objects[{number}].object_type is not a declared object type: "
                       f"{object_type!r}")

    observed = item.get("observed") or {}
    if not isinstance(observed, dict):
        raise Rejected(f"objects[{number}].observed must be an object")
    status = _declared(observed.get("status") or "NOT_ATTEMPTED", "observed_status", vocabulary,
                       f"objects[{number}].observed.status")
    receipt = observed.get("receipt")
    if receipt is not None:
        _require(receipt, f"objects[{number}].observed.receipt")

    blocked: list[str] = []
    object_scope = _scope(item.get("scope"), f"objects[{number}].scope")
    for kind, value in policy_scope.items():
        if object_scope.get(kind) != value:
            blocked.append(f"the object does not declare {kind}={value}, so it is outside the "
                           "policy scope")

    # A protected exception protects its objects at every level. Excluding the
    # exception's own audio from understanding would defeat the declaration just
    # as surely as deleting it, so this is checked before the level's own rules.
    for declaration in (exceptions or {}).values():
        if exception_applies(declaration, object_scope):
            blocked.append(f"protected exception {declaration['ref']} selects this object by "
                           "stable identifier, so no level may act on it")

    # The revision is checked at every level, isolation included: an isolation
    # record names the revision it isolated, and a plan written against a
    # revision that has since moved is wrong wherever it runs.
    revision, planned_revision = item.get("revision"), item.get("planned_revision")
    if revision is None or planned_revision is None:
        blocked.append("the current revision was not established")
    elif revision != planned_revision:
        blocked.append(f"the object is at revision {revision!r} and the plan named "
                       f"{planned_revision!r}: it changed after the plan was written")

    if level != "CONTENT_ISOLATION":
        ownership = item.get("ownership")
        if ownership != "OWNED":
            blocked.append(f"ownership is {ownership!r}: only an owned object with confirmed "
                           "permission may be acted on, and an unknown owner defers")
        if item.get("permission_confirmed") is not True:
            blocked.append("permission was not confirmed")
        holds = item.get("holds") or []
        if holds:
            blocked.append(f"a hold requires preservation: {sorted(map(str, holds))}")
        references = item.get("references") or []
        if references:
            blocked.append(f"{len(references)} reference(s) still name this object, so it is "
                           "preserved")
        if item.get("human_unique_edits") is True:
            blocked.append("the object carries human-unique edits: a human's own revision is "
                           "never silently rewritten")
        if level == "REBUILDABLE_ARTIFACT_CLEANUP" and item.get("rebuildable") is not True:
            blocked.append("the producer has not shown this object is rebuildable, and level B "
                           "clears only what can be regenerated")

    if status == "EXECUTED":
        intent = "ALREADY_EXECUTED"
    elif status == "RESTORED":
        intent = "REINDEX_REQUIRED"
    elif status == "PERMISSION_DENIED":
        intent = "BLOCKED_PERMISSION_DENIED"
    elif blocked:
        intent = "REFUSED"
    elif status == "FAILED" and retry_budget <= 0:
        intent = "SKIP"
    else:
        intent = {"CONTENT_ISOLATION": "ISOLATE",
                  "REBUILDABLE_ARTIFACT_CLEANUP": "CLEAN_REBUILDABLE"}.get(level, "DISPOSE")

    entry = {
        "object_ref": ref,
        "object_type": object_type,
        "intent": intent,
        "eligibility": "BLOCKED" if blocked else "ELIGIBLE",
        "blocked_by": blocked,
        "observed_status": status,
        "result": ({"status": status, "receipt": receipt} if status != "NOT_ATTEMPTED" else None),
        "receipt": receipt,
        "reason": [],
    }
    if intent == "ALREADY_EXECUTED":
        entry["reason"].append("read back as already executed; not retried")
    if intent == "REINDEX_REQUIRED":
        entry["reason"].append("the object was restored, so derived indexes must be rebuilt "
                               "and the candidate must not regrow")
    if intent == "BLOCKED_PERMISSION_DENIED":
        entry["reason"].append("the provider answered a permission denial; it is not retried "
                               "under another identity")
    if intent == "SKIP":
        entry["reason"].append("a failed attempt with no retry budget left: retrying is not free")
    if intent == "REFUSED":
        entry["reason"].extend(blocked)
    if not entry["reason"]:
        entry["reason"].append(f"{intent} under level {level}")
    return entry


def _cleanup_record(*, decision, level, policy, objects, reasons, deletability, recorded_only,
                    partial, already_executed=None, reindex_required=None, suppressed=None,
                    policy_scope=None) -> dict:
    if decision not in DECISIONS:
        raise AssertionError(f"unreachable decision: {decision}")
    _assert_reason(reasons)
    return {
        "schema": CLEANUP_SCHEMA,
        "decision": decision,
        "level": level,
        "policy_revision": (policy or {}).get("policy_revision"),
        "binding_state": (policy or {}).get("binding_state"),
        "scope": policy_scope or {},
        "axes": {"deletability": deletability},
        "objects": objects,
        "partial": partial,
        "already_executed": already_executed or [],
        "reindex_required": reindex_required or [],
        "suppress_candidate_regeneration": suppressed or [],
        "eligibility_inputs_ignored": recorded_only,
        "receipts": ("each acting object carries its own intent and, once the adapter has run "
                     "it, its own receipt"),
        "reason": reasons,
        "approval": ("explicit" if decision in ("REFUSAL", "PROPOSAL_REQUIRED", "UNSUPPORTED",
                                                "UNKNOWN") else "policy-approved"),
    }


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------

def decide(request: dict, vocabulary: dict | None = None) -> dict:
    """Answer a request document. See space/meeting-policy.schema.yaml."""
    if not isinstance(request, dict):
        raise Rejected("request must be an object")
    kind = request.get("kind")
    if kind == "cleanup":
        return plan_cleanup(request, vocabulary)
    if kind in (None, "relevance"):
        return classify(
            bundle=request.get("bundle"),
            policies=request.get("policies") or [],
            vocabulary=vocabulary,
            as_of=request.get("as_of"),
        )
    raise Rejected(f"unknown request kind: {kind!r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Decide a MeetingBundle's relevance and what may be done to it")
    ap.add_argument("--request", type=Path, help="JSON request document")
    ap.add_argument("--kind", choices=("relevance", "cleanup"),
                    help="override the request document's kind")
    ap.add_argument("--vocabulary", type=Path, help="override the vocabulary file")
    args = ap.parse_args()

    try:
        vocabulary = load_vocabulary(args.vocabulary)
        if args.request is None:
            raise Rejected("--request is required: this kernel answers an explicit document")
        request = json.loads(args.request.read_text(encoding="utf-8"))
        if args.kind:
            request = dict(request)
            request["kind"] = args.kind
        record = decide(request, vocabulary)
    except (OSError, json.JSONDecodeError, Rejected) as exc:
        print(json.dumps({"schema": SCHEMA, "decision": "REJECTED", "reason": [str(exc)]},
                         ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 2 if record["decision"] == "REJECTED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
