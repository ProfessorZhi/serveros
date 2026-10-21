#include "supervisor/state_directory.hpp"

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <ctime>
#include <string>
#include <utility>
#include <vector>

namespace nodecore::supervisor {
namespace {

std::string errno_text(int value) { return std::string(std::strerror(value)); }

std::vector<std::string> split_components(const std::string& path) {
    std::vector<std::string> parts;
    std::string current;
    for (const char c : path) {
        if (c == '/') {
            if (!current.empty()) {
                parts.push_back(current);
                current.clear();
            }
        } else {
            current.push_back(c);
        }
    }
    if (!current.empty()) {
        parts.push_back(current);
    }
    return parts;
}

unsigned long long next_nonce() {
    static unsigned long long counter = 0;
    struct timespec now {};
    ::clock_gettime(CLOCK_MONOTONIC, &now);
    ++counter;
    return (static_cast<unsigned long long>(now.tv_sec) * 1000000000ULL +
            static_cast<unsigned long long>(now.tv_nsec)) ^
           counter;
}

//: A tiny owning descriptor. The walk moves from one opened directory to the
//: next; every failure path closes whatever is currently open.
class Fd {
public:
    explicit Fd(int value = -1) : fd_(value) {}
    ~Fd() { reset(); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
    Fd(Fd&& other) noexcept : fd_(other.fd_) { other.fd_ = -1; }
    Fd& operator=(Fd&& other) noexcept {
        if (this != &other) {
            reset();
            fd_ = other.fd_;
            other.fd_ = -1;
        }
        return *this;
    }
    int get() const { return fd_; }
    void reset(int value = -1) {
        if (fd_ >= 0) {
            ::close(fd_);
        }
        fd_ = value;
    }
    int release() {
        const int value = fd_;
        fd_ = -1;
        return value;
    }

private:
    int fd_;
};

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

//: May a path component be trusted inside `directory`?
//:
//: A component is safe when the containing directory is not writable by group
//: or other. If it IS writable, the sticky bit must be set AND both the
//: directory and the resolved entry must be owned by this euid or root: sticky
//: then lets only the entry owner (or root) rename it, so another group member
//: cannot switch the path under us. `/tmp` (1777, root-owned) with an entry
//: owned by us is the safe case; a 0770 directory without sticky is not, even
//: when the entry is ours.
bool trusted_ancestor(const struct stat& directory, const struct stat& entry, uid_t euid) {
    if ((directory.st_mode & (S_IWGRP | S_IWOTH)) == 0) {
        return true;
    }
    if ((directory.st_mode & S_ISVTX) == 0) {
        return false;
    }
    const bool directory_owner_trusted = directory.st_uid == euid || directory.st_uid == 0;
    const bool entry_owner_trusted = entry.st_uid == euid || entry.st_uid == 0;
    return directory_owner_trusted && entry_owner_trusted;
}

bool private_directory(const struct stat& directory, uid_t euid) {
    return S_ISDIR(directory.st_mode) && directory.st_uid == euid &&
           (directory.st_mode & (S_IRWXG | S_IRWXO)) == 0;
}

bool target_still_owned(int parent, const std::string& basename, std::string& error) {
    struct stat info {};
    if (::fstatat(parent, basename.c_str(), &info, AT_SYMLINK_NOFOLLOW) != 0) {
        if (errno == ENOENT)
            return true;
        error = "the state target could not be inspected: " + errno_text(errno);
        return false;
    }
    if (!S_ISREG(info.st_mode) || info.st_uid != ::geteuid() ||
        (info.st_mode & (S_IWGRP | S_IWOTH)) != 0) {
        error = "the state target is no longer an owned, nonshared regular file";
        return false;
    }
    return true;
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

}  // namespace

// A private/writable exception or an explicit lease proof retains this chain.
// FDs, not names or the exception flag, establish and revalidate its enclosure.
struct StateDirectory::Ancestry {
    struct Node {
        Fd fd;
        std::string name;
        struct stat observed;
        Node(Fd handle, std::string component, const struct stat& info)
            : fd(std::move(handle)), name(std::move(component)), observed(info) {}
    };
    uid_t principal = ::geteuid();
    std::vector<Node> nodes;
    std::size_t private_index = static_cast<std::size_t>(-1);
    bool exception_used = false;

    bool remember(int fd, const std::string& name, const struct stat& info) {
        Fd held(::fcntl(fd, F_DUPFD_CLOEXEC, 0));
        if (held.get() < 0)
            return false;
        nodes.emplace_back(std::move(held), name, info);
        if (private_index == static_cast<std::size_t>(-1) && private_directory(info, principal))
            private_index = nodes.size() - 1;
        return true;
    }

    bool private_tail_owned() const {
        if (private_index >= nodes.size())
            return false;
        for (std::size_t i = private_index; i < nodes.size(); ++i)
            if (nodes[i].observed.st_uid != principal)
                return false;
        return true;
    }

    bool validate(int parent, std::string& error) const {
        if (::geteuid() != principal || nodes.empty()) {
            error = "the private state principal changed or its ancestry is absent";
            return false;
        }
        bool enclosed = false;
        struct stat previous {};
        for (std::size_t i = 0; i < nodes.size(); ++i) {
            const Node& node = nodes[i];
            struct stat now {}, entry {};
            if (::fstat(node.fd.get(), &now) != 0 || !S_ISDIR(now.st_mode) ||
                now.st_dev != node.observed.st_dev || now.st_ino != node.observed.st_ino ||
                now.st_uid != node.observed.st_uid || now.st_gid != node.observed.st_gid ||
                now.st_mode != node.observed.st_mode) {
                error = "the captured private state ancestry owner or permissions changed";
                return false;
            }
            const int inspected =
                i == 0 ? ::lstat("/", &entry)
                       : ::fstatat(nodes[i - 1].fd.get(), node.name.c_str(), &entry,
                                   AT_SYMLINK_NOFOLLOW);
            if (inspected != 0 || !S_ISDIR(entry.st_mode) || entry.st_dev != now.st_dev ||
                entry.st_ino != now.st_ino) {
                error = "the private state path no longer names its captured original inode";
                return false;
            }
            if (enclosed && now.st_uid != principal) {
                error = "the private state tail contains a foreign owner";
                return false;
            }
            if (i != 0 && !trusted_ancestor(previous, now, principal) &&
                !(enclosed && previous.st_uid == principal && now.st_uid == principal)) {
                error = "the state ancestry no longer has a trusted private enclosure";
                return false;
            }
            if (i == private_index && !private_directory(now, principal)) {
                error = "the captured private state boundary is no longer private";
                return false;
            }
            enclosed = enclosed || private_directory(now, principal);
            previous = now;
        }
        struct stat actual {};
        if ((exception_used && !enclosed) || ::fstat(parent, &actual) != 0 || actual.st_dev != previous.st_dev ||
            actual.st_ino != previous.st_ino || actual.st_uid != principal ||
            (actual.st_mode & (S_IWGRP | S_IWOTH)) != 0 ||
            ::faccessat(parent, ".", W_OK | X_OK, AT_EACCESS) != 0) {
            error = "the private state immediate parent is no longer owned and nonshared";
            return false;
        }
        return true;
    }
};

StateDirectory::StateDirectory() = default;
StateDirectory::~StateDirectory() { close(); }

StateDirectory::StateDirectory(StateDirectory&& other) noexcept
    : dirfd_(other.dirfd_),
      basename_(std::move(other.basename_)),
      parent_path_(std::move(other.parent_path_)),
      ancestry_(std::move(other.ancestry_)) {
    other.dirfd_ = -1;
}

StateDirectory& StateDirectory::operator=(StateDirectory&& other) noexcept {
    if (this != &other) {
        close();
        dirfd_ = other.dirfd_;
        basename_ = std::move(other.basename_);
        parent_path_ = std::move(other.parent_path_);
        ancestry_ = std::move(other.ancestry_);
        other.dirfd_ = -1;
    }
    return *this;
}

void StateDirectory::close() {
    ancestry_.reset();
    if (dirfd_ >= 0) {
        ::close(dirfd_);
        dirfd_ = -1;
    }
}

bool StateDirectory::open(const std::string& path, StatePathVerdict& verdict,
                          const OpenObservationHook& after_lookup,
                          const std::function<void()>& after_file_lookup,
                          bool retain_namespace) {
    close();
    basename_.clear();
    parent_path_.clear();
    verdict = StatePathVerdict{};

    if (path.empty()) {
        verdict = path_refuse(kStatePathEmpty, "no state path was given");
        return false;
    }
    if (path.front() != '/') {
        verdict = path_refuse(kStatePathNotAbsolute, "\"" + path + "\" is not absolute");
        return false;
    }
    if (path.back() == '/') {
        verdict = path_refuse(kStatePathNotAFile,
                              "\"" + path + "\" names a directory, not a state file");
        return false;
    }

    const std::vector<std::string> parts = split_components(path);
    for (const std::string& part : parts) {
        if (part == "." || part == "..") {
            verdict = path_refuse(kStatePathNotNormalized,
                                  "\"" + path +
                                      "\" contains a \".\" or \"..\" component; a state path is "
                                      "refused if its lexical form is not its resolved form");
            return false;
        }
    }
    if (parts.empty()) {
        verdict = path_refuse(kStatePathNotAFile,
                              "\"" + path + "\" names a directory, not a state file");
        return false;
    }

    const std::string basename = parts.back();
    const std::size_t slash = path.find_last_of('/');
    const std::string parent_path = slash == 0 ? std::string("/") : path.substr(0, slash);
    const std::vector<std::string> parent_parts = split_components(parent_path);
    if (parent_parts.size() > 128) {
        verdict = path_refuse(kStatePathUnusable,
                              "the state ancestry exceeds its 128-directory budget");
        return false;
    }
    auto ancestry = std::make_unique<Ancestry>();

    Fd current(::open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC));
    if (current.get() < 0) {
        verdict = path_refuse(kStatePathUnusable,
                              "the root directory could not be opened: " + errno_text(errno));
        return false;
    }
    struct stat dir_info {};
    if (::fstat(current.get(), &dir_info) != 0 || !S_ISDIR(dir_info.st_mode)) {
        verdict = path_refuse(kStatePathUnusable,
                              "the root directory could not be inspected: " + errno_text(errno));
        return false;
    }
    if (!ancestry->remember(current.get(), "", dir_info)) {
        verdict = path_refuse(kStatePathUnusable,
                              "the root ancestry descriptor could not be retained");
        return false;
    }

    for (std::size_t index = 0; index < parent_parts.size(); ++index) {
        const std::string& component = parent_parts[index];
        const bool last = (index + 1 == parent_parts.size());

        // Inspect the entry in the TRUSTED parent fd. `O_NOFOLLOW` on the open
        // below refuses a symlink that is swapped in after this fstatat, so a
        // race is fail-closed rather than followed.
        struct stat entry {};
        if (::fstatat(current.get(), component.c_str(), &entry, AT_SYMLINK_NOFOLLOW) != 0) {
            const int error = errno;
            if (error == ENOENT) {
                verdict = path_refuse(
                    last ? kStateParentMissing : kStateAncestorMissing,
                    "the state " + std::string(last ? "directory" : "ancestor") + " \"" +
                        component + "\" does not exist");
            } else {
                verdict = path_refuse(kStatePathUnusable,
                                      "the state component \"" + component +
                                          "\" could not be inspected: " + errno_text(error));
            }
            return false;
        }
        if (S_ISLNK(entry.st_mode)) {
            verdict = path_refuse(
                last ? kStateParentSymlink : kStateAncestorSymlink,
                "the state " + std::string(last ? "directory" : "ancestor") + " \"" +
                    component + "\" is a symlink; it is refused rather than followed");
            return false;
        }
        if (!S_ISDIR(entry.st_mode)) {
            verdict = path_refuse(
                last ? kStateParentNotADirectory : kStateAncestorNotADirectory,
                "the state " + std::string(last ? "parent" : "ancestor") + " \"" + component +
                    "\" is not a directory");
            return false;
        }
        if (after_lookup) {
            after_lookup(component);
        }

        Fd next(::openat(current.get(), component.c_str(),
                         O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
        if (next.get() < 0) {
            const int error = errno;
            if (error == ELOOP) {
                verdict = path_refuse(last ? kStateParentSymlink : kStateAncestorSymlink,
                                      "the state component \"" + component +
                                          "\" became a symlink during the walk");
            } else if (error == ENOTDIR) {
                verdict = path_refuse(last ? kStateParentNotADirectory : kStateAncestorNotADirectory,
                                      "the state component \"" + component +
                                          "\" is not a directory");
            } else if (error == ENOENT) {
                verdict = path_refuse(last ? kStateParentMissing : kStateAncestorMissing,
                                      "the state component \"" + component +
                                          "\" disappeared during the walk");
            } else {
                verdict = path_refuse(kStatePathUnusable,
                                      "the state component \"" + component +
                                          "\" could not be opened: " + errno_text(error));
            }
            return false;
        }

        struct stat opened {};
        if (::fstat(next.get(), &opened) != 0 || !S_ISDIR(opened.st_mode)) {
            verdict = path_refuse(kStatePathUnusable,
                                  "the state component \"" + component +
                                      "\" is not a directory after opening");
            return false;
        }
        // The fd we opened must be the inode we inspected. Without this a
        // real-directory swap between fstatat and openat would apply the policy
        // to one inode and traverse another.
        if (entry.st_dev != opened.st_dev || entry.st_ino != opened.st_ino) {
            verdict = path_refuse(
                last ? kStateParentIdentityChanged : kStateAncestorIdentityChanged,
                "the state component \"" + component +
                    "\" changed between look-up and open; refusing to apply a policy to one "
                    "inode and open another");
            return false;
        }
        if (!trusted_ancestor(dir_info, opened, ancestry->principal)) {
            const bool privately_enclosed = ancestry->private_tail_owned() &&
                                            dir_info.st_uid == ancestry->principal &&
                                            opened.st_uid == ancestry->principal;
            if (privately_enclosed) {
                ancestry->exception_used = true;
            } else {
                const bool world = (dir_info.st_mode & S_IWOTH) != 0;
                verdict = path_refuse(
                    world ? kStateAncestorWorldWritable : kStateAncestorGroupWritable,
                    "the ancestor directory containing \"" + component +
                        "\" is writable by group or other without a safe sticky-and-owner "
                        "combination");
                return false;
            }
        }
        if (ancestry->exception_used && opened.st_uid != ancestry->principal) {
            verdict = path_refuse(kStateAncestorWrongOwner,
                                  "a private state ancestor is owned by another account");
            return false;
        }
        if (!ancestry->remember(next.get(), component, opened)) {
            verdict = path_refuse(kStatePathUnusable,
                                  "a state ancestry descriptor could not be retained");
            return false;
        }
        if (last) {
            if (opened.st_uid != ::geteuid()) {
                verdict = path_refuse(kStateParentWrongOwner,
                                      "the state directory \"" + component +
                                          "\" is owned by another account");
                return false;
            }
            if ((opened.st_mode & (S_IWGRP | S_IWOTH)) != 0) {
                verdict = path_refuse(kStateParentGroupWritable,
                                      "the state directory \"" + component +
                                          "\" is writable by group or other, so the state file "
                                          "can be replaced by another account");
                return false;
            }
        }
        current = std::move(next);
        dir_info = opened;
    }

    // A state file directly under "/" has no parent component; the root fd is
    // the immediate parent and must still satisfy the same ownership rule.
    if (parent_parts.empty()) {
        struct stat root {};
        if (::fstat(current.get(), &root) != 0) {
            verdict = path_refuse(kStatePathUnusable,
                                  "the root directory could not be inspected: " + errno_text(errno));
            return false;
        }
        if (root.st_uid != ::geteuid()) {
            verdict = path_refuse(kStateParentWrongOwner,
                                  "the root directory is owned by another account");
            return false;
        }
        if ((root.st_mode & (S_IWGRP | S_IWOTH)) != 0) {
            verdict = path_refuse(kStateParentGroupWritable,
                                  "the root directory is writable by group or other");
            return false;
        }
    }

    // `renameat` needs write permission on the parent even when the final file
    // already exists, so this is checked for both the existing-file and
    // first-run cases.
    if (::faccessat(current.get(), ".", W_OK | X_OK, AT_EACCESS) != 0) {
        verdict = path_refuse(kStatePathUnwritable,
                              "the state directory is not writable, so a state file cannot be "
                              "created or replaced: " +
                                  errno_text(errno));
        return false;
    }

    // The state file itself, relative to the trusted parent fd.
    struct stat file {};
    if (::fstatat(current.get(), basename.c_str(), &file, AT_SYMLINK_NOFOLLOW) == 0) {
        if (S_ISLNK(file.st_mode)) {
            verdict = path_refuse(kStatePathSymlink,
                                  "the state path \"" + path +
                                      "\" is a symlink; it is refused rather than followed");
            return false;
        }
        if (!S_ISREG(file.st_mode)) {
            verdict = path_refuse(kStatePathNotAFile,
                                  "the state path \"" + path + "\" is not a regular file");
            return false;
        }
        if (file.st_uid != ::geteuid()) {
            verdict = path_refuse(kStatePathWrongOwner,
                                  "the state file \"" + path + "\" is owned by another account");
            return false;
        }
        if ((file.st_mode & (S_IWGRP | S_IWOTH)) != 0) {
            verdict = path_refuse(kStatePathGroupWritable,
                                  "the state file \"" + path + "\" is writable by group or other");
            return false;
        }
        if (after_file_lookup) {
            after_file_lookup();
        }
        Fd probe(::openat(current.get(), basename.c_str(), O_RDWR | O_NOFOLLOW | O_CLOEXEC));
        if (probe.get() < 0) {
            const int error = errno;
            if (error == ELOOP) {
                verdict = path_refuse(kStatePathSymlink,
                                      "the state file \"" + path +
                                          "\" became a symlink during the check");
            } else if (error == ENOENT) {
                verdict = path_refuse(kStatePathUnusable,
                                      "the state file \"" + path +
                                          "\" disappeared during the check");
            } else {
                verdict = path_refuse(kStatePathUnwritable,
                                      "the state file \"" + path +
                                          "\" is not writable: " + errno_text(error));
            }
            return false;
        }
        // The fd we opened must be the inode we inspected. The checks above
        // were made on `file`; a real-file swap between fstatat and openat is
        // refused rather than applying the policy to one inode and probing
        // another.
        struct stat opened_file {};
        if (::fstat(probe.get(), &opened_file) != 0) {
            verdict = path_refuse(kStatePathUnusable,
                                  "the state file \"" + path +
                                      "\" could not be inspected after opening");
            return false;
        }
        if (opened_file.st_dev != file.st_dev || opened_file.st_ino != file.st_ino) {
            verdict = path_refuse(kStatePathIdentityChanged,
                                  "the state file \"" + path +
                                      "\" changed between look-up and open");
            return false;
        }
    } else if (errno == ENOENT) {
        // A first run is allowed; the parent writability check above already
        // established that the file can be created.
    } else {
        verdict = path_refuse(kStatePathUnusable, "the state path \"" + path +
                                                     "\" could not be inspected: " +
                                                     errno_text(errno));
        return false;
    }

    dirfd_ = current.release();
    basename_ = basename;
    parent_path_ = parent_path;
    if (ancestry->exception_used || retain_namespace) {
        ancestry_ = std::move(ancestry);
        std::string error;
        if (!ancestry_->validate(dirfd_, error) || !target_still_owned(dirfd_, basename_, error)) {
            verdict = path_refuse(kStatePathUnusable, std::move(error));
            close();
            return false;
        }
    }
    verdict = path_ok("the state directory is an opened, owned, non-shared inode");
    return true;
}

bool StateDirectory::namespace_current(std::string& error) const {
    if (dirfd_ < 0 || !ancestry_) {
        error = "no retained namespace proof is open";
        return false;
    }
    return ancestry_->validate(dirfd_, error);
}

bool StateDirectory::read(std::string& text, bool& present, std::string& error) const {
    text.clear();
    present = false;
    if (dirfd_ < 0 || basename_.empty()) {
        error = "no trusted state directory is open";
        return false;
    }
    if (ancestry_ && !ancestry_->validate(dirfd_, error))
        return false;
    Fd file(::openat(dirfd_, basename_.c_str(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC));
    if (file.get() < 0) {
        if (errno == ENOENT) {
            return !ancestry_ || ancestry_->validate(dirfd_, error);  // first run
        }
        error = "the state file could not be opened: " + errno_text(errno);
        return false;
    }
    struct stat info {};
    if (::fstat(file.get(), &info) != 0) {
        error = "the state file could not be inspected: " + errno_text(errno);
        return false;
    }
    if (!S_ISREG(info.st_mode)) {
        error = "the state file is not a regular file";
        return false;
    }
    if (info.st_uid != ::geteuid()) {
        error = "the state file is owned by another account";
        return false;
    }
    if ((info.st_mode & (S_IWGRP | S_IWOTH)) != 0) {
        error = "the state file is writable by group or other";
        return false;
    }
    constexpr std::size_t kMaxStateBytes = 1024 * 1024;
    if (info.st_size < 0 || static_cast<std::size_t>(info.st_size) > kMaxStateBytes) {
        error = "the state file is larger than the bounded state size";
        return false;
    }
    text.reserve(static_cast<std::size_t>(info.st_size));
    char buffer[4096];
    for (;;) {
        const ssize_t count = ::read(file.get(), buffer, sizeof(buffer));
        if (count < 0) {
            if (errno == EINTR) {
                continue;
            }
            error = "the state file could not be read: " + errno_text(errno);
            return false;
        }
        if (count == 0) {
            break;
        }
        if (text.size() + static_cast<std::size_t>(count) > kMaxStateBytes) {
            error = "the state file grew beyond the bounded state size";
            return false;
        }
        text.append(buffer, static_cast<std::size_t>(count));
    }
    if (ancestry_ && !ancestry_->validate(dirfd_, error)) {
        text.clear();
        return false;
    }
    present = true;
    return true;
}

bool StateDirectory::write_atomically(const std::string& text, std::string& error) const {
    if (dirfd_ < 0 || basename_.empty()) {
        error = "no trusted state directory is open";
        return false;
    }
    if (ancestry_ && (!ancestry_->validate(dirfd_, error) ||
                      !target_still_owned(dirfd_, basename_, error)))
        return false;

    std::string temp;
    Fd file;
    const auto discard_temp = [&] {
        if (ancestry_) {
            std::string ignored;
            struct stat held {}, named {};
            if (!ancestry_->validate(dirfd_, ignored) || file.get() < 0 ||
                ::fstat(file.get(), &held) != 0 ||
                ::fstatat(dirfd_, temp.c_str(), &named, AT_SYMLINK_NOFOLLOW) != 0 ||
                !S_ISREG(named.st_mode) || held.st_dev != named.st_dev ||
                held.st_ino != named.st_ino || named.st_uid != ::geteuid())
                return;
        }
        ::unlinkat(dirfd_, temp.c_str(), 0);
    };
    for (int attempt = 0; attempt < 16; ++attempt) {
        temp = ".serveros-state.tmp." + std::to_string(static_cast<long>(::getpid())) + "." +
               std::to_string(next_nonce());
        const int raw = ::openat(dirfd_, temp.c_str(),
                                 O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (raw >= 0) {
            file.reset(raw);
            break;
        }
        if (errno != EEXIST) {
            error = "the state temp file could not be created: " + errno_text(errno);
            return false;
        }
    }
    if (file.get() < 0) {
        error = "a unique state temp name could not be allocated in the trusted directory";
        return false;
    }

    if (!write_all(file.get(), text, error)) {
        discard_temp();
        return false;
    }
    if (::fsync(file.get()) != 0) {
        error = "the state temp file could not be fsynced: " + errno_text(errno);
        discard_temp();
        return false;
    }
    if (ancestry_ && (!ancestry_->validate(dirfd_, error) ||
                      !target_still_owned(dirfd_, basename_, error))) {
        // Trust was revoked: retain the unique temp instead of deleting through
        // a namespace that may now be writable by another account.
        return false;
    }
    const int raw = file.release();
    if (::close(raw) != 0) {
        error = "the state temp file could not be closed: " + errno_text(errno);
        discard_temp();
        return false;
    }
    if (::renameat(dirfd_, temp.c_str(), dirfd_, basename_.c_str()) != 0) {
        error = "the state file could not be replaced atomically: " + errno_text(errno);
        discard_temp();
        return false;
    }
    if (::fsync(dirfd_) != 0) {
        error = "the state directory could not be fsynced: " + errno_text(errno);
        return false;
    }
    if (ancestry_ && !ancestry_->validate(dirfd_, error))
        return false;

    // The WRITE above is bound to the opened inode. This check detects a path
    // that was moved/replaced after the open so the caller can fail closed; it
    // is not the write authority.
    struct stat opened {};
    struct stat by_path {};
    if (::fstat(dirfd_, &opened) != 0 || ::lstat(parent_path_.c_str(), &by_path) != 0 ||
        opened.st_dev != by_path.st_dev || opened.st_ino != by_path.st_ino) {
        error =
            "the state directory path no longer names the opened directory; the write went to "
            "the original inode";
        return false;
    }
    return true;
}

StatePathVerdict check_state_path(const std::string& path) {
    StateDirectory directory;
    StatePathVerdict verdict;
    directory.open(path, verdict);
    return verdict;
}

}  // namespace nodecore::supervisor
