# The native node-core collector.
#
# Standalone on purpose: this directory is not part of the Python package, is not
# imported by `runtime/`, and must not enter the core's loader closure.
# `tests/test_core_freeze.py` asserts that closure, and a compiled binary is not
# a module — it is reached only by spawning it, the same way a Provider is.
#
# No external dependency beyond a C++20 standard library. There is no JSON
# library here on purpose: the document's byte-identity is a contract with the
# Python adapter, and a third-party serialiser's key order and float formatting
# are not ours to promise.
cmake_minimum_required(VERSION 3.20)
project(serveros_node_core_native LANGUAGES CXX VERSION 0.1.0)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

add_library(nodecore STATIC
  src/json.cpp
  src/time.cpp
  src/diff.cpp
  src/linux_readers.cpp
  src/sample.cpp
  src/snapshot.cpp
)
target_include_directories(nodecore PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(nodecore PRIVATE -Wall -Wextra -Wpedantic)
endif()

add_executable(serveros-node-resource-snapshot src/main.cpp)
target_link_libraries(serveros-node-resource-snapshot PRIVATE nodecore)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(serveros-node-resource-snapshot PRIVATE -Wall -Wextra -Wpedantic)
endif()

enable_testing()
add_subdirectory(tests)

# The Native Core supervisor: the resident decision engine that sits above the
# collector. It links `nodecore` for the JSON writer, and it is a SEPARATE
# target on purpose — the collector is a read-only one-shot and this is the
# thing that may ask systemd to act, so a reader can tell at the build graph
# which binary carries the reachability.
add_subdirectory(supervisor)

# Core-Lite: the per-user READ-ONLY observation profile. Added AFTER
# `supervisor` because it links `nodecore_supervisor` (for `valid_unit_id`) and
# the shared JSON reader — order is a dependency here, not a preference.
add_subdirectory(core-lite)

# The Windows native Core. Its own directory so that this one line is the ONLY
# file it shares with the rest of the tree — the Linux Core lane owns
# `supervisor/` and `core-lite/`, and a Windows adapter that edited either would
# be a lane collision rather than a feature.
add_subdirectory(windows-core)
