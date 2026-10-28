# Fleet Next RC — Wave 18 composition

This directory holds the machine-readable composition ledger for the Wave 18
Draft RC. It is **not** an ability, a plugin, a service or a runtime artifact.

| file | what it is |
| --- | --- |
| `composition.json` | base #500, composed source SHA/tree, #503/#504 own increments with author-blob sha256, the Wave 12/13/14/16 historical blobs, the Gate Matrix and the #425/#478/#434 release matrix |

The human record is `docs/integration/FLEET-NEXT-RC-WAVE18-20261029.md`; the
claim is asserted by `tests/test_fleet_next_rc_wave18_ledger.py`.

## Composition

`#500 80220a4` → `+ #503 b7f1f61` → `+ #504 6286bfb`, both by `--no-ff`
ancestry. #503 is one test file; #504 is the monitor's #501 P0-A/P0-B and the
deliveryId-only P1.

## What is green

* #503 runtime tick: **3 passed** (the three #500 composition failures).
* #504 monitor core: **52 passed, 4 subtests** — unauthorized CI device is
  blocked, UNKNOWN/EXPIRED never leaves exit 0, deliveryId-only is delivered
  under the native run id.
* #499 adapter: **80 passed, 21 subtests** — the two #490 declared-reader
  failures stay gone.
* #480/#483 Legacy: **86 passed, 11 subtests** — collision and shadow refusal.
* Historical ledgers: **70 passed, 196 subtests**; Wave 16 is re-scoped to
  `80220a4`, not weakened.

## What this ledger refuses

* A composition rebuilt by cherry-pick: each author head must be a merge
  parent and an ancestor.
* A hand edit to an author file: every recorded file is compared to the
  author commit's blob.
* A rewritten Wave 12/13/14/16 history: old ledgers keep their own blobs and
  trees.
* A second notifier or a reintroduced #415 fix: both are already in #500.
* A Legacy move: no `fleet_mcp/`, `runtime/`, `services/`, `plugin/`,
  `deploy/`, `serverosd/` or `bin/` path may appear in the delta.
* A release/device/production claim: those Gate Matrix rows are `NOT_RUN`.
