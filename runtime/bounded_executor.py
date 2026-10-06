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


def execute(*, envelope, grant, resolved, key, argv, receipts):
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
    private = receipts / ("engineering-" + hashlib.sha256(envelope["action_key"].encode()).hexdigest()[:20])
    private.mkdir(mode=0o700, exist_ok=True)
    _directory(private)
    if private.stat().st_mode & 0o077:
        raise ValueError("engineering receipt root must be private")
    action = "exec-" + hashlib.sha256(key.encode()).hexdigest()[:24]
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
        config = {**resolved, "identity": identity, "name": "serveros-exec-" + identity[:24],
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
        return {"receipt": receipt, "container": report, "artifacts": str(artifacts),
                "worker_stopped": report.get("worker_stopped") is True,
                "container_evidence": str(report_path),
                "output_tail": {stream: (private / (action + "." + stream)).read_text(errors="replace")[-4000:]
                                if (private / (action + "." + stream)).exists() else ""
                                for stream in ("stdout", "stderr")},
                "stdout_artifact": str(private / (action + ".stdout")),
                "stderr_artifact": str(private / (action + ".stderr"))}


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    # -I excludes cwd and user site from the trusted worker's import path.
    sys.exit(_worker(json.load(sys.stdin)))
