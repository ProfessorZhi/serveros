"""`The device's own runtime` when the device has no base-tag binding.

A machine that serves one OS account now enrols ONE account-qualified binding
(`device:<id>|claude-<account>`) and no base-tag one. Two spellings of the same
device then disagree:

    the bindings the host holds : device:<id>|claude-vince.huang
    "this device's own runtime" : device:<id>            (the base tag)

Every runtime-implicit read on the served surface -- which runtime to list,
which provider to probe, whose binding carries the device-wide send grant --
used the second. On such a machine that names a binding nobody wrote, so an
external client saw a fleet where every account-qualified device was offline:
`DEVICE_UNOBSERVED`, `send_allowed_device: false`, and an empty session list
that a `runtime=all` call on the same device contradicted.

The rule under test, and its two edges:

* one binding, and it is not the base tag -> that binding is the device's own;
* several bindings, none of them the base tag -> no single own runtime exists,
  so nothing is picked silently and the caller still has to name one;
* no binding at all -> unchanged, still reported as the gap it is.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp.config import (AuditBinding, AuthBinding, FleetConfig,   # noqa: E402
                              PolicyBinding, ProviderBinding, ServerBinding,
                              provider_key)
from fleet_mcp.errors import UNKNOWN_DEVICE                              # noqa: E402
from fleet_mcp.fleet import SCOPE_READ, FleetService, Principal          # noqa: E402
from fleet_mcp.identity import SessionKey                                # noqa: E402
from fleet_mcp.providers.base import ProviderHealth, SessionSummary      # noqa: E402
from fleet_mcp.registry import load_registry                             # noqa: E402

DEVICE = "device:workspace"
ACCOUNT = "claude-vince.huang"
SECOND = "claude-fjd"
UUID = "3f0c2a54-1111-2222-3333-444455556666"
BASE = "claude"


class _Provider:
    """One runtime's route, answering with one session and a healthy probe."""

    kind = "fleet-node"

    def __init__(self, runtime: str):
        self.device_ref = DEVICE
        self.runtime = runtime
        self.calls: list = []

    def health(self):
        return ProviderHealth(device_ref=DEVICE, kind=self.kind, reachable=True,
                              authorized=True, detail="ok")

    def list_sessions(self, *, limit, project=None, state=None):
        self.calls.append(("list_sessions", limit))
        return [SessionSummary(
            key=SessionKey(DEVICE, self.runtime, UUID),
            title=self.runtime,
            classification="MANAGED_IDLE",
            running=False,
            resident_host=False,
            host_state=None,
            lifecycle_mode=None,
            project_id=None,
            last_activity={"iso": "2026-11-05T00:00:00Z"},
        )][:limit]

    def get_session(self, key):
        self.calls.append(("get_session", key.ref))
        return {"session_ref": key.ref, "served_by": self.runtime}

    def native_call(self, tool, arguments, *, session_ref=None):
        self.calls.append((tool, arguments))
        return {"ok": True}


class Workspace:
    """One enrolled device and exactly the runtimes the test gives it."""

    def __init__(self, tmp: Path, *, runtimes: tuple[str, ...], allow_send: bool = True):
        self.tmp = tmp
        registry_path = tmp / "registry.yaml"
        registry_path.write_text(
            "devices:\n"
            f"  - device_ref: {DEVICE}\n"
            "    display_name: workspace\n"
            "    platform: linux\n"
            "    environment_profile: linux-shared-multiuser\n"
            "    ownership: company\n",
            encoding="utf-8")

        providers = {}
        instances = {}
        for index, runtime in enumerate(runtimes):
            anchor = tmp / f"{index}.node-secret"
            anchor.write_text(f"secret-{index}", encoding="utf-8")
            providers[provider_key(DEVICE, runtime)] = ProviderBinding(
                device_ref=DEVICE, kind="fleet-node",
                endpoint=f"http://127.0.0.1:73{index:02d}", credential_anchor=anchor,
                runtime=runtime, allow_send=allow_send, allow_control=True)
            instances[provider_key(DEVICE, runtime)] = _Provider(runtime)

        self.config = FleetConfig(
            source=None, local_device_ref="device:elsewhere", providers=providers,
            auth=AuthBinding(pat_anchor=next(iter(providers.values())).credential_anchor
                             if providers else tmp / "owner-pat"),
            server=ServerBinding(bind="127.0.0.1", port=0),
            policy=PolicyBinding(operator_grants={}),
            audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        )
        self.service = FleetService(self.config, registry=load_registry(registry_path),
                                    providers=instances)

    def principal(self) -> Principal:
        return Principal(name="oauth:test", scopes=frozenset({SCOPE_READ}),
                         credential_kind="oauth")

    def device(self) -> dict:
        return self.service.list_devices(self.principal())["devices"][0]


class OneAccountQualifiedBindingIsTheDevicesOwnRuntime(unittest.TestCase):
    """The machine every external client was calling offline."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_own_runtime_is_the_binding_that_exists(self):
        w = Workspace(self.tmp, runtimes=(ACCOUNT,))
        self.assertEqual(w.service._own_runtime(DEVICE), ACCOUNT)

    def test_a_listing_that_names_no_runtime_answers_from_it(self):
        w = Workspace(self.tmp, runtimes=(ACCOUNT,))
        listing = w.service.list_sessions(w.principal(), device=DEVICE)
        self.assertEqual(listing["runtimes_queried"],
                         [provider_key(DEVICE, ACCOUNT)])
        self.assertEqual([r["runtime"] for r in listing["sessions"]], [ACCOUNT])
        self.assertEqual(listing["devices_unreachable"], [])

    def test_the_probe_reports_the_device_rather_than_a_missing_binding(self):
        w = Workspace(self.tmp, runtimes=(ACCOUNT,))
        device = w.device()
        self.assertEqual(device["probe"]["provider_present"], True)
        self.assertEqual(device["probe"]["reachable"], True)
        self.assertEqual(device["probe"]["authorized"], True)
        self.assertEqual(device["availability"]["local_agent_controller"], "AVAILABLE")
        self.assertNotEqual(device["availability"]["state"], "DEVICE_UNOBSERVED")

    def test_the_device_wide_send_grant_is_read_from_that_binding(self):
        """`allow_send: true` was reported as `false`, i.e. a grant withdrawn
        by a lookup rather than by an owner."""
        w = Workspace(self.tmp, runtimes=(ACCOUNT,), allow_send=True)
        self.assertTrue(w.device()["control"]["send_allowed_device"])

    def test_a_withdrawn_grant_is_still_reported_as_withdrawn(self):
        w = Workspace(self.tmp, runtimes=(ACCOUNT,), allow_send=False)
        self.assertFalse(w.device()["control"]["send_allowed_device"])


class ABaseBindingIsStillWhatAnUnnamedCallMeans(unittest.TestCase):
    """The machine that grew a second runtime keeps its old default."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_default_stays_the_base_runtime(self):
        w = Workspace(self.tmp, runtimes=(BASE, SECOND))
        self.assertEqual(w.service._own_runtime(DEVICE), BASE)
        listing = w.service.list_sessions(w.principal(), device=DEVICE)
        self.assertEqual([r["runtime"] for r in listing["sessions"]], [BASE])

    def test_the_second_runtime_is_still_reachable_by_name(self):
        w = Workspace(self.tmp, runtimes=(BASE, SECOND))
        listing = w.service.list_sessions(w.principal(), device=DEVICE, runtime=SECOND)
        self.assertEqual([r["runtime"] for r in listing["sessions"]], [SECOND])


class SeveralUnnamedRuntimesAreNotGuessed(unittest.TestCase):
    """No base binding and no single candidate: the caller still has to say."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_default_names_nothing_and_says_so(self):
        w = Workspace(self.tmp, runtimes=(ACCOUNT, SECOND))
        self.assertEqual(w.service._own_runtime(DEVICE), BASE)
        listing = w.service.list_sessions(w.principal(), device=DEVICE)
        self.assertEqual(listing["sessions"], [])
        self.assertEqual([u["code"] for u in listing["devices_unreachable"]],
                         [UNKNOWN_DEVICE])

    def test_naming_one_answers_from_it(self):
        w = Workspace(self.tmp, runtimes=(ACCOUNT, SECOND))
        listing = w.service.list_sessions(w.principal(), device=DEVICE, runtime=SECOND)
        self.assertEqual([r["runtime"] for r in listing["sessions"]], [SECOND])

    def test_all_asks_both(self):
        w = Workspace(self.tmp, runtimes=(ACCOUNT, SECOND))
        listing = w.service.list_sessions(w.principal(), device=DEVICE, runtime="all")
        self.assertEqual(sorted(r["runtime"] for r in listing["sessions"]),
                         sorted([ACCOUNT, SECOND]))


class ADefaultRuntimeMustNotBecomeAnOrdinaryUnnamedDevice(unittest.TestCase):
    """Somebody reading `base` gets the device's own runtime, not a claim that
    the base account is bound."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_device_with_no_bindings_is_unchanged(self):
        w = Workspace(self.tmp, runtimes=())
        self.assertEqual(w.service._own_runtime(DEVICE), BASE)
        self.assertIsNone(w.service._binding_for_device(DEVICE))
        device = w.device()
        self.assertEqual(device["availability"]["state"], "DEVICE_UNOBSERVED")
        self.assertFalse(device["control"]["send_allowed_device"])


if __name__ == "__main__":
    unittest.main()
