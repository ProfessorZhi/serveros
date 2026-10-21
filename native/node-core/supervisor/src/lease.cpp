// The writer lease. See `lease.hpp` for why it is `flock` and not a pid file.
#include "supervisor/lease.hpp"

#include <cerrno>
#include <cstring>
#include <fstream>
#include <string>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <fcntl.h>
#include <unistd.h>

namespace nodecore::supervisor {

namespace {

//: The holder's own line, read without taking the lock. Best effort on purpose:
//: a holder that cannot be named still refuses.
std::string read_holder(const std::string& path) {
    std::ifstream file(path);
    if (!file) {
        return "";
    }
    std::string line;
    std::getline(file, line);
    // Trim the newline and any trailing whitespace, and refuse to carry an
    // unbounded line into a refusal message.
    const auto last = line.find_last_not_of(" \t\r\n");
    if (last == std::string::npos) {
        return "";
    }
    line = line.substr(0, last + 1);
    if (line.size() > 200) {
        line = line.substr(0, 200);
    }
    return line;
}

}  // namespace

WriterLease::~WriterLease() { release(); }

WriterLease::WriterLease(WriterLease&& other) noexcept : descriptor_(other.descriptor_) {
    other.descriptor_ = -1;
}

WriterLease& WriterLease::operator=(WriterLease&& other) noexcept {
    if (this != &other) {
        release();
        descriptor_ = other.descriptor_;
        other.descriptor_ = -1;
    }
    return *this;
}

void WriterLease::release() {
    if (descriptor_ >= 0) {
        // Closing the descriptor is what releases the lock, and it is also what
        // happens when this process dies without calling this function. The two
        // paths are the same path on purpose.
        ::close(descriptor_);
        descriptor_ = -1;
    }
}

LeaseAttempt WriterLease::acquire(const std::string& path, const std::string& identity) {
    release();  // re-acquiring through the same object drops what it held
    LeaseAttempt attempt;
    if (path.empty()) {
        attempt.code = kLeaseUnavailable;
        attempt.reason = "no lease path was given, so there is nothing to take";
        return attempt;
    }

    // 0600 and created if absent. O_CLOEXEC so a child — including the
    // `systemctl` this component runs — does not inherit and hold the lock,
    // which would keep it alive after the Core itself exited.
    // O_NOFOLLOW, because a symlink here is two writers holding two inodes and
    // both reporting a lock: `flock` on the link's target is a lock on a file
    // the other writer can bypass by opening the same path differently.
    const int descriptor =
        ::open(path.c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (descriptor < 0) {
        attempt.code = kLeaseUnavailable;
        attempt.reason = "the lease file at the given path could not be opened (" +
                         std::string(std::strerror(errno)) + ")";
        return attempt;
    }

    // The opened file must be a regular file this process owns with exactly one
    // link and no group/world write bit, and it must be the file the PATH names.
    // A descriptor that is not that is a lock on something else.
    {
        struct stat opened {};
        struct stat on_path {};
        std::string unsafe;
        if (::fstat(descriptor, &opened) != 0) {
            unsafe = "the lease descriptor could not be inspected";
        } else if (!S_ISREG(opened.st_mode)) {
            unsafe = "the lease is not a regular file";
        } else if (opened.st_uid != ::geteuid()) {
            unsafe = "the lease is owned by another uid";
        } else if (opened.st_nlink != 1) {
            unsafe = "the lease has more than one hard link";
        } else if ((opened.st_mode & (S_IWGRP | S_IWOTH)) != 0) {
            unsafe = "the lease is group- or world-writable";
        } else if (::lstat(path.c_str(), &on_path) != 0) {
            unsafe = "the lease path could not be inspected";
        } else if (S_ISLNK(on_path.st_mode)) {
            unsafe = "the lease path is a symlink";
        } else if (on_path.st_dev != opened.st_dev || on_path.st_ino != opened.st_ino) {
            unsafe = "the lease path does not name the file that was opened";
        }
        if (!unsafe.empty()) {
            ::close(descriptor);
            attempt.code = kLeaseUnavailable;
            attempt.reason = unsafe;
            return attempt;
        }
    }

    if (::flock(descriptor, LOCK_EX | LOCK_NB) != 0) {
        const int saved = errno;
        // Read the holder BEFORE closing: the content is still there, and the
        // close is what would end this attempt's association with the file.
        const std::string holder = read_holder(path);
        ::close(descriptor);
        if (saved == EWOULDBLOCK || saved == EAGAIN) {
            attempt.code = kLeaseHeldByAnother;
            attempt.holder = holder;
            attempt.reason =
                holder.empty()
                    ? "another writer holds the lease at the given path"
                    : "the lease is held by \"" + holder + "\"";
            return attempt;
        }
        attempt.code = kLeaseUnavailable;
        attempt.reason = "the lease could not be taken (" + std::string(std::strerror(saved)) + ")";
        return attempt;
    }

    path_ = path;
    // We hold it. Write who we are so the NEXT refusal can name us — truncated,
    // rewritten in place, and written after the lock so it cannot be read as a
    // claim by a writer that never held it.
    if (::ftruncate(descriptor, 0) == 0) {
        const std::string line = identity + "\n";
        ssize_t written = 0;
        while (written < static_cast<ssize_t>(line.size())) {
            const ssize_t n = ::write(descriptor, line.data() + written,
                                      line.size() - static_cast<std::size_t>(written));
            if (n <= 0) {
                break;
            }
            written += n;
        }
        ::fsync(descriptor);
    }

    descriptor_ = descriptor;
    attempt.ok = true;
    attempt.code = kLeaseAcquired;
    attempt.reason = "";
    return attempt;
}

LeaseAttempt peek_holder(const std::string& path) {
    LeaseAttempt attempt;
    const std::string holder = read_holder(path);
    if (holder.empty()) {
        attempt.code = kLeaseUnavailable;
        attempt.reason = "no holder could be read at the given path";
        return attempt;
    }
    attempt.ok = true;
    attempt.code = kLeaseHolderRead;
    attempt.holder = holder;
    attempt.reason = "the lease was last written by \"" + holder + "\"";
    return attempt;
}

LeaseAttempt WriterLease::revalidate() {
    LeaseAttempt attempt;
    if (descriptor_ < 0) {
        attempt.code = kLeaseUnavailable;
        attempt.reason = "no lease is held, so there is nothing to revalidate";
        return attempt;
    }
    if (path_.empty()) {
        attempt.code = kLeaseUnavailable;
        attempt.reason = "this lease was not acquired through a path";
        return attempt;
    }
    struct stat opened {};
    struct stat on_path {};
    std::string unsafe;
    if (::fstat(descriptor_, &opened) != 0) {
        unsafe = "the lease descriptor could not be inspected";
    } else if (!S_ISREG(opened.st_mode)) {
        unsafe = "the lease is no longer a regular file";
    } else if (opened.st_uid != ::geteuid()) {
        unsafe = "the lease is now owned by another uid";
    } else if (opened.st_nlink != 1) {
        unsafe = "the lease now has more than one hard link";
    } else if ((opened.st_mode & (S_IWGRP | S_IWOTH)) != 0) {
        unsafe = "the lease is now group- or world-writable";
    } else if (::lstat(path_.c_str(), &on_path) != 0) {
        unsafe = "the lease path has gone away since it was taken";
    } else if (S_ISLNK(on_path.st_mode)) {
        unsafe = "the lease path is now a symlink";
    } else if (on_path.st_dev != opened.st_dev || on_path.st_ino != opened.st_ino) {
        unsafe = "the lease path now names a different file than the one held";
    }
    if (!unsafe.empty()) {
        attempt.code = kLeaseHeldByAnother;
        attempt.reason = unsafe;
        return attempt;
    }
    attempt.ok = true;
    return attempt;
}

}  // namespace nodecore::supervisor
