// The snapshot's contract, asserted through the document it produces.
//
// Every case here is a pair of hand-written samples. There is no clock, no file
// and no sleep in this file, because the interesting cases are exactly the ones a
// live host will not produce on demand: a counter that went backwards, a window
// with no ticks in it, a reboot between the two reads.
#include <chrono>
#include <cstdio>
#include <string>

#include "check.hpp"
#include "nodecore/snapshot.hpp"

using namespace nodecore;
using namespace std::chrono;

namespace {

constexpr const char* kVirtualSpeedReason =
    "the driver reports no link speed (attribute value -1)";

InterfaceFacts make_interface(const std::string& name) {
    InterfaceFacts facts;
    facts.name = name;
    facts.stats_present = true;
    facts.rx_bytes = 9000000;
    facts.tx_bytes = 7000000;
    facts.operstate_known = true;
    facts.operstate = "up";
    facts.kind = "physical";
    facts.kind_source = "/sys/class/net/" + name + "/device";
    facts.speed_known = false;
    facts.speed_reason = kVirtualSpeedReason;
    return facts;
}

Sample make_sample(double monotonic, const std::string& boot_id) {
    Sample sample;
    sample.utc = system_clock::time_point(seconds(1770000000));
    sample.monotonic_seconds = monotonic;
    sample.boot_id = boot_id;

    sample.cpu.present = true;
    sample.cpu.user = 100;
    sample.cpu.system = 50;
    sample.cpu.idle = 1000;
    sample.cpu.iowait = 10;

    sample.memory.present = true;
    sample.memory.total_bytes = 1000;
    sample.memory.available_bytes = 400;
    sample.memory.free_bytes = 200;

    FilesystemFacts root;
    root.role = "ROOT_FS";
    root.path = "/";
    root.mounted = true;
    root.source = "/dev/vda2";
    root.fstype = "ext4";
    root.mount_point = "/";
    root.space_known = true;
    root.total_bytes = 1000;
    root.free_bytes = 400;
    root.available_bytes = 300;
    root.inodes_known = true;
    root.inodes_total = 100;
    root.inodes_free = 60;
    sample.filesystems.push_back(root);

    DiskFacts disk;
    disk.name = "vda2";
    disk.present = true;
    sample.disks.push_back(disk);

    sample.attribution.uid = 1002;
    sample.attribution.cgroup_known = true;
    sample.attribution.cgroup_version = 2;
    sample.attribution.cgroup_path = "/user.slice/x.scope";
    sample.attribution.memory_known = true;
    sample.attribution.memory_current_bytes = 12345;
    return sample;
}

// A second sample in which every counter advanced. Built fresh for each case: a
// sample mutated by one case and read by the next is how a test starts asserting
// a document nobody wrote.
Sample healthy_after() {
    Sample sample = make_sample(1000.5, "boot-aaa");
    sample.cpu.user = 200;
    sample.cpu.system = 100;
    sample.cpu.idle = 1500;
    sample.cpu.iowait = 20;
    sample.disks[0].sectors_read = 2048;
    sample.disks[0].sectors_written = 1024;
    sample.interfaces.push_back(make_interface("eth0"));
    return sample;
}

std::string compact(const Json& document) { return document.dump_compact(); }

// When a directory is named on the command line, each document this suite builds
// is written there as well. That is what lets the contract in
// contract/node-resource-snapshot.v1.schema.json be checked against the
// unavailable-heavy shapes a healthy host never produces.
std::string g_emit_dir;

void emit(const std::string& name, const Json& document) {
    if (g_emit_dir.empty()) {
        return;
    }
    std::FILE* file = std::fopen((g_emit_dir + "/" + name + ".json").c_str(), "wb");
    if (file == nullptr) {
        return;
    }
    const std::string text = document.dump();
    std::fwrite(text.data(), 1, text.size(), file);
    std::fputc('\n', file);
    std::fclose(file);
}

bool has(const std::string& haystack, const std::string& needle) {
    return haystack.find(needle) != std::string::npos;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc > 1) {
        g_emit_dir = argv[1];
    }
    // `before` is never mutated, so every case can share it.
    const Sample before = make_sample(1000.0, "boot-aaa");

    {
        // A healthy pair: half a second, every counter advanced.
        const Sample healthy = healthy_after();
        const std::string text = compact(build_snapshot(before, healthy, SnapshotOptions{}));
        CHECK_CONTAINS(text, R"("schema":"serveros/node-resource-snapshot.v1")");
        CHECK_CONTAINS(text, R"("live":false)");
        CHECK_CONTAINS(text, R"("sample_seconds":0.500000)");
        CHECK_CONTAINS(text, R"("observed_at":"2026-02-02T)");
        // busy delta 150 of total delta 660.
        CHECK_CONTAINS(text, R"("used_percent":22.7)");
        CHECK_CONTAINS(text, R"("busy_jiffies":150)");
        CHECK_CONTAINS(text, R"("total_jiffies":660)");
        // 2048 sectors x the kernel's fixed 512-byte unit, exactly.
        CHECK_CONTAINS(text, R"("read_bytes":1048576)");
        CHECK_CONTAINS(text, R"("write_bytes":524288)");
        // ...and the rate is derived from those bytes, not from the sectors:
        // 1048576 bytes over 0.5s. A rate taken from the sector counter would
        // read 4096.00 here while still being labelled bytes.
        CHECK_CONTAINS(text, R"("read_bytes_per_second":2097152.00)");
        CHECK_CONTAINS(text, R"("write_bytes_per_second":1048576.00)");
        CHECK_CONTAINS(text, R"("sector_size_bytes":512)");
        CHECK_CONTAINS(text, R"("state":"PRESENT")");
        CHECK_CONTAINS(text, R"("uid":1002)");
        CHECK_CONTAINS(text, R"("memory_current_bytes":12345)");
        CHECK_CONTAINS(text, R"("devices_without_a_role":[])");
        // A host-wide figure is labelled as one and never divided by account.
        CHECK_CONTAINS(text, R"("scope":"host-wide")");
        CHECK_CONTAINS(text, R"("scope":"self")");
    }
    {
        // A measured zero. Two equal readings over a real interval say "nothing
        // happened" — and the field must be absent from the unknowns list, or
        // "zero" and "unknown" have been collapsed into each other.
        const Sample flat = make_sample(1000.5, "boot-aaa");
        const std::string text = compact(build_snapshot(before, flat, SnapshotOptions{}));
        CHECK_CONTAINS(text, R"("read_bytes":0)");
        CHECK_CONTAINS(text, R"("write_bytes":0)");
        CHECK(!has(text, R"("field":"disk_io.ROOT_FS.read_bytes")"));
        CHECK(!has(text, R"("field":"disk_io.ROOT_FS.write_bytes")"));
    }
    {
        // A counter that went backwards is refused, not rewound and not clamped
        // to zero.
        Sample reversed = make_sample(1000.5, "boot-aaa");
        reversed.disks[0].sectors_read = 0;
        Sample start = make_sample(1000.0, "boot-aaa");
        start.disks[0].sectors_read = 5000;
        const std::string text = compact(build_snapshot(start, reversed, SnapshotOptions{}));
        CHECK_CONTAINS(text, "counter decreased");
        CHECK_CONTAINS(text, R"("field":"disk_io.ROOT_FS.read_bytes")");
        CHECK(!has(text, R"("read_bytes":0)"));
    }
    {
        // Zero interval: the two readings are not separated, so nothing that
        // needs a denominator is computed.
        Sample same = make_sample(1000.0, "boot-aaa");
        const std::string text = compact(build_snapshot(before, same, SnapshotOptions{}));
        CHECK_CONTAINS(text, "share no measurable interval");
        CHECK_CONTAINS(text, R"("field":"sample_seconds")");
        CHECK_CONTAINS(text, R"("field":"disk_io.ROOT_FS.read_bytes")");
        CHECK_CONTAINS(text, R"("read_bytes":{"reason":)");
        CHECK(!has(text, R"("read_bytes":0)"));
    }
    {
        // An interval elapsed but the tick counters did not move: a window that
        // measured nothing, which is not an idle machine.
        Sample idle_window = make_sample(1000.5, "boot-aaa");
        const std::string text = compact(build_snapshot(before, idle_window, SnapshotOptions{}));
        CHECK_CONTAINS(text, "no jiffies elapsed between the two /proc/stat reads");
        CHECK_CONTAINS(text, R"("cpu":{"reason":"no jiffies elapsed)");
        CHECK_CONTAINS(text, R"("field":"cpu.used_percent")");
    }
    {
        // The host restarted between the samples. Every counter in the document
        // was reset, so no delta across that boundary is a measurement.
        Sample rebooted = make_sample(1000.5, "boot-bbb");
        const std::string text = compact(build_snapshot(before, rebooted, SnapshotOptions{}));
        CHECK_CONTAINS(text, "the boot id changed between the two samples");
        CHECK_CONTAINS(text, R"("field":"sample_seconds")");
    }
    {
        // virtio_net reports no speed, so there is no denominator and no
        // utilisation figure. Reporting 0% would say the link is idle.
        Sample with_eth = make_sample(1000.0, "boot-aaa");
        with_eth.interfaces.push_back(make_interface("eth0"));
        Sample with_eth_later = make_sample(1000.5, "boot-aaa");
        with_eth_later.interfaces.push_back(make_interface("eth0"));
        with_eth_later.interfaces[0].rx_bytes = 10000000;
        const std::string text = compact(build_snapshot(with_eth, with_eth_later, SnapshotOptions{}));
        CHECK_CONTAINS(text, kVirtualSpeedReason);
        CHECK_CONTAINS(text, "no interface speed, so there is no denominator");
        CHECK_CONTAINS(text, R"("field":"interfaces.eth0.rx_utilisation_percent")");
        CHECK(!has(text, "\"rx_utilisation_percent\":0"));
        CHECK(!has(text, "\"speed_mbps\":0"));
        CHECK_CONTAINS(text, R"("rx_bytes":1000000)");
        CHECK_CONTAINS(text, R"("kind":"physical")");
    }
    {
        // An interface that did not exist at the first sample has no first
        // reading, and the kernel's new counters starting at zero are not a
        // measured zero rate.
        Sample without = make_sample(1000.0, "boot-aaa");
        Sample with = make_sample(1000.5, "boot-aaa");
        with.interfaces.push_back(make_interface("veth9"));
        const std::string text = compact(build_snapshot(without, with, SnapshotOptions{}));
        CHECK_CONTAINS(text, "this interface did not exist at the first sample");
        CHECK_CONTAINS(text, R"("field":"interfaces.veth9.rx_bytes")");
        CHECK(!has(text, "\"rx_bytes\":0"));
    }
    {
        // A mount whose source is not a block device gets no device counters, and
        // it is named rather than dropped.
        Sample with_overlay = make_sample(1000.5, "boot-aaa");
        FilesystemFacts overlay;
        overlay.role = "STATE_FS";
        overlay.path = "/state";
        overlay.mounted = true;
        overlay.source = "overlay";
        overlay.fstype = "overlay";
        overlay.mount_point = "/state";
        overlay.space_known = true;
        overlay.total_bytes = 10;
        overlay.available_bytes = 5;
        with_overlay.filesystems.push_back(overlay);

        DiskFacts extra;
        extra.name = "vdb";
        extra.present = true;
        with_overlay.disks.push_back(extra);

        const std::string text = compact(build_snapshot(before, with_overlay, SnapshotOptions{}));
        CHECK_CONTAINS(text, "not a kernel block device name");
        CHECK_CONTAINS(text, R"("field":"disk_io.STATE_FS")");
        CHECK_CONTAINS(text, R"("devices_without_a_role":["vdb"])");
    }
    {
        // The arithmetic a reader would check by hand.
        const std::string text = compact(build_snapshot(before, healthy_after(), SnapshotOptions{}));
        CHECK_CONTAINS(text, R"("memory":{")");
        // MemTotal 1000, MemAvailable 400 -> 60.0% used.
        CHECK_CONTAINS(text, R"("used_percent":60.0)");
        // filesystem: used = 1000-400 = 600, capacity = 600+300 = 900 -> 66.7%.
        CHECK_CONTAINS(text, R"("used_percent":66.7)");
        // inodes: 40 of 100 -> 40.0%.
        CHECK_CONTAINS(text, R"("used_percent":40.0)");
    }
    {
        // The document is byte-stable: same facts in, same bytes out, twice.
        const Json first = build_snapshot(before, healthy_after(), SnapshotOptions{});
        const Json second = build_snapshot(before, healthy_after(), SnapshotOptions{});
        CHECK_EQ(first.dump(), second.dump());
        CHECK_EQ(first.dump_compact(), second.dump_compact());
        // The compact form is the pretty form with the whitespace removed, not a
        // different document.
        const std::string pretty = first.dump();
        CHECK(!has(first.dump_compact(), "\n"));
        // A space may legitimately sit inside a string value, so compactness is
        // asserted on the separator rather than on whitespace in general.
        CHECK_CONTAINS(first.dump_compact(), R"("schema":"serveros/node-resource-snapshot.v1")");
        CHECK(has(pretty, "\n"));
        CHECK(pretty.rfind("{\n", 0) == 0);
        CHECK(pretty.back() == '}');
    }
    {
        // The version is the caller's, and `live` says where the facts came from.
        SnapshotOptions options;
        options.collector_version = "9.9.9";
        options.live = true;
        const std::string text = compact(build_snapshot(before, healthy_after(), options));
        CHECK_CONTAINS(text, R"("version":"9.9.9")");
        CHECK_CONTAINS(text, R"("live":true)");
        CHECK_CONTAINS(text, R"("language":"c++20")");
    }
    // Emitted for the contract check. The `all-unavailable` document is the one
    // a healthy host cannot produce on demand, and it is the shape the schema's
    // whole-block UNAVAILABLE branches exist for.
    emit("healthy", build_snapshot(before, healthy_after(), SnapshotOptions{}));
    emit("zero-interval",
         build_snapshot(before, make_sample(1000.0, "boot-aaa"), SnapshotOptions{}));
    emit("rebooted",
         build_snapshot(before, make_sample(1000.5, "boot-bbb"), SnapshotOptions{}));
    {
        Sample broken_before = make_sample(1000.0, "boot-aaa");
        Sample broken = make_sample(1000.5, "boot-aaa");
        broken.cpu.present = false;
        broken.cpu.reason = "cannot read /proc/stat (ENOENT)";
        broken.memory.present = false;
        broken.memory.reason = "cannot read /proc/meminfo (ENOENT)";
        broken.filesystems[0].space_known = false;
        broken.filesystems[0].space_reason = "statvfs failed (EACCES)";
        broken.filesystems[0].inodes_known = false;
        broken.filesystems[0].inodes_reason = "this filesystem reports no inode budget";
        broken.attribution.cgroup_known = false;
        broken.attribution.cgroup_reason = "cannot read /proc/self/cgroup (ENOENT)";
        broken.attribution.memory_known = false;
        broken.attribution.memory_reason = "the cgroup path is unknown";

        FilesystemFacts unmounted;
        unmounted.role = "STATE_FS";
        unmounted.path = "/state";
        unmounted.mount_reason = "no /proc/mounts row covers this path";
        // Space readable but no inode budget: the one shape that reaches the
        // inodes UNAVAILABLE branch. When statvfs itself fails the row stops
        // earlier, because there is nothing on it to qualify.
        unmounted.space_known = true;
        unmounted.total_bytes = 100;
        unmounted.available_bytes = 10;
        unmounted.inodes_reason = "this filesystem reports no inode budget";
        broken.filesystems.push_back(unmounted);

        InterfaceFacts odd;
        odd.name = "eth7";
        odd.stats_present = false;
        odd.stats_reason = "cannot read /proc/net/dev (EACCES)";
        odd.operstate_known = false;
        odd.operstate_reason = "cannot read operstate (EACCES)";
        odd.kind_source = "neither /virtual/ nor a device link was readable";
        odd.speed_reason = "the speed attribute could not be read (EACCES)";
        broken.interfaces.push_back(odd);

        const Json document = build_snapshot(broken_before, broken, SnapshotOptions{});
        emit("all-unavailable", document);
        // The block-level UNAVAILABLE shapes are real, not decorative.
        const std::string text = compact(document);
        // These carry an errno name in parentheses, so a `)"` appears inside the
        // text and the raw literal needs a delimiter of its own.
        CHECK_CONTAINS(text, R"j("cpu":{"reason":"second sample: cannot read /proc/stat (ENOENT)","state":"UNAVAILABLE"})j");
        CHECK_CONTAINS(text, R"j("memory":{"reason":"cannot read /proc/meminfo (ENOENT)","state":"UNAVAILABLE"})j");
        CHECK_CONTAINS(text, R"j("cgroup_path":{"reason":"cannot read /proc/self/cgroup (ENOENT)","state":"UNAVAILABLE"})j");
        CHECK_CONTAINS(text, R"j("mount":{"reason":"no /proc/mounts row covers this path","state":"UNAVAILABLE"})j");
        CHECK_CONTAINS(text, R"j("inodes":{"reason":"this filesystem reports no inode budget","state":"UNAVAILABLE"})j");
        CHECK_CONTAINS(text, R"j("reason":"statvfs failed (EACCES)","role":"ROOT_FS","source":"/dev/vda2","state":"UNAVAILABLE")j");
    }
    return check::report("test_snapshot");
}
