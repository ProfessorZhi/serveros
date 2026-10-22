// Task Scheduler has an independent TASK_* vocabulary, never inferred from SCM.
#pragma once

#include <string>

namespace nodecore::windows_core {

//: The closed task vocabulary. Every word is prefixed so that disjointness with
//: the service states is a property of the spelling, not a convention.
inline constexpr const char* kTaskReady = "TASK_READY";
inline constexpr const char* kTaskRunning = "TASK_RUNNING";
inline constexpr const char* kTaskDisabled = "TASK_DISABLED";
inline constexpr const char* kTaskUnknown = "TASK_UNKNOWN";
inline constexpr const char* kTaskUnsupported = "TASK_UNSUPPORTED";

inline constexpr const char* kTaskReasonUnsupportedPlatform = "UNSUPPORTED_PLATFORM";
inline constexpr const char* kTaskReasonNotImplemented = "TASK_SCHEDULER_NOT_IMPLEMENTED";

struct WindowsTaskObservation {
    std::string task_path;
    //: One of the kTask* words. Only meaningful when `measured`.
    std::string state = kTaskUnknown;
    //: The Task Scheduler's own `LastTaskResult`, or -1 when none was read. This
    //: is the field that answers "did the last run work", and it has no
    //: counterpart on the service side — a service does not have a last run.
    long last_result = -1;
    bool measured = false;
    std::string reason;
};

//: One question, no arguments beyond the task path. No run, no stop, no
//: creation, no registration, no export — a task the Core registered would be a
//: task it could then use, and that is a capability this stage does not have.
class WindowsTaskQuery {
public:
    virtual ~WindowsTaskQuery() = default;
    virtual WindowsTaskObservation query(const std::string& task_path) = 0;
};

// Explicit unsupported adapter retained for callers that disable task reads.
class UnsupportedTaskQuery final : public WindowsTaskQuery {
public:
    WindowsTaskObservation query(const std::string& task_path) override;
};

class Win32TaskQuery final : public WindowsTaskQuery {
public:
    WindowsTaskObservation query(const std::string& task_path) override;
};

//: Whether a task word can be confused with a service state word. Always false,
//: and asserted by the tests — a `true` here would mean a summary could report
//: one kind of thing using the other kind's vocabulary.
bool task_and_service_vocabularies_are_disjoint();

}  // namespace nodecore::windows_core
