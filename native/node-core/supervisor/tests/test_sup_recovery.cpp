// The budget, the ladder and the breaker — stated as instants, never as sleeps.
//
// Every case here would be a timing experiment if the clock were not injected.
// It is, so `t = 0` and `t = 3600` are values a reader can check by hand, and a
// decision that depended on when the test ran could not exist.
#include <string>
#include <vector>

#include "check.hpp"
#include "supervisor/recovery.hpp"

using namespace nodecore::supervisor;

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

        const std::string text = state.to_json();
        // No key, token, secret, password or credential appears in the state
        // this program writes. The Core holds no signing key, and a state file
        // that carried one would be the leak the acceptance list forbids.
        for (const char* forbidden : {"key", "token", "secret", "password", "credential", "passwd"}) {
            CHECK(text.find(forbidden) == std::string::npos);
        }

        const RecoveryLoaded loaded = load_recovery(&text);
        CHECK(loaded.ok);
        CHECK(loaded.was_present);
        CHECK_EQ(loaded.state.attempts("a").size(), std::size_t{1});
        CHECK_EQ(loaded.state.consecutive_failures("a"), 2);
        CHECK(loaded.state.circuit_open("a", 12));
        CHECK_EQ(loaded.state.attempts("b").size(), std::size_t{1});
    }
    {
        // No file at all is a first run, and it is a DIFFERENT answer from a
        // file that could not be read.
        const RecoveryLoaded absent = load_recovery(nullptr);
        CHECK(absent.ok);
        CHECK(!absent.was_present);
    }
    {
        // A corrupt state file must NOT load as an empty one: that would reset
        // the budget silently, and a budget that resets is not a budget.
        const std::string corrupt = "{\"schema\":\"serveros/native-core-recovery.v1\",";
        const RecoveryLoaded loaded = load_recovery(&corrupt);
        CHECK(!loaded.ok);
        CHECK(!loaded.was_present);
        CHECK(!loaded.error.empty());
    }
    {
        const std::string wrong_schema = "{\"schema\":\"something/else.v1\",\"services\":[]}";
        const RecoveryLoaded loaded = load_recovery(&wrong_schema);
        CHECK(!loaded.ok);
        CHECK_CONTAINS(loaded.error, "native-core-recovery");
    }
    {
        const std::string typo = "{\"schema\":\"serveros/native-core-recovery.v1\","
                                 "\"servies\":[]}";
        const RecoveryLoaded loaded = load_recovery(&typo);
        CHECK(!loaded.ok);
        CHECK_CONTAINS(loaded.error, "no services array");
    }

    return check::report("test_sup_recovery");
}
