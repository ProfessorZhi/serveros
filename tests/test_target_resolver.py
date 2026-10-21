#!/usr/bin/env python3
"""§3/§7/§8 — one request, one addressable target, and four ways to refuse it.

The property under test is not "the resolver returns AVAILABLE". It is that the
four facts it folds into one answer stay four facts:

    support     a CAPABILITY fact    repair: another provider
    readiness   a RUNTIME fact       repair: repair the machine
    admission   a POLICY decision    repair: ask a person
    route       a TRANSPORT fact     repair: deploy a node

A suite that only checked the happy path would pass against a resolver that
returned `UNAVAILABLE — provider not ready` for all four, which is the failure
mode this module exists to prevent. So most of what follows is refusals, and
each one asserts not only the status but which STEP refused and that the later
steps were not consulted.

The second property is that this is not a second resolver. `abilities/resolver.py`
already answers "can this device do this", `runtime/agent_sessions.py` already
decides admission, `runtime/approval.py` already decides the human gate. Those
are asserted by IDENTITY of the imported function objects, because a copy that
behaves identically today is a copy that disagrees the first time one of them is
edited — and `test_the_contract_and_the_code_name_the_same_vocabulary` is what
catches the day this module starts carrying its own list.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
ABILITIES = REPO / "abilities"
RUNTIME = REPO / "runtime"
CONTEXT = REPO / "context"
FLEET = REPO / "fleet"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


target = _load("serveros_abilities_target", ABILITIES / "target.py")
#: The name `abilities/target.py` and `runtime/cli.py` load this file under, and
#: the one `runtime/daemon.py`'s `_ABILITY_RESOLVER_NAME` documents as
#: load-bearing ("the CLI and the socket must not read two different worlds").
#: The assertion two hundred lines down compares this module with the one
#: `target` reaches, so it has to be the tree's name: under any other, the two
#: sides are two copies and the assertion is about nothing.
resolver = _load("serveros_ability_resolver", ABILITIES / "resolver.py")
provider_instance = _load("serveros_abilities_provider_instance",
                          ABILITIES / "provider_instance.py")
agent_sessions = _load("serveros_runtime_agent_sessions", RUNTIME / "agent_sessions.py")
approval = _load("serveros_runtime_approval", RUNTIME / "approval.py")
actor = _load("_serveros_context_actor", CONTEXT / "actor.py")
transport = _load("serveros_fleet_transport", FLEET / "transport.py")

NOW = "2026-09-14T00:00:00Z"
FRESH = "2026-09-13T23:59:00Z"

DEVICES = {
    "devices": [
        {"device_ref": "device:server-beijing", "display_name": "Beijing-Server",
         "platform": "linux", "roles": ["fleet-controller-primary"],
         "environment_profile": "linux-server", "agents": ["claude-code", "codex"]},
        {"device_ref": "device:work-fj", "display_name": "FJPCOS",
         "platform": "windows", "roles": ["corporate-workstation"],
         "environment_profile": "windows-corporate", "agents": ["claude-code", "codex"]},
        {"device_ref": "device:terminal-only", "display_name": "CodexOnly",
         "platform": "linux", "roles": ["build-runner"],
         "environment_profile": "linux-server", "agents": ["codex"]},
    ]
}

PROVIDER = {"id": "provider:botmux-agent-session", "kind": "local-service",
            "preference": 10}
RUNTIME_RECORD = {"id": "claude-code",
                  "consumable_provider_kinds": ["skill", "mcp-server", "cli",
                                                "local-service", "remote-service",
                                                "builtin"]}

ALL_ACTIONS_SUPPORTED = {action: {"state": "SUPPORTED"}
                         for action in agent_sessions.contract()["support"]["actions"]}


def instance(**overrides) -> dict:
    record = {
        "provider": "provider:botmux-agent-session",
        "device_ref": "device:server-beijing",
        "observed_at": FRESH,
        "status": "AVAILABLE",
        "status_provenance": "INFERRED",
        "reason": None,
        "action_support": dict(ALL_ACTIONS_SUPPORTED),
    }
    record.update(overrides)
    return record


def request(**overrides) -> dict:
    base = {
        "device_ref": "device:server-beijing",
        "agent_runtime": "agent-runtime:claude-code",
        "action": "session.create",
        "local_device_ref": "device:server-beijing",
        "resolved_at": NOW,
        "registry": transport.DeviceRegistry(DEVICES),
        "providers": [dict(PROVIDER)],
        "instances": [instance()],
        "runtime_record": dict(RUNTIME_RECORD),
    }
    base.update(overrides)
    return base


def step_outcomes(record: dict) -> list[tuple[str, str]]:
    return [(s["step"], s["outcome"]) for s in record["steps"]]


# ---------------------------------------------------------------------------
# The contract, and the code agreeing with it
# ---------------------------------------------------------------------------

def contract() -> dict:
    return yaml.safe_load((ABILITIES / "target-contract.yaml").read_text(encoding="utf-8"))


def test_the_contract_declares_the_steps_the_code_runs():
    assert target.steps_in_order() == tuple(contract()["steps"]["order"])
    assert set(target.STEP_FUNCTIONS) == set(target.steps_in_order())


def test_the_status_vocabulary_is_the_provider_contract_s_own():
    """Not a re-cut. §3 names AVAILABLE/DEGRADED/UNAVAILABLE/INCOMPATIBLE/UNKNOWN.

    If this module had its own list, a status reachable in one vocabulary and
    not the other would be silently dropped by whichever consumer read the
    shorter one.
    """
    provider_contract = yaml.safe_load(
        (ABILITIES / "provider-contract.yaml").read_text(encoding="utf-8"))
    assert target.statuses() == tuple(provider_contract["status"]["values"])
    assert target.statuses() == provider_instance.STATUSES


def test_the_reason_vocabulary_is_the_brief_s_fifteen_plus_what_the_kernels_return():
    """§8 names fifteen words. Two kernels produce six more between them.

    This test is the gate on that sentence. A word added to the contract
    without a verdict that produces it, or a verdict added to a kernel without
    a word here, both fail — which is the only way this stays a closed
    vocabulary rather than a list that grows quietly.

    The three coordination words were added deliberately, and this assertion
    is the review of that edit: it lists them by name, so a fourth could not
    arrive without somebody changing this line as well.
    """
    brief = {
        "DEVICE_UNREACHABLE", "NODE_RUNTIME_UNAVAILABLE", "AGENT_RUNTIME_UNAVAILABLE",
        "PROVIDER_UNAVAILABLE", "ACTION_UNSUPPORTED", "READINESS_UNKNOWN",
        "POLICY_DENIED", "APPROVAL_REQUIRED", "APPROVAL_BINDING_MISMATCH",
        "SESSION_NOT_FOUND", "TURN_FAILED", "PROVIDER_ERROR", "TIMEOUT",
        "INVALID_CONTEXT", "UNTRUSTED_ACTOR",
    }
    approval_words = {"APPROVAL_CONTEXT_MISMATCH", "APPROVAL_EXPIRED",
                      "APPROVAL_ALREADY_CONSUMED"}
    coordination_words = {"THREAD_ITEM_MISMATCH", "READ_ONLY_CLOSURE",
                          "THREAD_CLOSED_FOR_NEW_WORK"}
    declared = set(target.reason_vocabulary())
    assert brief <= declared
    assert declared - brief == approval_words | coordination_words
    assert contract()["reasons"]["from_brief"] == 15
    assert set(contract()["reasons"]["declared_here_only"]) == declared - brief


def test_every_approval_verdict_this_kernel_can_return_is_in_the_vocabulary():
    """Read out of `decide`'s own source, so the gate cannot be satisfied by
    editing the contract alone."""
    source = (RUNTIME / "approval.py").read_text(encoding="utf-8")
    declared = set(target.reason_vocabulary())
    for word in ("APPROVAL_REQUIRED", "APPROVAL_BINDING_MISMATCH",
                 "APPROVAL_CONTEXT_MISMATCH", "APPROVAL_EXPIRED",
                 "APPROVAL_ALREADY_CONSUMED"):
        assert f'"{word}"' in source, f"{word} is not produced by approval.decide"
        assert word in declared, f"{word} is produced by decide and not declared here"
    assert "ALLOW" not in declared  # a verdict, not a refusal reason


def test_the_provider_reason_translation_covers_every_reason_the_contract_declares():
    """A provider reason with no translation would fall through to a default
    and report a provider fault as something else."""
    provider_contract = yaml.safe_load(
        (ABILITIES / "provider-contract.yaml").read_text(encoding="utf-8"))
    assert set(target.PROVIDER_REASON_TRANSLATION) == set(provider_contract["status"]["reasons"])
    assert set(target.PROVIDER_REASON_TRANSLATION.values()) <= set(target.reason_vocabulary())


# ---------------------------------------------------------------------------
# Not a second resolver
# ---------------------------------------------------------------------------

def test_the_capability_kernel_is_imported_and_not_reimplemented():
    assert target.resolver.resolve is resolver.resolve


def test_the_admission_and_support_kernel_is_imported_and_not_reimplemented():
    assert target.sessions.admission is agent_sessions.admission
    assert target.sessions.support is agent_sessions.support


def test_the_readiness_kernel_is_imported_and_not_reimplemented():
    assert target.provider_instance.refusal is provider_instance.refusal
    assert target.provider_instance.is_stale is provider_instance.is_stale


def test_the_approval_and_identity_kernels_are_imported_and_not_reimplemented():
    assert target.approval.decide is approval.decide
    assert target.actor.build is actor.build


def test_the_device_registry_is_the_fleet_one():
    assert target.transport.DeviceRegistry is transport.DeviceRegistry


def test_the_module_carries_no_closed_vocabulary_of_its_own():
    """The module may not hold a list of legal values.

    Checked by parsing rather than by behaviour, because a duplicated list
    behaves identically on the day it is written — which is exactly why it is
    worth refusing here and not worth trusting to a happy-path assertion. The
    two dicts the module does carry, `PROVIDER_REASON_TRANSLATION` and
    `STEP_STATUS`, are mappings whose keys and values are both drawn from
    contracts, and both are already asserted against those contracts above.
    A list, tuple or set of string literals at module level has no such defence.
    """
    tree = ast.parse((ABILITIES / "target.py").read_text(encoding="utf-8"))
    offenders = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
            continue
        if all(isinstance(e, ast.Constant) and isinstance(e.value, str)
               for e in node.value.elts):
            offenders.append([t.id for t in targets if isinstance(t, ast.Name)])
    assert offenders == [], f"module-level value vocabularies: {offenders}"


def test_the_steps_that_may_be_unevaluable_are_declared_and_agent_runtime_is_not_among_them():
    """The declared list has to be the real one.

    This test exists because the first version of the contract declared two
    steps unevaluable and the code could produce six, which nothing failed on.
    A declaration that is narrower than the behaviour is how a reader comes to
    trust a `reason: null` as "the provider is fine".
    """
    declared = set(contract()["steps"]["unevaluable"]["steps"])
    assert declared == {"device", "provider_instance", "adapter", "support",
                        "readiness", "admission", "route"}
    assert contract()["steps"]["unevaluable"]["never"] == ["agent_runtime"]

    # And the one step declared never-unevaluable is genuinely never so: with
    # every piece of optional evidence absent, the runtime check still answered.
    # `device:work-fj` because this test needs a device that declares BOTH
    # runtimes: the point is that the runtime step answers even when every
    # other input is absent. It used to be `device:server-beijing`, which was
    # retired on 2026-10-21.
    starved = target.resolve({"device_ref": "device:work-fj",
                              "agent_runtime": "agent-runtime:codex",
                              "action": "session.create",
                              "local_device_ref": "device:work-fj",
                              "resolved_at": NOW})
    assert "agent_runtime" not in starved["unevaluated"]
    runtime_step = next(s for s in starved["steps"] if s["step"] == "agent_runtime")
    assert runtime_step["outcome"] == "AGREED"
    assert starved["unevaluated"] == ["provider_instance", "adapter", "support",
                                      "readiness"]


def test_a_step_without_a_resolved_provider_reports_itself_unevaluable_rather_than_crashing():
    """The chain continues past an unevaluable step, so a later step can run
    with no instance to read. Reaching for the missing key would raise out of
    `resolve`, which takes a resolution that has an answer and turns it into a
    traceback at the MCP boundary."""
    record = target.resolve(request(instances=None))
    assert record["status"] == "UNKNOWN"
    assert record["reason"] is None
    assert record["unevaluated"] == ["provider_instance", "adapter", "support",
                                     "readiness"]
    for step in record["steps"]:
        if step["step"] in ("adapter", "support", "readiness"):
            assert step["unevaluable_because"] == "provider_instance"
    # `route` is the one step that still answers, because the request named a
    # local device and that is a fact the request itself supplied.
    assert record["steps"][-1]["step"] == "route"
    assert record["steps"][-1]["outcome"] == "AGREED"
    assert record["target"]["route"]["kind"] == "local"


# ---------------------------------------------------------------------------
# Identity: what may and may not be a runtime or a device
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "claude-code",                       # the bare id
    "tmux:0",                            # a pane
    "pid:430178",                        # a process
    "/usr/bin/claude",                   # a binary path
    "container:serveros-claude",         # a container name
    "botmux:bot-7",                      # a provider's own bot id
])
def test_a_runtime_identity_that_is_not_canonical_is_refused(bad):
    with pytest.raises(target.Rejected) as exc:
        target.resolve(request(agent_runtime=bad))
    assert "Agent Runtime ref" in str(exc.value)


def test_an_undeclared_runtime_id_is_refused_rather_than_resolved_by_resemblance():
    with pytest.raises(target.Rejected) as exc:
        target.resolve(request(agent_runtime="agent-runtime:codex-app-server"))
    assert "does not declare" in str(exc.value)


def test_a_device_ref_that_is_not_a_device_ref_is_refused():
    with pytest.raises(target.Rejected) as exc:
        target.resolve(request(device_ref="FJPCOS"))
    assert "device ref" in str(exc.value)


def test_an_unenrolled_device_is_a_request_error_not_a_runtime_fact():
    """Enrolment is a human decision. There is nothing here to go and observe,
    so this refuses rather than returning a status that reads like a machine
    that might come back."""
    with pytest.raises(target.Rejected) as exc:
        target.resolve(request(device_ref="device:not-a-machine"))
    assert "not an enrolled device" in str(exc.value)


def test_the_provider_instance_ref_is_derived_from_the_two_declared_fields():
    record = target.resolve(request())
    device_ref, provider_id = target.parse_provider_instance_ref(
        record["target"]["provider_instance_ref"])
    assert device_ref == "device:server-beijing"
    assert provider_id == "provider:botmux-agent-session"


def test_the_adapter_ref_is_minted_from_the_pattern_the_provider_contract_declares():
    """One ref format, one declaration.

    The pattern is read out of `provider-contract.yaml` and used to rebuild the
    expected ref, so the two files cannot come to spell the same ref two ways —
    which for a ref is the same as having two refs.
    """
    provider_contract = yaml.safe_load(
        (ABILITIES / "provider-contract.yaml").read_text(encoding="utf-8"))
    pattern = provider_contract["objects"]["Adapter"]["id_pattern"]
    record = target.resolve(request())
    assert record["target"]["adapter_ref"] == \
        pattern.replace("<runtime>", "claude-code") \
               .replace("<provider>", "provider:botmux-agent-session")
    refs = contract()["refs"]
    assert refs["adapter_ref_source"].endswith(
        "provider-contract.yaml#objects.Adapter.id_pattern")


# ---------------------------------------------------------------------------
# The happy path, and what it does not claim
# ---------------------------------------------------------------------------

def test_a_resolvable_target_names_every_field_and_agrees_at_every_step():
    record = target.resolve(request())
    assert record["status"] == "AVAILABLE"
    assert record["reason"] is None
    assert step_outcomes(record) == [(n, "AGREED") for n in target.steps_in_order()]
    for field in contract()["record"]["target"]["required_fields"]:
        assert field in record["target"]
    assert record["target"]["route"]["kind"] == "local"


def test_an_available_target_still_reports_its_support_readiness_and_admission():
    """The three are always present and never merged, even when all three
    agreed. A record that only carries them on failure cannot be diffed."""
    record = target.resolve(request())
    assert record["support"]["state"] == "SUPPORTED"
    assert record["readiness"]["status"] == "AVAILABLE"
    assert record["admission"]["decision"] == "ALLOW"
    assert record["admission"]["class"] == "delegation"
    assert record["admission_required"] is False


def test_the_route_level_follows_the_admission_class_not_the_action_name():
    read = target.resolve(request(action="session.list"))
    write = target.resolve(request(action="session.create"))
    assert read["admission"]["class"] == "read_only"
    assert read["target"]["route"]["level"] == "inspect"
    assert write["admission"]["class"] == "delegation"
    assert write["target"]["route"]["level"] == "execute-safe"


def test_every_resolved_field_carries_provenance():
    record = target.resolve(request())
    steps = {p["step"] for p in record["provenance"]}
    assert "provider_instance" in steps
    assert "support" in steps
    for entry in record["provenance"]:
        assert set(entry) == {"step", "field", "source", "observed_at", "strength"}


def test_a_field_the_chain_never_reached_is_null_and_the_step_list_says_so():
    record = target.resolve(request(
        instances=[instance(action_support={"session.create": {"state": "UNSUPPORTED"}})]))
    assert record["target"]["route"] is None          # never evaluated
    assert record["readiness"] is None                # never evaluated
    assert step_outcomes(record)[-1] == ("support", "REFUSED")


def test_candidates_are_reported_without_the_records_under_them():
    record = target.resolve(request())
    for candidate in record["candidates"]:
        assert set(candidate) == {"provider", "status", "reason", "detail"}


# ---------------------------------------------------------------------------
# Support is not readiness is not admission
# ---------------------------------------------------------------------------

def test_an_unsupported_action_is_incompatible_and_never_a_pending_approval():
    """§24: UNSUPPORTED is refused, and it is never reported as an approval
    question, because no approval can grant a capability a provider lacks."""
    record = target.resolve(request(
        action="session.stop",
        instances=[instance(action_support={"session.stop": {
            "state": "UNSUPPORTED", "reason": "BotMux close is not turn interrupt"}})],
        context=verified_context()))
    assert record["status"] == "INCOMPATIBLE"
    assert record["reason"] == "ACTION_UNSUPPORTED"
    assert record["refused_by"] == "support"
    assert record["admission_required"] is False
    assert record["admission"] is None
    assert "close is not turn interrupt" in record["detail"]


def test_an_action_absent_from_a_published_table_is_unsupported_not_unknown():
    """A closed vocabulary with a hole in it is a "no" for the hole. Reading it
    as "nobody asked" would make every action a provider omitted look like one
    that might work."""
    record = target.resolve(request(
        action="session.stop",
        instances=[instance(action_support={"session.create": {"state": "SUPPORTED"}})]))
    assert record["support"]["state"] == "UNSUPPORTED"
    assert record["status"] == "INCOMPATIBLE"


def test_a_provider_with_no_action_support_table_at_all_is_unknown_and_not_a_refusal():
    """A provider that published no table has not said no.

    The step's own verdict is UNKNOWN, which makes it UNEVALUABLE rather than
    REFUSED, so `record.reason` stays null and the answer lives in
    `record.unevaluated`. Reporting a refusal word here would tell a caller the
    verb does not exist when what happened is that nobody asked.
    """
    record = target.resolve(request(instances=[instance(action_support=None)]))
    assert record["support"]["state"] == "UNKNOWN"
    assert record["status"] == "UNKNOWN"
    assert record["reason"] is None
    assert "refused_by" not in record
    # `route` answered: a local device is a fact the request itself supplied.
    assert record["unevaluated"] == ["support"]
    support_step = next(s for s in record["steps"] if s["step"] == "support")
    assert support_step["outcome"] == "UNEVALUABLE"
    assert support_step["reason"] == "ACTION_UNSUPPORTED"
    assert "no action_support table" in support_step["detail"]


def test_a_supported_action_on_a_down_provider_is_unavailable_and_not_unsupported():
    """Support stays SUPPORTED. Reporting the readiness failure as a reason to
    change it is how a working capability gets withdrawn because a machine was
    briefly broken."""
    record = target.resolve(request(instances=[instance(
        status="UNAVAILABLE", reason="EXECUTION_FAILED",
        reason_text="the provider accepted the action and it failed")]))
    assert record["status"] == "UNAVAILABLE"
    assert record["reason"] == "PROVIDER_UNAVAILABLE"
    assert record["refused_by"] == "readiness"
    assert record["support"]["state"] == "SUPPORTED"
    assert record["readiness"]["reason"] == "EXECUTION_FAILED"


def test_a_stale_failure_does_not_poison_the_provider_forever():
    """§23. Ageing a negative into a positive would be lying; keeping it as a
    current negative would be a permanent verdict from an old observation. The
    answer is UNKNOWN, which is neither."""
    record = target.resolve(request(
        resolved_at="2026-09-14T12:00:00Z",
        instances=[instance(status="UNAVAILABLE", reason="EXECUTION_FAILED",
                            observed_at="2026-09-13T00:00:00Z")]))
    assert record["status"] == "UNKNOWN"
    assert record["reason"] is None
    assert record["refused_by"] if "refused_by" in record else True
    assert record["readiness"]["status"] == "UNKNOWN"
    assert record["readiness"]["fresh"] is False


def test_a_stale_success_does_not_age_into_an_available_provider():
    record = target.resolve(request(
        resolved_at="2026-09-14T12:00:00Z",
        instances=[instance(status="AVAILABLE", observed_at="2026-09-13T00:00:00Z")]))
    assert record["status"] == "UNKNOWN"
    assert record["readiness"]["status"] == "UNKNOWN"
    assert record["readiness"]["fresh"] is False
    assert record["unevaluated"] == ["readiness"]
    stale_step = next(s for s in record["steps"] if s["step"] == "readiness")
    assert "older than the readiness window" in stale_step["detail"]
    # The status the provider last reported is kept verbatim beside it. A record
    # that overwrote AVAILABLE with UNKNOWN would lose the only fact anyone has.
    assert record["readiness"]["reported_status"] == "AVAILABLE"
    assert record["readiness"]["observed_at"] == "2026-09-13T00:00:00Z"


def test_a_degraded_provider_is_a_positive_answer_with_a_caveat():
    record = target.resolve(request(instances=[instance(status="DEGRADED")]))
    assert record["status"] == "DEGRADED"
    assert record["reason"] is None
    assert record["readiness"]["status"] == "DEGRADED"


def test_readiness_alone_never_refuses_an_action_support_has_not_admitted():
    """The order runs support, then readiness, then admission — so a provider
    that is UNSUPPORTED for a verb is refused for THAT reason even when it is
    also down. A caller that learned "unavailable" would go and repair a
    machine and find the verb still does not exist."""
    record = target.resolve(request(
        action="session.stop",
        instances=[instance(status="UNAVAILABLE", reason="EXECUTION_FAILED",
                            action_support={"session.stop": {"state": "UNSUPPORTED"}})]))
    assert record["refused_by"] == "support"
    assert record["reason"] == "ACTION_UNSUPPORTED"


# ---------------------------------------------------------------------------
# Device and runtime resolution
# ---------------------------------------------------------------------------

def test_an_undeployed_node_is_named_as_such_and_not_as_an_unknown_device():
    """§13. `device:work-fj` is an enrolled machine with no ServerOS node on it.
    Reporting "unknown device" would send someone to check enrolment."""
    record = target.resolve(request(
        device_ref="device:work-fj",
        node_runtime={"device:work-fj": {"state": "ABSENT",
                                         "provenance": "fleet relay"}}))
    assert record["status"] == "UNAVAILABLE"
    assert record["reason"] == "NODE_RUNTIME_UNAVAILABLE"
    assert record["refused_by"] == "device"
    assert record["steps"][0]["outcome"] == "REFUSED"


def test_an_offline_device_is_unreachable():
    record = target.resolve(request(
        device_ref="device:work-fj",
        presence={"device:work-fj": {"state": "KNOWN_OFFLINE"}}))
    assert record["reason"] == "DEVICE_UNREACHABLE"
    assert record["refused_by"] == "device"


def test_an_enrolled_device_nobody_has_observed_is_unknown_not_unavailable():
    """§13's distinction, on the device step itself.

    Everything else about this request resolves — the device is enrolled, it
    declares the runtime, a provider instance is registered on it — and the
    only thing missing is whether anything can reach it. That is an input to
    go and collect, not a verdict about the machine.
    """
    record = target.resolve(request(device_ref="device:work-fj",
                                    instances=[instance(device_ref="device:work-fj")]))
    assert record["status"] == "UNKNOWN"
    assert record["reason"] is None
    # Two unevaluable steps, and they are two different missing inputs: nobody
    # has reported reachability, and this request runs on a different machine so
    # the route cannot be called local either.
    assert record["unevaluated"] == ["device", "route"]
    assert record["target"]["provider_instance_ref"] is not None
    assert "refused_by" not in record


def test_a_reachable_remote_device_routes_through_the_fleet():
    record = target.resolve(request(
        device_ref="device:work-fj",
        presence={"device:work-fj": {"state": "KNOWN_ONLINE",
                                     "provenance": "fleet relay"}},
        instances=[instance(device_ref="device:work-fj")]))
    assert record["status"] == "AVAILABLE"
    assert record["target"]["route"]["kind"] == "fleet"
    assert record["target"]["route"]["level"] == "execute-safe"


def test_a_runtime_the_device_does_not_declare_is_unavailable():
    record = target.resolve(request(device_ref="device:terminal-only",
                                    local_device_ref="device:terminal-only"))
    assert record["status"] == "UNAVAILABLE"
    assert record["reason"] == "AGENT_RUNTIME_UNAVAILABLE"
    assert record["refused_by"] == "agent_runtime"
    assert record["steps"][1]["outcome"] == "REFUSED"


def test_an_unevaluable_device_route_does_not_stop_a_later_definite_refusal():
    """A definite "no" outranks an indefinite one, and the definite one is what
    the caller can act on."""
    record = target.resolve(request(device_ref="device:work-fj", instances=[]))
    assert record["refused_by"] == "provider_instance"
    assert record["reason"] == "PROVIDER_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Provider instance and adapter resolution
# ---------------------------------------------------------------------------

def test_a_provider_with_no_instance_on_the_device_is_unavailable():
    record = target.resolve(request(instances=[]))
    assert record["reason"] == "PROVIDER_UNAVAILABLE"
    assert record["refused_by"] == "provider_instance"


def test_an_instance_from_another_device_is_not_this_device_s_instance():
    record = target.resolve(request(
        instances=[instance(device_ref="device:work-fj")]))
    assert record["reason"] == "PROVIDER_UNAVAILABLE"


def test_an_absent_instance_list_is_unevaluable_and_an_empty_one_is_a_no():
    """The distinction `abilities/resolver.py` paid for once already: not
    supplying the list is not a statement that the provider is installed."""
    absent = target.resolve(request(instances=None))
    empty = target.resolve(request(instances=[]))
    assert absent["status"] == "UNKNOWN"
    assert empty["status"] == "UNAVAILABLE"


def test_the_preferred_provider_wins_and_the_losing_candidates_are_reported():
    second = {"id": "provider:other-agent-session", "kind": "local-service",
              "preference": 50}
    record = target.resolve(request(
        providers=[second, dict(PROVIDER)],
        instances=[instance(),
                   instance(provider="provider:other-agent-session")]))
    assert record["target"]["provider_ref"] == "provider:botmux-agent-session"
    assert [c["provider"] for c in record["candidates"]] == \
        ["provider:botmux-agent-session", "provider:other-agent-session"]


def test_a_pinned_provider_that_has_no_instance_here_does_not_fall_back():
    record = target.resolve(request(provider_id="provider:nonexistent"))
    assert record["status"] == "UNAVAILABLE"
    assert record["target"]["provider_ref"] is None


def test_a_runtime_with_no_adapter_for_the_provider_is_agent_unsupported():
    record = target.resolve(request(
        providers=[{"id": "provider:botmux-agent-session", "kind": "local-service",
                    "preference": 10, "adapters": ["codex"]}]))
    assert record["status"] == "INCOMPATIBLE"
    assert record["reason"] == "AGENT_RUNTIME_UNAVAILABLE"
    assert record["refused_by"] == "adapter"
    assert "AGENT_UNSUPPORTED" in record["detail"]


def test_a_provider_kind_the_runtime_cannot_consume_is_incompatible():
    record = target.resolve(request(
        providers=[{"id": "provider:botmux-agent-session", "kind": "gui-application",
                    "preference": 10}]))
    assert record["status"] == "INCOMPATIBLE"
    assert record["refused_by"] == "adapter"


def test_adapter_consumability_that_was_never_gathered_is_unknown_not_agreement():
    record = target.resolve(request(runtime_record=None))
    assert record["status"] == "UNKNOWN"
    assert "adapter" in record["unevaluated"]


# ---------------------------------------------------------------------------
# Admission, identity and the human gate
# ---------------------------------------------------------------------------

def verified_context(**overrides) -> dict:
    report = {
        "human_control_provider": "provider:botmux-human-control",
        "actor_ref": "human:ou_abc", "transport": "feishu",
        "transport_identity": "open_id:ou_abc",
        "conversation_ref": "oc_1", "message_ref": "om_1",
        "observed_at": FRESH, "provider_status": "VERIFIED", "provenance": "FACT",
    }
    report.update(overrides)
    return actor.build(report, now=NOW)


def approval_for(context: dict, action: str = "session.stop", params=None) -> dict:
    return approval.issue(
        action=action,
        target={"device_ref": "device:server-beijing", "session_ref": None,
                "agent_runtime": "agent-runtime:claude-code",
                "provider": "provider:botmux-agent-session"},
        params=params or {}, context=context, now=NOW)


GATED = {"session.stop": {"state": "SUPPORTED"}}


def gated_request(**overrides) -> dict:
    base = request(action="session.stop",
                   instances=[instance(action_support=dict(GATED))])
    base.update(overrides)
    return base


def test_a_human_gated_action_without_a_context_asks_for_an_approval():
    record = target.resolve(gated_request())
    assert record["status"] == "UNAVAILABLE"
    assert record["reason"] == "APPROVAL_REQUIRED"
    assert record["admission_required"] is True
    assert record["admission"]["decision"] == "REQUIRE_HUMAN"


def test_a_context_ref_with_no_resolved_context_is_invalid_not_approved():
    """A ref on its own is a claim. Identity is resolved against what ServerOS
    minted; accepting the ref would let a caller name any context it liked."""
    record = target.resolve(gated_request(context_ref="human-context:botmux-x/deadbeef"))
    assert record["reason"] == "INVALID_CONTEXT"
    assert "no context was resolved from it" in record["detail"]


def test_a_forged_identity_key_in_the_request_is_refused_by_name():
    record = target.resolve(gated_request(context=verified_context(),
                                          actor_ref="human:attacker"))
    assert record["reason"] == "INVALID_CONTEXT"
    assert "actor_ref" in record["detail"]
    assert "may not carry identity" in record["detail"]


def test_a_verified_context_without_an_approval_still_requires_one():
    record = target.resolve(gated_request(context=verified_context()))
    assert record["reason"] == "APPROVAL_REQUIRED"
    assert record["admission_required"] is True


def test_a_context_that_did_not_verify_its_human_cannot_authorize_a_mutation():
    """§67's standing fact: exit 0 is not a verified human.

    `UNSUPPORTED` rather than a made-up `UNVERIFIED`, because the provider
    contract's three words are VERIFIED / BLOCKED / UNSUPPORTED and the last of
    them is the one that describes this deployment: BotMux is installed and
    callable and has no surface that verifies a person.
    """
    blocked = verified_context(provider_status="UNSUPPORTED", provenance="UNKNOWN")
    record = target.resolve(gated_request(context=blocked))
    assert record["reason"] == "UNTRUSTED_ACTOR"
    assert "did not verify" in record["detail"]
    assert "UNSUPPORTED" in record["detail"]


def test_an_expired_context_cannot_authorize_a_mutation():
    context = verified_context(observed_at="2026-09-13T00:00:00Z")
    record = target.resolve(gated_request(
        context=context, approvals=[approval_for(context)]))
    assert record["reason"] == "UNTRUSTED_ACTOR"


def test_an_approval_for_exactly_this_request_allows_it():
    context = verified_context()
    record = target.resolve(gated_request(
        context=context, approvals=[approval_for(context)]))
    assert record["status"] == "AVAILABLE"
    assert record["admission"]["class"] == "human_gated"
    assert record["admission_required"] is True
    assert record["approval_ref"] == approval_for(context)["approval_ref"]


def test_changing_a_parameter_invalidates_the_approval():
    context = verified_context()
    record = target.resolve(gated_request(
        context=context, approvals=[approval_for(context)], params={"force": True}))
    assert record["reason"] == "APPROVAL_BINDING_MISMATCH"


def test_an_approval_decided_from_another_context_is_reported_as_such():
    """Not as "not approved": an approval exists, and reporting a bare refusal
    would send the requester to ask for one that is already there."""
    other = verified_context(conversation_ref="oc_2", message_ref="om_2")
    record = target.resolve(gated_request(
        context=verified_context(), approvals=[approval_for(other)]))
    assert record["reason"] == "APPROVAL_CONTEXT_MISMATCH"


def test_an_already_spent_approval_is_not_reusable():
    context = verified_context()
    spent = approval.consume(approval_for(context), consumed_by="probe", now=NOW)
    record = target.resolve(gated_request(context=context, approvals=[spent]))
    assert record["reason"] == "APPROVAL_ALREADY_CONSUMED"


def test_an_expired_approval_is_reported_as_expired():
    context = verified_context()
    record = target.resolve(gated_request(
        context=context, approvals=[approval_for(context)],
        resolved_at="2026-09-16T00:00:00Z"))
    # The context ages out first — 900s, the same window the approval uses — so
    # the honest answer is that nothing here authorizes anything any more.
    assert record["reason"] in ("UNTRUSTED_ACTOR", "APPROVAL_EXPIRED")


def test_a_delegating_action_never_asks_for_an_approval_even_with_a_context():
    record = target.resolve(request(context=verified_context()))
    assert record["admission_required"] is False
    assert record["admission"]["class"] == "delegation"


def test_an_undeclared_action_is_refused_as_a_policy_decision():
    record = target.resolve(request(action="runtime.wipe",
                                    instances=[instance(action_support={
                                        "runtime.wipe": {"state": "SUPPORTED"}})]))
    assert record["refused_by"] == "admission"
    assert record["reason"] == "POLICY_DENIED"


# ---------------------------------------------------------------------------
# The disclosure line
# ---------------------------------------------------------------------------

def test_summarize_is_one_line_in_every_outcome():
    outcomes = [
        target.resolve(request()),
        target.resolve(request(instances=[], )),
        target.resolve(request(runtime_record=None)),
    ]
    for record in outcomes:
        line = target.summarize(record)
        assert "\n" not in line
        assert len(line) < 160


# ---------------------------------------------------------------------------
# The CLI, which may not probe anything
# ---------------------------------------------------------------------------

def test_the_cli_answers_from_a_supplied_evidence_file(tmp_path):
    evidence = {"local_device_ref": "device:llm-lab-dell", "resolved_at": NOW,
                "providers": [dict(PROVIDER)], "instances": [instance()],
                "runtime_record": dict(RUNTIME_RECORD)}
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    assert target.main(["--device", "device:llm-lab-dell",
                        "--runtime", "agent-runtime:claude-code",
                        "--action", "session.create",
                        "--evidence", str(path)]) == 0


def test_the_cli_reports_a_malformed_request_on_stderr_and_exits_two(tmp_path, capsys):
    assert target.main(["--device", "FJPCOS",
                        "--action", "session.create"]) == 2
    assert "device ref" in capsys.readouterr().err
