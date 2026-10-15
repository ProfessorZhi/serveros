// Targeted tests for `serveros/core-lite-status.v1` and the runtime identity.
//
// Each test holds ONE product rule, and asserts the reason word as well as the
// fact: a test that only checked `state == "UNAVAILABLE"` would pass for
// UNSUPPORTED_PLATFORM when the truth was NO_SOURCE, and those send a reader to
// two different places.
#include <iostream>
#include <string>
#include <utility>
#include <vector>

#include "corelite/identity.hpp"
#include "corelite/status.hpp"

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

using nodecore::corelite::Profile;
using nodecore::corelite::kProfileSchema;
using nodecore::corelite::kStatusSchema;

Profile profile_with(const std::vector<std::string>& authorized_names,
                     const std::string& principal = "vince") {
    Profile profile;
    profile.schema = kProfileSchema;
    profile.node = "device:tokyo-shared-dev-01";
    profile.principal = principal;
    profile.platform = "linux";
    profile.observe_only = true;
    profile.generation = 3;
    for (const auto& name : authorized_names) {
        profile.observations.push_back({name, true});
    }
    return profile;
}

nodecore::corelite::Reading present(const std::string& name, const std::string& value) {
    nodecore::corelite::Reading reading;
    reading.name = name;
    reading.state = nodecore::corelite::ReadingState::Present;
    reading.source = "nodecore/NodeResourceSnapshot v1";
    reading.observed_at = "2026-10-15T23:07:00+00:00";
    reading.value_json = value;
    return reading;
}

nodecore::corelite::StatusInput base_input(const Profile& profile) {
    nodecore::corelite::StatusInput input;
    input.profile = &profile;
    input.observed_at = "2026-10-15T23:07:00+00:00";
    input.device_ref_known = true;
    input.device_ref_observed = profile.node;
    input.principal_known = true;
    input.principal_observed = profile.principal;
    return input;
}

bool contains(const std::string& haystack, const std::string& needle) {
    return haystack.find(needle) != std::string::npos;
}

void test_a_matched_principal_and_device_is_live() {
    std::cout << "matched identity is live\n";
    const Profile profile = profile_with({"cpu", "memory"});
    auto input = base_input(profile);
    input.readings.push_back(present("cpu", "{\"used_percent\":12.5}"));
    const auto outcome = nodecore::corelite::build_status(input);
    expect(outcome.ok, "status built");
    expect(outcome.live, "live: true");
    expect(contains(outcome.json, "\"mode\": \"observe-only\"") ||
               contains(outcome.json, "\"mode\":\"observe-only\""),
           "mode is observe-only");
    expect(contains(outcome.json, "\"schema\": \"" + std::string(kStatusSchema) + "\"") ||
               contains(outcome.json, "\"schema\":\"" + std::string(kStatusSchema) + "\""),
           "the status schema is stamped");
}

void test_an_unauthorised_observation_is_never_emitted() {
    std::cout << "an unauthorised observation does not appear at all\n";
    Profile profile = profile_with({"cpu"});
    // memory is NOT granted. The adapter has a reading for it anyway.
    auto input = base_input(profile);
    input.readings.push_back(present("cpu", "1"));
    input.readings.push_back(present("memory", "{\"used_percent\":80.0}"));
    const auto outcome = nodecore::corelite::build_status(input);
    expect(outcome.ok, "status built");
    expect(!contains(outcome.json, "memory"),
           "the ungranted name is absent — not UNAVAILABLE, ABSENT");
    expect(contains(outcome.json, "cpu"), "the granted one is present");
}

void test_a_missing_reading_is_unavailable_and_never_zero() {
    std::cout << "a granted observation with no reading is UNAVAILABLE/NO_SOURCE\n";
    const Profile profile = profile_with({"cpu", "memory"});
    const auto input = base_input(profile);  // no readings at all
    const auto outcome = nodecore::corelite::build_status(input);
    expect(outcome.ok, "status built");
    expect(contains(outcome.json, "NO_SOURCE"), "reason is NO_SOURCE");
    expect(!contains(outcome.json, "\"value\": 0") && !contains(outcome.json, "\"value\":0"),
           "nothing is rendered as zero");
    expect(!contains(outcome.json, "\"state\": \"PRESENT\"") &&
               !contains(outcome.json, "\"state\":\"PRESENT\""),
           "nothing claims PRESENT");
}

void test_windows_psi_is_unsupported_and_not_implemented_is_different() {
    std::cout << "UNSUPPORTED_PLATFORM and NOT_IMPLEMENTED are two words\n";
    Profile profile = profile_with({"cpu", "psi.memory"});
    profile.platform = "windows";
    auto input = base_input(profile);
    input.platform_supports_psi = false;

    // (a) a Windows host with a collector: cpu NOT_IMPLEMENTED (none written),
    //     psi UNSUPPORTED_PLATFORM (the platform cannot, ever).
    input.platform_has_collector = false;
    const auto cold = nodecore::corelite::build_status(input);
    expect(contains(cold.json, "UNSUPPORTED_PLATFORM"), "psi is UNSUPPORTED_PLATFORM");
    expect(contains(cold.json, "NOT_IMPLEMENTED"), "the rest is NOT_IMPLEMENTED");
    expect(!contains(cold.json, "\"value\""), "a Windows host reports no values at all");

    // (b) a Windows host that DOES have a collector: psi still cannot be read.
    input.platform_has_collector = true;
    input.readings.push_back(present("cpu", "{\"used_percent\":9.0}"));
    const auto warm = nodecore::corelite::build_status(input);
    expect(contains(warm.json, "UNSUPPORTED_PLATFORM"), "psi is still UNSUPPORTED_PLATFORM");
    expect(contains(warm.json, "9.0"), "cpu is PRESENT once a collector exists");
}

void test_a_principal_mismatch_reports_nothing() {
    std::cout << "a principal mismatch emits no observations\n";
    const Profile profile = profile_with({"cpu"});
    auto input = base_input(profile);
    input.principal_observed = "someoneelse";
    input.readings.push_back(present("cpu", "{\"used_percent\":12.5}"));
    const auto outcome = nodecore::corelite::build_status(input);
    expect(outcome.ok, "the status is still built (the verdict is the content)");
    expect(!outcome.live, "live: false");
    expect(outcome.principal.state == "MISMATCH", "principal is MISMATCH");
    expect(!contains(outcome.json, "used_percent"),
           "no reading is filed under an account that is not this one");
}

void test_an_unknown_principal_reports_nothing() {
    std::cout << "an unreadable identity emits no observations\n";
    const Profile profile = profile_with({"cpu"});
    auto input = base_input(profile);
    input.principal_known = false;
    input.principal_observed.clear();
    input.readings.push_back(present("cpu", "{\"used_percent\":12.5}"));
    const auto outcome = nodecore::corelite::build_status(input);
    expect(!outcome.live, "live: false");
    expect(outcome.principal.state == "UNKNOWN", "principal is UNKNOWN");
    expect(!contains(outcome.json, "used_percent"), "no reading under an unknown account");
}

void test_a_device_mismatch_stops_the_live_claim() {
    std::cout << "a device that is not the managed node is not live\n";
    const Profile profile = profile_with({"cpu"});
    for (const auto& pair : std::vector<std::pair<bool, std::string>>{
             {false, ""}, {true, "device:llm-lab-dell"}}) {
        auto input = base_input(profile);
        input.device_ref_known = pair.first;
        input.device_ref_observed = pair.second;
        input.readings.push_back(present("cpu", "1"));
        const auto outcome = nodecore::corelite::build_status(input);
        expect(!outcome.live, "live: false for a device that is not this node");
        expect(outcome.device.state != "MATCHED", "device is not MATCHED");
    }
}

//: PINS A DELIBERATE DECISION. When the principal matches but the device
//: declaration was not found, the readings ARE emitted and `live` is false.
//: Readings are this account's measurements of the machine it is on — proven by
//: the principal gate — while `live` is the claim that this really is the
//: declared device, which is a different claim. The alternative (suppressing all
//: telemetry when one line of config is missing) would take a monitor dark for a
//: reason that has nothing to do with what it monitors.
void test_an_unknown_device_still_reports_this_accounts_readings() {
    std::cout << "an unknown device reference stops the live claim, not the readings\n";
    const Profile profile = profile_with({"cpu"});
    auto input = base_input(profile);
    input.device_ref_known = false;
    input.device_ref_observed.clear();
    input.readings.push_back(present("cpu", "{\"used_percent\":12.5}"));
    const auto outcome = nodecore::corelite::build_status(input);
    expect(outcome.ok, "status built");
    expect(!outcome.live, "live: false — the device is not established");
    expect(outcome.device.state == "UNKNOWN", "the device verdict says UNKNOWN");
    expect(!outcome.device.reason.empty(), "and says why");
    expect(contains(outcome.json, "used_percent"),
           "the account's own readings are still reported");
}

//: UNKNOWN and STALE are answers, not gaps. Neither may be rendered as 0, and
//: neither may be silently renamed.
void test_stale_and_unknown_are_carried_and_never_zero() {
    std::cout << "a STALE reading stays STALE and carries no number\n";
    const Profile profile = profile_with({"cpu", "memory"});
    auto input = base_input(profile);
    nodecore::corelite::Reading stale;
    stale.name = "cpu";
    stale.state = nodecore::corelite::ReadingState::Unavailable;
    stale.reason = "STALE";
    stale.source = "nodecore/NodeResourceSnapshot v1";
    input.readings.push_back(stale);
    nodecore::corelite::Reading unknown;
    unknown.name = "memory";
    unknown.state = nodecore::corelite::ReadingState::Unavailable;
    unknown.reason = "IDENTITY_UNKNOWN";
    input.readings.push_back(unknown);

    const auto outcome = nodecore::corelite::build_status(input);
    expect(contains(outcome.json, "STALE"), "the stale reading is reported as STALE");
    expect(contains(outcome.json, "IDENTITY_UNKNOWN"), "the unknown one keeps its own word");
    expect(!contains(outcome.json, "\"value\""), "neither carries a value — not a zero, not null");
    expect(!contains(outcome.json, "\"state\": \"PRESENT\""), "and neither claims PRESENT");
}

//: The reason vocabulary is CLOSED. A typo must not become a new word a Fleet
//: reader has never seen, so an unrecognised reason is reported as NO_SOURCE.
void test_a_reason_outside_the_closed_set_becomes_no_source() {
    std::cout << "a reason nobody defined does not become a new vocabulary word\n";
    expect(nodecore::corelite::known_reason("STALE"), "STALE is a known reason");
    expect(!nodecore::corelite::known_reason("BANANA"), "BANANA is not");
    const Profile profile = profile_with({"cpu"});
    auto input = base_input(profile);
    nodecore::corelite::Reading odd;
    odd.name = "cpu";
    odd.state = nodecore::corelite::ReadingState::Unavailable;
    odd.reason = "BANANA";
    input.readings.push_back(odd);
    const auto outcome = nodecore::corelite::build_status(input);
    expect(!contains(outcome.json, "BANANA"), "the invented word is not propagated");
    expect(contains(outcome.json, "NO_SOURCE"), "it becomes the reason that is true: NO_SOURCE");
}

void test_a_malformed_value_fragment_is_unavailable() {
    std::cout << "a value that is not JSON becomes UNAVAILABLE\n";
    const Profile profile = profile_with({"cpu"});
    auto input = base_input(profile);
    input.readings.push_back(present("cpu", "{not json"));
    const auto outcome = nodecore::corelite::build_status(input);
    expect(contains(outcome.json, "NO_SOURCE"), "the malformed fragment is refused");
    expect(!contains(outcome.json, "not json"), "and is not spliced into the document");
}

void test_no_secret_shape_reaches_the_document() {
    std::cout << "the document carries no secret-shaped key\n";
    const Profile profile = profile_with({"cpu", "connector"});
    auto input = base_input(profile);
    input.readings.push_back(present("cpu", "{\"used_percent\":1.0}"));
    const auto outcome = nodecore::corelite::build_status(input);
    for (const char* forbidden : {"argv", "environ", "token", "secret", "password",
                                  "credential", "private_key"}) {
        expect(!contains(outcome.json, forbidden),
               std::string("no ") + forbidden + " in the status");
    }
}

void test_observe_only_off_is_refused() {
    std::cout << "a profile that is not observe_only is refused\n";
    Profile profile = profile_with({"cpu"});
    profile.observe_only = false;
    const auto input = base_input(profile);
    const auto outcome = nodecore::corelite::build_status(input);
    expect(!outcome.ok, "refused");
    expect(outcome.refusal_code == "V1_HAS_NO_EFFECT", "with V1_HAS_NO_EFFECT");
}

void test_the_runtime_readback_is_real_on_this_host() {
    std::cout << "the runtime identity readback\n";
    const auto identity = nodecore::corelite::current_user();
#if defined(__linux__)
    // A real readback on Linux: this process HAS a uid and, on an ordinary host,
    // a name. Asserted as a fact about the platform, not about a particular
    // account — the test must pass for whoever runs it.
    expect(identity.uid >= 0, "a uid was read");
    expect(identity.known, "the account name resolved");
    expect(!identity.user.empty(), "the name is non-empty");
#else
    expect(!identity.known, "off Linux the readback reports UNKNOWN, never a guess");
#endif

    // A declaration file that does not exist must be UNKNOWN, not a default.
    const auto missing = nodecore::corelite::managed_node_identity(
        "/nonexistent-home-for-test", ".config/serveros/node-identity");
    expect(!missing.known, "a missing declaration is UNKNOWN");
    expect(missing.device_ref.empty(), "and carries no device reference");
}

}  // namespace

int main() {
    test_a_matched_principal_and_device_is_live();
    test_an_unauthorised_observation_is_never_emitted();
    test_a_missing_reading_is_unavailable_and_never_zero();
    test_windows_psi_is_unsupported_and_not_implemented_is_different();
    test_a_principal_mismatch_reports_nothing();
    test_an_unknown_principal_reports_nothing();
    test_a_device_mismatch_stops_the_live_claim();
    test_an_unknown_device_still_reports_this_accounts_readings();
    test_stale_and_unknown_are_carried_and_never_zero();
    test_a_reason_outside_the_closed_set_becomes_no_source();
    test_a_malformed_value_fragment_is_unavailable();
    test_no_secret_shape_reaches_the_document();
    test_observe_only_off_is_refused();
    test_the_runtime_readback_is_real_on_this_host();

    std::cout << "\n" << checks << " checks, " << failures << " failures\n";
    return failures == 0 ? 0 : 1;
}
