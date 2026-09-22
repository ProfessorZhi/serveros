"""POSIX bounded invocation with a durable idempotency receipt; not a scheduler.

Only a trusted, configured adapter supplies argv and this private directory.
This helper neither authenticates a grant nor sandboxes commands. Direct-process
exit is deliberately not a claim about business results or descendant termination.
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


def invoke_once(*, key: str, argv: list[str], cwd: Path, receipts: Path,
                input_text: str = "", timeout: int = 120, readonly: bool = True,
                max_output_bytes: int = 2_000_000) -> dict:
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
    receipts.mkdir(parents=True, exist_ok=True, mode=0o700)
    if receipts.stat().st_mode & 0o077:
        raise ValueError("receipt directory must be private (0700)")
    digest = hashlib.sha256(json.dumps({"argv": argv, "cwd": str(cwd.resolve()),
                "input": input_text, "readonly": readonly, "timeout": timeout,
                "max_output_bytes": max_output_bytes}, sort_keys=True).encode()).hexdigest()
    path = receipts / (key + ".json")
    with _store_io()._exclusive(receipts / (key + ".lock")):
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
                                        stderr=subprocess.PIPE, start_new_session=True)
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
