// The `services` observation: what this account's declared units are doing,
// read and never touched.
//
// WHY THIS IS NOT `readings_from_snapshot`
// ----------------------------------------
// Everything the snapshot carries is a `/proc` fact. Service state is not: it
// lives in systemd, and the only supported way to ask is the supervisor's
// existing `systemd_user` adapter, whose `parse_systemctl_show` is a pure
// function of text. So this is a second, separate entry point that produces a
// `Reading` the same way — and it is separate precisely so that no second
// systemctl reader has to exist.
//
// WHY THE INTERFACE IS THIS NARROW
// --------------------------------
// `ServiceQuery` has ONE method. The supervisor's `Provider` has four —
// `query`, `start`, `restart`, `stop` — and a projection that was handed a
// `Provider` could call any of them. A type that cannot express "start a unit"
// cannot start one, so "Core-Lite never controls a service" is a property of the
// interface rather than a promise in a comment. Nothing in this file, or in
// anything reachable from it, can change what a unit is doing.
//
// WHAT IT REFUSES
// ---------------
//   * a service whose `owner` is not the account this process actually runs as.
//     It is not emitted AT ALL — not as UNAVAILABLE — for the same reason an
//     unauthorised observation is not: a field that is present and blank reads
//     as «measured nothing», and its presence alone discloses that the account
//     has a service there. The profile reader already refuses `owner !=
//     principal`; this is the second gate, on the RUNTIME account.
//   * a unit id `valid_unit_id` refuses. Checked here as well as in the reader.
//   * a query answer about a unit that is not the one asked for, or that is not
//     in this profile's allowlist. A source that answers about a different unit
//     must not be able to have it published under this one's name.
//   * a unit that is not INSTALLED. systemd answers `ActiveState=inactive` for a
//     unit that does not exist, so `INACTIVE` here would claim a stopped service
//     where there is no service. `NOT_INSTALLED` is a different word and is the
//     one that is true.
//   * the producer's own prose. A reason is mapped to the closed vocabulary, so
//     no text — and in particular no path — travels from systemctl into a status
//     that people who are not on this host will read.
#pragma once

#include <string>
#include <vector>

#include "corelite/identity.hpp"
#include "corelite/profile.hpp"
#include "corelite/status.hpp"
#include "supervisor/model.hpp"

namespace nodecore::corelite {

//: The only thing a service reading needs from the world. One question, no verb.
class ServiceQuery {
public:
    virtual ~ServiceQuery() = default;
    //: What systemd says about one unit. Never throws; an unreadable answer is
    //: an `Observed` with `measured = false` and a reason, as the supervisor's
    //: adapter already returns.
    virtual supervisor::Observed query(const std::string& unit) = 0;
};

//: The word a service reading carries when the unit is not installed at all.
//: Not `INACTIVE`, which would say the service exists and is stopped.
inline constexpr const char* kServiceNotInstalled = "NOT_INSTALLED";

//: One projected service. Minimal on purpose: what it is, what it is doing, when
//: that was read, and who said so.
struct ServiceReading {
    std::string id;    // the profile's own id for this service
    std::string unit;  // the unit id, verbatim
    //: One of the closed state words below. Only meaningful when `measured`.
    std::string state;
    //: `systemd`'s SubState, verbatim ("dead", "running", …). Empty when the
    //: answer did not carry one.
    std::string sub_state;
    //: The instant the reading was taken, copied from the observation.
    std::string observed_at;
    //: How old that was when it was read, or -1 when it could not be computed.
    long age_seconds = -1;
    //: "systemd-user", "fixture", … A reader has to be able to tell a reading of
    //: a host from one that was handed in.
    std::string source;
    bool measured = false;
    //: When `!measured`, one of the closed words in `kReasons`.
    std::string reason;
};

//: The closed state words a measured service can carry: the supervisor's own
//: `Active` vocabulary in its own spelling, plus `NOT_INSTALLED`.
extern const char* const kServiceStates[7];

//: Project the profile's services. `os` is the RUNTIME account readback, not the
//: profile's claim: a profile cannot make another account's service this
//: process's business.
Reading services_reading(const Profile& profile, const OsIdentity& os, ServiceQuery& query,
                         std::string& error);

}  // namespace nodecore::corelite
