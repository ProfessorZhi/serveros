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
//   * a `live` claim needs an identity that was established by something
//     stronger than the document's own text — see `basis`, below.
//
// WHAT IT DOES NOT CARRY
// ----------------------
// No argv, no environment, no token, no config path, no credential, no raw
// device name. A reading's value is a `Json` the ADAPTER built key by key, never
// a document it copied — see `Reading::value`.
#pragma once

#include <string>
#include <vector>

#include "corelite/profile.hpp"
#include "nodecore/json.hpp"

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
    //: ISO-8601 UTC. The SOURCE's own sample time, copied, never a clock read
    //: here and never a caller's string. Empty means the source stated none.
    std::string observed_at;
    //: The measured value, TYPED — never a text fragment.
    //:
    //: This is the fix for the class of bug that a fragment interface invites. A
    //: `std::string value_json` that is "re-parsed before emission" is a filter,
    //: and a filter has to be right on every path forever; one adapter that
    //: forgets, and a `token` key travels from an untrusted document into the
    //: status. A `Json` can only contain what its writer SET, so an adapter
    //: physically cannot forward a key it did not name.
    //:
    //: It is still not enough on its own: an adapter could copy a parsed block
    //: wholesale. `has_value` and this type make the SHAPE safe; the allowlist in
    //: the adapter makes the CONTENT reviewed, and the forged-snapshot test is
    //: what holds both.
    nodecore::Json value;
    bool has_value = false;
    //: How much of `value` was actually measured — `COMPLETE` when every
    //: projected field is a number, `PARTIAL` when at least one is an explicit
    //: UNAVAILABLE. Empty on a reading that is not PRESENT.
    //:
    //: WHY THIS EXISTS. Two real cases produce a value that is partly measured:
    //: a swap reading whose stock is readable while the interval between samples
    //: is not, and a `psi.cpu` on a kernel that does not report `full`. Both used
    //: to be a choice between two bad answers — call the whole reading
    //: UNAVAILABLE and throw away real measurements, or call it PRESENT and let a
    //: consumer assume the absent field is zero. This says which it is, so
    //: neither has to be guessed and nothing has to be thrown away.
    std::string completeness;
};

//: The closed UNAVAILABLE reasons. A reason outside this set is refused, so a
//: typo cannot become a new vocabulary word.
inline constexpr const char* kReasons[] = {
    "UNSUPPORTED_PLATFORM",  // this platform cannot provide it, ever
    "NOT_IMPLEMENTED",       // the platform could, but no collector exists yet
    "NO_SOURCE",             // the adapter was asked and had nothing usable
    "STALE",                 // a reading exists but is past its freshness bound
    "IDENTITY_UNKNOWN",      // couldn't establish which account this is
};

//: MATCHED is the only state that permits anything to be claimed.
inline constexpr const char* kMatched = "MATCHED";
inline constexpr const char* kMismatched = "MISMATCH";
inline constexpr const char* kUnknown = "UNKNOWN";

//: HOW an identity was established, weakest to strongest. This is the field that
//: stops a string from becoming a proof.
//:
//: A caller argument and a file the account wrote itself are BOTH just claims —
//: the second is a slightly better-organised claim, not evidence — and neither
//: may produce a `live` claim. Only ATTESTED, an answer that came back over an
//: authenticated route this process did not author, may.
inline constexpr const char* kBasisAttested = "ATTESTED";
inline constexpr const char* kBasisLocalDeclaration = "LOCAL_DECLARATION";
inline constexpr const char* kBasisCallerSupplied = "CALLER_SUPPLIED";
inline constexpr const char* kBasisNone = "NONE";

//: Whether every projected field of a PRESENT reading was measured.
inline constexpr const char* kComplete = "COMPLETE";
inline constexpr const char* kPartial = "PARTIAL";

//: The closed reason a PROJECTED FIELD carries when it could not be read.
//:
//: A field inside a value uses this, and never the producer's own prose: the
//: producer's reasons name /proc paths, and a status is read by people who are
//: not on this host. Same vocabulary as the observation-level reasons, so a
//: reader learns one set of words.
inline constexpr const char* kFieldUnavailable = "UNAVAILABLE";

//: How this builder describes the AGE of what it carries.
//:
//: `NOT_ASSERTED` is the only value this stage can emit, and it is the honest
//: one: the builder knows what the source stamped, and nothing else. It has no
//: clock it trusts, no signed time, and no way to tell «three seconds ago» from
//: «three days ago» in a document an operator handed it. Deciding that a reading
//: is stale is a trusted Node's or a Fleet reader's call, made against a clock
//: and a policy they own — not a claim this document makes about itself.
inline constexpr const char* kFreshnessNotAsserted = "NOT_ASSERTED";

struct IdentityVerdict {
    std::string state = kUnknown;
    //: See the kBasis* constants. `UNKNOWN` carries NONE.
    std::string basis = kBasisNone;
    std::string claimed;
    std::string observed;
    std::string reason;
};

struct StatusInput {
    const Profile* profile = nullptr;
    //: The SOURCE's own sample time, copied from the producer — the snapshot's
    //: `observed_at`, say. NOT a value read from this process's clock and NOT
    //: something a caller may invent: a caller who could set this could make a
    //: week-old document look current. Empty means the source stated none, and
    //: the document then states none.
    std::string source_observed_at;
    //: The managed Node identity and what the runtime established. `known` is
    //: false when the runtime could not read it — which is UNKNOWN, never a
    //: match by default.
    bool device_ref_known = false;
    std::string device_ref_observed;
    //: HOW that value was obtained. Required whenever `device_ref_known`; a
    //: caller that omits it gets no `live` claim, which is the correct answer for
    //: «somebody handed me a string».
    std::string device_ref_basis = kBasisNone;
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
    //: True ONLY when the principal MATCHED **and** the device identity MATCHED
    //: on an ATTESTED basis. Nothing in this stage produces an ATTESTED basis, so
    //: this is `false` everywhere today — deliberately, and the document says so
    //: in `device_identity.basis` rather than leaving a reader to guess.
    bool live = false;
    IdentityVerdict principal;
    IdentityVerdict device;
    std::string json;            // the document, when ok
};

StatusOutcome build_status(const StatusInput& input);

//: Whether a string is one of the closed UNAVAILABLE reasons.
bool known_reason(const std::string& reason);

//: Whether a string is one of the kBasis* constants.
bool known_basis(const std::string& basis);

}  // namespace nodecore::corelite
