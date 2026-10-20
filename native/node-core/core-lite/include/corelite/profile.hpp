// The Core-Lite deployment profile: ONE user's READ-ONLY observation surface.
//
// WHY THIS IS NOT A SECOND MANIFEST
// ---------------------------------
// `supervisor/contract/desired-state.v1.md` describes what a Core may ASK
// systemd to do. This file describes what a Core-Lite may LOOK AT, and the two
// do not share a shape on purpose: a document that could both authorise an
// action and grant an observation is a document where a typo in one half turns
// on the other. V1 is observation only, so the profile has no verb in its
// vocabulary at all.
//
// WHAT IT REFUSES, BY NAME
// ------------------------
// Every refusal below is a V1 product rule, not a stylistic preference:
//
//   * `uid` (or any numeric account id) — the UID is resolved from
//     `%h`/`getuid()` at RUN time. A profile that carried one would install
//     Tokyo's `vince` profile onto Dell's `fjd` account with a single `scp`,
//     and the file would look correct.
//   * `observe_only: false` — V1 has no acting mode. The field exists so that
//     the value is written down and refused, rather than absent and assumed.
//   * `restart`, `recover`, `actions`, `restart_allowed`, `enable_actions` —
//     V1 NEVER restarts a running process. A CC/GPU that is mid-run is not a
//     service to be bounced.
//   * an observation outside the closed vocabulary — a name nobody can read is
//     a capability nobody reviewed.
//   * a service whose `owner` is not the document's `principal`.
//   * a `unit` that `valid_unit_id` refuses (the SAME function the supervisor
//     uses, so the two cannot drift about what a unit id is).
#pragma once

#include <string>
#include <vector>

#include "supervisor/model.hpp"  // Refusal

namespace nodecore::corelite {

//: The only `schema` value this reader implements.
inline constexpr const char* kProfileSchema = "serveros/core-lite-profile.v1";

//: The CLOSED observation vocabulary. `psi.*` is Linux-only; on a platform that
//: cannot provide it the observation is reported UNAVAILABLE at runtime, never
//: silently dropped and never zero.
inline constexpr const char* kObservationNames[] = {
    "cpu", "memory", "swap", "psi.cpu", "psi.memory", "psi.io",
    "services", "connector", "gpu.readonly", "storage.readonly",
};

//: Fields that name an EFFECT. V1 refuses them wherever they appear, so that a
//: profile copied from a V2 draft fails loudly instead of quietly carrying a
//: capability this stage does not implement.
inline constexpr const char* kForbiddenFields[] = {
    "restart", "recover", "recovery", "actions", "restart_allowed",
    "enable_actions", "self_heal",
};

struct Observation {
    std::string name;
    //: The OWNER granted this, explicitly, in the document. Never inferred from
    //: the fact that the platform happens to support it.
    bool authorized = false;
};

struct ServiceEntry {
    std::string id;
    std::string owner;  // must equal Profile::principal
    std::string unit;   // must pass supervisor::valid_unit_id
};

struct Profile {
    std::string schema;
    std::string node;       // device:<id>
    std::string principal;  // the OS user. The UID is resolved at RUN time.
    std::string platform = "linux";
    bool observe_only = true;
    long generation = 0;
    std::vector<Observation> observations;
    std::vector<ServiceEntry> services;
};

struct ProfileOutcome {
    bool ok = false;
    Profile profile;
    std::vector<supervisor::Refusal> refusals;
};

//: Parse one profile. `ok` is false iff `refusals` is non-empty. Never throws,
//: never returns a half-built profile.
ProfileOutcome parse_profile(const std::string& text);

//: Whether `name` is in the closed observation vocabulary.
bool known_observation(const std::string& name);

}  // namespace nodecore::corelite
