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
nodecore::Json identity_verdict(const std::string& claimed, const std::string& reason) {
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
nodecore::Json service_row(const WindowsServiceObservation& observed) {
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

}  // namespace

std::string build_windows_status_json(const WindowsStatusInput& input) {
    nodecore::Json root = nodecore::Json::object();
    root.set("schema", nodecore::Json::string(kWindowsStatusSchema));
    root.set("platform", nodecore::Json::string(kPlatformWindows));
    root.set("mode", nodecore::Json::string(kModeObserveOnly));
    root.set("node", nodecore::Json::string(input.node));
    root.set("principal", nodecore::Json::string(input.principal));
    root.set("generation", nodecore::Json::integer(input.generation));
    root.set("freshness", nodecore::Json::string(kFreshnessNotAsserted));
    root.set("principal_identity",
             identity_verdict(input.principal, kReasonNoWindowsIdentity));
    root.set("device_identity", identity_verdict(input.node, kReasonNoWindowsAttestation));
    root.set("ipc", ipc_route());

    nodecore::Json services = nodecore::Json::array();
    for (const WindowsServiceObservation& observed : input.services) {
        services.push(service_row(observed));
    }
    root.set("services", std::move(services));
    return root.dump();
}

}  // namespace nodecore::windows_core
