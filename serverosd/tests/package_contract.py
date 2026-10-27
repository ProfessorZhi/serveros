"""The native runtime package contract: positive and negative cases.

Three halves, and the split is the point:

* `PackageVerifyTest` builds SYNTHETIC packages in a temporary directory and
  breaks one thing at a time. It needs no compiler, so the rules the verifier
  enforces are exercised on the host this package exists for — the one with no
  `cmake`.
* `ManifestWriterTest` exercises `package_manifest.build_manifest` against a
  directory of fake binaries: the writer's own refusals, and the digests it
  records.
* `RealTreeTest` asserts the in-repo tables agree with each other and with the
  recipe. A build that promoted a second actuator is then a failing test rather
  than a package nobody verified.

These tests live outside `tests/` because lane C owns `serverosd/**` and not the
repository's top-level suite. Run them with:

    python3 serverosd/tests/package_contract.py

This file is **not** named `test_*.py` or `*_test.py` on purpose.
`tests/test_collection_scope.py` refuses a module outside `tests/` whose name
matches pytest's collection glob, because such a file is collected by a bare
`pytest` while missing from the census in `scripts/verify.py` — the two
instruments that answer "what is the suite" then disagree, and nothing says so.
A module that is not part of the suite must not be named like one.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Importing the two modules beside this file writes `serverosd/__pycache__/`
# into the repository. It is gitignored, so nothing would show it — which is
# exactly why it is switched off rather than relied on to stay invisible.
sys.dont_write_bytecode = True

import package_manifest  # noqa: E402
import verify_native_runtime  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = Path(__file__).resolve().parent.parent / "Makefile"


def _write(path: Path, data: bytes, mode: int = 0o755) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


def _good_manifest(package: Path, *, version: str = "0.2.0",
                   skip: frozenset[str] = frozenset(),
                   symlink: frozenset[str] = frozenset(),
                   linked_bin: bool = False) -> dict:
    """A package that PASSES, with named defects available for the negative cases."""
    members = []
    bin_root = package / "bin"
    if linked_bin:
        # `bin` is a symlink to a sibling directory. Digests are computed THROUGH
        # it, so this package is correct in every respect except that its `bin/`
        # is not part of it.
        real = package / "real-bin"
        real.mkdir(parents=True, exist_ok=True)
        bin_root.symlink_to(real)
        bin_root = real
    for path_in_package, role in verify_native_runtime.EXPECTED_MEMBERS.items():
        name = path_in_package[len("bin/"):]
        path = bin_root / name
        if name not in skip:
            if name in symlink:
                target = package / "elsewhere" / name
                _write(target, f"#!/bin/sh\necho {name}\n".encode())
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(target)
            else:
                _write(path, f"#!/bin/sh\necho {name}\n".encode())
        if path.exists():
            digest = verify_native_runtime.sha256(path)
            size = path.stat().st_size
        else:
            # A declared member that was never written: the digest is a
            # well-formed string about bytes that do not exist.
            digest, size = "0" * 64, 0
        members.append({
            "path": f"bin/{name}",
            "role": role,
            "component": "supervisor" if role == "actuator" else "collector",
            "source": f"native/node-core/src/{name}.cpp",
            "sha256": digest,
            "bytes": size,
            "mode": "0755",
            "legacy_path": f"bin/{name}",
        })
    return {
        "schema": verify_native_runtime.PACKAGE_SCHEMA,
        "name": "serverosd",
        "version": version,
        "source": {"commit": "0" * 40, "dirty": False},
        "toolchain": {"cxx": "/usr/bin/g++", "version": "g++ (test)", "flags": [], "cmake": None},
        "members": members,
        "tests": [],
        "owner_map": {"schema": "serveros/native-core-owner-map.v1"},
        "compatibility": {
            "renames": [],
            "does_not_occupy": list(verify_native_runtime.RESERVED_PATHS),
            "reason": "test fixture",
        },
    }


def _make(package: Path, mutate=None, **kwargs) -> Path:
    manifest = _good_manifest(package, **kwargs)
    if mutate is not None:
        mutate(manifest, package)
    (package / "PACKAGE.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                          encoding="utf-8")
    return package


def _quiet_verify(package: Path, repo: Path | None = None):
    with contextlib.redirect_stdout(io.StringIO()):
        return verify_native_runtime.verify(package, repo)


class PackageVerifyTest(unittest.TestCase):
    def test_a_clean_package_verifies(self):
        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td)))
            self.assertEqual(report.failures, [], report.failures)
            self.assertGreater(report.checked, 10)

    def test_two_actuators_is_refused(self):
        def mutate(manifest, package):
            manifest["members"][1]["role"] = "actuator"

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(any("exactly one actuator" in f for f in report.failures),
                            report.failures)

    def test_a_different_binary_as_the_actuator_is_refused(self):
        def mutate(manifest, package):
            manifest["members"][0]["role"] = "read-only"
            manifest["members"][1]["role"] = "actuator"

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(any("the actuator is" in f for f in report.failures),
                            report.failures)

    def test_a_wrong_digest_is_refused(self):
        def mutate(manifest, package):
            _write(package / "bin/serveros-native-core", b"#!/bin/sh\nexit 0\n")

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(any("matches its digest" in f for f in report.failures),
                            report.failures)

    def test_a_missing_member_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td),
                                         skip=frozenset({"serveros-node-resource-snapshot"})))
            self.assertTrue(any("is a regular file" in f for f in report.failures),
                            report.failures)

    def test_a_symlinked_member_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td),
                                         symlink=frozenset({"serveros-node-resource-snapshot"})))
            self.assertTrue(any("is a regular file" in f for f in report.failures),
                            report.failures)

    def test_a_member_path_that_escapes_bin_is_refused(self):
        def mutate(manifest, package):
            manifest["members"][1]["path"] = "bin/../bin/serveros-node-resource-snapshot"
            manifest["members"][1]["legacy_path"] = manifest["members"][1]["path"]

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(any("is one name under bin/" in f for f in report.failures),
                            report.failures)

    def test_a_duplicate_member_row_is_refused(self):
        def mutate(manifest, package):
            manifest["members"].append(dict(manifest["members"][1]))

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(any("is declared once" in f for f in report.failures),
                            report.failures)

    def test_a_missing_member_row_is_refused(self):
        def mutate(manifest, package):
            manifest["members"].pop()

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(any("every expected member is declared" in f
                                for f in report.failures), report.failures)

    def test_an_unexpected_member_row_is_refused(self):
        def mutate(manifest, package):
            _write(package / "bin/extra", b"#!/bin/sh\n")
            row = dict(manifest["members"][1])
            row.update(path="bin/extra", legacy_path="bin/extra", role="read-only",
                       sha256=verify_native_runtime.sha256(package / "bin/extra"),
                       bytes=(package / "bin/extra").stat().st_size)
            manifest["members"].append(row)

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(any("no member is declared that this package does not have" in f
                                for f in report.failures), report.failures)

    def test_a_renamed_read_only_member_is_refused(self):
        """The manifest is self-consistent; only the reader's own table catches it."""
        def mutate(manifest, package):
            row = next(m for m in manifest["members"]
                       if m["path"].endswith("node-resource-snapshot"))
            source = package / row["path"]
            renamed = package / "bin/renamed-snapshot"
            _write(renamed, source.read_bytes())
            row["path"] = "bin/renamed-snapshot"
            row["legacy_path"] = "bin/renamed-snapshot"
            row["sha256"] = verify_native_runtime.sha256(renamed)
            row["bytes"] = renamed.stat().st_size

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(any("every expected member is declared" in f
                                for f in report.failures), report.failures)

    def test_a_promoted_role_on_a_known_member_is_refused(self):
        def mutate(manifest, package):
            row = next(m for m in manifest["members"] if m["role"] == "read-only")
            row["role"] = "actuator"
            manifest["members"] = [m for m in manifest["members"]
                                   if m["path"] != verify_native_runtime.ACTUATOR_PATH]

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(any("keeps its role" in f for f in report.failures),
                            report.failures)

    def test_does_not_occupy_must_be_a_list_of_paths(self):
        def mutate(manifest, package):
            manifest["compatibility"]["does_not_occupy"] = \
                "src/bin/serverosd and bin/serverosctl, honest"

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(any("does_not_occupy is a list of paths" in f
                                for f in report.failures), report.failures)

    def test_a_declared_rename_is_refused(self):
        def mutate(manifest, package):
            manifest["compatibility"]["renames"] = ["bin/renamed"]

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(any("manifest declares no renames" in f
                                for f in report.failures), report.failures)

    def test_a_linked_bin_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), linked_bin=True))
            self.assertTrue(any("bin/ is a real directory" in f for f in report.failures),
                            report.failures)

    def test_a_non_executable_member_is_refused(self):
        def mutate(manifest, package):
            (package / "bin/serveros-native-core").chmod(0o644)

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(any("is 0755" in f for f in report.failures),
                            report.failures)

    def test_an_undeclared_file_under_bin_is_refused(self):
        def mutate(manifest, package):
            _write(package / "bin/serverosd", b"#!/bin/sh\nexit 0\n")

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(any("no undeclared file" in f for f in report.failures),
                            report.failures)
            self.assertTrue(any("bin/serverosd is absent, as declared" in f
                                for f in report.failures),
                            report.failures)

    def test_a_rename_is_refused(self):
        def mutate(manifest, package):
            manifest["members"][0]["legacy_path"] = "bin/old-name"

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(any("keeps its shipped name" in f for f in report.failures),
                            report.failures)

    def test_an_unknown_role_is_refused(self):
        def mutate(manifest, package):
            manifest["members"][1]["role"] = "observer"

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(any("has a known role" in f for f in report.failures),
                            report.failures)

    def test_another_schema_is_refused(self):
        def mutate(manifest, package):
            manifest["schema"] = "serveros/artifact.v1"

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(any("names this schema" in f for f in report.failures),
                            report.failures)

    def test_an_empty_member_list_is_refused(self):
        def mutate(manifest, package):
            manifest["members"] = []

        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), mutate))
            self.assertTrue(report.failures)

    def test_a_missing_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(Path(td))
            self.assertEqual(len(report.failures), 1, report.failures)

    def test_unreadable_json_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            package = Path(td)
            (package / "PACKAGE.json").write_text("{not json", encoding="utf-8")
            report = _quiet_verify(package)
            self.assertTrue(any("readable JSON" in f for f in report.failures),
                            report.failures)

    def test_a_version_that_is_not_the_binarys_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            report = _quiet_verify(_make(Path(td), version="9.9.9"), REPO_ROOT)
            self.assertTrue(any("is the binary's version" in f for f in report.failures),
                            report.failures)


class ManifestWriterTest(unittest.TestCase):
    """The writer's own behaviour, against fake binaries."""

    def _package(self, td: str, names=None) -> Path:
        out = Path(td) / "out"
        for name in names or package_manifest.MEMBERS:
            _write(out / "bin" / name, f"#!/bin/sh\necho {name}\n".encode())
        return out

    def test_the_writer_records_the_bytes_it_hashed(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._package(td)
            manifest = package_manifest.build_manifest(out, REPO_ROOT, "a" * 40, "")
            self.assertEqual(manifest["schema"], package_manifest.PACKAGE_SCHEMA)
            self.assertEqual(manifest["name"], "serverosd")
            self.assertEqual(len(manifest["members"]), len(package_manifest.MEMBERS))
            for member in manifest["members"]:
                absolute = out / member["path"]
                self.assertEqual(member["sha256"],
                                 package_manifest.sha256(absolute))
                self.assertEqual(member["bytes"], absolute.stat().st_size)
                self.assertEqual(member["mode"], "0755")
                self.assertEqual(member["legacy_path"], member["path"])
            # The document the writer produces must pass the reader.
            (out / "PACKAGE.json").write_text(json.dumps(manifest), encoding="utf-8")
            report = _quiet_verify(out, REPO_ROOT)
            self.assertEqual(report.failures, [], report.failures)

    def test_the_writer_refuses_a_bin_file_it_cannot_describe(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._package(td)
            _write(out / "bin/mystery", b"#!/bin/sh\n")
            with self.assertRaises(SystemExit) as caught:
                package_manifest.build_manifest(out, REPO_ROOT, "a" * 40, "")
            self.assertIn("not a member this package declares", str(caught.exception))

    def test_the_writer_refuses_a_missing_declared_member(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._package(td, names=["serveros-native-core"])
            with self.assertRaises(SystemExit) as caught:
                package_manifest.build_manifest(out, REPO_ROOT, "a" * 40, "")
            self.assertIn("were not built", str(caught.exception))

    def test_provenance_is_measured_or_says_why_not(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._package(td)
            measured = package_manifest.build_manifest(out, REPO_ROOT, "a" * 40, "3")
            self.assertEqual(measured["source"]["commit"], "a" * 40)
            self.assertTrue(measured["source"]["dirty"])
            self.assertEqual(measured["source"]["dirty_paths"], 3)
            clean = package_manifest.build_manifest(out, REPO_ROOT, "a" * 40, "0")
            self.assertFalse(clean["source"]["dirty"],
                             "0 modified paths is a measurement, not an absence")
            unmeasured = package_manifest.build_manifest(out, REPO_ROOT, "", "")
            self.assertIsNone(unmeasured["source"]["commit"])
            self.assertIsNone(unmeasured["source"]["dirty"])
            self.assertIn("reason", unmeasured["source"])


class RealTreeTest(unittest.TestCase):
    """The in-repo tables, asserted against each other and the recipe."""

    def test_exactly_one_actuator_is_declared(self):
        actuators = [name for name, (role, _, _) in package_manifest.MEMBERS.items()
                     if role == "actuator"]
        self.assertEqual(actuators, ["serveros-native-core"])
        self.assertEqual(f"bin/{actuators[0]}", verify_native_runtime.ACTUATOR_PATH)

    def test_the_writer_and_the_reader_agree_on_the_reserved_paths(self):
        self.assertEqual(tuple(package_manifest.RESERVED_PATHS),
                         tuple(verify_native_runtime.RESERVED_PATHS))

    def test_the_writer_and_the_reader_agree_on_the_roles(self):
        declared = {role for role, _, _ in package_manifest.MEMBERS.values()}
        self.assertEqual(declared, set(verify_native_runtime.ROLES))

    def test_the_writer_and_the_reader_agree_on_the_member_set(self):
        """The reader pins the names independently, or a rename is invisible."""
        written = {f"bin/{name}": role
                   for name, (role, _, _) in package_manifest.MEMBERS.items()}
        self.assertEqual(written, verify_native_runtime.EXPECTED_MEMBERS)

    def test_binary_names_are_unique_and_are_not_the_package_name(self):
        names = list(package_manifest.MEMBERS)
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn("serverosd", names,
                         "the package name is a directory and a manifest, not a binary")

    def test_the_package_does_not_claim_the_python_entry_points(self):
        names = {f"bin/{name}" for name in package_manifest.MEMBERS}
        for reserved in verify_native_runtime.RESERVED_PATHS:
            self.assertNotIn(reserved, names)

    def test_every_declared_source_exists(self):
        for name, (_, _, source) in package_manifest.MEMBERS.items():
            self.assertTrue((REPO_ROOT / source).is_file(), f"{name}: {source}")

    def test_the_recipe_builds_every_declared_member(self):
        recipe = MAKEFILE.read_text(encoding="utf-8")
        for name, (_, _, source) in package_manifest.MEMBERS.items():
            self.assertIn(source, recipe, f"{name}'s source is not in the Makefile")
            self.assertIn(f"bin/{name}", recipe, f"{name} is not linked by the Makefile")

    def test_the_recipe_overrides_out_and_resolves_symlinks(self):
        """The two things the guard needs, because either alone is inert.

        A command-line variable beats a makefile assignment, so `OUT := ...`
        without `override` does nothing at all; and `abspath` does not follow a
        symlink, so a link into the repository passes a check made with it. Both
        were measured failing before this assertion existed, which is why it
        names the mechanism rather than the message.
        """
        recipe = MAKEFILE.read_text(encoding="utf-8")
        self.assertIn("refusing to build inside the repository", recipe)
        self.assertIn("override OUT := $(abspath $(OUT))", recipe)
        self.assertIn("OUT_REAL := $(realpath $(OUT))", recipe)

    def test_the_writer_refuses_an_output_directory_inside_the_repository(self):
        with tempfile.TemporaryDirectory() as td:
            # A symlink pointing into the repository: the case `abspath` cannot
            # see and `resolve()` can.
            link = Path(td) / "into-repo"
            link.symlink_to(REPO_ROOT)
            with self.assertRaises(SystemExit) as caught:
                package_manifest.build_manifest(link / "serverosd" / "build", REPO_ROOT,
                                                "a" * 40, "0")
            self.assertIn("inside the repository", str(caught.exception))

    def test_the_build_version_is_the_binarys_version(self):
        self.assertEqual(package_manifest.version_from_source(REPO_ROOT),
                         verify_native_runtime.source_version(REPO_ROOT))

    def test_no_python_here_runs_a_command(self):
        """The actuator gate scans every `*.py`; these files must add no reachability.

        Asserted by AST rather than by a grep, because a grep for `subprocess`
        would pass on a file that imports `os` and calls `os.system`, and would
        fail on a docstring that mentions one.
        """
        import ast

        forbidden = {"subprocess", "os.system", "os.popen", "os.execv", "os.execvp",
                     "os.spawnv", "os.posix_spawn", "pty"}
        for name in ("package_manifest.py", "verify_native_runtime.py",
                     "tests/package_contract.py"):
            path = Path(__file__).resolve().parent.parent / name
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], forbidden,
                                         f"{name} imports {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotIn((node.module or "").split(".")[0], forbidden,
                                     f"{name} imports from {node.module}")
                elif isinstance(node, ast.Call):
                    func = node.func
                    dotted = None
                    if isinstance(func, ast.Attribute):
                        base = func.value
                        if isinstance(base, ast.Name):
                            dotted = f"{base.id}.{func.attr}"
                        elif isinstance(base, ast.Attribute):
                            dotted = f"{base.attr}.{func.attr}"
                    self.assertNotIn(dotted, forbidden, f"{name} calls {dotted}")


class ConsumerMatrixTest(unittest.TestCase):
    """The consumer matrix, checked against the repository it describes.

    `consumers.v1.json` is a claim about which programs can observe or change
    this host and what gates them. A matrix nobody checks is a diagram: it says
    what was true on the day somebody drew it. These tests hold the repo half of
    it — every path it names, every gate source, every schema — so the day the
    code moves is the day the matrix goes red.
    """

    def setUp(self):
        self.matrix = json.loads(
            (REPO_ROOT / "serverosd" / "consumers.v1.json").read_text(encoding="utf-8"))

    def test_the_schema_and_shape(self):
        self.assertEqual(self.matrix["schema"], "serveros/native-core-consumers.v1")
        ids = [w["id"] for w in self.matrix["writers"]]
        self.assertEqual(sorted(ids), ["serveros-native-core", "serverosd"],
                         "there are exactly two programs that can mutate a unit")
        self.assertTrue(all(w["lease"]["required"] for w in self.matrix["writers"]),
                        "a writer without a lease is the split-lock this file exists for")

    def test_every_named_path_exists(self):
        for writer in self.matrix["writers"] + self.matrix["read_only"]:
            entry = writer["entry"]
            if entry["kind"] == "repo_path":
                self.assertTrue((REPO_ROOT / entry["path"]).is_file(),
                                f"{writer['id']}: {entry['path']}")
            elif entry["kind"] == "build_product":
                # MEMBERS is keyed by bare name; the package path is `bin/<name>`.
                name = entry["package_member"].removeprefix("bin/")
                self.assertIn(name, package_manifest.MEMBERS,
                              f"{writer['id']} names a member the package does not build")
            else:
                self.fail(f"{writer['id']} has an unknown entry kind {entry['kind']!r}")
        for writer in self.matrix["writers"]:
            for source in writer["gate_sources"]:
                self.assertTrue((REPO_ROOT / source).is_file(),
                                f"{writer['id']}: gate source {source} is gone")

    def test_the_shared_documents_are_read_by_files_that_exist(self):
        for document in self.matrix["shared_documents"]:
            for reader in document["read_by"]:
                self.assertTrue((REPO_ROOT / reader).is_file(),
                                f"{document['schema']}: reader {reader} is gone")
            self.assertTrue(document["written_by"], document["schema"])

    def test_the_owner_map_schema_is_the_one_both_readers_implement(self):
        declared = next(d for d in self.matrix["shared_documents"]
                        if d["schema"] == "serveros/native-core-owner-map.v1")["schema"]
        cpp = (REPO_ROOT / "native/node-core/supervisor/include/supervisor/facts.hpp"
               ).read_text(encoding="utf-8")
        self.assertIn(f'"{declared}"', cpp, "the C++ reader names another schema")
        facts = _load_module(REPO_ROOT / "runtime" / "native_core_facts.py",
                             "native_core_facts_matrix")
        self.assertEqual(facts.OWNER_MAP_SCHEMA, declared)

    def test_the_capability_schema_matches_the_cpp_constant(self):
        declared = next(d for d in self.matrix["shared_documents"]
                        if d["schema"] == "serveros/native-core-capability.v1")["schema"]
        header = (REPO_ROOT / "native/node-core/supervisor/include/supervisor/capability.hpp"
                  ).read_text(encoding="utf-8")
        self.assertIn(f'kCapabilitySchema = "{declared}"', header)

    def test_the_lease_path_is_shared_by_construction(self):
        """The matrix's sharpest claim, which is now the opposite one.

        It used to assert a GAP: the daemon defaulted a lease path and the native
        Core had none, so the two contended on one file only when an operator
        passed the same argument twice. The Core now derives that path itself and
        refuses to derive one without an absolute `XDG_RUNTIME_DIR`, so this holds
        the closure instead — by comparing the two sources that name the path.
        """
        single = self.matrix["single_writer"]
        self.assertTrue(single["shared_by_declaration"])
        self.assertEqual(single["native_default_lease"],
                         "$XDG_RUNTIME_DIR/serveros/native-core.lease")

        daemon = (REPO_ROOT / "runtime" / "daemon.py").read_text(encoding="utf-8")
        self.assertIn("native-core.lease", daemon,
                      "the daemon no longer defaults this path; the matrix is stale")

        main_cpp = (REPO_ROOT / "native/node-core/supervisor/src/main.cpp"
                    ).read_text(encoding="utf-8")
        self.assertIn('"/serveros/native-core.lease"', main_cpp,
                      "the C++ default and the daemon's default have drifted apart")
        self.assertIn("default_lease_path(lease_path", main_cpp,
                      "the Core no longer derives the lease path")
        # The refusal is the part that keeps a default from being a guess.
        self.assertIn("no default", main_cpp)



def _load_module(path: Path, name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
