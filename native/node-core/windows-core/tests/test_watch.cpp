#include "check.hpp"
#include "windows-core/watch.hpp"
#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <winsock2.h>
#include <windows.h>
#include <thread>
#endif
using namespace nodecore::windows_core;
struct TestTx : RecoveryTransaction {
    RecoveryState &state;
    explicit TestTx(RecoveryState &s) : state(s) {}
    bool load(RecoveryState &out) override {
        out = state;
        return true;
    }
    bool save(const RecoveryState &in) override {
        state = in;
        return true;
    }
};
struct TestStore : RecoveryStore {
    RecoveryState state;
    std::unique_ptr<RecoveryTransaction> lock(const UnitGrant &) override {
        return std::make_unique<TestTx>(state);
    }
};
struct TestBackend : RecoveryBackend {
    int starts = 0, inspections = 0;
    UnitState inspect(const UnitGrant &) override {
        inspections++;
        return {true, true, true, UnitCondition::Stopped};
    }
    ProbeReadiness probe(const UnitGrant &) override {
        return starts ? ProbeReadiness::Ready : ProbeReadiness::NotReady;
    }
    StartOutcome start(const UnitGrant &) override {
        starts++;
        return StartOutcome::Accepted;
    }
};
int main() {
    CHECK(!service_phase_transition(WatchServicePhase::Stopped, WatchServicePhase::Stopping));
    CHECK(!service_phase_transition(WatchServicePhase::Stopping, WatchServicePhase::Running));
    CHECK(service_phase_transition(WatchServicePhase::Running, WatchServicePhase::Stopping));
    CHECK(service_phase_transition(WatchServicePhase::Stopping, WatchServicePhase::Stopped));
    CHECK(service_phase_transition(WatchServicePhase::Starting, WatchServicePhase::Stopped));

    const std::string sid = "S-1-5-21-1";
    const std::string valid =
        R"({"schema":"serveros/windows-core-desired.v1","node":"device:fixture","principal":"S-1-5-21-1","generation":1,"services":[{"id":"cloudcli","owner":"S-1-5-21-1","manager":"scm","unit":"SyntheticService","desired":"running","health":{"probe":"loopback-http-json","port":8080,"path":"/health","key":"status","value":"ok"}}]})";
    WatchConfig cfg;
    std::string error;
    CHECK(parse_watch_config(valid, sid, cfg, error));
    CHECK(cfg.observe_only);
    CHECK_EQ(cfg.services.size(), std::size_t(1));
    CHECK(!parse_watch_config(valid, "S-1-5-21-2", cfg, error));
    for (auto pair : {std::pair{"\"running\"", "\"runing\""}, std::pair{"\"scm\"", "\"shell\""},
                      std::pair{"\"/health\"", "\"/health\\r\\nX: z\""}, std::pair{"8080", "0"},
                      std::pair{"\"generation\":1", "\"generation\":-1"}}) {
        auto bad = valid;
        auto pos = bad.find(pair.first);
        bad.replace(pos, std::string(pair.first).size(), pair.second);
        CHECK(!parse_watch_config(bad, sid, cfg, error));
    }
    auto unknown = valid;
    unknown.insert(1, "\"command\":\"bad\",");
    CHECK(!parse_watch_config(unknown, sid, cfg, error));
    auto duplicate = valid;
    duplicate.insert(1, "\"generation\":2,");
    CHECK(!parse_watch_config(duplicate, sid, cfg, error));
    CHECK(parse_watch_config(valid, sid, cfg, error));
    auto &probe = cfg.services[0].probe;
    CHECK_EQ(
        parse_http_probe("HTTP/1.1 200 OK\r\nContent-Length: 15\r\n\r\n{\"status\":\"ok\"}", probe),
        ProbeReadiness::Ready);
    CHECK_EQ(parse_http_probe("HTTP/1.1 503 Bad\r\nContent-Length: 15\r\n\r\n{\"status\":\"ok\"}",
                              probe),
             ProbeReadiness::NotReady);
    CHECK_EQ(parse_http_probe("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n", probe),
             ProbeReadiness::Unmeasured);
    CHECK_EQ(
        parse_http_probe(
            "HTTP/1.1 200 OK\r\nContent-Length: 15\r\nContent-Length: 1\r\n\r\n{\"status\":\"ok\"}",
            probe),
        ProbeReadiness::Unmeasured);
    CHECK_EQ(parse_http_probe("HTTP/1.1 200 OK\r\nContent-Length: 16\r\n\r\n{\"status\":\"bad\"}",
                              probe),
             ProbeReadiness::NotReady);
    {
        CHECK(parse_watch_config(valid, sid, cfg, error));
        TestStore store;
        TestBackend backend;
        auto rows = watch_runtime_pass(cfg, store, backend, 10000);
        CHECK_EQ(backend.starts, 0);
        CHECK_EQ(rows[0].recovery_code, std::string("OBSERVE_ONLY"));
        CHECK_EQ(backend.inspections, 0);
        cfg.observe_only = false;
        cfg.services[0].desired = "manual";
        cfg.services[0].grant.enabled = false;
        rows = watch_runtime_pass(cfg, store, backend, 10000);
        CHECK_EQ(backend.starts, 0);
        CHECK_EQ(rows[0].recovery_code, std::string("DESIRED_MANUAL"));
        CHECK_EQ(backend.inspections, 0);
        cfg.services[0].desired = "running";
        cfg.services[0].grant.enabled = true;
        rows = watch_runtime_pass(cfg, store, backend, 10000);
        CHECK_EQ(backend.starts, 1);
        CHECK_EQ(rows[0].recovery_code, std::string("RECOVERED"));
        CHECK_EQ(rows[0].readiness, std::string("READY"));
    }
#if defined(_WIN32)
    {
        WSADATA network{};
        CHECK(::WSAStartup(MAKEWORD(2, 2), &network) == 0);
        SOCKET listener = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        CHECK(listener != INVALID_SOCKET);
        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_addr.s_addr = ::htonl(INADDR_LOOPBACK);
        CHECK(::bind(listener, reinterpret_cast<sockaddr *>(&address), sizeof(address)) == 0);
        int size = sizeof(address);
        CHECK(::getsockname(listener, reinterpret_cast<sockaddr *>(&address), &size) == 0);
        CHECK(::listen(listener, 1) == 0);
        WatchConfig native;
        WatchUnit unit;
        unit.grant.role = UnitRole::CloudCli;
        unit.probe = {static_cast<unsigned>(::ntohs(address.sin_port)),
                      "/health",
                      "status",
                      "ok",
                      false,
                      false};
        native.services.push_back(unit);
        // The owned listener exists, then is stopped before probing. Winsock
        // must report its async refusal as a measured NOT_READY, not timeout.
        ::closesocket(listener);
        LoopbackFunctionalProbe probe(native);
        CHECK_EQ(probe.check(UnitRole::CloudCli), ProbeReadiness::NotReady);
        ::WSACleanup();
    }
#endif
    return check::report("windows_watch_contract");
}
