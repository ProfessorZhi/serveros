#!/usr/bin/env python3
"""§59 — cross-device handoff, run over the Git ledger.

WHY THIS FILE EXISTS
--------------------
§59 asks for a fixture in which the work crosses a device boundary and a result
comes back. The two devices in this fleet are rarely awake at the same time and
no remote actuator is available from here, which §59 anticipates: the fallback
is an async handoff through the memory provider, and the ledger's substrate is
Git, so the transport already exists.

What that leaves is a correctness question, and it is not "can a file be read
on two machines". It is:

    how does a device tell a handoff addressed to IT from the several hundred
    records that are not?

Everything below is about that, plus the four failure modes that make an
async handoff worse than useless:

  * a handoff nobody can select, because it names no addressee — the work
    stalls and the ledger looks fine;
  * an inbox built from `current_value(subject)`, which returns the newest
    record about the subject — the handoff the READER sent;
  * a checkpoint with no body, which passes every structural check and
    resumes nothing;
  * a round trip that only works while both devices are up.

Run it directly for the fixture's own report:
`python3 tests/test_cross_device_handoff.py`
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MEMORY = REPO / "memory"
FIXTURE = MEMORY / "fixtures/cross_device_handoff"

BEIJING = "device:server-beijing"
WORK_FJ = "device:work-fj"
# The same subject the real ledger uses for this round. A cross-device
# fixture whose subject differs from the ledger's is one where two devices
# read the same work under two names, and `current_value` would never line
# up with `pending()` — the interoperability the fixture exists to show.
SUBJECT = "project/serveros/SRV-0007"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


record = _load("serveros_memory_record", MEMORY / "record.py")
handoff = _load("serveros_memory_handoff", MEMORY / "handoff.py")
scope = _load("serveros_memory_scope", MEMORY / "scope.py")


def fixture_records() -> list[dict]:
    paths = sorted(FIXTURE.glob("*.yaml"))
    return record.load(paths)


# ---------------------------------------------------------------------------
# What the fixture must be, before anything is asserted about the round trip
# ---------------------------------------------------------------------------


class FixtureTests(unittest.TestCase):
    def test_the_fixture_directory_holds_records_and_nothing_else(self):
        """A `README.md` is not a record, and this is the assertion that keeps
        it that way. A ledger file that is prose is a file the loader refuses
        — loudly, which is fine — but a fixture directory that quietly holds
        two kinds of thing is one where the next glob picks up the wrong one."""
        names = sorted(p.name for p in FIXTURE.iterdir())
        self.assertEqual(names, ["01-beijing-checkpoint.yaml",
                                 "02-beijing-handoff.yaml",
                                 "03-work-fj-result.yaml",
                                 "README.md"])

    def test_every_record_says_it_is_a_fixture(self):
        """By `source`, which is the field whose job is provenance. A fixture
        record that cannot be told from real ledger content is one that gets
        cited, and the citation resolves."""
        self.assertTrue(fixture_records())
        for one in fixture_records():
            self.assertEqual(one["source"], "memory/fixtures/cross_device_handoff/",
                             f"{one['id']} does not name the fixture as its source")

    def test_the_fixture_ledger_is_conformant(self):
        report = record.audit(fixture_records())
        self.assertEqual(report["verdict"], "CONFORMANT", report["findings"])

    def test_the_fixture_subject_is_one_the_real_ledger_uses(self):
        """The fixture is only a fixture of the real handoff if it is about the
        same work under the same name. It was not: it said
        `serveros/fleet-device-registry-v1/SRV-0007` while every record in
        `records/projects/serveros/memory/` says `project/serveros/SRV-0007`,
        and two devices reading one work under two subject strings is exactly
        the defect this fixture is supposed to be able to exhibit — a sender
        asking `current_value` and a receiver asking `pending()` would never
        have been describing the same subject.

        Skips when the ledger is not checked out beside the worktree, because a
        missing ledger is not a failing one.
        """
        ledger = Path("/opt/applications/data/home-hwz/workspace/records/"
                      "projects/serveros/memory")
        if not ledger.is_dir():
            self.skipTest(f"no ledger at {ledger}")
        subjects = {r["subject"] for r in record.load(sorted(ledger.glob("*.yaml")))}
        self.assertTrue(subjects)
        self.assertIn(SUBJECT, subjects,
                      f"the fixture is about {SUBJECT!r}, which the real ledger "
                      f"does not use; it uses {sorted(subjects)}")

    def test_the_fixture_reads_through_the_module_that_defines_the_round_trip(self):
        """The fixture is authored as YAML and the constructors in
        `handoff.py` are what an Agent would call. They have to agree, or the
        fixture proves something about hand-written YAML rather than about the
        API — and the API is what ships."""
        built = handoff.checkpoint(
            "mem:fixture-rebuilt-checkpoint", subject=SUBJECT, device_ref=BEIJING,
            at="2026-09-13T01:00:00Z", by="claude-code",
            source="memory/fixtures/cross_device_handoff/", revision="19652da",
            body="x")
        authored = next(r for r in fixture_records()
                        if r["id"] == "mem:fixture-beijing-checkpoint-1")
        self.assertEqual(sorted(built), sorted(authored))
        # And the same holds for the handoff, which is the one with an
        # addressee and therefore the one most likely to drift.
        built_handoff = handoff.handoff(
            "mem:fixture-beijing-handoff-1", subject=SUBJECT, to_device_ref=WORK_FJ,
            device_ref=BEIJING, at="2026-09-13T01:05:00Z", by="claude-code",
            source="memory/fixtures/cross_device_handoff/", revision="19652da",
            body="x")
        self.assertIn("to_device_ref", built_handoff)


# ---------------------------------------------------------------------------
# The four steps
# ---------------------------------------------------------------------------


class RoundTripTests(unittest.TestCase):
    """§59's four steps, in order, each asserted where it is decided."""

    def setUp(self):
        self.records = fixture_records()

    # -- step 2: the receiving device finds it ------------------------------
    def test_the_addressed_device_finds_exactly_one_open_handoff(self):
        """Read at 02:00, between the handoff (01:05) and the result (03:30),
        because that is when step 2 happens. The unbounded read is asserted
        separately below — this one is the moment the work arrives."""
        inbox = handoff.pending(self.records, WORK_FJ, at="2026-09-13T02:00:00Z")
        self.assertEqual([h["id"] for h in inbox["open"]],
                         ["mem:fixture-beijing-handoff-1"])
        self.assertEqual(inbox["counts"], {"OPEN": 1, "ANSWERED": 0})
        self.assertEqual(inbox["as_of"], "2026-09-13T02:00:00Z")

    def test_a_device_the_handoff_is_not_addressed_to_finds_nothing(self):
        """The property that makes the inbox an inbox. Without it every device
        in the fleet reads every handoff, and the questions "is there work for
        me" and "did anyone do anything" both stop having answers."""
        for other in (BEIJING, "device:personal-dell"):
            self.assertEqual(
                handoff.pending(self.records, other, at="2026-09-13T02:00:00Z")["open"],
                [])

    def test_the_inbox_is_not_the_current_value_of_the_subject(self):
        """The mistake this function exists to not make, asserted against the
        function that would make it. `current_value` returns the NEWEST record
        about the subject — which here is the answer, written by the receiving
        device. A receiving device that asked it would be handed back its own
        work and would never see the request."""
        newest = record.current_value(self.records, SUBJECT,
                                      reader=scope.OPERATOR_READING)
        self.assertEqual(newest["id"], "mem:fixture-work-fj-result-1")
        inbox = handoff.pending(self.records, WORK_FJ, at="2026-09-13T02:00:00Z")
        self.assertEqual([h["id"] for h in inbox["open"]],
                         ["mem:fixture-beijing-handoff-1"])
        self.assertNotEqual(newest["id"], inbox["open"][0]["id"])

    def test_the_inbox_drains_when_the_answer_is_written(self):
        """The other half of the time bound, and the honest end state: after
        the result lands, this device's inbox is empty and the handoff shows
        as answered. Both readings come from the same three records — nothing
        was added to ask the first question or removed to ask the second."""
        inbox = handoff.pending(self.records, WORK_FJ)
        self.assertEqual(inbox["open"], [])
        self.assertEqual([h["id"] for h in inbox["answered"]],
                         ["mem:fixture-beijing-handoff-1"])
        self.assertEqual(inbox["as_of"], None)

    # -- step 3: the result, and the link that makes it an answer ------------
    def test_the_result_checkpoint_answers_the_handoff(self):
        found = handoff.answers(self.records, "mem:fixture-beijing-handoff-1")
        self.assertEqual([a["id"] for a in found], ["mem:fixture-work-fj-result-1"])
        self.assertEqual(found[0]["device_ref"], WORK_FJ)

    def test_the_answer_is_the_link_and_not_the_timing(self):
        """A checkpoint written after the handoff is not an answer to it.
        `related` is what says so, and this asserts the negative direction:
        strip the link and the handoff is open again, with the same records in
        the same order."""
        unlinked = [{k: v for k, v in r.items() if k != "related"}
                    for r in self.records]
        self.assertEqual(handoff.answers(unlinked, "mem:fixture-beijing-handoff-1"), [])
        self.assertEqual(handoff.pending(unlinked, WORK_FJ)["counts"]["OPEN"], 1)
        # And the positive control, so the assertion above is about the link
        # and not about `related` being unreadable: with the link present the
        # same records answer.
        self.assertEqual(
            [a["id"] for a in handoff.answers(self.records, "mem:fixture-beijing-handoff-1")],
            ["mem:fixture-work-fj-result-1"])

    # -- step 4: the sender retrieves it, having not waited -----------------
    def test_the_sender_learns_the_answer_without_contacting_the_other_device(self):
        """Step 4, and the whole reason this is async. `round_trip` is given a
        list of records and nothing else — no host, no socket, no client — and
        the sender's question is answered from it."""
        report = handoff.round_trip(self.records)
        self.assertEqual(report["counts"], {"OPEN": 0, "ANSWERED": 1})
        row = report["handoffs"][0]
        self.assertEqual(row["from"], BEIJING)
        self.assertEqual(row["to"], WORK_FJ)
        self.assertEqual([a["device_ref"] for a in row["answers"]], [WORK_FJ])

    def test_the_round_trip_module_reads_nothing_it_was_not_handed(self):
        """Asserted structurally rather than by reading the source for the
        word `socket`. A module that reached the network would have to import
        something that can, and the daemon-boundary rule in
        `memory/provider-contract.yaml` is enforced the same way."""
        import ast
        tree = ast.parse((MEMORY / "handoff.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("socket", "http", "urllib", "requests", "subprocess"):
            self.assertNotIn(forbidden, imported,
                             f"handoff.py imports {forbidden}; the round trip is "
                             "over the ledger, and a ledger read is a file read")

    # -- an open handoff is work waiting, not a failure ---------------------
    def test_an_unanswered_handoff_is_reported_open_and_not_as_an_error(self):
        """`OPEN` is the normal state of a fleet whose devices are rarely awake
        together. A module that reported it as a fault would spend attention on
        the ordinary case, and this round has already paid for that lesson once
        with a conflict report that fired on the ordinary case."""
        unlinked = [{k: v for k, v in r.items() if k != "related"}
                    for r in self.records]
        report = handoff.round_trip(unlinked)
        self.assertEqual(report["counts"], {"OPEN": 1, "ANSWERED": 0})
        self.assertNotIn("error", json.dumps(report).lower())
        self.assertIn("work waiting", report["note"])


# ---------------------------------------------------------------------------
# The failures the schema has to refuse, because the runtime cannot
# ---------------------------------------------------------------------------


class RefusalTests(unittest.TestCase):
    def test_a_handoff_with_no_addressee_is_refused(self):
        """The quiet one. A handoff that names nobody is a record that says the
        work moved and not where it went; no device selects it, nothing
        arrives, and nothing reports that nothing arrived — a record nobody
        read is byte-identical to a record everybody read."""
        blocked = {"id": "mem:unaddressed", "kind": "handoff", "subject": SUBJECT,
                   "at": "2026-09-13T01:05:00Z", "by": "claude-code",
                   "source": "x", "revision": "19652da", "device_ref": BEIJING,
                   "body": "the work moved somewhere"}
        with self.assertRaises(record.RecordRefused) as caught:
            record.validate(blocked)
        self.assertIn("to_device_ref is required", str(caught.exception))

    def test_a_device_may_not_hand_work_to_itself(self):
        """A self-addressed handoff appears in the sender's inbox forever and
        can never be closed by the only device that would see it. The ledger
        already holds the record; a handoff is what crosses a boundary."""
        with self.assertRaises(handoff.HandoffRefused) as caught:
            handoff.handoff("mem:self", subject=SUBJECT, to_device_ref=BEIJING,
                            device_ref=BEIJING, at="2026-09-13T01:05:00Z",
                            by="claude-code", source="x", revision="19652da",
                            body="x")
        self.assertIn("to itself", str(caught.exception))

    def test_a_checkpoint_with_no_body_is_refused(self):
        """It passes every structural check and resumes nothing. The contract's
        own test for the field is whether a different device can continue from
        it alone."""
        with self.assertRaises(handoff.HandoffRefused) as caught:
            handoff.checkpoint("mem:empty", subject=SUBJECT, device_ref=BEIJING,
                               at="2026-09-13T01:00:00Z", by="claude-code",
                               source="x", revision="19652da", body="   ")
        self.assertIn("resumes nothing", str(caught.exception))

    def test_an_addressee_that_is_not_a_device_is_refused(self):
        """`to_device_ref` is matched against a device's own `device_ref` by
        the receiving device. Anything else is a handoff nobody can receive,
        wearing the shape of one that was sent."""
        blocked = {"id": "mem:wrong-addressee", "kind": "handoff",
                   "subject": SUBJECT, "at": "2026-09-13T01:05:00Z",
                   "by": "claude-code", "source": "x", "revision": "19652da",
                   "device_ref": BEIJING, "to_device_ref": "claude-code",
                   "body": "x"}
        with self.assertRaises(record.RecordRefused) as caught:
            record.validate(blocked)
        self.assertIn("not a `device:<id>`", str(caught.exception))

    def test_to_device_ref_on_a_kind_with_no_addressee_is_refused(self):
        """Otherwise a checkpoint that happens to carry the field starts
        looking like something that was sent, and `pending()` — which selects
        on `kind` and `to_device_ref` — is one refactor away from selecting on
        the field alone."""
        blocked = {"id": "mem:confused", "kind": "checkpoint", "subject": SUBJECT,
                   "at": "2026-09-13T01:00:00Z", "by": "claude-code",
                   "source": "x", "revision": "19652da", "device_ref": BEIJING,
                   "to_device_ref": WORK_FJ, "body": "x"}
        with self.assertRaises(record.RecordRefused) as caught:
            record.validate(blocked)
        self.assertIn("only a handoff has an addressee", str(caught.exception))


# ---------------------------------------------------------------------------
# The CLI, because a fixture nobody can run is a fixture nobody checks
# ---------------------------------------------------------------------------


class CliTests(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(MEMORY / "handoff.py"),
             *sorted(str(p) for p in FIXTURE.glob("*.yaml")), *args],
            capture_output=True, text=True, cwd=str(REPO))

    def test_the_cli_prints_a_device_inbox(self):
        done = self._run("--device", WORK_FJ, "--at", "2026-09-13T02:00:00Z")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([h["id"] for h in json.loads(done.stdout)["open"]],
                         ["mem:fixture-beijing-handoff-1"])

    def test_the_cli_prints_the_round_trip(self):
        done = self._run("--round-trip")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["counts"], {"OPEN": 0, "ANSWERED": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
