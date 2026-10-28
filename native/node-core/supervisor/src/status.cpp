#include "supervisor/status.hpp"

#include <string>
#include <utility>
#include <vector>

#include "nodecore/json.hpp"

namespace nodecore::supervisor {
namespace {

nodecore::Json refusals_json(const std::vector<Refusal>& refusals) {
    nodecore::Json array = nodecore::Json::array();
    for (const Refusal& refusal : refusals) {
        nodecore::Json row = nodecore::Json::object();
        row.set("scope", nodecore::Json::string(refusal.scope));
        row.set("code", nodecore::Json::string(refusal.code));
        row.set("reason", nodecore::Json::string(refusal.reason));
        array.push(std::move(row));
    }
    return array;
}

nodecore::Json service_json(const StatusNotice& notice) {
    nodecore::Json row = nodecore::Json::object();
    row.set("id", nodecore::Json::string(notice.id));
    row.set("unit", nodecore::Json::string(notice.unit));
    row.set("desired", nodecore::Json::string(notice.desired));
    row.set("measured", nodecore::Json::boolean(notice.measured));
    row.set("active", nodecore::Json::string(notice.active));
    row.set("sub_state", nodecore::Json::string(notice.sub_state));
    row.set("observed_at", nodecore::Json::string(notice.observed_at));
    // A number ONLY when there is one. `age_seconds: -1` would be a value a
    // reader could compare against a threshold; an absent key cannot be, and
    // "how old is it" genuinely has no answer for a unit nobody read.
    if (notice.age_seconds >= 0) {
        row.set("age_seconds", nodecore::Json::integer(notice.age_seconds));
    }
    if (!notice.reason.empty()) {
        row.set("reason", nodecore::Json::string(notice.reason));
    }
    return row;
}

}  // namespace

std::string build_status_json(const StatusSnapshot& snapshot) {
    nodecore::Json root = nodecore::Json::object();
    root.set("schema", nodecore::Json::string(kStatusSchema));
    root.set("mode", nodecore::Json::string(snapshot.mode));
    root.set("profile", nodecore::Json::string(snapshot.profile));
    root.set("node", nodecore::Json::string(snapshot.node));
    root.set("principal", nodecore::Json::string(snapshot.principal));
    root.set("pid", nodecore::Json::integer(snapshot.pid));
    root.set("started_at", nodecore::Json::string(snapshot.started_at));
    // `uptime_seconds` is absent until a tick has happened, for the same reason
    // `age_seconds` is: a first request answered before the first observation
    // has no uptime to report, and 0 would read as "just started" rather than
    // "has not looked yet".
    if (snapshot.uptime_seconds >= 0) {
        root.set("uptime_seconds", nodecore::Json::integer(snapshot.uptime_seconds));
    }
    root.set("ticks", nodecore::Json::integer(snapshot.ticks));
    root.set("tick_errors", nodecore::Json::integer(snapshot.tick_errors));

    nodecore::Json about = nodecore::Json::object();
    about.set("schema", nodecore::Json::string(snapshot.manifest_schema));
    about.set("generation", nodecore::Json::integer(snapshot.generation));
    about.set("sha256", nodecore::Json::string(snapshot.manifest_sha256));
    root.set("manifest", std::move(about));

    nodecore::Json last = nodecore::Json::object();
    if (snapshot.last_tick_monotonic >= 0) {
        last.set("monotonic_seconds", nodecore::Json::integer(snapshot.last_tick_monotonic));
    }
    if (snapshot.last_tick_duration_seconds >= 0) {
        last.set("duration_seconds", nodecore::Json::integer(snapshot.last_tick_duration_seconds));
    }
    root.set("last_tick", std::move(last));

    nodecore::Json plan = nodecore::Json::object();
    plan.set("ok", nodecore::Json::boolean(snapshot.plan_ok));
    plan.set("actions", nodecore::Json::integer(snapshot.planned_actions));
    plan.set("refusals", refusals_json(snapshot.refusals));
    root.set("plan", std::move(plan));

    nodecore::Json services = nodecore::Json::array();
    for (const StatusNotice& notice : snapshot.services) {
        services.push(service_json(notice));
    }
    root.set("services", std::move(services));

    // The monitor's own cost, and the profile that incurred it. Numbers appear
    // only when measured — the same rule every other value in this document
    // follows: a value nobody read is not a zero.
    nodecore::Json monitor = nodecore::Json::object();
    monitor.set("profile", nodecore::Json::string(snapshot.profile));
    if (snapshot.rss_kb >= 0) monitor.set("rss_kb", nodecore::Json::integer(snapshot.rss_kb));
    if (snapshot.cpu_ms >= 0) monitor.set("cpu_ms", nodecore::Json::integer(snapshot.cpu_ms));
    if (snapshot.tick_ms >= 0) monitor.set("tick_ms", nodecore::Json::integer(snapshot.tick_ms));
    root.set("monitor", std::move(monitor));

    // Sorted keys and the Pretty form, so two answers that say the same thing
    // are the same bytes and a reader can diff them.
    return root.dump();
}

}  // namespace nodecore::supervisor
