// The status document: what a resident Core will say about itself.
//
// WHY IT IS A PURE FUNCTION OF A SNAPSHOT
// ---------------------------------------
// The document is built from a value, not from the running loop. That is what
// lets a test assert exactly what a status answer contains without a socket, a
// daemon or a clock — and it is what makes "the answer cannot be computed
// against a half-updated observation set" a property of the shape rather than a
// promise about locking: a request is answered from the snapshot the last tick
// finished with, and a tick that has not finished has not replaced it.
//
// WHAT IT MUST NOT CARRY
// ----------------------
// No argv, no executable path, no manifest path, no token and no key. This is a
// document an operator reads over a socket and pastes into a chat; the Core
// holds no secret, and the one thing worse than not having one is printing the
// command line that might name where one lives.
//
// AN UNKNOWN IS NOT A ZERO
// ------------------------
// A service the last tick could not read is `"measured": false` with
// `"active": "unknown"` and the reason. Every numeric-looking field is either
// the number that was measured or absent — never 0, which is a claim the host
// cannot support.
#pragma once

#include <string>
#include <vector>

#include "supervisor/model.hpp"

namespace nodecore::supervisor {

inline constexpr const char* kStatusSchema = "serveros/native-core-status.v1";

// One service as the loop last saw it.
struct StatusNotice {
    std::string id;
    std::string unit;
    std::string desired;  // the word from the manifest
    // False means nothing was read; `active` is "unknown" and `reason` says why.
    bool measured = false;
    std::string active;
    std::string sub_state;
    std::string observed_at;
    long age_seconds = -1;
    std::string reason;
};

struct StatusSnapshot {
    // "observe-only". Written down rather than inferred, because a client that
    // had to guess whether the Core is willing to act would guess wrong.
    std::string mode = "observe-only";
    std::string node;
    std::string principal;
    long pid = 0;
    std::string started_at;
    long uptime_seconds = -1;
    long ticks = 0;
    long tick_errors = 0;
    long last_tick_monotonic = -1;
    long last_tick_duration_seconds = -1;
    long generation = 0;
    std::string manifest_schema;
    std::string manifest_sha256;
    bool plan_ok = true;
    long planned_actions = 0;
    std::vector<Refusal> refusals;
    std::vector<StatusNotice> services;
};

std::string build_status_json(const StatusSnapshot& snapshot);

}  // namespace nodecore::supervisor
