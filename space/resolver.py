#!/usr/bin/env python3
"""Decide where a logical object canonically belongs.

    python3 resolver.py --surface mydocs --object-type document \
        --semantic-role deliverable --project litellm
    python3 resolver.py --request request.json

This is the ServerOS **Space Resolver**. It is the placement counterpart of the
Context Capsule resolver: Context answers "what is my working environment",
Space answers "where does this object canonically belong, and does it need to
move".

What this module is
-------------------
A deterministic decision kernel over an explicit vocabulary. Given an object's
surface, object type and semantic role, plus its project/Item context, it returns
one of:

    KEEP | MOVE | INDEX | PROPOSAL_REQUIRED | REJECTED

What this module is NOT
-----------------------
It does not talk to Feishu, GitHub, a database, or a language model. It does not
scan a filesystem. It does not know what `lark-cli` is, and it must never learn:
API collection and application belong to the surface **adapters** that live in
`workspace-management`.

That separation is deliberate. An earlier placement implementation hardcoded
concrete cloud paths such as `feishu://wiki/<space>/项目/<project>/...` into the
control plane, which turned a deterministic kernel primitive into a policy engine
for one tenant's current folder layout. To make that mistake unrepresentable
rather than merely discouraged:

  * the canonical target is reported as a **logical** placement
    (surface + role + context), never as a cloud path;
  * `target_locator` stays null unless the caller supplies an explicit
    `target_template` in the policy — so a locator can only come from a policy a
    human wrote down, never from a pattern this module invented;
  * `tests/test_space_resolver.py` asserts that no network, process or Feishu
    dependency is imported here.

A rule that does not exist yields PROPOSAL_REQUIRED. Silence is never a licence
to move something.

Exit status: 0 for a decision, 2 for REJECTED or a malformed request. A decision
of PROPOSAL_REQUIRED is still a successful decision, so it exits 0.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SCHEMA = "serveros/space-decision.v1"
VOCABULARY_SCHEMA = "serveros/space-vocabulary.v1"

DECISIONS = ("KEEP", "MOVE", "INDEX", "PROPOSAL_REQUIRED", "REJECTED")

# A template may only interpolate these. Keeping the set closed means a policy
# cannot reach for a value the resolver never validated.
TEMPLATE_SLOTS = ("surface", "semantic_role", "project", "item", "run", "repo", "owner")

# Context slots the resolver is willing to carry into a logical target.
CONTEXT_SLOTS = ("project", "repo", "item", "run", "owner")


class Rejected(ValueError):
    """The request cannot be answered — as opposed to "no rule covers it"."""


def default_vocabulary_path() -> Path:
    return Path(__file__).resolve().parent / "vocabulary.json"


def load_vocabulary(path: Path | str | None = None) -> dict:
    source = Path(path) if path else default_vocabulary_path()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise Rejected(f"cannot read space vocabulary: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise Rejected(f"space vocabulary is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != VOCABULARY_SCHEMA:
        raise Rejected(f"unsupported space vocabulary schema: {data.get('schema')!r}")
    return data


def _values(vocabulary: dict, dimension: str) -> dict:
    return ((vocabulary.get("dimensions") or {}).get(dimension) or {}).get("values") or {}


def _rules(vocabulary: dict) -> dict[tuple[str, str], dict]:
    """Index the placement table, rejecting an ambiguous or malformed policy."""
    table: dict[tuple[str, str], dict] = {}
    for number, rule in enumerate((vocabulary.get("placement") or {}).get("rules") or [], 1):
        if not isinstance(rule, dict):
            raise Rejected(f"placement rule {number} is not an object")
        key = (rule.get("surface"), rule.get("role"))
        if not all(key):
            raise Rejected(f"placement rule {number} needs both surface and role")
        if key in table:
            raise Rejected(f"duplicate placement rule for {key[0]}/{key[1]}")
        table[key] = rule
    return table


def _require(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Rejected(f"{label} must be a non-empty string")
    return value.strip()


def _validate(surface: str, object_type: str, role: str, vocabulary: dict) -> None:
    surfaces = _values(vocabulary, "surface")
    types = _values(vocabulary, "object_type")
    roles = _values(vocabulary, "semantic_role")
    if surface not in surfaces:
        raise Rejected(f"unknown surface: {surface!r}")
    if object_type not in types:
        raise Rejected(f"unknown object_type: {object_type!r}")
    if role not in roles:
        raise Rejected(f"unknown semantic_role: {role!r}")
    allowed = (vocabulary.get("surface_object_types") or {}).get(surface) or []
    if object_type not in allowed:
        raise Rejected(f"{object_type!r} is not a valid object_type on surface {surface!r}")


def _logical_target(surface: str, role: str, context: dict) -> dict:
    """The surface-neutral answer: surface + role + the context that scopes it.

    Only context slots the caller actually supplied are included. A slot that is
    absent is absent — it is not filled with a placeholder, because a placeholder
    silently becomes a wrong locator downstream.
    """
    target = {"surface": surface, "semantic_role": role}
    for slot in CONTEXT_SLOTS:
        value = context.get(slot)
        if value is not None:
            target[slot] = value
    return target


def _render_locator(template: str, surface: str, role: str, context: dict) -> str | None:
    """Fill a caller-supplied template, or return None when a slot is missing.

    Returning None (rather than a half-filled string) is what makes
    PROPOSAL_REQUIRED honest: an incomplete identity produces no target at all.
    """
    rendered = template
    for slot in TEMPLATE_SLOTS:
        token = "{" + slot + "}"
        if token not in rendered:
            continue
        if slot == "surface":
            value = surface
        elif slot == "semantic_role":
            value = role
        else:
            value = context.get(slot)
        if value is None or value == "":
            return None
        rendered = rendered.replace(token, str(value))
    return rendered


def _decision(
    decision: str,
    *,
    canonical_surface: str | None,
    canonical_role: str,
    target_locator: str | None,
    logical_target: dict | None,
    reasons: list[str],
    confidence: str,
    approval: str,
    index: str | None = None,
) -> dict:
    if decision not in DECISIONS:
        raise AssertionError(f"unreachable decision: {decision}")
    return {
        "schema": SCHEMA,
        "decision": decision,
        "canonical_surface": canonical_surface,
        "canonical_role": canonical_role,
        "target_locator": target_locator,
        "logical_target": logical_target,
        "index": index,
        "reason": reasons,
        "confidence": confidence,
        "approval": approval,
    }


def resolve(
    *,
    surface: str,
    object_type: str,
    semantic_role: str,
    context: dict | None = None,
    observed: dict | None = None,
    policy: dict | None = None,
    vocabulary: dict | None = None,
) -> dict:
    """Return the placement decision record for one logical object.

    `observed` may carry `locator` and `indexed`. It is used only to decide
    between KEEP and INDEX, and to echo the current locator back; it is never
    used to infer a role or to invent a target.
    """
    vocab = vocabulary if vocabulary is not None else load_vocabulary()
    context = dict(context or {})
    observed = dict(observed or {})
    policy = dict(policy or {})

    surface = _require(surface, "surface")
    object_type = _require(object_type, "object_type")
    semantic_role = _require(semantic_role, "semantic_role")
    for slot in CONTEXT_SLOTS:
        if context.get(slot) is not None:
            context[slot] = _require(context[slot], slot)

    _validate(surface, object_type, semantic_role, vocab)

    rules = _rules(vocab)
    approval_mode = (vocab.get("approval") or {}).get("cross_surface", "proposal-only")

    # An undecided role is a question for a human or an Agent, not for a lookup
    # table. Answering it here would be the resolver guessing about meaning.
    if semantic_role == "unclassified":
        return _decision(
            "PROPOSAL_REQUIRED",
            canonical_surface=None,
            canonical_role=semantic_role,
            target_locator=None,
            logical_target=None,
            reasons=["semantic_role is unclassified: classify the object before placing it"],
            confidence="high",
            approval="explicit",
        )

    rule = rules.get((surface, semantic_role))
    if rule is None:
        return _decision(
            "PROPOSAL_REQUIRED",
            canonical_surface=None,
            canonical_role=semantic_role,
            target_locator=None,
            logical_target=None,
            reasons=[
                f"no placement rule for surface={surface} semantic_role={semantic_role}",
                "an absent rule is not permission to keep or to move",
            ],
            confidence="low",
            approval="explicit",
        )

    target_surface = rule.get("canonical_surface")
    strength = rule.get("strength", "default")
    index_kind = rule.get("index")

    if target_surface is None:
        return _decision(
            "PROPOSAL_REQUIRED",
            canonical_surface=None,
            canonical_role=semantic_role,
            target_locator=None,
            logical_target=None,
            reasons=[rule.get("note") or "policy declines to choose a canon for this role"],
            confidence="high",
            approval="explicit",
        )

    logical = _logical_target(target_surface, semantic_role, context)
    template = rule.get("target_template") or policy.get("target_template")
    locator = _render_locator(template, target_surface, semantic_role, context) if template else None
    if template and locator is None:
        return _decision(
            "PROPOSAL_REQUIRED",
            canonical_surface=target_surface,
            canonical_role=semantic_role,
            target_locator=None,
            logical_target=logical,
            reasons=[
                "target template needs context this request did not supply",
                "an incomplete identity produces no target",
            ],
            confidence="medium",
            approval="explicit",
        )

    if target_surface == surface:
        # Already canonical. The remaining question is whether the logical index
        # that makes it findable exists yet.
        if index_kind and observed.get("indexed") is False:
            return _decision(
                "INDEX",
                canonical_surface=target_surface,
                canonical_role=semantic_role,
                target_locator=locator or observed.get("locator"),
                logical_target=logical,
                reasons=[f"object is canonical but has no {index_kind} entry"],
                confidence="high",
                approval="none-required",
                index=index_kind,
            )
        return _decision(
            "KEEP",
            canonical_surface=target_surface,
            canonical_role=semantic_role,
            target_locator=locator or observed.get("locator"),
            logical_target=logical,
            reasons=[f"{surface} is the canonical surface for semantic_role={semantic_role}"],
            confidence="high" if strength == "canonical" else "medium",
            approval="none-required",
            index=index_kind,
        )

    return _decision(
        "MOVE",
        canonical_surface=target_surface,
        canonical_role=semantic_role,
        target_locator=locator,
        logical_target=logical,
        reasons=[
            f"semantic_role={semantic_role} is canonical on {target_surface}, not on {surface}",
            rule.get("note") or f"promote from {surface} to {target_surface}",
        ],
        confidence="high" if strength == "canonical" else "medium",
        approval=approval_mode,
        index=index_kind,
    )


def decide(request: dict, vocabulary: dict | None = None) -> dict:
    """Resolve a request document. See space/schema.yaml for its shape."""
    if not isinstance(request, dict):
        raise Rejected("request must be an object")
    obj = request.get("object")
    if not isinstance(obj, dict):
        raise Rejected("request.object must be an object")
    return resolve(
        surface=obj.get("surface"),
        object_type=obj.get("object_type"),
        semantic_role=obj.get("semantic_role"),
        context=request.get("context") or {},
        observed=request.get("observed") or {},
        policy=request.get("policy") or {},
        vocabulary=vocabulary,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resolve canonical placement for a logical object")
    ap.add_argument("--request", type=Path, help="JSON request document")
    ap.add_argument("--surface")
    ap.add_argument("--object-type")
    ap.add_argument("--semantic-role")
    ap.add_argument("--project")
    ap.add_argument("--repo")
    ap.add_argument("--item")
    ap.add_argument("--run")
    ap.add_argument("--owner")
    ap.add_argument("--observed-locator")
    ap.add_argument("--unindexed", action="store_true",
                    help="assert the logical index entry is absent (KEEP may become INDEX)")
    ap.add_argument("--target-template",
                    help="caller-supplied locator template; without one, no locator is produced")
    ap.add_argument("--vocabulary", type=Path, help="override the vocabulary file")
    args = ap.parse_args()

    try:
        vocabulary = load_vocabulary(args.vocabulary)
        if args.request:
            request = json.loads(args.request.read_text(encoding="utf-8"))
        else:
            request = {
                "object": {
                    "surface": args.surface,
                    "object_type": args.object_type,
                    "semantic_role": args.semantic_role,
                },
                "context": {
                    "project": args.project, "repo": args.repo,
                    "item": args.item, "run": args.run, "owner": args.owner,
                },
                "observed": {
                    "locator": args.observed_locator,
                    **({"indexed": False} if args.unindexed else {}),
                },
                "policy": {"target_template": args.target_template},
            }
        record = decide(request, vocabulary)
    except (OSError, json.JSONDecodeError, Rejected) as exc:
        print(json.dumps({"schema": SCHEMA, "decision": "REJECTED", "reason": [str(exc)]},
                         ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 2 if record["decision"] == "REJECTED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
