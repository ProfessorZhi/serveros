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
                                   materialize the `boards` projection

The third one is worth pausing on, because it is the only entry this gate has
ever had to give back, and what it turned out to be was not a missing program.

The first reading was: the state-root contract lists `boards` as
`class: projection` and names its producer; there is no such command, no
script, no file anywhere by that name; the namespace is `required: false`, so
it reads as `NO_BOARDS` — "no boards have been made yet" — when the truth is
that no program can make one.

That reading was wrong in the way this round keeps finding wrong: a correct
observation of a surface, promoted to a conclusion about the world. Two
programs on this node are called `serverosctl`:

    ~/.local/bin/serverosctl -> .../serveros-linux-work/current/bin/serverosctl
        THIS repository's node CLI. No `board`, and never had one.
    envs/python/serveros-control-plane/bin/serverosctl
        the work-engine control plane, whose `pyproject.toml` declares
        `serverosctl = "workengine.cli:main"` alongside `workengine` — and
        whose argparse prog is literally `serverosctl`. It has `board render`.

PATH gives the name to the first. `board render` belongs to the second, and
it wrote every file under `state/serveros/boards/`. The declaration was not
naming a command that does not exist; it was naming a command of a different
distribution without saying so, which on this node is indistinguishable from
naming nothing.

So the entry was closed as REDECLARED — not by building the command, and not
by deleting the claim: the contract now names the ability that owns the
projection and the control-plane command beside it. The entry is gone from
`KNOWN_ABSENT` because the exemption's evidence is gone. This gate's own
rules did that, and they did it without being asked: the moment the
declaration stopped containing the literal string, this file went red and
said "remove the entry".

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
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The declared-but-absent commands, each with what it would take to close it.
KNOWN_ABSENT = {
    # `available` was the first entry here, with disposition REDECLARED: a
    # startup verb `docs/capability-discovery.md` sketched, never built, whose
    # surface became `context startup`. `test_the_exemption_list_cannot_outlive_
    # its_evidence` removed it on 2026-09-22, which is the mechanism working:
    # the document stopped containing the literal string, and the exemption for
    # it became a claim about a sentence that is no longer written. An
    # exemption list that keeps entries nobody can find is a list where the
    # live entries stop being read.
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
}

# Where a declaration may appear. Contracts state interfaces; skills and docs
# tell a reader what to type. All three are promises to the same reader.
#
# `workbench/` was the fourth and was missing, which is the shape this file
# exists to catch rather than an example of it. The set was hand-listed, and a
# hand-listed set is a declaration like any other: it can drift from the thing
# it describes, and the drift is silent, because a directory nobody added is
# not merely unscanned — it is invisible to a scan that iterates this tuple.
# The pages under `workbench/` are the ones a reader is handed first, so a
# command that is wrong there is wrong in the most expensive place, and the
# gate stayed green while they were outside its reach.
#
# Two tests carry the control:
# `test_the_workbench_surface_is_inside_the_scan` fails if the line below is
# removed, and `test_a_workbench_page_declaring_an_absent_command_is_caught`
# fails if the scanner goes blind. Without them, adding a directory here would
# be an unverifiable claim that it is now covered.
# Product `.py` files are the fifth surface and the last one added, and the
# reason is a measured defect rather than symmetry. `serverosctl architecture
# project` was named to readers from two Python sources — a comment
# `skills/serveros/scripts/map_projection.py` emits into every map it cannot
# anchor, and `architecture_check.py`'s `refresh_via` on a finding — and the
# verb did not exist. The gate above was green throughout, because its set was
# Markdown and YAML and the declaration was a string in a `.py`. That is the
# `workbench/` story again one level down: not a directory nobody added, but a
# FILE TYPE nobody thought of, and a file type is the same kind of blind spot
# because the scan iterates this tuple.
#
# `tests/` is excluded, and that is a decision rather than an omission: a test
# file is not a promise to a reader, and the controls in this very file name
# commands that do not exist on purpose (`serverosctl work frobnicate` is the
# positive control below). Scanning tests would turn every control green.
#
# The single-word form is required to have two words in a `.py`, which is the
# one place this scanner is deliberately narrower than it is for a document.
# Measured on 2026-09-22: across every product `.py` there are 36 two-word
# declarations and all 36 are real, plus one single-word false positive —
# `runtime/cli.py`'s ``"…run by serverosctl on {device_ref}"``, prose that
# reads as a command and is not one. A gate that reports a sentence is a gate
# somebody adds to the exemption list, and an exemption list that grows for
# prose is how the real entries stop being read.
SCANNED = tuple(
    sorted(p for p in REPO.glob("*/*.yaml"))
    + sorted(p for p in REPO.glob("skills/**/*.md"))
    + sorted(p for p in REPO.glob("docs/*.md"))
    + sorted(p for p in REPO.glob("workbench/*.md"))
    + sorted(p for p in REPO.glob("*/*.py") if "tests" not in p.parts)
    + sorted(p for p in REPO.glob("skills/**/*.py"))
)

_REF = re.compile(r"serverosctl\s+([a-z][a-z0-9-]*)(?:\s+([a-z][a-z0-9-]*))?")

# In a `.py` the subcommand is required. See `SCANNED` for the measured false
# positive that made this a rule rather than an oversight.
_REF_CODE = re.compile(r"serverosctl\s+([a-z][a-z0-9-]*)\s+([a-z][a-z0-9-]*)")

# The elided form: `serverosctl a b | c d | e f`. Only the first segment
# carries the program name, and the rest are just as much a promise to the
# reader — the pointer line in `disclosure-contract.yaml` uses exactly this
# notation, which is where this file's originating defect was hiding. A
# scanner that missed it would have passed the very line it was written to
# catch.
_BACKTICKED = re.compile(r"`([^`]*serverosctl[^`]*)`")
_BARE_COMMAND = re.compile(r"^([a-z][a-z0-9-]*)(?:\s+([a-z][a-z0-9-]*))?")
# The same two-word rule as `_REF_CODE`, applied to the segments after a `|`.
# Measured: the only elided form in a product `.py` is `runtime/daemon.py`'s
# `` `serverosctl lease declare|release` ``, where the second segment is a
# SUFFIX alternative — the two commands meant are `lease declare` and `lease
# release`, not a top-level `release`. The `a b | c d` convention this scanner
# learned from `disclosure-contract.yaml` is a document convention, and in code
# prose the same glyph is a shorthand for "the verb before, or the verb after".
# Requiring the subcommand keeps the real elision caught without reading a
# suffix as a command.
_BARE_CODE = re.compile(r"^([a-z][a-z0-9-]*)\s+([a-z][a-z0-9-]*)")


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


def declared_commands(paths=None) -> dict[tuple[str, ...], list[str]]:
    """Every `serverosctl ...` a reader is told to run, and where.

    `paths` exists so a control can point the same scanner at a file it wrote
    itself. A scanner that can only be run against the corpus it is supposed to
    judge cannot be shown to work: the only evidence available is that it found
    nothing wrong, which is also what a broken one reports.
    """
    found: dict[tuple[str, ...], list[str]] = {}
    for path in (SCANNED if paths is None else paths):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            rel = str(path.relative_to(REPO))
        except ValueError:
            # A control's own file is not under REPO. Naming it by its real
            # path keeps the report useful instead of raising here.
            rel = str(path)

        def record(verb: str, sub: str | None, *, at: str):
            parts = (verb,) if sub is None else (verb, sub)
            found.setdefault(parts, []).append(at)

        for match in (_REF_CODE if path.suffix == ".py" else _REF).finditer(text):
            record(match.group(1), match.group(2), at=rel)

        # Now the segments after a `|`, which have no `serverosctl` in front
        # of them and are nonetheless the same instruction.
        for span in _BACKTICKED.finditer(text):
            for segment in span.group(1).split("|")[1:]:
                bare = (_BARE_CODE if path.suffix == ".py"
                        else _BARE_COMMAND).match(segment.strip())
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

        Two declarations in the repo right now name commands that do not
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

    def test_the_workbench_surface_is_inside_the_scan(self):
        """The structural half of the workbench control.

        `workbench/*.md` was outside `SCANNED`, and the way that failed is not
        that the gate said the wrong thing — it said nothing, because a
        directory the tuple does not name is invisible to a scan that iterates
        the tuple. The pages are generated, so nobody hand-edits a bad command
        into them today; the point is that a generator change can, and the
        gate's job is to be looking when it does.

        This asserts the surface is non-empty and that every one of its files
        is in the scan. Removing `workbench/*.md` from `SCANNED` turns this
        red, which is what makes the line above it a check rather than a
        comment.
        """
        pages = sorted(REPO.glob("workbench/*.md"))
        self.assertTrue(pages,
                        "workbench/ has no pages; this control proves nothing "
                        "and the surface it guards has moved")
        missing = [str(p.relative_to(REPO)) for p in pages if p not in SCANNED]
        self.assertEqual(missing, [],
                         "these workbench pages are outside SCANNED, so a "
                         "command they name could be absent and the gate would "
                         "never look: " + ", ".join(missing))

    def test_a_workbench_page_declaring_an_absent_command_is_caught(self):
        """The behavioural half: point the same scanner at a page that lies.

        A positive control is the only evidence that separates "found nothing
        wrong" from "cannot see". This writes a page naming `serverosctl work
        frobnicate`, which no parser accepts, and asserts the scanner reports
        it — the same judgement the first test in this class makes, applied to
        a document this test wrote rather than to one the repository happens to
        contain.

        The name is deliberately not in `KNOWN_ABSENT`, so the result cannot be
        excused by an exemption.
        """
        self.assertNotIn(("work", "frobnicate"), real_commands())

        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "FABRICATED.md"
            page.write_text(
                "# a page that tells a reader to run something that is not there\n"
                "\n"
                "```\n"
                "serverosctl work frobnicate --json\n"
                "```\n"
                "`serverosctl work | alert list | document resolve`\n",
                encoding="utf-8")
            found = declared_commands(paths=[page])

        self.assertIn(("work", "frobnicate"), found,
                      "the scanner is blind to a plain `serverosctl a b` line")
        # And to the elided form, which is how the original defect hid.
        self.assertIn(("alert", "list"), found,
                      "the scanner missed the segments after a `|`")

    def test_the_product_source_surface_is_inside_the_scan(self):
        """The structural half of the `.py` control.

        The declaration that motivated adding it — `serverosctl architecture
        project`, named by `map_projection.py`'s generated comment and by
        `architecture_check.py`'s `refresh_via` — was a string in a Python
        file, and the gate above scanned no Python file. Removing
        `*/*.py` from `SCANNED` turns this red, which is what makes that line
        a check rather than a comment.
        """
        sources = sorted(
            p for p in (list(REPO.glob("*/*.py")) + list(REPO.glob("skills/**/*.py")))
            if "tests" not in p.parts)
        self.assertTrue(sources, "no product .py found; this control proves nothing")
        missing = [str(p.relative_to(REPO)) for p in sources if p not in SCANNED]
        self.assertEqual(missing, [],
                         "these product sources are outside SCANNED, so a "
                         "command they name could be absent and the gate would "
                         "never look: " + ", ".join(missing[:10]))
        # The exclusion is a decision with a reason, and the reason has to keep
        # being true: a test file that names an absent command is a CONTROL,
        # and scanning the control surface would turn every control green.
        self.assertNotIn("tests", {p.parts[0] for p in SCANNED if len(p.parts) > 1})

    def test_a_source_file_declaring_an_absent_command_is_caught(self):
        """The behavioural half, and its negative control in the same test.

        Both halves are needed and they pull in opposite directions. The
        scanner must see a command named in a string in a `.py`, because that
        is where the real defect was; it must NOT see `serverosctl on
        {device_ref}`, because that is prose in `runtime/cli.py` that reads
        like a command and is not one — and a gate that reports a sentence is a
        gate somebody silences by adding an exemption. The measured shape of
        the product source is 36 two-word declarations, all real, plus that one
        sentence, which is why the `.py` rule requires the subcommand.

        The prose line is written AFTER the elided one on purpose. `_BACKTICKED`
        pairs an opening backtick with the next one however far away it is, and
        `[^`]*` crosses newlines — measured, this happens in 17 of the scanned
        documents, so a single-line rule would silently drop declarations and is
        not a change to make here. What it means for this fixture is that a line
        whose backticks do not close before the next line's would swallow it.
        Ordered the other way, `PROSE`'s first backtick pairs with the one
        opening `ELIDED`, the span contains `serverosctl`, and the elision ends
        up inside it rather than after it.
        """
        self.assertNotIn(("work", "frobnicate"), real_commands())

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "fabricated.py"
            source.write_text(
                'HELP = "run `serverosctl work frobnicate --json` to do it"\n'
                'ELIDED = "`serverosctl work | alert list | document resolve`"\n'
                'PROSE = f"`{program} --version` run by serverosctl on {ref}"\n',
                encoding="utf-8")
            found = declared_commands(paths=[source])

        self.assertIn(("work", "frobnicate"), found,
                      "the scanner is blind to a command named in a `.py`")
        self.assertIn(("alert", "list"), found,
                      "the scanner missed a real elision in a `.py`")
        self.assertNotIn(("on",), found,
                         "the scanner read prose as a command; that is the "
                         "false positive the `.py` rule exists to prevent")

    def test_the_verb_this_surface_was_added_for_is_declared_and_real(self):
        """The defect itself, kept as a regression guard.

        `serverosctl architecture project` was named to readers from two Python
        sources and did not exist. It exists now, as a wrapper over the checker
        that already implemented it, and this asserts both halves: the
        declaration is present in the tree where a reader is told to run it,
        AND the parser accepts it. Either half alone passes while the defect is
        live — a declaration with no verb is the old state, and a verb nobody
        is told about is a capability with no surface.
        """
        declared = declared_commands()
        self.assertIn(("architecture", "project"), declared)
        self.assertIn(("architecture", "check"), declared)
        self.assertIn(("architecture", "project"), real_commands())
        self.assertIn(("architecture", "check"), real_commands())
        # And it is still named by the checker that emits `refresh_via`, so a
        # future rename of the verb has to move this string too.
        checker = (REPO / "skills/serveros/scripts/architecture_check.py").read_text(
            encoding="utf-8")
        self.assertIn("serverosctl architecture project", checker)

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
