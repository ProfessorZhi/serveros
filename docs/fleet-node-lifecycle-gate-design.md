# Session lifecycle on a `fleet-node` device: the gate design, and how to verify the upstream before writing any of it

**Status: design only. Nothing in this document is implemented.** No route, no
provider method, no Fleet tool, no scope, no contract entry. The companion patch
in this branch (`allow_all_sessions_for_owner`) implements the *floor* a
lifecycle verb would need, and implements no verb.

Read this as the answer to "what would it take, in what order, and what must be
read first" — not as a plan that has been half-built.

## 1. The gap, in the repository's own words

`fleet/fleet-mcp-contract.yaml` already states this surface's position on
lifecycle, under `does_not_own`:

> agent-session lifecycle semantics — `resume`, `stop` and `handoff` are not
> reachable here at all, **by omission rather than by refusal**

Three things follow, and they are the whole design problem:

1. The verbs this repository names are `resume`, `stop`, `handoff`. That is the
   only lifecycle vocabulary recorded anywhere in this tree, and it is recorded
   as *absent*.
2. They are absent **by omission** — an unlisted route, an undeclared tool, a
   missing protocol method. Omission is the correct default for a *safety*
   boundary and the wrong shape for an *intended* refusal: a caller cannot tell
   "this surface decided not to" from "nobody wrote it". Every other gate in
   this system answers with a named code (`WRITE_NOT_GRANTED`,
   `UNKNOWN_ROUTE`), and a lifecycle verb that lands must do the same.
3. The contract says those semantics are owned by something else. Any verb added
   here therefore needs an `overlap_with` entry naming the owner beside it, in
   the block that already records exactly this kind of double answer for
   `fleet_list_sessions`/`agent.session.list` and the two beside it. Adding a
   verb without that entry does not remove an ambiguity, it hides one.

## 2. What is verified today, and what is not

Measured from this tree at `4812308`, not assumed:

| Layer | Verified count and location |
|---|---|
| Upstream (CloudCLI) tools this surface calls | **5**, as string literals in `fleet_mcp/providers/cloudcli.py`: `sessions_list`, `session_get`, `session_read`, `session_send`, `run_get` |
| Node connector routes | **7**, `fleet_mcp/node_protocol.ROUTES` — closed tuple, unknown route refused by name before dispatch |
| Provider protocol | **6** methods, `fleet_mcp/providers/base.py:DeviceProvider` |
| Fleet tools | **7**, `fleet_mcp/tools.py:TOOLS`, declared in `fleet/fleet-mcp-contract.yaml` under `tools:`, revision `fleet-mcp-1` |
| Session states | **3**, `_SESSION_STATES = ("running", "idle", "resident")` |
| Gate layers for a write | **2** independent: Fleet `_may_send` (scope + device `allow_send` + session in `send_allow`), node `NodeConfig.permits_send` |

**Not verified, and not to be guessed:**

* The upstream tool **name** for a lifecycle verb on CloudCLI 1.39, its argument
  names, its return shape and its error codes. Nothing in this tree records
  them. The contract's own words are that they are unreachable, so there is no
  call site to copy a name from either.
* Whether CloudCLI 1.39 exposes lifecycle at all, and under what name.
* Whether it has any **approval** concept of its own distinct from the caller's
  permission mode, and what it is called.
* Whether `stop` is idempotent, whether it is interruptible, and what it reports
  for a session that has already finished.

Every one of these is a fact about the upstream that must be **read**, not
invented. A verb written against a guessed name is worse than no verb: it fails
at runtime, on a device, in a shape that looks like a device fault — which is
why section 4 exists and comes before any implementation step.

## 3. The layers a lifecycle verb must pass, in order

The send path already has this shape. A control verb repeats it rather than
inventing a second one; each layer refuses, none of them merely filters.

```
CloudCLI (owns session truth)
   ▲ 5. upstream call — one named tool, discovered not guessed (§4)
   │
node connector  ROUTES entry  ── refuses UNKNOWN_ROUTE by name if absent
   │            NodeConfig.owner floor / named list  ── refuses WRITE_NOT_GRANTED
   │            signature: device_ref, replay window, nonce  ── refuses 400/401
   ▲ 4. transport — unchanged; a new verb adds no new crypto and no new header
   │
FleetNodeProvider.method()  ── maps upstream refusals onto the closed code set
   ▲ 3. adapter — both providers implement the same protocol method
   │
Fleet tool (tools.py + fleet-mcp-contract.yaml)  ── declared, then scoped
   │   scope: a verb of its own, never a reuse of `fleet:send`
   ▲ 2. admission — per credential, per device, per session
   │
OAuth credential ── 1. the outbound caller's grant
```

Non-negotiable properties, each of which the send path already has and the
control path must copy rather than re-derive:

* **A new verb gets a new scope.** `fleet:control` (or the narrower
  `fleet:session.stop`) is a different question from `fleet:send`. Reusing
  `fleet:send` for `stop` would mean every caller that can reply to a session can
  also end it.
* **A new verb gets its own floor verb.** The owner floor introduced in this
  branch is a closed vocabulary (`OWNER_FLOOR_VERBS`). `"control"` is not in it
  and adding it *grants nothing by itself*: the route still has to exist, the
  provider method still has to exist and the scope still has to exist. That
  ordering is deliberate — it makes the grant expressible only after the thing
  it grants is real, so a binding can never be written ahead of the code.
* **Default deny at every layer, and a name at the layer that refuses.** A
  refusal a caller cannot act on is indistinguishable from a bug. This includes
  the two refusals that will be most common and are currently "by omission":
  verb exists but this device's binding has not granted it (`WRITE_NOT_GRANTED`),
  and verb exists but this credential lacks the scope (the existing scope
  refusal).
* **Identity stays `device_ref`.** The node composes the session key from its
  own `device_ref` (`Connector._key`), never from the request, so no new verb
  may introduce a request field that supplies a device. The existing test that
  posts a foreign device ref where a session id goes is the pattern to copy.
* **No new passthrough.** No `exec`, no shell, no path parameter, no generic
  "call any CloudCLI tool" route. A lifecycle verb is one more entry in a closed
  tuple, not a framework default.
* **`bypassPermissions` is not a floor and does not become one.** It is a
  CloudCLI-side mode of a session, and a Fleet grant that could set it would be
  a grant of everything CloudCLI can do on that machine, which is precisely what
  the connector exists to not be. If such a field is ever surfaced it needs its
  own layer, its own review and a written argument, not an entry in a tuple that
  already erases the difference between "may interrupt" and "may do anything".

### What "approval" means here

There is no CloudCLI approval API in this design, because none is verified to
exist (§2). In this architecture "approval" is the layered decision above,
recorded at the layer that made it: the OAuth scope the credential holds, the
Fleet admission verdict, the node's own floor. Two consequences:

* Any upstream approval/permission concept, if one exists, is an **upstream
  fact** to be discovered in §4, not a layer to be designed now. Designing a
  Fleet-side approval conversation against an upstream API that might not exist
  is how a design becomes a fiction.
* The audit requirement is met the way the send floor meets it: the gate that
  admitted a call is *named* in the response and in `GET /v1/self`, and a call
  admitted by a floor rather than a name is journalled locally, because that is
  the one admission the binding does not explain. Nothing in this design needs a
  new audit subsystem.

## 4. Verifying the upstream before writing anything

All five steps are read-only and none needs a production credential to be read,
printed or copied. Steps 1–3 need no live call at all.

1. **Read what is already recorded.** `fleet/fleet-mcp-contract.yaml`
   (`does_not_own`, `overlap_with`), `fleet_mcp/providers/cloudcli.py` for the
   five existing names, `fleet_mcp/providers/base.py:DeviceProvider` for the
   protocol shape, `fleet_mcp/node_protocol.py:ROUTES` for the route shape. If a
   verb is named here, use that name; if it is not, do not supply one.
2. **Read the upstream's own discovery output, not a guess.** The provider
   already asks the gateway and reports what it said:
   `CloudCliProvider.health()` calls `client.initialize()` then
   `client.tools_list()`, and `CloudCliProvider.tool_annotations()` returns
   `{name: annotations}` **as reported**, with the deliberate comment that this
   surface "does not have a second opinion about which CloudCLI verbs mutate".
   That mapping — names plus the gateway's own read/write annotations — is the
   schema. A verb absent from it is not addressable.
3. **Pin it.** Record the gateway's `serverInfo.name` and `.version` beside the
   tool list you read (`ProviderHealth` already carries both), and the tool list
   itself, into the Item's evidence. The design must say *which* upstream
   release it was written against, or the next reader cannot tell drift from a
   mistake.
4. **Do it on a non-production instance.** A second CloudCLI on a scratch port,
   or a recorded discovery payload, is enough to read names and annotations.
   Never confirm a lifecycle verb by invoking it on a session that matters.
5. **Write down what is still unknown** and stop, if step 2 leaves a question
   the upstream does not answer. "The gateway declares `session_interrupt` and
   annotates it `readOnly: false`" is a fact. "It probably takes the session id
   first" is not, and must not survive into code.

## 5. Implementation order, one reviewable step at a time

Each step is independently mergeable and independently revertible, and each one
is *inert* until the step after it lands. Nothing here is a partial state that
looks live.

| # | Step | Files | Refuses today because | Review question |
|---|---|---|---|---|
| 0 | §4, recorded as evidence | none | — | Is the verb name read or guessed? |
| 1 | Route added to the closed tuple | `node_protocol.py:ROUTES` | the connector has no dispatch branch, so it is a declared-but-unrouted route | Does the route table stay closed and enumerable? |
| 2 | `NodeConfig` verb + refusal | `node/connector.py` | the binding has not granted it → `WRITE_NOT_GRANTED`, named | Is the floor verb's absence a *named* refusal, not a 500? |
| 3 | Provider protocol method | `providers/base.py` (Protocol) + **both** implementations | not yet called | Do both providers answer, or does one raise? |
| 4 | Adapter error mapping | `providers/fleet_node.py:_map_code` | unmapped upstream codes pass through as `BAD_REQUEST: <code>` | Is a new code added deliberately, or does it fall through? |
| 5 | Fleet tool + contract | `tools.py`, `fleet/fleet-mcp-contract.yaml`, `overlap_with`, `does_not_own`, `tool_surface_revision` | the tool is not declared, so it is not on the surface | Is the lifecycle verb moved from *omitted* to *decided*, and is the other owner named? |
| 6 | Separate scope | the scope authority (`fleet_mcp/config.py:AuthBinding.grantable_scopes` today; `fleet_mcp/scopes.py` if PR #153 lands first) | credential lacks the scope → existing scope refusal | Is it a new scope, and is `fleet:send` untouched? |
| 7 | Tests at the node and adapter seams | `tests/` | — | Do they use a fake provider only, and never a live session? |

Ordering rules that are not negotiable:

* **Steps 1–2 before 3–5.** The node is the far end of the tunnel and the only
  layer whose answer survives the Fleet host being reconfigured, replaced or
  wrong. It refuses first, so it is written first.
* **The contract (step 5) is not documentation.** `tool_surface_revision` and
  the `tools:` block are what a consumer reads to decide whether a verb exists.
  A tool added to `tools.py` and not declared here is a surface that answers
  differently depending on who is asking.
* **No step may be skipped to make a demo work.** In particular, do not reach
  step 5 with the scope from step 6 unwritten; that is a tool every credential
  can call.

## 6. What this design deliberately does not do

* It does not add `resume`, `stop` or `handoff`. Those three names come from
  this repository's own contract, and that contract says they are owned
  elsewhere. Choosing where they belong is a decision for the owners of both
  surfaces, recorded in `overlap_with`, not a side effect of this patch.
* It does not implement one verb "as a sample". A single verb still crosses all
  seven steps, and step 0 — reading the upstream's actual name — cannot be
  skipped for it. A sample written against a guessed name would be the
  fabrication §4 exists to prevent, and would be harder to remove than to write.
* It does not build an approval conversation, for the reason in §3.
* It does not give the node a remote principal, an `exec`, or a
  `bypassPermissions` floor.

## 7. Interaction with the in-flight create branch

`feat/fleet-create-session-151` (PR #154) adds `POST /v1/sessions` to the same
`ROUTES` tuple and an `allow_create` field to the same `NodeConfig`. Both are
the same kind of change as step 1 and step 2 above, one layer down. Two
consequences:

* Whoever implements §5 should read #154 first and follow its shape for the
  create floor, rather than inventing a second convention for "a verb with no
  per-session form".
* `OWNER_FLOOR_VERBS` and `allow_create` are two spellings of one idea — "a grant
  that names no session". They should converge on one mechanism in a later
  patch, and this document is the place that records the intent so that the
  convergence is a decision rather than a discovery.
