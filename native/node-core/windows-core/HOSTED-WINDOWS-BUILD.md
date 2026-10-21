# Windows build evidence levels

The GitHub workflow `serveros-native-windows-contract` builds the standalone
Windows target with MSVC and runs all five suites. Its local tests create owner
pipes and unique protected journal records on an ephemeral hosted runner. No test
starts/stops, installs, registers or reconfigures SCM services or scheduled tasks.

A passing hosted job proves HOSTED_WINDOWS_BUILD/LOCAL_IPC, including a genuine
Windows token/pipe and journal exercise. A Linux cross-build proves only actual
_WIN32 compilation/link, even when it emits a PE executable. Linux ctest proves
portable policy/protocol behavior and unsupported-platform refusals. Neither is
REAL_DEVICE or CLIENT_E2E.

A release still requires a trusted local launcher, actual grants/probes, service
wrapper/watch ownership, and authorized real-device Fleet integration acceptance.
No source-level green result closes those gates.

The resident watch extension adds a sixth portable suite for strict owner-local
manifest parsing, functional HTTP/JSON contracts and observe-only/manual/action
policy. Hosted Windows additionally exercises actual owner-only config publication,
singleton watch ownership and persisted generation/hash fences with empty service
fixtures; it still performs no SCM/task effect. The `windows-core-watch.exe`
console/service entry point is linked under both toolchains.
