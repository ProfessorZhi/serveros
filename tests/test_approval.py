#!/usr/bin/env python3
"""§6 — approval is a binding, not a boolean.

The claim under test is one sentence:

    an approval names an ACTION on a TARGET with PARAMETERS, decided by an
    ACTOR from a CONTEXT, until a TIME — and any change to any of those is a
    different approval that nobody has granted.

Every test below changes exactly one of those six things and asserts that the
old approval stops applying. The tempting implementation — "this actor
approved `session.stop`, so allow it" — passes a test that changes the action
and fails all five of the others, which is why they are all here.

The second half of the file is the negative that makes the first half mean
something: **nothing an Agent can call grants an approval.** §6 forbids a fake
live approval, so the enforcement is an absence, and an absence needs a test
that fails when somebody adds the thing.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
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


approval = _load("serveros_runtime_approval", RUNTIME / "approval.py")
store_mod = _load("serveros_runtime_agent_control_store", RUNTIME / "agent_control_store.py")
actor = _load("_serveros_context_actor", CONTEXT / "actor.py")
daemon_mod = _load("serveros_runtime_daemon", RUNTIME / "daemon.py")

AgentControlStore = store_mod.AgentControlStore

T0 = "2026-09-14T00:00:00+00:00"
T1 = "2026-09-14T00:00:30+00:00"   # inside the fresh window
T2 = "2026-09-14T00:10:00+00:00"   # stale
T3 = "2026-09-14T01:00:00+00:00"   # past the 900s window

CONTEXT = {"context_ref": "human-context:provider%3Abotmux-human-control/abc",
           "actor_ref": "human:hwz",
           "human_control_provider": "provider:botmux-human-control",
           "transport": "feishu",
           "observed_at": T0}

TARGET = {"device_ref": "device:server-beijing", "session_ref": "agent-session:d/p/s",
          "agent_runtime": "agent-runtime:codex", "provider": "provider:codex-app-server"}

VERIFIED_REPORT = {
    "human_control_provider": "provider:botmux-human-control",
    "transport": "feishu",
    "transport_identity": "botmux-verified-enterprise-user",
    "conversation_ref": "oc_conversation",
    "message_ref": "om_message",
    "observed_at": T0,
    "provider_status": "VERIFIED",
    "provenance": "FACT",
    "actor_ref": "human:hwz",
    "provider_pid": 4242,
}


def issue(**overrides):
    kwargs = {"action": "session.interrupt", "target": TARGET, "params": {},
              "context": CONTEXT, "now": T0}
    kwargs.update(overrides)
    return approval.issue(**kwargs)


# ---------------------------------------------------------------------------
# Issuing
# ---------------------------------------------------------------------------

def test_issuing_an_ungated_action_is_refused():
    # Issuing an approval for something that does not need one would create
    # authority the contract never granted, and a later reader would find an
    # approval beside a delegation action and conclude the action is gated.
    with pytest.raises(approval.Rejected) as caught:
        issue(action="session.list")
    assert "not a human-gated action" in str(caught.value)


def test_issuing_without_a_context_is_refused():
    with pytest.raises(approval.Rejected) as caught:
        issue(context={"actor_ref": "human:hwz"})
    assert "trusted context" in str(caught.value)


def test_issuing_without_an_actor_is_refused():
    with pytest.raises(approval.Rejected):
        issue(context={"context_ref": "human-context:x/y"})


def test_the_approval_ref_is_the_binding_not_a_serial():
    # Two approvals of the same action on the same target from the same context
    # ARE the same approval, which is what makes issuance idempotent without a
    # lookup table of near-duplicates.
    first, second = issue(), issue()
    assert first["approval_ref"] == second["approval_ref"]
    assert first["approval_ref"].startswith("approval:")


def test_a_different_actor_produces_a_different_approval():
    other = {**CONTEXT, "actor_ref": "human:someone-else",
             "context_ref": "human-context:provider%3Abotmux-human-control/other"}
    assert issue()["approval_ref"] != issue(context=other)["approval_ref"], (
        "the ref is the binding hashed INCLUDING who, so an audit need not "
        "trust a field beside it")


def test_a_different_context_produces_a_different_approval():
    other = {**CONTEXT, "context_ref": "human-context:provider%3Abotmux-human-control/other"}
    assert issue()["approval_ref"] != issue(context=other)["approval_ref"]


def test_issuing_against_a_store_is_idempotent():
    store = {}
    first = issue(store=store)
    store[first["approval_ref"]] = first
    assert issue(store=store) is first


def test_the_recorded_target_keeps_its_null_fields():
    created = issue(target={"device_ref": "device:work-fj", "session_ref": None,
                            "agent_runtime": None, "provider": None})
    assert created["target"] == {"device_ref": "device:work-fj", "session_ref": None,
                                 "agent_runtime": None, "provider": None}


# ---------------------------------------------------------------------------
# The fingerprint — §6's "批准后修改目标、设备或动作，应失效"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field, changed", [
    ("action", "session.stop"),
    ("target", {**TARGET, "device_ref": "device:work-fj"}),
    ("target", {**TARGET, "session_ref": "agent-session:d/p/other"}),
    ("target", {**TARGET, "provider": "provider:other"}),
    ("params", {"force": True}),
])
def test_every_part_of_the_binding_changes_the_fingerprint(field, changed):
    base = {"action": "session.interrupt", "target": TARGET, "params": {}}
    other = {**base, field: changed}
    assert approval.fingerprint(**base) != approval.fingerprint(**other)


def test_an_extra_parameter_cannot_ride_along_on_an_approval():
    # The attack the `extra_params: participate` rule exists for: add a field
    # the normalizer ignores and the approval still matches.
    assert approval.fingerprint(action="session.stop", target=TARGET, params={}) != \
        approval.fingerprint(action="session.stop", target=TARGET,
                             params={"__proto__": {"admin": True}})


def test_key_order_does_not_change_the_fingerprint():
    assert approval.fingerprint(action="session.stop", target=TARGET, params={"a": 1, "b": 2}) == \
        approval.fingerprint(action="session.stop", target=TARGET, params={"b": 2, "a": 1})


def test_null_and_missing_coincide_where_the_key_set_is_fixed():
    # `target` has four declared keys. Not saying one and saying it is null are
    # the same statement, so they hash alike — otherwise a trailing `null`
    # would be a one-character edit that invalidates an approval nobody
    # expected to invalidate.
    assert approval.fingerprint(action="session.stop", target={}, params={}) == \
        approval.fingerprint(action="session.stop",
                             target={"device_ref": None}, params={})
    assert approval.fingerprint(action="session.stop", target={}, params={}) == \
        approval.fingerprint(action="session.stop",
                             target={"device_ref": None, "session_ref": None,
                                     "agent_runtime": None, "provider": None},
                             params={})


def test_null_and_missing_are_different_where_the_key_set_is_open():
    # `params` has no declared keys, so `{"x": null}` is a present parameter
    # and `{}` is not. Folding them would let a caller add a parameter while
    # leaving the fingerprint alone — the same evasion the extra_params rule
    # closes for non-null values. This is the one place the contract's
    # null-fold does NOT apply, and it says so.
    assert approval.fingerprint(action="session.stop", target=TARGET, params={}) != \
        approval.fingerprint(action="session.stop", target=TARGET, params={"x": None})


def test_a_stated_target_differs_from_an_unstated_one():
    # The point of `target_nullable_because`: null is part of the binding. An
    # approval for "the case where there was no session" must not apply to one
    # that names a session.
    assert approval.fingerprint(action="session.create", target={}, params={}) != \
        approval.fingerprint(action="session.create",
                             target={"session_ref": "agent-session:d/p/s"}, params={})


# ---------------------------------------------------------------------------
# Deciding — the six verdicts
# ---------------------------------------------------------------------------

def decide(held, *, context_ref=CONTEXT["context_ref"], request=None, now=T1):
    return approval.decide(
        approvals=held,
        request={**{"action": "session.interrupt", "target": TARGET, "params": {},
                    "actor_ref": "human:hwz"}, **(request or {})},
        context_ref=context_ref, now=now)


def test_a_matching_approval_allows():
    verdict = decide([issue()])
    assert verdict["decision"] == "ALLOW"
    assert verdict["class"] == "human_approved"
    assert verdict["approval_ref"] == issue()["approval_ref"]


def test_no_approval_at_all_is_approval_required_not_denied():
    verdict = decide([])
    assert verdict["decision"] == "APPROVAL_REQUIRED"
    assert "no approval names this action for this actor" in verdict["reason"]


def test_no_context_is_approval_required():
    assert decide([issue()], context_ref=None)["decision"] == "APPROVAL_REQUIRED"


def test_changing_the_target_after_approval_is_a_binding_mismatch():
    # The headline case: somebody approved interrupting session A, and the
    # request now names session B. This must NOT read as "denied" — the
    # requester needs to know an approval exists and is for something else.
    verdict = decide([issue()], request={
        "target": {**TARGET, "session_ref": "agent-session:d/p/OTHER"}})
    assert verdict["decision"] == "APPROVAL_BINDING_MISMATCH"
    assert verdict["approved_fingerprint"] != verdict["presented_fingerprint"]


def test_changing_the_params_after_approval_is_a_binding_mismatch():
    assert decide([issue(params={})], request={"params": {"force": True}})["decision"] == \
        "APPROVAL_BINDING_MISMATCH"


def test_changing_the_action_after_approval_is_approval_required():
    # A different action is a different approval, and none exists for it.
    verdict = decide([issue()], request={"action": "session.stop"})
    assert verdict["decision"] in ("APPROVAL_REQUIRED", "APPROVAL_BINDING_MISMATCH")
    assert verdict["decision"] != "ALLOW"


def test_an_expired_approval_says_so_rather_than_denying():
    verdict = decide([issue()], now=T3)
    assert verdict["decision"] == "APPROVAL_EXPIRED"
    assert "closed at" in verdict["reason"]


def test_an_approval_from_another_context_is_reported_as_such():
    verdict = decide([issue()], context_ref="human-context:provider%3Aother/zzz")
    assert verdict["decision"] == "APPROVAL_CONTEXT_MISMATCH"
    assert verdict["approved_from"] == CONTEXT["context_ref"]


def test_a_consumed_approval_cannot_be_used_twice():
    spent = approval.consume(issue(), consumed_by="serverosd", now=T1)
    verdict = decide([spent])
    assert verdict["decision"] == "APPROVAL_ALREADY_CONSUMED"
    assert "spent at" in verdict["reason"]


def test_an_approval_for_another_actor_is_not_matched():
    other = issue(context={**CONTEXT, "actor_ref": "human:someone-else",
                           "context_ref": CONTEXT["context_ref"]})
    assert decide([other])["decision"] == "APPROVAL_REQUIRED"


def test_deciding_an_ungated_action_is_refused():
    with pytest.raises(approval.Rejected):
        decide([], request={"action": "session.list"})


# ---------------------------------------------------------------------------
# Consumption
# ---------------------------------------------------------------------------

def test_consumption_is_a_separate_step_from_deciding():
    # So that a caller which decides and then fails to perform the action can
    # leave the approval unspent. Deciding is a read; spending is a write, and
    # they belong on opposite sides of the action they authorize.
    record = issue()
    assert approval.decide(
        approvals=[record],
        request={"action": "session.interrupt", "target": TARGET, "params": {},
                 "actor_ref": "human:hwz"},
        context_ref=CONTEXT["context_ref"], now=T1)["decision"] == "ALLOW"
    assert record["state"] == "ISSUED"


def test_consuming_does_not_mutate_the_record_it_was_given():
    record = issue()
    spent = approval.consume(record, consumed_by="serverosd", now=T1)
    assert record["state"] == "ISSUED" and spent["state"] == "CONSUMED"
    assert spent["consumed_by"] == "serverosd"


def test_consuming_twice_is_refused():
    spent = approval.consume(issue(), consumed_by="serverosd", now=T1)
    with pytest.raises(approval.Rejected) as caught:
        approval.consume(spent, consumed_by="serverosd", now=T1)
    assert "not ISSUED" in str(caught.value)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

def test_a_consumed_approval_survives_the_sweep(tmp_path):
    # It is the audit record of a decision somebody made. Deleting it would
    # make "who approved this" unanswerable for every action older than the
    # window — which is every action anybody would ask about.
    store = AgentControlStore(tmp_path / "store.json")
    spent = approval.consume(issue(), consumed_by="serverosd", now=T1)
    store.put_approval(spent)
    gone = store.sweep(before=T3)
    assert gone["approvals"] == []
    assert store.get_approval(spent["approval_ref"]) is not None


def test_an_unconsumed_approval_past_its_window_is_swept(tmp_path):
    store = AgentControlStore(tmp_path / "store.json")
    record = issue()
    store.put_approval(record)
    assert store.sweep(before=T3)["approvals"] == [record["approval_ref"]]
    assert store.get_approval(record["approval_ref"]) is None


def test_the_store_queries_by_actor_as_a_filter_not_a_key(tmp_path):
    store = AgentControlStore(tmp_path / "store.json")
    record = issue()
    store.put_approval(record)
    assert [a["approval_ref"] for a in store.for_actor_action(
        actor_ref="human:hwz", action="session.interrupt")] == [record["approval_ref"]]
    assert store.for_actor_action(actor_ref="human:hwz", action="session.stop") == []


def test_the_store_survives_a_round_trip(tmp_path):
    path = tmp_path / "store.json"
    first = AgentControlStore(path)
    record = issue()
    first.put_approval(record)
    assert AgentControlStore(path).get_approval(record["approval_ref"]) == record


def test_summary_counts_states_without_returning_records(tmp_path):
    store = AgentControlStore(tmp_path / "store.json")
    store.put_approval(issue())
    store.put_approval(approval.consume(issue(action="session.stop"), consumed_by="x", now=T1))
    summary = store.summary()
    assert summary["approvals"] == 2
    assert summary["approvals_by_state"] == {"ISSUED": 1, "CONSUMED": 1}
    assert "approvals_by_state" in json.dumps(summary) and "human:hwz" not in json.dumps(summary)


# ---------------------------------------------------------------------------
# The daemon — admission, gated end to end
# ---------------------------------------------------------------------------

class FakeRegistry:
    def all(self):
        return []

    def get(self, key):
        raise KeyError(key)

    def __len__(self):
        return 0


def _epoch(stamp: str) -> float:
    return datetime.fromisoformat(stamp).timestamp()


@pytest.fixture
def daemon(tmp_path):
    import os
    os.environ.pop("SERVEROS_BOTMUX_DASHBOARD_TOKEN", None)
    return daemon_mod.Daemon(FakeRegistry(), state_dir=tmp_path / "state",
                             socket_path=tmp_path / "run/serveros.sock",
                             now=lambda: _epoch("2026-09-14T00:00:30+00:00"),
                             log=lambda message: None)


def _call(daemon_obj, method, params, *, peer=None):
    return daemon_obj.handle({"method": method, "params": params}, peer=peer)


def _mint(daemon_obj, **overrides):
    """Attach a context through the real doorway, and return its ref."""
    report = {**VERIFIED_REPORT, "provider_pid": 4242, **overrides}
    response = _call(daemon_obj, "agent.context.attach", {"report": report},
                     peer={"pid": 4242})
    assert response["ok"] is True, response
    return response["result"]["context_ref"]


def test_a_read_only_action_is_allowed_with_no_approval(daemon):
    result = _call(daemon, "agent.action.admit",
                   {"action": "session.list", "context": {"task": "t"}})["result"]
    assert result["decision"] == "ALLOW"
    assert result["class"] == "read_only"
    assert result["approval"] is None


def test_a_human_gated_action_with_no_context_asks_for_an_approval(daemon):
    result = _call(daemon, "agent.action.admit",
                   {"action": "session.stop", "context": {"task": "t"}})["result"]
    assert result["decision"] == "REQUIRE_HUMAN"
    assert result["approval"]["decision"] == "APPROVAL_REQUIRED"
    assert "no trusted context" in result["approval"]["reason"]


def test_an_invented_context_ref_does_not_get_a_context(daemon):
    result = _call(daemon, "agent.action.admit",
                   {"action": "session.stop", "context": {},
                    "context_ref": "human-context:nobody/neverminted"})["result"]
    assert result["decision"] == "REQUIRE_HUMAN"
    assert result["approval"]["context_found"] is False


def test_a_stale_context_cannot_authorize_a_mutation(daemon):
    ref = _mint(daemon, observed_at="2026-09-13T23:00:00+00:00")  # an hour old
    result = _call(daemon, "agent.action.admit",
                   {"action": "session.stop", "context": {}, "context_ref": ref})["result"]
    assert result["decision"] == "REQUIRE_HUMAN"
    assert result["approval"]["context_found"] is True
    assert result["approval"]["context_freshness"] == "EXPIRED"


def test_a_fresh_context_with_no_approval_is_still_approval_required(daemon):
    # This is the whole point: being an identified human is not the same as
    # having approved this. The first is who you are; the second is a decision
    # about one action.
    ref = _mint(daemon)
    result = _call(daemon, "agent.action.admit",
                   {"action": "session.stop", "context": {}, "context_ref": ref})["result"]
    assert result["decision"] == "REQUIRE_HUMAN"
    assert result["approval"]["decision"] == "APPROVAL_REQUIRED"


def test_an_approval_for_this_exact_request_is_allowed(daemon):
    ref = _mint(daemon)
    record = approval.issue(
        action="session.stop", target={"device_ref": "device:server-beijing"},
        params={}, context={**CONTEXT, "context_ref": ref}, now=T0)
    daemon.control_store().put_approval(record)
    result = _call(daemon, "agent.action.admit",
                   {"action": "session.stop", "context": {},
                    "context_ref": ref,
                    "target": {"device_ref": "device:server-beijing"}})["result"]
    assert result["decision"] == "ALLOW"
    assert result["class"] == "human_approved"
    assert result["approval"]["approval_ref"] == record["approval_ref"]


def test_the_approval_is_not_consumed_by_admitting(daemon):
    ref = _mint(daemon)
    record = approval.issue(
        action="session.stop", target={"device_ref": "device:server-beijing"},
        params={}, context={**CONTEXT, "context_ref": ref}, now=T0)
    daemon.control_store().put_approval(record)
    _call(daemon, "agent.action.admit",
          {"action": "session.stop", "context": {}, "context_ref": ref,
           "target": {"device_ref": "device:server-beijing"}})
    assert daemon.control_store().get_approval(record["approval_ref"])["state"] == "ISSUED", (
        "a caller that fails to perform the action must not have spent the "
        "person's decision")


def test_an_approval_for_a_different_device_does_not_transfer(daemon):
    ref = _mint(daemon)
    record = approval.issue(
        action="session.stop", target={"device_ref": "device:work-fj"},
        params={}, context={**CONTEXT, "context_ref": ref}, now=T0)
    daemon.control_store().put_approval(record)
    result = _call(daemon, "agent.action.admit",
                   {"action": "session.stop", "context": {}, "context_ref": ref,
                    "target": {"device_ref": "device:server-beijing"}})["result"]
    assert result["decision"] == "REQUIRE_HUMAN"
    assert result["approval"]["decision"] == "APPROVAL_BINDING_MISMATCH"
    assert result["approval"]["approved_fingerprint"] != \
        result["approval"]["presented_fingerprint"]


def test_admit_refuses_a_payload_that_states_its_own_actor(daemon):
    response = _call(daemon, "agent.action.admit",
                     {"action": "session.stop", "context": {}, "actor": "hwz",
                      "approved": True})
    assert response["ok"] is False
    assert response["error"]["code"] == "ForgedContext"
    assert "actor" in response["error"]["message"]


def test_approval_inspect_reports_not_gated_for_a_delegation_action(daemon):
    ref = _mint(daemon)
    result = _call(daemon, "agent.approval.inspect",
                   {"context_ref": ref, "action": "session.create",
                    "target": {}, "args": {}})["result"]
    assert result["verdict"]["decision"] == "NOT_GATED"


def test_approval_inspect_computes_the_fingerprint_itself(daemon):
    # A caller-supplied fingerprint would let the requester choose what its
    # approval was for.
    ref = _mint(daemon)
    supplied = "0" * 64
    result = _call(daemon, "agent.approval.inspect",
                   {"context_ref": ref, "action": "session.stop",
                    "target": {"device_ref": "device:server-beijing"}, "args": {},
                    "request_fingerprint": supplied})["result"]
    expected = approval.fingerprint(action="session.stop",
                                    target={"device_ref": "device:server-beijing"},
                                    params={})
    assert result["request_fingerprint"] == expected != supplied


# ---------------------------------------------------------------------------
# The negative: no Agent-reachable verb grants an approval
# ---------------------------------------------------------------------------

def test_the_control_agent_mcp_surface_has_no_approval_granting_tool():
    """§6's enforcement is an absence, so it needs a test.

    Agents and the operator run as the same uid on this host, so no permission
    bit separates them. What separates them is that the interface an agent
    speaks has no verb for granting approval. This asserts that, by name, over
    the actual tool list rather than over the source of the module.
    """
    agent_mcp = _load("serveros_mcp_agent_mcp", MCP / "agent_mcp.py")
    tools = list(agent_mcp.tool_names())
    assert tools, "the surface declares no tools, so this test proves nothing"

    # Not even a read. The earlier version of this test exempted `inspect`,
    # which would have let `approval.inspect` through on the argument that it
    # grants nothing — and the argument is true, right up until somebody adds
    # a field to the answer or a second verb beside it.
    granting = [t for t in tools if "approv" in t.lower()]
    assert granting == [], (
        f"the control-agent surface exposes {granting}. An approval is a human "
        "decision; a tool an agent can call to record one is a tool an agent "
        "can use to approve itself.")

    # And the daemon methods the surface can reach are a closed set, so a
    # granting verb cannot become reachable by adding a call site.
    source = (MCP / "agent_mcp.py").read_text()
    for method in ("agent.approval.record", "agent.approval.grant"):
        assert method not in source, method


def test_the_daemon_refuses_to_record_an_approval_from_a_caller(daemon):
    # Served and reachable on the node plane, and it records nothing. What is
    # missing is a transport that carries a bound human decision, and §6 says
    # to report that rather than improvise around it.
    response = _call(daemon, "agent.approval.record",
                     {"action": "session.stop", "context_ref": "human-context:x/y"})
    assert response["ok"] is True
    result = response["result"]
    assert result["recorded"] is False
    assert result["decision"] == "APPROVAL_TRANSPORT_UNSUPPORTED"
    assert "404" in result["reason"]
    assert daemon.control_store().approvals() == {}


def test_an_ungated_action_cannot_be_recorded_as_approved(daemon):
    result = _call(daemon, "agent.approval.record",
                   {"action": "session.list"})["result"]
    assert result["recorded"] is False
    assert result["decision"] == "NOT_GATED"
