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
    """Load a module by path, reading `sys.modules` before writing to it.

    `tests/test_loader_identity.py` bans registering a module under a name
    without first asking what that name already means: the name is process-wide,
    so the second writer takes it from the first and each copy carries its own
    classes — a `Refused` raised as one class and caught as another. The
    `sys.modules.pop` on failure is the other half: a module that raised during
    `exec_module` must not be left behind half-initialised for the next caller
    to import.
    """
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
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
    """Recompute what the build would have written, so a mutation is caught by the
    check it targets and not by a stale tree digest.

    `ARTIFACT.json` is a member with no digest of its own, so it is out of both
    the tree hash and the module count; a manifest that is its own member and its
    own digest is the one shape a file cannot have.
    """
    digests = {name: digest for name, digest in manifest["member_sha256"].items()}
    manifest["source"]["tree_sha256"] = l2._tree_digest(
        [{"path": name, "sha256": digest} for name, digest in sorted(digests.items())])
    manifest["source"]["module_count"] = len(digests)


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
    """A hub upgrade must not change one byte of the node artifact.

    Naming two different versions proves nothing — it was true by construction.
    What D7 promises is that upgrading one side leaves the other side's release
    alone, so this builds the hub twice at two versions and compares the node's
    bytes across both, then verifies the node artifact on its own.
    """
    node_first = build(l2.L2_NODE, tmp_path / "n1.tar.gz", version=NODE_VERSION)
    hub_first = build(l2.L2_HUB, tmp_path / "h1.tar.gz", version=HUB_VERSION)
    hub_second = build(l2.L2_HUB, tmp_path / "h2.tar.gz", version="2.2.0+h20261028b")
    node_second = build(l2.L2_NODE, tmp_path / "n2.tar.gz", version=NODE_VERSION)

    assert hub_first.read_bytes() != hub_second.read_bytes()
    assert node_first.read_bytes() == node_second.read_bytes(), (
        "moving the hub's version moved the node artifact; the two releases are "
        "still one release")
    assert l2.read_artifact(node_second).version == NODE_VERSION
    l2.verify(l2.read_artifact(node_first), expect_kind=l2.L2_NODE)
    l2.verify(l2.read_artifact(hub_second), expect_kind=l2.L2_HUB)


def test_a_build_is_byte_for_byte_reproducible(tmp_path):
    first = build(l2.L2_NODE, tmp_path / "a.tar.gz", version=NODE_VERSION)
    second = build(l2.L2_NODE, tmp_path / "b.tar.gz", version=NODE_VERSION)
    assert first.read_bytes() == second.read_bytes(), (
        "two builds of the same tree differ: the artifact cannot be compared "
        "byte for byte, so 'did this release change' would be unanswerable")


def test_the_artifact_names_its_own_manifest_as_a_member(tmp_path):
    """`scripts/build.py` lists the manifest in `members`, and it has to.

    `scripts/release.py:739` compares `members` against the files on disk with
    only `RELEASE.json` exempted, so an `ARTIFACT.json` that is present but
    unlisted is reported as "a file present that the build did not produce" — the
    release path refusing our own artifact for carrying its own manifest.
    """
    path = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)
    with tarfile.open(path, "r:gz") as tar:
        names = tar.getnames()
        manifest = json.load(tar.extractfile(l2.ARTIFACT_MANIFEST))
    assert l2.ARTIFACT_MANIFEST in names
    assert set(manifest["members"]) == set(names)
    assert l2.ARTIFACT_MANIFEST not in manifest["member_sha256"], (
        "the manifest cannot carry its own digest")


def test_the_member_list_is_the_shape_the_release_reader_consumes(tmp_path):
    """`scripts/release.py` does `set(manifest["members"])` against the files on
    disk. Records there would raise TypeError inside that comparison — a crash in
    the release tool instead of a refusal with a reason.

    Asserted against the release tool's own reader, not against my memory of it:
    every member the payload has is either listed or is `RELEASE.json`, which the
    release path writes itself.
    """
    path = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    manifest = l2.read_artifact(path).manifest
    assert all(isinstance(item, str) for item in manifest["members"])
    assert isinstance(manifest["member_sha256"], dict)
    assert set(manifest["member_sha256"]) == set(manifest["members"]) - {l2.ARTIFACT_MANIFEST}
    stage = _stage(tmp_path, path)
    on_disk = {entry.relative_to(stage).as_posix()
               for entry in stage.rglob("*") if entry.is_file()}
    assert not (on_disk - set(manifest["members"]) - {RELEASE.TREE_MANIFEST}), (
        "the release tool would report these as files the build did not produce")


def test_extract_lays_out_the_directory_the_release_path_stages(tmp_path):
    """`release.py` stages a directory (`shutil.copytree`), not an archive, so the
    artifact has to become one; the files written must be exactly the ones the
    manifest names, or the release path is staging something else."""
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    read = l2.read_artifact(node)
    out = l2.extract(node, tmp_path / "rev")
    on_disk = {entry.relative_to(out).as_posix()
               for entry in out.rglob("*") if entry.is_file()}
    assert on_disk == set(read.members) | {l2.ARTIFACT_MANIFEST}
    assert on_disk <= set(read.manifest["members"]) | {RELEASE.TREE_MANIFEST}
    with pytest.raises(l2.Refused, match="already has contents"):
        l2.extract(node, out)


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

    Called with names, which is what both the build and the verifier pass, so the
    test cannot pass against one copy of the guard and miss the other.
    """
    with pytest.raises(l2.Refused, match="new hub coupling"):
        l2._refuse_node_members({"fleet_mcp/__init__.py",
                                 "fleet_mcp/node/__init__.py",
                                 "fleet_mcp/report.py"})


def test_a_hub_module_in_the_node_artifact_is_refused_by_name():
    with pytest.raises(l2.Refused, match="hub-side module"):
        l2._refuse_node_members({"fleet_mcp/__init__.py", "fleet_mcp/server.py"})


def test_the_verifier_refuses_a_node_payload_that_carries_the_hub_entry(tmp_path):
    """The build refusing is half of it. A payload that did not come from this
    build must be refused by `verify` as well, or the guard protects the thing
    that cannot lie and not the thing that can.

    `server.py` is added to the manifest the way a build would have declared it,
    so the layer guard — not the undeclared-file check — is what has to fire.
    """
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    smuggled = (ROOT / "fleet_mcp" / "server.py").read_bytes()

    def mutate(manifest, members):
        members["fleet_mcp/server.py"] = smuggled
        declare(manifest, "fleet_mcp/server.py", smuggled)
        reseal(manifest)

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="hub-side module"):
        l2.verify(l2.read_artifact(broken))


def test_the_verifier_refuses_a_hub_payload_that_carries_the_node(tmp_path):
    """A hub release that can install a node is a hub release with a second,
    unreviewed deployment path."""
    hub = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)
    smuggled = (ROOT / "fleet_mcp" / "node" / "connector.py").read_bytes()

    def mutate(manifest, members):
        members["fleet_mcp/node/connector.py"] = smuggled
        declare(manifest, "fleet_mcp/node/connector.py", smuggled)
        reseal(manifest)

    broken = rewrite(hub, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="device-side"):
        l2.verify(l2.read_artifact(broken))


def test_the_verifier_refuses_a_hub_payload_that_carries_a_binding(tmp_path):
    hub = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)
    binding = b"device: node-17\n"

    def mutate(manifest, members):
        members["bindings/node-17.yaml"] = binding
        declare(manifest, "bindings/node-17.yaml", binding)
        reseal(manifest)

    broken = rewrite(hub, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="machine-specific"):
        l2.verify(l2.read_artifact(broken))


def test_a_second_spelling_of_a_member_name_is_refused(tmp_path):
    """`fleet_mcp/./node/connector.py` is one file to the filesystem and a
    different string to a deny-list written against `fleet_mcp/node/`.

    Written by hand rather than through `write_artifact`, because the point is a
    payload that did not come from this build: the archive is the only place the
    two spellings can both be legal.
    """
    path = tmp_path / "smuggled.tar.gz"
    smuggled = (ROOT / "fleet_mcp" / "node" / "connector.py").read_bytes()
    with tarfile.open(path, "w:gz") as tar:
        for name, data in (("fleet_mcp/./node/connector.py", smuggled),
                           (l2.ARTIFACT_MANIFEST,
                            json.dumps({"kinds": [l2.L2_HUB]}).encode())):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    with pytest.raises(l2.Refused, match="non-canonical member name"):
        l2.read_artifact(path)


def test_bytecode_in_a_payload_is_refused(tmp_path):
    """`importlib.machinery.BYTECODE_SUFFIXES` is `['.pyc']`: a sourceless
    `fleet_mcp/server.pyc` imports where `server.py` would not."""
    with pytest.raises(l2.Refused, match="ships bytecode"):
        l2._refuse_node_members({"fleet_mcp/__init__.py", "fleet_mcp/server.pyc"})
    with pytest.raises(l2.Refused, match="ships bytecode"):
        l2._refuse_hub_members({"fleet_mcp/server.py", "fleet_mcp/__pycache__/x.pyo"})


def test_a_duplicated_member_is_refused(tmp_path):
    path = tmp_path / "twice.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for data in (b"ONE = 1\n", b"ONE = 2\n"):
            info = tarfile.TarInfo("fleet_mcp/version.py")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        manifest = json.dumps({"kinds": [l2.L2_NODE]}).encode()
        info = tarfile.TarInfo(l2.ARTIFACT_MANIFEST)
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
    with pytest.raises(l2.Refused, match="more than once"):
        l2.read_artifact(path)


def test_a_link_in_a_payload_is_refused(tmp_path):
    """The manifest describes bytes. A symlink is verified as its target and
    installed as a link to wherever it points on the machine."""
    path = tmp_path / "link.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo("fleet_mcp/versions.py")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
        manifest = json.dumps({"kinds": [l2.L2_NODE]}).encode()
        entry = tarfile.TarInfo(l2.ARTIFACT_MANIFEST)
        entry.size = len(manifest)
        tar.addfile(entry, io.BytesIO(manifest))
    with pytest.raises(l2.Refused, match="not regular files"):
        l2.read_artifact(path)


def test_a_node_private_module_needs_no_pin_and_a_hub_module_does(tmp_path):
    """The pin is what the node borrows from the hub. A module under
    `fleet_mcp.node` is the node's own, so adding one is not hub coupling — and a
    guard that made every node-side addition a build failure would be an argument
    against the connector growing at all."""
    names = set(l2.read_artifact(
        build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)).members)
    l2._refuse_node_members(names | {"fleet_mcp/node/telemetry.py"})
    with pytest.raises(l2.Refused, match="new hub coupling"):
        l2._refuse_node_members(names | {"fleet_mcp/report.py"})


def test_a_pin_entry_the_closure_no_longer_reaches_is_refused(tmp_path):
    """A cut edge must delete its pin entry in the same change.

    Otherwise `NODE_SHARED_SURFACE` stops being a measurement and becomes a claim
    about a coupling that is gone — and the next reader takes it as the current
    shape.
    """
    names = set(l2.read_artifact(
        build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)).members)
    reachable = {l2._module_of(name) for name in names}
    pinned = [module for module in l2.NODE_SHARED_SURFACE if module in reachable]
    assert pinned, "no pin entry is reachable; this control proves nothing"
    with pytest.raises(l2.Refused, match="no longer reaches"):
        l2._refuse_node_members(
            {name for name in names if l2._module_of(name) != pinned[0]})



def test_a_token_named_module_is_software_and_a_token_file_is_not():
    with pytest.raises(l2.Refused, match="credential-shaped"):
        l2._refuse_hub_members({"fleet_mcp/tokens.json"})
    l2._refuse_hub_members({"fleet_mcp/token_cache.py"})       # software, allowed
    with pytest.raises(l2.Refused, match="machine-specific"):
        l2._refuse_hub_members({"fleet_mcp/fleet.local.yaml"})


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


def test_the_build_cannot_narrow_platforms_below_what_the_kind_ships_to(tmp_path):
    """A build that emits what its own verifier refuses is a build nobody can use,
    so the verifier's platform rule is applied at build time too."""
    with pytest.raises(l2.Refused, match="must declare every platform"):
        l2.manifest_for(l2.L2_NODE, NODE_VERSION, root=ROOT, platforms=("linux",),
                        allow_external_in_repo=ALLOW)


def test_a_manifest_that_under_declares_platforms_is_refused(tmp_path):
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def mutate(manifest, members):
        manifest["l2"]["platforms"] = ["linux"]

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="must declare"):
        l2.verify(l2.read_artifact(broken))


def test_an_internal_import_the_payload_does_not_carry_is_refused(tmp_path):
    """`from .report import Report` promises `fleet_mcp/report.py` is in this
    artifact. The member list only says what IS here, so nothing else checks it."""
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def mutate(manifest, members):
        name = "fleet_mcp/audit.py"
        members[name] = members[name] + b"\nfrom .report import Report\n"
        manifest["member_sha256"][name] = l2._sha(members[name])
        reseal(manifest)

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="does not carry"):
        l2.verify(l2.read_artifact(broken))


def test_a_manifest_that_describes_itself_two_ways_is_refused(tmp_path):
    """`standalone` is derived from the dependency lists. A manifest where the
    flag and the lists disagree is the exact thing a verifier exists to catch."""
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def mutate(manifest, members):
        manifest["l2"]["standalone"] = True

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="describes itself two ways"):
        l2.verify(l2.read_artifact(broken))


def test_a_hub_artifact_cannot_claim_the_nodes_shared_surface(tmp_path):
    hub = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)

    def mutate(manifest, members):
        manifest["l2"]["shared_surface"] = list(l2.NODE_SHARED_SURFACE)

    broken = rewrite(hub, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="hub artifact lists a shared surface"):
        l2.verify(l2.read_artifact(broken))


def test_a_hub_artifact_cannot_provide_the_nodes_capability(tmp_path):
    hub = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)

    def mutate(manifest, members):
        manifest["l2"]["provides"] = {"node-connector": True}

    broken = rewrite(hub, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="must provide exactly"):
        l2.verify(l2.read_artifact(broken))


def test_depends_on_accepts_a_package_as_well_as_a_module(tmp_path):
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def mutate(manifest, members):
        manifest["l2"]["depends_on"] = ["fleet_mcp"]
        manifest["l2"]["standalone"] = False

    rewritten = rewrite(node, tmp_path / "ok.tar.gz", mutate)
    assert l2.verify(l2.read_artifact(rewritten))["ok"]


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
    with pytest.raises(l2.Refused, match="non-canonical member name"):
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
    """What this checks: a kind the release path knows must carry required members.

    `verify_revision` falls through to `verify_node_runtime` for a registered kind
    it does not name, so registration without required members is a registered
    kind that asks an L2 payload for `bin/serverosd`. Whether the kinds are
    registered at all is the other test's question, not this one's.
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
    """A packaging tool that can shell out or open a socket is a packaging tool
    that can do either while claiming to be offline.

    `os.system` is not an import, so the import scan never sees it: the attribute
    calls are checked separately, or the ban covers only the way it is spelled
    least often.
    """
    tree = ast.parse((PACKAGE / "l2_release.py").read_text(encoding="utf-8"))
    banned = {"subprocess", "socket", "urllib", "http", "requests", "ssl",
              "shutil", "telnetlib", "ftplib", "smtplib"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & banned), f"release build imports {sorted(imported & banned)}"
    calls = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        holder = node.func.value
        if isinstance(holder, ast.Name) and holder.id in {"os", "subprocess", "socket",
                                                          "shutil"}:
            calls.add(f"{holder.id}.{node.func.attr}")
        elif isinstance(holder, ast.Attribute) and holder.attr in {"system", "popen"}:
            calls.add(node.func.attr)
    assert not calls, f"release build calls {sorted(calls)}"


def test_the_artifact_contains_no_absolute_paths(tmp_path):
    for kind in l2.L2_KINDS:
        path = build(kind, tmp_path / f"{kind}.tar.gz", version=NODE_VERSION)
        for name in l2.read_artifact(path).members:
            assert not name.startswith("/") and ".." not in Path(name).parts
