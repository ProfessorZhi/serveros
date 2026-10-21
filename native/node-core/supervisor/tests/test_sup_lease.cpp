// The writer lease, against real `flock` — and the fixture's own safety.
//
// WHY THESE ARE NOT FAKES. The property under test in the lease cases is the
// kernel's, not this module's: that a lock taken on one file description
// excludes another, and that it is dropped when that description closes. A fake
// lock would test this file's own bookkeeping and prove nothing about the failure
// it exists to prevent, so these cases take real locks on a real file in a
// directory this test creates and removes.
//
// Two descriptors in ONE process contend under `flock`, because the lock belongs
// to the open file description and not to the process — which is what makes the
// two-writer case testable without a second process, and is also why the
// semantics differ from POSIX record locks.
//
// THE SCRATCH DIRECTORY IS FD-ANCHORED, AND THAT IS A SECURITY PROPERTY HERE.
// An earlier revision resolved the base with `lstat` and then handed the PATH to
// `mkdtemp`. Two things were wrong with that, and an independent review found
// both:
//
//   * `lstat` looked only at the FINAL component, so an intermediate symlink was
//     followed. `SERVEROS_TEST_TMPDIR=/sandbox/outerlink/sub`, where `outerlink`
//     is a symlink, was accepted and the leaf was created THROUGH the link — in
//     a directory the caller never named.
//   * the check and the create were two separate resolutions of the same string,
//     so replacing the base with a symlink in between redirected the create.
//
// A fixture whose stated purpose is to refuse a redirecting symlink cannot make
// that claim while doing either. So every component is now opened with
// `O_DIRECTORY | O_NOFOLLOW` relative to the previous one, the leaf is created
// with `mkdirat` on that pinned descriptor, and deletion is `unlinkat` on the
// same descriptor. A path that is replaced after validation cannot redirect a
// create or a delete, because neither uses the path.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <filesystem>
#include <iostream>
#include <random>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

#include "check.hpp"
#include "supervisor/lease.hpp"

using namespace nodecore::supervisor;

namespace {

//: A scratch directory this test owns, pinned by descriptor.
//:
//: `path` exists for diagnostics and for the lease module, which takes a path —
//: that is another owner's interface and this file does not change it. Nothing
//: in the CREATE or DELETE path uses `path`.
struct Scratch {
    int base_fd = -1;   // the validated base, O_DIRECTORY | O_NOFOLLOW, every component
    int leaf_fd = -1;   // the private leaf, 0700
    std::string leaf_name;
    std::string path;   // full path to the leaf, for the lease module only
    std::string error;
    bool ok = false;
};

bool absolute_components(const std::string& path, std::vector<std::string>& parts,
                         std::string& error) {
    parts.clear();
    if (path.empty() || path.front() != '/') {
        // A RELATIVE OVERRIDE IS REFUSED. It would be resolved against the test's
        // current directory, which the caller did not name and which a CI job
        // does not control — the same class of mistake as a hardcoded path,
        // wearing a variable's clothes.
        error = "the scratch base is not an absolute path: " + path;
        return false;
    }
    std::string current;
    for (const char c : path) {
        if (c != '/') {
            current.push_back(c);
            continue;
        }
        if (current.empty()) {
            continue;  // "//" and the leading "/" are separators, not components
        }
        if (current == "." || current == "..") {
            // `O_NOFOLLOW` does not refuse `..`: it is a real directory entry
            // that walks out of the tree. Refused by name, because a base that
            // climbs is not a base.
            error = "the scratch base contains a \"" + current + "\" component: " + path;
            return false;
        }
        parts.push_back(current);
        current.clear();
    }
    if (!current.empty()) {
        if (current == "." || current == "..") {
            error = "the scratch base contains a \"" + current + "\" component: " + path;
            return false;
        }
        parts.push_back(current);
    }
    if (parts.empty()) {
        error = "the scratch base is the filesystem root, which is not a scratch directory";
        return false;
    }
    return true;
}

//: Open `path` as a directory, refusing a symlink at EVERY component.
//:
//: Each step is `openat` relative to the descriptor from the previous step, so
//: the thing validated is the thing walked: a component replaced after it was
//: opened cannot affect the next step, because the next step does not re-resolve
//: the prefix.
int open_absolute_directory(const std::string& path, std::string& error) {
    std::vector<std::string> parts;
    if (!absolute_components(path, parts, error)) {
        return -1;
    }
    int fd = ::open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fd < 0) {
        error = std::string("the filesystem root could not be opened (") + std::strerror(errno) + ")";
        return -1;
    }
    for (const std::string& part : parts) {
        const int next = ::openat(fd, part.c_str(),
                                  O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        const int saved = errno;
        // Inspect the entry BEFORE closing its parent: `fstatat` needs the
        // directory descriptor, and a closed one only reports EBADF — which
        // would silently make every refusal look like "not a directory".
        struct stat looked;
        const bool looked_ok =
            ::fstatat(fd, part.c_str(), &looked, AT_SYMLINK_NOFOLLOW) == 0;
        ::close(fd);
        if (next < 0) {
            // `O_NOFOLLOW` on a symlink returns ELOOP, but with `O_DIRECTORY`
            // the kernel may report ENOTDIR first — either way the entry is
            // refused, and the REASON should name what is actually there rather
            // than whichever errno the kernel happened to check first.
            const bool is_link = looked_ok && S_ISLNK(looked.st_mode);
            if (saved == ELOOP || is_link) {
                error = "\"" + part + "\" is a symlink, and a scratch base may not be reached "
                        "through one";
            } else if (saved == ENOENT) {
                error = "the scratch base does not exist at \"" + part + "\"";
            } else if (saved == ENOTDIR) {
                error = "\"" + part + "\" is not a directory";
            } else {
                error = "\"" + part + "\" could not be opened as a directory (" +
                        std::strerror(saved) + ")";
            }
            return -1;
        }
        fd = next;
    }
    return fd;
}

//: A random leaf name. Collision-resistant rather than unpredictable: a second
//: run of this suite must not land on the first run's directory, and that is all
//: this needs to be.
std::string random_leaf_name() {
    std::random_device device;
    static const char* hex = "0123456789abcdef";
    std::string name = "srvos-lease-";
    for (int index = 0; index < 16; ++index) {
        name.push_back(hex[device() % 16]);
    }
    return name;
}

//: Create the private leaf under a pinned base descriptor.
bool make_leaf(Scratch& scratch) {
    for (int attempt = 0; attempt < 32; ++attempt) {
        const std::string name = random_leaf_name();
        // `mkdirat` on the PINNED descriptor: the create cannot be redirected by
        // anything that happens to the base's path afterwards.
        if (::mkdirat(scratch.base_fd, name.c_str(), 0700) != 0) {
            if (errno == EEXIST) {
                continue;
            }
            scratch.error = std::string("a private directory could not be created (") +
                            std::strerror(errno) + ")";
            return false;
        }
        const int leaf =
            ::openat(scratch.base_fd, name.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (leaf < 0) {
            scratch.error = std::string("the private directory could not be opened (") +
                            std::strerror(errno) + ")";
            ::unlinkat(scratch.base_fd, name.c_str(), AT_REMOVEDIR);
            return false;
        }
        struct stat info;
        if (::fstat(leaf, &info) != 0) {
            scratch.error = std::string("the private directory could not be inspected (") +
                            std::strerror(errno) + ")";
            ::close(leaf);
            ::unlinkat(scratch.base_fd, name.c_str(), AT_REMOVEDIR);
            return false;
        }
        // ASSERTED, not assumed: `mkdirat` was asked for 0700 and the umask can
        // only remove bits, but a fixture that claims a private directory should
        // prove it is one.
        if ((info.st_mode & 0777) != 0700) {
            scratch.error = "the private directory is not 0700";
            ::close(leaf);
            ::unlinkat(scratch.base_fd, name.c_str(), AT_REMOVEDIR);
            return false;
        }
        scratch.leaf_fd = leaf;
        scratch.leaf_name = name;
        scratch.path = scratch.path.empty() ? name : scratch.path + "/" + name;
        return true;
    }
    scratch.error = "a unique private directory could not be named";
    return false;
}

//: Where a scratch base may come from, and how the leaf is made under it.
//:
//: `SERVEROS_TEST_TMPDIR` first — an owner-supplied sandbox, which is how a CI
//: job, or an account that is not the developer's, points this suite at a
//: directory it controls. Then the platform's own answer,
//: `std::filesystem::temp_directory_path()`. Neither is assumed to exist and
//: neither is a literal: a test that only runs on its author's machine is a test
//: of the machine.
bool scratch_base_path(std::string& base, std::string& error) {
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

//: Open `base` fd-anchored and create a leaf under it. On failure nothing is
//: created anywhere and both descriptors are closed.
bool open_scratch_at(const std::string& base, Scratch& scratch) {
    scratch = Scratch();
    scratch.base_fd = open_absolute_directory(base, scratch.error);
    if (scratch.base_fd < 0) {
        return false;
    }
    scratch.path = base;
    if (!make_leaf(scratch)) {
        ::close(scratch.base_fd);
        scratch.base_fd = -1;
        scratch.path.clear();
        return false;
    }
    scratch.ok = true;
    return true;
}

bool open_default_scratch(Scratch& scratch) {
    std::string base;
    if (!scratch_base_path(base, scratch.error)) {
        return false;
    }
    return open_scratch_at(base, scratch);
}

//: Remove the leaf and everything named in `leaves`, ALL of it relative to the
//: pinned descriptors. A base path that has been replaced since validation
//: cannot redirect a delete, because the delete never resolves the path.
void release_scratch(Scratch& scratch, const std::vector<std::string>& leaves) {
    for (const std::string& leaf : leaves) {
        if (scratch.leaf_fd >= 0) {
            ::unlinkat(scratch.leaf_fd, leaf.c_str(), 0);
        }
    }
    if (scratch.leaf_fd >= 0) {
        ::close(scratch.leaf_fd);
        scratch.leaf_fd = -1;
    }
    if (scratch.base_fd >= 0 && !scratch.leaf_name.empty()) {
        ::unlinkat(scratch.base_fd, scratch.leaf_name.c_str(), AT_REMOVEDIR);
    }
    if (scratch.base_fd >= 0) {
        ::close(scratch.base_fd);
        scratch.base_fd = -1;
    }
    scratch.ok = false;
}

//: How many entries a descriptor's directory currently holds, "." and ".."
//: included. Used to prove a refused case created nothing: the count before and
//: after must be identical.
bool entry_count(int dir_fd, int& count) {
    // A FRESH open file description, not `dup`. `dup` shares the directory
    // offset with the original descriptor, so a second pass would resume at the
    // end and report zero entries — which would make a "no leftovers" assertion
    // pass for the wrong reason the first time and fail the second.
    const int fresh = ::openat(dir_fd, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fresh < 0) {
        return false;
    }
    DIR* dir = ::fdopendir(fresh);
    if (dir == nullptr) {
        ::close(fresh);
        return false;
    }
    count = 0;
    while (::readdir(dir) != nullptr) {
        ++count;
    }
    ::closedir(dir);  // closes `fresh`
    return true;
}


// --- the file the lease is allowed to be -----------------------------------
//
// `flock` proves a descriptor, not a path. These four cases are the ways the
// path stops meaning the descriptor: a symlink, a hard link, a world-writable
// file, and a file replaced underneath a held lock.

std::string scratch_file(const char* name) {
    char templ[] = "/tmp/serveros-lease-safe-XXXXXX";
    const char* made = ::mkdtemp(templ);
    if (made == nullptr) {
        return "";
    }
    return std::string(made) + "/" + name;
}

void test_a_symlinked_lease_is_refused() {
    const std::string real = scratch_file("real");
    if (real.empty()) return;
    ::close(::open(real.c_str(), O_RDWR | O_CREAT | O_CLOEXEC, 0600));
    const std::string link = real + ".link";
    if (::symlink(real.c_str(), link.c_str()) != 0) return;
    WriterLease lease;
    const LeaseAttempt attempt = lease.acquire(link, "test");
    CHECK(!attempt.ok);
    CHECK(!lease.held());
}

void test_a_hardlinked_lease_is_refused() {
    const std::string real = scratch_file("real");
    if (real.empty()) return;
    ::close(::open(real.c_str(), O_RDWR | O_CREAT | O_CLOEXEC, 0600));
    if (::link(real.c_str(), (real + ".second").c_str()) != 0) return;
    WriterLease lease;
    const LeaseAttempt attempt = lease.acquire(real, "test");
    CHECK(!attempt.ok);
    CHECK(!lease.held());
}

void test_a_world_writable_lease_is_refused() {
    const std::string real = scratch_file("real");
    if (real.empty()) return;
    ::close(::open(real.c_str(), O_RDWR | O_CREAT | O_CLOEXEC, 0600));
    ::chmod(real.c_str(), 0666);
    WriterLease lease;
    const LeaseAttempt attempt = lease.acquire(real, "test");
    CHECK(!attempt.ok);
    CHECK(!lease.held());
}

void test_a_replaced_lease_fails_revalidation() {
    const std::string real = scratch_file("real");
    if (real.empty()) return;
    WriterLease lease;
    const LeaseAttempt taken = lease.acquire(real, "test");
    CHECK(taken.ok);
    CHECK(lease.held());
    // Rename the held file away and put a fresh one at the path: the descriptor
    // still holds a lock, on an inode nobody else will ever open.
    ::rename(real.c_str(), (real + ".moved").c_str());
    const int replacement = ::open(real.c_str(), O_RDWR | O_CREAT | O_CLOEXEC, 0600);
    if (replacement >= 0) ::close(replacement);
    const LeaseAttempt again = lease.revalidate();
    CHECK(!again.ok);
    lease.release();
}

}  // namespace

//: The scratch-root contract. These are the cases an independent review asked
//: for, and each one is a way the previous revision could have been wrong.
void test_the_scratch_root_is_fd_anchored() {
    std::cout << "the scratch root is fd-anchored and fails closed\n";

    std::string base;
    std::string error;
    CHECK(scratch_base_path(base, error));
    CHECK(!base.empty());

    // With no override the base is the platform's own answer. Asserted by
    // EQUALITY rather than by scanning for a home directory: a CI that sets
    // TMPDIR under a home is still correct, because the platform chose it.
    const char* saved = std::getenv("SERVEROS_TEST_TMPDIR");
    const std::string saved_value = saved == nullptr ? std::string() : std::string(saved);
    const bool had_override = saved != nullptr;

    ::unsetenv("SERVEROS_TEST_TMPDIR");
    std::string plain;
    std::string plain_error;
    CHECK(scratch_base_path(plain, plain_error));
    std::error_code code;
    CHECK_EQ(plain, std::filesystem::temp_directory_path(code).string());

    // A sandbox this test owns, to hold the negative cases.
    Scratch sandbox;
    CHECK(open_default_scratch(sandbox));
    if (!sandbox.ok) {
        std::fprintf(stderr, "no sandbox: %s\n", sandbox.error.c_str());
        return;
    }
    {
        // A base that exists and is a real directory is accepted, and the leaf
        // is created UNDER it.
        Scratch scratch;
        CHECK(open_scratch_at(sandbox.path, scratch));
        CHECK(scratch.ok);
        CHECK(scratch.path.rfind(sandbox.path + "/", 0) == 0);
        struct stat info;
        CHECK(::fstat(scratch.leaf_fd, &info) == 0);
        CHECK_EQ(static_cast<int>(info.st_mode & 0777), 0700);
        release_scratch(scratch, {});
    }
    {
        // F1 — AN INTERMEDIATE SYMLINK IS REFUSED. The previous revision checked
        // only the last component, so `outerlink/sub` was accepted and the leaf
        // was created through the link. Nothing may be created now.
        const std::string link = sandbox.path + "/outerlink";
        const std::string real = sandbox.path + "/realdir";
        ::mkdirat(sandbox.leaf_fd, "realdir", 0700);
        CHECK(::symlinkat("realdir", sandbox.leaf_fd, "outerlink") == 0);
        int before = 0;
        CHECK(entry_count(sandbox.leaf_fd, before));

        Scratch scratch;
        CHECK(!open_scratch_at(link + "/sub", scratch));
        CHECK(!scratch.ok);
        CHECK_CONTAINS(scratch.error, "symlink");

        int after = 0;
        CHECK(entry_count(sandbox.leaf_fd, after));
        CHECK_EQ(before, after);
        ::unlinkat(sandbox.leaf_fd, "outerlink", 0);
        ::unlinkat(sandbox.leaf_fd, "realdir", AT_REMOVEDIR);
    }
    {
        // A FINAL-component symlink is still refused, and nothing is created.
        ::mkdirat(sandbox.leaf_fd, "realdir", 0700);
        CHECK(::symlinkat("realdir", sandbox.leaf_fd, "finallink") == 0);
        int before = 0;
        CHECK(entry_count(sandbox.leaf_fd, before));
        Scratch scratch;
        CHECK(!open_scratch_at(sandbox.path + "/finallink", scratch));
        CHECK_CONTAINS(scratch.error, "symlink");
        int after = 0;
        CHECK(entry_count(sandbox.leaf_fd, after));
        CHECK_EQ(before, after);
        ::unlinkat(sandbox.leaf_fd, "finallink", 0);
        ::unlinkat(sandbox.leaf_fd, "realdir", AT_REMOVEDIR);
    }
    {
        // F2 — THE CREATE CANNOT BE REDIRECTED AFTER VALIDATION. The leaf is
        // created through the pinned descriptor; replacing the BASE's own path
        // with a symlink afterwards cannot move it, and the delete still removes
        // the directory it made rather than whatever the link points at.
        const std::string victim = sandbox.path + "/victim";
        ::mkdirat(sandbox.leaf_fd, "victim", 0700);
        Scratch scratch;
        CHECK(open_scratch_at(victim, scratch));
        CHECK(scratch.ok);
        struct stat pinned;
        CHECK(::fstat(scratch.leaf_fd, &pinned) == 0);

        // Replace the PATH `victim` with a symlink to somewhere else. The
        // directory itself is MOVED aside rather than removed, because it holds
        // the scratch leaf — moving is what an attacker replacing a path would
        // do, and it leaves the pinned descriptors pointing where they did.
        CHECK(::renameat(sandbox.leaf_fd, "victim", sandbox.leaf_fd, "victim-moved") == 0);
        CHECK(::symlinkat("elsewhere", sandbox.leaf_fd, "victim") == 0);

        // The pinned descriptor still refers to the directory that was created.
        struct stat still;
        CHECK(::fstat(scratch.leaf_fd, &still) == 0);
        CHECK_EQ(static_cast<long>(pinned.st_ino), static_cast<long>(still.st_ino));
        // And releasing removes THAT leaf by name relative to the pinned base —
        // it does not follow the link now sitting at the same path.
        const std::string leaf_name = scratch.leaf_name;
        release_scratch(scratch, {});
        struct stat gone;
        CHECK(::fstatat(sandbox.leaf_fd, ("victim-moved/" + leaf_name).c_str(), &gone,
                        AT_SYMLINK_NOFOLLOW) != 0);
        // The symlink at the original path was never touched.
        CHECK(::fstatat(sandbox.leaf_fd, "victim", &gone, AT_SYMLINK_NOFOLLOW) == 0);
        CHECK(S_ISLNK(gone.st_mode));
        ::unlinkat(sandbox.leaf_fd, "victim", 0);
        ::unlinkat(sandbox.leaf_fd, "victim-moved", AT_REMOVEDIR);
    }
    {
        // F3 — a RELATIVE override is refused: it would resolve against the
        // test's cwd, which the caller did not name.
        Scratch scratch;
        CHECK(!open_scratch_at("relative/base", scratch));
        CHECK_CONTAINS(scratch.error, "not an absolute path");
        CHECK(!open_scratch_at("..", scratch));
    }
    {
        // A base that does not exist is refused and nothing is created.
        int before = 0;
        CHECK(entry_count(sandbox.leaf_fd, before));
        Scratch scratch;
        CHECK(!open_scratch_at(sandbox.path + "/absent", scratch));
        CHECK(!scratch.ok);
        CHECK(!scratch.error.empty());
        int after = 0;
        CHECK(entry_count(sandbox.leaf_fd, after));
        CHECK_EQ(before, after);
    }
    {
        // A base that is a regular FILE is refused — and this refusal does not
        // depend on the caller's capabilities, so it is the case that still
        // means something when the suite runs as root.
        const int file = ::openat(sandbox.leaf_fd, "notadir", O_CREAT | O_WRONLY | O_CLOEXEC, 0600);
        CHECK(file >= 0);
        if (file >= 0) {
            ::close(file);
        }
        Scratch scratch;
        CHECK(!open_scratch_at(sandbox.path + "/notadir", scratch));
        CHECK_CONTAINS(scratch.error, "not a directory");
        ::unlinkat(sandbox.leaf_fd, "notadir", 0);
    }
    {
        // F4 — an unwritable base is refused, but ROOT BYPASSES THE MODE BIT, so
        // the expectation is only asserted when it can actually hold. The
        // refusal above is the capability-independent one.
        if (::geteuid() != 0) {
            ::mkdirat(sandbox.leaf_fd, "locked", 0700);
            CHECK(::fchmodat(sandbox.leaf_fd, "locked", 0500, 0) == 0);
            int before = 0;
            CHECK(entry_count(sandbox.leaf_fd, before));
            Scratch scratch;
            CHECK(!open_scratch_at(sandbox.path + "/locked", scratch));
            CHECK(!scratch.ok);
            int after = 0;
            CHECK(entry_count(sandbox.leaf_fd, after));
            CHECK_EQ(before, after);
            CHECK(::fchmodat(sandbox.leaf_fd, "locked", 0700, 0) == 0);
            ::unlinkat(sandbox.leaf_fd, "locked", AT_REMOVEDIR);
        } else {
            std::cout << "  (root: the 0500 case is skipped, the mode bit does not bind)\n";
        }
    }
    {
        // Two calls, two directories: a reused path would let two runs overwrite
        // each other's lock files and look like a lease bug.
        Scratch first;
        Scratch second;
        CHECK(open_scratch_at(sandbox.path, first));
        CHECK(open_scratch_at(sandbox.path, second));
        CHECK(first.ok && second.ok);
        CHECK(first.path != second.path);
        release_scratch(first, {});
        release_scratch(second, {});
    }
    {
        // F6 — NO LEFTOVERS. After every case above has cleaned up, the sandbox
        // holds exactly what it started with.
        int count = 0;
        CHECK(entry_count(sandbox.leaf_fd, count));
        CHECK_EQ(count, 2);  // "." and ".."
    }

    release_scratch(sandbox, {});
    if (had_override) {
        ::setenv("SERVEROS_TEST_TMPDIR", saved_value.c_str(), 1);
    } else {
        ::unsetenv("SERVEROS_TEST_TMPDIR");
    }
}

int main() {
    test_the_scratch_root_is_fd_anchored();
    test_a_symlinked_lease_is_refused();
    test_a_hardlinked_lease_is_refused();
    test_a_world_writable_lease_is_refused();
    test_a_replaced_lease_fails_revalidation();

    Scratch scratch;
    if (!open_default_scratch(scratch)) {
        // The reason is printed rather than swallowed: no scratch root is a
        // fixture problem, and a reader needs to know which one.
        std::fprintf(stderr, "no scratch root: %s\n", scratch.error.c_str());
        CHECK(false);
        return check::report("test_sup_lease");
    }
    const std::string& root = scratch.path;
    CHECK(scratch.ok);

    {
        // A free lease is taken, and held by the object that took it.
        const std::string path = root + "/a.lock";
        WriterLease lease;
        const LeaseAttempt attempt = lease.acquire(path, "device:fixture-under-test");
        CHECK(attempt.ok);
        CHECK_EQ(attempt.code, std::string(kLeaseAcquired));
        CHECK(lease.held());
        CHECK(lease.descriptor() >= 0);

        // And the holder is written down, so the NEXT refusal can name it.
        const LeaseAttempt holder = peek_holder(path);
        CHECK(holder.ok);
        CHECK_EQ(holder.code, std::string(kLeaseHolderRead));
        CHECK_EQ(holder.holder, std::string("device:fixture-under-test"));
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

    {
        // A no-follow leaf open must not create a lock through an ancestor alias.
        CHECK(::mkdirat(scratch.leaf_fd, "ancestor-owned", 0700) == 0);
        CHECK(::symlinkat("ancestor-owned", scratch.leaf_fd, "ancestor-alias") == 0);
        WriterLease lease;
        CHECK(!lease.acquire(root + "/ancestor-alias/new.lock", "core-a").ok);
        CHECK(!std::filesystem::exists(root + "/ancestor-owned/new.lock"));
        // This branch also cleans up a lock incorrectly created by the RED version.
        ::unlink((root + "/ancestor-owned/new.lock").c_str());
        CHECK(::unlinkat(scratch.leaf_fd, "ancestor-alias", 0) == 0);
        CHECK(::unlinkat(scratch.leaf_fd, "ancestor-owned", AT_REMOVEDIR) == 0);
    }
    {
        // Replacing an ancestor with an alias to the SAME inode must not keep
        // the namespace claim valid just because leaf dev/ino still matches.
        CHECK(::mkdirat(scratch.leaf_fd, "lease-parent", 0700) == 0);
        WriterLease lease;
        CHECK(lease.acquire(root + "/lease-parent/stable.lock", "core-a").ok);
        CHECK(::renameat(scratch.leaf_fd, "lease-parent", scratch.leaf_fd, "lease-moved") == 0);
        CHECK(::symlinkat("lease-moved", scratch.leaf_fd, "lease-parent") == 0);
        CHECK(!lease.revalidate().ok);
        lease.release();
        CHECK(::unlinkat(scratch.leaf_fd, "lease-parent", 0) == 0);
        CHECK(::unlink((root + "/lease-moved/stable.lock").c_str()) == 0);
        CHECK(::unlinkat(scratch.leaf_fd, "lease-moved", AT_REMOVEDIR) == 0);
    }

    // Remove exactly what this test created, by name, relative to the pinned
    // descriptors.
    release_scratch(scratch, {"a.lock", "b.lock", "c.lock", "d.lock", "e.lock", "f.lock",
                              "never-written.lock"});

    return check::report("test_sup_lease");
}
