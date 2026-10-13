#include "nodecore/file_source.hpp"

#include <dirent.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <cstring>

namespace nodecore {
namespace {

// A single sysfs/procfs read is a few kilobytes at most. The cap exists so a
// misdirected path cannot make the collector allocate without bound on a host
// that is shared with other work.
constexpr std::size_t kMaxReadBytes = 8u * 1024u * 1024u;

}  // namespace

std::string errno_name(int error_number) {
    switch (error_number) {
        case 0: return "";
        case EPERM: return "EPERM";
        case ENOENT: return "ENOENT";
        case EINTR: return "EINTR";
        case EIO: return "EIO";
        case ENXIO: return "ENXIO";
        case EACCES: return "EACCES";
        case EFAULT: return "EFAULT";
        case EINVAL: return "EINVAL";
        case ENODEV: return "ENODEV";
        case EISDIR: return "EISDIR";
        case ENOTDIR: return "ENOTDIR";
        case ENAMETOOLONG: return "ENAMETOOLONG";
        case ELOOP: return "ELOOP";
        case ENOSPC: return "ENOSPC";
        case EROFS: return "EROFS";
        // ENOTSUP and EOPNOTSUPP are the same value on Linux, so only one of
        // them can appear here; EOPNOTSUPP is the name the kernel itself uses.
        case EOPNOTSUPP: return "EOPNOTSUPP";
        case ENOMEM: return "ENOMEM";
        case EOVERFLOW: return "EOVERFLOW";
        default: break;
    }
    char buffer[24];
    std::snprintf(buffer, sizeof(buffer), "ERRNO_%d", error_number);
    return buffer;
}

ReadResult SystemFileSource::read_file(const std::string& path) const {
    const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        const int saved = errno;
        return ReadResult::failure(saved, errno_name(saved));
    }
    std::string content;
    char buffer[16 * 1024];
    while (true) {
        const ssize_t got = ::read(fd, buffer, sizeof(buffer));
        if (got < 0) {
            const int saved = errno;
            // A short read is not a partial file, it is a failed read: return the
            // failure rather than the prefix, because a truncated /proc/stat
            // parses to wrong counters and a wrong counter is indistinguishable
            // from a real one downstream.
            ::close(fd);
            return ReadResult::failure(saved, errno_name(saved));
        }
        if (got == 0) {
            break;
        }
        if (content.size() + static_cast<std::size_t>(got) > kMaxReadBytes) {
            ::close(fd);
            return ReadResult::failure(EFBIG, "EFBIG");
        }
        content.append(buffer, static_cast<std::size_t>(got));
    }
    ::close(fd);
    return ReadResult::success(std::move(content));
}

ReadLinkResult SystemFileSource::read_link(const std::string& path) const {
    std::size_t size = 256;
    while (size <= 4096) {
        std::string buffer(size, '\0');
        const ssize_t got = ::readlink(path.c_str(), buffer.data(), buffer.size());
        if (got < 0) {
            const int saved = errno;
            return ReadLinkResult{false, {}, saved, errno_name(saved)};
        }
        if (static_cast<std::size_t>(got) < buffer.size()) {
            buffer.resize(static_cast<std::size_t>(got));
            return ReadLinkResult{true, std::move(buffer), 0, ""};
        }
        size *= 2;
    }
    return ReadLinkResult{false, {}, ENAMETOOLONG, "ENAMETOOLONG"};
}

bool SystemFileSource::exists(const std::string& path) const {
    return ::access(path.c_str(), F_OK) == 0;
}

SpaceResult SystemFileSource::stat_space(const std::string& path) const {
    struct statvfs info {};
    if (::statvfs(path.c_str(), &info) != 0) {
        const int saved = errno;
        SpaceResult result;
        result.error_number = saved;
        result.error_name = errno_name(saved);
        return result;
    }
    SpaceResult result;
    result.ok = true;
    const std::uint64_t block = info.f_frsize != 0 ? info.f_frsize : info.f_bsize;
    result.total_bytes = static_cast<std::uint64_t>(info.f_blocks) * block;
    result.free_bytes = static_cast<std::uint64_t>(info.f_bfree) * block;
    result.available_bytes = static_cast<std::uint64_t>(info.f_bavail) * block;
    if (info.f_files != 0) {
        result.inodes_reported = true;
        result.inodes_total = static_cast<std::uint64_t>(info.f_files);
        result.inodes_free = static_cast<std::uint64_t>(info.f_ffree);
    }
    return result;
}

std::vector<std::string> SystemFileSource::list_dir(const std::string& path) const {
    std::vector<std::string> names;
    DIR* dir = ::opendir(path.c_str());
    if (dir == nullptr) {
        return names;
    }
    while (const struct dirent* entry = ::readdir(dir)) {
        const char* name = entry->d_name;
        if (std::strcmp(name, ".") == 0 || std::strcmp(name, "..") == 0) {
            continue;
        }
        names.emplace_back(name);
    }
    ::closedir(dir);
    return names;
}

void FixtureFileSource::add_file(std::string path, std::string content) {
    Entry entry;
    entry.kind = Entry::Kind::File;
    entry.content = std::move(content);
    entries_.emplace_back(std::move(path), std::move(entry));
}

void FixtureFileSource::add_dir(std::string path, std::vector<std::string> entries) {
    Entry entry;
    entry.kind = Entry::Kind::Dir;
    entry.entries = std::move(entries);
    entries_.emplace_back(std::move(path), std::move(entry));
}

void FixtureFileSource::add_symlink(std::string path, std::string target) {
    Entry entry;
    entry.kind = Entry::Kind::Symlink;
    entry.content = std::move(target);
    entries_.emplace_back(std::move(path), std::move(entry));
}

void FixtureFileSource::add_space(std::string path, SpaceResult result) {
    Entry entry;
    entry.kind = Entry::Kind::File;
    entry.space = result;
    entries_.emplace_back(std::move(path), std::move(entry));
}

void FixtureFileSource::add_read_error(std::string path, int error_number, std::string error_name) {
    Entry entry;
    entry.kind = Entry::Kind::ReadError;
    entry.error_number = error_number;
    entry.error_name = std::move(error_name);
    entries_.emplace_back(std::move(path), std::move(entry));
}

const FixtureFileSource::Entry* FixtureFileSource::find(const std::string& path) const {
    for (const auto& item : entries_) {
        if (item.first == path) {
            return &item.second;
        }
    }
    return nullptr;
}

ReadResult FixtureFileSource::read_file(const std::string& path) const {
    const Entry* entry = find(path);
    if (entry == nullptr) {
        return ReadResult::failure(ENOENT, "ENOENT");
    }
    if (entry->kind == Entry::Kind::ReadError) {
        return ReadResult::failure(entry->error_number, entry->error_name);
    }
    if (entry->kind != Entry::Kind::File) {
        return ReadResult::failure(EISDIR, "EISDIR");
    }
    return ReadResult::success(entry->content);
}

ReadLinkResult FixtureFileSource::read_link(const std::string& path) const {
    const Entry* entry = find(path);
    if (entry == nullptr || entry->kind != Entry::Kind::Symlink) {
        return ReadLinkResult{false, {}, ENOENT, "ENOENT"};
    }
    return ReadLinkResult{true, entry->content, 0, ""};
}

bool FixtureFileSource::exists(const std::string& path) const {
    const Entry* entry = find(path);
    return entry != nullptr && entry->kind != Entry::Kind::ReadError;
}

SpaceResult FixtureFileSource::stat_space(const std::string& path) const {
    const Entry* entry = find(path);
    if (entry == nullptr || !entry->space.ok) {
        SpaceResult result;
        result.error_number = ENOENT;
        result.error_name = "ENOENT";
        return result;
    }
    return entry->space;
}

std::vector<std::string> FixtureFileSource::list_dir(const std::string& path) const {
    const Entry* entry = find(path);
    if (entry == nullptr || entry->kind != Entry::Kind::Dir) {
        return {};
    }
    return entry->entries;
}

}  // namespace nodecore
