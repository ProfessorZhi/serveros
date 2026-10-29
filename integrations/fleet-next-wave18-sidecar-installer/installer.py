#!/usr/bin/env python3
"""An install *writer* for the two-file sidecar, whose receipt #506 can revoke.

Wave 17 answered "can this sidecar be revoked?" with a revoker and a refusal
surface, and left one hole it refused to paper over: nothing in the tree had
ever written the receipt such a revoke verifies against. `upstream_verdict()`
said `BLOCKED_UPSTREAM`, correctly — the evidence did not exist.

This module is the side that writes it. It is offline, it is a **dry run unless
asked to apply**, and it installs the real two files, not a stand-in:

    fleet_mcp/deploy/next_tailscale_gate.py
    fleet_mcp/deploy/next_tailscale_plan.example.json

The receipt it writes is Wave 17's, byte for byte: the body comes from
`sidecar_release._receipt_body()` and the digest from
`sidecar_release.receipt_digest()`, so there is one definition of "a receipt"
and not two. What this module adds is about **how the bytes reach the disk** and
**who is allowed to ask**:

  * the whole tree is written into a sibling staging directory and appears at
    the root in one `os.rename`, so there is no instant at which the root holds
    half an install;
  * an upgrade replaces files one at a time — each replacement atomic — with the
    old bytes already copied into `.sidecar-backup/<version>/` and the old
    receipt held in memory, so a failure puts the previous version back byte for
    byte instead of leaving a mix;
  * a cross-process lock (`O_CREAT|O_EXCL`) keeps two writers out of one root,
    and a lock left by a dead process is named rather than ignored;
  * the bytes come from **git**, at a pinned commit, re-digested here, so a
    worktree edited after the pin cannot be installed by naming the worktree.

What the receipt authenticates, and what it does not
----------------------------------------------------
The receipt carries a sha256 over its own canonical body. That is a **checksum**,
not a signature: anyone who can write the root can write a receipt and recompute
the checksum, and this module will verify it. Without a trusted root of trust —
a key outside the root, a transparency log, a signed pin delivered by another
channel — an attacker with write access is not detected. What the checksum does
buy is that an ordinary edit, a half-written file, or a receipt describing a
different version cannot pass as this install. The module prints
`checksum-only` and never uses the word "signed" about it.

Refusals are named, and the guards are declared as **names** resolved per call,
so that `probe_guards.py` can withdraw exactly one and see what turns red.

Offline: no socket, no port, no service manager, no `systemctl`, no
`shutil.rmtree`, no `os.system`. `git` is read-only (`rev-parse`, `cat-file`)
and `git()` refuses any other verb. Installs happen in the tests under a
`tempfile` directory; nothing here knows how to install into `/opt`.

One thing this module cannot fix alone (finding F1)
---------------------------------------------------
`sidecar_release.restore()` rewrites the ownership marker with its own seven
keys, so the `next_backend_id` this module keeps there is dropped by a restore.
`rollback_to_previous()` puts it back from the surviving marker — but that is a
patch on this side of the interface, and the honest fix belongs to the owner of
`restore()`. It is reported in `FINDINGS`, not hidden.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "serveros/fleet-next-wave18-sidecar-installer.v1"

#: The two files #502 measured, spelled once. `_guard_descriptor` is what stops
#: this installer from being pointed at some other pair and calling the result
#: "the sidecar".
SIDECAR_FILES: tuple[str, ...] = (
    "fleet_mcp/deploy/next_tailscale_gate.py",
    "fleet_mcp/deploy/next_tailscale_plan.example.json",
)
SIDECAR_NAME = "sidecar"
SIDECAR_ROOT_NAME = "sidecar"
DEFAULT_VERSION = "0.1.0+wave18"

#: A root is `<allowed prefix>/sidecar`. Both halves are required: the prefix is
#: the operator's declaration of where installs may happen at all, and the leaf
#: name keeps two installs from ever sharing one root.
STAGING_SUFFIX = ".wave18-staging-"
LOCK_SUFFIX = ".wave18-lock"

#: What this module can honestly say about the receipt it writes.
RECEIPT_AUTHENTICATION = (
    "checksum-only: sha256 over the canonical receipt body; NOT a signature and "
    "NOT authenticated against a key outside the root, so a writer with access to "
    "the root can forge one undetected")

VERIFICATION = {
    "source": "SOURCE_VERIFIED",
    "device": "REAL_DEVICE_NOT_RUN",
    "receipt_authentication": RECEIPT_AUTHENTICATION,
}

#: Defects this round found in the interface it consumes. Reported here and in
#: the PR body; neither is fixed by editing #506's files.
FINDINGS: tuple[str, ...] = (
    "F1 sidecar_release.restore() rewrites the ownership marker with its own seven "
    "keys, dropping any other key the marker carried; this module's next_backend_id "
    "is one, so rollback_to_previous() re-adds it from the surviving marker, and a "
    "direct call to restore() would leave a root whose backend identity is "
    "unrecorded (named refusal BLOCKED_BACKEND_MISSING on the next revoke). Minimal "
    "patch for the owner: build the marker in restore() from the existing marker and "
    "overwrite the seven known keys, instead of constructing a fresh dict.",
    "F2 sidecar_release.install() writes files one at a time and the receipt last, so "
    "its own crash window leaves a root that a revoke refuses as "
    "BLOCKED_MODIFIED_AFTER_INSTALL. Not a defect in install(), which never claimed "
    "atomicity -- it is the reason this writer does not reuse install().",
)

# ---- decisions ------------------------------------------------------------- #
OK = "OK"
BLOCKED = "BLOCKED"
DRY_RUN = "DRY_RUN"
NOTHING_TO_RECOVER = "NOTHING_TO_RECOVER"

# ---- refusal codes --------------------------------------------------------- #
# Reused verbatim from #506 where the condition is the same condition, so an
# operator reading both reports sees one vocabulary.
BLOCKED_NO_GRANT = "BLOCKED_NO_GRANT"
BLOCKED_IDENTITY_MISMATCH = "BLOCKED_IDENTITY_MISMATCH"
BLOCKED_BOOT_MISMATCH = "BLOCKED_BOOT_MISMATCH"
BLOCKED_NOT_PINNED = "BLOCKED_NOT_PINNED"
BLOCKED_DESCRIPTOR_PATH = "BLOCKED_DESCRIPTOR_PATH"
BLOCKED_ROOT_NOT_ABSOLUTE = "BLOCKED_ROOT_NOT_ABSOLUTE"
BLOCKED_ROOT_NOT_ISOLATED = "BLOCKED_ROOT_NOT_ISOLATED"
BLOCKED_ROOT_IS_SYMLINK = "BLOCKED_ROOT_IS_SYMLINK"
BLOCKED_ROOT_NOT_EMPTY = "BLOCKED_ROOT_NOT_EMPTY"
BLOCKED_LEGACY_ROOT = "BLOCKED_LEGACY_ROOT"
BLOCKED_ALREADY_INSTALLED = "BLOCKED_ALREADY_INSTALLED"
BLOCKED_UPGRADE_CHAIN = "BLOCKED_UPGRADE_CHAIN"
BLOCKED_SOURCE_DIGEST = "BLOCKED_SOURCE_DIGEST"
BLOCKED_RECEIPT_TAMPERED = "BLOCKED_RECEIPT_TAMPERED"
BLOCKED_RECEIPT_MISMATCH = "BLOCKED_RECEIPT_MISMATCH"
BLOCKED_NO_RECEIPT = "BLOCKED_NO_RECEIPT"
BLOCKED_FOREIGN_PRESENT = "BLOCKED_FOREIGN_PRESENT"
BLOCKED_CONCURRENT_WRITER = "BLOCKED_CONCURRENT_WRITER"
BLOCKED_FILE_ABSENT = "BLOCKED_FILE_ABSENT"
BLOCKED_MODIFIED_AFTER_INSTALL = "BLOCKED_MODIFIED_AFTER_INSTALL"
BLOCKED_SYMLINK_TRAVERSAL = "BLOCKED_SYMLINK_TRAVERSAL"
BLOCKED_BACKUP_MODIFIED = "BLOCKED_BACKUP_MODIFIED"
BLOCKED_OWNER_MARKER_MISSING = "BLOCKED_OWNER_MARKER_MISSING"
BLOCKED_OWNER_MARKER_MISMATCH = "BLOCKED_OWNER_MARKER_MISMATCH"

#: New in this round, because the install side has questions the revoke side
#: never had to ask.
BLOCKED_INTERFACE = "BLOCKED_INTERFACE"
BLOCKED_NOT_THE_SIDECAR = "BLOCKED_NOT_THE_SIDECAR"
BLOCKED_NO_PROVENANCE = "BLOCKED_NO_PROVENANCE"
BLOCKED_PROVENANCE_MISMATCH = "BLOCKED_PROVENANCE_MISMATCH"
BLOCKED_IDENTITY_INCOMPLETE = "BLOCKED_IDENTITY_INCOMPLETE"
BLOCKED_IDENTITY_CONFLATED = "BLOCKED_IDENTITY_CONFLATED"
BLOCKED_ROOT_NOT_NAMESPACED = "BLOCKED_ROOT_NOT_NAMESPACED"
BLOCKED_PARENT_ESCAPE = "BLOCKED_PARENT_ESCAPE"
BLOCKED_STALE_WRITER = "BLOCKED_STALE_WRITER"
BLOCKED_STALE_STAGING = "BLOCKED_STALE_STAGING"
BLOCKED_VERIFY_FAILED = "BLOCKED_VERIFY_FAILED"
BLOCKED_ABORTED = "BLOCKED_ABORTED"
BLOCKED_BACKEND_MISSING = "BLOCKED_BACKEND_MISSING"
BLOCKED_BACKEND_MISMATCH = "BLOCKED_BACKEND_MISMATCH"

#: Spelled the same in both modules on purpose: an operator who sees this word
#: from either one is being told the same thing — a `sidecar_release` write to
#: this root began and did not finish, and its own `recover()` is the verb.
BLOCKED_TXN_PENDING = "BLOCKED_TXN_PENDING"

#: `git` verbs this module may run, and it may only run these.
READ_ONLY_GIT = frozenset({"rev-parse", "cat-file"})

#: Names this module requires from `sidecar_release` (#506). The receipt format
#: lives there and stays there; if one of these moves, the answer is
#: `BLOCKED_INTERFACE` plus a named patch, never a locally invented copy.
REQUIRED_INTERFACE: tuple[str, ...] = (
    "Descriptor", "Grant", "FileRecord", "Receipt", "Verdict",
    "check_descriptor", "receipt_digest", "read_receipt", "revoke", "restore",
    "observe_legacy", "legacy_drift",
    "_receipt_body", "_root_real", "_root_refusal", "_inside", "_is_link",
    "_read_marker", "_file_problems", "_allowed_entries",
    "RECEIPT_SCHEMA", "MARKER_SCHEMA", "RECEIPT_NAME", "OWNER_MARKER_NAME",
    "LOCK_NAME", "BACKUP_DIR",
)

#: The one name that may be spelled either way: `_receipt_body` today, and a
#: public `receipt_body` if the owner ever aliases it.
RECEIPT_BODY_ALIASES: tuple[str, ...] = ("_receipt_body", "receipt_body")

_UPSTREAM: Any = None


class SimulatedCrash(BaseException):
    """A deliberate death, raised where a `kill -9` would have landed.

    `BaseException`, not `Exception`, so no `except Exception` in this module or
    in a caller can absorb it and turn a crash test into a pass. It exists so
    that "a crash before the receipt leaves nothing to revoke" is an assertion
    about bytes on disk rather than a paragraph about what would have happened.

    It is **not** caught by this module's recovery handler: a process that dies
    does not run its cleanup, so a crash test that let the handler tidy up would
    be measuring the handler, not the death.
    """


class InjectedFailure(Exception):
    """A write that fails, as opposed to a process that stops.

    The two questions are different and get different evidence. `crash_at` asks
    "what does a kill leave behind?" and is answered by `recover()`. `fail_at`
    asks "what does the tool do when a write raises?" and is answered by the
    rollback that runs before the exception leaves. Neither is a real fault
    injector: both are switches on the code path they name.
    """


# --------------------------------------------------------------------------- #
# the upstream module (#506) and the interface required of it
# --------------------------------------------------------------------------- #
def upstream_path() -> Path:
    return (Path(__file__).resolve().parents[1]
            / "fleet-next-wave17-sidecar-release" / "sidecar_release.py")


def load_upstream(path: Path | str | None = None) -> Any:
    """Load `sidecar_release` (#506) from its path, once per process.

    The name is process-wide and other loaders in this tree use the same one, so
    it is read before it is written: two copies of that module would carry two
    `Verdict` classes, and a caller comparing them would see two modules
    agreeing about nothing.
    """
    name = "sidecar_release"
    existing = sys.modules.get(name)
    if existing is not None and getattr(existing, "__file__", None):
        return existing
    target = Path(path) if path else upstream_path()
    spec = importlib.util.spec_from_file_location(name, str(target))
    if spec is None or spec.loader is None:
        raise ImportError(f"no loader for {target}")
    module = sys.modules.get(name)
    if module is None:
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


def _upstream(module: Any | None = None) -> Any:
    global _UPSTREAM
    if module is not None:
        return module
    if _UPSTREAM is None:
        _UPSTREAM = load_upstream()
    return _UPSTREAM


def receipt_body_of(upstream: Any) -> Any:
    """The callable that builds a receipt body, or None if neither name exists."""
    for name in RECEIPT_BODY_ALIASES:
        candidate = getattr(upstream, name, None)
        if callable(candidate):
            return candidate
    return None


def interface(module: Any | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(present, missing) for the names this module needs from #506.

    Answered by attribute lookup on the loaded module, not by reading source:
    what matters is what the module in hand can do.
    """
    upstream = _upstream(module)
    missing: list[str] = []
    present: list[str] = []
    for name in REQUIRED_INTERFACE:
        if name in RECEIPT_BODY_ALIASES:
            if receipt_body_of(upstream) is None:
                missing.append("|".join(RECEIPT_BODY_ALIASES))
            else:
                present.append(name)
        elif hasattr(upstream, name):
            present.append(name)
        else:
            missing.append(name)
    return tuple(present), tuple(missing)


def interface_report(module: Any | None = None) -> dict[str, Any]:
    present, missing = interface(module)
    return {
        "schema": SCHEMA,
        "upstream": str(upstream_path()),
        "required": list(REQUIRED_INTERFACE),
        "present": list(present),
        "missing": list(missing),
        "compatible": not missing,
        "patch": _interface_patch(missing),
        "findings": list(FINDINGS),
    }


def _interface_patch(missing: Sequence[str]) -> str:
    """The minimal patch to #506 if the interface ever moves.

    Named here rather than in a commit message: a reader who gets
    `BLOCKED_INTERFACE` should not have to guess what to ask for.
    """
    if not missing:
        return ""
    aliases = [name for name in missing if name in RECEIPT_BODY_ALIASES]
    others = [name for name in missing if name not in RECEIPT_BODY_ALIASES]
    lines = ["# in integrations/fleet-next-wave17-sidecar-release/sidecar_release.py"]
    for _name in aliases:
        lines.append("receipt_body = _receipt_body  # public alias of the one receipt body")
    if others:
        lines.append(f"# restore or re-export: {', '.join(others)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# identity: the host, the boot, and the *other* identity
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Identity:
    """Who is installing, on which machine, in which boot, for which backend.

    `next_backend_id` is deliberately separate from `device_ref`. One names the
    host (it is what a revoke re-checks), the other names the Fleet Next backend
    the sidecar is being installed *for*. Letting one field stand for both is how
    a grant for backend A ends up revoking an install made for backend B, so
    `_guard_identity` refuses a root where the two are the same string.

    `operator` is here as well as in #506's `Grant` because the two are compared:
    a grant whose operator is not the operator holding this identity is a grant
    for someone else's action.
    """

    device_ref: str
    boot_id: str
    next_backend_id: str
    operator: str

    def missing(self) -> tuple[str, ...]:
        empty = []
        for name in ("device_ref", "boot_id", "next_backend_id", "operator"):
            if not str(getattr(self, name) or "").strip():
                empty.append(name)
        return tuple(empty)


@dataclass(frozen=True)
class Provenance:
    """Where the bytes came from: a repository and the commit they were read at."""

    repo: Path
    commit: str


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class InstallPlan:
    """Everything an apply would do, computed without touching the disk."""

    descriptor: Any
    root: Path
    root_real: str
    upgrade: bool
    entrypoint: str
    manifest_digest: str
    receipt_digest: str
    receipt_bytes: bytes
    marker_bytes: bytes
    sources: tuple[tuple[str, bytes], ...]
    staging: Path
    backup_dir: str = ""
    previous_digest: str = ""

    def file_digests(self) -> dict[str, str]:
        return {path: _sha(body) for path, body in self.sources}


@dataclass(frozen=True)
class InstallOutcome:
    decision: str
    code: str
    reasons: tuple[str, ...] = ()
    applied: bool = False
    written: tuple[str, ...] = ()
    root: str = ""
    manifest_digest: str = ""
    receipt_digest: str = ""
    staging: str = ""
    verification: Mapping[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "code": self.code,
            "applied": self.applied,
            "reasons": list(self.reasons),
            "written": list(self.written),
            "root": self.root,
            "manifest_digest": self.manifest_digest,
            "receipt_digest": self.receipt_digest,
            "staging": self.staging,
        }


@dataclass(frozen=True)
class RevokeOutcome:
    decision: str
    code: str
    reasons: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    backend_id: str = ""
    root: str = ""


@dataclass(frozen=True)
class RestoreOutcome:
    decision: str
    code: str
    reasons: tuple[str, ...] = ()
    restored: tuple[str, ...] = ()
    backend_id: str = ""
    root: str = ""


@dataclass(frozen=True)
class RecoveryOutcome:
    decision: str
    code: str
    reasons: tuple[str, ...] = ()
    restored: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    root: str = ""


# --------------------------------------------------------------------------- #
# small filesystem facts
# --------------------------------------------------------------------------- #
def _fsync_file(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_atomic(target: Path, body: bytes) -> None:
    """Write `body` so that a reader sees either the old bytes or all of them."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.wave18-tmp")
    with open(tmp, "wb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)


def _sha(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _is_link(path: Path) -> bool:
    """True for a symlink **and** for a Windows reparse point (a junction)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def git(repo: Path | str, args: Sequence[str]) -> bytes:
    """Run one read-only git verb. Anything else is refused before it runs."""
    if not args or args[0] not in READ_ONLY_GIT:
        raise ValueError(f"{list(args[:1])} is not one of {sorted(READ_ONLY_GIT)}")
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          env=env, check=False)
    if done.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed: "
                         f"{done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout


def git_blob(repo: Path | str, commit: str, path: str) -> bytes:
    """The bytes of `path` **at** `commit`, not in the worktree."""
    return git(repo, ["cat-file", "blob", f"{commit}:{path}"])


def git_commit(repo: Path | str, ref: str) -> str:
    raw = git(repo, ["rev-parse", ref]).decode("ascii", "replace").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", raw):
        raise ValueError(f"{ref!r} resolved to {raw!r}, which is not a commit")
    return raw


# --------------------------------------------------------------------------- #
# the lock: one writer per root, across processes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Lock:
    path: Path
    pid: int
    boot_id: str
    token: str

    def release(self) -> None:
        """Remove the lock only if it is still ours.

        A lock another writer now owns is not ours to delete, and a stale one is
        not ours to steal silently: `recover()` clears it, after checking that
        the recorded process is gone.
        """
        body = read_lock(self.path)
        if body is not None and str(body.get("token", "")) == self.token:
            _unlink_quietly(self.path)


def lock_path_for(root: Path) -> Path:
    return Path(str(root) + LOCK_SUFFIX)


def read_lock(path: Path) -> Mapping[str, Any] | None:
    try:
        body = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return body if isinstance(body, Mapping) else None


def pid_alive(pid: int) -> bool:
    """Whether a process with this pid exists. Assumes nothing else about it.

    On Windows this does not go through `os.kill`: `os.kill(pid, sig)` there is
    `TerminateProcess(handle, sig)` for any `sig` that is not a console event,
    and a liveness probe that can terminate the writer it is checking would be
    the worst possible implementation of "one writer per root". `tasklist` asks
    the question without acting on the answer.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:
    """Ask `tasklist`, and read its PID column rather than grep its text.

    CSV, because the fixed-width output pads with spaces and a pid can appear in
    the memory column ("23,952 K" contains 23 and 952). The pid column is the
    second field; anything else is a false positive waiting to happen.
    """
    try:
        done = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH",
                               "/FO", "CSV"],
                              capture_output=True, check=False)
    except OSError:
        return False
    for line in done.stdout.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith('"'):
            continue   # "INFO: No tasks are running ..." is not a process
        fields = [field.strip('"') for field in line.split('","')]
        if len(fields) >= 2 and fields[1] == str(pid):
            return True
    return False


def acquire_lock(root: Path, *, boot_id: str, purpose: str) -> Lock:
    """Create the lock, or raise `FileExistsError` if another writer holds it.

    `O_CREAT|O_EXCL` is the whole mechanism: the create *is* the test, so two
    processes cannot both win — which a read-then-write cannot promise.
    """
    path = lock_path_for(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}-{int(time.time() * 1000)}-{purpose}"
    body = json.dumps({"pid": os.getpid(), "boot_id": boot_id, "purpose": purpose,
                       "token": token, "started_at": int(time.time())},
                      sort_keys=True).encode("utf-8")
    handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        os.write(handle, body)
        os.fsync(handle)
    finally:
        os.close(handle)
    return Lock(path=path, pid=os.getpid(), boot_id=boot_id, token=token)


# --------------------------------------------------------------------------- #
# the guards, declared as names
# --------------------------------------------------------------------------- #
@dataclass
class _Ctx:
    upstream: Any
    descriptor: Any
    root: Path
    grant: Any
    identity: Identity
    allowed_prefixes: tuple[Path, ...] = ()
    legacy_roots: tuple[Path, ...] = ()
    provenance: Provenance | None = None
    sources: Mapping[str, bytes] = field(default_factory=dict)
    sources_given: bool = False
    root_real: str = ""
    receipt: Any = None
    marker: Mapping[str, Any] | None = None
    upgrade: bool = False


def _verdict(ctx: _Ctx, code: str, *reasons: str) -> Any:
    return ctx.upstream.Verdict(BLOCKED, code, tuple(reasons))


def _guard_interface(ctx: _Ctx) -> Any:
    _present, missing = interface(ctx.upstream)
    if missing:
        return _verdict(ctx, BLOCKED_INTERFACE,
                        f"sidecar_release is missing {', '.join(missing)}",
                        _interface_patch(missing))
    return None


def _guard_identity(ctx: _Ctx) -> Any:
    missing = ctx.identity.missing()
    if missing:
        return _verdict(ctx, BLOCKED_IDENTITY_INCOMPLETE,
                        f"the install identity is incomplete: {', '.join(missing)}")
    if ctx.identity.next_backend_id.strip() == ctx.identity.device_ref.strip():
        return _verdict(ctx, BLOCKED_IDENTITY_CONFLATED,
                        f"device_ref and next_backend_id are both "
                        f"{ctx.identity.device_ref!r}; one host identity and one "
                        f"backend identity are two facts, and a receipt that records "
                        f"one value cannot tell a revoke for the wrong backend from a "
                        f"revoke for the right one")
    return None


def _grant_problem(ctx: _Ctx, action: str) -> Any:
    grant = ctx.grant
    article = "an" if action[:1].lower() in "aeiou" else "a"
    if grant is None:
        return _verdict(ctx, BLOCKED_NO_GRANT,
                        f"{article} {action} needs a grant whose action is "
                        f"{action!r}")
    covers = getattr(grant, "covers", None)
    if not callable(covers) or not covers(action):
        return _verdict(ctx, BLOCKED_NO_GRANT,
                        f"the grant's action is {getattr(grant, 'action', '')!r}, which "
                        f"does not cover {action!r}")
    if getattr(grant, "device_ref", "") != ctx.identity.device_ref:
        return _verdict(ctx, BLOCKED_IDENTITY_MISMATCH,
                        f"the grant is for {getattr(grant, 'device_ref', '')!r}, this is "
                        f"{ctx.identity.device_ref!r}")
    if getattr(grant, "operator", "") != ctx.identity.operator:
        return _verdict(ctx, BLOCKED_IDENTITY_MISMATCH,
                        f"the grant names operator {getattr(grant, 'operator', '')!r}, "
                        f"the identity names {ctx.identity.operator!r}")
    return None


def _guard_grant(ctx: _Ctx) -> Any:
    return _grant_problem(ctx, "install")


def _guard_grant_revoke(ctx: _Ctx) -> Any:
    return _grant_problem(ctx, "revoke")


def _guard_grant_restore(ctx: _Ctx) -> Any:
    """A rollback is a third action, not a revoke with extra steps.

    #506's `restore()` requires a grant whose action is `restore`; checking the
    same thing here means a caller who brings a revoke grant hears it from this
    module, with this module's words, instead of from inside `restore()`.
    """
    return _grant_problem(ctx, "restore")


def _guard_provenance(ctx: _Ctx) -> Any:
    if ctx.provenance is None:
        return _verdict(ctx, BLOCKED_NO_PROVENANCE,
                        "no provenance was given; an install pins a commit, and a "
                        "receipt that cannot name one cannot be traced back")
    if not str(ctx.provenance.commit):
        return _verdict(ctx, BLOCKED_NO_PROVENANCE, "the provenance names no commit")
    if ctx.provenance.commit != getattr(ctx.descriptor, "commit", ""):
        return _verdict(ctx, BLOCKED_PROVENANCE_MISMATCH,
                        f"the descriptor pins {getattr(ctx.descriptor, 'commit', '')!r} "
                        f"and the provenance names {ctx.provenance.commit!r}")
    pinned = dict(getattr(ctx.descriptor, "files", ()))
    for path in pinned:
        try:
            body = git_blob(ctx.provenance.repo, ctx.provenance.commit, path)
        except ValueError as exc:
            return _verdict(ctx, BLOCKED_PROVENANCE_MISMATCH, f"{path}: {exc}")
        if _sha(body) != pinned[path]:
            return _verdict(ctx, BLOCKED_PROVENANCE_MISMATCH,
                            f"{path}: the bytes at {ctx.provenance.commit[:8]} do not "
                            f"have the digest this descriptor pins")
    return None


def _guard_descriptor(ctx: _Ctx) -> Any:
    verdict = ctx.upstream.check_descriptor(ctx.descriptor)
    if verdict.decision != OK:
        return verdict
    pinned = tuple(path for path, _digest in ctx.descriptor.files)
    if tuple(sorted(pinned)) != tuple(sorted(SIDECAR_FILES)):
        return _verdict(ctx, BLOCKED_NOT_THE_SIDECAR,
                        f"this descriptor pins {', '.join(sorted(pinned))}, and the "
                        f"sidecar is {', '.join(sorted(SIDECAR_FILES))}; a receipt for "
                        f"some other pair is not evidence about the sidecar")
    if ctx.descriptor.name != SIDECAR_NAME:
        return _verdict(ctx, BLOCKED_NOT_THE_SIDECAR,
                        f"the descriptor names {ctx.descriptor.name!r}")
    return None


def _guard_root(ctx: _Ctx) -> Any:
    refusal = ctx.upstream._root_refusal(ctx.root, ctx.legacy_roots)
    if refusal is not None:
        return refusal
    if not ctx.allowed_prefixes:
        return _verdict(ctx, BLOCKED_ROOT_NOT_NAMESPACED,
                        "no allowed prefix was supplied; an installer that writes "
                        "wherever it is pointed has no namespace")
    inside_any = False
    for prefix in ctx.allowed_prefixes:
        prefix_real = os.path.realpath(str(prefix))
        if ctx.root_real == prefix_real:
            return _verdict(ctx, BLOCKED_ROOT_NOT_NAMESPACED,
                            f"{str(ctx.root)!r} is the allowed prefix itself; a root is "
                            f"one level below it")
        if ctx.upstream._inside(ctx.root_real, prefix_real):
            inside_any = True
    if not inside_any:
        return _verdict(ctx, BLOCKED_ROOT_NOT_NAMESPACED,
                        f"{str(ctx.root)!r} is not inside any allowed prefix "
                        f"({', '.join(str(p) for p in ctx.allowed_prefixes)})")
    if ctx.root.name != SIDECAR_ROOT_NAME:
        return _verdict(ctx, BLOCKED_ROOT_NOT_NAMESPACED,
                        f"{str(ctx.root)!r} does not end in {SIDECAR_ROOT_NAME!r}; the "
                        f"leaf name is what keeps two installs from sharing a root")
    # The parent chain must not be a link: a junction anywhere above the root
    # silently redirects every write, and a resolved path would not show it.
    plain = Path(os.path.abspath(str(ctx.root)))
    for ancestor in plain.parents:
        if str(ancestor) in ("", os.sep, plain.anchor):
            continue
        if _is_link(ancestor):
            return _verdict(ctx, BLOCKED_PARENT_ESCAPE,
                            f"{str(ancestor)!r} is a link on the way to the root")
    return None


def _guard_sources(ctx: _Ctx) -> Any:
    """Only judges bytes the caller handed in; git-sourced bytes are judged above."""
    if not ctx.sources_given:
        return None
    problems: list[str] = []
    for path, pinned in getattr(ctx.descriptor, "files", ()):
        body = ctx.sources.get(path)
        if body is None:
            problems.append(f"{path}: no bytes supplied")
        elif _sha(body) != pinned:
            problems.append(f"{path}: the bytes are not the pinned bytes")
    if problems:
        return _verdict(ctx, BLOCKED_SOURCE_DIGEST, *problems)
    return None


def _guard_lock(ctx: _Ctx) -> Any:
    path = lock_path_for(ctx.root)
    if not (path.exists() or _is_link(path)):
        return None
    body = read_lock(path) or {}
    pid = int(body.get("pid", 0) or 0)
    if pid_alive(pid):
        return _verdict(ctx, BLOCKED_CONCURRENT_WRITER,
                        f"{path.name} is held by pid {pid} "
                        f"({body.get('purpose', '?')})")
    return _verdict(ctx, BLOCKED_STALE_WRITER,
                    f"{path.name} names pid {pid}, which is not running; this is a "
                    f"leftover from a crash, not a live writer, and recover() is what "
                    f"clears it")


def _guard_destination(ctx: _Ctx) -> Any:
    staging = _staging_candidates(ctx.root)
    if staging:
        return _verdict(ctx, BLOCKED_STALE_STAGING,
                        f"{len(staging)} staging director"
                        f"{'y' if len(staging) == 1 else 'ies'} beside the root "
                        f"({', '.join(p.name for p in staging)}); an interrupted run "
                        f"left them, and recover() decides what they are")
    if not ctx.root.exists():
        ctx.upgrade = False
        return None
    if not ctx.root.is_dir():
        return _verdict(ctx, BLOCKED_ROOT_NOT_EMPTY,
                        f"{str(ctx.root)!r} exists and is not a directory")
    receipt = ctx.upstream.read_receipt(ctx.root)
    ctx.receipt = receipt
    if receipt is None:
        if any(ctx.root.iterdir()):
            return _verdict(ctx, BLOCKED_ROOT_NOT_EMPTY,
                            f"{str(ctx.root)!r} is not empty and carries no receipt")
        ctx.upgrade = False
        return None
    ctx.upgrade = True
    return None


def _guard_upgrade(ctx: _Ctx) -> Any:
    if not ctx.upgrade:
        return None
    receipt = ctx.receipt
    if receipt is None or getattr(receipt, "broken", False) or not receipt.is_intact():
        return _verdict(ctx, BLOCKED_RECEIPT_TAMPERED,
                        "the receipt in the root does not verify, so an upgrade would "
                        "not be replacing something this tool can account for")
    if receipt.install_root != ctx.root_real:
        return _verdict(ctx, BLOCKED_RECEIPT_MISMATCH,
                        f"the receipt names {receipt.install_root!r}, not "
                        f"{ctx.root_real!r}")
    if receipt.device_ref != ctx.identity.device_ref:
        return _verdict(ctx, BLOCKED_IDENTITY_MISMATCH,
                        f"the receipt belongs to {receipt.device_ref!r}")
    if receipt.boot_id != ctx.identity.boot_id:
        return _verdict(ctx, BLOCKED_BOOT_MISMATCH,
                        f"the receipt was written on boot {receipt.boot_id!r}")
    if receipt.previous is not None:
        return _verdict(ctx, BLOCKED_UPGRADE_CHAIN,
                        f"this receipt already remembers {receipt.previous.version}; a "
                        f"second upgrade would overwrite that memory, and a rollback "
                        f"would have nothing to restore")
    problems = ctx.upstream._file_problems(ctx.root, receipt.files)
    if problems:
        first = problems[0]
        return ctx.upstream.Verdict(first.decision, first.code, first.reasons)
    allowed = ctx.upstream._allowed_entries(receipt)
    extra = []
    for path in sorted(ctx.root.rglob("*")):
        if path.is_dir() and not _is_link(path):
            continue
        rel = path.relative_to(ctx.root).as_posix()
        if rel not in allowed and rel != ctx.upstream.LOCK_NAME:
            extra.append(rel)
    if extra:
        return _verdict(ctx, BLOCKED_FOREIGN_PRESENT,
                        f"the root holds files the receipt does not own: "
                        f"{', '.join(sorted(extra))}; carrying them into the new "
                        f"receipt would make the result unrevocable")
    if getattr(receipt, "version", "") == ctx.descriptor.version:
        return _verdict(ctx, BLOCKED_ALREADY_INSTALLED,
                        f"{ctx.descriptor.version} is already installed in "
                        f"{str(ctx.root)!r}")
    return None


def _guard_receipt_first(ctx: _Ctx) -> Any:
    receipt = ctx.upstream.read_receipt(ctx.root)
    ctx.receipt = receipt
    if receipt is None:
        return _verdict(ctx, BLOCKED_NO_RECEIPT,
                        f"{str(ctx.root)!r} holds no receipt; there is no evidence to "
                        f"revoke against, and deleting without it is the operation this "
                        f"whole surface exists to refuse")
    return None


def _guard_backend(ctx: _Ctx) -> Any:
    marker = ctx.upstream._read_marker(ctx.root)
    ctx.marker = marker
    if marker is None:
        return _verdict(ctx, BLOCKED_BACKEND_MISSING,
                        "the ownership marker is gone, so the backend this install was "
                        "made for cannot be established")
    recorded = str(marker.get("next_backend_id", ""))
    if not recorded:
        return _verdict(ctx, BLOCKED_BACKEND_MISSING,
                        "the ownership marker records no next_backend_id; this is not an "
                        "install this module wrote")
    if recorded != ctx.identity.next_backend_id:
        return _verdict(ctx, BLOCKED_BACKEND_MISMATCH,
                        f"this install was made for backend {recorded!r}, and this "
                        f"revoke is for {ctx.identity.next_backend_id!r}")
    return None


#: Declared as **names**. A tuple of function objects is bound once at import,
#: so rebinding a guard would leave the pipeline unchanged and "disable exactly
#: one guard and see what turns red" would measure nothing. Wave 17 learned that
#: the expensive way; `_pipeline` resolves names at call time and a test asserts
#: that it does.
INSTALL_GUARDS: tuple[str, ...] = (
    "_guard_interface",
    "_guard_identity",
    "_guard_grant",
    "_guard_descriptor",
    "_guard_provenance",
    "_guard_root",
    "_guard_lock",
    "_guard_destination",
    "_guard_upgrade",
    "_guard_sources",
)

REVOKE_GUARDS: tuple[str, ...] = (
    "_guard_interface",
    "_guard_identity",
    "_guard_grant_revoke",
    "_guard_lock",
    # Isolation and Legacy containment are deliberately **not** re-checked here.
    # `revoke_verified` hands `legacy_roots` to #506's `revoke`, whose pipeline
    # runs `_root_refusal` on the same paths, so a copy of that check here would
    # refuse nothing the first one did not — and the mutation probe said SILENT
    # for it, twice, which is the measurement saying a second identical check is
    # not a control. Namespacing is not checked at all on this path: a root can
    # be somewhere this module would not install *today* (the prefix may have
    # been renamed since) and a revoke must still be able to clean it up.
    "_guard_receipt_first",
    "_guard_backend",
)

#: A rollback is its own question — the grant it needs is `restore`, and the
#: receipt it needs remembers a previous version — so it gets its own list
#: rather than borrowing the revoke one. Declared here for the same reason the
#: other two are: a guard the probe cannot enumerate is a guard nobody measured.
RESTORE_GUARDS: tuple[str, ...] = (
    "_guard_interface",
    "_guard_identity",
    "_guard_grant_restore",
    "_guard_lock",
    "_guard_receipt_first",
    "_guard_backend",
)


def _pipeline(names: Sequence[str]) -> tuple[Any, ...]:
    resolved = []
    for name in names:
        guard = globals().get(name)
        if not callable(guard):
            raise AssertionError(f"guard {name!r} is not a function in this module")
        resolved.append(guard)
    return tuple(resolved)


def install_guards() -> tuple[Any, ...]:
    return _pipeline(INSTALL_GUARDS)


def revoke_guards() -> tuple[Any, ...]:
    return _pipeline(REVOKE_GUARDS)


def restore_guards() -> tuple[Any, ...]:
    return _pipeline(RESTORE_GUARDS)


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #
def _staging_candidates(root: Path) -> tuple[Path, ...]:
    parent = root.parent
    if not parent.is_dir():
        return ()
    prefix = root.name + STAGING_SUFFIX
    return tuple(sorted(p for p in parent.iterdir() if p.name.startswith(prefix)))


def _token() -> str:
    return f"{os.getpid()}-{int(time.time() * 1000)}"


def _marker_body(upstream: Any, *, root_real: str, identity: Identity,
                 descriptor: Any, receipt_digest: str) -> dict[str, Any]:
    """The ownership marker, plus the one fact the receipt has no field for.

    #506's marker guard compares four keys (`install_root`, `device_ref`,
    `version`, `receipt_digest`) and tolerates the rest, so `next_backend_id`
    rides inside the same file rather than in a second schema. It is not a blind
    spot: `_guard_backend` re-checks it before any revoke is delegated.
    """
    return {
        "schema": upstream.MARKER_SCHEMA,
        "install_root": root_real,
        "device_ref": identity.device_ref,
        "name": descriptor.name,
        "version": descriptor.version,
        "manifest_digest": descriptor.manifest_digest(),
        "receipt_digest": receipt_digest,
        "next_backend_id": identity.next_backend_id,
        "installed_by": identity.operator,
        "installer_schema": SCHEMA,
    }


def _json_bytes(body: Mapping[str, Any]) -> bytes:
    return json.dumps(body, sort_keys=True, indent=2,
                      ensure_ascii=False).encode("utf-8")


def descriptor_for(repo: Path | str, ref: str, *,
                   version: str = DEFAULT_VERSION) -> tuple[Any, Provenance]:
    """Pin the two files at `ref` **in git**, and the commit they were read at."""
    upstream = _upstream()
    commit = git_commit(repo, ref)
    descriptor = upstream.descriptor_from_git(repo, commit, SIDECAR_FILES,
                                              version=version, name=SIDECAR_NAME)
    return descriptor, Provenance(repo=Path(repo), commit=commit)


def plan_install(descriptor: Any, root: Path | str, *, grant: Any,
                 identity: Identity,
                 allowed_prefixes: Iterable[Path],
                 provenance: Provenance | None = None,
                 sources: Mapping[str, bytes] | None = None,
                 legacy_roots: Iterable[Path] = (),
                 upstream: Any | None = None) -> tuple[InstallPlan | None, InstallOutcome]:
    """Run every install guard, then compute the bytes an apply would write.

    Nothing here writes: `sources=None` reads the pinned blobs out of git, and
    every failure is a refusal with a code. A caller that wants the plan to
    become an install calls `apply_plan`.
    """
    mod = _upstream(upstream)
    root = Path(root)
    ctx = _Ctx(upstream=mod, descriptor=descriptor, root=root, grant=grant,
               identity=identity,
               allowed_prefixes=tuple(Path(p) for p in allowed_prefixes),
               legacy_roots=tuple(Path(p) for p in legacy_roots),
               provenance=provenance,
               sources=dict(sources or {}), sources_given=sources is not None)
    ctx.root_real = os.path.realpath(str(root))

    for guard in install_guards():
        refusal = guard(ctx)
        if refusal is not None:
            return None, InstallOutcome(refusal.decision, refusal.code,
                                        tuple(refusal.reasons),
                                        verification=dict(VERIFICATION))

    if not ctx.sources_given:
        # The bytes come from the commit, which is the only thing that can be
        # pinned. `provenance` is not None here: `_guard_provenance` refused.
        assert provenance is not None
        try:
            ctx.sources = {path: git_blob(provenance.repo, provenance.commit, path)
                           for path in sorted(SIDECAR_FILES)}
        except ValueError as exc:
            return None, InstallOutcome(BLOCKED, BLOCKED_PROVENANCE_MISMATCH,
                                        (str(exc),), verification=dict(VERIFICATION))
        for path, pinned in descriptor.files:
            if _sha(ctx.sources[path]) != pinned:
                return None, InstallOutcome(
                    BLOCKED, BLOCKED_SOURCE_DIGEST,
                    (f"{path}: the bytes at {provenance.commit[:8]} are not the pinned "
                     f"bytes",), verification=dict(VERIFICATION))

    records = tuple(mod.FileRecord(path=path, sha256=_sha(body), size=len(body))
                    for path, body in sorted(ctx.sources.items()))
    previous = None
    backup_dir = ""
    previous_digest = ""
    if ctx.upgrade:
        old = ctx.receipt
        backup_dir = f"{mod.BACKUP_DIR}/{old.version}"
        previous_digest = old.digest
        previous = mod.PreviousVersion(version=old.version,
                                       manifest_digest=old.manifest_digest,
                                       backup_dir=backup_dir, files=old.files)
    body = receipt_body_of(mod)(descriptor, root_real=ctx.root_real,
                                device_ref=identity.device_ref,
                                boot_id=identity.boot_id, grant=grant,
                                records=records, previous=previous)
    digest = mod.receipt_digest(body)
    body["receipt_digest"] = digest
    plan = InstallPlan(descriptor=descriptor, root=root, root_real=ctx.root_real,
                       upgrade=ctx.upgrade, entrypoint=descriptor.entrypoint,
                       manifest_digest=descriptor.manifest_digest(),
                       receipt_digest=digest, receipt_bytes=_json_bytes(body),
                       marker_bytes=_json_bytes(_marker_body(
                           mod, root_real=ctx.root_real, identity=identity,
                           descriptor=descriptor, receipt_digest=digest)),
                       sources=tuple(sorted(ctx.sources.items())),
                       staging=root.parent / (root.name + STAGING_SUFFIX + _token()),
                       backup_dir=backup_dir, previous_digest=previous_digest)
    return plan, InstallOutcome(OK, DRY_RUN, applied=False, root=str(root),
                                manifest_digest=plan.manifest_digest,
                                receipt_digest=digest, staging=str(plan.staging),
                                verification=dict(VERIFICATION))


def install(descriptor: Any, root: Path | str, *, grant: Any, identity: Identity,
            allowed_prefixes: Iterable[Path],
            provenance: Provenance | None = None,
            sources: Mapping[str, bytes] | None = None,
            legacy_roots: Iterable[Path] = (), apply: bool = False,
            crash_at: str = "", fail_at: str = "",
            upstream: Any | None = None) -> InstallOutcome:
    """Plan, and — only when `apply=True` — write. Dry run is the default."""
    plan, outcome = plan_install(
        descriptor, root, grant=grant, identity=identity,
        allowed_prefixes=allowed_prefixes, provenance=provenance, sources=sources,
        legacy_roots=legacy_roots, upstream=upstream)
    if plan is None or not apply:
        return outcome
    return apply_plan(plan, boot_id=identity.boot_id, crash_at=crash_at,
                      fail_at=fail_at, upstream=upstream)


# --------------------------------------------------------------------------- #
# applying
# --------------------------------------------------------------------------- #
def _crash(point: str, crash_at: str) -> None:
    if crash_at and crash_at == point:
        raise SimulatedCrash(f"simulated crash at {point}")


def _checkpoint(point: str, crash_at: str, fail_at: str) -> None:
    _crash(point, crash_at)
    if fail_at and fail_at == point:
        raise InjectedFailure(f"simulated write failure at {point}")


def _stage_tree(plan: InstallPlan, mod: Any, *, crash_at: str = "",
                fail_at: str = "") -> None:
    """Write the whole install into the staging directory, receipt included.

    The staging directory is a **sibling** of the root on the same volume, which
    is what makes the next step one rename and this step invisible: nothing here
    is at the root yet, so a death here leaves a root that does not exist rather
    than a root that half exists.
    """
    staging = plan.staging
    if staging.exists():
        raise FileExistsError(f"{staging} already exists")
    staging.mkdir(parents=False)
    for path, body in plan.sources:
        target = staging / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        _fsync_file(target)
    _checkpoint("staged-files", crash_at, fail_at)
    (staging / mod.OWNER_MARKER_NAME).write_bytes(plan.marker_bytes)
    _checkpoint("staged-marker", crash_at, fail_at)
    (staging / mod.RECEIPT_NAME).write_bytes(plan.receipt_bytes)
    _fsync_file(staging / mod.RECEIPT_NAME)
    _checkpoint("staged-receipt", crash_at, fail_at)


def _verify_installed(plan: InstallPlan, mod: Any) -> tuple[Any, tuple[str, ...]]:
    """Read the result back through #506's reader, not through our own state."""
    receipt = mod.read_receipt(plan.root)
    if receipt is None:
        return (mod.Verdict(BLOCKED, BLOCKED_VERIFY_FAILED,
                            ("no receipt is readable at the root after the write",)), ())
    if not receipt.is_intact():
        return (mod.Verdict(BLOCKED, BLOCKED_RECEIPT_TAMPERED,
                            ("the receipt written is not intact when read back",)), ())
    if receipt.digest != plan.receipt_digest:
        return (mod.Verdict(BLOCKED, BLOCKED_VERIFY_FAILED,
                            ("the receipt read back is not the one this plan "
                             "computed",)), ())
    if receipt.manifest_digest != plan.manifest_digest:
        return (mod.Verdict(BLOCKED, BLOCKED_VERIFY_FAILED,
                            (f"the receipt records {receipt.manifest_digest}, this plan "
                             f"computed {plan.manifest_digest}",)), ())
    problems = mod._file_problems(plan.root, receipt.files)
    if problems:
        first = problems[0]
        return (mod.Verdict(BLOCKED, first.code, first.reasons), ())
    return None, tuple(sorted(record.path for record in receipt.files))


def apply_plan(plan: InstallPlan, *, boot_id: str, crash_at: str = "",
               fail_at: str = "", upstream: Any | None = None) -> InstallOutcome:
    """Write the plan to disk, atomically at the granularity each case allows."""
    mod = _upstream(upstream)
    try:
        lock = acquire_lock(plan.root, boot_id=boot_id, purpose="install")
    except FileExistsError:
        return InstallOutcome(BLOCKED, BLOCKED_CONCURRENT_WRITER,
                              (f"{lock_path_for(plan.root).name} exists; another writer "
                               f"created it first",),
                              verification=dict(VERIFICATION))
    try:
        if plan.upgrade:
            return _apply_upgrade(plan, mod, lock, crash_at=crash_at, fail_at=fail_at)
        return _apply_fresh(plan, mod, crash_at=crash_at, fail_at=fail_at)
    finally:
        lock.release()


def _apply_fresh(plan: InstallPlan, mod: Any, *, crash_at: str,
                 fail_at: str) -> InstallOutcome:
    """Create the root in one rename: before it, nothing; after it, everything."""
    try:
        _stage_tree(plan, mod, crash_at=crash_at, fail_at=fail_at)
        _checkpoint("before-rename", crash_at, fail_at)
        if plan.root.exists():
            # A guard checked this, but the window between the check and the
            # rename is real, and renaming onto an existing non-empty directory
            # is a mess.
            _discard(plan.staging)
            return InstallOutcome(BLOCKED, BLOCKED_ROOT_NOT_EMPTY,
                                  (f"{str(plan.root)!r} appeared while this install was "
                                   f"staging",), verification=dict(VERIFICATION))
        os.rename(plan.staging, plan.root)
    except SimulatedCrash:
        raise
    except Exception as exc:
        _discard(plan.staging)
        return InstallOutcome(BLOCKED, BLOCKED_ABORTED, (str(exc),),
                              root=str(plan.root), verification=dict(VERIFICATION))
    refusal, written = _verify_installed(plan, mod)
    if refusal is not None:
        _discard(plan.root)
        return InstallOutcome(refusal.decision, refusal.code, tuple(refusal.reasons),
                              root=str(plan.root), verification=dict(VERIFICATION))
    return InstallOutcome(OK, OK, applied=True, written=written, root=str(plan.root),
                          manifest_digest=plan.manifest_digest,
                          receipt_digest=plan.receipt_digest,
                          verification=dict(VERIFICATION))


def _apply_upgrade(plan: InstallPlan, mod: Any, lock: Lock,
                   *, crash_at: str, fail_at: str) -> InstallOutcome:
    """Replace an installed version, with the old bytes already saved.

    An upgrade cannot be one rename — the target exists and is not empty. So the
    order is chosen to make every interruption recoverable: the old bytes go into
    the in-root backup first (the *new* receipt names it as `previous`, which is
    what `restore()` needs anyway), then a lock inside the root makes a concurrent
    revoke refuse with `BLOCKED_CONCURRENT_WRITER` instead of reading a mix, then
    each file is replaced atomically, then the marker, then the receipt.
    """
    old = mod.read_receipt(plan.root)
    assert old is not None
    old_receipt_bytes = _read_bytes(plan.root / mod.RECEIPT_NAME) or b""
    old_marker_bytes = _read_bytes(plan.root / mod.OWNER_MARKER_NAME) or b""
    in_root_lock = plan.root / mod.LOCK_NAME
    if in_root_lock.exists() or _is_link(in_root_lock):
        _discard(plan.staging)
        return InstallOutcome(BLOCKED, BLOCKED_CONCURRENT_WRITER,
                              (f"{mod.LOCK_NAME} is present in the root",),
                              verification=dict(VERIFICATION))
    in_root_lock.write_bytes(json.dumps(
        {"pid": os.getpid(), "boot_id": lock.boot_id, "purpose": "upgrade",
         "token": lock.token}, sort_keys=True).encode("utf-8"))
    _fsync_file(in_root_lock)
    try:
        _stage_tree(plan, mod, crash_at=crash_at, fail_at=fail_at)
        for record in old.files:
            body = _read_bytes(plan.root / record.path)
            if body is None:
                raise OSError(f"{record.path} vanished during the upgrade")
            _write_atomic(plan.root / plan.backup_dir / record.path, body)
        _checkpoint("after-backup", crash_at, fail_at)
        for path, _body in plan.sources:
            os.replace(plan.staging / path, plan.root / path)
        _checkpoint("after-files", crash_at, fail_at)
        _write_atomic(plan.root / mod.OWNER_MARKER_NAME, plan.marker_bytes)
        _checkpoint("after-marker", crash_at, fail_at)
        _write_atomic(plan.root / mod.RECEIPT_NAME, plan.receipt_bytes)
        _checkpoint("after-receipt", crash_at, fail_at)
    except SimulatedCrash:
        # A process that stops does not run its cleanup; the debris is what
        # `recover()` is for, and the test asserts on the debris.
        raise
    except Exception as exc:
        _rollback_upgrade(plan, mod, old, old_receipt_bytes, old_marker_bytes)
        _discard(plan.staging)
        _unlink_quietly(in_root_lock)
        return InstallOutcome(BLOCKED, BLOCKED_ABORTED, (str(exc),),
                              root=str(plan.root), verification=dict(VERIFICATION))
    refusal, written = _verify_installed(plan, mod)
    if refusal is not None:
        _rollback_upgrade(plan, mod, old, old_receipt_bytes, old_marker_bytes)
        _discard(plan.staging)
        _unlink_quietly(in_root_lock)
        return InstallOutcome(refusal.decision, refusal.code, tuple(refusal.reasons),
                              root=str(plan.root), verification=dict(VERIFICATION))
    # What is left of the staging directory is an empty shell; the backup stays,
    # because the new receipt names it as `previous`.
    _discard(plan.staging)
    _unlink_quietly(in_root_lock)
    return InstallOutcome(OK, OK, applied=True, written=written, root=str(plan.root),
                          manifest_digest=plan.manifest_digest,
                          receipt_digest=plan.receipt_digest,
                          verification=dict(VERIFICATION))


def _rollback_upgrade(plan: InstallPlan, mod: Any, old: Any,
                      old_receipt_bytes: bytes, old_marker_bytes: bytes) -> None:
    """Put the previous version back, byte for byte, from the in-root backup."""
    for record in old.files:
        body = _read_bytes(plan.root / plan.backup_dir / record.path)
        if body is not None:
            _write_atomic(plan.root / record.path, body)
    _write_atomic(plan.root / mod.RECEIPT_NAME, old_receipt_bytes)
    _write_atomic(plan.root / mod.OWNER_MARKER_NAME, old_marker_bytes)
    _prune_backup(plan.root / plan.backup_dir)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _prune_backup(backup: Path) -> None:
    """Remove an owned backup directory tree, deepest first.

    Not `shutil.rmtree`: this is called on a directory this module created, and
    a file somebody else put there survives, with its directory. The parent
    (`.sidecar-backup`) goes only if it is empty afterwards, so a second backup
    it still holds is never touched.
    """
    if not backup.exists():
        return
    for current, _dirs, files in os.walk(backup, topdown=False):
        for name in files:
            _unlink_quietly(Path(current) / name)
        try:
            Path(current).rmdir()
        except OSError:
            pass
    try:
        backup.parent.rmdir()
    except OSError:
        pass


def _discard(tree: Path) -> None:
    """Remove a tree this module created, file by file.

    Every path here was named by a plan, and a directory that somehow holds
    something else keeps it, rather than having it deleted because it was in the
    way.
    """
    if _is_link(tree) or (tree.exists() and not tree.is_dir()):
        _unlink_quietly(tree)
        return
    if not tree.is_dir():
        return
    for current, _dirs, files in os.walk(tree, topdown=False):
        for name in files:
            _unlink_quietly(Path(current) / name)
        try:
            Path(current).rmdir()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# revoke: #506's entrypoint, with the facts #506 cannot see checked first
# --------------------------------------------------------------------------- #
def _revoke_ctx(upstream: Any, descriptor: Any, root: Path | str, *, grant: Any,
                identity: Identity, legacy_roots: Iterable[Path]) -> _Ctx:
    root_path = Path(root)
    ctx = _Ctx(upstream=upstream, descriptor=descriptor, root=root_path, grant=grant,
               identity=identity,
               legacy_roots=tuple(Path(p) for p in legacy_roots))
    ctx.root_real = os.path.realpath(str(root_path))
    return ctx


def revoke_verified(descriptor: Any, root: Path | str, *, grant: Any,
                    identity: Identity, legacy_roots: Iterable[Path] = (),
                    upstream: Any | None = None) -> RevokeOutcome:
    """Refuse what this module can see, then hand the delete to #506's `revoke`.

    The deletion is not reimplemented: `sidecar_release.revoke()` owns every
    guard about receipts, markers, files, symlinks and containment, and its
    answer is returned verbatim. What is added is the fact a receipt has no field
    for — the backend identity — plus this module's own lock, so a revoke cannot
    walk into an upgrade in progress.
    """
    mod = _upstream(upstream)
    ctx = _revoke_ctx(mod, descriptor, root, grant=grant, identity=identity,
                      legacy_roots=legacy_roots)
    for guard in revoke_guards():
        refusal = guard(ctx)
        if refusal is not None:
            return RevokeOutcome(refusal.decision, refusal.code,
                                 tuple(refusal.reasons), root=str(ctx.root))
    result = mod.revoke(descriptor, ctx.root, grant=grant,
                        device_ref=identity.device_ref, boot_id=identity.boot_id,
                        legacy_roots=ctx.legacy_roots)
    return RevokeOutcome(result.decision, result.code, tuple(result.reasons),
                         tuple(result.deleted), identity.next_backend_id,
                         str(ctx.root))


def rollback_to_previous(previous_descriptor: Any, root: Path | str, *, grant: Any,
                         identity: Identity, legacy_roots: Iterable[Path] = (),
                         upstream: Any | None = None) -> RestoreOutcome:
    """Undo one upgrade through #506's `restore`, after the same identity checks.

    `restore()` rewrites the marker with its own seven keys (finding F1), so the
    backend identity is put back afterwards from the marker it was read from. The
    re-add is a repair of a field its owner never knew about — reported to that
    owner, not hidden here.
    """
    mod = _upstream(upstream)
    ctx = _revoke_ctx(mod, previous_descriptor, root, grant=grant, identity=identity,
                      legacy_roots=legacy_roots)
    before = mod._read_marker(ctx.root)
    backend = str((before or {}).get("next_backend_id", ""))
    for guard in restore_guards():
        refusal = guard(ctx)
        if refusal is not None:
            return RestoreOutcome(refusal.decision, refusal.code,
                                  tuple(refusal.reasons), root=str(ctx.root))
    result = mod.restore(previous_descriptor, ctx.root, grant=grant,
                         device_ref=identity.device_ref, boot_id=identity.boot_id,
                         legacy_roots=ctx.legacy_roots)
    restored = tuple(getattr(result, "restored", ()))
    if result.decision == OK and restored:
        _readd_backend(ctx.root, mod, backend=backend, operator=identity.operator)
    return RestoreOutcome(result.decision, result.code, tuple(result.reasons),
                          restored, backend, str(ctx.root))


def _readd_backend(root: Path, mod: Any, *, backend: str, operator: str) -> None:
    """Re-attach `next_backend_id` to a marker #506's restore rewrote (F1)."""
    marker = mod._read_marker(root)
    if not isinstance(marker, dict) or not backend:
        return
    if (marker.get("next_backend_id") == backend
            and marker.get("installed_by") == operator):
        return
    marker["next_backend_id"] = backend
    marker["installed_by"] = operator
    marker["installer_schema"] = SCHEMA
    _write_atomic(root / mod.OWNER_MARKER_NAME, _json_bytes(marker))


# --------------------------------------------------------------------------- #
# recovery: what to do about a run that died
# --------------------------------------------------------------------------- #
def _pending_sidecar_transaction(root: Path, mod: Any) -> str | None:
    """Whether #506's own repair owns this root right now, and why.

    Wave 19 gave `sidecar_release` a write-ahead journal, and from then on the
    engine's `recover()` is the only verb allowed to read it. This module's repair
    is a *different* repair: it puts bytes back from `.sidecar-backup/<version>/`
    and rewrites the marker. Two repairs of one transaction is exactly the
    ambiguity both modules exist to refuse — the second one would be working from
    a receipt the first one is in the middle of replacing.

    The names are read with `getattr`: a #506 revision without a journal has
    nothing to defer to, and this installer must still work against it. Requiring
    them through `REQUIRED_UPSTREAM_NAMES` would turn "older sidecar" into
    `BLOCKED_INTERFACE`, which is a different and worse answer.
    """
    journal_name = getattr(mod, "JOURNAL_NAME", "")
    staging_name = getattr(mod, "STAGING_DIR", "")
    if journal_name:
        journal = root / journal_name
        if journal.exists() or _is_link(journal):
            return (f"{journal_name} is present: a sidecar_release write to this root "
                    f"began and did not finish, and sidecar_release.recover() is the "
                    f"only verb allowed to read its journal")
    if staging_name:
        staging = root / staging_name
        if staging.exists() or _is_link(staging):
            return (f"{staging_name} is present: a sidecar_release write was staging "
                    f"here, and sidecar_release.recover() is what finishes or discards "
                    f"it")
    return None


def recover(root: Path | str, *, upstream: Any | None = None) -> RecoveryOutcome:
    """Clear what a crashed run left, where the bytes say unambiguously what it was.

    Three shapes are recoverable without guessing, and each is gated on evidence
    rather than on an age:

    * a sibling lock whose recorded process is gone;
    * a staging directory beside the root — the root never contained it, so
      discarding it cannot lose an install;
    * an interrupted **upgrade**, recognised by the in-root lock plus a receipt
      whose own files disagree with the disk: the old bytes are in
      `.sidecar-backup/<receipt.version>/` and are written back only when they
      still digest to what the receipt recorded.

    Anything else is reported, not repaired. A repair that guesses is worse than
    a refusal that says what to look at.

    The sibling lock is the **liveness gate**. It is created by the same process
    that writes the in-root lock, and it is checked first, so reaching the
    upgrade repair means no live writer holds this root — which is why the
    in-root lock is not itself pid-checked. A real death leaves both; the repair
    only ever runs after the sibling one has been shown to be a corpse.
    """
    mod = _upstream(upstream)
    root_path = Path(root)
    removed: list[str] = []
    restored: list[str] = []
    reasons: list[str] = []

    pending = _pending_sidecar_transaction(root_path, mod)
    if pending is not None:
        # First, and before *anything* is removed: the sibling lock below is this
        # module's, but the root belongs to the engine's journal until that
        # journal is finished or discarded, and this repair must not start
        # rewriting files underneath it.
        return RecoveryOutcome(BLOCKED, BLOCKED_TXN_PENDING, (pending,),
                               root=str(root_path))

    lock = lock_path_for(root_path)
    if lock.exists() or _is_link(lock):
        body = read_lock(lock) or {}
        pid = int(body.get("pid", 0) or 0)
        if pid_alive(pid):
            return RecoveryOutcome(BLOCKED, BLOCKED_CONCURRENT_WRITER,
                                   (f"{lock.name} names pid {pid}, which is running",),
                                   root=str(root_path))
        try:
            lock.unlink()
            removed.append(lock.name)
            reasons.append(f"removed {lock.name}, whose pid {pid} is gone")
        except OSError as exc:
            return RecoveryOutcome(BLOCKED, BLOCKED_STALE_WRITER,
                                   (f"{lock.name} could not be removed: {exc}",),
                                   root=str(root_path))

    for staging in _staging_candidates(root_path):
        if staging.is_dir():
            _discard(staging)
            removed.append(staging.name)
            reasons.append(f"discarded staging directory {staging.name}: the root never "
                           f"contained it, so no install was half-written")

    in_root_lock = root_path / mod.LOCK_NAME
    if in_root_lock.exists() or _is_link(in_root_lock):
        repaired = _recover_upgrade(root_path, mod, in_root_lock,
                                    restored=restored, removed=removed,
                                    reasons=reasons)
        if repaired is not None:
            return repaired

    if not removed and not restored:
        return RecoveryOutcome(OK, NOTHING_TO_RECOVER,
                               ("no stale lock, no staging directory and no "
                                "interrupted upgrade to repair",), root=str(root_path))
    return RecoveryOutcome(OK, OK, tuple(reasons), tuple(restored), tuple(removed),
                           root=str(root_path))


def _recover_upgrade(root: Path, mod: Any, in_root_lock: Path, *,
                     restored: list[str], removed: list[str],
                     reasons: list[str]) -> RecoveryOutcome | None:
    """Repair the state an interrupted upgrade leaves, or refuse and say why."""
    receipt = mod.read_receipt(root)
    if receipt is None or getattr(receipt, "broken", False) or not receipt.is_intact():
        return RecoveryOutcome(
            BLOCKED, BLOCKED_RECEIPT_TAMPERED,
            (f"{mod.LOCK_NAME} is present and the receipt does not verify; an operator "
             f"has to read this root, not a repair script",), root=str(root))
    problems = mod._file_problems(root, receipt.files)
    if problems:
        backup = root / mod.BACKUP_DIR / receipt.version
        wanted = {record.path: (record.sha256, record.size) for record in receipt.files}
        present: dict[str, tuple[str, int] | None] = {}
        for path in wanted:
            body = _read_bytes(backup / path)
            present[path] = (_sha(body), len(body)) if body is not None else None
        if present != wanted:
            return RecoveryOutcome(
                BLOCKED, BLOCKED_BACKUP_MODIFIED,
                (f"the files do not match the receipt and {backup.name} does not hold "
                 f"the bytes that receipt recorded; restoring from it would write "
                 f"something no receipt names",), tuple(restored), tuple(removed),
                root=str(root))
        for path in sorted(wanted):
            body = _read_bytes(backup / path)
            assert body is not None
            _write_atomic(root / path, body)
            restored.append(path)
        _prune_backup(backup)
        reasons.append("an interrupted upgrade: the previous bytes were put back from "
                       f"{mod.BACKUP_DIR}/{receipt.version}, and the receipt they match "
                       f"is the one still in the root")
    marker = mod._read_marker(root)
    if marker is None:
        return RecoveryOutcome(
            BLOCKED, BLOCKED_OWNER_MARKER_MISSING,
            (f"{mod.LOCK_NAME} is present and {mod.OWNER_MARKER_NAME} is missing; an "
             f"ownership marker is evidence, and this module does not manufacture "
             f"evidence",), tuple(restored), tuple(removed), root=str(root))
    if (str(marker.get("receipt_digest", "")) != receipt.digest
            or str(marker.get("version", "")) != receipt.version):
        rebuilt = dict(marker)
        rebuilt.update({"schema": mod.MARKER_SCHEMA,
                        "install_root": receipt.install_root,
                        "device_ref": receipt.device_ref, "name": receipt.name,
                        "version": receipt.version,
                        "manifest_digest": receipt.manifest_digest,
                        "receipt_digest": receipt.digest})
        _write_atomic(root / mod.OWNER_MARKER_NAME, _json_bytes(rebuilt))
        restored.append(mod.OWNER_MARKER_NAME)
        reasons.append(f"an interrupted upgrade: {mod.OWNER_MARKER_NAME} named a receipt "
                       f"that is no longer in the root, so it was rebuilt from the "
                       f"receipt that is")
    if receipt.previous is None:
        # The receipt in the root is the one written *before* the upgrade, so the
        # backup taken for that upgrade is a copy of bytes already on disk: it
        # is named by no receipt, and the next upgrade would write into the same
        # directory. A backup is kept only while a receipt remembers it.
        orphan = root / mod.BACKUP_DIR / receipt.version
        if orphan.is_dir():
            _prune_backup(orphan)
            removed.append(f"{mod.BACKUP_DIR}/{receipt.version}")
            reasons.append(f"discarded {mod.BACKUP_DIR}/{receipt.version}: the receipt in "
                           f"the root is the pre-upgrade one, so no receipt names it")
    _unlink_quietly(in_root_lock)
    removed.append(mod.LOCK_NAME)
    reasons.append(f"removed {mod.LOCK_NAME}, left by an upgrade that did not finish")
    return RecoveryOutcome(OK, OK, tuple(reasons), tuple(restored), tuple(removed),
                           root=str(root))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part for part in (piece.strip() for piece in value.split(",")) if part)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    verb = parser.add_mutually_exclusive_group()
    verb.add_argument("--plan", action="store_true",
                      help="compute the install and report; writes nothing (default)")
    verb.add_argument("--apply", action="store_true",
                      help="write it: the only mode that touches the disk")
    verb.add_argument("--revoke", action="store_true",
                      help="revoke through sidecar_release.revoke() using the receipt "
                           "on disk; --ref must name the installed commit")
    verb.add_argument("--recover", action="store_true",
                      help="clear what a crashed run left, if it is unambiguous")
    verb.add_argument("--interface", action="store_true",
                      help="report which names this module needs from #506 and has")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--root", default="")
    parser.add_argument("--prefix", default="",
                        help="comma-separated allowed prefixes; the root must be one "
                             "level inside one of them and named 'sidecar'")
    parser.add_argument("--legacy-root", action="append", default=[])
    parser.add_argument("--device-ref", default="")
    parser.add_argument("--boot-id", default="")
    parser.add_argument("--backend-id", default="")
    parser.add_argument("--operator", default="")
    parser.add_argument("--grant-id", default="")
    parser.add_argument("--crash-at", default="",
                        help="death injection: staged-files|staged-marker|"
                             "staged-receipt|before-rename|after-backup|after-files|"
                             "after-marker|after-receipt")
    parser.add_argument("--fail-at", default="",
                        help="write-failure injection at the same points; the rollback "
                             "runs and the run reports BLOCKED_ABORTED")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.interface:
        report = interface_report()
        _emit(report)
        return 0 if report["compatible"] else 2

    if args.recover:
        if not args.root:
            parser.error("--recover needs --root")
        recovered = recover(args.root)
        _emit({"schema": SCHEMA, "verb": "recover", "decision": recovered.decision,
               "code": recovered.code, "reasons": list(recovered.reasons),
               "restored": list(recovered.restored),
               "removed": list(recovered.removed),
               "verification": dict(VERIFICATION)})
        return 0 if recovered.decision == OK else 2

    upstream = _upstream()
    identity = Identity(device_ref=args.device_ref, boot_id=args.boot_id,
                        next_backend_id=args.backend_id, operator=args.operator)
    if not args.root:
        parser.error("--root is required")
    descriptor, provenance = descriptor_for(args.repo, args.ref, version=args.version)

    if args.revoke:
        grant = upstream.Grant(grant_id=args.grant_id, operator=args.operator,
                               action="revoke", device_ref=args.device_ref)
        outcome = revoke_verified(descriptor, args.root, grant=grant,
                                  identity=identity,
                                  legacy_roots=[Path(p) for p in args.legacy_root])
        _emit({"schema": SCHEMA, "verb": "revoke", "decision": outcome.decision,
               "code": outcome.code, "reasons": list(outcome.reasons),
               "deleted": list(outcome.deleted), "root": outcome.root,
               "backend_id": outcome.backend_id,
               "verification": dict(VERIFICATION)})
        return 0 if outcome.decision == OK else 2

    grant = upstream.Grant(grant_id=args.grant_id, operator=args.operator,
                           action="install", device_ref=args.device_ref)
    outcome = install(descriptor, args.root, grant=grant, identity=identity,
                      allowed_prefixes=[Path(p) for p in _split_csv(args.prefix)],
                      provenance=provenance,
                      legacy_roots=[Path(p) for p in args.legacy_root],
                      apply=bool(args.apply), crash_at=args.crash_at,
                      fail_at=args.fail_at)
    _emit({"schema": SCHEMA, "verb": "apply" if args.apply else "plan",
           "descriptor": {"name": descriptor.name, "version": descriptor.version,
                          "commit": descriptor.commit,
                          "files": [{"path": path, "sha256": digest}
                                    for path, digest in descriptor.files],
                          "manifest_digest": descriptor.manifest_digest(),
                          "entrypoint": descriptor.entrypoint},
           **outcome.as_dict(), "verification": dict(VERIFICATION)})
    return 0 if outcome.decision in (OK, DRY_RUN) else 2


def _emit(payload: Mapping[str, Any]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
