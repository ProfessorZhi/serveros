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
#include "corelite/identity.hpp"
#include "corelite/profile.hpp"
#include "corelite/status.hpp"

namespace {

void print_usage(std::FILE* out) {
    std::fprintf(out,
                 "usage: core-lite-status --profile PATH [--snapshot PATH]\n"
                 "\n"
                 "  --profile PATH      a serveros/core-lite-profile.v1 document (required)\n"
                 "  --snapshot PATH     a serveros/node-resource-snapshot.v1 document to read\n"
                 "                      readings from. Without it, every granted observation is\n"
                 "                      reported UNAVAILABLE with the reason why.\n"
                 "\n"
                 "Reads only. Never acts, never writes, never reaches the network.\n"
                 "\n"
                 "The device reference and the observation time are NOT arguments: the first\n"
                 "comes from this host's declaration file, the second from the snapshot's own\n"
                 "stamp. See the comment at the top of this file for why.\n");
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

    for (int index = 1; index < argc; ++index) {
        const std::string arg = argv[index];
        if (arg == "-h" || arg == "--help") {
            print_usage(stdout);
            return 0;
        }
        const bool wants_value = (arg == "--profile" || arg == "--snapshot");
        if (wants_value && index + 1 >= argc) {
            std::fprintf(stderr, "%s needs a value\n", arg.c_str());
            return 1;
        }
        if (arg == "--profile") {
            profile_path = argv[++index];
        } else if (arg == "--snapshot") {
            snapshot_path = argv[++index];
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
        auto readings = nodecore::corelite::readings_from_snapshot(profile, snapshot_text, error);
        if (!error.empty()) {
            std::fprintf(stderr, "the snapshot could not be read: %s\n", error.c_str());
            return 1;
        }
        input.readings = std::move(readings.readings);
        // The source's OWN sample time. There is no flag that sets this, because
        // a caller who could set it could make an old document look new.
        input.source_observed_at = readings.observed_at;
    }

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
