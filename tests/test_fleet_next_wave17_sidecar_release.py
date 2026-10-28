"""Wave 17: a sidecar that can be taken back without `rm -rf`.

Wave 16 (#502) reported the sidecar as reproducible and import-independent but
**not revocable**: undoing it was a manual `rm -rf` with no reviewed entrypoint.
A release unit without a revoke path is a release unit whose rollback is a
runbook line, so this file pins the two halves of the fix:

  * a **release descriptor** that names exactly which bytes are being installed
    (version, commit, per-file sha256, one manifest digest), and
  * an **offline `revoke` entrypoint** that deletes only files a matching
    install receipt says it created, inside the root that receipt names, and
    refuses everything else.

Every refusal below is a *negative* test, and every one of them is written so
that disabling the guard it covers turns it red. The companion probe script
(besides this file) patches each guard out one at a time and counts the tests
that flip; a guard whose removal flips nothing is not a guard.

Nothing here installs, starts, stops, registers or contacts anything: the
"sidecar" is two files written into a fresh `tempfile` directory, and the
"Legacy" is a fake gateway in this file. No socket is opened, no service
manager is addressed, no credential is read.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (ROOT / "integrations" / "fleet-next-wave17-sidecar-release"
               / "sidecar_release.py")

#: `integrations/` has no `__init__.py`, so the module is loaded from its path.
#: Registering it in `sys.modules` before `exec_module` is what lets the
#: `@dataclass` decorators resolve their own module while the class body is
#: still being built.
_spec = importlib.util.spec_from_file_location("sidecar_release", MODULE_PATH)
wave17 = importlib.util.module_from_spec(_spec)
sys.modules["sidecar_release"] = wave17
_spec.loader.exec_module(wave17)
m = wave17

DEVICE = "device:wave17-test-01"
BOOT = "boot-2026-10-28-a"


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


#: Two files, deliberately different from the real sidecar's two files: this is
#: a synthetic release. Its digests are computed here, never copied from a claim.
SIDECAR_V1 = {
    "bin/sidecar.py": b"#!/usr/bin/env python3\nprint('sidecar 0.1.0')\n",
    "etc/sidecar.json": b'{"pinned": "0.1.0", "mode": "read-only"}\n',
}
SIDECAR_V2 = {
    "bin/sidecar.py": b"#!/usr/bin/env python3\nprint('sidecar 0.2.0')\n",
    "etc/sidecar.json": b'{"pinned": "0.2.0", "mode": "read-only"}\n',
    "etc/added-in-020.json": b'{"added": true}\n',
}


def release(files=None, *, version="0.1.0", commit="a" * 40, name="sidecar"):
    """A descriptor plus the bytes it describes, with digests derived here."""
    blobs = dict(files if files is not None else SIDECAR_V1)
    entries = tuple(sorted((path, sha(body)) for path, body in blobs.items()))
    desc = m.Descriptor(name=name, version=version, commit=commit, files=entries,
                        entrypoint=sorted(blobs)[0])
    return desc, blobs


def grant(action="revoke", *, device=DEVICE, grant_id="grant-1",
          operator="owner"):
    return m.Grant(grant_id=grant_id, operator=operator, action=action,
                   device_ref=device)


def git(repo: Path, *args: str) -> str:
    """Run git in `repo`, loudly. Used only to build fixtures for the reader."""
    done = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def tree_state(root: Path) -> dict[str, str]:
    """Every file under `root` with its digest — the "did anything move" probe."""
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            found[rel] = "symlink:" + (os.readlink(path) if hasattr(os, "readlink")
                                       else str(path))
        elif path.is_file():
            found[rel] = sha(path.read_bytes())
        elif path.is_dir():
            found[rel + "/"] = "dir"
    return found


class SidecarTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dirs: list[Path] = []

    def tearDown(self) -> None:
        for path in self._dirs:
            shutil.rmtree(path, ignore_errors=True)

    def tmp(self) -> Path:
        path = Path(tempfile.mkdtemp(prefix="wave17-sidecar-"))
        self._dirs.append(path)
        return path

    def fresh_root(self) -> Path:
        """A root that does not exist yet, under an isolated temp directory."""
        return self.tmp() / "root"

    def installed(self, files=None, **kw):
        desc, blobs = release(files, **kw)
        root = self.fresh_root()
        result = m.install(desc, root, blobs, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT)
        self.assertEqual(result.decision, m.OK, result.reasons)
        return desc, blobs, root, result

    def upgraded(self):
        """0.1.0 installed, then 0.2.0 over it: the only state restore acts on."""
        v1, blobs1, root, _result = self.installed()
        v2, blobs2 = release(SIDECAR_V2, version="0.2.0", commit="c" * 40)
        receipt_v1 = m.read_receipt(root)
        result = m.install(v2, root, blobs2, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT, replace=receipt_v1)
        self.assertEqual(result.decision, m.OK, result.reasons)
        return v1, blobs1, v2, blobs2, root

    def in_empty_dir(self) -> Path:
        """Run the rest of this test in a fresh directory.

        For assertions about a *relative* path: those resolve against the cwd,
        so a leftover directory of the same name anywhere else would turn a
        refusal into a false failure.
        """
        where = self.tmp()
        original = os.getcwd()
        os.chdir(where)
        self.addCleanup(os.chdir, original)
        return where

    def revoke(self, desc, root, **kw):
        args = dict(grant=grant("revoke"), device_ref=DEVICE, boot_id=BOOT)
        args.update(kw)
        return m.revoke(desc, root, **args)

    def assertCode(self, result, code) -> None:
        self.assertEqual(result.decision, m.BLOCKED, result.reasons)
        self.assertEqual(result.code, code, result.reasons)

    def forge(self, root: Path, mutate) -> None:
        """Rewrite the receipt **and** the marker: a complete forgery.

        A half-forgery — a receipt whose body was edited without recomputing its
        digest — is caught earlier, by the tamper guard. That is the point of
        having two pieces of evidence. The tests that aim at the *content*
        guards therefore have to forge both, or they would be measuring the
        tamper guard over and over.
        """
        receipt_path = root / m.RECEIPT_NAME
        body = json.loads(receipt_path.read_bytes())
        mutate(body)
        body["receipt_digest"] = m.receipt_digest(
            {k: v for k, v in body.items() if k != "receipt_digest"})
        receipt_path.write_bytes(json.dumps(body, sort_keys=True).encode("utf-8"))
        marker_path = root / m.OWNER_MARKER_NAME
        marker = json.loads(marker_path.read_bytes())
        marker["receipt_digest"] = body["receipt_digest"]
        marker_path.write_bytes(json.dumps(marker, sort_keys=True).encode("utf-8"))


class MakeLink:
    """Create a link the guards must refuse, on a host that may forbid symlinks.

    A directory link falls back to a junction (`mklink /J`), which needs no
    privilege. That fallback is not a convenience: a junction is a reparse
    point that `Path.is_symlink()` reports as **False**, so it is precisely the
    shape a check written with `is_symlink()` alone would wave through.
    """

    @staticmethod
    def make(target: Path, link: Path, *, directory: bool) -> None:
        try:
            os.symlink(target, link, target_is_directory=directory)
            return
        except (OSError, NotImplementedError, AttributeError):
            pass
        if directory and os.name == "nt":
            done = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True, text=True, check=False)
            if done.returncode == 0:
                return
        raise unittest.SkipTest(
            "this host cannot create a symlink or junction (no "
            "SeCreateSymbolicLinkPrivilege); the traversal guard is still "
            "covered on the Linux CI runner")


# --------------------------------------------------------------------------- #
# the descriptor pins bytes
# --------------------------------------------------------------------------- #
class DescriptorIsPinned(unittest.TestCase):
    def test_a_complete_pin_is_accepted(self):
        desc, _ = release()
        verdict = m.check_descriptor(desc)
        self.assertEqual(verdict.decision, m.OK, verdict.reasons)
        self.assertEqual(desc.file_count(), 2)

    def test_a_floating_version_is_refused(self):
        for version in ("", "latest", "*", "1.0", "1.0.0 "):
            with self.subTest(version=version):
                desc, _ = release(version=version)
                verdict = m.check_descriptor(desc)
                self.assertEqual(verdict.code, m.BLOCKED_NOT_PINNED, version)

    def test_a_short_commit_is_not_a_pin(self):
        desc, _ = release(commit="a1b2c3")
        self.assertEqual(m.check_descriptor(desc).code, m.BLOCKED_NOT_PINNED)

    def test_an_empty_manifest_is_refused(self):
        desc = m.Descriptor(name="sidecar", version="0.1.0", commit="a" * 40,
                            files=(), entrypoint="")
        self.assertEqual(m.check_descriptor(desc).code, m.BLOCKED_NOT_PINNED)

    def test_a_duplicated_path_is_refused(self):
        body = SIDECAR_V1["bin/sidecar.py"]
        desc = m.Descriptor(
            name="sidecar", version="0.1.0", commit="a" * 40,
            files=(("bin/sidecar.py", sha(body)), ("bin/sidecar.py", sha(b"x"))),
            entrypoint="bin/sidecar.py")
        self.assertEqual(m.check_descriptor(desc).code, m.BLOCKED_NOT_PINNED)

    def test_a_path_that_escapes_its_root_is_refused(self):
        for path in ("../escape.py", "/etc/passwd", "bin/../../escape.py",
                     "bin\\..\\..\\escape.py", "", "./bin/x.py"):
            with self.subTest(path=path):
                desc = m.Descriptor(
                    name="sidecar", version="0.1.0", commit="a" * 40,
                    files=((path, sha(b"x")),), entrypoint=path)
                self.assertEqual(m.check_descriptor(desc).code,
                                 m.BLOCKED_DESCRIPTOR_PATH, path)

    def test_a_digest_that_is_not_sha256_is_refused(self):
        desc = m.Descriptor(name="sidecar", version="0.1.0", commit="a" * 40,
                            files=(("bin/x.py", "md5:abcd"),),
                            entrypoint="bin/x.py")
        self.assertEqual(m.check_descriptor(desc).code, m.BLOCKED_NOT_PINNED)

    def test_the_entrypoint_must_be_one_of_the_pinned_files(self):
        desc, _ = release()
        wrong = m.Descriptor(name=desc.name, version=desc.version,
                             commit=desc.commit, files=desc.files,
                             entrypoint="bin/absent.py")
        self.assertEqual(m.check_descriptor(wrong).code, m.BLOCKED_NOT_PINNED)

    def test_the_manifest_digest_uses_the_l2_artifact_recipe(self):
        """The recipe is #473's `_tree_digest`, so an L2 artifact feeds this.

        Copied here rather than imported: the point of the assertion is that two
        implementations of the same receipt-independent formula agree. A
        `path\\tdigest` recipe (what #502 used for *blob* SHAs) would produce a
        different value and would not be reproducible from installed files.
        """
        desc, _ = release()
        recipe = "".join(f"{digest}  {path}\n"
                         for path, digest in sorted(desc.files))
        expected = "sha256:" + hashlib.sha256(recipe.encode("utf-8")).hexdigest()
        self.assertEqual(desc.manifest_digest(), expected)

    def test_the_manifest_digest_is_stable_and_order_independent(self):
        desc, _ = release()
        reversed_desc = m.Descriptor(name=desc.name, version=desc.version,
                                     commit=desc.commit,
                                     files=tuple(reversed(desc.files)),
                                     entrypoint=desc.entrypoint)
        self.assertEqual(desc.manifest_digest(),
                         reversed_desc.manifest_digest())

    def test_an_l2_artifact_becomes_a_descriptor(self):
        """The seam a real sidecar would use: #473's ARTIFACT.json, no re-typing."""
        artifact = {
            "schema": "serveros/artifact.v1",
            "name": "fleet-l2-node",
            "version": "2.1.0+sidecar",
            "source": {"entry": "fleet_mcp.node.connector",
                       "module_count": 2, "tree_sha256": "0" * 64},
            "member_sha256": {
                "fleet_mcp/node/connector.py": "ab" * 32,
                "fleet_mcp/errors.py": "cd" * 32,
            },
            "members": ["fleet_mcp/node/connector.py", "fleet_mcp/errors.py",
                        "ARTIFACT.json"],
        }
        desc = m.Descriptor.from_artifact(artifact, commit="b" * 40)
        self.assertEqual(desc.version, "2.1.0+sidecar")
        self.assertEqual(desc.file_count(), 2)
        self.assertEqual(desc.entrypoint, "fleet_mcp/node/connector.py")
        # `ARTIFACT.json` is the manifest itself and carries no digest of its
        # own, so it is not a member — exactly as #473 writes it.
        self.assertNotIn("ARTIFACT.json", dict(desc.files))
        self.assertEqual(m.check_descriptor(desc).decision, m.OK)

    def test_from_artifact_will_not_invent_a_commit(self):
        artifact = {"schema": "serveros/artifact.v1", "version": "2.1.0",
                    "source": {"entry": "fleet_mcp.errors"},
                    "member_sha256": {"fleet_mcp/errors.py": "cd" * 32},
                    "members": ["fleet_mcp/errors.py"]}
        with self.assertRaises(m.Refused):
            m.Descriptor.from_artifact(artifact, commit="")
        desc = m.Descriptor.from_artifact(artifact, commit="b" * 40)
        self.assertEqual(m.check_descriptor(desc).decision, m.OK)


# --------------------------------------------------------------------------- #
# install: a receipt inside an isolated root, or nothing at all
# --------------------------------------------------------------------------- #
class InstallWritesAReceiptInAnIsolatedRoot(SidecarTest):
    def test_install_writes_the_pinned_bytes_and_a_receipt(self):
        desc, blobs, root, result = self.installed()
        for rel, body in blobs.items():
            self.assertEqual((root / rel).read_bytes(), body)
        self.assertEqual(tuple(sorted(result.written)), tuple(sorted(blobs)))
        receipt = m.read_receipt(root)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.version, desc.version)
        self.assertEqual(receipt.commit, desc.commit)
        self.assertEqual(receipt.manifest_digest, desc.manifest_digest())
        self.assertEqual(receipt.install_root, os.path.realpath(root))
        self.assertEqual(receipt.device_ref, DEVICE)
        self.assertEqual(receipt.boot_id, BOOT)
        self.assertEqual(receipt.grant_id, "grant-1")
        self.assertEqual(receipt.previous, None)
        self.assertTrue(receipt.is_intact())

    def test_the_receipt_is_written_as_bytes_so_a_digest_survives_windows(self):
        """`write_text` would translate `\\n` to `\\r\\n` and move every digest.

        The installed bytes are compared to the pinned bytes above; this test
        makes the failure mode explicit rather than incidental.
        """
        desc, blobs, root, _ = self.installed()
        for rel, body in blobs.items():
            self.assertNotIn(b"\r\n", (root / rel).read_bytes())
            self.assertEqual(sha((root / rel).read_bytes()), dict(desc.files)[rel])

    def test_a_non_empty_root_is_refused(self):
        desc, blobs = release()
        root = self.fresh_root()
        root.mkdir(parents=True)
        (root / "somebody-elses-file.txt").write_bytes(b"keep me")
        result = m.install(desc, root, blobs, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT)
        self.assertCode(result, m.BLOCKED_ROOT_NOT_EMPTY)
        self.assertEqual((root / "somebody-elses-file.txt").read_bytes(), b"keep me")
        self.assertFalse((root / m.RECEIPT_NAME).exists())

    def test_a_legacy_or_shared_root_is_refused(self):
        desc, blobs = release()
        legacy = self.tmp() / "legacy-install"
        legacy.mkdir()
        for root in (legacy, legacy / "nested"):
            with self.subTest(root=str(root)):
                result = m.install(desc, root, blobs, grant=grant("install"),
                                   device_ref=DEVICE, boot_id=BOOT,
                                   legacy_roots=(legacy,))
                self.assertCode(result, m.BLOCKED_LEGACY_ROOT)
                self.assertEqual(list(legacy.iterdir()), [])

    def test_an_install_that_contains_a_legacy_root_is_refused(self):
        """Containment is checked both ways: a parent of Legacy is Legacy's."""
        desc, blobs = release()
        outer = self.tmp()
        legacy = outer / "legacy"
        legacy.mkdir()
        result = m.install(desc, outer, blobs, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT,
                           legacy_roots=(legacy,))
        self.assertCode(result, m.BLOCKED_LEGACY_ROOT)

    def test_a_root_that_is_a_link_is_refused(self):
        desc, blobs = release()
        base = self.tmp()
        target = base / "target"
        target.mkdir()
        link = base / "link"
        MakeLink.make(target, link, directory=True)
        result = m.install(desc, link, blobs, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT)
        self.assertCode(result, m.BLOCKED_ROOT_IS_SYMLINK)
        self.assertEqual(list(target.iterdir()), [])

    def test_a_relative_root_is_refused(self):
        desc, blobs = release()
        self.in_empty_dir()
        result = m.install(desc, Path("relative-root"), blobs,
                           grant=grant("install"), device_ref=DEVICE,
                           boot_id=BOOT)

        self.assertCode(result, m.BLOCKED_ROOT_NOT_ABSOLUTE)
        self.assertFalse(Path("relative-root").exists())

    def test_the_filesystem_root_and_the_home_directory_are_refused(self):
        desc, blobs = release()
        for root in (Path(os.path.abspath(os.sep)), Path.home()):
            with self.subTest(root=str(root)):
                result = m.install(desc, root, blobs, grant=grant("install"),
                                   device_ref=DEVICE, boot_id=BOOT)
                self.assertCode(result, m.BLOCKED_ROOT_NOT_ISOLATED)

    def test_install_without_a_grant_is_refused(self):
        desc, blobs = release()
        root = self.fresh_root()
        result = m.install(desc, root, blobs, grant=None,
                           device_ref=DEVICE, boot_id=BOOT)
        self.assertCode(result, m.BLOCKED_NO_GRANT)
        self.assertFalse(root.exists())

    def test_a_revoke_grant_does_not_authorise_an_install(self):
        desc, blobs = release()
        result = m.install(desc, self.fresh_root(), blobs,
                           grant=grant("revoke"), device_ref=DEVICE,
                           boot_id=BOOT)
        self.assertCode(result, m.BLOCKED_NO_GRANT)

    def test_a_grant_with_no_operator_is_not_a_grant(self):
        desc, blobs = release()
        result = m.install(desc, self.fresh_root(), blobs,
                           grant=grant("install", operator=""),
                           device_ref=DEVICE, boot_id=BOOT)
        self.assertCode(result, m.BLOCKED_NO_GRANT)

    def test_bytes_that_do_not_match_the_pin_are_refused_before_anything_is_written(self):
        desc, blobs = release()
        tampered = dict(blobs)
        tampered["bin/sidecar.py"] = blobs["bin/sidecar.py"] + b"# extra\n"
        root = self.fresh_root()
        result = m.install(desc, root, tampered, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT)
        self.assertCode(result, m.BLOCKED_SOURCE_DIGEST)
        self.assertEqual(result.written, ())
        self.assertFalse(root.exists(), "a refused install created the root")

    def test_a_missing_source_file_is_refused_rather_than_skipped(self):
        desc, blobs = release()
        partial = {"bin/sidecar.py": blobs["bin/sidecar.py"]}
        result = m.install(desc, self.fresh_root(), partial,
                           grant=grant("install"), device_ref=DEVICE,
                           boot_id=BOOT)
        self.assertCode(result, m.BLOCKED_SOURCE_DIGEST)

    def test_installing_over_an_install_is_refused(self):
        desc, blobs, root, _ = self.installed()
        again = m.install(desc, root, blobs, grant=grant("install"),
                          device_ref=DEVICE, boot_id=BOOT)
        self.assertCode(again, m.BLOCKED_ALREADY_INSTALLED)

    def test_a_live_writer_lock_stops_a_second_install(self):
        desc, blobs = release()
        root = self.fresh_root()
        root.mkdir(parents=True)
        (root / m.LOCK_NAME).write_bytes(b"writer=other\n")
        result = m.install(desc, root, blobs, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT)
        self.assertCode(result, m.BLOCKED_CONCURRENT_WRITER)
        self.assertEqual(sorted(p.name for p in root.iterdir()), [m.LOCK_NAME])

    def test_an_unpinned_descriptor_never_reaches_the_filesystem(self):
        desc, blobs = release(version="latest")
        root = self.fresh_root()
        result = m.install(desc, root, blobs, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT)
        self.assertCode(result, m.BLOCKED_NOT_PINNED)
        self.assertFalse(root.exists())


# --------------------------------------------------------------------------- #
# revoke: only what the receipt owns, only in the root the receipt names
# --------------------------------------------------------------------------- #
class RevokeDeletesOnlyWhatTheReceiptOwns(SidecarTest):
    def test_a_matching_receipt_removes_exactly_its_files(self):
        desc, blobs, root, _ = self.installed()
        result = self.revoke(desc, root)
        self.assertEqual(result.decision, m.OK, result.reasons)
        self.assertEqual(tuple(sorted(result.deleted)), tuple(sorted(blobs)))
        self.assertFalse(root.exists())

    def test_a_neighbour_file_outside_the_root_is_never_touched(self):
        desc, blobs, root, _ = self.installed()
        neighbour = root.parent / "neighbour.txt"
        neighbour.write_bytes(b"not mine")
        result = self.revoke(desc, root)
        self.assertEqual(result.decision, m.OK, result.reasons)
        self.assertEqual(neighbour.read_bytes(), b"not mine")

    def test_an_unpinned_descriptor_deletes_nothing(self):
        desc, blobs, root, _ = self.installed()
        floating, _ = release(version="latest")
        before = tree_state(root)
        result = self.revoke(floating, root)
        self.assertCode(result, m.BLOCKED_NOT_PINNED)
        self.assertEqual(result.deleted, ())
        self.assertEqual(tree_state(root), before)

    def test_no_receipt_means_no_deletion(self):
        desc, blobs, root, _ = self.installed()
        (root / m.RECEIPT_NAME).unlink()
        before = tree_state(root)
        result = self.revoke(desc, root)
        self.assertCode(result, m.BLOCKED_NO_RECEIPT)
        self.assertEqual(result.deleted, ())
        self.assertEqual(tree_state(root), before)

    def test_a_strange_directory_with_the_right_files_is_still_refused(self):
        """Files that merely look installed are not evidence that they were."""
        desc, blobs = release()
        root = self.tmp() / "someone-elses-dir"
        for rel, body in blobs.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        before = tree_state(root)
        result = self.revoke(desc, root)
        self.assertCode(result, m.BLOCKED_NO_RECEIPT)
        self.assertEqual(tree_state(root), before)

    def test_a_tampered_receipt_is_refused(self):
        desc, blobs, root, _ = self.installed()
        path = root / m.RECEIPT_NAME
        body = json.loads(path.read_bytes())
        body["version"] = "9.9.9"  # digest left untouched: the edit is visible
        path.write_bytes(json.dumps(body).encode("utf-8"))
        files_before = {rel: (root / rel).read_bytes() for rel in blobs}
        result = self.revoke(desc, root)
        self.assertCode(result, m.BLOCKED_RECEIPT_TAMPERED)
        self.assertEqual({rel: (root / rel).read_bytes() for rel in blobs},
                         files_before)

    def test_a_receipt_for_another_version_is_not_a_receipt_for_this_one(self):
        desc, blobs, root, _ = self.installed()
        other, _ = release(version="0.9.9")
        result = self.revoke(other, root)
        self.assertCode(result, m.BLOCKED_RECEIPT_MISMATCH)

    def test_a_receipt_naming_another_root_is_refused(self):
        desc, blobs, root, _ = self.installed()
        self.forge(root, lambda body: body.__setitem__(
            "install_root", os.path.realpath(self.tmp())))
        result = self.revoke(desc, root)
        self.assertCode(result, m.BLOCKED_RECEIPT_MISMATCH)

    def test_a_missing_owner_marker_is_refused(self):
        desc, blobs, root, _ = self.installed()
        (root / m.OWNER_MARKER_NAME).unlink()
        result = self.revoke(desc, root)
        self.assertCode(result, m.BLOCKED_OWNER_MARKER_MISSING)

    def test_an_owner_marker_that_disagrees_with_the_receipt_is_refused(self):
        desc, blobs, root, _ = self.installed()
        path = root / m.OWNER_MARKER_NAME
        marker = json.loads(path.read_bytes())
        marker["device_ref"] = "device:somebody-else"
        path.write_bytes(json.dumps(marker, sort_keys=True).encode("utf-8"))
        result = self.revoke(desc, root)
        self.assertCode(result, m.BLOCKED_OWNER_MARKER_MISMATCH)

    def test_a_file_the_user_edited_afterwards_is_refused_not_deleted(self):
        desc, blobs, root, _ = self.installed()
        (root / "bin/sidecar.py").write_bytes(blobs["bin/sidecar.py"] + b"# my edit\n")
        before = tree_state(root)
        result = self.revoke(desc, root)
        self.assertCode(result, m.BLOCKED_MODIFIED_AFTER_INSTALL)
        self.assertIn("bin/sidecar.py", result.reasons[0] if result.reasons else "")
        self.assertEqual(tree_state(root), before)

    def test_a_file_that_is_already_gone_is_refused_rather_than_half_revoked(self):
        desc, blobs, root, _ = self.installed()
        (root / "etc/sidecar.json").unlink()
        result = self.revoke(desc, root)
        self.assertCode(result, m.BLOCKED_FILE_ABSENT)
        self.assertTrue((root / "bin/sidecar.py").exists(),
                        "a refused revoke deleted the surviving file")

    def test_a_foreign_file_in_the_root_blocks_the_revoke(self):
        desc, blobs, root, _ = self.installed()
        (root / "etc/sidecar.local.json").write_bytes(b'{"mine": true}')
        result = self.revoke(desc, root)
        self.assertCode(result, m.BLOCKED_FOREIGN_PRESENT)
        self.assertTrue((root / "etc/sidecar.local.json").exists())
        self.assertTrue((root / "bin/sidecar.py").exists())

    def test_a_symlink_standing_in_for_a_managed_file_is_refused(self):
        desc, blobs, root, _ = self.installed()
        target = self.tmp() / "outside.txt"
        target.write_bytes(b"outside the root")
        (root / "etc/sidecar.json").unlink()
        MakeLink.make(target, root / "etc/sidecar.json", directory=False)
        result = self.revoke(desc, root)
        self.assertCode(result, m.BLOCKED_SYMLINK_TRAVERSAL)
        self.assertEqual(target.read_bytes(), b"outside the root")
        self.assertTrue((root / "bin/sidecar.py").exists())

    def test_a_linked_directory_on_the_way_is_refused(self):
        desc, blobs, root, _ = self.installed()
        outside = self.tmp() / "outside-dir"
        outside.mkdir()
        (outside / "sidecar.json").write_bytes(b"outside")
        shutil.rmtree(root / "etc")
        MakeLink.make(outside, root / "etc", directory=True)
        result = self.revoke(desc, root)
        self.assertCode(result, m.BLOCKED_SYMLINK_TRAVERSAL)
        self.assertEqual((outside / "sidecar.json").read_bytes(), b"outside")

    def test_a_receipt_path_that_leaves_the_root_is_refused(self):
        desc, blobs, root, _ = self.installed()
        self.forge(root, lambda body: body["files"][0].__setitem__(
            "path", "../../escape.txt"))
        outside = root.parent / "escape.txt"
        outside.write_bytes(b"keep")
        result = self.revoke(desc, root)
        self.assertCode(result, m.BLOCKED_OUTSIDE_INSTALL_ROOT)
        self.assertTrue(outside.exists())

    def test_a_receipt_path_that_is_absolute_is_refused(self):
        desc, blobs, root, _ = self.installed()
        self.forge(root, lambda body: body["files"][0].__setitem__(
            "path", str(self.tmp() / "absolute.txt")))
        result = self.revoke(desc, root)
        self.assertEqual(result.decision, m.BLOCKED, result.reasons)
        self.assertIn(result.code, (m.BLOCKED_OUTSIDE_INSTALL_ROOT,
                                    m.BLOCKED_DESCRIPTOR_PATH))

    def test_a_revoked_root_that_is_a_legacy_root_is_refused(self):
        """Even a valid receipt cannot make a Legacy root revocable."""
        desc, blobs, root, _ = self.installed()
        result = self.revoke(desc, root, legacy_roots=(root.parent,))
        self.assertCode(result, m.BLOCKED_LEGACY_ROOT)
        self.assertTrue((root / "bin/sidecar.py").exists())

    def test_a_revoke_without_a_grant_is_refused(self):
        desc, blobs, root, _ = self.installed()
        result = m.revoke(desc, root, grant=None, device_ref=DEVICE, boot_id=BOOT)
        self.assertCode(result, m.BLOCKED_NO_GRANT)
        self.assertTrue((root / "bin/sidecar.py").exists())

    def test_an_install_grant_does_not_authorise_a_revoke(self):
        desc, blobs, root, _ = self.installed()
        result = self.revoke(desc, root, grant=grant("install"))
        self.assertCode(result, m.BLOCKED_NO_GRANT)

    def test_a_grant_for_another_device_is_refused(self):
        desc, blobs, root, _ = self.installed()
        result = self.revoke(desc, root, grant=grant("revoke", device="device:other"))
        self.assertCode(result, m.BLOCKED_IDENTITY_MISMATCH)

    def test_another_device_is_refused_even_with_a_valid_grant(self):
        desc, blobs, root, _ = self.installed()
        result = self.revoke(desc, root, device_ref="device:somewhere-else")
        self.assertCode(result, m.BLOCKED_IDENTITY_MISMATCH)

    def test_another_boot_is_refused(self):
        desc, blobs, root, _ = self.installed()
        result = self.revoke(desc, root, boot_id="boot-somewhere-else")
        self.assertCode(result, m.BLOCKED_BOOT_MISMATCH)

    def test_a_parallel_writer_blocks_the_revoke(self):
        desc, blobs, root, _ = self.installed()
        (root / m.LOCK_NAME).write_bytes(b"writer=other\n")
        result = self.revoke(desc, root)
        self.assertCode(result, m.BLOCKED_CONCURRENT_WRITER)
        self.assertTrue((root / "bin/sidecar.py").exists())


# --------------------------------------------------------------------------- #
# a second revoke changes nothing, and still refuses to act without evidence
# --------------------------------------------------------------------------- #
class RevokeIsIdempotentButRefusesWithoutEvidence(SidecarTest):
    def test_the_second_revoke_deletes_nothing_and_does_not_raise(self):
        desc, blobs, root, _ = self.installed()
        first = self.revoke(desc, root)
        self.assertEqual(first.decision, m.OK, first.reasons)
        after_first = tree_state(root) if root.exists() else {}
        second = self.revoke(desc, root)
        self.assertEqual(second.decision, m.BLOCKED)
        self.assertEqual(second.code, m.BLOCKED_NO_RECEIPT)
        self.assertEqual(second.deleted, ())
        self.assertEqual(tree_state(root) if root.exists() else {}, after_first)

    def test_idempotence_is_about_the_tree_not_about_saying_ok(self):
        """A revoke that reports OK without evidence would be the dishonest one."""
        desc, blobs, root, _ = self.installed()
        self.revoke(desc, root)
        second = self.revoke(desc, root)
        self.assertNotEqual(second.decision, m.OK)
        self.assertEqual(second.deleted, ())


# --------------------------------------------------------------------------- #
# upgrade and restore
# --------------------------------------------------------------------------- #
class UpgradeAndRestore(SidecarTest):
    def test_an_upgrade_replaces_bytes_and_records_what_it_replaced(self):
        v1, blobs1, v2, blobs2, root = self.upgraded()
        for rel, body in blobs2.items():
            self.assertEqual((root / rel).read_bytes(), body)
        receipt = m.read_receipt(root)
        self.assertEqual(receipt.version, "0.2.0")
        self.assertIsNotNone(receipt.previous)
        self.assertEqual(receipt.previous.version, "0.1.0")
        self.assertEqual(receipt.previous.manifest_digest, v1.manifest_digest())
        self.assertTrue((root / m.BACKUP_DIR / "0.1.0" / "bin/sidecar.py").exists())

    def test_an_upgrade_over_edited_bytes_is_refused(self):
        v1, blobs1, root, _ = self.installed()
        (root / "etc/sidecar.json").write_bytes(b"edited")
        v2, blobs2 = release(SIDECAR_V2, version="0.2.0", commit="c" * 40)
        result = m.install(v2, root, blobs2, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT,
                           replace=m.read_receipt(root))
        self.assertCode(result, m.BLOCKED_MODIFIED_AFTER_INSTALL)
        self.assertEqual(m.read_receipt(root).version, "0.1.0")

    def test_only_one_level_of_history_is_claimed(self):
        """Two upgrades would overwrite the single backup the receipt names."""
        v1, blobs1, v2, blobs2, root = self.upgraded()
        v3, blobs3 = release(SIDECAR_V2, version="0.3.0", commit="d" * 40)
        result = m.install(v3, root, blobs3, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT,
                           replace=m.read_receipt(root))
        self.assertCode(result, m.BLOCKED_UPGRADE_CHAIN)

    def test_restore_puts_the_previous_bytes_back_exactly(self):
        v1, blobs1, v2, blobs2, root = self.upgraded()
        result = m.restore(v1, root, grant=grant("restore"),
                           device_ref=DEVICE, boot_id=BOOT)
        self.assertEqual(result.decision, m.OK, result.reasons)
        for rel, body in blobs1.items():
            self.assertEqual((root / rel).read_bytes(), body)
        self.assertFalse((root / "etc/added-in-020.json").exists(),
                         "a file the older version never shipped survived")
        receipt = m.read_receipt(root)
        self.assertEqual(receipt.version, "0.1.0")
        self.assertEqual(receipt.manifest_digest, v1.manifest_digest())
        self.assertIsNone(receipt.previous)

    def test_restore_of_a_version_the_receipt_does_not_remember_is_refused(self):
        v1, blobs1, v2, blobs2, root = self.upgraded()
        other, _ = release(version="0.0.9", commit="e" * 40)
        result = m.restore(other, root, grant=grant("restore"),
                           device_ref=DEVICE, boot_id=BOOT)
        self.assertCode(result, m.BLOCKED_RECEIPT_MISMATCH)
        self.assertEqual(m.read_receipt(root).version, "0.2.0")

    def test_restore_with_a_tampered_backup_is_refused(self):
        v1, blobs1, v2, blobs2, root = self.upgraded()
        backup = root / m.BACKUP_DIR / "0.1.0" / "bin/sidecar.py"
        backup.write_bytes(b"not the bytes that were backed up\n")
        before = tree_state(root)
        result = m.restore(v1, root, grant=grant("restore"),
                           device_ref=DEVICE, boot_id=BOOT)
        self.assertCode(result, m.BLOCKED_BACKUP_MODIFIED)
        self.assertEqual(tree_state(root), before)

    def test_restore_needs_a_restore_grant(self):
        v1, blobs1, v2, blobs2, root = self.upgraded()
        result = m.restore(v1, root, grant=grant("revoke"),
                           device_ref=DEVICE, boot_id=BOOT)
        self.assertCode(result, m.BLOCKED_NO_GRANT)

    def test_a_revoked_upgrade_still_leaves_no_trace(self):
        v1, blobs1, v2, blobs2, root = self.upgraded()
        result = self.revoke(v2, root)
        self.assertEqual(result.decision, m.OK, result.reasons)
        self.assertFalse(root.exists())
        self.assertEqual(list(root.parent.iterdir()), [])


# --------------------------------------------------------------------------- #
# restore writes, so restore has to refuse, one shape per guard
# --------------------------------------------------------------------------- #
class RestoreRefusesWhatRevokeRefuses(SidecarTest):
    """Each restore guard is driven to a refusal of its **own** code.

    Restore writes bytes back into a root, so a restore that can be talked into
    writing outside it is the same defect as a revoke that deletes outside it.
    These tests exist because the mutation probe found otherwise: with only the
    three original restore tests, twelve of the fifteen guards were *executed*
    but not *depended on* — deleting one from `RESTORE_GUARDS` changed no test's
    outcome. Guards that cannot be observed are guards that can be removed.

    Asserting the exact code (not merely `BLOCKED`) is load-bearing too: several
    of these shapes are caught by a neighbouring guard if the intended one is
    removed, so a test satisfied by any refusal could not tell them apart.
    """

    def restore(self, desc, root, **kw):
        args = dict(grant=grant("restore"), device_ref=DEVICE, boot_id=BOOT)
        args.update(kw)
        return m.restore(desc, root, **args)

    def test_an_unpinned_descriptor_is_refused(self):
        v1, _blobs1, _v2, _blobs2, root = self.upgraded()
        floating, _ = release(version="latest")
        before = tree_state(root)
        result = self.restore(floating, root)
        self.assertCode(result, m.BLOCKED_NOT_PINNED)
        self.assertEqual(tree_state(root), before)

    def test_a_relative_root_is_refused(self):
        v1, _blobs1, _v2, _blobs2, _root = self.upgraded()
        self.in_empty_dir()
        result = self.restore(v1, Path("relative-root"))
        self.assertCode(result, m.BLOCKED_ROOT_NOT_ABSOLUTE)
        self.assertFalse(Path("relative-root").exists())

    def test_a_root_inside_a_legacy_root_is_refused(self):
        v1, _blobs1, _v2, _blobs2, root = self.upgraded()
        before = tree_state(root)
        result = self.restore(v1, root, legacy_roots=[root.parent])
        self.assertCode(result, m.BLOCKED_LEGACY_ROOT)
        self.assertEqual(tree_state(root), before)

    def test_a_directory_without_a_receipt_is_refused(self):
        v1, _blobs = release()
        empty = self.tmp() / "empty"
        empty.mkdir()
        result = self.restore(v1, empty)
        self.assertCode(result, m.BLOCKED_NO_RECEIPT)
        self.assertEqual(list(empty.iterdir()), [])

    def test_a_receipt_edited_without_its_digest_is_refused(self):
        v1, _blobs1, _v2, _blobs2, root = self.upgraded()
        path = root / m.RECEIPT_NAME
        path.write_bytes(path.read_bytes().replace(b'"0.2.0"', b'"0.2.1"'))
        before = tree_state(root)
        result = self.restore(v1, root)
        self.assertCode(result, m.BLOCKED_RECEIPT_TAMPERED)
        self.assertEqual(tree_state(root), before)

    def test_a_receipt_that_names_another_root_is_refused(self):
        """Forged completely — receipt digest *and* marker — so only this is left."""
        v1, _blobs1, _v2, _blobs2, root = self.upgraded()

        def elsewhere(body):
            body["install_root"] = str(root.parent / "some-other-root")

        self.forge(root, elsewhere)
        before = tree_state(root)
        result = self.restore(v1, root)
        self.assertCode(result, m.BLOCKED_RECEIPT_MISMATCH)
        self.assertEqual(tree_state(root), before)

    def test_a_missing_ownership_marker_is_refused(self):
        v1, _blobs1, _v2, _blobs2, root = self.upgraded()
        (root / m.OWNER_MARKER_NAME).unlink()
        before = tree_state(root)
        result = self.restore(v1, root)
        self.assertCode(result, m.BLOCKED_OWNER_MARKER_MISSING)
        self.assertEqual(tree_state(root), before)

    def test_another_device_is_refused(self):
        v1, _blobs1, _v2, _blobs2, root = self.upgraded()
        before = tree_state(root)
        result = self.restore(v1, root, device_ref="device:somebody-else")
        self.assertCode(result, m.BLOCKED_IDENTITY_MISMATCH)
        self.assertEqual(tree_state(root), before)

    def test_another_boot_is_refused(self):
        v1, _blobs1, _v2, _blobs2, root = self.upgraded()
        before = tree_state(root)
        result = self.restore(v1, root, boot_id="boot-after-reboot")
        self.assertCode(result, m.BLOCKED_BOOT_MISMATCH)
        self.assertEqual(tree_state(root), before)

    def test_a_live_writer_is_refused(self):
        v1, _blobs1, _v2, _blobs2, root = self.upgraded()
        (root / m.LOCK_NAME).write_bytes(b"writer=someone-else\n")
        before = tree_state(root)
        result = self.restore(v1, root)
        self.assertCode(result, m.BLOCKED_CONCURRENT_WRITER)
        self.assertEqual(tree_state(root), before)

    def test_a_link_on_the_way_to_a_file_is_refused(self):
        v1, _blobs1, _v2, _blobs2, root = self.upgraded()
        outside = self.tmp() / "outside"
        shutil.move(str(root / "bin"), str(outside))
        MakeLink.make(outside, root / "bin", directory=True)
        result = self.restore(v1, root)
        # A link is the *precise* diagnosis. If the symlink guard were dropped,
        # the containment guard would still refuse — with a different code, which
        # is why this test asserts this one.
        self.assertCode(result, m.BLOCKED_SYMLINK_TRAVERSAL)

    def test_a_receipt_path_that_leaves_the_root_is_refused(self):
        v1, _blobs1, _v2, _blobs2, root = self.upgraded()

        def escape(body):
            files = list(body["files"])
            files.append({"path": "../escape.py", "sha256": sha(b"x"), "size": 1})
            body["files"] = files

        self.forge(root, escape)
        before = tree_state(root)
        result = self.restore(v1, root)
        self.assertCode(result, m.BLOCKED_OUTSIDE_INSTALL_ROOT)
        self.assertEqual(tree_state(root), before)

    def test_a_file_edited_after_install_is_refused(self):
        v1, _blobs1, _v2, _blobs2, root = self.upgraded()
        (root / "etc/sidecar.json").write_bytes(b"someone else's edit")
        result = self.restore(v1, root)
        self.assertCode(result, m.BLOCKED_MODIFIED_AFTER_INSTALL)
        self.assertEqual((root / "etc/sidecar.json").read_bytes(),
                         b"someone else's edit")

    def test_a_file_that_is_already_gone_is_refused(self):
        v1, _blobs1, _v2, _blobs2, root = self.upgraded()
        (root / "etc/added-in-020.json").unlink()
        before = tree_state(root)
        result = self.restore(v1, root)
        self.assertCode(result, m.BLOCKED_FILE_ABSENT)
        self.assertEqual(tree_state(root), before)

    def test_a_foreign_file_is_refused(self):
        v1, _blobs1, _v2, _blobs2, root = self.upgraded()
        (root / "etc" / "stranger.json").write_bytes(b'{"not": "mine"}\n')
        before = tree_state(root)
        result = self.restore(v1, root)
        self.assertCode(result, m.BLOCKED_FOREIGN_PRESENT)
        self.assertEqual(tree_state(root), before)

    def test_the_two_pipelines_do_not_drift(self):
        """Restore is revoke's list, minus the check only a revoke can make.

        `_guard_receipt_match` compares the receipt with the descriptor being
        undone — during a restore the root holds the *newer* version, so that
        comparison would refuse every correct restore. Its replacement,
        `_guard_previous_match`, is asserted to be there instead. Everything
        else must appear in both, in the same order, so a guard added to one
        cannot be silently missing from the other.
        """
        revoke_names = list(m.REVOKE_GUARDS)
        restore_names = list(m.RESTORE_GUARDS)
        self.assertIn("_guard_receipt_match", revoke_names)
        self.assertNotIn("_guard_receipt_match", restore_names)
        shared = [name for name in revoke_names if name != "_guard_receipt_match"]
        self.assertEqual([name for name in restore_names if name in shared], shared)
        extra = set(restore_names) - set(shared)
        self.assertEqual(extra, {"_guard_install_root_match",
                                 "_guard_previous_match", "_guard_backup"})


# --------------------------------------------------------------------------- #
# the Legacy contract does not move while the sidecar does
# --------------------------------------------------------------------------- #
class FakeLegacyGateway:
    """The V1 control path, as ChatGPT drives it: read, read, send once.

    `send` is idempotent **by intent key**. It appends to `deliveries` once per
    key, so "one intent, one delivery" is measurable across a whole lifecycle
    without sending twice to prove it.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.deliveries: list[str] = []
        self.run_status = "running"
        self.duplicate_delivery = False

    def get_session(self, key: str) -> dict:
        self.calls.append(("get_session", key))
        return {"session": key, "state": "open", "owner": "chatgpt"}

    def get_run(self, key: str) -> dict:
        self.calls.append(("get_run", key))
        return {"run": key, "status": self.run_status}

    def send(self, key: str) -> dict:
        self.calls.append(("send", key))
        if key not in self.deliveries or self.duplicate_delivery:
            self.deliveries.append(key)
        return {"intent": key, "receipt": f"receipt-{key}"}


class LegacyContractDoesNotMove(SidecarTest):
    def lifecycle(self, gateway):
        """install → failed install → upgrade → restore → revoke, in order."""
        v1, blobs1 = release()
        root = self.fresh_root()
        steps = []
        outcomes = []

        outcomes.append(m.install(v1, root, blobs1, grant=grant("install"),
                                  device_ref=DEVICE, boot_id=BOOT))
        steps.append("install")

        broken = dict(blobs1)
        broken["bin/sidecar.py"] = b"not the pinned bytes\n"
        outcomes.append(m.install(v1, self.tmp() / "failed-root", broken,
                                  grant=grant("install"), device_ref=DEVICE,
                                  boot_id=BOOT))
        steps.append("install-failure")

        v2, blobs2 = release(SIDECAR_V2, version="0.2.0", commit="c" * 40)
        outcomes.append(m.install(v2, root, blobs2, grant=grant("install"),
                                  device_ref=DEVICE, boot_id=BOOT,
                                  replace=m.read_receipt(root)))
        steps.append("upgrade")

        outcomes.append(m.restore(v1, root, grant=grant("restore"),
                                  device_ref=DEVICE, boot_id=BOOT))
        steps.append("restore")

        outcomes.append(self.revoke(v1, root))
        steps.append("revoke")
        return steps, outcomes

    def test_the_three_verbs_read_the_same_after_every_step(self):
        gateway = FakeLegacyGateway()
        gateway.send("intent-1")
        baseline = m.observe_legacy(gateway, session_key="s1", run_key="r1")

        v1, blobs1 = release()
        v2, blobs2 = release(SIDECAR_V2, version="0.2.0", commit="c" * 40)
        root = self.fresh_root()

        def do_install():
            return m.install(v1, root, blobs1, grant=grant("install"),
                             device_ref=DEVICE, boot_id=BOOT)

        def do_failure():
            return m.install(v1, self.tmp() / "failed-root",
                             {"bin/sidecar.py": b"wrong\n"},
                             grant=grant("install"), device_ref=DEVICE,
                             boot_id=BOOT)

        def do_upgrade():
            return m.install(v2, root, blobs2, grant=grant("install"),
                             device_ref=DEVICE, boot_id=BOOT,
                             replace=m.read_receipt(root))

        drift: dict[str, tuple[str, ...]] = {}
        outcomes: dict[str, object] = {}
        for name, act in (("install", do_install),
                          ("install-failure", do_failure),
                          ("upgrade", do_upgrade)):
            outcomes[name] = act()
            after = m.observe_legacy(gateway, session_key="s1", run_key="r1")
            drift[name] = m.legacy_drift(baseline, after)
        self.assertEqual(drift, {"install": (), "install-failure": (),
                                 "upgrade": ()})
        self.assertEqual(outcomes["install-failure"].decision, m.BLOCKED)
        self.assertEqual(gateway.deliveries, ["intent-1"],
                         "the sidecar lifecycle produced a second delivery")

    def test_the_full_lifecycle_holds_the_contract_still(self):
        gateway = FakeLegacyGateway()
        gateway.send("intent-1")
        baseline = m.observe_legacy(gateway, session_key="s1", run_key="r1")
        steps, outcomes = self.lifecycle(gateway)
        self.assertEqual(len(steps), len(outcomes))
        self.assertEqual([o.decision for o in outcomes],
                         [m.OK, m.BLOCKED, m.OK, m.OK, m.OK],
                         [o.code for o in outcomes])
        after = m.observe_legacy(gateway, session_key="s1", run_key="r1")
        self.assertEqual(m.legacy_drift(baseline, after), ())
        self.assertEqual(gateway.deliveries, ["intent-1"])
        self.assertEqual(
            [name for name, _ in gateway.calls if name == "send"], ["send"])

    def test_a_changed_run_is_detected(self):
        """The positive control: a gate that can only say "same" proves nothing."""
        gateway = FakeLegacyGateway()
        baseline = m.observe_legacy(gateway, session_key="s1", run_key="r1")
        gateway.run_status = "cancelled"
        after = m.observe_legacy(gateway, session_key="s1", run_key="r1")
        self.assertEqual(m.legacy_drift(baseline, after), ("get_run",))

    def test_a_second_delivery_is_detected(self):
        gateway = FakeLegacyGateway()
        gateway.send("intent-1")
        baseline = m.observe_legacy(gateway, session_key="s1", run_key="r1")
        gateway.duplicate_delivery = True
        gateway.send("intent-1")
        after = m.observe_legacy(gateway, session_key="s1", run_key="r1")
        self.assertEqual(m.legacy_drift(baseline, after), ("send",))

    def test_observe_legacy_never_sends(self):
        gateway = FakeLegacyGateway()
        m.observe_legacy(gateway, session_key="s1", run_key="r1")
        self.assertEqual([name for name, _ in gateway.calls],
                         ["get_session", "get_run"])


# --------------------------------------------------------------------------- #
# no network, no shell, no writes outside the root
# --------------------------------------------------------------------------- #
class NothingOutsideTheRootIsTouched(SidecarTest):
    def test_no_socket_no_urlopen_no_child_process_during_the_lifecycle(self):
        attempted: list[str] = []

        def refuse(*args, **kwargs):
            attempted.append(repr(args)[:80])
            raise AssertionError(f"the sidecar lifecycle tried to reach out: {args!r}")

        with mock.patch("socket.socket", refuse), \
                mock.patch("socket.create_connection", refuse), \
                mock.patch("urllib.request.urlopen", refuse), \
                mock.patch("subprocess.Popen", refuse), \
                mock.patch("os.system", refuse), \
                mock.patch("os.execv", refuse):
            gateway = FakeLegacyGateway()
            gateway.send("intent-1")
            self.lifecycle_for_quiet_test()
        self.assertEqual(attempted, [])

    def lifecycle_for_quiet_test(self):
        v1, blobs1 = release()
        root = self.fresh_root()
        out = m.install(v1, root, blobs1, grant=grant("install"),
                        device_ref=DEVICE, boot_id=BOOT)
        self.assertEqual(out.decision, m.OK, out.reasons)
        v2, blobs2 = release(SIDECAR_V2, version="0.2.0", commit="c" * 40)
        out = m.install(v2, root, blobs2, grant=grant("install"),
                        device_ref=DEVICE, boot_id=BOOT,
                        replace=m.read_receipt(root))
        self.assertEqual(out.decision, m.OK, out.reasons)
        out = m.restore(v1, root, grant=grant("restore"),
                        device_ref=DEVICE, boot_id=BOOT)
        self.assertEqual(out.decision, m.OK, out.reasons)
        out = self.revoke(v1, root)
        self.assertEqual(out.decision, m.OK, out.reasons)

    def test_the_parent_directory_is_left_as_it_was(self):
        parent = self.tmp()
        sentinel = parent / "sentinel.txt"
        sentinel.write_bytes(b"untouched")
        desc, blobs = release()
        root = parent / "root"
        m.install(desc, root, blobs, grant=grant("install"),
                  device_ref=DEVICE, boot_id=BOOT)
        self.revoke(desc, root)
        self.assertEqual(sorted(p.name for p in parent.iterdir()),
                         ["sentinel.txt"])
        self.assertEqual(sentinel.read_bytes(), b"untouched")

    def test_the_module_imports_nothing_that_could_reach_out(self):
        """An AST check, not a grep: the guard is about what the module can do."""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(
            imported & {"socket", "urllib", "http", "requests", "ssl",
                        "ftplib", "telnetlib", "shutil"},
            set(), f"the module can reach out: {sorted(imported)}")

    def test_the_one_external_process_is_the_read_only_git_reader(self):
        """`subprocess` is allowed only inside `_git`, and `_git` only reads.

        Banning the import outright would be a lie about how a descriptor reads
        a commit; allowing it anywhere would mean the revoke could shell out.
        This asserts the narrower thing that is actually true: every reference
        to `subprocess` lives in `_git`, and `_git` refuses any verb that is not
        in `READ_ONLY_GIT`.
        """
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        owners: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                name = getattr(inner, "id", None) or getattr(inner, "attr", None)
                if name == "subprocess":
                    owners.add(node.name)
        self.assertEqual(owners, {"_git"})
        self.assertEqual(set(m.READ_ONLY_GIT),
                         {"rev-parse", "cat-file", "ls-tree", "grep"})
        with self.assertRaises(AssertionError):
            m._git(ROOT, ["rm", "-rf", "x"])

    def test_the_module_has_no_bulk_delete_call(self):
        """`rm -rf` is the thing being replaced, so it must not be in the code."""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        forbidden = {"rmtree", "removedirs", "system", "popen", "execv",
                     "rmdir_tree"}
        hits: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                hits.append(node.attr)
            elif isinstance(node, ast.Name) and node.id in forbidden:
                hits.append(node.id)
        self.assertEqual(hits, [])


# --------------------------------------------------------------------------- #
# the real sidecar: BLOCKED_UPSTREAM, not a fabricated pass
# --------------------------------------------------------------------------- #
class TheRealSidecarIsBlockedNotSigned(SidecarTest):
    SIDECAR_FILES = (
        "fleet_mcp/deploy/next_tailscale_gate.py",
        "fleet_mcp/deploy/next_tailscale_plan.example.json",
    )

    def test_the_two_files_are_the_ones_wave16_reported(self):
        desc = m.descriptor_from_git(ROOT, "HEAD", self.SIDECAR_FILES,
                                     version="0.1.0+wave17", name="sidecar")
        self.assertEqual(desc.file_count(), 2)
        self.assertEqual(tuple(path for path, _ in desc.files),
                         self.SIDECAR_FILES)
        self.assertEqual(m.check_descriptor(desc).decision, m.OK,
                         m.check_descriptor(desc).reasons)

    def test_the_manifest_is_reproducible_from_git(self):
        first = m.descriptor_from_git(ROOT, "HEAD", self.SIDECAR_FILES,
                                      version="0.1.0+wave17")
        second = m.descriptor_from_git(ROOT, "HEAD", self.SIDECAR_FILES,
                                       version="0.1.0+wave17")
        self.assertEqual(first.manifest_digest(), second.manifest_digest())

    def test_the_upstream_verdict_is_blocked_and_names_what_is_missing(self):
        verdict = m.upstream_verdict(ROOT, "HEAD", self.SIDECAR_FILES)
        self.assertEqual(verdict.decision, m.BLOCKED)
        self.assertEqual(verdict.code, m.BLOCKED_UPSTREAM)
        self.assertFalse(verdict.receipt_found,
                         "a receipt was reported without one existing")
        self.assertEqual(tuple(verdict.files), self.SIDECAR_FILES)
        self.assertTrue(any("receipt" in reason.lower()
                            for reason in verdict.reasons), verdict.reasons)
        self.assertGreaterEqual(len(verdict.required_changes), 3)

    def test_the_upstream_verdict_says_a_real_revoke_is_not_verified(self):
        """The disclaimer is asserted positively, so deleting it fails the test."""
        verdict = m.upstream_verdict(ROOT, "HEAD", self.SIDECAR_FILES)
        something = " ".join(verdict.reasons + verdict.required_changes).lower()
        self.assertIn("not verified", something)
        self.assertIn("not a real-device pass", something)


class TheReceiptCheckLooksForAnArtifactNotAString(SidecarTest):
    """`git grep <schema>` matches the module that *defines* the schema.

    That is how this shipped: `upstream_verdict()` grep'd the commit for the
    receipt schema string, so on any commit containing this module it found "a
    receipt" — its own source code — and reported `receipt_found=True`. It
    passed locally because the module was still untracked, and `git grep <rev>`
    reads a **commit**, not a worktree. CI was right and the local run was
    measuring nothing, which is the whole reason these tests build their own
    repository: the answer must depend on the fixture, not on what happens to be
    staged in this checkout.
    """

    SIDECAR = {
        "fleet_mcp/deploy/next_tailscale_gate.py": b"# gate\n",
        "fleet_mcp/deploy/next_tailscale_plan.example.json": b"{}\n",
    }
    FILES = tuple(SIDECAR)

    def repo_with(self, extra: dict[str, bytes]) -> Path:
        repo = self.tmp() / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        for rel, body in {**self.SIDECAR, **extra}.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        git(repo, "add", "-A")
        git(repo, "-c", "user.name=wave17",
            "-c", "user.email=wave17@example.invalid",
            "commit", "-q", "--no-gpg-sign", "-m", "fixture")
        return repo

    def test_a_file_that_mentions_the_schema_is_not_a_receipt(self):
        """The defect, reproduced: a reader mentioning the schema is a mention."""
        repo = self.repo_with({"integrations/reader.py":
                               f'SCHEMA = "{m.RECEIPT_SCHEMA}"\n'.encode("utf-8")})
        verdict = m.upstream_verdict(repo, "HEAD", self.FILES)
        self.assertEqual(verdict.receipt_found, False, verdict.reasons)
        self.assertEqual(verdict.code, m.BLOCKED_UPSTREAM)

    def test_a_real_receipt_is_found_and_changes_the_answer(self):
        """A gate that can only say one thing is not a gate."""
        body = json.dumps({"schema": m.RECEIPT_SCHEMA, "version": "0.1.0"}).encode()
        repo = self.repo_with({m.RECEIPT_NAME: body})
        verdict = m.upstream_verdict(repo, "HEAD", self.FILES)
        self.assertTrue(verdict.receipt_found, verdict.reasons)
        self.assertEqual(verdict.code, m.BLOCKED_NOT_VERIFIED)
        self.assertEqual(verdict.required_changes, ())

    def test_a_file_named_like_a_receipt_but_empty_is_not_one(self):
        repo = self.repo_with({m.RECEIPT_NAME: b'{"schema": "something/else.v1"}\n'})
        verdict = m.upstream_verdict(repo, "HEAD", self.FILES)
        self.assertFalse(verdict.receipt_found)
        self.assertEqual(verdict.code, m.BLOCKED_UPSTREAM)
        self.assertTrue(any("named like a receipt" in reason
                            for reason in verdict.reasons), verdict.reasons)

    def test_a_receipt_under_another_name_is_not_counted(self):
        """Narrow on purpose: `revoke()` only ever reads `<root>/RECEIPT_NAME`."""
        body = json.dumps({"schema": m.RECEIPT_SCHEMA}).encode()
        repo = self.repo_with({"state/receipt-copy.json": body})
        self.assertFalse(m.upstream_verdict(repo, "HEAD", self.FILES).receipt_found)


class TheGuardPipelineIsResolvedAtCallTime(SidecarTest):
    """A guard tuple bound at import cannot be probed, so say so in a test.

    The mutation probe works by rebinding one `_guard_*` name and asking which
    tests turn red. If `revoke()` iterated a tuple built at import time, every
    rebinding would be a silent no-op and the probe would report "nothing
    depends on this guard" for all thirteen of them — a clean bill of health
    for the entire pipeline that is really a measurement of nothing. The first
    draft of this module had exactly that defect; these tests are why it cannot
    come back without a red run.
    """

    def test_every_declared_guard_resolves_to_a_function(self):
        for names, resolve in ((m.REVOKE_GUARDS, m.revoke_guards),
                               (m.RESTORE_GUARDS, m.restore_guards)):
            with self.subTest(pipeline=resolve.__name__):
                resolved = resolve()
                self.assertEqual(len(resolved), len(names))
                for guard in resolved:
                    self.assertTrue(callable(guard), guard)

    def test_the_declared_order_is_the_order_that_runs(self):
        """Symlink is judged before containment: the precise diagnosis wins."""
        names = list(m.REVOKE_GUARDS)
        self.assertLess(names.index("_guard_symlink"),
                        names.index("_guard_containment"))
        self.assertEqual([g.__name__ for g in m.revoke_guards()], names)
        self.assertEqual([g.__name__ for g in m.restore_guards()],
                         list(m.RESTORE_GUARDS))

    def test_rebinding_one_guard_changes_what_revoke_does(self):
        desc, _blobs, root, _result = self.installed()
        original = m._guard_foreign
        m._guard_foreign = lambda ctx: m.Verdict(
            m.BLOCKED, "BLOCKED_PROBE", ("this guard was replaced",))
        self.addCleanup(setattr, m, "_guard_foreign", original)

        result = self.revoke(desc, root)

        self.assertCode(result, "BLOCKED_PROBE")
        self.assertTrue(root.is_dir(), "a refused revoke must delete nothing")


class OwnerInputsAreNamed(unittest.TestCase):
    def test_the_owner_inputs_are_named_and_none_is_invented(self):
        inputs = m.owner_inputs()
        self.assertGreaterEqual(len(inputs), 5)
        joined = " ".join(inputs).lower()
        for expected in ("grant", "device_ref", "boot_id", "receipt",
                         "install_root"):
            self.assertIn(expected, joined)

    def test_the_cli_describes_the_pinned_sidecar(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = m.main(["--describe", "--ref", "HEAD", "--repo", str(ROOT)])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["revoke"]["decision"], m.BLOCKED)
        self.assertEqual(payload["revoke"]["code"], m.BLOCKED_UPSTREAM)
        self.assertEqual(payload["descriptor"]["file_count"], 2)

    def test_the_cli_writes_utf8_whatever_the_console_code_page_is(self):
        """The payload is only useful to a reader that parses it as JSON.

        On a GBK console the em dash in the refusal reasons came out as
        0xA1 0xAA and `json.load` on the bytes failed — so this is a regression
        test over real bytes, not over a string.
        """
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        with contextlib.redirect_stdout(stream):
            code = m.main(["--describe", "--ref", "HEAD", "--repo", str(ROOT)])
            stream.flush()
        self.assertEqual(code, 0)
        payload = json.loads(raw.getvalue().decode("utf-8"))
        self.assertEqual(payload["revoke"]["code"], m.BLOCKED_UPSTREAM)


if __name__ == "__main__":
    unittest.main()
