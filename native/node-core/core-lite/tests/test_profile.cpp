// Targeted tests for the Core-Lite profile reader.
//
// They are the V1 product rules, one test each, and they assert BOTH that a
// refusal happened AND which code it carries — a test that only checked
// `ok == false` would pass if the reader refused the document for the wrong
// reason.
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>

#include "corelite/profile.hpp"

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

std::string base_profile() {
    return R"({
      "schema": "serveros/core-lite-profile.v1",
      "node": "device:tokyo-shared-dev-01",
      "principal": "vince",
      "platform": "linux",
      "observe_only": true,
      "generation": 3,
      "observations": [
        {"name": "cpu", "authorized": true},
        {"name": "memory", "authorized": true},
        {"name": "swap", "authorized": true},
        {"name": "psi.memory", "authorized": true},
        {"name": "services", "authorized": true}
      ],
      "services": [
        {"id": "fleet-node", "owner": "vince", "unit": "serveros-fleet-node.service"}
      ]
    })";
}

bool has_code(const nodecore::corelite::ProfileOutcome& outcome, const std::string& code) {
    for (const auto& refusal : outcome.refusals) {
        if (refusal.code == code) {
            return true;
        }
    }
    return false;
}

void test_a_valid_profile_is_accepted() {
    std::cout << "a valid profile is accepted\n";
    const auto outcome = nodecore::corelite::parse_profile(base_profile());
    expect(outcome.ok, "the base profile parses");
    expect(outcome.refusals.empty(), "with no refusals");
    expect(outcome.profile.principal == "vince", "principal carried");
    expect(outcome.profile.observations.size() == 5, "five observations carried");
    expect(outcome.profile.services.size() == 1, "one service carried");
}

void test_an_owner_that_is_not_the_principal_is_refused() {
    std::cout << "a service owned by another account is refused\n";
    auto text = base_profile();
    const auto at = text.find("\"owner\": \"vince\"");
    text.replace(at, std::string("\"owner\": \"vince\"").size(),
                 "\"owner\": \"someoneelse\"");
    const auto outcome = nodecore::corelite::parse_profile(text);
    expect(!outcome.ok, "refused");
    expect(has_code(outcome, "OWNER_MISMATCH"), "with OWNER_MISMATCH");
}

void test_an_unknown_observation_is_refused() {
    std::cout << "an observation outside the vocabulary is refused\n";
    auto text = base_profile();
    const auto at = text.find("\"name\": \"cpu\"");
    text.replace(at, std::string("\"name\": \"cpu\"").size(),
                 "\"name\": \"keylogger\"");
    const auto outcome = nodecore::corelite::parse_profile(text);
    expect(!outcome.ok, "refused");
    expect(has_code(outcome, "UNKNOWN_OBSERVATION"), "with UNKNOWN_OBSERVATION");
}

void test_an_observation_without_an_explicit_grant_is_refused() {
    std::cout << "an observation without `authorized` is refused\n";
    auto text = base_profile();
    const auto at = text.find("{\"name\": \"cpu\", \"authorized\": true}");
    text.replace(at, std::string("{\"name\": \"cpu\", \"authorized\": true}").size(),
                 "{\"name\": \"cpu\"}");
    const auto outcome = nodecore::corelite::parse_profile(text);
    expect(!outcome.ok, "refused");
    expect(has_code(outcome, "MISSING"),
           "with MISSING — absent must not read as granted");
}

void test_a_restart_field_is_refused_as_a_v1_effect() {
    std::cout << "a restart/self-heal field is refused\n";
    for (const char* field : {"restart_allowed", "self_heal", "actions"}) {
        auto text = base_profile();
        const auto at = text.find("\"services\": [");
        text.replace(at, std::string("\"services\": [").size(),
                     std::string("\"") + field + "\": true, \"services\": [");
        const auto outcome = nodecore::corelite::parse_profile(text);
        expect(!outcome.ok, std::string("refused for ") + field);
        expect(has_code(outcome, "V1_HAS_NO_EFFECT"),
               std::string("with V1_HAS_NO_EFFECT for ") + field);
    }
}

void test_observe_only_false_is_refused() {
    std::cout << "observe_only false is refused\n";
    auto text = base_profile();
    const auto at = text.find("\"observe_only\": true");
    text.replace(at, std::string("\"observe_only\": true").size(),
                 "\"observe_only\": false");
    const auto outcome = nodecore::corelite::parse_profile(text);
    expect(!outcome.ok, "refused");
    expect(has_code(outcome, "V1_HAS_NO_EFFECT"), "with V1_HAS_NO_EFFECT");
}

void test_a_hardcoded_uid_is_refused() {
    std::cout << "a hard-coded uid is refused\n";
    for (const char* field : {"uid", "user_id", "uid_min"}) {
        auto text = base_profile();
        const auto at = text.find("\"platform\":");
        text.replace(at, 0, std::string("\"") + field + "\": 1002, ");
        const auto outcome = nodecore::corelite::parse_profile(text);
        expect(!outcome.ok, std::string("refused for ") + field);
        expect(has_code(outcome, "NO_HARDCODED_UID"),
               std::string("with NO_HARDCODED_UID for ") + field);
    }
}

void test_an_unlisted_unit_shape_is_refused() {
    std::cout << "a unit id the allowlist refuses is refused\n";
    for (const char* unit : {"../../etc/passwd.service", "a b.service", "no-suffix",
                             "/bin/sh", "x.service; rm -rf /"}) {
        auto text = base_profile();
        const auto at = text.find("serveros-fleet-node.service");
        text.replace(at, std::string("serveros-fleet-node.service").size(), unit);
        const auto outcome = nodecore::corelite::parse_profile(text);
        expect(!outcome.ok, std::string("refused for ") + unit);
        expect(has_code(outcome, "UNIT_ID"), std::string("with UNIT_ID for ") + unit);
    }
}

void test_a_stale_or_missing_generation_is_refused() {
    std::cout << "generation is required and must be non-negative\n";
    auto missing = base_profile();
    const auto at = missing.find("\"generation\": 3,");
    missing.erase(at, std::string("\"generation\": 3,").size());
    expect(!nodecore::corelite::parse_profile(missing).ok, "missing generation refused");

    auto negative = base_profile();
    const auto at2 = negative.find("\"generation\": 3");
    negative.replace(at2, std::string("\"generation\": 3").size(),
                     "\"generation\": -1");
    const auto outcome = nodecore::corelite::parse_profile(negative);
    expect(!outcome.ok, "negative generation refused");
    expect(has_code(outcome, "TYPE"), "with TYPE");
}

void test_a_duplicate_service_id_is_refused() {
    std::cout << "a duplicate service id is refused\n";
    auto text = base_profile();
    const auto at = text.find("      ]\n    }");
    text.replace(at, std::string("      ]\n    }").size(),
                 "        ,{\"id\": \"fleet-node\", \"owner\": \"vince\", "
                 "\"unit\": \"other.service\"}\n      ]\n    }");
    const auto outcome = nodecore::corelite::parse_profile(text);
    expect(!outcome.ok, "refused");
    expect(has_code(outcome, "DUPLICATE_ID"), "with DUPLICATE_ID");
}

void test_a_wrong_schema_and_non_json_are_refused() {
    std::cout << "schema and syntax\n";
    auto wrong = base_profile();
    const auto at = wrong.find("serveros/core-lite-profile.v1");
    wrong.replace(at, std::string("serveros/core-lite-profile.v1").size(),
                  "serveros/desired-state.v1");
    const auto schema = nodecore::corelite::parse_profile(wrong);
    expect(has_code(schema, "SCHEMA"), "a v2 document is refused as a wrong schema");

    const auto broken = nodecore::corelite::parse_profile("{not json");
    expect(has_code(broken, "NOT_JSON"), "malformed JSON is refused");
}

void test_the_windows_platform_parses_but_claims_nothing() {
    std::cout << "the windows platform parses as a declaration\n";
    auto text = base_profile();
    const auto at = text.find("\"platform\": \"linux\"");
    text.replace(at, std::string("\"platform\": \"linux\"").size(),
                 "\"platform\": \"windows\"");
    const auto outcome = nodecore::corelite::parse_profile(text);
    // The profile PARSES on either platform: whether a Windows host can actually
    // deliver `psi.*` (it cannot) is a RUNTIME fact reported as UNAVAILABLE, not
    // a reason to refuse the declaration.
    expect(outcome.ok, "a windows profile parses");
    expect(outcome.profile.platform == "windows", "platform carried");
}

}  // namespace

//: Parse a shipped profile file and report it. Driven by argv so the test can
//: be pointed at `core-lite/profiles/*.profile.json` and prove the files THIS
//: repository ships are readable by the reader it also ships.
int check_shipped(const std::string& path) {
    std::cout << "shipped profile: " << path << "\n";
    std::ifstream file(path);
    if (!file) {
        std::cout << "  FAIL: cannot open\n";
        ++failures;
        return 1;
    }
    std::string text((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    const auto outcome = nodecore::corelite::parse_profile(text);
    ++checks;
    if (!outcome.ok) {
        ++failures;
        std::cout << "  FAIL: refused —";
        for (const auto& refusal : outcome.refusals) {
            std::cout << " " << refusal.code;
        }
        std::cout << "\n";
        return 1;
    }
    std::cout << "  ok | principal " << outcome.profile.principal << " | observations "
              << outcome.profile.observations.size() << " | services "
              << outcome.profile.services.size() << "\n";
    return 0;
}

int main(int argc, char** argv) {
    test_a_valid_profile_is_accepted();
    test_an_owner_that_is_not_the_principal_is_refused();
    test_an_unknown_observation_is_refused();
    test_an_observation_without_an_explicit_grant_is_refused();
    test_a_restart_field_is_refused_as_a_v1_effect();
    test_observe_only_false_is_refused();
    test_a_hardcoded_uid_is_refused();
    test_an_unlisted_unit_shape_is_refused();
    test_a_stale_or_missing_generation_is_refused();
    test_a_duplicate_service_id_is_refused();
    test_a_wrong_schema_and_non_json_are_refused();
    test_the_windows_platform_parses_but_claims_nothing();

    for (int i = 1; i < argc; ++i) {
        check_shipped(argv[i]);
    }

    std::cout << "\n" << checks << " checks, " << failures << " failures\n";
    return failures == 0 ? 0 : 1;
}
