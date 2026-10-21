#include "nodecore/json.hpp"
#include "windows-core/status_document.hpp"
#include "windows-core/status_pipe.hpp"
#include "windows-core/win32_scm.hpp"
#include <charconv>
#include <iostream>
using namespace nodecore::windows_core;
namespace {
template <class T> bool number(const char *p, T &out) {
    std::string s(p);
    auto [end, error] = std::from_chars(s.data(), s.data() + s.size(), out);
    return error == std::errc{} && end == s.data() + s.size();
}
} // namespace
int main(int argc, char **argv) {
    if (argc >= 2 && std::string(argv[1]) == "serve" && argc <= 18) {
        WindowsStatusInput input;
        input.node = "unattested-local";
        input.principal = "unattested-local";
        Win32ScmQuery query;
        for (int i = 2; i < argc; i++) {
            if (!valid_service_name(argv[i])) {
                std::cerr << "INVALID_SERVICE_NAME\n";
                return 2;
            }
            input.services.push_back(query.query(argv[i]));
        }
        auto snapshot = build_windows_status_json(input);
        auto result = serve_status_pipe(snapshot, {}, [](const PipeIdentity &id) {
            // Trusted launcher captures this stdout at process creation and binds it
            // to its child process handle. Do not discover this identity over the
            // pipe.
            auto receipt = nodecore::Json::object();
            receipt.set("pid", nodecore::Json::uinteger(id.pid));
            receipt.set("creation_time", nodecore::Json::uinteger(id.creation_time));
            receipt.set("owner_sid", nodecore::Json::string(id.owner_sid));
            std::cout << receipt.dump_compact() << std::endl;
        });
        std::cerr << result.code << '\n';
        return result.ok ? 0 : 1;
    }
    if (argc == 5 && std::string(argv[1]) == "read") {
        PipeIdentity id;
        id.owner_sid = argv[2];
        if (!number(argv[3], id.pid) || !number(argv[4], id.creation_time))
            return 2;
        auto result = read_status_pipe(id);
        if (result.ok)
            std::cout << result.payload << '\n';
        else
            std::cerr << result.code << '\n';
        return result.ok ? 0 : 1;
    }
    std::cerr << "usage: windows-core-status serve [service ...] | read "
                 "PINNED_SID PINNED_PID PINNED_CREATION_TIME\n";
    return 2;
}
