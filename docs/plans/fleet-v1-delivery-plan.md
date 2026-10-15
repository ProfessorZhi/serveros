# Fleet V1 delivery plan

Status: **PLAN.** This document is not a deployment receipt. Where it states a
fact, that fact was read from this repository or measured on the host in
2026-10-15, and §7 says which. Where it states a requirement, no evidence is
implied. Nothing here authorises a production change.

Division of labour: **ChatGPT does the research, reasoning, architecture and
review** — the parts where being wrong is expensive and being slow is not.
**CC writes the documents, code, tests and deployment artifacts.** ChatGPT is
neither a remote control nor a programmer-for-hire; a plan that reduces it to
either has misread the arrangement.

---

## 1 · The V1 / V2 boundary

### V1 — a stable, narrow control surface

**V1 is one thing done completely:** ChatGPT, through **a real MCP surface and
thin Skills carried by an independent, fixed Plugin ID**, stably controls the
already-authorized CloudCLI CC/Codex agent cluster on Tokyo, the FJPCOS Windows
workstation and the Dell GPU node.

V1 includes:

| | |
|---|---|
| tool surface | **33 tools** = 9 hand-written + 22 CloudCLI mirrors + 2 mirror descriptors |
| scopes | **6** — `fleet:read`, `fleet:send`, `fleet:control`, `fleet:create`, `fleet:approve`, `fleet:navigate` |
| accounts | multiple OS users; several CC configurations inside one UID |
| defaults | the DeepSeek `admin`-route candidate as the default; **CC `bypassPermissions` only through an owner-authorized project** |
| exceptions | official Claude subscription and MarketOps Claude Code as separate, independent accounts |
| identity | single Feishu SSO |
| isolation | an independent Next port and a **brand-new, independent Tailscale node** |
| lifecycle | basic resource bounds, error/disconnect handling, continue, rollback, same-Plugin-ID update |

**33 tools is not 33 permissions.** The tool list is what the surface *offers*;
authority is what the credential *carries*. The implemented scope set is 6 and
the default grantable set is **2** (`fleet:read`, `fleet:send`) — a credential
holding the default can see 33 tools and call the read-and-send subset. Reading
"33 tools" as "33 permissions" is the single most likely misreading of this
product, so it is stated here rather than assumed.

### V2 — and why it is not V1

V2 is the long-horizon layer: OpenAgents, role-Tag hierarchical teams,
autonomous strong/weak model scheduling, wider local Ability governance,
advanced resource monitoring, and end-to-end autonomy from a Feishu work item to
a delivered report.

V2 is **deferred**. Nothing in V1 depends on it, and no V1 gate may be closed by
a V2 design. A stage, a service or a gate that exists only to prepare V2 must
not block V1's release; §6 is where such a block would be reported.

### The other two lines

ServerOS **B (Native C++ Core + V13 monitoring)** and **C (Ability family
end-to-end)** continue under their own projects. They do not block Fleet V1, and
Fleet V1 does not absorb them.

---

## 2 · Six parallel branch boundaries

Six lanes run at once. They are separated by **which files they own**, so that a
lane can be reviewed without reading the others.

| lane | owns | must not touch |
|---|---|---|
| **A — integration (this plan)** | `docs/plans/*`, the root entry link, conflict resolution, the release candidate | any lane's owned files |
| **B — runtime policy** | `fleet_mcp/fleet.py`, `fleet_mcp/config.py`, `fleet_mcp/runtime_create_policy.py` | packaging, deploy scripts |
| **C — packaging** | `integrations/**` (the overlay builder, skills, assets, `plugin.json`) | runtime policy, deploy |
| **D — Dell** | Dell device enrolment, the second runtime's binding and isolation facts | Tokyo ingress, Windows |
| **E — ingress** | `fleet_mcp/deploy/next_*`, `serveros-fleet-example-mcp.service` | V1's unit, V1's binding |
| **F — release QA** | live-evidence gates, the acceptance matrix's evidence collection | product code |

**The integration owner (A) does not take over an in-flight lane.** A's job is to
hold the boundary, read the diffs when they land, and choose a safe merge order —
not to finish someone else's branch.

---

## 3 · Release stages and their dependencies

| stage | depends on | exits when |
|---|---|---|
| **S0 · plans and contracts** | nothing | this document exists and the entry link points at it |
| **S1 · lanes land** | S0 | B–F each have a reviewed tip on their own branch |
| **S2 · offline integration** | S1 | the lanes merge into one candidate **in an integration worktree**, and the offline suites pass |
| **S3a · route provisioned** | S2 + owner approval | the Next-only HTTPS **endpoint exists** — DNS, certificate and tunnel configuration are in place and verifiable — while **the backend behind it is deliberately not yet live** |
| **S3b · backend live** | S3a | the Next service is running and bound to that endpoint's local target |
| **S4 · canary verified through the real route** | S3b + a green resource gate | over the **real HTTPS route**: OAuth discovery answers, `/health` answers, and the tool list reads back — with V1 health checked before and after, and V1 untouched |
| **S5 · client install** | S4 + owner action | the plugin is installed once, under its own Plugin ID |

**Why S3 splits, and what it avoids.** A single "S3: route reachable" step
ordered before the service exists is a circular gate: an endpoint whose backend
is not running cannot answer `/health`, so the route could never be declared
reachable and S4 would never be permitted to start the thing that would make it
reachable. Provisioning and liveness are therefore separate facts with separate
owners of proof — the endpoint is a configuration fact, and the answers it gives
are a runtime fact, measured in S4 **after** the service starts. A route that
resolves but has no live backend is `ENDPOINT_PROVISIONED / BACKEND_NOT_LIVE`,
which is a stage, not a failure.

S2 is a merge, not a deployment. S3a, S5 and everything beyond are **owner
actions on external systems**, and a plan may not stand in for any of them.

---

## 4 · Managed-runtime acceptance matrix

Every runtime is one credential instance. Two CC configurations in one OS
account are **two runtimes**, which is why the key is a stable runtime id and not
a username.

**Two questions are asked of every device, and the answers are independent.**
"Can the legacy Fleet reach it today?" and "is the new Next node bound on it?"
are different facts, and an earlier revision of this table ran them together —
it called the Tokyo deployment "live, 7 tools served" and the other two "not
deployed", which reads as a statement about the devices when it was a statement
about one old release.

| runtime | device | family | **legacy V1 field (live)** | **new Next node/binding** |
|---|---|---|---|---|
| `device:tokyo-shared-dev-01\|claude` | Tokyo (company) | claude | reachable + authorized | **not deployed** |
| `device:work-fj\|claude` | FJPCOS (company, Windows) | claude | reachable + authorized | **not deployed** |
| `device:llm-lab-dell\|claude` | Dell (company) | claude | reachable + authorized | **not deployed** |
| `device:llm-lab-dell\|claude-fjd` | Dell (company) | claude | **UNKNOWN** — no observation names this second account | **not deployed** |
| **Codex** (any device) | — | codex | registry declares `claude-code` only | **UNKNOWN** |

**How the left column was read.** It is an **operator-reported live reading**
taken today through the legacy Fleet 0.1.0 `fleet_status`: all three company
providers answer **reachable + authorized**, and each reports **22 native
capabilities**. The ChatGPT-facing surface shows **9 Fleet tools and 4 scopes**.
It is recorded as reported rather than re-measured, because verifying it would
need a live credential and this document is produced without one.

**How the right column was read**, and it was measured here: the Next unit is
`LoadState=not-found` on this host, nothing listens on the candidate ports, and
no independent public route exists. The two columns disagree on purpose —
Tokyo's legacy field being live says nothing about a Next node, and Windows and
Dell being reachable in the legacy field is **not** a new-version deployment
either.

Three readings this table refuses to make:

- **7 tools is a historical snapshot, not today's number.** It came from a
  2026-10-13 measurement of the then-current V1 and is not restated as live.
- **A reachable provider is not a deployed new version.** The old field's
  authorization is authority the old release already had.
- **A historical Codex session is not a working `create`.** Finding a session in
  a listing is evidence that something once existed; it is not evidence that
  `create` succeeds now. Codex stays UNKNOWN until **live capability discovery
  plus an explicit commitment** — a framework's ability to carry Codex is not
  evidence that any machine runs it.

Devices the registry declares `ownership: personal` are out of scope by rule, not
by omission: `GRANT_ELIGIBLE_OWNERSHIP` admits `company` and nothing else.

---

## 5 · Five evidence levels

A claim must name its level. The levels are cumulative but not interchangeable —
the highest reached for one lane says nothing about another.

| level | means | counts as |
|---|---|---|
| **CODE** | present in a branch, reviewed | nothing operational |
| **MOCK TEST** | exercised offline against fixtures | the logic holds on fakes |
| **LOCAL STAGED** | files placed on the host, nothing started | the bytes are where they will run |
| **LIVE SERVICE** | a service is running, with resource bounds read back | it runs here |
| **CLIENT INSTALLED** | a client shows it, with the tool list read back | a user can use it |

**PUBLIC HTTPS LIVE** is a sub-level of LIVE SERVICE: the route answers from
off-host. It is not the same as CLIENT INSTALLED, and a 200 from `/health` is
not a tool list.

---

## 6 · What V1 must not be blocked by

Recorded so a reviewer can point at a line rather than argue about intent:

- **No second Work engine.** Work items, parents, runs and acceptance already
  have an owner. A delegation envelope, a task store, an "Orchestrator" or a
  queue added here would be a second authority answering the same question.
- **No V2-only gate on a V1 release.** A gate whose only purpose is to prepare
  OpenAgents, role teams or autonomous scheduling does not stand between V1 and
  its release.
- **No reuse of the old Funnel.** V1's public exposure is not a template for
  Next's. Next gets its own node or its own route; sharing the published URL is
  refused by the preflight's own negative test.
- **No production change dressed as a plan.** Creating an App, logging into
  Feishu, operating a tunnel or installing a plugin are owner actions with their
  own approvals.

**Partly reviewed 2026-10-15 — one half is a checked absence, the other is an
open risk that Lane F must clear.**

*Checked and absent.* #198's staging, installer and unit carry no V2 concept
(`openagents`, `federation`, role teams and delegation match nothing in them),
and its preflight deliberately relaxes rather than tightens — recovery ignores
both limits and attestations, so a revoked OAuth client cannot prevent an
operator from restoring the Next-only service.

*Checked and NOT cleared — the claim that there is no V2 block is withdrawn.*
#199's evidence gate is not V2-shaped in its runtime set: that set is dynamic and
owner-authored ("Runtime count is not hardcoded"), and its Codex-on-Dell sentence
is a non-falsification rule rather than a requirement. But two of its demands are
**strict enough to block V1 permanently**, which is the risk, and an earlier
revision of this section read only for V2 vocabulary and so missed it:

- **Every required runtime must report all ten lifecycle operations `PASS`** —
  `list`, `read`, `create`, `send`, `interrupt`, `reconfigure`, **`approval`**,
  **`close`**, **`resume`**, **`recover_after_disconnect`**. This repository's own
  contract already records that Fleet has **no `close` verb**
  (`fleet/owner-grant-3devices.md` G3) and lists session-lifecycle entries under
  `not_implemented`. If any CloudCLI or Codex provider cannot perform one of
  these, that instance can never pass and V1 is blocked by a rule that no work
  can satisfy.
- **A blanket `real_time_monitoring` check** sits in the release list alongside
  the release's own checks. A monitoring capability deciding a V1 release is the
  shape of a V2 requirement reaching backwards.

**Status: PENDING INDEPENDENT QA — do not read this as "no V2 block".** The
question for Lane F is whether each listed operation is (a) genuinely required
for V1, (b) actually performable by every provider V1 declares, and (c) fail-
closed without becoming unsatisfiable. Until Lane F answers, the honest state is
*unverified*, not *absent*.

---

## 7 · Model, permission and resource floors (what is known, and what is not)

### Permission

`bypassPermissions` is a session mode, **not** OS root or admin. In this product
it is reachable only through `policy.create_allow[<project>].permission_modes`,
and a runtime-level default may only **narrow** that ceiling — the runtime policy
is parsed as a subset of the project grant, so it can never widen it. A machine
that authorized something in one account has not authorized it in the account
beside it: `allow_create` is read per binding.

### Model

**No model list lives in this repository.** The three DeepSeek routes discussed
for the default (`FJ 生产服(admin)`, `FJ 生产服(vince)`, `东京网关0`) are
**LIVE-UNVERIFIED**: nothing in this tree confirms any of them is offered by the
CloudCLI on this host, and the repo's own issue #197 says unqueried models are
UNKNOWN. `upstream_model` is provider-native and is **not** proof of entitlement.

The two exception accounts — the official Claude subscription and MarketOps —
must not inherit a DeepSeek route. That is enforced structurally by their profile
kind, with a substring check as defence in depth; the structural guard is the
allowlist, and the substring check should not be read as complete.

### Resource

A Next service runs under a bounded budget and the bounds are **read back from
systemd** after start; a rollout whose limits did not take effect is rolled back
rather than reported as healthy. The host's own state is checked before a start,
because a canary that begins during memory thrashing measures the thrashing.

---

## 8 · Client-side facts that force a human round trip

| change | what it costs |
|---|---|
| tool **schema** or **scope** change | the client may need a `tools/list` refresh, and a widened scope needs **OAuth re-consent** |
| Skills, assets, metadata | a new ZIP through the plugin's own update path |
| hosted MCP behaviour | **no ZIP** — MCP scans handle it |
| backend devices/runtimes/instances | **no client action at all**: the Plugin ID, App ID and MCP HTTPS address stay fixed |

Never promise "not even a refresh". The honest sentence is: the *identity* is
fixed, so scaling the backend needs no reinstall — while a change to what a tool
*is* can still require a refresh and a re-consent.

**Two ZIP kinds, which are not interchangeable**:

| | first install + Skill release | backend-only update |
|---|---|---|
| what moves | manifest, Skills, assets, version | nothing on the client |
| path | private Plugin Creator update against the existing Plugin ID, guarded by the expected release id | the server's own release |
| forbidden | app references in a **public** submission ZIP | — |

---

## 9 · Next-only, and the shortest safe path

Isolation is structural, not promised:

- a **separate unit name** — the Next unit's own header forbids installing under
  V1's name;
- a **separate release root** and composition;
- a **separate port**, and a preflight check that the **live** bindings on disk
  differ — not merely that two JSON numbers differ;
- **V1 health checked before** Next starts and after it launches; either failure
  stops **Next only**;
- a Next-only stop/rollback on every failure path, and an honest `writes` field:
  once anything has been attempted, the report never claims `writes: []`.

**Order**: resource gate → offline suites → stage (`--no-start`) → owner-approved
route → one bounded canary → client install. At any step the previous step's
artifacts are the rollback.

---

## 10 · Ownership of the remaining gates

| gate | who | state |
|---|---|---|
| **the new Next ingress and its own Tailscale node** | — | **already authorised in this session.** The exposure itself is in scope; only the account-owner's interactive SSO/MFA step remains a human call |
| creating the Next App / Plugin ID | **account owner** | external registration; one-click consent is the owner's |
| logging into the official Claude and MarketOps accounts | **account owner** | credentials are never copied, shared or agent-held |
| changing Feishu app 2's redirect list | **account owner** | a production console change |
| the first client install | **user** | it happens in their client, once |
| production deployment | **separate approval** | not covered by this plan |

The split is between **authorisation to work** and **an owner's own account
action**. Ordinary repository, staging and canary work on the Next lane does not
need to be re-approved; what stops at a human is anything that needs that
person's identity — an interactive login, a one-click consent, a console edit.
