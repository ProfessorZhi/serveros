#!/usr/bin/env python3
"""SYS-02 · 动态环境 — desired 引用、附着、引用计数与释放裁决。

问题
----
*「这个资源现在被谁要着，最后一个人走了以后它该不该停？」*

这和这个产品里已有的三个问题都不同，而且每一次混淆都会产生一个错误答案：

    runtime/desired.py      §15  注册声明的运行态 vs 实测，同一个资源的两个状态
    runtime/health.py       四轴  liveness / transport / functional / readiness
    runtime/environment.py  这里  一个资源身上挂着几个引用，以及引用走了以后

`desired.py` 的 `desired` 是一个两词状态（`running` / `stopped`），来自注册；
本模块的 desired 是一个**引用**——「这个会话要浏览器」是另一种句子，靠状态词
表达不了，所以它需要自己的对象，而不是往 `Resource` 上加一个字段。

这个模块不碰文件系统、不碰时钟、不碰 Docker
--------------------------------------------
纯的。有文件系统的那一半在 `runtime/lease_store.py`，两者分开是为了让裁决能
在没有容器、没有守护进程、没有一个 fixture 的情况下被驱动到红——包括
`tests/test_environment.py` 里那四个必需反例。

三个不变量，每一个都是这个产品已经付过代价的那一类
--------------------------------------------------
**`None` 不是 `0`，`[]` 不是「没有」。** `references: None` 是「没人查过」，
`references: []` 是「查过，一个都没有」，`ref_count: 0` 只属于后者。把前者渲染
成 `0` 就是把一次没做的测量报成一次做了的测量，而这个产品里「静默兜底是缺陷」
写在任务书里。

**观测到的东西不会静默变成 desired。** §6 的原话。一个跑着但没人声明的资源是
`CANDIDATE`——被报出来，交给人问一句话——而不是被追认为 desired。反过来，
一个被声明但观测不到的资源是 `MISSING`，也不是「就当它在那儿」。

**「引用退出」不蕴含「资源该停」。** 这是 SYS-02 的必需反例，也是这个模块存在
的主要理由。shared 的浏览器、device-persistent 的环境、别人的资源：引用走了，
它们不走。裁决表在 `environment-contract.yaml#retention`，按顺序读，第一条命中
即生效——顺序本身就是策略，所以它写在契约里而不是写成 `if` 的排列。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
CONTRACT = _HERE / "environment-contract.yaml"

SCHEMA = "serveros/environment.v1"

# The verdicts, spelled once. `RETAIN` is not a fourth decision — it is the
# record of "there was nothing here to release", which is why it is kept apart
# from the three the ownership contract already owns.
ALLOW = "ALLOW"
REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
DENY = "DENY"
RETAIN = "RETAIN"

# The three reference states and the three attachment states, spelled once for
# the callers that compare against them. Read from the contract by a test, so a
# contract that gains a fourth segment is a red test rather than a dead word.
LIVE, EXPIRED, RETIRED = "LIVE", "EXPIRED", "RETIRED"
HELD, RELEASABLE, UNATTACHED = "HELD", "RELEASABLE", "UNATTACHED"


class Rejected(ValueError):
    """A request this module will not answer.

    A subclass of `ValueError` for the same reason
    `abilities/resolver.py#Rejected` is: the daemon's `handle` turns a handler
    exception into `{"ok": false, "error": {"code": "Rejected", ...}}`, so a
    refusal and a finding stay distinguishable on the envelope without this
    module knowing anything about envelopes.

    The distinction the docstring above keeps pressing: an unknown *scope* is a
    refusal (the vocabulary is closed and the caller can name a real one); an
    unknown *resource* is an answer (`UNATTACHED` — nothing is declared for it,
    and that is a fact about the node, not a fault in the request).
    """


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# `ownership_gate` is loaded under the SAME synthetic name the daemon and the
# CLI use. Two names would be two module objects, and the retention table below
# is checked against the gate's decision rows — a check that silently compares
# a module against itself is worse than no check.
ownership_gate = _load("serveros_runtime_ownership_gate", "ownership_gate.py")


def contract() -> dict:
    try:
        import yaml

        data = yaml.safe_load(CONTRACT.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise Rejected(f"{CONTRACT} could not be read: {exc}") from exc
    if not isinstance(data, dict):
        raise Rejected(f"{CONTRACT} must contain a mapping")
    return data


def scopes() -> tuple:
    """The five attachment scopes, in contract order (short-lived to external).

    Order is read, not restated: the axis is short-lived → long-lived →
    not-ours, and the release default walks along it. A second copy in Python
    would agree until somebody reordered one of them.
    """
    return tuple((contract().get("scopes") or {}).keys())


def sharing_modes() -> tuple:
    return tuple((contract().get("sharing") or {}).keys())


def verdicts() -> tuple:
    return tuple((contract().get("verdicts") or {}).keys())


def reference_states() -> tuple:
    return tuple((contract().get("reference_states") or {}).keys())


def attachment_states() -> tuple:
    return tuple((contract().get("attachment_states") or {}).keys())


def release_action() -> dict:
    return dict(contract().get("release_action") or {})


def retention_rules() -> list:
    return list(contract().get("retention") or [])


def _vocabulary(name: str, value, options: tuple, what: str) -> str:
    if not isinstance(value, str) or value not in options:
        raise Rejected(
            f"{what} {value!r} is not one of {list(options)}. A vocabulary that "
            f"accepts anything is not a vocabulary: the value would be stored, "
            f"read back, and compared against nothing")
    return value


def parse_scope(scope) -> str:
    return _vocabulary("scope", scope, scopes(), "the attachment scope")


def parse_sharing(sharing) -> str:
    return _vocabulary("sharing", sharing, sharing_modes(), "the sharing mode")


def parse_reference_state(state) -> str:
    return _vocabulary("state", state, reference_states(), "the reference state")


# ---------------------------------------------------------------------------
# 一个引用的生命
# ---------------------------------------------------------------------------

def reference_state(reference: dict, *, now: float) -> str:
    """LIVE, EXPIRED or RETIRED — from the reference's own record and the clock.

    `expires_at` is re-checked every time and never cached into a state word on
    disk. `runtime/agent_control_store.py` states the rule for its own records:
    durability is not authority, so a record loaded from disk is not re-verified
    by being loaded. The same applies here with more force, because this record
    is what a person reads to decide whether a resource is free.
    """
    if reference.get("retired_at"):
        return RETIRED
    expires_at = reference.get("expires_at")
    if expires_at is None:
        # No expiry is a DECLARATION, not a missing value: some references are
        # meant to last until their holder retires them. It is reported as such
        # by `expiry: "none"` on the row rather than by a sentinel far-future
        # date, which would be an expiry that never arrives dressed as one that
        # does.
        return LIVE
    try:
        deadline = float(expires_at)
    except (TypeError, ValueError):
        raise Rejected(
            f"reference {reference.get('holder')!r} carries expires_at "
            f"{expires_at!r}, which is not a number. A reference whose expiry "
            "cannot be evaluated would be reported LIVE forever by a default")
    return LIVE if now < deadline else EXPIRED


def _reference_row(reference: dict, *, now: float) -> dict:
    state = reference_state(reference, now=now)
    row = {
        "holder": reference.get("holder"),
        "scope": reference.get("scope"),
        "state": state,
        "declared_at": reference.get("declared_at"),
        "expires_at": reference.get("expires_at"),
        "expiry": "none" if reference.get("expires_at") is None else "at",
        "note": reference.get("note") or "",
    }
    if state == EXPIRED:
        # Reported, never dropped and never silently revived. A holder that
        # crashed left a record that says a resource is in use; the honest
        # reading is "this claim is no longer backed by anything", which is a
        # different sentence from "nothing was ever claimed".
        row["why"] = (
            "the reference is past its own expires_at. Its holder may have "
            "crashed, and this module cannot tell a crash from a slow holder — "
            "so it reports the claim as unbacked and leaves the decision to "
            "the release verdict rather than reviving it")
    return row


def attachment_state(reference_rows: list, *, attached) -> str:
    """HELD / RELEASABLE / UNATTACHED, derived rather than stored.

    A derived field that is persisted has the freedom to disagree with its own
    inputs, and the disagreement would be found by whoever reads the file
    second. So this is computed on every read.

    The two ways to reach RELEASABLE are not the same case and both are
    RELEASABLE rather than UNATTACHED:

      * an EXPIRED reference. Somebody declared this resource and their claim
        stopped being backed by anything. Calling that UNATTACHED would read the
        crashed holder's claim as though nobody had ever made one, which is the
        crash counterexample answered with silence.
      * `attached is None` — nobody observed it. "Not known to be gone" is not
        "gone", and the default that says otherwise is the one that deletes.

    So UNATTACHED is reachable only on a caller that looked and states it found
    nothing: no live reference, no unbacked claim, and an observation that the
    resource is not there.
    """
    if any(row["state"] == LIVE for row in reference_rows):
        return HELD
    if any(row["state"] == EXPIRED for row in reference_rows):
        return RELEASABLE
    if attached is False:
        return UNATTACHED
    return RELEASABLE


# ---------------------------------------------------------------------------
# 释放裁决 — SYS-02 的必需反例
# ---------------------------------------------------------------------------

def _rule_matches(rule: dict, *, sharing, scope, owner_class, protection) -> bool:
    """Whether one contract rule fires, evaluated rather than interpreted.

    `when:` is a small closed set of comparisons and NOT an expression language.
    A contract that could carry code would be a contract that can widen its own
    rules, which is the reason `ownership_gate.decisive_evidence()` is copied
    into its contract rather than read from `attribution-contract.yaml`.
    """
    when = (rule.get("when") or "").strip()
    table = {
        'sharing == "shared"': sharing == "shared",
        'scope == "device-persistent"': scope == "device-persistent",
        'scope == "external"': scope == "external",
        'owner_class == "UNKNOWN"': owner_class == "UNKNOWN",
        'owner_class == "EXTERNAL"': owner_class == "EXTERNAL",
        "protection is True": protection is True,
    }
    if when not in table:
        raise Rejected(
            f"retention rule {rule.get('code')!r} states `when: {when}`, which "
            f"this evaluator does not recognise. It is readable by a person and "
            f"inert to the program, which is the worst of both: the rule looks "
            f"enforced and is not")
    return table[when]


def release_verdict(*, sharing: str, scope: str, owner_class: str,
                    protection, reference_count: int | None,
                    owner_class_source: str = "caller") -> dict:
    """May this resource be released when its last reference exits?

    Every argument is a fact somebody else already established:

        scope, sharing      `parse_scope` / `parse_sharing` over what the
                            resource's own declaration says
        owner_class         `ownership_gate`'s vocabulary — UNKNOWN, EXTERNAL,
                            MANAGED — from decisive evidence or from its absence
        protection          the TRI-STATE `protection.resolve` returns. `None`
                            and `False` reach the same verdict and are NOT the
                            same record, because an undeclared protection
                            reported as unprotected is a bypass performed by a
                            default. It is carried through in `protection_declared`.

    `reference_count` is passed so that a release asked for while references
    remain is answered honestly rather than optimistically.

    `owner_class_source` records HOW the class was come by, because the verdict
    `OWNER_UNKNOWN` is reached both by "an attribution was attempted and
    nothing decisive named an owner" and by "no attribution was attempted at
    all". Those are the finding and the gap (§「源答了没有」vs「源答了什么」), and
    they reach the same DENY. The reason is carried rather than stamped into
    the code, so the vocabulary stays `ownership_gate`'s — which makes the same
    distinction in its own words at the point where evidence would have been
    offered ("no evidence was offered at all").
    """
    owner_class = owner_class or "UNKNOWN"
    if owner_class not in ("UNKNOWN", "EXTERNAL", "MANAGED"):
        raise Rejected(
            f"owner class {owner_class!r} is not one of ['UNKNOWN', 'EXTERNAL', "
            "'MANAGED']. The classes are `ownership_gate`'s and this module does "
            "not add a fourth")

    fired = None
    for rule in retention_rules():
        if _rule_matches(rule, sharing=sharing, scope=scope,
                         owner_class=owner_class, protection=protection):
            fired = rule
            break

    if fired is not None:
        verdict = fired["verdict"]
        code = fired.get("code")
        because = _collapse(fired.get("because"))
        source = "environment-contract.yaml#retention"
    else:
        # Nothing in the retention table fired. Ownership has not refused and
        # protection is not declared, so the answer is the ownership contract's
        # own row for this action kind — READ from it, not restated. Reading is
        # what keeps one rule in one place: if the table changes, this changes.
        verdict, code, because, source = _managed_row(
            owner_class=owner_class, protection=protection)

    live_note = None
    if reference_count:
        # A release asked for while references remain is not a refusal — a
        # holder may legitimately hand a reference back early. It is recorded,
        # because "released with two references still live" is the shape of a
        # mistake that a later reader would otherwise have to reconstruct.
        live_note = (
            f"{reference_count} live reference(s) remain. Releasing now detaches "
            "something other holders still declare they need")

    return {
        "verdict": verdict,
        "code": code,
        "because": because,
        "rule_from": source,
        "scope": scope,
        "sharing": sharing,
        "owner_class": owner_class,
        "owner_class_source": owner_class_source,
        "protection": {"protected": protection, "declared": protection is not None},
        "reference_count": reference_count,
        "note": live_note,
    }


def _collapse(text) -> str:
    """YAML folded scalars carry the source's line breaks. A reason printed
    with them reads as several sentences that are not there."""
    return " ".join(str(text or "").split())


def _managed_row(*, owner_class: str, protection) -> tuple:
    """The ownership contract's row for MANAGED + this protection + destructive.

    Read from `decision_table` so that the code and the decision cannot drift
    from `ownership_gate.decide`. Which row applies is decided by the SAME
    comparisons the table is written in, and a test asserts the answer matches
    what the gate returns for the equivalent facts.
    """
    rows = ownership_gate.contract().get("decision_table") or []
    wanted_protection = "protected" if protection is True else "not_protected"
    for row in rows:
        if (row.get("owner_class") == "MANAGED"
                and row.get("protection") == wanted_protection
                and row.get("action") == release_action().get("kind")):
            return (row.get("decision"), row.get("code"), _collapse(row.get("because")),
                    "runtime/ownership-contract.yaml#decision_table")
    raise Rejected(
        f"runtime/ownership-contract.yaml's decision_table has no row for "
        f"MANAGED / {wanted_protection} / {release_action().get('kind')}. This "
        "module will not invent a verdict for a combination the table does not "
        "cover: a missing row is a gap in the contract, not a licence")


# ---------------------------------------------------------------------------
# 有效环境 — §6 的等式
# ---------------------------------------------------------------------------

def effective(*, desired, observed, ownership, policy, context,
              now: float, attached=None) -> dict:
    """Desired + Observed + Ownership/Policy + Context, and the two asymmetries.

    `desired` is a mapping of resource to its declaration:

        {"browser:chromium": {"sharing": "shared", "scope": "device-persistent",
                              "references": [{...}, ...]}}

    `references: None` says nobody looked, and survives as `None` all the way to
    the answer. That is the whole reason this takes a mapping rather than a list
    of references: the difference between "no references" and "no evidence about
    references" is the difference between two opposite decisions.

    `observed`, `ownership`, `policy` and `context` are the other three terms,
    each produced by the module that owns it — this function composes, it does
    not measure.
    """
    rows = []
    candidates = []
    for resource, declaration in sorted((desired or {}).items()):
        declaration = declaration or {}
        scope = parse_scope(declaration.get("scope"))
        sharing = parse_sharing(declaration.get("sharing"))
        references = declaration.get("references")
        if references is None:
            # Not measured. Every derived field is `None` rather than a zero or
            # an empty: `ref_count: 0` and `attachment_state: UNATTACHED` would
            # both be claims about a measurement that did not happen.
            rows.append({
                "resource": resource,
                "scope": scope,
                "sharing": sharing,
                "references": None,
                "ref_count": None,
                "live_count": None,
                "attachment_state": None,
                "release": None,
                # `None`, never `False`. `False` is the claim "this resource is
                # not releasable", which is a verdict, and no verdict was
                # reached here because no reference count was established.
                "releasable": None,
                "note": ("the declaration was read and its references were not. "
                         "Nothing here says the resource is free"),
            })
            continue

        reference_rows = [_reference_row(r, now=now) for r in references]
        attached_here = attached if attached is not None else _attached(observed, resource)
        state = attachment_state(reference_rows, attached=attached_here)
        live_count = sum(1 for row in reference_rows if row["state"] == LIVE)
        owner_class, owner_source = _owner_class(resource, ownership)
        verdict = release_verdict(
            sharing=sharing, scope=scope,
            owner_class=owner_class,
            protection=(policy or {}).get(resource, {}).get("protected"),
            reference_count=live_count, owner_class_source=owner_source)
        rows.append({
            "resource": resource,
            "scope": scope,
            "sharing": sharing,
            "references": reference_rows,
            # `ref_count` is the number of DISTINCT holders with a LIVE
            # reference, which is the number a "last reference exits" question
            # is about. The rows are kept beside it so the count can be audited
            # rather than trusted.
            "ref_count": len({(row["scope"], row["holder"]) for row in reference_rows
                              if row["state"] == LIVE}),
            "live_count": live_count,
            "expired_count": sum(1 for row in reference_rows
                                 if row["state"] == EXPIRED),
            "attachment_state": state,
            "attached": attached_here,
            "release": verdict,
            "releasable": state == RELEASABLE and verdict["verdict"] == ALLOW,
            "note": _attachment_note(state, verdict, live_count, attached_here),
        })

    for resource in sorted((observed or {})):
        if resource not in (desired or {}):
            # Observed, and nobody declared it. §6: "Observed must never
            # silently become Desired". It becomes a CANDIDATE — a question for
            # a person — and it does NOT enter the environment above, because an
            # entry there is a declaration and this is not one.
            candidates.append({
                "resource": resource,
                "observed": (observed or {}).get(resource),
                "state": "CANDIDATE",
                "why": ("it is present and nothing declares it. Reporting it as "
                        "desired would let 'we found it running' become 'we "
                        "decided it should run', which is the one substitution "
                        "§6 forbids"),
                "asks": "declare it, or leave it alone",
            })

    return {
        "schema": SCHEMA,
        "resources": rows,
        "counts": {
            "declared": len(rows),
            "held": sum(1 for row in rows if row["attachment_state"] == HELD),
            "releasable": sum(1 for row in rows if row.get("releasable")),
            # A row whose references were never measured is counted here, apart
            # from every other count. A reader that sees `releasable: 0` without
            # seeing `unmeasured: 3` has been told the node is free when three
            # resources were never asked about.
            "unmeasured": sum(1 for row in rows
                              if row["attachment_state"] is None),
            # References whose holder stopped backing them and that nobody has
            # resolved. Counted on its own for the same reason: a summary that
            # showed only `held: 0` would report an idle node while a crashed
            # holder's claim sat in the record.
            "orphaned": sum((row.get("expired_count") or 0) for row in rows),
            "candidates": len(candidates),
        },
        "candidates": candidates,
        "context": context or {},
        "as_of": now,
    }


def _owner_class(resource: str, ownership) -> tuple:
    """This resource's owner class, and how it was come by.

    Three cases, and the third is why this is a function rather than a
    `.get(...).get(...) or "UNKNOWN"`:

        ownership falsy            -> (None, "not_gathered")
        no entry for this resource -> (None, "not_attributed")
        an entry naming a class    -> (that class, "gathered")

    All three end at `UNKNOWN` inside `release_verdict`, which is the gate's
    own fallback and the conservative direction. Only the first two are gaps
    rather than findings, and a reader that cannot tell them apart will go
    looking for an attribution problem on a node where nobody attributed
    anything — the reverse of the failure the tri-state `protection` guards
    against, and the same defect.
    """
    if not ownership:
        return None, "not_gathered"
    entry = (ownership or {}).get(resource)
    if isinstance(entry, dict) and entry.get("class"):
        return entry["class"], "gathered"
    return None, "not_attributed"


def _attached(observed, resource):
    """Whether the environment actually holds this resource, from observation.

    Three-valued on purpose: `True`, `False`, or `None` when nothing observed
    it. `None` is not `False` — an unobserved resource whose references have all
    exited is RELEASABLE, and the alternative ("nobody looked, so it must be
    gone") is the default value that deletes things.
    """
    if not observed:
        return None
    entry = observed.get(resource)
    if entry is None:
        return None
    if isinstance(entry, dict):
        return entry.get("attached")
    return bool(entry)


def _attachment_note(state: str, verdict: dict, live_count: int, attached) -> str:
    if state is None:
        return "not measured"
    if state == HELD:
        return f"{live_count} live reference(s) hold it"
    if state == UNATTACHED:
        return "no references and nothing attached"
    verdict_word = verdict["verdict"]
    if verdict_word == RETAIN:
        return ("the last reference exited and the resource stays: "
                f"{verdict['code']}")
    if verdict_word == ALLOW:
        return "the last reference exited and nothing retains it; it may be released"
    return (f"the last reference exited and the release is {verdict_word} "
            f"({verdict['code']}); the resource stays until something decides")
