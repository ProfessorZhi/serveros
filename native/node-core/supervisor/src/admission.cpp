// The admission gate. See `admission.hpp` for why it is separate.
#include "supervisor/admission.hpp"

namespace nodecore::supervisor {

namespace {

AdmissionVerdict refuse(const char* code, const std::string& reason) {
    AdmissionVerdict verdict;
    verdict.admitted = false;
    verdict.code = code;
    verdict.reason = reason;
    return verdict;
}

}  // namespace

std::string to_word(Readiness value) {
    switch (value) {
        case Readiness::Ready:
            return "READY";
        case Readiness::NotReady:
            return "NOT_READY";
        case Readiness::Unmeasured:
            return "UNMEASURED";
    }
    return "UNMEASURED";
}

AdmissionVerdict admit(const AdmissionInput& input) {
    // NO OBSERVATION, NO ACTION. A verb proposed without a reading is a verb
    // proposed without a reason, and the reason is the only thing that makes the
    // action reviewable afterwards. Refused before any of the checks below,
    // because every one of them reads the observation.
    if (input.observed == nullptr) {
        return refuse(kAdmitInstallStateUnknown,
                      "\"" + input.unit + "\" would be acted on with no observation behind the "
                      "action, so there is nothing to review it against");
    }
    const Observed& observed = *input.observed;

    // A writer that does not hold the pen acts on nothing, whatever it believes
    // about the service. Checked FIRST among the facts because when two writers
    // are running, every other judgement below is being made twice.
    if (!input.lease.held_by_us) {
        return refuse(kAdmitNoLease,
                      input.lease.holder.empty()
                          ? "this writer does not hold the lease for \"" + input.unit +
                                "\", so it may not act on it"
                          : "the lease for \"" + input.unit + "\" is held by \"" +
                                input.lease.holder + "\", not by this writer");
    }

    // A MASK FIRST, and deliberately before the install state rather than
    // inside it. `unit_is_installed` answers `false` for a masked unit — "there
    // is no unit behind this name" is true of a mask — so checking the tri-state
    // first would report every mask as NOT_INSTALLED and lose the one fact that
    // changes the repair: a mask was switched off BY A PERSON, and the fix is to
    // ask them, not to deploy anything. Two different sentences, and a reader
    // sent to the wrong one loses the time this distinction saves.
    if (input.masked) {
        return refuse(kAdmitMasked,
                      "\"" + input.unit +
                          "\" is masked — deliberately switched off — so a Core starting it "
                          "would be undoing a decision a person made");
    }

    // INSTALL STATE. `nullopt` is refused rather than treated as installed,
    // because the direction of that guess matters: assuming installed spends an
    // attempt on a unit that is not there, and assuming absent refuses a service
    // that is running. Neither is acceptable, so the answer is "we do not know".
    if (!input.installed.has_value()) {
        return refuse(kAdmitInstallStateUnknown,
                      "whether \"" + input.unit +
                          "\" is installed was not established, so this Core cannot say whether "
                          "starting it is possible");
    }
    if (!*input.installed) {
        return refuse(kAdmitNotInstalled,
                      "\"" + input.unit +
                          "\" is not installed on this host, so there is no service to recover; "
                          "systemd reports it inactive, which is not the same fact");
    }

    // LINK. A disconnected host is not a broken service, and restarting on a
    // network fault is how one outage becomes a restart storm that outlives it.
    if (!input.link.known) {
        return refuse(kAdmitLinkDown,
                      "whether this host has a working link was not established, so a failure of "
                      "\"" + input.unit +
                          "\" cannot be attributed to the service rather than to the network");
    }
    if (!input.link.up) {
        return refuse(kAdmitLinkDown,
                      input.link.reason.empty()
                          ? "this host has no working link, so a restart of \"" + input.unit +
                                "\" would not address what is wrong"
                          : "this host has no working link (" + input.link.reason +
                                "), so a restart of \"" + input.unit +
                                "\" would not address what is wrong");
    }

    // BUSY. An agent mid-task on the service is a reason to wait. This is the
    // same rule the collector applies to its own measurements: a busy thing is
    // not a broken thing, and the cost of waiting is a slower recovery while the
    // cost of acting is interrupting work that was going to finish.
    if (!input.agent.known) {
        return refuse(kAdmitAgentStateUnknown,
                      "whether an agent is using \"" + input.unit +
                          "\" was not established, so this Core cannot tell a service that needs "
                          "recovering from one that is working");
    }
    if (input.agent.busy) {
        std::string who = "an agent";
        if (!input.agent.holder.empty()) {
            who = "the \"" + input.agent.holder + "\" role";
        }
        std::string how_long;
        if (input.agent.since_seconds >= 0) {
            how_long = " for " + std::to_string(input.agent.since_seconds) + "s";
        }
        return refuse(kAdmitAgentBusy,
                      who + " has been using \"" + input.unit + "\"" + how_long +
                          ", so it is busy rather than broken and acting now would interrupt it");
    }

    // The observation itself must be a measurement. `measured = false` is the
    // provider saying it could not read the unit — which the planner already
    // refuses to turn into an action, and which this gate refuses for the same
    // reason rather than trusting the caller to have checked.
    if (!observed.measured) {
        return refuse(kAdmitInstallStateUnknown,
                      "\"" + input.unit +
                          "\" was not readable, so there is nothing this Core could act on");
    }

    AdmissionVerdict verdict;
    verdict.admitted = true;
    verdict.code = kAdmitOk;
    verdict.reason = "";
    return verdict;
}

Readiness readiness_of(const Observed& after) {
    if (!after.measured) {
        // Could not look is not a verdict about the service. Reported as such,
        // because both "ready" and "not ready" would be statements this
        // observation does not support.
        return Readiness::Unmeasured;
    }
    // The ONLY ready answer. Everything else — activating, deactivating, failed,
    // inactive, and the enum's Unknown — is not a serving service, and the
    // distinction that matters is that a supervisor which has already run a verb
    // is exactly the one most likely to assume it worked.
    return after.active == Active::Active ? Readiness::Ready : Readiness::NotReady;
}

}  // namespace nodecore::supervisor
