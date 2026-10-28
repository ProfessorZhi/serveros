# `integrations/fleet-v1-rc-next/wave12/` — the Wave 12 composition receipt

This directory holds **no product code**. It is the pointer for one integration
pass: composing #471 and #472 onto #479's exact head by ancestry, so the RC that
closes Wave 7's two remaining hosted failures carries the **authors' own
commits** rather than a re-applied copy of their patches.

| | |
| --- | --- |
| branch | `integrate/fleet-v1-rc-wave12-20261028` |
| composed RC | `26ad7245`, tree `f64d7af1` |
| base | #479 head `cd1485f` |
| parents | `d38d47a` (RC + #471 `7b4b9d66`) and `0c588896` (#472) |
| delta | 4 files, `+215/−18`, zero conflicts, no `fleet_mcp/**` |

Read [`docs/integration/FLEET-NEXT-RC-WAVE12-20261028.md`](../../../docs/integration/FLEET-NEXT-RC-WAVE12-20261028.md):

* §2 why this is not #484 with a new name (blob table: same bytes, different
  history);
* §4 the two ids, red before and green after, with the exact failure text;
* §6 the digests that keep the composition honest, including the product file
  that must **not** move;
* §9 the phase-2 map with measured merge-bases and per-PR test runs.

The guard that keeps §6 true is
[`tests/test_fleet_next_rc_wave12_ledger.py`](../../../tests/test_fleet_next_rc_wave12_ledger.py):
11 tests, green on the composition and measured red (11 failed) on the
pre-composition tree — a fidelity digest per file, the declaration-versus-tree
agreement for the payload, and the five refusal cases #472 kept.

Nothing here claims REAL_DEVICE or PRODUCTION. No device, service, credential or
model was touched; NEXT remains shadow and zero-write, per #478.
