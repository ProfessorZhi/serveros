#pragma once
#include <cstdint>
#include <functional>
#include <string>
namespace nodecore::windows_core {
inline constexpr std::size_t kMaxStatusFrame = 65536;
struct PipeLimits {
    unsigned transaction_ms = 2000, lifetime_ms = 30000, max_connections = 32;
};
bool valid_pipe_limits(PipeLimits);
std::string encode_status_frame(const std::string &);
bool decode_status_frame(const std::string &, std::string &);
struct PipeIdentity {
    std::string owner_sid;
    std::uint32_t pid = 0;
    std::uint64_t creation_time = 0;
};
bool current_pipe_identity(PipeIdentity &);
std::wstring pipe_name(const PipeIdentity &);
struct PipeResult {
    bool ok = false;
    std::string code;
    std::string payload;
};
// identity must be pinned out of band by a trusted local launcher. This API
// never discovers server identity from the peer's response or a claimed SID.
PipeResult read_status_pipe(const PipeIdentity &, PipeLimits = {});
// Current-user token only. snapshot is prepared before binding. No callback or
// recovery request runs inside a transaction; connections and total serve
// lifetime are bounded.
PipeResult serve_status_pipe(const std::string &, PipeLimits = {},
                             const std::function<void(const PipeIdentity &)> &on_bound = {});
} // namespace nodecore::windows_core
