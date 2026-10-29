"""Wave 18 · real, reproducible, isolatable, recoverable Next release units.

Everything here is offline: `git archive` into a throwaway tree, the reviewed
CUT-A/CUT-B patches applied to that copy, deterministic tarballs, imports in a
fresh interpreter with no checkout and no neighbour, a negative control, and a
rollback against a Legacy 0.1.0 fake. Nothing is installed, started, bound,
authenticated or sent; `REAL_DEVICE NOT_RUN`.

The source files under `fleet_mcp/**` are read-only inputs and are asserted to
remain unmodified.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BUILDER_DIR = REPO / "integrations/fleet-next-wave18-artifact-builder"
VERSION = "1.0.0+wave18test"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


w18 = _load("wave18_builder", BUILDER_DIR / "wave18_builder.py")


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("wave18-evidence")
    result = w18.evidence(REPO, "HEAD", VERSION, out)
    return out, result, w18.load_builder(REPO)


# ---------------------------------------------------------------------------
# the D7 builder is reused, not re-implemented
# ---------------------------------------------------------------------------
def test_builder_reuses_the_pinned_d7_contract(built):
    _out, _result, l2 = built
    assert hashlib.sha256(
        (REPO / w18.BUILDER_REL).read_bytes()).hexdigest() == w18.BUILDER_PIN_SHA256
    for name in ("closure", "write_artifact", "read_artifact", "extract",
                 "measure_standalone"):
        assert callable(getattr(l2, name))
    # The four units come from the D7 closure, not a second file walk.
    assert l2.closure("fleet_mcp.node.connector", REPO).modules


# ---------------------------------------------------------------------------
# RED: the unpatched tree is not two independent artifacts
# ---------------------------------------------------------------------------
def test_baseline_is_red_and_refuses_cross_component(tmp_path):
    result = w18.baseline_evidence(REPO, "HEAD", tmp_path)
    assert result["state"] == "RED_BASELINE"
    assert result["decision"] == w18.BLOCKED_CROSS_COMPONENT
    assert result["partition"]["base_exact"] is False
    assert result["partition"]["node_own"] == 0
    assert result["leaks"]["hub"]["leaks"] == [
        "fleet_mcp/node/__init__.py", "fleet_mcp/node/connector.py"]
    assert "fleet_mcp/fleet.py" in result["leaks"]["node"]["leaks"]
    assert "fleet_mcp/registry.py" in result["leaks"]["node"]["leaks"]
    assert result["two_artifact_boot"]["hub"]["booted"] is False
    assert result["two_artifact_boot"]["node"]["booted"] is False

    with pytest.raises(w18.Refused) as exc:
        w18.build(REPO, "HEAD", VERSION, tmp_path / "refused",
                  mode="baseline")
    assert exc.value.code == w18.BLOCKED_CROSS_COMPONENT
    assert "Hub would carry" in exc.value.detail


# ---------------------------------------------------------------------------
# GREEN: four real units, disjoint and verifiable
# ---------------------------------------------------------------------------
def test_four_units_build_verify_and_stay_disjoint(built):
    out, result, l2 = built
    units = result["units"]
    expected = {
        w18.BASE_KIND: (19, []),
        w18.AUTHORITY_KIND: (3, []),
        w18.HUB_KIND: (13, [w18.BASE_KIND, w18.AUTHORITY_KIND]),
        w18.NODE_KIND: (2, [w18.BASE_KIND]),
        w18.COMPOSITION_KIND: (0, []),
    }
    for kind, (count, deps) in expected.items():
        assert units[kind]["member_count"] == count, kind
        assert units[kind]["depends_on"] == deps, kind
        assert units[kind]["installable"] is (kind != w18.COMPOSITION_KIND)
        assert units[kind]["tarball_sha256"].startswith("sha256:")
        assert len(units[kind]["tree_sha256"]) == 64
        assert all(char in "0123456789abcdef"
                   for char in units[kind]["tree_sha256"])
        path = out / "v1" / f"{kind}-{VERSION}.tar.gz"
        assert path.is_file()
        w18.verify_unit(l2, w18.Unit(
            kind=kind, version=VERSION, path=path,
            sha256=units[kind]["tarball_sha256"].split(":", 1)[1],
            tree_sha256=units[kind]["tree_sha256"],
            members={}, manifest={}), expect_kind=kind, expect_version=VERSION)

    hub = l2.read_artifact(out / "v1" / f"{w18.HUB_KIND}-{VERSION}.tar.gz")
    node = l2.read_artifact(out / "v1" / f"{w18.NODE_KIND}-{VERSION}.tar.gz")
    base = l2.read_artifact(out / "v1" / f"{w18.BASE_KIND}-{VERSION}.tar.gz")
    assert set(node.members) == {"fleet_mcp/node/__init__.py",
                                 "fleet_mcp/node/connector.py"}
    assert not any(rel.startswith("fleet_mcp/node/") for rel in hub.members)
    for forbidden in ("fleet_mcp/server.py", "fleet_mcp/tools.py",
                      "fleet_mcp/fleet.py", "fleet_mcp/registry.py",
                      "fleet_mcp/owner_setup.py",
                      "fleet_mcp/providers/fleet_node.py",
                      "fleet_mcp/providers/build.py"):
        assert forbidden in hub.members
        assert forbidden not in node.members
        assert forbidden not in base.members


def test_tarballs_are_byte_identical_across_builds(built):
    out, result, _l2 = built
    assert result["deterministic_rebuild"]
    assert all(result["deterministic_rebuild"].values())
    assert result["source"]["archive_reproducible_across_three_builds"] is True
    for kind in (*w18.UNITS, w18.COMPOSITION_KIND):
        first = (out / "v1" / f"{kind}-{VERSION}.tar.gz").read_bytes()
        second = (out / "rebuild" / f"{kind}-{VERSION}.tar.gz").read_bytes()
        assert first == second, kind
        assert hashlib.sha256(first).hexdigest() == \
               result["units"][kind]["tarball_sha256"].split(":", 1)[1]
    # The checksum file is a byte-for-byte artefact of the same build.
    checksums = (out / "ARTIFACTS.sha256").read_text().splitlines()
    assert len(checksums) == 5
    for line in checksums:
        digest, name = line.split("  ", 1)
        assert (out / "v1" / name).is_file()
        assert hashlib.sha256((out / "v1" / name).read_bytes()).hexdigest() == digest


# ---------------------------------------------------------------------------
# isolation: no checkout, no neighbour, no undeclared import
# ---------------------------------------------------------------------------
def test_isolation_without_checkout_or_neighbour(built):
    _out, result, _l2 = built
    isolation = result["isolation"]
    assert isolation["hub"]["booted"] is True
    assert isolation["hub"]["node_loader"] == "NODE_CONFIG_LOADER_UNAVAILABLE"
    assert isolation["hub"]["authority_symbols"] is True
    assert isolation["hub"]["dispatch_refused"] is True
    assert isolation["node"]["booted"] is True
    assert isolation["node"]["device_ref"] == "device:wave18-hermetic"

    hub_modules = {name for name, _path in isolation["hub"]["fleet_mcp_modules"]}
    node_modules = {name for name, _path in isolation["node"]["fleet_mcp_modules"]}
    assert not any(name == "fleet_mcp.node"
                   or name.startswith("fleet_mcp.node.") for name in hub_modules)
    assert "fleet_mcp.server" not in node_modules
    assert "fleet_mcp.fleet" not in node_modules
    assert "fleet_mcp.registry" not in node_modules


def test_undeclared_dependency_is_refused(tmp_path):
    root = tmp_path / "root"
    evil = root / "fleet_mcp" / "evil.py"
    evil.parent.mkdir(parents=True)
    evil.write_text("X = 1\n")
    answer = {"fleet_mcp_modules": [
        ["fleet_mcp", str(root / "fleet_mcp" / "__init__.py")],
        ["fleet_mcp.evil", str(evil)],
    ]}
    with pytest.raises(w18.Refused) as exc:
        w18.audit_loaded("node", answer, ["fleet_mcp"], root)
    assert exc.value.code == w18.BLOCKED_UNDECLARED_DEPENDENCY

    outside = tmp_path / "outside.py"
    outside.write_text("X = 1\n")
    answer = {"fleet_mcp_modules": [["fleet_mcp.evil", str(outside)]]}
    with pytest.raises(w18.Refused) as exc:
        w18.audit_loaded("node", answer, ["fleet_mcp.evil"], root)
    assert exc.value.code == w18.BLOCKED_UNDECLARED_DEPENDENCY


def test_negative_control_removing_a_shared_primitive_fails(built):
    _out, result, _l2 = built
    negatives = result["negative_control"]
    assert set(negatives) == {
        "node:fleet_mcp/errors.py",
        "node:fleet_mcp/config.py",
        "hub:fleet/transport.py",
    }
    for case, answer in negatives.items():
        assert answer["booted"] is False, case
        assert answer["error"], case


# ---------------------------------------------------------------------------
# rollback: independent, and the Legacy fake does not move
# ---------------------------------------------------------------------------
def test_independent_and_composition_rollback_preserve_legacy(built):
    _out, result, _l2 = built
    rollback = result["rollback"]
    assert rollback["decision"] == w18.OK
    assert rollback["legacy_unchanged"] is True
    assert rollback["after"] == rollback["before"]
    assert set(rollback["before"]["answers"]) == {"get_session", "get_run", "send"}
    assert rollback["before"]["identities"]
    steps = {step["step"]: step for step in rollback["steps"]}
    assert steps["rollback-node-only"]["node_booted"] is True
    assert steps["rollback-node-only"]["hub_UNTOUCHED"] is True
    assert steps["rollback-node-only"]["legacy_unchanged"] is True
    assert steps["rollback-hub-only"]["hub_booted"] is True
    assert steps["rollback-hub-only"]["node_UNTOUCHED"] is True
    assert steps["rollback-hub-only"]["legacy_unchanged"] is True
    assert steps["composition-rollback"]["legacy_unchanged"] is True


# ---------------------------------------------------------------------------
# release footprints: #483/#480 vocabulary
# ---------------------------------------------------------------------------
def test_release_plan_publishes_footprints_and_collision_veto(built):
    _out, result, _l2 = built
    plan = result["release_plan"]
    hub, node = plan["hub"], plan["node"]
    for name in w18.RESOURCES:
        assert name in hub and name in node
    assert plan["matrix"]["decision"] == w18.OK
    assert hub["device_ref"] == node["device_ref"]
    assert hub["install_path"] != node["install_path"]
    assert hub["port"] != node["port"]
    assert hub["lock"] != node["lock"]
    assert hub["backend_id"] != node["backend_id"]
    assert hub["oauth_audience"] != node["oauth_audience"]

    everything = {name: hub[name] for name in w18.RESOURCES}
    collided = w18.resource_matrix(hub, dict(node, **everything))
    assert collided["code"] == w18.BLOCKED_RESOURCE_COLLISION
    assert {item[0] for item in collided["collisions"]} == set(w18.RESOURCES)

    wrong_host = w18.resource_matrix(hub, dict(node, device_ref="device:osaka"))
    assert wrong_host["code"] == w18.BLOCKED_DEVICE_DISAGREEMENT
    assert wrong_host["collisions"] == []

    assert plan["shadow"]["read"]["code"] == w18.OK
    assert plan["shadow"]["write_even_if_granted"]["code"] == \
           w18.NEXT_SHADOW_WRITE_FORBIDDEN
    assert plan["shadow"]["unknown"]["code"] == w18.SHADOW_VERB_UNKNOWN
    assert w18.shadow_decision("get_run", granted=False)["code"] == \
           w18.NEXT_SHADOW_NOT_GRANTED


# ---------------------------------------------------------------------------
# authority and provenance
# ---------------------------------------------------------------------------
def test_one_identity_authority_and_no_second_device_ref(built):
    out, result, l2 = built
    authority = l2.read_artifact(
        out / "v1" / f"{w18.AUTHORITY_KIND}-{VERSION}.tar.gz")
    assert set(authority.members) == {
        "fleet/transport.py", "fleet/transport-contract.yaml",
        "fleet-authority.json"}
    declared = json.loads(authority.members["fleet-authority.json"])
    assert declared["single_authority"] is True
    assert declared["abi"] == w18.AUTHORITY_ABI
    assert declared["signed"] is False
    assert "fleet/registry.yaml" not in authority.members

    for kind in (*w18.UNITS, w18.COMPOSITION_KIND):
        artifact = l2.read_artifact(out / "v1" / f"{kind}-{VERSION}.tar.gz")
        for rel, data in artifact.members.items():
            if rel.startswith("fleet/"):
                continue
            assert b"class DeviceRef" not in data, f"{kind}:{rel}"
            assert b"class DeviceRegistry" not in data, f"{kind}:{rel}"


def test_provenance_records_owner_sha_original_and_result(built):
    _out, result, _l2 = built
    source = result["source"]
    assert source["built_from"].startswith("git archive")
    assert source["workdir_used_as_artifact"] is False
    assert source["live_tree_clean_at_build"] is True
    assert source["commit"] and source["tree"]

    patches = {patch["id"]: patch for patch in result["transform"]["patches"]}
    assert set(patches) == {"CUT-A", "CUT-B"}
    assert patches["CUT-A"]["owner_commit"].startswith("b042f515")
    assert patches["CUT-A"]["rebased_to"].startswith("main@e69fc03")
    assert patches["CUT-B"]["owner_commit"].startswith("3512c551")
    assert patches["CUT-B"]["rebased_to"] is None
    for patch in patches.values():
        assert patch["sha256"].startswith("sha256:")
        assert patch["applied_in"] == "isolated git-archive copy"
        assert patch["live_tree_modified"] is False
        assert patch["targets"]
        for target in patch["targets"]:
            assert target["result_sha256"]
            assert target["result_blob_sha"]
            if target["path"] != "fleet_mcp/providers/build.py":
                assert target["original_sha256"]
                assert target["original_blob_sha"]
            assert "input_sha256" in target and "input_blob_sha" in target

    # CUT-A's input is the source commit. CUT-B's input is CUT-A's result for
    # the file both touch, and the source commit for the rest. A reader can
    # replay the sequence from these fields alone.
    cut_a_results = {target["path"]: target["result_sha256"]
                     for target in patches["CUT-A"]["targets"]}
    for target in patches["CUT-A"]["targets"]:
        assert target["input_sha256"] == target["original_sha256"]
    for target in patches["CUT-B"]["targets"]:
        if target["path"] in cut_a_results:
            assert target["input_sha256"] == cut_a_results[target["path"]]
        elif target["path"] == "fleet_mcp/providers/build.py":
            assert target["input_sha256"] is None
            assert target["input_blob_sha"] is None
        else:
            assert target["input_sha256"] == target["original_sha256"]


# ---------------------------------------------------------------------------
# tamper, dirty source, and the never-modified owner tree
# ---------------------------------------------------------------------------
def test_tarball_is_tamper_evident(built, tmp_path):
    out, _result, l2 = built
    original = out / "v1" / f"{w18.NODE_KIND}-{VERSION}.tar.gz"
    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(original, "r:gz") as source:
        members = [(info, source.extractfile(info).read())
                   for info in source.getmembers()]
    with tarfile.open(tampered, "w:gz") as target:
        changed = False
        for info, data in members:
            if info.name == "fleet_mcp/node/connector.py" and not changed:
                data = data + b"\n# tampered\n"
                changed = True
            info.size = len(data)
            target.addfile(info, io.BytesIO(data))
    with pytest.raises(l2.Refused):
        l2.read_artifact(tampered)


def test_build_refuses_a_dirty_source(tmp_path):
    repo = tmp_path / "dirty"
    (repo / "fleet_mcp").mkdir(parents=True)
    (repo / "fleet").mkdir()
    (repo / "fleet_mcp/errors.py").write_text("X = 1\n")
    (repo / "fleet/transport.py").write_text("X = 1\n")
    env = {**__import__("os").environ,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(["git", "-C", str(repo), *args],
                       check=True, capture_output=True, env=env)
    (repo / "fleet_mcp/errors.py").write_text("X = 2\n")
    with pytest.raises(w18.Refused) as exc:
        w18.archive_source(repo, "HEAD", tmp_path / "out")
    assert exc.value.code == w18.BLOCKED_DIRTY_SOURCE


def test_owner_source_tree_is_untouched(built):
    proc = subprocess.run(
        ["git", "-C", str(REPO), "status", "--porcelain",
         "--untracked-files=no", "--", "fleet_mcp", "fleet", "runtime",
         "scripts"],
        capture_output=True, text=True, check=True)
    assert proc.stdout.strip() == "", proc.stdout


# ---------------------------------------------------------------------------
# the release is still not claimed
# ---------------------------------------------------------------------------
def test_release_is_not_claimed_and_device_is_not_run(built):
    _out, result, _l2 = built
    assert result["technical_state"] == w18.GREEN_ARTIFACTS
    assert result["source_state"] == w18.BLOCKED_OWNER
    assert result["decision"] == w18.BLOCKED_OWNER
    assert result["release_permitted"] is False
    assert result["real_device"] == w18.NOT_RUN
    assert result["why_blocked"]
    composition = result["units"][w18.COMPOSITION_KIND]
    assert composition["member_count"] == 0
    assert composition["installable"] is False
    # The composition is a tested set, not a same-version atomic claim.
    from_l2 = built[2].read_artifact(
        built[0] / "v1" / f"{w18.COMPOSITION_KIND}-{VERSION}.tar.gz")
    manifest = from_l2.manifest
    assert manifest["same_version_required"] is False
    assert manifest["rollback_order"] == [
        w18.NODE_KIND, w18.HUB_KIND, w18.AUTHORITY_KIND, w18.BASE_KIND]
    assert manifest["composition_rollback"]["kind"] == "compensating"


# ---------------------------------------------------------------------------
# W19 · reproducibility under a hostile Git line-ending configuration
# ---------------------------------------------------------------------------
REFERENCE_COMMIT = "ff514f4bd447588e77274753c34f0a664258ca64"
REFERENCE_VERSION = "1.0.0+wave18"
W19_EVIDENCE = BUILDER_DIR / "evidence-w19"


def _crlf_patched_tree(tmp_path: Path) -> Path:
    """Apply CUT-A/CUT-B with `core.autocrlf=true`, exactly as #514's host did.

    The archive comes from HEAD, which is the only commit a shallow PR
    checkout is guaranteed to have. `fleet_mcp/**` and `fleet/**` are
    unchanged from `ff514f4`, so the patched file bytes are the same; the test
    labels the Source with the historical commit the artifacts name.
    """
    root = tmp_path / "crlf-src"
    root.mkdir()
    archive = subprocess.run(
        ["git", "-C", str(REPO), "archive", "HEAD"],
        capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        try:
            tar.extractall(root, filter="data")
        except TypeError:
            tar.extractall(root)
    env = {**__import__("os").environ,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for args in (("init", "-q"), ("config", "core.autocrlf", "true"),
                 ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(["git", "-C", str(root), *args],
                       check=True, capture_output=True, env=env)
    for patch in ("cut_a_main_rebase.patch", "cut_b_hub_node_seam.patch"):
        path = (REPO / "integrations/fleet-next-wave17-hub-node-release-cut/"
                       "handoff" / patch)
        subprocess.run(["git", "-C", str(root), "apply", str(path)],
                       check=True, capture_output=True, env=env)
    assert b"\r\n" in (root / "fleet_mcp/node/connector.py").read_bytes()
    return root


def test_red_crlf_transform_reproduces_514(tmp_path, monkeypatch):
    """#514's four code digests are the CRLF transform, not a stale list."""
    root = _crlf_patched_tree(tmp_path)
    l2 = w18.load_builder(REPO)
    source = w18.Source(commit=REFERENCE_COMMIT, tree="0" * 40,
                        archive_sha256="0" * 64, root=root, live_clean=True)
    patches = (
        w18.PatchResult(id="CUT-A", path="x", sha256="0" * 64,
                        owner_source="", owner_commit="", rebased_to=None,
                        origin="", targets=()),
        w18.PatchResult(id="CUT-B", path="y", sha256="0" * 64,
                        owner_source="", owner_commit="", rebased_to=None,
                        origin="", targets=()),
    )
    with monkeypatch.context() as patch:
        # The old builder had no line-ending guard. Disabling only that guard
        # reproduces its bytes without keeping a second copy of the builder.
        patch.setattr(w18, "_refuse_crlf", lambda *a, **k: None)
        units = w18.build_units(l2, source, root, patches, REFERENCE_VERSION,
                                tmp_path / "red-crlf-build")
    produced = {kind: unit.sha256 for kind, unit in units.items()}
    # #514's exact prefixes for the four code units.
    assert produced[w18.HUB_KIND].startswith("d9f57a3d")
    assert produced[w18.NODE_KIND].startswith("5a2f3389")
    assert produced[w18.BASE_KIND].startswith("38316cf9")
    # The authority is unpatched, so it stayed the same: the clue that the
    # difference is in the transform, not in the archive or the gzip header.
    assert produced[w18.AUTHORITY_KIND].startswith("ace00295")


def test_fixed_builder_refuses_a_crlf_transform(tmp_path):
    """A CRLF result is refused, not published as a second digest."""
    root = _crlf_patched_tree(tmp_path)
    l2 = w18.load_builder(REPO)
    source = w18.Source(commit=REFERENCE_COMMIT, tree="0" * 40,
                        archive_sha256="0" * 64, root=root, live_clean=True)
    patches = (
        w18.PatchResult(id="CUT-A", path="x", sha256="0" * 64,
                        owner_source="", owner_commit="", rebased_to=None,
                        origin="", targets=()),
        w18.PatchResult(id="CUT-B", path="y", sha256="0" * 64,
                        owner_source="", owner_commit="", rebased_to=None,
                        origin="", targets=()),
    )
    with pytest.raises(w18.Refused) as exc:
        w18.build_units(l2, source, root, patches, REFERENCE_VERSION,
                        tmp_path / "fixed-crlf-build")
    assert exc.value.code == w18.BLOCKED_LINE_ENDING_DRIFT


def _build_in_subprocess(builder: Path, repo: Path, commit: str,
                         version: str, out: Path, env: dict) -> dict:
    runner = out.parent / "runner.py"
    runner.write_text(
        "import importlib.util,sys,json\n"
        "from pathlib import Path\n"
        "spec=importlib.util.spec_from_file_location('b',sys.argv[1])\n"
        "m=importlib.util.module_from_spec(spec);sys.modules['b']=m\n"
        "spec.loader.exec_module(m)\n"
        "b=m.build(Path(sys.argv[2]),sys.argv[3],sys.argv[4],Path(sys.argv[5]))\n"
        "print(json.dumps({k:u.sha256 for k,u in sorted(b.units.items())},"
        "sort_keys=True))\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-I", str(runner), str(builder), str(repo), commit,
         version, str(out)],
        capture_output=True, text=True, env=env, timeout=180)
    assert proc.returncode == 0, proc.stderr[-800:]
    return json.loads(proc.stdout)


def test_fixed_builder_is_invariant_under_hostile_git_config(tmp_path):
    """Same commit, same 5/5, regardless of the caller's autocrlf/HOME."""
    builder = BUILDER_DIR / "wave18_builder.py"
    base_env = {**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"}
    normal = _build_in_subprocess(builder, REPO, "HEAD", VERSION,
                                  tmp_path / "normal", base_env)
    system = tmp_path / "hostile-system.gitconfig"
    system.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
    hostile_system = _build_in_subprocess(
        builder, REPO, "HEAD", VERSION, tmp_path / "system",
        {**base_env, "GIT_CONFIG_SYSTEM": str(system)})
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[core]\n\tautocrlf = true\n",
                                     encoding="utf-8")
    hostile_global = _build_in_subprocess(
        builder, REPO, "HEAD", VERSION, tmp_path / "global",
        {**base_env, "HOME": str(home)})
    assert normal == hostile_system == hostile_global
    assert len(normal) == 5


def test_independent_qa_rebuild_or_head_determinism(tmp_path):
    """QA from the PR head: published 5/5 when the source commit is present.

    A hosted PR checkout is shallow and may not carry `ff514f4`. In that case
    the same test builds HEAD twice, compares 5/5 and runs the negative
    control, so the CI run still measures the builder rather than trusting a
    note. A full clone takes the published-checksum path.
    """
    chosen = None
    # Prefer the current-main pack; fall back to the historical #518 pack.
    for pack in (BUILDER_DIR / "evidence-main", W19_EVIDENCE):
        expected = pack / "ARTIFACTS.sha256"
        provenance_path = pack / "PROVENANCE.json"
        if not expected.is_file() or not provenance_path.is_file():
            continue
        provenance = json.loads(provenance_path.read_text())
        commit = provenance["source"]["commit"]
        present = subprocess.run(
            ["git", "-C", str(REPO), "cat-file", "-e", f"{commit}^{{commit}}"],
            capture_output=True).returncode == 0
        if present:
            chosen = (expected, provenance, commit)
            break
    if chosen is not None:
        expected, provenance, commit = chosen
        result = w18.qa_rebuild(REPO, commit, provenance["version"],
                                expected, tmp_path / "qa")
        assert result["checksums"]["ok"] is True
        assert result["checksums"]["missing"] == []
        assert result["checksums"]["extra"] == []
        assert result["checksums"]["mismatch"] == []
        assert result["ok"] is True
        assert all(not answer["booted"]
                   for answer in result["negative_control"].values())
    else:
        first = w18.build(REPO, "HEAD", VERSION, tmp_path / "head-a")
        second = w18.build(REPO, "HEAD", VERSION, tmp_path / "head-b")
        first_sha = {kind: unit.sha256 for kind, unit in first.units.items()}
        second_sha = {kind: unit.sha256 for kind, unit in second.units.items()}
        assert first_sha == second_sha
        assert len(first_sha) == 5
        l2 = w18.load_builder(REPO)
        negatives = w18.negative_control(l2, first.units,
                                         tmp_path / "head-negative")
        assert all(not answer["booted"] for answer in negatives.values())


def test_composition_is_manifest_only_and_never_installable(built):
    out, result, l2 = built
    composition = l2.read_artifact(
        out / "v1" / f"{w18.COMPOSITION_KIND}-{VERSION}.tar.gz")
    manifest = composition.manifest
    assert manifest["members"] == []
    assert composition.members == {}
    assert manifest["install"]["independently_installable"] is False
    assert manifest["install"]["blocked_by"]
    assert result["units"][w18.COMPOSITION_KIND]["installable"] is False
    for kind in w18.UNITS:
        assert result["units"][kind]["installable"] is True
