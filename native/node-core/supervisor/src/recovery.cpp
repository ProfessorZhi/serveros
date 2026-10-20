#include "supervisor/recovery.hpp"

#include <algorithm>
#include <cstdint>
#include <string>
#include <utility>

#include "json_detail.hpp"
#include "nodecore/json.hpp"

namespace nodecore::supervisor {
namespace {

//: A persisted stamp older than this is outside any policy window and is
//: refused rather than converted, so a corrupted file cannot overflow the
//: cross-boot arithmetic. Ten years is far beyond any 60/300/900 s ladder.
constexpr long kMaxPersistedAgeSeconds = 10L * 365L * 24L * 3600L;

//: The two clocks have different sane ranges. These bounds keep the conversion
//: inside `long long` without trusting a value from the file.
constexpr long long kMaxMonotonicSeconds = 100LL * 365LL * 24LL * 3600LL;
constexpr long long kMaxWallEpoch = 253402300799LL;  // 9999-12-31T23:59:59Z

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

ActionOutcome outcome_for(bool provider_ok, Readiness readiness) {
    if (!provider_ok) {
        return ActionOutcome::NotRecovered;
    }
    switch (readiness) {
        case Readiness::Ready:
            return ActionOutcome::Recovered;
        case Readiness::NotReady:
            return ActionOutcome::NotRecovered;
        case Readiness::Unmeasured:
            return ActionOutcome::Unmeasured;
    }
    return ActionOutcome::Unmeasured;
}

void Recovery::record_outcome(const std::string& service_id, ActionOutcome outcome,
                              const RecoveryPolicy& policy, long now) {
    switch (outcome) {
        case ActionOutcome::Recovered:
            record_success(service_id);
            return;
        case ActionOutcome::NotRecovered:
            record_failure(service_id, policy, now);
            return;
        case ActionOutcome::Unmeasured:
            // Deliberately nothing. The attempt was recorded when it was made;
            // this verdict is "we could not look", which is not a failure.
            return;
    }
}

std::string Recovery::to_json(const RecoveryClock& clock) const {
    nodecore::Json root = nodecore::Json::object();
    root.set("schema", nodecore::Json::string(kRecoverySchemaV2));
    root.set("boot_id", nodecore::Json::string(clock.boot_id));
    nodecore::Json anchor = nodecore::Json::object();
    anchor.set("monotonic", nodecore::Json::integer(clock.monotonic));
    anchor.set("wall_epoch", nodecore::Json::integer(clock.wall_epoch));
    root.set("anchor", std::move(anchor));
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

RecoveryLoaded load_recovery(const std::string* text, const RecoveryClock& now) {
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
    if (schema == nullptr || !schema->is_string()) {
        loaded.error = "the persisted recovery state has no schema";
        return loaded;
    }
    if (schema->text == kRecoverySchemaV1) {
        loaded.legacy = true;
        loaded.was_present = true;
        loaded.error =
            "the persisted recovery state is " + std::string(kRecoverySchemaV1) +
            ": it has no boot id or clock anchor, so its monotonic stamps cannot be placed in "
            "a boot. Refusing to act rather than guessing, and refusing to migrate, consume or "
            "clear it automatically. Inspect or remove it deliberately";
        return loaded;
    }
    if (schema->text != kRecoverySchemaV2) {
        loaded.error = std::string("the persisted recovery state is not ") + kRecoverySchemaV2;
        return loaded;
    }

    // THE CURRENT EPOCH MUST BE ESTABLISHABLE. This is the fail-closed half:
    // without a boot id there is no domain in which a persisted monotonic
    // stamp can be placed, and guessing is the defect this format removes.
    if (now.boot_id.empty()) {
        loaded.error =
            "this run has no boot id, so a persisted monotonic stamp cannot be placed in a boot";
        return loaded;
    }
    if (now.wall_epoch <= 0 || now.wall_epoch > kMaxWallEpoch) {
        loaded.error =
            "this run has no usable wall-clock epoch, so a persisted state cannot be placed in time";
        return loaded;
    }
    if (now.monotonic < 0 || now.monotonic > kMaxMonotonicSeconds) {
        loaded.error = "this run has no usable monotonic clock";
        return loaded;
    }

    const detail::Json* boot = document.find("boot_id");
    if (boot == nullptr || !boot->is_string() || boot->text.empty()) {
        loaded.error = "the persisted recovery state has no boot_id";
        return loaded;
    }
    const detail::Json* anchor = document.find("anchor");
    if (anchor == nullptr || !anchor->is_object()) {
        loaded.error = "the persisted recovery state has no clock anchor";
        return loaded;
    }
    const detail::Json* anchor_mono = anchor->find("monotonic");
    const detail::Json* anchor_wall = anchor->find("wall_epoch");
    if (anchor_mono == nullptr || !anchor_mono->is_int() || anchor_wall == nullptr ||
        !anchor_wall->is_int()) {
        loaded.error = "the persisted recovery state's anchor is missing monotonic or wall_epoch";
        return loaded;
    }
    if (anchor_mono->integer < 0 || anchor_mono->integer > kMaxMonotonicSeconds) {
        loaded.error =
            "the persisted recovery state's monotonic anchor is outside the representable range";
        return loaded;
    }
    if (anchor_wall->integer <= 0 || anchor_wall->integer > kMaxWallEpoch) {
        loaded.error =
            "the persisted recovery state's wall-clock anchor is not a usable epoch";
        return loaded;
    }

    const bool same_boot = boot->text == now.boot_id;
    if (same_boot && anchor_mono->integer > now.monotonic) {
        loaded.error =
            "the persisted recovery state's monotonic anchor is in the future for this boot, "
            "so it was not written by this boot's clock; refusing to place it";
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
        if (failures->integer < 0 || failures->integer > 1000000) {
            loaded.error = "a persisted recovery row has an out-of-range consecutive_failures";
            return loaded;
        }
        const long long opened_value = opened->integer;
        if (opened_value < -static_cast<long long>(kMaxPersistedAgeSeconds) ||
            opened_value >
                anchor_mono->integer + static_cast<long long>(kMaxPersistedAgeSeconds)) {
            loaded.error =
                "a persisted recovery row's opened_until is outside the state's clock range";
            return loaded;
        }

        std::vector<long> stamps;
        for (const detail::Json& stamp : attempts->items) {
            if (!stamp.is_int()) {
                loaded.error = "a persisted recovery attempt is not an integer";
                return loaded;
            }
            if (stamp.integer > anchor_mono->integer) {
                loaded.error =
                    "a persisted recovery attempt is later than the state's own clock anchor";
                return loaded;
            }
            if (stamp.integer < -static_cast<long long>(kMaxPersistedAgeSeconds)) {
                loaded.error =
                    "a persisted recovery attempt is outside the state's bounded clock domain";
                return loaded;
            }
            if (same_boot) {
                // Within one boot the monotonic domain is the authority. A wall
                // clock step, in either direction, cannot move this stamp.
                stamps.push_back(static_cast<long>(stamp.integer));
                continue;
            }

            // A different boot: place the old steady instant on the wall-clock
            // timeline using the anchor written beside it, then place that wall
            // instant in THIS boot's steady domain.
            const long long stamp_wall =
                anchor_wall->integer - (anchor_mono->integer - stamp.integer);
            long long elapsed = now.wall_epoch - stamp_wall;
            if (elapsed < 0) {
                // A wall clock that stepped BACKWARDS must not age an attempt
                // out. Treating it as "now" preserves the budget; a negative
                // age would erase it, which is the expansion this fix forbids.
                elapsed = 0;
            }
            if (elapsed > static_cast<long long>(kMaxPersistedAgeSeconds)) {
                elapsed = kMaxPersistedAgeSeconds;
            }
            long long placed = static_cast<long long>(now.monotonic) - elapsed;
            if (placed < -static_cast<long long>(kMaxPersistedAgeSeconds)) {
                placed = -kMaxPersistedAgeSeconds;
            }
            stamps.push_back(static_cast<long>(placed));
        }

        long opened_until = 0;
        if (same_boot) {
            opened_until = static_cast<long>(opened_value);
        } else {
            const long long opened_wall =
                anchor_wall->integer + (opened_value - anchor_mono->integer);
            long long remaining = opened_wall - now.wall_epoch;
            if (remaining > static_cast<long long>(kMaxPersistedAgeSeconds)) {
                remaining = kMaxPersistedAgeSeconds;
            }
            if (remaining > 0) {
                opened_until = static_cast<long>(now.monotonic + remaining);
            } else {
                // The cooldown elapsed while the Core was not running. A
                // FORWARD wall step can produce this; it is the same reading as
                // waiting out the cooldown. A BACKWARD step cannot, because it
                // makes `remaining` larger, never smaller.
                opened_until = now.monotonic;
            }
        }
        state.restore(id->text, std::move(stamps), static_cast<int>(failures->integer),
                      opened_until);
    }
    loaded.ok = true;
    loaded.was_present = true;
    loaded.state = std::move(state);
    return loaded;
}

}  // namespace nodecore::supervisor
