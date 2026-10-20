<!-- GENERATED FILE - DO NOT EDIT BY HAND -->

# G1 · Device Ability coverage matrix

> **GENERATED / NON-AUTHORITATIVE / REPRODUCIBLE.**
> A view of the authority's manifest and of one sanitized device observation. Editing
> this file moves nothing; the next render overwrites it and `--check` reports drift.
> Authority: `ProfessorZhi/ability-management` `abilities-manifest.yaml` @ `4a46f2b178e5f93de12b7a27c9d0df00e45683ea`.
> Vocabulary: `product-tree.yaml` @ the same revision (21 L2 families).
> Basis: `product_role.primary` for realization, `revisions[].installations[]` for
> installation, and a device observation snapshot for observation.

This file is **not a registry** and **not a callability claim**. It separates four
facts that are easy to collapse into one integer:

| word | comes from | means |
|---|---|---|
| declared | product vocabulary | the family exists (always 21 rows) |
| realized | manifest `product_role.primary` | some identity owns this responsibility |
| installed | manifest `revisions[].installations[]` | the **authority** records an installation on this device id |
| observed | device snapshot | the **device** reports something matching an identity |

`observed` is split into `strong` (a `stable_id` / canonical-source match) and
`probable` (name only). The authority's rule is that name-only is PROBABLE MATCH,
never SAME, so a probable match is never counted as identity.

`ready` and `grant` are `UNKNOWN` throughout: a scan snapshot carries no health and
no per-principal policy observation. **UNKNOWN is not false.** `NOT_OBSERVED` means
nobody looked this round; `UNRESOLVED` means the authority declares no device id at
all. `effective` is always a list here — the per-ability rule lives in `effective.py`.

## Matrix

| Family | Name | Realized | tokyo-linux installed / observed (strong+probable) | windows-dell-work installed / observed (strong+probable) | dell-a6000-linux installed / observed (strong+probable) | ready | grant | gap |
|---|---|---|---|---|---|---|---|
| G1 | Ability Governance | 1 | 0 / 0+0 | 1 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| A1 | Session Continuity & Handoff | 1 | 0 / 0+0 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| A2 | Memory Retrieval & Correction | 1 | 0 / 0+0 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| A3 | Workspace / Git / PR | 1 | 0 / 0+0 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| A4 | Runtime & Resource Management | 1 | 0 / 0+0 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| A5 | Fleet / Device Handoff | 1 | 0 / 0+0 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| A6 | Delegation & Parallel Execution | 1 | 0 / 0+0 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| A7 | Automated Testing & Acceptance | 3 | 0 / 1+0 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| A8 | Build & Release | 1 | 0 / 0+0 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| A9 | Production Observation | 1 | 0 / 0+0 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| A10 | Production Change | 1 | 0 / 0+0 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| B1 | Source Intake & Follow | 0 | 0 / 0+0 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN | NO_PRIMARY_REALIZATION |
| B2 | Structured Understanding | 1 | 1 / 0+1 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| B3 | Requirements | 0 | 0 / 0+0 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN | NO_PRIMARY_REALIZATION |
| B4 | Work Management & Admission | 1 | 1 / 0+1 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| B5 | Research & Evidence | 1 | 0 / 0+0 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| B6 | Solution Design | 2 | 2 / 0+2 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| B7 | Implementation Delivery | 1 | 0 / 0+0 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| B8 | Reporting | 2 | 1 / 0+1 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| B9 | Document / Knowledge Stewardship | 1 | 1 / 0+1 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |
| B10 | Human Control & Notification | 1 | 0 / 0+0 | 0 / NOT_OBSERVED | UNKNOWN | UNKNOWN | UNKNOWN |  |

## Counts

- Families: **21**; with a primary realization: **19**; without: **2**.
- Ability identities: **61**; without `product_role.primary`: **38**.
- `tokyo-linux` (OBSERVED): installed identities 19; observations 70 (strong 19, probable 27, ambiguous 1, unmatched 23); identities matched strong 7 / probable 20.
- `windows-dell-work` (NOT_OBSERVED): installed identities 13; observations UNKNOWN (strong UNKNOWN, probable UNKNOWN, ambiguous UNKNOWN, unmatched UNKNOWN); identities matched strong UNKNOWN / probable UNKNOWN.
- `dell-a6000-linux` (UNRESOLVED): installed identities UNKNOWN; observations UNKNOWN (strong UNKNOWN, probable UNKNOWN, ambiguous UNKNOWN, unmatched UNKNOWN); identities matched strong UNKNOWN / probable UNKNOWN.

## Gaps

- **B1 · Source Intake & Follow** — NO_PRIMARY_REALIZATION; cross-referenced by 3 identities (`ProfessorZhi/document-stewardship`, `ProfessorZhi/information-management`, `microsoft/playwright-mcp`). It is unowned, not absent.
- **B3 · Requirements** — NO_PRIMARY_REALIZATION; cross-referenced by 3 identities (`ProfessorZhi/design-and-contract`, `ProfessorZhi/work-management`, `unknown/grill-me`). It is unowned, not absent.
- **38 of 61 identities declare no `product_role.primary`.** They cannot be attributed to a family (not even as zero), so every family count above is a lower bound. The ones the authority records as installed are listed below.
- **`windows-dell-work` was NOT_OBSERVED.** The authority manifest declares device `dell-work` (os: windows) and records installations for it, but no observation snapshot for this device class was supplied in this round. Authority installation records are not an observation; mark the observation axes UNKNOWN rather than inferring them.
- **`dell-a6000-linux` is UNRESOLVED.** The authority manifest declares no device id for a Dell A6000 Linux node. Mapping it to `dell-work` would conflate a Windows device with a Linux one; the identity is UNKNOWN, so every device-dependent word is UNKNOWN rather than false.

### `tokyo-linux` — installed identities

- `ProfessorZhi/archguard/feature-developer`
- `ProfessorZhi/archguard/project-semantics-discovery`
- `ProfessorZhi/design-and-contract`
- `ProfessorZhi/document-stewardship`
- `ProfessorZhi/feature-development`
- `ProfessorZhi/feishu-report`
- `ProfessorZhi/feishu-space-management`
- `ProfessorZhi/fjllm-switch`
- `ProfessorZhi/information-management`
- `ProfessorZhi/inventory-publishing`
- `ProfessorZhi/muc-playwright-e2e`
- `ProfessorZhi/work-management`
- `VenusFennn/Backend-Solution-Design`
- `VenusFennn/Frontend-Solution-Design`
- `adewale/good-pr`
- `anthropics/claude-plugins-official/frontend-design`
- `jackwener/opencli`
- `openai/codex/codex-pr-body`
- `unknown/grill-me`

### `windows-dell-work` — installed identities

- `ProfessorZhi/ability-management`
- `ProfessorZhi/archguard`
- `ProfessorZhi/meta-cc`
- `ProfessorZhi/vision`
- `VenusFennn/Backend-Solution-Design`
- `VenusFennn/Frontend-Solution-Design`
- `anthropics/claude-plugins-official`
- `jackwener/opencli`
- `openai/codex-builtin`
- `openai/codex-curated-remote`
- `openai/codex-node-repl`
- `openai/codex-primary-runtime`
- `zavora-ai/computer-use`

### `tokyo-linux` — unclassified identities with an installation

- `ProfessorZhi/archguard/feature-developer`
- `ProfessorZhi/archguard/project-semantics-discovery`
- `ProfessorZhi/feature-development`
- `ProfessorZhi/feishu-space-management`
- `ProfessorZhi/fjllm-switch`
- `ProfessorZhi/inventory-publishing`
- `ProfessorZhi/muc-playwright-e2e`
- `VenusFennn/Backend-Solution-Design`
- `VenusFennn/Frontend-Solution-Design`
- `adewale/good-pr`
- `anthropics/claude-plugins-official/frontend-design`
- `jackwener/opencli`
- `openai/codex/codex-pr-body`

### `windows-dell-work` — unclassified identities with an installation

- `ProfessorZhi/archguard`
- `ProfessorZhi/meta-cc`
- `ProfessorZhi/vision`
- `VenusFennn/Backend-Solution-Design`
- `VenusFennn/Frontend-Solution-Design`
- `anthropics/claude-plugins-official`
- `jackwener/opencli`
- `openai/codex-builtin`
- `openai/codex-curated-remote`
- `openai/codex-node-repl`
- `openai/codex-primary-runtime`
- `zavora-ai/computer-use`

## One bounded request per device class

Command: `serverosctl ability resolve --json --request <request> --ability-repo <ability-management checkout> --ability-rev origin/main`

### `tokyo`

- status: `UNKNOWN`
- exit_code: 2
- device_ref: device:tokyo-shared-dev-01
- runtime: claude-code
- semantic_owner: B2
- cross_refs: `B1`
- inputs_seen: `ability`
- inputs_missing: `device_capability`, `device_profile`, `device_binding`, `agent_runtime`, `runtime_health`, `policy`, `task_context`
- detail: no input refused, and these were never supplied: the provider set
- source: ProfessorZhi/ability-management@4a46f2b178e5 (61 abilities, composed from 66 manifest entries)

### `windows_dell_work`

- status: `NOT_RUN`
- reason: No trusted observation channel to this device class was available in this round, and Windows changes are outside this lane's authorization. The authority's installation records for `dell-work` are not execution evidence.

### `dell_a6000_linux`

- status: `NOT_RUN`
- reason: The authority declares no device id for this class, so there is no `device_ref` to resolve against. FAIL CLOSED: no id was inferred from a product name.

The Tokyo request is evidence about the **conjunction**, not a success:
the resolver named seven axes it was never given, including `policy`. The
other two device classes have no `device_ref` this round and are NOT_RUN;
no id was inferred from a product name.

## Regenerate

```bash
python3 integrations/device-ability-projection/coverage.py --manifest <ability-management>/abilities-manifest.yaml --families integrations/device-ability-projection/fixtures/families.json --devices integrations/device-ability-projection/fixtures/coverage-devices.json --revision 4a46f2b178e5f93de12b7a27c9d0df00e45683ea --evidence integrations/device-ability-projection/fixtures/resolve-evidence.json
```
