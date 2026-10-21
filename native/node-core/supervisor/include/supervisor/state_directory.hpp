// The trusted state directory: a directory fd opened by walking from "/" with
// openat(O_NOFOLLOW | O_DIRECTORY), and the at-relative write that uses it.
//
// WHY THIS EXISTS
// ---------------
// #314/#317 validated a state path with path-based `lstat` and then wrote it
// with path-based `mkstemp`/`rename`. Between the check and the write an
// ancestor directory could be replaced by a symlink, so the write could land
// somewhere else while the budget was still spent. The authority must be the
// opened directory inode, not a string that was checked earlier.
//
// The walk opens each component relative to the previous fd. O_NOFOLLOW refuses
// a symlink at every level; O_DIRECTORY refuses a non-directory; fstat checks
// the opened inode. The immediate parent must be owned by this effective uid
// and not writable by group or other. A group- or world-writable ancestor is
// allowed only when it carries the sticky bit AND both the directory and the
// resolved entry are owned by this euid or root; otherwise a member of the
// group (or any account) could rename the entry. `/tmp` (1777, root-owned) with
// an entry owned by us is the safe case. The `fstatat` result is compared with
// the `openat` fd's `st_dev`/`st_ino`; a mismatch is refused rather than
// applying the policy to one inode and opening another.
//
// A writable intermediate directory may also be admitted below an opened,
// current-euid-owned private boundary (zero group/other permission bits), with
// current-euid ownership throughout that private tail. Only paths which actually
// need this exception retain the complete FD ancestry and captured permissions;
// before/after I/O they refuse any owner/gid/mode, principal or namespace change.
// Other paths retain the existing original-inode read/write semantics below.
//
// The write uses openat(dirfd, ...) for the temp file, renameat(dirfd, ...) for
// the replace, and fsync(dirfd). No path string is the write authority. A
// post-write stability check compares the path's inode to the opened fd and
// reports failure if the path moved; the data still went to the trusted inode.
#pragma once

#include <functional>
#include <memory>
#include <string>

namespace nodecore::supervisor {

//: The closed state-path vocabulary.
inline constexpr const char* kStatePathOk = "USABLE";
inline constexpr const char* kStatePathEmpty = "STATE_PATH_EMPTY";
inline constexpr const char* kStatePathNotAbsolute = "STATE_PATH_NOT_ABSOLUTE";
inline constexpr const char* kStatePathNotNormalized = "STATE_PATH_NOT_NORMALIZED";
inline constexpr const char* kStatePathNotAFile = "STATE_PATH_NOT_A_FILE";
inline constexpr const char* kStatePathSymlink = "STATE_PATH_SYMLINK";
inline constexpr const char* kStatePathWrongOwner = "STATE_PATH_WRONG_OWNER";
inline constexpr const char* kStatePathGroupWritable = "STATE_PATH_GROUP_WRITABLE";
inline constexpr const char* kStatePathUnwritable = "STATE_PATH_UNWRITABLE";
inline constexpr const char* kStatePathUnusable = "STATE_PATH_UNUSABLE";
inline constexpr const char* kStateParentMissing = "STATE_PARENT_MISSING";
inline constexpr const char* kStateParentNotADirectory = "STATE_PARENT_NOT_A_DIRECTORY";
inline constexpr const char* kStateParentSymlink = "STATE_PARENT_SYMLINK";
inline constexpr const char* kStateParentWrongOwner = "STATE_PARENT_WRONG_OWNER";
inline constexpr const char* kStateParentGroupWritable = "STATE_PARENT_GROUP_WRITABLE";
//: An ancestor ABOVE the immediate parent, opened relative to its parent fd.
inline constexpr const char* kStateAncestorSymlink = "STATE_ANCESTOR_SYMLINK";
inline constexpr const char* kStateAncestorNotADirectory = "STATE_ANCESTOR_NOT_A_DIRECTORY";
inline constexpr const char* kStateAncestorMissing = "STATE_ANCESTOR_MISSING";
inline constexpr const char* kStateAncestorWrongOwner = "STATE_ANCESTOR_WRONG_OWNER";
//: A group-writable ancestor WITHOUT the sticky bit: any member of the group
//: can rename the entry that leads to the state root.
inline constexpr const char* kStateAncestorGroupWritable = "STATE_ANCESTOR_GROUP_WRITABLE";
//: A world-writable ancestor WITHOUT the sticky bit: any account can rename the
//: entry that leads to the state root. `/tmp` (1777) is allowed only when both
//: the directory and the resolved entry are owned by this euid or root.
inline constexpr const char* kStateAncestorWorldWritable = "STATE_ANCESTOR_WORLD_WRITABLE";
//: The entry opened by `openat` is not the inode `fstatat` inspected: the path
//: was switched between look-up and open. Fail closed; never apply the policy to
//: one inode and open another.
inline constexpr const char* kStateAncestorIdentityChanged = "STATE_ANCESTOR_IDENTITY_CHANGED";
inline constexpr const char* kStateParentIdentityChanged = "STATE_PARENT_IDENTITY_CHANGED";
inline constexpr const char* kStatePathIdentityChanged = "STATE_PATH_IDENTITY_CHANGED";

struct StatePathVerdict {
    bool ok = false;
    std::string code;
    std::string reason;
};

//: A state file's immediate parent, held as an opened directory fd.
//:
//: `open` walks from `/` component by component with
//: `openat(current, component, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)`,
//: `fstat`s every opened inode, and keeps only the immediate parent. The file is
//: then addressed by `basename` relative to that fd, never by the full path.
class StateDirectory {
public:
    StateDirectory();
    ~StateDirectory();
    StateDirectory(StateDirectory&& other) noexcept;
    StateDirectory& operator=(StateDirectory&& other) noexcept;
    StateDirectory(const StateDirectory&) = delete;
    StateDirectory& operator=(const StateDirectory&) = delete;

    //: Observation seams for deterministic race tests, like the injected clocks
    //: elsewhere in this tree. Production passes neither. `after_lookup` is
    //: called after a component's `fstatat` and before its `openat`;
    //: `after_file_lookup` after the state file's `fstatat` and before its
    //: writability probe. A test uses them to replace the entry and assert the
    //: dev+ino identity check fails closed.
    using OpenObservationHook = std::function<void(const std::string& component)>;

    //: Open and verify the immediate parent of `path`, or refuse with a named
    //: verdict. On success the fd is kept for `write_atomically`.
    bool open(const std::string& path, StatePathVerdict& verdict,
              const OpenObservationHook& after_lookup = {},
              const std::function<void()>& after_file_lookup = {});

    bool valid() const { return dirfd_ >= 0; }
    int fd() const { return dirfd_; }
    const std::string& basename() const { return basename_; }
    const std::string& parent_path() const { return parent_path_; }

    //: Read the state file relative to the trusted parent fd. `present` is
    //: false for a first run (ENOENT). The opened file is `fstat`ed again; a
    //: symlink or a non-regular/replaced file is refused. The read is capped at
    //: 1 MiB so a corrupted file cannot consume unbounded memory.
    bool read(std::string& text, bool& present, std::string& error) const;

    //: Write `text` into the trusted directory: a unique temp name created by
    //: `openat(dirfd, ..., O_CREAT | O_EXCL | O_NOFOLLOW, 0600)`, `fsync`, close,
    //: `renameat(dirfd, temp, dirfd, basename)`, `fsync(dirfd)`. False means the
    //: durable file was not established (or the path moved after the write); the
    //: caller must not issue the effect.
    bool write_atomically(const std::string& text, std::string& error) const;

private:
    struct Ancestry;
    void close();

    int dirfd_ = -1;
    std::string basename_;
    std::string parent_path_;
    std::unique_ptr<Ancestry> ancestry_;
};

//: Whether `path` is a state file this account can durably own, implemented
//: through the SAME `StateDirectory::open` walk the writer uses. The temporary
//: fd is closed before returning; the writer opens its own and keeps it.
StatePathVerdict check_state_path(const std::string& path);

}  // namespace nodecore::supervisor
