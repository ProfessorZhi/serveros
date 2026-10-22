#include "windows-core/status_document.hpp"

#include <string>
#include <utility>

#include "nodecore/json.hpp"
#include "windows-core/win32_scm.hpp"

namespace nodecore::windows_core {
namespace {

//: An identity verdict that makes the CLAIM visible and refuses to dress it as
//: a reading. `claimed` is what the caller said; `state` and `basis` say how
//: little this build can attest about it.
nodecore::Json identity_verdict(const std::string &claimed, const std::string &reason) {
    nodecore::Json verdict = nodecore::Json::object();
    verdict.set("state", nodecore::Json::string(kIdentityUnknown));
    verdict.set("basis", nodecore::Json::string(kIdentityBasisNone));
    verdict.set("claimed", nodecore::Json::string(claimed));
    verdict.set("reason", nodecore::Json::string(reason));
    return verdict;
}

// Compiled transport capability, not an assertion that a pipe is bound.
nodecore::Json ipc_route() {
    nodecore::Json ipc = nodecore::Json::object();
    ipc.set("status", nodecore::Json::string(windows_status_ipc_status()));
    ipc.set("reason", nodecore::Json::string(windows_status_ipc_reason()));
    return ipc;
}

//: One service. `start_type` and `raw_state` are OMITTED when they were not
//: read: an unmeasured service must not carry a number a reader could mistake
//: for a state. The readiness and admission words come from the same functions
//: the SCM parser's tests already pin.
nodecore::Json service_row(const WindowsServiceObservation &observed) {
    nodecore::Json row = nodecore::Json::object();
    row.set("name", nodecore::Json::string(observed.service_name));
    row.set("state", nodecore::Json::string(observed.state));
    if (!observed.start_type.empty()) {
        row.set("start_type", nodecore::Json::string(observed.start_type));
    }
    if (observed.raw_state >= 0) {
        row.set("raw_state", nodecore::Json::integer(observed.raw_state));
    }
    row.set("measured", nodecore::Json::boolean(observed.measured));
    row.set("readiness", nodecore::Json::string(windows_readiness_of(observed)));
    row.set("admission",
            nodecore::Json::string(windows_admission_for(observed, observed.service_name)));
    if (!observed.reason.empty()) {
        row.set("reason", nodecore::Json::string(observed.reason));
    }
    return row;
}

} // namespace

std::string build_windows_status_json(const WindowsStatusInput &input) {
    nodecore::Json root = nodecore::Json::object();
    root.set("schema", nodecore::Json::string(kWindowsStatusSchema));
    root.set("platform", nodecore::Json::string(kPlatformWindows));
    root.set("mode",
             nodecore::Json::string(input.recover_local ? "recover-local" : kModeObserveOnly));
    root.set("node", nodecore::Json::string(input.node));
    root.set("principal", nodecore::Json::string(input.principal));
    root.set("generation", nodecore::Json::integer(input.generation));
    root.set("freshness", nodecore::Json::string(kFreshnessNotAsserted));
    root.set("principal_identity", identity_verdict(input.principal, kReasonNoWindowsIdentity));
    root.set("device_identity", identity_verdict(input.node, kReasonNoWindowsAttestation));
    root.set("ipc", ipc_route());

    nodecore::Json services = nodecore::Json::array();
    for (const WindowsServiceObservation &observed : input.services) {
        services.push(service_row(observed));
    }
    root.set("services", std::move(services));
    if (!input.observed_at.empty())
        root.set("observed_at", nodecore::Json::string(input.observed_at));
    if (!input.runtime_reason.empty())
        root.set("runtime_reason", nodecore::Json::string(input.runtime_reason));
    if (!input.tasks.empty()) {
        nodecore::Json tasks = nodecore::Json::array();
        for (const auto &observed : input.tasks) {
            auto row = nodecore::Json::object();
            row.set("path", nodecore::Json::string(observed.task_path));
            row.set("state", nodecore::Json::string(observed.state));
            row.set("measured", nodecore::Json::boolean(observed.measured));
            if (observed.measured)
                row.set("last_result", nodecore::Json::integer(observed.last_result));
            if (!observed.reason.empty())
                row.set("reason", nodecore::Json::string(observed.reason));
            tasks.push(std::move(row));
        }
        root.set("tasks", std::move(tasks));
    }
    if (!input.runtime.empty()) {
        auto rows = nodecore::Json::array();
        for (const auto &observed : input.runtime) {
            auto row = nodecore::Json::object();
            row.set("role", nodecore::Json::string(observed.role));
            row.set("desired", nodecore::Json::string(observed.desired));
            row.set("readiness", nodecore::Json::string(observed.readiness));
            row.set("recovery_code", nodecore::Json::string(observed.recovery_code));
            row.set("acted", nodecore::Json::boolean(observed.acted));
            rows.push(std::move(row));
        }
        root.set("runtime", std::move(rows));
    }

    return root.dump();
}

} // namespace nodecore::windows_core
