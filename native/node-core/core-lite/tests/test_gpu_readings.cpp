// `gpu.readonly`: the parser, the gates, and every way a GPU answer can be
// wrong.
//
// EVERY case here is a fixture and not one of them runs a process. The parser is
// a pure function of text and the projection is a pure function of (profile,
// account readback, one answered sample), so a wedged driver, a truncated
// answer, an `N/A` in a numeric field and a forged row are all statable without
// a GPU, a driver or a machine that has one.
//
// NO VALUE IN THIS FILE IS A MEASUREMENT OF ANY REAL CARD. The numbers are
// invented so that a reader cannot mistake a fixture for a reading — every
// fixture value is deliberately round or obviously synthetic. The one live run
// this branch performs is on a host with no GPU, and it asserts UNAVAILABLE.
//
// The rule this file exists to hold: an unreadable card is UNAVAILABLE with a
// reason, and nothing anywhere in this path may turn "we could not ask" into a
// plausible-looking 0%.
#include <iostream>
#include <string>
#include <vector>

#include "corelite/gpu_readings.hpp"

namespace {

int failures = 0;
int checks = 0;

void expect(bool condition, const std::string& what) {
    ++checks;
    if (!condition) {
        ++failures;
        std::cout << "  FAIL: " << what << "\n";
    }
}

using nodecore::corelite::GpuSample;
using nodecore::corelite::NvidiaAnswer;
using nodecore::corelite::NvidiaQuery;
using nodecore::corelite::OsIdentity;
using nodecore::corelite::Profile;
using nodecore::corelite::Reading;
using nodecore::corelite::ReadingState;

//: Answers from a table and COUNTS the calls. The count is how a test proves the
//: program was not run at all — which is the claim the authorisation gate makes,
//: and a claim no answer can demonstrate.
class FakeNvidia final : public NvidiaQuery {
public:
    explicit FakeNvidia(std::string text) : text_(std::move(text)) {}
    explicit FakeNvidia(std::string code, int exit_code)
        : ok_(false), text_(), code_(std::move(code)), exit_code_(exit_code) {}

    NvidiaAnswer sample() override {
        ++calls;
        NvidiaAnswer answer;
        answer.ok = ok_;
        answer.stdout_text = text_;
        answer.code = code_;
        answer.exit_code = exit_code_;
        return answer;
    }

    int calls = 0;

private:
    bool ok_ = true;
    std::string text_;
    std::string code_ = "EXITED";
    int exit_code_ = 0;
};

OsIdentity user(const std::string& name) {
    OsIdentity os;
    os.known = true;
    os.user = name;
    os.uid = 1000;
    os.home = "/home/" + name;
    return os;
}

Profile profile_granting(bool gpu_authorised, const std::string& platform = "linux") {
    Profile profile;
    profile.schema = nodecore::corelite::kProfileSchema;
    profile.node = "device:llm-lab-dell";
    profile.principal = "fjd";
    profile.platform = platform;
    profile.observe_only = true;
    profile.generation = 1;
    profile.observations.push_back({"gpu.readonly", gpu_authorised});
    return profile;
}

std::string value_text(const Reading& reading) {
    return reading.has_value ? reading.value.dump_compact() : std::string();
}

bool contains(const std::string& haystack, const std::string& needle) {
    return haystack.find(needle) != std::string::npos;
}

//: One synthetic card line. Deliberately not a real machine's numbers.
const char* const kOneCard = "0, Synthetic Model X, 42, 1024, 8192, 55\n";

// ---------------------------------------------------------------------------

void test_a_valid_answer_becomes_one_row_per_card() {
    std::cout << "a valid answer is projected, one row per card\n";
    const GpuSample sample = nodecore::corelite::parse_nvidia_smi_csv(kOneCard);
    expect(sample.present, "the sample is present");
    expect(sample.rows.size() == 1, "one row");
    if (sample.rows.empty()) {
        return;
    }
    expect(sample.rows[0].index == 0, "the index is kept");
    expect(sample.rows[0].label == "Synthetic Model X", "the model name is the label");
    expect(sample.rows[0].util_percent == 42.0, "utilisation is a percentage");
    // MiB in the text, bytes in the document: `nounits` removes the unit from
    // the TEXT and does not change what the number counts.
    expect(sample.rows[0].memory_used_bytes == 1024ull * 1024ull * 1024ull,
           "memory used is converted from MiB to bytes");
    expect(sample.rows[0].memory_total_bytes == 8192ull * 1024ull * 1024ull,
           "and so is the total");
    expect(sample.rows[0].temperature_c == 55.0, "temperature is carried");
}

void test_a_real_zero_survives_and_is_not_an_absence() {
    std::cout << "a card at 0% with 0 MiB used is a MEASUREMENT\n";
    // An idle card is the case a reader most needs told apart from a broken
    // one: 0% utilisation is a reading, and so is 0 bytes of memory used.
    const GpuSample sample =
        nodecore::corelite::parse_nvidia_smi_csv("0, Synthetic Model X, 0, 0, 8192, 55\n");
    expect(sample.present, "the sample is present");
    expect(sample.rows.size() == 1 && sample.rows[0].util_percent == 0.0,
           "0% utilisation is carried as 0, not as an absence");
    expect(sample.rows[0].memory_used_bytes == 0, "and 0 bytes used is carried as 0");
}

void test_more_than_one_card_is_bounded_and_ordered() {
    std::cout << "multiple cards, bounded and sorted by index\n";
    // Deliberately out of order in the text, so the sort is observable.
    const GpuSample sample = nodecore::corelite::parse_nvidia_smi_csv(
        "1, Synthetic Model Y, 10, 100, 8192, 50\n"
        "0, Synthetic Model X, 20, 200, 8192, 60\n");
    expect(sample.present, "present");
    expect(sample.rows.size() == 2, "two rows");
    if (sample.rows.size() == 2) {
        expect(sample.rows[0].index == 0 && sample.rows[1].index == 1,
               "sorted by index, so two runs are comparable position by position");
    }
}

void test_too_many_cards_is_refused() {
    std::cout << "more cards than the bound is refused, not truncated\n";
    std::string text;
    for (int index = 0; index < 20; ++index) {
        text += std::to_string(index) + ", Synthetic Model X, 1, 1, 8192, 50\n";
    }
    const GpuSample sample = nodecore::corelite::parse_nvidia_smi_csv(text);
    expect(!sample.present, "an answer larger than the bound is refused");
    expect(contains(sample.reason, "more cards"), "with a reason naming the bound");
}

void test_a_duplicate_index_is_refused() {
    std::cout << "two rows for one card is a contradiction\n";
    const GpuSample sample = nodecore::corelite::parse_nvidia_smi_csv(
        "0, Synthetic Model X, 1, 1, 8192, 50\n"
        "0, Synthetic Model X, 2, 2, 8192, 51\n");
    expect(!sample.present, "refused");
    expect(contains(sample.reason, "same card index"), "with a reason naming the duplicate");
}

void test_an_out_of_range_utilisation_is_refused() {
    std::cout << "a utilisation outside [0,100] is a malformed answer\n";
    for (const char* line : {"0, Synthetic Model X, 340, 1, 8192, 50\n",
                             "0, Synthetic Model X, -5, 1, 8192, 50\n",
                             "0, Synthetic Model X, 100.0001, 1, 8192, 50\n"}) {
        const GpuSample sample = nodecore::corelite::parse_nvidia_smi_csv(line);
        expect(!sample.present, std::string("refused: ") + line);
        expect(contains(sample.reason, "utilis"), "with a reason naming the utilisation");
    }
}

void test_memory_used_above_total_is_refused() {
    std::cout << "used above total does not add up\n";
    const GpuSample sample =
        nodecore::corelite::parse_nvidia_smi_csv("0, Synthetic Model X, 1, 9000, 8192, 50\n");
    expect(!sample.present, "refused");
    expect(contains(sample.reason, "greater than memory total"), "with a reason");
}

void test_a_negative_or_malformed_memory_figure_is_refused() {
    std::cout << "a memory figure that is not a non-negative integer is refused\n";
    for (const char* line : {"0, Synthetic Model X, 1, -1, 8192, 50\n",
                             "0, Synthetic Model X, 1, 1.5, 8192, 50\n",
                             "0, Synthetic Model X, 1, 12abc, 8192, 50\n",
                             "0, Synthetic Model X, 1, , 8192, 50\n"}) {
        const GpuSample sample = nodecore::corelite::parse_nvidia_smi_csv(line);
        expect(!sample.present, std::string("refused: ") + line);
    }
}

void test_a_memory_figure_that_overflows_is_refused() {
    std::cout << "MiB to bytes must not wrap\n";
    const GpuSample sample = nodecore::corelite::parse_nvidia_smi_csv(
        "0, Synthetic Model X, 1, 0, 18446744073709551615, 50\n");
    expect(!sample.present, "an overflowing total is refused");
    expect(contains(sample.reason, "overflow"), "with a reason naming the overflow");
}

void test_an_na_or_implausible_temperature_is_refused() {
    std::cout << "N/A and an impossible temperature are both refusals\n";
    for (const char* line : {"0, Synthetic Model X, 1, 1, 8192, N/A\n",
                             "0, Synthetic Model X, 1, 1, 8192, \n",
                             "0, Synthetic Model X, 1, 1, 8192, -273\n",
                             "0, Synthetic Model X, 1, 1, 8192, 9000\n"}) {
        const GpuSample sample = nodecore::corelite::parse_nvidia_smi_csv(line);
        expect(!sample.present, std::string("refused: ") + line);
        expect(contains(sample.reason, "temperature"), "with a reason naming the temperature");
    }
}

void test_an_na_in_a_numeric_field_is_never_zero() {
    std::cout << "N/A in the utilisation field is not 0%\n";
    // The failure this whole program is written against: a driver that cannot
    // answer must not be reported as an idle card.
    const GpuSample sample =
        nodecore::corelite::parse_nvidia_smi_csv("0, Synthetic Model X, N/A, 1, 8192, 50\n");
    expect(!sample.present, "refused rather than read as 0");
    expect(sample.rows.empty(), "and no row is emitted at all");
}

void test_a_line_without_six_fields_is_refused() {
    std::cout << "a truncated or extended line is refused whole\n";
    for (const char* line : {"0, Synthetic Model X, 1, 1, 8192\n",
                             "0, Synthetic Model X, 1, 1, 8192, 50, extra\n",
                             "0\n"}) {
        const GpuSample sample = nodecore::corelite::parse_nvidia_smi_csv(line);
        expect(!sample.present, std::string("refused: ") + line);
        expect(contains(sample.reason, "six fields"), "with a reason naming the shape");
    }
}

void test_an_empty_answer_is_refused_and_is_not_zero_cards() {
    std::cout << "nothing printed is not 'no GPUs'\n";
    for (const std::string& text : {std::string(), std::string("\n"), std::string("   \n\n")}) {
        const GpuSample sample = nodecore::corelite::parse_nvidia_smi_csv(text);
        expect(!sample.present, "refused");
        expect(contains(sample.reason, "no card rows"), "with a reason");
    }
}

void test_a_forged_name_cannot_carry_a_path_or_a_command() {
    std::cout << "the one free-text field cannot carry a path or a command\n";
    // The model name is the only field that could carry free text. A UUID, a
    // command line, a path with a shell metacharacter or a newline must all be
    // refused — and the refusal is of the WHOLE line, because a name that is not
    // a name is a sign the answer is not the answer we asked for.
    for (const char* name :
         {"GPU-12345678-1234-1234-1234-123456789012", "/etc/passwd",
          "X; rm -rf /", "X`id`", "X$(id)", "X|id", "X&id", "X'id'", "X\"id\"",
          "X\n0, Y, 1, 1, 8192, 50", "X<script>", "X\\id"}) {
        const std::string line = std::string("0, ") + name + ", 1, 1, 8192, 50\n";
        const GpuSample sample = nodecore::corelite::parse_nvidia_smi_csv(line);
        expect(!sample.present, std::string("refused: ") + name);
        expect(sample.rows.empty(), "and nothing is emitted from it");
    }
    // A long digit run inside an otherwise ordinary label is a SERIAL, not part
    // of a model name, and is refused for the same reason. The direction of this
    // guess is deliberate: a model name wrongly refused costs a label, an
    // identifier wrongly published is a hardware identifier leaked.
    const GpuSample serial = nodecore::corelite::parse_nvidia_smi_csv(
        "0, Synthetic Model X 0320918045678, 1, 1, 8192, 50\n");
    expect(!serial.present, "a serial-shaped digit run is refused");
    expect(serial.rows.empty(), "and nothing is emitted from it");
    // While an ordinary model name — short hex runs only — still works.
    const GpuSample ordinary = nodecore::corelite::parse_nvidia_smi_csv(
        "0, Synthetic RTX A6000 48GB, 1, 1, 8192, 50\n");
    expect(ordinary.present, "a normal marketing name is still a label");
}

// ---------------------------------------------------------------------------
// The gates: authorisation, identity, platform, and the run itself
// ---------------------------------------------------------------------------

void test_an_ungranted_observation_runs_nothing() {
    std::cout << "an ungranted gpu.readonly costs no process at all\n";
    FakeNvidia nvidia{std::string(kOneCard)};
    std::string error;
    const Reading reading = nodecore::corelite::gpu_reading(
        profile_granting(false), user("fjd"), nvidia, "2026-10-16T00:00:00+00:00");
    expect(nvidia.calls == 0, "the program was NOT run");
    expect(reading.state == ReadingState::Unavailable, "and the reading is UNAVAILABLE");
    expect(!reading.has_value, "with no value");
}

void test_an_unresolved_account_runs_nothing() {
    std::cout << "without a resolved account there is no Core to attribute a reading to\n";
    FakeNvidia nvidia{std::string(kOneCard)};
    OsIdentity unresolved;  // known == false
    const Reading reading = nodecore::corelite::gpu_reading(
        profile_granting(true), unresolved, nvidia, "2026-10-16T00:00:00+00:00");
    expect(reading.state == ReadingState::Unavailable, "UNAVAILABLE");
    expect(reading.reason == "IDENTITY_UNKNOWN", "with IDENTITY_UNKNOWN");
    expect(nvidia.calls == 0, "and the program was not run");
}

void test_a_non_linux_profile_does_not_use_this_source() {
    std::cout << "a Windows profile does not get this Linux sampler's answer\n";
    // `/usr/bin/nvidia-smi` is a Linux path and this is a Linux program.
    FakeNvidia nvidia{std::string(kOneCard)};
    const Reading reading = nodecore::corelite::gpu_reading(
        profile_granting(true, "windows"), user("fjd"), nvidia, "2026-10-16T00:00:00+00:00");
    expect(reading.state == ReadingState::Unavailable, "UNAVAILABLE");
    expect(reading.reason == "UNSUPPORTED_PLATFORM", "with UNSUPPORTED_PLATFORM");
    expect(nvidia.calls == 0, "and the Linux sampler was not run for a Windows Core");
}

void test_a_missing_binary_is_unavailable() {
    std::cout << "a program that cannot be started is UNAVAILABLE\n";
    // START_FAILED is what `run_argv` reports when execvp fails — which is what
    // a host without nvidia-smi produces.
    FakeNvidia nvidia("START_FAILED", 127);
    std::string error;
    const Reading reading = nodecore::corelite::gpu_reading(
        profile_granting(true), user("fjd"), nvidia, "2026-10-16T00:00:00+00:00");
    expect(nvidia.calls == 1, "the program was attempted");
    expect(reading.state == ReadingState::Unavailable, "UNAVAILABLE");
    expect(reading.reason == "NO_SOURCE", "with NO_SOURCE");
    expect(!reading.has_value, "and no value at all");
}

void test_a_timeout_or_nonzero_exit_is_unavailable() {
    std::cout << "a timeout or a failed exit is UNAVAILABLE, never an empty sample\n";
    for (const auto& failure : {std::pair<std::string, int>{"TIMED_OUT", -1},
                                std::pair<std::string, int>{"EXITED", 9},
                                std::pair<std::string, int>{"OUTPUT_LIMIT", 0}}) {
        FakeNvidia nvidia(failure.first, failure.second);
        const Reading reading = nodecore::corelite::gpu_reading(
            profile_granting(true), user("fjd"), nvidia, "2026-10-16T00:00:00+00:00");
        expect(reading.state == ReadingState::Unavailable,
               "UNAVAILABLE for " + failure.first);
        expect(reading.reason == "NO_SOURCE", "with NO_SOURCE");
        expect(!contains(value_text(reading), "0"), "and nothing rendered as zero");
    }
}

void test_a_successful_run_with_unusable_text_is_unavailable() {
    std::cout << "exit 0 with unreadable text is still UNAVAILABLE\n";
    for (const char* text : {"", "\n", "No devices were found\n",
                             "0, Synthetic Model X, N/A, N/A, N/A, N/A\n"}) {
        FakeNvidia nvidia{std::string(text)};
        const Reading reading = nodecore::corelite::gpu_reading(
            profile_granting(true), user("fjd"), nvidia, "2026-10-16T00:00:00+00:00");
        expect(reading.state == ReadingState::Unavailable, std::string("UNAVAILABLE for: ") + text);
        expect(!reading.has_value, "and no value");
    }
}

void test_the_projection_publishes_only_the_named_fields() {
    std::cout << "the value carries the six reviewed fields and nothing else\n";
    FakeNvidia nvidia{std::string(kOneCard)};
    const Reading reading = nodecore::corelite::gpu_reading(
        profile_granting(true), user("fjd"), nvidia, "2026-10-16T00:00:00+00:00");
    expect(reading.state == ReadingState::Present, "PRESENT");
    expect(reading.completeness == nodecore::corelite::kComplete, "and COMPLETE");
    const std::string text = value_text(reading);
    for (const char* wanted : {"\"gpus\"", "\"index\"", "\"label\"", "\"gpu_util_percent\"",
                               "\"memory_used_bytes\"", "\"memory_total_bytes\"",
                               "\"temperature_c\"", "\"sample_time\""}) {
        expect(contains(text, wanted), std::string("the value carries ") + wanted);
    }
    // What must NOT appear: no UUID, no bdf/bus id, no pid, no process list, no
    // driver version, no command line, no path.
    for (const char* forbidden : {"uuid", "UUID", "GPU-", "pid", "PID", "process",
                                  "pci", "bdf", "driver", "Driver", "cmdline", "/usr/bin",
                                  "/etc", "command"}) {
        expect(!contains(text, forbidden), std::string("the value carries no ") + forbidden);
    }
}

void test_the_sample_time_is_the_question_not_a_producer_stamp() {
    std::cout << "sample_time is when we asked\n";
    FakeNvidia nvidia{std::string(kOneCard)};
    const std::string when = "2026-10-16T01:02:03+00:00";
    const Reading reading =
        nodecore::corelite::gpu_reading(profile_granting(true), user("fjd"), nvidia, when);
    expect(contains(value_text(reading), "\"sample_time\":\"" + when + "\""),
           "the instant the program was asked is carried");
    // An empty time states none rather than inventing one.
    FakeNvidia second{std::string(kOneCard)};
    const Reading undated =
        nodecore::corelite::gpu_reading(profile_granting(true), user("fjd"), second, "");
    expect(!contains(value_text(undated), "sample_time"), "no time stated, none invented");
}

void test_the_parser_carries_no_instance_identifier() {
    std::cout << "a UUID-shaped name is refused, so no instance id can be published\n";
    const GpuSample sample = nodecore::corelite::parse_nvidia_smi_csv(
        "0, GPU-1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d, 1, 1, 8192, 50\n");
    expect(!sample.present, "refused");
    expect(sample.rows.empty(), "with no row emitted");
}

void test_a_blank_line_between_rows_is_skipped_not_counted() {
    std::cout << "a stray blank line does not become a card\n";
    const GpuSample sample = nodecore::corelite::parse_nvidia_smi_csv(
        "0, Synthetic Model X, 1, 1, 8192, 50\n"
        "\n"
        "1, Synthetic Model Y, 2, 2, 8192, 51\n");
    expect(sample.present, "present");
    expect(sample.rows.size() == 2, "exactly two cards, not three");
}

}  // namespace

int main() {
    test_a_valid_answer_becomes_one_row_per_card();
    test_a_real_zero_survives_and_is_not_an_absence();
    test_more_than_one_card_is_bounded_and_ordered();
    test_too_many_cards_is_refused();
    test_a_duplicate_index_is_refused();
    test_an_out_of_range_utilisation_is_refused();
    test_memory_used_above_total_is_refused();
    test_a_negative_or_malformed_memory_figure_is_refused();
    test_a_memory_figure_that_overflows_is_refused();
    test_an_na_or_implausible_temperature_is_refused();
    test_an_na_in_a_numeric_field_is_never_zero();
    test_a_line_without_six_fields_is_refused();
    test_an_empty_answer_is_refused_and_is_not_zero_cards();
    test_a_forged_name_cannot_carry_a_path_or_a_command();
    test_an_ungranted_observation_runs_nothing();
    test_an_unresolved_account_runs_nothing();
    test_a_non_linux_profile_does_not_use_this_source();
    test_a_missing_binary_is_unavailable();
    test_a_timeout_or_nonzero_exit_is_unavailable();
    test_a_successful_run_with_unusable_text_is_unavailable();
    test_the_projection_publishes_only_the_named_fields();
    test_the_sample_time_is_the_question_not_a_producer_stamp();
    test_the_parser_carries_no_instance_identifier();
    test_a_blank_line_between_rows_is_skipped_not_counted();

    std::cout << "\n" << checks << " checks, " << failures << " failures\n";
    return failures == 0 ? 0 : 1;
}
