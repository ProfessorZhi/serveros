// `core-lite-status` — print one `serveros/core-lite-status.v1` document.
//
// It is the front door to the whole read-only chain: read a profile, read a
// `node-resource-snapshot.v1`, ask the operating system who this process is, and
// print the status. It writes NOTHING, opens no socket, starts no service and
// has no flag that could make it act — the only way to make it do something else
// would be to add one, and that would be a different review.
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
#include <cstdlib>
#include <string>
#include <vector>

#include "corelite/collector_readings.hpp"
#include "corelite/identity.hpp"
#include "corelite/profile.hpp"
#include "corelite/status.hpp"

namespace {

void print_usage(std::FILE* out) {
    std::fprintf(out,
                 "usage: core-lite-status --profile PATH [--snapshot PATH] [--observed-at TIME]\n"
                 "\n"
                 "  --profile PATH      a serveros/core-lite-profile.v1 document (required)\n"
                 "  --snapshot PATH     a serveros/node-resource-snapshot.v1 document to read\n"
                 "                      readings from. Without it, every granted observation is\n"
                 "                      reported UNAVAILABLE with the reason why.\n"
                 "  --observed-at TIME  ISO-8601 UTC stamped into the document\n"
                 "  --device-ref REF    the managed Node identity, when it is known\n"
                 "                      independently of this node's declaration file\n"
                 "\n"
                 "Reads only. Never acts, never writes, never reaches the network.\n");
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

//: The declaration a device writes about itself. Kept in one place so the CLI
//: and any future caller agree on the path.
constexpr const char* kDeviceDeclarationRelpath = ".config/serveros/node-identity";

}  // namespace

int main(int argc, char** argv) {
    std::string profile_path;
    std::string snapshot_path;
    std::string observed_at;
    std::string device_ref_override;

    for (int index = 1; index < argc; ++index) {
        const std::string arg = argv[index];
        if (arg == "-h" || arg == "--help") {
            print_usage(stdout);
            return 0;
        }
        const bool wants_value = (arg == "--profile" || arg == "--snapshot" ||
                                  arg == "--observed-at" || arg == "--device-ref");
        if (wants_value && index + 1 >= argc) {
            std::fprintf(stderr, "%s needs a value\n", arg.c_str());
            return 1;
        }
        if (arg == "--profile") {
            profile_path = argv[++index];
        } else if (arg == "--snapshot") {
            snapshot_path = argv[++index];
        } else if (arg == "--observed-at") {
            observed_at = argv[++index];
        } else if (arg == "--device-ref") {
            device_ref_override = argv[++index];
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
    input.observed_at = observed_at;
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

    if (!device_ref_override.empty()) {
        input.device_ref_known = true;
        input.device_ref_observed = device_ref_override;
    } else {
        const char* home = std::getenv("HOME");
        const auto node_identity = nodecore::corelite::managed_node_identity(
            home == nullptr ? "" : home, kDeviceDeclarationRelpath);
        input.device_ref_known = node_identity.known;
        input.device_ref_observed = node_identity.device_ref;
    }

    if (!snapshot_path.empty()) {
        std::string snapshot_text;
        if (!read_file(snapshot_path, snapshot_text)) {
            std::fprintf(stderr, "could not read the snapshot at %s\n", snapshot_path.c_str());
            return 1;
        }
        std::string error;
        input.readings = nodecore::corelite::readings_from_snapshot(profile, snapshot_text, error);
        if (!error.empty()) {
            std::fprintf(stderr, "the snapshot could not be read: %s\n", error.c_str());
            return 1;
        }
    }

    const auto outcome = nodecore::corelite::build_status(input);
    if (!outcome.ok) {
        std::fprintf(stderr, "the status was refused: %s: %s\n", outcome.refusal_code.c_str(),
                     outcome.refusal_reason.c_str());
        return 1;
    }
    // stdout is the document and nothing else, so a caller can pipe it straight
    // into a JSON reader.
    std::fputs(outcome.json.c_str(), stdout);
    std::fputc('\n', stdout);
    return 0;
}
