// The systemd --user adapter: the only place in this component that names a
// program.
//
// WHAT IT DOES NOT DO
// -------------------
// It does not build a command line and hand it to a shell. It does not daemonize
// anything, does not fork a supervisor, and does not implement a retry. systemd
// is the process supervisor (§20), and this adapter's entire job is to ask it
// one question or one verb and report what it said.
//
// WHAT IT ENFORCES, TWICE
// -----------------------
// `valid_unit_id` and the caller's `UnitAllowlist` are checked here BEFORE any
// process is started, on top of the planner's own check. Two gates rather than
// one, because this is the function that turns a string into an argv and the
// planner's refusal is a decision, not an enforcement.
#pragma once

#include <functional>
#include <string>
#include <vector>

#include "supervisor/model.hpp"
#include "supervisor/provider.hpp"

namespace nodecore::supervisor {

// The argv for one `systemctl --user` verb. A LIST, exported so a test can
// assert its shape without running anything: `--user`, `--no-pager` and
// `--quiet` are structural (there is no pager to block on and no prompt to
// answer), and the unit id is always its own element, so no character in it can
// become part of another argument.
std::vector<std::string> systemctl_verb_argv(const std::string& systemctl_path,
                                             const std::string& verb, const std::string& unit);

std::vector<std::string> systemctl_show_argv(const std::string& systemctl_path,
                                             const std::string& unit);

// `ActiveState=...\nSubState=...` from `systemctl show` into one observation.
//
// An ActiveState this version does not know, or no ActiveState at all, is
// UNMEASURED with the reason — never `Inactive`. A state word added by a newer
// systemd must not arrive here as a fault.
Observed parse_systemctl_show(const std::string& unit, const std::string& stdout_text,
                              const std::string& observed_at);

class SystemdUserProvider : public Provider {
public:
    // `clock` returns an ISO-8601 instant for `observed_at` and is INJECTED, so
    // a test's observations carry the time the test says rather than the time it
    // happened to run.
    SystemdUserProvider(std::string systemctl_path, UnitAllowlist allowlist,
                        std::function<std::string()> clock, int timeout_seconds = 10);

    Observed query(const std::string& unit) override;
    ProviderResult start(const std::string& unit) override;
    ProviderResult restart(const std::string& unit) override;
    ProviderResult stop(const std::string& unit) override;

private:
    ProviderResult act(const std::string& verb, const std::string& unit);
    bool refuse(const std::string& unit, const std::string& what, ProviderResult& out,
                Observed& observed) const;

    std::string systemctl_path_;
    UnitAllowlist allowlist_;
    std::function<std::string()> clock_;
    int timeout_seconds_;
};

}  // namespace nodecore::supervisor
