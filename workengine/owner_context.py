"""Measure a repository for an existing, explicitly bound continuation wake.

Location/configuration is supplied by the outer owner assembly. The original
wake envelope is read, never reminted: its wall expiry must not move on attach.
Stored Git facts are context metadata, not signed execution evidence.
"""
import datetime
import hashlib
import stat
import time
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from .model import ContextMode, ExecutionContext
from .owner_artifacts import real_path


def _runtime(name):
    key = "serveros_runtime_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).resolve().parents[1] / "runtime" / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def execution_context(store, request, data, grant):
    import yaml
    workspace = getattr(store, "owner_workspace_root", None)
    receipts = getattr(store, "owner_continuation_receipts_root", None)
    if workspace is None or receipts is None:
        raise ValueError("OWNER_CONTEXT_BINDING_REQUIRED: explicit workspace and continuation receipt roots required")
    workspace, receipts = real_path(workspace), real_path(receipts)
    if not workspace.is_dir() or not receipts.is_dir():
        raise ValueError("OWNER_CONTEXT_BINDING_REQUIRED: configured roots must exist")
    if receipts.stat().st_uid != os.getuid() or receipts.stat().st_mode & 0o077:
        raise ValueError("OWNER_ENVELOPE_MISMATCH: receipt root must be private to owner")
    catalog = real_path(workspace / "ops/workspace/serveros/projects.yaml")
    config = (yaml.safe_load(catalog.read_text(encoding="utf-8")) or {}).get("projects", {}).get(request["project"])
    if not isinstance(config, dict):
        raise ValueError("OWNER_REPOSITORY_MISMATCH: project missing from authored catalog")
    repo = request["repo"]
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", repo):
        raise ValueError("OWNER_REPOSITORY_MISMATCH: exact repository identifier required")
    allowed = set(config.get("canonical_repos") or ())
    if config.get("worktrees_under"): allowed.add(config["worktrees_under"])
    if repo not in allowed:
        raise ValueError("OWNER_REPOSITORY_MISMATCH: repository outside project catalog")
    worktree = real_path(workspace / "worktrees" / repo / request["item_ref"])
    checkpoint = data.get("checkpoints", {}).get(request["checkpoint_ref"])
    if not isinstance(checkpoint, dict) or checkpoint.get("work_item") != request["item_ref"]:
        raise ValueError("OWNER_CHECKPOINT_MISMATCH: exact current checkpoint required")
    if checkpoint.get("superseded_by") or (checkpoint.get("parked_at") and not checkpoint.get("unparked_at")):
        raise ValueError("OWNER_CHECKPOINT_MISMATCH: checkpoint is not current")
    now = datetime.datetime.fromtimestamp(store.clock(), datetime.timezone.utc)
    wake = _runtime("wake")
    spend = wake.spending(grant.get("budget"), item=request["item_ref"], grant_ref=request["grant_ref"])
    if spend.get("reason"):
        raise ValueError("OWNER_ENVELOPE_MISMATCH: current wake spending is not authorized")
    candidates = []
    files = list(receipts.glob("*.envelope.json"))
    if len(files) > 4096:
        raise ValueError("OWNER_ENVELOPE_MISMATCH: bounded envelope catalog exceeded")
    for path in files:
        real_path(path)
        if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
            raise ValueError("OWNER_ENVELOPE_MISMATCH: envelope must be private to owner")
        if path.stat().st_size > 65536:
            raise ValueError("OWNER_ENVELOPE_MISMATCH: oversized envelope")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict):
            raise ValueError("OWNER_ENVELOPE_MISMATCH: malformed envelope")
        if envelope.get("checkpoint_ref") != request["checkpoint_ref"] or envelope.get("grant_ref") != request["grant_ref"]:
            continue
        expires = datetime.datetime.fromisoformat(str(envelope.get("expires_at")))
        issued = datetime.datetime.fromisoformat(str(envelope.get("issued_at")))
        if expires.tzinfo is None or issued.tzinfo is None or not issued <= now < expires:
            continue
        written = datetime.datetime.fromisoformat(str(checkpoint.get("written_at")))
        if written.tzinfo is None or issued < written:
            raise ValueError("OWNER_ENVELOPE_MISMATCH: envelope predates checkpoint")
        wait = _runtime("continuation").wait_state(checkpoint, admissions=data.get("admissions", {}), now=envelope["issued_at"])
        if wait.get("state") != "WAIT_OVER":
            raise ValueError("OWNER_ENVELOPE_MISMATCH: original wake prerequisite not established")
        attempt = envelope.get("attempt")
        if type(attempt) is not int or not 1 <= attempt <= spend["attempts_allowed"]:
            raise ValueError("OWNER_ENVELOPE_MISMATCH: invalid wake attempt")
        key = wake.action_key(request["item_ref"], checkpoint.get("epoch"), attempt)
        if path != wake.envelope_path(receipts, key):
            raise ValueError("OWNER_ENVELOPE_MISMATCH: envelope filename differs from producer key")
        expected = wake.capability_envelope(checkpoint, grant, request["grant_ref"], attempt=attempt,
            key=key, spend=spend, now=envelope["issued_at"])
        if envelope != expected:
            raise ValueError("OWNER_ENVELOPE_MISMATCH: original producer derivation/current binding differs")
        candidates.append(envelope)
    if len(candidates) != 1:
        raise ValueError("OWNER_ENVELOPE_MISMATCH: exactly one current existing wake envelope required")
    resolved = _runtime("bounded_executor").binding(envelope=candidates[0], checkpoint=checkpoint,
        grant=grant, worktrees_root=workspace / "worktrees")
    if Path(resolved["worktree"]) != worktree:
        raise ValueError("OWNER_REPOSITORY_MISMATCH: signed worktree differs from project layout")
    measured = measure_git(worktree, getattr(store, "owner_git_executable", None))
    revision, dirty = measured["revision"], measured["dirty"]
    for key, actual in (("expected_revision", revision), ("expected_worktree", str(worktree)), ("expected_dirty", dirty)):
        if key in request and request[key] != actual:
            raise ValueError("OWNER_REPOSITORY_MISMATCH: caller expectation differs from measured Git facts")
    return ExecutionContext(request["item_ref"], request["activity_ref"], repo, ContextMode(request["mode"]), revision, str(worktree), dirty)


def measure_git(worktree, executable):
    """Bounded helper-free plumbing for ordinary, unfiltered tracked files.

    Git never reads worktree content through status/diff conversion. Only raw
    local file bytes are hashed in-process; executable/conversion configuration
    is also refused for honest dirty semantics. Mutable config cannot turn this
    content check into a filter invocation. This is not a full Git converter.
    """
    worktree = real_path(worktree)
    if executable is None or not Path(executable).is_absolute() or not Path(executable).is_file():
        raise ValueError("OWNER_GIT_BINDING_REQUIRED: explicit absolute Git executable required")
    executable = real_path(executable)
    deadline = time.monotonic() + 30
    env = {"PATH": str(executable.parent), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0",
           "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": "", "GIT_TERMINAL_PROMPT": "0"}
    if "SYSTEMROOT" in os.environ: env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]

    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise ValueError("OWNER_REPOSITORY_BOUND: measurement deadline exhausted")
        return min(left, 5)

    def git(*args, allowed=(0,), input_data=None):
        try:
            result = subprocess.run([str(executable), "--no-replace-objects", "-c", "core.fsmonitor=false",
                                    "-c", "status.submoduleSummary=false", "-C", str(worktree), *args],
                                    capture_output=True, text=True, encoding="utf-8", timeout=remaining(),
                                    env=env, input=input_data)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("OWNER_REPOSITORY_BOUND: plumbing command timed out") from exc
        if result.returncode not in allowed or len(result.stdout) > 4 * 1024 * 1024:
            raise ValueError("OWNER_REPOSITORY_MISMATCH: bounded local plumbing unavailable")
        return result.stdout

    def configuration():
        # Names only: never return/log configured helper/credential values.
        keys = git("config", "--includes", "--null", "--name-only", "--list").split("\0")
        for key in keys:
            key = key.lower()
            if (key.startswith("filter.") or key == "diff.external"
                    or (key.startswith("diff.") and key.endswith((".command", ".textconv")))
                    or key == "core.alternaterefscommand" or key == "extensions.partialclone"
                    or (key.startswith("remote.") and key.endswith((".promisor", ".partialclonefilter")))):
                raise ValueError("OWNER_REPOSITORY_HELPER_REFUSED: executable conversion or lazy object configuration")
        autocrlf = git("config", "--get", "core.autocrlf", allowed=(0, 1)).strip().lower()
        eol = git("config", "--get", "core.eol", allowed=(0, 1)).strip().lower()
        if autocrlf not in {"", "false", "no", "off", "0", "true", "yes", "on", "1", "input"} or eol not in {"", "lf", "crlf", "native"}:
            raise ValueError("OWNER_REPOSITORY_UNSUPPORTED: unknown configured EOL conversion")
        filemode = git("config", "--bool", "--get", "core.filemode", allowed=(0, 1)).strip()
        return keys, autocrlf, eol, filemode

    configured = configuration()
    if Path(git("rev-parse", "--show-toplevel").strip()).resolve() != worktree:
        raise ValueError("OWNER_REPOSITORY_MISMATCH: Git top-level differs from worktree")
    revision = git("rev-parse", "--verify", "HEAD").strip()
    algorithm = git("rev-parse", "--show-object-format").strip()
    if algorithm not in {"sha1", "sha256"}:
        raise ValueError("OWNER_REPOSITORY_UNSUPPORTED: unknown Git object format")
    indexed = git("ls-files", "--stage", "-z")
    entries = []
    for line in indexed.split("\0"):
        if not line: continue
        metadata, name = line.split("\t", 1)
        mode, digest, stage = metadata.split()
        if mode not in {"100644", "100755"} or stage not in {"0", "1", "2", "3"}:
            raise ValueError("OWNER_REPOSITORY_UNSUPPORTED: submodules/symlinks/index modes are not raw regular files")
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
            raise ValueError("OWNER_REPOSITORY_UNSUPPORTED: unsafe index path")
        entries.append((mode, digest, stage, name))
    if len(entries) > 10000:
        raise ValueError("OWNER_REPOSITORY_BOUND: too many tracked entries")
    names = "\0".join(entry[3] for entry in entries) + ("\0" if entries else "")

    def attributes():
        value = git("check-attr", "--all", "-z", "--stdin", input_data=names) if entries else ""
        fields = value.split("\0")[:-1]
        if len(fields) % 3:
            raise ValueError("OWNER_REPOSITORY_UNSUPPORTED: malformed attributes")
        by_path = {}
        for offset in range(0, len(fields), 3):
            name, attribute, setting = fields[offset:offset + 3]
            by_path.setdefault(name, {})[attribute] = setting
            if attribute in {"filter", "ident", "working-tree-encoding"} and setting not in {"unset", "unspecified"}:
                raise ValueError("OWNER_REPOSITORY_UNSUPPORTED: raw measurement cannot apply content conversion")
        eol_paths = set()
        configured_eol = configured[1] not in {"", "false", "no", "off", "0"} or bool(configured[2])
        for _, _, _, name in entries:
            attributes = by_path.get(name, {})
            if attributes.get("text") == "unset": continue
            if (configured_eol or attributes.get("text") not in {None, "unset", "unspecified"}
                    or attributes.get("eol") not in {None, "unset", "unspecified"}):
                eol_paths.add(name)
        return value, eol_paths

    attr_snapshot = attributes()
    # Cached comparison reads index/object metadata only. Explicit no-ext-diff
    # and no-textconv prevent diff drivers even if config changes mid-call.
    dirty = bool(git("diff-index", "--cached", "--raw", "--no-ext-diff", "--no-textconv", "-z", "HEAD", "--"))
    dirty |= bool(git("ls-files", "--others", "--exclude-standard", "-z"))
    total = 0
    for mode, expected, stage, name in entries:
        remaining()
        if stage != "0":
            dirty = True
            continue
        path = real_path(worktree / name)
        try:
            before = path.lstat()
        except FileNotFoundError:
            dirty = True
            continue
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("OWNER_REPOSITORY_UNSUPPORTED: tracked worktree entry is not a regular file")
        if configured[3] != "false" and bool(before.st_mode & stat.S_IXUSR) != (mode == "100755"):
            dirty = True
        total += before.st_size
        if total > 128 * 1024 * 1024:
            raise ValueError("OWNER_REPOSITORY_BOUND: tracked content exceeds 128 MiB")
        digest = hashlib.new(algorithm)
        digest.update(("blob " + str(before.st_size) + "\0").encode("ascii"))
        counted = 0
        previous_byte, has_crlf = b"", False
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
                raise ValueError("OWNER_REPOSITORY_MISMATCH: tracked file replaced while opening")
            while chunk := handle.read(1024 * 1024):
                remaining()
                counted += len(chunk)
                if counted > before.st_size:
                    raise ValueError("OWNER_REPOSITORY_MISMATCH: tracked file grew during measurement")
                has_crlf |= b"\r\n" in previous_byte + chunk
                previous_byte = chunk[-1:]
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        current = path.lstat()
        # Compare handle timestamps with handle timestamps. Windows Python can
        # expose creation time through lstat().st_ctime but last-write time via
        # fstat().st_ctime, so crossing those APIs invents a content change.
        if (counted != before.st_size
                or (opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns)
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)):
            raise ValueError("OWNER_REPOSITORY_MISMATCH: tracked file changed during measurement")
        # Declared text/eol=lf with already-LF bytes is an identity transform,
        # including this repository's portability JSON fixtures. No converter
        # is needed. CRLF conversion, encoding and ident expansion stay outside
        # this bounded raw reader instead of being guessed or executed.
        if name in attr_snapshot[1] and has_crlf:
            raise ValueError("OWNER_REPOSITORY_UNSUPPORTED: nonidentity EOL conversion requires a supported reader")
        dirty |= digest.hexdigest() != expected
    if (configuration() != configured or attributes() != attr_snapshot
            or git("ls-files", "--stage", "-z") != indexed
            or git("rev-parse", "--verify", "HEAD").strip() != revision):
        raise ValueError("OWNER_REPOSITORY_MISMATCH: repository metadata changed during measurement")
    return {"revision": revision, "dirty": dirty}
