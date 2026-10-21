"""The CloudCLI native surface, inherited whole — as a closed, typed set.

What is inherited
-----------------
CloudCLI 1.39 exposes exactly 22 MCP tools on its gateway. Fleet inherits ALL of
them, each as `fleet_cloudcli_<native_name>`, so a caller discovers one typed
tool per native verb instead of the seven hand-written ones this surface used to
carry. The set is recorded here, once, and everything else reads it.

Why a closed set, and not a proxy
---------------------------------
`providers/base.py` refuses, deliberately, to expose a generic
`call(device, tool_name)`: "a provider that could forward any provider tool would
make the Fleet surface a transparent proxy to a device — the caller would be
choosing the operation, and there would be nothing left in the call to govern."
This module keeps that refusal and answers it. The mirror is not a passthrough;
it is a DECLARED set of 22 entries, each carrying its own native scope, its own
Fleet scope, and its own governance class. A tool name nobody recorded is not
forwarded — it is refused, because a name that arrived from the wire is not a
capability this build decided to offer.

Read and write never share an inheritance
-----------------------------------------
The native scopes are positional in CloudCLI's own vocabulary
(`cloudcli:read` / `session:send` / `session:create` / `session:control` /
`approve` / `navigate`). They are mapped here onto Fleet's scopes, and the
mapping is deliberately LOSSY IN ONE DIRECTION ONLY: a native verb can never
require LESS than the Fleet verb it maps to, and a native scope Fleet does not
implement (`approve`, `navigate`) is mapped to a Fleet scope that is *planned
and not implemented* — so those two tools are DENIED today rather than silently
inherited from a read grant. `approvals_list` is a read; `approval_answer` is
not, and a credential trusted to list is not thereby trusted to answer.

The recorded baseline and the live list
---------------------------------------
`NATIVE_TOOLS` is the recorded baseline — what CloudCLI 1.39.0 declares. The
provider already fetches the LIVE `tools/list` on every `health()`. `coverage()`
compares the two and reports drift (a tool that appeared, one that vanished, an
annotation that moved), so an upgrade is a diff a person reads rather than a
silent difference in what the mirror forwards.

`NATIVE_INPUT_SCHEMAS` IS a re-recording, and it is deliberate rather than
incidental: `tools/list` is answered for the whole surface before any device is
dialled, so the published `arguments` schema needs a device-independent baseline,
and `mirrored_schema` / `validate_arguments` read the same table so that what is
advertised and what is enforced cannot disagree. The cost is the one this
paragraph used to deny: a restated schema drifts, silently, the first time
CloudCLI changes a parameter. That is paid for by
`tests/test_fleet_cloudcli_full_inheritance.py`'s capture tests, which hold this
table against a VERBATIM 1.39.0 `tools/list` capture — a restatement checked
against the thing it restates, rather than trusted.

No I/O, no clock, no credentials. This module is a table and the functions over
it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

PREFIX = "fleet_cloudcli_"

#: CloudCLI's own scope vocabulary, in its own spelling. Fleet matching is by
#: the substring after `cloudcli:`; the native string is carried so a refusal can
#: name what the DEVICE wants, which is not the same as what this surface grants.
NATIVE_READ = "cloudcli:read"
NATIVE_SEND = "cloudcli:session:send"
NATIVE_CREATE = "cloudcli:session:create"
NATIVE_CONTROL = "cloudcli:session:control"
NATIVE_APPROVE = "cloudcli:approve"
NATIVE_NAVIGATE = "cloudcli:navigate"

#: Native scope -> the Fleet scope a caller must present. A native scope with no
#: implemented Fleet counterpart maps to a PLANNED Fleet scope and is therefore
#: denied by `Principal.require` — which is the point: inheriting a device's verb
#: must not be able to widen what a Fleet credential is trusted to do.
FLEET_SCOPE_FOR_NATIVE: dict[str, str] = {
    NATIVE_READ: "fleet:read",
    NATIVE_SEND: "fleet:send",
    NATIVE_CREATE: "fleet:create",
    NATIVE_CONTROL: "fleet:control",
    NATIVE_APPROVE: "fleet:approve",      # planned, not implemented -> denied
    NATIVE_NAVIGATE: "fleet:navigate",    # planned, not implemented -> denied
}

#: Governance classes, coarser than scope: which Fleet write verb (if any) and
#: which per-device flag a call must satisfy. `read` is the only class that
#: inherits nothing; every other class needs an explicit grant.
GOV_READ = "read"
GOV_SEND = "send"
GOV_CREATE = "create"
GOV_CONTROL = "control"
GOV_APPROVE = "approve"
GOV_NAVIGATE = "navigate"
#: `session_reconfigure` stops nothing; it CHANGES a session's model, effort and
#: permission mode. That is a change to the session's authority, so it carries
#: the create-side ceiling as well as the control floor — its own class rather
#: than `control`, which would leave the model/mode unbounded.
GOV_RECONFIGURE = "reconfigure"

#: The governance classes whose Fleet floor is stated over ONE session. The
#: outer schema must therefore both DECLARE `session_ref` and REQUIRE it, and
#: these are ONE predicate read twice rather than two lists.
#:
#: They were two lists, and they disagreed: `reconfigure` and `approve` had the
#: key in `required` while `properties` never declared it, under a top-level
#: `additionalProperties: false` — so a client that validates its own call
#: against the published schema had to supply a field the same schema forbade,
#: and the two tools were uncallable by anything that validates. Nothing in the
#: runtime noticed, because the runtime reads `session_ref` from the request and
#: never from the schema.
#:
#: `approve` belongs here on the runtime's own terms, not by symmetry: an
#: approval is answered for one request in one session, and
#: `_verify_approval_ownership` parses `session_ref` before it will enumerate
#: that session's requests. Leaving it out of this tuple would have declared a
#: tool whose required key the runtime demands and the schema did not — the same
#: defect with the sign flipped.
SESSION_ADDRESSED_GOVERNANCE: tuple[str, ...] = (GOV_SEND, GOV_CONTROL,
                                                 GOV_RECONFIGURE, GOV_NAVIGATE,
                                                 GOV_APPROVE)

#: The one class whose floor is stated over a PROJECT rather than a session.
PROJECT_ADDRESSED_GOVERNANCE: tuple[str, ...] = (GOV_CREATE,)

#: The READS whose disclosure the runtime also scopes to one session.
#:
#: A read is not session-addressed as a class: `session_read` names its session
#: inside `arguments`, and `projects_list` addresses nothing at all. The approval
#: register is the exception, and on the runtime's own terms rather than by
#: symmetry: a device-wide list is every other principal's pending decisions, so
#: `fleet.py` refuses an `approvals_list` that names no session and injects the
#: session id into `arguments` before it enumerates. A schema that did not ask
#: for `session_ref` therefore published a call its own runtime refuses — the
#: same defect `session_reconfigure` and `approval_answer` were fixed for, with
#: the sign flipped.
SESSION_ADDRESSED_READS: frozenset[str] = frozenset({"approvals_list"})


def is_session_addressed(native: "NativeTool") -> bool:
    """Whether this tool's Fleet address is ONE session rather than a device.

    One predicate, read by the schema builder for both the declared property and
    the required key, so the two halves cannot disagree — which is exactly how a
    tool came to require a key its own schema forbade.
    """
    return (native.governance in SESSION_ADDRESSED_GOVERNANCE
            or native.name in SESSION_ADDRESSED_READS)


@dataclass(frozen=True)
class NativeTool:
    """One native CloudCLI tool, as this surface declares it."""

    name: str
    governance: str
    #: Every native scope this verb needs. Two entries for a dual-scope tool
    #: (`session_background`: list `read` / stop `control`).
    native_scopes: tuple[str, ...]
    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool
    #: `True` when the recorded default handler is the named
    #: `MCP_TOOL_NOT_IMPLEMENTED` refusal rather than a wired one — the fact a
    #: `fleet_status` mirror must be able to report without calling the tool.
    placeholder_default: bool = False
    #: The gateway's own zod input schema SOURCE, recorded only where it was read
    #: off the installed build. `None` means "not recorded here; read it from the
    #: live tools/list" — never "these are the arguments".
    input_schema_zod: str | None = None

    @property
    def namespaced(self) -> str:
        return PREFIX + self.name

    def fleet_scopes(self) -> tuple[str, ...]:
        return tuple(FLEET_SCOPE_FOR_NATIVE[s] for s in self.native_scopes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "namespaced": self.namespaced,
            "governance": self.governance,
            "native_scopes": list(self.native_scopes),
            "fleet_scopes": list(self.fleet_scopes()),
            "read_only": self.read_only,
            "destructive": self.destructive,
            "idempotent": self.idempotent,
            "open_world": self.open_world,
            "placeholder_default": self.placeholder_default,
            "input_schema_zod": self.input_schema_zod,
        }


def _t(name: str, governance: str, *native_scopes: str, read_only: bool,
       destructive: bool, idempotent: bool, open_world: bool,
       placeholder_default: bool = False,
       input_schema_zod: str | None = None) -> NativeTool:
    return NativeTool(name=name, governance=governance, native_scopes=tuple(native_scopes),
                      read_only=read_only, destructive=destructive, idempotent=idempotent,
                      open_world=open_world, placeholder_default=placeholder_default,
                      input_schema_zod=input_schema_zod)


#: The 22 tools. Order is CloudCLI's own grouping: the eleven stage-3 reads, the
#: five stage-4 writes, then the six that register through their own modules. The
#: annotations are CloudCLI's (`MCP_TOOL_ANNOTATIONS`, read from the installed
#: 1.39.0), not this surface's opinion — carried, never re-derived.
NATIVE_TOOLS: tuple[NativeTool, ...] = (
    # -- the eleven reads, all cloudcli:read ---------------------------------
    _t("overview", GOV_READ, NATIVE_READ, read_only=True, destructive=False,
       idempotent=True, open_world=False, placeholder_default=True,
       input_schema_zod="{ project: z.string().optional() }"),
    _t("projects_list", GOV_READ, NATIVE_READ, read_only=True, destructive=False,
       idempotent=True, open_world=False,
       input_schema_zod="{ includeArchived: z.boolean().optional() }"),
    _t("sessions_list", GOV_READ, NATIVE_READ, read_only=True, destructive=False,
       idempotent=True, open_world=False,
       input_schema_zod="{ project?, state?, limit?, includeArchived? }"),
    _t("session_get", GOV_READ, NATIVE_READ, read_only=True, destructive=False,
       idempotent=True, open_world=False,
       input_schema_zod="{ session: z.string() }"),
    _t("session_read", GOV_READ, NATIVE_READ, read_only=True, destructive=False,
       idempotent=True, open_world=False),
    _t("run_get", GOV_READ, NATIVE_READ, read_only=True, destructive=False,
       idempotent=True, open_world=False, placeholder_default=True),
    _t("quay_snapshot", GOV_READ, NATIVE_READ, read_only=True, destructive=False,
       idempotent=True, open_world=False, placeholder_default=True,
       input_schema_zod="{ project: z.string().optional(), refresh: z.boolean().optional() }"),
    _t("ui_last_opened_session", GOV_READ, NATIVE_READ, read_only=True,
       destructive=False, idempotent=True, open_world=False, placeholder_default=True,
       input_schema_zod="{}"),
    _t("ui_visible_context", GOV_READ, NATIVE_READ, read_only=True, destructive=False,
       idempotent=True, open_world=False, placeholder_default=True,
       input_schema_zod="{ client: z.string().optional(), navigationId: z.string().optional() }"),
    _t("ui_clients_list", GOV_READ, NATIVE_READ, read_only=True, destructive=False,
       idempotent=True, open_world=False, placeholder_default=True, input_schema_zod="{}"),
    _t("session_search", GOV_READ, NATIVE_READ, read_only=True, destructive=False,
       idempotent=True, open_world=False, placeholder_default=True),
    _t("approvals_list", GOV_READ, NATIVE_READ, read_only=True, destructive=False,
       idempotent=True, open_world=False),
    # -- the five stage-4 writes ---------------------------------------------
    _t("session_send", GOV_SEND, NATIVE_SEND, read_only=False, destructive=False,
       idempotent=False, open_world=True),
    _t("session_create", GOV_CREATE, NATIVE_CREATE, read_only=False, destructive=False,
       idempotent=False, open_world=True),
    _t("session_interrupt", GOV_CONTROL, NATIVE_CONTROL, read_only=False,
       destructive=True, idempotent=False, open_world=True),
    _t("session_start", GOV_CONTROL, NATIVE_CONTROL, read_only=False, destructive=False,
       idempotent=True, open_world=True),
    _t("session_close", GOV_CONTROL, NATIVE_CONTROL, read_only=False, destructive=True,
       idempotent=False, open_world=True),
    # -- the six that register through their own modules ---------------------
    _t("ui_open_session", GOV_NAVIGATE, NATIVE_NAVIGATE, read_only=False,
       destructive=False, idempotent=True, open_world=False, placeholder_default=True),
    _t("session_cancel_queued", GOV_CONTROL, NATIVE_CONTROL, read_only=False,
       destructive=True, idempotent=True, open_world=False),
    _t("session_reconfigure", GOV_RECONFIGURE, NATIVE_CONTROL, read_only=False,
       destructive=False, idempotent=True, open_world=False),
    # Dual scope: listing needs read, the stop branch needs control. Recorded
    # with BOTH so a caller cannot stop a task under a read grant.
    _t("session_background", GOV_CONTROL, NATIVE_READ, NATIVE_CONTROL, read_only=False,
       destructive=True, idempotent=False, open_world=True),
    # A read and its answer are two different authorities: listing approvals is
    # read; answering one is approve, which Fleet plans and does not implement.
    _t("approval_answer", GOV_APPROVE, NATIVE_APPROVE, read_only=False,
       destructive=False, idempotent=False, open_world=True),
)

NATIVE_BY_NAME: dict[str, NativeTool] = {t.name: t for t in NATIVE_TOOLS}
BY_NAMESPACED: dict[str, NativeTool] = {t.namespaced: t for t in NATIVE_TOOLS}


# ---------------------------------------------------------------------------
# The native input schemas — READ OFF CloudCLI 1.39.0's tool registry
# ---------------------------------------------------------------------------
#
# These are not guessed from prose. Each is the JSON-Schema form of the zod
# object CloudCLI's own registered handler validates against, read from the
# installed build (`server/modules/mcp-gateway/*`): the `TOOL_BODIES` read
# schemas, the named `*_INPUT_SCHEMA` write schemas, and `sessionReadInputSchema`
# / `sessionSearchInputSchema`. `required` lists the parameters the native tool
# refuses to run without; a parameter absent from `required` is optional, and a
# nullable type is carried as a type ARRAY so `null` is not flattened into
# "omitted". A tool whose registered schema was the placeholder `{}` at 1.39.0
# records `{}` here — which is the truth about that build, not a widening.

def _s(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


NATIVE_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "overview": _s({"project": {"type": "string"}}, []),
    "projects_list": _s({"includeArchived": {"type": "boolean"}}, []),
    "sessions_list": _s({
        "project": {"type": "string"},
        "state": {"type": "string", "enum": ["running", "idle", "resident", "any"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
    }, []),
    "session_get": _s({"session": {"type": "string"}}, ["session"]),
    "session_read": _s({
        "session": {"type": "string"},
        "mode": {"type": "string", "enum": ["latest", "outline", "around"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "aroundId": {"type": "string"},
        "before": {"type": "number"},
        "after": {"type": "number"},
        "cursor": {"type": "string"},
    }, ["session"]),
    "run_get": _s({
        "runId": {"type": "string"},
        "waitSeconds": {"type": "number"},
        "session": {"type": "string"},
        "run": {"type": "string"},
        "wait": {"type": "boolean"},
    }, []),
    "quay_snapshot": _s({"project": {"type": "string"},
                         "refresh": {"type": "boolean"}}, []),
    "ui_last_opened_session": _s({}, []),
    "ui_visible_context": _s({"client": {"type": "string"},
                              "navigationId": {"type": "string"}}, []),
    "ui_clients_list": _s({}, []),
    "session_search": _s({
        "query": {"type": "string", "minLength": 1},
        "project": {"type": "string"},
        "provider": {"type": "string", "enum": ["claude", "codex"]},
        "speaker": {"type": "string", "enum": ["user", "assistant", "any"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "cursor": {"type": "string"},
        "mode": {"type": "string", "enum": ["fulltext"]},
    }, ["query"]),
    "session_send": _s({
        "session": {"type": "string"},
        "message": {"type": "string"},
        "waitSeconds": {"type": "number"},
    }, ["session", "message"]),
    "session_create": _s({
        "project": {"type": "string"},
        "message": {"type": "string"},
        "provider": {"type": "string"},
        "model": {"type": "string"},
        "lifecycleMode": {"type": "string"},
        "permissionMode": {"type": "string"},
    }, ["project"]),
    "session_interrupt": _s({"session": {"type": "string"}}, ["session"]),
    "session_start": _s({"session": {"type": "string"}}, ["session"]),
    "session_close": _s({"session": {"type": "string"},
                         "force": {"type": "boolean"}}, ["session"]),
    "ui_open_session": _s({
        "client": {"type": "string"},
        "session": {"type": "string"},
        # A discriminated union, not a bare object: the device accepts exactly
        # `{latest: true}` or `{messageId: <non-empty>}`. Recorded flat as
        # `{"type": "object"}` it advertised a shape the device refuses, and
        # dropped the `required` each branch carries.
        "at": {"anyOf": [
            {"type": "object", "properties": {"latest": {"type": "boolean", "const": True}},
             "required": ["latest"]},
            {"type": "object", "properties": {"messageId": {"type": "string"}},
             "required": ["messageId"]},
        ]},
    }, ["session"]),
    "session_cancel_queued": _s({"session": {"type": "string"},
                                 "messageUuid": {"type": "string"}},
                                ["session", "messageUuid"]),
    "session_reconfigure": _s({
        "session": {"type": "string"},
        "model": {"type": "string"},
        "effort": {"type": "string"},
        "permissionMode": {"type": "string"},
    }, ["session"]),
    "session_background": _s({"session": {"type": "string"},
                              "stopTaskId": {"type": "string"}}, ["session"]),
    "approvals_list": _s({"session": {"type": "string"}}, []),
    "approval_answer": _s({
        "requestId": {"type": "string"},
        "allow": {"type": "boolean"},
        "answers": {},
        "message": {"type": "string"},
    }, ["requestId", "allow"]),
}


def input_schema(native_name: str) -> dict[str, Any]:
    """The recorded native schema for one tool, or an empty object.

    An empty object means the registered schema declared nothing — never "any
    parameters". `validate_arguments` treats `{}` as no authority to check
    against, which is the honest reading.
    """
    return NATIVE_INPUT_SCHEMAS.get(native_name, {})

#: The count this build mirrors, as a number a test and a status can both read.
NATIVE_TOOL_COUNT = 22


def namespaced(native_name: str) -> str:
    return PREFIX + native_name


def native_of(namespaced_name: str) -> NativeTool | None:
    """The native tool a namespaced name denotes, or `None`.

    `None` rather than a guess: a name that is not in the recorded set is not a
    capability this build offers, and forwarding it because it looked like one
    would be the transparent proxy `providers/base.py` refuses.
    """
    return BY_NAMESPACED.get(namespaced_name)


def is_read_only(native_name: str) -> bool:
    tool = NATIVE_BY_NAME.get(native_name)
    return bool(tool and tool.read_only)


def manifest() -> dict[str, Any]:
    """The whole recorded surface, machine-readable. JSON-serialisable."""
    return {
        "schema": "serveros/fleet-cloudcli-native-manifest.v1",
        "native_server": "CloudCLI",
        "native_version_recorded": "1.39.0",
        "prefix": PREFIX,
        "count": len(NATIVE_TOOLS),
        "count_expected": NATIVE_TOOL_COUNT,
        "scope_map": dict(FLEET_SCOPE_FOR_NATIVE),
        "tools": [{**t.as_dict(), "input_schema": NATIVE_INPUT_SCHEMAS.get(t.name, {})}
                  for t in NATIVE_TOOLS],
    }


def coverage(observed_names: Iterable[str],
             observed_annotations: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Compare a LIVE tools/list against the recorded baseline.

    Returns a drift report, not a verdict: `missing` are recorded tools the
    device no longer declares (the mirror would forward a name that is gone),
    `extra` are device tools this build does not mirror (the device grew a verb
    nobody recorded), and `annotation_drift` are tools whose read/write hints
    moved (a verb that became destructive under a name this surface already
    governs). An empty report is the only state in which the recorded baseline
    and the device agree.
    """
    observed = list(observed_names)
    observed_set = set(observed)
    recorded_set = set(NATIVE_BY_NAME)
    missing = sorted(recorded_set - observed_set)
    extra = sorted(observed_set - recorded_set)
    drift: list[dict[str, Any]] = []
    if observed_annotations:
        for name in sorted(recorded_set & observed_set):
            tool = NATIVE_BY_NAME[name]
            got = observed_annotations.get(name) or {}
            hints = {
                "readOnlyHint": tool.read_only,
                "destructiveHint": tool.destructive,
                "idempotentHint": tool.idempotent,
                "openWorldHint": tool.open_world,
            }
            moved = {k: {"recorded": v, "observed": got.get(k)}
                     for k, v in hints.items() if k in got and got[k] != v}
            if moved:
                drift.append({"tool": name, "hints": moved})
    return {
        "schema": "serveros/fleet-cloudcli-coverage.v1",
        "recorded_count": len(NATIVE_BY_NAME),
        "observed_count": len(observed),
        "missing": missing,
        "extra": extra,
        "annotation_drift": drift,
        "agrees": not (missing or extra or drift),
    }


# ---------------------------------------------------------------------------
# Per-tool native schemas, generated from the live tools/list
# ---------------------------------------------------------------------------

#: A tool whose live schema differs between devices. The call for a device that
#: differs is refused by name rather than widened to a union no device accepts.
UNSUPPORTED_BY_DEVICE = "UNSUPPORTED_BY_DEVICE"

#: A verb the device DECLARES on `tools/list` whose recorded handler is the named
#: `MCP_TOOL_NOT_IMPLEMENTED` refusal — registered so the name set stays stable
#: while its deps are absent. `overview` is one: the installed 1.39.0 registers it
#: as `notImplemented('overview', 'AC-247')`. It is deliberately NOT `USABLE`: a
#: verb that answers a named refusal is not a capability this device has, and
#: reporting it as one would answer "what does this device expose?" with a verb
#: that provably cannot answer. That is the count-of-22 error reappearing in the
#: one place built to prevent it.
DECLARED_NOT_IMPLEMENTED = "DECLARED_NOT_IMPLEMENTED"

#: A verb the device declares with a wired handler. The only state that asserts
#: the device will answer a call.
USABLE = "USABLE"


def native_schema(tool_entry: dict) -> dict | None:
    """One live tool entry's own `inputSchema`, or `None`.

    Read from the DEVICE's `tools/list`, which is the version-reviewed
    authority. This module does NOT restate 22 schemas: a restated schema drifts
    the first time CloudCLI changes a parameter, and the drift is silent. A tool
    with no `inputSchema` (a placeholder registration — the write tools register
    `{}` until their deps are wired) returns `None`, which means "no parameters
    declared" and never "any parameters".
    """
    if not isinstance(tool_entry, dict):
        return None
    schema = tool_entry.get("inputSchema")
    return schema if isinstance(schema, dict) else None


def mirrored_schema(native: NativeTool, device_schema: dict | None = None) -> dict:
    """The mirrored tool's inputSchema for one device.

    The native tool's OWN parameters, carried UNCHANGED under `arguments`, with
    the Fleet outer fields ADDED: `device_ref` always, `session_ref` for every
    call whose floor is stated over one session (whether it writes or, like
    `approvals_list`, only reads), and `project` for the writes that mint one. `required`, `properties`
    and a nullable type are the native tool's; the outer layer only adds keys and
    never rewrites a native one. When `device_schema` is `None` the outer layer
    still declares `arguments` as an open object, because there is no native
    schema to narrow it with — and an open `arguments` is honest about that
    rather than inventing a closure.
    """
    native_props: dict[str, Any] = {}
    native_required: list[str] = []
    source = device_schema if isinstance(device_schema, dict) and device_schema \
        else NATIVE_INPUT_SCHEMAS.get(native.name, {})
    if isinstance(source, dict):
        native_props = dict(source.get("properties") or {})
        native_required = list(source.get("required") or [])
    outer: dict[str, Any] = {
        "device_ref": {"type": "string", "description": "Which device to call."},
    }
    if is_session_addressed(native):
        outer["session_ref"] = {
            "type": "string",
            "description": ("The session this verb is addressed to, as "
                            "`device:<id>|<provider>|<native session id>`. Its "
                            "device must equal `device_ref`, and its native id "
                            "must equal the `session` inside `arguments`."),
        }
    if native.governance in PROJECT_ADDRESSED_GOVERNANCE:
        outer["project"] = {
            "type": "string",
            "description": ("The project the new session is for; checked against "
                            "the project floor and named here rather than read "
                            "out of `arguments`."),
        }
    # The `arguments` object carries the SAME closure the runtime validator
    # uses: `additionalProperties` is copied from the recorded schema, not
    # advertised open. A published schema that said `true` while the runtime
    # refused unknown keys would be a contract that lies to the client.
    additional = True
    if isinstance(source, dict) and "additionalProperties" in source:
        additional = source["additionalProperties"]
    arguments: dict[str, Any] = {
        "type": "object",
        "description": f"{native.name}'s own arguments, passed through unchanged.",
        "additionalProperties": additional,
        "properties": native_props,
    }
    if native_required:
        arguments["required"] = native_required
    # The native `required` lives INSIDE `arguments`, so `arguments` itself is
    # required exactly when the native tool has a parameter it refuses to run
    # without — a caller that omits the payload is refused at the outer schema
    # rather than at the device.
    outer_required = ["device_ref"] + (["arguments"] if native_required else [])
    # LOW-13: the Fleet address a call's floor is stated over is REQUIRED at
    # runtime, so the schema says so — a schema-driven client learns it from the
    # schema rather than by trying. Read from the SAME predicate that declared
    # the property above, so `required` can never name a key `properties` does
    # not carry.
    if is_session_addressed(native):
        outer_required.append("session_ref")
    if native.governance in PROJECT_ADDRESSED_GOVERNANCE:
        outer_required.append("project")
    return {
        "type": "object",
        "properties": {**outer, "arguments": arguments},
        "required": outer_required,
        "additionalProperties": False,
    }


class NativeArgumentError(ValueError):
    """An argument this surface refuses before the device is ever asked.

    Raised by the single `governance_for` decision so both the host and the
    device translate it one way (BAD_REQUEST) rather than each deciding.
    """


def governance_for(native: "NativeTool", arguments: dict[str, Any] | None) -> str:
    """The class a CALL falls under, which for one tool depends on its arguments.

    `session_background` is two verbs under one name. CloudCLI's own handler
    (`mcp-session-background`) decides by PRESENCE, not truthiness: a
    `stopTaskId` that is `undefined`/`null` LISTS, and a `stopTaskId` that is
    present but not a non-empty string is `INVALID_ARGUMENT` — it never falls
    back to a listing. This surface matches that and is deliberately STRICTER on
    one point: a present `null` is also refused here rather than listed, because
    "present but valueless" is not a listing anyone asked for, and no read scope
    may ever reach the stop path.

    So: key absent -> READ; key present and a non-empty string -> CONTROL; key
    present and anything else (`""`, `None`, a number, `False`) -> refuse.
    `in args` rather than `args.get(...)`, because a falsy value is exactly the
    case that must never be read as a listing.
    """
    if native.name == "session_background":
        if not isinstance(arguments, dict) or "stopTaskId" not in arguments:
            return GOV_READ
        value = arguments["stopTaskId"]
        if not isinstance(value, str) or not value.strip():
            raise NativeArgumentError(
                "`session_background` was given a `stopTaskId` that is not a "
                "non-empty string. A stop request is refused rather than read as "
                "a listing, so a read scope can never stop a background task."
            )
        return GOV_CONTROL
    return native.governance


def capability_snapshot(*, device_ref: str, observed=None, source: str | None = None,
                        version: str | None = None, sampled_at: str | None = None,
                        reason: str | None = None) -> dict[str, Any]:
    """A read-only snapshot of what ONE device actually exposes.

    `observed` is `{tool_name: {"annotations": ..., "schema": ...}}` read from
    the device's OWN `tools/list`, or `None` when nobody could read it (a device
    that needs a credential this surface does not hold is reported UNKNOWN, not
    assumed complete).

    A recorded verb the device does NOT declare is `UNSUPPORTED_BY_DEVICE` — the
    22 names this build mirrors are not 22 working verbs on every machine, and a
    count of 22 is not evidence that any of them answer. A recorded verb the
    device DOES declare but whose recorded handler is the named
    `MCP_TOOL_NOT_IMPLEMENTED` refusal is `DECLARED_NOT_IMPLEMENTED`, never
    `USABLE`: being on `tools/list` is not answering. A verb the device declares
    that this build did NOT record is reported separately; it is not silently
    added, because a name that arrived from the wire is not a capability this
    build decided to offer.
    """
    base = {
        "schema": "serveros/fleet-cloudcli-capability.v1",
        "device_ref": device_ref,
        "recorded_count": len(NATIVE_TOOLS),
        "source": source,
        "version": version,
        "sampled_at": sampled_at,
    }
    if observed is None:
        return {**base, "state": "UNKNOWN", "observed_count": None,
                "reason": reason or "the device's tool list was not read",
                "tools": {}, "unsupported_by_device": sorted(NATIVE_BY_NAME),
                "declared_not_implemented": [],
                "unrecorded_by_build": []}
    observed = {k: v for k, v in observed.items() if isinstance(k, str)}
    tools: dict[str, Any] = {}
    unsupported: list[str] = []
    declared_not_implemented: list[str] = []
    for native in NATIVE_TOOLS:
        entry = observed.get(native.name)
        if entry is None:
            tools[native.name] = {
                "state": UNSUPPORTED_BY_DEVICE,
                "reason": "the device does not declare this verb",
            }
            unsupported.append(native.name)
            continue
        entry = entry if isinstance(entry, dict) else {}
        # A verb the device DECLARES is not thereby a verb that ANSWERS. The 22
        # names are all registered at stage 4; the ones whose deps are absent sit
        # on a named `MCP_TOOL_NOT_IMPLEMENTED` refusal. `USABLE` is the only
        # state that asserts the device will answer, so it is withheld from a
        # recorded placeholder — and the recorded fact rides ALONGSIDE the state
        # rather than being replaced by it, so a consumer can see which reading
        # produced the classification instead of trusting a bare word.
        tools[native.name] = {
            "state": (DECLARED_NOT_IMPLEMENTED if native.placeholder_default
                      else USABLE),
            "placeholder_default": native.placeholder_default,
            # Carried through, not interpreted: the device's own annotation.
            "annotations": entry.get("annotations"),
            "schema_present": isinstance(entry.get("schema"), dict),
        }
        if native.placeholder_default:
            declared_not_implemented.append(native.name)
    return {**base, "state": "OBSERVED", "observed_count": len(observed),
            "tools": tools, "unsupported_by_device": sorted(unsupported),
            "declared_not_implemented": sorted(declared_not_implemented),
            "unrecorded_by_build": sorted(set(observed) - set(NATIVE_BY_NAME))}


def approval_ids(listing) -> set[str]:
    """The request ids a device's `approvals_list` answer carries.

    ONE extractor, used by BOTH trust domains: the host verifies a request
    belongs to the session it will answer, and the device connector verifies it
    belongs to a request this machine enumerated. Two readers of the same shape
    would drift, and the one that drifted would be the one a caller saw.

    Tolerant of the envelope (`approvals` / `items` / `data` / `requests`) and
    strict about what counts as an id: a top-level list of objects, each naming
    its request under `requestId` / `request_id` / `id`. Anything else yields
    nothing — and nothing means «cannot locate», which callers refuse on, so an
    unrecognised shape fails closed rather than open.
    """
    out: set[str] = set()
    for key in ("approvals", "items", "data", "requests"):
        rows = listing.get(key) if isinstance(listing, dict) else None
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for id_key in ("requestId", "request_id", "id"):
                    value = row.get(id_key)
                    if isinstance(value, str) and value:
                        out.add(value)
    return out


def fleet_scopes_for(native: "NativeTool", governance: str) -> tuple[str, ...]:
    """The Fleet scopes a CALL needs, given the class it resolved to.

    A dual-scope tool names both in `fleet_scopes()` because it CAN be either;
    the requirement follows the class the call actually took. So a
    `session_background` LIST needs only `fleet:read`, and only its STOP needs
    `fleet:control` — which is the whole point of the split.
    """
    if native.name == "session_background" and governance == GOV_READ:
        return (FLEET_SCOPE_FOR_NATIVE[NATIVE_READ],)
    return native.fleet_scopes()


def surface_drift(per_device: dict[str, dict[str, Any]]) -> dict:
    """Compare per-device live tool sets and schemas.

    `per_device` is `{device_ref: {tool_name: inputSchema}}`. Per tool it reports
    whether every device that declares it AGREES, and which devices DIFFER. A
    tool whose schema differs by device is `UNSUPPORTED_BY_DEVICE` for the
    devices that differ, rather than being widened to a union nobody's device
    accepts — which is the failure the mirror exists to avoid, arriving as a
    convenience.
    """
    table: dict[str, dict[str, Any]] = {}
    for device, tools in (per_device or {}).items():
        for name, schema in (tools or {}).items():
            table.setdefault(name, {})[device] = schema
    report: dict[str, Any] = {}
    for name, by_device in table.items():
        variants: dict[str, list[str]] = {}
        for device, schema in by_device.items():
            variants.setdefault(json.dumps(schema, sort_keys=True), []).append(device)
        if len(variants) <= 1:
            report[name] = {"devices": sorted(by_device), "agrees": True,
                            "unsupported_by_device": []}
            continue
        # The devices that DISAGREE with the most common schema are the ones
        # whose call cannot be trusted to match what the surface declares. A tie
        # (no majority) leaves every device unsupported rather than picking one.
        ranked = sorted(variants.values(), key=len, reverse=True)
        tied = len(ranked[0]) == len(ranked[1])
        unsupported = (sorted(by_device) if tied
                       else sorted(d for group in ranked[1:] for d in group))
        report[name] = {"devices": sorted(by_device), "agrees": False,
                        "unsupported_by_device": unsupported}
    return {
        "schema": "serveros/fleet-cloudcli-surface-drift.v1",
        "tools": report,
        "drifted": sorted(n for n, r in report.items() if not r["agrees"]),
    }


def validate_arguments(arguments, schema) -> list[str]:
    """Check `arguments` against a live native schema. Returns problem strings.

    Only the two rules that protect the device: a REQUIRED parameter must be
    present, and a parameter the schema does not declare is refused rather than
    forwarded and ignored (an ignored filter widens the call). `null` and an
    omitted key are LEFT DISTINCT — `null` is present-and-null, an omitted key is
    absent, and neither is turned into the other. A `None` schema (a placeholder
    registration) checks nothing and returns `[]`, because there is no authority
    to check against.
    """
    if not isinstance(schema, dict):
        return []
    problems: list[str] = []
    if not isinstance(arguments, dict):
        return ["arguments must be an object"]
    declared = set(schema.get("properties") or {})
    for name in schema.get("required") or []:
        if name not in arguments:
            problems.append(f"missing required parameter {name!r}")
    if schema.get("additionalProperties") is False:
        for name in sorted(set(arguments) - declared):
            problems.append(f"unknown parameter {name!r}")
    return problems
