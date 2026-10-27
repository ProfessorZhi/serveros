#include "supervisor/capability.hpp"

#include <utility>

#include "nodecore/json.hpp"

namespace nodecore::supervisor {

std::vector<UnitClaim> claims_of(const Manifest& manifest, const OwnerMap& owners) {
    // Deliberately built from `manifest_owner_refusals` rather than from a
    // second walk of the same two arguments: the acting path refuses on that
    // function's answer, and a document that recomputed the verdict its own way
    // would be a second opinion free to disagree with the refusal it describes.
    std::vector<OwnerRefusal> refusals = manifest_owner_refusals(manifest, owners);

    std::vector<UnitClaim> claims;
    claims.reserve(manifest.services.size());
    for (const ServiceDesired& service : manifest.services) {
        UnitClaim claim;
        claim.id = service.id;
        claim.unit = service.unit;
        claim.verdict = kClaimOwned;
        claim.this_core_may_act = true;
        for (const OwnerRefusal& refusal : refusals) {
            if (refusal.unit != service.unit) {
                continue;
            }
            claim.owner = refusal.owner;
            claim.this_core_may_act = false;
            claim.verdict = refusal.owner.empty() ? kClaimUndeclared : kClaimNotMine;
            break;
        }
        if (claim.this_core_may_act) {
            const std::string* owner = owners.owner_of(service.unit);
            claim.owner = owner == nullptr ? std::string() : *owner;
        }
        claims.push_back(std::move(claim));
    }
    return claims;
}

std::vector<std::string> doubly_claimed_units(const std::vector<UnitClaim>& a,
                                              const std::vector<UnitClaim>& b) {
    std::vector<std::string> shared;
    for (const UnitClaim& left : a) {
        if (!left.this_core_may_act) {
            continue;
        }
        for (const UnitClaim& right : b) {
            if (right.this_core_may_act && right.unit == left.unit) {
                shared.push_back(left.unit);
                break;
            }
        }
    }
    return shared;
}

std::string build_capability_json(const Manifest& manifest, const OwnerMap& owners,
                                  const CapabilityInfo& info) {
    nodecore::Json root = nodecore::Json::object();
    root.set("schema", nodecore::Json::string(kCapabilitySchema));
    root.set("program", nodecore::Json::string("serveros-native-core"));
    root.set("version", nodecore::Json::string(info.version));
    root.set("mode", nodecore::Json::string(info.mode));
    // The owner this program would act under. A constant of the closed
    // vocabulary rather than a guess: a Core that acted under a different name
    // would be claiming a unit the map gave to somebody else.
    root.set("owner", nodecore::Json::string(kNativeCoreOwner));
    root.set("node", nodecore::Json::string(manifest.node));
    root.set("principal", nodecore::Json::string(manifest.principal));
    root.set("manifest_sha256", nodecore::Json::string(info.manifest_sha256));
    root.set("generation", nodecore::Json::integer(manifest.generation));
    // The manifest's own observe_only is a DECLARATION, and it is deliberately
    // not the same thing as the mode this process is in: a manifest may permit
    // acts while the binary was started observe-only. Both are reported so a
    // reader can see a disagreement instead of one field hiding it.
    root.set("manifest_observe_only", nodecore::Json::boolean(manifest.observe_only));

    nodecore::Json map = nodecore::Json::object();
    // `usable` first-class rather than implied by an empty unit list: a map that
    // was refused and a map that genuinely declares nothing are different facts
    // with the same shape, and only one of them means "every unit is unowned".
    map.set("usable", nodecore::Json::boolean(owners.usable));
    if (!owners.usable) {
        map.set("code", nodecore::Json::string(owners.code));
        map.set("reason", nodecore::Json::string(owners.reason));
    }
    nodecore::Json units = nodecore::Json::array();
    for (const auto& entry : owners.owners) {
        nodecore::Json row = nodecore::Json::object();
        row.set("unit", nodecore::Json::string(entry.first));
        row.set("owner", nodecore::Json::string(entry.second));
        units.push(std::move(row));
    }
    map.set("units", std::move(units));
    root.set("owner_map", std::move(map));

    nodecore::Json claims = nodecore::Json::array();
    for (const UnitClaim& claim : claims_of(manifest, owners)) {
        nodecore::Json row = nodecore::Json::object();
        row.set("id", nodecore::Json::string(claim.id));
        row.set("unit", nodecore::Json::string(claim.unit));
        // Present only when there is an owner to name. An absent key says "the
        // map did not name one", which is not the same as naming an empty
        // string and cannot be read as the native core's own name by accident.
        if (!claim.owner.empty()) {
            row.set("owner", nodecore::Json::string(claim.owner));
        }
        row.set("verdict", nodecore::Json::string(claim.verdict));
        row.set("this_core_may_act", nodecore::Json::boolean(claim.this_core_may_act));
        claims.push(std::move(row));
    }
    root.set("claims", std::move(claims));

    return root.dump();
}

}  // namespace nodecore::supervisor
