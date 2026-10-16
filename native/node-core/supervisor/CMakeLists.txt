# The Native Core supervisor.
#
# `nodecore_supervisor` is the pure-ish half: types, the strict manifest
# reader, the plan builder, the recovery engines, the provider abstraction and
# the receipt writer. `serveros-native-core` is the binary that wires them to
# a provider and a clock.
#
# The split matters for what can be tested: everything except the systemd
# adapter is deterministic and clock-injected, so its tests never start a
# process, never touch a unit and never read the wall clock. The adapter is
# tested against a fake `systemctl` on a temporary PATH — a real process
# boundary, but not this host's service manager.
add_library(nodecore_supervisor STATIC
  src/sha256.cpp
  src/model.cpp
  src/manifest.cpp
  src/recovery.cpp
  src/plan.cpp
  src/provider.cpp
  src/proc.cpp
  src/systemd_user.cpp
  src/admission.cpp
  src/probe.cpp
  src/lease.cpp
  src/receipt.cpp
  src/status.cpp
  src/serve.cpp
  src/status_socket.cpp
)
target_include_directories(nodecore_supervisor PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")
target_link_libraries(nodecore_supervisor PUBLIC nodecore)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(nodecore_supervisor PRIVATE -Wall -Wextra -Wpedantic)
endif()

# The binary. One pass and exit — see src/main.cpp: it does not daemonize, and
# the unit template starts it observe-only.
add_executable(serveros-native-core src/main.cpp)
target_link_libraries(serveros-native-core PRIVATE nodecore_supervisor)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(serveros-native-core PRIVATE -Wall -Wextra -Wpedantic)
endif()

add_subdirectory(tests)
