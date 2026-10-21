// The resident acting Core's facts document: every way it stops being evidence.
//
// The load-bearing property is not "a good document parses". It is that a BAD one
// leaves link and agent UNKNOWN, because the admission gate refuses on unknown and
// that is the only thing standing between a stale file and a restart spent on a
// network fault. So most cases here assert the safe degradation, not the refusal
// code: a document that is stale must not still report `link.up == true`.
#include <string>

#include "check.hpp"
#include "supervisor/facts.hpp"

using namespace nodecore::supervisor;

namespace {

constexpr long kNow = 1'800'000'000;

std::string good_document() {
    return R"({
      "schema": "serveros/native-core-facts.v1",
      "observed_at": "2026-10-21T23:40:00+08:00",
      "observed_epoch": 1800000000,
      "link": {"known": true, "up": true},
      "agent": {"known": true, "busy": false},
      "services": [
        {"id": "svc-a", "runtime": "serving"},
        {"id": "svc-b", "runtime": "needs-login"},
        {"id": "svc-c", "runtime": "unknown", "maintenance": true,
         "maintenance_reason": "window"}
      ]
    })";
}

HostFacts load(const std::string& text) {
    return load_host_facts(text.c_str(), kNow);
}

void a_current_document_is_believed() {
    const HostFacts facts = load(good_document());
    CHECK(facts.usable);
    CHECK_EQ(facts.code, std::string(kFactsOk));
    CHECK(facts.link.known);
    CHECK(facts.link.up);
    CHECK(facts.agent.known);
    CHECK(!facts.agent.busy);
    CHECK_EQ(facts.age_seconds, 0L);
    CHECK_EQ(facts.services.size(), static_cast<std::size_t>(3));
}

void an_absent_document_leaves_every_fact_unknown() {
    // Absent is not "no facts declared"; it is "we did not look". The difference
    // decides whether a reader is sent to the writer or to the file.
    const HostFacts facts = load_host_facts(nullptr, kNow);
    CHECK(!facts.usable);
    CHECK_EQ(facts.code, std::string(kFactsAbsent));
    CHECK(!facts.link.known);
    CHECK(!facts.agent.known);
}

void malformed_json_is_refused() {
    const HostFacts facts = load("{\"schema\": ");
    CHECK(!facts.usable);
    CHECK_EQ(facts.code, std::string(kFactsMalformed));
    CHECK(!facts.link.known);
}

void a_foreign_schema_is_refused() {
    std::string text = good_document();
    text.replace(text.find("native-core-facts.v1"), 19, "some-other-document");
    const HostFacts facts = load(text);
    CHECK(!facts.usable);
    CHECK_EQ(facts.code, std::string(kFactsBadSchema));
}

void a_stale_document_does_not_stay_permissive() {
    // The failure this file exists for: a writer stops, the file keeps saying
    // "link up, nobody busy", and the loop keeps acting on a fact about the past.
    const HostFacts facts = load(good_document());
    CHECK(facts.link.up);
    FactsOptions options;
    options.max_age_seconds = 90;
    const HostFacts aged = load_host_facts(good_document().c_str(), kNow + 91, options);
    CHECK(!aged.usable);
    CHECK_EQ(aged.code, std::string(kFactsStale));
    CHECK(!aged.link.known);
    CHECK(!aged.agent.known);
    CHECK(!aged.link.up);
}

void the_age_bound_is_inclusive() {
    FactsOptions options;
    options.max_age_seconds = 90;
    const HostFacts at_limit = load_host_facts(good_document().c_str(), kNow + 90, options);
    CHECK(at_limit.usable);
    CHECK_EQ(at_limit.age_seconds, 90L);
}

void a_document_from_the_future_is_disbelieved() {
    FactsOptions options;
    options.future_skew_seconds = 5;
    const HostFacts facts = load_host_facts(good_document().c_str(), kNow - 30, options);
    CHECK(!facts.usable);
    CHECK_EQ(facts.code, std::string(kFactsFromTheFuture));
    CHECK(!facts.link.known);
}

void a_field_nobody_reads_is_refused() {
    std::string text = good_document();
    text.replace(text.find("\"link\""), 6, "\"link_typo\": {}, \"link\"");
    const HostFacts facts = load(text);
    CHECK(!facts.usable);
    CHECK_EQ(facts.code, std::string(kFactsMalformed));
}

void a_contradictory_link_is_refused() {
    std::string text = good_document();
    text.replace(text.find("\"up\": true"), 10, "\"up\": true");
    // known=false while up=true: "I did not look, and it is up" is not a fact.
    const HostFacts facts = load_host_facts(
        R"({"schema":"serveros/native-core-facts.v1","observed_at":"x","observed_epoch":1800000000,
            "link":{"known":false,"up":true},"agent":{"known":true,"busy":false},"services":[]})",
        kNow);
    CHECK(!facts.usable);
    CHECK_EQ(facts.code, std::string(kFactsMalformed));
}

void a_contradictory_agent_is_refused() {
    const HostFacts facts = load_host_facts(
        R"({"schema":"serveros/native-core-facts.v1","observed_at":"x","observed_epoch":1800000000,
            "link":{"known":true,"up":true},"agent":{"known":false,"busy":true},"services":[]})",
        kNow);
    CHECK(!facts.usable);
    CHECK(!facts.agent.known);
}

void an_unbounded_fact_text_is_refused() {
    std::string long_reason(400, 'x');
    const HostFacts facts = load_host_facts(
        ("{\"schema\":\"serveros/native-core-facts.v1\",\"observed_at\":\"x\","
         "\"observed_epoch\":1800000000,\"link\":{\"known\":true,\"up\":false,"
         "\"reason\":\"" + long_reason + "\"},\"agent\":{\"known\":true,\"busy\":false},"
         "\"services\":[]}")
            .c_str(),
        kNow);
    CHECK(!facts.usable);
    CHECK_EQ(facts.code, std::string(kFactsMalformed));
}

void a_service_named_twice_is_refused() {
    const HostFacts facts = load_host_facts(
        R"({"schema":"serveros/native-core-facts.v1","observed_at":"x","observed_epoch":1800000000,
            "link":{"known":true,"up":true},"agent":{"known":true,"busy":false},
            "services":[{"id":"dup","runtime":"serving"},{"id":"dup","runtime":"needs-login"}]})",
        kNow);
    CHECK(!facts.usable);
    CHECK_EQ(facts.code, std::string(kFactsMalformed));
}

void an_unknown_runtime_word_is_refused() {
    const HostFacts facts = load_host_facts(
        R"({"schema":"serveros/native-core-facts.v1","observed_at":"x","observed_epoch":1800000000,
            "link":{"known":true,"up":true},"agent":{"known":true,"busy":false},
            "services":[{"id":"a","runtime":"probably-fine"}]})",
        kNow);
    CHECK(!facts.usable);
}

void maintenance_and_runtime_are_carried() {
    const HostFacts facts = load(good_document());
    const ServiceFact* a = facts.service("svc-a");
    const ServiceFact* b = facts.service("svc-b");
    const ServiceFact* c = facts.service("svc-c");
    CHECK(a != nullptr);
    CHECK(b != nullptr);
    CHECK(c != nullptr);
    if (a != nullptr) {
        CHECK(a->runtime.known);
        CHECK(!a->runtime.needs_login);
        CHECK(!a->maintenance);
    }
    if (b != nullptr) {
        CHECK(b->runtime.known);
        CHECK(b->runtime.needs_login);
        CHECK_EQ(b->runtime.detail, std::string("NeedsLogin"));
    }
    if (c != nullptr) {
        CHECK(c->maintenance);
        CHECK_EQ(c->maintenance_reason, std::string("window"));
    }
}

void an_undescribed_service_has_no_fact() {
    // Null, not a default: a service the owner's document does not mention has an
    // UNKNOWN runtime, which is not the same as serving.
    const HostFacts facts = load(good_document());
    CHECK(facts.service("svc-not-mentioned") == nullptr);
}

void a_missing_epoch_is_refused() {
    const HostFacts facts = load_host_facts(
        R"({"schema":"serveros/native-core-facts.v1","observed_at":"x",
            "link":{"known":true,"up":true},"agent":{"known":true,"busy":false},"services":[]})",
        kNow);
    CHECK(!facts.usable);
    CHECK_EQ(facts.code, std::string(kFactsMalformed));
}

}  // namespace

int main() {
    a_current_document_is_believed();
    an_absent_document_leaves_every_fact_unknown();
    malformed_json_is_refused();
    a_foreign_schema_is_refused();
    a_stale_document_does_not_stay_permissive();
    the_age_bound_is_inclusive();
    a_document_from_the_future_is_disbelieved();
    a_field_nobody_reads_is_refused();
    a_contradictory_link_is_refused();
    a_contradictory_agent_is_refused();
    an_unbounded_fact_text_is_refused();
    a_service_named_twice_is_refused();
    an_unknown_runtime_word_is_refused();
    maintenance_and_runtime_are_carried();
    an_undescribed_service_has_no_fact();
    a_missing_epoch_is_refused();
    return check::report("test_sup_facts");
}
