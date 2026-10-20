# The Windows native Core — platform source, built HERE only as far as it is
# portable.
#
# WHAT THIS TARGET IS. A pure parser and a typed state machine for what the
# Windows Service Control Manager says about a service, plus the contract for the
# Core's own secured status shape. It compiles on Linux because none of it is
# Windows-specific: the input is text, the output is a struct, and the process
# that would produce that text is behind an interface.
#
# WHAT THIS TARGET IS NOT, AND MUST NOT BE PRESENTED AS. A working Windows
# binary. There is no cross compiler on this host (no x86_64-w64-mingw32-g++, no
# clang-cl), so the `_WIN32` half — the named pipe, the DACL, the token check —
# is NOT COMPILED here and its build status is BUILD_UNVERIFIED. The live Windows
# verification is a separate, separately authorised step (issue #246).

add_library(windows_core_scm STATIC
  src/scm_status.cpp
  # The `_WIN32` half and its platform-neutral mapping. The file always compiles;
  # the four API calls inside it are `#if defined(_WIN32)` and are NOT compiled
  # here, because this host has no cross compiler. The fallback path — which IS
  # compiled and tested — answers UNSUPPORTED_PLATFORM rather than an empty list.
  src/win32_scm.cpp
  # The Task Scheduler vocabulary. Separate words, no SCM derivation.
  src/task_status.cpp
  # The status envelope: a pure document builder, the same readiness/admission
  # words as the Linux side, and a constant UNSUPPORTED for the route this build
  # cannot verify.
  src/status_document.cpp
)
target_include_directories(windows_core_scm PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")
target_link_libraries(windows_core_scm PUBLIC nodecore_supervisor)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(windows_core_scm PRIVATE -Wall -Wextra -Wpedantic)
endif()

# The parser, against synthetic SCM answers. No process, no Windows, no fixture
# that claims to be a real reading.
#
# `check.hpp` is the tree's own tiny harness, one directory up — the same file
# every other suite in `native/node-core` uses rather than a second one.
add_executable(test_windows_scm_status tests/test_scm_status.cpp)
target_link_libraries(test_windows_scm_status PRIVATE windows_core_scm)
target_include_directories(test_windows_scm_status PRIVATE
  "${CMAKE_CURRENT_SOURCE_DIR}/../tests")
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(test_windows_scm_status PRIVATE -Wall -Wextra -Wpedantic)
endif()
add_test(NAME windows_scm_status COMMAND test_windows_scm_status)

# The decision half of the `_WIN32` adapter, the IPC status, and the task
# vocabulary — all platform-neutral, all tested on this host.
add_executable(test_windows_core_source_contract tests/test_win32_source_contract.cpp)
target_link_libraries(test_windows_core_source_contract PRIVATE windows_core_scm)
target_include_directories(test_windows_core_source_contract PRIVATE
  "${CMAKE_CURRENT_SOURCE_DIR}/../tests")
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(test_windows_core_source_contract PRIVATE -Wall -Wextra -Wpedantic)
endif()
add_test(NAME windows_core_source_contract COMMAND test_windows_core_source_contract)

# The status envelope: the typed document a Windows Core could answer with, the
# schema it must fit, and the honest UNSUPPORTED for the route this host cannot
# verify. Pure: a struct in, JSON out, no process and no Windows machine.
add_executable(test_windows_status_document tests/test_status_document.cpp)
target_link_libraries(test_windows_status_document PRIVATE windows_core_scm)
target_include_directories(test_windows_status_document PRIVATE
  "${CMAKE_CURRENT_SOURCE_DIR}/../tests"
  "${CMAKE_CURRENT_SOURCE_DIR}/../supervisor/src")
target_compile_definitions(test_windows_status_document PRIVATE
  WINDOWS_STATUS_SCHEMA_PATH="${CMAKE_CURRENT_SOURCE_DIR}/../contract/windows-core-status.v1.schema.json"
  CORE_LITE_SCHEMA_PATH="${CMAKE_CURRENT_SOURCE_DIR}/../contract/core-lite-status.v1.schema.json")
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(test_windows_status_document PRIVATE -Wall -Wextra -Wpedantic)
endif()
add_test(NAME windows_core_status_document COMMAND test_windows_status_document)
