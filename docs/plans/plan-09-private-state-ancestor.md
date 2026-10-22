# Private state ancestor implementation plan

## Overview
Implement proposal-private-state-ancestor.md on frozen base 30f6fd7, scoped to
StateDirectory source/header, action-gate FS tests and these design documents.
Do not touch host state, grants, runtime daemons or other lanes.

## Phase 1: RED actual filesystem regression
Objectives: demonstrate the measured private-prefix admission failure without
weakening public-prefix/sticky expectations. Stages: add unique env-aware fixture
roots; test public 0775/0777 refusal and private 0700/0775/nonshared-parent positive,
then actual read/write, boundary chmod widening, parent widening, symlink and
post-open namespace replacement refusals. Retain lookup/open inode-race tests.
Check foreign immediate root ownership on unprivileged execution; privilege-
required chown/euid tests must clearly report unavailable instead of faking SID.
Acceptance: exact-head isolated hosted Linux native suite is RED on the new private
positive with unchanged production source. Tokyo only compiles under resource
bounds; no legacy hardcoded-/tmp suite executes on this shared host.
Dependencies: existing native Linux push test/fleet-v1-complete-* workflow.

## Phase 2: GREEN FD-derived trust
Objectives: bounded owning ancestry, fresh private boundary, strict local parent
and I/O revalidation. Stages: implement private ancestry storage, current-euid
capture and unchanged sticky/public-prefix policy; retain/revalidate the chain
only when a writable ancestor actually needed the private-prefix exception.
Preserve the old FD-authority read/write behavior otherwise. Validate captured
owner/gid/mode/identity/privacy
before/after read and before temp, pre-rename and after fsync for exception paths.
Unknown trust refuses
without effect; no widening of immediate-parent/regular-file/owner rules.
Acceptance: new actual FS regressions and all native CTest suites pass on exact
hosted Linux head; normal and moved StateDirectory maintain FD ownership. Local
Tokyo compile uses ARTIFACTS only, 2GiB/1CPU. No runtime state is written.
Dependencies: Phase 1 recorded RED.

## Phase 3: Independent source review and evidence
Objectives: a small reviewable commit chain from 30f6fd7 and honest host boundaries.
Stages: freeze SHA, capture official run/log and review proposal/implementation,
register delivery in RUN-0055/private-ancestor-20261022. Keep historical finalize
files unchanged; no public PR is needed. Acceptance: root can independently
cherry-pick/test, public unsafe prefixes and owner/symlink/inode checks remain
closed, actual live runtime deployment stays unperformed.
