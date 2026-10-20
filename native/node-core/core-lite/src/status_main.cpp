// `core-lite-status` — print one `serveros/core-lite-status.v1` document.
//
// It is the front door to the whole read-only chain: read a profile, read a
// `node-resource-snapshot.v1`, ask the operating system who this process is, and
// print the status. It writes NOTHING, opens no socket, starts no service and
// has no flag that could make it act — the only way to make it do something else
// would be to add one, and that would be a different review.
//
// WHAT IT DELIBERATELY HAS NO FLAG FOR
// ------------------------------------
// There is no `--device-ref`: a caller able to name the device could make the
// status report `MATCHED` by typing the profile's own string, which is not
// evidence of anything. The device reference comes from this host's declaration
// file, read out of the home directory the USER DATABASE records for this
// account (never `$HOME`, which a caller controls), and it is stamped
// `LOCAL_DECLARATION` — this account's claim about itself, which is still not
// the same as an authenticated attestation, and `live` stays false for it.
//
// There is no `--observed-at`: a caller able to stamp a time could make a
// week-old document look current. The time in the document is the source
// snapshot's own, and `freshness` is stated as `NOT_ASSERTED`.
//
// EXIT CODE
// ---------
//   0  a status document was printed. This includes `live: false`: a refusal to
//      claim a device or an account is a RESULT, not a program failure, and
//      exiting non-zero would make a monitor page someone for a correct answer.
//   1  the program could not produce a document at all (unreadable profile, a
//      profile the reader refuses). The reason goes to stderr, and no document
//      is printed, because half a status is worse than none.
#include <cstdio>
#include <string>
#include <vector>

#include "corelite/collector_readings.hpp"
#include "corelite/gpu_readings.hpp"
#include "corelite/identity.hpp"
#include "corelite/profile.hpp"
#include "corelite/service_readings.hpp"
#include "corelite/status.hpp"
#include "nodecore/clock.hpp"
#include "proc.hpp"
#include "supervisor/provider.hpp"
#include "supervisor/systemd_user.hpp"

namespace {

//: The systemctl this program would ask, if a profile grants `services`. The
//: same default the supervisor uses, and overridable for the same reason.
constexpr const char* kDefaultSystemctl = "/usr/bin/systemctl";

//: Wraps the supervisor's unit adapter and exposes ONE method.
//:
//: `SystemdUserProvider` also has `start`, `restart` and `stop`, and this class
//: holds one. What makes that safe is not this class but the interface the
//: projection is compiled against: `services_reading` takes a `ServiceQuery`,
//: which has only `query`, so the code that decides what a service reading SAYS
//: has no way to express a verb. This wrapper is the seam that keeps it that way.
class ReadOnlyUnits final : public nodecore::corelite::ServiceQuery {
public:
    explicit ReadOnlyUnits(nodecore::supervisor::SystemdUserProvider& provider)
        : provider_(provider) {}

    nodecore::supervisor::Observed query(const std::string& unit) override {
        return provider_.query(unit);
    }

private:
    nodecore::supervisor::SystemdUserProvider& provider_;
};

//: Runs the FIXED program for `gpu.readonly`.
//:
//: The argv is built here out of three constants and nothing else: an absolute
//: path, a field list assembled from `kNvidiaSmiQueryFields`, and a format
//: string. There is no flag on this program that can change any of them, so a
//: caller cannot point it at another program or add an argument to this one —
//: which is a stronger statement than "the caller is not supposed to".
//:
//: It goes through the supervisor's bounded runner: `execvp` with no shell, a
//: wall deadline that kills the whole process group, and both output streams
//: capped. A wedged driver is exactly when a monitoring program must not wait.
class NvidiaSmi final : public nodecore::corelite::NvidiaQuery {
public:
    nodecore::corelite::NvidiaAnswer sample() override {
        std::string query = "--query-gpu=";
        const std::size_t field_count =
            sizeof(nodecore::corelite::kNvidiaSmiQueryFields) /
            sizeof(nodecore::corelite::kNvidiaSmiQueryFields[0]);
        for (std::size_t index = 0; index < field_count; ++index) {
            if (index != 0) {
                query += ",";
            }
            query += nodecore::corelite::kNvidiaSmiQueryFields[index];
        }
        const std::vector<std::string> argv = {nodecore::corelite::kNvidiaSmiPath, query,
                                              "--format=csv,noheader,nounits"};
        const nodecore::supervisor::detail::RunResult run = nodecore::supervisor::detail::run_argv(
            argv, {}, nodecore::corelite::kNvidiaSmiTimeoutSeconds,
            nodecore::corelite::kNvidiaSmiMaxOutputBytes);
        nodecore::corelite::NvidiaAnswer answer;
        answer.ok = run.ok;
        answer.stdout_text = run.stdout_text;
        answer.code = run.code;
        answer.exit_code = run.exit_code;
        return answer;
    }
};

void print_usage(std::FILE* out) {
    std::fprintf(out,
                 "usage: core-lite-status --profile PATH [--snapshot PATH] [--systemctl PATH]\n"
                 "\n"
                 "  --profile PATH      a serveros/core-lite-profile.v1 document (required)\n"
                 "  --snapshot PATH     a serveros/node-resource-snapshot.v1 document to read\n"
                 "                      readings from. Without it, every granted observation is\n"
                 "                      reported UNAVAILABLE with the reason why.\n"
                 "  --systemctl PATH    the systemctl to ASK about this profile's units (default\n"
                 "                      %s). Only ever `show`: this program has no verb that\n"
                 "                      starts, stops or restarts anything.\n"
                 "\n"
                 "Reads only. Never acts, never writes, never reaches the network.\n"
                 "\n"
                 "The device reference and the observation time are NOT arguments: the first\n"
                 "comes from this host's declaration file, the second from the snapshot's own\n"
                 "stamp. See the comment at the top of this file for why.\n",
                 kDefaultSystemctl);
}

bool read_file(const std::string& path, std::string& out) {
    std::FILE* file = std::fopen(path.c_str(), "rb");
    if (file == nullptr) {
        return false;
    }
    char buffer[8192];
    std::size_t got = 0;
    while ((got = std::fread(buffer, 1, sizeof(buffer), file)) > 0) {
        out.append(buffer, got);
    }
    std::fclose(file);
    return true;
}

//: The declaration a device writes about itself, relative to its OWN home as the
//: user database records it. Kept in one place so the CLI and any future caller
//: agree on the path.
constexpr const char* kDeviceDeclarationRelpath = ".config/serveros/node-identity";

}  // namespace

int main(int argc, char** argv) {
    std::string profile_path;
    std::string snapshot_path;
    std::string systemctl_path = kDefaultSystemctl;

    for (int index = 1; index < argc; ++index) {
        const std::string arg = argv[index];
        if (arg == "-h" || arg == "--help") {
            print_usage(stdout);
            return 0;
        }
        const bool wants_value =
            (arg == "--profile" || arg == "--snapshot" || arg == "--systemctl");
        if (wants_value && index + 1 >= argc) {
            std::fprintf(stderr, "%s needs a value\n", arg.c_str());
            return 1;
        }
        if (arg == "--profile") {
            profile_path = argv[++index];
        } else if (arg == "--snapshot") {
            snapshot_path = argv[++index];
        } else if (arg == "--systemctl") {
            systemctl_path = argv[++index];
        } else {
            std::fprintf(stderr, "unknown argument: %s\n", arg.c_str());
            print_usage(stderr);
            return 1;
        }
    }
    if (profile_path.empty()) {
        std::fprintf(stderr, "--profile is required\n");
        print_usage(stderr);
        return 1;
    }

    std::string profile_text;
    if (!read_file(profile_path, profile_text)) {
        std::fprintf(stderr, "could not read the profile at %s\n", profile_path.c_str());
        return 1;
    }
    const auto parsed = nodecore::corelite::parse_profile(profile_text);
    if (!parsed.ok) {
        std::fprintf(stderr, "%s is not a usable profile:\n", profile_path.c_str());
        for (const auto& refusal : parsed.refusals) {
            std::fprintf(stderr, "  %s: %s\n", refusal.code.c_str(), refusal.reason.c_str());
        }
        return 1;
    }
    const auto& profile = parsed.profile;

    nodecore::corelite::StatusInput input;
    input.profile = &profile;
    // Linux can read every observation this stage names; the psi family is not
    // special-cased here because the ADAPTER is what knows no block exists for
    // it yet, and it says NOT_IMPLEMENTED rather than the platform saying
    // UNSUPPORTED_PLATFORM. Getting that backwards would blame the host for a
    // missing program.
    input.platform_supports_psi = (profile.platform == "linux");
    input.platform_has_collector = (profile.platform == "linux");

    // ---- the runtime identity readback ---------------------------------
    const auto os_identity = nodecore::corelite::current_user();
    input.principal_known = os_identity.known;
    input.principal_observed = os_identity.user;

    // The home comes from the user database, not from the environment.
    const auto node_identity =
        nodecore::corelite::managed_node_identity(os_identity, kDeviceDeclarationRelpath);
    input.device_ref_known = node_identity.known;
    input.device_ref_observed = node_identity.device_ref;
    input.device_ref_basis = node_identity.basis;

    if (!snapshot_path.empty()) {
        std::string snapshot_text;
        if (!read_file(snapshot_path, snapshot_text)) {
            std::fprintf(stderr, "could not read the snapshot at %s\n", snapshot_path.c_str());
            return 1;
        }
        std::string error;
        // `services` comes from systemd, not from the snapshot: named here so
        // this adapter does not also answer it with a placeholder.
        // `services` comes from systemd and `gpu.readonly` from nvidia-smi:
        // both are OS facts, not `/proc` ones, so both are named here and this
        // adapter does not also answer them with a placeholder.
        auto readings = nodecore::corelite::readings_from_snapshot(
            profile, snapshot_text, error, {"services", "gpu.readonly"});
        if (!error.empty()) {
            std::fprintf(stderr, "the snapshot could not be read: %s\n", error.c_str());
            return 1;
        }
        input.readings = std::move(readings.readings);
        // The source's OWN sample time. There is no flag that sets this, because
        // a caller who could set it could make an old document look new.
        input.source_observed_at = readings.observed_at;
    }

    // ---- the services observation ---------------------------------------
    // Services are not a /proc fact and are not in the snapshot: they live in
    // systemd, and the only supported way to ask is the supervisor's adapter.
    // Read once, at the end, and only when the owner granted the observation —
    // a profile that did not ask for `services` costs no process at all.
    bool grants_services = false;
    for (const auto& observation : profile.observations) {
        if (observation.authorized && observation.name == "services") {
            grants_services = true;
        }
    }
    if (grants_services) {
        std::vector<std::string> units;
        for (const auto& service : profile.services) {
            units.push_back(service.unit);
        }
        const auto built = nodecore::supervisor::UnitAllowlist::build(units);
        if (!built.ok) {
            std::fprintf(stderr, "the profile's units are not usable: %s\n", built.error.c_str());
            return 1;
        }
        nodecore::supervisor::SystemdUserProvider provider(
            systemctl_path, built.allowlist,
            []() { return nodecore::iso8601_utc(std::chrono::system_clock::now()); });
        ReadOnlyUnits units_query(provider);
        std::string service_error;
        const auto services =
            nodecore::corelite::services_reading(profile, os_identity, units_query, service_error);
        if (!service_error.empty()) {
            std::fprintf(stderr, "the services observation could not be built: %s\n",
                         service_error.c_str());
            return 1;
        }
        input.readings.push_back(services);
    }

    // ---- the gpu.readonly observation ------------------------------------
    // The sampler is constructed unconditionally and RUNS nothing unless the
    // owner granted the observation and this is a Linux profile: `gpu_reading`
    // checks both before it calls `sample()`, so an ungranted observation costs
    // no process at all.
    NvidiaSmi gpu_sampler;
    input.readings.push_back(nodecore::corelite::gpu_reading(
        profile, os_identity, gpu_sampler,
        nodecore::iso8601_utc(std::chrono::system_clock::now())));

    const auto outcome = nodecore::corelite::build_status(input);
    if (!outcome.ok) {
        std::fprintf(stderr, "the status was refused: %s: %s\n", outcome.refusal_code.c_str(),
                     outcome.refusal_reason.c_str());
        return 1;
    }

    // stdout is the document and nothing else, so a caller can pipe it straight
    // into a JSON reader. The one thing an operator needs that the document
    // states but a skim would miss — WHY it is not live — goes to stderr.
    if (!outcome.live) {
        std::fprintf(stderr,
                     "note: live is false. device_identity.basis=%s; %s\n",
                     outcome.device.basis.c_str(),
                     outcome.device.reason.empty()
                         ? "the device identity was not established by an authenticated route"
                         : outcome.device.reason.c_str());
    }
    std::fputs(outcome.json.c_str(), stdout);
    std::fputc('\n', stdout);
    return 0;
}
