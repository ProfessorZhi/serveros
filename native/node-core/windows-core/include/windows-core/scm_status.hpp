// What a Windows service is doing, read from the Service Control Manager — and
// never written to it.
//
// WHY THIS IS NOT A PORT OF THE LINUX MODULE
// ------------------------------------------
// The Linux Core reads a unit through `systemctl show` and calls the answer
// `Observed`. Windows has a different service manager with a different
// vocabulary, a different set of failure modes, and one fewer guarantee. Three
// things do NOT carry across, and each is a place a translation would have been
// wrong in a way that looks right:
//
//   * **`O_NOFOLLOW` has no meaning here.** The Linux route is a UNIX socket
//     under `$XDG_RUNTIME_DIR`, protected by a mode bit, a `SO_PEERCRED` check
//     and a refusal to follow a symlink. A Windows pipe has no symlink to follow
//     and no mode bits: its protection is a DACL, and its peer identity comes
//     from the client's token. So the Windows hazard is not "somebody symlinked
//     the socket", it is **instance squatting** — a first instance of a pipe
//     name taken by another principal before the Core gets there. A rule copied
//     from the POSIX side would defend against an attack that cannot happen and
//     miss the one that can.
//   * **A service that is RUNNING is not a service that answers.** That is true
//     on Linux too (`probe.hpp` says so), but on Windows it is worse: a service
//     can be RUNNING with its listener unbound and its worker thread dead, and
//     the SCM will still say RUNNING until the process exits. `Running` here is
//     the WEAKEST word, exactly as `systemd-is-active` is on Linux.
//   * **`MANUAL_STOP` and `DISABLED` are different facts.** A service a person
//     stopped is `SERVICE_STOPPED` with `dwStartType = SERVICE_DEMAND_START`; a
//     service a person switched off is `SERVICE_DISABLED`. The second must never
//     be started by a Core, and the two are indistinguishable if only the
//     current state is read — which is why this module reads the START TYPE as
//     well, the same way the Linux side reads `LoadState` beside `ActiveState`.
//
// WHAT IT CANNOT DO
// -----------------
// This file has **no verb**. There is no start, no stop, no restart, no
// `ChangeServiceConfig`, no registry write, and no shell. A type that cannot
// express a Service Control Manager write cannot perform one, which is a
// stronger statement than a comment saying it should not. The only interface is
// `WindowsServiceQuery::query`, one question, no arguments.
//
// It also never asks a REMOTE machine. `sc.exe \\host query` and PowerShell
// remoting are how a local monitoring component becomes a lateral-movement tool;
// the contract has no field for a host, so a caller cannot name one.
//
// NO READING IN THIS TREE IS A REAL WINDOWS READING. Every value a test feeds
// this parser is synthetic; the live verification on a real Windows host is a
// later, separately authorised step (#246).
#pragma once

#include <optional>
#include <string>
#include <vector>

namespace nodecore::windows_core {

//: The closed vocabulary. A word not in this set is never produced: an
//: unrecognised answer becomes `Unknown` WITH ITS REASON, because a state word
//: added by a newer Windows must not arrive as a fault.
inline constexpr const char* kStateRunning = "RUNNING";
inline constexpr const char* kStateStopped = "STOPPED";
inline constexpr const char* kStateStartPending = "START_PENDING";
inline constexpr const char* kStateStopPending = "STOP_PENDING";
inline constexpr const char* kStatePaused = "PAUSED";
inline constexpr const char* kStatePausePending = "PAUSE_PENDING";
inline constexpr const char* kStateContinuePending = "CONTINUE_PENDING";
//: A person set the start type to `SERVICE_DISABLED`. The Windows spelling of
//: `MASKED` on the Linux side: a decision made by a human, which a Core starting
//: the service would be undoing.
inline constexpr const char* kStateManualStop = "MANUAL_STOP";
//: The service exists and cannot run because its account cannot log on
//: (`ERROR_SERVICE_LOGON_FAILED`, 1069). Restarting it changes nothing; somebody
//: has to fix a credential, and a Core that retries is spending a budget on a
//: fact it cannot affect.
inline constexpr const char* kStateNeedsLogin = "NEEDS_LOGIN";
//: The name is not in the SCM database (`ERROR_SERVICE_DOES_NOT_EXIST`, 1060).
//: NOT `STOPPED`: there is no service, and the repair is a deployment.
inline constexpr const char* kStateNotInstalled = "NOT_INSTALLED";
//: Nothing usable was read, or a word this version does not know.
inline constexpr const char* kStateUnknown = "UNKNOWN";

//: The closed reason vocabulary for an `UNKNOWN` or `NOT_INSTALLED` answer.
//: A caller switches on these and never on a sentence.
inline constexpr const char* kReasonNotInstalled = "SERVICE_NOT_INSTALLED";
inline constexpr const char* kReasonNeedsLogin = "SERVICE_ACCOUNT_LOGON_FAILED";
inline constexpr const char* kReasonUnknownStateWord = "UNKNOWN_STATE_WORD";
inline constexpr const char* kReasonNoStateLine = "NO_STATE_IN_THE_ANSWER";
inline constexpr const char* kReasonUnsupportedPlatform = "UNSUPPORTED_PLATFORM";
//: `OpenSCManager`/`OpenService` was refused. A principal that may not read a
//: service is a DIFFERENT fact from a service that is down: one is a grant, the
//: other is a state. Collapsing them would let a Core report "stopped" for
//: something it was never allowed to look at.
inline constexpr const char* kReasonAccessDenied = "SCM_ACCESS_DENIED";
//: Any other SCM failure. The numeric code travels in `scm_error` so the reason
//: word stays closed while the diagnosis stays available.
inline constexpr const char* kReasonScmError = "SCM_ERROR";

//: The Windows equivalents of a start type, as `sc qc` prints them.
inline constexpr const char* kStartAuto = "AUTO_START";
inline constexpr const char* kStartDemand = "DEMAND_START";
inline constexpr const char* kStartDisabled = "DISABLED";

//: One reading of one service.
struct WindowsServiceObservation {
    std::string service_name;
    //: One of the kState* words. Only meaningful when `measured`.
    std::string state = kStateUnknown;
    //: `SERVICE_DISABLED` / `SERVICE_DEMAND_START` / `SERVICE_AUTO_START`, or
    //: empty when the answer did not carry one. Carried BESIDE the state because
    //: a stopped-and-disabled service and a stopped-and-on-demand service look
    //: identical from the current state alone, and only one of them may be
    //: started.
    std::string start_type;
    //: The SCM's own numeric `dwCurrentState`, or -1 when none was read. Kept
    //: so a reader can see the number the word came from rather than trusting
    //: the word alone.
    int raw_state = -1;
    //: The Win32 error that produced this observation, or 0. Diagnostics only —
    //: a reader switches on `reason`, which is closed; this is the number behind
    //: it, and it is never rendered as a state word.
    unsigned long scm_error = 0;
    //: False means no measurement was taken, and `reason` says why. Every field
    //: above is then meaningless and must not be read.
    bool measured = false;
    std::string reason;
};

//: The only thing a Windows reading needs from the world: ask the SCM one
//: question about one service. No `start`, no `stop`, no host, no credentials.
class WindowsServiceQuery {
public:
    virtual ~WindowsServiceQuery() = default;
    //: What the SCM says. Never throws; an unreadable answer is an observation
    //: with `measured = false` and a reason.
    virtual WindowsServiceObservation query(const std::string& service_name) = 0;
};

//: The parser, pure and fixture-testable: the text `sc query` / `sc qc` prints,
//: into a typed observation.
//:
//: It reads BOTH the state and the start type, because neither alone answers the
//: question that matters — whether a Core may start this service.
WindowsServiceObservation parse_scm_query(const std::string& service_name,
                                          const std::string& stdout_text,
                                          const std::string& stderr_text, int exit_code);

//: Whether a reading may be acted on at all, and with which of the Linux
//: contract's words. The returned string is one of the `kAdmit*` constants
//: declared in `supervisor/admission.hpp` — the SAME spelling, because a Fleet
//: reader must not have to learn two vocabularies for one fact.
//:
//: `NOT_INSTALLED`, `MANUAL_STOP` and `NEEDS_LOGIN` are returned as their own
//: answers rather than folded into "not ready": each has a different repair, and
//: a reader sent to the wrong one loses the time the distinction saves.
std::string windows_admission_for(const WindowsServiceObservation& observed,
                                  const std::string& unit);

//: The Windows equivalent of the Linux readiness answer, with the same three
//: outcomes and the same rule: `Running` is the WEAKEST word, and only a
//: service-level probe can answer the stronger question.
inline constexpr const char* kReadinessReady = "READY";
inline constexpr const char* kReadinessNotReady = "NOT_READY";
inline constexpr const char* kReadinessUnmeasured = "UNMEASURED";
std::string windows_readiness_of(const WindowsServiceObservation& observed);

}  // namespace nodecore::windows_core
