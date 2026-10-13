#include "nodecore/clock.hpp"

#include <ctime>
#include <cstdio>

namespace nodecore {

std::string iso8601_utc(std::chrono::system_clock::time_point when) {
    using namespace std::chrono;
    const auto since_epoch = when.time_since_epoch();
    auto seconds_part = duration_cast<seconds>(since_epoch);
    auto micros = duration_cast<microseconds>(since_epoch - seconds_part);
    if (micros.count() < 0) {
        // A negative sub-second remainder only arises before the epoch; carry it
        // into the whole seconds so the printed value is never "…:59.-1".
        seconds_part -= seconds(1);
        micros += seconds(1);
    }
    const std::time_t as_time = system_clock::to_time_t(system_clock::time_point(seconds_part));
    struct tm broken {};
    ::gmtime_r(&as_time, &broken);
    char buffer[64];
    std::snprintf(buffer, sizeof(buffer), "%04d-%02d-%02dT%02d:%02d:%02d.%06ld+00:00",
                  broken.tm_year + 1900, broken.tm_mon + 1, broken.tm_mday,
                  broken.tm_hour, broken.tm_min, broken.tm_sec,
                  static_cast<long>(micros.count()));
    return buffer;
}

}  // namespace nodecore
