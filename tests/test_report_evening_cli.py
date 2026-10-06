#!/usr/bin/env python3
"""`serverosctl report evening`, driven through its own argument parser.

WHY THIS FILE EXISTS
--------------------
`tests/test_alignment_delivery.py` proves the pieces: the four sections, the
version statement, the run record that measures the gap. None of that proves the
COMMAND, and the command is where the three answers that must agree are computed
— which Beijing day, which run record, which directory — and where the order is
fixed (compose before request before deliver). A unit file has to call exactly
one thing, so "does the one thing that a unit calls do all of it" is a question
with no other place to be asked.

WHAT IS STUBBED, AND WHAT THAT COSTS
------------------------------------
`_report_sender` only, and only in the tests that name a recipient. It builds
`runtime/message_transport.py`'s sender, which is a real Feishu transport: left
real, a test that configured a recipient would either send a card to an invented
open_id or measure this node's lark-cli. The stub raises, which is one of the two
things the real one does here — every case in this file then exercises the
`PENDING_CONSUMER` half of the delivery discipline, and the half that reaches a
person is not exercised by any test, in this file or any other.

That is the honest shape of it: the delivery path is proven up to the moment a
transport is named, and the moment a transport is named needs a person.
"""
from __future__ import annotations

import contextlib
import datetime
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


#: The names `runtime/cli.py`'s `_load_module_from` resolves, so what is loaded
#: here is what the command calls rather than a second copy of it.
alignment = _load("serveros_intake_alignment_delivery",
                  ROOT / "intake" / "alignment_delivery.py")
delivery = _load("serveros_runtime_report_delivery",
                 ROOT / "runtime" / "report_delivery.py")
cli = _load("serveros_runtime_cli_for_evening", ROOT / "runtime" / "cli.py")

CARD = "information/understanding/people/zzj.md"
MANIFEST = "feishu/chats/people/zzj/oc_d/manifest.json"

#: 20:00 Beijing on the day the fixtures below belong to.
AT = "2026-10-06T12:00:00+00:00"
DAY = "2026-10-06"


class EveningCommandTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="report-evening-test-"))
        self.addCleanup(self._cleanup)
        self.run_dir = self.root / "daily-intake"
        self.record_dir = self.root / "perception"
        self.out_root = self.root / "evening-alignment"
        self._write_run()
        self._write_record()
        # `compose` refuses a preparation from another day, and a fixture written
        # a moment ago is not that day, so the stamp is stated.
        stamp = datetime.datetime.fromisoformat(AT).timestamp()
        os.utime(self.run_dir / "alignment.json", (stamp, stamp))
        self._set_recipient(None)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _set_recipient(self, value):
        was = os.environ.get("SERVEROS_REPORT_CONVERSATION")
        if value is None:
            os.environ.pop("SERVEROS_REPORT_CONVERSATION", None)
        else:
            os.environ["SERVEROS_REPORT_CONVERSATION"] = value
        self.addCleanup(self._restore_recipient, was)

    @staticmethod
    def _restore_recipient(was):
        if was is None:
            os.environ.pop("SERVEROS_REPORT_CONVERSATION", None)
        else:
            os.environ["SERVEROS_REPORT_CONVERSATION"] = was

    def _write_run(self):
        waiting = {"id": "c1", "kind": "OBSERVED_FACT", "subject": "额度异常",
                   "assertion": {"was": None, "now": "确实存在"},
                   "support": {"positions": [171], "excerpt": "原话"},
                   "applies_to": None, "touches": [], "supersedes": [],
                   "conflicts": [], "card": CARD, "views": [],
                   "confirmation": "awaiting_decision", "confirmation_why": "等人"}
        reading = self.run_dir / "readings" / "a"
        reading.mkdir(parents=True)
        (reading / "reading.json").write_text(json.dumps(
            {"source": {"card": CARD, "manifest": MANIFEST}}))
        (reading / "resolution.json").write_text(json.dumps({
            "schema": alignment.RESOLUTION_SCHEMA, "committed": [],
            "awaiting": [waiting], "retracted": [], "delegation": None,
            "acceptance": {}}))
        (self.run_dir / "alignment.json").write_text(json.dumps({
            "schema": alignment.ALIGNMENT_SCHEMA,
            "pending": [{"reading": str(reading / "reading.json"),
                         "source": {"card": CARD, "manifest": MANIFEST},
                         "read": {"card": CARD}, "claims": [waiting],
                         "state": "AWAITING_DECISION_REVALIDATE_BEFORE_ACCEPT"}]}))

    def _write_record(self, *, finished="2026-10-06T11:20:30+00:00",
                      name="window", code="NO_CHANGE", only=False):
        self.record_dir.mkdir(exist_ok=True)
        if only:
            for stale in self.record_dir.glob("*.json"):
                stale.unlink()
        (self.record_dir / f"{name}.json").write_text(json.dumps(
            {"state": "STOPPED", "stopped_because": code, "stopped_why": "到了该停的地方",
             "finished_at": finished, "outcomes": {"unread": []}}))

    def evening(self, *argv, at=AT):
        """Exit code, stdout and stderr — kept apart.

        Apart because the two carry different kinds of sentence: a refusal is on
        stderr and the result is on stdout, and a helper that merged them would
        make `--json` output unparseable exactly in the cases where the command
        had something to refuse about.

        `at` is a parameter so a test can say which two instants it is asking
        about. It defaults to the fixture's own evening, which is what every test
        that cares about a *day* wants; a test that cares about what two runs
        minutes apart do has to state both instants itself, because a fixture
        that pinned one instant is exactly what hid the retry defect.
        """
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["report", "evening", "--json",
                             "--run-dir", str(self.run_dir),
                             "--record-dir", str(self.record_dir),
                             "--out-root", str(self.out_root),
                             "--requested-by", "serveros-evening-alignment.timer",
                             "--why", "每晚的日常阅读对齐", "--at", at, *argv])
        return code, out.getvalue(), err.getvalue()

    def out_dir(self) -> Path:
        return self.out_root / DAY

    def outbox(self):
        path = delivery.outbox_file(self.out_dir())
        if not Path(path).is_file():
            return None
        return json.loads(Path(path).read_text())

    # ── no consumer configured ──────────────────────────────────────────────

    def test_with_no_recipient_the_brief_is_written_and_nothing_is_requested(self):
        """Exit 2, not 0: "composed and nobody will be told" is not a delivery.

        The brief IS written — that is the useful half, and it is where a person
        finds it. What must not happen is a green result: a card that says
        "nothing needs you" reaches nobody by definition, and a unit that exits 0
        would make this evening identical to one that delivered.
        """
        code, out, err = self.evening()

        assert code == 2, out + err
        assert (self.out_dir() / alignment.BRIEF_FILE).is_file()
        assert json.loads(out)["recipient"] is None
        assert self.outbox() is None

    def test_the_card_says_which_run_record_answered_for_tonight(self):
        code, payload, _ = self.evening()
        result = json.loads(payload)

        assert code == 2
        assert result["day"] == DAY
        assert "2026-10-06" in result["run_record"]
        assert result["recipient"] is None and result["request"] is None

    def test_a_brief_from_a_run_record_of_another_day_is_not_measured(self):
        """The whole point of dating a record by itself: last night's pass is not tonight's."""
        self._write_record(finished="2026-10-04T11:20:30+00:00", name="older", only=True)

        code, payload, _ = self.evening()

        assert code == 2
        assert "没有 2026-10-06" in json.loads(payload)["run_record"]
        body = (self.out_dir() / alignment.BRIEF_FILE).read_text()
        assert "没有量过" in body

    def test_the_same_evening_twice_writes_one_brief_and_no_second_directory(self):
        self.evening()
        first = (self.out_dir() / alignment.BRIEF_FILE).read_bytes()
        self.evening()

        assert (self.out_dir() / alignment.BRIEF_FILE).read_bytes() == first
        assert sorted(p.name for p in self.out_root.iterdir()) == [DAY]

    # ── a recipient, and no transport that can reach one ────────────────────

    def test_the_intent_is_recorded_before_the_delivery_is_attempted(self):
        """Order, and the queue entry surviving the attempt.

        The intent is written by `report_delivery.request` and the attempt by
        `.deliver`; asserting the outbox after the command proves the first ran
        before the second stopped. A command that delivered first would leave
        nothing to retry, and `PENDING_CONSUMER` would read as "nothing was ever
        asked for".
        """
        self._set_recipient(None)
        real = cli._report_sender
        cli._report_sender = _no_transport
        self.addCleanup(setattr, cli, "_report_sender", real)

        code, payload, err = self.evening("--conversation", "user:ou_synthetic")

        assert code == 2, payload + err
        result = json.loads(payload)
        assert result["request"]["state"] == "REQUESTED"
        assert result["delivery"]["state"] == "PENDING_CONSUMER"
        entries = self.outbox()["entries"]
        assert len(entries) == 1
        assert entries[0]["state"] == "PENDING"
        assert entries[0]["audience"]["recipient"] == "user:ou_synthetic"
        assert entries[0]["requested_by"] == "serveros-evening-alignment.timer"

    def test_asking_twice_for_the_same_evening_is_one_intent_and_not_two_messages(self):
        real = cli._report_sender
        cli._report_sender = _no_transport
        self.addCleanup(setattr, cli, "_report_sender", real)

        self.evening("--conversation", "user:ou_synthetic")
        code, payload, _ = self.evening("--conversation", "user:ou_synthetic")

        assert code == 2
        assert json.loads(payload)["request"]["state"] == "ALREADY_REQUESTED"
        assert len(self.outbox()["entries"]) == 1

    def test_a_retry_minutes_later_is_one_intent_and_one_message(self):
        """The retry requirement, at the level the timer actually runs at.

        `serveros-evening-alignment.service` passes no `--at`: every run carries
        the wall clock, so two runs are two instants. While the title held the
        render minute, those two instants were two versions, `intent_key` keys a
        delivery on the version, and the second version was a second card to the
        same person — measured on this node, the transport was called twice.

        Five minutes apart rather than "twice in a row" on purpose: back-to-back
        runs land in the same minute and would pass even with the defect.
        """
        self._set_recipient(None)
        sent = []
        real = cli._report_sender
        cli._report_sender = _recording_transport(sent)
        self.addCleanup(setattr, cli, "_report_sender", real)

        first = self.evening("--conversation", "user:ou_synthetic",
                             at="2026-10-06T12:00:00+00:00")
        second = self.evening("--conversation", "user:ou_synthetic",
                              at="2026-10-06T12:05:00+00:00")

        assert first[0] == 0, first[1] + first[2]
        assert second[0] == 0, second[1] + second[2]
        assert json.loads(first[1])["delivery"]["state"] == "DELIVERED"
        assert json.loads(second[1])["delivery"]["state"] == "ALREADY_DELIVERED"
        assert len(sent) == 1
        assert len(self.outbox()["entries"]) == 1

    def test_a_recipient_from_the_environment_is_the_one_that_is_used(self):
        self._set_recipient("user:ou_from_env")
        real = cli._report_sender
        cli._report_sender = _no_transport
        self.addCleanup(setattr, cli, "_report_sender", real)

        code, _, err = self.evening()

        assert code == 2, err
        assert self.outbox()["entries"][0]["audience"]["recipient"] == "user:ou_from_env"

    # ── refusals ────────────────────────────────────────────────────────────

    def test_a_relative_out_root_is_refused_before_anything_is_written(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(["report", "evening", "--json",
                             "--run-dir", str(self.run_dir),
                             "--record-dir", str(self.record_dir),
                             "--out-root", "relative/evening",
                             "--requested-by", "serveros-evening-alignment.timer",
                             "--why", "每晚的日常阅读对齐", "--at", AT])

        assert code == 2
        assert "不是绝对路径" in out.getvalue()
        assert not (self.root / "relative").exists()

    def test_a_preparation_from_another_day_stops_the_command_rather_than_sending_it(self):
        stamp = datetime.datetime.fromisoformat("2026-10-05T12:00:00+00:00").timestamp()
        os.utime(self.run_dir / "alignment.json", (stamp, stamp))

        code, _, err = self.evening()

        assert code == 2, err
        assert "19:20 的准备没有在当天跑成" in err
        assert not self.out_dir().exists()


def _no_transport(recipient):
    """What `_report_sender` does on a node with no transport for this recipient."""
    raise RuntimeError("no transport in this test")


def _recording_transport(sent):
    """A transport that accepts and records. The half `_no_transport` cannot reach.

    Every other test in this file exercises the `PENDING_CONSUMER` side, because
    a real transport would either post to an invented open_id or measure this
    node's lark-cli. This one answers DELIVERED and keeps the envelope, which is
    the only way to ask "did the second run send a second message" — the
    question the retry requirement is about. What it does NOT show: that a real
    Feishu transport accepts the envelope. That still needs a person.
    """
    def send(envelope):
        sent.append(envelope)
        return {"state": "DELIVERED", "why": "测试替身：记下了这次调用",
                "provider": {"id": "test-transport", "transport": "test"}}
    return lambda recipient: send


if __name__ == "__main__":
    unittest.main()
