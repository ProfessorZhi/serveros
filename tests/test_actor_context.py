#!/usr/bin/env python3
"""§5 — identity is a lookup, not an argument.

Every test here is a way the property could be lost without anybody noticing.

The property is that an Agent runtime cannot produce a trusted actor context.
Not "should not", not "is discouraged from" — cannot, because there is no
function it could call that takes its word for who a person is. Three separate
mechanisms hold it up, and they fail independently:

    1. `build()` is called with what a PROVIDER reported, and refuses a report
       that identifies nobody.
    2. The daemon refuses a request that carries identity keys by name.
    3. `agent.context.attach` binds the report to the pid that observed it,
       from the kernel's `SO_PEERCRED` — a value no client can write.

A test that only covered (1) would pass against a daemon that accepted
`{"actor": "hwz"}` straight from a socket. So all three are here.
"""
from __future__ import annotations

import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"
MCP = REPO / "mcp"
CONTEXT = REPO / "context"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Loaded by module name because `runtime/daemon.py` loads them under exactly
# these names — loading a second copy would give the daemon one class and the
# test another, and `isinstance` across the two would be silently false.
actor = _load("_serveros_context_actor", CONTEXT / "actor.py")
store_mod = _load("serveros_runtime_agent_control_store", RUNTIME / "agent_control_store.py")
approval_mod = _load("serveros_runtime_approval", RUNTIME / "approval.py")
daemon_mod = _load("serveros_runtime_daemon", RUNTIME / "daemon.py")
human_control = _load("serveros_mcp_human_control", MCP / "human_control.py")
agent_sessions = _load("serveros_runtime_agent_sessions", RUNTIME / "agent_sessions.py")

AgentControlStore = store_mod.AgentControlStore
Rejected = actor.Rejected
ForgedContext = actor.ForgedContext
BotMuxHumanControlProvider = human_control.BotMuxHumanControlProvider

VERIFIED_REPORT = {
    "human_control_provider": "provider:botmux-human-control",
    "transport": "feishu",
    "transport_identity": "botmux-verified-enterprise-user",
    "conversation_ref": "oc_conversation",
    "message_ref": "om_message",
    "observed_at": "2026-09-14T00:00:00+00:00",
    "provider_status": "VERIFIED",
    "provenance": "FACT",
    "actor_ref": "human:hwz",
    "provider_pid": 4242,
}

NOW = "2026-09-14T00:00:30+00:00"


# ---------------------------------------------------------------------------
# The refs
# ---------------------------------------------------------------------------

def test_context_ref_round_trips_and_is_prefixed():
    ref = actor.context_ref("provider:botmux-human-control", "abc123")
    assert ref.startswith("human-context:")
    assert actor.parse_context_ref(ref) == ("provider:botmux-human-control", "abc123")


def test_parse_refuses_a_ref_that_is_not_a_context_ref():
    # A session ref, a device ref and a bare string all arrive here sooner or
    # later. Each is refused by name rather than looked up and missed, because
    # a miss reads as "expired" and sends the caller to re-authenticate.
    for wrong in ("agent-session:d/p/s", "device:server-beijing", "", "human:x/y/z"):
        with pytest.raises(Rejected):
            actor.parse_context_ref(wrong)


def test_actor_ref_refuses_to_wrap_an_already_typed_ref():
    with pytest.raises(Rejected):
        actor.actor_ref("human:hwz")


# ---------------------------------------------------------------------------
# Forgery refusal — mechanism 2, the one the request itself trips
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["human", "actor", "actor_ref", "approved",
                                 "user", "user_id", "open_id", "email"])
def test_every_forbidden_key_is_refused_by_name(key):
    with pytest.raises(ForgedContext) as caught:
        actor.refuse_if_forged({"action": "session.stop", key: "hwz"})
    assert key in str(caught.value), (
        "the refusal must name the offending key; a caller that is told only "
        "'forbidden' learns nothing about which field to remove")


def test_forged_payload_reports_every_offending_key_at_once():
    assert actor.forged_payload({"human": "a", "approved": True, "action": "x"}) == \
        ["approved", "human"]


def test_the_canonical_agent_self_approval_is_refused():
    # The exact payload §5 forbids: an agent asserting it is a person and that
    # the person approved. Both halves are refused, and the refusal happens
    # before any lookup, so there is no path where a partially-trusted record
    # is written first and validated later.
    with pytest.raises(ForgedContext):
        actor.refuse_if_forged({"action": "session.interrupt",
                                "context_ref": "human-context:x/y",
                                "human": "hwz", "approved": True})


def test_a_clean_payload_passes():
    assert actor.forged_payload({"action": "session.list", "context": {}}) == []
    actor.refuse_if_forged({"action": "session.list", "context": {}})


# ---------------------------------------------------------------------------
# Building — mechanism 1
# ---------------------------------------------------------------------------

def test_a_verified_report_builds_a_context():
    record = actor.build(VERIFIED_REPORT, now=NOW)
    assert record["actor_ref"] == "human:hwz"
    assert record["provider_status"] == "VERIFIED"
    assert record["provenance"] == "FACT"
    actor.validate(record)


def test_the_same_event_from_the_same_provider_is_one_context():
    # Idempotency is not a de-duplication pass; it falls out of the ref being a
    # hash of the provider's own fields. A second report of one event must not
    # mint a second context, or replaying a report would multiply evidence.
    first = actor.build(VERIFIED_REPORT, now=NOW)
    second = actor.build({**VERIFIED_REPORT, "provider_pid": 9999}, now="2026-09-14T00:00:45+00:00")
    assert first["context_ref"] == second["context_ref"], (
        "provider_pid is deliberately outside the digest: it binds the report "
        "to a process, and a restart must not make the same event a new context")


def test_a_blocked_provider_produces_no_context_at_all():
    # The live answer from this device. It must not produce a context with an
    # empty actor, a context attributed to "unknown", or an exception the
    # caller can catch and continue past.
    report = {**VERIFIED_REPORT, "provider_status": "BLOCKED", "provenance": "UNKNOWN",
              "actor_ref": None}
    with pytest.raises(Rejected) as caught:
        actor.build(report, now=NOW)
    assert "identifies no human" in str(caught.value)


def test_a_verified_report_cannot_carry_unknown_provenance():
    with pytest.raises(Rejected):
        actor.build({**VERIFIED_REPORT, "provenance": "UNKNOWN"}, now=NOW)


def test_an_unverified_report_cannot_carry_fact():
    with pytest.raises(Rejected):
        actor.build({**VERIFIED_REPORT, "provider_status": "BLOCKED",
                     "provenance": "FACT"}, now=NOW)


def test_a_report_missing_evidence_fields_is_refused():
    for missing in ("transport", "conversation_ref", "observed_at"):
        report = {k: v for k, v in VERIFIED_REPORT.items() if k != missing}
        with pytest.raises(Rejected):
            actor.build(report, now=NOW)


def test_a_context_with_no_window_is_refused():
    with pytest.raises(Rejected):
        actor.validate({"context_ref": "human-context:p/d", "actor_ref": "human:hwz",
                        "observed_at": "2026-09-14T00:00:00+00:00",
                        "expires_at": "2026-09-14T00:00:00+00:00"})


def test_an_unparseable_timestamp_is_refused():
    # A record that cannot be aged can never be found stale, which is the
    # failure mode of every "expires: yes, but nothing computes it" field.
    record = actor.build(VERIFIED_REPORT, now=NOW)
    record["expires_at"] = "soon"
    with pytest.raises(Rejected):
        actor.validate(record)


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------

def test_freshness_is_computed_from_the_clock_not_read_from_the_record():
    record = actor.build(VERIFIED_REPORT, now=NOW)
    assert actor.freshness(record, "2026-09-14T00:00:30+00:00")["state"] == "FRESH"
    assert actor.freshness(record, "2026-09-14T00:10:00+00:00")["state"] == "STALE"
    assert actor.freshness(record, "2026-09-14T00:20:00+00:00")["state"] == "EXPIRED"


def test_stale_does_not_authorize():
    record = actor.build(VERIFIED_REPORT, now=NOW)
    verdict = actor.authorizes(record, "2026-09-14T00:10:00+00:00")
    assert verdict["authorizes"] is False
    assert verdict["state"] == "STALE"


def test_fresh_and_verified_authorizes():
    record = actor.build(VERIFIED_REPORT, now=NOW)
    assert actor.authorizes(record, "2026-09-14T00:00:30+00:00")["authorizes"] is True


def test_authorizes_says_which_of_the_three_conditions_failed():
    # "no" is not an answer a requester can act on. The reason names whether it
    # was identity, provenance or time — three different repairs.
    blocked = {**VERIFIED_REPORT, "provider_status": "BLOCKED", "provenance": "UNKNOWN",
               "actor_ref": "human:hwz"}
    record = actor.build(blocked, now=NOW)
    verdict = actor.authorizes(record, "2026-09-14T00:00:30+00:00")
    assert verdict["authorizes"] is False
    assert "did not verify this actor" in verdict["because"]


# ---------------------------------------------------------------------------
# Resolution — mechanism 3's read half
# ---------------------------------------------------------------------------

def test_resolving_a_ref_serveros_never_minted_is_a_miss_not_a_default():
    result = actor.resolve("human-context:nobody/neverminted", {})
    assert result == {"found": False, "context_ref": "human-context:nobody/neverminted",
                      "because": "no context with this ref was reported by any "
                                 "HumanControlProvider"}


def test_resolving_a_malformed_ref_is_a_named_miss_not_a_raise():
    # Returned, not raised. A caller that invented a handle must not be able to
    # catch an exception and continue with a substitute context.
    result = actor.resolve("device:server-beijing", {"device:server-beijing": {"x": 1}})
    assert result["found"] is False
    assert "not a trusted actor context ref" in result["because"]


def test_resolving_a_stored_ref_returns_the_record():
    record = actor.build(VERIFIED_REPORT, now=NOW)
    found = actor.resolve(record["context_ref"], {record["context_ref"]: record})
    assert found["found"] is True and found["context"] is record


# ---------------------------------------------------------------------------
# The provider adapter
# ---------------------------------------------------------------------------

def test_a_blocked_actor_command_is_reported_as_blocked_not_unsupported():
    # The measured answer on this device, replayed through the real code path.
    provider = BotMuxHumanControlProvider(
        runner=lambda: {"schema": "botmux.current-actor.v2", "status": "blocked",
                        "error": "current_actor_unverified"},
        clock=lambda: NOW)
    report = provider.identify(conversation_ref="oc_x")
    assert report.status == "BLOCKED"
    assert report.actor_id is None
    assert "current_actor_unverified" in report.detail


def test_a_changed_actor_schema_is_refused_rather_than_read():
    # The `declared-but-absent` pathology, defended at the one place where it
    # would be a security failure: reading `user` out of a document that no
    # longer has one yields None, and None at the call site looks exactly like
    # "no user" rather than "this is not the document I think it is".
    provider = BotMuxHumanControlProvider(
        runner=lambda: {"schema": "botmux.current-actor.v3", "user": "hwz"},
        clock=lambda: NOW)
    report = provider.identify()
    assert report.status == "BLOCKED"
    assert "schema" in report.detail
    assert report.actor_id is None


def test_ok_without_a_user_is_blocked_not_a_private_turn():
    provider = BotMuxHumanControlProvider(
        runner=lambda: {"schema": "botmux.current-actor.v2", "status": "ok"},
        clock=lambda: NOW)
    report = provider.identify()
    assert report.status == "BLOCKED"
    assert "no verified user name" in report.detail


def test_a_verified_answer_produces_the_full_report_and_a_context():
    provider = BotMuxHumanControlProvider(
        runner=lambda: {"schema": "botmux.current-actor.v2", "status": "ok",
                        "user": "hwz"},
        clock=lambda: NOW)
    report = provider.identify(conversation_ref="oc_x", message_ref="om_y")
    assert report.status == "VERIFIED" and report.actor_id == "hwz"
    record = actor.build(report.as_report(), now=NOW)
    assert record["actor_ref"] == "human:hwz"
    assert record["conversation_ref"] == "oc_x"


def test_a_missing_actor_surface_is_unsupported_not_blocked():
    # "the interface is not there" and "the interface said no" send an operator
    # to different places, so they are different words.
    provider = BotMuxHumanControlProvider(
        runner=lambda: {"schema": None, "status": "unsupported", "error": "FileNotFoundError"},
        clock=lambda: NOW)
    assert provider.identify().status == "UNSUPPORTED"


def test_request_approval_refuses_and_names_the_binding_it_cannot_do():
    """The refusal survived the re-measurement, and its reason changed.

    Until 2026-09-15 the exception said BotMux had *no* surface for a human
    decision, resting on three `/api/*` 404s. BotMux v3.18 does have one:
    `botmux ask buttons --options "a,b"` delivers the question and returns the
    chosen key. So "there is no surface" became false, and a refusal resting on
    a false premise is a refusal somebody will delete once they notice.

    What is still true, and is why this still raises: a returned key is not
    BOUND to a request fingerprint, so it cannot be shown to be about the
    request the approval is for. §AA forbids treating a delivered message as an
    approval. The test asserts the surviving reason, not the retired one.
    """
    provider = BotMuxHumanControlProvider(clock=lambda: NOW)
    with pytest.raises(human_control.HumanControlUnsupported) as caught:
        provider.request_approval(conversation_ref="oc_x", question={}, idempotency_key="k")
    message = str(caught.value)
    assert "BOUND" in message and "fingerprint" in message, (
        "the refusal must name what is actually missing — an answer bound to "
        "the request — rather than an absence of a surface that now exists")
    assert "404" not in message, (
        "the retired claim. Every /api/* path answers 401 today, including a "
        "bogus one, so the 404s are not reproducible and may not be restated")


def test_capabilities_do_not_claim_a_capability_that_was_never_observed():
    caps = BotMuxHumanControlProvider(clock=lambda: NOW).capabilities()["capabilities"]
    assert caps["human.identity.verify"]["state"] == "UNKNOWN", (
        "the surface exists and answers, and has never been observed to verify "
        "anybody from here. Calling that SUPPORTED would be the declaration "
        "running ahead of the data, which is the pathology this round exists "
        "to stop repeating.")
    assert caps["human.notification.transport"]["state"] == "SUPPORTED"
    # Four UNKNOWNs as of 2026-09-15, two of them corrected downward-from-
    # UPWARD: approval and rendering were UNSUPPORTED, which claims the surface
    # does not exist. The CLI measurement says the surfaces do exist (`ask
    # buttons` returns a key; `send --card-file` is documented and reachable),
    # and that nothing has shown them carrying what ServerOS needs. UNSUPPORTED
    # would have meant "design around it"; UNKNOWN means "go and find out", and
    # the finding-out is named in each reason.
    assert caps["human.approval.transport"]["state"] == "UNKNOWN"
    assert caps["human.interactive.rendering"]["state"] == "UNKNOWN"
    assert caps["human.transport.ingress"]["state"] == "UNKNOWN"
    for name, entry in caps.items():
        assert entry.get("reason"), f"{name} is declared with no reason"
    # An UNSUPPORTED anywhere in this table is now a claim that a documented
    # surface is absent, and none survives measurement.
    assert not [n for n, e in caps.items() if e["state"] == "UNSUPPORTED"], (
        "UNSUPPORTED says the surface does not exist. Nothing in this table "
        "can support that today; each entry names a documented CLI path")


# ---------------------------------------------------------------------------
# The daemon doorway — all three mechanisms, through the served interface
# ---------------------------------------------------------------------------

class FakeRegistry:
    def all(self):
        return []

    def get(self, key):
        raise KeyError(key)

    def __len__(self):
        return 0


@pytest.fixture
def daemon(tmp_path):
    return daemon_mod.Daemon(FakeRegistry(), state_dir=tmp_path / "state",
                             socket_path=tmp_path / "run/serveros.sock",
                             now=lambda: 1_772_000_000.0,
                             log=lambda message: None)


def _call(daemon_obj, method, params, *, peer=None):
    return daemon_obj.handle({"method": method, "params": params}, peer=peer)


def test_attach_refuses_a_payload_carrying_identity(daemon):
    response = _call(daemon, "agent.context.attach",
                     {"report": VERIFIED_REPORT, "human": "hwz"}, peer={"pid": 4242})
    assert response["ok"] is False
    assert response["error"]["code"] == "ForgedContext"
    assert "human" in response["error"]["message"]


def test_attach_refuses_a_report_observed_by_a_different_process(daemon):
    # The mechanism that makes this more than a schema check: a report is
    # produced by running the identity probe inside the turn being identified,
    # so the report carries the pid that observed it, and that pid must be the
    # caller. An agent cannot change which process it is.
    response = _call(daemon, "agent.context.attach", {"report": VERIFIED_REPORT},
                     peer={"pid": 9999})
    assert response["ok"] is False
    assert "observed by pid 4242" in response["error"]["message"]
    assert "arrived from pid 9999" in response["error"]["message"]


def test_attach_refuses_a_report_with_no_observer_pid(daemon):
    report = {k: v for k, v in VERIFIED_REPORT.items() if k != "provider_pid"}
    response = _call(daemon, "agent.context.attach", {"report": report},
                     peer={"pid": 4242})
    assert response["ok"] is False
    assert "which process observed" in response["error"]["message"]


def test_attach_refuses_when_there_is_no_caller_process(daemon):
    # `handle` called directly, with no socket behind it. Refused rather than
    # treated as trusted-by-default, which is how an in-process shortcut
    # becomes the way around the check.
    response = _call(daemon, "agent.context.attach", {"report": VERIFIED_REPORT})
    assert response["ok"] is False
    assert "no caller process" in response["error"]["message"]


def test_attach_mints_a_context_and_stores_it(daemon):
    response = _call(daemon, "agent.context.attach", {"report": VERIFIED_REPORT},
                     peer={"pid": 4242})
    assert response["ok"] is True
    result = response["result"]
    assert result["context_ref"].startswith("human-context:")
    assert result["actor_ref"] == "human:hwz"
    assert result["observed_by_pid"] == 4242
    assert result["freshness"]["state"] == "FRESH"
    assert daemon.control_store().get_context(result["context_ref"]) is not None


def test_attach_refuses_a_blocked_report(daemon):
    report = {**VERIFIED_REPORT, "provider_status": "BLOCKED",
              "provenance": "UNKNOWN", "actor_ref": None}
    response = _call(daemon, "agent.context.attach", {"report": report},
                     peer={"pid": 4242})
    assert response["ok"] is False
    assert "identifies no human" in response["error"]["message"]


def test_inspect_of_an_unminted_ref_reports_a_miss(daemon):
    response = _call(daemon, "agent.context.inspect",
                     {"context_ref": "human-context:nobody/neverminted"})
    assert response["ok"] is True
    assert response["result"]["found"] is False


def test_control_status_reports_only_what_the_daemon_owns(daemon):
    """§18 holds here too, and this is the test that says so.

    The first version of `agent.control.status` imported the provider module to
    ask it for its capability list. That is the daemon becoming a client of a
    transport, which §18 forbids — and the failure mode is specific: a daemon
    that depends on a provider cannot answer when the provider is broken, which
    is exactly when somebody asks.
    """
    result = _call(daemon, "agent.control.status", {})["result"]
    assert result["store"]["contexts"] == 0
    assert "session.interrupt" in result["human_gated_actions"]
    assert result["authority"] == "serverosd"
    assert "mcp/human_control.py" in result["provider_surface"]
    # No transport capability is restated here, in any spelling. The provider
    # answers that question, on a surface allowed to know about transports.
    blob = json.dumps(result).lower()
    for leaked in ("feishu", "lark", "botmux", "http://", "approval.transport\":"):
        assert leaked not in blob, (
            f"{leaked!r} leaked into the node's control status; the daemon must "
            "not carry a transport's declaration")


def test_the_daemon_does_not_import_the_provider_module():
    """The invariant is about the import, not the substring.

    `human_control_provider` is a FIELD NAME in the actor context contract —
    the record the daemon stores must say which provider attested it, and a
    grep for the words would forbid the honest field along with the dependency.
    What is forbidden is loading the adapter: the daemon owns the trusted store
    and the decision, and owning the transport would make it unable to answer
    when the transport is broken.
    """
    source = (RUNTIME / "daemon.py").read_text()
    assert "serveros_mcp_human_control" not in source, (
        "the daemon loads the HumanControlProvider adapter")
    assert "human_control" not in {
        name for name in dir(daemon_mod) if not name.startswith("__")
    }, ("the daemon module carries a provider helper; the transport's "
        "declaration belongs on the surface that is allowed to know about it")
    assert not hasattr(daemon_mod, "_human_control_caps")


# ---------------------------------------------------------------------------
# The transport boundary, asserted rather than asserted-about
# ---------------------------------------------------------------------------

def test_peer_credentials_come_from_the_kernel_not_the_client(tmp_path):
    """The one test that proves the pid cannot be forged.

    A real AF_UNIX socket pair, a real `SO_PEERCRED` read, and a client that
    has done nothing but connect. If this returned anything the client could
    influence, every claim above would be decoration.
    """
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        peer = daemon_mod._peer_credentials(left)
    finally:
        left.close()
        right.close()
    assert peer is not None, "SO_PEERCRED is required on the deployment platform"
    import os
    assert peer["pid"] == os.getpid(), (
        "the peer of a local socketpair is this process, and the kernel is the "
        "one that says so")
    assert peer["uid"] == os.getuid()


def test_agent_control_store_is_keyed_by_ref_and_never_by_actor(tmp_path):
    path = tmp_path / "store.json"
    store = AgentControlStore(path)
    record = actor.build(VERIFIED_REPORT, now=NOW)
    store.put_context(record)
    on_disk = json.loads(path.read_text())
    assert record["context_ref"] in on_disk["contexts"]
    # The negative that matters: there is no method that returns "everything
    # about this person". Adding one is the change that turns an evidence log
    # into a directory of people, so its absence is a test.
    for forbidden in ("get_actor", "contexts_for_actor", "approvals_for_actor",
                      "actor", "whois"):
        assert not hasattr(store, forbidden), (
            f"AgentControlStore.{forbidden} would make this a HumanIdentity DB")


def test_a_corrupt_store_refuses_to_load_rather_than_reading_as_empty(tmp_path):
    path = tmp_path / "store.json"
    path.write_text("{ this is not json")
    with pytest.raises(store_mod.StoreError) as caught:
        AgentControlStore(path)
    assert "Refusing to start with an empty control store" in str(caught.value)


def test_the_gated_actions_are_read_from_the_session_contract_not_restated():
    from pathlib import Path as _Path
    import yaml
    contract = yaml.safe_load(
        (_Path(REPO) / "runtime" / "agent-session-contract.yaml").read_text())
    declared = set(contract["admission"]["human_gated"])
    assert set(approval_mod.human_gated_actions()) == declared, (
        "two lists of human-gated verbs agree until the day one changes, and "
        "the disagreement is an action that reads gated in the contract and "
        "ungated in the code that enforces it")


def test_the_session_contract_vocabulary_has_no_unused_states():
    # §5's admission classes must all be reachable. A declared class no action
    # lands in is the same pathology one layer down.
    contract = approval_mod.contract()  # the approval contract, for its own vocabulary
    assert contract["consumption"]["model"] == "single-use"
