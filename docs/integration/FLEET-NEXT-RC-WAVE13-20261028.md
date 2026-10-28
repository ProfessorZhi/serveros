# Fleet Next V1 — Wave 13: #487 + #477 + #481 + #480, and the tree-scoped ledger

**Status: Draft RC, integration-only.** No `main` merge, no force-push, no
release, no install, no service change, no OAuth/credential/Feishu/model call.
CODE / UNIT / CI only; REAL_DEVICE and PRODUCTION are **not measured**.

---

## 0. Identity

| | |
| --- | --- |
| branch | `feat/fleet-next-rc-wave13-20261028` |
| base (#487 current head) | `43efd5ce0bfbe61db335910fabb85c92a24ea8e1` — 5/5 hosted CI |
| composed source commit (before this ledger) | `0956e67d8b173a67c8cb27cecaeea1308413b599` |
| composed source tree | `61333635f3770be36d673784195b76d639cd125a` |
| ancestry, not patches | each author head is a **parent** of the merge that carries it |

This record, `tests/test_fleet_next_rc_wave13_ledger.py` and the tree-scoped
correction to `tests/test_fleet_next_rc_wave12_ledger.py` sit on top of the
composed source commit. They change no author file; they make the composition
checkable.

---

## 1. Inputs, in the order they were composed

| PR | head | what it is | own delta |
| --- | --- | --- | --- |
| #487 | `43efd5c` | Wave 12 RC: #471 + #472 on #479's exact head | base |
| #477 | `be2c1cc` | `fix(fleet): decide batch models from evidence and project grants` | 3 files, `+608/−28` |
| #481 | `9b7710a` | `feat(fleet-v1): one bounded tick of a filed batch, as a CLI` | 4 files, `+1432/−6` |
| #480 | `3777d13` | `feat(fleet-next): source-only parallel plugin preflight` | 4 files, `+637` |

The order is forced in two places, not merely chosen:

* `#480^ == be2c1cc` (#477), so #477 must be in the tree before #480.
* `#481^ == 0c58889`, which is already an ancestor of `43efd5c`, so #481 is
  independent of #477/#480 and could be placed between them.

Measured, not assumed:

```text
git merge-base be2c1cc 43efd5c  ->  7b4b9d6   (already an ancestor of #487)
git merge-base 9b7710a 43efd5c  ->  0c58889   (already an ancestor of #487)
git merge-base 3777d13 43efd5c  ->  7b4b9d6
```

### The three merge commits

| step | merge commit | parents | conflicts |
| --- | --- | --- | --- |
| + #477 | `f31f521` | `43efd5c`, `be2c1cc` | none (`ort`) |
| + #481 | `0085170` | `f31f521`, `9b7710a` | none (`ort`) |
| + #480 | `0956e67` | `0085170`, `3777d13` | none (`ort`) |

No file from #487 or from the three authors was edited by the composition.

---

## 2. The owner block, and why the repair is a re-scope rather than a new number

`tests/test_fleet_next_rc_wave12_ledger.py` pinned one LF-normalised sha256 per
composed file, including the Batch README, **from the Wave 12 tree
`f64d7af1`**. #481 legitimately edits that README (adds the `cli.py` ownership
row and the scheduler entrypoint section). On the Wave 13 tree the old
assertion produces:

```text
integrations/fleet-v1-milestone-batch/README.md
expected d3b5c1e59daca7da6daf545569f88bfc8a2ec6abe3ffda43be5c8e10a3e0e6cf
actual   3f479eec5a7643ab51e0a3e67a87cb841e537c1b37edaeb69d5961cb61104558
```

This is **not** repaired by:

* replacing the digest with `3f479eec` in the old table — that deletes the
  Wave 12 fact and leaves one number claiming two different trees;
* restoring the old README — that rejects a legitimate author change;
* weakening the assertion to "changed" or "contains" — that stops checking.

The repair has two halves, each with its own exact assertion:

1. **Wave 12 is tree-scoped.** `DIGESTS` keeps every original value. The new
   `test_the_wave12_tree_object_still_holds_every_recorded_digest` reads
   `f64d7af1:<path>` out of the object database and compares it to the table —
   including the README. The historical claim is now checked against the tree
   it was measured on, not against whatever the working tree has become.
   The four files no later author touched are still asserted on the working
   tree as well.
2. **Wave 13 records its own provenance.** `tests/test_fleet_next_rc_wave13_ledger.py`
   asserts, per file, that the working tree holds **the author commit's own
   blob** (`git cat-file -p <author>:<path>`), not merely a number written
   down. For the README that author is #481 `9b7710a`, and the ledger also
   asserts that the Wave 12 tree still holds the *other* digest — so "the
   README changed" is a recorded two-tree fact.

The same file also asserts that every author head is an **ancestor** of the
RC. A cherry-pick that reproduces the bytes would fail that assertion; that is
the difference between a composition and a copied tree.

---

## 3. What the ledger tests run against

```text
python3 -m pytest -q tests/test_fleet_next_rc_wave13_ledger.py
python3 -m pytest -q tests/test_fleet_next_rc_wave12_ledger.py
```

The ancestry and author-blob assertions read real git objects. The hosted
`suite` job therefore checks out `serveros` with `fetch-depth: 0` (this branch
changes `.github/workflows/tests.yml`); a shallow checkout would make these
pass locally and fail on the runner. No test is skipped by host shape: there
is no `skipUnless` and no baseline line for either ledger.

Local result on the composed tree (Python 3.12, full history):

| suite | result |
| --- | --- |
| Wave 12 ledger (tree-scoped) | **12 passed, 20 subtests** |
| Wave 13 ledger | **9 passed, 17 subtests** |

Harness CI is the authority for the final reading; this table is the local
reproduction.

---

## 4. Negative controls (mutation runs)

The ledger is only worth its digest if a wrong tree turns it red. Each of these
is run on a throwaway copy and reverted; the commands are recorded so they can
be repeated.

| mutation | expected | observed |
| --- | --- | --- |
| append one byte to the Batch README | Wave 13 per-file digest fails | see §7 |
| replace the README with the Wave 12 bytes | Wave 13 README digest fails | see §7 |
| delete the #480 author merge (rebuild by copy) | ancestry assertion fails | see §7 |
| edit a Legacy `fleet_mcp/` file | Legacy delta assertion fails | see §7 |

---

## 5. Legacy and Next boundary (#478)

* The composition delta `43efd5c..HEAD` contains no `fleet_mcp/`, `runtime/`,
  `services/`, `plugin/`, `deploy/`, `serverosd/` or `bin/` path. This is
  asserted by path prefix, and the `fleet_mcp` tree object is asserted
  byte-identical before and after.
* #480's synthetic preflight continues to refuse the Legacy backend plugin ID
  and Legacy MCP URL; nothing in this RC reads, changes or contacts a real
  Legacy backend.
* #489's QA tree (`integrations/fleet-next-rc-wave13-qa/`,
  `tests/test_fleet_next_rc_wave13_compose.py`) is **not** part of this RC and
  is asserted absent. The QA lane remains independent and is not modified.

---

## 6. Not merged, by decision

`#473` / `#474` and the `#407` V2 Draft are not merged. The #489 QA report
measured their merge-bases at `3f5ec20b` (#405), so a direct merge would carry
95/88 files including 20/18 under `fleet_mcp/` — the V2 tree, not the
sidecar. Their V1-only extraction needs an owner-authored rebase first.
Unchanged from the Wave 13 QA reading.

---

## 7. Evidence log

Filled from the actual runs; a failed run stays in this table as a failed run.

### 7.1 RED — reproduced before any repair

```text
python3 -m pytest -q tests/test_fleet_next_rc_wave12_ledger.py
SUBFAILED(file='integrations/fleet-v1-milestone-batch/README.md')
  TheCompositionIsTheAuthorsBytes::test_each_composed_file_still_holds_the_authors_content
1 failed, 11 passed, 15 subtests passed
```

The failure text is the one quoted in §2, byte for byte. The same failure is
what the hosted Wave 13 QA run `40658074157` shard 4/4 reports.

### 7.2 GREEN

```text
tests/test_fleet_next_rc_wave12_ledger.py   12 passed, 20 subtests passed
tests/test_fleet_next_rc_wave13_ledger.py    9 passed, 17 subtests passed
```

### 7.3 Negative controls

Recorded in §4; results appended when the runs complete.

### 7.4 Hosted CI

The run URL and shard readings for the final head are in the draft PR
description. Until that run exists, no green is claimed.

---

## 8. NOT_RUN

Creator, real device, real send/stop, live service, real plugin install,
OAuth/credential, paid model, GPU: **NOT_RUN**. REAL_DEVICE is not measured;
PRODUCTION is not measured. The old Fleet's services are untouched.

Cross: #425 #478 #434 #487 #489 #477 #481 #480 #473 #474.
