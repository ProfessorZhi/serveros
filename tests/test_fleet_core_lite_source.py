"""Admitting a Core-Lite reading into Fleet — and every reason not to.

`fleet_mcp/core_lite_source.py` decides one question: may a document that came
back be attributed to *this* device? It answers from the provider route and the
binding, and from nothing the document says about itself.

The sentence the tests below exist to keep false:

    *"the provider's transport answered, so the device's Core must be up."*

A reachable provider means a TCP connection succeeded. It says nothing about
whether a Core-Lite document arrived, or which device that document describes.
So `route.authenticated` is an explicit field a source must set, and the tests
here prove the module never derives it — least of all from a health probe.

Offline and pure: no socket, no host, no other device. The sources are fakes and
the clock is an argument.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import core_lite_source as src  # noqa: E402
from fleet_mcp import tools as fleet_tools  # noqa: E402
from fleet_mcp.config import (AuditBinding, AuthBinding, FleetConfig,  # noqa: E402
                              PolicyBinding, ProviderBinding, ServerBinding)
from fleet_mcp.errors import FleetRefusal  # noqa: E402
from fleet_mcp.fleet import FleetService, Principal  # noqa: E402
from fleet_mcp.providers.base import ProviderHealth  # noqa: E402
from fleet_mcp.version import TOOL_SURFACE_REVISION  # noqa: E402

NOW = dt.datetime(2026, 10, 16, 0, 30, 0, tzinfo=dt.timezone.utc)
DEVICE = "device:tokyo-shared-dev-01"
OTHER = "device:llm-lab-dell"
ACCOUNT = "vince"


def stamp(offset: float = -2.0) -> str:
    return (NOW + dt.timedelta(seconds=offset)).isoformat()


def document(**over) -> dict:
    base = {
        "schema": src.readback.SOURCE_SCHEMA,
        "node": DEVICE, "principal": ACCOUNT, "platform": "linux",
        "generation": 1, "mode": "observe-only", "observed_at": stamp(),
        "freshness": "NOT_ASSERTED",
        "principal_identity": {"state": "MATCHED", "basis": "ATTESTED",
                               "claimed": ACCOUNT, "observed": ACCOUNT},
        "device_identity": {"state": "UNKNOWN", "basis": "NONE"},
        "live": False,
        "observations": [
            {"name": "cpu", "state": "PRESENT", "completeness": "COMPLETE",
             "value": {"used_percent": 6.5}},
            {"name": "swap", "state": "PRESENT", "completeness": "COMPLETE",
             "value": {"total_bytes": 100, "free_bytes": 40, "used_bytes": 60,
                       "pages_in_per_second": 0.0, "pages_out_per_second": 0.0}},
        ],
    }
    base.update(over)
    return base


class FakeSource:
    """A provider-side reader that answers exactly what the test needs."""

    def __init__(self, *, document_value="auto", authenticated: bool = True,
                 route_device: str = DEVICE, raises: Exception | None = None,
                 route: object = "auto") -> None:
        self.document_value = document() if document_value == "auto" else document_value
        self.authenticated = authenticated
        self.route_device = route_device
        self.raises = raises
        self._route = route
        self.calls: list[str] = []

    def fetch(self, device_ref: str) -> src.CoreLiteFetch:
        self.calls.append(device_ref)
        if self.raises is not None:
            raise self.raises
        if self._route != "auto":
            return src.CoreLiteFetch(document=self.document_value, route=self._route)
        return src.CoreLiteFetch(
            document=self.document_value,
            route=src.CoreLiteRoute(device_ref=self.route_device,
                                    authenticated=self.authenticated,
                                    detail="fake provider route"))


def admit(source, *, device: str = DEVICE, account: str | None = ACCOUNT,
          received_at: dt.datetime = NOW) -> dict:
    return src.admit_core_lite(source, device_ref=device, binding_account=account,
                               received_at=received_at)


class NoSourceMeansNoReadingTest(unittest.TestCase):
    def test_an_unwired_surface_reports_not_connected(self):
        summary = admit(None)
        self.assertEqual(summary["connected"], "UNKNOWN")
        self.assertEqual(summary["route"]["state"], "NOT_CONFIGURED")
        self.assertFalse(summary["route"]["authenticated"])
        self.assertEqual(summary["observations"], {})
        self.assertFalse(summary["live"]["accepted"])

    def test_no_source_is_never_rendered_as_a_zero_or_a_healthy_device(self):
        summary = admit(None)
        rendered = json.dumps(summary)
        self.assertNotIn('"healthy"', rendered)
        self.assertEqual(summary["observations_unavailable"], [])
        self.assertIsNotNone(summary["route"]["reason"])

    def test_a_source_that_raises_is_a_fleet_fault_not_a_device_fault(self):
        """The device is not reported down because our own reader broke."""
        summary = admit(FakeSource(raises=RuntimeError("boom")))
        self.assertEqual(summary["route"]["state"], "SOURCE_FAILED")
        self.assertEqual(summary["connected"], "UNKNOWN")
        self.assertEqual(summary["route"]["failure"], "RuntimeError")
        self.assertNotEqual(summary["connected"], "NOT_CONNECTED")


class TheRouteDecidesNotTheDocumentTest(unittest.TestCase):
    def test_an_unauthenticated_route_is_refused(self):
        summary = admit(FakeSource(authenticated=False))
        self.assertEqual(summary["route"]["state"], "UNAUTHENTICATED")
        self.assertEqual(summary["connected"], "NOT_CONNECTED")
        self.assertEqual(summary["observations"], {})

    def test_a_route_reaching_another_device_is_refused(self):
        summary = admit(FakeSource(route_device=OTHER))
        self.assertEqual(summary["route"]["state"], "DEVICE_MISMATCH")
        self.assertEqual(summary["connected"], "NOT_CONNECTED")
        self.assertEqual(summary["device_ref"], DEVICE)

    def test_a_fetch_with_no_route_at_all_is_refused(self):
        """A document without a route has not answered the attribution question."""
        summary = admit(FakeSource(route=None))
        self.assertEqual(summary["route"]["state"], "UNAUTHENTICATED")
        self.assertEqual(summary["connected"], "NOT_CONNECTED")

    def test_an_authenticated_route_with_no_document_is_refused(self):
        summary = admit(FakeSource(document_value=None))
        self.assertEqual(summary["route"]["state"], "NO_DOCUMENT")
        self.assertEqual(summary["connected"], "UNKNOWN")
        self.assertEqual(summary["observations"], {})

    def test_a_verified_route_admits_the_reading(self):
        summary = admit(FakeSource())
        self.assertEqual(summary["route"]["state"], "VERIFIED")
        self.assertTrue(summary["route"]["authenticated"])
        self.assertEqual(summary["connected"], "CONNECTED")
        self.assertEqual(summary["observations"]["cpu"]["value"],
                         {"used_percent": 6.5})

    def test_a_verified_route_does_not_upgrade_the_sources_own_live_false(self):
        summary = admit(FakeSource())
        self.assertEqual(summary["connected"], "CONNECTED")
        self.assertFalse(summary["live"]["claimed"])
        self.assertFalse(summary["live"]["accepted"])

    def test_the_document_may_not_name_another_device(self):
        summary = admit(FakeSource(document_value=document(node=OTHER)))
        self.assertEqual(summary["route"]["state"], "VERIFIED")
        self.assertEqual(summary["device"]["state"], "MISMATCH")
        self.assertEqual(summary["connected"], "NOT_CONNECTED")

    def test_the_document_may_not_name_another_principal(self):
        summary = admit(FakeSource(document_value=document(principal="fjd")))
        self.assertEqual(summary["principal"]["state"], "MISMATCH")
        self.assertEqual(summary["connected"], "NOT_CONNECTED")

    def test_an_unbound_binding_account_is_unknown_and_never_matched(self):
        summary = admit(FakeSource(), account=None)
        self.assertEqual(summary["principal"]["state"], "UNKNOWN")
        self.assertEqual(summary["connected"], "UNKNOWN")
        self.assertNotEqual(summary["connected"], "CONNECTED")

    def test_a_spoofed_device_name_is_not_echoed(self):
        summary = admit(FakeSource(document_value=document(node="canary-DEADBEEF")))
        self.assertNotIn("canary-DEADBEEF", json.dumps(summary))
        self.assertIsNone(summary["device"]["declared"])


class FreshnessAndValuesStillApplyTest(unittest.TestCase):
    def test_a_stale_document_is_refused(self):
        summary = admit(FakeSource(document_value=document(observed_at=stamp(-3600))))
        self.assertEqual(summary["freshness"]["state"], "STALE")
        self.assertEqual(summary["connected"], "UNKNOWN")

    def test_a_partial_reading_still_reaches_the_summary(self):
        """PARTIAL is a legitimate producer state, not a refusal."""
        summary = admit(FakeSource(document_value=document(observations=[
            {"name": "psi.io", "state": "PRESENT", "completeness": "PARTIAL",
             "value": {"some": {"avg10": 0.61, "avg60": 0.22, "avg300": 0.67},
                       "full": {"state": "UNAVAILABLE", "reason": "NO_SOURCE"}}}])))
        row = summary["observations"]["psi.io"]
        self.assertEqual(row["state"], "PRESENT")
        self.assertEqual(row["completeness"], "PARTIAL")
        self.assertEqual(row["value"]["full"],
                         {"state": "UNAVAILABLE", "reason": "NO_SOURCE"})

    def test_an_out_of_range_value_makes_the_reading_unavailable(self):
        summary = admit(FakeSource(document_value=document(observations=[
            {"name": "cpu", "state": "PRESENT", "completeness": "COMPLETE",
             "value": {"used_percent": 340}}])))
        self.assertEqual(summary["observations"]["cpu"]["state"], "UNAVAILABLE")
        self.assertEqual(summary["observations"]["cpu"]["reason"], "NO_SOURCE")

    def test_a_foreign_schema_is_not_connected_through_a_verified_route(self):
        """The route is real and the payload is still not this reader's."""
        summary = admit(FakeSource(document_value=document(schema="other/v9")))
        self.assertEqual(summary["route"]["state"], "VERIFIED")
        self.assertEqual(summary["connected"], "NOT_CONNECTED")
        self.assertFalse(summary["live"]["accepted"])

    def test_the_summary_never_carries_a_path_or_a_token(self):
        summary = admit(FakeSource(document_value=document(
            aws_secret="ccp_deadbeef", config_path="/home/vince/.secrets/owner-pat")))
        rendered = json.dumps(summary)
        for leaked in ("ccp_deadbeef", "owner-pat", ".secrets"):
            with self.subTest(leaked=leaked):
                self.assertNotIn(leaked, rendered)


class _EmptyRegistry:
    def all(self):
        return []


def _config(tmp: Path, *, account: str | None = ACCOUNT) -> FleetConfig:
    anchor = tmp / "pat"
    anchor.write_text("owner-token-value", encoding="utf-8")
    return FleetConfig(
        source=None, local_device_ref=DEVICE,
        providers={DEVICE: ProviderBinding(
            device_ref=DEVICE, kind="cloudcli-mcp-http",
            endpoint="http://127.0.0.1:3011/mcp", credential_anchor=anchor,
            account=account)},
        auth=AuthBinding(pat_anchor=anchor),
        server=ServerBinding(bind="127.0.0.1", port=0),
        policy=PolicyBinding(),
        audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=10_000),
    )


class ReachabilityIsNotCoreConnectivityTest(unittest.TestCase):
    """The one sentence this whole module exists to keep false."""

    class ReachableProvider:
        device_ref = DEVICE
        kind = "cloudcli-mcp-http"

        def health(self) -> ProviderHealth:
            return ProviderHealth(device_ref=DEVICE, kind=self.kind,
                                  reachable=True, authorized=True,
                                  detail="answered")

    def _service(self, tmp, *, source=None):
        return FleetService(_config(tmp), registry=_EmptyRegistry(),
                            providers={DEVICE: self.ReachableProvider()},
                            core_lite_source=source)

    def test_a_healthy_provider_does_not_connect_a_core(self):
        with tempfile.TemporaryDirectory() as d:
            service = self._service(Path(d))
            principal = Principal(name="t", scopes=frozenset({"fleet:read"}),
                                  credential_kind="pat")
            # The provider's transport answers...
            self.assertTrue(service.providers[DEVICE].health().reachable)
            # ...and the Core is still not reported as connected.
            summary = service.core_lite_status(principal, DEVICE, received_at=NOW)
            self.assertEqual(summary["connected"], "UNKNOWN")
            self.assertEqual(summary["route"]["state"], "NOT_CONFIGURED")
            self.assertEqual(summary["observations"], {})

    def test_health_reachable_is_not_used_as_route_evidence(self):
        """Even with a source present, the ROUTE decides — not the health probe."""
        with tempfile.TemporaryDirectory() as d:
            service = self._service(Path(d), source=FakeSource(authenticated=False))
            principal = Principal(name="t", scopes=frozenset({"fleet:read"}),
                                  credential_kind="pat")
            self.assertTrue(service.providers[DEVICE].health().reachable)
            summary = service.core_lite_status(principal, DEVICE, received_at=NOW)
            self.assertEqual(summary["route"]["state"], "UNAUTHENTICATED")
            self.assertEqual(summary["connected"], "NOT_CONNECTED")


class FleetSurfaceIsUnchangedTest(unittest.TestCase):
    def test_no_tool_was_added_and_no_scope_changed(self):
        self.assertEqual(TOOL_SURFACE_REVISION, "fleet-mcp-3")
        self.assertEqual(len(fleet_tools.TOOLS), 9)
        self.assertEqual(len(fleet_tools.NATIVE_MIRROR_TOOLS), 22)
        self.assertEqual(len(fleet_tools.SERVED_TOOLS), 33)
        self.assertNotIn("core_lite_status", [t.name for t in fleet_tools.SERVED_TOOLS])

    def test_the_new_reader_is_not_reachable_as_a_tool(self):
        for name in src.__all__:
            with self.subTest(name=name):
                self.assertNotIn(name, [t.name for t in fleet_tools.SERVED_TOOLS])

    def test_list_devices_output_is_untouched_by_a_configured_source(self):
        """V1-not-connected regression: the served shape does not move.

        A source that WOULD return a full reading is injected, and the device
        listing is compared against the same listing with no source at all. If
        the seam had leaked into the served tools, these would differ — and a
        client that cached `tools/list` would have been told nothing.
        """
        class Reachable:
            device_ref = DEVICE
            kind = "cloudcli-mcp-http"
            def health(self):
                return ProviderHealth(device_ref=DEVICE, kind=self.kind,
                                      reachable=True, authorized=True, detail="ok")

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            principal = Principal(name="t", scopes=frozenset({"fleet:read"}),
                                  credential_kind="pat")
            without = FleetService(_config(tmp), registry=_EmptyRegistry(),
                                   providers={DEVICE: Reachable()})
            with_source = FleetService(_config(tmp), registry=_EmptyRegistry(),
                                       providers={DEVICE: Reachable()},
                                       core_lite_source=FakeSource())
            a = json.dumps(without.list_devices(principal), sort_keys=True)
            b = json.dumps(with_source.list_devices(principal), sort_keys=True)
            self.assertEqual(a, b)
            # ...and the same for the surface's own status report.
            sa = json.dumps(without.status(principal), sort_keys=True)
            sb = json.dumps(with_source.status(principal), sort_keys=True)
            self.assertEqual(sa, sb)
            for blob in (a, b, sa, sb):
                self.assertNotIn("core_lite", blob)

    def test_the_read_tool_needs_the_read_scope(self):
        with tempfile.TemporaryDirectory() as d:
            service = FleetService(_config(Path(d)), registry=_EmptyRegistry(),
                                   providers={})
            naked = Principal(name="t", scopes=frozenset(), credential_kind="pat")
            with self.assertRaises(FleetRefusal):
                service.core_lite_status(naked, DEVICE, received_at=NOW)

    def test_the_binding_account_is_what_reaches_the_verdict(self):
        """Not a parameter, not the document — the binding's own field."""
        with tempfile.TemporaryDirectory() as d:
            service = FleetService(_config(Path(d), account="fjd"),
                                   registry=_EmptyRegistry(), providers={},
                                   core_lite_source=FakeSource())
            principal = Principal(name="t", scopes=frozenset({"fleet:read"}),
                                  credential_kind="pat")
            summary = service.core_lite_status(principal, DEVICE, received_at=NOW)
            self.assertEqual(summary["principal"]["expected"], "fjd")
            self.assertEqual(summary["principal"]["state"], "MISMATCH")
            self.assertEqual(summary["connected"], "NOT_CONNECTED")



class ProviderDiagnosticsAreNeverPublishedTest(unittest.TestCase):
    """A distinct canary in the provider's own note, one per outcome.

    `CoreLiteRoute.detail` is where a provider puts a diagnostic for its own
    logs — an access-token fragment, a URL, a local path, an argv. An earlier
    revision published it verbatim in EVERY branch, which made the summary a
    second channel beside the document reader, with none of that reader's
    projection. These four tests are the ones that would have caught it.
    """

    CANARIES = {
        "VERIFIED": "canary-verified-11111111",
        "UNAUTHENTICATED": "canary-unauth-22222222",
        "DEVICE_MISMATCH": "canary-mismatch-33333333",
        "NO_DOCUMENT": "canary-nodoc-44444444",
    }

    def _summaries(self) -> dict:
        def route(outcome, **over):
            return src.CoreLiteRoute(
                device_ref=over.pop("device_ref", DEVICE),
                authenticated=over.pop("authenticated",
                                       outcome != "UNAUTHENTICATED"),
                detail=self.CANARIES[outcome])
        return {
            "VERIFIED": admit(FakeSource(route=route("VERIFIED"))),
            "UNAUTHENTICATED": admit(FakeSource(
                route=route("UNAUTHENTICATED", authenticated=False))),
            "DEVICE_MISMATCH": admit(FakeSource(
                route=route("DEVICE_MISMATCH", device_ref=OTHER))),
            "NO_DOCUMENT": admit(FakeSource(
                document_value=None, route=route("NO_DOCUMENT"))),
        }

    def test_each_outcome_is_actually_reached(self):
        """Both halves: the canary tests are worthless if a branch never runs."""
        got = {k: v["route"]["state"] for k, v in self._summaries().items()}
        self.assertEqual(got, {"VERIFIED": "VERIFIED",
                               "UNAUTHENTICATED": "UNAUTHENTICATED",
                               "DEVICE_MISMATCH": "DEVICE_MISMATCH",
                               "NO_DOCUMENT": "NO_DOCUMENT"})

    def test_not_one_canary_byte_is_emitted_in_any_outcome(self):
        summaries = self._summaries()
        for outcome, summary in summaries.items():
            text = json.dumps(summary, sort_keys=True)
            for other, canary in self.CANARIES.items():
                with self.subTest(outcome=outcome, canary=other):
                    self.assertNotIn(canary, text)

    def test_the_route_block_carries_no_provider_authored_string(self):
        """Every value in `route` is one this module wrote."""
        for outcome, summary in self._summaries().items():
            with self.subTest(outcome=outcome):
                self.assertEqual(sorted(summary["route"]),
                                 ["authenticated", "device_ref", "failure",
                                  "reason", "state"])
                self.assertNotIn("detail", summary["route"])
                self.assertTrue(isinstance(summary["route"]["authenticated"], bool))
                self.assertIn(summary["route"]["state"], src.ROUTE_STATES)
                if summary["route"]["reason"] is not None:
                    self.assertIn(summary["route"]["reason"],
                                  {v for v in _authored_reasons()})

    def test_a_subclass_cannot_smuggle_text_through_extra_attributes(self):
        """The summary is BUILT, not copied, so extra fields cannot ride along."""
        class Sneaky(src.CoreLiteRoute):
            pass
        route = Sneaky(device_ref=DEVICE, authenticated=True,
                       detail=self.CANARIES["VERIFIED"])
        object.__setattr__(route, "url", "https://host/?token=canary-extra-99999999")
        summary = admit(FakeSource(route=route))
        text = json.dumps(summary, sort_keys=True)
        self.assertNotIn("canary-extra-99999999", text)
        self.assertNotIn("https://host/", text)


def _authored_reasons() -> set:
    """Every refusal sentence this module can write, read off its source.

    Used to assert that whatever lands in `route.reason` is one of ours rather
    than something a provider chose.
    """
    import re
    text = (ROOT / "fleet_mcp" / "core_lite_source.py").read_text(encoding="utf-8")
    return set(re.findall(r'reason="([^"]+)"', text))


class TheRouteIsAnAdapterBoundaryTest(unittest.TestCase):
    def test_a_truthy_string_is_not_an_authenticated_route(self):
        """`authenticated="yes"` is a string its author chose, not a claim."""
        summary = admit(FakeSource(route=src.CoreLiteRoute(
            device_ref=DEVICE, authenticated="yes")))
        self.assertEqual(summary["route"]["state"], "UNAUTHENTICATED")
        self.assertEqual(summary["connected"], "NOT_CONNECTED")
        self.assertFalse(summary["route"]["authenticated"])

    def test_every_truthy_non_boolean_is_refused(self):
        for value in ("yes", "true", "1", 1, [1], {"a": 1}, object()):
            with self.subTest(value=repr(value)):
                summary = admit(FakeSource(route=src.CoreLiteRoute(
                    device_ref=DEVICE, authenticated=value)))
                self.assertEqual(summary["route"]["state"], "UNAUTHENTICATED")
                self.assertFalse(summary["route"]["authenticated"])

    def test_a_non_string_device_ref_is_refused(self):
        for value in (None, 42, ["device:x"], {"ref": "device:x"}):
            with self.subTest(value=repr(value)):
                summary = admit(FakeSource(route=src.CoreLiteRoute(
                    device_ref=value, authenticated=True)))
                self.assertEqual(summary["route"]["state"], "UNAUTHENTICATED")

    def test_the_exception_name_is_reported_only_from_a_closed_set(self):
        """A class is free to be called anything, including a URL."""
        weird = type("https://host/?token=canary-excname-55555555",
                     (Exception,), {})
        summary = admit(FakeSource(raises=weird("x")))
        self.assertEqual(summary["route"]["state"], "SOURCE_FAILED")
        self.assertEqual(summary["route"]["failure"], "Exception")
        self.assertNotIn("canary-excname-55555555", json.dumps(summary))

    def test_a_recognised_exception_name_is_still_reported(self):
        """The closed set keeps its diagnostic value for genuine builtins."""
        summary = admit(FakeSource(raises=TimeoutError("slow")))
        self.assertEqual(summary["route"]["failure"], "TimeoutError")
        self.assertIn("TimeoutError", src.SAFE_FAILURE_NAMES)


class TheSeamStaysNarrowTest(unittest.TestCase):
    def test_the_module_opens_no_socket_and_runs_no_command(self):
        """A source is handed to Fleet; this module does not reach anywhere."""
        path = ROOT / "fleet_mcp" / "core_lite_source.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        banned = {"socket", "subprocess", "urllib", "http", "requests", "os",
                  "shutil", "pathlib", "ssl", "asyncio"}
        self.assertEqual(imported & banned, set(),
                         f"the seam imported something that can leave: {imported & banned}")
        self.assertEqual(imported - {"__future__", "datetime", "dataclasses", "typing"},
                         set(), f"unexpected import: {imported}")
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        for forbidden in ("open", "exec", "eval", "__import__"):
            with self.subTest(call=forbidden):
                self.assertNotIn(forbidden, called)

    def test_every_route_state_is_a_distinct_answer(self):
        self.assertEqual(len(set(src.ROUTE_STATES)), len(src.ROUTE_STATES))
        self.assertIn("VERIFIED", src.ROUTE_STATES)
        self.assertEqual(src.ROUTE_STATES.count("VERIFIED"), 1)

    def test_every_refusal_carries_the_same_shape(self):
        """One shape for a caller to handle, whatever went wrong."""
        cases = {
            "NOT_CONFIGURED": admit(None),
            "SOURCE_FAILED": admit(FakeSource(raises=ValueError("x"))),
            "UNAUTHENTICATED": admit(FakeSource(authenticated=False)),
            "DEVICE_MISMATCH": admit(FakeSource(route_device=OTHER)),
            "NO_DOCUMENT": admit(FakeSource(document_value=None)),
        }
        for expected, summary in cases.items():
            with self.subTest(state=expected):
                self.assertEqual(summary["route"]["state"], expected)
                self.assertFalse(summary["route"]["authenticated"])
                self.assertIsNotNone(summary["route"]["reason"])
                self.assertEqual(summary["observations"], {})
                self.assertEqual(summary["observations_unavailable"], [])
                self.assertIn(summary["connected"], ("UNKNOWN", "NOT_CONNECTED"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
