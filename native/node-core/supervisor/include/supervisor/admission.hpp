// The last gate before a verb is sent: may THIS Core, at THIS moment, act on
// THIS unit at all?
//
// WHY IT IS SEPARATE FROM THE PLANNER
// -----------------------------------
// `plan.cpp` decides what the manifest WISHES. It is pure over the manifest, the
// observations, the trigger and the instant, and it deliberately knows nothing
// about the state of the world outside the service manager: whether the unit is
// even installed, whether the host currently has a working link, whether an
// agent is in the middle of using the service, or whether another writer holds
// the pen.
//
// Those are all facts that turn a correct plan into a wrong action, and each one
// has a real failure behind it:
//
//   * A unit that is NOT INSTALLED is not a unit that is stopped. systemd
//     answers `ActiveState=inactive` for a name it cannot find
//     (`LoadState=not-found`, measured on this host against both a real and a
//     fictional unit id), so a planner reading only `ActiveState` sees a stopped
//     service where there is no service, and on a boot trigger it will start
//     one — which is a wasted attempt against the budget and a receipt that
//     blames the host for a deployment that never happened.
//   * A MASKED unit was switched off ON PURPOSE. Restarting it is undoing a
//     person's decision with a program.
//   * A host with NO LINK is not a host whose service crashed. Restarting on a
//     DNS or network failure is how a brief outage becomes a restart storm, and
//     the service was never the thing that failed.
//   * An agent MID-TASK on the service is a reason to wait, not to act. This is
//     the same rule the collector already applies to its own measurements: a
//     busy thing is not a broken thing.
//   * TWO WRITERS is the failure that makes every other safeguard moot. A budget
//     that two processes each believe they own is a budget of twice the size,
//     and the receipts of both look correct.
//
// WHAT IT IS NOT
// --------------
// It is not a second planner. It never decides what to do, only whether what the
// planner decided may be done. It starts nothing, reads nothing, and holds no
// state: every fact arrives as an argument, so a test states a whole world
// without a service manager.
//
// AND IT IS NOT A LICENCE
// -----------------------
// `admitted` is not permission to act on any unit. The caller still holds the
// exact allowlist (`valid_unit_id`, `UnitAllowlist`) and the recovery budget,
// which are checked before and after this in their own right. This gate narrows;
// it never widens.
#pragma once

#include <optional>
#include <string>

#include "supervisor/model.hpp"

namespace nodecore::supervisor {

//: The closed refusal codes. A caller switches on these, never on a sentence.
//:
//: `INSTALL_STATE_UNKNOWN` is its own word rather than folded into
//: `NOT_INSTALLED` for the same reason `unit_is_installed` is a tri-state: "we
//: did not look" and "it is not there" are different facts, and only one of them
//: is about the host.
inline constexpr const char* kAdmitOk = "ADMITTED";
inline constexpr const char* kAdmitNotInstalled = "NOT_INSTALLED";
inline constexpr const char* kAdmitMasked = "MASKED";
inline constexpr const char* kAdmitInstallStateUnknown = "INSTALL_STATE_UNKNOWN";
inline constexpr const char* kAdmitLinkDown = "LINK_DOWN";
inline constexpr const char* kAdmitAgentBusy = "AGENT_BUSY";
inline constexpr const char* kAdmitAgentStateUnknown = "AGENT_STATE_UNKNOWN";
inline constexpr const char* kAdmitNoLease = "NO_LEASE";
inline constexpr const char* kAdmitNotReady = "NOT_READY";

//: Whether the host currently has a working path to what the service needs.
//:
//: `known = false` is NOT `up = true`. A caller that cannot tell is refused
//: rather than assumed connected — the whole point of the fact is to stop a
//: restart that the network, not the service, would have to fix.
struct LinkFacts {
    bool known = false;
    bool up = false;
    std::string reason;
};

//: Whether an agent is using the service right now.
//:
//: `since_seconds` is how long it has been busy, or -1 when that could not be
//: computed. It is reported rather than judged: a bounded wait belongs to the
//: caller, which knows what it is waiting for.
struct AgentFacts {
    bool known = false;
    bool busy = false;
    //: Who is busy with it. A role, never an account name or a pid — the same
    //: rule the rest of this tree applies to identifiers.
    std::string holder;
    long since_seconds = -1;
};

//: Whether THIS writer holds the pen. A holder that is not us is reported so a
//: person can find the other writer, and is never broken by this module.
struct LeaseState {
    bool held_by_us = false;
    std::string holder;
};

struct AdmissionInput {
    std::string service_id;
    std::string unit;
    //: The observation the plan was built from. Null is refused: an action
    //: proposed without a reading is an action proposed without a reason.
    const Observed* observed = nullptr;
    //: `unit_is_installed`'s tri-state, copied for the same reason it exists:
    //: true (installed), false (not found), nullopt (not read).
    std::optional<bool> installed;
    //: True when the observation says the unit is a MASK rather than a unit.
    bool masked = false;
    LinkFacts link;
    AgentFacts agent;
    LeaseState lease;
};

struct AdmissionVerdict {
    bool admitted = false;
    //: kAdmitOk, or one of the refusal codes above.
    std::string code;
    std::string reason;
};

//: The gate. Pure: no clock, no file, no process, no state between calls.
AdmissionVerdict admit(const AdmissionInput& input);

//: Whether a service counts as UP after a recovery attempt.
//:
//: `Active::Active` is the ONLY ready answer, and it is systemd's own judgement
//: rather than this component's. A process that exists is not a service that
//: answers: the four states between "we started something" and "it is serving"
//: are all reported as NOT ready, and an unmeasurable observation is reported as
//: UNMEASURED rather than assumed either way.
enum class Readiness { Ready, NotReady, Unmeasured };
Readiness readiness_of(const Observed& after);
std::string to_word(Readiness value);

}  // namespace nodecore::supervisor
