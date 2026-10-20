// Sampling from a fixture, with the host's own awkward cases in it.
//
// The fixture source answers ENOENT for any path nobody registered. That is
// deliberate and it is the property that makes these tests worth anything: a
// test cannot pass because the fake quietly supplied something plausible.
#include <cerrno>
#include <chrono>
#include <string>

#include "check.hpp"
#include "nodecore/clock.hpp"
#include "nodecore/file_source.hpp"
#include "nodecore/sample.hpp"

using namespace nodecore;

namespace {

const char* kCgroupPath =
    "/user.slice/user-1002.slice/user@1002.service/cloudcli.slice/"
    "claudecodeui-session-637899-0905a1bf.scope";

void register_host(FixtureFileSource& files) {
    files.add_file("/proc/uptime", "12345.67 98765.43\n");
    files.add_file("/proc/sys/kernel/random/boot_id",
                   "0f7c0f4a-1111-2222-3333-444455556666\n");
    files.add_file("/proc/stat",
                   "cpu  100 20 30 400 50 6 7 8 9 10\n"
                   "cpu0 50 10 15 200 25 3 3 4 5 5\n");
    files.add_file("/proc/meminfo",
                   "MemTotal:       258000000 kB\n"
                   "MemFree:         10000000 kB\n"
                   "MemAvailable:   170000000 kB\n");
    files.add_file("/proc/mounts",
                   "/dev/vda2 / ext4 rw,relatime 0 0\n"
                   "/dev/vdb /data xfs rw,relatime 0 0\n"
                   "overlay /var/lib/docker/overlay2/x overlay rw 0 0\n");
    files.add_file("/proc/diskstats",
                   " 253       2 vda2 101298652 31738359 1774249556 317940993 518365768 "
                   "1113930181 18544522874 656755303 0 137818419 974696297 0 0 0 0 0 0\n"
                   " 253      16 vdb 128937785 6636968 15416476901 1036771202 1192773070 "
                   "211062361 77261741645 2243659785 0 296336519 3280430987 0 0 0 0 0 0\n");
    files.add_file("/proc/net/dev",
                   "Inter-|   Receive                                                |  Transmit\n"
                   " face |bytes    packets errs drop fifo frame compressed multicast|bytes    "
                   "packets errs drop fifo colls carrier compressed\n"
                   "    lo:  500000    5000    0    0    0     0          0         0   500000    "
                   "5000    0    0    0     0       0          0\n"
                   "  eth0: 9000000   40000    0    0    0     0          0         0  7000000   "
                   "30000    0    0    0     0       0          0\n"
                   "veth0: 1000000   10000    0    0    0     0          0         0  2000000   "
                   "20000    0    0    0     0       0          0\n");
    files.add_file("/proc/self/cgroup", std::string("0::") + kCgroupPath + "\n");
    files.add_file(std::string("/sys/fs/cgroup") + kCgroupPath + "/memory.current",
                   "734003200\n");

    // /sys/class/net is listed in an order readdir would give, not sorted — the
    // sampler is what makes the result stable.
    files.add_dir("/sys/class/net", {"veth0", "lo", "eth0"});

    files.add_symlink("/sys/class/net/eth0",
                      "../../devices/pci0000:00/0000:00:05.0/virtio2/net/eth0");
    files.add_file("/sys/class/net/eth0/operstate", "up\n");
    // The attribute exists and says -1. This is the real virtio_net answer and
    // it is a reading, not a failure.
    files.add_file("/sys/class/net/eth0/speed", "-1\n");
    files.add_symlink("/sys/class/net/eth0/device", "../../../0000:00:05.0");

    files.add_symlink("/sys/class/net/lo", "../../devices/virtual/net/lo");
    files.add_file("/sys/class/net/lo/operstate", "unknown\n");
    files.add_read_error("/sys/class/net/lo/speed", EINVAL, "EINVAL");

    files.add_symlink("/sys/class/net/veth0", "../../devices/virtual/net/veth0");
    files.add_file("/sys/class/net/veth0/operstate", "up\n");
    files.add_file("/sys/class/net/veth0/speed", "10000\n");

    SpaceResult root;
    root.ok = true;
    root.total_bytes = 504ull * 1000 * 1000 * 1000;
    root.free_bytes = 448ull * 1000 * 1000 * 1000;
    root.available_bytes = 448ull * 1000 * 1000 * 1000;
    root.inodes_reported = true;
    root.inodes_total = 1000000;
    root.inodes_free = 900000;
    files.add_space("/", root);

    SpaceResult data;
    data.ok = true;
    data.total_bytes = 4000ull * 1000 * 1000 * 1000;
    data.free_bytes = 3000ull * 1000 * 1000 * 1000;
    data.available_bytes = 2900ull * 1000 * 1000 * 1000;
    // No inode budget: tmpfs-shaped, and a different fact from an empty one.
    data.inodes_reported = false;
    files.add_space("/data", data);
}

SampleRequest request_of() {
    SampleRequest request = SampleRequest::default_request();
    request.self_uid = 1002;
    return request;
}

}  // namespace

int main() {
    FixtureFileSource files;
    register_host(files);
    FakeClock clock;
    clock.set_utc(std::chrono::system_clock::time_point(std::chrono::seconds(1770000000)));

    const Sample sample = take_sample(files, clock, request_of());

    CHECK(sample.monotonic_seconds.has_value());
    CHECK_EQ(*sample.monotonic_seconds, 12345.67);
    CHECK_EQ(sample.boot_id, std::string("0f7c0f4a-1111-2222-3333-444455556666"));

    CHECK(sample.cpu.present);
    CHECK_EQ(sample.cpu.total(), 621u);

    CHECK(sample.memory.present);
    CHECK_EQ(sample.memory.total_bytes, 258000000ull * 1024ull);

    // Filesystems: role, the mount it lands on, space and inodes.
    CHECK_EQ(sample.filesystems.size(), std::size_t{2});
    CHECK_EQ(sample.filesystems[0].role, std::string("ROOT_FS"));
    CHECK(sample.filesystems[0].mounted);
    CHECK_EQ(sample.filesystems[0].source, std::string("/dev/vda2"));
    CHECK_EQ(sample.filesystems[0].mount_point, std::string("/"));
    CHECK(sample.filesystems[0].space_known);
    CHECK(sample.filesystems[0].inodes_known);
    CHECK_EQ(sample.filesystems[1].role, std::string("DATA_FS"));
    CHECK_EQ(sample.filesystems[1].source, std::string("/dev/vdb"));
    CHECK(!sample.filesystems[1].inodes_known);
    CHECK_CONTAINS(sample.filesystems[1].inodes_reason, "no inode budget");

    // Interfaces come back sorted, whatever readdir said.
    CHECK_EQ(sample.interfaces.size(), std::size_t{3});
    CHECK_EQ(sample.interfaces[0].name, std::string("eth0"));
    CHECK_EQ(sample.interfaces[1].name, std::string("lo"));
    CHECK_EQ(sample.interfaces[2].name, std::string("veth0"));

    // virtio_net: a readable attribute whose value says "no speed reported".
    CHECK(!sample.interfaces[0].speed_known);
    CHECK_CONTAINS(sample.interfaces[0].speed_reason, "no link speed");
    CHECK_CONTAINS(sample.interfaces[0].speed_reason, "-1");
    CHECK_EQ(sample.interfaces[0].kind, std::string("physical"));
    CHECK_CONTAINS(sample.interfaces[0].kind_source, "/sys/class/net/eth0/device");
    CHECK_EQ(sample.interfaces[0].operstate, std::string("up"));
    CHECK_EQ(sample.interfaces[0].rx_bytes, 9000000u);
    CHECK_EQ(sample.interfaces[0].tx_bytes, 7000000u);

    // lo: the read itself fails with EINVAL, which is a different sentence.
    CHECK(sample.interfaces[1].loopback);
    CHECK(!sample.interfaces[1].speed_known);
    CHECK_CONTAINS(sample.interfaces[1].speed_reason, "EINVAL");
    CHECK_EQ(sample.interfaces[1].kind, std::string("virtual"));
    CHECK_EQ(sample.interfaces[1].operstate, std::string("unknown"));

    // A bridge or veth reports a real speed through the virtual path.
    CHECK(sample.interfaces[2].speed_known);
    CHECK_EQ(sample.interfaces[2].speed_mbps, 10000);
    CHECK_EQ(sample.interfaces[2].kind, std::string("virtual"));

    // Attribution: this process's uid, its own cgroup, that cgroup's own counter.
    CHECK_EQ(sample.attribution.uid, 1002L);
    CHECK(sample.attribution.cgroup_known);
    CHECK_EQ(sample.attribution.cgroup_version, 2);
    CHECK_EQ(sample.attribution.cgroup_path, std::string(kCgroupPath));
    CHECK(sample.attribution.memory_known);
    CHECK_EQ(sample.attribution.memory_current_bytes, 734003200ull);

    {
        // Every read that was not registered reports as unavailable with the
        // kernel's own errno name, rather than as a zero.
        FixtureFileSource bare;
        const Sample empty = take_sample(bare, clock, request_of());
        CHECK(!empty.cpu.present);
        CHECK_CONTAINS(empty.cpu.reason, "ENOENT");
        CHECK(!empty.memory.present);
        CHECK(!empty.monotonic_seconds.has_value());
        CHECK_CONTAINS(empty.monotonic_reason, "ENOENT");
        CHECK(!empty.attribution.cgroup_known);
        CHECK(!empty.attribution.memory_known);
        CHECK(empty.boot_id.empty());
    }
    {
        // A /proc/stat with no aggregate row leaves the CPU block unmeasured
        // rather than zeroed.
        FixtureFileSource partial;
        partial.add_file("/proc/stat", "cpu0 1 2 3 4 5 6 7 8\n");
        const Sample sample2 = take_sample(partial, clock, request_of());
        CHECK(!sample2.cpu.present);
        CHECK_CONTAINS(sample2.cpu.reason, "no aggregate 'cpu ' line");
    }
    return check::report("test_sample");
}
