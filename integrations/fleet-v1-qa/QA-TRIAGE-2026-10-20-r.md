# QA-TRIAGE-2026-10-20-r — Q1 independent security QA for #250 / #256

**Status: `QA_PARTIAL` / `NOT_RELEASE`. No QA signature. No PASS on V1 as a whole.**

Lane: `Q1-independent-security-qa`, campaign #244, parent #250, QA register #256.
Independent Session; no product source edited. All writes are additive under
`integrations/fleet-v1-qa/**` plus a probe/evidence directory outside the repo.
No merge, no force-push, no worktree deletion, no service restart, no provider
or Feishu request.

Snapshot: **2026-10-20T05:06Z (UTC)**. Branch heads were re-read from GitHub
immediately before this run; PR #241 and #272 had moved since the previous QA
snapshot (see the pinned table), so no earlier SHA is reused here.

---

## 1. Pinned inputs (refreshed, not remembered)

| Candidate | Ref | Head SHA | Base | State |
|---|---|---|---|---|
| S1 writer fence | `feat/v1-session-write-fence-20261016` (#241) | `429a717bedf1f6c28057a5e355970ff351100571` | `main` | OPEN / Draft |
| S1 grant authority | `feat/v1-s1-s2-grant-epoch-authority-20261018` (#272) | `fc93590030c218934252920a53aa67277f144844` | `feat/v1-s1-s2-resource-contract-20261018` | OPEN / Draft |
| WE lease | `feat/we-lease-fencing-289-20261020` (#295) | `99e08f01d910f3941fcf7ff78a6c5968309ac250` | `main` | OPEN / Draft |
| WE authority seams | `feat/we-authority-290-20261020` (#296) | `12845228d161e042dc230bcd64023b8c5cc7eaa1` | #295 | OPEN / Draft |
| WE T10 finalize | `feat/we-t10-finalize-291-20261020` (#297) | `8d42916fce746de447e6c1f0443a3b862449aab5` | #296 | OPEN / Draft |
| WE serverosd adapter | `feat/we-serveros-authority-292-20261020` (#298) | `e1f783fd93d77b0f1a3fddc183f740285d442b72` | #297 | OPEN / Draft |
| RED baseline | `origin/main` | `771c81c06f6c0b6a5fac65fdf36e51cd52a0f4b8` | — | merged |
| S1 previous snapshot SHA | — | #241 was `d0754ed`, #272 was `fe4738a` | — | stale, not used |

Verified ancestry: `#295 → #296 → #297 → #298` is linear; **#272 is a
descendant of #241** (`git merge-base --is-ancestor 429a717b fc93590` → true).
Thus #272 contains the #241 writer-fence work, and the S1 stack is #241 + #272.

Independently verified: the #298 cross-repo test reports **no skips** when the
#272 object is present; all 22 adapter tests ran, so its “skip when absent”
branch was not silently counted as a pass.

---

## 2. Method and evidence levels

- **CODE** — source/contract review of the pinned tree (GitHub diff + local
  `git show` of the exact SHA).
- **LOCAL** — hermetic test with temp state/db, same host, no provider/network.
- **REAL_TEMP** — the real component over a temp state dir and, where stated, a
  real AF_UNIX socket with real `SO_PEERCRED`. Still synthetic context/grant.
- **REAL_DEVICE** — an enrolled second device (Dell/Windows). **NOT_RUN.**
- **CLIENT_E2E** — the real Claude Code Fleet plugin client. **NOT_RUN.**

All runs used `systemd-run --user --scope` with `MemoryMax=1G`,
`MemorySwapMax=0`, `CPUQuota=25%`, `TasksMax=64`, `nice -n 10`, explicit test
files only, and `timeout`. No large suite, C++ build, or multi-Session stress
was run; S1/S2 development lanes were not touched.

Author-reported full regressions in #295/#296/#297/#298 PR bodies are
**not QA-signed**: they were not re-run (light-first, concurrent lanes). Only the
targeted files below were independently executed.

---

## 3. Verdict summary per #237 gate

| Gate | CODE | LOCAL | REAL_TEMP | REAL_DEVICE | CLIENT_E2E | QA verdict | Blocked trust producer |
|---|---|---|---|---|---|---|---|
| **T1** stale A after handoff | PASS | PASS | PARTIAL | NOT_RUN | NOT_RUN | **PARTIAL** — F1 (P0), F2 (P1) | Provider-side shell effect still unfenced (B2) |
| **T2** A/B race one write scope | PASS | PASS (single device) | PARTIAL | NOT_RUN | NOT_RUN | **PARTIAL** — single-daemon only; cross-device not enforced | Cross-device shared authority |
| **T3** expired/revoked lease in long turn | PASS | PASS | PARTIAL | NOT_RUN | NOT_RUN | **PARTIAL** — F7 (no reconciliation), residual TOCTOU | Transactional grant+lease+commit boundary |
| **T4** unknown result / restart | ABSENT | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | **NOT_IMPLEMENTED** — no `EFFECT_UNKNOWN` producer on the B2 path | Durable attempt/effect store |
| **T5** same key, modified payload | PARTIAL | PARTIAL | NOT_RUN | NOT_RUN | NOT_RUN | **NOT_DONE** — replay ignores payload (author agrees; S2 #252) | Payload digest in the replay key |
| **T6** expired lease still in DB | PASS | PASS | PASS | NOT_RUN | NOT_RUN | **PASS at CODE/LOCAL** — F9 composition conflict | — |
| **T7** cross-user / stale grant | PARTIAL | PARTIAL | NOT_RUN | NOT_RUN | NOT_RUN | **PARTIAL** — F3 (peer uid asserted, not authorized) | OS-principal ⇔ context binding |
| **T8** Tokyo → Dell under grant | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | **BLOCKED** | No trusted Tokyo↔Dell transport |
| **T9** provider unreachable / partial dispatch | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | **NOT_RUN** | Dispatch/effect reconciliation |
| **T10** child PASS while tests fail | PASS | PASS | PARTIAL | NOT_RUN | NOT_RUN | **PARTIAL / BLOCKED_TRUST_SOURCE** — F5, F6 | Real runner-receipt parent |
| **T11** local recovery when control plane is gone | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | **NOT_RUN** | Verified local grant + bounded recovery |
| **T12** Tokyo loss, Dell continues, reconnect | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | **NOT_RUN** | Cross-device reconnect/reconcile |

Every gate that is not `NOT_RUN` is at most **LOCAL**. No gate reaches
`REAL_DEVICE` or `CLIENT_E2E`; no production trust producer is bound.

---

## 4. Findings

Severity is a QA proposal, not an owner decision. “Pre-existing” means the defect
is present on `origin/main@771c81c` and is not introduced by the cited PR.

### F1 — P0 — Handoff revocation is not wired on the production MCP path

**Candidate:** #241 `429a717b` — `mcp/agent_mcp.py`, `fleet/agent_control_router.py`.

`_revoke_source_write_authority` refuses to act unless the source transport's
bindings handle has a `close` method:

```text
fleet/agent_control_router.py:1460
    if source_bindings is None or not hasattr(source_bindings, "close"):
        return {"revoked": None, "because": "the source device has no bindings handle here"}
```

The only non-test production loopback is built with the real `DaemonBindings`:

```text
mcp/agent_mcp.py:1095   bindings=DaemonBindings(daemon)
```

and `DaemonBindings` has **no `close` method** (runtime import of the pinned
tree: `close_method=False`; methods = `get`, `put`, `records_for`,
`put_selection`, `selection_for`). Therefore a handoff over the deployed MCP
surface returns `revoked: None` and leaves the source binding **OPEN**. Its v2
writer claim stays current, `agent.binding.resolve` still reports the source as
`current_writer`, and a stale `session.send` on the source is admitted.

The 83 green tests in this PR do not catch it because the handoff fixtures use
`tests/threads.py::MemoryBindings`, which *does* define `close`. The test double
implements a method the production object does not have.

**Effect:** T1’s “stale A is refused after handoff” is true only for the test
double, not for the production `mcp/agent_mcp.py::build()` graph.

**Suggested repair:** implement `DaemonBindings.close(ref, at=…, reason=…)`
calling `agent.binding.close`, and add a router-level handoff test wired to the
real `DaemonBindings` (or to a handle without `close`, asserting a visible
refusal rather than a silent `revoked: None`).

### F2 — P1 — The handoff target is not established with an epoch; it can be preempted

**Candidate:** #241 `429a717b` — `runtime/daemon.py::_writer_claim`,
`runtime/coordination.py::current_writer`, `fleet/agent_control_router.py::_revoke_source_write_authority`.

The producer mints a claim only for the **first** execution thread on a scope;
a later thread records none. The router then revokes the source by **closing**
its binding. After that closure the scope's `current_writer` is `None` — the
scope is *freed*, not *granted* to B. B has no owner/epoch/token claim.

Independent probe (`qa_probe_r1_s1.py`, 11/11 checks) on a real in-process
daemon:

```text
A mints writer {owner: session:A, epoch: 1, token: "1"}
B (second thread) mints no claim
after closing A: current_writer = None; B.writer = None
later session C mints {owner: session:C, epoch: 1, token: "2"}
B's next admission: NOT_CURRENT_WRITER
```

So a session created *after* the handoff can take the freed scope and fence the
handoff target. T1’s acceptance wording is “B authorized only by active epoch”;
B is instead authorized by “nobody owns it yet”. `_revoke_source_write_authority`
also checks only `bound_item_ref` on the target (`fleet/agent_control_router.py:1483`),
not that the target holds the current claim; a same-Item binding that is not the
current writer is accepted as “established”.

**Suggested repair:** on an established handoff, record the target as the new
writer with a minted `(owner, epoch, token)` in the same authoritative store,
and require `current_writer(target) == target` before revoking the source. Add a
positive test that a third session cannot take the scope immediately after the
handoff, and a negative test for a same-Item-but-not-current target.

### F3 — P1 — `agent.lease.authority.verify` asserts `SO_PEERCRED` but does not authorize it

**Candidate:** #272 `fc93590` — `runtime/agent_control_store.py:434-515`.

`verify_live_grant` refuses when the peer uid is absent (`:453`) and reports
`os_principal: uid:<peer.uid>` (`:515`), but never compares that uid to the
context, the grant principal, or any allowlist. Independent probe:

```text
peer = {pid: 1, uid: 0, gid: 0}
answer = {verified: true, grant: {principal: "human:A", os_principal: "uid:0", ...}}
```

This re-confirms the Snapshot N P1 (“peer uid 未授权”). On a shared host where
concurrent agents run as the same uid, `SO_PEERCRED` does not distinguish them;
any same-uid process that can read a live `context_ref` can verify as its
principal. The adapter side (`#298`) then compares `grant.os_principal` to
`os.getuid()`, which passes for every same-uid process.

**Suggested repair:** bind the context to an OS principal at mint time and
compare the peer uid/pid against that binding; treat a mismatch as
`UNTRUSTED_ACTOR`. If same-uid agents are an accepted trust boundary, state that
explicitly in the contract instead of implying the peer is an authorization.

### F4 — P2 — `agent.binding.record` accepts caller-chosen write authority

**Candidate:** #241 `429a717b` — `runtime/daemon.py:3117` (`explicit = params.get("writer")`).

Any client that can reach the daemon socket (mode 0600, so any same-uid process)
may record a new execution binding with an arbitrary `writer`:

```text
writer={"owner": "victim-A", "epoch": 42, "token": 999}
→ binding accepted; current_writer = {owner: "victim-A", epoch: 42, token: "999"}
```

The PR calls this “arming by hand / the CLI path”, but the daemon RPC has no
operator/context check, so it is an unauthenticated authority-minting path on a
multi-agent host. It also bypasses the “first thread claims / a create is not a
transfer” rules.

**Suggested repair:** require an explicit operator/context for the explicit
writer path (or restrict it to a CLI verb with an audit record); never let a
generic daemon client mint `owner`/`epoch`/`token`.

### F5 — P2 — A forged `finalize.json` becomes `T10_VERIFIED` on re-read

**Candidate:** #297 `8d42916` / #298 tip — `workengine/store.py:1234-1276`.

For a terminal Run, `_write_finalize` reads the durable `finalize.json`, and if
it contains a `runner_receipt` object it returns
`authorization: "T10_VERIFIED", t10_acceptable: true` **without checking the
evidence row’s `sha256`** against the file. Independent probe:

1. legacy-finalize a fresh Run (`UNSIGNED`);
2. edit its `finalize.json` to add a fabricated `runner_receipt` and
   `authorization: T10_VERIFIED`;
3. call `finalize_run(..., t10_required=False)` again.

Observed: `{'authorization': 'T10_VERIFIED', 't10_acceptable': True}`.

The `t10_required=True` path is **not** laundered by this: it re-verifies a
fresh receipt and compares the stored commit/actor/anchor to it. The exposure is
to consumers of the terminal return/label or of `finalize.json`. No production
consumer of `t10_acceptable` exists in this tree today, so the severity is
latent-V1-gate (P2), not a live bypass.

**Suggested repair:** on the terminal read, hash the file and compare with the
evidence row’s `sha256` (and `locator`); a mismatch must be `INVALID_RECEIPT`,
not a label upgrade. Or store the authorization label in the evidence row and
read it from there.

### F6 — P2 — Final-rename failure concludes the Run after the transaction commits

**Candidate:** #297 `8d42916` / #298 tip — `workengine/store.py:1330` (pre-existing pattern).

`temporary.replace(locator)` runs **after** the `transaction()` block commits
the evidence row and the Run state. Independent injection (make
`finalize.json` a directory): the call raises `IsADirectoryError`, but the Run
is already `SUCCEEDED` and one evidence row exists; the durable file is absent
(or wrong). By contrast, making `finalize.json.tmp` a directory fails before the
commit: Run stays `FINALIZING`, zero evidence — the safe direction.

**Suggested repair:** write the final file before committing the state change,
or roll back the conclusion when the final rename fails; keep the temp-write
failure behaviour as the model.

### F7 — P2 — An expired writer claim deadlocks the scope (no reconciliation)

**Candidate:** #241 `429a717b` — `runtime/coordination.py:348-376` (`writer_axis`,
`current_writer`).

`current_writer` does not consider `expires_at`; an expired claim remains the
scope’s current writer. Independent probe:

```text
A records an expired claim; B (new thread) mints no claim;
A admission → EXPIRED / NOT_CURRENT_WRITER
B admission → NOT_CURRENT_WRITER (claims nothing while the scope is owned)
```

The scope is fail-closed but unrecoverable without the explicit-writer path
(F4) or an out-of-band binding close. T3’s “blocked **or reconciled**” half has
no reconciliation path.

**Suggested repair:** `current_writer` should skip expired claims for ownership
(the row stays as evidence), or a re-acquire path should mint a successor token
with an explicit epoch advance; document the recovery operation.

### F8 — P2 — Malformed `expected_epoch` is an untyped `ValueError`, not a refusal word

**Candidate:** #272 `fc93590` — `runtime/agent_control_store.py:486-495`.

`int(expected_epoch)` raises on a non-numeric value; the daemon returns
`{"ok": false, "error": {"code": "ValueError", ...}}`. It is fail-closed
(`verified` is never true), but it violates the contract’s “every uncertainty is
a refusal” and leaks an implementation exception through the daemon boundary.

**Suggested repair:** validate `expected_epoch` as a positive integer in the
handler and return `INVALID_CONTEXT`.

### F9 — COMPOSITION — #241 and #295 rewrite the same T6 function differently

`context/resolver.py::held_leases` is rewritten by both sides:

- #241: Python-side filter, injectable `now`, `_LEASE_STAMP`, `math.ceil`;
- #295: SQL `WHERE expires_at > strftime('%Y-%m-%dT%H:%M:%SZ','now')`.

`git merge-tree $(git merge-base 429a717b e1f783f) 429a717b e1f783f` emits
conflict markers in two hunks of that one file. All other files merge cleanly.
This is not a defect in either PR alone, but S1 + WE cannot be composed without
choosing one implementation; the S1 version is the more testable one (injectable
`now`). The integrator (#250) must make this an explicit decision, not an
automatic `-X theirs`.

### F10 — NOTE — T5 payload replay (author-declared not done)

`fleet/agent_control_router.py::_replay` compares only `action` and
`device_ref`, not the payload; the same idempotency key with a modified
instruction is replayed with the first result rather than refused as a conflict.
No second effect occurs, but T5’s “Conflict” half is absent. The author marks T5
NOT DONE; the fix is tracked by the S2 lane (#252), outside this stack.

### F11 — NOTE — T4 is absent, not merely untested

`EFFECT_UNKNOWN` has no producer in `fleet/`, `runtime/`, `workengine/` or
`mcp/` at the pinned tips. The only occurrence is an unrelated `BUSY` set in
`intake/daily_cycle.py`. The contract states `implemented: false`. T4 is
**NOT_IMPLEMENTED**, and a “green” run that does not exercise it must not be
read as effect safety.

---

## 5. Independent test receipts

All commands ran in `/data/home/vince/workspace/worktrees/serveros/q1-security-qa-20261020`
(the isolated QA worktree), using `/data/home/vince/toolchains/serveros-test/bin/python3`
(pytest 9.1.1), under the resource guard. Logs and probes are in
`/data/home/vince/artifacts/skills/automated-testing/q1-security-qa-20261020/`.

| Run | Candidate | Command (explicit files) | Result |
|---|---|---|---|
| R1 fence | #241 `429a717b` | `pytest tests/test_v1_write_fence.py -q` | **42 passed** |
| R1 coordination | #241 `429a717b` | `pytest tests/test_coordination.py -q` | **41 passed** |
| R1 wiring surface | #241 `429a717b` | `pytest tests/test_agent_mcp.py tests/test_agent_control_router.py -q` | **147 passed, 171 subtests** |
| S1 grant epoch | #272 `fc93590` | `pytest tests/test_v1_grant_epoch_authority.py -q` | **13 passed** |
| WE core | #298 tip `e1f783f` | `pytest tests/test_lease.py tests/test_lease_authority.py tests/test_runner_receipt.py tests/test_finalize_entry.py -q` | **84 passed** |
| WE cross-repo | #298 tip `e1f783f` | `pytest tests/test_serveros_lease_authority.py -q -rs` | **22 passed, 0 skipped** |
| WE resolver/T6 | #298 tip `e1f783f` | `pytest tests/test_resolver.py -q -rs` | **24 passed, 7 subtests** |
| RED baseline | `771c81c` | `qa_probe_r1_red.py` | **6/6 absence checks** |
| Probe S1 | #241 `429a717b` | `qa_probe_r1_s1.py` | **11/11** behaviour checks |
| Probe #272 | #272 `fc93590` | `qa_probe_r1_272.py` | **7/7** checks (F3 observable) |
| Probe WE | #298 tip `e1f783f` | `qa_probe_r1_we.py` | 21 checks, **3 expected-safe failures** = F5, F5-forgery, F6 |

Log hashes (sha256): `241-write-fence` `ed1649fb…`, `241-coordination`
`55b6bf1e…`, `241-agent-mcp-router` `c353d58c…`, `272-grant-epoch`
`30c6f4da…`, `we298-core` `52b7f797…`, `we298-xrepo` `0245677f…`,
`we298-resolver` `a17b64e7…`, `probe-red` `12302581…`, `probe-s1`
`057e4a99…`, `probe-272` `1f90cb86…`, `probe-we` `f9de12dd…`.

### RED → GREEN (independently observed)

On `origin/main@771c81c` the red probe proves the gates are absent:

- `admit_work` admits a binding carrying a superseded writer claim;
- `held_leases` returns an expired row as held;
- `ServerOSStore` has no `acquire_lease`;
- `finalize_run` has no `t10_required`, and caller `PASS/0` concludes
  `SUCCEEDED` with **no** authorization label;
- serverosd has no `agent.lease.authority.verify`.

On the pinned candidates the corresponding green runs above turn each of those
into a refusal or a labelled result — except where F1/F2/F5/F6 show the
production wiring or the durable read is weaker than the test double.

---

## 6. Author claims explicitly NOT converted to QA evidence

- #295 “36 passed / 2465 passed”, #296 “50 passed / 2541 passed”,
  #297 “92 passed”, #298 “50 passed / 2329 passed”: author-reported; only the
  targeted files above were re-run. Full regressions were not re-run because the
  host is concurrently running S1/S2 development lanes (light-first mandate).
- #298’s “test-double parent” positive path is **ELIGIBLE_FOR_QA only**, never a
  production `T10_VERIFIED`.
- `T10_VERIFIED` in the store means “a parent verified a receipt”, **not** an
  independent QA acceptance.

---

## 7. Handback / owner actions

1. **#241 (P0)**: implement `DaemonBindings.close` and add a handoff test over
   the real bindings handle; do not claim T1 closed on the MCP path until then.
2. **#241 (P1)**: mint the target’s `(owner, epoch, token)` at handoff and
   require `current_writer(target) == target` before revoking the source (F2).
3. **#272 (P1)**: bind and compare the peer OS principal, or state the same-uid
   trust boundary explicitly (F3); return a refusal word for malformed
   `expected_epoch` (F8).
4. **#297 (P2)**: verify the evidence-row `sha256` before trusting
   `finalize.json`’s label (F5); move the final rename before the state commit
   or roll it back (F6).
5. **#250 integrator**: reconcile `context/resolver.py::held_leases` before
   composing S1 + WE (F9); do not auto-resolve.
6. **#256 QA register**: `QA_PARTIAL` stands. T1/T2/T3/T7/T10 are LOCAL/PARTIAL;
   T4/T5/T8/T9/T11/T12 are NOT_RUN or NOT_IMPLEMENTED; no REAL_DEVICE or
   CLIENT_E2E evidence exists.

## 8. What this snapshot does not claim

- No `QA_SIGNED`, no `V1_COMPLETE`, no release or rollback approval.
- No production service, provider, credential, Feishu, model, or device was
  touched.
- No large pytest, C++ build, or multi-Session stress was run.
- The positive lease/T10 paths remain `BLOCKED_TRUST_SOURCE` in production; the
  test-double paths do not change that.

## 9. Reproduction

```bash
# isolated worktree, no product edits
git worktree add -b qa/q1-independent-security-20261020 \
  /path/to/q1-security-qa-20261020 771c81c
QA_TREE=/path/to/q1-security-qa-20261020 \
  /data/home/vince/toolchains/serveros-test/bin/python3 \
  integrations/fleet-v1-qa/probe/qa_probe_r1_red.py

git -C /path/to/q1-security-qa-20261020 checkout --detach 429a717b
QA_TREE=$PWD .../python3 integrations/fleet-v1-qa/probe/qa_probe_r1_s1.py

git -C /path/to/q1-security-qa-20261020 checkout --detach fc93590
QA_TREE=$PWD .../python3 integrations/fleet-v1-qa/probe/qa_probe_r1_272.py

git -C /path/to/q1-security-qa-20261020 checkout --detach e1f783f
QA_TREE=$PWD .../python3 integrations/fleet-v1-qa/probe/qa_probe_r1_we.py
```

Every probe exits non-zero when an expected-safe assertion fails; the failures
listed in §4 are the findings, not harness failures.
