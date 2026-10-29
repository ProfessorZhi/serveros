#!/usr/bin/env python3
"""Wave 18: an install writer whose receipt #506's revoke actually consumes.

Every test here runs in a `tempfile` directory and installs the **real** two
files, read out of a git commit — never a stand-in, and never a copy taken from
the worktree, because a worktree can carry different bytes than the commit and
the point of a pin is which of the two it names. `Artifact` checks that the
thing being installed is still the real sidecar, so a future empty file cannot
make the rest of this suite pass for nothing.

`IndependentProcesses` runs `installer.py` as a **separate Python process**, so
that "the receipt an install wrote is consumed by a revoke in another process"
is a fact about files on disk rather than about one interpreter's memory.

What is deliberately not here: no device, no service manager, no port, no
socket, no credentials, no removal of anything this round did not create. The
only deletions are of files these tests just installed, under `tempfile`.
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

#: The module under test. `m` is also the name the mutation probe
#: (`integrations/fleet-next-wave18-sidecar-installer/probe_guards.py`) rebinds
#: attributes on, which is why it lives at module level.
MODULE_PATH = (Path(__file__).resolve().parents[1] / "integrations"
               / "fleet-next-wave18-sidecar-installer" / "installer.py")

_spec = importlib.util.spec_from_file_location("sidecar_installer", str(MODULE_PATH))
m = sys.modules.get("sidecar_installer")
if m is None:
    m = importlib.util.module_from_spec(_spec)
    sys.modules["sidecar_installer"] = m
    _spec.loader.exec_module(m)

up = m._upstream()

#: The Wave-19 driver: one sidecar operation per process, with a real kill point.
#: Used here only to leave a genuine interrupted transaction for the interop
#: cases — an in-process exception would run the engine's own compensation.
CHILD = Path(__file__).resolve().parent / "wave19_child.py"

V1 = "1.0.0+test"
V2 = "2.0.0+test"
V3 = "3.0.0+test"
DEVICE = "fj-pc-01"
BOOT = "boot-0f31c2"
OTHER_BOOT = "a-new-boot"
BACKEND = "next-backend-B7"
OPERATOR = "op-vince"

#: "leave this argument alone", as distinct from "pass None".
_UNSET = object()


# --------------------------------------------------------------------------- #
# where the artifact comes from
# --------------------------------------------------------------------------- #
def _source_location() -> tuple[Path | None, str]:
    """The repository and commit the real sidecar bytes are read from.

    `HEAD` of this checkout, through `git cat-file`: the commit is the thing a
    pin names, and a worktree read can differ from it by line ending alone.
    """
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=False)
    commit = done.stdout.strip() if done.returncode == 0 else ""
    return (root, commit) if commit else (None, "")


SOURCE_REPO, SOURCE_COMMIT = _source_location()


# --------------------------------------------------------------------------- #
# the shared fixture: a two-commit repository holding the real sidecar
# --------------------------------------------------------------------------- #
class Fixture:
    """A git repo whose two commits carry the real files, built once per process.

    Reused across mutation rounds on purpose: rebuilding a repository per test
    would make the probe measure `git init` rather than the guards.
    """

    def __init__(self) -> None:
        if SOURCE_REPO is None or not SOURCE_COMMIT:
            raise unittest.SkipTest("the real sidecar is not readable from HEAD "
                                    "of this checkout")
        self.tmp = Path(tempfile.mkdtemp(prefix="wave18-fixture-"))
        self.repo = self.tmp / "repo"
        self.origin = {path: m.git_blob(SOURCE_REPO, SOURCE_COMMIT, path)
                       for path in m.SIDECAR_FILES}
        self.commit1, self.commit2 = self._build()
        self.descriptor1, self.provenance1 = m.descriptor_for(self.repo, self.commit1,
                                                             version=V1)
        self.descriptor2, self.provenance2 = m.descriptor_for(self.repo, self.commit2,
                                                             version=V2)
        self.blobs1 = {path: m.git_blob(self.repo, self.commit1, path)
                       for path in m.SIDECAR_FILES}
        self.blobs2 = {path: m.git_blob(self.repo, self.commit2, path)
                       for path in m.SIDECAR_FILES}

    def _build(self) -> tuple[str, str]:
        """Commit the real sidecar bytes, then a second commit that differs."""
        repo = self.repo
        repo.mkdir(parents=True)
        for rel in m.SIDECAR_FILES:
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.origin[rel])
        self._git(repo, "init", "-q")
        self._git(repo, "add", "-A")
        self._git(repo, "-c", "user.name=wave18", "-c", "user.email=wave18@example.invalid",
                  "commit", "-q", "-m", "sidecar pin")
        first = m.git_commit(repo, "HEAD")
        plan = repo / m.SIDECAR_FILES[1]
        plan.write_bytes(plan.read_bytes() + b"\n")
        self._git(repo, "add", "-A")
        self._git(repo, "-c", "user.name=wave18", "-c", "user.email=wave18@example.invalid",
                  "commit", "-q", "-m", "second pin")
        return first, m.git_commit(repo, "HEAD")

    @staticmethod
    def _git(repo: Path, *args: str) -> None:
        done = subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, check=False)
        if done.returncode != 0:
            raise AssertionError(done.stderr.decode("utf-8", "replace"))

    def close(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


FIXTURE: Fixture | None = None


def fixture() -> Fixture:
    global FIXTURE
    if FIXTURE is None:
        FIXTURE = Fixture()
    return FIXTURE


def setUpModule() -> None:
    fixture()


def tearDownModule() -> None:
    if FIXTURE is not None:
        FIXTURE.close()


# --------------------------------------------------------------------------- #
# helpers shared by the cases
# --------------------------------------------------------------------------- #
def grant(action: str = "install", *, device: str = DEVICE,
          operator: str = OPERATOR, grant_id: str = "grant-1"):
    return up.Grant(grant_id=grant_id, operator=operator, action=action,
                    device_ref=device)


def identity(**over) -> "m.Identity":
    fields = {"device_ref": DEVICE, "boot_id": BOOT,
              "next_backend_id": BACKEND, "operator": OPERATOR}
    fields.update(over)
    return m.Identity(**fields)


def make_dir_link(link: Path, target: Path) -> bool:
    """A directory link, on any platform, without the privilege Windows wants.

    A junction is a reparse point that `Path.is_symlink()` calls False — the
    reason `_is_link` reads the attribute bits rather than the mode.
    """
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
        return True
    except (OSError, NotImplementedError, AttributeError):
        pass
    if os.name != "nt":
        return False
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True, check=False)
    return done.returncode == 0


def tree_digest(root: Path) -> dict[str, str]:
    """Path -> digest of every file under `root`, for byte-identity comparisons."""
    if not root.is_dir():
        return {}
    return {p.relative_to(root).as_posix():
            "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def bytes_of(root: Path, rel: str) -> bytes | None:
    path = root / rel
    return path.read_bytes() if path.is_file() else None


def sidecar_bytes(root: Path) -> dict[str, bytes]:
    return {rel: (root / rel).read_bytes() for rel in m.SIDECAR_FILES}


def _dead_pid() -> int:
    """A pid that is certainly not running.

    Probed rather than assumed: a pid high enough to be unused on this machine
    still has to be checked, because "probably free" is exactly the kind of
    assumption that turns a stale-lock test into a live-lock test.
    """
    for candidate in (999_983, 999_982, 999_981, 999_980, 999_979):
        if not m.pid_alive(candidate):
            return candidate
    raise RuntimeError("every probe pid is running; this machine is busier than the "
                       "test expects")


_DEAD_PID = _dead_pid()


class Base(unittest.TestCase):
    """A fresh prefix and root per test, under a Python temporary directory."""

    def setUp(self) -> None:
        self.fx = fixture()
        self.tmp = Path(tempfile.mkdtemp(prefix="wave18-case-"))
        self.prefix = self.tmp / "opt"
        self.prefix.mkdir()
        self.root = self.prefix / "sidecar"
        self.legacy = self.tmp / "legacy"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fresh_case(self) -> None:
        """Abandon this case's directory and take a new one.

        Used where one test walks a matrix; every sub-case starts from a root
        nobody else has touched, so a failure cannot be blamed on the case
        before it.
        """
        self.tearDown()
        self.setUp()

    # -- shorthands ---------------------------------------------------------
    def install(self, descriptor=None, provenance=_UNSET, *, grant_=_UNSET,
                identity_=_UNSET, root=None, apply=False,
                allowed_prefixes=_UNSET, **kw) -> "m.InstallOutcome":
        """Install with the case's defaults; every default can be replaced.

        `_UNSET` rather than `None`, because "no grant" and "no identity" are
        states this suite has to be able to ask about — `None` there would
        silently mean "the default one", and a refusal test would pass for the
        wrong reason.
        """
        if descriptor is None:
            descriptor = self.fx.descriptor1
        if provenance is _UNSET:
            provenance = self.fx.provenance1
        return m.install(descriptor, root or self.root,
                         grant=grant("install") if grant_ is _UNSET else grant_,
                         identity=identity() if identity_ is _UNSET else identity_,
                         allowed_prefixes=([self.prefix] if allowed_prefixes is _UNSET
                                           else allowed_prefixes),
                         provenance=provenance, apply=apply, **kw)

    def install_ok(self, descriptor=None, provenance=_UNSET, **kw) -> "m.InstallOutcome":
        outcome = self.install(descriptor, provenance, apply=True, **kw)
        self.assertEqual(m.OK, outcome.decision, outcome.reasons)
        return outcome

    def upgrade(self, **kw) -> "m.InstallOutcome":
        return self.install(self.fx.descriptor2, self.fx.provenance2, apply=True, **kw)

    def revoke(self, descriptor=None, *, grant_=None, identity_=None,
               root=None, legacy_roots=()) -> "m.RevokeOutcome":
        return m.revoke_verified(descriptor or self.fx.descriptor1,
                                 root or self.root,
                                 grant=grant_ if grant_ is not None else grant("revoke"),
                                 identity=identity_ or identity(),
                                 legacy_roots=legacy_roots)

    def rollback(self, descriptor=None, **kw) -> "m.RestoreOutcome":
        return m.rollback_to_previous(descriptor or self.fx.descriptor1, self.root,
                                      grant=grant("restore"), identity=identity(), **kw)

    def assertRefused(self, outcome, code: str) -> None:
        self.assertEqual(m.BLOCKED, outcome.decision,
                         f"expected {code}, got {outcome.decision}/{outcome.code} "
                         f"{outcome.reasons}")
        self.assertEqual(code, outcome.code, outcome.reasons)

    def assertNothingWritten(self) -> None:
        self.assertFalse(self.root.exists(), "the root was created")
        self.assertEqual((), m._staging_candidates(self.root),
                         "a staging directory was left behind")
        self.assertFalse(m.lock_path_for(self.root).exists(), "a lock was left behind")

    def simulate_death(self, *, purpose: str = "install") -> None:
        """Recreate the lock a killed process leaves.

        The crash tests inject a death in-process, which means the module's own
        `finally` still released the sibling lock — a real kill does not run it.
        Rewriting the lock with a pid that is not running restores the state a
        death actually leaves, so `recover()` is measured against that state
        rather than against this test's convenience. The helper is named for
        what it models, not for what it does.
        """
        body = json.dumps({"pid": _DEAD_PID, "boot_id": BOOT, "purpose": purpose,
                           "token": "dead", "started_at": 0}, sort_keys=True)
        m.lock_path_for(self.root).write_bytes(body.encode("utf-8"))


# --------------------------------------------------------------------------- #
# what is being installed
# --------------------------------------------------------------------------- #
class Artifact(Base):
    def test_the_gate_is_the_real_gate(self) -> None:
        gate = self.fx.blobs1[m.SIDECAR_FILES[0]]
        self.assertGreater(len(gate), 20_000, "the gate file looks empty or truncated")
        self.assertTrue(gate.startswith(b"#!/usr/bin/env python3\n"))
        self.assertIn(b"def ", gate)
        self.assertNotIn(b"\r\n", gate, "the pinned blob is LF; a CRLF read would move "
                                        "every digest this suite compares")

    def test_the_plan_is_the_real_plan(self) -> None:
        plan = json.loads(self.fx.blobs1[m.SIDECAR_FILES[1]])
        self.assertEqual("serveros/fleet-next-tailscale-ingress.v1", plan["schema"])
        for key in ("existing", "next", "routes", "state"):
            self.assertIn(key, plan)

    def test_the_fixture_holds_the_bytes_at_head_not_a_stand_in(self) -> None:
        """A mock would pass every other test in this file."""
        self.assertRegex(SOURCE_COMMIT, r"^[0-9a-f]{40}$")
        for rel in m.SIDECAR_FILES:
            self.assertEqual(self.fx.origin[rel], self.fx.blobs1[rel], rel)

    def test_the_two_pins_actually_differ(self) -> None:
        self.assertNotEqual(self.fx.blobs1[m.SIDECAR_FILES[1]],
                            self.fx.blobs2[m.SIDECAR_FILES[1]],
                            "the upgrade tests prove nothing unless the pins differ")
        self.assertNotEqual(self.fx.descriptor1.manifest_digest(),
                            self.fx.descriptor2.manifest_digest())


# --------------------------------------------------------------------------- #
# the interface this module requires of #506
# --------------------------------------------------------------------------- #
class InterfaceShape(Base):
    def test_every_required_name_is_present(self) -> None:
        _present, missing = m.interface()
        self.assertEqual((), missing)

    def test_the_report_says_compatible_and_names_no_patch(self) -> None:
        report = m.interface_report()
        self.assertTrue(report["compatible"])
        self.assertEqual([], report["missing"])
        self.assertEqual("", report["patch"])
        self.assertEqual(list(m.REQUIRED_INTERFACE), report["required"])
        self.assertEqual(list(m.FINDINGS), report["findings"])

    def test_a_missing_name_is_named_with_a_patch(self) -> None:
        """BLOCKED_INTERFACE is reachable, and says what to ask for."""
        shim = type("Shim", (), {})
        for name in m.REQUIRED_INTERFACE:
            if name in m.RECEIPT_BODY_ALIASES:
                continue
            setattr(shim, name, getattr(up, name, None))
        _present, missing = m.interface(shim)
        self.assertEqual(("_receipt_body|receipt_body",), missing)
        outcome = self.install(apply=True, upstream=shim)
        self.assertRefused(outcome, m.BLOCKED_INTERFACE)
        self.assertIn("receipt_body", " ".join(outcome.reasons))
        self.assertFalse(self.root.exists())

    def test_receipt_body_is_found_under_either_name(self) -> None:
        class WithPublic:
            receipt_body = staticmethod(lambda *a, **k: {})

        class WithPrivate:
            _receipt_body = staticmethod(lambda *a, **k: {})

        self.assertIsNotNone(m.receipt_body_of(WithPublic()))
        self.assertIsNotNone(m.receipt_body_of(WithPrivate()))
        self.assertIsNone(m.receipt_body_of(object()))

    def test_pipelines_resolve_names_at_call_time(self) -> None:
        """A tuple of function objects bound at import would freeze the pipeline.

        This is the property that lets the mutation probe withdraw one guard:
        rebinding the attribute must change what `install_guards()` returns.
        """
        original = m._guard_lock

        def sentinel(ctx):
            return None

        try:
            m._guard_lock = sentinel
            self.assertIn(sentinel, m.install_guards())
            self.assertIn(sentinel, m.revoke_guards())
        finally:
            m._guard_lock = original
        self.assertNotIn(sentinel, m.install_guards())

    def test_every_guard_name_resolves_to_a_function(self) -> None:
        for name in m.INSTALL_GUARDS + m.REVOKE_GUARDS + m.RESTORE_GUARDS:
            self.assertTrue(callable(getattr(m, name, None)), name)

    def test_every_pipeline_resolves_its_declared_names(self) -> None:
        self.assertEqual(len(m.INSTALL_GUARDS), len(m.install_guards()))
        self.assertEqual(len(m.REVOKE_GUARDS), len(m.revoke_guards()))
        self.assertEqual(len(m.RESTORE_GUARDS), len(m.restore_guards()))

    def test_pipeline_refuses_an_unknown_guard_name(self) -> None:
        with self.assertRaises(AssertionError):
            m._pipeline(("_guard_not_a_thing",))

    def test_the_three_pipelines_ask_different_questions(self) -> None:
        install = set(m.INSTALL_GUARDS)
        revoke = set(m.REVOKE_GUARDS)
        restore = set(m.RESTORE_GUARDS)
        self.assertEqual({"_guard_interface", "_guard_identity", "_guard_lock"},
                         install & revoke)
        self.assertNotIn("_guard_upgrade", revoke)
        self.assertNotIn("_guard_backend", install)
        # A rollback needs `restore`, not `revoke`, and it needs a receipt that
        # remembers something to roll back to.
        self.assertIn("_guard_grant_restore", restore)
        self.assertNotIn("_guard_grant_revoke", restore)
        # Isolation is not re-checked anywhere in this module: `revoke_verified`
        # hands `legacy_roots` to #506's `revoke`, whose own `_guard_root` runs
        # the same `_root_refusal`. The probe measured the copy as SILENT — twice
        # — and a check no control can see is not a control, so it is gone rather
        # than kept for comfort. This asserts it does not come back.
        self.assertNotIn("_guard_root_revoke", install | revoke | restore)
        self.assertFalse(hasattr(m, "_guard_root_revoke"))

    def test_refusal_codes_are_namespaced_and_distinct(self) -> None:
        names = {name for name in vars(m) if name.startswith("BLOCKED_")}
        self.assertGreaterEqual(len(names), 35, "a refusal surface this wide does not "
                                                "shrink by five without a reason")
        for name in names:
            self.assertEqual(name, getattr(m, name), "a code whose name is not its "
                                                     "value")
        self.assertEqual("BLOCKED", m.BLOCKED)


# --------------------------------------------------------------------------- #
# dry run, then the real install
# --------------------------------------------------------------------------- #
class DryRunAndInstall(Base):
    def test_plan_is_the_default_and_writes_nothing(self) -> None:
        outcome = self.install()
        self.assertEqual(m.DRY_RUN, outcome.code)
        self.assertEqual(m.OK, outcome.decision)
        self.assertFalse(outcome.applied)
        self.assertNothingWritten()

    def test_plan_is_deterministic(self) -> None:
        first = self.install()
        second = self.install()
        self.assertEqual(first.receipt_digest, second.receipt_digest)
        self.assertEqual(first.manifest_digest, second.manifest_digest)

    def test_apply_writes_the_two_files_and_the_two_evidence_files(self) -> None:
        outcome = self.install_ok()
        self.assertTrue(outcome.applied)
        self.assertEqual(tuple(sorted(m.SIDECAR_FILES)), outcome.written)
        on_disk = sorted(p.relative_to(self.root).as_posix()
                         for p in self.root.rglob("*") if p.is_file())
        self.assertEqual(sorted([*m.SIDECAR_FILES,
                                 up.RECEIPT_NAME, up.OWNER_MARKER_NAME]), on_disk)

    def test_installed_bytes_are_the_commit_bytes(self) -> None:
        self.install_ok()
        digests = dict(self.fx.descriptor1.files)
        for rel in m.SIDECAR_FILES:
            self.assertEqual(self.fx.blobs1[rel], bytes_of(self.root, rel), rel)
            self.assertEqual("sha256:" + hashlib.sha256(self.fx.blobs1[rel]).hexdigest(),
                             digests[rel])

    def test_receipt_is_readable_by_the_revoke_side(self) -> None:
        self.install_ok()
        receipt = up.read_receipt(self.root)
        self.assertIsNotNone(receipt)
        assert receipt is not None
        self.assertTrue(receipt.is_intact())
        self.assertEqual(V1, receipt.version)
        self.assertEqual(self.fx.commit1, receipt.commit)
        self.assertEqual(DEVICE, receipt.device_ref)
        self.assertEqual(BOOT, receipt.boot_id)
        self.assertEqual(OPERATOR, receipt.operator)
        self.assertEqual("grant-1", receipt.grant_id)
        self.assertEqual(os.path.realpath(str(self.root)), receipt.install_root)
        self.assertEqual(2, len(receipt.files))
        self.assertIsNone(receipt.previous)
        self.assertEqual(self.fx.descriptor1.manifest_digest(), receipt.manifest_digest)
        self.assertEqual(up.RECEIPT_SCHEMA, receipt.raw["schema"])

    def test_marker_records_the_backend_the_receipt_has_no_field_for(self) -> None:
        self.install_ok()
        marker = up._read_marker(self.root)
        self.assertIsNotNone(marker)
        assert marker is not None
        self.assertEqual(up.MARKER_SCHEMA, marker["schema"])
        self.assertEqual(BACKEND, marker["next_backend_id"])
        self.assertEqual(OPERATOR, marker["installed_by"])
        self.assertEqual(m.SCHEMA, marker["installer_schema"])
        receipt = up.read_receipt(self.root)
        assert receipt is not None
        self.assertEqual(receipt.digest, marker["receipt_digest"])

    def test_nothing_is_left_beside_or_inside_the_root(self) -> None:
        self.install_ok()
        self.assertFalse(m.lock_path_for(self.root).exists())
        self.assertFalse((self.root / up.LOCK_NAME).exists())
        self.assertEqual((), m._staging_candidates(self.root))

    def test_a_second_install_of_the_same_version_is_refused(self) -> None:
        self.install_ok()
        self.assertRefused(self.install(apply=True), m.BLOCKED_ALREADY_INSTALLED)

    def test_the_staging_directory_is_a_sibling_named_for_the_root(self) -> None:
        plan, outcome = m.plan_install(self.fx.descriptor1, self.root,
                                       grant=grant("install"), identity=identity(),
                                       allowed_prefixes=[self.prefix],
                                       provenance=self.fx.provenance1)
        self.assertEqual(m.DRY_RUN, outcome.code)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(self.prefix, plan.staging.parent)
        self.assertNotEqual(self.root, plan.staging)
        self.assertTrue(plan.staging.name.startswith(self.root.name + m.STAGING_SUFFIX))

    def test_an_empty_pre_existing_root_is_refused_at_apply_time(self) -> None:
        """The guard lets an empty directory through; the rename cannot replace it.

        Asserted because the refusal has to happen *somewhere*: a plan that
        accepts an empty directory and an apply that silently renames onto it
        would be two different answers to one question.
        """
        self.root.mkdir()
        outcome = self.install(apply=True)
        self.assertRefused(outcome, m.BLOCKED_ROOT_NOT_EMPTY)
        self.assertEqual((), m._staging_candidates(self.root))
        self.assertTrue(self.root.is_dir(), "the empty directory was removed")


# --------------------------------------------------------------------------- #
# provenance: the bytes come from a commit, not from a directory
# --------------------------------------------------------------------------- #
class ProvenanceAndDescriptor(Base):
    def test_no_provenance_is_refused(self) -> None:
        self.assertRefused(self.install(provenance=None, apply=True),
                           m.BLOCKED_NO_PROVENANCE)

    def test_descriptor_and_provenance_must_name_the_same_commit(self) -> None:
        outcome = self.install(provenance=self.fx.provenance2, apply=True)
        self.assertRefused(outcome, m.BLOCKED_PROVENANCE_MISMATCH)
        self.assertNothingWritten()

    def test_an_unknown_commit_is_a_provenance_refusal(self) -> None:
        outcome = self.install(provenance=m.Provenance(repo=self.fx.repo,
                                                       commit="0" * 40),
                               apply=True)
        self.assertRefused(outcome, m.BLOCKED_PROVENANCE_MISMATCH)
        self.assertNothingWritten()

    def test_a_worktree_edit_after_the_pin_does_not_reach_the_install(self) -> None:
        repo = self.tmp / "edited"
        repo.mkdir()
        for rel in m.SIDECAR_FILES:
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.fx.blobs1[rel])
        Fixture._git(repo, "init", "-q")
        Fixture._git(repo, "add", "-A")
        Fixture._git(repo, "-c", "user.name=t", "-c", "user.email=t@e.invalid",
                     "commit", "-q", "-m", "pin")
        commit = m.git_commit(repo, "HEAD")
        descriptor, provenance = m.descriptor_for(repo, commit, version=V1)
        edited = repo / m.SIDECAR_FILES[1]
        edited.write_bytes(edited.read_bytes() + b"\n")
        outcome = m.install(descriptor, self.root, grant=grant("install"),
                            identity=identity(), allowed_prefixes=[self.prefix],
                            provenance=provenance, apply=True)
        self.assertEqual(m.OK, outcome.decision, outcome.reasons)
        self.assertEqual(self.fx.blobs1[m.SIDECAR_FILES[1]],
                         bytes_of(self.root, m.SIDECAR_FILES[1]))

    def test_caller_supplied_bytes_are_checked_against_the_pin(self) -> None:
        wrong = {path: b"not the pinned bytes" for path in m.SIDECAR_FILES}
        self.assertRefused(self.install(sources=wrong, apply=True),
                           m.BLOCKED_SOURCE_DIGEST)
        self.assertNothingWritten()

    def test_caller_supplied_bytes_that_match_are_accepted(self) -> None:
        outcome = self.install(sources=dict(self.fx.blobs1), apply=True)
        self.assertEqual(m.OK, outcome.decision, outcome.reasons)
        self.assertEqual(self.fx.blobs1[m.SIDECAR_FILES[1]],
                         bytes_of(self.root, m.SIDECAR_FILES[1]))

    def test_a_descriptor_that_pins_more_than_the_sidecar_is_refused(self) -> None:
        extra = ("fleet_mcp/deploy/extra.py", "sha256:" + "0" * 64)
        descriptor = dataclasses.replace(
            self.fx.descriptor1,
            files=tuple(sorted((*self.fx.descriptor1.files, extra))))
        self.assertRefused(self.install(descriptor, provenance=None),
                           m.BLOCKED_NOT_THE_SIDECAR)

    def test_a_descriptor_under_another_name_is_refused(self) -> None:
        descriptor = dataclasses.replace(self.fx.descriptor1, name="not-the-sidecar")
        self.assertRefused(self.install(descriptor), m.BLOCKED_NOT_THE_SIDECAR)

    def test_an_unpinned_version_is_refused(self) -> None:
        descriptor = dataclasses.replace(self.fx.descriptor1, version="latest")
        self.assertRefused(self.install(descriptor), m.BLOCKED_NOT_PINNED)

    def test_a_path_that_leaves_the_root_is_refused(self) -> None:
        escape = ("../escaped.py", "sha256:" + "1" * 64)
        descriptor = dataclasses.replace(
            self.fx.descriptor1, files=(escape, *self.fx.descriptor1.files),
            entrypoint="../escaped.py")
        self.assertRefused(self.install(descriptor), m.BLOCKED_DESCRIPTOR_PATH)

    def test_an_entrypoint_outside_the_manifest_is_refused(self) -> None:
        descriptor = dataclasses.replace(self.fx.descriptor1, entrypoint="other.py")
        self.assertRefused(self.install(descriptor), m.BLOCKED_NOT_PINNED)


# --------------------------------------------------------------------------- #
# identity and grant
# --------------------------------------------------------------------------- #
class IdentityAndGrant(Base):
    def test_an_incomplete_identity_is_refused(self) -> None:
        for field in ("device_ref", "boot_id", "next_backend_id", "operator"):
            with self.subTest(missing=field):
                outcome = self.install(identity_=identity(**{field: ""}))
                self.assertRefused(outcome, m.BLOCKED_IDENTITY_INCOMPLETE)

    def test_the_backend_identity_cannot_be_the_host_identity(self) -> None:
        outcome = self.install(identity_=identity(next_backend_id=DEVICE))
        self.assertRefused(outcome, m.BLOCKED_IDENTITY_CONFLATED)

    def test_a_host_identity_that_merely_starts_like_the_backend_is_fine(self) -> None:
        """Conflation is equality, not resemblance: a prefix match would refuse
        honest names."""
        self.install_ok(identity_=identity(next_backend_id=DEVICE + "-next"))

    def test_no_grant_is_refused(self) -> None:
        self.assertRefused(self.install(grant_=None), m.BLOCKED_NO_GRANT)

    def test_a_revoke_grant_does_not_cover_an_install(self) -> None:
        self.assertRefused(self.install(grant_=grant("revoke")), m.BLOCKED_NO_GRANT)

    def test_a_restore_grant_does_not_cover_an_install(self) -> None:
        self.assertRefused(self.install(grant_=grant("restore")), m.BLOCKED_NO_GRANT)

    def test_a_grant_for_another_device_is_refused(self) -> None:
        self.assertRefused(self.install(grant_=grant("install", device="other")),
                           m.BLOCKED_IDENTITY_MISMATCH)

    def test_a_grant_for_another_operator_is_refused(self) -> None:
        self.assertRefused(self.install(grant_=grant("install", operator="someone")),
                           m.BLOCKED_IDENTITY_MISMATCH)

    def test_a_grant_without_an_id_covers_nothing(self) -> None:
        self.assertRefused(self.install(grant_=grant("install", grant_id="")),
                           m.BLOCKED_NO_GRANT)

    def test_a_grant_whose_action_is_a_prefix_of_install_does_not_cover_it(self) -> None:
        self.assertRefused(self.install(grant_=grant("inst")), m.BLOCKED_NO_GRANT)


# --------------------------------------------------------------------------- #
# the root: isolation, namespace, links, Legacy
# --------------------------------------------------------------------------- #
class RootAndNamespace(Base):
    def test_a_relative_root_is_refused(self) -> None:
        outcome = m.install(self.fx.descriptor1, Path("sidecar"),
                            grant=grant("install"), identity=identity(),
                            allowed_prefixes=[self.prefix],
                            provenance=self.fx.provenance1)
        self.assertRefused(outcome, m.BLOCKED_ROOT_NOT_ABSOLUTE)

    def test_the_filesystem_root_is_refused(self) -> None:
        outcome = self.install(root=Path(os.path.abspath(os.sep)))
        self.assertRefused(outcome, m.BLOCKED_ROOT_NOT_ISOLATED)

    def test_the_home_directory_is_refused(self) -> None:
        """`_root_refusal` refuses a root that *is* or *encloses* the home
        directory; a dry run, so nothing is written near it."""
        outcome = self.install(root=Path.home())
        self.assertRefused(outcome, m.BLOCKED_ROOT_NOT_ISOLATED)

    def test_a_root_outside_every_prefix_is_refused(self) -> None:
        outcome = self.install(root=self.tmp / "elsewhere" / "sidecar")
        self.assertRefused(outcome, m.BLOCKED_ROOT_NOT_NAMESPACED)

    def test_no_prefix_at_all_is_refused(self) -> None:
        outcome = m.install(self.fx.descriptor1, self.root, grant=grant("install"),
                            identity=identity(), allowed_prefixes=[],
                            provenance=self.fx.provenance1)
        self.assertRefused(outcome, m.BLOCKED_ROOT_NOT_NAMESPACED)

    def test_the_prefix_itself_is_not_a_root(self) -> None:
        self.assertRefused(self.install(root=self.prefix),
                           m.BLOCKED_ROOT_NOT_NAMESPACED)

    def test_the_leaf_name_must_be_sidecar(self) -> None:
        self.assertRefused(self.install(root=self.prefix / "not-sidecar"),
                           m.BLOCKED_ROOT_NOT_NAMESPACED)

    def test_a_root_that_is_a_link_is_refused(self) -> None:
        target = self.tmp / "target"
        target.mkdir()
        if not make_dir_link(self.root, target):
            self.skipTest("this platform will not create a directory link")
        self.assertRefused(self.install(apply=True), m.BLOCKED_ROOT_IS_SYMLINK)
        self.assertEqual({}, tree_digest(target))

    def test_a_link_on_the_way_to_the_root_is_refused(self) -> None:
        real = self.tmp / "real" / "opt"
        real.mkdir(parents=True)
        link = self.tmp / "linked"
        if not make_dir_link(link, real):
            self.skipTest("this platform will not create a directory link")
        outcome = self.install(root=link / "sidecar", apply=True,
                               allowed_prefixes=[self.prefix, link])
        self.assertRefused(outcome, m.BLOCKED_PARENT_ESCAPE)
        self.assertEqual({}, tree_digest(real))

    def test_a_legacy_root_is_refused_when_the_root_is_inside_it(self) -> None:
        self.legacy.mkdir()
        outcome = self.install(root=self.legacy / "sidecar",
                               allowed_prefixes=[self.legacy],
                               legacy_roots=[self.legacy])
        self.assertRefused(outcome, m.BLOCKED_LEGACY_ROOT)
        self.assertEqual({}, tree_digest(self.legacy))

    def test_a_legacy_root_is_refused_when_it_is_the_root(self) -> None:
        self.legacy.mkdir()
        outcome = self.install(root=self.legacy,
                               allowed_prefixes=[self.tmp],
                               legacy_roots=[self.legacy])
        self.assertRefused(outcome, m.BLOCKED_LEGACY_ROOT)

    def test_a_legacy_root_is_refused_when_the_root_contains_it(self) -> None:
        """Deleting here would reach the Legacy root, so the install is refused."""
        outcome = self.install(legacy_roots=[self.root / "inner"])
        self.assertRefused(outcome, m.BLOCKED_LEGACY_ROOT)
        self.assertNothingWritten()

    def test_a_non_empty_root_without_a_receipt_is_refused(self) -> None:
        self.root.mkdir()
        (self.root / "someone-elses-file").write_bytes(b"keep me")
        self.assertRefused(self.install(apply=True), m.BLOCKED_ROOT_NOT_EMPTY)
        self.assertEqual(b"keep me", bytes_of(self.root, "someone-elses-file"))

    def test_a_root_that_is_a_file_is_refused(self) -> None:
        self.root.write_bytes(b"not a directory")
        self.assertRefused(self.install(apply=True), m.BLOCKED_ROOT_NOT_EMPTY)
        self.assertEqual(b"not a directory", self.root.read_bytes())


# --------------------------------------------------------------------------- #
# one writer per root
# --------------------------------------------------------------------------- #
class LockingAndConcurrency(Base):
    def test_a_live_lock_refuses_the_install(self) -> None:
        lock = m.acquire_lock(self.root, boot_id=BOOT, purpose="install")
        try:
            self.assertRefused(self.install(apply=True), m.BLOCKED_CONCURRENT_WRITER)
            self.assertFalse(self.root.exists())
        finally:
            lock.release()

    def test_a_lock_from_a_dead_process_is_named_not_ignored(self) -> None:
        self.simulate_death()
        self.assertRefused(self.install(apply=True), m.BLOCKED_STALE_WRITER)
        self.assertFalse(self.root.exists())

    def test_the_lock_is_created_exclusively(self) -> None:
        first = m.acquire_lock(self.root, boot_id=BOOT, purpose="install")
        self.assertTrue(m.lock_path_for(self.root).exists())
        try:
            with self.assertRaises(FileExistsError):
                m.acquire_lock(self.root, boot_id=BOOT, purpose="install")
        finally:
            first.release()
        self.assertFalse(m.lock_path_for(self.root).exists())

    def test_release_leaves_a_lock_another_writer_took(self) -> None:
        """`release()` removes its own lock, not whatever file now has that name."""
        mine = m.acquire_lock(self.root, boot_id=BOOT, purpose="install")
        path = mine.path
        path.unlink()
        theirs = m.acquire_lock(self.root, boot_id=BOOT, purpose="upgrade")
        try:
            mine.release()
            self.assertTrue(path.exists(), "release() deleted another writer's lock")
        finally:
            theirs.release()

    def test_a_liveness_probe_does_not_stop_the_process_it_checks(self) -> None:
        """`os.kill(pid, 0)` is `TerminateProcess` on Windows; a probe that can
        kill the writer it is asking about is not a liveness probe."""
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            time.sleep(0.5)
            self.assertTrue(m.pid_alive(child.pid))
            time.sleep(0.5)
            self.assertIsNone(child.poll(),
                              "pid_alive() terminated the process it asked about")
        finally:
            child.kill()
            child.wait(timeout=10)

    def test_another_process_holding_the_lock_refuses_this_one(self) -> None:
        """The lock is cross-process, so a second *interpreter* must be refused."""
        script = (
            "import importlib.util, sys, time\n"
            f"spec = importlib.util.spec_from_file_location('held', {str(MODULE_PATH)!r})\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "sys.modules['held'] = mod\n"
            "spec.loader.exec_module(mod)\n"
            "from pathlib import Path\n"
            f"mod.acquire_lock(Path({str(self.root)!r}), boot_id={BOOT!r}, "
            "purpose='install')\n"
            "print('LOCKED', flush=True)\n"
            "time.sleep(60)\n")
        child = subprocess.Popen([sys.executable, "-c", script],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            line = child.stdout.readline().decode("utf-8", "replace").strip()
            if line != "LOCKED":
                # Read stderr only after it is known to be needed: `assertEqual`'s
                # message argument is evaluated whether or not the assertion
                # fails, and reading a live child's stderr would block until it
                # exits — turning this test into a 60-second wait for a corpse.
                self.fail(f"the holder said {line!r}: "
                          f"{child.stderr.read().decode('utf-8', 'replace')}")
            self.assertRefused(self.install(apply=True), m.BLOCKED_CONCURRENT_WRITER)
            self.assertFalse(self.root.exists())
            self.assertIsNone(child.poll(), "the holder died; the refusal proves less")
        finally:
            child.kill()
            child.wait(timeout=10)
            for stream in (child.stdout, child.stderr):
                if stream is not None:
                    stream.close()

    def test_recover_clears_a_dead_lock_and_leaves_a_live_one(self) -> None:
        self.simulate_death()
        recovered = m.recover(self.root)
        self.assertEqual(m.OK, recovered.decision, recovered.reasons)
        self.assertIn(m.lock_path_for(self.root).name, recovered.removed)
        self.assertFalse(m.lock_path_for(self.root).exists())

        live = m.acquire_lock(self.root, boot_id=BOOT, purpose="install")
        try:
            self.assertRefused(m.recover(self.root), m.BLOCKED_CONCURRENT_WRITER)
            self.assertTrue(m.lock_path_for(self.root).exists())
        finally:
            live.release()


# --------------------------------------------------------------------------- #
# upgrade, rollback, restore
# --------------------------------------------------------------------------- #
class UpgradeAndRestore(Base):
    def test_an_upgrade_records_the_previous_version_and_keeps_its_bytes(self) -> None:
        self.install_ok()
        outcome = self.upgrade()
        self.assertEqual(m.OK, outcome.decision, outcome.reasons)
        receipt = up.read_receipt(self.root)
        assert receipt is not None
        self.assertEqual(V2, receipt.version)
        self.assertIsNotNone(receipt.previous)
        assert receipt.previous is not None
        self.assertEqual(V1, receipt.previous.version)
        self.assertEqual(self.fx.descriptor1.manifest_digest(),
                         receipt.previous.manifest_digest)
        self.assertEqual(f"{up.BACKUP_DIR}/{V1}", receipt.previous.backup_dir)
        for record in receipt.previous.files:
            self.assertEqual(self.fx.blobs1[record.path],
                             bytes_of(self.root, f"{receipt.previous.backup_dir}/"
                                                 f"{record.path}"), record.path)

    def test_the_installed_files_are_the_new_commit_bytes(self) -> None:
        self.install_ok()
        self.upgrade()
        for rel in m.SIDECAR_FILES:
            self.assertEqual(self.fx.blobs2[rel], bytes_of(self.root, rel), rel)

    def test_the_upgrade_marker_names_the_new_receipt(self) -> None:
        self.install_ok()
        self.upgrade()
        receipt = up.read_receipt(self.root)
        marker = up._read_marker(self.root)
        assert receipt is not None and marker is not None
        self.assertEqual(receipt.digest, marker["receipt_digest"])
        self.assertEqual(V2, marker["version"])
        self.assertEqual(BACKEND, marker["next_backend_id"])

    def test_a_second_upgrade_is_refused_as_a_chain(self) -> None:
        self.install_ok()
        self.upgrade()
        descriptor3, provenance3 = m.descriptor_for(self.fx.repo, self.fx.commit2,
                                                    version=V3)
        self.assertRefused(self.install(descriptor3, provenance3, apply=True),
                           m.BLOCKED_UPGRADE_CHAIN)

    def test_a_foreign_file_blocks_an_upgrade(self) -> None:
        self.install_ok()
        intruder = self.root / "fleet_mcp" / "deploy" / "extra.py"
        intruder.write_bytes(b"x")
        self.assertRefused(self.upgrade(), m.BLOCKED_FOREIGN_PRESENT)
        self.assertEqual(b"x", bytes_of(self.root, "fleet_mcp/deploy/extra.py"))

    def test_a_file_edited_after_install_blocks_an_upgrade(self) -> None:
        self.install_ok()
        target = self.root / m.SIDECAR_FILES[0]
        target.write_bytes(target.read_bytes() + b"# edited\n")
        self.assertRefused(self.upgrade(), m.BLOCKED_MODIFIED_AFTER_INSTALL)

    def test_a_tampered_receipt_blocks_an_upgrade(self) -> None:
        self.install_ok()
        path = self.root / up.RECEIPT_NAME
        body = json.loads(path.read_bytes())
        body["version"] = "9.9.9+forged"
        path.write_bytes(json.dumps(body, sort_keys=True, indent=2).encode("utf-8"))
        self.assertRefused(self.upgrade(), m.BLOCKED_RECEIPT_TAMPERED)

    def test_a_receipt_that_names_another_root_blocks_an_upgrade(self) -> None:
        """An intact receipt for a different directory is a different root."""
        self.install_ok()
        path = self.root / up.RECEIPT_NAME
        body = json.loads(path.read_bytes())
        body["install_root"] = os.path.realpath(str(self.tmp / "somewhere-else"))
        body.pop("receipt_digest", None)
        body["receipt_digest"] = up.receipt_digest(body)
        path.write_bytes(json.dumps(body, sort_keys=True, indent=2).encode("utf-8"))
        reread = up.read_receipt(self.root)
        assert reread is not None
        self.assertTrue(reread.is_intact(),
                        "the rewrite must still verify, or this tests tampering")
        self.assertRefused(self.upgrade(), m.BLOCKED_RECEIPT_MISMATCH)

    def test_an_upgrade_on_another_device_is_refused(self) -> None:
        self.install_ok()
        outcome = self.upgrade(identity_=identity(device_ref="another-device"),
                               grant_=grant("install", device="another-device"))
        self.assertRefused(outcome, m.BLOCKED_IDENTITY_MISMATCH)

    def test_an_upgrade_in_another_boot_is_refused(self) -> None:
        self.install_ok()
        outcome = self.upgrade(identity_=identity(boot_id=OTHER_BOOT))
        self.assertRefused(outcome, m.BLOCKED_BOOT_MISMATCH)

    def test_restore_puts_the_previous_bytes_back(self) -> None:
        self.install_ok()
        self.upgrade()
        outcome = self.rollback()
        self.assertEqual(m.OK, outcome.decision, outcome.reasons)
        self.assertEqual(tuple(sorted(m.SIDECAR_FILES)),
                         tuple(sorted(outcome.restored)))
        for rel in m.SIDECAR_FILES:
            self.assertEqual(self.fx.blobs1[rel], bytes_of(self.root, rel), rel)
        receipt = up.read_receipt(self.root)
        assert receipt is not None
        self.assertEqual(V1, receipt.version)
        self.assertIsNone(receipt.previous)

    def test_restore_leaves_no_backup_behind(self) -> None:
        self.install_ok()
        self.upgrade()
        self.rollback()
        on_disk = sorted(p.relative_to(self.root).as_posix()
                         for p in self.root.rglob("*") if p.is_file())
        self.assertEqual(sorted([*m.SIDECAR_FILES,
                                 up.RECEIPT_NAME, up.OWNER_MARKER_NAME]), on_disk)

    def test_restore_keeps_the_backend_identity_that_upstream_drops(self) -> None:
        """Finding F1: `restore()` writes a seven-key marker; this one has ten.

        The marker is the only place the backend identity lives, so a restore
        that dropped it would leave a root whose next revoke is refused as
        BLOCKED_BACKEND_MISSING. Asserted here so the repair cannot be dropped
        silently, and reported to the owner of `restore()` rather than patched
        in their file.
        """
        self.install_ok()
        self.upgrade()
        self.rollback()
        marker = up._read_marker(self.root)
        assert marker is not None
        self.assertEqual(BACKEND, marker["next_backend_id"])
        self.assertEqual(OPERATOR, marker["installed_by"])
        self.assertTrue(any("restore()" in finding for finding in m.FINDINGS))
        # #506's restore() writes exactly seven keys, and the backend is not one
        # of them; this module's marker carries ten, so a seven-key marker here
        # would be the finding coming true.
        self.assertEqual(10, len(marker))
        self.assertIn("next_backend_id", marker)

    def test_a_revoke_follows_a_restore(self) -> None:
        self.install_ok()
        self.upgrade()
        self.rollback()
        outcome = self.revoke()
        self.assertEqual(m.OK, outcome.decision, outcome.reasons)
        self.assertFalse(self.root.exists())

    def test_a_rollback_needs_a_restore_grant(self) -> None:
        self.install_ok()
        self.upgrade()
        for action in ("revoke", "install"):
            with self.subTest(action=action):
                outcome = m.rollback_to_previous(self.fx.descriptor1, self.root,
                                                 grant=grant(action),
                                                 identity=identity())
                self.assertRefused(outcome, m.BLOCKED_NO_GRANT)

    def test_a_rollback_grant_for_another_operator_is_refused(self) -> None:
        """The operator is asked here, not upstream.

        #506's `restore()` compares the grant's `device_ref` against the receipt
        and never looks at `operator`. A rollback grant issued to somebody else
        would therefore be accepted if this module did not ask, so the refusal is
        this module's and is asserted against this module: withdrawing
        `_guard_grant_restore` makes the rollback *succeed*, which is what makes
        this test the negative control for that guard.
        """
        self.install_ok()
        self.upgrade()
        outcome = m.rollback_to_previous(self.fx.descriptor1, self.root,
                                         grant=grant("restore", operator="someone"),
                                         identity=identity())
        self.assertRefused(outcome, m.BLOCKED_IDENTITY_MISMATCH)
        receipt = up.read_receipt(self.root)
        assert receipt is not None
        self.assertEqual(V2, receipt.version, "a refused rollback moved the root")
        for rel in m.SIDECAR_FILES:
            self.assertEqual(self.fx.blobs2[rel], bytes_of(self.root, rel), rel)

    def test_a_rollback_with_an_incomplete_identity_is_refused(self) -> None:
        """A missing field is not a disagreeing value.

        Passed through, an empty `operator` would sail past `restore()` (which
        compares device and boot only), and the rollback would run; the
        diagnosis has to name the field that is absent rather than the receipt
        that disagrees with a blank.
        """
        self.install_ok()
        self.upgrade()
        for field in ("device_ref", "boot_id", "next_backend_id", "operator"):
            with self.subTest(missing=field):
                outcome = m.rollback_to_previous(
                    self.fx.descriptor1, self.root, grant=grant("restore"),
                    identity=identity(**{field: ""}))
                self.assertRefused(outcome, m.BLOCKED_IDENTITY_INCOMPLETE)
        receipt = up.read_receipt(self.root)
        assert receipt is not None
        self.assertEqual(V2, receipt.version)

    def test_a_rollback_will_not_mint_a_root_with_no_backend(self) -> None:
        """The marker is the only place the backend identity lives.

        #506's `restore()` does not need it — it writes a fresh seven-key marker
        — so without this module's refusal a rollback over a markerless root
        would complete and leave an install whose next revoke is refused as
        `BLOCKED_BACKEND_MISSING` (finding F1, from the other side).
        """
        self.install_ok()
        self.upgrade()
        (self.root / up.OWNER_MARKER_NAME).unlink()
        self.assertRefused(self.rollback(), m.BLOCKED_BACKEND_MISSING)
        receipt = up.read_receipt(self.root)
        assert receipt is not None
        self.assertEqual(V2, receipt.version)
        self.assertEqual(self.fx.blobs2[m.SIDECAR_FILES[0]],
                         bytes_of(self.root, m.SIDECAR_FILES[0]))
        self.assertFalse((self.root / up.OWNER_MARKER_NAME).exists(),
                         "a refused rollback wrote a marker anyway")

    def test_a_rollback_while_a_writer_holds_the_root_is_refused(self) -> None:
        """The sibling lock is this module's, so `restore()` cannot see it.

        #506's `_guard_writer` looks at the in-root `.sidecar-install.lock`,
        which an in-progress install here does not leave behind until it starts
        writing; the cross-process lock is beside the root and would be ignored.
        """
        self.install_ok()
        self.upgrade()
        lock = m.acquire_lock(self.root, boot_id=BOOT, purpose="upgrade")
        try:
            self.assertRefused(self.rollback(), m.BLOCKED_CONCURRENT_WRITER)
        finally:
            lock.release()
        receipt = up.read_receipt(self.root)
        assert receipt is not None
        self.assertEqual(V2, receipt.version)

    def test_a_rollback_against_an_incompatible_upstream_is_a_named_refusal(self) -> None:
        """`BLOCKED_INTERFACE` is reachable from the rollback path, with a patch.

        An upstream that moved a name has to be a refusal carrying what to ask
        for, not an `AttributeError` from inside a write.
        """
        self.install_ok()
        self.upgrade()
        shim = type("Shim", (), {})
        for name in m.REQUIRED_INTERFACE:
            if name in m.RECEIPT_BODY_ALIASES:
                continue
            setattr(shim, name, getattr(up, name, None))
        outcome = m.rollback_to_previous(self.fx.descriptor1, self.root,
                                         grant=grant("restore"), identity=identity(),
                                         upstream=shim)
        self.assertRefused(outcome, m.BLOCKED_INTERFACE)
        self.assertIn("receipt_body", " ".join(outcome.reasons))
        receipt = up.read_receipt(self.root)
        assert receipt is not None
        self.assertEqual(V2, receipt.version)

    def test_a_rollback_without_a_previous_version_is_refused(self) -> None:
        self.install_ok()
        self.assertRefused(self.rollback(), m.BLOCKED_RECEIPT_MISMATCH)

    def test_a_rollback_without_a_receipt_is_refused_by_name(self) -> None:
        """No receipt is not "no marker": name the evidence that is missing.

        The marker guard runs after this one, and a root nothing was installed
        into has neither file — so the order decides which of the two absences an
        operator is told about, and it has to be the receipt.
        """
        self.assertRefused(self.rollback(), m.BLOCKED_NO_RECEIPT)
        self.assertNothingWritten()

    def test_a_rollback_to_a_descriptor_the_receipt_does_not_remember(self) -> None:
        self.install_ok()
        self.upgrade()
        other, _provenance = m.descriptor_for(self.fx.repo, self.fx.commit2,
                                              version="9.9.9+test")
        self.assertRefused(self.rollback(other), m.BLOCKED_RECEIPT_MISMATCH)
        self.assertEqual(self.fx.blobs2[m.SIDECAR_FILES[1]],
                         bytes_of(self.root, m.SIDECAR_FILES[1]))


# --------------------------------------------------------------------------- #
# crash (a process that stops) and recover
# --------------------------------------------------------------------------- #
class DeathAndRecovery(Base):
    def test_a_death_before_the_rename_leaves_no_root(self) -> None:
        with self.assertRaises(m.SimulatedCrash):
            self.install(apply=True, crash_at="before-rename")
        self.assertFalse(self.root.exists())
        self.assertEqual(1, len(m._staging_candidates(self.root)))
        # Nothing to revoke: the evidence never reached a root.
        self.assertRefused(self.revoke(), m.BLOCKED_NO_RECEIPT)

    def test_a_tree_staged_with_a_receipt_is_still_not_a_root(self) -> None:
        """The staged tree is complete — receipt and all — and still not a root.

        The receipt names where it was going, and that is not where it is, so a
        revoke pointed at the staging directory reads a receipt that does not
        describe it, and refuses rather than deleting.
        """
        with self.assertRaises(m.SimulatedCrash):
            self.install(apply=True, crash_at="staged-receipt")
        self.assertFalse(self.root.exists())
        staging = m._staging_candidates(self.root)[0]
        staged = up.read_receipt(staging)
        self.assertIsNotNone(staged)
        assert staged is not None
        self.assertTrue(staged.is_intact())
        self.assertEqual(os.path.realpath(str(self.root)), staged.install_root)
        self.assertNotEqual(os.path.realpath(str(staging)), staged.install_root)
        self.assertRefused(
            m.revoke_verified(self.fx.descriptor1, staging, grant=grant("revoke"),
                              identity=identity()),
            m.BLOCKED_RECEIPT_MISMATCH)
        self.assertTrue((staging / m.SIDECAR_FILES[0]).exists(),
                        "a refused revoke still deleted something")

    def test_a_death_leaves_no_writer_lock_but_the_helper_restores_it(self) -> None:
        """An in-process crash still runs `finally`; a real kill does not.

        The distinction is why `simulate_death()` exists: without it the crash
        tests would be measuring Python's cleanup and calling it a death.
        """
        with self.assertRaises(m.SimulatedCrash):
            self.install(apply=True, crash_at="before-rename")
        self.assertFalse(m.lock_path_for(self.root).exists())
        self.simulate_death()
        self.assertTrue(m.lock_path_for(self.root).exists())

    def test_recover_discards_a_staging_directory_and_nothing_else(self) -> None:
        with self.assertRaises(m.SimulatedCrash):
            self.install(apply=True, crash_at="before-rename")
        recovered = m.recover(self.root)
        self.assertEqual(m.OK, recovered.decision, recovered.reasons)
        self.assertEqual((), m._staging_candidates(self.root))
        self.assertFalse(self.root.exists())
        self.assertEqual(m.OK, self.install_ok().decision)

    def test_stale_staging_refuses_a_new_install_and_names_recover(self) -> None:
        """Debris beside the root blocks the next run instead of being ignored.

        Two staging directories with one root between them is the state a
        half-finished install leaves, and guessing which one was meant is how a
        writer installs something nobody measured. `recover()` is the answer, and
        the refusal says so.
        """
        with self.assertRaises(m.SimulatedCrash):
            self.install(apply=True, crash_at="before-rename")
        self.assertEqual(1, len(m._staging_candidates(self.root)))
        outcome = self.install(apply=True)
        self.assertRefused(outcome, m.BLOCKED_STALE_STAGING)
        self.assertIn("recover()", " ".join(outcome.reasons))
        self.assertFalse(self.root.exists())
        self.assertEqual(m.OK, m.recover(self.root).decision)
        self.assertEqual(m.OK, self.install_ok().decision)

    def test_recover_with_nothing_to_do_says_so(self) -> None:
        recovered = m.recover(self.root)
        self.assertEqual(m.NOTHING_TO_RECOVER, recovered.code)
        self.assertEqual((), recovered.removed)
        self.assertEqual((), recovered.restored)

    def test_recover_refuses_while_another_writer_is_alive(self) -> None:
        lock = m.acquire_lock(self.root, boot_id=BOOT, purpose="install")
        try:
            self.assertRefused(m.recover(self.root), m.BLOCKED_CONCURRENT_WRITER)
        finally:
            lock.release()

    def test_a_death_at_each_upgrade_step_still_revokes_something_honest(self) -> None:
        """Every interruption leaves a root a revoke can name, and recover repairs.

        At `after-receipt` the new receipt is on disk, so the honest revoke is
        for the new version. At every earlier point the root still carries the
        old receipt, so the honest revoke is for the old version and the two
        files must be the old bytes.
        """
        for point in ("after-backup", "after-files", "after-marker", "after-receipt"):
            with self.subTest(point=point):
                self.fresh_case()
                self.install_ok()
                before = sidecar_bytes(self.root)
                with self.assertRaises(m.SimulatedCrash):
                    self.upgrade(crash_at=point)
                self.simulate_death(purpose="upgrade")
                self.assertTrue((self.root / up.LOCK_NAME).exists(),
                                "an interrupted upgrade must leave the in-root lock; "
                                "that is what tells recover() what it is looking at")

                recovered = m.recover(self.root)
                self.assertEqual(m.OK, recovered.decision, recovered.reasons)
                self.assertFalse((self.root / up.LOCK_NAME).exists())
                self.assertFalse(m.lock_path_for(self.root).exists())

                if point == "after-receipt":
                    receipt = up.read_receipt(self.root)
                    assert receipt is not None
                    self.assertEqual(V2, receipt.version)
                    outcome = self.revoke(self.fx.descriptor2)
                else:
                    self.assertEqual(V1, up.read_receipt(self.root).version)
                    self.assertEqual(before, sidecar_bytes(self.root),
                                     "recover did not put the old bytes back")
                    orphan = [p.relative_to(self.root).as_posix()
                              for p in self.root.rglob("*")
                              if p.is_file() and up.BACKUP_DIR in p.as_posix()]
                    self.assertEqual([], orphan,
                                     "a backup no receipt names survived the repair")
                    outcome = self.revoke(self.fx.descriptor1)
                self.assertEqual(m.OK, outcome.decision, outcome.reasons)
                self.assertFalse(self.root.exists())

    def test_recover_refuses_a_backup_that_is_not_the_receipted_bytes(self) -> None:
        self.install_ok()
        with self.assertRaises(m.SimulatedCrash):
            self.upgrade(crash_at="after-files")
        self.simulate_death(purpose="upgrade")
        receipt = up.read_receipt(self.root)
        assert receipt is not None
        backup = self.root / up.BACKUP_DIR / receipt.version / m.SIDECAR_FILES[0]
        backup.write_bytes(b"tampered backup")
        self.assertRefused(m.recover(self.root), m.BLOCKED_BACKUP_MODIFIED)
        self.assertTrue((self.root / up.LOCK_NAME).exists(),
                        "a refused repair must leave the evidence in place")

    def test_recover_will_not_invent_an_ownership_marker(self) -> None:
        self.install_ok()
        with self.assertRaises(m.SimulatedCrash):
            self.upgrade(crash_at="after-files")
        self.simulate_death(purpose="upgrade")
        (self.root / up.OWNER_MARKER_NAME).unlink()
        self.assertRefused(m.recover(self.root), m.BLOCKED_OWNER_MARKER_MISSING)
        self.assertFalse((self.root / up.OWNER_MARKER_NAME).exists())

    def test_recover_refuses_an_unreadable_receipt(self) -> None:
        self.install_ok()
        with self.assertRaises(m.SimulatedCrash):
            self.upgrade(crash_at="after-files")
        self.simulate_death(purpose="upgrade")
        (self.root / up.RECEIPT_NAME).write_bytes(b"{not json")
        self.assertRefused(m.recover(self.root), m.BLOCKED_RECEIPT_TAMPERED)


# --------------------------------------------------------------------------- #
# a write that fails (as opposed to a process that stops)
# --------------------------------------------------------------------------- #
class InjectedFailure(Base):
    def test_a_failed_fresh_install_leaves_nothing(self) -> None:
        for point in ("staged-files", "staged-marker", "staged-receipt",
                      "before-rename"):
            with self.subTest(point=point):
                self.fresh_case()
                self.assertRefused(self.install(apply=True, fail_at=point),
                                   m.BLOCKED_ABORTED)
                self.assertNothingWritten()

    def test_a_failed_upgrade_puts_the_previous_version_back(self) -> None:
        for point in ("after-backup", "after-files", "after-marker", "after-receipt"):
            with self.subTest(point=point):
                self.fresh_case()
                self.install_ok()
                before = tree_digest(self.root)
                self.assertRefused(self.upgrade(fail_at=point), m.BLOCKED_ABORTED)
                self.assertEqual(before, tree_digest(self.root),
                                 "a failed upgrade left a different root: a new "
                                 "receipt, a mix of versions, or a backup")
                self.assertFalse((self.root / up.LOCK_NAME).exists())
                self.assertFalse((self.root / up.BACKUP_DIR).exists())
                self.assertFalse(m.lock_path_for(self.root).exists())
                self.assertEqual(m.OK, self.revoke().decision,
                                 "the root is still revocable after a failed upgrade")

    def test_a_failed_upgrade_is_retryable(self) -> None:
        self.install_ok()
        self.assertRefused(self.upgrade(fail_at="after-files"), m.BLOCKED_ABORTED)
        retried = self.upgrade()
        self.assertEqual(m.OK, retried.decision, retried.reasons)
        self.assertEqual(self.fx.blobs2[m.SIDECAR_FILES[1]],
                         bytes_of(self.root, m.SIDECAR_FILES[1]))

    def test_a_crash_and_a_failure_are_different_events(self) -> None:
        """The two injections must not be the same test twice."""
        with self.assertRaises(m.SimulatedCrash):
            self.install(apply=True, crash_at="before-rename")
        self.assertTrue(m._staging_candidates(self.root), "a death leaves debris")
        self.fresh_case()
        self.assertRefused(self.install(apply=True, fail_at="before-rename"),
                           m.BLOCKED_ABORTED)
        self.assertEqual((), m._staging_candidates(self.root),
                         "a failure is cleaned up; only a death is not")


# --------------------------------------------------------------------------- #
# revoke: #506's entrypoint, consuming this module's receipt
# --------------------------------------------------------------------------- #
class RevokeThroughUpstream(Base):
    def test_revoke_deletes_the_two_files_and_the_evidence(self) -> None:
        self.install_ok()
        outcome = self.revoke()
        self.assertEqual(m.OK, outcome.decision, outcome.reasons)
        self.assertEqual(tuple(sorted(m.SIDECAR_FILES)),
                         tuple(sorted(outcome.deleted)))
        self.assertFalse(self.root.exists())
        self.assertEqual(BACKEND, outcome.backend_id)

    def test_a_second_revoke_is_refused_by_name_and_deletes_nothing(self) -> None:
        self.install_ok()
        self.assertEqual(m.OK, self.revoke().decision)
        again = self.revoke()
        self.assertRefused(again, m.BLOCKED_NO_RECEIPT)
        self.assertEqual((), again.deleted)
        third = self.revoke()
        self.assertEqual(again.code, third.code,
                         "a repeated revoke must be idempotent in what it says, not "
                         "just in what it does")

    def test_a_revoke_for_another_backend_is_refused(self) -> None:
        self.install_ok()
        outcome = self.revoke(identity_=identity(next_backend_id="next-backend-OTHER"))
        self.assertRefused(outcome, m.BLOCKED_BACKEND_MISMATCH)
        for rel in m.SIDECAR_FILES:
            self.assertEqual(self.fx.blobs1[rel], bytes_of(self.root, rel))

    def test_a_revoke_on_another_boot_is_refused(self) -> None:
        """The receipt pins the boot it was written in; a new boot is a new fact."""
        self.install_ok()
        self.assertRefused(self.revoke(identity_=identity(boot_id=OTHER_BOOT)),
                           m.BLOCKED_BOOT_MISMATCH)
        self.assertTrue(self.root.exists())

    def test_a_revoke_grant_for_another_operator_is_refused(self) -> None:
        """The operator is asked here, not upstream.

        #506's `revoke()` compares the grant's `device_ref` against the receipt
        and never looks at `operator`; a revoke grant issued to somebody else
        would be honoured if this module did not ask. Withdrawing
        `_guard_grant_revoke` lets the delete happen, which is what makes this
        test the negative control for that guard.
        """
        self.install_ok()
        outcome = self.revoke(grant_=grant("revoke", operator="someone"))
        self.assertRefused(outcome, m.BLOCKED_IDENTITY_MISMATCH)
        self.assertTrue(self.root.exists(), "a refused revoke deleted the root")
        for rel in m.SIDECAR_FILES:
            self.assertEqual(self.fx.blobs1[rel], bytes_of(self.root, rel), rel)

    def test_a_revoke_from_another_device_is_refused(self) -> None:
        self.install_ok()
        outcome = self.revoke(grant_=grant("revoke", device="other-device"),
                              identity_=identity(device_ref="other-device"))
        self.assertRefused(outcome, m.BLOCKED_IDENTITY_MISMATCH)
        self.assertTrue(self.root.exists())

    def test_an_install_grant_does_not_cover_a_revoke(self) -> None:
        self.install_ok()
        self.assertRefused(self.revoke(grant_=grant("install")), m.BLOCKED_NO_GRANT)

    def test_a_revoke_needs_a_marker(self) -> None:
        self.install_ok()
        (self.root / up.OWNER_MARKER_NAME).unlink()
        self.assertRefused(self.revoke(), m.BLOCKED_BACKEND_MISSING)
        self.assertTrue(self.root.exists())

    def test_a_marker_without_a_backend_is_refused(self) -> None:
        self.install_ok()
        path = self.root / up.OWNER_MARKER_NAME
        marker = json.loads(path.read_bytes())
        del marker["next_backend_id"]
        path.write_bytes(json.dumps(marker, sort_keys=True, indent=2).encode("utf-8"))
        self.assertRefused(self.revoke(), m.BLOCKED_BACKEND_MISSING)

    def test_a_file_edited_after_install_is_not_deleted(self) -> None:
        self.install_ok()
        target = self.root / m.SIDECAR_FILES[0]
        target.write_bytes(target.read_bytes() + b"# someone else's edit\n")
        self.assertRefused(self.revoke(), m.BLOCKED_MODIFIED_AFTER_INSTALL)
        self.assertTrue(target.exists())

    def test_a_root_without_a_receipt_cannot_be_revoked(self) -> None:
        self.root.mkdir()
        (self.root / "stray").write_bytes(b"x")
        self.assertRefused(self.revoke(), m.BLOCKED_NO_RECEIPT)
        self.assertTrue((self.root / "stray").exists())

    def test_a_revoke_is_refused_while_a_writer_holds_the_root(self) -> None:
        self.install_ok()
        lock = m.acquire_lock(self.root, boot_id=BOOT, purpose="upgrade")
        try:
            self.assertRefused(self.revoke(), m.BLOCKED_CONCURRENT_WRITER)
        finally:
            lock.release()

    def test_a_legacy_root_cannot_be_revoked(self) -> None:
        self.install_ok()
        self.assertRefused(self.revoke(legacy_roots=[self.root]),
                           m.BLOCKED_LEGACY_ROOT)
        self.assertTrue(self.root.exists())

    def test_the_deletion_is_upstream_code_not_a_second_copy(self) -> None:
        """This module must not carry its own delete: `revoke` is #506's."""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertIn("revoke", called)
        self.assertNotIn("rmtree", called)
        self.assertIn("result = mod.revoke(", MODULE_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# the whole lifecycle across independent Python processes
# --------------------------------------------------------------------------- #
class IndependentProcesses(Base):
    """Install, fail an upgrade, and revoke — each in its own interpreter.

    Nothing is shared between these processes except the files on disk, which is
    the only channel a restart leaves intact. If the receipt were held in memory
    anywhere, this class would not pass.
    """

    def run_raw(self, *args: str) -> tuple[int, str, str]:
        done = subprocess.run([sys.executable, str(MODULE_PATH), *args],
                              capture_output=True, text=True, check=False)
        return done.returncode, done.stdout, done.stderr

    def run_cli(self, *args: str) -> tuple[int, dict]:
        code, out, err = self.run_raw(*args)
        try:
            payload = json.loads(out)
        except ValueError:
            self.fail(f"the CLI printed no JSON\nstdout: {out}\nstderr: {err}")
        return code, payload

    def common(self, ref: str, version: str) -> list[str]:
        return ["--repo", str(self.fx.repo), "--ref", ref, "--version", version,
                "--root", str(self.root), "--prefix", str(self.prefix),
                "--device-ref", DEVICE, "--boot-id", BOOT, "--backend-id", BACKEND,
                "--operator", OPERATOR, "--grant-id", "grant-cli"]

    def test_install_then_revoke_across_processes(self) -> None:
        code, installed = self.run_cli("--apply", *self.common(self.fx.commit1, V1))
        self.assertEqual(0, code, installed)
        self.assertEqual(m.OK, installed["decision"], installed)
        self.assertTrue(installed["applied"])
        self.assertEqual(m.VERIFICATION["device"], installed["verification"]["device"])
        self.assertIn("checksum-only",
                      installed["verification"]["receipt_authentication"])
        self.assertIn("not a signature",
                      installed["verification"]["receipt_authentication"].lower())
        receipt = up.read_receipt(self.root)
        assert receipt is not None
        self.assertEqual(V1, receipt.version)

        code, revoked = self.run_cli("--revoke", *self.common(self.fx.commit1, V1))
        self.assertEqual(0, code, revoked)
        self.assertEqual(m.OK, revoked["decision"], revoked)
        self.assertEqual(sorted(m.SIDECAR_FILES), sorted(revoked["deleted"]))
        self.assertFalse(self.root.exists())

        code, again = self.run_cli("--revoke", *self.common(self.fx.commit1, V1))
        self.assertEqual(2, code)
        self.assertEqual(m.BLOCKED_NO_RECEIPT, again["code"])
        self.assertEqual([], again["deleted"])

    def test_install_upgrade_failure_and_rollback_across_processes(self) -> None:
        """install → upgrade fails → the previous bytes are still there → revoke."""
        code, _ = self.run_cli("--apply", *self.common(self.fx.commit1, V1))
        self.assertEqual(0, code)
        before = tree_digest(self.root)

        code, failed = self.run_cli("--apply", "--fail-at", "after-files",
                                    *self.common(self.fx.commit2, V2))
        self.assertEqual(2, code, failed)
        self.assertEqual(m.BLOCKED_ABORTED, failed["code"], failed)
        self.assertEqual(before, tree_digest(self.root),
                         "a failed upgrade in another process left a different root")

        code, revoked = self.run_cli("--revoke", *self.common(self.fx.commit1, V1))
        self.assertEqual(0, code, revoked)
        self.assertEqual(m.OK, revoked["decision"], revoked)
        self.assertFalse(self.root.exists())

    def test_a_death_in_another_process_is_repaired_then_revoked(self) -> None:
        code, _ = self.run_cli("--apply", *self.common(self.fx.commit1, V1))
        self.assertEqual(0, code)
        before = sidecar_bytes(self.root)

        code, out, err = self.run_raw("--apply", "--crash-at", "after-files",
                                      *self.common(self.fx.commit2, V2))
        self.assertEqual(1, code, f"a simulated death must not exit 0 or 2\n{out}{err}")
        self.assertTrue((self.root / up.LOCK_NAME).exists())
        # A death at this point has already replaced the files and not yet the
        # receipt: the root is a mix, and the receipt in it is still the old one.
        receipt = up.read_receipt(self.root)
        assert receipt is not None
        self.assertEqual(V1, receipt.version)
        self.assertNotEqual(before, sidecar_bytes(self.root))
        self.simulate_death(purpose="upgrade")

        code, recovered = self.run_cli("--recover", "--root", str(self.root))
        self.assertEqual(0, code, recovered)
        self.assertEqual(m.OK, recovered["decision"], recovered)
        self.assertEqual(sorted(m.SIDECAR_FILES), sorted(recovered["restored"]))
        self.assertFalse((self.root / up.LOCK_NAME).exists())
        self.assertEqual(before, sidecar_bytes(self.root))

        code, revoked = self.run_cli("--revoke", *self.common(self.fx.commit1, V1))
        self.assertEqual(0, code, revoked)
        self.assertEqual(m.OK, revoked["decision"], revoked)
        self.assertFalse(self.root.exists())

    def test_the_plan_verb_writes_nothing(self) -> None:
        code, planned = self.run_cli(*self.common(self.fx.commit1, V1))
        self.assertEqual(0, code, planned)
        self.assertEqual(m.DRY_RUN, planned["code"])
        self.assertFalse(self.root.exists())

    def test_interface_verb_reports_a_compatible_upstream(self) -> None:
        code, report = self.run_cli("--interface")
        self.assertEqual(0, code, report)
        self.assertTrue(report["compatible"])
        self.assertEqual([], report["missing"])
        self.assertEqual(list(m.FINDINGS), report["findings"])


# --------------------------------------------------------------------------- #
# the Legacy control path is not touched
# --------------------------------------------------------------------------- #
class SidecarTransactionInterop(Base):
    """Two repairs of one root, and why only one of them may run.

    Wave 19 gave #506 a write-ahead journal, so `sidecar_release.recover()` is
    now the only verb allowed to look at an interrupted write. This installer has
    a repair of its own — it puts bytes back from `.sidecar-backup/<version>/`
    and rebuilds the marker — and running it while a journal is on disk means two
    modules rewriting the same files from two different ideas of what the
    transaction was. The installer therefore refuses and names the engine's verb.

    The interrupted transaction here is real: a separate interpreter is killed at
    a counted rename, so what the installer meets is the state a machine leaves.
    """

    def sidecar_spec(self, descriptor, blobs) -> Path:
        body = {"engine": str(up.__file__), "root": str(self.root),
                "name": descriptor.name, "version": descriptor.version,
                "commit": descriptor.commit, "entrypoint": descriptor.entrypoint,
                "files": [list(pair) for pair in descriptor.files],
                "sources": {rel: data.hex() for rel, data in blobs.items()},
                "device_ref": DEVICE, "boot_id": BOOT,
                "lock_token": "wave18-interop"}
        path = self.tmp / f"spec-{descriptor.version.replace('+', '_')}.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def crash_a_sidecar_upgrade(self) -> None:
        spec1 = self.sidecar_spec(self.fx.descriptor1, self.fx.blobs1)
        seeded = subprocess.run(
            [sys.executable, "-I", str(CHILD), "install", "--spec", str(spec1)],
            capture_output=True, text=True, check=False, timeout=180)
        self.assertEqual(seeded.returncode, 0, seeded.stdout + seeded.stderr)

        spec2 = self.sidecar_spec(self.fx.descriptor2, self.fx.blobs2)
        killed = subprocess.run(
            [sys.executable, "-I", str(CHILD), "upgrade", "--spec", str(spec2),
             "--die-at", "2"],
            capture_output=True, text=True, check=False, timeout=180)
        self.assertEqual(killed.returncode, 86, killed.stdout + killed.stderr)
        self.assertTrue((self.root / up.JOURNAL_NAME).is_file(),
                        "the crash was supposed to leave a journal")
        self.assertTrue((self.root / up.LOCK_NAME).is_file())

    def test_a_pending_sidecar_journal_is_refused_and_named(self) -> None:
        self.crash_a_sidecar_upgrade()
        before = tree_digest(self.root)
        outcome = m.recover(self.root)
        self.assertRefused(outcome, m.BLOCKED_TXN_PENDING)
        self.assertIn(up.JOURNAL_NAME, " ".join(outcome.reasons))
        self.assertEqual(tree_digest(self.root), before,
                         "the refusal changed the root it declined to repair")

    def test_the_same_word_comes_from_both_modules(self) -> None:
        """An operator reading the two refusal codes sees one situation."""
        self.assertEqual(m.BLOCKED_TXN_PENDING, "BLOCKED_TXN_PENDING")
        self.assertEqual(getattr(up, "BLOCKED_TXN_PENDING", "BLOCKED_TXN_PENDING"),
                         m.BLOCKED_TXN_PENDING)

    def test_the_engine_repair_unblocks_the_installer(self) -> None:
        self.crash_a_sidecar_upgrade()
        engine = up.recover(self.root,
                            grant=up.Grant(grant_id="grant-recover-1",
                                           operator=OPERATOR, action="recover",
                                           device_ref=DEVICE),
                            device_ref=DEVICE, boot_id=BOOT)
        self.assertEqual(up.OK, engine.decision, engine.reasons)
        self.assertFalse((self.root / up.JOURNAL_NAME).exists())

        after = m.recover(self.root)
        self.assertEqual(m.OK, after.decision, after.reasons)
        self.assertEqual(m.NOTHING_TO_RECOVER, after.code, after.reasons)
        for rel, data in self.fx.blobs2.items():
            self.assertEqual(data, bytes_of(self.root, rel), rel)


class LegacyUntouched(Base):
    class Gateway:
        deliveries = ("delivery-1",)

        def get_session(self, key):
            return {"key": key, "state": "live"}

        def get_run(self, key):
            return {"key": key, "state": "running"}

    def test_install_upgrade_and_revoke_leave_legacy_state_identical(self) -> None:
        self.legacy.mkdir()
        (self.legacy / "legacy-config.json").write_bytes(b'{"mode": "legacy"}\n')
        legacy_before = tree_digest(self.legacy)
        gateway = self.Gateway()
        before = up.observe_legacy(gateway, session_key="s-1", run_key="r-1")

        self.install_ok()
        outcome = self.upgrade()
        self.assertEqual(m.OK, outcome.decision, outcome.reasons)
        revoked = self.revoke(self.fx.descriptor2)
        self.assertEqual(m.OK, revoked.decision, revoked.reasons)

        after = up.observe_legacy(gateway, session_key="s-1", run_key="r-1")
        self.assertEqual((), up.legacy_drift(before, after))
        self.assertEqual(legacy_before, tree_digest(self.legacy))
        self.assertEqual(("delivery-1",), gateway.deliveries)

    def test_a_legacy_observation_that_did_change_is_visible(self) -> None:
        """A negative control for the test above: `legacy_drift` can report."""
        gateway = self.Gateway()
        before = up.observe_legacy(gateway, session_key="s-1", run_key="r-1")
        gateway.deliveries = ("delivery-2",)
        after = up.observe_legacy(gateway, session_key="s-1", run_key="r-1")
        self.assertNotEqual((), up.legacy_drift(before, after))


# --------------------------------------------------------------------------- #
# the source itself: offline, read-only git, no second delete
# --------------------------------------------------------------------------- #
class SourceHygiene(unittest.TestCase):
    def setUp(self) -> None:
        self.source = MODULE_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def _imported(self) -> set[str]:
        imported: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        return imported

    def _called_attributes(self) -> set[str]:
        return {node.func.attr for node in ast.walk(self.tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}

    def test_no_network_modules_are_imported(self) -> None:
        imported = self._imported()
        for forbidden in ("socket", "http", "urllib", "requests", "ssl",
                          "telnetlib", "asyncio"):
            self.assertNotIn(forbidden, imported)

    def test_no_service_manager_no_shell_and_no_tree_delete(self) -> None:
        """The prose may name `systemctl`; the code must not call anything like it."""
        called = self._called_attributes()
        for forbidden in ("rmtree", "system", "call", "Popen", "popen", "exec",
                          "spawn", "systemctl", "start", "stop"):
            self.assertNotIn(forbidden, called, f"{forbidden}() is called")
        self.assertNotIn("import shutil", self.source)

    def test_git_is_only_asked_read_only_questions(self) -> None:
        assigned = None
        for node in ast.walk(self.tree):
            if (isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name)
                            and target.id == "READ_ONLY_GIT"
                            for target in node.targets)):
                assigned = node.value
        self.assertIsNotNone(assigned, "READ_ONLY_GIT is not assigned")
        assert assigned is not None
        self.assertIsInstance(assigned, ast.Call)
        literal = assigned.args[0]
        self.assertIsInstance(literal, ast.Set)
        verbs = {element.value for element in literal.elts
                 if isinstance(element, ast.Constant)}
        self.assertEqual({"rev-parse", "cat-file"}, verbs)
        self.assertEqual({"rev-parse", "cat-file"}, set(m.READ_ONLY_GIT))

    def test_the_git_helper_refuses_every_other_verb(self) -> None:
        for verb in ("reset", "checkout", "commit", "push", "clean", "rm"):
            with self.assertRaises(ValueError):
                m.git(tempfile.gettempdir(), [verb])
        with self.assertRaises(ValueError):
            m.git(tempfile.gettempdir(), [])

    def test_bytes_are_written_never_text(self) -> None:
        """`write_text` would translate newlines on Windows and move every digest."""
        self.assertNotIn("write_text(", self.source)
        self.assertIn("write_bytes(", self.source)
        self.assertIn("os.replace(", self.source)

    def test_a_tree_this_module_created_keeps_what_it_did_not_write(self) -> None:
        self.assertIn("def _discard(", self.source)
        self.assertIn("keeps it", self.source)
        self.assertIn("def _prune_backup(", self.source)

    def test_the_module_can_be_run_as_a_script(self) -> None:
        done = subprocess.run([sys.executable, str(MODULE_PATH), "--interface"],
                              capture_output=True, text=True, check=False)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(json.loads(done.stdout)["compatible"])

    def test_the_verification_split_is_declared_and_never_claimed_as_signed(
            self) -> None:
        self.assertEqual("SOURCE_VERIFIED", m.VERIFICATION["source"])
        self.assertEqual("REAL_DEVICE_NOT_RUN", m.VERIFICATION["device"])
        self.assertIn("NOT a signature", m.VERIFICATION["receipt_authentication"])
        self.assertNotIn("signed by", self.source.lower())
        for name in ("plan_install", "install", "apply_plan", "revoke_verified",
                     "rollback_to_previous", "recover", "descriptor_for"):
            self.assertTrue(callable(getattr(m, name, None)), name)


# --------------------------------------------------------------------------- #
# agreement with the module it consumes
# --------------------------------------------------------------------------- #
class UpstreamConformance(Base):
    """The same state must get the same refusal code from #506 and from here.

    Two implementations that disagree about what a root looks like are two
    vocabularies pretending to be one. Each case builds a state, asks both
    modules, and requires the same answer.
    """

    def theirs(self, root=None, *, sources=None, grant_=_UNSET, legacy_roots=()):
        return up.install(self.fx.descriptor1, root or self.root,
                          dict(self.fx.blobs1) if sources is None else sources,
                          grant=grant("install") if grant_ is _UNSET else grant_,
                          device_ref=DEVICE, boot_id=BOOT,
                          legacy_roots=list(legacy_roots))

    def test_a_non_empty_root_without_a_receipt(self) -> None:
        self.root.mkdir()
        (self.root / "stray").write_bytes(b"x")
        mine = self.install(apply=True)
        theirs = self.theirs()
        self.assertEqual(m.BLOCKED_ROOT_NOT_EMPTY, mine.code)
        self.assertEqual(mine.code, theirs.code)

    def test_a_legacy_root(self) -> None:
        self.legacy.mkdir()
        mine = self.install(root=self.legacy / "sidecar",
                            allowed_prefixes=[self.legacy],
                            legacy_roots=[self.legacy])
        theirs = self.theirs(root=self.legacy / "sidecar",
                             legacy_roots=[self.legacy])
        self.assertEqual(m.BLOCKED_LEGACY_ROOT, mine.code)
        self.assertEqual(mine.code, theirs.code)

    def test_bytes_that_do_not_match_the_pin(self) -> None:
        wrong = {path: b"wrong" for path in m.SIDECAR_FILES}
        mine = self.install(sources=wrong, apply=True)
        theirs = self.theirs(sources=wrong)
        self.assertEqual(m.BLOCKED_SOURCE_DIGEST, mine.code)
        self.assertEqual(mine.code, theirs.code)

    def test_no_grant(self) -> None:
        mine = self.install(grant_=None)
        theirs = self.theirs(grant_=None)
        self.assertEqual(m.BLOCKED_NO_GRANT, mine.code)
        self.assertEqual(m.BLOCKED_NO_GRANT, theirs.code)

    def test_a_root_that_is_a_link(self) -> None:
        target = self.tmp / "target"
        target.mkdir()
        if not make_dir_link(self.root, target):
            self.skipTest("this platform will not create a directory link")
        mine = self.install(apply=True)
        theirs = self.theirs()
        self.assertEqual(m.BLOCKED_ROOT_IS_SYMLINK, mine.code)
        self.assertEqual(mine.code, theirs.code)

    def test_an_unpinned_version(self) -> None:
        descriptor = dataclasses.replace(self.fx.descriptor1, version="latest")
        mine = m.install(descriptor, self.root, grant=grant("install"),
                         identity=identity(), allowed_prefixes=[self.prefix],
                         provenance=None)
        theirs = up.install(descriptor, self.root, dict(self.fx.blobs1),
                            grant=grant("install"), device_ref=DEVICE, boot_id=BOOT)
        self.assertEqual(m.BLOCKED_NOT_PINNED, mine.code)
        self.assertEqual(mine.code, theirs.code)

    def test_a_relative_root(self) -> None:
        mine = self.install(root=Path("sidecar"))
        theirs = self.theirs(root=Path("sidecar"))
        self.assertEqual(m.BLOCKED_ROOT_NOT_ABSOLUTE, mine.code)
        self.assertEqual(mine.code, theirs.code)

    def test_the_two_receipt_bodies_have_the_same_shape(self) -> None:
        """One definition of a receipt: the field set must be identical."""
        self.install_ok()
        mine = up.read_receipt(self.root)
        assert mine is not None
        digests = dict(self.fx.descriptor1.files)
        records = tuple(up.FileRecord(path=path, sha256=digests[path], size=len(body))
                        for path, body in sorted(self.fx.blobs1.items()))
        theirs = up._receipt_body(self.fx.descriptor1,
                                  root_real=os.path.realpath(str(self.root)),
                                  device_ref=DEVICE, boot_id=BOOT,
                                  grant=grant("install"), records=records,
                                  previous=None)
        self.assertEqual(sorted(theirs), sorted(set(mine.raw) - {"receipt_digest"}))
        self.assertEqual(theirs["manifest_digest"], mine.manifest_digest)
        self.assertEqual([record["path"] for record in theirs["files"]],
                         [record["path"] for record in mine.raw["files"]])
        self.assertEqual(up.receipt_digest(theirs), mine.digest)

    def test_namespacing_is_this_modules_guard_not_the_upstreams(self) -> None:
        """A difference a reviewer should see, asserted rather than described.

        #506 writes wherever it is pointed; this module refuses a root outside
        every allowed prefix. The upstream call therefore succeeds — and what it
        wrote is removed here, in this test's own temporary directory.
        """
        other = self.tmp / "other" / "opt"
        other.mkdir(parents=True)
        root = other / "sidecar"
        mine = self.install(root=root, apply=True)
        self.assertEqual(m.BLOCKED_ROOT_NOT_NAMESPACED, mine.code)
        self.assertFalse(root.exists())
        theirs = self.theirs(root=root)
        self.assertEqual(m.OK, theirs.code, "upstream writes wherever it is pointed")
        self.assertTrue((root / m.SIDECAR_FILES[0]).exists())
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
