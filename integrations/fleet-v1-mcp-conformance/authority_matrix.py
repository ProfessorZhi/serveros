"""The 45-tool catalogue's authorization contract, in one table.

Why this module exists
----------------------
`fleet_mcp.tools.SERVED_TOOLS` says *what* the surface serves. It does not say,
per tool, *which* Fleet scope a caller must present, and the two questions are
different: a tool is visible to every client that can read `tools/list`, while
being callable only by a credential that carries the scope, and (for the write
verbs) by a principal the local binding also names. A release gate that counted
45 tools and stopped there would be reporting discovery as entitlement.

So this module is the join. Every served tool has exactly one row, the row
names the scope alternatives the call can take, and `selftest.py` fails if the
catalogue and the table stop agreeing. That is intentional: a new tool must
either be absent from this table (and the selftest red) or be added with an
explicit authorization answer.

What a row means
----------------
`scope_alternatives` is a disjunction, not a conjunction. Most tools have one
alternative. `fleet_cloudcli_session_background` has two because the same tool
has a read branch (list background tasks) and a control branch (stop one):
listing needs `fleet:read`, stopping needs `fleet:control`, and a read
credential must not reach the stop branch by omitting the stop argument.

An empty alternative (`frozenset()`) means the tool is callable with no scope
at all. Exactly one tool is in that state on purpose:
`fleet_cloudcli_native_manifest` returns the static, recorded catalogue. It
carries no device data, and `tools/list` already exposes the same set. Every
other tool — including `fleet_status`, which reports live routes and the audit
tail — requires at least `fleet:read`.

Scope is necessary, never sufficient. The `gates` column names the binding
checks that run on top of the scope: `policy.send_allow` / the `send` operator
grant for a send, `allow_create` + `create_allow` for a create, and
`allow_control` + (`control_allow` or the `control` operator grant) for a stop.
A scope-admitted call can still be refused, and the matrix says so rather than
reading the scope as the whole answer.
"""
from __future__ import annotations

from dataclasses import dataclass

READ = "fleet:read"
SEND = "fleet:send"
CREATE = "fleet:create"
CONTROL = "fleet:control"
APPROVE = "fleet:approve"
NAVIGATE = "fleet:navigate"

#: The ceiling the wire advertises. Equal to `scopes.IMPLEMENTED_SCOPES`.
SIX_SCOPES = (READ, SEND, CREATE, CONTROL, APPROVE, NAVIGATE)

#: What an operator who writes no `auth.grantable_scopes` gets.
DEFAULT_GRANTABLE = (READ, SEND)

#: What the owner's personal access token carries (`http_transport`).
#: Three of six, not six: the owner token predates control/approve/navigate and
#: was deliberately not widened when the mirror inherited those verbs.
OWNER_PAT = (READ, SEND, CREATE)


@dataclass(frozen=True)
class Authority:
    """One served tool's authorization contract."""

    name: str
    family: str
    scope_alternatives: tuple[frozenset[str], ...]
    gates: str
    note: str = ""

    def scope_admits(self, held: frozenset[str] | set[str] | tuple[str, ...]) -> bool:
        held = frozenset(held)
        return any(alternative <= held for alternative in self.scope_alternatives)

    @property
    def scopes(self) -> tuple[str, ...]:
        """Every scope any branch of this tool can require, sorted."""
        return tuple(sorted({scope for alt in self.scope_alternatives for scope in alt}))


def _tool(name: str, family: str, *scopes: str, gates: str, note: str = "") -> Authority:
    return Authority(name=name, family=family,
                     scope_alternatives=(frozenset(scopes),), gates=gates, note=note)


def _dual(name: str, family: str, alternatives: tuple[tuple[str, ...], ...], *,
          gates: str, note: str = "") -> Authority:
    return Authority(name=name, family=family,
                     scope_alternatives=tuple(frozenset(alt) for alt in alternatives),
                     gates=gates, note=note)


AUTHORITY: tuple[Authority, ...] = (
    # -- the twelve hand-written tools -----------------------------------
    _tool("fleet_list_devices", "authored", READ, gates="—",
          note="Enrolled devices and route reachability."),
    _tool("fleet_list_sessions", "authored", READ, gates="—"),
    _tool("fleet_get_session", "authored", READ, gates="—"),
    _tool("fleet_read_session", "authored", READ, gates="—"),
    _tool("fleet_send_to_session", "authored", SEND,
          gates="device allow_send AND (send_allow contains session OR operator 'send' grant)"),
    _tool("fleet_get_run", "authored", READ, gates="—"),
    _tool("fleet_status", "authored", READ, gates="—",
          note="Documented as fleet:read in fleet/owner-grant-3devices.md. "
               "The implementation did not enforce it before this table's "
               "branch; the selftest pins the documented contract."),
    _tool("fleet_create_session", "authored", CREATE,
          gates="device allow_create AND project in create_allow; the "
                "operator 'create' grant is additionally required when a "
                "runtime/project default rule is configured"),
    _tool("fleet_interrupt_session", "authored", CONTROL,
          gates="device allow_control AND (control_allow contains session OR "
                "operator 'control' grant)"),
    _tool("fleet_create_project", "authored", CREATE,
          gates="exact owner-max principal/namespace AND configured root AND target-local fresh creation"),
    _tool("fleet_delete_project", "authored", CONTROL,
          gates="device allow_control AND operator 'control' grant; the target "
                "is resolved from the device's own catalogue (archived rows "
                "included) and the caller must repeat its path",
          note="Removal is control, not creation: `allow_create` and the "
               "`create` grant do not authorize it. Archiving is the default; "
               "`force` deletes rows and transcripts and never the directory."),

    _tool("fleet_inbox", "authored", READ,
          gates="current enabled binding; per-subscriber contiguous delivered acknowledgement",
          note="Derived bounded status summaries, unavailable in standalone runtime."),

    # -- the 31 mirrored CloudCLI verbs ----------------------------------
    _tool("fleet_cloudcli_overview", "mirror", READ, gates="—"),
    _tool("fleet_cloudcli_projects_list", "mirror", READ, gates="—"),
    _tool("fleet_cloudcli_sessions_list", "mirror", READ, gates="—"),
    _tool("fleet_cloudcli_session_get", "mirror", READ, gates="—"),
    _tool("fleet_cloudcli_session_read", "mirror", READ, gates="—"),
    _tool("fleet_cloudcli_run_get", "mirror", READ, gates="—"),
    _tool("fleet_cloudcli_quay_snapshot", "mirror", READ, gates="—"),
    _tool("fleet_cloudcli_ui_last_opened_session", "mirror", READ, gates="—"),
    _tool("fleet_cloudcli_ui_visible_context", "mirror", READ, gates="—"),
    _tool("fleet_cloudcli_ui_clients_list", "mirror", READ, gates="—"),
    _tool("fleet_cloudcli_session_search", "mirror", READ, gates="—"),
    _tool("fleet_cloudcli_approvals_list", "mirror", READ, gates="—"),
    _tool("fleet_cloudcli_changes", "mirror", READ, gates="—"),
    _tool("fleet_cloudcli_mcp_usage_stats", "mirror", READ, gates="—"),
    _tool("fleet_cloudcli_ui_selected_text", "mirror", READ, gates="—"),
    _tool("fleet_cloudcli_draft_get", "mirror", READ, gates="—"),
    _tool("fleet_cloudcli_session_send", "mirror", SEND,
          gates="same per-session floor as fleet_send_to_session"),
    _tool("fleet_cloudcli_draft_set", "mirror", SEND,
          gates="same per-session floor as fleet_send_to_session",
          note="The device declares this destructive (it overwrites a draft); "
               "the floor is still the per-session send floor."),
    _tool("fleet_cloudcli_draft_submit", "mirror", SEND,
          gates="same per-session floor as fleet_send_to_session",
          note="Sending the draft is delivery, not authority over what the "
               "receiving session then touches."),
    _tool("fleet_cloudcli_session_create", "mirror", CREATE,
          gates="device allow_create AND project in create_allow AND "
                "operator 'create' grant (always, unlike the authored verb)"),
    _tool("fleet_cloudcli_session_interrupt", "mirror", CONTROL,
          gates="device allow_control AND operator 'control' grant "
                "(always; control_allow alone is not enough on this path)"),
    _tool("fleet_cloudcli_session_start", "mirror", CONTROL,
          gates="device allow_control AND operator 'control' grant"),
    _tool("fleet_cloudcli_session_close", "mirror", CONTROL,
          gates="device allow_control AND operator 'control' grant"),
    _tool("fleet_cloudcli_ui_open_session", "mirror", NAVIGATE,
          gates="per-session send floor AND operator 'navigate' grant"),
    _tool("fleet_cloudcli_ui_focus_turn", "mirror", NAVIGATE,
          gates="per-session send floor AND operator 'navigate' grant"),
    _tool("fleet_cloudcli_ui_show_diff", "mirror", NAVIGATE,
          gates="per-session send floor AND operator 'navigate' grant"),
    _tool("fleet_cloudcli_ui_open_task", "mirror", NAVIGATE,
          gates="per-session send floor AND operator 'navigate' grant",
          note="Opens a task on the machine in front of whoever is sitting at "
               "it, so it takes the same per-session floor as opening a "
               "session, not the device-wide floor."),
    _tool("fleet_cloudcli_session_cancel_queued", "mirror", CONTROL,
          gates="device allow_control AND operator 'control' grant"),
    _tool("fleet_cloudcli_session_reconfigure", "mirror", CONTROL,
          gates="device allow_control AND operator 'control' grant; the "
                "model/mode ceiling is a separate policy check"),
    _dual("fleet_cloudcli_session_background", "mirror",
          ((READ,), (CONTROL,)),
          gates="list branch: —; stop branch: device allow_control AND "
                "operator 'control' grant",
          note="One tool, two authorities. The governance class is resolved "
               "from the arguments before the scope gate; a read credential "
               "cannot reach the stop branch by omitting the stop value."),
    _tool("fleet_cloudcli_approval_answer", "mirror", APPROVE,
          gates="device allow_control AND operator 'control' grant; approval "
                "ownership is verified against the named session"),

    # -- the two capability reads ----------------------------------------
    _tool("fleet_cloudcli_native_manifest", "capability",
          gates="—",
          note="No scope by design: the static recorded catalogue, no device "
               "data. Discovery is not entitlement, and this tool is the "
               "only one that is callable with no scope at all."),
    _tool("fleet_cloudcli_native_capability", "capability", READ, gates="—",
          note="Live per-device answer, therefore a read."),
)

BY_NAME: dict[str, Authority] = {row.name: row for row in AUTHORITY}


def scope_admitted(held: frozenset[str] | set[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Tool names whose scope alternative is satisfied by `held`.

    This is scope admission, not call authorization: every write verb still
    passes the binding gates named in its row.
    """
    return tuple(row.name for row in AUTHORITY if row.scope_admits(held))


def scope_denied(held: frozenset[str] | set[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(row.name for row in AUTHORITY if not row.scope_admits(held))


def summary() -> dict[str, object]:
    """Counts a receipt can carry without re-deriving the table."""
    return {
        "tools": len(AUTHORITY),
        "six_scopes": list(SIX_SCOPES),
        "default_grantable": list(DEFAULT_GRANTABLE),
        "owner_pat": list(OWNER_PAT),
        "admitted_by_default": len(scope_admitted(DEFAULT_GRANTABLE)),
        "denied_by_default": len(scope_denied(DEFAULT_GRANTABLE)),
        "admitted_by_owner_pat": len(scope_admitted(OWNER_PAT)),
        "denied_by_owner_pat": len(scope_denied(OWNER_PAT)),
        "admitted_by_all_six": len(scope_admitted(SIX_SCOPES)),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), indent=2, sort_keys=True))
