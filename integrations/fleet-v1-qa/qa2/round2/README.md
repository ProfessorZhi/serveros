# QA2 Round 2 — R1 #365 / S1 #366

**Verdict: `QA_PARTIAL` / `NOT_RELEASE`.** No P0/P1. #365 has six P2
contract-boundary/residual findings; #366 has one P2 probe-quality finding.
Both PRs remain fail-closed; no production owner issuer, trusted provider
readback or cross-store atomic commit is wired. No QA/V1 signature.

## Pinned refs

| Lane | PR | exact head | exact base |
|---|---|---|---|
| R1 observation contract | #365 | `d043521c516033f00e2315ace74858b3aa5cef87` | #361 `6a27204300753f129719103d0aee811ac675622a` |
| S1 atomic commit contract | #366 | `471e300707cc68a669ca3e0c8ff81aecdc278805` | #364 `7fe1cccd6b7f103e58140ef7d512d69e436704ec` |

Neither head is in RC-v7 `85c79b2`; RC-v8 is not published. This directory
only adds independent QA evidence; no product source, author worktree,
production daemon/Fleet/Connector, provider, token, OAuth or system service is
touched.

## Layout

```text
QA2-ROUND2-365-366-2026-10-20.md      full report, findings, fixes
probes/r1_365_observation_probe.py    QA2 controls + contract-boundary witnesses
probes/s1_366_revoked_ttl_probe.py    revoked/TTL/no-resolver/owner-live no-write
probes/qa_probe_r17_r1_361.py         prior independent v2 probe (25/25 regression)
probes/qa_probe_r19_seam_364.py       prior independent seam probe (10/10 regression)
logs/                                 exact logs + rc files
run.sh                                reproducible export + bounded runs
```

## Evidence levels

- **#365**: `CODE` / `LOCAL` / `SYNTHETIC_FIXTURE`; the Ed25519 control uses
  in-process temporary keys (`LOCAL_ED25519`), not a provider/owner.
  `REAL_PROVIDER`, `REAL_DEVICE`, `CLIENT_E2E` **NOT_RUN**.
- **#366**: `REAL_TEMP` socket/`SO_PEERCRED` + production daemon/store/adapter
  code; owner policy/context/grant are synthetic fixtures; one uid;
  `BLOCKED_MULTIPRINCIPAL_NOT_RUN`. No `REAL_DEVICE`/`HOSTED`.
- No real provider/model/token/transcript/OAuth/send/retry/settle.

## Current result

| Gate | rc | Result |
|---|---|---|
| #365 author 41+61 | 0 | 102 passed |
| #365 upstream v2 probe | 0 | TOTAL 25 FAIL 0 |
| #365 QA2 controls | 0 | 26/26 |
| #365 QA2 findings | 1 (expected) | 8/8 confirmed |
| #366 contract test | 0 | 1 passed, 2 skipped |
| #366 #364 four suites | 0 | 52 passed, 1 skipped |
| #366 five-file combined | 0 | 53 passed, 3 skipped |
| #366 probe controls | 0 | 5/5 |
| #366 probe positive | 2 (expected) | `BLOCKED_OWNER_ATOMIC_AUTHORITY` |
| #366 probe all | 2 (expected) | controls 5/5 + positive |
| #366 seam probe | 0 | 10/10 (3/3 reruns; one first-run socket flake, no lease) |
| #366 revoked/TTL no-write | 0 | 7/7, `lease_written=false` |

The two real-UID skips are `BLOCKED_MULTIPRINCIPAL_NOT_RUN` (uid 1002;
`sudo -n true` rc1). The positive-authority skip is
`BLOCKED_OWNER_ATOMIC_AUTHORITY`. None is simulated.

## Run

```sh
cd integrations/fleet-v1-qa/qa2/round2
./run.sh all
```

`run.sh` exports the exact heads with `git archive` (fetching
`pull/365/head` / `pull/366/head` only if the objects are absent), verifies
each `.snapshot-sha`, and bounds every run with
`MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 / timeout` when
`systemd-run --user --scope` is available. It never enters the author
worktrees and never calls a provider or production service.

The #366 pytest needs `PYTHONPATH=<snapshot>` because `workengine/` is imported
from the repository root; the script sets it. The system `python3` is used for
the probes (it has `cryptography` for the #365 Ed25519 control); the
`serveros-test` toolchain `pytest` is used for the test suites.

See `QA2-ROUND2-365-366-2026-10-20.md` for the exact findings, line
references, negative tests and fixes.
