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
| `list_skills(key)` / `invoke_skill(key, name, args)` | the Skills the agent in that session can use, and calling one | **to add** |
| `list_commands(key)` / `invoke_command(key, name, args)` | the slash commands, and calling one | **to add** |
| `set_model(key, ...)` / `set_permission_mode(key, ...)` | switch a session's model or permission mode | **to add** (today: CloudCLI native tools) |
| `approve(key, request_id, decision)` | answer an agent's permission request | **to add** (today: CloudCLI native tools) |

Acceptance (D-2): three devices × two providers (Claude Code, Codex) each do
«list Skills → invoke one → read the reply».

## Implementations

| implementation | where | reaches |
|---|---|---|
| CloudCLI over MCP/HTTP | `fleet_mcp/providers/cloudcli.py` | a device's CloudCLI gateway directly |
| node connector | `fleet_mcp/providers/fleet_node.py` → `fleet_mcp/node/connector.py` | a device behind the node channel, which then talks to its local CloudCLI |

## Where CloudCLI is still named above the interface

Two CloudCLI-only features sit beside the interface today. They are listed
here so the list can only shrink; `tests/test_agent_access_boundary.py` fails
when a module not on it starts importing a CloudCLI module.

| feature | modules | where it goes |
|---|---|---|
| the 22 mirrored native tools (`fleet_cloudcli_*`), incl. model and permission switching and approvals | `cloudcli_native.py`, used by `fleet.py`, `tools.py`, `providers/fleet_node.py`, `node/connector.py` | the generic ones become interface verbs (`set_model`, `set_permission_mode`, `approve`); the rest stay a CloudCLI-only extension that is absent when another implementation is in use |
| the model library | `cloudcli_models.py`, used by `fleet.py`, `node/connector.py`, `node/model_login.py`, `deploy/release_plan.py` | interface verbs `list_models` / `upsert_model` |

## Rules

- Fleet decides who may do what; the implementation only says how to reach it.
- An implementation never receives or returns a secret value (see
  `cloudcli_models.py`: keys are named, never carried).
- UNKNOWN is not NO: an implementation that cannot answer says so; it never
  reports an empty list as "there are none".
