#!/usr/bin/env python3
"""Tests for evidence-based container attribution.

Hermetic. No Docker daemon: `attribute()` is a pure function of an inspect
record and a registry index, so every case here is a dict.

Four of these are regression tests for defects found by running this module
against the live host's 29 containers, and each one names the incident:

  * `litellm-container0-litellm` — the prod-parity baseline, registered
    `protection: protected` — reported `disposition: NORMAL`, because the code
    read only the registry's ref-level protection list and ignored the
    protection on the resource that actually matched it. The one container on
    this host that must never be reclaimed was the one it got wrong.

  * `lit-0103-r3-litellm` and `lit-0101-*` reported NORMAL for the same
    reason in a different place: their working directory is the shared
    `litellm-e2e`, which names no work item, and the only thing connecting
    them to a protected or deferred lane is the compose project name.

  * `xpra-gui-hwz` was reported as owned by `path:/home/hwz` at medium
    confidence, because it bind-mounts the whole home directory and
    `/home/hwz` IS the `home` root. A container that mounts a directory
    wholesale has told us nothing about whose work it is.

  * `openclaw` containers were given a low-confidence owner taken from the
    compose project name, which this module's own contract lists as never
    sufficient alone. A plausible name in the owner field is a plausible name
    in the owner field whatever its confidence says.

The secret test is structural rather than illustrative: `.Config.Env` is
present in the fixtures with real-looking credential values, and the test
asserts those values appear NOWHERE in the attribution output.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

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


cont = _load("serveros_runtime_containers", "containers.py")
# This node's roots, so that a test about "this user's home" is about the
# home of the machine it runs on. `ROOTS` below stays Beijing's: it is
# fixture DATA handed to `attribute(roots=...)`, and a fixture is allowed
# to describe any node. What must not be a fixture is the answer to "which
# node am I".
paths_mod = _load("serveros_runtime_paths", "paths.py")

ROOTS = {
    "home": "/home/hwz",
    "data": "/opt/applications/data/home-hwz",
    "workspace": "/opt/applications/data/home-hwz/workspace",
    "state": "/opt/applications/data/home-hwz/state/serveros",
}

# A credential value that must never reach an output. Deliberately shaped like
# a real key so that a future "redact things that look secret" pass cannot
# make this test pass by accident — the only correct reason for it to pass is
# that the value was never read.
SECRET_VALUE = "sk-do-not-let-this-appear-anywhere-0123456789"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def inspect_record(name, *, labels=None, env=None, mounts=None, image="img:1",
                   status="running", health=None, exit_code=0, ports=None,
                   networks=None):
    """One `docker inspect` record, in the shape Docker really returns."""
    state = {"Status": status, "StartedAt": "2026-09-11T00:00:00Z",
             "FinishedAt": "0001-01-01T00:00:00Z", "ExitCode": exit_code}
    if health:
        state["Health"] = {"Status": health}
    return {
        "Id": "c" * 64,
        "Name": "/" + name,
        "Created": "2026-09-11T00:00:00Z",
        "Image": "sha256:" + "b" * 64,
        "Config": {"Image": image, "Labels": labels or {}, "Env": env or []},
        "State": state,
        "RestartCount": 0,
        "Mounts": mounts or [],
        "NetworkSettings": {"Ports": ports or {}, "Networks": networks or {}},
    }


def compose_labels(project, *, working_dir=None, service="app"):
    labels = {
        "com.docker.compose.project": project,
        "com.docker.compose.service": service,
    }
    if working_dir:
        labels["com.docker.compose.project.working_dir"] = working_dir
    return labels


class FakeDockerHost:
    """Serves inspect records by name. An unknown name raises, as Docker does."""

    def __init__(self, records):
        self.records = records

    def docker_ps(self, all_=True):
        return [{"Names": n} for n in self.records]

    def docker_inspect(self, name):
        if name not in self.records:
            raise RuntimeError(f"No such container: {name}")
        return self.records[name]


def registry_doc():
    """The registry shape this host actually has, with its five resources."""
    return {
        "resources": [
            {"id": "litellm-baseline-litellm", "kind": "container",
             "owner": {"type": "project", "ref": "project:litellm"},
             "protection": {"level": "protected"},
             "probes": [{"id": "l", "container": "litellm-container0-litellm"}]},
            {"id": "litellm-baseline-postgres", "kind": "container",
             "owner": {"type": "project", "ref": "project:litellm"},
             "protection": {"level": "protected"},
             "probes": [{"id": "p", "container": "litellm-container0-postgres"}]},
            {"id": "lit0104-dev-litellm", "kind": "test-runtime",
             "owner": {"type": "work-item", "ref": "work-item:LIT-0104"},
             "protection": {"level": "protected"},
             "probes": [{"id": "l4", "container": "lit-0104-dev100-litellm"}]},
        ],
        "protected_refs": [
            {"ref": "work-item:LIT-0103", "because": "protected parallel lane"},
            {"ref": "work-item:LIT-0104", "because": "protected parallel lane"},
        ],
        "defer_refs": [
            {"ref": "work-item:LIT-0101", "because": "superseded"},
            {"ref": "project:fjd-req1-e2e",
             "because": '"fjd" + "req1" is a plausible reading, not an owner.'},
        ],
    }


@pytest.fixture
def registry():
    return {"registry_present": True,
            **cont.build_registry_index(registry_doc()),
            "owner_label_namespaces": {}}


# ---------------------------------------------------------------------------
# §30 · the secret boundary
# ---------------------------------------------------------------------------


def test_env_values_are_never_read(registry):
    """§30. The variable NAMES are read; the values are not, at any point.

    The values here look like real credentials so that the only way this test
    can pass is by the value never being read — not by a redaction pass
    noticing it later.
    """
    record = inspect_record(
        "app",
        labels=compose_labels("p", working_dir="/opt/applications/data/home-hwz/workspace/src/p"),
        env=[f"AWS_SECRET_ACCESS_KEY={SECRET_VALUE}",
             f"OPENAI_API_KEY={SECRET_VALUE}",
             "PLAIN_SETTING=harmless"],
    )
    result = cont.attribute(cont.evidence(record), registry=registry, roots=ROOTS)
    rendered = json.dumps(result)
    assert SECRET_VALUE not in rendered
    assert "OPENAI_API_KEY" in rendered        # the NAME is present ...
    assert "PLAIN_SETTING" in rendered         # ... and so is this one


def test_the_evidence_record_keeps_only_env_names(registry):
    ev = cont.evidence(inspect_record("app", env=[f"TOKEN={SECRET_VALUE}", "LEVEL=2"]))
    assert ev["env_var_names"] == ["LEVEL", "TOKEN"]
    assert "env" not in ev
    assert SECRET_VALUE not in json.dumps(ev)


def test_an_owner_namespace_label_is_read_by_key_and_never_by_value(registry):
    """The key namespace is the ownership signal; the value is configuration."""
    labels = {"com.multiuserclaw.hermes-runtime-config": SECRET_VALUE}
    result = cont.attribute(
        cont.evidence(inspect_record("hermes-1", labels=labels)),
        registry={**registry, "owner_label_namespaces": {"com.multiuserclaw": "project:multiuserclaw"}},
        roots=ROOTS,
    )
    assert result["owner"]["ref"] == "project:multiuserclaw"
    assert "not read" in result["attribution"]["decided_by"]
    assert SECRET_VALUE not in json.dumps(result)


def test_an_undeclared_namespace_leaves_the_container_unowned_but_names_the_gap(registry):
    """The live case: four hermes containers, one label, no declaration.

    Declaring the namespace is a human decision — it changes what may be
    reclaimed — so the code reports the one-line fix instead of taking it.
    """
    result = cont.attribute(
        cont.evidence(inspect_record(
            "hermes-1",
            labels={"com.multiuserclaw.hermes-runtime-config": SECRET_VALUE},
        )),
        registry=registry, roots=ROOTS,
    )
    assert result["unknown_owner"] is True
    assert result["disposition"] == "DEFER"
    assert any("com.multiuserclaw" in n for n in result["notes"])
    assert SECRET_VALUE not in json.dumps(result)


# ---------------------------------------------------------------------------
# Decisive evidence
# ---------------------------------------------------------------------------


def test_an_owner_label_decides_and_is_attributed_to_the_owner(registry):
    result = cont.attribute(
        cont.evidence(inspect_record("svc", labels={"serveros.owner": "work-item:LIT-0104"})),
        registry=registry, roots=ROOTS,
    )
    assert result["owner"]["ref"] == "work-item:LIT-0104"
    assert result["attribution"]["confidence"] == "high"
    assert "docker-label" in result["attribution"]["provenance"]
    assert result["disposition"] == "PROTECTED"     # LIT-0104 is a protected ref


def test_registry_match_by_container_name_is_decisive(registry):
    result = cont.attribute(
        cont.evidence(inspect_record("litellm-container0-litellm")),
        registry=registry, roots=ROOTS,
    )
    assert result["owner"]["ref"] == "project:litellm"
    assert "registry-match" in result["attribution"]["provenance"]


def test_regression_a_protected_resource_is_protected_by_its_own_registration(registry):
    """Regression, found on the live host.

    `litellm-container0-litellm` is registered as `litellm-baseline-litellm`
    with `protection: protected`, and its owner `project:litellm` is NOT in
    `protected_refs`. The first version read only the ref list, so the
    prod-parity baseline — the one container here that must never be
    reclaimed — reported `disposition: NORMAL`. The registry had said
    otherwise all along.
    """
    result = cont.attribute(
        cont.evidence(inspect_record("litellm-container0-litellm")),
        registry=registry, roots=ROOTS,
    )
    assert result["disposition"] == "PROTECTED"
    assert "litellm-baseline-litellm" in result["disposition_because"]


def test_persistence_is_protection_and_not_a_different_axis(registry):
    """A stopped container is still protected. Liveness never relaxes it."""
    result = cont.attribute(
        cont.evidence(inspect_record("litellm-container0-litellm",
                                     status="exited", exit_code=137)),
        registry=registry, roots=ROOTS,
    )
    assert result["state"] == "exited"
    assert result["disposition"] == "PROTECTED"
    assert result["reclaim_candidate"] is False


# ---------------------------------------------------------------------------
# The project-name normalisation, and the name it must NOT match
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("project,expected", [
    ("lit-0104-dev100", "LIT-0104"),
    ("lit-0103-r3", "LIT-0103"),
    ("lit-0101-after", "LIT-0101"),
    ("lit-0101", "LIT-0101"),
    # These five must NOT normalise. Each is on the live host.
    ("fjd-req1-e2e", None),        # `req1` is not all digits
    ("openclaw", None),            # no second part
    ("litellm-container0", None),  # `container0` is not all digits
    ("opensearch-demo", None),
    ("new-api", None),
])
def test_the_project_name_normalisation_is_narrow(project, expected):
    assert cont._work_item_from_token(project) == expected


def test_the_contracts_declared_normalisation_agrees_with_the_code():
    """A contract rule the code does not implement is the pathology this
    codebase keeps finding. This checks the two against each other on the
    live examples rather than describing them separately."""
    import re
    import yaml

    contract = yaml.safe_load((RUNTIME / "attribution-contract.yaml").read_text())
    pattern = contract["container_attribution"]["project_name_normalisation"]
    for project, expected in [("lit-0104-dev100", "LIT-0104"),
                              ("lit-0103-r3", "LIT-0103"),
                              ("fjd-req1-e2e", None),
                              ("openclaw", None)]:
        match = re.match(pattern, project)
        declared = None
        if match:
            declared = f"{match.group('prefix').upper()}-{match.group('number')}"
        assert declared == expected, f"{project}: contract says {declared}, code says {expected}"
        assert cont._work_item_from_token(project) == expected


def test_regression_a_protected_lane_is_reached_through_the_project_name(registry):
    """Regression, found on the live host.

    `lit-0103-r3-litellm` and the four `lit-0101-*` containers declare no work
    item in their path — their working directory is the shared `litellm-e2e`
    — so the compose project name is the only thing connecting them to a
    protected or deferred lane. Without this they reported NORMAL.
    """
    record = inspect_record(
        "lit-0103-r3-litellm",
        labels=compose_labels(
            "lit-0103-r3",
            working_dir="/opt/applications/data/home-hwz/workspace/test-envs/litellm-bridge/litellm-e2e",
        ),
    )
    result = cont.attribute(cont.evidence(record), registry=registry, roots=ROOTS)
    assert result["owner"]["ref"] == "work-item:LIT-0103"
    assert result["disposition"] == "PROTECTED"


def test_a_deferred_lane_is_reached_the_same_way(registry):
    record = inspect_record(
        "lit-0101-litellm",
        labels=compose_labels("lit-0101",
                              working_dir="/opt/applications/data/home-hwz/workspace/test-envs/litellm-bridge/litellm-e2e"),
    )
    result = cont.attribute(cont.evidence(record), registry=registry, roots=ROOTS)
    assert result["owner"]["ref"] == "work-item:LIT-0101"
    assert result["disposition"] == "DEFER"


def test_the_plausible_reading_is_refused_by_the_registry(registry):
    """`project:fjd-req1-e2e` IS registered — as a deferral, with the reason
    spelled out. The registry is what turns a plausible name into a decision."""
    record = inspect_record(
        "fjd-req1-e2e-litellm",
        labels=compose_labels("fjd-req1-e2e",
                              working_dir="/opt/applications/data/home-hwz/workspace/test-envs/litellm-bridge/litellm-e2e"),
    )
    result = cont.attribute(cont.evidence(record), registry=registry, roots=ROOTS)
    assert result["owner"]["ref"] == "project:fjd-req1-e2e"
    assert result["disposition"] == "DEFER"


def test_regression_a_project_name_alone_never_names_an_owner(registry):
    """Regression, found on the live host.

    `openclaw` has five containers, no registered owner, and a working
    directory outside every known root. They were given `project:openclaw` at
    `confidence: low` — and a plausible name in the owner field is a plausible
    name in the owner field whatever its confidence says. §6: if the evidence
    does not reach an owner, the answer is UNKNOWN_OWNER.
    """
    record = inspect_record(
        "openclaw-frontend",
        labels=compose_labels("openclaw",
                              working_dir="/opt/applications/data/home-ubuntu/Project/MultiUserClaw"),
    )
    result = cont.attribute(cont.evidence(record), registry=registry, roots=ROOTS)
    assert result["owner"]["ref"] is None
    assert result["unknown_owner"] is True
    assert result["disposition"] == "DEFER"
    # ... and the name is still recorded, as a locator rather than an owner.
    assert result["locator"]["value"] == "project:openclaw"
    assert result["locator"]["status"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# Path evidence
# ---------------------------------------------------------------------------


def test_regression_a_path_equal_to_a_root_is_not_evidence(registry):
    """Regression, found on the live host.

    `xpra-gui-hwz` bind-mounts the whole home directory, and `/home/hwz` IS
    `ROOTS['home']`. The first version accepted a path equal to a root and
    reported the container as owned by `path:/home/hwz` at medium confidence.
    """
    record = inspect_record("xpra-gui-hwz", mounts=[
        {"Type": "bind", "Source": "/", "Destination": "/host"},
        {"Type": "bind", "Source": "/home/hwz", "Destination": "/home/hwz"},
    ])
    result = cont.attribute(cont.evidence(record), registry=registry, roots=ROOTS)
    assert result["owner"]["ref"] is None
    assert result["unknown_owner"] is True
    assert result["disposition"] == "DEFER"
    # And no locator either — not just no owner. Asserting only the owner field
    # would let a root-equal path back in as a locator and this test would
    # still pass, which is how the assertion and the defect drift apart.
    assert result["locator"] is None
    assert "bind-mount" not in result["attribution"]["provenance"]
    # The mount is still reported, because it is a fact about the container.
    assert any("entire host filesystem" in n for n in result["notes"])


def test_the_most_specific_root_wins():
    """These roots nest. `/…/workspace/src/x` is under both `data` and
    `workspace`, and answering `data` discards the only part that says which
    area of the node it is — the same mistake as reporting `user@1006.service`
    as the supervisor of `dbus.service` instead of `dbus.service`."""
    record = inspect_record("nested", mounts=[
        {"Type": "bind", "Source": "/opt/applications/data/home-hwz/workspace/src/nested",
         "Destination": "/x"},
    ])
    result = cont.attribute(cont.evidence(record), registry={"registry_present": True},
                            roots=ROOTS)
    assert result["locator"]["root"] == "workspace"


def test_a_path_strictly_below_a_root_is_a_locator_not_an_owner(registry):
    record = inspect_record(
        "mystery-1",
        labels=compose_labels("mystery", working_dir="/opt/applications/data/home-hwz/workspace/src/mystery"),
    )
    result = cont.attribute(cont.evidence(record), registry=registry, roots=ROOTS)
    assert result["owner"]["ref"] is None
    assert result["unknown_owner"] is True
    assert result["locator"]["kind"] == "path"
    assert result["locator"]["value"] == "/opt/applications/data/home-hwz/workspace/src/mystery"
    assert result["locator"]["root"] == "workspace"
    assert result["locator"]["confidence"] == "medium"
    assert "working-directory" in result["attribution"]["provenance"]


def test_a_bind_mount_is_reported_as_bind_mount_not_as_a_working_directory(registry):
    record = inspect_record("mystery-2", mounts=[
        {"Type": "bind", "Source": "/opt/applications/data/home-hwz/artifacts/work-items/x",
         "Destination": "/data"},
    ])
    result = cont.attribute(cont.evidence(record), registry=registry, roots=ROOTS)
    assert "bind-mount" in result["attribution"]["provenance"]
    assert "working-directory" not in result["attribution"]["provenance"]


def test_host_exposure_is_reported_even_when_the_owner_is_known(registry):
    """A recommendation that did not carry this would be asking an operator
    to consent to a consequence nobody described."""
    record = inspect_record("litellm-container0-litellm", mounts=[
        {"Type": "bind", "Source": "/", "Destination": "/host"},
        {"Type": "bind", "Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock"},
    ])
    result = cont.attribute(cont.evidence(record), registry=registry, roots=ROOTS)
    assert result["disposition"] == "PROTECTED"
    joined = " ".join(result["notes"])
    assert "entire host filesystem" in joined
    assert "Docker control socket" in joined


# ---------------------------------------------------------------------------
# Evidence that disagrees with itself
# ---------------------------------------------------------------------------


def test_regression_a_compose_project_with_two_working_dirs_downgrades_confidence(registry):
    """Regression, found on the live host.

    The `openclaw` project's five containers declare two different working
    directories, one of which is a path that does not exist on this node. Each
    container read alone looks fine; read together one of them is wrong and
    nothing says which. The honest output is to say so.
    """
    records = {
        "openclaw-frontend": inspect_record("openclaw-frontend",
            labels=compose_labels("openclaw", working_dir="/opt/applications/data/home-ubuntu/Project/MultiUserClaw")),
        "openclaw-postgres": inspect_record("openclaw-postgres",
            labels=compose_labels("openclaw", working_dir="/home/ubuntu/Project/MultiUserClaw")),
    }
    report = cont.attribute_all(FakeDockerHost(records), registry=registry, roots=ROOTS)
    for item in report["containers"]:
        assert any("more than one working directory" in n for n in item["notes"]), item["container"]


def test_a_consistent_project_has_no_conflict_note(registry):
    records = {
        "a": inspect_record("a", labels=compose_labels("solo", working_dir="/opt/applications/data/home-hwz/workspace/src/solo")),
        "b": inspect_record("b", labels=compose_labels("solo", working_dir="/opt/applications/data/home-hwz/workspace/src/solo")),
    }
    report = cont.attribute_all(FakeDockerHost(records), registry=registry, roots=ROOTS)
    for item in report["containers"]:
        assert not any("more than one working directory" in n for n in item["notes"])


# ---------------------------------------------------------------------------
# The invariants that must hold for every container
# ---------------------------------------------------------------------------


def test_regression_nothing_on_this_host_is_ever_a_reclaim_candidate(registry):
    """§18. Attribution is not authorization, and the flag is a constant."""
    records = {
        "baseline": inspect_record("litellm-container0-litellm"),
        "unknown": inspect_record("xpra-gui-hwz", mounts=[{"Type": "bind", "Source": "/", "Destination": "/host"}]),
        "named": inspect_record("openclaw-frontend",
            labels=compose_labels("openclaw", working_dir="/opt/applications/data/home-ubuntu/Project/MultiUserClaw")),
    }
    report = cont.attribute_all(FakeDockerHost(records), registry=registry, roots=ROOTS)
    assert report["containers"]
    for item in report["containers"]:
        assert item["reclaim_candidate"] is False


def test_every_container_carries_an_owner_status_and_a_disposition(registry):
    records = {
        "a": inspect_record("a", labels={"serveros.owner": "work-item:LIT-0104"}),
        "b": inspect_record("b", labels=compose_labels("lit-0103-r3")),
        "c": inspect_record("c"),
    }
    report = cont.attribute_all(FakeDockerHost(records), registry=registry, roots=ROOTS)
    for item in report["containers"]:
        assert item["owner"]["status"] in ("FACT", "INFERRED", "UNKNOWN")
        assert item["disposition"] in ("PROTECTED", "DEFER", "NORMAL", "DISPOSABLE")


def test_an_unknown_owner_always_defers(registry):
    result = cont.attribute(cont.evidence(inspect_record("mystery")),
                            registry=registry, roots=ROOTS)
    assert result["unknown_owner"] is True
    assert result["disposition"] == "DEFER"
    assert "fails safe" in result["disposition_because"]


def test_an_absent_registry_makes_everything_defer_and_says_so():
    """A node with no registry has no registrations. That is a valid state and
    it is reported as such, so that "nothing is registered" is never read as
    "everything is unowned and therefore free"."""
    registry = cont.load_registry(None)
    assert registry["registry_present"] is False
    report = cont.attribute_all(
        FakeDockerHost({"a": inspect_record("a", labels=compose_labels("p"))}),
        registry=registry, roots=ROOTS,
    )
    assert report["registry_present"] is False
    assert all(c["disposition"] == "DEFER" for c in report["containers"])


def test_an_unreadable_registry_is_reported_as_absent_not_as_empty(tmp_path):
    registry = cont.load_registry(tmp_path / "does-not-exist.yaml")
    assert registry["registry_present"] is False


def test_a_containers_own_state_is_recorded_but_never_used_to_relax_protection(registry):
    for status in ("running", "exited", "created", "paused"):
        result = cont.attribute(
            cont.evidence(inspect_record("litellm-container0-litellm", status=status)),
            registry=registry, roots=ROOTS,
        )
        assert result["state"] == status
        assert result["disposition"] == "PROTECTED", status


def test_a_container_that_vanishes_mid_walk_does_not_abort_the_report(registry):
    """`docker ps` and `docker inspect` are two calls; a container can exit
    between them. One vanished container must not lose the other twenty-eight."""
    class RacingHost(FakeDockerHost):
        def docker_inspect(self, name):
            if name == "gone":
                raise RuntimeError("No such container")
            return super().docker_inspect(name)

    report = cont.attribute_all(
        RacingHost({"gone": object(), "here": inspect_record("here")}),
        registry=registry, roots=ROOTS,
    )
    assert report["count"] == 1
    assert report["containers"][0]["container"] == "here"


# ---------------------------------------------------------------------------
# §8 · another actor's resource is not an unknown one
# ---------------------------------------------------------------------------


def test_a_mount_under_another_users_home_is_positive_evidence_of_another_actor(registry):
    """The live case: `new-api` mounts /home/szj/gpt-img-2/data.

    The owner is still not known — and must not be — but "nobody can say who
    owns this" and "this is demonstrably not this node's" are different
    states, and collapsing them loses the distinction a cleanup pass needs.
    """
    record = inspect_record("new-api", mounts=[
        {"Type": "bind", "Source": "/home/szj/gpt-img-2/data", "Destination": "/data"},
    ])
    result = cont.attribute(cont.evidence(record), registry=registry, roots=ROOTS)

    assert result["foreign_actor"] is True
    assert result["foreign_actor_evidence"] == [
        {"path": "/home/szj/gpt-img-2/data", "from": "bind-mount"},
    ]
    # ... and the owner field is untouched by it.
    assert result["owner"]["ref"] is None
    assert result["unknown_owner"] is True
    assert result["disposition"] == "DEFER"


def test_foreign_evidence_does_not_change_the_unknown_owner_count(registry):
    """The count is deliberately not flattered by the new field.

    Reclassifying containers out of UNKNOWN_OWNER would make a report that
    knows exactly as much as before look like one that had learned something.
    Both containers here are unknown-owned; one of them also carries
    non-ownership evidence, and that is the only difference the output shows.
    """
    def unknown(name, **kw):
        return cont.attribute(cont.evidence(inspect_record(name, **kw)),
                              registry=registry, roots=ROOTS)

    foreign = unknown("new-api", mounts=[
        {"Type": "bind", "Source": "/home/szj/gpt-img-2/data", "Destination": "/data"}])
    plain = unknown("xpra-gui-hwz", mounts=[
        {"Type": "bind", "Source": "/", "Destination": "/host"}])

    assert foreign["unknown_owner"] is True
    assert plain["unknown_owner"] is True
    assert foreign["disposition"] == plain["disposition"] == "DEFER"
    assert (foreign["foreign_actor"], plain["foreign_actor"]) == (True, False)


def test_a_whole_serveros_root_mount_is_not_foreign_evidence(registry):
    """A container that mounts THIS user's home, wholesale, is not a stranger's.

    It is a locator and a host-exposure finding. It is not evidence that the
    container belongs to someone else, and a rule that read this node's own
    home as a foreign home would have said the node's own work is a stranger's.

    The mount source is read from the node's Profile rather than written as
    `/home/hwz`. That literal made the test assert a fact about Beijing — there
    the path is the user's home, here it belongs to nobody — so it passed for a
    reason that had nothing to do with the rule.
    """
    home = str(paths_mod.Paths().root("home"))
    result = cont.attribute(
        cont.evidence(inspect_record("xpra-gui-hwz", mounts=[
            {"Type": "bind", "Source": home, "Destination": "/host"}])),
        registry=registry, roots=ROOTS,
    )
    assert result["foreign_actor"] is False
    assert result["foreign_actor_evidence"] == []


def test_the_foreign_home_rule_does_not_invert_when_USER_is_unset(monkeypatch):
    """A regression test for a defect that shipped and was never caught.

    The rule read `os.environ['USER']`, which is empty under a systemd unit —
    the exact conditions the serverosd pilot runs in. With it empty, every
    `/home/<anything>` that is not the empty string compares unequal, so
    `/home/hwz` was called another user's directory: this node's own work
    classified as a stranger's, in the report an operator reads before
    deciding what is safe to touch.
    """
    import os as _os
    monkeypatch.delenv("USER", raising=False)
    attribution = _load("serveros_runtime_attribution", "attribution.py")

    assert _os.environ.get("USER") is None          # the condition is real

    # Both layouts the rule handles, each built from the identity this node
    # actually has, in the same run. The rule must say "mine" for both and
    # "theirs" for a stranger, with `USER` gone.
    me = attribution.current_user()
    home = str(paths_mod.Paths().root("home"))
    assert me, "no current user was established; the rest of this proves nothing"
    assert attribution.foreign_home(f"{home}/workspace") is False
    assert attribution.foreign_home(
        f"/opt/applications/data/home-{me}/workspace") is False
    assert attribution.foreign_home("/home/szj/gpt-img-2/data") is True


def test_an_undeterminable_current_user_makes_nothing_foreign(monkeypatch):
    """Fails toward False. Both readings end in DEFER, so the safe branch is
    also the honest one: no user, no claim that a directory is somebody
    else's."""
    attribution = _load("serveros_runtime_attribution", "attribution.py")
    assert attribution.foreign_home("/home/szj/x", user="") is False


# ---------------------------------------------------------------------------
# §9/§10 · the runtime identity is not the Docker name
# ---------------------------------------------------------------------------


def test_a_rename_does_not_change_the_runtime_ref(registry):
    """§10. The load-bearing test for this section.

    Before this, `ref` was built as `runtime:container:<name>`, so renaming a
    container silently produced a DIFFERENT resource. Every record that had
    stored the old ref — evidence, an anomaly subject, an operator's notes —
    pointed at something that no longer existed, and nothing said so.

    Here the same container is attributed twice under two names. The
    identity, the owner and the disposition must all be identical; only the
    display name may differ.
    """
    labels = compose_labels("litellm-baseline", working_dir="/srv/litellm")
    before = cont.attribute(cont.evidence(inspect_record("old-name", labels=labels)),
                            registry=registry, roots=ROOTS)
    after = cont.attribute(cont.evidence(inspect_record("renamed", labels=labels)),
                           registry=registry, roots=ROOTS)

    assert before["ref"] == after["ref"]
    assert before["identity"]["runtime_ref"] == after["identity"]["runtime_ref"]
    assert before["owner"] == after["owner"]
    assert before["disposition"] == after["disposition"]
    # And the name still moves, because it is the human-readable label and a
    # rename that changed nothing at all would mean the name was not read.
    assert before["container"] == "old-name"
    assert after["container"] == "renamed"
    assert before["identity"]["display_name"] == "old-name"
    assert after["identity"]["display_name"] == "renamed"


def test_the_identity_says_it_is_stable_and_why(registry):
    """A reader must be able to see which anchor was used without reverse
    engineering the ref string. "it happens to look stable" is not the same
    claim as "this anchor cannot be moved by a rename"."""
    labels = compose_labels("proj", service="svc")
    result = cont.attribute(cont.evidence(inspect_record("c1", labels=labels)),
                            registry=registry, roots=ROOTS)

    identity = result["identity"]
    assert identity["anchor"] == "compose"
    assert identity["runtime_ref"] == "runtime:container:compose:proj/svc"
    assert identity["stable_across_rename"] is True
    assert "name" in identity["why"]


def test_a_container_with_no_labels_is_identified_by_its_id_not_its_name(registry):
    """A plain container — no compose labels, nothing declared — is still not
    identified by its name. `docker inspect` always returns an `Id`, so the
    id anchor is always available and the name anchor is reached only when
    Docker returned no id at all.

    This is worth pinning because the opposite is the intuitive guess: with
    nothing else to go on it looks as though the name is all there is. It is
    not, and a name-derived ref is the one case that a rename can move."""
    result = cont.attribute(cont.evidence(inspect_record("bare")),
                            registry=registry, roots=ROOTS)

    identity = result["identity"]
    assert identity["anchor"] == "container-id"
    assert identity["stable_across_rename"] is True
    assert identity["runtime_ref"] == "runtime:container:" + "c" * 12
    # §9: the name did not disappear. It is the display label, and it is in
    # the alias set so a record keyed by the old name can still be re-keyed.
    assert identity["display_name"] == "bare"
    assert [a["anchor"] for a in identity["anchors"]] == ["container-id", "name"]


def test_the_name_anchor_is_the_honest_floor_and_says_it_is_not_stable():
    """Reachable only when the container reported no id — a malformed or
    truncated inspect record. The result must SAY a rename would change the
    identity rather than presenting a name-derived ref as if it were stable."""
    record = inspect_record("bare")
    record["Id"] = ""
    result = cont.attribute(cont.evidence(record), registry={}, roots=ROOTS)

    identity = result["identity"]
    assert identity["anchor"] == "name"
    assert identity["stable_across_rename"] is False
    assert identity["runtime_ref"] == "runtime:container:bare"
    # "renaming" — matched on the stem, because the point is that the reason
    # names the failure mode, not that it uses one particular word form.
    assert "renam" in identity["why"]
    # It is still an identity. A container identified weakly has not failed
    # to be identifiable, and both are different from having no identity.
    assert result["ref"] == "runtime:container:bare"


def test_the_declared_label_outranks_every_other_anchor(registry):
    """§9: `serveros.runtime_ref` is written by whoever created the container,
    so it is the identity even when compose labels disagree with it."""
    labels = compose_labels("proj", service="svc")
    labels["serveros.runtime_ref"] = "sos-litellm-baseline-9f3a"
    result = cont.attribute(cont.evidence(inspect_record("c1", labels=labels)),
                            registry=registry, roots=ROOTS)

    assert result["identity"]["anchor"] == "declared-label"
    assert result["ref"] == "runtime:container:sos-litellm-baseline-9f3a"
    # The compose anchor is not discarded — it stays in the alias set, which
    # is what lets a reader re-key a record written before the label existed.
    anchors = {a["anchor"] for a in result["identity"]["anchors"]}
    assert anchors == {"declared-label", "compose", "container-id", "name"}


def test_a_declared_ref_naming_a_different_kind_is_refused_not_rewritten(registry):
    """`runtime:volume:v1` is not a container. Re-prefixing it into
    `runtime:container:runtime:volume:v1` would invent an identity nobody
    wrote, so the label is rejected and the next anchor is used instead."""
    labels = compose_labels("proj", service="svc")
    labels["serveros.runtime_ref"] = "runtime:volume:v1"
    result = cont.attribute(cont.evidence(inspect_record("c1", labels=labels)),
                            registry=registry, roots=ROOTS)

    assert result["identity"]["anchor"] == "compose"
    assert "volume" not in result["ref"]


def test_every_anchor_carries_its_own_reason(registry):
    """The alias set is a list of things the container SAID, not a list of
    guesses. Each entry says why it qualifies."""
    labels = compose_labels("proj", service="svc")
    labels["serveros.runtime_ref"] = "declared"
    anchors = cont.stable_identity(cont.evidence(inspect_record("c1", labels=labels)))["anchors"]

    assert [a["anchor"] for a in anchors] == [
        "declared-label", "compose", "container-id", "name"]
    for anchor in anchors:
        assert anchor["because"]
        assert anchor["runtime_ref"].startswith("runtime:container:")
    # Only the name is moveable, and it is the only one that says so.
    assert [a["stable_across_rename"] for a in anchors] == [True, True, True, False]


def test_a_container_with_no_id_and_no_name_has_no_identity():
    """Not an empty string standing in for "unknown" — None, with a reason."""
    identity = cont.stable_identity({"compose": {}, "runtime_labels": {}})
    assert identity["runtime_ref"] is None
    assert identity["anchor"] is None
    assert identity["anchors"] == []
    assert identity["stable_across_rename"] is False
    assert identity["why"]


def test_a_registry_anchor_survives_a_rename_the_name_would_have_missed(registry):
    """§10 at the registry rung. Rung 3 is a NAME lookup, so a rename defeats
    it. A registry that pins its container by a compose anchor keeps matching
    after the name moves."""
    document = registry_doc()
    document["resources"][2]["probes"][0] = {
        "id": "l4", "container": "lit-0104-dev100-litellm",
        "anchor": "compose:lit-0104-e2e/litellm",
    }
    anchored = {"registry_present": True,
                **cont.build_registry_index(document),
                "owner_label_namespaces": {}}

    labels = compose_labels("lit-0104-e2e", service="litellm")
    result = cont.attribute(cont.evidence(inspect_record("renamed-entirely", labels=labels)),
                            registry=anchored, roots=ROOTS)

    assert result["owner"]["ref"] == "work-item:LIT-0104"
    assert result["disposition"] == "PROTECTED"
    assert "anchor" in result["attribution"]["decided_by"]
    assert result["attribution"]["registry_resource_id"] == "lit0104-dev-litellm"


def test_a_registry_with_no_anchors_reports_a_miss_instead_of_only_UNKNOWN(registry):
    """What a rename looks like from the outside when the registry has not
    declared an anchor. The disposition is still DEFER — that does not
    change — but the record now names every identity it tried, so an operator
    can tell a rename from a container that was never registered."""
    labels = compose_labels("unregistered-project", service="app")
    result = cont.attribute(cont.evidence(inspect_record("mystery", labels=labels)),
                            registry=registry, roots=ROOTS)

    assert result["disposition"] == "DEFER"
    assert result["unknown_owner"] is True
    tried = result["registry_anchors_tried"]
    assert tried["matched"] is False
    assert "runtime:container:compose:unregistered-project/app" in tried["tried"]
    assert tried["note"]


def test_an_attributed_container_reports_no_registry_miss(registry):
    """The negative half. `registry_anchors_tried` is assembled beside the
    anchor loop but must not be REPORTED for a container a later rung goes on
    to attribute — that would put a miss in the output next to a match."""
    labels = compose_labels("litellm-baseline")
    labels["com.docker.compose.project.working_dir"] = "/opt/applications/data/home-hwz/workspace/test-envs/lit-0104"
    result = cont.attribute(cont.evidence(inspect_record("lit-0104-dev100-litellm",
                                                         labels=labels)),
                            registry=registry, roots=ROOTS)

    assert result["owner"]["ref"] == "work-item:LIT-0104"
    assert result["registry_anchors_tried"] is None


# ---------------------------------------------------------------------------
# §11 · MANAGED / EXTERNAL / UNKNOWN
# ---------------------------------------------------------------------------


def _management(record, *, registry=None, roots=ROOTS):
    return cont.attribute(cont.evidence(record), registry=registry or {},
                          roots=roots)["management"]


def test_a_registered_container_is_managed_authoritatively(registry):
    """The registry is the system of record, so a hit is a statement rather
    than an inference."""
    result = _management(inspect_record("litellm-container0-litellm"), registry=registry)
    assert result["class"] == "MANAGED"
    assert result["confidence"] == "AUTHORITATIVE"
    assert result["decided_by"] == "registry-match"


def test_a_declared_managed_label_outranks_the_registry():
    """§9: the container declaring its own state is the strongest evidence
    there is, and it is read before anything else."""
    record = inspect_record("anything", labels={"serveros.managed": "true"})
    result = _management(record, registry={})
    assert result["class"] == "MANAGED"
    assert result["confidence"] == "AUTHORITATIVE"
    assert result["decided_by"] == "docker-label"


def test_a_container_that_says_it_is_not_managed_is_external_not_unknown():
    """`serveros.managed=false` is a statement, and collapsing it into the
    same UNKNOWN as a container that said nothing throws away the only
    positive evidence in the rung."""
    result = _management(inspect_record("x", labels={"serveros.managed": "false"}))
    assert result["class"] == "EXTERNAL"
    assert result["confidence"] == "AUTHORITATIVE"
    assert "false" in result["because"]


def test_an_unparseable_managed_value_is_not_read_as_false():
    """`serveros.managed=1` is neither. Reading it as `not managed` would be
    a guess dressed as a reading, and reading it as True would be the same
    guess pointed the other way."""
    result = _management(inspect_record("x", labels={"serveros.managed": "1"}))
    assert result["class"] == "UNKNOWN"
    assert result["declared"]["managed_label_raw"] == "1"


def test_an_identity_label_without_the_managed_flag_is_managed_by_inference():
    record = inspect_record("x", labels={"serveros.runtime_ref": "sos-a-b-1"})
    result = _management(record, registry={})
    assert result["class"] == "MANAGED"
    assert result["confidence"] == "INFERRED"
    assert "serveros.*" in result["because"]


def test_a_foreign_mount_makes_a_container_external(registry):
    """Kept on the same axis as the §10 foreign_actor finding: this is what
    positive evidence that a resource is somebody else's looks like."""
    result = _management(
        inspect_record("new-api",
                       mounts=[{"Type": "bind", "Source": "/home/szj/gpt-img-2/data",
                                "Destination": "/data", "Mode": "rw"}]),
        registry=registry)
    assert result["class"] == "EXTERNAL"
    assert result["confidence"] == "INFERRED"
    assert result["decided_by"] == "bind-mount"


def test_an_unrecognised_label_namespace_makes_a_container_external(registry):
    """`com.multiuserclaw.hermes-runtime-config` is a dotted key that is not
    an image convention — so some other system labels this container."""
    labels = {"com.multiuserclaw.hermes-runtime-config": "{}"}
    result = _management(inspect_record("hermes-1", labels=labels), registry=registry)
    assert result["class"] == "EXTERNAL"
    assert result["confidence"] == "INFERRED"


def test_an_ecosystem_label_alone_does_not_make_a_container_external(registry):
    """The negative half, and the one that matters for the live inventory:
    `org.opencontainers.image.*` and `com.docker.compose.*` are on nearly
    every container, including ours. Treating them as somebody else's label
    would classify most of this host as EXTERNAL."""
    labels = {"org.opencontainers.image.source": "x",
              "com.docker.compose.project": "p",
              "com.docker.compose.service": "s"}
    result = _management(inspect_record("c1", labels=labels), registry=registry)
    assert result["class"] == "UNKNOWN"


def test_a_bare_word_label_does_not_make_a_container_external(registry):
    """`maintainer` and `version` are image metadata. They have no namespace
    and say nothing about who runs the container."""
    result = _management(inspect_record("c1", labels={"maintainer": "x", "version": "1"}),
                         registry=registry)
    assert result["class"] == "UNKNOWN"


def test_an_absent_registry_is_unknown_not_external():
    """With no system of record there is nothing to be external TO. The class
    is UNKNOWN and the reason says why, rather than defaulting to the class
    that sounds safer."""
    result = _management(inspect_record("c1"), registry={"registry_present": False})
    assert result["class"] == "UNKNOWN"
    assert "absent" in result["because"]


def test_management_is_not_a_safety_claim(registry):
    """§11. A MANAGED container with an unknown owner keeps UNKNOWN_OWNER and
    DEFER. Merging the two axes is the substitution this module exists to
    refuse, so the pair is pinned together."""
    labels = {"serveros.managed": "true"}
    result = cont.attribute(cont.evidence(inspect_record("mystery", labels=labels)),
                            registry=registry, roots=ROOTS)

    assert result["management"]["class"] == "MANAGED"
    assert result["management"]["confidence"] == "AUTHORITATIVE"
    assert result["unknown_owner"] is True
    assert result["disposition"] == "DEFER"
    assert result["reclaim_candidate"] is False
    assert "not a safety claim" in result["management"]["note"]


def test_a_declared_protection_of_true_is_reported(registry):
    record = inspect_record("x", labels={"serveros.protected": "true"})
    result = _management(record, registry=registry)
    assert result["declared"]["protected"] is True


def test_an_unreadable_protection_is_reported_and_relaxes_nothing():
    """The bypass this guards: `bool("false")` is True, so a naive read of an
    unparseable value is not merely wrong, it is wrong in an unpredictable
    direction. The value is reported as what it was."""
    result = _management(inspect_record("x", labels={"serveros.protected": "perhaps"}))
    assert result["declared"]["protected"] is None
    assert result["declared"]["protected_because"]
    assert "NOT read as unprotected" in result["declared"]["protected_because"]


def test_an_absent_protection_says_absent_rather_than_false():
    result = _management(inspect_record("x"))
    assert result["declared"]["protected"] is None
    assert "no protection was declared" in result["declared"]["protected_because"]


def test_the_runtime_namespace_keys_never_reach_the_opaque_namespace_rung(registry):
    """The footgun this closes: rung 2 trusts the CONTRACT for a namespace it
    is declared, so leaving `serveros.protected` in the opaque-key list would
    let one declaration of the `serveros` namespace assign every labelled
    container on the host to a single owner — from a label that says nothing
    about ownership."""
    declaring = {"registry_present": True,
                 **cont.build_registry_index(registry_doc()),
                 "owner_label_namespaces": {"serveros": "project:whoever-declared-it"}}
    labels = {"serveros.protected": "true", "serveros.scope": "machine"}
    result = cont.attribute(cont.evidence(inspect_record("x", labels=labels)),
                            registry=declaring, roots=ROOTS)

    assert result["owner"]["ref"] is None
    assert result["unknown_owner"] is True
    assert result["disposition"] == "DEFER"


def test_the_owner_ref_label_is_read_directly_and_decides(registry):
    """`serveros.owner_ref` is a known key with defined semantics, so it is
    read as an owner claim in its own right — not as an opaque namespace."""
    labels = {"serveros.owner_ref": "work-item:LIT-0104",
              "serveros.owner_type": "work-item"}
    result = cont.attribute(cont.evidence(inspect_record("x", labels=labels)),
                            registry=registry, roots=ROOTS)

    assert result["owner"]["ref"] == "work-item:LIT-0104"
    assert result["owner"]["type"] == "work-item"
    assert result["disposition"] == "PROTECTED"


def test_a_recorded_namespace_does_not_make_our_own_containers_external():
    """The live misclassification this corrected, pinned.

    Reading any dotted non-ecosystem key as "somebody else's system" put 17 of
    37 containers in EXTERNAL and 10 of those were ours — the `litellm.*`
    build-provenance containers and the `hwz.p18` pair. The contract records
    all three namespaces, so the classifier is told which ones this host has
    already accounted for.
    """
    recognised = {"litellm": "recorded and deliberately undeclared",
                  "hwz": "recorded and deliberately undeclared",
                  "com.multiuserclaw": "recorded and deliberately undeclared"}
    result = cont.management_class(
        evidence_record=cont.evidence(inspect_record(
            "lit-0104-dev100-litellm",
            labels={"litellm.source-sha": "abc", "litellm.profile": "dev"})),
        owner_ref=None, registry_resource_id=None, foreign_actor=False,
        registry_present=True, recognised_namespaces=recognised)

    assert result["class"] != "EXTERNAL"


def test_a_recorded_but_unresolved_namespace_is_unknown_not_managed():
    """The other direction, and the reason the branch falls through rather
    than returning MANAGED. `com.multiuserclaw.*` is another product's
    namespace running on the same host. The contract knows about it and
    deliberately resolves nothing — so the management class is genuinely
    undetermined. Calling it MANAGED would import the same assumption as
    calling it EXTERNAL, pointed the other way, and §11 asks for neither."""
    recognised = {"com.multiuserclaw": "recorded and deliberately undeclared"}
    result = cont.management_class(
        evidence_record=cont.evidence(inspect_record(
            "hermes-user-1",
            labels={"com.multiuserclaw.hermes-runtime-config": "{}"})),
        owner_ref=None, registry_resource_id=None, foreign_actor=False,
        registry_present=True, recognised_namespaces=recognised)

    assert result["class"] == "UNKNOWN"
    assert result["confidence"] == "UNKNOWN"
    assert "deliberately not resolved" in result["because"]
    assert result["decided_by"] is None


def test_an_unrecorded_namespace_still_reads_as_external_on_the_same_ladder():
    """The positive half: recognising three namespaces must not make the
    EXTERNAL branch unreachable for everything else."""
    recognised = {"litellm": "recorded and deliberately undeclared"}
    result = cont.management_class(
        evidence_record=cont.evidence(inspect_record(
            "stranger", labels={"acme.internal.runtime": "x"})),
        owner_ref=None, registry_resource_id=None, foreign_actor=False,
        registry_present=True, recognised_namespaces=recognised)

    assert result["class"] == "EXTERNAL"
    assert "acme.internal" in result["because"]


def test_an_empty_recognised_set_is_the_stricter_answer():
    """An unreadable contract recognises nothing, and that direction is
    deliberate: with nothing recognised MORE containers are called EXTERNAL,
    and EXTERNAL is the branch that never reclaims."""
    result = cont.management_class(
        evidence_record=cont.evidence(inspect_record(
            "x", labels={"litellm.profile": "dev"})),
        owner_ref=None, registry_resource_id=None, foreign_actor=False,
        registry_present=True, recognised_namespaces={})

    assert result["class"] == "EXTERNAL"


def test_the_contract_is_the_source_of_the_recognised_namespaces():
    """Read from the contract rather than hardcoded, so a namespace is added
    to that list in one place and the classifier follows."""
    recognised = cont.load_recognised_namespaces()
    assert "litellm" in recognised
    assert "hwz" in recognised
    assert "com.multiuserclaw" in recognised
    assert "serveros" in recognised
    # `serveros` is the runtime namespace and is recorded from a different
    # section, which is the case a reader hardcoding the other two misses.
    assert "runtime namespace" in recognised["serveros"]


def test_an_unreadable_contract_recognises_nothing(tmp_path):
    """Not "recognises everything": a contract that failed to parse must not
    make the host more permissive than a contract that declares nothing."""
    assert cont.load_recognised_namespaces(tmp_path / "absent.yaml") == {}


def test_the_coverage_summary_counts_the_classes_it_cannot_name():
    """§11. UNKNOWN is the majority bucket on this host, and a summary that
    listed only the classes it could name would render "we could not tell" as
    a short list rather than as the largest one."""
    host = FakeDockerHost({
        "registered": inspect_record("litellm-container0-litellm"),
        "stranger": inspect_record("stranger", labels={"acme.runtime": "x"}),
        "nothing": inspect_record("nothing"),
    })
    result = cont.attribute_all(host, registry={"registry_present": True,
                                                **cont.build_registry_index(registry_doc())},
                                roots=ROOTS)

    assert result["count"] == 3
    assert result["by_management"] == {"MANAGED": 1, "EXTERNAL": 1, "UNKNOWN": 1}
    assert sum(result["by_management"].values()) == result["count"]
    assert sum(result["by_management_confidence"].values()) == result["count"]


def test_the_coverage_summary_reports_identity_stability():
    """§10, as a property of the inventory rather than of one row. Today the
    list is empty and the flag is true; a rename would land here."""
    host = FakeDockerHost({
        "a": inspect_record("a"),
        "b": inspect_record("b"),
    })
    result = cont.attribute_all(host, registry={}, roots=ROOTS)

    assert result["identity_stable_across_rename"] is True
    assert result["name_derived_identities"] == []


def test_an_id_less_container_names_itself_in_the_instability_list():
    """The other half. If a container ever does come back name-anchored, the
    summary must name it — a boolean alone would say the inventory is unstable
    without saying which container made it so."""
    nameless = inspect_record("only-a-name")
    nameless["Id"] = ""
    host = FakeDockerHost({"only-a-name": nameless, "b": inspect_record("b")})
    result = cont.attribute_all(host, registry={}, roots=ROOTS)

    assert result["identity_stable_across_rename"] is False
    assert result["name_derived_identities"] == ["only-a-name"]


# ---------------------------------------------------------------------------
# §8 · the runtime ownership model
# ---------------------------------------------------------------------------

# The brief's field list, verbatim. Every one must be present in every row —
# a model that omits the fields it could not fill is a model that reads as
# complete while being partial.
RUNTIME_FIELDS = (
    "runtime_ref", "device_ref", "owner_ref", "owner_type", "scope",
    "desired_state", "actual_state", "protected", "retention", "created_by",
    "created_at", "last_observed_at", "provider", "resource_refs",
)


def _runtime(record, *, registry=None, observed_at=None):
    return cont.attribute(cont.evidence(record), registry=registry or {},
                          roots=ROOTS, observed_at=observed_at)["runtime"]


def test_every_field_in_the_model_is_present_in_every_row(registry):
    result = _runtime(inspect_record("c1"), registry=registry)
    for field in RUNTIME_FIELDS:
        assert field in result, f"§8 field {field!r} is missing from the model"


def test_the_model_carries_the_identity_the_row_carries(registry):
    """One identity, not two that can disagree."""
    row = cont.attribute(cont.evidence(inspect_record("c1")), registry=registry, roots=ROOTS)
    assert row["runtime"]["runtime_ref"] == row["identity"]["runtime_ref"] == row["ref"]


def test_an_unmeasurable_field_is_none_and_named_in_unknowns(registry):
    """§8's hardest case. `retention` has no source on this host, so it is
    None — and it is named with a reason. None alone is ambiguous between
    "there is no retention" and "we did not look"."""
    result = _runtime(inspect_record("c1"), registry=registry)
    assert result["retention"] is None
    assert "retention" in result["unknowns"]
    assert result["unknowns"]["retention"]


def test_desired_state_is_unknown_rather_than_defaulted_to_running(registry):
    """The anomaly generator that a default would build. If desired_state
    defaulted to "running", every intentionally-stopped container becomes a
    §15 DESIRED_ACTUAL_MISMATCH — an alert per stopped test container, all of
    them false, and the detector's precision gone before it has a real source
    for desired state."""
    stopped = _runtime(inspect_record("c1", status="exited"), registry=registry)
    assert stopped["desired_state"] is None
    assert stopped["actual_state"] == "exited"
    assert "desired_state" in stopped["unknowns"]
    assert "anomaly" in stopped["unknowns"]["desired_state"]


def test_the_observation_time_is_stamped_by_the_caller_not_invented(registry):
    """`attribute()` stays pure. The stamp is an argument, so a row built with
    no tick says so instead of reading the wall clock."""
    assert _runtime(inspect_record("c1"), registry=registry)["last_observed_at"] is None
    stamped = _runtime(inspect_record("c1"), registry=registry,
                       observed_at="2026-09-14T00:00:00Z")
    assert stamped["last_observed_at"] == "2026-09-14T00:00:00Z"


def test_actual_state_is_the_containers_own_reported_state(registry):
    assert _runtime(inspect_record("c1", status="exited", exit_code=3),
                    registry=registry)["actual_state"] == "exited"


def test_the_device_comes_from_the_registry_that_describes_this_node():
    """Read rather than re-derived: this observer is running on the device its
    registry names, and a second way to compute that identity is a second
    answer waiting to disagree with the first."""
    registry = {"registry_present": True,
                **cont.build_registry_index({**registry_doc(),
                                             "device_ref": "device:server-beijing"}),
                "owner_label_namespaces": {}}
    assert _runtime(inspect_record("c1"), registry=registry)["device_ref"] == "device:server-beijing"


def test_a_declared_device_that_disagrees_is_reported_and_not_resolved():
    """Both values are kept. This observer is on the node its registry names
    and the container says it was made for another one — that difference is
    the finding, and replacing either value with the other deletes it."""
    registry = {"registry_present": True,
                **cont.build_registry_index({**registry_doc(),
                                             "device_ref": "device:server-beijing"}),
                "owner_label_namespaces": {}}
    labels = {"serveros.device_ref": "device:server-shanghai"}
    result = _runtime(inspect_record("c1", labels=labels), registry=registry)

    assert result["device_ref"] == "device:server-shanghai"
    assert "server-beijing" in result["unknowns"]["device_ref_drift"]


def test_protection_is_read_from_both_sources_together(registry):
    """A container that declares itself protected IS protected even when
    nothing registered it, and a registered protected resource is protected
    even when the container says nothing."""
    from_label = _runtime(inspect_record("nobody-registered-this",
                                         labels={"serveros.protected": "true"}))
    assert from_label["protected"] is True

    from_registry = _runtime(inspect_record("litellm-container0-litellm"), registry=registry)
    assert from_registry["protected"] is True


def test_an_undeclared_protection_is_none_not_false(registry):
    """An undeclared protection reported as unprotected is a protection
    bypass performed by a default value."""
    result = _runtime(inspect_record("c1"), registry=registry)
    assert result["protected"] is None
    assert "protected" in result["unknowns"]
    assert "NOT reported as unprotected" in result["unknowns"]["protected"]


def test_created_by_is_unknown_because_docker_does_not_record_it(registry):
    """Docker records WHEN a container was created and never who or what
    created it. The reason is the finding."""
    result = _runtime(inspect_record("c1"), registry=registry)
    assert result["created_by"] is None
    assert result["created_at"] == "2026-09-11T00:00:00Z"
    assert "docker inspect" in result["unknowns"]["created_by"]


def test_the_provider_is_named(registry):
    assert _runtime(inspect_record("c1"), registry=registry)["provider"] == "docker"


def test_resource_refs_name_the_volumes_and_networks_the_container_holds(registry):
    record = inspect_record(
        "c1",
        mounts=[{"Type": "volume", "Name": "lit-data", "Source": "/var/lib/docker/volumes/lit-data/_data",
                 "Destination": "/data", "Mode": "rw"},
                {"Type": "bind", "Source": "/srv/x", "Destination": "/x", "Mode": "ro"}],
        networks={"bridge": {"IPAddress": "1.2.3.4"}})
    refs = {r["ref"] for r in _runtime(record, registry=registry)["resource_refs"]}

    assert refs == {"volume:lit-data", "network:bridge"}
    # The bind mount is not a Docker resource, so it is not a resource ref.
    # It is path evidence and it is already reported as such.
    assert not any("srv" in r for r in refs)


def test_the_image_is_not_a_resource_ref(registry):
    """The image is what the container IS, not something it holds. Listing it
    would make an image look reachable through the container, and an image is
    the one resource a cleanup pass must never reach that way."""
    refs = _runtime(inspect_record("c1"), registry=registry)["resource_refs"]
    assert not any("img" in r["ref"] for r in refs)


def test_the_scope_is_derived_from_the_owners_kind(registry):
    """`work-item:LIT-0104` names a work item, so the scope is the work item.
    A reading of an observed value rather than a guess — and a declared scope
    still wins over it."""
    labels = {"serveros.owner_ref": "work-item:LIT-0104"}
    result = _runtime(inspect_record("c1", labels=labels), registry=registry)
    assert result["scope"] == "work-item"

    declared = _runtime(inspect_record("c1", labels={**labels, "serveros.scope": "machine"}),
                        registry=registry)
    assert declared["scope"] == "machine"


def test_an_underivable_scope_is_unknown_not_empty(registry):
    result = _runtime(inspect_record("c1"), registry=registry)
    assert result["scope"] is None
    assert "scope" in result["unknowns"]


def test_the_tick_stamps_every_row_with_the_same_time():
    """One report, one time. Rows observed a few hundred milliseconds apart
    would make a reader unable to tell one tick from two."""
    class ClockedHost(FakeDockerHost):
        def now(self):
            return "2026-09-14T00:00:00Z"

    host = ClockedHost({"a": inspect_record("a"), "b": inspect_record("b")})
    result = cont.attribute_all(host, registry={}, roots=ROOTS)
    stamps = {row["runtime"]["last_observed_at"] for row in result["containers"]}
    assert stamps == {"2026-09-14T00:00:00Z"}


def test_a_host_with_no_clock_leaves_the_stamp_null_but_still_reports():
    """Not an error and not a crash: a host that cannot say when is a real
    state, and the rows are still produced."""
    host = FakeDockerHost({"a": inspect_record("a")})
    assert not hasattr(host, "now")
    result = cont.attribute_all(host, registry={}, roots=ROOTS)
    assert result["count"] == 1
    assert result["containers"][0]["runtime"]["last_observed_at"] is None
