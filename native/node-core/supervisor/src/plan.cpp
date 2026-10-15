#include "supervisor/plan.hpp"

#include <algorithm>
#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace nodecore::supervisor {

std::string to_word(Trigger value) { return value == Trigger::Boot ? "boot" : "tick"; }

namespace {

const ServiceDesired* find_service(const Manifest& manifest, const std::string& id) {
    for (const ServiceDesired& service : manifest.services) {
        if (service.id == id) {
            return &service;
        }
    }
    return nullptr;
}

bool allows_verb(const ServiceDesired& service, const std::string& verb) {
    return std::find(service.recovery.allowed_verbs.begin(), service.recovery.allowed_verbs.end(),
                     verb) != service.recovery.allowed_verbs.end();
}

struct Ordering {
    std::vector<std::string> order;
    // Services Kahn's algorithm could not place, sorted. Non-empty means there
    // is a cycle somewhere in the residual set or feeding it.
    std::vector<std::string> cyclic;
};

// Kahn's algorithm over `depends_on`. The ready set is a std::set, so the
// order is deterministic: the same manifest yields the same plan, which is what
// lets a receipt be compared against the plan it came from.
Ordering topological_order(const Manifest& manifest) {
    std::map<std::string, int> indegree;
    for (const ServiceDesired& service : manifest.services) {
        indegree.emplace(service.id, 0);
    }
    for (const ServiceDesired& service : manifest.services) {
        for (const std::string& dependency : service.depends_on) {
            if (indegree.count(dependency) != 0 && dependency != service.id) {
                indegree[service.id] += 1;
            }
        }
    }

    std::set<std::string> ready;
    for (const ServiceDesired& service : manifest.services) {
        if (indegree[service.id] == 0) {
            ready.insert(service.id);
        }
    }

    Ordering ordering;
    while (!ready.empty()) {
        const std::string id = *ready.begin();
        ready.erase(ready.begin());
        ordering.order.push_back(id);
        for (const ServiceDesired& service : manifest.services) {
            if (service.id == id) {
                continue;
            }
            const bool depends_on_id =
                std::find(service.depends_on.begin(), service.depends_on.end(), id) !=
                service.depends_on.end();
            if (depends_on_id && --indegree[service.id] == 0) {
                ready.insert(service.id);
            }
        }
    }

    for (const ServiceDesired& service : manifest.services) {
        if (std::find(ordering.order.begin(), ordering.order.end(), service.id) ==
            ordering.order.end()) {
            ordering.cyclic.push_back(service.id);
        }
    }
    std::sort(ordering.cyclic.begin(), ordering.cyclic.end());
    return ordering;
}

std::string join(const std::vector<std::string>& values) {
    std::string out;
    for (const std::string& value : values) {
        if (!out.empty()) {
            out += ", ";
        }
        out += value;
    }
    return out;
}

}  // namespace

Plan build_plan(const PlanInput& input) {
    Plan plan;
    if (input.manifest == nullptr) {
        plan.ok = false;
        plan.refusals.push_back({"", "NO_MANIFEST",
                                 "no manifest was supplied, so there is nothing to plan against"});
        return plan;
    }
    const Manifest& manifest = *input.manifest;
    plan.generation = manifest.generation;
    plan.manifest_sha256 = manifest.sha256;
    plan.manifest_schema = manifest.schema;
    plan.observe_only = manifest.observe_only;
    plan.trigger = input.trigger;

    if (manifest.generation < input.applied_generation) {
        // EPOCH FENCE. Older than something already applied ⇒ the WHOLE
        // document is refused, not merely the services that differ. A manifest
        // that went backwards is not a newer statement about the fleet with a
        // few stale rows in it; it is an older statement, and carrying out any
        // part of it acts on a decision that has been superseded. Refusing
        // service-by-service would also make the outcome depend on which rows a
        // backup happened to contain.
        //
        // `plan.ok` stays TRUE: this is a plan (a reviewed refusal), the same
        // reading the per-service refusals above already use. A caller that
        // could not tell «I considered it and declined» from «there is no plan»
        // would have to refuse both.
        plan.refusals.push_back(
            {"", "STALE_GENERATION",
             "this manifest is generation " + std::to_string(manifest.generation) +
                 " and generation " + std::to_string(input.applied_generation) +
                 " has already been applied. A manifest older than one the Core has acted on is a "
                 "document that came back from somewhere — a restore, a copy, a stale checkout — "
                 "and carrying it out would re-authorise a plan that has been superseded. No "
                 "actions are planned from it"});
        return plan;
    }

    const Ordering ordering = topological_order(manifest);
    plan.order = ordering.order;
    if (!ordering.cyclic.empty()) {
        // A cycle has no valid order, so there is no partial plan to offer:
        // starting half of a cycle is starting something whose dependency
        // cannot be satisfied by construction.
        plan.ok = false;
        plan.refusals.push_back(
            {"", "CYCLE",
             "these services depend on each other in a loop, so no order exists in which to bring "
             "them up: " +
                 join(ordering.cyclic) + ". Adding another one to the manifest cannot fix this"});
        return plan;
    }

    const std::set<std::string> allowlist(input.allowed_units.begin(), input.allowed_units.end());
    // Services this plan intends to start, so that a dependent can tell
    // "already up" from "will be up by the time you need it" without the
    // planner having to invent a future observation.
    std::set<std::string> starting;

    for (const std::string& id : plan.order) {
        const ServiceDesired* service = find_service(manifest, id);
        if (service == nullptr) {
            continue;
        }

        if (service->owner != manifest.principal) {
            plan.refusals.push_back(
                {id, "OWNER_MISMATCH",
                 "owner is \"" + service->owner + "\" and this manifest is for \"" +
                     manifest.principal +
                     "\". A manifest that lists another account's unit is refused rather than "
                     "filtered, because a Core that quietly dropped it would leave a reader "
                     "believing it had been considered"});
            continue;
        }
        if (allowlist.find(service->unit) == allowlist.end()) {
            plan.refusals.push_back(
                {id, "UNIT_NOT_ALLOWED",
                 service->unit +
                     " is not in this caller's allowlist. The allowlist is exact unit ids, never a "
                     "pattern, so a unit that was renamed does not inherit the permission"});
            continue;
        }
        if (service->desired == Desired::Manual) {
            plan.refusals.push_back(
                {id, "DESIRED_MANUAL",
                 "desired is manual: a person owns this service's lifecycle, and a deliberate stop "
                 "and a crash read the same from outside, so the Core does not guess"});
            continue;
        }
        if (service->desired == Desired::Disabled) {
            plan.refusals.push_back({id, "DESIRED_DISABLED",
                                     "desired is disabled: the Core never starts this service"});
            continue;
        }

        Observed observed = Observed::unavailable(
            service->unit, "no observation was taken for this unit in this tick");
        const auto found = input.observed.find(service->unit);
        if (found != input.observed.end()) {
            observed = found->second;
        }

        if (!observed.measured || observed.active == Active::Unknown) {
            plan.refusals.push_back(
                {id, "UNKNOWN_IS_NOT_STOPPED",
                 observed.measured
                     ? "the unit was read as unknown. An unknown unit is not a stopped one, and "
                       "starting on an unknown is how a restart loop starts"
                     : "this unit was not measured at all: " +
                           (observed.reason.empty() ? std::string("no reason given")
                                                    : observed.reason)});
            continue;
        }
        if (observed.age_seconds < 0 ||
            observed.age_seconds > static_cast<long>(service->health.stale_after_seconds)) {
            plan.refusals.push_back(
                {id, "STALE_HEALTH",
                 "the last reading is " +
                     (observed.age_seconds < 0
                          ? std::string("of an uncomputable age")
                          : std::to_string(observed.age_seconds) + "s old") +
                     " and this service is stale after " +
                     std::to_string(service->health.stale_after_seconds) +
                     "s. Stale evidence is the absence of a reading, not a reading of a fault, so "
                     "it never triggers a start or a restart"});
            continue;
        }
        if (observed.active == Active::Active) {
            continue;  // at the state the manifest asks for; nothing to decide
        }
        if (observed.active == Active::Activating || observed.active == Active::Deactivating) {
            plan.refusals.push_back(
                {id, "IN_TRANSITION",
                 "the unit is " + to_word(observed.active) + ". Asking for a verb while the manager "
                 "is already moving it is how two transitions race"});
            continue;
        }

        std::string verb;
        if (input.trigger == Trigger::Boot) {
            if (!service->start_on_boot) {
                plan.refusals.push_back(
                    {id, "NOT_START_ON_BOOT",
                     "the unit is absent and this is a boot, but the manifest does not say "
                     "start_on_boot. Boot is the one moment absence is proven, and it is also the "
                     "one moment a start must have been written down in advance"});
                continue;
            }
            verb = "start";
        } else {
            if (observed.active != Active::Failed) {
                plan.refusals.push_back(
                    {id, "INACTIVE_WITHOUT_FAILURE",
                     "the unit is inactive on a tick, not failed. A person stopping a service and a "
                     "service never having started look identical here, and only a boot can tell "
                     "them apart, so a tick leaves it alone"});
                continue;
            }
            verb = "restart";
        }

        if (!allows_verb(*service, verb)) {
            plan.refusals.push_back(
                {id, "VERB_NOT_ALLOWED",
                 "\"" + verb + "\" is not in this service's allowed_verbs (" +
                     (service->recovery.allowed_verbs.empty()
                          ? std::string("empty: the Core may do nothing here")
                          : join(service->recovery.allowed_verbs)) +
                     "). Recovery is not a licence to do anything"});
            continue;
        }

        bool dependencies_ready = true;
        for (const std::string& dependency_id : service->depends_on) {
            if (starting.count(dependency_id) != 0) {
                continue;  // this plan brings it up earlier than this service
            }
            const ServiceDesired* dependency = find_service(manifest, dependency_id);
            if (dependency == nullptr) {
                dependencies_ready = false;
                break;
            }
            const auto dependency_observed = input.observed.find(dependency->unit);
            const bool up = dependency_observed != input.observed.end() &&
                            dependency_observed->second.measured &&
                            dependency_observed->second.active == Active::Active;
            if (!up) {
                dependencies_ready = false;
                break;
            }
        }
        if (!dependencies_ready) {
            plan.refusals.push_back(
                {id, "DEPENDENCY_NOT_READY",
                 "not every dependency of \"" + id +
                     "\" is up or being brought up earlier in this plan, so starting it would ask "
                     "the manager for a service whose inputs are not there yet"});
            continue;
        }

        if (input.recovery != nullptr) {
            const RecoveryDecision decision =
                input.recovery->may_act(id, service->recovery, input.now);
            if (!decision.allowed) {
                plan.refusals.push_back({id, decision.code, decision.reason});
                continue;
            }
        }

        plan.actions.push_back(
            Action{id, service->unit, verb,
                   input.trigger == Trigger::Boot
                       ? "absent at boot, desired running, start_on_boot set"
                       : "failed on a tick with desired running"});
        starting.insert(id);
    }

    if (plan.observe_only) {
        // The actions are DECIDED and not taken, and they are reported as
        // refusals rather than dropped: a reader of an observe-only receipt
        // should be able to see exactly what would have happened, which is the
        // entire value of running in this mode before turning it off.
        for (const Action& action : plan.actions) {
            plan.refusals.push_back(
                {action.service_id, "OBSERVE_ONLY",
                 "observe_only is on: would have asked systemd to " + action.verb + " " +
                     action.unit + " (" + action.reason + ")"});
        }
        plan.actions.clear();
    }

    return plan;
}

}  // namespace nodecore::supervisor
