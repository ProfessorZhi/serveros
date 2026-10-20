# Hosted Windows contract build — reviewer pack

**Status label: `HOSTED_WINDOWS_BUILD/UNIT`.** This says the `_WIN32` half of
`win32_scm.cpp` compiles with MSVC on a GitHub-hosted Windows runner and the
synthetic suites pass there. It is **not** `REAL_DEVICE`, **not** `CLIENT_E2E`,
and **not** a reading of any Windows machine — the suites never call the
runner's Service Control Manager.

## Source and workflow

- Base: PR #309 head `c97eb410f63d4eb7664824025d459de071ac4b6b`.
- Workflow: `.github/workflows/serveros-native-windows-contract.yml`.
- Trigger: `pull_request` only, paths `native/node-core/windows-core/**` and the
  workflow file itself. No schedule, tag, release or default-branch run.
- Permissions: `contents: read`. One third-party action:
  `actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683` (v4.2.2), pinned
  by full SHA, `persist-credentials: false`.
- Job timeout: 15 minutes. Observed duration: 48 seconds.

## Runs

| run | head | result | note |
|---|---|---|---|
| [39390279049](https://github.com/ProfessorZhi/serveros/actions/runs/39390279049) | `5000294` | failure (18s) | configure named `Visual Studio 17 2022`, but the image ships VS 18 2026. Kept in the record rather than deleted. |
| [39390383252](https://github.com/ProfessorZhi/serveros/actions/runs/39390383252) | `78f2399` | **success (48s)** | CMake default generator; job `118520205978`. |

The first failure is a workflow defect, not a source finding: no compiler was
invoked, so it produced no build evidence either way.

## Runner and toolchain (run 39390383252)

```
Runner image: windows-2025-vs2026, version 20261007.264.1
OS:           Microsoft Windows Server 2025, 10.0.26100
Visual Studio: 18 Enterprise (C:\Program Files\Microsoft Visual Studio\18\Enterprise)
CMake:        4.4.4
CXX:          MSVC 19.51.36260.0
cl.exe:       ...\VC\Tools\MSVC\14.51.36231\bin\Hostx64\x64\cl.exe
```

## Build and test evidence

```
-- Building for: Visual Studio 18 2026
compiled binary present: build-windows/Release/test_windows_scm_status.exe
compiled binary present: build-windows/Release/test_windows_core_source_contract.exe
compiled binary present: build-windows/Release/test_windows_status_document.exe
1: test_scm_status: 74 checks, 0 failures
2: test_win32_source_contract: 55 checks, 0 failures
3: test_windows_status_document: 139 checks, 0 failures
100% tests passed out of 3
```

Total: **3/3 suites, 268 checks, 0 failures**.

The Windows check count is 55 where the Linux build reports 59, and the
difference is the platform branch, not a skipped test: on `_WIN32` the suite
asserts that the compiled half is present (one check) and deliberately does not
run the four `UNSUPPORTED_PLATFORM` observations, which describe a build where
the half is absent. The `\\remote\x` negative case still runs on both and is
rejected by name validation before any API is reached.

## Local pre-check (Linux, GCC 13.3, standalone configure)

```
cmake -S native/node-core/windows-core -B <out> -DCMAKE_BUILD_TYPE=Release
ctest --test-dir <out> --output-on-failure -V
```

`test_scm_status` 74, `test_win32_source_contract` 59,
`test_windows_status_document` 139 — **3/3 passed, 272 checks**. This validates
the standalone CMake path and the platform-neutral behaviour; it is not Windows
evidence.

## What the run proves, and what it does not

Proved:

- `win32_scm.cpp` compiles under MSVC with `_WIN32` defined, including the four
  SCM query calls and the `static_assert`s that compare every restated Win32
  constant against the real `<winsvc.h>`.
- The three test binaries link and run on a Windows kernel.
- The `_WIN32` branch of the source-contract suite is the one that executed.

Not proved:

- That a real SCM answers as expected. No `OpenSCManagerW` or `OpenServiceW`
  call is made by any test; a hosted runner's service database is not a fixture.
- The named pipe, DACL and token check: they are not implemented, and the status
  document still reports the IPC route as `UNSUPPORTED`.
- Non-ASCII service-name handling. `Win32ScmQuery::query` currently widens the
  name byte-by-byte into `std::wstring`; that path is compiled but never
  exercised, and it is recorded here rather than quietly changed.
- Any user device, credential, OAuth scope, registry value, service state,
  scheduled task, tunnel or network endpoint.

## Reproduce

Push a commit touching `native/node-core/windows-core/**` to a pull request in
this repository, or re-run the job from the PR checks page. The workflow uses
only the runner's preinstalled Visual Studio and CMake; it installs nothing.
