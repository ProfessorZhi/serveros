#include "nodecore/sample.hpp"

#include <algorithm>
#include <cstdlib>

namespace nodecore {
namespace {

std::string trim(const std::string& raw) {
    std::size_t begin = 0;
    std::size_t end = raw.size();
    while (begin < end && (raw[begin] == ' ' || raw[begin] == '\t' || raw[begin] == '\r' ||
                           raw[begin] == '\n')) {
        ++begin;
    }
    while (end > begin && (raw[end - 1] == ' ' || raw[end - 1] == '\t' || raw[end - 1] == '\r' ||
                           raw[end - 1] == '\n')) {
        --end;
    }
    return raw.substr(begin, end - begin);
}

std::vector<std::string> split_whitespace(const std::string& line) {
    std::vector<std::string> tokens;
    std::size_t index = 0;
    while (index < line.size()) {
        while (index < line.size() && (line[index] == ' ' || line[index] == '\t' ||
                                       line[index] == '\r' || line[index] == '\n')) {
            ++index;
        }
        const std::size_t begin = index;
        while (index < line.size() && line[index] != ' ' && line[index] != '\t' &&
               line[index] != '\r' && line[index] != '\n') {
            ++index;
        }
        if (index > begin) {
            tokens.push_back(line.substr(begin, index - begin));
        }
    }
    return tokens;
}

std::vector<std::string> split_lines(const std::string& text) {
    std::vector<std::string> lines;
    std::size_t begin = 0;
    while (begin <= text.size()) {
        const std::size_t newline = text.find('\n', begin);
        const std::size_t end = newline == std::string::npos ? text.size() : newline;
        if (end > begin) {
            lines.push_back(text.substr(begin, end - begin));
        }
        if (newline == std::string::npos) {
            break;
        }
        begin = newline + 1;
    }
    return lines;
}

// Strict: a trailing character that is not a digit makes the whole token
// unparseable. std::stoull would silently accept "12abc" as 12, and a counter
// read from a surprising place is worse than no counter at all.
std::optional<std::uint64_t> to_u64(const std::string& token) {
    if (token.empty()) {
        return std::nullopt;
    }
    std::uint64_t value = 0;
    for (const char ch : token) {
        if (ch < '0' || ch > '9') {
            return std::nullopt;
        }
        const std::uint64_t digit = static_cast<std::uint64_t>(ch - '0');
        if (value > (UINT64_MAX - digit) / 10) {
            return std::nullopt;
        }
        value = value * 10 + digit;
    }
    return value;
}

std::optional<std::int64_t> to_i64(const std::string& token) {
    if (token.empty()) {
        return std::nullopt;
    }
    const bool negative = token[0] == '-';
    const std::string digits = negative ? token.substr(1) : token;
    const auto magnitude = to_u64(digits);
    if (!magnitude.has_value() || *magnitude > static_cast<std::uint64_t>(INT64_MAX)) {
        return std::nullopt;
    }
    return negative ? -static_cast<std::int64_t>(*magnitude) : static_cast<std::int64_t>(*magnitude);
}

bool starts_with(const std::string& text, const std::string& prefix) {
    return text.size() >= prefix.size() && text.compare(0, prefix.size(), prefix) == 0;
}

// The longest mount target that is an ancestor of `path`, which is the mount the
// path actually lands on. Matching by prefix rather than equality is what lets
// "/data/artifacts" be answered by the "/data" row.
std::optional<ProcMount> mount_for_path(const std::vector<ProcMount>& mounts,
                                        const std::string& path) {
    std::optional<ProcMount> best;
    for (const ProcMount& mount : mounts) {
        const bool matches =
            mount.target == path ||
            (starts_with(path, mount.target) &&
             (mount.target == "/" || path.size() > mount.target.size()) &&
             (mount.target == "/" || path[mount.target.size()] == '/'));
        if (!matches) {
            continue;
        }
        if (!best.has_value() || mount.target.size() > best->target.size()) {
            best = mount;
        }
    }
    return best;
}

}  // namespace

SampleRequest SampleRequest::default_request() {
    SampleRequest request;
    request.filesystems = {{"ROOT_FS", "/"}, {"DATA_FS", "/data"}};
    return request;
}

CpuTimes parse_proc_stat(const std::string& text) {
    for (const std::string& line : split_lines(text)) {
        if (!starts_with(line, "cpu ")) {
            // "cpu0", "cpu12" and "cpu  " all differ here: only the aggregate
            // line is "cpu" followed by a space.
            continue;
        }
        const std::vector<std::string> tokens = split_whitespace(line);
        // tokens[0] is "cpu"; the eight counters follow.
        if (tokens.size() < 5) {
            CpuTimes times;
            times.reason = "aggregate cpu line has fewer than four counters";
            return times;
        }
        std::uint64_t values[8] = {0, 0, 0, 0, 0, 0, 0, 0};
        for (std::size_t index = 0; index < 8 && index + 1 < tokens.size(); ++index) {
            const auto parsed = to_u64(tokens[index + 1]);
            if (!parsed.has_value()) {
                CpuTimes times;
                times.reason = "aggregate cpu line has a non-numeric counter";
                return times;
            }
            values[index] = *parsed;
        }
        CpuTimes times;
        times.present = true;
        times.user = values[0];
        times.nice = values[1];
        times.system = values[2];
        times.idle = values[3];
        times.iowait = values[4];
        times.irq = values[5];
        times.softirq = values[6];
        times.steal = values[7];
        return times;
    }
    CpuTimes times;
    times.reason = "no aggregate 'cpu ' line in /proc/stat";
    return times;
}

MemoryFacts parse_meminfo(const std::string& text) {
    struct Field {
        const char* key;
        std::uint64_t* slot;
    };
    std::uint64_t total = 0;
    std::uint64_t available = 0;
    std::uint64_t free_bytes = 0;
    bool saw_available = false;
    bool saw_total = false;
    const Field fields[] = {
        {"MemTotal:", &total},
        {"MemAvailable:", &available},
        {"MemFree:", &free_bytes},
    };
    for (const std::string& line : split_lines(text)) {
        const std::size_t colon = line.find(':');
        if (colon == std::string::npos) {
            continue;
        }
        const std::string key = trim(line.substr(0, colon)) + ":";
        for (const Field& field : fields) {
            if (key != field.key) {
                continue;
            }
            const std::vector<std::string> rest = split_whitespace(line.substr(colon + 1));
            // Every value /proc/meminfo reports is in kB. The unit is in the
            // line, not in the number, so a line without it is refused rather
            // than assumed to be bytes.
            if (rest.size() < 2 || rest[1] != "kB") {
                MemoryFacts facts;
                facts.reason = "a /proc/meminfo row is not in kB, so its scale is unknown";
                return facts;
            }
            const auto value = to_u64(rest[0]);
            if (!value.has_value()) {
                MemoryFacts facts;
                facts.reason = "a /proc/meminfo row has a non-numeric value";
                return facts;
            }
            *field.slot = *value * 1024ull;
            if (field.slot == &available) {
                saw_available = true;
            }
            if (field.slot == &total) {
                saw_total = true;
            }
        }
    }
    if (!saw_total) {
        MemoryFacts facts;
        facts.reason = "/proc/meminfo carries no MemTotal row";
        return facts;
    }
    if (!saw_available) {
        // Kernels before 3.14 have no MemAvailable. Memory total without a
        // usable-now figure is reported as unavailable rather than silently
        // standing in MemFree, which excludes reclaimable page cache.
        MemoryFacts facts;
        facts.reason = "/proc/meminfo carries no MemAvailable row on this kernel";
        return facts;
    }
    MemoryFacts facts;
    facts.present = true;
    facts.total_bytes = total;
    facts.available_bytes = available;
    facts.free_bytes = free_bytes;
    return facts;
}

std::string decode_mount_escape(const std::string& token) {
    std::string out;
    out.reserve(token.size());
    for (std::size_t index = 0; index < token.size(); ++index) {
        if (token[index] != '\\' || index + 3 >= token.size()) {
            out.push_back(token[index]);
            continue;
        }
        const char a = token[index + 1];
        const char b = token[index + 2];
        const char c = token[index + 3];
        if (a < '0' || a > '7' || b < '0' || b > '7' || c < '0' || c > '7') {
            out.push_back(token[index]);
            continue;
        }
        const int value = (a - '0') * 64 + (b - '0') * 8 + (c - '0');
        out.push_back(static_cast<char>(value));
        index += 3;
    }
    return out;
}

std::vector<ProcMount> parse_proc_mounts(const std::string& text) {
    std::vector<ProcMount> mounts;
    for (const std::string& line : split_lines(text)) {
        const std::vector<std::string> tokens = split_whitespace(line);
        if (tokens.size() < 3) {
            continue;
        }
        ProcMount mount;
        mount.source = decode_mount_escape(tokens[0]);
        mount.target = decode_mount_escape(tokens[1]);
        mount.fstype = tokens[2];
        mounts.push_back(std::move(mount));
    }
    return mounts;
}

std::vector<DiskFacts> parse_diskstats(const std::string& text) {
    std::vector<DiskFacts> disks;
    for (const std::string& line : split_lines(text)) {
        const std::vector<std::string> tokens = split_whitespace(line);
        if (tokens.size() < 3) {
            continue;
        }
        DiskFacts device;
        device.name = tokens[2];
        if (tokens.size() < 10) {
            // The kernel grew these rows from 14 to 20 fields (discard and flush
            // columns). Reading by position is still right, but a row shorter
            // than the fields this collector reads is a row it cannot read.
            device.present = false;
            device.reason = "/proc/diskstats row has fewer than the ten fields this collector reads";
            disks.push_back(std::move(device));
            continue;
        }
        const auto sectors_read = to_u64(tokens[5]);
        const auto sectors_written = to_u64(tokens[9]);
        if (!sectors_read.has_value() || !sectors_written.has_value()) {
            device.present = false;
            device.reason = "/proc/diskstats row has a non-numeric sector counter";
            disks.push_back(std::move(device));
            continue;
        }
        device.present = true;
        device.sectors_read = *sectors_read;
        device.sectors_written = *sectors_written;
        disks.push_back(std::move(device));
    }
    return disks;
}

std::vector<std::pair<std::string, std::pair<std::uint64_t, std::uint64_t>>> parse_net_dev(
    const std::string& text) {
    std::vector<std::pair<std::string, std::pair<std::uint64_t, std::uint64_t>>> rows;
    for (const std::string& line : split_lines(text)) {
        const std::size_t colon = line.find(':');
        if (colon == std::string::npos) {
            // The two header lines carry no colon, so this is the whole filter
            // that separates them from interface rows.
            continue;
        }
        const std::string name = trim(line.substr(0, colon));
        if (name.empty()) {
            continue;
        }
        const std::vector<std::string> counters = split_whitespace(line.substr(colon + 1));
        if (counters.size() < 9) {
            continue;
        }
        const auto rx = to_u64(counters[0]);
        const auto tx = to_u64(counters[8]);
        if (!rx.has_value() || !tx.has_value()) {
            continue;
        }
        rows.emplace_back(name, std::make_pair(*rx, *tx));
    }
    return rows;
}

std::optional<double> parse_uptime_seconds(const std::string& text) {
    const std::vector<std::string> tokens = split_whitespace(text);
    if (tokens.empty()) {
        return std::nullopt;
    }
    // strtod is used here and nowhere else in this file, and it is used on a
    // token already proven to start with a digit or a '-', so locale cannot turn
    // "3.5" into "3" through a decimal-comma parse.
    if (tokens[0].find_first_not_of("0123456789.") != std::string::npos) {
        return std::nullopt;
    }
    char* end = nullptr;
    const double value = std::strtod(tokens[0].c_str(), &end);
    if (end == tokens[0].c_str() || *end != '\0') {
        return std::nullopt;
    }
    return value;
}

std::optional<std::string> reliable_device_name(const std::string& mount_source) {
    if (!starts_with(mount_source, "/dev/")) {
        // overlay, tmpfs, proc, an NFS export, a bind-mount target: none of these
        // is a block device, and the counters of whatever device happens to sit
        // underneath are not the counters of this mount.
        return std::nullopt;
    }
    const std::string name = mount_source.substr(5);
    if (name.empty() || name.find('/') != std::string::npos) {
        // "/dev/mapper/vg-lv" and "/dev/disk/by-uuid/…" name a device through a
        // directory, and this collector will not chase them to a kernel name
        // whose counters it would then be guessing about.
        return std::nullopt;
    }
    return name;
}

Sample take_sample(const FileSource& files, const Clock& clock, const SampleRequest& request) {
    Sample sample;
    sample.utc = clock.utc_now();

    if (const ReadResult uptime = files.read_file("/proc/uptime"); uptime.ok) {
        if (const auto seconds = parse_uptime_seconds(uptime.content); seconds.has_value()) {
            sample.monotonic_seconds = seconds;
        } else {
            sample.monotonic_reason = "/proc/uptime did not parse as a second count";
        }
    } else {
        sample.monotonic_reason =
            "cannot read /proc/uptime (" + uptime.error_name + "), so no interval can be measured";
    }

    if (const ReadResult boot_id = files.read_file("/proc/sys/kernel/random/boot_id"); boot_id.ok) {
        sample.boot_id = trim(boot_id.content);
    }

    if (const ReadResult stat = files.read_file("/proc/stat"); stat.ok) {
        sample.cpu = parse_proc_stat(stat.content);
    } else {
        sample.cpu.reason = "cannot read /proc/stat (" + stat.error_name + ")";
    }

    if (const ReadResult meminfo = files.read_file("/proc/meminfo"); meminfo.ok) {
        sample.memory = parse_meminfo(meminfo.content);
    } else {
        sample.memory.reason = "cannot read /proc/meminfo (" + meminfo.error_name + ")";
    }

    std::vector<ProcMount> mounts;
    std::string mounts_reason;
    if (const ReadResult raw_mounts = files.read_file("/proc/mounts"); raw_mounts.ok) {
        mounts = parse_proc_mounts(raw_mounts.content);
    } else {
        mounts_reason = "cannot read /proc/mounts (" + raw_mounts.error_name + ")";
    }

    for (const auto& [role, path] : request.filesystems) {
        FilesystemFacts facts;
        facts.role = role;
        facts.path = path;
        if (mounts_reason.empty()) {
            if (const auto mount = mount_for_path(mounts, path); mount.has_value()) {
                facts.mounted = true;
                facts.source = mount->source;
                facts.fstype = mount->fstype;
                facts.mount_point = mount->target;
            } else {
                facts.mount_reason = "no /proc/mounts row covers this path";
            }
        } else {
            facts.mount_reason = mounts_reason;
        }
        const SpaceResult space = files.stat_space(path);
        if (space.ok) {
            facts.space_known = true;
            facts.total_bytes = space.total_bytes;
            facts.free_bytes = space.free_bytes;
            facts.available_bytes = space.available_bytes;
            if (space.inodes_reported) {
                facts.inodes_known = true;
                facts.inodes_total = space.inodes_total;
                facts.inodes_free = space.inodes_free;
            } else {
                facts.inodes_reason = "this filesystem reports no inode budget";
            }
        } else {
            facts.space_reason = "statvfs failed (" + space.error_name + ")";
            facts.inodes_reason = facts.space_reason;
        }
        sample.filesystems.push_back(std::move(facts));
    }

    if (const ReadResult diskstats = files.read_file("/proc/diskstats"); diskstats.ok) {
        sample.disks = parse_diskstats(diskstats.content);
    }

    std::vector<std::pair<std::string, std::pair<std::uint64_t, std::uint64_t>>> net_rows;
    bool net_dev_ok = false;
    std::string net_dev_reason;
    if (const ReadResult net_dev = files.read_file("/proc/net/dev"); net_dev.ok) {
        net_rows = parse_net_dev(net_dev.content);
        net_dev_ok = true;
    } else {
        net_dev_reason = "cannot read /proc/net/dev (" + net_dev.error_name + ")";
    }

    std::vector<std::string> names = request.interfaces;
    if (names.empty()) {
        names = files.list_dir("/sys/class/net");
        // readdir order is filesystem order and differs between hosts and
        // between runs. Sorting here is what makes the interface block of the
        // snapshot byte-stable.
        std::sort(names.begin(), names.end());
    }

    for (const std::string& name : names) {
        InterfaceFacts facts;
        facts.name = name;
        facts.loopback = name == "lo";

        if (net_dev_ok) {
            bool found = false;
            for (const auto& row : net_rows) {
                if (row.first == name) {
                    facts.stats_present = true;
                    facts.rx_bytes = row.second.first;
                    facts.tx_bytes = row.second.second;
                    found = true;
                    break;
                }
            }
            if (!found) {
                facts.stats_reason = "/proc/net/dev lists no such interface";
            }
        } else {
            facts.stats_reason = net_dev_reason;
        }

        const std::string base = "/sys/class/net/" + name;
        if (const ReadResult operstate = files.read_file(base + "/operstate"); operstate.ok) {
            facts.operstate_known = true;
            facts.operstate = trim(operstate.content);
        } else {
            facts.operstate_reason = "cannot read operstate (" + operstate.error_name + ")";
        }

        // Virtual or physical is decided from sysfs, and the path it was decided
        // from travels with the answer: a bare judgement is not evidence.
        const ReadLinkResult link = files.read_link(base);
        if (link.ok && link.target.find("/virtual/") != std::string::npos) {
            facts.kind = "virtual";
            facts.kind_source = base + " -> " + link.target;
        } else if (files.exists(base + "/device")) {
            facts.kind = "physical";
            facts.kind_source = base + "/device";
        } else {
            facts.kind.clear();
            facts.kind_source = link.ok ? (base + " -> " + link.target)
                                        : "neither /virtual/ nor a device link was readable";
        }

        if (const ReadResult speed = files.read_file(base + "/speed"); speed.ok) {
            const auto parsed = to_i64(trim(speed.content));
            if (!parsed.has_value()) {
                facts.speed_reason = "the speed attribute is not an integer";
            } else if (*parsed < 0) {
                // virtio_net answers -1 here: a real, readable attribute whose
                // value says "this driver does not report a link speed".
                facts.speed_reason = "the driver reports no link speed (attribute value " +
                                     std::to_string(*parsed) + ")";
            } else if (*parsed == 0) {
                facts.speed_reason =
                    "the driver reports 0 Mb/s, which is not a usable link speed";
            } else {
                facts.speed_known = true;
                facts.speed_mbps = *parsed;
            }
        } else {
            facts.speed_reason = "the speed attribute could not be read (" + speed.error_name + ")";
        }

        sample.interfaces.push_back(std::move(facts));
    }

    sample.attribution.uid = request.self_uid;
    if (const ReadResult cgroup = files.read_file("/proc/self/cgroup"); cgroup.ok) {
        for (const std::string& line : split_lines(cgroup.content)) {
            const std::size_t first = line.find(':');
            if (first == std::string::npos) {
                continue;
            }
            const std::size_t second = line.find(':', first + 1);
            if (second == std::string::npos) {
                continue;
            }
            const std::string hierarchy = line.substr(0, first);
            const std::string controllers = line.substr(first + 1, second - first - 1);
            const std::string path = line.substr(second + 1);
            if (hierarchy == "0" && controllers.empty()) {
                // cgroup v2 unified: exactly one row, no controller list.
                sample.attribution.cgroup_known = true;
                sample.attribution.cgroup_version = 2;
                sample.attribution.cgroup_path = path;
                break;
            }
            if (!sample.attribution.cgroup_known) {
                sample.attribution.cgroup_known = true;
                sample.attribution.cgroup_version = 1;
                sample.attribution.cgroup_path = path;
            }
        }
        if (!sample.attribution.cgroup_known) {
            sample.attribution.cgroup_reason = "/proc/self/cgroup had no parsable row";
        }
    } else {
        sample.attribution.cgroup_reason =
            "cannot read /proc/self/cgroup (" + cgroup.error_name + ")";
    }

    if (sample.attribution.cgroup_known) {
        // Only the collector's own cgroup is opened, and only its own usage
        // counter. No other account's processes, argv, environment or files are
        // read anywhere in this collector.
        const std::string memory_path = "/sys/fs/cgroup" + sample.attribution.cgroup_path +
                                        "/memory.current";
        if (const ReadResult memory = files.read_file(memory_path); memory.ok) {
            const auto parsed = to_u64(trim(memory.content));
            if (parsed.has_value()) {
                sample.attribution.memory_known = true;
                sample.attribution.memory_current_bytes = *parsed;
            } else {
                sample.attribution.memory_reason = "memory.current is not a byte count";
            }
        } else {
            sample.attribution.memory_reason =
                "cannot read this cgroup's memory.current (" + memory.error_name + ")";
        }
    } else {
        sample.attribution.memory_reason = "the cgroup path is unknown";
    }

    return sample;
}

}  // namespace nodecore
