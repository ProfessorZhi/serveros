// Pure Windows status snapshot. IPC capability is compiled availability;
// principal/device claims remain UNKNOWN until their separate owner attests them.
#pragma once

#include <string>
#include <vector>

#include "windows-core/scm_status.hpp"
#include "windows-core/task_status.hpp"

namespace nodecore::windows_core {

//: The document's own schema id. A reader that does not know this string must
//: refuse the document rather than best-effort parse it.
inline constexpr const char *kWindowsStatusSchema = "serveros/windows-core-status.v1";
inline constexpr const char *kPlatformWindows = "windows";
inline constexpr const char *kModeObserveOnly = "observe-only";
//: This producer has no clock it trusts, and no attested time. Deciding that a
//: reading is stale belongs to a trusted Node or Fleet reader.
inline constexpr const char *kFreshnessNotAsserted = "NOT_ASSERTED";

//: The identity verdict words, shared with `corelite/status.hpp` and the
//: core-lite JSON schema. `UNKNOWN` with basis `NONE` is the only honest answer
//: on a build with no OS readback and no attestation.
inline constexpr const char *kIdentityUnknown = "UNKNOWN";
inline constexpr const char *kIdentityBasisNone = "NONE";
inline constexpr const char *kReasonNoWindowsIdentity = "NO_WINDOWS_IDENTITY_READBACK";
inline constexpr const char *kReasonNoWindowsAttestation = "NO_WINDOWS_DEVICE_ATTESTATION";

//: What a Windows Core is asked to describe. `node` and `principal` are
//: CLAIMS: the document carries them beside an identity verdict that says
//: UNKNOWN, never as if the OS had confirmed them.
struct WindowsRuntimeRow {
    std::string role, desired, readiness, recovery_code;
    bool acted = false;
};
struct WindowsStatusInput {
    std::string node;
    std::string principal;
    long generation = 0;
    std::vector<WindowsServiceObservation> services;
    std::vector<WindowsTaskObservation> tasks;
    std::vector<WindowsRuntimeRow> runtime;
    std::string observed_at, runtime_reason;
    bool recover_local = false;
};

//: The document. Pure: every byte comes from `input` and the closed constants
//: above. No process, no pipe, no Windows API, no clock.
std::string build_windows_status_json(const WindowsStatusInput &input);

} // namespace nodecore::windows_core
