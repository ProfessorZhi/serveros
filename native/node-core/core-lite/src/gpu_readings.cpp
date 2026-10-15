// The `gpu.readonly` observation. See `gpu_readings.hpp` for the rules.
#include "corelite/gpu_readings.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstdint>
#include <string>
#include <vector>

#include "nodecore/json.hpp"

namespace nodecore::corelite {

namespace {

using Json = nodecore::Json;

//: `nvidia-smi --format=csv,noheader,nounits` counts memory in MiB. The unit is
//: removed from the TEXT by `nounits`, not from the number, so it is stated here
//: the way `/proc/diskstats`' 512 is stated in the snapshot: a reader who assumes
//: bytes is wrong by a factor of a million, and the constant is the reason they
//: are not.
constexpr std::uint64_t kMebibyte = 1024ull * 1024ull;

//: A run of hex characters long enough to be an IDENTIFIER rather than a model
//: name: a UUID segment is eight, a serial is a long digit run.
//:
//: Character class alone cannot tell "GPU-1a2b3c4d-…" from "NVIDIA RTX A6000" —
//: both are letters, digits and hyphens. What separates them is that an
//: identifier contains a long unbroken hex run and a marketing name does not:
//: the longest run in "A6000" is five, in "RTX 8000" is four.
//:
//: Refusing the RUN rather than the whole shape is what keeps ordinary model
//: names working while no instance id can travel in this field. The direction of
//: the guess is deliberate: a model name wrongly refused costs a label, an
//: identifier wrongly published is a hardware identifier leaked.
bool looks_like_an_identifier(const std::string& text) {
    std::size_t run = 0;
    for (const char c : text) {
        const bool hex = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
        run = hex ? run + 1 : 0;
        if (run >= 8) {
            return true;
        }
    }
    return false;
}

//: The model name is the one free-text field on the line, so it is allowed
//: through only when it looks like a model name.
//:
//: No `/`: a path is not a model name, and there is no GPU whose marketing name
//: contains a directory separator. No `:`, no quote, no backslash, no shell
//: metacharacter — a forged answer cannot dress a command line as a label.
//: And nothing identifier-shaped, so a UUID or a serial cannot travel here.
bool safe_label(const std::string& text) {
    if (text.empty() || text.size() > 64) {
        return false;
    }
    for (const char c : text) {
        const bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                        (c >= '0' && c <= '9') || c == ' ' || c == '.' || c == '-' || c == '_' ||
                        c == '+' || c == '(' || c == ')';
        if (!ok) {
            return false;
        }
    }
    return !looks_like_an_identifier(text);
}

std::string trim(const std::string& raw) {
    std::size_t begin = 0;
    std::size_t end = raw.size();
    while (begin < end && (raw[begin] == ' ' || raw[begin] == '\t' || raw[begin] == '\r')) {
        ++begin;
    }
    while (end > begin && (raw[end - 1] == ' ' || raw[end - 1] == '\t' || raw[end - 1] == '\r')) {
        --end;
    }
    return raw.substr(begin, end - begin);
}

std::vector<std::string> split(const std::string& text, char delimiter) {
    std::vector<std::string> parts;
    std::size_t begin = 0;
    while (begin <= text.size()) {
        const std::size_t at = text.find(delimiter, begin);
        const std::size_t end = at == std::string::npos ? text.size() : at;
        parts.push_back(text.substr(begin, end - begin));
        if (at == std::string::npos) {
            break;
        }
        begin = at + 1;
    }
    return parts;
}

//: A non-negative integer. `to_u64` elsewhere in this tree is strict about a
//: trailing character; the same rule applies here, because "12abc" read as 12 is
//: a counter from a surprising place.
bool parse_u64(const std::string& token, std::uint64_t& out) {
    if (token.empty()) {
        return false;
    }
    std::uint64_t value = 0;
    for (const char c : token) {
        if (c < '0' || c > '9') {
            return false;
        }
        const std::uint64_t digit = static_cast<std::uint64_t>(c - '0');
        if (value > (UINT64_MAX - digit) / 10) {
            return false;
        }
        value = value * 10 + digit;
    }
    out = value;
    return true;
}

//: A decimal, no sign and no exponent, as this program prints them.
bool parse_double(const std::string& token, double& out) {
    if (token.empty()) {
        return false;
    }
    bool saw_digit = false;
    bool saw_dot = false;
    for (const char c : token) {
        if (c >= '0' && c <= '9') {
            saw_digit = true;
        } else if (c == '.' && !saw_dot) {
            saw_dot = true;
        } else {
            return false;
        }
    }
    if (!saw_digit) {
        return false;
    }
    out = std::strtod(token.c_str(), nullptr);
    return std::isfinite(out);
}

GpuSample refuse(const std::string& reason) {
    GpuSample sample;
    sample.present = false;
    sample.reason = reason;
    return sample;
}

}  // namespace

GpuSample parse_nvidia_smi_csv(const std::string& text) {
    GpuSample sample;
    std::vector<long> seen_indices;

    std::size_t begin = 0;
    while (begin <= text.size()) {
        const std::size_t newline = text.find('\n', begin);
        const std::size_t end = newline == std::string::npos ? text.size() : newline;
        const std::string raw_line = text.substr(begin, end - begin);
        if (newline == std::string::npos) {
            begin = text.size() + 1;
        } else {
            begin = newline + 1;
        }
        // A trailing newline, or a blank line between answers, is not a card.
        const std::string line = trim(raw_line);
        if (line.empty()) {
            continue;
        }

        if (sample.rows.size() >= kMaxGpuRows) {
            return refuse("the answer carries more cards than this document allows");
        }

        const std::vector<std::string> fields = split(line, ',');
        if (fields.size() != 6) {
            // Not six fields is not a line this program prints. Refused whole
            // rather than parsed as far as it goes.
            return refuse("a line does not carry the six fields this program asks for");
        }

        GpuRow row;
        std::uint64_t index = 0;
        if (!parse_u64(trim(fields[0]), index) || index > 4096) {
            return refuse("a card index is not a small non-negative integer");
        }
        row.index = static_cast<long>(index);

        const std::string name = trim(fields[1]);
        if (!safe_label(name)) {
            // EMPTY or unsafe. Either way this is not the model name this
            // program prints, and the field is the one place free text could
            // ride in — so a name that fails is a signal about the whole line.
            return refuse("a card name is empty or is not a bounded model name");
        }
        row.label = name;

        double util = 0.0;
        if (!parse_double(trim(fields[2]), util) || util < 0.0 || util > 100.0) {
            // "N/A" lands here, as does 340. A utilisation is a share of time,
            // so it lives in [0, 100]; a value outside that is a malformed
            // answer, not a busy card.
            return refuse("a utilisation is not a percentage in [0,100]");
        }
        row.util_percent = util;

        std::uint64_t used_mib = 0;
        std::uint64_t total_mib = 0;
        if (!parse_u64(trim(fields[3]), used_mib)) {
            return refuse("memory used is not a non-negative integer");
        }
        if (!parse_u64(trim(fields[4]), total_mib)) {
            return refuse("memory total is not a non-negative integer");
        }
        if (used_mib > total_mib) {
            // More used than the card has is not a full card, it is an answer
            // that does not add up. A percentage derived from it would be
            // meaningless and a reader would act on it.
            return refuse("memory used is greater than memory total");
        }
        if (used_mib > UINT64_MAX / kMebibyte || total_mib > UINT64_MAX / kMebibyte) {
            return refuse("a memory figure in MiB overflows a byte count");
        }
        row.memory_used_bytes = used_mib * kMebibyte;
        row.memory_total_bytes = total_mib * kMebibyte;

        double temperature = 0.0;
        if (!parse_double(trim(fields[5]), temperature) ||
            temperature < kMinPlausibleTemperatureC || temperature > kMaxPlausibleTemperatureC) {
            // "N/A" lands here too. A temperature outside the band is a driver
            // reporting an error into a field, and a wrong number in front of
            // somebody who would act on it is worse than no number.
            return refuse("a temperature is not a plausible Celsius reading");
        }
        row.temperature_c = temperature;

        if (std::find(seen_indices.begin(), seen_indices.end(), row.index) != seen_indices.end()) {
            return refuse("two lines report the same card index");
        }
        seen_indices.push_back(row.index);
        sample.rows.push_back(row);
    }

    if (sample.rows.empty()) {
        // No rows at all. NOT "no GPUs so everything is zero": a program that
        // printed nothing usable told us nothing, and this reading says so.
        return refuse("the answer carries no card rows");
    }
    // Sorted by index, so two runs of the same machine produce the same order
    // and a reader can compare them position by position.
    std::sort(sample.rows.begin(), sample.rows.end(),
              [](const GpuRow& left, const GpuRow& right) { return left.index < right.index; });
    sample.present = true;
    return sample;
}

Reading gpu_reading(const Profile& profile, const OsIdentity& os, NvidiaQuery& query,
                    const std::string& sample_time) {
    Reading reading;
    reading.name = "gpu.readonly";
    reading.source = "nvidia-smi";
    reading.state = ReadingState::Unavailable;

    // AUTHORISED FIRST, before anything is run. An observation the owner did not
    // grant costs no process at all — it is not read and then discarded, because
    // reading it would itself be the thing the grant exists to prevent.
    bool authorised = false;
    for (const Observation& observation : profile.observations) {
        if (observation.name == "gpu.readonly" && observation.authorized) {
            authorised = true;
        }
    }
    if (!authorised) {
        reading.reason = "NO_SOURCE";
        return reading;
    }

    // AND THIS ACCOUNT'S TO ASK. Without a resolved account there is no Core
    // here, and a reading attributable to nobody is not a reading.
    if (!os.known) {
        reading.reason = "IDENTITY_UNKNOWN";
        return reading;
    }

    // The platform gate before the process gate: `/usr/bin/nvidia-smi` is a
    // Linux path and this is a Linux program. A Windows Core's GPU answer comes
    // from a Windows adapter, and nothing here may be presented as one.
    if (profile.platform != "linux") {
        reading.reason = "UNSUPPORTED_PLATFORM";
        return reading;
    }

    const NvidiaAnswer answer = query.sample();
    if (!answer.ok) {
        // The program could not be run or did not finish. The closed vocabulary
        // has one word for "the source was asked and gave nothing usable", and
        // the reason it failed stays in the log rather than in the document —
        // `run_argv`'s codes and any stderr are the operator's business, not a
        // Fleet reader's.
        reading.reason = "NO_SOURCE";
        return reading;
    }

    const GpuSample sample = parse_nvidia_smi_csv(answer.stdout_text);
    if (!sample.present) {
        reading.reason = sample.reason.empty() ? "NO_SOURCE" : "NO_SOURCE";
        return reading;
    }

    Json rows = Json::array();
    for (const GpuRow& row : sample.rows) {
        Json entry = Json::object();
        // `index` and `label` identify the card to a human without naming an
        // instance: no UUID, no serial, no bus id.
        entry.set("index", Json::integer(row.index));
        entry.set("label", Json::string(row.label));
        entry.set("gpu_util_percent", Json::number(row.util_percent, 2));
        entry.set("memory_used_bytes", Json::uinteger(row.memory_used_bytes));
        entry.set("memory_total_bytes", Json::uinteger(row.memory_total_bytes));
        entry.set("temperature_c", Json::number(row.temperature_c, 2));
        rows.push(entry);
    }

    Json value = Json::object();
    value.set("gpus", rows);
    // The instant this program was asked — NOT a producer's stamp. nvidia-smi
    // stamps nothing, so unlike the snapshot's `observed_at` this is the time of
    // the question, and the document's `freshness` still says NOT_ASSERTED.
    if (!sample_time.empty()) {
        value.set("sample_time", Json::string(sample_time));
    }
    reading.value = value;
    reading.has_value = true;
    reading.state = ReadingState::Present;
    // Every field of every row survived validation, or this reading would not be
    // PRESENT at all — one bad card refuses the sample rather than being dropped.
    reading.completeness = kComplete;
    return reading;
}

}  // namespace nodecore::corelite
