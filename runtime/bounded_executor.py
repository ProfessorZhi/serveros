"""One offline engineering command in a pinned local Docker image.

The continuation adapter supplies a verified grant and current checkpoint. This
module is an execution boundary, not an authority issuer. Docker receives only
two mounts; its socket and the host environment never enter the container.
Invocation receipts live outside both mounts. An uncertain invocation is never
launched again under the same key.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

_spec = importlib.util.spec_from_file_location(
    "serveros_runtime_bounded_invocation", Path(__file__).with_name("bounded_invocation.py"))
bounded = sys.modules.get(_spec.name)
if bounded is None:
    bounded = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = bounded
    _spec.loader.exec_module(bounded)

SCHEMA = "serveros/engineering-binding.v1"
DOCKER = "/usr/bin/docker"
# PID 1 enforces the deadline even if the MCP adapter or Docker client dies.
# Exiting PID 1 terminates every remaining process in this private PID namespace.
BOOTSTRAP = '''import json, os, subprocess, sys, time
c=json.loads(sys.argv[1])
remaining=c["deadline"]-time.time()
if remaining <= 0: sys.exit(124)
env={"PATH":"/usr/local/bin:/usr/bin:/bin", "HOME":"/tmp", "TMPDIR":"/tmp",
     "PYTHONDONTWRITEBYTECODE":"1"}
try:
    p=subprocess.Popen(c["argv"], env=env, start_new_session=True)
    sys.exit(p.wait(timeout=remaining))
except subprocess.TimeoutExpired:
    sys.exit(124)
'''


def _directory(raw, *, exists=True):
    path = Path(str(raw))
    if (not path.is_absolute() or path != path.resolve() or "," in str(path)
            or ".." in path.parts or (exists and not path.is_dir())):
        raise ValueError("an absolute, real directory without traversal or symlinks is required")
    return path


def binding(*, envelope, checkpoint, grant, worktrees_root):
    """Resolve only from the live grant, then intersect both declared scopes."""
    value = grant.get("engineering")
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ValueError("this grant has no engineering binding")
    if "engineering.execute" not in grant.get("actions", []):
        raise ValueError("this grant does not authorize engineering.execute")
    if envelope.get("work_item_ref") not in grant.get("items", []):
        raise ValueError("the engineering grant does not include this Item")
    root = _directory(worktrees_root)
    worktree = _directory(value.get("worktree"))
    if not worktree.is_relative_to(root) or len(worktree.relative_to(root).parts) < 2:
        raise ValueError("engineering must name one project worktree, not a shared root")
    for scopes in (envelope.get("allowed_write_scope"), checkpoint.get("write_scope")):
        if not any(worktree.is_relative_to(_directory(p)) for p in (scopes or [])):
            raise ValueError("worktree is outside this wake's current write scope")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(value.get("image", ""))):
        raise ValueError("engineering image must be a pinned local image ID")
    limits = {"memory_bytes": (32 * 1024**2, 10 * 1024**3),
              "cpus": (1, 4), "pids_limit": (8, 256)}
    for field, (low, high) in limits.items():
        if type(value.get(field)) is not int or not low <= value[field] <= high:
            raise ValueError(f"engineering {field} must be an integer in [{low}, {high}]")
    if os.getuid() == 0:
        raise ValueError("engineering adapter must run as a non-root user")
    return {k: value[k] for k in ("worktree", "image", *limits)}


def _docker(config):
    # Explicit local transport/config: no inherited DOCKER_HOST or credential
    # helpers can redirect this invocation to another daemon.
    return [DOCKER, "--host", "unix:///var/run/docker.sock",
            "--config", str(Path(config["private_root"]) / "docker-config")]


def create_argv(config):
    mount = lambda source, target: ["--mount", f"type=bind,src={source},dst={target}"]
    return (_docker(config) + ["create", "--name", config["name"], "--pull=never",
            "--label", "serveros.executor=" + config["identity"],
            "--label", "serveros.item=" + config["work_item"],
            "--label", "serveros.grant=" + config["grant_ref"],
            "--label", "serveros.epoch=" + str(config["epoch"]),
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit", str(config["pids_limit"]),
            "--memory", str(config["memory_bytes"]),
            "--memory-swap", str(config["memory_bytes"]), "--cpus", str(config["cpus"]),
            "--user", f"{config['uid']}:{config['gid']}", "--log-driver=none",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=67108864,mode=1777",
            "--workdir=/workspace", "--entrypoint=python3"]
            + mount(config["worktree"], "/workspace")
            + mount(config["artifacts"], "/artifacts")
            + [config["image"], "-I", "-c", BOOTSTRAP,
               json.dumps({"argv": config["argv"], "deadline": config["deadline"]})])


def _run(config, args):
    return subprocess.run(_docker(config) + args, capture_output=True,
                          text=True, timeout=10, check=False)


def _state(config):
    result = _run(config, ["inspect", "--format", "{{json .}}", config["name"]])
    if result.returncode:
        return None  # unavailable is not proof of absence
    row = json.loads(result.stdout)
    if row.get("Config", {}).get("Labels", {}).get("serveros.executor") != config["identity"]:
        raise ValueError("container identity does not match this invocation")
    return row


def _settle(config):
    """Only our labelled container may be stopped; never claim success blind."""
    row = _state(config)
    if row is None:
        return {"worker_stopped": False, "effect": "UNKNOWN"}
    if row["State"].get("Running"):
        _run(config, ["kill", config["name"]])
        row = _state(config)
    if row is None or row["State"].get("Running") or row["State"].get("Status") not in ("exited", "created", "dead"):
        return {"worker_stopped": False, "effect": "UNKNOWN"}
    result = {"worker_stopped": True, "container_state": row["State"],
              "effect": "EXECUTED_UNVERIFIED", "container_id": row["Id"]}
    removed = _run(config, ["rm", config["name"]])
    result["container_removed"] = removed.returncode == 0
    return result


def _worker(config):
    """Trusted host worker; stdout/stderr are captured by bounded_invocation."""
    metadata = _run(config, ["image", "inspect", "--format", "{{json .}}", config["image"]])
    if metadata.returncode:
        raise ValueError("pinned local engineering image is unavailable; no pull attempted")
    image = json.loads(metadata.stdout)
    if image["Id"] != config["image"] or image["Config"].get("Volumes"):
        raise ValueError("image identity changed or declares unbounded implicit volumes")
    made = subprocess.run(create_argv(config), capture_output=True, text=True, timeout=10)
    if made.returncode:
        raise ValueError("container creation failed: " + made.stderr[:1000])
    report = None
    try:
        # Outer invocation owns output bounds; container PID 1 owns its deadline.
        subprocess.run(_docker(config) + ["start", "--attach", config["name"]],
                       timeout=max(1, config["deadline"] - datetime.now(timezone.utc).timestamp()) + 5)
    finally:
        report = _settle(config)
        Path(config["report"]).write_text(json.dumps(report, sort_keys=True))
    return 0 if report.get("worker_stopped") else 2


RUN_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "INVALID"}


def _paths_module():
    """`runtime/paths.py`, under the name the continuation adapter already uses."""
    spec = importlib.util.spec_from_file_location(
        "serveros_runtime_paths", Path(__file__).with_name("paths.py"))
    module = sys.modules.get(spec.name)
    if module is None:
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


def _work_engine_rows(state_db, sql: str, params: tuple = ()):
    """Read the Work Engine store read-only; a miss is a refusal, never empty."""
    path = Path(str(state_db))
    if not path.is_absolute() or not path.is_file():
        raise ValueError(
            f"T10 continuation binding: Work Engine store {str(state_db)!r} is "
            f"not a readable absolute file. Nothing was run")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise ValueError(
            f"T10 continuation binding: Work Engine store {path} cannot be "
            f"opened read-only ({exc}). Nothing was run") from exc
    try:
        conn.row_factory = sqlite3.Row
        return list(conn.execute(sql, params))
    except sqlite3.Error as exc:
        raise ValueError(
            f"T10 continuation binding: Work Engine store {path} has no "
            f"readable Run/execution-context rows ({exc}). Nothing was run") from exc
    finally:
        conn.close()


def _open_run(state_db, item: str):
    """The Item's unique open Work Engine Run, or a refusal.

    `context/resolver._runs` calls the latest Run "ordering only, never a
    writable target". This is the stronger reading: an engineering execution
    binds only when exactly one non-terminal Run exists. Zero means no canonical
    Run; more than one means the binding would have to choose, and choosing is
    the invention this milestone refuses.
    """
    rows = _work_engine_rows(
        state_db,
        "SELECT run_id, activity_id, worker, state FROM runs "
        "WHERE item_id=? ORDER BY run_id", (item,))
    opened = [row for row in rows
              if str(row["state"] or "").strip().upper() not in RUN_TERMINAL]
    if not opened:
        raise ValueError(
            f"T10 continuation binding: no open Work Engine Run for {item}; a "
            f"continuation execution without a canonical Work Run cannot be "
            f"bound. Nothing was run")
    if len(opened) > 1:
        names = ", ".join(str(row["run_id"]) for row in opened)
        raise ValueError(
            f"T10 continuation binding: more than one open Work Engine Run for "
            f"{item} ({names}); a continuation execution cannot choose one. "
            f"Nothing was run")
    row = opened[0]
    for field in ("run_id", "activity_id", "worker", "state"):
        if not str(row[field] or "").strip():
            raise ValueError(
                f"T10 continuation binding: Work Engine Run for {item} has no "
                f"{field}. Nothing was run")
    return row


def _execution_context(state_db, item: str, activity: str, worktree: Path):
    """The execution_context for this Run's activity at the granted worktree."""
    rows = _work_engine_rows(
        state_db,
        "SELECT repo, mode, revision, worktree, dirty FROM execution_contexts "
        "WHERE item_id=? AND activity_id=? ORDER BY context_id",
        (item, activity))
    wanted = worktree.resolve()
    matching = []
    for row in rows:
        raw = str(row["worktree"] or "").strip()
        if not raw:
            continue
        try:
            if Path(raw).resolve() == wanted:
                matching.append(row)
        except OSError:
            continue
    if not matching:
        raise ValueError(
            f"T10 continuation binding: no Work Engine execution_context for "
            f"{item}/{activity} at {wanted}; the Run does not name the tree "
            f"this grant would execute in. Nothing was run")
    if len(matching) > 1:
        raise ValueError(
            f"T10 continuation binding: more than one Work Engine "
            f"execution_context for {item}/{activity} at {wanted}. Nothing was run")
    return matching[0]


def _git_output(cwd: Path, *argv: str) -> str:
    """One read-only git answer, with no ambient GIT_* pointing elsewhere."""
    env = {name: value for name, value in os.environ.items()
           if not name.startswith("GIT_")}
    try:
        done = subprocess.run(
            ["git", *argv], cwd=str(cwd), capture_output=True, text=True,
            timeout=15, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(
            f"T10 continuation binding cannot read the git worktree at {cwd}: "
            f"{type(exc).__name__}: {exc}. Nothing was run") from exc
    if done.returncode != 0:
        raise ValueError(
            f"T10 continuation binding cannot read the git worktree at {cwd}: "
            f"{done.stderr.strip() or 'git exited ' + str(done.returncode)}. "
            f"Nothing was run")
    return done.stdout.strip()


def continuation_binding(*, envelope, grant, resolved, key, action,
                         state_db) -> dict:
    """The canonical T10 binding for one continuation engineering execution.

    Every operand is read from an existing authority or from the validated
    envelope/grant, and nothing is taken from the caller or the child:

      * `cycle_ref` is the envelope's checkpoint_ref;
      * `work_item_id`, `grant_ref` are the envelope's;
      * `principal` is the grant's `delegated_by`, and the envelope's
        `delegation_ref` must equal it;
      * `work_run_id` and `runner_identity` are the unique open Work Engine
        Run's `run_id` and `worker`;
      * `commit_sha` is that Run's execution_context `revision`, which must be
        the exact worktree HEAD with a clean tracked tree;
      * `action_key` is the bounded-invocation key this execution will write.

    Missing, ambiguous, revoked, expired or mismatched provenance refuses here,
    before the receipt lock or any spawn.
    """
    work_item = str(envelope.get("work_item_ref") or "").strip()
    grant_ref = str(envelope.get("grant_ref") or "").strip()
    checkpoint_ref = str(envelope.get("checkpoint_ref") or "").strip()
    principal = str(grant.get("delegated_by") or "").strip()
    delegation = str(envelope.get("delegation_ref") or "").strip()
    if not all((work_item, grant_ref, checkpoint_ref, principal, delegation)):
        raise ValueError(
            "T10 continuation binding: the envelope/grant is missing "
            "work_item_ref, grant_ref, checkpoint_ref, delegation_ref or "
            "delegated_by. Nothing was run")
    if delegation != principal:
        raise ValueError(
            f"T10 continuation binding: envelope delegation_ref {delegation!r} "
            f"does not match grant delegated_by {principal!r}. Nothing was run")
    if grant.get("revoked") is True:
        raise ValueError(
            f"T10 continuation binding: grant {grant_ref!r} is revoked. "
            f"Nothing was run")
    try:
        valid_from = datetime.fromisoformat(str(grant.get("valid_from")))
        expires_at = datetime.fromisoformat(str(grant.get("expires_at")))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"T10 continuation binding: grant {grant_ref!r} has no readable "
            f"valid_from/expires_at. Nothing was run") from exc
    if valid_from.tzinfo is None or expires_at.tzinfo is None:
        raise ValueError(
            f"T10 continuation binding: grant {grant_ref!r} has a naive "
            f"window; a bound with no timezone is not one. Nothing was run")
    now = datetime.now(timezone.utc)
    if not valid_from <= now < expires_at:
        raise ValueError(
            f"T10 continuation binding: grant {grant_ref!r} is not in force at "
            f"{now.isoformat()} (window {grant.get('valid_from')} to "
            f"{grant.get('expires_at')}). Nothing was run")

    worktree = Path(str(resolved.get("worktree") or "")).resolve()
    if not worktree.is_dir():
        raise ValueError(
            f"T10 continuation binding: the engineering worktree {worktree} is "
            f"not a directory. Nothing was run")
    run = _open_run(state_db, work_item)
    context = _execution_context(
        state_db, work_item, str(run["activity_id"]), worktree)
    revision = str(context["revision"] or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError(
            f"T10 continuation binding: execution_context revision {revision!r} "
            f"is not an exact full 40-hex SHA. Nothing was run")
    if int(context["dirty"] or 0):
        raise ValueError(
            "T10 continuation binding: execution_context records a dirty "
            "worktree; the SHA does not describe what would be tested. "
            "Nothing was run")
    if not str(context["repo"] or "").strip() or not str(context["mode"] or "").strip():
        raise ValueError(
            "T10 continuation binding: execution_context carries no repo/mode. "
            "Nothing was run")
    head = _git_output(worktree, "rev-parse", "HEAD").lower()
    if head != revision:
        raise ValueError(
            f"T10 continuation binding: execution_context revision {revision} "
            f"is not the worktree HEAD {head}. Nothing was run")
    dirty = _git_output(worktree, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise ValueError(
            "T10 continuation binding: the worktree has tracked changes; the "
            f"SHA does not describe it: {dirty.splitlines()[:5]}. Nothing was run")

    return {
        "cycle_ref": checkpoint_ref,
        "action_key": str(action),
        "work_item_id": work_item,
        "work_run_id": str(run["run_id"]),
        "commit_sha": revision,
        "principal": principal,
        "grant_ref": grant_ref,
        "runner_identity": str(run["worker"]).strip(),
    }


def receipt_sha256(receipts_dir, action_key: str) -> str | None:
    """Anchor over the receipt and its three artifacts, or None if incomplete.

    The same bytes-anchor the consumer recomputes. It is written into the
    existing private `<action>.container` report; no new ledger is created.
    """
    root = _directory(receipts_dir)
    path = root / (str(action_key) + ".json")
    if path.is_symlink() or not path.is_file():
        return None
    artifacts = {}
    for name in ("stdin", "stdout", "stderr"):
        item = root / (str(action_key) + "." + name)
        if item.is_symlink() or not item.is_file():
            return None
        artifacts[name] = item.read_bytes()
    digest = hashlib.sha256()
    for label, data in (
        ("receipt", path.read_bytes()),
        ("stdin", artifacts["stdin"]),
        ("stdout", artifacts["stdout"]),
        ("stderr", artifacts["stderr"]),
    ):
        digest.update(label.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(data)
        digest.update(b"\x00")
    return digest.hexdigest()


def execute(*, envelope, grant, resolved, key, argv, receipts, state_db=None):
    """Caller must recheck authority immediately before this call.

    Commands are argv inside the container. No caller-supplied host argv,
    environment, Docker option, image or mount can enter this interface.
    """
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", str(key)):
        raise ValueError("execution key must contain 1-48 letters, digits, _ or -")
    if (not isinstance(argv, list) or not argv or len(argv) > 128
            or any(not isinstance(s, str) or not s or "\x00" in s for s in argv)
            or sum(map(len, argv)) > 32768):
        raise ValueError("a bounded, nonempty container argv is required")
    budget = grant.get("budget", {})
    for field in ("wall_seconds", "max_attempts", "max_output_bytes"):
        if type(budget.get(field)) is not int or budget[field] <= 0:
            raise ValueError("engineering requires a positive bounded grant budget")
    receipts = _directory(receipts)
    if receipts.is_relative_to(Path(resolved["worktree"])):
        raise ValueError("engineering receipts must be outside the writable worktree")
    identity = hashlib.sha256((str(receipts) + envelope["action_key"] + key).encode()).hexdigest()
    action = "exec-" + hashlib.sha256(key.encode()).hexdigest()[:24]
    # THE BINDING IS BUILT BEFORE THIS CALL CREATES ANYTHING. A missing Work
    # run, an ambiguous run, a stale revision, a revoked grant or a
    # cross-principal envelope refuses here, before the private directory, the
    # lock or any spawn exists.
    if state_db is None:
        paths = _paths_module().Paths()
        if not paths.bound:
            raise ValueError(
                "T10 continuation binding: node path profile is unbound, so the "
                "Work Engine store cannot be located. Nothing was run")
        state_db = Path(paths.root("state")) / "serveros.db"
    t10_binding = continuation_binding(
        envelope=envelope, grant=grant, resolved=resolved, key=key,
        action=action, state_db=state_db)

    private = receipts / ("engineering-" + hashlib.sha256(envelope["action_key"].encode()).hexdigest()[:20])
    private.mkdir(mode=0o700, exist_ok=True)
    _directory(private)
    if private.stat().st_mode & 0o077:
        raise ValueError("engineering receipt root must be private")
    # One adapter is serial, and the private budget lock also closes concurrent
    # MCP/reconnect admission races. Parameter errors above consume no attempt.
    with bounded._receipt_lock(private / "budget.lock"):
        if not (private / (action + ".json")).exists() and len(list(private.glob("exec-*.json"))) >= budget["max_attempts"]:
            raise ValueError("this wake's engineering attempt budget is exhausted")
        artifacts = private / (action + "-artifacts")
        artifacts.mkdir(mode=0o700, exist_ok=True)
        _directory(artifacts)
        (private / "docker-config").mkdir(mode=0o700, exist_ok=True)
        expires = min(datetime.fromisoformat(envelope["expires_at"]).timestamp(),
                      datetime.fromisoformat(grant["expires_at"]).timestamp())
        issued = datetime.fromisoformat(envelope["issued_at"]).timestamp()
        config = {**resolved, **t10_binding,
                  "identity": identity, "name": "serveros-exec-" + identity[:24],
                  "work_item": envelope["work_item_ref"], "grant_ref": envelope["grant_ref"],
                  "epoch": envelope["epoch"],
                  "private_root": str(private), "artifacts": str(artifacts),
                  "report": str(private / (action + ".container")), "argv": argv,
                  "uid": os.getuid(), "gid": os.getgid(),
                  "deadline": min(expires, issued + budget["wall_seconds"])}
        receipt = bounded.invoke_once(key=action, argv=[sys.executable, "-I", str(Path(__file__).resolve()), "--worker"],
            cwd=private, receipts=private, input_text=json.dumps(config, sort_keys=True),
            timeout=budget["wall_seconds"] + 30, readonly=False,
            max_output_bytes=budget["max_output_bytes"])
        # A killed host client is not a stopped container. Reconcile only this
        # exact identity, including replay of an uncertain invocation.
        report_path = Path(config["report"])
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        if report.get("worker_stopped") is not True:
            try:
                report = _settle(config)
            except (OSError, ValueError, subprocess.SubprocessError):
                report = {"worker_stopped": False, "effect": "UNKNOWN"}
            report_path.write_text(json.dumps(report, sort_keys=True))
        # The receipt is written by `invoke_once`, outside the container mounts.
        # Anchor it into the report the trusted worker already owns; no new
        # ledger, and a later consumer can detect a receipt edited after this.
        anchor = receipt_sha256(private, action)
        report["receipt_sha256"] = anchor
        report["receipt_path"] = str(private / (action + ".json"))
        report_path.write_text(json.dumps(report, sort_keys=True))
        return {"receipt": receipt, "container": report, "artifacts": str(artifacts),
                "worker_stopped": report.get("worker_stopped") is True,
                "container_evidence": str(report_path),
                "receipt_path": report["receipt_path"],
                "receipt_sha256": anchor,
                "output_tail": {stream: (private / (action + "." + stream)).read_text(errors="replace")[-4000:]
                                if (private / (action + "." + stream)).exists() else ""
                                for stream in ("stdout", "stderr")},
                "stdout_artifact": str(private / (action + ".stdout")),
                "stderr_artifact": str(private / (action + ".stderr"))}


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    # -I excludes cwd and user site from the trusted worker's import path.
    sys.exit(_worker(json.load(sys.stdin)))
