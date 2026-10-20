# S1 · Owner policy: kernel uid → audited actor → exact scope (#237 B3)

**Status: typed fail-closed adapter. Production remains
`BLOCKED_OWNER_POLICY`; no owner-issued uid mapping is invented.**

| Fact | State |
|---|---|
| Grant epoch + kernel-peer `os_principal` | existing #272 code, unchanged |
| Owner policy | external owner-issued fact; a resolver injected at daemon construction |
| Production `serverosd` | passes no resolver → every issue/verify refuses `BLOCKED_OWNER_POLICY` |
| Test evidence | `REAL_TEMP` (real AF_UNIX + real `SO_PEERCRED`), `LOCAL` (synthetic peer negatives) |
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

## 4. Refusal matrix

| Case | Result |
|---|---|
| production daemon (no resolver) | `BLOCKED_OWNER_POLICY` |
| grant has no owner binding | `BLOCKED_OWNER_POLICY` |
| resolver returns `None`/raises | `BLOCKED_OWNER_POLICY` |
| policy revoked | `POLICY_DENIED` |
| policy expired / not yet valid | `POLICY_DENIED` |
| policy epoch differs from the grant binding | `BLOCKED_OWNER_POLICY` |
| policy uid ≠ kernel peer ≠ grant uid | `UNTRUSTED_ACTOR` |
| policy actor ≠ context actor ≠ grant actor | `UNTRUSTED_ACTOR` |
| grant/request scope outside the policy | `POLICY_DENIED` |
| context not FRESH + FACT + VERIFIED | existing #318 `UNTRUSTED_ACTOR` |
| no kernel peer | `UNTRUSTED_ACTOR` |

## 5. Evidence

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

### GREEN on this branch

```text
15 passed, 1 skipped   tests/test_owner_policy_binding.py
  (the skip is BLOCKED_MULTIPRINCIPAL_NOT_RUN)
2 passed               tests/test_owner_policy_reduced_gate.py
63 passed, 1 skipped   owner-policy + reduced gate + #272 grant suites +
                       agent_control_store
26 passed              test_v1_grant_epoch_authority.py +
                       test_v1_grant_os_principal_schema.py
43 passed, 23 subtests test_fleet_owner_grant.py +
                       test_fleet_owner_grant_template.py
30 passed              lease gate/producer + daemon method-coverage selection
72 passed, 177 subtests test_agent_mcp.py
```

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
* The `LeaseGrant` window/uid/epoch checks are #272's; this slice adds the
  owner binding, not a second grant source.
* No real provider, model request, credential or paid send; no merge,
  deployment or live Daemon/Fleet/Grant mutation.
