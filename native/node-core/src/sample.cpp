#include "nodecore/sample.hpp"

#include <algorithm>
#include <cmath>
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

// A decimal the kernel writes: digits, at most one '.', at least one digit
// overall. No sign, no exponent, no leading '+'.
//
// The exponent is refused rather than supported because nothing in /proc writes
// one, and accepting it would let `1e400` arrive as an infinity that a later
// range check has to remember to catch. A format that cannot occur is not a
// format worth parsing.
std::optional<double> to_double(const std::string& token) {
    if (token.empty()) {
        return std::nullopt;
    }
    bool saw_dot = false;
    bool saw_digit = false;
    for (const char ch : token) {
        if (ch >= '0' && ch <= '9') {
            saw_digit = true;
            continue;
        }
        if (ch == '.' && !saw_dot) {
            saw_dot = true;
            continue;
        }
        return std::nullopt;
    }
    if (!saw_digit) {
        return std::nullopt;
    }
    const double value = std::strtod(token.c_str(), nullptr);
    if (!std::isfinite(value)) {
        return std::nullopt;
    }
    return value;
}

const char* const kSwapMeminfoKeys[] = {"SwapTotal:", "SwapFree:"};

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


// SwapTotal/SwapFree, in bytes. The same "the unit is in the line, not in the
// number" rule `parse_meminfo` applies: a row without its `kB` is refused rather
// than assumed to be bytes.
SwapFacts parse_meminfo_swap(const std::string& text) {
    SwapFacts facts;
    std::uint64_t total_kb = 0;
    std::uint64_t free_kb = 0;
    bool saw_total = false;
    bool saw_free = false;
    for (const std::string& line : split_lines(text)) {
        const std::size_t colon = line.find(':');
        if (colon == std::string::npos) {
            continue;
        }
        const std::string key = trim(line.substr(0, colon)) + ":";
        std::uint64_t* slot = nullptr;
        if (key == kSwapMeminfoKeys[0]) {
            if (saw_total) {
                facts.totals_reason = "/proc/meminfo states SwapTotal twice";
                return facts;
            }
            slot = &total_kb;
            saw_total = true;
        } else if (key == kSwapMeminfoKeys[1]) {
            if (saw_free) {
                facts.totals_reason = "/proc/meminfo states SwapFree twice";
                return facts;
            }
            slot = &free_kb;
            saw_free = true;
        } else {
            continue;
        }
        const std::vector<std::string> rest = split_whitespace(line.substr(colon + 1));
        if (rest.size() < 2 || rest[1] != "kB") {
            facts.totals_reason = "a /proc/meminfo swap row is not in kB, so its scale is unknown";
            return facts;
        }
        const auto value = to_u64(rest[0]);
        if (!value.has_value()) {
            facts.totals_reason = "a /proc/meminfo swap row has a non-numeric value";
            return facts;
        }
        *slot = *value;
    }
    if (!saw_total || !saw_free) {
        // A kernel with no swap configured still writes both rows with 0, so a
        // missing row means the file could not be read as meminfo — not that the
        // machine has no swap. Those are different facts.
        facts.totals_reason = saw_total ? "/proc/meminfo carries no SwapFree row"
                                        : "/proc/meminfo carries no SwapTotal row";
        return facts;
    }
    if (free_kb > total_kb) {
        // Free swap above total swap is not a busy machine, it is a file that
        // does not add up; a "used" derived from it would be negative.
        facts.totals_reason = "/proc/meminfo reports more free swap than total swap";
        return facts;
    }
    // kB -> bytes can overflow: the multiplication is checked rather than
    // allowed to wrap, because a wrapped uint64 is a small, plausible,
    // completely wrong number of bytes — the exact failure this collector is
    // written to make impossible.
    if (total_kb > UINT64_MAX / 1024ull || free_kb > UINT64_MAX / 1024ull) {
        facts.totals_reason = "/proc/meminfo reports a swap size that overflows a uint64 byte count";
        return facts;
    }
    facts.totals_known = true;
    facts.total_bytes = total_kb * 1024ull;
    facts.free_bytes = free_kb * 1024ull;
    return facts;
}

// pswpin/pswpout: pages, lifetime totals. The file is name-space-value, one pair
// per line.
SwapFacts parse_vmstat_page_counters(const std::string& text) {
    SwapFacts facts;
    bool saw_in = false;
    bool saw_out = false;
    for (const std::string& line : split_lines(text)) {
        const std::vector<std::string> tokens = split_whitespace(line);
        if (tokens.size() != 2) {
            continue;
        }
        std::uint64_t* slot = nullptr;
        if (tokens[0] == "pswpin") {
            if (saw_in) {
                facts.pages_reason = "/proc/vmstat states pswpin twice";
                return facts;
            }
            slot = &facts.pages_in;
            saw_in = true;
        } else if (tokens[0] == "pswpout") {
            if (saw_out) {
                facts.pages_reason = "/proc/vmstat states pswpout twice";
                return facts;
            }
            slot = &facts.pages_out;
            saw_out = true;
        } else {
            continue;
        }
        const auto value = to_u64(tokens[1]);
        if (!value.has_value()) {
            // A counter that overflows uint64 is not a counter this document can
            // carry, and truncating it would produce a small plausible number.
            facts.pages_reason = "/proc/vmstat reports a page counter that is not a uint64";
            return facts;
        }
        *slot = *value;
    }
    if (!saw_in || !saw_out) {
        facts.pages_reason = saw_in ? "/proc/vmstat carries no pswpout row"
                                    : "/proc/vmstat carries no pswpin row";
        return facts;
    }
    facts.pages_known = true;
    return facts;
}

const char* const kPressureResources[3] = {"cpu", "memory", "io"};

PsiFacts parse_pressure(const std::string& resource, const std::string& text) {
    PsiFacts facts;
    facts.resource = resource;
    for (const std::string& line : split_lines(text)) {
        const std::vector<std::string> tokens = split_whitespace(line);
        if (tokens.empty()) {
            continue;
        }
        PsiLine* slot = nullptr;
        if (tokens[0] == "some") {
            slot = &facts.some;
        } else if (tokens[0] == "full") {
            slot = &facts.full;
        } else {
            // A line this parser does not know is IGNORED, not refused: a future
            // kernel may add one, and a collector that goes blind on a kernel
            // upgrade is worse than one that carries the two lines it was
            // written for. Nothing here projects an unknown line, so ignoring it
            // cannot publish anything.
            continue;
        }
        if (slot->present || !slot->reason.empty()) {
            facts.reason = "/proc/pressure/" + resource + " states " + tokens[0] + " twice";
            facts.some = PsiLine{};
            facts.full = PsiLine{};
            return facts;
        }

        bool saw_avg10 = false;
        bool saw_avg60 = false;
        bool saw_avg300 = false;
        bool saw_total = false;
        bool bad = false;
        std::string bad_reason;
        for (std::size_t index = 1; index < tokens.size(); ++index) {
            const std::size_t equals = tokens[index].find('=');
            if (equals == std::string::npos || equals == 0) {
                bad = true;
                bad_reason = "a token is not key=value";
                break;
            }
            const std::string key = tokens[index].substr(0, equals);
            const std::string raw = tokens[index].substr(equals + 1);
            double* average = nullptr;
            bool* seen = nullptr;
            if (key == "avg10") {
                average = &slot->avg10;
                seen = &saw_avg10;
            } else if (key == "avg60") {
                average = &slot->avg60;
                seen = &saw_avg60;
            } else if (key == "avg300") {
                average = &slot->avg300;
                seen = &saw_avg300;
            } else if (key == "total") {
                const auto value = to_u64(raw);
                if (!value.has_value()) {
                    bad = true;
                    bad_reason = "total is not a uint64";
                    break;
                }
                if (saw_total) {
                    bad = true;
                    bad_reason = "total appears twice";
                    break;
                }
                slot->total_microseconds = *value;
                saw_total = true;
                continue;
            } else {
                // An unknown key inside a line this parser DOES read. Refused
                // rather than ignored: the line's shape is not the shape this
                // parser understands, so the values it did read cannot be
                // trusted to mean what they are named.
                bad = true;
                bad_reason = "unknown key \"" + key + "\"";
                break;
            }
            const auto value = to_double(raw);
            // A percentage is a share of wall-clock time, so it lives in
            // [0, 100]. A kernel cannot report one outside that, so a value
            // outside it is a malformed line and not a high reading.
            if (!value.has_value() || *value < 0.0 || *value > 100.0) {
                bad = true;
                bad_reason = key + " is not a percentage in [0,100]";
                break;
            }
            if (*seen) {
                bad = true;
                bad_reason = key + " appears twice";
                break;
            }
            *average = *value;
            *seen = true;
        }
        if (!bad && !(saw_avg10 && saw_avg60 && saw_avg300 && saw_total)) {
            bad = true;
            bad_reason = "the line is missing " +
                         std::string(saw_avg10 ? "" : "avg10 ")
                         + (saw_avg60 ? "" : "avg60 ") + (saw_avg300 ? "" : "avg300 ") +
                         (saw_total ? "" : "total");
        }
        if (bad) {
            facts.reason = "/proc/pressure/" + resource + " " + tokens[0] + ": " + bad_reason;
            facts.some = PsiLine{};
            facts.full = PsiLine{};
            return facts;
        }
        slot->present = true;
    }

    if (!facts.some.present && !facts.full.present) {
        // Not "no pressure": a file with neither line is a file this parser did
        // not recognise, and reporting 0.00 for it would invent the calmest
        // possible machine out of an unreadable one.
        facts.reason = "/proc/pressure/" + resource + " carries neither a some nor a full line";
        return facts;
    }
    if (!facts.some.present) {
        // `full` without `some` is not a state the kernel produces for any
        // resource; treating it as a measurement would be reading a file whose
        // shape we do not understand.
        facts.reason = "/proc/pressure/" + resource + " carries full without some";
        facts.full = PsiLine{};
        return facts;
    }
    if (!facts.full.present && facts.full.reason.empty()) {
        // Legitimate on kernels that do not provide cpu.full. Named so a reader
        // can tell "this kernel does not report it" from "no task was ever fully
        // stalled", which is what a zero would have said.
        facts.full.reason = "/proc/pressure/" + resource +
                            " has no full line on this kernel (not every kernel provides it)";
    }
    facts.present = true;
    return facts;
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
        // The SAME text, parsed for the swap rows. Two budgets, two results: a
        // kernel with no swap still writes both swap rows with 0, and a meminfo
        // that cannot be read must blank neither.
        sample.swap = parse_meminfo_swap(meminfo.content);
    } else {
        sample.memory.reason = "cannot read /proc/meminfo (" + meminfo.error_name + ")";
        sample.swap.totals_reason =
            "cannot read /proc/meminfo (" + meminfo.error_name + ")";
    }

    if (const ReadResult vmstat = files.read_file("/proc/vmstat"); vmstat.ok) {
        const SwapFacts pages = parse_vmstat_page_counters(vmstat.content);
        sample.swap.pages_known = pages.pages_known;
        sample.swap.pages_reason = pages.pages_reason;
        sample.swap.pages_in = pages.pages_in;
        sample.swap.pages_out = pages.pages_out;
    } else {
        sample.swap.pages_reason = "cannot read /proc/vmstat (" + vmstat.error_name + ")";
    }

    // PSI, one file per resource. The kernel exposes these three and nothing
    // else, so the set is closed here rather than taken from the request: a
    // caller cannot ask for a resource the kernel does not have, and cannot
    // forget one it does.
    for (const char* resource : kPressureResources) {
        PsiFacts facts;
        const std::string path = std::string("/proc/pressure/") + resource;
        // A resource that has no file at all — an older kernel, or one built
        // without CONFIG_PSI — is an ABSENT reading with a reason, and every
        // resource is reported so that "not supported here" and "nobody looked"
        // cannot look the same.
        if (const ReadResult pressure = files.read_file(path); pressure.ok) {
            facts = parse_pressure(resource, pressure.content);
        } else {
            facts.resource = resource;
            facts.reason = "cannot read " + path + " (" + pressure.error_name + ")";
            facts.some.reason = "cannot read " + path + " (" + pressure.error_name + ")";
            facts.full.reason = facts.some.reason;
        }
        sample.psi.push_back(std::move(facts));
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
