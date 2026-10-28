# Fleet Next RC — Wave 14 composition record

This directory holds the machine-readable composition ledger for the Wave 14
Draft RC. It is **not** an ability, a plugin, a service or a runtime artifact.
It records what the integration branch composed and how a reviewer can
re-derive it.

| file | what it is |
| --- | --- |
| `composition.json` | base, composed source SHA/tree, each PR's own increment (file list + author-blob sha256), merge commits, Legacy boundary, preserved Wave 12/13 blobs, offline-packaging evidence, gate readings |

The human record is `docs/integration/FLEET-NEXT-RC-WAVE14-20261028.md`; the
claim is asserted by `tests/test_fleet_next_rc_wave14_ledger.py`.

## What the ledger refuses

* A composition rebuilt by cherry-pick: every author head must be an ancestor
  of the RC, and the merge commit must name it as a parent.
* A hand edit to an author file: every recorded file is compared to
  `git cat-file -p <author>:<path>`.
* A second notifier: #423's `milestone_notify.py` must be the only file with
  that name, and the Monitor's `notifier.py` must stay an adapter.
* A Legacy move: no `fleet_mcp/`, `runtime/`, `services/`, `plugin/`,
  `deploy/`, `serverosd/` or `bin/` path may appear in the delta.

## Offline

All four composed packages import in an isolated interpreter
(`python3 -I`) with `socket` disabled. No credential, service, device,
Feishu/IM call, paid model or GPU is read or contacted by the ledger.
