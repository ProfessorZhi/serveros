// The Windows Core's status envelope: one typed document, and an honest
// UNSUPPORTED for the route this host cannot verify.
//
// WHY THIS IS A SEPARATE DOCUMENT FROM THE SCM PARSER
// ---------------------------------------------------
// `scm_status.hpp` answers "what does the Service Control Manager say about one
// service". A Core has a second question to answer: what may it REPORT about
// that answer, and what must it refuse to claim? This header is that second
// answer, and it is deliberately a pure function of a value — no socket, no
// pipe, no process, no clock — so the document can be asserted on a Linux host.
//
// THE SAME TYPED SCHEMA AS THE LINUX SIDE
// ---------------------------------------
// The readiness words (READY / NOT_READY / UNMEASURED), the admission words
// (`kAdmit*` from `supervisor/admission.hpp`), and the identity verdict
// (MATCHED / MISMATCH / UNKNOWN with a basis) are the SAME words the Linux
// Core-Lite status uses. A Fleet reader comparing a Linux host and a Windows
// host must see one refusal per fact, not two spellings for one fact. The test
// compares against the Linux constants and against the core-lite schema, so a
// rename on either side fails here.
//
// WHAT IT DOES NOT DO
// -------------------
//   * It does not implement the Windows status route. A named pipe protected by
//     a DACL and a client token is the Windows equivalent of the Linux UNIX
//     socket, and a security descriptor that has never been compiled, let alone
//     run, is not a guarantee. `ipc.status` is therefore a CONSTANT
//     `UNSUPPORTED` with `NO_VERIFIED_SECURITY_DESCRIPTOR`. A later change that
//     wants to claim the route works must edit this line and the test that pins
//     it, which is a visible decision rather than a silent drift.
//   * It does not copy `O_NOFOLLOW`, `SO_PEERCRED` or a mode bit into the
//     Windows document. Those defend against an attack that cannot happen on a
//     pipe and miss instance squatting, which is the one that can.
//   * It does not attest the principal or the device. This build has neither a
//     Windows identity readback nor an authenticated device route, so both
//     verdicts are UNKNOWN / NONE with a reason naming what is missing. A
//     MATCHED here would be a claim nobody made.
//   * It does not carry a task row. Task Scheduler is its own vocabulary
//     (`task_status.hpp`) and its own contract, not a second set of service
//     states.
#pragma once

#include <string>
#include <vector>

#include "windows-core/scm_status.hpp"

namespace nodecore::windows_core {

//: The document's own schema id. A reader that does not know this string must
//: refuse the document rather than best-effort parse it.
inline constexpr const char* kWindowsStatusSchema = "serveros/windows-core-status.v1";
inline constexpr const char* kPlatformWindows = "windows";
inline constexpr const char* kModeObserveOnly = "observe-only";
//: This producer has no clock it trusts, and no attested time. Deciding that a
//: reading is stale belongs to a trusted Node or Fleet reader.
inline constexpr const char* kFreshnessNotAsserted = "NOT_ASSERTED";

//: The identity verdict words, shared with `corelite/status.hpp` and the
//: core-lite JSON schema. `UNKNOWN` with basis `NONE` is the only honest answer
//: on a build with no OS readback and no attestation.
inline constexpr const char* kIdentityUnknown = "UNKNOWN";
inline constexpr const char* kIdentityBasisNone = "NONE";
inline constexpr const char* kReasonNoWindowsIdentity = "NO_WINDOWS_IDENTITY_READBACK";
inline constexpr const char* kReasonNoWindowsAttestation = "NO_WINDOWS_DEVICE_ATTESTATION";

//: What a Windows Core is asked to describe. `node` and `principal` are
//: CLAIMS: the document carries them beside an identity verdict that says
//: UNKNOWN, never as if the OS had confirmed them.
struct WindowsStatusInput {
    std::string node;
    std::string principal;
    long generation = 0;
    std::vector<WindowsServiceObservation> services;
};

//: The document. Pure: every byte comes from `input` and the closed constants
//: above. No process, no pipe, no Windows API, no clock.
std::string build_windows_status_json(const WindowsStatusInput& input);

}  // namespace nodecore::windows_core
