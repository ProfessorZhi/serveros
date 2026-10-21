// The Task Scheduler, as its OWN vocabulary.
//
// WHY IT IS NOT A SECOND SET OF SERVICE STATES
// --------------------------------------------
// A scheduled task is not a service wearing a different name. A service has a
// `dwCurrentState` and stays up until something stops it; a task has no current
// state worth the name — it starts, it finishes, and the question that matters
// is what its LAST RUN did. Putting both behind one vocabulary produces a word
// that means different things depending on which half produced it: `RUNNING` for
// a task that finished an hour ago is the obvious example, and it is the kind of
// word a Fleet reader would act on.
//
// So this vocabulary is separate, and it is separate in a way a machine can
// check rather than a way a comment promises: every task word carries a `TASK_`
// prefix, so no word here can equal a service state word. The test asserts that
// set intersection is empty, which means a summary cannot report a task's status
// with a service's word, and cannot report a service's status with a task's.
//
// STATUS IN THIS BUILD
// --------------------
// NOT IMPLEMENTED, and not approximated. There is no Task Scheduler API call
// anywhere in this tree, no Windows compiler on this host to build one, and no
// owner authorisation to exercise it on a real machine (`contract/
// windows-core-status.v1.md` §5). `UnsupportedTaskQuery` returns
// `TASK_UNSUPPORTED` with a reason, and **no code path derives a task state from
// SCM data** — the two producers do not share a field, a function or a word.
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

//: The only implementation in this tree. It answers `TASK_UNSUPPORTED` with a
//: reason, because nothing here can read a task and nothing here will pretend to.
class UnsupportedTaskQuery final : public WindowsTaskQuery {
public:
    WindowsTaskObservation query(const std::string& task_path) override;
};

//: Whether a task word can be confused with a service state word. Always false,
//: and asserted by the tests — a `true` here would mean a summary could report
//: one kind of thing using the other kind's vocabulary.
bool task_and_service_vocabularies_are_disjoint();

}  // namespace nodecore::windows_core
