// The Win32 source contract: the decision half of the `_WIN32` adapter, the IPC
// status, and the Task Scheduler vocabulary.
//
// EVERY case here is platform-neutral, which is the point. The four Win32 calls
// themselves are `_WIN32`-gated and are NOT COMPILED on this host — but every
// refusal they can produce is a pure mapping from plain integers, so the part
// that decides what a Core DOES about a Windows service is fully tested on
// Tokyo. What cannot be tested here is that the calls are spelled correctly,
// and the build label says so rather than implying otherwise.
#include <iostream>
#include <string>
#include <vector>

#include "check.hpp"
#include "supervisor/admission.hpp"
#include "windows-core/scm_status.hpp"
#include "windows-core/task_status.hpp"
#include "windows-core/win32_scm.hpp"

using namespace nodecore::windows_core;

namespace {

// The Win32 constants, restated so a test can state a world without <winsvc.h>.
constexpr unsigned long kStopped = 1;
constexpr unsigned long kStartPending = 2;
constexpr unsigned long kRunning = 4;
constexpr unsigned long kDisabledStart = 4;
constexpr unsigned long kDemandStart = 3;
constexpr unsigned long kAuthStart = 2;

constexpr unsigned long kErrorAccessDenied = 5;
constexpr unsigned long kErrorServiceDoesNotExist = 1060;
constexpr unsigned long kErrorServiceLogonFailed = 1069;

}  // namespace

int main() {
    const std::string name = "SyntheticService";

    // ---------------------------------------------------------------------
    // The mapping: every way a real SCM answer becomes a typed observation
    // ---------------------------------------------------------------------
    {
        const auto observed = from_win32(name, kRunning, 0, kAuthStart, 0);
        CHECK(observed.measured);
        CHECK_EQ(observed.state, std::string(kStateRunning));
        CHECK_EQ(observed.start_type, std::string(kStartAuto));
        CHECK_EQ(windows_readiness_of(observed), std::string(kReadinessReady));
        CHECK_EQ(windows_admission_for(observed, name),
                 std::string(nodecore::supervisor::kAdmitOk));
    }
    {
        // A service a person DISABLED. Both this and the next case report
        // SERVICE_STOPPED to the SCM; only the start type separates them, and
        // only one of them may be started by a Core.
        const auto disabled = from_win32(name, kStopped, 0, kDisabledStart, 0);
        CHECK(disabled.measured);
        CHECK_EQ(disabled.state, std::string(kStateManualStop));
        CHECK_EQ(windows_admission_for(disabled, name),
                 std::string(nodecore::supervisor::kAdmitMasked));

        const auto stopped = from_win32(name, kStopped, 0, kDemandStart, 0);
        CHECK_EQ(stopped.state, std::string(kStateStopped));
        CHECK_EQ(windows_admission_for(stopped, name),
                 std::string(nodecore::supervisor::kAdmitOk));
    }
    {
        // THE READ-ONLY PATH TO A LOGON FAILURE. QueryServiceStatusEx reports
        // SERVICE_STOPPED with dwWin32ExitCode 1069; a Core that looked only at
        // the state would spend a retry on a credential.
        const auto logon = from_win32(name, kStopped, kErrorServiceLogonFailed, kDemandStart, 0);
        CHECK(logon.measured);
        CHECK_EQ(logon.state, std::string(kStateNeedsLogin));
        CHECK_EQ(windows_admission_for(logon, name),
                 std::string(nodecore::supervisor::kAdmitNeedsLogin));

        // The same exit code on a RUNNING service is ignored: it logged on,
        // whatever it last carried.
        const auto running = from_win32(name, kRunning, kErrorServiceLogonFailed, kAuthStart, 0);
        CHECK_EQ(running.state, std::string(kStateRunning));
    }
    {
        // ACCESS DENIED is a grant, not a state. Reporting it as STOPPED would
        // claim knowledge this Core does not have.
        const auto denied = from_win32(name, 0, 0, 0, kErrorAccessDenied);
        CHECK(!denied.measured);
        CHECK_EQ(denied.reason, std::string(kReasonAccessDenied));
        CHECK_EQ(denied.scm_error, kErrorAccessDenied);
        CHECK(denied.state != kStateStopped);
        CHECK_EQ(windows_admission_for(denied, name),
                 std::string(nodecore::supervisor::kAdmitInstallStateUnknown));
    }
    {
        const auto absent = from_win32(name, 0, 0, 0, kErrorServiceDoesNotExist);
        CHECK(absent.measured);
        CHECK_EQ(absent.state, std::string(kStateNotInstalled));
        CHECK_EQ(windows_admission_for(absent, name),
                 std::string(nodecore::supervisor::kAdmitNotInstalled));
    }
    {
        // Any other SCM error: the reason stays closed, the number is carried.
        const auto other = from_win32(name, 0, 0, 0, 87);
        CHECK(!other.measured);
        CHECK_EQ(other.reason, std::string(kReasonScmError));
        CHECK_EQ(other.scm_error, 87ul);
    }
    {
        // An unknown dwCurrentState is UNKNOWN with a reason, and never stopped.
        const auto novel = from_win32(name, 99, 0, kDemandStart, 0);
        CHECK(!novel.measured);
        CHECK_EQ(novel.reason, std::string(kReasonUnknownStateWord));
        CHECK(novel.state != kStateStopped);
    }
    {
        // Every pending state maps and none is ready.
        for (unsigned long state : {kStartPending, 3ul, 5ul, 6ul, 7ul}) {
            const auto observed = from_win32(name, state, 0, kDemandStart, 0);
            CHECK(observed.measured);
            CHECK_EQ(windows_readiness_of(observed), std::string(kReadinessNotReady));
        }
    }

    // ---------------------------------------------------------------------
    // No remote target, structurally
    // ---------------------------------------------------------------------
    {
        CHECK(valid_service_name("SomeService"));
        CHECK(valid_service_name("A B C"));  // the SCM allows spaces
        // `sc \\host` and a path both live behind a backslash, and neither is a
        // local service this component will resolve.
        CHECK(!valid_service_name("\\\\remote\\SomeService"));
        CHECK(!valid_service_name("a\\b"));
        CHECK(!valid_service_name(""));
        CHECK(!valid_service_name(std::string(257, 'x')));
    }

    // ---------------------------------------------------------------------
    // The build's own honesty
    // ---------------------------------------------------------------------
    {
        // This host has no cross compiler, so the `_WIN32` half is not in this
        // binary — and the observation says so rather than returning an empty
        // service list a caller could read as "nothing is running".
        CHECK(!win32_scm_available());
        Win32ScmQuery query;
        const auto observed = query.query(name);
        CHECK(!observed.measured);
        CHECK_EQ(observed.reason, std::string(kReasonUnsupportedPlatform));
        CHECK(observed.state != kStateStopped);
        CHECK(observed.state != kStateRunning);

        // The same refusals apply before any API is reached, on every platform.
        const auto rejected = query.query("\\\\remote\\x");
        CHECK(!rejected.measured);
    }
    {
        // THE IPC ROUTE IS NOT FAKED. A security descriptor that has never been
        // compiled is not a guarantee, so the status is a constant that says so —
        // and a later change that wants to claim otherwise has to change this
        // line deliberately.
        CHECK_EQ(windows_status_ipc_status(), std::string(kIpcStatusUnsupported));
        CHECK_EQ(windows_status_ipc_reason(), std::string(kIpcReasonUnverified));
    }

    // ---------------------------------------------------------------------
    // Task Scheduler: a separate vocabulary, and nothing faked from the SCM
    // ---------------------------------------------------------------------
    {
        UnsupportedTaskQuery tasks;
        const auto observed = tasks.query("\\Synthetic\\Task");
        CHECK(!observed.measured);
        CHECK_EQ(observed.state, std::string(kTaskUnsupported));
        CHECK_EQ(observed.reason, std::string(kTaskReasonNotImplemented));
        // A task has a last run; a service does not. Neither is invented here.
        CHECK_EQ(observed.last_result, -1L);
        // And no task state is a service state.
        CHECK(observed.state != kStateRunning);
        CHECK(observed.state != kStateStopped);
    }
    {
        // The disjointness is asserted by the module and by this test, so a
        // summary cannot report one kind of thing with the other's vocabulary.
        CHECK(task_and_service_vocabularies_are_disjoint());
    }

    return check::report("test_win32_source_contract");
}
