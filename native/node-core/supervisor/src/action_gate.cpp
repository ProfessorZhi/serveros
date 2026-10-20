#include "supervisor/action_gate.hpp"

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <string>
#include <utility>

namespace nodecore::supervisor {
namespace {

ActionGateVerdict gate_refuse(const char* code, std::string reason) {
    ActionGateVerdict verdict;
    verdict.ok = false;
    verdict.code = code;
    verdict.reason = std::move(reason);
    return verdict;
}

ActionGateVerdict gate_ok(std::string reason) {
    ActionGateVerdict verdict;
    verdict.ok = true;
    verdict.code = kActionGateOk;
    verdict.reason = std::move(reason);
    return verdict;
}

StatePathVerdict path_refuse(const char* code, std::string reason) {
    StatePathVerdict verdict;
    verdict.ok = false;
    verdict.code = code;
    verdict.reason = std::move(reason);
    return verdict;
}

StatePathVerdict path_ok(std::string reason) {
    StatePathVerdict verdict;
    verdict.ok = true;
    verdict.code = kStatePathOk;
    verdict.reason = std::move(reason);
    return verdict;
}

std::string errno_text(int value) { return std::string(std::strerror(value)); }

}  // namespace

ActionGateVerdict check_action_gate(const ActionGateInput& input) {
    if (!input.act) {
        // Observe-only has no side effect to authorise, so the acting facts are
        // not required. The absence of a state path is not a refusal here.
        return gate_ok("actions are not enabled, so there is no side effect to authorise");
    }
    if (!input.have_lease) {
        return gate_refuse(
            kActionGateNeedsLease,
            "two Cores acting on one host each believe they own the recovery budget, and neither "
            "receipt shows it");
    }
    if (!input.link_known) {
        return gate_refuse(kActionGateNeedsLink,
                           "a restart cannot fix a host that is not connected, and this Core does "
                           "not know which it is");
    }
    if (!input.agent_known) {
        return gate_refuse(kActionGateNeedsAgent,
                           "an agent mid-task on a service is a reason to wait, and this Core does "
                           "not know whether one is");
    }
    if (input.state_path.empty()) {
        // THE FACT THIS UNIT EXISTS FOR. Without a persisted budget, a process
        // restart or a reboot grants a fresh set of attempts; the cap is then
        // bypassed by omitting a flag rather than by deciding to raise it.
        return gate_refuse(
            kActionGateNeedsState,
            "an acting Core must name a persistent --state file: without a persisted recovery "
            "budget, every process restart and every reboot starts with a fresh set of attempts, "
            "so the repeated-restart cap can be bypassed by omitting the path");
    }
    if (input.state_path[0] != '/') {
        return gate_refuse(
            kActionGateStateNotAbsolute,
            "\"" + input.state_path +
                "\" is not absolute; a relative state path would resolve against whatever "
                "directory the Core happened to start in, which is not a durable budget");
    }
    return gate_ok("every acting fact is present");
}

StatePathVerdict check_state_path(const std::string& path) {
    if (path.empty()) {
        return path_refuse(kStatePathEmpty, "no state path was given");
    }
    if (path[0] != '/') {
        return path_refuse(kStatePathNotAbsolute, "\"" + path + "\" is not absolute");
    }

    const std::size_t slash = path.find_last_of('/');
    const std::string parent = slash == 0 ? std::string("/") : path.substr(0, slash);
    const std::string base = path.substr(slash + 1);
    if (base.empty()) {
        return path_refuse(kStatePathNotAFile,
                           "\"" + path + "\" names a directory, not a state file");
    }

    // The PARENT is checked first, for both an existing and an absent file: a
    // group/other-writable directory lets another account replace the state by
    // rename, which defeats a 0600 file inside it.
    struct stat parent_info;
    if (::lstat(parent.c_str(), &parent_info) != 0) {
        const int error = errno;
        if (error == ENOENT) {
            return path_refuse(kStateParentMissing,
                               "the state directory \"" + parent + "\" does not exist");
        }
        return path_refuse(kStatePathUnusable, "the state directory \"" + parent +
                                                  "\" could not be inspected: " + errno_text(error));
    }
    if (S_ISLNK(parent_info.st_mode)) {
        return path_refuse(kStateParentSymlink,
                           "the state directory \"" + parent +
                               "\" is a symlink; a state file reached through a link is not "
                               "necessarily this account's");
    }
    if (!S_ISDIR(parent_info.st_mode)) {
        return path_refuse(kStateParentNotADirectory,
                           "the state parent \"" + parent + "\" is not a directory");
    }
    if (parent_info.st_uid != ::getuid()) {
        return path_refuse(kStateParentWrongOwner,
                           "the state directory \"" + parent + "\" is owned by another account");
    }
    if ((parent_info.st_mode & (S_IWGRP | S_IWOTH)) != 0) {
        return path_refuse(kStateParentGroupWritable,
                           "the state directory \"" + parent +
                               "\" is writable by group or other, so the state file can be "
                               "replaced by another account");
    }

    struct stat file_info;
    if (::lstat(path.c_str(), &file_info) == 0) {
        if (S_ISLNK(file_info.st_mode)) {
            return path_refuse(kStatePathSymlink,
                               "the state path \"" + path +
                                   "\" is a symlink; it is refused rather than followed");
        }
        if (!S_ISREG(file_info.st_mode)) {
            return path_refuse(kStatePathNotAFile,
                               "the state path \"" + path + "\" is not a regular file");
        }
        if (file_info.st_uid != ::getuid()) {
            return path_refuse(kStatePathWrongOwner,
                               "the state file \"" + path + "\" is owned by another account");
        }
        if ((file_info.st_mode & (S_IWGRP | S_IWOTH)) != 0) {
            return path_refuse(kStatePathGroupWritable,
                               "the state file \"" + path + "\" is writable by group or other");
        }
        const int fd = ::open(path.c_str(), O_RDWR | O_CLOEXEC | O_NOFOLLOW);
        if (fd < 0) {
            const int error = errno;
            return path_refuse(kStatePathUnwritable, "the state file \"" + path +
                                                         "\" is not writable: " +
                                                         errno_text(error));
        }
        ::close(fd);
        return path_ok("the existing state file is a regular, owned, non-shared writable file");
    }

    const int stat_error = errno;
    if (stat_error != ENOENT) {
        return path_refuse(kStatePathUnusable, "the state path \"" + path +
                                                   "\" could not be inspected: " +
                                                   errno_text(stat_error));
    }
    if (::access(parent.c_str(), W_OK | X_OK) != 0) {
        const int error = errno;
        return path_refuse(kStatePathUnwritable,
                           "the state directory \"" + parent +
                               "\" is not writable, so a first-run state file cannot be created: " +
                               errno_text(error));
    }
    return path_ok("the state file does not exist yet and its directory is writable");
}

}  // namespace nodecore::supervisor
