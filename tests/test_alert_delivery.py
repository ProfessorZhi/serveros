#!/usr/bin/env python3
"""§W — the last hop. A person is told, and the record says what happened.

WHAT EACH SECTION KEEPS FALSE
-----------------------------
    the units      "it is installed" (the timer is not a queue, the worker is
                    not a daemon, and the placeholder is not a chat id)
    the seam       "you were told" (the CLI said DELIVERED and the register
                    believed a word it invented)
    delegation     "the cooldown is 15 minutes" (a second copy of the register's
                    clock, in the surface that runs every five)
    §Z             "the system handled it" (an alert became an authorization)

WHY THIS FILE IS NOT `test_alerting.py`
---------------------------------------
That file owns the register, and it covers dedupe, cooldown, hysteresis,
recovery and the four delivery outcomes thoroughly — through
`alerting.record_delivery`, which is where the facts are written. What it does
not and cannot cover is the hop this round added: `serverosctl alert sync`,
which holds the only provider in the tree, reads the transport's answer in the
transport's vocabulary, and has to TRANSLATE it before handing it to a register
that refuses words outside its own four.

That translation is four lines in `runtime/cli.py` and it is load-bearing: the
register rejects a state it does not know, so a passthrough of the provider's
`SUPPORTED` would turn every failed delivery into a `DaemonError` at the exact
moment somebody needs to be told. The seam is what this file tests.

WHY THE FIXTURE IS A REAL STORE
-------------------------------
The fake below is a fake DAEMON, not a fake register: `alert.pass`,
`alert.pending` and `alert.record-delivery` are served by calling the real
`alerting` functions against a real `AlertStore` on a temp path, and the
delivery validation mirrors `runtime/daemon.py#_r_alert_record_delivery`
including its refusal of an unknown state. A stub that returned `{"ok": True}`
would let the CLI's mapping be wrong and stay green, which is the one thing
this file exists to catch.
"""
from __future__ import annotations

import ast
import datetime
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNTIME = ROOT / "runtime"

DEVICE = "device:server-beijing"
FS = "filesystem:/"
COOLDOWN_HIGH = 900


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Loaded under the names the modules themselves look each other up with. One
# file under two names is two classes, and `isinstance` then answers False about
# an object of its own type — the hazard `test_alerting.py` documents.
alerting = _load("serveros_runtime_alerting", RUNTIME / "alerting.py")
cli_mod = _load("serveros_runtime_cli", RUNTIME / "cli.py")


def at(seconds: float = 0.0) -> str:
    base = datetime.datetime(2026, 9, 15, 2, 0, 0, tzinfo=datetime.timezone.utc)
    return (base + datetime.timedelta(seconds=seconds)).isoformat().replace(
        "+00:00", "Z")


T0 = at(0)

# The two detectors by the names a row stores. Literals, for the reason
# `test_alerting.py` gives at its own copy of these two lines.
MEASUREMENT = "measurement-anomaly"


class RecordingProvider:
    """A HumanControlProvider that records what it was asked to say.

    Its capability table is keyed `human.notification.transport`, which is
    `notifications.CAPABILITY` — a fixture using a key of its own would test
    this file's opinion about the transport rather than the transport.
    """

    def __init__(self, state="SUPPORTED", *, reason=None, notify_raises=None):
        self.provider_id = "provider:test-human-control"
        self._state = state
        self._reason = reason
        self._notify_raises = notify_raises
        self.calls: list[dict] = []

    def capabilities(self) -> dict:
        table = {"human.notification.transport":
                 self._state if self._reason is None
                 else {"state": self._state, "reason": self._reason}}
        return {"provider": self.provider_id, "transport": "test",
                "capabilities": table, "measured_by": "fixture"}

    def notify(self, *, conversation_ref: str, text: str,
               idempotency_key: str) -> dict:
        self.calls.append({"conversation_ref": conversation_ref, "text": text,
                           "idempotency_key": idempotency_key})
        if self._notify_raises:
            raise self._notify_raises
        return {"accepted": True, "message_ref": f"om_{len(self.calls)}"}


class FakeDaemonError(RuntimeError):
    """What the real daemon raises for a refusal. Named separately from
    `DaemonError` so a test that sees it knows it came from the fake."""


def fake_daemon(store, *, now=T0):
    """The three alert RPCs `alert sync` uses, served by the real register.

    Faithful where it matters: `record-delivery` refuses a state outside the
    register's four, exactly as `runtime/daemon.py` does. That refusal is what
    makes the CLI's translation table observable — without it, passing the
    provider's own vocabulary through would be silently accepted and this
    file's central assertion would be untestable.
    """
    calls: list[tuple[str, dict]] = []

    def answer(method: str, params: dict) -> dict:
        calls.append((method, dict(params or {})))
        if method == "alert.pass":
            return {"at": now, "passes": []}
        if method == "alert.pending":
            pending = alerting.pending_for(store, at=now)
            return {"at": now, "count": len(pending), "pending": pending}
        if method == "alert.record-delivery":
            state = params.get("state")
            if state not in ("DELIVERED", "FAILED", "UNSUPPORTED", "UNKNOWN"):
                raise FakeDaemonError(
                    f"{state!r} is not one of DELIVERED, FAILED, UNSUPPORTED, "
                    f"UNKNOWN — an outcome nobody can name is an outcome that "
                    f"will be read as a delivery")
            stored = alerting.record_delivery(
                store, alert_ref=params["alert_ref"], kind=params["kind"],
                at=now, delivered=state == "DELIVERED", state=state,
                reason=params.get("reason"))
            return {"alert_ref": stored["alert_ref"], "state": state}
        raise FakeDaemonError(f"the fixture serves no {method!r}")

    return answer, calls


class Args:
    """The four attributes `cmd_alert_sync` reads, and no parser."""

    def __init__(self, *, conversation="conversation:ops", dry_run=False,
                 json=False):
        self.conversation = conversation
        self.dry_run = dry_run
        self.json = json


# ---------------------------------------------------------------------------
# The seam. §12, and the four lines in `runtime/cli.py` that carry it.
# ---------------------------------------------------------------------------


class TheDeliveryWorkerTranslatesWhatTheTransportSaid(unittest.TestCase):
    """Every assertion here runs the real `cmd_alert_sync` end to end.

    The fake is a daemon; the register, the renderer, the provider contract and
    the translation are all the ones that ship.
    """

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = alerting.AlertStore(
            Path(directory.name) / "state" / "alerts.json")
        self.daemon, self.rpcs = fake_daemon(self.store)
        self._real_ask = cli_mod.ask_daemon
        self._real_provider = cli_mod._alert_provider
        cli_mod.ask_daemon = self.daemon
        self.addCleanup(self._restore)
        self.alert = self._open()

    def _restore(self):
        cli_mod.ask_daemon = self._real_ask
        # RESTORE the original, do not `del` the attribute. `_alert_provider`
        # here is a module-level FUNCTION and not a patch on one, so deleting it
        # does not undo this fixture's shadow — it removes the real function and
        # leaves an AttributeError for every later caller in the same run. The
        # bug was latent until a second test in the suite called
        # `_alert_provider()` after this file had run
        # (`test_direct_lark_notify.py::AlertProviderSelectionTest`); `del` was
        # never the right inverse of assigning a module attribute.
        cli_mod._alert_provider = self._real_provider

    def _open(self):
        alerting.evaluate(
            self.store,
            detection={"signals": [{
                "code": "DATA_FS_PRESSURE", "subject": FS, "severity": "high",
                "measured": {"used_percent": 93.0, "threshold_percent": 85.0},
                "why": "the data filesystem is at 93%"}],
                "levels": {f"DATA_FS_PRESSURE|{FS}": {
                    "code": "DATA_FS_PRESSURE", "subject": FS, "value": 93.0,
                    "threshold": 85.0,
                    "margin_key": "filesystem_percent_recovery_margin"}},
                "codes_not_evaluated": [], "answered_by": MEASUREMENT,
                "measured_at": T0},
            device_ref=DEVICE, at=T0, detector=MEASUREMENT)
        rows = self.store.alerts()
        self.assertEqual(len(rows), 1, rows)
        return next(iter(rows.values()))

    def row(self):
        return self.store.alerts()[self.alert["alert_ref"]]

    def sync(self, provider, **kwargs):
        cli_mod._alert_provider = lambda: provider
        return cli_mod.cmd_alert_sync(Args(**kwargs))

    def test_the_alert_is_due_before_the_worker_runs(self):
        # The precondition for every assertion below. If nothing were pending,
        # each of them would be green and about nothing — the shape of test that
        # makes a delivery runtime look working while it says nothing.
        self.assertEqual(len(alerting.pending_for(self.store, at=T0)), 1)

    def test_a_delivered_notification_is_recorded_as_delivered(self):
        provider = RecordingProvider()
        self.assertEqual(self.sync(provider), cli_mod.EXIT_OK)
        self.assertEqual(len(provider.calls), 1)
        row = self.row()
        self.assertEqual(row["last_delivery"], "DELIVERED")
        self.assertEqual(row["last_notified_at"], T0)
        self.assertIsNone(row["pending"])
        self.assertEqual(row["state"], "OPEN",
                         "a delivery is not a resolution — nothing said the "
                         "condition ended")
        self.assertEqual(provider.calls[0]["conversation_ref"],
                         "conversation:ops")

    def test_a_provider_that_raises_is_recorded_as_FAILED_not_as_SUPPORTED(self):
        """The translation, and the reason it cannot be a passthrough.

        The provider's own word here is `SUPPORTED` — the transport CAN carry a
        notification — and the call threw anyway. Handing `SUPPORTED` to the
        register would be refused by name (`DaemonError`), and a caller that
        caught that would be left with an alert that nothing could ever mark as
        attempted. The CLI owns both answers, so the CLI is where they are
        reconciled.
        """
        provider = RecordingProvider(notify_raises=RuntimeError("gateway 502"))
        self.assertEqual(self.sync(provider), cli_mod.EXIT_OK)
        row = self.row()
        self.assertEqual(row["last_delivery"], "FAILED")
        self.assertIsNone(row["last_notified_at"])
        self.assertEqual(row["state"], "OPEN")
        self.assertIn("502", row["delivery_failure"] or "")
        self.assertIsNotNone(row["pending"],
                             "a failed delivery leaves the intent pending, or "
                             "the alert is never said again")
        self.assertEqual(row["pending"]["not_before"], at(COOLDOWN_HIGH),
                         "the next attempt is a cooldown away, not immediate — "
                         "the cooldown clock runs from the ATTEMPT")

    def test_an_unsupported_transport_is_recorded_as_UNSUPPORTED(self):
        provider = RecordingProvider("UNSUPPORTED", reason="no bot configured")
        self.assertEqual(self.sync(provider), cli_mod.EXIT_OK)
        self.assertEqual(provider.calls, [],
                         "a transport that has said it cannot must not be asked")
        row = self.row()
        self.assertEqual(row["last_delivery"], "UNSUPPORTED")
        self.assertIsNone(row["last_notified_at"])
        self.assertIsNotNone(row["pending"])

    def test_a_transport_that_raises_when_asked_is_UNKNOWN(self):
        provider = RecordingProvider()
        provider.capabilities = lambda: (_ for _ in ()).throw(
            RuntimeError("no answer"))
        self.assertEqual(self.sync(provider), cli_mod.EXIT_OK)
        row = self.row()
        self.assertEqual(row["last_delivery"], "UNKNOWN",
                         "'nobody established whether it can' is not the same "
                         "answer as 'it cannot'")
        self.assertIsNotNone(row["pending"])

    def test_a_worker_with_no_destination_sends_nothing_and_records_nothing(self):
        provider = RecordingProvider()
        code = self.sync(provider, conversation=None)
        self.assertEqual(code, cli_mod.EXIT_COULD_NOT_ANSWER)
        self.assertEqual(provider.calls, [])
        row = self.row()
        self.assertIsNone(row["last_delivery"])
        self.assertIsNone(row["last_notified_at"])
        self.assertIsNotNone(row["pending"],
                             "the intent survives: nothing was said, so "
                             "nothing was recorded as said")

    def test_a_dry_run_says_what_would_be_sent_and_sends_nothing(self):
        provider = RecordingProvider()
        self.assertEqual(self.sync(provider, dry_run=True), cli_mod.EXIT_OK)
        self.assertEqual(provider.calls, [])
        self.assertIsNone(self.row()["last_delivery"])
        self.assertIn("alert.pass", [m for m, _ in self.rpcs],
                      "a dry run still evaluates — it is the delivery it skips")

    def test_it_evaluates_before_it_delivers(self):
        # Ordering, not decoration. `alert.pending` answers from what the pass
        # just wrote, so a worker that asked for pending first would deliver a
        # tick behind and report the tick before it as the state of the world.
        self.sync(RecordingProvider())
        methods = [m for m, _ in self.rpcs]
        self.assertEqual(methods[0], "alert.pass")
        self.assertLess(methods.index("alert.pass"),
                        methods.index("alert.pending"))
        self.assertLess(methods.index("alert.pending"),
                        methods.index("alert.record-delivery"))


# ---------------------------------------------------------------------------
# Delegation. The register owns the clock; the worker owns only the hop.
# ---------------------------------------------------------------------------


def sync_rpc_names(source: str) -> set[str]:
    """Every string literal `cmd_alert_sync` passes to `_alert_answer`."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_alert_sync":
            return {arg.value for call in ast.walk(node)
                    if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "_alert_answer"
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and isinstance(call.args[0].value, str)
                    for arg in [call.args[0]]}
    raise AssertionError("cmd_alert_sync is not in this source")


# The register's own fields. A name that appears here is a fact
# `alerting.record_delivery` writes, and the delivery worker is allowed to READ
# every one of them — it reads `pending` out of an answer and carries it on, and
# the RPC parameters it sends back (`alert_ref`, `kind`, `state`, `reason`) are
# the surface the register declares. What it may not do is NAME one of these as
# a field of its own: that is a second producer of the schedule, and the two
# would disagree the first time one of them was edited.
REGISTER_FIELDS = ("not_before", "last_state", "last_reason",
                   "cooldown_seconds", "last_notified_at", "last_attempt_at",
                   "delivery_failure", "last_delivery", "last_notified_severity")

# Functions that write to the register. Calling one of these from the worker is
# reaching around the daemon to the store.
REGISTER_WRITERS = ("_shift", "record_delivery", "evaluate", "pending_for")


def _function_named(source: str, name: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not in this source")


def local_derivations(source: str) -> list[str]:
    """Register facts `cmd_alert_sync` produces instead of asking for.

    Written over the AST rather than the text, and over three shapes rather than
    one, because the honest reimplementation is the subtle one: nobody would
    write `not_before = ...` at the top of the worker. They would write
    `alert["pending"] = {"not_before": ...}` while "just fixing up the payload",
    which is a Dict KEY inside the value of a Subscript assignment — a shape the
    first version of this function walked straight past, and the positive
    control below is what caught it.
    """
    fn = _function_named(source, "cmd_alert_sync")
    found = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and node.value in REGISTER_FIELDS:
            found.add(f"names {node.value}")
        if isinstance(node, ast.Assign):
            for target in node.targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name) and sub.id in REGISTER_FIELDS:
                        found.add(f"assigns {sub.id}")
                    if (isinstance(sub, ast.Constant)
                            and sub.value in REGISTER_FIELDS):
                        found.add(f"assigns {sub.value}")
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else None)
            if name in REGISTER_WRITERS:
                found.add(f"calls {name}")
    return sorted(found)


class TheWorkerDelegatesTheClockItDoesNotOwn(unittest.TestCase):
    """§W: 复用现有 runtime。The unit's header claims dedupe, cooldown, recovery
    and failure are all delegated to the register. That claim was prose; this
    makes it checkable against the code that would break it.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = (RUNTIME / "cli.py").read_text()

    def test_the_worker_asks_the_register_three_things(self):
        self.assertEqual(sync_rpc_names(self.source),
                         {"alert.pass", "alert.pending",
                          "alert.record-delivery"},
                         "the delivery worker's whole conversation with the "
                         "register is these three; anything else is state it "
                         "has started keeping")

    def test_the_worker_keeps_no_clock_of_its_own(self):
        self.assertEqual(local_derivations(self.source), [],
                         "the cooldown, the pending clock and the delivery "
                         "verdict belong to the register — see "
                         "`alerting.record_delivery`")

    def test_the_scanner_reports_a_worker_that_kept_one(self):
        # The positive control. Both functions above return an empty answer for
        # a source they cannot read, and an empty answer is what "no
        # reimplementation" looks like — so a synthetic worker that DOES keep
        # the clock has to be caught by the same two functions.
        reimplemented = (
            "def cmd_alert_sync(args):\n"
            "    passed, code = _alert_answer('alert.pass', {})\n"
            "    pending, code = _alert_answer('alert.pending', {})\n"
            "    for item in pending['pending']:\n"
            "        alert = item['alert']\n"
            "        alert['pending'] = {'not_before': _shift(\n"
            "            passed['at'], 900)}\n"
            "    record_delivery(store, alert_ref=ref, at=now)\n")
        self.assertIn("names not_before", local_derivations(reimplemented))
        self.assertIn("calls _shift", local_derivations(reimplemented))
        self.assertIn("calls record_delivery", local_derivations(reimplemented))
        self.assertEqual(sync_rpc_names(reimplemented),
                         {"alert.pass", "alert.pending"})
        # The blunt shape too, because the two are not the same mistake and a
        # scanner that only caught one of them would be half a gate.
        blunt = ("def cmd_alert_sync(args):\n"
                 "    last_notified_at = args.at\n"
                 "    record_delivery(store, at=args.at)\n")
        self.assertIn("assigns last_notified_at", local_derivations(blunt))
        # And a source with no such function at all is refused rather than
        # read as a clean one — the failure mode of every scanner in this tree
        # that returns an empty list for input it could not read.
        for empty in ("def something_else():\n    pass\n", ""):
            with self.assertRaises(AssertionError):
                sync_rpc_names(empty)
            with self.assertRaises(AssertionError):
                local_derivations(empty)


# ---------------------------------------------------------------------------
# The units. §W's "不是第二个 ServerOS daemon", made checkable.
# ---------------------------------------------------------------------------


def directives(text: str) -> list[tuple[str, str, str]]:
    """(section, key, value) for every `Key=value` line, case-folded on key."""
    found, section = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        found.append((section, key.strip().lower(), value.strip()))
    return found


class TheDeliveryRuntimeIsTwoFilesAndNothingMore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = (RUNTIME / "serveros-alert-sync.service").read_text()
        cls.timer = (RUNTIME / "serveros-alert-sync.timer").read_text()

    def test_the_worker_is_a_oneshot_that_does_not_restart_itself(self):
        keys = dict((k, v) for _, k, v in directives(self.service))
        self.assertEqual(keys.get("type"), "oneshot")
        self.assertNotIn("restart", keys,
                         "the retry policy is the register's, and the next "
                         "timer tick is the retry")
        self.assertEqual(keys.get("timeoutstartsec"), "120",
                         "an unbounded oneshot is a timer that stops firing")

    def test_the_timer_is_a_schedule_and_not_a_queue(self):
        keys = dict((k, v) for _, k, v in directives(self.timer))
        self.assertEqual(keys.get("persistent"), "false",
                         "the durable queue is the register; catch-up would "
                         "only decide whether a returning machine sends a burst")
        self.assertEqual(keys.get("unit"), "serveros-alert-sync.service")
        self.assertNotIn("execstart", keys, "a timer does not run anything")

    def test_the_worker_holds_no_socket_directory(self):
        keys = dict((k, v) for _, k, v in directives(self.service))
        self.assertNotIn("runtimedirectory", keys,
                         "systemd deletes a RuntimeDirectory when the unit "
                         "that declared it stops — a oneshot declaring the "
                         "daemon's would delete the daemon's socket "
                         "every five minutes")
        self.assertNotIn("listenstream", keys)

    def test_the_template_names_a_destination_it_cannot_invent(self):
        """Without this the refusal in `render_unit` is vacuous, and the whole
        "no destination, no install" property would rest on a constant nobody
        uses.

        Asserted against the DIRECTIVE and not against the file, which is the
        difference between a gate and a green tick: this header discusses the
        placeholder by name four times, so a file-wide `assertIn` stayed green
        when ExecStart was rewritten to a literal. The mutation harness found
        that, and this is the repair.
        """
        starts = [v for _, k, v in directives(self.service) if k == "execstart"]
        self.assertEqual(len(starts), 1, starts)
        self.assertIn("@SERVEROS_ALERT_CONVERSATION@", starts[0],
                      "the installed unit has no destination a person supplies, "
                      "so `render_unit`'s refusal is about a placeholder nothing "
                      "uses")
        keys = {k for _, k, _ in directives(self.timer)}
        self.assertNotIn("execstart", keys, "a timer does not carry a destination")

    def test_it_starts_the_cli_and_never_the_provider(self):
        starts = [v for _, k, v in directives(self.service) if k == "execstart"]
        self.assertEqual(len(starts), 1, starts)
        self.assertIn("bin/serverosctl", starts[0])
        self.assertIn("alert sync", starts[0])
        for forbidden in ("human_control", "notifications", "curl", "python3 -c"):
            self.assertNotIn(forbidden, starts[0],
                             "the worker carries the CLI; the transport is "
                             "constructed inside it, from configuration, and "
                             "never named on a command line a unit file owns")


if __name__ == "__main__":
    unittest.main()
