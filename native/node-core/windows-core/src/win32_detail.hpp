#pragma once
#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <aclapi.h>
#include <sddl.h>
#include <string>
#include <utility>
#include <vector>
#include <cstdint>
namespace nodecore::windows_core::detail {
inline thread_local const char *state_stage = "NONE";
inline thread_local DWORD state_error = 0;
inline thread_local std::uint64_t state_written_file_id = 0, state_read_file_id = 0;
inline void state_fault(const char *stage) {
    state_stage = stage;
    state_error = ::GetLastError();
}
struct Handle {
    HANDLE h = INVALID_HANDLE_VALUE;
    explicit Handle(HANDLE v = INVALID_HANDLE_VALUE) : h(v) {}
    ~Handle() {
        if (h && h != INVALID_HANDLE_VALUE)
            ::CloseHandle(h);
    }
    Handle(const Handle &) = delete;
    Handle &operator=(const Handle &) = delete;
    operator HANDLE() const { return h; }
    bool valid() const { return h && h != INVALID_HANDLE_VALUE; }
};
inline std::string token_sid(HANDLE token) {
    DWORD n = 0;
    ::GetTokenInformation(token, TokenUser, nullptr, 0, &n);
    if (n == 0 || n > 4096)
        return {};
    std::vector<unsigned char> buf(n);
    if (!::GetTokenInformation(token, TokenUser, buf.data(), n, &n))
        return {};
    LPWSTR text = nullptr;
    if (!::ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER *>(buf.data())->User.Sid, &text))
        return {};
    std::wstring w(text);
    ::LocalFree(text);
    return std::string(w.begin(), w.end());
}
inline std::string process_sid(HANDLE process) {
    HANDLE raw = nullptr;
    if (!::OpenProcessToken(process, TOKEN_QUERY, &raw))
        return {};
    Handle token(raw);
    return token_sid(token);
}
inline std::string current_sid() { return process_sid(::GetCurrentProcess()); }
inline std::string account_sid(const std::wstring &account) {
    if (account.empty())
        return {};
    PSID raw = nullptr;
    if (::ConvertStringSidToSidW(account.c_str(), &raw)) {
        LPWSTR text = nullptr;
        std::string out;
        if (::ConvertSidToStringSidW(raw, &text)) {
            std::wstring w(text);
            out.assign(w.begin(), w.end());
            ::LocalFree(text);
        }
        ::LocalFree(raw);
        return out;
    }
    DWORD sn = 0, dn = 0;
    SID_NAME_USE use;
    ::LookupAccountNameW(nullptr, account.c_str(), nullptr, &sn, nullptr, &dn, &use);
    if (sn == 0 || sn > 4096 || dn > 512)
        return {};
    std::vector<unsigned char> sid(sn);
    std::vector<wchar_t> domain(dn);
    if (!::LookupAccountNameW(nullptr, account.c_str(), sid.data(), &sn, domain.data(), &dn,
                              &use) ||
        use != SidTypeUser)
        return {};
    LPWSTR text = nullptr;
    if (!::ConvertSidToStringSidW(sid.data(), &text))
        return {};
    std::wstring w(text);
    ::LocalFree(text);
    return std::string(w.begin(), w.end());
}
// Windows can assign an administrator/SYSTEM as profile-directory owner.
// These are OS trust principals, not another ordinary user's profile. Child
// Core directories/files still require an exact current-user-only owner/DACL.
inline bool trusted_profile_owner(PSID actual, PSID current) {
    return actual && current &&
           (::EqualSid(actual, current) || ::IsWellKnownSid(actual, WinLocalSystemSid) ||
            ::IsWellKnownSid(actual, WinBuiltinAdministratorsSid));
}
struct OwnerSecurity {
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    SECURITY_ATTRIBUTES attributes{};
    explicit OwnerSecurity(const std::string &sid) {
        if (sid.empty())
            return;
        std::wstring s(sid.begin(), sid.end());
        std::wstring d = L"O:" + s + L"D:P(A;;GA;;;" + s + L")";
        if (!::ConvertStringSecurityDescriptorToSecurityDescriptorW(d.c_str(), SDDL_REVISION_1,
                                                                    &descriptor, nullptr))
            return;
        attributes = {sizeof(SECURITY_ATTRIBUTES), descriptor, FALSE};
    }
    ~OwnerSecurity() {
        if (descriptor)
            ::LocalFree(descriptor);
    }
};
// AccessCheck requires owner and primary group as well as the DACL. Refuse
// incomplete descriptors before checking actual current-process token rights.
inline bool task_execute_descriptor(const std::wstring &descriptor) {
    PSECURITY_DESCRIPTOR sd = nullptr;
    if (!::ConvertStringSecurityDescriptorToSecurityDescriptorW(descriptor.c_str(), SDDL_REVISION_1,
                                                                &sd, nullptr))
        return false;
    PSID owner = nullptr, group = nullptr;
    BOOL ignored = FALSE;
    bool complete = ::IsValidSecurityDescriptor(sd) &&
                    ::GetSecurityDescriptorOwner(sd, &owner, &ignored) && owner &&
                    ::IsValidSid(owner) && ::GetSecurityDescriptorGroup(sd, &group, &ignored) &&
                    group && ::IsValidSid(group);
    HANDLE raw = nullptr, dup = nullptr;
    bool allowed = false;
    if (complete &&
        ::OpenProcessToken(::GetCurrentProcess(), TOKEN_QUERY | TOKEN_DUPLICATE, &raw)) {
        Handle token(raw);
        if (::DuplicateToken(token, SecurityImpersonation, &dup)) {
            Handle impersonation(dup);
            GENERIC_MAPPING map{FILE_GENERIC_READ, FILE_GENERIC_WRITE, FILE_GENERIC_EXECUTE,
                                FILE_ALL_ACCESS};
            DWORD desired = GENERIC_EXECUTE;
            ::MapGenericMask(&desired, &map);
            alignas(PRIVILEGE_SET) BYTE bytes[4096];
            DWORD count = sizeof(bytes), granted = 0;
            BOOL access = FALSE;
            if (::AccessCheck(sd, impersonation, desired, &map,
                              reinterpret_cast<PRIVILEGE_SET *>(bytes), &count, &granted, &access))
                allowed = access != FALSE;
        }
    }
    ::LocalFree(sd);
    return allowed;
}
inline bool creation_time(HANDLE p, std::uint64_t &out) {
    FILETIME c{}, e{}, k{}, u{};
    if (!::GetProcessTimes(p, &c, &e, &k, &u))
        return false;
    out = (static_cast<std::uint64_t>(c.dwHighDateTime) << 32) | c.dwLowDateTime;
    return true;
}
} // namespace nodecore::windows_core::detail
#endif
