#!/usr/bin/env python3
"""A contract that names a producer, a field, or an enforcer must name a real one.

WHY THIS FILE EXISTS
--------------------
`tests/test_declared_commands.py` is the one gate in this tree that checks a
declaration against an implementation, and it checks exactly one class of
declaration: `serverosctl <verb> [<sub>]` told to a reader. It was written after
that class produced three real defects, and it promptly caught a fourth — in a
document written the same day, by the same hand, describing the commands it got
wrong.

The contract files carry three more classes of the same promise, and none of
them had a gate:

  * `producers:` — the six events `runtime/notification-contract.yaml` says are
    emitted. Nothing emits any of them.
  * `required_fields:` — a list of keys a record must carry. A key nothing ever
    reads is a key that is written, checked for presence, and then dropped.
  * `enforcement:` / `enforced_by:` / `decided_by:` — the rule states which code
    enforces it. `abilities/pack-contract.yaml` names this problem itself, in
    the field: the closure rule "was written first and implemented in no version
    of this product, and a `grep` for this filename over the ServerOS tree
    returned exactly one hit — this file."

WHAT IS OUT OF SCOPE, AND WHY SAYING SO MATTERS
-----------------------------------------------
  * A `producer:` whose value is prose rather than a path is not checked.
    `fleet/state-root-contract.yaml` says the `abilities` namespace is produced
    by "ability-management (writes), this node (observes)" and the `federation`
    namespace by "the federation collectors". No mechanical rule can resolve
    those, and a rule that guessed would be wrong in the confident direction.
  * `serverosctl board render`, listed as the producer of the `boards`
    namespace, is NOT reported here. It is already a recorded gap in
    `test_declared_commands.py`, which owns "a document names a command that
    does not exist". Two gates reporting one finding is how a reader learns that
    neither of them is the authority on it.
  * A `rule:` with no `enforcement:` key is prose. This file checks claims, not
    sentences.
  * A claim that names a function WITHOUT naming a file it lives in is not
    checked. `fleet/state-root-contract.yaml` says its third invariant is
    enforced by "`read_manifest`, which requires `reproducible: true`". There is
    no file in that sentence to resolve, and guessing which module defines
    `read_manifest` would be a rule reporting on its own guesswork.

WHAT A SYMBOL CLAIM HAS TO LOOK LIKE TO BE CHECKED
--------------------------------------------------
Three shapes, all measured from the contracts rather than imagined, all
anchored on backticks:

    `memory/record.py — validate()`              path and symbol in one span
    `abilities/packs.py` — `resolve()`           path and symbol in two
    memory/projection.py — `build`, `manifest_for`, `staleness`
                                                 a comma-separated list, the
                                                 file named once

Anything else is prose and is not read as a claim. That restriction is not
caution for its own sake: the first version of this rule took everything
identifier-shaped after an em dash, and so read the pack contract's closing
sentence — "... returned exactly one hit — this file." — as a claim that
`abilities/packs.py` defines a symbol called `this file`. It reported a
correctly-named file as broken.

WHAT THE READER DETECTOR CANNOT SEE, AND HOW I KNOW
---------------------------------------------------
This rule is written down because writing it down is the only thing that makes
"3 fields have no reader" checkable rather than a number. A declared field
counts as READ when its name appears as a string constant in one of these
positions, in a non-test `.py` file:

    record["field"]              a subscript with a constant key
    record.get("field")          likewise `.pop` / `.setdefault` / `getattr`
    "field" in record            a membership test
    ("a", "field", "c")          enumerated into a collection of names

That last position is not decoration, and the reason is the whole lesson of
this file. The first version of this rule counted only the first two, and
reported five unread fields. Two of them were wrong:

    generator    read at `runtime/state_root.py:330`, by
                 `for field in ("authority", "generated", "generator", ...):
                      if field not in document:`
                 A membership test against a document is a reader. The narrow
                 rule saw the tuple as a literal and the loop variable as a
                 name it could not follow.

    purpose      read at `abilities/packs.py:113`, by the same shape:
                 `for key in ("id", "name", "purpose", "members"): if not pack.get(key)`

Both are real readers. A gate that reported them as dead would have taught its
first reader to distrust it, which is how a gate gets turned off. The rule is
generous in this direction on purpose: it under-reports dead fields rather than
inventing them.

The narrow version of the same rule is why the number went 5 → 3, and that
number is the finding. The three that survived are listed below with what would
close each.

THE LIST IS NOT A GRAVEYARD
---------------------------
Same two properties as `test_declared_commands.py`, for the same reason:

  * every entry must still be declared in the repo — delete the declaration and
    the entry must go with it;
  * every entry must carry a `because` and a `disposition`;
  * and it works in the other direction too: a field that gains a reader, or an
    event that gains a producer, FAILS until its exemption is deleted. The
    exemption is what would otherwise hide the fix.

`tests/test_notifications.py` is not superseded by this file. It checks that
the event table and the reason table agree with each other — declaration against
declaration. This checks declaration against code. Those are different claims,
and the older test's thoroughness is exactly why it was mistaken for coverage.
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loader_graph import REPO, module_refs  # noqa: E402

CONTRACTS = tuple(sorted(
    p for p in REPO.glob("*/*.yaml") if p.is_file()
    and ".build" not in p.name))

# ---------------------------------------------------------------------------
# Class 1 · events declared to have producers.
# ---------------------------------------------------------------------------
EVENT_CONTRACT = Path("runtime/notification-contract.yaml")

# Measured: none of the six has a producer outside `tests/` and the contract
# itself. `runtime/notifications.py` can deliver each one and has tests that
# prove it; nothing calls `deliver`. That is the shape worth recording — the
# module is complete, tested, and unreachable.
#
# Note what is NOT a producer: `mcp/codex_app_server_provider.py` speaks the
# codex app-server protocol, whose notification is `turn/completed` — a
# JSON-RPC method name with a slash, and a different fact from this contract's
# `turn.completed`. A substring scan would have called that a producer and
# reported this table as healthy.
KNOWN_UNPRODUCED = {
    "session.created": {
        "because": "nothing calls `notifications.deliver`; the session service "
                   "persists nothing and emits nothing.",
        "disposition": "NEEDS_HUMAN",
    },
    "session.waiting_input": {
        "because": "the provider reports `waiting_input` as session status and "
                   "no bridge turns a status transition into an event.",
        "disposition": "NEEDS_HUMAN",
    },
    "turn.completed": {
        "because": "the provider observes the turn and the transport can carry "
                   "a notification; the step between them does not exist.",
        "disposition": "NEEDS_HUMAN",
    },
    "approval.required": {
        "because": "approvals are created and swept by "
                   "`runtime/agent_control_store.py`, which emits nothing.",
        "disposition": "NEEDS_HUMAN",
    },
    "handoff.completed": {
        "because": "`memory/handoff.py` has no production caller at all.",
        "disposition": "DEFER",
    },
    "run.terminated": {
        "because": "run termination is written by the work-engine, in another "
                   "repository, and nothing reads it back here.",
        "disposition": "DEFER",
    },
}

# ---------------------------------------------------------------------------
# Class 2 · record fields nothing reads.
# ---------------------------------------------------------------------------
KNOWN_UNREAD = {
    # Two entries left this table in W7, and leaving is the only exit it has.
    # `capability_probe` and `version_observed_by` were both declared in
    # `record.required_fields` with no reader; the first was deleted and the
    # second was implemented as a per-runtime probe declaration that
    # `abilities/runtime_binding.py` reads. An exemption whose field gained a
    # reader is not a smaller exemption, it is a lie, so the gate requires it
    # to be removed rather than narrowed — see
    # `test_a_field_that_gained_a_reader_must_lose_its_exemption`.
    "request": {
        "declared_at": ("abilities/target-contract.yaml",),
        "because": "`abilities/target.py:845` copies the whole request into "
                   "every resolution record, including `work_item`; no consumer "
                   "reads it back. `fleet/view.py` reads `steps` and "
                   "`target.route`, and `serverosctl ability resolve` renders "
                   "the resolver's own record, which has no `request` field.",
        "disposition": "NEEDS_HUMAN",
    },
}

# ---------------------------------------------------------------------------
# Class 3 · enforcer claims that name nothing.
# ---------------------------------------------------------------------------
# Empty today, and that is a claim this file has to earn rather than assert.
# A gate whose findings list is empty on the day it opens is indistinguishable
# from a gate whose scanner stopped matching, so
# `test_the_enforcer_resolver_catches_a_broken_claim` feeds it a synthetic
# contract naming a file and a symbol that do not exist, and requires both to
# be reported. Without that control, "no unresolved enforcers" would be a
# sentence about the scanner and not about the tree.
KNOWN_UNRESOLVED = {}

ENFORCER_KEYS = ("enforcement", "enforced_by", "decided_by")

_PY_PATH = re.compile(r"[\w./-]+\.py")
_CALLED_FROM = re.compile(r"called\s+(?:from|by)\s+`?([\w./-]+\.py)`?")

# A symbol claim is checked in exactly two shapes, both measured from the
# contracts rather than imagined:
#
#   `memory/record.py — validate()`            one symbol, the whole claim
#                                              inside one backtick span
#   memory/projection.py — `build`, `manifest_for`, `staleness`
#                                              a comma-separated list, each
#                                              name backticked separately
#
# An earlier version of this used `—\s*([A-Za-z_][\w, `]*)` — "everything
# identifier-shaped after the dash" — and it read the pack contract's trailing
# sentence "... returned exactly one hit — this file." as a claim that
# `abilities/packs.py` defines a symbol called `this file`. That was a finding
# against a file that was named correctly, and it cost one round to find.
# Both patterns below are anchored on the backticks, so prose after a dashes
# is prose.
#   `abilities/packs.py` — `no_such_symbol()`, ...   path and symbol in
#                                                   separate spans
#
# The optional backtick on either side of the path is what makes the third
# shape readable; without it the path and the symbol have to be inside one
# span, which only one of the three claims in this repo does.
_BACKTICKED_CLAIM = re.compile(
    r"`([\w./-]+\.py)\s*—\s*([A-Za-z_][A-Za-z0-9_]*)\(\)`")
_LIST_CLAIM = re.compile(
    r"`?([\w./-]+\.py)`?\s*—\s*"
    r"(`[A-Za-z_][A-Za-z0-9_]*(?:\(\))?`"
    r"(?:\s*,\s*`[A-Za-z_][A-Za-z0-9_]*(?:\(\))?`)*)")


def _flatten(value) -> list[str]:
    """Every string inside a contract value, whatever its container.

    `enforcement` is a mapping in `memory/record-contract.yaml` — one entry per
    enforcer — and a string elsewhere. Reading only strings silently dropped
    the two claims that name `memory/record.py`, which are the ones the closure
    rule of §2 is about.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _flatten(item)]
    if isinstance(value, list):
        return [s for item in value for s in _flatten(item)]
    return []


def symbol_claims(value: str) -> list[tuple[str, str]]:
    """(file, symbol) pairs this claim names, in every measured shape.

    Deduplicated in order: the shapes overlap, and reporting one symbol twice
    would make a single broken claim look like two.
    """
    found: list[tuple[str, str]] = []
    for match in _BACKTICKED_CLAIM.finditer(value):
        found.append((match.group(1), match.group(2)))
    for match in _LIST_CLAIM.finditer(value):
        owner = match.group(1)
        for symbol in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)", match.group(2)):
            found.append((owner, symbol))
    return list(dict.fromkeys(found))


def _strings_in(tree: ast.AST) -> list[tuple[str, int]]:
    return [(n.value, n.lineno) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


_SOURCE_CACHE: list[tuple[str, ast.AST]] | None = None


def _sources() -> list[tuple[str, ast.AST]]:
    """Every non-test module, parsed once.

    Parsed once because this file asks about 45 fields and 6 events, and each
    question is a question about the same set of files.
    """
    global _SOURCE_CACHE
    if _SOURCE_CACHE is None:
        parsed: list[tuple[str, ast.AST]] = []
        for path in sorted(REPO.rglob("*.py")):
            rel = path.relative_to(REPO)
            if rel.parts[0] == "tests":
                continue
            try:
                parsed.append((rel.as_posix(),
                               ast.parse(path.read_text(encoding="utf-8"))))
            except SyntaxError:
                continue
        _SOURCE_CACHE = parsed
    return _SOURCE_CACHE


_READ_INDEX: dict[str, list[str]] | None = None
_STRING_INDEX: dict[str, list[str]] | None = None


def _build_indexes() -> None:
    """One walk of the tree, two name → sites maps.

    The first version of this asked the tree a fresh question per field and per
    event — 51 full traversals, 45 seconds on its own, in a suite that already
    takes two and a half minutes. Indexed the other way round it is one walk,
    and `readers_of` is a dictionary lookup.
    """
    global _READ_INDEX, _STRING_INDEX
    if _READ_INDEX is not None:
        return
    reads: dict[str, list[str]] = {}
    strings: dict[str, list[str]] = {}

    def note(bucket: dict[str, list[str]], key, where: str) -> None:
        if isinstance(key, str):
            bucket.setdefault(key, []).append(where)

    for rel, tree in _sources():
        for node in ast.walk(tree):
            if not hasattr(node, "lineno"):
                continue  # the Module itself, which is not a line
            where = f"{rel}:{node.lineno}"
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                note(strings, node.value, where)
            if isinstance(node, ast.Subscript):
                index = node.slice
                if isinstance(index, ast.Constant):
                    note(reads, index.value, where)
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"get", "pop", "setdefault", "getattr"}
                    and node.args):
                first = node.args[0]
                if isinstance(first, ast.Constant):
                    note(reads, first.value, where)
            elif isinstance(node, ast.Compare):
                left = node.left
                if (isinstance(left, ast.Constant)
                        and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)):
                    note(reads, left.value, where)
            elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                for element in node.elts:
                    if isinstance(element, ast.Constant):
                        note(reads, element.value, where)

    _READ_INDEX, _STRING_INDEX = reads, strings


def _declarations_cache() -> dict[str, list[str]]:
    global _DECLARATIONS
    if _DECLARATIONS is None:
        _DECLARATIONS = _read_required_fields()
    return _DECLARATIONS


_DECLARATIONS: dict[str, list[str]] | None = None


def _walk_yaml(node, path: str, out: list):
    if isinstance(node, dict):
        for key, value in node.items():
            out.append((path, key, value))
            _walk_yaml(value, f"{path}.{key}", out)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _walk_yaml(value, f"{path}[{i}]", out)


def _contracts() -> list[tuple[Path, dict]]:
    loaded = []
    for path in CONTRACTS:
        try:
            loaded.append((path, yaml.safe_load(path.read_text(encoding="utf-8"))))
        except yaml.YAMLError:  # a contract that will not parse is another gate's job
            continue
    return loaded


def declared_events() -> list[str]:
    document = yaml.safe_load((REPO / EVENT_CONTRACT).read_text(encoding="utf-8"))
    return list(document.get("events", {}).get("producers") or [])


def producers_of(event: str) -> list[str]:
    """Non-test modules that name this event as a string constant.

    Exact string equality, not a substring search. Nothing here is a
    provider's own event: the codex app-server's `turn/completed` is a
    different string, and the declaration `@1`-style suffixes providers use
    for their own vocabulary are not this contract's names.
    """
    _build_indexes()
    return [site for site in _STRING_INDEX.get(event, ())
            if not site.startswith("runtime/notifications.py:")]


# -- §17: a producer that is declared, and the producer that is behind it -----
#
# `producers_of` above answers "does the event name appear anywhere outside the
# contract and the module that renders it". That question has a false positive
# built into it and §17 asks a narrower one: the contract now says WHERE each
# event is raised — `raised_by: runtime/alerting.py#notify_for` — and a
# reference like that is checkable in three separate ways, each of which can be
# false on its own:
#
#   * the file may not exist (a rename),
#   * the file may not define the function (a rename on the other side),
#   * the file may define it and nothing may ever call it.
#
# The third is the one that matters, and it is the pathology this whole file
# exists for: a function that is correct, tested, and reachable from nothing.
# A string-literal scan cannot see it — `alerting.py` names both event names as
# module constants, so `producers_of` is satisfied by the file merely declaring
# them. `reachable_producers_of` asks who CALLS the named function instead, and
# that is a different question with a different answer.

_RAISED_BY = re.compile(r"^([\w./-]+\.py)#([A-Za-z_][A-Za-z0-9_]*)$")


def declared_producer_entries() -> dict[str, dict]:
    """`runtime/notification-contract.yaml#events.producers`, verbatim.

    A mapping now rather than the bare list it was: each entry carries either
    where it is raised or the reason it is not, and the two are mutually
    exclusive by construction rather than by convention — see
    `producer_problems`.
    """
    document = yaml.safe_load(
        (REPO / EVENT_CONTRACT).read_text(encoding="utf-8"))
    block = (document.get("events") or {}).get("producers") or {}
    if not isinstance(block, dict):
        raise AssertionError(
            "`events.producers` must be a mapping of event → disposition, so "
            "that an entry can say it is NOT produced. A bare list can only "
            "say which events exist, which is what made the old one "
            "unfalsifiable.")
    return {str(event): (entry if isinstance(entry, dict) else {"status": entry})
            for event, entry in block.items()}


def _callers_of(relative: str, function: str) -> list[str]:
    """Non-test modules that CALL `function` — not modules that mention it.

    The distinction is the whole value of this check, so the rule is narrow on
    purpose: a caller is a `Call` whose target is the bare name
    (`notify_for(...)`) or the attribute (`alerting.notify_for(...)`). An
    import alone is not a call, and a mention is not an import.

    The first version accepted any `ast.Name` with a matching id, and the
    positive control caught it immediately: `runtime/protection.py` has a
    PARAMETER named `identity`, so `alerting.identity` — a helper nothing
    outside its own file calls — was reported as reached. A scan that counts a
    parameter name as a caller is the same class of false positive as a text
    search that counts a sentence in a docstring, and it fails in the direction
    that matters here: it reports a dead producer as live.
    """
    callers: list[str] = []
    for rel, tree in _sources():
        if rel == relative:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = (target.id if isinstance(target, ast.Name)
                    else target.attr if isinstance(target, ast.Attribute)
                    else None)
            if name == function:
                callers.append(rel)
                break
    return sorted(set(callers))


def producer_problems(event: str, entry: dict) -> list[str]:
    """Everything wrong with one producer entry, as readable lines.

    Four statuses are possible and the contract uses two of them. The other two
    are named here so that an entry cannot invent a third word that reads as a
    promise:

      IMPLEMENTED     something raises it. `raised_by` must resolve, and the
                      function it names must be called from outside its own
                      file — otherwise "implemented" means "written".
      DECLARED_ABSENT named as not produced, with `because` and `disposition`.
                      It must NOT carry a `raised_by`: an entry that says both
                      "nothing raises this" and "here is where it is raised" is
                      a file disagreeing with itself.
    """
    problems: list[str] = []
    status = str(entry.get("status") or "")
    raised_by = entry.get("raised_by")

    if status not in {"IMPLEMENTED", "DECLARED_ABSENT"}:
        problems.append(
            f"{event}: status {status!r} is not one of IMPLEMENTED, "
            f"DECLARED_ABSENT")
        return problems

    if status == "DECLARED_ABSENT":
        if raised_by:
            problems.append(
                f"{event}: says nothing raises it and names {raised_by!r} as "
                f"where it is raised")
        for field in ("because", "disposition"):
            if not str(entry.get(field) or "").strip():
                problems.append(f"{event}: DECLARED_ABSENT with no {field}")
        return problems

    if not raised_by:
        problems.append(f"{event}: IMPLEMENTED with no `raised_by` to check")
        return problems

    match = _RAISED_BY.match(str(raised_by))
    if not match:
        problems.append(
            f"{event}: `raised_by: {raised_by}` is not `path#function`")
        return problems

    relative, function = match.group(1), match.group(2)
    if not (REPO / relative).is_file():
        problems.append(f"{event}: raised_by names {relative}, which is gone")
        return problems

    # The event name has to be a literal in the file it names — so a rename of
    # either the event or the file is red rather than silent. Literal and not
    # substring: `alert.opened` and `alert.opened_at` are different names.
    literals = {value for value, _ in _strings_in(
        ast.parse((REPO / relative).read_text(encoding="utf-8")))}
    if event not in literals:
        problems.append(
            f"{event}: {relative} does not name the event as a string literal")

    if function not in _defined_symbols(relative):
        problems.append(
            f"{event}: {relative} does not define {function!r}")
        return problems

    callers = _callers_of(relative, function)
    if not callers:
        problems.append(
            f"{event}: {relative}#{function} exists and NOTHING outside that "
            f"file calls it — which is `declared-but-absent` in its ordinary "
            f"form: the producer is written, tested, and unreachable")
    return problems


def _read_required_fields() -> dict[str, list[str]]:
    """Every `required_fields` entry, by field name, with where it was declared."""
    declared: dict[str, list[str]] = {}
    for path, document in _contracts():
        flat: list[tuple[str, str, object]] = []
        _walk_yaml(document, "", flat)
        for where, key, value in flat:
            if key != "required_fields":
                continue
            names = value if isinstance(value, list) else list(value or {})
            for name in names:
                declared.setdefault(str(name), []).append(
                    f"{path.relative_to(REPO)}{where}")
    return declared


def readers_of(field: str) -> list[str]:
    """Non-test modules that read this field. See the module docstring for the
    four positions that count, and for the two false findings the first,
    narrower version of this rule produced."""
    _build_indexes()
    return list(_READ_INDEX.get(field, ()))


def enforcer_claims() -> list[tuple[str, str, str]]:
    """(where, key, value) for every enforcer claim that names a `.py` file.

    Prose values are dropped here rather than later, so that the two tests
    below are written against exactly the claims this file takes
    responsibility for."""
    claims: list[tuple[str, str, str]] = []
    for path, document in _contracts():
        flat: list[tuple[str, str, object]] = []
        _walk_yaml(document, "", flat)
        for where, key, value in flat:
            if key not in ENFORCER_KEYS:
                continue
            for text in _flatten(value):
                if _PY_PATH.search(text):
                    claims.append((f"{path.relative_to(REPO)}{where}", key, text))
    return claims


def _defined_symbols(rel: str) -> set[str]:
    tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def resolve_enforcer_claim(value: str) -> list[str]:
    """Everything wrong with one enforcer claim, as human-readable lines.

    Every file the claim names must exist. Every symbol it claims in one of the
    two measured shapes must be defined in the file that shape attaches it to —
    which is why the shapes carry the owner: `abilities/pack-contract.yaml`
    says the closure rule is decided by "`abilities/packs.py — resolve()`,
    called from `scripts/build.py`", and a rule that checked `resolve` against
    every file named in the sentence reported it missing from the second one.
    """
    problems: list[str] = []
    mentions = [m.group() for m in _PY_PATH.finditer(value)]
    for relative in dict.fromkeys(mentions):
        if not (REPO / relative).is_file():
            problems.append(f"names {relative}, which does not exist")

    for owner, symbol in symbol_claims(value):
        if not (REPO / owner).is_file():
            continue  # already reported above; one finding, one line
        if symbol not in _defined_symbols(owner):
            problems.append(
                f"names {owner}'s {symbol!r}, which it does not define")

    # "called from `x.py`" is checked against the loader graph rather than the
    # text: the file has to be able to load the thing it claims to call. A
    # textual mention of `resolve` is satisfied by `pathlib`'s `.resolve()`,
    # which is a gate passing for the wrong reason.
    caller = _CALLED_FROM.search(value)
    if caller and mentions and (REPO / caller.group(1)).is_file():
        target = mentions[0]
        loaded = {str(reference) for reference in module_refs(Path(caller.group(1)))}
        if target not in loaded:
            problems.append(
                f"says {caller.group(1)} calls into {target}, and it does not "
                f"load it")
    return problems


class DeclaredEventsHaveProducers(unittest.TestCase):
    def test_every_declared_event_is_produced_or_a_recorded_gap(self):
        unproduced = [event for event in declared_events()
                      if not producers_of(event) and event not in KNOWN_UNPRODUCED]
        self.assertEqual(unproduced, [], "\n".join(
            ["the notification contract declares events nothing emits:",
             *unproduced,
             "",
             "Either wire a producer, or add it to KNOWN_UNPRODUCED with a",
             "`because` and a `disposition`."]))

    def test_an_event_that_gained_a_producer_must_lose_its_exemption(self):
        """The direction that matters. An exemption that survives the fix is
        how a reader comes to believe a working bridge is broken."""
        now_produced = [event for event in KNOWN_UNPRODUCED if producers_of(event)]
        self.assertEqual(now_produced, [], "\n".join(
            ["these events have a producer now; delete their KNOWN_UNPRODUCED "
             "entries:", *[f"  {e} <- {producers_of(e)}" for e in now_produced]]))

    def test_each_recorded_gap_says_what_would_close_it(self):
        for event, entry in KNOWN_UNPRODUCED.items():
            self.assertTrue(entry.get("because"), f"{event} has no because")
            self.assertIn(entry.get("disposition"),
                          {"NEEDS_HUMAN", "REDECLARED", "DEFER"},
                          f"{event} has no usable disposition")

    def test_the_exemption_list_cannot_outlive_its_declaration(self):
        declared = set(declared_events())
        for event in KNOWN_UNPRODUCED:
            self.assertIn(event, declared,
                          f"{event} is exempted but no longer declared; remove "
                          "the entry")

    def test_the_scanner_sees_a_real_event(self):
        """A control against the scan going blind. `turn.completed` is the case
        worth pinning: it must NOT be found, because the provider's
        `turn/completed` is a different string, and a substring scan would
        report this table as healthy."""
        self.assertIn("turn.completed", declared_events())
        self.assertGreaterEqual(len(declared_events()), 5, declared_events())
        self.assertEqual(producers_of("turn.completed"), [])
        self.assertEqual(producers_of("session.created"), [])


class DeclaredProducersResolve(unittest.TestCase):
    """§17. Every `raised_by` resolves, and every absence says so out loud.

    `DeclaredEventsHaveProducers` above asks whether the event NAME appears in
    the tree. This asks the narrower question the contract now makes possible:
    whether the specific function the contract points at exists, and whether
    anything calls it. Both are needed — the first catches an event nobody
    mentions at all, the second catches the case that motivated §17, where the
    function is written and correct and no production path reaches it.
    """

    def test_every_implemented_producer_resolves_and_is_reachable(self):
        problems = []
        for event, entry in declared_producer_entries().items():
            problems.extend(producer_problems(event, entry))
        self.assertEqual(problems, [], "\n".join(
            ["the notification contract's producer table does not check out:",
             *[f"  {line}" for line in problems], "",
             "An IMPLEMENTED producer must name a file#function that exists,",
             "names the event as a literal, and is called from outside its own",
             "file. An entry that is not produced says DECLARED_ABSENT with a",
             "`because` and a `disposition` and names no raised_by."]))

    def test_the_two_records_of_absence_cannot_drift(self):
        """This test and the contract each say which events are not produced.

        Two spellings of one fact, and the join is the test — the same shape
        the ability tree uses one directory over. They are not merged, because
        they answer to different readers: the contract's `because` explains the
        gap to whoever lands on it next, and `KNOWN_UNPRODUCED` is what makes
        the exemption above force a failure the moment the gap closes. A
        contract entry added without an exemption would exempt nothing; an
        exemption without an entry would be a note in a test file about a claim
        the contract no longer makes.
        """
        absent = {event for event, entry in declared_producer_entries().items()
                  if entry.get("status") == "DECLARED_ABSENT"}
        self.assertEqual(
            absent, set(KNOWN_UNPRODUCED),
            "the contract's DECLARED_ABSENT set and KNOWN_UNPRODUCED disagree: "
            f"only in the contract {sorted(absent - set(KNOWN_UNPRODUCED))}, "
            f"only in this file {sorted(set(KNOWN_UNPRODUCED) - absent)}")

    def test_every_declared_event_has_a_disposition(self):
        """No event may be missing from the table entirely.

        An event with no entry is not "produced" and not "absent" — it is
        unclassified, and unclassified is the state in which nobody has had to
        decide, which is how the six got there in the first place.
        """
        missing = sorted(set(declared_events()) - set(declared_producer_entries()))
        self.assertEqual(missing, [], "\n".join(
            ["these events are declared and the producer table says nothing "
             "about them:", *missing]))

    def test_the_producer_scanner_can_go_red(self):
        """Positive controls, in both directions.

        A gate nobody has watched fail is a gate that might be comparing a
        constant to itself. Each of these is a real claim taken from the
        contract and broken in exactly one way.
        """
        # The real one passes.
        self.assertEqual(
            producer_problems("alert.opened", {
                "raised_by": "runtime/alerting.py#notify_for",
                "status": "IMPLEMENTED"}), [])

        # A function that exists in a file that exists, and nothing calls it:
        # the pathology, and the one a name-scan cannot see. `identity` is used
        # as a PARAMETER name elsewhere in the tree, which is why the first
        # version of this scan called it reachable — the control is not
        # incidental, it is the bug it caught.
        unreachable = producer_problems("alert.opened", {
            "raised_by": "runtime/alerting.py#identity",
            "status": "IMPLEMENTED"})
        self.assertTrue(any("NOTHING outside that file calls it" in p
                            for p in unreachable), unreachable)

        # A rename on either side.
        self.assertTrue(any("is gone" in p for p in producer_problems(
            "alert.opened", {"raised_by": "runtime/alerting_gone.py#notify_for",
                             "status": "IMPLEMENTED"})))
        self.assertTrue(any("does not define" in p for p in producer_problems(
            "alert.opened", {"raised_by": "runtime/alerting.py#not_for",
                             "status": "IMPLEMENTED"})))

        # An event the file does not name.
        self.assertTrue(any("does not name the event" in p for p in
                            producer_problems("alert.deleted", {
                                "raised_by": "runtime/alerting.py#notify_for",
                                "status": "IMPLEMENTED"})))

        # Both sides of the contradiction.
        self.assertTrue(any("and names" in p for p in producer_problems(
            "turn.completed", {"status": "DECLARED_ABSENT",
                               "raised_by": "runtime/alerting.py#notify_for",
                               "because": "x", "disposition": "y"})))
        self.assertTrue(any("no disposition" in p for p in producer_problems(
            "turn.completed", {"status": "DECLARED_ABSENT", "because": "x"})))
        self.assertTrue(any("is not one of" in p for p in producer_problems(
            "turn.completed", {"status": "MAYBE_LATER"})))

    def test_the_producer_of_both_alert_events_is_the_daemon(self):
        """The one production path, named so that unwiring it is red.

        `notify_for` is called from `runtime/cli.py#cmd_alert_sync`, and the
        daemon reaches the same two events through `alert.pass` → the pending
        intent → that command. What this asserts is the edge that makes the
        event real: a surface that is not the module itself calls it. The full
        chain — that `cmd_alert_sync` is wired to a parser — is not asserted
        here because `test_declared_commands.py` already fails when a declared
        `serverosctl` subcommand stops existing, and between the two gates the
        path from a person typing a command to a message leaving the machine is
        covered end to end.
        """
        callers = _callers_of("runtime/alerting.py", "notify_for")
        self.assertIn("runtime/cli.py", callers, callers)


class DeclaredFieldsHaveReaders(unittest.TestCase):
    def test_every_declared_field_is_read_or_a_recorded_gap(self):
        unread = [name for name in _declarations_cache()
                  if not readers_of(name) and name not in KNOWN_UNREAD]
        self.assertEqual(unread, [], "\n".join(
            ["a contract requires a field nothing reads:",
             *[f"  {n} — declared at {_declarations_cache()[n]}"
               for n in unread],
             "",
             "Either read it, drop it from `required_fields`, or add it to",
             "KNOWN_UNREAD with a `because` and a `disposition`."]))

    def test_a_field_that_gained_a_reader_must_lose_its_exemption(self):
        now_read = [name for name in KNOWN_UNREAD if readers_of(name)]
        self.assertEqual(now_read, [], "\n".join(
            ["these fields are read now; delete their KNOWN_UNREAD entries:",
             *[f"  {n} <- {readers_of(n)[:3]}" for n in now_read]]))

    def test_each_recorded_gap_names_where_it_was_declared(self):
        declared = _declarations_cache()
        for name, entry in KNOWN_UNREAD.items():
            self.assertTrue(entry.get("because"), f"{name} has no because")
            self.assertIn(entry.get("disposition"),
                          {"NEEDS_HUMAN", "REDECLARED", "DEFER"},
                          f"{name} has no usable disposition")
            self.assertIn(name, declared,
                          f"{name} is exempted but no longer declared; remove "
                          "the entry")
            # Recorded as a file, checked against the dotted site inside it,
            # so moving a declaration to another block does not read as a
            # different declaration.
            for where in entry["declared_at"]:
                self.assertTrue(
                    any(site.startswith(where) for site in declared[name]),
                    f"{name} is recorded as declared at {where}, and it is "
                    f"declared at {declared[name]}")

    def test_the_reader_detector_sees_the_two_shapes_that_beat_it(self):
        """The measurement that produced the 5 → 3 above, kept as an assertion.

        Both of these are read through a `for` loop over a tuple of names,
        which the first version of the detector could not follow. If this test
        ever fails, the numbers above are wrong again and the two fields it
        names must go back into KNOWN_UNREAD — which is the honest outcome, and
        the reason this control exists rather than a comment.
        """
        self.assertTrue(readers_of("generator"),
                        "`generator` is read at runtime/state_root.py:330 by "
                        "`for field in (...): if field not in document`")
        self.assertTrue(readers_of("purpose"),
                        "`purpose` is read at abilities/packs.py:113 by "
                        "`for key in (...): if not pack.get(key)`")

    def test_the_reader_detector_does_not_count_a_writer(self):
        """The other side of the same control: a field written into a dict
        literal and never looked up must NOT count as read. `request` is
        exactly that — copied into every resolution record at
        `abilities/target.py:845`, read by nothing."""
        self.assertEqual(readers_of("request"), [])


class EnforcerClaimsResolve(unittest.TestCase):
    def test_every_enforcer_claim_names_something_real(self):
        unresolved = []
        for where, key, value in enforcer_claims():
            problems = resolve_enforcer_claim(value)
            if problems:
                unresolved.append(f"{where} [{key}] " + "; ".join(problems))
        self.assertEqual(unresolved, [], "\n".join(
            ["a contract says which code enforces its rule, and names code that "
             "is not there:", *unresolved]))

    def test_no_recorded_gap_has_since_been_closed(self):
        still = [entry for entry in KNOWN_UNRESOLVED
                 if not resolve_enforcer_claim(KNOWN_UNRESOLVED[entry]["claim"])]
        self.assertEqual(still, [], "\n".join(
            ["these enforcer claims resolve now; delete their entries:", *still]))

    def test_the_scanner_finds_the_claims(self):
        """A control against the walker quietly returning nothing, which would
        make both tests above pass by having nothing to check.

        Five claims today, three of them in `memory/record-contract.yaml` and
        `memory/provider-contract.yaml` — where `enforcement` is a MAPPING, one
        entry per enforcer. A version of the walker that read only string
        values found two claims instead of five and reported the tree clean.
        """
        claims = enforcer_claims()
        self.assertGreaterEqual(len(claims), 5, claims)
        named = " ".join(value for _, _, value in claims)
        self.assertIn("abilities/packs.py", named)
        self.assertIn("memory/record.py", named)

    def test_both_symbol_shapes_are_read(self):
        """The two measured forms, kept as assertions because a rule that
        handled only the first would leave the claim naming three symbols
        unchecked, and "no unresolved enforcers" would not say so."""
        one = symbol_claims("`memory/record.py — validate()` refuses it.")
        self.assertEqual(one, [("memory/record.py", "validate")], one)

        listed = symbol_claims(
            "memory/projection.py — `build`, `manifest_for`, `staleness`")
        self.assertEqual(listed, [("memory/projection.py", "build"),
                                  ("memory/projection.py", "manifest_for"),
                                  ("memory/projection.py", "staleness")], listed)

    def test_prose_after_a_dash_is_not_a_symbol_claim(self):
        """The false positive this file started with, kept as a regression.

        The pack contract's `decided_by` ends "... returned exactly one hit —
        this file." A rule that took everything identifier-shaped after a dash
        read that as a claim that `abilities/packs.py` defines `this file`, and
        reported a correctly-named file as broken.
        """
        prose = ("`abilities/packs.py — resolve()`, called from "
                 "`scripts/build.py`. This line is not decoration: a `grep` "
                 "returned exactly one hit — this file.")
        self.assertEqual(symbol_claims(prose),
                         [("abilities/packs.py", "resolve")])
        self.assertEqual(resolve_enforcer_claim(prose), [])

    def test_the_enforcer_resolver_catches_a_broken_claim(self):
        """The positive control, and the reason `KNOWN_UNRESOLVED` can be empty.

        Two synthetic claims, one broken in each way this file checks: a file
        that does not exist, and a symbol that is not defined in the file that
        does. Both must be reported. Without this, "no unresolved enforcers"
        is a statement about the scanner.
        """
        missing_file = resolve_enforcer_claim(
            "`runtime/no_such_module.py` — `resolve()` refuses it.")
        self.assertTrue(any("does not exist" in problem for problem in missing_file),
                        missing_file)

        missing_symbol = resolve_enforcer_claim(
            "`abilities/packs.py` — `no_such_symbol()`, which does not exist.")
        self.assertTrue(
            any("no_such_symbol" in problem for problem in missing_symbol),
            missing_symbol)

    def test_the_called_from_half_is_checked_against_the_loader_graph(self):
        """A `called from <file>` claim is not a textual one.

        `scripts/build.py` really does load `abilities/packs.py` — at
        `_packs_module()`, through `spec_from_file_location(name, repo_root() /
        "abilities" / "packs.py")`. Asserting the loader edge means a claim
        stays honest when the load is deleted, which a substring search for
        `resolve` would not notice: `pathlib`'s `.resolve()` satisfies that.
        """
        self.assertIn("abilities/packs.py",
                      {str(r) for r in module_refs(Path("scripts/build.py"))})
        broken = resolve_enforcer_claim(
            "`abilities/packs.py` — `resolve()`, called from `runtime/anomaly.py`.")
        self.assertTrue(any("does not load it" in problem for problem in broken),
                        broken)


if __name__ == "__main__":
    unittest.main()
