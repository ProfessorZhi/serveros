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

    _disambiguate_ids(groups)
    groups.sort(key=lambda g: (-g["aggregate"]["cpu_percent"], g["ref"]))
    return groups


def _disambiguate_ids(groups: list[dict]) -> None:
    """Give every group an id that names exactly one group.

    `_describe` names a group after the last scope segment: readable, and
    enough whenever one scope holds one root. It is NOT enough across scopes.
    On the live host `/init.scope` and five
    `/user.slice/user-<uid>.slice/user@<uid>.service/init.scope` all end in
    `init.scope`, so all six were named `init` and all six shared the ref
    `runtime-group:process-tree:init`; the same happened to five
    `dbus.service` groups. The existing guard adds `:pid<root>` only when a
    group does not hold its whole scope, so it never fires for these.

    A ref that names six groups is not an identity. `runtime group <ref>`
    answered about whichever sorted first, and anything keyed on the ref
    conflated them — which is exactly what the alert dedupe key (subject +
    code) does, so one group's alert and another's closed each other.

    The fix widens the name with parent segments until no OTHER SCOPE shares
    it, and only for the groups that actually collide: a host with no
    collision keeps the short names it had. Two groups in the same scope are
    already separated by their `:pid<root>` suffix and are not compared
    against each other, so a multi-root scope is left alone. Widening is
    computed from the set of scopes, so the result does not depend on the
    order groups were built in.
    """
    def parts_of(scope: str) -> list[str]:
        return [p for p in (scope or "").split("/") if p]

    def label(scope: str, depth: int) -> str:
        parts = parts_of(scope)
        if not parts:
            return "root"
        return ":".join(p[:-len(".scope")] if p.endswith(".scope") else p
                        for p in parts[-depth:])

    for record in groups:
        scope = record.get("scope") or ""
        short = label(scope, 1)
        if not parts_of(scope) or not record["id"].startswith(short):
            continue
        depth = 1
        total = len(parts_of(scope))
        while depth < total:
            mine = label(scope, depth)
            if not any(o.get("scope") and o["scope"] != scope
                       and label(o["scope"], depth) == mine for o in groups):
                break
            depth += 1
        widened = label(scope, depth)
        if widened == short:
            continue
        # `:pid<root>` is orthogonal to the name and is kept as it was.
        record["id"] = widened + record["id"][len(short):]
        record["ref"] = "runtime-group:" + record["kind"] + ":" + record["id"]


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
        # Who owns this group, derived from the cgroup the kernel wrote. Carried
        # on the record rather than computed by each consumer, so that the
        # detector, the view and the CLI cannot reach three different answers
        # about one group — and so that the evidence travels with the verdict.
        "ownership": attribution.ownership_of_scope(scope),
        "container_ref": f"runtime:container:{container_id}" if container_id else None,
        "aggregate": aggregate(facts),
        # The largest terms of `aggregate`, so a reader who sees a sum can see
        # what is inside it. See `contributors()` for why the ratio matters and
        # why no write rate appears per process.
        "contributors": contributors(facts),
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


# How many members of a group are named individually. Three, because the
# question a contributor list answers is "is the cost one process or is it the
# group", and past three the answer is "the group" — a list long enough to be
# the member list is the aggregate with extra steps.
CONTRIBUTORS = 3


def contributors(facts: list, *, limit: int = CONTRIBUTORS) -> list[dict]:
    """Which members make up the aggregate, largest by summed-lifetime CPU first.

    `aggregate()` sums, and a sum is the one shape a real consumer can hide in:
    "codex-app-server, 5 processes, 279.5%" reads as a fleet when one process is
    doing 2.8 cores and four are idle, and the four idle ones are what makes the
    number look like a quantity of work rather than one runaway. This is the
    same measurement, split back out, so the largest term is visible next to the
    total it dominates.

    PID is a field here and never an identity: it says which process to look at
    right now, and a restarted process is a different row. What does NOT appear
    is a per-process write rate — this host cannot observe one (`ProcessFacts`
    carries no IO counters), and a `write_bytes: 0` beside a process burning
    30 MB/s would be a fabricated measurement of the exact quantity this list
    exists to make visible. Disk throughput is observable at the HOST level
    only, which is a different subject and is reported as one.
    """
    ordered = sorted(facts, key=lambda f: (-(f.cpu_percent or 0.0), f.pid))
    rows = []
    for facts_row in ordered[: max(0, limit)]:
        rows.append({
            "pid": facts_row.pid,
            "comm": facts_row.comm,
            "role": identify_role(facts_row)["value"],
            "cpu_percent": facts_row.cpu_percent,
            "cpu_metric": "lifetime_average_percent_of_one_core",
            "rss_bytes": facts_row.rss_bytes,
            "age_seconds": facts_row.age_seconds,
        })
    return rows


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


def session_states(groups: list[dict], *, host=None) -> dict:
    """The origin session of each group, as `loginctl` reports it — one call per
    distinct session, not one per group.

    `anomaly.detect()` needs this as a document rather than as a call it makes
    itself, for the reason it takes `groups` and `containers` as documents: the
    detector is pure, and a detector that shells out is a detector whose answer
    depends on when it was asked. The shape is `attribution.session_facts()`'s,
    keyed by the ref the group already carries.

    Only the sessions a group actually names are asked about, so a group list
    with no session scope costs nothing here — and the empty dict that comes
    back is distinguishable from "every session is fine" because
    `anomaly.unchecked()` reports the input as missing.
    """
    if host is None:
        host = attribution.LiveHost()
    refs = sorted({g["session_ref"] for g in groups if g.get("session_ref")})
    out: dict[str, dict] = {}
    for ref in refs:
        session_id = ref.removeprefix("session:")
        if not session_id:
            continue
        out[ref] = attribution.session_facts(session_id, host)
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


# ---------------------------------------------------------------------------
# §12 — the resource graph
#
# `build_groups` and `build_chain` above answer "what is this PROCESS", which
# is a question about the machine as it is running. This section answers "what
# does this RUNTIME hold, and who does it belong to", which is a question
# about resources. Two questions, two graphs, and deliberately one primitive:
# `link()`.
#
# A second way to spell an epistemic status is a second thing that can drift,
# which is why the brief's "do not create ResourceGraphV2" is a correctness
# rule and not a taste rule. This section adds nodes and edges; it does not
# add a vocabulary.
# ---------------------------------------------------------------------------

# §12 names three levels of edge confidence. The module already has three
# epistemic statuses (FACT / INFERRED / UNKNOWN from `attribution`), and these
# are NOT a rename of them — see `edge()` for why both are carried.
AUTHORITATIVE = "AUTHORITATIVE"

CONFIDENCE_LEVELS = (AUTHORITATIVE, INFERRED, UNKNOWN)

# The relationships this graph is allowed to draw. Enumerated rather than
# free-form so that an edge nothing can interpret is a test failure instead
# of a string a reader has to guess at.
RELATIONSHIPS = (
    "hosts",        # device -> managed runtime
    "mounts",       # managed runtime -> volume
    "binds-to",     # managed runtime -> host path
    "attached-to",  # managed runtime -> network
    "publishes",    # managed runtime -> port (the provider published it)
    "forwards",     # managed runtime -> tunnel
    "owned-by",     # managed runtime | tunnel -> owner
    "alias-of",     # a registry's own spelling -> the canonical runtime node
)

NODE_KINDS = ("device", "managed-runtime", "provider-resource", "host-path", "owner")

# Declared freshness windows, in seconds, per evidence source. This is a
# POLICY, stated here so that it can be argued with.
#
# A source with no entry produces UNKNOWN freshness rather than a default. A
# `DEFAULT_WINDOW` constant would merge "this is fresh" with "this host has
# no declared window for this kind of evidence", and those are different
# sentences — the same substitution §15 forbids for desired state.
FRESH_WITHIN_SECONDS = {
    "docker-inspect": 300,
    "registry": 86400,
    # -- §18 resource-observation sources ------------------------------------
    # Host pressure moves on the scale of a build, not of a tick: five minutes
    # is long enough that a reading is not re-taken for every question and
    # short enough that a filling disk is not reported as yesterday's news.
    "host-filesystem": 300,
    "host-memory": 300,
    # The per-account memory inventory, and it is its own source rather than a
    # second reading of `host-memory` above. The two are taken from the same
    # machine but they are not the same evidence: the window above answers "is
    # this host out of memory" from one `/proc/meminfo`, and this one answers
    # "which account is holding it" from a process listing read twice a second
    # apart. Same window, because they move on the same scale and are read on
    # the same tick — and declared separately, because a source with no entry
    # here produces UNKNOWN freshness, and quietly borrowing the entry above
    # would be the only way that omission could ever be discovered.
    "user-memory": 300,
    # The Docker store's own accounting is a ~22s read on this host, so a call
    # that paid for it is allowed to be an hour old before it is called stale.
    # A window shorter than the cost of the measurement would make every
    # observation stale by construction.
    "docker-space": 3600,
    "docker-sizes": 900,
    "docker-logs": 900,
    "docker-hygiene": 900,
    "desired-actual": 900,
    # The cross-check re-reads the same two documents the other two sources
    # did, so it ages with the faster of them rather than declaring a third
    # clock for one question.
    "protected-cross-check": 300,
    "tunnel-observation": 300,
}


def edge(relationship: str, source: str, target, *, provenance: str,
         confidence: str, rule: str | None = None, why: str | None = None,
         observed_at: str | None = None, freshness: dict | None = None) -> dict:
    """One edge, reusing `link()` for the epistemic half.

    `provenance` and `confidence` are not the same field written twice:

      provenance   how the raw fact was obtained — FACT, INFERRED, UNKNOWN.
      confidence   how far the edge BINDS — AUTHORITATIVE, INFERRED, UNKNOWN.

    They come apart in the ordinary case, which is why both exist. `docker
    inspect` naming the network a container is attached to is a FACT and the
    edge is AUTHORITATIVE, because the provider's own structured API named it.
    The same inspect naming a mount's host path is also a FACT, and the edge
    is INFERRED — a path is not a resource identity, and tomorrow a volume
    may occupy it.

    Collapsing the two would force one of those to be reported wrongly. The
    `why` on an INFERRED edge names what is missing, so the edge can be
    argued with rather than merely disbelieved.
    """
    if relationship not in RELATIONSHIPS:
        raise ValueError(
            f"{relationship!r} is not a relationship this graph draws. Known: "
            f"{sorted(RELATIONSHIPS)}. An edge nothing can interpret is not a "
            f"finding, it is a string."
        )
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(
            f"{confidence!r} is not a confidence level. Known: "
            f"{list(CONFIDENCE_LEVELS)}"
        )
    out = link(target, provenance, rule=rule, why=why)
    out["relationship"] = relationship
    out["source"] = source
    out["confidence"] = confidence
    out["observed_at"] = observed_at
    out["freshness"] = freshness
    return out


def freshness_of(observed_at, *, now, source: str | None = None,
                 within_seconds: int | None = None) -> dict:
    """How old one observation is, against a DECLARED window.

    Reports FRESH or STALE only when there is a window to compare against,
    a time on the observation, and a clock to age it with. Every other case
    is UNKNOWN with the reason — never FRESH. "Nothing has aged" is not a
    state this function can reach; the most it can say is that it cannot
    tell, and saying more than that is how a stale edge comes to read as a
    current fact.
    """
    window = within_seconds
    because = None
    if window is None:
        window = FRESH_WITHIN_SECONDS.get(source or "")
        if window is None:
            because = (
                f"no freshness window is declared for evidence from "
                f"{source or 'an unnamed source'}, so its age is not "
                f"measurable against a policy"
            )
    if observed_at is None:
        because = because or (
            "the observation carries no time, so its age cannot be computed"
        )
    if now is None:
        because = because or (
            "the reader supplied no clock, so this observation cannot be aged"
        )

    if because is not None or window is None:
        return {
            "state": UNKNOWN,
            "age_seconds": None,
            "within_seconds": window,
            "observed_at": observed_at,
            "why": because,
        }

    age = _seconds_between(observed_at, now)
    if age is None:
        # WHICH of the two failed is the whole content of this message. The
        # first version said "the observation's timestamp ... does not parse"
        # whenever either side did, and the case that reaches it in practice is
        # the other one: a caller passing a display string as `now`. A reader
        # sent to the wrong document by a diagnostic is worse off than one told
        # nothing, because they will fix the end that was already right.
        if _parse_time(now) is None:
            because = (f"the clock supplied to age this observation ({now!r}) "
                       f"does not parse as a time")
        else:
            because = (f"the observation's timestamp {observed_at!r} does not "
                       f"parse as a time")
        return {
            "state": UNKNOWN,
            "age_seconds": None,
            "within_seconds": window,
            "observed_at": observed_at,
            "why": f"{because}, so its age is unknown rather than assumed zero",
        }

    return {
        "state": "FRESH" if age <= window else "STALE",
        "age_seconds": age,
        "within_seconds": window,
        "observed_at": observed_at,
        "why": None,
    }


def _parse_time(value):
    """One timestamp, or None. The only parser this module has.

    Split out of `_seconds_between` so that a caller can ask WHICH side failed
    without restating the rule — two copies of a parse rule disagree the first
    time either learns a format the other has not.
    """
    import datetime as _dt

    if isinstance(value, _dt.datetime):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return _dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def _seconds_between(then, now) -> float | None:
    """Seconds from `then` to `now`, or None if either will not parse.

    Tolerant of both the ISO-with-offset form docker returns and a bare
    `datetime`, because the two callers between them use both. A parse
    failure is NOT zero: an edge whose age reads as 0 is reported as fresh,
    which is the single most misleading answer this function could give.
    """
    a, b = _parse_time(then), _parse_time(now)
    if a is None or b is None:
        return None
    if (a.tzinfo is None) != (b.tzinfo is None):
        # Comparing an aware and a naive time raises. Guessing a zone here
        # would silently shift an age by hours; refusing is the honest answer.
        return None
    return (b - a).total_seconds()


# Public under a second name, and deliberately the same function rather than a
# copy: `runtime/alerting.py` measures a cooldown and a hysteresis window with
# it, and two parsers is how one module comes to read an unparseable timestamp
# as "0 seconds ago" while the other refuses. The refusing reading is the only
# safe one — an alert whose age reads as zero never leaves its cooldown.
seconds_between = _seconds_between


def _node(ref: str, *, kind: str, **extra) -> dict:
    node = {"ref": ref, "kind": kind}
    node.update(extra)
    return node


def _node_kind_for(ref: str) -> str:
    """What kind of thing a bare ref names.

    Prefix-driven, over the ref namespaces this product already uses. An
    unrecognised ref is `owner`, because the only refs this graph reaches it
    without having classified are the ones an attribution passed through, and
    those are owners by construction.
    """
    if ref.startswith("device:"):
        return "device"
    if ref.startswith("runtime:container:"):
        return "managed-runtime"
    if ref.startswith(("volume:", "network:", "tunnel:", "port:", "image:")):
        return "provider-resource"
    if ref.startswith("path:"):
        return "host-path"
    return "owner"


def _identity_anchor_refs(record: dict) -> list[str]:
    return [
        a["runtime_ref"] for a in (record.get("identity") or {}).get("anchors") or []
        if a.get("runtime_ref")
    ]


def _binds_and_volumes(evidence, ref: str, *, observed_at, now) -> tuple[list[dict], set[str]]:
    """`docker inspect`'s mounts, split into the two relationships they mean.

    A named volume and a bind mount arrive in the same list and are NOT the
    same kind of edge:

      volume    docker's own API named a volume by the identity docker uses
                for it. AUTHORITATIVE.
      bind      the source is a host PATH. The fact is the provider's, but
                the edge is INFERRED: a path is not a resource identity, and
                the same path may hold a different thing by tomorrow.
    """
    out: list[dict] = []
    volumes: set[str] = set()
    if not evidence:
        return out, volumes
    for mount in evidence.get("mounts") or []:
        kind = mount.get("type")
        destination = mount.get("destination")
        if kind == "volume" and mount.get("name"):
            volumes.add(f"volume:{mount['name']}")
            out.append(edge(
                "mounts", ref, f"volume:{mount['name']}",
                provenance=FACT, confidence=AUTHORITATIVE,
                rule="docker inspect Mounts, structured",
                why=f"mounted at {destination}",
                observed_at=observed_at,
                freshness=freshness_of(observed_at, now=now, source="docker-inspect"),
            ))
        elif mount.get("source"):
            out.append(edge(
                "binds-to", ref, f"path:{mount['source']}",
                provenance=FACT, confidence=INFERRED,
                rule="docker inspect Mounts, structured",
                why=(
                    f"the provider states this path is bound at {destination}; "
                    f"a path is not a resource identity and the same path may "
                    f"hold something else later"
                ),
                observed_at=observed_at,
                freshness=freshness_of(observed_at, now=now, source="docker-inspect"),
            ))
    return out, volumes


def resource_graph(attribution_document: dict, *, evidences=None, tunnels=None,
                   ports=None, aliases=None, device_ref=None, observed_at=None,
                   now=None) -> dict:
    """§12. Device -> Managed Runtime -> Provider Resource, and Runtime -> Owner.

    Built from the records `containers.attribute_all` already produces rather
    than by re-inspecting the host, so that the graph cannot disagree with the
    inventory it describes. Re-inspecting would give this function a second
    chance to see a different machine, and a graph that disagrees with the
    inventory it was built from is worse than no graph.

    What this function will NOT do:

      * It never draws an owner edge from a NAME. Two containers called
        `litellm-1` and `litellm-2` are two containers; the first being
        attributed says nothing whatever about the second. §12 forbids the
        inference by name, and the prohibition is stronger than that — it
        forbids it for being an inference at all.
      * It never silently picks between disagreeing sources. When two
        observations name different owners for one runtime, BOTH edges are
        emitted and the pair is recorded in `conflicts`. Resolving that is a
        human's job; a graph that chose would hide the disagreement.
      * It never drops an edge because it is old. A stale edge is still an
        edge and its `freshness` says so; dropping it would turn "we last saw
        this an hour ago" into "this never existed".

    `aliases` maps a ref a registry spells one way onto the canonical runtime
    node, which is how a registry naming a container by NAME still reaches the
    node keyed by id. The alias becomes an `alias-of` edge rather than a
    rewrite: replacing the ref would make the registry's own spelling
    unrecoverable, and that spelling is what a reader has to search for.
    """
    records = attribution_document.get("containers") or []
    tunnels = tunnels or []
    ports = ports or []
    aliases = aliases or {}

    if device_ref is None:
        device_ref = attribution_document.get("device_ref")

    by_id = {ev.get("id"): ev for ev in (evidences or []) if ev.get("id")}
    by_ref = {r["ref"]: r for r in records if r.get("ref")}

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    conflicts: list[dict] = []

    def add_node(ref, kind=None, **extra):
        if ref in nodes:
            nodes[ref].update({k: v for k, v in extra.items() if v is not None})
            return nodes[ref]
        nodes[ref] = _node(ref, kind=kind or _node_kind_for(ref), **extra)
        return nodes[ref]

    if device_ref:
        add_node(device_ref, display_name=device_ref)

    observed = freshness_of(observed_at, now=now, source="docker-inspect")
    mount_evidence_seen = 0

    for record in records:
        ref = record.get("ref")
        if not ref:
            continue
        runtime = record.get("runtime") or {}
        identity = record.get("identity") or {}
        management = record.get("management") or {}
        owner = record.get("owner") or {}

        add_node(
            ref, display_name=record.get("container"),
            provider=runtime.get("provider"),
            actual_state=runtime.get("actual_state"),
            protected=runtime.get("protected"),
            management=management.get("class"),
            management_confidence=management.get("confidence"),
            stable_across_rename=identity.get("stable_across_rename"),
        )

        # -- device -> runtime ---------------------------------------------
        evidence = by_id.get(_container_id_of(record)) if by_id else None
        if evidence is not None:
            mount_evidence_seen += 1

        declared_device = (
            (evidence or {}).get("runtime_labels") or {}
        ).get("device_ref")
        host_ref = device_ref
        if host_ref:
            add_node(host_ref, kind="device")
            edges.append(edge(
                "hosts", host_ref, ref,
                provenance=FACT, confidence=AUTHORITATIVE,
                rule="observed on this node by its own provider API",
                observed_at=observed_at, freshness=observed,
            ))
        # The runtime's OWN device reading. `runtime_record` prefers a declared
        # label and falls back to the registry, so a value that differs from
        # the registry's can only have come from a declaration — which is why
        # the differing case is drawn as a second edge rather than ignored.
        # (Without the raw evidence the graph cannot read the label itself, and
        # the rule below says so instead of implying it was inspected.)
        record_device = runtime.get("device_ref")
        if record_device and host_ref and record_device != host_ref:
            # §12's conflict rule, applied to identity rather than ownership.
            add_node(record_device, kind="device", declared_not_observed=True)
            edges.append(edge(
                "hosts", record_device, ref,
                provenance=FACT if evidence is not None else INFERRED,
                confidence=INFERRED,
                rule=(
                    "the container's own label names a device"
                    if evidence is not None else
                    "the runtime record's device_ref, whose raw label was not "
                    "supplied to this graph"
                ),
                why=(
                    f"the container declares {record_device} while this node "
                    f"is {host_ref}; both are reported and neither is treated "
                    f"as authoritative over the other"
                ),
                observed_at=observed_at, freshness=observed,
            ))
            conflicts.append({
                "subject": ref,
                "relationship": "hosts",
                "candidates": sorted([host_ref, record_device]),
                "why": (
                    "the node this observer runs on and the device the "
                    "container declares for itself disagree; §12 keeps both "
                    "and does not choose"
                ),
            })

        # -- runtime -> provider resources ---------------------------------
        # The mount evidence is the richer source and goes first; the
        # `resource_refs` list on the record is the fallback for a container
        # whose raw evidence the caller did not supply. A volume named by both
        # is drawn ONCE, from the structured one, so the edge count is the
        # number of resources rather than the number of lists they appeared in.
        mount_edges, drawn_volumes = _binds_and_volumes(
            evidence, ref, observed_at=observed_at, now=now,
        )
        edges.extend(mount_edges)
        for resource in runtime.get("resource_refs") or []:
            target = resource.get("ref")
            if not target:
                continue
            if target in drawn_volumes:
                continue
            add_node(target, kind="provider-resource", provider=runtime.get("provider"))
            edges.append(edge(
                "attached-to" if target.startswith("network:") else "mounts",
                ref, target,
                provenance=FACT, confidence=AUTHORITATIVE,
                rule="docker inspect, structured",
                why=resource.get("because"),
                observed_at=observed_at, freshness=observed,
            ))

        # -- runtime -> owner ----------------------------------------------
        if owner.get("ref"):
            add_node(
                owner["ref"], kind="owner",
                owner_type=owner.get("type"),
            )
            confidence = _confidence_from(management.get("confidence"))
            edges.append(edge(
                "owned-by", ref, owner["ref"],
                provenance=INFERRED if confidence != AUTHORITATIVE else FACT,
                confidence=confidence,
                rule=(record.get("attribution") or {}).get("decided_by")
                     or "attribution ladder",
                why=record.get("disposition_because"),
                observed_at=observed_at, freshness=observed,
            ))
        else:
            # No owner edge. An edge to a node called `owner:unknown` would be
            # a node that does not exist dressed as a finding; §12 asks for
            # "missing owner" to be visible, and the node's own link is where
            # that is visible.
            nodes[ref]["owner"] = link(
                None, UNKNOWN,
                why=(
                    "no rung of the attribution ladder reached an owner. This "
                    "is reported as an absence on the node rather than as an "
                    "edge to an unknown owner, because the second would make a "
                    "missing answer look like a present one"
                ),
            )

        # -- what a reader asked for and did not get -----------------------
        drift = (runtime.get("unknowns") or {}).get("device_ref_drift")
        if drift:
            nodes[ref].setdefault("drift", {})["device_ref_drift"] = drift

    # -- tunnels (§13) -----------------------------------------------------
    for tunnel in tunnels:
        _add_tunnel(tunnel, add_node, edges, conflicts,
                    observed_at=observed_at, now=now, aliases=aliases)

    # -- ports (§14) -------------------------------------------------------
    for port in ports:
        _add_port(port, by_ref, add_node, edges, observed_at=observed_at,
                  now=now, aliases=aliases)

    # -- registry aliases --------------------------------------------------
    for alias_ref, canonical in aliases.items():
        if alias_ref == canonical:
            continue
        add_node(canonical, kind="managed-runtime")
        add_node(alias_ref, kind="managed-runtime", alias_of=canonical)
        edges.append(edge(
            "alias-of", alias_ref, canonical,
            provenance=FACT, confidence=AUTHORITATIVE,
            rule="a declaration on this node names the same runtime twice",
            why=(
                "the alias is kept as its own node because the declaration "
                "spells it that way, and a reader searching for the declared "
                "spelling must still find it"
            ),
            observed_at=observed_at, freshness=freshness_of(
                observed_at, now=now, source="registry"),
        ))

    ordered_edges = sorted(
        edges, key=lambda e: (e["source"], e["relationship"], str(e["value"]))
    )

    # Every edge endpoint is a node. Stated once, here, rather than left to
    # each producer — a producer that draws an edge and forgets the node
    # leaves a dangling reference, and a dangling reference is how a graph
    # reports a resource that "exists" only as the far end of an arrow.
    # Found the hard way: `_binds_and_volumes` drew `mounts` and `binds-to`
    # edges without adding their targets, so a bound path and a named volume
    # were reachable only as strings.
    for e in ordered_edges:
        add_node(e["source"])
        if e["value"]:
            add_node(e["value"])

    return {
        "schema": "serveros/resource-graph.v1",
        "device_ref": device_ref,
        "observed_at": observed_at,
        "nodes": [nodes[ref] for ref in sorted(nodes)],
        "edges": ordered_edges,
        "conflicts": sorted(conflicts, key=lambda c: (c["subject"], c["relationship"])),
        "coverage": {
            "nodes": len(nodes),
            "edges": len(ordered_edges),
            "runtime_nodes": sum(1 for n in nodes.values()
                                 if n["kind"] == "managed-runtime"),
            "owner_nodes": sum(1 for n in nodes.values() if n["kind"] == "owner"),
            "runtimes_without_owner_edge": sorted(
                r["ref"] for r in records
                if r.get("ref") and not (r.get("owner") or {}).get("ref")
            ),
            "conflicts": len(conflicts),
            # The graph can only draw a bind edge for a container whose raw
            # evidence the caller supplied. Reporting the shortfall keeps
            # "this container has no bind mounts" apart from "nobody passed
            # this container's mounts to the graph".
            "mount_evidence_supplied": mount_evidence_seen,
            "mount_evidence_absent_for": sorted(
                r["ref"] for r in records
                if r.get("ref") and _container_id_of(r) not in by_id
            ),
        },
    }


def _container_id_of(record: dict) -> str | None:
    """The container id behind a runtime ref, from the record's own anchors.

    Within one `attribute_all` tick this is an exact join: the anchor and the
    evidence record's `id` are both `(record["Id"])[:12]` of the same inspect
    output. It is NOT an exact join against `build_groups`, whose container
    refs come from the cgroup and therefore carry docker's full 64-hex id.
    See `reconcile_container_refs` for that one — the two spellings differ and
    pretending otherwise is how a graph silently reports two nodes for one
    container.
    """
    for anchor in (record.get("identity") or {}).get("anchors") or []:
        if anchor.get("anchor") == "container-id" and anchor.get("runtime_ref"):
            return anchor["runtime_ref"].removeprefix("runtime:container:")
    return None


def reconcile_container_refs(canonical_refs, foreign_refs) -> dict:
    """Join a container ref from another surface onto this graph's node refs.

    Two surfaces name the same container differently and both are right:

      `containers.py`   `runtime:container:<12 hex>` — `docker inspect` is
                        read for the first 12 characters of `Id`, which is
                        what a human and `docker ps` use.
      `graph.build_groups`  `runtime:container:<64 hex>` — the cgroup scope
                        is `/system.slice/docker-<full id>.scope`, and
                        `attribution.container_of_cgroup` does not truncate.

    So the join is a PREFIX join, and a prefix join is a guess unless it is
    unambiguous. Two containers on one host can share twelve hex characters,
    and this function refuses rather than picks: an ambiguous ref is reported
    as ambiguous and reaches no node. That is the same rule as everywhere else
    in this round — a wrong join that reads as a right one is worse than a
    reported gap, because only one of the two is visible.

    Returns `{"matched": {foreign: canonical}, "ambiguous": [...],
    "unmatched": [...], "rule": str}`. `matched` is empty when either side is
    empty, which is a measurement rather than a failure.
    """
    canonical = sorted(set(canonical_refs or ()))
    by_prefix: dict[str, list[str]] = {}
    for ref in canonical:
        key = ref.removeprefix("runtime:container:")
        by_prefix.setdefault(key, []).append(ref)

    matched: dict[str, str] = {}
    ambiguous: list[dict] = []
    unmatched: list[str] = []
    for foreign in sorted(set(foreign_refs or ())):
        key = foreign.removeprefix("runtime:container:")
        # Equality is the one-candidate case of this predicate, not a special
        # path: a 12-hex id against itself matches once and joins.
        hits = [ref for cand, refs in by_prefix.items()
                if cand.startswith(key) or key.startswith(cand) for ref in refs]
        if len(hits) == 1:
            matched[foreign] = hits[0]
        elif len(hits) > 1:
            ambiguous.append({
                "ref": foreign,
                "candidates": sorted(hits),
                "why": (
                    "more than one node on this device shares the id prefix "
                    "this ref carries; joining would be a coin toss, so no "
                    "node is reached and the collision is reported instead"
                ),
            })
        else:
            unmatched.append(foreign)

    return {
        "matched": matched,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
        "rule": (
            "equality first; otherwise an unambiguous 12-hex prefix match, "
            "because `docker inspect` is read truncated and the cgroup is not"
        ),
    }


def _confidence_from(management_confidence) -> str:
    word = (management_confidence or "").upper()
    if word in CONFIDENCE_LEVELS:
        return word
    return UNKNOWN


def _add_tunnel(tunnel, add_node, edges, conflicts, *, observed_at, now, aliases):
    ref = tunnel.get("tunnel_ref")
    if not ref:
        return
    add_node(ref, kind="provider-resource", provider=tunnel.get("provider"),
             scope=tunnel.get("scope"), health=tunnel.get("health"))
    runtime_ref = tunnel.get("runtime_ref")
    if runtime_ref:
        canonical = aliases.get(runtime_ref, runtime_ref)
        add_node(canonical, kind="managed-runtime")
        edges.append(edge(
            "forwards", canonical, ref,
            provenance=FACT if runtime_ref == canonical else INFERRED,
            confidence=AUTHORITATIVE if runtime_ref == canonical else INFERRED,
            rule="the tunnel observation names the runtime it belongs to",
            why=None if runtime_ref == canonical else (
                f"the observation spells the runtime {runtime_ref} and the "
                f"canonical node is {canonical}"
            ),
            observed_at=observed_at,
            freshness=freshness_of(observed_at, now=now,
                                   source=tunnel.get("provenance") or "tunnel-probe"),
        ))
    _owner_edge(tunnel.get("owner_ref"), tunnel.get("ownership_confidence"),
                "the tunnel's own declaration or provider", ref, add_node, edges,
                conflicts, observed_at=observed_at, now=now,
                conflict_sources=tunnel.get("owner_conflicts"))


def _add_port(port, by_ref, add_node, edges, *, observed_at, now, aliases):
    ref = port.get("port_ref")
    if not ref:
        return
    add_node(ref, kind="provider-resource", provider=port.get("provider"),
             protocol=port.get("protocol"), local=port.get("local_address"),
             state=port.get("state"))
    runtime_ref = port.get("runtime_ref")
    if runtime_ref and not (port.get("state") == "UNKNOWN"):
        canonical = aliases.get(runtime_ref, runtime_ref)
        add_node(canonical, kind="managed-runtime")
        edges.append(edge(
            "publishes", canonical, ref,
            provenance=port.get("provenance") or INFERRED,
            confidence=_confidence_from(port.get("confidence")),
            rule=port.get("because") or "port attribution",
            observed_at=observed_at,
            freshness=freshness_of(observed_at, now=now,
                                   source=port.get("provenance_source") or "docker-inspect"),
        ))


def _owner_edge(owner_ref, confidence_word, rule, subject, add_node, edges,
                conflicts, *, observed_at, now, conflict_sources=None):
    """Draw `owned-by`, or record why there is none — never a fabricated one."""
    sources = [s for s in (conflict_sources or []) if s]
    names = [s.get("owner_ref") for s in sources if s.get("owner_ref")]
    distinct = sorted({n for n in names if n} | ({owner_ref} if owner_ref else set()))
    if len(distinct) > 1:
        for name in distinct:
            add_node(name, kind="owner")
            edges.append(edge(
                "owned-by", subject, name,
                provenance=INFERRED, confidence=INFERRED,
                rule=rule,
                why=(
                    "another source names a different owner for this subject; "
                    "§12 keeps every candidate and does not choose"
                ),
                observed_at=observed_at,
                freshness=freshness_of(observed_at, now=now, source="conflict"),
            ))
        conflicts.append({
            "subject": subject,
            "relationship": "owned-by",
            "candidates": distinct,
            "why": (
                "two sources disagree about the owner of this subject; §12 "
                "keeps both candidates and does not choose"
            ),
        })
        return
    if not owner_ref:
        # No owner edge, and no stand-in node for one. `_add_tunnel` and
        # `_add_port` have already added the subject; what is missing is
        # recorded on it, the same way a container runtime records its
        # missing owner, so that "nothing owns this" and "nobody looked" stay
        # different readings.
        subject_node = add_node(subject)
        subject_node["owner"] = link(
            None, UNKNOWN,
            why=(
                "no source reached an owner for this subject, and none is "
                "invented. An edge to an unknown owner would read as an "
                "answer where there is only an absence"
            ),
        )
        return
    add_node(owner_ref, kind="owner")
    edges.append(edge(
        "owned-by", subject, owner_ref,
        provenance=FACT if _confidence_from(confidence_word) == AUTHORITATIVE
        else INFERRED,
        confidence=_confidence_from(confidence_word),
        rule=rule,
        observed_at=observed_at,
        freshness=freshness_of(observed_at, now=now, source="declaration"),
    ))
