// Pure conversion from what Windows said to a `Sample`. Compiled on every
// platform so the Linux test suite can prove it; the Win32 calls live in
// windows_system.cpp and are compiled only under _WIN32.
#include "nodecore/windows_sample.hpp"

namespace nodecore {

namespace {

//: 100 ns ticks per USER_HZ tick (1/100 s). The snapshot names its CPU counters
//: `*_jiffies`; converting keeps that unit true on this platform as well.
constexpr std::uint64_t kTicksPerJiffy = 100000;

constexpr const char* kSwapReason =
    "windows: the page file's size and use are not read by this version; "
    "GlobalMemoryStatusEx's page-file figures are the commit limit, which is not swap";

}  // namespace

std::vector<std::pair<std::string, std::string>> default_windows_filesystems() {
    return {{"ROOT_FS", "C:\\"}};
}

Sample sample_from_windows_facts(const WindowsFacts& facts,
                                 std::chrono::system_clock::time_point utc) {
    Sample sample;
    sample.utc = utc;

    if (facts.tick_ok) {
        sample.monotonic_seconds = static_cast<double>(facts.tick_milliseconds) / 1000.0;
    } else {
        sample.monotonic_reason = "GetTickCount64 gave no reading";
    }
    sample.boot_id_reason = "windows has no boot identifier; a restart is visible only as the "
                            "monotonic clock going backwards";

    if (facts.cpu_ok && facts.kernel_ticks >= facts.idle_ticks) {
        sample.cpu.present = true;
        sample.cpu.user = facts.user_ticks / kTicksPerJiffy;
        // GetSystemTimes' kernel time includes idle time; the rest is system.
        sample.cpu.system = (facts.kernel_ticks - facts.idle_ticks) / kTicksPerJiffy;
        sample.cpu.idle = facts.idle_ticks / kTicksPerJiffy;
    } else {
        sample.cpu.reason = facts.cpu_ok ? "GetSystemTimes reported less kernel time than idle time"
                                         : facts.cpu_reason;
    }

    sample.memory.source = "GlobalMemoryStatusEx";
    if (facts.memory_ok && facts.total_physical_bytes > 0) {
        sample.memory.present = true;
        sample.memory.total_bytes = facts.total_physical_bytes;
        sample.memory.available_bytes = facts.available_physical_bytes;
        // Windows does not separate "free" from "available" the way MemFree and
        // MemAvailable differ; the available figure is the one the OS reports.
        sample.memory.free_bytes = facts.available_physical_bytes;
    } else {
        sample.memory.reason = facts.memory_ok ? "GlobalMemoryStatusEx reported no physical memory"
                                               : facts.memory_reason;
    }

    sample.swap.totals_reason = kSwapReason;
    sample.swap.pages_reason = kSwapReason;

    for (const WindowsVolumeFacts& volume : facts.volumes) {
        FilesystemFacts fs{};
        fs.role = volume.role;
        fs.path = volume.path;
        if (volume.volume_ok) {
            fs.mounted = true;
            fs.mount_point = volume.mount_point;
            fs.source = volume.volume_name;
            fs.fstype = volume.fstype;
        } else {
            fs.mount_reason = volume.volume_reason;
        }
        if (volume.space_ok) {
            fs.space_known = true;
            fs.total_bytes = volume.total_bytes;
            fs.free_bytes = volume.free_bytes;
            fs.available_bytes = volume.available_bytes;
        } else {
            fs.space_reason = volume.space_reason;
        }
        fs.inodes_reason = "windows volumes have no inode budget";
        sample.filesystems.push_back(fs);
    }

    // No uid on Windows. The account is established by core-lite-status from
    // the process token, not by this document; -1 is the "no POSIX uid" value
    // the identity reader already uses.
    sample.attribution.uid = -1;
    sample.attribution.cgroup_reason = "windows has no cgroups";
    sample.attribution.memory_reason = "windows has no cgroups";
    return sample;
}

}  // namespace nodecore
