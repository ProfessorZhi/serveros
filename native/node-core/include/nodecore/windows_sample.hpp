// The Windows collector's door to the machine, split the same way the Linux one
// is split: a pure conversion that tests can drive with stated values, and one
// Win32-only function that asks the OS.
//
// What Windows can answer for the shared `node-resource-snapshot.v1` contract:
//
//   cpu         GetSystemTimes — idle / kernel (which INCLUDES idle) / user, in
//               100 ns ticks. Converted to USER_HZ=100 ticks so the snapshot's
//               `*_jiffies` names keep their unit.
//   memory      GlobalMemoryStatusEx — total and available physical memory.
//   filesystems GetDiskFreeSpaceExW per role path, plus the volume it lands on
//               and that volume's filesystem name.
//   monotonic   GetTickCount64.
//
// What it cannot, and says so instead of inventing a number:
//
//   swap        the page file's size and use are not what GlobalMemoryStatusEx's
//               commit figures describe; UNAVAILABLE with that reason.
//   psi         does not exist on Windows (core-lite/windows/README.md §2.5).
//   boot_id     Windows has no boot identifier; UNAVAILABLE.
//   disk_io, interfaces, cgroup attribution: not read by this version.
#pragma once

#include <chrono>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "nodecore/sample.hpp"

namespace nodecore {

struct WindowsVolumeFacts {
    std::string role;
    std::string path;
    bool space_ok = false;
    std::string space_reason;
    std::uint64_t total_bytes = 0;
    std::uint64_t free_bytes = 0;       // GetDiskFreeSpaceExW's TotalNumberOfFreeBytes
    std::uint64_t available_bytes = 0;  // ... FreeBytesAvailableToCaller (quotas apply)
    bool volume_ok = false;
    std::string volume_reason;
    std::string mount_point;  // GetVolumePathNameW, e.g. "C:\"
    std::string volume_name;  // GetVolumeNameForVolumeMountPointW, e.g. "\\?\Volume{...}\"
    std::string fstype;       // GetVolumeInformationW, e.g. "NTFS"
};

struct WindowsFacts {
    bool cpu_ok = false;
    std::string cpu_reason;
    std::uint64_t idle_ticks = 0;    // 100 ns
    std::uint64_t kernel_ticks = 0;  // 100 ns, includes idle
    std::uint64_t user_ticks = 0;    // 100 ns

    bool memory_ok = false;
    std::string memory_reason;
    std::uint64_t total_physical_bytes = 0;
    std::uint64_t available_physical_bytes = 0;

    bool tick_ok = false;
    std::uint64_t tick_milliseconds = 0;

    std::vector<WindowsVolumeFacts> volumes;
};

//: Pure: what the snapshot builder needs, from what the OS said. No I/O.
Sample sample_from_windows_facts(const WindowsFacts& facts,
                                 std::chrono::system_clock::time_point utc);

//: The default roles when none are asked for: the system drive.
std::vector<std::pair<std::string, std::string>> default_windows_filesystems();

#ifdef _WIN32
//: Asks the OS. Read-only: no handle opened for writing, no privilege enabled.
WindowsFacts read_windows_facts(const std::vector<std::pair<std::string, std::string>>& roles);
#endif

}  // namespace nodecore
