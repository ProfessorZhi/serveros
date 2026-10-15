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
//
// WHAT IT IS NOT
// --------------
// A device reference read here is a LOCAL DECLARATION: a file on this host that
// this account wrote about itself. It is evidence about what the account
// believes, not about which device this is, and it carries
// `basis = LOCAL_DECLARATION` so nothing downstream can mistake the one for the
// other. Establishing what a device actually IS takes an authenticated route
// (a Fleet/Node attestation this stage does not have), and nothing here
// pretends otherwise.
#pragma once

#include <string>

#include "corelite/status.hpp"

namespace nodecore::corelite {

//: Who this process runs as, as the OS reports it.
struct OsIdentity {
    bool known = false;
    std::string user;  // the account NAME, never a numeric id in the output
    //: The account's home directory **as the OS records it** (`pw_dir`), never
    //: `$HOME`. `$HOME` is an environment variable the caller of a program
    //: controls; a declaration file is only worth reading from a path the
    //: user database named, or a caller would point the read at a file they
    //: wrote five seconds ago and call the result the machine's own statement.
    std::string home;
    long uid = -1;
    std::string error;
};

//: The managed Node identity (`device:<id>`) this device declares itself to be.
struct NodeIdentity {
    bool known = false;
    std::string device_ref;
    //: kBasisLocalDeclaration when read from this host's declaration file.
    std::string basis = kBasisNone;
    std::string error;
};

//: The current user, from the OS. Linux: `getuid()` + `getpwuid_r` (which also
//: yields `home`). Any other platform: `known = false` with the platform named
//: in `error`.
OsIdentity current_user();

//: The device reference from this node's OWN declaration, at
//: `<os.home>/<relpath>`.
//:
//: The read is hardened because the file is INPUT, not a trusted document:
//:   * the path comes from `os.home` — the OS's own record for this account, so
//:     a caller cannot redirect it with an environment variable;
//:   * every component of `relpath` is `lstat`ed: no symlink at any level, each
//:     one owned by `os.uid`, and none of them writable by group or other. A
//:     declaration world-writable is a declaration anybody can rewrite;
//:   * the file itself must be a regular file, owned by `os.uid`, and no larger
//:     than a bounded size.
//:
//: A failure at any of those is `known = false` with the reason — never a
//: default device id, because a guess here would let a Core claim another
//: device's health.
//:
//: WHAT THIS DOES NOT DEFEAT, STATED PLAINLY: an attacker who is already the
//: same account can edit the file between the check and the read, and can do
//: anything else in that account too. What the checks above DO stop is a
//: redirection this program would otherwise follow — an environment variable
//: naming the home, a symlink in the path, another user's file, a
//: world-writable declaration. Those are the cases where a program ends up
//: reporting a fact about somewhere else, and they are the ones that matter.
NodeIdentity managed_node_identity(const OsIdentity& os, const std::string& relpath);

}  // namespace nodecore::corelite
