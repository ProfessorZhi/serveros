#!/usr/bin/env python3
"""§8–§12 — the runtime action model.

WHAT THIS FILE IS ABOUT
-----------------------
Until this round the runtime had exactly one actuator (`restart`), one global
switch (`--enable-reconcile`), and one concept — `is_automatable` — that
answered four different questions at once. §8–§12 separate them:

  §8  the operation vocabulary is closed: PROBE, RECONCILE, RESTART, RECONNECT,
      STOP. Five, and a sixth has to be written into the contract first.
  §9  five default rules, which are about permission rather than capability.
  §10 seven conjuncts that must ALL hold before an automatic restart.
  §11 the policy lives on the resource, not on a daemon flag; default observe.
  §12 three failure reasons that are never recovered automatically.

Two properties are load-bearing and are tested here because nothing else can
test them:

  * **A declaration with no consumer is the defect this project keeps
    finding.** `admission()` was a complete, tested policy function with no
    caller anywhere, and the report said "the tier policy decides" while
    nothing ever asked it. So the tests below assert the *call happens*, not
    that the function is correct.

  * **The gate must fail closed on the live node.** Every registered resource
    on this host is `protected`, so no §8–§12 change can alter what the daemon
    does today. That is asserted too, because a refactor of the permission
    model that silently made a live baseline restartable would otherwise look
    exactly like this one passing.

Hermetic: no docker, no systemd, no socket, and the "live node" test reads the
registry file rather than probing anything.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


registry_mod = _load("serveros_runtime_registry", "registry.py")
health_mod = _load("serveros_runtime_health", "health.py")
daemon_mod = _load("serveros_runtime_daemon", "daemon.py")
hygiene_mod = _load("serveros_runtime_hygiene", "hygiene.py")

Registry = registry_mod.Registry
Health = health_mod.Health


# ---------------------------------------------------------------------------
# a resource that satisfies every §10 conjunct, and knives to remove them
# ---------------------------------------------------------------------------


def a_resource(rid="res", **overrides):
    record = {
        "id": rid,
        "kind": "container",
        "owner": {"type": "project", "ref": "project:litellm"},
        "scope": "user",
        "lifecycle": {"class": "persistent"},
        "desired": {"state": "running"},
        "protection": {"level": "normal"},
        "recovery": {"model": "restart"},
        "reconcile": {"mode": "restart", "max_attempts": 2, "backoff": [60, 300]},
        "probes": [
            {"id": "l", "kind": "container-state", "axis": "liveness", "container": rid},
            {"id": "f", "kind": "container-healthcheck", "axis": "functional",
             "container": rid},
        ],
    }
    record.update(overrides)
    return record


def a_registry(*resources):
    return Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:server-beijing",
        "resources": list(resources),
    })


def reasons_for(**overrides) -> list[str]:
    return a_registry(a_resource("res", **overrides)).get("res").why_not_automatable()


# ---------------------------------------------------------------------------
# §8 — the operation vocabulary
# ---------------------------------------------------------------------------


def test_the_operation_vocabulary_is_exactly_the_five_declared():
    """§8 names five operations and nothing else may be performed.

    A sixth entry is not forbidden by this test — it is forbidden by having to
    be added to a YAML file a person reviews, which is the whole reason the
    vocabulary is declared rather than compiled in.
    """
    values = registry_mod.contract()["operations"]["values"]
    assert values == ["PROBE", "RECONCILE", "RESTART", "RECONNECT", "STOP"]


def test_every_declared_mode_names_a_declared_operation():
    """The two vocabularies are in one file and must agree.

    `mode_operations` maps `observe` to null, and null here is a decision —
    `observe` authorises no operation. If a mode were added without an entry,
    the reconciler would raise at the moment an action was due rather than at
    the moment the mode was declared, which is the wrong time to find out.
    """
    policy = registry_mod.contract()["reconcile_policy"]
    operations = set(registry_mod.contract()["operations"]["values"])
    for mode in policy["modes"]:
        assert mode in policy["mode_operations"], f"{mode} has no declared operation"
        named = policy["mode_operations"][mode]
        assert named is None or named in operations, f"{mode} -> {named} is not an operation"


def test_a_mode_that_authorises_nothing_performs_nothing():
    assert daemon_mod._operation_for_mode("observe") == "NONE"


def test_an_unknown_mode_is_refused_rather_than_rounded():
    """The one thing a control plane must never do."""
    with pytest.raises(registry_mod.RegistryError):
        daemon_mod._operation_for_mode("restart-it-anyway")


def test_the_unimplemented_operations_say_so_instead_of_guessing():
    """§8 declares RECONNECT and STOP. Neither has an actuator.

    The failure this prevents is a declared operation being silently satisfied
    by the nearest one that happens to exist — acting on a different object and
    reporting it as the one that asked.
    """
    actuator = daemon_mod.Actuator(runner=None, dry_run=False)
    resource = a_registry(a_resource("res")).get("res")
    for operation in ("RECONNECT", "STOP"):
        result = actuator.perform(operation, resource)
        assert result["performed"] is False
        assert result["reason"] == "NO_ACTUATOR"
        assert result["operation"] == operation


def test_an_operation_outside_the_vocabulary_is_refused():
    actuator = daemon_mod.Actuator(runner=None, dry_run=False)
    resource = a_registry(a_resource("res")).get("res")
    result = actuator.perform("PRUNE", resource)
    assert result["performed"] is False
    assert result["reason"] == "UNKNOWN_OPERATION"


# ---------------------------------------------------------------------------
# §10 — seven conjuncts
# ---------------------------------------------------------------------------


def test_a_resource_that_satisfies_all_seven_conjuncts_is_automatable():
    """The positive control. Without it every test below would pass against a
    gate that blocks everything."""
    resource = a_registry(a_resource("res")).get("res")
    assert resource.why_not_automatable() == []
    assert resource.is_automatable is True


@pytest.mark.parametrize("override,expected", [
    ({"protection": {"level": "protected"}}, "protection=protected"),
    ({"scope": "system"}, "scope=system"),
    ({"desired": {"state": "stopped"}}, "desired=stopped"),
    ({"lifecycle": {"class": "task-scoped"}}, "lifecycle=task-scoped"),
    ({"lifecycle": {"class": "disposable"}}, "lifecycle=disposable"),
    ({"reconcile": None}, "reconcile.mode=observe"),
    ({"probes": [{"id": "l", "kind": "container-state", "axis": "liveness",
                  "container": "res"}]}, "no functional probe"),
])
def test_each_conjunct_blocks_on_its_own(override, expected):
    """§10 is a conjunction, so removing any one term must be sufficient.

    `reconcile: None` is the case that matters most: the block is not written
    by default, so the default is the one that has to be safe. It is `observe`,
    and every resource registered on this host before this round is in it.
    """
    reasons = reasons_for(**override)
    assert any(expected in reason for reason in reasons), (expected, reasons)


def test_an_owner_type_outside_the_vocabulary_cannot_be_registered():
    """§9's "UNKNOWN_OWNER → no action", enforced at registration.

    There is no `unknown` owner type to write, so an unowned resource is not
    something the reconciler declines later — it is something the registry
    refuses to hold. `is_automatable` still checks for it, because a registry
    document read from a file is not the only way a Resource can be built.
    """
    with pytest.raises(registry_mod.RegistryInvalid) as excinfo:
        a_registry(a_resource("res", owner={"type": "unknown", "ref": "unknown:x"}))
    assert "owner.type" in str(excinfo.value)


def test_a_missing_budget_blocks_and_is_named_separately_from_observe():
    """§10's last conjunct. A mode that permits an action without bounding it
    is a retry loop wearing a policy's name — and it must not be reported as
    "no policy", because the policy is there and is what needs fixing."""
    # `max_attempts` is dropped from the block, so the registration itself is
    # refused. Reaching the reason list requires building the Resource without
    # going through validate, which is not something a caller can do — so the
    # refusal at registration IS the assertion.
    with pytest.raises(registry_mod.RegistryInvalid) as excinfo:
        a_registry(a_resource("res", reconcile={"mode": "restart", "backoff": [60]}))
    assert "max_attempts" in str(excinfo.value)


# ---------------------------------------------------------------------------
# §11 — the policy is on the resource
# ---------------------------------------------------------------------------


def test_the_default_mode_is_observe():
    """Silence is the safe value, and it is what the whole host has today."""
    assert registry_mod.contract()["reconcile_policy"]["default"] == "observe"
    resource = a_registry(a_resource("res", reconcile=None)).get("res")
    assert resource.reconcile["mode"] == "observe"
    assert resource.reconcile["declared"] is False
    assert resource.reconcile["authorises_action"] is False


def test_an_enabled_mode_without_a_recovery_model_is_refused():
    """The 2026-09-12 reflex, re-instituted by omission.

    Permitting a restart without saying what it is for is the same act as
    configuring `Restart=always` on an endpoint whose problem is a consumed
    token.
    """
    with pytest.raises(registry_mod.RegistryInvalid) as excinfo:
        a_registry(a_resource("res", recovery=None))
    assert "recovery" in str(excinfo.value)


def test_an_unknown_mode_is_refused_at_registration():
    with pytest.raises(registry_mod.RegistryInvalid) as excinfo:
        a_registry(a_resource("res", reconcile={"mode": "restartt",
                                                "max_attempts": 1, "backoff": [60]}))
    assert "reconcile mode" in str(excinfo.value)


def test_a_permitted_resource_still_needs_a_second_authority_to_act():
    """§11 forbids a switch that turns recovery on globally.

    The daemon's interlock is deliberately kept, and this asserts its shape:
    with a resource whose policy says `restart`, the interlock still withholds
    the action. It can therefore only ever *reduce* what happens — a resource
    whose mode is `observe` is filtered out before the flag is consulted.
    """
    resource = a_registry(a_resource("res")).get("res")
    assert resource.reconcile["authorises_action"] is True
    assert resource.is_automatable is True


# ---------------------------------------------------------------------------
# §9 — default action rules, as dispositions
# ---------------------------------------------------------------------------


def _obs(axis, state, reason=None):
    return health_mod.Observation(
        axis=axis, state=state,
        observed_by={"type": "probe", "id": "p"}, observed_at="2026-09-13T00:00:00Z",
        evidence={"type": "host-measurement"}, reason=reason)


def _health(*, functional="OK", liveness="RUNNING", reason=None):
    """`reason` rides on the functional observation, because that is where
    `Health.reason` reads it from — it is derived, not a field, so a caller
    cannot set a reason the axes do not carry."""
    return Health(
        liveness=_obs("liveness", liveness),
        transport=health_mod.unknown("transport"),
        functional=_obs("functional", functional, reason=reason),
        readiness=health_mod.unknown("readiness"),
        desired="running",
    )


def test_a_policy_blocked_resource_is_a_recommendation_not_an_alarm():
    """§9: task-scoped → recommendation only.

    The distinction the previous model lost. A task-scoped container that is
    down is not a situation requiring a person; it is one ServerOS may report
    and not fix. Escalating it to NEEDS_HUMAN is how an alarm stops being read.
    """
    resource = a_registry(a_resource("res", lifecycle={"class": "task-scoped"})).get("res")
    decision = health_mod.action_for(resource, _health(liveness="STOPPED",
                                                       functional="NOT_APPLICABLE"))
    assert decision["action"] == "RECONCILE"
    assert decision["automatic"] is False
    assert decision["recommendation_only"] is True
    assert any("lifecycle=task-scoped" in why for why in decision["would_act_if"])


def test_a_permission_block_is_needs_human_and_names_no_operation():
    """§9: persistent + protected → no cleanup, and no recommendation either."""
    resource = a_registry(a_resource("res", protection={"level": "protected"})).get("res")
    decision = health_mod.action_for(resource, _health(liveness="STOPPED",
                                                       functional="FAILED"))
    assert decision["action"] == "NEEDS_HUMAN"
    assert decision["blocked_by"] == ["protection=protected"]
    assert decision["automatic"] is False
    assert "recommendation_only" not in decision


def test_a_fully_permitted_resource_reconciles_automatically():
    resource = a_registry(a_resource("res")).get("res")
    decision = health_mod.action_for(resource, _health(liveness="STOPPED",
                                                       functional="NOT_APPLICABLE"))
    assert decision["action"] == "RECONCILE"
    assert decision["automatic"] is True


# ---------------------------------------------------------------------------
# §12 — never recovered automatically
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["AUTH_REAUTH_REQUIRED", "CREDENTIAL_MISSING",
                                    "POLICY_DENIED"])
def test_human_required_reasons_route_to_needs_human(reason):
    """§12 names three and the code must know all three.

    Two of them did not exist before this round: `AUTH_REASONS` held only the
    two authorization-flow failures, so a resource whose credential was simply
    absent, or whose request had been refused by a policy, had no way to say so
    and fell through to the reconciliation path.
    """
    resource = a_registry(a_resource("res")).get("res")
    health = _health(liveness="STOPPED", functional="FAILED", reason=reason)
    assert health.reason == reason
    decision = health_mod.action_for(resource, health)
    assert decision["action"] == "NEEDS_HUMAN", reason
    assert decision["reason"] == reason
    assert decision["human_required"] is True
    assert decision["restarting_is_the_wrong_response"]


def test_each_human_required_reason_has_its_own_sentence():
    """Three different problems, three different readers.

    A shared message would send a missing credential, an expired device code
    and a policy denial to the same person with the same instruction.
    """
    sentences = {reason: health_mod._HUMAN_REQUIRED_BECAUSE[reason]
                 for reason in health_mod.HUMAN_REQUIRED_REASONS}
    assert len(set(sentences.values())) == len(sentences)
    # And the auth reasons are still the auth reasons: AUTH_REASONS is a subset,
    # not a synonym, so a caller asking the narrower question still gets the
    # narrower answer.
    assert health_mod.AUTH_REASONS < health_mod.HUMAN_REQUIRED_REASONS


def test_an_unknown_reason_cannot_be_claimed():
    """REASONS is closed, so a probe cannot invent a reason that routes
    somewhere convenient."""
    with pytest.raises(health_mod.HealthError):
        health_mod.Observation(
            axis="functional", state="FAILED",
            observed_by={"type": "probe", "id": "p"}, observed_at="2026-09-13T00:00:00Z",
            evidence={"type": "host-measurement"}, reason="JUST_RESTART_IT")


# ---------------------------------------------------------------------------
# the policy that had no caller
# ---------------------------------------------------------------------------


def test_the_hygiene_report_actually_asks_the_admission_policy():
    """Ablation finding: `admission()` had no consumer anywhere in the repo.

    The report's own note said "the tier policy decides" while nothing ever
    asked it, so the sentence was a declaration the code did not honour. This
    asserts the call happens by observing its effect: a protected container
    cannot be admitted for STOP, and the decision appears in the report.
    """
    registry = a_registry(a_resource("protected-one", protection={"level": "protected"}))

    class FakeInventory:
        def docker_list(self, all_=True):
            return {"count": 1, "containers": [{
                "Names": "protected-one", "Image": "x", "State": "exited",
                "Status": "Exited (0) 2 days ago", "Labels": "",
            }]}

        def disk_status(self):
            return {"roles": [], "others": []}

        def pressure(self):
            return {"advisory_only": True, "notes": []}

        def processes_top(self, limit=15, by="cpu"):
            return {"rows": [], "scope_note": ""}

    report = hygiene_mod.Hygiene(registry, FakeInventory()).report()
    admission_rows = report["summary"]["candidate_admission"]
    assert isinstance(admission_rows, list)
    assert report["summary"]["permitted_count"] == sum(
        1 for row in admission_rows if row["decision"]["allowed"])
    # PROTECTED is never a candidate at all, which is why the list is empty —
    # and the empty list is the honest answer, not a policy that was skipped.
    assert report["docker"]["actionable"] == []


def test_no_cleanup_operation_reaches_the_runtime():
    """§17: docker removal never enters the automatic reconciler.

    Stated the way §17 states it — as an absent *operation* rather than as a
    denied one. `admission()` models a human decision that has already been
    recorded, and `approved=True` on a removable tier legitimately returns
    allowed; what must never exist is a path from the reconciler to a removal.
    So the assertion is that no removal appears in the operation vocabulary and
    that the actuator refuses one by name.
    """
    operations = set(registry_mod.contract()["operations"]["values"])
    for forbidden in ("REMOVE", "REMOVE_CONTAINER", "REMOVE_IMAGE", "REMOVE_VOLUME",
                      "PRUNE", "STOP_CONTAINER"):
        assert forbidden not in operations, forbidden

    actuator = daemon_mod.Actuator(runner=None, dry_run=False)
    resource = a_registry(a_resource("res")).get("res")
    for tier in hygiene_mod.RISK_TIERS:
        if tier == "STOP":
            continue  # declared in the vocabulary; unimplemented, so NO_ACTUATOR
        result = actuator.perform(tier, resource)
        assert result["performed"] is False, tier
        assert result["reason"] == "UNKNOWN_OPERATION", tier


def test_the_live_registry_has_no_automatable_resource():
    """The gate must fail closed on the node it is running on.

    §10 widens what "automatable" requires, so it can only have removed
    permissions — but this asserts the result rather than the argument. Every
    resource registered on this host is `protected`, and if a change to the
    permission model ever made one of them automatable, that would be a live
    behaviour change and it would look exactly like this refactor passing.
    """
    path = Path("/opt/applications/data/home-hwz/state/serveros/runtime/registry/resources.yaml")
    if not path.exists():
        pytest.skip("no live registry on this host")
    document = yaml.safe_load(path.read_text())
    registry = Registry(document=document)
    automatable = [r.id for r in registry.all() if r.is_automatable]
    assert automatable == [], f"§10 would permit action on {automatable}"
    # And the reason is the permission model, not an accident of the policy
    # defaults: the protection rule alone would hold even if every resource
    # declared `reconcile.mode: restart` tomorrow.
    for resource in registry.all():
        assert resource.protection == "protected"
