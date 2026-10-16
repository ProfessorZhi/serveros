#!/usr/bin/env python3
"""Isolated Fleet Next installer wrapper. DRY-RUN by default; never targets V1.

The V1 install.py hardcodes serveros-fleet-mcp.service and must not be invoked
directly to install the Next canary. This wrapper uses a separate unit template,
configuration, release root and distinct unit name, and requires the machine-
readable dual-stack preflight to pass before any Next-side write or restart.

Owner flags are intent acknowledgments, NOT authentication or a substitute for
the human release approval required by the deployment policy.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fleet_mcp.deploy import next_dual_stack_preflight as gate

UNIT = gate.NEXT_UNIT
TEMPLATE = "fleet_mcp/deploy/serveros-fleet-example-mcp.service"
COMPOSITION = "ingress-next"

#: Commands that publish a Next revision WITHOUT starting it. Both are
#: unpublished bootstrap: they may leave the public URL and Plugin ID
#: unassigned, and both always pass `--no-start` down to the shared installer.
#: `refresh-stage` differs from `stage` in exactly one place — which revision
#: root it accepts. `stage` demands an EMPTY one; `refresh-stage` demands a
#: POPULATED one and refuses to run at all when the Next unit is not inactive.
#: Keeping them separate is what leaves `stage`'s first-install semantics
#: unchanged rather than subtly widened by a flag.
STAGING_COMMANDS = ("stage", "refresh-stage")

#: The one verb that STARTS the already-staged Next unit and nothing else.
#:
#: It exists because the two facts `install` demands before it will start —
#: a real `next.plugin_id` and `attestations.next_oauth_client_authorized` —
#: are facts about a client registered against a *running* public origin. A
#: first start cannot have them, and the only way to obtain them is to start
#: the service, so `install` alone leaves the fixed Next unit permanently
#: unstartable. `canary-start` closes that ordering gap without widening
#: anything else:
#:
#:   * it publishes NOTHING — no revision is copied, no `current`/`previous`
#:     pointer moves and no unit file is written, so it cannot misname bytes
#:     or repoint a service (the class of defect `install.py`'s guards and the
#:     refresh-stage work exist to close);
#:   * it never `enable`s (or `disable`s) the unit. `install` uses
#:     `enable --now`; this account has `Linger=yes`, so an enabled unit is
#:     started by the user manager at boot/login with no approval. A canary
#:     that enabled would convert a supervised start into an unsupervised one;
#:   * it never touches V1: V1 is read for its health and its MainPID, and for
#:     nothing else.
#:
#: What it does NOT excuse is every other precondition to serving: the staged
#: revision and its manifest, a binding the service itself accepts, a present
#: 0600 owner PAT anchor outside V1's roots, a *measured* public TLS origin,
#: the owner's reviewed attestations other than the two above, live memory
#: headroom, and the unit's inactive-and-unable-to-self-start state.
CANARY_START = "canary-start"

BUDGET = {
    "memory_max_mib": 1536,
    "memory_swap_max_mib": 0,
    "cpu_quota_pct": 40,
    "tasks_max": 96,
}

#: Mirrors of `install.py`'s own names, which are NOT imported at module load:
#: the shared installer is loaded lazily by `_load_legacy_installer()` precisely
#: so that importing this wrapper has no effect and no dependency. The mirrors
#: are asserted against the real module by the tests, so a rename on either side
#: fails loudly rather than silently checking for a file that is never written.
MANIFEST = "ARTIFACT.json"
ARTIFACT_SCHEMA = "serveros/fleet-mcp-artifact/v1"

#: The mode the owner's personal access token anchor must carry. It is a check
#: on the FILE, never on its contents: this module does not read the token, and
#: nothing in this package should.
PAT_ANCHOR_MODE = 0o600


class Refused(Exception):
    pass


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            item = json.load(f)
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot load non-secret proposal: {type(exc).__name__}") from exc
    if not isinstance(item, dict):
        raise Refused("proposal must be a JSON object")
    return item


def plan(proposal: dict, command: str, *, no_start: bool = False) -> dict:
    if command == "uninstall":
        return {"decision": "BLOCKED",
                "blockers": ["Next uninstall requires separate reviewed retirement"],
                "writes": [], "executes": []}
    recovery = command in ("rollback", "status")
    bootstrap = command in STAGING_COMMANDS
    canary = command == CANARY_START
    refresh = command == "refresh-stage"
    preflight = gate.evaluate(proposal, recovery=recovery, bootstrap=bootstrap,
                              canary=canary)
    ready = ("RECOVERY_PLAN_READY" if recovery else
             "BOOTSTRAP_PLAN_READY" if bootstrap else
             "CANARY_START_READY" if canary else
             "PRECHECK_READY_FOR_HUMAN_CANARY")
    if preflight["decision"] != ready:
        return {"decision": "BLOCKED", "blockers": preflight["blockers"],
                "writes": [], "executes": []}
    nxt = proposal["next"]
    root = nxt["runtime_root"]
    if Path(root).name != COMPOSITION:
        return {"decision": "BLOCKED",
                "blockers": ["next.runtime_root: must end in /ingress-next"],
                "writes": [], "executes": []}
    if not recovery and proposal["limits"] != BUDGET:
        return {"decision": "BLOCKED",
                "blockers": ["limits: must match the Next service template"],
                "writes": [], "executes": []}
    if not recovery:
        template = _HERE / "serveros-fleet-example-mcp.service"
        if not template.is_file():
            return {"decision": "BLOCKED", "blockers": ["Next unit template missing"],
                    "writes": [], "executes": []}
        body = template.read_text(encoding="utf-8")
        for required in (
            "MemoryMax=1536M", "MemorySwapMax=0", "CPUQuota=40%",
            "TasksMax=96", "NoNewPrivileges=yes", "@FLEET_MCP_RUNTIME@",
            "@FLEET_MCP_CONFIG@",
        ):
            if required not in body:
                return {"decision": "BLOCKED",
                        "blockers": ["Next unit template does not match bounded canary contract"],
                        "writes": [], "executes": []}
    return {
        "decision": "DRY_RUN_READY",
        "mode": ("NEXT_ONLY_RECOVERY" if recovery else
                 "NEXT_ONLY_CANARY_START" if canary else
                 "NEXT_ONLY_REFRESH_STAGE_NO_START" if refresh else
                 "NEXT_ONLY_STAGE_NO_START" if bootstrap else "NEXT_CANARY_INSTALL"),
        "command": command,
        "unit": UNIT,
        "composition": COMPOSITION,
        "same_as_v1": False,
        "limits": dict(BUDGET) if not recovery else None,
        "writes": [], "executes": [],
        "requires": [
            ("A Next-only binding the SERVICE accepts, a public HTTPS /mcp "
             "origin whose certificate is measured at start time, and a present "
             "0600 owner token anchor outside V1's roots. The Next Plugin ID "
             "and the Next OAuth client authorization may still be unassigned: "
             "this start is what makes them obtainable. Nothing is published, "
             "no pointer moves and the unit is not enabled"
             if canary else
             "Owner-reviewed Next-only binding; public URL and Plugin ID may "
             "remain unassigned only for this non-starting bootstrap stage"
             if bootstrap else
             "Next owner authorization and distinct real App/Plugin registration"),
            ("An INACTIVE Next unit that cannot start on its own (`disabled`, "
             "or not installed yet), a clean `--source`, and a revision that "
             "differs from the one `current` already names; an explicit "
             "`--revision` must restate the clean HEAD byte-for-byte; the "
             "revision being replaced is kept as `previous`"
             if refresh else
             "An INACTIVE Next unit that cannot start on its own (`disabled`, "
             "or not installed yet), and an empty Next revision root; the bytes "
             "are published without being started, so a unit that could start "
             "itself — enabled, or auto-restarting — is refused rather than "
             "silently re-pointed"
             if bootstrap else
             "An INACTIVE Next unit that cannot start on its own (`disabled`, "
             "or not installed yet): `--no-start` publishes without starting, "
             "so a unit that could start itself would run the new revision at "
             "the next boot/login with no activation approval"
             if command == "install" and no_start else
             "Short live memory-PSI/swap-IO probe before Next start"),
            "systemctl --user show confirms effective cgroup limits after start",
            "V1 /health remains healthy throughout canary and Next-only rollback",
        ],
    }


def _current_pressure() -> str | None:
    """One-second, tiny procfs-only sample; never uses SwapUsed occupancy."""
    try:
        def psi() -> float:
            for line in Path("/proc/pressure/memory").read_text().splitlines():
                if line.startswith("full "):
                    return float(re.search(r"\bavg10=([0-9.]+)", line).group(1))
            raise ValueError("missing full PSI")

        def swapout() -> int:
            for line in Path("/proc/vmstat").read_text().splitlines():
                if line.startswith("pswpout "):
                    return int(line.split()[1])
            raise ValueError("missing pswpout")

        first_psi = psi()
        before = swapout()
        time.sleep(1)
        second_psi = psi()
        pages = max(0, swapout() - before)
        mbps = pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except (OSError, ValueError, AttributeError, TypeError):
        return "cannot verify immediate host memory pressure"
    if max(first_psi, second_psi) >= 30 or (
            max(first_psi, second_psi) >= 10 and mbps >= 2):
        return "host actively stalled or writing swap; defer this launch attempt"
    return None


def _load_legacy_installer():
    """The constants are changed inside THIS subprocess only, not V1's unit."""
    spec = importlib.util.spec_from_file_location("fleet_next_shared_installer",
                                                  _HERE / "install.py")
    if spec is None or spec.loader is None:
        raise Refused("shared installer module cannot be loaded")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.UNIT_NAME = UNIT
    mod.UNIT_TEMPLATE = TEMPLATE
    mod.SERVICE_NAME = "serveros-fleet-example-mcp"
    mod.COMPOSITION = COMPOSITION
    return mod


def _effective_limit_errors(mod) -> list[str]:
    """Inspect live systemd values. A template alone cannot prove isolation."""
    info = mod.systemctl(
        "show", UNIT, "-p", "MemoryMax", "-p", "MemorySwapMax",
        "-p", "TasksMax", "-p", "CPUQuotaPerSecUSec", check=False)
    if info.returncode != 0:
        return ["systemd limits could not be read back"]
    props = dict(line.split("=", 1) for line in info.stdout.splitlines() if "=" in line)
    problems = []
    for key, wanted in (
        ("MemoryMax", BUDGET["memory_max_mib"] * 1024 * 1024),
        ("MemorySwapMax", 0),
        ("TasksMax", BUDGET["tasks_max"]),
    ):
        try:
            if int(props.get(key, "")) != wanted:
                problems.append(key + " not enforced")
        except ValueError:
            problems.append(key + " unavailable")
    # systemd normally reports CPUQuotaPerSecUSec as e.g. 400ms.
    quota = props.get("CPUQuotaPerSecUSec", "")
    matched = re.fullmatch(r"(\d+)(us|ms|s)", quota.strip())
    if not matched:
        problems.append("CPUQuotaPerSecUSec unavailable")
    else:
        num = int(matched.group(1))
        micros = num * {"us": 1, "ms": 1000, "s": 1000000}[matched.group(2)]
        if micros != BUDGET["cpu_quota_pct"] * 10000:
            problems.append("CPUQuotaPerSecUSec not enforced")
    return problems


def _git(source: Path, *args: str) -> str | None:
    """Read-only git query. `None` means the question could not be answered.

    An empty string is a real answer — `status --porcelain` on a clean tree
    prints nothing — so the two are kept distinct rather than collapsed.
    """
    try:
        done = subprocess.run(["git", "-C", str(source), *args],
                              capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return done.stdout


def _source_state(source: str) -> tuple[str | None, str | None]:
    """The revision a refresh would publish, or why the source is untrustworthy.

    Two refusals here, and both are about the same thing: a revision name is a
    claim about bytes. If the tree has uncommitted edits, the bytes copied into
    the revision directory are not the bytes `HEAD` names. If another git
    process holds the index, the tree is mid-write and can change under the
    copy. Either way the artifact would carry a SHA it does not honestly stand
    for, so this refuses rather than publishing a revision that lies.
    """
    path = Path(source)
    if not path.is_dir():
        return None, f"--source is not a directory: {source}"
    revision = _git(path, "rev-parse", "HEAD")
    if not revision or not revision.strip():
        return None, f"cannot read a git revision from {source}"
    dirty = _git(path, "status", "--porcelain")
    if dirty is None:
        return None, f"cannot read the working-tree state of {source}"
    if dirty.strip():
        count = len(dirty.splitlines())
        return None, (f"--source has {count} uncommitted path(s); a staged "
                      f"revision would carry a SHA those bytes do not match")
    gitdir = _git(path, "rev-parse", "--absolute-git-dir")
    if gitdir and gitdir.strip():
        lock = Path(gitdir.strip()) / "index.lock"
        if lock.exists():
            return None, (f"another git process holds {lock}; the tree can "
                          f"change during the copy")
    return revision.strip(), None


#: The enablement states in which the Next unit cannot start on its own.
#:
#: `disabled` is the explicit case. `not-found` is a unit FILE that does not
#: exist yet — the state of every host that has never installed Next, and the
#: state in which `systemctl --user is-enabled` answers `not-found` rather than
#: `disabled` (verified: rc=4, stdout `not-found`). It is strictly SAFER than
#: `disabled`, because a unit that does not exist cannot be started at all.
#: Refusing it would refuse the very first `stage` — the command that creates
#: the unit file — so it is accepted, not treated as an unknown.
_NEVER_STARTS_BY_ENABLEMENT = ("disabled", "not-found")


def _next_unit_enablement(mod) -> tuple[str | None, str | None]:
    """The Next unit's enablement, or why a revision may not be published to it.

    Publishing a revision without starting it — by `stage`, by `install
    --no-start`, or by `refresh-stage` — is safe only while the unit cannot
    start on its own. `is-active` does not answer that question: a unit can be
    inactive and ENABLED at the same time. This account has `Linger=yes`, so an
    enabled unit is started by the user manager at boot/login with no further
    approval, and a revision published now would run later without an
    activation approval — the one outcome a non-starting flow exists to prevent.

    This is a distinct audit fact, not a restatement of activation: `stage` and
    `install --no-start` never inspected the unit at all, and an operator who
    left a unit enabled after an earlier `install` needs that named rather than
    silently worked around.

    Only the two states in `_NEVER_STARTS_BY_ENABLEMENT` are accepted; anything
    else — including an empty or unreadable answer — is refused. This guard
    fails closed.
    """
    try:
        result = mod.systemctl("is-enabled", UNIT, check=False)
    except (OSError, RuntimeError) as exc:
        return None, ("Next unit enablement could not be read ("
                      + type(exc).__name__ + "); refusing to publish blind")
    state = (result.stdout or "").strip()
    if state not in _NEVER_STARTS_BY_ENABLEMENT:
        return None, (
            f"Next unit is {state or 'unknown'!r}, not one of "
            f"{list(_NEVER_STARTS_BY_ENABLEMENT)}; an enabled unit is started "
            f"by the user manager at boot/login (Linger is on for this "
            f"account), so a revision published now would run later without a "
            f"separate activation approval")
    return state, None


def _next_unit_state(mod) -> tuple[str | None, str | None]:
    """The Next unit's activation AND enablement state, or why it may not be published to.

    Two facts are required and they are different facts; neither implies the
    other.

    `is-active` must be `inactive`. `active` is refused because repointing a
    running service's revision without restarting it is the one change a
    non-starting flow must never make. `failed` is refused for a different
    reason: a unit that already tried and did not come up is a fact an operator
    has to read, and silently repointing it would overwrite the evidence.

    Enablement is checked by `_next_unit_enablement`.

    All three non-starting publishes share this: `stage`, `install --no-start`
    and `refresh-stage`. The wording below therefore speaks of publishing, not
    of refreshing — an operator running `stage` must not be told about a verb
    they did not run.

    Anything unreadable, and anything that is not the exact expected string, is
    refused: an unknown state is not a safe one, and this guard fails closed.
    """
    try:
        result = mod.systemctl("is-active", UNIT, check=False)
    except (OSError, RuntimeError) as exc:
        return None, ("Next unit state could not be read ("
                      + type(exc).__name__ + "); refusing to publish blind")
    state = (result.stdout or "").strip()
    if state != "inactive":
        return None, (f"Next unit is {state or 'unknown'!r}, not 'inactive'; "
                      f"only an inactive Next may be published to without "
                      f"starting it")
    _, problem = _next_unit_enablement(mod)
    if problem:
        return None, problem
    return state, None


def one_receipt(text: str) -> tuple[dict | None, str | None]:
    """The legacy installer's stdout as EXACTLY ONE machine receipt.

    `install.py::install()` prints one JSON object on both of its success
    paths — `--no-start` and a full start — and `rollback()` prints one too.
    The wrapper therefore has something specific to require, and this is where
    it says what that is.

    Returns `(payload, None)` on a single well-formed object, or
    `(None, reason)` otherwise. The reason is always a statement about the
    OUTPUT, never about the system: an unreadable receipt cannot tell us
    whether the command took effect, and the caller must not turn it into
    either a success or a clean failure.
    """
    stripped = text.strip()
    if not stripped:
        return None, "the installer printed no receipt"
    decoder = json.JSONDecoder()
    found: list = []
    index = 0
    while index < len(stripped):
        while index < len(stripped) and stripped[index].isspace():
            index += 1
        if index >= len(stripped):
            break
        try:
            obj, end = decoder.raw_decode(stripped, index)
        except ValueError:
            return None, "the installer's receipt is not valid JSON"
        found.append(obj)
        index = end
    if len(found) != 1:
        return None, (f"the installer printed {len(found)} JSON documents; "
                      f"exactly one Next-only receipt is required")
    if not isinstance(found[0], dict):
        return None, "the installer's receipt is not a JSON object"
    return found[0], None


def _real_path_isolation(proposal: dict) -> None:
    """Resolve symlinks at execution time so lexical separation cannot lie."""
    for next_key in gate.PATH_FIELDS:
        nxt = Path(proposal["next"][next_key]).resolve()
        for old_key in gate.PATH_FIELDS:
            v1 = Path(proposal["v1"][old_key]).resolve()
            if nxt == v1 or nxt in v1.parents or v1 in nxt.parents:
                raise Refused("Next path resolves inside V1; refusing effect")


def _tls_readback(host: str, *, timeout: float = 10.0, port: int = 443) -> dict:
    """A REAL handshake with the public origin, not a flag a proposal can set.

    `ssl.create_default_context()` verifies the chain against the system trust
    store AND checks the hostname, so a `verified` result is the conjunction of
    both — it is not a report about a configuration file. The SAN list is read
    back from the certificate that was actually served, which is what makes
    "the certificate names this host" a measurement.

    Every failure mode is returned as `verified: False` with a reason the
    caller can print. A readback that could not be taken is NEVER a pass, and
    it is never reported as a 200 anywhere upstream.
    """
    import socket
    import ssl

    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                cert = tls.getpeercert() or {}
    except (OSError, ssl.SSLError, ValueError) as exc:
        return {"host": host, "verified": False, "reason": type(exc).__name__}
    sans = sorted(value for kind, value in cert.get("subjectAltName", ()) if kind == "DNS")
    return {
        "host": host,
        "verified": True,
        "subject_alt_names": sans,
        "not_after": cert.get("notAfter"),
        "issuer": [dict(part) for part in cert.get("issuer", ())][-1:]
                            if cert.get("issuer") else [],
    }


def _http_status(url: str, *, timeout: float = 8.0) -> int | None:
    """The status line for one URL, or `None` when the question went unanswered.

    `None` and a status are different answers and are kept apart: an
    unanswerable probe must not be folded into a 200, and it must not be
    fabricated into a number the caller can pass to a threshold.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _tree_sha256(files: dict) -> str:
    """`install.py::manifest_of`'s tree hash, recomputed from a file list.

    A mirror, and a mirror of a hash is worth exactly as much as the proof that
    it is the same function. The test suite asserts this against
    `install.manifest_of` on a real tree, so a change to the formula upstream
    fails a test here rather than quietly making every staged revision look
    corrupt — or, worse, making a corrupt one look sound.
    """
    return hashlib.sha256(
        "\n".join(f"{name} {digest}" for name, digest in sorted(files.items())).encode()
    ).hexdigest()


def _staged_revision(mod, root: Path) -> tuple[dict | None, str | None]:
    """The revision `current` names, or why it may not be started.

    Three refusals, all about the same claim: `current` names bytes. A missing
    link means nothing is staged. A link that does not resolve, or resolves to
    something that is not a directory, means the name points at nothing. And a
    manifest that does not describe the tree it sits in means the revision is
    not the artifact its own name says it is — which is exactly the state a
    partial copy or an interrupted write leaves behind, and the one a start
    must not paper over by starting it anyway.

    The tree hash is RECOMPUTED rather than read back, because reading the
    manifest's own hash only proves the manifest agrees with itself.
    """
    current = root / "current"
    if not current.is_symlink():
        return None, (f"no staged Next revision at {current}; a canary start "
                      f"starts what `stage` published and publishes nothing "
                      f"itself")
    try:
        target = current.resolve()
    except OSError:
        return None, f"the staged Next revision link at {current} is unreadable"
    if not target.is_dir():
        return None, f"the staged Next revision {target} is not a directory"
    manifest_path = target / MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, (f"the staged Next revision {target} carries no {MANIFEST}; "
                      f"it is not an artifact this wrapper may start")
    except (OSError, ValueError) as exc:
        return None, (f"the manifest of the staged Next revision {target} is "
                      f"unreadable ({type(exc).__name__})")
    if not isinstance(manifest, dict) or manifest.get("schema") != ARTIFACT_SCHEMA:
        return None, (f"the manifest of the staged Next revision {target} does "
                      f"not declare schema {ARTIFACT_SCHEMA!r}")
    recorded = manifest.get("tree_sha256")
    if not isinstance(recorded, str) or not re.fullmatch(r"[0-9a-f]{64}", recorded):
        return None, (f"the manifest of the staged Next revision {target} carries "
                      f"no usable tree_sha256")
    recorded_files = manifest.get("files")
    if not isinstance(recorded_files, dict) or not recorded_files:
        return None, (f"the manifest of the staged Next revision {target} carries "
                      f"no file list, so its tree hash names nothing")
    # The manifest is written AFTER `manifest_of()` hashed the tree, so it is
    # absent from its own file list. Re-hashing the directory as it stands now
    # would therefore never agree with a healthy revision, and a check that can
    # only fail is not a check — it is the same class of defect the wrapper
    # refuses elsewhere. The manifest is dropped from the recomputation, not
    # from the comparison.
    actual = dict(mod.manifest_of(target)["files"])
    actual.pop(MANIFEST, None)
    if actual != recorded_files:
        missing = sorted(set(recorded_files) - set(actual))
        unlisted = sorted(set(actual) - set(recorded_files))
        altered = sorted(name for name in set(actual) & set(recorded_files)
                         if actual[name] != recorded_files[name])
        return None, (f"the staged Next revision {target} does not match its own "
                      f"manifest: {len(missing)} listed file(s) absent, "
                      f"{len(unlisted)} file(s) unlisted, {len(altered)} altered. "
                      f"These bytes are not the artifact the revision names")
    if _tree_sha256(recorded_files) != recorded:
        return None, (f"the manifest of the staged Next revision {target} records "
                      f"a tree hash that is not the hash of its own file list")
    return {"path": str(target), "tree_sha256": recorded,
            "files": len(recorded_files)}, None


def _next_binding(config_path: Path) -> tuple[dict | None, str | None]:
    """The binding as the SERVICE ITSELF reads it, or why it may not be started.

    Deliberately the real loader and not a private re-parse: the question is
    whether the Next service will accept this binding at startup, and only
    `fleet_mcp.config.load_config` answers that. A wrapper-side YAML read that
    agreed with itself while the service refused would produce the worst
    receipt of all — a start reported against a process that exited.

    The refusal carries the exception TYPE, never a value from the file. The
    loader's own message goes to stderr, where the operator can read it and a
    published receipt cannot.
    """
    try:
        from fleet_mcp.config import load_config  # noqa: PLC0415 - lazy on purpose
    except Exception as exc:  # pragma: no cover - an unimportable package
        return None, (f"the binding loader is unavailable ({type(exc).__name__}); "
                      f"refusing to start a service whose configuration could "
                      f"not be validated")
    try:
        config = load_config(config_path)
    except Exception as exc:
        sys.stderr.write(f"next_install.py: {type(exc).__name__}: "
                         f"{getattr(exc, 'detail', '')}\n")
        return None, (f"the Next binding at {config_path} is not acceptable to the "
                      f"service ({type(exc).__name__}); the service would refuse "
                      f"it at startup")
    return {
        "device_ref": config.local_device_ref,
        "bind": config.server.bind,
        "port": config.server.port,
        "mcp_path": config.server.mcp_path,
        "public_base_url": config.server.public_base_url,
        "pat_anchor": config.auth.pat_anchor,
        "oauth_enabled": config.auth.oauth_enabled,
    }, None


def _pat_anchor_state(anchor: Path | None) -> tuple[dict | None, str | None]:
    """Whether the owner's token anchor is present and private — never its value.

    The token is the owner's, it is what makes the human consent step real, and
    this module does not read a byte of it. What a start can establish without
    it is the three things an owner has to be able to rely on: that the file is
    THERE (otherwise the OAuth issuer builds nothing and the public surface
    degrades silently), that it is a REGULAR FILE and not a symlink (a symlink
    is how another deployment's token gets borrowed by accident), and that its
    mode is 0600 (a group- or world-readable owner token is a token that has
    already leaked).
    """
    if anchor is None:
        return None, ("auth.pat_anchor is unset in the Next binding; the OAuth "
                      "issuer refuses to build without it and the public surface "
                      "would come up degraded")
    if anchor.is_symlink():
        return None, (f"the owner token anchor {anchor} is a symlink; a start must "
                      f"not follow it to a file that may belong to another "
                      f"deployment")
    try:
        info = anchor.stat()
    except FileNotFoundError:
        return None, (f"the owner token anchor {anchor} does not exist; the owner "
                      f"supplies it, this tooling never creates it")
    except OSError as exc:
        return None, (f"the owner token anchor {anchor} is unreadable "
                      f"({type(exc).__name__})")
    if not anchor.is_file():
        return None, f"the owner token anchor {anchor} is not a regular file"
    mode = info.st_mode & 0o777
    if mode != PAT_ANCHOR_MODE:
        return None, (f"the owner token anchor {anchor} is mode {mode:04o}, not "
                      f"{PAT_ANCHOR_MODE:04o}")
    return {"path": str(anchor), "mode": f"{mode:04o}", "bytes": info.st_size}, None


def _canary_instance_errors(mod, before: dict) -> list[str]:
    """Exactly one Next instance, and the same V1 that was there before.

    "Exactly one" is asked of the unit rather than of a process list: the unit
    is the thing that was started, `MainPID` is its single main process, and a
    non-zero `NRestarts` means what is running is the tail of a restart loop
    rather than one supervised instance. V1 is compared by `MainPID` because
    "V1 still answers" is answered by a process that a cutover may have
    replaced with one that also answers.
    """
    info = mod.systemctl(
        "show", UNIT, "-p", "ActiveState", "-p", "MainPID", "-p", "NRestarts",
        check=False)
    if info.returncode != 0:
        return ["the Next unit state could not be read back"]
    props = dict(line.split("=", 1) for line in info.stdout.splitlines() if "=" in line)
    problems: list[str] = []
    if props.get("ActiveState") != "active":
        problems.append("the Next unit is not active after the start")
    try:
        main_pid = int(props.get("MainPID", ""))
        restarts = int(props.get("NRestarts", ""))
    except ValueError:
        return problems + ["the Next unit's MainPID/NRestarts are unavailable"]
    if main_pid <= 0:
        problems.append("the Next unit reports no main process")
    elif not Path(f"/proc/{main_pid}").is_dir():
        problems.append("the Next unit's main process is gone")
    if restarts != before.get("restarts"):
        problems.append("the Next unit restarted during the canary")
    return problems


def _public_base(public_mcp_url: str) -> str:
    """The origin a public MCP URL is served from. `/mcp` is a path, not a host."""
    base = public_mcp_url.rstrip("/")
    return base[: -len("/mcp")] if base.endswith("/mcp") else base


def _canary_preconditions(mod, proposal: dict) -> dict:
    """Every check that must pass BEFORE the Next unit is started, or a refusal.

    Ordered cheapest-and-most-fundamental first, so the operator is told about
    the configuration before being told about the host. Nothing here writes,
    starts, stops or enables anything: the caller takes that boundary itself,
    which is why this returns evidence rather than acting.
    """
    nxt = proposal["next"]

    # 1. The unit must be exactly the state a non-starting publish requires:
    #    inactive, and unable to start on its own. A canary start on an enabled
    #    unit would be an unsupervised start wearing a supervised name.
    _state, problem = _next_unit_state(mod)
    if problem:
        raise Refused(problem)

    # 2. Something reviewed must actually be staged, and its manifest must
    #    describe it. Starting bytes that are not the artifact they name is the
    #    failure a revision name exists to prevent.
    root = Path(nxt["runtime_root"])
    revision, problem = _staged_revision(mod, root)
    if problem:
        raise Refused(problem)

    # 3. The binding is read by the SERVICE's own loader, so "the service will
    #    accept it" is a measurement rather than a hope.
    binding, problem = _next_binding(Path(nxt["config_path"]))
    if problem:
        raise Refused(problem)
    if not binding["oauth_enabled"]:
        raise Refused("auth.oauth_enabled is false in the Next binding; a ChatGPT "
                      "connector cannot authenticate without it, and a canary "
                      "start has nothing else to bring up")
    if (binding["port"] != nxt["local_listen"]["port"]
            or binding["bind"] != nxt["local_listen"]["host"]):
        raise Refused("the Next binding the service reads does not match the "
                      "reviewed proposal's local_listen")

    # 4. V1 is read for two facts and changed for none: its own binding is used
    #    only to prove the two ports differ.
    old = mod.binding(Path(proposal["v1"]["config_path"]))
    if (old["port"] != proposal["v1"]["local_listen"]["port"]
            or old["bind"] != proposal["v1"]["local_listen"]["host"]):
        raise Refused("live V1 binding differs from the reviewed proposal")
    if binding["port"] == old["port"]:
        raise Refused("Next would reuse V1's local listening port")

    # 5. The owner's token: present, private, and not a borrowed one.
    anchor, problem = _pat_anchor_state(binding["pat_anchor"])
    if problem:
        raise Refused(problem)
    oauth_root = Path(binding["pat_anchor"]).parent / "fleet-mcp-oauth"
    for key in gate.PATH_FIELDS:
        v1_path = Path(proposal["v1"][key]).resolve()
        candidate = oauth_root.resolve()
        if (candidate == v1_path or candidate in v1_path.parents
                or v1_path in candidate.parents):
            raise Refused("the Next OAuth store would resolve inside a V1 root; "
                          "an owner token written there would be V1's")

    # 6. The public origin, MEASURED. A proposal cannot assert this: the
    #    certificate has to be served, chain to a trusted root and name this
    #    host, and the only way to know that is to ask for it.
    public_base = _public_base(str(nxt["public_mcp_url"]))
    host = public_base.split("://", 1)[-1].split("/", 1)[0]
    tls = _tls_readback(host)
    if not tls["verified"]:
        raise Refused(f"the public origin {host} did not complete a verified TLS "
                      f"handshake ({tls['reason']}); a canary start would publish "
                      f"a route with no certificate behind it")
    if host not in tls["subject_alt_names"]:
        raise Refused(f"the certificate served for {host} does not name {host} "
                      f"in its subjectAltName; it is another host's certificate")

    # 7. Live memory headroom, then V1's health, both immediately before start.
    reason = _current_pressure()
    if reason:
        raise Refused(reason)
    info = mod.systemctl("show", UNIT, "-p", "NRestarts", check=False)
    props = dict(line.split("=", 1) for line in info.stdout.splitlines() if "=" in line)
    try:
        restarts = int(props.get("NRestarts", ""))
    except ValueError:
        raise Refused("the Next unit's restart count is unavailable; a start "
                      "cannot be judged against an unknown baseline") from None
    before_v1 = mod.systemctl(
        "show", proposal["v1"]["service_unit"], "-p", "MainPID", check=False)
    v1_props = dict(line.split("=", 1)
                    for line in before_v1.stdout.splitlines() if "=" in line)
    try:
        v1_main_pid = int(v1_props.get("MainPID", ""))
    except ValueError:
        raise Refused("the V1 unit's MainPID is unreadable; a canary start must "
                      "know which V1 process it is promising not to lose") from None
    if mod.wait_for_health(old["probe"], 3.0) is None:
        raise Refused("V1 /health did not answer before the Next canary start; "
                      "do not risk a cutover")

    return {"unit_state": "inactive", "revision": revision, "binding": binding,
            "pat_anchor": anchor, "tls": tls, "restarts": restarts,
            "v1_main_pid": v1_main_pid, "v1_probe": old["probe"],
            "public_base": public_base}


def _canary_postcondition(mod, proposal: dict, before: dict, *,
                          health_timeout: float) -> dict:
    """What the started Next actually does, and what V1 is still doing.

    Every field is a measurement. A probe that could not be taken is `None` and
    is also a problem — the two are reported together so a reader cannot mistake
    an unanswerable question for a passing one. No status here is ever invented:
    in particular this function has no way to report a 200 it did not receive.
    """
    from fleet_mcp.version import (  # noqa: PLC0415 - authoritative route names
        AUTHORIZATION_SERVER_PATH,
        PROTECTED_RESOURCE_PATH,
    )

    nxt = proposal["next"]
    problems: list[str] = []
    problems.extend(_effective_limit_errors(mod))
    problems.extend(_canary_instance_errors(mod, before))

    probe = f"http://{nxt['local_listen']['host']}:{nxt['local_listen']['port']}"
    local_health = mod.wait_for_health(probe, health_timeout)
    if local_health is None:
        problems.append(f"the Next unit answered nothing on {probe}/health")
    public_base = before["public_base"]
    public = {
        "/health": _http_status(f"{public_base}/health"),
        PROTECTED_RESOURCE_PATH: _http_status(public_base + PROTECTED_RESOURCE_PATH),
        AUTHORIZATION_SERVER_PATH: _http_status(public_base + AUTHORIZATION_SERVER_PATH),
    }
    for name, status in public.items():
        if status != 200:
            problems.append(f"the public route {public_base}{name} answered "
                            f"{status!r}, not 200")

    v1_after = mod.systemctl(
        "show", proposal["v1"]["service_unit"], "-p", "MainPID", check=False)
    v1_props = dict(line.split("=", 1)
                    for line in v1_after.stdout.splitlines() if "=" in line)
    try:
        v1_main_pid = int(v1_props.get("MainPID", ""))
    except ValueError:
        v1_main_pid = None
    if v1_main_pid != before["v1_main_pid"]:
        problems.append("V1's MainPID changed across the Next canary")
    if mod.wait_for_health(before["v1_probe"], 3.0) is None:
        problems.append("V1 /health stopped answering during the Next canary")

    return {
        "local_health": local_health,
        "public": public,
        "v1_main_pid": v1_main_pid,
        "v1_main_pid_unchanged": v1_main_pid == before["v1_main_pid"],
        # The MCP tool surface is an AUTHENTICATED readback (`tools/list` needs
        # an OAuth client or the owner token). It is deliberately not asserted
        # here: an unauthenticated canary cannot measure it, and a number this
        # module did not receive is not one it may report.
        "tool_declarations": None,
        "tool_declarations_reason": ("requires an authenticated MCP tools/list; "
                                     "not read by this unauthenticated canary"),
        "problems": problems,
    }


def _restore_failed_canary(mod, reason: str) -> None:
    """Stop ONLY Next; leave the staged revision and V1 exactly as they were.

    There are no pointers to restore, because a canary start moved none. What
    it changed is one activation state, and stopping is the inverse of that and
    of nothing else. The revision is deliberately left in place: it was
    reviewed, it is what the next attempt will start, and deleting it would
    destroy the evidence for the failure being reported.
    """
    stop_error = ""
    try:
        stopped = mod.systemctl("stop", UNIT, check=False)
        if stopped.returncode != 0:
            stop_error = " Next stop reported nonzero status."
    except (OSError, RuntimeError):
        stop_error = " Next stop could not be confirmed."
    raise Refused("the Next canary did not come up; Next-only stop attempted; "
                  "Next service state is UNKNOWN until read back: "
                  + reason + stop_error)


def _restore_failed_install(mod, root: Path, before: tuple, reason: str,
                            *, effect_attempted: bool) -> None:
    """Stop only Next, restore its previous pointers, never restart V1.

    Artifacts are intentionally retained for forensics. A recovered prior
    pointer is not an assurance the prior Next process is running.

    `effect_attempted` is supplied by the caller, which is the only party that
    knows it. Whether the `current` symlink MOVED is not the question: an
    install over an already-live unit takes the `restart` path, and a restart
    of the SAME revision leaves `current` byte-identical while replacing the
    process that serves it. Judging by symlink equality alone therefore
    reported "existing Next untouched" for the one case that most needs a
    rollback — a restart that was attempted and did not come back healthy —
    and left the unhealthy process running under a clean-sounding receipt.

    So: no effect attempted means nothing to undo and a healthy Next must not
    be touched. An effect attempted means Next-only stop plus a pointer restore
    if there is one to restore, and the Next service state is reported UNKNOWN
    unless a readback says otherwise.
    """
    if not effect_attempted:
        raise Refused("Next install failed before any effect was attempted; "
                      "existing Next untouched: " + reason)
    stop_error = None
    try:
        stopped = mod.systemctl("stop", UNIT, check=False)
        if stopped.returncode != 0:
            stop_error = " Next stop reported nonzero status."
    except (OSError, RuntimeError):
        stop_error = " Next stop could not be confirmed."
    try:
        if mod.read_link(root / "current") != before[0]:
            mod.point(root / "current", before[0])
            mod.point(root / "previous", before[1])
    except (OSError, ValueError) as exc:
        raise Refused("Next failed; could not restore Next revision links: "
                      + type(exc).__name__ + (";" + stop_error if stop_error else "")) from exc
    # A same-revision restart leaves no pointer to restore, so the receipt must
    # not imply the Next service is back to its previous state: it was stopped
    # and that is all that is known.
    raise Refused("Next failed, Next-only stop/rollback attempted; Next service "
                  "state is UNKNOWN until read back: " + reason + (stop_error or ""))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command",
                   choices=("stage", "refresh-stage", "install", CANARY_START,
                            "status", "rollback", "uninstall"))
    p.add_argument("--proposal", required=True, help="non-secret, freshly reviewed JSON")
    p.add_argument("--source", default=str(_ROOT))
    p.add_argument("--revision")
    p.add_argument("--label")
    p.add_argument("--no-start", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--owner-approved", action="store_true")
    p.add_argument("--health-timeout", type=float, default=20.0)
    args = p.parse_args(argv)
    effect_started = False
    #: Did anything need undoing? Distinct from `effect_started`, which only
    #: says the installer was INVOKED. A refusal that happens before the shared
    #: installer crosses its own effect boundary changes nothing, and stopping a
    #: healthy Next for it would be a self-inflicted outage.
    effect_attempted = False
    try:
        # This command's contract is ONE parseable document on stdout. A bound
        # check of ours belongs inside it, not in `p.error`, which prints usage
        # and no document at all. (`argparse`'s own errors — an unknown flag,
        # a missing `--proposal` — stay usage-level: they are about the shape of
        # the invocation, not about the host.)
        if args.health_timeout <= 0 or args.health_timeout > 60:
            raise Refused("--health-timeout must be within (0, 60]")
        proposal = _load_json(args.proposal)
        preview = plan(proposal, args.command, no_start=args.no_start)
        if preview["decision"] == "BLOCKED":
            print(json.dumps(preview, indent=2, sort_keys=True))
            return 2

        if not args.execute:
            print(json.dumps(preview, indent=2, sort_keys=True))
            return 0
        if not args.owner_approved:
            raise Refused("--execute requires explicit --owner-approved confirmation")
        # Consent is per-operator intent. It must not be replaced with a
        # platform-wide permission switch or inherited from the V1 token.
        _real_path_isolation(proposal)
        if args.command == CANARY_START:
            # The whole of the canary path, and it differs from `install` in
            # what it does NOT do: no revision is copied, no `current`/`previous`
            # pointer moves, no unit file is written and the unit is not
            # `enable`d. The only system action is one `systemctl start`.
            mod = _load_legacy_installer()
            before = _canary_preconditions(mod, proposal)
            # EFFECT BOUNDARY. Everything above only read; from here a failure
            # must undo Next and must not touch V1 or the staged revision.
            effect_attempted = True
            effect_started = True
            try:
                started = mod.systemctl("start", UNIT, check=False)
                if started.returncode != 0:
                    _restore_failed_canary(mod, "systemctl start reported "
                                                "nonzero status")
                after = _canary_postcondition(mod, proposal, before,
                                              health_timeout=args.health_timeout)
            except Refused:
                raise
            except (SystemExit, Exception) as exc:  # noqa: BLE001 - see below
                # The start or the readback died. `install.py` treats a death of
                # unknown timing as an attempted effect and this does the same,
                # for the same reason: the unit may be running the revision right
                # now, and "it probably did not start" is not a state.
                _restore_failed_canary(mod, f"{type(exc).__name__} during start "
                                            f"or readback")
            if after["problems"]:
                _restore_failed_canary(mod, "; ".join(after["problems"]))
            print(json.dumps({
                "decision": "EXECUTED",
                "command": args.command,
                "unit": UNIT,
                "mode": "NEXT_ONLY_CANARY_START",
                "effect_state": "NEXT_ONLY_ACTION_ATTEMPTED",
                "writes": [],
                "executes": [UNIT],
                "v1_action": False,
                "enabled": False,
                "revision": before["revision"]["path"],
                "tree_sha256": before["revision"]["tree_sha256"],
                "binding": {"device_ref": before["binding"]["device_ref"],
                            "bind": before["binding"]["bind"],
                            "port": before["binding"]["port"],
                            "public_base_url": before["binding"]["public_base_url"]},
                "owner_token_anchor": before["pat_anchor"],
                "tls": before["tls"],
                "local_health": after["local_health"],
                "public_readback": after["public"],
                "v1_main_pid": after["v1_main_pid"],
                "v1_main_pid_unchanged": after["v1_main_pid_unchanged"],
                "tool_declarations": after["tool_declarations"],
                "tool_declarations_reason": after["tool_declarations_reason"],
            }, indent=2, sort_keys=True))
            return 0
        if args.command == "install" and not args.no_start:
            reason = _current_pressure()
            if reason:
                raise Refused(reason)

        mod = _load_legacy_installer()
        nxt = proposal["next"]
        runtime_root = str(Path(nxt["runtime_root"]).parent)
        args_list = ["install" if args.command in STAGING_COMMANDS else args.command,
                     "--runtime-root", runtime_root,
                     "--composition", COMPOSITION, "--config", nxt["config_path"],
                     "--health-timeout", str(args.health_timeout)]
        if args.command in STAGING_COMMANDS or args.command == "install":
            args_list.extend(["--source", args.source])
            if args.revision:
                # Refuse a caller-named revision the source's HEAD disagrees with
                # HERE, before any effect — not only inside the shared installer.
                # The installer refuses it too, but it refuses by DYING, and this
                # wrapper treats a dying installer as an attempted effect (it may
                # have died after writing) and runs a Next-only stop/rollback.
                # That is the right default for a death of unknown timing, but it
                # is the wrong answer for a mistyped SHA: a validation refusal
                # must not stop a healthy Next. `refresh-stage` checks the same
                # thing against its own clean-source rules a little further down.
                # A source that is not a git repository has no HEAD to disagree
                # with, and `--revision` stays its only route.
                if args.command in ("stage", "install"):
                    head = mod.git_revision(Path(args.source).expanduser().resolve())
                    if head and args.revision != head:
                        raise Refused(
                            f"--revision {args.revision!r} is not the source's HEAD "
                            f"{head!r}; a revision name records which bytes the "
                            f"artifact was built from, and no system action has "
                            f"been taken")
                args_list.extend(["--revision", args.revision])
            if args.command in ("stage", "install"):
                # Same reasoning as the revision check above, and it applies
                # whether or not `--revision` was given: the shared installer
                # refuses a dirty tree by DYING, and a dying installer is read
                # as an attempted effect — so a dirty source would stop a
                # healthy Next over a validation refusal. Taken here, before any
                # system action.
                #
                # Called UNGATED. `tree_state` itself answers None for a source
                # that is not a work tree, so the `--revision` route for a
                # non-git artifact stays open; and it does NOT answer None for a
                # repository with an unborn HEAD, which is the tree that has
                # bytes no commit describes. Gating here on a readable HEAD — as
                # an earlier revision did, mirroring `install.py` — quietly
                # exempted exactly that case.
                source = Path(args.source).expanduser().resolve()
                problem = mod.tree_state(source)
                if problem:
                    raise Refused(problem + " No system action has been taken.")
            if args.label:
                args_list.extend(["--label", args.label])
            # `--force` is never passed, so the shared installer's own
            # immutability rule stands: a revision directory that already
            # exists is refused rather than replaced.
            if args.no_start or args.command in STAGING_COMMANDS:
                args_list.append("--no-start")

            # The Next and V1 binding files must be ACTUALLY distinct today,
            # not merely separate addresses typed into a JSON proposal.
            old = mod.binding(Path(proposal["v1"]["config_path"]))
            info = mod.binding(Path(nxt["config_path"]))
            if (old["port"] != proposal["v1"]["local_listen"]["port"]
                    or old["bind"] != proposal["v1"]["local_listen"]["host"]):
                raise Refused("live V1 binding differs from reviewed proposal")
            if (info["port"] != nxt["local_listen"]["port"]
                    or info["bind"] != nxt["local_listen"]["host"]):
                raise Refused("Next binding differs from reviewed proposal")
            if info["port"] == old["port"]:
                raise Refused("Next would reuse V1's local listening port")
            if args.command == "install" and not args.no_start and not mod.wait_for_health(old["probe"], 3.0):
                raise Refused("V1 health unavailable before Next start; do not risk cutover")

        # Every destructive operation is addressed to the Next unit ONLY.
        # The wrapper does not grant OAuth, register a plugin or change V1.
        root = Path(nxt["runtime_root"])
        prior = (mod.read_link(root / "current"), mod.read_link(root / "previous"))
        if args.command == "stage" and prior[0] is not None:
            raise Refused("bootstrap stage requires an empty Next revision root; "
                          "do not repoint an existing Next instance")
        if args.command == "stage":
            # The empty root establishes that nothing is SERVING from this tree.
            # It establishes nothing about the unit, and `stage` used to read no
            # unit state at all. Two distinct ways that bites:
            #   * a unit left ENABLED by an earlier `install` (its revision tree
            #     since emptied) is started by the user manager at boot/login
            #     (Linger is on), so the revision published here would run with
            #     no activation approval;
            #   * a unit in `activating (auto-restart)` (the template sets
            #     `Restart=on-failure`) re-evaluates `ExecStart` on every attempt,
            #     so it can pick the revision up within `RestartSec` — while
            #     `is-enabled` still reads `disabled`.
            # Both are answered by the same rule `refresh-stage` uses: inactive
            # AND unable to start on its own.
            state, problem = _next_unit_state(mod)
            if problem:
                raise Refused(problem)
        if args.command == "install" and args.no_start:
            # `install --no-start` is a THIRD non-starting publish: it writes the
            # revision and the unit file and starts nothing. It is therefore in
            # exactly the same hazard class as `stage`, and reading no unit
            # state here would leave the invariant this file states — a
            # non-starting publish is safe only while the unit cannot start
            # itself — true for two of the three verbs that do it.
            state, problem = _next_unit_state(mod)
            if problem:
                raise Refused(problem)
        if args.command == "refresh-stage":
            # A refresh is the ONE flow that deliberately moves `current`, so
            # every guard that makes that safe is checked here, before any
            # write: nothing above this line has touched the system.
            if prior[0] is None:
                raise Refused(
                    "refresh-stage requires an existing Next revision; the "
                    "revision root is empty — use `stage` for the first one")
            state, problem = _next_unit_state(mod)
            if problem:
                raise Refused(problem)
            revision, problem = _source_state(args.source)
            if problem:
                raise Refused(problem)
            # An explicit `--revision` may only RESTATE the revision the clean
            # source actually is. It is not an override: a caller-chosen SHA
            # would stamp these bytes with a name `HEAD` does not give them,
            # which is the one thing a revision name must never do. A mismatch
            # is refused rather than silently ignored — ignoring it would
            # publish a revision under a name the caller did not ask for.
            if args.revision and args.revision != revision:
                # Both values are printed in full: a short SHA is refused, and
                # truncating them would print two identical strings for a
                # refusal that is precisely about them not being identical.
                raise Refused(
                    f"--revision {args.revision!r} is not the clean source's HEAD "
                    f"{revision!r}; an explicit --revision may only restate HEAD "
                    f"byte-for-byte, so a short or different SHA is refused")
            # `install.py` derives the revision the same way; mirroring it is
            # what makes this a statement about the revision that will actually
            # be written rather than about a different one.
            target = revision
            current_rev = prior[0].name.rsplit("@", 1)[-1]
            if target[:7] == current_rev:
                raise Refused(
                    f"the revision named for staging ({target[:7]}) is already "
                    f"`current`; a refresh must name a different revision — "
                    f"re-pointing to the same one would consume `previous` "
                    f"without changing what runs")
        effect_started = True  # no longer a dry-run, even if the install fails
        # The shared installer writes this marker the instant it is about to
        # change the host, and clears it when it finishes cleanly. Clearing it
        # first makes it a statement about THIS attempt; reading it after a
        # failure is the only honest way to tell a refusal that must be left
        # alone from a run that must be rolled back. The exit code cannot say it
        # — `systemctl` can fail *after* `current` moved — and neither can the
        # symlinks, because a restart of the SAME revision leaves them
        # byte-identical. Whether `current` moved is not the question.
        effect_marker = root / mod.EFFECT_MARKER
        effect_marker.unlink(missing_ok=True)
        # The legacy installer's own stdout is CAPTURED, not forwarded. It
        # prints one JSON receipt per successful operation and the wrapper
        # republishes exactly one Next-only receipt — so a caller parses a
        # single document whichever command it ran, and cannot be shown two
        # conflicting ones. `mod.main` may also die after printing, which is
        # why the capture outlives the call.
        captured = io.StringIO()
        try:
            with contextlib.redirect_stdout(captured):
                result = mod.main(args_list)
        except (SystemExit, Exception) as exc:
            if args.command in STAGING_COMMANDS or args.command == "install":
                # The installer's own marker answers "did it get past the point
                # of no return?". A refusal BEFORE its effect boundary leaves no
                # marker, wrote nothing, moved nothing and stopped nothing — so
                # a healthy Next must not be stopped for it.
                #
                # `Exception` is caught with `SystemExit` on purpose: a
                # `systemctl` that is missing from the installer's PATH, an
                # `OSError` writing the unit, an `ENOSPC` on `os.replace` are
                # all failures that can land AFTER the boundary, and they used
                # to escape as a traceback — no receipt and, worse, no recovery
                # for a run that had already moved `current`.
                effect_attempted = effect_marker.exists()
                payload, problem = one_receipt(captured.getvalue())
                if payload is not None:
                    detail = (f" The installer's own receipt said started="
                              f"{payload.get('started')!r}, health={payload.get('health')!r}.")
                elif problem:
                    detail = f" It also printed no usable receipt ({problem})."
                else:
                    detail = ""
                if not isinstance(exc, SystemExit):
                    detail += f" The installer raised {type(exc).__name__}."
                _restore_failed_install(
                    mod, root, prior,
                    "installer exited nonzero; status must be inspected." + detail,
                    effect_attempted=effect_attempted)
            raise
        if args.command in STAGING_COMMANDS or args.command == "install":
            # Reaching here means `mod.main` RETURNED. A zero return means the
            # installer ran to completion and cleared its own marker; a nonzero
            # return means it stopped early, and only the marker says how far.
            effect_attempted = (result == 0) or effect_marker.exists()
            if result != 0:
                _restore_failed_install(mod, root, prior, "installer returned nonzero",
                                        effect_attempted=effect_attempted)
        if args.command == "install" and not args.no_start:
            faults = _effective_limit_errors(mod)
            if faults:
                _restore_failed_install(mod, root, prior,
                                        "cgroup limits not enforced: " + ", ".join(faults),
                                        effect_attempted=True)
            if not mod.wait_for_health(old["probe"], 3.0):
                _restore_failed_install(mod, root, prior,
                                        "V1 /health unavailable after Next launch",
                                        effect_attempted=True)

        # Success is a RECEIPT, not an exit code. A command that returned 0
        # without leaving one parseable Next-only report has not told us what
        # it did, and "it probably worked" is not a state this wrapper reports.
        payload, problem = one_receipt(captured.getvalue())
        if problem is not None:
            print(json.dumps({
                "decision": "UNKNOWN",
                "command": args.command,
                "unit": UNIT,
                "reason": problem,
                "effect_state": "NEXT_ONLY_ACTION_ATTEMPTED",
                "writes": "POSSIBLE_OR_ROLLED_BACK",
                "executes": [UNIT],
                "v1_action": False,
                "receipt": None,
            }, indent=2, sort_keys=True))
            return 4
        # Read the revision pointers back rather than trusting the installer's
        # description of them: `previous` holding the OLD `current` is the
        # whole rollback story, and it is a filesystem fact, not a promise.
        after = (mod.read_link(root / "current"), mod.read_link(root / "previous"))
        print(json.dumps({
            "decision": "EXECUTED",
            "command": args.command,
            "unit": UNIT,
            "effect_state": "NEXT_ONLY_ACTION_ATTEMPTED",
            "writes": [str(Path(nxt["runtime_root"]))],
            "executes": [UNIT],
            "v1_action": False,
            "started": payload.get("started"),
            "health": payload.get("health"),
            "unit_path": payload.get("unit_path"),
            "revision": payload.get("revision"),
            "current": str(after[0]) if after[0] else None,
            "previous": str(after[1]) if after[1] else None,
            "receipt": payload,
        }, indent=2, sort_keys=True))
        return result
    except Refused as exc:
        # Never report writes=[] after attempting an install/restart and
        # perhaps a Next-only rollback. The outcome may be partial/unknown.
        # `effect_attempted`, not `effect_started`: a refusal that never reached
        # the installer's effect boundary wrote nothing, and reporting a
        # possible write for it is the same overstatement in the other
        # direction.
        print(json.dumps({
            "decision": "BLOCKED",
            "reason": str(exc),
            "effect_state": ("NEXT_ONLY_ACTION_ATTEMPTED" if effect_attempted
                             else "NO_SYSTEM_ACTION_STARTED"),
            "writes": ("POSSIBLE_OR_ROLLED_BACK" if effect_attempted else []),
            "executes": ([UNIT] if effect_attempted else []),
            "v1_action": False,
        }, sort_keys=True))
        return 2
    except (SystemExit, Exception) as exc:
        # A HELPER of the shared installer can refuse before any effect by
        # dying — `install.py::binding()` does exactly that when the Next
        # binding file is absent, and it is reached from the cross-check that
        # runs before the installer is ever driven. Nothing was written, nothing
        # was started or stopped, and the caller still gets the ONE document
        # this contract promises; without this the execute path printed no JSON
        # at all, which is indistinguishable from a crash.
        #
        # `Exception` rides along for the same reason: this command's contract
        # is one parseable document, and a traceback is not one. The exception
        # TYPE is reported and its message is not — a message can carry paths,
        # and a caller that needs the detail has it on stderr.
        #
        # Once `effect_started` is set, the inner handler has already reported
        # and the exception must propagate unchanged.
        if effect_started:
            # An exception from a region the INNER handler does not wrap: the
            # wrapper's own post-start verification, or the marker clear — with
            # the installer already driven. Re-raising printed no document at
            # all, and "no document" is indistinguishable from a crash; worse,
            # the post-start verification runs after a COMPLETED install, so the
            # caller was left with neither a receipt nor a reason.
            #
            # The recovery verbs keep their existing no-document behaviour:
            # their effect tracking is the recorded residue, and silently
            # reporting "no action" for a rollback that may have moved `current`
            # would be the same overstatement in the other direction.
            if args.command in ("rollback", "status"):
                raise
            print(json.dumps({
                "decision": "BLOCKED",
                "reason": (f"the wrapper failed after driving the installer "
                           f"({type(exc).__name__}); the receipt could not be "
                           f"completed, so the outcome is unknown"),
                "effect_state": ("NEXT_ONLY_ACTION_ATTEMPTED" if effect_attempted
                                 else "NO_SYSTEM_ACTION_STARTED"),
                "writes": ("POSSIBLE_OR_ROLLED_BACK" if effect_attempted else []),
                "executes": ([UNIT] if effect_attempted else []),
                "v1_action": False,
                "refusal_exception": type(exc).__name__,
            }, sort_keys=True))
            return 2
        print(json.dumps({
            "decision": "BLOCKED",
            "reason": ("a pre-effect check refused before anything was written, "
                       "started or stopped; no system action was taken"),
            "effect_state": "NO_SYSTEM_ACTION_STARTED",
            "writes": [],
            "executes": [],
            "v1_action": False,
            "refusal_exit": getattr(exc, "code", None),
            "refusal_exception": type(exc).__name__,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
