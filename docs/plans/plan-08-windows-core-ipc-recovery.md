# Windows Core IPC and recovery implementation plan

## Overview
Implement approved proposal-windows-core-ipc-recovery.md. Source scope is windows-core plus its Windows contract and hosted build. No deployment or Windows executor.

## Phases
### 1. Portable protocol and recovery policy
Objectives: bounded frames, exact request vocabulary, configured typed allowlist, fail-closed durable state semantics.
Stages: first add failing C++ tests for malformed frames, unauthorized/duplicate units, each refusal state, functional readiness vs running, persisted UNKNOWN, corrupt/future budget, backoff and exhaustion. Introduce status_pipe.hpp and recovery.hpp with test-injected backend and transaction store, then implement until tests pass.
Acceptance: no action without granted native state + confirmed failed functional probe; a durable pending claim exists before a verb. ACK never equals healthy. Read/restart preserves budget. Existing SCM/status tests pass.
Dependencies: existing nodecore::Json writer and portable supervisor admission vocabulary only.
### 2. Native Win32 adapters
Objectives: actual current-user IPC and real native SCM/task start/query, protected single-writer journal.
Stages: add source-contract security boundary tests; implement status_pipe.cpp with overlapped absolute deadline operations, single first pipe instance and OS token SID auth; implement win32_recovery.cpp and win32_store.cpp using documented Win32/COM APIs. No shell. Build a read-only status server/client executable with pinned OS server identity passed by its trusted launcher.
Acceptance: actual Win32 compilation/link, Linux non-Windows implementations refuse unsupported; every native action rechecks state/grant and exact current-user principal. Hold journal lock through native action/probe; FlushFileBuffers before effect. Default grants empty.
Dependencies: phase 1 green. Windows native functional probes are supplied by trusted owning runtime; no absent probe default.
### 3. Validation and review
Objectives: independently reviewable source candidate and truthful validation evidence.
Stages: Linux CMake configure/build/ctest; actual x86_64 Win32 cross-compile/link with trusted toolchain. Hosted Windows CI additionally tests real loopback pipe against synthetic status (no SCM/task effects). Capture revision/command/exit in RUN-0049, inspect diff, commit and publish Draft PR stacked on native-family branch.
Acceptance: protocol/policy suites pass, cross-build proves `_WIN32` paths compile, all unresolved real-machine/configuration integration gaps documented. No Windows-device execution claim.

### 4. Reviewed boundary fixes and resident wiring
First repair all three independent-review findings as a separate frozen commit. Acceptance: hosted actual AccessCheck allow/deny/incomplete SD fixtures, bounded heap-owned cancellation lifetime without infinite wait or use-after-free, first lock/load/release without explicit save followed by successful reopen, and deleted journal refusal. Cross-link actual Win32 branches again.
Then add strict owner-local manifest/config/probe/watch contracts with failing portable tests, native read/write/grant/generation wiring, console and OS service wrapper, and fixed-schema runtime observations. Verify observe-only never invokes an action, manual/disabled stay observed, and the fixed watch interval is not shortened by clients. Retain live Windows deployment NOT_RUN. Two amendment reviews must confirm no unresolved warnings before implementation.

Resident implementation details: fixed LocalAppData owner-only config publisher and
singleton watch; persisted generation plus BCrypt SHA256 exact-byte fence;
nonblocking IPv4-loopback HTTP with a three-second absolute deadline and strict
length/JSON marker; per-pass recovery via the same RecoveryEngine; status snapshot
and pipe publication; SCM dispatcher wrapper reusing the same loop. Directory
handles pin every OS-resolved ancestor against write/delete sharing. Task start
also requires measured current-user interactive session, LUA and ignore-new.
Native watch startup rejects built-in service accounts; no service installation,
credentials or global policy changes occur. Linux and Win32 build validate source;
actual Windows runtime remains NOT_RUN.

Resident review regression cases: real hosted Win32 stopped-listener connection
refusal must yield NOT_READY; read-held atomic configure and a subsequent
configure must succeed; a deliberately incompatible external reader can refuse
one configure but must not poison the next call after release. Lifecycle tests
pin STOPPED terminal and forbid STOPPING-to-RUNNING; native SCM status publication
uses one SRW lock for state and SetServiceStatus, publishes STOP_PENDING before
the stop flag and never reports a backwards terminal transition. Recovery state
is refused on redirected remote/non-ACL volumes or a mismatched base owner.

Hosted 24144a4 exposed Windows default profile ownership (administrator/SYSTEM may own the OS profile root) and loopback refusal latency at the original one-second budget. The private Core children still enforce exact current-user owner-only ACLs; only the OS-resolved base admits current user or trusted OS administrator/SYSTEM ownership. The socket budget is now three seconds total, retaining unknown on genuine timeout. These are executable hosted failures and require a new hosted PASS before release.

Package closure: optional static C++ runtime (/MT or MinGW -static), actual PE
imports check, version/source --version readback, CPack versioned ZIP and SHA256.
Hosted Windows extracts that package and starts its watch with an empty
observe-only fixture before retaining the complete candidate. No missing runtime
DLL assembly is delegated to the user. Cross-toolchain runtime notices accompany
that distribution; deployed user-device OS-manager switching remains NOT_RUN.

Actual held-reader diagnostics pinned MoveFileEx failure to ERROR_ACCESS_DENIED.
The publisher now uses FileRenameInfoEx POSIX replacement on its original
CREATE_NEW writer handle, with pre/post flush, checked target owner and no source
reopen. Minimum OS is Windows 10 1709 / Server 2019 and appears in the package
manifest; unsupported APIs refuse and discard only the current temp. Hosted
read-held tests keep both READ|DELETE and ordinary no-delete-sharing reader
positive cases, verify old reader bytes/new opens, and exercise read-only target
refusal followed by successful configuration. The recovery budget path is unchanged.

Exact readback guards remain mandatory: verify the target path reopens the same
writer FileID, size and bytes before acknowledging publication. The Win32 rename
wrapper receives an explicit NUL-terminated absolute path (FileNameLength excludes
the terminator); buffer padding must not be used as the terminator at exact
alignment. The relative RootDirectory experiment failed the hosted factory and is
not claimed as delivered. Source identity stays the original writer handle,
ancestors remain pinned, and no arbitrary caller destination is accepted.

Final sharing boundary: keep the real READ|DELETE exact-ID/bytes positive case. A reader denying delete-sharing is an explicit bounded refusal; release followed by repeated configure succeeds. Retry only sharing/lock conflicts, at most 26 calls within 250 ms, and never retry unknown/unsupported errors. Exact target bytes are checked before ACK; read-only intent remains a refusal.

### 5. Separate hosted native effect acceptance
The frozen f4f425c source/package gate passes independently; its six suites do
not prove actual SCM/TaskScheduler execution. Add an opt-in hosted-only fixture
that registers a create-only, current-CI-user-owned unique ScheduledTask with an
interactive token, LUA and ignore-new. Invoke the actual typed RecoveryEngine,
native backend and durable store; require stopped/not-ready to RunEx to live
process-identity-pinned functional readiness, busy refusal without another PID or
LastRunTime, manual and disabled refusal, final stopped state and checked deletion
of only this freshly-created task/folder. Retain exact source, object-owner
readback, native grants, outcomes and actual HRESULTs in RUN-0054's unique
native-toy-20261022 subdirectory. Build this fixture under actual Win32 before
running it in a separate hosted job. No production service installation, user
Windows executor, credentials, permission change or elevated task is allowed.
OS denial must fail honestly and preserve evidence. User Windows LIVE remains
NOT_RUN even if hosted effect passes. This supplemental acceptance does not
change Core authority, default-enabled targets or existing six-suite semantics.
