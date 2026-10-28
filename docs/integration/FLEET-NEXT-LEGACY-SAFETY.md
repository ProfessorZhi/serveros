# Interop acceptance report — Fleet Next beside Legacy (#478)

Date: 2026-10-28. Track: `feat/fleet-next-legacy-safety-20261028`, Draft PR
against `main`. Files added, nothing modified:

*   `integrations/fleet-next-legacy-safety/legacy_safety.py`
*   `integrations/fleet-next-legacy-safety/README.md`
*   `tests/test_fleet_next_legacy_survival.py`
*   `docs/integration/FLEET-NEXT-LEGACY-SAFETY.md` (this file)

## The premise this round works from

The Owner's decision is recorded, not inferred: **the old Fleet keeps serving
ChatGPT with all of its existing device, session, agent and run control. Fleet
Next is a parallel new system, not a replacement and not a retirement of the
old one.** Three of the old Fleet's primary routes are reachable at 0.1.0 today.

This round therefore implements the *forbidden* rather than the *possible*: the
offline arithmetic that detects a shared resource between the two tracks and
refuses before anything is installed.

## Provenance, stated plainly

A previous attempt attributed to this area (`Run bbc9e0ac`, Tokyo) reported
`controller-completed` inside 0.1 s with no Activity and no PR. That is
`EXECUTION_UNVERIFIED`: nothing in it is usable as evidence of work done.
**This round is not a resend to that Run.** It is an independent implementation
in a fresh isolated worktree, on new paths, with its own RED→GREEN history.

## File ownership

| path | owner |
|---|---|
| `integrations/fleet-next-legacy-safety/**` | this round |
| `tests/test_fleet_next_legacy_survival*.py` | this round |
| `docs/integration/FLEET-NEXT-LEGACY-SAFETY*.md` | this round |
| `integrations/fleet-next-parallel-plugin/**` (#480) | #480 — **not touched** |
| `integrations/fleet-next-coexistence/**` | Tokyo's own tree — **not written** |
| `fleet_mcp/**` (#407), legacy `mcp/` / `fleet/` / `runtime/`, services, plugins, ports, DB, permissions | **not touched** |

Scope check before writing: no path conflict exists. #480 (`3777d134`) owns a
*different* directory with an overlapping *capability*; the only shared thing
is vocabulary, and this module reuses `NEXT_SHADOW_WRITE_FORBIDDEN` verbatim
rather than coining a second spelling. `integrations/fleet-next-coexistence/**`
does not exist on `main`, so nothing was displaced.

## What is actually delivered

| rule | where | how it is proved |
|---|---|---|
| every shared resource is one `BLOCKED_LEGACY_COLLISION`, reported all at once, never renamed aside | `install_plan` | 8 collision resources; a case where all 8 fire at once must return all 8; a nested root collides while a prefix-sibling does not |
| versions and rollback are per track; `rollback(upgrade(p,v2,d2)) == p` | `upgrade` / `rollback` | the invariant is asserted directly; four refusal codes by name |
| a Next start is refused on Legacy identity drift; an unreachable Next is `UNKNOWN` | `startup_preflight` | drift reported field by field; `next_reachable=False` must not produce a refusal or a success |
| shadow reads granted facts and never writes | `shadow_call` | 9 write verbs refused with the gateway's own call log still showing only `install`; ungranted and unknown verbs refused before the call |
| an id belongs to `(track, instance, boot)`; a delivery id is not a run id | `index_facts` / `resolve` / `run_state` | `CROSS_TRACK`, `FOREIGN_INSTANCE`, `STALE_BOOT`, `ID_KIND_MISMATCH`, `NOT_FOUND`; a Next `COMPLETED` leaves the Legacy run `RUNNING` |
| Legacy keeps answering identically across install / fail / upgrade / rollback | `snapshot` / `compare_survival` / `run_lifecycle` | `FakeLegacyGateway` answers `get_session`, `get_run`, `send`; answers, handler object and identity must be unchanged; **plus a positive control that forces `DRIFTED`, and an assertion that the steps really ran** |

Test count: 70, all passing, single-process, no fixtures outside the file.

## Gate status, honestly

### Gate A — Legacy control reachable; its URLs, ids and digests unchanged; Legacy survives a Next test/install **failure**

*   **Rules for the failure half: CODE + UNIT.** The `fail` step is in the
    lifecycle the suite runs, and the survival comparison is asserted around it
    in both directions (positive control included).
*   **The "unchanged" half: NOT_RUN against a live deployment.** Nothing here
    compares a real Legacy digests-before/digests-after, because doing so needs
    the deployment this round is not authorised to touch.
*   Reverse failure examples: the fakes model Next failing *after* install and
    Next never being installed at all; in both, the Legacy assertions are
    identical. This is a fake-level guarantee, and is labelled as one.

### Gate B — Next as a separate artifact: reproducible build, install-to-temp, uninstall-to-temp, explicit port/path/namespace/profile/schema

*   **The separation half: CODE + UNIT.** Distinct plugin backend id, MCP
    endpoint, port, socket, install/state/lock root and unit are enforced as
    collisions when shared.
*   **Build/install/uninstall: NOT_IN_SCOPE for this round.** This module adds
    no build step and no installer; writing one here would have been a second,
    unagreed deployment mechanism.

### Gate C — Agent Monitor SOURCE / UNIT / HOSTED_CI two-process end-to-end

*   **NOT_RUN.** No process was started. The suite is single-process and calls
    nothing.

### Gate D — real parallel deployment

*   **NOT_AUTHORIZED, and not attempted.** No service, unit, port, socket,
    OAuth client, credential or Creator was touched. The only Next gateway in
    this round is a fake in the test file.

### Gate E — #425 / #429 / #434 preserve the Legacy Fleet in writing

*   **For this round: this report and the PR body state it explicitly.** What
    those PRs say is theirs to state; this branch neither writes to them nor
    speaks for them.

## What would move each gate

| gate | next step (needs authorisation this round does not have) |
|---|---|
| A | capture Legacy's endpoints/ids/digests before and after a Next install attempt on a real host; both must match |
| B | build Next into a temp root, install, uninstall, and diff the Legacy paths before and after |
| C | run the two-process end-to-end with Agent Monitor as the observer |
| D | an explicit deployment decision, separately authorised |

## Non-claims

*   This is **not** a deployment, an installer, a service change, a Creator
    update, an OAuth or credential change, a real IM or model call, or a merge
    to `main`.
*   A green suite here does **not** mean "coexistence is safe". It means the
    rules that decide safety are stated and tested, and it says which of them
    are only tested against fakes.
*   The static source checks (no Legacy imports, no filesystem/socket access)
    are supporting evidence, not proof: a string rule cannot show that the old
    entry points were not rewritten. The behavioural survival tests over the
    fakes are the proof this round can offer; the live proof is Gate A's
    NOT_RUN half.
