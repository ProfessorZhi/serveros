// The Linux adapter: what a `serveros/node-resource-snapshot.v1` document can
// answer, PROJECTED onto the reviewed fields, for the observations a profile
// grants.
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
// WHY IT PROJECTS RATHER THAN FORWARDS
// ------------------------------------
// A snapshot is INPUT, not a trusted document. It arrives as a file, and a file
// can be edited. Forwarding a block wholesale — however valid its syntax — hands
// whoever wrote that file a channel into the status: an added `token`, `argv` or
// `config_path` key, or the collector's own `path` / `mount_point` / `source`
// fields, would be published verbatim. So every field below is NAMED here, and
// nothing unnamed can appear. A projection is not a filter — a filter has to
// recognise what to remove and is wrong the first time something new appears; a
// projection can only emit what it was written to emit.
//
//   observation          projected                                   never
//   ------------------   -----------------------------------------   -------------------------
//   cpu                  used_percent (0..100)                       counters, jiffies, state
//   memory               total/available/free_bytes, used_percent    scope, source
//   storage.readonly     per-role {role, total/available/used_bytes} path, mount_point, source,
//                        and per-role {role, read/write_bytes}       fstype, inodes, device name
//
// A role name is a small closed-looking vocabulary (`ROOT_FS`, `DATA_FS`), so it
// is allowed through ONLY when it matches `[A-Za-z0-9_-]{1,32}`. The disk-io
// device NAME (`vda2`) is deliberately dropped: it is a raw hardware identifier,
// and the role already says which filesystem it belongs to.
//
// WHAT IT REFUSES
// ---------------
//   * a profile whose `platform` is not `linux`. This adapter is the Linux one.
//     A Windows Core's status comes from the Windows adapter (see
//     `windows/README.md`); running this one there would make the Linux program
//     claim a Windows host's health, which is the exact substitution the
//     platform field exists to prevent.
//   * a document that is not a `node-resource-snapshot.v1`, or is not JSON.
//   * a block that is PRESENT but does not carry every field projected from it,
//     or carries one that is out of range. The reading becomes UNAVAILABLE /
//     NO_SOURCE rather than a partly-filled value: a status with three of four
//     memory numbers is a status a reader will do arithmetic on and get wrong.
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

struct SnapshotReadings {
    std::vector<Reading> readings;
    //: The snapshot's OWN `observed_at`, copied — the time its two samples were
    //: taken. Empty when the document stated none. This is what the status may
    //: carry; it is never this process's clock, because this process did not take
    //: the sample and has no way to know how old the document is.
    std::string observed_at;
};

//: Map `snapshot_json` onto the observations `profile` grants. On failure
//: returns empty readings and sets `error`; on success every granted
//: observation gets exactly one Reading — PRESENT when the snapshot measures it
//: and every projected field survives validation, UNAVAILABLE / NOT_IMPLEMENTED
//: when the platform could but no collector block exists yet. A granted
//: observation is NEVER silently omitted, because omission and "measured nothing"
//: would then look the same to a Fleet reader.
//: `answered_elsewhere` names observations the CALLER is supplying from another
//: source, so this adapter does not emit a placeholder for them.
//:
//: WHY IT EXISTS. `build_status` takes the FIRST reading with a given name, so
//: two sources emitting the same observation is a precedence question that would
//: otherwise be settled by the order somebody happened to concatenate two
//: vectors in. `services` is the live case: it is a systemd fact, not a `/proc`
//: one, so it comes from the supervisor's unit adapter — and this adapter must
//: not also answer it with `NOT_IMPLEMENTED`.
//:
//: The "a granted observation is never silently omitted" rule is not weakened:
//: the omission is REQUESTED by the caller that is supplying the answer, and the
//: name has to be passed in for it to happen.
SnapshotReadings readings_from_snapshot(const Profile& profile, const std::string& snapshot_json,
                                        std::string& error,
                                        const std::vector<std::string>& answered_elsewhere = {});

}  // namespace nodecore::corelite
