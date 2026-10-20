#include "supervisor/probe.hpp"

#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/types.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <string>

namespace nodecore::supervisor {
namespace {

//: `sockaddr_un.sun_path` holds 108 bytes including the terminating NUL, so the
//: longest usable path is 107. A longer path is refused HERE, by name and before
//: anything is opened, rather than arriving as `ENAMETOOLONG` from `bind()` on
//: the service's side — the same refusal-before-opening rule `status_socket.hpp`
//: applies to the Core's own socket.
constexpr std::size_t kMaxUnixPath = 107;

std::string strerror_text(int err) {
    return std::string(std::strerror(err));
}

//: One bounded connect over an already-built `sockaddr`. Non-blocking, so a
//: listener that accepts the connection but never completes the handshake cannot
//: hold the Core; `poll` supplies the wall bound; and the descriptor is closed on
//: every path out.
ProbeOutcome connect_bounded(int fd, const sockaddr* addr, socklen_t addrlen, int timeout_ms) {
    ProbeOutcome out;
    out.attempted = true;

    const int rc = ::connect(fd, addr, addrlen);
    if (rc == 0) {
        out.connected = true;
        out.code = "CONNECTED";
        return out;
    }
    if (errno != EINPROGRESS) {
        const int err = errno;
        out.code = err == ENOENT     ? "NO_SUCH_PATH"
                   : err == ECONNREFUSED ? "REFUSED"
                   : err == ENOTSOCK     ? "NOT_A_SOCKET"
                   : err == EACCES       ? "REFUSED"
                                         : "CONNECT_FAILED";
        out.reason = strerror_text(err);
        return out;
    }

    pollfd waiting{};
    waiting.fd = fd;
    waiting.events = POLLOUT;
    const int polled = ::poll(&waiting, 1, timeout_ms);
    if (polled == 0) {
        out.code = "TIMEOUT";
        out.reason = "no answer within " + std::to_string(timeout_ms) + " ms";
        return out;
    }
    if (polled < 0) {
        const int err = errno;
        out.code = "CONNECT_FAILED";
        out.reason = "poll: " + strerror_text(err);
        return out;
    }

    // A writable descriptor after a non-blocking connect means the attempt
    // finished — successfully or not. `SO_ERROR` is the only place the answer
    // is, and it must be read (that read is also what clears it).
    int err = 0;
    socklen_t len = sizeof(err);
    if (::getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len) != 0) {
        const int gerr = errno;
        out.code = "CONNECT_FAILED";
        out.reason = "getsockopt: " + strerror_text(gerr);
        return out;
    }
    if (err == 0) {
        out.connected = true;
        out.code = "CONNECTED";
        return out;
    }
    out.code = err == ECONNREFUSED ? "REFUSED" : "CONNECT_FAILED";
    out.reason = strerror_text(err);
    return out;
}

}  // namespace

ProbeSpec probe_spec_of(const HealthPolicy& health) {
    ProbeSpec spec;
    spec.kind = health.probe;
    spec.path = health.probe_path;
    spec.host = health.probe_host;
    spec.port = health.probe_port;
    return spec;
}

std::string validate_probe(const ProbeSpec& spec) {
    if (spec.kind == kProbeSystemdActive) {
        if (!spec.path.empty() || !spec.host.empty() || spec.port != 0) {
            return "probe \"systemd-is-active\" takes no target, but a path, a host or a port was "
                   "given beside it; a target with no probe is a field nobody reviewed";
        }
        return "";
    }
    if (spec.kind == kProbeUnixConnect) {
        if (!spec.host.empty() || spec.port != 0) {
            return "probe \"unix-socket-connect\" takes a \"probe_path\" only, not a host or a port";
        }
        if (spec.path.empty()) {
            return "probe \"unix-socket-connect\" needs a \"probe_path\"";
        }
        if (spec.path[0] != '/') {
            return "probe_path \"" + spec.path +
                   "\" is not absolute; a relative path would resolve against whatever directory the "
                   "Core happened to be started in";
        }
        if (spec.path.size() > kMaxUnixPath) {
            return "probe_path is " + std::to_string(spec.path.size()) + " bytes; a unix socket path " +
                   "is at most " + std::to_string(kMaxUnixPath) +
                   " (sockaddr_un.sun_path is 108 with its terminator), so this could never be a "
                   "socket this host can bind";
        }
        return "";
    }
    if (spec.kind == kProbeTcpConnect) {
        if (!spec.path.empty()) {
            return "probe \"tcp-connect\" takes a \"probe_host\" and a \"probe_port\", not a path";
        }
        if (spec.host.empty()) {
            return "probe \"tcp-connect\" needs a \"probe_host\"";
        }
        // Loopback LITERALS only. Not `localhost` (a name resolved by whatever
        // resolver is configured), and not a remote address: a Core that will
        // connect anywhere is a scanner.
        if (spec.host != "127.0.0.1" && spec.host != "::1") {
            return "\"" + spec.host +
                   "\" is not a loopback address; this Core probes the service on THIS machine. Only "
                   "the literals \"127.0.0.1\" and \"::1\" are accepted";
        }
        if (spec.port < 1 || spec.port > 65535) {
            return "probe_port must be between 1 and 65535, got " + std::to_string(spec.port);
        }
        return "";
    }
    return "\"" + spec.kind +
           "\" is not a probe this version implements; the probes it knows are \"systemd-is-active\", "
           "\"unix-socket-connect\" and \"tcp-connect\"";
}

ProbeOutcome SocketProbeTransport::connect_unix(const std::string& path, int timeout_ms) {
    ProbeOutcome out;
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (fd < 0) {
        out.code = "SOCKET_FAILED";
        out.reason = strerror_text(errno);
        return out;
    }
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    std::memcpy(addr.sun_path, path.c_str(), path.size());
    const ProbeOutcome result = connect_bounded(
        fd, reinterpret_cast<const sockaddr*>(&addr),
        static_cast<socklen_t>(sizeof(addr.sun_family) + path.size() + 1), timeout_ms);
    ::close(fd);
    return result;
}

ProbeOutcome SocketProbeTransport::connect_tcp(const std::string& host, int port, int timeout_ms) {
    ProbeOutcome out;
    if (host == "127.0.0.1") {
        const int fd = ::socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
        if (fd < 0) {
            out.code = "SOCKET_FAILED";
            out.reason = strerror_text(errno);
            return out;
        }
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(static_cast<uint16_t>(port));
        addr.sin_addr.s_addr = htonl(0x7f000001u);
        const ProbeOutcome result =
            connect_bounded(fd, reinterpret_cast<const sockaddr*>(&addr),
                            static_cast<socklen_t>(sizeof(addr)), timeout_ms);
        ::close(fd);
        return result;
    }
    // `validate_probe` admits only the two loopback literals, so reaching here
    // means "::1".
    const int fd = ::socket(AF_INET6, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (fd < 0) {
        out.code = "SOCKET_FAILED";
        out.reason = strerror_text(errno);
        return out;
    }
    sockaddr_in6 addr{};
    addr.sin6_family = AF_INET6;
    addr.sin6_port = htons(static_cast<uint16_t>(port));
    addr.sin6_addr = in6addr_loopback;
    const ProbeOutcome result = connect_bounded(fd, reinterpret_cast<const sockaddr*>(&addr),
                                                static_cast<socklen_t>(sizeof(addr)), timeout_ms);
    ::close(fd);
    return result;
}

ProbeOutcome run_probe(const ProbeSpec& spec, ProbeTransport& transport) {
    const std::string invalid = validate_probe(spec);
    if (!invalid.empty()) {
        ProbeOutcome refusal;
        refusal.attempted = false;
        refusal.code = "UNVALIDATED";
        refusal.reason = invalid;
        return refusal;
    }
    if (spec.kind == kProbeSystemdActive) {
        // There is no endpoint to reach. The unit's own word is the whole
        // reading and it was taken elsewhere, so this is not a failed connect —
        // it is a probe with nothing to connect to. Reported as `attempted` so
        // readiness is decided by the unit word rather than by an absent probe.
        ProbeOutcome done;
        done.attempted = true;
        done.connected = false;
        done.code = "NOT_APPLICABLE";
        done.reason = "this probe is the unit's own word; it connects to nothing";
        return done;
    }
    if (spec.kind == kProbeUnixConnect) {
        return transport.connect_unix(spec.path, kProbeTimeoutMillis);
    }
    return transport.connect_tcp(spec.host, spec.port, kProbeTimeoutMillis);
}

Readiness readiness_from_probe(const ProbeSpec& spec, const Observed& unit_reading,
                               const ProbeOutcome& outcome) {
    // A unit that CANNOT be running is not ready, whatever an endpoint answers.
    // A stale listener can outlive the service it belonged to, so a successful
    // connect is not allowed to overrule systemd saying the unit is gone. This
    // is the one direction in which the unit word outranks the probe.
    if (unit_reading.measured) {
        if (unit_reading.active == Active::Failed || unit_reading.active == Active::Inactive ||
            unit_reading.active == Active::Deactivating) {
            return Readiness::NotReady;
        }
    }

    if (spec.kind == kProbeSystemdActive) {
        // The unit's own word. Deliberately routed through `readiness_of`, so
        // the default path is bit-for-bit the path that existed before any
        // service-level probe did.
        return readiness_of(unit_reading);
    }

    // A service-level probe is the authority: the connection IS the answer.
    if (!outcome.attempted) {
        // We could not look. Not a verdict — never read as ready and never read
        // as not ready. An unmeasured probe must not clear or spend a budget.
        return Readiness::Unmeasured;
    }
    return outcome.connected ? Readiness::Ready : Readiness::NotReady;
}

}  // namespace nodecore::supervisor
