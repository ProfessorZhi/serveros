"""Tests for the derived relations index.

The interesting tests here are not "does it write a file". They are the three
that pin the properties separating an index from a second database:

  * a row cannot be written without naming where the relation already lives;
  * the file must be reproducible byte-for-byte from the canonical objects;
  * a relation with no producer is absent, not invented.

Hermetic: the records root is a synthetic tree in a temp directory. The
projection is also run once against this repository's real `records/` checkout
when it is reachable, because a projection that only works on a fixture has not
been shown to work.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "relations" / "project.py"
SCHEMA = ROOT / "relations" / "relations.schema.json"

_spec = importlib.util.spec_from_file_location("serveros_relations_project", PROJECT)
project = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(project)

RECORDS_CANDIDATES = (
    Path("/opt/applications/data/home-hwz/workspace/records"),
    Path("/opt/applications/data/home-hwz/workspace/worktrees/records/SRV-0003"),
)


def card(item_id, prj, refs=(), acceptance=("a",)):
    return {
        "schema": "serveros/item.v2",
        "id": item_id,
        "project": prj,
        "kind": "ops",
        "title": f"{item_id} title",
        "source_refs": list(refs),
        "acceptance": list(acceptance),
    }


class ProjectionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.records = self.root / "records"
        (self.records / "projects" / "litellm" / "items" / "LIT-0101").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def write_card(self, item_id, prj, refs=(), body=None, name="card.yaml"):
        payload = card(item_id, prj, refs)
        if body:
            payload.update(body)
        path = self.records / "projects" / prj / "items" / item_id / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
        return path

    def test_a_card_yields_containment_and_derivation(self):
        self.write_card("LIT-0101", "litellm", refs=["brief.md"])
        rows, skipped = project.project(self.records)
        self.assertEqual(skipped, [])
        self.assertEqual(
            [(r["from"], r["rel"], r["to"]) for r in rows],
            [("item:LIT-0101", "belongs_to", "project:litellm"),
             ("item:LIT-0101", "derived_from",
              "records://projects/litellm/items/LIT-0101/brief.md")],
        )

    def test_every_row_names_its_own_derivation(self):
        self.write_card("LIT-0101", "litellm", refs=["brief.md"])
        rows, _ = project.project(self.records)
        for entry in rows:
            self.assertTrue(entry["derived_from"], entry)
            self.assertTrue(all("/" in source for source in entry["derived_from"]), entry)

    def test_a_relation_with_no_source_cannot_be_written(self):
        """The structural guard: an underivable row is refused, not stored."""
        with self.assertRaises(project.InvalidRow):
            project.row("item:LIT-0101", "supports", "item:LIT-0102", [])

    def test_an_unlisted_predicate_cannot_be_written(self):
        with self.assertRaises(project.InvalidRow):
            project.row("item:LIT-0101", "is_near", "item:LIT-0102", "records://x/card.yaml")

    def test_a_relation_with_no_producer_is_absent_not_invented(self):
        """`discussed_in` has no producer, so no row for it exists."""
        self.write_card("LIT-0101", "litellm", refs=["brief.md"])
        rows, _ = project.project(self.records)
        self.assertNotIn("discussed_in", {r["rel"] for r in rows})
        # And the declared set is wider than the produced set: that gap is
        # recorded, not hidden.
        self.assertTrue(set(project.PRODUCED) < set(project.PREDICATES))
        self.assertTrue(set(project.PRODUCED) == {r["rel"] for r in rows})

    def test_the_projection_is_deterministic(self):
        self.write_card("LIT-0101", "litellm", refs=["brief.md", "plan.md"])
        self.write_card("LIT-0102", "litellm", refs=["brief.md"])
        first, _ = project.project(self.records)
        second, _ = project.project(self.records)
        self.assertEqual(project.dumps(first), project.dumps(second))

    def test_duplicate_edges_merge_their_derivations(self):
        rows = project.normalise([
            project.row("item:A", "belongs_to", "project:p", "records://a"),
            project.row("item:A", "belongs_to", "project:p", "records://b"),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["derived_from"], ["records://a", "records://b"])

    def test_a_bare_reference_is_anchored_to_the_card_not_the_cwd(self):
        """The projection must not depend on where it was invoked from."""
        self.write_card("LIT-0101", "litellm", refs=["source-events.jsonl#L2"])
        (self.records / "projects" / "litellm" / "items" / "LIT-0101"
         / "source-events.jsonl").write_text("{}\n{}\n", encoding="utf-8")
        locator = project._source_locator(
            "source-events.jsonl#L2",
            self.records / "projects" / "litellm" / "items" / "LIT-0101",
            self.records,
        )
        self.assertEqual(
            locator,
            "records://projects/litellm/items/LIT-0101/source-events.jsonl#L2",
        )

    def test_a_structured_reference_keeps_its_own_type(self):
        locator = project._source_locator(
            {"type": "user-feedback", "locator": "codex-thread:litellm-e2e"},
            self.records, self.records,
        )
        self.assertEqual(locator, "codex-thread:litellm-e2e")

    def test_a_reference_escaping_the_records_root_is_refused(self):
        self.assertIsNone(
            project._source_locator("../../../../etc/passwd", self.records, self.records)
        )

    def test_an_untyped_reference_is_reported_not_dropped(self):
        self.write_card("LIT-0101", "litellm", refs=[{"nonsense": True}])
        _, skipped = project.project(self.records)
        self.assertEqual(len(skipped), 1)
        self.assertIn("LIT-0101/card.yaml", skipped[0])


class IndexFileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.records = self.root / "records"
        path = self.records / "projects" / "litellm" / "items" / "LIT-0101"
        path.mkdir(parents=True)
        path.joinpath("card.yaml").write_text(
            yaml.safe_dump(card("LIT-0101", "litellm", refs=["brief.md"]), allow_unicode=True),
            encoding="utf-8",
        )
        self.index = self.root / "state" / "serveros" / "relations" / "current.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def build(self):
        rows, _ = project.project(self.records)
        project.write(rows, self.index)
        return rows

    def test_a_rebuilt_index_verifies(self):
        self.build()
        self.assertEqual(project.verify(self.records, self.index), [])

    def test_the_index_is_deletable_and_recoverable(self):
        """The property the whole design rests on."""
        self.build()
        before = self.index.read_bytes()
        self.index.unlink()
        self.assertFalse(self.index.exists())
        self.build()
        self.assertEqual(self.index.read_bytes(), before)

    def test_a_hand_added_row_is_detected(self):
        rows = self.build()
        rows.append(project.row("item:LIT-0101", "supports", "item:LIT-0102", "records://invented"))
        project.write(rows, self.index)
        reasons = project.verify(self.records, self.index)
        self.assertTrue(reasons)
        self.assertIn("no record derives", reasons[0])

    def test_a_stale_row_is_detected(self):
        rows = self.build()
        project.write([r for r in rows if r["rel"] != "belongs_to"], self.index)
        reasons = project.verify(self.records, self.index)
        self.assertTrue(reasons)
        self.assertIn("missing a relation", reasons[0])

    def test_a_changed_derivation_is_detected(self):
        rows = self.build()
        rows[0]["derived_from"] = ["records://somewhere/else.yaml"]
        project.write(rows, self.index)
        reasons = project.verify(self.records, self.index)
        self.assertTrue(any("derivation differs" in r for r in reasons), reasons)

    def test_the_file_is_locator_only(self):
        self.build()
        text = self.index.read_text(encoding="utf-8")
        for line in text.splitlines():
            entry = json.loads(line)
            self.assertEqual(set(entry), {"from", "rel", "to", "derived_from"})

    def test_load_rejects_an_unknown_field(self):
        self.build()
        entry = json.loads(self.index.read_text(encoding="utf-8").splitlines()[0])
        entry["body"] = "a document body has no business here"
        self.index.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        with self.assertRaises(project.InvalidRow):
            project.load(self.index)

    def test_load_rejects_a_row_without_a_derivation(self):
        self.build()
        self.index.write_text(
            json.dumps({"from": "item:A", "rel": "supports", "to": "item:B"}) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(project.InvalidRow):
            project.load(self.index)

    def test_verify_reports_a_missing_file_rather_than_crashing(self):
        reasons = project.verify(self.records, self.root / "nope.jsonl")
        self.assertTrue(reasons)


class SchemaAgreementTest(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    def test_the_schema_is_the_declared_one(self):
        self.assertEqual(self.schema["$id"], "serveros/relations.v1")

    def test_the_schema_predicate_set_is_the_code_predicate_set(self):
        declared = self.schema["properties"]["rel"]["enum"]
        self.assertEqual(sorted(declared), sorted(project.PREDICATES))

    def test_the_schema_requires_a_derivation(self):
        self.assertIn("derived_from", self.schema["required"])
        self.assertEqual(self.schema["properties"]["derived_from"]["minItems"], 1)

    def test_the_schema_admits_no_extra_fields(self):
        self.assertFalse(self.schema["additionalProperties"])

    def test_the_documented_split_matches_the_code(self):
        """schema.yaml's implemented/declared_only split must match PRODUCED."""
        document = yaml.safe_load((ROOT / "relations" / "schema.yaml").read_text(encoding="utf-8"))
        predicates = document["predicates"]
        self.assertEqual(sorted(predicates["implemented"]), sorted(project.PRODUCED))
        self.assertEqual(
            sorted(set(predicates["implemented"]) | set(predicates["declared_only"])),
            sorted(project.PREDICATES),
        )


class RealRecordsTest(unittest.TestCase):
    """Run the projection against the real records checkout, when it is there.

    A projection that only works on its own fixture has not been shown to work.
    Skipped rather than failed when no checkout is reachable, so the suite does
    not depend on this machine's layout — but it is not silently passed.
    """

    def records_root(self):
        for candidate in RECORDS_CANDIDATES:
            if (candidate / "projects").is_dir():
                return candidate
        self.skipTest("no records checkout reachable")

    def test_every_card_projects_without_untyped_references(self):
        rows, skipped = project.project(self.records_root())
        self.assertEqual(skipped, [], "a card carries a source reference the projection cannot type")
        self.assertTrue(rows)
        self.assertTrue({r["rel"] for r in rows} <= set(project.PREDICATES))

    def test_no_row_carries_a_body(self):
        rows, _ = project.project(self.records_root())
        for entry in rows:
            for field in ("from", "to"):
                self.assertLess(len(entry[field]), 400, entry)

    def test_the_projection_is_reproducible_from_the_command_line(self):
        records = self.records_root()
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "current.jsonl"
            first = subprocess.run(
                [sys.executable, str(PROJECT), "--records", str(records), "--write", str(index)],
                stdout=subprocess.PIPE, text=True,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            snapshot = index.read_bytes()
            index.unlink()
            subprocess.run(
                [sys.executable, str(PROJECT), "--records", str(records), "--write", str(index)],
                stdout=subprocess.PIPE, text=True, check=True,
            )
            self.assertEqual(index.read_bytes(), snapshot)
            verified = subprocess.run(
                [sys.executable, str(PROJECT), "--records", str(records), "--verify", str(index)],
                stdout=subprocess.PIPE, text=True,
            )
            self.assertEqual(verified.returncode, 0, verified.stdout)


if __name__ == "__main__":
    unittest.main()
