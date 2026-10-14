// The failure budget, the backoff ladder and the circuit breaker.
//
// WHY THIS IS THE LOAD-BEARING PART
// ---------------------------------
// Everything else in this component decides WHAT should happen. This decides
// whether it is allowed to happen again, and it is the half that a control
// plane gets wrong: "restart it again" is not a recovery strategy, it is how
// the original incident stayed invisible for 54 minutes
// (`runtime/daemon.py`'s §19 note, whose numbers this mirrors: three attempts
// per hour, a 60/300/900 s ladder).
//
// Two properties it must have, and both are asserted:
//
//   * it is PERSISTED. A budget that resets when the supervisor restarts is
//     not a budget — `Restart=on-failure` on the Core's own unit would grant
//     it a fresh three attempts on every crash, which is the original bug
//     wearing a different hat. So `to_json` writes it and `load` reads it.
//   * it is CLOCK-INJECTED. Every method takes `now`. A test states an instant
//     rather than sleeping, and a decision that depends on when it was asked
//     is a decision nobody can reproduce.
#pragma once

#include <map>
#include <string>
#include <vector>

#include "supervisor/model.hpp"

namespace nodecore::supervisor {

struct RecoveryDecision {
    bool allowed = false;
    // ALLOWED / BACKOFF / BUDGET_EXHAUSTED / CIRCUIT_OPEN. A caller switches on
    // this rather than on `allowed`, because "wait 40 more seconds" and "stop
    // trying and tell a person" are different instructions that share a false.
    std::string code;
    std::string reason;
    long retry_after_seconds = 0;
};

class Recovery {
public:
    // Whether `service_id` may be acted on at `now`, under `policy`.
    RecoveryDecision may_act(const std::string& service_id, const RecoveryPolicy& policy,
                             long now) const;

    // An attempt is recorded when one is MADE, not when it succeeds — the
    // budget bounds attempts, and an attempt that failed is exactly the one
    // that must be counted.
    void record_attempt(const std::string& service_id, long now);
    // A success clears the BREAKER and nothing else. The window's attempts
    // stand: a service that failed twice and then worked is still a service
    // that needed two attempts this hour, and forgetting them would let a
    // flapping service look healthy at every restart.
    void record_success(const std::string& service_id);
    // Increments the consecutive-failure count and, at the policy's threshold,
    // opens the breaker until `now + cooldown_seconds`.
    void record_failure(const std::string& service_id, const RecoveryPolicy& policy, long now);

    std::vector<long> attempts(const std::string& service_id) const;
    int consecutive_failures(const std::string& service_id) const;
    bool circuit_open(const std::string& service_id, long now) const;

    // The persisted form. Contains NO key, token or credential: this program
    // holds none, and a receipt or a state file that carried one would be the
    // leak the acceptance list forbids.
    std::string to_json() const;

    // One row of persisted state, put back. Public because the loader is a free
    // function: a nested `Loaded` holding a `Recovery` by value cannot be
    // declared inside the class it is trying to contain.
    void restore(const std::string& service_id, std::vector<long> attempts,
                 int consecutive_failures, long opened_until);

private:
    struct Entry {
        std::vector<long> attempts;
        int consecutive_failures = 0;
        long opened_until = 0;
    };
    std::map<std::string, Entry> entries_;
};

struct RecoveryLoaded {
    bool ok = false;
    // False when there was no state file at all, which is a legitimate first
    // run. A caller that cannot tell that from a CORRUPT file would read "I
    // could not read the budget" as "the budget is empty" — the permissive
    // reading of an unknown, which is the one thing this product never does.
    bool was_present = false;
    Recovery state;
    std::string error;
};

// `text` is nullptr when no state file exists.
RecoveryLoaded load_recovery(const std::string* text);

}  // namespace nodecore::supervisor
