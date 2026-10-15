# Core-Lite — the V1 per-user observation profile.
#
# Standalone on purpose, like the collector above it: it links the supervisor's
# `valid_unit_id` and the shared JSON reader rather than re-implementing either,
# because a second opinion about what a unit id is, or about what JSON is, is a
# second place the two can disagree.
#
# NOTE: `cmake` is NOT installed on the Tokyo host today, so this file has never
# been configured there. The tests were built with a direct `g++` invocation over
# the same sources (see README §3/§6) — the CMake path is the reproducible one
# for a host that has cmake, and it is not evidence of anything by itself.
cmake_minimum_required(VERSION 3.20)
project(serveros_core_lite LANGUAGES CXX VERSION 0.1.0)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# The pure reader. No provider, no clock, no socket: it reads a document and
# returns refusals.
add_library(core_lite_profile STATIC src/profile.cpp)
target_include_directories(core_lite_profile PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")
# `valid_unit_id` and the shared JSON reader live in the supervisor; this links
# them rather than copying them.
target_link_libraries(core_lite_profile PUBLIC nodecore_supervisor)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(core_lite_profile PRIVATE -Wall -Wextra -Wpedantic)
endif()

# A CLI whose ONLY job is to validate a profile before a human installs anything.
# It observes nothing and writes nothing.
add_executable(core-lite-profile-check src/profile_check.cpp)
target_link_libraries(core-lite-profile-check PRIVATE core_lite_profile)

enable_testing()
add_executable(test_core_lite_profile tests/test_profile.cpp)
target_link_libraries(test_core_lite_profile PRIVATE core_lite_profile)
add_test(NAME core_lite_profile COMMAND test_core_lite_profile)
# The SHIPPED profiles must be readable by the reader this repository ships.
add_test(NAME core_lite_shipped_profiles
         COMMAND test_core_lite_profile
                 "${CMAKE_CURRENT_SOURCE_DIR}/profiles/tokyo-node.profile.json"
                 "${CMAKE_CURRENT_SOURCE_DIR}/profiles/dell-fjd.profile.json")
