// Targeted tests for `serveros/core-lite-status.v1` and the runtime identity.
//
// Each test holds ONE product rule, and asserts the reason word as well as the
// fact: a test that only checked `state == "UNAVAILABLE"` would pass for
// UNSUPPORTED_PLATFORM when the truth was NO_SOURCE, and those send a reader to
// two different places.
//
// Two of these — `only an ATTESTED device reference can be live` and `a caller
// supplied device reference never produces live` — are a PAIR on purpose. A
// gate that is simply closed forever and a gate that works look identical from
// one negative test; the positive control is what tells them apart.
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <string>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#include <utility>
#include <vector>

#include "corelite/identity.hpp"
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

//: A reading whose value the TEST built — a `Json`, so the test has the same
//: shape an adapter does and cannot construct a value from a text document.
nodecore::corelite::Reading present(const std::string& name, double percent) {
    nodecore::corelite::Reading reading;
    reading.name = name;
    reading.state = nodecore::corelite::ReadingState::Present;
    reading.source = "nodecore/NodeResourceSnapshot v1";
    reading.observed_at = "2026-10-15T23:07:00+00:00";
    reading.value = nodecore::Json::object();
    reading.value.set("used_percent", nodecore::Json::number(percent, 2));
    reading.has_value = true;
    return reading;
}

//: The by-default HONEST input: the device reference is this host's own
//: declaration, which is a claim the account makes about itself. Tests that want
//: a stronger basis must say so explicitly, and the tests below do — that is the
//: point of making the basis a required field rather than a default.
nodecore::corelite::StatusInput base_input(const Profile& profile) {
    nodecore::corelite::StatusInput input;
    input.profile = &profile;
    input.source_observed_at = "2026-10-15T23:07:00+00:00";
    input.device_ref_known = true;
    input.device_ref_observed = profile.node;
    input.device_ref_basis = nodecore::corelite::kBasisLocalDeclaration;
    input.principal_known = true;
    input.principal_observed = profile.principal;
    return input;
}

bool contains(const std::string& haystack, const std::string& needle) {
    return haystack.find(needle) != std::string::npos;
}

//: Re-read the produced document with the supervisor's own reader, so an
//: assertion is about the DOCUMENT and not about the writer's spacing.
nodecore::supervisor::detail::Json parse_document(const std::string& text, bool& ok) {
    nodecore::supervisor::detail::Json document;
    std::string error;
    nodecore::supervisor::detail::Reader reader(text);
    ok = reader.parse(document, error);
    if (!ok) {
        std::cout << "  (the document did not re-read: " << error << ")\n";
    }
    return document;
}

const std::string* member_text(const nodecore::supervisor::detail::Json& object, const char* key) {
    const auto* found = object.find(key);
    if (found == nullptr || !found->is_string()) {
        return nullptr;
    }
    return &found->text;
}

// ---------------------------------------------------------------------------
// The identity gate, which is what the two P0 findings were about
// ---------------------------------------------------------------------------

//: A caller-supplied device reference that happens to equal the profile proves
//: nothing: the caller chose both sides of the comparison. This is the negative
//: test the review asked for, and it must hold for the WHOLE document — not just
//: for the `live` flag, since a reader that trusts `device_identity.state` would
//: otherwise be reading `MATCHED`.
void test_a_caller_supplied_device_ref_never_produces_live() {
    std::cout << "a caller-supplied device reference can never make this live\n";
    const Profile profile = profile_with({"cpu"});
    for (const std::string& basis : {std::string(nodecore::corelite::kBasisCallerSupplied),
                                     std::string(nodecore::corelite::kBasisNone),
                                     std::string("BANANA")}) {
        auto input = base_input(profile);
        input.device_ref_basis = basis;
        input.readings.push_back(present("cpu", 12.5));
        const auto outcome = nodecore::corelite::build_status(input);
        expect(outcome.ok, "status built for basis " + basis);
        expect(!outcome.live, "live: false for basis \"" + basis + "\"");
        // Asserted on the DEVICE's own basis, not on the whole document: the
        // principal verdict legitimately IS `ATTESTED`, because this process read
        // its own account straight from the OS. Searching the document for the
        // word would flag that correct answer as a leak.
        expect(outcome.device.basis != nodecore::corelite::kBasisAttested,
               "the DEVICE never claims ATTESTED for \"" + basis + "\"");
        bool parsed = false;
        const auto document = parse_document(outcome.json, parsed);
        const auto* device = document.find("device_identity");
        const auto* device_basis = device == nullptr ? nullptr : device->find("basis");
        expect(device_basis != nullptr && device_basis->is_string() &&
                   device_basis->text != nodecore::corelite::kBasisAttested,
               "and the document's device_identity says so for \"" + basis + "\"");
    }
    // And the readable half: a caller-supplied value is named as such, so an
    // operator can see WHY it did not count rather than guessing.
    auto named = base_input(profile);
    named.device_ref_basis = nodecore::corelite::kBasisCallerSupplied;
    const auto outcome = nodecore::corelite::build_status(named);
    expect(outcome.device.basis == nodecore::corelite::kBasisCallerSupplied,
           "the caller-supplied basis is reported as CALLER_SUPPLIED");
    expect(!outcome.device.reason.empty(), "with a reason saying why it is not evidence");
}

//: The positive control. Without this, `live` could be hardcoded false and every
//: negative test above would still pass.
void test_only_an_attested_device_reference_can_be_live() {
    std::cout << "an ATTESTED device reference IS live — the gate opens when it should\n";
    const Profile profile = profile_with({"cpu"});
    auto input = base_input(profile);
    input.device_ref_basis = nodecore::corelite::kBasisAttested;
    input.readings.push_back(present("cpu", 12.5));
    const auto outcome = nodecore::corelite::build_status(input);
    expect(outcome.ok, "status built");
    expect(outcome.live, "live: true when the device reference is attested");
    expect(outcome.device.basis == nodecore::corelite::kBasisAttested, "and it says ATTESTED");
    // The document says so out loud, so a consumer need not know the rule.
    bool parsed = false;
    const auto document = parse_document(outcome.json, parsed);
    expect(parsed, "the document re-reads");
    const auto* live = document.find("live");
    expect(live != nullptr && live->is_bool() && live->boolean, "and the document says live: true");
}

//: THE TEST THE OLD ONE BECAME. A local declaration that matches is still a
//: claim the account makes about itself, so it is MATCHED and NOT live. If this
//: ever starts returning live:true, a file anybody in the account can write has
//: become proof of which device this is.
void test_a_local_declaration_matches_but_is_not_live() {
    std::cout << "a local declaration matches, and is NOT live\n";
    const Profile profile = profile_with({"cpu", "memory"});
    auto input = base_input(profile);
    input.readings.push_back(present("cpu", 12.5));
    const auto outcome = nodecore::corelite::build_status(input);
    expect(outcome.ok, "status built");
    expect(outcome.device.state == "MATCHED", "the declaration matches the profile");
    expect(outcome.device.basis == nodecore::corelite::kBasisLocalDeclaration,
           "and says it is a LOCAL_DECLARATION");
    expect(!outcome.live, "live: false — the account agreeing with itself is not evidence");
    expect(contains(outcome.json, "\"mode\": \"observe-only\""), "mode is observe-only");
    expect(contains(outcome.json, std::string("\"schema\": \"") + kStatusSchema + "\""),
           "the status schema is stamped");
}

void test_an_unauthorised_observation_is_never_emitted() {
    std::cout << "an unauthorised observation does not appear at all\n";
    Profile profile = profile_with({"cpu"});
    // memory is NOT granted. The adapter has a reading for it anyway.
    auto input = base_input(profile);
    input.readings.push_back(present("cpu", 1.0));
    input.readings.push_back(present("memory", 80.0));
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
    expect(!contains(outcome.json, "\"state\": \"PRESENT\""), "nothing claims PRESENT");
}

void test_windows_psi_is_unsupported_and_not_implemented_is_different() {
    std::cout << "UNSUPPORTED_PLATFORM and NOT_IMPLEMENTED are two words\n";
    Profile profile = profile_with({"cpu", "psi.memory"});
    profile.platform = "windows";
    auto input = base_input(profile);
    input.platform_supports_psi = false;

    // (a) a Windows host with no collector: cpu NOT_IMPLEMENTED (none written),
    //     psi UNSUPPORTED_PLATFORM (the platform cannot, ever).
    input.platform_has_collector = false;
    const auto cold = nodecore::corelite::build_status(input);
    expect(contains(cold.json, "UNSUPPORTED_PLATFORM"), "psi is UNSUPPORTED_PLATFORM");
    expect(contains(cold.json, "NOT_IMPLEMENTED"), "the rest is NOT_IMPLEMENTED");
    expect(!contains(cold.json, "\"value\""), "a Windows host reports no values at all");

    // (b) a Windows host that DOES have a collector: psi still cannot be read.
    input.platform_has_collector = true;
    input.readings.push_back(present("cpu", 9.0));
    const auto warm = nodecore::corelite::build_status(input);
    expect(contains(warm.json, "UNSUPPORTED_PLATFORM"), "psi is still UNSUPPORTED_PLATFORM");
    expect(contains(warm.json, "9.0") || contains(warm.json, "9"),
           "cpu is PRESENT once a collector exists");
}

void test_a_principal_mismatch_reports_nothing() {
    std::cout << "a principal mismatch emits no observations\n";
    const Profile profile = profile_with({"cpu"});
    auto input = base_input(profile);
    input.principal_observed = "someoneelse";
    input.readings.push_back(present("cpu", 12.5));
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
    input.readings.push_back(present("cpu", 12.5));
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
        input.readings.push_back(present("cpu", 1.0));
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
    input.device_ref_basis = nodecore::corelite::kBasisNone;
    input.readings.push_back(present("cpu", 12.5));
    const auto outcome = nodecore::corelite::build_status(input);
    expect(outcome.ok, "status built");
    expect(!outcome.live, "live: false — the device is not established");
    expect(outcome.device.state == "UNKNOWN", "the device verdict says UNKNOWN");
    expect(outcome.device.basis == nodecore::corelite::kBasisNone, "and carries no basis");
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
    expect(nodecore::corelite::known_basis("ATTESTED"), "ATTESTED is a known basis");
    expect(!nodecore::corelite::known_basis("TRUST_ME"), "TRUST_ME is not");
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

void test_no_secret_shape_reaches_the_document() {
    std::cout << "the document carries no secret-shaped key\n";
    const Profile profile = profile_with({"cpu", "connector"});
    auto input = base_input(profile);
    input.readings.push_back(present("cpu", 1.0));
    const auto outcome = nodecore::corelite::build_status(input);
    for (const char* forbidden : {"argv", "environ", "token", "secret", "password",
                                  "credential", "private_key", "config_path", "mount_point"}) {
        expect(!contains(outcome.json, forbidden),
               std::string("no ") + forbidden + " in the status");
    }
}

//: THE FRESHNESS RULE. The document must not present a timestamp as if this
//: program had checked it. It carries the SOURCE's stamp and says out loud that
//: freshness was not asserted, because deciding a reading is stale needs a clock
//: and a policy that belong to a trusted reader, not to the document.
void test_freshness_is_not_asserted_and_the_time_is_the_sources() {
    std::cout << "the document states the source's time and asserts no freshness\n";
    const Profile profile = profile_with({"cpu", "memory"});
    for (const std::string& source_time :
         {std::string("2019-01-01T00:00:00+00:00"), std::string("2026-10-15T23:07:00+00:00"),
          std::string("")}) {
        auto input = base_input(profile);
        input.source_observed_at = source_time;
        input.readings.push_back(present("cpu", 1.0));
        const auto outcome = nodecore::corelite::build_status(input);
        bool parsed = false;
        const auto document = parse_document(outcome.json, parsed);
        expect(parsed, "the document re-reads");
        const auto* freshness = document.find("freshness");
        expect(freshness != nullptr && freshness->is_string() &&
                   freshness->text == nodecore::corelite::kFreshnessNotAsserted,
               "freshness is stated as NOT_ASSERTED");
        // Read off the TOP LEVEL, not by substring: a reading row carries its own
        // `observed_at` legitimately, so a substring check would be about the
        // rows and not about the document's own stamp.
        const auto* stated = document.find("observed_at");
        if (source_time.empty()) {
            expect(stated == nullptr,
                   "with no source time, the document states none rather than inventing one");
        } else {
            // A seven-year-old stamp is carried EXACTLY as given, not corrected
            // and not replaced with "now" — the reader is the one who knows it
            // is old.
            expect(stated != nullptr && stated->is_string() && stated->text == source_time,
                   "the source's own stamp is carried unchanged: " + source_time);
        }
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

// ---------------------------------------------------------------------------
// The runtime readback, and the hardening of the declaration file read
// ---------------------------------------------------------------------------

void test_the_runtime_readback_is_real_on_this_host() {
    std::cout << "the runtime identity readback\n";
    const auto identity = nodecore::corelite::current_user();
#if defined(__linux__)
    // A real readback on Linux: this process HAS a uid and, on an ordinary host,
    // a name and a home. Asserted as a fact about the platform, not about a
    // particular account — the test must pass for whoever runs it.
    expect(identity.uid >= 0, "a uid was read");
    expect(identity.known, "the account name resolved");
    expect(!identity.user.empty(), "the name is non-empty");
    expect(!identity.home.empty() && identity.home.front() == '/',
           "the home came from the user database and is absolute");
#else
    expect(!identity.known, "off Linux the readback reports UNKNOWN, never a guess");
#endif

    // A declaration that does not exist must be UNKNOWN, not a default. The
    // home here is a real directory (the OS's own) with a path inside it that is
    // not there, so the failure is the MISSING file and not a missing home.
    const auto missing = nodecore::corelite::managed_node_identity(
        identity, "no-such-dir-for-test/node-identity");
    expect(!missing.known, "a missing declaration is UNKNOWN");
    expect(missing.device_ref.empty(), "and carries no device reference");
    expect(missing.basis == nodecore::corelite::kBasisNone, "and no basis");

#if defined(__linux__)
    // An identity that was never established has no home this program may read.
    nodecore::corelite::OsIdentity unresolved;
    const auto no_identity = nodecore::corelite::managed_node_identity(
        unresolved, ".config/serveros/node-identity");
    expect(!no_identity.known, "without a resolved account there is no declaration to read");
#endif
}

#if defined(__linux__)
//: The declaration file is INPUT. These are the redirections a program must not
//: follow, each built for real in a directory this test owns and removes.
void test_the_declaration_read_refuses_redirection() {
    std::cout << "a symlinked or world-writable declaration is refused\n";
    const auto os_identity = nodecore::corelite::current_user();
    if (!os_identity.known) {
        expect(false, "this test needs a resolved account");
        return;
    }

    char pattern[] = "/tmp/corelite-decl-XXXXXX";
    const char* root = ::mkdtemp(pattern);
    if (root == nullptr) {
        expect(false, "a temporary directory could be created");
        return;
    }
    const std::string dir(root);
    const std::string real_dir = dir + "/real";
    ::mkdir(real_dir.c_str(), 0700);
    const std::string real_file = real_dir + "/node-identity";
    {
        std::FILE* file = std::fopen(real_file.c_str(), "wb");
        if (file != nullptr) {
            std::fputs("device:tokyo-shared-dev-01\n", file);
            std::fclose(file);
        }
        ::chmod(real_file.c_str(), 0600);
    }

    // (a) the happy path, so the refusals below are known to be about the
    //     thing under test rather than about a directory that never worked.
    {
        nodecore::corelite::OsIdentity os = os_identity;
        os.home = real_dir;
        const auto good = nodecore::corelite::managed_node_identity(os, "node-identity");
        expect(good.known, "a plain owned 0600 declaration IS read: " + good.error);
        expect(good.device_ref == "device:tokyo-shared-dev-01", "and its value is used");
        expect(good.basis == nodecore::corelite::kBasisLocalDeclaration,
               "and it is stamped LOCAL_DECLARATION, not attested");
    }

    // (b) a symlink in the path. `lstat` sees the link itself, so this is
    //     refused rather than followed to the file that works.
    const std::string link = dir + "/link";
    if (::symlink(real_file.c_str(), link.c_str()) == 0) {
        nodecore::corelite::OsIdentity os = os_identity;
        os.home = dir;
        const auto linked = nodecore::corelite::managed_node_identity(os, "link");
        expect(!linked.known, "a symlinked declaration is refused");
        expect(!linked.error.empty(), "with a reason naming the link");
    } else {
        expect(false, "the symlink fixture could be created");
    }

    // (c) a declaration group- or world-writable: anybody could rewrite it, so it
    //     is not this account's statement.
    const std::string loose = real_dir + "/loose";
    {
        std::FILE* file = std::fopen(loose.c_str(), "wb");
        if (file != nullptr) {
            std::fputs("device:tokyo-shared-dev-01\n", file);
            std::fclose(file);
        }
        ::chmod(loose.c_str(), 0666);
    }
    {
        nodecore::corelite::OsIdentity os = os_identity;
        os.home = real_dir;
        const auto writable = nodecore::corelite::managed_node_identity(os, "loose");
        expect(!writable.known, "a world-writable declaration is refused");
    }

    // (d) a declaration that is not one line of `device:<id>`.
    const std::string junk = real_dir + "/junk";
    {
        std::FILE* file = std::fopen(junk.c_str(), "wb");
        if (file != nullptr) {
            std::fputs("not-a-device\n", file);
            std::fclose(file);
        }
        ::chmod(junk.c_str(), 0600);
    }
    {
        nodecore::corelite::OsIdentity os = os_identity;
        os.home = real_dir;
        const auto malformed = nodecore::corelite::managed_node_identity(os, "junk");
        expect(!malformed.known, "a declaration that is not device:<id> is refused");
    }

    // Clean up exactly what this test created, by name. Never a recursive remove
    // over a directory whose contents the test did not enumerate.
    ::unlink(link.c_str());
    ::unlink(loose.c_str());
    ::unlink(junk.c_str());
    ::unlink(real_file.c_str());
    ::rmdir(real_dir.c_str());
    ::rmdir(dir.c_str());
}
#endif

}  // namespace

int main() {
    test_a_caller_supplied_device_ref_never_produces_live();
    test_only_an_attested_device_reference_can_be_live();
    test_a_local_declaration_matches_but_is_not_live();
    test_an_unauthorised_observation_is_never_emitted();
    test_a_missing_reading_is_unavailable_and_never_zero();
    test_windows_psi_is_unsupported_and_not_implemented_is_different();
    test_a_principal_mismatch_reports_nothing();
    test_an_unknown_principal_reports_nothing();
    test_a_device_mismatch_stops_the_live_claim();
    test_an_unknown_device_still_reports_this_accounts_readings();
    test_stale_and_unknown_are_carried_and_never_zero();
    test_a_reason_outside_the_closed_set_becomes_no_source();
    test_no_secret_shape_reaches_the_document();
    test_freshness_is_not_asserted_and_the_time_is_the_sources();
    test_observe_only_off_is_refused();
    test_the_runtime_readback_is_real_on_this_host();
#if defined(__linux__)
    test_the_declaration_read_refuses_redirection();
#endif

    std::cout << "\n" << checks << " checks, " << failures << " failures\n";
    return failures == 0 ? 0 : 1;
}
