// Actual local SCM query and compiled status-transport capability.
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

// Whether this binary contains the actual Win32 query implementation.
bool win32_scm_available();

// Read-only local SCM query. Non-Windows builds refuse unsupported.
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

// Compiled transport capability only. A caller must still bind/authenticate a
// pipe via status_pipe.hpp; AVAILABLE does not attest a running service.
inline constexpr const char* kIpcStatusUnsupported = "UNSUPPORTED";
inline constexpr const char* kIpcReasonUnverified = "NO_VERIFIED_SECURITY_DESCRIPTOR";
std::string windows_status_ipc_status();
std::string windows_status_ipc_reason();
} // namespace nodecore::windows_core
