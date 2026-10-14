#include "supervisor/provider.hpp"

#include <algorithm>
#include <utility>

#include "supervisor/manifest.hpp"

namespace nodecore::supervisor {

UnitAllowlistBuilt UnitAllowlist::build(const std::vector<std::string>& units) {
    UnitAllowlistBuilt built;
    for (const std::string& unit : units) {
        if (!valid_unit_id(unit)) {
            built.error = "\"" + unit +
                          "\" is not a systemd user unit id, so it cannot be allowlisted. Refusing "
                          "beats dropping it: a dropped entry is indistinguishable from one that "
                          "was never configured";
            return built;
        }
        if (std::find(built.allowlist.units_.begin(), built.allowlist.units_.end(), unit) !=
            built.allowlist.units_.end()) {
            built.error = "\"" + unit + "\" is allowlisted twice";
            return built;
        }
        built.allowlist.units_.push_back(unit);
    }
    built.ok = true;
    return built;
}

bool UnitAllowlist::permits(const std::string& unit) const {
    // Whole-string comparison. A prefix match would let `a.service-evil` pass an
    // allowlist entry for `a.service`, which is the failure mode a pattern
    // introduces the moment it is written.
    return std::find(units_.begin(), units_.end(), unit) != units_.end();
}

}  // namespace nodecore::supervisor
