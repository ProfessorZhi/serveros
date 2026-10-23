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
from test_fleet_create_session_runtime import (  # noqa: E402
    TwoAccounts, DEVICE, SECOND, PROJECT, OWNER_ID)

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

    def test_the_receipt_keeps_requested_and_resolved_apart(self):
        """The caller asked for NOTHING; the policy filled something in.

        This is the case the three words exist for. `requested` is null because
        the caller named no model, `resolved` is the owner-approved default, and
        `effective` carries no value because this fake device reported nothing
        back. Reporting `resolved` as effective would assert a readback the
        surface never took, and the one case that matters — a device that
        quietly applies its own default — is exactly where the two differ.
        """
        self.configure()
        self.h.second_provider.create = lambda **kw: {"sessionId": "session-defaulted"}
        result = self.h.create(runtime=SECOND, project=PROJECT)
        defaults = result["defaults"]
        self.assertIsNone(defaults["requested"]["model"])
        self.assertIsNone(defaults["requested"]["permission_mode"])
        self.assertEqual(defaults["resolved"]["model"], M[0])
        self.assertEqual(defaults["resolved"]["permission_mode"], "bypassPermissions")
        self.assertEqual(defaults["resolved"]["by"], "runtime/project policy")
        self.assertIsNone(defaults["effective"]["model"])
        self.assertIsNone(defaults["effective"]["permission_mode"])
        self.assertIn("UNKNOWN", defaults["effective_reason"])

    def test_an_explicit_request_survives_as_requested(self):
        """`resolve()` REBINDS the names, so this is what it used to destroy.

        A caller asking for `M[2]` used to leave no trace of having asked: the
        local names were overwritten by the resolution, so a receipt built
        afterwards could not tell "asked for `M[2]`" from "asked for nothing and
        was given `M[2]`". Both readings are plausible from the old answer and
        only one of them is true.
        """
        self.configure()
        self.h.second_provider.create = lambda **kw: {"sessionId": "session-explicit"}
        result = self.h.create(runtime=SECOND, project=PROJECT, model=M[2],
                               permission_mode="default")
        defaults = result["defaults"]
        self.assertEqual(defaults["requested"]["model"], M[2])
        self.assertEqual(defaults["requested"]["permission_mode"], "default")
        self.assertEqual(defaults["resolved"]["model"], M[2])
        self.assertEqual(defaults["resolved"]["permission_mode"], "default")

    def test_effective_is_never_inferred_from_resolved(self):
        """Even when the two agree, `effective` stays empty without a readback.

        The value the device reported would be fine to relay — this fake device
        reports nothing, so the honest answer is null. Copying `resolved` into
        it would be the claim that is wrong, not the value.
        """
        self.configure()
        self.h.second_provider.create = lambda **kw: {"sessionId": "session-x"}
        for kwargs in ({}, {"model": M[2], "permission_mode": "default"}):
            with self.subTest(kwargs=sorted(kwargs)):
                result = self.h.create(runtime=SECOND, project=PROJECT, **kwargs)
                effective = result["defaults"]["effective"]
                self.assertIsNone(effective["model"])
                self.assertIsNone(effective["permission_mode"])
                self.assertNotEqual(
                    effective["permission_mode"],
                    result["defaults"]["resolved"]["permission_mode"])
                self.assertIn("UNKNOWN", result["defaults"]["effective_reason"])

    def test_without_a_policy_rule_nothing_is_attributed_to_one(self):
        """No rule means no `by`: the surface must not claim a policy resolved
        something it never looked at."""
        self.h.second_provider.create = lambda **kw: {"sessionId": "session-norule"}
        result = self.h.create(runtime=SECOND, project=PROJECT)
        defaults = result["defaults"]
        self.assertIsNone(defaults["resolved"]["by"])
        self.assertIsNone(defaults["effective"]["permission_mode"])

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

    def test_a_mode_change_is_satisfiable_and_the_outer_project_is_what_it_reads(self):
        """The ceiling needs a project the caller names OUTSIDE `arguments`.

        `session_reconfigure`'s native schema has no `project` parameter, so
        before 2026-10-23 the ceiling's refusal ("must also name `project`") was
        unsatisfiable through this surface: the outer schema carried no such key
        and `arguments` refuses unknown ones. This drives the whole path —
        `native_call` → handler-passed `project` → ceiling — and checks both
        halves: no claim is refused, a matching claim is forwarded to the
        device, and the device's own `session_get` is what the ceiling compared.
        """
        from fleet_mcp.fleet import Principal
        from fleet_mcp.scopes import SCOPE_CONTROL, SCOPE_READ

        self.configure()
        providers = dict(self.h.config.providers)
        providers[KEY] = replace(providers[KEY], allow_control=True)
        self.h.config = replace(
            self.h.config, providers=providers,
            policy=replace(self.h.config.policy,
                           operator_grants={"oauth:test": {KEY: ("create", "control")}}))
        self.h.service.config = self.h.config
        seen: list[tuple[str, dict]] = []

        def fake_native(tool, arguments, *, session_ref=None):
            seen.append((tool, dict(arguments)))
            if tool == "session_get":
                return {"sessionId": OWNER_ID, "projectId": PROJECT}
            return {"ok": True}

        self.h.second_provider.native_call = fake_native
        principal = Principal(name="oauth:test",
                              scopes=frozenset((SCOPE_READ, SCOPE_CONTROL)),
                              credential_kind="oauth")
        session_ref = f"{DEVICE}|{SECOND}|{OWNER_ID}"

        with self.assertRaises(FleetRefusal) as caught:
            self.h.service.native_call(
                principal, device_ref=DEVICE, native_tool="session_reconfigure",
                arguments={"session": OWNER_ID, "permissionMode": "default"},
                session_ref=session_ref, runtime=SECOND)
        self.assertEqual(caught.exception.code, "BAD_REQUEST")

        out = self.h.service.native_call(
            principal, device_ref=DEVICE, native_tool="session_reconfigure",
            arguments={"session": OWNER_ID, "permissionMode": "default"},
            session_ref=session_ref, project=PROJECT, runtime=SECOND)
        self.assertIsInstance(out, dict)
        self.assertEqual([name for name, _ in seen],
                         ["session_get", "session_reconfigure"])

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
