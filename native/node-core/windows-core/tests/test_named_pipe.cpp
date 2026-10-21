#include "check.hpp"
#include "windows-core/recovery.hpp"
#include "windows-core/status_pipe.hpp"
#include <chrono>
#include <future>
#include <thread>
#if defined(_WIN32)
#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif
using namespace nodecore::windows_core;
struct UnknownProbe : FunctionalProbe {
    ProbeReadiness check(UnitRole) override { return ProbeReadiness::Unmeasured; }
};
int main() {
    UnknownProbe functional;
    Win32RecoveryBackend backend(functional);
    UnitGrant invalid{UnitRole::CloudCli, UnitManager::Scm, "", "", false};
    CHECK(!backend.inspect(invalid).measured);
    CHECK(backend.start(invalid) == StartOutcome::Refused);
#if defined(_WIN32)
    PipeIdentity self;
    CHECK(current_pipe_identity(self));
    {
        std::promise<PipeIdentity> ready;
        auto wait = ready.get_future();
        PipeResult served;
        std::thread server([&] {
            served = serve_status_pipe("{\"synthetic\":true}", {500, 2000, 1},
                                       [&](auto &id) { ready.set_value(id); });
        });
        CHECK(wait.wait_for(std::chrono::seconds(2)) == std::future_status::ready);
        auto id = wait.get();
        auto response = read_status_pipe(id, {500, 2000, 1});
        CHECK(response.ok);
        CHECK_EQ(response.payload, std::string("{\"synthetic\":true}"));
        server.join();
        CHECK(served.ok);
    }
    {
        std::promise<PipeIdentity> ready;
        auto wait = ready.get_future();
        PipeResult served;
        auto start = std::chrono::steady_clock::now();
        std::thread server([&] {
            served = serve_status_pipe("snapshot", {100, 1000, 1},
                                       [&](auto &id) { ready.set_value(id); });
        });
        CHECK(wait.wait_for(std::chrono::seconds(2)) == std::future_status::ready);
        auto id = wait.get();
        HANDLE pipe = ::CreateFileW(pipe_name(id).c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr,
                                    OPEN_EXISTING, 0, nullptr);
        CHECK(pipe != INVALID_HANDLE_VALUE);
        server.join();
        if (pipe != INVALID_HANDLE_VALUE)
            ::CloseHandle(pipe);
        CHECK(served.ok);
        CHECK(std::chrono::steady_clock::now() - start < std::chrono::seconds(2));
    }
    {
        // Same-user instance squatting fails closed before serving a single byte.
        PipeIdentity id;
        CHECK(current_pipe_identity(id));
        auto pipe = ::CreateNamedPipeW(
            pipe_name(id).c_str(), PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
            PIPE_TYPE_BYTE | PIPE_REJECT_REMOTE_CLIENTS, 1, 1024, 1024, 0, nullptr);
        CHECK(pipe != INVALID_HANDLE_VALUE);
        auto result = serve_status_pipe("snapshot", {100, 1000, 1});
        CHECK(!result.ok);
        CHECK_EQ(result.code, std::string("CREATE_OR_SQUATTING_FAILED"));
        if (pipe != INVALID_HANDLE_VALUE)
            ::CloseHandle(pipe);
    }
    {
        // Squatted name encodes another process identity. OS server PID must still
        // match the pin; owner SID / pipe spelling alone cannot authenticate it.
        PipeIdentity fake = self;
        fake.pid += 100000;
        auto pipe = ::CreateNamedPipeW(
            pipe_name(fake).c_str(), PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
            PIPE_TYPE_BYTE | PIPE_REJECT_REMOTE_CLIENTS, 1, 1024, 1024, 0, nullptr);
        CHECK(pipe != INVALID_HANDLE_VALUE);
        if (pipe != INVALID_HANDLE_VALUE) {
            auto response = read_status_pipe(fake, {100, 1000, 1});
            CHECK(!response.ok);
            CHECK_EQ(response.code, std::string("SERVER_IDENTITY_MISMATCH"));
            ::CloseHandle(pipe);
        }
    }
    {
        // No SCM or task action. Actual native journal + fake backend exercise
        // durable pre-effect reservation and reconstruction on this hosted runner.
        PipeIdentity id;
        CHECK(current_pipe_identity(id));
        UnitGrant g{UnitRole::CloudCli, UnitManager::Scm,
                    "SyntheticCoreTest" + std::to_string(id.pid) + std::to_string(id.creation_time),
                    id.owner_sid, true};
        Win32RecoveryStore store;
        auto tx = store.lock(g);
        CHECK(tx != nullptr);
        if (tx) {
            RecoveryState state;
            CHECK(tx->load(state));
            state.last_seen = 10000;
            state.pending = true;
            state.attempts = {10000};
            CHECK(tx->save(state));
            CHECK(!store.lock(g));
            tx.reset();
            auto reopened = store.lock(g);
            CHECK(reopened != nullptr);
            if (reopened) {
                RecoveryState out;
                CHECK(reopened->load(out));
                CHECK(out.pending);
                CHECK_EQ(out.attempts, state.attempts);
            }
        }
    }
#else
    PipeIdentity id;
    CHECK(!current_pipe_identity(id));
    CHECK(!read_status_pipe(id).ok);
    CHECK(!serve_status_pipe("snapshot").ok);
    Win32RecoveryStore store;
    CHECK(!store.lock({UnitRole::CloudCli, UnitManager::Scm, "Synthetic", "S-1-5-21-1", true}));
#endif
    return check::report("windows_named_pipe_and_store");
}
