// The status socket: a real one, in a temporary directory, with a real client.
//
// Three of these cases are about what did NOT happen, and each is checked the
// only honest way — by looking for the effect that would exist if it had:
//
//   * a peer from another uid is refused BEFORE the status is assembled, so the
//     status lambda must not have been called at all;
//   * an unrecognised request is refused the same way, which is what makes "no
//     verb can be carried out" a property rather than a promise;
//   * an existing path is refused and NOT unlinked, so the file that was there
//     is still there afterwards.
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "check.hpp"
#include "json_detail.hpp"
#include "supervisor/status.hpp"
#include "supervisor/status_socket.hpp"

using namespace nodecore::supervisor;

namespace {

const std::string kRoot = "/tmp/serveros-stage2-socket-test";

void run_shell(const std::string& command) {
    const int status = ::system(command.c_str());
    (void)status;
}

long mode_of(const std::string& path) {
    struct stat info {};
    if (::stat(path.c_str(), &info) != 0) {
        return -1;
    }
    return static_cast<long>(info.st_mode & 0777);
}

bool exists(const std::string& path) {
    struct stat info {};
    return ::lstat(path.c_str(), &info) == 0;
}

int connect_to(const std::string& path) {
    const int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path, path.c_str(), path.size());
    if (::connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0) {
        ::close(fd);
        return -1;
    }
    return fd;
}

std::string read_all(int fd) {
    std::string out;
    char buffer[1024];
    for (int i = 0; i < 50; ++i) {
        struct pollfd entry{fd, POLLIN, 0};
        if (::poll(&entry, 1, 100) <= 0) {
            break;
        }
        const ssize_t got = ::read(fd, buffer, sizeof(buffer));
        if (got <= 0) {
            break;
        }
        out.append(buffer, static_cast<std::size_t>(got));
        if (out.find('\n') != std::string::npos) {
            break;
        }
    }
    return out;
}

// A counting status function, so "the status was not computed" is checkable.
struct Counter {
    int calls = 0;
    std::string json = "{\"schema\":\"serveros/native-core-status.v1\",\"mode\":\"observe-only\"}";
};

// A socketpair stands in for an accepted connection: same fd type, same
// read/write semantics, and the peer uid is a PARAMETER so a test can present
// an identity no unprivileged process could actually have.
int socket_pair(int fds[2]) { return ::socketpair(AF_UNIX, SOCK_STREAM, 0, fds); }

}  // namespace

int main() {
    run_shell("rm -rf " + kRoot);
    run_shell("mkdir -p " + kRoot);

    // ------------------------------------------------------ the path is derived

    {
        const SocketPath missing = status_socket_path("", 1000);
        CHECK(!missing.ok);
        CHECK_CONTAINS(missing.error, "XDG_RUNTIME_DIR is not set");

        const SocketPath relative = status_socket_path("run/user/1000", 1000);
        CHECK(!relative.ok);
        CHECK_CONTAINS(relative.error, "not an absolute path");

        const SocketPath unnamed = status_socket_path("/run/user/1000", -1);
        CHECK(!unnamed.ok);

        const SocketPath good = status_socket_path("/run/user/1000", 1000);
        CHECK(good.ok);
        CHECK_EQ(good.path, std::string("/run/user/1000/serveros/native-core.1000.sock"));
        // The uid is IN the name, so two users' Cores cannot collide even if
        // they somehow shared a runtime directory.
        const SocketPath other = status_socket_path("/run/user/1000", 1006);
        CHECK(other.ok);
        CHECK(good.path != other.path);
    }

    {
        // A path longer than the kernel's `sun_path` is REFUSED BY NAME. This
        // is a real limit — 108 bytes on Linux — and it was found by pointing
        // the binary at an `XDG_RUNTIME_DIR` under a deep artifacts path, where
        // the failure would otherwise have arrived as ENAMETOOLONG from bind()
        // with the descriptor already open.
        const std::string too_long = "/tmp/" + std::string(200, 'x') + "/native-core.1000.sock";
        const BindOutcome refused = bind_user_status_socket(too_long);
        CHECK(!refused.ok);
        CHECK_CONTAINS(refused.error, "kernel's limit");
        // Nothing was created on the way to that refusal — not the directory,
        // not the socket.
        CHECK(!exists(too_long));
        CHECK(!exists("/tmp/" + std::string(200, 'x')));
    }

    // ----------------------------------------------------------- peer identity

    CHECK(peer_allowed(1000, 1000));
    CHECK(!peer_allowed(1000, 1006));
    CHECK(!peer_allowed(1006, 1000));
    // Unreadable credentials are -1 and are REFUSED: "I could not establish who
    // this is" must never resolve to "let them in".
    CHECK(!peer_allowed(-1, 1000));
    CHECK(!peer_allowed(1000, -1));

    // ------------------------------------------- a refused peer computes nothing

    {
        Counter counter;
        int fds[2] = {-1, -1};
        CHECK_EQ(socket_pair(fds), 0);
        const int our_uid = static_cast<int>(::getuid());
        // The forged peer: a different account, presented through the parameter
        // that production fills from SO_PEERCRED.
        serve_status_connection(fds[0], our_uid + 1, our_uid,
                                [&counter] { counter.calls += 1; return counter.json; }, 512, 500);
        const std::string answer = read_all(fds[1]);
        CHECK_EQ(counter.calls, 0);  // THE POINT: nothing was assembled
        CHECK_CONTAINS(answer, "PEER_NOT_PERMITTED");
        ::close(fds[0]);
        ::close(fds[1]);
    }

    // ------------------------------------------ an illegal request computes nothing

    {
        Counter counter;
        int fds[2] = {-1, -1};
        CHECK_EQ(socket_pair(fds), 0);
        const int our_uid = static_cast<int>(::getuid());
        const std::string request = "POST /v1/restart serveros-tunnel.service\n";
        const ssize_t written = ::write(fds[1], request.data(), request.size());
        CHECK(written == static_cast<ssize_t>(request.size()));
        serve_status_connection(fds[0], our_uid, our_uid,
                                [&counter] { counter.calls += 1; return counter.json; }, 512, 500);
        const std::string answer = read_all(fds[1]);
        CHECK_EQ(counter.calls, 0);  // no verb reached anything, not even a lookup
        CHECK_CONTAINS(answer, "UNSUPPORTED_REQUEST");
        CHECK_CONTAINS(answer, "carries out no verb");
        ::close(fds[0]);
        ::close(fds[1]);
    }

    {
        // A request longer than the bound is refused rather than read forever.
        Counter counter;
        int fds[2] = {-1, -1};
        CHECK_EQ(socket_pair(fds), 0);
        const int our_uid = static_cast<int>(::getuid());
        const std::string request(4096, 'x');
        const ssize_t written = ::write(fds[1], request.data(), request.size());
        CHECK(written > 0);
        serve_status_connection(fds[0], our_uid, our_uid,
                                [&counter] { counter.calls += 1; return counter.json; }, 64, 500);
        const std::string answer = read_all(fds[1]);
        CHECK_EQ(counter.calls, 0);
        CHECK_CONTAINS(answer, "REQUEST_TOO_LONG");
        ::close(fds[0]);
        ::close(fds[1]);
    }

    // ---------------------------------------------------------- a real socket

    const std::string directory = kRoot + "/runtime";
    run_shell("mkdir -p " + directory);
    const std::string socket_path = directory + "/serveros/native-core.1000.sock";

    const int our_uid = static_cast<int>(::getuid());
    BindOutcome bound = bind_user_status_socket(socket_path);
    CHECK(bound.ok);
    if (!bound.ok) {
        std::fprintf(stderr, "bind failed: %s\n", bound.error.c_str());
    }
    // The permissions are the permission, not a tidy-up: a socket another
    // account can connect to is the whole thing this design avoids.
    CHECK_EQ(mode_of(socket_path), 0600L);
    CHECK_EQ(mode_of(directory + "/serveros"), 0700L);

    {
        // A second Core refuses rather than taking the path, and leaves the
        // first one's socket exactly where it is.
        const BindOutcome second = bind_user_status_socket(socket_path);
        CHECK(!second.ok);
        CHECK_CONTAINS(second.error, "already exists");
        CHECK(exists(socket_path));
    }

    {
        // End to end: a real client, a real accept, a real answer.
        Counter counter;
        const int client = connect_to(socket_path);
        CHECK(client >= 0);
        const std::string request = "GET /status\n";
        const ssize_t written = ::write(client, request.data(), request.size());
        CHECK(written == static_cast<ssize_t>(request.size()));

        const bool served = serve_pending_connection(
            bound.fd, our_uid, [&counter] { counter.calls += 1; return counter.json; }, 2000, 512,
            1000);
        CHECK(served);
        CHECK_EQ(counter.calls, 1);

        const std::string answer = read_all(client);
        detail::Json document;
        std::string error;
        detail::Reader reader(answer);
        CHECK(reader.parse(document, error));
        CHECK_EQ(document.find("schema") != nullptr ? document.find("schema")->text : std::string(),
                 std::string(kStatusSchema));
        ::close(client);
    }

    {
        // A bare `status` word is the other accepted spelling, and a client that
        // connects and says NOTHING must not hold the loop: the accept returns
        // and the read times out.
        Counter counter;
        const int client = connect_to(socket_path);
        CHECK(client >= 0);
        const std::string request = "status\n";
        const ssize_t written = ::write(client, request.data(), request.size());
        CHECK(written == static_cast<ssize_t>(request.size()));
        CHECK(serve_pending_connection(bound.fd, our_uid,
                                       [&counter] { counter.calls += 1; return counter.json; }, 2000,
                                       512, 1000));
        CHECK_EQ(counter.calls, 1);
        ::close(client);
    }

    // No pending connection: the accept does not block the loop.
    {
        Counter counter;
        const bool served = serve_pending_connection(
            bound.fd, our_uid, [&counter] { counter.calls += 1; return counter.json; }, 50, 512, 100);
        CHECK(!served);
        CHECK_EQ(counter.calls, 0);
    }

    // ------------------------------------------------------------- shut down

    const int listen_fd = bound.fd;
    close_status_socket(bound.fd, socket_path);
    CHECK_EQ(bound.fd, -1);
    // The path is gone and the listener is closed — that is the "no listening
    // leak": a Core that has stopped is not still reachable, and its path is
    // free for the next start rather than forcing a manual cleanup.
    CHECK(!exists(socket_path));
    CHECK(::close(listen_fd) != 0);  // already closed
    const int after = connect_to(socket_path);
    CHECK(after < 0);
    close_status_socket(bound.fd, socket_path);  // idempotent

    // The path being free means a Core can start again after a clean stop.
    BindOutcome again = bind_user_status_socket(socket_path);
    CHECK(again.ok);
    close_status_socket(again.fd, socket_path);

    {
        // An existing REGULAR FILE is refused and left alone. A program that
        // unlinked whatever it found would delete a person's file the first time
        // someone pointed it at the wrong path.
        const std::string plain = directory + "/serveros/not-a-socket.sock";
        run_shell("printf 'keep me' > " + plain);
        const long mode_before = mode_of(plain);
        CHECK(mode_before > 0);
        const BindOutcome refused = bind_user_status_socket(plain);
        CHECK(!refused.ok);
        CHECK_CONTAINS(refused.error, "already exists");
        // Still there, and untouched — this program does not chmod a file it
        // refused to use.
        CHECK(exists(plain));
        CHECK_EQ(mode_of(plain), mode_before);
    }

    run_shell("rm -rf " + kRoot);
    return check::report("test_sup_status_socket");
}
