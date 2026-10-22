#include "windows-core/watch.hpp"
#include <csignal>
#include <iostream>
#include "nodecore/json.hpp"
#ifndef SERVEROS_WINDOWS_COMPONENT_VERSION
#define SERVEROS_WINDOWS_COMPONENT_VERSION "development"
#define SERVEROS_WINDOWS_SOURCE_REVISION "unknown"
#endif
namespace {
std::atomic<bool> stop{false};
void terminate(int) { stop.store(true); }
} // namespace
int main(int argc, char **argv) {
    if (argc == 2 && std::string(argv[1]) == "--version") {
        auto version = nodecore::Json::object();
        version.set("component", nodecore::Json::string("serveros-windows-core"));
        version.set("version", nodecore::Json::string(SERVEROS_WINDOWS_COMPONENT_VERSION));
        version.set("source_revision", nodecore::Json::string(SERVEROS_WINDOWS_SOURCE_REVISION));
        std::cout << version.dump_compact() << '\n';
        return 0;
    }

    if (argc == 2 && std::string(argv[1]) == "configure") {
        std::string text;
        char byte;
        while (std::cin.get(byte)) {
            if (text.size() >= 16384)
                return 2;
            text.push_back(byte);
        }
        bool ok = nodecore::windows_core::publish_windows_desired(text);
        std::cerr << (ok ? "CONFIGURED" : "CONFIG_REFUSED") << '\n';
        return ok ? 0 : 1;
    }
    if (argc == 2 && std::string(argv[1]) == "service")
        return nodecore::windows_core::run_windows_service();
    if (argc == 2 && std::string(argv[1]) == "console") {
        std::signal(SIGINT, terminate);
        std::signal(SIGTERM, terminate);
        return nodecore::windows_core::run_windows_watch(stop);
    }
    std::cerr
        << "usage: windows-core-watch console | service | configure < protected-manifest.json\n";
    return 2;
}
