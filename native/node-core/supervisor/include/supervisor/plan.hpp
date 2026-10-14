// The pure planner: what, if anything, should be asked of the service manager.
//
// This function decides and does nothing else. It starts no process, reads no
// file, looks at no clock of its own and holds no state between calls. Given
// one manifest, one set of observations, one trigger and one instant, it
// returns the SAME plan every time — which is what makes a plan reviewable
// before it is executed, and what lets a receipt be checked against the plan it
// claims to have carried out.
//
// THE RULES, IN THE ORDER THEY BIND
// ---------------------------------
//   1. A dependency CYCLE refuses the whole plan. There is no order to start
//      in, so there is no plan; naming the members is the whole output.
//   2. A service whose `owner` is not the manifest's `principal` is refused. Not
//      filtered — refused, so a reader sees it was considered.
//   3. A unit outside the caller's exact allowlist is refused. The provider
//      enforces the same list; this is the earlier of two gates, not the only
//      one.
//   4. `desired: manual` and `desired: disabled` are never acted on.
//   5. AN UNKNOWN IS NOT A STOPPED. A service that could not be read has no
//      verb, whatever the manifest wishes, because "I could not look" rendered
//      as "it is down" is the restart loop this product exists to avoid.
//   6. STALE EVIDENCE IS NOT EVIDENCE. A reading older than the service's own
//      window says nothing about now, so it never triggers a start or a
//      restart. It is not a failure; it is the absence of one.
//   7. AT BOOT, absence is proven and `start_on_boot` decides. ON A TICK, a
//      crash is what `failed` means and a restart is allowed; a plain
//      `inactive` is left alone, because a person stopping a service and a
//      service being absent look identical from here and only boot can tell
//      them apart.
//   8. The recovery budget and the breaker are asked LAST, and their refusal
//      wins over the action.
//   9. A dependent is started only when its dependencies are already up or are
//      being started earlier in the same plan.
//  10. `observe_only` produces the refusals and no actions at all.
#pragma once

#include <map>
#include <string>
#include <vector>

#include "supervisor/manifest.hpp"
#include "supervisor/model.hpp"
#include "supervisor/recovery.hpp"

namespace nodecore::supervisor {

enum class Trigger { Boot, Tick };
std::string to_word(Trigger value);

struct Action {
    std::string service_id;
    std::string unit;
    std::string verb;  // start | restart
    std::string reason;
};

struct PlanInput {
    const Manifest* manifest = nullptr;
    // Keyed by UNIT id, because that is the identity the service manager and
    // the allowlist both use. A manifest's service id is this component's own
    // name for the same thing.
    std::map<std::string, Observed> observed;
    Trigger trigger = Trigger::Tick;
    long now = 0;
    // The exact unit ids the provider will accept. Empty means nothing may be
    // acted on, which is the safe reading for a caller that forgot to say.
    std::vector<std::string> allowed_units;
    // May be null: a caller with no recovery state gets a plan with no budget
    // checks, which is a caller that will be told so by the missing refusals.
    const Recovery* recovery = nullptr;
};

struct Plan {
    // False only when the plan is not well formed — a cycle. Every other
    // refusal leaves `ok` true, because "I considered it and declined" is a
    // plan, and a caller that could not tell that from "there is no plan"
    // would have to refuse both.
    bool ok = true;
    long generation = 0;
    std::string manifest_sha256;
    std::string manifest_schema;
    bool observe_only = true;
    Trigger trigger = Trigger::Tick;
    // Every service id, in the order dependencies permit. Deterministic: ties
    // are broken by id, so the same manifest always yields the same order.
    std::vector<std::string> order;
    std::vector<Action> actions;
    std::vector<Refusal> refusals;
};

Plan build_plan(const PlanInput& input);

}  // namespace nodecore::supervisor
