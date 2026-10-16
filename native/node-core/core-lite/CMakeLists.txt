# Core-Lite — the V1 per-user observation profile.
#
# THIS IS A SUBDIRECTORY, NOT A STANDALONE PROJECT. It used to declare its own
# `project()` and link `nodecore_supervisor`, which that standalone scope never
# defined — so the file could not configure on its own, and nothing reached it
# from the root either. The fix is the one the description asks for: the root
# `native/node-core/CMakeLists.txt` adds this directory, and `nodecore_supervisor`
# is a target by the time these lines run.
#
# It links the supervisor's `valid_unit_id` and the shared JSON reader rather
# than re-implementing either, because a second opinion about what a unit id is,
# or about what JSON is, is a second place the two can disagree.

add_library(core_lite_profile STATIC
  src/profile.cpp
  # The status builder. It writes with `nodecore::Json` — the collector's own
  # writer — and carries values as `Json`, never as text: there is no round trip
  # through a string for an untrusted document to ride in on.
  src/status.cpp
  # The runtime identity readback, apart from the parser because a parser that
  # called `getuid()` would only be testable on the host it was written on.
  src/identity.cpp
  # The `gpu.readonly` observation. The program it runs is an absolute constant
  # with a fixed field list; the runner is the supervisor's bounded one.
  src/gpu_readings.cpp
  # The `services` observation, projected from the supervisor's read-only unit
  # observations. It compiles against the NARROW `ServiceQuery` — one method —
  # rather than the supervisor's `Provider`, which also has start/restart/stop: a
  # type that cannot express those cannot call them.
  src/service_readings.cpp
  # The Linux adapter: a `node-resource-snapshot.v1` PROJECTED onto the reviewed
  # fields. It reads the collector's document rather than `/proc`, so the numbers
  # a Fleet reader sees here are the collector's own — and it names every field it
  # emits, so a key nobody named cannot reach the status.
  src/collector_readings.cpp
)
target_include_directories(core_lite_profile PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")

# `supervisor/src` is on the path because the ONE strict JSON reader in this
# repository lives there, and this component reads JSON. The alternative — a
# second parser here — is the thing this repository refuses, and the alternative
# the supervisor's own header names is that a private header reachable from
# outside is only private by convention.
#
# PRIVATE, so this does not become part of core-lite's own interface for anyone
# downstream. The structural follow-up, which belongs to the supervisor's review
# and not to this one: publish the reader as `supervisor/include/supervisor/
# json_reader.hpp` and include it by that name here. Until then this line is the
# smallest thing that lets the documented CMake path build at all — it did not
# before, which is what the missing include was hiding.
target_include_directories(core_lite_profile PRIVATE
  "${CMAKE_CURRENT_SOURCE_DIR}/../supervisor/src")
target_link_libraries(core_lite_profile PUBLIC nodecore_supervisor)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(core_lite_profile PRIVATE -Wall -Wextra -Wpedantic)
endif()

# A CLI whose ONLY job is to validate a profile before a human installs anything.
# It observes nothing and writes nothing.
add_executable(core-lite-profile-check src/profile_check.cpp)
target_link_libraries(core-lite-profile-check PRIVATE core_lite_profile)

# The READ-ONLY status front door: read a profile and a snapshot, ask the OS who
# this is, print one `serveros/core-lite-status.v1` document. It has no flag that
# could make it act, and adding one would be a different review.
add_executable(core-lite-status src/status_main.cpp)
target_link_libraries(core-lite-status PRIVATE core_lite_profile)
# The supervisor's `src/` again, for `proc.hpp` — the bounded argv runner that
# runs the fixed GPU program. PRIVATE includes do not propagate from the library
# to this executable, so the path is repeated here rather than inherited. This is
# the third use of the same private directory (the JSON reader, the unit adapter's
# parser, now the runner), which is why the README names publishing those headers
# as the structural follow-up rather than leaving it as three local choices.
target_include_directories(core-lite-status PRIVATE
  "${CMAKE_CURRENT_SOURCE_DIR}/../supervisor/src")

# `enable_testing()` and `add_test` are the PARENT's: `native/node-core` already
# calls `enable_testing()` before its subdirectories, so `ctest` from the root
# build directory runs these beside the collector's and the supervisor's.
add_executable(test_core_lite_profile tests/test_profile.cpp)
target_link_libraries(test_core_lite_profile PRIVATE core_lite_profile)
add_test(NAME core_lite_profile COMMAND test_core_lite_profile)
# The SHIPPED profiles must be readable by the reader this repository ships.
add_test(NAME core_lite_shipped_profiles
         COMMAND test_core_lite_profile
                 "${CMAKE_CURRENT_SOURCE_DIR}/profiles/tokyo-node.profile.json"
                 "${CMAKE_CURRENT_SOURCE_DIR}/profiles/dell-fjd.profile.json")

# The status contract and the runtime identity readback.
add_executable(test_core_lite_status tests/test_status.cpp)
target_link_libraries(test_core_lite_status PRIVATE core_lite_profile)
# The status tests re-read the produced document with the component's own reader,
# so an assertion is about the DOCUMENT and not about the writer's spacing.
target_include_directories(test_core_lite_status PRIVATE
  "${CMAKE_CURRENT_SOURCE_DIR}/../supervisor/src")
add_test(NAME core_lite_status COMMAND test_core_lite_status)

# The Linux adapter, against the snapshot the repository already ships. The test
# finds the fixture relative to its working directory and FAILS if it cannot, so
# a wrong `WORKING_DIRECTORY` cannot pass as a green run.
# The status document's schema, asserted against the document this producer
# emits: the schema is READ, not duplicated, so the two cannot drift apart.
add_executable(test_core_lite_schema tests/test_status_schema.cpp)
target_link_libraries(test_core_lite_schema PRIVATE core_lite_profile)
target_include_directories(test_core_lite_schema PRIVATE
  "${CMAKE_CURRENT_SOURCE_DIR}/../supervisor/src")
add_test(NAME core_lite_schema COMMAND test_core_lite_schema)
set_tests_properties(core_lite_schema PROPERTIES
  WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}")

add_executable(test_core_lite_gpu tests/test_gpu_readings.cpp)
target_link_libraries(test_core_lite_gpu PRIVATE core_lite_profile)
add_test(NAME core_lite_gpu COMMAND test_core_lite_gpu)

add_executable(test_core_lite_services tests/test_service_readings.cpp)
target_link_libraries(test_core_lite_services PRIVATE core_lite_profile)
# The test re-reads the observation text with the component's own parser, and
# asserts the tri-state that decides absent-vs-stopped.
target_include_directories(test_core_lite_services PRIVATE
  "${CMAKE_CURRENT_SOURCE_DIR}/../supervisor/src")
add_test(NAME core_lite_services COMMAND test_core_lite_services)

add_executable(test_core_lite_readings tests/test_collector_readings.cpp)
target_link_libraries(test_core_lite_readings PRIVATE core_lite_profile)
# The test re-reads the status document with the component's own reader rather
# than matching substrings — a document checked by substring is a document nobody
# checked. Same reason, and same path, as the supervisor's own suite.
target_include_directories(test_core_lite_readings PRIVATE
  "${CMAKE_CURRENT_SOURCE_DIR}/../supervisor/src")
add_test(NAME core_lite_readings COMMAND test_core_lite_readings)
set_tests_properties(core_lite_readings PROPERTIES
  WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}")
