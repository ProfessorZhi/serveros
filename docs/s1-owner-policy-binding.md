# S1 · Owner policy: kernel uid → audited actor → exact scope (#237 B3)

**Status: typed fail-closed adapter. Production remains
`BLOCKED_OWNER_POLICY`; no owner-issued uid mapping is invented.**

| Fact | State |
|---|---|
| Grant epoch + kernel-peer `os_principal` | existing #272 code, unchanged |
| Owner policy | external owner-issued fact; a resolver injected at daemon construction |
| Production `serverosd` | passes no resolver → every issue/verify refuses `BLOCKED_OWNER_POLICY` |
| Test evidence | `REAL_TEMP` (real AF_UNIX + real `SO_PEERCRED`), `LOCAL` (synthetic peer negatives) |
| QA2 #360 S1 findings | independently reproduced 6/6 RED at `#358` `b183aab`; 6/6 `ok` on this branch |
| Owner-backed mutation eligibility | `verified: false`, `BLOCKED_OWNER_COMMIT_SEAM`; no owner commit fence exists |
| WorkEngine adapter | refuses owner-backed replies unless `atomicity == "COMMIT_FENCED"`; none emitted |
| Two real uids | `BLOCKED_MULTIPRINCIPAL_NOT_RUN` (no passwordless sudo/setuid) |
| New registry / grant store | **none** |

## 1. The remaining unknown

#272 proves a grant is live, has a store-minted epoch, is bound to the kernel
peer uid of its issuing call, and is scoped. It does not prove the **owner**
authorized that uid to act as that principal for that project/Item/actions. A
provider-signed delegation names a human; the issuing process names a uid. That
bridge is the missing `#237` B3 fact, and it cannot be inferred from a uid, a
bearer context, or a caller field.

## 2. The contract (not a registry)

`runtime/owner_policy.py` defines an `OwnerPolicy` with:

* `os_principal` — the authorized kernel uid (`uid:<n>`);
* `actor_ref` — the audited principal that uid may act as;
* `projects`, `items`, `actions` — the exact scope;
* `epoch` — the owner-policy generation;
* `valid_from`, `expires_at` — zoned instants;
* `revoked`;
* `issued_by`, `issued_at` — the owner audit trail.

An `OwnerPolicyResolver` is a **read**: `resolve(...)` returns the live policy
or `None`; an exception is a refusal. The resolver is injected at daemon
construction. `serverosd` binds none, so production is fail-closed. Nothing
here persists a policy, mints a policy, or backfills a uid.

## 3. Where the binding is written and checked

### Grant issue (the mutating boundary)

`Daemon(owner_policy_resolver=None)`:

* no resolver → `agent.grant.issue` returns
  `{"issued": false, "reason": "BLOCKED_OWNER_POLICY"}` and writes nothing;
* with a resolver, `AgentControlStore.issue_grant(...,
  owner_policy_resolver=...)` resolves the live policy inside the same store
  mutation, after the provider-signed delegation is verified. It requires the
  issuing kernel peer uid, the verified context's actor, the whole approved
  scope, the policy window/epoch/revocation and a non-empty audited issuer.
  Only then does it store:
  `owner_policy_ref`, `owner_policy_epoch`, `owner_policy_issued_by`,
  `owner_policy_issued_at`, `actor_ref` (and the existing `os_principal`,
  `epoch`).

`put_grant` refuses a caller-supplied owner binding (`owner_policy_ref`,
`owner_policy_epoch`, `owner_policy_issued_by`, `owner_policy_issued_at`,
`actor_ref`), for the same reason it refuses `os_principal`: a binding a caller
could choose is one the caller could claim for somebody else.

### Verify (the read Work Engine consumes before commit)

`agent.lease.authority.verify` runs the existing #272 checks first
(context `authorizes`, delegated_by, revoked, window, grant epoch,
kernel-peer `os_principal`, scope). If they pass, it:

1. refuses `BLOCKED_OWNER_POLICY` if the daemon has no resolver;
2. refuses `BLOCKED_OWNER_POLICY` if the grant carries no owner binding;
3. resolves the live policy and checks: same `policy_ref`, same
   `owner_policy_epoch` (stale refuses), not revoked, live window, kernel peer
   uid == policy uid == grant uid, context actor == policy actor == grant actor,
   the grant scope and the requested project/Item/action inside the policy;
4. returns the 10-field `LeaseGrant` **plus** a top-level `owner_policy` audit
   (`policy_ref`, `policy_epoch`, `issued_by`, `issued_at`, `actor_ref`,
   `os_principal`).

The 10-field `grant` is unchanged, so the Work Engine `LeaseGrant` consumer
still constructs it. The Work Engine adapter itself is not modified.

## 3.5 QA2 #360 hardening (stacked branch)

QA2's independent S1 probe was run against `#358` head `b183aab` before any
change: **6/6 findings RED**. Each finding and the fix in this branch:

| QA2 finding | Pre-fix RED behaviour | Fix |
|---|---|---|
| P1 `revoked=1` | `revoked is True` is false → treated as unrevoked | `revoked` must be a `bool`; any other value is `BLOCKED_OWNER_POLICY` (no truthiness, no normalization) |
| P1 `revoked="yes"` | same | same |
| P2 `issued_at=2999` | a policy issued in the future authorized | `issued_at > now` → `BLOCKED_OWNER_POLICY`; `issued_at > expires_at` also refuses |
| P1 TOCTOU | revoke between `resolve` and `return` still returned `VERIFIED` | a second `verify_live_grant` over the same ledger plus a second `resolver.resolve`; any change is `BLOCKED_ATOMICITY`. The positive answer labels `atomicity: BLOCKED_OWNER_COMMIT_SEAM` and `point_in_time: true` — the post-return seam is stated, not claimed atomic |
| P2 `approved` owner fields | `issue_grant` stored caller-supplied owner fields | owner-written fields are stripped from `approved` before storage; the refusal is recorded as `issuance.refused_owner_fields` (non-owner fields are untouched) |
| P2 idempotent readback | equal evidence returned the old grant before any policy read | when a resolver exists, the existing grant is re-checked against the live policy over its own grant/context/peer; a revoked/expired/stale policy raises `StoreError` (`POLICY_DENIED`/`BLOCKED_OWNER_POLICY`), never a second grant |

The TOCTOU fix does **not** invent an atomic owner-authority commit. The
existing grant ledger has no owner-authority epoch/snapshot seam, so the code
re-reads the same ledger and refuses on change; the residual window after the
answer is returned is explicitly labelled `BLOCKED_OWNER_COMMIT_SEAM` and the
answer is never promoted to `VERIFIED` for an atomicity claim it cannot make.
A true atomic commit requires the upstream owner issuance interface; that
remains a protocol requirement (section 6).

## 3.6 The mutation boundary: no commit fence, no positive (stacked branch)

The cross-module review at #363 `8840f41` found that the point-in-time answer
was still consumed as a write grant: `workengine/serveros_authority.py` read
only `verified: true`, built a `LeaseGrant`, and
`OwnerVerifiedLease.acquire` wrote a lease. A revoke after the daemon's last
resolver read but before that write was invisible.

Fix, in two places:

1. `agent.lease.authority.verify` with an owner resolver **never** returns
   `verified: true`. When every owner check passes it returns
   `verified: false, reason: BLOCKED_OWNER_COMMIT_SEAM`; `point_in_time: true`
   and the `owner_policy` audit remain as a read-only record, but no `grant`
   is returned. The daemon has no owner-issued atomic policy/epoch commit
   fence, so it cannot authorize a mutation.
2. `ServerOSLeaseAuthority.verify` explicitly refuses any owner-backed reply
   (`owner_policy`, `point_in_time` or `atomicity` present) whose
   `atomicity` is not exactly `COMMIT_FENCED`. A refusal marker cannot be
   swallowed as decoration. No production code emits `COMMIT_FENCED`; a
   synthetic HMAC or a legacy #272 readback is not such a fence.

Legacy #272 reads (no owner markers) keep their previous behavior. The
Work Engine adapter's fence check is the last gate before
`OwnerVerifiedLease.acquire` writes.

### RED → GREEN on the stacked branch

The RED test materializes the real chain — temp Daemon + temp AF_UNIX socket +
`SO_PEERCRED`, `UnixSocketVerifyClient`, `ServerOSLeaseAuthority`,
`OwnerVerifiedLease`, temp `ServerOSStore` — and revokes the owner policy in
the client after the daemon's last read and before `acquire`:

```text
RED at 8840f41:
  test_the_owner_route_never_returns_verified_without_a_commit_fence  FAILED
  test_a_revoke_after_the_last_read_cannot_acquire_a_lease           FAILED DID NOT RAISE
  test_the_adapter_refuses_an_owner_backed_positive_without_commit_fence FAILED DID NOT RAISE
  3 failed

GREEN on this branch:
  3 passed   tests/test_owner_commit_fence.py
```

The rewritten positives now assert the safe expectation: a live owner policy
yields a `BLOCKED_OWNER_COMMIT_SEAM` refusal with a point-in-time audit and
**no** grant; #272 DTO/epoch/uid positives call `verify_live_grant` at its own
boundary. The two QA2 probe controls that expected an owner-backed
`verified: true` (`valid-policy-10-field-dto`, `caller-actor-ref-ignored`) are
superseded by this requirement; all six findings and the other 34 controls
still hold.

## 4. Refusal matrix

| Case | Result |
|---|---|
| production daemon (no resolver) | `BLOCKED_OWNER_POLICY` |
| grant has no owner binding | `BLOCKED_OWNER_POLICY` |
| resolver returns `None`/raises | `BLOCKED_OWNER_POLICY` |
| `revoked` is not a `bool` (`1`, `"yes"`, `None`) | `BLOCKED_OWNER_POLICY` |
| policy issued in the future / after its own expiry | `BLOCKED_OWNER_POLICY` |
| grant or policy changed between the two reads | `BLOCKED_ATOMICITY` |
| owner checks pass but no owner commit fence exists | `BLOCKED_OWNER_COMMIT_SEAM` (`verified: false`, no grant) |
| WorkEngine adapter sees an owner-backed reply with `atomicity != COMMIT_FENCED` | `AuthorityError` `BLOCKED_OWNER_COMMIT_SEAM` |
| idempotent issue readback with a dead live policy | `StoreError` `POLICY_DENIED` / `BLOCKED_OWNER_POLICY` |
| policy revoked | `POLICY_DENIED` |
| policy expired / not yet valid | `POLICY_DENIED` |
| policy epoch differs from the grant binding | `BLOCKED_OWNER_POLICY` |
| policy uid ≠ kernel peer ≠ grant uid | `UNTRUSTED_ACTOR` |
| policy actor ≠ context actor ≠ grant actor | `UNTRUSTED_ACTOR` |
| grant/request scope outside the policy | `POLICY_DENIED` |
| context not FRESH + FACT + VERIFIED | existing #318 `UNTRUSTED_ACTOR` |
| no kernel peer | `UNTRUSTED_ACTOR` |

## 5. Evidence

### QA2 S1 findings at `#358` head `b183aab` (independent reproduction)

QA2's runner was extracted from PR #360 head into a read-only temp checkout and
run against the `#358` worktree (no mutation of `#358`):

```text
integrations/fleet-v1-qa/qa2/qa2-run.sh s1
  author       86 passed, 1 skipped, 23 subtests   rc=0
  controls     36 passed                           rc=0
  s1-signed    BLOCKED_CRYPTOGRAPHY (venv has no cryptography; not a pass)
  s1-findings  6/6 confirmed, rc=1
```

The six RED names: `f1-revoked-int`, `f1-revoked-str`,
`f2-policy-issued-in-future`, `f3-revoke-after-verify-before-return`,
`f4-issue-without-resolver-stores-approved-owner-fields`,
`f5-idempotent-issue-readback-skips-revoked-policy`.

### RED at the pre-fix base `8620a90`

`tests/test_owner_policy_reduced_gate.py` is import-minimal and runs against
both trees. On the base, a live grant with **no** owner binding verified:

```text
FAILED test_the_verify_route_refuses_without_an_owner_resolver
  assert True is False  (the pre-fix base admitted a live grant with no
                         owner-to-uid binding)
FAILED test_the_issue_route_refuses_without_an_owner_resolver
  result == {}          (the pre-fix base reached the grant store with no
                         owner policy)
2 failed
```

### GREEN on this branch (haircut `#358` + hardening)

QA2's own probe, run against this branch's worktree:

```text
s1_probe.py --mode findings  {"failed": 0, "total": 6}
  f1-int/f1-str → BLOCKED_OWNER_POLICY
  f2 future issued_at → BLOCKED_OWNER_POLICY
  f3 revoke during resolve → BLOCKED_ATOMICITY (stored_revoked: true)
  f4 stored owner ref null, verified false
  f5 second `issued: false`
s1_probe.py --mode controls  {"failed": 0, "total": 36}
```

The author and focused regression suites on this branch:

```text
114 passed, 1 skipped, 23 subtests
  test_owner_policy_binding.py + test_owner_policy_reduced_gate.py +
  test_v1_grant_epoch_authority.py + test_v1_grant_os_principal_schema.py +
  test_fleet_owner_grant.py + test_fleet_owner_grant_template.py +
  test_agent_control_store.py
  (the skip is BLOCKED_MULTIPRINCIPAL_NOT_RUN)
86 passed, 8 subtests
  test_lease_authority.py + test_serveros_lease_authority.py +
  test_v1_lease_authority_gate.py + test_handoff_daemon_bindings.py +
  test_grant_window_authority.py
72 passed, 177 subtests  test_agent_mcp.py
```

All were run with `MemoryMax=1G`, `MemorySwapMax=0`, `CPUQuota=25%`,
`TasksMax=64`, `nice -n 10`, an inner timeout and `-p no:cacheprovider`; no
full-suite pytest was run on the shared host.

The real-socket positive is `REAL_TEMP`: a temp AF_UNIX socket, the kernel's
real `SO_PEERCRED` uid, and production daemon/store code. The context, grant
and owner policy are **test fixtures**; there is no real provider signature and
no production owner. The synthetic-peer negative is `LOCAL` and labelled
`SYNTHETIC_PEER`, not OS evidence.

`test_durable_delegation.py` could not be collected in this venv because
`cryptography` is absent; it is an environment limitation, not a pass.

## 6. Blocked / not claimed

* **Production owner policy is not implemented.** The owner-issued
  uid→actor→scope source and its manual issuance are absent; production
  `serverosd` binds no resolver and therefore returns `BLOCKED_OWNER_POLICY`.
* **Two real uids are `BLOCKED_MULTIPRINCIPAL_NOT_RUN`.** The test process is
  uid 1002 with no passwordless sudo/setuid; synthetic peer metadata is only a
  LOCAL negative, never REAL_OS.
* **Atomic owner-authority commit is `BLOCKED_OWNER_COMMIT_SEAM`.** The
  existing ledger has no owner-authority snapshot/epoch seam, so the verify
  path re-reads and refuses on change but cannot make the resolve→return window
  atomic. A protocol requirement remains: an upstream owner issuance interface
  that commits an authority snapshot/generation the verify path can pin in one
  read. Until then the daemon returns `verified: false` with that reason and a
  point-in-time audit only; the WorkEngine adapter requires the exact
  `COMMIT_FENCED` marker for any owner-backed reply, and no production code
  emits it. No synthetic atomicity is promoted to production.
* The `LeaseGrant` window/uid/epoch checks are #272's; this slice adds the
  owner binding, not a second grant source.
* No real provider, model request, credential or paid send; no merge,
  deployment or live Daemon/Fleet/Grant mutation.
