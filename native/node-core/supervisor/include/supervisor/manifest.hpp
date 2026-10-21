// The strict reader for a desired-state manifest.
//
// WHY STRICT IS THE WHOLE FEATURE
// -------------------------------
// This file is the boundary between a document somebody wrote and a list of
// services this program may ask systemd to start. Every leniency here becomes
// an authorisation nobody signed off on:
//
//   * an UNKNOWN FIELD is refused rather than ignored. A typo'd `start_on_boot`
//     silently defaulting to false is a service that never comes back after a
//     reboot, and nobody finds out until the reboot.
//   * a WRONG TYPE is refused rather than coerced. `"observe_only": "false"` is
//     a truthy string, and a coerced read of it turns the safety default off.
//   * a UNIT ID is validated by `valid_unit_id` before it is ever put in an
//     argv, so a traversal (`../../etc/passwd`), a shell metacharacter or an
//     absolute path has nowhere to enter.
//   * a DUPLICATE service id is refused, because the dependency graph is keyed
//     by it and two services with one id is two services one of which is
//     unreachable.
//   * an UNKNOWN DEPENDENCY is refused, because a graph with a dangling edge
//     is one the planner would have to guess about.
//
// The reader never throws and never returns a half-built manifest: either the
// document is one this version reads, or there is a list of named refusals
// explaining exactly which rule it broke.
#pragma once

#include <string>
#include <vector>

#include "supervisor/model.hpp"

namespace nodecore::supervisor {

//: The only `schema` value this reader implements.
inline constexpr const char* kDesiredStateSchema = "serveros/desired-state.v1";

struct ParseOutcome {
    bool ok = false;
    Manifest manifest;
    std::vector<Refusal> refusals;
};

// Parse one manifest. `ok` is false iff `refusals` is non-empty.
//
// `raw` is kept for the digest: `manifest.sha256` is computed over the bytes
// handed in here, exactly as they were read, so `sha256sum manifest.json`
// reproduces it.
ParseOutcome parse_manifest(const std::string& text);

// Whether `unit` is an id this program may put in a `systemctl --user` argv.
//
// The rule is an ALLOWLIST of shapes, not a denylist of dangerous ones: an
// id must be a non-empty stem followed by `.service`, and every character must
// be alphanumeric or one of `_ . @ : -`. That last clause is what makes
// `foo; rm -rf /`, `$(id)`, `a b`, `../../etc/x` and `/bin/sh` unrepresentable
// rather than filtered, and it is the same function the provider calls, so the
// reader and the executor cannot drift apart about what a unit id is.
bool valid_unit_id(const std::string& unit);

}  // namespace nodecore::supervisor
