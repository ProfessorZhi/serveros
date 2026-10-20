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
// process, and so a receipt can name the refusal. The state-path check is the
// one part that touches the filesystem; it decides whether a path is a durable,
// exclusive-enough budget, not whether a particular write will succeed (the
// post-action write is the authority and exits 2 on failure).
#pragma once

#include <string>

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

//: The closed state-path vocabulary.
inline constexpr const char* kStatePathOk = "USABLE";
inline constexpr const char* kStatePathEmpty = "STATE_PATH_EMPTY";
inline constexpr const char* kStatePathNotAbsolute = "STATE_PATH_NOT_ABSOLUTE";
inline constexpr const char* kStatePathNotAFile = "STATE_PATH_NOT_A_FILE";
inline constexpr const char* kStatePathSymlink = "STATE_PATH_SYMLINK";
inline constexpr const char* kStatePathWrongOwner = "STATE_PATH_WRONG_OWNER";
inline constexpr const char* kStatePathGroupWritable = "STATE_PATH_GROUP_WRITABLE";
inline constexpr const char* kStatePathUnwritable = "STATE_PATH_UNWRITABLE";
inline constexpr const char* kStatePathUnusable = "STATE_PATH_UNUSABLE";
inline constexpr const char* kStateParentMissing = "STATE_PARENT_MISSING";
inline constexpr const char* kStateParentNotADirectory = "STATE_PARENT_NOT_A_DIRECTORY";
inline constexpr const char* kStateParentSymlink = "STATE_PARENT_SYMLINK";
inline constexpr const char* kStateParentWrongOwner = "STATE_PARENT_WRONG_OWNER";
inline constexpr const char* kStateParentGroupWritable = "STATE_PARENT_GROUP_WRITABLE";

struct StatePathVerdict {
    bool ok = false;
    std::string code;
    std::string reason;
};

//: Whether `path` is a state file this account can durably own:
//:   * absolute, with a non-empty basename;
//:   * its parent exists, is a real directory (not a symlink), is owned by this
//:     uid, and is not writable by group or other (otherwise the file can be
//:     replaced by rename);
//:   * if the file exists: a regular file, not a symlink, owned by this uid,
//:     not group/other writable, and openable O_RDWR;
//:   * if it does not exist: the parent is writable, so a first run can create
//:     it.
//:
//: The path is never created here. Creating an empty file at startup would make
//: a later truncation look like a legitimate first run, which is the budget
//: reset this product must never allow.
StatePathVerdict check_state_path(const std::string& path);

}  // namespace nodecore::supervisor
