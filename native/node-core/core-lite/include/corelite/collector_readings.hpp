// The Linux adapter: what a `serveros/node-resource-snapshot.v1` document can
// answer, mapped to the observations a profile grants.
//
// WHY IT READS THE COLLECTOR'S DOCUMENT AND NOT `/proc` AGAIN
// ----------------------------------------------------------
// The native collector already samples `/proc` twice and does the arithmetic —
// the counter deltas, the interval, the "a counter went backwards" and "the host
// rebooted between the samples" refusals. A second reader of `/proc` here would
// be a second opinion about what a percentage is, and the two would drift the
// first time one of them learned about a new counter. So Core-Lite does not
// sample: it consumes the snapshot, which is also why the numbers a Fleet reader
// sees here are the same numbers it would see from the collector directly.
//
// WHAT IT REFUSES
// ---------------
//   * a profile whose `platform` is not `linux`. This adapter is the Linux one.
//     A Windows Core's status comes from the Windows adapter (see
//     `windows/README.md`); running this one there would make the Linux program
//     claim a Windows host's health, which is the exact substitution the
//     platform field exists to prevent.
//   * a document that is not a `node-resource-snapshot.v1`, or is not JSON.
//   * an observation the profile did NOT grant is never emitted — not as
//     UNAVAILABLE, not as an empty value. It does not appear.
//
// WHAT IT DOES NOT DO
// -------------------
// It writes nothing, opens nothing, and starts nothing. It is a pure function of
// (profile, document text) and is therefore testable in full against a fixture.
#pragma once

#include <string>
#include <vector>

#include "corelite/profile.hpp"
#include "corelite/status.hpp"

namespace nodecore::corelite {

//: Map `snapshot_json` onto the observations `profile` grants. On failure
//: returns an empty vector and sets `error`; on success every granted
//: observation gets exactly one Reading — PRESENT when the snapshot measures it,
//: UNAVAILABLE / NOT_IMPLEMENTED when the platform could but no collector block
//: exists yet. A granted observation is NEVER silently omitted, because
//: omission and "measured nothing" would then look the same to a Fleet reader.
std::vector<Reading> readings_from_snapshot(const Profile& profile,
                                            const std::string& snapshot_json, std::string& error);

}  // namespace nodecore::corelite
