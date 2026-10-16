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
| the `_WIN32` half — named pipe, DACL, token check, `sc` invocation | **BUILD_UNVERIFIED**. There is no cross compiler on this host: `x86_64-w64-mingw32-g++`, `i686-w64-mingw32-g++` and `clang-cl` are all absent |
| a live Windows reading | **NOT RUN**. #246 asks for it as a separate, separately authorised step |

`BUILD_UNVERIFIED` is not a synonym for "working". Nothing here may be reported
as a working Windows binary, and no Linux fixture may be presented as a Windows
measurement.

## 5. Task Scheduler: declared, not yet implemented

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
