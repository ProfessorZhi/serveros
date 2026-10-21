#include "windows-core/watch.hpp"
#include "json_detail.hpp"
#include <algorithm>
#include <charconv>
#include <cctype>
#include <set>
namespace nodecore::windows_core {
namespace {
using J = nodecore::supervisor::detail::Json;
bool keys(const J &j, std::initializer_list<const char *> allowed) {
    if (!j.is_object())
        return false;
    for (auto &[key, value] : j.members) {
        (void)value;
        bool found = false;
        for (auto a : allowed)
            if (key == a)
                found = true;
        if (!found)
            return false;
    }
    return true;
}
bool text(const J &j, const char *key, std::string &s) {
    auto p = j.find(key);
    if (!p || !p->is_string() || p->text.empty() || p->text.size() > 256)
        return false;
    s = p->text;
    return true;
}
std::string lower(std::string s) {
    for (char &c : s)
        if (c >= 'A' && c <= 'Z')
            c = static_cast<char>(c - 'A' + 'a');
    return s;
}
} // namespace
bool service_phase_transition(WatchServicePhase before, WatchServicePhase after) {
    if (before == WatchServicePhase::Stopped)
        return after == WatchServicePhase::Stopped;
    if (before == WatchServicePhase::Stopping)
        return after == WatchServicePhase::Stopping || after == WatchServicePhase::Stopped;
    if (before == WatchServicePhase::Running)
        return after != WatchServicePhase::Starting;
    return true;
}
bool parse_watch_config(const std::string &source, const std::string &sid, WatchConfig &out,
                        std::string &error) {
    auto fail = [&](const char *code) {
        error = code;
        return false;
    };
    if (source.empty() || source.size() > 16384)
        return fail("CONFIG_SIZE");
    J root;
    std::string detail;
    if (!nodecore::supervisor::detail::Reader(source).parse(root, detail))
        return fail("CONFIG_JSON");
    if (!keys(root, {"schema", "node", "principal", "generation", "observe_only", "interval_ms",
                     "services"}))
        return fail("CONFIG_FIELDS");
    WatchConfig c;
    std::string schema;
    if (!text(root, "schema", schema) || schema != "serveros/windows-core-desired.v1" ||
        !text(root, "node", c.node) || c.node.rfind("device:", 0) != 0 ||
        !text(root, "principal", c.principal) || c.principal != sid)
        return fail("CONFIG_IDENTITY");
    auto gen = root.find("generation");
    if (!gen || !gen->is_int() || gen->integer < 0 || gen->integer > 2147483647)
        return fail("CONFIG_GENERATION");
    c.generation = gen->integer;
    if (auto v = root.find("observe_only")) {
        if (!v->is_bool())
            return fail("CONFIG_OBSERVE_ONLY");
        c.observe_only = v->boolean;
    }
    if (auto v = root.find("interval_ms")) {
        if (!v->is_int() || v->integer < 1000 || v->integer > 10000)
            return fail("CONFIG_INTERVAL");
        c.interval_ms = static_cast<unsigned>(v->integer);
    }
    auto services = root.find("services");
    if (!services || !services->is_array() || services->items.size() > 3)
        return fail("CONFIG_SERVICES");
    std::vector<UnitGrant> grants;
    for (auto &row : services->items) {
        if (!keys(row, {"id", "owner", "manager", "unit", "desired", "health"}))
            return fail("CONFIG_SERVICE_FIELDS");
        WatchUnit u;
        std::string id, owner, manager;
        if (!text(row, "id", id) || !text(row, "owner", owner) || owner != sid ||
            !text(row, "manager", manager) || !text(row, "unit", u.grant.target) ||
            !text(row, "desired", u.desired))
            return fail("CONFIG_SERVICE");
        if (id == "cloudcli")
            u.grant.role = UnitRole::CloudCli;
        else if (id == "connector")
            u.grant.role = UnitRole::Connector;
        else if (id == "tunnel")
            u.grant.role = UnitRole::Tunnel;
        else
            return fail("CONFIG_ROLE");
        if (manager == "scm")
            u.grant.manager = UnitManager::Scm;
        else if (manager == "task-scheduler")
            u.grant.manager = UnitManager::TaskScheduler;
        else
            return fail("CONFIG_MANAGER");
        if (u.desired != "running" && u.desired != "manual" && u.desired != "disabled")
            return fail("CONFIG_DESIRED");
        u.grant.owner_sid = sid;
        u.grant.enabled = u.desired == "running" && !c.observe_only;
        auto h = row.find("health");
        if (!h || !keys(*h, {"probe", "port", "path", "key", "value"}))
            return fail("CONFIG_HEALTH");
        std::string probe;
        if (!text(*h, "probe", probe) || probe != "loopback-http-json" ||
            !text(*h, "path", u.probe.path) || u.probe.path[0] != '/' ||
            u.probe.path.rfind("//", 0) == 0 || !text(*h, "key", u.probe.key))
            return fail("CONFIG_PROBE");
        for (unsigned char ch : u.probe.path)
            if (ch < 33 || ch > 126)
                return fail("CONFIG_PROBE_PATH");
        auto port = h->find("port");
        if (!port || !port->is_int() || port->integer < 1 || port->integer > 65535)
            return fail("CONFIG_PROBE_PORT");
        u.probe.port = static_cast<unsigned>(port->integer);
        auto value = h->find("value");
        if (!value)
            return fail("CONFIG_PROBE_VALUE");
        if (value->is_bool()) {
            u.probe.is_boolean = true;
            u.probe.boolean_value = value->boolean;
        } else if (value->is_string() && value->text.size() <= 128)
            u.probe.value = value->text;
        else
            return fail("CONFIG_PROBE_VALUE");
        grants.push_back(u.grant);
        c.services.push_back(std::move(u));
    }
    if (!valid_grants(grants))
        return fail("CONFIG_GRANTS");
    out = std::move(c);
    error.clear();
    return true;
}
std::vector<WindowsRuntimeRow> watch_runtime_pass(const WatchConfig &config, RecoveryStore &store,
                                                  RecoveryBackend &backend, std::int64_t now) {
    std::vector<UnitGrant> grants;
    for (const auto &unit : config.services)
        grants.push_back(unit.grant);
    RecoveryEngine engine(grants, store, backend);
    std::vector<WindowsRuntimeRow> rows;
    for (const auto &unit : config.services) {
        RecoveryResult result;
        if (config.observe_only)
            result = {false, "OBSERVE_ONLY"};
        else if (unit.desired != "running")
            result = {false, unit.desired == "manual" ? "DESIRED_MANUAL" : "DESIRED_DISABLED"};
        else
            result = engine.recover(unit.grant.role, now);
        auto readiness = backend.probe(unit.grant);
        const char *role = unit.grant.role == UnitRole::CloudCli    ? "cloudcli"
                           : unit.grant.role == UnitRole::Connector ? "connector"
                                                                    : "tunnel";
        rows.push_back({role, unit.desired,
                        readiness == ProbeReadiness::Ready      ? "READY"
                        : readiness == ProbeReadiness::NotReady ? "NOT_READY"
                                                                : "UNMEASURED",
                        result.code, result.acted});
    }
    return rows;
}
ProbeReadiness parse_http_probe(const std::string &raw, const HttpProbe &expected) {
    if (raw.size() > 16384)
        return ProbeReadiness::Unmeasured;
    auto split = raw.find("\r\n\r\n");
    if (split == std::string::npos || split > 4096)
        return ProbeReadiness::Unmeasured;
    auto line = raw.find("\r\n");
    if (line == std::string::npos)
        return ProbeReadiness::Unmeasured;
    auto status = raw.substr(0, line);
    if (status.rfind("HTTP/1.1 200 ", 0) != 0 && status.rfind("HTTP/1.0 200 ", 0) != 0)
        return ProbeReadiness::NotReady;
    std::size_t p = line + 2, body_size = 0;
    bool seen = false;
    std::set<std::string> headers;
    while (p < split) {
        auto end = raw.find("\r\n", p);
        if (end == std::string::npos)
            return ProbeReadiness::Unmeasured;
        auto colon = raw.find(':', p);
        if (colon == std::string::npos || colon >= end)
            return ProbeReadiness::Unmeasured;
        auto key = lower(raw.substr(p, colon - p));
        if (!headers.insert(key).second)
            return ProbeReadiness::Unmeasured;
        if (key == "transfer-encoding")
            return ProbeReadiness::Unmeasured;
        if (key == "content-length") {
            auto start = colon + 1;
            while (start < end && raw[start] == ' ')
                start++;
            auto [last, ec] = std::from_chars(raw.data() + start, raw.data() + end, body_size);
            if (ec != std::errc{} || last != raw.data() + end || body_size > 8192)
                return ProbeReadiness::Unmeasured;
            seen = true;
        }
        p = end + 2;
    }
    if (!seen || raw.size() != split + 4 + body_size)
        return ProbeReadiness::Unmeasured;
    J body;
    std::string error;
    if (!nodecore::supervisor::detail::Reader(raw.substr(split + 4)).parse(body, error) ||
        !body.is_object())
        return ProbeReadiness::Unmeasured;
    auto value = body.find(expected.key);
    if (!value)
        return ProbeReadiness::NotReady;
    bool match = expected.is_boolean ? value->is_bool() && value->boolean == expected.boolean_value
                                     : value->is_string() && value->text == expected.value;
    return match ? ProbeReadiness::Ready : ProbeReadiness::NotReady;
}
} // namespace nodecore::windows_core
