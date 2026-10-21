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
nonblocking IPv4-loopback HTTP with a one-second absolute deadline and strict
length/JSON marker; per-pass recovery via the same RecoveryEngine; status snapshot
and pipe publication; SCM dispatcher wrapper reusing the same loop. Directory
handles pin every OS-resolved ancestor against write/delete sharing. Task start
also requires measured current-user interactive session, LUA and ignore-new.
Native watch startup rejects built-in service accounts; no service installation,
credentials or global policy changes occur. Linux and Win32 build validate source;
actual Windows runtime remains NOT_RUN.
