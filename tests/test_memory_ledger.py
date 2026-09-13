#!/usr/bin/env python3
"""§20–§28 — the ledger, the projection, and the layering between them.

WHAT THIS FILE IS ABOUT
-----------------------
`memory/record-contract.yaml` declared six kinds, seven required fields, a
forbidden-content list, a supersession rule and a projection block. Nothing
refused anything, nothing built a projection, and nothing compared the two
layers. A description of a ledger is not a ledger, and a description cannot
fail.

Three things are worth knowing about the tests below.

1. The transcription checks are first and they are not ceremony. This round has
   now found the same closed vocabulary written twice and disagreeing in five
   different places — the disclosure axes, the provider conditions, the
   `~/.serveros` allowlist, the projection location, the record kinds (this
   one, in `docs/memory-fabric.md`, which named five of the contract's six).
   A test that asserts the two copies agree is the only thing that has ever
   caught it.

2. The secret scanner has negative controls that matter more than the positive
   ones. Every `revision` field holds a git SHA and every `source` may hold a
   URL; a scanner that flagged those would refuse the ledger for being a
   ledger, and — this codebase has already paid for this once — the response to
   a check that refuses legitimate work is to switch the check off.

3. `test_the_contract_that_ships_is_refused_when_it_should_be` runs the real
   files through the real kernel, the same way `test_runtime_parity.py` does.
   A validator only ever shown fixtures agrees with whatever fixture its author
   wrote.
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


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


record = _load("serveros_memory_record",
               MEMORY / "record.py")
projection = _load("serveros_memory_projection",
                   MEMORY / "projection.py")
layering = _load("serveros_memory_layering",
                 MEMORY / "layering.py")

sys.path.insert(0, str(REPO / "runtime"))
paths_mod = _load("serveros_paths", REPO / "runtime" / "paths.py")

import yaml  # noqa: E402

CONTRACT = yaml.safe_load((MEMORY / "record-contract.yaml").read_text(encoding="utf-8"))


def a_record(**over):
    base = {
        "id": "mem:the-tunnel-is-task-scoped",
        "kind": "decision",
        "subject": "project/serveros/SRV-0007",
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
    def test_the_kinds_are_the_contracts_kinds(self):
        self.assertEqual(list(record.KINDS), list(CONTRACT["kinds"]))

    def test_the_required_fields_are_the_contracts_required_fields(self):
        """The contract writes these as a commented YAML list, so the comment
        is stripped and the name read."""
        declared = [str(line).split("#")[0].strip()
                    for line in CONTRACT["required_fields"]]
        self.assertEqual(list(record.REQUIRED), declared)

    def test_the_optional_fields_are_the_contracts_optional_fields(self):
        declared = [str(line).split("#")[0].strip()
                    for line in CONTRACT["optional_fields"]]
        self.assertEqual(list(record.OPTIONAL), declared)

    def test_the_confidence_rule_is_the_contracts_rule(self):
        """The contract scopes `confidence` to observations in one place and
        makes it a refusal in another. Both halves are asserted, because either
        alone reads as optional."""
        rule = " ".join(CONTRACT["rules"])
        self.assertIn("observation without confidence is refused", rule)
        self.assertEqual(list(record.REQUIRED_FOR_KIND["observation"]),
                         ["confidence"])
        # The three values are in a YAML comment, so they are not in the parsed
        # document — which is itself the reason this is asserted against the
        # raw text rather than against a key.
        raw = (MEMORY / "record-contract.yaml").read_text(encoding="utf-8")
        self.assertIn("high | medium | low", raw)
        self.assertEqual(list(record.CONFIDENCE_VALUES), ["high", "medium", "low"])

    def test_the_projection_location_is_the_one_the_state_root_contract_owns(self):
        """The defect this closes. The record contract said
        `state/serveros/memory/`; `fleet/state-root-contract.yaml` declares
        `state/serveros/projections/` to be the ONLY projections namespace and
        gives the reason. Two declarations, one fact, nothing comparing them."""
        declared = CONTRACT["projection"]["location"].strip("/")
        resolved = paths_mod.Paths().memory_projection()["path"]
        self.assertTrue(resolved.endswith(declared),
                        f"{declared!r} does not resolve to {resolved!r}")

        state_contract = yaml.safe_load(
            (REPO / "fleet/state-root-contract.yaml").read_text(encoding="utf-8"))
        namespace = state_contract["projections"]["ref"].strip("/")
        self.assertTrue(declared.startswith(namespace + "/"),
                        f"{declared!r} is not inside {namespace!r}, which the "
                        "state root contract declares to be the only "
                        "projections namespace")

    def test_the_document_the_contract_points_at_names_every_kind(self):
        """`docs/memory-fabric.md` listed five kinds and omitted `question`
        while the contract declared six and its own retention rule referred to
        one. A reader who started at the doc got a smaller vocabulary than a
        reader who started at the contract."""
        text = (REPO / "docs/memory-fabric.md").read_text(encoding="utf-8")
        for kind in CONTRACT["kinds"]:
            self.assertIn(kind, text, f"{kind} is missing from the doc")

    def test_the_ledger_path_is_the_records_repository(self):
        """The substrate is Git, and it is Git because the ledger inherits
        blame, diff, revert and a cross-device sync that is a `git pull`. A
        path outside a versioned tree would have none of those."""
        resolved = paths_mod.Paths().memory_ledger("serveros")["path"]
        self.assertTrue(resolved.endswith("records/projects/serveros/memory"),
                        resolved)


# ---------------------------------------------------------------------------
# One record
# ---------------------------------------------------------------------------


class ValidateTests(unittest.TestCase):
    def test_a_complete_record_passes(self):
        self.assertEqual(record.validate(a_record()), a_record())

    def test_a_record_missing_a_required_field_is_refused_and_says_which(self):
        broken = a_record()
        del broken["source"]
        self.assertIn("source is required and empty", refused(broken))

    def test_every_missing_field_is_reported_at_once(self):
        """An author told about one refusal at a time fixes records one at a
        time, and the second pass costs what the first did."""
        reasons = refused({"id": "mem:x"})
        for field in ("kind", "subject", "at", "by", "source", "revision"):
            self.assertTrue(any(field in r for r in reasons), (field, reasons))

    def test_an_id_that_is_not_a_mem_id_is_refused(self):
        self.assertTrue(refused(a_record(id="SRV-0007")))

    def test_a_kind_outside_the_closed_set_is_refused(self):
        self.assertTrue(refused(a_record(kind="rumour")))

    def test_an_observation_without_confidence_is_refused(self):
        reasons = refused(a_record(kind="observation"))
        self.assertTrue(any("confidence is required" in r for r in reasons))

    def test_a_decision_without_confidence_is_fine(self):
        """The rule is scoped to observations. Making it global would be a
        refusal nobody asked for, on the field the contract marks optional."""
        self.assertEqual(record.validate(a_record(kind="decision")),
                         a_record(kind="decision"))

    def test_a_confidence_outside_the_three_values_is_refused(self):
        self.assertTrue(refused(a_record(kind="observation", confidence="probably")))

    def test_a_time_without_a_zone_is_refused(self):
        """The contract requires ISO-8601 UTC, and the reason is in the
        contract: an offset-less timestamp on a three-device fleet is a fact
        with an unstated premise."""
        self.assertTrue(refused(a_record(at="2026-09-13 04:00")))

    def test_a_supersedes_that_is_not_a_mem_id_is_refused(self):
        self.assertTrue(refused(a_record(supersedes="the-old-one")))

    def test_an_undeclared_field_is_refused(self):
        """The contract gives a closed field list. A field nobody declared is a
        fact the ledger promises nothing about, and it is how a second store
        starts."""
        self.assertTrue(refused(a_record(severity="high")))

    def test_a_subject_that_is_neither_an_item_nor_a_device_is_refused(self):
        self.assertTrue(refused(a_record(subject="the LIT-0104 tunnel")))

    def test_a_device_subject_is_allowed(self):
        self.assertEqual(record.validate(a_record(subject="device:linux-node"))["subject"],
                         "device:linux-node")


class ForbiddenContentTests(unittest.TestCase):
    """The checkable half of the forbidden list — and the half that is not."""

    def test_a_private_key_block_is_refused(self):
        reasons = refused(a_record(body="-----BEGIN OPENSSH PRIVATE KEY-----\nabc"))
        self.assertTrue(any("private key" in r for r in reasons))

    def test_a_github_token_is_refused(self):
        reasons = refused(a_record(body="ghp_" + "A" * 36))
        self.assertTrue(any("GitHub token" in r for r in reasons))

    def test_a_credential_assignment_is_refused(self):
        reasons = refused(a_record(note="api_key = 8f3ba91c77d2"))
        self.assertTrue(any("credential" in r for r in reasons))

    def test_a_mac_address_is_refused(self):
        """`a raw hardware identifier`, which is the one entry in that list a
        program can recognise."""
        reasons = refused(a_record(note="bound to 3c:22:fb:0a:11:9e"))
        self.assertTrue(any("MAC address" in r for r in reasons))

    # -- negative controls. These are the ones that decide whether the check
    # -- survives contact with a real ledger.

    def test_a_git_sha_in_the_revision_is_not_a_secret(self):
        self.assertEqual(record.validate(a_record(revision="a65bd931c10c4e2f")),
                         a_record(revision="a65bd931c10c4e2f"))

    def test_a_url_source_is_not_a_secret(self):
        url = "https://github.com/ProfessorZhi/hwz-records/blob/main/README.md"
        self.assertEqual(record.validate(a_record(source=url)),
                         a_record(source=url))

    def test_prose_about_tokens_is_not_a_credential(self):
        """The word is the subject of the sentence, not a value. A scanner that
        could not tell those apart would refuse every record that discusses
        secret handling — which is most of the records about §42."""
        body = ("the token is refreshed by the launcher, and no token value is "
                "recorded here; see the vault")
        self.assertEqual(record.validate(a_record(body=body))["body"], body)

    def test_a_placeholder_is_not_a_credential(self):
        """`token: ${VAULT_TOKEN}` is a reference. Refusing the reference form
        would make the rule unusable in exactly the records that are being
        careful — which is how a check teaches people to route around it."""
        for body in ("token: ${VAULT_TOKEN}", "token: <from vault>",
                     "token: $VAULT_TOKEN"):
            self.assertEqual(refused(a_record(body=body)), [], body)

    def test_the_uncheckable_half_is_reported_rather_than_assumed_clean(self):
        """`a raw transcript body` and `a message body from a chat` cannot be
        detected by anything here. A validator that returned "no findings" and
        stopped would let a caller read that as "the ledger is clean"."""
        report = record.audit([a_record()])
        self.assertEqual(report["verdict"], "CONFORMANT")
        self.assertTrue(report["not_checked"])


# ---------------------------------------------------------------------------
# The whole ledger
# ---------------------------------------------------------------------------


class AuditTests(unittest.TestCase):
    def test_a_clean_ledger_is_conformant(self):
        report = record.audit([a_record(),
                               a_record(id="mem:another", kind="finding")])
        self.assertEqual(report["verdict"], "CONFORMANT")
        self.assertEqual(report["findings"], [])

    def test_a_duplicate_id_is_a_finding(self):
        report = record.audit([a_record(), a_record()])
        self.assertIn("DUPLICATE_ID", {f["code"] for f in report["findings"]})

    def test_an_invalid_record_is_a_finding_not_an_exception(self):
        """Audit reports; `validate` refuses. A ledger with a broken record in
        it is a normal state — the record is in the history and cannot be
        un-written — so the check on what is already there must not be the gate
        on what may be added."""
        report = record.audit([a_record(), {"id": "nope"}])
        self.assertEqual(report["verdict"], "DEFECT")
        self.assertIn("RECORD_INVALID", {f["code"] for f in report["findings"]})

    def test_a_supersedes_naming_a_record_that_is_not_here_is_a_finding(self):
        report = record.audit([a_record(supersedes="mem:never-recorded")])
        self.assertIn("SUPERSEDES_ABSENT", {f["code"] for f in report["findings"]})

    def test_a_correction_of_another_subject_is_a_finding(self):
        """A correction of a different subject is not a correction, and it
        makes `the current value of a subject` incoherent: the chain would
        carry a value about something else."""
        original = a_record(id="mem:first", subject="project/serveros/SRV-0003")
        correction = a_record(id="mem:second", supersedes="mem:first",
                              subject="project/serveros/SRV-0007")
        report = record.audit([original, correction])
        self.assertIn("SUPERSEDES_ANOTHER_SUBJECT",
                      {f["code"] for f in report["findings"]})

    def test_a_supersession_cycle_is_a_finding(self):
        """The walk in `current_value` stops at its own guard rather than at a
        fact, which is why a cycle is reported rather than assumed away."""
        first = a_record(id="mem:first", supersedes="mem:second")
        second = a_record(id="mem:second", supersedes="mem:first")
        report = record.audit([first, second])
        self.assertIn("SUPERSESSION_CYCLE", {f["code"] for f in report["findings"]})

    def test_a_valid_correction_chain_is_not_a_finding(self):
        """The negative control for both findings above. Superseding is the
        supported operation, and a check that flagged it would be flagging the
        contract's own rule for editing a record."""
        first = a_record(id="mem:first")
        second = a_record(id="mem:second", supersedes="mem:first",
                          at="2026-09-13T05:00:00Z")
        report = record.audit([first, second])
        self.assertEqual(report["verdict"], "CONFORMANT", report["findings"])


class SupersessionTests(unittest.TestCase):
    def setUp(self):
        self.first = a_record(id="mem:first", body="the first answer")
        self.second = a_record(id="mem:second", supersedes="mem:first",
                               at="2026-09-13T05:00:00Z", body="the correction")
        self.ledger = [self.first, self.second]

    def test_the_current_value_is_the_head_of_the_chain(self):
        self.assertEqual(record.current_value(self.ledger, self.first["subject"])["id"],
                         "mem:second")

    def test_the_chain_is_newest_first_and_carries_the_history(self):
        """Sorted oldest-first by `heads`, so the chain is reversed here — and
        the point of the chain is that the superseded record is still legible.
        The contract's precedent is a release note whose wrong sentence was
        more useful than its correction."""
        chain = record.chain(self.ledger, "mem:second")
        self.assertEqual([r["id"] for r in chain], ["mem:second", "mem:first"])

    def test_a_subject_with_no_records_returns_none(self):
        self.assertIsNone(record.current_value(self.ledger, "project/litellm/LIT-0104"))

    def test_choosing_between_heads_is_visible_rather_than_quiet(self):
        """`current_value` picks the newer of several heads, which is a choice
        made on the reader's behalf — so the number of heads is what the
        projection records, and that is where the choice is visible."""
        rival = a_record(id="mem:rival", at="2026-09-13T06:00:00Z")
        ledger = [*self.ledger, rival]
        self.assertEqual(len(record.heads(ledger, self.first["subject"])), 2)
        self.assertEqual(record.current_value(ledger, self.first["subject"])["id"],
                         "mem:rival")

    def test_many_heads_are_ordinary_and_are_not_a_conflict_report(self):
        """Falsified by the first real ledger, which put eight records under one
        work item and was reported as conflicted by a rule this test used to
        assert.

        The lesson is that the ledger CANNOT detect a conflict: two decisions
        about one subject may answer one question and disagree, or answer two
        questions and agree, and nothing in a record says which claim it is
        about. Supersession is the only conflict mechanism there is, and it is
        one the author has to use. A check that flagged every repeated kind
        would be wrong on the ordinary case and ignored on the day it was
        right."""
        ledger = [a_record(id=f"mem:decision-{n}",
                           at=f"2026-09-13T0{n}:00:00Z") for n in range(1, 5)]
        self.assertEqual(len(record.heads(ledger, self.first["subject"])), 4)
        self.assertEqual(record.audit(ledger)["verdict"], "CONFORMANT")


class YAMLTimestampTests(unittest.TestCase):
    """Two defects found by the first live run, both from one cause.

    YAML parses an unquoted `2026-09-13T06:00:00Z` into a `datetime`. The
    validator refused all seven correct baseline records for it, because
    `str(datetime)` is `2026-09-13 06:00:00+00:00` — a space where the `T`
    should be. Then, once the validator accepted them, the projection writer
    died with `Object of type datetime is not JSON serializable`, three layers
    from the cause.

    Both tests below are on the loaded form, because that is what a caller has:
    `str(value)` on a string the author wrote is not the thing that broke.
    """

    def _ledger(self, tmp, body: str) -> Path:
        path = Path(tmp) / "one.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_an_unquoted_timestamp_loads_as_the_canonical_string(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._ledger(tmp, "id: mem:one\nkind: decision\n"
                                     "subject: project/serveros/SRV-0007\n"
                                     "at: 2026-09-13T06:00:00Z\n"
                                     "by: claude-code\nsource: docs/x.md\nrevision: abc\n")
            loaded = record.load([path])
        self.assertIsInstance(loaded[0]["at"], str)
        self.assertEqual(loaded[0]["at"], "2026-09-13T06:00:00Z")
        self.assertEqual(record.validate(loaded[0]), loaded[0])

    def test_a_loaded_ledger_reaches_json_without_dying_in_the_writer(self):
        """The actual symptom. A projection built from a loaded ledger has to
        serialize, and it did not."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._ledger(tmp, "id: mem:one\nkind: decision\n"
                                     "subject: project/serveros/SRV-0007\n"
                                     "at: 2026-09-13T06:00:00Z\n"
                                     "by: claude-code\nsource: docs/x.md\nrevision: abc\n")
            loaded = record.load([path])
        document = projection.build(loaded, ledger_ref="r", ledger_revision="abc",
                                    generated_at="2026-09-13T09:00:00Z",
                                    generator="test")
        self.assertIn("2026-09-13T06:00:00Z", json.dumps(document))

    def test_a_datetime_with_no_timezone_is_still_refused(self):
        """The case the rule exists for, and the one the repair must not have
        relaxed. An offset-less timestamp about a three-device fleet is a fact
        with an unstated premise."""
        import datetime

        self.assertTrue(refused(a_record(at=datetime.datetime(2026, 9, 13, 6, 0))))
        self.assertIsNone(record.canonical_at(datetime.datetime(2026, 9, 13, 6, 0)))

    def test_a_zoned_datetime_is_accepted_and_normalized_to_utc(self):
        import datetime

        value = datetime.datetime(2026, 9, 13, 8, 0,
                                  tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
        self.assertEqual(record.canonical_at(value), "2026-09-13T06:00:00Z")
        self.assertEqual(refused(a_record(at=value)), [])

    def test_a_bare_date_is_refused(self):
        """A day is not a moment, and two records dated the same day have no
        order. The contract requires the time."""
        import datetime

        self.assertTrue(refused(a_record(at=datetime.date(2026, 9, 13))))


# ---------------------------------------------------------------------------
# Layer A
# ---------------------------------------------------------------------------


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            a_record(id="mem:first"),
            a_record(id="mem:second", supersedes="mem:first",
                     at="2026-09-13T05:00:00Z"),
            a_record(id="mem:open", kind="question", subject="project/serveros/SRV-0003"),
        ]
        self.document = projection.build(
            self.records, ledger_ref="records/projects/serveros/memory",
            ledger_revision="a65bd931c10c",
            generated_at="2026-09-13T06:00:00Z", generator="test")

    def test_the_projection_says_it_has_no_authority(self):
        """The one field the state root contract checks against the literal
        False. A projection that could be built claiming authority would make
        the namespace's invariant a caller's choice."""
        self.assertIs(self.document["authority"], False)

    def test_the_projection_carries_pointers_and_not_content(self):
        """`NO_SOURCE_AUTHORITY`: nothing reads a projection to decide what is
        true. A projection carrying the text would be a second copy of the
        ledger in the one namespace declared to have no authority."""
        rendered = json.dumps(self.document)
        self.assertNotIn("a tunnel belongs to the task", rendered)
        current = self.document["subjects"]["project/serveros/SRV-0007"]["current"]
        self.assertEqual(sorted(current), sorted(projection.CITED_FIELDS))

    def test_the_projection_names_the_ledger_revision_it_was_built_from(self):
        self.assertEqual(self.document["ledger"]["revision"], "a65bd931c10c")

    def test_rebuilding_from_the_same_inputs_produces_the_same_bytes(self):
        """`reproducible: true` is a claim, and this is the test of it.
        `generated_at` is an argument rather than a clock read precisely so
        that this can be asserted."""
        again = projection.build(
            self.records, ledger_ref="records/projects/serveros/memory",
            ledger_revision="a65bd931c10c",
            generated_at="2026-09-13T06:00:00Z", generator="test")
        self.assertEqual(json.dumps(again, sort_keys=True),
                         json.dumps(self.document, sort_keys=True))

    def test_a_projection_with_no_revision_is_refused_rather_than_built(self):
        """It would be STALE the moment it existed. The contract's staleness
        rule makes an unnameable projection stale, and building one that is
        born stale is worse than building none."""
        with self.assertRaises(projection.ProjectionRefused):
            projection.build(self.records, ledger_ref="x", ledger_revision="",
                             generated_at="2026-09-13T06:00:00Z", generator="test")

    def test_an_open_question_is_listed_by_id_and_not_counted(self):
        """A count of open questions that does not say which ones is a count
        nobody can act on."""
        self.assertEqual(self.document["subjects"]["project/serveros/SRV-0003"]["open_questions"],
                         ["mem:open"])

    def test_a_question_someone_answered_stops_being_open(self):
        answered = [*self.records,
                    a_record(id="mem:answer", kind="decision",
                             supersedes="mem:open",
                             subject="project/serveros/SRV-0003")]
        document = projection.build(
            answered, ledger_ref="x", ledger_revision="b" * 12,
            generated_at="2026-09-13T06:00:00Z", generator="test")
        self.assertEqual(document["subjects"]["project/serveros/SRV-0003"]["open_questions"],
                         [])


class ManifestTests(unittest.TestCase):
    def test_the_manifest_has_every_field_the_state_root_contract_requires(self):
        document = projection.build([], ledger_ref="r", ledger_revision="abc",
                                    generated_at="2026-09-13T06:00:00Z",
                                    generator="test")
        manifest = projection.manifest_for(document, generated_at="2026-09-13T06:00:00Z",
                                           generator="test")
        self.assertEqual(sorted(manifest), sorted(projection.MANIFEST_FIELDS))

    def test_the_manifest_passes_the_reader_that_already_existed(self):
        """The writer here and `runtime/state_root.py` were written without
        reference to each other — the reader is older. This asserts the pair
        agrees, which is the only way either claim means anything."""
        state_root = _load("serveros_state_root", REPO / "runtime" / "state_root.py")
        import tempfile

        document = projection.build([], ledger_ref="r", ledger_revision="abc",
                                    generated_at="2026-09-13T06:00:00Z",
                                    generator="test")
        manifest = projection.manifest_for(document, generated_at="2026-09-13T06:00:00Z",
                                           generator="test")
        with tempfile.TemporaryDirectory() as tmp:
            projection.write(document, manifest, Path(tmp))
            read = state_root.read_manifest(Path(tmp))
        self.assertTrue(read["present"])
        self.assertEqual(read["problems"], [])
        self.assertIs(read["authority"], False)

    def test_the_declared_revision_is_read_back_off_the_last_at_sign(self):
        """A ref path may legally contain an `@`, and a parser that split on
        the first one would read half a path as a revision — which is still a
        string, so the answer would look fine."""
        manifest = {"source_refs": ["records/projects/a@b/memory@a65bd931"]}
        self.assertEqual(projection.ledger_revision_of(manifest), "a65bd931")

    def test_a_manifest_naming_no_revision_reads_as_none(self):
        self.assertIsNone(projection.ledger_revision_of({"source_refs": ["records/x"]}))
        self.assertIsNone(projection.ledger_revision_of({}))


class StalenessTests(unittest.TestCase):
    def test_a_projection_built_from_the_current_revision_is_current(self):
        manifest = {"source_refs": ["records/x@abc123"]}
        self.assertEqual(projection.staleness(manifest, ledger_revision="abc123")["status"],
                         "CURRENT")

    def test_a_projection_built_from_an_older_revision_is_stale(self):
        manifest = {"source_refs": ["records/x@abc123"]}
        result = projection.staleness(manifest, ledger_revision="def456")
        self.assertEqual(result["status"], "STALE")
        self.assertIn("regenerate", result["because"])

    def test_a_projection_that_cannot_name_its_revision_is_stale(self):
        """The contract's own words: "a projection that cannot name its
        revision is reported STALE". Not a third state — a caller acts the same
        way either way, and it regenerates."""
        result = projection.staleness({"source_refs": ["records/x"]},
                                      ledger_revision="abc123")
        self.assertEqual(result["status"], "STALE")
        self.assertIn("names no ledger revision", result["because"])

    def test_nothing_there_is_missing_and_missing_is_not_stale(self):
        result = projection.staleness(None, ledger_revision="abc123")
        self.assertEqual(result["status"], "MISSING")
        self.assertIn("read the ledger", result["because"])

    def test_the_status_of_a_real_directory_round_trips(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "memory"
            self.assertEqual(projection.status(out, ledger_revision="abc")["status"],
                             "MISSING")
            document = projection.build([], ledger_ref="r", ledger_revision="abc",
                                        generated_at="2026-09-13T06:00:00Z",
                                        generator="test")
            projection.write(document, projection.manifest_for(
                document, generated_at="2026-09-13T06:00:00Z", generator="test"), out)
            self.assertEqual(projection.status(out, ledger_revision="abc")["status"],
                             "CURRENT")
            self.assertEqual(projection.status(out, ledger_revision="moved")["status"],
                             "STALE")


# ---------------------------------------------------------------------------
# The layering between B and C
# ---------------------------------------------------------------------------


class LayeringTests(unittest.TestCase):
    """The founding rule: **if a fact exists only in Layer C, it is lost.**

    It is the one claim the three-layer design rests on, and until
    `memory/layering.py` nothing checked it. The provider contract assigns the
    enforcement to the caller in as many words — "discarded by the caller, not
    by the provider" — and there was no caller.
    """

    LEDGER_IDS = ["mem:the-tunnel-is-task-scoped", "mem:another"]

    def hits(self):
        double = _load("openviking_double2",
                       MEMORY / "fixtures" / "openviking_double.py")
        index = double.OpenVikingDouble()
        index.index([
            {"id": "mem:the-tunnel-is-task-scoped", "revision": "r1",
             "text": "the tunnel belongs to the task"},
            {"id": "mem:another", "revision": "r1", "text": "something else"},
        ])
        return index

    def test_every_hit_a_real_index_returns_is_citeable(self):
        """End to end through the fixture, against the ledger. A hit that could
        not be cited would be a fact living only in Layer C."""
        index = self.hits()
        result = layering.derivable(index.search("tunnel"), self.LEDGER_IDS)
        self.assertEqual(result["verdict"], "DERIVABLE")
        self.assertEqual([h["record_id"] for h in result["usable"]],
                         ["mem:the-tunnel-is-task-scoped"])

    def test_the_index_is_rebuildable_and_says_the_same_thing(self):
        """The rule stated as an operation: "Design as though the index is
        already empty." Delete it, rebuild from B, ask the same question."""
        index = self.hits()
        before = layering.usable_hits(index.search("tunnel"), self.LEDGER_IDS)
        index.invalidate(["mem:the-tunnel-is-task-scoped", "mem:another"])
        self.assertEqual(index.held(), [])
        index.index([
            {"id": "mem:the-tunnel-is-task-scoped", "revision": "r1",
             "text": "the tunnel belongs to the task"},
            {"id": "mem:another", "revision": "r1", "text": "something else"},
        ])
        after = layering.usable_hits(index.search("tunnel"), self.LEDGER_IDS)
        self.assertEqual(before["usable"], after["usable"])

    def test_a_hit_the_ledger_does_not_have_is_discarded_with_a_reason(self):
        """The check has to have teeth. An index that has drifted ahead of the
        ledger serves records that are not there any more, and that is the one
        thing the authority rule forbids outright."""
        hits = [{"record_id": "mem:invented", "revision": "r1", "score": 1.0}]
        result = layering.derivable(hits, self.LEDGER_IDS)
        self.assertEqual(result["verdict"], "NOT_DERIVABLE")
        self.assertEqual(result["discarded"][0]["reason"], layering.NOT_IN_LEDGER)
        self.assertIn("invented", result["discarded"][0]["detail"])

    def test_a_hit_with_no_id_is_discarded_and_is_not_an_error(self):
        """A provider that ignores the contract is a fact to report, not an
        exception to raise: the caller is mid-search and the other hits are
        still good."""
        result = layering.usable_hits([{"score": 1.0, "excerpt": "text"}],
                                      self.LEDGER_IDS)
        self.assertEqual(result["discarded"][0]["reason"], layering.NO_RECORD_ID)
        self.assertIn("citeable ids", result["discarded"][0]["why"])

    def test_one_unciteable_hit_is_enough_to_answer_no(self):
        """Not "mostly". The rule is that a fact existing only in the index is
        lost, and one such hit is one such fact."""
        hits = [{"record_id": "mem:another"}, {"record_id": "mem:missing"}]
        self.assertEqual(layering.derivable(hits, self.LEDGER_IDS)["verdict"],
                         "NOT_DERIVABLE")

    def test_an_empty_index_is_derivable_and_that_is_the_point(self):
        """Zero hits is the state the whole rule is designed around, and it
        must not read as a failure."""
        self.assertEqual(layering.derivable([], self.LEDGER_IDS)["verdict"],
                         "DERIVABLE")

    def test_the_module_runs_against_a_ledger_on_disk(self):
        """It has a `__main__`, so it is exercised. An entry point nothing
        calls is the same defect this module was written to close, one level
        down."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "one.yaml"
            ledger.write_text(yaml.safe_dump(a_record()), encoding="utf-8")
            hits = Path(tmp) / "hits.json"
            hits.write_text(json.dumps([
                {"record_id": "mem:the-tunnel-is-task-scoped", "revision": "r1"},
                {"record_id": "mem:gone", "revision": "r1"},
            ]))
            proc = subprocess.run(
                [sys.executable, str(MEMORY / "layering.py"),
                 "--hits", str(hits), "--ledger", str(ledger)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            result = json.loads(proc.stdout)
            self.assertEqual(result["verdict"], "NOT_DERIVABLE")
            self.assertEqual([h["record_id"] for h in result["usable"]],
                             ["mem:the-tunnel-is-task-scoped"])


# ---------------------------------------------------------------------------
# The surface
# ---------------------------------------------------------------------------


class CliTests(unittest.TestCase):
    def _ctl(self, *args):
        return subprocess.run(
            [sys.executable, str(REPO / "bin" / "serverosctl"), *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_audit_runs_and_reports_a_verdict(self):
        proc = self._ctl("memory", "audit", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertIn(report["verdict"], ("CONFORMANT", "DEFECT"))
        self.assertIn("not_checked", report)

    def test_audit_refuses_a_ledger_with_a_broken_record(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bad.yaml").write_text("id: nope\nkind: rumour\n")
            proc = self._ctl("memory", "audit", "--ledger-dir", tmp, "--json")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["verdict"], "DEFECT")

    def test_project_refuses_to_derive_from_a_defective_ledger(self):
        """A projection built from a ledger with a broken record in it is a
        derived copy of a defect, and the defect is much easier to see in the
        ledger than in a projection of it."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger"
            ledger.mkdir()
            (ledger / "bad.yaml").write_text("id: nope\nkind: rumour\n")
            out = Path(tmp) / "out"
            proc = self._ctl("memory", "project", "--ledger-dir", str(ledger),
                             "--out", str(out), "--json")
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(json.loads(proc.stdout)["written"], [])
            self.assertFalse(out.exists())

    def test_project_then_status_round_trips_through_the_filesystem(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger"
            ledger.mkdir()
            (ledger / "one.yaml").write_text(yaml.safe_dump(a_record()))
            out = Path(tmp) / "out"
            built = self._ctl("memory", "project", "--ledger-dir", str(ledger),
                              "--out", str(out),
                              "--generated-at", "2026-09-13T06:00:00Z", "--json")
            self.assertEqual(built.returncode, 0, built.stderr)
            status = self._ctl("memory", "status", "--ledger-dir", str(ledger),
                               "--projection-dir", str(out), "--json")
            self.assertEqual(json.loads(status.stdout)["status"], "CURRENT")

    def test_status_reports_stale_once_the_ledger_moves(self):
        """The reason the revision is a content hash and not HEAD: a ledger
        with an uncommitted record in it has moved, and a projection built
        before that record is not current however clean the tree looks."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger"
            ledger.mkdir()
            (ledger / "one.yaml").write_text(yaml.safe_dump(a_record()))
            out = Path(tmp) / "out"
            self._ctl("memory", "project", "--ledger-dir", str(ledger),
                      "--out", str(out), "--generated-at", "2026-09-13T06:00:00Z")
            (ledger / "two.yaml").write_text(
                yaml.safe_dump(a_record(id="mem:added-later",
                                        at="2026-09-13T07:00:00Z")))
            status = self._ctl("memory", "status", "--ledger-dir", str(ledger),
                               "--projection-dir", str(out), "--json")
            self.assertEqual(status.returncode, 1)
            self.assertEqual(json.loads(status.stdout)["status"], "STALE")

    def test_subject_reports_the_current_value_and_exits_1_when_there_is_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "one.yaml").write_text(yaml.safe_dump(a_record()))
            found = self._ctl("memory", "subject", "project/serveros/SRV-0007",
                              "--ledger-dir", tmp, "--json")
            self.assertEqual(found.returncode, 0, found.stderr)
            self.assertEqual(json.loads(found.stdout)["current"]["id"],
                             "mem:the-tunnel-is-task-scoped")
            absent = self._ctl("memory", "subject", "project/litellm/LIT-0104",
                               "--ledger-dir", tmp, "--json")
            self.assertEqual(absent.returncode, 2)
            self.assertIsNone(json.loads(absent.stdout)["current"])


if __name__ == "__main__":
    unittest.main()
