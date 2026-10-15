// The `serveros/core-lite-status.v1` builder. See `status.hpp` for the rules.
//
// It writes with the collector's own `nodecore::Json` and reuses
// `Json::unavailable(reason)` — the same `{"state":"UNAVAILABLE","reason":…}`
// shape `node-resource-snapshot.v1` uses, so a reader who learned the invariant
// on the collector finds it unchanged here.
#include "corelite/status.hpp"

#include <set>
#include <string>
#include <vector>

#include "nodecore/json.hpp"
#include "json_bridge.hpp"

namespace nodecore::corelite {

using nodecore::Json;
using detail::to_nodecore;

namespace {

bool starts_with(const std::string& value, const std::string& prefix) {
    return value.size() >= prefix.size() &&
           value.compare(0, prefix.size(), prefix) == 0;
}

//: The names an observation covers, for deciding whether a `psi.*` grant is
//: one this platform could ever honour.
bool is_psi(const std::string& name) { return starts_with(name, "psi."); }

void emit_identity(Json& root, const std::string& key, const IdentityVerdict& verdict) {
    Json row = Json::object();
    row.set("state", Json::string(verdict.state));
    row.set("claimed", Json::string(verdict.claimed));
    if (!verdict.observed.empty()) {
        row.set("observed", Json::string(verdict.observed));
    }
    if (!verdict.reason.empty()) {
        row.set("reason", Json::string(verdict.reason));
    }
    root.set(key, row);
}

}  // namespace

bool known_reason(const std::string& reason) {
    for (const char* candidate : kReasons) {
        if (reason == candidate) {
            return true;
        }
    }
    return false;
}

StatusOutcome build_status(const StatusInput& input) {
    StatusOutcome outcome;
    if (input.profile == nullptr) {
        outcome.refusal_code = "NO_PROFILE";
        outcome.refusal_reason = "no profile was supplied, so there is nothing to describe";
        return outcome;
    }
    const Profile& profile = *input.profile;
    if (!profile.observe_only) {
        // V1 has no acting mode; a profile that reached here with it off is a
        // caller bypassing the reader, and the status refuses rather than
        // describing a Core that could act.
        outcome.refusal_code = "V1_HAS_NO_EFFECT";
        outcome.refusal_reason =
            "this profile is not observe_only; V1 has no acting mode and refuses to describe one";
        return outcome;
    }

    // ---- the two identity verdicts ------------------------------------
    // PRINCIPAL. MATCHED only on a positive OS readback that equals the claim.
    IdentityVerdict principal;
    principal.claimed = profile.principal;
    principal.observed = input.principal_observed;
    if (!input.principal_known) {
        principal.state = kUnknown;
        principal.reason =
            "the OS could not establish which account this process runs as; a Core that cannot "
            "name its own user must not report readings under a claimed one";
    } else if (input.principal_observed == profile.principal) {
        principal.state = kMatched;
    } else {
        principal.state = kMismatched;
        principal.reason =
            "this process runs as \"" + input.principal_observed +
            "\" and the profile is for \"" + profile.principal +
            "\". Refused rather than reported: one account's Core may not describe itself as "
            "another's";
    }

    // DEVICE. MATCHED only on a known Node identity that equals the claim. JSON
    // cannot make a device healthy by asserting it.
    IdentityVerdict device;
    device.claimed = profile.node;
    device.observed = input.device_ref_observed;
    if (!input.device_ref_known) {
        device.state = kUnknown;
        device.reason =
            "the managed Node identity was not established; a profile must not claim a device's "
            "health from its own text";
    } else if (input.device_ref_observed == profile.node) {
        device.state = kMatched;
    } else {
        device.state = kMismatched;
        device.reason = "the managed Node identity is \"" + input.device_ref_observed +
                        "\" and the profile claims \"" + profile.node + "\"";
    }

    Json root = Json::object();
    root.set("schema", Json::string(kStatusSchema));
    root.set("node", Json::string(profile.node));
    root.set("principal", Json::string(profile.principal));
    root.set("platform", Json::string(profile.platform));
    root.set("generation", Json::integer(profile.generation));
    // Written down, never inferred: a client that had to guess whether the Core
    // is willing to act would guess wrong.
    root.set("mode", Json::string("observe-only"));
    if (!input.observed_at.empty()) {
        root.set("observed_at", Json::string(input.observed_at));
    }
    emit_identity(root, "principal_identity", principal);
    emit_identity(root, "device_identity", device);

    outcome.principal = principal;
    outcome.device = device;
    outcome.live = (principal.state == kMatched && device.state == kMatched);
    root.set("live", Json::boolean(outcome.live));

    // ---- the observations ---------------------------------------------
    Json observations = Json::array();

    // A principal that is not MATCHED emits NO observations. The readings would
    // be THIS process's, and filing them under the claimed principal is exactly
    // the confusion the identity gate exists to prevent.
    if (principal.state == kMatched) {
        std::set<std::string> granted;
        for (const Observation& observation : profile.observations) {
            if (observation.authorized) {
                granted.insert(observation.name);
            }
        }
        // Readings the profile did NOT grant are dropped WITHOUT a trace: an
        // unauthorised observation must not appear even as UNAVAILABLE, because
        // its presence would disclose that the capability exists.
        std::vector<Reading> by_name;
        for (const Reading& reading : input.readings) {
            if (granted.find(reading.name) != granted.end()) {
                by_name.push_back(reading);
            }
        }

        for (const Observation& observation : profile.observations) {
            if (!observation.authorized) {
                continue;
            }
            Json row = Json::object();
            row.set("name", Json::string(observation.name));

            const Reading* found = nullptr;
            for (const Reading& reading : by_name) {
                if (reading.name == observation.name) {
                    found = &reading;
                    break;
                }
            }

            // Degradations, in the order that says the most about WHY.
            if (is_psi(observation.name) && !input.platform_supports_psi) {
                row.set("state", Json::string("UNAVAILABLE"));
                row.set("reason", Json::string("UNSUPPORTED_PLATFORM"));
                row.set("source", Json::string("platform"));
            } else if (!input.platform_has_collector) {
                row.set("state", Json::string("UNAVAILABLE"));
                row.set("reason", Json::string("NOT_IMPLEMENTED"));
                row.set("source", Json::string("platform"));
            } else if (found == nullptr) {
                row.set("state", Json::string("UNAVAILABLE"));
                row.set("reason", Json::string("NO_SOURCE"));
                row.set("source", Json::string("os-adapter"));
            } else if (found->state == ReadingState::Unavailable) {
                const std::string reason =
                    known_reason(found->reason) ? found->reason : std::string("NO_SOURCE");
                row.set("state", Json::string("UNAVAILABLE"));
                row.set("reason", Json::string(reason));
                if (!found->source.empty()) {
                    row.set("source", Json::string(found->source));
                }
            } else if (found->value_json.empty()) {
                // PRESENT with no value is not a reading. Reported as NO_SOURCE
                // rather than as an empty value, so nothing downstream renders a
                // blank where a number was promised.
                row.set("state", Json::string("UNAVAILABLE"));
                row.set("reason", Json::string("NO_SOURCE"));
                row.set("source", Json::string(found->source));
            } else {
                // The value fragment is PARSED with the supervisor's own reader
                // rather than spliced in as text: a fragment that is not JSON
                // must not become a document nobody can read.
                nodecore::supervisor::detail::Json parsed;
                std::string error;
                nodecore::supervisor::detail::Reader reader(found->value_json);
                if (!reader.parse(parsed, error)) {
                    row.set("state", Json::string("UNAVAILABLE"));
                    row.set("reason", Json::string("NO_SOURCE"));
                    row.set("source", Json::string(found->source));
                } else {
                    row.set("state", Json::string("PRESENT"));
                    row.set("value", to_nodecore(parsed));
                    row.set("source", Json::string(found->source));
                    if (!found->observed_at.empty()) {
                        row.set("observed_at", Json::string(found->observed_at));
                    }
                }
            }
            observations.push(row);
        }
    }
    root.set("observations", observations);

    outcome.ok = true;
    outcome.json = root.dump();
    return outcome;
}

}  // namespace nodecore::corelite
