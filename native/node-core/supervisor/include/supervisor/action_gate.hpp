// The gate in front of a side effect: may this process ACT at all, and is the
// budget it will spend actually persistable?
//
// WHY IT IS A SEPARATE, PURE UNIT
// -------------------------------
// `main.cpp` used to inline the lease/link/agent checks and never check
// `--state`. Inlining made the one missing fact invisible: a caller could enter
// the acting path with no persisted budget, so every process restart and every
// reboot began with a fresh three attempts. The repeated-restart cap was
// bypassed by omission, not by a decision.
//
// The rules are here as a closed vocabulary so a test can state them without a
// process, and so a receipt can name the refusal. The state-path half lives in
// `state_directory.hpp`: it opens the immediate parent as a directory fd and
// binds the writer to that inode, rather than checking a string and writing it
// later.
#pragma once

#include <string>

#include "supervisor/state_directory.hpp"

namespace nodecore::supervisor {

//: The closed action-gate vocabulary. `ADMITTED` is the only ok value.
inline constexpr const char* kActionGateOk = "ADMITTED";
inline constexpr const char* kActionGateNeedsLease = "LEASE_REQUIRED";
inline constexpr const char* kActionGateNeedsLink = "LINK_STATE_REQUIRED";
inline constexpr const char* kActionGateNeedsAgent = "AGENT_STATE_REQUIRED";
inline constexpr const char* kActionGateNeedsState = "STATE_REQUIRED";
inline constexpr const char* kActionGateStateNotAbsolute = "STATE_PATH_NOT_ABSOLUTE";

struct ActionGateInput {
    bool act = false;
    bool have_lease = false;
    bool link_known = false;
    bool agent_known = false;
    //: The `--state` path. Must be non-empty and absolute when `act` is true.
    std::string state_path;
};

struct ActionGateVerdict {
    bool ok = false;
    std::string code;
    std::string reason;
};

//: Whether the acting flags are complete. Pure: no filesystem, no clock.
ActionGateVerdict check_action_gate(const ActionGateInput& input);

}  // namespace nodecore::supervisor
