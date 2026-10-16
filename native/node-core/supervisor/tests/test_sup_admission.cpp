// The admission gate: every way a correct plan becomes a wrong action.
//
// Not one case here starts a process, reads a file or takes a lock. Each fact
// arrives as an argument — which is the whole reason the gate is separate from
// the planner — so the world can be stated rather than waited for, and a refusal
// is a unit test rather than an incident.
//
// The precedence is asserted as carefully as the outcomes: when several facts
// are wrong at once, WHICH refusal comes back decides what a reader is told to
// fix, and a gate whose order drifts silently sends them to the wrong repair.
#include <optional>
#include <string>

#include "check.hpp"
#include "supervisor/admission.hpp"

using namespace nodecore::supervisor;

namespace {

//: A world in which every fact is good, so a case only has to break the one it
//: is about.
AdmissionInput good() {
    static Observed observed;
    observed = Observed{};
    observed.unit = "toy.service";
    observed.active = Active::Failed;
    observed.sub_state = "failed";
    observed.measured = true;

    AdmissionInput input;
    input.service_id = "toy";
    input.unit = "toy.service";
    input.observed = &observed;
    input.installed = true;
    input.masked = false;
    input.link.known = true;
    input.link.up = true;
    input.agent.known = true;
    input.agent.busy = false;
    input.lease.held_by_us = true;
    return input;
}

}  // namespace

int main() {
    {
        // The one case that must be admitted. A gate that refuses everything is
        // not a gate, and every refusal below is only meaningful beside this.
        const AdmissionVerdict verdict = admit(good());
        CHECK(verdict.admitted);
        CHECK_EQ(verdict.code, std::string(kAdmitOk));
        CHECK(verdict.reason.empty());
    }
    {
        // No observation is not "everything is fine".
        AdmissionInput input = good();
        input.observed = nullptr;
        const AdmissionVerdict verdict = admit(input);
        CHECK(!verdict.admitted);
        CHECK_EQ(verdict.code, std::string(kAdmitInstallStateUnknown));
        CHECK_CONTAINS(verdict.reason, "no observation");
    }
    {
        // NOT INSTALLED. The case this gate exists for: systemd answers
        // `inactive` for a unit it cannot find, so the planner would see a
        // stopped service and start one.
        AdmissionInput input = good();
        input.installed = false;
        const AdmissionVerdict verdict = admit(input);
        CHECK(!verdict.admitted);
        CHECK_EQ(verdict.code, std::string(kAdmitNotInstalled));
        CHECK_CONTAINS(verdict.reason, "not installed");
        CHECK_CONTAINS(verdict.reason, "inactive, which is not the same fact");
    }
    {
        // MASKED gets its own word even though `unit_is_installed` also answers
        // false for it, and it wins over the install state: a mask was switched
        // off by a person, and the repair is to ask them, not to deploy.
        AdmissionInput input = good();
        input.installed = false;
        input.masked = true;
        const AdmissionVerdict verdict = admit(input);
        CHECK(!verdict.admitted);
        CHECK_EQ(verdict.code, std::string(kAdmitMasked));
        CHECK_CONTAINS(verdict.reason, "a decision a person made");
    }
    {
        // An install state that was NEVER READ is refused rather than assumed,
        // in both directions: assuming installed spends an attempt on nothing,
        // assuming absent refuses a running service.
        AdmissionInput input = good();
        input.installed = std::nullopt;
        const AdmissionVerdict verdict = admit(input);
        CHECK(!verdict.admitted);
        CHECK_EQ(verdict.code, std::string(kAdmitInstallStateUnknown));
        CHECK_CONTAINS(verdict.reason, "was not established");
    }
    {
        // A masked input with a KNOWN-installed state is contradictory, and it
        // is still refused: the mask check does not depend on the tri-state, so
        // a caller that gets the two facts out of step cannot slip past it.
        AdmissionInput input = good();
        input.installed = true;
        input.masked = true;
        const AdmissionVerdict verdict = admit(input);
        CHECK(!verdict.admitted);
        CHECK_EQ(verdict.code, std::string(kAdmitMasked));
    }
    {
        // NO LINK, known: the failure is the network's, not the service's.
        AdmissionInput input = good();
        input.link.known = true;
        input.link.up = false;
        input.link.reason = "no route to the default gateway";
        const AdmissionVerdict verdict = admit(input);
        CHECK(!verdict.admitted);
        CHECK_EQ(verdict.code, std::string(kAdmitLinkDown));
        CHECK_CONTAINS(verdict.reason, "no working link");
        CHECK_CONTAINS(verdict.reason, "no route to the default gateway");
    }
    {
        // NO LINK, not known: refused rather than assumed connected, because
        // this fact exists to stop a restart the network would have to fix.
        AdmissionInput input = good();
        input.link.known = false;
        const AdmissionVerdict verdict = admit(input);
        CHECK(!verdict.admitted);
        CHECK_EQ(verdict.code, std::string(kAdmitLinkDown));
        CHECK_CONTAINS(verdict.reason, "was not established");
    }
    {
        // BUSY. Named, and with how long, because the next question a person
        // asks is "busy with what, and since when".
        AdmissionInput input = good();
        input.agent.known = true;
        input.agent.busy = true;
        input.agent.holder = "inference";
        input.agent.since_seconds = 42;
        const AdmissionVerdict verdict = admit(input);
        CHECK(!verdict.admitted);
        CHECK_EQ(verdict.code, std::string(kAdmitAgentBusy));
        CHECK_CONTAINS(verdict.reason, "inference");
        CHECK_CONTAINS(verdict.reason, "42s");
        CHECK_CONTAINS(verdict.reason, "busy rather than broken");
    }
    {
        // A busy fact with no duration still refuses; the sentence simply does
        // not claim one.
        AdmissionInput input = good();
        input.agent.busy = true;
        input.agent.since_seconds = -1;
        const AdmissionVerdict verdict = admit(input);
        CHECK(!verdict.admitted);
        CHECK_EQ(verdict.code, std::string(kAdmitAgentBusy));
        CHECK(verdict.reason.find("42s") == std::string::npos);
    }
    {
        // An agent state that was never read is refused: this Core cannot tell a
        // service that needs recovering from one that is working.
        AdmissionInput input = good();
        input.agent.known = false;
        const AdmissionVerdict verdict = admit(input);
        CHECK(!verdict.admitted);
        CHECK_EQ(verdict.code, std::string(kAdmitAgentStateUnknown));
    }
    {
        // THE LEASE, and it is checked before every other fact: with two writers
        // running, every judgement below is being made twice.
        AdmissionInput input = good();
        input.lease.held_by_us = false;
        input.lease.holder = "device:llm-lab-dell";
        const AdmissionVerdict verdict = admit(input);
        CHECK(!verdict.admitted);
        CHECK_EQ(verdict.code, std::string(kAdmitNoLease));
        CHECK_CONTAINS(verdict.reason, "device:llm-lab-dell");
    }
    {
        // Precedence, stated rather than left to the order the code happens to
        // have: lease > mask > install > link > agent > measured.
        {
            AdmissionInput input = good();
            input.lease.held_by_us = false;
            input.masked = true;
            input.installed = false;
            input.link.up = false;
            input.agent.busy = true;
            CHECK_EQ(admit(input).code, std::string(kAdmitNoLease));
        }
        {
            AdmissionInput input = good();
            input.masked = true;
            input.installed = false;
            input.link.up = false;
            input.agent.busy = true;
            CHECK_EQ(admit(input).code, std::string(kAdmitMasked));
        }
        {
            AdmissionInput input = good();
            input.installed = false;
            input.link.up = false;
            input.agent.busy = true;
            CHECK_EQ(admit(input).code, std::string(kAdmitNotInstalled));
        }
        {
            AdmissionInput input = good();
            input.link.up = false;
            input.agent.busy = true;
            CHECK_EQ(admit(input).code, std::string(kAdmitLinkDown));
        }
        {
            AdmissionInput input = good();
            input.agent.busy = true;
            CHECK_EQ(admit(input).code, std::string(kAdmitAgentBusy));
        }
    }
    {
        // An observation the provider could not take is refused even when every
        // other fact is good. The planner refuses this too; the gate refuses it
        // rather than trusting the caller to have checked.
        AdmissionInput input = good();
        static Observed unread = Observed::unavailable("toy.service", "systemctl printed nothing");
        input.observed = &unread;
        const AdmissionVerdict verdict = admit(input);
        CHECK(!verdict.admitted);
        CHECK_CONTAINS(verdict.reason, "not readable");
    }
    {
        // READINESS. A process that exists is not a service that answers, and
        // this is the only place that judgement is made.
        Observed row;
        row.unit = "toy.service";
        row.measured = true;

        row.active = Active::Active;
        CHECK_EQ(to_word(readiness_of(row)), std::string("READY"));
        row.active = Active::Failed;
        CHECK_EQ(to_word(readiness_of(row)), std::string("NOT_READY"));
        row.active = Active::Inactive;
        CHECK_EQ(to_word(readiness_of(row)), std::string("NOT_READY"));
        row.active = Active::Activating;
        CHECK_EQ(to_word(readiness_of(row)), std::string("NOT_READY"));
        row.active = Active::Deactivating;
        CHECK_EQ(to_word(readiness_of(row)), std::string("NOT_READY"));
        // `Unknown` is NOT ready and NOT unmeasured: a measurement was taken and
        // it said the state is not one this version knows.
        row.active = Active::Unknown;
        CHECK_EQ(to_word(readiness_of(row)), std::string("NOT_READY"));

        row.measured = false;
        CHECK_EQ(to_word(readiness_of(row)), std::string("UNMEASURED"));
    }

    return check::report("test_sup_admission");
}
