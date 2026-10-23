"""The CloudCLI release plan: who it targets, what it claims, and that it stops.

Hermetic where it can be and read-only everywhere else. The plan reads the real
source tree (that is the thing under test) and the real registry, but nothing
here writes, installs, restarts, or repoints `ingress/current`; the ingress
directory the plan reports on is left exactly as it was found.

The defect these tests exist for is not a crash. The rollout named
`device:personal-dell` — a machine the registry declares `ownership: personal` —
and omitted `device:work-fj`, the company Windows workstation the mirror is
actually for. Nothing failed; the plan printed a device list and an operator
would have connected a company connector on a personal machine. A test that only
asserted "the plan returned" would have passed throughout, so what is asserted
here is the SELECTION RULE and the exact set it produces.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import scopes, tools                      # noqa: E402
from fleet_mcp.deploy import release_plan as rp          # noqa: E402

#: The three company-owned devices, by the refs the registry gives them.
COMPANY = ["device:llm-lab-dell", "device:tokyo-shared-dev-01", "device:work-fj"]
PERSONAL = "device:personal-dell"


class _Device:
    """Only what `company_devices()` reads: the ref and the ownership."""

    def __init__(self, ref: str, ownership: str):
        self.device_ref = ref
        self.ownership = ownership


class _Registry:
    def __init__(self, devices):
        self._devices = devices

    def all(self):
        return list(self._devices)


def _with_registry(devices):
    return mock.patch("fleet_mcp.registry.load_registry",
                      return_value=_Registry(devices))


class CompanyDeviceSelectionTest(unittest.TestCase):
    """(1) The rollout targets the company's machines and only those."""

    def test_the_rollout_targets_exactly_the_three_company_devices(self):
        self.assertEqual(rp.company_devices(), COMPANY)

    def test_a_personal_device_cannot_enter_the_rollout(self):
        """The rule, not the list: a `personal` device is never selected even
        when it is the only other device in the registry. This is the assertion
        the shipped defect would have failed."""
        devices = [_Device(PERSONAL, "personal"),
                   _Device("device:work-fj", "company")]
        with _with_registry(devices):
            self.assertEqual(rp.company_devices(), ["device:work-fj"])
        with _with_registry(devices):
            self.assertNotIn(PERSONAL, rp.plan()["rollout"][1]["devices"])

    def test_a_registry_with_no_company_device_refuses_rather_than_targeting_nothing(self):
        """An empty device list is not a plan — it is a rollout that looks
        finished and connected nothing."""
        with _with_registry([_Device(PERSONAL, "personal")]):
            with self.assertRaises(ValueError) as caught:
                rp.company_devices()
        self.assertIn("ownership: company", str(caught.exception))

    def test_every_targeted_device_is_one_the_registry_owns(self):
        """The set is derived, so this holds by construction; asserted anyway so
        a future hand-written list fails here rather than on a workstation."""
        from fleet_mcp.registry import load_registry
        owned = {str(d.device_ref): d.ownership for d in load_registry().all()}
        for ref in rp.plan()["rollout"][1]["devices"]:
            self.assertEqual(owned.get(ref), "company", ref)

    def test_the_device_list_is_sorted_and_stable(self):
        self.assertEqual(rp.company_devices(), sorted(rp.company_devices()))


class PlanClaimsTest(unittest.TestCase):
    """(2) What the plan claims matches what the surface serves."""

    def test_the_gate_count_is_the_running_served_tools(self):
        gates = rp.plan()["rollout"][0]["gates"]
        wanted = f"served_tools == {len(tools.SERVED_TOOLS)}"
        self.assertIn(wanted, gates)
        # ...and the number in that string is the runtime one, not a copy.
        embedded = int(wanted.rsplit(" ", 1)[1])
        self.assertEqual(embedded, len(tools.SERVED_TOOLS))

    def test_the_gate_count_is_not_the_stale_thirty_one(self):
        """`31` was 9 + 22 and left out the two tools that describe the mirror."""
        self.assertEqual(len(tools.SERVED_TOOLS), 44)
        self.assertNotIn("served_tools == 31", rp.plan()["rollout"][0]["gates"])

    def test_the_scope_gate_is_the_running_scope_count(self):
        self.assertIn(f"scopes_supported == {len(scopes.IMPLEMENTED_SCOPES)}",
                      rp.plan()["rollout"][0]["gates"])

    def test_the_contract_served_total_matches_the_running_surface(self):
        """The contract is a copy of `SERVED_TOOLS`; this is the test that stops
        the copy drifting, which is how it came to say 31."""
        import yaml
        contract = yaml.safe_load(
            (ROOT / "fleet" / "fleet-mcp-contract.yaml").read_text(encoding="utf-8"))
        self.assertEqual(contract["native_mirror"]["served_total"],
                         len(tools.SERVED_TOOLS))

    def test_the_contract_mirror_count_matches_the_recorded_set(self):
        import yaml
        contract = yaml.safe_load(
            (ROOT / "fleet" / "fleet-mcp-contract.yaml").read_text(encoding="utf-8"))
        self.assertEqual(contract["native_mirror"]["count"],
                         len(tools.SERVED_TOOLS) - len(tools.TOOLS) - 2)


class DryRunTest(unittest.TestCase):
    """(3) The plan plans. It does not perform."""

    def test_the_plan_is_dry_run_and_carries_no_execute_key(self):
        document = rp.plan()
        self.assertEqual(document["mode"], "DRY_RUN")
        self.assertNotIn("execute", document)

    def test_the_planner_never_invokes_the_installer(self):
        """`--execute` is inert: it annotates and refuses, and the module holds
        no reference to the installer it tells an operator to run."""
        text = (ROOT / "fleet_mcp" / "deploy" / "release_plan.py").read_text(encoding="utf-8")
        self.assertNotIn("import install", text)
        self.assertNotIn("from .install", text)
        self.assertNotIn("subprocess", text)

    def test_the_plan_reports_but_does_not_move_the_ingress(self):
        before = rp.installed_revision()["current"]
        rp.plan()
        self.assertEqual(rp.installed_revision()["current"], before)


class RollbackTest(unittest.TestCase):
    """(4) Every rollout step has a reversal, and the ingress one is the 9-tool
    surface coming back."""

    def test_every_rollout_step_has_a_rollback_step(self):
        document = rp.plan()
        self.assertEqual(len(document["rollback"]), len(document["rollout"]))
        for step in document["rollout"]:
            self.assertIn("reversible_by", step)
            self.assertTrue(step["reversible_by"])

    def test_the_ingress_rollback_restores_the_previous_surface(self):
        rollback = rp.plan()["rollback"][0]
        self.assertIn("repoint", rollback["action"])
        self.assertIn("9-tool surface", rollback["effect"])
        self.assertIn("current", rollback["action"])

    def test_the_connector_rollback_is_a_no_op_by_design(self):
        self.assertIn("backward-compatible", rp.plan()["rollback"][1]["action"])


class ProductionFixTest(unittest.TestCase):
    """(5) The CSP / RFC 9207 note follows the measurement, so an operator is
    told to rebase only when there is something to rebase."""

    def test_the_tree_carries_the_production_fix_and_says_so(self):
        delta = rp.source_delta()
        self.assertEqual(delta["source_missing_production_fix"], [])
        self.assertFalse(delta["reconcile_before_deploy"])
        self.assertNotIn("REBASE OR MERGE", delta["note"])
        self.assertIn("does not regress", delta["note"])

    def test_a_missing_marker_reverses_both_the_flag_and_the_note(self):
        """The note is derived, not a fixed paragraph. With a marker absent the
        same code must produce the warning — otherwise the sentence that matters
        is one an operator has learned to skip."""
        with mock.patch.dict(rp.PRODUCTION_FIX_MARKERS,
                             {"fleet_mcp/errors.py": "a_marker_that_is_not_there"}):
            delta = rp.source_delta()
        self.assertEqual(delta["source_missing_production_fix"], ["fleet_mcp/errors.py"])
        self.assertTrue(delta["reconcile_before_deploy"])
        self.assertIn("REBASE OR MERGE", delta["note"])

    def test_the_marker_files_still_carry_their_markers(self):
        for rel, marker in rp.PRODUCTION_FIX_MARKERS.items():
            with self.subTest(rel=rel):
                self.assertIn(marker, (ROOT / rel).read_text(encoding="utf-8"))


class ScriptTest(unittest.TestCase):
    """(6) It is run as a script, so it has to survive being one.

    The derived device list is imported from inside `fleet_mcp`, and a relative
    import there raises `attempted relative import with no known parent package`
    for exactly the invocation an operator uses. This is that regression.
    """

    def _run(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "fleet_mcp" / "deploy"
                                                   / "release_plan.py"), *args],
                              capture_output=True, text=True, timeout=120, cwd=str(ROOT))

    def test_it_runs_as_a_script_and_stays_dry(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr[-600:])
        self.assertIn("DRY_RUN", proc.stdout)

    def test_the_printed_parts_add_up_to_the_served_count(self):
        """The human line used to name two of three parts, so it read
        `33 tools (9 hand-written + 22 mirrored)` — an equation that is false."""
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr[-600:])
        line = [l for l in proc.stdout.splitlines() if l.startswith("source serves:")][0]
        head, _, tail = line.partition(" (")
        self.assertEqual(int(head.split()[2]), len(tools.SERVED_TOOLS))
        parts = [int(p.split()[0]) for p in tail.rstrip(")").split("+")]
        self.assertEqual(sum(parts), len(tools.SERVED_TOOLS),
                         f"the named parts {parts} do not sum to the total: {line}")

    def test_it_prints_the_derived_rollout_targets(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr[-600:])
        self.assertIn("rollout targets:", proc.stdout)
        for ref in COMPANY:
            self.assertIn(ref, proc.stdout)
        self.assertNotIn(PERSONAL, proc.stdout)

    def test_json_mode_is_the_plan_and_nothing_else(self):
        proc = self._run("--json")
        self.assertEqual(proc.returncode, 0, proc.stderr[-600:])
        document = json.loads(proc.stdout)
        self.assertEqual(document["mode"], "DRY_RUN")
        self.assertEqual(document["rollout"][1]["devices"], COMPANY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
