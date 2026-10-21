// The `_WIN32` implementation behind `WindowsServiceQuery`, and the honest
// status of the Core's own status route.
//
// THE SPLIT THAT MAKES THIS TESTABLE ON A LINUX HOST
// --------------------------------------------------
// There are two halves here and only one of them is Windows-specific:
//
//   * `from_win32(...)` is the DECISION — raw integers in, a typed observation
//     out. Every refusal lives in it. It takes plain numbers, so it compiles and
//     is fully tested on Tokyo, and a mistake in it is a failing unit test
//     rather than an incident on somebody's Windows machine.
//   * `Win32ScmQuery::query` is the CALL — four documented Win32 functions and
//     four `GetLastError` handlings. It is inside `#if defined(_WIN32)` and it is
//     **not compiled on this host**: no cross compiler exists here, so its build
//     status is BUILD_UNVERIFIED and that is not a synonym for working.
//
// WHAT THE CALL IS ALLOWED TO ASK FOR
// -----------------------------------
//   * `OpenSCManagerW(nullptr, nullptr, SC_MANAGER_CONNECT)` — the machine name
//     is **a literal `nullptr`, never a parameter**, so this component cannot be
//     pointed at a remote SCM. `SC_MANAGER_CONNECT` is the least right that
//     opens the manager at all; `SC_MANAGER_ENUMERATE_SERVICE` and
//     `SC_MANAGER_CREATE_SERVICE` are never requested.
//   * `OpenServiceW(…, SERVICE_QUERY_STATUS | SERVICE_QUERY_CONFIG)` — QUERY
//     only. `SERVICE_START`, `SERVICE_STOP`, `SERVICE_CHANGE_CONFIG` and
//     `DELETE` are never requested, so the handles this code holds cannot carry
//     out a verb even if a later change tried.
//
// There is no write verb in this file, no `ChangeServiceConfig`, no registry
// access, no shell, and no credential of any kind.
#pragma once

#include <string>

#include "windows-core/scm_status.hpp"

namespace nodecore::windows_core {

//: The mapping from raw Win32 values to the typed observation.
//:
//: `last_error` is `GetLastError()` from the first call that failed, or 0 when
//: the calls succeeded. `current_state` and `start_type` are the
//: `SERVICE_STATUS_PROCESS.dwCurrentState` and `SERVICE_STATUS.dwStartType`
//: values; `win32_exit_code` is `dwWin32ExitCode`. All are plain integers, so
//: this function is the same on every platform and a test can state any world.
WindowsServiceObservation from_win32(const std::string& service_name, unsigned long current_state,
                                     unsigned long win32_exit_code, unsigned long start_type,
                                     unsigned long last_error);

//: Whether this binary contains a compiled Win32 implementation. False on every
//: build made on this host, and the observation it produces says so rather than
//: guessing.
bool win32_scm_available();

//: The read-only SCM query. On a build without the `_WIN32` half — which is
//: every build this repository has ever produced — every query answers
//: `UNSUPPORTED_PLATFORM` with `measured = false`. A Core on such a build
//: reports UNKNOWN, which is the true answer, rather than an empty service list
//: that would read as "nothing is running".
class Win32ScmQuery final : public WindowsServiceQuery {
public:
    WindowsServiceObservation query(const std::string& service_name) override;
};

//: A service name this component will put into a Win32 call.
//:
//: Windows service names may be long and permissive, and this is not the place
//: to invent a stricter grammar than the SCM's. What it does refuse is the two
//: things that would change WHAT is addressed rather than WHICH service: an
//: empty name, and any name containing a backslash — `sc`'s remote form and a
//: path both live behind that character, and neither is a service name this
//: component will resolve.
bool valid_service_name(const std::string& name);

// ---- the Core's own status route -------------------------------------------
//
// The Linux Core answers on a UNIX socket under `$XDG_RUNTIME_DIR`, protected by
// a mode bit, `SO_PEERCRED` and a refusal to follow a symlink. The Windows
// equivalent is a named pipe whose protection is a **DACL** and whose peer
// identity comes from the client's **token** — a different mechanism with
// different failure modes (see `contract/windows-core-status.v1.md` §1).
//
// THAT EQUIVALENT IS NOT IMPLEMENTED HERE, AND IS NOT FAKED.
// A security descriptor that has never been compiled, let alone run, is not a
// guarantee — it is the appearance of one, and this is precisely the area where
// the appearance is worse than the absence. There is no Windows compiler on this
// host and no owner authorisation to exercise it on a real machine, so the route
// reports `UNSUPPORTED` and the reason names what is missing.
//
// The status is a CONSTANT rather than a computation on purpose: a later change
// that wants to claim the route works has to change this line and the test that
// pins it, which is a visible decision rather than a silent drift.
inline constexpr const char* kIpcStatusUnsupported = "UNSUPPORTED";
inline constexpr const char* kIpcReasonUnverified = "NO_VERIFIED_SECURITY_DESCRIPTOR";

std::string windows_status_ipc_status();
std::string windows_status_ipc_reason();

}  // namespace nodecore::windows_core
