// One reading of the machine, and the pure parsers that produce it.
//
// A `Sample` is not a snapshot. It is what the kernel said at one instant, with
// no arithmetic applied. Every derived number in the snapshot — every delta,
// every rate — comes from putting two of these through `counter_delta`, and that
// split is what makes the interesting cases testable without a sleep: a reset is
// two samples, a zero interval is two samples, and a fixture can state both.
//
// Each parser is pure and takes text, so a truncated or hostile kernel file is a
// unit test rather than something that only shows up on a host.
#pragma once

#include <chrono>
#include <cstdint>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "nodecore/clock.hpp"
#include "nodecore/diff.hpp"
#include "nodecore/file_source.hpp"

namespace nodecore {

struct CpuTimes {
    bool present = false;
    std::string reason;
    // The eight columns a total can be taken over. guest and guest_nice are
    // parsed by nobody and summed by nobody: the kernel already counts them
    // inside user and nice, so adding them would double-count a guest's time.
    std::uint64_t user = 0;
    std::uint64_t nice = 0;
    std::uint64_t system = 0;
    std::uint64_t idle = 0;
    std::uint64_t iowait = 0;
    std::uint64_t irq = 0;
    std::uint64_t softirq = 0;
    std::uint64_t steal = 0;

    std::uint64_t total() const {
        return user + nice + system + idle + iowait + irq + softirq + steal;
    }
    // iowait counts as idle, matching runtime/inventory.py:692.
    std::uint64_t busy() const { return total() - idle - iowait; }
};

struct MemoryFacts {
    bool present = false;
    std::string reason;
    std::uint64_t total_bytes = 0;
    std::uint64_t available_bytes = 0;
    std::uint64_t free_bytes = 0;
};

// What the machine's swap looks like, in two independent halves.
//
// WHY THE TWO HALVES ARE SEPARATE. "How much swap is in use" and "is this host
// paging right now" are different questions, and only the second is a
// performance signal. SwapFree falls when a process is killed, and a page can
// come back in without ever having gone out in the same window; so a used
// percentage is a STOCK and says nothing about paging. Anyone who wants to know
// whether the host is thrashing needs the page counters, and this struct keeps
// them where that is obvious.
//
// They also come from two different files, so either half can be missing on its
// own, and each says so separately.
struct SwapFacts {
    // From /proc/meminfo's SwapTotal/SwapFree.
    bool totals_known = false;
    std::string totals_reason;
    std::uint64_t total_bytes = 0;
    std::uint64_t free_bytes = 0;

    // From /proc/vmstat's pswpin/pswpout. These are LIFETIME counters: a rate
    // needs two readings, so the difference is taken in the snapshot, not here.
    bool pages_known = false;
    std::string pages_reason;
    std::uint64_t pages_in = 0;
    std::uint64_t pages_out = 0;
};

// One line of a /proc/pressure/<resource> file.
//
// The kernel reports averages it has ALREADY windowed, so these need no second
// sample: `avg10` is the share of the last ten seconds in which at least one
// task was stalled on this resource. That is why PSI is readable from a single
// sample while the swap page counters — which are lifetime totals — are not.
struct PsiLine {
    bool present = false;
    std::string reason;
    double avg10 = 0.0;
    double avg60 = 0.0;
    double avg300 = 0.0;
    // Microseconds, cumulative since boot. A raw monotonic counter with no
    // window: kept so a consumer that wants its own interval can have one, and
    // deliberately NOT what the averages are derived from here.
    std::uint64_t total_microseconds = 0;
};

// One resource's two lines. `full` is absent on kernels that do not provide it
// for a given resource — `cpu` on older kernels is the known case — and that is
// reported as an absent line rather than as a zero, because "no task was fully
// stalled" and "this kernel does not report full" are different facts.
struct PsiFacts {
    std::string resource;  // cpu, memory, io
    bool present = false;
    std::string reason;
    PsiLine some;
    PsiLine full;
};

struct ProcMount {
    std::string source;
    std::string target;
    std::string fstype;
};

struct FilesystemFacts {
    std::string role;   // ROOT_FS, DATA_FS, ... — a role, never a device name
    std::string path;   // the path that was asked about
    bool mounted = false;
    std::string mount_reason;
    std::string source;
    std::string fstype;
    std::string mount_point;  // the mount this path actually lands on
    bool space_known = false;
    std::string space_reason;
    std::uint64_t total_bytes = 0;
    std::uint64_t free_bytes = 0;
    std::uint64_t available_bytes = 0;
    bool inodes_known = false;
    std::string inodes_reason;
    std::uint64_t inodes_total = 0;
    std::uint64_t inodes_free = 0;
};

struct DiskFacts {
    std::string name;
    bool present = false;
    std::string reason;
    std::uint64_t sectors_read = 0;
    std::uint64_t sectors_written = 0;
};

struct InterfaceFacts {
    std::string name;
    bool stats_present = false;
    std::string stats_reason;
    std::uint64_t rx_bytes = 0;
    std::uint64_t tx_bytes = 0;
    bool operstate_known = false;
    std::string operstate_reason;
    std::string operstate;
    // "physical", "virtual", or empty when sysfs could not say. There is no
    // existing Python vocabulary for this, so it is new — and it is reported
    // with the sysfs path it was decided from rather than as a bare judgement.
    std::string kind;
    std::string kind_source;
    bool speed_known = false;
    std::string speed_reason;
    std::int64_t speed_mbps = 0;
    bool loopback = false;
};

struct AttributionFacts {
    long uid = 0;
    bool cgroup_known = false;
    std::string cgroup_reason;
    std::string cgroup_path;
    int cgroup_version = 0;
    bool memory_known = false;
    std::string memory_reason;
    std::uint64_t memory_current_bytes = 0;
};

struct Sample {
    std::chrono::system_clock::time_point utc{};
    // Seconds since boot, from /proc/uptime — the same source and the same unit
    // as runtime/inventory.py:1122, so an interval measured here and one
    // measured there are the same number. Absent means every delta is refused.
    std::optional<double> monotonic_seconds;
    std::string monotonic_reason;
    std::string boot_id;
    CpuTimes cpu;
    MemoryFacts memory;
    // Optional in the schema's sense: a document produced by an older build has
    // neither, and a consumer must ignore what it does not know (see the
    // contract's "`.v1` is additive"). A build that has them ALWAYS emits them,
    // with UNAVAILABLE and a reason when the host gave nothing — never omitted,
    // so "not measured" can never look like "not asked for".
    SwapFacts swap;
    std::vector<PsiFacts> psi;
    std::vector<FilesystemFacts> filesystems;
    std::vector<DiskFacts> disks;
    std::vector<InterfaceFacts> interfaces;
    AttributionFacts attribution;
};

struct SampleRequest {
    // role -> path. A path with no role is not sampled: a filesystem fact whose
    // role nobody stated cannot be placed in a policy.
    std::vector<std::pair<std::string, std::string>> filesystems;
    // Empty means every interface /sys/class/net lists.
    std::vector<std::string> interfaces;
    // The collector's own uid, stated by the caller rather than discovered here,
    // so that a fixture-backed sample can say whose facts it is describing and a
    // test can assert the number without reading the host.
    long self_uid = 0;

    static SampleRequest default_request();
};

// -------- pure parsers --------
CpuTimes parse_proc_stat(const std::string& text);
MemoryFacts parse_meminfo(const std::string& text);
// The SwapTotal/SwapFree half, from the SAME /proc/meminfo text `parse_meminfo`
// reads. Parsed separately rather than folded into `MemoryFacts` because memory
// and swap are two budgets, and one of them being unreadable must not blank the
// other.
SwapFacts parse_meminfo_swap(const std::string& text);
// The pswpin/pswpout half, from /proc/vmstat. Lifetime totals.
SwapFacts parse_vmstat_page_counters(const std::string& text);
// One /proc/pressure/<resource> file. `resource` is carried into the result so a
// caller cannot lose which file it parsed; an empty or unrecognised text is
// UNAVAILABLE with a reason, never an all-zero reading.
PsiFacts parse_pressure(const std::string& resource, const std::string& text);
// The resources /proc/pressure exposes. A closed set: the kernel documents these
// three and answers nothing else.
extern const char* const kPressureResources[3];
std::vector<ProcMount> parse_proc_mounts(const std::string& text);
std::vector<DiskFacts> parse_diskstats(const std::string& text);
// name -> (rx_bytes, tx_bytes)
std::vector<std::pair<std::string, std::pair<std::uint64_t, std::uint64_t>>>
parse_net_dev(const std::string& text);
std::optional<double> parse_uptime_seconds(const std::string& text);
// /proc/mounts escapes space, tab, newline and backslash as octal. Undecoded,
// "/data/my\040dir" is a path that does not exist.
std::string decode_mount_escape(const std::string& token);

// -------- sampling --------
Sample take_sample(const FileSource& files, const Clock& clock, const SampleRequest& request);

// Mount-source -> kernel device name, when that mapping is reliable. Returns
// nullopt for overlay/tmpfs/bind/NFS and for anything that is not a /dev path,
// because those have no kernel block device whose counters would mean what a
// reader assumes they mean.
std::optional<std::string> reliable_device_name(const std::string& mount_source);

}  // namespace nodecore
