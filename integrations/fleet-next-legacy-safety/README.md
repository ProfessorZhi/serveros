# #478 · Legacy safety: what it takes for a second Fleet to stand beside the first

`legacy_safety.py` is a pure decision surface. It answers one question in
several parts: **may Fleet Next be installed next to the old Fleet, without the
old Fleet — or anything ChatGPT already drives through it — changing?**

It calls nothing. No service, no port, no socket, no credential, no file, no
network. Every function takes values a caller already holds and returns a
decision with the reason attached, so a green test run is a statement about the
rules rather than about one machine.

The premise is the Owner's, and it is not a migration: the old Fleet keeps
serving every device, session, agent and run control it serves today. Next is a
**parallel** release. Anything that makes the two indistinguishable — a shared
port, a reused plugin backend id, one state directory, one lock, one unit name,
an id read across tracks — is a takeover wearing the word "coexistence".

## One placement per track

A `Placement` is everything that makes a track addressable and identifiable:

| field | why it must differ |
|---|---|
| `backend_plugin_id` | ChatGPT picks a connector by id; two tracks under one id is one connector |
| `mcp_url` | the endpoint an MCP client is configured with |
| `listen_port` | who answers a connection |
| `socket_path` | the same, for a unix socket |
| `install_root` | code |
| `state_root` | the run/session record |
| `lock_root` | the mutual exclusion — two tracks sharing it serialise against each other |
| `unit` | the supervisor's handle; one unit name cannot describe two services |

`install_plan(legacy=..., next=...)` reports **every** shared resource at once as
`BLOCKED_LEGACY_COLLISION`. Reporting only the first would send an operator
back once per field.

Three properties of that function matter more than its list:

*   **There are two answers.** `OK` or `BLOCKED`. There is no branch that
    returns a corrected placement, renames a directory aside, or picks another
    port: a silently adjusted placement is a takeover with extra steps.
    `plan.legacy` and `plan.next` echo the inputs unchanged, by identity.
*   **Overlap is checked with a slash boundary, in both directions.** A Next
    root nested inside the Legacy root is a collision (`/opt/fleet/next` inside
    `/opt/fleet`); a sibling with a matching prefix is not (`/opt/fleet-next`
    is not inside `/opt/fleet`). Getting this wrong in the permissive direction
    is how one tree becomes two installations.
*   **A placement is validated before anything is compared.** Empty roots,
    relative paths, a non-`sha256:` artifact digest, a swapped track — all are
    `BLOCKED_PLACEMENT_INCOMPLETE`. A relative root is refused rather than
    resolved, because resolving it against the caller's working directory is
    exactly how two tracks end up in one directory.

Two tracks may legitimately carry the **same version number**. They version
independently; equal numbers are not evidence of a shared artifact.

## Version and rollback, per track

`upgrade(placement, version=..., artifact_digest=...)` records the placement it
replaced, and `rollback(...)` restores it. The invariant is stated as an
assertion in the tests:

```
rollback(upgrade(p, v2, d2)).placement == p
```

Consequences that are refused by name:

| attempt | code |
|---|---|
| roll back a track that recorded no target | `BLOCKED_PLACEMENT_INCOMPLETE` |
| roll back to a version whose artifact cannot be pinned | `ROLLBACK_WITHOUT_DIGEST` |
| roll back or upgrade the **Legacy** track from here | `ROLLBACK_CROSSES_TRACKS` |
| "upgrade" to the same or an older version | `VERSION_NOT_ADVANCING` |

`ROLLBACK_CROSSES_TRACKS` is the important one: this module has no operation
that changes the old Fleet. Rolling Next back is never a way to reach it.

An `upgrade`/`rollback` answer is a `Lifecycle`: on a refusal its `placement`
is the input, unchanged, so a refused step cannot be mistaken for an applied
one.

## Startup preflight

`startup_preflight(recorded, observed, plan, next_reachable=...)` decides in this
order:

1. **the plan** — a colliding plan is `BLOCKED_LEGACY_COLLISION`, immediately,
   before anything built on a stale baseline is reasoned about further;
2. **Legacy's identity** — if `observed` is not `recorded` field for field
   (`track`, `fleet_instance_id`, `route_id`, `boot_id`, `schema_version`,
   `source_version`), it is `LEGACY_IDENTITY_DRIFT`, with the drifted fields
   named.

`next_reachable` produces **no refusal**. An unreachable Next is
`next_facts == "UNKNOWN"`: it says nothing about Legacy, and it is not a
success either. That asymmetry is the point — the failure of the new system
must never be recorded as a fact about the old one.

`legacy_usable` is **not a health check of Legacy**. It says whether Legacy's
observed identity still matches the baseline the plan was built against, and
therefore whether a start may proceed.

## Shadow phase: read facts, never write

`shadow_call(verb, granted=..., gateway=...)` refuses in this order, and the
fakes prove "refused before the call" by their own call log rather than by
assertion:

| condition | code | gateway called |
|---|---|---|
| the verb is a write (`create`, `send`, `stop`, `restart`, `wake`, `session_create`, `session_send`, `session_interrupt`, …) | `NEXT_SHADOW_WRITE_FORBIDDEN` | **no** |
| the verb is not a read-only fact verb at all | `SHADOW_VERB_UNKNOWN` | no |
| the verb is read-only but was not granted | `SHADOW_NOT_GRANTED` | no |
| granted, read-only, and the gateway fails | `SHADOW_FACT_UNKNOWN`, state `UNKNOWN` | yes |
| granted, read-only, and the gateway answers | `OK`, state `OBSERVED` | yes |

`NEXT_SHADOW_WRITE_FORBIDDEN` is spelled exactly as #480's parallel-plugin
preflight spells it. One refusal with two spellings is how a second vocabulary
starts.

The grant is a **ceiling, not a census**: a verb being grantable says nothing
about which facts exist. `WRITE_VERBS ∩ READ_ONLY_FACT_VERBS == ∅` is asserted,
so no verb can be both.

A fact carries the identity of the gateway that produced it — the test uses a
non-default `boot_id` precisely so a constant cannot stand in for it.

## An id belongs to one track, one instance, one boot

Every run, delivery and session id is scoped to
`(track, fleet_instance_id, boot_id, kind, fact_id)`. `resolve(...)` checks the
scope **before** looking the id up:

| situation | code |
|---|---|
| the reference is for the other track | `CROSS_TRACK` |
| same track, different `fleet_instance_id` | `FOREIGN_INSTANCE` |
| same instance, different `boot_id` | `STALE_BOOT` |
| the id exists under a different kind in this scope | `ID_KIND_MISMATCH` |
| nothing in this scope | `NOT_FOUND` |
| exact scope | `HIT` |

`run_state(facts, ref)` reads only inside the reference's own scope, so the two
claims the Owner forbids by name are unrepresentable:

*   a `COMPLETED` on Next does not complete a Legacy run — the Legacy ref
    resolves to Legacy's own `RUNNING`;
*   a run that only Next knows is `NOT_FOUND` on Legacy, not `COMPLETED`.

A fact whose payload carries no status is `UNKNOWN`, never `FAILED` and never a
default success.

`same_object(a, b)` is true only when every scope field agrees. The same
spelling in two scopes is two objects.

## Survival: the part strings cannot prove

The rest of the file is rules; this part is evidence.

`snapshot(gateway, verbs)` asks every verb and records a **deep, frozen** copy
of the answer. `compare_survival(before, after)` compares verb by verb and
returns `SAME` or `DRIFTED` with the verbs that moved.
`run_lifecycle(gateway, verbs, steps)` wraps each Next lifecycle step
(install, fail, upgrade, roll back) in a before/after snapshot of the **Legacy**
gateway.

The test file's `FakeLegacyGateway` serves `get_session`, `get_run` and `send`
from a fixed table and keeps its handler as a plain attribute, so the suite can
assert that the *same function object* is still installed afterwards. What is
asserted across the whole lifecycle:

*   every Legacy answer is byte-identical before and after each step;
*   `get_session`, `get_run` and an **authorised** `send` still work;
*   an **unauthorised** `send` is still refused — Next did not widen Legacy's
    permissions either;
*   the handler object and the identity object are untouched;
*   the harness never asks Legacy for anything outside the read verbs.

Two guards keep this from being a ceremony:

*   **a positive control** — `test_the_survival_harness_detects_a_real_drift`
    perturbs the fake and requires `DRIFTED`, so `SAME` everywhere else means
    "nothing moved", not "the comparison cannot fail";
*   **the steps really run** — the fakes report their own log, and the suite
    asserts install / fail / upgrade / rollback actually happened and that a
    rollback really returned to `0.2.0`. A no-op step would otherwise make every
    `SAME` vacuous.

The static checks in the test file (no Legacy imports, no filesystem or socket
access, no installation paths in the source) are **supporting evidence only**:
a string rule cannot prove the old entry points were not rewritten. That claim
rests on the behavioural tests above, over fakes, and — beyond this module — on
a real deployment that is not authorised in this round.

## Status

| item | state |
|---|---|
| placement guard, per-track version/rollback, startup preflight, shadow refusal, id scoping, survival harness (pure) | **CODE + UNIT** — `tests/test_fleet_next_legacy_survival.py` |
| real parallel deployment beside a live Legacy Fleet | **NOT_AUTHORIZED** — and **NOT_RUN** |
| real device / client end-to-end | **NOT_RUN** — this directory intentionally calls nothing |
| reproducible build + install-to-temp / uninstall-to-temp of the Next artifact | **NOT_IN_SCOPE** here (Gate B's build half); the placement half is covered above |
| CI | **PENDING** until the PR's own runs report |

This directory adds rules; it changes no existing file. The Legacy Fleet's
gateway, plugin connection, identity, run store, ports, sessions and
permissions are untouched by it, and nothing here is a deployment command.
