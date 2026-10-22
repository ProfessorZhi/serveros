#include "win32_detail.hpp"
#include "windows-core/recovery.hpp"
#include "windows-core/task_status.hpp"
#include "windows-core/win32_scm.hpp"
#if defined(_WIN32)
#include <cstddef>
#include <oleauto.h>
#include <taskschd.h>
#include <wtsapi32.h>
#include <vector>
namespace nodecore::windows_core {
namespace {
struct Service {
    SC_HANDLE h = nullptr;
    ~Service() {
        if (h)
            ::CloseServiceHandle(h);
    }
};
template <class T> struct Com {
    T *p = nullptr;
    ~Com() {
        if (p)
            p->Release();
    }
    T *operator->() const { return p; }
};
struct Bstr {
    BSTR s = nullptr;
    explicit Bstr(const std::wstring &v) : s(::SysAllocString(v.c_str())) {}
    Bstr() = default;
    ~Bstr() { ::SysFreeString(s); }
    operator BSTR() const { return s; }
};
struct Apartment {
    HRESULT hr = ::CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    ~Apartment() {
        if (SUCCEEDED(hr))
            ::CoUninitialize();
    }
    bool ok() { return SUCCEEDED(hr) || hr == RPC_E_CHANGED_MODE; }
};
std::wstring wide(const std::string &s) { return {s.begin(), s.end()}; }
bool current_owner(const UnitGrant &g) {
    return valid_grants({g}) && g.enabled && detail::current_sid() == g.owner_sid;
}
UnitState scm_state(SC_HANDLE service, const UnitGrant &g) {
    UnitState out;
    SERVICE_STATUS_PROCESS status{};
    DWORD n = 0;
    if (!::QueryServiceStatusEx(service, SC_STATUS_PROCESS_INFO, reinterpret_cast<BYTE *>(&status),
                                sizeof(status), &n))
        return out;
    // Properly aligned buffer: QueryServiceConfig writes pointer-containing data.
    std::vector<std::max_align_t> buffer((8192 + sizeof(std::max_align_t) - 1) /
                                         sizeof(std::max_align_t));
    auto *config = reinterpret_cast<QUERY_SERVICE_CONFIGW *>(buffer.data());
    if (!::QueryServiceConfigW(service, config, 8192, &n))
        return out;
    out.native_grant = true;
    out.owner_matches = config->lpServiceStartName &&
                        detail::account_sid(config->lpServiceStartName) == g.owner_sid;
    auto obs =
        from_win32(g.target, status.dwCurrentState, status.dwWin32ExitCode, config->dwStartType, 0);
    out.measured = obs.measured;
    if (obs.state == kStateManualStop)
        out.condition = UnitCondition::Disabled;
    else if (obs.state == kStateNeedsLogin)
        out.condition = UnitCondition::NeedsLogin;
    else if (obs.state == kStateRunning)
        out.condition = UnitCondition::Running;
    else if (obs.state == kStateStopped && (config->dwStartType == SERVICE_AUTO_START ||
                                            config->dwStartType == SERVICE_DEMAND_START))
        out.condition = UnitCondition::Stopped;
    else if (obs.state == kStateStartPending || obs.state == kStateStopPending ||
             obs.state == kStateContinuePending || obs.state == kStatePausePending ||
             obs.state == kStatePaused)
        out.condition = UnitCondition::Busy;
    return out;
}
bool open_service(const UnitGrant &g, Service &manager, Service &service) {
    manager.h = ::OpenSCManagerW(nullptr, nullptr, SC_MANAGER_CONNECT);
    if (!manager.h)
        return false;
    service.h = ::OpenServiceW(manager.h, wide(g.target).c_str(),
                               SERVICE_QUERY_STATUS | SERVICE_QUERY_CONFIG | SERVICE_START);
    return service.h != nullptr;
}
bool task_execute_grant(IRegisteredTask *task) {
    Bstr descriptor;
    if (FAILED(task->GetSecurityDescriptor(OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION |
                                               DACL_SECURITY_INFORMATION,
                                           &descriptor.s)))
        return false;
    return descriptor.s && detail::task_execute_descriptor(descriptor.s);
}

bool open_task(const UnitGrant &g, Com<ITaskService> &service, Com<IRegisteredTask> &task) {
    if (FAILED(::CoCreateInstance(CLSID_TaskScheduler, nullptr, CLSCTX_INPROC_SERVER,
                                  IID_ITaskService, reinterpret_cast<void **>(&service.p))))
        return false;
    VARIANT empty;
    ::VariantInit(&empty);
    // All empty variants mean local scheduler/current token; no
    // host/user/password.
    if (FAILED(service->Connect(empty, empty, empty, empty)))
        return false;
    Com<ITaskFolder> root;
    Bstr slash(L"\\");
    if (FAILED(service->GetFolder(slash, &root.p)))
        return false;
    Bstr path(wide(g.target));
    return SUCCEEDED(root->GetTask(path, &task.p));
}
bool interactive_owner_present(const std::string &sid, bool &measured) {
    PWTS_SESSION_INFOW sessions = nullptr;
    DWORD count = 0;
    measured = false;
    if (!::WTSEnumerateSessionsW(WTS_CURRENT_SERVER_HANDLE, 0, 1, &sessions, &count))
        return false;
    if (count > 256) {
        ::WTSFreeMemory(sessions);
        return false;
    }
    bool all_read = true, present = false;
    for (DWORD i = 0; i < count; i++) {
        if (sessions[i].State != WTSActive && sessions[i].State != WTSDisconnected)
            continue;
        LPWSTR user = nullptr, domain = nullptr;
        DWORD user_bytes = 0, domain_bytes = 0;
        bool ok = ::WTSQuerySessionInformationW(WTS_CURRENT_SERVER_HANDLE, sessions[i].SessionId,
                                                WTSUserName, &user, &user_bytes) &&
                  ::WTSQuerySessionInformationW(WTS_CURRENT_SERVER_HANDLE, sessions[i].SessionId,
                                                WTSDomainName, &domain, &domain_bytes);
        if (!ok || user_bytes > 1024 || domain_bytes > 1024)
            all_read = false;
        else if (user && *user) {
            std::wstring name = (domain && *domain ? std::wstring(domain) + L"\\" : L"") + user;
            if (detail::account_sid(name) == sid)
                present = true;
        }
        if (user)
            ::WTSFreeMemory(user);
        if (domain)
            ::WTSFreeMemory(domain);
    }
    ::WTSFreeMemory(sessions);
    measured = present || all_read;
    return present;
}
UnitState task_state(IRegisteredTask *task, const UnitGrant &g) {
    UnitState out;
    TASK_STATE state;
    VARIANT_BOOL enabled = VARIANT_FALSE;
    LONG result = 0;
    if (FAILED(task->get_State(&state)) || FAILED(task->get_Enabled(&enabled)) ||
        FAILED(task->get_LastTaskResult(&result)))
        return out;
    Com<ITaskDefinition> def;
    Com<IPrincipal> principal;
    Bstr user;
    TASK_LOGON_TYPE logon;
    TASK_RUNLEVEL_TYPE run_level;
    if (FAILED(task->get_Definition(&def.p)) || FAILED(def->get_Principal(&principal.p)) ||
        FAILED(principal->get_UserId(&user.s)) || FAILED(principal->get_LogonType(&logon)) ||
        FAILED(principal->get_RunLevel(&run_level)))
        return out;
    Com<ITaskSettings> settings;
    TASK_INSTANCES_POLICY instances;
    if (FAILED(def->get_Settings(&settings.p)) ||
        FAILED(settings->get_MultipleInstances(&instances)) ||
        instances != TASK_INSTANCES_IGNORE_NEW)
        return out;
    out.measured = true;
    out.owner_matches = logon == TASK_LOGON_INTERACTIVE_TOKEN && run_level == TASK_RUNLEVEL_LUA &&
                        detail::account_sid(std::wstring(user.s ? user.s : L"")) == g.owner_sid;
    out.native_grant = task_execute_grant(task);
    bool login_measured = false;
    bool logged_in = interactive_owner_present(g.owner_sid, login_measured);
    if (!login_measured) {
        out.measured = false;
        return out;
    }
    if (!logged_in) {
        out.condition = UnitCondition::NeedsLogin;
        return out;
    }
    if (enabled == VARIANT_FALSE || state == TASK_STATE_DISABLED)
        out.condition = UnitCondition::Disabled;
    else if (static_cast<unsigned long>(result) == 0x8007052eUL ||
             static_cast<unsigned long>(result) == 0x80070569UL || result == 1069)
        out.condition = UnitCondition::NeedsLogin;
    else if (state == TASK_STATE_RUNNING || state == TASK_STATE_QUEUED)
        out.condition = UnitCondition::Busy;
    else if (state == TASK_STATE_READY)
        out.condition = UnitCondition::Stopped;
    return out;
}
} // namespace
UnitState Win32RecoveryBackend::inspect(const UnitGrant &g) {
    if (!current_owner(g))
        return {};
    if (g.manager == UnitManager::Scm) {
        Service manager, service;
        if (!open_service(g, manager, service))
            return {};
        return scm_state(service.h, g);
    }
    Apartment com;
    if (!com.ok())
        return {};
    Com<ITaskService> service;
    Com<IRegisteredTask> task;
    if (!open_task(g, service, task))
        return {};
    return task_state(task.p, g);
}
StartOutcome Win32RecoveryBackend::start(const UnitGrant &g) {
    if (!current_owner(g))
        return StartOutcome::Refused;
    if (g.manager == UnitManager::Scm) {
        Service manager, service;
        if (!open_service(g, manager, service))
            return StartOutcome::Refused;
        auto s = scm_state(service.h, g);
        if (!s.measured || !s.native_grant || !s.owner_matches ||
            s.condition != UnitCondition::Stopped)
            return StartOutcome::Refused;
        return ::StartServiceW(service.h, 0, nullptr) != 0 ? StartOutcome::Accepted
                                                           : StartOutcome::Unknown;
    }
    Apartment com;
    if (!com.ok())
        return StartOutcome::Refused;
    Com<ITaskService> service;
    Com<IRegisteredTask> task;
    if (!open_task(g, service, task))
        return StartOutcome::Refused;
    auto s = task_state(task.p, g);
    if (!s.measured || !s.native_grant || !s.owner_matches || s.condition != UnitCondition::Stopped)
        return StartOutcome::Refused;
    VARIANT empty;
    ::VariantInit(&empty);
    Com<IRunningTask> running;
    // Do not ignore constraints, select another session/user, or change the
    // action.
    return SUCCEEDED(task->RunEx(empty, 0, 0, nullptr, &running.p)) ? StartOutcome::Accepted
                                                                    : StartOutcome::Unknown;
}
WindowsTaskObservation Win32TaskQuery::query(const std::string &path) {
    WindowsTaskObservation out;
    out.task_path = path;
    UnitGrant g{UnitRole::CloudCli, UnitManager::TaskScheduler, path, detail::current_sid(), true};
    if (!valid_grants({g})) {
        out.reason = "INVALID_TASK_PATH";
        return out;
    }
    Apartment com;
    Com<ITaskService> service;
    Com<IRegisteredTask> task;
    if (!com.ok() || !open_task(g, service, task)) {
        out.reason = "TASK_QUERY_FAILED";
        return out;
    }
    TASK_STATE state;
    VARIANT_BOOL enabled;
    LONG last;
    if (FAILED(task->get_State(&state)) || FAILED(task->get_Enabled(&enabled)) ||
        FAILED(task->get_LastTaskResult(&last))) {
        out.reason = "TASK_QUERY_FAILED";
        return out;
    }
    out.measured = true;
    out.last_result = last;
    if (enabled == VARIANT_FALSE || state == TASK_STATE_DISABLED)
        out.state = kTaskDisabled;
    else if (state == TASK_STATE_RUNNING || state == TASK_STATE_QUEUED)
        out.state = kTaskRunning;
    else if (state == TASK_STATE_READY)
        out.state = kTaskReady;
    else {
        out.state = kTaskUnknown;
        out.measured = false;
        out.reason = "TASK_STATE_UNKNOWN";
    }
    return out;
}
} // namespace nodecore::windows_core
#else
namespace nodecore::windows_core {
UnitState Win32RecoveryBackend::inspect(const UnitGrant &) { return {}; }
StartOutcome Win32RecoveryBackend::start(const UnitGrant &) { return StartOutcome::Refused; }
WindowsTaskObservation Win32TaskQuery::query(const std::string &path) {
    WindowsTaskObservation o;
    o.task_path = path;
    o.state = kTaskUnsupported;
    o.reason = kTaskReasonUnsupportedPlatform;
    return o;
}
} // namespace nodecore::windows_core
#endif
