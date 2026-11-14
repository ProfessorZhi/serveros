# Owner-access helper implementation plan

## Overview

Implement the approved helper proposal only. The profile is trusted local
configuration; callers select root references and names, never authority or
arbitrary paths. Existing runtime/create policy and provider files are not
modified in this workstream.

## Phase 1 — Strict immutable configuration and lexical paths

Objective: compile an explicit owner profile and plan a portable project path.

Stages:

1. Add failing tests in `tests/test_fleet_owner_access.py` for absent config,
   explicit maximum permission, exact booleans, unknown/missing fields, immutable
   snapshots, root-reference shape and empty mappings.
2. Add failing tests for POSIX/Windows absolute roots, raw relative components,
   overlapping/duplicate roots, device/UNC/drive-relative roots, malformed root
   values, Windows reserved root names and cross-platform project-name aliases.
3. Implement `fleet_mcp/owner_access.py` with a `ValueError` subtype, frozen
   dataclass, parsing and lexical planning. Root mappings are defensively copied
   to `MappingProxyType` and revalidated for direct construction.

Acceptance: all lexical tests pass on Linux without touching root paths.
`local_check` requires a strict bool; only a valid `OwnerAccessProfile` is
accepted. Tests patch filesystem probes to fail if lexical mode performs IO.
Errors identify the invariant without echoing arbitrary raw config.

Dependencies: none beyond Python standard library and existing test runner.

## Phase 2 — Local filesystem boundary

Objective: reject unsafe/existing destinations before the creation owner acts.

Stages:

1. Add failing tests using a private artifact-root temporary directory for a
   missing root, file root, symlink root, intermediate symlink, dangling target,
   existing target file/directory, mocked reparse attribute and stat errors.
2. Implement `lstat` ancestor inspection for matching local path flavors. Reject
   reparse points in every existing ancestor. A non-native Windows path on Linux
   is an explicit error in local mode. No `resolve`, `mkdir` or writes occur.
3. Verify a valid new destination returns unchanged and remains nonexistent.

Acceptance: focused tests pass; no writes except test-owned artifacts. Tests
exercise checks at every ancestor and target; do not claim a race-free create.

Dependencies: Phase 1 passing.

## Phase 3 — Regression and handoff

Objective: provide a clean integration commit to the core owner.

Stages: run helper tests plus existing runtime/create-policy tests in a transient
user unit with 2 GiB memory, zero swap, one CPU quota, a bounded timeout and no
Python bytecode or pytest cache in the worktree. Store test output under
`/data/home/vince/artifacts/skills/feature-developer/fleet-owner-access-20261021/`.
Inspect the diff and commit only the helper, its tests and these design docs.

Acceptance: green tests, exact API/SHA/test evidence sent to the parent. No
deployment, credential access, global configuration edits or cleanup.

## Plan Reviews

Round 1: warning — `frozen=True` does not freeze nested mappings, and direct
construction could bypass parser validation. Require both defensive copying
and validation in `__post_init__`; added to Phase 1. No critical findings.
Round 2: warning — lexical mode must prove it does not touch remote paths, and
mocked reparse points are necessary on Linux. Added strict IO-free lexical tests
and reparse probes. No remaining critical or warning findings.

## Validation

The lexical suite first failed because the new module did not exist, then passed
15 tests and 105 subtests. The local suite then failed against its explicit
unimplemented inspection boundary and passed after implementation. Final helper
and adjacent runtime/create-policy/Node-owner regression: **114 tests and 127
subtests passed** in 2.84 seconds. Logs are in the artifact directory above.
The transient test unit enforced 2 GiB memory, zero swap, 100% CPU quota and a
120-second timeout; bytecode and pytest cache writing were disabled.

Cross-platform Windows checks here are lexical and mocked reparse observations
on Linux. Actual Windows filesystem creation remains an integration acceptance
task. Duplicate YAML keys must be rejected by the configuration loader before
it discards them; this mapping helper rejects duplicate/overlapping path values.
