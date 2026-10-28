# `integrations/fleet-v1-rc-next/` — the RC-next integration lane (2026-10-28, round 2)

This directory holds **no product code**. It is the receipt for one integration
QA pass over the Fleet V1 release candidate, and it exists so that what blocks
the RC is *decidable* rather than remembered.

- The measured record — live PR SHAs, the exact composition recipe (13
  zero-conflict merges over #449 `4db2e711` → commit `c2e885e5`, tree
  `5a97605e`), the inherited-baseline vs cross-PR-new-failure separation, and
  every BLOCKED / DEPENDENCY item — is in
  [`docs/integration/FLEET-V1-RC-NEXT-20261028.md`](../../docs/integration/FLEET-V1-RC-NEXT-20261028.md).
- The two merge gates live in `tests/`, not here, because they are tests:
  - `tests/test_fleet_v1_rc_next_packaging.py` — the composition's declared
    artifact kinds must be the bytes it ships. **Green**: #462 narrowed the rule
    builder-side (L2 kinds refused by name; the undeclared-kind direction limited
    to the two build faces), and the gate keeps the bite with positive controls.
  - `tests/test_fleet_v1_rc_next_notifier_seam.py` — #459's batch needs one
    public `send_text(*, delivery_key, milestone_key, outcome, text, why)` on the
    notifier. **Green**: #460 published it and #464 supplies the registration
    side; the gate's `expectedFailure` turned into an unexpected success exactly
    as designed, and the decorator is gone.

Nothing here claims REAL_DEVICE, CLIENT_E2E or PRODUCTION. No device was
touched, no service was started, no message was delivered.

## Replaying the tree

The composed tree is an object-database artifact, not a branch. Starting from
#449's head, merge in this order with `git merge-tree --write-tree` +
`git commit-tree` (the full oid list is in §3 of the document):

```
459 462 464 460 431 447 451 448 450 453 456 457 458
```

Only three of those positions are causal: `459` before `462`/`464` (their base
is #459's branch), `447` before `451` (#451 is #447's stacked patch), `453`
before `456`. `461` and `465` are deliberately **not** in the list: both sit on
#407, whose 85-file Draft is excluded (document §2/§7).

## Running the gates

```
python -m pytest -q tests/test_fleet_v1_rc_next_packaging.py \
                    tests/test_fleet_v1_rc_next_notifier_seam.py
```

On the RC-next tree this is **22 passed**. Both gates flip when their rule flips
— the document records the two probes (widening `build_face_kinds()` reddens
three tests; removing `send_text` reddens three, one of them with #423's own
`NOTIFIER_SEAM_MISSING`). Neither is supposed to be edited to go green.
