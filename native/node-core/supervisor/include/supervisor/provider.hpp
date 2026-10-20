// The one seam between "the Core decided" and "something happened".
//
// Everything above this interface is pure. Everything below it touches the
// machine. That boundary is the whole point of the file: a plan can be built,
// printed and reviewed without this interface existing, and a test can exercise
// the planner for a year without ever starting a process.
//
// THE ALLOWLIST IS EXACT IDS, NEVER A PATTERN
// -------------------------------------------
// `UnitAllowlist::permits` compares whole unit ids. A prefix or glob match would
// make a unit that was renamed, or one an attacker can name to look like a
// permitted one, inherit the permission. The planner consults the same list
// (through `PlanInput::allowed_units`) and the provider consults it again, so a
// unit outside it is refused twice rather than once.
#pragma once

#include <string>
#include <vector>

#include "supervisor/model.hpp"

namespace nodecore::supervisor {

struct ProviderResult {
    bool ok = false;
    // A stable token: EXITED, TIMED_OUT, START_FAILED, UNIT_NOT_ALLOWED,
    // UNIT_ID_INVALID. A caller switches on this, not on a sentence.
    std::string code;
    std::string reason;
    std::string stdout_text;
    int exit_code = -1;
};

class Provider {
public:
    virtual ~Provider() = default;
    // What the manager says now. Never throws: a unit that could not be read
    // comes back as an UNMEASURED `Observed` with the reason, because "I could
    // not look" must not arrive as `Inactive`.
    virtual Observed query(const std::string& unit) = 0;
    virtual ProviderResult start(const std::string& unit) = 0;
    virtual ProviderResult restart(const std::string& unit) = 0;
    virtual ProviderResult stop(const std::string& unit) = 0;
};

// Forward-declared because `build` returns it and it holds a `UnitAllowlist`
// by value: a type cannot contain itself, so the two definitions have to be
// ordered and the name has to be known before it is a return type.
struct UnitAllowlistBuilt;

// The exact set of unit ids this Core may name.
class UnitAllowlist {
public:
    UnitAllowlist() = default;
    explicit UnitAllowlist(std::vector<std::string> units) : units_(std::move(units)) {}

    // Builds an allowlist and REFUSES an entry that is not a unit id, rather
    // than silently dropping it — a dropped entry looks identical to one that
    // was never configured, and the operator would never learn which.
    static UnitAllowlistBuilt build(const std::vector<std::string>& units);

    bool permits(const std::string& unit) const;
    const std::vector<std::string>& units() const { return units_; }

private:
    std::vector<std::string> units_;
};

// Defined outside the class because it holds a `UnitAllowlist` by value, and a
// type cannot contain itself. `build`'s return type is therefore incomplete
// where it is declared and complete where it is defined and called, which is
// the ordinary shape for a factory that returns its own product.
struct UnitAllowlistBuilt {
    bool ok = false;
    UnitAllowlist allowlist;
    std::string error;
};

}  // namespace nodecore::supervisor
