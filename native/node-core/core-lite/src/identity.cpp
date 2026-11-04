// The runtime identity readback. See `identity.hpp` for why it is apart.
#include "corelite/identity.hpp"

#include <fstream>
#include <string>
#include <vector>

#if defined(__linux__) || defined(__unix__)
#include <pwd.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#elif defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <aclapi.h>
#include <sddl.h>
#include <shlobj.h>
#endif

namespace nodecore::corelite {

namespace {

//: Trim surrounding whitespace. A one-line declaration file is permitted to end
//: with a newline; anything else is left exactly as written.
std::string trim(const std::string& value) {
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) {
        return "";
    }
    const auto last = value.find_last_not_of(" \t\r\n");
    return value.substr(first, last - first + 1);
}

#if defined(__linux__) || defined(__unix__)

//: The largest a declaration may be. It is one line holding a `device:<id>`; a
//: file larger than this is not that file, whatever it contains.
constexpr std::size_t kMaxDeclarationBytes = 4096;

std::vector<std::string> split_path(const std::string& relpath) {
    std::vector<std::string> parts;
    std::string current;
    for (const char c : relpath) {
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

//: Walk `relpath` under `home`, refusing anything that could be pointing
//: somewhere else. Each component is `lstat`ed, so a SYMLINK is seen as a symlink
//: rather than followed — with a plain `stat` the check would describe the
//: target and pass.
bool walk_owned_path(const std::string& home, long expected_uid,
                     const std::vector<std::string>& parts, bool final_is_file,
                     std::string& path_out, std::string& error) {
    std::string path = home;
    for (std::size_t index = 0; index < parts.size(); ++index) {
        path += "/";
        path += parts[index];
        const bool last = (index + 1 == parts.size());
        struct stat info;
        if (::lstat(path.c_str(), &info) != 0) {
            error = "no declaration at the declared path (\"" + parts[index] +
                    "\" is not there)";
            return false;
        }
        if (S_ISLNK(info.st_mode)) {
            error = "the declaration path contains a symlink (\"" + parts[index] +
                    "\"); a declaration reached through a link is a declaration about whatever the "
                    "link points at, which is not necessarily this account's";
            return false;
        }
        if (last && final_is_file) {
            if (!S_ISREG(info.st_mode)) {
                error = "the declaration is not a regular file";
                return false;
            }
        } else if (!S_ISDIR(info.st_mode)) {
            error = "\"" + parts[index] + "\" is not a directory";
            return false;
        }
        if (info.st_uid != static_cast<uid_t>(expected_uid)) {
            error = "\"" + parts[index] + "\" is owned by another account";
            return false;
        }
        if ((info.st_mode & (S_IWGRP | S_IWOTH)) != 0) {
            error = "\"" + parts[index] +
                    "\" is writable by group or other, so the declaration is one anybody can "
                    "rewrite";
            return false;
        }
        if (last && final_is_file && info.st_size > 0 &&
            static_cast<std::size_t>(info.st_size) > kMaxDeclarationBytes) {
            error = "the declaration is larger than a one-line device reference";
            return false;
        }
    }
    path_out = path;
    return true;
}

#endif  // __linux__ || __unix__

#if defined(_WIN32)

constexpr std::size_t kMaxDeclarationBytesWin = 4096;

std::string narrow(const wchar_t* text) {
    const int size = ::WideCharToMultiByte(CP_UTF8, 0, text, -1, nullptr, 0, nullptr, nullptr);
    if (size <= 1) {
        return std::string();
    }
    std::string out(static_cast<std::size_t>(size - 1), '\0');
    ::WideCharToMultiByte(CP_UTF8, 0, text, -1, out.data(), size, nullptr, nullptr);
    return out;
}

std::wstring widen(const std::string& text) {
    if (text.empty()) {
        return std::wstring();
    }
    const int size = ::MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()),
                                           nullptr, 0);
    std::wstring out(static_cast<std::size_t>(size > 0 ? size : 0), L'\0');
    if (size > 0) {
        ::MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), out.data(),
                              size);
    }
    return out;
}

//: One token field, read from this process's own token, never from the
//: environment.
bool token_field(TOKEN_INFORMATION_CLASS which, std::vector<unsigned char>& buffer) {
    HANDLE token = nullptr;
    if (!::OpenProcessToken(::GetCurrentProcess(), TOKEN_QUERY, &token)) {
        return false;
    }
    DWORD size = 0;
    ::GetTokenInformation(token, which, nullptr, 0, &size);
    buffer.assign(size, 0);
    const BOOL ok = size > 0 && ::GetTokenInformation(token, which, buffer.data(), size, &size);
    ::CloseHandle(token);
    return ok == TRUE;
}

//: The same rules as the POSIX walk: every component exists, none is a reparse
//: point (a junction or symlink would make the declaration a statement about
//: whatever it points at), each is a directory except the last, which is a
//: file, and each is OWNED by this process's account.
bool walk_owned_path_win(const std::string& home, const std::vector<std::string>& parts,
                         std::string& path_out, std::string& error) {
    // "This account's" is either SID the token itself would stamp on a new
    // object: the user, or the token's default owner. An elevated
    // administrator's token makes BUILTIN\Administrators the owner of what it
    // creates, so a file this same token wrote carries that SID instead.
    std::vector<unsigned char> user_buffer;
    std::vector<unsigned char> owner_buffer;
    if (!token_field(TokenUser, user_buffer) || !token_field(TokenOwner, owner_buffer)) {
        error = "this process's token could not be read, so ownership cannot be checked";
        return false;
    }
    const PSID me = reinterpret_cast<TOKEN_USER*>(user_buffer.data())->User.Sid;
    const PSID my_default_owner = reinterpret_cast<TOKEN_OWNER*>(owner_buffer.data())->Owner;

    std::string path = home;
    for (std::size_t index = 0; index < parts.size(); ++index) {
        path += "\\";
        path += parts[index];
        const bool last = (index + 1 == parts.size());
        const std::wstring wide = widen(path);
        const DWORD attributes = ::GetFileAttributesW(wide.c_str());
        if (attributes == INVALID_FILE_ATTRIBUTES) {
            error = "no declaration at the declared path (\"" + parts[index] + "\" is not there)";
            return false;
        }
        if ((attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
            error = "the declaration path contains a reparse point (\"" + parts[index] +
                    "\"); a declaration reached through a link is a declaration about whatever "
                    "the link points at, which is not necessarily this account's";
            return false;
        }
        const bool is_dir = (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
        if (last && is_dir) {
            error = "the declaration is not a regular file";
            return false;
        }
        if (!last && !is_dir) {
            error = "\"" + parts[index] + "\" is not a directory";
            return false;
        }
        PSID owner = nullptr;
        PSECURITY_DESCRIPTOR descriptor = nullptr;
        if (::GetNamedSecurityInfoW(wide.c_str(), SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION,
                                    &owner, nullptr, nullptr, nullptr,
                                    &descriptor) != ERROR_SUCCESS) {
            error = "the owner of \"" + parts[index] + "\" could not be read";
            return false;
        }
        const bool mine =
            owner != nullptr && (::EqualSid(owner, me) || ::EqualSid(owner, my_default_owner));
        ::LocalFree(descriptor);
        if (!mine) {
            error = "\"" + parts[index] + "\" is owned by another account";
            return false;
        }
    }
    path_out = path;
    return true;
}

#endif  // _WIN32

}  // namespace

OsIdentity current_user() {
    OsIdentity identity;
#if defined(__linux__) || defined(__unix__)
    const uid_t uid = ::getuid();
    identity.uid = static_cast<long>(uid);
    // `getpwuid_r` rather than `getpwuid`: the reentrant form writes into a
    // caller-owned buffer, so this cannot be corrupted by another thread calling
    // the same function at the same time.
    long buffer_size = ::sysconf(_SC_GETPW_R_SIZE_MAX);
    if (buffer_size <= 0) {
        buffer_size = 4096;
    }
    std::string buffer(static_cast<std::size_t>(buffer_size), '\0');
    struct passwd entry;
    struct passwd* result = nullptr;
    const int rc = ::getpwuid_r(uid, &entry, buffer.data(), buffer.size(), &result);
    if (rc != 0 || result == nullptr) {
        // The UID exists but has no name in the user database (a deleted
        // account, a container's uid without a passwd entry). UNKNOWN, with the
        // errno, rather than a synthesized name.
        identity.error = "getpwuid_r could not resolve this uid (" + std::to_string(rc) + ")";
        return identity;
    }
    identity.user = std::string(result->pw_name);
    // The home comes from the user database, NOT from `$HOME`.
    if (result->pw_dir != nullptr) {
        identity.home = std::string(result->pw_dir);
    }
    identity.known = !identity.user.empty();
    if (!identity.known) {
        identity.error = "the user database returned an empty account name";
    }
    return identity;
#elif defined(_WIN32)
    // The account name the OS returns for this process, and the profile folder
    // the OS records for it (FOLDERID_Profile), never %USERPROFILE%, which the
    // caller of a program controls.
    wchar_t name[257] = {};
    DWORD length = 257;
    if (!::GetUserNameW(name, &length)) {
        identity.error =
            "GetUserNameW failed (GetLastError " + std::to_string(::GetLastError()) + ")";
        return identity;
    }
    identity.user = narrow(name);
    PWSTR profile = nullptr;
    if (SUCCEEDED(::SHGetKnownFolderPath(FOLDERID_Profile, 0, nullptr, &profile))) {
        identity.home = narrow(profile);
    }
    ::CoTaskMemFree(profile);
    identity.uid = -1;  // no POSIX uid on Windows
    identity.known = !identity.user.empty();
    if (!identity.known) {
        identity.error = "GetUserNameW returned an empty account name";
    }
    return identity;
#else
    // NOT a fallback to a guessed name. On a platform without a POSIX user
    // database the identity must come from that platform's adapter.
    identity.error = "UNSUPPORTED_PLATFORM: no POSIX user database on this platform";
    return identity;
#endif
}

NodeIdentity managed_node_identity(const OsIdentity& os, const std::string& relpath) {
    NodeIdentity identity;
    // The identity itself must be established first: without a resolved account
    // there is no home this program is entitled to read a declaration out of.
    if (!os.known) {
        identity.error = "this process's account was not established, so it has no declaration "
                         "path it is entitled to read";
        return identity;
    }
#if defined(_WIN32)
    const bool absolute_home = os.home.size() >= 3 && os.home[1] == ':' &&
                               (os.home[2] == '\\' || os.home[2] == '/');
#else
    const bool absolute_home = !os.home.empty() && os.home.front() == '/';
#endif
    if (!absolute_home) {
        identity.error = "the user database gave no absolute home directory for this account";
        return identity;
    }
    if (relpath.empty()) {
        identity.error = "no declaration path was given";
        return identity;
    }

#if defined(__linux__) || defined(__unix__)
    const std::vector<std::string> parts = split_path(relpath);
    if (parts.empty()) {
        identity.error = "the declaration path is empty";
        return identity;
    }
    std::string path;
    std::string error;
    if (!walk_owned_path(os.home, os.uid, parts, /*final_is_file=*/true, path, error)) {
        identity.error = error;
        return identity;
    }

    std::ifstream file(path, std::ios::binary);
    if (!file) {
        identity.error = "the declaration at the declared path could not be opened";
        return identity;
    }
    std::string content;
    content.resize(kMaxDeclarationBytes);
    file.read(&content[0], static_cast<std::streamsize>(content.size()));
    content.resize(static_cast<std::size_t>(file.gcount()));
    const std::string value = trim(content);
    if (value.empty()) {
        identity.error = "the device declaration is empty";
        return identity;
    }
    // The shape is checked HERE, not downstream: a declaration that is not a
    // `device:<id>` must not reach the status builder as a device reference.
    if (value.compare(0, 7, "device:") != 0 || value.size() <= 7) {
        identity.error = "the device declaration is not of the form device:<id>";
        return identity;
    }
    // A declaration is one line. Trailing content means the file is not the file
    // this program thinks it is, and half of it would be a device reference
    // nobody wrote.
    if (value.find('\n') != std::string::npos) {
        identity.error = "the device declaration is more than one line";
        return identity;
    }
    identity.device_ref = value;
    // Stamped with HOW it was obtained, so the status can say a MATCH here is
    // this account agreeing with itself rather than an independent statement
    // about the device.
    identity.basis = kBasisLocalDeclaration;
    identity.known = true;
    return identity;
#elif defined(_WIN32)
    std::vector<std::string> parts;
    {
        std::string current;
        for (const char c : relpath) {
            if (c == '/' || c == '\\') {
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
    }
    if (parts.empty()) {
        identity.error = "the declaration path is empty";
        return identity;
    }
    std::string path;
    std::string error;
    if (!walk_owned_path_win(os.home, parts, path, error)) {
        identity.error = error;
        return identity;
    }
    std::ifstream file(widen(path), std::ios::binary);
    if (!file) {
        identity.error = "the declaration at the declared path could not be opened";
        return identity;
    }
    std::string content;
    content.resize(kMaxDeclarationBytesWin + 1);
    file.read(&content[0], static_cast<std::streamsize>(content.size()));
    content.resize(static_cast<std::size_t>(file.gcount()));
    if (content.size() > kMaxDeclarationBytesWin) {
        identity.error = "the declaration is larger than a one-line device reference";
        return identity;
    }
    const std::string value = trim(content);
    if (value.empty()) {
        identity.error = "the device declaration is empty";
        return identity;
    }
    if (value.compare(0, 7, "device:") != 0 || value.size() <= 7) {
        identity.error = "the device declaration is not of the form device:<id>";
        return identity;
    }
    if (value.find('\n') != std::string::npos) {
        identity.error = "the device declaration is more than one line";
        return identity;
    }
    identity.device_ref = value;
    identity.basis = kBasisLocalDeclaration;
    identity.known = true;
    return identity;
#else
    identity.error = "UNSUPPORTED_PLATFORM: no POSIX user database on this platform";
    return identity;
#endif
}

}  // namespace nodecore::corelite
