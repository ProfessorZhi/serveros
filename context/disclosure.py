#!/usr/bin/env python3
"""Progressive disclosure: what a session is told, in what order, and what it
is never told.

    python3 disclosure.py --startup --item LIT-0104 --run r3 --capabilities 12
    python3 disclosure.py --level capability:2 --value '{"ability": "..."}'

This is the ServerOS **Disclosure** kernel, the fourth of the read-only
decision kernels beside Context, Space and Ability. It answers one question:

    given everything ServerOS could say, what should this session actually
    be told, right now, at the level it asked for?

Why this is a module and not a template
---------------------------------------
A startup view looks like presentation, so it gets built as presentation: a
string template that each round adds one true and useful line to, and no round
removes any, because every line was individually justified. The result is a
context that is read once, believed generally, and used specifically — which
is to say, not used.

So the budget is a number in a file (`disclosure-contract.yaml`), the levels
are declared per axis, and this module enforces both. When a future line does
not fit, the choice is which line to remove, and it is made explicitly.

What this module is NOT
-----------------------
It reads no file, runs no probe, opens no socket, and does not know what a
Skill is. Every input is supplied by the caller, exactly as in
`abilities/resolver.py`. A disclosure that fetched its own content would make
the startup cost of a session a property of the fleet's health, and the first
time that was slow somebody would cache it, and the cache would be the thing
people read.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA = "serveros/disclosure.v1"

# The three axes. Order is the order they are needed in, and it is not
# alphabetical: an agent needs to know what it is working on before it needs
# to know what the machine can do about it, and both before it needs to know
# what it may do without asking.
AXES = ("context", "capability", "authority")

LEVELS = ("L0", "L1", "L2", "L3")

# The budget, from the contract. If these disagree with the file the tests
# fail, which is the point — a budget nobody checks is a style guide.
BUDGET_LINES = 6
BUDGET_TOKENS = 120

# What the capability L0 must never become. These are the shapes a session
# will browse rather than use, and every one of them has been proposed at some
# point as an improvement to the startup view.
FORBIDDEN_AT_L0 = (
    "ability:",
    "skills/",
    "device:",
    "SessionStart",
)


class Rejected(ValueError):
    """The disclosure request cannot be answered — as opposed to "nothing to
    say", which is an answer and is rendered as such."""


# ---------------------------------------------------------------------------
# Rendering one axis at one level.
# ---------------------------------------------------------------------------

def _unknown(axis: str, why: str) -> dict:
    """A level that cannot be produced says so, at its own level.

    It never falls back to a lower level's silence. An omitted line reads as
    "nothing to report", and "we could not tell" is a different fact that a
    reader is entitled to — the same defect the board view has, where a
    projection renders an unknown as an empty.
    """
    return {"axis": axis, "state": "UNKNOWN", "why": why, "value": None}


def _disclosed(axis: str, level: str, value, source: str | None = None) -> dict:
    return {"axis": axis, "state": "DISCLOSED", "level": level,
            "value": value, "source": source}


def disclose(axis: str, level: str, value, *, why_unknown: str | None = None,
             source: str | None = None) -> dict:
    """One axis at one level.

    `value is None` is the caller saying it does not have this — which is
    UNKNOWN and not an empty list. The distinction is the whole of
    `degradation.rule`: a caller that knows there are no abilities passes `0`;
    a caller that could not count passes None.
    """
    if axis not in AXES:
        raise Rejected(f"unknown axis {axis!r}; known: {list(AXES)}")
    if level not in LEVELS:
        raise Rejected(f"unknown level {level!r}; known: {list(LEVELS)}")
    if value is None:
        return _unknown(axis, why_unknown or "the caller did not supply this")
    if axis == "capability" and level == "L0":
        return _capability_l0(value, source)
    return _disclosed(axis, level, value, source)


def _capability_l0(value, source: str | None) -> dict:
    """Capability L0 is a NUMBER, not a list.

    A list is a menu, a menu invites browsing, and browsing is how a session
    spends its first thousand tokens reading a catalogue instead of working.
    The native agent already has its own Skill discovery and does not need a
    second, worse one.

    Two numbers, and they are not the same number
    ---------------------------------------------
    A catalogue count and a resolved count answer different questions, and
    the contract prices them differently: `costs.capability.L0` is "one
    manifest read, no probes", which is a catalogue read. Counting what this
    device can actually RESOLVE is the eight-input intersection — it needs
    records a cheap startup read does not have, and at L2 it may run a probe.

    Rendering a catalogue count as "N resolvable" is the union wearing the
    intersection's name, and it is the same defect as the resolver returning
    AVAILABLE for an ability nothing had checked — one layer up, in the
    presentation. So the two are carried as different values and rendered
    with different words. A caller that does not say which it has does not
    get the stronger claim.

    A plain integer is a CATALOGUE count: it is the cheap read, and the only
    one a caller that passes a bare number can have performed.

    Anything else is refused rather than summarised, because the first caller
    to pass a list would otherwise get a truncated one back and never learn
    that it had been truncated.
    """
    if isinstance(value, bool) or not isinstance(value, (int, dict)):
        raise Rejected(
            "capability L0 is a count. A list belongs at capability L2 or "
            "above, where asking for it is a deliberate act. Refusing rather "
            "than truncating: a caller who passed a list would otherwise "
            "receive a shortened one and never know."
        )
    if isinstance(value, dict):
        total = value.get("total")
        if not isinstance(total, int):
            raise Rejected("capability L0 record has no integer `total`")
        if "usable" not in value:
            usable = undecided = None
            basis = "catalogue"
        else:
            usable = value.get("usable")
            undecided = value.get("undecided", 0)
            if not isinstance(usable, int):
                raise Rejected("capability L0 record has a non-integer `usable`")
            basis = "resolved"
    else:
        total, usable, undecided, basis = value, None, None, "catalogue"

    return {
        "axis": "capability", "state": "DISCLOSED", "level": "L0",
        "value": {"total": total, "usable": usable, "undecided": undecided,
                  "basis": basis},
        "source": source,
    }


def _capability_line(counts: dict) -> str:
    """The words, which are the part that must not overclaim.

    "known to this authority" and "resolvable here" are different facts. The
    first is what a catalogue read earns; the second costs the intersection
    and is the one a session would act on.
    """
    if counts["basis"] == "catalogue":
        return (f"capabilities: {counts['total']} known to this authority; "
                "resolve one with `serverosctl ability resolve`")
    text = f"capabilities: {counts['usable']} resolvable"
    if counts["undecided"]:
        # Named separately and never folded into the usable count. A session
        # that reads one number will act on a false negative, and "we could
        # not tell" is the one thing it must not read as "no".
        text += f", {counts['undecided']} undecided"
    return text


# ---------------------------------------------------------------------------
# The startup view.
# ---------------------------------------------------------------------------

PRESENCE_LINE = "ServerOS is present on this device"


def startup_view(*, item: str | None = None, capability_count=None,
                 authority_line: str | None = None,
                 context_why: str | None = None,
                 authority_why: str | None = None) -> dict:
    """The whole of what is rendered before the agent has asked for anything.

    Pure, and deliberately narrow. The signature is the enforcement: there is
    no parameter for a Skill, a provider list, a device or a memory record, so
    a future caller cannot add one to the startup view without changing this
    function's signature — which is a visible edit, in a file whose docstring
    says why not to.

    A line that cannot be produced is rendered as unknown rather than dropped.
    Three lines is not "smaller" than five; it is five with two of them
    missing, and the reader cannot tell which.
    """
    lines = [{"axis": None, "level": None, "text": PRESENCE_LINE}]

    if item is None:
        lines.append({"axis": "context", "level": "L0",
                      "text": f"context: unknown ({context_why or 'not supplied'})"})
    else:
        lines.append({"axis": "context", "level": "L0", "text": f"context: {item}"})

    capability = None
    if capability_count is None:
        lines.append({"axis": "capability", "level": "L0",
                      "text": "capabilities: unknown (not counted)"})
    else:
        capability = _capability_l0(capability_count, None)
        lines.append({"axis": "capability", "level": "L0",
                      "text": _capability_line(capability["value"])})

    if authority_line is None:
        lines.append({"axis": "authority", "level": "L0",
                      "text": f"authority: unknown ({authority_why or 'not supplied'})"})
    else:
        lines.append({"axis": "authority", "level": "L0", "text": authority_line})

    lines.append({"axis": None, "level": None, "text":
                  "details on request: `serverosctl context current | "
                  "ability list | ability resolve --request <file>`"})

    return {
        "schema": SCHEMA,
        "view": "startup",
        "levels": {"context": "L0", "capability": "L0", "authority": "L0"},
        "lines": lines,
        "text": "\n".join(line["text"] for line in lines),
        "budget": {"lines": BUDGET_LINES, "tokens": BUDGET_TOKENS},
        "within_budget": (len(lines) <= BUDGET_LINES
                          and _estimate_tokens(lines) <= BUDGET_TOKENS),
    }


def _estimate_tokens(lines: list[dict]) -> int:
    """Deliberately crude, and deliberately not a tokenizer.

    A real tokenizer would make this module depend on a model, and the number
    that matters is whether the view fits in a budget with room to spare —
    which a four-characters-per-token estimate answers for text this short.
    Being wrong by ten percent changes nothing; being principled about it
    would add a dependency to the startup path.
    """
    return sum(len(line["text"]) for line in lines) // 4


# ---------------------------------------------------------------------------
# Level queries, so an agent can ask for exactly what it wants.
# ---------------------------------------------------------------------------

def request(axis: str, level: str, value, *, why_unknown: str | None = None) -> dict:
    """The CLI-facing form of `disclose`, plus the level it came from.

    The level is stated on every answer because an agent that does not know
    where an answer came from cannot reason about its cost, and will ask for
    the expensive level next time whether or not it needed to.
    """
    record = disclose(axis, level, value, why_unknown=why_unknown)
    index = LEVELS.index(level)
    return {
        **record,
        "requested_level": level,
        # A skipping contract: an agent may ask for L3 without L1, and this
        # says so rather than implying a protocol was walked.
        "skipped_levels": list(LEVELS[:index]),
        "schema": SCHEMA,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--startup", action="store_true")
    parser.add_argument("--item")
    parser.add_argument("--capabilities", type=int)
    parser.add_argument("--authority")
    parser.add_argument("--level", help="axis:level, e.g. capability:2")
    parser.add_argument("--value", help="a JSON value for --level")
    args = parser.parse_args(argv)

    if args.startup:
        record = startup_view(item=args.item, capability_count=args.capabilities,
                              authority_line=args.authority)
    elif args.level:
        if ":" not in args.level:
            parser.error("--level takes axis:level, e.g. capability:2")
        axis, _, level = args.level.partition(":")
        value = json.loads(args.value) if args.value else None
        record = request(axis, f"L{level}" if not level.startswith("L") else level,
                         value, why_unknown="no value supplied")
    else:
        parser.error("one of --startup or --level is required")

    if record.get("view") == "startup":
        # The rendered view is the output, because the caller here is a person
        # or a session-start hook that wants the lines. The full record — with
        # levels, budget and `within_budget` — is available from the module,
        # and printing both would make the budget a lie by doubling the text.
        print(record["text"])
        return 0

    json.dump(record, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
