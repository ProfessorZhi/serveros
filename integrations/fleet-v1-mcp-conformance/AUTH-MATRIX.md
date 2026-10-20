# The 33-tool catalogue and what actually authorizes a call

`TOOL_SURFACE_REVISION = "fleet-mcp-4"` serves **33 tools**: 9 hand-written,
22 mirrored CloudCLI verbs, and 2 capability reads. `tools/list` shows all 33
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

## 2. The 33 rows

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
| 10 | `fleet_cloudcli_overview` | mirror | `fleet:read` | — |
| 11 | `fleet_cloudcli_projects_list` | mirror | `fleet:read` | — |
| 12 | `fleet_cloudcli_sessions_list` | mirror | `fleet:read` | — |
| 13 | `fleet_cloudcli_session_get` | mirror | `fleet:read` | — |
| 14 | `fleet_cloudcli_session_read` | mirror | `fleet:read` | — |
| 15 | `fleet_cloudcli_run_get` | mirror | `fleet:read` | — |
| 16 | `fleet_cloudcli_quay_snapshot` | mirror | `fleet:read` | — |
| 17 | `fleet_cloudcli_ui_last_opened_session` | mirror | `fleet:read` | — |
| 18 | `fleet_cloudcli_ui_visible_context` | mirror | `fleet:read` | — |
| 19 | `fleet_cloudcli_ui_clients_list` | mirror | `fleet:read` | — |
| 20 | `fleet_cloudcli_session_search` | mirror | `fleet:read` | — |
| 21 | `fleet_cloudcli_approvals_list` | mirror | `fleet:read` | — |
| 22 | `fleet_cloudcli_session_send` | mirror | `fleet:send` | same per-session floor as row 5 |
| 23 | `fleet_cloudcli_session_create` | mirror | `fleet:create` | device `allow_create` AND project in `create_allow` AND operator `create` (always) |
| 24 | `fleet_cloudcli_session_interrupt` | mirror | `fleet:control` | device `allow_control` AND operator `control` (always) |
| 25 | `fleet_cloudcli_session_start` | mirror | `fleet:control` | device `allow_control` AND operator `control` |
| 26 | `fleet_cloudcli_session_close` | mirror | `fleet:control` | device `allow_control` AND operator `control` |
| 27 | `fleet_cloudcli_ui_open_session` | mirror | `fleet:navigate` | per-session send floor AND operator `navigate` |
| 28 | `fleet_cloudcli_session_cancel_queued` | mirror | `fleet:control` | device `allow_control` AND operator `control` |
| 29 | `fleet_cloudcli_session_reconfigure` | mirror | `fleet:control` | device `allow_control` AND operator `control`; model/mode ceiling is a separate policy check |
| 30 | `fleet_cloudcli_session_background` | mirror | `fleet:read` (list) / `fleet:control` (stop) | list: —; stop: device `allow_control` AND operator `control` |
| 31 | `fleet_cloudcli_approval_answer` | mirror | `fleet:approve` | device `allow_control` AND operator `control`; approval ownership verified against the named session |
| 32 | `fleet_cloudcli_native_manifest` | capability | **none** | — (static catalogue; discovery is not entitlement) |
| 33 | `fleet_cloudcli_native_capability` | capability | `fleet:read` | — (live per-device answer) |

## 3. What each credential can reach

Counts are scope admission only; rows 5, 8, 9 and 22–31 still need their
binding gates.

| credential | scopes | tools admitted | tools denied at the scope |
|---|---|---|---|
| operator who writes nothing | `read`, `send` | **23** | **10** |
| owner personal access token | `read`, `send`, `create` | **25** | **8** |
| a token granted all six | all six | **33** | **0** |

The 10 tools the default grant cannot reach are the two creates, the six
controls (`fleet_interrupt_session` and five mirror verbs), the one navigate
(`fleet_cloudcli_ui_open_session`) and the one approve
(`fleet_cloudcli_approval_answer`). `fleet_cloudcli_session_background` is
scope-admitted by `fleet:read` in its **list** branch only; the stop branch is
still denied.

The owner PAT's 8 denials are the same set minus the two creates.

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
4. **Stale prose.** `oauth.py`'s module docstring still says the scopes are
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

## 6. What this table does not claim

* It does not claim any tool reached a real device. `selftest.py` and `run.py`
  run against an in-process `FakeDevice`; the mode is `FAKE_DEVICE`.
* It does not claim the installed Next canary serves this revision. The
  installed canary is A `008a336`, which predates `fleet-mcp-4`.
* It does not claim `effective` anything. `fleet_create_session` reports
  `effective: null` because no device read it back; that is UNKNOWN, not a
  resolved value.
