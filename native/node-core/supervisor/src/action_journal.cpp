#include "supervisor/action_journal.hpp"

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <string>
#include <utility>

namespace nodecore::supervisor {
namespace {

std::string errno_text(int value) { return std::string(std::strerror(value)); }

std::string parent_of(const std::string& path) {
    const std::size_t slash = path.find_last_of('/');
    return slash == 0 ? std::string("/") : path.substr(0, slash);
}

//: Write every byte, including a short write or an EINTR retry.
bool write_all(int fd, const std::string& text, std::string& error) {
    std::size_t offset = 0;
    while (offset < text.size()) {
        const ssize_t written = ::write(fd, text.data() + offset, text.size() - offset);
        if (written < 0) {
            if (errno == EINTR) {
                continue;
            }
            error = "the state temp file could not be written: " + errno_text(errno);
            return false;
        }
        offset += static_cast<std::size_t>(written);
    }
    return true;
}

//: A temp file in the SAME directory, then fsync the file, rename over the
//: final path, and fsync the directory. A crash leaves either the old complete
//: file or the new complete file, never a partially written one.
bool atomic_replace(const std::string& path, const std::string& text, std::string& error) {
    if (path.empty() || path.front() != '/') {
        error = "the state path is not absolute";
        return false;
    }
    const std::string directory = parent_of(path);
    //: `mkstemp` creates a unique 0600 file in the SAME directory and refuses
    //: an existing name. A pid+counter name could collide with a stale temp
    //: from a crashed process whose pid was reused.
    std::string temp = directory + "/.serveros-state.tmp.XXXXXX";
    const int fd = ::mkstemp(temp.data());
    if (fd < 0) {
        error = "the state temp file could not be created: " + errno_text(errno);
        return false;
    }
    // `mkstemp` does not set close-on-exec; this program does not exec, but the
    // descriptor is still closed on the paths below and marked the same way as
    // every other descriptor this tree opens.
    ::fcntl(fd, F_SETFD, FD_CLOEXEC);

    bool ok = write_all(fd, text, error);
    if (ok && ::fsync(fd) != 0) {
        error = "the state temp file could not be fsynced: " + errno_text(errno);
        ok = false;
    }
    if (::close(fd) != 0 && ok) {
        error = "the state temp file could not be closed: " + errno_text(errno);
        ok = false;
    }
    if (ok && ::rename(temp.c_str(), path.c_str()) != 0) {
        error = "the state file could not be replaced atomically: " + errno_text(errno);
        ok = false;
    }
    if (ok) {
        const int dirfd = ::open(directory.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
        if (dirfd < 0) {
            error = "the state directory could not be opened for fsync: " + errno_text(errno);
            ok = false;
        } else {
            if (::fsync(dirfd) != 0) {
                error = "the state directory could not be fsynced: " + errno_text(errno);
                ok = false;
            }
            ::close(dirfd);
        }
    }
    if (!ok) {
        ::unlink(temp.c_str());
    }
    return ok;
}

}  // namespace

ActionJournal::ActionJournal(std::string path, Recovery* recovery,
                             std::function<RecoveryClock()> clock)
    : path_(std::move(path)), recovery_(recovery), clock_(std::move(clock)) {}

bool ActionJournal::prewrite_attempt(const std::string& service_id, long now,
                                     std::string& error) {
    if (recovery_ == nullptr) {
        error = "the action journal has no recovery ledger";
        return false;
    }
    recovery_->record_attempt(service_id, now);
    return persist(error);
}

bool ActionJournal::write_outcome(std::string& error) { return persist(error); }

bool ActionJournal::persist(std::string& error) {
    if (recovery_ == nullptr) {
        error = "the action journal has no recovery ledger";
        return false;
    }
    if (!clock_) {
        error = "the action journal has no clock";
        return false;
    }
    const RecoveryClock clock = clock_();
    if (clock.boot_id.empty() || clock.wall_epoch <= 0 || clock.monotonic < 0) {
        error = "the boot/clock anchor is not usable";
        return false;
    }
    return atomic_replace(path_, recovery_->to_json(clock) + "\n", error);
}

EffectExecution execute_effect(Provider& provider, ActionJournal& journal, bool lease_held,
                               const std::string& service_id, const std::string& unit,
                               const std::string& verb, long now) {
    EffectExecution effect;
    if (!lease_held) {
        effect.error =
            "the writer lease is not held, so no effect may be issued or persisted";
        return effect;
    }
    if (verb != "start" && verb != "restart" && verb != "stop") {
        effect.error = "unsupported verb \"" + verb + "\"";
        return effect;
    }

    // THE ORDER THAT MATTERS: the attempt is durable BEFORE the side effect.
    std::string error;
    if (!journal.prewrite_attempt(service_id, now, error)) {
        effect.prewrite_failed = true;
        effect.error = error;
        return effect;
    }

    effect.provider_called = true;
    if (verb == "start") {
        effect.provider = provider.start(unit);
    } else if (verb == "restart") {
        effect.provider = provider.restart(unit);
    } else {
        effect.provider = provider.stop(unit);
    }
    effect.ok = true;
    return effect;
}

}  // namespace nodecore::supervisor
