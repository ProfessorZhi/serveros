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

## Resident watch and configuration

`windows-core-watch console` runs the bounded local watch. `service` is the
same loop under the OS SCM dispatcher named `ServerOSCoreUser`. The binary never
registers a service/task, supplies logon credentials or changes OS recovery policy.
Run it as the intended user; LocalSystem/LocalService/NetworkService are refused.
The OS manager owns process restart and its backoff; the watch owns the admitted
component starts. It refuses its own reserved SCM/task target.

The fixed owner-protected config is
`LocalAppData/ServerOS/Core/desired-state.v1.json`. Install/update it with
`windows-core-watch configure < manifest.json`; this validates the current OS SID
and exact closed config before an atomic owner-only write, and can run while the
watch is active. There are no extra approval prompts. Omitted `observe_only`
means true. `running` may recover only with explicit `observe_only:false`;
`manual` and `disabled` are observed and never started. `interval_ms` is 1–10
seconds, default 5 seconds; clients cannot accelerate this interval.

```json
{
  "schema": "serveros/windows-core-desired.v1",
  "node": "device:fixture",
  "principal": "S-1-5-21-REPLACE-WITH-ACTUAL-USER-SID",
  "generation": 1,
  "observe_only": true,
  "services": [{
    "id": "cloudcli", "owner": "S-1-5-21-REPLACE-WITH-ACTUAL-USER-SID",
    "manager": "scm", "unit": "EXACT_INSTALLED_SERVICE_NAME", "desired": "running",
    "health": {"probe":"loopback-http-json","port":8080,"path":"/health","key":"status","value":"ok"}
  }]
}
```

Use the actual device reference, user SID, registered unit and its existing
functional health contract; the example does not grant a guessed installed
service. At most three roles (`cloudcli`, `connector`, `tunnel`) are admitted.
Manager is `scm` or `task-scheduler`. Probes connect only to literal IPv4 loopback,
use a 3-second absolute nonblocking socket deadline and bounded strict HTTP/JSON
response, and require the configured string/boolean field. They use no shell,
DNS, remote host, model call, credential or arbitrary command.

The watch reserves its singleton writer and a durable monotonic generation/SHA256
fence before recovery. Same generation with changed bytes and older generations
refuse. It publishes `watch-status.v1.json` atomically in the protected Core
state root and serves the same snapshot on the authenticated pipe, including
sample time, desired/readiness/recovery codes and independently measured SCM/task
rows. Missing/bad config publishes an explicit observe-only refusal.

Interactive tasks additionally require a measured currently logged-on owner
session; inability to read that fact remains unknown. Native action errors and
missing/corrupt journals stay fenced. Deferred cancelled pipe operations retain
all kernel memory in a fixed 128-slot registry; no cancelled I/O wait or later
allocation can extend a transaction deadline or free a pending context.

Actual Windows machine deployment/acceptance remains NOT_RUN. The host's real
unit inventory and functional endpoint fields must be supplied from its existing
runtime authority. The watch has no automatic service installer and does not
claim Fleet device attestation. The existing Node agent can consume the fixed
status snapshot; remote Fleet projection acceptance remains a separate client
integration check.

SDK references: [CreateNamedPipeW](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-createnamedpipew),
[ImpersonateNamedPipeClient](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-impersonatenamedpipeclient),
[GetNamedPipeServerProcessId](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getnamedpipeserverprocessid),
[StartServiceW](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-startservicew),
[IRegisteredTask::RunEx](https://learn.microsoft.com/en-us/windows/win32/api/taskschd/nf-taskschd-iregisteredtask-runex).

## Versioned Windows package

Standalone Windows builds default `SERVEROS_WINDOWS_STATIC_RUNTIME=ON` (MSVC /MT,
MinGW static C++ runtime). `cmake --build BUILD --config Release --target package`
produces a versioned ZIP containing both binaries, docs and the exact source
revision manifest. `windows-core-watch --version` reads back that revision
without accessing config or services. The hosted workflow extracts the ZIP,
checks binary/manifest identity and starts the extracted watch with an empty
observe-only fixture before preserving the ZIP and its SHA256. It does not
install or restart an OS service.

Keep the existing owner config/journals outside the release directory. For an
update, verify SHA256, extract into a new version directory, read back version,
stop only the Core watch owned by this user through its existing OS manager,
select the new binary path and start it. Retain the prior version directory and
manager configuration; rollback selects that prior path and starts it. Never
clear pending journals or lower the accepted config generation during rollback.
An in-use Windows binary is not overwritten; config, sessions and native unit
registration are not included in the ZIP. User-device update/restart acceptance
remains NOT_RUN until that authorized Windows host can execute the candidate.

The resident publisher requires Windows 10 1709 / Windows Server 2019 x64 or newer for FileRenameInfoEx POSIX replacement. It renames the original CREATE_NEW handle atomically, flushes before and after publication, and leaves compatible readers on their old bytes. Unsupported rename APIs refuse without modifying the destination; read-only target intent is respected, and only the current call’s newly-created temp is discarded by handle. The recovery budget journal keeps its existing write-through transaction path.

Readers must permit delete-sharing for concurrent atomic replacement. Sharing/lock conflicts are retried at most 26 times within a 250 ms deadline; unknown/unsupported errors are not retried. An incompatible external reader may refuse publication, and release permits a subsequent update without a poisoned temp. Native budget UNKNOWN fences are never cleared by these document retries.
