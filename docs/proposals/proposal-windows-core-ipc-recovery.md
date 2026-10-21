# Windows Core local IPC and bounded recovery

## Problem Statement
`native/node-core/windows-core` only implements SCM reads and pure status documents. The status route and Task Scheduler deliberately return unsupported. V1 needs current-user status IPC and independently enforceable recovery without borrowing Linux security or invoking arbitrary commands.

## Goals
Implement actual Win32 named-pipe transport and SCM/Task Scheduler adapters, platform-neutral policy/protocol tests, an owner-only durable transaction journal with a single writer, and a runnable read-only status binary. Cross-compile the actual `_WIN32` code; retain real-device NOT_RUN.

## Non-Goals
No installation, elevation, remote access, credential/config changes, task registration, service reconfiguration, Linux supervisor changes, or automatic adoption of existing unknown services. A generic status query cannot mutate a unit.

## Design
Existing `WindowsServiceQuery`, `Win32ScmQuery`, `WindowsTaskQuery`, `WindowsStatusInput` and the shared admission/readiness vocabulary remain. Introduce files only under windows-core plus explicit Windows contract/build changes.

`status_pipe.hpp/.cpp` introduces bounded framing (little-endian uint32 length), strict status-only request, and absolute transaction timeout. Win32 pipe creation uses an explicit protected DACL granting only current process token SID, FILE_FLAG_FIRST_PIPE_INSTANCE, PIPE_REJECT_REMOTE_CLIENTS and overlapped I/O. Server authenticates the impersonated client token SID after reading the request; client validates server OS PID, process creation time and process token SID against a pinned server instance received through trusted local launch, never through pipe response. No SID supplied in payload authenticates anything. A bounded connection/serve lifetime prevents idle peer starvation.

`recovery.hpp/.cpp` introduces closed UnitRole/Manager enums and immutable trusted local grants mapping role to exact SCM service name or Task Scheduler task path. No request field accepts commands/paths. The engine requires measured state, granted native rights, matching configured current-user run identity, an explicitly enabled grant, no manual-disabled/needs-login/busy/unknown state, and a functional probe. A trusted in-process `RecoveryBackend` is test injectable. Native backend queries real local SCM/COM task state and rights, starts only stopped/ready admitted units, never stops running services, modifies registration or elevates. Running but functionally failed units are refused until a separately specified safe restart protocol exists.

`recovery_store.hpp/.cpp` introduces a protected fixed journal under current-user LocalAppData/ServerOS/Core, no caller-selected state paths. Exclusive LockFileEx lock per canonical manager/target identity is held through read, durable pre-effect attempt write, action, functional recheck, and post-effect write. Owner-only DACL/file owner/no reparse/no extra links validated on every open. Attempts persist across process restart; an incomplete pre-effect claim fences UNKNOWN instead of repeating. Grant sets reject duplicate targets even across roles. Task recovery only admits TASK_LOGON_INTERACTIVE_TOKEN with the actual registered current-user SID; a start ACK is never a functional result. Three attempts/hour, 60/300/900 seconds backoff and three failures circuit-open reuse existing recovery semantics. Wall clock rollback/future stamps refuse; no monotonic stamp crosses boot.

The status binary is read-only and reports identity assertions separately from IPC authentication; a process token does not attest a Fleet device. Build artifacts and tests live under RUN evidence, not source. Native action paths require explicit local grant and actual OS access; the defaults grant nothing.

## Alternatives
Linux sockets/SO_PEERCRED do not implement Windows ACL/token semantics. Shell sc.exe/schtasks would add command/quoting surfaces and are rejected. Automatic restart of unknown tasks risks terminating work; status-only refusal remains the fallback. A separate recovery database is unnecessary: bounded per-target transaction state is sufficient.

## Open Questions
Real Windows service/task identity and functional probe configurations require local integration; this change does not guess deployed unit names. Live Windows behavior and authenticated Fleet integration remain NOT_RUN. Trusted grant creation/launch integration is documented rather than silently installing global policy.

## Reviewed implementation corrections and resident extension
Independent review found three concrete blockers in the first source candidate: AccessCheck lacked owner/group; CancelIoEx followed by infinite GetOverlappedResult violated deadlines; first healthy/refused transaction never initialized its journal. Request the complete task descriptor and exercise real AccessCheck fixtures; heap-own every OVERLAPPED and kernel-facing buffer, retain incomplete cancellations in a process-lifetime registry capped at 128, and close failed pipe instances; initialize the journal durably under its first exclusive lock before exposing it. Reopening a deleted committed record remains a refusal.

The next commit adds a minimal resident Windows watch console/SCM service wrapper, fixed owner-protected local desired configuration, per-target functional loopback HTTP JSON probes and per-pass status publication. It reuses running/manual/disabled, observe_only and generation semantics; it does not install a service or invent a second machine authority. The watch must remain responsive when a status client stalls. Native service actions remain real-device NOT_RUN.
