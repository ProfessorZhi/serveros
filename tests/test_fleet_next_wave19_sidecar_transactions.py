"""Wave 19: a write that either happens or leaves nothing behind.

Wave 18 measured two real failures and this file is where they stop being able
to happen:

  * **#514's trap** — `install()` wrote the release one file at a time and wrote
    the receipt last, so an obstacle it only met halfway (a *regular file* where
    an upgrade needed a directory) left ten files behind and no receipt. The root
    then answered `BLOCKED_FOREIGN_PRESENT` to every revoke, because a revoke is
    not allowed to delete a file no receipt records — correctly.
  * **F1** — `restore()` rebuilt the ownership marker from its own seven keys, so
    the Next backend identity #513 hangs on the same file was dropped, and the
    next revoke said `BLOCKED_BACKEND_MISSING`.

The fix is not "be more careful". It is a shape:

  1. every obstacle is found **before** the first rename (`BLOCKED_UPGRADE_OBSTACLE`);
  2. the target bytes are staged **inside the root** and re-digested there;
  3. a **write-ahead journal** names every path, digest and evidence body the
     transaction may touch, and is the *only* authority a compensation or a
     repair accepts;
  4. `recover()` finishes the transaction when it can and undoes it when it
     cannot, and refuses — deletes nothing — when it can neither prove which.

Nothing in this file touches a real root, a real service, a real grant or a real
device. Every path is a `tempfile` directory, every "crash" is `os._exit` in a
child process, and every refusal is paired with the state it must leave behind.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (ROOT / "integrations" / "fleet-next-wave17-sidecar-release"
               / "sidecar_release.py")
CHILD = Path(__file__).resolve().parent / "wave19_child.py"

#: `integrations/` has no `__init__.py`, so the module is loaded from its path —
#: under the name the rest of the suite uses, so there is one module object per
#: file (see `tests/test_fleet_next_wave17_sidecar_release.py`).
_spec = importlib.util.spec_from_file_location("sidecar_release", MODULE_PATH)
m = sys.modules.get("sidecar_release")
if m is None:
    m = importlib.util.module_from_spec(_spec)
    sys.modules["sidecar_release"] = m
    _spec.loader.exec_module(m)

DEVICE = "device:wave19-test-01"
BOOT = "boot-2026-10-29-a"

V1 = {
    "bin/sidecar.py": b"#!/usr/bin/env python3\nprint('sidecar 0.1.0')\n",
    "etc/sidecar.json": b'{"pinned": "0.1.0"}\n',
}
V2 = {
    "bin/sidecar.py": b"#!/usr/bin/env python3\nprint('sidecar 0.2.0')\n",
    "etc/sidecar.json": b'{"pinned": "0.2.0"}\n',
}
#: The upgrade every crash test in this file performs: both pinned files change
#: *and* a third appears. The two changes matter — `_commit` skips a target that
#: already holds the new bytes, so a release that only added a file would have
#: one commit-time rename and a sweep over it would measure almost nothing.
V2_AND_ADDED = dict(V2, **{"etc/added-in-020.json": b'{"added": "0.2.0"}\n'})

#: The rename sequence that upgrade performs, in order. A kill point is a
#: position in *this* list, not an abstract "k": `_write_atomic` moves the
#: journal, the marker and the receipt on with a rename of their own, and
#: `_commit` moves one staged file at a time in descriptor order (sorted).
#: Entry 1 is the journal on purpose — a kill *before* the journal is written is
#: a state the repair has to survive too, and it is the one that leaves a
#: half-written journal temp behind.
UPGRADE_RENAMES = (
    m.JOURNAL_NAME,
    "bin/sidecar.py",
    "etc/added-in-020.json",
    "etc/sidecar.json",
    m.OWNER_MARKER_NAME,
    m.RECEIPT_NAME,
)


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def release(files=None, *, version="0.1.0", commit="a" * 40, name="sidecar"):
    blobs = dict(files if files is not None else V1)
    entries = tuple(sorted((path, sha(body)) for path, body in blobs.items()))
    return m.Descriptor(name=name, version=version, commit=commit, files=entries,
                        entrypoint=sorted(blobs)[0]), blobs


def grant(action, *, device=DEVICE):
    return m.Grant(grant_id=f"grant-{action}-1", operator="owner",
                   action=action, device_ref=device)


def tree_state(root: Path) -> dict[str, str]:
    """Every entry under `root` with its digest — the "did anything move" probe."""
    found: dict[str, str] = {}
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            found[rel] = "link"
        elif path.is_file():
            found[rel] = sha(path.read_bytes())
        elif path.is_dir():
            found[rel + "/"] = "dir"
    return found


def files_under(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*")
                  if path.is_file() and not path.is_symlink())


def orphans(root: Path) -> list[str]:
    """Files the receipt in `root` does not own, backup contents included.

    This is #514's own definition, so the numbers here can be compared with the
    numbers it reported: the receipt's files, the receipt, the marker, and the
    contents of the backup directory it names.
    """
    receipt = m.read_receipt(root)
    if receipt is None:
        return files_under(root)
    owned = {record.path for record in receipt.files}
    owned |= {m.RECEIPT_NAME, m.OWNER_MARKER_NAME}
    if receipt.previous is not None:
        owned |= {f"{receipt.previous.backup_dir}/{record.path}"
                  for record in receipt.previous.files}
    return [rel for rel in files_under(root) if rel not in owned]


def make_dir_link(target: Path, link: Path) -> None:
    """A directory link, with a junction fallback for hosts without symlinks."""
    if not hasattr(os, "symlink"):  # pragma: no cover - Windows always has it
        raise unittest.SkipTest("this host cannot make links")
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - privileges
        if os.name != "nt":
            raise unittest.SkipTest("this host forbids symlinks")
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, text=True, check=False)
        if done.returncode != 0:  # pragma: no cover - no junction either
            raise unittest.SkipTest(f"no link could be made: {done.stderr}")


class Wave19Test(unittest.TestCase):
    def setUp(self) -> None:
        self._dirs: list[Path] = []
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        for path in self._dirs:
            shutil.rmtree(path, ignore_errors=True)

    def tmp(self) -> Path:
        path = Path(tempfile.mkdtemp(prefix="wave19-txn-"))
        self._dirs.append(path)
        return path

    def fresh_root(self) -> Path:
        return self.tmp() / "root"

    def install(self, files=None, *, root=None, version="0.1.0", commit="a" * 40,
                **kw):
        desc, blobs = release(files, version=version, commit=commit)
        root = root or self.fresh_root()
        result = m.install(desc, root, blobs, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT, **kw)
        self.assertEqual(result.decision, m.OK, result.reasons)
        return desc, blobs, root, result

    def upgraded(self):
        v1, blobs1, root, _r = self.install()
        v2, blobs2 = release(V2, version="0.2.0", commit="c" * 40)
        result = m.install(v2, root, blobs2, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT,
                           replace=m.read_receipt(root))
        self.assertEqual(result.decision, m.OK, result.reasons)
        return v1, blobs1, v2, blobs2, root

    def recover(self, root, *, device=DEVICE, boot=BOOT, action="recover", **kw):
        return m.recover(root, grant=grant(action, device=device),
                         device_ref=device, boot_id=boot, **kw)

    def revoke(self, desc, root, **kw):
        args = dict(grant=grant("revoke"), device_ref=DEVICE, boot_id=BOOT)
        args.update(kw)
        return m.revoke(desc, root, **args)

    def assertCode(self, result, code) -> None:
        self.assertEqual(result.decision, m.BLOCKED, result.reasons)
        self.assertEqual(result.code, code, result.reasons)

    # -- child processes ---------------------------------------------------- #
    def spec(self, desc, blobs, root: Path, *, token="lock-token-wave19",
             device=DEVICE, boot=BOOT) -> Path:
        """Everything a child process needs, in one file inside the world."""
        path = self.tmp() / "spec.json"
        body = {
            "engine": str(MODULE_PATH),
            "root": str(root),
            "name": desc.name,
            "version": desc.version,
            "commit": desc.commit,
            "entrypoint": desc.entrypoint,
            "files": [list(pair) for pair in desc.files],
            "sources": {path_: body_.hex() for path_, body_ in blobs.items()},
            "device_ref": device,
            "boot_id": boot,
            "lock_token": token,
        }
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def child(self, op: str, spec: Path, *extra: str, expect: int = 0) -> dict:
        """Run one operation in its own process and read its one JSON line."""
        done = subprocess.run(
            [sys.executable, "-I", str(CHILD), op, "--spec", str(spec), *extra],
            capture_output=True, text=True, timeout=180, check=False)
        self.assertEqual(done.returncode, expect,
                         f"{op}: exit {done.returncode}\n"
                         f"stdout: {done.stdout}\nstderr: {done.stderr}")
        lines = [line for line in done.stdout.splitlines() if line.strip()]
        self.assertTrue(lines, f"{op}: no output at all\nstderr: {done.stderr}")
        return json.loads(lines[-1])

    def crash_after(self, die_at: int):
        """A real upgrade killed by `os._exit` *instead of* rename `die_at`.

        `die_at` indexes `UPGRADE_RENAMES`, so 1 means "the journal write was
        killed", 2 "the first file was committed and then the process died", and
        `len(UPGRADE_RENAMES)` "everything but the receipt is on disk". The kill
        is a process death, not an exception: no compensation runs, so the state
        handed back is exactly what the disk held at that instant.

        Returns `(v1, blobs1, v2, blobs2, upgrade_spec, root)`.
        """
        self.assertIn(die_at, range(1, len(UPGRADE_RENAMES) + 1))
        desc_v1, blobs1, root, _r = self.install()
        v2, blobs2 = release(V2_AND_ADDED, version="0.2.0", commit="c" * 40)
        spec = self.spec(v2, blobs2, root)
        killed = self.child("upgrade", spec, "--die-at", str(die_at), expect=86)
        self.assertEqual(killed["killed_at"], die_at)
        return desc_v1, blobs1, v2, blobs2, spec, root


# --------------------------------------------------------------------------- #
# #514's trap: an obstacle is a refusal before the first rename
# --------------------------------------------------------------------------- #
class TheTrapFromFiveHundredFourteen(Wave19Test):
    def test_a_file_where_a_directory_is_needed_writes_nothing(self):
        """#514, reproduced: the upgrade meets `zz` halfway through its list.

        The pre-fix engine wrote `next/aaa-extra.py` first and then failed on
        `zz/blocked.py`, leaving ten files and no receipt. The assertion is not
        "an error was reported" — it is that the root is *byte for byte* what it
        was before the call.
        """
        v1, blobs1, root, _r = self.install()
        (root / "zz").write_bytes(b"a stale file where a directory is needed\n")
        before = tree_state(root)

        v2, blobs2 = release(dict(blobs1, **{
            "next/aaa-extra.py": b"EXTRA = 'wave19'\n",
            "zz/blocked.py": b"BLOCKED = False\n",
        }), version="0.2.0", commit="c" * 40)
        result = m.install(v2, root, blobs2, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT,
                           replace=m.read_receipt(root))

        self.assertCode(result, m.BLOCKED_UPGRADE_OBSTACLE)
        self.assertEqual(tree_state(root), before)
        self.assertEqual(result.written, ())
        self.assertFalse((root / m.JOURNAL_NAME).exists())
        self.assertFalse((root / m.STAGING_DIR).exists())
        self.assertEqual(m.read_receipt(root).version, "0.1.0")
        self.assertEqual(orphans(root), ["zz"])

    def test_the_obstacle_is_named_not_counted(self):
        """The refusal says which path met what, so an operator can act on it."""
        v1, blobs1, root, _r = self.install()
        (root / "zz").write_bytes(b"stale\n")
        v2, blobs2 = release(dict(blobs1, **{"zz/blocked.py": b"X = 1\n"}),
                             version="0.2.0", commit="c" * 40)
        result = m.install(v2, root, blobs2, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT,
                           replace=m.read_receipt(root))
        self.assertCode(result, m.BLOCKED_UPGRADE_OBSTACLE)
        self.assertIn("zz", " ".join(result.reasons))

    def test_a_target_that_is_a_directory_is_the_same_refusal(self):
        v1, blobs1, root, _r = self.install()
        (root / "etc" / "added-in-020.json").mkdir()
        before = tree_state(root)
        v2, blobs2 = release(dict(blobs1, **{"etc/added-in-020.json": b"{}\n"}),
                             version="0.2.0", commit="c" * 40)
        result = m.install(v2, root, blobs2, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT,
                           replace=m.read_receipt(root))
        self.assertCode(result, m.BLOCKED_UPGRADE_OBSTACLE)
        self.assertEqual(tree_state(root), before)

    def test_a_link_on_the_way_is_refused_before_it_is_written_through(self):
        """Writing through a link would put a byte outside the receipt's root."""
        v1, blobs1, root, _r = self.install()
        outside = self.tmp() / "outside"
        outside.mkdir()
        make_dir_link(outside, root / "next")
        before = tree_state(root)
        v2, blobs2 = release(dict(blobs1, **{"next/aaa-extra.py": b"X = 1\n"}),
                             version="0.2.0", commit="c" * 40)
        result = m.install(v2, root, blobs2, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT,
                           replace=m.read_receipt(root))
        self.assertCode(result, m.BLOCKED_SYMLINK_TRAVERSAL)
        self.assertEqual(tree_state(root), before)
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_root_that_is_not_empty_and_has_no_receipt_is_refused_first(self):
        """The obstacle check is not the first refusal — an unknown root is.

        `install()` must not scan a directory it has no claim on, and it must not
        create one under a directory someone else filled: the "do I own this"
        question comes before "can I write here".
        """
        root = self.fresh_root()
        root.mkdir(parents=True)
        (root / "etc").write_bytes(b"a file where a directory is needed\n")
        before = tree_state(root)
        desc, blobs = release(V1)
        result = m.install(desc, root, blobs, grant=grant("install"),
                           device_ref=DEVICE, boot_id=BOOT)
        self.assertCode(result, m.BLOCKED_ROOT_NOT_EMPTY)
        self.assertEqual(tree_state(root), before)


# --------------------------------------------------------------------------- #
# a failure *during* the commit: the journal, and the two whole states
# --------------------------------------------------------------------------- #
class AFailureMidCommit(Wave19Test):
    """A one-shot fault at the k-th rename, swept over every commit point.

    The fault is injected at the process's own `os.replace`, so every rename
    before it *really happened*: the tree the repair meets is a real partial
    write. For each k the property is the same — after `recover()` the root is
    either the release it was or the release the write was making, never a
    mixture, and a revoke then finishes cleanly.
    """

    def fault_at(self, k: int, *, stays_broken: bool = False):
        """Break the k-th rename — or every rename from it on — and hand back `real`.

        `stays_broken` is what a full disk actually does: the failure that
        interrupts the commit is still there when the compensation tries to
        write the old bytes back.
        """
        real = m.os.replace
        seen = {"n": 0}

        def counted(src, dst, *args, **kwargs):
            seen["n"] += 1
            broke = seen["n"] >= k if stays_broken else seen["n"] == k
            if broke:
                raise OSError(28, "injected: no space left on device")
            return real(src, dst, *args, **kwargs)

        m.os.replace = counted
        self.addCleanup(setattr, m.os, "replace", real)
        return real

    def upgrade_with_fault(self, k: int, *, stays_broken: bool = False):
        """Run the upgrade with the k-th rename failing; return (result, root)."""
        desc_v1, blobs1, root, _r = self.install()
        before = tree_state(root)
        v2, blobs2 = release(V2_AND_ADDED, version="0.2.0", commit="c" * 40)
        real = self.fault_at(k, stays_broken=stays_broken)
        try:
            result = m.install(v2, root, blobs2, grant=grant("install"),
                               device_ref=DEVICE, boot_id=BOOT,
                               replace=m.read_receipt(root))
        finally:
            m.os.replace = real
        return desc_v1, v2, result, root, before

    def test_every_commit_point_ends_in_one_of_the_two_whole_states(self):
        """One injected fault at each rename — and afterwards the root is whole.

        The fault is raised from the process's own `os.replace`, once, so every
        rename before it *really happened* and every rename after it really
        happens too: this is the disk-full case that is survivable. Two whole
        states are acceptable and nothing else is: either the root is byte for
        byte what it was (the write undid itself and there is nothing left to
        recover), or a journal is there and `recover()` is what finishes it.
        """
        for k in range(1, len(UPGRADE_RENAMES) + 2):
            with self.subTest(rename=UPGRADE_RENAMES[k - 1] if k <= len(UPGRADE_RENAMES)
                              else "past the last rename"):
                desc_v1, v2, result, root, before = self.upgrade_with_fault(k)

                if result.decision == m.OK:
                    # k past the end: the upgrade never met its fault. The sweep
                    # still holds — nothing was left behind.
                    self.assertEqual(k, len(UPGRADE_RENAMES) + 1)
                    self.assertEqual(m.read_receipt(root).version, "0.2.0")
                    self.assertEqual(orphans(root), [])
                    continue
                self.assertIn(result.code,
                              (m.BLOCKED_INSTALL_ABORTED, m.BLOCKED_TXN_INCOMPLETE),
                              result.reasons)

                if result.code == m.BLOCKED_INSTALL_ABORTED:
                    # Undone completely: the failure is invisible afterwards, and
                    # a repair is told there is nothing to repair.
                    self.assertEqual(tree_state(root), before)
                    self.assertCode(self.recover(root), m.BLOCKED_NO_JOURNAL)
                else:  # pragma: no cover - a one-shot fault always undoes here
                    # The undo itself could not finish, so the journal is what
                    # says which direction `recover()` may take — either end is a
                    # whole state, and it must be the one that finishes.
                    self.assertTrue((root / m.JOURNAL_NAME).exists())
                    repaired = self.recover(root)
                    self.assertEqual(repaired.decision, m.OK, repaired.reasons)

                receipt = m.read_receipt(root)
                self.assertIsNotNone(receipt)
                self.assertTrue(receipt.is_intact())
                self.assertIn(receipt.version, ("0.1.0", "0.2.0"))
                state = tree_state(root)
                self.assertNotIn(m.JOURNAL_NAME, state)
                self.assertFalse(any(rel.startswith(m.STAGING_DIR + "/")
                                     for rel in state), state)
                self.assertEqual(orphans(root), [], state)
                # Every file the receipt names is the file on disk: the release
                # is whole, not merely present.
                for record in receipt.files:
                    self.assertEqual(state[record.path], record.sha256, record.path)

                descriptor = desc_v1 if receipt.version == "0.1.0" else v2
                revoked = self.revoke(descriptor, root)
                self.assertEqual(revoked.decision, m.OK, revoked.reasons)
                self.assertEqual(files_under(root), [])

    def test_a_fault_the_undo_cannot_survive_leaves_a_journal_not_an_exception(self):
        """A disk that stays full fails the undo too — and that is reported.

        The first version of this rewrite let the second `OSError` escape
        `install()`, so the caller got a traceback and no verdict while the
        journal sat on disk. The journal is still the authority: the verb that
        reads it is `recover()`, and it is what the operator is told to run.
        """
        desc_v1, v2, result, root, _before = self.upgrade_with_fault(
            2, stays_broken=True)
        self.assertEqual(result.decision, m.BLOCKED, result.reasons)
        self.assertEqual(result.code, m.BLOCKED_TXN_INCOMPLETE, result.reasons)
        self.assertTrue((root / m.JOURNAL_NAME).exists())

        # ... and with the fault gone, the same root finishes cleanly.
        repaired = self.recover(root)
        self.assertEqual(repaired.decision, m.OK, repaired.reasons)
        receipt = m.read_receipt(root)
        self.assertIn(receipt.version, ("0.1.0", "0.2.0"))
        self.assertEqual(orphans(root), [])
        descriptor = desc_v1 if receipt.version == "0.1.0" else v2
        self.assertEqual(self.revoke(descriptor, root).decision, m.OK)
        self.assertEqual(files_under(root), [])

    def test_a_short_write_in_the_staging_tree_is_caught_before_the_commit(self):
        """Staging is re-digested in place: a short copy is a failure, not a file.

        Without the re-read, `write_bytes` returning fewer bytes than asked (a
        full disk, an antivirus truncation) would be committed and the receipt
        would describe a file that is not on disk. The check happens while the
        transaction is still undoable, so nothing is left behind.
        """
        desc_v1, blobs1, root, _r = self.install()
        before = tree_state(root)
        victim = "etc/added-in-020.json"
        v2, blobs2 = release(V2_AND_ADDED, version="0.2.0", commit="c" * 40)
        real = m._stage_bytes
        shorted = {"done": False}

        def truncated(target_root, rel, body):
            if rel == victim and not shorted["done"]:
                shorted["done"] = True
                real(target_root, rel, body[:max(1, len(body) // 2)])
                return
            real(target_root, rel, body)

        m._stage_bytes = truncated
        try:
            result = m.install(v2, root, blobs2, grant=grant("install"),
                               device_ref=DEVICE, boot_id=BOOT,
                               replace=m.read_receipt(root))
        finally:
            m._stage_bytes = real
        self.assertTrue(shorted["done"], "the short write was never injected")
        self.assertCode(result, m.BLOCKED_INSTALL_ABORTED)
        self.assertIn(victim, " ".join(result.reasons))
        self.assertEqual(tree_state(root), before)

    def test_destroyed_evidence_still_lets_the_undo_finish_when_the_root_is_the_base(
            self):
        """Missing staged evidence is only *stuck* if the file moved — checked.

        A staged copy can be gone because the commit never started (nothing to
        undo, the file on disk is still the base) or because it was destroyed
        (do not claim an undo was verified). The difference is measured against
        the file, not assumed from the order of operations.
        """
        desc_v1, blobs1, v2, blobs2, spec, root = self.crash_after(3)
        # Block the forward direction, so the repair has to consider the undo.
        (root / m.STAGING_DIR / "new" / "etc" / "sidecar.json").unlink()
        (root / m.STAGING_DIR / "undo" / "__evidence__" / m.RECEIPT_NAME).unlink()

        repaired = self.recover(root)
        self.assertEqual(repaired.decision, m.OK, repaired.reasons)
        receipt = m.read_receipt(root)
        self.assertEqual(receipt.version, "0.1.0")
        self.assertTrue(receipt.is_intact())
        self.assertEqual(orphans(root), [])

    def test_destroyed_evidence_with_a_changed_marker_is_refused(self):
        """The same missing copy, but the marker is no longer what it started from."""
        desc_v1, blobs1, v2, blobs2, spec, root = self.crash_after(3)
        (root / m.STAGING_DIR / "new" / "etc" / "sidecar.json").unlink()
        (root / m.STAGING_DIR / "undo" / "__evidence__" / m.OWNER_MARKER_NAME).unlink()
        marker_path = root / m.OWNER_MARKER_NAME
        marker = json.loads(marker_path.read_bytes())
        marker["receipt_digest"] = "sha256:" + "0" * 64
        marker_path.write_bytes(json.dumps(marker).encode("utf-8"))
        before = tree_state(root)

        result = self.recover(root)
        self.assertCode(result, m.BLOCKED_TXN_INCOMPLETE)
        self.assertEqual(tree_state(root), before)

    def test_a_committed_file_edited_after_the_crash_is_not_overwritten(self):
        """The repair refuses to delete somebody's edit; it does not "repair" it."""
        desc_v1, blobs1, v2, blobs2, spec, root = self.crash_after(3)
        # A real crash, not an exception: `bin/sidecar.py` holds the new bytes
        # and the journal is on disk to say so.
        self.assertTrue((root / m.JOURNAL_NAME).exists())
        self.assertEqual((root / "bin" / "sidecar.py").read_bytes(),
                         V2_AND_ADDED["bin/sidecar.py"])

        edited = b"#!/usr/bin/env python3\nprint('THE OPERATOR WAS HERE')\n"
        (root / "bin" / "sidecar.py").write_bytes(edited)
        before = tree_state(root)

        repaired = self.recover(root)
        self.assertCode(repaired, m.BLOCKED_TXN_FOREIGN_STATE)
        # A refusal that deleted anything would be the same bug wearing a code.
        self.assertEqual(tree_state(root), before)
        self.assertEqual((root / "bin" / "sidecar.py").read_bytes(), edited)

    def test_a_repair_that_can_neither_finish_nor_undo_is_refused(self):
        """Half the evidence on each side: `BLOCKED_TXN_INCOMPLETE`, no deletion.

        Forward needs every file either committed or staged; undo needs the old
        bytes of every committed file. Remove one copy from each side and neither
        direction is provable — the answer is a refusal, and putting the removed
        copies back makes the same root repairable again.
        """
        desc_v1, blobs1, v2, blobs2, spec, root = self.crash_after(3)
        forward_gap = root / m.STAGING_DIR / "new" / "etc" / "sidecar.json"
        undo_gap = root / m.STAGING_DIR / "undo" / "bin" / "sidecar.py"
        self.assertTrue(forward_gap.is_file(), "the staged copy should be there")
        self.assertTrue(undo_gap.is_file(), "the old bytes should be recorded")
        forward_gap.unlink()
        undo_gap.unlink()
        before = tree_state(root)

        result = self.recover(root)
        self.assertCode(result, m.BLOCKED_TXN_INCOMPLETE)
        self.assertEqual(tree_state(root), before)
        self.assertTrue((root / m.JOURNAL_NAME).exists())
        # Still refused, not "repaired" on the second look: the evidence did not
        # change, so neither may the answer.
        self.assertCode(self.recover(root), m.BLOCKED_TXN_INCOMPLETE)
        self.assertEqual(tree_state(root), before)

    def test_a_stranger_file_in_the_root_survives_the_repair(self):
        """The journal bounds deletion: a path it does not name is not in reach."""
        desc_v1, blobs1, v2, blobs2, spec, root = self.crash_after(2)
        stranger = root / "operator-notes.txt"
        stranger.write_bytes(b"do not delete this\n")
        self.assertEqual(self.recover(root).decision, m.OK)
        self.assertEqual(stranger.read_bytes(), b"do not delete this\n")


# --------------------------------------------------------------------------- #
# the same thing across real processes, with a real `os._exit`
# --------------------------------------------------------------------------- #
class RecoveryAcrossProcesses(Wave19Test):
    """Install, crash, repair and revoke, each operation in its own interpreter.

    Nothing here is simulated in-process: the writer is a separate interpreter
    that dies at a counted rename, the repair is a second one that has only the
    disk to go on, and the revoke is a third. That is the only way to show the
    recovery path is not secretly holding state in memory.
    """

    def test_the_whole_sequence_in_four_processes(self):
        """Kill *before*, *inside* and *after* the commit — repair then revoke."""
        for die_at in (1, 2, len(UPGRADE_RENAMES)):
            with self.subTest(rename=UPGRADE_RENAMES[die_at - 1]):
                desc_v1, blobs1, v2, blobs2, upgrade_spec, root = \
                    self.crash_after(die_at)
                # At die_at 1 there is no journal: the journal write is the call
                # that was killed. That is exactly why that point is in the list.
                if die_at > 1:
                    self.assertIn(m.JOURNAL_NAME, tree_state(root))

                repaired = self.child("recover", upgrade_spec)
                self.assertEqual(repaired["decision"], m.OK, repaired["reasons"])
                self.assertNotEqual(repaired["pid"], os.getpid())

                receipt = m.read_receipt(root)
                self.assertIn(receipt.version, ("0.1.0", "0.2.0"))
                self.assertEqual(orphans(root), [], tree_state(root))
                for record in receipt.files:
                    self.assertEqual(sha((root / record.path).read_bytes()),
                                     record.sha256)

                if receipt.version == "0.1.0":
                    spec = self.spec(desc_v1, blobs1, root)
                else:
                    spec = self.spec(v2, blobs2, root)
                revoked = self.child("revoke", spec)
                self.assertEqual(revoked["decision"], m.OK, revoked["reasons"])
                self.assertEqual(files_under(root), [])

    def test_the_crash_and_repair_pair_repeats_cleanly(self):
        """Crash, repair, again — because the failure it guards was intermittent.

        Before the §5.1 fix this pair refused roughly once in fifteen runs, each
        time on a different world, with "a writer is running" about a process
        that was gone. A single pass proves nothing about a rate, so this one
        asks for five.
        """
        for attempt in range(5):
            with self.subTest(attempt=attempt):
                _d, _b, _v2, _b2, spec, root = self.crash_after(2)
                repaired = self.recover(root)
                self.assertEqual(repaired.decision, m.OK, repaired.reasons)
                self.assertEqual(orphans(root), [])

    def test_a_lock_whose_writer_died_is_not_a_live_writer(self):
        """`_pid_alive` on a corpse, across a real process boundary."""
        _d, _b, _v2, _b2, spec, root = self.crash_after(2)
        lock = json.loads((root / m.LOCK_NAME).read_bytes())
        self.assertFalse(m._pid_alive(int(lock["pid"])))
        self.assertEqual(self.recover(root).decision, m.OK)

    @unittest.skipUnless(os.name == "nt", "a handle-kept process object is Windows")
    def test_a_corpse_whose_object_is_still_open_is_not_a_live_writer(self):
        """A successful `OpenProcess` is not a live process, and this proves it.

        Windows keeps a process object alive while any handle to it is open, so
        an exited writer whose handle is still held — a security product, a
        debugger, a launcher — stays addressable under its old pid with its old
        start time. The first draft of `_pid_alive` asked only whether
        `OpenProcess` succeeded and answered "a writer is running"; the repair
        then refused forever. This test holds the handle itself, so the shape is
        produced on demand rather than waited for.
        """
        import subprocess as sp

        ctypes, _wintypes, kernel32, _file_time = m._kernel32()
        proc = sp.Popen([sys.executable, "-I", "-c", "pass"])
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        handle = kernel32.OpenProcess(0x1000, False, proc.pid)
        self.assertTrue(handle, "could not hold a handle to the child")
        try:
            proc.wait(timeout=120)
            # The object is still addressable, and it says so — that is the naive
            # answer this test exists to reject.
            again = kernel32.OpenProcess(0x1000, False, proc.pid)
            self.assertTrue(again, "the object was collected; the test proves nothing")
            kernel32.CloseHandle(again)
            self.assertFalse(m._pid_alive(proc.pid),
                             "an exited process is being reported as running")
        finally:
            kernel32.CloseHandle(handle)
        self.assertFalse(m._pid_alive(proc.pid))

    def test_a_pid_that_now_belongs_to_another_process_is_not_the_writer(self):
        """A pid is not an identity, and the repair can inherit the dead one.

        Windows hands a freed pid to the next process it creates. When that
        process is the repair itself, a lock judged by pid alone answers "another
        writer holds this root" about the pid it is running under, and the
        interrupted upgrade traps the root — the #514 shape in a third place, and
        the only one an operator can undo only by deleting a file by hand.

        Both halves are made deterministic here by naming *this* process in the
        lock: whether the operating system really did reuse the pid does not have
        to happen for the check to be exercised.
        """
        _d, _b, _v2, _b2, spec, root = self.crash_after(2)
        path = root / m.LOCK_NAME
        body = json.loads(path.read_bytes())
        self.assertTrue(body.get("process_start"),
                        f"the writer did not record when it started: {body}")
        body["pid"] = os.getpid()          # running: this very process
        body["process_start"] = "0"        # ... which started at another time
        path.write_bytes(json.dumps(body).encode("utf-8"))

        self.assertEqual(self.recover(root).decision, m.OK, tree_state(root))

    def test_the_same_pid_with_the_same_start_time_is_still_a_writer(self):
        """The negative control for the test above: only the *start* is believed."""
        _d, _b, _v2, _b2, spec, root = self.crash_after(2)
        path = root / m.LOCK_NAME
        body = json.loads(path.read_bytes())
        started = m._process_start(os.getpid())
        self.assertTrue(started)
        self.assertEqual(started, m._process_start(os.getpid()))
        body["pid"] = os.getpid()
        body["process_start"] = started
        path.write_bytes(json.dumps(body).encode("utf-8"))
        before = tree_state(root)

        self.assertCode(self.recover(root), m.BLOCKED_CONCURRENT_WRITER)
        self.assertEqual(tree_state(root), before)
        path.unlink()
        self.assertEqual(self.recover(root).decision, m.OK)

    def test_a_live_writer_is_refused_even_though_it_holds_only_the_lock(self):
        _d, _b, _v2, _b2, spec, root = self.crash_after(2)
        # A writer that is alive right now: this very process, same token.
        token = json.loads((root / m.LOCK_NAME).read_bytes())["token"]
        m._take_lock(root, "install", BOOT, token)
        try:
            result = self.recover(root)
            self.assertCode(result, m.BLOCKED_CONCURRENT_WRITER)
        finally:
            (root / m.LOCK_NAME).unlink()
        self.assertEqual(self.recover(root).decision, m.OK)


# --------------------------------------------------------------------------- #
# what recovery refuses, and that a refusal deletes nothing
# --------------------------------------------------------------------------- #
class RecoveryRefusals(Wave19Test):
    def pending(self):
        """A root with a real interrupted transaction: crash, then hand it back."""
        return self.crash_after(2)

    def test_a_clean_root_has_nothing_to_recover(self):
        _desc, _blobs, root, _r = self.install()
        before = tree_state(root)
        first = self.recover(root)
        self.assertCode(first, m.BLOCKED_NO_JOURNAL)
        second = self.recover(root)
        self.assertCode(second, m.BLOCKED_NO_JOURNAL)
        self.assertEqual(tree_state(root), before)

    def test_a_missing_root_is_not_a_recovery(self):
        result = self.recover(self.fresh_root())
        self.assertCode(result, m.BLOCKED_NO_JOURNAL)

    def test_the_repair_needs_a_grant_for_the_repair(self):
        _d, _b, _v2, _b2, spec, root = self.pending()
        before = tree_state(root)
        self.assertCode(self.recover(root, action="install"), m.BLOCKED_NO_GRANT)
        self.assertCode(self.recover(root, device="device:somebody-else"),
                        m.BLOCKED_IDENTITY_MISMATCH)
        self.assertCode(self.recover(root, boot="boot-from-another-turn"),
                        m.BLOCKED_BOOT_MISMATCH)
        self.assertEqual(tree_state(root), before)

    def test_a_grant_named_for_another_device_is_refused(self):
        _d, _b, _v2, _b2, spec, root = self.pending()
        before = tree_state(root)
        result = m.recover(root, grant=grant("recover", device="device:elsewhere"),
                           device_ref=DEVICE, boot_id=BOOT)
        self.assertCode(result, m.BLOCKED_IDENTITY_MISMATCH)
        self.assertEqual(tree_state(root), before)

    def test_a_journal_that_does_not_verify_is_not_obeyed(self):
        _d, _b, _v2, _b2, spec, root = self.pending()
        path = root / m.JOURNAL_NAME
        body = json.loads(path.read_bytes())
        body["deletes"] = [m.RECEIPT_NAME]
        path.write_bytes(json.dumps(body).encode("utf-8"))
        before = tree_state(root)
        self.assertCode(self.recover(root), m.BLOCKED_JOURNAL_UNREADABLE)
        self.assertEqual(tree_state(root), before)

    def test_a_journal_naming_another_root_is_refused(self):
        """A complete forgery — every digest recomputed — and still refused."""
        _d, _b, _v2, _b2, spec, root = self.pending()
        path = root / m.JOURNAL_NAME
        body = json.loads(path.read_bytes())
        body["receipt_body"]["install_root"] = str(self.tmp())
        body["receipt_body"]["receipt_digest"] = m.receipt_digest(
            {k: v for k, v in body["receipt_body"].items() if k != "receipt_digest"})
        body["marker_body"]["receipt_digest"] = body["receipt_body"]["receipt_digest"]
        body["journal_digest"] = m.receipt_digest(
            {k: v for k, v in body.items() if k != "journal_digest"})
        path.write_bytes(json.dumps(body).encode("utf-8"))
        before = tree_state(root)
        self.assertCode(self.recover(root), m.BLOCKED_TXN_FOREIGN_STATE)
        self.assertEqual(tree_state(root), before)

    def test_a_journal_that_names_a_path_outside_the_root_is_refused(self):
        """The journal is a signed-by-nobody list; it is checked, not believed."""
        outside = self.tmp() / "outside.txt"
        outside.write_bytes(b"a file no receipt owns\n")
        _d, _b, _v2, _b2, spec, root = self.pending()
        path = root / m.JOURNAL_NAME
        body = json.loads(path.read_bytes())
        body["new"].append({"path": "../outside.txt", "sha256": sha(b"x"), "size": 1})
        body["journal_digest"] = m.receipt_digest(
            {k: v for k, v in body.items() if k != "journal_digest"})
        path.write_bytes(json.dumps(body).encode("utf-8"))
        self.assertCode(self.recover(root), m.BLOCKED_JOURNAL_PATH)
        self.assertEqual(outside.read_bytes(), b"a file no receipt owns\n")

    def test_a_journal_whose_receipt_body_was_edited_is_refused(self):
        _d, _b, _v2, _b2, spec, root = self.pending()
        path = root / m.JOURNAL_NAME
        body = json.loads(path.read_bytes())
        body["receipt_body"]["files"][0]["sha256"] = sha(b"anything")
        body["journal_digest"] = m.receipt_digest(
            {k: v for k, v in body.items() if k != "journal_digest"})
        path.write_bytes(json.dumps(body).encode("utf-8"))
        self.assertCode(self.recover(root), m.BLOCKED_JOURNAL_UNREADABLE)

    def test_the_repair_is_refused_on_a_root_that_is_declared_shared(self):
        """A pending journal does not make a Legacy or shared root repairable.

        The transaction is real and repairable — the second call proves it — and
        that is the point: the refusal above is about *where* the root is, not
        about what is in it.
        """
        desc_v1, blobs1, v2, blobs2, spec, root = self.pending()
        before = tree_state(root)
        self.assertCode(self.recover(root, legacy_roots=(root,)),
                        m.BLOCKED_LEGACY_ROOT)
        inside = root / "legacy"
        inside.mkdir()
        self.assertCode(self.recover(root, legacy_roots=(inside,)),
                        m.BLOCKED_LEGACY_ROOT)
        inside.rmdir()
        self.assertEqual(tree_state(root), before)
        self.assertEqual(self.recover(root).decision, m.OK)

    def test_a_receipt_that_is_neither_end_is_refused(self):
        """The receipt on disk must be the one it started from, or the new one.

        A third receipt that verifies against its own digest, names this root and
        still describes neither end is somebody else's state, and a repair that
        wrote over it would be deleting evidence it cannot account for.
        """
        desc_v1, blobs1, v2, blobs2, spec, root = self.pending()
        journal = json.loads((root / m.JOURNAL_NAME).read_bytes())
        body = dict(journal["receipt_body"])
        body["version"] = "0.9.9"
        body["receipt_digest"] = m.receipt_digest(
            {k: v for k, v in body.items() if k != "receipt_digest"})
        (root / m.RECEIPT_NAME).write_bytes(json.dumps(body).encode("utf-8"))
        before = tree_state(root)

        self.assertCode(self.recover(root), m.BLOCKED_TXN_FOREIGN_STATE)
        self.assertEqual(tree_state(root), before)

    def test_a_dead_temp_beside_a_real_journal_is_cleaned_by_the_repair(self):
        """`_write_atomic`'s temp is this module's litter, and `_finish` takes it."""
        desc_v1, blobs1, v2, blobs2, spec, root = self.crash_after(3)
        dead = root / f"{m.RECEIPT_NAME}.tmp-999999"
        dead.write_bytes(b'{"half": "a receipt"}')

        repaired = self.recover(root)
        self.assertEqual(repaired.decision, m.OK, repaired.reasons)
        self.assertFalse(dead.exists(), "the repair left its own temp behind")
        self.assertEqual(orphans(root), [])

    def test_a_dead_temp_alone_is_cleaned_on_a_root_this_module_owns(self):
        """The same litter with no journal: the receipt is what proves the root."""
        _desc, _blobs, root, _r = self.install()
        temps = [root / f"{m.RECEIPT_NAME}.tmp-999999",
                 root / f"{m.OWNER_MARKER_NAME}.tmp-999998"]
        for path in temps:
            path.write_bytes(b'{"half": "written"}')

        result = self.recover(root)
        self.assertEqual(result.decision, m.OK, result.reasons)
        for path in temps:
            self.assertFalse(path.exists(), path.name)
            self.assertIn(path.name, result.removed)
        self.assertEqual(orphans(root), [])

    def test_a_pending_transaction_blocks_revoke_install_and_restore(self):
        desc_v1, blobs1, v2, blobs2, spec, root = self.pending()
        before = tree_state(root)
        self.assertCode(self.revoke(desc_v1, root), m.BLOCKED_TXN_PENDING)
        self.assertCode(
            m.install(v2, root, blobs2, grant=grant("install"), device_ref=DEVICE,
                      boot_id=BOOT, replace=m.read_receipt(root)),
            m.BLOCKED_TXN_PENDING)
        self.assertEqual(tree_state(root), before)

    def test_recovery_is_idempotent_and_the_second_call_deletes_nothing(self):
        desc_v1, blobs1, v2, blobs2, spec, root = self.pending()
        self.assertEqual(self.recover(root).decision, m.OK)
        state = tree_state(root)
        self.assertCode(self.recover(root), m.BLOCKED_NO_JOURNAL)
        self.assertEqual(tree_state(root), state)

    def test_a_staging_tree_without_a_journal_is_cleaned_only_with_evidence(self):
        desc_v1, blobs1, root, _r = self.install()
        staging = root / m.STAGING_DIR / "new" / "etc"
        staging.mkdir(parents=True)
        (staging / "orphan.json").write_bytes(b"{}\n")
        release_state = {rel: digest for rel, digest in tree_state(root).items()
                         if not rel.startswith(m.STAGING_DIR)}
        result = self.recover(root)
        self.assertEqual(result.decision, m.OK, result.reasons)
        self.assertIn(f"{m.STAGING_DIR}/new/etc/orphan.json", result.removed)
        self.assertFalse((root / m.STAGING_DIR).exists())
        self.assertEqual({rel: digest for rel, digest in tree_state(root).items()
                          if not rel.startswith(m.STAGING_DIR)}, release_state)

    def test_a_staging_tree_on_a_root_nothing_owns_is_left_alone(self):
        root = self.fresh_root()
        staging = root / m.STAGING_DIR / "new"
        staging.mkdir(parents=True)
        (staging / "somebody-elses.bin").write_bytes(b"not ours\n")
        before = tree_state(root)
        self.assertCode(self.recover(root), m.BLOCKED_NO_JOURNAL)
        self.assertEqual(tree_state(root), before)

    def test_a_stranger_lock_is_not_a_dead_writer(self):
        root = self.fresh_root()
        root.mkdir(parents=True)
        (root / m.LOCK_NAME).write_bytes(b"just some bytes\n")
        before = tree_state(root)
        self.assertCode(self.recover(root), m.BLOCKED_NO_JOURNAL)
        self.assertEqual(tree_state(root), before)

    def test_a_plain_file_named_like_a_lock_is_refused_not_deleted(self):
        """Our lock has a shape: a body `_take_lock` did not write is not ours."""
        root = self.fresh_root()
        root.mkdir(parents=True)
        (root / m.LOCK_NAME).write_bytes(json.dumps({"pid": 999999}).encode("utf-8"))
        before = tree_state(root)
        self.assertCode(self.recover(root), m.BLOCKED_NO_JOURNAL)
        self.assertEqual(tree_state(root), before)


# --------------------------------------------------------------------------- #
# F1: a restore keeps what it does not own
# --------------------------------------------------------------------------- #
class RestoreKeepsTheBackendIdentity(Wave19Test):
    def test_next_backend_id_survives_a_restore(self):
        """#513 hangs the backend on the marker; a restore must not drop it."""
        v1, blobs1, v2, blobs2, root = self.upgraded()
        marker_path = root / m.OWNER_MARKER_NAME
        marker = json.loads(marker_path.read_bytes())
        marker["next_backend_id"] = "backend:next-01"
        marker["installed_by"] = "wave18-installer"
        marker_path.write_bytes(json.dumps(marker).encode("utf-8"))

        result = m.restore(v1, root, grant=grant("restore"), device_ref=DEVICE,
                           boot_id=BOOT)
        self.assertEqual(result.decision, m.OK, result.reasons)

        after = json.loads(marker_path.read_bytes())
        self.assertEqual(after.get("next_backend_id"), "backend:next-01")
        self.assertEqual(after.get("installed_by"), "wave18-installer")
        # ... and the keys the restore owns are the new ones, not the old ones.
        self.assertEqual(after["version"], "0.1.0")
        self.assertEqual(after["receipt_digest"], m.read_receipt(root).digest)

    def test_the_backup_is_consumed_but_the_release_is_not(self):
        """The bug this replaces: `consume` named the restored files themselves."""
        v1, blobs1, v2, blobs2, root = self.upgraded()
        result = m.restore(v1, root, grant=grant("restore"), device_ref=DEVICE,
                           boot_id=BOOT)
        self.assertEqual(result.decision, m.OK, result.reasons)
        for rel, body in blobs1.items():
            self.assertEqual((root / rel).read_bytes(), body, rel)
        self.assertFalse((root / m.BACKUP_DIR).exists())

    def test_a_restored_root_can_still_be_revoked(self):
        v1, blobs1, v2, blobs2, root = self.upgraded()
        self.assertEqual(m.restore(v1, root, grant=grant("restore"),
                                   device_ref=DEVICE, boot_id=BOOT).decision, m.OK)
        revoked = self.revoke(v1, root)
        self.assertEqual(revoked.decision, m.OK, revoked.reasons)
        self.assertEqual(files_under(root), [])


# --------------------------------------------------------------------------- #
# what the receipt actually is
# --------------------------------------------------------------------------- #
class TheReceiptIsChecksumOnly(Wave19Test):
    def test_a_self_consistent_forgery_is_accepted(self):
        """Proof of the documented limit: nothing here is signed.

        Whoever can write the root can write a receipt that names files they
        choose, with a digest that verifies and a marker that agrees with it. The
        module refuses *inconsistent* evidence, not *unauthenticated* evidence.
        Real defence needs a trust root outside the root (a key, a transparency
        log, a pin delivered by another channel) — `RECEIPT_AUTHENTICATION` says
        so in the module, and this test keeps the sentence honest.
        """
        _desc, _blobs, root, _r = self.install()
        stranger = root / "etc" / "stranger.py"
        stranger.write_bytes(b"WORTH = 'stealing'\n")

        receipt_path = root / m.RECEIPT_NAME
        body = json.loads(receipt_path.read_bytes())
        body["files"].append({"path": "etc/stranger.py",
                              "sha256": sha(stranger.read_bytes()),
                              "size": stranger.stat().st_size})
        body["receipt_digest"] = m.receipt_digest(
            {k: v for k, v in body.items() if k != "receipt_digest"})
        receipt_path.write_bytes(json.dumps(body).encode("utf-8"))
        marker_path = root / m.OWNER_MARKER_NAME
        marker = json.loads(marker_path.read_bytes())
        marker["receipt_digest"] = body["receipt_digest"]
        marker_path.write_bytes(json.dumps(marker).encode("utf-8"))

        forged = m.read_receipt(root)
        self.assertTrue(forged.is_intact())
        self.assertIn("etc/stranger.py", [record.path for record in forged.files])

    def test_the_module_says_so_in_its_own_vocabulary(self):
        self.assertIn("checksum", m.RECEIPT_AUTHENTICATION.lower())
        self.assertIn("sign", m.RECEIPT_AUTHENTICATION.lower())


if __name__ == "__main__":
    unittest.main()
