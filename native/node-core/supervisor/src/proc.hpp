// A bounded argv runner. Private to this component.
//
// There is no shell here and there is no way to ask for one: the input is a
// list of strings handed straight to `execvp`, so a unit id containing `;`,
// `$(...)`, a space or a backtick cannot become a second command — it can only
// become a program name that does not exist. That is the property
// `native/node-core/adapter/native_node_core.py` relies on at the Python layer
// (`runtime/bounded_invocation.invoke_once` hands a LIST to `Popen`), and it is
// the same shape here for the same reason.
//
// What it bounds:
//   * a wall deadline, after which the child's whole process group is killed —
//     the child is put in its own session, so a `systemctl` that forked
//     something does not outlive the timeout;
//   * both output streams, so a child that prints without end cannot make this
//     process read without end.
#pragma once

#include <map>
#include <string>
#include <vector>

namespace nodecore::supervisor::detail {

struct RunResult {
    bool ok = false;
    // EXITED / TIMED_OUT / OUTPUT_LIMIT / START_FAILED
    std::string code;
    std::string reason;
    int exit_code = -1;
    std::string stdout_text;
    std::string stderr_text;
};

RunResult run_argv(const std::vector<std::string>& argv,
                   const std::map<std::string, std::string>& env_overrides, int timeout_seconds,
                   std::size_t max_output_bytes = 65536);

}  // namespace nodecore::supervisor::detail
