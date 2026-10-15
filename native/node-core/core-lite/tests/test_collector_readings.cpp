// The Linux adapter, the projection that keeps an untrusted document out of the
// status, and the whole read-only chain — against a REAL collector document.
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
using Parsed = nodecore::supervisor::detail::Json;

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

//: The value's key set, read back from the `Json` itself rather than from text.
//: "The key is not in the document" and "the key is not in this value" are
//: different claims, and the projection test needs the second.
std::vector<std::string> value_keys(const Reading& reading) {
    std::vector<std::string> keys;
    if (!reading.has_value) {
        return keys;
    }
    // `Json` has no key enumerator, so the dump is read back with the strict
    // reader — the same reader the status builder uses, so the test and the
    // producer agree on what the value IS.
    Parsed parsed;
    std::string error;
    // The text is NAMED, not a temporary: `Reader` holds a reference to it, and
    // `Reader(dump_compact())` would hand it a reference to a string that dies
    // before the parse runs.
    const std::string text = reading.value.dump_compact();
    nodecore::supervisor::detail::Reader reader(text);
    if (!reader.parse(parsed, error)) {
        return keys;
    }
    for (const auto& member : parsed.members) {
        keys.push_back(member.first);
    }
    return keys;
}

bool has_key(const Reading& reading, const std::string& key) {
    for (const auto& candidate : value_keys(reading)) {
        if (candidate == key) {
            return true;
        }
    }
    return false;
}

//: Run the WHOLE chain — profile, snapshot text, status — and hand back the
//: document. Used by the forgery tests, which are about the final artifact: a
//: leak that the adapter dropped but the builder re-added would pass an
//: adapter-only test.
std::string status_document_from(const Profile& profile, const std::string& snapshot) {
    StatusInput input;
    input.profile = &profile;
    input.device_ref_known = true;
    input.device_ref_observed = profile.node;
    input.device_ref_basis = nodecore::corelite::kBasisAttested;
    input.principal_known = true;
    input.principal_observed = profile.principal;
    input.platform_supports_psi = true;
    input.platform_has_collector = true;
    std::string error;
    auto readings = nodecore::corelite::readings_from_snapshot(profile, snapshot, error);
    expect(error.empty(), "the snapshot was read: " + error);
    input.readings = std::move(readings.readings);
    input.source_observed_at = readings.observed_at;
    const auto outcome = nodecore::corelite::build_status(input);
    expect(outcome.ok, "the status was built");
    return outcome.json;
}

// ---------------------------------------------------------------------------
// The real document, projected
// ---------------------------------------------------------------------------

void test_the_real_snapshot_becomes_real_linux_readings() {
    std::cout << "the committed collector snapshot yields PRESENT cpu and memory\n";
    const Profile profile = profile_granting({"cpu", "memory"});
    std::string error;
    const auto answer = nodecore::corelite::readings_from_snapshot(profile, fixture_text(), error);
    expect(error.empty(), "no error: " + error);
    expect(answer.readings.size() == 2, "one reading per granted observation");
    expect(answer.observed_at == "2026-01-01T00:00:03.300000+00:00",
           "the snapshot's own sample time is carried, not a clock read here");

    const Reading* cpu = find(answer.readings, "cpu");
    expect(cpu != nullptr, "cpu is present at all");
    if (cpu != nullptr) {
        expect(cpu->state == ReadingState::Present, "cpu is PRESENT");
        expect(cpu->source == "nodecore/NodeResourceSnapshot v1", "cpu names its producer");
        expect(cpu->observed_at == answer.observed_at, "cpu carries the source's stamp");
        // THE PROJECTION: exactly one key, and it is the reviewed one.
        const auto keys = value_keys(*cpu);
        expect(keys.size() == 1, "cpu's value has exactly one key");
        expect(has_key(*cpu, "used_percent"), "and it is used_percent");
        expect(!has_key(*cpu, "counters"), "the counter block is NOT forwarded");
        expect(!has_key(*cpu, "busy_jiffies"), "nor the jiffy counts");
        expect(!has_key(*cpu, "state"), "nor the collector's own state field");
    }

    const Reading* memory = find(answer.readings, "memory");
    expect(memory != nullptr && memory->state == ReadingState::Present, "memory is PRESENT");
    if (memory != nullptr) {
        for (const char* wanted : {"total_bytes", "available_bytes", "free_bytes", "used_percent"}) {
            expect(has_key(*memory, wanted), std::string("memory carries ") + wanted);
        }
        // The collector's own provenance fields are NOT part of the projection.
        expect(!has_key(*memory, "source"), "memory does not forward the collector's source field");
        expect(!has_key(*memory, "scope"), "nor scope");
        expect(value_keys(*memory).size() == 4, "and nothing beyond the four named fields");
    }
}

void test_names_the_collector_does_not_measure_are_not_implemented() {
    std::cout << "a granted name with no collector block is NOT_IMPLEMENTED, not zero\n";
    const std::vector<std::string> names = {"cpu",     "swap",     "psi.cpu", "psi.memory",
                                           "psi.io",  "services", "connector", "gpu.readonly"};
    const Profile profile = profile_granting(names);
    std::string error;
    const auto answer = nodecore::corelite::readings_from_snapshot(profile, fixture_text(), error);
    expect(error.empty(), "no error: " + error);
    expect(answer.readings.size() == names.size(), "every granted name got exactly one reading");

    for (const auto& name : names) {
        const Reading* reading = find(answer.readings, name);
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
        expect(!reading->has_value, name + " carries no value at all");
    }
}

void test_an_ungranted_observation_is_never_read() {
    std::cout << "a name the owner did not grant is not emitted even though the fixture has it\n";
    // The fixture DOES carry a memory block. The profile does not grant memory.
    const Profile profile = profile_granting({"cpu"});
    std::string error;
    const auto answer = nodecore::corelite::readings_from_snapshot(profile, fixture_text(), error);
    expect(error.empty(), "no error: " + error);
    expect(answer.readings.size() == 1, "exactly one reading, for the one grant");
    expect(find(answer.readings, "memory") == nullptr,
           "memory does not appear even as UNAVAILABLE — absence, not a blank");
    expect(find(answer.readings, "cpu") != nullptr, "the granted one is there");
}

void test_the_linux_adapter_refuses_a_windows_profile() {
    std::cout << "the Linux adapter will not describe a Windows host\n";
    const Profile profile = profile_granting({"cpu"}, "windows");
    std::string error;
    const auto answer = nodecore::corelite::readings_from_snapshot(profile, fixture_text(), error);
    expect(answer.readings.empty(), "no readings are produced");
    expect(contains(error, "Linux adapter"), "the refusal names the adapter (was \"" + error + "\")");
    expect(contains(error, "windows"), "and quotes the platform it refused");
}

void test_a_document_that_is_not_a_snapshot_is_refused() {
    std::cout << "a document that is not a node-resource-snapshot.v1 is refused\n";
    const Profile profile = profile_granting({"cpu"});
    for (const std::string& document : {std::string("{"), std::string("{}"),
                                        std::string("{\"schema\":\"serveros/something-else.v1\"}")}) {
        std::string error;
        const auto answer = nodecore::corelite::readings_from_snapshot(profile, document, error);
        expect(answer.readings.empty(), "no readings from \"" + document + "\"");
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
    const auto answer = nodecore::corelite::readings_from_snapshot(profile, document, error);
    expect(error.empty(), "the document itself is well formed");
    const Reading* memory = find(answer.readings, "memory");
    expect(memory != nullptr, "memory was answered");
    if (memory != nullptr) {
        expect(memory->state == ReadingState::Unavailable, "it is UNAVAILABLE");
        expect(memory->reason == "NO_SOURCE", "the adapter was asked and had nothing usable");
        expect(!memory->has_value, "no value is invented");
    }
}

void test_storage_is_projected_to_roles_and_numbers_only() {
    std::cout << "storage.readonly carries roles and byte counts, and no paths\n";
    const Profile profile = profile_granting({"storage.readonly"});
    std::string error;
    const auto answer = nodecore::corelite::readings_from_snapshot(profile, fixture_text(), error);
    const Reading* storage = find(answer.readings, "storage.readonly");
    expect(storage != nullptr, "storage.readonly was answered");
    if (storage == nullptr || !storage->has_value) {
        expect(false, "and it has a value");
        return;
    }
    // The aggregate names two blocks and nothing else.
    expect(has_key(*storage, "filesystems"), "the filesystem roles are there");
    expect(has_key(*storage, "disk_io"), "and the device counters");
    expect(value_keys(*storage).size() == 2, "and nothing beyond those two blocks");

    const std::string compact = storage->value.dump_compact();
    // The fields the collector's own rows carry, NONE of which belong in a status.
    for (const char* leaked : {"path", "mount_point", "/dev/", "/data", "ext4", "xfs", "inodes",
                               "fstype", "source", "\"name\""}) {
        expect(!contains(compact, leaked),
               std::string("the storage value carries no ") + leaked);
    }
    // The reviewed fields ARE there.
    for (const char* wanted : {"role", "total_bytes", "available_bytes", "used_bytes",
                               "read_bytes", "write_bytes"}) {
        expect(contains(compact, wanted), std::string("and it does carry ") + wanted);
    }
}

// ---------------------------------------------------------------------------
// THE FORGERY TESTS — what the projection exists for
// ---------------------------------------------------------------------------

void test_a_forged_snapshot_cannot_smuggle_a_key_into_the_status() {
    std::cout << "a forged snapshot's extra keys do not reach the status document\n";
    // Built as TEXT, the way an attacker would: the fixture plus three keys a
    // status has no business carrying. Inserted into the real document rather
    // than hand-written, so the forged one is otherwise perfectly valid.
    std::string forged = fixture_text();
    const std::size_t cpu_at = forged.find("\"cpu\"");
    expect(cpu_at != std::string::npos, "the fixture has a cpu block to poison");
    const std::string canary =
        "\"token\":\"SECRET-CANARY-abc123\","
        "\"argv\":[\"/usr/bin/secret\",\"--password=hunter2\"],"
        "\"config_path\":\"/data/home/vince/.secrets/vault/key.pem\",";
    forged.insert(forged.find('{', cpu_at) + 1, canary);

    const Profile profile = profile_granting(
        {"cpu", "memory", "storage.readonly", "swap"});
    const std::string document = status_document_from(profile, forged);

    for (const char* leak : {"SECRET-CANARY-abc123", "hunter2", "vault/key.pem", "config_path",
                             "token", "argv", "/usr/bin/secret"}) {
        expect(!contains(document, leak), std::string("the status carries no ") + leak);
    }
    // And the machine-readable half: the poisoned cpu reading is still PRESENT,
    // because the projection dropped the extra keys rather than the reading.
    expect(contains(document, "used_percent"), "the real reading survives the poisoning");
}

void test_a_snapshot_that_forwards_paths_does_not_forward_them() {
    std::cout << "the collector's own path fields do not reach the status document\n";
    // Not a forgery — the collector's normal output, which really does carry
    // `path`, `mount_point` and `source` in its filesystem rows. Those are
    // correct for the snapshot and have no place in a status.
    const Profile profile = profile_granting({"storage.readonly", "memory"});
    const std::string document = status_document_from(profile, fixture_text());
    for (const char* path : {"/dev/vda2", "/dev/vdb", "mount_point", "\"fstype\"", "\"inodes\""}) {
        expect(!contains(document, path), std::string("the status carries no ") + path);
    }
    expect(contains(document, "ROOT_FS"), "the role name is what identifies the filesystem");
}

void test_an_out_of_range_or_malformed_value_is_unavailable_not_published() {
    std::cout << "a value outside the reviewed range is UNAVAILABLE, not clamped or published\n";
    const Profile profile = profile_granting({"cpu", "memory"});
    // 340% is not a high reading, it is a wrong one. A byte count written as a
    // real is not a byte count. Neither may be published, and neither may be
    // silently corrected into something that looks fine.
    const std::string document =
        "{\"schema\":\"serveros/node-resource-snapshot.v1\","
        "\"cpu\":{\"state\":\"PRESENT\",\"used_percent\":340.0},"
        "\"memory\":{\"state\":\"PRESENT\",\"total_bytes\":1.5,\"available_bytes\":1,"
        "\"free_bytes\":1,\"used_percent\":10.0}}";
    const std::string status = status_document_from(profile, document);
    expect(!contains(status, "340"), "the out-of-range percentage is not published");
    expect(!contains(status, "1.5"), "the malformed byte count is not published");
    expect(contains(status, "NO_SOURCE"), "both are UNAVAILABLE with a reason");
    // The memory reading must not be published HALF-filled either: three of four
    // numbers is a value a reader will do arithmetic on and get wrong.
    expect(!contains(status, "available_bytes"), "no partial memory value is published");
}

void test_an_unsafe_role_name_makes_the_aggregate_unavailable() {
    std::cout << "a role name that is not a role is refused, not published\n";
    // The role field is the one free-text field the storage projection carries.
    // It is allowed through only when it looks like a role; a path or an account
    // name is not one.
    const Profile profile = profile_granting({"storage.readonly"});
    const std::string document =
        "{\"schema\":\"serveros/node-resource-snapshot.v1\","
        "\"filesystems\":{\"roles\":[{\"role\":\"/home/vince/.ssh/id_rsa\",\"state\":\"PRESENT\","
        "\"total_bytes\":1,\"available_bytes\":1}]}}";
    std::string error;
    const auto answer = nodecore::corelite::readings_from_snapshot(profile, document, error);
    expect(error.empty(), "the document is well formed");
    const Reading* storage = find(answer.readings, "storage.readonly");
    expect(storage != nullptr, "storage.readonly was answered");
    if (storage != nullptr) {
        expect(storage->state == ReadingState::Unavailable,
               "an unusable row makes the whole aggregate UNAVAILABLE");
        expect(storage->reason == "NO_SOURCE", "with NO_SOURCE");
        expect(!storage->has_value, "and no value at all");
    }
}

// ---------------------------------------------------------------------------
// The whole chain
// ---------------------------------------------------------------------------

//: The whole chain, on the real document, through the real shipped profile.
void test_the_end_to_end_status_from_the_real_snapshot() {
    std::cout << "profile -> snapshot -> readings -> status, end to end\n";
    const auto parsed = nodecore::corelite::parse_profile(shipped_profile_text());
    expect(parsed.ok, "the shipped Tokyo profile parses");
    if (!parsed.ok) {
        return;
    }
    // A real Tokyo document today: the account matches, the device reference
    // comes from this host's own declaration, and nothing attests it — so the
    // readings are reported and `live` is false.
    StatusInput input;
    input.profile = &parsed.profile;
    input.device_ref_known = true;
    input.device_ref_observed = parsed.profile.node;
    input.device_ref_basis = nodecore::corelite::kBasisLocalDeclaration;
    input.principal_known = true;
    input.principal_observed = parsed.profile.principal;
    // Linux: `/proc/pressure` exists on this host, so a psi grant is a thing the
    // platform could honour; the collector simply has no block for it yet.
    input.platform_supports_psi = true;
    input.platform_has_collector = true;
    std::string error;
    auto readings = nodecore::corelite::readings_from_snapshot(parsed.profile, fixture_text(), error);
    expect(error.empty(), "the adapter read the snapshot: " + error);
    input.readings = std::move(readings.readings);
    input.source_observed_at = readings.observed_at;

    const auto outcome = nodecore::corelite::build_status(input);
    expect(outcome.ok, "the status was built");
    expect(!outcome.live, "live: false — a local declaration is not an attestation");

    // Asserted STRUCTURALLY, by re-reading the document with the supervisor's
    // own reader, rather than by matching substrings: the writer is free to
    // change its spacing or its member order without that being a product
    // change, and a test that broke on a space would train a reader to ignore it.
    Parsed document;
    std::string parse_error;
    nodecore::supervisor::detail::Reader reader(outcome.json);
    expect(reader.parse(document, parse_error), "the document re-reads as JSON: " + parse_error);

    const auto* mode = document.find("mode");
    expect(mode != nullptr && mode->is_string() && mode->text == "observe-only",
           "the mode is stated as observe-only");
    const auto* freshness = document.find("freshness");
    expect(freshness != nullptr && freshness->is_string() &&
               freshness->text == nodecore::corelite::kFreshnessNotAsserted,
           "freshness is stated as NOT_ASSERTED");
    const auto* device = document.find("device_identity");
    expect(device != nullptr, "the device identity is stated");
    if (device != nullptr) {
        const auto* basis = device->find("basis");
        expect(basis != nullptr && basis->is_string() &&
                   basis->text == nodecore::corelite::kBasisLocalDeclaration,
               "and its basis is LOCAL_DECLARATION, not an attestation");
    }

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
    const std::string document = status_document_from(profile, text);
    expect(contains(document, "used_percent"),
           "the live host's cpu/memory are reported with a measured percentage");
    expect(contains(document, "NOT_IMPLEMENTED"), "and swap/psi are still NOT_IMPLEMENTED");
    // The live document must carry no host path either.
    for (const char* path : {"/dev/", "mount_point", "/proc/meminfo"}) {
        expect(!contains(document, path), std::string("the live status carries no ") + path);
    }
}

}  // namespace

int main(int argc, char** argv) {
    test_the_real_snapshot_becomes_real_linux_readings();
    test_names_the_collector_does_not_measure_are_not_implemented();
    test_an_ungranted_observation_is_never_read();
    test_the_linux_adapter_refuses_a_windows_profile();
    test_a_document_that_is_not_a_snapshot_is_refused();
    test_a_block_the_collector_could_not_measure_is_no_source();
    test_storage_is_projected_to_roles_and_numbers_only();
    test_a_forged_snapshot_cannot_smuggle_a_key_into_the_status();
    test_a_snapshot_that_forwards_paths_does_not_forward_them();
    test_an_out_of_range_or_malformed_value_is_unavailable_not_published();
    test_an_unsafe_role_name_makes_the_aggregate_unavailable();
    test_the_end_to_end_status_from_the_real_snapshot();
    test_a_live_snapshot_if_one_was_given(argc, argv);

    std::cout << "\n" << checks << " checks, " << failures << " failures\n";
    return failures == 0 ? 0 : 1;
}
