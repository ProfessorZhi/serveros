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
`MANIFEST` is the recorded baseline — what CloudCLI 1.39.0 declares. The
provider already fetches the LIVE `tools/list` on every `health()`. `coverage()`
compares the two and reports drift (a tool that appeared, one that vanished, an
annotation that moved), so an upgrade is a diff a person reads rather than a
silent difference in what the mirror forwards. The per-tool `inputSchema` is NOT
re-recorded here: it travels through from the live list unchanged, which is what
"参数语义无损映射 (null vs omitted preserved)" requires — a schema restated here
would be a second copy that drifts.

No I/O, no clock, no credentials. This module is a table and the functions over
it.
"""
from __future__ import annotations

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
       idempotent=True, open_world=False),
    _t("quay_snapshot", GOV_READ, NATIVE_READ, read_only=True, destructive=False,
       idempotent=True, open_world=False, placeholder_default=True,
       input_schema_zod="{ project: z.string().optional(), refresh: z.boolean().optional() }"),
    _t("ui_last_opened_session", GOV_READ, NATIVE_READ, read_only=True,
       destructive=False, idempotent=True, open_world=False, placeholder_default=True,
       input_schema_zod="{}"),
    _t("ui_visible_context", GOV_READ, NATIVE_READ, read_only=True, destructive=False,
       idempotent=True, open_world=False,
       input_schema_zod="{ client: z.string().optional(), navigationId: z.string().optional() }"),
    _t("ui_clients_list", GOV_READ, NATIVE_READ, read_only=True, destructive=False,
       idempotent=True, open_world=False, input_schema_zod="{}"),
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
       destructive=False, idempotent=True, open_world=False),
    _t("session_cancel_queued", GOV_CONTROL, NATIVE_CONTROL, read_only=False,
       destructive=True, idempotent=True, open_world=False),
    _t("session_reconfigure", GOV_CONTROL, NATIVE_CONTROL, read_only=False,
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
        "tools": [t.as_dict() for t in NATIVE_TOOLS],
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
