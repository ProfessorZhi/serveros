// A lease is an opened file and a currently proven, no-follow namespace.
#include "supervisor/lease.hpp"

#include <cerrno>
#include <cstring>
#include <string>
#include <sys/file.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <utility>

namespace nodecore::supervisor {
namespace {

// Diagnostic only: bound the read itself, not just the string after getline.
std::string read_holder(int fd) {
    char data[201];
    ssize_t count;
    do { count = ::pread(fd, data, sizeof(data), 0); } while (count < 0 && errno == EINTR);
    if (count <= 0) return {};
    std::string line(data, static_cast<std::size_t>(count));
    const auto newline = line.find('\n');
    if (newline != std::string::npos) line.resize(newline);
    if (line.size() > 200) line.resize(200);
    const auto last = line.find_last_not_of(" \t\r\n");
    if (last == std::string::npos) return {};
    line.resize(last + 1);
    return line;
}

std::string file_problem(int fd, int parent, const std::string& basename) {
    struct stat opened {}, entry {};
    if (::fstat(fd, &opened) != 0) return "the lease descriptor could not be inspected";
    if (!S_ISREG(opened.st_mode)) return "the lease is not a regular file";
    if (opened.st_uid != ::geteuid()) return "the lease is owned by another uid";
    if (opened.st_nlink != 1) return "the lease does not have exactly one hard link";
    if ((opened.st_mode & (S_IWGRP | S_IWOTH)) != 0)
        return "the lease is writable by group or other";
    if (::fstatat(parent, basename.c_str(), &entry, AT_SYMLINK_NOFOLLOW) != 0)
        return "the lease path could not be inspected";
    if (S_ISLNK(entry.st_mode)) return "the lease path is a symlink";
    if (opened.st_dev != entry.st_dev || opened.st_ino != entry.st_ino)
        return "the lease path does not name the held file";
    return {};
}

}  // namespace

WriterLease::~WriterLease() { release(); }

WriterLease::WriterLease(WriterLease&& other) noexcept
    : descriptor_(other.descriptor_), directory_(std::move(other.directory_)) {
    other.descriptor_ = -1;
}

WriterLease& WriterLease::operator=(WriterLease&& other) noexcept {
    if (this != &other) {
        release();
        descriptor_ = other.descriptor_;
        directory_ = std::move(other.directory_);
        other.descriptor_ = -1;
    }
    return *this;
}

void WriterLease::release() {
    if (descriptor_ >= 0) ::close(descriptor_);
    descriptor_ = -1;
    directory_ = StateDirectory{};
}

LeaseAttempt WriterLease::acquire(const std::string& path, const std::string& identity) {
    release();
    LeaseAttempt attempt;
    StatePathVerdict verdict;
    // Reuse the canonical FD walk and its private-boundary policy. Keeping ALL
    // ancestry is specific to leases; ordinary state I/O keeps its old contract.
    if (!directory_.open(path, verdict, {}, {}, true)) {
        attempt.code = kLeaseUnavailable;
        attempt.reason = verdict.reason;
        return attempt;
    }
    const int fd = ::openat(directory_.fd(), directory_.basename().c_str(),
                            O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) {
        attempt.code = kLeaseUnavailable;
        attempt.reason = "the lease could not be opened: " + std::string(std::strerror(errno));
        release();
        return attempt;
    }
    const std::string unsafe = file_problem(fd, directory_.fd(), directory_.basename());
    if (!unsafe.empty()) {
        ::close(fd);
        attempt.code = kLeaseUnavailable;
        attempt.reason = unsafe;
        release();
        return attempt;
    }
    if (::flock(fd, LOCK_EX | LOCK_NB) != 0) {
        const int saved = errno;
        attempt.holder = read_holder(fd);
        ::close(fd);
        attempt.code = saved == EWOULDBLOCK || saved == EAGAIN
                           ? kLeaseHeldByAnother : kLeaseUnavailable;
        attempt.reason = attempt.code == kLeaseHeldByAnother
                             ? (attempt.holder.empty() ? "another writer holds the lease"
                                : "the lease is held by \"" + attempt.holder + "\"")
                             : "the lease could not be taken: " + std::string(std::strerror(saved));
        release();
        return attempt;
    }
    descriptor_ = fd;
    // A namespace change during open/flock must refuse BEFORE truncating any
    // holder metadata. The lock protects this fd; ancestry binds it to the path.
    attempt = revalidate();
    if (!attempt.ok) {
        release();
        return attempt;
    }
    std::string holder = identity + "\n";
    if (::ftruncate(fd, 0) != 0 || ::lseek(fd, 0, SEEK_SET) < 0) {
        attempt.ok = false;
        attempt.code = kLeaseUnavailable;
        attempt.reason = "the lease holder could not be recorded";
        release();
        return attempt;
    }
    std::size_t offset = 0;
    while (offset < holder.size()) {
        const ssize_t count = ::write(fd, holder.data() + offset, holder.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) {
            attempt.ok = false;
            attempt.code = kLeaseUnavailable;
            attempt.reason = "the lease holder could not be recorded";
            release();
            return attempt;
        }
        offset += static_cast<std::size_t>(count);
    }
    attempt.code = kLeaseAcquired;
    attempt.reason = "this writer holds the lease";
    attempt.holder = identity;
    return attempt;
}

LeaseAttempt WriterLease::revalidate() {
    LeaseAttempt attempt;
    attempt.code = kLeaseUnavailable;
    if (descriptor_ < 0) {
        attempt.reason = "no lease is held";
        return attempt;
    }
    if (!directory_.namespace_current(attempt.reason)) return attempt;
    attempt.reason = file_problem(descriptor_, directory_.fd(), directory_.basename());
    if (!attempt.reason.empty()) return attempt;
    attempt.ok = true;
    attempt.code = kLeaseAcquired;
    return attempt;
}

LeaseAttempt peek_holder(const std::string& path) {
    LeaseAttempt attempt;
    attempt.code = kLeaseUnavailable;
    StateDirectory directory;
    StatePathVerdict verdict;
    if (!directory.open(path, verdict, {}, {}, true)) {
        attempt.reason = verdict.reason;
        return attempt;
    }
    const int fd = ::openat(directory.fd(), directory.basename().c_str(),
                            O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) {
        attempt.reason = "no recorded lease holder could be read";
        return attempt;
    }
    std::string error;
    if (directory.namespace_current(error) && file_problem(fd, directory.fd(), directory.basename()).empty())
        attempt.holder = read_holder(fd);
    ::close(fd);
    if (attempt.holder.empty()) {
        attempt.reason = "no recorded lease holder could be read";
        return attempt;
    }
    attempt.ok = true;
    attempt.code = kLeaseHolderRead;
    attempt.reason = "the lease was last written by \"" + attempt.holder + "\"";
    return attempt;
}

}  // namespace nodecore::supervisor
