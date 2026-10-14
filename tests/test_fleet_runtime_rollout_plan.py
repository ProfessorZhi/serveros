"""The multi-runtime rollout plan, against a production-shaped fleet.

The fixture below is not a toy: it is the fleet the plan is FOR, with the same
shapes the shipped registry and the deployed binding use — five devices, three
of them `ownership: company`, one of those a Linux GPU box that serves **two**
runtimes because two OS accounts each run their own CloudCLI on it. Three
physical machines, four runtimes. Every gap kind is then produced by breaking
one thing at a time on top of that fleet, because a gap list that only ever
appears empty is a gap list nobody has tested.

What is asserted, in order of how much it would cost to get wrong:

1. The arithmetic. Three physical machines and four runtimes are different
   numbers, and a plan that reported either one alone would be read as the
   other.
2. The plan WRITES NOTHING. Measured, not asserted from the absence of a write
   call: every byte and mtime in the fixture directory is compared before and
   after.
3. No credential escapes. The fixture plants recognizable secrets in an
   endpoint's userinfo and in the anchor files, and the plan's own JSON is
   scanned for them.
4. The order. Host, then device per user, then the client re-read — with the
   device half attributed to the account that owns it and not to this host.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp.deploy import release_plan as rp                 # noqa: E402

DELL = "device:llm-lab-dell"
TOKYO = "device:tokyo-shared-dev-01"
WINDOWS = "device:work-fj"
PERSONAL = ["device:personal-dell", "device:server-beijing"]

SECOND = "claude-fjd"

#: A value that would be a credential if it were real, planted where a careless
#: plan would carry it: inside an endpoint's userinfo. The plan must report the
#: host and port and drop everything before the `@`.
PLANTED_ENDPOINT_SECRET = "fjd-node-secret-do-not-carry"
PLANTED_ANCHOR_SECRET = "s3cr3t-anchor-value-do-not-carry"


def _device(ref: str, name: str, platform: str, ownership: str) -> str:
    """One device entry in the shipped registry's own spelling."""
    return (
        f"  - device_ref: {ref}\n"
        f"    device_id: {ref.split(':', 1)[1]}\n"
        f"    display_name: {name}\n"
        f"    platform: {platform}\n"
        f"    os_family: {'windows' if platform == 'windows' else 'linux-ubuntu-26.04'}\n"
        f"    ownership: {ownership}\n"
        f"    roles: [development-worker]\n"
        f"    environment_profile: linux-gpu-compute\n"
        f"    agents: [claude-code]\n"
        f"    runtime_bindings:\n"
        f"      - runtime_ref: agent-runtime:claude-code\n"
        f"        declared_at: 2026-10-12\n"
        f"    legacy_ids: []\n"
        f"    enrollment: enrolled\n"
    )


def _registry_text(devices=None) -> str:
    rows = devices if devices is not None else [
        _device(TOKYO, "Tokyo-Shared-Dev-01", "linux", "company"),
        _device(WINDOWS, "FJPCOS", "windows", "company"),
        _device(DELL, "Dell-LLM-Lab", "linux", "company"),
        _device(PERSONAL[0], "DellPCOS", "windows", "personal"),
        _device(PERSONAL[1], "Beijing-Server", "linux", "personal"),
    ]
    return "schema: serveros/fleet-registry.v1\ndevices:\n" + "".join(rows)


#: The production fleet, as `(device_ref, runtime, port)`. The Dell appears
#: twice because two OS accounts on it each run their own CloudCLI; the other
#: two company machines appear once each. Three machines, four runtimes.
PRODUCTION_RUNTIMES = [
    (DELL, "claude", 7401),
    (DELL, SECOND, 7402),
    (TOKYO, "claude", 7403),
    (WINDOWS, "claude", 7404),
]


class Fleet:
    """A temp registry + binding pair shaped like the production one.

    An anchor is created per (device, runtime) unless a test says otherwise, so
    the default fleet holds the fourth isolation fact and only the tests ABOUT
    its absence have to arrange it.
    """

    def __init__(self, tmp: Path, *, runtimes=None, grants=None, shared_anchor=False,
                 endpoint_secret=False, disabled=(), missing_anchor=()):
        self.tmp = tmp
        self.registry = tmp / "registry.yaml"
        self.registry.write_text(_registry_text(), encoding="utf-8")
        self.config = tmp / "fleet-mcp.local.yaml"
        self.runtimes = list(PRODUCTION_RUNTIMES if runtimes is None else runtimes)

        # One anchor per runtime, its own file. Two runtimes collapsing onto one
        # anchor is the failure this plan can SEE without reading a value, and
        # `shared_anchor` collapses them deliberately.
        self.anchor_for: dict[tuple[str, str], Path] = {}
        providers = []
        for device_ref, runtime, port in self.runtimes:
            key = device_ref if runtime == "claude" else f"{device_ref}|{runtime}"
            device_id = device_ref.split(":", 1)[1]
            anchor = (tmp / f"{device_id}.node-secret" if shared_anchor and runtime == "claude"
                      else tmp / f"{device_id}-{runtime}.node-secret")
            anchor.write_text(f"{PLANTED_ANCHOR_SECRET}:{device_id}:{runtime}\n",
                              encoding="utf-8")
            if runtime in missing_anchor:
                anchor = tmp / "does-not-exist.node-secret"
            self.anchor_for[(device_ref, runtime)] = anchor
            endpoint = (f"http://fjd:{PLANTED_ENDPOINT_SECRET}@127.0.0.1:{port}"
                        if endpoint_secret else f"http://127.0.0.1:{port}")
            providers.append(
                f"  '{key}':\n"
                f"    kind: fleet-node\n"
                f"    runtime: {runtime}\n"
                f"    endpoint: {endpoint}\n"
                f"    credential_anchor: {anchor}\n"
                f"    enabled: {'false' if runtime in disabled else 'true'}\n"
                f"    allow_send: true\n"
                f"    allow_create: false\n"
                f"    allow_control: false\n"
                f"    note: fixture\n"
            )
        self.config.write_text(
            "schema: serveros/fleet-mcp.local/v1\n"
            f"device_ref: {TOKYO}\n"
            "providers:\n" + "".join(providers) +
            "policy:\n"
            "  send_allow: []\n"
            "  control_allow: []\n"
            + self._grants_yaml(grants),
            encoding="utf-8")

    def shared_second_anchor(self) -> None:
        """Make the Dell's second runtime sign with the first one's anchor.

        Isolating exactly one fact, so the test that asserts the sharing is
        reported is not also asserting five other things about the fixture.
        """
        base = self.anchor_for[(DELL, "claude")]
        text = self.config.read_text(encoding="utf-8").replace(
            str(self.anchor_for[(DELL, SECOND)]), str(base))
        self.config.write_text(text, encoding="utf-8")

    @staticmethod
    def _grants_yaml(grants) -> str:
        if not grants:
            return "  operator_grants: {}\n"
        lines = ["  operator_grants:"]
        for principal, per_device in grants.items():
            lines.append(f'    "{principal}":')
            for device_ref, verbs in per_device.items():
                lines.append(f"      '{device_ref}': [{', '.join(verbs)}]")
        return "\n".join(lines) + "\n"

    def survey(self) -> dict:
        return rp.runtime_bindings(self.registry, self.config)

    def snapshot(self) -> dict:
        """Every file the fixture owns, by name, size, mtime and bytes."""
        out = {}
        for path in sorted(self.tmp.iterdir()):
            if path.is_file():
                stat = path.stat()
                out[path.name] = (stat.st_size, stat.st_mtime_ns, path.read_bytes())
        return out


class TheFleetIsThreeMachinesAndFourRuntimesTest(unittest.TestCase):
    """The headline, and the two numbers in it that are not each other."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fleet = Fleet(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_three_physical_devices_and_four_runtimes(self):
        got = self.fleet.survey()
        self.assertEqual(got["state"], "REPORTED")
        self.assertEqual(got["physical_devices"], 3)
        self.assertEqual(got["runtimes"], 4)
        # The whole point: the two numbers differ, so neither may stand in for
        # the other.
        self.assertNotEqual(got["physical_devices"], got["runtimes"])

    def test_a_two_runtime_machine_is_one_machine_and_two_runtimes(self):
        got = self.fleet.survey()
        dell = next(d for d in got["devices"] if d["device_ref"] == DELL)
        self.assertEqual(dell["runtime_count"], 2)
        self.assertEqual([r["runtime"] for r in dell["runtimes"]], ["claude", SECOND])
        self.assertEqual([r["provider_key"] for r in dell["runtimes"]],
                         [DELL, f"{DELL}|{SECOND}"])

    def test_the_base_runtime_is_listed_first_and_keeps_the_bare_key(self):
        """The key a binding was already filed under. If the survey reported the
        base runtime as `device:llm-lab-dell|claude`, every grant written against
        the bare key would look unbound."""
        got = self.fleet.survey()
        dell = next(d for d in got["devices"] if d["device_ref"] == DELL)
        self.assertEqual(dell["runtimes"][0]["provider_key"], DELL)
        self.assertNotIn("|", dell["runtimes"][0]["provider_key"])

    def test_the_arithmetic_closes_over_every_device_in_the_registry(self):
        """In scope plus excluded is every device declared. A plan naming only
        the three would leave two machines unaccounted for."""
        got = self.fleet.survey()
        self.assertEqual(got["devices_in_scope"], 3)
        self.assertEqual(len(got["devices_excluded"]), 2)
        self.assertEqual(got["devices_seen_total"], 5)
        self.assertEqual(got["devices_in_scope"] + len(got["devices_excluded"]),
                         got["devices_seen_total"])
        self.assertEqual({d["device_ref"] for d in got["devices_excluded"]},
                         set(PERSONAL))

    def test_a_correctly_authored_fleet_reports_no_gaps(self):
        got = self.fleet.survey()
        self.assertEqual(got["gaps"], [])
        self.assertEqual(got["unresolved"], 0)

    def test_it_says_the_registry_is_not_where_the_second_runtime_is_declared(self):
        """A device record carries `agent-runtime:<family>` and nothing else, so
        two OS accounts on one machine are one declared family and two runtimes.
        The Dell's registry entry is therefore already correct, and a plan that
        waited for it to declare `claude-fjd` would wait forever."""
        got = self.fleet.survey()
        dell = next(d for d in got["devices"] if d["device_ref"] == DELL)
        self.assertEqual(dell["declared_runtimes"], ["agent-runtime:claude-code"])
        self.assertEqual(dell["runtime_count"], 2)


class EveryGapIsProducedByBreakingOneThingTest(unittest.TestCase):
    """One break at a time, on top of the fleet above. Each gap is a different
    remedy, so each is a different kind rather than one "incomplete" flag."""

    def _kinds(self, **broken):
        with tempfile.TemporaryDirectory() as d:
            return {g["kind"] for g in Fleet(Path(d), **broken).survey()["gaps"]}

    def test_no_binding_for_a_declared_device(self):
        """The state the fleet is in today for the Dell's second runtime before
        anyone ships it: declared, and unreachable from here."""
        with tempfile.TemporaryDirectory() as d:
            got = Fleet(Path(d), runtimes=[]).survey()
        self.assertEqual({g["kind"] for g in got["gaps"]}, {"NO_BINDING"})
        self.assertEqual({g["device_ref"] for g in got["gaps"]}, {DELL, TOKYO, WINDOWS})
        self.assertEqual(got["runtimes"], 0)
        self.assertEqual(got["physical_devices"], 3, "still three machines; none bound")

    def test_a_disabled_binding_is_named_as_disabled(self):
        self.assertIn("BINDING_DISABLED", self._kinds(disabled={SECOND}))

    def test_a_missing_credential_anchor_is_named_as_the_devices_work(self):
        with tempfile.TemporaryDirectory() as d:
            gaps = Fleet(Path(d), missing_anchor={SECOND}).survey()["gaps"]
        row = next(g for g in gaps if g["kind"] == "CREDENTIAL_ANCHOR_MISSING")
        self.assertEqual(row["runtime"], SECOND)
        self.assertIn("device-side owner", row["detail"])

    def test_two_runtimes_sharing_one_anchor_is_an_isolation_fact_that_is_wrong(self):
        """The fourth isolation fact, and the half of it this host CAN check:
        one anchor means one node secret means one signature for both. It is not
        a missing thing, it is a wrong thing, and it is reported as neither
        runtime's absence."""
        with tempfile.TemporaryDirectory() as d:
            fleet = Fleet(Path(d))
            self.assertEqual(fleet.survey()["gaps"], [], "the fixture starts clean")
            fleet.shared_second_anchor()
            gaps = fleet.survey()["gaps"]
        rows = [g for g in gaps if g["kind"] == "SHARED_CREDENTIAL_ANCHOR"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["device_ref"], DELL)
        self.assertIsNone(rows[0]["runtime"],
                          "a shared anchor belongs to neither runtime alone")
        self.assertIn("its own", rows[0]["detail"])

    def test_distinct_anchors_are_not_reported_as_shared(self):
        with tempfile.TemporaryDirectory() as d:
            gaps = Fleet(Path(d)).survey()["gaps"]
        self.assertEqual([g for g in gaps if g["kind"] == "SHARED_CREDENTIAL_ANCHOR"], [])

    def test_a_grant_for_a_runtime_no_binding_declares(self):
        with tempfile.TemporaryDirectory() as d:
            gaps = Fleet(Path(d), grants={
                "oauth:fleet-abc": {f"{DELL}|claude-kai": ["send"]}}).survey()["gaps"]
        row = next(g for g in gaps if g["kind"] == "GRANT_FOR_UNBOUND_RUNTIME")
        self.assertEqual(row["device_ref"], DELL)
        self.assertEqual(row["runtime"], "claude-kai")

    def test_a_grant_for_a_bound_runtime_is_not_a_gap(self):
        with tempfile.TemporaryDirectory() as d:
            gaps = Fleet(Path(d), grants={
                "oauth:fleet-abc": {f"{DELL}|{SECOND}": ["send"]}}).survey()["gaps"]
        self.assertEqual(gaps, [])

    def test_a_personal_device_a_binding_reaches_is_refused_by_name(self):
        """The registry rule the whole rollout is filtered by, applied to the
        bindings: reaching a personal machine is the defect, not a device the
        plan may quietly include. The binding is the thing that would have put a
        company connector on an operator's own laptop."""
        with tempfile.TemporaryDirectory() as d:
            fleet = Fleet(Path(d), runtimes=[(PERSONAL[0], "claude", 7405)])
            got = fleet.survey()
        row = next(g for g in got["gaps"] if g["kind"] == "PERSONAL_DEVICE_IN_SCOPE")
        self.assertEqual(row["device_ref"], PERSONAL[0])
        self.assertEqual(row["runtime"], "claude")
        personal = next(x for x in got["devices"] if x["device_ref"] == PERSONAL[0])
        self.assertEqual(personal["runtime_count"], 1)
        # ...and a personal machine is NOT in scope, even when it is bound, so
        # the headline never counts a machine the rollout will not touch.
        self.assertEqual(got["company_devices"], sorted([DELL, TOKYO, WINDOWS]))
        self.assertEqual(got["physical_devices"], 3)
        self.assertNotIn(PERSONAL[0], got["company_devices"])
        # Zero runtimes in scope, and that is the correct answer rather than an
        # empty one: the only binding in this fleet reaches a personal machine,
        # so the rollout has nothing to deploy. The three company machines are
        # still counted — they are what the rollout is FOR — and the binding
        # that reaches the laptop is reported as the gap it is.
        self.assertEqual(got["runtimes"], 0)
        self.assertEqual({g["kind"] for g in got["gaps"]},
                         {"PERSONAL_DEVICE_IN_SCOPE", "NO_BINDING"})


class ThePlanWritesNothingTest(unittest.TestCase):
    """A dry run is measured, not asserted."""

    def test_no_file_the_fixture_owns_changes(self):
        with tempfile.TemporaryDirectory() as d:
            fleet = Fleet(Path(d))
            before = fleet.snapshot()
            for _ in range(3):
                fleet.survey()
                rp.plan(fleet.registry, fleet.config)
            self.assertEqual(fleet.snapshot(), before)

    def test_the_survey_and_the_planner_both_say_writes_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            fleet = Fleet(Path(d))
            self.assertEqual(fleet.survey()["writes"], [])
            self.assertEqual(rp.plan(fleet.registry, fleet.config)
                             ["runtime_bindings"]["writes"], [])
            self.assertEqual(rp.plan(fleet.registry, fleet.config)["mode"], "DRY_RUN")

    def test_the_planner_still_holds_no_reference_to_the_installer(self):
        text = (ROOT / "fleet_mcp" / "deploy" / "release_plan.py").read_text(encoding="utf-8")
        for forbidden in ("subprocess", "import install", "from .install", "os.system"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)


class NoCredentialEscapesIntoThePlanTest(unittest.TestCase):
    """The plan is a document an operator reads and may paste into a ticket."""

    def test_an_endpoint_carrying_userinfo_is_reduced_to_host_and_port(self):
        with tempfile.TemporaryDirectory() as d:
            fleet = Fleet(Path(d), endpoint_secret=True)
            document = json.dumps(rp.plan(fleet.registry, fleet.config))
        self.assertNotIn(PLANTED_ENDPOINT_SECRET, document)
        dell = None
        with tempfile.TemporaryDirectory() as d:
            fleet = Fleet(Path(d), endpoint_secret=True)
            dell = next(x for x in fleet.survey()["devices"] if x["device_ref"] == DELL)
        self.assertEqual(dell["runtimes"][1]["endpoint"], "127.0.0.1:7402")

    def test_no_anchor_VALUE_is_ever_read_let_alone_printed(self):
        """The plan reports whether an anchor EXISTS. It never opens one, and
        this is the test that would fail the day somebody decided a survey
        should also report what is inside."""
        with tempfile.TemporaryDirectory() as d:
            fleet = Fleet(Path(d))
            document = json.dumps(rp.plan(fleet.registry, fleet.config))
        self.assertNotIn(PLANTED_ANCHOR_SECRET, document)
        self.assertIn("credential_anchor_present", document)

    def test_grant_principals_are_counted_and_never_named(self):
        """A principal name is a credential identifier (`oauth:<client_id>`),
        which is why `FleetConfig.describe` reports grants as their shape."""
        with tempfile.TemporaryDirectory() as d:
            fleet = Fleet(Path(d), grants={
                "oauth:fleet-SUPERSECRETID": {f"{DELL}|{SECOND}": ["send"]}})
            got = fleet.survey()
            document = json.dumps(rp.plan(fleet.registry, fleet.config))
        self.assertNotIn("fleet-SUPERSECRETID", document)
        row = got["grants"][f"{DELL}|{SECOND}"]
        self.assertEqual(row["principals"], 1)
        self.assertEqual(row["verbs"], ["send"])


class TheOrderIsHostThenDeviceThenClientTest(unittest.TestCase):
    """A host → B device (per user) → C client refresh. The order is stated
    because it is not arbitrary: B's connector verifies a request only A's v2
    signer can produce."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fleet = Fleet(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_three_stages_are_in_order(self):
        stages = [s["stage"] for s in self.fleet.survey()["rollout"]]
        self.assertEqual(stages, ["A", "B", "C"])

    def test_the_device_stage_is_per_runtime_and_names_the_second_account(self):
        rollout = self.fleet.survey()["rollout"]
        device_stage = next(s for s in rollout if s["stage"] == "B")
        self.assertEqual([(r["device_ref"], r["runtime"]) for r in device_stage["per_runtime"]],
                         [(DELL, SECOND)])

    def test_the_device_stage_belongs_to_the_other_account_and_not_to_this_host(self):
        device_stage = next(s for s in self.fleet.survey()["rollout"] if s["stage"] == "B")
        self.assertIn("does not create their account", device_stage["owner"])
        self.assertIn("copy a token", device_stage["owner"])

    def test_the_host_stage_says_a_v1_only_fleet_is_unaffected(self):
        host_stage = next(s for s in self.fleet.survey()["rollout"] if s["stage"] == "A")
        self.assertIn("v1", " ".join(host_stage["gates"]))

    def test_the_client_stage_adds_no_tool_and_no_scope(self):
        client_stage = next(s for s in self.fleet.survey()["rollout"] if s["stage"] == "C")
        gates = " ".join(client_stage["gates"])
        self.assertIn("unchanged", gates)
        self.assertIn("no scope", gates)

    def test_every_stage_has_a_rollback_and_an_owner(self):
        for stage in self.fleet.survey()["rollout"]:
            with self.subTest(stage=stage["stage"]):
                self.assertTrue(stage["reversible_by"])
                self.assertTrue(stage["owner"])

    def test_the_plan_reports_the_order_even_with_nothing_bound(self):
        """Before the host half ships there are no bindings, so there is no
        per-runtime device work — but the ORDER still has to be reported, or an
        operator with no config reads the missing list as a missing plan."""
        with tempfile.TemporaryDirectory() as d:
            fleet = Fleet(Path(d), runtimes=[])
            got = fleet.survey()
        self.assertEqual([s["stage"] for s in got["rollout"]], ["A", "B", "C"])
        self.assertEqual(got["runtimes"], 0)


class NoBindingIsUnavailableAndNotEmptyTest(unittest.TestCase):
    """"No runtimes anywhere" and "this host cannot see the bindings" are
    different facts, and the second read as the first would look like a fleet
    that is already done."""

    def test_a_config_that_is_not_there_is_reported_with_its_reason(self):
        with tempfile.TemporaryDirectory() as d:
            fleet = Fleet(Path(d))
            got = rp.runtime_bindings(fleet.registry, Path(d) / "absent.yaml")
        self.assertEqual(got["state"], "UNAVAILABLE")
        self.assertIn("absent.yaml", got["reason"])
        self.assertEqual(got["writes"], [])

    def test_unavailable_is_not_an_empty_survey(self):
        with tempfile.TemporaryDirectory() as d:
            fleet = Fleet(Path(d))
            got = rp.runtime_bindings(fleet.registry, Path(d) / "absent.yaml")
        self.assertNotIn("devices", got)
        self.assertNotEqual(got.get("runtimes"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
