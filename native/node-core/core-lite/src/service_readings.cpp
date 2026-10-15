// The `services` observation. See `service_readings.hpp` for the rules.
#include "corelite/service_readings.hpp"

#include <string>
#include <vector>

#include "supervisor/manifest.hpp"
#include "supervisor/provider.hpp"

namespace nodecore::corelite {

namespace {

using nodecore::supervisor::Active;
using nodecore::supervisor::Observed;
using nodecore::supervisor::UnitAllowlist;

const char* const kServiceStates[7] = {"ACTIVE",  "INACTIVE", "FAILED",
                                       "ACTIVATING", "DEACTIVATING", "UNKNOWN",
                                       kServiceNotInstalled};

//: The supervisor's `Active` in this document's spelling. Upper-case because
//: every other closed vocabulary in the status is.
std::string state_word(Active active) {
    switch (active) {
        case Active::Active:
            return "ACTIVE";
        case Active::Inactive:
            return "INACTIVE";
        case Active::Failed:
            return "FAILED";
        case Active::Activating:
            return "ACTIVATING";
        case Active::Deactivating:
            return "DEACTIVATING";
        case Active::Unknown:
            // Reachable only if a caller bypasses `parse_systemctl_show`, which
            // refuses an unknown ActiveState with `measured = false`. A measured
            // reading never carries this word, and the projection below refuses
            // it rather than publishing it.
            return "UNKNOWN";
    }
    return "UNKNOWN";
}

//: One projected service as the document carries it. Every key is named here
//: and nothing else can appear: this is a projection, not a pass-through of
//: whatever the observation happened to hold.
nodecore::Json service_row(const ServiceReading& reading) {
    nodecore::Json row = nodecore::Json::object();
    row.set("id", nodecore::Json::string(reading.id));
    row.set("unit", nodecore::Json::string(reading.unit));
    if (!reading.measured) {
        // A service whose state could not be read carries the closed reason and
        // NO state word: "UNKNOWN" beside a reason would be a second vocabulary
        // word saying the same thing.
        row.set("state", nodecore::Json::string("UNAVAILABLE"));
        row.set("reason", nodecore::Json::string(reading.reason));
        return row;
    }
    row.set("state", nodecore::Json::string(reading.state));
    if (!reading.sub_state.empty()) {
        row.set("sub_state", nodecore::Json::string(reading.sub_state));
    }
    if (!reading.observed_at.empty()) {
        row.set("observed_at", nodecore::Json::string(reading.observed_at));
    }
    if (reading.age_seconds >= 0) {
        row.set("age_seconds", nodecore::Json::integer(reading.age_seconds));
    }
    if (!reading.source.empty()) {
        row.set("source", nodecore::Json::string(reading.source));
    }
    return row;
}

ServiceReading unmeasured(const ServiceEntry& entry, const std::string& reason) {
    ServiceReading reading;
    reading.id = entry.id;
    reading.unit = entry.unit;
    reading.state = "UNKNOWN";
    reading.measured = false;
    reading.reason = reason;
    return reading;
}

}  // namespace

Reading services_reading(const Profile& profile, const OsIdentity& os, ServiceQuery& query,
                         std::string& error) {
    error.clear();
    Reading reading;
    reading.name = "services";
    reading.source = "systemd-user";

    // Without a resolved account there is no owner to compare against, so there
    // is no service this process is entitled to look at. Refused rather than
    // guessed, for the same reason the device declaration is.
    if (!os.known) {
        reading.state = ReadingState::Unavailable;
        reading.reason = "IDENTITY_UNKNOWN";
        return reading;
    }

    // The allowlist is built from the profile's OWN declared units: the list of
    // what this Core may name is the list it was granted, and a query answer
    // about anything else is refused rather than published.
    std::vector<std::string> declared;
    for (const ServiceEntry& entry : profile.services) {
        declared.push_back(entry.unit);
    }
    const auto built = UnitAllowlist::build(declared);
    if (!built.ok) {
        // A declared unit that is not a unit id. The profile reader refuses this
        // too; reaching here means a caller built a `Profile` by hand.
        error = "the profile declares a unit that is not a unit id: " + built.error;
        reading.state = ReadingState::Unavailable;
        reading.reason = "NO_SOURCE";
        return reading;
    }

    nodecore::Json services = nodecore::Json::array();
    bool any_measured = false;
    bool all_measured = true;
    bool anything_this_account_may_look_at = false;

    for (const ServiceEntry& entry : profile.services) {
        // THE OWNER GATE, on the RUNTIME account. A service belonging to another
        // account is not emitted at all — not as UNAVAILABLE, because its
        // presence would disclose that the account has one there, and because
        // "present and blank" reads as «measured nothing». Same rule as an
        // ungranted observation.
        if (entry.owner != os.user) {
            continue;
        }
        anything_this_account_may_look_at = true;

        // Belt and braces: the reader checked this, and this is the function that
        // turns the id into a query. Two gates, because this one is enforcement.
        if (!nodecore::supervisor::valid_unit_id(entry.unit)) {
            services.push(service_row(unmeasured(entry, "NO_SOURCE")));
            all_measured = false;
            continue;
        }

        const Observed observed = query.query(entry.unit);

        // A source that answered about a DIFFERENT unit, or about a unit this
        // profile never declared, is not allowed to have its answer published
        // under this entry's name. Refused, not relabelled.
        if (observed.unit != entry.unit || !built.allowlist.permits(observed.unit)) {
            services.push(service_row(unmeasured(entry, "NO_SOURCE")));
            all_measured = false;
            continue;
        }

        if (!observed.measured) {
            services.push(service_row(unmeasured(entry, "NO_SOURCE")));
            all_measured = false;
            continue;
        }

        // IS THE THING EVEN THERE? systemd answers `ActiveState=inactive` for a
        // unit name it cannot find, so reporting INACTIVE here would state that a
        // service exists and is stopped when in fact there is no service. The
        // tri-state refuses when the load state was not read, which is also not
        // "installed".
        const std::optional<bool> installed = nodecore::supervisor::unit_is_installed(observed);
        if (!installed.has_value()) {
            services.push(service_row(unmeasured(entry, "NO_SOURCE")));
            all_measured = false;
            continue;
        }
        if (!*installed) {
            ServiceReading not_installed;
            not_installed.id = entry.id;
            not_installed.unit = entry.unit;
            not_installed.state = kServiceNotInstalled;
            not_installed.sub_state = observed.sub_state;
            not_installed.observed_at = observed.observed_at;
            not_installed.age_seconds = observed.age_seconds;
            not_installed.source = observed.source;
            not_installed.measured = true;
            services.push(service_row(not_installed));
            any_measured = true;
            continue;
        }

        // A measured reading with no state word is a contradiction: the parser
        // refuses an unknown ActiveState with measured = false, so a measured
        // Unknown means the reading did not come from that parser.
        if (observed.active == Active::Unknown) {
            services.push(service_row(unmeasured(entry, "NO_SOURCE")));
            all_measured = false;
            continue;
        }

        ServiceReading projected;
        projected.id = entry.id;
        projected.unit = entry.unit;
        projected.state = state_word(observed.active);
        projected.sub_state = observed.sub_state;
        projected.observed_at = observed.observed_at;
        projected.age_seconds = observed.age_seconds;
        projected.source = observed.source;
        projected.measured = true;
        services.push(service_row(projected));
        any_measured = true;
    }

    if (!anything_this_account_may_look_at) {
        // Either the profile declares no services at all — a legitimate "this
        // account runs none" — or it declares only other accounts'. The two are
        // NOT the same answer and must not both read as an empty list.
        if (profile.services.empty()) {
            nodecore::Json value = nodecore::Json::object();
            value.set("services", services);
            reading.state = ReadingState::Present;
            reading.value = value;
            reading.completeness = kComplete;
            reading.has_value = true;
            return reading;
        }
        // Declared, and none of it is this account's. An empty list would claim
        // this account runs no services, which is false; the truth is that
        // nothing here is ours to look at.
        reading.state = ReadingState::Unavailable;
        reading.reason = "IDENTITY_UNKNOWN";
        return reading;
    }

    if (!any_measured) {
        // Every declared service's state could not be read. NOT an empty list,
        // and NOT "none running".
        reading.state = ReadingState::Unavailable;
        reading.reason = "NO_SOURCE";
        return reading;
    }

    nodecore::Json value = nodecore::Json::object();
    value.set("services", services);
    reading.state = ReadingState::Present;
    // Per #215: a partly-measured value says so rather than being thrown away or
    // read as complete. One service readable and another not is exactly that
    // case, and the unreadable one is an explicit UNAVAILABLE in place.
    reading.completeness = all_measured ? kComplete : kPartial;
    reading.value = value;
    reading.has_value = true;
    return reading;
}

}  // namespace nodecore::corelite
