# Fleet Next V1 — release captain

An operation frame for releasing Fleet Next **beside** Legacy Fleet 0.1.0, and
for taking the Next half back out again. It is not a Fleet service, not a
second control plane and not an installer.

The owner's 2026-10-28 decision (#478) and the campaign issues (#425, #434,
#409) grant: new isolated worktrees, offline tests, Draft PRs, issue receipts,
and a side-by-side Next canary that the device's own operator approves. They do
**not** grant a `main` merge, a Legacy replacement, a credential/OAuth
provider change, a real Feishu/IM send, a Tailscale mutation, a paid-model
trial, or an irreversible delete. This module encodes that boundary rather
than relying on the next reader to remember it.

## What is here

| file | role |
| --- | --- |
| `release_captain.py` | the state machine, the gate arithmetic, the rollback planner. Stdlib only, no clock, no network, no writes. |
| `release-manifest.2026-10-29.json` | the facts this release is allowed to depend on, as observed on 2026-10-29. |
| `schema/release-manifest.v1.schema.json` | the manifest's documented shape. |
| `rollback-plan.example.json` | the shape of a generated Next-only rollback (Tokyo first-install, `mode=withdraw`). |
| `evidence/CI-SNAPSHOT-2026-10-29.json` | per-head check-run conclusions at capture time, and what each one means. |

## States

`PRE_FLIGHT → READY → CANARY → PROMOTE`, plus `ROLLBACK` and `BLOCKED`.

| state | meaning | what must be true |
| --- | --- | --- |
| `PRE_FLIGHT` | collection has begun; every open item is a **missing** observation | no gate is red; at least one is unknown |
| `READY` | this exact tree could be canaried | source identity, 5/5 CI on that head, independent QA on that head, artifacts + provenance, Legacy pre snapshot, disjoint namespaces |
| `CANARY` | one device holds a bounded Next instance | READY, plus local operator grant, reachability, measured headroom, verified Next-only rollback, device order |
| `PROMOTE` | this device's canary is accepted (never a Legacy switch-over) | CANARY, plus canary probes PASS, Legacy still serving, independent acceptance, owner approval, Next plugin id registered |
| `ROLLBACK` | the Next half is being withdrawn | a plan exists, every step is Next-only or read-only-Legacy, and it is idempotent |
| `BLOCKED` | at least one gate is red or unknown | the reason codes below |

`PRE_FLIGHT` is not a polite word for `BLOCKED`. It means nobody has said *no*
yet — only that nobody has looked. Both block the release; they are different
facts and the receipt says which.

## Reason codes

| code | means |
| --- | --- |
| `BLOCKED_UNKNOWN` | a required fact was never observed. Never read as a default. |
| `BLOCKED_SOURCE_IDENTITY` | a SHA/tree moved, or green evidence names a different commit. |
| `BLOCKED_CI` | fewer than 5 jobs, a failing job, or a job still running. |
| `BLOCKED_QA` | QA is red, or was not independent of the author. |
| `BLOCKED_ARTIFACT` | a package has no sha256, or it is not 64 hex. |
| `BLOCKED_PROVENANCE` | no builder/command/time, or the artifact was built from another tree. |
| `BLOCKED_LEGACY_COLLISION` | Next and Legacy share a unit, port, socket, plugin id, backend id, audience, state root, lock, runtime root or config path. |
| `BLOCKED_LEGACY_SNAPSHOT` | the Legacy pre/post snapshot is missing, malformed, or drifted. |
| `BLOCKED_DEVICE` | the device is unreachable (a gateway timeout is this, not "retry until green"). |
| `BLOCKED_DEVICE_GRANT` | no grant from that device's own local operator. |
| `BLOCKED_DEVICE_ORDER` | an earlier device has not accepted a canary yet. |
| `BLOCKED_RESOURCE` | another actor holds a conflicting resource, or headroom was not measured. |
| `BLOCKED_OWNER` | the action is outside the owner's grant, or the owner has not approved yet. |
| `BLOCKED_LEGACY_PARITY` | the plan would retire or interrupt Legacy. |

Every code in this table is produced by at least one fixture in
`tests/test_fleet_next_v1_release_captain.py`; a code that no fixture can emit
fails that suite.

## Running it

```bash
# Where are we, honestly?
python3 integrations/fleet-next-v1-release-captain/release_captain.py \
    --manifest integrations/fleet-next-v1-release-captain/release-manifest.2026-10-29.json \
    state

# Would this tree reach READY / CANARY / PROMOTE? Prints every blocker.
python3 ... release_captain.py --manifest ... evaluate --target READY
python3 ... release_captain.py --manifest ... evaluate --target CANARY \
    --device device:tokyo-shared-dev-01

# The order a release would take. Executes nothing.
python3 ... release_captain.py --manifest ... plan

# The Next-only rollback, as a dry run. Writes nothing, executes nothing.
python3 ... release_captain.py --manifest ... rollback
```

Exit codes: `0` target reached / plan printed, `2` target blocked,
`3` manifest or rollback refused.

### Why `--execute` cannot run anything here

This CLI has no executor. `--execute` prints `EXECUTOR_ABSENT` and exits `2`.
The only executor that exists is the caller's, and `execute_plan()` refuses to
drive it unless all of the following hold: the environment is `canary`, an
owner approval record is present and granted, and an evidence directory is
named. That is deliberate — a planner that can silently become a deployer is
how a "dry run" stops being one.

## Device order

1. `device:tokyo-shared-dev-01` (order 1)
2. `device:llm-lab-dell` (order 2)
3. `device:work-fj` (order 3)

A later device is gated on every earlier device being `accepted` **and**
independently accepted. If a gateway times out, that device is `BLOCKED_DEVICE`
and the release says two devices, not three. It never reports a device it could
not see as ready.

## Namespace rules

Next must not reuse any of Legacy's: service unit, ports, socket, plugin id,
backend id, OAuth audience, state root, lock path, runtime root, config path,
OAuth state root.

* Identical values collide.
* A **nested** path collides (`/srv/legacy/state/next` under `/srv/legacy/state`).
* A **prefix sibling** does not (`/srv/legacy-state` beside `/srv/legacy/state`).
* URLs and bare names are compared exactly, never as directories.

`plugin_id` is the one identity value allowed to be unknown at `READY` and
`CANARY`: the reviewed two-stage bootstrap cannot know it before a Next
endpoint has served a public origin once. `PROMOTE` still requires it, and
`evaluate(target="PROMOTE")` is where that is enforced — the exemption is one
state wide, not a permanent hole.

## Rollback

Two modes, because "roll back" means two things:

* `revert` — a previous Next revision exists; repoint `current` at it, restart
  the Next unit.
* `withdraw` — this was the first Next install; stop, disable, remove the Next
  unit and the Next state root.

Refusals by name:

| code | when |
| --- | --- |
| `ROLLBACK_NOTHING_INSTALLED` | no installed revision is recorded — there is nothing to roll back |
| `ROLLBACK_SAME_REVISION` | previous == installed: a no-op wearing the word rollback |
| `ROLLBACK_NO_DEVICE` | the device is not in the manifest |
| `ROLLBACK_UNKNOWN_STEP_KIND` | a step is neither Next-only, read-only-Legacy, nor evidence |
| `ROLLBACK_TOUCHES_LEGACY` | a step targets a Legacy unit or a path inside a Legacy root |

Idempotency is asserted, not assumed: every step declares `idempotent: true`,
`rollback_dry_run(applied=...)` reports a second application as `changed:
false` with no pending steps, and `execute_plan(applied=...)` drives the
executor for zero steps on a repeat.

## Status on 2026-10-29

`PRE_FLIGHT`. No composed head, no 5/5 CI, no artifact, no Legacy snapshot, no
device grant, no canary. Every named head either has a failing shard or is
still running — see `evidence/CI-SNAPSHOT-2026-10-29.json`. Nothing was
installed, and a Draft PR existing is not release evidence.

## File ownership

This directory, `tests/test_fleet_next_v1_release_captain.py` and
`docs/integration/FLEET-NEXT-V1-RELEASE-CAPTAIN-20261029.md` belong to the
release-captain lane. It reads `fleet_mcp/**` as an input and modifies none of
it; it does not touch `runtime/**`, any other lane's tests, or any service
configuration.
