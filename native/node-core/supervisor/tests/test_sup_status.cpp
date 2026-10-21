// The status document: parsed back, and checked for what it must NOT carry.
//
// Two claims are asserted here and neither is about formatting. The first is
// that an unmeasured service reports `measured: false` with a reason and NO
// age — a `0` in a field a reader compares against a threshold is a claim the
// host cannot support. The second is that the document is a thing an operator
// pastes into a chat: it carries no argv, no executable path, no manifest path
// and no token.
#include <cstdio>
#include <string>

#include "check.hpp"
#include "json_detail.hpp"
#include "supervisor/manifest.hpp"
#include "supervisor/status.hpp"

using namespace nodecore::supervisor;

namespace {

std::string text_of(const detail::Json& object, const std::string& key) {
    const detail::Json* found = object.find(key);
    return found != nullptr && found->is_string() ? found->text : std::string("(absent)");
}

bool has_key(const detail::Json& object, const std::string& key) {
    return object.find(key) != nullptr;
}

StatusSnapshot base_snapshot() {
    StatusSnapshot snapshot;
    snapshot.mode = "observe-only";
    snapshot.node = "device:tokyo-shared-dev-01";
    snapshot.principal = "vince";
    snapshot.pid = 4242;
    snapshot.started_at = "2026-10-14T00:00:00+00:00";
    snapshot.ticks = 3;
    snapshot.generation = 7;
    snapshot.manifest_schema = kDesiredStateSchema;
    snapshot.manifest_sha256 = "a03f7ded2ce4641c385b064397db0f9d2bdfd36911f749372c381a33038a0b53";
    return snapshot;
}

}  // namespace

int main() {
    {
        StatusSnapshot snapshot = base_snapshot();
        StatusNotice unread;
        unread.id = "tunnel";
        unread.unit = "serveros-tunnel.service";
        unread.desired = "running";
        unread.measured = false;
        unread.active = "unknown";
        unread.reason = "systemctl could not be read (TIMED_OUT)";
        snapshot.services.push_back(unread);

        StatusNotice read;
        read.id = "fleet-node";
        read.unit = "serveros-fleet-node.service";
        read.desired = "running";
        read.measured = true;
        read.active = "active";
        read.sub_state = "running";
        read.observed_at = "2026-10-14T00:00:03+00:00";
        read.age_seconds = 2;
        snapshot.services.push_back(read);

        snapshot.refusals.push_back(
            Refusal{"tunnel", "UNKNOWN_IS_NOT_STOPPED", "the unit was read as unknown"});
        snapshot.planned_actions = 0;

        const std::string text = build_status_json(snapshot);

        detail::Json document;
        std::string error;
        detail::Reader reader(text);
        CHECK(reader.parse(document, error));
        if (!error.empty()) {
            std::fprintf(stderr, "status did not parse: %s\n", error.c_str());
        }

        CHECK_EQ(text_of(document, "schema"), std::string(kStatusSchema));
        CHECK_EQ(text_of(document, "mode"), std::string("observe-only"));
        CHECK_EQ(text_of(document, "principal"), std::string("vince"));

        const detail::Json* services = document.find("services");
        CHECK(services != nullptr && services->is_array() && services->items.size() == 2);
        if (services != nullptr && services->items.size() == 2) {
            const detail::Json& unread_row = services->items[0];
            const detail::Json* measured = unread_row.find("measured");
            CHECK(measured != nullptr && measured->is_bool() && !measured->boolean);
            CHECK_EQ(text_of(unread_row, "active"), std::string("unknown"));
            CHECK_CONTAINS(text_of(unread_row, "reason"), "TIMED_OUT");
            // THE ONE THAT MATTERS: no age at all. `age_seconds: 0` would be a
            // reading of "just now" for a unit nobody read.
            CHECK(!has_key(unread_row, "age_seconds"));

            const detail::Json& read_row = services->items[1];
            CHECK(has_key(read_row, "age_seconds"));
            const detail::Json* age = read_row.find("age_seconds");
            CHECK(age != nullptr && age->is_int() && age->integer == 2);
            CHECK_EQ(text_of(read_row, "active"), std::string("active"));
        }

        const detail::Json* plan = document.find("plan");
        CHECK(plan != nullptr && plan->is_object());
        if (plan != nullptr) {
            const detail::Json* refusals = plan->find("refusals");
            CHECK(refusals != nullptr && refusals->items.size() == 1);
            CHECK_EQ(text_of(refusals->items[0], "code"), std::string("UNKNOWN_IS_NOT_STOPPED"));
        }

        // Nothing a status answer must never carry. The Core holds no secret;
        // the risk is a future field printing the command line that names where
        // one would live.
        for (const char* forbidden : {"argv", "token", "secret", "password", "credential",
                                      "/usr/bin/systemctl", "native-core-stage2", "ExecStart"}) {
            CHECK(text.find(forbidden) == std::string::npos);
        }
    }

    {
        // Before the first tick there is no uptime. `uptime_seconds: 0` would
        // read as "just started" rather than "has not looked yet", and those
        // are different answers.
        StatusSnapshot snapshot = base_snapshot();
        snapshot.uptime_seconds = -1;
        snapshot.ticks = 0;
        const std::string text = build_status_json(snapshot);
        detail::Json document;
        std::string error;
        detail::Reader reader(text);
        CHECK(reader.parse(document, error));
        CHECK(!has_key(document, "uptime_seconds"));
        CHECK(has_key(document, "last_tick"));
    }

    {
        StatusSnapshot snapshot = base_snapshot();
        snapshot.uptime_seconds = 90;
        snapshot.last_tick_monotonic = 12;
        snapshot.last_tick_duration_seconds = 1;
        const std::string text = build_status_json(snapshot);
        detail::Json document;
        std::string error;
        detail::Reader reader(text);
        CHECK(reader.parse(document, error));
        CHECK(has_key(document, "uptime_seconds"));
        const detail::Json* last = document.find("last_tick");
        CHECK(last != nullptr && has_key(*last, "duration_seconds"));
    }

    return check::report("test_sup_status");
}
