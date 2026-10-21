// Counter readings and their deltas.
//
// The whole reason this is a separate, testable unit is one sentence: an unknown
// delta and a zero delta are different facts, and the failure mode this file
// exists to prevent is printing 0 when the truth is "cannot be computed".
//
// A counter that went backwards is reported UNKNOWN rather than rewound. It can
// mean the device was reset, the interface was recreated, or a 32-bit counter
// wrapped; the kernel does not say which, so neither does this. Guessing a
// wrapped value would invent a large, plausible, wrong number of bytes — the
// exact failure this collector is meant to make impossible.
#pragma once

#include <cstdint>
#include <optional>
#include <string>

namespace nodecore {

// What one counter read at one instant says.
struct Reading {
    bool known = false;
    std::uint64_t value = 0;
    std::string reason;  // set when !known

    static Reading measured(std::uint64_t value);
    static Reading unknown(std::string reason);
};

// The difference between two readings of the same counter.
struct Delta {
    bool known = false;
    std::uint64_t value = 0;
    std::string reason;  // set when !known, and explains exactly which condition failed

    static Delta measured(std::uint64_t value);
    static Delta unknown(std::string reason);
};

// `interval` is the observed spacing of the two samples in nanoseconds. An
// absent or non-positive interval makes every rate undefined, so a delta that
// depends on it is refused rather than divided by zero and rendered as infinity.
Delta counter_delta(const Reading& previous, const Reading& current,
                    std::optional<std::int64_t> interval_nanoseconds);

// Signed deltas, for the parts of /proc/stat where the sums are not counters of
// bytes but still must not go backwards silently.
Delta signed_counter_delta(std::optional<std::uint64_t> previous,
                           std::optional<std::uint64_t> current,
                           std::optional<std::int64_t> interval_nanoseconds,
                           const std::string& what);

// bytes (or any unit) per second, over a known delta and a known interval.
// Returns nullopt when either is unknown: a rate with no denominator is not a
// rate.
std::optional<double> rate_per_second(const Delta& delta,
                                      std::optional<std::int64_t> interval_nanoseconds);

// A percentage, guarded the same way.
std::optional<double> percentage(const Delta& part, const Delta& whole);

}  // namespace nodecore
