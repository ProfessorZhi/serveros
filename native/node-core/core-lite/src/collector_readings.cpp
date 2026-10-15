// The Linux adapter, and the projection that keeps an untrusted document out of
// the status. See `collector_readings.hpp` for the rules.
#include "corelite/collector_readings.hpp"

#include <cmath>
#include <cstdint>
#include <string>
#include <vector>

#include "json_detail.hpp"
#include "nodecore/json.hpp"

namespace nodecore::corelite {

namespace {

using Parsed = nodecore::supervisor::detail::Json;
using Json = nodecore::Json;

//: How many rows a projected list may carry. A document is input, and an input
//: can be a million rows; a status that grew to match is a status nobody reads
//: and a memory bound this program chose not to have.
constexpr std::size_t kMaxRowsPerBlock = 64;

//: A role name the collector uses (`ROOT_FS`, `DATA_FS`) is a short identifier.
//: Anything else — a path, an account, a long free-form string — is not a role,
//: and is refused rather than published under a name we would be inventing.
bool safe_role_name(const std::string& name) {
    if (name.empty() || name.size() > 32) {
        return false;
    }
    for (const char c : name) {
        const bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                        (c >= '0' && c <= '9') || c == '_' || c == '-';
        if (!ok) {
            return false;
        }
    }
    return true;
}

//: A JSON number, whether the document wrote it as an integer or a real. Bool is
//: NOT a number here: `true` would otherwise arrive as 1.
bool as_number(const Parsed& node, double& out) {
    if (node.kind == Parsed::Kind::Int) {
        out = static_cast<double>(node.integer);
        return true;
    }
    if (node.kind == Parsed::Kind::Real) {
        out = node.real;
        return std::isfinite(out);
    }
    return false;
}

//: A byte count: an integer, non-negative. A byte count written as `1.5` is not
//: a byte count, and is refused rather than rounded into one.
bool as_bytes(const Parsed& node, std::uint64_t& out) {
    if (node.kind != Parsed::Kind::Int || node.integer < 0) {
        return false;
    }
    out = static_cast<std::uint64_t>(node.integer);
    return true;
}

//: A percentage: a number in [0, 100]. The bound is stated because 340% is not a
//: high reading, it is a wrong one, and publishing it would make a Fleet reader
//: build an alert on it.
bool as_percent(const Parsed& node, double& out) {
    if (!as_number(node, out)) {
        return false;
    }
    return out >= 0.0 && out <= 100.0;
}

bool member(const Parsed& object, const char* key, Parsed& out) {
    const Parsed* found = object.find(key);
    if (found == nullptr) {
        return false;
    }
    out = *found;
    return true;
}

//: The collector marks a block it could not measure with `state: UNAVAILABLE`.
//: That is NO_SOURCE — the adapter was asked and had nothing — and the
//: collector's own reason stays in the snapshot rather than being promoted to a
//: second vocabulary here.
bool block_is_present(const Parsed& block) {
    const Parsed* state = block.find("state");
    if (state == nullptr) {
        // The storage aggregate is assembled from two blocks that carry no
        // `state` of their own; treated as present, and every row is validated.
        return true;
    }
    return state->is_string() && state->text == "PRESENT";
}

//: `used_bytes` when the collector states it, otherwise the collector's OWN
//: arithmetic (`total >= available ? total - available : 0`, which is what
//: `snapshot.cpp` uses for memory). Derived only when both operands are present,
//: and derived the same way the producer derives it, so this is not a second
//: opinion about what "used" means.
bool used_bytes(const Parsed& row, std::uint64_t& out) {
    Parsed stated;
    if (member(row, "used_bytes", stated)) {
        return as_bytes(stated, out);
    }
    std::uint64_t total = 0;
    std::uint64_t available = 0;
    Parsed total_node;
    Parsed available_node;
    if (!member(row, "total_bytes", total_node) || !as_bytes(total_node, total) ||
        !member(row, "available_bytes", available_node) || !as_bytes(available_node, available)) {
        return false;
    }
    out = total >= available ? total - available : 0;
    return true;
}

// --- the projections -------------------------------------------------------
//
// Each returns false when the block cannot be projected in full, and the caller
// turns that into UNAVAILABLE / NO_SOURCE. Nothing partial is ever returned.

bool project_cpu(const Parsed& block, Json& out) {
    Parsed used_percent;
    double percent = 0.0;
    if (!member(block, "used_percent", used_percent) || !as_percent(used_percent, percent)) {
        return false;
    }
    out = Json::object();
    out.set("used_percent", Json::number(percent, 2));
    return true;
}

bool project_memory(const Parsed& block, Json& out) {
    Parsed total_node;
    Parsed available_node;
    Parsed free_node;
    Parsed used_percent;
    std::uint64_t total = 0;
    std::uint64_t available = 0;
    std::uint64_t free_bytes = 0;
    double percent = 0.0;
    if (!member(block, "total_bytes", total_node) || !as_bytes(total_node, total) ||
        !member(block, "available_bytes", available_node) || !as_bytes(available_node, available) ||
        !member(block, "free_bytes", free_node) || !as_bytes(free_node, free_bytes) ||
        !member(block, "used_percent", used_percent) || !as_percent(used_percent, percent)) {
        return false;
    }
    out = Json::object();
    out.set("total_bytes", Json::uinteger(total));
    out.set("available_bytes", Json::uinteger(available));
    out.set("free_bytes", Json::uinteger(free_bytes));
    out.set("used_percent", Json::number(percent, 2));
    return true;
}

//: One filesystem role: the role name and three byte counts. No path, no mount
//: point, no device, no fs type, no inode counts.
bool project_filesystem_row(const Parsed& row, Json& out) {
    Parsed role;
    if (!member(row, "role", role) || !role.is_string() || !safe_role_name(role.text)) {
        return false;
    }
    if (!block_is_present(row)) {
        return false;
    }
    Parsed total_node;
    Parsed available_node;
    std::uint64_t total = 0;
    std::uint64_t available = 0;
    std::uint64_t used = 0;
    if (!member(row, "total_bytes", total_node) || !as_bytes(total_node, total) ||
        !member(row, "available_bytes", available_node) || !as_bytes(available_node, available) ||
        !used_bytes(row, used)) {
        return false;
    }
    out = Json::object();
    out.set("role", Json::string(role.text));
    out.set("total_bytes", Json::uinteger(total));
    out.set("available_bytes", Json::uinteger(available));
    out.set("used_bytes", Json::uinteger(used));
    return true;
}

//: One device-io role: the role name and the two byte counters. The device NAME
//: is dropped on purpose — it is a raw hardware identifier and the role already
//: identifies which filesystem this is about.
bool project_disk_io_row(const Parsed& row, Json& out) {
    Parsed role;
    if (!member(row, "role", role) || !role.is_string() || !safe_role_name(role.text)) {
        return false;
    }
    Parsed read_node;
    Parsed write_node;
    std::uint64_t read_bytes = 0;
    std::uint64_t write_bytes = 0;
    if (!member(row, "read_bytes", read_node) || !as_bytes(read_node, read_bytes) ||
        !member(row, "write_bytes", write_node) || !as_bytes(write_node, write_bytes)) {
        return false;
    }
    out = Json::object();
    out.set("role", Json::string(role.text));
    out.set("read_bytes", Json::uinteger(read_bytes));
    out.set("write_bytes", Json::uinteger(write_bytes));
    return true;
}

bool project_rows(const Parsed& block, const char* list_key, bool (*project_row)(const Parsed&, Json&),
                  const char* out_key, Json& out) {
    const Parsed* list = block.find(list_key);
    if (list == nullptr) {
        return false;
    }
    if (!list->is_array() || list->items.size() > kMaxRowsPerBlock) {
        return false;
    }
    Json rows = Json::array();
    for (const Parsed& row : list->items) {
        Json projected;
        if (!row.is_object() || !project_row(row, projected)) {
            // One unusable row makes the whole aggregate unusable. Silently
            // skipping it would report a machine with fewer filesystems than it
            // has, and the reader would have no way to tell that from a machine
            // that has them.
            return false;
        }
        rows.push(projected);
    }
    out = Json::object();
    out.set(out_key, rows);
    return true;
}

//: Storage is two blocks and one observation. Either block may be absent, and
//: what is present is projected; if NEITHER is, the observation has no source.
bool project_storage(const Parsed& root, Json& out) {
    const Parsed* filesystems = root.find("filesystems");
    const Parsed* disk_io = root.find("disk_io");
    if (filesystems == nullptr && disk_io == nullptr) {
        return false;
    }
    Json filesystem_rows;
    Json disk_rows;
    const bool have_filesystems =
        filesystems != nullptr && filesystems->is_object() &&
        project_rows(*filesystems, "roles", project_filesystem_row, "roles", filesystem_rows);
    const bool have_disk_io =
        disk_io != nullptr && disk_io->is_object() &&
        project_rows(*disk_io, "roles", project_disk_io_row, "roles", disk_rows);
    if (!have_filesystems && !have_disk_io) {
        return false;
    }
    out = Json::object();
    if (have_filesystems) {
        out.set("filesystems", filesystem_rows);
    }
    if (have_disk_io) {
        out.set("disk_io", disk_rows);
    }
    return true;
}

//: What the COLLECTOR is designed to answer for `name`, or nullptr when it has
//: no block for it at all. Deliberately exhaustive over `kObservationNames`: a
//: name added there and forgotten here would be a granted observation that
//: silently stopped being answered.
//:
//: This is a DESIGN question ("does a block exist for this?"), and it is kept
//: separate from "is the block in THIS document?" — which is a fact about the
//: input and a different reason word.
//: `storage.readonly` is two blocks and one observation, so it is named here and
//: assembled in `project_storage`.
const char* designed_block_for(const std::string& name) {
    if (name == "cpu") return "cpu";
    if (name == "memory") return "memory";
    if (name == "storage.readonly") return "storage";
    // The collector samples `/proc/stat` and `/proc/meminfo` and stops there.
    // Everything below is a thing Linux COULD read (`/proc/pressure`,
    // `/proc/swaps`, `/proc/<pid>`) and no block exists for yet.
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

}  // namespace

SnapshotReadings readings_from_snapshot(const Profile& profile, const std::string& snapshot_json,
                                        std::string& error) {
    error.clear();
    SnapshotReadings answer;

    // The platform gate comes FIRST, before the document is even looked at: a
    // Linux adapter handed a Windows profile must refuse for that reason, not
    // for whatever the document happened to be missing.
    if (profile.platform != "linux") {
        error = "this is the Linux adapter and the profile declares platform \"" + profile.platform +
                "\"; a Windows Core's readings come from the Windows adapter";
        return answer;
    }

    Parsed root;
    nodecore::supervisor::detail::Reader reader(snapshot_json);
    if (!reader.parse(root, error)) {
        return answer;
    }
    if (!root.is_object()) {
        error = "the snapshot document is not a JSON object";
        return answer;
    }
    const Parsed* schema = root.find("schema");
    if (schema == nullptr || !schema->is_string() ||
        schema->text != "serveros/node-resource-snapshot.v1") {
        error = "the document is not a serveros/node-resource-snapshot.v1";
        return answer;
    }

    // The document's own sample time, copied. An absent or non-string value
    // leaves it empty, and the status then states no time rather than one this
    // program invented.
    const Parsed* observed_at = root.find("observed_at");
    if (observed_at != nullptr && observed_at->is_string()) {
        answer.observed_at = observed_at->text;
    }

    for (const Observation& observation : profile.observations) {
        if (!observation.authorized) {
            // Not granted by the owner: it does not appear. Enforced here as
            // well as in the status builder, so an ungranted name cannot even
            // reach the document.
            continue;
        }

        // Does a block exist for this name AT ALL? If not, the platform could
        // read it and nobody has written the block — NOT_IMPLEMENTED, a
        // different word from UNSUPPORTED_PLATFORM, and the right one for Linux,
        // whose platform is not the problem.
        const char* designed = designed_block_for(observation.name);
        if (designed == nullptr) {
            answer.readings.push_back(unavailable(observation.name, "NOT_IMPLEMENTED"));
            continue;
        }

        Json projected;
        bool have_projection = false;
        if (observation.name == "storage.readonly") {
            have_projection = project_storage(root, projected);
        } else {
            const Parsed* block = root.find(designed);
            if (block != nullptr && block->is_object() && block_is_present(*block)) {
                have_projection = (observation.name == "cpu") ? project_cpu(*block, projected)
                                                             : project_memory(*block, projected);
            }
        }

        if (!have_projection) {
            // Either the block is not in this document, or the collector marked
            // it unavailable, or it was present and did not survive the
            // projection. All three mean this adapter has nothing USABLE to say,
            // which is NO_SOURCE — and none of them means zero.
            answer.readings.push_back(unavailable(observation.name, "NO_SOURCE"));
            continue;
        }

        Reading reading;
        reading.name = observation.name;
        reading.state = ReadingState::Present;
        reading.source = "nodecore/NodeResourceSnapshot v1";
        reading.observed_at = answer.observed_at;
        reading.value = projected;
        reading.has_value = true;
        answer.readings.push_back(reading);
    }

    return answer;
}

}  // namespace nodecore::corelite
