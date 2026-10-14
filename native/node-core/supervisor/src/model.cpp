#include "supervisor/model.hpp"

#include <array>
#include <utility>

namespace nodecore::supervisor {
namespace {

// Word -> enum, as a table rather than a chain of `if`s, so that the words a
// reader can find in the header are the same list the parser accepts. A word
// missing from here is refused, which is the point: an unrecognised `desired`
// must not fall through to a default.
template <typename Enum, std::size_t N>
std::optional<Enum> from_word(const std::array<std::pair<const char*, Enum>, N>& table,
                              const std::string& word) {
    for (const auto& row : table) {
        if (word == row.first) {
            return row.second;
        }
    }
    return std::nullopt;
}

constexpr std::array<std::pair<const char*, Desired>, 3> kDesiredWords{{
    {"running", Desired::Running},
    {"manual", Desired::Manual},
    {"disabled", Desired::Disabled},
}};

constexpr std::array<std::pair<const char*, Active>, 6> kActiveWords{{
    {"active", Active::Active},
    {"inactive", Active::Inactive},
    {"failed", Active::Failed},
    {"activating", Active::Activating},
    {"deactivating", Active::Deactivating},
    {"unknown", Active::Unknown},
}};

}  // namespace

std::string to_word(Desired value) {
    switch (value) {
        case Desired::Running:
            return "running";
        case Desired::Manual:
            return "manual";
        case Desired::Disabled:
            return "disabled";
    }
    return "unknown";
}

std::optional<Desired> desired_from_word(const std::string& word) {
    return from_word(kDesiredWords, word);
}

std::string to_word(Active value) {
    switch (value) {
        case Active::Active:
            return "active";
        case Active::Inactive:
            return "inactive";
        case Active::Failed:
            return "failed";
        case Active::Activating:
            return "activating";
        case Active::Deactivating:
            return "deactivating";
        case Active::Unknown:
            return "unknown";
    }
    return "unknown";
}

std::optional<Active> active_from_word(const std::string& word) {
    return from_word(kActiveWords, word);
}

Observed Observed::unavailable(std::string unit, std::string reason) {
    Observed observed;
    observed.unit = std::move(unit);
    observed.active = Active::Unknown;
    observed.measured = false;
    observed.reason = std::move(reason);
    return observed;
}

}  // namespace nodecore::supervisor
