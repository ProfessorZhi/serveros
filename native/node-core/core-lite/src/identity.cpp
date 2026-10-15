// The runtime identity readback. See `identity.hpp` for why it is apart.
#include "corelite/identity.hpp"

#include <fstream>
#include <string>

#if defined(__linux__) || defined(__unix__)
#include <pwd.h>
#include <sys/types.h>
#include <unistd.h>
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
    identity.known = !identity.user.empty();
    if (!identity.known) {
        identity.error = "the user database returned an empty account name";
    }
    return identity;
#else
    // NOT a fallback to a guessed name. On a platform without a POSIX user
    // database the identity must come from that platform's adapter.
    identity.error = "UNSUPPORTED_PLATFORM: no POSIX user database on this platform";
    return identity;
#endif
}

NodeIdentity managed_node_identity(const std::string& home, const std::string& relpath) {
    NodeIdentity identity;
    if (home.empty() || relpath.empty()) {
        identity.error = "no declaration path was given";
        return identity;
    }
    const std::string path = home + "/" + relpath;
    std::ifstream file(path);
    if (!file) {
        identity.error = "no device declaration at the declared path";
        return identity;
    }
    std::string line;
    std::getline(file, line);
    const std::string value = trim(line);
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
    identity.device_ref = value;
    identity.known = true;
    return identity;
}

}  // namespace nodecore::corelite
