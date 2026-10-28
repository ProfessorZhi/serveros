"""`l2-node` / `l2-hub` on the release path: verified, and NOT_STANDALONE.

Why two verdicts
----------------
The handoff from the L2 authors (PR #431, `integrations/fleet-v1-l2-release/
HANDOFF-419.md`) asks the release path to recognise two more kinds and verify
their payloads. That is the first half of this file.

The second half exists because registering a kind is not the same as blessing a
release. Both L2 artifacts measured on 2026-10-28 import the repository's L0
`fleet/` package and do not carry it — their manifests say `l2.standalone: false`.
Such an artifact is *correct* (it is exactly what the build produced) and is not
*releasable* (a revision directory pointed at it would hold a payload that raises
at import on a machine without this checkout). So `verify_l2_layer` returns two
answers, and `promote`, its dry run and `rollback` all refuse on the second one by
name: `NOT_STANDALONE`.

Every test is offline: temp roots, synthetic payloads, no service, no network.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "scripts" / "release.py"
PEER = ROOT / "integrations" / "fleet-v1-l2-release" / "l2_release.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.dont_write_bytecode = True
release = _load("serveros_release_l2", RELEASE)


def tree_digest(entries) -> str:
    """The recipe #431 uses, written out here so a change to it fails loudly."""
    recipe = "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in entries)
    return hashlib.sha256(recipe.encode("utf-8")).hexdigest()


#: Modules that are packages, not single files.
_PACKAGES = {"fleet_mcp", "fleet_mcp.node", "fleet_mcp.providers"}


def _module_path(dotted: str) -> str:
    base = dotted.replace(".", "/")
    return f"{base}/__init__.py" if dotted in _PACKAGES else f"{base}.py"


def _node_files() -> dict[str, str]:
    """A node payload with the real shape: its leaves plus the pinned surface.

    Not a four-file toy. The pin is a measurement of what the connector's closure
    actually contains — 13 leaves and 17 shared modules — and a fixture smaller
    than the measurement would pass every check except the one that compares them.
    """
    modules = list(release.L2_NODE_EXCLUSIVE) + list(release.L2_NODE_SHARED_SURFACE)
    files = {_module_path(name): f'"""{name}."""\n' for name in modules}
    files["fleet_mcp/node/connector.py"] = '"""connector."""\nfrom fleet_mcp import config\n'
    return files


BASE_FILES = {
    "l2-node": _node_files(),
    "l2-hub": {
        "fleet_mcp/__init__.py": '"""fleet_mcp."""\n',
        "fleet_mcp/server.py": "import json\n",
    },
}
PLATFORMS = {"l2-node": ["linux", "windows"], "l2-hub": ["linux"]}
NAMES = {"l2-node": "fleet-l2-node", "l2-hub": "fleet-l2-hub"}


def make_l2(base: Path, kind: str, *, version: str = "1.4.0+n20261028a",
            standalone: bool = True, external_in_repo=(), files=None,
            mutate=None, reseal: bool = False) -> Path:
    """A staged L2 payload in the shape #431's `manifest_for` produces."""
    payload = dict(files if files is not None else BASE_FILES[kind])
    art = base / f"{kind}-{abs(hash((kind, version, standalone))) % 10000}"
    for name, text in payload.items():
        path = art / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    # #431's 2026-10-28 revision: `members` INCLUDES the manifest, and the digest
    # map covers everything except it.
    digests_list = sorted(payload)
    member_sha256 = {name: hashlib.sha256(payload[name].encode()).hexdigest()
                     for name in digests_list}
    members = sorted(set(digests_list) | {release.ARTIFACT_MANIFEST})
    manifest = {
        "schema": release.ARTIFACT_MANIFEST_SCHEMA,
        "composition": "fleet-l2",
        "name": NAMES[kind],
        "version": version,
        "kinds": [kind],
        "members": members,
        "member_sha256": member_sha256,
        "source": {
            "entry": "fleet_mcp.node.connector" if kind == "l2-node" else "fleet_mcp.server",
            "module_count": len(member_sha256),
            "tree_sha256": tree_digest(
                [{"path": name, "sha256": member_sha256[name]} for name in digests_list]),
        },
        "l2": {
            "kind": kind,
            "platforms": list(PLATFORMS[kind]),
            "depends_on": [],
            "requires": {"python": ">=3.10", "modules": []},
            "external_in_repo": list(external_in_repo),
            "standalone": standalone and not external_in_repo,
        },
    }
    if mutate is not None:
        mutate(manifest, art)
    if reseal:
        # Opt-in, and deliberately NOT the default. A mutation that silently
        # recomputes the digests it is supposed to contradict turns every future
        # digest test green, and the repair is invisible in the test body.
        real = sorted(name for name in set(manifest["members"])
                      if name != release.ARTIFACT_MANIFEST and (art / name).is_file())
        manifest["member_sha256"] = {
            name: hashlib.sha256((art / name).read_bytes()).hexdigest() for name in real}
        manifest["members"] = sorted(set(real) | {release.ARTIFACT_MANIFEST})
        manifest["source"]["module_count"] = len(real)
        manifest["source"]["tree_sha256"] = tree_digest(
            [{"path": name, "sha256": manifest["member_sha256"][name]} for name in real])
    (art / release.ARTIFACT_MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return art


class L2VerificationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    # ---------------------------------------------------------------- GREEN
    def test_a_standalone_node_verifies_and_is_installable(self):
        art = make_l2(self.base, "l2-node")
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertTrue(report["ok"], report["problems"])
        self.assertTrue(report["installable"])
        self.assertTrue(report["standalone"])

    def test_a_standalone_hub_verifies_as_its_own_kind(self):
        art = make_l2(self.base, "l2-hub")
        report = release.verify_l2_layer(art, kind="l2-hub")
        self.assertTrue(report["ok"], report["problems"])

    def test_a_kind_without_a_declaration_still_resolves_to_it_by_shape(self):
        art = make_l2(self.base, "l2-node")
        (art / release.ARTIFACT_MANIFEST).unlink()
        self.assertEqual(release.resolve_kind(art), ("l2-node", "structural"))

    def test_the_kind_is_resolved_and_never_as_a_node_runtime(self):
        for kind in ("l2-node", "l2-hub"):
            with self.subTest(kind=kind):
                art = make_l2(self.base, kind)
                self.assertEqual(release.resolve_kind(art), (kind, "declared"))
                report = release.verify_revision(art, self.base / "state", kind=kind)
                self.assertNotIn("missing bin/serverosd", " ".join(report["problems"]))

    def test_a_wrong_kind_is_refused(self):
        art = make_l2(self.base, "l2-hub")
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("expected ['l2-node']", " ".join(report["problems"]))

    # ------------------------------------------------------ NOT_STANDALONE
    def test_a_payload_that_needs_the_checkout_verifies_but_is_not_installable(self):
        art = make_l2(self.base, "l2-node", standalone=False, external_in_repo=("fleet",))
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertTrue(report["ok"], "the payload is exactly what the build produces")
        self.assertFalse(report["installable"])
        self.assertFalse(report["standalone"])
        self.assertIn("does not carry", " ".join(report["standalone_reasons"]))
        self.assertIn("fleet", " ".join(report["standalone_reasons"]))

    def test_a_manifest_that_does_not_claim_standalone_is_not_installable(self):
        def drop_claim(manifest, art):
            manifest["l2"]["standalone"] = False
        art = make_l2(self.base, "l2-node", mutate=drop_claim)
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["installable"])
        self.assertIn("does not claim standalone", " ".join(report["standalone_reasons"]))

    # ------------------------------------------------------------ integrity
    def test_a_tampered_member_is_refused(self):
        art = make_l2(self.base, "l2-node")
        target = art / "fleet_mcp/config.py"
        target.write_text("import json\n# changed after the manifest was written\n")
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("does not match the manifest entry", " ".join(report["problems"]))

    def test_an_undeclared_file_is_refused(self):
        art = make_l2(self.base, "l2-node")
        (art / "fleet_mcp/stowaway.py").write_text("x = 1\n")
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("did not declare", " ".join(report["problems"]))

    def test_a_member_the_payload_lost_is_refused(self):
        art = make_l2(self.base, "l2-node")
        (art / "fleet_mcp/config.py").unlink()
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("missing declared member", " ".join(report["problems"]))

    def test_a_member_path_that_escapes_is_refused_before_it_is_read(self):
        def escape(manifest, art):
            manifest["members"] = manifest["members"] + ["../outside.py"]
            manifest["member_sha256"]["../outside.py"] = "0" * 64
        art = make_l2(self.base, "l2-node", mutate=escape)
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("not a relative path", " ".join(report["problems"]))

    def test_a_vendored_tool_schema_is_refused(self):
        def vendor(manifest, art):
            # A JSON document, which is what a copied tool schema looks like. A
            # source file that merely MENTIONS both markers is the provider
            # itself, and must stay acceptable.
            path = art / "fleet_mcp/tools.schema.json"
            path.write_text('{"tools": [{"name": "run_get", "inputSchema": {}}]}')
            manifest["members"] = manifest["members"] + ["fleet_mcp/tools.schema.json"]
        art = make_l2(self.base, "l2-hub", mutate=vendor, reseal=True)
        report = release.verify_l2_layer(art, kind="l2-hub")
        self.assertFalse(report["ok"])
        self.assertIn("vendored CloudCLI tool schema", " ".join(report["problems"]))

    def test_a_symlinked_member_is_refused(self):
        """A link is not a member: the bytes match, and the payload lacks the file."""
        art = make_l2(self.base, "l2-hub")
        outside = self.base / "outside.py"
        outside.write_text('"""fleet_mcp."""\n')     # same bytes as the real member
        real = art / "fleet_mcp/__init__.py"
        real.unlink()
        real.symlink_to(outside)
        report = release.verify_l2_layer(art, kind="l2-hub")
        self.assertFalse(report["ok"])
        self.assertIn("is a symlink", " ".join(report["problems"]))

    def test_a_duplicated_member_is_refused(self):
        art = make_l2(self.base, "l2-node")
        manifest = json.loads((art / release.ARTIFACT_MANIFEST).read_text())
        manifest["members"] = manifest["members"] + ["fleet_mcp/config.py"]
        (art / release.ARTIFACT_MANIFEST).write_text(json.dumps(manifest))
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("more than once", " ".join(report["problems"]))

    def test_an_absolute_member_path_is_refused(self):
        art = make_l2(self.base, "l2-node")
        manifest = json.loads((art / release.ARTIFACT_MANIFEST).read_text())
        manifest["members"] = manifest["members"] + ["/etc/passwd"]
        manifest["member_sha256"]["/etc/passwd"] = "0" * 64
        (art / release.ARTIFACT_MANIFEST).write_text(json.dumps(manifest))
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("not a relative path", " ".join(report["problems"]))

    def test_a_required_member_missing_is_refused_even_on_a_direct_call(self):
        """`verify_revision` prechecks this too; a direct caller must not get less."""
        art = make_l2(self.base, "l2-hub")
        (art / "fleet_mcp/server.py").unlink()
        manifest = json.loads((art / release.ARTIFACT_MANIFEST).read_text())
        manifest["members"] = [name for name in manifest["members"]
                               if name != "fleet_mcp/server.py"]
        manifest["member_sha256"].pop("fleet_mcp/server.py", None)
        manifest["source"]["tree_sha256"] = tree_digest(
            [{"path": name, "sha256": manifest["member_sha256"][name]}
             for name in manifest["members"] if name != release.ARTIFACT_MANIFEST])
        (art / release.ARTIFACT_MANIFEST).write_text(json.dumps(manifest))

        report = release.verify_l2_layer(art, kind="l2-hub")
        self.assertFalse(report["ok"])
        self.assertIn("missing its required member fleet_mcp/server.py",
                      " ".join(report["problems"]))

    def test_a_tree_digest_over_a_subset_is_refused(self):
        art = make_l2(self.base, "l2-node")
        manifest = json.loads((art / release.ARTIFACT_MANIFEST).read_text())
        first = sorted(manifest["member_sha256"])[0]
        manifest["source"]["tree_sha256"] = tree_digest(
            [{"path": first, "sha256": manifest["member_sha256"][first]}])
        (art / release.ARTIFACT_MANIFEST).write_text(json.dumps(manifest))
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("does not describe this member list", " ".join(report["problems"]))

    def test_a_manifest_with_another_schema_is_refused(self):
        art = make_l2(self.base, "l2-node")
        manifest = json.loads((art / release.ARTIFACT_MANIFEST).read_text())
        manifest["schema"] = "serveros/something-else"
        (art / release.ARTIFACT_MANIFEST).write_text(json.dumps(manifest))
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("schema", " ".join(report["problems"]))

    def test_a_payload_with_no_l2_block_is_refused(self):
        art = make_l2(self.base, "l2-node")
        manifest = json.loads((art / release.ARTIFACT_MANIFEST).read_text())
        manifest["l2"] = "not an object"
        (art / release.ARTIFACT_MANIFEST).write_text(json.dumps(manifest))
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("is not one of these kinds", " ".join(report["problems"]))

    def test_a_non_canonical_member_name_is_refused(self):
        art = make_l2(self.base, "l2-node")
        manifest = json.loads((art / release.ARTIFACT_MANIFEST).read_text())
        name = "fleet_mcp/config.py"
        manifest["members"] = [f"fleet_mcp/./config.py" if item == name else item
                               for item in manifest["members"]]
        (art / release.ARTIFACT_MANIFEST).write_text(json.dumps(manifest))
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("not in canonical form", " ".join(report["problems"]))

    def test_bytecode_is_refused(self):
        art = make_l2(self.base, "l2-node")
        (art / "fleet_mcp/__pycache__").mkdir()
        (art / "fleet_mcp/__pycache__/config.cpython-314.pyc").write_bytes(b"\x00")
        manifest = json.loads((art / release.ARTIFACT_MANIFEST).read_text())
        manifest["members"] = manifest["members"] + [
            "fleet_mcp/__pycache__/config.cpython-314.pyc"]
        (art / release.ARTIFACT_MANIFEST).write_text(json.dumps(manifest))
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("is bytecode", " ".join(report["problems"]))

    def test_a_member_list_without_the_manifest_is_refused(self):
        """#431 @ 2469ec5f: `members` INCLUDES ARTIFACT.json."""
        art = make_l2(self.base, "l2-node")
        manifest = json.loads((art / release.ARTIFACT_MANIFEST).read_text())
        manifest["members"] = [item for item in manifest["members"]
                               if item != release.ARTIFACT_MANIFEST]
        (art / release.ARTIFACT_MANIFEST).write_text(json.dumps(manifest))
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("omits ARTIFACT.json", " ".join(report["problems"]))

    def test_a_digest_map_that_does_not_cover_the_members_is_refused(self):
        art = make_l2(self.base, "l2-node")
        manifest = json.loads((art / release.ARTIFACT_MANIFEST).read_text())
        manifest["member_sha256"]["fleet_mcp/ghost.py"] = "0" * 64   # extra key
        (art / release.ARTIFACT_MANIFEST).write_text(json.dumps(manifest))
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("covers", " ".join(report["problems"]))

    def test_a_pin_entry_the_payload_no_longer_carries_is_refused(self):
        """The pin is a measurement; an entry left behind after a cut is rot."""
        art = make_l2(self.base, "l2-node")
        dropped = art / "fleet_mcp/audit.py"
        dropped.unlink()
        manifest = json.loads((art / release.ARTIFACT_MANIFEST).read_text())
        manifest["members"] = [item for item in manifest["members"]
                               if item != "fleet_mcp/audit.py"]
        del manifest["member_sha256"]["fleet_mcp/audit.py"]
        manifest["source"]["tree_sha256"] = tree_digest(
            [{"path": name, "sha256": manifest["member_sha256"][name]}
             for name in manifest["members"] if name != release.ARTIFACT_MANIFEST])
        (art / release.ARTIFACT_MANIFEST).write_text(json.dumps(manifest))
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("pinned shared surface is stale", " ".join(report["problems"]))

    def test_an_import_of_a_module_the_payload_does_not_carry_is_refused(self):
        """`fleet_mcp` is not third-party, so the dependency check cannot see this."""
        def import_missing(manifest, art):
            (art / "fleet_mcp/config.py").write_text("import fleet_mcp.audit\n")
            (art / "fleet_mcp/audit.py").unlink()
            manifest["members"] = [item for item in manifest["members"]
                                   if item != "fleet_mcp/audit.py"]
        art = make_l2(self.base, "l2-node", mutate=import_missing, reseal=True)
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        joined = " ".join(report["problems"])
        self.assertIn("does not carry those modules", joined)

    # ------------------------------------------------------------ separation
    def test_a_node_artifact_carrying_the_hub_ingress_is_refused(self):
        def leak(manifest, art):
            (art / "fleet_mcp/server.py").write_text("x = 1\n")
            manifest["members"] = manifest["members"] + ["fleet_mcp/server.py"]
        art = make_l2(self.base, "l2-node", mutate=leak, reseal=True)
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("must not carry hub-side", " ".join(report["problems"]))

    def test_a_hub_artifact_carrying_the_device_half_is_refused(self):
        def leak(manifest, art):
            (art / "fleet_mcp/node/connector.py").parent.mkdir(parents=True)
            (art / "fleet_mcp/node/connector.py").write_text("x = 1\n")
            manifest["members"] = manifest["members"] + ["fleet_mcp/node/connector.py"]
        art = make_l2(self.base, "l2-hub", mutate=leak, reseal=True)
        report = release.verify_l2_layer(art, kind="l2-hub")
        self.assertFalse(report["ok"])
        self.assertIn("must not carry device-side", " ".join(report["problems"]))

    def test_a_hub_artifact_carrying_machine_state_is_refused(self):
        def bind(manifest, art):
            (art / "bindings").mkdir(parents=True)
            (art / "bindings" / "dell.yaml").write_text("device: dell\n")
            manifest["members"] = manifest["members"] + ["bindings/dell.yaml"]
        art = make_l2(self.base, "l2-hub", mutate=bind, reseal=True)
        report = release.verify_l2_layer(art, kind="l2-hub")
        self.assertFalse(report["ok"])
        self.assertIn("machine-specific", " ".join(report["problems"]))

    def test_a_hub_artifact_carrying_a_credential_shaped_file_is_refused(self):
        def secret(manifest, art):
            (art / "tokens.json").write_text("{}\n")
            manifest["members"] = manifest["members"] + ["tokens.json"]
        art = make_l2(self.base, "l2-hub", mutate=secret, reseal=True)
        report = release.verify_l2_layer(art, kind="l2-hub")
        self.assertFalse(report["ok"])
        self.assertIn("credential-shaped", " ".join(report["problems"]))

    def test_an_undeclared_third_party_import_is_refused(self):
        def need_yaml(manifest, art):
            (art / "fleet_mcp/config.py").write_text("import yaml\n")
        art = make_l2(self.base, "l2-node", mutate=need_yaml, reseal=True)
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("does not declare", " ".join(report["problems"]))

    def test_declaring_the_import_makes_it_acceptable(self):
        def need_yaml(manifest, art):
            (art / "fleet_mcp/config.py").write_text("import yaml\n")
            manifest["l2"]["requires"]["modules"] = ["yaml"]
        art = make_l2(self.base, "l2-node", mutate=need_yaml, reseal=True)
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertTrue(report["ok"], report["problems"])

    def test_a_platform_the_kind_does_not_support_is_refused(self):
        def windows_hub(manifest, art):
            manifest["l2"]["platforms"] = ["linux", "windows"]
        art = make_l2(self.base, "l2-hub", mutate=windows_hub)
        report = release.verify_l2_layer(art, kind="l2-hub")
        self.assertFalse(report["ok"])
        self.assertIn("unsupported platform", " ".join(report["problems"]))

    def test_a_kind_must_declare_all_of_its_platforms(self):
        def linux_only_node(manifest, art):
            manifest["l2"]["platforms"] = ["linux"]
        art = make_l2(self.base, "l2-node", mutate=linux_only_node)
        report = release.verify_l2_layer(art, kind="l2-node")
        self.assertFalse(report["ok"])
        self.assertIn("must declare", " ".join(report["problems"]))


class L2LifecycleTest(unittest.TestCase):
    """promote / dry-run / rollback against a temp runtime root."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.runtime_root = self.base / "runtime-assets"
        self.unit_dir = self.base / "systemd-user"
        self.bin_dir = self.base / "bin"
        self.composition = "fleet-l2"
        self.comp_root = self.runtime_root / self.composition
        self.data_root = self.base / "node"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.env = {**os.environ, "DATA_ROOT": str(self.data_root)}

    def tearDown(self):
        self._tmp.cleanup()

    def common(self, *extra):
        return ["--runtime-root", str(self.runtime_root),
                "--composition", self.composition,
                "--state-dir", str(self.data_root / "state/serveros"),
                "--unit-dir", str(self.unit_dir),
                "--bin-dir", str(self.bin_dir),
                "--no-reload", *extra]

    def run_release(self, args):
        return subprocess.run([sys.executable, str(RELEASE), *args],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=self.env)

    def promote(self, artifact, revision, *extra):
        return self.run_release(["promote", "--artifact", str(artifact),
                                 "--revision", revision, *self.common(*extra)])

    def current(self):
        link = self.comp_root / "current"
        return link.resolve().name if link.is_symlink() else None

    # ---------------------------------------------------------------- GREEN
    def test_a_standalone_artifact_promotes_and_installs_nothing(self):
        art = make_l2(self.base, "l2-node")
        proc = self.promote(art, "rev-1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("kind=l2-node", proc.stdout)
        self.assertEqual(self.current(), "rev-1")
        # L2 has no unit and no shim: the same property the plugin face has.
        self.assertFalse(self.unit_dir.exists())
        self.assertFalse(self.bin_dir.exists())
        receipt = json.loads(
            (self.comp_root / "revisions" / "rev-1" / release.TREE_MANIFEST).read_text())
        self.assertTrue(receipt["installable"])

    def test_previous_is_recorded_and_rollback_restores_it(self):
        first = make_l2(self.base, "l2-node", version="1.4.0+n1")
        second = make_l2(self.base, "l2-node", version="1.4.0+n2")
        self.assertEqual(self.promote(first, "rev-1").returncode, 0)
        self.assertEqual(self.promote(second, "rev-2").returncode, 0)
        proc = self.run_release(["rollback", *self.common()])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.current(), "rev-1")

    # ------------------------------------------------------ NOT_STANDALONE
    def test_promote_refuses_a_payload_that_cannot_stand_alone(self):
        art = make_l2(self.base, "l2-node", standalone=False, external_in_repo=("fleet",))
        proc = self.promote(art, "rev-x")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("NOT_STANDALONE", proc.stderr)
        self.assertIn("staged but NOT promoted", proc.stderr)
        self.assertIsNone(self.current(),
                          "an artifact that cannot run was pointed at anyway")

    def test_a_dry_run_refuses_it_too(self):
        art = make_l2(self.base, "l2-node", standalone=False, external_in_repo=("fleet",))
        proc = self.promote(art, "rev-x", "--dry-run")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("NOT_STANDALONE", proc.stderr)
        self.assertNotIn("would promote", proc.stdout)

    def test_rollback_refuses_a_target_that_cannot_stand_alone(self):
        good = make_l2(self.base, "l2-node", version="1.4.0+n1")
        self.assertEqual(self.promote(good, "rev-1").returncode, 0)
        # A revision from before the gate existed: staged, recorded, not installable.
        bad = make_l2(self.base, "l2-node", version="1.4.0+n2",
                      standalone=False, external_in_repo=("fleet",))
        target = self.comp_root / "revisions" / "rev-bad"
        target.parent.mkdir(parents=True, exist_ok=True)
        import shutil as _shutil
        _shutil.copytree(bad, target)
        (target / release.TREE_MANIFEST).write_text(json.dumps({
            "composition": self.composition, "revision": "rev-bad", "kind": "l2-node",
            "kind_basis": "declared", "tree_hash": None,
            "runtime_root": str(self.comp_root / "current"), "previous": "rev-1",
            "installable": False, "standalone": False,
        }))
        # `rollback` reads the previous symlink, so the bad revision has to be
        # the recorded previous — the state a node is in when it was promoted
        # before this gate existed.
        previous = self.comp_root / "previous"
        if previous.is_symlink() or previous.exists():
            previous.unlink()
        previous.symlink_to(target)

        proc = self.run_release(["rollback", *self.common()])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("NOT_STANDALONE", proc.stderr)
        self.assertEqual(self.current(), "rev-1",
                         "a refused rollback moved `current` anyway")

    def test_status_reports_the_second_verdict(self):
        art = make_l2(self.base, "l2-node")
        self.assertEqual(self.promote(art, "rev-1").returncode, 0)
        proc = self.run_release(["status", *self.common()])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("n/a (L2 node face)", proc.stdout)
        self.assertIn("installable       yes", proc.stdout)


    def test_status_does_not_assume_a_revision_it_never_assessed(self):
        """A receipt with no `installable` key was never judged; it is not a yes."""
        art = make_l2(self.base, "l2-node")
        self.assertEqual(self.promote(art, "rev-1").returncode, 0)
        receipt_path = self.comp_root / "revisions" / "rev-1" / release.TREE_MANIFEST
        receipt = json.loads(receipt_path.read_text())
        receipt.pop("installable", None)
        receipt_path.write_text(json.dumps(receipt))

        proc = self.run_release(["status", *self.common()])
        self.assertIn("installable       NOT_STANDALONE", proc.stdout,
                      "a revision nobody assessed printed as installable")


class ExistingKindsAreUnchangedTest(unittest.TestCase):
    """Adding two kinds must not move the three that were there."""

    def test_the_kind_vocabulary_gained_exactly_two(self):
        self.assertEqual(release.ARTIFACT_KINDS,
                         ("node-runtime", "plugin", "native-runtime",
                          "l2-node", "l2-hub"))

    def test_the_existing_required_members_are_untouched(self):
        self.assertEqual(release.REQUIRED_MEMBERS["node-runtime"],
                         ("bin/serverosd", "bin/serverosctl", "runtime/daemon.py",
                          "runtime/cli.py", "runtime/serverosd.service",
                          "workengine/schema.sql"))
        self.assertEqual(release.REQUIRED_MEMBERS["plugin"],
                         (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"))
        self.assertEqual(release.REQUIRED_MEMBERS["native-runtime"],
                         ("PACKAGE.json", "bin/serveros-native-core"))

    def test_a_directory_holding_both_l2_halves_is_refused_as_ambiguous(self):
        """#431's HANDOFF §5 says the two member lists are disjoint. They are not.

        A directory can hold `fleet_mcp/server.py` AND `fleet_mcp/node/connector.py`
        and satisfy both complete lists. What keeps `resolve_kind` single-valued is
        not disjointness — it is the refusal below: two fits is an error, not a
        first-match. This asserts the actual property rather than the claim.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel in (release.REQUIRED_MEMBERS["l2-node"]
                        + release.REQUIRED_MEMBERS["l2-hub"]):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x\n")
            with self.assertRaises(SystemExit) as caught:
                release.resolve_kind(root)
            self.assertIn("cannot tell what kind of artifact this is",
                          str(caught.exception))

    def test_each_l2_kind_on_its_own_still_fits_exactly_one(self):
        for kind in ("l2-node", "l2-hub"):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                for rel in release.REQUIRED_MEMBERS[kind]:
                    path = root / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("x\n")
                self.assertEqual(release.resolve_kind(root), (kind, "structural"))

    def test_a_node_shaped_directory_still_resolves_to_node_runtime(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel in release.REQUIRED_MEMBERS["node-runtime"]:
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x\n")
            self.assertEqual(release.resolve_kind(root), ("node-runtime", "structural"))


@unittest.skipUnless(PEER.is_file(), "PR #431 is not in this tree")
class PeerContractTest(unittest.TestCase):
    """When #431 is present, the two verifiers must agree — not just by intent."""

    def setUp(self):
        self.peer = _load("l2_release_peer", PEER)

    def test_the_copied_tables_are_the_ones_the_builder_declares(self):
        peer = self.peer
        self.assertEqual(sorted(release.L2_KINDS), sorted(peer.L2_KINDS))
        self.assertEqual(release.L2_COMPOSITION, peer.COMPOSITION)
        self.assertEqual(release.L2_NAMES, peer.NAMES)
        self.assertEqual(release.L2_ENTRY, peer.ENTRY)
        self.assertEqual(release.L2_PLATFORMS, peer.PLATFORMS)
        for kind in peer.L2_KINDS:
            self.assertEqual(release.REQUIRED_MEMBERS[kind], peer.REQUIRED_MEMBERS[kind])
        self.assertEqual(release.L2_NODE_DENIED_MODULES, peer.NODE_DENIED_MODULES)
        self.assertEqual(release.L2_NODE_DENIED_PREFIXES, peer.NODE_DENIED_PREFIXES)
        self.assertEqual(tuple(release.L2_NODE_SHARED_SURFACE), tuple(peer.NODE_SHARED_SURFACE))
        self.assertEqual(set(release.L2_NODE_EXCLUSIVE), set(peer.NODE_EXCLUSIVE))
        self.assertEqual(release.L2_HUB_DENIED_PREFIXES, peer.HUB_DENIED_PREFIXES)
        self.assertEqual(release.L2_HUB_DENIED_PATTERNS, peer.HUB_DENIED_PATTERNS)
        self.assertEqual(release.L2_HUB_SECRET_PATTERNS, peer.HUB_SECRET_PATTERNS)
        self.assertEqual(release.L2_SCHEMA_MARKER, peer.SCHEMA_MARKER)
        self.assertEqual(release.L2_SCHEMA_TOOL_HINT, peer.SCHEMA_TOOL_HINT)

    def test_the_tree_digest_recipe_is_the_same(self):
        entries = [{"path": "a.py", "sha256": "0" * 64},
                   {"path": "b/c.py", "sha256": "f" * 64}]
        self.assertEqual(release._l2_tree_digest(entries), self.peer._tree_digest(entries))

    def test_both_verifiers_accept_a_real_staged_build(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            manifest, members = self.peer.manifest_for(
                "l2-node", "1.4.0+n20261028a", root=ROOT,
                allow_external_in_repo=("fleet",))
            stage = base / "rev"
            stage.mkdir()
            for name, data in members.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            (stage / "ARTIFACT.json").write_text(json.dumps(manifest, indent=2))

            mine = release.verify_l2_layer(stage, kind="l2-node")
            self.assertTrue(mine["ok"], mine["problems"])
            # And the same payload is NOT_STANDALONE, which is the whole point.
            self.assertFalse(mine["installable"])
            self.assertTrue(manifest["l2"]["standalone"] is False)


if __name__ == "__main__":
    unittest.main()
