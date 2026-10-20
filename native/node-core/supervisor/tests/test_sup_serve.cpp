// The resident loop, driven entirely by injected functions.
//
// Nothing here sleeps, nothing here reads a clock, and the whole lifecycle —
// two ticks, a stop request, a backwards clock, an overrunning tick — runs in
// microseconds. That is the point of the injection: a lifecycle test that slept
// would take minutes and would still only test the machine it ran on.
//
// The fake clock counts MILLISECONDS and reports seconds. Storing seconds and
// adding `millis / 1000` loses every sub-second slice, which is how the first
// version of this file deadlocked: a 200 ms slice advanced the clock by zero,
// the deadline was never reached, and the loop span forever. The production
// loop is fine — a real `nanosleep` moves a real clock — but a fake that
// silently refuses to move is a fake that tests nothing.
#include <cstdio>
#include <stdexcept>
#include <string>

#include "check.hpp"
#include "supervisor/serve.hpp"

using namespace nodecore::supervisor;

namespace {

struct FakeClock {
    long now_millis = 0;
    long slices = 0;
    long slept_millis = 0;
    bool stop = false;
    // Move the clock backwards from inside a sleep, once, after this many
    // slices. Negative disables it.
    long backwards_after_slices = -1;
    long backwards_by_millis = 0;
    bool went_backwards = false;

    long seconds() const { return now_millis / 1000; }
};

ServeHooks hooks_for(FakeClock& clock) {
    ServeHooks hooks;
    hooks.monotonic_seconds = [&clock] { return clock.seconds(); };
    hooks.sleep_millis = [&clock](long millis) {
        clock.slices += 1;
        clock.slept_millis += millis;
        clock.now_millis += millis;
        if (!clock.went_backwards && clock.backwards_after_slices >= 0 &&
            clock.slices >= clock.backwards_after_slices) {
            clock.now_millis -= clock.backwards_by_millis;
            clock.went_backwards = true;
        }
    };
    hooks.stop_requested = [&clock] { return clock.stop; };
    return hooks;
}

}  // namespace

int main() {
    // ------------------------------------------------------------- two ticks

    {
        FakeClock clock;
        ServeOptions options;
        options.tick_seconds = 60;
        options.slice_millis = 60000;  // one slice per interval
        options.max_ticks = 2;

        int tick_count = 0;
        const TickFn tick = [&tick_count] {
            tick_count += 1;
            return TickOutcome{};
        };

        const ServeReport report = run_serve(options, tick, hooks_for(clock));
        CHECK(report.ok);
        CHECK(report.completed);
        CHECK(!report.stopped_by_request);
        CHECK_EQ(report.ticks, 2L);
        CHECK_EQ(tick_count, 2);
        CHECK_EQ(report.tick_errors, 0L);
        // The FIRST tick is immediate: a Core that came up and said nothing for
        // a whole interval is indistinguishable from one that failed to start.
        CHECK_EQ(report.first_tick_monotonic, 0L);
        // And the second is one interval later, because the clock says so.
        CHECK_EQ(report.last_tick_monotonic, 60L);
        CHECK_EQ(clock.seconds(), 60L);
    }

    // ------------------------------------------------- a stop request wins

    {
        FakeClock clock;
        ServeOptions options;
        options.tick_seconds = 60;
        options.slice_millis = 60000;
        options.max_ticks = 10;  // never reached

        const TickFn tick = [&clock] {
            clock.stop = true;  // a SIGTERM arriving during the first tick
            return TickOutcome{};
        };

        const ServeReport report = run_serve(options, tick, hooks_for(clock));
        CHECK(report.ok);
        CHECK(report.stopped_by_request);
        CHECK(!report.completed);
        // Exactly the tick that was already running. A stop is honoured between
        // ticks, so nobody loses the observation that was in flight.
        CHECK_EQ(report.ticks, 1L);
    }

    // ------------------------------------------------- a bad tick is survivable

    {
        FakeClock clock;
        ServeOptions options;
        options.tick_seconds = 60;
        options.slice_millis = 60000;
        options.max_ticks = 3;

        int calls = 0;
        const TickFn tick = [&calls] {
            calls += 1;
            if (calls == 2) {
                return TickOutcome{false, "systemctl could not be read"};
            }
            if (calls == 3) {
                throw std::runtime_error("the observation step raised");
            }
            return TickOutcome{};
        };

        const ServeReport report = run_serve(options, tick, hooks_for(clock));
        // A failing tick does NOT end the loop and does NOT restart anything:
        // the Core keeps observing, and the count is what a status reader sees.
        CHECK(report.ok);
        CHECK(report.completed);
        CHECK_EQ(report.ticks, 3L);
        CHECK_EQ(report.tick_errors, 2L);
    }

    // --------------------------------- an overrunning tick is not a backlog

    {
        FakeClock clock;
        ServeOptions options;
        options.tick_seconds = 60;
        options.slice_millis = 60000;
        options.max_ticks = 2;

        const TickFn tick = [&clock] {
            clock.now_millis += 120000;  // the observation took two intervals
            return TickOutcome{};
        };

        const ServeReport report = run_serve(options, tick, hooks_for(clock));
        CHECK_EQ(report.ticks, 2L);
        // The second tick is one interval after the FIRST FINISHED (0 + 120),
        // not one interval after it started. Measuring from the start would
        // fire the missed ticks back to back against a host that is already too
        // slow to keep up.
        CHECK_EQ(report.last_tick_monotonic, 180L);
    }

    // ------------------------------------------------------ a backwards clock

    {
        FakeClock clock;
        clock.backwards_after_slices = 2;
        clock.backwards_by_millis = 90000;  // back to 30 s, below the 60 s seen

        ServeOptions options;
        options.tick_seconds = 60;
        options.slice_millis = 60000;
        options.max_ticks = 3;

        const TickFn tick = [] { return TickOutcome{}; };
        const ServeReport report = run_serve(options, tick, hooks_for(clock));
        CHECK(report.ok);
        CHECK(report.completed);
        CHECK_EQ(report.ticks, 3L);
        // Counted rather than obeyed. A clock that stepped backwards must cost
        // at most a delayed tick, never a burst of them until it catches up.
        CHECK(clock.went_backwards);
        CHECK(report.clock_backwards_events >= 1);
    }

    // ------------------------------------------------------- unusable options

    {
        FakeClock clock;
        const TickFn tick = [] { return TickOutcome{}; };
        ServeOptions zero_interval;
        zero_interval.tick_seconds = 0;
        const ServeReport bad_interval = run_serve(zero_interval, tick, hooks_for(clock));
        CHECK(!bad_interval.ok);
        CHECK_CONTAINS(bad_interval.error, "tick_seconds");
        CHECK_EQ(bad_interval.ticks, 0L);

        ServeOptions zero_slice;
        zero_slice.slice_millis = 0;
        const ServeReport bad_slice = run_serve(zero_slice, tick, hooks_for(clock));
        CHECK(!bad_slice.ok);
        CHECK_CONTAINS(bad_slice.error, "slice_millis");

        ServeHooks incomplete;
        const ServeReport bad_hooks = run_serve(ServeOptions{}, tick, incomplete);
        CHECK(!bad_hooks.ok);
        CHECK_CONTAINS(bad_hooks.error, "incomplete");
    }

    // ------------------------------------ the slice is what keeps a stop prompt

    {
        FakeClock clock;
        ServeOptions options;
        options.tick_seconds = 60;
        options.slice_millis = 200;  // the production slice
        options.max_ticks = 2;

        const TickFn tick = [] { return TickOutcome{}; };
        const ServeReport report = run_serve(options, tick, hooks_for(clock));
        CHECK_EQ(report.ticks, 2L);
        // 60 s spread over 200 ms slices — a SIGTERM is seen within one slice,
        // not at the end of the interval.
        CHECK(clock.slices >= 300);
        CHECK(clock.slept_millis > 0);
    }

    return check::report("test_sup_serve");
}
