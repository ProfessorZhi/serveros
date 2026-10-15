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
TEMPLATE = "fleet_mcp/deploy/serveros-fleet-next-mcp.service"
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

BUDGET = {
    "memory_max_mib": 1536,
    "memory_swap_max_mib": 0,
    "cpu_quota_pct": 40,
    "tasks_max": 96,
}


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


def plan(proposal: dict, command: str) -> dict:
    if command == "uninstall":
        return {"decision": "BLOCKED",
                "blockers": ["Next uninstall requires separate reviewed retirement"],
                "writes": [], "executes": []}
    recovery = command in ("rollback", "status")
    bootstrap = command in STAGING_COMMANDS
    refresh = command == "refresh-stage"
    preflight = gate.evaluate(proposal, recovery=recovery, bootstrap=bootstrap)
    ready = ("RECOVERY_PLAN_READY" if recovery else
             "BOOTSTRAP_PLAN_READY" if bootstrap else "PRECHECK_READY_FOR_HUMAN_CANARY")
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
        template = _HERE / "serveros-fleet-next-mcp.service"
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
        "mode": "NEXT_ONLY_RECOVERY" if recovery else
                "NEXT_ONLY_REFRESH_STAGE_NO_START" if refresh else
                "NEXT_ONLY_STAGE_NO_START" if bootstrap else "NEXT_CANARY_INSTALL",
        "command": command,
        "unit": UNIT,
        "composition": COMPOSITION,
        "same_as_v1": False,
        "limits": dict(BUDGET) if not recovery else None,
        "writes": [], "executes": [],
        "requires": [
            ("Owner-reviewed Next-only binding; public URL and Plugin ID may "
             "remain unassigned only for this non-starting bootstrap stage"
             if bootstrap else
             "Next owner authorization and distinct real App/Plugin registration"),
            ("An INACTIVE and DISABLED Next unit, a clean `--source`, and a "
             "revision that differs from the one `current` already names; an "
             "explicit `--revision` must restate the clean HEAD byte-for-byte; "
             "the revision being replaced is kept as `previous`"
             if refresh else
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
    mod.SERVICE_NAME = "serveros-fleet-next-mcp"
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


def _next_unit_state(mod) -> tuple[str | None, str | None]:
    """The Next unit's activation AND enablement state, or why it may not be refreshed.

    Two facts are required and they are different facts; neither implies the
    other.

    `is-active` must be `inactive`. `active` is refused because repointing a
    running service's revision without restarting it is the one change a
    non-starting flow must never make. `failed` is refused for a different
    reason: a unit that already tried and did not come up is a fact an operator
    has to read, and silently repointing it would overwrite the evidence.

    `is-enabled` must be `disabled`. This is NOT implied by `inactive` — a unit
    can be inactive and enabled at the same time — and the account that runs
    Next has `Linger=yes`, so an ENABLED unit is started by the user manager at
    boot/login with no further approval. Repointing it would therefore run the
    moved revision the next time the manager restarts, which is exactly the
    activation this non-starting flow exists to avoid.

    Anything unreadable, and anything that is not the exact expected string, is
    refused: an unknown state is not a safe one, and this guard fails closed.
    """
    try:
        result = mod.systemctl("is-active", UNIT, check=False)
    except (OSError, RuntimeError) as exc:
        return None, ("Next unit state could not be read ("
                      + type(exc).__name__ + "); refusing to refresh blind")
    state = (result.stdout or "").strip()
    if state != "inactive":
        return None, (f"Next unit is {state or 'unknown'!r}, not 'inactive'; "
                      f"only an inactive Next may be refreshed without starting it")
    try:
        enabled = mod.systemctl("is-enabled", UNIT, check=False)
    except (OSError, RuntimeError) as exc:
        return None, ("Next unit enablement could not be read ("
                      + type(exc).__name__ + "); refusing to refresh blind")
    enablement = (enabled.stdout or "").strip()
    if enablement != "disabled":
        return None, (
            f"Next unit is {enablement or 'unknown'!r}, not 'disabled'; an "
            f"enabled unit is started by the user manager at boot/login "
            f"(Linger is on for this account), so the repointed revision would "
            f"run without a separate activation approval")
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
                   choices=("stage", "refresh-stage", "install", "status",
                            "rollback", "uninstall"))
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
    if args.health_timeout <= 0 or args.health_timeout > 60:
        p.error("--health-timeout must be within (0, 60]")
    try:
        proposal = _load_json(args.proposal)
        preview = plan(proposal, args.command)
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
                args_list.extend(["--revision", args.revision])
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
        except SystemExit:
            if args.command in STAGING_COMMANDS or args.command == "install":
                payload, problem = one_receipt(captured.getvalue())
                if payload is not None:
                    detail = (f" The installer's own receipt said started="
                              f"{payload.get('started')!r}, health={payload.get('health')!r}.")
                elif problem:
                    detail = f" It also printed no usable receipt ({problem})."
                else:
                    detail = ""
                _restore_failed_install(
                    mod, root, prior,
                    "installer exited nonzero; status must be inspected." + detail,
                    effect_attempted=True)
            raise
        if (args.command in STAGING_COMMANDS or args.command == "install") and result != 0:
            _restore_failed_install(mod, root, prior, "installer returned nonzero",
                                    effect_attempted=True)
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
        print(json.dumps({
            "decision": "BLOCKED",
            "reason": str(exc),
            "effect_state": ("NEXT_ONLY_ACTION_ATTEMPTED" if effect_started
                             else "NO_SYSTEM_ACTION_STARTED"),
            "writes": ("POSSIBLE_OR_ROLLED_BACK" if effect_started else []),
            "executes": ([UNIT] if effect_started else []),
            "v1_action": False,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
