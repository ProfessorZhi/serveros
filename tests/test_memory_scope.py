#!/usr/bin/env python3
"""§18 — the recall firewall: who may read a record, and how a refusal is told
apart from an absence.

Four things are asserted, and the third is the one the other three exist for:

  1. The transcription — the contract's `scope` block against the kernel.
  2. Each refusal fires, with the reason named and the other reasons NOT named.
  3. A refusal is never an empty answer. `heads()` and `current_value()` RAISE
     for a subject this reader may not have, and return `None` / `[]` for a
     subject nobody recorded. The prose rule ("cross-Item recall is refused by
     default") was satisfiable by a `if False: pass`; the difference between
     "refused" and "empty" is what makes it not satisfiable by one.
  4. There is no read that skips the firewall by not mentioning it: `reader` is
     required, keyword-only, and has no default, so the omission is a
     `TypeError` at the call site rather than an admitted read.

Why the refusal has to be an exception
--------------------------------------
`docs/memory-fabric.md` says the ledger records what was WRITTEN and never what
was READ, and that stays true. But a read that returns nothing tells a caller
"this Item has no memory of that", which is a true sentence about the firewall
and a false sentence about the ledger — and the caller will believe the second.
So the refusal is raised, it carries the reason and the ids it refused, and it
says which of the three repairs applies.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MEMORY = REPO / "memory"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


record = _load("serveros_memory_record", MEMORY / "record.py")
scope = _load("serveros_memory_scope", MEMORY / "scope.py")
projection = _load("serveros_memory_projection", MEMORY / "projection.py")

import yaml  # noqa: E402

CONTRACT = yaml.safe_load((MEMORY / "record-contract.yaml").read_text(encoding="utf-8"))

SERVEROS = "project/serveros/SRV-0007"
LITELLM_0101 = "project/litellm/LIT-0101"
LITELLM_0205 = "project/litellm/LIT-0205"
A_DEVICE = "device:server-beijing"


def a_record(**over):
    base = {
        "id": "mem:the-tunnel-is-task-scoped",
        "kind": "decision",
        "subject": SERVEROS,
        "at": "2026-09-13T04:00:00Z",
        "by": "claude-code",
        "source": "records/projects/serveros/items/SRV-0007/design.md",
        "revision": "ad06b11",
        "body": "a tunnel belongs to the task, not to the session that opened it",
    }
    base.update(over)
    return base


def refused(record_dict) -> list[str]:
    try:
        record.validate(record_dict)
    except record.RecordRefused as exc:
        return exc.reasons
    return []


# ---------------------------------------------------------------------------
# The transcription
# ---------------------------------------------------------------------------


class ContractAgreementTests(unittest.TestCase):
    def test_the_scopes_are_the_contracts_scopes(self):
        self.assertEqual(list(scope.SCOPES), list(CONTRACT["scope"]["values"]))

    def test_the_default_is_the_contracts_default(self):
        self.assertEqual(scope.DEFAULT_SCOPE, CONTRACT["scope"]["default"])

    def test_the_scope_field_is_a_declared_optional_field(self):
        """§18's promotion lives in a field `validate()` already knows about.
        An undeclared field is refused by `validate`, so a promotion written
        without this line would be refused — which is the right failure, and it
        is only the right failure because the field is declared here too."""
        declared = [str(line).split("#")[0].strip()
                    for line in CONTRACT["optional_fields"]]
        self.assertIn("scope", declared)
        self.assertIn("scope", record.OPTIONAL)

    def test_the_refusals_are_the_contracts_refusals(self):
        self.assertEqual(set(CONTRACT["scope"]["refuses"]), set(scope.REASONS))

    def test_every_refusal_says_what_to_do_about_it(self):
        """A refusal with no repair is a wall. Each of these names a different
        action, which is the whole reason there are three words rather than
        one — this repository adds a word when the repair differs, and only
        then."""
        for word, why in scope.REASONS.items():
            self.assertIn("Repair:", why, word)
            self.assertIn(word, CONTRACT["scope"]["refuses"], word)

    def test_the_enforcement_names_a_module_that_exists(self):
        self.assertEqual(CONTRACT["scope"]["enforcement"], "memory/scope.py")
        self.assertEqual(CONTRACT["enforcement"]["recall"].split("—")[0].strip(),
                         "`memory/scope.py")

    def test_the_read_rule_is_the_contracts_read_rule(self):
        rule = " ".join(CONTRACT["rules"])
        self.assertIn("Cross-Item recall is refused by default", rule)
        self.assertIn("the promotion is the consent", rule)
        self.assertIn("raised rather than returned as an empty answer", rule)


# ---------------------------------------------------------------------------
# The admission rule on the way IN
# ---------------------------------------------------------------------------


class ValidateTests(unittest.TestCase):
    def test_an_undeclared_scope_value_is_refused_rather_than_defaulted(self):
        """A typo'd promotion read as the default would be a silent demotion,
        and a silent demotion is a fact somebody believes is shared."""
        reasons = refused(a_record(scope="progect"))
        self.assertTrue(any("progect" in r for r in reasons), reasons)
        self.assertTrue(any("silent demotion" in r for r in reasons), reasons)

    def test_the_three_scopes_are_accepted(self):
        for value in scope.SCOPES:
            self.assertEqual(refused(a_record(scope=value)), [], value)

    def test_an_absent_scope_is_the_default_and_not_a_refusal(self):
        self.assertEqual(refused(a_record()), [])
        self.assertEqual(scope.scope_of(a_record()), "item")

    def test_a_project_scope_on_a_subject_with_no_project_is_refused(self):
        """`device:` names no project, so `scope: project` there asks for a
        reach that no reader can be checked against."""
        reasons = refused(a_record(subject=A_DEVICE, scope="project"))
        self.assertTrue(any("names no project" in r for r in reasons), reasons)
        self.assertEqual(refused(a_record(subject=A_DEVICE, scope="user")), [])
        self.assertEqual(refused(a_record(subject=A_DEVICE)), [])


# ---------------------------------------------------------------------------
# The decision, unit by unit
# ---------------------------------------------------------------------------


class VerdictTests(unittest.TestCase):
    def thread(self, item_ref):
        return scope.thread(item_ref)

    def test_a_thread_may_read_its_own_items_subject(self):
        answer = scope.verdict(a_record(), self.thread("SRV-0007"))
        self.assertTrue(answer["admitted"])
        self.assertEqual(answer["scope"], "item")

    def test_a_thread_may_not_read_another_items_subject(self):
        answer = scope.verdict(a_record(subject=LITELLM_0205), self.thread("LIT-0101"))
        self.assertFalse(answer["admitted"])
        self.assertEqual(answer["reason"], "CROSS_ITEM_RECALL")

    def test_the_same_item_in_another_project_is_still_another_item(self):
        """An Item id is not globally unique and the reader is a token, not a
        ref — so `LIT-0101` reading `project/litellm/LIT-0101` is reading its
        own subject because the SUBJECT names the project, not the reader."""
        answer = scope.verdict(a_record(subject=LITELLM_0101), self.thread("LIT-0101"))
        self.assertTrue(answer["admitted"])

    def test_a_promoted_record_is_readable_across_items_of_the_same_project(self):
        reader = scope.thread("LIT-0101", project_ref="litellm")
        answer = scope.verdict(a_record(subject=LITELLM_0205, scope="project"), reader)
        self.assertTrue(answer["admitted"])

    def test_a_promoted_record_is_not_readable_from_another_project(self):
        reader = scope.thread("SRV-0007", project_ref="serveros")
        answer = scope.verdict(a_record(subject=LITELLM_0205, scope="project"), reader)
        self.assertFalse(answer["admitted"])
        self.assertEqual(answer["reason"], "CROSS_ITEM_RECALL")

    def test_a_record_promoted_to_the_node_is_readable_from_any_item(self):
        reader = scope.thread("SRV-0007", project_ref="serveros")
        answer = scope.verdict(a_record(subject=LITELLM_0205, scope="user"), reader)
        self.assertTrue(answer["admitted"])

    def test_an_unbound_thread_reads_only_what_was_promoted_to_the_node(self):
        """A coordinator crosses Items by definition (§17), so it is a legal
        reader with no Item — and this is the boundary that survives that: it
        gets node-wide promotions and nothing Item-scoped."""
        reader = {"kind": "thread", "item_ref": None, "session_ref": "s1",
                  "project_ref": None}
        answer = scope.verdict(a_record(), reader)
        self.assertEqual(answer["reason"], "UNBOUND_READER")
        self.assertTrue(scope.verdict(a_record(scope="user"), reader)["admitted"])

    def test_a_device_fact_is_not_recallable_by_a_thread(self):
        answer = scope.verdict(a_record(subject=A_DEVICE), self.thread("SRV-0007"))
        self.assertEqual(answer["reason"], "UNPROMOTED_SUBJECT")

    def test_a_device_fact_promoted_to_the_node_is(self):
        answer = scope.verdict(a_record(subject=A_DEVICE, scope="user"),
                               self.thread("SRV-0007"))
        self.assertTrue(answer["admitted"])

    def test_the_operator_reads_everything(self):
        for over in ({}, {"subject": LITELLM_0205}, {"subject": A_DEVICE},
                     {"subject": LITELLM_0205, "scope": "project"}):
            self.assertTrue(
                scope.verdict(a_record(**over), scope.OPERATOR_READING)["admitted"],
                over)

    def test_a_reader_this_module_cannot_name_is_refused_rather_than_admitted(self):
        """The failure mode this prevents is a third reader kind arriving as a
        typo and falling through every branch to `admitted`."""
        with self.assertRaises(scope.ScopeRefused):
            scope.verdict(a_record(), {"kind": "thread-", "item_ref": "SRV-0007"})

    def test_a_project_scope_on_a_projectless_subject_says_so(self):
        """Unreachable through `validate` — see ValidateTests — and reachable
        by anyone holding an unvalidated record, which is everyone who reads a
        ledger before auditing it."""
        answer = scope.verdict(a_record(subject=A_DEVICE, scope="project"),
                               self.thread("SRV-0007"))
        self.assertEqual(answer["reason"], "UNPROMOTED_SUBJECT")
        self.assertIn("`validate()` refuses", answer["detail"])


# ---------------------------------------------------------------------------
# The reader
# ---------------------------------------------------------------------------


class ReaderTests(unittest.TestCase):
    def test_the_operator_is_the_word_and_is_a_named_value(self):
        self.assertEqual(scope.parse_reader("operator"), scope.OPERATOR_READING)
        self.assertEqual(scope.OPERATOR_READING["kind"], "operator")

    def test_a_bare_item_ref_is_a_thread_bound_to_that_item(self):
        reader = scope.parse_reader("SRV-0007")
        self.assertEqual(reader["kind"], "thread")
        self.assertEqual(reader["item_ref"], "SRV-0007")
        self.assertIsNone(reader["project_ref"])

    def test_a_reader_may_state_its_project_by_writing_the_subject_form(self):
        reader = scope.parse_reader("project/serveros/SRV-0007")
        self.assertEqual(reader["item_ref"], "SRV-0007")
        self.assertEqual(reader["project_ref"], "serveros")

    def test_a_reader_that_is_not_an_item_ref_is_refused(self):
        """Nothing here is a near-miss that could be read two ways. `a/b/c/d`
        is the interesting one: three slashes is a subject with a stray token,
        not an Item ref, and the count is what says so."""
        for bad in ("", "  ", "project/serveros", "a/b/c/d", "not a ref"):
            with self.assertRaises(scope.ScopeRefused, msg=bad):
                scope.parse_reader(bad)

    def test_a_reader_with_no_project_finds_it_in_the_ledger(self):
        """The Item's project is a fact the ledger already holds, so a reader
        that names only an Item does not have to restate it — and gets the
        answer the ledger gives rather than one this module invented."""
        records = [a_record(subject=LITELLM_0101),
                   a_record(id="mem:other", subject=LITELLM_0205, scope="project")]
        reading = scope.recall(records, LITELLM_0205, reader=scope.thread("LIT-0101"))
        self.assertTrue(reading["recalled"], reading["refused"])

    def test_a_reader_whose_item_is_in_two_projects_is_told_to_say_which(self):
        """`LIT-0101` under two projects is not a contrived ledger: an Item id
        is unique inside a project, and nothing upstream promises it is unique
        across them. Resolving to `litellm` here would be this module picking
        one of two answers on the reader's behalf."""
        records = [
            a_record(subject=LITELLM_0101),
            a_record(id="mem:elsewhere", subject="project/other/LIT-0101"),
            a_record(id="mem:other", subject=LITELLM_0205, scope="project"),
        ]
        reading = scope.recall(records, LITELLM_0205, reader=scope.thread("LIT-0101"))
        self.assertFalse(reading["recalled"])
        self.assertIn("did not name a project", reading["refused"][0]["detail"])

    def test_a_reader_that_stated_its_project_is_not_overruled_by_the_ledger(self):
        """A reader that said where it works keeps that answer even when the
        ledger places its Item elsewhere, because the alternative is that the
        same call means different things on two devices holding different
        ledgers."""
        records = [a_record(subject=LITELLM_0101),
                   a_record(id="mem:other", subject=LITELLM_0205, scope="project")]
        reader = scope.thread("LIT-0101", project_ref="serveros")
        self.assertFalse(scope.recall(records, LITELLM_0205, reader=reader)["recalled"])


# ---------------------------------------------------------------------------
# The read path — refusal is not absence
# ---------------------------------------------------------------------------


class ReadPathTests(unittest.TestCase):
    def test_a_cross_item_read_is_refused_and_not_empty(self):
        """The headline rule, and the one an empty answer would silently
        satisfy. LIT-0101's thread reads LIT-0205's subject and gets a refusal
        that names the Item it is not entitled to — the incident this is
        against is a session that finished LIT-0101 and began LIT-0205's work
        in the same conversation."""
        ledger = [a_record(subject=LITELLM_0205, body="LIT-0205 context")]
        with self.assertRaises(scope.RecallRefused) as caught:
            record.heads(ledger, LITELLM_0205, reader=scope.thread("LIT-0101"))
        self.assertEqual(caught.exception.reason, "CROSS_ITEM_RECALL")
        self.assertIn("LIT-0101", str(caught.exception))
        self.assertIn("Repair:", str(caught.exception))

    def test_a_subject_nobody_recorded_is_an_empty_answer_and_not_a_refusal(self):
        """An empty ledger about a subject answers the same way for every
        reader, including one who would have been refused had there been
        anything there. Nothing was refused and nothing was withheld, so there
        is no refusal to raise — the two cases are told apart by whether the
        window held records, not by which answer the caller preferred."""
        self.assertEqual(record.heads([], SERVEROS, reader=scope.thread("SRV-0007")), [])
        self.assertEqual(record.heads([], LITELLM_0205, reader=scope.thread("LIT-0101")), [])
        self.assertIsNone(record.current_value([], SERVEROS,
                                              reader=scope.thread("SRV-0007")))

    def test_a_mixed_subject_gives_the_reader_what_it_is_entitled_to(self):
        """A promoted record and an Item-scoped one under one subject is
        ordinary, and the scope means the reader gets one and not the other —
        which is different from the subject being closed to it."""
        ledger = [a_record(subject=LITELLM_0205, id="mem:promoted", scope="project"),
                  a_record(subject=LITELLM_0205, id="mem:private")]
        found = record.heads(ledger, LITELLM_0205,
                             reader=scope.thread("LIT-0101", project_ref="litellm"))
        self.assertEqual([r["id"] for r in found], ["mem:promoted"])

    def test_a_subject_closed_entirely_to_the_reader_refuses(self):
        ledger = [a_record(subject=LITELLM_0205, id="mem:private")]
        with self.assertRaises(scope.RecallRefused):
            record.heads(ledger, LITELLM_0205,
                         reader=scope.thread("LIT-0101", project_ref="litellm"))

    def test_an_operator_read_is_admitted(self):
        ledger = [a_record(subject=LITELLM_0205)]
        self.assertEqual(len(record.heads(ledger, LITELLM_0205,
                                         reader=scope.OPERATOR_READING)), 1)

    def test_the_window_is_applied_before_the_firewall(self):
        """`at` decides which records are in play and the firewall decides who
        may see them, in that order — so a subject whose only records are
        outside the window is empty to everyone, including a reader who would
        have been refused had they been inside it."""
        ledger = [a_record(subject=LITELLM_0205, at="2026-09-13T09:00:00Z")]
        reader = scope.thread("LIT-0101")
        self.assertEqual(
            record.heads(ledger, LITELLM_0205, at="2026-09-13T05:00:00Z",
                         reader=reader), [])
        with self.assertRaises(scope.RecallRefused):
            record.heads(ledger, LITELLM_0205, reader=reader)

    def test_a_read_without_a_reader_cannot_be_expressed(self):
        """The strongest form of "no read skips the firewall": the omission is
        a TypeError at the call site, so there is no value — not None, not a
        string — that stands in for a reader."""
        with self.assertRaises(TypeError):
            record.heads([a_record()], SERVEROS)
        with self.assertRaises(TypeError):
            record.current_value([a_record()], SERVEROS)

    def test_a_read_writes_nothing_to_the_ledger(self):
        """`docs/memory-fabric.md`: the ledger records what was written, never
        what was read. A read that left a record behind would make the ledger's
        size a function of how often it was consulted."""
        ledger = [a_record(subject=LITELLM_0205)]
        before = json.dumps(ledger, sort_keys=True)
        scope.recall(ledger, LITELLM_0205, reader=scope.thread("LIT-0101"))
        record.heads(ledger, LITELLM_0205, reader=scope.OPERATOR_READING)
        self.assertEqual(json.dumps(ledger, sort_keys=True), before)

    def test_the_reading_names_the_reader_so_the_refusal_can_be_acted_on(self):
        ledger = [a_record(subject=LITELLM_0205, id="mem:private")]
        reading = scope.recall(ledger, LITELLM_0205, reader=scope.thread("LIT-0101"))
        self.assertFalse(reading["recalled"])
        self.assertEqual(reading["reader"]["item_ref"], "LIT-0101")
        self.assertEqual(reading["refused"][0]["id"], "mem:private")
        self.assertEqual(reading["refused"][0]["scope"], "item")

    def test_the_projection_reads_as_an_operator_and_says_so(self):
        ledger = [a_record(subject=LITELLM_0205),
                  a_record(id="mem:d", subject=A_DEVICE)]
        built = projection.build(ledger, ledger_ref="records", ledger_revision="abc",
                                 generated_at="2026-09-13T06:00:00Z",
                                 generator="test")
        self.assertEqual(sorted(built["subjects"]), sorted([LITELLM_0205, A_DEVICE]))
        self.assertEqual(built["subjects"][A_DEVICE]["heads"], 1)


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------


class CliTests(unittest.TestCase):
    def _ctl(self, *args):
        return subprocess.run(
            [sys.executable, str(REPO / "runtime" / "cli.py"), *args],
            capture_output=True, text=True, cwd=str(REPO))

    def test_the_read_says_who_asked_even_when_it_is_admitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "one.yaml").write_text(
                yaml.safe_dump(a_record(subject=LITELLM_0205)))
            found = self._ctl("memory", "subject", LITELLM_0205, "--as", "LIT-0101",
                              "--ledger-dir", tmp, "--json")
            self.assertEqual(found.returncode, 1, found.stderr)
            payload = json.loads(found.stdout)
            self.assertFalse(payload["recalled"])
            self.assertEqual(payload["reason"], "CROSS_ITEM_RECALL")

    def test_the_cli_admits_the_reader_that_owns_the_subject(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "one.yaml").write_text(yaml.safe_dump(a_record()))
            found = self._ctl("memory", "subject", SERVEROS, "--as", "SRV-0007",
                              "--ledger-dir", tmp, "--json")
            self.assertEqual(found.returncode, 0, found.stderr)
            payload = json.loads(found.stdout)
            self.assertEqual(payload["reader"]["item_ref"], "SRV-0007")
            self.assertEqual(payload["current"]["id"], "mem:the-tunnel-is-task-scoped")

    def test_the_operator_read_is_unchanged_by_the_firewall(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "one.yaml").write_text(yaml.safe_dump(a_record()))
            found = self._ctl("memory", "subject", SERVEROS, "--as", "operator",
                              "--ledger-dir", tmp, "--json")
            self.assertEqual(found.returncode, 0, found.stderr)
            self.assertEqual(json.loads(found.stdout)["reader"]["kind"], "operator")

    def test_a_reader_that_is_not_a_reader_is_refused_before_the_ledger_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            found = self._ctl("memory", "subject", SERVEROS, "--as", "not/a/ref/ok",
                              "--ledger-dir", tmp, "--json")
            self.assertEqual(found.returncode, 2)
            self.assertIn("not an Item ref", found.stderr)


if __name__ == "__main__":
    unittest.main()
