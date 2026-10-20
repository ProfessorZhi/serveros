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
//   * the persisted form NAMES ITS BOOT AND BOTH CLOCKS. A monotonic stamp
//     from another boot is not a duration, so `contract/recovery-state.v2.md`
//     carries the boot id and a `{monotonic, wall_epoch}` anchor; the budget
//     itself stays in steady-clock seconds.
#pragma once

#include <map>
#include <string>
#include <vector>

#include "supervisor/admission.hpp"
#include "supervisor/model.hpp"

namespace nodecore::supervisor {

//: The persisted budget's schema. v2 carries the boot id and the clock anchor
//: that v1 lacked; v1 is recognised only so the loader can refuse it rather
//: than guess which boot its monotonic stamps belong to.
inline constexpr const char* kRecoverySchemaV2 = "serveros/native-core-recovery.v2";
inline constexpr const char* kRecoverySchemaV1 = "serveros/native-core-recovery.v1";

struct RecoveryDecision {
    bool allowed = false;
    // ALLOWED / BACKOFF / BUDGET_EXHAUSTED / CIRCUIT_OPEN. A caller switches on
    // this rather than on `allowed`, because "wait 40 more seconds" and "stop
    // trying and tell a person" are different instructions that share a false.
    std::string code;
    std::string reason;
    long retry_after_seconds = 0;
};

//: What an executed verb taught the budget.
//:
//: The third value is the one this type exists for. A probe that could not be
//: attempted is not a failed recovery: the ATTEMPT was made and stands, but
//: "we could not look" must neither clear the breaker nor advance it. Mapping
//: that outcome onto a bool is how a transient inability to look trips a
//: breaker on a service that is fine.
enum class ActionOutcome {
    Recovered,
    NotRecovered,
    Unmeasured,
};

//: The bridge from the readiness rule to the budget. A provider failure is a
//: failed recovery whatever a probe might say; otherwise `Ready` is a recovery,
//: `NotReady` is a failure, and `Unmeasured` is neither.
ActionOutcome outcome_for(bool provider_ok, Readiness readiness);

//: The two clocks a persisted budget needs, plus the boot that owns the
//: monotonic one. The caller supplies this; the class never reads a clock, so a
//: decision stays reproducible and a test can state a reboot without one.
//:
//: `monotonic` is the same steady-clock domain `record_attempt` and
//: `record_failure` already use. It resets at boot and is NOT comparable across
//: boots. `wall_epoch` is the only bridge between boots, and it is never used
//: as a duration. `boot_id` says which boot owns `monotonic`; empty means the
//: caller could not establish it, which refuses a persisted state.
struct RecoveryClock {
    long monotonic = 0;
    long long wall_epoch = 0;
    std::string boot_id;
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
    // Applies one executed verb's outcome. `Unmeasured` is deliberately a
    // no-op: the attempt was already recorded, but an unreadable probe is not
    // evidence that the service failed, so it neither clears nor advances the
    // breaker.
    void record_outcome(const std::string& service_id, ActionOutcome outcome,
                        const RecoveryPolicy& policy, long now);

    std::vector<long> attempts(const std::string& service_id) const;
    int consecutive_failures(const std::string& service_id) const;
    bool circuit_open(const std::string& service_id, long now) const;

    // The persisted form. Contains NO key, token or credential: this program
    // holds none, and a receipt or a state file that carried one would be the
    // leak the acceptance list forbids.
    //:
    //: `clock` is written beside the rows so a later load can place this
    //: boot's monotonic stamps on the wall-clock timeline. The rows themselves
    //: stay in monotonic seconds; the anchor is the only wall-clock value in
    //: the document.
    std::string to_json(const RecoveryClock& clock) const;

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
    // True when the file is the v1 format. It has no boot id or clock anchor,
    // so its monotonic stamps cannot be placed in a boot. The caller refuses to
    // act; the file is left to a person, never migrated or cleared automatically.
    bool legacy = false;
    Recovery state;
    std::string error;
};

// `text` is nullptr when no state file exists. `now` must be establishable:
// an empty boot id, a non-positive wall epoch, or a negative monotonic clock
// refuses a present state rather than placing it in a guessed domain.
RecoveryLoaded load_recovery(const std::string* text, const RecoveryClock& now);

}  // namespace nodecore::supervisor
