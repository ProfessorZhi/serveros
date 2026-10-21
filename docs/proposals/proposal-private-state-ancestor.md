# Private state ancestor admission

## Problem Statement
Tokyo has a current-user-owned 0700 state prefix containing a 0775 serveros
intermediate directory. `supervisor/src/state_directory.cpp` currently applies
`trusted_ancestor` without considering an enclosing private directory, so its
budget path cannot open even with a nonshared immediate parent. Public writable
ancestors must retain their current refusal and sticky-owner exception.

## Goals
Permit current-user-owned writable intermediate directories only below an
already-opened, fstat-verified current-euid directory with zero group/other bits.
Keep immediate parent ownership/nonshared checks. Revalidate the pinned chain's
permissions, identity and effective uid before/after budget read/write.

## Non-Goals
No directory chmod, production state write, service restart, new permissions,
CLI/model/task changes, FD passed by untrusted caller, symlink traversal or broad
public-prefix exception. Root remains the OS privilege boundary. No claim of
atomic protection against a hostile same-euid/root process changing permissions
immediately after a final check.

## Design
`StateDirectory` retains its existing immediate-parent FD/basename API, with an
internal owning ancestry object holding the full nofollow directory FD chain and
component names. Every edge retains lookup/open dev+ino comparison. Up to 128
directory components bound FD consumption. A fresh current-euid/no-group-or-other-
bits fstat establishes the first private boundary. Below it, each component must
remain current-euid-owned; the containing 0775/0777 directory can then be trusted.
Before that boundary the existing trusted_ancestor/sticky rule is unchanged.
The immediate parent always remains current-euid-owned and non-group/world-write.

Before and after I/O, re-fstat every held FD, verify baseline owner/current euid,
recompute trust, require the original boundary remain private, and compare every
nofollow parent-relative entry against its held child's dev+ino. Refuse changed
owners, symlinks, path replacement, widened boundary permissions or a shared
immediate parent. Reading continues to validate the state file. Writing rechecks
existing target and ancestry before temp creation, before rename and after durable
publish; on lost trust it refuses, leaves uncertain state and never issues effect.
The caller's existing unknown/pending policy stays authoritative. File operations
remain relative to the immediate-parent FD; a path string never establishes trust.

## Alternatives
Removing writable-ancestor checks globally admits public-prefix replacement and
is rejected. Persisting only a boolean private flag misses chmod/ownership drift.
Changing the real host directories just to satisfy an old checker obscures the
measured host contract and is outside this source change.

## Open Questions
None blocking. Same-euid/root mutation cannot be excluded by directory FDs; checks
observe the boundary and fail closed if changed. Group/other ACL search access is
excluded by zero group/other POSIX bits on the private boundary (ACL mask included).
