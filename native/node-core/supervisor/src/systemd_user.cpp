#include "supervisor/systemd_user.hpp"

#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include "proc.hpp"
#include "supervisor/manifest.hpp"

namespace nodecore::supervisor {

std::vector<std::string> systemctl_verb_argv(const std::string& systemctl_path,
                                             const std::string& verb, const std::string& unit) {
    // `--` before the unit even though `valid_unit_id` refuses a leading dash:
    // the two together mean a future loosening of the id rule cannot silently
    // turn a unit name into an option.
    return {systemctl_path, "--user", "--no-pager", "--quiet", verb, "--", unit};
}

std::vector<std::string> systemctl_show_argv(const std::string& systemctl_path,
                                             const std::string& unit) {
    return {systemctl_path, "--user",      "--no-pager", "--quiet",
            "show",         "--property=ActiveState,SubState", "--", unit};
}

Observed parse_systemctl_show(const std::string& unit, const std::string& stdout_text,
                              const std::string& observed_at) {
    std::string active;
    std::string sub_state;
    std::istringstream stream(stdout_text);
    std::string line;
    while (std::getline(stream, line)) {
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        const std::size_t split = line.find('=');
        if (split == std::string::npos) {
            continue;
        }
        const std::string key = line.substr(0, split);
        const std::string value = line.substr(split + 1);
        if (key == "ActiveState") {
            active = value;
        } else if (key == "SubState") {
            sub_state = value;
        }
    }

    if (active.empty()) {
        return Observed::unavailable(
            unit, "systemctl printed no ActiveState, so this unit was not read at all. An unread "
                  "unit is not an inactive one");
    }
    const std::optional<Active> parsed = active_from_word(active);
    if (!parsed.has_value()) {
        return Observed::unavailable(unit,
                                     "systemctl reported an ActiveState this version does not "
                                     "know (\"" +
                                         active +
                                         "\"). A state word added by a newer systemd must not "
                                         "arrive here as a fault");
    }

    Observed row;
    row.unit = unit;
    row.active = *parsed;
    row.sub_state = sub_state;
    row.observed_at = observed_at;
    row.source = "systemd-user";
    row.measured = true;
    // Just asked, so its age is zero. The provider knows what time it is; the
    // planner only compares, which is why the age travels instead of a second
    // timestamp.
    row.age_seconds = 0;
    return row;
}

SystemdUserProvider::SystemdUserProvider(std::string systemctl_path, UnitAllowlist allowlist,
                                        std::function<std::string()> clock, int timeout_seconds)
    : systemctl_path_(std::move(systemctl_path)),
      allowlist_(std::move(allowlist)),
      clock_(std::move(clock)),
      timeout_seconds_(timeout_seconds) {}

bool SystemdUserProvider::refuse(const std::string& unit, const std::string& what,
                                 ProviderResult& out, Observed& observed) const {
    if (!valid_unit_id(unit)) {
        out.code = "UNIT_ID_INVALID";
        out.reason = "\"" + unit + "\" is not a systemd user unit id, so it cannot be put in an argv";
        observed = Observed::unavailable(unit, out.reason);
        return true;
    }
    if (!allowlist_.permits(unit)) {
        out.code = "UNIT_NOT_ALLOWED";
        out.reason = "\"" + unit + "\" is not in this Core's allowlist, so " + what +
                     " was refused before any process was started";
        observed = Observed::unavailable(unit, out.reason);
        return true;
    }
    return false;
}

Observed SystemdUserProvider::query(const std::string& unit) {
    ProviderResult ignored;
    Observed observed;
    if (refuse(unit, "reading it", ignored, observed)) {
        return observed;
    }
    const std::vector<std::string> argv = systemctl_show_argv(systemctl_path_, unit);
    const detail::RunResult run = detail::run_argv(argv, {}, timeout_seconds_);
    if (!run.ok) {
        return Observed::unavailable(unit, "systemctl could not be read (" + run.code + "): " +
                                               (run.reason.empty() ? run.stderr_text : run.reason));
    }
    return parse_systemctl_show(unit, run.stdout_text, clock_ ? clock_() : std::string());
}

ProviderResult SystemdUserProvider::act(const std::string& verb, const std::string& unit) {
    ProviderResult out;
    Observed ignored;
    if (refuse(unit, verb, out, ignored)) {
        return out;
    }
    const std::vector<std::string> argv = systemctl_verb_argv(systemctl_path_, verb, unit);
    const detail::RunResult run = detail::run_argv(argv, {}, timeout_seconds_);
    out.code = run.code;
    out.reason = run.reason;
    out.exit_code = run.exit_code;
    out.ok = run.ok;
    if (!run.ok && out.reason.size() == 0) {
        out.reason = "systemctl " + verb + " exited " + std::to_string(run.exit_code) + ": " +
                     run.stderr_text;
    }
    return out;
}

ProviderResult SystemdUserProvider::start(const std::string& unit) { return act("start", unit); }
ProviderResult SystemdUserProvider::restart(const std::string& unit) { return act("restart", unit); }
ProviderResult SystemdUserProvider::stop(const std::string& unit) { return act("stop", unit); }

}  // namespace nodecore::supervisor
