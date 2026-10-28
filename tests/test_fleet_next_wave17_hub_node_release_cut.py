"""Wave 17 · Hub/Node independent release cut — offline RED -> GREEN.

Everything below runs against fakes, AST reads and temporary directories. No
install is performed, no service manager is addressed, no port is bound, no
socket is opened, no credential is read, and no device is contacted. A green
run is a statement about the **release units**, never about a real co-install;
that remains `NOT_RUN`.

The RED side is the live tree: #502's 49/180 ownership map, one Hub -> Node
import and ten Node -> Hub imports, and two artifacts that cannot boot without
a checkout. The GREEN side is a throwaway `git archive` copy with the owner
handoff applied — never the checkout itself.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CUT_DIR = REPO / "integrations/fleet-next-wave17-hub-node-release-cut"
VERSION = "1.0.0+wave17test"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cut = _load("wave17_cut", CUT_DIR / "wave17_cut.py")
handoff = _load("wave17_apply_handoff", CUT_DIR / "handoff" / "apply_handoff.py")

BASELINE_NODE_FOREIGN = {
    "fleet_mcp",
    "fleet_mcp.config",
    "fleet_mcp.errors",
    "fleet_mcp.fleet",
    "fleet_mcp.identity",
    "fleet_mcp.local_roots",
    "fleet_mcp.node_protocol",
    "fleet_mcp.owner_access",
    "fleet_mcp.providers.cloudcli",
    "fleet_mcp.runtime_identity",
}


@pytest.fixture(scope="session")
def sandbox() -> Path:
    """A copy of HEAD with the owner handoff applied; never the checkout."""
    root = Path(tempfile.mkdtemp(prefix="wave17-test-sandbox-"))
    archive = subprocess.run(["git", "-C", str(REPO), "archive", "HEAD"],
                             capture_output=True, check=True).stdout
    with tarfile.open(fileobj=__import__("io").BytesIO(archive)) as tar:
        try:
            tar.extractall(root, filter="data")
        except TypeError:  # Python < 3.12
            tar.extractall(root)
    handoff.apply(root)
    yield root
    shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# the builder is #431's, and it has not moved
# ---------------------------------------------------------------------------
def test_builder_is_431_and_pinned():
    builder = cut.load_builder(REPO)
    assert builder.__file__ == str(REPO / cut.BUILDER_REL)
    assert hasattr(builder, "closure") and hasattr(builder, "measure_standalone")
    import hashlib
    actual = hashlib.sha256(
        (REPO / cut.BUILDER_REL).read_bytes()).hexdigest()
    assert actual == cut.BUILDER_PIN_SHA256
    # The two lanes must not be able to disagree about the closure of a module.
    assert builder.closure("fleet_mcp.node.connector", REPO).modules


# ---------------------------------------------------------------------------
# RED: the live tree is #502's shape
# ---------------------------------------------------------------------------
def test_baseline_reproduces_502_and_blocks_the_owner():
    report = cut.analyze(REPO, VERSION)
    assert report["ownership"]["counts"] == {"hub": 49, "node": 180, "sidecar": 2}
    assert report["ownership"]["foreign"]["hub"] == {
        "fleet_mcp.node.connector": ["fleet_mcp/owner_setup.py"]}
    assert set(report["ownership"]["foreign"]["node"]) == BASELINE_NODE_FOREIGN
    assert report["ownership"]["independent"] == {"hub": False, "node": False}
    assert report["partition"]["base_exact"] is False
    assert report["partition"]["node_own"] == 0
    assert report["technical_state"] == cut.BLOCKED_OWNER
    assert report["decision"] == cut.BLOCKED_OWNER
    assert report["release_permitted"] is False
    assert report["device_co_install"] == cut.NOT_RUN


def test_baseline_two_artifact_units_cannot_boot(tmp_path):
    builder = cut.load_builder(REPO)
    answer = cut.two_artifact_only(builder, REPO, tmp_path)
    assert answer["hub"]["imported"] is False
    assert answer["node"]["imported"] is False
    # The failures are the missing neighbour/shared layer, not a missing wheel.
    assert answer["hub"]["error"] in {"ModuleNotFoundError", "FleetRefusal"}
    assert answer["node"]["error"] == "ModuleNotFoundError"


# ---------------------------------------------------------------------------
# the handoff only ever touches a copy
# ---------------------------------------------------------------------------
def test_handoff_refuses_the_live_checkout(tmp_path):
    with pytest.raises(handoff.HandoffRefused):
        handoff.guard(REPO)
    decoy = tmp_path / "decoy"
    (decoy / ".git").mkdir(parents=True)
    with pytest.raises(handoff.HandoffRefused):
        handoff.guard(decoy)
    with pytest.raises(handoff.HandoffRefused):
        handoff.guard(tmp_path / "does-not-exist")


def test_handoff_does_not_modify_the_live_tree(sandbox):
    proc = subprocess.run(
        ["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no",
         "--", "fleet_mcp", "runtime", "scripts"],
        capture_output=True, text=True, check=True)
    assert proc.stdout.strip() == "", (
        "the handoff or the measurement modified an owner's file:\n"
        + proc.stdout)


# ---------------------------------------------------------------------------
# GREEN: the ownership map and the release map agree in the sandbox
# ---------------------------------------------------------------------------
def test_handoff_makes_the_maps_agree(sandbox, tmp_path):
    report = cut.analyze(sandbox, VERSION, workdir=tmp_path,
                         baseline_root=REPO, tree_kind="sandbox")
    assert report["ownership"]["counts"] == {"hub": 50, "node": 180, "sidecar": 2}
    assert report["ownership"]["foreign"]["hub"] == {}
    assert set(report["ownership"]["foreign"]["node"]) == (
        BASELINE_NODE_FOREIGN - {"fleet_mcp.fleet"})
    assert report["effective_foreign"] == {"hub": {}, "node": {}}
    assert report["partition"]["base_exact"] is True
    assert report["partition"]["shared"] == len(cut.BASE_ADMISSIBLE) == 19
    assert sorted(report["partition"]["base"]) == sorted(cut.BASE_ADMISSIBLE)
    assert report["partition"]["hub_own"] == 13
    assert report["partition"]["node_own"] == 2
    assert report["technical_state"] == cut.GREEN_SANDBOX
    # A green sandbox is still not a release.
    assert report["decision"] == cut.BLOCKED_OWNER
    assert report["release_permitted"] is False
    assert report["device_co_install"] == cut.NOT_RUN
    assert report["baseline"]["ownership"]["counts"]["hub"] == 49


def test_green_units_boot_without_a_checkout_or_the_neighbour(sandbox, tmp_path):
    report = cut.analyze(sandbox, VERSION, workdir=tmp_path,
                         baseline_root=REPO, tree_kind="sandbox")
    three = report["isolation"]["three_unit"]
    assert three["node"]["booted"] is True
    assert three["node"]["device_ref"] == "device:wave17-hermetic"
    assert three["hub"]["booted"] is True
    assert three["hub"]["authority_symbols"] is True
    assert three["hub"]["dispatch_refused"] is True
    # The Hub was installed without the Node artifact, and says so by name.
    assert three["hub"]["node_loader"] == "NODE_CONFIG_LOADER_UNAVAILABLE"
    # The strictly two-artifact model, measured on the BASELINE tree, fails.
    two = report["isolation"]["two_artifact_only"]
    assert two["hub"]["imported"] is False
    assert two["node"]["imported"] is False
    assert two["node"]["error"] == "ModuleNotFoundError"


# ---------------------------------------------------------------------------
# manifests: reproducible, disjoint, and no second identity
# ---------------------------------------------------------------------------
def test_manifests_are_reproducible_and_disjoint(sandbox, tmp_path):
    builder = cut.load_builder(sandbox)
    first = cut.compose(builder, sandbox, VERSION)
    second = cut.compose(builder, sandbox, VERSION)
    for kind in first:
        assert first[kind].tree_sha256 == second[kind].tree_sha256
        assert first[kind].member_sha256 == second[kind].member_sha256
        assert first[kind].manifest == second[kind].manifest

    base, hub, node = (set(first[cut.BASE_KIND].members),
                       set(first[cut.HUB_KIND].members),
                       set(first[cut.NODE_KIND].members))
    assert not (base & hub) and not (base & node) and not (hub & node)
    assert node == {"fleet_mcp/node/__init__.py",
                    "fleet_mcp/node/connector.py"}
    assert "fleet_mcp/server.py" in hub and "fleet_mcp/fleet.py" in hub
    assert "fleet_mcp/server.py" not in node
    assert not any(rel.startswith("fleet_mcp/node/") for rel in hub)
    assert not any(rel in base for rel in
                   ("fleet_mcp/server.py", "fleet_mcp/fleet.py",
                    "fleet_mcp/registry.py", "fleet_mcp/node/connector.py",
                    "fleet_mcp/providers/fleet_node.py"))

    one, two = tmp_path / "one", tmp_path / "two"
    cut.write_plans(first, one)
    cut.write_plans(second, two)
    for rel in sorted(first[cut.HUB_KIND].members):
        assert (one / cut.HUB_KIND / rel).read_bytes() == \
               (two / cut.HUB_KIND / rel).read_bytes()
    assert (one / "wave17-manifest.json").read_bytes() == \
           (two / "wave17-manifest.json").read_bytes()
    recorded = json.loads((one / "wave17-manifest.json").read_text())
    assert recorded["artifacts"][cut.NODE_KIND]["tree_sha256"] == \
           first[cut.NODE_KIND].tree_sha256


def test_there_is_one_identity_authority(sandbox):
    authority = cut.authority_members(sandbox)
    assert set(authority) == {
        "fleet/transport.py", "fleet/transport-contract.yaml",
        "fleet-authority.json"}
    declared = json.loads(authority["fleet-authority.json"])
    assert declared["single_authority"] is True
    assert declared["abi"] == "serveros.fleet.transport/v1"
    assert set(declared["files"]) == {"fleet/transport.py",
                                      "fleet/transport-contract.yaml"}
    assert "fleet/registry.yaml" not in authority

    # The seven names exist once, in the authority, and nowhere else in the
    # four units. A second `DeviceRef` would agree today and diverge later.
    for name in ("class DeviceRef", "class DeviceRegistry", "class Device:"):
        holders = [rel for rel, data in authority.items()
                   if name.encode() in data]
        assert holders == ["fleet/transport.py"], name
    builder = cut.load_builder(sandbox)
    plans = cut.compose(builder, sandbox, VERSION)
    for kind, plan in plans.items():
        for rel, data in plan.members.items():
            if rel.startswith("fleet/"):
                continue
            assert b"class DeviceRef" not in data, f"{kind}:{rel} redefines DeviceRef"
            assert b"class DeviceRegistry" not in data, \
                f"{kind}:{rel} redefines DeviceRegistry"


# ---------------------------------------------------------------------------
# no shared socket / lock / state / credential
# ---------------------------------------------------------------------------
def test_shared_runtime_state_is_refused():
    with pytest.raises(cut.Refused):
        cut._refuse_unsafe_members({"fleet_mcp/node/connector.sock": b""}, "node")
    with pytest.raises(cut.Refused):
        cut._refuse_unsafe_members({"credentials.json": b""}, "hub")
    with pytest.raises(cut.Refused):
        cut._refuse_unsafe_members({"../escape.py": b""}, "hub")

    same = {"install_path": "/opt/fleet-next/hub", "port": 8101,
            "socket": None, "backend_id": "hub-a", "oauth_audience": "https://a",
            "cred_state_root": "/var/lib/next/hub", "lock": "/var/lock/hub",
            "unit": "next-hub.service", "device_ref": "device:tokyo"}
    other = {"install_path": "/opt/fleet-next/node", "port": 3402,
             "socket": None, "backend_id": "node-a",
             "oauth_audience": "https://b", "cred_state_root": "/var/lib/next/node",
             "lock": "/var/lock/node", "unit": "next-node.service",
             "device_ref": "device:tokyo"}
    assert cut.resource_matrix(same, other)["decision"] == cut.OK
    assert cut.resource_matrix(same, dict(same, port=8102))["code"] == \
           cut.BLOCKED_RESOURCE_COLLISION
    assert cut.resource_matrix(same, dict(other, device_ref="device:osaka"))["code"] == \
           cut.BLOCKED_DEVICE_DISAGREEMENT
    assert cut.resource_matrix(
        {"install_path": "/srv/next", "cred_state_root": "/srv/next/state"},
        {"install_path": "/opt/node", "cred_state_root": "/opt/node/state"}
    )["collisions"] == []  # different names, different machines, no overlap


def test_release_units_declare_no_runtime_resources(sandbox):
    builder = cut.load_builder(sandbox)
    plans = cut.compose(builder, sandbox, VERSION)
    for plan in plans.values():
        assert plan.manifest["runtime_resources"] == {
            "socket": None, "lock": None, "state_root": None, "credential": None}
    cut.refuse_shared_runtime_state(plans)


# ---------------------------------------------------------------------------
# reverse delete / rollback leaves the Legacy fake alone
# ---------------------------------------------------------------------------
def test_reverse_delete_and_rollback_leave_legacy_unchanged(sandbox, tmp_path):
    builder = cut.load_builder(sandbox)
    plans = cut.compose(builder, sandbox, VERSION)
    previous = cut.compose(builder, sandbox, "0.9.0+wave17prev")
    answer = cut.reverse_delete_and_rollback(plans, previous, tmp_path)
    assert answer["decision"] == cut.OK
    assert answer["code"] == cut.OK
    assert answer["legacy_unchanged"] is True
    assert answer["deliveries"] == []
    assert answer["steps"][0][0] == "reverse-delete-node"
    assert answer["steps"][0][2]["remaining_node_files"] == 0
    assert answer["steps"][1][0] == "rollback-hub"
    assert answer["steps"][1][1] == "OK"
    assert answer["steps"][1][2]["previous_version"] == "0.9.0+wave17prev"


# ---------------------------------------------------------------------------
# the negatives the report must keep
# ---------------------------------------------------------------------------
def test_report_never_claims_a_release_or_a_device_run(sandbox):
    live = cut.analyze(REPO, VERSION)
    green = cut.analyze(sandbox, VERSION, baseline_root=REPO, tree_kind="sandbox")
    for report in (live, green):
        assert report["decision"] == cut.BLOCKED_OWNER
        assert report["release_permitted"] is False
        assert report["device_co_install"] == cut.NOT_RUN
    assert live["technical_state"] == cut.BLOCKED_OWNER
    assert green["technical_state"] == cut.GREEN_SANDBOX
