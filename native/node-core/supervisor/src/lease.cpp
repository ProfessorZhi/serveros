// The writer lease. See `lease.hpp` for why it is `flock` and not a pid file.
#include "supervisor/lease.hpp"

#include <cerrno>
#include <cstring>
#include <fstream>
#include <string>
#include <sys/file.h>
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
    const int descriptor =
        ::open(path.c_str(), O_RDWR | O_CREAT | O_CLOEXEC, 0600);
    if (descriptor < 0) {
        attempt.code = kLeaseUnavailable;
        attempt.reason = "the lease file at the given path could not be opened (" +
                         std::string(std::strerror(errno)) + ")";
        return attempt;
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

}  // namespace nodecore::supervisor
