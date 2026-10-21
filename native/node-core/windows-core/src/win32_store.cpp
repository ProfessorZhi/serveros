#include "win32_detail.hpp"
#include "windows-core/recovery.hpp"
#include <iomanip>
#include <sstream>
#if defined(_WIN32)
#include <cstddef>
#include <shlobj.h>
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
std::wstring state_directory(const std::string &owner, detail::OwnerSecurity &security) {
    PWSTR raw = nullptr;
    if (FAILED(::SHGetKnownFolderPath(FOLDERID_LocalAppData, KF_FLAG_DONT_VERIFY, nullptr, &raw)))
        return {};
    std::wstring root(raw);
    ::CoTaskMemFree(raw);
    // Only this OS-resolved private root is used; no caller-selected pathname.
    for (const wchar_t *part : {L"ServerOS", L"Core"}) {
        root += L"\\";
        root += part;
        if (!::CreateDirectoryW(root.c_str(), &security.attributes) &&
            ::GetLastError() != ERROR_ALREADY_EXISTS)
            return {};
        detail::Handle dir(::CreateFileW(
            root.c_str(), READ_CONTROL | FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
        if (!dir.valid() || !owner_only(dir, owner, true))
            return {};
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
    detail::Handle lock_;
    std::wstring path_;
    std::string key_, owner_;
    bool fresh_, locked_ = false;

  public:
    NativeTransaction(HANDLE f, std::wstring path, std::string key, std::string owner, bool fresh)
        : lock_(f), path_(std::move(path)), key_(std::move(key)), owner_(std::move(owner)),
          fresh_(fresh) {}
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
    auto root = state_directory(grant.owner_sid, security);
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
    auto tx = std::make_unique<NativeTransaction>(file, path, key, grant.owner_sid, created);
    if (!tx->acquire())
        return {};
    return tx;
}
} // namespace nodecore::windows_core
#else
namespace nodecore::windows_core {
std::unique_ptr<RecoveryTransaction> Win32RecoveryStore::lock(const UnitGrant &) { return {}; }
} // namespace nodecore::windows_core
#endif
