// The resident loop: what makes the Core a Core rather than a command.
//
// WHY EVERYTHING IS INJECTED
// --------------------------
// The loop takes its clock, its sleep and its stop signal as functions. That is
// not ceremony — it is the difference between a test that says "after two ticks
// the report says two ticks" and a test that sleeps for two minutes and then
// hopes. Nothing here reads the wall clock, and a test drives the whole
// lifecycle in microseconds.
//
// WHAT IT DOES NOT DO
// -------------------
//   * It does not restart itself. An exception inside a tick is caught, counted
//     and survived, because a Core that exited on the first bad reading would
//     hand its lifecycle to whoever notices. The process exits only when it is
//     asked to or when the loop cannot continue at all, and then `systemd`'s
//     `Restart=on-failure` decides what happens next — one supervisor, never
//     two.
//   * It does not catch up on missed ticks. A tick that overran its interval
//     moves the next deadline to when it FINISHED, so a slow host gets fewer
//     ticks rather than a backlog of them fired back to back.
//   * It does not spin on a clock that jumps backwards. A monotonic clock
//     should not, but a caller can inject one that does, so a backwards jump
//     re-anchors the deadline and is counted rather than becoming a storm of
//     ticks until the clock catches up.
#pragma once

#include <functional>
#include <string>

namespace nodecore::supervisor {

struct TickOutcome {
    bool ok = true;
    std::string error;
};

using TickFn = std::function<TickOutcome()>;

struct ServeHooks {
    // Seconds from an arbitrary fixed point. Monotonic in production; whatever
    // a test says in a test.
    std::function<long()> monotonic_seconds;
    // Sleeps AT MOST `millis`. The loop asks for a bounded slice rather than
    // the whole interval so that a stop request is seen promptly.
    std::function<void(long millis)> sleep_millis;
    std::function<bool()> stop_requested;
};

struct ServeOptions {
    long tick_seconds = 60;
    // How long the loop may sleep before it looks at the stop flag again. A
    // SIGTERM during a 60 s tick should not take 60 s to be honoured.
    long slice_millis = 200;
    // -1 means "until stopped". Tests set a small number so a lifecycle is
    // finite without a signal.
    long max_ticks = -1;
};

struct ServeReport {
    bool ok = true;
    std::string error;
    long ticks = 0;
    long tick_errors = 0;
    bool stopped_by_request = false;
    bool completed = false;
    long first_tick_monotonic = -1;
    long last_tick_monotonic = -1;
    long clock_backwards_events = 0;
};

// Runs the loop until it is stopped, until `max_ticks` have run, or until the
// options are unusable. The first tick happens immediately: a Core that came up
// and reported nothing for a whole interval would be indistinguishable from one
// that failed to start.
ServeReport run_serve(const ServeOptions& options, const TickFn& tick, const ServeHooks& hooks);

}  // namespace nodecore::supervisor
