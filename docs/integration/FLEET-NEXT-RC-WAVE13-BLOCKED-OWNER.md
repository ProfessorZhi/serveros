# Fleet Next RC Wave 13 — compose candidate, BLOCKED_OWNER

- Date: 2026-10-28
- QA branch: `qa/fleet-next-rc-wave13-compose-20261028`
- #487 exact head: `d8dc1f92b4252338e52271ca3f74b4d05ee39fbc`
- #487 parent: `26ad7245d2b065f96edad5aecf271d4e1cd1bd23`
- Merge-base #487 / main: `e69fc0337ae5d33000478e3d5162077f525bc8cc`
- Source order: **#487 → #477 → #481 → #480**

## Exact composition

| PR | head | parent | merge-base with #487 | merge commit |
|---|---|---|---|---|
| #487 | `d8dc1f9` | `26ad724` | — | base |
| #477 | `be2c1cc` | `7b4b9d6` | `7b4b9d6` | `950d05c` |
| #481 | `9b7710a` | `0c58889` | `0c58889` | `0ca9b8b` |
| #480 | `3777d13` | `be2c1cc` | `7b4b9d6` | `f8a7e13` |

The order is forced where it matters, not merely chosen:

- `#480^ == be2c1cc` (#477), so #477 must be in the tree before #480; #480's
  merge-base with #487 is also `7b4b9d6`, already an ancestor of #487.
- `#481^ == 0c58889`, and `0c58889` is already an ancestor of #487, so #481
  is independent of #477/#480.
- All three merges were `ort` clean. The P2 source delta is exactly #477's
  3 files + #481's 4 files + #480's 4 files.

No file from #487 or from the three authors was edited.

## The owner block

`#487`'s `tests/test_fleet_next_rc_wave12_ledger.py` pins the exact
LF-normalised README byte string for the Wave 12 composition. `#481`
legitimately edits that README (adds the `cli.py` ownership row and updates the
seam section). The composed tree therefore produces:

```text
integrations/fleet-v1-milestone-batch/README.md
expected d3b5c1e59daca7da6daf545569f88bfc8a2ec6abe3ffda43be5c8e10a3e0e6cf
actual   3f479eec5a7643ab51e0a3e67a87cb841e537c1b37edaeb69d5961cb61104558
```

The old test, unchanged, fails with exactly:

```text
FAILED tests/test_fleet_next_rc_wave12_ledger.py::
  TheCompositionIsTheAuthorsBytes::
  test_each_composed_file_still_holds_the_authors_content[
  file='integrations/fleet-v1-milestone-batch/README.md']
```

This is **BLOCKED_OWNER**. It is not repaired by changing the digest, by
copying the old README back, or by weakening the test:

- #487 owner must re-derive the Wave 12 ledger for a composition that includes
  #481's README (the ledger's own message says so), or
- #481 owner must split/ rebase the README change so it does not enter this
  composition.

This QA lane does neither.

## Producer tests on the composed tree

| suite | result |
|---|---|
| #477 model evidence + CreateGrant | **45 passed, 32 subtests** |
| #481 bounded CLI | **24 passed** |
| #480 parallel plugin | **16 passed, 11 subtests** |
| Wave 13 QA suite | **7 passed** |
| #487 Wave 12 ledger | **11 passed, 1 failed, 15 subtests** |
| combined #487/P2/QA targeted | **235 passed, 1 failed, 88 subtests** |

The only failure is the README digest subtest above. #487's other ledger
assertions pass, as do the Wave 7 ledger, plugin package, batch family and
registration suites.

## Source archives

| product | archive sha256 | content sha256 | members | Skills |
|---|---|---|---|---|
| canonical `fleet_v1_9skill` | `7320129e…e12db` | `6e8c6e98…6e93b` | 15 | 9 |
| standalone `chatgpt_fleet_next_7member` | `14b3b4ee…65e2` | `df15ee37…a7e1` | 7 | 4 |

The 7-member archive was rebuilt from the in-tree builder; both archive and
content digests match. The 9-Skill archive is the canonical external artifact
pinned by #480; its count and both digests are checked, not substituted.

## Legacy Fleet boundary

- The P2 delta contains no `fleet_mcp/`, `runtime/`, `services/`, `plugin/` or
  `deploy/` path. `legacy_surface_touched()` is `[]`.
- #480's synthetic preflight refuses the Legacy backend plugin ID and Legacy
  MCP URL (`LEGACY_ID_UPDATE_FORBIDDEN`, `NEXT_TAKEOVER_URL`).
- A Next shadow write is `NEXT_SHADOW_WRITE_FORBIDDEN`, with
  `creator_calls=0` and `network=False`.
- No real Legacy backend ID or URL is read, changed, installed or contacted.

## V1-only extraction prerequisites (read-only evaluation)

Separate read-only worktree:
`worktrees/serveros/SRV-0011-fleet-rc-wave13-sidecar-ro-20261028`, branch
`readonly/fleet-rc-wave13-sidecar-20261028`, at #487 `d8dc1f9`. Nothing was
merged from either sidecar line.

### #473 Hub identity sidecar

- Head `a097b27d17c184d3222d982991a90cc04283fea3`, parent
  `ccd045258ef2d62041630823ff6c5b9011cede03`.
- Merge-base with #487 is `3f5ec20b` (`#405`), so a direct merge would carry
  **95 files, +11470/−619, 20 under `fleet_mcp/`** — the V2 tree, not just the
  sidecar.
- #473's own delta is **5 files**: `fleet_mcp/identity_adapter_port.py`,
  `fleet_mcp/registry.py`, `integrations/fleet-v1-l2-release/l2_release.py`,
  `tests/test_fleet_v1_hub_authority_release.py`,
  `tests/test_fleet_v1_node_dist_pin.py`.
- V1-only prerequisites: extract #466 `2634882…` (3 files, parent #407
  `9538238…`) and #467 `ccd0452…` first, then #473's 5-file delta; #466/#467
  must themselves be extracted onto a V1 base, not merged through #407.
- Status: `V1_ONLY_EXTRACTION_REQUIRES_OWNER`; not merged.

### #474 receipt secret guard

- Head `0ad3c078a7d4d865ff7d8c54d01a08235237f8ec`, parent
  `1a162c44b61f4122fbaa17564a2b3d98949d1d6d` (#465).
- Merge-base with #487 is `3f5ec20b`, so a direct merge would carry **88
  files, +8394/−566, 18 under `fleet_mcp/`**.
- #474's own delta is **2 files**: `fleet_mcp/mcp_http.py`,
  `tests/test_fleet_v1_receipt_secret_guard.py`.
- Its parent #465 is **4 files** (`cloudcli_native.py`, `mcp_http.py`,
  `providers/cloudcli.py`, `test_fleet_v1_receipt_truth_projection.py`) whose
  parent is #407 `9538238…`.
- V1-only prerequisites: extract #465's exact 4-file delta + #474's 2-file
  delta onto a V1 base; keep #468's upper receipt projection separate. Do not
  merge #407.
- Status: `V1_ONLY_EXTRACTION_REQUIRES_OWNER`; not merged.

### #407

The 85-file V2 Draft `#407` head `9538238…` is not merged, copied or used as a
base. Both extraction paths above would need an owner-authored V1-only rebase
before they can be measured against #487.

## Permissions and NOT_RUN

- Edits: only the new Wave 13 QA module, test and this doc.
- No `fleet_mcp/**`, runtime, service, plugin manifest, OAuth, credential,
  secret, paid model, GPU, real Feishu/IM or real device operation.
- Creator, real device, real send/stop, live service and real plugin install:
  **NOT_RUN**. Old Fleet remains untouched.
- Hosted CI: pending at draft creation; the #487 ledger is expected to stay red
  until an owner resolves the README digest. Not claimed green.

Cross: #425 #478 #434 #487 #477 #481 #480 #473 #474.
