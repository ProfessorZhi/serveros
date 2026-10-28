# `integrations/fleet-v1-rc-next/` — the RC-next integration lane (2026-10-28)

This directory holds **no product code**. It is the receipt for one integration
QA pass over the Fleet V1 release candidate, and it exists so that the two
things that can block the RC are *decidable* rather than remembered.

- The measured record — live PR SHAs, the exact composition recipe (10
  zero-conflict merges over #449 `4db2e711` → tree `5113cdc6`), the
  inherited-baseline vs cross-PR-new-failure separation, and every BLOCKED /
  DEPENDENCY item — is in
  [`docs/integration/FLEET-V1-RC-NEXT-20261028.md`](../../docs/integration/FLEET-V1-RC-NEXT-20261028.md).
- The two merge gates live in `tests/`, not here, because they are tests:
  - `tests/test_fleet_v1_rc_next_packaging.py` — the composition's declared
    artifact kinds must be the bytes it ships (`l2-node`, `l2-hub` are undeclared
    today; `scripts/build.py#verify_artifact_kinds` refuses the payload).
  - `tests/test_fleet_v1_rc_next_notifier_seam.py` — #459 needs one public
    `send_text(*, delivery_key, milestone_key, outcome, text, why)` on #423's
    `MilestoneNotifier`, and the batch must never reach for `_send`.

Nothing here claims REAL_DEVICE, CLIENT_E2E or PRODUCTION. No device was
touched, no service was started, no message was delivered.

## Replaying the tree

The composed tree is an object-database artifact, not a branch. Starting from
#449's head, merge in this order with `git merge-tree --write-tree` +
`git commit-tree` (the full SHA list and the resulting oids are in §3 of the
document):

```
431 447 451 448 450 453 456 457 458 459
```

`447` before `451` matters: #451 is #447's stacked patch. Everything else
follows the authors' own bases, and #459 comes last because its base is #449's
branch.

## Running the gates

```
python -m pytest -q tests/test_fleet_v1_rc_next_packaging.py \
                    tests/test_fleet_v1_rc_next_notifier_seam.py
```

On the RC-next tree this is `16 passed, 1 xfailed, 1 failed` — the single red is
the packaging declaration gate, and the xfail is the sender-seam gate waiting on
#423. Both flip by themselves when their owner lands the fix; neither is
supposed to be edited to go green.
