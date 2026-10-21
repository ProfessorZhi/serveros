# Windows Core status — the SCM side, and the secured channel

`serveros/windows-core-status.v1` — what a Windows Node's Core may read about a
service, what it may report, and how its own status route is secured.

Depends on the Linux typed contract (#242, `service-readiness.v1.md` and the
`kAdmit*` vocabulary in `supervisor/admission.hpp`) and on #175. Parent tracker:
issue #244, package **N2**; this milestone is issue #246.

> **No reading in this tree is a real Windows reading.** Everything the tests
> feed the parser is synthetic text. The live Windows verification — one bounded
> read, one toy service — is a later, separately authorised step.

## 1. Why this is not a port

Three things do not carry from the Linux module, and each is a place a
translation would have been wrong in a way that looks right.

### `O_NOFOLLOW` has no meaning here

The Linux route is a UNIX socket under `$XDG_RUNTIME_DIR`, protected by a mode
bit, a `SO_PEERCRED` check, and a refusal to follow a symlink.

A Windows named pipe has **no symlink to follow and no mode bits**. Its
protection is a **DACL**, and its peer identity comes from the client's token.
So the two mechanisms fail differently, and the Windows hazard is not "somebody
symlinked the socket":

| | Linux (`status_socket.hpp`) | Windows |
|---|---|---|
| who may connect | mode `0600` on the socket and its directory | a DACL on the pipe's security descriptor |
| who is on the other end | `SO_PEERCRED` — the kernel reports the peer's uid/gid/pid | the client's **token**, read from the pipe instance |
| path attack | a symlink at the socket path is REFUSED, not followed | no symlink exists on a pipe name |
| the real hazard | another account creating the socket **first** | **instance squatting**: another principal creating the first instance of the pipe name |
| stale path | an existing path is refused, never unlinked | a pipe has no path to leave behind; the instance dies with its creator |

Both mechanisms answer the same two questions — *who may connect* and *who is
connected* — and neither answer is transferable. **`O_NOFOLLOW` is refused as a
Windows rule rather than translated**, because a rule copied from the POSIX side
would defend against an attack that cannot happen here and miss the one that can.

### A service that is RUNNING is not a service that answers

True on Linux too (`probe.hpp` says so), and worse on Windows: a service can be
`SERVICE_RUNNING` with its listener unbound and its worker thread dead, and the
SCM will keep saying RUNNING until the process exits. **`RUNNING` is the weakest
word**, exactly as `systemd-is-active` is on Linux, and a service that needs the
stronger question declares a service-level probe.

### A disabled service and a stopped service look identical

Both report `SERVICE_STOPPED`. Only `dwStartType` separates *a service that
happened to stop* from *a service a person switched off*, so this module reads
**both** — the same reason the Linux side reads `LoadState` beside `ActiveState`.

## 2. The closed vocabulary

| word | is | readiness | admission (Linux spelling) |
|---|---|---|---|
| `RUNNING` | `SERVICE_RUNNING` (4) | `READY` | `ADMITTED` |
| `STOPPED` | `SERVICE_STOPPED` (1) | `NOT_READY` | `ADMITTED` |
| `START_PENDING` (2), `STOP_PENDING` (3) | a transition | `NOT_READY` | `ADMITTED` |
| `CONTINUE_PENDING` (5), `PAUSE_PENDING` (6), `PAUSED` (7) | a transition, or paused | `NOT_READY` | `ADMITTED` |
| `MANUAL_STOP` | `SERVICE_STOPPED` **and** `dwStartType = SERVICE_DISABLED` (4) | `NOT_READY` | **`MASKED`** |
| `NEEDS_LOGIN` | `ERROR_SERVICE_LOGON_FAILED` (1069) | `NOT_READY` | **`NEEDS_LOGIN`** |
| `NOT_INSTALLED` | `ERROR_SERVICE_DOES_NOT_EXIST` (1060) | `NOT_READY` | **`NOT_INSTALLED`** |
| `UNKNOWN` | a state word this version does not know, or nothing was read | `UNMEASURED` | `INSTALL_STATE_UNKNOWN` |

**The admission column is the Linux contract's spelling, reused rather than
re-invented.** A Fleet reader comparing a Linux host and a Windows host must see
one refusal per fact, not two words that mean one thing. The test compares
against `supervisor/admission.hpp`'s own constants, so a rename on the Linux side
fails on this side rather than silently producing a second vocabulary.

### The two named errors are MEASUREMENTS

`1060` and `1069` are answers about the host, not failures of the reader. The SCM
was asked and it said something specific:

- `NOT_INSTALLED` — there is no service. The repair is a deployment, and a boot
  trigger with `start_on_boot` must not spend an attempt starting nothing.
- `NEEDS_LOGIN` — there is a service and its account cannot log on. **Restarting
  it changes nothing**: somebody has to fix a credential, and a Core that retries
  is spending a budget on a fact it cannot affect.

Both are `measured = true`, because the question *was* answered — the same
distinction #217 draws between `NOT_INSTALLED` and `AVAILABLE`.

### Unknown is never STOPPED

A state number this version does not know, or an answer with no `STATE` line at
all, is `measured = false` with a reason — **never `STOPPED`**. "We could not
read it" rendered as "it is down" is the restart loop this whole line of work
exists to prevent, and a state word added by a newer Windows must not arrive here
as a fault.

## 3. What this module cannot do

- **No verb.** There is no start, stop, restart, `ChangeServiceConfig`, registry
  write, or shell. The only interface is `WindowsServiceQuery::query` — one
  question, no arguments. A type that cannot express a Service Control Manager
  write cannot perform one, which is stronger than a comment saying it should not.
- **No remote host.** `sc.exe \\host query` and PowerShell remoting are how a
  local monitoring component becomes a lateral-movement tool. The contract has no
  field for a host, so a caller cannot name one. This is the Windows form of the
  rule `probe.hpp` already applies when it refuses a non-loopback TCP target.
- **No credentials.** The query takes a service name and nothing else.
- **No fabricated metrics.** A missing reading is `UNMEASURED`; there is no path
  in this module that produces a number from an absent one.

## 4. Build status, stated rather than implied

| part | status |
|---|---|
| the parser, the state machine, the vocabulary | **BUILT AND TESTED** on Tokyo — none of it is Windows-specific: text in, struct out |
| the status envelope and its schema | **BUILT AND TESTED** on Tokyo — a value in, JSON out; `contract/windows-core-status.v1.schema.json` is closed and the test reads it |
| `from_win32` — the mapping every Win32 refusal lives in | **BUILT AND TESTED** on Tokyo. It takes plain integers and strings, so a mistake in it is a failing unit test rather than an incident |
| `Win32ScmQuery::query` — the four API calls | **SOURCE ONLY / BUILD_UNVERIFIED**. `#if defined(_WIN32)`, never compiled here |
| the `_WIN32` half — named pipe, DACL, token check, `sc` invocation | **BUILD_UNVERIFIED**. There is no cross compiler on this host: `x86_64-w64-mingw32-g++`, `i686-w64-mingw32-g++` and `clang-cl` are all absent |
| a live Windows reading | **NOT RUN**. #246 asks for it as a separate, separately authorised step |

`BUILD_UNVERIFIED` is not a synonym for "working". Nothing here may be reported
as a working Windows binary, and no Linux fixture may be presented as a Windows
measurement.

## 4b. The `_WIN32` implementation: what it may ask for

`src/win32_scm.cpp` is the read-only query. It is split so that the part which
DECIDES is testable on a host that has no Windows at all:

| function | platform | tested here |
|---|---|---|
| `from_win32(name, current_state, win32_exit_code, start_type, last_error)` | any | **yes** — every refusal is a pure mapping from plain numbers |
| `Win32ScmQuery::query` | `_WIN32` only | **no** — not compiled on this host |

**The rights requested, and no others:**

- `OpenSCManagerW(nullptr, nullptr, SC_MANAGER_CONNECT)` — the machine name is a
  literal `nullptr`, **never a parameter**, so this component cannot be aimed at a
  remote SCM. `SC_MANAGER_ENUMERATE_SERVICE` and `SC_MANAGER_CREATE_SERVICE` are
  never requested.
- `OpenServiceW(…, SERVICE_QUERY_STATUS | SERVICE_QUERY_CONFIG)` — QUERY only.
  `SERVICE_START`, `SERVICE_STOP`, `SERVICE_CHANGE_CONFIG` and `DELETE` are never
  requested, so the handles this code holds **cannot carry out a verb** however
  they are used later.

There is no write call, no registry access, no shell and no credential in that
file. A service name containing a backslash is refused before any API is
reached — that character addresses something other than a local service — which
is a second, independent reason the component cannot be pointed elsewhere.

**The Win32 constants are stated locally, then checked.** `from_win32` compiles
where `<winsvc.h>` does not exist, so the values live in `win32_values`; under
`_WIN32` a `static_assert` compares every one against the real header, so a
Windows build that found a different number **fails to compile** instead of
silently mapping `SERVICE_RUNNING` onto something else. This build cannot check
them, which is why the check is not optional where it can run.

**The read-only path to a logon failure.** `QueryServiceStatusEx` does not return
1069 for a service that cannot log on; it returns `SERVICE_STOPPED` with
`dwWin32ExitCode = 1069`. A Core that read only the state would see a stopped
service and spend a retry on a credential, so the exit code is read on both
routes — the typed call and the text parser — and only when the service is not
running.

**Access denied is a grant, not a state.** `ERROR_ACCESS_DENIED` (5) is
`measured = false` with `SCM_ACCESS_DENIED`. Reporting it as `STOPPED` would
claim knowledge this Core does not have, and would make a permission problem look
like an outage.

## 4c. The Core's status route: UNSUPPORTED, and deliberately not faked

The Linux route is a UNIX socket protected by a mode bit, `SO_PEERCRED` and a
refusal to follow a symlink. The Windows equivalent is a named pipe protected by
a **DACL** and a client **token**.

**It is not implemented here.** A security descriptor that has never been
compiled, let alone run, is not a guarantee — it is the appearance of one, and in
this area the appearance is worse than the absence. There is no Windows compiler
on this host and no owner authorisation to exercise one on a real machine, so
`windows_status_ipc_status()` returns `UNSUPPORTED` with the reason
`NO_VERIFIED_SECURITY_DESCRIPTOR`.

The status is a **constant**, not a computation: a later change that wants to
claim the route works has to change that line and the test that pins it, which is
a visible decision rather than a silent drift.

### 4d. The machine-checkable envelope

`status_document.hpp` and `contract/windows-core-status.v1.schema.json` are the
typed document a Windows Core may answer with. The document carries:

- the **same** readiness words (`READY` / `NOT_READY` / `UNMEASURED`) and
  admission words (`kAdmit*`) as the Linux contract;
- the **same** `identity_verdict` shape as
  `contract/core-lite-status.v1.schema.json`, with `principal_identity` and
  `device_identity` both `UNKNOWN` / `NONE` and a reason naming the missing
  Windows readback. A `MATCHED` here would be a claim nobody made;
- `ipc` as a constant `UNSUPPORTED` / `NO_VERIFIED_SECURITY_DESCRIPTOR`;
- **no** POSIX secure-route mechanism. There is no `O_NOFOLLOW`, no mode bit,
  no peer uid and no socket path in the document, because a Windows pipe has
  none of those and the rule that matters there is instance squatting.

An unmeasured row is `UNKNOWN`, `UNMEASURED`, `INSTALL_STATE_UNKNOWN`, and it
**omits** `raw_state`: a reader must not be able to mistake "we could not look"
for a state number. The schema is closed (`additionalProperties: false`
everywhere), and the test drives the producer and asserts the document fits it.

## 5. Task Scheduler: declared, not yet implemented

The vocabulary is added (`task_status.hpp`) and every word carries a `TASK_`
prefix, so **disjointness with the service states is a property of the spelling**
and a test asserts the set intersection is empty. A summary therefore cannot
report a task with a service's word, or a service with a task's word.

`UnsupportedTaskQuery` returns `TASK_UNSUPPORTED` with a reason. **No code path
derives a task state from SCM data**: the two producers share no field, no
function and no word.

#246 names SCM **and** Task Scheduler. This milestone covers the SCM only.

A scheduled task is not a service wearing a different name: it has no
`dwCurrentState`, it runs to completion rather than staying up, and "is it
healthy" is a question about its last run rather than its current one. Putting
both behind one vocabulary would produce a word — `RUNNING` for a task that
finished an hour ago — that means different things depending on which half
produced it. So the SCM vocabulary above is closed and Task Scheduler is a
separate contract, not a second set of states bolted onto this one.

## 6. What the live Windows step will need

1. The `_WIN32` implementation behind `WindowsServiceQuery`, using the SCM API
   directly (`OpenSCManager` → `OpenService` → `QueryServiceStatusEx` and
   `QueryServiceConfig`), **read-only**, with no `SC_MANAGER_*` write access
   requested.
2. The secured status channel: a named pipe with an explicit DACL, and a token
   check on the client — the Windows answers to the two questions the DACL and
   the token list in §1.
3. One bounded read of one real service, and one toy service started and stopped
   **by a person**, with the same `REAL_TOY` labelling the Linux side uses.
4. Explicit owner approval, per issue #244's policy: no Windows policy, registry,
   service start/stop, or login change without it.

## 7. Current source extension: local IPC and recovery (2026-10-21)

The earlier sections describe the original query-only milestone. The current
Windows target additionally implements the secured named pipe and native
SCM/Task Scheduler recovery adapters described in `windows-core/README.md`.
`ipc.status` now reports compiled availability (`AVAILABLE` on Windows,
`UNSUPPORTED` elsewhere); availability is not a bound-route or identity verdict.
The schema retains UNKNOWN principal/device identities and NOT_ASSERTED freshness.

Recovery is a separate trusted in-process API, never an IPC mutation verb. It
requires immutable enabled role/target/current-user grants, actual OS native
access and identity readback, a functional probe, and a protected durable
per-native-target writer journal. Unknown outcomes remain fenced. Only stopped
SCM services / ready interactive-current-user tasks can start; disabled, busy,
needs-login, unknown and running-but-unhealthy states refuse automatic restart.
The source has no stop/reconfigure/register/elevate operation. Cross compilation
and hosted local IPC testing are separate from real-device and Fleet acceptance;
those remain explicitly NOT_RUN until evidence exists.
