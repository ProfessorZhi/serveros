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
| **S3 · independent public route** | S2 + owner approval | a Next-only HTTPS base is **provisioned and observed reachable**, separate from V1's |
| **S4 · Next-only canary** | S3 + a green resource gate | one bounded Next service runs, with V1 health checked before and after |
| **S5 · client install** | S4 + owner action | the plugin is installed once, under its own Plugin ID |

S2 is a merge, not a deployment. S3 and S5 are **owner actions on external
systems**, and a plan may not stand in for either.

---

## 4 · Managed-runtime acceptance matrix

Every runtime is one credential instance. Two CC configurations in one OS
account are **two runtimes**, which is why the key is a stable runtime id and not
a username.

| runtime | device | family | state today | what closes it |
|---|---|---|---|---|
| `device:tokyo-shared-dev-01\|claude` | Tokyo (company) | claude | **live** — V1 running, 7 tools served | already deployed; the 33-tool surface is a Next-side change |
| `device:work-fj\|claude` | FJPCOS (company, Windows) | claude | **not deployed** — connector/binding not proven present | Windows-side onboarding, executed on that machine |
| `device:llm-lab-dell\|claude` | Dell (company) | claude | **not deployed** — `README.linux.md`: "not enrolled by any of this" | owner-approved enrolment |
| `device:llm-lab-dell\|claude-fjd` | Dell (company) | claude | **not deployed** — the second account's isolation facts are owned by the device, not the host | owner login as that account |
| **Codex** (any device) | — | codex | **UNKNOWN** — the registry declares `claude-code` only | **live capability discovery plus an explicit commitment**; a framework's ability to carry Codex is not evidence that any machine runs it |

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

**Reviewed 2026-10-15, result: no such block exists today.** #198's staging,
installer and unit carry no V2 concept (`openagents`, `federation`, role teams
and delegation match nothing in them), and its preflight deliberately relaxes —
recovery ignores both limits and attestations so that a revoked OAuth client
cannot prevent an operator from restoring the Next-only service. #199's evidence
gate is the opposite of a V2 blocker: its required runtime set is **dynamic and
owner-authored** ("Runtime count is not hardcoded"), its Codex-on-Dell sentence
is a non-falsification rule rather than a requirement, and it refuses to call
its own output `PRODUCTION_READY`. Recorded as a checked absence, not as a fix
that was needed.

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

| gate | owner | why it cannot be an agent |
|---|---|---|
| creating the Next App / Plugin ID | **account owner** | it is an external registration |
| logging into the official Claude and MarketOps accounts | **account owner** | credentials are never copied or shared |
| changing Feishu app 2's redirect list | **account owner** | a production console change |
| provisioning a Next-only public route | **owner + user approval** | off-host exposure |
| the first client install | **user** | it happens in their client |
| production deployment | **separate approval** | not covered by this plan |
