#include "supervisor/action_gate.hpp"

#include <string>
#include <utility>

namespace nodecore::supervisor {
namespace {

ActionGateVerdict gate_refuse(const char* code, std::string reason) {
    ActionGateVerdict verdict;
    verdict.ok = false;
    verdict.code = code;
    verdict.reason = std::move(reason);
    return verdict;
}

ActionGateVerdict gate_ok(std::string reason) {
    ActionGateVerdict verdict;
    verdict.ok = true;
    verdict.code = kActionGateOk;
    verdict.reason = std::move(reason);
    return verdict;
}

}  // namespace

ActionGateVerdict check_action_gate(const ActionGateInput& input) {
    if (!input.act) {
        // Observe-only has no side effect to authorise, so the acting facts are
        // not required. The absence of a state path is not a refusal here.
        return gate_ok("actions are not enabled, so there is no side effect to authorise");
    }
    if (!input.have_lease) {
        return gate_refuse(
            kActionGateNeedsLease,
            "two Cores acting on one host each believe they own the recovery budget, and neither "
            "receipt shows it");
    }
    if (!input.link_known) {
        return gate_refuse(kActionGateNeedsLink,
                           "a restart cannot fix a host that is not connected, and this Core does "
                           "not know which it is");
    }
    if (!input.agent_known) {
        return gate_refuse(kActionGateNeedsAgent,
                           "an agent mid-task on a service is a reason to wait, and this Core does "
                           "not know whether one is");
    }
    if (input.state_path.empty()) {
        // THE FACT THIS UNIT EXISTS FOR. Without a persisted budget, a process
        // restart or a reboot grants a fresh set of attempts; the cap is then
        // bypassed by omitting a flag rather than by deciding to raise it.
        return gate_refuse(
            kActionGateNeedsState,
            "an acting Core must name a persistent --state file: without a persisted recovery "
            "budget, every process restart and every reboot starts with a fresh set of attempts, "
            "so the repeated-restart cap can be bypassed by omitting the path");
    }
    if (input.state_path[0] != '/') {
        return gate_refuse(
            kActionGateStateNotAbsolute,
            "\"" + input.state_path +
                "\" is not absolute; a relative state path would resolve against whatever "
                "directory the Core happened to start in, which is not a durable budget");
    }
    return gate_ok("every acting fact is present");
}

}  // namespace nodecore::supervisor
