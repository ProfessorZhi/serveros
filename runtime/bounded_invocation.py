"""POSIX bounded invocation with a durable idempotency receipt; not a scheduler.

Only a trusted, configured adapter supplies argv and this private directory.
This helper neither authenticates a grant nor sandboxes commands. Direct-process
exit is deliberately not a claim about business results or descendant termination.

Why the receipt lock REFUSES instead of waiting
-----------------------------------------------
One action key is one receipt, so the read of the receipt and the write of it
have to be one step, and a lock is how that is done. That lock is held from
before `Popen` until after the group is reaped — the whole life of the command.
A caller that BLOCKED on it would therefore be waiting for another dispatch's
command to finish, up to its full `wall_seconds`, and would then be told what
the receipt's own digest check would have said at once. On a host where several
Items dispatch concurrently, that is one scope's work freezing another's for no
answer, which J07's fifth criterion names: 一项等待不冻其他合法 scope. So the
lock is taken non-blocking and contention comes back as a `ValueError` — an
immediate, and truthful, refusal to start anything.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _store_io():
    """`_atomic_write` and `_exclusive`, loaded by path rather than imported.

    This line read `from .agent_control_store import …` until 2026-09-22, and
    the artifact is not a set of packages: `bin/serverosctl` puts `runtime/` on
    `sys.path` and imports by BARE name, so there is no parent package for a
    relative import to be relative to. In the install layout the module raised
    `ImportError` the moment anything loaded it — measured: the seventh failing
    test of the suite, and the reason `invoke_once` had no consumer anywhere in
    the fabric. Under pytest, which imports `runtime.bounded_invocation` as a
    namespace package, the same line worked, so the defect was invisible from
    the only file that imported the module at all.

    `runtime/alerting.py` and `runtime/alert_digest.py` load this same store the
    same way, for the same reason; this is that idiom rather than a fourth
    variant of it.
    """
    return _load("serveros_runtime_agent_control_store",
                 _HERE / "agent_control_store.py")


@contextmanager
def _receipt_lock(lock: Path):
    """This action key's receipt lock, refused rather than waited for.

    The translation lives here rather than in `invoke_once` so that the
    `ValueError` contract is one place: every `ValueError` this module raises
    is on the NEAR side of `Popen`, which is what `serverosctl work dispatch`
    tells a person when it prints the message —「nothing was started」— and a
    lock refusal raised after the spawn would make that sentence false. It is
    raised from the acquisition, so nothing was written here either: no
    `STARTED` receipt, no stdin/stdout/stderr files. The only file this path
    touches is the lock sidecar, which already existed and is a kernel lock
    rather than a record.

    Why the caller is told to retry rather than to re-key: a retry is safe and
    is the right move — if that dispatch finished with the same input, the next
    call REPLAYS its receipt instead of executing again, and the phase records
    from the same file. Deriving a second key to get around this would be the
    one thing the receipt cannot protect against: two keys mean two receipts,
    two executions of one command.
    """
    store = _store_io()
    try:
        with store._exclusive_nowait(lock):
            yield
    except store.LockUnavailable as exc:
        raise ValueError(
            f"another dispatch under this action key is still running: {exc}. "
            f"This is not a queue and it does not wait: that lock is held for "
            f"the whole life of the command it guards, so waiting would have "
            f"blocked this call for another dispatch's full wall budget only to "
            f"receive the same answer. Nothing was run and nothing was written. "
            f"Retry when that dispatch finishes — the same input replays its "
            f"receipt rather than executing again, and if the input differs the "
            f"refusal you get then is the one that refusal is for"
        ) from None


def invoke_once(*, key: str, argv: list[str], cwd: Path, receipts: Path,
                input_text: str = "", timeout: int = 120, readonly: bool = True,
                max_output_bytes: int = 2_000_000, env: dict | None = None) -> dict:
    """`env` IS THE CALLER'S, AND IT IS ADDED TO THIS PROCESS'S ENVIRONMENT.

    Added and not substituted, because this helper does not know what a child
    needs to find — `PATH`, `HOME` and the state root are all the ambient
    environment's business. What a caller names here is therefore exactly the
    extra, and nothing else changes.

    It is part of the input digest, and that is the point: a second call under
    the same key that hands the child a different environment is a different
    dispatch, and this helper's rule is that one key carries one input or
    refuses. That puts a requirement on the caller rather than on this helper —
    a value that varies run to run (a timestamp, a fresh temporary directory)
    makes every replay a refusal. Name something derived from the key, and the
    digest stays put across the replays it exists to serve.
    """
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", key):
        raise ValueError("safe action key required")
    if not argv or any(not isinstance(a, str) or not a or "\x00" in a for a in argv):
        raise ValueError("explicit nonempty argv required")
    if not cwd.is_absolute() or not receipts.is_absolute():
        raise ValueError("absolute caller-resolved roots required")
    if not cwd.is_dir() or receipts.is_symlink() or type(timeout) is not int or timeout <= 0:
        raise ValueError("valid cwd, owned receipt root and positive timeout required")
    if type(max_output_bytes) is not int or max_output_bytes <= 0:
        raise ValueError("positive per-stream output bound required")
    # A malformed environment is refused HERE rather than at `Popen`, because
    # `Popen` would take it and fail as an `OSError` after the receipt had been
    # written — turning a caller's typo into a dispatch that started nothing and
    # is recorded as one that might have.
    #
    # The name pattern is the portable one, and it is deliberately narrower than
    # what `execve` accepts. `execve` takes any string without `=`, so a name
    # like `MY TOKEN` would be handed to the child and be addressable by nothing
    # — no shell can expand it, and the caller asking for it meant `MY_TOKEN`.
    # Refusing it turns a silent no-op into a message. A caller with a genuine
    # unaddressable name (a bash function export, `BASH_FUNC_f%%`) is not served
    # by this helper and should say so rather than be quietly accommodated.
    if env is not None and (not isinstance(env, dict) or any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) if isinstance(k, str)
            else True for k in env) or any(
            not isinstance(v, str) or "\x00" in v for v in env.values())):
        raise ValueError(
            "env must map portable names ([A-Za-z_][A-Za-z0-9_]*) to string "
            "values with no NUL; a name nothing can address is a typo, not a "
            "setting")
    receipts.mkdir(parents=True, exist_ok=True, mode=0o700)
    if receipts.stat().st_mode & 0o077:
        raise ValueError("receipt directory must be private (0700)")
    digest = hashlib.sha256(json.dumps({"argv": argv, "cwd": str(cwd.resolve()),
                "input": input_text, "readonly": readonly, "timeout": timeout,
                "max_output_bytes": max_output_bytes,
                "env": env or {}}, sort_keys=True).encode()).hexdigest()
    path = receipts / (key + ".json")
    with _receipt_lock(receipts / (key + ".lock")):
        if path.is_symlink():
            raise ValueError("symlink receipt refused")
        if path.exists():
            old = json.loads(path.read_text())
            if old.get("input_digest") != digest:
                raise ValueError("same action key with different input")
            return {**old, "replayed": True, "executed_now": False}
        record = {"action_key": key, "input_digest": digest, "status": "STARTED",
                  "started_at": datetime.now(timezone.utc).isoformat(),
                  "read_only_requested": readonly, "process_exit_code": None,
                  "effect": "UNKNOWN", "worker_stopped": False,
                  "direct_process_exited": False, "descendants_verified": False}
        _store_io()._atomic_write(path, record)  # Crash here requires reconciliation, never blind replay.
        paths = {k: receipts / (key + "." + k) for k in ("stdin", "stdout", "stderr")}
        if any(p.exists() or p.is_symlink() for p in paths.values()):
            record.update(status="REFUSED", reason="OUTPUT_ALREADY_EXISTS")
            _store_io()._atomic_write(path, record)
            return {**record, "replayed": False, "executed_now": False}
        def private_file(p):
            return os.fdopen(os.open(p, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600), "w+b")
        proc = None
        with private_file(paths["stdin"]) as source, private_file(paths["stdout"]) as out, private_file(paths["stderr"]) as err:
            # Regular stdin avoids deadlocking while writing a large prompt into a pipe.
            source.write(input_text.encode()); source.flush(); source.seek(0)
            streams = {"stdout": out, "stderr": err}
            try:
                proc = subprocess.Popen(argv, cwd=cwd, stdin=source, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, start_new_session=True,
                                        env={**os.environ, **(env or {})})
                deadline = time.monotonic() + timeout
                sizes = {"stdout": 0, "stderr": 0}
                reason = None
                with selectors.DefaultSelector() as selector:
                    for name in streams:
                        selector.register(getattr(proc, name), selectors.EVENT_READ, name)
                    while selector.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            reason = "TIMED_OUT"; break
                        for event, _ in selector.select(min(remaining, 0.1)):
                            data = os.read(event.fileobj.fileno(), 65536)
                            if not data:
                                selector.unregister(event.fileobj); continue
                            name = event.data
                            available = max_output_bytes - sizes[name]
                            kept = data[:available]
                            streams[name].write(kept); sizes[name] += len(kept)
                            if len(data) > available:
                                reason = "OUTPUT_LIMIT"; break
                        if reason:
                            break
                if reason is None:
                    try:
                        proc.wait(timeout=max(0.001, deadline-time.monotonic()))
                    except subprocess.TimeoutExpired:
                        reason = "TIMED_OUT"
                if reason:
                    try: os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                    try: proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        try: os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                        proc.wait(timeout=3)
                    record.update(status=reason, effect="UNKNOWN",
                                  reason="requires effect/worker reconciliation; no automatic retry")
                else:
                    record.update(status="EXITED", effect="UNVERIFIED")
                record.update(process_exit_code=proc.returncode, direct_process_exited=True)
            except OSError as exc:
                record.update(status="START_FAILED" if proc is None else "IO_FAILED",
                              error_type=type(exc).__name__)
                if proc is None:
                    record.update(worker_stopped=True, effect="NO_PROCESS_STARTED")
            finally:
                if proc is not None:
                    if proc.poll() is None:
                        try: os.killpg(proc.pid, signal.SIGTERM)
                        except ProcessLookupError: pass
                    for name in streams:
                        getattr(proc, name).close()
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        record["output_sha256"] = {k:hashlib.sha256(paths[k].read_bytes()).hexdigest() for k in ("stdout", "stderr")}
        record["executed_now"] = True
        _store_io()._atomic_write(path, record)
        # The pair every return carries, so a caller reads one shape. A first
        # call and a replay are the two cases an idempotency check exists to
        # tell apart, and a caller that has to use `result.get("replayed")`
        # because the first call omits the key is a caller that will forget.
        return {**record, "replayed": False}
