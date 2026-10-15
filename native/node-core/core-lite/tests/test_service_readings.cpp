// The `services` observation: owner gate, allowlist, inactive vs unknown, and
// the unit that is not there at all.
//
// EVERY case here is a fixture, and not one of them starts a process. The
// projection is a pure function of (profile, account readback, query answers),
// so the awkward cases — another account's unit, an unreadable state, a source
// that answers about the wrong unit — are all statable without a service
// manager, and the live path only has to prove that the wiring runs.
//
// The distinction this file exists to hold: `INACTIVE` and `NOT_INSTALLED` are
// different facts, and systemd answers `ActiveState=inactive` for a unit name
// that does not exist. Measured on this host: the declared unit and a fictional
// one both report `ActiveState=inactive LoadState=not-found`. A projection that
// printed `INACTIVE` for both would state that a service exists and is stopped
// when there is no service.
#include <iostream>
#include <string>
#include <vector>

#include "corelite/service_readings.hpp"
#include "json_detail.hpp"
#include "supervisor/systemd_user.hpp"

namespace {

int failures = 0;
int checks = 0;

void expect(bool condition, const std::string& what) {
    ++checks;
    if (!condition) {
        ++failures;
        std::cout << "  FAIL: " << what << "\n";
    }
}

using nodecore::corelite::OsIdentity;
using nodecore::corelite::Profile;
using nodecore::corelite::Reading;
using nodecore::corelite::ReadingState;
using nodecore::corelite::ServiceQuery;
using nodecore::supervisor::Observed;

//: A query that answers from a table and COUNTS what it was asked. The count is
//: how a test proves that a unit was not queried at all — which is the claim the
//: owner gate makes, and a claim no answer can demonstrate on its own.
class FakeQuery final : public ServiceQuery {
public:
    void add(const std::string& unit, const Observed& observed) {
        answers_.push_back({unit, observed});
    }

    Observed query(const std::string& unit) override {
        asked.push_back(unit);
        for (const auto& entry : answers_) {
            if (entry.first == unit) {
                return entry.second;
            }
        }
        // The same answer a service manager gives for a name nobody installed.
        return Observed::unavailable(unit, "no such unit");
    }

    std::vector<std::string> asked;

private:
    std::vector<std::pair<std::string, Observed>> answers_;
};

Observed observed(const std::string& unit, nodecore::supervisor::Active active,
                  const std::string& load_state, const std::string& sub_state = "dead") {
    Observed row;
    row.unit = unit;
    row.active = active;
    row.sub_state = sub_state;
    row.load_state = load_state;
    row.observed_at = "2026-10-16T00:00:00+00:00";
    row.age_seconds = 0;
    row.source = "fixture";
    row.measured = true;
    return row;
}

OsIdentity user(const std::string& name) {
    OsIdentity os;
    os.known = true;
    os.user = name;
    os.uid = 1002;
    os.home = "/data/home/" + name;
    return os;
}

Profile profile_with(const std::vector<std::string>& authorized,
                     const std::vector<nodecore::corelite::ServiceEntry>& services) {
    Profile profile;
    profile.schema = nodecore::corelite::kProfileSchema;
    profile.node = "device:tokyo-shared-dev-01";
    profile.principal = "vince";
    profile.platform = "linux";
    profile.observe_only = true;
    profile.generation = 1;
    for (const auto& name : authorized) {
        profile.observations.push_back({name, true});
    }
    profile.services = services;
    return profile;
}

nodecore::corelite::ServiceEntry entry(const std::string& id, const std::string& owner,
                                       const std::string& unit) {
    nodecore::corelite::ServiceEntry service;
    service.id = id;
    service.owner = owner;
    service.unit = unit;
    return service;
}

//: Re-read the produced value with the supervisor's own reader, so an assertion
//: is about the DOCUMENT and not about the writer's spacing.
std::string value_text(const Reading& reading) {
    return reading.has_value ? reading.value.dump_compact() : std::string();
}

bool contains(const std::string& haystack, const std::string& needle) {
    return haystack.find(needle) != std::string::npos;
}

// ---------------------------------------------------------------------------

void test_a_measured_unit_is_projected_with_its_own_words() {
    std::cout << "a measured unit is projected, and INACTIVE survives as INACTIVE\n";
    const Profile profile = profile_with({"services"}, {entry("fleet-node", "vince",
                                                              "serveros-fleet-node.service")});
    FakeQuery query;
    query.add("serveros-fleet-node.service",
              observed("serveros-fleet-node.service", nodecore::supervisor::Active::Inactive,
                       "loaded", "dead"));
    std::string error;
    const Reading reading = nodecore::corelite::services_reading(profile, user("vince"), query, error);
    expect(error.empty(), "no error: " + error);
    expect(reading.name == "services", "the reading is the services observation");
    expect(reading.state == ReadingState::Present, "it is PRESENT");
    expect(reading.completeness == nodecore::corelite::kComplete, "and COMPLETE");
    const std::string text = value_text(reading);
    // A unit that IS installed and stopped is INACTIVE, and that is a reading.
    expect(contains(text, "\"state\":\"INACTIVE\""), "INACTIVE is published for an installed unit");
    expect(contains(text, "\"sub_state\":\"dead\""), "with its SubState");
    expect(contains(text, "\"unit\":\"serveros-fleet-node.service\""), "and its unit id");
    expect(contains(text, "\"observed_at\":\"2026-10-16T00:00:00+00:00\""),
           "and the instant the reading was taken");
    expect(query.asked.size() == 1, "exactly one question was asked");
}

void test_a_unit_that_is_not_installed_is_not_INACTIVE() {
    std::cout << "a unit that does not exist is NOT_INSTALLED, not INACTIVE\n";
    // The case measured on this host: the declared unit is not installed, and
    // systemd answers ActiveState=inactive for it anyway. Reporting INACTIVE
    // would say a service exists and is stopped.
    const Profile profile = profile_with({"services"}, {entry("fleet-node", "vince",
                                                              "serveros-fleet-node.service")});
    FakeQuery query;
    query.add("serveros-fleet-node.service",
              observed("serveros-fleet-node.service", nodecore::supervisor::Active::Inactive,
                       "not-found", "dead"));
    std::string error;
    const Reading reading = nodecore::corelite::services_reading(profile, user("vince"), query, error);
    expect(reading.state == ReadingState::Present, "the reading is PRESENT — the question WAS answered");
    const std::string text = value_text(reading);
    expect(contains(text, "NOT_INSTALLED"), "the state is NOT_INSTALLED");
    expect(!contains(text, "\"state\":\"INACTIVE\""),
           "and NOT INACTIVE — the service is absent, not stopped");
}

void test_a_masked_unit_is_not_installed_either() {
    std::cout << "a masked unit is not installed: it was switched off on purpose\n";
    const Profile profile = profile_with({"services"}, {entry("fleet-node", "vince",
                                                              "serveros-fleet-node.service")});
    FakeQuery query;
    query.add("serveros-fleet-node.service",
              observed("serveros-fleet-node.service", nodecore::supervisor::Active::Inactive,
                       "masked", "dead"));
    std::string error;
    const Reading reading = nodecore::corelite::services_reading(profile, user("vince"), query, error);
    expect(contains(value_text(reading), "NOT_INSTALLED"), "a mask is the absence of a unit");
}

void test_a_load_state_that_was_not_read_is_refused_not_assumed() {
    std::cout << "an unread LoadState is UNAVAILABLE, not 'installed'\n";
    // The tri-state again: "we did not look" and "it is not there" are different
    // facts, and only one of them is about the host. An observation that carries
    // no load state must not be read as installed, and must not be read as
    // absent either — it is refused.
    const Profile profile = profile_with({"services"}, {entry("fleet-node", "vince",
                                                              "serveros-fleet-node.service")});
    FakeQuery query;
    query.add("serveros-fleet-node.service",
              observed("serveros-fleet-node.service", nodecore::supervisor::Active::Active, "",
                       "running"));
    std::string error;
    const Reading reading = nodecore::corelite::services_reading(profile, user("vince"), query, error);
    expect(reading.state == ReadingState::Unavailable, "the reading is UNAVAILABLE");
    // The whole observation is UNAVAILABLE, so there is no value at all — the
    // reason sits on the reading, not inside a value that does not exist.
    expect(reading.reason == "NO_SOURCE", "with NO_SOURCE");
    expect(!reading.has_value, "and no value, not even an empty one");
}

void test_an_unmeasured_answer_is_unavailable_and_never_inactive() {
    std::cout << "an unread unit is UNAVAILABLE, never INACTIVE\n";
    // `parse_systemctl_show` refuses an unknown ActiveState and an absent one
    // with measured = false. Those must not arrive here as a state word.
    const Profile profile = profile_with({"services"}, {entry("fleet-node", "vince",
                                                              "serveros-fleet-node.service")});
    FakeQuery query;
    query.add("serveros-fleet-node.service",
              Observed::unavailable("serveros-fleet-node.service", "systemctl printed no ActiveState"));
    std::string error;
    const Reading reading = nodecore::corelite::services_reading(profile, user("vince"), query, error);
    expect(reading.state == ReadingState::Unavailable, "the reading is UNAVAILABLE");
    expect(!contains(value_text(reading), "INACTIVE"), "and nothing says INACTIVE");
    // The producer's prose is NOT carried: it is not this document's vocabulary.
    expect(!contains(value_text(reading), "systemctl printed"),
           "the producer's prose does not travel");
}

void test_another_accounts_service_is_not_emitted_at_all() {
    std::cout << "a service owned by another account is not emitted and not even asked about\n";
    const Profile profile = profile_with(
        {"services"}, {entry("mine", "vince", "serveros-fleet-node.service"),
                       entry("theirs", "fjd", "serveros-dell-agent.service")});
    FakeQuery query;
    query.add("serveros-fleet-node.service",
              observed("serveros-fleet-node.service", nodecore::supervisor::Active::Active,
                       "loaded", "running"));
    std::string error;
    const Reading reading = nodecore::corelite::services_reading(profile, user("vince"), query, error);
    const std::string text = value_text(reading);
    expect(contains(text, "serveros-fleet-node.service"), "this account's service is there");
    // Absence, not an UNAVAILABLE row and not an empty string: a blank field
    // reads as «measured nothing», and its presence discloses that the other
    // account has a service there.
    expect(!contains(text, "serveros-dell-agent.service"),
           "the other account's unit does not appear at all");
    expect(!contains(text, "fjd"), "nor its owner");
    // And it was never asked about: asking would be the leak, whatever we then
    // did with the answer.
    expect(query.asked.size() == 1, "exactly one question was asked");
    for (const std::string& unit : query.asked) {
        expect(unit != "serveros-dell-agent.service", "the other account's unit was not queried");
    }
}

void test_a_profile_that_is_all_another_accounts_declines() {
    std::cout << "a profile whose services are all another account's reports nothing\n";
    // An empty list would claim "this account runs no services", which is false:
    // there are services, they are simply not ours to look at.
    const Profile profile = profile_with({"services"}, {entry("theirs", "fjd",
                                                              "serveros-dell-agent.service")});
    FakeQuery query;
    std::string error;
    const Reading reading = nodecore::corelite::services_reading(profile, user("vince"), query, error);
    expect(reading.state == ReadingState::Unavailable, "UNAVAILABLE rather than an empty list");
    expect(reading.reason == "IDENTITY_UNKNOWN", "with IDENTITY_UNKNOWN: nothing here is ours");
    expect(query.asked.empty(), "and no unit was queried");
}

void test_no_declared_services_is_present_and_empty() {
    std::cout << "a profile that declares no services is PRESENT with an empty list\n";
    // The other half of the pair above: "this account runs none" and "none of
    // these are ours" are different answers, and only the first is empty.
    const Profile profile = profile_with({"services"}, {});
    FakeQuery query;
    std::string error;
    const Reading reading = nodecore::corelite::services_reading(profile, user("vince"), query, error);
    expect(reading.state == ReadingState::Present, "PRESENT");
    expect(contains(value_text(reading), "\"services\":[]"), "with an empty list");
    expect(query.asked.empty(), "and nothing was asked");
}

void test_an_unresolved_account_refuses_everything() {
    std::cout << "without a resolved account there is no service this process may look at\n";
    const Profile profile = profile_with({"services"}, {entry("fleet-node", "vince",
                                                              "serveros-fleet-node.service")});
    FakeQuery query;
    std::string error;
    OsIdentity unresolved;  // known == false
    const Reading reading =
        nodecore::corelite::services_reading(profile, unresolved, query, error);
    expect(reading.state == ReadingState::Unavailable, "UNAVAILABLE");
    expect(reading.reason == "IDENTITY_UNKNOWN", "with IDENTITY_UNKNOWN");
    expect(query.asked.empty(), "and nothing was queried");
}

void test_a_malicious_unit_name_never_becomes_a_query() {
    std::cout << "a unit id that is not a unit id is refused before it is queried\n";
    // The reader refuses these too. This is the gate that runs where the string
    // would become a query, which is the one that has to hold.
    for (const std::string& hostile :
         {std::string("--now evil.service"), std::string("a b.service"),
          std::string("../../etc/passwd"), std::string("evil.service; rm -rf /"),
          std::string("x\n--user.service"), std::string("")}) {
        const Profile profile = profile_with({"services"}, {entry("hostile", "vince", hostile)});
        FakeQuery query;
        std::string error;
        const Reading reading =
            nodecore::corelite::services_reading(profile, user("vince"), query, error);
        // Either the query was never made, or the answer about some other unit
        // was refused. What must not happen is a projection carrying it.
        const std::string text = value_text(reading);
        expect(!contains(text, "rm -rf") && !contains(text, "passwd") && !contains(text, "--now"),
               "the hostile id is not published: \"" + hostile + "\"");
        for (const std::string& asked : query.asked) {
            expect(asked != hostile, "and it was never put to systemctl: \"" + hostile + "\"");
        }
    }
}

void test_an_answer_about_the_wrong_unit_is_refused() {
    std::cout << "a source that answers about another unit cannot have it published\n";
    // A query implementation that returns a row for a different unit must not be
    // able to get that row published under this entry's name.
    const Profile profile = profile_with({"services"}, {entry("fleet-node", "vince",
                                                              "serveros-fleet-node.service")});
    FakeQuery query;
    query.add("serveros-fleet-node.service",
              observed("some-other.service", nodecore::supervisor::Active::Active, "loaded",
                       "running"));
    std::string error;
    const Reading reading = nodecore::corelite::services_reading(profile, user("vince"), query, error);
    expect(reading.state == ReadingState::Unavailable, "the mismatched answer is refused");
    expect(!contains(value_text(reading), "\"state\":\"ACTIVE\""),
           "and its state is not published for this unit");
}

void test_one_unreadable_service_makes_the_reading_partial() {
    std::cout << "a partly-measured services list says so\n";
    const Profile profile = profile_with(
        {"services"}, {entry("a", "vince", "a.service"), entry("b", "vince", "b.service")});
    FakeQuery query;
    query.add("a.service", observed("a.service", nodecore::supervisor::Active::Active, "loaded",
                                    "running"));
    // b.service has no answer: the default is an unmeasured observation.
    std::string error;
    const Reading reading = nodecore::corelite::services_reading(profile, user("vince"), query, error);
    expect(reading.state == ReadingState::Present, "PRESENT — one of them was measured");
    expect(reading.completeness == nodecore::corelite::kPartial, "but PARTIAL");
    const std::string text = value_text(reading);
    expect(contains(text, "\"state\":\"ACTIVE\""), "the measured one is reported");
    expect(contains(text, "\"state\":\"UNAVAILABLE\""),
           "and the unreadable one is an explicit UNAVAILABLE in place");
    expect(contains(text, "\"reason\":\"NO_SOURCE\""), "with the closed reason");
}

void test_the_projection_carries_no_path_and_no_prose() {
    std::cout << "nothing but the named keys reaches the document\n";
    const Profile profile = profile_with({"services"}, {entry("fleet-node", "vince",
                                                              "serveros-fleet-node.service")});
    FakeQuery query;
    Observed row = observed("serveros-fleet-node.service", nodecore::supervisor::Active::Failed,
                            "loaded", "failed");
    // A producer that stuffed extra text into its observation must not have it
    // published: the projection names every key it emits.
    row.reason = "/usr/bin/systemctl --user show -- /etc/systemd/system/secret.service";
    query.add("serveros-fleet-node.service", row);
    std::string error;
    const Reading reading = nodecore::corelite::services_reading(profile, user("vince"), query, error);
    const std::string text = value_text(reading);
    expect(contains(text, "\"state\":\"FAILED\""), "the state word is published");
    for (const char* leaked : {"/usr/bin/systemctl", "/etc/systemd", "secret.service", "--user"}) {
        expect(!contains(text, leaked), std::string("and no ") + leaked);
    }
}

//: The parser that produces the observation, against the text this host's
//: systemctl really prints for a unit that does not exist. This is the fact the
//: whole NOT_INSTALLED distinction rests on, so it is asserted at its source.
void test_the_parser_carries_the_load_state_it_was_given() {
    std::cout << "parse_systemctl_show carries LoadState, which is what tells absent from stopped\n";
    const std::string text =
        "ActiveState=inactive\n"
        "SubState=dead\n"
        "LoadState=not-found\n";
    const Observed row = nodecore::supervisor::parse_systemctl_show(
        "serveros-fleet-node.service", text, "2026-10-16T00:00:00+00:00");
    expect(row.measured, "the observation is measured");
    expect(row.load_state == "not-found", "with the LoadState it was given");
    const std::optional<bool> installed = nodecore::supervisor::unit_is_installed(row);
    expect(installed.has_value(), "the tri-state has an answer");
    expect(installed.has_value() && !*installed, "and the unit is NOT installed");

    const Observed loaded = nodecore::supervisor::parse_systemctl_show(
        "x.service", "ActiveState=active\nSubState=running\nLoadState=loaded\n", "t");
    const std::optional<bool> is_installed = nodecore::supervisor::unit_is_installed(loaded);
    expect(is_installed.has_value() && *is_installed, "a loaded unit IS installed");

    // No LoadState at all: refused, because "we did not look" is not "it is not
    // there".
    const Observed silent = nodecore::supervisor::parse_systemctl_show(
        "x.service", "ActiveState=active\nSubState=running\n", "t");
    expect(!nodecore::supervisor::unit_is_installed(silent).has_value(),
           "a missing LoadState has no answer rather than a default");

    // An unknown LoadState word from a newer systemd: refused, not guessed.
    const Observed novel = nodecore::supervisor::parse_systemctl_show(
        "x.service", "ActiveState=active\nSubState=running\nLoadState=reticulating\n", "t");
    expect(!nodecore::supervisor::unit_is_installed(novel).has_value(),
           "an unknown LoadState is refused rather than read as installed");
}

}  // namespace

int main() {
    test_a_measured_unit_is_projected_with_its_own_words();
    test_a_unit_that_is_not_installed_is_not_INACTIVE();
    test_a_masked_unit_is_not_installed_either();
    test_a_load_state_that_was_not_read_is_refused_not_assumed();
    test_an_unmeasured_answer_is_unavailable_and_never_inactive();
    test_another_accounts_service_is_not_emitted_at_all();
    test_a_profile_that_is_all_another_accounts_declines();
    test_no_declared_services_is_present_and_empty();
    test_an_unresolved_account_refuses_everything();
    test_a_malicious_unit_name_never_becomes_a_query();
    test_an_answer_about_the_wrong_unit_is_refused();
    test_one_unreadable_service_makes_the_reading_partial();
    test_the_projection_carries_no_path_and_no_prose();
    test_the_parser_carries_the_load_state_it_was_given();

    std::cout << "\n" << checks << " checks, " << failures << " failures\n";
    return failures == 0 ? 0 : 1;
}
