# The Windows native Core — the SCM side

**Nothing here is a working Windows binary, and nothing here is a Windows
reading.** This directory holds the part of the Windows adapter that is portable:
a pure parser and a typed state machine for what the Service Control Manager says
about a service, plus the closed vocabulary the Linux Core and the Windows Core
share.

| part | status |
|---|---|
| `scm_status.cpp` — the parser and the state machine | **BUILT AND TESTED on Linux**. Text in, struct out; none of it is platform-specific. |
| `status_document.cpp` — the status envelope and its schema | **BUILT AND TESTED on Linux**. A value in, JSON out; `contract/windows-core-status.v1.schema.json` is closed and read by the test. |
| the `_WIN32` half — the SCM calls, the named pipe, the DACL, the token check | **HOSTED_WINDOWS_BUILD when its workflow passes.** `.github/workflows/serveros-native-windows-contract.yml` compiles this translation unit with MSVC on `windows-latest` and asserts the `_WIN32` branch is present. It never calls the runner's SCM, and the Linux build still does not compile this half. |
| a live Windows reading | **NOT RUN.** A later, separately authorised step (issue #246). |

`HOSTED_WINDOWS_BUILD` is not a synonym for "working Windows Core": it says the
`_WIN32` half compiles and its synthetic suites pass, nothing more. It is not
`REAL_DEVICE`, not `CLIENT_E2E`, and not a reading of any user's machine. No
Linux fixture in this tree may be presented as a measurement of a Windows host.

## What it refuses, and why each is a refusal

- **`O_NOFOLLOW` is refused as a Windows rule rather than translated.** A Windows
  pipe has no symlink to follow and no mode bits; its protection is a DACL and
  its peer identity comes from a client token. The Windows hazard is *instance
  squatting*, which a POSIX rule copied over would not address.
- **No verb.** `WindowsServiceQuery` has one method, `query`. There is no start,
  stop, restart, `ChangeServiceConfig`, registry write or shell anywhere in this
  directory — a type that cannot express a write cannot perform one.
- **No remote host.** The contract has no field for one, so a caller cannot name
  a machine. This is the Windows form of the rule `probe.hpp` already applies
  when it refuses a non-loopback TCP target.
- **`UNKNOWN` is never `STOPPED`.** A state word this version does not know, or
  an answer with no `STATE` line, is `measured = false` with a reason.
- **`MANUAL_STOP` is not `STOPPED`.** Both report `SERVICE_STOPPED`; only
  `dwStartType = SERVICE_DISABLED` separates a service a person switched off from
  one that happened to stop.

## The vocabulary is shared with the Linux contract

`windows_admission_for` returns the constants declared in
`supervisor/admission.hpp` — `kAdmitMasked`, `kAdmitNeedsLogin`,
`kAdmitNotInstalled`, `kAdmitInstallStateUnknown` — compared against those
constants by the tests, so a rename on the Linux side fails on this side rather
than silently giving a Fleet reader two spellings for one fact.

## Ownership

This directory is the N2 lane's (issue #246). It shares exactly **one** file with
the rest of the tree: a single `add_subdirectory(windows-core)` line in
`native/node-core/CMakeLists.txt`. The Linux Core lane owns `supervisor/` and
`core-lite/`, and a Windows adapter that edited either would be a lane collision
rather than a feature.
