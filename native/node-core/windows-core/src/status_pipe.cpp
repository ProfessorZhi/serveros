#include "windows-core/status_pipe.hpp"
#include "win32_detail.hpp"
#include <algorithm>
#include <exception>
#include <limits>
namespace nodecore::windows_core {
bool valid_pipe_limits(PipeLimits l) {
    return l.transaction_ms >= 1 && l.transaction_ms <= 10000 &&
           l.lifetime_ms >= l.transaction_ms && l.lifetime_ms <= 60000 && l.max_connections >= 1 &&
           l.max_connections <= 128;
}
std::string encode_status_frame(const std::string &s) {
    if (s.empty() || s.size() > kMaxStatusFrame)
        return {};
    auto n = static_cast<std::uint32_t>(s.size());
    std::string out(4, '\0');
    for (unsigned i = 0; i < 4; i++)
        out[i] = static_cast<char>((n >> (8 * i)) & 255);
    return out + s;
}
bool decode_status_frame(const std::string &frame, std::string &out) {
    if (frame.size() < 4)
        return false;
    std::uint32_t n = 0;
    for (unsigned i = 0; i < 4; i++)
        n |= static_cast<std::uint32_t>(static_cast<unsigned char>(frame[i])) << (8 * i);
    if (n == 0 || n > kMaxStatusFrame || frame.size() != 4 + n)
        return false;
    out = frame.substr(4);
    return true;
}
std::wstring pipe_name(const PipeIdentity &id) {
    if (id.owner_sid.rfind("S-1-", 0) != 0 || id.owner_sid.size() > 184 || id.pid == 0 ||
        id.creation_time == 0)
        return {};
    for (char c : id.owner_sid)
        if (!((c >= '0' && c <= '9') || c == 'S' || c == '-'))
            return {};
    return L"\\\\.\\pipe\\ServerOS.Core." + std::wstring(id.owner_sid.begin(), id.owner_sid.end()) +
           L"." + std::to_wstring(id.pid) + L"." + std::to_wstring(id.creation_time);
}
#if defined(_WIN32)
namespace {
using detail::Handle;
DWORD remaining(ULONGLONG end) {
    auto now = ::GetTickCount64();
    return now >= end ? 0 : static_cast<DWORD>(std::min<ULONGLONG>(end - now, 60000));
}
bool finish(HANDLE h, OVERLAPPED &ov, ULONGLONG end, DWORD &count) {
    DWORD left = remaining(end);
    if (!left || ::WaitForSingleObject(ov.hEvent, left) != WAIT_OBJECT_0) {
        ::CancelIoEx(h, &ov);
        ::GetOverlappedResult(h, &ov, &count, TRUE);
        return false;
    }
    return ::GetOverlappedResult(h, &ov, &count, FALSE) != 0;
}
bool transfer(HANDLE h, void *buffer, DWORD len, bool write, ULONGLONG end) {
    auto *bytes = static_cast<unsigned char *>(buffer);
    DWORD done = 0;
    while (done < len) {
        if (!remaining(end))
            return false;
        Handle event(::CreateEventW(nullptr, TRUE, FALSE, nullptr));
        if (!event.valid())
            return false;
        OVERLAPPED ov{};
        ov.hEvent = event;
        DWORD n = 0;
        BOOL ok = write ? ::WriteFile(h, bytes + done, len - done, &n, &ov)
                        : ::ReadFile(h, bytes + done, len - done, &n, &ov);
        if (!ok && (::GetLastError() != ERROR_IO_PENDING || !finish(h, ov, end, n)))
            return false;
        if (n == 0)
            return false;
        done += n;
    }
    return true;
}
bool read_frame(HANDLE h, std::string &text, ULONGLONG end) {
    unsigned char hdr[4];
    if (!transfer(h, hdr, 4, false, end))
        return false;
    std::uint32_t n = 0;
    for (unsigned i = 0; i < 4; i++)
        n |= static_cast<std::uint32_t>(hdr[i]) << (8 * i);
    if (n == 0 || n > kMaxStatusFrame)
        return false;
    text.assign(n, '\0');
    return transfer(h, text.data(), n, false, end);
}
bool write_frame(HANDLE h, const std::string &text, ULONGLONG end) {
    auto f = encode_status_frame(text);
    return !f.empty() && transfer(h, f.data(), static_cast<DWORD>(f.size()), true, end);
}
bool client_owner(HANDLE pipe, const std::string &sid) {
    if (!::ImpersonateNamedPipeClient(pipe))
        return false;
    HANDLE raw = nullptr;
    bool match = false;
    if (::OpenThreadToken(::GetCurrentThread(), TOKEN_QUERY, TRUE, &raw)) {
        Handle token(raw);
        match = detail::token_sid(token) == sid;
    }
    // Failure to revert must terminate before running any trusted owner callback.
    if (!::RevertToSelf()) {
        ::TerminateProcess(::GetCurrentProcess(), ERROR_ACCESS_DENIED);
        std::terminate();
    }
    return match;
}
bool pinned_server(HANDLE pipe, const PipeIdentity &id) {
    ULONG pid = 0;
    if (!::GetNamedPipeServerProcessId(pipe, &pid) || pid != id.pid)
        return false;
    Handle process(::OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid));
    std::uint64_t creation = 0;
    return process.valid() && detail::creation_time(process, creation) &&
           creation == id.creation_time && detail::process_sid(process) == id.owner_sid;
}
} // namespace
bool current_pipe_identity(PipeIdentity &id) {
    id.owner_sid = detail::current_sid();
    id.pid = ::GetCurrentProcessId();
    return !id.owner_sid.empty() && detail::creation_time(::GetCurrentProcess(), id.creation_time);
}
PipeResult read_status_pipe(const PipeIdentity &expected, PipeLimits limits) {
    if (!valid_pipe_limits(limits) || pipe_name(expected).empty())
        return {false, "INVALID_ARGUMENT", {}};
    // Only the current user's service is addressed; caller text cannot choose a
    // different principal. SECURITY_IDENTIFICATION prevents server impersonation.
    if (expected.owner_sid != detail::current_sid())
        return {false, "OWNER_MISMATCH", {}};
    auto deadline = ::GetTickCount64() + limits.transaction_ms;
    Handle pipe(::CreateFileW(
        pipe_name(expected).c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
        FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, nullptr));
    if (!pipe.valid())
        return {false, "CONNECT_FAILED", {}};
    if (!pinned_server(pipe, expected))
        return {false, "SERVER_IDENTITY_MISMATCH", {}};
    std::string response;
    if (!write_frame(pipe, "status.v1", deadline) || !read_frame(pipe, response, deadline))
        return {false, "DEADLINE_OR_PROTOCOL", {}};
    if (!write_frame(pipe, "received.v1", deadline))
        return {false, "DEADLINE_OR_PROTOCOL", {}};
    return {true, "OK", response};
}
PipeResult serve_status_pipe(const std::string &status, PipeLimits limits,
                             const std::function<void(const PipeIdentity &)> &bound) {
    if (!valid_pipe_limits(limits) || status.empty() || status.size() > kMaxStatusFrame)
        return {false, "INVALID_ARGUMENT", {}};
    PipeIdentity id;
    if (!current_pipe_identity(id))
        return {false, "IDENTITY_UNAVAILABLE", {}};
    detail::OwnerSecurity security(id.owner_sid);
    if (!security.descriptor)
        return {false, "ACL_FAILED", {}};
    Handle pipe(::CreateNamedPipeW(
        pipe_name(id).c_str(),
        PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, 1, 65540,
        65540, 0, &security.attributes));
    if (!pipe.valid())
        return {false, "CREATE_OR_SQUATTING_FAILED", {}};
    if (bound)
        bound(id);
    auto lifetime = ::GetTickCount64() + limits.lifetime_ms;
    for (unsigned count = 0; count < limits.max_connections; count++) {
        if (::GetTickCount64() >= lifetime)
            return {true, "LIFETIME_EXHAUSTED", {}};
        Handle event(::CreateEventW(nullptr, TRUE, FALSE, nullptr));
        if (!event.valid())
            return {false, "EVENT_FAILED", {}};
        OVERLAPPED ov{};
        ov.hEvent = event;
        DWORD n = 0;
        bool connected = ::ConnectNamedPipe(pipe, &ov) != 0;
        if (!connected) {
            auto error = ::GetLastError();
            connected = error == ERROR_PIPE_CONNECTED ||
                        (error == ERROR_IO_PENDING && finish(pipe, ov, lifetime, n));
        }
        if (!connected)
            return {true, "LIFETIME_EXHAUSTED", {}};
        auto deadline = std::min(lifetime, ::GetTickCount64() + limits.transaction_ms);
        std::string request;
        if (read_frame(pipe, request, deadline) && request == "status.v1" &&
            client_owner(pipe, id.owner_sid)) {
            std::string receipt;
            if (remaining(deadline) && write_frame(pipe, status, deadline)) {
                // DisconnectNamedPipe discards unread buffered bytes. A bounded receipt
                // confirms the client consumed the frame without unbounded
                // FlushFileBuffers.
                read_frame(pipe, receipt, deadline);
            }
        }
        ::DisconnectNamedPipe(pipe);
    }
    return {true, "CONNECTION_BUDGET_EXHAUSTED", {}};
}
#else
bool current_pipe_identity(PipeIdentity &) { return false; }
PipeResult read_status_pipe(const PipeIdentity &, PipeLimits) {
    return {false, "UNSUPPORTED_PLATFORM", {}};
}
PipeResult serve_status_pipe(const std::string &, PipeLimits,
                             const std::function<void(const PipeIdentity &)> &) {
    return {false, "UNSUPPORTED_PLATFORM", {}};
}
#endif
} // namespace nodecore::windows_core
