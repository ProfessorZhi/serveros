// The gate in front of a side effect: may this process ACT at all, and is the
// budget it will spend actually persistable?
//
// WHY THIS EXISTS
// ---------------
// `--enable-actions` used to require a lease, a link fact and an agent fact,
// but not `--state`. A caller could therefore enter the acting path with no
// persisted recovery budget: every process restart — and every reboot — began
// with a fresh three attempts, which is the repeated-restart cap being bypassed
// by omission rather than by a decision.
//
// This file pins the missing fact, the state path's structural checks, and the
// toy harness's invocation contract. It is all pure or filesystem-local: no
// service manager, no unit, no process is started.
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "check.hpp"
#include "supervisor/action_gate.hpp"

using namespace nodecore::supervisor;

namespace {

std::string make_temp_dir() {
    char pattern[] = "/tmp/serveros-sup-gate-XXXXXX";
    char* made = ::mkdtemp(pattern);
    return made == nullptr ? std::string() : std::string(made);
}

bool write_file(const std::string& path, const std::string& text, mode_t mode) {
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    if (!out) {
        return false;
    }
    out << text;
    out.close();
    return ::chmod(path.c_str(), mode) == 0;
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

//: Join Bash backslash-newline continuations so one invocation is one line.
std::string join_continuations(const std::string& text) {
    std::string out;
    for (std::size_t i = 0; i < text.size(); ++i) {
        if (text[i] == '\\' && i + 1 < text.size() && text[i + 1] == '\n') {
            out.push_back(' ');
            ++i;
        } else if (text[i] == '\\' && i + 2 < text.size() && text[i + 1] == '\r' &&
                   text[i + 2] == '\n') {
            out.push_back(' ');
            i += 2;
        } else {
            out.push_back(text[i]);
        }
    }
    return out;
}

// ---------------------------------------------------------------------------

void test_the_gate_requires_every_acting_fact() {
    {
        // Observe-only is not gated: there is no side effect to authorise.
        ActionGateInput input;
        input.act = false;
        const ActionGateVerdict verdict = check_action_gate(input);
        CHECK(verdict.ok);
        CHECK_EQ(verdict.code, std::string(kActionGateOk));
    }
    {
        ActionGateInput input;
        input.act = true;
        const ActionGateVerdict verdict = check_action_gate(input);
        CHECK(!verdict.ok);
        CHECK_EQ(verdict.code, std::string(kActionGateNeedsLease));
    }
    {
        ActionGateInput input;
        input.act = true;
        input.have_lease = true;
        const ActionGateVerdict verdict = check_action_gate(input);
        CHECK(!verdict.ok);
        CHECK_EQ(verdict.code, std::string(kActionGateNeedsLink));
    }
    {
        ActionGateInput input;
        input.act = true;
        input.have_lease = true;
        input.link_known = true;
        const ActionGateVerdict verdict = check_action_gate(input);
        CHECK(!verdict.ok);
        CHECK_EQ(verdict.code, std::string(kActionGateNeedsAgent));
    }
    {
        // THE GAP THIS FILE EXISTS FOR: everything except the budget.
        ActionGateInput input;
        input.act = true;
        input.have_lease = true;
        input.link_known = true;
        input.agent_known = true;
        const ActionGateVerdict verdict = check_action_gate(input);
        CHECK(!verdict.ok);
        CHECK_EQ(verdict.code, std::string(kActionGateNeedsState));
        CHECK_CONTAINS(verdict.reason, "budget");
        CHECK_CONTAINS(verdict.reason, "restart");
    }
    {
        ActionGateInput input;
        input.act = true;
        input.have_lease = true;
        input.link_known = true;
        input.agent_known = true;
        input.state_path = "state.json";
        const ActionGateVerdict verdict = check_action_gate(input);
        CHECK(!verdict.ok);
        CHECK_EQ(verdict.code, std::string(kActionGateStateNotAbsolute));
    }
    {
        ActionGateInput input;
        input.act = true;
        input.have_lease = true;
        input.link_known = true;
        input.agent_known = true;
        input.state_path = "/run/user/1000/serveros/recovery-state.json";
        const ActionGateVerdict verdict = check_action_gate(input);
        CHECK(verdict.ok);
        CHECK_EQ(verdict.code, std::string(kActionGateOk));
    }
}

void test_the_state_path_must_be_safe_to_write() {
    const std::string dir = make_temp_dir();
    CHECK(!dir.empty());
    if (dir.empty()) {
        return;
    }

    // An absent file in a 0700 directory is the normal first run.
    CHECK(check_state_path(dir + "/state.json").ok);

    CHECK_EQ(check_state_path("").code, std::string(kStatePathEmpty));
    CHECK_EQ(check_state_path("state.json").code, std::string(kStatePathNotAbsolute));
    const std::string missing =
        "/no-such-serveros-gate-" + std::to_string(static_cast<long>(::getpid())) + "/state.json";
    CHECK_EQ(check_state_path(missing).code, std::string(kStateParentMissing));

    // An existing 0600 regular file is usable.
    CHECK(write_file(dir + "/state.json", "{}", 0600));
    CHECK(check_state_path(dir + "/state.json").ok);

    // Group-writable file: another account can rewrite the budget.
    CHECK(::chmod((dir + "/state.json").c_str(), 0660) == 0);
    CHECK_EQ(check_state_path(dir + "/state.json").code,
             std::string(kStatePathGroupWritable));
    CHECK(::chmod((dir + "/state.json").c_str(), 0600) == 0);

    // A symlink at the state path is refused, never followed.
    CHECK(::symlink((dir + "/state.json").c_str(), (dir + "/link.json").c_str()) == 0);
    CHECK_EQ(check_state_path(dir + "/link.json").code, std::string(kStatePathSymlink));

    // A directory at the state path is not a state file.
    CHECK(::mkdir((dir + "/sub").c_str(), 0700) == 0);
    CHECK_EQ(check_state_path(dir + "/sub").code, std::string(kStatePathNotAFile));
    CHECK_EQ(check_state_path(dir + "/").code, std::string(kStatePathNotAFile));

    // A group-writable PARENT lets another account replace the file by rename,
    // so it is refused even when the file itself is 0600.
    CHECK(::chmod(dir.c_str(), 0770) == 0);
    CHECK_EQ(check_state_path(dir + "/other.json").code,
             std::string(kStateParentGroupWritable));
    CHECK_EQ(check_state_path(dir + "/state.json").code,
             std::string(kStateParentGroupWritable));
    CHECK(::chmod(dir.c_str(), 0700) == 0);

    // A parent we cannot write cannot gain a first-run state file.
    CHECK(::chmod(dir.c_str(), 0500) == 0);
    CHECK_EQ(check_state_path(dir + "/fresh.json").code, std::string(kStatePathUnwritable));
    // `renameat` needs the parent writable even when the file already exists.
    CHECK_EQ(check_state_path(dir + "/state.json").code, std::string(kStatePathUnwritable));
    CHECK(::chmod(dir.c_str(), 0700) == 0);

    // An INTERMEDIATE ancestor symlink is refused too. The immediate parent
    // (`dir/link/sub`) is a real directory reached through the link, so a check
    // of the immediate parent alone would miss it.
    CHECK(::mkdir((dir + "/real").c_str(), 0700) == 0);
    CHECK(::mkdir((dir + "/real/sub").c_str(), 0700) == 0);
    CHECK(::symlink((dir + "/real").c_str(), (dir + "/link").c_str()) == 0);
    CHECK_EQ(check_state_path(dir + "/link/sub/state.json").code,
             std::string(kStateAncestorSymlink));

    // A non-normalised path is refused rather than lexically reinterpreted.
    CHECK_EQ(check_state_path(dir + "/real/../state.json").code,
             std::string(kStatePathNotNormalized));

    // A world-writable ancestor WITHOUT the sticky bit lets any account rename
    // the entry. `/tmp` (1777) is allowed because only the owner can rename an
    // entry in a sticky directory; a bare 0777 directory is not.
    CHECK(::mkdir((dir + "/open").c_str(), 0700) == 0);
    CHECK(::chmod((dir + "/open").c_str(), 0777) == 0);
    CHECK(::mkdir((dir + "/open/sub").c_str(), 0700) == 0);
    CHECK_EQ(check_state_path(dir + "/open/sub/state.json").code,
             std::string(kStateAncestorWorldWritable));
    ::rmdir((dir + "/open/sub").c_str());
    CHECK(::chmod((dir + "/open").c_str(), 0700) == 0);
    ::rmdir((dir + "/open").c_str());

    ::unlink((dir + "/link").c_str());
    ::rmdir((dir + "/real/sub").c_str());
    ::rmdir((dir + "/real").c_str());
    ::unlink((dir + "/link.json").c_str());
    ::unlink((dir + "/state.json").c_str());
    ::rmdir((dir + "/sub").c_str());
    ::rmdir(dir.c_str());
}

void test_the_toy_harness_passes_the_budget() {
    // The opt-in REAL_TOY harness is the one committed caller of
    // `--enable-actions`. It must carry the same budget path the gate now
    // requires, or the documented manual recipe would be refused. This is a
    // source-level contract test: it does not run the harness or any unit.
    bool ok = false;
    const std::string script =
        read_file(std::string(SUPERVISOR_SOURCE_DIR) + "/tests/toy/service_recovery_toy.sh", ok);
    CHECK(ok);
    if (!ok) {
        return;
    }
    const std::string joined = join_continuations(script);
    std::istringstream lines(joined);
    std::string line;
    int invocations = 0;
    while (std::getline(lines, line)) {
        if (line.find("--enable-actions") == std::string::npos) {
            continue;
        }
        ++invocations;
        CHECK_CONTAINS(line, "--state");
        CHECK_CONTAINS(line, "--lease");
        CHECK_CONTAINS(line, "--link-state");
        CHECK_CONTAINS(line, "--agent-state");
    }
    CHECK(invocations > 0);
    CHECK_CONTAINS(script, "STATE=");
}

}  // namespace

int main() {
    test_the_gate_requires_every_acting_fact();
    test_the_state_path_must_be_safe_to_write();
    test_the_toy_harness_passes_the_budget();
    return check::report("test_sup_action_gate");
}
