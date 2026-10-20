#include "supervisor/serve.hpp"

#include <algorithm>
#include <exception>
#include <string>

namespace nodecore::supervisor {

ServeReport run_serve(const ServeOptions& options, const TickFn& tick, const ServeHooks& hooks) {
    ServeReport report;
    if (options.tick_seconds < 1) {
        report.ok = false;
        report.error = "tick_seconds must be at least 1";
        return report;
    }
    if (options.slice_millis < 1) {
        report.ok = false;
        report.error = "slice_millis must be at least 1";
        return report;
    }
    if (!hooks.monotonic_seconds || !hooks.sleep_millis || !hooks.stop_requested || !tick) {
        report.ok = false;
        report.error = "the loop was given an incomplete set of hooks";
        return report;
    }

    long last_now = hooks.monotonic_seconds();
    // The first tick is due NOW. Waiting a whole interval before the first
    // observation is how a Core that started successfully looks identical to
    // one that did not.
    long deadline = last_now;

    while (true) {
        if (hooks.stop_requested()) {
            report.stopped_by_request = true;
            return report;
        }
        if (options.max_ticks >= 0 && report.ticks >= options.max_ticks) {
            report.completed = true;
            return report;
        }

        const long now = hooks.monotonic_seconds();
        if (now < last_now) {
            // Counted, not obeyed. Re-anchoring means a clock that stepped
            // backwards costs one delayed tick rather than a burst of them
            // fired until it catches up.
            report.clock_backwards_events += 1;
            const long re_anchored = now + options.tick_seconds;
            if (re_anchored > deadline) {
                deadline = re_anchored;
            }
        }
        last_now = now;

        if (now >= deadline) {
            TickOutcome outcome;
            try {
                outcome = tick();
            } catch (const std::exception& exc) {
                // Caught rather than propagated: one bad tick is not a reason
                // to end the process, and ending it would leave the decision to
                // a supervisor that is not this one. Counted so a status reader
                // can see it happened.
                outcome.ok = false;
                outcome.error = std::string("the tick raised: ") + exc.what();
            } catch (...) {
                outcome.ok = false;
                outcome.error = "the tick raised something that is not an exception";
            }
            report.ticks += 1;
            if (!outcome.ok) {
                report.tick_errors += 1;
            }
            if (report.first_tick_monotonic < 0) {
                report.first_tick_monotonic = now;
            }
            report.last_tick_monotonic = now;
            // Measured from FINISH, so a tick that took longer than its own
            // interval costs one delayed tick rather than a backlog fired back
            // to back.
            const long finished = hooks.monotonic_seconds();
            deadline = (finished > now ? finished : now) + options.tick_seconds;
            continue;
        }

        const long remaining_seconds = deadline - now;
        const long slice = std::min<long>(options.slice_millis, remaining_seconds * 1000);
        hooks.sleep_millis(slice > 0 ? slice : 1);
    }
}

}  // namespace nodecore::supervisor
