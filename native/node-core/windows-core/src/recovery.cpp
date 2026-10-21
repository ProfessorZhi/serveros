#include "windows-core/recovery.hpp"
#include <algorithm>
#include <cctype>
#include <limits>
#include <set>
#include <sstream>
namespace nodecore::windows_core {
namespace {
bool ascii_name(const std::string &s, bool path) {
    if (s.empty() || s.size() > 256 || (path && (s[0] != '\\' || s.size() == 1)))
        return false;
    for (unsigned char c : s)
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
              c == '_' || c == '-' || c == '.' || (path && c == '\\')))
            return false;
    if (s.find("..") != std::string::npos || s.find("\\\\") != std::string::npos ||
        s.back() == '\\')
        return false;
    return true;
}
bool known_role(UnitRole r) {
    return r == UnitRole::CloudCli || r == UnitRole::Connector || r == UnitRole::Tunnel;
}
} // namespace
std::string unit_key(const UnitGrant &g) {
    std::string s = (g.manager == UnitManager::Scm ? "scm:" : "task:") + g.target;
    for (auto &c : s)
        if (c >= 'A' && c <= 'Z')
            c = static_cast<char>(c - 'A' + 'a');
    return s;
}
bool valid_grants(const std::vector<UnitGrant> &gs) {
    if (gs.size() > 3)
        return false;
    std::set<UnitRole> roles;
    std::set<std::string> targets;
    for (auto &g : gs) {
        if (!known_role(g.role) ||
            (g.manager != UnitManager::Scm && g.manager != UnitManager::TaskScheduler) ||
            !ascii_name(g.target, g.manager == UnitManager::TaskScheduler) ||
            g.owner_sid.size() > 184 || g.owner_sid.rfind("S-1-", 0) != 0 ||
            !roles.insert(g.role).second || !targets.insert(unit_key(g)).second)
            return false;
        for (char c : g.owner_sid)
            if (!((c >= '0' && c <= '9') || c == 'S' || c == '-'))
                return false;
    }
    return true;
}
std::string encode_recovery_state(const RecoveryState &s) {
    std::ostringstream o;
    o << "serveros/windows-recovery.v1\n"
      << s.last_seen << ' ' << s.circuit_until << ' ' << s.failures << ' ' << (s.pending ? 1 : 0)
      << ' ' << s.attempts.size() << '\n';
    for (auto t : s.attempts)
        o << t << ' ';
    o << '\n';
    return o.str();
}
bool decode_recovery_state(const std::string &text, RecoveryState &out) {
    if (text.empty() || text.size() > 4096)
        return false;
    std::istringstream in(text);
    std::string schema;
    std::getline(in, schema);
    if (schema != "serveros/windows-recovery.v1")
        return false;
    RecoveryState s;
    unsigned pending = 0;
    std::size_t n = 0;
    if (!(in >> s.last_seen >> s.circuit_until >> s.failures >> pending >> n) || s.last_seen < 0 ||
        s.last_seen > std::numeric_limits<std::int64_t>::max() - 3600 || s.circuit_until < 0 ||
        s.failures > 3 || pending > 1 || n > 3)
        return false;
    s.pending = pending != 0;
    for (std::size_t i = 0; i < n; i++) {
        std::int64_t t;
        if (!(in >> t) || t <= 0 || t > s.last_seen ||
            (!s.attempts.empty() && t < s.attempts.back()))
            return false;
        s.attempts.push_back(t);
    }
    in >> std::ws;
    if (!in.eof() || s.circuit_until > s.last_seen + 3600)
        return false;
    out = s;
    return true;
}
RecoveryEngine::RecoveryEngine(std::vector<UnitGrant> g, RecoveryStore &s, RecoveryBackend &b)
    : grants_(std::move(g)), store_(s), backend_(b), valid_(valid_grants(grants_)) {}
RecoveryResult RecoveryEngine::recover(UnitRole role, std::int64_t now) {
    auto refuse = [](const char *s) { return RecoveryResult{false, s}; };
    if (!valid_)
        return refuse("INVALID_GRANTS");
    auto it = std::find_if(grants_.begin(), grants_.end(), [&](auto &g) { return g.role == role; });
    if (it == grants_.end() || !it->enabled)
        return refuse("NOT_GRANTED");
    if (now <= 0 || now > std::numeric_limits<std::int64_t>::max() - 7200)
        return refuse("CLOCK_UNTRUSTED");
    auto tx = store_.lock(*it);
    if (!tx)
        return refuse("WRITER_BUSY_OR_UNTRUSTED");
    RecoveryState s;
    if (!tx->load(s))
        return refuse("STATE_UNTRUSTED");
    // Every store including an injected one must satisfy the serialized
    // invariant.
    RecoveryState checked;
    if (!decode_recovery_state(encode_recovery_state(s), checked))
        return refuse("STATE_UNTRUSTED");
    if (s.pending)
        return refuse("OUTCOME_UNKNOWN");
    if (s.last_seen > now)
        return refuse("CLOCK_UNTRUSTED");
    auto obs = backend_.inspect(*it);
    if (!obs.measured)
        return refuse("STATE_UNMEASURED");
    if (!obs.native_grant || !obs.owner_matches)
        return refuse("NATIVE_GRANT_DENIED");
    switch (obs.condition) {
    case UnitCondition::Disabled:
        return refuse("MANUAL_DISABLED");
    case UnitCondition::NeedsLogin:
        return refuse("NEEDS_LOGIN");
    case UnitCondition::Busy:
        return refuse("BUSY");
    case UnitCondition::Unknown:
        return refuse("STATE_UNKNOWN");
    default:
        break;
    }
    auto pre = backend_.probe(*it);
    if (pre == ProbeReadiness::Ready)
        return refuse("ALREADY_READY");
    if (pre == ProbeReadiness::Unmeasured)
        return refuse("PROBE_UNMEASURED");
    if (obs.condition == UnitCondition::Running)
        return refuse("RUNNING_RESTART_NOT_GRANTED");
    if (s.circuit_until > now)
        return refuse("CIRCUIT_OPEN");
    s.attempts.erase(std::remove_if(s.attempts.begin(), s.attempts.end(),
                                    [&](auto t) { return now - t >= 3600; }),
                     s.attempts.end());
    if (s.attempts.size() >= 3)
        return refuse("BUDGET_EXHAUSTED");
    const std::int64_t delays[] = {60, 300, 900};
    if (!s.attempts.empty() &&
        now - s.attempts.back() < delays[std::min<std::size_t>(s.attempts.size() - 1, 2)])
        return refuse("BACKOFF");
    s.last_seen = now;
    s.pending = true;
    s.attempts.push_back(now);
    if (!tx->save(s))
        return refuse("PRECOMMIT_FAILED");
    // An exception or process death after this point deliberately leaves pending.
    auto outcome = backend_.start(*it);
    if (outcome == StartOutcome::Unknown) {
        backend_.probe(*it);
        return {true, "OUTCOME_UNKNOWN"};
    }
    const bool started = outcome == StartOutcome::Accepted;
    auto after = backend_.probe(*it);
    s.pending = false;
    if (started && after == ProbeReadiness::Ready)
        s.failures = 0;
    else if (!started || after == ProbeReadiness::NotReady) {
        s.failures = std::min(s.failures + 1, 3U);
        if (s.failures >= 3)
            s.circuit_until = now + 3600;
    }
    if (!tx->save(s))
        return {true, "OUTCOME_UNKNOWN"};
    return {true, started && after == ProbeReadiness::Ready ? "RECOVERED"
                  : after == ProbeReadiness::Unmeasured     ? "PROBE_UNMEASURED"
                                                            : "NOT_RECOVERED"};
}
} // namespace nodecore::windows_core
