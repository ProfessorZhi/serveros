"""同 id 换内容重放：要么真接受，要么什么都没写并且说出来。

Measured 2026-10-08 through the installed CLI (`/data/home/vince/.local/bin/workengine`,
tree `0a24696b`) with `probe/probe-conflicting-replay-v2.py`: four of the five
things a second call could change were answered with **exit 0 and a value the
caller never wrote**. `register_source` with a second `--locator`, and
`record_observation` with a second `--content-hash`, `--semantic-type` or
`--source-id`, all returned the OLD row and reported no problem, because all
three entry points did `INSERT OR IGNORE` and then returned whatever came back
from the read. The fifth, `record_claim`, silently kept its first locator the
same way; and when it was handed a different observation set it folded it in
without saying so — the answer was the bare `claims` row, which names no
observations at all.

That is this module's recurring defect — a fact about the reader reported as a
fact about the world — and it is the one `create_candidate` was fixed for on
2026-09-25. What its docstring settled is the shape used here: look the row up
FIRST and answer the two cases separately.

Two legitimate answers exist for a conflicting replay, and which one is right
depends on the field, not on taste:

  * a SINGLE-VALUED field (`locator`, `status`, `content_hash`, a source) has to
    lose one of its two values, so a second differing one is refused by name with
    both values in the sentence — nothing is written;
  * a SET-valued field (a claim's observations) can grow without losing anything,
    so it is extended — and the stored set is then named in the answer, because a
    caller who passed one observation is entitled to see the others.

Every test below turns one of those measurements around. The controls matter as
much as the refusals: a guard that refused every second call would satisfy the
refusals alone, so the same-value replays, the second-revision source and the
extension that is allowed are all asserted here too.
"""
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from workengine import WorkItem
from workengine.store import ServerOSStore
from workengine.cli import main

H = "a" * 64
H2 = "b" * 64
H3 = "c" * 64
OBSERVATION_COLUMNS = {"observation_id", "source_id", "semantic_type", "source_origin",
                       "carrier", "information_form", "content_hash", "created_at"}


class _StoreFixture(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.state = Path(self.td.name) / "state"
        self.artifacts = Path(self.td.name) / "artifacts"
        self.s = ServerOSStore(self.state)
        self.s.register_project("demo", "DEM")
        self.s.create_item(WorkItem("DEM-0001", "demo", "x", "test", "/card"))
        self.s.register_source("docs", "doc-1", revision="r1", locator="file:///tmp/doc-1")
        self.s.record_observation("docs:doc-1:r1", "obs-1", semantic_type="text", content_hash=H)
        self.s.record_claim("clm-1", "file:///tmp/clm-1", ["obs-1"])

    def tearDown(self):
        self.td.cleanup()

    def one(self, sql, params=()):
        """One value, read on a connection of its own.

        A second instrument on purpose: the assertions about what was written are
        not allowed to be readings of the object under test.
        """
        c = sqlite3.connect(f"file:{self.state}/serveros.db?mode=ro", uri=True)
        try:
            return c.execute(sql, params).fetchone()[0]
        finally:
            c.close()

    def _cli(self, *args):
        """One CLI call, `--state-dir` right after the group name; both streams captured.

        `main` RETURNS its exit code rather than raising `SystemExit`, so the
        return value is the code — reading it as "did not raise" would turn a
        refusal into a pass.
        """
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main([args[0], "--state-dir", str(self.state), *args[1:]])
        return rc, err.getvalue()


class ASingleValuedFieldIsRefusedByName(_StoreFixture):
    def test_a_second_locator_for_a_registered_source_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self.s.register_source("docs", "doc-1", revision="r1", locator="file:///elsewhere")
        said = str(ctx.exception)
        self.assertIn("file:///tmp/doc-1", said)
        self.assertIn("file:///elsewhere", said)
        self.assertEqual(self.one("SELECT locator FROM source_objects WHERE source_id='docs:doc-1:r1'"),
                         "file:///tmp/doc-1")
        self.assertEqual(self.one("SELECT COUNT(*) FROM source_objects"), 1)

    def test_a_second_revision_is_another_source_and_not_a_conflict(self):
        # The guard must be about the key, not about the locator: the same
        # document read at a second revision is a second source.
        self.s.register_source("docs", "doc-1", revision="r2", locator="file:///elsewhere")
        self.assertEqual(self.one("SELECT COUNT(*) FROM source_objects"), 2)

    def test_a_second_content_hash_for_an_observation_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self.s.record_observation("docs:doc-1:r1", "obs-1", semantic_type="text", content_hash=H2)
        said = str(ctx.exception)
        self.assertIn(repr(H), said)
        self.assertIn(repr(H2), said)
        self.assertEqual(self.one("SELECT content_hash FROM observations WHERE observation_id='obs-1'"), H)

    def test_a_second_semantic_type_is_refused(self):
        with self.assertRaises(ValueError):
            self.s.record_observation("docs:doc-1:r1", "obs-1",
                                      semantic_type="completed_in_source", content_hash=H)
        self.assertEqual(self.one("SELECT semantic_type FROM observations WHERE observation_id='obs-1'"), "text")

    def test_a_second_source_for_one_observation_id_is_refused(self):
        # The primary key is `observation_id` and the UNIQUE constraint is
        # `(source_id, observation_id)`, so this conflicts with the PRIMARY KEY —
        # and `INSERT OR IGNORE` swallowed that as silently as everything else.
        self.s.register_source("docs", "doc-2", revision="r1", locator="file:///tmp/doc-2")
        with self.assertRaises(ValueError) as ctx:
            self.s.record_observation("docs:doc-2:r1", "obs-1", semantic_type="text", content_hash=H)
        said = str(ctx.exception)
        self.assertIn("docs:doc-1:r1", said)
        self.assertIn("docs:doc-2:r1", said)
        self.assertEqual(self.one("SELECT source_id FROM observations WHERE observation_id='obs-1'"),
                         "docs:doc-1:r1")

    def test_a_second_value_for_a_defaulted_field_is_refused_too(self):
        # `carrier` and its two neighbours are defaults, not decoration: a call
        # that spells one out differently is asserting a different reading. A
        # guard that compared only the two obvious fields would pass everything
        # above and fail here.
        with self.assertRaises(ValueError) as ctx:
            self.s.record_observation("docs:doc-1:r1", "obs-1", semantic_type="text",
                                      content_hash=H, carrier="meeting")
        self.assertIn("meeting", str(ctx.exception))
        self.assertEqual(self.one("SELECT carrier FROM observations WHERE observation_id='obs-1'"), "other")

    def test_a_second_locator_for_a_claim_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self.s.record_claim("clm-1", "file:///tmp/clm-2", ["obs-1"])
        said = str(ctx.exception)
        self.assertIn("file:///tmp/clm-1", said)
        self.assertIn("file:///tmp/clm-2", said)
        self.assertEqual(self.one("SELECT locator FROM claims WHERE claim_id='clm-1'"), "file:///tmp/clm-1")

    def test_a_second_status_for_a_claim_is_refused(self):
        # Library-level: `claim --help` has no `--status`, so the installed
        # entrance cannot express this one and no probe can judge it.
        with self.assertRaises(ValueError) as ctx:
            self.s.record_claim("clm-1", "file:///tmp/clm-1", ["obs-1"], status="WITHDRAWN")
        self.assertIn("WITHDRAWN", str(ctx.exception))
        self.assertEqual(self.one("SELECT status FROM claims WHERE claim_id='clm-1'"), "ACTIVE")

    def test_a_refused_claim_leaves_its_observations_alone(self):
        # The refusal sentence says nothing was written, so that is asserted.
        self.s.record_observation("docs:doc-1:r1", "obs-2", semantic_type="text", content_hash=H3)
        with self.assertRaises(ValueError):
            self.s.record_claim("clm-1", "file:///tmp/clm-2", ["obs-1", "obs-2"])
        self.assertEqual(self.one("SELECT COUNT(*) FROM claim_observations WHERE claim_id='clm-1'"), 1)


class AReplayIsStillAReplay(_StoreFixture):
    def test_the_same_source_again_returns_the_stored_row(self):
        again = self.s.register_source("docs", "doc-1", revision="r1", locator="file:///tmp/doc-1")
        self.assertEqual(again["locator"], "file:///tmp/doc-1")
        self.assertEqual(self.one("SELECT COUNT(*) FROM source_objects"), 1)

    def test_the_same_observation_again_returns_the_stored_row_unchanged(self):
        # No field is added to this answer: `test_v3_information.py` compares the
        # whole dict, and a caller who replays is entitled to exactly what the
        # caller who wrote it got.
        again = self.s.record_observation("docs:doc-1:r1", "obs-1", semantic_type="text", content_hash=H)
        c = sqlite3.connect(f"file:{self.state}/serveros.db?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        try:
            row = dict(c.execute("SELECT * FROM observations WHERE observation_id='obs-1'").fetchone())
        finally:
            c.close()
        self.assertEqual(again, row)
        self.assertEqual(set(again), OBSERVATION_COLUMNS)
        self.assertEqual(self.one("SELECT COUNT(*) FROM observations"), 1)

    def test_the_same_claim_again_returns_the_stored_row(self):
        again = self.s.record_claim("clm-1", "file:///tmp/clm-1", ["obs-1"])
        self.assertEqual(again["claim_id"], "clm-1")
        self.assertEqual(self.one("SELECT COUNT(*) FROM claims"), 1)


class AClaimsObservationsAccumulateAndAreNamed(_StoreFixture):
    def setUp(self):
        super().setUp()
        self.s.record_observation("docs:doc-1:r1", "obs-2", semantic_type="text", content_hash=H3)

    def _joined(self):
        c = sqlite3.connect(f"file:{self.state}/serveros.db?mode=ro", uri=True)
        try:
            return [r[0] for r in c.execute(
                "SELECT observation_id FROM claim_observations WHERE claim_id='clm-1' ORDER BY 1")]
        finally:
            c.close()

    def test_a_second_observation_extends_the_claim_and_the_answer_names_the_set(self):
        again = self.s.record_claim("clm-1", "file:///tmp/clm-1", ["obs-2"])
        self.assertEqual(self._joined(), ["obs-1", "obs-2"])
        self.assertEqual(again["observation_ids"], ["obs-1", "obs-2"])
        self.assertEqual(again["locator"], "file:///tmp/clm-1")

    def test_extending_twice_does_not_duplicate_the_join(self):
        self.s.record_claim("clm-1", "file:///tmp/clm-1", ["obs-2"])
        self.s.record_claim("clm-1", "file:///tmp/clm-1", ["obs-2"])
        self.assertEqual(self._joined(), ["obs-1", "obs-2"])

    def test_the_first_call_names_its_observations_as_well(self):
        # The create path and the replay path answer in the same shape, so a
        # caller does not have to know which one it got.
        first = self.s.record_claim("clm-2", "file:///tmp/clm-2", ["obs-1"])
        self.assertEqual(first["observation_ids"], ["obs-1"])

    def test_a_conflict_is_not_a_reason_to_drop_the_extension(self):
        # The extension and the refusal are about different fields: refusing a
        # changed locator must not also lose the observations this call named.
        with self.assertRaises(ValueError):
            self.s.record_claim("clm-1", "file:///tmp/clm-other", ["obs-2"])
        self.assertEqual(self._joined(), ["obs-1"])


class TheInstalledEntranceAnswersTheSameWay(_StoreFixture):
    def test_a_conflicting_replay_is_a_json_refusal_and_exit_2(self):
        rc, err = self._cli("source", "docs", "doc-1", "--revision", "r1", "--locator", "file:///elsewhere")
        self.assertEqual(rc, 2, err)
        self.assertNotIn("Traceback", err)
        message = json.loads(err)["error"]["message"]
        self.assertIn("file:///tmp/doc-1", message)
        self.assertIn("file:///elsewhere", message)

    def test_the_same_replay_through_the_entrance_still_succeeds(self):
        rc, err = self._cli("source", "docs", "doc-1", "--revision", "r1", "--locator", "file:///tmp/doc-1")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.one("SELECT COUNT(*) FROM source_objects"), 1)

    def test_a_claim_extended_through_the_entrance_names_the_stored_set(self):
        self.s.record_observation("docs:doc-1:r1", "obs-2", semantic_type="text", content_hash=H3)
        rc, out = self._cli("claim", "clm-1", "--locator", "file:///tmp/clm-1", "--observation", "obs-2")
        self.assertEqual(rc, 0, out)
        c = sqlite3.connect(f"file:{self.state}/serveros.db?mode=ro", uri=True)
        try:
            joined = [r[0] for r in c.execute(
                "SELECT observation_id FROM claim_observations WHERE claim_id='clm-1' ORDER BY 1")]
        finally:
            c.close()
        self.assertEqual(joined, ["obs-1", "obs-2"])


if __name__ == "__main__":
    unittest.main()
