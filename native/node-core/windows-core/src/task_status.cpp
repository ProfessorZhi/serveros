// The Task Scheduler vocabulary. See `task_status.hpp` for why it is separate.
#include "windows-core/task_status.hpp"

#include <string>
#include <vector>

#include "windows-core/scm_status.hpp"

namespace nodecore::windows_core {

namespace {

const std::vector<std::string>& task_words() {
    static const std::vector<std::string> words = {kTaskReady, kTaskRunning, kTaskDisabled,
                                                   kTaskUnknown, kTaskUnsupported};
    return words;
}

const std::vector<std::string>& service_words() {
    static const std::vector<std::string> words = {
        kStateRunning,   kStateStopped,       kStateStartPending, kStateStopPending,
        kStatePaused,    kStatePausePending,  kStateContinuePending, kStateManualStop,
        kStateNeedsLogin, kStateNotInstalled, kStateUnknown,
    };
    return words;
}

}  // namespace

WindowsTaskObservation UnsupportedTaskQuery::query(const std::string& task_path) {
    WindowsTaskObservation observed;
    observed.task_path = task_path;
    observed.state = kTaskUnsupported;
    observed.measured = false;
    observed.reason = kTaskReasonNotImplemented;
    return observed;
}

bool task_and_service_vocabularies_are_disjoint() {
    for (const std::string& task : task_words()) {
        for (const std::string& service : service_words()) {
            if (task == service) {
                return false;
            }
        }
    }
    return true;
}

}  // namespace nodecore::windows_core
