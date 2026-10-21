# S1 write-authority adaptation — proposal only

**Status: `PROPOSAL_ONLY`.** No S1 file is edited by this branch, no adapter is
implemented, and nothing below is claimed to run. This page exists so the F1
surface and the S1 lane can agree on a seam instead of one lane growing a second
copy of the other's authority.

## What each side owns today

| | authority | evidence |
|---|---|---|
| S1 | writer lease / epoch, send admission, dispatch attempt and effect reconciliation (`#237` T1–T12) | `runtime/coordination.py`, Work Engine — not touched here |
| F1 | Fleet scope + local binding floors for `fleet_send_to_session`, `fleet_create_session`, `fleet_interrupt_session` and their mirrored verbs | `fleet_mcp/fleet.py` `_may_send` / `_may_create` / `_may_control` |

The two answer different questions. F1 answers "may this credential address this
session on this device, under this binding?" S1 answers "is this writer still the
current writer, and has this attempt already produced an effect?" Neither answer
implies the other, and folding them would make "reachable and permitted, but
stale writer" unexpressible.

## The proposed seam

One advisory call at the point where F1 is about to cause an external effect —
before `provider.send(...)` and before the mirrored `native_call("session_send",
...)` forwards — with an S1-owned interface shaped like:

```text
admit_write(principal, target, idempotency_key) -> Admitted | Refused(reason)
```

* `target` is the same identity F1 already resolved (`session_ref`, or
  `device + runtime + project` for a create). F1 does not re-derive it.
* `idempotency_key` is F1's existing key; S1 decides whether it names a new
  attempt or a replay. F1 does not keep a second attempt store.
* On `Refused`, F1 returns the typed refusal and **does not call the provider**.
  On an unavailable or ambiguous S1 answer for an external write, F1 fails
  closed: "unknown" is not "go ahead".
* Reads are out of scope: S1's authority is about effects, and a read has none.

## What this proposal deliberately does not do

* It does not import S1 code into `fleet_mcp`, and it does not copy lease,
  epoch or attempt state into the Fleet binding. S1 remains the only store.
* It does not touch `runtime/coordination.py`, `fleet/agent_control_router.py`,
  Work Engine or the Agent Control surface.
* It does not claim the current conformance fixture exercises a lease. The
  fake device has no S1 authority; a future conformance case would inject a
  fake `admit_write` and assert two things: an admitted call reaches
  `device.calls`, and a refused call leaves `device.calls == []`.
* It does not decide the transport between F1 and S1 (in-process call, Unix
  socket, or MCP). That is S1's boundary to name; F1 only needs a callable that
  cannot be silently omitted.

## Why this belongs in the conformance slice

The authorization matrix already distinguishes discovery, scope, operator grant
and per-session floor. A lease/epoch decision is a fifth axis, and a release
receipt that does not name it would read as if the four were the whole chain.
Naming the seam here keeps that fifth axis visible without pretending it is
wired.
