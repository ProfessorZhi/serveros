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
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
                   symlink: frozenset[str] = frozenset()) -> dict:
    """A package that PASSES, with named defects available for the negative cases."""
    members = []
    for name, role in (
        ("serveros-native-core", "actuator"),
        ("serveros-node-resource-snapshot", "read-only"),
    ):
        path = package / "bin" / name
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

    def test_the_recipe_refuses_to_build_inside_the_repository(self):
        recipe = MAKEFILE.read_text(encoding="utf-8")
        self.assertIn("refusing to build inside the repository", recipe)

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


if __name__ == "__main__":
    unittest.main()
