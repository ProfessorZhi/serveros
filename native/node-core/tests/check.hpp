// The smallest assertion harness that can fail loudly.
//
// A test framework would be a dependency, and this collector is meant to build
// on a host that has a C++20 compiler and nothing else. Every check increments a
// counter, so a test file that silently ran zero assertions is visible in the
// output rather than passing by emptiness.
#pragma once

#include <cstdio>
#include <string>

namespace check {

struct State {
    int checks = 0;
    int failures = 0;
};

inline State& state() {
    static State instance;
    return instance;
}

inline void fail(const char* file, int line, const std::string& what) {
    std::fprintf(stderr, "FAIL %s:%d: %s\n", file, line, what.c_str());
    state().failures++;
}

inline void contains_fail(const char* file, int line, const std::string& haystack,
                          const std::string& needle) {
    std::fprintf(stderr, "FAIL %s:%d: expected to find \"%s\" in:\n%s\n", file, line, needle.c_str(),
                 haystack.c_str());
    state().failures++;
}

inline int report(const char* suite) {
    std::fprintf(stderr, "%s: %d checks, %d failures\n", suite, state().checks, state().failures);
    if (state().checks == 0) {
        std::fprintf(stderr, "%s: NO CHECKS RAN\n", suite);
        return 1;
    }
    return state().failures == 0 ? 0 : 1;
}

}  // namespace check

#define CHECK(condition)                                       \
    do {                                                       \
        check::state().checks++;                               \
        if (!(condition)) {                                    \
            check::fail(__FILE__, __LINE__, #condition);       \
        }                                                      \
    } while (0)

#define CHECK_EQ(left, right)                                                      \
    do {                                                                           \
        check::state().checks++;                                                   \
        if (!((left) == (right))) {                                                \
            check::fail(__FILE__, __LINE__, std::string("expected ") + #left +      \
                                                " == " + #right);                  \
        }                                                                          \
    } while (0)

#define CHECK_CONTAINS(haystack, needle)                                           \
    do {                                                                           \
        check::state().checks++;                                                   \
        const std::string haystack_value = (haystack);                             \
        if (haystack_value.find(needle) == std::string::npos) {                    \
            check::contains_fail(__FILE__, __LINE__, haystack_value, needle);      \
        }                                                                          \
    } while (0)
