// The budget, the ladder and the breaker — stated as instants, never as sleeps.
//
// Every case here would be a timing experiment if the clock were not injected.
// It is, so `t = 0` and `t = 3600` are values a reader can check by hand, and a
// decision that depended on when the test ran could not exist.
#include <string>
#include <vector>

#include "check.hpp"
#include "json_detail.hpp"
#include "supervisor/recovery.hpp"

using namespace nodecore::supervisor;
using nodecore::supervisor::detail::Json;
using nodecore::supervisor::detail::Reader;

namespace {

RecoveryPolicy policy_with(int max_attempts = 3, int window = 3600,
                           std::vector<int> ladder = {60, 300, 900}, int breaker = 3,
                           int cooldown = 1800) {
    RecoveryPolicy policy;
    policy.max_attempts = max_attempts;
    policy.window_seconds = window;
    policy.backoff_seconds = std::move(ladder);
    policy.circuit.consecutive_failures = breaker;
    policy.circuit.cooldown_seconds = cooldown;
    return policy;
}

std::string reasons_of(const RecoveryDecision& decision) { return decision.code + ": " + decision.reason; }

//: A clock reading a test can state: a steady-clock instant, the wall-clock
//: epoch at that instant, and which boot owns the monotonic one.
RecoveryClock clock_at(long monotonic, long long wall_epoch, const std::string& boot_id) {
    RecoveryClock clock;
    clock.monotonic = monotonic;
    clock.wall_epoch = wall_epoch;
    clock.boot_id = boot_id;
    return clock;
}

}  // namespace

int main() {
    const RecoveryPolicy policy = policy_with();

    // ----------------------------------------------------------- a clean slate

    {
        const Recovery state;
        const RecoveryDecision decision = state.may_act("a", policy, 0);
        CHECK(decision.allowed);
        CHECK_EQ(decision.code, std::string("ALLOWED"));
    }

    // --------------------------------------------------------- the window budget

    {
        Recovery state;
        state.record_attempt("a", 0);
        state.record_attempt("a", 10);
        const RecoveryDecision second = state.may_act("a", policy, 400);
        // ladder[1] is 300 and the last attempt was at 10, so 400 is late enough.
        CHECK(second.allowed);
        state.record_attempt("a", 400);
        // Asked at t=1000, all three attempts are inside the 3600 s window, so
        // the third is refused. Asked at t=4000 the earliest would be exactly on
        // the boundary and pruned — the budget would read as fresh, which is the
        // arithmetic that made this case fail the first time it ran.
        const RecoveryDecision third = state.may_act("a", policy, 1000);
        CHECK(!third.allowed);
        CHECK_EQ(third.code, std::string("BUDGET_EXHAUSTED"));
        CHECK_EQ(third.retry_after_seconds, 2600L);  // 3600 - (1000 - 0)
        CHECK_CONTAINS(reasons_of(third), "it would be the loop");
    }
    {
        // The window PRUNES. A service that failed three times an hour ago is
        // not permanently barred, which is the reading a budget without a
        // window would produce.
        Recovery state;
        state.record_attempt("a", 0);
        state.record_attempt("a", 1);
        state.record_attempt("a", 2);
        CHECK(!state.may_act("a", policy, 10).allowed);
        const RecoveryDecision later = state.may_act("a", policy, 5000);
        CHECK(later.allowed);
        CHECK_EQ(state.attempts("a").size(), std::size_t{3});  // kept, not erased
    }

    // ------------------------------------------------------------ the ladder

    {
        Recovery state;
        state.record_attempt("a", 0);
        const RecoveryDecision too_soon = state.may_act("a", policy, 10);
        CHECK(!too_soon.allowed);
        CHECK_EQ(too_soon.code, std::string("BACKOFF"));
        // ladder[0] is 60 and ten seconds have passed.
        CHECK_EQ(too_soon.retry_after_seconds, 50L);

        CHECK(state.may_act("a", policy, 60).allowed);

        // The second wait is ladder[1] = 300, measured from the LAST attempt.
        state.record_attempt("a", 60);
        const RecoveryDecision second = state.may_act("a", policy, 100);
        CHECK(!second.allowed);
        CHECK_EQ(second.retry_after_seconds, 260L);
    }
    {
        // Past the end of the ladder it is clamped to the last rung rather than
        // read off the end. A ladder that ran out would otherwise start over at
        // the shortest wait, which is the storm the ladder exists to prevent.
        Recovery state;
        const RecoveryPolicy short_ladder = policy_with(5, 3600, {60});
        state.record_attempt("a", 0);
        state.record_attempt("a", 100);
        const RecoveryDecision decision = state.may_act("a", short_ladder, 120);
        CHECK(!decision.allowed);
        CHECK_EQ(decision.retry_after_seconds, 40L);  // still waiting 60 from t=100
    }

    // ------------------------------------------------------- the circuit breaker

    {
        Recovery state;
        const RecoveryPolicy breaker = policy_with(5, 3600, {60, 300, 900}, 2, 1800);
        state.record_failure("a", breaker, 100);
        CHECK(!state.circuit_open("a", 100));
        CHECK_EQ(state.consecutive_failures("a"), 1);
        state.record_failure("a", breaker, 120);
        CHECK(state.circuit_open("a", 120));
        CHECK_EQ(state.consecutive_failures("a"), 2);

        const RecoveryDecision open = state.may_act("a", breaker, 130);
        CHECK(!open.allowed);
        CHECK_EQ(open.code, std::string("CIRCUIT_OPEN"));
        CHECK_EQ(open.retry_after_seconds, 1790L);
        CHECK_CONTAINS(reasons_of(open), "not one more restart away from working");

        // The cooldown ends and the service is eligible again — the breaker is
        // a pause, not a permanent ban. A ban nobody can lift is how a service
        // stays down until a person notices, which is the opposite of the job.
        CHECK(!state.circuit_open("a", 1920));
        CHECK(state.may_act("a", breaker, 1920).allowed);
    }
    {
        // A success clears the BREAKER and not the window. A service that
        // failed twice and then worked is still a service that needed two
        // attempts this hour.
        Recovery state;
        const RecoveryPolicy breaker = policy_with(3, 3600, {60, 300, 900}, 2, 1800);
        state.record_attempt("a", 10);
        state.record_attempt("a", 20);
        state.record_failure("a", breaker, 20);
        state.record_failure("a", breaker, 21);
        CHECK(state.circuit_open("a", 30));
        state.record_success("a");
        CHECK(!state.circuit_open("a", 30));
        CHECK_EQ(state.consecutive_failures("a"), 0);
        CHECK_EQ(state.attempts("a").size(), std::size_t{2});
        // Still inside the ladder from t=20: 30-20 = 10 < 300.
        CHECK_EQ(state.may_act("a", breaker, 30).code, std::string("BACKOFF"));
    }

    // --------------------------------------------------- what a probe taught us

    {
        // The bridge from the readiness rule to the budget. This is the mapping
        // the wiring in `main.cpp` uses, tested where it can be stated without
        // a service manager: a provider failure is a failed recovery, a READY
        // probe is a recovery, and an UNMEASURED one is neither.
        CHECK(outcome_for(true, Readiness::Ready) == ActionOutcome::Recovered);
        CHECK(outcome_for(true, Readiness::NotReady) == ActionOutcome::NotRecovered);
        CHECK(outcome_for(true, Readiness::Unmeasured) == ActionOutcome::Unmeasured);
        CHECK(outcome_for(false, Readiness::Ready) == ActionOutcome::NotRecovered);
    }
    {
        // An UNMEASURED reprobe is not a failed recovery. The attempt was made,
        // so it stands; the consecutive-failure count and the breaker are
        // untouched, because "we could not look" is not evidence either way.
        Recovery state;
        const RecoveryPolicy breaker = policy_with(3, 3600, {60, 300, 900}, 2, 1800);
        state.record_attempt("a", 10);
        state.record_outcome("a", ActionOutcome::Unmeasured, breaker, 10);
        CHECK_EQ(state.consecutive_failures("a"), 0);
        CHECK(!state.circuit_open("a", 10));
        CHECK_EQ(state.attempts("a").size(), std::size_t{1});
    }
    {
        // It neither adds to a failure that is already there nor clears one. A
        // failure followed by an unmeasured probe is still one failure, and an
        // OPEN breaker stays open: only a READY probe may clear it.
        Recovery state;
        const RecoveryPolicy breaker = policy_with(3, 3600, {60, 300, 900}, 2, 1800);
        state.record_attempt("a", 10);
        state.record_outcome("a", ActionOutcome::NotRecovered, breaker, 10);
        CHECK_EQ(state.consecutive_failures("a"), 1);
        state.record_outcome("a", ActionOutcome::Unmeasured, breaker, 11);
        CHECK_EQ(state.consecutive_failures("a"), 1);
        CHECK(!state.circuit_open("a", 11));
        state.record_outcome("a", ActionOutcome::NotRecovered, breaker, 12);
        CHECK(state.circuit_open("a", 12));
        state.record_outcome("a", ActionOutcome::Unmeasured, breaker, 13);
        CHECK(state.circuit_open("a", 13));
        CHECK_EQ(state.consecutive_failures("a"), 2);
    }
    {
        // The two verdicts that DO decide something: a recovery clears the
        // breaker, and a failed recovery advances it by exactly one.
        Recovery state;
        const RecoveryPolicy breaker = policy_with(3, 3600, {60, 300, 900}, 1, 1800);
        state.record_failure("a", breaker, 10);
        CHECK(state.circuit_open("a", 10));
        state.record_outcome("a", ActionOutcome::Recovered, breaker, 11);
        CHECK(!state.circuit_open("a", 11));
        CHECK_EQ(state.consecutive_failures("a"), 0);
        state.record_outcome("a", ActionOutcome::NotRecovered, breaker, 12);
        CHECK_EQ(state.consecutive_failures("a"), 1);
        CHECK(state.circuit_open("a", 12));
    }

    // -------------------------------------------------------- the persisted form

    {
        Recovery state;
        const RecoveryPolicy breaker = policy_with(3, 3600, {60, 300, 900}, 2, 1800);
        state.record_attempt("a", 10);
        state.record_failure("a", breaker, 10);
        state.record_failure("a", breaker, 11);
        state.record_attempt("b", 12);

        const RecoveryClock written = clock_at(12, 1770000000, "boot-a");
        const std::string text = state.to_json(written);
        // No key, token, secret, password or credential appears in the state
        // this program writes. The Core holds no signing key, and a state file
        // that carried one would be the leak the acceptance list forbids.
        for (const char* forbidden : {"key", "token", "secret", "password", "credential", "passwd"}) {
            CHECK(text.find(forbidden) == std::string::npos);
        }

        const RecoveryLoaded loaded = load_recovery(&text, written);
        CHECK(loaded.ok);
        CHECK(loaded.was_present);
        CHECK(!loaded.legacy);
        CHECK_EQ(loaded.state.attempts("a").size(), std::size_t{1});
        CHECK_EQ(loaded.state.consecutive_failures("a"), 2);
        CHECK(loaded.state.circuit_open("a", 12));
        CHECK_EQ(loaded.state.attempts("b").size(), std::size_t{1});
    }
    {
        // The persisted document separates the two clocks: the anchor carries
        // the wall epoch AND the steady instant, while the attempts remain
        // steady-clock seconds. A reader can see which one is which.
        Recovery state;
        state.record_attempt("a", 10);
        const std::string text = state.to_json(clock_at(42, 1770000000, "boot-a"));
        Json document;
        std::string error;
        Reader reader(text);
        CHECK(reader.parse(document, error));
        if (document.is_object()) {
            const Json* schema = document.find("schema");
            CHECK(schema != nullptr && schema->is_string() &&
                  schema->text == kRecoverySchemaV2);
            const Json* boot = document.find("boot_id");
            CHECK(boot != nullptr && boot->is_string() && boot->text == "boot-a");
            const Json* anchor = document.find("anchor");
            CHECK(anchor != nullptr && anchor->is_object());
            if (anchor != nullptr && anchor->is_object()) {
                const Json* mono = anchor->find("monotonic");
                const Json* wall = anchor->find("wall_epoch");
                CHECK(mono != nullptr && mono->is_int() && mono->integer == 42);
                CHECK(wall != nullptr && wall->is_int() && wall->integer == 1770000000);
            }
            const Json* services = document.find("services");
            CHECK(services != nullptr && services->is_array() &&
                  services->items.size() == 1);
            if (services != nullptr && services->is_array() && services->items.size() == 1) {
                const Json* attempts = services->items[0].find("attempts");
                CHECK(attempts != nullptr && attempts->is_array() &&
                      attempts->items.size() == 1);
                if (attempts != nullptr && attempts->is_array() && attempts->items.size() == 1) {
                    CHECK(attempts->items[0].is_int() &&
                          attempts->items[0].integer == 10);
                }
            }
        }
    }
    {
        // No file at all is a first run, and it is a DIFFERENT answer from a
        // file that could not be read.
        const RecoveryLoaded absent = load_recovery(nullptr, clock_at(0, 1770000000, "boot-a"));
        CHECK(absent.ok);
        CHECK(!absent.was_present);
        CHECK(!absent.legacy);
    }
    {
        // A corrupt state file must NOT load as an empty one: that would reset
        // the budget silently, and a budget that resets is not a budget.
        const std::string corrupt = "{\"schema\":\"serveros/native-core-recovery.v2\",";
        const RecoveryLoaded loaded = load_recovery(&corrupt, clock_at(0, 1770000000, "boot-a"));
        CHECK(!loaded.ok);
        CHECK(!loaded.was_present);
        CHECK(!loaded.error.empty());
    }
    {
        const std::string wrong_schema = "{\"schema\":\"something/else.v1\",\"services\":[]}";
        const RecoveryLoaded loaded =
            load_recovery(&wrong_schema, clock_at(0, 1770000000, "boot-a"));
        CHECK(!loaded.ok);
        CHECK_CONTAINS(loaded.error, "native-core-recovery");
    }
    {
        const std::string typo =
            "{\"schema\":\"serveros/native-core-recovery.v2\",\"boot_id\":\"boot-a\","
            "\"anchor\":{\"monotonic\":1,\"wall_epoch\":1770000000},\"servies\":[]}";
        const RecoveryLoaded loaded = load_recovery(&typo, clock_at(2, 1770000000, "boot-a"));
        CHECK(!loaded.ok);
        CHECK_CONTAINS(loaded.error, "no services array");
    }

    // ----------------------------------------------- reboot and clock-epoch
    //
    // The monotonic clock is the authority WITHIN a boot; the wall clock is
    // only a bridge BETWEEN boots. Each case below is a direction a clock can
    // move, and the property is always: the budget is preserved unless the
    // wall clock proves a full window has actually elapsed.

    {
        // Same boot, wall clock steps BACKWARDS. The stored steady stamps are
        // used directly, so the step cannot age them out and reset the budget.
        Recovery state;
        const RecoveryPolicy breaker = policy_with(3, 3600, {60, 300, 900}, 3, 1800);
        state.record_attempt("a", 10);
        state.record_attempt("a", 20);
        state.record_attempt("a", 30);
        const std::string text = state.to_json(clock_at(100, 1770000000, "boot-a"));
        const RecoveryLoaded loaded =
            load_recovery(&text, clock_at(200, 1769996400, "boot-a"));
        CHECK(loaded.ok);
        CHECK_EQ(loaded.state.attempts("a").size(), std::size_t{3});
        CHECK_EQ(loaded.state.may_act("a", breaker, 200).code,
                 std::string("BUDGET_EXHAUSTED"));
    }
    {
        // New boot, wall clock moves FORWARD. The old steady stamps are placed
        // in THIS boot's steady domain, so they are in the past rather than in
        // the future. One attempt, 30 s before now, is still inside the ladder.
        Recovery state;
        const RecoveryPolicy breaker = policy_with(3, 3600, {60, 300, 900}, 3, 1800);
        state.record_attempt("a", 990);
        const std::string text = state.to_json(clock_at(1000, 1770000000, "boot-a"));
        const RecoveryLoaded loaded =
            load_recovery(&text, clock_at(10, 1770000020, "boot-b"));
        CHECK(loaded.ok);
        const std::vector<long> converted = loaded.state.attempts("a");
        CHECK_EQ(converted.size(), std::size_t{1});
        CHECK(converted[0] <= 10L);  // in the past; the old 990 would be future
        const RecoveryDecision decision = loaded.state.may_act("a", breaker, 10);
        CHECK_EQ(decision.code, std::string("BACKOFF"));
        CHECK_EQ(decision.retry_after_seconds, 30L);  // 60 - (10 - (-20))
    }
    {
        // New boot, wall clock forward by MORE than the window. The attempts
        // are genuinely old, so the window prunes them and the budget is fresh.
        // This is the one direction where aging out is correct, and it is not
        // a rollback: the Core waited out a full window.
        Recovery state;
        const RecoveryPolicy breaker = policy_with(3, 3600, {60, 300, 900}, 3, 1800);
        state.record_attempt("a", 900);
        state.record_attempt("a", 950);
        state.record_attempt("a", 990);
        const std::string text = state.to_json(clock_at(1000, 1770000000, "boot-a"));
        const RecoveryLoaded loaded =
            load_recovery(&text, clock_at(10, 1770007200, "boot-b"));  // +7200 s
        CHECK(loaded.ok);
        CHECK(loaded.state.may_act("a", breaker, 10).allowed);
    }
    {
        // New boot, wall clock steps BACKWARDS. Negative elapsed is clamped to
        // zero, so every attempt is treated as "now": the budget is preserved,
        // not reset. This is the anti-expansion direction.
        Recovery state;
        const RecoveryPolicy breaker = policy_with(3, 3600, {60, 300, 900}, 3, 1800);
        state.record_attempt("a", 900);
        state.record_attempt("a", 950);
        state.record_attempt("a", 990);
        const std::string text = state.to_json(clock_at(1000, 1770000000, "boot-a"));
        const RecoveryLoaded loaded =
            load_recovery(&text, clock_at(10, 1769999000, "boot-b"));
        CHECK(loaded.ok);
        CHECK_EQ(loaded.state.attempts("a").size(), std::size_t{3});
        // No stamp is in the future: the clamp placed every attempt at "now",
        // not at a negative elapsed delta.
        for (long stamp : loaded.state.attempts("a")) {
            CHECK(stamp <= 10L);
        }
        CHECK_EQ(loaded.state.may_act("a", breaker, 10).code,
                 std::string("BUDGET_EXHAUSTED"));
    }
    {
        // New boot, wall clock backwards, breaker OPEN: the remaining cooldown
        // is preserved (and grows by the backward step), never cleared.
        Recovery state;
        const RecoveryPolicy breaker = policy_with(5, 3600, {60, 300, 900}, 1, 1800);
        state.record_failure("a", breaker, 1000);  // opened_until = 2800
        const std::string text = state.to_json(clock_at(1000, 1770000000, "boot-a"));
        const RecoveryLoaded loaded =
            load_recovery(&text, clock_at(10, 1769999000, "boot-b"));
        CHECK(loaded.ok);
        CHECK(loaded.state.circuit_open("a", 10));
        CHECK_EQ(loaded.state.consecutive_failures("a"), 1);
        const RecoveryDecision decision = loaded.state.may_act("a", breaker, 10);
        CHECK_EQ(decision.code, std::string("CIRCUIT_OPEN"));
        CHECK_EQ(decision.retry_after_seconds, 2800L);
    }
    {
        // Legacy v1 is REFUSED, not migrated. It has no boot/clock anchor, so
        // guessing which boot its monotonic stamps belong to is exactly the bug
        // this format exists to remove. The file is left for a person.
        const std::string legacy =
            "{\"schema\":\"serveros/native-core-recovery.v1\",\"services\":[{\"id\":\"a\","
            "\"consecutive_failures\":3,\"opened_until\":9999,\"attempts\":[1,2,3]}]}";
        const RecoveryLoaded loaded =
            load_recovery(&legacy, clock_at(100, 1770000000, "boot-a"));
        CHECK(!loaded.ok);
        CHECK(loaded.was_present);
        CHECK(loaded.legacy);
        CHECK_CONTAINS(loaded.error, "v1");
        CHECK_CONTAINS(loaded.error, "Refusing");
    }
    {
        // A v2 state whose own anchor is in the future for a same-boot load is
        // inconsistent or copied; refuse rather than place it in a guessed
        // domain.
        const std::string future =
            "{\"schema\":\"serveros/native-core-recovery.v2\",\"boot_id\":\"boot-a\","
            "\"anchor\":{\"monotonic\":200,\"wall_epoch\":1770000000},"
            "\"services\":[{\"id\":\"a\",\"consecutive_failures\":0,\"opened_until\":0,"
            "\"attempts\":[100]}]}";
        const RecoveryLoaded loaded =
            load_recovery(&future, clock_at(150, 1770000050, "boot-a"));
        CHECK(!loaded.ok);
        CHECK_CONTAINS(loaded.error, "future");
    }
    {
        // A v2 state with no boot id is not placeable.
        const std::string no_boot =
            "{\"schema\":\"serveros/native-core-recovery.v2\","
            "\"anchor\":{\"monotonic\":100,\"wall_epoch\":1770000000},"
            "\"services\":[]}";
        const RecoveryLoaded loaded =
            load_recovery(&no_boot, clock_at(150, 1770000050, "boot-a"));
        CHECK(!loaded.ok);
        CHECK_CONTAINS(loaded.error, "boot_id");
    }
    {
        // The CURRENT run's boot id is required too. Without it there is no
        // domain in which a persisted monotonic stamp can be placed.
        Recovery state;
        state.record_attempt("a", 10);
        const std::string text = state.to_json(clock_at(100, 1770000000, "boot-a"));
        const RecoveryLoaded loaded =
            load_recovery(&text, clock_at(150, 1770000050, ""));
        CHECK(!loaded.ok);
        CHECK_CONTAINS(loaded.error, "boot id");
    }
    {
        // A same-boot attempt later than the state's own anchor is inconsistent,
        // never a future attempt to trust.
        const std::string inconsistent =
            "{\"schema\":\"serveros/native-core-recovery.v2\",\"boot_id\":\"boot-a\","
            "\"anchor\":{\"monotonic\":100,\"wall_epoch\":1770000000},"
            "\"services\":[{\"id\":\"a\",\"consecutive_failures\":0,\"opened_until\":0,"
            "\"attempts\":[150]}]}";
        const RecoveryLoaded loaded =
            load_recovery(&inconsistent, clock_at(150, 1770000050, "boot-a"));
        CHECK(!loaded.ok);
        CHECK_CONTAINS(loaded.error, "anchor");
    }
    {
        // An attempt older than the bounded age is refused before any
        // arithmetic, so a corrupted file cannot overflow the conversion.
        const std::string ancient =
            "{\"schema\":\"serveros/native-core-recovery.v2\",\"boot_id\":\"boot-a\","
            "\"anchor\":{\"monotonic\":100,\"wall_epoch\":1770000000},"
            "\"services\":[{\"id\":\"a\",\"consecutive_failures\":0,\"opened_until\":0,"
            "\"attempts\":[-999999999999]}]}";
        const RecoveryLoaded loaded =
            load_recovery(&ancient, clock_at(150, 1770000050, "boot-a"));
        CHECK(!loaded.ok);
        CHECK_CONTAINS(loaded.error, "outside");
    }

    return check::report("test_sup_recovery");
}
