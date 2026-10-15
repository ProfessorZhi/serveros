// The Linux adapter. See `collector_readings.hpp` for the rules.
#include "corelite/collector_readings.hpp"

#include <string>
#include <vector>

#include "json_bridge.hpp"
#include "json_detail.hpp"

namespace nodecore::corelite {

namespace {

using Parsed = nodecore::supervisor::detail::Json;

//: What the snapshot block that answers `name` is called at the document's root,
//: or nullptr when the collector has no such block at all.
//:
//: This table is deliberately EXHAUSTIVE over `kObservationNames`: a name added
//: there and forgotten here is a granted observation that would silently stop
//: being reported, so the compiler refusing to build is the cheaper failure.
const char* snapshot_block_for(const std::string& name) {
    if (name == "cpu") return "cpu";
    if (name == "memory") return "memory";
    // Storage is two blocks — the mounted filesystems and the block-device
    // counters — and one observation, so both are handed over together.
    if (name == "storage.readonly") return "storage";
    // The collector samples `/proc/stat` and `/proc/meminfo` and stops there.
    // Everything below is a thing this platform COULD read
    // (`/proc/pressure`, `/proc/swaps`, `/proc/<pid>`) and no block exists for
    // yet, which is `NOT_IMPLEMENTED` — a different word from
    // `UNSUPPORTED_PLATFORM`, and the right one for Linux.
    if (name == "swap" || name == "psi.cpu" || name == "psi.memory" || name == "psi.io" ||
        name == "services" || name == "connector" || name == "gpu.readonly") {
        return nullptr;
    }
    return nullptr;
}

Reading unavailable(const std::string& name, const std::string& reason) {
    Reading reading;
    reading.name = name;
    reading.state = ReadingState::Unavailable;
    reading.reason = reason;
    reading.source = "nodecore/NodeResourceSnapshot v1";
    return reading;
}

//: The storage observation is assembled from two root blocks. If NEITHER is
//: present the whole observation is unavailable; if one of them is, the other is
//: carried as its own UNAVAILABLE object rather than dropped, so a reader can
//: tell "no disk counters" from "no filesystems listed".
bool assemble_storage(const Parsed& root, Parsed& out) {
    const Parsed* filesystems = root.find("filesystems");
    const Parsed* disk_io = root.find("disk_io");
    if (filesystems == nullptr && disk_io == nullptr) {
        return false;
    }
    out.kind = Parsed::Kind::Object;
    if (filesystems != nullptr) {
        out.members.emplace_back("filesystems", *filesystems);
    }
    if (disk_io != nullptr) {
        out.members.emplace_back("disk_io", *disk_io);
    }
    return true;
}

}  // namespace

std::vector<Reading> readings_from_snapshot(const Profile& profile, const std::string& snapshot_json,
                                            std::string& error) {
    error.clear();
    std::vector<Reading> readings;

    // The platform gate comes FIRST, before the document is even looked at: a
    // Linux adapter handed a Windows profile must refuse for that reason, not
    // for whatever the document happened to be missing.
    if (profile.platform != "linux") {
        error = "this is the Linux adapter and the profile declares platform \"" + profile.platform +
                "\"; a Windows Core's readings come from the Windows adapter";
        return readings;
    }

    Parsed root;
    nodecore::supervisor::detail::Reader reader(snapshot_json);
    if (!reader.parse(root, error)) {
        return readings;
    }
    if (!root.is_object()) {
        error = "the snapshot document is not a JSON object";
        return readings;
    }
    const Parsed* schema = root.find("schema");
    if (schema == nullptr || !schema->is_string() ||
        schema->text != "serveros/node-resource-snapshot.v1") {
        error = "the document is not a serveros/node-resource-snapshot.v1";
        return readings;
    }

    for (const Observation& observation : profile.observations) {
        if (!observation.authorized) {
            // Not granted by the owner: it does not appear. Enforced here as
            // well as in the status builder, so an ungranted name cannot even
            // reach the document.
            continue;
        }
        const char* block_name = snapshot_block_for(observation.name);
        if (block_name == nullptr) {
            readings.push_back(unavailable(observation.name, "NOT_IMPLEMENTED"));
            continue;
        }

        Parsed block;
        bool have_block = false;
        if (observation.name == "storage.readonly") {
            have_block = assemble_storage(root, block);
        } else {
            const Parsed* found = root.find(block_name);
            if (found != nullptr) {
                block = *found;
                have_block = true;
            }
        }
        if (!have_block) {
            readings.push_back(unavailable(observation.name, "NO_SOURCE"));
            continue;
        }

        // A block the collector itself marked unavailable is NO_SOURCE: the
        // adapter was asked and had nothing. The collector's own reason stays in
        // the snapshot; it is not promoted to a second vocabulary here.
        const Parsed* state = block.find("state");
        if (state != nullptr && state->is_string() && state->text != "PRESENT") {
            readings.push_back(unavailable(observation.name, "NO_SOURCE"));
            continue;
        }

        Reading reading;
        reading.name = observation.name;
        reading.state = ReadingState::Present;
        reading.source = "nodecore/NodeResourceSnapshot v1";
        const Parsed* observed_at = root.find("observed_at");
        if (observed_at != nullptr && observed_at->is_string()) {
            reading.observed_at = observed_at->text;
        }
        reading.value_json = nodecore::corelite::detail::to_nodecore(block).dump_compact();
        readings.push_back(reading);
    }

    return readings;
}

}  // namespace nodecore::corelite
