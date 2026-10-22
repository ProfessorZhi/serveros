# Standalone portable policy suites plus actual Win32 IPC/recovery adapters.
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
  cmake_minimum_required(VERSION 3.20)
  project(serveros_windows_core_contract LANGUAGES CXX VERSION 0.1.0)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_CXX_EXTENSIONS OFF)
  enable_testing()

  option(SERVEROS_WINDOWS_STATIC_RUNTIME "Link Windows C++ runtime into executables" ON)
  if(SERVEROS_WINDOWS_STATIC_RUNTIME AND MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
  endif()

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
 target_compile_definitions(windows_core_scm PUBLIC _WIN32_WINNT=0x0A00 NTDDI_VERSION=0x0A000004)
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
set_tests_properties(windows_watch_contract PROPERTIES TIMEOUT 15)

add_executable(windows-core-watch src/watch_main.cpp)
target_link_libraries(windows-core-watch PRIVATE windows_core_scm)

if(MINGW AND SERVEROS_WINDOWS_STATIC_RUNTIME)
  target_link_options(windows_core_scm INTERFACE -static)
endif()
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
  execute_process(COMMAND git -C "${CMAKE_CURRENT_SOURCE_DIR}" rev-parse HEAD
    OUTPUT_VARIABLE SERVEROS_WINDOWS_SOURCE_REVISION OUTPUT_STRIP_TRAILING_WHITESPACE)
  if(NOT SERVEROS_WINDOWS_SOURCE_REVISION MATCHES "^[0-9a-f]+$")
    set(SERVEROS_WINDOWS_SOURCE_REVISION "unknown")
  endif()
  target_compile_definitions(windows_core_scm PUBLIC
    SERVEROS_WINDOWS_COMPONENT_VERSION="${PROJECT_VERSION}"
    SERVEROS_WINDOWS_SOURCE_REVISION="${SERVEROS_WINDOWS_SOURCE_REVISION}")
  if(WIN32)
  install(TARGETS windows-core-watch windows-core-status RUNTIME DESTINATION bin)
  install(FILES README.md HOSTED-WINDOWS-BUILD.md "${CMAKE_CURRENT_SOURCE_DIR}/../../../LICENSE" DESTINATION .)
  if(MINGW)
    install(DIRECTORY licenses DESTINATION .)
  endif()
  configure_file(package-manifest.json.in "${CMAKE_CURRENT_BINARY_DIR}/manifest.json" @ONLY)
  install(FILES "${CMAKE_CURRENT_BINARY_DIR}/manifest.json" DESTINATION .)
  set(CPACK_GENERATOR ZIP)
  set(CPACK_PACKAGE_NAME "serveros-windows-core")
  set(CPACK_PACKAGE_VERSION "${PROJECT_VERSION}")
  string(SUBSTRING "${SERVEROS_WINDOWS_SOURCE_REVISION}" 0 12 _package_revision)
  set(CPACK_PACKAGE_FILE_NAME "serveros-windows-core-${PROJECT_VERSION}-g${_package_revision}-win64")
  include(CPack)
  endif()
endif()

option(SERVEROS_WINDOWS_NATIVE_EFFECT_TEST "Opt in to one current-CI-user ScheduledTask effect fixture" OFF)
if(WIN32 AND SERVEROS_WINDOWS_NATIVE_EFFECT_TEST)
  add_executable(test_windows_native_task_effect tests/test_native_task_effect.cpp)
  target_link_libraries(test_windows_native_task_effect PRIVATE windows_core_scm)
  target_include_directories(test_windows_native_task_effect PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src")
  if(MINGW)
    target_link_options(test_windows_native_task_effect PRIVATE -municode)
  endif()
  add_test(NAME windows_native_task_effect COMMAND test_windows_native_task_effect --evidence "${CMAKE_CURRENT_BINARY_DIR}/windows-native-task-evidence.json")
  set_tests_properties(windows_native_task_effect PROPERTIES TIMEOUT 45)
endif()
