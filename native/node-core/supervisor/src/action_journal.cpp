#include "supervisor/action_journal.hpp"

#include <string>
#include <utility>

namespace nodecore::supervisor {

ActionJournal::ActionJournal(StateDirectory directory, Recovery* recovery,
                             std::function<RecoveryClock()> clock)
    : directory_(std::move(directory)), recovery_(recovery), clock_(std::move(clock)) {}

bool ActionJournal::prewrite_attempt(const std::string& service_id, long now,
                                     std::string& error) {
    if (recovery_ == nullptr) {
        error = "the action journal has no recovery ledger";
        return false;
    }
    recovery_->record_attempt(service_id, now);
    return persist(error);
}

bool ActionJournal::write_outcome(std::string& error) { return persist(error); }

bool ActionJournal::persist(std::string& error) {
    if (recovery_ == nullptr) {
        error = "the action journal has no recovery ledger";
        return false;
    }
    if (!clock_) {
        error = "the action journal has no clock";
        return false;
    }
    if (!directory_.valid()) {
        error = "the action journal has no trusted state directory";
        return false;
    }
    const RecoveryClock clock = clock_();
    if (clock.boot_id.empty() || clock.wall_epoch <= 0 || clock.monotonic < 0) {
        error = "the boot/clock anchor is not usable";
        return false;
    }
    return directory_.write_atomically(recovery_->to_json(clock) + "\n", error);
}

EffectExecution execute_effect(Provider& provider, ActionJournal& journal, bool lease_held,
                               const std::string& service_id, const std::string& unit,
                               const std::string& verb, long now) {
    EffectExecution effect;
    if (!lease_held) {
        effect.error =
            "the writer lease is not held, so no effect may be issued or persisted";
        return effect;
    }
    if (verb != "start" && verb != "restart" && verb != "stop") {
        effect.error = "unsupported verb \"" + verb + "\"";
        return effect;
    }

    // THE ORDER THAT MATTERS: the attempt is durable BEFORE the side effect.
    std::string error;
    if (!journal.prewrite_attempt(service_id, now, error)) {
        effect.prewrite_failed = true;
        effect.error = error;
        return effect;
    }

    effect.provider_called = true;
    if (verb == "start") {
        effect.provider = provider.start(unit);
    } else if (verb == "restart") {
        effect.provider = provider.restart(unit);
    } else {
        effect.provider = provider.stop(unit);
    }
    effect.ok = true;
    return effect;
}

}  // namespace nodecore::supervisor
