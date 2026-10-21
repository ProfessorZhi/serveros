// The `gpu.readonly` observation: what this machine's NVIDIA cards are doing,
// read and never operated.
//
// WHAT IT READS, EXACTLY
// ----------------------
// One program, named by an ABSOLUTE constant path, with a FIXED argument list
// built here. There is no flag, no parameter and no profile field that can
// change either: a caller cannot point this at another program, and cannot add
// an argument to this one. The supervisor's `run_argv` hands the list straight
// to `execvp` with no shell in between, so nothing in it can become a second
// command.
//
// It asks for six fields and nothing else:
//
//   index, name, utilization.gpu, memory.used, memory.total, temperature.gpu
//
// Card-level counters only. It does not enumerate processes, so it never reads
// another account's PIDs, command lines or UIDs — those are not in the query, so
// they cannot be in the answer.
//
// WHAT IT REFUSES
// ---------------
//   * a sample that is not authorised in the profile. The process is not run at
//     all — an unauthorised observation costs nothing, rather than being read and
//     then discarded.
//   * a card whose utilisation is outside [0, 100], whose memory used exceeds its
//     total, whose temperature is outside a plausible band, or whose fields are
//     `N/A` or otherwise unparseable. The WHOLE sample becomes UNAVAILABLE: one
//     bad card makes the reading untrustworthy rather than being silently
//     dropped, because a reader comparing two cards would not know one was gone.
//   * two rows with the same index. Two answers for one card is a contradiction,
//     and picking one is picking silently.
//   * more cards than a desktop or a small server can hold. A document that grew
//     to a thousand rows is not this shape.
//   * the free text nvidia-smi might print. The model name is carried ONLY when
//     it matches a bounded, safe charset; a UUID, a serial, a process command
//     line or kernel noise cannot pass it, and a name that fails is a sign the
//     output is not the output this program asked for.
//
// WHAT IT IS NOT
// --------------
// It does not touch models, inference, or any process. **A GPU utilisation figure
// is not a health signal for the service running on it**: a model server can be
// wedged while the card sits at 0%, and busy while the server is answering
// nothing. Service health comes from a trusted probe that has the right to ask,
// not from this number.
//
// And on Windows this is not the source: `/usr/bin/nvidia-smi` is a Linux path
// and this is a Linux program. A Windows Core's GPU answer must come from a
// Windows adapter, and nothing here may be presented as one.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "corelite/identity.hpp"
#include "corelite/profile.hpp"
#include "corelite/status.hpp"

namespace nodecore::corelite {

//: The program, ABSOLUTE and fixed. Deliberately not a parameter.
inline constexpr const char* kNvidiaSmiPath = "/usr/bin/nvidia-smi";

//: The fields asked for, in the order the parser expects them. Exactly the six
//: named in the header comment; no `--query-compute-apps`, so no process data.
inline constexpr const char* kNvidiaSmiQueryFields[] = {
    "index",      "name",         "utilization.gpu",
    "memory.used", "memory.total", "temperature.gpu",
};

//: How long nvidia-smi may run before its whole process group is killed. A
//: driver can wedge, and a wedged driver is exactly when a monitoring program
//: must not sit waiting.
inline constexpr int kNvidiaSmiTimeoutSeconds = 5;

//: The output this program will read. Bounded well below `run_argv`'s own
//: default: six short fields per card, so anything larger is not this shape.
inline constexpr std::size_t kNvidiaSmiMaxOutputBytes = 16384;

//: How many cards a sample may carry. Sixteen is beyond any machine this
//: document describes; more than that is a truncated or forged answer.
inline constexpr std::size_t kMaxGpuRows = 16;

//: The band a GPU temperature can plausibly be in, in Celsius. Outside it, the
//: number is not a temperature: a driver that reports -273 or 9000 is reporting
//: an error into a field, and publishing it would put a wrong number in front of
//: somebody who would act on it.
inline constexpr double kMinPlausibleTemperatureC = -40.0;
inline constexpr double kMaxPlausibleTemperatureC = 150.0;

//: What one run of the program answered. The codes are `run_argv`'s own.
struct NvidiaAnswer {
    bool ok = false;
    std::string stdout_text;
    // EXITED / TIMED_OUT / OUTPUT_LIMIT / START_FAILED — a caller switches on
    // this rather than on a sentence.
    std::string code;
    int exit_code = -1;
};

//: The only thing a GPU reading needs: run the fixed program once and hand back
//: what it printed. One method, no arguments — a type that cannot express "run
//: something else" cannot be asked to.
class NvidiaQuery {
public:
    virtual ~NvidiaQuery() = default;
    virtual NvidiaAnswer sample() = 0;
};

//: One card. Card-level counters only.
struct GpuRow {
    long index = -1;
    //: The model name, carried only when it matches the safe charset. Never a
    //: UUID, never a serial, never a free-text line.
    std::string label;
    double util_percent = 0.0;
    std::uint64_t memory_used_bytes = 0;
    std::uint64_t memory_total_bytes = 0;
    double temperature_c = 0.0;
};

struct GpuSample {
    bool present = false;
    //: One of `kReasons` when `!present`.
    std::string reason;
    std::vector<GpuRow> rows;
};

//: The parser, pure and fixture-testable. Takes the program's stdout and the
//: instant the sample was taken; returns rows or a closed reason.
//:
//: `memory.used` and `memory.total` arrive in MiB — `--format=…,nounits` removes
//: the unit from the text and does NOT change what the number counts — so they
//: are converted here, once, before anything else touches them.
GpuSample parse_nvidia_smi_csv(const std::string& text);

//: The `gpu.readonly` reading. `sample_time` is the instant the program was run,
//: read from the caller's clock: nvidia-smi stamps nothing, so unlike the
//: snapshot's `observed_at` this is NOT a producer's stamp, and the document's
//: `freshness` remains `NOT_ASSERTED`.
Reading gpu_reading(const Profile& profile, const OsIdentity& os, NvidiaQuery& query,
                    const std::string& sample_time);

}  // namespace nodecore::corelite
