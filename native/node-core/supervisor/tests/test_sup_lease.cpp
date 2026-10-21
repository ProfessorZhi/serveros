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
#include <filesystem>
#include <iostream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

#include "check.hpp"
#include "supervisor/lease.hpp"

using namespace nodecore::supervisor;

namespace {

//: Where a scratch directory may be created.
//:
//: `SERVEROS_TEST_TMPDIR` first — an owner-supplied sandbox, which is how a CI
//: job, or an account that is not the developer's, points this suite at a
//: directory it controls. Then the platform's own convention,
//: `std::filesystem::temp_directory_path()`, which reads TMPDIR and falls back
//: to the platform default.
//:
//: WHY THIS IS NOT A LITERAL PATH. The first version of this helper named a
//: directory inside one developer's home on one machine. On any other host that
//: parent does not exist, `mkdtemp` returns nullptr, `root` is empty, and every
//: CHECK in this file fails for a reason that has nothing to do with the lease.
//: A test that only runs on the machine it was written on is a test of the
//: machine — and this suite is meant to be run on another box, by a different
//: account, where that directory does not exist.
bool scratch_base(std::string& base, std::string& error) {
    const char* override = std::getenv("SERVEROS_TEST_TMPDIR");
    if (override != nullptr && override[0] != '\0') {
        base = override;
        return true;
    }
    std::error_code code;
    const std::filesystem::path temp = std::filesystem::temp_directory_path(code);
    if (code) {
        base.clear();
        error = "the platform's temp directory could not be named (" + code.message() + ")";
        return false;
    }
    base = temp.string();
    return true;
}

//: Create a private directory under `base`, or fail closed.
//:
//: The base is checked with `lstat`, which does **not** follow: a symlink in
//: that position would silently redirect every artefact this test writes to
//: somewhere else, and a fixture is exactly where that must be refused rather
//: than followed. Only a real directory is accepted.
//:
//: `mkdtemp` then creates the leaf with mode 0700, unique per call, so two runs
//: of this suite — or two tests in one run — cannot collide. On any failure this
//: returns false and the caller creates NOTHING: there is no fallback path,
//: because a fallback is how a test starts writing somewhere nobody chose.
bool make_scratch_in(const std::string& base, std::string& root, std::string& error) {
    root.clear();
    if (base.empty()) {
        error = "no scratch base was given";
        return false;
    }
    struct stat info;
    if (::lstat(base.c_str(), &info) != 0) {
        error = "the scratch base does not exist: " + base;
        return false;
    }
    if (S_ISLNK(info.st_mode)) {
        error = "the scratch base is a symlink, which would redirect what this test writes: " +
                base;
        return false;
    }
    if (!S_ISDIR(info.st_mode)) {
        error = "the scratch base is not a directory: " + base;
        return false;
    }
    const std::string pattern = base + "/srvos-lease-XXXXXX";
    std::vector<char> buffer(pattern.begin(), pattern.end());
    buffer.push_back('\0');
    char* made = ::mkdtemp(buffer.data());
    if (made == nullptr) {
        error = "a private directory could not be created under " + base;
        return false;
    }
    root = made;
    return true;
}

bool scratch_root(std::string& root, std::string& error) {
    root.clear();
    std::string base;
    if (!scratch_base(base, error)) {
        return false;
    }
    return make_scratch_in(base, root, error);
}

//: Remove a directory this test created, by name. Never recursive: everything
//: under it was created by name too, and a recursive remove is how a test
//: deletes something it did not create.
void remove_scratch(const std::string& root, const std::vector<std::string>& leaves) {
    for (const std::string& leaf : leaves) {
        ::unlink((root + "/" + leaf).c_str());
    }
    ::rmdir(root.c_str());
}

}  // namespace

//: The scratch-root contract itself. These are the cases that make the fix a
//: fix rather than a different literal: a sandbox the caller names is honoured,
//: a base that is missing, a symlink or unwritable is REFUSED WITHOUT CREATING
//: ANYTHING, and two calls never collide.
void test_the_scratch_root_is_portable() {
    std::cout << "the scratch root is portable and fails closed"

              << "\n";
    std::string base;
    std::string error;
    CHECK(scratch_base(base, error));
    CHECK(!base.empty());

    // With no override the base is the platform's own answer — which is the
    // property the literal path lacked. Asserted by equality rather than by
    // scanning for a home directory, because a CI that sets TMPDIR under a home
    // is still correct: the platform chose it.
    const char* saved = std::getenv("SERVEROS_TEST_TMPDIR");
    const std::string saved_value = saved == nullptr ? std::string() : std::string(saved);
    const bool had_override = saved != nullptr;

    ::unsetenv("SERVEROS_TEST_TMPDIR");
    std::string plain;
    std::string plain_error;
    CHECK(scratch_base(plain, plain_error));
    std::error_code code;
    // Equality with the platform's own answer IS the portability property, and
    // it needs no literal: a base named after one account would fail here on
    // any host where that account's home is somewhere else, which is the bug
    // this change exists to remove.
    CHECK_EQ(plain, std::filesystem::temp_directory_path(code).string());

    // An owner-supplied sandbox is honoured.
    std::string sandbox;
    CHECK(make_scratch_in(plain, sandbox, error));
    if (!sandbox.empty()) {
        CHECK(sandbox.rfind(plain + "/", 0) == 0);
        ::setenv("SERVEROS_TEST_TMPDIR", sandbox.c_str(), 1);
        std::string chosen;
        CHECK(scratch_base(chosen, plain_error));
        CHECK_EQ(chosen, sandbox);
        ::unsetenv("SERVEROS_TEST_TMPDIR");
    }

    {
        // A base that does not exist is REFUSED, and nothing is created
        // anywhere — there is no fallback to a path the test did not choose.
        const std::string missing = (sandbox.empty() ? plain : sandbox) + "/srvos-absent-base";
        std::string refused;
        std::string why;
        CHECK(!make_scratch_in(missing, refused, why));
        CHECK(refused.empty());
        CHECK(!why.empty());
        struct stat info;
        CHECK(::lstat(missing.c_str(), &info) != 0);
    }
    {
        // A SYMLINK in that position is refused rather than followed: it would
        // silently redirect everything this test writes.
        const std::string link = (sandbox.empty() ? plain : sandbox) + "/srvos-link-base";
        ::unlink(link.c_str());
        CHECK(::symlink((sandbox.empty() ? plain : sandbox).c_str(), link.c_str()) == 0);
        std::string refused;
        std::string why;
        CHECK(!make_scratch_in(link, refused, why));
        CHECK(refused.empty());
        CHECK_CONTAINS(why, "symlink");
        ::unlink(link.c_str());
    }
    {
        // An unwritable base is refused, and the refusal is not "created anyway
        // somewhere else".
        const std::string locked = (sandbox.empty() ? plain : sandbox) + "/srvos-locked-base";
        ::mkdir(locked.c_str(), 0700);
        ::chmod(locked.c_str(), 0500);
        std::string refused;
        std::string why;
        CHECK(!make_scratch_in(locked, refused, why));
        CHECK(refused.empty());
        ::chmod(locked.c_str(), 0700);
        ::rmdir(locked.c_str());
    }
    {
        // TWO CALLS, TWO DIRECTORIES. A fixture that reused one path would let
        // two runs of this suite overwrite each other's lock files, and the
        // failure would look like a lease bug.
        std::string first;
        std::string second;
        CHECK(make_scratch_in(sandbox.empty() ? plain : sandbox, first, error));
        CHECK(make_scratch_in(sandbox.empty() ? plain : sandbox, second, error));
        CHECK(!first.empty() && !second.empty());
        CHECK(first != second);
        remove_scratch(first, {});
        remove_scratch(second, {});
    }

    if (!sandbox.empty()) {
        remove_scratch(sandbox, {});
    }
    if (had_override) {
        ::setenv("SERVEROS_TEST_TMPDIR", saved_value.c_str(), 1);
    } else {
        ::unsetenv("SERVEROS_TEST_TMPDIR");
    }
}

int main() {
    test_the_scratch_root_is_portable();

    std::string root;
    std::string root_error;
    scratch_root(root, root_error);
    CHECK(!root.empty());
    if (root.empty()) {
        // The reason is printed rather than swallowed: an empty root is a
        // fixture problem, and a reader needs to know which one.
        std::fprintf(stderr, "no scratch root: %s\n", root_error.c_str());
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
    remove_scratch(root, {"a.lock", "b.lock", "c.lock", "d.lock", "e.lock", "f.lock",
                          "never-written.lock"});

    return check::report("test_sup_lease");
}
