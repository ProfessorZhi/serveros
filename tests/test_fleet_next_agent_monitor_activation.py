#!/usr/bin/env python3
"""The Fleet Next Agent Monitor *activation* contract — and what blocks it.

WHY THIS FILE EXISTS
--------------------
#478 asks for a bounded, one-shot process that the owner can put behind a systemd
**user** unit so the monitor keeps watching after the ChatGPT turn is gone, on a
track that runs beside the Legacy Fleet and never touches it.

Two things are true today and this file pins both:

1. **The upstream entry does not exist.** The batch/dispatch path (#470 at
   `56fcb927`) ships `tick_once`/`tick_from_file` and no `main`; the only real
   bounded CLI in this area is #446's `runner.py`. The FJ one-shot CLI has no
   public SHA. So the activation wrapper must **fail closed** with
   `BLOCKED_UPSTREAM_ENTRY` rather than invent an entry and hope.
2. **Activation is not installation.** Nothing here starts, stops or restarts a
   service, touches systemd, reads a credential or sends a message. The unit file
   is a template source file, and a test refuses to let it name any Legacy
   resource.

The Red→Green discipline applies: every test below fails before the module
exists, and the negative ones keep failing if the module ever grows the ability to
reach the Legacy track.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = (REPO / "integrations" / "fleet-next-agent-monitor-activation"
               / "activation.py")
MODULE_NAME = "serveros_next_agent_monitor_activation"
TEMPLATE = (REPO / "integrations" / "fleet-next-agent-monitor-activation"
            / "serveros-next-agent-monitor.service.template")

#: The wrapper's own rule is "every path is absolute", so the fixture paths have
#: to be absolute *on the host running the test*. A POSIX literal is not
#: absolute on win32, where the same file then failed for the wrong reason
#: (`MISCONFIGURED`) and hid whatever it was meant to check.
if os.name == "nt":
    _FIXTURE = Path(tempfile.gettempdir()).resolve() / "serveros-next-monitor-fixture"
    LEGACY_ROOT = str(_FIXTURE / "legacy-fleet")
    NEXT_ROOT = str(_FIXTURE / "fleet-next")
else:
    LEGACY_ROOT = "/srv/legacy-fleet"
    NEXT_ROOT = "/srv/fleet-next"
LEGACY_PORT = 3231
NEXT_PORT = 3241


def _load():
    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="next-monitor-activation-"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.m = _load()

    def activation(self, **overrides):
        fields = dict(
            legacy_root=LEGACY_ROOT, next_root=NEXT_ROOT,
            state=NEXT_ROOT + "/state", lock=NEXT_ROOT + "/monitor.lock",
            port=NEXT_PORT, legacy_port=LEGACY_PORT,
            service_name="serveros-next-agent-monitor",
            legacy_service_name="serveros-fleet-node-connector",
            plugin_id="serveros-fleet-next-ingress",
            legacy_plugin_id="serveros-fleet-ingress",
            ticks=2)
        fields.update(overrides)
        return self.m.Activation(**fields)


class UpstreamEntryIsMissingSoWeFailClosed(Base):
    def test_an_unresolvable_monitor_entry_blocks_with_its_own_code(self):
        """The real state today: no quotable upstream CLI exists."""
        result = self.m.run_bounded(
            self.activation(), monitor_entry="does.not.exist:monitor",
            legacy_read=lambda: True, next_write=lambda: False,
            clock=lambda: 0.0)
        self.assertEqual(result["state"], self.m.BLOCKED_UPSTREAM_ENTRY)
        self.assertEqual(result["ticks"], 0,
                         "a blocked activation must not tick the monitor at all")
        self.assertEqual(result["exit"], self.m.EXIT_BLOCKED_UPSTREAM)

    def test_a_resolvable_entry_ticks_a_bounded_number_then_exits(self):
        """No loop: N ticks and out, whatever the monitor would prefer."""
        calls = []

        def monitor():
            calls.append(1)
            return {"state": "SETTLED"}

        result = self.m.run_bounded(
            self.activation(ticks=3), monitor_entry=monitor,
            legacy_read=lambda: True, next_write=lambda: False,
            clock=lambda: 0.0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(result["state"], self.m.BOUNDED_STOP)
        self.assertEqual(result["ticks"], 3)
        self.assertEqual(result["exit"], self.m.EXIT_OK)

    def test_a_raising_monitor_is_reported_and_does_not_loop(self):
        calls = []

        def monitor():
            calls.append(1)
            raise RuntimeError("tick exploded")

        result = self.m.run_bounded(
            self.activation(ticks=5), monitor_entry=monitor,
            legacy_read=lambda: True, next_write=lambda: False,
            clock=lambda: 0.0)
        self.assertEqual(len(calls), 1, "a failing tick must not be retried in place")
        self.assertEqual(result["state"], self.m.TICK_FAILED)
        self.assertEqual(result["exit"], self.m.EXIT_REFUSED)
        self.assertIn("tick exploded", result["why"])


class IsolationFromLegacyIsMechanical(Base):
    def _problems(self, **overrides):
        return self.m.problems_with_activation(self.activation(**overrides))

    def test_the_committed_defaults_are_isolated(self):
        self.assertEqual(self._problems(), [])

    def test_a_next_root_inside_the_legacy_root_is_refused(self):
        problems = self._problems(next_root=LEGACY_ROOT + "/next",
                                  state=LEGACY_ROOT + "/next/state")
        self.assertTrue(any("legacy" in p.lower() for p in problems),
                        "the Next track may not live inside the Legacy checkout")

    def test_sharing_the_legacy_state_directory_is_refused(self):
        problems = self._problems(state=LEGACY_ROOT + "/state")
        self.assertTrue(any("state" in p.lower() for p in problems))

    def test_sharing_the_legacy_lock_is_refused(self):
        problems = self._problems(lock=LEGACY_ROOT + "/monitor.lock")
        self.assertTrue(any("lock" in p.lower() for p in problems))

    def test_reusing_the_legacy_port_is_refused(self):
        problems = self._problems(port=LEGACY_PORT)
        self.assertTrue(any("port" in p.lower() for p in problems))

    def test_reusing_the_legacy_service_name_is_refused(self):
        problems = self._problems(service_name="serveros-fleet-node-connector")
        self.assertTrue(any("service" in p.lower() for p in problems))

    def test_reusing_the_legacy_plugin_id_is_refused(self):
        problems = self._problems(plugin_id="serveros-fleet-ingress")
        self.assertTrue(any("plugin" in p.lower() for p in problems))

    def test_a_relative_path_is_refused(self):
        problems = self._problems(state="relative/state")
        self.assertTrue(any("absolute" in p.lower() for p in problems))


class ReadLegacyButNeverWriteNext(Base):
    def test_legacy_read_being_unavailable_refuses(self):
        result = self.m.run_bounded(
            self.activation(), monitor_entry=lambda: {"row": "ok"},
            legacy_read=lambda: False, next_write=lambda: False,
            clock=lambda: 0.0)
        self.assertEqual(result["state"], self.m.LEGACY_UNREADABLE)
        self.assertEqual(result["exit"], self.m.EXIT_REFUSED)
        self.assertEqual(result["ticks"], 0,
                         "monitoring cannot start against facts it cannot read")

    def test_a_next_shadow_with_write_capability_refuses(self):
        result = self.m.run_bounded(
            self.activation(), monitor_entry=lambda: {"row": "ok"},
            legacy_read=lambda: True, next_write=lambda: True,
            clock=lambda: 0.0)
        self.assertEqual(result["state"], self.m.NEXT_NOT_READ_ONLY)
        self.assertEqual(result["exit"], self.m.EXIT_REFUSED)
        self.assertEqual(result["ticks"], 0)

    def test_a_probe_that_raises_is_treated_as_unavailable(self):
        def broken():
            raise OSError("no route to host")

        result = self.m.run_bounded(
            self.activation(), monitor_entry=lambda: {"row": "ok"},
            legacy_read=broken, next_write=lambda: False, clock=lambda: 0.0)
        self.assertEqual(result["state"], self.m.LEGACY_UNREADABLE)


class TheMonitorWatchesItself(Base):
    def test_self_health_names_the_five_things_a_stalled_monitor_hides(self):
        result = self.m.run_bounded(
            self.activation(ticks=1), monitor_entry=lambda: {"row": "ok"},
            legacy_read=lambda: True, next_write=lambda: False,
            clock=lambda: 0.0)
        health = result["health"]
        for field in ("last_tick", "cursor_age", "backlog",
                      "unknown_receipts", "stale"):
            self.assertIn(field, health, f"{field} is missing from self-health")

    def test_a_stale_cursor_is_reported_as_a_warning_not_a_restart(self):
        result = self.m.run_bounded(
            self.activation(ticks=1), monitor_entry=lambda: {"row": "ok"},
            legacy_read=lambda: True, next_write=lambda: False,
            clock=lambda: 0.0,
            cursor_age_seconds=99999)
        self.assertTrue(result["health"]["stale"])
        self.assertIn("STALE_MONITOR", [w["code"] for w in result["warnings"]])
        self.assertNotIn("restart", json.dumps(result).lower(),
                         "the activation may report a stall; it may never restart")

    def test_backlog_and_unknown_receipts_are_counted(self):
        result = self.m.run_bounded(
            self.activation(ticks=1),
            monitor_entry=lambda: {"backlog": 7, "unknown_receipts": 2},
            legacy_read=lambda: True, next_write=lambda: False, clock=lambda: 0.0)
        self.assertEqual(result["health"]["backlog"], 7)
        self.assertEqual(result["health"]["unknown_receipts"], 2)


class NoServiceIsEverTouched(Base):
    def test_the_module_imports_nothing_that_can_start_or_stop_a_service(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("import systemctl", "systemctl ", "subprocess",
                          "os.system", "os.kill", "signal.pthread_kill"):
            self.assertNotIn(forbidden, source,
                             f"activation must not be able to {forbidden!r}")


class TheProcessEntryReportsItsOwnVerdict(Base):
    """The CLI is the thing a unit runs, so its exit code is the contract."""

    def _argv(self, **overrides):
        values = dict(legacy_root=LEGACY_ROOT, next_root=NEXT_ROOT,
                      state=NEXT_ROOT + "/state", lock=NEXT_ROOT + "/monitor.lock",
                      port=str(NEXT_PORT), legacy_port=str(LEGACY_PORT),
                      service_name="serveros-next-agent-monitor",
                      legacy_service_name="serveros-fleet-node-connector",
                      plugin_id="serveros-fleet-next-ingress",
                      legacy_plugin_id="serveros-fleet-ingress", ticks="1",
                      monitor_entry="does.not.exist:monitor",
                      legacy_read=MODULE_NAME + ":probe_true",
                      next_write=MODULE_NAME + ":probe_false")
        values.update(overrides)
        argv = []
        for key, value in values.items():
            argv.extend(["--" + key.replace("_", "-"), str(value)])
        return argv

    def test_a_missing_upstream_entry_exits_three(self):
        self.assertEqual(self.m.main(self._argv()), self.m.EXIT_BLOCKED_UPSTREAM)

    def test_a_misconfiguration_exits_two(self):
        self.assertEqual(self.m.main(self._argv(port=str(LEGACY_PORT))),
                         self.m.EXIT_REFUSED)

    def test_the_probes_resolve_through_the_same_loader(self):
        """`module:callable` for the probes too, so a unit passes strings."""
        self.assertTrue(callable(self.m.resolve_entry(MODULE_NAME + ":probe_true")))

    def test_a_bounded_run_through_the_cli_exits_zero(self):
        import types
        calls = []
        module = types.ModuleType(MODULE_NAME + "_probes")
        module.tick = lambda: calls.append(1) or {"state": "SETTLED", "backlog": 0}
        module.probe_true = lambda: True
        module.probe_false = lambda: False
        sys.modules[module.__name__] = module
        try:
            code = self.m.main(self._argv(
                monitor_entry=module.__name__ + ":tick",
                legacy_read=module.__name__ + ":probe_true",
                next_write=module.__name__ + ":probe_false"))
        finally:
            sys.modules.pop(module.__name__, None)
        self.assertEqual(code, self.m.EXIT_OK)
        self.assertEqual(len(calls), 1)


class TheUnitIsATemplateAndNamesNoLegacyResource(Base):
    def setUp(self):
        super().setUp()
        if not TEMPLATE.is_file():
            self.skipTest("the unit template is not in this tree")
        self.text = TEMPLATE.read_text(encoding="utf-8")

    def test_the_template_is_not_installable_as_committed(self):
        self.assertIn("TEMPLATE", self.text.upper(),
                      "a template that does not say so is a unit file someone "
                      "will copy next to the Legacy one")

    def test_the_template_names_no_legacy_service_port_or_plugin(self):
        for forbidden in ("serveros-fleet-node-connector", "3231",
                          "serveros-fleet-ingress", "serveros-fleet-owned"):
            self.assertNotIn(forbidden, self.text,
                             f"the Next unit must not name the Legacy {forbidden!r}")

    def test_every_path_and_name_is_a_placeholder(self):
        self.assertIn("@NEXT_", self.text)
        for literal in ("/home/fjd", "/srv/"):
            self.assertNotIn(literal, self.text,
                             "a unit template with a real path is a unit file")

    def test_the_entry_point_is_not_asserted_to_exist(self):
        """No ExecStart that pretends the upstream CLI is there."""
        for line in self.text.splitlines():
            if line.strip().startswith("ExecStart="):
                self.assertIn("@", line,
                              "ExecStart must be parameterised, not typed")
                self.assertNotIn("milestone-batch", line)


if __name__ == "__main__":
    unittest.main()
