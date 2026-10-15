// Validate ONE Core-Lite profile and print the refusals. Observes nothing.
//
// This exists so a person can check a profile BEFORE installing anything; it is
// the step the README's install sequence puts first. Exit code is 0 only when
// the profile is one this version reads.
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>

#include "corelite/profile.hpp"

int main(int argc, char** argv) {
    if (argc != 2) {
        std::cerr << "usage: core-lite-profile-check <profile.json>\n";
        return 2;
    }
    std::ifstream file(argv[1]);
    if (!file) {
        std::cerr << "cannot open " << argv[1] << "\n";
        return 2;
    }
    const std::string text((std::istreambuf_iterator<char>(file)),
                           std::istreambuf_iterator<char>());
    const auto outcome = nodecore::corelite::parse_profile(text);
    if (outcome.ok) {
        std::cout << "ok: " << outcome.profile.node << " principal "
                  << outcome.profile.principal << " platform " << outcome.profile.platform
                  << " observations " << outcome.profile.observations.size() << " services "
                  << outcome.profile.services.size() << "\n";
        return 0;
    }
    for (const auto& refusal : outcome.refusals) {
        std::cout << (refusal.scope.empty() ? "(document)" : refusal.scope) << " ["
                  << refusal.code << "] " << refusal.reason << "\n";
    }
    return 1;
}
