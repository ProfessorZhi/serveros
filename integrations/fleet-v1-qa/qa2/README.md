# QA2 — independent security audit of S1 #358 and R1 #356

**Verdict: `QA_PARTIAL` / `NOT_RELEASE`.** No P0. S1 has two P1 and three P2
findings; R1 has two P1 and four P2 findings. Both PRs remain fail-closed in
production, but the P1s must be fixed before an owner resolver is bound or the
reconcile gate is wired.

This directory is the only change of the QA2 branch. It contains no product
source, no author worktree, no production secret, token, transcript, provider
call or system-service change.

## Pinned refs

| Lane | PR | head | base |
|---|---|---|---|
| S1 owner policy | #358 | `b183aab0b8913ed36e5de539c50223d3eaf508f1` | `8620a90e58cf92ba261560b1e4eb5f0797beee09` (`rc/i1-v6-review-20261020`) |
| R1 reconcile source | #356 | `b4020b73e6b7ec2b1f6301471a9872f8864c1643` | `34b215c355d93efe9e7b06fcc09f9a803c061f00` (#311 head) |

Neither head is in RC-v7 `85c79b2`. The QA2 branch is based on
`rc/i1-v7-review-20261020` and adds only `integrations/fleet-v1-qa/qa2/**`.

## Layout

```text
QA2-INDEPENDENT-SECURITY-2026-10-20.md   full report, findings, fixes
probes/s1_probe.py                       daemon/store controls + adversarial witnesses
probes/s1_signed_probe.py                real local Ed25519 path in a temp HOME
probes/r1_probe.py                       pure reconcile-source gate controls + witnesses
logs/                                    exact run logs and rc files
qa2-run.sh                               reproducible export + bounded runs
```

## Evidence levels

- **S1**: `LOCAL_SYNTHETIC_PEER` (peer metadata is test data, not
  `SO_PEERCRED`), `LOCAL_SYNTHETIC_POLICY` (test `OwnerPolicy`), and
  `LOCAL_SYNTHETIC_TRUST` (a locally generated Ed25519 key in a temporary HOME,
  not a Provider). The author's `REAL_TEMP` test uses a real AF_UNIX socket and
  a real `SO_PEERCRED`, but its context/grant/policy are fixtures.
  Real owner resolver, two real UIDs and real Provider signatures are
  **BLOCKED**.
- **R1**: `SYNTHETIC_FIXTURE`; the fixture test key is public test data, not an
  owner authority. Production mode is always blocked. Real provider echo,
  real owner-signed observation and production settle are **NOT_RUN /
  BLOCKED**.
- The S2 kernel #328 `cf32af82c03eb52a4f34e2907e670f23b04a9b57` was inspected
  statically for downstream checks, but it is not in #356's base and the gate
  is not wired. No integration pass is claimed.

## Run

```sh
cd integrations/fleet-v1-qa/qa2
./qa2-run.sh export   # archive the exact PR heads from the repo/fetch
./qa2-run.sh all      # author tests + controls + findings
./qa2-run.sh s1
./qa2-run.sh r1
```

The script verifies the exported `.snapshot-sha`, uses
`PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider`, and bounds each run with
`MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 / timeout 120` when
`systemd-run --user --scope` is available. It never enters the S1/R1 author
worktrees and never calls a provider or production service.

`probes/s1_signed_probe.py` needs `cryptography` (the system `python3` on this
host has it; the serveros-test venv does not). If it is unavailable, the script
reports `BLOCKED_CRYPTOGRAPHY` and does not claim that control.

## Current result

| Gate | rc | Result |
|---|---|---|
| S1 author tests | 0 | 86 passed, 1 skipped, 23 subtests |
| S1 controls | 0 | 36/36 |
| S1 signed (local Ed25519) | 0 | 10/10 |
| S1 findings | 1 (expected) | 6/6 confirmed |
| R1 author test | 0 | 35 passed |
| R1 controls | 0 | 22/22 |
| R1 findings | 1 (expected) | 8/8 confirmed |

A findings rc of 1 means the adversarial probe detected the documented gaps; it
is not a failure of the authors' test suite. See the report for the exact
findings, line references, minimal negative tests and fixes.
