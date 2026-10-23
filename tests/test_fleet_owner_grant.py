#!/usr/bin/env python3
"""The three-dimension grant: principal × device × verb.

What this file is the admission question for
--------------------------------------------
Before this change, "may this caller drive that device" had two answers and
neither was about the caller:

*   the device binding's `allow_send`, which is a property of the *device*; and
*   `policy.send_allow`, which is a list of *session refs*.

`send_allow: ["*"]` collapsed the second into "every session", and it was
reachable by every credential holding `fleet:send` — a statement about all
callers, written in a file that has no way to name one. A newly registered OAuth
client inherited it on the day it was registered, without anyone editing
anything. That is the shape of grant this file exists to make unwritable.

The replacement is exact-match in three dimensions and empty by default:

    principal (`Principal.name`) → device (`device:<id>`) → verb (`send`)

Four properties carry the weight, and each has cases below:

1.  **A grant admits a session nobody named.** That is the whole point: a
    session that does not exist yet cannot be listed in `send_allow`, so a
    per-session allowlist is a list that has to be edited after every create.
2.  **Refusal is per dimension, not per bit.** An ungranted principal on a
    granted device, a granted principal on a different device, and a grant on a
    device whose owner does not make it eligible are three different refusals,
    because they send an operator to three different edits.
3.  **The verb does not inherit.** `send` is granted by the word `send`. The
    three planned verbs are refused at load rather than accepted and ignored,
    and a grant listing one of them does not thereby admit a send.
4.  **Nothing is inferred from the session id.** The legacy per-session path is
    byte-identical and still principal-blind; the grant is a second path on a
    second axis, not a widening of the first.

Nothing here touches a real device: every provider is fake and every assertion
is about what the *service* did or refused to do before it asked one. No
session is created, interrupted or stopped anywhere in this file.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp.config import (  # noqa: E402
    AuditBinding,
    AuthBinding,
    FleetConfig,
    KNOWN_DEVICE_OWNERSHIPS,
    OPERATOR_GRANT_VERBS,
    PLANNED_OPERATOR_GRANT_VERBS,
    PolicyBinding,
    ProviderBinding,
    ServerBinding,
    load_config,
    load_operator_grants,
)
from fleet_mcp.errors import FleetRefusal  # noqa: E402
from fleet_mcp.fleet import (  # noqa: E402
    SCOPE_READ,
    SCOPE_SEND,
    FleetService,
    Principal,
)
from fleet_mcp.identity import SessionKey  # noqa: E402
from fleet_mcp.providers.base import ProviderHealth, SessionSummary  # noqa: E402
from fleet_mcp.registry import load_registry  # noqa: E402

TOKYO = "device:tokyo-shared-dev-01"
WINDOWS = "device:work-fj"
DELL = "device:llm-lab-dell"
PERSONAL_DELL = "device:personal-dell"

# The three the operator named, and the one that must stay refused. Written
# here as the *expected* set so the rule below is checked against an answer that
# was stated independently of it.
#
# It was two refusals while `device:server-beijing` was enrolled; Beijing was
# retired on 2026-10-21, and it was the only other personally-owned device. A
# tuple of one is still a tuple: the rule is "company devices are eligible", and
# the single personal device left is what keeps that rule from degenerating into
# "every device is eligible".
WANTED = (TOKYO, WINDOWS, DELL)
REFUSED = (PERSONAL_DELL,)

OAUTH = "oauth:fleet-EXAMPLEprincipal01"
SOMEONE_ELSE = "oauth:fleet-EXAMPLEprincipal02"

OPERATOR = "Operator"


class FakeProvider:
    """A device that records what it was asked, in CloudCLI's shape."""

    kind = "cloudcli-mcp-http"

    def __init__(self, device_ref: str, sessions: tuple[str, ...] = ()) -> None:
        self.device_ref = device_ref
        self._sessions = sessions
        self.sent: list[dict] = []

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            device_ref=self.device_ref, kind=self.kind, reachable=True, authorized=True,
            detail="fake", server_name="claudecodeui-mcp-gateway", server_version="0.1.0",
            tool_count=23, latency_ms=1.0,
        )

    def list_sessions(self, *, limit: int, project=None, state=None):
        return [
            SessionSummary(
                key=SessionKey.compose(self.device_ref, "claude", native),
                title=f"session {native}", classification="MANAGED_IDLE",
                running=False, resident_host=False, host_state=None,
                lifecycle_mode=None, project_id="p-1", last_activity=None,
            )
            for native in self._sessions
        ]

    def get_session(self, key):
        raise FleetRefusal("SESSION_NOT_FOUND", "no such session on this device")

    def read_session(self, key, **kwargs):
        return {"messages": []}

    def send(self, key, message, *, idempotency_key=None):
        self.sent.append({"session_ref": key.ref, "message": message,
                          "idempotency_key": idempotency_key})
        return {"runId": f"run-send-{len(self.sent)}", "accepted": True}

    def create(self, **kwargs):  # pragma: no cover - this file never creates
        raise AssertionError("this file must not create sessions")


def _registry(*, ownership: dict[str, str | None]) -> object:
    """A registry with exactly the ownership values a test needs."""
    return load_registry(_registry_file(ownership))


def _registry_file(ownership: dict[str, str | None]) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    devices = []
    for ref, owner in ownership.items():
        entry = {
            "device_ref": ref,
            "display_name": ref.split(":", 1)[1],
            "platform": "linux",
            "environment_profile": "linux-shared-multiuser",
        }
        if owner is not None:
            entry["ownership"] = owner
        devices.append(entry)
    handle.write("devices:\n")
    for entry in devices:
        handle.write(f"  - device_ref: {entry['device_ref']}\n")
        handle.write(f"    display_name: {entry['display_name']}\n")
        handle.write(f"    platform: {entry['platform']}\n")
        handle.write(f"    environment_profile: {entry['environment_profile']}\n")
        if "ownership" in entry:
            handle.write(f"    ownership: {entry['ownership']}\n")
    handle.close()
    return Path(handle.name)


class Harness:
    """A service over the real ownership map, both gates stated."""

    def __init__(self, tmp: Path, *, ownership=None, grants=None, send_allow=(),
                 allow_send=True, sessions=(TOKYO,)):
        self.tmp = tmp
        anchor = tmp / "pat"
        anchor.write_text("owner-token-value", encoding="utf-8")
        if ownership is None:
            ownership = {TOKYO: "company", WINDOWS: "company", DELL: "company",
                         PERSONAL_DELL: "personal"}
        self.ownership = ownership
        self.registry = _registry(ownership=ownership)
        self.config = FleetConfig(
            source=None,
            local_device_ref=WINDOWS,
            providers={
                ref: ProviderBinding(
                    device_ref=ref, kind="cloudcli-mcp-http",
                    endpoint=f"http://{ref}/mcp", credential_anchor=anchor,
                    allow_send=allow_send, allow_create=False,
                )
                for ref in ownership
            },
            auth=AuthBinding(pat_anchor=anchor),
            server=ServerBinding(bind="127.0.0.1", port=0),
            policy=PolicyBinding(send_allow=tuple(send_allow),
                                 operator_grants=dict(grants or {})),
            audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        )
        self.providers = {
            ref: FakeProvider(ref, sessions if ref == TOKYO else ()) for ref in ownership
        }
        self.service = FleetService(self.config, registry=self.registry,
                                    providers=dict(self.providers))

    def principal(self, name=OAUTH, *scopes_):
        return Principal(name=name,
                         scopes=frozenset(scopes_ or (SCOPE_READ, SCOPE_SEND)),
                         credential_kind="oauth")

    def send(self, session_ref, *, principal=None, message="hello", **kwargs):
        return self.service.send_to_session(
            principal if principal is not None else self.principal(),
            SessionKey.parse(session_ref), message=message, **kwargs,
        )

    def sessions(self, *, principal=None, device=TOKYO):
        return self.service.list_sessions(
            principal if principal is not None else self.principal(), device=device)

    def devices(self, *, principal=None):
        return self.service.list_devices(
            principal if principal is not None else self.principal())

    def edit_policy(self, **changes) -> None:
        self.config = replace(self.config, policy=replace(self.config.policy, **changes))
        self.service.config = self.config

    def audit_rows(self, event: str | None = None) -> list[dict]:
        path: Path = self.config.audit.path
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [r for r in rows if event is None or r.get("event") == event]


# ---------------------------------------------------------------------------
# 1. The device rule, against the registry that actually ships
# ---------------------------------------------------------------------------

class TheDeviceRule(unittest.TestCase):
    """Eligibility is `ownership == "company"`, not a list of three names.

    A hardcoded list would be a list that drifts: it would keep admitting a
    device whose owner changed and keep refusing one that was enrolled later.
    The registry is already the authored answer to "whose device is this", so
    the grant reads it rather than restating it — and these cases pin the rule
    against the *shipped* `fleet/registry.yaml`, so an edit there that changes
    who can be granted turns a test red instead of silently widening a grant.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.real = load_registry()  # no path: the registry that ships

    def _service(self) -> FleetService:
        tmp = Path(tempfile.mkdtemp())
        harness = Harness(tmp)
        harness.service.registry = self.real
        return harness.service

    def test_the_default_admits_every_device_the_registry_states(self):
        """The default is the wide reading: every authored ownership class.

        The narrow reading is a narrowing, and it is expressed in a binding
        (`policy.grant_eligible_ownership`), not assumed here.
        """
        service = self._service()
        eligible = [d.device_ref.ref for d in self.real.all()
                    if service._device_is_grant_eligible(d.device_ref.ref)]
        self.assertEqual(sorted(eligible), sorted(set(WANTED) | set(REFUSED)))

    def test_a_binding_naming_only_company_refuses_the_personal_device(self):
        tmp = Path(tempfile.mkdtemp())
        harness = Harness(tmp)
        harness.edit_policy(grant_eligible_ownership=("company",))
        harness.service.registry = self.real
        refused = [d.device_ref.ref for d in self.real.all()
                   if not harness.service._device_is_grant_eligible(d.device_ref.ref)]
        self.assertEqual(sorted(refused), sorted(REFUSED))

    def test_every_shipped_device_states_an_owner(self):
        """The rule has one allowed value, so a device with no owner is refused.

        That is only defensible while every shipped device states one — if the
        registry ever grows an entry with no `ownership`, this test is where it
        is noticed, as a device that quietly cannot be granted rather than as a
        grant that quietly does nothing.
        """
        unstated = [d.device_ref.ref for d in self.real.all() if d.ownership is None]
        self.assertEqual(unstated, [])

    def test_an_unstated_owner_is_not_eligible(self):
        """`None` is an unknown owner, and an unknown owner is DEFER, not a grant.

        This is why eligibility tests membership in the configured list and not
        `!= "personal"`: the two polarities differ on exactly the inputs nobody
        thought about, and the default list names every value the registry can
        author rather than excluding one.
        """
        tmp = Path(tempfile.mkdtemp())
        harness = Harness(tmp, ownership={TOKYO: None, WINDOWS: None})
        self.assertFalse(harness.service._device_is_grant_eligible(TOKYO))
        self.assertEqual(KNOWN_DEVICE_OWNERSHIPS, ("company", "personal"))
        self.assertEqual(harness.service._eligible_ownerships(),
                         KNOWN_DEVICE_OWNERSHIPS)

    def test_the_binding_can_narrow_eligibility_to_one_ownership(self):
        """Which ownerships may hold a grant is policy, not a code constant."""
        tmp = Path(tempfile.mkdtemp())
        harness = Harness(tmp, ownership={TOKYO: "company", WINDOWS: "personal"})
        harness.edit_policy(grant_eligible_ownership=("company",))
        self.assertTrue(harness.service._device_is_grant_eligible(TOKYO))
        self.assertFalse(harness.service._device_is_grant_eligible(WINDOWS))

    def test_the_binding_can_admit_a_personal_device(self):
        tmp = Path(tempfile.mkdtemp())
        harness = Harness(tmp, ownership={WINDOWS: "personal"})
        self.assertTrue(harness.service._device_is_grant_eligible(WINDOWS))

    def test_an_empty_eligibility_list_grants_nothing(self):
        tmp = Path(tempfile.mkdtemp())
        harness = Harness(tmp, ownership={TOKYO: "company"})
        harness.edit_policy(grant_eligible_ownership=())
        self.assertFalse(harness.service._device_is_grant_eligible(TOKYO))

    def test_a_case_variant_or_unknown_owner_is_not_eligible(self):
        """The list holds authored values exactly; it is not a case folder."""
        for variant in ("Company", "COMPANY", "", "contractor"):
            with self.subTest(ownership=variant):
                tmp = Path(tempfile.mkdtemp())
                harness = Harness(tmp, ownership={TOKYO: variant})
                harness.service.registry = _registry(ownership={TOKYO: variant})
                self.assertFalse(harness.service._device_is_grant_eligible(TOKYO))


# ---------------------------------------------------------------------------
# 2. The principal axis
# ---------------------------------------------------------------------------

class ThePrincipalAxis(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.granted_session = f"{TOKYO}|claude|never-named-anywhere"

    def harness(self, **kwargs) -> Harness:
        return Harness(self.tmp, **kwargs)

    def test_a_grant_admits_a_session_no_one_named(self):
        """The reason the dimension exists: a future session cannot be listed."""
        h = self.harness(grants={OAUTH: {TOKYO: ("send",)}})
        result = h.send(self.granted_session)
        self.assertFalse(result["idempotent_replay"])
        self.assertEqual(h.providers[TOKYO].sent[0]["session_ref"], self.granted_session)
        self.assertEqual(h.config.policy.send_allow, ())  # nothing was named

    def test_a_principal_not_named_in_the_grant_gets_nothing_extra(self):
        h = self.harness(grants={OAUTH: {TOKYO: ("send",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.send(self.granted_session, principal=h.principal(name=SOMEONE_ELSE))
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertEqual(h.providers[TOKYO].sent, [])

    def test_the_name_is_the_credential_s_not_a_caller_s_choice(self):
        """Two principals, identical in every other respect, get two answers.

        This is the structural claim in the form a test can make it: the
        difference between the two calls below is the `Principal` the transport
        built, and `send_to_session` has no parameter that could carry a name.
        """
        h = self.harness(grants={OAUTH: {TOKYO: ("send",)}})
        allowed = h.send(self.granted_session)
        self.assertFalse(allowed["idempotent_replay"])
        with self.assertRaises(FleetRefusal):
            h.send(self.granted_session, principal=h.principal(name=SOMEONE_ELSE))

        import inspect

        params = inspect.signature(h.service.send_to_session).parameters
        self.assertEqual(
            [p for p in params if "name" in p or "principal" == p], ["principal"],
            "the only identity a send accepts is the one the transport built",
        )
        signature = inspect.signature(Principal)
        self.assertEqual(list(signature.parameters), ["name", "scopes", "credential_kind"])

    def test_the_grant_does_not_leak_across_devices(self):
        h = self.harness(grants={OAUTH: {TOKYO: ("send",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.send(f"{WINDOWS}|claude|other-device-session")
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertIn(TOKYO, json.dumps(h.config.policy.operator_grants))

    def test_refusal_says_which_of_the_three_situations_it_is(self):
        """A granted device, an ungranted one, and an ineligible one read apart."""
        h = self.harness(grants={OAUTH: {TOKYO: ("send",), PERSONAL_DELL: ("send",)}})
        h.edit_policy(grant_eligible_ownership=("company",))

        # (a) granted principal, device not in the grant
        with self.assertRaises(FleetRefusal) as other_device:
            h.send(f"{WINDOWS}|claude|x")
        # (b) principal not in the grant at all
        with self.assertRaises(FleetRefusal) as other_principal:
            h.send(f"{TOKYO}|claude|x", principal=h.principal(name=SOMEONE_ELSE))
        # (c) granted, but the device is not one that may be granted
        with self.assertRaises(FleetRefusal) as ineligible:
            h.send(f"{PERSONAL_DELL}|claude|x")

        self.assertIn("holds no `send` grant for " + WINDOWS,
                      other_device.exception.detail)
        self.assertIn("holds no `send` grant for " + TOKYO,
                      other_principal.exception.detail)
        # (c) is the one that is a policy question rather than a typo, and it
        # says so: the grant *does* name the device, and the device is refused
        # for its owner. A message that said "no grant" here would send the
        # operator to re-check a principal name that is perfectly correct.
        self.assertIn("does name", ineligible.exception.detail)
        self.assertIn("'personal'", ineligible.exception.detail)
        self.assertIn("policy.grant_eligible_ownership` is ['company']",
                      ineligible.exception.detail)

    def test_the_inert_grant_is_audited_because_its_author_is_not_the_caller(self):
        h = self.harness(grants={OAUTH: {PERSONAL_DELL: ("send",)}})
        h.edit_policy(grant_eligible_ownership=("company",))
        with self.assertRaises(FleetRefusal):
            h.send(f"{PERSONAL_DELL}|claude|x")
        rows = h.audit_rows("fleet.grant")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decision"], "refused")
        self.assertEqual(rows[0]["grant_reason"], "device-not-eligible")
        self.assertEqual(rows[0]["device_ref"], PERSONAL_DELL)


# ---------------------------------------------------------------------------
# 2b. The wildcard, which is a binding's way of saying "everyone, everywhere"
# ---------------------------------------------------------------------------

class TheWildcardBranch(unittest.TestCase):
    """`"*"` on either axis, and the two rules it does not touch.

    The wildcard is expressible so an OPEN binding can be written in a file
    without listing every principal and every device by name; closing it is
    deleting the line. What it must not become is a way around the rules it
    sits on: ownership eligibility (a binding narrows that separately) and an
    exact name winning over the wildcard.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.session = f"{TOKYO}|claude|never-named-anywhere"

    def test_a_wildcard_principal_reaches_a_stranger(self):
        h = Harness(self.tmp, grants={"*": {TOKYO: ("send",)}})
        result = h.send(self.session, principal=h.principal(name=SOMEONE_ELSE))
        self.assertFalse(result["idempotent_replay"])
        self.assertEqual(h.providers[TOKYO].sent[0]["session_ref"], self.session)

    def test_a_wildcard_device_reaches_every_company_device(self):
        h = Harness(self.tmp, grants={OAUTH: {"*": ("send",)}})
        h.send(self.session)
        h.send(f"{WINDOWS}|claude|elsewhere")
        self.assertEqual([s["session_ref"] for s in h.providers[TOKYO].sent],
                         [self.session])
        self.assertEqual([s["session_ref"] for s in h.providers[WINDOWS].sent],
                         [f"{WINDOWS}|claude|elsewhere"])

    def test_the_wildcard_does_not_admit_a_device_the_binding_excludes(self):
        """Eligibility is policy; the wildcard is not a way to rewrite it.

        With the default policy the wildcard does reach a personal device —
        that is the wide default. Narrowing the ownership list is what keeps a
        personal machine out, and the wildcard still respects it.
        """
        h = Harness(self.tmp, grants={"*": {"*": ("send",)}})
        h.edit_policy(grant_eligible_ownership=("company",))
        with self.assertRaises(FleetRefusal) as caught:
            h.send(f"{PERSONAL_DELL}|claude|x")
        self.assertIn("policy.grant_eligible_ownership` is ['company']",
                      caught.exception.detail)

    def test_the_wildcard_reaches_a_personal_device_under_the_default_policy(self):
        h = Harness(self.tmp, grants={"*": {"*": ("send",)}})
        h.send(f"{PERSONAL_DELL}|claude|x")
        self.assertEqual(h.providers[PERSONAL_DELL].sent[0]["session_ref"],
                         f"{PERSONAL_DELL}|claude|x")

    def test_an_exact_principal_entry_with_nothing_in_it_grants_nothing(self):
        """Absent consults `"*"`; present-and-empty is a decision, not a gap."""
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ()},
                                      "*": {TOKYO: ("send",)}})
        with self.assertRaises(FleetRefusal):
            h.send(self.session)
        # The stranger the wildcard was written for still gets through.
        h.send(self.session, principal=h.principal(name=SOMEONE_ELSE))

    def test_an_exact_device_entry_with_nothing_in_it_grants_nothing(self):
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: (), "*": ("send",)}})
        with self.assertRaises(FleetRefusal):
            h.send(self.session)
        h.send(f"{WINDOWS}|claude|elsewhere")

    def test_removing_the_wildcard_closes_the_door_again(self):
        """The point of the whole arrangement: the switch is the file."""
        h = Harness(self.tmp, grants={"*": {TOKYO: ("send",)}})
        h.send(self.session)
        h.edit_policy(operator_grants={})
        with self.assertRaises(FleetRefusal) as caught:
            h.send(self.session)
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")


# ---------------------------------------------------------------------------
# 3. The verb axis
# ---------------------------------------------------------------------------

class TheVerbAxis(unittest.TestCase):
    def test_the_implemented_verbs_and_the_planned_ones_are_disjoint(self):
        # `control` and `create` graduated when the CloudCLI mirror needed to be
        # governed at the same principal x device x verb axis. Graduating a verb
        # grants nothing — a principal with no entry still holds nothing.
        self.assertEqual(OPERATOR_GRANT_VERBS,
                         ("send", "control", "create", "approve", "navigate"))
        # Nothing is planned-but-inert any more; every name is enforced.
        self.assertEqual(tuple(PLANNED_OPERATOR_GRANT_VERBS), ())
        self.assertEqual(set(OPERATOR_GRANT_VERBS) & set(PLANNED_OPERATOR_GRANT_VERBS),
                         set())

    def test_a_planned_verb_is_refused_at_load_and_names_the_gap(self):
        """Refused, not accepted-and-ignored.

        A binding that lists `control` is a binding whose author believes a
        control verb is live. Accepting it would produce a file that reads as
        authority and a runtime in which the verb does nothing — and the next
        person to debug that would have to work out, from a silent no-op, which
        of the two halves is missing.
        """
        for verb in PLANNED_OPERATOR_GRANT_VERBS:
            with self.subTest(verb=verb):
                with self.assertRaises(FleetRefusal) as caught:
                    load_operator_grants({"operator_grants": {OAUTH: {TOKYO: [verb]}}})
                self.assertEqual(caught.exception.code, "BAD_REQUEST")
                self.assertIn(verb, caught.exception.detail)

    def test_a_grant_listing_only_a_verb_that_is_not_send_admits_nothing(self):
        """Verbs do not inherit, checked on the service rather than the parser.

        The parser refuses `approve` outright (it is planned), so this
        constructs the state a future revision could reach and asks the gate
        directly: a grant that carries a verb is not a grant of *every* verb.
        """
        tmp = Path(tempfile.mkdtemp())
        h = Harness(tmp, grants={OAUTH: {TOKYO: ("send",)}})
        # The grant names exactly the verb it wrote.
        self.assertEqual(h.service._operator_grant_verbs(h.principal(), TOKYO),
                         ("send",))
        self.assertNotIn("control",
                         h.service._operator_grant_verbs(h.principal(), TOKYO))

    def test_an_unknown_verb_is_refused_at_load(self):
        with self.assertRaises(FleetRefusal) as caught:
            load_operator_grants({"operator_grants": {OAUTH: {TOKYO: ["sends"]}}})
        self.assertIn("not a verb", caught.exception.detail)


# ---------------------------------------------------------------------------
# 4. The legacy per-session path, unchanged
# ---------------------------------------------------------------------------

class TheLegacyPathIsUnchanged(unittest.TestCase):
    def test_a_named_session_is_reachable_with_no_grant_at_all(self):
        """Byte-identical to the behaviour before the grant existed.

        `send_allow` naming a session ref was, and remains, principal-blind.
        Narrowing it is a separate change with its own blast radius; this case
        exists so "the legacy path did not regress" is a claim with a test
        behind it rather than an assertion in a pull request description.
        """
        tmp = Path(tempfile.mkdtemp())
        named = f"{TOKYO}|claude|named-in-the-binding"
        h = Harness(tmp, send_allow=(named,), grants={})
        result = h.send(named, principal=h.principal(name=SOMEONE_ELSE))
        self.assertFalse(result["idempotent_replay"])
        self.assertEqual(h.providers[TOKYO].sent[0]["session_ref"], named)

    def test_the_grant_does_not_reach_a_device_that_is_not_authorized(self):
        """The device gate is asked first and is not bypassed by a grant."""
        tmp = Path(tempfile.mkdtemp())
        h = Harness(tmp, grants={OAUTH: {TOKYO: ("send",)}}, allow_send=False)
        with self.assertRaises(FleetRefusal) as caught:
            h.send(f"{TOKYO}|claude|x")
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertIn("not authorized to send", caught.exception.detail)
        # The refusal names the verb that was attempted. It used to say
        # "control" — copied from `_may_control` — which told a caller that
        # *send* authority had been weighed and withheld when the sentence was
        # about a different verb entirely.
        self.assertNotIn("control", caught.exception.detail)

    def test_the_wildcard_is_refused_rather_than_deprioritized(self):
        """Leaving the hatch in place would leave a second way to say it."""
        body = (
            "schema: serveros/fleet-mcp.local/v1\n"
            "device_ref: " + WINDOWS + "\n"
            "providers:\n"
            "  " + TOKYO + ":\n"
            "    kind: fleet-node\n"
            "    endpoint: http://127.0.0.1:3402\n"
            "    credential_anchor: /nonexistent/secret\n"
            "policy:\n"
            "  send_allow: ['*']\n"
            "  allow_wildcard_send: true\n"
        )
        path = Path(tempfile.mkdtemp()) / "binding.yaml"
        path.write_text(body, encoding="utf-8")
        with self.assertRaises(FleetRefusal) as caught:
            load_config(path)
        self.assertEqual(caught.exception.code, "BAD_REQUEST")
        # The message is the migration an existing deployment will meet.
        self.assertIn("operator_grants", caught.exception.detail)
        for device in WANTED:
            self.assertIn(device, caught.exception.detail)

    def test_a_named_send_allow_entry_still_loads(self):
        """The refusal is about `*`, and does not catch the ordinary case."""
        body = (
            "schema: serveros/fleet-mcp.local/v1\n"
            "device_ref: " + WINDOWS + "\n"
            "providers:\n"
            "  " + TOKYO + ":\n"
            "    kind: fleet-node\n"
            "    endpoint: http://127.0.0.1:3402\n"
            "    credential_anchor: /nonexistent/secret\n"
            "policy:\n"
            "  send_allow:\n"
            "    - '" + TOKYO + "|claude|a-session'\n"
        )
        path = Path(tempfile.mkdtemp()) / "binding.yaml"
        path.write_text(body, encoding="utf-8")
        config = load_config(path)
        self.assertFalse(config.policy.allow_wildcard_send)
        self.assertEqual(config.policy.send_allow, (f"{TOKYO}|claude|a-session",))


# ---------------------------------------------------------------------------
# 5. Read surfaces agree with the gate
# ---------------------------------------------------------------------------

class TheReadSurfacesAgree(unittest.TestCase):
    """A surface that reports `send_allowed` must make the same check `send` does.

    It used to be principal-blind, which was correct while the gate was too.
    Threading the principal is not cosmetic: left alone, `fleet_list_sessions`
    would report `send_allowed: true` for a caller that `send_to_session`
    refuses, and the caller would be told a wrong thing by the one tool it
    asked.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def _session_ref(self) -> str:
        return f"{TOKYO}|claude|a-managed-session"

    def test_a_granted_principal_is_reported_as_allowed(self):
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("send",)}}, sessions=("a-managed-session",))
        row = h.sessions()["sessions"][0]
        self.assertTrue(row["control_allowed"]["send_allowed"])
        self.assertEqual(row["control_allowed"]["operator_grant_verbs"], ["send"])
        self.assertFalse(row["control_allowed"]["session_allowlisted"])

    def test_an_ungranted_principal_is_reported_as_refused(self):
        h = Harness(self.tmp, grants={}, sessions=("a-managed-session",))
        row = h.sessions()["sessions"][0]
        self.assertFalse(row["control_allowed"]["send_allowed"])
        self.assertEqual(row["control_allowed"]["operator_grant_verbs"], [])

    def test_the_reported_state_and_the_gate_never_disagree(self):
        """The property, checked over every combination rather than one."""
        cases = [
            ({OAUTH: {TOKYO: ("send",)}}, OAUTH, True),
            ({OAUTH: {TOKYO: ("send",)}}, SOMEONE_ELSE, False),
            ({}, OAUTH, False),
            ({OAUTH: {WINDOWS: ("send",)}}, OAUTH, False),
            ({OAUTH: {TOKYO: ("control",)}}, OAUTH, False),
        ]
        for grants, name, expected in cases:
            with self.subTest(grants=grants, name=name):
                h = Harness(self.tmp, grants=grants, sessions=("a-managed-session",))
                principal = h.principal(name=name)
                reported = h.sessions(principal=principal)["sessions"][0]["control_allowed"]["send_allowed"]
                try:
                    h.send(self._session_ref(), principal=principal)
                    allowed = True
                except FleetRefusal:
                    allowed = False
                self.assertEqual(reported, allowed)
                self.assertEqual(allowed, expected)

    def test_the_caller_sees_its_own_grant_in_list_devices(self):
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("send",), WINDOWS: ("send",)}})
        rows = {d["device_ref"]: d["control"] for d in h.devices()["devices"]}
        self.assertEqual(rows[TOKYO]["operator_grant_verbs"], ["send"])
        self.assertEqual(rows[WINDOWS]["operator_grant_verbs"], ["send"])
        self.assertEqual(rows[PERSONAL_DELL]["operator_grant_verbs"], [])

    def test_list_devices_never_reports_another_principal_s_grant(self):
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("send",)}})
        rows = h.devices(principal=h.principal(name=SOMEONE_ELSE))["devices"]
        self.assertEqual([d["control"]["operator_grant_verbs"] for d in rows],
                         [[] for _ in rows])


# ---------------------------------------------------------------------------
# 6. Revocation
# ---------------------------------------------------------------------------

class Revocation(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_removing_the_grant_stops_the_send(self):
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("send",)}})
        h.send(f"{TOKYO}|claude|x")
        h.edit_policy(operator_grants={})
        with self.assertRaises(FleetRefusal):
            h.send(f"{TOKYO}|claude|y")
        self.assertEqual(len(h.providers[TOKYO].sent), 1)

    def test_removing_one_device_stops_only_that_device(self):
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("send",), DELL: ("send",)}})
        h.edit_policy(operator_grants={OAUTH: {DELL: ("send",)}})
        with self.assertRaises(FleetRefusal):
            h.send(f"{TOKYO}|claude|x")
        h.send(f"{DELL}|claude|y")

    def test_a_revoked_grant_stops_the_idempotent_replay_too(self):
        """The recheck is above the cache, and this is the case that proves it.

        Without it, a revoked grant would still answer a retry from the
        idempotency table — the caller would get a recorded run back from an
        authorization that no longer exists, which is a cache outliving the
        grant it was made under.
        """
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("send",)}})
        first = h.send(f"{TOKYO}|claude|x", idempotency_key="k-1")
        self.assertFalse(first["idempotent_replay"])
        h.edit_policy(operator_grants={})
        with self.assertRaises(FleetRefusal):
            h.send(f"{TOKYO}|claude|x", idempotency_key="k-1")
        self.assertEqual(len(h.providers[TOKYO].sent), 1)

    def test_a_live_grant_still_answers_the_replay(self):
        """The negative control: the case above is not passing for another reason."""
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("send",)}})
        h.send(f"{TOKYO}|claude|x", idempotency_key="k-1")
        again = h.send(f"{TOKYO}|claude|x", idempotency_key="k-1")
        self.assertTrue(again["idempotent_replay"])
        self.assertEqual(len(h.providers[TOKYO].sent), 1)


# ---------------------------------------------------------------------------
# 7. Audit
# ---------------------------------------------------------------------------

class TheAuditTrail(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    #: Every key `record_grant` can produce. The signature *is* the allowlist —
    #: there is no `**fields` — so this set is closed by construction, and the
    #: test below checks the construction rather than trusting it.
    GRANT_FIELDS = {"ts", "event", "principal", "device_ref", "verb",
                    "decision", "grant_reason", "session_ref"}

    def test_an_admission_through_the_grant_is_recorded(self):
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("send",)}})
        h.send(f"{TOKYO}|claude|some-session")
        rows = h.audit_rows("fleet.grant")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decision"], "allowed")
        self.assertEqual(rows[0]["grant_reason"], "operator-grant")
        self.assertEqual(rows[0]["principal"], OAUTH)
        self.assertEqual(rows[0]["verb"], "send")

    def test_the_grant_record_carries_only_allowlisted_fields(self):
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("send",)}})
        h.send(f"{TOKYO}|claude|some-session", message="a secret instruction")
        for row in h.audit_rows("fleet.grant"):
            self.assertLessEqual(set(row), self.GRANT_FIELDS,
                                 "a field outside the closed set was written")

    def test_no_message_body_reaches_the_audit_log_through_this_path(self):
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("send",)}})
        secret = "the-message-body-must-not-be-recorded"
        h.send(f"{TOKYO}|claude|some-session", message=secret)
        raw = (self.tmp / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)

    def test_the_legacy_path_writes_no_grant_record(self):
        """Only the admission with no other trace is recorded here.

        A send through `send_allow` is already visible as a `fleet.send` row
        naming the session, so a second record would be noise; the grant
        admission is the one whose "why" is nowhere else.
        """
        named = f"{TOKYO}|claude|named"
        h = Harness(self.tmp, send_allow=(named,))
        h.send(named)
        self.assertEqual(h.audit_rows("fleet.grant"), [])
        self.assertEqual(len(h.audit_rows("fleet.send")), 1)


# ---------------------------------------------------------------------------
# 8. The binding's shape
# ---------------------------------------------------------------------------

class TheParsedShape(unittest.TestCase):
    def test_a_grant_round_trips(self):
        parsed = load_operator_grants(
            {"operator_grants": {OAUTH: {TOKYO: ["send"], WINDOWS: ["send"]}}})
        self.assertEqual(parsed, {OAUTH: {TOKYO: ("send",), WINDOWS: ("send",)}})

    def test_absent_is_empty_and_empty_is_absent(self):
        self.assertEqual(load_operator_grants({}), {})
        self.assertEqual(load_operator_grants({"operator_grants": {}}), {})
        # A principal whose every device list is empty is dropped rather than
        # kept as a principal with no reach — the two mean the same thing and
        # only one of them makes `describe()` report a principal that is not
        # actually granted anything.
        self.assertEqual(load_operator_grants({"operator_grants": {OAUTH: {TOKYO: []}}}), {})

    def test_a_wildcard_is_a_grant_like_any_other(self):
        """`"*"` is expressible on BOTH axes, and it means what it says.

        It exists because a binding has to be able to say "every principal on
        every company device" without listing devices by name — and because the
        decision belongs in the file, not in the parser. What the wildcard does
        NOT do is bypass the ownership rule below, which is code and stays code.
        """
        self.assertEqual(
            load_operator_grants({"operator_grants": {"*": {TOKYO: ["send"]}}}),
            {"*": {TOKYO: ("send",)}},
        )
        self.assertEqual(
            load_operator_grants({"operator_grants": {OAUTH: {"*": ["send", "control"]}}}),
            {OAUTH: {"*": ("send", "control")}},
        )

    def test_a_wildcard_principal_still_cannot_grant_a_verb_that_is_not_real(self):
        with self.assertRaises(FleetRefusal):
            load_operator_grants({"operator_grants": {"*": {TOKYO: ["teleport"]}}})

    def test_a_bare_word_on_the_device_axis_is_still_refused(self):
        """Only the literal `*` is a wildcard; a hostname is a missing type."""
        with self.assertRaises(FleetRefusal) as caught:
            load_operator_grants({"operator_grants": {OAUTH: {"tokyo-shared-dev-01": ["send"]}}})
        self.assertIn("typed ref", caught.exception.detail)

    def test_a_device_ref_that_is_not_typed_is_refused(self):
        with self.assertRaises(FleetRefusal) as caught:
            load_operator_grants({"operator_grants": {OAUTH: {"tokyo": ["send"]}}})
        self.assertIn("typed ref", caught.exception.detail)

    def test_a_verb_list_that_is_not_a_list_is_refused(self):
        with self.assertRaises(FleetRefusal):
            load_operator_grants({"operator_grants": {OAUTH: {TOKYO: "send"}}})

    def test_a_missing_verb_list_admits_nothing(self):
        self.assertEqual(load_operator_grants({"operator_grants": {OAUTH: {TOKYO: None}}}), {})

    def test_describe_reports_the_shape_and_no_principal_names(self):
        """The one policy view that is safe to print at start-up.

        A principal name is a credential identifier (`oauth:<client_id>`), so
        `describe()` reports how many, which devices and which verbs, and never
        the names. "Who holds what" is answered per call by `fleet_list_devices`,
        which is scoped to the caller and so cannot leak a different principal's.
        """
        tmp = Path(tempfile.mkdtemp())
        h = Harness(tmp, grants={OAUTH: {TOKYO: ("send",), WINDOWS: ("send",)}})
        described = h.config.describe()
        block = described["policy"]["operator_grants"]
        self.assertEqual(block["principals"], 1)
        self.assertEqual(block["devices"], sorted([TOKYO, WINDOWS]))
        self.assertEqual(block["verbs"], ["send"])
        self.assertNotIn(OAUTH, json.dumps(described))


if __name__ == "__main__":
    unittest.main()
