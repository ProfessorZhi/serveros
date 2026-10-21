// serveros-native-core — one pass, or the resident loop.
//
// TWO MODES, ONE DECISION ENGINE
// ------------------------------
// Without `--serve` it runs once, prints a receipt and exits. With `--serve` it
// is the resident Core: it ticks on a monotonic schedule, answers a read-only
// status socket between ticks, and exits cleanly on SIGTERM/SIGINT.
//
// WHAT IT STILL DOES NOT DO
// -------------------------
// It does not daemonize (systemd is the supervisor), does not restart itself
// (an exception inside a tick is caught and counted; if the process ends,
// `Restart=on-failure` decides), and does not fork.
//
// It starts OBSERVE ONLY, in BOTH modes, and `--enable-actions` is refused
// together with `--serve`: this stage ships a resident Core that WATCHES. The
// decision engine can already produce the verbs and the planner refuses them
// per-unit anyway, but a resident process that can act on a fleet is not
// something to have on by accident while the production Python `serverosd` is
// still the only writer on this node.
//
// A resident pass writes NOTHING but its status socket: no state file, no
// ledger, no directory of its own beyond the socket's parent. That is the
// property a run beside `serverosd` needs — the Python daemon owns the health
// ledger, and a second writer would be a second authority over one account.
#include <signal.h>
#include <time.h>
#include <unistd.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <functional>
#include <sstream>
#include <string>
#include <vector>

#include "supervisor/action_gate.hpp"
#include "supervisor/action_journal.hpp"
#include "supervisor/admission.hpp"
#include "supervisor/facts.hpp"
#include "supervisor/state_directory.hpp"
#include "supervisor/lease.hpp"
#include "supervisor/manifest.hpp"
#include "supervisor/plan.hpp"
#include "supervisor/probe.hpp"
#include "supervisor/receipt.hpp"
#include "supervisor/recovery.hpp"
#include "supervisor/serve.hpp"
#include "supervisor/status.hpp"
#include "supervisor/status_socket.hpp"
#include "supervisor/systemd_user.hpp"

namespace {

constexpr const char* kVersion = "0.2.0";
constexpr const char* kDefaultSystemctl = "/usr/bin/systemctl";

// Set from a signal handler, which may do nothing else. `volatile sig_atomic_t`
// is the only type a handler is allowed to write.
volatile sig_atomic_t g_stop_requested = 0;

void on_stop_signal(int) { g_stop_requested = 1; }

void print_usage(std::FILE* out) {
    std::fprintf(out,
                 "serveros-native-core %s\n"
                 "\n"
                 "One pass, or with --serve the resident loop. Reads a desired-state\n"
                 "manifest, asks systemd --user what is running, decides, and records.\n"
                 "OBSERVE ONLY in both modes.\n"
                 "\n"
                 "Usage:\n"
                 "  serveros-native-core --manifest FILE [options]\n"
                 "  serveros-native-core --manifest FILE --serve [options]\n"
                 "\n"
                 "Options:\n"
                 "  --manifest FILE        the desired-state manifest to read (required).\n"
                 "  --allow-unit UNIT      a unit this run may name (repeatable). No unit\n"
                 "                         is permitted unless it is listed here.\n"
                 "  --trigger boot|tick    boot proves absence and uses start_on_boot;\n"
                 "                         tick restarts only a failed unit (default tick).\n"
                 "                         Ignored by --serve, which is always a tick.\n"
                 "  --state FILE           persisted recovery budget. REQUIRED with\n"
                 "                         --enable-actions, where it is read and written;\n"
                 "                         observe-only never uses it.\n"
                 "  --systemctl PATH       the systemctl to run (default %s).\n"
                 "  --serve                run the resident loop: monotonic ticks, a status\n"
                 "                         socket, and a clean exit on SIGTERM.\n"
                 "  --interval-seconds N   seconds between ticks in --serve (default 60).\n"
                 "  --status-socket PATH   where the read-only status socket goes. The\n"
                 "                         default is $XDG_RUNTIME_DIR/serveros/\n"
                 "                         native-core.<uid>.sock, and there is no fallback.\n"
                 "  --no-status-socket     run with no status surface at all. Explicit,\n"
                 "                         because a resident Core nobody can ask is a\n"
                 "                         different thing from this one.\n"
                 "  --max-ticks N          stop after N ticks. For tests; a unit must not\n"
                 "                         pass it.\n"
                 "  --enable-actions       ACT. REQUIRES --lease, --link-state and\n"
                 "                         --agent-state. With --serve it additionally REQUIRES\n"
                 "                         --facts, because a resident loop cannot act on a fact\n"
                 "                         stated once at startup.\n"
                 "  --facts FILE           per-tick facts document (serveros/native-core-facts.v1),\n"
                 "                         re-read on every tick of --serve --enable-actions.\n"
                 "                         Absent, stale, malformed or unreadable leaves link and\n"
                 "                         agent unknown, which refuses rather than assumes.\n"
                 "  --facts-max-age-seconds N  how old the facts document may be (default 90).\n"
                 "  --owner-map FILE       the shared owner map (serveros/native-core-owner-map.v1).\n"
                 "                         REQUIRED with --serve --enable-actions: a manifest unit\n"
                 "                         the map assigns to another program is refused.\n"
                 "  --lease FILE           single-writer lease. Taken with flock, never\n"
                 "                         waited for, and released when this process exits.\n"
                 "  --link-state up|down|unknown\n"
                 "                         whether this host has a working link. Default\n"
                 "                         unknown, which REFUSES to act.\n"
                 "  --agent-state idle|busy|unknown\n"
                 "                         whether an agent is using the service now. Default\n"
                 "                         unknown, which REFUSES to act.\n"
                 "  --service-runtime serving|needs-login|unknown\n"
                 "                         whether the SERVICE's own runtime says it needs a\n"
                 "                         person (a tunnel that is up but NeedsLogin). Default\n"
                 "                         unknown; unlike the other facts, an unknown runtime is\n"
                 "                         NOT a refusal — it is the absence of an optional fact.\n"
                 "  --receipt FILE         write the receipt here instead of stdout.\n"
                 "  --version              print the version and exit.\n"
                 "  --help                 print this text and exit.\n",
                 kVersion, kDefaultSystemctl);
}

std::string iso_now() {
    const auto now = std::chrono::system_clock::now();
    const std::time_t seconds = std::chrono::system_clock::to_time_t(now);
    std::tm utc{};
    gmtime_r(&seconds, &utc);
    char buffer[32];
    std::strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%S", &utc);
    return std::string(buffer) + "+00:00";
}

long monotonic_seconds() {
    using namespace std::chrono;
    return duration_cast<seconds>(steady_clock::now().time_since_epoch()).count();
}

//: The wall clock at the same instant as `monotonic_seconds()`. The ONLY
//: cross-boot bridge the persisted budget has; it is never used as a duration.
long long wall_epoch_seconds() {
    using namespace std::chrono;
    return duration_cast<seconds>(system_clock::now().time_since_epoch()).count();
}

//: The kernel's boot id, which says which boot owns the monotonic clock. It is
//: kernel-provided and read-only; an unreadable id is a refusal, never a guess.
bool read_boot_id(std::string& out) {
    std::ifstream file("/proc/sys/kernel/random/boot_id");
    if (!file.good()) {
        return false;
    }
    std::string line;
    if (!std::getline(file, line)) {
        return false;
    }
    while (!line.empty() &&
           (line.back() == '\r' || line.back() == '\n' || line.back() == ' ' ||
            line.back() == '\t')) {
        line.pop_back();
    }
    if (line.empty() || line.size() > 128) {
        return false;
    }
    out = line;
    return true;
}

bool read_file(const std::string& path, std::string& out) {
    std::ifstream stream(path);
    if (!stream.good()) {
        return false;
    }
    std::ostringstream buffer;
    buffer << stream.rdbuf();
    out = buffer.str();
    return true;
}

void sleep_millis_real(long millis) {
    if (millis > 0) {
        ::usleep(static_cast<useconds_t>(millis) * 1000);
    }
}

// A status notice for one service, from the observation the tick just took.
// An unmeasured unit keeps `measured: false` and its reason and NO age.
nodecore::supervisor::StatusNotice notice_for(const nodecore::supervisor::ServiceDesired& service,
                                              const nodecore::supervisor::Observed& observed) {
    nodecore::supervisor::StatusNotice notice;
    notice.id = service.id;
    notice.unit = service.unit;
    notice.desired = nodecore::supervisor::to_word(service.desired);
    notice.measured = observed.measured;
    notice.active = nodecore::supervisor::to_word(observed.active);
    notice.sub_state = observed.sub_state;
    notice.observed_at = observed.observed_at;
    notice.age_seconds = observed.age_seconds;
    notice.reason = observed.reason;
    return notice;
}

}  // namespace

int main(int argc, char** argv) {
    std::string manifest_path;
    std::string state_path;
    std::string receipt_path;
    std::string systemctl_path = kDefaultSystemctl;
    std::string status_socket_override;
    std::vector<std::string> allowed_units;
    std::string lease_path;
    // The facts the planner cannot see, and the DEFAULT IS "NOT ESTABLISHED".
    // Assuming a link is up is how a restart gets spent on a network fault, and
    // assuming nobody is using a service is how an agent's work is interrupted;
    // both are permissive-by-omission, so neither is the default here. A caller
    // that wants to act states them.
    nodecore::supervisor::LinkFacts link;
    nodecore::supervisor::AgentFacts agent;
    // The service's own runtime, when the caller has read one. Its default,
    // `known = false`, is NOT a refusal — see `ServiceRuntime` for why the one
    // optional fact is the one whose absence is safe.
    nodecore::supervisor::ServiceRuntime service_runtime;
    //: The resident acting Core's facts document. In `--serve --enable-actions`
    //: the facts above are NOT used: they state the instant the process started,
    //: and a loop that reuses them is a loop that hard-codes LINK_UP/AGENT_IDLE
    //: for as long as it runs. This file is re-read on every tick instead.
    std::string facts_path;
    std::string owner_map_path;
    long facts_max_age_seconds = 90;
    bool act = false;
    bool serve = false;
    bool no_status_socket = false;
    long interval_seconds = 60;
    long max_ticks = -1;
    nodecore::supervisor::Trigger trigger = nodecore::supervisor::Trigger::Tick;

    for (int i = 1; i < argc; ++i) {
        const std::string flag = argv[i];
        const bool has_value = i + 1 < argc;
        if (flag == "--help" || flag == "-h") {
            print_usage(stdout);
            return 0;
        }
        if (flag == "--version") {
            std::printf("serveros-native-core %s\n", kVersion);
            return 0;
        }
        if (flag == "--lease") {
            if (!has_value) {
                std::fprintf(stderr, "--lease needs a value\n");
                return 2;
            }
            lease_path = argv[++i];
            continue;
        }
        if (flag == "--link-state") {
            const std::string value = has_value ? argv[++i] : std::string();
            if (value == "up") {
                link.known = true;
                link.up = true;
            } else if (value == "down") {
                link.known = true;
                link.up = false;
                link.reason = "stated by the caller";
            } else if (value == "unknown") {
                link = nodecore::supervisor::LinkFacts{};
            } else {
                std::fprintf(stderr, "--link-state is up, down or unknown\n");
                return 2;
            }
            continue;
        }
        if (flag == "--agent-state") {
            const std::string value = has_value ? argv[++i] : std::string();
            if (value == "idle") {
                agent.known = true;
                agent.busy = false;
            } else if (value == "busy") {
                agent.known = true;
                agent.busy = true;
            } else if (value == "unknown") {
                agent = nodecore::supervisor::AgentFacts{};
            } else {
                std::fprintf(stderr, "--agent-state is idle, busy or unknown\n");
                return 2;
            }
            continue;
        }
        if (flag == "--service-runtime") {
            const std::string value = has_value ? argv[++i] : std::string();
            if (value == "serving") {
                service_runtime.known = true;
                service_runtime.needs_login = false;
            } else if (value == "needs-login") {
                service_runtime.known = true;
                service_runtime.needs_login = true;
                service_runtime.detail = "NeedsLogin";
            } else if (value == "unknown") {
                service_runtime = nodecore::supervisor::ServiceRuntime{};
            } else {
                std::fprintf(stderr, "--service-runtime is serving, needs-login or unknown\n");
                return 2;
            }
            continue;
        }
        if (flag == "--enable-actions") {
            act = true;
            continue;
        }
        if (flag == "--serve") {
            serve = true;
            continue;
        }
        if (flag == "--no-status-socket") {
            no_status_socket = true;
            continue;
        }
        if (!has_value) {
            std::fprintf(stderr, "%s needs a value\n", flag.c_str());
            return 2;
        }
        const std::string value = argv[++i];
        if (flag == "--manifest") {
            manifest_path = value;
        } else if (flag == "--state") {
            state_path = value;
        } else if (flag == "--receipt") {
            receipt_path = value;
        } else if (flag == "--systemctl") {
            systemctl_path = value;
        } else if (flag == "--status-socket") {
            status_socket_override = value;
        } else if (flag == "--interval-seconds") {
            interval_seconds = std::strtol(value.c_str(), nullptr, 10);
            if (interval_seconds < 1) {
                std::fprintf(stderr, "--interval-seconds must be at least 1\n");
                return 2;
            }
        } else if (flag == "--max-ticks") {
            max_ticks = std::strtol(value.c_str(), nullptr, 10);
        } else if (flag == "--allow-unit") {
            allowed_units.push_back(value);
        } else if (flag == "--facts") {
            facts_path = value;
        } else if (flag == "--owner-map") {
            owner_map_path = value;
        } else if (flag == "--facts-max-age-seconds") {
            facts_max_age_seconds = std::strtol(value.c_str(), nullptr, 10);
            if (facts_max_age_seconds < 1 || facts_max_age_seconds > 3600) {
                std::fprintf(stderr, "--facts-max-age-seconds must be in [1, 3600]\n");
                return 2;
            }
        } else if (flag == "--trigger") {
            if (value == "boot") {
                trigger = nodecore::supervisor::Trigger::Boot;
            } else if (value == "tick") {
                trigger = nodecore::supervisor::Trigger::Tick;
            } else {
                std::fprintf(stderr, "--trigger is boot or tick, got %s\n", value.c_str());
                return 2;
            }
        } else {
            std::fprintf(stderr, "unknown option %s\n", flag.c_str());
            return 2;
        }
    }

    if (manifest_path.empty()) {
        std::fprintf(stderr, "--manifest is required\n");
        return 2;
    }
    if (serve && act && owner_map_path.empty()) {
        // The owner map is how "this unit is mine" stops being a review rule.
        // Both programs read the same file, so a manifest that silently claims
        // another program's unit is refused here rather than discovered at the
        // first restart of somebody else's service.
        std::fprintf(stderr,
                     "--serve and --enable-actions require --owner-map FILE: without it this "
                     "Core cannot show that the units it would act on are its own, and the "
                     "Python daemon may own them\n");
        return 2;
    }
    if (!owner_map_path.empty() && !(serve && act)) {
        std::fprintf(stderr, "--owner-map only applies to --serve --enable-actions\n");
        return 2;
    }
    if (serve && act) {
        // A resident process that acts is a change of authority, and the thing
        // that made the one-shot version honest was that its facts were stated
        // by the caller for THAT pass. A loop cannot inherit that: a `--link-state`
        // given at startup is a claim about a single instant, and reusing it for
        // the life of the process is how `LINK_UP` and `AGENT_IDLE` become
        // hard-coded. So the resident acting form is admitted only with a facts
        // document it re-reads every tick; the persisted budget and the
        // single-writer lease are still required, and the existing ActionGate
        // below enforces them unchanged.
        if (facts_path.empty()) {
            std::fprintf(stderr,
                         "--serve and --enable-actions require --facts FILE: a resident Core that "
                         "acts must re-read link and agent facts every tick. A fact given once at "
                         "startup is a claim about that instant and nothing later\n");
            return 2;
        }
    }
    if (!facts_path.empty() && !(serve && act)) {
        // Refused rather than ignored: a caller who passed --facts believes the
        // loop is using it, and silently not doing so is the failure mode this
        // whole path exists to avoid.
        std::fprintf(stderr, "--facts only applies to --serve --enable-actions\n");
        return 2;
    }
    if (!serve && interval_seconds != 60) {
        std::fprintf(stderr, "--interval-seconds only applies with --serve\n");
        return 2;
    }
    //: The opened, verified immediate parent of the state file. `open` walks
    //: from "/" with openat(O_NOFOLLOW | O_DIRECTORY); the journal writes only
    //: relative to this fd, so a path string replaced later cannot redirect a
    //: budget write.
    nodecore::supervisor::StateDirectory state_directory;
    if (act) {
        // ACTING REQUIRES EVERY FACT. Each is the specific thing that turns a
        // correct plan into a wrong action, and none has a safe default: two
        // writers each believe they own the budget, a restart spent on a
        // network fault outlives the fault, a busy service is not a broken one,
        // and a budget that is not persisted resets at every process restart or
        // reboot. Refused with a named reason rather than defaulted.
        nodecore::supervisor::ActionGateInput gate;
        gate.act = true;
        gate.have_lease = !lease_path.empty();
        if (serve && !facts_path.empty()) {
            // The facts document IS how the resident path knows these. Asking
            // for the same fact again as a startup flag would reintroduce the
            // one-shot claim this path exists to remove -- and would let a
            // caller pass `up` once and never be asked again.
            nodecore::supervisor::FactsOptions facts_options;
            facts_options.max_age_seconds = facts_max_age_seconds;
            std::string facts_text;
            const bool readable = read_file(facts_path, facts_text);
            const nodecore::supervisor::HostFacts opening =
                nodecore::supervisor::load_host_facts(
                    readable ? facts_text.c_str() : nullptr,
                    static_cast<long>(std::time(nullptr)), facts_options);
            gate.link_known = opening.link.known;
            gate.agent_known = opening.agent.known;
            if (!opening.usable) {
                std::fprintf(stderr,
                             "core facts unusable at startup (%s): %s\n",
                             opening.code.c_str(), opening.reason.c_str());
            }
        } else {
            gate.link_known = link.known;
            gate.agent_known = agent.known;
        }
        gate.state_path = state_path;
        const nodecore::supervisor::ActionGateVerdict verdict =
            nodecore::supervisor::check_action_gate(gate);
        if (!verdict.ok) {
            std::fprintf(stderr, "--enable-actions refused: %s (%s)\n",
                         verdict.reason.c_str(), verdict.code.c_str());
            return 2;
        }
        // The flags are complete; now the immediate parent must be opened and
        // verified. The journal keeps this fd and writes relative to it, so the
        // authority is the inode, not a path string checked earlier.
        nodecore::supervisor::StatePathVerdict state_verdict;
        if (!state_directory.open(state_path, state_verdict)) {
            std::fprintf(stderr, "--enable-actions refused: --state %s (%s)\n",
                         state_verdict.reason.c_str(), state_verdict.code.c_str());
            return 2;
        }
    }

    if (!serve && max_ticks >= 0) {
        std::fprintf(stderr, "--max-ticks only applies with --serve\n");
        return 2;
    }

    std::string text;
    if (!read_file(manifest_path, text)) {
        std::fprintf(stderr, "cannot read %s\n", manifest_path.c_str());
        return 2;
    }
    const nodecore::supervisor::ParseOutcome parsed = nodecore::supervisor::parse_manifest(text);
    if (!parsed.ok) {
        std::fprintf(stderr, "%s is not a manifest this version reads:\n", manifest_path.c_str());
        for (const nodecore::supervisor::Refusal& refusal : parsed.refusals) {
            std::fprintf(stderr, "  %s %s: %s\n",
                         refusal.scope.empty() ? "(manifest)" : refusal.scope.c_str(),
                         refusal.code.c_str(), refusal.reason.c_str());
        }
        return 2;
    }
    const nodecore::supervisor::Manifest& manifest = parsed.manifest;

    //: The SAME owner map the Python daemon reads. A manifest unit that the map
    //: assigns to `serverosd`, or does not mention at all, is refused: "this
    //: unit is mine" is a fact two programs agree on, not a comment in a
    //: manifest nobody re-checks.
    if (serve && act && !owner_map_path.empty()) {
        std::string owner_text;
        const bool readable = read_file(owner_map_path, owner_text);
        const nodecore::supervisor::OwnerMap owners =
            nodecore::supervisor::load_owner_map(readable ? owner_text.c_str() : nullptr);
        if (!owners.usable) {
            std::fprintf(stderr, "--owner-map unusable (%s): %s\n",
                         owners.code.c_str(), owners.reason.c_str());
            return 2;
        }
        bool misowned = false;
        for (const nodecore::supervisor::ServiceDesired& service : manifest.services) {
            const std::string* owner = owners.owner_of(service.unit);
            if (owner == nullptr) {
                std::fprintf(stderr,
                             "manifest unit %s has no entry in the owner map; an undeclared "
                             "unit is not this Core's to act on\n",
                             service.unit.c_str());
                misowned = true;
            } else if (*owner != "native-core") {
                std::fprintf(stderr,
                             "manifest unit %s is owned by %s in the shared owner map; this "
                             "Core may not act on it\n",
                             service.unit.c_str(), owner->c_str());
                misowned = true;
            }
        }
        if (misowned) {
            return 2;
        }
    }

    const nodecore::supervisor::UnitAllowlistBuilt allowlist =
        nodecore::supervisor::UnitAllowlist::build(allowed_units);
    if (!allowlist.ok) {
        std::fprintf(stderr, "allowlist refused: %s\n", allowlist.error.c_str());
        return 2;
    }

    nodecore::supervisor::Recovery recovery;
    //: Set inside the tick lambda when the state cannot be persisted. The
    //: single-pass path turns it into exit 2. The resident path stops acting for
    //: the rest of the run: a budget that could not be recorded cannot bound the
    //: next attempt, so continuing would act without one.
    bool state_write_failed = false;
    //: Set when an action could not be issued from a durable attempt (lease or
    //: verb refusal). The single-pass path turns it into exit 2 as well.
    bool action_failed = false;
    //: The clock reading the persisted budget was loaded in. The write takes a
    //: fresh instant but keeps the same boot id, so both halves of the file
    //: name one boot.
    nodecore::supervisor::RecoveryClock state_clock;
    if (act) {
        // The acting gate above guarantees a non-empty, validated state path.
        state_clock.monotonic = monotonic_seconds();
        state_clock.wall_epoch = wall_epoch_seconds();
        if (!read_boot_id(state_clock.boot_id)) {
            std::fprintf(stderr,
                         "refusing to act: the kernel boot id could not be read, so a persisted "
                         "monotonic budget cannot be placed in a boot\n");
            return 2;
        }
        if (state_clock.wall_epoch <= 0 || state_clock.monotonic < 0) {
            std::fprintf(stderr,
                         "refusing to act: the wall or monotonic clock is not usable, so a "
                         "persisted budget cannot be placed in time\n");
            return 2;
        }
        std::string state_text;
        bool present = false;
        std::string state_read_error;
        if (!state_directory.read(state_text, present, state_read_error)) {
            std::fprintf(stderr, "refusing to act: %s\n", state_read_error.c_str());
            return 2;
        }
        const nodecore::supervisor::RecoveryLoaded loaded =
            nodecore::supervisor::load_recovery(present ? &state_text : nullptr, state_clock);
        if (!loaded.ok) {
            std::fprintf(stderr, "refusing to act: %s\n", loaded.error.c_str());
            return 2;
        }
        recovery = loaded.state;
    }

    //: The durable journal at the effect boundary. It writes the SAME
    //: `recovery` ledger the planner consults and owns no budget of its own.
    const auto recovery_clock = [&state_clock]() {
        nodecore::supervisor::RecoveryClock clock;
        clock.monotonic = monotonic_seconds();
        clock.wall_epoch = wall_epoch_seconds();
        clock.boot_id = state_clock.boot_id;
        return clock;
    };
    nodecore::supervisor::ActionJournal journal(std::move(state_directory), &recovery,
                                                recovery_clock);

    nodecore::supervisor::SystemdUserProvider provider(systemctl_path, allowlist.allowlist, &iso_now);

    // One unit of work, shared by both modes: read every unit this run is
    // allowed to name ONCE, decide, and describe. Nothing here writes.
    const long process_started_monotonic = monotonic_seconds();
    nodecore::supervisor::StatusSnapshot snapshot;
    snapshot.mode = "observe-only";
    snapshot.node = manifest.node;
    snapshot.principal = manifest.principal;
    snapshot.pid = static_cast<long>(::getpid());
    snapshot.started_at = iso_now();
    snapshot.generation = manifest.generation;
    snapshot.manifest_schema = manifest.schema;
    snapshot.manifest_sha256 = manifest.sha256;

    nodecore::supervisor::Plan last_plan;
    std::vector<nodecore::supervisor::ExecutionResult> executed;

    //: The one place this program opens a socket: a service-level readiness
    //: probe. Declared once so every reprobe in this run uses the same one.
    nodecore::supervisor::SocketProbeTransport probe_transport;

    //: The service entry for an id, or null. The gate and the reprobe read the
    //: SERVICE's own policy (its probe, and whether its recovery is local) from
    //: this rather than guessing either from the unit name.
    const auto find_service = [&manifest](const std::string& id)
        -> const nodecore::supervisor::ServiceDesired* {
        for (const nodecore::supervisor::ServiceDesired& candidate : manifest.services) {
            if (candidate.id == id) {
                return &candidate;
            }
        }
        return nullptr;
    };

    const auto observe_and_plan = [&](bool allow_execution) -> nodecore::supervisor::TickOutcome {
        // Effects execute inside this pass. Scope the lease here so every exit,
        // including a thrown observation/provider error, releases the writer.
        nodecore::supervisor::WriterLease writer_lease;
        const long tick_started = monotonic_seconds();
        //: Resident acting re-reads its facts EVERY tick, here, before the plan
        //: is built. `usable == false` (absent, unreadable, stale, malformed)
        //: leaves link and agent unknown, and the admission gate refuses on
        //: unknown -- so a stopped writer stops the loop from acting rather than
        //: silently keeping the last good answer.
        nodecore::supervisor::HostFacts tick_facts;
        const bool have_tick_facts = serve && act && !facts_path.empty();
        if (have_tick_facts) {
            nodecore::supervisor::FactsOptions facts_options;
            facts_options.max_age_seconds = facts_max_age_seconds;
            std::string facts_text;
            const bool readable = read_file(facts_path, facts_text);
            tick_facts = nodecore::supervisor::load_host_facts(
                readable ? facts_text.c_str() : nullptr,
                static_cast<long>(std::time(nullptr)), facts_options);
            if (!tick_facts.usable) {
                std::fprintf(stderr, "core facts unusable (%s): %s\n",
                             tick_facts.code.c_str(), tick_facts.reason.c_str());
            }
        }
        nodecore::supervisor::PlanInput input;
        input.manifest = &manifest;
        input.trigger = trigger;
        input.now = tick_started;
        input.allowed_units = allowlist.allowlist.units();
        input.recovery = (allow_execution && act) ? &recovery : nullptr;

        for (const nodecore::supervisor::ServiceDesired& service : manifest.services) {
            if (!allowlist.allowlist.permits(service.unit)) {
                continue;
            }
            const nodecore::supervisor::Observed observed = provider.query(service.unit);
            input.observed[service.unit] = observed;
            if (!observed.measured) {
                std::fprintf(stderr, "not measured: %s (%s)\n", service.unit.c_str(),
                             observed.reason.c_str());
            }
        }

        nodecore::supervisor::Plan plan = nodecore::supervisor::build_plan(input);

        // ---- the gate, applied to the plan BEFORE it is recorded ----
        // Everything here can only REMOVE actions, and it runs before
        // `last_plan` is assigned so that a receipt can never describe a verb
        // that was refused. A receipt that lists an action the Core did not take
        // is worse than no receipt: it is a false record of an effect.
        if (allow_execution && act && plan.ok && !plan.actions.empty()) {
            // 1. THE LEASE. Taken for THIS tick's effects and released when
            //    they finish, not held until the process exits: a lifetime hold
            //    would starve any other owner's units for as long as this Core
            //    ran. A writer that cannot take it does not act at all: with two
            //    writers, every judgement below is being made twice, by two
            //    processes that each believe they are the only one.
            const nodecore::supervisor::LeaseAttempt taken = writer_lease.acquire(
                lease_path, manifest.node.empty() ? std::string("serveros-native-core")
                                                  : manifest.node);
            if (!taken.ok) {
                std::fprintf(stderr, "not acting: %s\n", taken.reason.c_str());
                for (const nodecore::supervisor::Action& action : plan.actions) {
                    plan.refusals.push_back({action.service_id, "NO_LEASE", taken.reason});
                }
                plan.actions.clear();
            }
        }
        if (allow_execution && act && plan.ok && !plan.actions.empty()) {
            // 2. THE FACTS THE PLANNER CANNOT SEE, per action. Admission reads
            //    the same observation the plan was built from, so a refusal
            //    names the fact that made the plan wrong rather than blaming the
            //    service.
            std::vector<nodecore::supervisor::Action> admitted;
            for (const nodecore::supervisor::Action& action : plan.actions) {
                nodecore::supervisor::AdmissionInput gate;
                gate.service_id = action.service_id;
                gate.unit = action.unit;
                const auto found = input.observed.find(action.unit);
                if (found != input.observed.end()) {
                    gate.observed = &found->second;
                    gate.installed = nodecore::supervisor::unit_is_installed(found->second);
                    gate.masked = found->second.load_state == "masked";
                }
                //: In resident acting the tick's own reading wins, because it is
                //: the only one that is true now. The one-shot flags are still
                //: read so the one-shot path is unchanged.
                gate.link = have_tick_facts ? tick_facts.link : link;
                gate.agent = have_tick_facts ? tick_facts.agent : agent;
                gate.runtime = service_runtime;
                if (have_tick_facts) {
                    if (const nodecore::supervisor::ServiceFact* fact =
                            tick_facts.service(action.service_id)) {
                        gate.runtime = fact->runtime;
                        if (fact->maintenance) {
                            // A person put this service into maintenance. Acting
                            // is undoing their decision, so it is refused here
                            // rather than discovered by spending the budget.
                            std::fprintf(stderr, "not acting on %s: MAINTENANCE (%s)\n",
                                         action.unit.c_str(),
                                         fact->maintenance_reason.empty()
                                             ? "declared by the facts document"
                                             : fact->maintenance_reason.c_str());
                            plan.refusals.push_back(
                                {action.service_id, "MAINTENANCE",
                                 "the owner's facts document declares this service in maintenance"});
                            continue;
                        }
                    }
                }
                // The link gate is per-service: a service declared
                // `offline_recoverable` is recovered locally and is not refused
                // on a link that is not about it. Everything else keeps the
                // default, and the default refuses.
                if (const nodecore::supervisor::ServiceDesired* service =
                        find_service(action.service_id)) {
                    gate.link_dependent = !service->recovery.offline_recoverable;
                }
                gate.lease.held_by_us = writer_lease.held();

                const nodecore::supervisor::AdmissionVerdict verdict =
                    nodecore::supervisor::admit(gate);
                if (verdict.admitted) {
                    admitted.push_back(action);
                } else {
                    std::fprintf(stderr, "not acting on %s: %s\n", action.unit.c_str(),
                                 verdict.reason.c_str());
                    plan.refusals.push_back({action.service_id, verdict.code, verdict.reason});
                }
            }
            plan.actions = admitted;
        }

        last_plan = plan;

        if (allow_execution && act && plan.ok) {
            for (const nodecore::supervisor::Action& action : plan.actions) {
                // RE-ESTABLISH THE CLAIM IMMEDIATELY BEFORE THE EFFECT. The
                // lock was taken for a descriptor; if the file was renamed away
                // and replaced since, another writer now holds the path this
                // process believes it holds, and every judgement below would be
                // made twice. A held descriptor is not a held path.
                const nodecore::supervisor::LeaseAttempt still_ours = writer_lease.revalidate();
                if (!still_ours.ok) {
                    std::fprintf(stderr, "not acting on %s: %s\n", action.unit.c_str(),
                                 still_ours.reason.c_str());
                    plan.refusals.push_back({action.service_id, "NO_LEASE", still_ours.reason});
                    writer_lease.release();
                    break;
                }
                // PREWRITE BEFORE THE EFFECT. `execute_effect` records and
                // atomically persists this attempt, and only then invokes the
                // provider. A prewrite failure means no effect is issued.
                const nodecore::supervisor::EffectExecution effect =
                    nodecore::supervisor::execute_effect(provider, journal, writer_lease.held(),
                                                         action.service_id, action.unit,
                                                         action.verb, tick_started);
                if (effect.prewrite_failed) {
                    std::fprintf(stderr, "not acting on %s: %s\n", action.unit.c_str(),
                                 effect.error.c_str());
                    plan.refusals.push_back(
                        {action.service_id, "STATE_PREWRITE_FAILED", effect.error});
                    state_write_failed = true;
                    break;
                }
                if (!effect.ok) {
                    std::fprintf(stderr, "not acting on %s: %s\n", action.unit.c_str(),
                                 effect.error.c_str());
                    plan.refusals.push_back(
                        {action.service_id, "ACTION_NOT_EXECUTED", effect.error});
                    action_failed = true;
                    break;
                }
                const nodecore::supervisor::ProviderResult& result = effect.provider;

                // A VERB THAT EXITED 0 IS NOT A SERVICE THAT ANSWERS. The
                // provider's `ok` says the verb was carried out; it says nothing
                // about whether the service came back serving. So after a start
                // or a restart the Core REPROBES the service's own declared
                // readiness signal, and only READY is counted as a recovery —
                // which is what the budget and the breaker are informed by.
                //
                // The receipt still records the provider's own answer, because
                // `ExecutionResult` is documented as the provider's word and the
                // receipt schema is a versioned cross-language contract; whether
                // the SERVICE recovered is a policy decision and belongs in the
                // budget, not in a field this receipt never carried. What changes
                // here is the accounting: a verb that did not bring the service
                // back spends an attempt and counts toward the breaker, instead
                // of clearing it.
                const nodecore::supervisor::ServiceDesired* service =
                    find_service(action.service_id);
                // An unmeasured probe is the DEFAULT, not an assumption of
                // failure: if the service entry is missing or the probe cannot
                // be attempted, the budget learns nothing.
                nodecore::supervisor::Readiness ready =
                    nodecore::supervisor::Readiness::Unmeasured;
                if (result.ok && service != nullptr) {
                    const nodecore::supervisor::ProbeSpec spec =
                        nodecore::supervisor::probe_spec_of(service->health);
                    const nodecore::supervisor::Observed after = provider.query(action.unit);
                    const nodecore::supervisor::ProbeOutcome probe =
                        nodecore::supervisor::run_probe(spec, probe_transport);
                    ready = nodecore::supervisor::readiness_from_probe(spec, after, probe);
                    if (ready != nodecore::supervisor::Readiness::Ready) {
                        std::fprintf(stderr,
                                     "%s returned %s but is not ready afterwards: %s (%s)\n",
                                     action.unit.c_str(), result.code.c_str(),
                                     nodecore::supervisor::to_word(ready).c_str(),
                                     probe.attempted ? probe.code.c_str()
                                                     : probe.reason.c_str());
                    }
                }
                // `outcome_for` is the one place that decides what the budget
                // learns: a provider failure is a failed recovery; otherwise
                // only READY clears the breaker, only NOT_READY advances it,
                // and UNMEASURED is neither. See `Recovery::record_outcome`.
                const nodecore::supervisor::ActionOutcome outcome =
                    nodecore::supervisor::outcome_for(result.ok, ready);
                const nodecore::supervisor::RecoveryPolicy policy =
                    service != nullptr ? service->recovery
                                       : nodecore::supervisor::RecoveryPolicy{};
                recovery.record_outcome(action.service_id, outcome, policy, tick_started);

                // THE OUTCOME IS A SEPARATE WRITE. If it fails, the attempt from
                // the prewrite is already durable; the outcome is unknown. Do
                // not report success and do not retry blindly.
                std::string outcome_error;
                if (!journal.write_outcome(outcome_error)) {
                    std::fprintf(stderr,
                                 "the effect on %s was issued but its outcome was not "
                                 "persisted: %s\n",
                                 action.unit.c_str(), outcome_error.c_str());
                    state_write_failed = true;
                    executed.push_back(nodecore::supervisor::ExecutionResult{
                        action.service_id, action.unit, action.verb, result.ok, result.code,
                        result.reason, result.exit_code});
                    break;
                }
                executed.push_back(nodecore::supervisor::ExecutionResult{
                    action.service_id, action.unit, action.verb, result.ok, result.code,
                    result.reason, result.exit_code});
            }
            // PER-EFFECT EXCLUSION, NOT A LIFETIME HOLD. Releasing here is what
            // lets a second program — `serverosd` — take the pen for the units
            // IT owns between this Core's ticks. Holding it until process exit
            // would starve every other owner for as long as this one ran.
            writer_lease.release();
        }

        snapshot.ticks += 1;
        snapshot.last_tick_monotonic = tick_started - process_started_monotonic;
        snapshot.last_tick_duration_seconds =
            monotonic_seconds() - tick_started;
        snapshot.uptime_seconds = monotonic_seconds() - process_started_monotonic;
        snapshot.plan_ok = plan.ok;
        snapshot.planned_actions = static_cast<long>(plan.actions.size());
        snapshot.refusals = plan.refusals;
        snapshot.services.clear();
        for (const nodecore::supervisor::ServiceDesired& service : manifest.services) {
            const auto found = input.observed.find(service.unit);
            const nodecore::supervisor::Observed observed =
                found != input.observed.end()
                    ? found->second
                    : nodecore::supervisor::Observed::unavailable(
                          service.unit, "this unit is not in the allowlist, so it was not read");
            snapshot.services.push_back(notice_for(service, observed));
        }
        return nodecore::supervisor::TickOutcome{};
    };

    // ------------------------------------------------------------ resident

    if (serve) {
        // The signal handler is installed before anything is opened, so a
        // SIGTERM that arrives during startup is not lost and does not leave a
        // socket behind.
        struct sigaction action {};
        action.sa_handler = on_stop_signal;
        ::sigemptyset(&action.sa_mask);
        ::sigaction(SIGTERM, &action, nullptr);
        ::sigaction(SIGINT, &action, nullptr);
        // A peer that goes away mid-write must not kill the Core.
        ::signal(SIGPIPE, SIG_IGN);

        int listen_fd = -1;
        std::string socket_path;
        if (!no_status_socket) {
            if (!status_socket_override.empty()) {
                socket_path = status_socket_override;
            } else {
                const char* runtime_dir = ::getenv("XDG_RUNTIME_DIR");
                const nodecore::supervisor::SocketPath resolved =
                    nodecore::supervisor::status_socket_path(runtime_dir == nullptr
                                                                 ? std::string()
                                                                 : std::string(runtime_dir),
                                                             static_cast<long>(::getuid()));
                if (!resolved.ok) {
                    std::fprintf(stderr, "%s\n", resolved.error.c_str());
                    return 2;
                }
                socket_path = resolved.path;
            }
            const nodecore::supervisor::BindOutcome bound =
                nodecore::supervisor::bind_user_status_socket(socket_path);
            if (!bound.ok) {
                // A path that is already there is refused here and NOT removed:
                // see status_socket.hpp. A resident Core does not take over
                // another one's endpoint.
                std::fprintf(stderr, "%s\n", bound.error.c_str());
                return 2;
            }
            listen_fd = bound.fd;
            std::fprintf(stderr, "status socket: %s\n", socket_path.c_str());
        } else {
            std::fprintf(stderr, "status socket: none (--no-status-socket)\n");
        }

        const long uid = static_cast<long>(::getuid());
        const auto status_json = [&snapshot] { return build_status_json(snapshot); };

        nodecore::supervisor::ServeHooks hooks;
        hooks.monotonic_seconds = &monotonic_seconds;
        hooks.stop_requested = [] { return g_stop_requested != 0; };
        if (listen_fd >= 0) {
            // Between ticks the process WAITS on the socket rather than
            // sleeping, so one thread serves both and no answer can be computed
            // against a half-updated observation set: a tick that has not
            // finished has not replaced the snapshot.
            hooks.sleep_millis = [listen_fd, uid, &status_json](long millis) {
                nodecore::supervisor::serve_pending_connection(listen_fd, uid, status_json,
                                                               static_cast<int>(millis), 512, 1000);
            };
        } else {
            hooks.sleep_millis = [](long millis) { sleep_millis_real(millis); };
        }

        nodecore::supervisor::ServeOptions options;
        options.tick_seconds = interval_seconds;
        options.max_ticks = max_ticks;

        const nodecore::supervisor::TickFn tick = [&observe_and_plan, act] {
            // Acting in the resident loop is admitted only when `--enable-actions`
            // was given, which above also required `--state`, `--lease` and a
            // per-tick `--facts` document. Observe-only remains the default.
            return observe_and_plan(act);
        };
        const nodecore::supervisor::ServeReport report = nodecore::supervisor::run_serve(
            options, tick, hooks);

        // Cleanup runs on every exit from the loop, including the one a signal
        // caused: a Core that stopped is not still listening and its path is
        // free for the next start.
        nodecore::supervisor::close_status_socket(listen_fd, socket_path);

        std::fprintf(stderr, "stopped: ticks=%ld errors=%ld stopped_by_request=%d\n", report.ticks,
                     report.tick_errors, report.stopped_by_request ? 1 : 0);
        if (!report.ok) {
            std::fprintf(stderr, "%s\n", report.error.c_str());
            return 2;
        }
        return report.stopped_by_request || report.completed ? 0 : 1;
    }

    // ---------------------------------------------------------- single pass

    const nodecore::supervisor::TickOutcome tick_outcome = observe_and_plan(true);
    if (!tick_outcome.ok) {
        std::fprintf(stderr, "%s\n", tick_outcome.error.c_str());
    }
    if (state_write_failed || action_failed) {
        // The durable attempt or its outcome could not be persisted, or the
        // action could not be issued from a durable attempt. Reporting success
        // here would let the next run start with a stale budget, so the pass
        // exits non-zero.
        return 2;
    }
    const std::string receipt = nodecore::supervisor::build_receipt(
        manifest, last_plan, executed, act ? "active" : "observe-only", snapshot.started_at,
        iso_now());

    if (receipt_path.empty()) {
        std::printf("%s\n", receipt.c_str());
    } else {
        std::ofstream out(receipt_path, std::ios::trunc);
        if (!out.good()) {
            std::fprintf(stderr, "cannot write %s\n", receipt_path.c_str());
            return 2;
        }
        out << receipt << "\n";
    }

    if (!last_plan.ok) {
        return 3;
    }
    for (const auto& result : executed) {
        if (!result.ok) {
            return 4;
        }
    }
    return 0;
}
