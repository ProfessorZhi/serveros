# W19E · main-based Hub/Node builder candidate (offline)

Date: 2026-10-29
Scope: `ProfessorZhi/serveros`, source and offline builds only.
Role: original owner of the Wave 18/19 builder (#505/#509/#518).
Base: protected `main@44f039d70dc4f593ea9db73aae873be3455fc48c`
(Push CI 5/5). This is a **candidate branch**, not a merge.

## 0. Read this first

No install, service start, port/socket bind, OAuth/key/Tailscale, Feishu/IM,
paid model, GPU or Legacy configuration change. **REAL_DEVICE NOT_RUN.** The
merge decision belongs to the Tokyo Captain after independent QA; this lane
does not merge `main`.

## 1. Why a new main-based branch instead of retargeting #518

`main@44f039d` does **not** contain #431, #505, #509 or #518:

```
git merge-base --is-ancestor 517ef91 origin/main   # false (#431)
git merge-base --is-ancestor 3512c55 origin/main   # false (#505)
git merge-base --is-ancestor 3520d0c origin/main   # false (#509)
git merge-base --is-ancestor d704561 origin/main   # false (#518)
```

Retargeting #518 to `main` would pull the whole old stack into the PR, not a
narrow candidate. The old stack also inherits the Lark date-window test failure;
current main has that fix. So this branch starts at `main@44f039d` and carries
**only the author delta** of #505/#509/#518.

## 2. What is carried, and what is not

Carried (our paths only):

```
integrations/fleet-next-wave17-hub-node-release-cut/**
integrations/fleet-next-wave18-artifact-builder/**
tests/test_fleet_next_wave17_hub_node_release_cut.py
tests/test_fleet_next_wave18_artifact_builder.py
docs/integration/FLEET-NEXT-WAVE17-HUB-NODE-CUT-20261028.md
docs/integration/FLEET-NEXT-WAVE18-ARTIFACT-BUILDER-20261029.md
docs/integration/FLEET-NEXT-WAVE19-REPRODUCIBILITY-20261029.md
docs/integration/FLEET-NEXT-W19E-MAIN-BUILDER-20261029.md
```

Not carried: `fleet_mcp/**`, `fleet/**`, `runtime/**`, `scripts/release.py`,
#431's tests, #407/#461/#467/#473's V2 sources, any Legacy file.

### The one dependency main does not have

The W18/W17 builders reuse #431's D7 closure/archive/reader by SHA pin. Main
does not contain #431, so the single file is vendored **byte-for-byte,
unmodified**:

```
integrations/fleet-next-wave18-artifact-builder/vendor/l2_release.py
sha256:7f87a59611b00585f2e7eba3799c7d4bd0c256abec775b96e7ed8d6f0a5ba01d
source: #431 517ef915074e3b14537b746a07b4dcee63eccfb6
        integrations/fleet-v1-l2-release/l2_release.py
```

`vendor/VENDORED.json` records the source and states that #431 authored the
file and this lane only stores and pins it. No #431 test or other source is
carried, and no second implementation of the release facts is introduced.

## 3. Current-main evidence

Source `47030bc1d9a728c4ce99f2b27ccd16017aea0eab`, version `1.0.0+wave19e`,
builder `wave18_builder.py` `sha256:4a6b9b6b…`.

| unit | files | tarball sha256 | installable |
|---|---:|---|---|
| `fleet-runtime-base` | 19 | `885ab0e57194e2f8e29e7f9867aaf1b64f22331fb960af1679e52edbcb37fc8b` | yes |
| `fleet-authority` | 3 | `38f7e6cc1726b31b9b74d5ebf35f7558578467828583b168d5d26627b309a01c` | yes |
| `fleet-hub` | 13 | `dce13bed52efa907ff160a34e49c614f2bc03099e7f2916f48305f68f83143f0` | yes |
| `fleet-node` | 2 | `ad9091491d514a4f3412030a7e954414c431f82d8363aa5791c39e6467f8b929` | yes |
| `fleet-next-composition` | 0 | `1a92993c654e6ea555cbb8c7193d90c6d29bc46444ef54332ce1d4454fb583b0` | **no** |

* Two fresh builds at `47030bc` are **byte-identical 5/5**.
* The same five digests under normal Git config,
  `GIT_CONFIG_SYSTEM core.autocrlf=true`, and
  `HOME/.gitconfig core.autocrlf=true`.
* The fixed builder refuses a CRLF transform with
  `BLOCKED_LINE_ENDING_DRIFT`; the historical #514 RED digests remain in
  `evidence-w19/ENVIRONMENT-MATRIX.json`.
* Isolation: Hub boots with Base + Authority and no Node file; Node boots with
  Base and no Hub file, in a fresh `-I -B` interpreter with no checkout.
* Negative control: removing `fleet_mcp/errors.py`, `fleet_mcp/config.py` or
  `fleet/transport.py` makes the corresponding boot fail.
* Rollback: Node-only and Hub-only rollbacks leave the other prefix untouched;
  the composition rollback is compensating; the Legacy 0.1.0 fake's
  `get_session`/`get_run`/`send`, identities and deliveries are unchanged.
* Composition has zero code members and `install.independently_installable:
  false`; it is a manifest that pins the four units, not an installable payload.

Evidence: `integrations/fleet-next-wave18-artifact-builder/evidence-main/`.

## 4. Independent QA from a full clone

```bash
python3 -I integrations/fleet-next-wave18-artifact-builder/wave18_builder.py \
  --repo . --commit 47030bc1d9a728c4ce99f2b27ccd16017aea0eab \
  --version 1.0.0+wave19e \
  --expect-checksums integrations/fleet-next-wave18-artifact-builder/evidence-main/ARTIFACTS.sha256 \
  --out /tmp/w19e-qa --json
```

The command rebuilds, compares 5/5, runs the negative control and exits 0 only
if both hold. A shallow PR checkout may not carry `47030bc`; the CI test then
builds `HEAD` twice, compares 5/5 and runs the same negative control.

## 5. Hosted CI — 4/5, blocked by a stale non-author pin

At head `a53866a`: plugin + Suite shards 1, 3 and 4 pass. Shard 2 reports 14
new failures, **all** in `tests/test_fleet_next_mainline_ledger.py`, and all
from its `setUpModule`:

```text
AssertionError: PR #517 moved:
  818bfb1cc0615de1cdfe8859db6a4ead31b95dd6
  != pinned 823a38198322c90c30381758befd4e208ba85c2a
```

The ledger test pins PR #517's old head. #517 advanced at 06:36 to
`818bfb1` (“run W19 QA against current main 44f039d”); `823a381` is its
ancestor, but it is not in main. A depth-1 PR checkout does not have
`823a381`, so `_ensure_pins()` fetches `refs/pull/517/head`, gets `818bfb1`,
and fails before any test runs.

This is **not caused by this candidate**:

* the candidate does not touch `tests/test_fleet_next_mainline_ledger.py`,
  `integrations/fleet-next-mainline/dag.json`, or
  `docs/integration/FLEET-NEXT-MAINLINE-20261029.md`;
* in a full clone, `pytest tests/test_fleet_next_mainline_ledger.py -q` is
  **14 passed, 128 subtests**;
* the 20 Wave 18/19 builder tests and the 13 Wave 17 tests are not in the
  failure list.

The pin appears in three mainline-owner files, all still `823a381`:

```
tests/test_fleet_next_mainline_ledger.py:42   QA_HEAD
integrations/fleet-next-mainline/dag.json:164 "head"
docs/integration/FLEET-NEXT-MAINLINE-20261029.md:79  #517 row
```

Owner fix: set all three to `818bfb1cc0615de1cdfe8859db6a4ead31b95dd6` (or
make the pin a set that accepts the current head), then re-run #526. No
baseline exemption is needed or was added. `ci_state:
BLOCKED_EXTERNAL_PIN`; `technical_verified` remains true.

## 6. Verdict and handoff

| field | value |
|---|---|
| `technical_verified` | **true** — 5/5 reproducible, environment-invariant, isolation + negative control + rollback pass |
| `ci_state` | `BLOCKED_EXTERNAL_PIN` — hosted 4/5; the only new failures are the mainline ledger's stale #517 pin (`823a381` vs `818bfb1`), not this candidate |
| `source_state` | `BLOCKED_OWNER` — the CUT-A/CUT-B transforms are not landed in `fleet_mcp/**` |
| `release_permitted` | **false** |
| `real_device` | `NOT_RUN` |

Handoff to #515 / Tokyo Captain:

1. review this candidate as the main-based builder delta;
2. require hosted CI 5/5 with zero new failures;
3. run the independent QA command above on a full clone;
4. only then decide merge. This lane does not merge `main`, does not install,
   and does not claim production readiness.

## 7. Non-goals honoured

* No `main` merge, no real install, no service/port/socket change.
* No OAuth, key, Tailscale, Feishu/IM, paid-model or GPU operation.
* No Legacy takeover or configuration change.
* No `fleet_mcp/**`, `fleet/**`, `runtime/**`, `scripts/` edit; no V2 #407
  merge; no #431/#505/#509/#518 unrelated file.
* No fabricated artifact; the zero-member composition is not installable.
* `REAL_DEVICE NOT_RUN`.
