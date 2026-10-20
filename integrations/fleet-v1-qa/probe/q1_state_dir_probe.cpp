// Q1 independent negative probe for the state-directory walk (#321/#327).
//
// Without Q1_HAS_HOOKS it compiles against #321 (open(path, verdict) only) and
// checks the group-writable/sticky policy. With -DQ1_HAS_HOOKS it also drives
// the fstatat/openat identity hooks added by #327.
//
// Expected-safe:
//   * group-writable no-sticky ancestor -> STATE_ANCESTOR_GROUP_WRITABLE;
//   * world-writable no-sticky ancestor -> STATE_ANCESTOR_WORLD_WRITABLE;
//   * group-writable STICKY 01770 owned by us + entry owned by us -> ok;
//   * /tmp (1777 sticky root-owned) as an ancestor -> ok;
//   * after_lookup swap of the parent -> STATE_PARENT_IDENTITY_CHANGED;
//   * after_file_lookup swap of the state file -> STATE_PATH_IDENTITY_CHANGED;
//   * post-open rename+symlink: write lands in the moved inode and returns false.

#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>

#include "supervisor/state_directory.hpp"

using nodecore::supervisor::StateDirectory;
using nodecore::supervisor::StatePathVerdict;
namespace sd = nodecore::supervisor;

static int failures = 0;
static int checks = 0;

static void check(const char* name, bool ok, const std::string& detail = "") {
    ++checks;
    if (!ok) {
        ++failures;
        std::printf("FAIL %s :: %s\n", name, detail.c_str());
    } else {
        std::printf("PASS %s :: %s\n", name, detail.c_str());
    }
}

static std::string mkdtemp_root() {
    char pattern[] = "/tmp/q1-state-dir-XXXXXX";
    char* made = ::mkdtemp(pattern);
    return made == nullptr ? std::string() : std::string(made);
}

static void write_file(const std::string& path, mode_t mode = 0600) {
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    out << "{}\n";
    out.close();
    ::chmod(path.c_str(), mode);
}

int main() {
    const std::string root = mkdtemp_root();
    if (root.empty()) {
        std::printf("FAIL could not create temp root\n");
        return 2;
    }
    ::mkdir((root + "/state").c_str(), 0700);

    {
        StatePathVerdict verdict;
        StateDirectory directory;
        const bool ok = directory.open(root + "/state/state.json", verdict);
        check("normal path under /tmp sticky ancestor is accepted",
              ok && verdict.code == std::string(sd::kStatePathOk),
              verdict.code + " " + verdict.reason);
    }

    // Group-writable, non-sticky ancestor.
    ::mkdir((root + "/gw").c_str(), 0700);
    ::chmod((root + "/gw").c_str(), 0770);
    ::mkdir((root + "/gw/sub").c_str(), 0700);
    {
        StatePathVerdict verdict;
        StateDirectory directory;
        const bool ok = directory.open(root + "/gw/sub/state.json", verdict);
#ifdef Q1_HAS_GROUP_CODE
        check("group-writable no-sticky ancestor is refused",
              !ok && verdict.code == std::string(sd::kStateAncestorGroupWritable),
              verdict.code + " " + verdict.reason);
#else
        // #321 (before #327) has no group-writable ancestor refusal; the
        // observation is expected to be USABLE and the check to FAIL.
        check("group-writable no-sticky ancestor is refused", !ok,
              verdict.code + " " + verdict.reason);
#endif
    }
    ::chmod((root + "/gw").c_str(), 0700);
    ::rmdir((root + "/gw/sub").c_str());
    ::rmdir((root + "/gw").c_str());

    // World-writable, non-sticky ancestor.
    ::mkdir((root + "/ww").c_str(), 0700);
    ::chmod((root + "/ww").c_str(), 0777);
    ::mkdir((root + "/ww/sub").c_str(), 0700);
    {
        StatePathVerdict verdict;
        StateDirectory directory;
        const bool ok = directory.open(root + "/ww/sub/state.json", verdict);
        check("world-writable no-sticky ancestor is refused",
              !ok && verdict.code == std::string(sd::kStateAncestorWorldWritable),
              verdict.code + " " + verdict.reason);
    }
    ::chmod((root + "/ww").c_str(), 0700);
    ::rmdir((root + "/ww/sub").c_str());
    ::rmdir((root + "/ww").c_str());

    // Group-writable STICKY ancestor owned by us, entry owned by us.
    ::mkdir((root + "/gs").c_str(), 0700);
    ::chmod((root + "/gs").c_str(), 01770);
    ::mkdir((root + "/gs/sub").c_str(), 0700);
    {
        StatePathVerdict verdict;
        StateDirectory directory;
        const bool ok = directory.open(root + "/gs/sub/state.json", verdict);
        check("group-writable sticky 01770 owned by us is accepted",
              ok && verdict.code == std::string(sd::kStatePathOk),
              verdict.code + " " + verdict.reason);
    }
    ::rmdir((root + "/gs/sub").c_str());
    ::chmod((root + "/gs").c_str(), 0700);
    ::rmdir((root + "/gs").c_str());

#ifdef Q1_HAS_HOOKS
    // fstatat parent vs openat parent identity swap.
    {
        bool swapped = false;
        const auto hook = [&](const std::string& component) {
            if (component == "state" && !swapped) {
                swapped = true;
                ::rename((root + "/state").c_str(), (root + "/moved").c_str());
                ::mkdir((root + "/state").c_str(), 0700);
            }
        };
        StatePathVerdict verdict;
        StateDirectory directory;
        const bool ok = directory.open(root + "/state/state.json", verdict, hook);
        check("in-between parent inode replacement is refused",
              !ok && verdict.code == std::string(sd::kStateParentIdentityChanged),
              verdict.code + " " + verdict.reason);
        ::rmdir((root + "/state").c_str());
        ::rmdir((root + "/moved").c_str());
        ::mkdir((root + "/state").c_str(), 0700);
    }

    // fstatat file vs openat file identity swap.
    write_file(root + "/state/state.json");
    {
        bool swapped = false;
        const auto file_hook = [&]() {
            if (!swapped) {
                swapped = true;
                ::unlink((root + "/state/state.json").c_str());
                write_file(root + "/state/state.json");
            }
        };
        StatePathVerdict verdict;
        StateDirectory directory;
        const bool ok = directory.open(root + "/state/state.json", verdict,
                                       {}, file_hook);
        check("final file inode switch is refused",
              !ok && verdict.code == std::string(sd::kStatePathIdentityChanged),
              verdict.code + " " + verdict.reason);
    }
#endif

    // Post-open rename+symlink: the write is fd-bound and the stability check
    // fails closed.
    {
        StatePathVerdict verdict;
        StateDirectory directory;
        const bool opened = directory.open(root + "/state/state.json", verdict);
        check("post-open fixture opened", opened, verdict.code);
        if (opened) {
            ::rename((root + "/state").c_str(), (root + "/moved2").c_str());
            ::mkdir((root + "/evil").c_str(), 0700);
            ::symlink((root + "/evil").c_str(), (root + "/state").c_str());
            std::string error;
            const bool wrote = directory.write_atomically("{\"x\":1}\n", error);
            check("post-open write reports failure", !wrote, error);
            check("write landed in the moved inode",
                  ::access((root + "/moved2/state.json").c_str(), F_OK) == 0, "");
            check("write did not follow the symlink",
                  ::access((root + "/evil/state.json").c_str(), F_OK) != 0, "");
            ::unlink((root + "/state").c_str());
            ::unlink((root + "/moved2/state.json").c_str());
            ::rmdir((root + "/moved2").c_str());
            ::rmdir((root + "/evil").c_str());
        }
    }

    // cleanup
    ::unlink((root + "/state/state.json").c_str());
    ::rmdir((root + "/state").c_str());
    ::rmdir(root.c_str());
    std::printf("{\"checks\": %d, \"failed\": %d}\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
