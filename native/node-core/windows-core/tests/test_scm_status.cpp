// The Windows service-status parser: every way an SCM answer can be read wrong.
//
// NOTHING HERE IS A REAL WINDOWS READING, and nothing here runs a process. The
// parser is a pure function of two strings and an exit code — which is the whole
// reason it was built this way: the states that matter (a service a person
// disabled, an account that cannot log on, a name that is not in the database, a
// state word from a newer Windows) are all statable as text on a Linux host, and
// a real Windows machine is a later, separately authorised step.
//
// The two rules this file exists to hold:
//   * a service that is DISABLED by a person is not a service that stopped, and
//     the difference is the START TYPE, never the current state alone;
//   * a word this version does not understand is UNKNOWN with a reason, and
//     NEVER `STOPPED` — "we could not read it" rendered as "it is down" is the
//     restart loop this whole line of work exists to prevent.
#include <iostream>
#include <string>

#include "check.hpp"
#include "supervisor/admission.hpp"
#include "windows-core/scm_status.hpp"

using namespace nodecore::windows_core;

namespace {

//: What `sc query` prints on a real Windows host, with the count of spaces the
//: tool actually uses. Synthetic name, synthetic state — the SHAPE is the point.
std::string sc_query(const std::string& name, int state_number, const std::string& state_word) {
    return "SERVICE_NAME: " + name + "\r\n" +
           "        TYPE               : 20  WIN32_SHARE_PROCESS\r\n" +
           "        STATE              : " + std::to_string(state_number) + "  " + state_word +
           "\r\n" +
           "                                (STOPPABLE, NOT_PAUSABLE, ACCEPTS_SHUTDOWN)\r\n" +
           "        WIN32_EXIT_CODE    : 0  (0x0)\r\n" +
           "        SERVICE_EXIT_CODE  : 0  (0x0)\r\n" +
           "        CHECKPOINT         : 0x0\r\n" +
           "        WAIT_HINT          : 0x0\r\n";
}

//: What `sc qc` adds, and the only place the START TYPE appears.
std::string sc_qc(const std::string& name, int start_number, const std::string& start_word) {
    return "SERVICE_NAME: " + name + "\r\n" +
           "        START_TYPE         : " + std::to_string(start_number) + "   " + start_word +
           "\r\n";
}

std::string sc_error(int code, const std::string& what) {
    return "[SC] OpenService FAILED " + std::to_string(code) + ":\n\n" + what + "\n";
}

//: A `sc query` answer that carries an exit code — which is where a READ-ONLY
//: query finds a logon failure, because `QueryServiceStatusEx` does not return
//: 1069 for a service that cannot log on; it returns STOPPED with that exit code.
std::string sc_query_with_exit(const std::string& name, int state_number,
                               const std::string& state_word, int win32_exit) {
    return "SERVICE_NAME: " + name + "\r\n" +
           "        STATE              : " + std::to_string(state_number) + "  " + state_word +
           "\r\n" +
           "        WIN32_EXIT_CODE    : " + std::to_string(win32_exit) + "  (0x0)\r\n";
}

}  // namespace

int main() {
    const std::string name = "SyntheticService";

    // ---------------------------------------------------------------------
    // The states a Core may act on
    // ---------------------------------------------------------------------
    {
        const auto observed =
            parse_scm_query(name, sc_query(name, 4, "RUNNING"), "", 0);
        CHECK(observed.measured);
        CHECK_EQ(observed.state, std::string(kStateRunning));
        CHECK_EQ(observed.raw_state, 4);
        CHECK_EQ(windows_readiness_of(observed), std::string(kReadinessReady));
        CHECK_EQ(windows_admission_for(observed, name),
                 std::string(nodecore::supervisor::kAdmitOk));
    }
    {
        // A service a person STOPPED with an on-demand start type is a service
        // that may be started again. This is the admitted case that is not RUNNING.
        const auto observed = parse_scm_query(name,
                                             sc_query(name, 1, "STOPPED") +
                                                 sc_qc(name, 3, "DEMAND_START"),
                                             "", 0);
        CHECK(observed.measured);
        CHECK_EQ(observed.state, std::string(kStateStopped));
        CHECK_EQ(observed.start_type, std::string(kStartDemand));
        CHECK_EQ(windows_readiness_of(observed), std::string(kReadinessNotReady));
        CHECK_EQ(windows_admission_for(observed, name),
                 std::string(nodecore::supervisor::kAdmitOk));
    }
    {
        // Every pending and paused state maps, and none of them is ready. A
        // service mid-transition is the one a supervisor is most tempted to call
        // up, because something IS happening.
        const std::pair<int, const char*> states[] = {
            {2, kStateStartPending}, {3, kStateStopPending}, {5, kStateContinuePending},
            {6, kStatePausePending}, {7, kStatePaused},
        };
        for (const auto& entry : states) {
            const auto observed = parse_scm_query(name, sc_query(name, entry.first, entry.second),
                                                  "", 0);
            CHECK(observed.measured);
            CHECK_EQ(observed.state, std::string(entry.second));
            CHECK_EQ(windows_readiness_of(observed), std::string(kReadinessNotReady));
        }
    }

    // ---------------------------------------------------------------------
    // THE RULE THIS MODULE EXISTS FOR: disabled is not stopped
    // ---------------------------------------------------------------------
    {
        // Both of these report `SERVICE_STOPPED`. The start type is the only
        // thing that separates a service that happened to stop from one a person
        // switched off, and a Core that starts the second is undoing a decision.
        const auto disabled = parse_scm_query(
            name, sc_query(name, 1, "STOPPED") + sc_qc(name, 4, "DISABLED"), "", 0);
        CHECK(disabled.measured);
        CHECK_EQ(disabled.state, std::string(kStateManualStop));
        CHECK_EQ(disabled.start_type, std::string(kStartDisabled));
        CHECK_EQ(windows_readiness_of(disabled), std::string(kReadinessNotReady));
        CHECK_EQ(windows_admission_for(disabled, name),
                 std::string(nodecore::supervisor::kAdmitMasked));

        // And with no start type read at all, the state stays STOPPED — the
        // module does not invent a mask it did not see.
        const auto stopped = parse_scm_query(name, sc_query(name, 1, "STOPPED"), "", 0);
        CHECK_EQ(stopped.state, std::string(kStateStopped));
        CHECK(stopped.start_type.empty());
    }

    // ---------------------------------------------------------------------
    // The two named errors, which are ANSWERS rather than failures
    // ---------------------------------------------------------------------
    {
        const auto absent = parse_scm_query(
            name, "", sc_error(1060, "The specified service does not exist as an installed service."),
            1060);
        CHECK(absent.measured);
        CHECK_EQ(absent.state, std::string(kStateNotInstalled));
        CHECK_EQ(absent.reason, std::string(kReasonNotInstalled));
        CHECK_EQ(windows_admission_for(absent, name),
                 std::string(nodecore::supervisor::kAdmitNotInstalled));
        // NOT `STOPPED`: there is no service, and the repair is a deployment.
        CHECK(absent.state != kStateStopped);
    }
    {
        // The same answer, arriving only on stderr with a zero exit — some
        // wrappers do this, and the code is the fact either way.
        const auto absent = parse_scm_query(name, "",
                                            sc_error(1060, "does not exist"), 0);
        CHECK(absent.measured);
        CHECK_EQ(absent.state, std::string(kStateNotInstalled));
    }
    {
        const auto logon = parse_scm_query(
            name, "", sc_error(1069, "The service did not start due to a logon failure."), 1069);
        CHECK(logon.measured);
        CHECK_EQ(logon.state, std::string(kStateNeedsLogin));
        CHECK_EQ(windows_admission_for(logon, name),
                 std::string(nodecore::supervisor::kAdmitNeedsLogin));
    }
    {
        const auto logon = parse_scm_query(name, "", sc_error(1069, "logon failure"), 0);
        CHECK_EQ(logon.state, std::string(kStateNeedsLogin));
    }
    {
        // The code matcher must not fire on a LONGER number that merely contains
        // it. A path or an exit code ending in 1060 would otherwise report a
        // service as absent because of an unrelated digit run.
        const auto noise = parse_scm_query(name, sc_query(name, 4, "RUNNING"),
                                           "[SC] something FAILED 110609:\n", 0);
        CHECK_EQ(noise.state, std::string(kStateRunning));
        CHECK(noise.measured);
    }

    {
        // THE READ-ONLY PATH TO A LOGON FAILURE, through the text answer. A
        // service whose account cannot log on reports STOPPED with
        // WIN32_EXIT_CODE 1069; a parser that read only STATE would see a
        // stopped service and spend a retry on a credential.
        const auto logon = parse_scm_query(
            name, sc_query_with_exit(name, 1, "STOPPED", 1069), "", 0);
        CHECK(logon.measured);
        CHECK_EQ(logon.state, std::string(kStateNeedsLogin));
        CHECK_EQ(windows_admission_for(logon, name),
                 std::string(nodecore::supervisor::kAdmitNeedsLogin));

        // The same code on a RUNNING service is ignored: it logged on, whatever
        // it last carried.
        const auto running = parse_scm_query(
            name, sc_query_with_exit(name, 4, "RUNNING", 1069), "", 0);
        CHECK_EQ(running.state, std::string(kStateRunning));

        // And a DISABLED service stays MANUAL_STOP even with the code: the
        // person's decision is the actionable fact.
        const auto disabled = parse_scm_query(
            name, sc_query_with_exit(name, 1, "STOPPED", 1069) + sc_qc(name, 4, "DISABLED"), "",
            0);
        CHECK_EQ(disabled.state, std::string(kStateManualStop));
    }

    // ---------------------------------------------------------------------
    // Unknown is UNKNOWN, and never STOPPED
    // ---------------------------------------------------------------------
    {
        // A state number this version does not know. NOT a fault — a newer
        // Windows may add one — and NOT stopped, which would be a claim about
        // the host the answer does not support.
        const auto novel = parse_scm_query(name, sc_query(name, 99, "SOME_NEW_STATE"), "", 0);
        CHECK(!novel.measured);
        CHECK_EQ(novel.state, std::string(kStateUnknown));
        CHECK_EQ(novel.reason, std::string(kReasonUnknownStateWord));
        CHECK_EQ(windows_readiness_of(novel), std::string(kReadinessUnmeasured));
        CHECK_EQ(windows_admission_for(novel, name),
                 std::string(nodecore::supervisor::kAdmitInstallStateUnknown));
        CHECK(novel.state != kStateStopped);
    }
    {
        // No STATE line at all. The answer was not understood, so the service is
        // not known to be stopped.
        const auto silent = parse_scm_query(name, "SERVICE_NAME: " + name + "\r\n", "", 0);
        CHECK(!silent.measured);
        CHECK_EQ(silent.reason, std::string(kReasonNoStateLine));
        CHECK(silent.state != kStateStopped);
    }
    {
        const auto empty = parse_scm_query(name, "", "", 0);
        CHECK(!empty.measured);
        CHECK_EQ(empty.reason, std::string(kReasonNoStateLine));
    }
    {
        // The qualifier line `(STOPPABLE, NOT_PAUSABLE, …)` is indented under
        // STATE and carries no colon of its own on the same line — but a parser
        // that split on the first colon anywhere would read `NOT_PAUSABLE` as a
        // field. Asserted so that stays true.
        const auto observed = parse_scm_query(name, sc_query(name, 4, "RUNNING"), "", 0);
        CHECK_EQ(observed.state, std::string(kStateRunning));
        CHECK(observed.raw_state == 4);
    }
    {
        // A CRLF answer — which is what Windows actually writes — parses, and
        // the trailing CR does not become part of the state word.
        const auto observed = parse_scm_query(name, sc_query(name, 4, "RUNNING"), "", 0);
        CHECK(observed.state.find('\r') == std::string::npos);
        CHECK_EQ(observed.state, std::string(kStateRunning));
    }
    {
        // A bare LF answer parses identically. The parser does not depend on the
        // line ending, so a fixture and a real host agree.
        const std::string lf = "SERVICE_NAME: " + name +
                               "\n        STATE              : 4  RUNNING\n";
        const auto observed = parse_scm_query(name, lf, "", 0);
        CHECK(observed.measured);
        CHECK_EQ(observed.state, std::string(kStateRunning));
    }

    // ---------------------------------------------------------------------
    // The vocabulary is SHARED with the Linux contract, and that is asserted
    // ---------------------------------------------------------------------
    {
        // These are the same strings the Linux admission gate returns, compared
        // against its own constants rather than against copies — so a rename on
        // the Linux side fails HERE rather than silently giving a Fleet reader
        // two spellings for one fact.
        const auto disabled = parse_scm_query(
            name, sc_query(name, 1, "STOPPED") + sc_qc(name, 4, "DISABLED"), "", 0);
        const auto absent = parse_scm_query(name, "", sc_error(1060, "x"), 1060);
        const auto logon = parse_scm_query(name, "", sc_error(1069, "x"), 1069);

        CHECK_EQ(windows_admission_for(disabled, name),
                 std::string(nodecore::supervisor::kAdmitMasked));
        CHECK_EQ(windows_admission_for(absent, name),
                 std::string(nodecore::supervisor::kAdmitNotInstalled));
        CHECK_EQ(windows_admission_for(logon, name),
                 std::string(nodecore::supervisor::kAdmitNeedsLogin));

        // And the state words themselves are the ones this header declares, so a
        // contract document quoting them cannot drift from the code.
        CHECK_EQ(std::string(kStateManualStop), std::string("MANUAL_STOP"));
        CHECK_EQ(std::string(kStateNeedsLogin), std::string("NEEDS_LOGIN"));
        CHECK_EQ(std::string(kStateNotInstalled), std::string("NOT_INSTALLED"));
        CHECK_EQ(std::string(kStateUnknown), std::string("UNKNOWN"));
    }

    return check::report("test_scm_status");
}
