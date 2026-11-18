# Agent access: the connection layer (D-2)

Fleet drives the agents on a device — Claude Code and Codex — through one
interface. **CloudCLI is its first implementation, not the interface.** Another
project, or a direct connection, can replace it by implementing the same verbs;
nothing above this line (Fleet's tools, the hub, the commander's skills)
changes.

## The interface

`fleet_mcp/providers/base.py` → `DeviceProvider` is the interface in code. Each
verb is named and bounded; there is deliberately no generic
`call(device, tool_name)`, because a pass-through would leave nothing for Fleet
to govern.

| verb | what it answers | status |
|---|---|---|
| `health()` | can this device's agents be reached, now | in code |
| `list_sessions(...)` | the sessions on the device, each classified (running / resident / idle / archived) | in code |
| `get_session(key)` | one session's facts | in code |
| `read_session(key, ...)` | its transcript, bounded | in code |
| `create(...)` | a new session in a managed profile | in code |
| `create_project(...)` | a project directory below an owner-configured root | in code |
| `send(key, message, ...)` | a message into a session; returns a run id | in code |
| `get_run(key, run_id, ...)` | a run's outcome; readable after the run and its process have ended (D-3) | in code; D-3 open |
| `interrupt(key)` | stop the run the session has, and nothing else | in code |
| `list_skills(key)` / `invoke_skill(key, name, args)` | the Skills the agent in that session can use, each with its exact invocation text; and calling one | in code (2026-11-18) |
| `list_commands(key)` / `invoke_command(key, name, args)` | the slash commands of the session's project and user, with `invocable`; and calling one | in code (2026-11-18) |
| `reconfigure(key, model=, effort=, permission_mode=)` | switch a session's model, effort or permission mode for its next run | in code (2026-11-18) |
| `approve(key, request_id, allow=, ...)` | answer one permission request the agent is waiting on | in code (2026-11-18) |
| `list_models(provider=)` / `upsert_model(...)` | the device's model library; no secret value either way (`fleet_mcp/model_entries.py` holds the rules) | in code (2026-11-18) |

Invoking is "resolve a name from the device's own catalog, then send"
(`providers/base.py` → `invocation_text`). Fleet's `fleet_agent_invoke` does
exactly that through `fleet_send_to_session`, so an invocation passes every
gate a send passes and nothing unlisted can be typed into a session. The Fleet
tools are `fleet_agent_catalog` (read) and `fleet_agent_invoke` (send); model,
permission and approval answers keep their existing tools and now reach the
device through `reconfigure` / `approve`.

Acceptance (D-2): three devices × two providers (Claude Code, Codex) each do
«list Skills → invoke one → read the reply».

## Implementations

| implementation | where | reaches |
|---|---|---|
| CloudCLI over MCP/HTTP | `fleet_mcp/providers/cloudcli.py` | a device's CloudCLI gateway directly |
| node connector | `fleet_mcp/providers/fleet_node.py` → `fleet_mcp/node/connector.py` | a device behind the node channel, which then talks to its local CloudCLI |

## Where CloudCLI is still named above the interface

Listed so the list can only shrink; `tests/test_agent_access_boundary.py`
fails when a module imports a CloudCLI module it is not listed for.

| module | still imports | why, and where it goes |
|---|---|---|
| `fleet.py`, `tools.py` | `cloudcli_native` | the mirrored native tools (`fleet_cloudcli_*`) and their governance. The generic ones (model / permission switch, approvals) already reach the device through `reconfigure` / `approve`; the rest stay a CloudCLI-only extension, absent when another implementation is in use |
| `providers/fleet_node.py` | `cloudcli_native` | the signed native route checks a tool name against the recorded set before it is sent |
| `node/connector.py` | `providers.cloudcli`, `cloudcli_native` | the connector's own implementation choice (it talks to this machine's CloudCLI), and the signed native route's floor on this side |
| `node/model_login.py` | `cloudcli_models` | the owner logs in to this machine's CloudCLI once, for its REST routes |
| `deploy/release_plan.py` | `cloudcli_native` | counts the mirrored tools in a release plan |

Since 2026-11-18 `fleet.py` reads the model library only through the interface
and `model_entries.py`, and the connector reads it, the Skills and the commands
only through its provider.

## Rules

- Fleet decides who may do what; the implementation only says how to reach it.
- An implementation never receives or returns a secret value (see
  `cloudcli_models.py`: keys are named, never carried).
- UNKNOWN is not NO: an implementation that cannot answer says so; it never
  reports an empty list as "there are none".
