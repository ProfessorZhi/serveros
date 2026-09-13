#!/usr/bin/env python3
"""A declaration that names a CLI surface must name one that exists.

WHY THIS FILE EXISTS
--------------------
The pathology this round keeps meeting — `declared-but-absent` — has a shape
that is easy to state and hard to notice: the declaration layer runs ahead of
the data or code layer, nothing detects the gap, and an empty branch never
errors. A contract is written, a document is written, both read as settled,
and the thing they name was never built.

Every instance found so far was found by accident. This file is one of them
made deliberate, for the smallest and most checkable class of the pathology:
a document or contract that tells a reader to run `serverosctl <a> <b>`.

This test found three, all predating the round:

    serverosctl available          docs/capability-discovery.md
    serverosctl context attach     context/schema.yaml, context/resolver.py
    serverosctl board render       fleet/state-root-contract.yaml — as a
                                   `producer:`, i.e. the thing declared to
                                   materialize the `boards` projection, which
                                   is produced by nothing

`board render` is the one worth pausing on. The state-root contract lists
`boards` as `class: projection` and names its producer. There is no such
command, no script, and no file anywhere by that name. The namespace is
`required: false`, so it reads as `NO_BOARDS` — "no boards have been made
yet" — when the truth is that no program can ever make one.

WHAT THIS GATE DOES NOT DO
--------------------------
It does not fail on those three. A gate that is red on the day it is written
is a gate that gets turned off, and one that gets turned off catches nothing.
Instead each is listed below WITH A REASON, the list is asserted to be exactly
as long as the evidence for it, and adding an entry is a deliberate edit to a
named constant rather than a comment somebody scrolls past.

Two properties keep the list from becoming a place where findings go to be
forgotten:

  * every entry must still be declared somewhere in the repo. Delete the
    declaration and this test fails, so the exemption cannot outlive it.
  * every entry must carry a `because` and a `disposition`. "We know" is not
    an answer; "we know and here is what would fix it" is.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The declared-but-absent commands, each with what it would take to close it.
KNOWN_ABSENT = {
    "available": {
        "because": "`docs/capability-discovery.md` sketched a startup verb that "
                   "was never built; the surface that renders it is "
                   "`context startup`, and the document now says so.",
        "declared_at": ("docs/capability-discovery.md",),
        "disposition": "REDECLARED",
    },
    "context attach": {
        "because": "declared in `context/schema.yaml` as the mechanism by which "
                   "a Run becomes writable to an execution, and referenced by "
                   "`context/resolver.py`. No implementation, so `runs.writable` "
                   "is false for every Run on the strength of a verb that does "
                   "not exist — which is defensible, and is not what the "
                   "contract says is happening.",
        "declared_at": ("context/schema.yaml",),
        "disposition": "NEEDS_HUMAN",
    },
    "board render": {
        "because": "declared as the `producer:` of the `boards` projection in "
                   "`fleet/state-root-contract.yaml`. Nothing produces it, so "
                   "the namespace reads as NO_BOARDS rather than as a missing "
                   "producer.",
        "declared_at": ("fleet/state-root-contract.yaml",),
        "disposition": "NEEDS_HUMAN",
    },
}

# Where a declaration may appear. Contracts state interfaces; skills and docs
# tell a reader what to type. All three are promises to the same reader.
SCANNED = tuple(
    sorted(p for p in REPO.glob("*/*.yaml"))
    + sorted(p for p in REPO.glob("skills/**/*.md"))
    + sorted(p for p in REPO.glob("docs/*.md"))
)

_REF = re.compile(r"serverosctl\s+([a-z][a-z0-9-]*)(?:\s+([a-z][a-z0-9-]*))?")

# The elided form: `serverosctl a b | c d | e f`. Only the first segment
# carries the program name, and the rest are just as much a promise to the
# reader — the pointer line in `disclosure-contract.yaml` uses exactly this
# notation, which is where this file's originating defect was hiding. A
# scanner that missed it would have passed the very line it was written to
# catch.
_BACKTICKED = re.compile(r"`([^`]*serverosctl[^`]*)`")
_BARE_COMMAND = re.compile(r"^([a-z][a-z0-9-]*)(?:\s+([a-z][a-z0-9-]*))?")


def real_commands() -> set[tuple[str, ...]]:
    """Every subcommand the parser actually accepts, walked from the parser
    itself. A hand-kept list here would be one more declaration that can
    drift from the thing it describes."""
    sys.path.insert(0, str(REPO / "runtime"))
    sys.dont_write_bytecode = True
    if "cli" in sys.modules:
        cli = sys.modules["cli"]
    else:
        spec = importlib.util.spec_from_file_location("cli", REPO / "runtime" / "cli.py")
        cli = importlib.util.module_from_spec(spec)
        sys.modules["cli"] = cli
        spec.loader.exec_module(cli)

    found: set[tuple[str, ...]] = set()

    def walk(parser, prefix: tuple[str, ...]):
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    found.add(prefix + (name,))
                    walk(sub, prefix + (name,))

    walk(cli.build_parser(), ())
    return found


def declared_commands() -> dict[tuple[str, ...], list[str]]:
    """Every `serverosctl ...` a reader is told to run, and where."""
    found: dict[tuple[str, ...], list[str]] = {}
    for path in SCANNED:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(REPO))

        def record(verb: str, sub: str | None, *, at: str):
            parts = (verb,) if sub is None else (verb, sub)
            found.setdefault(parts, []).append(at)

        for match in _REF.finditer(text):
            record(match.group(1), match.group(2), at=rel)

        # Now the segments after a `|`, which have no `serverosctl` in front
        # of them and are nonetheless the same instruction.
        for span in _BACKTICKED.finditer(text):
            for segment in span.group(1).split("|")[1:]:
                bare = _BARE_COMMAND.match(segment.strip())
                if bare:
                    record(bare.group(1), bare.group(2), at=f"{rel} (after `|`)")
    return found


class DeclaredCommandsExist(unittest.TestCase):
    def test_every_declared_command_is_real_or_a_recorded_gap(self):
        real = real_commands()
        undeclared: list[str] = []
        for parts, where in sorted(declared_commands().items()):
            if parts in real:
                continue
            if " ".join(parts) in KNOWN_ABSENT:
                continue
            undeclared.append(f"serverosctl {' '.join(parts)} — cited in "
                              f"{', '.join(sorted(set(where)))}")
        self.assertEqual(undeclared, [], "\n".join(
            ["a document tells the reader to run a command that does not exist:",
             *undeclared,
             "",
             "Either build it, or fix the document, or add it to KNOWN_ABSENT",
             "with a `because` and a `disposition`."]))

    def test_each_recorded_gap_says_what_would_close_it(self):
        """"We know about it" is not a disposition. Every entry names why it is
        absent and what kind of action closes it, so the next reader does not
        have to re-derive either."""
        for name, entry in KNOWN_ABSENT.items():
            self.assertTrue(entry.get("because"), f"{name} has no because")
            self.assertIn(entry.get("disposition"),
                          {"NEEDS_HUMAN", "REDECLARED", "DEFER"},
                          f"{name} has no usable disposition")

    def test_the_exemption_list_cannot_outlive_its_evidence(self):
        """The property that stops this constant becoming a graveyard. If the
        declaration is removed, the entry must be removed with it — otherwise
        the list slowly fills with things nobody declares any more, and stops
        meaning "these are the real gaps"."""
        declared = {" ".join(parts) for parts in declared_commands()}
        for name in KNOWN_ABSENT:
            self.assertIn(name, declared,
                          f"{name} is exempted but no longer declared anywhere; "
                          "remove the entry")

    def test_a_gap_entry_that_has_since_been_built_is_a_failure(self):
        """The other direction, and the one that matters for a list like this.
        Somebody building `context attach` should be told to delete its
        exemption, because an exemption that survives its gap is how a reader
        comes to believe a working command is broken."""
        real = real_commands()
        stale = [name for name in KNOWN_ABSENT
                 if tuple(name.split()) in real]
        self.assertEqual(stale, [], "\n".join(
            ["these commands now exist; delete their KNOWN_ABSENT entries:", *stale]))

    def test_the_scanner_actually_finds_declarations(self):
        """A guard against the whole file passing because the regex stopped
        matching. Test files are checked against a known-present command, so
        this fails loudly if the scan goes blind rather than quietly finding
        nothing wrong."""
        found = declared_commands()
        self.assertGreater(len(found), 5, sorted(found))
        self.assertIn(("context", "current"), found)
        self.assertIn(("ability", "list"), found)

    def test_the_gate_is_not_vacuous(self):
        """A positive control, because every assertion in this file is of the
        form "nothing bad was found" and that is exactly the shape a broken
        checker produces.

        Three declarations in the repo right now name commands that do not
        exist. If the scanner stopped seeing them, `test_every_declared...`
        would still pass — it would simply have nothing left to object to.
        """
        declared = declared_commands()
        real = real_commands()
        for name in KNOWN_ABSENT:
            parts = tuple(name.split())
            self.assertIn(parts, declared,
                          f"the scanner has gone blind to {name}")
            self.assertNotIn(parts, real,
                             f"{name} resolves after all; the control is stale")

    def test_the_contracts_own_pointer_line_names_real_commands(self):
        """The specific instance that started this file. The startup view's
        pointer line named `capability list` and `capability resolve`, neither
        of which has ever existed — in a contract written the same day, by the
        same hand that wrote the commands it was describing."""
        import yaml
        contract = yaml.safe_load(
            (REPO / "context/disclosure-contract.yaml").read_text(encoding="utf-8"))
        text = " ".join(str(line) for line in contract["startup_view"]["lines"])
        for match in _REF.finditer(text):
            parts = (match.group(1),) if match.group(2) is None else (
                match.group(1), match.group(2))
            self.assertIn(parts, real_commands(),
                          f"the startup pointer names `serverosctl "
                          f"{' '.join(parts)}`, which does not exist")


if __name__ == "__main__":
    unittest.main()
