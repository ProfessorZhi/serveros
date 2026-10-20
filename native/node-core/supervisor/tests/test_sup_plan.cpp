// The planner: order, refusals, and the four things it must never do.
//
// The planner is pure, so every case here states its own world: a manifest, a
// set of observations, one trigger, one instant. Nothing starts a process and
// nothing reads a clock, which is why "what would happen at boot with this
// fleet" is a question a test can ask rather than a scenario somebody has to
// stage on a machine.
#include <cstdio>
#include <string>
#include <vector>

#include "check.hpp"
#include "supervisor/plan.hpp"

using namespace nodecore::supervisor;

namespace {

std::string q(const std::string& value) { return "\"" + value + "\""; }

// A manifest with `services` given verbatim. observe_only defaults to false
// here, because most cases are about what WOULD be asked of systemd; the
// observe_only case turns it back on.
std::string manifest_json(const std::string& services, bool observe_only = false,
                          long generation = 7) {
    return "{" + q("schema") + ":" + q(kDesiredStateSchema) + "," + q("node") + ":" +
           q("device:x") + "," + q("principal") + ":" + q("vince") + "," + q("observe_only") +
           ":" + (observe_only ? "true" : "false") + "," + q("generation") + ":" +
           std::to_string(generation) + "," + q("services") + ":[" + services + "]}";
}

std::string service(const std::string& id, const std::string& unit,
                    const std::string& extra = "") {
    return "{" + q("id") + ":" + q(id) + "," + q("owner") + ":" + q("vince") + "," + q("unit") +
           ":" + q(unit) + "," + q("desired") + ":" + q("running") +
           (extra.empty() ? std::string() : "," + extra) + "}";
}

Manifest parsed(const std::string& text) {
    const ParseOutcome outcome = parse_manifest(text);
    if (!outcome.ok) {
        for (const Refusal& refusal : outcome.refusals) {
            std::fprintf(stderr, "fixture refused: %s %s: %s\n", refusal.scope.c_str(),
                         refusal.code.c_str(), refusal.reason.c_str());
        }
    }
    CHECK(outcome.ok);
    return outcome.manifest;
}

Observed observed(const std::string& unit, Active active, long age = 5) {
    Observed row;
    row.unit = unit;
    row.active = active;
    row.measured = true;
    row.age_seconds = age;
    row.source = "test";
    return row;
}

bool has_code(const Plan& plan, const std::string& code) {
    for (const Refusal& refusal : plan.refusals) {
        if (refusal.code == code) {
            return true;
        }
    }
    return false;
}

std::string refusal_for(const Plan& plan, const std::string& service_id) {
    for (const Refusal& refusal : plan.refusals) {
        if (refusal.scope == service_id) {
            return refusal.code;
        }
    }
    return "(none)";
}

std::string verbs_of(const Plan& plan) {
    std::string out;
    for (const Action& action : plan.actions) {
        out += action.service_id + ":" + action.verb + " ";
    }
    return out.empty() ? std::string("(none)") : out;
}

const std::vector<std::string> kAllowlist{"tunnel.service", "fleet.service", "cli.service"};

PlanInput input_for(const Manifest& manifest, Trigger trigger,
                    std::vector<Observed> rows = {}, const Recovery* recovery = nullptr,
                    long now = 1000) {
    PlanInput input;
    input.manifest = &manifest;
    input.trigger = trigger;
    input.now = now;
    input.allowed_units = kAllowlist;
    input.recovery = recovery;
    for (const Observed& row : rows) {
        input.observed[row.unit] = row;
    }
    return input;
}

}  // namespace

int main() {
    const std::string chain =
        service("tunnel", "tunnel.service", q("start_on_boot") + ":true") + "," +
        service("fleet", "fleet.service",
                q("start_on_boot") + ":true," + q("depends_on") + ":[" + q("tunnel") + "]") + "," +
        service("cli", "cli.service",
                q("start_on_boot") + ":false," + q("depends_on") + ":[" + q("fleet") + "]");
    const Manifest chain_manifest = parsed(manifest_json(chain));

    // ---------------------------------------------------- boot, in dependency order

    {
        const Plan plan = build_plan(input_for(chain_manifest, Trigger::Boot,
                                               {observed("tunnel.service", Active::Inactive),
                                                observed("fleet.service", Active::Inactive),
                                                observed("cli.service", Active::Inactive)}));
        CHECK(plan.ok);
        CHECK(plan.generation == 7);
        CHECK(!plan.manifest_sha256.empty());
        // The order is the dependency order, and `fleet` comes after `tunnel`
        // because it depends on it. This is the D1 -> D2 property: the plan is
        // the proof, and the manager is asked in this order or not at all.
        CHECK_EQ(plan.order.size(), std::size_t{3});
        CHECK_EQ(plan.order[0], std::string("tunnel"));
        CHECK_EQ(plan.order[1], std::string("fleet"));
        CHECK_EQ(plan.order[2], std::string("cli"));
        CHECK_EQ(plan.actions.size(), std::size_t{2});
        CHECK_EQ(plan.actions[0].service_id, std::string("tunnel"));
        CHECK_EQ(plan.actions[0].verb, std::string("start"));
        CHECK_EQ(plan.actions[1].service_id, std::string("fleet"));
        CHECK_EQ(refusal_for(plan, "cli"), std::string("NOT_START_ON_BOOT"));
    }

    // Same input, same plan. A planner that ordered by container iteration
    // would give a different answer per run, and a receipt could not be
    // compared with the plan it claims to have carried out.
    {
        const Plan first = build_plan(input_for(chain_manifest, Trigger::Boot,
                                                {observed("tunnel.service", Active::Inactive),
                                                 observed("fleet.service", Active::Inactive),
                                                 observed("cli.service", Active::Inactive)}));
        const Plan second = build_plan(input_for(chain_manifest, Trigger::Boot,
                                                 {observed("tunnel.service", Active::Inactive),
                                                  observed("fleet.service", Active::Inactive),
                                                  observed("cli.service", Active::Inactive)}));
        CHECK(first.order == second.order);
        CHECK_EQ(verbs_of(first), verbs_of(second));
    }

    // ------------------------------------------------------------- observe_only

    {
        const Manifest quiet = parsed(manifest_json(chain, /*observe_only=*/true));
        const Plan plan = build_plan(input_for(quiet, Trigger::Boot,
                                               {observed("tunnel.service", Active::Inactive),
                                                observed("fleet.service", Active::Inactive),
                                                observed("cli.service", Active::Inactive)}));
        CHECK(plan.ok);
        CHECK(plan.observe_only);
        // No actions at all, and the two that would have been taken are named
        // in the refusals rather than dropped.
        CHECK_EQ(plan.actions.size(), std::size_t{0});
        CHECK(has_code(plan, "OBSERVE_ONLY"));
        CHECK_EQ(refusal_for(plan, "tunnel"), std::string("OBSERVE_ONLY"));
    }

    // ------------------------------------------------------------------ cycles

    {
        const Manifest cyclic = parsed(manifest_json(
            service("a", "tunnel.service", q("depends_on") + ":[" + q("b") + "]") + "," +
            service("b", "fleet.service", q("depends_on") + ":[" + q("a") + "]")));
        const Plan plan = build_plan(input_for(cyclic, Trigger::Boot,
                                               {observed("tunnel.service", Active::Inactive),
                                                observed("fleet.service", Active::Inactive)}));
        CHECK(!plan.ok);
        CHECK_EQ(plan.actions.size(), std::size_t{0});
        CHECK(has_code(plan, "CYCLE"));
        CHECK_CONTAINS(plan.refusals[0].reason, "depend on each other in a loop");
    }

    // ------------------------------------------------- owners, units, and verbs

    {
        const Manifest other_owner = parsed(manifest_json(
            "{" + q("id") + ":" + q("a") + "," + q("owner") + ":" + q("fjd") + "," + q("unit") +
            ":" + q("tunnel.service") + "," + q("desired") + ":" + q("running") + "," +
            q("start_on_boot") + ":true}"));
        const Plan plan = build_plan(input_for(other_owner, Trigger::Boot,
                                               {observed("tunnel.service", Active::Inactive)}));
        CHECK(plan.ok);
        CHECK_EQ(plan.actions.size(), std::size_t{0});
        CHECK_EQ(refusal_for(plan, "a"), std::string("OWNER_MISMATCH"));
    }
    {
        const Manifest stray = parsed(manifest_json(service(
            "a", "something-else.service", q("start_on_boot") + ":true")));
        const Plan plan = build_plan(input_for(stray, Trigger::Boot,
                                               {observed("something-else.service", Active::Inactive)}));
        CHECK_EQ(plan.actions.size(), std::size_t{0});
        CHECK_EQ(refusal_for(plan, "a"), std::string("UNIT_NOT_ALLOWED"));
    }
    {
        // An empty allowlist refuses everything, which is the safe reading for a
        // caller that forgot to say what it is allowed to touch.
        PlanInput input = input_for(chain_manifest, Trigger::Boot);
        input.allowed_units.clear();
        const Plan plan = build_plan(input);
        CHECK_EQ(plan.actions.size(), std::size_t{0});
        CHECK(has_code(plan, "UNIT_NOT_ALLOWED"));
    }
    {
        const Manifest single = parsed(manifest_json(service(
            "cli", "cli.service",
            q("recovery") + ":{" + q("allowed_verbs") + ":[" + q("start") + "]}")));
        const Plan plan = build_plan(input_for(single, Trigger::Tick,
                                               {observed("cli.service", Active::Failed)}));
        CHECK_EQ(plan.actions.size(), std::size_t{0});
        CHECK_EQ(refusal_for(plan, "cli"), std::string("VERB_NOT_ALLOWED"));
        CHECK_CONTAINS(plan.refusals[0].reason, "not a licence to do anything");
    }
    {
        // A manifest that allows ONLY "stop" cannot make the Core stop
        // anything. The planner's verbs are start (boot) and restart (tick);
        // "stop" is not one of them, so a failed unit on a tick is refused as
        // VERB_NOT_ALLOWED rather than turned into a stop.
        const Manifest single = parsed(manifest_json(service(
            "stop-only", "tunnel.service",
            q("recovery") + ":{" + q("allowed_verbs") + ":[" + q("stop") + "]}")));
        const Plan plan = build_plan(input_for(single, Trigger::Tick,
                                               {observed("tunnel.service", Active::Failed)}));
        CHECK_EQ(plan.actions.size(), std::size_t{0});
        CHECK_EQ(refusal_for(plan, "stop-only"), std::string("VERB_NOT_ALLOWED"));
        CHECK_CONTAINS(plan.refusals[0].reason, "stop");
    }

    // ------------------------------------------- the four things it never does

    {
        // 1. An UNKNOWN is not a STOPPED.
        const Manifest single = parsed(manifest_json(service(
            "a", "tunnel.service", q("start_on_boot") + ":true")));
        const Plan plan = build_plan(
            input_for(single, Trigger::Boot, {observed("tunnel.service", Active::Unknown)}));
        CHECK_EQ(plan.actions.size(), std::size_t{0});
        CHECK_EQ(refusal_for(plan, "a"), std::string("UNKNOWN_IS_NOT_STOPPED"));
    }
    {
        // 1b. Nor is a unit nobody measured.
        const Manifest single = parsed(manifest_json(service(
            "a", "tunnel.service", q("start_on_boot") + ":true")));
        const Plan plan = build_plan(input_for(single, Trigger::Boot, {}));
        CHECK_EQ(plan.actions.size(), std::size_t{0});
        CHECK_EQ(refusal_for(plan, "a"), std::string("UNKNOWN_IS_NOT_STOPPED"));
    }
    {
        // 2. Stale evidence never triggers a start.
        const Manifest single = parsed(manifest_json(service(
            "a", "tunnel.service",
            q("start_on_boot") + ":true," + q("health") + ":{" + q("stale_after_seconds") + ":60}")));
        const Plan plan = build_plan(
            input_for(single, Trigger::Boot, {observed("tunnel.service", Active::Inactive, 300)}));
        CHECK_EQ(plan.actions.size(), std::size_t{0});
        CHECK_EQ(refusal_for(plan, "a"), std::string("STALE_HEALTH"));
        CHECK_CONTAINS(plan.refusals[0].reason, "absence of a reading");
    }
    {
        // 3. On a TICK, a plain inactive is left alone.
        const Manifest single = parsed(manifest_json(service("a", "tunnel.service")));
        const Plan plan = build_plan(
            input_for(single, Trigger::Tick, {observed("tunnel.service", Active::Inactive)}));
        CHECK_EQ(plan.actions.size(), std::size_t{0});
        CHECK_EQ(refusal_for(plan, "a"), std::string("INACTIVE_WITHOUT_FAILURE"));
    }
    {
        // 4. `manual` is owned by a person.
        const Manifest single = parsed(manifest_json(
            "{" + q("id") + ":" + q("a") + "," + q("owner") + ":" + q("vince") + "," + q("unit") +
            ":" + q("tunnel.service") + "," + q("desired") + ":" + q("manual") + "}"));
        const Plan plan = build_plan(
            input_for(single, Trigger::Boot, {observed("tunnel.service", Active::Inactive)}));
        CHECK_EQ(plan.actions.size(), std::size_t{0});
        CHECK_EQ(refusal_for(plan, "a"), std::string("DESIRED_MANUAL"));
    }

    // ------------------------------------------------------------ tick recovery

    {
        const Manifest single = parsed(manifest_json(service("a", "tunnel.service")));
        const Plan plan =
            build_plan(input_for(single, Trigger::Tick, {observed("tunnel.service", Active::Failed)}));
        CHECK_EQ(plan.actions.size(), std::size_t{1});
        CHECK_EQ(plan.actions[0].verb, std::string("restart"));
    }
    {
        const Manifest single = parsed(manifest_json(service("a", "tunnel.service")));
        const Plan plan = build_plan(
            input_for(single, Trigger::Tick, {observed("tunnel.service", Active::Activating)}));
        CHECK_EQ(plan.actions.size(), std::size_t{0});
        CHECK_EQ(refusal_for(plan, "a"), std::string("IN_TRANSITION"));
    }

    // -------------------------------------------------------- dependency gating

    {
        // `fleet` needs `tunnel`, and `tunnel` is manual — so nothing will bring
        // it up, and starting `fleet` would ask for a service whose input is not
        // there.
        const Manifest gated = parsed(manifest_json(
            "{" + q("id") + ":" + q("tunnel") + "," + q("owner") + ":" + q("vince") + "," +
            q("unit") + ":" + q("tunnel.service") + "," + q("desired") + ":" + q("manual") + "}," +
            service("fleet", "fleet.service",
                    q("start_on_boot") + ":true," + q("depends_on") + ":[" + q("tunnel") + "]")));
        const Plan plan = build_plan(input_for(gated, Trigger::Boot,
                                               {observed("tunnel.service", Active::Inactive),
                                                observed("fleet.service", Active::Inactive)}));
        CHECK_EQ(plan.actions.size(), std::size_t{0});
        CHECK_EQ(refusal_for(plan, "fleet"), std::string("DEPENDENCY_NOT_READY"));
    }
    {
        // A dependency already up satisfies the gate without being started.
        const Manifest chain2 = parsed(manifest_json(
            service("tunnel", "tunnel.service", q("start_on_boot") + ":true") + "," +
            service("fleet", "fleet.service",
                    q("start_on_boot") + ":true," + q("depends_on") + ":[" + q("tunnel") + "]")));
        const Plan plan = build_plan(input_for(chain2, Trigger::Boot,
                                               {observed("tunnel.service", Active::Active),
                                                observed("fleet.service", Active::Inactive)}));
        CHECK_EQ(plan.actions.size(), std::size_t{1});
        CHECK_EQ(plan.actions[0].service_id, std::string("fleet"));
    }

    // ------------------------------------------------------- the recovery gate

    {
        const Manifest single = parsed(manifest_json(service("a", "tunnel.service")));
        Recovery recovery;
        // Three attempts inside the window and the budget is spent. The default
        // policy permits three per hour (`model.hpp` / `runtime/daemon.py`).
        recovery.record_attempt("a", 900);
        recovery.record_attempt("a", 950);
        recovery.record_attempt("a", 990);
        const Plan plan = build_plan(input_for(single, Trigger::Tick,
                                               {observed("tunnel.service", Active::Failed)},
                                               &recovery, 1000));
        CHECK_EQ(plan.actions.size(), std::size_t{0});
        CHECK_EQ(refusal_for(plan, "a"), std::string("BUDGET_EXHAUSTED"));
        CHECK_CONTAINS(plan.refusals[0].reason, "it would be the loop");
    }

    return check::report("test_sup_plan");
}
