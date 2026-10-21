// Service-level readiness: the validation matrix, the readiness rule, a FAKE
// transport for the outcomes a real host will not produce on demand, and a REAL
// loopback and unix socket for the ones it will.
//
// The split is the point. A listener that answers, one that is bound but not
// listening, a path that is not there and a closed port are all real here — a
// real `connect(2)` against a real socket. What cannot be produced on demand
// (a connect that times out) is stated through the injected transport, which is
// why the transport is an interface at all.
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "check.hpp"
#include "supervisor/probe.hpp"

using namespace nodecore::supervisor;

namespace {

//: A transport that answers from a table instead of the machine. Every case
//: that needs a specific outcome without waiting for it uses this.
class FakeTransport : public ProbeTransport {
public:
    ProbeOutcome unix_answer;
    ProbeOutcome tcp_answer;
    int unix_calls = 0;
    int tcp_calls = 0;

    ProbeOutcome connect_unix(const std::string&, int) override {
        unix_calls++;
        return unix_answer;
    }
    ProbeOutcome connect_tcp(const std::string&, int, int) override {
        tcp_calls++;
        return tcp_answer;
    }
};

ProbeOutcome connected() {
    ProbeOutcome out;
    out.attempted = true;
    out.connected = true;
    out.code = "CONNECTED";
    return out;
}

ProbeOutcome refused() {
    ProbeOutcome out;
    out.attempted = true;
    out.connected = false;
    out.code = "REFUSED";
    out.reason = "Connection refused";
    return out;
}

ProbeOutcome could_not_look() {
    ProbeOutcome out;
    out.attempted = false;
    out.code = "UNVALIDATED";
    out.reason = "no transport";
    return out;
}

Observed unit(const Active active) {
    Observed row;
    row.unit = "toy.service";
    row.measured = true;
    row.active = active;
    return row;
}

// A unix socket bound and listening. Returns the fd, or -1.
int make_unix_listener(const std::string& path) {
    const int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    std::memcpy(addr.sun_path, path.c_str(), path.size());
    if (::bind(fd, reinterpret_cast<const sockaddr*>(&addr),
               static_cast<socklen_t>(sizeof(addr.sun_family) + path.size() + 1)) != 0) {
        ::close(fd);
        return -1;
    }
    if (::listen(fd, 1) != 0) {
        ::close(fd);
        ::unlink(path.c_str());
        return -1;
    }
    return fd;
}

// A loopback TCP listener on an ephemeral port.
int make_tcp_listener(int& port_out) {
    const int fd = ::socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(0x7f000001u);
    addr.sin_port = 0;
    if (::bind(fd, reinterpret_cast<const sockaddr*>(&addr), sizeof(addr)) != 0) {
        ::close(fd);
        return -1;
    }
    socklen_t len = sizeof(addr);
    if (::getsockname(fd, reinterpret_cast<sockaddr*>(&addr), &len) != 0) {
        ::close(fd);
        return -1;
    }
    port_out = ntohs(addr.sin_port);
    if (::listen(fd, 1) != 0) {
        ::close(fd);
        return -1;
    }
    return fd;
}

}  // namespace

int main() {
    // ------------------------------------------------------------- validation

    {
        // The default probe is the unit's word and needs no target.
        ProbeSpec spec;
        CHECK_EQ(validate_probe(spec), std::string(""));
        CHECK_EQ(probe_spec_of(HealthPolicy{}).kind, std::string("systemd-is-active"));
    }
    {
        // A target beside `systemd-is-active` is refused rather than ignored:
        // a field nobody reads is a rule nobody applied, and an author who meant
        // a service-level probe and mistyped the kind must be told.
        ProbeSpec spec;
        spec.path = "/run/x.sock";
        CHECK(validate_probe(spec) != std::string(""));
    }
    {
        ProbeSpec spec;
        spec.kind = kProbeUnixConnect;
        spec.path = "/run/user/1000/toy.sock";
        CHECK_EQ(validate_probe(spec), std::string(""));
    }
    {
        // A relative path would resolve against the Core's own working
        // directory, which is not a fact about the service.
        ProbeSpec spec;
        spec.kind = kProbeUnixConnect;
        spec.path = "run/toy.sock";
        CHECK_CONTAINS(validate_probe(spec), "not absolute");
    }
    {
        ProbeSpec spec;
        spec.kind = kProbeUnixConnect;
        CHECK_CONTAINS(validate_probe(spec), "needs a \"probe_path\"");
    }
    {
        // `sockaddr_un.sun_path` is 108 bytes with its terminator, so 107 is the
        // longest usable path. A longer one could never be a socket this host
        // can bind, and it is refused here rather than at the service's `bind`.
        ProbeSpec spec;
        spec.kind = kProbeUnixConnect;
        spec.path = "/" + std::string(107, 'a');
        CHECK_CONTAINS(validate_probe(spec), "at most 107");
    }
    {
        ProbeSpec spec;
        spec.kind = kProbeTcpConnect;
        spec.host = "127.0.0.1";
        spec.port = 9;
        CHECK_EQ(validate_probe(spec), std::string(""));
    }
    {
        // Not `localhost` and not a remote address. A name would be resolved by
        // whatever resolver is configured, and a Core that will connect anywhere
        // is a scanner.
        ProbeSpec spec;
        spec.kind = kProbeTcpConnect;
        spec.host = "localhost";
        spec.port = 9;
        CHECK_CONTAINS(validate_probe(spec), "not a loopback address");
    }
    {
        ProbeSpec spec;
        spec.kind = kProbeTcpConnect;
        spec.host = "10.0.0.1";
        spec.port = 9;
        CHECK(validate_probe(spec) != std::string(""));
    }
    {
        ProbeSpec spec;
        spec.kind = kProbeTcpConnect;
        spec.host = "::1";
        CHECK_CONTAINS(validate_probe(spec), "probe_port");
    }
    {
        // A word nobody defined is refused, and the message names the three that
        // exist so an author is not left guessing.
        ProbeSpec spec;
        spec.kind = "http-get";
        CHECK_CONTAINS(validate_probe(spec), "not a probe this version implements");
    }

    // --------------------------------------------------------- the readiness rule

    {
        // THE CASE THE MODULE EXISTS FOR. systemd says the unit is active, and
        // `readiness_of` alone would call that READY — but the service's own
        // endpoint refuses, so the SERVICE is not serving. The two answers are
        // asserted side by side so the difference is the tested thing.
        const Observed row = unit(Active::Active);
        CHECK_EQ(to_word(readiness_of(row)), std::string("READY"));

        ProbeSpec spec;
        spec.kind = kProbeUnixConnect;
        spec.path = "/run/user/1000/toy.sock";
        CHECK_EQ(to_word(readiness_from_probe(spec, row, refused())), std::string("NOT_READY"));
        CHECK_EQ(to_word(readiness_from_probe(spec, row, connected())), std::string("READY"));
    }
    {
        // A unit that CANNOT be running is not ready even when something
        // answers: a stale listener can outlive the service it belonged to.
        ProbeSpec spec;
        spec.kind = kProbeUnixConnect;
        spec.path = "/run/user/1000/toy.sock";
        CHECK_EQ(to_word(readiness_from_probe(spec, unit(Active::Failed), connected())),
                 std::string("NOT_READY"));
        CHECK_EQ(to_word(readiness_from_probe(spec, unit(Active::Inactive), connected())),
                 std::string("NOT_READY"));
        CHECK_EQ(to_word(readiness_from_probe(spec, unit(Active::Deactivating), connected())),
                 std::string("NOT_READY"));
    }
    {
        // A unit between "we started something" and "it is serving" is not
        // ready by its word alone, but a service-level probe is the authority:
        // if the endpoint genuinely answers, the service is serving.
        const Observed activating = unit(Active::Activating);
        const ProbeSpec default_probe;
        CHECK_EQ(to_word(readiness_from_probe(default_probe, activating, could_not_look())),
                 std::string("NOT_READY"));

        ProbeSpec spec;
        spec.kind = kProbeTcpConnect;
        spec.host = "127.0.0.1";
        spec.port = 1;
        CHECK_EQ(to_word(readiness_from_probe(spec, activating, connected())), std::string("READY"));
    }
    {
        // An unmeasured probe is NOT a verdict. "We could not look" is neither
        // ready nor not ready, and must not spend or clear a budget.
        ProbeSpec spec;
        spec.kind = kProbeUnixConnect;
        spec.path = "/run/user/1000/toy.sock";
        CHECK_EQ(to_word(readiness_from_probe(spec, unit(Active::Active), could_not_look())),
                 std::string("UNMEASURED"));
    }
    {
        // The default probe is bit-for-bit the old path: the unit's own word.
        const ProbeSpec spec;
        const Observed active = unit(Active::Active);
        CHECK_EQ(to_word(readiness_from_probe(spec, active, connected())),
                 to_word(readiness_of(active)));
        const Observed failed = unit(Active::Failed);
        CHECK_EQ(to_word(readiness_from_probe(spec, failed, connected())),
                 to_word(readiness_of(failed)));
    }

    // ------------------------------------------------- run_probe with a fake host

    {
        FakeTransport transport;
        ProbeOutcome answer = connected();
        transport.unix_answer = answer;

        ProbeSpec spec;
        spec.kind = kProbeUnixConnect;
        spec.path = "/run/user/1000/toy.sock";
        const ProbeOutcome outcome = run_probe(spec, transport);
        CHECK(outcome.attempted);
        CHECK(outcome.connected);
        CHECK_EQ(transport.unix_calls, 1);
        CHECK_EQ(transport.tcp_calls, 0);
    }
    {
        // The unit's probe connects to nothing, so the transport is never
        // consulted — asserted, because a probe that quietly opened a socket it
        // should not have is the failure this shape prevents.
        FakeTransport transport;
        transport.unix_answer = connected();
        transport.tcp_answer = connected();
        const ProbeOutcome outcome = run_probe(ProbeSpec{}, transport);
        CHECK(outcome.attempted);
        CHECK_EQ(outcome.code, std::string("NOT_APPLICABLE"));
        CHECK_EQ(transport.unix_calls, 0);
        CHECK_EQ(transport.tcp_calls, 0);
    }
    {
        // An unvalidated spec is refused by `run_probe` too, not only by the
        // reader: the executor does not trust its caller to have validated.
        FakeTransport transport;
        ProbeSpec spec;
        spec.kind = kProbeUnixConnect;  // no path
        const ProbeOutcome outcome = run_probe(spec, transport);
        CHECK(!outcome.attempted);
        CHECK_EQ(outcome.code, std::string("UNVALIDATED"));
        CHECK_EQ(transport.unix_calls, 0);
    }

    // --------------------------------------------------------- the real machine

    {
        char directory[] = "/tmp/nodecore-probe-XXXXXX";
        char* made = ::mkdtemp(directory);
        CHECK(made != nullptr);
        if (made != nullptr) {
            const std::string base(made);
            const std::string live = base + "/live.sock";

            // A real listener, a real connect.
            const int listener = make_unix_listener(live);
            CHECK(listener >= 0);

            SocketProbeTransport transport;
            ProbeSpec spec;
            spec.kind = kProbeUnixConnect;
            spec.path = live;
            const ProbeOutcome answered = run_probe(spec, transport);
            CHECK(answered.attempted);
            CHECK(answered.connected);
            CHECK_EQ(answered.code, std::string("CONNECTED"));
            CHECK_EQ(to_word(readiness_from_probe(spec, unit(Active::Active), answered)),
                     std::string("READY"));

            // A path that is not there: "the service is absent", which is a real
            // answer and not a failure to look.
            ProbeSpec missing;
            missing.kind = kProbeUnixConnect;
            missing.path = base + "/absent.sock";
            const ProbeOutcome absent = run_probe(missing, transport);
            CHECK(absent.attempted);
            CHECK(!absent.connected);
            CHECK_EQ(absent.code, std::string("NO_SUCH_PATH"));
            CHECK_EQ(to_word(readiness_from_probe(missing, unit(Active::Active), absent)),
                     std::string("NOT_READY"));

            // A bound socket nobody is listening on: the connection is refused.
            const std::string idle = base + "/idle.sock";
            const int bound = ::socket(AF_UNIX, SOCK_STREAM, 0);
            CHECK(bound >= 0);
            if (bound >= 0) {
                sockaddr_un addr{};
                addr.sun_family = AF_UNIX;
                std::memcpy(addr.sun_path, idle.c_str(), idle.size());
                CHECK(::bind(bound, reinterpret_cast<const sockaddr*>(&addr),
                             static_cast<socklen_t>(sizeof(addr.sun_family) + idle.size() + 1)) == 0);
                ProbeSpec idle_spec;
                idle_spec.kind = kProbeUnixConnect;
                idle_spec.path = idle;
                const ProbeOutcome idle_outcome = run_probe(idle_spec, transport);
                CHECK(idle_outcome.attempted);
                CHECK(!idle_outcome.connected);
                ::close(bound);
            }

            // A path that is a plain file, not a socket: refused, and reported
            // as a failure to serve rather than a failure to look.
            const std::string plain = base + "/plain.txt";
            const int plain_fd = ::open(plain.c_str(), O_CREAT | O_WRONLY, 0600);
            if (plain_fd >= 0) {
                ::close(plain_fd);
            }
            ProbeSpec file_spec;
            file_spec.kind = kProbeUnixConnect;
            file_spec.path = plain;
            const ProbeOutcome file_outcome = run_probe(file_spec, transport);
            CHECK(file_outcome.attempted);
            CHECK(!file_outcome.connected);

            if (listener >= 0) {
                ::close(listener);
            }
            ::unlink(live.c_str());
            ::unlink(idle.c_str());
            ::unlink(plain.c_str());
            ::rmdir(base.c_str());
        }
    }
    {
        // A real loopback TCP listener, a real connect, and a closed port for
        // the refusal. All on 127.0.0.1 — the only address `validate_probe`
        // will admit.
        int port = 0;
        const int listener = make_tcp_listener(port);
        CHECK(listener >= 0);
        CHECK(port > 0);

        SocketProbeTransport transport;
        ProbeSpec spec;
        spec.kind = kProbeTcpConnect;
        spec.host = "127.0.0.1";
        spec.port = port;
        const ProbeOutcome answered = run_probe(spec, transport);
        CHECK(answered.attempted);
        CHECK(answered.connected);
        CHECK_EQ(answered.code, std::string("CONNECTED"));

        if (listener >= 0) {
            ::close(listener);
        }
        // The port is free again, so the same probe is now refused.
        const ProbeOutcome closed = run_probe(spec, transport);
        CHECK(closed.attempted);
        CHECK(!closed.connected);
    }

    return check::report("test_sup_probe");
}
