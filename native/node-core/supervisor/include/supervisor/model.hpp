// The typed vocabulary the Native Core decides in.
//
// Two rules shape everything here, and both are copied from the Python core
// because they are the ones that keep a control plane honest:
//
//   * a state is a WORD with a reason, never a bool. `running: true` cannot
//     say "I could not look", and "I could not look" is the answer this
//     whole product exists to keep apart from "it is not running"
//     (`runtime/daemon.py`'s observe-only default and
//     `docs/NODE-OBSERVABILITY.md` §3).
//   * every vocabulary is CLOSED. A word nobody defined is refused where it
//     is read, not normalised into a member of the set. An unrecognised
//     `desired` that defaulted to `running` is how a manifest nobody wrote
//     comes to authorise a start.
#pragma once

#include <optional>
#include <optional>
#include <string>
#include <vector>

namespace nodecore::supervisor {

// ---------------------------------------------------------------- desired

// What the manifest says a service should be. These are the three words the
// contract defines and there are no others:
//
//   running   the service is meant to be up; the Core may ask systemd to
//             start it under the rules in `RecoveryPolicy`
//   manual    a PERSON owns this service's lifecycle. The Core observes it,
//             reports it, and never acts on it — a deliberate stop and a
//             crash are the same reading from outside, so the Core does not
//             get to guess
//   disabled  never started by the Core, and not expected to be up
enum class Desired { Running, Manual, Disabled };

std::string to_word(Desired value);
std::optional<Desired> desired_from_word(const std::string& word);

// --------------------------------------------------------------- observed

// What the OS service manager said. `Unknown` is the state for "this was not
// measured" and it is NOT a synonym for `Inactive`: a unit that could not be
// read must never render as a unit that is down, because that is the reading
// that starts a restart loop against something that is fine.
enum class Active { Active, Inactive, Failed, Activating, Deactivating, Unknown };

std::string to_word(Active value);
std::optional<Active> active_from_word(const std::string& word);

// Whether a measurement was taken at all. Carried beside the word rather than
// inside it, because a *measured* `Inactive` and an *unmeasured* anything are
// different answers with different repairs.
struct Observed {
    std::string unit;
    Active active = Active::Unknown;
    std::string sub_state;
    // `LoadState` from the same `systemctl show`, verbatim.
    //
    // WHY IT IS CARRIED, AND WHY IT IS NOT OPTIONAL IN PRACTICE. `ActiveState`
    // alone cannot tell "this unit is installed and stopped" from "this unit does
    // not exist": systemd synthesizes a unit for an unknown name and answers
    // `ActiveState=inactive LoadState=not-found` for it, measured on this host
    // against both a real and a fictional unit id. A reader that reports
    // `Inactive` for a unit nobody installed has stated a fact that is false —
    // the service is not stopped, it is absent — so the distinction travels with
    // the reading.
    //
    // Empty when the output carried no LoadState. That is NOT `loaded`: it means
    // the load state was not read, which is why `unit_is_installed` refuses on an
    // empty value rather than assuming.
    std::string load_state;
    // ISO-8601 with an explicit offset, the same shape the rest of the tree
    // writes (`native/node-core/contract/node-resource-snapshot.v1.md`).
    std::string observed_at;
    // How long ago that was, in seconds, or -1 when it could not be computed.
    //
    // The AGE rather than a second timestamp, because the planner is pure and
    // arithmetic: two ISO instants would make it parse dates, and a date parser
    // in a decision engine is a second clock. The provider that took the
    // reading knows what time it is; the planner only compares.
    long age_seconds = -1;
    // Who said so: "systemd-user", "fixture", … A reader has to be able to
    // tell a reading of a host from a fixture that was handed in.
    std::string source;
    // False means no measurement was taken, and `reason` says why. Every
    // field below `active` is then meaningless and must not be read.
    bool measured = false;
    std::string reason;

    static Observed unavailable(std::string unit, std::string reason);
};

// Whether the observation says the unit is INSTALLED, as opposed to merely
// inactive.
//
// Tri-state on purpose, and the third state is the point:
//   * `true`  — LoadState says the unit was found and read.
//   * `false` — LoadState says it was not found. The unit does not exist.
//   * `nullopt` — LoadState was not read, or the observation was not measured.
//     Refused rather than answered, because "we did not look" and "it is not
//     there" are different facts and only one of them is about the host.
std::optional<bool> unit_is_installed(const Observed& observed);

// --------------------------------------------------------------- recovery

// §19's budget, per service. The Python core's module constants
// (`runtime/daemon.py`: MAX_ATTEMPTS 3, WINDOW_SECONDS 3600,
// BACKOFF_SECONDS (60, 300, 900)) are the DEFAULTS a manifest inherits, not
// values it overrides — a service that declares `max_attempts: 1` and is
// given three is a service whose declaration nothing read.
struct CircuitBreaker {
    // Consecutive failures that trip the breaker. A success resets the count,
    // so this measures a service that keeps dying and not a service that has
    // died three times ever.
    int consecutive_failures = 3;
    int cooldown_seconds = 1800;
};

struct RecoveryPolicy {
    int max_attempts = 3;
    int window_seconds = 3600;
    std::vector<int> backoff_seconds{60, 300, 900};
    CircuitBreaker circuit;
    // The verbs this service permits. A verb outside this set is refused by
    // the Core even when the service needs it, because "recovery" is not a
    // licence to do anything.
    std::vector<std::string> allowed_verbs{"start", "restart"};
    // Whether this service's recovery is LOCAL — whether starting it again is a
    // local act that does not depend on the host having a route.
    //
    // WHY THIS IS PER-SERVICE AND OFF BY DEFAULT. `admission` refuses to act
    // while the link is down, because a restart spent on a network fault
    // outlives the fault. That rule is right for a service that needs the
    // network to serve. It is WRONG for a crashed local tunnel: the thing that
    // failed is the tunnel's own process, restarting it is local, and refusing
    // on the link would leave exactly the broken thing this Core exists to fix
    // unrestarted — and it would need the network, the one thing that is down,
    // to come back before the tunnel could be brought back.
    //
    // So a manifest that names a crashable LOCAL service (an approved tunnel or
    // Tailscale unit) says so here, and only that service skips the link gate.
    // It is deliberately not a global switch: a fleet node or a connector that
    // reaches the network to serve must not be restarted offline, and a written-
    // down per-service opt-in is the only way to tell the two apart.
    bool offline_recoverable = false;
};

// ---------------------------------------------------------------- service

struct HealthPolicy {
    // WHICH probe answers "is this service ready". Named rather than assumed: a
    // manifest that wants a probe this version does not implement gets a
    // refusal, not a silent fallback to the weakest one.
    //
    // `systemd-is-active` is the UNIT's word and it is the WEAKEST answer: a
    // unit is `active` while the process systemd started is still there, even
    // when that process's own work has died — a tunnel whose daemon exited but
    // whose launcher lives, a listener that never bound. The two service-level
    // probes ask the SERVICE, which is what "ready" has to mean before a
    // recovery may be counted as one.
    std::string probe = "systemd-is-active";
    // The target for a service-level probe, kept beside `probe` rather than in a
    // nested object so the manifest stays flat and the strict reader has one
    // place to check. A target given with `systemd-is-active` is refused, not
    // ignored: a field nobody reads is a rule nobody applied.
    //: `unix-socket-connect`: an absolute path the service listens on.
    std::string probe_path;
    //: `tcp-connect`: a LOOPBACK literal (`127.0.0.1` or `::1`) and a port.
    std::string probe_host;
    int probe_port = 0;
    // How old a reading may be before it stops being evidence. A stale
    // reading is NOT a failure — it is the absence of one — so it never
    // triggers a restart. Measured: `runtime/graph.py` declares 300 s for
    // host-resource evidence, and this is per-service on purpose.
    int stale_after_seconds = 120;
};

struct ServiceDesired {
    // Stable id, unique in the manifest, used by `depends_on`. Not the unit
    // name: a unit may be renamed while the service it provides is the same
    // service, and a dependency graph written against unit names would have
    // to be rewritten for that.
    std::string id;
    // The principal who owns this service. Must equal `Manifest::principal`:
    // a manifest that lists another user's unit is refused rather than
    // filtered, because a Core that quietly dropped it would leave a reader
    // believing it had been considered.
    std::string owner;
    // The exact systemd --user unit id. Not a pattern and not a path.
    std::string unit;
    Desired desired = Desired::Manual;
    // Whether a BOOT trigger may start this service. A tick never uses it:
    // boot is the only moment where "absent" is proven rather than inferred.
    bool start_on_boot = false;
    std::vector<std::string> depends_on;
    HealthPolicy health;
    RecoveryPolicy recovery;
};

struct Manifest {
    std::string schema;
    // The device this manifest is for. Carried so a manifest that landed on
    // the wrong machine is refused rather than obeyed.
    std::string node;
    // The OS user this manifest speaks for. Every service's `owner` must
    // equal it.
    std::string principal;
    // The default, and the only value that may be assumed. False must be
    // written down deliberately.
    bool observe_only = true;
    // Monotonic. The Core acts on a generation, and a manifest that is older
    // than one it has already applied is refused — a file that was restored
    // from a backup must not silently re-authorise yesterday's plan.
    long generation = 0;
    std::vector<ServiceDesired> services;
    // SHA-256 over the manifest BYTES EXACTLY AS READ — the file's own bytes,
    // not a re-serialisation of the parsed document. A reader can therefore
    // re-run `sha256sum` on the file and get this number; a digest over a
    // canonicalised form would match a file nobody has. Recorded in every
    // receipt. The Core holds NO signing key, so this is an integrity record
    // and not a signature: it says which bytes were read, never who wrote them.
    std::string sha256;
};

// A refusal is a NAME and a SENTENCE, never a bare `false`.
//
// Every path in this component that declines to do something produces one of
// these. Two reasons, and the second is the one that matters: a test can
// assert on `code` without matching prose, and a person reading a receipt can
// tell "the Core considered this and refused it" from "the Core never saw it".
// A control plane whose refusals are indistinguishable from its omissions is
// one nobody can audit.
struct Refusal {
    // The service id this is about, or empty when it is about the document.
    std::string scope;
    // A stable token: SCHEMA, UNIT_ID, CYCLE, UNKNOWN_IS_NOT_STOPPED, …
    std::string code;
    std::string reason;
};

}  // namespace nodecore::supervisor
