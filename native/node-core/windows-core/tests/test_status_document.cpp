// The Windows Core's status envelope, asserted against the schema this tree
// ships and against the Linux contract's own vocabulary.
//
// WHAT THIS TEST IS FOR
// ---------------------
// The SCM parser already answers "what does the service manager say". This
// document is the next question: what may a Windows Core REPORT about that
// answer, and what must it refuse to claim? Three claims are checked here:
//
//   * the Core's own status route is UNSUPPORTED, and the reason names what is
//     missing. A route that has never been compiled is not a route;
//   * the identity verdicts are UNKNOWN / NONE — a Windows Core on this host has
//     no OS readback and no attestation, so it must not say MATCHED;
//   * an unmeasured service is UNMEASURED, never STOPPED, and carries no state
//     number that a reader could mistake for a measurement.
//
// NOTHING HERE IS A REAL WINDOWS READING. Every observation is synthetic, no
// process is started, and no Windows machine is contacted.
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "check.hpp"
#include "json_detail.hpp"
#include "supervisor/admission.hpp"
#include "windows-core/scm_status.hpp"
#include "windows-core/status_document.hpp"
#include "windows-core/win32_scm.hpp"

using namespace nodecore::windows_core;
using nodecore::supervisor::detail::Json;
using nodecore::supervisor::detail::Reader;

namespace {

void expect(bool condition, const std::string& what) {
    check::state().checks++;
    if (!condition) {
        check::fail(__FILE__, __LINE__, what);
    }
}

std::string read_file(const char* path, bool& ok) {
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

Json parse(const std::string& text, bool& ok) {
    Json document;
    std::string error;
    Reader reader(text);
    ok = reader.parse(document, error);
    if (!ok) {
        std::cout << "  (parse failed: " << error << ")\n";
    }
    return document;
}

Json schema_document(const char* path) {
    bool ok = false;
    const std::string text = read_file(path, ok);
    expect(ok, std::string("the schema file was found: ") + path);
    if (!ok) {
        return Json();
    }
    bool parsed = false;
    Json document = parse(text, parsed);
    expect(parsed, "the schema parses as JSON");
    return document;
}

std::string text_of(const Json& object, const char* key) {
    const Json* found = object.find(key);
    return found != nullptr && found->is_string() ? found->text : std::string();
}

int integer_of(const Json& object, const char* key) {
    const Json* found = object.find(key);
    return found != nullptr && found->is_int() ? static_cast<int>(found->integer) : -999;
}

bool has_key(const Json& object, const char* key) { return object.find(key) != nullptr; }

//: Follow a `$ref` of the form `#/$defs/<name>`. The identity verdict and the
//: service row are defined once in `$defs` and referenced from the document and
//: the array, exactly as the core-lite schema does.
const Json* deref(const Json& schema, const Json* node) {
    if (node == nullptr) {
        return nullptr;
    }
    const Json* ref = node->find("$ref");
    if (ref == nullptr || !ref->is_string()) {
        return node;
    }
    const std::string prefix = "#/$defs/";
    if (ref->text.compare(0, prefix.size(), prefix) != 0) {
        return node;
    }
    const Json* defs = schema.find("$defs");
    const Json* target = defs == nullptr ? nullptr : defs->find(ref->text.substr(prefix.size()));
    return target == nullptr ? node : target;
}

//: Every key a schema node permits. The repository's schemas are closed and have
//: no branch-per-row structure here, so a union across properties is enough.
void collect_allowed(const Json& schema, const Json& node, std::vector<std::string>& out) {
    const Json* target = deref(schema, &node);
    const Json* properties = target == nullptr ? nullptr : target->find("properties");
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

std::vector<std::string> allowed_keys(const Json& schema, const Json& node) {
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

void assert_object_within(const std::string& where, const Json& schema, const Json& schema_node,
                          const Json& object) {
    const std::vector<std::string> permitted = allowed_keys(schema, schema_node);
    for (const auto& member : object.members) {
        expect(declares(permitted, member.first),
               where + " key \"" + member.first + "\" is declared by the schema");
    }
}

void assert_required(const std::string& where, const Json& schema_node,
                     const std::vector<const char*>& keys) {
    const Json* required = schema_node.find("required");
    expect(required != nullptr && required->is_array(), where + " declares required keys");
    if (required == nullptr || !required->is_array()) {
        return;
    }
    for (const char* key : keys) {
        bool found = false;
        for (const Json& item : required->items) {
            found = found || (item.is_string() && item.text == key);
        }
        expect(found, where + " requires \"" + key + "\"");
    }
}

//: Every object the schema describes must be closed. A schema that quietly
//: allowed extra properties would let the producer start emitting a key nobody
//: reviewed, and this is the only place that can be caught statically.
void assert_closed_objects(const std::string& where, const Json& node) {
    const Json* type = node.find("type");
    const Json* properties = node.find("properties");
    if (type != nullptr && type->is_string() && type->text == "object" &&
        properties != nullptr) {
        const Json* additional = node.find("additionalProperties");
        expect(additional != nullptr && additional->is_bool() && !additional->boolean,
               where + " is additionalProperties: false");
    }
    if (properties != nullptr && properties->is_object()) {
        for (const auto& member : properties->members) {
            assert_closed_objects(where + "." + member.first, member.second);
        }
    }
    const Json* defs = node.find("$defs");
    if (defs != nullptr && defs->is_object()) {
        for (const auto& member : defs->members) {
            assert_closed_objects(where + ".$defs." + member.first, member.second);
        }
    }
    const Json* items = node.find("items");
    if (items != nullptr && items->is_object()) {
        assert_closed_objects(where + "[]", *items);
    }
}

std::vector<std::string> enum_values(const Json& schema, const char* definition,
                                     const char* property) {
    std::vector<std::string> values;
    const Json* defs = schema.find("$defs");
    const Json* def = defs == nullptr ? nullptr : defs->find(definition);
    const Json* properties = def == nullptr ? nullptr : def->find("properties");
    const Json* node = deref(schema, properties == nullptr ? nullptr : properties->find(property));
    const Json* enumeration = node == nullptr ? nullptr : node->find("enum");
    if (enumeration == nullptr || !enumeration->is_array()) {
        return values;
    }
    for (const Json& item : enumeration->items) {
        if (item.is_string()) {
            values.push_back(item.text);
        }
    }
    return values;
}

WindowsServiceObservation running() {
    WindowsServiceObservation observed;
    observed.service_name = "synthetic-running";
    observed.state = kStateRunning;
    observed.start_type = kStartAuto;
    observed.raw_state = 4;
    observed.measured = true;
    return observed;
}

WindowsServiceObservation manual_stop() {
    WindowsServiceObservation observed;
    observed.service_name = "synthetic-manual";
    observed.state = kStateManualStop;
    observed.start_type = kStartDisabled;
    observed.raw_state = 1;
    observed.measured = true;
    return observed;
}

WindowsServiceObservation needs_login() {
    WindowsServiceObservation observed;
    observed.service_name = "synthetic-login";
    observed.state = kStateNeedsLogin;
    observed.measured = true;
    observed.reason = kReasonNeedsLogin;
    return observed;
}

WindowsServiceObservation not_installed() {
    WindowsServiceObservation observed;
    observed.service_name = "synthetic-absent";
    observed.state = kStateNotInstalled;
    observed.measured = true;
    observed.reason = kReasonNotInstalled;
    return observed;
}

WindowsServiceObservation unmeasured() {
    WindowsServiceObservation observed;
    observed.service_name = "synthetic-unknown";
    observed.state = kStateUnknown;
    observed.measured = false;
    observed.reason = kReasonNoStateLine;
    return observed;
}

WindowsStatusInput input_with_all_cases() {
    WindowsStatusInput input;
    input.node = "device:synthetic-windows-01";
    input.principal = "synthetic-principal";
    input.generation = 7;
    input.services = {running(), manual_stop(), needs_login(), not_installed(), unmeasured()};
    return input;
}

// ---------------------------------------------------------------------------

void test_the_document_fits_a_closed_schema() {
    std::cout << "the status document fits a closed schema\n";
    const Json schema = schema_document(WINDOWS_STATUS_SCHEMA_PATH);
    if (!schema.is_object()) {
        return;
    }
    assert_closed_objects("schema", schema);
    assert_required("document", schema,
                    {"schema", "platform", "mode", "node", "principal", "generation", "freshness",
                     "principal_identity", "device_identity", "ipc", "services"});

    bool parsed = false;
    const Json document = parse(build_windows_status_json(input_with_all_cases()), parsed);
    CHECK(parsed);
    assert_object_within("document", schema, schema, document);

    CHECK_EQ(text_of(document, "schema"), std::string(kWindowsStatusSchema));
    CHECK_EQ(text_of(document, "platform"), std::string(kPlatformWindows));
    CHECK_EQ(text_of(document, "mode"), std::string(kModeObserveOnly));
    CHECK_EQ(text_of(document, "freshness"), std::string(kFreshnessNotAsserted));
    CHECK_EQ(integer_of(document, "generation"), 7);
    CHECK_EQ(text_of(document, "node"), std::string("device:synthetic-windows-01"));
    CHECK_EQ(text_of(document, "principal"), std::string("synthetic-principal"));

    // The IPC route is UNSUPPORTED, and the reason names what is missing.
    const Json* ipc = document.find("ipc");
    CHECK(ipc != nullptr && ipc->is_object());
    if (ipc != nullptr && ipc->is_object()) {
        CHECK_EQ(text_of(*ipc, "status"), windows_status_ipc_status());
        CHECK_EQ(text_of(*ipc, "reason"), windows_status_ipc_reason());
        // Exactly two keys: a later working route adds a field by editing the
        // schema and the test, not by quietly widening this object.
        CHECK_EQ(ipc->members.size(), std::size_t{2});
        assert_object_within("ipc", schema, *schema.find("properties")->find("ipc"), *ipc);
    }

    // The identity verdicts are UNKNOWN / NONE, because this build has no OS
    // readback and no attestation. A MATCHED here would be a claim nobody made.
    for (const char* key : {"principal_identity", "device_identity"}) {
        const Json* verdict = document.find(key);
        CHECK(verdict != nullptr && verdict->is_object());
        if (verdict == nullptr || !verdict->is_object()) {
            continue;
        }
        CHECK_EQ(text_of(*verdict, "state"), std::string(kIdentityUnknown));
        CHECK_EQ(text_of(*verdict, "basis"), std::string(kIdentityBasisNone));
        assert_object_within(key, schema, *schema.find("properties")->find(key), *verdict);
    }
    const Json* principal = document.find("principal_identity");
    CHECK(principal != nullptr &&
          text_of(*principal, "reason") == kReasonNoWindowsIdentity);
    const Json* device = document.find("device_identity");
    CHECK(device != nullptr && text_of(*device, "reason") == kReasonNoWindowsAttestation);
}

void test_every_service_row_uses_the_shared_vocabulary() {
    std::cout << "every service row uses the shared readiness/admission words\n";
    const Json schema = schema_document(WINDOWS_STATUS_SCHEMA_PATH);
    if (!schema.is_object()) {
        return;
    }
    bool parsed = false;
    const Json document = parse(build_windows_status_json(input_with_all_cases()), parsed);
    CHECK(parsed);
    const Json* services = document.find("services");
    CHECK(services != nullptr && services->is_array());
    if (services == nullptr || !services->is_array()) {
        return;
    }
    CHECK_EQ(services->items.size(), std::size_t{5});
    if (services->items.size() != 5) {
        return;
    }

    const Json& row0 = services->items[0];
    CHECK_EQ(text_of(row0, "state"), std::string(kStateRunning));
    CHECK_EQ(text_of(row0, "readiness"), std::string(kReadinessReady));
    CHECK_EQ(text_of(row0, "admission"), std::string(nodecore::supervisor::kAdmitOk));
    CHECK(row0.find("measured") != nullptr && row0.find("measured")->is_bool() &&
          row0.find("measured")->boolean);
    CHECK_EQ(integer_of(row0, "raw_state"), 4);

    const Json& row1 = services->items[1];
    CHECK_EQ(text_of(row1, "state"), std::string(kStateManualStop));
    CHECK_EQ(text_of(row1, "readiness"), std::string(kReadinessNotReady));
    CHECK_EQ(text_of(row1, "admission"), std::string(nodecore::supervisor::kAdmitMasked));

    const Json& row2 = services->items[2];
    CHECK_EQ(text_of(row2, "state"), std::string(kStateNeedsLogin));
    CHECK_EQ(text_of(row2, "admission"), std::string(nodecore::supervisor::kAdmitNeedsLogin));

    const Json& row3 = services->items[3];
    CHECK_EQ(text_of(row3, "state"), std::string(kStateNotInstalled));
    CHECK_EQ(text_of(row3, "admission"), std::string(nodecore::supervisor::kAdmitNotInstalled));

    // UNMEASURED is never STOPPED, and it carries no state number: a reader
    // must not be able to mistake "we could not look" for a measurement.
    const Json& row4 = services->items[4];
    CHECK_EQ(text_of(row4, "state"), std::string(kStateUnknown));
    CHECK_EQ(text_of(row4, "readiness"), std::string(kReadinessUnmeasured));
    CHECK_EQ(text_of(row4, "admission"),
             std::string(nodecore::supervisor::kAdmitInstallStateUnknown));
    CHECK(row4.find("measured") != nullptr && row4.find("measured")->is_bool() &&
          !row4.find("measured")->boolean);
    CHECK(!has_key(row4, "raw_state"));
    CHECK(row4.find("state") != nullptr && row4.find("state")->text != kStateStopped);

    // Every row is within the row schema, and every row names its service.
    const Json* properties = schema.find("properties");
    const Json* services_schema = properties == nullptr ? nullptr : properties->find("services");
    const Json* row_schema = services_schema == nullptr ? nullptr : services_schema->find("items");
    CHECK(row_schema != nullptr);
    if (row_schema != nullptr) {
        for (const Json& row : services->items) {
            assert_object_within("service", schema, *row_schema, row);
            CHECK(!text_of(row, "name").empty());
        }
    }
}

void test_no_posix_mechanism_is_claimed() {
    std::cout << "the document claims no POSIX secure-route mechanism\n";
    const std::string text = build_windows_status_json(input_with_all_cases());
    // A Windows pipe has no symlink and no mode bits. Copying the POSIX words
    // into this document would be the translated rule the contract refuses.
    for (const char* forbidden : {"O_NOFOLLOW", "SO_PEERCRED", "symlink", "unix_socket",
                                  "peer_uid", "mode_bits"}) {
        CHECK(text.find(forbidden) == std::string::npos);
    }
}

void test_identity_vocabulary_matches_core_lite() {
    std::cout << "the identity verdict reuses the core-lite vocabulary\n";
    const Json windows_schema = schema_document(WINDOWS_STATUS_SCHEMA_PATH);
    const Json core_lite_schema = schema_document(CORE_LITE_SCHEMA_PATH);
    if (!windows_schema.is_object() || !core_lite_schema.is_object()) {
        return;
    }
    const std::vector<std::string> states =
        enum_values(windows_schema, "identity_verdict", "state");
    const std::vector<std::string> bases =
        enum_values(windows_schema, "identity_verdict", "basis");
    CHECK_EQ(states.size(), std::size_t{3});
    CHECK_EQ(bases.size(), std::size_t{4});
    CHECK(states == enum_values(core_lite_schema, "identity_verdict", "state"));
    CHECK(bases == enum_values(core_lite_schema, "identity_verdict", "basis"));

    const Json* defs = windows_schema.find("$defs");
    const Json* def = defs == nullptr ? nullptr : defs->find("identity_verdict");
    CHECK(def != nullptr);
    if (def != nullptr) {
        assert_required("identity_verdict", *def, {"state", "basis", "claimed"});
    }
}

}  // namespace

int main() {
    test_the_document_fits_a_closed_schema();
    test_every_service_row_uses_the_shared_vocabulary();
    test_no_posix_mechanism_is_claimed();
    test_identity_vocabulary_matches_core_lite();

    {
        const auto schema=schema_document(WINDOWS_STATUS_SCHEMA_PATH);
        auto resident=input_with_all_cases();resident.recover_local=true;resident.observed_at="2026-10-21T00:00:00Z";
        resident.runtime_reason="LOCAL_CONFIG_MISSING_OR_UNTRUSTED";
        resident.runtime.push_back({"cloudcli","manual","UNMEASURED","DESIRED_MANUAL",false});
        WindowsTaskObservation task;task.task_path="\\Fixture\\Task";task.state=kTaskReady;task.measured=true;task.last_result=0;resident.tasks.push_back(task);
        bool ok=false;auto doc=parse(build_windows_status_json(resident),ok);CHECK(ok);assert_object_within("resident",schema,schema,doc);
        CHECK_EQ(text_of(doc,"mode"),std::string("recover-local"));CHECK(doc.find("tasks")!=nullptr);CHECK(doc.find("runtime")!=nullptr);
    }
    return check::report("test_windows_status_document");
}
