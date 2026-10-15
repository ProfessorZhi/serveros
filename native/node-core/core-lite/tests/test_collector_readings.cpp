// The Linux adapter and the whole read-only chain, against a REAL collector
// document.
//
// The fixture is `adapter/fixtures/snapshot.v1.example.json` — the snapshot the
// native collector produced and the repository already carries. Using the
// committed example rather than a hand-written stub is the point: every field
// the adapter reads was written by the producer it claims to read, so a rename
// on either side fails here instead of in production.
//
// Every test below asserts a REASON as well as a state. `UNAVAILABLE` alone
// would pass for NOT_IMPLEMENTED when the truth was NO_SOURCE, and those send an
// operator to two different places.
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "corelite/collector_readings.hpp"
#include "corelite/identity.hpp"
#include "corelite/profile.hpp"
#include "corelite/status.hpp"
#include "json_detail.hpp"

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
using nodecore::corelite::Reading;
using nodecore::corelite::ReadingState;
using nodecore::corelite::StatusInput;

std::string read_file(const std::string& path, bool& ok) {
    std::ifstream file(path);
    if (!file) {
        ok = false;
        return "";
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    ok = true;
    return buffer.str();
}

//: The fixture is looked for relative to the working directory, walking UP from
//: it. A test that quietly skipped when the path was wrong would be worse than
//: no test, so a miss is a FAILURE with the directories it tried.
std::string fixture_text() {
    const std::vector<std::string> candidates = {
        "adapter/fixtures/snapshot.v1.example.json",
        "../adapter/fixtures/snapshot.v1.example.json",
        "../../adapter/fixtures/snapshot.v1.example.json",
        "../../../adapter/fixtures/snapshot.v1.example.json",
        "native/node-core/adapter/fixtures/snapshot.v1.example.json",
    };
    for (const auto& candidate : candidates) {
        bool ok = false;
        const std::string text = read_file(candidate, ok);
        if (ok) {
            return text;
        }
    }
    expect(false, "the snapshot fixture was found");
    return "";
}

std::string shipped_profile_text() {
    for (const char* candidate : {"core-lite/profiles/tokyo-node.profile.json",
                                  "../core-lite/profiles/tokyo-node.profile.json",
                                  "../../core-lite/profiles/tokyo-node.profile.json",
                                  "native/node-core/core-lite/profiles/tokyo-node.profile.json"}) {
        bool ok = false;
        const std::string text = read_file(candidate, ok);
        if (ok) {
            return text;
        }
    }
    expect(false, "the shipped Tokyo profile was found");
    return "";
}

Profile profile_granting(const std::vector<std::string>& names, const std::string& platform = "linux") {
    Profile profile;
    profile.schema = nodecore::corelite::kProfileSchema;
    profile.node = "device:tokyo-shared-dev-01";
    profile.principal = "vince";
    profile.platform = platform;
    profile.observe_only = true;
    profile.generation = 3;
    for (const auto& name : names) {
        profile.observations.push_back({name, true});
    }
    return profile;
}

const Reading* find(const std::vector<Reading>& readings, const std::string& name) {
    for (const auto& reading : readings) {
        if (reading.name == name) {
            return &reading;
        }
    }
    return nullptr;
}

bool contains(const std::string& haystack, const std::string& needle) {
    return haystack.find(needle) != std::string::npos;
}

// ---------------------------------------------------------------------------

void test_the_real_snapshot_becomes_real_linux_readings() {
    std::cout << "the committed collector snapshot yields PRESENT cpu and memory\n";
    const Profile profile = profile_granting({"cpu", "memory"});
    std::string error;
    const auto readings = nodecore::corelite::readings_from_snapshot(profile, fixture_text(), error);
    expect(error.empty(), "no error: " + error);
    expect(readings.size() == 2, "one reading per granted observation");

    const Reading* cpu = find(readings, "cpu");
    expect(cpu != nullptr, "cpu is present at all");
    if (cpu != nullptr) {
        expect(cpu->state == ReadingState::Present, "cpu is PRESENT");
        expect(cpu->source == "nodecore/NodeResourceSnapshot v1", "cpu names its producer");
        expect(contains(cpu->value_json, "used_percent"),
               "cpu carries the measured percentage, not an empty object");
        expect(cpu->observed_at == "2026-01-01T00:00:03.300000+00:00",
               "cpu is stamped with the snapshot's own observation time");
    }

    const Reading* memory = find(readings, "memory");
    expect(memory != nullptr && memory->state == ReadingState::Present, "memory is PRESENT");
    if (memory != nullptr) {
        expect(contains(memory->value_json, "total_bytes"), "memory carries the measured total");
    }
}

void test_names_the_collector_does_not_measure_are_not_implemented() {
    std::cout << "a granted name with no collector block is NOT_IMPLEMENTED, not zero\n";
    const std::vector<std::string> names = {"cpu",     "swap",     "psi.cpu", "psi.memory",
                                           "psi.io",  "services", "connector", "gpu.readonly"};
    const Profile profile = profile_granting(names);
    std::string error;
    const auto readings = nodecore::corelite::readings_from_snapshot(profile, fixture_text(), error);
    expect(error.empty(), "no error: " + error);
    expect(readings.size() == names.size(), "every granted name got exactly one reading");

    for (const auto& name : names) {
        const Reading* reading = find(readings, name);
        expect(reading != nullptr, name + " was answered rather than omitted");
        if (reading == nullptr) {
            continue;
        }
        if (name == "cpu") {
            expect(reading->state == ReadingState::Present, "cpu is the one the collector measures");
            continue;
        }
        expect(reading->state == ReadingState::Unavailable, name + " is UNAVAILABLE");
        // The word is NOT_IMPLEMENTED, not UNSUPPORTED_PLATFORM: Linux CAN read
        // /proc/pressure and /proc/swaps. Nobody wrote the block yet.
        expect(reading->reason == "NOT_IMPLEMENTED",
               name + " says NOT_IMPLEMENTED (was \"" + reading->reason + "\")");
        expect(reading->value_json.empty(), name + " carries no value at all");
    }
}

void test_an_ungranted_observation_is_never_read() {
    std::cout << "a name the owner did not grant is not emitted even though the fixture has it\n";
    // The fixture DOES carry a memory block. The profile does not grant memory.
    const Profile profile = profile_granting({"cpu"});
    std::string error;
    const auto readings = nodecore::corelite::readings_from_snapshot(profile, fixture_text(), error);
    expect(error.empty(), "no error: " + error);
    expect(readings.size() == 1, "exactly one reading, for the one grant");
    expect(find(readings, "memory") == nullptr,
           "memory does not appear even as UNAVAILABLE — absence, not a blank");
    expect(find(readings, "cpu") != nullptr, "the granted one is there");
}

void test_the_linux_adapter_refuses_a_windows_profile() {
    std::cout << "the Linux adapter will not describe a Windows host\n";
    const Profile profile = profile_granting({"cpu"}, "windows");
    std::string error;
    const auto readings = nodecore::corelite::readings_from_snapshot(profile, fixture_text(), error);
    expect(readings.empty(), "no readings are produced");
    expect(contains(error, "Linux adapter"), "the refusal names the adapter (was \"" + error + "\")");
    expect(contains(error, "windows"), "and quotes the platform it refused");
}

void test_a_document_that_is_not_a_snapshot_is_refused() {
    std::cout << "a document that is not a node-resource-snapshot.v1 is refused\n";
    const Profile profile = profile_granting({"cpu"});
    for (const std::string& document : {std::string("{"), std::string("{}"),
                                        std::string("{\"schema\":\"serveros/something-else.v1\"}")}) {
        std::string error;
        const auto readings = nodecore::corelite::readings_from_snapshot(profile, document, error);
        expect(readings.empty(), "no readings from \"" + document + "\"");
        expect(!error.empty(), "and a reason was given for \"" + document + "\"");
    }
}

void test_a_block_the_collector_could_not_measure_is_no_source() {
    std::cout << "a block the collector marked UNAVAILABLE is NO_SOURCE, never zero\n";
    const Profile profile = profile_granting({"memory"});
    const std::string document =
        "{\"schema\":\"serveros/node-resource-snapshot.v1\","
        "\"observed_at\":\"2026-01-01T00:00:00.000000+00:00\","
        "\"memory\":{\"state\":\"UNAVAILABLE\",\"reason\":\"/proc/meminfo was not readable\"}}";
    std::string error;
    const auto readings = nodecore::corelite::readings_from_snapshot(profile, document, error);
    expect(error.empty(), "the document itself is well formed");
    const Reading* memory = find(readings, "memory");
    expect(memory != nullptr, "memory was answered");
    if (memory != nullptr) {
        expect(memory->state == ReadingState::Unavailable, "it is UNAVAILABLE");
        expect(memory->reason == "NO_SOURCE", "the adapter was asked and had nothing");
        expect(memory->value_json.empty(), "no value is invented");
    }
}

void test_storage_is_assembled_from_two_blocks() {
    std::cout << "storage.readonly carries both the filesystems and the device counters\n";
    const Profile profile = profile_granting({"storage.readonly"});
    std::string error;
    const auto readings = nodecore::corelite::readings_from_snapshot(profile, fixture_text(), error);
    const Reading* storage = find(readings, "storage.readonly");
    expect(storage != nullptr, "storage.readonly was answered");
    if (storage != nullptr) {
        expect(storage->state == ReadingState::Present, "it is PRESENT");
        expect(contains(storage->value_json, "filesystems"), "the mounted filesystems are there");
        expect(contains(storage->value_json, "disk_io"), "and so are the device counters");
    }
}

//: The whole chain, on the real document, through the real shipped profile.
void test_the_end_to_end_status_from_the_real_snapshot() {
    std::cout << "profile -> snapshot -> readings -> status, end to end\n";
    const auto parsed = nodecore::corelite::parse_profile(shipped_profile_text());
    expect(parsed.ok, "the shipped Tokyo profile parses");
    if (!parsed.ok) {
        return;
    }
    // The profile's own node and principal, so the identity gate matches on the
    // same values the document declares — this is the path a real Core takes.
    StatusInput input;
    input.profile = &parsed.profile;
    input.observed_at = "2026-10-15T23:07:00+00:00";
    input.device_ref_known = true;
    input.device_ref_observed = parsed.profile.node;
    input.principal_known = true;
    input.principal_observed = parsed.profile.principal;
    // Linux: `/proc/pressure` exists on this host, so a psi grant is a thing the
    // platform could honour; the collector simply has no block for it yet.
    input.platform_supports_psi = true;
    input.platform_has_collector = true;
    std::string error;
    input.readings = nodecore::corelite::readings_from_snapshot(parsed.profile, fixture_text(), error);
    expect(error.empty(), "the adapter read the snapshot: " + error);

    const auto outcome = nodecore::corelite::build_status(input);
    expect(outcome.ok, "the status was built");
    expect(outcome.live, "live: true — the principal and the device both matched");

    // Asserted STRUCTURALLY, by re-reading the document with the supervisor's
    // own reader, rather than by matching substrings: the writer is free to
    // change its spacing or its member order without that being a product
    // change, and a test that broke on a space would train a reader to ignore it.
    nodecore::supervisor::detail::Json document;
    std::string parse_error;
    nodecore::supervisor::detail::Reader reader(outcome.json);
    expect(reader.parse(document, parse_error), "the document re-reads as JSON: " + parse_error);

    const auto* mode = document.find("mode");
    expect(mode != nullptr && mode->is_string() && mode->text == "observe-only",
           "the mode is stated as observe-only");
    const auto* live = document.find("live");
    expect(live != nullptr && live->is_bool() && live->boolean, "the document says live: true");

    const auto* observations = document.find("observations");
    expect(observations != nullptr && observations->is_array(), "observations is an array");
    if (observations == nullptr || !observations->is_array()) {
        return;
    }
    expect(observations->items.size() == parsed.profile.observations.size(),
           "one row per observation in the profile");

    bool saw_cpu_present = false;
    bool saw_not_implemented = false;
    for (const auto& row : observations->items) {
        const auto* name = row.find("name");
        const auto* state = row.find("state");
        const auto* reason = row.find("reason");
        const auto* value = row.find("value");
        expect(name != nullptr && name->is_string(), "every row names its observation");
        expect(state != nullptr && state->is_string(), "every row states a state");
        if (name == nullptr || state == nullptr) {
            continue;
        }
        expect(state->text == "PRESENT" || state->text == "UNAVAILABLE",
               name->text + " has one of the two states");
        // THE invariant, asserted on the parsed document: a row that is not
        // PRESENT carries no value. Not a zero, not null, not an empty object.
        if (state->text != "PRESENT") {
            expect(value == nullptr, name->text + " is UNAVAILABLE and carries no value at all");
            expect(reason != nullptr, name->text + " is UNAVAILABLE and says why");
        }
        if (reason != nullptr && reason->is_string()) {
            expect(nodecore::corelite::known_reason(reason->text),
                   name->text + " uses a reason from the closed set (was \"" + reason->text + "\")");
            // Linux can read /proc/pressure and /proc/swaps, so blaming the
            // platform here would be a lie about this host.
            expect(reason->text != "UNSUPPORTED_PLATFORM",
                   name->text + " is not blamed on the platform — Linux can read these");
        }
        if (name->text == "cpu") {
            saw_cpu_present = state->text == "PRESENT" && value != nullptr;
            if (saw_cpu_present) {
                const auto* percent = value->find("used_percent");
                expect(percent != nullptr,
                       "cpu's value carries the measured percentage the collector computed");
            }
        }
        if (reason != nullptr && reason->is_string() && reason->text == "NOT_IMPLEMENTED") {
            saw_not_implemented = true;
        }
    }
    expect(saw_cpu_present, "cpu is PRESENT with a value, from the real collector snapshot");
    expect(saw_not_implemented, "the names the collector does not measure say NOT_IMPLEMENTED");
}

//: Optional: a REAL snapshot taken on this host, passed in by the caller. It
//: asserts the same chain over live data without committing host facts to the
//: repository.
void test_a_live_snapshot_if_one_was_given(int argc, char** argv) {
    if (argc < 2) {
        std::cout << "a live snapshot was not supplied (optional; pass a path as argv[1])\n";
        return;
    }
    std::cout << "the live snapshot at " << argv[1] << "\n";
    bool ok = false;
    const std::string text = read_file(argv[1], ok);
    expect(ok, "the live snapshot was readable");
    if (!ok) {
        return;
    }
    const Profile profile = profile_granting({"cpu", "memory", "swap", "psi.memory"});
    std::string error;
    const auto readings = nodecore::corelite::readings_from_snapshot(profile, text, error);
    expect(error.empty(), "the live snapshot is a node-resource-snapshot.v1: " + error);
    const Reading* cpu = find(readings, "cpu");
    expect(cpu != nullptr, "cpu was answered on the live host");
    // Either answer is honest: PRESENT from a real /proc/stat, or UNAVAILABLE
    // with a reason. What is NOT allowed is a fabricated value.
    if (cpu != nullptr && cpu->state == ReadingState::Unavailable) {
        expect(cpu->value_json.empty(), "an unavailable live reading carries no value");
    }
    const Reading* swap = find(readings, "swap");
    expect(swap != nullptr && swap->reason == "NOT_IMPLEMENTED",
           "swap is still NOT_IMPLEMENTED on the live host");
}

}  // namespace

int main(int argc, char** argv) {
    test_the_real_snapshot_becomes_real_linux_readings();
    test_names_the_collector_does_not_measure_are_not_implemented();
    test_an_ungranted_observation_is_never_read();
    test_the_linux_adapter_refuses_a_windows_profile();
    test_a_document_that_is_not_a_snapshot_is_refused();
    test_a_block_the_collector_could_not_measure_is_no_source();
    test_storage_is_assembled_from_two_blocks();
    test_the_end_to_end_status_from_the_real_snapshot();
    test_a_live_snapshot_if_one_was_given(argc, argv);

    std::cout << "\n" << checks << " checks, " << failures << " failures\n";
    return failures == 0 ? 0 : 1;
}
