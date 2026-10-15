// `serveros/core-lite-status.v1` — ONE read-only status a Core-Lite may report.
//
// WHAT IT IS FOR
// --------------
// Tokyo, the FJ Windows box and the Dell box should be described by the SAME
// document, so a Fleet reader does not need three parsers and cannot compare
// three vocabularies. This is that document's builder: it is handed an already
// parsed `Profile` and the readings an OS adapter already has, and returns JSON.
//
// WHAT IT REFUSES, AND WHY EACH ONE IS A PRODUCT RULE
// ---------------------------------------------------
//   * an UNAUTHORISED observation is NEVER emitted. `profile.observations[].authorized`
//     is the grant; an observation the owner did not grant does not appear at
//     all — not as UNAVAILABLE, not as an empty string. A field that is present
//     and blank reads as «measured nothing», which is a different claim.
//   * an observation nobody measured is `UNAVAILABLE` with a reason. NEVER `0`:
//     the collector's own invariant, inherited rather than restated.
//   * `psi.*` on a platform that cannot read PSI is `UNAVAILABLE` /
//     `UNSUPPORTED_PLATFORM`. On Windows `/proc/pressure` does not exist, so a
//     zero here would be a fabricated reading.
//   * an observation whose Windows collector does not exist yet is
//     `UNAVAILABLE` / `NOT_IMPLEMENTED` — a different word from
//     `UNSUPPORTED_PLATFORM`, because «this platform cannot» and «nobody wrote
//     it» send a reader to two different places.
//   * `mode` is ALWAYS `observe-only`.
//   * the principal must MATCH the OS readback before ANY observation is
//     emitted. A Core that cannot prove it is the account it claims must not
//     report another account's readings under that account's name; a mismatch
//     yields the identity verdict and an EMPTY observation list.
//   * the device reference must MATCH the managed Node identity. JSON cannot
//     claim a device is healthy by asserting it is.
//
// WHAT IT DOES NOT CARRY
// ----------------------
// No argv, no environment, no token, no config path, no credential. The reading
// values are handed in opaquely; this builder never opens a file.
#pragma once

#include <string>
#include <vector>

#include "corelite/profile.hpp"

namespace nodecore::corelite {

inline constexpr const char* kStatusSchema = "serveros/core-lite-status.v1";

enum class ReadingState { Present, Unavailable };

//: One OS adapter's answer about one observation.
struct Reading {
    std::string name;
    ReadingState state = ReadingState::Unavailable;
    //: Required when UNAVAILABLE. One of the closed reasons below.
    std::string reason;
    //: Provenance, e.g. "nodecore/NodeResourceSnapshot v1". Stamped so a reader
    //: can tell WHICH producer a number came from.
    std::string source;
    //: ISO-8601 UTC. Empty is treated as «no provenance» and downgrades to STALE
    //: at the reader, never to «now».
    std::string observed_at;
    //: A JSON fragment, emitted verbatim when PRESENT. Never parsed here.
    std::string value_json;
};

//: The closed UNAVAILABLE reasons. A reason outside this set is refused, so a
//: typo cannot become a new vocabulary word.
inline constexpr const char* kReasons[] = {
    "UNSUPPORTED_PLATFORM",  // this platform cannot provide it, ever
    "NOT_IMPLEMENTED",       // the platform could, but no collector exists yet
    "NO_SOURCE",             // the adapter was asked and had nothing
    "STALE",                 // a reading exists but is past its freshness bound
    "IDENTITY_UNKNOWN",      // couldn't establish which account this is
};

//: MATCHED is the ONLY state that permits a LIVE claim.
inline constexpr const char* kMatched = "MATCHED";
inline constexpr const char* kMismatched = "MISMATCH";
inline constexpr const char* kUnknown = "UNKNOWN";

struct IdentityVerdict {
    std::string state = kUnknown;
    std::string claimed;
    std::string observed;
    std::string reason;
};

struct StatusInput {
    const Profile* profile = nullptr;
    std::string observed_at;  // ISO-8601 UTC
    //: The managed Node identity and what the runtime established. `known` is
    //: false when the runtime could not read it — which is UNKNOWN, never a
    //: match by default.
    bool device_ref_known = false;
    std::string device_ref_observed;
    bool principal_known = false;
    std::string principal_observed;
    //: Whether THIS platform can read `/proc/pressure`.
    bool platform_supports_psi = true;
    //: Whether the platform has any collector at all (Windows has none yet).
    bool platform_has_collector = true;
    //: What the OS adapter managed to read. Filtered by the profile's grants.
    std::vector<Reading> readings;
};

struct StatusOutcome {
    bool ok = false;
    std::string refusal_code;    // "" when ok
    std::string refusal_reason;
    //: True ONLY when the principal AND the device reference both MATCH.
    bool live = false;
    IdentityVerdict principal;
    IdentityVerdict device;
    std::string json;            // the document, when ok
};

StatusOutcome build_status(const StatusInput& input);

//: Whether a string is one of the closed UNAVAILABLE reasons.
bool known_reason(const std::string& reason);

}  // namespace nodecore::corelite
