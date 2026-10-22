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
#include <utility>
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

std::string make_private_fixture() {
    // CI defaults to its isolated /tmp. Shared hosts must explicitly select an
    // admitted artifact/toolchain root and can run only this fixture via argv.
    const char* configured = std::getenv("SERVEROS_STATE_TEST_ROOT");
    const std::string root = configured ? configured : "/tmp";
    if (root.empty() || root.front() != '/' || root.find("/../") != std::string::npos)
        return {};
    const std::string name = root + "/serveros-private-ancestor-XXXXXX";
    std::vector<char> pattern(name.begin(), name.end());
    pattern.push_back('\0');
    char* made = ::mkdtemp(pattern.data());
    return made ? made : std::string();
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
    // These are public-prefix negatives. A 0700 mkdtemp root would establish a
    // legitimate private boundary around both writable intermediate fixtures.
    CHECK(::chmod(dir.c_str(), 0755) == 0);
    CHECK(::mkdir((dir + "/open").c_str(), 0700) == 0);
    CHECK(::chmod((dir + "/open").c_str(), 0777) == 0);
    CHECK(::mkdir((dir + "/open/sub").c_str(), 0700) == 0);
    CHECK_EQ(check_state_path(dir + "/open/sub/state.json").code,
             std::string(kStateAncestorWorldWritable));
    ::rmdir((dir + "/open/sub").c_str());
    CHECK(::chmod((dir + "/open").c_str(), 0700) == 0);
    ::rmdir((dir + "/open").c_str());

    // A GROUP-writable ancestor without the sticky bit lets any member of the
    // group rename the entry, so it is refused even though it is not
    // world-writable.
    CHECK(::mkdir((dir + "/gopen").c_str(), 0700) == 0);
    CHECK(::chmod((dir + "/gopen").c_str(), 0770) == 0);
    CHECK(::mkdir((dir + "/gopen/sub").c_str(), 0700) == 0);
    CHECK_EQ(check_state_path(dir + "/gopen/sub/state.json").code,
             std::string(kStateAncestorGroupWritable));
    ::rmdir((dir + "/gopen/sub").c_str());
    CHECK(::chmod((dir + "/gopen").c_str(), 0700) == 0);
    ::rmdir((dir + "/gopen").c_str());

    // A GROUP-writable STICKY ancestor is the one writable form that is safe:
    // sticky prevents other group members from renaming our entry, and both
    // the directory and the resolved entry are owned by us.
    CHECK(::mkdir((dir + "/gsticky").c_str(), 0700) == 0);
    CHECK(::chmod((dir + "/gsticky").c_str(), 01770) == 0);
    CHECK(::mkdir((dir + "/gsticky/sub").c_str(), 0700) == 0);
    CHECK(check_state_path(dir + "/gsticky/sub/state.json").ok);
    ::rmdir((dir + "/gsticky/sub").c_str());
    CHECK(::chmod((dir + "/gsticky").c_str(), 0700) == 0);
    ::rmdir((dir + "/gsticky").c_str());
    CHECK(::chmod(dir.c_str(), 0700) == 0);

    // `/tmp` itself is 1777 (world-writable + sticky, root-owned) and is the
    // ancestor of every temp root in this file. The positive checks prove the
    // sticky ancestor is preserved rather than refused.
    CHECK(check_state_path(dir + "/state.json").ok);

    ::unlink((dir + "/link").c_str());
    ::rmdir((dir + "/real/sub").c_str());
    ::rmdir((dir + "/real").c_str());
    ::unlink((dir + "/link.json").c_str());
    ::unlink((dir + "/state.json").c_str());
    ::rmdir((dir + "/sub").c_str());
    ::rmdir(dir.c_str());
}

void test_private_ancestors_are_fd_verified_and_revalidated() {
    const std::string root = make_private_fixture();
    CHECK(!root.empty());
    if (root.empty())
        return;
    CHECK(::chmod(root.c_str(), 0755) == 0);
    const std::string shared = root + "/public-shared";
    CHECK(::mkdir(shared.c_str(), 0700) == 0);
    CHECK(::chmod(shared.c_str(), 0775) == 0);
    CHECK(::mkdir((shared + "/parent").c_str(), 0700) == 0);
    CHECK_EQ(check_state_path(shared + "/parent/budget.json").code,
             std::string(kStateAncestorGroupWritable));
    CHECK(::chmod(shared.c_str(), 0777) == 0);
    CHECK_EQ(check_state_path(shared + "/parent/budget.json").code,
             std::string(kStateAncestorWorldWritable));
    CHECK(::chmod(shared.c_str(), 01777) == 0);
    CHECK(check_state_path(shared + "/parent/budget.json").ok);

    const std::string hidden = root + "/private";
    const std::string middle = hidden + "/serveros";
    const std::string parent = middle + "/native";
    const std::string budget = parent + "/budget.json";
    CHECK(::mkdir(hidden.c_str(), 0700) == 0);
    CHECK(::mkdir(middle.c_str(), 0700) == 0);
    CHECK(::chmod(middle.c_str(), 0775) == 0);
    CHECK(::mkdir(parent.c_str(), 0700) == 0);

    // Group search access is already enough to defeat a private boundary.
    CHECK(::chmod(hidden.c_str(), 0710) == 0);
    CHECK_EQ(check_state_path(budget).code, std::string(kStateAncestorGroupWritable));
    CHECK(::chmod(hidden.c_str(), 0700) == 0);
    bool weakened_during_open = false;
    StateDirectory opening;
    StatePathVerdict opening_verdict;
    CHECK(!opening.open(budget, opening_verdict, [&](const std::string& component) {
        if (component == "serveros") {
            weakened_during_open = true;
            CHECK(::chmod(hidden.c_str(), 0755) == 0);
        }
    }));
    CHECK(weakened_during_open);
    CHECK(!opening.valid());
    CHECK(::chmod(hidden.c_str(), 0700) == 0);

    StateDirectory directory;
    StatePathVerdict verdict;
    const bool admitted = directory.open(budget, verdict);
    CHECK(admitted);  // RED on 30f6fd7: 0700/private/0775/nonshared parent.
    if (admitted) {
        std::string error, text;
        bool present = false;
        CHECK(directory.write_atomically("{\"attempts\":1}", error));
        CHECK(directory.read(text, present, error));
        CHECK(present);
        CHECK_EQ(text, std::string("{\"attempts\":1}"));

        // Privacy is derived again from fstat; the old open/flag is insufficient.
        CHECK(::chmod(hidden.c_str(), 0755) == 0);
        CHECK(!directory.read(text, present, error));
        CHECK(!directory.write_atomically("weakened", error));
        CHECK(::chmod(hidden.c_str(), 0700) == 0);
        CHECK(directory.read(text, present, error));
        CHECK_EQ(text, std::string("{\"attempts\":1}"));

        // Even below a private boundary the immediate parent stays nonshared.
        CHECK(::chmod(parent.c_str(), 0775) == 0);
        CHECK(!directory.read(text, present, error));
        CHECK(!directory.write_atomically("shared-parent", error));
        CHECK_EQ(check_state_path(budget).code, std::string(kStateParentGroupWritable));
        CHECK(::chmod(parent.c_str(), 0700) == 0);

        // A real directory replacement after open is not a new authority.
        const std::string moved = root + "/original-private";
        CHECK(::rename(hidden.c_str(), moved.c_str()) == 0);
        CHECK(::mkdir(hidden.c_str(), 0700) == 0);
        CHECK(!directory.read(text, present, error));
        CHECK(!directory.write_atomically("replacement", error));
        bool original_ok = false;
        CHECK_EQ(read_file(moved + "/serveros/native/budget.json", original_ok),
                 std::string("{\"attempts\":1}"));
        CHECK(original_ok);
        CHECK(::rmdir(hidden.c_str()) == 0);
        CHECK(::rename(moved.c_str(), hidden.c_str()) == 0);

        // The anchored descriptors survive a move, with exactly one owner.
        StateDirectory transferred(std::move(directory));
        CHECK(!directory.valid());
        CHECK(transferred.read(text, present, error));
        CHECK_EQ(text, std::string("{\"attempts\":1}"));
        StateDirectory assigned;
        assigned = std::move(transferred);
        CHECK(!transferred.valid());
        CHECK(assigned.write_atomically("{\"attempts\":2}", error));
    }

    CHECK(::symlink(middle.c_str(), (hidden + "/alias").c_str()) == 0);
    CHECK_EQ(check_state_path(hidden + "/alias/native/budget.json").code,
             std::string(kStateAncestorSymlink));
    if (::geteuid() != 0) {
        // Real foreign-owned immediate parent, observed without writing to it.
        struct stat foreign {};
        CHECK(::stat("/", &foreign) == 0);
        CHECK(foreign.st_uid != ::geteuid());
        CHECK_EQ(check_state_path("/serveros-foreign-budget.json").code,
                 std::string(kStateParentWrongOwner));
    }

    // Fixtures are freshly created and owned by this test. Leave them under the
    // isolated runner for evidence rather than recursively deleting paths.
}

void test_an_in_between_inode_replacement_is_refused() {
    // The hook replaces the immediate parent with a NEW REAL directory between
    // fstatat and openat. This is not a symlink (O_NOFOLLOW would catch that);
    // only the dev+ino identity check can tell the opened inode from the one
    // that was inspected.
    const std::string root = make_temp_dir();
    CHECK(!root.empty());
    if (root.empty()) {
        return;
    }
    CHECK(::mkdir((root + "/state").c_str(), 0700) == 0);

    bool swapped = false;
    const auto after_lookup = [&](const std::string& component) {
        if (component == "state" && !swapped) {
            swapped = true;
            CHECK(::rename((root + "/state").c_str(), (root + "/moved").c_str()) == 0);
            CHECK(::mkdir((root + "/state").c_str(), 0700) == 0);
        }
    };

    StateDirectory directory;
    StatePathVerdict verdict;
    CHECK(!directory.open(root + "/state/state.json", verdict, after_lookup));
    CHECK_EQ(verdict.code, std::string(kStateParentIdentityChanged));
    CHECK(!directory.valid());

    ::rmdir((root + "/state").c_str());
    ::rmdir((root + "/moved").c_str());
    ::rmdir(root.c_str());
}

void test_a_final_file_lookup_switch_is_refused() {
    // The same identity check on the state file itself: fstatat sees one inode,
    // the writability probe opens another. The gate must fail closed.
    const std::string root = make_temp_dir();
    CHECK(!root.empty());
    if (root.empty()) {
        return;
    }
    CHECK(::mkdir((root + "/state").c_str(), 0700) == 0);
    CHECK(write_file(root + "/state/state.json", "{}", 0600));

    bool swapped = false;
    const auto after_file_lookup = [&] {
        if (!swapped) {
            swapped = true;
            // Keep the old inode alive. unlink+recreate can reuse its number
            // immediately on ext4, which would not exercise an inode switch.
            CHECK(::rename((root + "/state/state.json").c_str(),
                           (root + "/state/previous.json").c_str()) == 0);
            CHECK(write_file(root + "/state/state.json", "{\"different\":true}", 0600));
        }
    };

    StateDirectory directory;
    StatePathVerdict verdict;
    CHECK(!directory.open(root + "/state/state.json", verdict, {}, after_file_lookup));
    CHECK_EQ(verdict.code, std::string(kStatePathIdentityChanged));
    CHECK(!directory.valid());

    ::unlink((root + "/state/state.json").c_str());
    ::unlink((root + "/state/previous.json").c_str());
    ::rmdir((root + "/state").c_str());
    ::rmdir(root.c_str());
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

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--private-ancestor-only") {
        test_private_ancestors_are_fd_verified_and_revalidated();
        return check::report("test_sup_private_ancestor");
    }
    test_the_gate_requires_every_acting_fact();
    test_the_state_path_must_be_safe_to_write();
    test_an_in_between_inode_replacement_is_refused();
    test_a_final_file_lookup_switch_is_refused();
    test_the_toy_harness_passes_the_budget();
    test_private_ancestors_are_fd_verified_and_revalidated();
    return check::report("test_sup_action_gate");
}
