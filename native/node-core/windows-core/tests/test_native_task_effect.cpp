// Opt-in isolated hosted Windows effect test. Only a freshly created task under
// this CI user's SID is registered/stopped/deleted; no user machine or service.
#include "nodecore/json.hpp"
#include "win32_detail.hpp"
#include "windows-core/status_pipe.hpp"
#include "windows-core/watch.hpp"
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <oleauto.h>
#include <sstream>
#include <stdexcept>
#include <taskschd.h>
using namespace nodecore::windows_core;
#ifndef SERVEROS_WINDOWS_SOURCE_REVISION
#define SERVEROS_WINDOWS_SOURCE_REVISION "unknown"
#endif
namespace {
struct Apartment {
  HRESULT result = ::CoInitializeEx(nullptr, COINIT_MULTITHREADED);
  ~Apartment() {
    if (SUCCEEDED(result))
      ::CoUninitialize();
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
struct Text {
  BSTR p = nullptr;
  explicit Text(const std::wstring &s) : p(::SysAllocString(s.c_str())) {}
  Text() = default;
  ~Text() { ::SysFreeString(p); }
  operator BSTR() const { return p; }
};
std::wstring wide(const std::string &s) { return {s.begin(), s.end()}; }
VARIANT borrowed(BSTR value) {
  VARIANT v;
  ::VariantInit(&v);
  v.vt = VT_BSTR;
  v.bstrVal = value;
  return v;
}
std::uint64_t creation(HANDLE process) {
  std::uint64_t out = 0;
  detail::creation_time(process, out);
  return out;
}
std::string primary_group() {
  HANDLE raw = nullptr;
  if (!::OpenProcessToken(::GetCurrentProcess(), TOKEN_QUERY, &raw))
    return {};
  detail::Handle token(raw);
  DWORD size = 0;
  ::GetTokenInformation(token, TokenPrimaryGroup, nullptr, 0, &size);
  if (size < sizeof(TOKEN_PRIMARY_GROUP) || size > 4096)
    return {};
  std::vector<unsigned char> data(size);
  if (!::GetTokenInformation(token, TokenPrimaryGroup, data.data(), size,
                             &size))
    return {};
  LPWSTR sid = nullptr;
  if (!::ConvertSidToStringSidW(
          reinterpret_cast<TOKEN_PRIMARY_GROUP *>(data.data())->PrimaryGroup,
          &sid))
    return {};
  std::wstring result(sid);
  ::LocalFree(sid);
  return {result.begin(), result.end()};
}
void require(bool condition, const char *stage) {
  if (!condition)
    throw std::runtime_error(stage);
}
std::string ascii(const std::wstring &s) { return {s.begin(), s.end()}; }
void write_new(const std::wstring &file, const std::string &text,
               const std::string &sid) {
  detail::OwnerSecurity security(sid);
  detail::Handle h(::CreateFileW(
      file.c_str(), GENERIC_WRITE, FILE_SHARE_READ, &security.attributes,
      CREATE_NEW, FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_WRITE_THROUGH,
      nullptr));
  DWORD n = 0;
  require(h.valid() &&
              ::WriteFile(h, text.data(), static_cast<DWORD>(text.size()), &n,
                          nullptr) &&
              n == text.size() && ::FlushFileBuffers(h),
          "WRITE_OWNED_FIXTURE");
}
int actor(const std::wstring &ready, const std::wstring &stop) {
  auto parent = std::filesystem::path(ready).parent_path();
  require(parent.is_absolute() &&
              parent == std::filesystem::path(stop).parent_path(),
          "ACTOR_PATH");
  auto sid = detail::current_sid();
  auto stamp = creation(::GetCurrentProcess());
  write_new(ready,
            "serveros/native-task-toy.v1\n" +
                std::to_string(::GetCurrentProcessId()) + " " +
                std::to_string(stamp) + "\n",
            sid);
  auto end = ::GetTickCount64() + 30000;
  while (::GetTickCount64() < end &&
         ::GetFileAttributesW(stop.c_str()) == INVALID_FILE_ATTRIBUTES)
    ::Sleep(20);
  return 0;
}
struct ActualProbe final : FunctionalProbe {
  IRegisteredTask *task;
  std::wstring marker;
  std::string sid;
  DWORD actor_pid = 0;
  std::uint64_t actor_creation = 0;
  ActualProbe(IRegisteredTask *t, std::wstring p, std::string s)
      : task(t), marker(std::move(p)), sid(std::move(s)) {}
  bool ready() {
    detail::Handle file(::CreateFileW(
        marker.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
    if (!file.valid())
      return false;
    char buffer[256];
    DWORD n = 0;
    if (!::ReadFile(file, buffer, sizeof(buffer), &n, nullptr) || n == 0 ||
        n == sizeof(buffer))
      return false;
    std::istringstream in(std::string(buffer, n));
    std::string schema;
    DWORD pid = 0;
    std::uint64_t stamp = 0;
    if (!(in >> schema >> pid >> stamp) ||
        schema != "serveros/native-task-toy.v1" ||
        pid == ::GetCurrentProcessId())
      return false;
    detail::Handle process(::OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, pid));
    DWORD exit = 0;
    if (!process.valid() || !::GetExitCodeProcess(process, &exit) ||
        exit != STILL_ACTIVE || creation(process) != stamp ||
        detail::process_sid(process) != sid)
      return false;
    actor_pid = pid;
    actor_creation = stamp;
    return true;
  }
  ProbeReadiness check(UnitRole) override {
    auto deadline = ::GetTickCount64() + 5000;
    do {
      if (ready())
        return ProbeReadiness::Ready;
      TASK_STATE state = TASK_STATE_UNKNOWN;
      if (FAILED(task->get_State(&state)))
        return ProbeReadiness::Unmeasured;
      if (state != TASK_STATE_RUNNING && state != TASK_STATE_QUEUED &&
          state != TASK_STATE_READY)
        return ProbeReadiness::NotReady;
      // RunEx may return before Scheduler publishes QUEUED/RUNNING.
      // The functional marker, never just its scheduler state, is proof.
      ::Sleep(20);
    } while (::GetTickCount64() < deadline);
    return ProbeReadiness::NotReady;
  }
};
struct Fixture {
  Com<ITaskService> service;
  Com<ITaskFolder> root, folder;
  Com<IRegisteredTask> task;
  std::wstring folder_name, task_name = L"Component";
  bool created = false, registered = false;
  HRESULT last = S_OK;
  HRESULT cleanup_error = S_OK;
  std::string stage;
  void ok(HRESULT hr, const char *step) {
    last = hr;
    stage = step;
    require(SUCCEEDED(hr), step);
  }
  bool stop() {
    if (!registered)
      return true;
    HRESULT stopped = task->Stop(0);
    if (FAILED(stopped))
      cleanup_error = stopped;
    auto end = ::GetTickCount64() + 5000;
    do {
      TASK_STATE state = TASK_STATE_UNKNOWN;
      if (SUCCEEDED(task->get_State(&state)) && state != TASK_STATE_RUNNING &&
          state != TASK_STATE_QUEUED)
        return true;
      ::Sleep(20);
    } while (::GetTickCount64() < end);
    return false;
  }
  bool cleanup() {
    bool good = stop();
    if (registered) {
      Text name(task_name);
      HRESULT removed = folder->DeleteTask(name, 0);
      if (SUCCEEDED(removed))
        registered = false;
      else
        cleanup_error = removed;
      good = SUCCEEDED(removed) && good;
    }
    if (created) {
      Text name(folder_name);
      HRESULT removed = root->DeleteFolder(name, 0);
      if (SUCCEEDED(removed))
        created = false;
      else
        cleanup_error = removed;
      good = SUCCEEDED(removed) && good;
    }
    return good;
  }
  ~Fixture() { cleanup(); }
};
int effect_test(const std::wstring &output) {
  require(std::filesystem::path(output).is_absolute(), "EVIDENCE_ABSOLUTE");
  auto sid = detail::current_sid(), group = primary_group();
  require(!sid.empty() && !group.empty(), "CURRENT_USER_IDENTITY");
  Apartment apartment;
  Fixture fixture;
  auto evidence = nodecore::Json::object();
  evidence.set("schema", nodecore::Json::string(
                             "serveros/windows-native-task-effect.v1"));
  evidence.set("source_revision",
               nodecore::Json::string(SERVEROS_WINDOWS_SOURCE_REVISION));
  evidence.set("user_windows_live", nodecore::Json::string("NOT_RUN"));
  evidence.set("context",
               nodecore::Json::string("isolated-hosted-current-user-toy"));
  int result = 1;
  try {
    fixture.ok(apartment.result, "COM_INIT");
    fixture.ok(
        ::CoCreateInstance(CLSID_TaskScheduler, nullptr, CLSCTX_INPROC_SERVER,
                           IID_ITaskService,
                           reinterpret_cast<void **>(&fixture.service.p)),
        "CREATE_LOCAL_TASK_SERVICE");
    VARIANT empty;
    ::VariantInit(&empty);
    fixture.ok(fixture.service->Connect(empty, empty, empty, empty),
               "CONNECT_LOCAL_CURRENT_USER");
    Text slash(L"\\");
    fixture.ok(fixture.service->GetFolder(slash, &fixture.root.p),
               "ROOT_FOLDER");
    auto suffix = std::to_wstring(::GetCurrentProcessId()) + L"_" +
                  std::to_wstring(creation(::GetCurrentProcess()));
    fixture.folder_name = L"ServerOSCoreCI_" + suffix;
    Text folder_name(fixture.folder_name);
    Text descriptor(L"O:" + wide(sid) + L"G:" + wide(group) + L"D:P(A;;FA;;;" +
                    wide(sid) + L")(A;;FA;;;SY)");
    auto security = borrowed(descriptor);
    fixture.ok(
        fixture.root->CreateFolder(folder_name, security, &fixture.folder.p),
        "CREATE_UNIQUE_CURRENT_USER_FOLDER");
    fixture.created = true;
    Com<ITaskDefinition> definition;
    fixture.ok(fixture.service->NewTask(0, &definition.p),
               "NEW_TOY_DEFINITION");
    Com<IPrincipal> principal;
    fixture.ok(definition->get_Principal(&principal.p), "PRINCIPAL");
    Text user(wide(sid));
    fixture.ok(principal->put_UserId(user), "CURRENT_USER_PRINCIPAL");
    fixture.ok(principal->put_LogonType(TASK_LOGON_INTERACTIVE_TOKEN),
               "INTERACTIVE_TOKEN_ONLY");
    fixture.ok(principal->put_RunLevel(TASK_RUNLEVEL_LUA), "LUA_NO_ELEVATION");
    Com<ITaskSettings> settings;
    fixture.ok(definition->get_Settings(&settings.p), "SETTINGS");
    fixture.ok(settings->put_MultipleInstances(TASK_INSTANCES_IGNORE_NEW),
               "IGNORE_NEW");
    fixture.ok(settings->put_AllowDemandStart(VARIANT_TRUE), "DEMAND_START");
    fixture.ok(settings->put_DisallowStartIfOnBatteries(VARIANT_FALSE),
               "BATTERY_START");
    fixture.ok(settings->put_StopIfGoingOnBatteries(VARIANT_FALSE),
               "BATTERY_STOP");
    fixture.ok(settings->put_RunOnlyIfIdle(VARIANT_FALSE), "NOT_IDLE_ONLY");
    Text duration(L"PT1M");
    fixture.ok(settings->put_ExecutionTimeLimit(duration), "TIME_LIMIT");
    wchar_t image[32768];
    DWORD image_length = ::GetModuleFileNameW(nullptr, image, 32768);
    require(image_length && image_length < 32768, "OWN_EXECUTABLE");
    wchar_t temp[32768];
    DWORD temp_length = ::GetTempPathW(32768, temp);
    require(temp_length && temp_length < 32768, "OS_TEMP");
    std::wstring directory = std::wstring(temp) + L"ServerOSCoreCI_" + suffix;
    detail::OwnerSecurity own(sid);
    require(::CreateDirectoryW(directory.c_str(), &own.attributes) != 0,
            "CREATE_UNIQUE_TOY_DIRECTORY");
    std::wstring ready = directory + L"\\ready.txt",
                 stop = directory + L"\\stop.txt";
    Com<IActionCollection> actions;
    fixture.ok(definition->get_Actions(&actions.p), "ACTIONS");
    Com<IAction> action;
    fixture.ok(actions->Create(TASK_ACTION_EXEC, &action.p),
               "SINGLE_OWN_EXEC_ACTION");
    Com<IExecAction> exec;
    fixture.ok(action->QueryInterface(IID_IExecAction,
                                      reinterpret_cast<void **>(&exec.p)),
               "EXEC_ACTION");
    Text executable(std::wstring(image, image_length)),
        arguments(L"--actor \"" + ready + L"\" \"" + stop + L"\""),
        working(directory);
    fixture.ok(exec->put_Path(executable), "OWN_BINARY_PATH");
    fixture.ok(exec->put_Arguments(arguments), "FIXED_TOY_ARGUMENTS");
    fixture.ok(exec->put_WorkingDirectory(working), "OWN_WORKING_DIRECTORY");
    Text task_name(fixture.task_name);
    auto actor_user = borrowed(user);
    fixture.ok(fixture.folder->RegisterTaskDefinition(
                   task_name, definition.p, TASK_CREATE, actor_user, empty,
                   TASK_LOGON_INTERACTIVE_TOKEN, security, &fixture.task.p),
               "REGISTER_OWNED_TEMP_TASK");
    fixture.registered = true;
    Text actual_security;
    fixture.ok(fixture.task->GetSecurityDescriptor(
                   OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION |
                       DACL_SECURITY_INFORMATION,
                   &actual_security.p),
               "READBACK_ACTUAL_TASK_SECURITY");
    PSECURITY_DESCRIPTOR parsed = nullptr;
    require(::ConvertStringSecurityDescriptorToSecurityDescriptorW(
                actual_security, SDDL_REVISION_1, &parsed, nullptr) != 0,
            "PARSE_ACTUAL_TASK_SECURITY");
    PSID owner = nullptr;
    BOOL defaulted = FALSE;
    bool owner_valid =
        ::GetSecurityDescriptorOwner(parsed, &owner, &defaulted) != 0;
    LPWSTR owner_text = nullptr;
    owner_valid =
        owner_valid && ::ConvertSidToStringSidW(owner, &owner_text) != 0;
    bool current_owner = owner_valid && std::wstring(owner_text) == wide(sid);
    ::LocalFree(owner_text);
    ::LocalFree(parsed);
    evidence.set("actual_task_object_owned_by_current_user",
                 nodecore::Json::boolean(current_owner));
    require(current_owner, "ACTUAL_TASK_OBJECT_OWNER");
    UnitGrant grant{UnitRole::CloudCli, UnitManager::TaskScheduler,
                    "\\" + ascii(fixture.folder_name) + "\\Component", sid,
                    true};
    ActualProbe probe(fixture.task.p, ready, sid);
    Win32RecoveryBackend backend(probe);
    Win32RecoveryStore store;
    RecoveryEngine engine({grant}, store, backend);
    auto before = backend.inspect(grant);
    evidence.set("native_measured", nodecore::Json::boolean(before.measured));
    evidence.set("native_execute_grant",
                 nodecore::Json::boolean(before.native_grant));
    evidence.set("owner_matches_current_user",
                 nodecore::Json::boolean(before.owner_matches));
    evidence.set("initial_unit_condition",
                 nodecore::Json::integer(static_cast<int>(before.condition)));
    evidence.set("task_path", nodecore::Json::string(grant.target));
    require(before.measured && before.native_grant && before.owner_matches &&
                before.condition == UnitCondition::Stopped,
            "ACTUAL_NATIVE_ADMISSION");
    require(probe.check(UnitRole::CloudCli) == ProbeReadiness::NotReady,
            "REAL_COMPONENT_NOT_READY");
    auto recovered = engine.recover(
        UnitRole::CloudCli, static_cast<std::int64_t>(std::time(nullptr)));
    evidence.set("recover_code", nodecore::Json::string(recovered.code));
    evidence.set("recover_acted", nodecore::Json::boolean(recovered.acted));
    require(recovered.acted && recovered.code == "RECOVERED" && probe.ready(),
            "REAL_RUNEX_AND_FUNCTIONAL_READINESS");
    auto pid = probe.actor_pid;
    evidence.set("actor_pid", nodecore::Json::uinteger(pid));
    evidence.set("actor_creation_time",
                 nodecore::Json::uinteger(probe.actor_creation));
    DATE last_run = 0;
    fixture.ok(fixture.task->get_LastRunTime(&last_run),
               "LAST_RUN_BEFORE_BUSY");
    auto busy = engine.recover(UnitRole::CloudCli,
                               static_cast<std::int64_t>(std::time(nullptr)));
    DATE after_busy = 0;
    fixture.ok(fixture.task->get_LastRunTime(&after_busy),
               "LAST_RUN_AFTER_BUSY");
    evidence.set("busy_code", nodecore::Json::string(busy.code));
    require(!busy.acted && busy.code == "BUSY" && probe.ready() &&
                probe.actor_pid == pid && last_run == after_busy,
            "BUSY_REFUSED_WITHOUT_RESTART");
    require(fixture.stop(), "STOP_OWN_TASK_AFTER_EFFECT");
    auto stopped_deadline = ::GetTickCount64() + 5000;
    while (probe.ready() && ::GetTickCount64() < stopped_deadline)
      ::Sleep(20);
    require(!probe.ready(), "ACTOR_STOPPED");
    WatchConfig config;
    config.observe_only = false;
    WatchUnit manual;
    manual.grant = grant;
    manual.grant.enabled = false;
    manual.desired = "manual";
    config.services.push_back(manual);
    auto manual_rows = watch_runtime_pass(
        config, store, backend, static_cast<std::int64_t>(std::time(nullptr)));
    require(manual_rows.size() == 1 && !manual_rows[0].acted &&
                manual_rows[0].recovery_code == "DESIRED_MANUAL" &&
                !probe.ready(),
            "MANUAL_REFUSED_WITHOUT_START");
    evidence.set("manual_code",
                 nodecore::Json::string(manual_rows[0].recovery_code));
    fixture.ok(fixture.task->put_Enabled(VARIANT_FALSE), "DISABLE_OWN_TASK");
    auto disabled = engine.recover(
        UnitRole::CloudCli, static_cast<std::int64_t>(std::time(nullptr)));
    require(!disabled.acted && disabled.code == "MANUAL_DISABLED" &&
                !probe.ready(),
            "DISABLED_REFUSED_WITHOUT_START");
    evidence.set("disabled_code", nodecore::Json::string(disabled.code));
    TASK_STATE final_state = TASK_STATE_UNKNOWN;
    fixture.ok(fixture.task->get_State(&final_state), "FINAL_TASK_STATE");
    evidence.set("final_task_state", nodecore::Json::integer(final_state));
    evidence.set("actor_stopped", nodecore::Json::boolean(!probe.ready()));
    bool cleaned = fixture.cleanup();
    evidence.set("cleanup_deleted_owned_task",
                 nodecore::Json::boolean(cleaned));
    require(cleaned && !fixture.created && !fixture.registered,
            "OWN_TASK_CLEANED");
    evidence.set("verdict", nodecore::Json::string("PASS"));
    result = 0;
  } catch (const std::exception &e) {
    evidence.set("verdict", nodecore::Json::string("FAIL_OR_OS_DENIED"));
    evidence.set("stage", nodecore::Json::string(e.what()));
    evidence.set("last_com_stage", nodecore::Json::string(fixture.stage));
    evidence.set("hresult", nodecore::Json::integer(fixture.last));
    evidence.set("native_error", nodecore::Json::uinteger(::GetLastError()));
    evidence.set("cleanup_deleted_owned_task",
                 nodecore::Json::boolean(fixture.cleanup()));
    evidence.set("cleanup_hresult",
                 nodecore::Json::integer(fixture.cleanup_error));
  }
  write_new(output, evidence.dump() + "\n", sid);
  std::cout << evidence.dump() << '\n';
  return result;
}
} // namespace
int wmain(int argc, wchar_t **argv) {
  try {
    if (argc == 4 && std::wstring(argv[1]) == L"--actor")
      return actor(argv[2], argv[3]);
    if (argc == 3 && std::wstring(argv[1]) == L"--evidence")
      return effect_test(argv[2]);
  } catch (const std::exception &e) {
    std::cerr << "NATIVE_TOY_BOOTSTRAP_REFUSED " << e.what() << ' '
              << ::GetLastError() << '\n';
  }
  return 2;
}
