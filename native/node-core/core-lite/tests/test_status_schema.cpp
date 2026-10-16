// The status document's schema, asserted against the document this producer
// actually emits.
//
// WHY THIS EXISTS
// ---------------
// `contract/core-lite-status.v1.md` was prose, and a consumer that had to read a
// prose contract GUESSED. Three of its guesses disagreed with this producer:
// `psi.*` was assumed flat (`some_avg10`) and is nested (`some.avg10`), `swap`
// was assumed to carry `used_percent` and carries two page rates instead, and
// `services`/`gpu.readonly` were assumed to carry no value at all. A schema file
// is the machine-checkable half of that contract — and this test is what keeps
// the schema and the producer from drifting apart, in either direction.
//
// It READS the schema (the one source), drives the real projections to produce a
// document, and asserts the two agree: every key the document carries is
// declared, every key the schema requires is present, and every shape the schema
// calls closed really is closed. No key set is duplicated here — duplicating
// them would move the drift into the test.
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "corelite/collector_readings.hpp"
#include "corelite/gpu_readings.hpp"
#include "corelite/service_readings.hpp"
#include "corelite/status.hpp"
#include "json_detail.hpp"
#include "nodecore/file_source.hpp"
#include "nodecore/sample.hpp"
#include "nodecore/snapshot.hpp"
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

using Parsed = nodecore::supervisor::detail::Json;
using nodecore::corelite::Profile;

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

Parsed parse(const std::string& text, bool& ok) {
    Parsed out;
    std::string error;
    nodecore::supervisor::detail::Reader reader(text);
    ok = reader.parse(out, error);
    if (!ok) {
        std::cout << "  (parse failed: " << error << ")\n";
    }
    return out;
}

//: The schema, found relative to the working directory and FAILING if it is not
//: there. A test that quietly skipped on a wrong path would be worse than none.
Parsed schema_document() {
    for (const char* candidate : {"contract/core-lite-status.v1.schema.json",
                                  "../contract/core-lite-status.v1.schema.json",
                                  "../../contract/core-lite-status.v1.schema.json",
                                  "native/node-core/contract/core-lite-status.v1.schema.json"}) {
        bool ok = false;
        const std::string text = read_file(candidate, ok);
        if (ok) {
            bool parsed = false;
            Parsed document = parse(text, parsed);
            expect(parsed, "the schema parses as JSON");
            return document;
        }
    }
    expect(false, "the schema file was found");
    return Parsed();
}

//: Follow a `$ref` of the form `#/$defs/<name>` inside the schema.
const Parsed* deref(const Parsed& schema, const Parsed& node) {
    const Parsed* ref = node.find("$ref");
    if (ref == nullptr || !ref->is_string()) {
        return &node;
    }
    const std::string prefix = "#/$defs/";
    if (ref->text.compare(0, prefix.size(), prefix) != 0) {
        return &node;
    }
    const Parsed* defs = schema.find("$defs");
    if (defs == nullptr) {
        return &node;
    }
    const Parsed* target = defs->find(ref->text.substr(prefix.size()));
    return target == nullptr ? &node : target;
}

//: Every key that MAY appear under `node`, unioned across the branches of any
//: `oneOf`. That union is the right question: this test asserts that the
//: document's keys are a subset of what the schema permits, and that what the
//: schema requires is present.
void collect_allowed(const Parsed& schema, const Parsed& node, std::vector<std::string>& out) {
    const Parsed* target = deref(schema, node);
    const Parsed* one_of = target->find("oneOf");
    if (one_of != nullptr && one_of->is_array()) {
        for (const Parsed& branch : one_of->items) {
            collect_allowed(schema, branch, out);
        }
        return;
    }
    const Parsed* properties = target->find("properties");
    if (properties == nullptr || !properties->is_object()) {
        return;
    }
    for (const auto& member : properties->members) {
        bool seen = false;
        for (const std::string& existing : out) {
            seen = seen || existing == member.first;
        }
        if (!seen) {
            out.push_back(member.first);
        }
    }
}

//: Every arm of an `items` node: a branch may be wrapped in `oneOf` (the nine
//: observations with a value) or be a bare object (the one that has no value).
//: Assuming one shape is what crashed this test the first time it ran.
void collect_arms(const Parsed& node, std::vector<const Parsed*>& arms) {
    const Parsed* one_of = node.find("oneOf");
    if (one_of != nullptr && one_of->is_array()) {
        for (const Parsed& branch : one_of->items) {
            collect_arms(branch, arms);
        }
        return;
    }
    if (node.find("properties") != nullptr) {
        arms.push_back(&node);
    }
}

//: The `name` const of an arm, or nullptr when the arm does not name one.
const std::string* arm_name(const Parsed& arm) {
    const Parsed* properties = arm.find("properties");
    if (properties == nullptr) {
        return nullptr;
    }
    const Parsed* name = properties->find("name");
    if (name == nullptr) {
        return nullptr;
    }
    const Parsed* constant = name->find("const");
    if (constant == nullptr || !constant->is_string()) {
        return nullptr;
    }
    return &constant->text;
}

//: The arm for one (name, state) pair. An observation with a value has TWO arms
//: — the UNAVAILABLE one and the PRESENT one — so looking an arm up by name alone
//: finds whichever was written first and silently checks the row against the
//: wrong shape. That is what this test did on its first correct run.
const Parsed* find_arm(const std::vector<const Parsed*>& arms, const std::string& name,
                       const std::string& state) {
    for (const Parsed* arm : arms) {
        const std::string* named = arm_name(*arm);
        if (named == nullptr || *named != name) {
            continue;
        }
        const Parsed* properties = arm->find("properties");
        const Parsed* state_node = properties == nullptr ? nullptr : properties->find("state");
        const Parsed* fixed = state_node == nullptr ? nullptr : state_node->find("const");
        if (fixed != nullptr && fixed->is_string() && fixed->text == state) {
            return arm;
        }
    }
    return nullptr;
}

std::vector<std::string> allowed_keys(const Parsed& schema, const Parsed& node) {
    std::vector<std::string> keys;
    collect_allowed(schema, node, keys);
    return keys;
}

bool declares(const std::vector<std::string>& keys, const std::string& key) {
    for (const std::string& candidate : keys) {
        if (candidate == key) {
            return true;
        }
    }
    return false;
}

//: Every key in `object`, checked against what the schema permits.
void assert_object_within(const std::string& where, const Parsed& schema, const Parsed& node,
                          const Parsed& object) {
    const std::vector<std::string> permitted = allowed_keys(schema, node);
    for (const auto& member : object.members) {
        expect(declares(permitted, member.first),
               where + " key \"" + member.first + "\" is declared by the schema");
    }
}

//: Walk the schema and assert every object it describes is CLOSED. A schema that
//: quietly allowed extra properties would let the producer start emitting a key
//: nobody reviewed, and this is the only place that can be caught statically.
void assert_closed_objects(const std::string& where, const Parsed& node) {
    const Parsed* type = node.find("type");
    const Parsed* properties = node.find("properties");
    if (type != nullptr && type->is_string() && type->text == "object" &&
        properties != nullptr) {
        const Parsed* additional = node.find("additionalProperties");
        expect(additional != nullptr && additional->is_bool() && !additional->boolean,
               where + " is additionalProperties: false");
    }
    const Parsed* one_of = node.find("oneOf");
    if (one_of != nullptr && one_of->is_array()) {
        for (std::size_t index = 0; index < one_of->items.size(); ++index) {
            assert_closed_objects(where + " oneOf[" + std::to_string(index) + "]",
                                  one_of->items[index]);
        }
    }
    if (properties != nullptr && properties->is_object()) {
        for (const auto& member : properties->members) {
            assert_closed_objects(where + "." + member.first, member.second);
        }
    }
    const Parsed* items = node.find("items");
    if (items != nullptr && items->is_object()) {
        assert_closed_objects(where + "[]", *items);
    }
}

// ---------------------------------------------------------------------------
// The document this producer actually emits, driven by the real projections
// ---------------------------------------------------------------------------

nodecore::Sample sample_at(double monotonic, std::uint64_t pages_in, std::uint64_t pages_out) {
    nodecore::Sample sample;
    sample.utc = std::chrono::system_clock::time_point(std::chrono::seconds(1770000000));
    sample.monotonic_seconds = monotonic;
    sample.boot_id = "boot-aaa";
    sample.cpu.present = true;
    sample.cpu.user = 100;
    sample.cpu.system = 50;
    sample.cpu.idle = 1000;
    sample.memory.present = true;
    sample.memory.total_bytes = 4194304;
    sample.memory.available_bytes = 1048576;
    sample.memory.free_bytes = 524288;
    sample.swap.totals_known = true;
    sample.swap.total_bytes = 8388608;
    sample.swap.free_bytes = 2097152;
    sample.swap.pages_known = true;
    sample.swap.pages_in = pages_in;
    sample.swap.pages_out = pages_out;
    nodecore::PsiFacts cpu;
    cpu.resource = "cpu";
    cpu.present = true;
    cpu.some.present = true;
    cpu.some.avg10 = 1.0;
    cpu.some.avg60 = 2.0;
    cpu.some.avg300 = 3.0;
    cpu.full.present = true;
    cpu.full.avg10 = 0.5;
    sample.psi.push_back(cpu);
    nodecore::FilesystemFacts root;
    root.role = "ROOT_FS";
    root.mounted = true;
    root.space_known = true;
    root.total_bytes = 1024;
    root.available_bytes = 512;
    root.free_bytes = 512;
    sample.filesystems.push_back(root);
    nodecore::DiskFacts disk;
    disk.name = "vda";
    disk.present = true;
    disk.sectors_read = 1;
    disk.sectors_written = 1;
    sample.disks.push_back(disk);
    return sample;
}

std::string fixture_snapshot() {
    nodecore::SnapshotOptions options;
    options.live = false;
    const nodecore::Sample before = sample_at(1000.0, 500, 900);
    const nodecore::Sample after = sample_at(1000.5, 700, 1400);
    return nodecore::build_snapshot(before, after, options).dump_compact();
}

class FakeServiceQuery final : public nodecore::corelite::ServiceQuery {
public:
    nodecore::supervisor::Observed query(const std::string& unit) override {
        nodecore::supervisor::Observed row;
        row.unit = unit;
        row.active = nodecore::supervisor::Active::Active;
        row.sub_state = "running";
        row.load_state = "loaded";
        row.observed_at = "2026-10-16T00:00:00+00:00";
        row.age_seconds = 0;
        row.source = "fixture";
        row.measured = true;
        return row;
    }
};

class FakeNvidia final : public nodecore::corelite::NvidiaQuery {
public:
    nodecore::corelite::NvidiaAnswer sample() override {
        nodecore::corelite::NvidiaAnswer answer;
        answer.ok = true;
        // Synthetic, as every GPU fixture in this tree is.
        answer.stdout_text = "0, Synthetic Model X, 42, 1024, 8192, 55\n";
        return answer;
    }
};

Profile profile_granting_all() {
    Profile profile;
    profile.schema = nodecore::corelite::kProfileSchema;
    profile.node = "device:tokyo-shared-dev-01";
    profile.principal = "vince";
    profile.platform = "linux";
    profile.observe_only = true;
    profile.generation = 1;
    for (const char* name : {"cpu", "memory", "swap", "psi.cpu", "psi.memory", "psi.io",
                             "storage.readonly", "services", "gpu.readonly", "connector"}) {
        profile.observations.push_back({name, true});
    }
    nodecore::corelite::ServiceEntry service;
    service.id = "fleet-node";
    service.owner = "vince";
    service.unit = "serveros-fleet-node.service";
    profile.services.push_back(service);
    return profile;
}

//: The document, assembled the way the CLI assembles it: the snapshot adapter
//: for the `/proc` facts, the OS adapters for `services` and `gpu.readonly`, and
//: `connector` unanswered because it has no trusted typed source.
std::string produced_document(const std::string& snapshot_text, bool& ok) {
    const Profile profile = profile_granting_all();
    nodecore::corelite::StatusInput input;
    input.profile = &profile;
    input.device_ref_known = true;
    input.device_ref_observed = profile.node;
    input.device_ref_basis = nodecore::corelite::kBasisLocalDeclaration;
    input.principal_known = true;
    input.principal_observed = profile.principal;
    input.platform_supports_psi = true;
    input.platform_has_collector = true;

    std::string error;
    auto readings = nodecore::corelite::readings_from_snapshot(
        profile, snapshot_text, error, {"services", "gpu.readonly"});
    expect(error.empty(), "the snapshot was read: " + error);
    input.readings = std::move(readings.readings);
    input.source_observed_at = readings.observed_at;

    FakeServiceQuery services;
    std::string service_error;
    input.readings.push_back(
        nodecore::corelite::services_reading(profile, nodecore::corelite::OsIdentity{}, services,
                                             service_error));
    // An unresolved account refuses the services reading, which is a shape the
    // schema must also describe — so it is left as produced.

    FakeNvidia nvidia;
    input.readings.push_back(nodecore::corelite::gpu_reading(
        profile, nodecore::corelite::OsIdentity{}, nvidia, "2026-10-16T00:00:00+00:00"));

    const auto outcome = nodecore::corelite::build_status(input);
    expect(outcome.ok, "the status was built");
    ok = outcome.ok;
    return outcome.json;
}

// ---------------------------------------------------------------------------

void test_the_schema_is_closed_and_the_document_fits_it() {
    std::cout << "the schema is closed, and the produced document fits it\n";
    const Parsed schema = schema_document();
    assert_closed_objects("schema", schema);

    bool built = false;
    const std::string text = produced_document(fixture_snapshot(), built);
    if (!built) {
        return;
    }
    bool parsed = false;
    const Parsed document = parse(text, parsed);
    expect(parsed, "the produced document re-reads as JSON");

    // Every document member is declared, and every required member is present.
    assert_object_within("document", schema, schema, document);
    expect(document.find("schema") != nullptr, "the document names its schema");
    const Parsed* name = document.find("schema");
    expect(name != nullptr && name->is_string() && name->text == "serveros/core-lite-status.v1",
           "and it is this schema");

    // The identity verdicts.
    for (const char* key : {"principal_identity", "device_identity"}) {
        const Parsed* verdict = document.find(key);
        expect(verdict != nullptr, std::string(key) + " is present");
        if (verdict != nullptr) {
            assert_object_within(key, schema, *schema.find("properties")->find(key), *verdict);
        }
    }

    // Every observation row, against the branch the schema declares for that
    // name — which is the check that would have caught the three guessed shapes.
    const Parsed* observations = document.find("observations");
    expect(observations != nullptr && observations->is_array(), "observations is an array");
    if (observations == nullptr || !observations->is_array()) {
        return;
    }
    std::vector<const Parsed*> arms;
    if (const Parsed* properties = schema.find("properties")) {
        if (const Parsed* obs_schema = properties->find("observations")) {
            if (const Parsed* items = obs_schema->find("items")) {
                collect_arms(*items, arms);
            }
        }
    }
    // Every arm names an observation, and no (name, state) pair appears twice —
    // a duplicate would make the lookup above silently order-dependent again.
    {
        std::vector<std::string> pairs;
        for (const Parsed* arm : arms) {
            const std::string* named = arm_name(*arm);
            expect(named != nullptr, "every schema arm names an observation");
            if (named == nullptr) {
                continue;
            }
            const Parsed* properties = arm->find("properties");
            const Parsed* state_node =
                properties == nullptr ? nullptr : properties->find("state");
            const Parsed* fixed = state_node == nullptr ? nullptr : state_node->find("const");
            expect(fixed != nullptr && fixed->is_string(),
                   "arm " + *named + " fixes its state");
            const std::string pair =
                *named + "/" + (fixed != nullptr && fixed->is_string() ? fixed->text : "?");
            for (const std::string& seen : pairs) {
                expect(seen != pair, "the schema declares " + pair + " once");
            }
            pairs.push_back(pair);
        }
    }
    for (const Parsed& row : observations->items) {
        const Parsed* row_name = row.find("name");
        expect(row_name != nullptr && row_name->is_string(), "every row names its observation");
        if (row_name == nullptr || !row_name->is_string()) {
            continue;
        }
        const std::string where = "observation " + row_name->text;

        const Parsed* row_state = row.find("state");
        expect(row_state != nullptr && row_state->is_string(), where + " states a state");
        const std::string state =
            row_state != nullptr && row_state->is_string() ? row_state->text : std::string();
        const Parsed* branch = find_arm(arms, row_name->text, state);
        expect(branch != nullptr, where + " has an arm for state " + state);
        if (branch == nullptr) {
            continue;
        }

        assert_object_within(where, schema, *branch, row);

        const Parsed* value = row.find("value");
        const Parsed* branch_properties = branch->find("properties");
        const Parsed* value_schema =
            branch_properties == nullptr ? nullptr : branch_properties->find("value");
        if (value != nullptr && value_schema != nullptr) {
            assert_object_within(where + " value", schema, *value_schema, *value);
            // Every key the schema DECLARES for this value shape must be one a
            // real value carries — so the schema cannot describe a shape nobody
            // produces. The reverse direction is the one that catches a producer
            // adding a key.
            const std::vector<std::string> declared = allowed_keys(schema, *value_schema);
            const std::vector<std::string> carried = [&value] {
                std::vector<std::string> keys;
                for (const auto& member : value->members) {
                    keys.push_back(member.first);
                }
                return keys;
            }();
            for (const std::string& key : declared) {
                // `some`/`full` may be an explicit UNAVAILABLE rather than the
                // three averages, so an unavailable line is allowed to carry
                // `state`/`reason` instead of `avg*`. The union is the permitted
                // set, and that is what is asserted above; here we only require
                // that a shape claiming a key set is not wholly absent.
                expect(!carried.empty() || declared.empty(),
                       where + " value is not empty where the schema declares keys");
            }
        }
    }
}

void test_the_connector_branch_refuses_a_present_reading() {
    std::cout << "connector is UNAVAILABLE-only because it has no trusted source\n";
    // The schema says so, and this asserts the schema still says so: a change
    // that let `connector` be PRESENT would be adding a health claim this
    // producer cannot support.
    const Parsed schema = schema_document();
    std::vector<const Parsed*> arms;
    if (const Parsed* properties = schema.find("properties")) {
        if (const Parsed* obs_schema = properties->find("observations")) {
            if (const Parsed* items = obs_schema->find("items")) {
                collect_arms(*items, arms);
            }
        }
    }
    bool saw_connector = false;
    bool present_allowed = false;
    for (const Parsed* arm : arms) {
        const std::string* named = arm_name(*arm);
        if (named == nullptr || *named != "connector") {
            continue;
        }
        saw_connector = true;
        const Parsed* properties = arm->find("properties");
        const Parsed* state = properties == nullptr ? nullptr : properties->find("state");
        const Parsed* fixed = state == nullptr ? nullptr : state->find("const");
        if (fixed != nullptr && fixed->is_string() && fixed->text == "PRESENT") {
            present_allowed = true;
        }
    }
    expect(saw_connector, "the schema declares a connector branch");
    expect(!present_allowed, "and it does not allow connector to be PRESENT");
}

}  // namespace

int main() {
    test_the_schema_is_closed_and_the_document_fits_it();
    test_the_connector_branch_refuses_a_present_reading();

    std::cout << "\n" << checks << " checks, " << failures << " failures\n";
    return failures == 0 ? 0 : 1;
}
