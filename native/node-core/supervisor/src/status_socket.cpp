#include "supervisor/status_socket.hpp"

#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstring>
#include <string>

namespace nodecore::supervisor {
namespace {

long monotonic_millis() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count();
}

std::string refusal_json(const std::string& code, const std::string& reason) {
    // Built by hand rather than through `nodecore::Json` because this is a
    // two-field object and the reader on the other end may be `nc`. It is
    // still valid JSON, and the escaping below is the whole reason it is safe
    // to interpolate the code.
    auto escape = [](const std::string& value) {
        std::string out;
        for (char c : value) {
            if (c == '"' || c == '\\') {
                out.push_back('\\');
            }
            out.push_back(c);
        }
        return out;
    };
    return "{\"error\":\"" + escape(code) + "\",\"reason\":\"" + escape(reason) + "\"}";
}

std::string trimmed(const std::string& value) {
    std::size_t begin = 0;
    std::size_t end = value.size();
    while (begin < end && (value[begin] == ' ' || value[begin] == '\t' || value[begin] == '\r' ||
                           value[begin] == '\n')) {
        ++begin;
    }
    while (end > begin &&
           (value[end - 1] == ' ' || value[end - 1] == '\t' || value[end - 1] == '\r' ||
            value[end - 1] == '\n')) {
        --end;
    }
    return value.substr(begin, end - begin);
}

// Whether the request is one this socket answers. The allowlist is literals, so
// the set of things a client can ask for is exactly this and is checkable by
// reading four lines of code.
bool recognised_request(const std::string& request) {
    if (request == "status" || request == "GET /status") {
        return true;
    }
    // `curl --unix-socket` sends a version suffix, and refusing it would only
    // teach people to write a client library for a JSON document.
    if (request.rfind("GET /status HTTP/", 0) == 0) {
        return true;
    }
    return false;
}

void write_all(int fd, const std::string& payload) {
    std::size_t written = 0;
    while (written < payload.size()) {
        const ssize_t got = ::write(fd, payload.data() + written, payload.size() - written);
        if (got > 0) {
            written += static_cast<std::size_t>(got);
            continue;
        }
        if (got < 0 && errno == EINTR) {
            continue;
        }
        // EAGAIN here means the send timeout fired and the peer is not reading.
        // Stop rather than block: a client that will not read must not hold the
        // loop, and the connection is closed by the caller either way.
        return;
    }
}

}  // namespace

SocketPath status_socket_path(const std::string& xdg_runtime_dir, long uid) {
    SocketPath out;
    if (xdg_runtime_dir.empty()) {
        out.error =
            "XDG_RUNTIME_DIR is not set, so there is nowhere private to put the status socket. "
            "There is deliberately no fallback to /tmp: a predictable path in a world-writable "
            "directory is a socket another account can create first";
        return out;
    }
    if (xdg_runtime_dir.front() != '/') {
        out.error = "XDG_RUNTIME_DIR is not an absolute path: \"" + xdg_runtime_dir + "\"";
        return out;
    }
    if (uid < 0) {
        out.error = "the uid could not be established, so the socket cannot be named for it";
        return out;
    }
    out.ok = true;
    out.path = xdg_runtime_dir + "/serveros/native-core." + std::to_string(uid) + ".sock";
    return out;
}

BindOutcome bind_user_status_socket(const std::string& path) {
    BindOutcome out;

    // Checked FIRST, before anything is created or opened. `sun_path` is 108
    // bytes on Linux and this is a real limit, not a formality: an
    // `XDG_RUNTIME_DIR` under a deep artifacts path exceeds it, and left to
    // bind() the failure would arrive as ENAMETOOLONG with a directory already
    // made and a descriptor already open.
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    if (path.size() >= sizeof(address.sun_path)) {
        out.error = "the socket path is " + std::to_string(path.size()) +
                    " bytes and the kernel's limit for a UNIX socket is " +
                    std::to_string(sizeof(address.sun_path) - 1) +
                    " bytes, so it cannot be bound at all. This is why the socket lives under "
                    "$XDG_RUNTIME_DIR (/run/user/<uid>, which is short) and not under a deeper path";
        return out;
    }

    const std::size_t slash = path.rfind('/');
    if (slash == std::string::npos || slash == 0) {
        out.error = "the socket path has no directory: \"" + path + "\"";
        return out;
    }
    const std::string directory = path.substr(0, slash);

    struct stat parent {};
    if (::stat(path.substr(0, directory.rfind('/')).c_str(), &parent) != 0 || !S_ISDIR(parent.st_mode)) {
        out.error = "the runtime directory does not exist: \"" + directory + "\"";
        return out;
    }
    // 0700. A directory another account can list is a directory in which the
    // socket can be enumerated, and one it can write is one where it can be
    // replaced.
    if (::mkdir(directory.c_str(), 0700) != 0 && errno != EEXIST) {
        out.error = "cannot create \"" + directory + "\": " + std::strerror(errno);
        return out;
    }

    // REFUSED, never unlinked. A live daemon's socket unlinked by a second
    // starter leaves the first listening where nobody can reach it; a stale one
    // is a person's decision, because a program cannot tell the two apart.
    struct stat existing {};
    if (::lstat(path.c_str(), &existing) == 0) {
        out.error = "\"" + path +
                    "\" already exists. A running Core is not restarted and a stale socket is not "
                    "removed by this program; a path that is already there is refused";
        return out;
    }

    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) {
        out.error = std::string("socket() failed: ") + std::strerror(errno);
        return out;
    }
    std::memcpy(address.sun_path, path.c_str(), path.size());

    if (::bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0) {
        ::close(fd);
        out.error = std::string("bind() failed: ") + std::strerror(errno);
        return out;
    }
    // 0600 before anything can connect: bind() creates the file with the
    // umask, so the chmod is not a tidy-up, it is the permission.
    if (::chmod(path.c_str(), 0600) != 0) {
        ::close(fd);
        ::unlink(path.c_str());
        out.error = std::string("chmod() failed: ") + std::strerror(errno);
        return out;
    }
    if (::listen(fd, 8) != 0) {
        ::close(fd);
        ::unlink(path.c_str());
        out.error = std::string("listen() failed: ") + std::strerror(errno);
        return out;
    }
    out.ok = true;
    out.fd = fd;
    return out;
}

bool peer_allowed(long peer_uid, long expected_uid) {
    // An unreadable credential is -1 and is REFUSED: "I could not establish who
    // this is" must not resolve to "let them in".
    return peer_uid >= 0 && expected_uid >= 0 && peer_uid == expected_uid;
}

void serve_status_connection(int connection_fd, long peer_uid, long expected_uid,
                             const std::function<std::string()>& status_json,
                             std::size_t max_request_bytes, int read_timeout_millis) {
    if (connection_fd < 0) {
        return;
    }
    if (!status_json) {
        return;
    }

    // A peer that never reads must not hold the loop, so the write is bounded
    // too and not only the read.
    struct timeval send_timeout {};
    send_timeout.tv_sec = 1;
    ::setsockopt(connection_fd, SOL_SOCKET, SO_SNDTIMEO, &send_timeout, sizeof(send_timeout));

    if (!peer_allowed(peer_uid, expected_uid)) {
        // Answered BEFORE the status is computed. The status is not secret from
        // the file modes alone — it is the socket's whole content and a
        // different account must not be able to read it, so the refusal happens
        // where nothing has been assembled yet.
        write_all(connection_fd, refusal_json("PEER_NOT_PERMITTED",
                                              "this socket answers only a connection from the "
                                              "account that owns the Core") +
                                     "\n");
        return;
    }

    const long deadline = monotonic_millis() + read_timeout_millis;
    std::string request;
    bool too_long = false;
    while (true) {
        if (request.size() >= max_request_bytes) {
            too_long = true;
            break;
        }
        const long remaining = deadline - monotonic_millis();
        if (remaining <= 0) {
            break;
        }
        struct pollfd entry{connection_fd, POLLIN, 0};
        const int ready = ::poll(&entry, 1, static_cast<int>(remaining));
        if (ready < 0) {
            if (errno == EINTR) {
                continue;
            }
            break;
        }
        if (ready == 0) {
            break;  // the deadline; whatever arrived so far is the request
        }
        char buffer[256];
        const ssize_t got = ::read(connection_fd, buffer, sizeof(buffer));
        if (got == 0) {
            break;  // EOF
        }
        if (got < 0) {
            if (errno == EINTR) {
                continue;
            }
            break;
        }
        request.append(buffer, static_cast<std::size_t>(got));
        if (request.find('\n') != std::string::npos) {
            break;
        }
    }

    if (too_long) {
        write_all(connection_fd,
                  refusal_json("REQUEST_TOO_LONG",
                               "a status request is one short line; this one was longer than " +
                                   std::to_string(max_request_bytes) + " bytes") +
                      "\n");
        return;
    }

    const std::string line = trimmed(request);
    if (!recognised_request(line)) {
        // The refusal names what this socket does, so a person who tried to
        // restart a service through it learns the answer rather than a silence.
        write_all(connection_fd,
                  refusal_json("UNSUPPORTED_REQUEST",
                               "this socket answers exactly one read request: \"GET /status\". It "
                               "carries out no verb and accepts no argument, so nothing was "
                               "attempted") +
                      "\n");
        return;
    }

    write_all(connection_fd, status_json() + "\n");
}

bool serve_pending_connection(int listen_fd, long expected_uid,
                              const std::function<std::string()>& status_json, int timeout_millis,
                              std::size_t max_request_bytes, int read_timeout_millis) {
    if (listen_fd < 0) {
        return false;
    }
    struct pollfd entry{listen_fd, POLLIN, 0};
    const int ready = ::poll(&entry, 1, timeout_millis);
    if (ready <= 0) {
        return false;
    }
    const int connection = ::accept4(listen_fd, nullptr, nullptr, SOCK_CLOEXEC);
    if (connection < 0) {
        return false;
    }
    struct ucred credentials {};
    socklen_t length = sizeof(credentials);
    long peer_uid = -1;
    if (::getsockopt(connection, SOL_SOCKET, SO_PEERCRED, &credentials, &length) == 0) {
        peer_uid = static_cast<long>(credentials.uid);
    }
    serve_status_connection(connection, peer_uid, expected_uid, status_json, max_request_bytes,
                            read_timeout_millis);
    ::close(connection);
    return true;
}

void close_status_socket(int& listen_fd, const std::string& path) {
    if (listen_fd >= 0) {
        ::close(listen_fd);
        listen_fd = -1;
    }
    if (!path.empty()) {
        ::unlink(path.c_str());
    }
}

}  // namespace nodecore::supervisor
