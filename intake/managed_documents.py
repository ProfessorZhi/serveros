"""The maintenance contracts we hold, read as contracts rather than as prose.

WHY THIS MODULE EXISTS
----------------------
`docs/CC-EXECUTION-PLAN.md` P4 draws the line itself: **「文档 identity resolver
存在不等于远端 publisher 已闭环」**. `documents/document_governance.py` answers
what a document *is* — its kind, its scope, whether a given ref is the authority
or a copy of one — and it knows nothing about maintenance. The maintenance half
is the B9 ability `document-stewardship`: a registry of managed documents, one
profile per document saying who it is for, what register each section is in, how
much risk a change carries and where a fact has to be checked before it may be
written. On 2026-09-25 that half existed, complete for one real document, and
**nothing in ServerOS had ever read it**. `command grep -rn "managed\\|registry"
documents/document_governance.py` returns nothing: the two halves had never met.

They also could not have met by accident, because the one real instance is broken
in two ways at once and neither is visible from either half alone. Its profile
carries eight derived-state fields under a doctrine that keeps state out of the
contract (`profile（Git）= 稳定契约；state（非 Git）= 派生状态`), so both of the
ability's own verbs refuse it with `契约已腐坏`; and its sections carry no `key`,
which the same verbs index on, so the refusal is the only thing standing between
the reader and a `KeyError`. The refusal is correct and the profile underneath it
is not, and a surface that reported only "refused" would leave a reader unable to
tell a corrupt contract from a working one.

WHAT IT REFUSES TO DO
---------------------
It does not carry its own copy of the ability's rules. The write gate
(`authorize_edit`), the protected-content guard, the dynamic-field check and the
section deriver are loaded from the ability's own script, by a path the caller
states — never searched for, never guessed. Re-stating a fail-closed gate here
would let the two disagree, and a fail-closed gate that disagrees with itself
means one of the two says `allow` while the other says `blocked`; the one that
says `allow` is the one that writes. The path is an argument for the same reason
the registry is: this module reads records a caller names.

It does not let the ability's crash stand in for its answer. The ability indexes
its sections by `key`, so a profile without one makes the gate raise rather than
refuse. ServerOS checks the contract **before** calling in, and reports what it
found: a caller that routes a contract it cannot check has no answer, and an
exception is not an answer.

It does not derive the delivery form from a word of its own. `OWNED` /
`FOLLOW_MIRROR` / `EXTERNAL_MAINTAINED` are the plan's three, and the object's
own declaration of which one it is lives in the registry entry, where the
registry's own header says desired state lives. What this module does is check
that declaration against the facts the profile carries, because the one mistake
the document vocabulary calls out by name is reading a copy as the authority:
「把 DERIVATIVE 读成 CANONICAL，会让一份副本被当成权威引用出去」. A `FOLLOW_MIRROR`
whose profile says `standing: CANONICAL` is reported as `INCONSISTENT` with the
two statements quoted, never resolved by preferring one.

It does not count a document twice. Two registry entries materialising one
logical object — the pair of `Codex 配置教程` copies, one in a private space and
one in a shared knowledge base — are grouped and reported as **one object with
two materializations**. Reporting them as two objects is how one of them becomes
a second authority, which is the outcome the invariants forbid.

It does not call a missing declaration a default. A registry entry with no
`relation`, a profile with no `body_authority`, a change class with no
`edit_policy` tier — each is `UNDECLARED` with the field named. There is no
fallback value, because a fallback would make "nobody decided" and "somebody
decided this" the same reading.

It does not read a clock, send anything, or write anything. Drift in the sense
of "the document moved since the last observation" needs a fetch and belongs to
the ability; what this module reports about state is only what the state file
already says, and whether the profile's sections and the state's derived
sections describe the same document.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCHEMA = "serveros/managed-documents.v1"

# The plan's three delivery forms. They are the vocabulary this module *checks*;
# the value itself is declared per object in the registry.
DELIVERY_FORMS = ("OWNED", "FOLLOW_MIRROR", "EXTERNAL_MAINTAINED")

# Whether the object's body is authoritative where we found it. Copied from
# `documents/document-vocabulary.json#dimensions.standing` rather than imported:
# that file is DATA and is read by the governance kernel, which owns it, and a
# second reader importing it would make this module stop working the day the
# kernel's loader changes. The two are cross-checked by a test.
STANDING = ("CANONICAL", "DERIVATIVE")

# How the local materialization follows its authority. `SOURCE_REVISION` is the
# README's own words for the guide case (「按 source revision 更新允许区块」);
# `SNAPSHOT` is the capture case. `NONE` is not a fourth policy but the honest
# statement that nothing follows automatically — without it, "maintained by hand"
# would have to be written as the absence of a value, which is the same as
# "nobody said".
SYNC_MODES = ("SOURCE_REVISION", "SNAPSHOT", "NONE")

# The registry's own hard rules, in the registry's own order.
#   1. status 必须显式声明 —— 缺失即 unknown，不得默认成 managed
#   2. managed 必须有 profile —— 没有契约就不能改
#   3. 只存维护规则，绝不存正文副本
STATUSES = ("managed",)

# What one entry's contract reads as. `CHECKED` is the only state in which the
# write gate may be consulted at all.
CONTRACT_STATES = (
    "CHECKED",
    "REGISTRY_UNREADABLE",
    "STATUS_UNDECLARED",
    "STATUS_UNKNOWN",
    "PROFILE_MISSING",
    "PROFILE_UNREADABLE",
    "PROFILE_ID_MISMATCH",
    "DYNAMIC_FIELDS_IN_PROFILE",
    "SECTION_WITHOUT_KEY",
    "ABILITY_NOT_FOUND",
)

# How the declared delivery form sits against the facts the profile carries.
DELIVERY_STATES = ("CONSISTENT", "UNDECLARED", "INCONSISTENT")

# A change class with no `edit_policy` tier. The ability names its own three;
# copied because the profile is written against them and a profile author has to
# be able to read the list without loading code.
CHANGE_CLASSES = ("PATCH", "REFINE", "RESTRUCTURE")


class Refused(Exception):
    """The request itself cannot be answered — never a verdict about a document."""


class Malformed(Exception):
    """A document that had to be an object was not one."""


def load_ability(script: Path):
    """The ability's own module, loaded from the path the caller states.

    A missing script is `ABILITY_NOT_FOUND` and not an exception: the whole point
    of loading rather than re-stating is that the ability is the authority on its
    own contract, so "the authority is not here" is an answer about this reading
    rather than a reason to fall back on a copy of its rules.
    """
    script = Path(script)
    if not script.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "serveros_intake_managed_documents_ability", script)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_registry(path: Path) -> dict:
    """One registry document, or a refusal that says which file was opened."""
    path = Path(path)
    if not path.is_file():
        raise Refused(f"registry not found: {path}")
    try:
        import yaml
    except ImportError as exc:                      # pragma: no cover - env fault
        raise Refused(f"no YAML reader available: {exc}") from exc
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        # The parser's own words and position, never `str(exc)`: that string is
        # the message plus a boxed copy of the document, and a report is not a
        # place to reprint the document. Same rule as §9.21.
        problem = getattr(exc, "problem", None) or "unparseable"
        mark = getattr(exc, "problem_mark", None)
        where = f" line {mark.line + 1} column {mark.column + 1}" if mark else ""
        raise Refused(f"registry does not parse:{where} {problem}") from exc
    if document is None:
        raise Malformed(f"registry is empty: {path}")
    if not isinstance(document, dict):
        raise Malformed(f"registry is a {type(document).__name__}, not a document: {path}")
    return document


def read_profile(registry_path: Path, relative: str) -> tuple[dict | None, str | None]:
    """One profile, and the reason it could not be read when it could not.

    Returns `(profile, None)` or `(None, reason)`. A profile that is missing, is
    not a mapping, or does not parse are three different facts, and the caller
    reports which one — a single `None` would make "the file is gone" and "the
    file is not a contract" the same reading.
    """
    path = (Path(registry_path).parent / relative)
    if not path.is_file():
        return None, f"profile not found: {relative}"
    try:
        import yaml
    except ImportError as exc:                      # pragma: no cover - env fault
        return None, f"no YAML reader available: {exc}"
    try:
        profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        problem = getattr(exc, "problem", None) or "unparseable"
        mark = getattr(exc, "problem_mark", None)
        where = f" line {mark.line + 1} column {mark.column + 1}" if mark else ""
        return None, f"profile does not parse:{where} {problem}"
    if profile is None:
        return None, f"profile is empty: {relative}"
    if not isinstance(profile, dict):
        return None, f"profile is a {type(profile).__name__}, not a contract: {relative}"
    return profile, None


def read_state(state_dir: Path, document_id: str) -> dict | None:
    """The state half, or `None` when there is none.

    `None` here is the absence of an observation and is reported as such; it is
    not an empty state. The ability writes state only after a successful fetch,
    so a missing state file means this document has never been observed from
    this node, which is a different thing from having been observed and found
    unchanged.
    """
    path = Path(state_dir) / f"{document_id}.json"
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def contract_health(profile: dict, ability) -> tuple[str, list[str]]:
    """Whether this profile is a contract the write gate can be asked about.

    The checks run **before** the ability is called, in the order in which a
    failure makes the later ones meaningless. Returns `(state, reasons)`; the
    reasons are the field paths, because "the contract is corrupt" without
    naming what is corrupt sends the reader back to the file to guess.
    """
    if ability is None:
        return "ABILITY_NOT_FOUND", []
    drift = list(ability.dynamic_state_fields(profile))
    if drift:
        return "DYNAMIC_FIELDS_IN_PROFILE", drift
    sections = profile.get("sections")
    if not isinstance(sections, list):
        return "SECTION_WITHOUT_KEY", ["sections"]
    missing = [f"sections[{i}]" for i, section in enumerate(sections)
               if not isinstance(section, dict) or not section.get("key")]
    if missing:
        return "SECTION_WITHOUT_KEY", missing
    return "CHECKED", []


def read_surfaces(vocabulary_path: Path) -> tuple[str, ...]:
    """The closed set of surfaces a body authority may live on.

    Read from `space/vocabulary.json` rather than copied: `document-vocabulary
    .json` already makes the point that the document kernel and the space
    resolver must not name one document's surface two ways, and a third copy
    here would be a third way. An unreadable vocabulary yields no values, which
    makes every declared surface `UNDECLARED` — the closed-set doctrine, applied
    to the file that holds the set.
    """
    try:
        document = json.loads(Path(vocabulary_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    values = (((document.get("dimensions") or {}).get("surface") or {})
              .get("values") or {})
    return tuple(sorted(values)) if isinstance(values, dict) else ()


def read_delivery(entry: dict, profile: dict,
                  surfaces: tuple[str, ...]) -> dict:
    """The declared delivery form, checked against what the profile says.

    Declared per object in the registry, because that is the file whose header
    says it holds desired state. Checked here because a declaration nobody
    audits is a label, and the label that matters most is the one that can be
    wrong in the dangerous direction: a copy calling itself the authority.
    """
    declared = entry.get("relation")
    body_authority = profile.get("body_authority")
    standing = profile.get("standing")
    sync_mode = profile.get("sync_mode")
    missing = [name for name, value in (("body_authority", body_authority),
                                        ("sync_mode", sync_mode))
               if value in (None, "")]
    out = {"declared": declared, "body_authority": body_authority,
           "standing": standing, "sync_mode": sync_mode, "missing": missing}

    if declared is None:
        return {**out, "form": None, "state": "UNDECLARED",
                "why": "registry entry declares no relation, so which of the "
                       "three delivery forms governs this object was never "
                       "decided — a default here would make 'nobody decided' "
                       "read as 'somebody decided this'"}
    if declared not in DELIVERY_FORMS:
        return {**out, "form": declared, "state": "INCONSISTENT",
                "why": f"relation {declared!r} is not one of {list(DELIVERY_FORMS)}"}
    if standing is not None and standing not in STANDING:
        return {**out, "form": declared, "state": "INCONSISTENT",
                "why": f"standing {standing!r} is not one of {list(STANDING)}; "
                       f"the two values decide whether a copy may be cited as "
                       f"the original, so an unlisted one is refused rather "
                       f"than defaulted"}
    if sync_mode is not None and sync_mode not in SYNC_MODES:
        return {**out, "form": declared, "state": "INCONSISTENT",
                "why": f"sync_mode {sync_mode!r} is not one of {list(SYNC_MODES)}"}
    if isinstance(body_authority, dict):
        surface = body_authority.get("surface")
        if surface not in surfaces:
            return {**out, "form": declared, "state": "INCONSISTENT",
                    "why": f"body_authority.surface {surface!r} is not one of "
                           f"{list(surfaces)} — an undeclared surface is refused "
                           f"rather than defaulted, because 'the authority lives "
                           f"somewhere we have no name for' writes a copy into "
                           f"that nameless place"}
        if not body_authority.get("locator"):
            return {**out, "form": declared, "state": "UNDECLARED",
                    "why": "body_authority names a surface but no locator: the "
                           "surface says where such an object lives, and only a "
                           "locator says which one"}
    if declared == "FOLLOW_MIRROR":
        # The dangerous direction. A mirror's whole reason to carry `copied_from`
        # is that its body is not the authority, and `standing: CANONICAL` says
        # it is — the vocabulary file names this exact misreading as the one that
        # makes a copy get cited as the original.
        if standing == "CANONICAL":
            return {**out, "form": declared, "state": "INCONSISTENT",
                    "why": "relation is FOLLOW_MIRROR and the profile says "
                           "standing: CANONICAL — a mirror whose body is the "
                           "authority is not a mirror, and the two statements "
                           "cannot both be true"}
        if not profile.get("copied_from"):
            return {**out, "form": declared, "state": "UNDECLARED",
                    "why": "relation is FOLLOW_MIRROR and the profile names no "
                           "copied_from: a mirror that does not say what it "
                           "mirrors cannot be checked against its source"}
    if missing:
        return {**out, "form": declared, "state": "UNDECLARED",
                "why": f"profile declares no {', '.join(missing)}"}
    return {**out, "form": declared, "state": "CONSISTENT",
            "why": f"relation {declared} agrees with the profile"}


def read_write_policy(profile: dict) -> dict:
    """The tier each change class lands on, per class, with the gaps named."""
    declared = profile.get("edit_policy") or {}
    tiers = {name: declared.get(key) for name, key in
             (("PATCH", "factual_patch"), ("REFINE", "section_refactor"),
              ("RESTRUCTURE", "structural_rewrite"))}
    return {"tiers": tiers,
            "undeclared": [name for name, tier in tiers.items() if tier is None],
            "sections": [{"key": section.get("key"),
                          "register": section.get("register"),
                          "change_risk": section.get("change_risk"),
                          "require_fact_verification": bool(
                              section.get("require_fact_verification"))}
                         for section in profile.get("sections") or []
                         if isinstance(section, dict)]}


def read_reach(profile: dict, state: dict | None) -> dict:
    """Whether the profile's sections and the state's derived ones agree.

    Two facts about one document, and they are allowed to disagree: a section
    can be added to the profile before the next fetch, and a heading that was
    renamed shows up as derived-without-declaration. What is not allowed is for
    the disagreement to be invisible, because `unresolved_section_keys` is the
    field that says which declared sections the deriver could not locate — and
    that field is only meaningful against the declared set.
    """
    declared = [section.get("key") for section in profile.get("sections") or []
                if isinstance(section, dict) and section.get("key")]
    derived: list[str] = []
    unresolved: list[str] = []
    revision = None
    observed_at = None
    if state is not None:
        derived = sorted((((state.get("derived") or {}).get("sections")) or {}).keys())
        unresolved = list(state.get("unresolved_section_keys") or [])
        revision = state.get("revision")
        observed_at = state.get("last_observed_at")
    return {
        "state_present": state is not None,
        "state_revision": revision,
        "state_last_observed_at": observed_at,
        "declared": sorted(declared),
        "derived": derived,
        "declared_never_derived": sorted(set(declared) - set(derived)),
        "derived_never_declared": sorted(set(derived) - set(declared)),
        "unresolved_section_keys": unresolved,
    }


def read_root(registry_path: Path, state_dir: Path, ability_script: Path,
              vocabulary_path: Path) -> dict:
    """Every managed document, each with its contract and what state says.

    One entry per **logical object**, not per registry row: entries naming the
    same locator are grouped, because two rows materialising one document is the
    state in which a second editable body gets created.
    """
    ability = load_ability(ability_script)
    surfaces = read_surfaces(vocabulary_path)
    try:
        registry = read_registry(registry_path)
    except Refused as exc:
        return {"schema": SCHEMA, "registry": str(registry_path),
                "state_dir": str(state_dir), "ability": str(ability_script),
                "ability_loaded": ability is not None,
                "surfaces": list(surfaces),
                "contract": "REGISTRY_UNREADABLE", "why": str(exc),
                "documents": [], "objects": []}

    rows = registry.get("documents")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise Malformed(f"registry `documents` is a {type(rows).__name__}, not a list")

    documents = []
    for row in rows:
        if not isinstance(row, dict):
            raise Malformed("a registry entry is not a mapping")
        document_id = row.get("id")
        status = row.get("status")
        entry = {"id": document_id, "title": row.get("title"),
                 "locator": row.get("locator"), "status": status,
                 "profile": row.get("profile"),
                 "snapshot_watch": row.get("snapshot_watch")}

        if status is None:
            # Rule 1: 缺失即 unknown，不得默认成 managed.
            documents.append({**entry, "contract": "STATUS_UNDECLARED",
                              "reasons": [], "delivery": None})
            continue
        if status not in STATUSES:
            documents.append({**entry, "contract": "STATUS_UNKNOWN",
                              "reasons": [f"status={status!r} is not one of "
                                          f"{list(STATUSES)}"],
                              "delivery": None})
            continue
        if not row.get("profile"):
            # Rule 2: managed 必须有 profile —— 没有契约就不能改.
            documents.append({**entry, "contract": "PROFILE_MISSING",
                              "reasons": ["profile"], "delivery": None})
            continue

        profile, why = read_profile(registry_path, row["profile"])
        if profile is None:
            documents.append({**entry, "contract": "PROFILE_UNREADABLE",
                              "reasons": [why], "delivery": None})
            continue
        if profile.get("id") != document_id:
            documents.append({**entry, "contract": "PROFILE_ID_MISMATCH",
                              "reasons": [f"registry id={document_id!r} but "
                                          f"profile id={profile.get('id')!r}"],
                              "delivery": None})
            continue

        state, reasons = contract_health(profile, ability)
        documents.append({
            **entry,
            "contract": state,
            "reasons": reasons,
            "delivery": read_delivery(row, profile, surfaces),
            "write_policy": read_write_policy(profile),
            "reach": read_reach(profile, read_state(state_dir, document_id)),
            "audience": profile.get("audience"),
            "copied_from": profile.get("copied_from"),
            "owner": profile.get("owner"),
            "maintainers": profile.get("maintainers"),
            "sources_of_truth": profile.get("sources_of_truth"),
            "relations": profile.get("relations"),
            "unresolved_locators": profile.get("unresolved_locators"),
        })

    # One logical object, however many rows materialise it. Grouped by locator
    # and reported together, because the alternative reading — two objects that
    # happen to share a locator — is how one copy becomes a second authority.
    grouped: dict[str, list[str]] = {}
    for entry in documents:
        grouped.setdefault(entry.get("locator") or f"(no locator: {entry['id']})",
                           []).append(entry["id"])
    objects = [{"locator": locator, "documents": ids,
                "materializations": len(ids)}
               for locator, ids in sorted(grouped.items())]

    return {"schema": SCHEMA, "registry": str(registry_path),
            "state_dir": str(state_dir), "ability": str(ability_script),
            "ability_loaded": ability is not None,
            "surfaces": list(surfaces),
            "contract": "READ", "why": None,
            "documents": documents, "objects": objects}


def gate(ability, profile: dict, section_key: str, change_class: str, *,
         authority=None, original_text: str = "", proposed_text: str = "") -> dict:
    """Ask the ability whether one edit is allowed. `ABILITY_NOT_FOUND` refuses.

    The refusal is produced here rather than by returning a decision, because
    the four decisions the gate can return are all verdicts **about an edit**,
    and "there is no gate to ask" is not one of them. Collapsing the two would
    let a missing ability read as a `blocked` the gate itself decided.
    """
    if ability is None:
        return {"decision": "blocked",
                "reason": "ABILITY_NOT_FOUND: the write gate is not on this node, "
                          "so no edit is authorized and none may be assumed"}
    if change_class not in CHANGE_CLASSES:
        return {"decision": "blocked",
                "reason": f"unknown_change_class: {change_class!r} is not one of "
                          f"{list(CHANGE_CLASSES)}"}
    return ability.authorize_edit(profile, section_key, change_class,
                                  authority=authority,
                                  original_text=original_text,
                                  proposed_text=proposed_text)


def render(report: dict) -> list[str]:
    """The reading as lines. Every refusal carries its reasons on the same line."""
    if report["contract"] == "REGISTRY_UNREADABLE":
        return [f"registry    {report['registry']}",
                f"能力        {'已加载' if report['ability_loaded'] else '未找到'}",
                f"✗ 读不了：{report['why']}"]

    lines = [f"registry    {report['registry']}",
             f"state       {report['state_dir']}",
             f"能力        {'已加载' if report['ability_loaded'] else '未找到'} "
             f"（{report['ability']}）",
             ""]
    for entry in report["documents"]:
        state = entry["contract"]
        where = entry.get("locator") or "（无 locator）"
        lines.append(f"{entry['id']}  {state}  {where}")
        if state != "CHECKED":
            # The reasons, always: "契约腐坏" without them sends the reader back
            # to the file to guess which field.
            for reason in entry.get("reasons") or []:
                lines.append(f"    ✗ {reason}")
            lines.append("")
            continue
        delivery = entry["delivery"] or {}
        lines.append(f"    交付        {delivery.get('form') or '未声明'}"
                     f"（{delivery.get('state')}）")
        lines.append(f"    正文权威    {delivery.get('body_authority') or '未声明'}")
        lines.append(f"    同步        {delivery.get('sync_mode') or '未声明'}")
        lines.append(f"    站位        {delivery.get('standing') or '未声明'}")
        if delivery.get("why"):
            lines.append(f"    · {delivery['why']}")
        policy = entry["write_policy"]
        tiers = "  ".join(f"{name}={policy['tiers'][name] or '未声明'}"
                          for name in CHANGE_CLASSES)
        lines.append(f"    写策略      {tiers}")
        reach = entry["reach"]
        if not reach["state_present"]:
            lines.append("    观测        从未（没有 state）—— 不是「观测过且没变」")
        else:
            lines.append(f"    观测        revision {reach['state_revision']}"
                         f"  {reach['state_last_observed_at']}")
            if reach["declared_never_derived"]:
                lines.append(f"    ⚠ 声明了但没定位到 {reach['declared_never_derived']}")
            if reach["derived_never_declared"]:
                lines.append(f"    ⚠ 文档里有而 profile 没声明 "
                             f"{reach['derived_never_declared']}")
        lines.append("")
    for obj in report["objects"]:
        if obj["materializations"] > 1:
            lines.append(f"同一个对象有 {obj['materializations']} 份物化："
                         f"{obj['documents']} —— {obj['locator']}")
    return lines
