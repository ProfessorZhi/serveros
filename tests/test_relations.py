"""Tests for the derived relations index.

The interesting tests here are not "does it write a file". They are the three
that pin the properties separating an index from a second database:

  * a row cannot be written without naming where the relation already lives;
  * the file must be reproducible byte-for-byte from the canonical objects;
  * a relation with no producer is absent, not invented.

And, for the one predicate whose producer reads *captures* rather than cards,
a fourth: an attachment whose other end is not recorded is neither written as a
plausible row nor dropped silently. `attached_to` is where a wrong locator is
most believable — "the media belongs to this document" is exactly the shape of
statement that survives review because it is usually true.

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

from live_checkouts import readable_dir  # noqa: E402

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
    Path("/data/home/vince/workspace/records"),
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
        self.assertNotIn("discussed_in", project.PRODUCED)
        # And the declared set is wider than the produced set: that gap is
        # recorded, not hidden. A predicate is produced only where its records
        # are, so this fixture (cards, no captures) produces a subset.
        self.assertTrue(set(project.PRODUCED) < set(project.PREDICATES))
        self.assertTrue({r["rel"] for r in rows} <= set(project.PRODUCED))

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


class AttachedToTest(unittest.TestCase):
    """The third predicate, where a plausible row and a wrong one look alike.

    Both capture forms are exercised because they are genuinely different: a
    document's media belong to the document, a chat's attachments belong to the
    message that carried them. Flattening both to "attached to the capture" would
    pass a test written against either one alone.
    """

    LINK = "information/raw/feishu/chats/people/x/oc_x/links/doc"
    CHAT = "information/raw/feishu/chats/people/x/oc_x"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.records = self.root / "records"
        self.link = self.records / self.LINK
        self.chat = self.records / self.CHAT
        (self.link / "media").mkdir(parents=True)
        (self.chat / "files").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def write_manifest(self, capture: Path, payload: dict):
        capture.mkdir(parents=True, exist_ok=True)
        path = capture / "manifest.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def link_manifest(self, media=("a.png",), materialize=None):
        """Declare `media`; materialize only the names the test means to have.

        `materialize=None` writes every plain name, which is the ordinary case. A
        test about a declaration that did not survive to this node names it
        explicitly instead, so the fixture cannot accidentally create the file
        its own assertion says is missing.
        """
        for name in (media if materialize is None else materialize):
            if "/" in name:
                continue
            (self.link / "media" / name).write_bytes(b"x")
        (self.link / "content.md").write_text("body", encoding="utf-8")
        return self.write_manifest(self.link, {"media": list(media), "media_count": len(media)})

    def chat_manifest(self, by_message=None, backfilled=(), image_keys=(), materialize=None,
                      transcript=True, **extra):
        if transcript:
            (self.chat / "messages.jsonl").write_text('{"message_id": "om_1"}\n',
                                                      encoding="utf-8")
        paths = [entry.get("path") for entry in (by_message or {}).values()
                 if isinstance(entry, dict)]
        for path in (paths if materialize is None else materialize):
            if isinstance(path, str) and path.startswith("files/"):
                (self.chat / path).write_bytes(b"x")
        for name in image_keys:
            (self.chat / "files" / f"{name}.jpg").write_bytes(b"x")
        files = {"by_message": by_message or {}, "backfilled": list(backfilled),
                 "pending": "无附件待展开"}
        if image_keys:
            files["image_keys"] = list(image_keys)
        files.update(extra)
        return self.write_manifest(self.chat, {"files": files})

    def attached(self):
        rows, skipped = project.project(self.records)
        return [r for r in rows if r["rel"] == "attached_to"], skipped

    # -- a document capture ------------------------------------------------

    def test_media_hangs_off_the_document_it_came_from(self):
        self.link_manifest(media=("a.png", "b.png"))
        rows, skipped = self.attached()
        self.assertEqual(skipped, [])
        self.assertEqual(
            [(r["from"], r["to"]) for r in rows],
            [(f"records://{self.LINK}/media/a.png", f"records://{self.LINK}/content.md"),
             (f"records://{self.LINK}/media/b.png", f"records://{self.LINK}/content.md")],
        )

    def test_every_attachment_row_is_derived_from_its_manifest(self):
        self.link_manifest(media=("a.png",))
        rows, _ = self.attached()
        self.assertEqual(rows[0]["derived_from"], [f"records://{self.LINK}/manifest.json"])

    def test_a_declared_attachment_that_is_not_on_disk_is_a_gap_not_a_row(self):
        self.link_manifest(media=("a.png", "missing.png"), materialize=("a.png",))
        rows, skipped = self.attached()
        self.assertEqual([r["from"].rsplit("/", 1)[-1] for r in rows], ["a.png"])
        self.assertEqual(len(skipped), 1, skipped)
        self.assertIn("missing.png", skipped[0])
        self.assertIn("not on this node", skipped[0])

    def test_a_media_name_that_leaves_the_capture_is_refused(self):
        """A manifest could name anything; the locator is still confined to it."""
        self.link_manifest(media=("a.png", "../../outside.png"))
        rows, skipped = self.attached()
        self.assertEqual(len(rows), 1)
        self.assertTrue(any("outside.png" in gap for gap in skipped), skipped)
        self.assertFalse(any("outside" in r["from"] for r in rows))

    def test_an_absolute_media_name_is_refused(self):
        self.link_manifest(media=("a.png", "/etc/passwd"))
        rows, skipped = self.attached()
        self.assertEqual(len(rows), 1)
        self.assertTrue(any("/etc/passwd" in gap for gap in skipped), skipped)

    def test_declared_media_with_no_document_is_reported(self):
        """The document is one end of the relation; without it there is no row."""
        (self.link / "media").mkdir(parents=True, exist_ok=True)
        (self.link / "media" / "a.png").write_bytes(b"x")
        self.write_manifest(self.link, {"media": ["a.png"], "media_count": 1})
        rows, skipped = self.attached()
        self.assertEqual(rows, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("content.md", skipped[0])

    # -- a chat capture ----------------------------------------------------

    def test_an_attachment_hangs_off_the_message_that_carried_it(self):
        self.chat_manifest(by_message={"om_1": {"path": "files/a.jpg"}})
        rows, skipped = self.attached()
        self.assertEqual(skipped, [])
        self.assertEqual(
            [(r["from"], r["to"]) for r in rows],
            [(f"records://{self.CHAT}/files/a.jpg",
              f"records://{self.CHAT}/messages.jsonl#om_1")],
        )

    def test_an_attachment_recorded_against_no_message_is_a_gap(self):
        self.chat_manifest(by_message={"": {"path": "files/a.jpg"}})
        rows, skipped = self.attached()
        self.assertEqual(rows, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("no message", skipped[0])

    def test_an_attachment_path_that_leaves_the_capture_is_refused(self):
        self.chat_manifest(by_message={"om_1": {"path": "../../outside.jpg"}})
        rows, skipped = self.attached()
        self.assertEqual(rows, [])
        self.assertTrue(any("no usable" in gap for gap in skipped), skipped)

    def test_an_attachment_recorded_but_absent_from_disk_is_a_gap(self):
        self.chat_manifest(by_message={"om_1": {"path": "files/gone.jpg"}},
                           materialize=())
        rows, skipped = self.attached()
        self.assertEqual(rows, [])
        self.assertEqual(len(skipped), 1, skipped)
        self.assertIn("not on this node", skipped[0])

    def test_an_attachment_with_no_transcript_is_reported_once(self):
        self.chat_manifest(by_message={"om_1": {"path": "files/a.jpg"},
                                       "om_2": {"path": "files/b.jpg"}},
                           transcript=False)
        rows, skipped = self.attached()
        self.assertEqual(rows, [])
        self.assertEqual(len(skipped), 1, skipped)
        self.assertIn("2 attachment(s)", skipped[0])

    def test_a_materialized_file_no_message_records_is_reported(self):
        self.chat_manifest(by_message={"om_1": {"path": "files/a.jpg"}},
                           backfilled=["a.jpg", "orphan.jpg"])
        rows, skipped = self.attached()
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(skipped), 1, skipped)
        self.assertIn("orphan.jpg", skipped[0])

    def test_an_attributed_file_is_not_also_reported_as_unattributed(self):
        """One absence must not be counted twice, by two different routes."""
        self.chat_manifest(by_message={"om_1": {"path": "files/a.jpg"}},
                           backfilled=["a.jpg"])
        rows, skipped = self.attached()
        self.assertEqual(len(rows), 1)
        self.assertEqual(skipped, [])

    def test_a_file_lost_after_attribution_is_reported_once(self):
        """Attribution survives a missing file: the manifest did map that name."""
        self.chat_manifest(by_message={"om_1": {"path": "files/a.jpg"}},
                           backfilled=["a.jpg"])
        (self.chat / "files" / "a.jpg").unlink()
        rows, skipped = self.attached()
        self.assertEqual(rows, [])
        self.assertEqual(len(skipped), 1, skipped)
        self.assertIn("not on this node", skipped[0])

    def test_an_attachment_key_with_no_message_is_reported(self):
        """`image_keys` names neither a path nor a message, so it yields no row."""
        self.chat_manifest(image_keys=["img_1"])
        rows, skipped = self.attached()
        self.assertEqual(rows, [])
        self.assertEqual(len(skipped), 1, skipped)
        self.assertIn("img_1", skipped[0])

    def test_a_zip_expansion_is_reported_verbatim_and_never_parsed(self):
        """The field holds a path and a sentence; neither end is reliable."""
        self.chat_manifest(zip_expanded_into="reference/x (git repo: y)")
        rows, skipped = self.attached()
        self.assertEqual(rows, [])
        self.assertEqual(len(skipped), 1, skipped)
        self.assertIn("reference/x (git repo: y)", skipped[0])

    # -- the projection's own rules ----------------------------------------

    def test_a_manifest_that_cannot_be_read_is_reported_not_raised(self):
        (self.chat / "manifest.json").write_text("{not json", encoding="utf-8")
        rows, skipped = self.attached()
        self.assertEqual(rows, [])
        self.assertEqual(len(skipped), 1, skipped)
        self.assertIn("unreadable", skipped[0])

    def test_an_attachment_row_carries_no_body(self):
        self.link_manifest(media=("a.png",))
        rows, _ = self.attached()
        for entry in rows:
            for field in ("from", "to"):
                self.assertLess(len(entry[field]), 400, entry)
                self.assertNotIn("\n", entry[field])

    def test_the_projection_is_deterministic_with_attachments(self):
        self.link_manifest(media=("a.png", "missing.png"))
        self.chat_manifest(by_message={"om_1": {"path": "files/a.jpg"}, "": {}},
                           backfilled=["orphan.jpg"], image_keys=["img_1"])
        first, first_skipped = project.project(self.records)
        second, second_skipped = project.project(self.records)
        self.assertEqual(project.dumps(first), project.dumps(second))
        self.assertEqual(first_skipped, second_skipped)

    def test_a_capture_that_is_not_there_is_not_a_finding(self):
        """A records root with no capture tree projects cards, and says nothing."""
        (self.records / "projects" / "litellm" / "items" / "LIT-0101").mkdir(parents=True)
        self.write_card_for_litellm()
        rows, skipped = project.project(self.records)
        self.assertEqual(skipped, [])
        self.assertTrue(rows)

    def write_card_for_litellm(self):
        (self.records / "projects" / "litellm" / "items" / "LIT-0101" / "card.yaml").write_text(
            yaml.safe_dump(card("LIT-0101", "litellm"), allow_unicode=True), encoding="utf-8"
        )

    def test_the_helper_refuses_every_shape_it_cannot_confine(self):
        base = self.link
        for refused in ("/etc/passwd", "../../outside.png", "a\\b.png", "", "   ", None, 7):
            self.assertIsNone(project._inside(base, refused), refused)
        inside = project._inside(base, "media/a.png")
        self.assertEqual(inside, (base / "media" / "a.png").resolve())


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
            if readable_dir(candidate / "projects"):
                return candidate
        self.skipTest("no records checkout reachable")

    def test_every_card_projects_without_untyped_references(self):
        rows, skipped = project.project(self.records_root())
        # `skipped` carries two kinds of finding and only one of them is a
        # failure. A card whose source reference cannot be typed is a defect in
        # the records; an attachment whose other end was never recorded is a
        # measurement of what the captures do not say, and it is expected to be
        # non-empty. Entries are prefixed with the records-relative path of the
        # object they came from, which is what separates them here.
        card_gaps = [gap for gap in skipped if gap.startswith("projects/")]
        self.assertEqual(card_gaps, [],
                         "a card carries a source reference the projection cannot type")
        self.assertTrue(rows)
        self.assertTrue({r["rel"] for r in rows} <= set(project.PREDICATES))

    def test_the_real_captures_produce_attachment_rows(self):
        """`attached_to` is not fixture-only: the live captures have a producer."""
        rows, skipped = project.project(self.records_root())
        attached = [r for r in rows if r["rel"] == "attached_to"]
        self.assertTrue(attached, "no capture on this node yields an attachment relation")
        for entry in attached:
            self.assertTrue(entry["from"].startswith("records://information/raw/"), entry)
            self.assertIn("/manifest.json", entry["derived_from"][0], entry)
        # Both capture forms are present in the live tree, and both are reported
        # rather than silently absorbed: the document form's target is a
        # `content.md`, the chat form's is a transcript fragment.
        targets = {r["to"] for r in attached}
        self.assertTrue(any(t.endswith("/content.md") for t in targets), sorted(targets)[:5])
        self.assertTrue(any("#om_" in t for t in targets), sorted(targets)[:5])
        self.assertTrue(skipped)

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
