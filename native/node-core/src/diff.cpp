#include "nodecore/diff.hpp"

namespace nodecore {

Reading Reading::measured(std::uint64_t value) {
    Reading reading;
    reading.known = true;
    reading.value = value;
    return reading;
}

Reading Reading::unknown(std::string reason) {
    Reading reading;
    reading.known = false;
    reading.reason = std::move(reason);
    return reading;
}

Delta Delta::measured(std::uint64_t value) {
    Delta delta;
    delta.known = true;
    delta.value = value;
    return delta;
}

Delta Delta::unknown(std::string reason) {
    Delta delta;
    delta.known = false;
    delta.reason = std::move(reason);
    return delta;
}

Delta counter_delta(const Reading& previous, const Reading& current,
                    std::optional<std::int64_t> interval_nanoseconds) {
    if (!previous.known) {
        return Delta::unknown("previous reading unavailable: " + previous.reason);
    }
    if (!current.known) {
        return Delta::unknown("current reading unavailable: " + current.reason);
    }
    if (!interval_nanoseconds.has_value() || *interval_nanoseconds <= 0) {
        return Delta::unknown("sample interval is zero or not measurable");
    }
    if (current.value < previous.value) {
        return Delta::unknown(
            "counter decreased between samples (device reset or counter wrap); "
            "the wrapped value is not reconstructed");
    }
    return Delta::measured(current.value - previous.value);
}

Delta signed_counter_delta(std::optional<std::uint64_t> previous,
                           std::optional<std::uint64_t> current,
                           std::optional<std::int64_t> interval_nanoseconds,
                           const std::string& what) {
    if (!previous.has_value()) {
        return Delta::unknown("previous " + what + " unavailable");
    }
    if (!current.has_value()) {
        return Delta::unknown("current " + what + " unavailable");
    }
    return counter_delta(Reading::measured(*previous), Reading::measured(*current),
                         interval_nanoseconds);
}

std::optional<double> rate_per_second(const Delta& delta,
                                      std::optional<std::int64_t> interval_nanoseconds) {
    if (!delta.known || !interval_nanoseconds.has_value() || *interval_nanoseconds <= 0) {
        return std::nullopt;
    }
    const double seconds = static_cast<double>(*interval_nanoseconds) / 1e9;
    return static_cast<double>(delta.value) / seconds;
}

std::optional<double> percentage(const Delta& part, const Delta& whole) {
    if (!part.known || !whole.known || whole.value == 0) {
        return std::nullopt;
    }
    return 100.0 * static_cast<double>(part.value) / static_cast<double>(whole.value);
}

}  // namespace nodecore
