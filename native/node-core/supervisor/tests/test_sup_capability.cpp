// What the Core says it is, and who owns what.
//
// The load-bearing assertion is not "the document has a schema key". It is that
// `claims_of` and the acting path's refusal are the SAME verdict: the resident
// loop refuses a unit through `manifest_owner_refusals`, and this document is
// supposed to render that refusal rather than form a second opinion about it.
// So several cases below assert both at once, and the two-writer case asserts
// the property the whole module exists for — two programs told by one map that
// the same unit is theirs.
#include <string>
#include <vector>

#include "check.hpp"
#include "supervisor/capability.hpp"
#include "supervisor/facts.hpp"

using namespace nodecore::supervisor;

namespace {

const char* kMap = R"({"schema":"serveros/native-core-owner-map.v1",
                        "units":{"mine.service":"native-core",
                                 "theirs.service":"serverosd"}})";

Manifest manifest_naming(const std::vector<std::string>& units) {
    Manifest manifest;
    manifest.node = "dell-fjd";
    manifest.principal = "fjd";
    manifest.generation = 7;
    manifest.sha256 = std::string(64, 'a');
    for (std::size_t i = 0; i < units.size(); ++i) {
        ServiceDesired service;
        service.id = "svc-" + std::to_string(i);
        service.unit = units[i];
        service.owner = "fjd";
        manifest.services.push_back(service);
    }
    return manifest;
}

CapabilityInfo info() {
    CapabilityInfo value;
    value.version = "0.2.0";
    value.mode = kCapabilityModeObserve;
    value.manifest_sha256 = std::string(64, 'a');
    return value;
}

const UnitClaim* find_claim(const std::vector<UnitClaim>& claims, const std::string& unit) {
    for (const UnitClaim& claim : claims) {
        if (claim.unit == unit) return &claim;
    }
    return nullptr;
}

// ------------------------------------------------------------ the verdicts

void a_unit_the_map_gives_this_core_is_owned() {
    const OwnerMap owners = load_owner_map(kMap);
    const std::vector<UnitClaim> claims = claims_of(manifest_naming({"mine.service"}), owners);
    CHECK_EQ(claims.size(), std::size_t{1});
    const UnitClaim* claim = find_claim(claims, "mine.service");
    CHECK(claim != nullptr);
    if (claim != nullptr) {
        CHECK_EQ(claim->verdict, std::string(kClaimOwned));
        CHECK_EQ(claim->owner, std::string(kNativeCoreOwner));
        CHECK(claim->this_core_may_act);
    }
}

void a_unit_the_map_gives_the_python_daemon_is_not_mine() {
    const OwnerMap owners = load_owner_map(kMap);
    const Manifest manifest = manifest_naming({"theirs.service"});
    const std::vector<UnitClaim> claims = claims_of(manifest, owners);
    const UnitClaim* claim = find_claim(claims, "theirs.service");
    CHECK(claim != nullptr);
    if (claim != nullptr) {
        CHECK_EQ(claim->verdict, std::string(kClaimNotMine));
        CHECK_EQ(claim->owner, std::string("serverosd"));
        CHECK(!claim->this_core_may_act);
    }
    // And the acting path refuses the same unit. If these two ever disagree, the
    // document is describing a decision the Core does not make.
    CHECK_EQ(manifest_owner_refusals(manifest, owners).size(), std::size_t{1});
}

void a_unit_the_map_does_not_name_is_undeclared() {
    const OwnerMap owners = load_owner_map(kMap);
    const Manifest manifest = manifest_naming({"nobody.service"});
    const std::vector<UnitClaim> claims = claims_of(manifest, owners);
    const UnitClaim* claim = find_claim(claims, "nobody.service");
    CHECK(claim != nullptr);
    if (claim != nullptr) {
        CHECK_EQ(claim->verdict, std::string(kClaimUndeclared));
        // Empty, and the rendered document must omit the key entirely rather
        // than print an empty owner a reader could mistake for a real one.
        CHECK(claim->owner.empty());
        CHECK(!claim->this_core_may_act);
    }
    CHECK_EQ(manifest_owner_refusals(manifest, owners).size(), std::size_t{1});
}

void an_unusable_map_makes_every_unit_unowned() {
    const OwnerMap owners = load_owner_map(nullptr);
    CHECK(!owners.usable);
    const std::vector<UnitClaim> claims =
        claims_of(manifest_naming({"mine.service", "theirs.service"}), owners);
    CHECK_EQ(claims.size(), std::size_t{2});
    for (const UnitClaim& claim : claims) {
        CHECK(!claim.this_core_may_act);
        CHECK_EQ(claim.verdict, std::string(kClaimUndeclared));
        CHECK(claim.owner.empty());
    }
}

// --------------------------------------------------------- the two writers

void two_programs_claiming_one_unit_is_a_value_not_a_review() {
    const OwnerMap owners = load_owner_map(kMap);
    // The same manifest read by two programs that each believed the map made
    // them the owner. `load_owner_map` cannot produce this for one reader — the
    // map is one value per unit — so the conflict here stands for the real
    // failure: two programs NOT reading the same map, or not reading it the same
    // way.
    const std::vector<UnitClaim> a = claims_of(manifest_naming({"mine.service", "theirs.service"}), owners);
    std::vector<UnitClaim> b = a;
    for (UnitClaim& claim : b) {
        claim.this_core_may_act = true;
        claim.verdict = kClaimOwned;
    }

    const std::vector<std::string> shared = doubly_claimed_units(a, b);
    CHECK_EQ(shared.size(), std::size_t{1});
    if (!shared.empty()) {
        CHECK_EQ(shared[0], std::string("mine.service"));
    }
}

void two_programs_that_own_different_units_conflict_on_none() {
    const OwnerMap owners = load_owner_map(kMap);
    const std::vector<UnitClaim> a = claims_of(manifest_naming({"mine.service"}), owners);
    const std::vector<UnitClaim> b = claims_of(manifest_naming({"theirs.service"}), owners);
    CHECK(doubly_claimed_units(a, b).empty());
    // A program that may act on nothing conflicts with nobody, which is the
    // state an observe-only Core is in.
    CHECK(!a.empty());
}

// ------------------------------------------------------------- the document

void the_document_names_the_program_and_the_map_it_read() {
    const OwnerMap owners = load_owner_map(kMap);
    const std::string text = build_capability_json(manifest_naming({"mine.service"}), owners, info());
    CHECK_CONTAINS(text, std::string("\"schema\": \"") + kCapabilitySchema + "\"");
    CHECK_CONTAINS(text, "\"program\": \"serveros-native-core\"");
    CHECK_CONTAINS(text, "\"owner\": \"native-core\"");
    CHECK_CONTAINS(text, "\"mode\": \"observe\"");
    CHECK_CONTAINS(text, "\"manifest_sha256\"");
    // The map's own contents travel with the verdict, so a reader comparing two
    // documents can tell "we read the same map" from "we agreed by accident".
    CHECK_CONTAINS(text, "\"units\"");
    CHECK_CONTAINS(text, "\"owner\": \"serverosd\"");
    CHECK_CONTAINS(text, std::string("\"verdict\": \"") + kClaimOwned + "\"");
    CHECK_CONTAINS(text, "\"this_core_may_act\": true");
    // A path or a command line must never appear here; the document is pasted
    // into chats and inventories.
    CHECK(text.find("/home/") == std::string::npos);
}

void an_unusable_map_is_reported_not_escalated() {
    const OwnerMap owners = load_owner_map(nullptr);
    const std::string text = build_capability_json(manifest_naming({"mine.service"}), owners, info());
    CHECK_CONTAINS(text, "\"usable\": false");
    CHECK_CONTAINS(text, "\"code\": \"FACTS_ABSENT\"");
    // Every claim is undeclared, and the document says so rather than omitting
    // the section — an absent section reads as "no units", which is a different
    // and much more comfortable fact.
    CHECK_CONTAINS(text, std::string("\"verdict\": \"") + kClaimUndeclared + "\"");
}

void an_empty_manifest_is_a_document_with_no_claims() {
    const OwnerMap owners = load_owner_map(kMap);
    const std::string text = build_capability_json(Manifest{}, owners, info());
    CHECK_CONTAINS(text, "\"claims\": []");
    CHECK_CONTAINS(text, "\"generation\": 0");
}

}  // namespace

int main() {
    a_unit_the_map_gives_this_core_is_owned();
    a_unit_the_map_gives_the_python_daemon_is_not_mine();
    a_unit_the_map_does_not_name_is_undeclared();
    an_unusable_map_makes_every_unit_unowned();
    two_programs_claiming_one_unit_is_a_value_not_a_review();
    two_programs_that_own_different_units_conflict_on_none();
    the_document_names_the_program_and_the_map_it_read();
    an_unusable_map_is_reported_not_escalated();
    an_empty_manifest_is_a_document_with_no_claims();
    return check::report("test_sup_capability");
}
