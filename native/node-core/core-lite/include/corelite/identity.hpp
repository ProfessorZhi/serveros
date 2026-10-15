// The RUNTIME identity readback — deliberately a SEPARATE translation unit.
//
// WHY IT IS NOT IN `profile.cpp`
// ------------------------------
// Everything in `profile.cpp` is a pure function of a string: it can be tested
// on any machine, in any account, with no OS at all. This file is the opposite —
// it asks the operating system who we are — so it lives apart, and the tests
// that need a real readback are marked as such. A parser that consulted
// `getuid()` would be untestable off the host it was written on, and a test that
// faked the identity would prove nothing about the gate.
//
// WHAT IT CANNOT DO
// -----------------
// On Windows there is no `getuid()` and no POSIX user database; the current-user
// answer must come from the Windows adapter (see `windows/README.md`). This file
// therefore reports UNKNOWN with a reason on any platform it was not compiled
// for, rather than returning a plausible-looking string. **UNKNOWN is the
// honest answer, and it is the one the status builder expects.**
#pragma once

#include <string>

namespace nodecore::corelite {

//: Who this process runs as, as the OS reports it.
struct OsIdentity {
    bool known = false;
    std::string user;  // the account NAME, never a numeric id in the output
    long uid = -1;
    std::string error;
};

//: The managed Node identity (`device:<id>`) this device is enrolled as.
struct NodeIdentity {
    bool known = false;
    std::string device_ref;
    std::string error;
};

//: The current user, from the OS. Linux: `getuid()` + `getpwuid_r`. Any other
//: platform: `known = false` with the platform named in `error`.
OsIdentity current_user();

//: The device reference from this node's OWN declaration, at `<home>/<relpath>`.
//: A missing or unreadable file is `known = false` — never a default device id,
//: because a guess here would let a Core claim another device's health.
NodeIdentity managed_node_identity(const std::string& home, const std::string& relpath);

}  // namespace nodecore::corelite
