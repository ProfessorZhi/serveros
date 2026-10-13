#include "nodecore/snapshot.hpp"

#include <algorithm>
#include <cmath>
#include <map>

namespace nodecore {
namespace {

// The kernel counts /proc/diskstats in 512-byte units no matter what the device
// says its logical or physical sector size is. A reader who assumes the device's
// own sector size gets a number that is wrong by a factor of eight on a 4K
// device, so the constant is stated in the document rather than left implicit.
constexpr std::uint64_t kDiskstatsSectorBytes = 512;

using Unknowns = std::vector<std::pair<std::string, std::string>>;

// Every field that could not be measured is built through here, so that it is
// impossible to emit an UNAVAILABLE value without also listing it.
Json unavailable_field(Unknowns& sink, const std::string& field, const std::string& reason) {
    sink.emplace_back(field, reason);
    return Json::unavailable(reason);
}

Json optional_number(Unknowns& sink, const std::string& field,
                     const std::optional<double>& value, int precision,
                     const std::string& reason_when_absent) {
    if (value.has_value()) {
        return Json::number(*value, precision);
    }
    return unavailable_field(sink, field, reason_when_absent);
}

Json counter_block(Unknowns& sink, const std::string& prefix, const Reading& previous,
                   const Reading& current, std::optional<std::int64_t> interval) {
    const Delta delta = counter_delta(previous, current, interval);
    if (!delta.known) {
        return unavailable_field(sink, prefix, delta.reason);
    }
    return Json::uinteger(delta.value);
}

Json rate_block(Unknowns& sink, const std::string& prefix, const Reading& previous,
                const Reading& current, std::optional<std::int64_t> interval,
                const std::string& unit_suffix) {
    const Delta delta = counter_delta(previous, current, interval);
    const std::optional<double> rate = rate_per_second(delta, interval);
    return optional_number(sink, prefix, rate, 2,
                           "no per-second rate for " + unit_suffix + ": " +
                               (delta.known ? "the sample interval is not measurable"
                                            : delta.reason));
}

Reading reading_of(bool known, std::uint64_t value, const std::string& reason) {
    return known ? Reading::measured(value) : Reading::unknown(reason);
}

Json cpu_block(const Sample& before, const Sample& after, std::optional<std::int64_t> interval,
               Unknowns& sink) {
    Json block = Json::object();
    const bool both = before.cpu.present && after.cpu.present;
    if (!both) {
        const std::string reason = !before.cpu.present
                                       ? "first sample: " + before.cpu.reason
                                       : "second sample: " + after.cpu.reason;
        block.set("state", Json::string("UNAVAILABLE"));
        block.set("reason", Json::string(reason));
        sink.emplace_back("cpu", reason);
        return block;
    }

    const Reading total_before = Reading::measured(before.cpu.total());
    const Reading total_after = Reading::measured(after.cpu.total());
    const Reading busy_before = Reading::measured(before.cpu.busy());
    const Reading busy_after = Reading::measured(after.cpu.busy());
    const Delta total = counter_delta(total_before, total_after, interval);
    const Delta busy = counter_delta(busy_before, busy_after, interval);

    // A window in which no tick elapsed is not an idle machine, it is a window
    // that measured nothing. runtime/inventory.py:696 refuses it for the same
    // reason, and clamping it to 0% would report a sleeping host as a busy one
    // observed doing nothing.
    if (total.known && total.value == 0) {
        const std::string reason = "no jiffies elapsed between the two /proc/stat reads";
        block.set("state", Json::string("UNAVAILABLE"));
        block.set("reason", Json::string(reason));
        sink.emplace_back("cpu.used_percent", reason);
        return block;
    }

    block.set("state", Json::string(busy.known ? "PRESENT" : "UNAVAILABLE"));
    if (!busy.known) {
        block.set("reason", Json::string(busy.reason));
        sink.emplace_back("cpu.busy_jiffies", busy.reason);
    }
    block.set("sample_seconds", interval.has_value()
                                   ? Json::number(static_cast<double>(*interval) / 1e9, 6)
                                   : Json::unavailable("the sample interval is not measurable"));
    block.set("total_jiffies", total.known ? Json::uinteger(total.value)
                                           : Json::unavailable(total.reason));
    block.set("busy_jiffies", busy.known ? Json::uinteger(busy.value)
                                         : Json::unavailable(busy.reason));
    const std::optional<double> used_percent = percentage(busy, total);
    block.set("used_percent", optional_number(
                                  sink, "cpu.used_percent", used_percent, 1,
                                  "busy and total jiffy deltas are both required for a percentage"));

    Json counters = Json::object();
    counters.set("user", counter_block(sink, "cpu.counters.user", Reading::measured(before.cpu.user),
                                       Reading::measured(after.cpu.user), interval));
    counters.set("nice", counter_block(sink, "cpu.counters.nice", Reading::measured(before.cpu.nice),
                                       Reading::measured(after.cpu.nice), interval));
    counters.set("system",
                 counter_block(sink, "cpu.counters.system", Reading::measured(before.cpu.system),
                               Reading::measured(after.cpu.system), interval));
    counters.set("idle", counter_block(sink, "cpu.counters.idle", Reading::measured(before.cpu.idle),
                                       Reading::measured(after.cpu.idle), interval));
    counters.set("iowait",
                 counter_block(sink, "cpu.counters.iowait", Reading::measured(before.cpu.iowait),
                               Reading::measured(after.cpu.iowait), interval));
    counters.set("irq", counter_block(sink, "cpu.counters.irq", Reading::measured(before.cpu.irq),
                                      Reading::measured(after.cpu.irq), interval));
    counters.set("softirq",
                 counter_block(sink, "cpu.counters.softirq", Reading::measured(before.cpu.softirq),
                               Reading::measured(after.cpu.softirq), interval));
    counters.set("steal",
                 counter_block(sink, "cpu.counters.steal", Reading::measured(before.cpu.steal),
                               Reading::measured(after.cpu.steal), interval));
    block.set("counters", std::move(counters));
    return block;
}

Json memory_block(const Sample& after, Unknowns& sink) {
    Json block = Json::object();
    if (!after.memory.present) {
        const std::string reason = after.memory.reason;
        block.set("state", Json::string("UNAVAILABLE"));
        block.set("reason", Json::string(reason));
        sink.emplace_back("memory", reason);
        return block;
    }
    block.set("state", Json::string("PRESENT"));
    block.set("source", Json::string("/proc/meminfo"));
    // These are the machine's figures. On this shared host they are not the
    // collector account's share, and nothing here divides them up by account,
    // because no readable fact on this host would support that division.
    block.set("scope", Json::string("host-wide"));
    block.set("total_bytes", Json::uinteger(after.memory.total_bytes));
    block.set("available_bytes", Json::uinteger(after.memory.available_bytes));
    block.set("free_bytes", Json::uinteger(after.memory.free_bytes));
    const std::uint64_t used =
        after.memory.total_bytes >= after.memory.available_bytes
            ? after.memory.total_bytes - after.memory.available_bytes
            : 0;
    const std::optional<double> used_percent =
        after.memory.total_bytes > 0
            ? std::optional<double>(100.0 * static_cast<double>(used) /
                                    static_cast<double>(after.memory.total_bytes))
            : std::nullopt;
    block.set("used_percent",
              optional_number(sink, "memory.used_percent", used_percent, 1,
                              "MemTotal is zero, so a percentage has no denominator"));
    return block;
}

Json filesystem_row(const FilesystemFacts& facts, Unknowns& sink) {
    Json row = Json::object();
    const std::string prefix = "filesystems." + facts.role;
    row.set("role", Json::string(facts.role));
    row.set("path", Json::string(facts.path));
    if (facts.mounted) {
        row.set("source", Json::string(facts.source));
        row.set("fstype", Json::string(facts.fstype));
        // The path a role is asked about and the mount it lands on are different
        // strings, and the second is the one a policy acts on.
        row.set("mount_point", Json::string(facts.mount_point));
    } else {
        row.set("mount", unavailable_field(sink, prefix + ".mount", facts.mount_reason));
    }

    if (!facts.space_known) {
        row.set("state", Json::string("UNAVAILABLE"));
        row.set("reason", Json::string(facts.space_reason));
        sink.emplace_back(prefix, facts.space_reason);
        return row;
    }

    row.set("state", Json::string("PRESENT"));
    const std::uint64_t used = facts.total_bytes >= facts.free_bytes
                                   ? facts.total_bytes - facts.free_bytes
                                   : 0;
    // df's own arithmetic: capacity excludes the blocks reserved for root, so a
    // filesystem that is full for everyone else does not read as 100% used.
    const std::uint64_t capacity = used + facts.available_bytes;
    row.set("total_bytes", Json::uinteger(facts.total_bytes));
    row.set("free_bytes", Json::uinteger(facts.free_bytes));
    row.set("available_bytes", Json::uinteger(facts.available_bytes));
    row.set("used_bytes", Json::uinteger(used));
    const std::optional<double> used_percent =
        capacity > 0 ? std::optional<double>(100.0 * static_cast<double>(used) /
                                             static_cast<double>(capacity))
                     : std::nullopt;
    row.set("used_percent",
            optional_number(sink, prefix + ".used_percent", used_percent, 1,
                            "the filesystem reports no capacity, so a percentage has no denominator"));

    Json inodes = Json::object();
    if (facts.inodes_known) {
        inodes.set("state", Json::string("PRESENT"));
        const std::uint64_t inodes_used = facts.inodes_total >= facts.inodes_free
                                              ? facts.inodes_total - facts.inodes_free
                                              : 0;
        inodes.set("total", Json::uinteger(facts.inodes_total));
        inodes.set("free", Json::uinteger(facts.inodes_free));
        inodes.set("used", Json::uinteger(inodes_used));
        const std::optional<double> inodes_percent =
            facts.inodes_total > 0
                ? std::optional<double>(100.0 * static_cast<double>(inodes_used) /
                                        static_cast<double>(facts.inodes_total))
                : std::nullopt;
        inodes.set("used_percent", optional_number(
                                       sink, prefix + ".inodes.used_percent", inodes_percent, 1,
                                       "the filesystem reports no inode budget, so a percentage has "
                                       "no denominator"));
    } else {
        inodes.set("state", Json::string("UNAVAILABLE"));
        inodes.set("reason", Json::string(facts.inodes_reason));
        sink.emplace_back(prefix + ".inodes", facts.inodes_reason);
    }
    row.set("inodes", std::move(inodes));
    return row;
}

const DiskFacts* find_disk(const std::vector<DiskFacts>& disks, const std::string& name) {
    for (const DiskFacts& disk : disks) {
        if (disk.name == name) {
            return &disk;
        }
    }
    return nullptr;
}

Json disk_io_block(const Sample& before, const Sample& after,
                   std::optional<std::int64_t> interval, Unknowns& sink) {
    Json block = Json::object();
    Json roles = Json::array();
    Json unmapped = Json::array();
    std::vector<std::string> attributed_devices;

    for (const FilesystemFacts& facts : after.filesystems) {
        if (!facts.mounted) {
            continue;
        }
        const auto device = reliable_device_name(facts.source);
        if (!device.has_value()) {
            Json row = Json::object();
            row.set("role", Json::string(facts.role));
            row.set("source", Json::string(facts.source));
            row.set("reason", Json::string(
                                  "this mount's source is not a kernel block device name, so no "
                                  "device counter belongs to it"));
            unmapped.push(std::move(row));
            sink.emplace_back("disk_io." + facts.role,
                              "no reliable block-device mapping for source " + facts.source);
            continue;
        }

        Json row = Json::object();
        row.set("role", Json::string(facts.role));
        row.set("name", Json::string(*device));
        attributed_devices.push_back(*device);

        const DiskFacts* previous = find_disk(before.disks, *device);
        const DiskFacts* current = find_disk(after.disks, *device);
        // A device present in one sample and absent from the other was created,
        // removed or renamed in between. Its counters are not comparable across
        // that boundary, and the difference of its two lifetimes is not a rate.
        if (previous == nullptr || current == nullptr || !previous->present || !current->present) {
            std::string reason;
            if (previous == nullptr || current == nullptr) {
                reason = "this device appears in only one of the two samples";
            } else if (!previous->present) {
                reason = "first sample: " + previous->reason;
            } else {
                reason = "second sample: " + current->reason;
            }
            row.set("read_bytes", unavailable_field(sink, "disk_io." + facts.role + ".read_bytes", reason));
            row.set("write_bytes",
                    unavailable_field(sink, "disk_io." + facts.role + ".write_bytes", reason));
            roles.push(std::move(row));
            continue;
        }

        // The kernel counts sectors; this document reports bytes. The conversion
        // happens here, once, before any arithmetic: a rate taken from the sector
        // reading would be off by 512 and would still be labelled bytes.
        const Reading read_before =
            Reading::measured(previous->sectors_read * kDiskstatsSectorBytes);
        const Reading read_after = Reading::measured(current->sectors_read * kDiskstatsSectorBytes);
        const Reading write_before =
            Reading::measured(previous->sectors_written * kDiskstatsSectorBytes);
        const Reading write_after =
            Reading::measured(current->sectors_written * kDiskstatsSectorBytes);

        // Byte counters are exact integers, so they are emitted as integers
        // rather than as doubles that would lose precision above 2^53 on a busy
        // device.
        const Delta read_bytes = counter_delta(read_before, read_after, interval);
        const Delta write_bytes = counter_delta(write_before, write_after, interval);
        row.set("read_bytes",
                read_bytes.known
                    ? Json::uinteger(read_bytes.value)
                    : unavailable_field(sink, "disk_io." + facts.role + ".read_bytes",
                                        read_bytes.reason));
        row.set("write_bytes",
                write_bytes.known
                    ? Json::uinteger(write_bytes.value)
                    : unavailable_field(sink, "disk_io." + facts.role + ".write_bytes",
                                        write_bytes.reason));
        row.set("read_bytes_per_second",
                rate_block(sink, "disk_io." + facts.role + ".read_bytes_per_second", read_before,
                           read_after, interval, "read_bytes"));
        row.set("write_bytes_per_second",
                rate_block(sink, "disk_io." + facts.role + ".write_bytes_per_second", write_before,
                           write_after, interval, "write_bytes"));
        roles.push(std::move(row));
    }

    Json not_attributed = Json::array();
    for (const DiskFacts& disk : after.disks) {
        if (std::find(attributed_devices.begin(), attributed_devices.end(), disk.name) !=
            attributed_devices.end()) {
            continue;
        }
        // The kernel lists every block device, including the ones this snapshot
        // has no role for. They are named rather than dropped, so "no row" is
        // never mistaken for "no device".
        not_attributed.push(Json::string(disk.name));
    }

    block.set("sample_seconds",
              interval.has_value() ? Json::number(static_cast<double>(*interval) / 1e9, 6)
                                   : Json::unavailable("the sample interval is not measurable"));
    block.set("sector_size_bytes", Json::uinteger(kDiskstatsSectorBytes));
    block.set("roles", std::move(roles));
    block.set("unmapped", std::move(unmapped));
    block.set("devices_without_a_role", std::move(not_attributed));
    block.set("note",
              Json::string("sector_size_bytes is the kernel's fixed 512-byte unit for "
                           "/proc/diskstats, not the device's logical sector size"));
    return block;
}

Json interface_row(const InterfaceFacts& before, const InterfaceFacts& after,
                   std::optional<std::int64_t> interval, Unknowns& sink) {
    Json row = Json::object();
    const std::string prefix = "interfaces." + after.name;
    row.set("name", Json::string(after.name));
    row.set("loopback", Json::boolean(after.loopback));

    if (after.operstate_known) {
        row.set("operstate", Json::string(after.operstate));
        row.set("active", Json::boolean(after.operstate == "up"));
    } else {
        row.set("active", unavailable_field(sink, prefix + ".active", after.operstate_reason));
    }

    if (!after.kind.empty()) {
        row.set("kind", Json::string(after.kind));
        // The sysfs path the classification came from, so the reader can disagree
        // with the judgement without having to re-derive it.
        row.set("kind_source", Json::string(after.kind_source));
    } else {
        row.set("kind", unavailable_field(sink, prefix + ".kind", after.kind_source));
    }

    const Reading rx_before = reading_of(before.stats_present, before.rx_bytes, before.stats_reason);
    const Reading rx_after = reading_of(after.stats_present, after.rx_bytes, after.stats_reason);
    const Reading tx_before = reading_of(before.stats_present, before.tx_bytes, before.stats_reason);
    const Reading tx_after = reading_of(after.stats_present, after.tx_bytes, after.stats_reason);

    const Delta rx = counter_delta(rx_before, rx_after, interval);
    const Delta tx = counter_delta(tx_before, tx_after, interval);
    row.set("rx_bytes", rx.known ? Json::uinteger(rx.value)
                                 : unavailable_field(sink, prefix + ".rx_bytes", rx.reason));
    row.set("tx_bytes", tx.known ? Json::uinteger(tx.value)
                                 : unavailable_field(sink, prefix + ".tx_bytes", tx.reason));
    const Json rx_rate = rate_block(sink, prefix + ".rx_bytes_per_second", rx_before, rx_after,
                                    interval, "rx_bytes");
    const Json tx_rate = rate_block(sink, prefix + ".tx_bytes_per_second", tx_before, tx_after,
                                    interval, "tx_bytes");
    row.set("rx_bytes_per_second", rx_rate);
    row.set("tx_bytes_per_second", tx_rate);

    if (!after.speed_known) {
        row.set("speed_mbps",
                unavailable_field(sink, prefix + ".speed_mbps", after.speed_reason));
    } else {
        row.set("speed_mbps", Json::integer(after.speed_mbps));
    }

    // Link utilisation is only meaningful against a link speed, and on this host
    // the interface that carries real traffic is virtio_net, which reports none.
    // docs/NODE-OBSERVABILITY.md:35 states the rule this follows: no denominator,
    // no utilisation figure.
    const std::optional<double> rx_rate_value = rate_per_second(rx, interval);
    const std::optional<double> tx_rate_value = rate_per_second(tx, interval);
    auto utilisation = [&](const std::optional<double>& rate,
                           const std::string& field) -> Json {
        if (!after.speed_known) {
            return unavailable_field(sink, prefix + "." + field,
                                     "no interface speed, so there is no denominator: " +
                                         after.speed_reason);
        }
        if (!rate.has_value()) {
            return unavailable_field(sink, prefix + "." + field,
                                     "no measurable byte rate for this interface");
        }
        const double megabits = *rate * 8.0 / 1e6;
        return Json::number(100.0 * megabits / static_cast<double>(after.speed_mbps), 2);
    };
    row.set("rx_utilisation_percent", utilisation(rx_rate_value, "rx_utilisation_percent"));
    row.set("tx_utilisation_percent", utilisation(tx_rate_value, "tx_utilisation_percent"));
    return row;
}

Json interfaces_block(const Sample& before, const Sample& after,
                      std::optional<std::int64_t> interval, Unknowns& sink) {
    Json block = Json::array();
    std::map<std::string, const InterfaceFacts*> previous_by_name;
    for (const InterfaceFacts& facts : before.interfaces) {
        previous_by_name[facts.name] = &facts;
    }
    for (const InterfaceFacts& facts : after.interfaces) {
        const auto found = previous_by_name.find(facts.name);
        if (found == previous_by_name.end()) {
            // An interface that did not exist at the first sample has no first
            // reading, and the kernel's counters for it start at zero — which is
            // not the same as having measured a zero rate.
            InterfaceFacts absent;
            absent.name = facts.name;
            absent.stats_reason = "this interface did not exist at the first sample";
            absent.operstate_known = false;
            absent.operstate_reason = "this interface did not exist at the first sample";
            block.push(interface_row(absent, facts, interval, sink));
            continue;
        }
        block.push(interface_row(*found->second, facts, interval, sink));
    }
    return block;
}

Json attribution_block(const Sample& after, Unknowns& sink) {
    Json block = Json::object();
    block.set("uid", Json::integer(after.attribution.uid));
    block.set("scope", Json::string("self"));
    // The collector reads its own uid, its own cgroup path and that cgroup's own
    // usage counter, and nothing else. It does not read another account's
    // processes, command lines, environments or files, and it does not divide a
    // host-wide figure up by account, because no readable fact here would
    // support that division.
    block.set("note",
              Json::string("only this process's uid, cgroup path and its own cgroup memory "
                           "counter are read; no other account's processes, argv or environment "
                           "are inspected"));
    if (after.attribution.cgroup_known) {
        block.set("cgroup_path", Json::string(after.attribution.cgroup_path));
        block.set("cgroup_version", Json::integer(after.attribution.cgroup_version));
    } else {
        block.set("cgroup_path",
                  unavailable_field(sink, "attribution.cgroup_path",
                                    after.attribution.cgroup_reason));
    }
    if (after.attribution.memory_known) {
        block.set("memory_current_bytes", Json::uinteger(after.attribution.memory_current_bytes));
    } else {
        block.set("memory_current_bytes",
                  unavailable_field(sink, "attribution.memory_current_bytes",
                                    after.attribution.memory_reason));
    }
    return block;
}

}  // namespace

std::optional<std::int64_t> interval_nanoseconds(const Sample& before, const Sample& after,
                                                 std::string& reason) {
    // A reboot resets every counter in this document. The boot id is the only
    // evidence available that the two samples describe the same uptime, and
    // without it a delta taken across a restart is a number with no meaning.
    if (!before.boot_id.empty() && !after.boot_id.empty() && before.boot_id != after.boot_id) {
        reason =
            "the boot id changed between the two samples, so the host restarted and its counters "
            "are not comparable";
        return std::nullopt;
    }
    if (!before.monotonic_seconds.has_value()) {
        reason = "the first sample has no /proc/uptime reading: " + before.monotonic_reason;
        return std::nullopt;
    }
    if (!after.monotonic_seconds.has_value()) {
        reason = "the second sample has no /proc/uptime reading: " + after.monotonic_reason;
        return std::nullopt;
    }
    const double seconds = *after.monotonic_seconds - *before.monotonic_seconds;
    if (!(seconds > 0.0)) {
        reason = "the two /proc/uptime readings share no measurable interval";
        return std::nullopt;
    }
    return static_cast<std::int64_t>(seconds * 1e9);
}

Json build_snapshot(const Sample& before, const Sample& after, const SnapshotOptions& options) {
    Unknowns unknowns;
    std::string interval_reason;
    const std::optional<std::int64_t> interval =
        interval_nanoseconds(before, after, interval_reason);

    Json root = Json::object();
    root.set("schema", Json::string("serveros/node-resource-snapshot.v1"));

    Json collector = Json::object();
    collector.set("name", Json::string("serveros-node-core-native"));
    collector.set("version", Json::string(options.collector_version));
    collector.set("language", Json::string("c++20"));
    // False means these facts came from fixtures. A snapshot built from test data
    // must never be readable as a reading of this host.
    collector.set("live", Json::boolean(options.live));
    root.set("collector", std::move(collector));

    root.set("observed_at", Json::string(iso8601_utc(after.utc)));
    root.set("sample_started_at", Json::string(iso8601_utc(before.utc)));
    root.set("sample_seconds",
             interval.has_value() ? Json::number(static_cast<double>(*interval) / 1e9, 6)
                                  : unavailable_field(unknowns, "sample_seconds", interval_reason));
    root.set("boot_id", after.boot_id.empty() ? unavailable_field(unknowns, "boot_id",
                                                                  "/proc/sys/kernel/random/boot_id "
                                                                  "was not readable")
                                               : Json::string(after.boot_id));

    Json host = Json::object();
    host.set("scope", Json::string("host-wide"));
    host.set("note",
             Json::string("CPU, memory, filesystem and device counters describe the whole shared "
                          "machine; they are not one account's share of it"));
    root.set("host", std::move(host));
    root.set("monotonic_seconds",
             after.monotonic_seconds.has_value()
                 ? Json::number(*after.monotonic_seconds, 6)
                 : unavailable_field(unknowns, "monotonic_seconds", after.monotonic_reason));

    root.set("cpu", cpu_block(before, after, interval, unknowns));
    root.set("memory", memory_block(after, unknowns));

    Json filesystems = Json::object();
    Json role_rows = Json::array();
    for (const FilesystemFacts& facts : after.filesystems) {
        role_rows.push(filesystem_row(facts, unknowns));
    }
    filesystems.set("roles", std::move(role_rows));
    root.set("filesystems", std::move(filesystems));

    root.set("disk_io", disk_io_block(before, after, interval, unknowns));
    root.set("interfaces", interfaces_block(before, after, interval, unknowns));
    root.set("attribution", attribution_block(after, unknowns));

    // The list of what is missing, in one place. A reader who wants to know what
    // this snapshot does not know should not have to walk it looking for the
    // shape of an unavailable value.
    Json unknown_rows = Json::array();
    std::sort(unknowns.begin(), unknowns.end());
    for (const auto& [field, reason] : unknowns) {
        Json row = Json::object();
        row.set("field", Json::string(field));
        row.set("reason", Json::string(reason));
        unknown_rows.push(std::move(row));
    }
    root.set("unknowns", std::move(unknown_rows));
    return root;
}

}  // namespace nodecore
