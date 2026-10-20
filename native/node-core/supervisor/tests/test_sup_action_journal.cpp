// The durable attempt at the effect boundary.
//
// WHY THIS EXISTS
// ---------------
// The acting loop used to call `provider.start/restart/stop` FIRST and persist
// the budget only after the whole loop. A kill, crash, power loss or ENOSPC
// between the effect and that write left a real service action outside the
// persisted budget, so a relaunch could repeat it. The `--state` path being
// required is not enough if the write happens after the effect.
//
// These tests use a FAKE Provider and a real temporary state file. Nothing
// starts or stops a real unit; the "crash" is simply dropping the in-memory
// Recovery and loading the file again. The fake provider reads the state file
// INSIDE the provider call, so the prewrite-before-effect ordering is asserted
// at the moment the effect would happen.
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cstdlib>
#include <fstream>
#include <functional>
#include <iostream>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include "check.hpp"
#include "json_detail.hpp"
#include "supervisor/action_journal.hpp"
#include "supervisor/recovery.hpp"

using namespace nodecore::supervisor;
using nodecore::supervisor::detail::Json;
using nodecore::supervisor::detail::Reader;

namespace {

std::string make_temp_dir() {
    char pattern[] = "/tmp/serveros-sup-journal-XXXXXX";
    char* made = ::mkdtemp(pattern);
    return made == nullptr ? std::string() : std::string(made);
}

void remove_dir(const std::string& dir) {
    ::chmod(dir.c_str(), 0700);
    ::unlink((dir + "/state.json").c_str());
    ::rmdir(dir.c_str());
}

std::string read_file(const std::string& path, bool& ok) {
    std::ifstream file(path);
    if (!file) {
        ok = false;
        return "";
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    ok = true;
    return buffer.str();
}

RecoveryClock clock_at(long monotonic, long long wall_epoch, const std::string& boot_id) {
    RecoveryClock clock;
    clock.monotonic = monotonic;
    clock.wall_epoch = wall_epoch;
    clock.boot_id = boot_id;
    return clock;
}

RecoveryLoaded load_from(const std::string& path, const RecoveryClock& clock) {
    bool ok = false;
    const std::string text = read_file(path, ok);
    if (!ok) {
        RecoveryLoaded loaded;
        loaded.error = "the state file does not exist";
        return loaded;
    }
    return load_recovery(&text, clock);
}

bool state_has_attempt(const std::string& path, const std::string& service_id) {
    bool ok = false;
    const std::string text = read_file(path, ok);
    if (!ok) {
        return false;
    }
    Json document;
    std::string error;
    Reader reader(text);
    if (!reader.parse(document, error) || !document.is_object()) {
        return false;
    }
    const Json* services = document.find("services");
    if (services == nullptr || !services->is_array()) {
        return false;
    }
    for (const Json& row : services->items) {
        if (!row.is_object()) {
            continue;
        }
        const Json* id = row.find("id");
        const Json* attempts = row.find("attempts");
        if (id != nullptr && id->is_string() && id->text == service_id && attempts != nullptr &&
            attempts->is_array() && !attempts->items.empty()) {
            return true;
        }
    }
    return false;
}

ActionJournal make_journal(const std::string& path, Recovery* recovery,
                           std::function<RecoveryClock()> clock) {
    StateDirectory directory;
    StatePathVerdict verdict;
    const bool ok = directory.open(path, verdict);
    CHECK(ok);
    if (!ok) {
        std::cout << "  (" << verdict.code << ": " << verdict.reason << ")\n";
    }
    return ActionJournal(std::move(directory), recovery, std::move(clock));
}

bool file_exists(const std::string& path) {
    struct stat info;
    return ::lstat(path.c_str(), &info) == 0;
}

int count_temp_files(const std::string& dir) {
    DIR* handle = ::opendir(dir.c_str());
    if (handle == nullptr) {
        return -1;
    }
    int count = 0;
    while (dirent* entry = ::readdir(handle)) {
        const std::string name = entry->d_name;
        if (name.find(".tmp.") != std::string::npos) {
            ++count;
        }
    }
    ::closedir(handle);
    return count;
}

//: A Provider that records whether the durable attempt was already on disk
//: when the effect was invoked, and never touches a service manager.
class InspectingProvider : public Provider {
public:
    std::string state_path;
    std::string attempt_id;
    bool start_called = false;
    bool restart_called = false;
    bool stop_called = false;
    bool query_called = false;
    bool durable_attempt_seen = false;

    ProviderResult start(const std::string&) override { return effect(&start_called); }
    ProviderResult restart(const std::string&) override { return effect(&restart_called); }
    ProviderResult stop(const std::string&) override { return effect(&stop_called); }

    Observed query(const std::string& unit) override {
        query_called = true;
        return Observed::unavailable(unit, "this test does not observe");
    }

private:
    ProviderResult effect(bool* marker) {
        *marker = true;
        durable_attempt_seen = state_has_attempt(state_path, attempt_id);
        ProviderResult result;
        result.ok = true;
        result.code = "EXITED";
        return result;
    }
};

}  // namespace

// ---------------------------------------------------------------------------

void test_prewrite_is_durable_before_the_effect() {
    const std::string dir = make_temp_dir();
    CHECK(!dir.empty());
    if (dir.empty()) {
        return;
    }
    const std::string path = dir + "/state.json";

    Recovery recovery;
    ActionJournal journal = make_journal(path, &recovery,
                          [] { return clock_at(1000, 1770000000, "boot-a"); });
    InspectingProvider provider;
    provider.state_path = path;
    provider.attempt_id = "svc";

    const EffectExecution effect =
        execute_effect(provider, journal, true, "svc", "svc.service", "restart", 1000);
    CHECK(effect.ok);
    CHECK(effect.provider_called);
    CHECK(provider.restart_called);
    // THE ASSERTION THIS FILE EXISTS FOR: the fake provider saw the durable
    // attempt while it was being called, not after the loop.
    CHECK(provider.durable_attempt_seen);
    CHECK(!provider.query_called);

    // The effect wrote no outcome; only the attempt is durable.
    const RecoveryLoaded loaded = load_from(path, clock_at(1001, 1770000000, "boot-a"));
    CHECK(loaded.ok);
    CHECK_EQ(loaded.state.attempts("svc").size(), std::size_t{1});
    CHECK_EQ(loaded.state.consecutive_failures("svc"), 0);

    // The atomic replace leaves a complete, 0600 regular file and no temp.
    struct stat info;
    CHECK(::stat(path.c_str(), &info) == 0);
    CHECK(S_ISREG(info.st_mode));
    CHECK_EQ(info.st_mode & 0777, 0600);
    CHECK_EQ(count_temp_files(dir), 0);

    remove_dir(dir);
}

void test_prewrite_failure_has_no_effect() {
    const std::string dir = make_temp_dir();
    CHECK(!dir.empty());
    if (dir.empty()) {
        return;
    }
    const std::string path = dir + "/state.json";

    Recovery recovery;
    ActionJournal journal = make_journal(path, &recovery,
                          [] { return clock_at(1000, 1770000000, "boot-a"); });
    InspectingProvider provider;
    provider.state_path = path;
    provider.attempt_id = "svc";

    // The directory is not writable, so the prewrite cannot create the temp
    // file. The provider MUST NOT be called.
    CHECK(::chmod(dir.c_str(), 0500) == 0);
    const EffectExecution effect =
        execute_effect(provider, journal, true, "svc", "svc.service", "restart", 1000);
    CHECK(::chmod(dir.c_str(), 0700) == 0);

    CHECK(!effect.ok);
    CHECK(effect.prewrite_failed);
    CHECK(!effect.provider_called);
    CHECK(!provider.restart_called);
    CHECK(!provider.query_called);
    CHECK(!state_has_attempt(path, "svc"));

    remove_dir(dir);
}

void test_a_crash_after_the_effect_leaves_the_attempt() {
    const std::string dir = make_temp_dir();
    CHECK(!dir.empty());
    if (dir.empty()) {
        return;
    }
    const std::string path = dir + "/state.json";

    Recovery recovery;
    RecoveryPolicy policy;
    policy.max_attempts = 1;
    ActionJournal journal = make_journal(path, &recovery,
                          [] { return clock_at(1000, 1770000000, "boot-a"); });
    InspectingProvider provider;
    provider.state_path = path;
    provider.attempt_id = "svc";

    const EffectExecution effect =
        execute_effect(provider, journal, true, "svc", "svc.service", "restart", 1000);
    CHECK(effect.ok);
    CHECK(provider.durable_attempt_seen);

    // THE CRASH: no write_outcome, and the in-memory Recovery is discarded.
    const RecoveryLoaded loaded = load_from(path, clock_at(1001, 1770000000, "boot-a"));
    CHECK(loaded.ok);
    CHECK_EQ(loaded.state.attempts("svc").size(), std::size_t{1});
    CHECK_EQ(loaded.state.may_act("svc", policy, 1001).code,
             std::string("BUDGET_EXHAUSTED"));

    remove_dir(dir);
}

void test_outcome_write_failure_keeps_the_attempt() {
    const std::string dir = make_temp_dir();
    CHECK(!dir.empty());
    if (dir.empty()) {
        return;
    }
    const std::string path = dir + "/state.json";

    Recovery recovery;
    RecoveryPolicy policy;
    policy.max_attempts = 1;
    ActionJournal journal = make_journal(path, &recovery,
                          [] { return clock_at(1000, 1770000000, "boot-a"); });
    InspectingProvider provider;
    provider.state_path = path;
    provider.attempt_id = "svc";

    const EffectExecution effect =
        execute_effect(provider, journal, true, "svc", "svc.service", "restart", 1000);
    CHECK(effect.ok);
    recovery.record_outcome("svc", ActionOutcome::NotRecovered, policy, 1000);
    CHECK_EQ(recovery.consecutive_failures("svc"), 1);

    // The outcome write fails. The attempt from the prewrite must remain
    // durable; the outcome is simply not on disk.
    CHECK(::chmod(dir.c_str(), 0500) == 0);
    std::string error;
    CHECK(!journal.write_outcome(error));
    CHECK(!error.empty());
    CHECK(::chmod(dir.c_str(), 0700) == 0);

    const RecoveryLoaded loaded = load_from(path, clock_at(1001, 1770000000, "boot-a"));
    CHECK(loaded.ok);
    CHECK_EQ(loaded.state.attempts("svc").size(), std::size_t{1});
    CHECK_EQ(loaded.state.consecutive_failures("svc"), 0);
    CHECK_EQ(loaded.state.may_act("svc", policy, 1001).code,
             std::string("BUDGET_EXHAUSTED"));

    remove_dir(dir);
}

void test_cross_boot_places_the_attempt() {
    const std::string dir = make_temp_dir();
    CHECK(!dir.empty());
    if (dir.empty()) {
        return;
    }
    const std::string path = dir + "/state.json";

    Recovery recovery;
    RecoveryPolicy policy;
    ActionJournal journal = make_journal(path, &recovery,
                          [] { return clock_at(1000, 1770000000, "boot-a"); });
    InspectingProvider provider;
    provider.state_path = path;
    provider.attempt_id = "svc";

    const EffectExecution effect =
        execute_effect(provider, journal, true, "svc", "svc.service", "restart", 1000);
    CHECK(effect.ok);

    // The next boot is 20 s later by wall clock. The attempt is in the past,
    // not the old monotonic 1000, so the ladder applies rather than a future
    // stamp locking the budget.
    const RecoveryLoaded loaded = load_from(path, clock_at(10, 1770000020, "boot-b"));
    CHECK(loaded.ok);
    CHECK_EQ(loaded.state.attempts("svc").size(), std::size_t{1});
    const RecoveryDecision decision = loaded.state.may_act("svc", policy, 10);
    CHECK_EQ(decision.code, std::string("BACKOFF"));
    CHECK_EQ(decision.retry_after_seconds, 40L);  // 60 - (10 - (-10))

    remove_dir(dir);
}

void test_a_second_start_cannot_exceed_the_budget() {
    const std::string dir = make_temp_dir();
    CHECK(!dir.empty());
    if (dir.empty()) {
        return;
    }
    const std::string path = dir + "/state.json";

    Recovery recovery;
    RecoveryPolicy policy;
    policy.max_attempts = 2;
    ActionJournal journal = make_journal(path, &recovery,
                          [] { return clock_at(1001, 1770000000, "boot-a"); });
    InspectingProvider provider;
    provider.state_path = path;
    provider.attempt_id = "svc";

    CHECK(execute_effect(provider, journal, true, "svc", "svc.service", "restart", 1000).ok);
    CHECK(execute_effect(provider, journal, true, "svc", "svc.service", "restart", 1001).ok);
    CHECK_EQ(recovery.may_act("svc", policy, 1002).code, std::string("BUDGET_EXHAUSTED"));

    // A relaunch reads the same two attempts from disk and is still capped.
    const RecoveryLoaded loaded = load_from(path, clock_at(1002, 1770000000, "boot-a"));
    CHECK(loaded.ok);
    CHECK_EQ(loaded.state.attempts("svc").size(), std::size_t{2});
    CHECK_EQ(loaded.state.may_act("svc", policy, 1002).code,
             std::string("BUDGET_EXHAUSTED"));

    remove_dir(dir);
}

void test_without_the_lease_no_effect() {
    const std::string dir = make_temp_dir();
    CHECK(!dir.empty());
    if (dir.empty()) {
        return;
    }
    const std::string path = dir + "/state.json";

    Recovery recovery;
    ActionJournal journal = make_journal(path, &recovery,
                          [] { return clock_at(1000, 1770000000, "boot-a"); });
    InspectingProvider provider;
    provider.state_path = path;
    provider.attempt_id = "svc";

    const EffectExecution effect =
        execute_effect(provider, journal, false, "svc", "svc.service", "restart", 1000);
    CHECK(!effect.ok);
    CHECK(!effect.provider_called);
    CHECK(!provider.restart_called);
    CHECK(!state_has_attempt(path, "svc"));

    remove_dir(dir);
}

void test_the_write_is_bound_to_the_opened_directory() {
    // THE SYNTHETIC RACE. The directory is opened and verified, then the path's
    // parent is renamed and replaced by a symlink to a different directory. A
    // path-based writer would follow the symlink; the fd-bound writer must land
    // in the ORIGINAL inode and the post-write stability check must fail closed.
    const std::string root = make_temp_dir();
    CHECK(!root.empty());
    if (root.empty()) {
        return;
    }
    CHECK(::mkdir((root + "/state").c_str(), 0700) == 0);
    CHECK(::mkdir((root + "/evil").c_str(), 0700) == 0);
    const std::string path = root + "/state/state.json";

    StateDirectory directory;
    StatePathVerdict verdict;
    CHECK(directory.open(path, verdict));
    CHECK(directory.valid());

    CHECK(::rename((root + "/state").c_str(), (root + "/moved").c_str()) == 0);
    CHECK(::symlink((root + "/evil").c_str(), (root + "/state").c_str()) == 0);

    Recovery recovery;
    ActionJournal journal(std::move(directory), &recovery,
                          [] { return clock_at(1000, 1770000000, "boot-a"); });
    std::string error;
    CHECK(!journal.write_outcome(error));
    CHECK(!error.empty());
    CHECK_CONTAINS(error, "original inode");

    // The write went to the trusted inode, not to the symlink target.
    CHECK(file_exists(root + "/moved/state.json"));
    CHECK(!file_exists(root + "/evil/state.json"));

    ::unlink((root + "/state").c_str());  // the attacker's symlink
    ::unlink((root + "/moved/state.json").c_str());
    ::rmdir((root + "/moved").c_str());
    ::rmdir((root + "/evil").c_str());
    ::rmdir(root.c_str());
}

void test_the_read_is_bound_to_the_opened_directory() {
    // The same race on the READ side: a path-based load could read an
    // attacker-chosen empty state and reset the budget. The trusted fd must
    // still read the original inode.
    const std::string root = make_temp_dir();
    CHECK(!root.empty());
    if (root.empty()) {
        return;
    }
    CHECK(::mkdir((root + "/state").c_str(), 0700) == 0);
    CHECK(::mkdir((root + "/evil").c_str(), 0700) == 0);
    const std::string path = root + "/state/state.json";
    const std::string trusted =
        "{\"schema\":\"serveros/native-core-recovery.v2\",\"boot_id\":\"boot-a\","
        "\"anchor\":{\"monotonic\":100,\"wall_epoch\":1770000000},"
        "\"services\":[{\"id\":\"svc\",\"consecutive_failures\":0,"
        "\"opened_until\":0,\"attempts\":[10]}]}\n";
    {
        std::ofstream out(path, std::ios::binary | std::ios::trunc);
        out << trusted;
    }
    CHECK(::chmod(path.c_str(), 0600) == 0);

    StateDirectory directory;
    StatePathVerdict verdict;
    CHECK(directory.open(path, verdict));

    CHECK(::rename((root + "/state").c_str(), (root + "/moved").c_str()) == 0);
    CHECK(::symlink((root + "/evil").c_str(), (root + "/state").c_str()) == 0);
    {
        std::ofstream out(root + "/evil/state.json", std::ios::binary | std::ios::trunc);
        out << "{\"schema\":\"serveros/native-core-recovery.v2\",\"boot_id\":\"boot-a\","
               "\"anchor\":{\"monotonic\":1,\"wall_epoch\":1770000000},\"services\":[]}\n";
    }
    CHECK(::chmod((root + "/evil/state.json").c_str(), 0600) == 0);

    std::string text;
    bool present = false;
    std::string error;
    CHECK(directory.read(text, present, error));
    CHECK(present);
    CHECK(text == trusted);  // the trusted inode, not the symlink target
    CHECK_CONTAINS(text, "\"id\":\"svc\"");

    ::unlink((root + "/state").c_str());
    ::unlink((root + "/moved/state.json").c_str());
    ::rmdir((root + "/moved").c_str());
    ::unlink((root + "/evil/state.json").c_str());
    ::rmdir((root + "/evil").c_str());
    ::rmdir(root.c_str());
}

int main() {
    test_prewrite_is_durable_before_the_effect();
    test_prewrite_failure_has_no_effect();
    test_a_crash_after_the_effect_leaves_the_attempt();
    test_outcome_write_failure_keeps_the_attempt();
    test_cross_boot_places_the_attempt();
    test_a_second_start_cannot_exceed_the_budget();
    test_without_the_lease_no_effect();
    test_the_write_is_bound_to_the_opened_directory();
    test_the_read_is_bound_to_the_opened_directory();
    return check::report("test_sup_action_journal");
}
