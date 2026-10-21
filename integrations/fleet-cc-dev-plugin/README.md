# Fleet — the Claude Code development adapter

A **DEVELOPMENT ONLY** Claude Code plugin generated from the same sources as the
ChatGPT first-install package. It exists so the server side can be verified in a
real Claude Code session while the release stays blocked.

```sh
python3 integrations/fleet-cc-dev-plugin/build_cc_plugin.py \
    --out <empty-dir> \
    --next-mcp-url https://<next-ingress>.<tailnet>.ts.net/mcp \
    [--scopes "fleet:read"]
claude --plugin-dir <empty-dir> plugin details serveros-fleet-dev
```

## Two artefacts, one source, and they are not interchangeable

| | this adapter | `integrations/fleet-v1-first-install/` |
|---|---|---|
| output | a **directory**, loaded with `--plugin-dir` | a **ZIP**, installed once |
| brand | none — deliberately unbranded | the two approved PNGs, pinned by SHA-256 |
| binding | `.mcp.json` with `type: http` | `mcp.json` generated from an observed URL |
| plugin id | none | assigned by the client's Plugin Creator |
| blocked by | nothing | `MISSING_BRAND_ASSET`, and an unobserved endpoint |
| status | **DEVELOPMENT_ONLY** | **FINAL ZIP BLOCKED** |

The **Skills are not duplicated**: the generator reads
`integrations/fleet-v1-first-install/plugin/skills/` and writes those bytes into
the plugin. The scope vocabulary comes from the same builder's `FLEET_SCOPES`, so
there is one list rather than two that drift. `tests/test_fleet_cc_dev_plugin.py`
pins the byte-identity, because a test that only counted five directories would
pass on a generator that had re-typed them.

The absence of the approved PNGs blocks the **release build** and nothing else.
It must not be repaired here, and this adapter must never be described as the
release.

## What was verified, on this host

Claude Code **2.1.272**, `--plugin-dir`, from a clean working directory.

| field | value |
|---|---|
| manifest location | `.claude-plugin/plugin.json`, components at the plugin root |
| binding | `.mcp.json` — `{"type": "http", "url": …, "oauth": {"scopes": "…"}}` |

`claude plugin validate` → **Validation passed** (also under `--strict`, with no
warnings). `claude --plugin-dir … plugin details serveros-fleet-dev` → 5 Skills
enumerated by name, **1 MCP server** registered.

### What that evidence is, and what it is not

Both commands are **package-load evidence**. They say the plugin loads, and that
Claude Code registers a component named `serveros-fleet`. They are **not an MCP
enumeration**: no `tools/list` was performed and no tool name came back from
that server. A registered server name is not a tool list, and "MCP servers (1)"
is not "N tools discovered".

The binding pins `oauth.scopes` to `fleet:read`, which makes this adapter a
**read-only smoke profile** — not a statement of what the product requests. What
the surface **advertises** (six scopes), what a token **carries** (only the
granted subset — `fleet:read` and `fleet:send` by default), and what an owner
may **consent to** later are three different things, and this adapter
hard-codes none of them into a claim. Do not read any one of the three as the
others, and never read "the surface has six scopes" as "this client has six".

### Two real differences from the documentation, measured here

1. **`claude plugin validate` on 2.1.272 does not read `.mcp.json` at all.** A
   deliberately broken binding — a `url` with no `type`, and a non-absolute URL
   — still reports `Validation passed`. The manifest reference states the MCP
   checks require **v2.1.281 or later**, and this host is below that. The
   tests cover the binding's shape because the local validator will not.
2. **`oauth.scopes` is documented, and this version's validator cannot confirm
   it.** The field is in the `.mcp.json` schema (`oauth.scopes`, one
   space-separated string, taking precedence over discovery), but since (1)
   holds, "accepted by this version's validator" would be a claim about a check
   that did not run. Runtime acceptance on 2.1.272 is **UNVERIFIED**.

## Receipts

Each is a separate fact, and none is discharged by another.

| receipt | state | what it rests on |
|---|---|---|
| `LOCAL_CC_PLUGIN_LOADED` | **VERIFIED** | validate passed; `plugin details` listed 5 Skills and 1 MCP server |
| `NEXT_TOOLS_DISCOVERED` | **NOT ACHIEVED — `BLOCKED_BY_OWNER_OAUTH`** | unauthenticated `initialize` and `tools/list` both returned HTTP **401 `invalid_token`** with `www-authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource"`. No token was obtained and no consent was completed, so there is **no tool list** — this is a refusal, not a discovery |
| `SKILL_BEHAVIOR_VERIFIED` | **PARTIAL — one item withdrawn as `FAILED_REQUIREMENT`** | a restricted model call answered from the Skills, but its self-healing answer was wrong: it called self-healing V2, and **V1 has bounded self-healing of approved services**. The Skills were corrected on this branch; the corrected text has **not** been re-run against a model, so that item is pending re-test |
| `DEVICE_ACTIONS_TESTED` | **NOT_RUN** | no create/send/control attempted, and none was possible: no credential, `fleet:read` pinned, and `Task`/`Bash`/`Write`/`Edit` denied to the probe |
| `CORE_MONITORING_VERIFIED` | **NOT_RUN** | the monitoring plane was not read in this round |
| `SELF_HEAL_VERIFIED` | **NOT_RUN** | the recovery loop was not exercised against any device. It is **V1** — bounded, allowlisted, budgeted, owner-authorised — and a mock or an isolated toy service is where it is exercised offline. That is a separate thing from production recovery, which stays behind the owner's approval |
| `OFFLINE_STACK_FIXTURE` | **VERIFIED — 18 tests, offline** | `tests/test_fleet_cc_dev_plugin_offline_stack.py`: the real generated plugin, the real `fleet_mcp` HTTP surface on loopback, the real `abilities/resolver.py`, and the real `FleetService` over a fake device. It asserts the four claims are four and that none substitutes for another. A fixture is not a device, so it discharges `NEXT_TOOLS_DISCOVERED` and `DEVICE_ACTIONS_TESTED` **not at all** |

A mock or a fixture does not stand in for any row marked `NOT_RUN`. The
authenticated `tools/list` is the one that needs the owner: it is an interactive
OAuth consent, and this round did not simulate one.

### Model-call accounting, kept as it happened

**Two** restricted model calls were made, not one. The first ran against a
plugin generated *before* the Skills were extended, so it measured the old text
and was discarded; the second ran against the regenerated plugin. Both outputs
are in the evidence directory. One call was authorised, so the second is over
the stated limit — the record says so rather than being tidied away, and no
further call was made.

## The offline stack fixture, and the four things it keeps apart

`tests/test_fleet_cc_dev_plugin_offline_stack.py` is where "the plugin works" is
resolved into four separate claims and then held apart. Each is a different
instrument reading a different object, and the whole point of the file is that
no one of them can be quoted for another:

| claim | what is actually driven | what it cannot say |
|---|---|---|
| **package load** | the generated directory, structurally: a manifest, a typed binding, five Skills byte-identical to the one shared source | that any tool exists — the directory carries no tool *declaration* (`inputSchema` appears in no generated file), and the manifest and binding name no tool at all |
| **an authenticated `tools/list`** | the real `HttpSurface` on loopback: 401 `invalid_token` with the `resource_metadata` challenge when unauthenticated, and 33 distinct names when the owner token is presented | that the credential may *do* them — 13 of the 33 are writes, and the advertised six scopes are not the two granted by default |
| **an ability resolution** | the real `abilities/resolver.py`, which intersects eight inputs and knows nothing of `fleet_mcp` | anything about authority — no status is a grant, the record carries no `authorized`/`granted` field, and `UNKNOWN` is an answer rather than a pass |
| **a session lifecycle** | the real `FleetService` over a fake device: an accepted send carries a run id and **no** run state; the state arrives from a separate `get_run` | that the run finished — and a read-only credential is refused `SCOPE_DENIED` before the device is reached at all |

The Skill that named `fleet_send_session` — a tool the surface does not serve —
is now caught by the first class, which reads every `fleet_*` backtick in the
Skills and resolves it against `fleet_mcp.tools.SERVED_BY_NAME`. A Skill that
names a tool nobody serves is worse than a vague one: the assistant calls it,
gets a protocol error, and reads that as the device being unreachable.

The Skills were also corrected on three points this round, each against a source
in this repository rather than against recollection: **advertised is not held**
(six scopes are advertised, two are granted by default, and a token carries only
the granted subset); **`Role` is V2** (role-Tag hierarchical teams, not a scope
or a grant); and **`UNKNOWN` is never a permission** (a gate whose state could
not be read is not an open gate). None of these is a behaviour claim: the
corrected text has still not been run against a model, and that item stays
**pending re-test** above.

What the fixture deliberately does **not** do: it never invokes the `claude` CLI
(so no `--plugin-dir` load is claimed from a directory assertion), it makes no
model call, it opens no socket off loopback, and it installs nothing.

## Real Claude Code client over a local stdio fixture (P1, 2026-10-20)

The offline stack above drives the HTTP surface from Python. It cannot say what
a real Claude Code client does with a plugin, because it never starts one. The
P1 lane adds a second, deliberately separate artefact for that question:

| | `build_cc_plugin.py` | `build_local_fixture.py` |
|---|---|---|
| binding | `type: http` to an observed `/mcp` URL | `type: stdio` to `fixture_stdio_server.py` |
| device | the real fleet through the real ingress | `device:fixture-node`, invented |
| credential | OAuth request for `fleet:read` | no credential; a fixture principal holds `fleet:read` |
| purpose | verify the server side while the release is blocked | let a real client exercise the protocol and the scope gate |
| installable | no | **no** |

The fixture's stdio command is the point of the second artefact. It is **not**
a relaxation of the development adapter: `build_cc_plugin.py` still refuses a
binding with a command or an env block, and its tests still assert that. One
builder, one shape; two artefacts, two files.

The fixture server imports the **real** `fleet_mcp.server.McpServer`, the real
`fleet_mcp.tools.SERVED_TOOLS` (all 33, with their annotations) and the real
`fleet_mcp.fleet.FleetService`. Only the device and the provider are fake, and
every fake answer says `TEST_FIXTURE`.

### Commands, and what each one proves

```sh
# 1. package load: a directory and a registration, no tool enumeration
python3 integrations/fleet-cc-dev-plugin/build_local_fixture.py --out <dir>
claude plugin validate <dir> --strict
claude --plugin-dir <dir> plugin details serveros-fleet-dev-fixture

# 2. a real client handshake and tools/list, with no model call
CLAUDE_CONFIG_DIR=<fresh-dir> claude --plugin-dir <dir> mcp list
#    the fixture's .fixture-state/mcp-wire.log proves initialize,
#    notifications/initialized and tools/list, and the 33-tool answer

# 3. the whole receipt; --model-readbacks adds one real claude -p session
python3 integrations/fleet-cc-dev-plugin/run_cc_client_e2e.py \
    --out <evidence-dir> [--model-readbacks]
```

`run_cc_client_e2e.py` writes `RECEIPT.json` / `RECEIPT.md` and keeps five
claims apart:

| receipt | measured on 2026-10-20, Claude Code 2.1.272 | what it is not |
|---|---|---|
| `PLUGIN_LOAD_OK` | `validate --strict` exit 0; `plugin details` listed **5 Skills / 1 MCP server** | not a tool list |
| `MCP_INITIALIZE_OK` | the real client sent `initialize` and `notifications/initialized`; the server answered `serveros-fleet-mcp` | not authentication |
| `TOOLS_LIST_OK` | `tools/list` returned **33** names over the fixture's stdio | not the live Next surface, and not entitlement |
| `AUTH_SCOPE` | a `fleet:read` principal discovered all 33; `fleet_send_to_session` was refused `SCOPE_DENIED`; the fake provider saw **0** sends | not a real token's grants |
| `CLIENT_E2E` | one `claude -p` session called `fleet_list_devices`, `fleet_list_sessions` and `fleet_cloudcli_native_capability`; each result was non-error; **no permission denials** | not a real device; the device is `device:fixture-node` |

The model call is opt-in (`--model-readbacks`). The receipt records its cost
and count. The run uses a fresh `CLAUDE_CONFIG_DIR`, so no global plugin or MCP
configuration is read as a source of truth or written; no credential value is
read, copied or printed; and no socket leaves the process.

### Negative cases, all deterministic

`tests/test_fleet_cc_dev_plugin_cc_client.py` drives the same fixture server
without a model and asserts the refusals:

- a bare `fixture-session-0001` is not an address → `BAD_SESSION_REF`;
- an unknown tool → `BAD_REQUEST`;
- `device_ref` instead of `device` on `fleet_list_sessions` → `BAD_REQUEST`
  (an unknown filter is refused rather than ignored, because ignoring it
  answers a wider question);
- a write by a `fleet:read` principal → `SCOPE_DENIED`, with the provider's
  send count still **0** at shutdown.

### Finding reported, not fixed here

`FleetService._session_control_state` (`fleet_mcp/fleet.py:918`) says it is
"the same check `_may_send` makes", but `send_to_session`
(`fleet_mcp/fleet.py:1045`) calls `principal.require(SCOPE_SEND)` at
`fleet_mcp/fleet.py:1053` first. A read-only principal is therefore shown
`control_allowed.send_allowed: true` for an allowlisted session on a
send-enabled device, while the send tool refuses the same principal
`SCOPE_DENIED`. The fixture's default policy keeps `send_allow` empty so the
listing is not made to repeat that state; the discrepancy is pinned by
`KnownFindingReadOnlyControlState` as an `expectedFailure`, and the fix belongs
to the `fleet_mcp` owner rather than to this plugin lane.

### What this section does not claim

`LOCAL` in the receipt means a real Claude Code client on this host, over local
stdio, against an invented device. It is **not** `REAL_DEVICE`, **not** the live
Fleet Next ingress, and **not** `PRODUCTION`. It does not discharge the
authenticated Next `tools/list` (still `BLOCKED_BY_OWNER_OAUTH`) or any
device/Core/recovery action. A green fixture is a statement about the client
and the protocol, not about the fleet.

## Live Fleet Next read preflight (2026-10-20) — `BLOCKED_OWNER_OAUTH`

`preflight_live_read.py` is the gate in front of a **live** read. It makes no
model call, reads no token store, and sends no `Authorization` header. It
answers four questions: is the surface up; what does its OAuth discovery
advertise; does an isolated `CLAUDE_CONFIG_DIR` already hold a Fleet MCP entry;
and what does the surface say to an unauthenticated `initialize` and
`tools/list`.

```sh
python3 integrations/fleet-cc-dev-plugin/preflight_live_read.py \
    --surface http://127.0.0.1:3411 \
    --cc-home <isolated-CLAUDE_CONFIG_DIR> \
    --out <receipt.json>
```

Observed on 2026-10-20T06:06:58Z against the loopback Next entrance (installed
revision `fleet-mcp-20261016@008a336`):

| check | result |
|---|---|
| `/health` | **200**, `auth_required: true` |
| OAuth discovery | **200**; six scopes advertised and grantable, including `fleet:read`; `authorization_code` + `refresh_token`; a registration endpoint is published |
| unauthenticated `initialize` | **401 `invalid_token`**, no `result` |
| unauthenticated `tools/list` | **401 `invalid_token`**, no `result` and no `tools` |
| isolated CC config | present, **no Fleet MCP entry** |
| global CC config | **no Fleet MCP entry** |
| `credentials_read` / `model_calls` | **false / 0** |
| verdict | **`BLOCKED_OWNER_OAUTH`** |

A 401 is a refusal, not an empty tool list. "Six scopes are advertised and
grantable" is not "this client holds six"; no CC configuration here presents a
live Fleet grant, and the owner PAT anchor was not read. The only existing
`fleet:read` in this lane is the **synthetic fixture principal** from the
section above.

What would unblock a live read: the owner completes OAuth consent (or has
already registered and consented to a client), and an isolated
`CLAUDE_CONFIG_DIR` holds that client. The preflight can then confirm the
client and the read; it still does not read the token. Until then,
`CLIENT_E2E_TEST (live)` and `REAL_READ_ONLY` are **NOT_RUN / BLOCKED**, and
the fixture's `CLIENT_E2E` remains a **LOCAL** result, not a live one.

`tests/test_fleet_cc_dev_plugin_live_preflight.py` pins the refusal, the
`BLOCKED_SURFACE_UNREACHABLE` and unexpected-status paths, and the redaction
rule: a planted `headers.Authorization`, `oauth.clientSecret` and URL query are
never copied into the receipt, and a stdio `command` is not reported at all.

## Acceptance receipt (2026-10-20)

[`ACCEPTANCE-2026-10-20.md`](ACCEPTANCE-2026-10-20.md) records the layered
functional acceptance at code SHA **`b24b15a`**: L0 package structure and
byte-identical Skills, L1 real Claude Code CLI load/handshake with 33 tools,
L2 the local temporary daemon's virtual facts and the `SCOPE_DENIED` write
refusal (provider send count 0), and L3 one real `claude -p` session calling
three read-only tools (0 permission denials, **1** model call, **$0.218094**).
All layers pass at **LOCAL**; the live Next read remains
`BLOCKED_OWNER_OAUTH`, and no real device, `serverosd` or production state was
touched.
