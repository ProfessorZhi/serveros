// What this program says it is, and which units it says are its own.
//
// WHY THIS EXISTS
// ---------------
// Two programs can act on this machine's services: the Python `serverosd` and
// this Core. "One writer per unit" is written down in the shared owner map, but
// until now it was only ever *enforced* — inside `main()`, as an exit code and a
// line on stderr. Nothing produced a value a reader could compare, so the
// question "do these two programs both believe they own `x.service`?" could only
// be answered by reading two manifests and one map by hand.
//
// This module turns that gate into a document. For each unit the manifest names,
// it states the owner the shared map assigns and whether THIS program may act on
// it. Two programs' documents can then be compared by something that is not a
// person, which is the difference between a rule and a claim.
//
// WHAT IT IS NOT
// --------------
// It is not an authority. It reports what the map said; it does not decide what
// the map should say, and it never grants a unit. The acting path still refuses
// through `manifest_owner_refusals`, unchanged — this document is that verdict
// rendered, not a second opinion about it.
//
// It is also not an inventory of the device. It describes one program reading one
// manifest against one owner map at one instant, and says so with the digests it
// read. A device's installed versions are a separate fact, collected read-only,
// and this document does not stand in for it.
//
// WHAT IT MUST NOT CARRY
// ----------------------
// No paths, no argv, no token. It carries digests of the two documents it read
// and the identity of the program reading them; those are the facts a comparison
// needs and nothing else. This is the same discipline `status.hpp` states, for
// the same reason: a document an operator pastes into a chat must not name where
// a credential lives.
#pragma once

#include <string>
#include <vector>

#include "supervisor/facts.hpp"
#include "supervisor/model.hpp"

namespace nodecore::supervisor {

//: The document this module emits. Bumped if a field's meaning changes.
inline constexpr const char* kCapabilitySchema = "serveros/native-core-capability.v1";

//: The two modes a Core runs in. `observe` is the default everywhere; `act` is
//: reachable only through `--serve --enable-actions` and its preconditions.
inline constexpr const char* kCapabilityModeObserve = "observe";
inline constexpr const char* kCapabilityModeAct = "act";

//: The closed vocabulary of claim verdicts. A caller switches on these rather
//: than parsing a sentence, and a new one is a change to this header.
inline constexpr const char* kClaimOwned = "OWNED";
inline constexpr const char* kClaimNotMine = "NOT_MINE";
inline constexpr const char* kClaimUndeclared = "UNDECLARED";

//: What the caller knows about the run that this module cannot: the version is
//: the binary's own, and the manifest digest is over the bytes as read.
struct CapabilityInfo {
    std::string version;
    //: kCapabilityModeObserve or kCapabilityModeAct. Anything else is a
    //: programming error and is rendered as-is rather than guessed at.
    std::string mode;
    //: SHA-256 over the manifest's bytes, the same number `sha256sum` gives.
    std::string manifest_sha256;
};

//: One manifest service, its owner in the shared map, and the verdict.
struct UnitClaim {
    std::string id;
    std::string unit;
    //: Empty when the map does not declare the unit. That is a different reason
    //: from "declared, and it is someone else's", with the same refusal.
    std::string owner;
    //: kClaimOwned / kClaimNotMine / kClaimUndeclared.
    std::string verdict;
    bool this_core_may_act = false;
};

//: The gate as data. Derived from `manifest_owner_refusals`, so this and the
//: acting path cannot disagree about who may act on what.
std::vector<UnitClaim> claims_of(const Manifest& manifest, const OwnerMap& owners);

//: Units both writers claim, from two documents' claim lists.
//:
//: Empty is the one-writer property holding. Non-empty is two programs each told
//: by the same map that a unit is theirs — which the map cannot produce for a
//: *single* reader (it is one value per unit), so a non-empty answer means the
//: two programs are not reading the same map, or not reading it the same way.
std::vector<std::string> doubly_claimed_units(const std::vector<UnitClaim>& a,
                                              const std::vector<UnitClaim>& b);

//: Render one capability document. Keys are lexicographic and the bytes are
//: canonical, because a reader compares two of these and `dump_compact()` is the
//: form the Python core hashes.
std::string build_capability_json(const Manifest& manifest, const OwnerMap& owners,
                                  const CapabilityInfo& info);

}  // namespace nodecore::supervisor
