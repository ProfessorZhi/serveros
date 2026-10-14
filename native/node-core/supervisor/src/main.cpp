// serveros-native-core — one pass: read a manifest, plan, and record.
//
// WHAT IT DOES NOT DO, WHICH IS MOST OF THE DESIGN
// ------------------------------------------------
// It does not daemonize, does not hold a schedule and does not loop. It runs
// once and exits — which is what makes it runnable inside an isolated fixture,
// and what makes "this is what the Core would do" a command rather than a
// deployment.
//
// It starts OBSERVE ONLY. `--enable-actions` is the switch, it has no short
// form, and the unit template never passes it. A reboot cannot turn acting on.
//
// In observe mode it writes NOTHING but the receipt on stdout: no state file,
// no lock, no directory. That is the property a run beside the production
// Python `serverosd` needs — the Python daemon is the writer, and an observer
// that also wrote would be a second one.
#include <time.h>
#include <unistd.h>

#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <functional>
#include <sstream>
#include <string>
#include <vector>

#include "supervisor/manifest.hpp"
#include "supervisor/plan.hpp"
#include "supervisor/receipt.hpp"
#include "supervisor/recovery.hpp"
#include "supervisor/systemd_user.hpp"

namespace {

constexpr const char* kVersion = "0.1.0";
constexpr const char* kDefaultSystemctl = "/usr/bin/systemctl";

void print_usage(std::FILE* out) {
    std::fprintf(out,
                 "serveros-native-core %s\n"
                 "\n"
                 "One pass: read a desired-state manifest, ask systemd --user what is\n"
                 "running, decide, and print a receipt. OBSERVE ONLY unless\n"
                 "--enable-actions is given.\n"
                 "\n"
                 "Usage:\n"
                 "  serveros-native-core --manifest FILE [options]\n"
                 "\n"
                 "Options:\n"
                 "  --manifest FILE        the desired-state manifest to read (required).\n"
                 "  --allow-unit UNIT      a unit this run may name (repeatable). No unit\n"
                 "                         is permitted unless it is listed here.\n"
                 "  --trigger boot|tick    boot proves absence and uses start_on_boot;\n"
                 "                         tick restarts only a failed unit (default tick).\n"
                 "  --state FILE           persisted recovery budget. READ in observe mode\n"
                 "                         and never written there.\n"
                 "  --systemctl PATH       the systemctl to run (default %s).\n"
                 "  --enable-actions       ACT. Asks systemd for the planned verbs. Never\n"
                 "                         enabled by the unit template.\n"
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

}  // namespace

int main(int argc, char** argv) {
    std::string manifest_path;
    std::string state_path;
    std::string receipt_path;
    std::string systemctl_path = kDefaultSystemctl;
    std::vector<std::string> allowed_units;
    bool act = false;
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
        if (flag == "--enable-actions") {
            act = true;
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
        } else if (flag == "--allow-unit") {
            allowed_units.push_back(value);
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

    std::string text;
    if (!read_file(manifest_path, text)) {
        std::fprintf(stderr, "cannot read %s\n", manifest_path.c_str());
        return 2;
    }
    const nodecore::supervisor::ParseOutcome parsed = nodecore::supervisor::parse_manifest(text);
    if (!parsed.ok) {
        std::fprintf(stderr, "%s is not a manifest this version reads:\n", manifest_path.c_str());
        for (const nodecore::supervisor::Refusal& refusal : parsed.refusals) {
            std::fprintf(stderr, "  %s %s: %s\n", refusal.scope.empty() ? "(manifest)" : refusal.scope.c_str(),
                         refusal.code.c_str(), refusal.reason.c_str());
        }
        return 2;
    }
    const nodecore::supervisor::Manifest& manifest = parsed.manifest;

    const nodecore::supervisor::UnitAllowlistBuilt allowlist =
        nodecore::supervisor::UnitAllowlist::build(allowed_units);
    if (!allowlist.ok) {
        std::fprintf(stderr, "allowlist refused: %s\n", allowlist.error.c_str());
        return 2;
    }

    // Observe mode writes nothing, so it does not even read the budget it would
    // not be spending.
    nodecore::supervisor::Recovery recovery;
    if (act && !state_path.empty()) {
        std::string state_text;
        const bool present = read_file(state_path, state_text);
        const nodecore::supervisor::RecoveryLoaded loaded =
            nodecore::supervisor::load_recovery(present ? &state_text : nullptr);
        if (!loaded.ok) {
            // A corrupt budget is NOT an empty one: reading it as empty would
            // reset the count silently, and a budget that resets is not one.
            std::fprintf(stderr, "refusing to act: %s\n", loaded.error.c_str());
            return 2;
        }
        recovery = loaded.state;
    }

    nodecore::supervisor::SystemdUserProvider provider(systemctl_path, allowlist.allowlist, &iso_now);

    const std::string started_at = iso_now();
    nodecore::supervisor::PlanInput input;
    input.manifest = &manifest;
    input.trigger = trigger;
    input.now = static_cast<long>(std::chrono::system_clock::to_time_t(
        std::chrono::system_clock::now()));
    input.allowed_units = allowlist.allowlist.units();
    input.recovery = act ? &recovery : nullptr;

    // Every unit the manifest names, read once. A unit outside the allowlist is
    // never asked about at all — the planner refuses it, and the provider would
    // refuse it again.
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

    const nodecore::supervisor::Plan plan = nodecore::supervisor::build_plan(input);

    std::vector<nodecore::supervisor::ExecutionResult> executed;
    if (act && plan.ok) {
        for (const nodecore::supervisor::Action& action : plan.actions) {
            nodecore::supervisor::ProviderResult result;
            if (action.verb == "start") {
                result = provider.start(action.unit);
            } else if (action.verb == "restart") {
                result = provider.restart(action.unit);
            } else {
                result = provider.stop(action.unit);
            }
            recovery.record_attempt(action.service_id, input.now);
            if (result.ok) {
                recovery.record_success(action.service_id);
            } else {
                const nodecore::supervisor::ServiceDesired* service = nullptr;
                for (const auto& candidate : manifest.services) {
                    if (candidate.id == action.service_id) {
                        service = &candidate;
                    }
                }
                if (service != nullptr) {
                    recovery.record_failure(action.service_id, service->recovery, input.now);
                }
            }
            executed.push_back(nodecore::supervisor::ExecutionResult{
                action.service_id, action.unit, action.verb, result.ok, result.code, result.reason,
                result.exit_code});
        }
        if (!state_path.empty()) {
            std::ofstream state(state_path, std::ios::trunc);
            state << recovery.to_json() << "\n";
        }
    }

    const std::string receipt = nodecore::supervisor::build_receipt(
        manifest, plan, executed, act ? "active" : "observe-only", started_at, iso_now());

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

    // Exit 0 when the plan is well formed and nothing failed. A refused plan or
    // a failed attempt is a non-zero exit, so a caller can gate on it without
    // parsing the receipt — the receipt is for the person, the code is for the
    // script.
    if (!plan.ok) {
        return 3;
    }
    for (const auto& result : executed) {
        if (!result.ok) {
            return 4;
        }
    }
    return 0;
}
