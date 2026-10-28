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
    """`standalone` is the legacy spelling of `runtime_standalone`.

    A manifest where the two spellings disagree is the exact thing a verifier
    exists to catch: `standalone: true` used to mean "not external, not
    depends_on", and a reader that believed it would install a payload that has
    just been measured to die on the first fleet call.
    """
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def mutate(manifest, members):
        manifest["l2"]["standalone"] = True

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="disagrees with l2.runtime_standalone"):
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


# --------------------------------------------------------------------------- #
# import_standalone vs runtime_standalone: measured, in a fresh interpreter
# --------------------------------------------------------------------------- #
def _mutate_member(path: Path, out: Path, name: str, transform) -> Path:
    """Change one member's bytes and keep the manifest honest about them."""
    def mutate(manifest, members):
        members[name] = transform(members[name])
        manifest["member_sha256"][name] = l2._sha(members[name])
        reseal(manifest)
    return rewrite(path, out, mutate)


def test_the_node_entrypoint_imports_with_the_repository_out_of_reach(tmp_path):
    """The node imports without a checkout; that is a fact, not a compliment.

    Measured in a separate interpreter (`-I`, cwd elsewhere, any `sys.path` entry
    that can see a `fleet/` dropped), because the same measurement in this process
    would answer "yes" for a payload that found `fleet` already in `sys.modules`.
    """
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    measured = l2.measure_standalone(l2.read_artifact(node).members,
                                     entry=l2.ENTRY[l2.L2_NODE])
    assert measured.entry_imports is True
    assert dict(measured.probe)[l2.ENTRY[l2.L2_NODE]] == "imports"


def test_the_hub_entrypoint_does_not_import_with_the_repository_out_of_reach(tmp_path):
    """The hub's import chain is eager, so it dies at `registry` immediately."""
    hub = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)
    measured = l2.measure_standalone(l2.read_artifact(hub).members,
                                     entry=l2.ENTRY[l2.L2_HUB])
    assert measured.entry_imports is False
    assert dict(measured.probe)[l2.ENTRY[l2.L2_HUB]] == "FleetRefusal"


def test_the_node_runtime_path_needs_the_repository_even_though_it_imports(tmp_path):
    """This is the whole point of two flags instead of one.

    `connector.py` defers `from ..fleet import FleetService` into three function
    bodies (1124, 1317, 1384), so the artifact *loads* on a device with no checkout
    and then fails the first time the runtime needs a fleet — after the process is
    up, inside an agent turn, with nothing in the release notes about it. One
    boolean cannot say that; the pair can.
    """
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    measured = l2.measure_standalone(l2.read_artifact(node).members,
                                     entry=l2.ENTRY[l2.L2_NODE])
    probe = dict(measured.probe)
    assert measured.entry_imports is True, "the premise: the node does import"
    assert measured.runtime_imports is False, "and it still cannot run alone"
    assert probe["fleet_mcp.registry"] == "FleetRefusal"
    assert measured.entry_imports != measured.runtime_imports, (
        "a single `standalone` flag collapses exactly this difference")


def test_the_two_standalone_flags_are_separate_facts_in_the_manifest(tmp_path):
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    hub = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)
    node_block = l2.read_artifact(node).manifest["l2"]
    hub_block = l2.read_artifact(hub).manifest["l2"]

    assert (node_block["import_standalone"], node_block["runtime_standalone"]) \
        == (True, False)
    assert (hub_block["import_standalone"], hub_block["runtime_standalone"]) \
        == (False, False)
    for block in (node_block, hub_block):
        assert block["standalone"] is block["runtime_standalone"], (
            "`standalone` is the legacy spelling of the strong claim; if it "
            "followed the import instead, a payload that cannot run would read as "
            "standalone")
        assert block["install_requires_repo"] == {"fleet": ["fleet_mcp.registry"]}
        assert block["install"]["independently_installable"] is False
        assert block["install"]["blocked_by"], (
            "an artifact that needs a checkout must name what blocks it")
        assert block["probe"]["fleet_mcp.registry"] == "FleetRefusal"


def test_a_manifest_that_understates_import_standalone_is_refused(tmp_path):
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def mutate(manifest, members):
        manifest["l2"]["import_standalone"] = False

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="l2.import_standalone says False"):
        l2.verify(l2.read_artifact(broken))


def test_a_manifest_that_claims_runtime_standalone_is_refused(tmp_path):
    """Both spellings flipped: the measurement is what refuses it, not the alias."""
    hub = build(l2.L2_HUB, tmp_path / "hub.tar.gz", version=HUB_VERSION)

    def mutate(manifest, members):
        manifest["l2"]["runtime_standalone"] = True
        manifest["l2"]["standalone"] = True

    broken = rewrite(hub, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="l2.runtime_standalone says True"):
        l2.verify(l2.read_artifact(broken))


def test_a_payload_whose_probe_record_is_not_its_probe_is_refused(tmp_path):
    """The evidence travels with the claim, so the two are compared."""
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def mutate(manifest, members):
        manifest["l2"]["probe"]["fleet_mcp.registry"] = "imports"

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="l2.probe records"):
        l2.verify(l2.read_artifact(broken))


def test_a_non_standalone_artifact_cannot_call_itself_independently_installable(
        tmp_path):
    """`allow_external_in_repo` buys a build, never an install.

    The build flag exists so the packaging work can proceed against today's
    coupling. It is not a statement that the coupling is fine, and the manifest
    must not be able to turn it into one.
    """
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def mutate(manifest, members):
        manifest["l2"]["install"]["independently_installable"] = True

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused,
                       match="not made installable by a manifest field"):
        l2.verify(l2.read_artifact(broken))


def test_a_payload_that_loses_its_blocked_by_list_is_refused(tmp_path):
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def mutate(manifest, members):
        manifest["l2"]["install"]["blocked_by"] = []

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="install.blocked_by is \\[\\]"):
        l2.verify(l2.read_artifact(broken))


def test_an_allowance_the_closure_does_not_need_is_refused(tmp_path):
    """A named permission is a permission for one dependency, not a standing one."""
    with pytest.raises(l2.Refused, match="allow_external_in_repo names \\['runtime'\\]"):
        build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION,
              allow_external_in_repo=("fleet", "runtime"))


def test_the_dependency_on_the_repository_is_named_not_merely_permitted(tmp_path):
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    block = l2.read_artifact(node).manifest["l2"]
    assert block["install_requires_repo"] == {"fleet": ["fleet_mcp.registry"]}
    assert block["install"]["requires_repository"] == block["install_requires_repo"]
    assert any("fleet_mcp.registry -> fleet.transport (7 symbols)"
               in line for line in block["install"]["blocked_by"]), \
        block["install"]["blocked_by"]


# --------------------------------------------------------------------------- #
# the pinned edge: one holder, one module, exactly seven names
# --------------------------------------------------------------------------- #
def test_the_pin_matches_the_source_it_was_measured_from():
    """The constant and `fleet_mcp/registry.py:33` are the same statement."""
    tree = ast.parse((ROOT / "fleet_mcp" / "registry.py").read_text(encoding="utf-8"))
    imports = [node for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom) and node.module == "fleet.transport"]
    assert len(imports) == 1, (
        "registry.py has one `from fleet.transport import (...)`; two would be two "
        "edges the pin does not describe")
    names = sorted(alias.name for alias in imports[0].names)
    assert names == sorted(l2.EXTERNAL_EDGES["fleet"]["symbols"])
    assert len(names) == 7, names
    assert imports[0].level == 0, "a relative import would resolve inside fleet_mcp"


def test_the_pinned_edge_is_the_only_way_the_two_payloads_leave_their_package(tmp_path):
    """Out of the repository, not out of the package: `yaml` is a wheel and is not
    an edge this pin is about."""
    for kind, version in ((l2.L2_NODE, NODE_VERSION), (l2.L2_HUB, HUB_VERSION)):
        artifact = l2.read_artifact(build(kind, tmp_path / f"{kind}.tar.gz",
                                          version=version))
        block = artifact.manifest["l2"]
        holders = l2._refuse_external_edges(
            kind, artifact.members,
            third_party=block["requires"]["modules"])
        assert holders == {"fleet": ["fleet_mcp.registry"]}, holders
        edges = l2._external_edges(artifact.members)["fleet"]
        assert {edge["module"] for edge in edges} == {"fleet.transport"}
        assert [len(edge["symbols"]) for edge in edges] == [7]
        assert {edge["form"] for edge in edges} == {l2.EDGE_FORM}


def test_an_eight_name_import_from_the_pinned_module_is_refused(tmp_path):
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def add_one(data: bytes) -> bytes:
        # No line-ending assumption: `registry.py` is CRLF in a Windows checkout
        # and LF in the blob, and a test that only works on one of them proves
        # something about this worktree rather than about the guard.
        return data.replace(b"canonical_level,",
                            b"canonical_level,\n        DeviceCertificate,", 1)

    broken = _mutate_member(node, tmp_path / "broken.tar.gz",
                            "fleet_mcp/registry.py", add_one)
    with pytest.raises(l2.Refused, match="imports 8 name"):
        l2.verify(l2.read_artifact(broken))


def test_a_second_module_entering_the_pinned_edge_is_refused(tmp_path):
    """The pin is a count. A second holder is how seven names become twenty."""
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def mutate(manifest, members):
        name = "fleet_mcp/privacy.py"
        members[name] = members[name] + (
            b"\nfrom fleet.transport import Device\n")
        declare(manifest, name, members[name])
        reseal(manifest)

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="the pin says exactly one holder"):
        l2.verify(l2.read_artifact(broken))


def test_a_wider_import_form_of_the_pinned_edge_is_refused(tmp_path):
    """`import fleet.registry` and `from fleet import transport` are not the pin.

    Both reach *more* than one module's seven names, and neither names what it
    takes — so neither is something a manifest can describe.
    """
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    for addition, expected in ((b"\nimport fleet.registry\n", "`import fleet.registry`"),
                               (b"\nfrom fleet import transport\n",
                                "`from-package-import-names fleet`")):
        broken = Path(tmp_path) / f"broken{len(addition)}.tar.gz"
        _mutate_member(node, broken, "fleet_mcp/registry.py",
                       lambda data, extra=addition: data + extra)
        with pytest.raises(l2.Refused, match="enters fleet as") as caught:
            l2.verify(l2.read_artifact(broken))
        assert expected in str(caught.value), str(caught.value)


def test_a_repository_package_the_manifest_does_not_mention_is_refused(tmp_path):
    """`runtime/` is not `yaml`: an artifact that imports it depends on this
    checkout, and the manifest has to say so before the pin can be asked about it.
    """
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)

    def mutate(manifest, members):
        name = "fleet_mcp/version.py"
        members[name] = members[name] + b"\nimport runtime.agent_control_store\n"
        manifest["member_sha256"][name] = l2._sha(members[name])
        reseal(manifest)

    broken = rewrite(node, tmp_path / "broken.tar.gz", mutate)
    with pytest.raises(l2.Refused, match="does not name its dependencies"):
        l2.verify(l2.read_artifact(broken))


def test_an_unpinned_in_repo_package_is_refused_by_the_edge_check():
    """The build measures `third_party` from the source tree, so a repository
    package can never be listed there. This asserts the mechanism with the set the
    build would pass — the place where the distinction is still knowable.
    """
    members = {"fleet_mcp/__init__.py": b'"""x."""\n',
               "fleet_mcp/version.py":
                   b"from runtime.agent_control_store import ControlStore\n"}
    with pytest.raises(l2.Refused,
                       match="neither a declared third-party module nor a pinned"):
        l2._refuse_external_edges(l2.L2_NODE, members, third_party=("yaml",))
    # And the same payload is accepted the moment the name is pinned, which is what
    # makes the refusal a pin and not a blacklist: the guard is about an edge
    # nobody decided on, not about a package somebody dislikes.
    pinned = dict(l2.EXTERNAL_EDGES)
    try:
        l2.EXTERNAL_EDGES["runtime"] = {"module": "runtime.agent_control_store",
                                        "holder": "fleet_mcp.version",
                                        "symbols": ("ControlStore",)}
        assert l2._refuse_external_edges(l2.L2_NODE, members,
                                        third_party=("yaml",)) == {
            "runtime": ["fleet_mcp.version"]}
    finally:
        l2.EXTERNAL_EDGES.clear()
        l2.EXTERNAL_EDGES.update(pinned)


# --------------------------------------------------------------------------- #
# the 17 pinned modules are reachable, not just present
# --------------------------------------------------------------------------- #
def test_all_seventeen_pinned_modules_are_reachable_from_the_entrypoint(tmp_path):
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    members = l2.read_artifact(node).members
    reachable = l2._reachable_from(l2.ENTRY[l2.L2_NODE], members)
    assert len(l2.NODE_SHARED_SURFACE) == 17
    assert set(l2.NODE_SHARED_SURFACE) <= reachable
    assert set(l2.NODE_EXCLUSIVE) <= reachable
    assert len(reachable) == 30, sorted(reachable)


def test_a_pin_entry_that_is_present_but_unreachable_is_refused(tmp_path):
    """Present and reachable are different claims; this is the second one.

    Blanking `config.py` drops the imports that reach five pinned modules without
    removing a single file, so every presence check still passes and only the
    reachability walk notices.
    """
    node = build(l2.L2_NODE, tmp_path / "node.tar.gz", version=NODE_VERSION)
    broken = _mutate_member(node, tmp_path / "broken.tar.gz", "fleet_mcp/config.py",
                            lambda data: b'""""""\n')
    with pytest.raises(l2.Refused, match="carries but never reaches"):
        l2.verify(l2.read_artifact(broken))


def test_deleting_the_lazy_imports_on_a_copy_flips_the_packaging_flags(tmp_path):
    """The **packaging** premise of edge A, measured on a copy: three import
    statements, and the artifact's flags flip and it verifies.

    This is deliberately weaker than it looks, and the name says so. It proves
    the *payload* stops needing the repository once nothing imports it; it does
    **not** prove the product still works, because deleting the three
    `from ..fleet import FleetService` statements leaves three call sites named
    `FleetService` with nothing to resolve them. Measured on a copy at #407's head
    `9538238d`, exactly that cut turns eight `DeleteProjectHere` tests red with
    `NameError: name 'FleetService' is not defined` — packaging clean, product
    broken.

    The patch that is behaviour-preserving (it moves the three helpers the
    connector needs into `fleet_mcp/owner_access.py`, which the node already
    carries) is published unapplied as `L2-CUT-A.patch` and measured in
    `HANDOFF-407.md`; `tests/test_l2_source_standalone_contract.py` is its
    RED → GREEN acceptance. Nothing in `fleet_mcp/` is edited here or there.
    """
    import shutil

    tree = tmp_path / "tree"
    shutil.copytree(ROOT / "fleet_mcp", tree / "fleet_mcp")
    shutil.copytree(ROOT / "fleet", tree / "fleet")
    connector = tree / "fleet_mcp" / "node" / "connector.py"
    source = connector.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    cut = [(node.lineno, node.end_lineno) for node in ast.walk(ast.parse(source))
           if isinstance(node, ast.ImportFrom) and node.module == "fleet"]
    assert cut, "edge A has moved; re-measure HANDOFF-407 before trusting its table"
    for start, end in sorted(cut, reverse=True):
        del lines[start - 1:end]
    connector.write_text("".join(lines), encoding="utf-8")

    before_manifest, before_members = l2.manifest_for(
        l2.L2_NODE, NODE_VERSION, root=ROOT, allow_external_in_repo=ALLOW)
    assert "fleet_mcp/registry.py" in before_members, (
        "the premise: A is what drags registry into the node's closure")
    assert before_manifest["l2"]["runtime_standalone"] is False

    after_modules = set(l2.closure(l2.ENTRY[l2.L2_NODE], tree).modules)
    pin = l2.NODE_SHARED_SURFACE
    trimmed = tuple(module for module in pin if module in after_modules)
    assert trimmed != pin, "cut A must remove something from the pin, or nothing moved"

    try:
        l2.NODE_SHARED_SURFACE = trimmed
        manifest, members = l2.manifest_for(l2.L2_NODE, NODE_VERSION, root=tree)
        block = manifest["l2"]
        assert block["external_in_repo"] == [], (
            "A is the only path to the repository root; if this is not empty then a "
            "second edge exists and HANDOFF-407 is out of date")
        assert len(members) == 25, sorted(members)
        assert (block["import_standalone"], block["runtime_standalone"]) == (True, True)
        assert block["install"]["independently_installable"] is True
        assert block["install"]["blocked_by"] == []
        assert len(block["shared_surface"]) == 12
        # And the cut payload verifies under the trimmed pin, which is the check
        # `verify` makes: the manifest must describe the pin it was built against.
        l2.write_artifact(manifest, members, tmp_path / "after.tar.gz")
        l2.verify(l2.read_artifact(tmp_path / "after.tar.gz"), expect_kind=l2.L2_NODE)
    finally:
        l2.NODE_SHARED_SURFACE = pin


def test_the_module_opens_no_socket_and_spawns_only_the_isolation_probe():
    """A packaging tool that can open a socket, or shell out to anything it likes,
    is a tool that can do either while claiming to be offline.

    `subprocess` is not banned outright any more, and that is a decision rather
    than a relaxation: "does this payload import without the repository" can only
    be answered in a fresh interpreter — measuring it in this process would answer
    "yes" for a payload that merely found `fleet` already in `sys.modules`. So the
    test keeps the ban that matters (no network, no shell) and pins the one spawn
    to the one shape: `sys.executable -I -B -c <the _PROBE constant> <dir> …`, no
    `shell=`, no `check=`, no arguments assembled from data.

    `os.system` is not an import, so the import scan never sees it: the attribute
    calls are checked separately, or the ban covers only the way it is spelled
    least often.
    """
    tree = ast.parse((PACKAGE / "l2_release.py").read_text(encoding="utf-8"))
    banned = {"socket", "urllib", "http", "requests", "ssl", "shutil",
              "telnetlib", "ftplib", "smtplib"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & banned), f"release build imports {sorted(imported & banned)}"

    calls = set()
    spawns = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        holder = node.func.value
        if isinstance(holder, ast.Name) and holder.id == "subprocess":
            spawns.append(node)
            calls.add(f"subprocess.{node.func.attr}")
        elif isinstance(holder, ast.Name) and holder.id in {"os", "socket"}:
            calls.add(f"{holder.id}.{node.func.attr}")
        elif isinstance(holder, ast.Attribute) and holder.attr in {"system", "popen"}:
            calls.add(node.func.attr)
    assert calls == {"subprocess.run"}, f"release build calls {sorted(calls)}"

    call = spawns[0]
    assert {keyword.arg for keyword in call.keywords} <= {"capture_output", "text",
                                                          "cwd", "env", "timeout"}
    argv = call.args[0]
    assert isinstance(argv, ast.List), "the child command must be a literal list"
    fixed = [ast.unparse(element) for element in argv.elts]
    assert fixed[0] == "sys.executable"
    # `-I`: no PYTHONPATH, no user site, no cwd. `-B`: no bytecode written into
    # the payload being measured. `-c`: the probe, not a file from the payload.
    assert fixed[1:4] == ["'-I'", "'-B'", "'-c'"], fixed[1:4]
    assert fixed[4] == "_PROBE", fixed[4]
    assert l2._PROBE.count("importlib.import_module") == 1
    assert "fleet" in l2._PROBE, (
        "the probe must strip any sys.path entry that can see a `fleet` package, "
        "or a site-packages copy makes the measurement say the opposite")


def test_the_artifact_contains_no_absolute_paths(tmp_path):
    for kind in l2.L2_KINDS:
        path = build(kind, tmp_path / f"{kind}.tar.gz", version=NODE_VERSION)
        for name in l2.read_artifact(path).members:
            assert not name.startswith("/") and ".." not in Path(name).parts
