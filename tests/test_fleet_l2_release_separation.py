"""D7: the node artifact and the hub artifact are two releases, and say so.

Every test here is offline. Nothing installs, starts, connects, or reads a
credential; the archives are written to `tmp_path`, read back, and thrown away.

The measured starting point, asserted rather than described: on `main` the node
connector's closure and the hub's closure differ by an entrypoint, not by a
surface — 30 members each, sharing 17 modules beyond the node's own leaves. That
is why `NODE_SHARED_SURFACE` is a pinned list rather than a rule, and why the
first test below fails the day the coupling grows without a decision.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "integrations" / "fleet-v1-l2-release"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


l2 = _load("l2_release", PACKAGE / "l2_release.py")
#: The release tool, loaded (not modified) so the contract is asserted against the
#: file that will actually read these artifacts.
RELEASE = _load("serveros_release", ROOT / "scripts" / "release.py")

NODE_VERSION = "1.4.0+n20261028a"
HUB_VERSION = "2.1.0+h20261028a"

#: Both artifacts need the repository's L0 `fleet/` namespace package today,
#: through `fleet_mcp.registry`. Building them for real means saying so.
ALLOW = ("fleet",)


def build(kind: str, out: Path, *, version: str, **kwargs) -> Path:
    kwargs.setdefault("allow_external_in_repo", ALLOW)
    manifest, members = l2.manifest_for(kind, version, root=ROOT, **kwargs)
    return l2.write_artifact(manifest, members, out)


def rewrite(artifact: Path, out: Path, mutate) -> Path:
    """Rebuild an archive from a read one, applying `mutate(manifest, members)`."""
    read = l2.read_artifact(artifact)
    manifest = json.loads(json.dumps(read.manifest))
    members = dict(read.members)
    mutate(manifest, members)
    return l2.write_artifact(manifest, members, out)


def reseal(manifest: dict) -> None:
    """Recompute `source.tree_sha256` so a mutation is caught by the check it targets."""
    manifest["source"]["tree_sha256"] = l2._tree_digest(
        [{"path": name, "sha256": manifest["member_sha256"][name]}
         for name in manifest["members"]])


def declare(manifest: dict, name: str, data: bytes) -> None:
    """Add a member to the manifest the way the build would have."""
    manifest["members"] = sorted(set(manifest["members"]) | {name})
    manifest["member_sha256"][name] = l2._sha(data)


# --------------------------------------------------------------------------- #
# two artifacts, two versions
# --------------------------------------------------------------------------- #
def test_the_node_artifact_builds_and_verifies_as_a_node(tmp_path):
    path = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    result = l2.verify(l2.read_artifact(path), expect_kind=l2.L2_NODE)
    assert result["ok"] and result["kind"] == l2.L2_NODE
    assert result["version"] == NODE_VERSION


def test_the_hub_artifact_builds_and_verifies_as_a_hub(tmp_path):
    path = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)
    result = l2.verify(l2.read_artifact(path), expect_kind=l2.L2_HUB)
    assert result["ok"] and result["kind"] == l2.L2_HUB
    assert result["version"] == HUB_VERSION


def test_the_two_versions_are_independent(tmp_path):
    """A node upgrade must not drag a hub upgrade with it, and vice versa."""
    node = l2.read_artifact(build(l2.L2_NODE, tmp_path / "n.tar.gz", version=NODE_VERSION))
    hub = l2.read_artifact(build(l2.L2_HUB, tmp_path / "h.tar.gz", version=HUB_VERSION))
    assert node.version != hub.version
    assert node.manifest["kinds"] == [l2.L2_NODE]
    assert hub.manifest["kinds"] == [l2.L2_HUB]
    l2.verify(node, expect_kind=l2.L2_NODE)
    l2.verify(hub, expect_kind=l2.L2_HUB)


def test_a_build_is_byte_for_byte_reproducible(tmp_path):
    first = build(l2.L2_NODE, tmp_path / "a.tar.gz", version=NODE_VERSION)
    second = build(l2.L2_NODE, tmp_path / "b.tar.gz", version=NODE_VERSION)
    assert first.read_bytes() == second.read_bytes(), (
        "two builds of the same tree differ: the artifact cannot be compared "
        "byte for byte, so 'did this release change' would be unanswerable")


def test_the_artifact_carries_its_own_manifest(tmp_path):
    path = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)
    with tarfile.open(path, "r:gz") as tar:
        names = tar.getnames()
        manifest = json.load(tar.extractfile(l2.ARTIFACT_MANIFEST))
    assert l2.ARTIFACT_MANIFEST in names
    assert set(manifest["members"]) == set(names) - {l2.ARTIFACT_MANIFEST}


def test_the_member_list_is_the_shape_the_release_reader_consumes(tmp_path):
    """`scripts/release.py` does `set(manifest["members"])` against the files on
    disk. Records there would raise TypeError inside that comparison — a crash in
    the release tool instead of a refusal with a reason."""
    path = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    manifest = l2.read_artifact(path).manifest
    assert all(isinstance(item, str) for item in manifest["members"])
    assert isinstance(manifest["member_sha256"], dict)
    assert set(manifest["member_sha256"]) == set(manifest["members"])


# --------------------------------------------------------------------------- #
# the separation itself
# --------------------------------------------------------------------------- #
def test_the_node_artifact_carries_no_hub_entry(tmp_path):
    """The connector must not be able to serve MCP, tools or OAuth because it was
    imported — not even transitively."""
    path = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    names = set(l2.read_artifact(path).members)
    for forbidden in ("fleet_mcp/server.py", "fleet_mcp/tools.py", "fleet_mcp/oauth.py",
                      "fleet_mcp/owner_setup.py"):
        assert forbidden not in names
    assert not [name for name in names if name.startswith(("fleet_mcp/deploy/", "fleet_mcp/bin/"))]
    assert "fleet_mcp/node/connector.py" in names


def test_the_hub_artifact_carries_no_node(tmp_path):
    path = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)
    names = set(l2.read_artifact(path).members)
    assert "fleet_mcp/server.py" in names
    assert not [name for name in names if name.startswith("fleet_mcp/node/")]


def test_the_hub_artifact_carries_no_machine_state(tmp_path):
    """Credentials, bindings and units belong to the machine that has them."""
    path = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)
    names = set(l2.read_artifact(path).members)
    for suffix in (".local.yaml", ".service", ".timer", ".ps1", ".vbs", ".pem", ".key"):
        assert not [name for name in names if name.endswith(suffix)]
    assert not [name for name in names if name.startswith("bindings/")]


def test_the_shared_surface_is_pinned_and_exact(tmp_path):
    """Today's coupling is legal because it is recorded; tomorrow's is a failure."""
    path = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    manifest = l2.read_artifact(path).manifest
    assert manifest["l2"]["shared_surface"] == list(l2.NODE_SHARED_SURFACE)
    modules = {name[:-3].replace("/", ".").replace(".__init__", "")
               for name in l2.read_artifact(path).members if name.endswith(".py")}
    shared = modules - l2.NODE_EXCLUSIVE
    assert shared == set(l2.NODE_SHARED_SURFACE)


def test_a_new_hub_import_is_refused_by_name():
    """The pin has teeth: a module the connector does not need today is refused.

    Simulated on a synthetic tree so the test does not depend on the real
    connector growing the edge that would make it fail for the right reason and
    the wrong reason at once.
    """
    synthetic = l2.Closure(("fleet_mcp", "fleet_mcp.node", "fleet_mcp.node.connector",
                            "fleet_mcp.oauth"),
                           {"fleet_mcp/__init__.py": ROOT / "fleet_mcp/__init__.py",
                            "fleet_mcp/node/__init__.py": ROOT / "fleet_mcp/node/__init__.py",
                            "fleet_mcp/node/connector.py": ROOT / "fleet_mcp/node/connector.py",
                            "fleet_mcp/oauth.py": ROOT / "fleet_mcp/oauth.py"},
                           (), ("fleet",), {})
    with pytest.raises(l2.Refused, match="hub-side module"):
        l2._refuse_cross_layer(l2.L2_NODE, synthetic, {}, ROOT)


# --------------------------------------------------------------------------- #
# dependencies
# --------------------------------------------------------------------------- #
def test_third_party_dependencies_are_declared_and_measured(tmp_path):
    path = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    manifest = l2.read_artifact(path).manifest
    assert manifest["l2"]["requires"]["modules"] == ["yaml"], (
        "the connector imports PyYAML; a manifest that does not say so is an "
        "ImportError at first start on a clean machine")


def test_an_undeclared_import_is_refused(tmp_path):
    """A new import inside an allowed module still has to be declared.

    Slipped into `fleet_mcp.audit`, which the pin already allows, so the only
    thing that can catch it is the dependency check.
    """
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def mutate(manifest, members):
        name = "fleet_mcp/audit.py"
        members[name] = members[name] + b"\nimport numpy\n"
        manifest["member_sha256"][name] = l2._sha(members[name])
        reseal(manifest)

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="does not name its dependencies"):
        l2.verify(l2.read_artifact(broken))


def test_a_dependency_the_payload_does_not_carry_is_refused(tmp_path):
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def mutate(manifest, members):
        manifest["l2"]["depends_on"] = ["fleet_mcp.not_here"]

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="does not carry"):
        l2.verify(l2.read_artifact(broken))


def test_the_artifact_declares_that_it_is_not_standalone(tmp_path):
    """The measured truth: both payloads need the repository's L0 `fleet/`.

    `fleet_mcp.registry` does `from fleet.transport import ...` at import time and
    refuses with INTERNAL when it fails, so an artifact installed without that
    checkout cannot start. The manifest names the package instead of hiding the
    gap in a dependency line.
    """
    node = l2.read_artifact(build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION))
    assert node.manifest["l2"]["external_in_repo"] == ["fleet"]
    assert node.manifest["l2"]["standalone"] is False
    assert not [name for name in node.members if name.startswith("fleet/")]


def test_building_without_naming_the_checkout_dependency_is_refused(tmp_path):
    with pytest.raises(l2.Refused, match="leaves fleet_mcp"):
        l2.manifest_for(l2.L2_NODE, NODE_VERSION, root=ROOT)


# --------------------------------------------------------------------------- #
# refusals: tampering, drift, wrong kind, vendored schema, platforms
# --------------------------------------------------------------------------- #
def test_a_tampered_member_is_refused_at_read(tmp_path):
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def mutate(manifest, members):
        name = "fleet_mcp/node/connector.py"
        members[name] = members[name] + b"\n# tampered\n"

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="does not match the manifest"):
        l2.read_artifact(broken)


def test_an_undeclared_extra_member_is_refused(tmp_path):
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def mutate(manifest, members):
        members["fleet_mcp/sneaked.py"] = b"VALUE = 1\n"

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="undeclared"):
        l2.verify(l2.read_artifact(broken))


def test_a_member_the_payload_lost_is_refused(tmp_path):
    """The manifest still promises the file; the archive does not have it."""
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def mutate(manifest, members):
        members.pop("fleet_mcp/version.py")

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="missing"):
        l2.verify(l2.read_artifact(broken))


def test_the_wrong_kind_is_refused(tmp_path):
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    with pytest.raises(l2.Refused, match="was expected"):
        l2.verify(l2.read_artifact(node), expect_kind=l2.L2_HUB)


def test_an_unknown_kind_is_refused(tmp_path):
    hub = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)

    def mutate(manifest, members):
        manifest["kinds"] = ["plugin"]

    broken = rewrite(hub, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="not an L2 artifact kind"):
        l2.verify(l2.read_artifact(broken))


def test_a_version_that_drifted_from_the_build_is_refused(tmp_path):
    hub = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)
    with pytest.raises(l2.Refused, match="version drift"):
        l2.verify(l2.read_artifact(hub), expect_version="9.9.9+other")


def test_a_manifest_schema_drift_is_refused(tmp_path):
    hub = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)

    def mutate(manifest, members):
        manifest["schema"] = "serveros/artifact.v2"

    broken = rewrite(hub, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="schema"):
        l2.verify(l2.read_artifact(broken))


def test_a_vendored_cloudcli_schema_is_refused(tmp_path):
    """The provider forwards the running CLI's schema; a copy goes stale silently."""
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    vendored = json.dumps({"tools": [{"name": "run_get",
                                      "inputSchema": {"type": "object"}}]}).encode()

    def mutate(manifest, members):
        members["fleet_mcp/cloudcli_schema.json"] = vendored
        declare(manifest, "fleet_mcp/cloudcli_schema.json", vendored)
        reseal(manifest)

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="vendored CloudCLI"):
        l2.verify(l2.read_artifact(broken))


def test_a_platform_the_kind_does_not_support_is_refused(tmp_path):
    with pytest.raises(l2.Refused, match="does not run on"):
        l2.manifest_for(l2.L2_HUB, HUB_VERSION, root=ROOT, platforms=("linux", "windows"),
                        allow_external_in_repo=ALLOW)


def test_a_payload_missing_its_required_member_is_refused(tmp_path):
    hub = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)

    def mutate(manifest, members):
        dropped = "fleet_mcp/server.py"
        members.pop(dropped, None)
        manifest["members"].remove(dropped)
        del manifest["member_sha256"][dropped]
        reseal(manifest)

    broken = rewrite(hub, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="required member"):
        l2.verify(l2.read_artifact(broken))


def test_an_unsafe_member_name_is_refused(tmp_path):
    path = tmp_path / "evil.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, data in ((l2.ARTIFACT_MANIFEST, json.dumps({"kinds": ["l2-node"]}).encode()),
                           ("../escape.py", b"x")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    with pytest.raises(l2.Refused, match="unsafe member name"):
        l2.read_artifact(path)


# --------------------------------------------------------------------------- #
# rollback: two versions, read back, nothing installed
# --------------------------------------------------------------------------- #
def test_a_rollback_target_is_the_previous_artifact_and_still_verifies(tmp_path):
    old = build(l2.L2_NODE, tmp_path / "node-1.tar.gz", version="1.3.0+n20261020a")
    new = build(l2.L2_NODE, tmp_path / "node-2.tar.gz", version=NODE_VERSION)
    report = l2.pair(old, new, kind=l2.L2_NODE)
    assert report == {"kind": l2.L2_NODE, "from": "1.3.0+n20261020a",
                      "to": NODE_VERSION, "rollback_to": "1.3.0+n20261020a",
                      "rollback_available": True}
    restored = l2.rollback_target(old, kind=l2.L2_NODE)
    assert restored.version == "1.3.0+n20261020a"


def test_rollback_refuses_a_target_of_another_kind(tmp_path):
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    with pytest.raises(l2.Refused, match="was expected"):
        l2.rollback_target(node, kind=l2.L2_HUB)


def test_a_node_and_a_hub_cannot_be_rolled_back_into_each_other(tmp_path):
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    hub = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)
    with pytest.raises(l2.Refused, match="cannot roll"):
        l2.pair(node, hub)


# --------------------------------------------------------------------------- #
# contract with the release tool, and hygiene of this module
# --------------------------------------------------------------------------- #
def test_the_manifest_name_and_schema_match_the_release_reader():
    release = RELEASE
    assert l2.ARTIFACT_MANIFEST == release.ARTIFACT_MANIFEST
    assert l2.ARTIFACT_MANIFEST_SCHEMA == release.ARTIFACT_MANIFEST_SCHEMA


def test_the_new_kinds_are_either_unknown_to_release_or_fully_registered():
    """The handoff in HANDOFF-419.md is a state, not a promise.

    Before it lands, `release.py` must not know these kinds — an unknown kind is
    refused by name. After it lands, the kinds must carry required members, so the
    release path cannot fall through to the node verifier. Both states are legal;
    a kind that is known but has no requirements is not.
    """
    release = RELEASE
    for kind in l2.L2_KINDS:
        if kind in release.ARTIFACT_KINDS:
            assert release.REQUIRED_MEMBERS.get(kind), (
                f"{kind} is registered with the release tool but has no required "
                f"members; it would be verified as a node runtime")


def test_the_release_tool_refuses_our_manifest_until_the_handoff_lands():
    """Documents the current state so it cannot change silently in either way."""
    release = RELEASE
    kinds = set(release.ARTIFACT_KINDS)
    pending = sorted(set(l2.L2_KINDS) - kinds)
    assert pending in (sorted(l2.L2_KINDS), []), (
        "the handoff landed for some kinds and not others")


def _stage(tmp_path: Path, artifact: Path) -> Path:
    """Lay an archive out as a revision directory, the way `release.py` reads one."""
    read = l2.read_artifact(artifact)
    stage = tmp_path / "rev"
    stage.mkdir()
    for name, data in read.members.items():
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (stage / l2.ARTIFACT_MANIFEST).write_text(
        json.dumps(read.manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return stage


def test_the_release_reader_refuses_our_kinds_by_name_until_the_handoff_lands(tmp_path):
    """Pins the state HANDOFF-419.md describes, so it cannot drift silently."""
    release = RELEASE
    if all(kind in release.ARTIFACT_KINDS for kind in l2.L2_KINDS):
        pytest.skip("HANDOFF-419 has landed; see the parity test below")
    stage = _stage(tmp_path, build(l2.L2_NODE, tmp_path / "n.tar.gz", version=NODE_VERSION))
    with pytest.raises(SystemExit, match="unknown artifact kind"):
        release.read_artifact_manifest(stage)


@pytest.mark.skipif(
    not all(kind in RELEASE.ARTIFACT_KINDS for kind in l2.L2_KINDS),
    reason="HANDOFF-419 has not landed: scripts/release.py has no l2 verifier yet")
def test_release_and_l2_release_agree_on_a_staged_directory(tmp_path):
    """Two verifiers, one verdict. The day the handoff lands, this stops skipping."""
    release = RELEASE
    stage = _stage(tmp_path, build(l2.L2_NODE, tmp_path / "n.tar.gz", version=NODE_VERSION))
    assert release.read_artifact_manifest(stage)["kinds"] == [l2.L2_NODE]
    report = release.verify_l2_layer(stage, kind=l2.L2_NODE)
    assert report["ok"], report


def test_the_module_makes_no_network_process_or_credential_calls():
    tree = ast.parse((PACKAGE / "l2_release.py").read_text(encoding="utf-8"))
    banned = {"subprocess", "socket", "urllib", "http", "requests", "ssl",
              "shutil", "os.system", "os.popen"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & banned), f"release build imports {sorted(imported & banned)}"


def test_the_artifact_contains_no_absolute_paths(tmp_path):
    for kind in l2.L2_KINDS:
        path = build(kind, tmp_path / f"{kind}.tar.gz", version=NODE_VERSION)
        for name in l2.read_artifact(path).members:
            assert not name.startswith("/") and ".." not in Path(name).parts
