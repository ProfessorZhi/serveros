#include "windows-core/watch.hpp"
#include "windows-core/status_document.hpp"
#include "windows-core/status_pipe.hpp"
#include "windows-core/win32_scm.hpp"
#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <windows.h>
#include <ws2tcpip.h>
#include <chrono>
#include <ctime>
#include <iostream>
#include <iomanip>
#include <sstream>
namespace nodecore::windows_core {
namespace {
struct Network {
    WSADATA data{};
    bool ok = ::WSAStartup(MAKEWORD(2, 2), &data) == 0;
    ~Network() {
        if (ok)
            ::WSACleanup();
    }
};
struct Socket {
    SOCKET value = INVALID_SOCKET;
    ~Socket() {
        if (value != INVALID_SOCKET)
            ::closesocket(value);
    }
};
bool wait_socket(SOCKET socket, bool write, ULONGLONG deadline, bool connecting = false) {
    auto now = ::GetTickCount64();
    if (now >= deadline)
        return false;
    auto remaining = deadline - now;
    fd_set set;
    FD_ZERO(&set);
    FD_SET(socket, &set);
    timeval timeout{static_cast<long>(remaining / 1000),
                    static_cast<long>((remaining % 1000) * 1000)};
    fd_set errors;
    FD_ZERO(&errors);
    if (connecting)
        FD_SET(socket, &errors);
    return ::select(0, write ? nullptr : &set, write ? &set : nullptr,
                    connecting ? &errors : nullptr, &timeout) > 0;
}
ProbeReadiness http_probe(const HttpProbe &spec) {
    Network network;
    if (!network.ok)
        return ProbeReadiness::Unmeasured;
    Socket socket;
    socket.value = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (socket.value == INVALID_SOCKET)
        return ProbeReadiness::Unmeasured;
    u_long nonblocking = 1;
    if (::ioctlsocket(socket.value, FIONBIO, &nonblocking) != 0)
        return ProbeReadiness::Unmeasured;
    auto deadline = ::GetTickCount64() + 3000;
    sockaddr_in target{};
    target.sin_family = AF_INET;
    target.sin_addr.s_addr = ::htonl(INADDR_LOOPBACK);
    target.sin_port = ::htons(static_cast<u_short>(spec.port));
    if (::connect(socket.value, reinterpret_cast<sockaddr *>(&target), sizeof(target)) ==
        SOCKET_ERROR) {
        auto error = ::WSAGetLastError();
        if (error == WSAECONNREFUSED)
            return ProbeReadiness::NotReady;
        if (error != WSAEWOULDBLOCK && error != WSAEINPROGRESS)
            return ProbeReadiness::Unmeasured;
        if (!wait_socket(socket.value, true, deadline, true))
            return ProbeReadiness::Unmeasured;
        int result = 0;
        int n = sizeof(result);
        if (::getsockopt(socket.value, SOL_SOCKET, SO_ERROR, reinterpret_cast<char *>(&result),
                         &n) != 0)
            return ProbeReadiness::Unmeasured;
        if (result == WSAECONNREFUSED)
            return ProbeReadiness::NotReady;
        if (result)
            return ProbeReadiness::Unmeasured;
    }
    std::string request =
        "GET " + spec.path + " HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    std::size_t offset = 0;
    while (offset < request.size()) {
        if (!wait_socket(socket.value, true, deadline))
            return ProbeReadiness::Unmeasured;
        int n = ::send(socket.value, request.data() + offset,
                       static_cast<int>(request.size() - offset), 0);
        if (n == SOCKET_ERROR) {
            if (::WSAGetLastError() == WSAEWOULDBLOCK)
                continue;
            return ProbeReadiness::Unmeasured;
        }
        if (n <= 0)
            return ProbeReadiness::Unmeasured;
        offset += n;
    }
    std::string response;
    char buffer[1024];
    while (response.size() <= 16384) {
        if (!wait_socket(socket.value, false, deadline))
            return ProbeReadiness::Unmeasured;
        int n = ::recv(socket.value, buffer, sizeof(buffer), 0);
        if (n == 0)
            return parse_http_probe(response, spec);
        if (n == SOCKET_ERROR) {
            if (::WSAGetLastError() == WSAEWOULDBLOCK)
                continue;
            return ProbeReadiness::Unmeasured;
        }
        response.append(buffer, n);
        // A complete valid length-delimited reply need not wait for peer close.
        auto reading = parse_http_probe(response, spec);
        if (reading != ProbeReadiness::Unmeasured)
            return reading;
    }
    return ProbeReadiness::Unmeasured;
}
std::string utc_now() {
    SYSTEMTIME s{};
    ::GetSystemTime(&s);
    std::ostringstream out;
    out << std::setfill('0') << std::setw(4) << s.wYear << '-' << std::setw(2) << s.wMonth << '-'
        << std::setw(2) << s.wDay << 'T' << std::setw(2) << s.wHour << ':' << std::setw(2)
        << s.wMinute << ':' << std::setw(2) << s.wSecond << "Z";
    return out.str();
}
std::atomic<bool> service_stop{false};
SRWLOCK service_lock = SRWLOCK_INIT;
SERVICE_STATUS_HANDLE service_handle = nullptr;
WatchServicePhase service_phase = WatchServicePhase::Starting;
DWORD service_checkpoint = 0;
struct ServiceGuard {
    ServiceGuard() { ::AcquireSRWLockExclusive(&service_lock); }
    ~ServiceGuard() { ::ReleaseSRWLockExclusive(&service_lock); }
};
void report_service(WatchServicePhase next, DWORD error = NO_ERROR) {
    ServiceGuard guard;
    if (!service_phase_transition(service_phase, next))
        return;
    SERVICE_STATUS status{};
    status.dwServiceType = SERVICE_WIN32_OWN_PROCESS;
    status.dwCurrentState = next == WatchServicePhase::Starting   ? SERVICE_START_PENDING
                            : next == WatchServicePhase::Running  ? SERVICE_RUNNING
                            : next == WatchServicePhase::Stopping ? SERVICE_STOP_PENDING
                                                                  : SERVICE_STOPPED;
    status.dwControlsAccepted =
        next == WatchServicePhase::Running ? SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN : 0;
    status.dwWin32ExitCode = error;
    status.dwServiceSpecificExitCode = error == ERROR_SERVICE_SPECIFIC_ERROR ? 1 : 0;
    bool pending = next == WatchServicePhase::Starting || next == WatchServicePhase::Stopping;
    status.dwWaitHint = pending ? 30000 : 0;
    status.dwCheckPoint = pending ? ++service_checkpoint : 0;
    service_phase = next;
    if (service_handle)
        ::SetServiceStatus(service_handle, &status);
}
void WINAPI control_service(DWORD control) {
    if (control == SERVICE_CONTROL_STOP || control == SERVICE_CONTROL_SHUTDOWN) {
        // Publish STOP_PENDING before allowing the loop to publish STOPPED.
        report_service(WatchServicePhase::Stopping);
        service_stop.store(true);
    }
}
void WINAPI service_main(DWORD, LPWSTR *) {
    service_stop.store(false);
    auto handle = ::RegisterServiceCtrlHandlerW(L"ServerOSCoreUser", control_service);
    if (!handle)
        return;
    {
        ServiceGuard guard;
        service_handle = handle;
    }
    report_service(WatchServicePhase::Starting);
    report_service(WatchServicePhase::Running);
    int result = run_windows_watch(service_stop);
    report_service(WatchServicePhase::Stopped,
                   result == 0 ? NO_ERROR : ERROR_SERVICE_SPECIFIC_ERROR);
}

} // namespace
ProbeReadiness LoopbackFunctionalProbe::check(UnitRole role) {
    for (const auto &unit : config_.services)
        if (unit.grant.role == role)
            return http_probe(unit.probe);
    return ProbeReadiness::Unmeasured;
}
int run_windows_watch(std::atomic<bool> &stop) {
    auto local = open_local_watch_state();
    if (!local) {
        std::cerr << "LOCAL_OWNER_OR_WRITER_UNTRUSTED\n";
        return 1;
    }
    bool announced = false;
    while (!stop.load()) {
        auto tick = ::GetTickCount64();
        WindowsStatusInput input;
        input.node = "unattested-local";
        input.principal = local->owner_sid();
        input.observed_at = utc_now();
        WatchConfig config;
        std::string source, error;
        bool usable = local->read_desired(source) &&
                      parse_watch_config(source, local->owner_sid(), config, error);
        if (usable && !local->commit_generation(config.generation, source)) {
            usable = false;
            error = "GENERATION_FENCE_UNTRUSTED";
        }
        if (usable) {
            input.node = config.node;
            input.principal = config.principal;
            input.generation = static_cast<long>(config.generation);
            input.recover_local = !config.observe_only;
            LoopbackFunctionalProbe probe(config);
            Win32RecoveryBackend backend(probe);
            Win32RecoveryStore store;
            input.runtime = watch_runtime_pass(config, store, backend,
                                               static_cast<std::int64_t>(std::time(nullptr)));
            Win32ScmQuery scm;
            Win32TaskQuery task;
            for (const auto &unit : config.services) {
                if (unit.grant.manager == UnitManager::Scm)
                    input.services.push_back(scm.query(unit.grant.target));
                else
                    input.tasks.push_back(task.query(unit.grant.target));
            }
        } else
            input.runtime_reason = error.empty() ? "LOCAL_CONFIG_MISSING_OR_UNTRUSTED" : error;
        auto snapshot = build_windows_status_json(input);
        if (!local->publish(snapshot)) {
            std::cerr << "STATUS_PUBLISH_FAILED\n";
            return 1;
        }
        unsigned interval = usable ? config.interval_ms : 5000;
        auto elapsed = ::GetTickCount64() - tick;
        if (elapsed < interval && !stop.load()) {
            unsigned left = static_cast<unsigned>(interval - elapsed);
            auto served = serve_status_pipe(snapshot, {std::min(500U, left), left, 32},
                                            [&](const PipeIdentity &id) {
                                                if (!announced) {
                                                    std::cout << id.pid << ' ' << id.creation_time
                                                              << ' ' << id.owner_sid << std::endl;
                                                    announced = true;
                                                }
                                            });
            if (!served.ok && served.code != "DEADLINE_OR_PROTOCOL") {
                std::cerr << served.code << '\n';
                return 1;
            }
        }
        // Client traffic cannot shorten the pass interval or accelerate probes.
        while (!stop.load() && ::GetTickCount64() - tick < interval)
            ::Sleep(25);
    }
    return 0;
}
int run_windows_service() {
    SERVICE_TABLE_ENTRYW table[] = {{const_cast<LPWSTR>(L"ServerOSCoreUser"), service_main},
                                    {nullptr, nullptr}};
    return ::StartServiceCtrlDispatcherW(table) ? 0 : 1;
}
} // namespace nodecore::windows_core
#else
namespace nodecore::windows_core {
ProbeReadiness LoopbackFunctionalProbe::check(UnitRole) { return ProbeReadiness::Unmeasured; }
int run_windows_watch(std::atomic<bool> &) { return 1; }
int run_windows_service() { return 1; }
} // namespace nodecore::windows_core
#endif
