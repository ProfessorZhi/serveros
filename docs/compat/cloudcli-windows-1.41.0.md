# CloudCLI 1.41.0 on Windows — the native contract as observed

A dated, read-only record of one CloudCLI device's native MCP surface, taken for
the L2 lane of Fleet V1 (issue #409: project / session / create / send / read /
get_run / stop, real permission modes, upgrade compatibility). The fixture files
under `tests/data/capture-cloudcli-1.41.0-*` are the machine-readable half of
this document; `tests/test_cloudcli_windows_compat.py` holds the assertions.

**Nothing in this file is a proposal, and nothing was inferred where it could be
measured.** Where a value comes from the installed source rather than from a
call, the file is named.

## 1. Device and build

| | |
| --- | --- |
| Class | company Windows workstation (not a lab machine) |
| Host | `FJ-NJ-CL-250028`, Windows 10 Pro `10.0.19045` |
| CloudCLI | `@yalehwang/cloudcli` **1.41.0**, install `C:/develop/tools/cloudcli-nvme` |
| Local patches | **5/5 PATCHED** (`python patches/apply-patches.py --check`), including `0004` "MCP session_create inherits remembered defaults" and `0005` "HTTP POST /sessions records creator defaults" |
| Gateway | `claudecodeui-mcp-gateway` `0.1.0` at `http://127.0.0.1:3001/mcp` |
| Protocol | MCP `2025-06-18`; responses arrive as SSE frames |
| serveros source | branch `claude/cloudcli-compat-windows-20261027`, `origin/main` @ **`e69fc0337ae5d33000478e3d5162077f525bc8cc`** |
| Resources | 16 GB RAM, ~1.7 GB free, commit ~24.7/38.6 GB during the run — every call below is small and serial by design |

The patch set matters: `0004`/`0005` change what `session_create` inherits, so a
capture that did not record them would describe a build nobody runs.

## 2. How the evidence was taken

Only the existing managed path was used: a PAT read from its anchor file and
sent as a bearer header. The token was never printed, echoed, or written to any
file in this record; the capture script refuses to write output that contains it
(`docs/compat/capture_cloudcli_tools_list.py`, exit 3).

- Verb surface: `initialize` → `notifications/initialized` → `tools/list`
  (`docs/compat/capture_cloudcli_tools_list.py`). Re-running it produced an
  identical parameter surface — the capture is reproducible, not a one-off.
- Round trips: `tools/call` on **scratch sessions this lane created**, in project
  `D:/tmp`. No existing user session was sent to, interrupted, reconfigured or
  closed. The two per-run scratch sessions cannot be closed by anyone (see §7),
  so they remain as empty benign rows.
- At most one resident host existed at a time, and it was closed at the end of
  the probe.

## 3. The verb surface: 31, not 22 or 33

| Build | Tools | Difference |
| --- | --- | --- |
| 1.39.0 | 22 | no project verbs |
| 1.40.0-fleet.1 | 33 | `project_create`, `project_delete` added |
| **1.41.0 (this device)** | **31** | **both project verbs removed** |

What 1.41.0 changed, verbatim from the capture:

1. **Every tool now publishes `outputSchema`** (none did in 1.40). All 31 also
   publish the full annotation set (`readOnlyHint`, `destructiveHint`,
   `idempotentHint`, `openWorldHint`).
2. **`_meta["cloudcli/handlerBound"]` is gone.** 1.40 advertised it per tool;
   1.41 publishes only `_meta["cloudcli/errorCodes"]`. Nothing in the answer now
   says whether a verb is wired to a handler — a reader that inferred "wired"
   from a missing field would be guessing.
3. **`project_create` / `project_delete` no longer exist natively**, so a Fleet
   "create project" cannot be a native mirror on this build. `projects_list`
   remains.
4. **No tool declares `additionalProperties` on its input schema**, and the
   gateway rejects unknown parameters at call time (see `run_get` in §5). A
   mirror must therefore not send fields the recorded schema does not name.

`MCP_TOOL_NOT_IMPLEMENTED` appears in 13 tools' `errorCodes` — and is **not** a
stub marker. `run_get` is one of the 13 and returned a completed run reading in
the same build. The code is a reserved vocabulary item, not evidence about a
handler; `capability_snapshot()` in the connector is right to treat the absence
of binding evidence as a third state rather than as "usable" or "placeholder".

## 4. V1 verbs: supported / rejected / unknown

| V1 capability | Native verb(s) | State on this device | Evidence |
| --- | --- | --- | --- |
| list projects | `projects_list` | supported | declared, 31-tool capture |
| list sessions | `sessions_list` | supported | declared |
| get session | `session_get` | supported | host / no-host receipts (§8) |
| read session | `session_read` | supported | `latest`, `outline`; `around` only with a real id (§8) |
| create session | `session_create` | supported | two shapes, §5 |
| send | `session_send` | supported | per-run and resident-queued, §5 |
| get run | `run_get` | **partially** — `runId` works, **`deliveryId` does not** | §5.3 |
| stop | `session_interrupt` (run), `session_close` (resident host) | supported with conditions | §7 |
| create project | — | **not available natively** | verbs removed in 1.41 |

## 5. Identifiers: which answer accepts which id

### 5.1 What each call returns

| Call | Returns |
| --- | --- |
| `session_create` **without** `message` | `{sessionId}` |
| `session_create` **with** `message` | `{sessionId, deliveryId: null, runId}` |
| `session_send` on a **per-run** session | `{deliveryId: null, runId, queued: false, queuedMessageUuid: null, source: "mcp", blockedBy: null}` |
| `session_send` **queued** on a **resident** session | `{deliveryId, runId, queued: true, queuedMessageUuid, source, blockedBy: null}` with **`deliveryId == queuedMessageUuid`** (one value under two names) |
| `session_send` when the per-run session is busy | refusal `SESSION_BUSY`, `retryable: true`, `details.runId` = the in-flight run; **never queued** |
| `run_get` | `{idKind, runId, deliveryId, sessionId, source, status, outcome, bootId, startedAt, completedAt, elapsedMs, lastAssistantMessage, note, …}` |

`sessionId` is the native identity: it is the Claude transcript filename and the
Codex thread id. For a newly created codex session the CloudCLI id is a UUIDv4
while the native thread id is a **UUIDv7** — the two are different namespaces for
the same conversation, and neither can be derived from the other.

### 5.2 The identity rule a connector must obey

> A run is addressed by **`runId`**. A delivery is a *queue receipt*, not an
> address: the only value ever minted for one is the same UUID it was handed
> back as `queuedMessageUuid`.

### 5.3 The defect: `deliveryId` is advertised but cannot be read back

`run_get`'s schema accepts `deliveryId`, and `session_send` returns one for a
queued resident send. On this build the two do not connect:

| Sample | Call | Answer |
| --- | --- | --- |
| queued send #1, delivery `f87036be-…` | `run_get({deliveryId})` immediately | `RUN_NOT_FOUND` — *"This deliveryId was never issued."* (`details.reason: never_issued`) |
| same delivery, +1.5 s | `run_get({deliveryId})` | identical refusal |
| same send | `run_get({runId: 6c86b701-…})` | **success**, `status: "running"`, and its own `deliveryId` reads **`null`** |
| queued send #2, delivery `f484d317-…` | `run_get({deliveryId})` | `RUN_NOT_FOUND` / `never_issued` |

So at the moment the delivery's run was alive and readable by `runId`, its
`deliveryId` was still "never issued" — the delivery arm of the lookup is dead
in this build, not merely slow. A reader cannot repair it from the run side
either: the run reading states `deliveryId: null` for a send that definitely had
one.

The installed source shows why this is a plumbing gap rather than a rejection
policy: `mcp-run-get.js` resolves a delivery through `deps.deliveries?.lookupById`
and distinguishes `never_issued` / `expired` / `restarted` in its refusals, so the
route exists — the gateway in front of it is not handing that lookup a populated
registry (`chat-run-registry.service.js` keeps runs, not deliveries; deliveries
live in the host driver).

**Consequence for Fleet:** the connector's `validate_delivery` / delivery-lookup
feature (AC-386 in Draft #407) has no working counterpart on this device. A Fleet
send receipt must key on `runId` (plus the gateway `bootId`), and must treat
`deliveryId` as an opaque echo for correlation only.

## 6. Retention and boot binding

- **`runId` survives ~5 minutes.** `chat-run-registry.service.js`:
  `DEFAULT_RUN_RETENTION_MS = 5 * 60 * 1000`. Observed: the same `runId` that had
  answered `completed` later answered `RUN_NOT_FOUND` with
  `details.reason: "expired"` (the refusal also carries
  `details.fallback.messages`, i.e. the verb degrades to a transcript hint rather
  than disappearing).
- **Deliveries are shorter-lived still**: `claude-host-driver.provider.js`
  sets `DEFAULT_DELIVERY_RETENTION_MS = 5 * 60 * 1000` and
  `DELIVERY_TERMINAL_LIMIT = 50`.
- **Every answer states `bootId`.** A run (or delivery) is scoped to one gateway
  process. Nothing read now can be persisted as a handle across a restart — a
  caller that stored a `runId` and asks a restarted gateway should expect
  `restarted`, not a hit.
- Consequence: `run_get` is a **live-follow** verb. A Fleet run record must cache
  the terminal reading it already received rather than re-reading the device
  later and treating "gone" as "unknown".

## 7. Stop: two verbs, two conditions

| Verb | Condition | Answer |
| --- | --- | --- |
| `session_interrupt` | a run is in flight | `{aborted: true}` |
| `session_interrupt` | nothing in flight | `{aborted: false, message: "This session currently has no run in flight, so there is no run to abort."}` |
| `session_close` | session is **resident** | host receipt: `{hostId: "host-<uuid>", sessionId, mode: "resident", closeReason: "user", leases: [{kind: "resident-policy"}, …]}` |
| `session_close` | session is **per-run** | refusal `LIFECYCLE_MODE_NOT_RESIDENT` |

`session_close` is therefore not "stop this run" and cannot be used to tidy up a
per-run session; it tears down a resident host (observed: the host `pid` 5492 was
gone afterwards). `interrupt` is the V1 stop for a turn; `close` is host
lifecycle management, applicable only where the lifecycle mode allows it.

## 8. Reading: prose by default, ids only in `outline`

- `mode: "latest"` returns `{session, mode, content}` where **`content` is a
  rendered transcript string** (`[ts] role: text` lines) — no message list, no
  ids, no cursor. Code that iterates `structuredContent.content` as an array is
  reading 1.39-era assumptions.
- `mode: "outline"` is the **only** mode that exposes per-turn ids:
  `[ts] #<turnIndex> <messageId>: <text>`. Claude message ids are UUIDs; Codex
  ids are prefixed `codex_<uuid>`.
- `mode: "around"` requires a **real** message id; an unknown one is
  `INTERNAL_ERROR` *"No message with id \"…\" was found"* with
  `retryable: false` — not `INVALID_ARGUMENT`, so a client cannot distinguish
  "I passed a bad id" from "the device faulted" except by this code.
- `session_get` separates a host from its leases: no host →
  `{host: null, run: null, hostNote: "No host: …"}`; resident →
  `{host: {mode, state, pid, peerName, leases: [{kind: "resident-policy"}]}, run, hostNote: null}`.

## 9. Permission modes and where they are enforced

The device publishes the `claude` list only inside a refusal:
`UNSUPPORTED_PERMISSION_MODE` with `details.supported = ["default", "auto",
"acceptEdits", "bypassPermissions", "plan"]`.

- Validation happens **before** any side effect: `session_create` with an invalid
  mode refuses with `UNSUPPORTED_PERMISSION_MODE` and creates nothing.
- `session_reconfigure` to a valid mode with no live host answers
  `{ok: true, stored: {...}, applied: "next-turn", liveSupported: true, message:
  "This provider was not reconfigured live …"}` — `ok: true` is **not** "in
  effect now".
- `lifecycleMode` and `permissionMode` are separate axes; `close` behaviour keys
  on the former only.

## 10. Windows-specific: project paths must be forward-slashed

The same project, named two ways in the same call shape:

| `project` | Answer |
| --- | --- |
| `D:\tmp` | `PROJECT_NOT_FOUND` — *"No project has a title containing \"D:\tmp\"."* |
| `D:/tmp` | reaches the mode check (`UNSUPPORTED_PERMISSION_MODE`), i.e. the project resolved |

The backslash form never reaches path comparison and falls through to
title-substring matching, so it reads as "no such project" rather than as a bad
path. Windows callers must normalise `\` → `/` — or use a project name or id —
before naming a project. This is a device contract detail, not a client bug, and
belongs in the Fleet-side path normalisation.

## 11. Codex: same envelopes, two real differences

- Create / send / interrupt / read answer the same shapes as `claude`; the
  outline ids are in the `codex_` namespace (§8).
- **A `completed` run can carry no output.** Observed: a codex run whose native
  rollout shows a provider usage-limit failure still answers `status:
  "completed"`, `outcome: "settled"`, `lastAssistantMessage: null`, plus
  `note: "This run has ended, but the session has no readable assistant
  message."` A Fleet receipt that maps `completed` to "succeeded with text" would
  report a failure as a success — the only honest signal available at this layer
  is the missing message plus the note.
- `run_get` on an **aborted** run settles (`status: "aborted"`) but
  `lastAssistantMessage` is the session's last assistant message, i.e. the
  **previous** turn's reply. It is not this run's output; do not attribute it.

## 12. Contract drift against the connector (for the #407 owner — not edited here)

Two findings; both live in files this lane does not own, so they are recorded,
not repaired.

1. **`run_get` schema: the recorded table is narrower than the device.**
   The device declares `{runId, deliveryId, waitSeconds, session, run, wait}` and
   enforces "exactly one of `runId`/`deliveryId`" at call time (no static
   `required`). `fleet_mcp/cloudcli_native.NATIVE_INPUT_SCHEMAS["run_get"]` still
   records the 1.40 shape (`runId` required, no `deliveryId`/`run`/`wait`).
   Because the mirror refuses unknown parameters, the recorded table would make a
   **device-accepted** `deliveryId` call impossible. Minimal fix suggestion: take
   the `run_get` row from this capture verbatim (five/six properties, no
   `required`) and re-run the 1.40→1.41 inheritance tests against it. Draft #407
   already contains this change; the test below is marked `expectedFailure` and
   will flip to an unexpected success once it lands.
2. **Delivery lookup has no working device counterpart** (§5.3). If
   `validate_delivery` is a V1 acceptance criterion, its acceptance evidence
   cannot come from this build: the device refuses every delivery id it itself
   minted. Suggested minimum: keep the delivery id as a correlation field on the
   send receipt and make `runId` + `bootId` the read handle; record the device
   refusal verbatim in the feature's notes so a later build can be checked
   against it.

The reproduction test for (1) is
`ConnectorCrossCheckTest::test_the_recorded_run_get_schema_is_not_narrower_than_the_device`;
it reads the module and never writes to it.

## 13. What was NOT run

Stated so that no absence above reads as success.

- **No other device was tested.** Tokyo and the Dell node run different CloudCLI
  builds; this lane does not SSH to hosts it does not own, so their versions are
  **unknown here**, not "compatible".
- **No Fleet end-to-end path.** These are native-gateway round trips; the
  connector → hub → client path belongs to the other lanes / #407 and was not
  exercised from this device.
- **No true in-place upgrade.** The 1.39 → 1.40 → 1.41 comparison is between
  captures and the recorded table, not a device upgraded while observed.
- **No exact retention timing.** The 5-minute figures are from source constants
  plus one observed `expired`; a timed boundary was not measured (a 5-minute
  wall-clock probe on a 16 GB workstation is not worth the memory pressure).
- **`project_create` / `project_delete` authored tools** were not exercised (the
  native verbs do not exist on this build; their Fleet-side replacements are
  #407's).
- **`deliveryId` persistence across restart** was not attempted; the boot-scoped
  design and `restarted` refusal path are from source, not from a restart.
- **The `run` alias of `run_get`** is declared in the device schema but was not
  called: the legacy-alias probe used `runId` + `session` + `wait`, which proves
  `session` and `wait` are accepted (the call reached the registry) and proves
  nothing about `run`.
- **Transport variants** (JSON responses vs SSE, streamable resumption,
  `mcp-session-id` reuse across long gaps) were not probed beyond what the calls
  above needed.

## 14. Reproducing

```bash
export CLOUDCLI_MCP_URL="http://127.0.0.1:3001/mcp"
export CLOUDCLI_PAT_FILE="$HOME/.cloudcli/mcp-pat-claude-code.txt"

# verb surface (read-only)
python docs/compat/capture_cloudcli_tools_list.py \
    --out /tmp/tools-list-1.41.0.json \
    --compare tests/data/capture-cloudcli-1.41.0-tools-list-2026-10-27.json

# assertions over both checked-in captures (no network, no credential)
python -m pytest tests/test_cloudcli_windows_compat.py -q
```

Round trips themselves are not scripted into the repo on purpose: they create
real sessions and would need a project whose scratch-ness the runner can vouch
for. The capture files are the record of the run that was done by hand under the
conditions in §2.
