// One owner map, two readers, the same verdict.
//
// WHY A SHARED CORPUS AND NOT A CROSS-LANGUAGE TEST
// -------------------------------------------------
// The property this file exists for is that the C++ Core and the Python daemon
// agree about which units exist and whose they are. The obvious way to check
// that is to run both against one input and compare — but a test that spawns the
// other language needs a compiler at test time, a PATH, a timeout and a parity
// harness that is itself new code nobody reviews twice.
//
// So the comparison is done against a RECORDED verdict instead. Each fixture in
// `contract/owner-map/` carries its expected answer in `expectations.json`; this
// suite asserts the C++ reader meets it, and `tests/test_native_owner_map_parity.py`
// asserts the Python reader meets the same file. Neither suite can drift without
// its own failure, and there is exactly one list of cases — a second copy would
// be the thing that lets the two sides quietly test different documents.
//
// The corpus also asserts it is COMPLETE: every fixture on disk appears in the
// index. A fixture nobody listed is a document both readers could disagree about
// with no test noticing, which is worse than not having the fixture.
#include <filesystem>
#include <fstream>
#include <set>
#include <sstream>
#include <string>

#include "check.hpp"
#include "json_detail.hpp"
#include "supervisor/facts.hpp"

using namespace nodecore::supervisor;

namespace {

std::string read_text(const std::string& path) {
    std::ifstream stream(path);
    if (!stream.good()) {
        return std::string();
    }
    std::ostringstream buffer;
    buffer << stream.rdbuf();
    return buffer.str();
}

std::string corpus_dir() {
    return std::string(SUPERVISOR_SOURCE_DIR) + "/contract/owner-map";
}

struct Case {
    std::string file;
    bool usable = false;
    std::string why;
};

std::vector<Case> cases_from_index(const detail::Json& root) {
    std::vector<Case> cases;
    const detail::Json* list = root.find("cases");
    if (list == nullptr || !list->is_array()) {
        return cases;
    }
    for (const detail::Json& entry : list->items) {
        Case one;
        const detail::Json* file = entry.find("file");
        const detail::Json* usable = entry.find("usable");
        const detail::Json* why = entry.find("why");
        if (file != nullptr && file->is_string()) one.file = file->text;
        if (usable != nullptr && usable->is_bool()) one.usable = usable->boolean;
        if (why != nullptr && why->is_string()) one.why = why->text;
        cases.push_back(std::move(one));
    }
    return cases;
}

void the_corpus_is_readable() {
    const std::string text = read_text(corpus_dir() + "/expectations.json");
    CHECK(!text.empty());
}

void every_case_reaches_its_recorded_verdict() {
    const std::string text = read_text(corpus_dir() + "/expectations.json");
    detail::Json root;
    std::string error;
    detail::Reader reader(text);
    const bool parsed = reader.parse(root, error);
    CHECK(parsed);
    if (!parsed) {
        return;
    }
    const std::vector<Case> cases = cases_from_index(root);
    // A corpus that parsed to zero cases would make every assertion below
    // vacuous, and a vacuous suite is indistinguishable from a passing one.
    CHECK(cases.size() >= 10);
    CHECK_EQ(root.find("schema") != nullptr && root.find("schema")->is_string(),
             true);

    for (const Case& one : cases) {
        const std::string document = read_text(corpus_dir() + "/" + one.file);
        // A missing fixture is a FAILURE, not a skip: the index names a document
        // that is supposed to exist in this repository.
        CHECK(!document.empty());
        const OwnerMap map = load_owner_map(document.c_str());
        const bool matches = map.usable == one.usable;
        if (!matches) {
            check::fail(__FILE__, __LINE__,
                        one.file + ": C++ verdict is " + (map.usable ? "usable" : "refused") +
                            " (" + map.code + "), index records " +
                            (one.usable ? "usable" : "refused") + " — " + one.why);
        } else {
            check::state().checks++;
        }
    }
}

void every_fixture_on_disk_is_in_the_index() {
    const std::string text = read_text(corpus_dir() + "/expectations.json");
    detail::Json root;
    std::string error;
    detail::Reader reader(text);
    if (!reader.parse(root, error)) {
        CHECK(false);
        return;
    }
    std::set<std::string> indexed;
    for (const Case& one : cases_from_index(root)) {
        indexed.insert(one.file);
    }

    std::error_code ec;
    std::set<std::string> on_disk;
    for (const auto& entry : std::filesystem::directory_iterator(corpus_dir(), ec)) {
        if (!entry.is_regular_file()) continue;
        const std::string name = entry.path().filename().string();
        if (name.size() > 5 && name.compare(name.size() - 5, 5, ".json") == 0 &&
            name != "expectations.json") {
            on_disk.insert(name);
        }
    }
    CHECK(!ec);
    CHECK(!on_disk.empty());

    for (const std::string& name : on_disk) {
        if (indexed.count(name) == 0) {
            check::fail(__FILE__, __LINE__,
                        name + " is on disk but not listed in expectations.json; an "
                               "unlisted fixture is a document neither reader is held to");
        } else {
            check::state().checks++;
        }
    }
    for (const std::string& name : indexed) {
        if (on_disk.count(name) == 0) {
            check::fail(__FILE__, __LINE__,
                        name + " is listed in expectations.json but not on disk");
        } else {
            check::state().checks++;
        }
    }
}

void the_boundary_cases_are_really_at_the_boundary() {
    // The two numeric fixtures exist to sit EXACTLY one unit over the bound both
    // readers enforce. A fixture that was over by ten would pass this suite
    // while leaving an off-by-one in either reader invisible, so the position is
    // asserted rather than assumed.
    const std::string too_many = read_text(corpus_dir() + "/too-many-units.json");
    detail::Json many;
    std::string error;
    detail::Reader many_reader(too_many);
    CHECK(many_reader.parse(many, error));
    const detail::Json* units = many.find("units");
    CHECK(units != nullptr && units->is_object());
    if (units != nullptr && units->is_object()) {
        CHECK_EQ(units->members.size(), std::size_t{65});
    }

    const std::string long_unit = read_text(corpus_dir() + "/long-unit.json");
    detail::Json single;
    detail::Reader single_reader(long_unit);
    CHECK(single_reader.parse(single, error));
    const detail::Json* one = single.find("units");
    CHECK(one != nullptr && one->is_object());
    if (one != nullptr && one->is_object() && one->members.size() == 1) {
        CHECK_EQ(one->members[0].first.size(), std::size_t{201});
    }
}

}  // namespace

int main() {
    the_corpus_is_readable();
    every_case_reaches_its_recorded_verdict();
    every_fixture_on_disk_is_in_the_index();
    the_boundary_cases_are_really_at_the_boundary();
    return check::report("test_sup_owner_map_parity");
}
