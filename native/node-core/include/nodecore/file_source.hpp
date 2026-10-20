// The collector's only door to the machine.
//
// Every system read goes through this interface so a test can hand the collector
// a fixture instead of the host. The interface is deliberately narrow: four
// reads and a directory listing, each returning an outcome that keeps "absent"
// apart from "refused" and from "present but empty". The snapshot needs that
// difference, because "ENOENT" and "EACCES" are different sentences to a reader
// and neither of them is "zero".
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace nodecore {

struct ReadResult {
    bool ok = false;
    std::string content;
    int error_number = 0;
    std::string error_name;

    static ReadResult failure(int error_number, std::string error_name) {
        ReadResult result;
        result.error_number = error_number;
        result.error_name = std::move(error_name);
        return result;
    }
    static ReadResult success(std::string content) {
        ReadResult result;
        result.ok = true;
        result.content = std::move(content);
        return result;
    }
};

struct ReadLinkResult {
    bool ok = false;
    std::string target;
    int error_number = 0;
    std::string error_name;
};

struct SpaceResult {
    bool ok = false;
    std::uint64_t total_bytes = 0;
    std::uint64_t free_bytes = 0;
    std::uint64_t available_bytes = 0;
    // Inodes are their own exhaustion axis, not a second view of the same one:
    // a filesystem runs out of inodes while holding plenty of bytes. Reported
    // separately, and "no inode budget at all" (tmpfs with f_files == 0) is a
    // fact distinct from an empty one.
    bool inodes_reported = false;
    std::uint64_t inodes_total = 0;
    std::uint64_t inodes_free = 0;
    int error_number = 0;
    std::string error_name;
};

class FileSource {
public:
    virtual ~FileSource() = default;

    virtual ReadResult read_file(const std::string& path) const = 0;
    virtual ReadLinkResult read_link(const std::string& path) const = 0;
    virtual bool exists(const std::string& path) const = 0;
    virtual SpaceResult stat_space(const std::string& path) const = 0;

    // One flat listing. Used for /sys/class/net only, which is a small flat
    // directory; the collector never walks the tree.
    virtual std::vector<std::string> list_dir(const std::string& path) const = 0;

    // False when the facts came from a fixture. The snapshot records this, so a
    // snapshot captured from test data can never be handed to a human as a
    // reading of this host.
    virtual bool is_live() const = 0;
};

// Reads the real machine. Read-only throughout: nothing here opens a file for
// writing, and no call takes a lock.
class SystemFileSource final : public FileSource {
public:
    ReadResult read_file(const std::string& path) const override;
    ReadLinkResult read_link(const std::string& path) const override;
    bool exists(const std::string& path) const override;
    SpaceResult stat_space(const std::string& path) const override;
    std::vector<std::string> list_dir(const std::string& path) const override;
    bool is_live() const override { return true; }
};

// Backed by values a test states. A path with no entry reads as ENOENT, which is
// the same answer the host gives for a file that is not there — so a test that
// forgets to provide a fixture exercises the absent path rather than silently
// passing.
class FixtureFileSource final : public FileSource {
public:
    void add_file(std::string path, std::string content);
    void add_dir(std::string path, std::vector<std::string> entries);
    void add_symlink(std::string path, std::string target);
    void add_space(std::string path, SpaceResult result);

    // A read that fails with a chosen errno, to exercise "refused" separately
    // from "absent".
    void add_read_error(std::string path, int error_number, std::string error_name);

    ReadResult read_file(const std::string& path) const override;
    ReadLinkResult read_link(const std::string& path) const override;
    bool exists(const std::string& path) const override;
    SpaceResult stat_space(const std::string& path) const override;
    std::vector<std::string> list_dir(const std::string& path) const override;
    bool is_live() const override { return false; }

private:
    struct Entry {
        enum class Kind { File, Dir, Symlink, ReadError };
        Kind kind = Kind::File;
        std::string content;
        std::vector<std::string> entries;
        SpaceResult space;
        int error_number = 0;
        std::string error_name;
    };
    std::vector<std::pair<std::string, Entry>> entries_;

    const Entry* find(const std::string& path) const;
};

// errno name without strerror(): strerror is locale-dependent and its text is
// not part of any contract, so the snapshot carries the portable macro name.
std::string errno_name(int error_number);

}  // namespace nodecore
