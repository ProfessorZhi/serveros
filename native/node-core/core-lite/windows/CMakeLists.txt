# The Windows resource collector and status builder: a STANDALONE build.
#
# The root `native/node-core` build cannot configure under MSVC (the supervisor
# is POSIX), so this file names the portable sources it shares with Linux and
# adds the two Win32 ones. The document both platforms write is the same
# `node-resource-snapshot.v1`; only the door to the machine differs.
#
#   serveros-node-resource-snapshot.exe  GetSystemTimes / GlobalMemoryStatusEx /
#                                        GetDiskFreeSpaceExW → snapshot on stdout
#   core-lite-status.exe                 profile + snapshot + process token →
#                                        core-lite-status.v1 on stdout
#
# Both read only. Neither opens a file for writing, starts anything or reaches
# the network.
cmake_minimum_required(VERSION 3.20)
project(serveros_core_lite_windows LANGUAGES CXX VERSION 0.1.0)

if(NOT WIN32)
  message(FATAL_ERROR "this is the Windows build; on Linux build native/node-core instead")
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
# One static binary each, so the device needs no Visual C++ runtime installed.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

set(NODE_CORE "${CMAKE_CURRENT_SOURCE_DIR}/../..")

add_library(nodecore_portable STATIC
  ${NODE_CORE}/src/json.cpp
  ${NODE_CORE}/src/time.cpp
  ${NODE_CORE}/src/diff.cpp
  ${NODE_CORE}/src/sample.cpp
  ${NODE_CORE}/src/snapshot.cpp
  ${NODE_CORE}/src/windows_sample.cpp
  ${NODE_CORE}/src/windows_system.cpp
)
target_include_directories(nodecore_portable PUBLIC "${NODE_CORE}/include")

add_executable(serveros-node-resource-snapshot ${NODE_CORE}/src/main.cpp)
target_link_libraries(serveros-node-resource-snapshot PRIVATE nodecore_portable)

# The status builder's portable half: the profile reader (and the supervisor's
# manifest vocabulary it validates against), the projection, the status writer,
# the GPU gate (which answers UNSUPPORTED_PLATFORM here) and the identity
# reader, whose Win32 branch reads the process token.
add_library(core_lite_portable STATIC
  ${NODE_CORE}/supervisor/src/model.cpp
  ${NODE_CORE}/supervisor/src/manifest.cpp
  ${NODE_CORE}/supervisor/src/probe.cpp
  ${NODE_CORE}/supervisor/src/sha256.cpp
  ${NODE_CORE}/supervisor/src/admission.cpp
  ${NODE_CORE}/core-lite/src/profile.cpp
  ${NODE_CORE}/core-lite/src/status.cpp
  ${NODE_CORE}/core-lite/src/identity.cpp
  ${NODE_CORE}/core-lite/src/gpu_readings.cpp
  ${NODE_CORE}/core-lite/src/collector_readings.cpp
)
target_include_directories(core_lite_portable PUBLIC
  "${NODE_CORE}/core-lite/include"
  "${NODE_CORE}/supervisor/include"
  "${NODE_CORE}/supervisor/src")
target_link_libraries(core_lite_portable PUBLIC nodecore_portable)
if(WIN32)
  target_link_libraries(core_lite_portable PUBLIC advapi32 shell32 ole32)
endif()

add_executable(core-lite-status ${NODE_CORE}/core-lite/src/status_main.cpp)
target_link_libraries(core-lite-status PRIVATE core_lite_portable)

enable_testing()
add_executable(test_windows_sample ${NODE_CORE}/tests/test_windows_sample.cpp)
target_include_directories(test_windows_sample PRIVATE "${NODE_CORE}/tests")
target_link_libraries(test_windows_sample PRIVATE nodecore_portable)
add_test(NAME windows_sample COMMAND test_windows_sample)
