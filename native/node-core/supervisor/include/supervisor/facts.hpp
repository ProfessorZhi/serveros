// Host facts for a Resident Core that acts.
//
// WHY THIS FILE EXISTS
// --------------------
// `contract/bounded-recovery.v1.md` §6 says plainly that there is no link or
// agent provider, that the facts are supplied by the caller as one-shot flags,
// and that reading them from the host is "a separate change". A resident Core
// that acts is exactly that change: a flag given once at startup is a claim
// about the instant the process started, and a loop that keeps using it is a
// loop that hard-codes LINK_UP and AGENT_IDLE for as long as it runs.
//
// So the resident loop re-reads its facts every tick, from a small document the
// OS owner writes. This module is only the *reading*: it parses, bounds and ages
// that document and hands back the same `LinkFacts` / `AgentFacts` /
// `ServiceRuntime` the existing admission gate already consumes. The gate, the
// planner, the budget, the lease and the breaker are untouched.
//
// THE FAILURE MODE IT REFUSES
// ---------------------------
// Every failure here yields `link.known == false` and `agent.known == false`,
// and the existing gate refuses on unknown. There is no default that means
// "probably fine": absent, unreadable, stale, malformed and contradictory all
// end in the same safe place, because the alternative — assuming the host is
// connected because we could not read that it is not — is the permissive
// default this contract exists to remove.
#pragma once

#include <string>
#include <vector>

#include "supervisor/admission.hpp"

namespace nodecore::supervisor {

//: The document this module reads. Bumped if a field's meaning changes.
inline constexpr const char* kFactsSchema = "serveros/native-core-facts.v1";

//: The closed vocabulary of reasons a facts document is unusable. A caller
//: switches on these; it never parses a sentence.
inline constexpr const char* kFactsOk = "FACTS_OK";
inline constexpr const char* kFactsAbsent = "FACTS_ABSENT";
inline constexpr const char* kFactsUnreadable = "FACTS_UNREADABLE";
inline constexpr const char* kFactsBadSchema = "FACTS_SCHEMA";
inline constexpr const char* kFactsMalformed = "FACTS_MALFORMED";
inline constexpr const char* kFactsStale = "FACTS_STALE";
inline constexpr const char* kFactsFromTheFuture = "FACTS_FROM_THE_FUTURE";

struct ServiceFact {
    std::string id;
    //: The service's own runtime. Absence is NOT a refusal here, matching
    //: `ServiceRuntime`'s documented asymmetry: a service with no runtime to
    //: report is not a service that needs a person.
    ServiceRuntime runtime;
    //: A person put the service into maintenance. Acting on it is undoing a
    //: decision, which the admission gate already refuses for MASKED; this is
    //: the same statement made by the owner rather than by systemd.
    bool maintenance = false;
    std::string maintenance_reason;
};

struct HostFacts {
    //: True only when the document was present, well-formed, current, and named
    //: this schema. Everything else leaves the fact structs unknown.
    bool usable = false;
    std::string code = kFactsAbsent;
    std::string reason;
    //: What the owner wrote, kept for the receipt. Never interpreted.
    std::string observed_at;
    //: now_epoch - observed_epoch. Negative only when the writer's clock is
    //: ahead of ours; `-1` when nothing was read.
    long age_seconds = -1;
    LinkFacts link;
    AgentFacts agent;
    std::vector<ServiceFact> services;

    //: The fact for a service id, or null. Null means "this document does not
    //: describe it", which leaves its runtime unknown rather than serving.
    const ServiceFact* service(const std::string& id) const;
};

struct FactsOptions {
    //: A document older than this is not evidence about now. Default 90 s: long
    //: enough for a 60 s writer cadence to be late once, short enough that a
    //: stopped writer cannot keep authorising actions through a whole shift.
    long max_age_seconds = 90;
    //: How far into the future a writer's clock may be before the document is
    //: disbelieved. A small allowance for two clocks on one host, no more.
    long future_skew_seconds = 5;
};

//: The document that says which program owns which unit. Python writes it; this
//: Core reads the SAME file, so "one owner per service" is a fact both sides
//: check rather than a rule one side remembers.
inline constexpr const char* kOwnerMapSchema = "serveros/native-core-owner-map.v1";

struct OwnerMap {
    bool usable = false;
    std::string code;
    std::string reason;
    std::vector<std::pair<std::string, std::string>> owners;

    //: The owner of `unit`, or null when the map does not mention it. Null is
    //: not "fine": a Core manifest naming an undeclared unit has no verified
    //: owner, and the caller refuses on that.
    const std::string* owner_of(const std::string& unit) const;
};

//: Parse one owner map. `text` is null when the file could not be read.
OwnerMap load_owner_map(const char* text);

//: Parse and age one facts document.
//:
//: `text` is null when the file could not be read at all — that is
//: FACTS_ABSENT rather than an empty-document parse, because the two have
//: different fixes and only one of them is the owner's.
//:
//: `now_epoch` is injected so a test states an instant instead of sleeping.
HostFacts load_host_facts(const char* text, long now_epoch, const FactsOptions& options = {});

}  // namespace nodecore::supervisor
