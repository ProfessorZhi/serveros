#!/usr/bin/env python3
"""The convergence of the two `fleet_create_session` branches: what each kept.

Two sibling branches grew the same tool from the same base and neither
contained the other. This file is the admission question for merging them, and
every case below is a property that only one of the two had — so a case that
fails here is a branch being silently dropped rather than a branch being
merged:

*   **The project allowlist is closed** (from one branch): a device authorized
    for creation is not an authorization for *every* project on it.
*   **The idempotency key is scoped to device, project and verb** (neither had
    it; the shared table was keyed by credential and key alone). Two bugs lived
    in that: the same key against a different device returned the first device's
    session, and a key spent on `send` answered a later `create` with a create
    result that had never been created. Both are reproduced below as negative
    controls against the fixed identity.
*   **A revoked grant stops answering a replay** (from one branch): the
    authorization is re-established on every call, so the idempotency cache is
    never the authority on whether the call may happen.
*   **The caller's `permissionMode` and `model` are constrained to the project's
    own policy** (neither had it): `create` must not be a route to widening
    one's own authority, and an empty ceiling means the caller may name neither.
*   **The scope vocabulary is written once** (from one branch): the declared
    set, the grantable ceiling and the token's claims all read the same list,
    and the default ceiling is read+send — not read+send+create.

Nothing here touches a real device: every provider is a fake that records what
it was asked, and every assertion is about what the *service* did or refused to
do before it asked.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import scopes  # noqa: E402
from fleet_mcp.config import (  # noqa: E402
    AuditBinding,
    AuthBinding,
    CreateGrant,
    FleetConfig,
    PolicyBinding,
    ProviderBinding,
    ServerBinding,
    load_config,
)
from fleet_mcp.errors import FleetRefusal  # noqa: E402
from fleet_mcp.fleet import (  # noqa: E402
    SCOPE_CREATE,
    SCOPE_READ,
    SCOPE_SEND,
    FleetService,
    Principal,
)
from fleet_mcp.identity import SessionKey  # noqa: E402
from fleet_mcp.providers.base import ProviderHealth  # noqa: E402

TOKYO = "device:tokyo-shared-dev-01"
WINDOWS = "device:work-fj"
ALL = (TOKYO, WINDOWS)


class FakeProvider:
    """A device that records exactly what it was asked, in CloudCLI's shape."""

    kind = "cloudcli-mcp-http"

    def __init__(self, device_ref: str) -> None:
        self.device_ref = device_ref
        self.created: list[dict] = []
        self.sent: list[dict] = []

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            device_ref=self.device_ref, kind=self.kind, reachable=True, authorized=True,
            detail="fake", server_name="claudecodeui-mcp-gateway", server_version="0.1.0",
            tool_count=23, latency_ms=1.0,
        )

    def list_sessions(self, *, limit: int, project=None, state=None):
        return []

    def get_session(self, key):
        raise FleetRefusal("SESSION_NOT_FOUND", "no such session on this device")

    def read_session(self, key, **kwargs):
        return {"messages": []}

    def send(self, key, message, *, idempotency_key=None):
        self.sent.append({"session_ref": key.ref, "message": message,
                          "idempotency_key": idempotency_key})
        return {"runId": f"run-send-{len(self.sent)}", "accepted": True}

    def create(self, *, project, message=None, provider="claude", model=None,
               lifecycle_mode=None, permission_mode=None):
        self.created.append({
            "project": project, "message": message, "provider": provider,
            "model": model, "lifecycle_mode": lifecycle_mode,
            "permission_mode": permission_mode,
        })
        run_id = f"run-{len(self.created)}" if message else None
        return {"sessionId": f"created-{len(self.created)}", **({"runId": run_id} if run_id else {})}


def _binding(device_ref: str, *, allow_create: bool, allow_send: bool, anchor: Path) -> ProviderBinding:
    return ProviderBinding(
        device_ref=device_ref, kind="cloudcli-mcp-http",
        endpoint=f"http://{device_ref}/mcp", credential_anchor=anchor,
        allow_send=allow_send, allow_create=allow_create,
    )


class Harness:
    """A service over two devices, with both gates stated explicitly.

    `create_allow` and `send_allow` are the two per-scope allowlists and neither
    is defaulted: an empty one of either means refusal, which is the property
    most of this file is about.
    """

    def __init__(self, tmp: Path, *, create_allow=None, send_allow=(),
                 allow_send=True, allow_create=True, wildcard_create=False):
        anchor = tmp / "pat"
        anchor.write_text("owner-token-value", encoding="utf-8")
        if create_allow is None:
            create_allow = {"p-1": CreateGrant()}
        self.config = FleetConfig(
            source=None,
            local_device_ref=WINDOWS,
            providers={
                ref: _binding(ref, allow_create=allow_create, allow_send=allow_send, anchor=anchor)
                for ref in ALL
            },
            auth=AuthBinding(pat_anchor=anchor),
            server=ServerBinding(bind="127.0.0.1", port=0),
            policy=PolicyBinding(create_allow=create_allow, send_allow=tuple(send_allow),
                                 allow_wildcard_create=wildcard_create),
            audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        )
        self.providers = {ref: FakeProvider(ref) for ref in ALL}
        self.service = FleetService(self.config, providers=dict(self.providers))

    def principal(self, *scopes_, name="tester"):
        return Principal(name=name,
                         scopes=frozenset(scopes_ or (SCOPE_READ, SCOPE_SEND, SCOPE_CREATE)),
                         credential_kind="pat")

    def create(self, device=TOKYO, project="p-1", **kwargs):
        return self.service.create_session(self.principal(), device=device, project=project, **kwargs)

    def send(self, session_ref, **kwargs):
        return self.service.send_to_session(self.principal(), SessionKey.parse(session_ref), **kwargs)

    # -- the operator's hand on the binding, mid-run ----------------------
    #
    # Both configs are frozen, so "the operator edited the file" is a rebuild
    # rather than an attribute assignment. That is the point: these edits stand
    # in for a re-read binding, and the service must pick the new answer up on
    # the next call instead of caching the old one.

    def edit_policy(self, **changes) -> None:
        self.config = replace(self.config, policy=replace(self.config.policy, **changes))
        self.service.config = self.config

    def edit_provider(self, device_ref: str, **changes) -> None:
        providers = dict(self.config.providers)
        providers[device_ref] = replace(providers[device_ref], **changes)
        self.config = replace(self.config, providers=providers)
        self.service.config = self.config


class TheKeyIsScopedToWhatItWasAKeyFor(unittest.TestCase):
    """One key names one attempt: same device, same project, same verb.

    Each case below is a *negative control* — it asserts that the cache does NOT
    answer — followed by the positive control that the same key really does
    replay when nothing differs. Without the positive control a broken cache
    that never replays would pass every negative one.
    """

    def test_the_same_key_on_a_different_device_does_not_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            first = h.create(device=TOKYO, idempotency_key="k-1")
            second = h.create(device=WINDOWS, idempotency_key="k-1")
        self.assertFalse(second["idempotent_replay"],
                         "a key spent on one device answered for another; the caller "
                         "would be handed a session id minted somewhere else")
        self.assertEqual(second["device_ref"], WINDOWS)
        self.assertEqual(len(h.providers[TOKYO].created), 1)
        self.assertEqual(len(h.providers[WINDOWS].created), 1,
                         "the second device was never asked")
        self.assertNotEqual(first["session_ref"], second["session_ref"])

    def test_the_same_key_on_a_different_project_does_not_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), create_allow={"p-1": CreateGrant(), "p-2": CreateGrant()})
            first = h.create(device=TOKYO, project="p-1", idempotency_key="k-1")
            second = h.create(device=TOKYO, project="p-2", idempotency_key="k-1")
        self.assertFalse(second["idempotent_replay"],
                         "a key spent on one project answered for another; the session "
                         "would be in the wrong working tree and look valid")
        self.assertEqual(len(h.providers[TOKYO].created), 2)
        self.assertEqual(h.providers[TOKYO].created[1]["project"], "p-2")
        self.assertNotEqual(first["session_ref"], second["session_ref"])

    def test_a_key_spent_on_send_does_not_answer_a_create(self):
        """`send` and `create` share one table, so the verb has to be in the key.

        Without it the create returns the *send* record, whose shape is a run —
        so the caller is told a session was created, gets a `run_id` that belongs
        to a message someone else's session received, and no session is minted.
        """
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), send_allow=(f"{TOKYO}|claude|existing-1",))
            sent = h.send(f"{TOKYO}|claude|existing-1", message="go", idempotency_key="k-1")
            created = h.create(device=TOKYO, idempotency_key="k-1")
        self.assertFalse(created["idempotent_replay"],
                         "a send key answered a create; the two verbs share a table and "
                         "the verb was not part of the identity")
        self.assertEqual(created["session_ref"], f"{TOKYO}|claude|created-1")
        self.assertEqual(len(h.providers[TOKYO].created), 1)
        self.assertEqual(len(h.providers[TOKYO].sent), 1)
        # The send's own replay still works — the verb split the namespaces, it
        # did not break either one.
        self.assertEqual(sent["run"]["run_id"], "run-send-1")

    def test_the_same_key_on_the_same_attempt_still_replays(self):
        """The positive control: scoping the key must not disable replay."""
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            first = h.create(device=TOKYO, project="p-1", message="go", idempotency_key="k-1")
            second = h.create(device=TOKYO, project="p-1", message="go", idempotency_key="k-1")
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(second["code"], "IDEMPOTENT_REPLAY")
        self.assertEqual(second["session_ref"], first["session_ref"])
        self.assertEqual(len(h.providers[TOKYO].created), 1)

    def test_the_key_is_still_scoped_to_the_credential_that_spent_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            h.service.create_session(h.principal(name="alice"), device=TOKYO,
                                     project="p-1", idempotency_key="same")
            other = h.service.create_session(h.principal(name="bob"), device=TOKYO,
                                             project="p-1", idempotency_key="same")
        self.assertFalse(other["idempotent_replay"])
        self.assertEqual(len(h.providers[TOKYO].created), 2)


class ARevokedGrantStopsAnsweringAReplay(unittest.TestCase):
    """The cache records what happened. It does not decide what may happen."""

    def test_removing_the_project_makes_the_replay_a_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            h.create(device=TOKYO, project="p-1", idempotency_key="k-1")
            # The operator edits the binding: this project is no longer authorized.
            h.edit_policy(create_allow={})
            with self.assertRaises(FleetRefusal) as caught:
                h.create(device=TOKYO, project="p-1", idempotency_key="k-1")
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertEqual(len(h.providers[TOKYO].created), 1,
                         "the replay was answered without asking; the cache was the authority")

    def test_withdrawing_the_device_grant_makes_the_replay_a_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            h.create(device=TOKYO, project="p-1", idempotency_key="k-1")
            h.edit_provider(TOKYO, allow_create=False)
            with self.assertRaises(FleetRefusal) as caught:
                h.create(device=TOKYO, project="p-1", idempotency_key="k-1")
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")

    def test_a_send_replay_is_rechecked_against_the_session_allowlist(self):
        ref = f"{TOKYO}|claude|existing-1"
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), send_allow=(ref,))
            h.send(ref, message="go", idempotency_key="k-1")
            h.edit_policy(send_allow=())  # the operator withdraws the session
            with self.assertRaises(FleetRefusal) as caught:
                h.send(ref, message="go", idempotency_key="k-1")
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertEqual(len(h.providers[TOKYO].sent), 1)

    def test_the_replay_recomputes_control_rather_than_replaying_it(self):
        ref = f"{TOKYO}|claude|created-1"
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            first = h.create(device=TOKYO, project="p-1", idempotency_key="k-1")
            self.assertFalse(first["control"]["send_allowed"])
            h.edit_provider(TOKYO, allow_send=True)
            h.edit_policy(send_allow=(ref,))
            second = h.create(device=TOKYO, project="p-1", idempotency_key="k-1")
        self.assertTrue(second["idempotent_replay"])
        self.assertTrue(second["control"]["send_allowed"],
                        "the replay returned a stale control block; control is a question "
                        "about policy now, not a fact about the past")


class TheProjectAllowlistIsClosed(unittest.TestCase):
    def test_an_empty_allowlist_refuses_every_project(self):
        """An allowlist that is empty by omission means none, never all."""
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), create_allow={})
            with self.assertRaises(FleetRefusal) as caught:
                h.create(device=TOKYO, project="p-1")
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertEqual(h.providers[TOKYO].created, [],
                         "the device was asked before the project was checked")

    def test_an_unlisted_project_is_refused_before_the_device_is_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), create_allow={"p-1": CreateGrant()})
            with self.assertRaises(FleetRefusal) as caught:
                h.create(device=TOKYO, project="p-other")
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertIn("p-other", caught.exception.detail)
        self.assertEqual(h.providers[TOKYO].created, [])

    def test_the_wildcard_is_refused_at_load_unless_it_is_asked_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "binding.yaml"
            path.write_text(
                _binding_yaml(policy_extra='create_allow:\n    "*": {}\n'), encoding="utf-8"
            )
            with self.assertRaises(FleetRefusal) as caught:
                load_config(path)
        self.assertEqual(caught.exception.code, "BAD_REQUEST")
        self.assertIn("allow_wildcard_create", caught.exception.detail)

    def test_the_wildcard_is_a_deliberate_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "binding.yaml"
            path.write_text(
                _binding_yaml(
                    policy_extra='create_allow:\n    "*": {}\n  allow_wildcard_create: true\n'
                ),
                encoding="utf-8",
            )
            config = load_config(path)
        self.assertEqual(sorted(config.policy.create_allow), ["*"])
        self.assertTrue(config.policy.allow_wildcard_create)

    def test_the_allowlist_does_not_widen_the_device_gate(self):
        """Two gates, both needed: a listed project on an unauthorized device is
        still refused."""
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), allow_create=False)
            with self.assertRaises(FleetRefusal) as caught:
                h.create(device=TOKYO, project="p-1")
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertIn("allow_create", caught.exception.detail)


class TheCallerCannotWidenItsOwnAuthority(unittest.TestCase):
    """`create` is not a route around `send`: naming a mode is not a grant."""

    def test_an_empty_ceiling_refuses_any_named_permission_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), create_allow={"p-1": CreateGrant()})
            with self.assertRaises(FleetRefusal) as caught:
                h.create(device=TOKYO, project="p-1", permission_mode="bypassPermissions")
        self.assertEqual(caught.exception.code, "WRITE_NOT_GRANTED")
        self.assertIn("bypassPermissions", caught.exception.detail)
        self.assertEqual(h.providers[TOKYO].created, [],
                         "the device was asked before the mode was checked")

    def test_an_empty_ceiling_refuses_any_named_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), create_allow={"p-1": CreateGrant()})
            with self.assertRaises(FleetRefusal) as caught:
                h.create(device=TOKYO, project="p-1", model="some-other-backend")
        self.assertEqual(caught.exception.code, "WRITE_NOT_GRANTED")
        self.assertEqual(h.providers[TOKYO].created, [])

    def test_naming_nothing_still_works_and_the_provider_default_applies(self):
        """The ceiling constrains what the caller *says*, not the provider.

        A caller that supplies neither gets the owner's own default, which the
        operator who configured that provider has already accepted — so an empty
        ceiling must not make every create impossible.
        """
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), create_allow={"p-1": CreateGrant()})
            h.create(device=TOKYO, project="p-1", message="go")
        self.assertEqual(h.providers[TOKYO].created,
                         [{"project": "p-1", "message": "go", "provider": "claude",
                           "model": None, "lifecycle_mode": None, "permission_mode": None}])

    def test_a_named_mode_the_project_authorizes_is_carried_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), create_allow={
                "p-1": CreateGrant(permission_modes=("plan",), models=("m-1",)),
            })
            h.create(device=TOKYO, project="p-1", model="m-1", permission_mode="plan")
        self.assertEqual(h.providers[TOKYO].created[0]["permission_mode"], "plan")
        self.assertEqual(h.providers[TOKYO].created[0]["model"], "m-1")

    def test_one_projects_latitude_is_not_another_projects(self):
        """The ceiling is per project because that is the scope the answer is
        known at; a flat allowlist would have to answer for both with one word."""
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), create_allow={
                "p-open": CreateGrant(permission_modes=("bypassPermissions",)),
                "p-closed": CreateGrant(),
            })
            h.create(device=TOKYO, project="p-open", permission_mode="bypassPermissions")
            with self.assertRaises(FleetRefusal) as caught:
                h.create(device=TOKYO, project="p-closed", permission_mode="bypassPermissions")
        self.assertEqual(caught.exception.code, "WRITE_NOT_GRANTED")
        self.assertEqual(len(h.providers[TOKYO].created), 1)

    def test_the_ceiling_does_not_apply_to_an_unnamed_lifecycle_mode(self):
        """Only `permission_mode` and `model` are constrained — `lifecycle_mode`
        is not a privilege, and refusing it here would be a policy this surface
        never meant to state."""
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            h.create(device=TOKYO, project="p-1", lifecycle_mode="resident")
        self.assertEqual(h.providers[TOKYO].created[0]["lifecycle_mode"], "resident")


class TheScopeVocabularyIsWrittenOnce(unittest.TestCase):
    def test_the_implemented_set_is_the_advertised_set(self):
        self.assertEqual(scopes.ALLOWED_SCOPES, scopes.IMPLEMENTED_SCOPES)
        self.assertEqual(sorted(scopes.ALLOWED_SCOPES),
                         ["fleet:create", "fleet:read", "fleet:send"])

    def test_planned_scopes_are_named_but_never_issuable(self):
        """Naming a verb we owe is fine; issuing it is not. A token for a verb no
        code path enforces fails at the call, which the client reads as a device
        that did not answer rather than as a scope it never had."""
        for planned in scopes.PLANNED_SCOPES:
            with self.subTest(scope=planned):
                self.assertNotIn(planned, scopes.ALLOWED_SCOPES)
                self.assertFalse(scopes.requires_implemented_scope(planned))

    def test_the_oauth_module_reads_the_same_list(self):
        from fleet_mcp import oauth

        self.assertIs(oauth.ALLOWED_SCOPES, scopes.ALLOWED_SCOPES)

    def test_the_default_ceiling_is_read_and_send_not_create(self):
        self.assertEqual(scopes.DEFAULT_GRANTABLE_SCOPES, (SCOPE_READ, SCOPE_SEND))
        self.assertNotIn(SCOPE_CREATE, scopes.DEFAULT_GRANTABLE_SCOPES)

    def test_a_binding_that_says_nothing_gets_the_narrow_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "binding.yaml"
            path.write_text(_binding_yaml(""), encoding="utf-8")
            config = load_config(path)
        self.assertEqual(config.auth.grantable_scopes, (SCOPE_READ, SCOPE_SEND))

    def test_a_binding_may_widen_the_ceiling_deliberately(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "binding.yaml"
            path.write_text(
                _binding_yaml("grantable_scopes: [fleet:read, fleet:send, fleet:create]\n"),
                encoding="utf-8",
            )
            config = load_config(path)
        self.assertEqual(sorted(config.auth.grantable_scopes),
                         ["fleet:create", "fleet:read", "fleet:send"])

    def test_a_binding_may_not_grant_a_verb_this_build_does_not_have(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "binding.yaml"
            path.write_text(_binding_yaml("grantable_scopes: [fleet:read, fleet:control]\n"),
                            encoding="utf-8")
            with self.assertRaises(FleetRefusal) as caught:
                load_config(path)
        self.assertEqual(caught.exception.code, "BAD_REQUEST")
        self.assertIn("fleet:control", caught.exception.detail)

    def test_the_discovery_documents_publish_the_ceiling(self):
        """A client that asks for something it will be refused should be able to
        read that from discovery instead of discovering it by refusal."""
        from fleet_mcp.oauth import TokenStore, AuthorizationServer

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = TokenStore(root / "oauth", access_ttl=3600, refresh_ttl=600)
            for grantable in ((SCOPE_READ, SCOPE_SEND),
                              (SCOPE_READ, SCOPE_SEND, SCOPE_CREATE)):
                server = AuthorizationServer(
                    store=store, auth=AuthBinding(grantable_scopes=grantable),
                    base_url="https://fleet.example", pat_reader=lambda: None,
                )
                with self.subTest(grantable=grantable):
                    self.assertEqual(sorted(server.protected_resource_metadata()["scopes_grantable"]),
                                     sorted(grantable))
                    self.assertEqual(sorted(server.authorization_server_metadata()["scopes_grantable"]),
                                     sorted(grantable))
                    # The standard key is untouched: it is the vocabulary, not
                    # the ceiling.
                    self.assertEqual(sorted(server.authorization_server_metadata()["scopes_supported"]),
                                     sorted(scopes.ALLOWED_SCOPES))


class TheContractSaysWhatTheCodeDoes(unittest.TestCase):
    """`fleet/fleet-mcp-contract.yaml` is a declaration, and a declaration that
    drifts from the code is worse than none — it is read as the specification."""

    def setUp(self):
        import yaml

        self.contract = yaml.safe_load(
            (ROOT / "fleet" / "fleet-mcp-contract.yaml").read_text(encoding="utf-8")
        )

    def test_the_declared_vocabulary_is_the_implemented_one(self):
        declared = tuple(self.contract["ingress"]["auth"]["scopes"])
        self.assertEqual(declared, scopes.IMPLEMENTED_SCOPES)

    def test_the_declared_default_ceiling_is_the_code_default(self):
        auth = self.contract["ingress"]["auth"]
        self.assertEqual(tuple(auth["scopes_grantable_default"]),
                         scopes.DEFAULT_GRANTABLE_SCOPES)
        self.assertEqual(tuple(auth["scopes_planned"]), scopes.PLANNED_SCOPES)

    def test_the_declared_project_floor_matches_the_loader(self):
        """The declaration says `policy.create_allow`, and the loader's default
        for a binding that mentions nothing really is the empty mapping — the
        two are checked against each other rather than against a copy."""
        entry = self.contract["tools"]["fleet_create_session"]
        self.assertEqual(entry["project_floor"]["field"], "policy.create_allow")
        self.assertEqual(entry["project_floor"]["default"], {})
        self.assertIn("refused", entry["project_floor"]["default_means"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "binding.yaml"
            path.write_text(_binding_yaml(), encoding="utf-8")
            self.assertEqual(load_config(path).policy.create_allow, {})

    def test_the_declaration_names_both_sides_that_enforce_the_project_floor(self):
        """A claim of coverage is the drift that matters here, in either
        direction: a reader who believes the device enforces projects relies on
        a boundary that is only on this side, and a reader told it does not
        would go and build a second one that already exists.

        So the declaration is checked for naming the device field *and* for
        saying which transport it exists on — the connector is on the far side
        of the fleet-node path and nowhere else."""
        floor = self.contract["tools"]["fleet_create_session"]["project_floor"]
        self.assertEqual(floor["enforced_by"][0], "this surface, before the device is asked")
        self.assertIn("allow_create_projects", floor["enforced_by"][1])
        self.assertIn("fleet-node transport", floor["enforced_by"][1])
        self.assertIn("Fleet-local provider", floor["not_enforced_by"])

    def test_the_declared_device_project_floor_is_the_connectors_own_field(self):
        """The contract names `allow_create_projects`; the connector is the
        machine that reads it, so the two are checked against each other."""
        from fleet_mcp.node.connector import NodeConfig

        self.assertIn("allow_create_projects", NodeConfig.__dataclass_fields__)
        self.assertEqual(
            self.contract["node_transport"]["control_floor"]["create_projects_field"],
            "allow_create_projects")

    def test_the_declared_caller_ceiling_names_the_fields_it_constrains(self):
        entry = self.contract["tools"]["fleet_create_session"]
        self.assertIn("permissionMode", entry["caller_discretion"])
        self.assertIn("model", entry["caller_discretion"])
        self.assertIn("defaults to empty", entry["caller_discretion"])
        self.assertIn("permission_modes", entry["project_floor"]["inner_ceiling"])
        self.assertIn("models", entry["project_floor"]["inner_ceiling"])


def _binding_yaml(auth_extra: str = "", *, policy_extra: str = "") -> str:
    """A minimal valid local binding, with one line's worth of room to vary."""
    return (
        "schema: serveros/fleet-mcp.local/v1\n"
        "device_ref: device:work-fj\n"
        "providers:\n"
        "  device:work-fj:\n"
        "    kind: cloudcli-mcp-http\n"
        "    endpoint: http://127.0.0.1:13001/mcp\n"
        "    credential_anchor: ~/.fleet/pat\n"
        + (f"auth:\n  {auth_extra}" if auth_extra else "")
        + ("policy:\n  " + policy_extra if policy_extra else "")
    )


if __name__ == "__main__":
    unittest.main()
