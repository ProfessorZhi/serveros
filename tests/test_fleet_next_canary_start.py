"""`canary-start` — start the STAGED Next unit, and publish nothing at all.

Every test here is mock/offline. `systemctl`, the public TLS handshake and the
HTTP probes are all faked, and each test asserts which of them were driven:
a fake that was never consulted is the difference between "the guard passed"
and "the guard did not run", and only one of those is a guard.

The two facts this verb deliberately does NOT require — a real Next plugin id
and a completed Next OAuth client authorization — are the two facts that cannot
exist until the fixed Next unit has served a public origin at least once. The
tests that pin that exemption also pin that `install` still requires both, so
the exemption cannot quietly become the default.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from fleet_mcp.deploy import install  # noqa: E402
from fleet_mcp.deploy import next_install, next_dual_stack_preflight as gate  # noqa: E402

UNIT = gate.NEXT_UNIT
V1_UNIT = gate.V1_UNIT
FQDN = "next.example.invalid"
PUBLIC_MCP_URL = f"https://{FQDN}/mcp"


def candidate() -> dict:
    """A reviewed dual-stack proposal for the canary, with the two
    client-registration facts still unassigned — which is the whole point."""
    return {
        "schema": gate.SCHEMA,
        "v1": {
            "service_unit": V1_UNIT,
            "runtime_root": "/home/operator/runtime-assets/old-ingress/ingress",
            "config_path": "/home/operator/.config/old-fleet/config.yaml",
            "oauth_state_root": "/home/operator/.local/state/old-fleet-oauth",
            "local_listen": {"host": "127.0.0.1", "port": 8401},
            "public_mcp_url": "https://v1.example.invalid/mcp",
            "plugin_id": "plugin_asdk_app_existing_v1",
        },
        "next": {
            "service_unit": UNIT,
            "runtime_root": "/home/operator/runtime-assets/new-ingress/ingress-next",
            "config_path": "/home/operator/.config/new-fleet/config.yaml",
            "oauth_state_root": "/home/operator/.local/state/new-fleet-oauth",
            "local_listen": {"host": "127.0.0.1", "port": 8402},
            "public_mcp_url": PUBLIC_MCP_URL,
            "plugin_id": None,
        },
        "limits": dict(next_install.BUDGET),
        "attestations": {
            "v1_identity_observed": True,
            "next_owner_binding_reviewed": True,
            "next_oauth_client_authorized": False,
            "rollback_ready": True,
        },
    }


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


class FakeMod:
    """`install.py`'s surface as the canary path consults it, with a record."""

    EFFECT_MARKER = install.EFFECT_MARKER

    def __init__(self, *, active: str = "inactive", enabled: str = "disabled",
                 restarts: int = 0, limits_ok: bool = True, start_rc: int = 0,
                 stop_rc: int = 0, start_raises: Exception | None = None,
                 local_health: bool = True, v1_health: bool = True,
                 v1_health_after_start: bool | None = None,
                 v1_main_pid: int = 1111, next_main_pid: int | None = None):
        # A main process that really exists: the wrapper checks `/proc/<pid>`,
        # and a fake pid would make that check fail for the wrong reason.
        self.next_main_pid = os.getpid() if next_main_pid is None else next_main_pid
        self.active = active
        self.enabled = enabled
        self.restarts = restarts
        self.limits_ok = limits_ok
        self.start_rc = start_rc
        self.stop_rc = stop_rc
        self.start_raises = start_raises
        self.local_health = local_health
        self.v1_health = v1_health
        self.v1_health_after_start = v1_health_after_start
        self.v1_main_pid = v1_main_pid
        self.calls: list[tuple] = []
        self.started = False
        self.stopped = False

    # --- the surface the canary path uses ---------------------------------
    def binding(self, config_path: Path) -> dict:
        # Only V1's binding is read (to prove the two ports differ).
        return {"bind": "127.0.0.1", "port": 8401,
                "probe": "http://127.0.0.1:8401", "device_ref": "device:old"}

    def manifest_of(self, root: Path) -> dict:
        return install.manifest_of(root)

    def wait_for_health(self, base: str, seconds: float = 20.0) -> dict | None:
        if base.endswith(":8401"):
            healthy = self.v1_health
            if self.started and self.v1_health_after_start is not None:
                healthy = self.v1_health_after_start
            return {"status": "ok"} if healthy else None
        return {"status": "ok"} if self.local_health else None

    def systemctl(self, *args, check: bool = True) -> _Result:
        self.calls.append(args)
        verb = args[0]
        if verb == "is-active":
            return _Result(self.active)
        if verb == "is-enabled":
            return _Result(self.enabled)
        if verb == "start":
            if self.start_raises is not None:
                raise self.start_raises
            self.started = True
            return _Result("", self.start_rc)
        if verb == "stop":
            self.stopped = True
            return _Result("", self.stop_rc)
        if verb == "enable":
            raise AssertionError("canary-start must never enable the Next unit")
        if verb == "show":
            unit = args[1]
            wanted = [args[i + 1] for i, a in enumerate(args) if a == "-p"]
            return _Result("\n".join(
                f"{name}={value}" for name, value in
                ((n, self._prop(unit, n)) for n in wanted)))
        return _Result("")

    def _prop(self, unit: str, name: str) -> str:
        if unit == V1_UNIT:
            return {"MainPID": str(self.v1_main_pid), "ActiveState": "active",
                    "NRestarts": "0"}.get(name, "")
        values = {
            "MemoryMax": str(1536 * 1024 * 1024) if self.limits_ok else str(1024),
            "MemorySwapMax": "0",
            "TasksMax": "96",
            "CPUQuotaPerSecUSec": "400ms",
            "ActiveState": "active" if self.started else "inactive",
            "MainPID": str(self.next_main_pid) if self.started else "0",
            "NRestarts": str(self.restarts),
        }
        return values.get(name, "")

    def verbs(self) -> list[str]:
        return [call[0] for call in self.calls]


def fixture(tmp: str, *, corrupt: bool = False, staged: bool = True) -> Path:
    """A staged Next revision whose manifest really describes its bytes."""
    root = Path(tmp) / next_install.COMPOSITION
    root.mkdir(parents=True, exist_ok=True)
    if not staged:
        return root
    rev = root / "revisions" / "fleet-mcp-20261016@deadbee"
    (rev / "fleet_mcp").mkdir(parents=True, exist_ok=True)
    (rev / "fleet_mcp" / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
    (rev / "serveros-fleet-next-mcp").write_text("#!/bin/sh\n", encoding="utf-8")
    files = install.manifest_of(rev)["files"]
    manifest = {"schema": next_install.ARTIFACT_SCHEMA, "files": files,
                "tree_sha256": next_install._tree_sha256(files)}
    (rev / next_install.MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n",
                                             encoding="utf-8")
    if corrupt:
        (rev / "fleet_mcp" / "runtime.py").write_text("VALUE = 2\n", encoding="utf-8")
    current = root / "current"
    if current.is_symlink() or current.exists():
        current.unlink()
    current.symlink_to(rev)
    return root


def binding_for(tmp: str, *, pat: Path | None = None, oauth_enabled: bool = True) -> dict:
    anchor = pat if pat is not None else Path(tmp) / "owner-pat"
    return {"device_ref": "device:operator-host", "bind": "127.0.0.1", "port": 8402,
            "mcp_path": "/mcp", "public_base_url": f"https://{FQDN}",
            "pat_anchor": anchor, "oauth_enabled": oauth_enabled}


def owner_token(tmp: str, *, mode: int = 0o600, symlink_to: Path | None = None) -> Path:
    path = Path(tmp) / "owner-pat"
    path.write_text("not-read-by-any-test\n", encoding="utf-8")
    path.chmod(mode)
    if symlink_to is not None:
        path.unlink()
        path.symlink_to(symlink_to)
    return path


def cli(argv: list[str], proposal: dict) -> tuple[int, dict]:
    with tempfile.TemporaryDirectory() as temp:
        p = Path(temp) / "proposal.json"
        p.write_text(json.dumps(proposal), encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = next_install.main([argv[0], "--proposal", str(p)] + argv[1:])
    text = stdout.getvalue()
    payload, problem = next_install.one_receipt(text)
    if problem is not None:
        raise AssertionError(f"canary receipt contract: {problem}; raw={text[:300]!r}")
    return rc, payload


def tls_ok(host: str = FQDN) -> dict:
    return {"host": host, "verified": True, "subject_alt_names": [host],
            "not_after": "Jan 14 00:00:00 2027 GMT", "issuer": []}


class CanaryHarness(unittest.TestCase):
    """Everything the canary path touches, faked in one place, loudly."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def run_canary(self, proposal: dict, mod: FakeMod, *,
                   execute: bool = True, pat: Path | None = None,
                   tls: dict | None = None, http: int = 200,
                   binding: dict | None = None, pressure: str | None = None,
                   omit_runtime_root: bool = False,
                   extra_args: list[str] | None = None) -> tuple[int, dict, FakeMod]:
        root = fixture(self.tmp)
        if not omit_runtime_root:
            proposal["next"]["runtime_root"] = str(root)
        # Only mint the default token when the caller did not bring its own
        # binding: the default lives at the same path a caller-supplied token
        # uses, and creating it here would silently reset that token's mode to
        # 0600 — disarming the very check a mode test is exercising.
        if binding is None:
            resolved = binding_for(self.tmp, pat=pat if pat is not None
                                   else owner_token(self.tmp))
        else:
            resolved = binding
        argv = ["canary-start"]
        if execute:
            argv += ["--execute", "--owner-approved"]
        argv += (extra_args or [])
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                next_install, "_load_legacy_installer", return_value=mod))
            stack.enter_context(mock.patch.object(
                next_install, "_next_binding", return_value=(resolved, None)))
            stack.enter_context(mock.patch.object(
                next_install, "_tls_readback", return_value=tls or tls_ok()))
            stack.enter_context(mock.patch.object(
                next_install, "_http_status", return_value=http))
            stack.enter_context(mock.patch.object(
                next_install, "_current_pressure", return_value=pressure))
            rc, payload = cli(argv, proposal)
        return rc, payload, mod


class PatAnchorMustBePresentAndPrivate(CanaryHarness):
    """(1) A missing owner token blocks the start without touching systemd."""

    def test_missing_anchor_is_blocked_and_nothing_is_started(self):
        proposal = candidate()
        missing = Path(self.tmp) / "absent-owner-pat"
        mod = FakeMod()
        rc, payload, _ = self.run_canary(
            proposal, mod, binding=binding_for(self.tmp, pat=missing))
        self.assertEqual(rc, 2)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertIn("does not exist", payload["reason"])
        self.assertEqual(payload["executes"], [])
        self.assertFalse(mod.started)
        self.assertNotIn("start", mod.verbs())

    def test_world_readable_anchor_is_blocked(self):
        proposal = candidate()
        token = owner_token(self.tmp, mode=0o644)
        mod = FakeMod()
        _, payload, _ = self.run_canary(proposal, mod,
                                        binding=binding_for(self.tmp, pat=token))
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertIn("0600", payload["reason"])
        self.assertFalse(mod.started)

    def test_symlinked_anchor_is_blocked(self):
        proposal = candidate()
        elsewhere = Path(self.tmp) / "elsewhere-pat"
        elsewhere.write_text("x\n", encoding="utf-8")
        token = owner_token(self.tmp, symlink_to=elsewhere)
        mod = FakeMod()
        _, payload, _ = self.run_canary(proposal, mod,
                                        binding=binding_for(self.tmp, pat=token))
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertIn("symlink", payload["reason"])
        self.assertFalse(mod.started)


class PathsMustNotReachIntoV1(CanaryHarness):
    """(2) A token or OAuth store that resolves inside V1 is refused."""

    def test_oauth_store_inside_v1_root_is_blocked(self):
        proposal = candidate()
        # A real, private anchor whose derived OAuth store
        # (`anchor.parent/"fleet-mcp-oauth"`) resolves inside V1's own state.
        v1_oauth = Path(self.tmp) / "v1-oauth"
        borrowed = v1_oauth / "borrowed"
        borrowed.mkdir(parents=True, exist_ok=True)
        token = borrowed / "owner-pat"
        token.write_text("not-read-by-any-test\n", encoding="utf-8")
        token.chmod(0o600)
        proposal["v1"]["oauth_state_root"] = str(v1_oauth)
        mod = FakeMod()
        _, payload, _ = self.run_canary(proposal, mod,
                                        binding=binding_for(self.tmp, pat=token))
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertIn("V1 root", payload["reason"])
        self.assertFalse(mod.started)

    def test_next_runtime_inside_v1_is_refused_before_any_start(self):
        proposal = candidate()
        proposal["next"]["runtime_root"] = proposal["v1"]["runtime_root"] + "/ingress-next"
        mod = FakeMod()
        _, payload, _ = self.run_canary(proposal, mod, omit_runtime_root=True)
        self.assertEqual(payload["decision"], "BLOCKED")
        # Refused by the dual-stack gate's own overlap rule, which runs before
        # any system action — so the refusal arrives as `blockers`, not as a
        # `reason` from a check that had already started reading the host.
        self.assertIn("overlaps V1", " ".join(payload.get("blockers", [])))
        self.assertFalse(mod.started)
        self.assertEqual(mod.calls, [])

    def test_a_symlink_that_lands_in_v1_is_refused_at_execute_time(self):
        """Lexical separation is not separation; the resolved path is asked."""
        proposal = candidate()
        v1_root = Path(self.tmp) / "v1-ingress"
        v1_root.mkdir(parents=True, exist_ok=True)
        link = Path(self.tmp) / "linked-v1"
        link.symlink_to(v1_root)
        proposal["v1"]["runtime_root"] = str(v1_root)
        proposal["next"]["runtime_root"] = str(link / "ingress-next")
        mod = FakeMod()
        _, payload, _ = self.run_canary(proposal, mod, omit_runtime_root=True)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertIn("resolves inside V1", payload["reason"])
        self.assertFalse(mod.started)
        self.assertEqual(mod.verbs()[:1], [])


class TheUnitMustBeInactiveAndUnableToStartItself(CanaryHarness):
    """(3) active / enabled / unreadable are each a refusal, not a start."""

    def test_active_unit_is_blocked(self):
        mod = FakeMod(active="active")
        _, payload, _ = self.run_canary(candidate(), mod)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertIn("not 'inactive'", payload["reason"])
        self.assertFalse(mod.started)

    def test_enabled_unit_is_blocked(self):
        mod = FakeMod(enabled="enabled")
        _, payload, _ = self.run_canary(candidate(), mod)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertIn("enabled", payload["reason"])
        self.assertFalse(mod.started)

    def test_unknown_enablement_is_blocked(self):
        mod = FakeMod(enabled="")
        _, payload, _ = self.run_canary(candidate(), mod)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertFalse(mod.started)


class ThePublicOriginMustBeMeasured(CanaryHarness):
    """(4) A proposal cannot assert TLS; the handshake is the gate."""

    def test_unverified_handshake_is_blocked(self):
        mod = FakeMod()
        _, payload, _ = self.run_canary(candidate(), mod,
                                        tls={"host": FQDN, "verified": False,
                                             "reason": "SSLCertVerificationError"})
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertIn("verified TLS handshake", payload["reason"])
        self.assertFalse(mod.started)

    def test_certificate_naming_another_host_is_blocked(self):
        mod = FakeMod()
        _, payload, _ = self.run_canary(
            candidate(), mod,
            tls={"host": FQDN, "verified": True,
                 "subject_alt_names": ["somewhere.else.invalid"]})
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertIn("subjectAltName", payload["reason"])
        self.assertFalse(mod.started)


class TheStagedRevisionMustBeTheArtifactItNames(CanaryHarness):
    """(5) Missing, unmanifested and tampered revisions are refused."""

    def test_nothing_staged_is_blocked(self):
        proposal = candidate()
        root = fixture(self.tmp, staged=False)
        proposal["next"]["runtime_root"] = str(root)
        mod = FakeMod()
        with mock.patch.object(next_install, "_load_legacy_installer", return_value=mod), \
             mock.patch.object(next_install, "_next_binding",
                               return_value=(binding_for(self.tmp), None)), \
             mock.patch.object(next_install, "_tls_readback", return_value=tls_ok()), \
             mock.patch.object(next_install, "_current_pressure", return_value=None):
            rc, payload = cli(["canary-start", "--execute", "--owner-approved"], proposal)
        self.assertEqual(rc, 2)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertIn("no staged Next revision", payload["reason"])
        self.assertFalse(mod.started)

    def test_tampered_tree_is_blocked(self):
        proposal = candidate()
        root = fixture(self.tmp, corrupt=True)
        proposal["next"]["runtime_root"] = str(root)
        mod = FakeMod()
        with mock.patch.object(next_install, "_load_legacy_installer", return_value=mod), \
             mock.patch.object(next_install, "_next_binding",
                               return_value=(binding_for(self.tmp), None)), \
             mock.patch.object(next_install, "_tls_readback", return_value=tls_ok()), \
             mock.patch.object(next_install, "_current_pressure", return_value=None):
            rc, payload = cli(["canary-start", "--execute", "--owner-approved"], proposal)
        self.assertEqual(rc, 2)
        self.assertIn("does not match its own manifest", payload["reason"])
        self.assertFalse(mod.started)


class TheHappyPathStartsOnceAndChangesNothingElse(CanaryHarness):
    """(6) One start, cgroup enforced, V1 identical, and it is NOT enabled."""

    def test_happy_start(self):
        mod = FakeMod()
        rc, payload, _ = self.run_canary(candidate(), mod)
        self.assertEqual(rc, 0, payload)
        self.assertEqual(payload["decision"], "EXECUTED")
        self.assertEqual(payload["executes"], [UNIT])
        self.assertEqual(payload["writes"], [])
        self.assertIs(payload["enabled"], False)
        self.assertFalse(payload["v1_action"])
        self.assertTrue(mod.started)
        self.assertEqual(mod.verbs().count("start"), 1)
        self.assertNotIn("enable", mod.verbs())
        self.assertNotIn("restart", mod.verbs())
        # the readbacks are measurements, not promises
        self.assertEqual(payload["v1_main_pid"], 1111)
        self.assertTrue(payload["v1_main_pid_unchanged"])
        self.assertEqual(payload["public_readback"],
                         {"/health": 200,
                          "/.well-known/oauth-protected-resource": 200,
                          "/.well-known/oauth-authorization-server": 200})
        self.assertIsNone(payload["tool_declarations"])
        self.assertEqual(payload["binding"]["device_ref"], "device:operator-host")
        self.assertEqual(payload["owner_token_anchor"]["mode"], "0600")

    def test_cgroup_limits_are_read_back_not_assumed(self):
        mod = FakeMod(limits_ok=False)
        rc, payload, _ = self.run_canary(candidate(), mod)
        self.assertEqual(rc, 2)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertIn("MemoryMax not enforced", payload["reason"])
        self.assertTrue(mod.stopped)          # Next-only recovery ran
        self.assertNotIn("enable", mod.verbs())


class AFailedCanaryStopsNextOnly(CanaryHarness):
    """(7) A failure after the effect boundary undoes Next and nothing else."""

    def test_public_route_wrong_status_stops_next(self):
        mod = FakeMod()
        rc, payload, _ = self.run_canary(candidate(), mod, http=502)
        self.assertEqual(rc, 2)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertEqual(payload["effect_state"], "NEXT_ONLY_ACTION_ATTEMPTED")
        self.assertEqual(payload["executes"], [UNIT])
        self.assertTrue(mod.stopped)
        self.assertFalse(payload["v1_action"])

    def test_local_health_silent_stops_next(self):
        mod = FakeMod(local_health=False)
        rc, payload, _ = self.run_canary(candidate(), mod)
        self.assertEqual(rc, 2)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertIn("answered nothing on", payload["reason"])
        self.assertTrue(mod.stopped)

    def test_exception_during_start_stops_next_only(self):
        mod = FakeMod(start_raises=OSError("systemctl is not on PATH"))
        rc, payload, _ = self.run_canary(candidate(), mod)
        self.assertEqual(rc, 2)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertEqual(payload["effect_state"], "NEXT_ONLY_ACTION_ATTEMPTED")
        self.assertTrue(mod.stopped)
        self.assertNotIn("enable", mod.verbs())

    def test_v1_health_lost_after_start_stops_next(self):
        mod = FakeMod(v1_health_after_start=False)
        rc, payload, _ = self.run_canary(candidate(), mod)
        self.assertEqual(rc, 2)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertIn("V1 /health stopped answering", payload["reason"])
        self.assertTrue(mod.stopped)

    def test_v1_unhealthy_before_the_start_refuses_without_touching_it(self):
        mod = FakeMod(v1_health=False)
        rc, payload, _ = self.run_canary(candidate(), mod)
        self.assertEqual(rc, 2)
        self.assertEqual(payload["decision"], "BLOCKED")
        self.assertIn("V1 /health did not answer", payload["reason"])
        self.assertFalse(mod.started)
        self.assertFalse(mod.stopped)
        # It read the unit (that is how the state was checked) and changed nothing.
        self.assertNotIn("start", mod.verbs())
        self.assertNotIn("stop", mod.verbs())


class TheDryRunIsInert(CanaryHarness):
    """(8) A canary preview emits writes=[] executes=[] and drives no systemd."""

    def test_preview_touches_nothing(self):
        proposal = candidate()
        mod = FakeMod()
        with mock.patch.object(next_install, "_load_legacy_installer",
                               return_value=mod) as loader:
            rc, payload = cli(["canary-start"], proposal)
        self.assertEqual(rc, 0)
        self.assertEqual(payload["decision"], "DRY_RUN_READY")
        self.assertEqual(payload["mode"], "NEXT_ONLY_CANARY_START")
        self.assertEqual(payload["writes"], [])
        self.assertEqual(payload["executes"], [])
        self.assertEqual(mod.calls, [])
        loader.assert_not_called()

    def test_install_no_start_preview_is_unchanged(self):
        # The same proposal that a canary starts on is NOT installable: the
        # preview for `install --no-start` keeps refusing it, because that verb
        # still demands the two registration facts. Only once they exist does
        # the install preview open — which is the unchanged behaviour under test.
        self.assertEqual(
            next_install.plan(candidate(), "install", no_start=True)["decision"],
            "BLOCKED")
        registered = candidate()
        registered["next"]["plugin_id"] = "plugin_asdk_app_fresh_next"
        registered["attestations"]["next_oauth_client_authorized"] = True
        preview = next_install.plan(registered, "install", no_start=True)
        self.assertEqual(preview["decision"], "DRY_RUN_READY")
        self.assertEqual(preview["writes"], [])
        self.assertEqual(preview["executes"], [])


class TheExemptionIsNarrow(CanaryHarness):
    """(9) No Plugin ID is fine for THIS start, and still fatal for `install`."""

    def test_canary_start_runs_with_unassigned_plugin_and_unauthorized_client(self):
        proposal = candidate()
        self.assertIsNone(proposal["next"]["plugin_id"])
        self.assertIs(proposal["attestations"]["next_oauth_client_authorized"], False)
        mod = FakeMod()
        rc, payload, _ = self.run_canary(proposal, mod)
        self.assertEqual(rc, 0)
        self.assertEqual(payload["decision"], "EXECUTED")

    def test_install_still_requires_both(self):
        proposal = candidate()
        preflight = gate.evaluate(proposal)
        self.assertEqual(preflight["decision"], "BLOCKED")
        self.assertIn("next.plugin_id: actual installed/registered ID required",
                      preflight["blockers"])
        self.assertIn("attestations.next_oauth_client_authorized: "
                      "owner/readback confirmation missing", preflight["blockers"])
        self.assertEqual(next_install.plan(proposal, "install")["decision"], "BLOCKED")

    def test_canary_still_requires_every_other_attestation(self):
        for key in ("v1_identity_observed", "next_owner_binding_reviewed",
                    "rollback_ready"):
            proposal = candidate()
            proposal["attestations"][key] = False
            preflight = gate.evaluate(proposal, canary=True)
            self.assertEqual(preflight["decision"], "BLOCKED", key)
            self.assertIn(f"attestations.{key}", " ".join(preflight["blockers"]))

    def test_canary_requires_a_real_public_url(self):
        proposal = candidate()
        proposal["next"]["public_mcp_url"] = None
        preflight = gate.evaluate(proposal, canary=True)
        self.assertEqual(preflight["decision"], "BLOCKED")
        self.assertIn("next.public_mcp_url", " ".join(preflight["blockers"]))

    def test_modes_are_mutually_exclusive(self):
        for kwargs in ({"bootstrap": True, "canary": True},
                       {"recovery": True, "canary": True},
                       {"recovery": True, "bootstrap": True, "canary": True}):
            self.assertEqual(gate.evaluate(candidate(), **kwargs)["decision"], "BLOCKED")


class TheMirrorsAgreeWithTheirAuthority(unittest.TestCase):
    """The two names copied from `install.py`, and the hash formula, are bound."""

    def test_manifest_names_match(self):
        self.assertEqual(next_install.MANIFEST, install.MANIFEST)
        self.assertEqual(next_install.ARTIFACT_SCHEMA, install.ARTIFACT_SCHEMA)

    def test_tree_hash_matches_the_installer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "tree"
            (root / "sub").mkdir(parents=True)
            (root / "a.txt").write_text("a\n", encoding="utf-8")
            (root / "sub" / "b.txt").write_text("bb\n", encoding="utf-8")
            manifest = install.manifest_of(root)
            self.assertEqual(next_install._tree_sha256(manifest["files"]),
                             manifest["tree_sha256"])


class TheStagedRevisionIsConfirmedAgainstItsManifest(unittest.TestCase):
    """The manifest check itself, without a start in front of it."""

    def test_healthy_revision_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = fixture(temp)
            revision, problem = next_install._staged_revision(FakeMod(), root)
            self.assertIsNone(problem, problem)
            self.assertEqual(len(revision["tree_sha256"]), 64)

    def test_unlisted_file_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = fixture(temp)
            target = (root / "current").resolve()
            (target / "smuggled.py").write_text("VALUE = 3\n", encoding="utf-8")
            _, problem = next_install._staged_revision(FakeMod(), root)
            self.assertIn("unlisted", problem)

    def test_recorded_hash_not_matching_its_own_list_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = fixture(temp)
            target = (root / "current").resolve()
            manifest = json.loads((target / next_install.MANIFEST).read_text())
            manifest["tree_sha256"] = "0" * 64
            (target / next_install.MANIFEST).write_text(json.dumps(manifest))
            _, problem = next_install._staged_revision(FakeMod(), root)
            self.assertIn("not the hash of its own file list", problem)


if __name__ == "__main__":
    unittest.main()
