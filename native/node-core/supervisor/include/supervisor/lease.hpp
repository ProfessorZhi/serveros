// One writer, or none.
//
// WHY `flock` AND NOT A PID FILE
// ------------------------------
// A pid file needs a rule for "the holder is gone", and every such rule is a
// guess: the pid may have been reused, the file may be left over from a
// different boot, the process may be stopped rather than dead. `flock(2)` has
// no such rule because it needs none — the kernel drops the lock when the
// holding file description closes, which happens when the process EXITS, dies,
// or is killed. A crashed writer therefore releases its own lease, and this
// module never has to decide whether a lock is stale.
//
// That is also why it never unlinks the lock file. Removing the path does not
// release the lock — the lock lives on the open file description — so unlinking
// a live writer's lock file leaves it holding a lock nobody can find, and the
// next writer creates a NEW file with a new inode and takes a lock that means
// nothing. Refusing to unlink is the same rule `status_socket.hpp` applies, and
// for the same reason.
//
// WHAT IT IS NOT
// --------------
// It does not wait. `acquire` either takes the lock or reports who has it; a
// caller that wants to retry owns the retry policy, because the useful waiting
// time is a property of what the caller is doing and not of this file.
//
// It is also not a distributed lock. It serialises writers on ONE host sharing
// ONE filesystem, which is exactly the failure it exists for: two Cores on one
// machine, each with its own budget, both believing they are the only writer.
#pragma once

#include <string>

namespace nodecore::supervisor {

//: The closed outcomes of an acquisition.
inline constexpr const char* kLeaseAcquired = "ACQUIRED";
inline constexpr const char* kLeaseHeldByAnother = "HELD_BY_ANOTHER";
inline constexpr const char* kLeaseUnavailable = "UNAVAILABLE";
//: `peek_holder` only. Reading a name is not acquiring anything, and reusing
//: ACQUIRED here would let a caller that ignores `ok` believe it holds the pen.
inline constexpr const char* kLeaseHolderRead = "HOLDER_READ";

struct LeaseAttempt {
    bool ok = false;
    //: kLeaseAcquired, kLeaseHeldByAnother, kLeaseUnavailable.
    std::string code;
    std::string reason;
    //: Who holds it, read from the file's first line. Empty when it could not be
    //: read — an unknown holder still refuses, it just cannot be named.
    std::string holder;
};

//: A held writer lease. Moveable, not copyable, and it releases on destruction
//: by closing the descriptor — which is also how it releases on a crash.
class WriterLease {
public:
    WriterLease() = default;
    ~WriterLease();
    WriterLease(const WriterLease&) = delete;
    WriterLease& operator=(const WriterLease&) = delete;
    WriterLease(WriterLease&& other) noexcept;
    WriterLease& operator=(WriterLease&& other) noexcept;

    //: Non-blocking. On success THIS object holds the lease until it is released
    //: or destroyed — a static factory returning only a verdict would open a
    //: descriptor with nowhere to live, which is a lock nobody can ever drop.
    //:
    //: `identity` is written into the file so another writer's refusal can NAME
    //: the holder; it is a role or a node ref, never a pid, an account name or a
    //: token. `path` is created if absent and never removed.
    LeaseAttempt acquire(const std::string& path, const std::string& identity);

    bool held() const { return descriptor_ >= 0; }
    int descriptor() const { return descriptor_; }

    void release();

private:
    int descriptor_ = -1;
};

//: What another writer would learn about the current holder, without taking the
//: lock. Read-only, and for diagnostics only: the LOCK is the authority, this is
//: a sentence. A caller must never use it to decide that the lock is free.
LeaseAttempt peek_holder(const std::string& path);

}  // namespace nodecore::supervisor
