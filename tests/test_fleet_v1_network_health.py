"""N3 source-only network/service health and bounded recovery planning.

Hermetic by construction: this file loads synthetic fixtures from
``integrations/fleet-v1-network-health/fixtures/`` and never opens a socket,
spawns a process, reads an environment variable or touches a live service.
The Windows case is a mock host, not a Windows machine.

The property under test is not "does the model like this service". It is that
four different facts stay four different facts:

    listener bound          !=  transport carrying a request
    transport connected     !=  identity/account established
    identity established    !=  authenticated application readiness
    unit active             !=  service able to serve

A model that collapses any two of those is wrong in the direction that matters:
it reports reassurance for a service that cannot serve.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "integrations" / "fleet-v1-network-health"
FIXTURES = PACKAGE / "fixtures"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


nh = _load("serveros_fleet_v1_network_health", PACKAGE / "network_health.py")

NOW = nh.parse_timestamp("2026-10-18T00:00:00Z")

FIXTURE_NAMES = (
    "tailscale",
    "cloudcli_fleet_node",
    "linux_ssh_tunnel",
    "windows_16095_selkies",
    "selkies_stream_local",
    "selkies_stream_remote",
    "rdc",
)


def load_fixture(name: str):
    payload = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    profile = nh.profile_from_mapping(payload["profile"])
    readings = tuple(nh.observation_from_mapping(row) for row in payload["readings"])
    return profile, readings


def context_for(profile, **overrides):
    grants = set(profile.required_grants)
    if profile.recovery_grant:
        grants.add(profile.recovery_grant)
    values = dict(
        now=NOW,
        requester_device_ref=profile.owner_device_ref,
        requester_principal_ref=profile.owner_principal_ref,
        grant_refs=frozenset(grants),
        manual_stopped=False,
        lease_available=True,
        masked=False,
        install_state="installed",
        link_state="up",
        agent_state="idle",
        attempts_used=0,
        external_grant_ref=None,
    )
    values.update(overrides)
    return nh.EvaluationContext(**values)


def axis_reading(readings, axis):
    matches = [reading for reading in readings if reading.axis == axis]
    assert len(matches) == 1, (axis, len(matches))
    return matches[0]


def replace_axis(readings, axis, state, **changes):
    changed = []
    for reading in readings:
        if reading.axis != axis:
            changed.append(reading)
            continue
        if state == "UNKNOWN":
            changed.append(
                dataclasses.replace(
                    reading,
                    state="UNKNOWN",
                    observed_by={"type": "none", "id": "not-measured"},
                    evidence={"type": "none", "why": "NOT_OBSERVED"},
                    observed_at=None,
                    detail_code="NOT_OBSERVED",
                    **changes,
                )
            )
        else:
            changed.append(dataclasses.replace(reading, state=state, **changes))
    return tuple(changed)


# ---------------------------------------------------------------------------
# 1. six distinct profiles, and the Windows endpoint is not invented
# ---------------------------------------------------------------------------


def test_profiles_are_distinct_and_keep_owner_domain_explicit():
    profiles = {name: load_fixture(name)[0] for name in FIXTURE_NAMES}
    kinds = {p.service_kind for p in profiles.values()}
    assert kinds == {
        "tailscale",
        "cloudcli_fleet_node",
        "linux_ssh_tunnel",
        "windows_loopback_selkies",
        "selkies_stream",
        "rdc",
    }
    assert len({p.profile_id for p in profiles.values()}) == len(profiles)
    # Recovery authority is a property of the profile, not a guess made by a
    # caller: local nodes, remote owners and human-only surfaces differ.
    assert profiles["tailscale"].recovery_domain == "local_device"
    assert profiles["linux_ssh_tunnel"].recovery_domain == "local_device"
    assert profiles["selkies_stream_remote"].recovery_domain == "remote_device"
    assert profiles["rdc"].recovery_domain == "human_only"


def test_windows_16095_is_a_user_stated_local_listener_not_a_service_claim():
    profile, readings = load_fixture("windows_16095_selkies")
    assert profile.platform == "windows"
    assert profile.identity.listener_address == "127.0.0.1"
    assert profile.identity.listener_port == 16095
    assert profile.identity.declared_by == "user_stated"
    # No service name, PID, tunnel id or remote address is inferred.
    assert profile.identity.service_name is None
    assert profile.identity.pid is None
    assert profile.identity.tunnel_id is None
    assert profile.identity.remote_device_ref is None
    assert profile.identity.identity_ref is not None  # local Windows identity

    assessment = nh.assess(profile, readings, context_for(profile))
    # Listener + SSH transport + local identity are measured; authenticated
    # application readiness is not. That is LOCAL_ONLY, never HEALTHY.
    assert assessment.verdict == "LOCAL_ONLY"
    assert assessment.axes["listener"].state == "OK"
    assert assessment.axes["transport"].state == "OK"
    assert assessment.axes["functional"].state == "UNKNOWN"

    plan = nh.plan_recovery(assessment, context_for(profile))
    assert plan.decision == "NO_ACTION"
    assert plan.executable is False
    assert "REMOTE" not in (plan.action or "")


def test_pid_and_service_name_are_diagnostic_not_identity():
    profile, readings = load_fixture("tailscale")
    baseline = nh.assess(profile, readings, context_for(profile))
    renamed = dataclasses.replace(
        profile,
        identity=dataclasses.replace(
            profile.identity, service_name="a-different-name", pid=999999
        ),
    )
    changed = nh.assess(renamed, readings, context_for(renamed))
    assert changed.verdict == baseline.verdict
    assert changed.verdict_reason == baseline.verdict_reason
    assert changed.axes["identity"].state == baseline.axes["identity"].state


def test_identity_must_be_declared_not_invented():
    payload = json.loads((FIXTURES / "linux_ssh_tunnel.json").read_text(encoding="utf-8"))
    payload["profile"]["identity"]["remote_device_ref"] = None
    profile = nh.profile_from_mapping(payload["profile"])
    readings = tuple(nh.observation_from_mapping(row) for row in payload["readings"])
    assessment = nh.assess(profile, readings, context_for(profile))
    assert assessment.verdict == "IDENTITY_UNSPECIFIED"
    assert "remote_device_ref" in assessment.verdict_reason
    plan = nh.plan_recovery(assessment, context_for(profile))
    assert plan.decision == "REFUSED"
    assert plan.refusal_code == "IDENTITY_UNSPECIFIED"


# ---------------------------------------------------------------------------
# 2. happy path is synthetic and evidence-provenanced
# ---------------------------------------------------------------------------


def test_all_synthetic_profiles_keep_four_axes_separate():
    for name in FIXTURE_NAMES:
        profile, readings = load_fixture(name)
        assessment = nh.assess(profile, readings, context_for(profile))
        assert set(assessment.axes) == set(nh.AXES), name
        assert assessment.verdict in {"HEALTHY", "LOCAL_ONLY"}, name
        for axis in profile.required_axes:
            assert assessment.axes[axis].state == "OK", (name, axis)
            assert assessment.axes[axis].authoritative is True, (name, axis)
        # Every non-Windows profile claims real authenticated app health.
        if profile.platform != "windows" or profile.service_kind == "rdc":
            assert assessment.verdict == "HEALTHY", name


def test_listener_and_transport_alone_cannot_be_authenticated_health():
    profile, readings = load_fixture("cloudcli_fleet_node")
    listener_ok = replace_axis(readings, "functional", "UNKNOWN")
    assessment = nh.assess(profile, listener_ok, context_for(profile))
    assert assessment.axes["listener"].state == "OK"
    assert assessment.axes["transport"].state == "OK"
    assert assessment.verdict == "UNKNOWN"
    assert assessment.verdict_reason == "REQUIRED_AXIS_UNKNOWN"

    functional_down = replace_axis(readings, "functional", "FAIL")
    assessment = nh.assess(profile, functional_down, context_for(profile))
    assert assessment.verdict == "UNHEALTHY"
    assert assessment.verdict_reason == "REQUIRED_AXIS_FAILED"


def test_journal_line_cannot_carry_a_state():
    _, readings = load_fixture("cloudcli_fleet_node")
    okay = readings[0]
    with pytest.raises(nh.InvalidObservation):
        nh.observation_from_mapping(
            {
                "axis": okay.axis,
                "state": okay.state,
                "observed_by": dict(okay.observed_by),
                "observed_at": okay.observed_at,
                "evidence": {"type": "journal-diagnostic", "pattern": "looks-fine"},
                "device_ref": okay.device_ref,
                "principal_ref": okay.principal_ref,
                "max_age_s": okay.max_age_s,
                "detail_code": okay.detail_code,
            }
        )


# ---------------------------------------------------------------------------
# 3. every fail-closed condition named by the issue
# ---------------------------------------------------------------------------


def test_needs_login_is_human_never_restart():
    profile, readings = load_fixture("cloudcli_fleet_node")
    assessment = nh.assess(profile, replace_axis(readings, "identity", "NEEDS_LOGIN"), context_for(profile))
    assert assessment.verdict == "NEEDS_LOGIN"
    plan = nh.plan_recovery(assessment, context_for(profile))
    assert plan.decision == "NEEDS_HUMAN"
    assert plan.action == "REQUEST_LOGIN"
    assert plan.executable is False
    assert plan.refusal_code == "NEEDS_LOGIN"


def test_manual_stopped_is_refused_before_any_restart():
    profile, readings = load_fixture("tailscale")
    stopped = context_for(profile, manual_stopped=True)
    assessment = nh.assess(profile, readings, stopped)
    assert assessment.verdict == "MANUAL_STOPPED"
    plan = nh.plan_recovery(assessment, stopped)
    assert plan.decision == "REFUSED"
    assert plan.refusal_code == "MANUAL_STOPPED"
    assert plan.action in (None, "NONE")


def test_wrong_os_user_foreign_device_and_wrong_requester_fail_closed():
    profile, readings = load_fixture("cloudcli_fleet_node")

    wrong_principal = replace_axis(readings, "functional", "OK", principal_ref="someone-else")
    assessment = nh.assess(profile, wrong_principal, context_for(profile))
    assert assessment.verdict == "WRONG_OS_USER"

    foreign = replace_axis(readings, "listener", "OK", device_ref="device:another-node")
    assessment = nh.assess(profile, foreign, context_for(profile))
    assert assessment.verdict == "FOREIGN_DEVICE_SERVICE"

    assessment = nh.assess(
        profile, readings, context_for(profile, requester_principal_ref="someone-else")
    )
    assert assessment.verdict == "WRONG_OS_USER"

    assessment = nh.assess(
        profile, readings, context_for(profile, requester_device_ref="device:another-node")
    )
    assert assessment.verdict == "WRONG_DEVICE"


def test_missing_grant_fails_closed():
    profile, readings = load_fixture("cloudcli_fleet_node")
    no_grant = context_for(profile, grant_refs=frozenset())
    assessment = nh.assess(profile, readings, no_grant)
    assert assessment.verdict == "MISSING_GRANT"
    plan = nh.plan_recovery(assessment, no_grant)
    assert plan.decision == "REFUSED"
    assert plan.refusal_code == "MISSING_GRANT"


def test_stale_future_and_ambiguous_readings_fail_closed():
    profile, readings = load_fixture("cloudcli_fleet_node")

    stale = replace_axis(readings, "functional", "OK", observed_at="2026-10-17T20:00:00Z")
    assessment = nh.assess(profile, stale, context_for(profile))
    assert assessment.verdict == "STALE"

    future = replace_axis(readings, "functional", "OK", observed_at="2026-10-18T06:00:00Z")
    assessment = nh.assess(profile, future, context_for(profile))
    assert assessment.verdict == "STALE"
    assert "FUTURE" in assessment.verdict_reason

    conflict = readings + (
        dataclasses.replace(axis_reading(readings, "functional"), state="FAIL"),
    )
    assessment = nh.assess(profile, conflict, context_for(profile))
    assert assessment.verdict == "AMBIGUOUS"

    # A non-authoritative duplicate is not evidence at all.
    unauthoritative = readings + (
        dataclasses.replace(
            axis_reading(readings, "functional"),
            state="UNKNOWN",
            evidence={"type": "none", "why": "NOT_OBSERVED"},
            observed_by={"type": "none", "id": "not-measured"},
            observed_at=None,
            detail_code="NOT_OBSERVED",
        ),
    )
    assessment = nh.assess(profile, unauthoritative, context_for(profile))
    assert assessment.verdict == "HEALTHY"


def test_remote_healthy_local_tunnel_down_never_recovers_the_remote():
    profile, readings = load_fixture("linux_ssh_tunnel")
    local_down = replace_axis(readings, "transport", "FAIL")
    assessment = nh.assess(profile, local_down, context_for(profile))
    assert assessment.verdict == "REMOTE_OK_LOCAL_DOWN"
    assert assessment.axes["functional"].state == "OK"

    plan = nh.plan_recovery(assessment, context_for(profile))
    assert plan.decision == "PLAN_ONLY"
    assert plan.action == "REESTABLISH_LOCAL_TUNNEL"
    assert plan.action_domain == "local_device"
    assert plan.requires_external_grant is False
    assert plan.executable is False
    assert plan.execution_denied_reason == "SOURCE_ONLY_MILESTONE"


def test_local_tunnel_healthy_remote_app_down_requires_the_remote_owner():
    profile, readings = load_fixture("linux_ssh_tunnel")
    remote_down = replace_axis(readings, "functional", "FAIL")
    assessment = nh.assess(profile, remote_down, context_for(profile))
    assert assessment.verdict == "UNHEALTHY"
    assert assessment.axes["transport"].state == "OK"

    without = nh.plan_recovery(assessment, context_for(profile))
    assert without.decision == "REFUSED"
    assert without.refusal_code == "EXTERNAL_PERMISSION_REQUIRED"

    with_grant = nh.plan_recovery(
        assessment, context_for(profile, external_grant_ref="grant:remote-owner-fixture")
    )
    assert with_grant.decision == "PLAN_ONLY"
    assert with_grant.action == "REQUEST_REMOTE_OWNER_RECOVERY"
    assert with_grant.action_domain == "remote_device"


def test_windows_local_only_remote_app_failure_is_human_not_a_local_restart():
    profile, readings = load_fixture("windows_16095_selkies")
    broken = axis_reading(readings, "functional")
    broken = dataclasses.replace(
        broken,
        state="FAIL",
        observed_by={"type": "provider-response", "id": "mock-remote-app"},
        observed_at="2026-10-17T23:59:50Z",
        evidence={"type": "provider-response"},
        detail_code="AUTHENTICATED_FAILED",
    )
    assessment = nh.assess(profile, readings + (broken,), context_for(profile))
    # The required local axes still pass, so the profile cannot claim more
    # than LOCAL_ONLY; but a known authenticated failure must not be ignored.
    assert assessment.verdict == "LOCAL_ONLY"
    plan = nh.plan_recovery(assessment, context_for(profile))
    assert plan.decision == "NEEDS_HUMAN"
    assert plan.refusal_code == "REMOTE_APP_UNHEALTHY_UNOWNED"
    assert plan.action is None
    assert plan.executable is False


def test_remote_service_failure_needs_the_remote_owner_and_external_grant():
    profile, readings = load_fixture("selkies_stream_remote")
    remote_down = replace_axis(readings, "functional", "FAIL")
    assessment = nh.assess(profile, remote_down, context_for(profile))
    assert assessment.verdict == "UNHEALTHY"

    without = nh.plan_recovery(assessment, context_for(profile))
    assert without.decision == "REFUSED"
    assert without.refusal_code == "EXTERNAL_PERMISSION_REQUIRED"

    with_grant = nh.plan_recovery(
        assessment, context_for(profile, external_grant_ref="grant:remote-owner-fixture")
    )
    assert with_grant.decision == "PLAN_ONLY"
    assert with_grant.action == "REQUEST_REMOTE_OWNER_RECOVERY"
    assert with_grant.action_domain == "remote_device"
    assert with_grant.executable is False


def test_rdc_is_human_only_even_with_every_grant():
    profile, readings = load_fixture("rdc")
    down = replace_axis(readings, "functional", "FAIL")
    assessment = nh.assess(profile, down, context_for(profile))
    assert assessment.verdict == "UNHEALTHY"
    plan = nh.plan_recovery(
        assessment,
        context_for(profile, external_grant_ref="grant:remote-owner-fixture"),
    )
    assert plan.decision == "NEEDS_HUMAN"
    assert plan.refusal_code == "HUMAN_ONLY"
    assert plan.action is None
    assert plan.executable is False


# ---------------------------------------------------------------------------
# 4. recovery is a pure decision with admission gates and a budget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"lease_available": False}, "NO_LEASE"),
        ({"masked": True}, "MASKED"),
        ({"install_state": "unknown"}, "INSTALL_STATE_UNKNOWN"),
        ({"install_state": "not_installed"}, "NOT_INSTALLED"),
        ({"link_state": "unknown"}, "LINK_STATE_UNKNOWN"),
        ({"link_state": "down"}, "LINK_DOWN"),
        ({"agent_state": "unknown"}, "AGENT_STATE_UNKNOWN"),
        ({"agent_state": "busy"}, "AGENT_BUSY"),
    ],
)
def test_admission_gates_refuse_with_the_n1_codes(overrides, code):
    profile, readings = load_fixture("cloudcli_fleet_node")
    down = replace_axis(readings, "functional", "FAIL")
    assessment = nh.assess(profile, down, context_for(profile, **overrides))
    plan = nh.plan_recovery(assessment, context_for(profile, **overrides))
    assert plan.decision == "REFUSED"
    assert plan.refusal_code == code
    assert plan.executable is False


def test_offline_recoverable_is_per_service_and_defaults_closed():
    # The crashed local tunnel may be restarted even while the link is down.
    tunnel, readings = load_fixture("linux_ssh_tunnel")
    down = replace_axis(readings, "transport", "FAIL")
    assessment = nh.assess(tunnel, down, context_for(tunnel, link_state="down"))
    plan = nh.plan_recovery(assessment, context_for(tunnel, link_state="down"))
    assert plan.decision == "PLAN_ONLY"
    assert plan.action == "REESTABLISH_LOCAL_TUNNEL"

    # A link-dependent connector is not restarted offline.
    connector, connector_readings = load_fixture("cloudcli_fleet_node")
    assert connector.offline_recoverable is False
    assessment = nh.assess(
        connector,
        replace_axis(connector_readings, "functional", "FAIL"),
        context_for(connector, link_state="down"),
    )
    plan = nh.plan_recovery(assessment, context_for(connector, link_state="down"))
    assert plan.refusal_code == "LINK_DOWN"


def test_retry_budget_is_bounded_and_backoff_is_capped():
    profile, readings = load_fixture("tailscale")
    down = replace_axis(readings, "functional", "FAIL")

    first = nh.plan_recovery(nh.assess(profile, down, context_for(profile)), context_for(profile))
    assert first.decision == "PLAN_ONLY"
    assert first.attempt == 1
    assert first.delay_s == profile.retry_budget.base_delay_s
    assert first.budget_remaining == profile.retry_budget.max_attempts - 1

    second = nh.plan_recovery(
        nh.assess(profile, down, context_for(profile, attempts_used=1)),
        context_for(profile, attempts_used=1),
    )
    assert second.delay_s == min(
        profile.retry_budget.base_delay_s * profile.retry_budget.factor,
        profile.retry_budget.max_delay_s,
    )

    exhausted = context_for(profile, attempts_used=profile.retry_budget.max_attempts)
    plan = nh.plan_recovery(nh.assess(profile, down, exhausted), exhausted)
    assert plan.decision == "REFUSED"
    assert plan.refusal_code == "RETRY_BUDGET_EXHAUSTED"
    assert plan.delay_s == 0.0


def test_plan_never_carries_an_executable_command_or_raw_arguments():
    profile, readings = load_fixture("tailscale")
    down = replace_axis(readings, "functional", "FAIL")
    plan = nh.plan_recovery(nh.assess(profile, down, context_for(profile)), context_for(profile))
    rendered = json.dumps(plan.to_dict(), sort_keys=True)
    assert plan.executable is False
    assert plan.execution_denied_reason == "SOURCE_ONLY_MILESTONE"
    for forbidden in ("argv", "cmdline", "subprocess", "env", "token", "password"):
        assert forbidden not in rendered.lower()
    # The module cannot probe anything: no socket/subprocess import surface.
    source = (PACKAGE / "network_health.py").read_text(encoding="utf-8")
    assert "import socket" not in source
    assert "import subprocess" not in source
    assert "urllib" not in source


# ---------------------------------------------------------------------------
# 5. raw process args, env, tokens and URL query are refused at the boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden_key",
    ["argv", "args", "cmdline", "env", "environ", "token", "password", "secret", "url", "query"],
)
def test_observation_boundary_refuses_raw_material(forbidden_key):
    profile, _ = load_fixture("windows_16095_selkies")
    row = {
        "axis": "listener",
        "state": "OK",
        "observed_by": {"type": "host-measurement", "id": "mock-windows-scm"},
        "observed_at": "2026-10-17T23:59:50Z",
        "evidence": {"type": "host-measurement"},
        "device_ref": profile.owner_device_ref,
        "principal_ref": profile.owner_principal_ref,
        "max_age_s": 120,
        "detail_code": "LISTENER_ACCEPTING",
        forbidden_key: "must-not-be-accepted",
    }
    with pytest.raises(nh.ForbiddenObservationField):
        nh.observation_from_mapping(row)


def test_windows_mock_host_is_offline_and_returns_only_declared_facts():
    """The Windows path is a mock: no socket, no SCM, no live host."""

    class WindowsMockHost:
        def __init__(self):
            self.calls = []

        def read(self, axis):
            self.calls.append(axis)
            facts = {
                "liveness": ("OK", "host-measurement", "mock-windows-task-scheduler"),
                "listener": ("OK", "host-measurement", "mock-windows-loopback"),
                "transport": ("OK", "host-measurement", "mock-windows-ssh-forward"),
                "identity": ("OK", "host-measurement", "mock-windows-principal"),
                "functional": ("UNKNOWN", "none", "not-measured"),
            }
            state, source, observer = facts[axis]
            return nh.observation_from_mapping(
                {
                    "axis": axis,
                    "state": state,
                    "observed_by": {"type": source, "id": observer}
                    if source != "none"
                    else {"type": "none", "id": "not-measured"},
                    "observed_at": "2026-10-17T23:59:50Z" if source != "none" else None,
                    "evidence": {"type": source}
                    if source != "none"
                    else {"type": "none", "why": "NOT_OBSERVED"},
                    "device_ref": "device:fixture-windows",
                    "principal_ref": "fixture-windows-user",
                    "max_age_s": 120,
                    "detail_code": "MOCK_MEASURED" if source != "none" else "NOT_OBSERVED",
                }
            )

    profile, _ = load_fixture("windows_16095_selkies")
    host = WindowsMockHost()
    readings = tuple(host.read(axis) for axis in ("liveness", "listener", "transport", "identity", "functional"))
    assessment = nh.assess(profile, readings, context_for(profile))
    assert host.calls == ["liveness", "listener", "transport", "identity", "functional"]
    assert assessment.verdict == "LOCAL_ONLY"
    assert assessment.axes["functional"].state == "UNKNOWN"
    assert assessment.profile.identity.listener_address == "127.0.0.1"
    assert assessment.profile.identity.listener_port == 16095
    # No remote address, no service name, no tunnel id appeared from the mock.
    rendered = json.dumps(assessment.to_dict(), sort_keys=True)
    assert "ssh://" not in rendered
    assert "remote_address" not in rendered
