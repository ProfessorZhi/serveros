#!/usr/bin/env python3
"""serveros-node-mcp — the thin adapter.

    Claude / Codex  ->  serveros-node-mcp  ->  local IPC  ->  serverosd

It is a transport and nothing else. It holds no registry, computes no health,
classifies no container and writes no file. Every question is forwarded to the
daemon over the Unix socket and the answer is returned unchanged.

Why it is worth stating that so plainly: the temptation is to let the adapter
"help" — cache a registry read, fall back to reading the state files, classify
a container inline when the daemon is slow. Each of those makes the adapter a
second implementation of a policy that exists in one place. Two
implementations of one policy is not redundancy; it is an eventual
disagreement, and the caller has no way to know which one it just talked to.

What it does instead when the daemon is unavailable is REFUSE, by name. Not an
empty list, not a cached answer, not a degraded-but-plausible one. `serverosd
is not listening on <path>` is a fact the caller can act on; `[]` is a fact
that gets read as "nothing is wrong".

**No side-effecting tools are declared.** §24 says that if restart / stop /
reconcile exist they must go through admission. They do not exist yet, so they
are not declared — an absent tool is a clearer fact than one that exists and
refuses, and the caller cannot accidentally build a workflow on something that
was never going to work. When they are added they will need an admission
record, not just a tool entry.

**Protocol.** JSON-RPC 2.0 over stdio, implemented directly rather than via a
client library. The surface is four methods and the framing is one object per
line; a dependency here would put a package resolution between an agent and
the control plane on a host that is already swapping, and would fail closed in
a way that looks like "the node has nothing to say".
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "serveros-node-mcp"
SERVER_VERSION = "0.1.0"

# §23: the adapter reaches the daemon over a Unix socket and never over TCP.
DEFAULT_TIMEOUT_SECONDS = 20.0


class DaemonUnavailable(Exception):
    """The control plane did not answer. Refused, not approximated."""


class DaemonBusy(DaemonUnavailable):
    """The control plane was reached and is working, and did not get to this.

    A DIFFERENT FACT FROM `DaemonUnavailable`, in the same way `DaemonRefused`
    below is. `serverosd` serves requests on the same single thread that runs its
    once-a-minute tick (`daemon.serve_forever`: "deliberately single-threaded and
    deadline-driven… requests are served between ticks, on the same thread"), and
    on 2026-10-04 that tick took 4.964 s as the daemon itself records it. A
    request arriving mid-tick is ACCEPTED — measured over 1,624 probes that day,
    `connect` never once took above 0.000 s and never errored — and answered when
    the tick ends.

    So a timeout here does not mean "no reading". It means there is a reading,
    the runtime is up, and this caller stopped listening for it. Collapsing the
    two into one word is how a busy daemon becomes a missing measurement, and a
    missing measurement is what a status card renders as a gap. This class lets a
    caller tell them apart; it does not itself retry, and nothing below catching
    it differently yet.
    """


class DaemonRefused(DaemonUnavailable):
    """The control plane answered, and the answer was no.

    Kept apart from the parent because the two send an operator to different
    places: "serverosd is not listening" is an outage to repair, and "serverosd
    refused this call" is a decision to read. Until this existed, every refusal
    — a forged payload, a report observed by another process, an action this
    actor may not take — arrived as `DaemonUnavailable`, which says the control
    plane was unavailable at the one moment it was working exactly as designed.

    A subclass, so callers that only want "no answer" keep catching the parent
    and none of them change behaviour.
    """


# ---------------------------------------------------------------------------
# The tool surface (§24). Each entry names the daemon method it forwards to.
# ---------------------------------------------------------------------------

TOOLS: tuple[dict, ...] = (
    {
        "name": "device.show",
        "description": (
            "Identity and measured resource facts for this node: CPU, memory, "
            "load, GPU, disk, and how many managed runtimes it holds."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "context.current",
        "description": (
            "The ServerOS roots, and the Item inferred from a working "
            "directory YOU state. Roots come from the root contract and never "
            "from a directory. The Item is UNKNOWN unless `cwd` is given: this "
            "surface will not infer one from the daemon's own directory, "
            "because an artifact written to a guessed Item is the failure this "
            "answer exists to prevent. Pass your cwd to get your Item."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"cwd": {"type": "string",
                                   "description": "the caller's working directory; omit and the Item is UNKNOWN"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "path.resolve",
        "description": (
            "Resolve a NAMED path reference to an absolute path. Deterministic "
            "and independent of the working directory. Use this instead of "
            "composing a durable path from string parts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"ref": {"type": "string",
                                   "description": "e.g. 'state/runtime/observed' or 'workspace/src'"},
                         "writing": {"type": "boolean", "default": True}},
            "required": ["ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "path.admit",
        "description": (
            "Check an absolute path against the root contract and classify it, "
            "or refuse it. Returns a decision; it writes nothing. Call this "
            "before producing output somewhere you chose yourself."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"},
                           "writing": {"type": "boolean", "default": True}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "runtime.list",
        "description": "Every resource registered as a managed runtime on this node.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "runtime.status",
        "description": (
            "The four-axis health of one managed runtime — liveness, transport, "
            "functional, readiness — plus what may be done about it. A running "
            "process is not a healthy one; read `functional`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "runtime.explain",
        "description": (
            "Why a runtime is or is not automatable, what its registration says, "
            "and the state of its restart budget."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "resource.summary",
        "description": (
            "Host resource pressure: CPU, memory, swap, GPU, and disk by role "
            "(root versus data). Advisory only — this surface never reclaims "
            "anything."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "resource.hygiene",
        "description": (
            "Container classification: protected, active, stale candidate, "
            "orphaned, unknown owner, stopped-reclaim candidate. Reports "
            "candidates; never acts on them, and a protected resource is never "
            "a candidate."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "observation.get",
        "description": "The most recent observation tick, what it saw, and any drift.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "ability.list",
        "description": (
            "The LOCAL capability projection for this node. NOT the authority: "
            "ability-management owns the Ability Registry. The response says "
            "which authority it is and whether this view is fresh or stale, so "
            "an empty answer is never confused with a current one."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "ability.status",
        "description": (
            "One ability as this node last observed it. May be stale; the "
            "response says so."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    # -- §4/§2: the discovery tree ------------------------------------------
    #
    # Two derivations of the projection above, and neither adds an argument a
    # caller could use to describe the node differently from how it is.
    {
        "name": "ability.tree",
        "description": (
            "The taxonomy this node derives from its local capability "
            "projection: the node set, where each observed ability falls, the "
            "abilities that are in no node, and the cross-references kept "
            "separately. Derived from classification metadata ALONE — never "
            "from where a file lives, so the tree can be reorganised without "
            "moving anything. An ability appears exactly once, under its "
            "primary. `nodes` is null rather than empty when nothing has been "
            "materialized: an empty tree would be a claim about "
            "ability-management that nobody has read."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "ability.inspect",
        "description": (
            "One ability as this node last observed it, plus where the tree "
            "places it and what it is cross-referenced from. Does NOT report "
            "whether this device can execute it: `resolution` is always "
            "`not attempted`, because that question is the projection "
            "intersected with the providers this device actually has, and "
            "`serverosctl ability resolve` is what answers it. Listed is not "
            "the same as executable."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    # -- §45/§46: the Agent fast path ----------------------------------------
    #
    # The two questions an Agent has before it can start, and the reason they
    # are here rather than in the catalogue it would otherwise read: which of
    # these fifty-odd names is mine, and can THIS node do it now. Both are
    # read-only, both answer from what this node already holds, and neither
    # loads the catalogue into the caller's context.
    {
        "name": "ability.search",
        "description": (
            "Find the few abilities a task needs, by substring, in this "
            "node's projection and catalogue, including product roles and trigger hints "
            "(not avoid hints). Returns compact rows and says how many "
            "matched beside how many it returned, so a truncated list never "
            "reads as a complete one. `matched` is null — not 0 — for an empty "
            "query and for no readable source: neither is a search that came back "
            "empty. Ask a question; this surface deliberately does not serve "
            "the whole catalogue, because loading it is the cost this exists "
            "to remove."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "e.g. 'document', 'test', 'runtime', a repo name, a domain"},
                "limit": {"type": "integer",
                          "description": "max rows to return; default 8, ceiling 50"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ability.resolve",
        "description": (
            "Read-only assessment for one ability/runtime using this node's pinned catalogue. "
            "Optional request supplies bounded evaluation inputs (64 KiB); these are caller "
            "claims, not verified observations or grants. Missing inputs remain unknown. "
            "The result never authorizes execution, and native tools do not require this call. "
            "Use ability:<name>#<verb> for an operation; runtime is explicit."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ability": {"type": "string",
                            "description": "a reference, e.g. 'ability:runtime-management' or 'ability:team-document-management#restore'"},
                "runtime": {"type": "string",
                            "description": "the Agent runtime asking, e.g. 'claude-code' or 'codex'"},
                "device": {"type": "string",
                           "description": "defaults to THIS node; state one to ask about another"},
                "request": {
                    "type": "object", "additionalProperties": False,
                    "description": "Evaluation only; cannot override catalogue, ability, runtime or device. Maximum 64 KiB, depth 12; provider/instance lists at most 32 objects.",
                    "properties": {
                        **{key: {"type": ["array", "null"], "maxItems": 32,
                                 "items": {"type": "object"}}
                           for key in ("providers", "instances")},
                        **{key: {"type": ["object", "null"], "maxProperties": 128}
                           for key in ("device_capabilities", "profile", "runtime_record",
                                       "runtime_binding", "health", "policy", "task_context")},
                    },
                },
            },
            "required": ["ability", "runtime"],
            "additionalProperties": False,
        },
    },
    # -- §3/§5: the runtime graph -------------------------------------------
    {
        "name": "runtime.groups",
        "description": (
            "Every runtime group on this node — the unit of reasoning is the "
            "group, never the pid, because one agent task routinely owns a "
            "proxy, a supervisor, several MCP servers and a browser helper. "
            "Judging them one at a time is how a live task gets half-reclaimed."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "runtime.group",
        "description": (
            "One runtime group: its members, what it costs, and the chain "
            "Device -> Transport -> Agent -> Task -> Processes -> MCP -> "
            "Container. Every hop is FACT, INFERRED or UNKNOWN, and the "
            "unknown ones are listed with their reason."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string",
                        "description": "a group ref, its id, or any member pid"},
            },
            "required": ["ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "runtime.tree",
        "description": (
            "One runtime group drawn out: the chain above plus the process "
            "tree beneath, including whether the root is reparented to init. "
            "Three processes that look like three harmless rows in `ps` are one "
            "thing when the lineage is drawn."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "runtime.budget",
        "description": (
            "The retry budget: what this node has restarted, how many attempts "
            "remain in the window, and what is blocked. A resource with no "
            "entry has never been acted on — which is NOT the same as healthy."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # -- §14: the process view ----------------------------------------------
    {
        "name": "process.top",
        "description": (
            "The busiest processes, each with what it belongs to: group, "
            "session, container and role. A process list cannot answer 'whose "
            "is this'; this can, and says UNKNOWN where it cannot."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10},
                "by": {"type": "string", "enum": ["cpu", "mem"], "default": "cpu"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "process.trace",
        "description": (
            "One pid, attributed: its lineage to the visibility boundary, its "
            "cgroup scope, its session, container and unit, and the fields that "
            "could not be established. `cpu_percent_lifetime` is an average, "
            "not a sample."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"pid": {"type": "integer"}},
            "required": ["pid"],
            "additionalProperties": False,
        },
    },
    # -- §10/§11: anomalies --------------------------------------------------
    {
        "name": "anomaly.list",
        "description": (
            "Every anomaly this node can support, with its named combination "
            "classification and — separately — the inputs the detector could "
            "NOT evaluate. Read `unchecked` before concluding anything: two of "
            "the fourteen codes are structurally unreachable on this host, and "
            "their absence is not evidence. Every recommendation is a proposal."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "anomaly.explain",
        "description": (
            "Why one subject classified as it did, INCLUDING which declared "
            "combinations did not fire and which signal each was missing. The "
            "negative half is the point: a classification without it cannot be "
            "argued with."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"subject": {"type": "string",
                                       "description": "a subject ref, e.g. a runtime-group ref"}},
            "required": ["subject"],
            "additionalProperties": False,
        },
    },
    # -- §6: containers ------------------------------------------------------
    {
        "name": "container.attribution",
        "description": (
            "Every container, attributed from evidence with the evidence kept "
            "beside the decision. An unknown owner is DEFER and is never a "
            "reclaim candidate. This surface can report; it cannot remove, "
            "stop or prune."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # -- §14/§15/§16/§16A/§17 -------------------------------------------------
    #
    # Five reads, and three of them exist because the tempting alternative is
    # a guess. Each takes NO argument, for the reason spelled out in
    # `daemon.handler_map`: a caller that can supply an identity, a protection
    # claim or a measurement can supply one that is not true, and this surface
    # would then be relaying a claim as an observation. An empty `inputSchema`
    # is the tool-surface form of that rule, and it is why these five are not
    # the `path.resolve` shape with a helpful optional override.
    {
        "name": "ports.ownership",
        "description": (
            "Every listening endpoint on this node and the claims on each one. "
            "Disagreements between claims are reported as conflicts rather "
            "than resolved, and a port nobody can be attributed to stays "
            "unattributed — which is the honest answer on a shared host, not a "
            "failure to look."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "desired.actual",
        "description": (
            "Every registration compared against what was measured about it. "
            "A registration whose desired state is unmeasurable is UNCHECKED "
            "and NOT a mismatch: `desired_state = None` is the absence of a "
            "comparison. Read `unchecked` before concluding anything from an "
            "empty `mismatches`."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # -- §6/SYS-02 -----------------------------------------------------------
    #
    # The fact SYS-02's dynamic environment produces had no way for an agent to
    # ask for it. An agent about to take a browser, a port or a profile can ask
    # what is already recorded against it, instead of discovering the conflict
    # by failing — which is the cheaper of the two ways to learn it.
    #
    # No argument, for the reason the group above gives: there is no parameter
    # through which a caller could assert an ownership class or a reference
    # count and have the daemon relay it as a measurement of this host. The
    # read is served from the daemon's own state directory, so a daemon started
    # with `--state-dir` answers about THAT directory.
    #
    # `environment.effective` is not here on purpose: three of its four terms
    # are not gathered by this process yet, so it would answer
    # `DENY / OWNER_UNKNOWN` for every resource on the node.
    {
        "name": "environment.leases",
        "description": (
            "Every resource in this node's `state/serveros/resource-leases/` "
            "namespace with something recorded against it: the desirable "
            "references (who wants it, in which scope, until when), and — where "
            "a release has happened — the verdict on whether the last reference "
            "out may let it go. A resource with no record is NO_LEASES_HELD. "
            "`recorded` is the number of records and NOT the number in use: a "
            "record exists for a crashed holder's expired claim and for a "
            "shared resource nobody currently wants, so read the references "
            "rather than the count. Nothing here stops, removes or restarts "
            "anything."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "runtime.hygiene",
        "description": (
            "The nine cleanliness classes over the registrations, with the "
            "evidence kept beside each decision. CLEANUP_CANDIDATE is a label "
            "attached to a question for a person and is never decided by one "
            "variable — and `stopped for N days` is not that variable."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "fs.hygiene",
        "description": (
            "The filesystem roots classified — canonical, allowed exception, "
            "legacy, misplaced, oversized, duplicate semantic root, unknown. "
            "Every class is a description: nothing here moves, renames or "
            "deletes anything, and duplicate semantic roots are reported "
            "rather than reunited."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "protection.resolve",
        "description": (
            "Whether each container on this node is protected, resolved over "
            "every spelling of its identity, plus any spelling where the "
            "answer differs. Protection is declared, never inferred: a name "
            "that contains 'prod' is not a permission level. Nothing here "
            "writes a label or registers a resource."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # -- §6/§7/§13 -----------------------------------------------------------
    #
    # Two reads §19's suggested surface names that had no tool behind them at
    # all. Both were reachable from `serverosctl` and from nothing else, which
    # is the shape this round keeps finding: the fact had a producer and no
    # way for an agent to ask for it.
    #
    # Neither takes an argument, and the Docker one is the case where that
    # costs something: `resource.docker` cannot be asked for the reclaimable
    # picture, because that is a `docker system df` measured at ~22s. The
    # description says so and names the CLI flag, so the limitation is a
    # sentence a caller can read rather than an argument that quietly did
    # nothing.
    {
        "name": "resource.docker",
        "description": (
            "The Docker inventory of this node: daemon summary and data root, "
            "containers with their writable-layer and rootfs sizes, images, "
            "volumes and networks, and the ten biggest containers by measured "
            "size. Nothing here is a cleanup candidate. The reclaimable and "
            "build-cache picture is deliberately NOT included — it is one "
            "`docker system df` call, measured at ~22s; ask for it with "
            "`serverosctl resource docker --space`."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "tunnel.list",
        "description": (
            "The tunnels this node can see, the forwarders it can see, and — "
            "the important field — what it structurally CANNOT see. Read "
            "`blind_spots` before concluding anything from `declared_count: "
            "0`: `/proc` is mounted with hidepid=invisible here, so an sshd "
            "forward is invisible to this surface by construction, and a "
            "declared tunnel is joined to a visible forwarder by PID rather "
            "than by guessing."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # -- §18 ----------------------------------------------------------------
    #
    # The fifteen resource anomaly codes, graded, with the codes that could NOT
    # be graded named separately. That second list is the answer, not a
    # footnote: `codes_graded` is what was looked for and not found, and
    # `codes_not_evaluated` is what was never looked for at all, and on this
    # node the difference is large — two codes need a `docker system df` nobody
    # paid for. A caller that read an empty `signals` as a healthy node would be
    # reading the second list as the first.
    {
        "name": "resource.anomaly.list",
        "description": (
            "The resource anomalies this node can currently see — disk and "
            "inode tiers, memory and swap pressure, Docker storage and build "
            "cache, container log and writable-layer sizes, unowned and "
            "orphaned runtimes, a protected resource standing on a filling "
            "filesystem, declared-vs-measured mismatches, and unhealthy or "
            "unowned tunnels. Read `codes_not_evaluated` before concluding "
            "anything from an empty `signals`: it names each code that could "
            "not be graded and why, and the reclaimable/build-cache picture is "
            "NOT included — it is one `docker system df` at ~22s, available "
            "with `serverosctl resource anomaly --space`. Every row is a "
            "measurement with its threshold beside it; nothing here stops, "
            "removes, prunes or restarts anything, and §25's two forbidden "
            "responses (`high CPU => kill`, `high memory => kill`) do not "
            "exist as code."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # -- §21: the alert register --------------------------------------------
    #
    # Two tools, and the absence of the other two is the design rather than an
    # omission. `alert.ack` and `alert.snooze` exist as daemon methods and as
    # CLI commands, and they are NOT here: this surface "holds no state and
    # changes nothing" (its own `initialize` block says so), and §11 makes
    # acknowledgement a statement about a person's attention. §18 leaves
    # whether an agent may make that statement to the existing human-identity
    # policy; that policy has no way to establish which person an agent call is
    # speaking for, so the write stays where a person is: the CLI.
    {
        "name": "alert.list",
        "description": (
            "Every alert this node holds, most in need of attention first. An "
            "alert is an anomaly given a stable identity, so the same problem "
            "persisting across ticks is ONE row with an occurrence count rather "
            "than a new finding each minute. Read two fields before concluding "
            "anything from the list: `hold_reason` — this node has a reason not "
            "to repeat it, which is not the same as it being fixed — and "
            "`delivery_failure` — it was said and did not arrive. Nothing here "
            "stops, restarts, kills or reclaims anything; an alert is a "
            "finding, not an authorization."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "alert.inspect",
        "description": (
            "One alert in full, plus the exact sentence a person would have "
            "been sent. `rendered` is the message and not a summary of one, so "
            "it is the thing to read when the question is what somebody is "
            "being told. `found: false` is an answer and not an error: an "
            "alert ref is derived from code, device and subject, so a ref that "
            "never existed and one that was never opened look the same here."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"alert_ref": {"type": "string",
                                         "description": "e.g. alert:0f3a…, from alert.list"}},
            "required": ["alert_ref"],
            "additionalProperties": False,
        },
    },
)


def tool_names() -> tuple[str, ...]:
    return tuple(tool["name"] for tool in TOOLS)


# ---------------------------------------------------------------------------
# The transport
# ---------------------------------------------------------------------------


def default_socket_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime_dir) / "serveros/serveros.sock"


class DaemonClient:
    """One request per connection over the daemon's Unix socket."""

    def __init__(self, socket_path: Path | None = None,
                 timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.socket_path = Path(socket_path) if socket_path else default_socket_path()
        self.timeout = timeout

    def call(self, method: str, params: dict | None = None) -> dict:
        if not self.socket_path.exists():
            raise DaemonUnavailable(
                f"serverosd is not listening: {self.socket_path} does not exist. "
                f"Start it with `serverosd`, or check `systemctl --user status "
                f"serverosd`. This surface has no cached answer and will not "
                f"invent one."
            )
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        try:
            connection.connect(str(self.socket_path))
            connection.sendall(
                json.dumps({"method": method, "params": params or {}}).encode() + b"\n"
            )
            buffer = b""
            while b"\n" not in buffer:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                buffer += chunk
        except socket.timeout as exc:
            # Caught BEFORE the `OSError` clause, because `socket.timeout` is one
            # (`TimeoutError` since 3.10). Written the other way round — as this
            # was — a tick that ran long and a daemon that was not there produce
            # the same sentence, and the sentence says "did not answer", which a
            # reader takes as absence.
            raise DaemonBusy(
                f"serverosd did not answer {method!r} on {self.socket_path} "
                f"within {self.timeout:.0f}s. It is running its tick — "
                f"`serverosd` serves on the same single thread that runs the "
                f"tick, so a request arriving mid-tick waits for it — and this "
                f"request is still queued on it. This is not a missing "
                f"measurement. Retry, or read `observation.get` for the tick's "
                f"own recorded duration."
            ) from exc
        except OSError as exc:
            raise DaemonUnavailable(
                f"serverosd did not answer {method!r} on {self.socket_path}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        finally:
            connection.close()

        if not buffer.strip():
            raise DaemonUnavailable(
                f"serverosd closed the connection without answering {method!r}."
            )
        try:
            response = json.loads(buffer.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DaemonUnavailable(
                f"serverosd returned something that is not JSON: {buffer[:200]!r}"
            ) from exc
        if not response.get("ok"):
            error = response.get("error") or {}
            raise DaemonRefused(
                f"serverosd refused {method!r}: {error.get('code')}: "
                f"{error.get('message')}"
            )
        return response.get("result")


# ---------------------------------------------------------------------------
# JSON-RPC over stdio
# ---------------------------------------------------------------------------


def _result(request_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


class NodeMcpServer:
    """The MCP surface. Every tool is one forwarded call, with no local logic.

    `client` is injectable so the tests can exercise the protocol against a
    fake daemon instead of a live one. That is the whole reason there is no
    inline socket code in the handlers: a surface that can only be tested
    against a running control plane is a surface that gets tested by hand.
    """

    def __init__(self, client=None) -> None:
        self.client = client or DaemonClient()

    # -- MCP lifecycle -----------------------------------------------------

    def handle(self, request: dict) -> dict | None:
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params") or {}

        if method == "initialize":
            return _result(request_id, {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                # Said once, up front, so a client does not have to infer it
                # from an error message later.
                "instructions": (
                    "This surface reports what this node has observed. It "
                    "holds no state and changes nothing. Paths it returns come "
                    "from the node's root contract; do not compose durable "
                    "paths yourself — call path.resolve and path.admit. "
                    "Ability data is a LOCAL projection: ability-management is "
                    "the authority, and every ability response says whether "
                    "what you are looking at is fresh or stale."
                ),
            })
        if method in ("notifications/initialized", "initialized"):
            return None  # a notification: no response, ever
        if method == "tools/list":
            return _result(request_id, {"tools": [dict(t) for t in TOOLS]})
        if method == "tools/call":
            return self._call_tool(request_id, params)
        if method == "ping":
            return _result(request_id, {})
        if str(method).startswith("notifications/"):
            return None
        # `resources/list` and `prompts/list` are intentionally not implemented:
        # this surface declares no resources and no prompts, and an empty list
        # for a capability that was never offered is a claim that it is empty
        # rather than that it does not exist.
        return _error(request_id, -32601, f"Method not found: {method!r}")

    def _call_tool(self, request_id, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") or {}

        if name not in tool_names():
            # Named, not silently empty. A misspelled tool must not look like a
            # tool that returned nothing.
            return _result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text":
                             f"Unknown tool {name!r}. Available: {list(tool_names())}"}],
            })

        allowed = set(TOOLS[tool_names().index(name)]["inputSchema"]["properties"])
        unexpected = set(arguments) - allowed
        if unexpected:
            return _result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text":
                             f"{name} got unexpected argument(s) {sorted(unexpected)}. "
                             f"Accepted: {sorted(allowed)}"}],
            })

        try:
            result = self.client.call(name, arguments)
        except DaemonUnavailable as exc:
            # isError, with the reason intact. The alternative — returning an
            # empty result — would tell the caller the node is healthy and
            # unremarkable, which is the failure this whole architecture is
            # about.
            return _result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text":
                             f"ServerOS node surface unavailable: {exc}"}],
            })

        return _result(request_id, {
            "content": [{"type": "text",
                         "text": json.dumps(result, indent=2, sort_keys=True,
                                            default=str)}],
            "isError": False,
        })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def serve(instream=None, outstream=None, server=None) -> int:
    """One JSON-RPC object per line. Returns when stdin closes."""
    instream = instream or sys.stdin
    outstream = outstream or sys.stdout
    server = server or NodeMcpServer()

    for line in instream:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            # No id is recoverable from unparseable input, so id is null. The
            # spec allows it, and silence here would hang the client.
            _write(outstream, _error(None, -32700, f"Parse error: {exc}"))
            continue
        if not isinstance(request, dict):
            _write(outstream, _error(None, -32600, "Request must be an object"))
            continue
        try:
            response = server.handle(request)
        except Exception as exc:  # noqa: BLE001 - the client must not hang
            response = _error(request.get("id"), -32603,
                              f"Internal error: {type(exc).__name__}: {exc}")
        if response is not None:  # notifications get no reply
            _write(outstream, response)
    return 0


def _write(outstream, payload: dict) -> None:
    outstream.write(json.dumps(payload, default=str) + "\n")
    outstream.flush()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="serveros-node-mcp",
        description="ServerOS node MCP surface. A thin adapter over serverosd; "
                    "holds no state and changes nothing.",
    )
    parser.add_argument("--socket", help="serverosd Unix socket path")
    parser.add_argument("--list-tools", action="store_true",
                       help="print the tool surface and exit")
    args = parser.parse_args(argv)

    if args.list_tools:
        for tool in TOOLS:
            print(f"{tool['name']:<20} {tool['description'].splitlines()[0]}")
        return 0

    server = NodeMcpServer(DaemonClient(Path(args.socket) if args.socket else None))
    return serve(server=server)


if __name__ == "__main__":
    sys.exit(main())
