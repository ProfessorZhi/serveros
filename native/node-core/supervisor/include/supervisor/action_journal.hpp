// The durable journal at the effect boundary.
//
// WHY THIS EXISTS
// ---------------
// The acting loop used to call the provider FIRST and persist the budget only
// after the whole loop. A kill, crash, power loss or ENOSPC between the effect
// and that write left a real service action outside the persisted budget, so a
// relaunch could repeat it. Requiring `--state` is not enough if the write
// happens after the effect.
//
// THE CONTRACT
// ------------
//   * `prewrite_attempt` records the attempt in the SAME `Recovery` ledger the
//     planner consults and atomically replaces the state file (temp file +
//     fsync + rename + directory fsync) BEFORE the provider is invoked.
//   * If it returns false, the caller MUST NOT invoke the provider. There is no
//     effect without a durable attempt.
//   * `write_outcome` persists the post-effect ledger separately. A failure
//     leaves the already-durable attempt in place: the budget is conservatively
//     spent, the outcome is unknown, and the caller must not retry blindly.
//
// It owns no budget of its own. It never acquires the lease; the caller passes
// `lease_held` to `execute_effect`, which refuses before the prewrite when the
// writer does not hold the pen.
#pragma once

#include <functional>
#include <string>

#include "supervisor/provider.hpp"
#include "supervisor/recovery.hpp"
#include "supervisor/state_directory.hpp"

namespace nodecore::supervisor {

class ActionJournal {
public:
    //: `directory` is the already-opened, verified immediate parent of the state
    //: file. The journal writes only relative to that fd; it never re-resolves
    //: the path string.
    ActionJournal(StateDirectory directory, Recovery* recovery,
                  std::function<RecoveryClock()> clock);

    //: Record the attempt and persist it. False means the durable attempt does
    //: not exist, so the effect must not run.
    bool prewrite_attempt(const std::string& service_id, long now, std::string& error);

    //: Persist the ledger after the effect. False means the attempt is durable
    //: but the outcome is not; do not report success and do not retry blindly.
    bool write_outcome(std::string& error);

private:
    bool persist(std::string& error);

    StateDirectory directory_;
    Recovery* recovery_ = nullptr;
    std::function<RecoveryClock()> clock_;
};

//: What happened at the effect boundary. `ok` means the prewrite succeeded and
//: the provider was invoked; the provider's own answer is `provider`.
struct EffectExecution {
    bool ok = false;
    bool prewrite_failed = false;
    bool provider_called = false;
    ProviderResult provider;
    std::string error;
};

//: Prewrite the attempt, then invoke exactly one provider verb. The provider is
//: NEVER called when the prewrite fails, the lease is not held, or the verb is
//: not one this stage implements.
EffectExecution execute_effect(Provider& provider, ActionJournal& journal, bool lease_held,
                               const std::string& service_id, const std::string& unit,
                               const std::string& verb, long now);

}  // namespace nodecore::supervisor
