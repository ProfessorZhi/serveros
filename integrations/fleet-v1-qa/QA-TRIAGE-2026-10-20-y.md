# QA-TRIAGE-2026-10-20-y — X1 context-age fix / RC-v4 readiness

**Status: `LOCAL` PASS for #338 (test-only, no product authority change).
RC-v4 is PENDING. No V1 signature.**

Lane: `Q1-independent-qa`, campaign #244, parent #250, QA register #256.
Independent Session; no product source edited and no author code changed.
Additive QA files only under `integrations/fleet-v1-qa/**`; probes/logs under
`artifacts/skills/automated-testing/q1-security-qa-20261020/`.

Snapshot: **2026-10-20T09:0xZ (UTC)**.

---

## 1. Pinned inputs

| Item | Exact ref |
|---|---|
| #338 | `96e0fcf2cd464e5e15fa88ef7032f06139c8a06c` |
| #338 branch | `test/x1-agent-mcp-context-age-20261020` (local = remote = `96e0fcf`) |
| Base RC-v3 | `e3dc9c7ca0aeca9dd97e981de85da3a727ed9d8a` |
| RC-v4 | **no GitHub `rc/*` v4 ref** — **PENDING** |
| Previous QA | #337 `d2fbf25` |

The local SHA, the PR head, and the remote branch head are identical. The I1
RC-v4 worktree was **not accessed or modified**; only its existence was visible
in the process list while checking host load.

---

## 2. Diff and product boundary

`git diff --stat e3dc9c7 96e0fcf` → **one file**, `tests/test_agent_mcp.py`,
+105/−3. `git diff --name-only e3dc9c7 96e0fcf | grep -v '^tests/'` → empty.
No product file, no `xfail`/`skip` added. The change:
- `minted_context(now=...)` for a past instant;
- `_refresh_context()` at the start of `build()`, so the positive fixture is
  re-minted per build;
- `arguments_for` resolves a `_CURRENT_CONTEXT` sentinel at call time instead
  of an import-time `CONTEXT_REF`;
- two new tests: the re-mint positive and an explicit 301s/901s negative.

The product freshness code (`context/actor.py`, `abilities/target.py`) and all
grant/scope/TTL logic are **unchanged**.

---

## 3. RED on the base `e3dc9c7`

Independent probe `qa_probe_r8_context_freshness.py` (does not edit the test
file; it imports the test module and installs an aged context):

```text
module context aged 301s: expected=APPROVAL_REQUIRED actual='UNTRUSTED_ACTOR'
provider_calls=0
PASS RED aged positive fixture is refused before approval
PASS RED no provider call
```

The base `build()` does not re-mint; the module-level context has aged past
`stale_after_seconds=300`, so `actor.authorizes` refuses before the approval
check. This is the reported fixture-age bug, not a product dual source.

---

## 4. GREEN on #338 `96e0fcf`

Same probe, after `build()` re-mints:

- positive aged-context call → **`APPROVAL_REQUIRED`**, provider calls **0**;
- an explicitly presented **301s** context → **`UNTRUSTED_ACTOR`**, detail
  “not recently enough…”, provider calls **0**;
- an explicitly presented **901s** context → **`UNTRUSTED_ACTOR`**, detail
  “past the 900s window”, provider calls **0**;
- contract: `stale_after_seconds=300`, `window_seconds=900` (unchanged);
- the candidate changes only `tests/test_agent_mcp.py`; no xfail/skip.

Targeted suite at the exact SHA: `python -m pytest tests/test_agent_mcp.py -q
-rs -p no:cacheprovider` → **72 passed, 177 subtests**, exit 0 (79.90s), under
1G/no-swap/25% CPU/nice-10.

The author's representative 4-file order (264 passed, 184 subtests, 235s) was
**not re-run**: the shared host had the I1 long suite running (load ≈ 52, CPU
PSI ≈ 5, IO PSI ≈ 6). The targeted file plus the independent probe are the
bounded evidence for this round.

---

## 5. Judgment

- **No product authority change**: the fix only re-mints the test fixture at
  build time. It does not move the 300s threshold, add a bypass, or change a
  grant/scope/TTL decision.
- **Stale/expired are not hidden**: 301s and 901s contexts still refuse with
  `UNTRUSTED_ACTOR` and zero provider calls. The positive path is positive
  only because it is freshly minted; the negative boundary remains enforced.
- **No blocker** for #338 as a test-only fix. It does not by itself make RC-v4
  acceptable.

---

## 6. RC-v4 — PENDING

There is no GitHub review ref for RC-v4 (`rc/*` is `3f0240d`, `ec43f71`,
`e3dc9c7`). **RC-v4 is PENDING; no QA_SIGNED.**

When a review ref is actually published, QA must use a **fresh isolated
checkout pinned to its exact SHA** and run, at minimum:

1. T1/T7 handoff and stale/expired/actor probes (`qa_probe_r2_handoff.py`,
   `qa_probe_r3_*`, `qa_probe_r4_lease_admission.py`);
2. Core boundary (`tests/test_core_ability_boundary.py`, `qa_probe_r5_*`);
3. combined T10/Finalize (the 8-file sequence from Snapshot X) and public
   authority (`#335` X2 three + guarded five);
4. Fleet Next isolation (`qa_probe_r6_fleet_next_gate.py`);
5. a test-only REAL_TEMP daemon/socket run with a temp state and a no-effect
   refusal (no production state, no provider send).

A V1 signature additionally requires positive trust sources, the full T1–T12
matrix, and real host/Windows E2E and rollback evidence — none of which is
available in this round. The RC-v4 local I1 worktree and its long suite were
not accessed or interrupted.

---

## 7. Receipts

| Run | Result | Log |
|---|---|---|
| RED base `e3dc9c7` | expected APPROVAL_REQUIRED, actual UNTRUSTED_ACTOR; provider 0 | `probe-r8-context-age-red-base.log` |
| GREEN #338 aged positive | APPROVAL_REQUIRED; provider 0 | `probe-r8-context-age-green-338.log` |
| GREEN #338 301s | UNTRUSTED_ACTOR; provider 0 | same |
| GREEN #338 901s | UNTRUSTED_ACTOR; provider 0 | same |
| #338 targeted suite | 72 passed, 177 subtests | `r8-338-agent-mcp-targeted.log` |

All runs used explicit files, `timeout`, and the `systemd-run`
1G/no-swap/25% CPU/nice-10 guard. No production daemon, provider/network/model
send, secrets/OAuth, service/plugin change, or main merge.

---

## 8. Handback

1. **#338**: test-only LOCAL PASS; keep it Draft; it is not a product authority
   change.
2. **#256/#244**: record the RED/GREEN fixture-age evidence and the RC-v4
   PENDING status; no V1 signature.
3. **I1**: do not treat the local RC-v4 assembly or a green long suite as QA
   evidence; publish the exact review ref and let QA rerun the matrix.

## 9. What this does not claim

- No RC-v4 acceptance, no full T1–T12, no REAL_DEVICE/CLIENT_E2E.
- No positive trusted parent; production remains `BLOCKED_TRUST_SOURCE`.
- The author's representative 4-file order was not re-run.
