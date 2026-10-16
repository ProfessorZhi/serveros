#!/usr/bin/env python3
"""serveros-agent-mcp — the Agent Control surface (§11).

Three authorities meet here and this process is none of them:

  * `serverosd` decides policy. Every mutation is admitted there before any
    provider sees it, and this surface has no path to a provider that skips it.
  * The provider owns its session. This surface reads provider state and sends
    typed intents; it never reconstructs a session from a PID, a container name
    or a tmux pane.
  * `fleet/` owns device identity and routing. A request names a device, the
    router decides whether that device is reachable, and a device with no node
    runtime answers NODE_RUNTIME_UNAVAILABLE rather than being quietly served
    from the machine the caller happens to be sitting on.

What this surface deliberately does not have
--------------------------------------------

  * No `remote.shell`, no `docker rm`, no path to a provider that bypasses
    admission, and no direct read or write of a state database. An MCP call
    reaches exactly one of three things: the fleet router, the fleet view, or
    the daemon's own admission/context methods.
  * No mutation without a `context_ref`. A caller's statement about who it is
    is a string; a `context_ref` is the one thing it can present that ServerOS
    resolved beforehand (§22). The actor is never an argument.
  * No `agent.session.interrupt`. §11 names `stop` and `resume`; `interrupt` is
    a provider-flavoured verb whose meaning is the provider's (the BotMux
    contract already records that its close is not an interrupt). The surface
    serves the semantic lifecycle verb and declines the provider's, and the
    contract records the decline rather than leaving it to be inferred from an
    absence.
  * No fourth MCP server. The fleet-level reads live here rather than in a
    `serveros-fleet-mcp`, which `fleet/mcp-contract.yaml` declared for two
    revisions without an implementation behind it. See that file's note.
  * No port, filesystem or protection read. §19 of the resource-observation
    brief extends the EXISTING agent control surface, and the decision of which
    surface carries a new read is §11's own rule: the surface that owns the
    fact. A listening socket, a declared-versus-measured comparison, a runtime
    class and a filesystem root's classification are all facts about ONE
    machine, so they went on `serveros-node` as `ports.ownership`,
    `desired.actual`, `runtime.hygiene`, `fs.hygiene` and `protection.resolve`.
    Declining them here is recorded rather than left to be inferred from their
    absence, because an absent tool and a tool nobody wrote are the same empty
    list and two different facts.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


node_mcp = _load("serveros_mcp_node_shared", _HERE / "node_mcp.py")
service_mod = _load("serveros_agent_session_service", _HERE / "agent_session_service.py")
botmux_mod = _load("serveros_botmux_provider", _HERE / "botmux_provider.py")
codex_mod = _load("serveros_codex_provider", _HERE / "codex_app_server_provider.py")

# Loaded under the same module names the rest of the tree uses, so everything
# holding a `FleetAgentControlRouter` or a `FleetView` holds the SAME classes.
# Loading one of these a second time under a second name yields two class
# objects that `isinstance` refuses to relate, and nothing about that failure
# says what happened. `fleet/agent_control_router.py` loads before
# `fleet/view.py` because both pull in `fleet/transport.py` and the router
# names it by full path.
router_mod = _load("serveros_fleet_agent_control_router",
                   _ROOT / "fleet" / "agent_control_router.py")
transport_mod = _load("serveros_fleet_transport", _ROOT / "fleet" / "transport.py")
view_mod = _load("serveros_fleet_view", _ROOT / "fleet" / "view.py")
target_mod = _load("serveros_abilities_target", _ROOT / "abilities" / "target.py")
# §17. Same synthetic name as everywhere else — this module is loaded once and
# the roles enum below is read from it, so a role added to the kernel reaches
# the tool schema without a second edit that could be forgotten.
coordination_mod = _load("serveros_runtime_coordination",
                         _ROOT / "runtime" / "coordination.py")
# §L–§P. The Remote Console. Loaded here because THIS is the surface an agent
# inside a BotMux turn can reach: the console's levels are computed from session
# rows, and the only process that holds a provider is this one. §N's UI is
# delivered into the conversation by the agent that calls this tool.
human_console_mod = _load("serveros_runtime_human_console",
                          _ROOT / "runtime" / "human_console.py")
# §P. The runtime feature vocabulary that decides whether a provider-specific
# operation is offered. Read from its owner rather than restated — the whole
# point of §P is that ONE vocabulary decides, and a second list here would be
# the second spelling this repository keeps paying for.
runtime_binding_mod = _load("serveros_abilities_runtime_binding",
                            _ROOT / "abilities" / "runtime_binding.py")

PROTOCOL_VERSION = node_mcp.PROTOCOL_VERSION
SERVER_NAME = "serveros-agent-mcp"
# Taken from the node adapter rather than re-typed here. Two literals is how a
# release ends up with one surface at one version and another at another; there
# is still exactly one literal in this tree, and giving it a canonical home is
# tracked. Re-typing it here would add a third answer, not remove one.
SERVER_VERSION = node_mcp.SERVER_VERSION

# The actions §11 calls mutations. Every one of them requires a `context_ref`;
# the router refuses a mutating action without one, and `_mutating()` below
# derives that from the session contract rather than from this list.
CONTROL_ACTIONS = (
    "agent.session.create",
    "agent.session.send",
    "agent.session.resume",
    "agent.session.stop",
    "agent.session.handoff",
)


def _schema(properties=None, required=None) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


_STR = {"type": "string"}
# The enum is read from the kernel rather than retyped. A literal here would be
# a second copy of a closed vocabulary, and the second copy is the one that
# falls behind — which on this field means a caller building a thread whose
# purpose the router then refuses.
_ROLE = {
    "type": "string",
    "enum": list(coordination_mod.ROLES),
    "description": (
        "What this conversation is for. `execution` requires bound_item_ref; "
        "`coordinator` is forbidden one."
    ),
}
_CONTEXT = {
    "type": "string",
    "description": (
        "A trusted context minted by ServerOS. The actor behind this call is "
        "resolved from it and is never taken from the caller."
    ),
}
_RUNTIME = {
    "type": "string",
    "description": (
        "An `agent-runtime:<id>` ref. Required and never derived: which runtimes "
        "a device DECLARES is an authoring statement, and routing on a "
        "declaration would make a registry entry into a capability."
    ),
}
_DEVICE = {"type": "string", "description": "A stable `device:<id>` ref."}
_LEVEL_SHALLOW = {
    "type": "string", "enum": ["L0", "L1"], "default": "L1",
    "description": (
        "How much this answer costs. L0 reads the authored registry alone and "
        "touches no clock; L1 adds one reachability observation per device and "
        "still contacts nothing."
    ),
}
_LEVEL_DEEP = {
    "type": "string", "enum": ["L2", "L3"], "default": "L2",
    "description": (
        "L2 reaches the device for its provider inventory and is the cheapest "
        "level that can answer 'could this device run this now'; L3 adds the "
        "provenance of every field."
    ),
}
# The console's level is a different axis from the two above: it prices what
# ServerOS says about a SESSION, not about a device. It spans all four because
# L0 is the whole point of §N — the cheap answer a person reads first — and L3
# is where the diagnostics a suspicious reader asks for live. Sharing
# `_LEVEL_SHALLOW` here would have made L0 the console's default while meaning
# "a device read that touches no clock".
_LEVEL_ALL = {
    "type": "string", "enum": ["L0", "L1", "L2", "L3"], "default": "L1",
    "description": (
        "L0 is counts only and loads no session row, no transcript and no "
        "skill; L1 is one card per session; L2 adds the operations that may be "
        "performed, each priced; L3 adds the provider's own fields and the "
        "provenance of every one."
    ),
}


def _envelope(*extra: dict) -> dict:
    """Address keys shared by the control actions, plus this action's own."""
    base = {
        "device": _DEVICE,
        "runtime": _RUNTIME,
        "session_ref": _STR,
        "context_ref": _CONTEXT,
        "workspace": _STR,
        "work_item": _STR,
        "timeout_s": {"type": "number"},
    }
    for group in extra:
        base.update(group)
    return base


TOOLS: tuple[dict, ...] = (
    # -- fleet reads -------------------------------------------------------
    {"name": "device.list",
     "description": (
        "Enrolled devices and what was observed about each. A device nobody "
        "reached reports UNKNOWN with a reason; it is never served as an empty "
        "row or an empty provider list."
     ),
     "inputSchema": _schema({"level": _LEVEL_SHALLOW})},
    {"name": "device.inspect",
     "description": (
        "One enrolled device. Not a synonym for 'connect and enumerate' — it "
        "answers successfully while the device is unreachable, and says so."
     ),
     "inputSchema": _schema({"device": _DEVICE, "level": _LEVEL_SHALLOW},
                            ["device"])},
    {"name": "agent.runtime.list",
     "description": (
        "The Agent Runtimes this product declares. Which one a particular "
        "device can run is a device fact and is answered by device.inspect."
     ),
     "inputSchema": _schema()},
    {"name": "agent.runtime.inspect",
     "description": "One declared Agent Runtime and what it can consume.",
     "inputSchema": _schema({"runtime": _RUNTIME}, ["runtime"])},
    {"name": "agent.provider.list",
     "description": (
        "Provider inventory and readiness on one device, with the observation "
        "time on each readiness entry rather than a verdict. Reaches the device."
     ),
     "inputSchema": _schema({"device": _DEVICE, "level": _LEVEL_DEEP}, ["device"])},
    {"name": "agent.provider.inspect",
     "description": "One provider on one device, from the same inventory.",
     "inputSchema": _schema({"device": _DEVICE, "provider": _STR,
                             "level": _LEVEL_DEEP}, ["device", "provider"])},
    {"name": "agent.list",
     "description": (
        "The agents one device's providers report, each with its owning "
        "provider's own readiness record. This is the MEASURED counterpart of "
        "`device.inspect`'s `declared_agents`, and the finding it exists to "
        "make readable is the one place it is visible: agents listed, provider "
        "unable to execute. Readiness travels here rather than behind its own "
        "tool so the surface stays exactly as wide as the session contract "
        "declares it. A provider that could not be asked is reported in "
        "`provider_errors` rather than omitted, and `complete` says whether the "
        "list is the whole fleet or a partial one."
     ),
     "inputSchema": _schema({"device": _DEVICE, "level": _LEVEL_DEEP}, ["device"])},
    {"name": "agent.inspect",
     "description": (
        "One agent, out of the same inventory `agent.list` serves — so the two "
        "cannot answer differently about which agents exist. An id matching no "
        "agent and an id matching several are reported as the two different "
        "answers they are, each naming what the device did list."
     ),
     "inputSchema": _schema({"device": _DEVICE, "agent_id": _STR}, ["device", "agent_id"])},
    {"name": "agent.session.list",
     "description": (
        "Active Agent Sessions on one device. Local sessions are enumerated; a "
        "device without a node runtime answers NODE_RUNTIME_UNAVAILABLE rather "
        "than an empty list."
     ),
     "inputSchema": _schema({"device": _DEVICE, "level": _LEVEL_DEEP}, ["device"])},
    {"name": "agent.session.inspect",
     "description": (
        "One session, by provider-native state. A session is never inferred "
        "from a PID, a container name or a pane."
     ),
     "inputSchema": _schema(_envelope(), ["session_ref", "runtime"])},
    {"name": "agent.session.result",
     "description": "The provider's asynchronous result for a session or turn.",
     "inputSchema": _schema(_envelope({"trigger_id": _STR}),
                            ["session_ref", "runtime"])},
    {"name": "agent.console",
     "description": (
        "§N's progressive console over one device's sessions, rendered into "
        "whatever conversation the caller is in. This is what a person in "
        "Feishu reads: L0 counts only and carries no session row; L1 one card "
        "per session (session_ref, runtime, device, bound_item, status, last "
        "activity); L2 adds what may be done, priced per operation; L3 adds the "
        "provider's own fields, copied and never interpreted.\n\n"
        "An operation the provider does not have is RETURNED and marked "
        "UNSUPPORTED rather than omitted, so a person can tell 'this cannot be "
        "done' from 'nobody thought of it'. A session's `close` never reports "
        "as `interrupt`: the two are different verbs and the console will not "
        "pass one off as the other. `bound_item` comes from §17's thread "
        "binding and not from the provider — no provider knows what a session "
        "is for.\n\n"
        "`conversation_ref` names the conversation whose selected session this "
        "is about, which is how a person answers 'which one am I talking to'."
     ),
     "inputSchema": _schema({"device": _DEVICE, "level": _LEVEL_ALL,
                             "conversation_ref": _STR}, ["device"])},

    # -- control actions. §11: every one of these requires a context_ref. ---
    {"name": "agent.session.create",
     "description": (
        "Create a provider session on a named device, after ServerOS "
        "admission. `provider` is NOT a parameter: it comes from the resolved "
        "target, so a caller cannot use a resolution it did not ask for and "
        "cannot see. Requires context_ref.\n\n"
        "`session_role` is required and has no default. Say what this "
        "conversation is FOR: `execution` for a thread doing one Item's work, "
        "which then requires `bound_item_ref`; `coordinator` for a thread that "
        "crosses Items and is therefore bound to none. The role is frozen at "
        "creation and cannot be changed — a thread that needs a different Item "
        "needs a new thread."
     ),
     "inputSchema": _schema(
         _envelope({"agent_id": _STR, "instruction": _STR,
                    "idempotency_key": _STR, "session_role": _ROLE,
                    "bound_item_ref": _STR}),
         ["device", "runtime", "agent_id", "instruction",
          "idempotency_key", "context_ref", "session_role"])},
    {"name": "agent.session.send",
     "description": (
        "Send one follow-up turn to an existing session, after admission. "
        "Requires context_ref."
     ),
     "inputSchema": _schema(
         _envelope({"instruction": _STR, "turn_idempotency_key": _STR}),
         ["session_ref", "runtime", "instruction",
          "turn_idempotency_key", "context_ref"])},
    {"name": "agent.session.resume",
     "description": "Resume an existing session, after admission. Requires context_ref.",
     "inputSchema": _schema(_envelope(), ["session_ref", "runtime", "context_ref"])},
    {"name": "agent.session.stop",
     "description": (
        "Stop an existing session, after admission. Requires context_ref. "
        "Stops the session; it does not signal a process this control plane "
        "does not own."
     ),
     "inputSchema": _schema(_envelope(), ["session_ref", "runtime", "context_ref"])},
    {"name": "agent.session.handoff",
     "description": (
        "§9's server-side procedure: checkpoint the source, resolve and start "
        "the target, hand the context over, and only then release the source. "
        "The source is never stopped before the target has accepted. Requires "
        "context_ref."
     ),
     "inputSchema": _schema(
         _envelope({"checkpoint": _STR, "to_device_ref": _DEVICE,
                    "to_session_ref": _STR, "target_action": _STR,
                    "target_params": {"type": "object"},
                    "release_source": {"type": "boolean"}}),
         ["session_ref", "runtime", "context_ref"])},
)


def tool_names() -> tuple[str, ...]:
    return tuple(tool["name"] for tool in TOOLS)


def tool(name: str) -> dict:
    return TOOLS[tool_names().index(name)]


def declared_actions() -> frozenset[str]:
    """The control actions the router contract declares, read from it.

    Read rather than restated: this surface serves §11's five mutations and the
    contract is where their parameter closure lives. A second list here would
    be a second answer to "what can be asked", and it would be the one that
    falls behind.
    """
    return frozenset(router_mod.control_actions())


# ---------------------------------------------------------------------------
# The context store, resolved one record at a time through serverosd.
# ---------------------------------------------------------------------------

class DaemonContexts(Mapping):
    """What ServerOS minted, asked for through the daemon.

    A `context_ref` arrives over stdio; the record behind it lives in the
    daemon's store. This mapping is the join between the two, and it is
    deliberately lazy: `actor.resolve` calls `.get(ref)` for the one ref on the
    request, and copying the store into a dict here would materialise records
    this call is not about.

    A mapping exists rather than a resolver function because `actor.resolve` is
    the ONE reading of what a well-formed context ref is, and it takes a
    mapping. Handing it anything else would mean this file deciding what a ref
    looks like — the second reading this design exists to avoid.
    """

    def __init__(self, daemon) -> None:
        self._daemon = daemon

    def get(self, ref, default=None):
        answer = self._daemon.call("agent.context.resolve", {"context_ref": str(ref)})
        if not (answer or {}).get("found"):
            return default
        return answer.get("context")

    def __getitem__(self, ref):
        found = self.get(ref, _MISSING)
        if found is _MISSING:
            raise KeyError(ref)
        return found

    def __iter__(self):
        # Refused rather than approximated with an empty iterator. This store
        # has no local index; enumerating it would be a scan of the daemon's
        # records, and answering `[]` would say the store is empty — which is a
        # claim no caller is in a position to make.
        raise TypeError(
            "the context store behind this surface is not enumerable; ask for "
            "one ref and it is resolved for that ref alone")

    def __len__(self):
        raise TypeError("the context store behind this surface has no count")

    def __bool__(self):
        # True, and not a claim about the contents.
        #
        # `__len__` raises, so without this every `store or {}` in the tree
        # would raise instead of reading "a store was supplied". That idiom
        # tests for `None`, and `None` is the only false answer here: this
        # object is a live handle on the daemon's records, and how many there
        # are is not something it knows. Returning False would say the store is
        # empty; raising would break a caller that asked a different question.
        return True


class DaemonBindings:
    """§17's thread bindings, read and written through serverosd.

    Same join as `DaemonContexts` and for the same reason: the records live in
    the daemon's control store, this process holds a socket to it, and copying
    the store into a dict here would materialise bindings this call is not
    about. `get` is called once per gated turn for the one ref on the request.

    WHY THIS RAISES INSTEAD OF ANSWERING "NO BINDING"
    -------------------------------------------------
    The loopback reads `get(...)` and feeds the answer to `coordination.admit_work`,
    which refuses with INVALID_CONTEXT when there is no binding. That refusal is
    correct for a thread nobody recorded. It is exactly the wrong answer for a
    store that could not be read: it would report "this session was never bound"
    about a session that is bound, and the repair it names — create a session for
    the Item you mean — is not the repair for a daemon that is down.

    So an unanswered call raises rather than returning an empty read. The router
    catches `TransportFailure` around dispatch and turns it into a typed refusal,
    which keeps the vocabulary closed and keeps the two failures apart. This is
    the same distinction the daemon's own handlers draw; getting it wrong here
    would restore, one layer up, the "absent and unreadable look identical"
    failure that §17 is about.

    `put` is a write, so it has no safe degraded mode at all: a create whose
    binding could not be stored must fail loudly. An unbound thread refuses every
    later turn, which is the correct side to be wrong on — see `_record`.
    """

    # The fields the daemon's own write path reads. Named rather than forwarded
    # wholesale: `schema`, `state`, `bound_item_ref_frozen` and `recorded_at` are
    # the daemon's to decide, and a caller that could pass them would be able to
    # create a thread that arrives already closed or already frozen by somebody
    # else's clock.
    FIELDS = ("session_ref", "session_role", "bound_item_ref", "device_ref")

    def __init__(self, daemon) -> None:
        self._daemon = daemon

    def _call(self, method: str, params: dict) -> dict:
        try:
            return self._daemon.call(method, params) or {}
        except Exception as exc:  # noqa: BLE001 - any failure to reach the daemon
            raise router_mod.TransportFailure(
                "NODE_RUNTIME_UNAVAILABLE",
                f"{method} could not be answered by serverosd: "
                f"{type(exc).__name__}: {exc}. ServerOS cannot state what this "
                f"thread is for, and a thread whose purpose cannot be stated "
                f"admits no work") from exc

    def get(self, ref) -> dict:
        answer = self._call("agent.binding.resolve", {"session_ref": str(ref)})
        # Both halves of one read. `admit_work` joins them, and joining two
        # separately-fetched records would decide about a thread using an Item
        # state from a different moment than the binding. The CURRENT WRITER
        # (#237 B1) travels in the same answer for the same reason: the gate
        # compares the binding's claim against the scope's owner, and two
        # separately-fetched records would be a decision about a moment that
        # never existed.
        return {"binding": answer.get("binding"), "item": answer.get("item"),
                "current_writer": answer.get("current_writer"),
                "found": bool(answer.get("found"))}

    def put(self, record) -> dict:
        answer = self._call("agent.binding.record", {
            key: record[key] for key in self.FIELDS if record.get(key) is not None})
        if not answer.get("recorded"):
            raise router_mod.TransportFailure(
                str(answer.get("reason") or "INVALID_CONTEXT"),
                str(answer.get("detail") or "serverosd did not record the binding"))
        return answer.get("binding") or {}

    def records_for(self, refs) -> dict:
        """The bindings for the sessions a caller just listed, in one read.

        One call rather than one per session, because the console builds an L1
        card for every session it was given and N round trips per page is a
        latency the reader pays for no extra truth. The filter is on the
        session refs the caller already holds: this cannot ask about a thread it
        has no ref for, which is the same shape `agent.binding.resolve` has.
        """
        answer = self._call("agent.binding.list",
                            {"session_refs": [str(r) for r in refs]})
        return answer.get("records") or {}

    def put_selection(self, *, conversation_ref: str, session_ref: str,
                      device_ref: str | None = None) -> dict:
        """§M's one console write. Refused by the daemon, not by a check here.

        The record is built in `serverosd` — by
        `human_console.selection_record`, which is the only thing that knows a
        selection may not carry an Item. A client that assembled one would be a
        second place that decides what a selection is, and the field §M forbids
        would be one caller away from being recorded.
        """
        params = {"conversation_ref": conversation_ref, "session_ref": session_ref}
        if device_ref:
            params["device_ref"] = device_ref
        answer = self._call("agent.selection.record", params)
        if not answer.get("selected"):
            raise _BadRequest(
                str(answer.get("reason") or "INVALID_CONTEXT"),
                str(answer.get("detail") or "serverosd did not record the selection"))
        return answer.get("selection") or {}

    def selection_for(self, conversation_ref: str, *, live_session_refs=None) -> dict:
        """What this conversation is addressing, and whether it is still there.

        The live list is passed IN rather than fetched here, because the daemon
        holds no provider and the comparison is against what the caller can see.
        Omitting it does not make the answer wrong; it makes it *partial*, and
        the daemon says so in `basis` rather than reporting a stale selection as
        current.
        """
        params: dict = {"conversation_ref": conversation_ref}
        if live_session_refs is not None:
            params["live_session_refs"] = [str(r) for r in live_session_refs]
        return self._call("agent.selection.resolve", params)


_MISSING = object()


def _refused(reason_code: str, detail: str, **extra) -> dict:
    """A refusal in §8's closed vocabulary, and nothing outside it.

    A refusal is an answer. It carries a word a caller can switch on and a
    sentence a human can act on, and it never reuses a transport outcome for a
    policy outcome.
    """
    return {"state": "REFUSED", "reason_code": reason_code, "detail": detail, **extra}


# ---------------------------------------------------------------------------
# The tool implementations.
# ---------------------------------------------------------------------------

class AgentControlClient:
    """§11's tool set, over the fleet router and the fleet view.

    Both are injected. The router answers control actions and owns the routing
    decision; the view answers fleet questions and owns what each disclosure
    level costs. Neither is reimplemented here — this class is a dispatch table
    and the argument shaping that belongs to a typed boundary.
    """

    def __init__(self, *, router, view, bindings=None, registry=None) -> None:
        self.router = router
        self.view = view
        # §17/§M. The two console joins. `bindings` is the daemon handle
        # (read the thread, write the selection); `registry` is where the
        # device's declared runtimes live, which §P needs to price an
        # operation. Both default to None so that every existing caller — and
        # the 40-odd tests that build this class directly — keeps working, and
        # the console reports UNKNOWN for what it was not given rather than
        # inventing it.
        self._bindings = bindings
        self._registry = registry

    # -- reads ---------------------------------------------------------------

    def _envelope_of(self, level: str, device_refs=None) -> dict:
        return self.view.render(level=level, device_refs=device_refs)

    def device_list(self, level: str = "L1") -> dict:
        return self._envelope_of(level)

    def device_inspect(self, device: str, level: str = "L1") -> dict:
        return self._envelope_of(level, [device])

    def agent_runtime_list(self) -> dict:
        rows = []
        for runtime_id in target_mod.runtime_ids():
            record = router_mod.runtime_record(runtime_id) or {}
            rows.append({
                "runtime_ref": target_mod.runtime_ref(runtime_id),
                "runtime_id": runtime_id,
                "consumable_provider_kinds": list(
                    record.get("consumable_provider_kinds") or ()),
            })
        return {
            "state": "DECLARED",
            "count": len(rows),
            "runtimes": rows,
            "note": (
                "A runtime in this list exists as a product fact. It does not "
                "follow that any device can run it; that is a device fact and "
                "device.inspect is where it is measured-or-UNKNOWN."
            ),
        }

    def agent_runtime_inspect(self, runtime: str) -> dict:
        """One declared runtime, or a NOT_FOUND that says what IS declared.

        There is one absence here, not two, so there is one branch. An earlier
        draft also handled `runtime_record()` returning None, which cannot
        happen: `parse_runtime_ref` and `runtime_record` read the same contract,
        so a ref that parsed is a ref that has a record. The branch was dead
        code that read as a second, more careful case — and the case it claimed
        to handle was the one a caller actually hits, since the ref is rejected
        before a record is ever looked up.
        """
        try:
            runtime_id = target_mod.parse_runtime_ref(runtime)
        except Exception as exc:  # noqa: BLE001 - a bad ref is a caller mistake
            return {
                "state": "NOT_FOUND",
                "runtime": runtime,
                "because": f"{type(exc).__name__}: {exc}",
                "declared": [target_mod.runtime_ref(rid)
                             for rid in target_mod.runtime_ids()],
            }
        return {"state": "DECLARED", **router_mod.runtime_record(runtime_id)}

    def _deep_row(self, device: str, level: str) -> dict:
        return self._envelope_of(level, [device])["devices"][0]

    def agent_provider_list(self, device: str, level: str = "L2") -> dict:
        row = self._deep_row(device, level)
        return {
            "device_ref": row["device_ref"],
            "state": row["providers"].get("state"),
            "providers": row["providers"],
            "provider_instances": row["provider_instances"],
            "provider_readiness": row["provider_readiness"],
            "note": (
                "Readiness is an observation with a time on it, not a verdict. "
                "A stale observation is reported as stale rather than as the "
                "provider being dead."
            ),
        }

    def agent_provider_inspect(self, device: str, provider: str,
                               level: str = "L2") -> dict:
        listing = self.agent_provider_list(device, level)
        rows = (listing["providers"].get("providers") or [])
        found = [row for row in rows if row.get("id") == provider]
        if not found:
            # Absent, and the answer says which absence it is: the device
            # listed providers and this was not among them, or the device never
            # answered. Those are different repair actions.
            return {
                "device_ref": listing["device_ref"],
                "state": "NOT_FOUND",
                "provider": provider,
                "inventory_state": listing["state"],
                "providers_listed": [row.get("id") for row in rows],
                "because": (
                    "this device did not list this provider. If inventory_state "
                    "is not KNOWN, that is because the device did not answer, "
                    "and the listing above is what nobody established rather "
                    "than what was ruled out."
                ),
            }
        return {
            "device_ref": listing["device_ref"],
            "state": "KNOWN",
            "provider": found[0],
            "provider_readiness": listing["provider_readiness"],
        }

    def agent_list(self, device: str, level: str = "L2") -> dict:
        """The agents one device's providers report, readiness included.

        Unpacked to the top level rather than nested under a key, because this
        is the record `_readiness_summary` in `agent_session_service` points a
        caller at by name: "the full record already travels on `agent.list`
        under `providers[]`". Nesting it would make that sentence false.

        The `state` word is always present and the three inventory keys are
        absent when nothing was measured. That asymmetry is the point — a
        caller that read `agents: []` off an unreachable device would take
        "nobody looked" for "there are none", and those have different repairs.
        """
        row = self._deep_row(device, level)
        inventory = row["agents"]
        if not isinstance(inventory, dict):
            return {"device_ref": row["device_ref"], "state": "UNKNOWN",
                    "reason": "the view served no agent inventory for this device"}
        # Passed through WHOLE, not rebuilt from the keys this method happens to
        # know. The unknown record carries a `detail` that names the device that
        # was asked and the device this control plane holds providers for, and
        # an earlier draft of this method dropped it by listing `state` and
        # `reason` by hand — which left the refusal without the half that says
        # what to do about it.
        return {"device_ref": row["device_ref"], **inventory}

    def agent_inspect(self, device: str, agent_id: str, level: str = "L2") -> dict:
        """One agent, resolved against the same list `agent.list` serves.

        Composed from `agent_list` rather than from the service directly, for
        the reason `agent_provider_inspect` is composed from
        `agent_provider_list`: an inspect that read its own source could report
        an agent the listing does not show, and a caller reconciling the two
        would have two answers to one question.
        """
        listing = self.agent_list(device, level)
        rows = listing.get("agents")
        if rows is None:
            # Same passthrough rule as `agent_list`: the unmeasured case is
            # answered with the inventory's own words about why, plus the id
            # that was asked about. `NOT_FOUND` would be a claim about the
            # fleet, and nobody established one.
            return {**listing, "agent_id": agent_id}
        matches = [row for row in rows
                   if row.get("agent_id") == agent_id
                   or row.get("agent_ref") == agent_id]
        if not matches:
            return {"device_ref": listing["device_ref"], "state": "NOT_FOUND",
                    "agent_id": agent_id,
                    "agents_listed": [row.get("agent_id") for row in rows],
                    "inventory_complete": listing.get("complete"),
                    "because": ("this device's providers did not list this "
                                "agent. `agents_listed` is what they did list, "
                                "and a misspelling and an unenrolled agent are "
                                "different repairs")}
        if len(matches) > 1:
            return {"device_ref": listing["device_ref"], "state": "AMBIGUOUS",
                    "agent_id": agent_id,
                    "matched": [row.get("agent_ref") for row in matches],
                    "because": ("two agents answer to this id, so naming one "
                                "would be this surface choosing which was "
                                "meant. The agent_ref is the unambiguous "
                                "spelling")}
        return {"device_ref": listing["device_ref"], "state": "FOUND",
                "agent": matches[0],
                "inventory_complete": listing.get("complete"),
                "provider_errors": listing.get("provider_errors")}

    def agent_session_list(self, device: str, level: str = "L2") -> dict:
        row = self._deep_row(device, level)
        return {"device_ref": row["device_ref"], "active_sessions": row["active_sessions"]}

    # -- the console (§L–§P) -------------------------------------------------

    def agent_console(self, device: str, level: str = "L1",
                      conversation_ref: str | None = None) -> dict:
        """§N's levels over this device's sessions, joined to what they are for.

        Four reads, one answer, and the reader is told when any of them was
        missing rather than being handed a hole:

            sessions     the provider rows, from the service — this process's,
                         because it is the only one holding a provider
            bindings     §17's, from serverosd, one read for every session
            features     §P's vocabulary, from the runtime binding for this
                         device
            selection    §M's, for the conversation the caller named

        **Why the join is here and not in the console module.** The console
        takes rows and answers about levels; it reaches nothing. This method
        reaches four planes and produces no meaning of its own — every sentence
        in the answer is `human_console`'s. A console that fetched would be a
        console that could be given a provider and would eventually decide
        something.
        """
        # The view's level and the console's level are different axes: the
        # first prices what ServerOS says about a DEVICE, the second what it
        # says about a SESSION. Asking the view for the deepest row and letting
        # `human_console.levels` project downward is what keeps the device
        # question from being answered by the console's argument.
        row = self._deep_row(device, "L3")
        sessions = list((row.get("active_sessions") or {}).get("sessions") or [])

        bindings: dict = {}
        binding_state = "KNOWN"
        binding_because = None
        try:
            bindings = self._bindings.records_for(
                [s.get("session_ref") for s in sessions])
        except Exception as exc:  # noqa: BLE001 - a missing read is reported
            # Not an empty dict. "No thread has a purpose" and "the store did
            # not answer" are different, and only one of them means every card's
            # bound_item is genuinely absent.
            binding_state = "UNKNOWN"
            binding_because = (f"the binding store did not answer "
                               f"({type(exc).__name__}: {exc}), so no card below "
                               f"says what its thread is for")

        features = self._session_features(row.get("device_ref"))
        axis = None
        if conversation_ref:
            try:
                axis = (self._bindings.selection_for(
                    conversation_ref,
                    live_session_refs=[s.get("session_ref") for s in sessions])
                    or {}).get("axis")
            except Exception as exc:  # noqa: BLE001
                axis = {"state": "UNKNOWN",
                        "because": (f"the selection store did not answer "
                                    f"({type(exc).__name__}: {exc})")}

        payload = human_console_mod.levels(
            level, sessions, bindings=bindings, features=features,
            action_support=self._support_for(sessions, axis), selected=axis)
        payload["device_ref"] = row.get("device_ref")
        payload["binding_state"] = binding_state
        if binding_because:
            payload["binding_because"] = binding_because
        if conversation_ref:
            payload["conversation_ref"] = conversation_ref
        return payload

    def _support_for(self, sessions: list, axis: dict | None) -> dict:
        """The action support of the session a selection points at, or nothing.

        `action_support` is a PER-SESSION map — the provider saying which verbs
        it has for THIS session — so there is no device-wide answer that would
        be true. With a session selected, that session's map is the one to price
        operations with. With nothing selected there is no session to ask about
        and every operation reports UNKNOWN, which is the honest reading of
        "nobody asked" and is why this returns an empty map rather than a
        hopeful one.
        """
        ref = (axis or {}).get("session_ref")
        if not ref:
            return {}
        for session in sessions:
            if session.get("session_ref") == ref:
                return session.get("action_support") or {}
        # Selected but not in this device's list: a selection is a pointer and
        # the session may be on another device or gone. Returning {} would say
        # "the provider does not have these verbs", which is a claim about a
        # provider nobody asked.
        return {}

    def _session_features(self, device_ref: str | None) -> dict | None:
        """§P's feature map for this device's runtimes, or None and why.

        Read from `abilities/runtime_binding.py`, which owns the observation.
        None means the vocabulary was not consulted and every operation is
        priced on the provider's answer alone — the console's `_apply_feature_gate`
        treats an absent map as "nothing to narrow", which is correct and must
        not be silently confused with "every feature is available".
        """
        if self._registry is None or not device_ref:
            return None
        try:
            listing = runtime_binding_mod.list_bindings(self._registry, device_ref)
        except Exception:  # noqa: BLE001 - an unread vocabulary narrows nothing
            return None
        merged: dict = {}
        for record in listing.get("bindings") or []:
            for name, state in ((record.get("levels") or {}).get("L2") or {}).get(
                    "features", {}).items():
                # First writer wins and a disagreement is kept as UNKNOWN.
                # Two runtimes on one device may differ, and a console that let
                # the last one read decide would make which runtime gated an
                # operation depend on list order.
                if name not in merged:
                    merged[name] = state
                elif merged[name] != state:
                    merged[name] = "UNKNOWN"
        return merged

    # -- control actions -----------------------------------------------------

    def _payload(self, name: str, arguments: dict) -> dict:
        """The router's own request payload, built from a tool call.

        Shaped here and validated there. `request_from_payload` is the thing
        that closes the envelope and closes `params` per action, and calling it
        rather than constructing `AgentControlRequest` directly is what keeps
        one definition of "a parameter this action may carry" — which is the
        definition a command string would have to get past.
        """
        payload: dict = {
            "action": name,
            "device_ref": self._device_for(name, arguments),
            "agent_runtime": str(arguments.get("runtime") or ""),
            "params": {},
            "session_ref": arguments.get("session_ref"),
            "context_ref": arguments.get("context_ref"),
            "workspace": arguments.get("workspace"),
            "work_item": arguments.get("work_item"),
        }
        # §17's envelope fields, and they are added ONLY when the caller supplied
        # one. The envelope is closed per action, and a key present holding
        # `None` is present: setting these unconditionally made every `send`,
        # `resume` and `stop` fail the closure with "does not take
        # ['bound_item_ref', 'session_role']". The closure lives in
        # `request_from_payload` and is not repeated here — this is about not
        # sending a field the caller never wrote, which is a different claim.
        for field_name in ("session_role", "bound_item_ref"):
            if arguments.get(field_name) is not None:
                payload[field_name] = arguments[field_name]
        # `approvals` is deliberately NOT built here, and the omission is the
        # enforcement rather than an oversight. The router's
        # `request_from_payload` accepts the key and threads it into every
        # admission decision, so adding a line for it would make this surface a
        # presenter of approvals — and `approval.decide` compares an approval's
        # fields against the request, which a caller able to compute a
        # fingerprint can satisfy. The node's position is that the interface an
        # Agent speaks has no approval verb (`agent.approval.record` refuses on
        # every served call, `agent.control.status` publishes it); a presenter
        # here would replace that with a surface that does not grant approval
        # only because nobody has typed the arguments yet.
        if arguments.get("timeout_s") is not None:
            payload["timeout_s"] = arguments["timeout_s"]
        declared = set(router_mod.action_params().get(name, ())) | set(
            router_mod.action_optional().get(name, ()))
        for key in declared:
            if key in arguments:
                payload["params"][key] = arguments[key]
        # The router's replay key, taken from the parameter that already means
        # "this intent, once". A second key on the envelope that a caller could
        # set independently of the parameter would be two answers to one
        # question, and the one that took effect would be whichever the router
        # read first.
        replay = arguments.get("idempotency_key") or arguments.get("turn_idempotency_key")
        if replay:
            payload["idempotency_key"] = replay
        return payload

    def _device_for(self, name: str, arguments: dict) -> str:
        """The device this call is addressed to.

        A session ref already names its device, so a caller that supplies both
        is held to them agreeing. Servicing a ref on a device other than the one
        it names would answer a question nobody asked, and the two-part answer
        would look like a successful call.
        """
        ref = arguments.get("session_ref")
        named = str(arguments.get("device") or "").strip()
        if ref:
            try:
                from_ref = router_mod.semantics.parse_session_ref(ref)[0]
            except Exception as exc:  # noqa: BLE001 - a bad ref is a caller mistake
                raise _BadRequest(
                    "INVALID_CONTEXT",
                    f"{ref!r} is not an Agent Session ref: {exc}") from exc
            if named and named != from_ref:
                raise _BadRequest(
                    "INVALID_CONTEXT",
                    f"device {named!r} was named but the session ref belongs to "
                    f"{from_ref!r}; a session runs on one device and this call "
                    f"will not pick which")
            return from_ref
        if not named:
            raise _BadRequest("INVALID_CONTEXT",
                              f"{name} names no device and carries no session ref "
                              f"to take one from")
        return named

    def _control(self, name: str, arguments: dict) -> dict:
        payload = self._payload(name, arguments)
        try:
            runtime_id = target_mod.parse_runtime_ref(payload["agent_runtime"])
        except Exception as exc:  # noqa: BLE001 - a bad ref is a caller mistake
            return _refused("INVALID_CONTEXT",
                            f"{payload['agent_runtime']!r} is not an Agent Runtime "
                            f"ref: {exc}")
        try:
            request = router_mod.request_from_payload(payload)
        except Exception as exc:  # noqa: BLE001 - a malformed request is a mistake
            return _refused("INVALID_CONTEXT", f"{type(exc).__name__}: {exc}")
        if name == "agent.session.handoff":
            return self.router.handoff(request).show()
        return self.router.route(request).show()

    def call(self, name: str, arguments: dict | None = None) -> dict:
        args = dict(arguments or {})
        try:
            if name == "device.list":
                return self.device_list(args.get("level") or "L1")
            if name == "device.inspect":
                return self.device_inspect(args["device"], args.get("level") or "L1")
            if name == "agent.runtime.list":
                return self.agent_runtime_list()
            if name == "agent.runtime.inspect":
                return self.agent_runtime_inspect(args["runtime"])
            if name == "agent.provider.list":
                return self.agent_provider_list(args["device"], args.get("level") or "L2")
            if name == "agent.provider.inspect":
                return self.agent_provider_inspect(
                    args["device"], args["provider"], args.get("level") or "L2")
            if name == "agent.list":
                return self.agent_list(args["device"], args.get("level") or "L2")
            if name == "agent.inspect":
                return self.agent_inspect(args["device"], args["agent_id"],
                                          args.get("level") or "L2")
            if name == "agent.session.list":
                return self.agent_session_list(args["device"], args.get("level") or "L2")
            if name == "agent.console":
                return self.agent_console(args["device"], args.get("level") or "L1",
                                          args.get("conversation_ref"))
        except _BadRequest:
            raise
        except Exception as exc:  # noqa: BLE001 - a read must still answer
            # An unknown device ref, or a disclosure level this build does not
            # serve. Both are the request being wrong, not the fleet being
            # unavailable, and saying DEVICE_UNREACHABLE here would report a
            # typed unavailability about a machine that was never contacted.
            return _refused("INVALID_CONTEXT", f"{type(exc).__name__}: {exc}")

        if name in ("agent.session.inspect", "agent.session.result"):
            return self._read_through(name, args)
        if name in CONTROL_ACTIONS:
            return self._control(name, args)
        raise RuntimeError(f"unimplemented Agent MCP operation: {name}")

    def _read_through(self, name: str, arguments: dict) -> dict:
        """A read that still has to reach the device it is about.

        These are `read_only` in the session contract, so no context is
        required — but they are routed like everything else, because the answer
        is the provider's and a locally-composed one would be this surface
        answering on a provider's behalf.
        """
        payload = self._payload(name, arguments)
        try:
            target_mod.parse_runtime_ref(payload["agent_runtime"])
            request = router_mod.request_from_payload(payload)
        except Exception as exc:  # noqa: BLE001 - a bad request is a caller mistake
            return _refused("INVALID_CONTEXT", f"{type(exc).__name__}: {exc}")
        return self.router.route(request).show()


class _BadRequest(ValueError):
    """A tool call that cannot be addressed, raised rather than returned.

    Returned refusals are findings about the fleet; this is a mistake in the
    request with nothing to observe. The server catches this one and reports it
    as a tool error rather than dressing it as a refusal with a reason word
    from the fleet vocabulary, because no fleet fact was established.
    """

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


# ---------------------------------------------------------------------------
# The deployment. One wiring, shared by `main()` and by the tests.
# ---------------------------------------------------------------------------

def build(*, daemon=None, providers=None, registry=None, local_device_ref=None):
    """The real graph: one daemon, one loopback, one node transport, one view.

    The local device ref comes from `serverosd device.show` — which is an
    authored value, not a hostname. Deriving it from the machine is the one
    thing §4 forbids, and the router refuses a ref that is not `device:*`.
    """
    daemon = daemon or node_mcp.DaemonClient()
    # Normalised once, so the registry the router routes against and the one
    # §P prices operations against are the same object. Two `DeviceRegistry()`
    # instances would be two reads of one file, and they can disagree after an
    # edit — which would make a runtime the router knows about a runtime the
    # console says does not exist.
    registry = registry or transport_mod.DeviceRegistry()
    device_ref = local_device_ref or _authored_device_ref(daemon)
    service = _service(daemon, providers, device_ref)
    loopback = router_mod.LocalLoopbackTransport(
        device_ref=device_ref, service=service,
        # §17. The real handle on the real store, so a create over this surface
        # records what its thread is for in the same place the daemon's own
        # answers read it from. A loopback given anything weaker would make the
        # MCP path the one path §17 does not hold on.
        bindings=DaemonBindings(daemon))
    node = router_mod.NodeControlTransport(connector=None, device_refs=_remote_refs(registry))
    router = router_mod.FleetAgentControlRouter(
        local_device_ref=device_ref, transports=[loopback, node],
        registry=registry, context_store=DaemonContexts(daemon),
    )
    view = view_mod.FleetView(routing=router,
                              sessions=_sessions_for(service, device_ref),
                              agents=_agents_for(service, device_ref))
    # §M/§P. The console's two inputs. `bindings` is the SAME daemon handle the
    # loopback uses, so a selection recorded through the console is read back by
    # the path that gates turns, and there is no second writer. `registry` is
    # the device declarations §P prices operations against.
    return AgentControlClient(router=router, view=view,
                              bindings=DaemonBindings(daemon),
                              registry=registry)


def _authored_device_ref(daemon) -> str:
    view = daemon.call("device.show", {})
    ref = str((view or {}).get("device_ref") or "").strip()
    if not ref:
        raise RuntimeError("serverosd device.show returned no authored device_ref")
    return ref


def _remote_refs(registry=None) -> tuple[str, ...]:
    registry = registry or transport_mod.DeviceRegistry()
    return tuple(device.device_ref.ref for device in registry.all())


def _sessions_for(service, local_device_ref: str):
    """What the view asks about sessions: a callable, not a store.

    The view takes a callable so that it cannot grow its own session ledger,
    which would be a second record of what the providers own. This one answers
    for the device it was built for and knows nothing about any other.

    "Knows nothing about any other" is now enforced rather than asserted. This
    closure used to accept a `device_ref` and answer anyway: the parameter was
    received, ignored, and `service.session_list()` was called for whatever the
    view asked about. A call for `device:server-beijing` therefore came back
    `{"state": "KNOWN", "sessions": []}` — this node's empty session list,
    wearing a remote device's name. `fleet/view.py` says the opposite in the
    same words ("Local for the node this process is on; typed-unavailable
    otherwise"), so the two files had agreed on the contract and disagreed on
    the behaviour, which is the failure this round keeps recording.

    The damage was not a missing answer but a wrong one. `state: KNOWN` with an
    empty list reads as "that device has no active sessions" — a claim about a
    machine nobody asked. UNKNOWN with a reason is the honest reading of the
    same situation, and it is what the view's own vocabulary is for.
    """
    def sessions(device_ref: str) -> dict:
        if str(device_ref) != local_device_ref:
            return view_mod.unknown(
                "this session source answers for one device only, and this is "
                "not it",
                detail=(f"the view asked about {device_ref}; this control plane "
                        f"owns {local_device_ref}. Reaching another device's "
                        f"sessions needs a transport that has actually asked it"))
        listing = service.session_list()
        sessions = listing.get("sessions")
        if sessions is None:
            return {"state": listing.get("state", "UNKNOWN"),
                    "reason": listing.get("reason")}
        return {"state": "KNOWN", "sessions": sessions}
    return sessions


def _agents_for(service, local_device_ref: str):
    """What the view asks about agents: a callable, not a store.

    The same shape as `_sessions_for`, one provider method over, and the same
    two rules. It answers for ONE device — the one this process holds providers
    for — and a question about any other device is UNKNOWN with a reason rather
    than this node's answer wearing that device's name.

    `SessionService.agent_list` is the producer, and it was, until this was
    wired, a producer with no consumer: it was reachable only from
    `agent_inspect` and from tests, while `runtime/agent-session-contract.yaml`
    declared `agent.list` read-only and two docstrings told a reader the record
    travelled on it. That gap is what this closure exists to close, and the
    reason it is closed here rather than by having the tool call the service
    directly is that "a device this control plane does not hold providers for"
    is a case the service cannot see and the view already knows how to spell.
    """
    def agents(device_ref: str) -> dict:
        if str(device_ref) != local_device_ref:
            return view_mod.unknown(
                "this agent source answers for one device only, and this is "
                "not it",
                detail=(f"the view asked about {device_ref}; this control plane "
                        f"holds providers for {local_device_ref}. An agent "
                        f"inventory is what a PROVIDER reports, so reaching "
                        f"another device's needs a provider running there"))
        record = service.agent_list()
        # `state: KNOWN` even when `provider_errors` is not empty, and
        # deliberately: `agent_list` answers for every configured provider and
        # reports the ones that failed beside the ones that did not, so a
        # partial list is a KNOWN partial answer rather than an unknown one.
        # That is why the service carries its own `complete` flag — a reader
        # that saw only `state` would take a partial fleet for the whole one.
        return {"state": "KNOWN", **record}
    return agents


def _service(daemon, providers, device_ref: str):
    if providers is None:
        # Both real providers of this deployment, neither privileged over the
        # other. A provider that is down lists itself in `provider_errors`
        # rather than being omitted, so "this deployment is small" and "one
        # member is unreachable" stay different answers.
        providers = [botmux_mod.BotMuxProvider(), codex_mod.CodexAppServerProvider()]

    def admit(action: str, context: dict) -> dict:
        """The admission callable, and the last seam before the daemon.

        `context_ref` is lifted OUT of `context` and onto the request, because
        that is where the daemon reads it: its handler treats `context` as the
        subject being adjudicated and the context_ref as a field of the call,
        and the actor is resolved from that field alone (§5 forbids the payload
        carrying identity). So it has to be promoted here or not at all — a
        `context_ref` left sitting inside `context` would be echoed back to the
        caller as part of the subject and never resolved into an actor, which is
        a human-gated verb answered before its subject was read.

        It is absent by default, and absent is not "empty string": a service
        that was not handed one sends exactly the payload it sent before this
        line existed. That is what keeps a read-only or delegation call — the
        overwhelming majority — decided by the contract alone, with no store
        read and no dependence on a control-plane file being readable.

        `approvals` is deliberately NOT promoted. The daemon reads approvals
        from its own store, keyed by the actor it resolved; accepting them from
        a caller would make this surface the grantor of the very thing
        `agent.approval.record` refuses to grant, and `approval.decide` cannot
        tell a fabricated record from a real one.
        """
        context_ref = context.get("context_ref")
        if context_ref is None:
            return daemon.call("agent.action.admit",
                               {"action": action, "context": context})
        return daemon.call("agent.action.admit", {
            "action": action, "context": context, "context_ref": context_ref,
        })

    return service_mod.SessionService(
        device_ref=device_ref, providers=list(providers), admission=admit)


# ---------------------------------------------------------------------------
# The protocol surface.
# ---------------------------------------------------------------------------

class AgentMcpServer:
    def __init__(self, client=None) -> None:
        self.client = client or build()

    def handle(self, request: dict) -> dict | None:
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params") or {}
        if method == "initialize":
            return node_mcp._result(request_id, {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Agent Session state is provider-backed. ServerOS owns device "
                    "identity, routing and admission; this MCP owns none of them. "
                    "Every mutating tool resolves its actor from context_ref — the "
                    "actor is never an argument — and calls serverosd admission "
                    "before the provider. A device with no node runtime answers "
                    "NODE_RUNTIME_UNAVAILABLE; it is never served from the machine "
                    "the caller is on."
                ),
            })
        if method in ("notifications/initialized", "initialized"):
            return None
        if method == "tools/list":
            return node_mcp._result(request_id, {"tools": [dict(t) for t in TOOLS]})
        if method == "tools/call":
            return self._call_tool(request_id, params)
        if method == "ping":
            return node_mcp._result(request_id, {})
        if str(method).startswith("notifications/"):
            return None
        return node_mcp._error(request_id, -32601, f"Method not found: {method!r}")

    def _call_tool(self, request_id, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in tool_names():
            return node_mcp._result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text":
                             f"Unknown tool {name!r}. Available: {list(tool_names())}"}],
            })
        spec = tool(name)
        allowed = set(spec["inputSchema"]["properties"])
        required = set(spec["inputSchema"].get("required") or ())
        # An `actor` argument is refused here rather than ignored. Dropping it
        # silently would let a caller believe the name it supplied was the one
        # that got recorded, and the recorded actor is the whole point of
        # requiring a context_ref.
        unexpected = set(arguments) - allowed
        missing = required - set(arguments)
        if unexpected or missing:
            problem = []
            if unexpected:
                problem.append(f"unexpected={sorted(unexpected)}")
            if missing:
                problem.append(f"missing={sorted(missing)}")
            return node_mcp._result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text": f"{name}: {'; '.join(problem)}"}],
            })
        try:
            result = self.client.call(name, arguments)
        except _BadRequest as exc:
            result = _refused(exc.reason_code, exc.detail)
        except Exception as exc:  # provider/admission failures are named tool errors
            return node_mcp._result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text":
                             f"{name} failed: {type(exc).__name__}: {exc}"}],
            })
        return node_mcp._result(request_id, {
            "content": [{"type": "text", "text":
                         json.dumps(result, indent=2, sort_keys=True, default=str)}],
            "isError": False,
        })


def serve(instream=None, outstream=None, server=None) -> int:
    return node_mcp.serve(instream=instream, outstream=outstream,
                          server=server or AgentMcpServer())


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="serveros-agent-mcp")
    parser.add_argument("--socket", help="serverosd Unix socket path")
    parser.add_argument("--local-device",
                        help="this runtime's stable device ref; defaults to the "
                             "authored value serverosd reports")
    parser.add_argument("--list-tools", action="store_true")
    args = parser.parse_args(argv)
    if args.list_tools:
        for spec in TOOLS:
            print(f"{spec['name']:<24} {spec['description']}")
        return 0
    client = build(daemon=node_mcp.DaemonClient(Path(args.socket) if args.socket else None),
                   local_device_ref=args.local_device)
    return serve(server=AgentMcpServer(client))


if __name__ == "__main__":
    sys.exit(main())
