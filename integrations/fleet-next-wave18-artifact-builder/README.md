# Wave 18 · Next release artifact builder

Status: **Draft · SOURCE/OFFLINE only · GREEN_ARTIFACTS / BLOCKED_OWNER**

This directory builds the four Next release units as **real artifacts**: deterministic
`.tar.gz` files with SHA manifests, a provenance record for every source
transform, isolation booting with no checkout and no neighbour, a negative
control, independent rollback against a Legacy 0.1.0 fake, and the #483/#480
release-footprint veto.

It does not modify `fleet_mcp/**`, `runtime/**`, `scripts/release.py`, Legacy
configuration, or the V2-rooted #461/#467/#473 files. The D7 dependency is
vendored byte-for-byte from #431 `517ef91` at `vendor/l2_release.py` with
`vendor/VENDORED.json` provenance. Nothing is installed,
started, bound, authenticated or sent. **REAL_DEVICE NOT_RUN.**

## What it builds

| unit | files | role | depends on |
|---|---:|---|---|
| `fleet-runtime-base` | 19 | shared primitives, pinned in both directions | — |
| `fleet-authority` | 3 | the ONE `fleet/transport.py` + contract + manifest | — |
| `fleet-hub` | 13 | Hub modules, no Node file | Base, Authority |
| `fleet-node` | 2 | Node connector, no Hub file | Base |
| `fleet-next-composition` | 0 | pins the four units and the compensating rollback order | — |

The unit manifest is `serveros/artifact.v1`, the same shape #431's D7 work
writes and `scripts/release.py` reads. Every tarball is USTAR + gzip with
`mtime=0`, empty filename, uid/gid 0, sorted members and mode 0644, so two
builds of the same commit are **byte-identical**.

`same_version_required` is `false`: D7 gives each kind its own version, and the
Hub and Node are independently installable given the Base and (for the Hub) the
Authority. The composition is an optional tested set with a compensating
rollback order, not a claim that the units are atomic.

## Why four units, not two

The measured tree has 19 modules both halves genuinely need (`config`, `errors`,
`identity`, `providers.cloudcli`, …). Copying them into both artifacts would be
duplication dressed as independence. Shipping two 180-file packages and calling
them separate would be worse. The Base is a **pinned allowlist** checked in both
directions: a shared module not on the list fails, and a listed module the
closures no longer share fails too. The only identity implementation is
`fleet/transport.py`; no unit defines a second `DeviceRef` or `DeviceRegistry`.

## Provenance

`evidence/PROVENANCE.json` records, for each transform:

* patch id, path and SHA256;
* the owner source commit (`CUT-A` = #461 `b042f515`, rebased to
  `main@e69fc03`; `CUT-B` = #505 `3512c55`);
* for every target file: original blob SHA, original SHA256, result SHA256 and
  result blob SHA;
* `applied_in: "isolated git-archive copy"`, `live_tree_modified: false`;
* the source commit/tree and the deterministic `git archive` digest.

The build never reads the working directory as an artifact input: it refuses a
dirty `fleet_mcp/`/`fleet/` and archives a commit.

## Commands

```bash
# RED: what the unpatched tree actually is
python3 -I integrations/fleet-next-wave18-artifact-builder/wave18_builder.py \
  --repo . --commit HEAD --out /tmp/w18-baseline --mode baseline --json

# GREEN: build twice, verify, isolate, negative control, rollback
python3 -I integrations/fleet-next-wave18-artifact-builder/wave18_builder.py \
  --repo . --commit HEAD --version 1.0.0+wave18 \
  --out /tmp/w18-evidence --json

# tests
python3 -m pytest tests/test_fleet_next_wave18_artifact_builder.py -q
```

## Verdict

* `technical_state: GREEN_ARTIFACTS` — tarballs build, verify, boot in
  isolation, survive the negative control and roll back without moving the
  Legacy fake.
* `source_state: BLOCKED_OWNER` — the CUT-A/CUT-B transforms are applied to an
  isolated copy; the `fleet_mcp/**` owner has not landed them.
* `decision: BLOCKED_OWNER`, `release_permitted: false`.
* `real_device: NOT_RUN`.
