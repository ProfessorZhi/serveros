#include "win32_detail.hpp"
#include "windows-core/recovery.hpp"
#include "windows-core/watch.hpp"
#include <iomanip>
#include <sstream>
#if defined(_WIN32)
#include <cstddef>
#include <shlobj.h>
#include <bcrypt.h>
#include <charconv>
namespace nodecore::windows_core {
namespace {
bool owner_only(HANDLE h, const std::string &owner, bool directory) {
    BY_HANDLE_FILE_INFORMATION info{};
    if (!::GetFileInformationByHandle(h, &info) ||
        (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) ||
        static_cast<bool>(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != directory ||
        (!directory && info.nNumberOfLinks != 1))
        return false;
    PSID sid = nullptr;
    PACL acl = nullptr;
    PSECURITY_DESCRIPTOR sd = nullptr;
    if (::GetSecurityInfo(h, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                          &sid, nullptr, &acl, nullptr, &sd) != ERROR_SUCCESS)
        return false;
    bool good = false;
    PSID expected = nullptr;
    std::wstring ws(owner.begin(), owner.end());
    if (::ConvertStringSidToSidW(ws.c_str(), &expected)) {
        SECURITY_DESCRIPTOR_CONTROL flags = 0;
        DWORD revision = 0;
        if (sid && acl && ::EqualSid(sid, expected) &&
            ::GetSecurityDescriptorControl(sd, &flags, &revision) && (flags & SE_DACL_PROTECTED) &&
            acl->AceCount == 1) {
            void *raw = nullptr;
            if (::GetAce(acl, 0, &raw)) {
                auto *ace = static_cast<ACCESS_ALLOWED_ACE *>(raw);
                good =
                    ace->Header.AceType == ACCESS_ALLOWED_ACE_TYPE && ace->Header.AceFlags == 0 &&
                    ::EqualSid(&ace->SidStart, expected) &&
                    ((ace->Mask & GENERIC_ALL) || (ace->Mask & FILE_ALL_ACCESS) == FILE_ALL_ACCESS);
            }
        }
        ::LocalFree(expected);
    }
    ::LocalFree(sd);
    return good;
}
std::wstring state_directory(const std::string &owner, detail::OwnerSecurity &security,
                             std::vector<std::unique_ptr<detail::Handle>> &pins) {
    PWSTR raw = nullptr;
    if (FAILED(::SHGetKnownFolderPath(FOLDERID_LocalAppData, KF_FLAG_DONT_VERIFY, nullptr, &raw)))
        return {};
    std::wstring root(raw);
    ::CoTaskMemFree(raw);
    // Known folders may be redirected. Recovery state must remain available
    // without the network and must support persistent security descriptors.
    wchar_t volume[MAX_PATH]{};
    DWORD flags = 0;
    if (root.size() < 3 || root[1] != L':' || root[2] != L'\\' ||
        !::GetVolumePathNameW(root.c_str(), volume, MAX_PATH) ||
        ::GetDriveTypeW(volume) != DRIVE_FIXED ||
        !::GetVolumeInformationW(volume, nullptr, 0, nullptr, nullptr, &flags, nullptr, 0) ||
        !(flags & FILE_PERSISTENT_ACLS))
        return {};

    // Only this OS-resolved private root is used; no caller-selected pathname.
    // Keep the OS-resolved base open without write/delete sharing. Open
    // children only while every ancestor is pinned against rename/reparse.
    auto base = std::make_unique<detail::Handle>(::CreateFileW(
        root.c_str(), READ_CONTROL | FILE_READ_ATTRIBUTES, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
    BY_HANDLE_FILE_INFORMATION base_info{};
    if (!base->valid() || !::GetFileInformationByHandle(*base, &base_info) ||
        (base_info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) ||
        !(base_info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY))
        return {};
    PSID base_owner = nullptr, expected_owner = nullptr;
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    auto security_error = ::GetSecurityInfo(*base, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION,
                                            &base_owner, nullptr, nullptr, nullptr, &descriptor);
    std::wstring expected(owner.begin(), owner.end());
    bool owner_matches = security_error == ERROR_SUCCESS && base_owner &&
                         ::ConvertStringSidToSidW(expected.c_str(), &expected_owner) &&
                         detail::trusted_profile_owner(base_owner, expected_owner);
    if (expected_owner)
        ::LocalFree(expected_owner);
    if (descriptor)
        ::LocalFree(descriptor);
    if (!owner_matches)
        return {};
    pins.push_back(std::move(base));
    for (const wchar_t *part : {L"ServerOS", L"Core"}) {
        root += L"\\";
        root += part;
        if (!::CreateDirectoryW(root.c_str(), &security.attributes) &&
            ::GetLastError() != ERROR_ALREADY_EXISTS)
            return {};
        auto dir = std::make_unique<detail::Handle>(::CreateFileW(
            root.c_str(), READ_CONTROL | FILE_READ_ATTRIBUTES, FILE_SHARE_READ, nullptr,
            OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
        if (!dir->valid() || !owner_only(*dir, owner, true))
            return {};
        pins.push_back(std::move(dir));
    }
    return root;
}
std::wstring hashed_name(const std::string &key) {
    // Collision only causes a conservative refusal: journal embeds full key.
    std::uint64_t value = 14695981039346656037ULL;
    for (unsigned char c : key) {
        value ^= c;
        value *= 1099511628211ULL;
    }
    std::wostringstream s;
    s << std::hex << std::setfill(L'0') << std::setw(16) << value;
    return s.str() + L".journal";
}
class NativeTransaction final : public RecoveryTransaction {
    std::vector<std::unique_ptr<detail::Handle>> pins_;
    detail::Handle lock_;
    std::wstring path_;
    std::string key_, owner_;
    bool fresh_, locked_ = false;

  public:
    NativeTransaction(HANDLE f, std::wstring path, std::string key, std::string owner, bool fresh,
                      std::vector<std::unique_ptr<detail::Handle>> pins)
        : pins_(std::move(pins)), lock_(f), path_(std::move(path)), key_(std::move(key)),
          owner_(std::move(owner)), fresh_(fresh) {}
    bool acquire() {
        OVERLAPPED ov{};
        locked_ = ::LockFileEx(lock_, LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY, 0,
                               MAXDWORD, MAXDWORD, &ov) != 0;
        return locked_;
    }
    ~NativeTransaction() override {
        if (locked_) {
            OVERLAPPED ov{};
            ::UnlockFileEx(lock_, 0, MAXDWORD, MAXDWORD, &ov);
        }
    }
    bool load(RecoveryState &out) override {
        detail::Handle file(::CreateFileW(path_.c_str(), GENERIC_READ | READ_CONTROL,
                                          FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                                          FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
        if (!file.valid()) {
            if (::GetLastError() == ERROR_FILE_NOT_FOUND && fresh_) {
                out = RecoveryState{};
                return true;
            }
            return false;
        }
        if (!owner_only(file, owner_, false))
            return false;
        LARGE_INTEGER size{};
        if (!::GetFileSizeEx(file, &size) || size.QuadPart < 1 || size.QuadPart > 4096)
            return false;
        std::string text(static_cast<std::size_t>(size.QuadPart), '\0');
        DWORD count = 0;
        if (!::ReadFile(file, text.data(), static_cast<DWORD>(text.size()), &count, nullptr) ||
            count != text.size())
            return false;
        const auto prefix = key_ + "\n";
        if (text.rfind(prefix, 0) != 0)
            return false;
        return decode_recovery_state(text.substr(prefix.size()), out);
    }
    bool save(const RecoveryState &state) override {
        auto text = key_ + "\n" + encode_recovery_state(state);
        if (text.size() > 4096)
            return false;
        auto temp = path_ + L".next";
        detail::OwnerSecurity security(owner_);
        if (!security.descriptor)
            return false;
        {
            // CREATE_NEW refuses a crash-left partial record. Never overwrite a
            // previously valid journal in place: torn writes must not erase pending.
            detail::Handle file(::CreateFileW(
                temp.c_str(), GENERIC_WRITE | READ_CONTROL, 0, &security.attributes, CREATE_NEW,
                FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_WRITE_THROUGH,
                nullptr));
            DWORD n = 0;
            if (!file.valid() || !owner_only(file, owner_, false) ||
                !::WriteFile(file, text.data(), static_cast<DWORD>(text.size()), &n, nullptr) ||
                n != text.size() || !::FlushFileBuffers(file))
                return false;
        }
        // Same directory/volume. Complete flushed record replaces the old record
        // under the permanent lock. Crash before replacement retains pending.
        if (!::MoveFileExW(temp.c_str(), path_.c_str(),
                           MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
            return false;
        fresh_ = false;
        return true;
    }
};
} // namespace
std::unique_ptr<RecoveryTransaction> Win32RecoveryStore::lock(const UnitGrant &grant) {
    if (!valid_grants({grant}) || !grant.enabled || detail::current_sid() != grant.owner_sid)
        return {};
    detail::OwnerSecurity security(grant.owner_sid);
    if (!security.descriptor)
        return {};
    std::vector<std::unique_ptr<detail::Handle>> pins;
    auto root = state_directory(grant.owner_sid, security, pins);
    if (root.empty())
        return {};
    auto key = unit_key(grant);
    auto path = root + L"\\" + hashed_name(key);
    auto lockpath = path + L".lock";
    HANDLE file = ::CreateFileW(
        lockpath.c_str(), GENERIC_READ | GENERIC_WRITE | READ_CONTROL, FILE_SHARE_READ,
        &security.attributes, CREATE_NEW,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_WRITE_THROUGH, nullptr);
    bool created = file != INVALID_HANDLE_VALUE;
    if (!created) {
        if (::GetLastError() != ERROR_FILE_EXISTS && ::GetLastError() != ERROR_ALREADY_EXISTS)
            return {};
        file = ::CreateFileW(lockpath.c_str(), GENERIC_READ | GENERIC_WRITE | READ_CONTROL,
                             FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                             FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT |
                                 FILE_FLAG_WRITE_THROUGH,
                             nullptr);
    }
    if (file == INVALID_HANDLE_VALUE)
        return {};
    if (!owner_only(file, grant.owner_sid, false)) {
        ::CloseHandle(file);
        return {};
    }
    auto tx = std::make_unique<NativeTransaction>(file, path, key, grant.owner_sid, created,
                                                  std::move(pins));
    if (!tx->acquire())
        return {};
    if (created) {
        // Healthy/disabled/unmeasured early returns still need a committed
        // empty journal. Do not make a subsequent absence look like first use.
        RecoveryState initial;
        if (!tx->load(initial) || !tx->save(initial))
            return {};
    }
    return tx;
}
namespace {
bool read_document(const std::wstring &root, const wchar_t *name, const std::string &sid,
                   std::string &out, bool &missing) {
    missing = false;
    detail::Handle file(::CreateFileW((root + L"\\" + name).c_str(), GENERIC_READ | READ_CONTROL,
                                      FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
                                      FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
    if (!file.valid()) {
        missing = ::GetLastError() == ERROR_FILE_NOT_FOUND;
        return false;
    }
    if (!owner_only(file, sid, false))
        return false;
    LARGE_INTEGER size{};
    if (!::GetFileSizeEx(file, &size) || size.QuadPart < 1 || size.QuadPart > 16384)
        return false;
    out.assign(static_cast<std::size_t>(size.QuadPart), '\0');
    DWORD n = 0;
    bool ok = ::ReadFile(file, out.data(), static_cast<DWORD>(out.size()), &n, nullptr) &&
              n == out.size();
    BY_HANDLE_FILE_INFORMATION observed{};
    if (ok && ::GetFileInformationByHandle(file, &observed))
        detail::state_read_file_id =
            (static_cast<std::uint64_t>(observed.nFileIndexHigh) << 32) | observed.nFileIndexLow;
    return ok;
}
bool write_document(const std::wstring &root, const wchar_t *name, const std::string &sid,
                    const std::string &text) {
    if (text.empty() || text.size() > 16384)
        return false;
    detail::OwnerSecurity security(sid);
    if (!security.descriptor)
        return false;
    auto path = root + L"\\" + name;
    auto temp = path + L".next";
    detail::Handle file(::CreateFileW(
        temp.c_str(), GENERIC_WRITE | READ_CONTROL | DELETE, FILE_SHARE_READ | FILE_SHARE_DELETE,
        &security.attributes, CREATE_NEW,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_WRITE_THROUGH, nullptr));
    if (!file.valid()) {
        detail::state_fault("CREATE_TEMP");
        return false;
    }
    auto discard_owned_temp = [](HANDLE owned) {
        FILE_DISPOSITION_INFO disposition{TRUE};
        ::SetFileInformationByHandle(owned, FileDispositionInfo, &disposition, sizeof(disposition));
    };
    DWORD n = 0;
    detail::state_input_bytes = static_cast<DWORD>(text.size());
    BY_HANDLE_FILE_INFORMATION created{};
    if (!owner_only(file, sid, false) ||
        !::WriteFile(file, text.data(), static_cast<DWORD>(text.size()), &n, nullptr) ||
        n != text.size() || !::FlushFileBuffers(file) ||
        !::GetFileInformationByHandle(file, &created)) {
        detail::state_fault("TEMP_WRITE_FLUSH_ID");
        discard_owned_temp(file);
        return false;
    }
    detail::state_written_bytes = n;
    // Rename the exact file handle we created, never reopen it by pathname.
    // POSIX replacement keeps old readers on their old immutable inode.
    detail::Handle prior(::CreateFileW(path.c_str(), READ_CONTROL | FILE_READ_ATTRIBUTES,
                                       FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                       nullptr, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT,
                                       nullptr));
    if (prior.valid()) {
        BY_HANDLE_FILE_INFORMATION info{};
        if (!owner_only(prior, sid, false) || !::GetFileInformationByHandle(prior, &info)) {
            detail::state_fault("TARGET_UNTRUSTED");
            discard_owned_temp(file);
            return false;
        }
        if (info.dwFileAttributes & FILE_ATTRIBUTE_READONLY) {
            ::SetLastError(ERROR_ACCESS_DENIED);
            detail::state_fault("TARGET_READ_ONLY");
            discard_owned_temp(file);
            return false;
        }
    } else if (::GetLastError() != ERROR_FILE_NOT_FOUND) {
        detail::state_fault("TARGET_UNREADABLE");
        discard_owned_temp(file);
        return false;
    }
    // The Win32 wrapper normalizes the absolute DOS pathname. Supply an
    // explicit NUL as required by FILE_RENAME_INFO::FileName, even though
    // FileNameLength excludes it; exact alignment must not remove padding.
    const DWORD name_bytes = static_cast<DWORD>(path.size() * sizeof(wchar_t));
    const DWORD info_bytes =
        static_cast<DWORD>(offsetof(FILE_RENAME_INFO, FileName)) + name_bytes + sizeof(wchar_t);
    std::vector<std::max_align_t> storage((info_bytes + sizeof(std::max_align_t) - 1) /
                                          sizeof(std::max_align_t));
    auto *rename = reinterpret_cast<FILE_RENAME_INFO *>(storage.data());
    rename->Flags = FILE_RENAME_FLAG_REPLACE_IF_EXISTS | FILE_RENAME_FLAG_POSIX_SEMANTICS;
    rename->RootDirectory = nullptr;
    rename->FileNameLength = name_bytes;
    ::CopyMemory(rename->FileName, path.c_str(), name_bytes + sizeof(wchar_t));
    bool renamed = false;
    auto retry_deadline = ::GetTickCount64() + 250;
    for (unsigned attempt = 0; attempt < 26; attempt++) {
        if (::SetFileInformationByHandle(file, FileRenameInfoEx, rename, info_bytes)) {
            renamed = true;
            break;
        }
        auto error = ::GetLastError();
        if ((error == ERROR_SHARING_VIOLATION || error == ERROR_LOCK_VIOLATION) && attempt < 25 &&
            ::GetTickCount64() < retry_deadline) {
            ::Sleep(10);
            continue;
        }
        detail::state_fault(error == ERROR_INVALID_PARAMETER || error == ERROR_INVALID_FUNCTION ||
                                    error == ERROR_NOT_SUPPORTED
                                ? "ATOMIC_RENAME_UNSUPPORTED"
                                : "ATOMIC_HANDLE_REPLACE");
        break;
    }
    if (!renamed) {
        discard_owned_temp(file);
        return false;
    }
    // The record is already published: a failed post-rename flush is unknown,
    // and must never trigger deletion of the now-live target by this handle.
    if (!::FlushFileBuffers(file)) {
        detail::state_fault("POST_RENAME_FLUSH_UNKNOWN");
        return false;
    }
    BY_HANDLE_FILE_INFORMATION observed{};
    if (!::GetFileInformationByHandle(file, &observed)) {
        detail::state_fault("POST_RENAME_ID_UNKNOWN");
        return false;
    }
    detail::state_written_file_id =
        (static_cast<std::uint64_t>(observed.nFileIndexHigh) << 32) | observed.nFileIndexLow;
    detail::state_writer_size = observed.nFileSizeLow;
    detail::Handle verify(::CreateFileW(path.c_str(), GENERIC_READ | READ_CONTROL,
                                        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                        nullptr, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT,
                                        nullptr));
    BY_HANDLE_FILE_INFORMATION reopened{};
    DWORD read = 0;
    std::string confirmed(text.size(), '\0');
    if (!verify.valid() || !owner_only(verify, sid, false) ||
        !::GetFileInformationByHandle(verify, &reopened)) {
        detail::state_fault("PUBLISH_READBACK_OPEN_UNKNOWN");
        return false;
    }
    detail::state_verify_file_id =
        (static_cast<std::uint64_t>(reopened.nFileIndexHigh) << 32) | reopened.nFileIndexLow;
    detail::state_verify_size = reopened.nFileSizeLow;
    bool exact = observed.dwVolumeSerialNumber == reopened.dwVolumeSerialNumber &&
                 detail::state_written_file_id == detail::state_verify_file_id &&
                 reopened.nFileSizeHigh == 0 && reopened.nFileSizeLow == text.size() &&
                 ::ReadFile(verify, confirmed.data(), static_cast<DWORD>(confirmed.size()), &read,
                            nullptr) &&
                 read == confirmed.size() && confirmed == text;
    if (!exact) {
        detail::state_fault("PUBLISH_READBACK_MISMATCH_UNKNOWN");
        return false;
    }
    return true;
}
std::string sha256(const std::string &text) {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    unsigned char output[32];
    if (::BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0)
        return {};
    BCRYPT_HASH_HANDLE hash = nullptr;
    DWORD object_size = 0, received = 0;
    auto result =
        ::BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH, reinterpret_cast<PUCHAR>(&object_size),
                            sizeof(object_size), &received, 0);
    if (result < 0 || object_size == 0 || object_size > 4096) {
        ::BCryptCloseAlgorithmProvider(algorithm, 0);
        return {};
    }
    std::vector<unsigned char> object(object_size);
    result = ::BCryptCreateHash(algorithm, &hash, object.data(), object_size, nullptr, 0, 0);
    if (result >= 0)
        result = ::BCryptHashData(hash, reinterpret_cast<PUCHAR>(const_cast<char *>(text.data())),
                                  static_cast<ULONG>(text.size()), 0);
    if (result >= 0)
        result = ::BCryptFinishHash(hash, output, sizeof(output), 0);
    if (hash)
        ::BCryptDestroyHash(hash);
    ::BCryptCloseAlgorithmProvider(algorithm, 0);
    if (result < 0)
        return {};
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for (auto byte : output)
        out << std::setw(2) << static_cast<unsigned>(byte);
    return out.str();
}
class NativeWatchState final : public LocalWatchState {
    std::vector<std::unique_ptr<detail::Handle>> pins_;
    detail::Handle lock_;
    std::wstring root_;
    std::string sid_;

  public:
    NativeWatchState(std::vector<std::unique_ptr<detail::Handle>> pins, HANDLE lock,
                     std::wstring root, std::string sid)
        : pins_(std::move(pins)), lock_(lock), root_(std::move(root)), sid_(std::move(sid)) {}
    std::string owner_sid() const override { return sid_; }
    bool read_desired(std::string &out) override {
        bool missing = false;
        return read_document(root_, L"desired-state.v1.json", sid_, out, missing);
    }
    bool commit_generation(std::int64_t generation, const std::string &text) override {
        std::string old;
        bool missing = false;
        if (!read_document(root_, L"watch-generation.v1", sid_, old, missing))
            return false;
        std::istringstream in(old);
        std::string schema, digest;
        long long prior = -2;
        if (!(in >> schema >> prior >> digest) ||
            schema != "serveros/windows-watch-generation.v1" || prior < -1 || prior > 2147483647)
            return false;
        in >> std::ws;
        if (!in.eof())
            return false;
        auto current = sha256(text);
        if (current.empty())
            return false;
        if (generation < prior || (generation == prior && current != digest))
            return false;
        if (generation == prior)
            return true;
        return write_document(root_, L"watch-generation.v1", sid_,
                              "serveros/windows-watch-generation.v1\n" +
                                  std::to_string(generation) + "\n" + current + "\n");
    }
    bool publish(const std::string &text) override {
        return write_document(root_, L"watch-status.v1.json", sid_, text);
    }
};
} // namespace
std::unique_ptr<LocalWatchState> open_local_watch_state() {
    auto sid = detail::current_sid();
    if (sid.empty() || sid == "S-1-5-18" || sid == "S-1-5-19" || sid == "S-1-5-20")
        return {};
    detail::OwnerSecurity security(sid);
    std::vector<std::unique_ptr<detail::Handle>> pins;
    auto root = state_directory(sid, security, pins);
    if (root.empty())
        return {};
    auto path = root + L"\\watch.instance.lock";
    HANDLE file = ::CreateFileW(path.c_str(), GENERIC_READ | GENERIC_WRITE | READ_CONTROL,
                                FILE_SHARE_READ, &security.attributes, CREATE_NEW,
                                FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
    bool created = file != INVALID_HANDLE_VALUE;
    if (!created) {
        if (::GetLastError() != ERROR_FILE_EXISTS && ::GetLastError() != ERROR_ALREADY_EXISTS)
            return {};
        file = ::CreateFileW(path.c_str(), GENERIC_READ | GENERIC_WRITE | READ_CONTROL,
                             FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT,
                             nullptr);
    }
    if (file == INVALID_HANDLE_VALUE)
        return {};
    if (!owner_only(file, sid, false)) {
        ::CloseHandle(file);
        return {};
    }
    if (created) {
        std::string old;
        bool missing = false;
        if (!read_document(root, L"watch-generation.v1", sid, old, missing) &&
            (!missing || !write_document(root, L"watch-generation.v1", sid,
                                         "serveros/windows-watch-generation.v1\n-1\n-\n"))) {
            ::CloseHandle(file);
            return {};
        }
    }
    return std::make_unique<NativeWatchState>(std::move(pins), file, root, sid);
}

bool publish_windows_desired(const std::string &text) {
    detail::state_stage = "CONFIG_PARSE";
    detail::state_error = 0;
    auto sid = detail::current_sid();
    WatchConfig config;
    std::string error;
    if (!parse_watch_config(text, sid, config, error) || sid == "S-1-5-18" || sid == "S-1-5-19" ||
        sid == "S-1-5-20")
        return false;
    detail::OwnerSecurity security(sid);
    std::vector<std::unique_ptr<detail::Handle>> pins;
    detail::state_stage = "STATE_DIRECTORY";
    auto root = state_directory(sid, security, pins);
    if (root.empty())
        return false;
    auto path = root + L"\\desired-state.writer.lock";
    detail::Handle lock(::CreateFileW(path.c_str(), GENERIC_READ | GENERIC_WRITE | READ_CONTROL,
                                      FILE_SHARE_READ, &security.attributes, OPEN_ALWAYS,
                                      FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
    if (!lock.valid() || !owner_only(lock, sid, false)) {
        detail::state_fault("CONFIG_WRITER_LOCK");
        return false;
    }
    return write_document(root, L"desired-state.v1.json", sid, text);
}

} // namespace nodecore::windows_core
#else
namespace nodecore::windows_core {
std::unique_ptr<RecoveryTransaction> Win32RecoveryStore::lock(const UnitGrant &) { return {}; }
std::unique_ptr<LocalWatchState> open_local_watch_state() { return {}; }
bool publish_windows_desired(const std::string &) { return false; }
} // namespace nodecore::windows_core
#endif
