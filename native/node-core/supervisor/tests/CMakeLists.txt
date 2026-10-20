# One executable per unit, no external test framework — the same shape as the
# collector's suite next door (`native/node-core/tests/CMakeLists.txt`), so an
# assertion that fails names the unit rather than the suite.
#
# `sup_` prefixes every CTest name because CTest has one namespace for the
# whole project and the collector already owns the bare names.
#
# `SUPERVISOR_SOURCE_DIR` is defined so a test can read the committed example
# manifest. Documentation that is never parsed by a test is documentation that
# is free to stop matching the code.
set(supervisor_cases
  test_sup_sha256
  test_sup_manifest
  test_sup_recovery
  test_sup_plan
  test_sup_provider
  test_sup_receipt
  test_sup_status
  test_sup_serve
  test_sup_status_socket
  test_sup_admission
  test_sup_action_gate
  test_sup_action_journal
  test_sup_probe
  test_sup_lease
)

foreach(case IN ITEMS ${supervisor_cases})
  add_executable(${case} ${case}.cpp)
  target_link_libraries(${case} PRIVATE nodecore_supervisor)
  target_include_directories(${case} PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/../../tests"
    "${CMAKE_CURRENT_SOURCE_DIR}/.."
    # `src/` so a test can parse what the component writes with the component's
    # OWN reader. A receipt checked by substring is a receipt nobody checked.
    "${CMAKE_CURRENT_SOURCE_DIR}/../src")
  target_compile_definitions(${case} PRIVATE
    SUPERVISOR_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/..")
  if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(${case} PRIVATE -Wall -Wextra -Wpedantic)
  endif()
  add_test(NAME ${case} COMMAND ${case})
endforeach()
