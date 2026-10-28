# Fleet Next RC — Wave 16 composition

This directory holds the machine-readable composition ledger for the Wave 16
Draft RC. It is **not** an ability, a plugin, a service or a runtime artifact.

| file | what it is |
| --- | --- |
| `composition.json` | base #494, composed source SHA/tree, each P0 PR's own increment (file list + author-blob sha256), ancestry, historical Wave 12/13/14 blobs, the exact #497×#498 block, #425/#478/#434 matrix |

The human record is `docs/integration/FLEET-NEXT-RC-WAVE16-20261028.md`; the
claim is asserted by `tests/test_fleet_next_rc_wave16_ledger.py`.

## Composition

`#494 00503f9` → `+ #497 3db8ebf` → `+ #498 0096ba6 (3 commits)` →
`+ #499 02027c5`. Every author head is a merge parent. #498's branch base is
#488 `009662f`; its immediate parent is `f8e1b6f`, and its own delta is all
three commits.

## What is green, and what is blocked

* #497 core: **37 passed** — UNKNOWN receipt → retry stays non-green → a
  confirmed `DELIVERED`; typed CI subject / boot SHA; fake completed never
  green.
* #499 adapter: **80 passed, 21 subtests** — compat denied costs zero reads,
  CI SHA mismatch speaks the monitor's word, `read_request` rename.
* #480/#483: **86 passed, 11 subtests** — Legacy collision and shadow refusal.
* Historical ledgers: Wave 12+13 **21 passed, 41 subtests**; Wave 14
  **25 passed, 75 subtests** after its tree-scoped correction.
* **#498 block:** three runtime-tick tests skip on the author head (no #493)
  and fail on the composed tree: `health.reported` has no `backlog`, and full
  document/`ticks_report` equality includes wall-clock `last_tick`/`last_ok`.
  Owner handoff is in the record. No test is added to a baseline file.

## What this ledger refuses

* A composition rebuilt by cherry-pick: each author head must be a merge
  parent and an ancestor.
* A hand edit to an author file: every recorded file is compared to the
  author commit's blob.
* A rewritten Wave 12/13/14 history: the old ledgers keep their own blobs and
  their own trees; Wave 14 is re-scoped to `00503f9`, not weakened.
* A second notifier or a reintroduced #415 fix: both are already in #494.
* A Legacy move: no `fleet_mcp/`, `runtime/`, `services/`, `plugin/`,
  `deploy/`, `serverosd/` or `bin/` path may appear in the delta.
