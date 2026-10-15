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
| **Core-Lite** | a **per-OS-user, observe-only** local Node/Core on each device: **Linux** (the managed UID) and **Dell `fjd`** get the C++ Core; **Windows** keeps its existing local runtime and waits for a light adapter. See §14 |
| **basic monitoring** | CPU / RAM / Swap / realtime PSI (where the kernel offers it) / disk / service health / sampling freshness, plus a read-only Dell GPU-VRAM summary. UNKNOWN where unavailable, and **never an execution permission** |

**Monitoring adds no surface.** These readings ride as sub-fields of the
**existing read-only Fleet status**, not as new MCP tools, a new state store, or
a shell. The tool count stays 33 and the scope count stays 6 — a monitor is not
a verb, and a metric is not an entitlement.

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

| runtime | device | family | **legacy V1 field (live)** | **new Next node/binding** | **required for V1?** |
|---|---|---|---|---|---|
| `device:tokyo-shared-dev-01\|claude` | Tokyo (company) | claude | reachable + authorized | **not deployed** | **yes** — the managed account is Tokyo's `vince` |
| `device:work-fj\|claude` | FJPCOS (company, Windows) | claude | reachable + authorized | **not deployed** | **yes** — the authorised local user |
| `device:llm-lab-dell\|claude-fjd` | Dell (company) | claude | **UNKNOWN** — no observation names this account | **not deployed** | **yes** — **`fjd` is Dell's required runtime** |
| `device:llm-lab-dell\|claude` | Dell (company) | claude | bound to **`vince`** | **not deployed** | **no** — legacy only, see below |
| **Codex** (any device) | — | codex | registry declares `claude-code` only | **UNKNOWN** | capability-dependent |

**Dell's required runtime is `fjd`, and the legacy reference is not remapped.**
`device:llm-lab-dell|claude|<id>` is bound to `vince` and **must not be silently
repointed at `fjd`**. The address someone already holds keeps meaning what it
means. `fjd` is reached by its **own tag**, `claude-fjd`, on the **same**
`device:llm-lab-dell` ref — one physical machine, two runtimes, as everywhere
else in this document.

Two things that look like shortcuts and are not: `device_default_account` is
**not** set to `fjd` in order to relabel the old `claude` tag, because that
would re-address live sessions through a declaration; and a device-level
`preferred_runtime = claude-fjd` is a **UX choice** about which runtime an
operator is shown first — it is not an alias, and it grants nothing.

The old `vince` connector is a **lossless transition**, not a target: it is not
dispatched work by the new Next, and retiring it is a separate act requiring
**its owner's** approval. Nothing here deletes the `vince` OS account.

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

**Status: CLOSED by Lane F at `a8fe25d`.** The risk was real and Lane F fixed it
rather than restating it. The flat rule is gone: operations are split into
`MANAGED_ACTIONS` and `CAPABILITY_ACTIONS`, and the provider-gated side must be
**evidenced** — a declared `UNSUPPORTED` needs its own receipt, must agree with
the per-operation verdict, and cannot cover anything the release promises, while
`UNKNOWN` or a merely absent field blocks. Lane F's own words for why: under the
flat rule a Codex runtime with no interactive approval channel "blocked the
release forever, and the only way past it was to write `PASS`" — a false receipt
that the gate would then treat as its strongest evidence.

The blanket `real_time_monitoring` check is renamed `psi_and_resource_caps`: a
release-gating check should name the V1 precondition it actually tests, not a
monitoring *outcome*. The runtime census became a six-dimensional managed matrix
(`Device × OSUser × family × credentialInstance × Runtime × Project`), which is
what stops two CC accounts under one OS UID — and Dell's vince and fjd — from
collapsing into a single green row. And V2 is excluded in the module's own text:
no OpenAgents layering, no autonomous self-governance, no trend forecasting or
self-healing.

Kept as a standing instruction rather than a closed item: a *new* gate must be
read for two failures — one vocabulary check for V2 concepts, and one strength
check for a rule V1 cannot satisfy. The first revision of this section ran only
the first and therefore missed the second.

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
| **Dell is reached as `fjd`** | **`fjd`** | **their own** login and their own workspace; nothing is copied from `vince` and nothing of theirs is read. §14 |
| **`fjd`'s `linger`** | **`fjd` / OS owner** | currently `no`; a user-level Core needs it on, and enabling it is a controlled system act |
| **installing a Core** on any device | **owner**, per device | §14 — Core is SOURCE/TESTED today and a monitor is not an installation |
| **Windows C++ native parity** | **owner** | needs a **real Windows test environment**; until it exists the Windows Core gate is **UNKNOWN** |
| **retiring the legacy Dell `vince` connector** | **`vince`'s owner** | only after `fjd` passes acceptance, and only on their word |

The split is between **authorisation to work** and **an owner's own account
action**. Ordinary repository, staging and canary work on the Next lane does not
need to be re-approved; what stops at a human is anything that needs that
person's identity — an interactive login, a one-click consent, a console edit.

---

## 11 · Integrated tip, and what it actually runs

**This section is a receipt, not a plan.** Everything in it was measured; the
numbers are the real output of a real run.

### What the tip is

`8257d2d` — the integration branch `feat/fleet-v1-integration-20261015`, merged
with `--no-ff` so each lane's history stays readable:

| brought in | lane | what it adds |
|---|---|---|
| **F · `a8fe25d`** | release QA | the promise/capability split, the six-dimension managed matrix, `health_admission`, and a human acceptance runbook |
| **D · `1243f2d`** | Dell | the two-account route isolation tests, the runtime matrix, and the Dell readiness document |
| **A · `cf21c67`, `f5ddd88`** | integration | this plan and the V1/V2 boundary |

The three lanes touched **disjoint file sets** (zero overlap, checked before
merging), which is why both merges were clean and neither needed a conflict
resolution that a reviewer would have to trust.

### The real run

```
systemd-run --user --scope -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64
  -- timeout 120 python3 -m pytest -q tests/test_fleet_v1_dell_*.py tests/test_fleet_next_*.py

228 passed, 2 failed, 111 subtests passed in 4.70s
```

The kernel limit was **read back, not assumed**: `memory.max` inside the scope
reported `1073741824` — exactly 1 GiB. The run is bounded in memory, in swap
(zero) and in CPU, so it cannot be the thing that takes the host down.

### The two failures are inherited, and they are not this lane's to fix

Both are **#198's own tests**, and both test files *and* the sources they
exercise are **byte-identical between `8186028` and the integrated tip** — so
neither D nor F nor A caused them:

| test | cause |
|---|---|
| `test_fleet_next_dual_stack_preflight::…human_canary_precheck` | a **stale assertion string**. The test wants `"not an authorization"`; the source emits `"This is dry-run metadata, never an authorization or a launch."` — the same claim, stated more fully. |
| `test_fleet_next_install::…stage_always_passes_no_start…` | the **executed path of `main()` prints nothing**: only the preview and blocked paths emit JSON, so the test's helper parses an empty string. That is also a gap in the module's own promise to report what it did after acting. |

**Owner: Lane E** (now reassigned to the Dell vince session). They are recorded
here rather than patched from this lane, because editing E's source and tests
from here would put two writers on one file — the exact collision the
reassignment was made to avoid. **The honest state is 2 red, not 230 green.**

### What is *not* live — the part that matters most

**V1 is not live in any sense this document can certify.** Not one row of §5
reaches beyond LOCAL STAGED for the Next surface: no Next service has ever
started, no Next node is bound, no Next route exists, and no plugin has been
installed. The Dell two-account paths are exercised **against fixtures**;
`fjd` has never been connected on real hardware and needs its **owner's login**.
The Tokyo legacy field is reachable — and that is the old release, not this one.

Everything past S3a therefore remains an **owner action**, listed in §10, and
this document still cannot stand in for one.

---

## 12 · A ↔ B contract: runtime identity, and one test class that must not gate

**Status: CONTRACT_PENDING_REWRITE.** Recorded here so the requirement survives
the branch that will satisfy it.

### Two different things are called "the runtime"

| | what it is | what it is for | where it belongs |
|---|---|---|---|
| **profile tag** | a **stable logical** selector for one credential profile — the general DeepSeek route, the official Claude account, the MarketOps account | addressing. It is what a session ref names and what a grant is written against | **in** the session ref, as the middle segment |
| **instance / incarnation** | one **running** binding of a profile: this connector, this boot, this credential | fencing. It is how a reconnect proves it is the successor and a stale binding is refused | **out** of the session ref, as a sidecar on the Node identity |

They must not be the same field, and neither may stand in for the other.
Putting the incarnation in the tag makes the address drift on every restart and
every credential rotation — every live session would be re-addressed by an event
that changed nothing about who it belongs to. Collapsing two profiles into one
tag is the opposite failure and the worse one: two accounts' sessions share an
address, so a native session id that exists in both becomes a coin toss.

### Why the OS user is not enough

`runtime_tag(user)` currently derives `claude` / `claude-fjd` from the **OS
user** alone. That is exactly wide enough for the two-account case this release
started with and wrong for the one it is heading to: one UID is to carry **0..N
independent CC identities** — a general DeepSeek profile, an official Claude
subscription, a MarketOps identity — and those are three different credential
owners inside one account, not three spellings of one.

So:

- Each credential profile gets its **own stable tag**, and the tag is stable
  across restarts and credential rotation.
- The **legacy address keeps its meaning**: `device:<id>|claude|<native id>` is
  what every live session already says, and it must keep saying it. The default
  profile continues to answer to the bare tag.
- The **agent family stays a separate axis**. `claude` / `codex` is what kind of
  agent it is; the profile is whose credential it runs under. One runtime serves
  both families and neither is derivable from the other.
- The **sidref stays three segments**. A fourth is refused by name today and
  that refusal is a positive invariant, kept — see
  `TheSessionRefMustNotChangeTest`.

### The instance sidecar

An instance id is bound to the **Node identity and the connector's own live
readback**, and it is used for reconnect proof and fencing: a returning
connector shows which incarnation it is, and a binding from an earlier one is
refused. It **grants nothing** — it is not a credential and not a permission,
and an identity field is never evidence of authority. A profile whose owner has
not logged in is **refused**, not defaulted to a neighbour: no implicit
cross-account fallback, ever.

### The test class this supersedes

`tests/test_fleet_v1_dell_instance_identity_and_smoke.py::TheBindingNamesAnAccountAndNotAnInstanceTest`
asserts that something is **absent** — no instance field on a binding, no way to
distinguish a restart from its predecessor, a provider key with exactly one
separator. It was written as a measurement of the gap and its own docstring says
so, but a green is a green: left passing it reads as "this is how it should be",
and Lane B's correct change would turn it red with "keep the gap" as the
cheapest repair.

It is therefore **skipped under the name `CONTRACT_PENDING_REWRITE`**, with the
reason printed in every run, and **not deleted** — the measurements stay
readable and stop counting toward a release. When Lane B lands, each becomes a
positive test (two profiles under one UID get two distinct stable tags; a
restart is distinguishable from its predecessor; a stale binding is refused) and
the skip comes off in the same change that makes it pass.

One item is **watched rather than skipped**, because it is genuinely unclear
whether it survives: `test_fleet_v1_dell_runtime_matrix.py` asserts
`runtime_tag("admin", …) == "claude-admin"`. That stays correct if the default
profile keeps the OS-user derivation; it breaks if the derivation gains a
profile axis. It is recorded here so the next round decides with B's interface
in hand instead of guessing.

---

## 13 · S2 integration state, and the S3 pre-launch checklist

**This section is a receipt.** Every number in it is a real run; every "unknown"
is something nobody has run.

### What is on the tip

`cd68cb4` plus `f56ca1a`, merging two lanes **by their own histories** rather
than by re-application:

| brought in | lane | how |
|---|---|---|
| `feat/fleet-v1-runtime-fallback-20261015` @ `23668db` | **B · runtime identity** | `--no-ff` merge of the branch |
| `feat/fleet-v1-preflight-smoke-20261015` @ `d4f4e5c` | **E · ingress** | `--no-ff` merge — E was already stacked on this branch's former tip, so it is ONE merge and no E commit is applied twice |

Both merges were checked for file overlap first and neither needed a conflict
resolution a reviewer would have to trust. Lane E independently reached the same
conclusion A's §3 did about the circular route stage, and its commit says so.

### The two gap characterisations are migrated, not deleted

Lane B closed both gaps, and both were written by their authors to be rewritten
when that happened:

- `…_dell_create_route_isolation.py` — a runtime with no binding now answers
  `UNKNOWN_DEVICE` for **send as well as create**, and the refusal no longer
  carries the withheld-grant vocabulary that would confirm a profile exists.
- `…_dell_instance_identity_and_smoke.py` — a binding now HAS `account`,
  `profile` and `instance_id`; two incarnations of one account are
  distinguishable; naming one grants nothing; and the incarnation stays out of
  the three-segment session ref.

Neither was kept as a false constraint and neither was removed. The
`CONTRACT_PENDING_REWRITE` marker is gone because the rewrite it named happened.

### The real run

```
20 fleet suites, in systemd-run --user --scope
  -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64

730 passed, 401 subtests — no failures, no skips
```

The two failures #198 carried (`test_fleet_next_dual_stack_preflight`,
`test_fleet_next_install`) are **gone**: Lane E repaired them.

### What is NOT done, and must not be reported as done

| | state |
|---|---|
| **C · plugin #204** | **NOT integrated.** Its `extensions.com.openai.interface` and `mcpServers.<name>.extensions.com.openai.auth` fields are wrong and it is with the Dell C lane. Integrating it now would put a manifest nobody has reviewed into the candidate. |
| **the approved logo bytes** | **not in this repository.** The conversation's asset pack has not been transferred; a synthetic fixture is NOT a brand asset and the overlay reports `NEEDS_ASSET`. |
| **`instance_id`** | a **local declaration**. Nothing signs it and no remote incarnation is read back, so it is not fencing. **Node signed incarnation is an open protocol item**, not a passing test. |
| **the three pytest suites E did not run on Dell** | **UNKNOWN** — E's report named them; this run is the first time some of them executed here. |
| **real accounts** | **not logged in.** `fjd`, official Claude and MarketOps are unconnected. No host has been changed. |

### S3 pre-launch checklist — what was observed, read-only

Nothing below starts anything. Each line is a fact read from the host, and the
owner actions beside it are the ones a plan cannot perform.

| # | checked | observed |
|---|---|---|
| 1 | candidate port **3411** free | yes — `ss -ltn` filtered to `:3411` returns no listener |
| 2 | V1 untouched and serving | `serveros-fleet-mcp.service` active, `127.0.0.1:3401` sole listener |
| 3 | user systemd | available (`systemd-run` at `/usr/bin/systemd-run`) |
| 4 | userspace Tailscale binary | **staged only** — 1.104.1 at `~/.local/opt/fleet-next-tailscale/1.104.1/`, SHA verified against the official sidecar; **no node created** |
| 5 | `tailscale`/`tailscaled` on PATH | **absent** — the staged pin is not installed anywhere, deliberately |

**Owner actions still required before any canary**, none of which an agent may
perform: the owner's browser login and MFA for the new Tailscale node; the
Funnel approval for it; a real FQDN for the Next ingress; registration of the
Next **App and Plugin ID**; the Feishu app-2 callback registered against that
FQDN; and the approved brand bytes transferred in.

Until those exist the Next surface has **no public route at all** — so a
discovery document, a callback URL or an installable ZIP cannot be produced from
what is here, and none is claimed.

### Scope, unchanged

V1 is stable cross-device control of the authorised CC/Codex cluster with thin
Skills. OpenAgents, role teams and broader Ability governance are V2 and are not
a dependency of this release. Nothing in this section adds scope to V1.

---

## 14 · V1 gains Core-Lite — and what "observe-only" is a claim about

### The addition, and its ceiling

V1 now includes a **minimal, per-OS-user, observe-only local Core** on each
device, reporting **basic resource and service health**. The ceiling is stated
first because it is the part that is easy to lose:

- **observe-only.** It reads. It does not restart, stop, reclaim, heal or
  execute. Bounded self-healing is **V2** and is not a V1 dependency.
- **No new surface.** The readings arrive as **sub-fields of the existing
  read-only Fleet status** — not as new MCP tools, not as a new state store,
  and not through a shell. **33 tools and 6 scopes do not move.**
- **A metric is not a permission.** Nothing here widens what a credential may
  do, and a freshness field is not a grant.

| what is read | note |
|---|---|
| CPU, RAM, Swap | per the managed UID |
| realtime PSI | **where the kernel offers it**; absent is reported absent, not zero |
| disk | |
| service health | |
| **sampling freshness** | when the reading was taken, and how old it is |
| Dell GPU / VRAM | a **read-only summary**, where the capability is available |
| anything unavailable | **UNKNOWN** — never estimated, never inferred from silence |

**Timestamps are UTC.** A monitoring record with a local-time timestamp is one
that two devices disagree about, and the disagreement lands exactly when someone
is comparing them during an incident.

**Anomalies are reported as findings.** A reading outside its bound is recorded
with the bound it crossed and the reading that crossed it — and it changes
nothing. There is no code path from an anomaly to an action, in V1, by design.

### Core local truth, and the Fleet's thin proxy

The device is the **authority** for its own facts. The Core reads them locally;
Fleet carries a **summary** and never recomputes it. So a number shown through
Fleet is a **copy of a local reading with its own time on it**, and the two must
be reconcilable: if the proxy and the device disagree, the device is right and
the proxy is stale — which is why freshness travels with the value rather than
being assumed from the fact that something was returned.

### C++ Core maturity — as it actually is

| | |
|---|---|
| **what exists** | Lane B's `#180` (`5a174ce`) is a **Linux C++20 observe-only resident loop**, a Unix status socket protected by **exact `SO_PEERCRED`**, a status JSON, and a user-systemd template, with offline unit tests |
| **what does NOT exist** | it is **not installed and not boot-enabled anywhere**; the production Python `serverosd` remains **the only writer**; the `applied_generation` fence is **not persisted and not consumed** by the loop; signed trust and bounded self-healing are **not verified on real hardware**; and the **Windows SCM/Task-Scheduler adapter is a separate, unimplemented design** |
| **what that means for V1** | **SOURCE / TESTED only.** Adding Core to the scope does **not** install it, boot it, or make it authoritative about anything |

**Two writers on one unit is forbidden.** The production Python daemon and a
native Core must never both own the same unit; a migration is its own reviewed
step with an explicit handover, not a side effect of deploying a monitor.

### The Windows-parity risk, named as a gate

Windows has an existing ServerOS local runtime, and a **light Windows adapter**
is the plan. But a Linux `systemd` template cannot stand in for it: Windows
supervises differently, and "we have the same code" is not "it behaves the same
here". **If Windows C++ native parity is required, it needs a real Windows test
environment, and until that exists the Windows Core release gate is UNKNOWN** —
not passed, and not waived.

### Migration: what may move, and what may not

The rule is one sentence: **code that is in git may move; anything that is a
credential, a session or a private file may not.**

| may move | may NOT move |
|---|---|
| ServerOS project **git repositories, branches, unpublished commits** (committed first, or exported as a reviewed key-free patch) | `mv /home/vince /home/operator`, any bulk `rsync` of a whole `HOME`, any `sudo`/`chown` shortcut |
| **owner-selected, credential-free config templates** and necessary project records | `.ssh`, `.config/gh`, `.claude`, `.codex`, OAuth state, Tailscale state, node secrets, CloudCLI credentials, Unix sockets, **live session databases** |
| | `/opt/strata` and the existing GPU/Qwen data — **left in place, untouched** |

The **`fjd` account is logged into by `fjd`** (or by someone they explicitly
authorise) — never by copying `vince`'s git token or Claude account. Source
worktrees are **preserved as they are**, with their dirty state, hashes and
manifests checked rather than assumed.

**`fjd`'s `linger` is `no`.** A user-level Core needs the account to survive
logout, so enabling it is an **account-owner action performed with the
controlled system method** — not something this lane may do, and not something
to be worked around.

### Staged acceptance, in order

1. Core-Lite requirements and the device profiles; the Linux candidate's
   regression; **a separate Windows adapter design** (not a port of the Linux one).
2. Each device's read-only sampling, service state and sampling time; UID
   isolation; resource caps; and **no automatic action anywhere**.
3. `fjd` authorises and prepares **their own** workspace and their own
   CloudCLI/CC/Codex accounts, with each model and permission mode genuinely
   read back.
4. Register Next Dell `claude-fjd`; confirm read-only, then a controlled
   session create/send. **The old `claude` history is not repointed and `vince`
   is not in the new required runtimes.**
5. The GPU/Strata services are still alive, and Next and the legacy Fleet are
   both stable; the user confirms the legacy Dell `vince` connector's retirement
   from their own receipt.
6. The ChatGPT first install over a stable MCP + Skills, and a same-Plugin-ID
   backend upgrade.

V2 adds automation and governance on top. It is not a precondition for any step
above.

### What this section does not authorise

Nothing here installs a Linux or Windows Core, switches users, reads `fjd`'s
private files, writes `fjd`'s home, or retires the legacy connector. Those are
owner acts in the sequence above, and a document recording scope is not a
receipt for any of them.
