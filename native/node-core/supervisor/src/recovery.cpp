#include "supervisor/recovery.hpp"

#include <algorithm>
#include <cstdint>
#include <string>
#include <utility>

#include "json_detail.hpp"
#include "nodecore/json.hpp"

namespace nodecore::supervisor {
namespace {

constexpr const char* kRecoverySchema = "serveros/native-core-recovery.v1";

// The attempts still inside the window. Pruned on read rather than on write,
// so a resource that failed three times last month is not permanently barred
// and no sweep has to run for that to be true.
std::vector<long> windowed(const std::vector<long>& attempts, int window_seconds, long now) {
    std::vector<long> kept;
    for (long stamp : attempts) {
        if (now - stamp < static_cast<long>(window_seconds)) {
            kept.push_back(stamp);
        }
    }
    return kept;
}

}  // namespace

std::vector<long> Recovery::attempts(const std::string& service_id) const {
    const auto found = entries_.find(service_id);
    return found == entries_.end() ? std::vector<long>() : found->second.attempts;
}

int Recovery::consecutive_failures(const std::string& service_id) const {
    const auto found = entries_.find(service_id);
    return found == entries_.end() ? 0 : found->second.consecutive_failures;
}

bool Recovery::circuit_open(const std::string& service_id, long now) const {
    const auto found = entries_.find(service_id);
    return found != entries_.end() && found->second.opened_until > now;
}

RecoveryDecision Recovery::may_act(const std::string& service_id, const RecoveryPolicy& policy,
                                   long now) const {
    RecoveryDecision decision;
    const auto found = entries_.find(service_id);
    if (found == entries_.end()) {
        decision.allowed = true;
        decision.code = "ALLOWED";
        return decision;
    }
    const Entry& entry = found->second;

    // The breaker is checked FIRST, before the budget. Its answer is the
    // stronger one — "stop, a person is needed" rather than "wait a bit" — and
    // a service that is both over budget and tripped should not be told to
    // retry in eight minutes by a rule that was asked second.
    if (entry.opened_until > now) {
        decision.code = "CIRCUIT_OPEN";
        decision.retry_after_seconds = entry.opened_until - now;
        decision.reason = "the breaker is open: " + std::to_string(entry.consecutive_failures) +
                          " consecutive failures, cooling down for " +
                          std::to_string(decision.retry_after_seconds) +
                          " more seconds. A service that has failed this many times in a row is "
                          "not one more restart away from working";
        return decision;
    }

    const std::vector<long> in_window = windowed(entry.attempts, policy.window_seconds, now);
    if (static_cast<int>(in_window.size()) >= policy.max_attempts) {
        const long oldest = *std::min_element(in_window.begin(), in_window.end());
        decision.code = "BUDGET_EXHAUSTED";
        decision.retry_after_seconds = static_cast<long>(policy.window_seconds) - (now - oldest);
        decision.reason = std::to_string(in_window.size()) + " attempts within " +
                          std::to_string(policy.window_seconds) + "s and this service permits " +
                          std::to_string(policy.max_attempts) +
                          ". One more would not be a new attempt at a fix, it would be the loop";
        return decision;
    }

    if (!in_window.empty() && !policy.backoff_seconds.empty()) {
        const long last = *std::max_element(in_window.begin(), in_window.end());
        // The ladder is indexed by how many attempts have already happened, so
        // the FIRST retry waits backoff[0]. Indexing by the count still to come
        // would skip the shortest interval, which is the one that matters most
        // because it runs while somebody is still looking at the screen.
        const std::size_t index = std::min(in_window.size() - 1, policy.backoff_seconds.size() - 1);
        const long wait = policy.backoff_seconds[index];
        if (now - last < wait) {
            decision.code = "BACKOFF";
            decision.retry_after_seconds = wait - (now - last);
            decision.reason = "the backoff ladder waits " + std::to_string(wait) +
                              "s after attempt " + std::to_string(in_window.size()) +
                              "; " + std::to_string(decision.retry_after_seconds) + "s left";
            return decision;
        }
    }

    decision.allowed = true;
    decision.code = "ALLOWED";
    return decision;
}

void Recovery::record_attempt(const std::string& service_id, long now) {
    entries_[service_id].attempts.push_back(now);
}

void Recovery::record_success(const std::string& service_id) {
    Entry& entry = entries_[service_id];
    entry.consecutive_failures = 0;
    entry.opened_until = 0;
}

void Recovery::record_failure(const std::string& service_id, const RecoveryPolicy& policy,
                              long now) {
    Entry& entry = entries_[service_id];
    entry.consecutive_failures += 1;
    if (entry.consecutive_failures >= policy.circuit.consecutive_failures) {
        entry.opened_until = now + static_cast<long>(policy.circuit.cooldown_seconds);
    }
}

std::string Recovery::to_json() const {
    nodecore::Json root = nodecore::Json::object();
    root.set("schema", nodecore::Json::string(kRecoverySchema));
    nodecore::Json services = nodecore::Json::array();
    for (const auto& row : entries_) {
        nodecore::Json entry = nodecore::Json::object();
        entry.set("id", nodecore::Json::string(row.first));
        entry.set("consecutive_failures", nodecore::Json::integer(row.second.consecutive_failures));
        entry.set("opened_until", nodecore::Json::integer(row.second.opened_until));
        nodecore::Json stamps = nodecore::Json::array();
        for (long stamp : row.second.attempts) {
            stamps.push(nodecore::Json::integer(stamp));
        }
        entry.set("attempts", std::move(stamps));
        services.push(std::move(entry));
    }
    root.set("services", std::move(services));
    return root.dump();
}

void Recovery::restore(const std::string& service_id, std::vector<long> attempts,
                       int consecutive_failures, long opened_until) {
    Entry& entry = entries_[service_id];
    entry.attempts = std::move(attempts);
    entry.consecutive_failures = consecutive_failures;
    entry.opened_until = opened_until;
}

RecoveryLoaded load_recovery(const std::string* text) {
    RecoveryLoaded loaded;
    if (text == nullptr) {
        // No file is a first run. It is NOT an error, and it is NOT the same
        // answer as a file that could not be read; `was_present` says which.
        loaded.ok = true;
        loaded.was_present = false;
        return loaded;
    }

    detail::Json document;
    std::string error;
    detail::Reader reader(*text);
    if (!reader.parse(document, error)) {
        loaded.error = "the persisted recovery state is not JSON: " + error;
        return loaded;
    }
    if (!document.is_object()) {
        loaded.error = "the persisted recovery state is not an object";
        return loaded;
    }
    const detail::Json* schema = document.find("schema");
    if (schema == nullptr || !schema->is_string() || schema->text != kRecoverySchema) {
        loaded.error = std::string("the persisted recovery state is not ") + kRecoverySchema;
        return loaded;
    }
    const detail::Json* services = document.find("services");
    if (services == nullptr || !services->is_array()) {
        loaded.error = "the persisted recovery state has no services array";
        return loaded;
    }

    Recovery state;
    for (const detail::Json& row : services->items) {
        if (!row.is_object()) {
            loaded.error = "a persisted recovery row is not an object";
            return loaded;
        }
        const detail::Json* id = row.find("id");
        const detail::Json* attempts = row.find("attempts");
        const detail::Json* failures = row.find("consecutive_failures");
        const detail::Json* opened = row.find("opened_until");
        if (id == nullptr || !id->is_string() || id->text.empty() || attempts == nullptr ||
            !attempts->is_array() || failures == nullptr || !failures->is_int() || opened == nullptr ||
            !opened->is_int()) {
            loaded.error = "a persisted recovery row is missing a field or has the wrong type";
            return loaded;
        }
        std::vector<long> stamps;
        for (const detail::Json& stamp : attempts->items) {
            if (!stamp.is_int()) {
                loaded.error = "a persisted recovery attempt is not an integer";
                return loaded;
            }
            stamps.push_back(static_cast<long>(stamp.integer));
        }
        state.restore(id->text, std::move(stamps), static_cast<int>(failures->integer),
                      static_cast<long>(opened->integer));
    }
    loaded.ok = true;
    loaded.was_present = true;
    loaded.state = std::move(state);
    return loaded;
}

}  // namespace nodecore::supervisor
