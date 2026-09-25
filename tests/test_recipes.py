"""§7's three finite loops, and the four properties their records must have.

THE CRITERION THIS FILE IS ABOUT
--------------------------------
    | SYS-17 | §7 Perception / Work / Report 三个自动 recipe |

§7 states four properties for those loops, and none of them is a thing a loop
can be said to have. Each is a property of the RECORD the run leaves:

  * 「重启不把一个工作重复派两次」 -- the key is `recipe + scope + period` and it
    is the record path, so the second run of a key finds the first one's file.
  * 「无变化不产生 LLM 工作」 -- the increment gate is upstream of the model
    boundary, so a quiet period never reaches it. Ordering, not a promise.
  * 「待决策只阻塞依赖」 -- `depends_on: []` means declared-none and an absent key
    means UNKNOWN, and only the first is safe to walk past.
  * a budget is a ceiling somebody agreed to, and an unbound transport is a gap
    rather than a quiet period.

So the tests below are mostly about the record, and the ones that matter most
are the ones a plausible-looking implementation would fail:

  * `test_no_change_does_not_reach_the_model_step` builds a real checkout, sets a
    cursor to its current revision, binds a COUNTING transport and gives a budget
    of five -- and asserts the counter was never called. An implementation that
    checked for a delta inside the model step would pass every other test here
    and fail this one.
  * `test_the_budget_refusal_comes_before_the_transport_refusal` gives a run both
    problems at once. Only the cheaper fact may be reported: a run that said
    MODEL_UNBOUND when its ceiling was already spent would hide a number the
    caller set behind a binding nobody did.
  * `test_every_word_the_engine_can_emit_is_in_the_contract` scans the module's
    own literals against the contract's vocabulary, so a new stop word in the
    engine with no entry in the contract fails here rather than in a cron log
    nobody reads.
  * `test_a_claim_with_no_outcome_is_not_a_success_and_not_a_no_op` is the one
    the whole two-write design exists for. A single write would render "somebody
    started this and nobody knows how far they got" as "nobody has done this".
"""
import importlib.util
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "serveros_recipes", ROOT / "runtime" / "recipes.py")
recipes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recipes)

#: The words a record can be in, and the two a reader returns for an input it
#: could not read. They are NOT stops: `CLAIMED` is the state a run writes
#: before its first step, and `UNREADABLE` is what a record or a list that did
#: not parse answers. Named here so that the vocabulary scan below can be exact
#: rather than a list of exceptions that grows quietly.
NON_STOP_WORDS = {
    "CLAIMED", "DONE", "STOPPED", "NEVER_RUN",          # record states
    "READ", "UNPRODUCED", "UNREADABLE",                 # reader states
    "UNSTATED",                                         # a card with no state
    "HEAD",                                             # `git rev-parse HEAD`
}

PERIOD = {"start": "2026-09-20T00:00:00+09:00", "end": "2026-09-20T23:59:00+09:00",
          "id": "20260920T0000+0900-20260920T2359+0900"}


def running_record(**overrides):
    """The record the engine owns, as `Context` reads it.

    Only for the unit-level tests that drive one method. Every run-level test
    goes through `Engine.run`, which builds this itself -- a test that built
    its own would be testing a shape the engine does not write.
    """
    record = {"recipe": "work", "budget": {"given": 0, "spent": 0},
              "model_calls": 0, "steps": [], "notes": [], "cursor": {},
              "would_call": None, "outcomes": {}}
    record.update(overrides)
    return record


def a_checkout(directory: Path, *, filename="work.txt", body="one\n"):
    """A real git checkout with one commit. Returns the commit's SHA."""
    directory.mkdir(parents=True, exist_ok=True)
    run = lambda *argv: subprocess.run(                       # noqa: E731
        ["git", "-C", str(directory), *argv], capture_output=True, text=True)
    run("init", "-q")
    run("config", "user.email", "test@example.invalid")
    run("config", "user.name", "test")
    (directory / filename).write_text(body)
    run("add", filename)
    run("commit", "-q", "-m", "one")
    return run("rev-parse", "HEAD").stdout.strip()


class CountingTransport:
    """A model transport that is never reached unless the engine reaches it.

    It answers, so a run that calls it looks exactly like a run that should
    have: the assertion is the COUNT, and a transport that raised would turn
    "the gate held" into "the step failed".
    """

    def __init__(self):
        self.calls = []

    def __call__(self, transport, ask, ctx):
        self.calls.append({"transport": transport, "ask": ask})
        return {"candidates": [], "by": transport}


class RecipeCase(unittest.TestCase):
    """Two isolated roots: where records go, and what a scope reads."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.state_root = base / "state" / "recipes"
        self.records_root = base / "records"
        self.state_root.mkdir(parents=True)
        self.records_root.mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def sources(self, project: str, entries: list) -> Path:
        path = (self.records_root / "projects" / project / "context"
                / "sources.yaml")
        path.parent.mkdir(parents=True, exist_ok=True)
        import yaml
        path.write_text(yaml.safe_dump({"sources": entries}), encoding="utf-8")
        return path

    def a_run(self, recipe_id, scope, *, budget=0, period=None, transport=None,
              model=None, retake=False, **kwargs):
        engine = recipes.Engine(
            state_root=self.state_root, scope=scope, period=period or PERIOD,
            model=model, transport=transport, records_root=self.records_root,
            now="2026-09-20T12:00:00+09:00", **kwargs)
        return engine.run(recipe_id=recipe_id, budget=budget, retake=retake)

    def a_checkout_with_work(self, name="worked"):
        """A checkout with one commit, made once per test."""
        checkout = Path(self._tmp.name) / name
        if not (checkout / ".git").exists():
            a_checkout(checkout)
        return checkout

    def a_window_containing_now(self):
        return {"start": "2000-01-01T00:00:00+00:00",
                "end": "2099-01-01T00:00:00+00:00",
                "id": "20000101T0000+0000-209901010T0000+0000"}

    def a_period(self, hour: int, day: int = 20):
        """Another period of the same scope, so another key.

        Tests that need a second pass of a loop must change this: the key is
        `recipe + scope + period`, so a second pass of the SAME period is
        ALREADY_DONE by design and would measure the single-flight gate rather
        than the thing the test is about.
        """
        return {"start": f"2026-09-{day:02d}T{hour:02d}:00:00+09:00",
                "end": f"2026-09-{day:02d}T{hour:02d}:59:00+09:00",
                "id": f"202609{day:02d}T{hour:02d}00+0900-202609{day:02d}T{hour:02d}59+0900"}

    def an_engine(self, scope, **kwargs):
        kwargs.setdefault("records_root", self.records_root)
        kwargs.setdefault("now", "2026-09-20T12:00:00+09:00")
        return recipes.Engine(state_root=self.state_root, scope=scope,
                              period=PERIOD, **kwargs)

    def record(self, recipe_id, scope, period=None):
        return recipes.record_path(self.state_root, recipe_id=recipe_id,
                                   scope_ref=scope,
                                   period_id=(period or PERIOD)["id"])


# ---------------------------------------------------------------------------
# The contract, and the closed vocabulary
# ---------------------------------------------------------------------------

class TheContractTest(unittest.TestCase):
    def test_it_declares_the_three_loops_of_the_criterion(self):
        self.assertEqual([entry["id"] for entry in recipes.recipes()],
                         ["perception", "work", "report"])

    def test_every_stop_a_recipe_declares_is_in_the_vocabulary(self):
        vocabulary = recipes.stop_reasons()
        for entry in recipes.recipes():
            for reason in entry["stops"]:
                self.assertIn(reason, vocabulary, entry["id"])
        self.assertTrue(vocabulary)

    def test_every_declared_stop_says_what_it_means_and_whether_it_is_normal(self):
        for reason, entry in recipes.stop_reasons().items():
            self.assertIn(entry.get("normal"), (True, False), reason)
            self.assertGreater(len(entry.get("means", "").strip()), 20, reason)

    def test_every_word_the_engine_can_emit_is_in_the_contract(self):
        """The module's own literals, against the contract's closed set.

        The engine produces a word and `exit_code_for` refuses one the contract
        does not have, but that only catches a word that reaches it at runtime.
        This catches one written into a branch nothing has exercised yet -- and
        the failure it prevents is a cron log that renders a stop nobody can
        look up.
        """
        source = (ROOT / "runtime" / "recipes.py").read_text(encoding="utf-8")
        words = set(re.findall(r'"([A-Z][A-Z_]{2,})"', source))
        allowed = set(recipes.stop_reasons()) | NON_STOP_WORDS
        self.assertEqual(sorted(words - allowed), [])

    def test_exit_code_for_refuses_a_word_the_contract_does_not_have(self):
        with self.assertRaises(recipes.Refused):
            recipes.exit_code_for("LOOKS_FINE_TO_ME")

    def test_a_normal_stop_exits_zero_and_the_rest_exit_could_not_answer(self):
        self.assertEqual(recipes.exit_code_for("NO_CHANGE"), recipes.EXIT_OK)
        self.assertEqual(recipes.exit_code_for("ALREADY_DONE"), recipes.EXIT_OK)
        for reason in ("MODEL_UNBOUND", "SOURCES_UNPRODUCED",
                       "IN_FLIGHT_OR_ABANDONED", "DELIVERY_UNRECONCILED"):
            self.assertEqual(recipes.exit_code_for(reason),
                             recipes.EXIT_COULD_NOT_ANSWER, reason)

    def test_the_report_loop_has_no_model_step_and_so_needs_no_budget(self):
        self.assertEqual(recipes.model_steps("report"), ())
        self.assertEqual(recipes.model_steps("perception"), ("refresh",))
        self.assertEqual(recipes.model_steps("work"), ("advance",))

    def test_the_model_registry_is_empty_and_the_engine_does_not_widen_it(self):
        self.assertEqual(recipes.model_transports(), ())


# ---------------------------------------------------------------------------
# Periods and scopes: what a run is about
# ---------------------------------------------------------------------------

class PeriodAndScopeTest(unittest.TestCase):
    def test_a_period_without_an_offset_is_refused(self):
        with self.assertRaises(recipes.Refused) as caught:
            recipes.parse_period(start="2026-09-20T00:00:00",
                                 end="2026-09-20T23:59:00")
        self.assertIn("offset", str(caught.exception))

    def test_a_period_id_carries_the_offset_it_was_given(self):
        period = recipes.parse_period(start="2026-09-20T00:00+09:00",
                                      end="2026-09-20T23:59+09:00")
        self.assertEqual(period["id"],
                         "20260920T0000+0900-20260920T2359+0900")

    def test_a_period_that_starts_after_it_ends_is_refused(self):
        with self.assertRaises(recipes.Refused):
            recipes.parse_period(start="2026-09-20T23:00+09:00",
                                 end="2026-09-20T00:00+09:00")

    def test_an_unknown_scope_kind_is_refused(self):
        with self.assertRaises(recipes.Refused):
            recipes.parse_scope("fleet:everything")

    def test_a_scope_that_names_no_project_is_refused(self):
        with self.assertRaises(recipes.Refused):
            recipes.parse_scope("project:")

    def test_the_scope_is_part_of_the_key_so_two_scopes_do_not_share_a_record(self):
        one = recipes.record_path("/s", recipe_id="work", scope_ref="project:a",
                                  period_id=PERIOD["id"])
        two = recipes.record_path("/s", recipe_id="work", scope_ref="project:b",
                                  period_id=PERIOD["id"])
        self.assertNotEqual(one, two)

    def test_a_schedule_this_instance_has_not_bound_is_a_gap_not_a_window(self):
        import argparse
        args = argparse.Namespace(period_start=None, period_end=None,
                                  schedule="daily")
        with self.assertRaises(recipes.Unbound) as caught:
            recipes.period_from_args(args, self.state_root())
        self.assertEqual(caught.exception.reason, "NO_SCHEDULE_BOUND")
        self.assertEqual(recipes.exit_code_for(caught.exception.reason),
                         recipes.EXIT_COULD_NOT_ANSWER)

    def state_root(self):
        import tempfile
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        return Path(holder.name)

    def test_a_schedule_that_binds_half_of_what_seven_requires_is_refused(self):
        import tempfile
        import yaml
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        root = Path(holder.name)
        (root / "schedules.yaml").write_text(yaml.safe_dump(
            {"schedules": {"daily": {"timezone": "Asia/Tokyo", "budget": 0}}}),
            encoding="utf-8")
        with self.assertRaises(recipes.Unbound) as caught:
            recipes.bound_schedule(root, "daily")
        self.assertEqual(caught.exception.reason, "NO_SCHEDULE_BOUND")
        self.assertIn("scope", str(caught.exception))


# ---------------------------------------------------------------------------
# A schedule is a binding: the window comes from the person's timezone
# ---------------------------------------------------------------------------
#
# The failure this set exists to catch is not "the schedule is missing". It is
# the subtler one: a schedule that IS bound, and whose window is nevertheless
# read off the host's clock. On a shared machine the host's timezone is a fact
# about the machine, so that window belongs to the node's operator and not to
# the person the report is for -- and the period id renders it with an offset,
# so the two are indistinguishable to a reader.

class TheScheduleTest(unittest.TestCase):
    SCHEDULE = {"timezone": "Asia/Tokyo", "window": "08:00-09:00",
                "scope": "project:example", "budget": 0,
                "recipients": ["someone"]}

    def state_root(self, schedule=None):
        import tempfile
        import yaml
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        root = Path(holder.name)
        if schedule is not None:
            (root / "schedules.yaml").write_text(
                yaml.safe_dump({"schedules": {"daily-report": schedule}}),
                encoding="utf-8")
        return root

    def zone(self, name):
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)

    def test_a_bound_schedule_hands_back_a_window_and_not_the_text_of_one(self):
        """`window` is parsed on the way out, and the parsed pair is the one a
        caller gets.

        The string and the pair share a key, so a spread in the wrong order
        leaves `"08:00-09:00"` where `_scheduled_window` unpacks two times --
        and the failure surfaces three functions away from the spread."""
        entry = recipes.bound_schedule(self.state_root(self.SCHEDULE),
                                       "daily-report")
        self.assertNotIsInstance(entry["window"], str)
        start, end = entry["window"]
        self.assertEqual((start.hour, start.minute), (8, 0))
        self.assertEqual((end.hour, end.minute), (9, 0))

    def test_the_window_is_the_schedule_zone_and_not_the_process_zone(self):
        """One instant, read from two zones: same window either way.

        `when` is passed as the SAME MOMENT seen from two different offsets.
        08:17 in Tokyo and 23:17 the previous day in UTC are one instant, and a
        window computed by asking the process what time it is would give two."""
        from datetime import datetime
        entry = recipes.bound_schedule(self.state_root(self.SCHEDULE),
                                       "daily-report")
        jst = recipes._scheduled_window(
            entry, when=datetime(2026, 9, 20, 8, 17, tzinfo=self.zone("Asia/Tokyo")))
        utc = recipes._scheduled_window(
            entry, when=datetime(2026, 9, 19, 23, 17, tzinfo=self.zone("UTC")))
        self.assertEqual(jst, utc)
        self.assertEqual(jst, ("2026-09-20T08:00:00+09:00",
                               "2026-09-20T09:00:00+09:00"))

    def test_a_period_from_a_schedule_carries_that_schedules_offset(self):
        import argparse
        from datetime import datetime
        root = self.state_root(self.SCHEDULE)
        args = argparse.Namespace(period_start=None, period_end=None,
                                  schedule="daily-report")
        period = recipes.period_from_args(
            args, root, now=datetime(2026, 9, 20, 8, 17, tzinfo=self.zone("Asia/Tokyo")))
        self.assertEqual(period["id"], "20260920T0800+0900-20260920T0900+0900")
        self.assertEqual(period["source"], "schedule:daily-report")

    def test_a_schedule_whose_timezone_this_node_cannot_resolve_is_a_gap(self):
        """A timezone name the zone database does not have is not a window.

        Falling back to the host's zone here would produce a period that looks
        exactly like a bound one -- which is the whole failure this file is
        about, arriving by a different door."""
        entry = recipes.bound_schedule(
            self.state_root({**self.SCHEDULE, "timezone": "Mars/Olympus"}),
            "daily-report")
        with self.assertRaises(recipes.Unbound) as caught:
            recipes._scheduled_window(entry)
        self.assertEqual(caught.exception.reason, "NO_SCHEDULE_BOUND")
        self.assertIn("Mars/Olympus", str(caught.exception))

    def test_a_scheduled_run_is_not_asked_for_its_ceiling_twice(self):
        """`budget` is one of the fields a schedule binds, so the scheduled
        path reads it there.

        The failure this catches is a required field that nobody reads: §7
        lists the ceiling among what a schedule binds, and a `--schedule` run
        that also demanded `--budget` would make the schedule's own field
        decorative."""
        import argparse
        root = self.state_root({**self.SCHEDULE, "budget": 3})
        args = argparse.Namespace(budget=None, schedule="daily-report")
        self.assertEqual(recipes.budget_from_args(args, root),
                         (3, "schedule:daily-report"))

    def test_an_explicit_budget_wins_and_says_that_it_did(self):
        import argparse
        root = self.state_root({**self.SCHEDULE, "budget": 3})
        args = argparse.Namespace(budget=0, schedule="daily-report")
        self.assertEqual(recipes.budget_from_args(args, root), (0, "--budget"))

    def test_neither_a_budget_nor_a_schedule_is_refused_rather_than_defaulted(self):
        import argparse
        args = argparse.Namespace(budget=None, schedule=None)
        with self.assertRaises(recipes.Refused) as caught:
            recipes.budget_from_args(args, self.state_root(self.SCHEDULE))
        self.assertIn("never defaulted", str(caught.exception))

    def test_a_budget_of_zero_is_a_binding_and_not_an_absence(self):
        """The `None`/`0` confusion, at the one field where it is easiest.

        `budget: 0` is a declared ceiling of nothing -- an ordinary scheduled
        perception pass -- and `if not entry.get("budget")` reads it as
        "no ceiling given" and refuses the schedule. Each of the three
        absences below is a different absence and none of them binds."""
        self.assertTrue(recipes._binds({"budget": 0}, "budget"))
        self.assertTrue(recipes._binds({"budget": 7}, "budget"))
        for absent in ({}, {"budget": None}, {"budget": True}, {"budget": -1}):
            with self.subTest(absent=absent):
                self.assertFalse(recipes._binds(absent, "budget"))

    def test_an_empty_recipient_list_is_not_a_bound_schedule(self):
        """A list that binds is a list with somebody in it. §7 requires the
        recipients because a report nobody is sent is not a delivery."""
        self.assertTrue(recipes._binds({"recipients": ["a"]}, "recipients"))
        self.assertFalse(recipes._binds({"recipients": []}, "recipients"))
        self.assertFalse(recipes._binds({"recipients": "  "}, "recipients"))


# ---------------------------------------------------------------------------
# The key: two runs of one key are one run
# ---------------------------------------------------------------------------

class TheKeyTest(RecipeCase):
    def test_the_first_run_writes_a_claim_before_its_first_step(self):
        """The two writes, observed rather than asserted in a comment.

        The claim is what the next run of the key finds. This reads the file
        the loop left -- a DONE record -- and then the claim a crashed run
        leaves, which is the next test.
        """
        result = self.a_run("report", "project:one",
                            out_dir=self.state_root / "out",
                            worktree=self.a_checkout_with_work())
        self.assertEqual(result["state"], recipes.DONE)
        self.assertEqual(result["stopped_because"], "COMPLETED")
        self.assertEqual(result["exit_code"], recipes.EXIT_OK)
        self.assertEqual(result["claimed_by"], f"pid:{__import__('os').getpid()}")
        on_disk = json.loads(Path(result["path"]).read_text())
        self.assertEqual(on_disk["state"], recipes.DONE)

    def test_the_second_run_of_a_key_does_nothing_a_second_time(self):
        out = self.state_root / "out"
        worktree = self.a_checkout_with_work()
        first = self.a_run("report", "project:one", out_dir=out,
                           worktree=worktree)
        second = self.a_run("report", "project:one", out_dir=out,
                            worktree=worktree)
        self.assertEqual(second["stopped_because"], "ALREADY_DONE")
        self.assertEqual(second["model_calls"], 0)
        self.assertEqual(second["exit_code"], recipes.EXIT_OK)
        self.assertEqual(second["claimed_at"], first["claimed_at"])
        self.assertEqual(second["finished_at"], first["finished_at"])

    def test_a_run_that_took_no_step_does_not_claim_a_ceiling_of_zero(self):
        """`None` and `0` again, on the results that describe a non-run.

        The second run above spends nothing, and `given: 0` would say a ceiling
        of zero was agreed to for it. Nobody agreed to one: it never had a
        ceiling of its own, because it never reached a step that a ceiling
        governs. A reader totalling ceilings across a week's runs has to be
        able to tell those apart."""
        out = self.state_root / "out"
        worktree = self.a_checkout_with_work()
        self.a_run("report", "project:one", out_dir=out, worktree=worktree)
        second = self.a_run("report", "project:one", out_dir=out, worktree=worktree)
        self.assertEqual(second["stopped_because"], "ALREADY_DONE")
        self.assertIsNone(second["budget"]["given"])
        self.assertEqual(second["budget"]["spent"], 0)
        self.assertEqual(second["budget"]["from"], "no-run:ALREADY_DONE")

    def test_a_claim_with_no_outcome_is_not_a_success_and_not_a_no_op(self):
        path = self.record("report", "project:one")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "schema": recipes.SCHEMA_RECORD, "recipe": "report",
            "scope": "project:one", "period": PERIOD, "state": recipes.CLAIMED,
            "claimed_at": "2026-09-20T07:00:00+09:00", "claimed_by": "pid:1",
            "finished_at": None}), encoding="utf-8")
        result = self.a_run("report", "project:one",
                            out_dir=self.state_root / "out",
                            worktree=self.a_checkout_with_work())
        self.assertEqual(result["stopped_because"], "IN_FLIGHT_OR_ABANDONED")
        self.assertEqual(result["exit_code"], recipes.EXIT_COULD_NOT_ANSWER)
        self.assertEqual(result["model_calls"], 0)

    def test_a_retake_keeps_the_earlier_claim(self):
        path = self.record("report", "project:one")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "schema": recipes.SCHEMA_RECORD, "recipe": "report",
            "scope": "project:one", "period": PERIOD, "state": recipes.CLAIMED,
            "claimed_at": "2026-09-20T07:00:00+09:00", "claimed_by": "pid:1",
            "finished_at": None}), encoding="utf-8")
        result = self.a_run("report", "project:one", retake=True,
                            out_dir=self.state_root / "out",
                            worktree=self.a_checkout_with_work())
        self.assertEqual(result["state"], recipes.DONE)
        self.assertEqual(result["previous_state"], recipes.CLAIMED)
        self.assertEqual(result["retook_at"], "2026-09-20T12:00:00+09:00")
        self.assertEqual(result["previous_claims"], [{
            "claimed_at": "2026-09-20T07:00:00+09:00",
            "claimed_by": "pid:1",
            "retaken_at": "2026-09-20T12:00:00+09:00"}])

    def test_a_record_that_does_not_parse_is_not_read_as_never_run(self):
        path = self.record("report", "project:one")
        path.parent.mkdir(parents=True)
        path.write_text("{ this is not json", encoding="utf-8")
        result = self.a_run("report", "project:one",
                            out_dir=self.state_root / "out",
                            worktree=self.a_checkout_with_work())
        self.assertEqual(result["stopped_because"], "SOURCE_UNAVAILABLE")
        self.assertEqual(path.read_text(encoding="utf-8"), "{ this is not json")

    def test_a_budget_that_was_not_given_is_refused_rather_than_defaulted(self):
        with self.assertRaises(recipes.Refused):
            self.a_run("report", "project:one", budget=None)

    def test_status_says_could_not_answer_when_nothing_has_run(self):
        document = recipes.status(state_root=self.state_root,
                                  scope_ref="project:one", period=PERIOD)
        self.assertEqual(document["summary"][recipes.NEVER_RUN], 3)
        self.assertEqual(document["exit_code"], recipes.EXIT_COULD_NOT_ANSWER)

    def test_status_stops_saying_could_not_answer_once_every_loop_has_run(self):
        worktree = self.a_checkout_with_work()
        for recipe_id in ("perception", "work", "report"):
            self.a_run(recipe_id, "project:one", budget=0, worktree=worktree,
                       out_dir=self.state_root / "out")
        document = recipes.status(state_root=self.state_root,
                                  scope_ref="project:one", period=PERIOD)
        # Two of the three stopped with SOURCES_UNPRODUCED / QUEUE_UNPRODUCED,
        # which are STOPPED rather than NEVER_RUN. The summary is what says so.
        self.assertEqual(document["summary"][recipes.NEVER_RUN], 0)
        self.assertEqual(document["summary"][recipes.CLAIMED], 0)


# ---------------------------------------------------------------------------
# The increment gate: no change never reaches the model boundary
# ---------------------------------------------------------------------------

class TheDeltaGateTest(RecipeCase):
    def test_no_change_does_not_reach_the_model_step(self):
        checkout = Path(self._tmp.name) / "followed"
        revision = a_checkout(checkout)
        self.sources("one", [{"id": "followed", "kind": "git",
                              "path": str(checkout)}])
        # The first pass needs a transport of its own: with none bound it stops
        # MODEL_UNBOUND before it can advance the cursor, and then the second
        # pass would be measuring a first pass and not a delta.
        first = self.a_run("perception", "project:one", budget=5,
                           period=self.a_period(10), transport="counting",
                           model=CountingTransport())
        self.assertEqual(first["state"], recipes.DONE)
        self.assertEqual(first["cursor"]["followed"], revision)

        transport = CountingTransport()
        second = self.a_run("perception", "project:one", budget=5,
                            period=self.a_period(11), transport="counting",
                            model=transport)
        self.assertEqual(second["stopped_because"], "NO_CHANGE")
        # and the cursor survives the quiet period unchanged: NO_CHANGE is the
        # gate holding, not the loop forgetting where it was.
        self.assertEqual(second["cursor"], first["cursor"])
        self.assertEqual(second["exit_code"], recipes.EXIT_OK)
        self.assertEqual(second["model_calls"], 0)
        self.assertEqual(transport.calls, [])

    def test_a_source_that_moved_reaches_the_model_step_and_is_counted(self):
        checkout = Path(self._tmp.name) / "followed"
        a_checkout(checkout)
        self.sources("one", [{"id": "followed", "kind": "git",
                              "path": str(checkout)}])
        self.a_run("perception", "project:one", budget=5,
                   period=self.a_period(10), transport="counting",
                   model=CountingTransport())
        a_checkout(checkout, filename="second.txt", body="two\n")
        transport = CountingTransport()
        result = self.a_run("perception", "project:one", budget=5,
                            period=self.a_period(11), transport="counting",
                            model=transport)
        self.assertEqual(result["state"], recipes.DONE)
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0]["ask"]["reason"],
                         "say what a source delta means for this scope's understanding")
        self.assertEqual(list(transport.calls[0]["ask"]["moved"]), ["followed"])
        # And what the step is NOT asked for. A reading asked to say whether the
        # change "is a candidate for a new Item" is the shortcut §P3 names --
        # 「source变了」becoming 「新Item候选」 with nobody deciding it. The
        # instruction has to refuse that in as many words, because the model
        # reads this string and nothing else.
        self.assertIn("Do not propose Items", transport.calls[0]["ask"]["instruction"])

    def test_a_source_that_did_not_answer_leaves_the_cursor_where_it_was(self):
        checkout = Path(self._tmp.name) / "followed"
        revision = a_checkout(checkout)
        self.sources("one", [{"id": "followed", "kind": "git",
                              "path": str(checkout)}])
        self.a_run("perception", "project:one", budget=5,
                   period=self.a_period(10), transport="counting",
                   model=CountingTransport())
        # A source pointed at somewhere that is not a checkout: it answers
        # nothing, and the run must not write that off as "unchanged".
        self.sources("one", [{"id": "followed", "kind": "git",
                              "path": str(Path(self._tmp.name) / "nowhere")}])
        result = self.a_run("perception", "project:one", budget=5,
                            period=self.a_period(11))
        self.assertEqual(result["stopped_because"], "SOURCE_UNAVAILABLE")
        # A normal stop, and it exits 0: §7 lists 源暂不可用 as one of this
        # loop's ordinary reasons for stopping, and a scheduler that read it as
        # a failure would retry a source that is simply down. What matters is
        # that the record SAYS it, and that the cursor did not move.
        self.assertEqual(result["exit_code"], recipes.EXIT_OK)
        self.assertEqual(len(result["outcomes"]["unanswered"]), 1)
        self.assertEqual(result["outcomes"]["unanswered"][0]["id"], "followed")
        # The stop says WHICH of the two situations this is. "Nothing at all was
        # read" and "the sources I did read are unchanged" are different
        # findings, and the second one is wrong here: there was no source this
        # pass could compare at all.
        self.assertIn("none of 1 source(s) answered", result["stopped_why"])
        # and the next run against the real checkout still compares against the
        # revision it last read, rather than against the failure.
        self.sources("one", [{"id": "followed", "kind": "git",
                              "path": str(checkout)}])
        again = self.a_run("perception", "project:one", budget=5,
                           period=self.a_period(12))
        self.assertEqual(again["stopped_because"], "NO_CHANGE")
        self.assertEqual(again["cursor"]["followed"], revision)

    def a_pass_with_one_source_down(self, period_hour: int, *, blind_path: Path):
        """Two sources, one of them moved, and the other somewhere unreadable."""
        moved = Path(self._tmp.name) / "moved"
        a_checkout(moved)
        blind = Path(self._tmp.name) / "blind"
        blind_revision = a_checkout(blind)
        self.sources("one", [{"id": "moved", "kind": "git", "path": str(moved)},
                             {"id": "blind", "kind": "git", "path": str(blind)}])
        self.a_run("perception", "project:one", budget=5,
                   period=self.a_period(10), transport="counting",
                   model=CountingTransport())
        moved_revision = a_checkout(moved, filename="second.txt", body="two\n")
        self.sources("one", [{"id": "moved", "kind": "git", "path": str(moved)},
                             {"id": "blind", "kind": "git",
                              "path": str(blind_path)}])
        transport = CountingTransport()
        result = self.a_run("perception", "project:one", budget=5,
                            period=self.a_period(period_hour),
                            transport="counting", model=transport)
        return moved, blind, moved_revision, blind_revision, transport, result

    def test_one_source_that_did_not_answer_does_not_block_the_others(self):
        """The coupling §P3 names, as a scenario rather than as a comment.

        Before this, an unreachable source refused the whole pass: the one
        checkout that had moved was not read, no model was reached, and its
        delta stayed pending behind a failure that had nothing to do with it.
        """
        moved, blind, moved_revision, blind_revision, transport, result = (
            self.a_pass_with_one_source_down(
                11, blind_path=Path(self._tmp.name) / "nowhere"))
        # The source that answered is read -- all the way to the model boundary.
        self.assertEqual(result["state"], recipes.DONE)
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(list(transport.calls[0]["ask"]["moved"]), ["moved"])
        # And the record still says, by name and by its own reason, what nobody
        # read. A pass that finished normally over a list it only partly read is
        # the quiet version of the same write-off.
        self.assertEqual([row["id"] for row in result["outcomes"]["unread"]],
                         ["blind"])
        self.assertIn("blind", result["outcomes"]["unread"][0]["why"])
        # Per source, in the cursor: what was read is written down at the
        # revision it was read at, and the source that did not answer keeps the
        # revision we last actually read -- not a guess at where it might be.
        self.assertEqual(result["cursor"]["moved"], moved_revision)
        self.assertEqual(result["cursor"]["blind"], blind_revision)
        # The capture is a step of its own, and it carries both halves.
        steps = {step["step"]: step for step in result["steps"]}
        self.assertEqual(steps["observe"]["detail"]["captured"], ["moved"])
        self.assertEqual(steps["observe"]["detail"]["unanswered"], ["blind"])

    def test_the_source_nobody_read_is_still_compared_against_what_was_last_read(self):
        """The half of the rule that makes the other half safe.

        Keeping the unread source's revision in the cursor is only correct if a
        later pass still sees its next change. This is that pass.
        """
        moved, blind, _, blind_revision, _, _ = self.a_pass_with_one_source_down(
            11, blind_path=Path(self._tmp.name) / "nowhere")
        late_revision = a_checkout(blind, filename="late.txt", body="late\n")
        # The source is reachable again, and has moved while it was not.
        self.sources("one", [{"id": "moved", "kind": "git", "path": str(moved)},
                             {"id": "blind", "kind": "git", "path": str(blind)}])
        transport = CountingTransport()
        later = self.a_run("perception", "project:one", budget=5,
                           period=self.a_period(12), transport="counting",
                           model=transport)
        self.assertEqual(list(transport.calls[0]["ask"]["moved"]), ["blind"])
        was = transport.calls[0]["ask"]["moved"]["blind"]["was"]
        self.assertEqual(was, blind_revision)
        self.assertEqual(later["cursor"]["blind"], late_revision)

    def test_nothing_new_plus_a_source_that_did_not_answer_is_not_a_quiet_period(self):
        """NO_CHANGE is a statement about every source, so it needs every source."""
        answered = Path(self._tmp.name) / "answered"
        a_checkout(answered)
        blind = Path(self._tmp.name) / "blind"
        a_checkout(blind)
        self.sources("one", [{"id": "answered", "kind": "git",
                              "path": str(answered)},
                             {"id": "blind", "kind": "git", "path": str(blind)}])
        self.a_run("perception", "project:one", budget=5,
                   period=self.a_period(10), transport="counting",
                   model=CountingTransport())
        self.sources("one", [{"id": "answered", "kind": "git",
                              "path": str(answered)},
                             {"id": "blind", "kind": "git",
                              "path": str(Path(self._tmp.name) / "nowhere")}])
        result = self.a_run("perception", "project:one", budget=5,
                            period=self.a_period(11))
        self.assertEqual(result["stopped_because"], "SOURCE_UNAVAILABLE")
        self.assertEqual(result["exit_code"], recipes.EXIT_OK)
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual([row["id"] for row in result["outcomes"]["unanswered"]],
                         ["blind"])

    def test_a_source_taken_off_the_followed_list_does_not_keep_a_cursor_entry(self):
        """Merging is what the unread source needs, and it has to stop somewhere.

        A cursor entry is a claim about a source this scope follows. Once a
        person narrows the list, the entry for the source they dropped is a
        claim about nothing.
        """
        kept = Path(self._tmp.name) / "kept"
        a_checkout(kept)
        dropped = Path(self._tmp.name) / "dropped"
        a_checkout(dropped)
        self.sources("one", [{"id": "kept", "kind": "git", "path": str(kept)},
                             {"id": "dropped", "kind": "git",
                              "path": str(dropped)}])
        self.a_run("perception", "project:one", budget=5,
                   period=self.a_period(10), transport="counting",
                   model=CountingTransport())
        a_checkout(kept, filename="second.txt", body="two\n")
        self.sources("one", [{"id": "kept", "kind": "git", "path": str(kept)}])
        result = self.a_run("perception", "project:one", budget=5,
                            period=self.a_period(11), transport="counting",
                            model=CountingTransport())
        self.assertEqual(result["state"], recipes.DONE)
        self.assertEqual(list(result["cursor"]), ["kept"])

    def test_no_authored_source_list_is_a_gap_not_a_quiet_period(self):
        result = self.a_run("perception", "project:one", budget=5)
        self.assertEqual(result["stopped_because"], "SOURCES_UNPRODUCED")
        self.assertEqual(result["exit_code"], recipes.EXIT_COULD_NOT_ANSWER)
        self.assertEqual(result["model_calls"], 0)


# ---------------------------------------------------------------------------
# The model boundary: budget first, then transport
# ---------------------------------------------------------------------------

class TheModelBoundaryTest(RecipeCase):
    def a_run_that_wants_a_model(self, **kwargs):
        checkout = Path(self._tmp.name) / "followed"
        a_checkout(checkout)
        self.sources("one", [{"id": "followed", "kind": "git",
                              "path": str(checkout)}])
        return self.a_run("perception", "project:one", **kwargs)

    def test_the_budget_refusal_comes_before_the_transport_refusal(self):
        """Both problems at once, and only the cheaper fact may be reported.

        A run whose ceiling is already spent does not need to know whether a
        transport is bound. Reporting MODEL_UNBOUND here would hide a number
        the CALLER set behind a binding nobody did.
        """
        result = self.a_run_that_wants_a_model(budget=0, transport=None)
        self.assertEqual(result["stopped_because"], "BUDGET_EXHAUSTED")
        self.assertEqual(result["exit_code"], recipes.EXIT_OK)
        self.assertEqual(result["would_call"]["why"], "budget exhausted")

    def test_an_unbound_transport_records_what_it_would_have_asked(self):
        result = self.a_run_that_wants_a_model(budget=1, transport=None)
        self.assertEqual(result["stopped_because"], "MODEL_UNBOUND")
        self.assertEqual(result["exit_code"], recipes.EXIT_COULD_NOT_ANSWER)
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["would_call"]["step"], "refresh")
        self.assertIn("moved", result["would_call"]["ask"])
        self.assertIn("no model transport is bound",
                      result["would_call"]["why"])

    def test_a_bound_transport_is_called_exactly_once_per_model_step(self):
        transport = CountingTransport()
        result = self.a_run_that_wants_a_model(budget=1, transport="counting",
                                               model=transport)
        self.assertEqual(result["state"], recipes.DONE)
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(result["budget"]["spent"], 1)
        # And the record says which half of §7's two ways in this ceiling came
        # from. A number without its provenance is the one thing this record
        # cannot be audited against later.
        self.assertEqual(result["budget"]["given"], 1)
        self.assertEqual(result["budget"]["from"], "--budget")
        self.assertEqual(len(transport.calls), 1)

    def test_a_ceiling_of_zero_is_a_complete_run_of_every_other_step(self):
        """`--budget 0` is the ordinary shape of a scheduled pass, not a no-op.

        The steps that need no model still run and are still recorded; what a
        zero ceiling buys is that the run cannot reach the model boundary.
        """
        checkout = Path(self._tmp.name) / "followed"
        a_checkout(checkout)
        self.sources("one", [{"id": "followed", "kind": "git",
                              "path": str(checkout)}])
        result = self.a_run("perception", "project:one", budget=0,
                          transport="counting", model=CountingTransport())
        self.assertEqual([step["step"] for step in result["steps"]],
                         ["sources", "observe", "delta"])
        self.assertEqual(result["stopped_because"], "BUDGET_EXHAUSTED")

    def test_the_second_model_step_is_refused_by_a_ceiling_of_one(self):
        engine = recipes.Engine(state_root=self.state_root, scope="node",
                                period=PERIOD, transport="counting",
                                model=CountingTransport(),
                                now="2026-09-20T12:00:00+09:00")
        running = {"recipe": "work", "budget": {"given": 1}, "model_calls": 0,
                   "steps": [], "notes": [], "cursor": {}, "would_call": None}
        ctx = recipes.Context(engine=engine, running=running)
        ctx.spend("first", ask={"reason": "one"})
        self.assertEqual(running["model_calls"], 1)
        with self.assertRaises(recipes.Stop) as caught:
            ctx.spend("second", ask={"reason": "two"})
        self.assertEqual(caught.exception.reason, "BUDGET_EXHAUSTED")
        self.assertEqual(running["would_call"]["step"], "second")

    def test_a_binding_outside_the_registry_is_refused_rather_than_used(self):
        import yaml
        (self.state_root / "model-binding.yaml").write_text(
            yaml.safe_dump({"transport": "anything-at-all"}), encoding="utf-8")
        with self.assertRaises(recipes.Refused):
            recipes.transport_for(self.state_root)

    def test_the_default_invoker_refuses_rather_than_sending_anything(self):
        with self.assertRaises(recipes.Unbound) as caught:
            recipes.default_model_invoker("counting", {}, None)
        self.assertEqual(caught.exception.reason, "MODEL_UNBOUND")


# ---------------------------------------------------------------------------
# Decisions: a pending one blocks its dependents and nothing else
# ---------------------------------------------------------------------------

class DecisionsTest(RecipeCase):
    def a_queue(self, project, cards):
        import yaml
        root = self.records_root / "projects" / project
        (root / "board").mkdir(parents=True, exist_ok=True)
        (root / "board" / "today.yaml").write_text(
            yaml.safe_dump({"date": "2026-09-20",
                            "items": [name for name, _ in cards]}),
            encoding="utf-8")
        for name, card in cards:
            directory = root / "items" / name
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "card.yaml").write_text(yaml.safe_dump(card),
                                                 encoding="utf-8")

    def test_declared_none_and_undeclared_are_different_answers(self):
        self.a_queue("one", [("A", {"state": "ACTIVE", "depends_on": []}),
                             ("B", {"state": "ACTIVE"})])
        ctx = recipes.Context(engine=self.an_engine("project:one"),
                              running=running_record())
        self.assertEqual(recipes._read_entry(ctx, "A")["depends_on"], [])
        self.assertIsNone(recipes._read_entry(ctx, "B")["depends_on"])

    def test_an_undeclared_edge_stops_the_run_only_when_a_decision_is_open(self):
        self.a_queue("one", [("A", {"state": "ACTIVE", "awaiting": "vince"}),
                             ("B", {"state": "ACTIVE"})])
        result = recipes.Engine(
            state_root=self.state_root, scope="project:one",
            period=self.a_window_containing_now(), records_root=self.records_root,
            worktree=self.a_checkout_with_work(),
            now="2026-09-20T12:00:00+09:00").run(recipe_id="work", budget=0)
        self.assertEqual(result["stopped_because"], "DECISION_SCOPE_UNKNOWN")
        self.assertEqual(result["exit_code"], recipes.EXIT_COULD_NOT_ANSWER)

    def test_a_pending_decision_blocks_its_dependents_and_not_the_others(self):
        self.a_queue("one", [("A", {"state": "ACTIVE", "awaiting": "vince",
                                    "depends_on": []}),
                             ("B", {"state": "ACTIVE", "depends_on": []}),
                             ("C", {"state": "ACTIVE", "depends_on": ["A"]})])
        result = recipes.Engine(
            state_root=self.state_root, scope="project:one",
            period=self.a_window_containing_now(), records_root=self.records_root,
            worktree=self.a_checkout_with_work(),
            work_engine=str(ROOT / "bin" / "serverosctl"),
            transport="counting", model=CountingTransport(),
            now="2026-09-20T12:00:00+09:00").run(recipe_id="work", budget=1)
        # B is ready and A's decision does not touch it; C is A's dependent and
        # is the only entry held back. So the run advances exactly one Item, and
        # WHICH one it advanced is the assertion: an implementation that blocked
        # everything on A would have had nothing to advance, and one that
        # ignored the edge would have advanced C as well.
        self.assertEqual(result["state"], recipes.DONE)
        self.assertEqual(result["model_calls"], 1)
        advanced = [s["did"] for s in result["steps"] if s["step"] == "advance"]
        self.assertEqual(advanced, [
            "called the bound model transport 'counting'", "advanced B"])
        classify = [s for s in result["steps"] if s["step"] == "classify"]
        self.assertEqual(classify[1]["detail"]["blocked"], ["C"])

    def test_a_closure_is_transitive_over_declared_edges_only(self):
        entries = [{"id": "A", "depends_on": []}, {"id": "B", "depends_on": ["A"]},
                   {"id": "C", "depends_on": ["B"]}, {"id": "D", "depends_on": []}]
        self.assertEqual(recipes._dependent_closure(entries, roots={"A"}),
                         {"B", "C"})

    def test_every_entry_parked_is_the_loop_stopping_not_failing(self):
        self.a_queue("one", [("A", {"state": "ACTIVE", "awaiting": "vince",
                                    "depends_on": []})])
        result = recipes.Engine(
            state_root=self.state_root, scope="project:one",
            period=self.a_window_containing_now(), records_root=self.records_root,
            now="2026-09-20T12:00:00+09:00").run(recipe_id="work", budget=0)
        self.assertEqual(result["stopped_because"], "NEEDS_DECISION")
        self.assertEqual(result["exit_code"], recipes.EXIT_OK)

    def test_no_selection_at_all_is_not_a_selection_with_nothing_in_it(self):
        result = recipes.Engine(
            state_root=self.state_root, scope="project:one", period=PERIOD,
            records_root=self.records_root,
            now="2026-09-20T12:00:00+09:00").run(recipe_id="work", budget=0)
        self.assertEqual(result["stopped_because"], "QUEUE_UNPRODUCED")
        self.assertEqual(result["exit_code"], recipes.EXIT_COULD_NOT_ANSWER)


# ---------------------------------------------------------------------------
# Report: a period that may already be in somebody's hands
# ---------------------------------------------------------------------------

class ReportTest(RecipeCase):
    def test_a_worktree_that_is_not_a_checkout_is_a_gap_not_zero_commits(self):
        """The report is still written, and it says 缺口 rather than 零.

        This is the one place the loop does NOT stop on a source that did not
        answer, and §7's own normal stop for this loop is why: 本期生成完. A
        daily report whose whole purpose is to show a quiet period is exactly
        the artifact that must exist for a period nobody could measure -- and
        what it must not do is render "nobody could read the worktree" as "no
        commits", which is the reading `measure_worktree` and `progress_verdict`
        already exist to keep apart.
        """
        out = self.state_root / "out"
        result = self.a_run("report", "project:one", out_dir=out,
                            worktree=Path(self._tmp.name) / "not-a-checkout")
        self.assertEqual(result["state"], recipes.DONE)
        measure = [s for s in result["steps"] if s["step"] == "measure"][0]
        self.assertIn("缺口", measure["detail"]["because"])
        # The document itself has to carry the reading, not just the record:
        # the report is the artifact a person reads.
        rendered = (out / "daily-report.md").read_text(encoding="utf-8")
        self.assertIn("UNKNOWN", rendered)
        self.assertIn("缺口", rendered)
        # The gap is NAMED as a gap and not as a quiet period: the verdict
        # sentence says 这不是「没有进展」, which is the whole distinction this
        # loop is not allowed to flatten.
        self.assertIn("这不是「没有进展」", measure["detail"]["because"])

    def test_the_loop_refuses_to_write_a_report_nobody_gave_it_a_place_for(self):
        """The backstop inside the loop.

        The CLI checks --out-dir first and writes no record at all (see
        `test_a_report_with_no_out_dir_writes_no_record`), so this branch is
        only reachable by a caller that skipped the CLI. It is still recorded
        rather than raised out of the run: a record that vanished because a
        step refused would be indistinguishable from one nobody wrote.
        """
        result = self.a_run("report", "project:one")
        self.assertEqual(result["stopped_because"], "FAULT")
        self.assertIn("--out-dir", result["stopped_why"])
        self.assertEqual(result["exit_code"], recipes.EXIT_COULD_NOT_ANSWER)

    def test_a_receipt_that_does_not_say_delivered_stops_a_second_publication(self):
        out = self.state_root / "out"
        worktree = self.a_checkout_with_work()
        window = self.a_window_containing_now()
        first = self.a_run("report", "project:one", period=window, out_dir=out,
                           worktree=worktree)
        self.assertEqual(first["state"], recipes.DONE)
        receipt = json.loads((out / "publication-receipt.json").read_text())
        self.assertFalse(receipt["publication"]["delivered"])

        # A DIFFERENT key for the SAME period and the same out-dir. The
        # single-flight gate cannot help here: what stops the second document
        # is the receipt, which is the fact about somebody's hands.
        second = self.a_run("report", "item:ONE", period=window, out_dir=out,
                            worktree=worktree)
        self.assertEqual(second["stopped_because"], "DELIVERY_UNRECONCILED")
        self.assertEqual(second["exit_code"], recipes.EXIT_COULD_NOT_ANSWER)
        self.assertIn("somebody", second["stopped_why"])

    def test_the_report_loop_records_the_verdict_of_the_daily_report(self):
        out = self.state_root / "out"
        result = recipes.Engine(
            state_root=self.state_root, scope="project:one",
            period=self.a_window_containing_now(), out_dir=out,
            worktree=self.a_checkout_with_work(),
            now="2026-09-20T12:00:00+09:00").run(recipe_id="report", budget=0)
        self.assertEqual(result["state"], recipes.DONE)
        measure = [s for s in result["steps"] if s["step"] == "measure"][0]
        self.assertIn(measure["detail"]["verdict"] if "verdict" in
                      measure["detail"] else measure["did"], (measure["did"],))
        self.assertTrue((out / "daily-report.md").is_file())
        self.assertEqual(result["model_calls"], 0)


# ---------------------------------------------------------------------------
# Where a record goes
# ---------------------------------------------------------------------------

class WhereRecordsGoTest(unittest.TestCase):
    def test_an_explicit_state_root_is_the_answer(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(recipes.state_root_for(directory), Path(directory))

    def test_the_namespace_this_module_writes_is_declared_by_the_root_contract(self):
        contract_path = ROOT / "fleet" / "state-root-contract.yaml"
        import yaml
        document = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        entry = document["namespaces"][recipes.NAMESPACE]
        self.assertEqual(entry["producer"], "runtime/recipes.py")
        self.assertEqual(entry["class"], "authority")
        self.assertEqual(entry["absent_means"], "NO_RECIPE_HAS_RUN")

    def test_the_namespace_is_one_the_path_resolver_will_resolve(self):
        """A namespace the resolver does not know is a directory, not state.

        `state_root_for` resolves through `paths.resolve("state/recipes")`, and
        that refuses a suffix outside `STATE_SERVEROS_TOP_LEVEL`. Asking the
        resolver rather than reading the tuple keeps this a measurement: the
        two halves have to agree, and each alone looks correct.
        """
        spec = importlib.util.spec_from_file_location(
            "serveros_paths_for_recipes", ROOT / "runtime" / "paths.py")
        paths_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(paths_mod)
        self.assertIn(recipes.NAMESPACE,
                      paths_mod.VOCABULARIES["state"])


if __name__ == "__main__":
    unittest.main()
