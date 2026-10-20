// The strict reader for a Core-Lite profile. See `profile.hpp` for the rules.
//
// It reuses, rather than re-implements, the three things that must not have a
// second opinion: the JSON parser (`supervisor::detail::Json`/`Reader`), the
// unit-id rule (`supervisor::valid_unit_id`) and the refusal shape
// (`supervisor::Refusal`). What is local is only the field plumbing.
#include "corelite/profile.hpp"

#include <set>
#include <string>
#include <utility>

#include "supervisor/manifest.hpp"
#include "json_detail.hpp"

namespace nodecore::corelite {

using nodecore::supervisor::Refusal;
using nodecore::supervisor::valid_unit_id;
using nodecore::supervisor::detail::Json;
using nodecore::supervisor::detail::Reader;

namespace {

constexpr const char* kTopKeys[] = {"schema", "node", "principal", "platform",
                                    "observe_only", "generation", "observations",
                                    "services"};

constexpr const char* kObservationKeys[] = {"name", "authorized"};
constexpr const char* kServiceKeys[] = {"id", "owner", "unit"};

constexpr const char* kPlatforms[] = {"linux", "windows"};

void refuse(std::vector<Refusal>& out, const std::string& scope, const std::string& code,
            const std::string& reason) {
    out.push_back({scope, code, reason});
}

bool in_list(const char* const* list, std::size_t n, const std::string& value) {
    for (std::size_t i = 0; i < n; ++i) {
        if (value == list[i]) {
            return true;
        }
    }
    return false;
}

//: Refuse `key` wherever it appears, and say which V1 rule it broke. Reported
//: as one refusal per occurrence rather than silently dropped: a profile that
//: carried a V2 field has an author who believes it does something.
bool only_allowed_keys(const Json& object, const char* const* allowed, std::size_t count,
                       const std::string& scope, std::vector<Refusal>& out) {
    bool ok = true;
    for (const auto& member : object.members) {
        if (in_list(allowed, count, member.first)) {
            continue;
        }
        if (in_list(kForbiddenFields,
                    sizeof(kForbiddenFields) / sizeof(kForbiddenFields[0]), member.first)) {
            refuse(out, scope, "V1_HAS_NO_EFFECT",
                   "field \"" + member.first +
                       "\" names an EFFECT and this profile is observation only. V1 does not "
                       "restart, recover or self-heal anything — a running CC or GPU is not a "
                       "service to be bounced. A profile copied from a V2 draft is refused here "
                       "rather than quietly carrying a capability this stage does not have");
        } else if (member.first == "uid" || member.first == "uid_min" ||
                   member.first == "uid_max" || member.first == "user_id") {
            refuse(out, scope, "NO_HARDCODED_UID",
                   "field \"" + member.first +
                       "\" pins a numeric account id. The UID is resolved from the running user "
                       "(%h, getuid()) so that ONE profile installs on any account; a file that "
                       "carried an id would install Tokyo's `vince` profile onto Dell's `fjd` "
                       "account with one scp, and the file would still look correct");
        } else {
            refuse(out, scope, "UNKNOWN_FIELD",
                   "field \"" + member.first + "\" is not one this version reads");
        }
        ok = false;
    }
    return ok;
}

bool require_string(const Json& object, const std::string& key, const std::string& scope,
                    std::string& target, std::vector<Refusal>& out) {
    const Json* value = object.find(key);
    if (value == nullptr) {
        refuse(out, scope, "MISSING", "field \"" + key + "\" is required");
        return false;
    }
    if (!value->is_string() || value->text.empty()) {
        refuse(out, scope, "TYPE", "field \"" + key + "\" must be a non-empty string");
        return false;
    }
    target = value->text;
    return true;
}

}  // namespace

bool known_observation(const std::string& name) {
    return in_list(kObservationNames,
                   sizeof(kObservationNames) / sizeof(kObservationNames[0]), name);
}

ProfileOutcome parse_profile(const std::string& text) {
    ProfileOutcome outcome;
    Json document;
    std::string error;
    Reader reader(text);
    if (!reader.parse(document, error)) {
        refuse(outcome.refusals, "", "NOT_JSON", "the profile is not JSON: " + error);
        return outcome;
    }
    if (!document.is_object()) {
        refuse(outcome.refusals, "", "TYPE", "a profile is a JSON object");
        return outcome;
    }
    if (!only_allowed_keys(document, kTopKeys,
                           sizeof(kTopKeys) / sizeof(kTopKeys[0]), "",
                           outcome.refusals)) {
        return outcome;
    }

    bool ok = true;
    ok = require_string(document, "schema", "", outcome.profile.schema, outcome.refusals) && ok;
    if (ok && outcome.profile.schema != kProfileSchema) {
        refuse(outcome.refusals, "", "SCHEMA",
               "schema is \"" + outcome.profile.schema + "\"; this version reads \"" +
                   kProfileSchema + "\"");
        return outcome;
    }
    ok = require_string(document, "node", "", outcome.profile.node, outcome.refusals) && ok;
    ok = require_string(document, "principal", "", outcome.profile.principal, outcome.refusals) &&
         ok;

    if (const Json* platform = document.find("platform")) {
        if (!platform->is_string() || platform->text.empty() ||
            !in_list(kPlatforms, sizeof(kPlatforms) / sizeof(kPlatforms[0]), platform->text)) {
            refuse(outcome.refusals, "", "PLATFORM",
                   "field \"platform\" must be one of linux, windows");
            ok = false;
        } else {
            outcome.profile.platform = platform->text;
        }
    }

    // V1 HAS NO ACTING MODE. The default is observe-only and the OTHER value is
    // refused, so the field is written down rather than absent-and-assumed.
    if (const Json* observe_only = document.find("observe_only")) {
        if (!observe_only->is_bool()) {
            refuse(outcome.refusals, "", "TYPE", "field \"observe_only\" must be a boolean");
            ok = false;
        } else if (!observe_only->boolean) {
            refuse(outcome.refusals, "", "V1_HAS_NO_EFFECT",
                   "\"observe_only\": false asks for an acting Core, and V1 has none. The default "
                   "is true and the false value is refused rather than ignored, so that a profile "
                   "cannot enable an effect by asserting it is already allowed");
            ok = false;
        }
    }

    const Json* generation = document.find("generation");
    if (generation == nullptr) {
        refuse(outcome.refusals, "", "MISSING", "field \"generation\" is required");
        ok = false;
    } else if (!generation->is_int() || generation->integer < 0) {
        refuse(outcome.refusals, "", "TYPE",
               "field \"generation\" must be a non-negative integer");
        ok = false;
    } else {
        outcome.profile.generation = static_cast<long>(generation->integer);
    }

    const Json* observations = document.find("observations");
    if (observations == nullptr || !observations->is_array()) {
        refuse(outcome.refusals, "", observations == nullptr ? "MISSING" : "TYPE",
               "field \"observations\" must be an array");
        return outcome;
    }
    for (std::size_t i = 0; i < observations->items.size(); ++i) {
        const Json& item = observations->items[i];
        const std::string scope = "observations[" + std::to_string(i) + "]";
        if (!item.is_object()) {
            refuse(outcome.refusals, scope, "TYPE", "an observation is a JSON object");
            ok = false;
            continue;
        }
        if (!only_allowed_keys(item, kObservationKeys,
                               sizeof(kObservationKeys) / sizeof(kObservationKeys[0]), scope,
                               outcome.refusals)) {
            ok = false;
            continue;
        }
        Observation observation;
        ok = require_string(item, "name", scope, observation.name, outcome.refusals) && ok;
        if (!observation.name.empty() && !known_observation(observation.name)) {
            refuse(outcome.refusals, scope, "UNKNOWN_OBSERVATION",
                   "\"" + observation.name +
                       "\" is not in this version's observation vocabulary; an observation nobody "
                       "can name is a capability nobody reviewed");
            ok = false;
        }
        if (const Json* authorized = item.find("authorized")) {
            if (!authorized->is_bool()) {
                refuse(outcome.refusals, scope, "TYPE",
                       "\"authorized\" must be a boolean and must be written down");
                ok = false;
            } else {
                observation.authorized = authorized->boolean;
            }
        } else {
            // Absent is NOT «granted»: the owner states it, or it is not granted.
            refuse(outcome.refusals, scope, "MISSING",
                   "\"authorized\" is required on every observation. Absent must not mean "
                   "granted — that reading is how a capability arrives that nobody agreed to");
            ok = false;
        }
        outcome.profile.observations.push_back(std::move(observation));
    }

    const Json* services = document.find("services");
    if (services == nullptr || !services->is_array()) {
        refuse(outcome.refusals, "", services == nullptr ? "MISSING" : "TYPE",
               "field \"services\" must be an array (it may be empty)");
        return outcome;
    }
    std::set<std::string> seen_ids;
    for (std::size_t i = 0; i < services->items.size(); ++i) {
        const Json& item = services->items[i];
        const std::string scope = "services[" + std::to_string(i) + "]";
        if (!item.is_object()) {
            refuse(outcome.refusals, scope, "TYPE", "a service entry is a JSON object");
            ok = false;
            continue;
        }
        if (!only_allowed_keys(item, kServiceKeys,
                               sizeof(kServiceKeys) / sizeof(kServiceKeys[0]), scope,
                               outcome.refusals)) {
            ok = false;
            continue;
        }
        ServiceEntry service;
        ok = require_string(item, "id", scope, service.id, outcome.refusals) && ok;
        ok = require_string(item, "owner", scope, service.owner, outcome.refusals) && ok;
        ok = require_string(item, "unit", scope, service.unit, outcome.refusals) && ok;
        if (!service.owner.empty() && service.owner != outcome.profile.principal) {
            refuse(outcome.refusals, scope, "OWNER_MISMATCH",
                   "owner is \"" + service.owner + "\" and this profile is for \"" +
                       outcome.profile.principal +
                       "\". Refused rather than filtered: a Core-Lite that quietly dropped it "
                       "would leave a reader believing the account had been considered");
            ok = false;
        }
        if (!service.unit.empty() && !valid_unit_id(service.unit)) {
            refuse(outcome.refusals, scope, "UNIT_ID",
                   "\"" + service.unit +
                       "\" is not a unit id this program may name. The rule is the SAME "
                       "allowlist function the supervisor uses, so the reader and the executor "
                       "cannot drift about what a unit id is");
            ok = false;
        }
        if (!service.id.empty() && !seen_ids.insert(service.id).second) {
            refuse(outcome.refusals, scope, "DUPLICATE_ID",
                   "\"" + service.id + "\" appears twice; two entries under one id is two "
                   "entries one of which is unreachable");
            ok = false;
        }
        outcome.profile.services.push_back(std::move(service));
    }

    outcome.ok = ok && outcome.refusals.empty();
    return outcome;
}

}  // namespace nodecore::corelite
