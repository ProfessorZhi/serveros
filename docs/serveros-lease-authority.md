# serverosd lease-authority adapter for Work Engine

**Status (this milestone):**

| Fact | State |
|---|---|
| Cross-repo adapter | **SOURCE / LOCAL / HERMETIC**, proven against serveros #272 `fc93590` (PR #272 tip, OPEN — not yet on `main`) |
| Production parent | **`BLOCKED_TRUST_SOURCE`** — no owner-approved Work Engine caller binds it |
| CLI / MCP lease write verb | none (deliberately) |
| Canonical resource | `work-write:<item_ref>` — asserted equal to serveros #272's `work_write_resource` |
| Evidence level | `REAL_SO_PEERCRED` + **synthetic context/grant** in a temp DB; not `REAL_DEVICE` |

This document is the exact wiring contract for the owner-approved parent. It
does not claim that a production parent exists.

## 1. What is now real

`workengine/serveros_authority.py` maps the serveros #272 read
`agent.lease.authority.verify` onto Work Engine's `LeaseGrant`:

* `UnixSocketVerifyClient` speaks the daemon's newline-JSON AF_UNIX protocol.
  The socket path is **required** and absolute; there is no default, no
  environment fallback and no production path compiled in.
* `ServerOSLeaseAuthority` binds one `grant_ref` at construction and calls the
  read with the exact `context_ref`, `action`, `project` and `item_ref`. It
  never accepts an owner, `os_user`, action list, epoch or token from its
  caller.
* The serverosd read resolves the context **out of band**, reads the kernel
  `SO_PEERCRED` peer, and re-checks `delegated_by`, project, Item, action,
  revocation, window and epoch against the live grant. Refusals are answers
  (`verified: False` + `reason` + `because`), never a permissive default.
* An **owner-backed** reply is a point-in-time read, not a write grant.
  `ServerOSLeaseAuthority` refuses it unless the reply carries
  `atomicity: "COMMIT_FENCED"`. The current daemon does not emit that marker;
  when every owner check passes it answers
  `verified: False, reason: BLOCKED_OWNER_COMMIT_SEAM` (with an informational
  `point_in_time` audit and no `grant`), so no owner-backed lease is written.
  A synthetic signature or a legacy #272 readback is not a commit fence.

The Work Engine side then re-checks the mapped `LeaseGrant` in
`OwnerVerifiedLease`: exact Item/action, `revoked`, window, positive epoch, and
`os_principal == uid:<getuid()>`. A grant the serverosd read did not verify
never reaches the lease table.

## 2. Process-boundary rule (load-bearing)

`UnixSocketVerifyClient` must connect from the **same process** that calls
`OwnerVerifiedLease`. serverosd then sees that process's kernel uid via
`SO_PEERCRED`, and `OwnerVerifiedLease`'s `uid:<getuid()>` check binds the
returned grant back to it.

A proxy process, a helper daemon, or a "marshal the JSON" wrapper changes the
peer and must be refused. The adapter does not paper over this: if the peer's
uid does not match the Work Engine process, the result is
`OS_PRINCIPAL_MISMATCH` and no lease is written.

A `context_ref` is still a bearer-like reference. serverosd checks that the
grant's `delegated_by` is that context and that the context authorizes a
mutation right now. Choosing a context you were not delegated is
`UNTRUSTED_ACTOR`; a grant for another actor is not usable even if its ref is
presented.

## 3. Exact parent call site

```python
from workengine.authority import OwnerVerifiedLease
from workengine.serveros_authority import (
    ServerOSLeaseAuthority, UnixSocketVerifyClient,
)

lease = OwnerVerifiedLease(
    we_store,
    ServerOSLeaseAuthority(
        UnixSocketVerifyClient("/run/user/<uid>/serveros/serveros.sock"),
        grant_ref="grant:<owner-provisioned>",
    ),
)
lease.acquire(
    context_ref="<ServerOS-minted context_ref>",
    project="serveros",
    item_ref="SRV-0001",
    ttl_s=30,
)
```

The parent must:

1. Run in the same OS process/uid as the lease holder.
2. Pass the real serverosd socket path explicitly; do not read it from argv or
   the environment in a way that lets the caller choose its own authority.
3. Bind the `grant_ref` it provisioned; do not let the caller change it.
4. Ensure the live grant explicitly carries the exact actions it needs:
   `lease.acquire`, `lease.renew`, `lease.release`, `work.commit`. The adapter
   does not grant these and does not accept a caller-supplied action list.
5. Never backfill an epoch onto an old grant. A record without an epoch is
   `INVALID_CONTEXT` / UNKNOWN and authorizes nothing.
6. Never accept an `owner`, `os_user`, `token`, `actions` or `epoch` from the
   caller. The owner written into `leases` is the verified principal.

## 4. Refusal mapping

| Source | Reason code |
|---|---|
| serverosd: no/unresolvable context, wrong `delegated_by`, missing peer | `UNTRUSTED_ACTOR` |
| serverosd: missing grant, no epoch, expected-epoch mismatch | `INVALID_CONTEXT` |
| serverosd: revoked, expired, not-yet-valid, action/project/Item not granted | `POLICY_DENIED` |
| transport: missing socket, refused connection, timeout | `BLOCKED_TRUST_SOURCE` |
| Work Engine: owner-backed answer has no `atomicity: "COMMIT_FENCED"` | `BLOCKED_OWNER_COMMIT_SEAM` |
| Work Engine: verified answer carries no grant | `UNTRUSTED_ACTOR` |
| Work Engine: verified grant is not the bound grant | `INVALID_CONTEXT` |
| Work Engine: peer uid != this process | `OS_PRINCIPAL_MISMATCH` |
| Work Engine: observed epoch moved | `EPOCH_STALE` |
| Work Engine: another live holder / stale token / expired lease | `LEASE_HELD_BY_OTHER` / `STALE_TOKEN` / `LEASE_EXPIRED` |

Every refusal leaves `leases` unchanged.

## 5. Evidence

Run from the repository root:

```text
python -m pytest tests/test_serveros_lease_authority.py tests/test_lease_authority.py -q
```

Result: **50 passed**, no skips (22 adapter cases: 18 ported + 4 hardening, plus
28 authority cases). The owner commit-fence chain is covered separately by
`tests/test_owner_commit_fence.py` (3 passed on this branch): a temp daemon,
real AF_UNIX/`SO_PEERCRED`, the adapter, `OwnerVerifiedLease` and a temp lease
store, with an owner revoke after the last read and before `acquire`. The
test materializes the actual serveros #272 source (PR #272 tip
`fc93590030c218934252920a53aa67277f144844`, an OPEN pull request not yet on
`main`) from THIS repository's object store into a temp tree, builds a temp
daemon/state DB and a temp pathname AF_UNIX socket, and connects with the real
`SO_PEERCRED` path. The context record and grant are test-minted. Foreign-uid
and missing-peer cases use the daemon's `handle(peer=...)` seam and are labelled
as synthetic.

When that object is not present, the adapter class skips with a precise reason;
a CI run without the #272 fetch is not silently counted as a pass.
`SERVEROS_LEASE_AUTHORITY_REF` may pin a later fetched ref.

It is not `REAL_DEVICE`, not a production daemon, and not an independent QA
acceptance.

## 6. External approval / wiring blocker

The owner-backed positive is additionally `BLOCKED_OWNER_ATOMIC_AUTHORITY`:
there is no owner-issued policy/epoch commit store and no lease commit in the
same serializable transaction as the epoch check. See
`docs/s1-owner-atomic-commit-contract.md` and the runnable
`tests/probes/s1_owner_atomic_commit_probe.py`.

Production remains `BLOCKED_TRUST_SOURCE` because:

* No owner-approved Work Engine caller constructs this adapter. The CLI and MCP
  expose no lease write verb, and this milestone adds none.
* The real serverosd socket path, the provisioned `grant_ref`, the
  `context_ref`, and the OS process/uid are owner-provisioned facts, not
  something this repository can invent.
* The parent must be the same process as the lease holder; a cross-process
  proxy is refused by the uid check.
* serveros #272 is an OPEN pull request, not yet on `main`, and is `LOCAL`
  (tests use `handle(peer=...)`); it is not a production deployment. Binding
  this adapter to a real daemon is a separate, owner-approved wiring step, and
  #292 must not be merged ahead of #272 without re-checking the base.

Until those are supplied, the honest state is **BLOCKED_TRUST_SOURCE**, not a
working production lease producer.

## 7. Non-claims

* No production daemon, production socket, network, credentials, OAuth or
  secret was used.
* No CLI/MCP write verb was added; no caller can choose its own owner.
* The adapter does not grant actions, mint an epoch, backfill an old grant, or
  create a second lease/evidence store.
* `work-write:<Item>` is one namespace on both sides; there is no fallback to
  the naked Item name.
* Synthetic test context + real kernel peer is not the same as real OS
  attestation of a real user session.
