#include "supervisor/facts.hpp"

#include <set>
#include <utility>

#include "json_detail.hpp"

namespace nodecore::supervisor {
namespace {

using detail::Json;

//: Every string this module will carry into a receipt is bounded here rather
//: than at the receipt, so a writer cannot grow the journal one field at a time.
constexpr std::size_t kMaxFactText = 200;
constexpr std::size_t kMaxServices = 64;

void fail(HostFacts& facts, const char* code, std::string reason) {
    facts.usable = false;
    facts.code = code;
    facts.reason = std::move(reason);
    // The whole document is disbelieved together. A partly-trusted fact set is
    // how a stale link fact outlives the stale agent fact beside it.
    facts.link = LinkFacts{};
    facts.agent = AgentFacts{};
    facts.services.clear();
}

bool allowed_only(const Json& object, std::initializer_list<const char*> allowed,
                  const std::string& scope, std::string& error) {
    for (const auto& member : object.members) {
        bool known = false;
        for (const char* candidate : allowed) {
            if (member.first == candidate) {
                known = true;
                break;
            }
        }
        if (!known) {
            error = scope + ": unknown field \"" + member.first +
                    "\" (this reader refuses rather than ignoring it)";
            return false;
        }
    }
    return true;
}

bool bounded_text(const Json& value, const std::string& scope, std::string& out,
                  std::string& error) {
    if (!value.is_string()) {
        error = scope + ": expected a string";
        return false;
    }
    if (value.text.size() > kMaxFactText) {
        error = scope + ": text exceeds " + std::to_string(kMaxFactText) + " bytes";
        return false;
    }
    out = value.text;
    return true;
}

bool required_bool(const Json& object, const char* key, const std::string& scope, bool& out,
                   std::string& error) {
    const Json* found = object.find(key);
    if (found == nullptr) {
        error = scope + ": missing \"" + key + "\"";
        return false;
    }
    if (!found->is_bool()) {
        error = scope + ": \"" + key + "\" must be true or false";
        return false;
    }
    out = found->boolean;
    return true;
}

bool parse_link(const Json& value, LinkFacts& out, std::string& error) {
    if (!value.is_object()) {
        error = "link: expected an object";
        return false;
    }
    if (!allowed_only(value, {"known", "up", "reason"}, "link", error)) {
        return false;
    }
    if (!required_bool(value, "known", "link", out.known, error)) {
        return false;
    }
    if (!required_bool(value, "up", "link", out.up, error)) {
        return false;
    }
    const Json* reason = value.find("reason");
    if (reason != nullptr && !bounded_text(*reason, "link.reason", out.reason, error)) {
        return false;
    }
    // `known = false, up = true` is a contradiction, not a permissive default:
    // a writer that cannot tell us whether it looked is not telling us it is up.
    if (!out.known && out.up) {
        error = "link: \"up\" is true while \"known\" is false";
        return false;
    }
    return true;
}

bool parse_agent(const Json& value, AgentFacts& out, std::string& error) {
    if (!value.is_object()) {
        error = "agent: expected an object";
        return false;
    }
    if (!allowed_only(value, {"known", "busy", "holder"}, "agent", error)) {
        return false;
    }
    if (!required_bool(value, "known", "agent", out.known, error)) {
        return false;
    }
    if (!required_bool(value, "busy", "agent", out.busy, error)) {
        return false;
    }
    const Json* holder = value.find("holder");
    if (holder != nullptr && !bounded_text(*holder, "agent.holder", out.holder, error)) {
        return false;
    }
    if (!out.known && out.busy) {
        error = "agent: \"busy\" is true while \"known\" is false";
        return false;
    }
    return true;
}

bool parse_service(const Json& value, ServiceFact& out, std::string& error) {
    if (!value.is_object()) {
        error = "services[]: expected an object";
        return false;
    }
    if (!allowed_only(value, {"id", "runtime", "maintenance", "maintenance_reason"}, "services[]",
                      error)) {
        return false;
    }
    const Json* id = value.find("id");
    if (id == nullptr || !bounded_text(*id, "services[].id", out.id, error) || out.id.empty()) {
        if (error.empty()) {
            error = "services[]: \"id\" is required and must be non-empty";
        }
        return false;
    }
    const Json* runtime = value.find("runtime");
    if (runtime == nullptr || !runtime->is_string()) {
        error = "services[" + out.id + "]: \"runtime\" must be a string";
        return false;
    }
    if (runtime->text == "serving") {
        out.runtime.known = true;
        out.runtime.needs_login = false;
    } else if (runtime->text == "needs-login") {
        out.runtime.known = true;
        out.runtime.needs_login = true;
        out.runtime.detail = "NeedsLogin";
    } else if (runtime->text == "unknown") {
        out.runtime = ServiceRuntime{};
    } else {
        error = "services[" + out.id + "]: \"runtime\" is serving, needs-login or unknown";
        return false;
    }
    const Json* maintenance = value.find("maintenance");
    if (maintenance != nullptr) {
        if (!maintenance->is_bool()) {
            error = "services[" + out.id + "]: \"maintenance\" must be true or false";
            return false;
        }
        out.maintenance = maintenance->boolean;
    }
    const Json* reason = value.find("maintenance_reason");
    if (reason != nullptr &&
        !bounded_text(*reason, "services[" + out.id + "].maintenance_reason",
                      out.maintenance_reason, error)) {
        return false;
    }
    return true;
}

}  // namespace

const ServiceFact* HostFacts::service(const std::string& id) const {
    for (const ServiceFact& fact : services) {
        if (fact.id == id) {
            return &fact;
        }
    }
    return nullptr;
}

const std::string* OwnerMap::owner_of(const std::string& unit) const {
    for (const auto& entry : owners) {
        if (entry.first == unit) {
            return &entry.second;
        }
    }
    return nullptr;
}

OwnerMap load_owner_map(const char* text) {
    OwnerMap map;
    if (text == nullptr) {
        map.code = kFactsAbsent;
        map.reason = "no owner map was readable; every unit's owner stays unverified";
        return map;
    }
    const std::string document(text);
    Json root;
    std::string error;
    detail::Reader reader(document);
    if (!reader.parse(root, error) || !root.is_object()) {
        map.code = kFactsMalformed;
        map.reason = "owner map is not a JSON object: " + error;
        return map;
    }
    if (!allowed_only(root, {"schema", "units"}, "owner map", error)) {
        map.code = kFactsMalformed;
        map.reason = error;
        return map;
    }
    const Json* schema = root.find("schema");
    if (schema == nullptr || !schema->is_string() || schema->text != kOwnerMapSchema) {
        map.code = kFactsBadSchema;
        map.reason = std::string("owner map does not name schema ") + kOwnerMapSchema;
        return map;
    }
    const Json* units = root.find("units");
    if (units == nullptr || !units->is_object()) {
        map.code = kFactsMalformed;
        map.reason = "owner map has no \"units\" object";
        return map;
    }
    // Parsed into a LOCAL, and only published to `map` once the whole document
    // is accepted. `owner_of` answers from `owners` without consulting `usable`,
    // so a refused map that still carried a prefix would answer questions while
    // claiming to be unusable — two statements about one document, and a caller
    // that asked the wrong one would act on a map the reader disbelieved.
    std::vector<std::pair<std::string, std::string>> owners;
    for (const auto& member : units->members) {
        const std::string& unit = member.first;
        const Json& owner = member.second;
        if (unit.empty()) {
            map.code = kFactsMalformed;
            map.reason = "owner map names an empty unit";
            return map;
        }
        // The Python READER's two bounds, restated. An unbounded map is not a
        // bigger statement about this host; it is a document `OwnerMap.load`
        // refuses, and a map one reader disbelieves has no authority behind the
        // units the other one would act on.
        if (unit.size() > kMaxOwnerUnitBytes) {
            map.code = kFactsMalformed;
            map.reason = "owner map names a unit longer than " +
                         std::to_string(kMaxOwnerUnitBytes) + " bytes";
            return map;
        }
        if (owners.size() >= kMaxOwnerEntries) {
            map.code = kFactsMalformed;
            map.reason = "owner map names more than " + std::to_string(kMaxOwnerEntries) +
                         " units";
            return map;
        }
        if (!owner.is_string() || (owner.text != kNativeCoreOwner && owner.text != "serverosd")) {
            // A closed vocabulary: an owner this Core does not know is not a
            // reason to guess which program should act.
            map.code = kFactsMalformed;
            map.reason = "owner map names an unknown owner for \"" + unit + "\"";
            return map;
        }
        owners.emplace_back(unit, owner.text);
    }
    map.owners = std::move(owners);
    map.usable = true;
    map.code = kFactsOk;
    return map;
}

std::vector<OwnerRefusal> manifest_owner_refusals(const Manifest& manifest,
                                                  const OwnerMap& owners) {
    std::vector<OwnerRefusal> refusals;
    // An unusable map is not "nobody objected". Every unit becomes a refusal,
    // because the map is the only fact that says a unit is this Core's, and a
    // map the reader disbelieved says nothing at all. The caller has already
    // stopped on the map's own reason; this keeps a direct caller from reaching
    // the opposite conclusion by asking a second question.
    if (!owners.usable) {
        for (const ServiceDesired& service : manifest.services) {
            refusals.push_back(OwnerRefusal{service.unit, std::string()});
        }
        return refusals;
    }
    for (const ServiceDesired& service : manifest.services) {
        const std::string* owner = owners.owner_of(service.unit);
        if (owner == nullptr || *owner != kNativeCoreOwner) {
            refusals.push_back(OwnerRefusal{
                service.unit, owner == nullptr ? std::string() : *owner});
        }
    }
    return refusals;
}

HostFacts load_host_facts(const char* text, long now_epoch, const FactsOptions& options) {
    HostFacts facts;
    if (text == nullptr) {
        fail(facts, kFactsAbsent,
             "no facts document was readable; every link and agent fact stays unknown");
        return facts;
    }

    // The reader holds a REFERENCE to the text it parses, so the string must
    // outlive it. Building one inline would bind a temporary and leave the
    // reader reading freed bytes.
    const std::string document(text);
    Json root;
    std::string error;
    detail::Reader reader(document);
    if (!reader.parse(root, error)) {
        fail(facts, kFactsMalformed, "facts document is not valid JSON: " + error);
        return facts;
    }
    if (!root.is_object()) {
        fail(facts, kFactsMalformed, "facts document is not an object");
        return facts;
    }
    if (!allowed_only(root,
                      {"schema", "observed_at", "observed_epoch", "link", "agent", "services"},
                      "facts", error)) {
        fail(facts, kFactsMalformed, error);
        return facts;
    }

    const Json* schema = root.find("schema");
    if (schema == nullptr || !schema->is_string() || schema->text != kFactsSchema) {
        fail(facts, kFactsBadSchema,
             std::string("facts document does not name schema ") + kFactsSchema);
        return facts;
    }
    const Json* observed_at = root.find("observed_at");
    if (observed_at == nullptr ||
        !bounded_text(*observed_at, "observed_at", facts.observed_at, error) ||
        facts.observed_at.empty()) {
        fail(facts, kFactsMalformed,
             error.empty() ? "observed_at is required" : error);
        return facts;
    }
    const Json* observed_epoch = root.find("observed_epoch");
    if (observed_epoch == nullptr || !observed_epoch->is_int() || observed_epoch->integer < 0) {
        fail(facts, kFactsMalformed,
             "observed_epoch is required and must be a non-negative integer");
        return facts;
    }

    // Age is computed against an INJECTED clock so a test states an instant.
    const long long written = observed_epoch->integer;
    const long long age = static_cast<long long>(now_epoch) - written;
    facts.age_seconds = static_cast<long>(age);
    if (age < -static_cast<long long>(options.future_skew_seconds)) {
        fail(facts, kFactsFromTheFuture,
             "facts document is stamped " + std::to_string(-age) +
                 "s in the future; the writer's clock is not this host's");
        return facts;
    }
    if (age > static_cast<long long>(options.max_age_seconds)) {
        fail(facts, kFactsStale,
             "facts document is " + std::to_string(age) + "s old (limit " +
                 std::to_string(options.max_age_seconds) +
                 "s); a stopped writer cannot keep authorising actions");
        return facts;
    }

    const Json* link = root.find("link");
    if (link == nullptr || !parse_link(*link, facts.link, error)) {
        fail(facts, kFactsMalformed, error.empty() ? "link is required" : error);
        return facts;
    }
    const Json* agent = root.find("agent");
    if (agent == nullptr || !parse_agent(*agent, facts.agent, error)) {
        fail(facts, kFactsMalformed, error.empty() ? "agent is required" : error);
        return facts;
    }
    const Json* services = root.find("services");
    if (services == nullptr || !services->is_array()) {
        fail(facts, kFactsMalformed, "services is required and must be an array");
        return facts;
    }
    if (services->items.size() > kMaxServices) {
        fail(facts, kFactsMalformed,
             "services lists more than " + std::to_string(kMaxServices) + " entries");
        return facts;
    }

    std::set<std::string> seen;
    for (const Json& item : services->items) {
        ServiceFact fact;
        if (!parse_service(item, fact, error)) {
            fail(facts, kFactsMalformed, error);
            return facts;
        }
        if (!seen.insert(fact.id).second) {
            // A document that names one service twice leaves its runtime
            // undecidable, and "first one wins" is a rule nobody wrote down.
            fail(facts, kFactsMalformed,
                 "services names \"" + fact.id + "\" more than once");
            return facts;
        }
        facts.services.push_back(std::move(fact));
    }

    facts.usable = true;
    facts.code = kFactsOk;
    facts.reason.clear();
    return facts;
}

}  // namespace nodecore::supervisor
