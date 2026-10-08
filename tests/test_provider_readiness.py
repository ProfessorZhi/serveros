#!/usr/bin/env python3
"""Execution readiness — the axis ServerOS could not ask about.

The vertical slice drove a provider that was reachable by every measure the
product had: `/api/sessions` listed, `/api/bots` listed, `/api/trigger` accepted
a real dispatch and returned a real sessionId, and every declared action was
SUPPORTED. No turn ever ran. The provider's execution backend was gone, and
nothing in ServerOS could say so, because there was one axis and the provider
passed it.

These tests are about the second axis, and about the three refusals staying
three: capability, readiness, and authorization.
"""
from __future__ import annotations

import datetime
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"
MCP = REPO / "mcp"
ABILITIES = REPO / "abilities"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


instances = _load("serveros_abilities_provider_instance", ABILITIES / "provider_instance.py")
service_mod = _load("serveros_agent_session_service", MCP / "agent_session_service.py")
botmux_mod = _load("serveros_botmux_provider", MCP / "botmux_provider.py")
semantics = _load("serveros_runtime_agent_sessions", RUNTIME / "agent_sessions.py")
health = _load("serveros_runtime_health", RUNTIME / "health.py")

BotMuxProvider = botmux_mod.BotMuxProvider
BotMuxConfig = botmux_mod.BotMuxConfig
BotMuxError = botmux_mod.BotMuxError
SessionService = service_mod.SessionService
ProviderNotReady = service_mod.ProviderNotReady
ActionNotSupported = service_mod.ActionNotSupported
ActionNotAdmitted = service_mod.ActionNotAdmitted
session_ref = semantics.session_ref
support = semantics.support

PROVIDER = "provider:botmux-agent-session"
DEVICE = "device:server-beijing"

# The failure the slice hit, verbatim from the provider's own trigger-result.
LIVE_FAILURE = {
    "state": "failed",
    "errorCode": "no_output",
    "error": "上一次派发结果未知（歧义崩溃），按至多一次语义不重跑",
    "triggerId": "trg_1",
}


def at(iso: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))


class Clock:
    """A clock under the test's control, so freshness is tested and not waited for.

    Injected rather than monkeypatched: `observed_at` should be a property of
    the call rather than of whichever wall this host happens to have, and a
    module that reads the real clock in one place cannot be tested for ageing
    anywhere.
    """

    def __init__(self, iso: str) -> None:
        self.moment = at(iso)

    def __call__(self) -> datetime.datetime:
        return self.moment

    def advance(self, seconds: int) -> None:
        self.moment = self.moment + datetime.timedelta(seconds=seconds)


def botmux(responses, *, clock=None):
    provider = BotMuxProvider(BotMuxConfig(dashboard_token="test-token"), clock=clock)
    calls = []

    def request(method, path, *, body=None, require_auth=False):
        calls.append((method, path))
        key = (method, path.split("?", 1)[0])
        value = responses[key]
        if isinstance(value, Exception):
            raise value
        return value() if callable(value) else value

    provider._request = request
    return provider, calls


SESSIONS = [
    {"sessionId": "s1", "larkAppId": "b1", "cliId": "claude-code",
     "status": "dormant", "queued": False},
]


# ---------------------------------------------------------------------------
# §3: API reachable + executor unknown
# ---------------------------------------------------------------------------

def test_a_fresh_instance_is_unknown_and_never_available():
    """Nothing has been called, so nothing has been established.

    The tempting default is AVAILABLE — the provider is configured, it is
    listed, it is the one we meant. That default is the defect this round is
    about: a value nobody measured, rendered in the word that means somebody
    did.
    """
    provider, _ = botmux({("GET", "/api/sessions"): {"sessions": SESSIONS}})
    record = provider.observe(device_ref=DEVICE)
    assert record["status"] == "UNKNOWN"
    assert record["reason"] == "EXECUTION_UNVERIFIED"
    assert record["status_provenance"] == "UNKNOWN"
    assert record["persistence"] == "none"


def test_the_control_plane_answering_is_recorded_as_a_fact_and_not_as_readiness():
    """`/api/sessions` answering says the API is up. Only that.

    This is the exact confusion the live slice produced: everything that could
    be asked answered, and the answer to "does it work" was still no.
    """
    provider, _ = botmux({("GET", "/api/sessions"): {"sessions": SESSIONS}})
    record = provider.observe(device_ref=DEVICE)
    by_axis = {o.get("state"): o for o in record["observations"]}
    control = next(o for o in record["observations"]
                   if o["observed_by"]["type"] == "provider-response")
    assert control["state"] == "RUNNING"
    assert control["observed_by"] == {"type": "provider-response", "id": PROVIDER}
    assert control["evidence"]["type"] == "provider-response"
    assert control["evidence_authoritative"] is True
    # ...and the overall answer is still not AVAILABLE.
    assert record["status"] == "UNKNOWN"
    assert "RUNNING" in by_axis


def test_an_unreachable_control_plane_is_a_finding_and_not_an_exception():
    provider, _ = botmux({
        ("GET", "/api/sessions"): BotMuxError("connection refused"),
    })
    record = provider.observe(device_ref=DEVICE)
    assert record["status"] == "UNAVAILABLE"
    assert record["reason"] == "RUNTIME_UNHEALTHY"
    control = record["observations"][0]
    assert control["state"] == "STOPPED"
    # Measured from this host, not reported by the provider — and it says so,
    # because the two are not equally strong.
    assert control["observed_by"]["type"] == "host-measurement"


# ---------------------------------------------------------------------------
# §3: API reachable + negative execution evidence
# ---------------------------------------------------------------------------

def test_a_real_failed_turn_makes_the_provider_unavailable():
    """The live failure, replayed through the code path that observed it."""
    provider, _ = botmux({
        ("GET", "/api/sessions"): {"sessions": SESSIONS},
        ("GET", "/api/sessions/s1/trigger-result"): LIVE_FAILURE,
    })
    result = provider.result(provider_session_id="s1")
    assert result["turn_state"] == "TURN_FAILED"

    record = provider.observe(device_ref=DEVICE)
    assert record["status"] == "UNAVAILABLE"
    assert record["reason"] == "EXECUTION_FAILED"
    # Both axes, at once, describing a provider that is up and not working.
    # Indexed by which axis, not by who observed it — both are provider
    # responses, which is the point: the same call answered and failed.
    control = next(o for o in record["observations"]
                   if o["evidence"].get("source") == "botmux:/api/sessions")
    assert control["state"] == "RUNNING"
    assert control["evidence"]["source"] == "botmux:/api/sessions"
    assert control["observed_by"]["type"] == "provider-response"
    execution = next(o for o in record["observations"]
                     if o["evidence"].get("error_code"))
    assert execution["state"] == "FAILED"
    assert execution["reason"] == "EXECUTION_FAILED"
    assert execution["evidence"]["error_code"] == "no_output"
    assert execution["observed_at"].endswith("Z")


def test_a_running_turn_is_not_execution_evidence_in_either_direction():
    """TURN_RUNNING settles nothing, so it is recorded as nothing.

    A turn that has not finished has not established that execution works or
    that it does not. Recording it would be a readiness value derived from a
    fact that is not yet one.
    """
    provider, _ = botmux({
        ("GET", "/api/sessions"): {"sessions": SESSIONS},
        ("GET", "/api/sessions/s1/trigger-result"): {"state": "running"},
    })
    provider.result(provider_session_id="s1")
    assert provider._execution_log == []
    assert provider.observe(device_ref=DEVICE)["status"] == "UNKNOWN"


def test_a_successful_turn_is_the_only_thing_that_produces_available():
    provider, _ = botmux({
        ("GET", "/api/sessions"): {"sessions": SESSIONS},
        ("GET", "/api/sessions/s1/trigger-result"): {"state": "completed"},
    })
    provider.result(provider_session_id="s1")
    record = provider.observe(device_ref=DEVICE)
    assert record["status"] == "AVAILABLE"
    assert record["reason"] is None
    assert record["status_provenance"] == "INFERRED"


# ---------------------------------------------------------------------------
# §3: stale evidence does not permanently poison the provider
# ---------------------------------------------------------------------------

def test_a_negative_observation_ages_out_instead_of_condemning_the_provider():
    """Freshness, and the direction the fallback is allowed to go.

    An execution failure observed now is a real verdict now. Fifteen minutes
    later the machine may have been repaired, and a stored NEGATIVE that keeps
    refusing is the failure mode this rule exists for.

    Note which way it falls back: to UNKNOWN, so the next call is made and
    resolves it. It never falls back to AVAILABLE, because ageing a negative
    into a positive is the one way this mechanism could start lying.
    """
    clock = Clock("2026-09-13T10:00:00Z")
    provider, _ = botmux({
        ("GET", "/api/sessions"): {"sessions": SESSIONS},
        ("GET", "/api/sessions/s1/trigger-result"): LIVE_FAILURE,
    }, clock=clock)

    provider.result(provider_session_id="s1")
    fresh = provider.observe(device_ref=DEVICE)
    assert fresh["status"] == "UNAVAILABLE"
    assert fresh["reason"] == "EXECUTION_FAILED"
    assert fresh["freshness_window_seconds"] == 900

    clock.advance(20 * 60)
    stale = provider.observe(device_ref=DEVICE)
    assert stale["status"] == "UNKNOWN"
    assert stale["status_provenance"] == "UNKNOWN"
    # The observation is still reported — it happened, and hiding it would be
    # its own kind of lie. It is simply not counted.
    assert any(o.get("reason") == "EXECUTION_FAILED" for o in stale["observations"])
    assert "execution-readiness" in stale["stale_axes"]


def test_the_freshness_window_comes_from_the_contract_and_not_from_this_module():
    """One number, in the file that declares the record, asserted here."""
    import yaml
    contract = yaml.safe_load(
        (ABILITIES / "provider-contract.yaml").read_text(encoding="utf-8"))
    declared = contract["instance"]["readiness"]["freshness"]["window_seconds"]
    assert declared == instances.freshness_seconds()


def test_readiness_is_not_written_anywhere():
    """A stored readiness is a memory of a past resolution read as current."""
    import yaml
    contract = yaml.safe_load(
        (ABILITIES / "provider-contract.yaml").read_text(encoding="utf-8"))
    assert contract["instance"]["readiness"]["persistence"] == "none"
    provider, _ = botmux({("GET", "/api/sessions"): {"sessions": SESSIONS}})
    assert provider.observe(device_ref=DEVICE)["persistence"] == "none"


# ---------------------------------------------------------------------------
# §3: support SUPPORTED and readiness UNAVAILABLE, at the same time
# ---------------------------------------------------------------------------

def test_capability_and_readiness_disagree_and_both_are_true():
    """The finding, as an assertion.

    `session.send = SUPPORTED` is a statement about what BotMux can do.
    `readiness = UNAVAILABLE` is a statement about whether it works right now.
    Code that collapsed them would have to falsify one of them, and either
    falsification is expensive: withdraw the capability and the product cannot
    describe a machine that is temporarily broken; keep the capability and
    report it ready and the caller drives a broken provider.
    """
    provider, _ = botmux({
        ("GET", "/api/sessions"): {"sessions": [
            {"sessionId": "s1", "larkAppId": "b1", "cliId": "claude-code",
             "status": "dormant"},
        ]},
        ("GET", "/api/sessions/s1/trigger-result"): LIVE_FAILURE,
    })
    provider.result(provider_session_id="s1")

    row = provider.list_sessions(device_ref=DEVICE)[0]
    assert row["action_support"]["session.send"]["state"] == "SUPPORTED"
    assert row["action_support"]["session.create"] if "session.create" in \
        row["action_support"] else True
    assert provider.observe(device_ref=DEVICE)["status"] == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# §5: the three refusals stay three
# ---------------------------------------------------------------------------

class StubProvider:
    """Minimal provider with each axis under the test's control."""

    provider_id = "provider:stub"

    def __init__(self, *, send_support="SUPPORTED", observe_record=None):
        self._send_support = send_support
        self._observe_record = observe_record
        self.calls = []

    def list_agents(self, *, device_ref):
        return []

    def list_sessions(self, *, device_ref):
        ref = session_ref(device_ref, self.provider_id, "s1")
        return [{"session_ref": ref, "provider_session_id": "s1",
                 "action_support": {"session.send": support(self._send_support)}}]

    def send(self, **kwargs):
        self.calls.append("send")
        return {"sent": True}

    def create(self, **kwargs):
        self.calls.append("create")
        return {"created": True}

    def observe(self, *, device_ref):
        return self._observe_record


def _service(provider, *, decision="ALLOW"):
    return SessionService(
        device_ref=DEVICE, providers=[provider],
        admission=lambda action, context: {
            "action": action, "decision": decision, "class": "delegation",
            "reason": "test", "context": dict(context),
        },
    )


def _unknown_record() -> dict:
    return {"provider": "provider:stub", "device_ref": DEVICE, "status": "UNKNOWN",
            "reason": "EXECUTION_UNVERIFIED", "reason_text": "no surface",
            "observations": []}


def _unavailable_record() -> dict:
    return {"provider": "provider:stub", "device_ref": DEVICE,
            "status": "UNAVAILABLE", "reason": "EXECUTION_FAILED",
            "reason_text": "the provider accepted the action and it failed",
            "observations": []}


def test_unsupported_is_not_reported_as_not_ready():
    provider = StubProvider(send_support="UNSUPPORTED")
    with pytest.raises(ActionNotSupported):
        _service(provider).send(session_ref_value=session_ref(DEVICE, provider.provider_id, "s1"),
                                instruction="x", turn_idempotency_key="k")
    assert provider.calls == []


def test_not_ready_is_not_reported_as_unsupported():
    """A capability that exists and does not work must not be withdrawn.

    Reporting this as UNSUPPORTED would be a permanent claim about a temporary
    condition — and the first thing a reader would do is go and look for
    another provider, which is the wrong repair.
    """
    provider = StubProvider(observe_record=_unavailable_record())
    with pytest.raises(ProviderNotReady) as excinfo:
        _service(provider).send(session_ref_value=session_ref(DEVICE, provider.provider_id, "s1"),
                                instruction="x", turn_idempotency_key="k")
    assert excinfo.value.status == "UNAVAILABLE"
    assert excinfo.value.reason == "EXECUTION_FAILED"
    assert provider.calls == []


def test_not_ready_is_not_reported_as_a_policy_denial():
    """Nobody refused this. Nothing approved it either. It just does not work."""
    provider = StubProvider(observe_record=_unavailable_record())
    with pytest.raises(ProviderNotReady) as excinfo:
        _service(provider).send(session_ref_value=session_ref(DEVICE, provider.provider_id, "s1"),
                                instruction="x", turn_idempotency_key="k")
    assert not isinstance(excinfo.value, ActionNotAdmitted)
    assert not isinstance(excinfo.value, ActionNotSupported)


def test_unknown_readiness_does_not_block_a_call():
    """The property that keeps a provider with no readiness surface drivable.

    Every provider is UNKNOWN before it is called, because there is nothing to
    have observed yet. Refusing on UNKNOWN would make readiness a gate that
    nothing can pass on its first attempt, which is indistinguishable from
    refusing the provider entirely.
    """
    provider = StubProvider(observe_record=_unknown_record())
    out = _service(provider).send(
        session_ref_value=session_ref(DEVICE, provider.provider_id, "s1"),
        instruction="x", turn_idempotency_key="k")
    assert out["sent"] is True
    assert provider.calls == ["send"]
    assert out["provider_readiness"]["status"] == "UNKNOWN"


def test_a_provider_with_no_readiness_observer_reports_unknown_not_available():
    """An absent observer is an absent answer, not an absent problem.

    Most providers will not implement `observe`. The wrong default for them is
    AVAILABLE — it reads as "nothing to report" while actually meaning "nobody
    looked", which is the same substitution this whole round is about, one
    layer down.
    """
    class NoObserver:
        provider_id = "provider:stub"

        def list_agents(self, *, device_ref):
            return []

        def list_sessions(self, *, device_ref):
            return []

    record = _service(NoObserver()).readiness("provider:stub")
    assert record["status"] == "UNKNOWN"
    assert record["status_provenance"] == "UNKNOWN"
    assert record["reason"] == "EXECUTION_UNVERIFIED"
    assert "implements no observe()" in record["observations"][0]["evidence"]["why"]


def test_readiness_is_checked_before_admission_so_a_human_is_not_asked_to_approve_a_doomed_call():
    """Order, stated as an observable.

    Readiness can only narrow: it refuses calls admission would have allowed and
    can never allow one admission would refuse. That property is what makes it
    safe to put first, and putting it first is what stops a person being asked
    to approve something that cannot run.

    The admission callback here REFUSES. If readiness ran second, the error
    would be ActionNotAdmitted — the human would have been consulted. It is
    ProviderNotReady, so they were not.
    """
    seen = []

    def admit(action, context):
        seen.append(action)
        return {"action": action, "decision": "DENY", "class": "delegation",
                "reason": "test", "context": context}

    provider = StubProvider(observe_record=_unavailable_record())
    service = SessionService(device_ref=DEVICE, providers=[provider], admission=admit)
    with pytest.raises(ProviderNotReady):
        service.send(session_ref_value=session_ref(DEVICE, provider.provider_id, "s1"),
                     instruction="x", turn_idempotency_key="k")
    assert seen == [], "admission was consulted for a call that could not run"


def test_supported_still_needs_its_own_admission_when_readiness_is_available():
    """Readiness does not authorize anything, in either direction.

    A provider that reports AVAILABLE has said something about a machine. It
    has not said anything about permission.
    """
    provider = StubProvider(observe_record={
        "provider": "provider:stub", "device_ref": DEVICE, "status": "AVAILABLE",
        "reason": None, "observations": []})
    service = _service(provider, decision="DENY")
    with pytest.raises(ActionNotAdmitted):
        service.send(session_ref_value=session_ref(DEVICE, provider.provider_id, "s1"),
                     instruction="x", turn_idempotency_key="k")
    assert provider.calls == []


def test_a_successful_mutation_carries_the_readiness_it_was_made_under():
    provider = StubProvider(observe_record={
        "provider": "provider:stub", "device_ref": DEVICE, "status": "AVAILABLE",
        "status_provenance": "INFERRED", "reason": None,
        "observed_at": "2026-09-13T10:00:00Z", "observations": []})
    out = _service(provider).send(
        session_ref_value=session_ref(DEVICE, provider.provider_id, "s1"),
        instruction="x", turn_idempotency_key="k")
    assert out["provider_readiness"]["status"] == "AVAILABLE"
    assert out["provider_readiness"]["observed_at"] == "2026-09-13T10:00:00Z"
    assert out["admission"]["decision"] == "ALLOW"


# ---------------------------------------------------------------------------
# §3: evidence strength is health.py's, not a new one
# ---------------------------------------------------------------------------

def test_evidence_strength_is_reused_from_the_health_module():
    assert set(instances.health.EVIDENCE_STRENGTH) >= {
        "provider-response", "host-measurement", "journal-diagnostic", "none"}
    assert instances.health.EVIDENCE_STRENGTH["provider-response"] is True


def test_an_observation_without_provenance_is_refused_by_the_existing_constructor():
    """Not a new rule, enforced here. `runtime/health.py` already refuses it."""
    with pytest.raises(health.UnprovenancedObservation):
        health.Observation(axis="functional", state="OK", observed_by={},
                           observed_at="2026-09-13T10:00:00Z",
                           evidence={"type": "provider-response"})


def test_a_readiness_state_cannot_be_carried_by_an_unauthoritative_evidence_type():
    """A log line is not allowed to decide that a provider works."""
    with pytest.raises(health.HealthError):
        health.Observation(axis="functional", state="OK",
                           observed_by={"type": "provider-response", "id": "x"},
                           observed_at="2026-09-13T10:00:00Z",
                           evidence={"type": "journal-diagnostic"})


def test_the_readiness_reason_vocabulary_is_closed_by_the_provider_contract():
    import yaml
    contract = yaml.safe_load(
        (ABILITIES / "provider-contract.yaml").read_text(encoding="utf-8"))
    declared = set(contract["status"]["reasons"])
    assert set(instances.REASONS) == declared, (
        "two lists of status reasons is the defect this round found four times")
    assert set(contract["status"]["values"]) == set(instances.STATUSES)


def test_the_readiness_axes_are_the_managed_runtime_axes_and_not_new_ones():
    """One vocabulary for evidence, and the axes come from it.

    `liveness` and `functional` already mean "does it answer" and "can it do the
    thing it claims" — the second one's own comment is "Asked by calling, not by
    looking", which is this entire feature written down a plane earlier.
    """
    import yaml
    contract = yaml.safe_load(
        (RUNTIME / "managed-runtime-contract.yaml").read_text(encoding="utf-8"))
    axes = contract["health_axes"]
    assert instances.MANAGED_AXIS == {
        instances.AXIS_CONTROL_PLANE: "liveness",
        instances.AXIS_EXECUTION: "functional",
    }
    assert "OK" in axes["functional"]["values"]
    assert "FAILED" in axes["functional"]["values"]
    assert "RUNNING" in axes["liveness"]["values"]
    assert "STOPPED" in axes["liveness"]["values"]


def test_no_botmux_private_state_or_tmux_socket_is_read():
    """The permitted sources, asserted rather than promised.

    Reading `~/.botmux/data/*` or probing the tmux socket would make ServerOS a
    second truth source about a provider it does not own — and it would be a
    source that goes stale silently.
    """
    source = (MCP / "botmux_provider.py").read_text(encoding="utf-8")
    # Checked as ACCESS, not as a substring. The module's docstring says the
    # path is not read, and a test that forbade the string would forbid saying
    # so — which is how a rule ends up documented and unenforced at once.
    for forbidden in ("expanduser", "Path.home", "subprocess", "basename",
                      "list-sessions", "os.listdir", "glob("):
        assert forbidden not in source, f"{forbidden} in the provider adapter"
    # Every request goes through the one HTTP client, so the set of reachable
    # hosts is a single line rather than a property of the whole file.
    assert source.count("urlopen(") == 1
    assert "127.0.0.1:7891" in source


def test_no_readiness_endpoint_exists_so_the_pre_action_answer_is_unknown():
    """Recorded because it was probed, not assumed.

    Confirmed against BotMux 3.18.14 on this host: /api/health, /api/status,
    /api/ready, /api/readiness, /api/doctor, /api/version and /api/openapi.json
    all answer `404 {"error":"not_found_yet"}`. So there is no documented way to
    ask the question before acting, and the pre-action answer is UNKNOWN rather
    than a guess in either direction.
    """
    provider, _ = botmux({("GET", "/api/sessions"): {"sessions": SESSIONS}})
    record = provider.observe(device_ref=DEVICE)
    execution = next(o for o in record["observations"]
                     if o["evidence"].get("why"))
    assert "404" in execution["evidence"]["why"]
    assert execution["observed_by"] == {"type": "none", "id": "no-readiness-surface"}


# ---------------------------------------------------------------------------
# The instance record is a closed shape declared in one file. Every producer
# must satisfy it, and there must be exactly one producer.
# ---------------------------------------------------------------------------

def test_every_instance_record_carries_the_fields_the_contract_declares_required():
    """A second producer of a closed shape disagrees with the first eventually.

    `SessionService.readiness` used to assemble the no-observer record as a dict
    literal beside the request to `abilities/provider_instance.py`. It was
    already missing `observed_at` — the required field that says when any of it
    was true — and nothing failed, because nothing compared the two producers.

    Both branches are checked here rather than the shape being asserted once in
    the abstract: the defect was not in the derivation, which was right, but in
    a caller that did not use it.
    """
    import yaml

    contract = yaml.safe_load((ABILITIES / "provider-contract.yaml").read_text())
    required = set(contract["instance"]["required_fields"])

    class NoObserver:
        provider_id = "provider:stub"
        # No `observe` at all, which is the real shape: an adapter that has not
        # implemented the axis simply does not have the method.

    seen = {}
    # Branch one: a provider with an observer, through the adapter.
    provider, _ = botmux({("GET", "/api/sessions"): {"sessions": SESSIONS}})
    seen["adapter"] = provider.observe(device_ref=DEVICE)
    # Branch two: a provider with none, through the service.
    seen["service"] = _service(NoObserver()).readiness("provider:stub")

    for origin, record in seen.items():
        missing = required - set(record)
        assert not missing, f"the {origin}-produced instance is missing {sorted(missing)}"
        assert record["schema"] == instances.SCHEMA
        # `observed_at` is the one that had gone missing, so it is not merely
        # present but parseable: a placeholder like "" would satisfy the key set.
        datetime.datetime.fromisoformat(str(record["observed_at"]).replace("Z", "+00:00"))


def test_the_derivation_has_exactly_one_producer():
    """Named as a rule because two was the defect.

    Anywhere a dict with an `instance`-shaped set of keys is built by hand is a
    fork of the derivation, whether or not it disagrees today.
    """
    import ast

    # The discriminating keys are the ones only an INSTANCE record carries.
    # `provider`, `device_ref` and `status` are not enough to pick one out: a
    # session record has all three too, and `status` there means the session's
    # lifecycle rather than the provider's readiness. `status_provenance` beside
    # `observations` is the pair no other record in this repo has.
    instance_keys = {"status_provenance", "observations"}

    for path in (MCP / "agent_session_service.py", MCP / "botmux_provider.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        literals = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Dict)
            and instance_keys <= {
                k.value for k in node.keys if isinstance(k, ast.Constant)
                and isinstance(k.value, str)
            }
        ]
        assert not literals, (
            f"{path.name} builds an instance-shaped record as a dict literal; "
            "route it through abilities/provider_instance.py instead"
        )
