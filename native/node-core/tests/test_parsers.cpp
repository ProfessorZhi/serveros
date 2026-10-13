// The kernel's files, as text, with the awkward shapes the real ones have.
//
// These fixtures are not invented: the /proc/diskstats rows are two copies of
// this host's own (a 20-field kernel-6.8 row and a 14-field older one), and the
// /proc/mounts escape is the one the kernel actually writes for a space.
#include <optional>
#include <string>
#include <vector>

#include "check.hpp"
#include "nodecore/sample.hpp"

using namespace nodecore;

int main() {
    {
        const std::string text =
            "cpu  100 20 30 400 50 6 7 8 9 10\n"
            "cpu0 50 10 15 200 25 3 3 4 5 5\n"
            "intr 12345 0 0\n";
        const CpuTimes times = parse_proc_stat(text);
        CHECK(times.present);
        CHECK_EQ(times.user, 100u);
        CHECK_EQ(times.nice, 20u);
        CHECK_EQ(times.system, 30u);
        CHECK_EQ(times.idle, 400u);
        CHECK_EQ(times.iowait, 50u);
        CHECK_EQ(times.irq, 6u);
        CHECK_EQ(times.softirq, 7u);
        CHECK_EQ(times.steal, 8u);
        // guest and guest_nice are read by nobody and summed by nobody: the
        // kernel already counts them inside user and nice.
        CHECK_EQ(times.total(), 621u);
        CHECK_EQ(times.busy(), 171u);  // iowait counts as idle
    }
    {
        // "cpu0" is a different line and must not be mistaken for the aggregate.
        const CpuTimes times = parse_proc_stat("cpu0 1 2 3 4 5 6 7 8\n");
        CHECK(!times.present);
        CHECK_CONTAINS(times.reason, "no aggregate 'cpu ' line");
    }
    {
        const CpuTimes times = parse_proc_stat("cpu  1 2 3\n");
        CHECK(!times.present);
        CHECK_CONTAINS(times.reason, "fewer than four counters");
    }
    {
        // An eight-field line (pre-2.6.24 layout) still parses; the missing
        // columns are zero rather than a guess.
        const CpuTimes times = parse_proc_stat("cpu  100 20 30 400\n");
        CHECK(times.present);
        CHECK_EQ(times.total(), 550u);
        CHECK_EQ(times.steal, 0u);
    }
    {
        const CpuTimes times = parse_proc_stat("cpu  100 20 x 400 50 6 7 8\n");
        CHECK(!times.present);
        CHECK_CONTAINS(times.reason, "non-numeric counter");
    }
    {
        const MemoryFacts facts = parse_meminfo(
            "MemTotal:       258000000 kB\n"
            "MemFree:         10000000 kB\n"
            "MemAvailable:   170000000 kB\n"
            "Buffers:          500000 kB\n");
        CHECK(facts.present);
        CHECK_EQ(facts.total_bytes, 258000000ull * 1024ull);
        CHECK_EQ(facts.available_bytes, 170000000ull * 1024ull);
        CHECK_EQ(facts.free_bytes, 10000000ull * 1024ull);
    }
    {
        const MemoryFacts facts = parse_meminfo("MemTotal: 100 kB\nMemFree: 10 kB\n");
        CHECK(!facts.present);
        CHECK_CONTAINS(facts.reason, "no MemAvailable");
    }
    {
        // A row whose unit is not kB is refused rather than assumed to be bytes.
        const MemoryFacts facts = parse_meminfo("MemTotal: 100 MB\nMemAvailable: 10 MB\n");
        CHECK(!facts.present);
        CHECK_CONTAINS(facts.reason, "not in kB");
    }
    {
        const MemoryFacts facts = parse_meminfo("MemAvailable: 10 kB\n");
        CHECK(!facts.present);
        CHECK_CONTAINS(facts.reason, "no MemTotal");
    }
    {
        CHECK_EQ(decode_mount_escape("/data/my\\040dir"), std::string("/data/my dir"));
        CHECK_EQ(decode_mount_escape("/plain"), std::string("/plain"));
        // A backslash that is not an octal escape survives as itself.
        CHECK_EQ(decode_mount_escape("/a\\9b"), std::string("/a\\9b"));
    }
    {
        const std::vector<ProcMount> mounts = parse_proc_mounts(
            "/dev/vda2 / ext4 rw,relatime 0 0\n"
            "/dev/vdb /data xfs rw,relatime 0 0\n"
            "tmpfs /data/my\\040dir tmpfs rw 0 0\n"
            "not-a-mount-row\n");
        CHECK_EQ(mounts.size(), std::size_t{3});
        CHECK_EQ(mounts[0].source, std::string("/dev/vda2"));
        CHECK_EQ(mounts[0].target, std::string("/"));
        CHECK_EQ(mounts[0].fstype, std::string("ext4"));
        CHECK_EQ(mounts[2].target, std::string("/data/my dir"));
    }
    {
        // This host's own rows, verbatim: a 20-field kernel-6.8 row.
        const std::vector<DiskFacts> disks = parse_diskstats(
            " 253       0 vda 101299355 31738359 1774261244 317941253 518365773 1113930189 "
            "18544522978 656755306 0 128035831 974696560 0 0 0 0 0 0\n"
            " 253       2 vda2 101298652 31738359 1774249556 317940993 518365768 1113930181 "
            "18544522874 656755303 0 137818419 974696297 0 0 0 0 0 0\n");
        CHECK_EQ(disks.size(), std::size_t{2});
        CHECK_EQ(disks[0].name, std::string("vda"));
        CHECK_EQ(disks[0].sectors_read, 1774261244ull);
        CHECK_EQ(disks[0].sectors_written, 18544522978ull);
        CHECK(disks[1].present);
        CHECK_EQ(disks[1].name, std::string("vda2"));
    }
    {
        // The older 14-field layout keeps its sector columns in the same places.
        const std::vector<DiskFacts> disks =
            parse_diskstats("   8       0 sda 1000 20 3000 40 2000 30 4000 50 0 60 70\n");
        CHECK_EQ(disks.size(), std::size_t{1});
        CHECK(disks[0].present);
        CHECK_EQ(disks[0].sectors_read, 3000u);
        CHECK_EQ(disks[0].sectors_written, 4000u);
    }
    {
        // A row shorter than the fields this collector reads is reported as an
        // unreadable device, not as a device with zero traffic.
        const std::vector<DiskFacts> disks = parse_diskstats("   8       0 sdb 1000 20\n");
        CHECK_EQ(disks.size(), std::size_t{1});
        CHECK(!disks[0].present);
        CHECK_EQ(disks[0].name, std::string("sdb"));
        CHECK_CONTAINS(disks[0].reason, "fewer than the ten fields");
    }
    {
        const std::vector<DiskFacts> disks = parse_diskstats("   8       0 sdb 1 2 3 4 5 6 x 8 9 10\n");
        CHECK_EQ(disks.size(), std::size_t{1});
        CHECK(!disks[0].present);
        CHECK_CONTAINS(disks[0].reason, "non-numeric sector counter");
    }
    {
        const std::string text =
            "Inter-|   Receive                                                |  Transmit\n"
            " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets "
            "errs drop fifo colls carrier compressed\n"
            "    lo:  500000    5000    0    0    0     0          0         0   500000    5000   "
            " 0    0    0     0       0          0\n"
            "  eth0: 9000000   40000    0    0    0     0          0         0  7000000   30000   "
            " 0    0    0     0       0          0\n";
        const auto rows = parse_net_dev(text);
        CHECK_EQ(rows.size(), std::size_t{2});
        CHECK_EQ(rows[0].first, std::string("lo"));
        CHECK_EQ(rows[0].second.first, 500000u);
        CHECK_EQ(rows[1].first, std::string("eth0"));
        CHECK_EQ(rows[1].second.first, 9000000u);
        CHECK_EQ(rows[1].second.second, 7000000u);
    }
    {
        CHECK(parse_uptime_seconds("12345.67 98765.43\n").value() == 12345.67);
        CHECK(!parse_uptime_seconds("").has_value());
        CHECK(!parse_uptime_seconds("nonsense 1.0").has_value());
    }
    {
        CHECK_EQ(reliable_device_name("/dev/vda2").value(), std::string("vda2"));
        CHECK_EQ(reliable_device_name("/dev/vdb").value(), std::string("vdb"));
        // Everything below names a filesystem or a device through a directory,
        // and the counters of whatever sits underneath are not its counters.
        CHECK(!reliable_device_name("overlay").has_value());
        CHECK(!reliable_device_name("tmpfs").has_value());
        CHECK(!reliable_device_name("/dev/mapper/vg-lv").has_value());
        CHECK(!reliable_device_name("/dev/disk/by-uuid/1234-5678").has_value());
        CHECK(!reliable_device_name("").has_value());
        CHECK(!reliable_device_name("/dev/").has_value());
    }
    return check::report("test_parsers");
}
