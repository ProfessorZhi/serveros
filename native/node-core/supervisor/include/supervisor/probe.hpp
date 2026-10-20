// Service-level readiness: does the SERVICE answer, or only the unit?
//
// WHY THIS EXISTS
// ---------------
// `readiness_of()` answers with systemd's own word: READY iff `ActiveState` is
// `active`. That is a statement about a UNIT, and it is not a statement about a
// service. A tunnel whose user daemon has exited while its launcher still lives,
// a listener that never bound, a connector wedged mid-handshake — each is
// `active` as far as systemd is concerned, because the process systemd started
// is still there to be active. Counting a recovery as successful on that word
// alone is how a Core reports a clean restart for a service that was never
// serving, and clears the breaker on a service that is still down.
//
// So a service may DECLARE a probe that asks the service, and this module
// answers whether that probe passed:
//
//   * `systemd-is-active` — the unit's word, unchanged, and now explicitly the
//     WEAKEST probe. It stays the default, so a manifest that does not choose
//     gets exactly today's behaviour.
//   * `unix-socket-connect` — connect(2) to a declared absolute socket path.
//   * `tcp-connect` — connect(2) to a declared LOOPBACK address and port.
//
// For a service-level probe the CONNECTION is the answer: a service that
// accepts a connection on its own endpoint is serving. There is no output to
// parse, no exit code to trust, and no program to run.
//
// WHAT IT IS NOT
// --------------
// It runs no program. The endpoint is a typed field — a path, or a loopback
// host and a port — and never an argv, so there is nothing to smuggle a command
// into. A non-loopback TCP target is REFUSED, because a Core that probes a
// remote address has become a scanner. The connect is bounded by a constant
// timeout that no manifest can raise, because a service that could set its own
// timeout could set an unbounded one and wedge the Core inside a probe.
#pragma once

#include <string>

#include "supervisor/admission.hpp"
#include "supervisor/model.hpp"

namespace nodecore::supervisor {

//: The closed probe vocabulary. A probe named and not one of these is refused
//: by `validate_probe`, never normalised into one of them.
inline constexpr const char* kProbeSystemdActive = "systemd-is-active";
inline constexpr const char* kProbeUnixConnect = "unix-socket-connect";
inline constexpr const char* kProbeTcpConnect = "tcp-connect";

//: The wall bound on one connect, in milliseconds. A CONSTANT and not a
//: manifest field: see the note above about a service setting its own bound.
inline constexpr int kProbeTimeoutMillis = 1000;

//: Where a probe points. A closed, typed target rather than a command line.
struct ProbeSpec {
    std::string kind = kProbeSystemdActive;
    //: `unix-socket-connect`: an absolute path.
    std::string path;
    //: `tcp-connect`: a loopback literal (`127.0.0.1` or `::1`).
    std::string host;
    int port = 0;
};

//: The spec a health policy declares. Gathered in one place so the reader and
//: the executor cannot disagree about which field belongs with which kind.
ProbeSpec probe_spec_of(const HealthPolicy& health);

//: Empty when this version can run the spec; otherwise the reason it refuses,
//: BY NAME. A spec that is not validated is never run — the reader and the
//: executor call this SAME function, so a manifest the reader accepted cannot
//: be one the executor would refuse.
std::string validate_probe(const ProbeSpec& spec);

//: The result of attempting a probe. `attempted = false` is "we could not
//: look", which is NOT "it did not answer": a probe that could not run produces
//: `Readiness::Unmeasured`, never `NotReady`.
struct ProbeOutcome {
    bool attempted = false;
    bool connected = false;
    // CONNECTED / REFUSED / TIMEOUT / NO_SUCH_PATH / NOT_A_SOCKET /
    // CONNECT_FAILED / SOCKET_FAILED / NOT_APPLICABLE / UNVALIDATED.
    std::string code;
    std::string reason;
};

//: The one thing a probe needs from the machine: a bounded connect. Injected so
//: a test STATES a connection instead of making one, and so that the real
//: transport is the only place in this component that opens a socket.
class ProbeTransport {
public:
    virtual ~ProbeTransport() = default;
    virtual ProbeOutcome connect_unix(const std::string& path, int timeout_ms) = 0;
    virtual ProbeOutcome connect_tcp(const std::string& host, int port, int timeout_ms) = 0;
};

//: The real transport: a non-blocking connect(2) driven by poll(2), so a
//: listener that never answers costs one bounded wait and not the Core.
class SocketProbeTransport : public ProbeTransport {
public:
    ProbeOutcome connect_unix(const std::string& path, int timeout_ms) override;
    ProbeOutcome connect_tcp(const std::string& host, int port, int timeout_ms) override;
};

//: Run `spec`. `systemd-is-active` needs no transport and reports
//: NOT_APPLICABLE; a service-level probe consults it. An unvalidated spec is
//: refused here as well as by the reader.
ProbeOutcome run_probe(const ProbeSpec& spec, ProbeTransport& transport);

//: THE READINESS RULE. Three answers, and the direction of each is the point.
Readiness readiness_from_probe(const ProbeSpec& spec, const Observed& unit_reading,
                               const ProbeOutcome& outcome);

}  // namespace nodecore::supervisor
