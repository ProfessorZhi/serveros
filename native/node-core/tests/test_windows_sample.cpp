// The Windows collector's pure half: what Win32 said, turned into the shared
// snapshot contract. The Win32 calls themselves run in the hosted Windows CI;
// these cases are the arithmetic and the honesty, which a fixture can state.
#include <chrono>
#include <string>

#include "check.hpp"
#include "nodecore/snapshot.hpp"
#include "nodecore/windows_sample.hpp"

using namespace nodecore;
using namespace std::chrono;

namespace {

WindowsFacts facts_at(std::uint64_t idle, std::uint64_t kernel, std::uint64_t user,
                      std::uint64_t tick_ms) {
    WindowsFacts facts;
    facts.cpu_ok = true;
    facts.idle_ticks = idle;
    facts.kernel_ticks = kernel;
    facts.user_ticks = user;
    facts.memory_ok = true;
    facts.total_physical_bytes = 32ull << 30;
    facts.available_physical_bytes = 8ull << 30;
    facts.tick_ok = true;
    facts.tick_milliseconds = tick_ms;
    WindowsVolumeFacts c;
    c.role = "ROOT_FS";
    c.path = "C:\\";
    c.space_ok = true;
    c.total_bytes = 1000ull << 30;
    c.free_bytes = 250ull << 30;
    c.available_bytes = 250ull << 30;
    c.volume_ok = true;
    c.mount_point = "C:\\";
    c.volume_name = "\\\\?\\Volume{0000}\\";
    c.fstype = "NTFS";
    facts.volumes.push_back(c);
    return facts;
}

}  // namespace

int main() {
    const auto t0 = system_clock::time_point(seconds(1770000000));
    {
        // 1 s apart: 4e6 ticks of idle and 6e6 of busy (kernel includes idle).
        const Sample before = sample_from_windows_facts(facts_at(10000000, 20000000, 5000000, 1000), t0);
        const Sample after = sample_from_windows_facts(
            facts_at(14000000, 26000000, 9000000, 2000), t0 + seconds(1));
        CHECK(before.cpu.present);
        // kernel 20e6 includes idle 10e6, so system is 10e6 ticks = 100 jiffies.
        CHECK_EQ(before.cpu.system, 100ull);
        CHECK_EQ(before.cpu.idle, 100ull);
        CHECK_EQ(before.cpu.user, 50ull);

        SnapshotOptions options;
        options.collector_version = "test";
        options.live = false;
        const std::string document = build_snapshot(before, after, options).dump_compact();
        // busy delta = (60+90) - (... ) : user 40 + system 20 = 60 jiffies of 100 total.
        CHECK_CONTAINS(document, "\"used_percent\":60.0");
        CHECK_CONTAINS(document, "\"source\":\"GlobalMemoryStatusEx\"");
        CHECK_CONTAINS(document, "\"total_bytes\":34359738368");
        CHECK_CONTAINS(document, "\"fstype\":\"NTFS\"");
        CHECK_CONTAINS(document, "\"mount_point\":\"C:\\\\\"");
        CHECK_CONTAINS(document, "\"sample_seconds\":1.0");
        // What Windows cannot say is UNAVAILABLE with a reason, never zero.
        CHECK_CONTAINS(document, "windows: the page file");
        CHECK_CONTAINS(document, "windows has no boot identifier");
        CHECK_CONTAINS(document, "windows volumes have no inode budget");
        CHECK_CONTAINS(document, "\"uid\":-1");
    }
    {
        // A failed call is a reason, not a zero.
        WindowsFacts broken;
        broken.cpu_reason = "GetSystemTimes failed (GetLastError 5)";
        broken.memory_reason = "GlobalMemoryStatusEx failed (GetLastError 5)";
        WindowsVolumeFacts missing;
        missing.role = "DATA_FS";
        missing.path = "D:\\";
        missing.space_reason = "GetDiskFreeSpaceExW failed (GetLastError 3)";
        missing.volume_reason = "GetVolumePathNameW failed (GetLastError 3)";
        broken.volumes.push_back(missing);
        const Sample sample = sample_from_windows_facts(broken, t0);
        CHECK(!sample.cpu.present);
        CHECK(!sample.memory.present);
        CHECK(!sample.monotonic_seconds.has_value());
        SnapshotOptions options;
        options.collector_version = "test";
        const std::string document = build_snapshot(sample, sample, options).dump_compact();
        CHECK_CONTAINS(document, "GlobalMemoryStatusEx failed (GetLastError 5)");
        CHECK_CONTAINS(document, "GetDiskFreeSpaceExW failed (GetLastError 3)");
    }
    {
        // Kernel time below idle time cannot be split; it is refused, not clamped.
        const Sample sample = sample_from_windows_facts(facts_at(30, 20, 10, 5), t0);
        CHECK(!sample.cpu.present);
    }
    return check::report("test_windows_sample");
}
