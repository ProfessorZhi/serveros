// The resident acting Core's facts document: every way it stops being evidence.
//
// The load-bearing property is not "a good document parses". It is that a BAD one
// leaves link and agent UNKNOWN, because the admission gate refuses on unknown and
// that is the only thing standing between a stale file and a restart spent on a
// network fault. So most cases here assert the safe degradation, not the refusal
// code: a document that is stale must not still report `link.up == true`.
#include <cstddef>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "check.hpp"
#include "supervisor/facts.hpp"

using namespace nodecore::supervisor;

namespace {

constexpr long kNow = 1'800'000'000;

// The shared owner map has TWO readers, and this file is where their agreement
// is asserted. Python's `OwnerMap.load` refuses more than `MAX_SERVICES` entries
// and a unit id longer than `MAX_FACT_TEXT`; these helpers build documents on
// either side of the same two numbers so the C++ reader cannot accept a map its
// writer would have refused.
std::string owner_map_naming(const std::vector<std::string>& units, const char* owner) {
    std::string text = R"({"schema":"serveros/native-core-owner-map.v1","units":{)";
    for (std::size_t i = 0; i < units.size(); ++i) {
        if (i != 0) text += ",";
        text += "\"" + units[i] + "\":\"" + owner + "\"";
    }
    text += "}}";
    return text;
}

Manifest manifest_naming(const std::vector<std::string>& units) {
    Manifest manifest;
    for (std::size_t i = 0; i < units.size(); ++i) {
        ServiceDesired service;
        service.id = "svc-" + std::to_string(i);
        service.unit = units[i];
        service.owner = "fjd";
        manifest.services.push_back(service);
    }
    return manifest;
}

std::string read_text(const std::string& path) {
    std::ifstream stream(path);
    if (!stream.good()) {
        return std::string();
    }
    std::ostringstream buffer;
    buffer << stream.rdbuf();
    return buffer.str();
}

// Read `<name> = <digits>` out of the Python writer, so the two constants are
// compared rather than trusted. A missing assignment is a failure, not a skip:
// the file is in this repository, and a test that quietly passes when it cannot
// find its subject is the kind of green run this suite exists to refuse.
bool python_integer(const std::string& text, const std::string& name, std::size_t& out) {
    const std::string needle = "\n" + name + " = ";
    const std::size_t at = text.find(needle);
    if (at == std::string::npos) {
        return false;
    }
    std::size_t index = at + needle.size();
    std::size_t value = 0;
    bool any = false;
    while (index < text.size() && text[index] >= '0' && text[index] <= '9') {
        value = value * 10 + static_cast<std::size_t>(text[index] - '0');
        ++index;
        any = true;
    }
    if (!any) {
        return false;
    }
    out = value;
    return true;
}

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

void a_well_formed_owner_map_names_owners() {
    const OwnerMap map = load_owner_map(
        R"({"schema":"serveros/native-core-owner-map.v1",
            "units":{"toy.service":"serverosd","core.service":"native-core"}})");
    CHECK(map.usable);
    const std::string* toy = map.owner_of("toy.service");
    const std::string* core = map.owner_of("core.service");
    CHECK(toy != nullptr);
    CHECK(core != nullptr);
    if (toy != nullptr) CHECK_EQ(*toy, std::string("serverosd"));
    if (core != nullptr) CHECK_EQ(*core, std::string("native-core"));
    // An undeclared unit is NOT "fine": it has no verified owner.
    CHECK(map.owner_of("unlisted.service") == nullptr);
}

void an_owner_map_with_an_unknown_owner_is_refused() {
    const OwnerMap map = load_owner_map(
        R"({"schema":"serveros/native-core-owner-map.v1","units":{"toy.service":"whoever"}})");
    CHECK(!map.usable);
    CHECK(!map.owner_of("toy.service"));
}

void an_owner_map_naming_another_schema_is_refused() {
    const OwnerMap map = load_owner_map(
        R"({"schema":"serveros/something-else","units":{}})");
    CHECK(!map.usable);
}

void an_absent_owner_map_is_not_usable() {
    const OwnerMap map = load_owner_map(nullptr);
    CHECK(!map.usable);
    CHECK(map.owner_of("toy.service") == nullptr);
}

void an_owner_map_with_a_stray_field_is_refused() {
    const OwnerMap map = load_owner_map(
        R"({"schema":"serveros/native-core-owner-map.v1","units":{},"extra":1})");
    CHECK(!map.usable);
}

// ---------------------------------------------------------------- the bounds
//
// One owner per unit is only a fact if both programs read the same document as
// the same size. Each case sits exactly on a bound, with the accepting side
// asserted too — a reader that refused everything would pass a one-sided test.

void the_cpp_bounds_are_the_python_readers_bounds() {
    const std::string path =
        std::string(SUPERVISOR_SOURCE_DIR) + "/../../../runtime/native_core_facts.py";
    const std::string text = read_text(path);
    CHECK(!text.empty());
    std::size_t services = 0;
    std::size_t fact_text = 0;
    CHECK(python_integer(text, "MAX_SERVICES", services));
    CHECK(python_integer(text, "MAX_FACT_TEXT", fact_text));
    CHECK_EQ(kMaxOwnerEntries, services);
    CHECK_EQ(kMaxOwnerUnitBytes, fact_text);
}

void an_owner_map_at_the_entry_cap_is_accepted() {
    std::vector<std::string> units;
    for (std::size_t i = 0; i < kMaxOwnerEntries; ++i) {
        units.push_back("svc" + std::to_string(i) + ".service");
    }
    const OwnerMap map = load_owner_map(owner_map_naming(units, kNativeCoreOwner).c_str());
    CHECK(map.usable);
    CHECK_EQ(map.owners.size(), kMaxOwnerEntries);
}

void an_owner_map_beyond_the_entry_cap_is_refused() {
    std::vector<std::string> units;
    for (std::size_t i = 0; i <= kMaxOwnerEntries; ++i) {
        units.push_back("svc" + std::to_string(i) + ".service");
    }
    const OwnerMap map = load_owner_map(owner_map_naming(units, kNativeCoreOwner).c_str());
    CHECK(!map.usable);
    CHECK_EQ(map.code, std::string(kFactsMalformed));
    // Refused WHOLE, not truncated to the first 64: a reader that kept a prefix
    // would report a map that is not the document it was given.
    CHECK_EQ(map.owners.size(), std::size_t{0});
}

void an_owner_map_at_the_unit_length_bound_is_accepted() {
    const std::string unit(kMaxOwnerUnitBytes, 'a');
    const OwnerMap map = load_owner_map(owner_map_naming({unit}, kNativeCoreOwner).c_str());
    CHECK(map.usable);
    CHECK(map.owner_of(unit) != nullptr);
}

void an_owner_map_with_an_over_long_unit_is_refused() {
    const std::string unit(kMaxOwnerUnitBytes + 1, 'a');
    const OwnerMap map = load_owner_map(owner_map_naming({unit}, kNativeCoreOwner).c_str());
    CHECK(!map.usable);
    CHECK_EQ(map.code, std::string(kFactsMalformed));
}

void an_owner_map_naming_one_unit_twice_is_refused() {
    // Python refuses this with `object_pairs_hook`; the C++ reader refuses it as
    // a duplicate key. The same document is malformed to both, which is the
    // property that matters — a map that resolved differently on each side would
    // be a map that assigned a unit to two programs at once.
    const OwnerMap map = load_owner_map(
        R"({"schema":"serveros/native-core-owner-map.v1",
            "units":{"toy.service":"native-core","toy.service":"serverosd"}})");
    CHECK(!map.usable);
    CHECK(!map.owner_of("toy.service"));
}

// ------------------------------------------------------- the executor gate

void a_manifest_the_map_assigns_to_this_core_has_no_refusals() {
    const OwnerMap owners = load_owner_map(
        owner_map_naming({"a.service", "b.service"}, kNativeCoreOwner).c_str());
    CHECK(owners.usable);
    CHECK(manifest_owner_refusals(manifest_naming({"a.service", "b.service"}), owners).empty());
}

void a_manifest_with_no_services_has_no_refusals() {
    const OwnerMap owners = load_owner_map(
        R"({"schema":"serveros/native-core-owner-map.v1","units":{}})");
    CHECK(owners.usable);
    CHECK(manifest_owner_refusals(Manifest{}, owners).empty());
}

void a_unit_the_map_gives_to_the_python_daemon_is_refused() {
    const OwnerMap owners = load_owner_map(
        R"({"schema":"serveros/native-core-owner-map.v1","units":{"toy.service":"serverosd"}})");
    const std::vector<OwnerRefusal> refusals =
        manifest_owner_refusals(manifest_naming({"toy.service"}), owners);
    CHECK_EQ(refusals.size(), std::size_t{1});
    if (!refusals.empty()) {
        CHECK_EQ(refusals[0].unit, std::string("toy.service"));
        CHECK_EQ(refusals[0].owner, std::string("serverosd"));
    }
}

void an_undeclared_unit_is_refused_with_no_owner() {
    const OwnerMap owners = load_owner_map(
        R"({"schema":"serveros/native-core-owner-map.v1","units":{"mine.service":"native-core"}})");
    const std::vector<OwnerRefusal> refusals =
        manifest_owner_refusals(manifest_naming({"someone-elses.service"}), owners);
    CHECK_EQ(refusals.size(), std::size_t{1});
    if (!refusals.empty()) {
        CHECK_EQ(refusals[0].unit, std::string("someone-elses.service"));
        // Empty, not "unknown": an undeclared unit has NO owner, and a caller
        // that printed a made-up one would be inventing a fact.
        CHECK(refusals[0].owner.empty());
    }
}

void only_the_units_the_map_withholds_are_refused() {
    const OwnerMap owners = load_owner_map(
        R"({"schema":"serveros/native-core-owner-map.v1",
            "units":{"mine.service":"native-core","theirs.service":"serverosd"}})");
    const std::vector<OwnerRefusal> refusals = manifest_owner_refusals(
        manifest_naming({"mine.service", "theirs.service", "unlisted.service"}), owners);
    CHECK_EQ(refusals.size(), std::size_t{2});
    for (const OwnerRefusal& refusal : refusals) {
        CHECK(refusal.unit != "mine.service");
    }
}

void an_unusable_map_refuses_every_unit() {
    const OwnerMap owners = load_owner_map(nullptr);
    CHECK(!owners.usable);
    const std::vector<OwnerRefusal> refusals =
        manifest_owner_refusals(manifest_naming({"a.service", "b.service"}), owners);
    CHECK_EQ(refusals.size(), std::size_t{2});
    for (const OwnerRefusal& refusal : refusals) {
        CHECK(refusal.owner.empty());
    }
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
    a_well_formed_owner_map_names_owners();
    an_owner_map_with_an_unknown_owner_is_refused();
    an_owner_map_naming_another_schema_is_refused();
    an_absent_owner_map_is_not_usable();
    an_owner_map_with_a_stray_field_is_refused();
    the_cpp_bounds_are_the_python_readers_bounds();
    an_owner_map_at_the_entry_cap_is_accepted();
    an_owner_map_beyond_the_entry_cap_is_refused();
    an_owner_map_at_the_unit_length_bound_is_accepted();
    an_owner_map_with_an_over_long_unit_is_refused();
    an_owner_map_naming_one_unit_twice_is_refused();
    a_manifest_the_map_assigns_to_this_core_has_no_refusals();
    a_manifest_with_no_services_has_no_refusals();
    a_unit_the_map_gives_to_the_python_daemon_is_refused();
    an_undeclared_unit_is_refused_with_no_owner();
    only_the_units_the_map_withholds_are_refused();
    an_unusable_map_refuses_every_unit();
    return check::report("test_sup_facts");
}
