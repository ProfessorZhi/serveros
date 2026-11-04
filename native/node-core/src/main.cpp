// serveros-node-resource-snapshot — a read-only reading of this machine.
//
// This is not a service, not a daemon and not a scheduler. It takes two samples
// a short interval apart, prints one NodeResourceSnapshot v1 document to stdout
// and exits. It holds no state between runs, writes nothing, and opens no file
// for writing. The Python core remains the authority for every question this
// document answers: this program supplies measurements, and a measurement is not
// a policy.
#ifndef _WIN32
#include <unistd.h>
#endif

#include <chrono>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

#include "nodecore/clock.hpp"
#include "nodecore/file_source.hpp"
#include "nodecore/sample.hpp"
#include "nodecore/snapshot.hpp"
#include "nodecore/windows_sample.hpp"

namespace {

constexpr const char* kVersion = "0.1.0";

void print_usage(std::FILE* out) {
    std::fprintf(out,
                 "serveros-node-resource-snapshot %s\n"
                 "\n"
                 "Read-only. Takes two samples of this machine and writes one\n"
                 "serveros/node-resource-snapshot.v1 document to stdout.\n"
                 "\n"
                 "Usage:\n"
                 "  serveros-node-resource-snapshot [options]\n"
                 "\n"
                 "Options:\n"
                 "  --interval-ms N        milliseconds between the two samples (default 500).\n"
                 "                         Zero is accepted and produces a snapshot whose every\n"
                 "                         delta is UNAVAILABLE, because no interval was measured.\n"
                 "  --filesystem ROLE=PATH add a filesystem role (repeatable). Defaults:\n"
                 "                         ROOT_FS=/ DATA_FS=/data (Windows: ROOT_FS=C:\\)\n"
                 "  --interface NAME       sample only this interface (repeatable).\n"
                 "                         Default: every interface /sys/class/net lists.\n"
                 "  --compact              emit the canonical single-line form instead of the\n"
                 "                         indented one. Same bytes, no whitespace.\n"
                 "  --version              print the collector version and exit.\n"
                 "  --help                 print this text and exit.\n",
                 kVersion);
}

bool parse_long(const char* text, long& out) {
    if (text == nullptr || *text == '\0') {
        return false;
    }
    char* end = nullptr;
    const long value = std::strtol(text, &end, 10);
    if (end == text || *end != '\0') {
        return false;
    }
    out = value;
    return true;
}

}  // namespace

int main(int argc, char** argv) {
    long interval_ms = 500;
    bool compact = false;
    std::vector<std::string> filesystem_args;
    std::vector<std::string> interface_args;

    for (int index = 1; index < argc; ++index) {
        const std::string arg = argv[index];
        if (arg == "--help" || arg == "-h") {
            print_usage(stdout);
            return 0;
        }
        if (arg == "--version") {
            std::printf("serveros-node-resource-snapshot %s\n", kVersion);
            return 0;
        }
        if (arg == "--compact") {
            compact = true;
            continue;
        }
        if (arg == "--interval-ms") {
            if (index + 1 >= argc || !parse_long(argv[++index], interval_ms) || interval_ms < 0) {
                std::fprintf(stderr, "--interval-ms needs a non-negative integer\n");
                return 2;
            }
            continue;
        }
        if (arg == "--filesystem") {
            if (index + 1 >= argc) {
                std::fprintf(stderr, "--filesystem needs ROLE=PATH\n");
                return 2;
            }
            filesystem_args.emplace_back(argv[++index]);
            continue;
        }
        if (arg == "--interface") {
            if (index + 1 >= argc) {
                std::fprintf(stderr, "--interface needs a name\n");
                return 2;
            }
            interface_args.emplace_back(argv[++index]);
            continue;
        }
        std::fprintf(stderr, "unrecognised argument: %s\n", arg.c_str());
        print_usage(stderr);
        return 2;
    }

    nodecore::SampleRequest request;
    if (filesystem_args.empty()) {
#ifdef _WIN32
        request.filesystems = nodecore::default_windows_filesystems();
#else
        request.filesystems = {{"ROOT_FS", "/"}, {"DATA_FS", "/data"}};
#endif
    } else {
        for (const std::string& spec : filesystem_args) {
            const std::size_t equals = spec.find('=');
            if (equals == std::string::npos || equals == 0 || equals + 1 >= spec.size()) {
                std::fprintf(stderr, "--filesystem expects ROLE=PATH, got: %s\n", spec.c_str());
                return 2;
            }
            request.filesystems.emplace_back(spec.substr(0, equals), spec.substr(equals + 1));
        }
    }
    request.interfaces = interface_args;

    nodecore::SnapshotOptions options;
    options.collector_version = kVersion;
#ifdef _WIN32
    // The Windows door: Win32 queries instead of /proc. --interface is accepted
    // and ignored, because this version reads no interface counters there.
    const auto sample_now = [&request]() {
        return nodecore::sample_from_windows_facts(
            nodecore::read_windows_facts(request.filesystems), std::chrono::system_clock::now());
    };
    const nodecore::Sample before = sample_now();
    if (interval_ms > 0) {
        std::this_thread::sleep_for(std::chrono::milliseconds(interval_ms));
    }
    const nodecore::Sample after = sample_now();
    options.live = true;
#else
    request.self_uid = static_cast<long>(::getuid());

    nodecore::SystemFileSource files;
    nodecore::SystemClock clock;

    const nodecore::Sample before = nodecore::take_sample(files, clock, request);
    if (interval_ms > 0) {
        std::this_thread::sleep_for(std::chrono::milliseconds(interval_ms));
    }
    const nodecore::Sample after = nodecore::take_sample(files, clock, request);
    options.live = files.is_live();
#endif

    const nodecore::Json document = nodecore::build_snapshot(before, after, options);
    const std::string text = compact ? document.dump_compact() : document.dump();
    std::fwrite(text.data(), 1, text.size(), stdout);
    std::fputc('\n', stdout);
    return 0;
}
