"""Fleet Next per-credential-instance model and permission defaults (offline).

This suite uses two fake runtimes on one enrolled device. No production model
endpoint, subscription, OS account, bypass flag, or CloudCLI process is changed.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fleet_mcp.config import CreateGrant, PolicyBinding, provider_key  # noqa: E402
from fleet_mcp.errors import FleetRefusal  # noqa: E402
from fleet_mcp.runtime_create_policy import (  # noqa: E402
    RuntimeProjectDefaults, PolicyError, parse_runtime_create_defaults)
from test_fleet_create_session_runtime import TwoAccounts, DEVICE, SECOND, PROJECT  # noqa: E402

M = ["deepseek-v4-flash-vision-anthropic",
     "deepseek-v4-flash-anthropic",
     "v4.1flash-anthropic"]
MODES = ["default", "bypassPermissions"]
KEY = provider_key(DEVICE, SECOND)


def configuration(kind="cloudcli-deepseek"):
    return {KEY: {PROJECT: {
        "kind": kind,
        "family": "claude",
        "owner_approved": True,
        "default_model": M[0],
        "default_permission_mode": "bypassPermissions",
        "allowed_models": list(M),
        "allowed_permission_modes": list(MODES),
    }}}


def compiled(raw=None, ceilings=None):
    return parse_runtime_create_defaults(
        configuration() if raw is None else raw, known_runtimes={KEY},
        project_ceilings=ceilings or {
            PROJECT: SimpleNamespace(models=tuple(M),
                                     permission_modes=tuple(MODES)),
        })


class ParsePolicy(unittest.TestCase):
    def test_default_model_and_bypass_are_explicit(self):
        p = compiled()[KEY][PROJECT]
        self.assertEqual(
            p.resolve(family="claude", model=None, permission_mode=None),
            (M[0], "bypassPermissions"))

    def test_other_two_deepseek_models_remain_selectable(self):
        p = compiled()[KEY][PROJECT]
        for model in M[1:]:
            self.assertEqual(p.resolve(family="claude", model=model,
                                       permission_mode="default"),
                             (model, "default"))

    def test_unknown_runtime_is_not_silently_created(self):
        with self.assertRaises(PolicyError):
            compiled({"device:other|claude": configuration()[KEY]})

    def test_other_project_has_no_inherited_default(self):
        with self.assertRaises(PolicyError):
            compiled({KEY: {"not-in-create-allow": configuration()[KEY][PROJECT]}})

    def test_owner_approval_is_not_optional(self):
        p = configuration()
        p[KEY][PROJECT]["owner_approved"] = False
        with self.assertRaises(PolicyError):
            compiled(p)

    def test_global_project_ceiling_cannot_be_widened(self):
        for grant in (
            SimpleNamespace(models=tuple(M[:1]), permission_modes=tuple(MODES)),
            SimpleNamespace(models=tuple(M), permission_modes=("default",)),
        ):
            with self.subTest(grant=grant), self.assertRaises(PolicyError):
                compiled(ceilings={PROJECT: grant})

    def test_unknown_override_never_falls_back_to_admin(self):
        p = compiled()[KEY][PROJECT]
        with self.assertRaises(PolicyError):
            p.resolve(family="claude", model="unsupported-backend",
                      permission_mode=None)
        with self.assertRaises(PolicyError):
            p.resolve(family="codex", model=None, permission_mode=None)

    def test_official_and_marketops_must_not_inherit_deepseek(self):
        for kind in ("official-claude", "marketops-claude"):
            with self.subTest(kind=kind), self.assertRaises(PolicyError):
                compiled(configuration(kind))

    def test_dedicated_claude_without_authorized_default_stays_unset(self):
        p = configuration("official-claude")
        entry = p[KEY][PROJECT]
        entry.update(default_model=None, default_permission_mode=None,
                     allowed_models=["owner-approved-claude-model"])
        r = compiled(p, {PROJECT: SimpleNamespace(
            models=("owner-approved-claude-model",),
            permission_modes=tuple(MODES))})[KEY][PROJECT]
        self.assertEqual(r.resolve(family="claude", model=None, permission_mode=None),
                         (None, None))

    def test_legacy_absence_does_not_rewrite_other_accounts(self):
        self.assertEqual(parse_runtime_create_defaults(
            None, known_runtimes=set(), project_ceilings={}), {})

    def test_unknown_fields_and_duplicate_models_are_denied(self):
        p = configuration()
        p[KEY][PROJECT]["credential_secret"] = "NEVER_ALLOWED"
        with self.assertRaises(PolicyError):
            compiled(p)
        p = configuration()
        p[KEY][PROJECT]["allowed_models"] = [M[0], M[0]]
        with self.assertRaises(PolicyError):
            compiled(p)


class RealServiceIntegrationUsingFakeConnectors(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.h = TwoAccounts(Path(tmp.name))

    def configure(self, *, owner: bool = True):
        grant = CreateGrant(models=tuple(M), permission_modes=tuple(MODES))
        runtime = compiled()[KEY][PROJECT]
        policy = replace(
            self.h.config.policy,
            create_allow={PROJECT: grant},
            runtime_create_defaults={KEY: {PROJECT: runtime}},
            operator_grants=({"oauth:test": {KEY: ("create",)}} if owner else {}),
        )
        self.h.config = replace(self.h.config, policy=policy)
        self.h.service.config = self.h.config

    def test_real_create_uses_target_runtime_default_fields(self):
        self.configure()
        observed = {}
        def fake_create(**kwargs):
            observed.update(kwargs)
            return {"sessionId": "new-runtime-session"}
        self.h.second_provider.create = fake_create
        result = self.h.create(runtime=SECOND, project=PROJECT)
        self.assertEqual(observed["model"], M[0])
        self.assertEqual(observed["permission_mode"], "bypassPermissions")
        self.assertEqual(observed["provider"], "claude")
        self.assertEqual(result["session_ref"],
                         DEVICE + "|" + SECOND + "|new-runtime-session")
        self.assertEqual(self.h.owner_provider.calls, [])

    def test_explicit_alternative_model_is_confined_to_same_runtime(self):
        self.configure()
        observed = {}
        self.h.second_provider.create = lambda **kw: (
            observed.update(kw) or {"sessionId": "session-alternative"})
        self.h.create(runtime=SECOND, model=M[2], permission_mode="default")
        self.assertEqual(observed["model"], M[2])
        self.assertEqual(observed["permission_mode"], "default")
        self.assertEqual(self.h.owner_provider.calls, [])

    def test_no_operator_create_grant_denies_even_when_scope_is_present(self):
        self.configure(owner=False)
        with self.assertRaises(FleetRefusal) as caught:
            self.h.create(runtime=SECOND)
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertEqual(self.h.second_provider.calls, [])

    def test_unapproved_model_refused_before_provider_call(self):
        self.configure()
        with self.assertRaises(FleetRefusal) as caught:
            self.h.create(runtime=SECOND, model="another-model")
        self.assertEqual(caught.exception.code, "WRITE_NOT_GRANTED")
        self.assertEqual(self.h.second_provider.calls, [])

    def test_wrong_agent_family_refused_before_provider_call(self):
        self.configure()
        with self.assertRaises(FleetRefusal):
            self.h.create(runtime=SECOND, provider="codex")
        self.assertEqual(self.h.second_provider.calls, [])

    def test_native_cloudcli_session_create_uses_same_runtime_defaults(self):
        self.configure()
        self.h.service.native_call(
            self.h.principal(), device_ref=DEVICE, native_tool="session_create",
            arguments={"project": PROJECT}, project=PROJECT, runtime=SECOND)
        self.assertEqual(self.h.owner_provider.calls, [])
        name, kwargs = self.h.second_provider.calls[0]
        self.assertEqual(name, "session_create")
        self.assertEqual(kwargs["model"], M[0])
        self.assertEqual(kwargs["permissionMode"], "bypassPermissions")

    def test_native_create_wrong_family_is_refused_without_provider_call(self):
        self.configure()
        with self.assertRaises(FleetRefusal) as caught:
            self.h.service.native_call(
                self.h.principal(), device_ref=DEVICE, native_tool="session_create",
                arguments={"project": PROJECT, "provider": "codex"},
                project=PROJECT, runtime=SECOND)
        self.assertEqual(caught.exception.code, "WRITE_NOT_GRANTED")
        self.assertEqual(self.h.second_provider.calls, [])

    def test_native_create_respects_own_operator_grant(self):
        self.configure(owner=False)
        with self.assertRaises(FleetRefusal):
            self.h.service.native_call(
                self.h.principal(), device_ref=DEVICE, native_tool="session_create",
                arguments={"project": PROJECT}, project=PROJECT, runtime=SECOND)
        self.assertEqual(self.h.second_provider.calls, [])

    def test_reconfigure_cannot_widen_to_a_model_allowed_only_globally(self):
        self.configure()
        rule = self.h.config.policy.runtime_create_defaults[KEY][PROJECT]
        narrow = replace(rule, allowed_models=(M[0],),
                         allowed_permission_modes=("default",),
                         default_permission_mode="default")
        self.h.config = replace(self.h.config, policy=replace(
            self.h.config.policy,
            runtime_create_defaults={KEY: {PROJECT: narrow}}))
        self.h.service.config = self.h.config
        provider = SimpleNamespace(native_call=lambda *a, **kw: {"projectId": PROJECT})
        native = SimpleNamespace(namespaced="fleet_cloudcli_session_reconfigure")
        session_ref = DEVICE + "|" + SECOND + "|native-reconfigured"
        with self.assertRaises(FleetRefusal) as caught:
            self.h.service._native_reconfigure_ceiling(
                provider, DEVICE, session_ref, PROJECT, {"model": M[1]}, native)
        self.assertEqual(caught.exception.code, "WRITE_NOT_GRANTED")

    def test_reconfigure_does_not_set_unmentioned_permission_mode(self):
        self.configure()
        provider = SimpleNamespace(native_call=lambda *a, **kw: {"projectId": PROJECT})
        native = SimpleNamespace(namespaced="fleet_cloudcli_session_reconfigure")
        arguments = {"model": M[0]}
        self.h.service._native_reconfigure_ceiling(
            provider, DEVICE, DEVICE + "|" + SECOND + "|native-ok",
            PROJECT, arguments, native)
        self.assertEqual(arguments, {"model": M[0]})

    def test_unconfigured_base_runtime_retains_legacy_defaults(self):
        self.configure()
        out = self.h.create()
        self.assertIn("|claude|", out["session_ref"])
        self.assertEqual(self.h.second_provider.calls, [])
        self.assertEqual(self.h.owner_provider.calls[0][0], "create")


if __name__ == "__main__":
    unittest.main()
