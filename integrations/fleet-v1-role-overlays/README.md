# L3 role overlays (#434 / ability-management #39)

Per-device **selection and admission** for the local Ability family. A role says
which already-catalogued Abilities a device attaches for one task; a task says
whether it is company work or an authorized personal idle job. Nothing here
defines an Ability, installs a payload, samples a GPU, or creates a Work Item.

## What is here

| file | what it is |
| --- | --- |
| `role-overlays.yaml` | the data: three roles, their per-task attach sets, native observations and three GPU gaps |
| `role_overlays.py` | a resolver that validates against a pinned catalogue and returns an attach set + an admission answer |
| `../../tests/test_fleet_v1_role_overlays.py` | 37 offline tests, including the two behaviours below |

```python
plan(document, role="dell-gpu-compute", task="company_model_serving",
     catalogue=<ability names at a revision>, catalogue_revision="<40-hex>",
     acceptance=<Work Engine record or None>, approval=None,
     company_active=False, device_state="UNKNOWN")
```

The result carries `attach`, `native`, `gaps`, `admission`, `work_item`,
`reports`, `forbidden`, `cross_device_sync`, `data_boundary` and a deliberately
`UNKNOWN` `observation` (installed/ready/effective are the Fleet observer's
facts, not this plan's).

## The three roles

| role | device | task class | attach | item / report |
| --- | --- | --- | --- | --- |
| `tokyo-development-primary` | `device:tokyo-shared-dev-01` | `company_requirement` | bootstrap + ability-management + runtime/workspace/work/information/test | Work Item only on Work Engine acceptance; no report |
| | | `company_delivery_report` | bootstrap + work-management + feishu-report | no new item; one report |
| `dell-gpu-compute` | `device:llm-lab-dell` (company A6000) | `company_model_serving` | bootstrap + runtime-management + test-management; three gaps | Work Item on acceptance; no eager report |
| | | `personal_idle_experiment` | bootstrap only | **no item, no report, no Feishu, local-only** |
| `work-fj-corporate-windows` | `device:work-fj` | `company_task` | bootstrap + runtime-management; native Windows adapter | Work Item on acceptance |
| | | `company_delivery_report` | bootstrap + feishu-report | no new item; one report |

A role never attaches the whole registry: every attach set is a strict subset of
the resolvable catalogue, and the union across all roles is still strict.

## Why this is a policy, not a fifth packaging story

* **#413 (face split).** A role is selection; the `plugin` face is the packaged
  bytes. A role does not create an artifact, version or cachebuster. An attach
  set can later be realized *through* the plugin face, but this file never
  writes it.
* **#421 (runtime-truth).** A source-level plan says nothing about an installed
  face. `observation.installed/ready` are `UNKNOWN` and `effective` is null; a
  device report is still required. `device_state` may only echo `UNKNOWN`,
  `OBSERVED` or `NOT_OBSERVED`; any other word is refused.
* **#437 (local package).** The package carries bytes and hashes; the overlay
  carries names and task policy. The overlay does not install, roll back or
  claim a package version.
* **`plugin/profiles/linux-work.yaml`.** That profile bundles the three things a
  Linux work node uses without deciding to and *recommends* the rest. A role is
  stricter: it attaches per task, so a report Ability is not in the requirement
  set just because the same device may later write a report.
* **L1 native GPU observation.** `gpu.readonly` and `core-lite.status` are
  observations from
  `native/node-core/contract/core-lite-status.v1.md` §6c; a Windows host uses
  the Windows adapter. The producer exists, but the same contract's §6d records
  the Fleet **reader** side of `gpu.readonly` as `STILL A GAP` — the overlay
  consumes the observation when present and does not claim the read path is
  wired. The overlay lists them under `native`, and validation refuses
  a native id that is also a catalogue Skill — wrapping native telemetry in a
  Skill would create a second identity for one provider. No GPU is sampled here.
* **A4 / A7 / A8.** The GPU gaps name the existing seams they consume:
  A4 runtime/resource readiness and lease, A7 automated testing & acceptance
  verdict, A8 build/release recoverable previous. The owner handoff said
  “A7 rollback”; in this repository A7 is acceptance and A8 owns the
  recoverable previous, so the data cites A7 for the verdict and A8 for
  rollback rather than inventing a seam. A gap is not an installed Ability, and
  this file adds no second resource or acceptance authority.
* **ability-management #39.** The registry, revision, compatibility, adoption
  and desired state remain ability-management's. This overlay is ServerOS
  selection policy over that one catalogue; it embeds no ability definition.
  `authority.local_skills` may only name the repository bootstrap (`serveros`);
  any other local name is refused, so a second catalogue cannot be smuggled in
  through the authority block.
* **`forbidden_everywhere`.** The document-level list is enforced into every
  plan (not inert data), and a plan may not omit it.

## The two non-negotiables

**NEGATIVE — personal GPU.** On the company A6000, `personal_idle_experiment`
requires `owner_approval` and a **stated** `company_idle`. An omitted company
load is `REFUSED_COMPANY_LOAD_UNKNOWN` (idle is not assumed); active company work
is `DEFER_COMPANY_FIRST`; a non-`True` approval (including `"yes"`) is
`REFUSED_APPROVAL_REQUIRED`. Its attach set is restricted to the personal
allowlist (`serveros`, runtime/workspace/test) — a report ability cannot be
attached to a task that forbids reporting. When allowed, it still creates **no
canonical Work Item** — a Work Engine acceptance record handed to it is ignored
— writes no company report, sends nothing to Feishu, forbids B3/B4, is
`local_only`, and cannot request a cross-device sync (the resolver refuses one).

**POSITIVE — Tokyo company requirement.** `company_requirement` never creates an
item on its own. It creates exactly one canonical Work Item only when the caller
presents the **requirement it is planning** plus an
`acceptance.authority == "work-engine"`, `accepted is True` (exactly `True`), a
non-empty `canonical_ref` and a matching `requirement_ref`. A foreign authority,
a truthy non-bool, a refusal, no acceptance, an empty item ref or an acceptance
for another requirement leaves the item uncreated, and the reason names which
test failed. The same Work Engine owns the item either way.

## Evidence

| level | state |
| --- | --- |
| CODE | this directory |
| UNIT | `python3 -m unittest -v tests.test_fleet_v1_role_overlays` — 37 PASS |
| HOSTED_CI | the Draft PR's workflow |
| INSTALLED / READY / EFFECTIVE | **NOT CLAIMED** — source-level plan only |
| REAL_DEVICE / GPU | **NOT_RUN** — no model, no GPU allocation, no service |

## Review TODO

* Independent reviewer: check the catalogue names against the pinned
  ability-management revision (this file validates names, not their semantics).
* Wire `tokyo-development-primary` into the Work Engine's requirement admission
  as a consumer; it must not become a second item store.
* The three GPU gaps need an L1/L3 owner before any implementation; they must
  consume A4/A7/A8 rather than re-sample or re-rule.
* A future composition (not this PR) may realize the attach sets through the
  plugin face; the overlay stays the policy.
