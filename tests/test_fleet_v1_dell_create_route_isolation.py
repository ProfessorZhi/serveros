"""Lane D — create / continue / reply across two OS accounts on one device.

`tests/test_fleet_multi_runtime_routing.py` already pins that a *read* follows
the reference and that a listing walks device x runtime. This file pins the
three WRITE verbs, because they are the ones that can act on the wrong account:

  * create  — the session does not exist yet, so there is no reference to
              follow and the runtime is the only thing naming the account;
  * continue — a send that starts a run for an idle session;
  * reply    — a send into a session that is already mid-run.

All three are the same provider verb with a different starting state, so the
failure this file exists against is one of them resolving the account from the
device alone and reaching the runtime beside it.

Offline throughout: fake providers, no socket, no credential value, no second
OS account. Every case asserts which fake was called and that the other one
was not.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp.config import (AuditBinding, AuthBinding, CreateGrant,   # noqa: E402
                              FleetConfig, PolicyBinding, ProviderBinding,
                              ServerBinding, provider_key)
from fleet_mcp.errors import (APPROVAL_REQUIRED, BAD_REQUEST,   # noqa: E402
                              BAD_SESSION_REF, FleetRefusal,
                              PROVIDER_UNREACHABLE, UNKNOWN_DEVICE,
                              WRITE_NOT_GRANTED)
from fleet_mcp.fleet import (SCOPE_CREATE, SCOPE_READ, SCOPE_SEND,   # noqa: E402
                             FleetService, Principal)
from fleet_mcp.identity import SessionKey                            # noqa: E402
from fleet_mcp.providers.base import SessionSummary                  # noqa: E402
from fleet_mcp.runtime_create_policy import (PolicyError,            # noqa: E402
                                             RuntimeProjectDefaults)

DEVICE = "device:llm-lab-dell"
OWNER = "vince"
OTHER = "fjd"
OTHER_RUNTIME = "claude-fjd"
BASE_RUNTIME = "claude"
PROJECT = "dell-lab"
UUID = "b2cb3f10-1111-2222-3333-444455556666"
OWNER_REF = f"{DEVICE}|{BASE_RUNTIME}|{UUID}"
OTHER_REF = f"{DEVICE}|{OTHER_RUNTIME}|{UUID}"

#: What the owner approved for this project. Deliberately narrow: one model,
#: one permission mode, so "not approved" has something to be outside of.
APPROVED_MODEL = "deepseek-v4-flash"
APPROVED_MODE = "plan"


def _registry(tmp: Path) -> Path:
    p = tmp / "registry.yaml"
    p.write_text(
        "devices:\n"
        f"  - device_ref: {DEVICE}\n"
        "    display_name: llm-lab-dell\n"
        "    platform: linux\n"
        "    environment_profile: linux-shared-multiuser\n"
        "    ownership: company\n",
        encoding="utf-8")
    return p


class _Recorder:
    """One account's runtime, recording every verb it was handed.

    `answers=False` models the account whose connector is not running, which on
    this device is the ordinary state of the second account today.
    """

    kind = "fleet-node"

    def __init__(self, runtime: str, *, answers: bool = True):
        self.device_ref = DEVICE
        self.runtime = runtime
        self.answers = answers
        self.calls: list = []

    # -- reads -------------------------------------------------------------
    def health(self):
        raise AssertionError("health is not exercised here")

    def list_sessions(self, *, limit, project=None, state=None):
        self.calls.append(("list_sessions", project))
        self._or_offline()
        return []

    def get_session(self, key):
        self.calls.append(("get_session", key.ref))
        self._or_offline()
        return {"session_ref": key.ref, "served_by": self.runtime}

    def read_session(self, key, **_):
        self.calls.append(("read_session", key.ref))
        self._or_offline()
        return {"session_ref": key.ref, "served_by": self.runtime}

    def get_run(self, key, run_id, **_):
        self.calls.append(("get_run", key.ref))
        self._or_offline()
        return {"served_by": self.runtime}

    # -- writes ------------------------------------------------------------
    def create(self, *, project, message=None, provider="claude", model=None,
               lifecycle_mode=None, permission_mode=None):
        self.calls.append(("create", {"project": project, "provider": provider,
                                      "model": model, "permission_mode": permission_mode}))
        self._or_offline()
        return {"sessionId": UUID, "runId": None, "served_by": self.runtime}

    def send(self, key, message, *, idempotency_key=None):
        self.calls.append(("send", {"ref": key.ref, "message": message}))
        self._or_offline()
        return {"sessionId": key.native_session_id, "served_by": self.runtime,
                "runId": f"{self.runtime}-fixture-run-{len(self.calls)}"}

    def interrupt(self, key):
        self.calls.append(("interrupt", key.ref))
        self._or_offline()
        return {"aborted": True}

    def _or_offline(self):
        if not self.answers:
            raise _offline(self.runtime)


def _offline(runtime: str):
    from fleet_mcp.errors import refuse
    return refuse(PROVIDER_UNREACHABLE, f"{runtime} did not answer")


class TwoAccounts:
    """One Dell device, two account runtimes, and the service in front of them."""

    def __init__(self, tmp: Path, *, owner: bool = True, other: bool = True,
                 other_enabled: bool = True, other_answers: bool = True,
                 other_allow_create: bool = False, other_allow_send: bool = True,
                 grants=None):
        self.tmp = tmp
        owner_anchor = tmp / "vince.node-secret"
        owner_anchor.write_text("owner-secret", encoding="utf-8")
        other_anchor = tmp / "fjd.node-secret"
        other_anchor.write_text("other-secret", encoding="utf-8")

        providers = {}
        if owner:
            providers[DEVICE] = ProviderBinding(
                device_ref=DEVICE, kind="fleet-node",
                endpoint="http://127.0.0.1:7301", credential_anchor=owner_anchor,
                runtime=BASE_RUNTIME, allow_send=True, allow_create=True,
                allow_control=True)
        if other:
            providers[provider_key(DEVICE, OTHER_RUNTIME)] = ProviderBinding(
                device_ref=DEVICE, kind="fleet-node",
                endpoint="http://127.0.0.1:7302", credential_anchor=other_anchor,
                runtime=OTHER_RUNTIME, allow_send=other_allow_send,
                allow_create=other_allow_create, allow_control=False,
                enabled=other_enabled)

        self.config = FleetConfig(
            source=None, local_device_ref=DEVICE, providers=providers,
            auth=AuthBinding(pat_anchor=owner_anchor),
            server=ServerBinding(bind="127.0.0.1", port=0),
            policy=PolicyBinding(
                operator_grants=dict(grants or {}),
                # Both sessions are named, because `send_allow` is per SESSION
                # ref, and the two refs differ only in the runtime tag. A policy
                # that named one and not the other is the shape being tested.
                send_allow=(OWNER_REF, OTHER_REF),
                create_allow={PROJECT: CreateGrant(
                    models=(APPROVED_MODEL,), permission_modes=(APPROVED_MODE,))},
            ),
            audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        )

        self.owner = _Recorder(BASE_RUNTIME) if owner else None
        self.other = _Recorder(OTHER_RUNTIME, answers=other_answers) if other else None
        instances = {}
        if owner:
            instances[provider_key(DEVICE, BASE_RUNTIME)] = self.owner
        if other and other_enabled:
            instances[provider_key(DEVICE, OTHER_RUNTIME)] = self.other
        from fleet_mcp.registry import load_registry
        self.service = FleetService(self.config, registry=load_registry(_registry(tmp)),
                                    providers=instances)

    def principal(self, *scopes, name="oauth:test"):
        return Principal(name=name,
                         scopes=frozenset(scopes or (SCOPE_READ, SCOPE_SEND, SCOPE_CREATE)),
                         credential_kind="oauth")

    def verbs(self, recorder, verb):
        return [c for c in recorder.calls if c[0] == verb]


class CreateNamesTheAccountAndNothingElseCanTest(unittest.TestCase):
    """A create has no session ref to follow, so the runtime carries the whole
    account decision."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_create_on_the_owner_runtime_reaches_only_the_owner(self):
        h = TwoAccounts(self.tmp)
        h.service.create_session(h.principal(), device=DEVICE, project=PROJECT,
                                 runtime=BASE_RUNTIME)
        self.assertEqual(len(h.verbs(h.owner, "create")), 1)
        self.assertEqual(h.other.calls, [])

    def test_the_default_runtime_is_the_devices_own_and_not_the_other_account(self):
        """Every caller written before this device had two accounts omits the
        runtime. That call must keep reaching the account it always reached."""
        h = TwoAccounts(self.tmp)
        h.service.create_session(h.principal(), device=DEVICE, project=PROJECT)
        self.assertEqual(len(h.verbs(h.owner, "create")), 1)
        self.assertEqual(h.other.calls, [])

    def test_a_create_on_the_other_account_needs_its_own_binding_flag(self):
        """`allow_create` is read for the caller's runtime. An operator who
        authorized creation in one account has not authorized it in the one
        beside it, and the refusal says so instead of reaching for the flag
        next door."""
        h = TwoAccounts(self.tmp, other_allow_create=False)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.create_session(h.principal(), device=DEVICE, project=PROJECT,
                                     runtime=OTHER_RUNTIME)
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertEqual(h.other.calls, [])

    def test_with_its_own_flag_the_same_call_reaches_the_other_account(self):
        h = TwoAccounts(self.tmp, other_allow_create=True)
        h.service.create_session(h.principal(), device=DEVICE, project=PROJECT,
                                 runtime=OTHER_RUNTIME)
        self.assertEqual(len(h.verbs(h.other, "create")), 1)
        self.assertEqual(h.owner.calls, [])

    def test_a_missing_second_binding_is_unreachable_not_unauthorized(self):
        """The two answers point at different files: a missing binding is a
        routing fact, a false flag is a policy fact."""
        h = TwoAccounts(self.tmp, other=False)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.create_session(h.principal(), device=DEVICE, project=PROJECT,
                                     runtime=OTHER_RUNTIME)
        self.assertEqual(caught.exception.code, UNKNOWN_DEVICE)

    def test_a_disabled_second_binding_is_also_unreachable(self):
        h = TwoAccounts(self.tmp, other=True, other_enabled=False,
                        other_allow_create=True)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.create_session(h.principal(), device=DEVICE, project=PROJECT,
                                     runtime=OTHER_RUNTIME)
        self.assertEqual(caught.exception.code, UNKNOWN_DEVICE)


class ContinueAndReplyFollowTheReferenceTest(unittest.TestCase):
    """Both are `send`; they differ only in the session's starting state, and
    neither may resolve the account from the device alone."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_send_follows_the_reference_to_the_other_account(self):
        h = TwoAccounts(self.tmp)
        h.service.send_to_session(h.principal(), SessionKey.parse(OTHER_REF),
                                  message="continue please")
        sent = h.verbs(h.other, "send")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1]["ref"], OTHER_REF)
        self.assertEqual(h.owner.calls, [])

    def test_a_send_follows_the_reference_to_the_owner(self):
        h = TwoAccounts(self.tmp)
        h.service.send_to_session(h.principal(), SessionKey.parse(OWNER_REF),
                                  message="reply please")
        self.assertEqual(len(h.verbs(h.owner, "send")), 1)
        self.assertEqual(h.other.calls, [])

    def test_the_same_native_id_under_the_two_accounts_sends_to_the_named_one(self):
        """The whole reason the triple exists. Two live sessions share a native
        id; a send addressed to one must not land on the other."""
        h = TwoAccounts(self.tmp)
        h.service.send_to_session(h.principal(), SessionKey.parse(OTHER_REF),
                                  message="to the second account")
        self.assertEqual([c[1]["ref"] for c in h.verbs(h.owner, "send")], [])
        self.assertEqual([c[1]["ref"] for c in h.verbs(h.other, "send")], [OTHER_REF])

    def test_the_other_accounts_session_is_not_readable_through_the_owner_runtime(self):
        h = TwoAccounts(self.tmp)
        h.service.get_session(h.principal(), SessionKey.parse(OTHER_REF))
        self.assertEqual(h.owner.calls, [])
        self.assertEqual([c[0] for c in h.other.calls], ["get_session"])

    def test_a_reference_naming_a_runtime_that_is_not_bound_is_refused(self):
        h = TwoAccounts(self.tmp, other=False)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.send_to_session(h.principal(), SessionKey.parse(OTHER_REF),
                                      message="hello")
        # Refused either way; see the class below for why the CODE is the weaker
        # of the two answers the surface has for this situation.
        self.assertIn(caught.exception.code, (UNKNOWN_DEVICE, APPROVAL_REQUIRED))
        self.assertEqual(h.owner.calls, [], "the owner runtime was not tried instead")

    def test_a_send_into_a_silent_second_account_is_unreachable_not_delivered(self):
        """The failure mode this closes: a send that could not be delivered
        reported as one that was."""
        h = TwoAccounts(self.tmp, other_answers=False)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.send_to_session(h.principal(), SessionKey.parse(OTHER_REF),
                                      message="hello")
        self.assertEqual(caught.exception.code, PROVIDER_UNREACHABLE)
        self.assertEqual(h.owner.calls, [], "the owner runtime was not tried instead")

    def test_a_send_without_the_send_scope_never_reaches_either_account(self):
        h = TwoAccounts(self.tmp)
        with self.assertRaises(FleetRefusal):
            h.service.send_to_session(h.principal(SCOPE_READ), SessionKey.parse(OTHER_REF),
                                      message="hello")
        self.assertEqual(h.owner.calls, [])
        self.assertEqual(h.other.calls, [])

    def test_a_reference_whose_tag_is_not_a_bound_runtime_is_refused_typed(self):
        h = TwoAccounts(self.tmp)
        rogue = SessionKey(DEVICE, "claude-nobody", UUID)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.send_to_session(h.principal(), rogue, message="hello")
        self.assertIn(caught.exception.code, (UNKNOWN_DEVICE, BAD_SESSION_REF,
                                              APPROVAL_REQUIRED))
        self.assertEqual(h.owner.calls, [])
        self.assertEqual(h.other.calls, [])

    def test_a_reference_to_the_silent_account_is_not_served_by_the_owner(self):
        """The invariant that matters more than any single code: whichever way
        the refusal is spelled, it is a refusal — the message is not delivered
        through the account that happens to be reachable."""
        for name, kwargs in (("unbound", {"other": False}),
                             ("silent", {"other_answers": False})):
            with self.subTest(case=name):
                sub = self.tmp / name
                sub.mkdir()
                h = TwoAccounts(sub, **kwargs)
                with self.assertRaises(FleetRefusal):
                    h.service.send_to_session(h.principal(),
                                              SessionKey.parse(OTHER_REF),
                                              message="hello")
                self.assertEqual([c for c in h.owner.calls if c[0] == "send"], [])


class TheModelCeilingIsPerRuntimeAndProjectTest(unittest.TestCase):
    """An upstream model id is not proof of entitlement, and a refusal must not
    fall back to the default the caller did not ask for."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_an_unapproved_model_is_refused_not_substituted(self):
        h = TwoAccounts(self.tmp)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.create_session(h.principal(), device=DEVICE, project=PROJECT,
                                     model="some-unapproved-model")
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
        self.assertEqual(h.owner.calls, [], "nothing reached the device")
        self.assertEqual(h.other.calls, [])

    def test_the_refusal_names_what_is_allowed(self):
        h = TwoAccounts(self.tmp)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.create_session(h.principal(), device=DEVICE, project=PROJECT,
                                     model="nope")
        structured = getattr(caught.exception, "as_dict", None)
        detail = str(caught.exception)
        self.assertIn("model", detail)
        if callable(structured):
            self.assertEqual(structured().get("allowed"), [APPROVED_MODEL])

    def test_an_approved_model_is_forwarded_under_the_providers_own_name(self):
        h = TwoAccounts(self.tmp, other_allow_create=True)
        h.service.create_session(h.principal(), device=DEVICE, project=PROJECT,
                                 runtime=OTHER_RUNTIME, model=APPROVED_MODEL,
                                 permission_mode=APPROVED_MODE)
        call = h.verbs(h.other, "create")[0][1]
        self.assertEqual(call["model"], APPROVED_MODEL)
        self.assertEqual(call["permission_mode"], APPROVED_MODE)
        self.assertEqual(h.owner.calls, [])

    def test_an_unapproved_permission_mode_is_refused(self):
        h = TwoAccounts(self.tmp)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.create_session(h.principal(), device=DEVICE, project=PROJECT,
                                     permission_mode="bypassPermissions")
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
        self.assertEqual(h.owner.calls, [])

    def test_naming_neither_gets_the_providers_own_default(self):
        """The asymmetry is deliberate: the ceiling constrains what the CALLER
        may name, not what the operator's provider already does by default."""
        h = TwoAccounts(self.tmp)
        h.service.create_session(h.principal(), device=DEVICE, project=PROJECT)
        call = h.verbs(h.owner, "create")[0]
        self.assertIsNone(call[1]["model"])
        self.assertIsNone(call[1]["permission_mode"])

    def test_the_runtime_rule_refuses_a_family_the_runtime_does_not_serve(self):
        """The per-runtime policy is stricter than the global grant, and it is
        addressed by the exact bound runtime — there is no global default and no
        fallback account."""
        rule = RuntimeProjectDefaults(
            kind="cloudcli-deepseek", family="claude",
            default_model=APPROVED_MODEL, default_permission_mode=APPROVED_MODE,
            allowed_models=(APPROVED_MODEL,), allowed_permission_modes=(APPROVED_MODE,))
        with self.assertRaises(PolicyError) as caught:
            rule.resolve(family="codex", model=None, permission_mode=None)
        self.assertIn("family", str(caught.exception))

    def test_the_runtime_rule_refuses_a_model_outside_its_allowlist(self):
        rule = RuntimeProjectDefaults(
            kind="cloudcli-deepseek", family="claude",
            default_model=APPROVED_MODEL, default_permission_mode=APPROVED_MODE,
            allowed_models=(APPROVED_MODEL,), allowed_permission_modes=(APPROVED_MODE,))
        with self.assertRaises(PolicyError) as caught:
            rule.resolve(family="claude", model="gpt-4o", permission_mode=None)
        self.assertIn("model not approved", str(caught.exception))

    def test_the_runtime_rule_is_closed_when_the_caller_names_nothing(self):
        """No default declared and nothing named resolves to nothing — it does
        not reach for a model from the account beside it."""
        rule = RuntimeProjectDefaults(
            kind="other", family="claude", default_model=None,
            default_permission_mode=None, allowed_models=(APPROVED_MODEL,),
            allowed_permission_modes=(APPROVED_MODE,))
        self.assertEqual(rule.resolve(family="claude", model=None,
                                      permission_mode=None), (None, None))


class TheOtherAccountIsNotASecondDeviceTest(unittest.TestCase):
    """Two accounts, one `device:` ref, and no path by which the account becomes
    a device identity of its own."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_device_ref_carrying_a_runtime_is_not_an_enrolled_device(self):
        h = TwoAccounts(self.tmp)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.create_session(h.principal(), device=f"{DEVICE}|{OTHER_RUNTIME}",
                                     project=PROJECT, runtime=OTHER_RUNTIME)
        self.assertIn(caught.exception.code, (UNKNOWN_DEVICE, BAD_REQUEST))

    def test_both_bindings_report_the_same_device_ref(self):
        h = TwoAccounts(self.tmp)
        refs = {b.device_ref for b in h.config.providers.values()}
        self.assertEqual(refs, {DEVICE})


class TheTwoVerbsAgreeOnAnUnboundRuntimeTest(unittest.TestCase):
    """MIGRATED FROM A GAP CHARACTERISATION — this is the evidence the fix landed.

    This class used to be `TheSendRefusalFoldsUnboundIntoUnauthorizedTest`, and
    its own docstring said what would happen to it: "when the Runtime Lane
    changes the code, this test is expected to be UPDATED, and the update is the
    evidence the fix landed". Lane B has now changed it, so the update is here.

    What the old test recorded was that `_may_send` read `binding is None or not
    binding.allow_send` as ONE condition, so a session ref naming a runtime this
    host holds no binding for came back `APPROVAL_REQUIRED` — "not authorized" —
    while `create_session` answered the same situation `UNKNOWN_DEVICE`. The two
    verbs disagreed about one missing binding.

    They agree now. The reason it mattered is not that anything was
    mis-delivered — both refused, and no provider was reached — but that the two
    answers send an operator to different files: policy for an authorization
    question, the binding for a routing one. On a device whose second account is
    simply not running yet, the wrong file is the common case.

    Rewritten as POSITIVE assertions of the corrected behaviour. The
    characterisation did not survive as a constraint, and the fix was not
    allowed to be quietly reverted to make anything green.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_send_to_an_unbound_runtime_is_unreachable(self):
        """Was `…is_approval_required_today`. The answer is now the routing one."""
        h = TwoAccounts(self.tmp, other=False)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.send_to_session(h.principal(), SessionKey.parse(OTHER_REF),
                                      message="hello")
        self.assertEqual(caught.exception.code, UNKNOWN_DEVICE)
        self.assertNotEqual(caught.exception.code, APPROVAL_REQUIRED)

    def test_the_two_verbs_now_agree_about_the_same_missing_binding(self):
        """They used to disagree, and the disagreement was the tell: create
        asked reachability first and send did not."""
        h = TwoAccounts(self.tmp, other=False)
        with self.assertRaises(FleetRefusal) as sent:
            h.service.send_to_session(h.principal(), SessionKey.parse(OTHER_REF),
                                      message="hello")
        with self.assertRaises(FleetRefusal) as created:
            h.service.create_session(h.principal(), device=DEVICE, project=PROJECT,
                                     runtime=OTHER_RUNTIME)
        self.assertEqual(sent.exception.code, UNKNOWN_DEVICE)
        self.assertEqual(created.exception.code, sent.exception.code)

    def test_the_unbound_answer_does_not_confirm_the_account_exists(self):
        """The other half of the fix: `APPROVAL_REQUIRED` would have said "there
        is a route here and you were not given it", which tells a caller probing
        a tag that the profile behind it is real."""
        h = TwoAccounts(self.tmp, other=False)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.send_to_session(h.principal(), SessionKey.parse(OTHER_REF),
                                      message="hello")
        for phrase in ("not authorized", "allow_send", "granted", "grant"):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, caught.exception.detail.lower())

    def test_neither_verb_reaches_a_provider_for_the_missing_runtime(self):
        """The invariant any fix must preserve: refusal, not a fallback."""
        h = TwoAccounts(self.tmp, other=False)
        for call in (
            lambda: h.service.send_to_session(h.principal(),
                                              SessionKey.parse(OTHER_REF), message="x"),
            lambda: h.service.create_session(h.principal(), device=DEVICE,
                                             project=PROJECT, runtime=OTHER_RUNTIME),
        ):
            with self.subTest(call=call):
                with self.assertRaises(FleetRefusal):
                    call()
        self.assertEqual(h.owner.calls, [], "no write reached the reachable account")


if __name__ == "__main__":
    unittest.main(verbosity=2)
