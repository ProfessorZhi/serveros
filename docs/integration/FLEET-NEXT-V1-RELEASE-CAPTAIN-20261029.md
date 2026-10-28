# Fleet Next V1 — release captain, 2026-10-29

**Status: `PRE_FLIGHT`. Nothing was installed. Nothing was deployed. Legacy
Fleet 0.1.0 keeps serving ChatGPT.**

This is the operating frame and the acceptance order for releasing Fleet Next
V1 **beside** Legacy, not a new Fleet service. It answers one question at a
time, per device: *does this exact tree, with these exact artifacts, on these
exact measured resources, reach this exact state — and if not, which fact is
missing?*

Draft PR: [#510](https://github.com/ProfessorZhi/serveros/pull/510), branch
`feat/fleet-next-v1-release-captain-20261029`, based on `main` @
`e69fc0337ae5d33000478e3d5162077f525bc8cc`.

The machine-readable counterpart is
`integrations/fleet-next-v1-release-captain/release-manifest.2026-10-29.json`;
the engine is `integrations/fleet-next-v1-release-captain/release_captain.py`;
the pinned CI observations are in
`integrations/fleet-next-v1-release-captain/evidence/CI-SNAPSHOT-2026-10-29.json`.

---

## 1. Authority, stated before anything else

The owner's decision in #478 (2026-10-28) and the campaign in #425 / #434 /
#409 grant:

* an independent worktree and branch, offline tests, a **Draft** PR, and issue
  receipts;
* a **new, independent, reversible Next canary instance** beside Legacy;
* per-device local operator approval as the gate for that device.

They do **not** grant, and this round did not perform: a `main` merge, a
Legacy replacement or retirement, a Legacy plugin URL change, a Legacy state
migration, a production auth/secret/OAuth-provider change, a real Feishu/IM
send, a Tailscale mutation, an irreversible delete or shutdown, or a paid-model
trial. Any request for one of those is `BLOCKED_OWNER` in the engine, by name,
not by convention.

Legacy is the control plane ChatGPT is using today, across three routes with
different sessions, runs and IM identities. None of them was touched.

---

## 2. What was built

| file | what it is |
| --- | --- |
| `release_captain.py` | deterministic state machine + gate arithmetic + Next-only rollback planner. Stdlib only; no clock, socket, credential or write. |
| `release-manifest.2026-10-29.json` | the facts this release may depend on, as observed today. |
| `schema/release-manifest.v1.schema.json` | the manifest's documented shape. |
| `rollback-plan.example.json` | the generated shape of a Tokyo first-install rollback (`mode=withdraw`). |
| `evidence/CI-SNAPSHOT-2026-10-29.json` | per-head check-run conclusions, with what each means. |
| `tests/test_fleet_next_v1_release_captain.py` | 95 tests: positive controls, negative guards, mutation controls. |

The engine writes nothing and executes nothing. `--execute` on the CLI prints
`EXECUTOR_ABSENT` and exits 2. The only executor that exists is the caller's,
and `execute_plan()` refuses to drive it unless the environment is `canary`, an
owner approval is granted, and an evidence directory is named.

---

## 3. State machine

```text
PRE_FLIGHT ──▶ READY ──▶ CANARY ──▶ PROMOTE
     │            │          │          │
     └────────────┴──────────┴──────────┴──▶ BLOCKED   (any gate red or UNKNOWN)
                                    ROLLBACK  (Next-only, idempotent)
```

`PRE_FLIGHT` and `BLOCKED` are different facts. `PRE_FLIGHT` means every open
item is a **missing** observation — nobody has looked. `BLOCKED` means at least
one gate is red. Both stop the release; only one of them is somebody's defect.

### Gate matrix, per stage

Each stage is judged independently. A green in one column is not evidence in
another.

| stage | gates |
| --- | --- |
| `CODE` | composed `head_sha`/`tree_sha` pinned; every component SHA present and unmoved |
| `UNIT` | the release-captain suite; independent QA on the same tree |
| `HOSTED_CI` | 5/5 jobs `success` on that exact head — not four, not a pending fifth; not a green run for another commit |
| `RELEASE_ARTIFACT` | per-package sha256 (64 hex) + provenance whose `source_tree` equals the release tree |
| `CLIENT_E2E` | `NOT_RUN` |
| `REAL_DEVICE` | per-device local operator grant, measured headroom, reachability |
| `PRODUCTION` | `NOT_AUTHORIZED` — requires the owner decision this round does not hold |
| `ROLLBACK` | Next-only plan, verified dry run, idempotent second application |

Two identity rules do most of the work:

1. **Evidence belongs to a tree.** A green CI run or QA verdict counts only when
   the SHA it names is the SHA being released. A green result for somebody
   else's commit is `BLOCKED_SOURCE_IDENTITY`, not a green.
2. **An absence is not a yes.** Every gate is tri-state. A field nobody observed
   is `BLOCKED_UNKNOWN`; it is never read as a default, and it never turns a
   stage green by being absent.

---

## 4. Source identity pinned for this release

These are the heads named by the 2026-10-29 brief, re-read from GitHub at
capture time and asserted by
`test_the_manifest_pins_the_five_heads_the_brief_names`.

| PR | role | head SHA | state |
| --- | --- | --- | --- |
| #500 | Wave 16 RC base | `80220a4ad1b009946f11bda0ce96dd8ff8e05468` | Draft |
| #503 | runtime-tick ABI | `b7f1f6134a1e3dcee4b916068c693ff059c097bd` | Draft |
| #504 | monitor P0/P1 | `6286bfbc885771a45415efcb33405bb21a841fde` | Draft |
| #505 | Hub/Node release cut | `3512c551574a94ad360f8bde6435bdf07c729167` | Draft |
| #506 | Sidecar release | `30d7c4d34ce7ebf76d751b8e306fa2c623c4cac7` | Draft |

Wave 18 is the only composition that merges #500 + #503 + #504: **#507
`05da04f8c78f302b029c6304ed96f4e8ded5fc9b`**, with independent QA on **#508
`3340a1e1af5c52cdb8cd8606f6f04685db8cb253`**. Neither is a release head yet.

**No composed `head_sha`/`tree_sha` is recorded**, because no composition has
passed its gates. That absence is the first `BLOCKED_UNKNOWN` in the manifest,
and it is recorded rather than filled with the most recent SHA.

---

## 5. CI, as measured — a Draft PR is not release evidence

Every named head was checked at
`repos/ProfessorZhi/serveros/commits/<sha>/check-runs`:

| PR | shard 1 | shard 2 | shard 3 | shard 4 | plugin | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| #500 | ✅ | ✅ | ❌ (3 new) | ✅ | ✅ | red |
| #503 | ❌ | ❌ | ❌ | ❌ | ✅ | red |
| #504 | ✅ | ✅ | ✅ | ❌ (1 new) | ✅ | red |
| #505 | ✅ | ✅ | ✅ | ❌ (1 new) | ✅ | red |
| #506 | ✅ | ✅ | ✅ | ❌ (1 new) | ✅ | red |
| #507 | ✅ | ✅ | ⏳ | ⏳ | ✅ | pending |
| #508 | ✅ | ⏳ | ⏳ | ⏳ | ✅ | pending |

The #500 failures are exactly the three `test_fleet_next_agent_monitor_activation_runtime_tick.py`
cases that #503 exists to fix. The recurring "1 new" failure on #504 / #505 /
#506 is
`tests/test_direct_lark_notify.py::CardCreateThenPatchTest::test_create_returns_an_id_and_patch_names_it`
(`'FAILED' != 'DELIVERED'`), which is a new baseline failure on those heads, not
a known one. It is named here rather than folded into "flaky".

**Consequence:** `BLOCKED_CI` on every head. No installation, no canary, no
plugin registration. Waiting for exact heads and a true 5/5 is the correct
state, not a delay to work around.

---

## 6. Per-device readiness

The order is Tokyo, then Dell, then FJ. A later device is gated on every earlier
device being accepted **and** independently accepted.

| device | order | local operator grant | reachability | measured headroom | verified Next-only rollback | state |
| --- | --- | --- | --- | --- | --- | --- |
| `device:tokyo-shared-dev-01` | 1 | **UNKNOWN** | **UNKNOWN** | **UNKNOWN** | **UNKNOWN** | `BLOCKED_UNKNOWN` |
| `device:llm-lab-dell` | 2 | **UNKNOWN** | **UNKNOWN** | **UNKNOWN** | **UNKNOWN** | `BLOCKED_DEVICE_ORDER` |
| `device:work-fj` | 3 | **UNKNOWN** | **UNKNOWN** (gateway timing out at capture) | **UNKNOWN** | **UNKNOWN** | `BLOCKED_DEVICE` when reachability is recorded |

No device is claimed ready. A gateway timeout is `BLOCKED_DEVICE`; it is never
retried until it becomes green, and it never turns two reachable machines into
"three machines online".

### Resource non-conflict

`BLOCKED_RESOURCE` is produced by a measured fact (`resource.checked = true,
resource.conflict = true`), not by a guess. Because the resource snapshot was
not collected for any device in this round, every device carries a resource
`UNKNOWN`, which blocks the canary. The engine deliberately has no path from
"not measured" to green.

---

## 7. Rollback, tested both ways

Two modes, because "roll back" means two things:

* `revert` — a previous Next revision exists; repoint `current` at it, restart
  the Next unit.
* `withdraw` — first Next install; stop, disable, remove the Next unit and the
  Next state root.

Refused by name: `ROLLBACK_NOTHING_INSTALLED`, `ROLLBACK_SAME_REVISION`,
`ROLLBACK_NO_DEVICE`, `ROLLBACK_UNKNOWN_STEP_KIND`, `ROLLBACK_TOUCHES_LEGACY`.

The tests assert, not just describe:

* a rollback request with nothing installed is refused (no fabricated success);
* `previous == installed` is refused — "a no-op wearing the word rollback";
* a first install produces `withdraw`, a second install produces `revert`;
* a step targeting the Legacy unit, or a path nested inside a Legacy root, is
  refused; a prefix sibling is allowed;
* `rollback_dry_run` reports `writes: []` and `executes: []`;
* a second dry run with `applied=<first plan>` reports `changed: false` and no
  pending steps;
* `execute_plan` with `applied=<receipt>` drives the injected executor for
  **zero** steps;
* `execute_plan` refuses without owner approval, outside `canary`, or without
  an evidence directory.

---

## 8. Tests

```text
python3 -m pytest -q -p no:cacheprovider tests/test_fleet_next_v1_release_captain.py
95 passed

python3 scripts/hermetic_tests.py tests/test_fleet_next_v1_release_captain.py -q
95 passed   (hermetic roots bound; no ability-management / hwz-ops needed)
```

The suite includes the two controls that stop it being decorative:

* **positive controls** — a fully green manifest reaches `READY`, `CANARY` and
  `PROMOTE`; without them every red test could be red because the checker sees
  nothing;
* **mutation controls** — three guards were removed one at a time and the suite
  went red each time:

  | guard removed | tests that failed |
  | --- | --- |
  | CI failure ignored (`elif conclusion != "success"` → `elif False`) | 2 |
  | namespace collisions ignored | 5 |
  | `UNKNOWN` treated as a value | 14 |

A fourth, structural control: `test_every_reason_code_is_reachable` walks a
table of one manifest per reason code and fails if any code in `REASONS` cannot
be produced. A vocabulary entry that nothing emits is a word for a state the
checker cannot actually report.

Also run alongside, because a new top-level directory and a new test file are
the usual ways this repository's own structural tests break:

```text
tests/test_repository_layers.py tests/test_no_node_absolute_paths.py tests/test_collection_scope.py
114 passed, 40 subtests passed
```

---

## 9. BLOCKED vs READY — the honest summary

| item | state | why |
| --- | --- | --- |
| composed release tree | `BLOCKED_UNKNOWN` | no composition accepted |
| hosted CI 5/5 on an exact head | `BLOCKED_CI` | every head red or pending |
| independent QA on that head | `BLOCKED_UNKNOWN` | #508 reports 43 passed locally; hosted CI unfinished |
| release artifacts + provenance | `BLOCKED_UNKNOWN` | nothing built for this release |
| Legacy pre snapshot | `BLOCKED_UNKNOWN` | not collected; identity never observed this round |
| Next namespace disjointness | `BLOCKED_UNKNOWN` | Next identity not yet assigned |
| Tokyo local operator grant | `BLOCKED_UNKNOWN` | not requested in this round |
| Dell / FJ | `BLOCKED_DEVICE_ORDER` / `BLOCKED_DEVICE` | Tokyo has not accepted |
| `main` merge, Legacy takeover, credentials, Feishu, Tailscale, paid model | `BLOCKED_OWNER` | outside the grant |

Nothing above is a request to proceed. It is the list a later round must
close, in this order, with evidence that names the same tree.

---

## 10. What the next run should do

1. Wait for #507 / #508 to finish and re-read `check-runs` for the **exact
   heads**; a moved head invalidates every earlier green.
2. Refuse to proceed while any new baseline failure remains (`test_direct_lark_notify.py`
   is the one to watch).
3. Build the artifacts from the accepted tree, record sha256 and provenance,
   and only then fill `artifacts`.
4. Have the Tokyo operator observe Legacy's identity and take the pre snapshot;
   fill `legacy.identity_observed`, `snapshot_pre`, and the Next namespace.
5. Ask the Tokyo local operator for the grant. Only then evaluate `CANARY`.
6. When the canary is accepted **independently**, hand Dell and then FJ the same
   gate set. A timeout stays `BLOCKED_DEVICE`.

This document does not promise that a future run will do any of the above. It
records what was true on 2026-10-29 and what the next gate is.
