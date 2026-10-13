# One test executable per unit, no external test framework.
#
# The cases that matter here are about values, not about plumbing, so the
# smallest thing that can fail loudly is enough. Each case is a separate CTest
# entry, which means a failure names the unit rather than the suite.
foreach(case IN ITEMS test_json test_diff test_parsers test_sample test_snapshot)
  add_executable(${case} ${case}.cpp)
  target_link_libraries(${case} PRIVATE nodecore)
  if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(${case} PRIVATE -Wall -Wextra -Wpedantic)
  endif()
  add_test(NAME ${case} COMMAND ${case})
endforeach()
