#!/usr/bin/env python3
"""Tokyo-local Fleet Next Goal Supervisor.

WHY THIS FILE EXISTS
--------------------
The owner's 2026-10-29 instruction moved long-horizon supervision off ChatGPT's
scheduled tasks and onto this machine.  Nothing in Claude Code 2.1.308 is called
"Goal Mode"; what the runtime actually offers is a background session primitive
(``claude --bg`` / ``claude agents --json``).  This module is the *fallback*
supervisor that the instruction explicitly permits when no official mode exists:
a user-scope, single-instance, crash-recoverable tick loop.

WHAT IT IS NOT
--------------
It is not the product's Agent Monitor.  The Agent Monitor observes agents and
delivers receipts; this process decides *when a human-owned goal needs the next
independent task*, and stops deciding when it cannot prove the facts.

Three rules are structural, not conventions:

1. **One supervisor.**  ``flock`` on ``goal.lock``.  A stale PID file is not a
   lock; the kernel releases the lock when the holder dies.
2. **No fabricated progress.**  A source that did not answer is ``UNKNOWN``.
   A dispatch whose outcome was never read stays ``DISPATCH_ACKED_UNOBSERVED``
   and is never re-sent.  A receipt carries only ids that were observed.
3. **Never message a running agent.**  ``RUNNING_AGENT`` sessions are recorded
   as refused targets.  Only ``MANAGED_IDLE`` / ``RESIDENT_HOST`` may be offered,
   and only when dispatch is both enabled *and* a verified provider is named.

Everything below is stdlib-only so the daemon can run without the repo's venv.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA = "serveros/fleet-next-local-goal-supervisor.v1"
RECEIPT_SCHEMA = "serveros/fleet-next-local-goal-supervisor.receipt.v1"
HEARTBEAT_SCHEMA = "serveros/fleet-next-local-goal-supervisor.heartbeat.v1"
CONFIG_SCHEMA = "serveros/fleet-next-local-goal-supervisor.config.v1"
DEFAULT_GOAL_ID = "fleet-next-v1-long-horizon-closure"

ACTIVE_LEDGER_STATUSES = {"DISPATCHED", "RUNNING", "DISPATCH_ACKED_UNOBSERVED"}
DISPATCHABLE_CLASSES = {"MANAGED_IDLE", "RESIDENT_HOST"}


class PathEscape(Exception):
    """A path a producer handed us resolved outside the root it was given."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _now_epoch() -> int:
    return int(time.time())


def kernel_boot_epoch() -> str:
    """The kernel's boot identity: a reboot changes it, a restart does not.

    Named ``kernel_boot_epoch`` rather than ``boot_epoch`` because ``tick``
    takes a ``boot_epoch`` argument, and a function and a parameter with the
    same name in one scope is how a live tick called ``None``.
    """
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return "unknown"


def confine(root: str | os.PathLike[str], target: str | os.PathLike[str]) -> Path:
    """Resolve *target* and refuse anything that escapes *root*.

    The producer is passed an absolute path, but an absolute path is not a
    boundary.  Symlinks and ``..`` are how evidence lands in another owner's
    directory, so the check happens on the resolved path.
    """
    root_p = Path(root).resolve()
    target_p = Path(target).resolve()
    if target_p != root_p and root_p not in target_p.parents:
        raise PathEscape(f"{target_p} is outside {root_p}")
    return target_p


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def default_config() -> dict[str, Any]:
    """Safe defaults: observe, do not dispatch, shell out to nothing.

    ``dispatch.enabled`` is false and ``dispatch.provider`` is null on purpose.
    Turning the supervisor into something that creates agents is an explicit
    operator act, not a side effect of installing it.
    """
    return {
        "schema": CONFIG_SCHEMA,
        "goal": {
            "id": DEFAULT_GOAL_ID,
            "title": "Fleet Next V1 Long-Horizon Closure",
        },
        "dispatch": {
            "enabled": False,
            "provider": None,
            "max_inflight": 3,
        },
        "poll": {"base_seconds": 60, "cap_seconds": 1800, "jitter": 0.1},
        "sources": {
            "github_repo": "ProfessorZhi/serveros",
            "fleet_snapshot": "",
            # Off by default: a test or a dry run must not invoke the CLI just
            # because default_config() was used.
            "local_agents": False,
        },
        "watch": {
            "issues": [478, 425, 424, 434],
            "prs": [507, 508, 509],
        },
        "paths": {"stop_file": "STOP"},
    }


def load_config(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    cfg = default_config()
    if not path:
        return cfg
    p = Path(path)
    if not p.exists():
        return cfg
    raw = json.loads(p.read_text())
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return cfg


# ---------------------------------------------------------------------------
# Single instance
# ---------------------------------------------------------------------------


class SingleInstanceLock:
    """An ``flock``-backed single-instance guard.

    The file carries pid/boot metadata for a reader, but the metadata is not
    the lock: ``flock`` is.  A killed process leaves its JSON behind and still
    does not block the next holder, which is the recovery property we want.
    """

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._fh = None

    @property
    def held(self) -> bool:
        return self._fh is not None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "boot_epoch": kernel_boot_epoch(),
                    "acquired_at": _now_iso(),
                }
            )
            + "\n"
        )
        fh.flush()
        os.fsync(fh.fileno())
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "SingleInstanceLock":
        if not self.acquire():
            raise RuntimeError(f"lock already held: {self.path}")
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


# ---------------------------------------------------------------------------
# Persistent state
# ---------------------------------------------------------------------------


def new_state(goal_id: str, boot_epoch: str) -> dict[str, Any]:
    now = _now_iso()
    return {
        "schema": SCHEMA,
        "goal_id": goal_id,
        "boot_epoch": boot_epoch,
        "resume_count": 0,
        "created_at": now,
        "updated_at": now,
        "cursor": {
            "github_head": None,
            "last_tick_at": None,
            "last_success_at": None,
            "source_state": {},
        },
        "dispatch_ledger": {},
        "counters": {"ticks": 0, "dispatches": 0},
        "last_decision": None,
    }


def resume_state(state: dict[str, Any], boot_epoch: str) -> dict[str, Any]:
    """Carry a persisted state across a process restart or a reboot.

    A different boot id means the machine restarted; everything else is the
    same goal, so the cursor and the ledger survive.  A dispatch recorded as
    acknowledged-but-unobserved stays that way — restarting is not evidence
    that the Run finished, and it is not permission to send it again.
    """
    resumed = copy.deepcopy(state)
    if resumed.get("boot_epoch") != boot_epoch:
        resumed["boot_epoch"] = boot_epoch
        resumed["resume_count"] = int(resumed.get("resume_count", 0)) + 1
    resumed.setdefault("dispatch_ledger", {})
    resumed.setdefault("cursor", {})
    return resumed


class StateStore:
    def __init__(self, state_dir: str | os.PathLike[str]):
        self.dir = Path(state_dir)
        self.path = self.dir / "goal.json"

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return new_state(DEFAULT_GOAL_ID, kernel_boot_epoch())
        return json.loads(self.path.read_text())

    def save(self, state: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        with open(tmp, "r+", encoding="utf-8") as fh:
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)


def record_dispatch(
    state: dict[str, Any], action: dict[str, Any], now: str, outcome: str
) -> None:
    """Write the ledger entry *before* control leaves this process.

    ``DISPATCHED`` means the provider accepted the request.  It does not mean
    the task ran, and ``run_id`` stays null until a read proves one.
    """
    key = action["task_key"]
    state.setdefault("dispatch_ledger", {})[key] = {
        "status": outcome,
        "session_ref": action.get("session_ref"),
        "run_id": action.get("run_id"),
        "dispatched_at": now,
        "provider": action.get("provider"),
    }
    state.setdefault("counters", {}).setdefault("dispatches", 0)
    state["counters"]["dispatches"] += 1


# ---------------------------------------------------------------------------
# Sources — each answers for itself; none may answer for another
# ---------------------------------------------------------------------------


def _subprocess_runner(argv: list[str], cwd: str | None = None, timeout: int = 45) -> str:
    proc = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{' '.join(argv[:3])} exited {proc.returncode}: {proc.stderr.strip()[:300]}"
        )
    return proc.stdout.strip()


def observe_github(
    repo: str,
    cwd: str | None = None,
    runner: Callable[[list[str]], str] | None = None,
    prs: list[int] | None = None,
    issues: list[int] | None = None,
) -> dict[str, Any]:
    """Read HEAD, open PRs and watched issues through ``git``/``gh``.

    The returned ids are the ones the command printed.  Nothing is defaulted to
    a plausible value: if ``gh`` did not answer, ``state`` is ``UNKNOWN`` and
    the error is carried, not swallowed.
    """
    run = runner if runner is not None else (lambda av: _subprocess_runner(av, cwd=cwd))
    out: dict[str, Any] = {
        "state": "OK",
        "head_sha": None,
        "head_source": None,
        "open_prs": [],
        "issues": {},
        "error": None,
    }
    # The repository's HEAD is what GitHub says it is, not what this host's
    # checkout happens to have fetched.  On 2026-10-29 the running supervisor
    # reported e69fc03 for eight hours while main had moved to 44f039d: a
    # local `git rev-parse` is a clock, not a fact about the remote.
    try:
        sha = run(["gh", "api", f"repos/{repo}/commits/main", "--jq", ".sha"])
        if not sha:
            raise RuntimeError("empty sha from gh api")
        out["head_sha"] = sha
        out["head_source"] = "remote"
    except Exception as exc:  # a source adapter is untrusted by construction
        out["state"] = "UNKNOWN"
        out["error"] = f"github.head: {exc}"

    try:
        raw = run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--limit",
                "200",
                "--json",
                "number,title,headRefName,isDraft,headRefOid,updatedAt",
            ]
        )
        parsed = json.loads(raw or "[]")
        out["open_prs"] = [
            {
                "number": item.get("number"),
                "title": item.get("title"),
                "head_ref": item.get("headRefName"),
                "is_draft": item.get("isDraft"),
                "head_sha": item.get("headRefOid"),
                "updated_at": item.get("updatedAt"),
            }
            for item in parsed
        ]
    except Exception as exc:
        out["state"] = "UNKNOWN"
        out["error"] = (out["error"] + "; " if out["error"] else "") + f"github.prs: {exc}"

    for number in issues or []:
        try:
            raw = run(
                [
                    "gh",
                    "issue",
                    "view",
                    str(number),
                    "--repo",
                    repo,
                    "--json",
                    "number,state,title,updatedAt",
                ]
            )
            out["issues"][str(number)] = json.loads(raw or "{}")
        except Exception as exc:
            out["state"] = "UNKNOWN"
            out["error"] = (
                out["error"] + "; " if out["error"] else ""
            ) + f"github.issue.{number}: {exc}"

    return out


def observe_ci(
    repo: str,
    prs: list[int] | None = None,
    runner: Callable[[list[str]], str] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Read exact-head check runs per watched PR.

    ``NO_CHECKS`` and ``UNKNOWN`` are deliberately distinct from ``PASS``: a PR
    whose checks have not started has not passed them, and a command that did
    not answer has not established anything.  ``gh pr checks --json`` exits 0
    even when a check failed, so a failure is classified here from the bucket.
    """
    run = runner if runner is not None else (lambda av: _subprocess_runner(av, cwd=cwd))
    out: dict[str, Any] = {"state": "OK", "prs": {}, "error": None}
    for number in prs or []:
        try:
            raw = run(
                [
                    "gh",
                    "pr",
                    "checks",
                    str(number),
                    "--repo",
                    repo,
                    "--json",
                    "name,state,bucket,link",
                ]
            )
            rows = json.loads(raw or "[]")
            failing = [r for r in rows if r.get("bucket") == "fail"]
            pending = [r for r in rows if r.get("bucket") == "pending"]
            if not rows:
                verdict = "UNKNOWN"
            elif failing:
                verdict = "FAIL"
            elif pending:
                verdict = "PENDING"
            else:
                verdict = "PASS"
            out["prs"][str(number)] = {
                "verdict": verdict,
                "checks": len(rows),
                "failing": [{"name": r.get("name"), "link": r.get("link")} for r in failing],
                "pending": [r.get("name") for r in pending],
                "rows": rows,
            }
        except Exception as exc:
            out["state"] = "UNKNOWN"
            out["error"] = (out["error"] + "; " if out["error"] else "") + f"ci.{number}: {exc}"
            out["prs"][str(number)] = {
                "verdict": "UNKNOWN",
                "checks": 0,
                "failing": [],
                "pending": [],
                "error": str(exc),
            }
    return out


def observe_local_agents(
    runner: Callable[[list[str]], str] | None = None,
) -> dict[str, Any]:
    run = runner if runner is not None else (lambda av: _subprocess_runner(av, timeout=20))
    try:
        raw = run(["claude", "agents", "--json"])
        agents = json.loads(raw or "[]")
        return {"state": "OK", "agents": agents, "error": None}
    except Exception as exc:
        return {"state": "UNKNOWN", "agents": [], "error": str(exc)}


def observe_fleet_snapshot(
    path: str | os.PathLike[str] | None,
    max_age_seconds: int = 300,
    now_epoch: int | None = None,
) -> dict[str, Any]:
    """Read a snapshot another producer wrote.  Absence is UNKNOWN, not OK.

    The daemon has no MCP client of its own; the snapshot is the seam.  A
    snapshot older than ``max_age_seconds`` is STALE, and stale sessions are
    still reported — a reader can see what was believed and how old it is,
    instead of seeing an empty list that looks like "nothing is running".
    """
    now = _now_epoch() if now_epoch is None else now_epoch
    if not path:
        return {"state": "UNKNOWN", "sessions": [], "reason": "no snapshot configured"}
    p = Path(path)
    if not p.exists():
        return {"state": "UNKNOWN", "sessions": [], "reason": f"no snapshot at {p}"}
    try:
        data = json.loads(p.read_text())
    except Exception as exc:
        return {"state": "UNKNOWN", "sessions": [], "reason": f"unreadable snapshot: {exc}"}

    written = data.get("written_at_epoch")
    sessions = data.get("sessions", [])
    if not isinstance(written, (int, float)):
        return {
            "state": "UNKNOWN",
            "sessions": sessions,
            "reason": "snapshot has no written_at_epoch",
        }
    age = now - int(written)
    state = "FRESH" if age <= max_age_seconds else "STALE"
    return {
        "state": state,
        "sessions": sessions,
        "written_at_epoch": int(written),
        "age_seconds": age,
        "reason": None if state == "FRESH" else f"snapshot age {age}s exceeds {max_age_seconds}s",
    }


FLEET_SNAPSHOT_SCHEMA = "serveros/fleet-next-local-goal-supervisor.fleet-snapshot.v1"


def write_fleet_snapshot(
    raw_path: str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    now_epoch: int | None = None,
    producer: str = "unknown",
) -> dict[str, Any]:
    """Normalize a producer's session JSON into the daemon's snapshot seam.

    The daemon cannot speak the Fleet MCP's OAuth transport on its own (the
    local PAT is disabled), so the snapshot is how an MCP-authorized producer
    hands it facts.  ``producer`` and ``written_at_epoch`` travel with the
    facts so a reader can tell a fresh measurement from a remembered one.
    """
    raw = json.loads(Path(raw_path).read_text())
    sessions = raw.get("sessions", raw if isinstance(raw, list) else [])
    normalized = [
        {
            "session_ref": item.get("session_ref"),
            "device_ref": item.get("device_ref"),
            "runtime": item.get("runtime"),
            "classification": item.get("classification"),
            "title": item.get("title") or item.get("name"),
        }
        for item in sessions
        if isinstance(item, dict)
    ]
    payload = {
        "schema": FLEET_SNAPSHOT_SCHEMA,
        "written_at_epoch": _now_epoch() if now_epoch is None else now_epoch,
        "producer": producer,
        "sessions": normalized,
    }
    _write_json_atomic(Path(out_path), payload)
    return payload


def empty_observation() -> dict[str, Any]:
    return {
        "github": {"state": "UNKNOWN", "head_sha": None, "open_prs": [], "issues": {}, "error": "not observed"},
        "ci": {"state": "UNKNOWN", "prs": {}, "error": "not observed"},
        "local_agents": {"state": "UNKNOWN", "agents": [], "error": "not observed"},
        "fleet": {"state": "UNKNOWN", "sessions": [], "reason": "not observed"},
    }


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def compute_backoff(attempt: int, base: int = 60, cap: int = 1800) -> int:
    if attempt < 0:
        attempt = 0
    return min(cap, base * (2**attempt))


def _task_key(session_ref: str, head_sha: str | None) -> str:
    # Deterministic on identity, not on position: a reordered snapshot or a
    # restart must not turn one intent into two dispatches, so the session's
    # own ref and the cursor are the only inputs.
    return f"continue:{session_ref}:{head_sha or 'unknown'}"


def plan_tick(
    observation: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
    now: str,
    stop_file: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Pure decision function.  No I/O, no clock, no sending.

    The order matters.  A stop wins over everything.  A missing provider is a
    BLOCKED activation, not a dry run.  A held-unknown dispatch suppresses new
    work.  The cap counts sessions already running.  Only then are candidates
    offered, and a RUNNING_AGENT is refused rather than messaged.
    """
    if stop_file and Path(stop_file).exists():
        return {
            "decision": "STOPPED",
            "reason": f"stop file present: {stop_file}",
            "effective_inflight": 0,
            "would_dispatch": [],
            "refused": {},
            "held_unknown": [],
            "already_known": [],
        }

    dispatch_cfg = config.get("dispatch", {})
    cap = int(dispatch_cfg.get("max_inflight", 3))

    ledger = state.get("dispatch_ledger", {})
    ledger_active = [
        key for key, entry in ledger.items() if entry.get("status") in ACTIVE_LEDGER_STATUSES
    ]
    held_unknown = [
        key
        for key, entry in ledger.items()
        if entry.get("status") == "DISPATCH_ACKED_UNOBSERVED"
        or (entry.get("status") in {"DISPATCHED", "RUNNING"} and not entry.get("run_id"))
    ]

    sessions = observation.get("fleet", {}).get("sessions", []) or []
    running = [
        s.get("session_ref")
        for s in sessions
        if s.get("classification") == "RUNNING_AGENT" and s.get("session_ref")
    ]

    # "Already active" counts work this supervisor did not start.
    effective_inflight = max(len(running), len(ledger_active))

    base = {
        "effective_inflight": effective_inflight,
        "running_observed": running,
        "ledger_active": ledger_active,
        "held_unknown": held_unknown,
        "refused": {},
        "would_dispatch": [],
        "already_known": [],
        "now": now,
        "cap": cap,
    }

    if dispatch_cfg.get("enabled") and not dispatch_cfg.get("provider"):
        return {
            **base,
            "decision": "BLOCKED_ACTIVATION",
            "gap": "NO_VERIFIED_DISPATCH_PROVIDER",
            "reason": "dispatch is enabled but no verified provider is named; "
            "an accepted create/send would not be long-horizon residency",
        }

    # Candidates: idle owners only.  RUNNING_AGENT is recorded, never targeted.
    candidates: list[dict[str, Any]] = []
    refused: dict[str, str] = {}
    head_sha = observation.get("github", {}).get("head_sha")
    for session in sessions:
        ref = session.get("session_ref")
        classification = session.get("classification")
        if not ref:
            continue
        if classification == "RUNNING_AGENT":
            refused[ref] = "RUNNING_AGENT"
            continue
        if classification in DISPATCHABLE_CLASSES:
            key = _task_key(ref, head_sha)
            if key in ledger:
                base["already_known"].append(key)
                continue
            candidates.append(
                {
                    "task_key": key,
                    "session_ref": ref,
                    "classification": classification,
                    "provider": dispatch_cfg.get("provider"),
                    "run_id": None,
                }
            )
    base["refused"] = refused

    if held_unknown:
        return {
            **base,
            "decision": "NO_DISPATCH",
            "reason": "a dispatch was acknowledged but never observed; "
            "re-sending would be a second Run",
        }

    if effective_inflight >= cap:
        return {
            **base,
            "decision": "NO_DISPATCH_CAP",
            "reason": f"{effective_inflight} active >= cap {cap}",
        }

    if not candidates:
        return {**base, "decision": "NO_DISPATCH", "reason": "no idle independent owner"}

    if not dispatch_cfg.get("enabled"):
        return {
            **base,
            "decision": "DRY_RUN_DISPATCH",
            "reason": "dispatch disabled; this is the plan a live tick would execute",
            "would_dispatch": candidates,
        }

    return {
        **base,
        "decision": "DISPATCH",
        "reason": "enabled, provider named, under cap, no held-unknown",
        "would_dispatch": candidates,
    }


# ---------------------------------------------------------------------------
# Tick
# ---------------------------------------------------------------------------


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(tmp, path)


def observe_all(
    config: dict[str, Any],
    runner: Callable[[list[str]], str] | None = None,
    local_runner: Callable[[list[str]], str] | None = None,
    now_epoch: int | None = None,
) -> dict[str, Any]:
    sources = config.get("sources", {})
    github = observe_github(
        repo=sources.get("github_repo", "ProfessorZhi/serveros"),
        cwd=sources.get("cwd"),
        runner=runner,
        prs=config.get("watch", {}).get("prs"),
        issues=config.get("watch", {}).get("issues"),
    )
    ci = observe_ci(
        repo=sources.get("github_repo", "ProfessorZhi/serveros"),
        prs=config.get("watch", {}).get("prs"),
        runner=runner,
        cwd=sources.get("cwd"),
    )
    if sources.get("local_agents"):
        local = observe_local_agents(local_runner)
    else:
        local = {"state": "UNKNOWN", "agents": [], "error": "local_agents source disabled"}
    fleet = observe_fleet_snapshot(
        sources.get("fleet_snapshot"), now_epoch=now_epoch
    )
    return {"github": github, "ci": ci, "local_agents": local, "fleet": fleet}


def tick(
    config: dict[str, Any],
    state_dir: str | os.PathLike[str],
    runner: Callable[[list[str]], str] | None = None,
    local_runner: Callable[[list[str]], str] | None = None,
    now_epoch: int | None = None,
    now_iso: str | None = None,
    boot_epoch: str | None = None,
) -> dict[str, Any]:
    now_epoch = _now_epoch() if now_epoch is None else now_epoch
    now_iso = _now_iso() if now_iso is None else now_iso
    boot = kernel_boot_epoch() if boot_epoch is None else boot_epoch
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    store = StateStore(state_dir)
    state = resume_state(store.load(), boot)
    observation = observe_all(config, runner=runner, local_runner=local_runner, now_epoch=now_epoch)

    stop_file = state_dir / config.get("paths", {}).get("stop_file", "STOP")
    decision = plan_tick(observation, state, config, now_iso, stop_file=stop_file)

    state["cursor"]["github_head"] = observation["github"].get("head_sha")
    state["cursor"]["last_tick_at"] = now_iso
    state["cursor"]["source_state"] = {
        name: observation[name].get("state") for name in observation
    }
    if observation["github"].get("state") == "OK":
        state["cursor"]["last_success_at"] = now_iso
    state["counters"]["ticks"] = int(state["counters"].get("ticks", 0)) + 1
    state["last_decision"] = decision["decision"]
    state["updated_at"] = now_iso

    heartbeat = {
        "schema": HEARTBEAT_SCHEMA,
        "pid": os.getpid(),
        "goal_id": state["goal_id"],
        "boot_epoch": boot,
        "last_tick_iso": now_iso,
        "last_tick_epoch": now_epoch,
        "decision": decision["decision"],
        "sources": state["cursor"]["source_state"],
    }
    _write_json_atomic(state_dir / "heartbeat.json", heartbeat)

    tick_id = uuid.uuid4().hex[:12]
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "tick_id": tick_id,
        "goal_id": state["goal_id"],
        "boot_epoch": boot,
        "now": now_iso,
        "observation": observation,
        "decision": decision,
        "state_summary": {
            "resume_count": state.get("resume_count", 0),
            "ticks": state["counters"]["ticks"],
            "dispatches": state["counters"].get("dispatches", 0),
        },
    }
    receipt_dir = state_dir / "receipts"
    _write_json_atomic(receipt_dir / f"{now_iso.replace(':', '').replace('+', 'p')}-{tick_id}.json", receipt)
    store.save(state)
    return receipt


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _log(event: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = SingleInstanceLock(state_dir / "goal.lock")
    if not lock.acquire():
        _log({"event": "supervisor.already_running", "lock": str(state_dir / "goal.lock")})
        return 3

    stop = {"flag": False}

    def _on_signal(signum: int, _frame: Any) -> None:
        stop["flag"] = True
        _log({"event": "supervisor.signal", "signal": signum})

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    attempt = 0
    poll = config.get("poll", {})
    try:
        _log(
            {
                "event": "supervisor.started",
                "goal_id": config.get("goal", {}).get("id"),
                "pid": os.getpid(),
                "state_dir": str(state_dir),
                "dispatch_enabled": bool(config.get("dispatch", {}).get("enabled")),
            }
        )
        while not stop["flag"]:
            stop_file = state_dir / config.get("paths", {}).get("stop_file", "STOP")
            if stop_file.exists():
                _log({"event": "supervisor.stopped_by_file", "stop_file": str(stop_file)})
                break
            try:
                receipt = tick(config, state_dir)
                _log(
                    {
                        "event": "tick",
                        "tick_id": receipt["tick_id"],
                        "decision": receipt["decision"]["decision"],
                        "head_sha": receipt["observation"]["github"].get("head_sha"),
                        "sources": {
                            k: v.get("state")
                            for k, v in receipt["observation"].items()
                        },
                    }
                )
                healthy = receipt["observation"]["github"].get("state") == "OK"
                attempt = 0 if healthy else attempt + 1
                delay = compute_backoff(
                    attempt,
                    base=int(poll.get("base_seconds", 60)),
                    cap=int(poll.get("cap_seconds", 1800)),
                )
            except Exception as exc:  # a failed tick must not kill the supervisor
                attempt += 1
                delay = compute_backoff(
                    attempt,
                    base=int(poll.get("base_seconds", 60)),
                    cap=int(poll.get("cap_seconds", 1800)),
                )
                _log({"event": "tick.error", "error": str(exc), "next_delay": delay})

            deadline = time.time() + delay
            while time.time() < deadline and not stop["flag"]:
                time.sleep(min(1.0, deadline - time.time()))
    finally:
        lock.release()
        _log({"event": "supervisor.exit", "pid": os.getpid()})
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    state_dir = Path(args.state_dir)
    lock = SingleInstanceLock(state_dir / "goal.lock")
    if not lock.acquire():
        _log({"event": "supervisor.already_running"})
        return 3
    try:
        receipt = tick(config, state_dir)
    finally:
        lock.release()
    _log(receipt)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir)
    store = StateStore(state_dir)
    heartbeat_path = state_dir / "heartbeat.json"
    heartbeat = json.loads(heartbeat_path.read_text()) if heartbeat_path.exists() else None
    state = store.load() if store.exists() else None
    lock = SingleInstanceLock(state_dir / "goal.lock")
    held = not lock.acquire()
    if not held:
        lock.release()

    age = None
    if heartbeat and heartbeat.get("last_tick_epoch"):
        age = _now_epoch() - int(heartbeat["last_tick_epoch"])

    _log(
        {
            "event": "supervisor.status",
            "goal_id": (state or {}).get("goal_id"),
            "lock_held": held,
            "heartbeat": heartbeat,
            "heartbeat_age_seconds": age,
            "resume_count": (state or {}).get("resume_count"),
            "last_decision": (state or {}).get("last_decision"),
            "state_path": str(store.path),
        }
    )
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir)
    stop_file = state_dir / "STOP"
    state_dir.mkdir(parents=True, exist_ok=True)
    stop_file.write_text(f"stopped by pid {os.getpid()} at {_now_iso()}\n")
    _log({"event": "supervisor.stop_requested", "stop_file": str(stop_file)})
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir)
    stop_file = state_dir / "STOP"
    if stop_file.exists():
        stop_file.unlink()
    _log({"event": "supervisor.resume_ready", "stop_file": str(stop_file)})
    return 0


def cmd_verify_lock(args: argparse.Namespace) -> int:
    lock = SingleInstanceLock(Path(args.state_dir) / "goal.lock")
    acquired = lock.acquire()
    if acquired:
        lock.release()
    _log({"event": "supervisor.lock_check", "acquired": acquired})
    return 0 if acquired else 1


def cmd_plan(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    state_dir = Path(args.state_dir)
    state = StateStore(state_dir).load()
    observation = observe_all(config)
    decision = plan_tick(
        observation,
        state,
        config,
        _now_iso(),
        stop_file=state_dir / config.get("paths", {}).get("stop_file", "STOP"),
    )
    _log({"event": "supervisor.plan", "observation": observation, "decision": decision})
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    payload = write_fleet_snapshot(args.raw, args.out, producer=args.producer)
    _log(
        {
            "event": "supervisor.fleet_snapshot_written",
            "out": args.out,
            "sessions": len(payload["sessions"]),
            "producer": args.producer,
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=str(Path.home() / "state" / "fleet-next-local-goal-supervisor"))
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run the long-horizon tick loop")
    run.set_defaults(func=cmd_run)

    once = sub.add_parser("once", help="run exactly one tick and print the receipt")
    once.set_defaults(func=cmd_once)

    status = sub.add_parser("status", help="read state and heartbeat without taking the lock")
    status.set_defaults(func=cmd_status)

    stop = sub.add_parser("stop", help="write the kill-switch file")
    stop.set_defaults(func=cmd_stop)

    resume = sub.add_parser("resume", help="remove the kill-switch file")
    resume.set_defaults(func=cmd_resume)

    verify = sub.add_parser("verify-lock", help="prove the single-instance lock")
    verify.set_defaults(func=cmd_verify_lock)

    plan = sub.add_parser("plan", help="observe and print the decision without writing state")
    plan.set_defaults(func=cmd_plan)

    snapshot = sub.add_parser(
        "snapshot", help="normalize a producer's session JSON into the fleet snapshot seam"
    )
    snapshot.add_argument("--raw", required=True, help="session JSON from an MCP-authorized producer")
    snapshot.add_argument("--out", required=True, help="snapshot path the daemon reads")
    snapshot.add_argument("--producer", default="unknown")
    snapshot.set_defaults(func=cmd_snapshot)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
