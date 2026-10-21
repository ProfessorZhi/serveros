// A user-scoped, read-only status socket.
//
// WHAT IT IS FOR
// --------------
// A resident Core has to be able to say "here is what I see" to the person
// whose Core it is, without a network port, without a client library and
// without a second daemon. A UNIX socket in that user's own runtime directory
// does exactly that and nothing else.
//
// WHAT IT IS NOT
// --------------
// It is NOT a control surface. It accepts one read request and answers it.
// There is no verb it can carry out, no argument that reaches a program, and no
// path by which a client can ask for anything to be started, stopped or
// restarted — the request is matched against a short allowlist of literals and
// everything else is refused without the status being computed at all.
//
// FOUR RULES, EACH ENFORCED RATHER THAN INTENDED
// ----------------------------------------------
//   * the path lives under `$XDG_RUNTIME_DIR`, which is 0700 by construction and
//     is emptied at logout. There is NO fallback to `/tmp`: a predictable path
//     in a world-writable directory is a socket any local account can create
//     first, and creating it first is how a hijack starts.
//   * the socket file is 0600 and its directory 0700, so another account cannot
//     connect at all — and `SO_PEERCRED` is checked as well, because a mode bit
//     and a kernel-reported identity are two independent answers to one
//     question and the second does not depend on the first being right.
//   * an existing path is REFUSED, never unlinked. Unlinking a live daemon's
//     socket leaves that daemon listening where nobody can reach it, and
//     unlinking a stale one is a decision for a person, not for a program that
//     cannot tell the two apart.
//   * a request is bounded — a maximum of bytes and a deadline — so a client
//     that sends one byte a second cannot hold the loop.
#pragma once

#include <cstddef>
#include <functional>
#include <string>

namespace nodecore::supervisor {

// Where this user's Core puts its status socket.
//
// `$XDG_RUNTIME_DIR/serveros/native-core.<uid>.sock`. A missing, empty or
// relative `XDG_RUNTIME_DIR` is a REFUSAL and not a fallback: the whole point of
// the path is that its parent is private, and a guess about where "private"
// might be is where the socket ends up somewhere shared.
struct SocketPath {
    bool ok = false;
    std::string path;
    std::string error;
};
SocketPath status_socket_path(const std::string& xdg_runtime_dir, long uid);

struct BindOutcome {
    bool ok = false;
    int fd = -1;
    std::string error;
};

// Creates `serveros/` inside the runtime directory (0700) and binds the socket
// (0600). Refuses if the path already exists, in any form.
BindOutcome bind_user_status_socket(const std::string& path);

// Whether a connection from `peer_uid` may be answered by a Core running as
// `expected_uid`. An unreadable peer credential (-1) is REFUSED: "I could not
// establish who this is" must not resolve to "let them in".
bool peer_allowed(long peer_uid, long expected_uid);

// Serves one accepted connection and closes it. `status_json` is called ONLY
// when the request is permitted, which is what makes "no illegal command
// computes anything" checkable.
void serve_status_connection(int connection_fd, long peer_uid, long expected_uid,
                             const std::function<std::string()>& status_json,
                             std::size_t max_request_bytes, int read_timeout_millis);

// Closes the listener and unlinks the path. Idempotent, and safe to call on a
// socket that was already closed.
void close_status_socket(int& listen_fd, const std::string& path);

// Accepts and serves at most one pending connection, waiting up to
// `timeout_millis`. Returns true if a connection was served. Never blocks
// longer than the timeout: the resident loop calls this between ticks, and a
// client that connects and says nothing must not stop the tick.
bool serve_pending_connection(int listen_fd, long expected_uid,
                              const std::function<std::string()>& status_json,
                              int timeout_millis, std::size_t max_request_bytes,
                              int read_timeout_millis);

}  // namespace nodecore::supervisor
