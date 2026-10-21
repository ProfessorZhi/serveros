# Windows Core: local status and bounded recovery

This target contains actual `_WIN32` SCM/Task Scheduler and named-pipe adapters,
plus portable protocol/recovery policy tests. Cross-compiling proves the Win32
code compiles and links; Linux tests do not prove Windows behavior.

## Components

- `windows-core-status serve [service ...]` reads at most 16 explicitly named
  local SCM services before binding a current-user status pipe. It serves a fixed
  snapshot for at most 30 seconds / 32 connections, with 2 second transactions.
  Snapshot freshness is NOT_ASSERTED; refresh by launching a fresh producer.
- `windows-core-status read PINNED_SID PINNED_PID PINNED_CREATION_TIME` uses the
  trusted local launcher's process identity. The launcher must capture stdout
  from its own child process and compare the receipt with its retained OS process
  handle. Never discover/trust a server identity from an unauthenticated peer.
- `status_pipe.cpp` uses an explicit protected owner-only DACL,
  PIPE_REJECT_REMOTE_CLIENTS and FILE_FLAG_FIRST_PIPE_INSTANCE. Server checks
  the impersonated client token SID; client checks the server PID, creation time
  and process token SID before sending. SID text in payload never authenticates.
  Frames are uint32 little-endian length plus at most 64 KiB. Exact `status.v1`
  request and bounded `received.v1` receipt prevent unread-response truncation
  without unbounded FlushFileBuffers. One absolute deadline spans request,
  authentication, response and receipt. No mutation verb exists on this pipe.
- `RecoveryEngine` takes an immutable trusted local allowlist: CloudCli,
  Connector and Tunnel roles map to exact SCM names or Task Scheduler paths.
  Empty/disabled grants act on nothing. Requests select roles, never shell
  commands, paths, executable arguments, other accounts or remote hosts.
- `Win32RecoveryBackend` obtains actual SERVICE_START/query rights; SCM account
  SID must equal current process SID. Task Scheduler uses local COM Connect,
  actual execute access check, registered current-user principal,
  TASK_LOGON_INTERACTIVE_TOKEN, TASK_RUNLEVEL_LUA and TASK_INSTANCES_IGNORE_NEW. StartService/RunEx
  is the only effect. No service stop, task stop, registration/configuration,
  credential, elevation or shell operation is implemented.
- Manual disabled, needs login, busy, unknown/unreadable state and failed grants
  refuse. Running-but-functionally-failed units refuse automatic restart, to
  preserve active work. A trusted owner must supply `FunctionalProbe`; there is
  no built-in fake success or equation of SCM RUNNING with functional health.
- `Win32RecoveryStore` resolves LocalAppData through the OS and creates protected
  owner-only ServerOS/Core state. Existing roots/files must pass DACL/owner,
  reparse and link checks. A permanent per-target lock spans read, preclaim,
  action, functional retest and outcome. Attempts persist across restarts.
  Complete flushed records replace the previous record atomically on the same
  volume; a crash-left `.next` refuses further writes instead of overwriting it.
  No caller chooses a state directory. A lock created without a committed record
  is a conservative refusal on next process, not a budget reset.
- Three attempts/hour, 60/300/900 second backoff and three-failure circuit match
  the existing Core semantics. Future/rollback clocks and corrupt state refuse.
  Native action errors with uncertain execution retain the durable pending claim;
  `OUTCOME_UNKNOWN` is not retried automatically. There is no clear/reset API.

`ipc.status=AVAILABLE` means compiled transport support on Windows, not that a
pipe is bound or a device identity is attested. Linux reports UNSUPPORTED.
Principal/device claims remain UNKNOWN; authenticated OS IPC does not attest a
Fleet device/account mapping.

## Build and validation

Configure this directory independently with CMake/C++20. Linux runs portable
suites plus unsupported-platform checks. The hosted Windows workflow builds with
MSVC and runs local pipe/ACL/token/timeout/squatting and durable-journal tests,
using synthetic status and unique journal targets on the ephemeral runner. It
never starts/stops a service or task. Cross-build with a Windows CMake toolchain
compiles/links all actual SDK branches, but does not execute them.

Remaining owner integration: real local grant inventory and functional probes,
trusted launch identity persistence, a host service wrapper/watch loop, and Fleet
projection of this status. Actual Windows machine acceptance is NOT_RUN. This
library and read-only binary do not install themselves or claim deployed
self-healing. An interactive-token task whose user is logged out remains refused
by native admission/run and must not trigger credential or logon changes.

SDK references: [CreateNamedPipeW](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-createnamedpipew),
[ImpersonateNamedPipeClient](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-impersonatenamedpipeclient),
[GetNamedPipeServerProcessId](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getnamedpipeserverprocessid),
[StartServiceW](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-startservicew),
[IRegisteredTask::RunEx](https://learn.microsoft.com/en-us/windows/win32/api/taskschd/nf-taskschd-iregisteredtask-runex).
