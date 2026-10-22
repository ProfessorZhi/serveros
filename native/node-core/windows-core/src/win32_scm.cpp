// The Win32 SCM query, and the mapping every refusal lives in.
//
// The mapping is platform-neutral and is tested on Linux; the four API calls are
// `_WIN32`-gated. A Linux build does not compile them; the hosted Windows
// contract workflow does, with MSVC, without calling a real SCM.
#include "windows-core/win32_scm.hpp"

#if defined(_WIN32)
// This translation unit is the only one that sees <windows.h>, and the C++
// headers above already use `std::` names: keep the SDK's min/max macros and
// its rarely needed extras out of this compile.
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <winsvc.h>
#endif

namespace nodecore::windows_core {

//: The Win32 values this mapping is written against, stated here so the mapping
//: compiles on a platform that has no `<winsvc.h>`.
//:
//: They are the documented constants from `winsvc.h` and have been stable since
//: NT. Stating them is only half the arrangement: under `_WIN32` the
//: `static_assert`s below compare every one against the real header, so a
//: Windows build that found a different number fails to compile instead of
//: silently mapping `SERVICE_RUNNING` onto something else. A Linux build (this
//: one) cannot check them, which is exactly why the check is not optional where
//: it can run.
namespace win32_values {

constexpr unsigned long kServiceStopped = 0x00000001;
constexpr unsigned long kServiceStartPending = 0x00000002;
constexpr unsigned long kServiceStopPending = 0x00000003;
constexpr unsigned long kServiceRunning = 0x00000004;
constexpr unsigned long kServiceContinuePending = 0x00000005;
constexpr unsigned long kServicePausePending = 0x00000006;
constexpr unsigned long kServicePaused = 0x00000007;

constexpr unsigned long kServiceAutoStart = 0x00000002;
constexpr unsigned long kServiceDemandStart = 0x00000003;
constexpr unsigned long kServiceDisabled = 0x00000004;

constexpr unsigned long kErrorAccessDenied = 5;
constexpr unsigned long kErrorServiceDoesNotExist = 1060;
constexpr unsigned long kErrorServiceLogonFailed = 1069;

#if defined(_WIN32)
static_assert(kServiceStopped == SERVICE_STOPPED, "SERVICE_STOPPED moved");
static_assert(kServiceStartPending == SERVICE_START_PENDING, "SERVICE_START_PENDING moved");
static_assert(kServiceStopPending == SERVICE_STOP_PENDING, "SERVICE_STOP_PENDING moved");
static_assert(kServiceRunning == SERVICE_RUNNING, "SERVICE_RUNNING moved");
static_assert(kServiceContinuePending == SERVICE_CONTINUE_PENDING, "SERVICE_CONTINUE_PENDING moved");
static_assert(kServicePausePending == SERVICE_PAUSE_PENDING, "SERVICE_PAUSE_PENDING moved");
static_assert(kServicePaused == SERVICE_PAUSED, "SERVICE_PAUSED moved");
static_assert(kServiceAutoStart == SERVICE_AUTO_START, "SERVICE_AUTO_START moved");
static_assert(kServiceDemandStart == SERVICE_DEMAND_START, "SERVICE_DEMAND_START moved");
static_assert(kServiceDisabled == SERVICE_DISABLED, "SERVICE_DISABLED moved");
static_assert(kErrorAccessDenied == ERROR_ACCESS_DENIED, "ERROR_ACCESS_DENIED moved");
static_assert(kErrorServiceDoesNotExist == ERROR_SERVICE_DOES_NOT_EXIST,
              "ERROR_SERVICE_DOES_NOT_EXIST moved");
static_assert(kErrorServiceLogonFailed == ERROR_SERVICE_LOGON_FAILED,
              "ERROR_SERVICE_LOGON_FAILED moved");
#endif

}  // namespace win32_values

namespace {

WindowsServiceObservation unread(const std::string& name, const char* reason,
                                 unsigned long scm_error) {
    WindowsServiceObservation observed;
    observed.service_name = name;
    observed.state = kStateUnknown;
    observed.measured = false;
    observed.reason = reason;
    observed.scm_error = scm_error;
    return observed;
}

}  // namespace

bool valid_service_name(const std::string& name) {
    if (name.empty() || name.size() > 256) {
        return false;
    }
    // A backslash addresses something other than a local service: `sc \\host`
    // is the remote form, and a path is not a service name. Neither is resolved,
    // and the machine name in `OpenSCManagerW` is a literal `nullptr` besides —
    // two independent reasons this component cannot be aimed at another host.
    if (name.find('\\') != std::string::npos) {
        return false;
    }
    return true;
}

WindowsServiceObservation from_win32(const std::string& service_name, unsigned long current_state,
                                     unsigned long win32_exit_code, unsigned long start_type,
                                     unsigned long last_error) {
    // A FAILED CALL IS A FACT ABOUT THE CALL, NOT ABOUT THE SERVICE, and the
    // three answers a reader can act on are separated here rather than folded
    // into "not running":
    //
    //   5     ERROR_ACCESS_DENIED    — this principal may not read the service.
    //                                  A grant, not a state: reporting it as
    //                                  "stopped" would claim knowledge we do not
    //                                  have.
    //   1060  ERROR_SERVICE_DOES_NOT_EXIST — there is no service. A deployment.
    //   1069  ERROR_SERVICE_LOGON_FAILED  — the account cannot log on. A
    //                                  credential, which a restart cannot fix.
    switch (last_error) {
        case 0:
            break;
        case win32_values::kErrorAccessDenied:
            return unread(service_name, kReasonAccessDenied, last_error);
        case win32_values::kErrorServiceDoesNotExist: {
            WindowsServiceObservation observed = unread(service_name, kReasonNotInstalled, last_error);
            observed.state = kStateNotInstalled;
            observed.measured = true;  // the question WAS answered
            return observed;
        }
        case win32_values::kErrorServiceLogonFailed: {
            WindowsServiceObservation observed = unread(service_name, kReasonNeedsLogin, last_error);
            observed.state = kStateNeedsLogin;
            observed.measured = true;
            return observed;
        }
        default:
            // Any other failure. The reason word stays closed; the number
            // travels beside it so the diagnosis is not thrown away.
            return unread(service_name, kReasonScmError, last_error);
    }

    // THE START TYPE OUTRANKS THE STATE, before anything else looks at it. A
    // service a person disabled reports `SERVICE_STOPPED` exactly as a service
    // that happened to stop does, so the decision to respect is only visible in
    // `dwStartType`.
    WindowsServiceObservation observed;
    observed.service_name = service_name;
    observed.scm_error = 0;

    if (start_type == win32_values::kServiceDisabled) {
        observed.measured = true;
        observed.state = kStateManualStop;
        observed.start_type = kStartDisabled;
        observed.raw_state = static_cast<int>(current_state);
        return observed;
    }

    std::string word;
    switch (current_state) {
        case win32_values::kServiceStopped:
            word = kStateStopped;
            break;
        case win32_values::kServiceStartPending:
            word = kStateStartPending;
            break;
        case win32_values::kServiceStopPending:
            word = kStateStopPending;
            break;
        case win32_values::kServiceRunning:
            word = kStateRunning;
            break;
        case win32_values::kServiceContinuePending:
            word = kStateContinuePending;
            break;
        case win32_values::kServicePausePending:
            word = kStatePausePending;
            break;
        case win32_values::kServicePaused:
            word = kStatePaused;
            break;
        default:
            // A state this version does not know. NOT a fault, and NOT stopped.
            return unread(service_name, kReasonUnknownStateWord, 0);
    }

    observed.measured = true;
    observed.state = word;
    observed.raw_state = static_cast<int>(current_state);
    switch (start_type) {
        case win32_values::kServiceAutoStart:
            observed.start_type = kStartAuto;
            break;
        case win32_values::kServiceDemandStart:
            observed.start_type = kStartDemand;
            break;
        default:
            // Boot and system start types belong to drivers, not to a service
            // this component would recover; carried as empty rather than named
            // by a word that would be wrong.
            break;
    }

    // THE READ-ONLY PATH TO A LOGON FAILURE. `QueryServiceStatusEx` does not
    // return 1069 for a service that cannot log on — it returns `SERVICE_STOPPED`
    // with `dwWin32ExitCode = 1069`. A Core that looked only at the state would
    // see a stopped service and spend a retry on a credential, so the exit code
    // is read here. Only when the service is not running: a running service
    // logged on, whatever it last carried.
    if (word != kStateRunning && win32_exit_code == 1069) {
        observed.state = kStateNeedsLogin;
        observed.reason = kReasonNeedsLogin;
    }
    return observed;
}

bool win32_scm_available() {
#if defined(_WIN32)
    return true;
#else
    return false;
#endif
}

WindowsServiceObservation Win32ScmQuery::query(const std::string& service_name) {
    // THE SAME REFUSALS ON EVERY PLATFORM, before any API is reached.
    if (!valid_service_name(service_name)) {
        WindowsServiceObservation observed;
        observed.service_name = service_name;
        observed.state = kStateUnknown;
        observed.measured = false;
        observed.reason = kReasonScmError;
        return observed;
    }

#if defined(_WIN32)
    // `nullptr` for the machine name is not a default — it is the design. There
    // is no parameter that could carry a remote target.
    SC_HANDLE scm = ::OpenSCManagerW(nullptr, nullptr, SC_MANAGER_CONNECT);
    if (scm == nullptr) {
        return from_win32(service_name, 0, 0, 0, ::GetLastError());
    }

    const std::wstring wide(service_name.begin(), service_name.end());
    // QUERY ONLY. `SERVICE_START` and `SERVICE_STOP` are not requested, so this
    // handle cannot carry out a verb however it is used later.
    SC_HANDLE service = ::OpenServiceW(scm, wide.c_str(),
                                       SERVICE_QUERY_STATUS | SERVICE_QUERY_CONFIG);
    if (service == nullptr) {
        const unsigned long error = ::GetLastError();
        ::CloseServiceHandle(scm);
        return from_win32(service_name, 0, 0, 0, error);
    }

    SERVICE_STATUS_PROCESS status{};
    DWORD needed = 0;
    const bool status_ok = ::QueryServiceStatusEx(service, SC_STATUS_PROCESS_INFO,
                                                  reinterpret_cast<LPBYTE>(&status), sizeof(status),
                                                  &needed) != 0;
    unsigned long status_error = status_ok ? 0 : ::GetLastError();

    // The start type is a SECOND call, and its failure does not discard the
    // state we already have: a service whose configuration we may not read is
    // still a service whose state we may report. It leaves `start_type` empty,
    // which is the honest answer — not "disabled", and not "on demand".
    DWORD start_type = 0;
    if (status_ok) {
        // One fixed, bounded buffer. `QueryServiceConfigW` reports the size it
        // needs; a service with an enormous path could exceed this, and that is
        // handled by leaving the start type unknown rather than by growing a
        // buffer from a number the SCM chose.
        constexpr DWORD kConfigBytes = 8192;
        alignas(QUERY_SERVICE_CONFIGW) BYTE buffer[kConfigBytes];
        DWORD config_needed = 0;
        if (::QueryServiceConfigW(service, reinterpret_cast<LPQUERY_SERVICE_CONFIGW>(buffer),
                                  kConfigBytes, &config_needed) != 0) {
            start_type = reinterpret_cast<LPQUERY_SERVICE_CONFIGW>(buffer)->dwStartType;
        }
    }

    ::CloseServiceHandle(service);
    ::CloseServiceHandle(scm);

    if (!status_ok) {
        return from_win32(service_name, 0, 0, 0, status_error);
    }
    return from_win32(service_name, status.dwCurrentState, status.dwWin32ExitCode, start_type, 0);
#else
    // No Win32 implementation in this binary. UNKNOWN with a reason, never an
    // empty answer that a caller could read as "there is nothing to see".
    WindowsServiceObservation observed;
    observed.service_name = service_name;
    observed.state = kStateUnknown;
    observed.measured = false;
    observed.reason = kReasonUnsupportedPlatform;
    return observed;
#endif
}

std::string windows_status_ipc_status() { return win32_scm_available() ? "AVAILABLE" : kIpcStatusUnsupported; }

std::string windows_status_ipc_reason() { return win32_scm_available() ? "LOCAL_PIPE_REQUIRES_PINNED_INSTANCE" : kIpcReasonUnverified; }

}  // namespace nodecore::windows_core
