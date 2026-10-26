# The 45-tool catalogue and what actually authorizes a call

`TOOL_SURFACE_REVISION = "fleet-mcp-8"` serves **45 tools**: 12 hand-written,
31 mirrored CloudCLI verbs, and 2 capability reads. `tools/list` shows all 45
to every client that can reach the surface. That is discovery. It is not
entitlement.

The machine-readable source of this table is
[`authority_matrix.py`](authority_matrix.py); `selftest.py` fails if the table
and `fleet_mcp.tools.SERVED_BY_NAME` stop agreeing, or if a tool is missing
from this page. Everything below is derived from the code paths named in the
last column.

## 1. Six scopes, three different sets

| set | contents | where it is read |
|---|---|---|
| `IMPLEMENTED_SCOPES` / `ALLOWED_SCOPES` | `fleet:read`, `fleet:send`, `fleet:create`, `fleet:control`, `fleet:approve`, `fleet:navigate` | `scopes.py`; advertised as `scopes_supported` |
| `DEFAULT_GRANTABLE_SCOPES` | `fleet:read`, `fleet:send` | `scopes.py`; used when the binding writes no `auth.grantable_scopes` |
| owner PAT | `fleet:read`, `fleet:send`, `fleet:create` | `http_transport.py` |

Issuance is `requested ∩ granted ∩ grantable`. **Advertising six is not holding
six.** An operator who writes nothing grants two; a credential may request the
other four only if the deployment's `grantable_scopes` says so and the human
consent screen ticks them. The owner's personal token holds three.

Consequences visible in the catalogue:

* `fleet_cloudcli_native_manifest` is callable with **no scope**: it is the
  static recorded catalogue and carries no device data.
* `fleet_status` is documented as `fleet:read` in
  `fleet/owner-grant-3devices.md`, but `FleetService.status()` used to call
  only `admit()` (the rate limiter). A zero-scope or send-only credential could
  read the enrolled-device inventory, route health, `policy.send_allow` and the
  audit tail. This branch adds the documented gate; `selftest.py` pins it.
* `fleet:control`, `fleet:approve` and `fleet:navigate` are not in the owner PAT
  or the default grant. A caller that needs them needs an OAuth token minted
  with them and a deployment that marks them grantable.

## 2. The 45 rows

Scope is **necessary, never sufficient**. The `gates` column names the binding
checks that still apply after the scope. A scope-admitted call can be refused.

| # | tool | family | scope(s) | additional gates |
|---|---|---|---|---|
| 1 | `fleet_list_devices` | authored | `fleet:read` | — |
| 2 | `fleet_list_sessions` | authored | `fleet:read` | — |
| 3 | `fleet_get_session` | authored | `fleet:read` | — |
| 4 | `fleet_read_session` | authored | `fleet:read` | — |
| 5 | `fleet_send_to_session` | authored | `fleet:send` | device `allow_send` AND (`send_allow` contains the session OR operator `send`) |
| 6 | `fleet_get_run` | authored | `fleet:read` | — |
| 7 | `fleet_status` | authored | `fleet:read` | — (gate added in this branch) |
| 8 | `fleet_create_session` | authored | `fleet:create` | device `allow_create` AND project in `create_allow`; operator `create` only when a runtime/project default rule exists |
| 9 | `fleet_interrupt_session` | authored | `fleet:control` | device `allow_control` AND (`control_allow` contains the session OR operator `control`) |
| 10 | `fleet_create_project` | authored | `fleet:create` | exact owner-max principal/namespace AND configured local root AND target-local fresh creation |
| 11 | `fleet_delete_project` | authored | `fleet:control` | device `allow_control` AND operator `control`; target resolved from the device catalogue (archived rows included) and `confirm_path` must equal its `path` |
| 12 | `fleet_inbox` | authored | `fleet:read` | current enabled configured binding; subscriber may acknowledge only its contiguous scanned range |
| 13 | `fleet_cloudcli_overview` | mirror | `fleet:read` | — |
| 14 | `fleet_cloudcli_projects_list` | mirror | `fleet:read` | — |
| 15 | `fleet_cloudcli_sessions_list` | mirror | `fleet:read` | — |
| 16 | `fleet_cloudcli_session_get` | mirror | `fleet:read` | — |
| 17 | `fleet_cloudcli_session_read` | mirror | `fleet:read` | — |
| 18 | `fleet_cloudcli_run_get` | mirror | `fleet:read` | — |
| 19 | `fleet_cloudcli_quay_snapshot` | mirror | `fleet:read` | — |
| 20 | `fleet_cloudcli_ui_last_opened_session` | mirror | `fleet:read` | — |
| 21 | `fleet_cloudcli_ui_visible_context` | mirror | `fleet:read` | — |
| 22 | `fleet_cloudcli_ui_clients_list` | mirror | `fleet:read` | — |
| 23 | `fleet_cloudcli_session_search` | mirror | `fleet:read` | — |
| 24 | `fleet_cloudcli_approvals_list` | mirror | `fleet:read` | — |
| 25 | `fleet_cloudcli_changes` | mirror | `fleet:read` | — |
| 26 | `fleet_cloudcli_mcp_usage_stats` | mirror | `fleet:read` | — |
| 27 | `fleet_cloudcli_ui_selected_text` | mirror | `fleet:read` | — |
| 28 | `fleet_cloudcli_draft_get` | mirror | `fleet:read` | — |
| 29 | `fleet_cloudcli_session_send` | mirror | `fleet:send` | same per-session floor as row 5 |
| 30 | `fleet_cloudcli_draft_set` | mirror | `fleet:send` | same per-session floor as row 5 (the device declares it destructive: it overwrites a draft) |
| 31 | `fleet_cloudcli_draft_submit` | mirror | `fleet:send` | same per-session floor as row 5 |
| 32 | `fleet_cloudcli_session_create` | mirror | `fleet:create` | device `allow_create` AND project in `create_allow` AND operator `create` (always) |
| 33 | `fleet_cloudcli_session_interrupt` | mirror | `fleet:control` | device `allow_control` AND operator `control` (always) |
| 34 | `fleet_cloudcli_session_start` | mirror | `fleet:control` | device `allow_control` AND operator `control` |
| 35 | `fleet_cloudcli_session_close` | mirror | `fleet:control` | device `allow_control` AND operator `control` |
| 36 | `fleet_cloudcli_ui_open_session` | mirror | `fleet:navigate` | per-session send floor AND operator `navigate` |
| 37 | `fleet_cloudcli_ui_focus_turn` | mirror | `fleet:navigate` | per-session send floor AND operator `navigate` |
| 38 | `fleet_cloudcli_ui_show_diff` | mirror | `fleet:navigate` | per-session send floor AND operator `navigate` |
| 39 | `fleet_cloudcli_ui_open_task` | mirror | `fleet:navigate` | per-session send floor AND operator `navigate` |
| 40 | `fleet_cloudcli_session_cancel_queued` | mirror | `fleet:control` | device `allow_control` AND operator `control` |
| 41 | `fleet_cloudcli_session_reconfigure` | mirror | `fleet:control` | device `allow_control` AND operator `control`; model/mode ceiling is a separate policy check |
| 42 | `fleet_cloudcli_session_background` | mirror | `fleet:read` (list) / `fleet:control` (stop) | list: —; stop: device `allow_control` AND operator `control` |
| 43 | `fleet_cloudcli_approval_answer` | mirror | `fleet:approve` | device `allow_control` AND operator `control`; approval ownership verified against the named session |
| 44 | `fleet_cloudcli_native_manifest` | capability | **none** | — (static catalogue; discovery is not entitlement) |
| 45 | `fleet_cloudcli_native_capability` | capability | `fleet:read` | — (live per-device answer) |

## 3. What each credential can reach

Counts are scope admission only; every write row still needs its binding
gates.

| credential | scopes | tools admitted | tools denied at the scope |
|---|---|---|---|
| operator who writes nothing | `read`, `send` | **30** | **15** |
| owner personal access token | `read`, `send`, `create` | **33** | **12** |
| a token granted all six | all six | **45** | **0** |

The 15 tools the default grant cannot reach are the three creates, the six
controls (`fleet_interrupt_session` and five mirror verbs), the four navigate
verbs (`ui_open_session`, `ui_focus_turn`, `ui_show_diff`, `ui_open_task`),
the one approve (`fleet_cloudcli_approval_answer`) and
`fleet_delete_project`. `fleet_cloudcli_session_background` is scope-admitted
by `fleet:read` in its **list** branch only; the stop branch is still denied.

The owner PAT's 12 denials are the same set minus the three creates.

## 4. Differences that are real, and are not defects of this table

These are authorization differences between two tools that look like one
capability. They are recorded so nobody has to rediscover them from a refusal.

1. **Authored vs mirrored control.** `fleet_interrupt_session` accepts a
   session named in `policy.control_allow` without an operator `control` grant
   (`_may_control`). The mirrored `fleet_cloudcli_session_interrupt` calls the
   same `_may_control` and then `_require_operator_verb("control")`, so it
   additionally requires the grant. Same class, two floors.
2. **Authored vs mirrored create.** `fleet_create_session` checks the operator
   `create` grant only when a runtime/project default rule is configured. The
   mirrored `fleet_cloudcli_session_create` checks it unconditionally in
   `_gate_native_write`.
3. **`session_reconfigure`.** The comment above `GOV_RECONFIGURE` says it
   "carries the create-side ceiling as well as the control floor", but its
   recorded native scope is `cloudcli:session:control` only, so the Fleet scope
   is `fleet:control`. The model/mode ceiling is enforced by
   `policy.runtime_create_defaults`, not by `fleet:create`.
4. **`session_create` vs `create_project`.** Both are creates, but they are
floored over different subjects. `fleet_cloudcli_session_create` floors over
an EXISTING project id and so checks `policy.create_allow`; `fleet_create_project`
floors over a project that has no id yet, so it requires an exact owner-max
profile for the named namespace, a root the profile configures, and a
target-local `freshDirectoryCreated` ACK. `create_allow` cannot stand in for
the second: it is keyed by ids that by definition do not exist yet.
5. **Stale prose.** `oauth.py`'s module docstring still says the scopes are
   read/send/create, and `config.py`'s comment above `OPERATOR_GRANT_VERBS`
   still says approve/navigate are planned. Both lists have since grown to six
   and five respectively. The code is authoritative; the prose is not.

None of these is changed here: each is a product decision about a documented
property, and this branch is a conformance slice, not a redesign. What the
branch does change is the one place where the code contradicted an explicit
documented contract (`fleet_status`) and the two protocol behaviours below.

## 5. Protocol behaviours this branch pins

* **Unknown method** returns JSON-RPC `-32601` (`METHOD_NOT_FOUND`). The
  constant existed but was never used; every unknown method came back as
  `-32602`, which tells a client to fix its parameters instead of its method.
* **A `tools/call` sent as a JSON-RPC notification is not executed.** It used
  to run before the dispatcher checked for an id, delivering a write whose
  result the caller could never observe.
* **`control_allowed.send_allowed` includes the caller's `fleet:send` scope.**
  A read-only caller on a send-enabled device with an allowlisted session used
  to be told `true` and then refused `SCOPE_DENIED`. The listing now uses the
  same upper bound as `send_to_session` (scope × runtime binding × device
  `allow_send` × session allowlist or operator `send`), and `selftest.py`
  asserts the reported value and the gate agree across the matrix, including
  grant revocation, another principal, another runtime and an unbound runtime.

## 6. What this table does not claim

* It does not claim any tool reached a real device. `selftest.py` and `run.py`
  run against an in-process `FakeDevice`; the mode is `FAKE_DEVICE`.
* It does not claim a particular canary serves this revision. The 31 mirrored
  verbs are CloudCLI 1.40.0's surface; whether a given device answers one is
  read per device from `fleet_cloudcli_native_capability` (all 22 answered on
  `device:tokyo-shared-dev-01` when this table was written), not inferred here.
* It does not claim `effective` anything. `fleet_create_session` reports
  `effective: null` because no device read it back; that is UNKNOWN, not a
  resolved value.
