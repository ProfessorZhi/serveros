# Standalone portable policy suites plus actual Win32 IPC/recovery adapters.
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
  cmake_minimum_required(VERSION 3.20)
  project(serveros_windows_core_contract LANGUAGES CXX VERSION 0.1.0)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_CXX_EXTENSIONS OFF)
  enable_testing()

  add_library(nodecore STATIC ../src/json.cpp)
  target_include_directories(nodecore PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/../include")

  # Interface only: the Windows contract uses supervisor constants and the
  # strict JSON reader, both header-only. Compiling the Linux .cpp set here
  # would fail on Windows for symbols this target never references.
  add_library(nodecore_supervisor INTERFACE)
  target_include_directories(nodecore_supervisor INTERFACE
    "${CMAKE_CURRENT_SOURCE_DIR}/../supervisor/include"
    "${CMAKE_CURRENT_SOURCE_DIR}/../supervisor/src")
  target_link_libraries(nodecore_supervisor INTERFACE nodecore)
endif()

add_library(windows_core_scm STATIC
  src/win32_store.cpp
  src/win32_recovery.cpp
  src/win32_watch.cpp
  src/watch.cpp
  src/recovery.cpp
  src/status_pipe.cpp
  src/scm_status.cpp
  # Native calls are _WIN32 gated; Linux tests exercise explicit refusals.
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
target_include_directories(windows_core_scm PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../supervisor/src")

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

add_executable(test_windows_recovery tests/test_recovery.cpp)
target_link_libraries(test_windows_recovery PRIVATE windows_core_scm)
target_include_directories(test_windows_recovery PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../tests")
add_test(NAME windows_recovery_protocol COMMAND test_windows_recovery)

if(WIN32)
 target_link_libraries(windows_core_scm PUBLIC advapi32 taskschd ole32 oleaut32 uuid shell32 ws2_32 bcrypt wtsapi32)
endif()

add_executable(windows-core-status src/main.cpp)
target_link_libraries(windows-core-status PRIVATE windows_core_scm)
find_package(Threads REQUIRED)
add_executable(test_windows_named_pipe tests/test_named_pipe.cpp)
target_link_libraries(test_windows_named_pipe PRIVATE windows_core_scm Threads::Threads)
target_include_directories(test_windows_named_pipe PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../tests" "${CMAKE_CURRENT_SOURCE_DIR}/src")
add_test(NAME windows_named_pipe_and_store COMMAND test_windows_named_pipe)
set_tests_properties(windows_named_pipe_and_store PROPERTIES TIMEOUT 15)

add_executable(test_windows_watch tests/test_watch.cpp)
target_link_libraries(test_windows_watch PRIVATE windows_core_scm)
target_include_directories(test_windows_watch PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../tests")
add_test(NAME windows_watch_contract COMMAND test_windows_watch)

add_executable(windows-core-watch src/watch_main.cpp)
target_link_libraries(windows-core-watch PRIVATE windows_core_scm)
