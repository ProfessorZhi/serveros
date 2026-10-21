// The snapshot's wall clock, and only its wall clock.
//
// There are two times in a snapshot and they are not the same kind of thing:
//
//   * `observed_at` is UTC wall time, which is what a person and the Python core
//     compare against reports from elsewhere. It can step backwards when NTP
//     corrects the host, which is exactly why it is never used as a divisor.
//   * the sample interval is measured on the monotonic clock. That one is NOT
//     taken here: the Python core reads it from `/proc/uptime`
//     (`runtime/inventory.py:1122` `_monotonic()`), and reading the same file
//     through the same `FileSource` keeps the two collectors' intervals
//     comparable and keeps this whole unit testable from a fixture.
#pragma once

#include <chrono>

namespace nodecore {

class Clock {
public:
    virtual ~Clock() = default;
    virtual std::chrono::system_clock::time_point utc_now() const = 0;
};

class SystemClock final : public Clock {
public:
    std::chrono::system_clock::time_point utc_now() const override {
        return std::chrono::system_clock::now();
    }
};

class FakeClock final : public Clock {
public:
    void set_utc(std::chrono::system_clock::time_point value) { utc_ = value; }
    void advance(std::chrono::milliseconds delta) { utc_ += delta; }
    std::chrono::system_clock::time_point utc_now() const override { return utc_; }

private:
    std::chrono::system_clock::time_point utc_{};
};

// ISO-8601 in UTC with microsecond precision and an explicit "+00:00" offset.
// The offset form rather than "Z" because that is what Python's
// `datetime.isoformat()` produces by default, and it is the form the rest of
// this tree's observations carry.
std::string iso8601_utc(std::chrono::system_clock::time_point when);

}  // namespace nodecore
