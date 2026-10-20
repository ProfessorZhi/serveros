// Two samples in, one NodeResourceSnapshot v1 out.
//
// Everything here is arithmetic over two `Sample`s that a test can state by
// hand. There is no clock, no filesystem and no sleep in this file, which is the
// point: the cases that matter — a counter that went backwards, an interval of
// zero, a NIC whose driver reports no speed, a device that appeared between the
// samples — are all expressible as two fixtures.
//
// The invariant this file exists to keep: a fact that could not be measured is
// emitted as {"state": "UNAVAILABLE", "reason": "..."} and never as 0. Every such
// field is also listed under `unknowns`, so a reader does not have to walk the
// document to find out what is missing.
#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "nodecore/json.hpp"
#include "nodecore/sample.hpp"

namespace nodecore {

struct SnapshotOptions {
    std::string collector_version = "0.1.0";
    // Whether the facts came from the machine or from a fixture. The caller
    // states it because only the caller knows which FileSource it sampled from.
    bool live = false;
};

// The measured spacing of the two samples, in nanoseconds, or nullopt with a
// reason. Refused — not zero — when either sample has no /proc/uptime reading,
// when the two readings are not separated, and when the host restarted between
// them (a reboot resets every counter in this document, and a delta taken across
// one is a fiction).
std::optional<std::int64_t> interval_nanoseconds(const Sample& before, const Sample& after,
                                                 std::string& reason);

Json build_snapshot(const Sample& before, const Sample& after, const SnapshotOptions& options);

}  // namespace nodecore
