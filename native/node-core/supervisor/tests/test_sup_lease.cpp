// The writer lease, against real `flock`.
//
// WHY THESE ARE NOT FAKES. The property under test is the kernel's, not this
// module's: that a lock taken on one file description excludes another, and that
// it is dropped when that description closes. A fake lock would test this file's
// own bookkeeping and prove nothing about the failure it exists to prevent, so
// these cases take real locks on a real file in a directory this test creates
// and removes.
//
// Two descriptors in ONE process contend under `flock`, because the lock belongs
// to the open file description and not to the process — which is what makes the
// two-writer case testable without a second process, and is also why the
// semantics differ from POSIX record locks.
#include <cstdio>
#include <cstdlib>
#include <string>
#include <unistd.h>

#include "check.hpp"
#include "supervisor/lease.hpp"

using namespace nodecore::supervisor;

namespace {

//: A directory this test owns, removed by name at the end. Never a recursive
//: remove over anything it did not create.
std::string scratch_root() {
    char pattern[] = "/data/home/vince/artifacts/lease-test-XXXXXX";
    const char* made = ::mkdtemp(pattern);
    return made == nullptr ? std::string() : std::string(made);
}

}  // namespace

int main() {
    const std::string root = scratch_root();
    CHECK(!root.empty());
    if (root.empty()) {
        return check::report("test_sup_lease");
    }

    {
        // A free lease is taken, and held by the object that took it.
        const std::string path = root + "/a.lock";
        WriterLease lease;
        const LeaseAttempt attempt = lease.acquire(path, "device:tokyo-shared-dev-01");
        CHECK(attempt.ok);
        CHECK_EQ(attempt.code, std::string(kLeaseAcquired));
        CHECK(lease.held());
        CHECK(lease.descriptor() >= 0);

        // And the holder is written down, so the NEXT refusal can name it.
        const LeaseAttempt holder = peek_holder(path);
        CHECK(holder.ok);
        CHECK_EQ(holder.code, std::string(kLeaseHolderRead));
        CHECK_EQ(holder.holder, std::string("device:tokyo-shared-dev-01"));
        lease.release();
        CHECK(!lease.held());
    }
    {
        // THE CASE THIS EXISTS FOR: while one writer holds it, a second is
        // refused AND TOLD WHO HAS IT. A refusal that cannot name the holder
        // sends a person looking at the wrong machine.
        const std::string path = root + "/b.lock";
        WriterLease first;
        CHECK(first.acquire(path, "core-a").ok);

        WriterLease second;
        const LeaseAttempt attempt = second.acquire(path, "core-b");
        CHECK(!attempt.ok);
        CHECK_EQ(attempt.code, std::string(kLeaseHeldByAnother));
        CHECK_EQ(attempt.holder, std::string("core-a"));
        CHECK_CONTAINS(attempt.reason, "core-a");
        CHECK(!second.held());
    }
    {
        // THE RECOVERY PATH, and the reason `flock` was chosen over a pid file:
        // when the holder releases — and equally when it CRASHES, which the
        // kernel makes the same event — the next writer proceeds. Nothing here
        // decides whether a lock is stale, because nothing has to.
        const std::string path = root + "/c.lock";
        WriterLease first;
        CHECK(first.acquire(path, "core-a").ok);

        WriterLease second;
        CHECK_EQ(second.acquire(path, "core-b").code, std::string(kLeaseHeldByAnother));

        first.release();
        const LeaseAttempt retake = second.acquire(path, "core-b");
        CHECK(retake.ok);
        CHECK(second.held());
        // The recorded holder is now the second writer, so a later refusal names
        // the current one rather than the one that left.
        CHECK_EQ(peek_holder(path).holder, std::string("core-b"));
    }
    {
        // Re-acquiring through the SAME object replaces what it held rather than
        // holding two locks: a writer that loops must not accumulate leases.
        // NOTE: the descriptor NUMBER is deliberately not asserted. `release`
        // closes it and `open` hands the lowest free one back, so the number is
        // usually reused — it is an implementation detail, not an identity. What
        // matters is that the object still holds a live, exclusive lock after
        // re-acquiring, which is asserted by asking a second writer.
        const std::string path = root + "/d.lock";
        WriterLease lease;
        CHECK(lease.acquire(path, "core-a").ok);
        CHECK(lease.acquire(path, "core-a").ok);
        CHECK(lease.held());
        CHECK_EQ(peek_holder(path).holder, std::string("core-a"));
        WriterLease other;
        CHECK_EQ(other.acquire(path, "core-b").code, std::string(kLeaseHeldByAnother));
    }
    {
        // Moving a lease keeps the lock, and the moved-from object holds nothing
        // — otherwise destroying it would release the lock the new owner holds.
        const std::string path = root + "/e.lock";
        WriterLease source;
        CHECK(source.acquire(path, "core-a").ok);
        WriterLease moved(std::move(source));
        CHECK(moved.held());
        CHECK(!source.held());
        // Still exclusive after the move.
        WriterLease other;
        CHECK_EQ(other.acquire(path, "core-b").code, std::string(kLeaseHeldByAnother));
    }
    {
        // An empty path is refused rather than silently taking nothing.
        WriterLease lease;
        const LeaseAttempt attempt = lease.acquire("", "x");
        CHECK(!attempt.ok);
        CHECK_EQ(attempt.code, std::string(kLeaseUnavailable));
        CHECK(!lease.held());
    }
    {
        // A path that cannot be opened — here, a directory — is UNAVAILABLE and
        // not "held": the two lead to different repairs.
        WriterLease lease;
        const LeaseAttempt attempt = lease.acquire(root, "x");
        CHECK(!attempt.ok);
        CHECK_EQ(attempt.code, std::string(kLeaseUnavailable));
        CHECK(!lease.held());
    }
    {
        // `peek_holder` on a path with nothing recorded is NOT a claim that the
        // lease is free. It reports that it could not read a holder, and a
        // caller must still take the lock to find out.
        const LeaseAttempt holder = peek_holder(root + "/never-written.lock");
        CHECK(!holder.ok);
        CHECK_EQ(holder.code, std::string(kLeaseUnavailable));
    }
    {
        // A long identity is bounded on read, so a refusal sentence cannot be
        // made arbitrarily large by whoever wrote the file last.
        const std::string path = root + "/f.lock";
        WriterLease lease;
        CHECK(lease.acquire(path, std::string(4000, 'x')).ok);
        const LeaseAttempt holder = peek_holder(path);
        CHECK(holder.ok);
        CHECK(holder.holder.size() <= 200);
    }

    // Remove exactly what this test created, by name.
    for (const char* name : {"/a.lock", "/b.lock", "/c.lock", "/d.lock", "/e.lock",
                             "/f.lock", "/never-written.lock"}) {
        ::unlink((root + name).c_str());
    }
    ::rmdir(root.c_str());

    return check::report("test_sup_lease");
}
