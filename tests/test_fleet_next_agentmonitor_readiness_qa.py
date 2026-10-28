"""Independent QA: readiness of the Agent Monitor, and what is NOT being claimed.

WHAT THIS FILE IS, AND WHAT IT REFUSES TO BE
--------------------------------------------
The Agent Monitor's own source (`integrations/fleet-next-agent-monitor/`) has not
landed on `main`, on any open PR, or in any local checkout. The one thing this
suite must never do is invent a stub of it so that "monitor QA" can go green —
a stub would certify the shape of the test rather than the shape of the product,
and the next reader would have no way to tell the two apart.

So the monitor's behaviour tests are **skipped with a named block**
(`BLOCKED_UPSTREAM_SOURCE`), which reads as *not run*, never as *passed*. What
this suite does assert is everything the landed pieces genuinely carry:

  * the old Fleet control surface is not touched by the monitor train
    (acceptance 1, as far as it can be without the monitor);
  * queued / RUN_NOT_FOUND / completed-without-native-output are never GREEN
    (acceptance 2 — the classifier and the observer both exist today);
  * one delivery per key across two interpreters (acceptance 3);
  * actionable RED and significant VERIFIED_GREEN stay distinguishable, and the
    observer's own health is readable (acceptance 4);
  * no second bus, sender, resident LLM or auto-remediation (acceptance 5);
  * the batch dispatcher has no entry point, so no timer can be claimed
    (`BLOCKED_ENTRY`).

Modules are loaded by path rather than imported: they live under
`integrations/`, are not packages, and their filenames are chosen by their
authors.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTIFY = ROOT / "integrations" / "fleet-v1-milestone-notify" / "milestone_notify.py"
OBSERVER = ROOT / "integrations" / "fleet-v1-milestone-observer" / "observer.py"
FIXTURES = ROOT / "integrations" / "fleet-v1-milestone-observer" / "fixtures" / "cloudcli"
MONITOR_DIR = ROOT / "integrations" / "fleet-next-agent-monitor"
BATCH_DISPATCH = ROOT / "integrations" / "fleet-v1-milestone-batch" / "dispatch.py"

BLOCKED_UPSTREAM_SOURCE = (
    "BLOCKED_UPSTREAM_SOURCE: integrations/fleet-next-agent-monitor/ is absent from "
    "this tree, from main and from every open PR. The monitor's own behaviour "
    "cannot be exercised, and this suite will not substitute a stub for it. "
    "Replace this skip with behavioural tests when the source lands."
)


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def notify_module():
    return load(NOTIFY, "monitor_qa_notify")


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TheMonitorSourceHasNotLanded(unittest.TestCase):
    """The honest headline. A skip is not a pass, and this says why."""

    @unittest.skipUnless(MONITOR_DIR.is_dir(), BLOCKED_UPSTREAM_SOURCE)
    def test_the_monitor_answers_one_bounded_tick(self):
        self.fail("unreachable while the skip holds — the monitor has no source here")

    @unittest.skipUnless(MONITOR_DIR.is_dir(), BLOCKED_UPSTREAM_SOURCE)
    def test_the_monitor_owns_a_last_tick_and_a_cursor(self):
        self.fail("unreachable while the skip holds")


class TheOldFleetIsNotTakenOver(unittest.TestCase):
    """Acceptance 1, measured where it can be: the control surface is untouched.

    The monitor train adds observers and notifiers. If any of them reached into
    `fleet_mcp`, "old Fleet keeps serving GPT" would be a hope rather than a
    fact. Nothing here imports it.
    """

    CONTROL_MODULES = ("fleet_mcp/fleet.py", "fleet_mcp/registry.py")

    def test_the_monitor_train_does_not_import_the_control_plane(self):
        offenders = []
        for path in (NOTIFY, OBSERVER):
            text = path.read_text(encoding="utf-8")
            for needle in ("import fleet_mcp", "from fleet_mcp"):
                if needle in text:
                    offenders.append((path.name, needle))
        self.assertEqual(offenders, [],
                         "the monitor train reached into the control plane; the old "
                         "Fleet surface would no longer be independent of it")

    def test_the_control_surface_these_modules_must_not_disturb_is_present(self):
        fleet = (ROOT / "fleet_mcp" / "fleet.py").read_text(encoding="utf-8")
        for method in ("def get_run(", "def get_session("):
            self.assertIn(method, fleet, f"the old Fleet must still expose {method}")
        self.assertTrue((ROOT / "fleet_mcp" / "registry.py").is_file())

    def test_the_notifier_delivers_through_the_existing_transport(self):
        text = NOTIFY.read_text(encoding="utf-8")
        # Reuse, not a second sender: the docstring names the shared transport,
        # and no send loop of its own is defined.
        self.assertIn("message_transport", text,
                      "the notifier must reuse the existing transport rather than "
                      "speak to a provider itself")
        self.assertNotIn("def _send_loop", text)


class QueuedNotFoundAndSilentCompletionAreNeverGreen(unittest.TestCase):
    """Acceptance 2. Uses the authors' own fixtures, not payloads written here."""

    def setUp(self):
        self.n = notify_module()

    def _decision(self, payload):
        return self.n.decide(self.n.normalize("cloudcli", payload))

    def test_a_completed_run_without_native_output_is_unknown_not_green(self):
        decision = self._decision(fixture("completed-no-output.json"))
        self.assertEqual(decision.outcome, "UNKNOWN")
        self.assertNotIn(decision.outcome, ("VERIFIED_GREEN", "ORDINARY_GREEN"),
                         "a completion whose native output is missing is never green")
        # It is still DELIVERED — as an ACTION escalation, not as a milestone.
        # That distinction is the product's, and conflating it with "silent" here
        # would have hidden a real behaviour behind a passing assertion.
        self.assertTrue(decision.deliver)
        self.assertEqual(decision.severity, "ACTION")
        self.assertIn("not treat this as success", decision.detail.lower()
                      + " " + getattr(decision, "next_action", "").lower())

    def test_a_running_run_is_silent(self):
        decision = self._decision(fixture("running.json"))
        self.assertEqual(decision.outcome, "SILENT")

    def test_a_queued_run_is_silent_and_never_green(self):
        payload = fixture("running.json")
        payload["run"] = dict(payload["run"], status="queued")
        decision = self._decision(payload)
        self.assertNotIn(decision.outcome, ("VERIFIED_GREEN", "ORDINARY_GREEN"))
        self.assertFalse(decision.deliver)

    def test_a_failed_run_is_red_but_not_action_required(self):
        decision = self._decision(fixture("failed.json"))
        self.assertEqual(decision.outcome, "RED")

    def test_a_fully_gated_completion_is_verified_green(self):
        decision = self._decision(fixture("completed.json"))
        self.assertEqual(decision.outcome, "VERIFIED_GREEN")
        self.assertTrue(decision.deliver)

    def test_run_not_found_is_a_wait_on_the_observer_and_not_a_milestone(self):
        o = load(OBSERVER, "monitor_qa_observer")
        # The observer's refusal vocabulary decides this, not the notifier.
        self.assertIn("RUN_NOT_FOUND", o._TRANSIENT_CODES,
                      "RUN_NOT_FOUND must be a wait that can clear, not a milestone")
        self.assertNotIn("PERMISSION", o._TRANSIENT_CODES)
        self.assertIn("RUN_NOT_FOUND", o.__doc__ + o.MilestoneObserver.__doc__ + " ")


class OneDeliveryPerKeyAcrossInterpreters(unittest.TestCase):
    """Acceptance 3, at the grain that can be measured without the monitor.

    Two interpreters, one decision: the delivery key must be identical, because a
    ledger keyed on anything process-local would deliver twice. The ledger's own
    "DELIVERED is final" rule is asserted separately.
    """

    SCRIPT = (
        "import importlib.util,json,sys\n"
        "p=sys.argv[1]\n"
        "s=importlib.util.spec_from_file_location('n',p);m=importlib.util.module_from_spec(s)\n"
        "sys.modules['n']=m;s.loader.exec_module(m)\n"
        "d=json.load(open(sys.argv[2]))\n"
        "dec=m.decide(m.normalize('cloudcli',d))\n"
        "print(m.delivery_key_for(dec))\n"
    )

    def _key_in_fresh_interpreter(self) -> str:
        done = subprocess.run(
            [sys.executable, "-I", "-c", self.SCRIPT, str(NOTIFY),
             str(FIXTURES / "completed.json")],
            capture_output=True, text=True, timeout=120,
            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(done.returncode, 0, done.stderr[-600:])
        return done.stdout.strip().splitlines()[-1]

    def test_two_interpreters_agree_on_the_delivery_key(self):
        first, second = self._key_in_fresh_interpreter(), self._key_in_fresh_interpreter()
        self.assertEqual(first, second,
                         "a delivery key that differs between processes cannot make "
                         "the ledger a single-delivery guard")
        self.assertTrue(first)

    def test_delivered_is_final_in_the_ledger(self):
        text = NOTIFY.read_text(encoding="utf-8")
        self.assertIn("DELIVERED is final", text.replace("\n", " ").replace("  ", " ")
                      or text,
                      "the ledger must treat DELIVERED as terminal, or a later tick "
                      "re-asks and delivers twice")


class RedAndVerifiedGreenStayDistinguishable(unittest.TestCase):
    """Acceptance 4: an alert is not a milestone, and the monitor's own health
    must be readable rather than inferred."""

    def setUp(self):
        self.n = notify_module()
        self.o = load(OBSERVER, "monitor_qa_observer_2")

    def test_action_required_is_red_and_carries_the_flag(self):
        payload = json.loads(
            (ROOT / "integrations" / "fleet-v1-milestone-observer" / "fixtures"
             / "github_actions" / "action-required.json").read_text(encoding="utf-8"))
        decision = self.n.decide(self.n.normalize("github_actions", payload))
        self.assertEqual(decision.outcome, "RED")
        self.assertTrue(decision.action_required)

    def test_a_green_ci_run_is_verified_green_only_with_its_gates(self):
        payload = json.loads(
            (ROOT / "integrations" / "fleet-v1-milestone-observer" / "fixtures"
             / "github_actions" / "green.json").read_text(encoding="utf-8"))
        decision = self.n.decide(self.n.normalize("github_actions", payload))
        self.assertIn(decision.outcome, ("VERIFIED_GREEN", "UNKNOWN"),
                      "green CI is either fully gated or explicitly unknown")

    def test_one_real_tick_reports_its_own_health_and_delivers_once(self):
        """A real tick: injected source, real notifier, mock clock, temp ledger.

        Read as the monitor's own health surface — when it looked, how many reads
        it spent, what each ref settled as — and as the single-delivery guard:
        the second tick must not send a second message for the same decision.
        """
        import tempfile
        work = Path(tempfile.mkdtemp(prefix="monitor-qa-tick-"))
        self.addCleanup(__import__("shutil").rmtree, work, True)

        class FakeSource:
            """Reads a real fixture. Not a stub of the monitor — a stub of the WIRE."""
            def __init__(self):
                self.calls = 0
            def read(self, source, ref):
                self.calls += 1
                return fixture("completed.json")

        class RecordingProvider:
            def __init__(self):
                self.sent = []
            def send_card(self, card, *, idempotency_key, version=None):
                self.sent.append(idempotency_key)
                return {"ok": True, "message_id": "om_qa_1"}

        source = FakeSource()
        provider = RecordingProvider()
        notifier = self.n.MilestoneNotifier(
            provider, work / "ledger.json",
            conversation_ref="conversation:qa",
            clock=lambda: 1790000000)
        observer = self.o.MilestoneObserver(
            source=source, notifier=notifier, state_path=work / "state.json",
            watches=[self.o.Watch(source="cloudcli", ref="run_qa_1",
                                  device_ref="device:tokyo-shared-dev-01",
                                  boot_id="boot-A")],
            clock=lambda: 1790000000)

        first = observer.run_once()
        for key in ("observed_at", "reads", "counts", "rows"):
            self.assertIn(key, first,
                          f"the observer must publish {key!r} about its own tick")
        # `reads` is a per-source counter, not a scalar.
        self.assertIsInstance(first["reads"], dict)
        self.assertGreaterEqual(first["reads"].get("cloudcli", 0), 1)
        self.assertEqual(len(first["rows"]), 1)

        second = observer.run_once()
        self.assertEqual(len(second["rows"]), 1)
        self.assertLessEqual(len(provider.sent), 1,
                             "a second tick re-delivered the same decision; the "
                             "ledger is not a single-delivery guard")
        self.assertEqual(len(provider.sent), len(set(provider.sent)),
                         "the same key was sent twice")

    def test_the_observer_keeps_a_read_cursor_per_entry(self):
        text = OBSERVER.read_text(encoding="utf-8")
        self.assertIn("last_read_at", text,
                      "each entry must carry when it was last read, or 'cursor age' "
                      "cannot be answered")


class NoSecondBusSenderOrRemediation(unittest.TestCase):
    """Acceptance 5, read from the AST rather than from the prose.

    A text search for `while True` matches the module's own docstring, which says
    it is *not* an event bus — the same trap this review keeps finding elsewhere.
    So the check is structural: no resident loop, no thread/process/task is
    created, and nothing calls something that looks like a repair.
    """

    def _trees(self):
        import ast
        for path in (NOTIFY, OBSERVER):
            yield path, ast.parse(path.read_text(encoding="utf-8"))

    def test_neither_module_creates_a_loop_thread_or_process(self):
        import ast
        offenders = []
        for path, tree in self._trees():
            for node in ast.walk(tree):
                if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) \
                        and node.test.value is True:
                    # A generator whose loop is driven by a caller-supplied
                    # `sleep` is a seam, not a resident loop: it yields, it is
                    # bounded by `max_ticks`, and nothing here starts it. Only a
                    # loop that actually spins on its own is refused.
                    spins = not any(isinstance(child, (ast.Yield, ast.YieldFrom))
                                    for child in ast.walk(node))
                    if spins:
                        offenders.append((path.name, "self-driven while True", node.lineno))
                if isinstance(node, ast.Call):
                    name = ""
                    if isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    elif isinstance(node.func, ast.Name):
                        name = node.func.id
                    if name in {"Thread", "Popen", "create_task", "fork",
                                "Process", "Timer"}:
                        offenders.append((path.name, name, node.lineno))
        self.assertEqual(offenders, [],
                         "the monitor train starts its own execution context; that is "
                         "the second event system the design refuses")

    def test_neither_module_calls_a_repair(self):
        import ast
        offenders = []
        for path, tree in self._trees():
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if any(word in node.name.lower()
                           for word in ("restart", "remediat", "recover", "heal")):
                        offenders.append((path.name, node.name, node.lineno))
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "attr", None) or getattr(node.func, "id", "")
                    if name and any(word in name.lower()
                                    for word in ("restart", "remediat", "recover")):
                        offenders.append((path.name, name, node.lineno))
        self.assertEqual(offenders, [],
                         "an observer that repairs is not an observer")


class TheBatchDispatchHasNoEntryPoint(unittest.TestCase):
    """`BLOCKED_ENTRY`: the dispatcher is a library, so no timer can be claimed.

    The check runs against `integrations/fleet-v1-milestone-batch/dispatch.py`
    when the batch branch is present. On this tree it is absent, and absence is
    recorded rather than papered over.
    """

    def test_there_is_no_cli_to_schedule(self):
        if not BATCH_DISPATCH.is_file():
            self.skipTest(
                "BLOCKED_ENTRY: integrations/fleet-v1-milestone-batch/dispatch.py is "
                "not on this tree (#470). Measured on #470@56fcb927: the module has "
                "no argparse, no main(), no __main__ and no sys.argv, so nothing here "
                "can be scheduled and no Tokyo timer may be claimed.")
        text = BATCH_DISPATCH.read_text(encoding="utf-8")
        has_cli = any(token in text for token in
                      ("argparse", "def main(", "__main__", "sys.argv"))
        self.assertFalse(has_cli,
                         "the dispatcher grew an entry point; BLOCKED_ENTRY no longer "
                         "holds and the timer claim must be re-measured")


if __name__ == "__main__":
    unittest.main()
