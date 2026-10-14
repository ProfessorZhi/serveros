#include "supervisor/manifest.hpp"

#include <cctype>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "json_detail.hpp"
#include "supervisor/sha256.hpp"

namespace nodecore::supervisor {
namespace {
// The strict reader lives in `json_detail.hpp`, because the recovery state this
// program writes for itself is read back with the same rules: one reader rather
// than two free to disagree about what a duplicate key or a number is.
using detail::Json;
using detail::Reader;

// ------------------------------------------------------------- mapping help

void refuse(std::vector<Refusal>& out, std::string scope, std::string code, std::string reason) {
    out.push_back(Refusal{std::move(scope), std::move(code), std::move(reason)});
}

// Every key in `object` must be one of `allowed`. A reader that ignores the
// rest is a reader whose author cannot tell a typo from a decision.
bool only_allowed_keys(const Json& object, const std::vector<const char*>& allowed,
                       const std::string& scope, std::vector<Refusal>& out) {
    bool ok = true;
    for (const auto& member : object.members) {
        bool known = false;
        for (const char* candidate : allowed) {
            if (member.first == candidate) {
                known = true;
                break;
            }
        }
        if (!known) {
            ok = false;
            refuse(out, scope, "UNKNOWN_FIELD",
                   "unknown field \"" + member.first +
                       "\": this reader refuses rather than ignoring it, because a field "
                       "nobody reads is a rule nobody applied");
        }
    }
    return ok;
}

bool require_string(const Json& object, const char* key, const std::string& scope,
                    std::string& out, std::vector<Refusal>& refusals) {
    const Json* found = object.find(key);
    if (found == nullptr) {
        refuse(refusals, scope, "MISSING", std::string("field \"") + key + "\" is required");
        return false;
    }
    if (!found->is_string()) {
        refuse(refusals, scope, "TYPE",
               std::string("field \"") + key + "\" must be a string");
        return false;
    }
    if (found->text.empty()) {
        refuse(refusals, scope, "EMPTY", std::string("field \"") + key + "\" must not be empty");
        return false;
    }
    out = found->text;
    return true;
}

bool optional_positive_int(const Json& object, const char* key, const std::string& scope,
                           long long minimum, long long fallback, long long& out,
                           std::vector<Refusal>& refusals) {
    const Json* found = object.find(key);
    if (found == nullptr) {
        out = fallback;
        return true;
    }
    if (!found->is_int()) {
        refuse(refusals, scope, "TYPE", std::string("field \"") + key + "\" must be an integer");
        return false;
    }
    if (found->integer < minimum) {
        refuse(refusals, scope, "RANGE",
               std::string("field \"") + key + "\" must be at least " + std::to_string(minimum) +
                   ", got " + std::to_string(found->integer));
        return false;
    }
    out = found->integer;
    return true;
}

bool parse_int_list(const Json& value, const std::string& scope, const char* key,
                    std::vector<int>& out, std::vector<Refusal>& refusals) {
    if (!value.is_array()) {
        refuse(refusals, scope, "TYPE", std::string("field \"") + key + "\" must be an array");
        return false;
    }
    if (value.items.empty()) {
        refuse(refusals, scope, "RANGE",
               std::string("field \"") + key + "\" must not be empty");
        return false;
    }
    out.clear();
    int previous = 0;
    bool first = true;
    for (const Json& item : value.items) {
        if (!item.is_int()) {
            refuse(refusals, scope, "TYPE",
                   std::string("every entry of \"") + key + "\" must be an integer");
            return false;
        }
        if (item.integer < 1) {
            refuse(refusals, scope, "RANGE",
                   std::string("every entry of \"") + key + "\" must be at least 1 second");
            return false;
        }
        if (!first && item.integer < previous) {
            // A ladder that goes down re-tries sooner after each failure, which
            // is a storm rather than a backoff. The Python core's own ladder
            // (60, 300, 900) is non-decreasing for this reason.
            refuse(refusals, scope, "RANGE",
                   std::string("field \"") + key + "\" must be non-decreasing");
            return false;
        }
        previous = static_cast<int>(item.integer);
        first = false;
        out.push_back(previous);
    }
    return true;
}

bool parse_string_list(const Json& value, const std::string& scope, const char* key,
                       std::vector<std::string>& out, std::vector<Refusal>& refusals) {
    if (!value.is_array()) {
        refuse(refusals, scope, "TYPE", std::string("field \"") + key + "\" must be an array");
        return false;
    }
    out.clear();
    for (const Json& item : value.items) {
        if (!item.is_string() || item.text.empty()) {
            refuse(refusals, scope, "TYPE",
                   std::string("every entry of \"") + key + "\" must be a non-empty string");
            return false;
        }
        out.push_back(item.text);
    }
    return true;
}

const std::vector<const char*> kTopKeys{"schema", "node", "principal", "observe_only",
                                        "generation", "services"};
const std::vector<const char*> kServiceKeys{"id",     "owner",          "unit", "desired",
                                            "start_on_boot", "depends_on", "health", "recovery"};
const std::vector<const char*> kHealthKeys{"probe", "stale_after_seconds"};
const std::vector<const char*> kRecoveryKeys{"max_attempts", "window_seconds", "backoff_seconds",
                                             "circuit", "allowed_verbs"};
const std::vector<const char*> kCircuitKeys{"consecutive_failures", "cooldown_seconds"};

bool parse_health(const Json& parent, const std::string& scope, HealthPolicy& out,
                  std::vector<Refusal>& refusals) {
    const Json* health = parent.find("health");
    if (health == nullptr) {
        return true;
    }
    if (!health->is_object()) {
        refuse(refusals, scope, "TYPE", "field \"health\" must be an object");
        return false;
    }
    if (!only_allowed_keys(*health, kHealthKeys, scope, refusals)) {
        return false;
    }
    // `probe` is OPTIONAL and defaults to the one probe this version implements.
    // Requiring it would make `"health": {"stale_after_seconds": 60}` a refusal
    // for omitting a field whose only legal value is the default — ceremony
    // that stops an author without protecting anything. What is NOT optional is
    // the vocabulary: a probe that is named and is not this one is refused, so
    // a manifest that asked for a different check cannot be read as having
    // asked for this one.
    bool ok = true;
    if (const Json* probe = health->find("probe")) {
        if (!probe->is_string() || probe->text.empty()) {
            refuse(refusals, scope, "TYPE",
                   "field \"health.probe\" must be a non-empty string");
            ok = false;
        } else {
            out.probe = probe->text;
        }
    }
    if (ok && out.probe != "systemd-is-active") {
        refuse(refusals, scope, "VOCABULARY",
               "health.probe is \"" + out.probe +
                   "\"; this version implements only \"systemd-is-active\"");
        ok = false;
    }
    long long stale = 0;
    ok = optional_positive_int(*health, "stale_after_seconds", scope, 1, 120, stale, refusals) && ok;
    out.stale_after_seconds = static_cast<int>(stale);
    return ok;
}

bool parse_recovery(const Json& parent, const std::string& scope, RecoveryPolicy& out,
                    std::vector<Refusal>& refusals) {
    const Json* recovery = parent.find("recovery");
    if (recovery == nullptr) {
        return true;
    }
    if (!recovery->is_object()) {
        refuse(refusals, scope, "TYPE", "field \"recovery\" must be an object");
        return false;
    }
    if (!only_allowed_keys(*recovery, kRecoveryKeys, scope, refusals)) {
        return false;
    }
    bool ok = true;
    long long number = 0;
    ok = optional_positive_int(*recovery, "max_attempts", scope, 1, 3, number, refusals) && ok;
    out.max_attempts = static_cast<int>(number);
    ok = optional_positive_int(*recovery, "window_seconds", scope, 1, 3600, number, refusals) && ok;
    out.window_seconds = static_cast<int>(number);

    if (const Json* backoff = recovery->find("backoff_seconds")) {
        ok = parse_int_list(*backoff, scope, "backoff_seconds", out.backoff_seconds, refusals) && ok;
    }
    if (const Json* verbs = recovery->find("allowed_verbs")) {
        ok = parse_string_list(*verbs, scope, "allowed_verbs", out.allowed_verbs, refusals) && ok;
        if (ok) {
            // An EMPTY list is legal and is not the same as an absent one: it
            // says "the Core may do nothing to this service", which is the
            // honest way to write a service that is observed but not managed
            // (`manual`/`disabled` entries in the example manifest). Refusing
            // it would force an author to list a verb they do not want.
            for (const std::string& verb : out.allowed_verbs) {
                if (verb != "start" && verb != "restart" && verb != "stop") {
                    refuse(refusals, scope, "VOCABULARY",
                           "recovery.allowed_verbs contains \"" + verb +
                               "\"; the verbs this version knows are start, restart and stop");
                    ok = false;
                }
            }
        }
    }
    if (const Json* circuit = recovery->find("circuit")) {
        if (!circuit->is_object()) {
            refuse(refusals, scope, "TYPE", "field \"recovery.circuit\" must be an object");
            return false;
        }
        ok = only_allowed_keys(*circuit, kCircuitKeys, scope, refusals) && ok;
        ok = optional_positive_int(*circuit, "consecutive_failures", scope, 1, 3, number,
                                   refusals) &&
             ok;
        out.circuit.consecutive_failures = static_cast<int>(number);
        ok = optional_positive_int(*circuit, "cooldown_seconds", scope, 0, 1800, number, refusals) &&
             ok;
        out.circuit.cooldown_seconds = static_cast<int>(number);
    }
    return ok;
}

bool parse_service(const Json& service, std::size_t index, ServiceDesired& out,
                   std::vector<Refusal>& refusals) {
    std::string scope = "services[" + std::to_string(index) + "]";
    if (!service.is_object()) {
        refuse(refusals, scope, "TYPE", "every entry of \"services\" must be an object");
        return false;
    }
    bool ok = only_allowed_keys(service, kServiceKeys, scope, refusals);
    ok = require_string(service, "id", scope, out.id, refusals) && ok;
    if (!out.id.empty()) {
        scope = out.id;
    }
    ok = require_string(service, "owner", scope, out.owner, refusals) && ok;
    ok = require_string(service, "unit", scope, out.unit, refusals) && ok;
    if (!out.unit.empty() && !valid_unit_id(out.unit)) {
        refuse(refusals, scope, "UNIT_ID",
               "\"" + out.unit +
                   "\" is not a systemd user unit id: an id is a name ending in \".service\" "
                   "whose characters are alphanumeric or one of _ . @ : - , so a path, a "
                   "traversal and a shell metacharacter are all unrepresentable");
        ok = false;
    }

    std::string desired_word;
    ok = require_string(service, "desired", scope, desired_word, refusals) && ok;
    if (!desired_word.empty()) {
        const std::optional<Desired> parsed = desired_from_word(desired_word);
        if (!parsed.has_value()) {
            refuse(refusals, scope, "VOCABULARY",
                   "desired is \"" + desired_word +
                       "\"; the words this version defines are running, manual and disabled");
            ok = false;
        } else {
            out.desired = *parsed;
        }
    }

    if (const Json* boot = service.find("start_on_boot")) {
        if (!boot->is_bool()) {
            refuse(refusals, scope, "TYPE", "field \"start_on_boot\" must be a boolean");
            ok = false;
        } else {
            out.start_on_boot = boot->boolean;
        }
    }
    if (const Json* deps = service.find("depends_on")) {
        ok = parse_string_list(*deps, scope, "depends_on", out.depends_on, refusals) && ok;
        for (const std::string& dependency : out.depends_on) {
            if (dependency == out.id && !out.id.empty()) {
                refuse(refusals, scope, "SELF_DEPENDENCY",
                       "\"" + out.id + "\" depends on itself");
                ok = false;
            }
        }
    }
    ok = parse_health(service, scope, out.health, refusals) && ok;
    ok = parse_recovery(service, scope, out.recovery, refusals) && ok;
    return ok;
}

}  // namespace

bool valid_unit_id(const std::string& unit) {
    // ".service" is 8 characters, so the shortest legal id has a one-character
    // stem. The upper bound is systemd's own UNIT_NAME_MAX (256) with room to
    // spare; a longer id is refused here rather than at the D-Bus call.
    if (unit.size() < 9 || unit.size() > 128) {
        return false;
    }
    if (unit.compare(unit.size() - 8, 8, ".service") != 0) {
        return false;
    }
    if (unit.find("..") != std::string::npos) {
        return false;
    }
    if (!std::isalnum(static_cast<unsigned char>(unit.front()))) {
        return false;
    }
    for (char c : unit) {
        const unsigned char byte = static_cast<unsigned char>(c);
        const bool allowed = std::isalnum(byte) || c == '_' || c == '.' || c == '@' || c == ':' ||
                             c == '-';
        if (!allowed) {
            return false;
        }
    }
    return true;
}

ParseOutcome parse_manifest(const std::string& text) {
    ParseOutcome outcome;
    outcome.manifest.sha256 = sha256_hex(text);

    Json document;
    std::string error;
    Reader reader(text);
    if (!reader.parse(document, error)) {
        refuse(outcome.refusals, "", "NOT_JSON", "the manifest is not JSON: " + error);
        return outcome;
    }
    if (!document.is_object()) {
        refuse(outcome.refusals, "", "TYPE", "a manifest is a JSON object");
        return outcome;
    }
    if (!only_allowed_keys(document, kTopKeys, "", outcome.refusals)) {
        return outcome;
    }

    bool ok = true;
    ok = require_string(document, "schema", "", outcome.manifest.schema, outcome.refusals) && ok;
    if (ok && outcome.manifest.schema != kDesiredStateSchema) {
        refuse(outcome.refusals, "", "SCHEMA",
               "schema is \"" + outcome.manifest.schema + "\"; this version reads \"" +
                   kDesiredStateSchema + "\"");
        return outcome;
    }
    ok = require_string(document, "node", "", outcome.manifest.node, outcome.refusals) && ok;
    ok = require_string(document, "principal", "", outcome.manifest.principal, outcome.refusals) &&
         ok;

    if (const Json* observe_only = document.find("observe_only")) {
        if (!observe_only->is_bool()) {
            refuse(outcome.refusals, "", "TYPE", "field \"observe_only\" must be a boolean");
            ok = false;
        } else {
            outcome.manifest.observe_only = observe_only->boolean;
        }
    }

    long long generation = 0;
    if (!optional_positive_int(document, "generation", "", 0, -1, generation, outcome.refusals)) {
        ok = false;
    } else if (generation < 0) {
        refuse(outcome.refusals, "", "MISSING", "field \"generation\" is required");
        ok = false;
    }
    outcome.manifest.generation = generation;

    const Json* services = document.find("services");
    if (services == nullptr) {
        refuse(outcome.refusals, "", "MISSING", "field \"services\" is required");
        return outcome;
    }
    if (!services->is_array()) {
        refuse(outcome.refusals, "", "TYPE", "field \"services\" must be an array");
        return outcome;
    }
    for (std::size_t i = 0; i < services->items.size(); ++i) {
        ServiceDesired service;
        ok = parse_service(services->items[i], i, service, outcome.refusals) && ok;
        outcome.manifest.services.push_back(std::move(service));
    }

    // Cross-service rules. Both of these are about the SET, so neither can be
    // decided while reading one entry.
    for (std::size_t i = 0; i < outcome.manifest.services.size(); ++i) {
        const ServiceDesired& service = outcome.manifest.services[i];
        for (std::size_t j = i + 1; j < outcome.manifest.services.size(); ++j) {
            if (!service.id.empty() && service.id == outcome.manifest.services[j].id) {
                refuse(outcome.refusals, service.id, "DUPLICATE_ID",
                       "two services share the id \"" + service.id +
                           "\"; the dependency graph is keyed by it, so one of them would be "
                           "unreachable");
                ok = false;
            }
        }
        for (const std::string& dependency : service.depends_on) {
            bool found = false;
            for (const ServiceDesired& candidate : outcome.manifest.services) {
                if (candidate.id == dependency) {
                    found = true;
                    break;
                }
            }
            if (!found) {
                refuse(outcome.refusals, service.id, "UNKNOWN_DEPENDENCY",
                       "\"" + service.id + "\" depends on \"" + dependency +
                           "\", which no service in this manifest declares");
                ok = false;
            }
        }
    }

    outcome.ok = ok && outcome.refusals.empty();
    return outcome;
}

}  // namespace nodecore::supervisor
