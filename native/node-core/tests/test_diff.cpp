#include <cstdint>
#include <optional>
#include <string>

#include "check.hpp"
#include "nodecore/diff.hpp"

using nodecore::counter_delta;
using nodecore::Delta;
using nodecore::percentage;
using nodecore::rate_per_second;
using nodecore::Reading;

namespace {
constexpr std::int64_t kOneSecond = 1'000'000'000;
}

int main() {
    {
        // A measured zero is a measurement. This is the case the whole file
        // exists for: two equal readings over a real interval say "nothing
        // happened", which is a different fact from "could not be measured".
        const Delta delta = counter_delta(Reading::measured(500), Reading::measured(500), kOneSecond);
        CHECK(delta.known);
        CHECK_EQ(delta.value, 0u);
        CHECK(delta.reason.empty());
    }
    {
        const Delta delta =
            counter_delta(Reading::measured(1000), Reading::measured(1750), kOneSecond);
        CHECK(delta.known);
        CHECK_EQ(delta.value, 750u);
    }
    {
        // A counter that went backwards is refused, not rewound. Guessing a wrap
        // would invent a large, plausible, wrong number of bytes.
        const Delta delta =
            counter_delta(Reading::measured(9000), Reading::measured(10), kOneSecond);
        CHECK(!delta.known);
        CHECK_CONTAINS(delta.reason, "counter decreased");
        CHECK_EQ(delta.value, 0u);
    }
    {
        const Delta delta =
            counter_delta(Reading::measured(10), Reading::measured(20), std::nullopt);
        CHECK(!delta.known);
        CHECK_CONTAINS(delta.reason, "sample interval is zero or not measurable");
    }
    {
        const Delta delta = counter_delta(Reading::measured(10), Reading::measured(20), 0);
        CHECK(!delta.known);
        CHECK_CONTAINS(delta.reason, "sample interval is zero or not measurable");
    }
    {
        const Delta delta = counter_delta(Reading::measured(10), Reading::measured(20), -5);
        CHECK(!delta.known);
        CHECK_CONTAINS(delta.reason, "sample interval is zero or not measurable");
    }
    {
        const Delta delta = counter_delta(Reading::unknown("cannot read /proc/net/dev (ENOENT)"),
                                          Reading::measured(20), kOneSecond);
        CHECK(!delta.known);
        CHECK_CONTAINS(delta.reason, "previous reading unavailable");
        CHECK_CONTAINS(delta.reason, "ENOENT");
    }
    {
        const Delta delta = counter_delta(Reading::measured(10),
                                          Reading::unknown("speed attribute unreadable"), kOneSecond);
        CHECK(!delta.known);
        CHECK_CONTAINS(delta.reason, "current reading unavailable");
    }
    {
        const Delta delta = counter_delta(Reading::measured(7), Reading::measured(7), std::nullopt);
        CHECK(!delta.known);
        CHECK_CONTAINS(delta.reason, "interval");
    }
    {
        const Delta delta =
            counter_delta(Reading::measured(0), Reading::measured(2048), kOneSecond);
        const std::optional<double> rate = rate_per_second(delta, kOneSecond);
        CHECK(rate.has_value());
        CHECK_EQ(*rate, 2048.0);
    }
    {
        const Delta unknown_delta = Delta::unknown("counter decreased");
        CHECK(!rate_per_second(unknown_delta, kOneSecond).has_value());
        const Delta known = Delta::measured(100);
        CHECK(!rate_per_second(known, std::nullopt).has_value());
        CHECK(!rate_per_second(known, 0).has_value());
    }
    {
        CHECK(percentage(Delta::measured(1), Delta::measured(4)).value() == 25.0);
        CHECK(!percentage(Delta::measured(1), Delta::measured(0)).has_value());
        CHECK(!percentage(Delta::unknown("x"), Delta::measured(4)).has_value());
    }
    {
        // The signed form is the same rules with a reason the caller supplies,
        // used for the /proc/stat sums.
        const Delta delta = nodecore::signed_counter_delta(
            std::optional<std::uint64_t>(100), std::optional<std::uint64_t>(40), kOneSecond,
            "jiffies");
        CHECK(!delta.known);
        CHECK_CONTAINS(delta.reason, "counter decreased");
        const Delta missing = nodecore::signed_counter_delta(
            std::nullopt, std::optional<std::uint64_t>(40), kOneSecond, "jiffies");
        CHECK(!missing.known);
        CHECK_CONTAINS(missing.reason, "previous jiffies unavailable");
    }
    return check::report("test_diff");
}
