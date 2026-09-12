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
