#include "check.hpp"
#include "windows-core/recovery.hpp"
#include "windows-core/status_pipe.hpp"
#include "windows-core/watch.hpp"
#include <chrono>
#include <future>
#include <thread>
#if defined(_WIN32)
#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include "win32_detail.hpp"
#include <shlobj.h>
#include <sstream>
#include <iomanip>
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
        std::wstring sid(self.owner_sid.begin(), self.owner_sid.end());
        CHECK(detail::task_execute_descriptor(L"O:" + sid + L"G:" + sid + L"D:(A;;0x1200a0;;;" +
                                              sid + L")"));
        CHECK(!detail::task_execute_descriptor(L"O:" + sid + L"G:" + sid + L"D:(D;;FA;;;" + sid +
                                               L")"));
        CHECK(!detail::task_execute_descriptor(L"D:(A;;FA;;;" + sid + L")"));
        CHECK(!detail::task_execute_descriptor(L"invalid descriptor"));
        PSID current = nullptr, other = nullptr;
        CHECK(::ConvertStringSidToSidW(sid.c_str(), &current) != 0);
        CHECK(::ConvertStringSidToSidW(L"S-1-5-21-999-999-999-999", &other) != 0);
        if (current && other) {
            CHECK(detail::trusted_profile_owner(current, current));
            CHECK(!detail::trusted_profile_owner(other, current));
        }
        if (current)
            ::LocalFree(current);
        if (other)
            ::LocalFree(other);
    }
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
        CHECK(!served.ok);
        CHECK_EQ(served.code, std::string("DEADLINE_OR_PROTOCOL"));
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
            tx.reset();
            tx = store.lock(g);
            CHECK(tx != nullptr);
            CHECK(tx && tx->load(state));
            if (!tx)
                return check::report("windows_named_pipe_and_store");
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
                reopened.reset();
                // Delete only this test's unique native journal on the hosted
                // runner; the old lock must prevent silently resetting it.
                PWSTR local = nullptr;
                CHECK(SUCCEEDED(::SHGetKnownFolderPath(FOLDERID_LocalAppData, 0, nullptr, &local)));
                if (local) {
                    std::uint64_t hash = 14695981039346656037ULL;
                    for (unsigned char c : unit_key(g)) {
                        hash ^= c;
                        hash *= 1099511628211ULL;
                    }
                    std::wostringstream name;
                    name << std::hex << std::setfill(L'0') << std::setw(16) << hash;
                    auto path =
                        std::wstring(local) + L"\\ServerOS\\Core\\" + name.str() + L".journal";
                    ::CoTaskMemFree(local);
                    CHECK(::DeleteFileW(path.c_str()) != 0);
                    auto missing = store.lock(g);
                    CHECK(missing != nullptr);
                    if (missing) {
                        RecoveryState absent;
                        CHECK(!missing->load(absent));
                    }
                }
            }
        }
    }
    {
        auto watch = open_local_watch_state();
        CHECK(watch != nullptr);
        CHECK(!open_local_watch_state());
        if (watch) {
            auto manifest =
                std::string(
                    "{\"schema\":\"serveros/"
                    "windows-core-desired.v1\",\"node\":\"device:fixture\",\"principal\":\"") +
                self.owner_sid + "\",\"generation\":1,\"services\":[]}";
            CHECK(publish_windows_desired(manifest));
            std::string loaded;
            CHECK(watch->read_desired(loaded));
            CHECK_EQ(loaded, manifest);
            CHECK(watch->commit_generation(1, manifest));
            CHECK(watch->commit_generation(1, manifest));
            CHECK(!watch->commit_generation(0, manifest));
            CHECK(!watch->commit_generation(1, manifest + " "));
            CHECK(watch->publish("{\"synthetic\":true}"));
            PWSTR appdata = nullptr;
            CHECK(SUCCEEDED(::SHGetKnownFolderPath(FOLDERID_LocalAppData, 0, nullptr, &appdata)));
            if (appdata) {
                auto path = std::wstring(appdata) + L"\\ServerOS\\Core\\desired-state.v1.json";
                ::CoTaskMemFree(appdata);
                HANDLE reader =
                    ::CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE,
                                  nullptr, OPEN_EXISTING, 0, nullptr);
                CHECK(reader != INVALID_HANDLE_VALUE);
                CHECK(publish_windows_desired(manifest + " "));
                CHECK(publish_windows_desired(manifest + "  "));
                if (reader != INVALID_HANDLE_VALUE)
                    ::CloseHandle(reader);
                // An incompatible external reader may legitimately prevent a
                // replace. This call must remove only its own temp, so a later
                // configure can succeed after that reader releases the file.
                reader = ::CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                                       OPEN_EXISTING, 0, nullptr);
                CHECK(reader != INVALID_HANDLE_VALUE);
                CHECK(!publish_windows_desired(manifest + "   "));
                if (reader != INVALID_HANDLE_VALUE)
                    ::CloseHandle(reader);
                CHECK(publish_windows_desired(manifest + "    "));
                CHECK(publish_windows_desired(manifest + "     "));
            }

            watch.reset();
            auto reopened = open_local_watch_state();
            CHECK(reopened != nullptr);
            if (reopened)
                CHECK(reopened->commit_generation(1, manifest));
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
