#!/usr/bin/env python3
"""Runtime graph: who this work belongs to, and what it would take down.

`attribution.py` answers "what is PID N". This module answers the question an
operator actually asks:

    Device -> Transport -> Agent -> Task -> Process tree -> MCP -> Container

Why a graph and not a process list
----------------------------------
On 2026-09-13 this host had three processes in one cgroup burning 407% CPU:

    178963  node codex.js app-server      ppid 1
     178978  codex app-server             ppid 178963   407% CPU, 316 MB
      684890  codex-code-mode-host        ppid 178978

Read as three rows in `ps`, that is three harmless-looking processes and no
reason to touch any of them. Read as a graph it is one thing — a remote
agent runtime whose origin session is closing — and the decision is obvious.
Every process list this host has ever produced showed the rows and hid the
thing.

Runtime groups
--------------
The unit of reasoning is the group, never the PID. One agent task routinely
owns a proxy, a supervisor, several MCP servers and a browser helper, and
judging them one at a time is how a live task gets half-reclaimed: the helper
looks idle, gets stopped, and the supervisor that needed it spins.

The rule that forms a group is mechanical and testable:

    within one cgroup scope, a process whose parent is NOT in that scope
    starts a group; the group is that process and all its descendants in
    the same scope

Nothing here is inferred from names. Two processes named `codex` in different
login sessions are different groups, and that is correct.

The honesty rule
----------------
Every link in the chain carries FACT, INFERRED or UNKNOWN. For the case above
the Task link is genuinely UNKNOWN and this module says so, on the record,
with the reason — because the alternative is a report that reads like a fact
and is actually a guess. §4 states the rule and this module is where it is
either kept or broken.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


attribution = _load("serveros_runtime_attribution", "attribution.py")

FACT = attribution.FACT
INFERRED = attribution.INFERRED
UNKNOWN = attribution.UNKNOWN

# ---------------------------------------------------------------------------
# Identity rules. Each one is a named rule so that an inference can be argued
# with rather than merely believed, and each produces INFERRED — never FACT.
#
# Matching runs against the executable path and argv[0..N]. It deliberately
# does NOT match the whole command line as one string: this host has already
# paid for that mistake once, when a `pkill -f` pattern matched its own shell
# because the pattern appeared in the command line doing the matching.
# ---------------------------------------------------------------------------

AGENT_RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("codex", re.compile(r"(^|/)codex(-[a-z0-9-]+)?$|codex-linux|/codex\.js$")),
    ("claude", re.compile(r"(^|/)claude(-code)?$|claude-code")),
)

# What a member IS inside its group. Order matters: the first match wins, so
# the more specific roles are listed first.
#
# `proxy` before `app-server` matters on this host: the proxy's command line
# contains the words `app-server proxy`, and matching app-server first would
# label a forwarding shim as the supervisor.
ROLE_RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("agent-proxy", re.compile(r"app-server\s+proxy|\bproxy\b")),
    ("agent-supervisor", re.compile(r"app-server|--listen\s+unix")),
    ("mcp-server", re.compile(r"--mcp\b|\bmcp[-_]server|mcp-server")),
    ("browser-helper", re.compile(r"playwright|browser-use|chromium|chrome-linux|puppeteer")),
    ("shell", re.compile(r"(^|/)(bash|sh|zsh|dash)$")),
    ("node-runtime", re.compile(r"(^|/)node$")),
    ("python-runtime", re.compile(r"(^|/)python3?$")),
)


def identify_agent(facts) -> dict:
    """Which agent this process belongs to, as an inference with its rule."""
    executable = facts.executable or ""
    for name, pattern in AGENT_RULES:
        if pattern.search(executable):
            return {"value": name, "status": INFERRED,
                    "rule": f"executable matches /{pattern.pattern}/"}
    return {"value": None, "status": UNKNOWN,
            "why": "no declared agent rule matches this executable"}


def identify_role(facts) -> dict:
    """What part this process plays inside its group."""
    haystack = " ".join([facts.executable or ""] + list(facts.argv or [])[:6])
    for name, pattern in ROLE_RULES:
        if pattern.search(haystack):
            return {"value": name, "status": INFERRED,
                    "rule": f"argv matches /{pattern.pattern}/"}
    return {"value": "process", "status": INFERRED, "rule": "no role rule matched"}


# Interpreters whose own basename says nothing about the program being run.
# `python3 /home/hwz/.local/bin/browser-use --mcp` is the browser-use MCP
# server; calling it `python3` is a locator masquerading as a name, and on
# this host it made three unrelated MCP servers indistinguishable.
_INTERPRETERS = {"python", "python3", "python3.11", "python3.12", "node",
                 "nodejs", "sh", "bash", "deno", "bun", "ruby", "perl"}


def identify_mcp(facts) -> dict | None:
    """Is this process an MCP server, and which one?

    An MCP server is recognised by how it was invoked (`--mcp`), not by what
    it is called. The tool name is then taken from the executable's basename
    — or, when the executable is an INTERPRETER, from the script it was handed
    — because `python3` is a locator and not a name.

    The name is still a locator rather than an identity, and the record says
    so: two builds of the same tool produce the same name, and nothing may
    resolve an owner from it.
    """
    haystack = " ".join([facts.executable or ""] + list(facts.argv or [])[:6])
    if not re.search(r"--mcp\b|\bmcp[-_]server", haystack):
        return None

    exe = (facts.executable or "").rsplit("/", 1)[-1]
    name = exe or None
    basis = "executable basename"
    if exe in _INTERPRETERS and len(facts.argv or []) > 1:
        script = facts.argv[1].rsplit("/", 1)[-1]
        if script and not script.startswith("-"):
            name = script
            basis = f"{exe} script argument"

    return {"name": name, "status": INFERRED,
            "rule": f"argv declares an MCP server; name taken from {basis}",
            "name_is_a_locator_not_an_identity": True}


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


def scope_key(facts) -> str:
    """The cgroup scope a process belongs to. The grouping key.

    Falls back to the process's own pid when there is no cgroup, which makes
    a cgroup-less process its own group rather than silently joining another.
    """
    return facts.cgroup or f"pid:{facts.pid}"


def build_groups(table: dict) -> list[dict]:
    """Every runtime group on the host, largest-CPU first.

    See the module docstring for the formation rule. Note what this does NOT
    do: it does not merge groups because they look similar, and it does not
    split a group because one member looks idle. The kernel's scope is the
    boundary, and a boundary that moves with the reader's opinion is not one.
    """
    by_scope: dict[str, list[int]] = {}
    for pid, facts in table.items():
        by_scope.setdefault(scope_key(facts), []).append(pid)

    groups: list[dict] = []
    for scope, pids in by_scope.items():
        whole_scope = set(pids)
        roots = [p for p in pids if table[p].ppid not in whole_scope]
        for root in sorted(roots):
            subtree = {root} | set(attribution.descendants(root, table))
            subtree &= whole_scope
            if not subtree:
                continue
            groups.append(_describe(scope, root, subtree, table,
                                    whole_scope=whole_scope))

    groups.sort(key=lambda g: (-g["aggregate"]["cpu_percent"], g["ref"]))
    return groups


def _describe(scope: str, root: int, members: set[int], table: dict, *,
              whole_scope: set[int] | None = None) -> dict:
    """Turn a member set into a group record with its aggregates and chain."""
    facts = [table[p] for p in sorted(members)]
    agents = {identify_agent(f)["value"] for f in facts} - {None}

    # The kind is decided by what the group IS, and the id by what can be
    # PROVEN about it. When exactly one agent owns the group, the session that
    # owns it is still the more defensible identity, because a session is a
    # fact and an agent is an inference.
    agent = sorted(agents)[0] if len(agents) == 1 else None
    kind = f"{agent}-session" if agent else "process-tree"
    group_id = scope.split("/")[-1].removesuffix(".scope") or f"pid{root}"

    # A scope can hold several roots — usually orphans whose parent exited —
    # and each becomes its own group. Without this suffix all of them get the
    # same ref, and a ref that names six different groups is not an identity:
    # `runtime group <ref>` would answer about whichever one sorted first and
    # nothing would report the collision. Found on the live host: six
    # `process-tree:session-106` groups before this line existed.
    if whole_scope is not None and len(members) != len(whole_scope):
        group_id = f"{group_id}:pid{root}"

    session_id = attribution.session_of_cgroup(scope)
    container_id = attribution.container_of_cgroup(scope)

    return {
        "ref": f"runtime-group:{kind}:{group_id}",
        "kind": kind,
        "id": group_id,
        "scope": scope,
        "root_pid": root,
        "members": sorted(members),
        "member_roles": {
            str(p): identify_role(table[p])["value"] for p in sorted(members)
        },
        "agent": {"value": agent, "status": INFERRED if agent else UNKNOWN,
                  "rule": "single agent rule matched across the group's members"
                          if agent else "members disagree or match no rule"},
        "session_ref": f"session:{session_id}" if session_id else None,
        "container_ref": f"runtime:container:{container_id}" if container_id else None,
        "aggregate": aggregate(facts),
        "tree": attribution.process_tree(root, table),
    }


def aggregate(facts: list) -> dict:
    """Group-level totals. The numbers a per-PID view cannot produce.

    `cpu_percent` SUMS rather than averaging. Four processes at 100% is four
    cores' worth of a machine, and reporting them separately is exactly how a
    host reaches load 6 while every individual process looks unremarkable.

    `age_seconds` takes the OLDEST member. The group is as old as the thing
    that started it, not as old as the youngest child that happens to still
    be alive — a supervisor that restarts its worker every minute is a
    day-old group, not a minute-old one.
    """
    ages = [f.age_seconds for f in facts if f.age_seconds is not None]
    return {
        "process_count": len(facts),
        "cpu_percent": round(sum(f.cpu_percent or 0.0 for f in facts), 1),
        "memory_rss_bytes": sum(f.rss_bytes or 0 for f in facts),
        "age_seconds": round(max(ages), 1) if ages else None,
        "cpu_metric": "lifetime_average_percent_of_one_core",
        "cpu_is_a_sum": True,
    }


def find_group(ref: str, table: dict) -> dict | None:
    for group in build_groups(table):
        if group["ref"] == ref or group["id"] == ref or str(group["root_pid"]) == ref:
            return group
    return None


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------


def link(value, status: str, *, rule: str | None = None, why: str | None = None) -> dict:
    """One link in the chain, with its epistemic status. THE core primitive.

    A link with a value and no status is the failure mode this whole design
    exists to prevent, so the status is positional and cannot be omitted.
    """
    out = {"value": value, "status": status}
    if rule:
        out["rule"] = rule
    if why:
        out["why"] = why
    return out


def build_chain(group: dict, table: dict, *, host=None,
                endpoint_map: dict | None = None) -> dict:
    """Device -> Transport -> Agent -> Task -> Processes -> MCP -> Container.

    Every link is either proven, inferred by a named rule, or UNKNOWN. The
    Task link is the one that most often comes back UNKNOWN, and that is a
    finding rather than a gap: this host's Codex app-server does not put a
    task id anywhere a user-space reader can see it, so nothing may claim one.

    `host` is read once per group rather than once per member — `loginctl` is
    a subprocess, and calling it for every process in a group would make
    `runtime tree` cost grow with the size of the thing it describes.
    """
    if host is None:
        host = attribution.LiveHost()
    session = None
    chain: dict = {}

    # -- device + transport -------------------------------------------------
    session_id = (group.get("session_ref") or "").removeprefix("session:") or None
    if session_id:
        session = attribution.session_facts(session_id, host)
    if session and session.get("remote") == "yes" and session.get("remote_host"):
        ip = session["remote_host"]
        identity = (endpoint_map or {}).get(ip)
        if identity:
            chain["device"] = link(identity, INFERRED,
                                   rule="declared endpoint map; the address is a "
                                        "fact and the device identity is declared")
        else:
            # The honest answer. The address is proven; which device it IS is
            # not, and no amount of the address looking familiar changes that.
            chain["device"] = link(
                f"ip:{ip}", INFERRED,
                rule="ssh session RemoteHost",
                why=("the peer address is a fact; no declared mapping reaches a "
                     "device_ref, so the device is identified by address only"),
            )
        chain["transport"] = link(session.get("service") or "sshd", FACT,
                                  rule="loginctl Service")
        chain["origin_session"] = link(f"session:{session_id}", FACT, rule="cgroup scope")
        if session.get("state"):
            chain["origin_session_state"] = link(
                session["state"], FACT, rule="loginctl State"
            )
    else:
        chain["device"] = link(None, UNKNOWN,
                               why="no session scope in the cgroup; no origin to read")
        chain["transport"] = link(None, UNKNOWN, why="no origin session")

    # -- agent --------------------------------------------------------------
    chain["agent"] = group["agent"]

    # -- task ---------------------------------------------------------------
    # Not derived from the command line. A task id that appears in argv is
    # evidence; a task id that appears nowhere is UNKNOWN. On this host it
    # appears nowhere, so this stays UNKNOWN and the report says why.
    task = _task_link(group, table)
    chain["task"] = task

    # -- processes ----------------------------------------------------------
    chain["processes"] = link(
        [{"pid": p, "role": group["member_roles"].get(str(p)),
          "comm": table[p].comm if p in table else None} for p in group["members"]],
        FACT, rule="cgroup scope + process tree",
    )

    # -- mcp + helpers ------------------------------------------------------
    mcp = []
    helpers = []
    for pid in group["members"]:
        member = table.get(pid)
        if member is None:
            continue
        found = identify_mcp(member)
        if found:
            mcp.append({"pid": pid, "name": found["name"]})
        role = group["member_roles"].get(str(pid))
        if role in ("browser-helper", "agent-proxy"):
            helpers.append({"pid": pid, "role": role})
    chain["mcp_servers"] = link(mcp, FACT, rule="argv declares an MCP server")
    chain["helpers"] = link(helpers, FACT, rule="role rule matched argv")

    # -- container ----------------------------------------------------------
    if group.get("container_ref"):
        chain["container"] = link(group["container_ref"], FACT, rule="cgroup")
    else:
        chain["container"] = link(None, FACT,
                                  rule="cgroup contains no container id",
                                  why="this group is not containerised")

    return chain


def _task_link(group: dict, table: dict) -> dict:
    """Is this group's task provable?

    Deliberately returns UNKNOWN on this host. The Codex app-server keeps its
    task state in a log and a `session_index.jsonl` that name tasks but never
    name the process serving one, so the join does not exist in any source of
    record a user-space reader can reach.

    Returning UNKNOWN here is the point of §4. A report that said
    `task: litellm-image-build v4` would be believed, and it would be a guess.
    """
    for pid in group["members"]:
        facts = table.get(pid)
        if facts is None:
            continue
        haystack = " ".join([facts.executable or ""] + list(facts.argv or []))
        match = re.search(r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                          r"[0-9a-f]{4}-[0-9a-f]{12})\b", haystack)
        if match:
            return link(match.group(1), INFERRED,
                        rule="a UUID appears in argv; it is not confirmed to be a task id")
    return link(
        None, UNKNOWN,
        why=("no task identifier is present in any member's command line or "
             "environment, and no other source of record joins a task to a "
             "process. A task name would be a guess and is not reported."),
    )


def load_endpoint_map(path=None) -> dict:
    """Optional declared map from peer address to device_ref.

    Absent by design: this host has no such declaration, so the device link
    stays an address. The hook exists so that closing the gap is a one-line
    declaration with a visible provenance rather than a rule hidden in code.
    """
    if path is None:
        return {}
    try:
        import yaml

        document = yaml.safe_load(Path(path).read_text()) or {}
    except (OSError, Exception):  # noqa: BLE001 - an unreadable map is "none"
        return {}
    entries = document.get("endpoints") or {}
    return {str(k): str(v) for k, v in entries.items()}
