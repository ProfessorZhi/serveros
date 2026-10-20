// Swap and PSI: the parsers, the sampling paths, and the two-sample rates.
//
// The cases that matter here are the ones a healthy host will not produce on
// demand: a counter that went backwards, a value that overflows, a percentage
// that cannot exist, a file that is there but not readable, and a kernel that
// does not provide `full`. Every one of them must produce an UNAVAILABLE with a
// reason rather than a number, and every one of them is a fixture.
//
// The distinction this file exists to hold, in one sentence: a measured zero and
// an unmeasurable fact are different, and a document that renders the second as
// the first is worse than one that says nothing.
#include <cerrno>
#include <chrono>
#include <string>

#include "check.hpp"
#include "nodecore/file_source.hpp"
#include "nodecore/sample.hpp"
#include "nodecore/snapshot.hpp"

using namespace nodecore;
using namespace std::chrono;

namespace {

FakeClock fake_clock() {
    FakeClock clock;
    clock.set_utc(system_clock::time_point(seconds(1770000000)));
    return clock;
}

// This host's own /proc/pressure/cpu, copied verbatim.
const char* const kRealCpuPressure =
    "some avg10=0.69 avg60=0.33 avg300=0.52 total=24839609798\n"
    "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n";

// A kernel that does not provide cpu.full — the shape the kernel uses when it
// reports only the `some` line.
const char* const kCpuPressureWithoutFull = "some avg10=1.25 avg60=0.50 avg300=0.10 total=99\n";

SwapFacts swap_of(const std::string& meminfo, const std::string& vmstat) {
    SwapFacts facts = parse_meminfo_swap(meminfo);
    const SwapFacts pages = parse_vmstat_page_counters(vmstat);
    facts.pages_known = pages.pages_known;
    facts.pages_reason = pages.pages_reason;
    facts.pages_in = pages.pages_in;
    facts.pages_out = pages.pages_out;
    return facts;
}

Sample sample_at(double monotonic, std::uint64_t pages_in, std::uint64_t pages_out) {
    Sample sample;
    sample.utc = system_clock::time_point(seconds(1770000000));
    sample.monotonic_seconds = monotonic;
    sample.boot_id = "boot-aaa";
    sample.cpu.present = true;
    sample.cpu.user = 100;
    sample.cpu.system = 50;
    sample.cpu.idle = 1000;
    sample.memory.present = true;
    sample.memory.total_bytes = 1000;
    sample.memory.available_bytes = 400;
    sample.memory.free_bytes = 200;
    sample.swap.totals_known = true;
    sample.swap.total_bytes = 4000;
    sample.swap.free_bytes = 1000;
    sample.swap.pages_known = true;
    sample.swap.pages_in = pages_in;
    sample.swap.pages_out = pages_out;
    PsiFacts cpu;
    cpu.resource = "cpu";
    cpu.present = true;
    cpu.some.present = true;
    cpu.some.avg10 = 1.0;
    cpu.some.avg60 = 2.0;
    cpu.some.avg300 = 3.0;
    cpu.some.total_microseconds = 500;
    cpu.full.present = true;
    cpu.full.avg10 = 0.5;
    cpu.full.total_microseconds = 100;
    sample.psi.push_back(cpu);
    return sample;
}

SnapshotOptions fixture_options() {
    SnapshotOptions options;
    options.live = false;
    return options;
}

std::string document_of(const Sample& before, const Sample& after) {
    return build_snapshot(before, after, fixture_options()).dump_compact();
}

}  // namespace

int main() {
    // ---------------------------------------------------------------------
    // Swap totals: /proc/meminfo
    // ---------------------------------------------------------------------
    {
        const SwapFacts facts = parse_meminfo_swap(
            "MemTotal:       1000 kB\n"
            "SwapTotal:      33554428 kB\n"
            "SwapFree:        5084264 kB\n");
        CHECK(facts.totals_known);
        CHECK_EQ(facts.total_bytes, 33554428ull * 1024ull);
        CHECK_EQ(facts.free_bytes, 5084264ull * 1024ull);
    }
    {
        // A machine with NO swap still writes both rows with 0. That is a
        // measurement — "this host has no swap" — and must not be reported as an
        // absence.
        const SwapFacts facts = parse_meminfo_swap("SwapTotal: 0 kB\nSwapFree: 0 kB\n");
        CHECK(facts.totals_known);
        CHECK_EQ(facts.total_bytes, 0u);
        CHECK_EQ(facts.free_bytes, 0u);
    }
    {
        const SwapFacts facts = parse_meminfo_swap("SwapFree: 1 kB\n");
        CHECK(!facts.totals_known);
        CHECK_CONTAINS(facts.totals_reason, "no SwapTotal row");
    }
    {
        const SwapFacts facts = parse_meminfo_swap("SwapTotal: 1 kB\n");
        CHECK(!facts.totals_known);
        CHECK_CONTAINS(facts.totals_reason, "no SwapFree row");
    }
    {
        // The unit is in the line, not in the number. A row without it is a row
        // whose scale is unknown, and assuming bytes would be off by 1024.
        const SwapFacts facts = parse_meminfo_swap("SwapTotal: 1000\nSwapFree: 500\n");
        CHECK(!facts.totals_known);
        CHECK_CONTAINS(facts.totals_reason, "not in kB");
    }
    {
        const SwapFacts facts = parse_meminfo_swap("SwapTotal: twelve kB\nSwapFree: 5 kB\n");
        CHECK(!facts.totals_known);
        CHECK_CONTAINS(facts.totals_reason, "non-numeric");
    }
    {
        // kB -> bytes must not wrap. A wrapped uint64 is a small, plausible,
        // completely wrong number of bytes.
        const SwapFacts facts = parse_meminfo_swap(
            "SwapTotal: 18446744073709551615 kB\nSwapFree: 0 kB\n");
        CHECK(!facts.totals_known);
        CHECK_CONTAINS(facts.totals_reason, "overflow");
    }
    {
        const SwapFacts facts = parse_meminfo_swap("SwapTotal: 1 kB\nSwapTotal: 2 kB\n");
        CHECK(!facts.totals_known);
        CHECK_CONTAINS(facts.totals_reason, "SwapTotal twice");
    }
    {
        // Free above total is not a busy machine, it is a file that does not add
        // up; a "used" derived from it would be negative.
        const SwapFacts facts = parse_meminfo_swap("SwapTotal: 10 kB\nSwapFree: 20 kB\n");
        CHECK(!facts.totals_known);
        CHECK_CONTAINS(facts.totals_reason, "more free swap than total");
    }

    // ---------------------------------------------------------------------
    // Swap page counters: /proc/vmstat
    // ---------------------------------------------------------------------
    {
        // Realistic: the file is hundreds of lines and most of them are nothing
        // to do with swap.
        const SwapFacts facts = parse_vmstat_page_counters(
            "nr_free_pages 1234567\n"
            "pswpin 199243849\n"
            "pswpout 632268372\n"
            "pgpgin 12345678\n");
        CHECK(facts.pages_known);
        CHECK_EQ(facts.pages_in, 199243849u);
        CHECK_EQ(facts.pages_out, 632268372u);
    }
    {
        // A lifetime counter that happens to be 0 is measured, not missing.
        const SwapFacts facts = parse_vmstat_page_counters("pswpin 0\npswpout 0\n");
        CHECK(facts.pages_known);
        CHECK_EQ(facts.pages_out, 0u);
    }
    {
        const SwapFacts facts = parse_vmstat_page_counters("pswpin 1\n");
        CHECK(!facts.pages_known);
        CHECK_CONTAINS(facts.pages_reason, "no pswpout row");
    }
    {
        const SwapFacts facts = parse_vmstat_page_counters("pswpout 1\n");
        CHECK(!facts.pages_known);
        CHECK_CONTAINS(facts.pages_reason, "no pswpin row");
    }
    {
        const SwapFacts facts = parse_vmstat_page_counters(
            "pswpin 99999999999999999999999999\npswpout 1\n");
        CHECK(!facts.pages_known);
        CHECK_CONTAINS(facts.pages_reason, "not a uint64");
    }
    {
        const SwapFacts facts = parse_vmstat_page_counters("pswpin 1\npswpin 2\npswpout 1\n");
        CHECK(!facts.pages_known);
        CHECK_CONTAINS(facts.pages_reason, "pswpin twice");
    }
    {
        const SwapFacts facts = parse_vmstat_page_counters("pswpin -5\npswpout 1\n");
        CHECK(!facts.pages_known);
        CHECK_CONTAINS(facts.pages_reason, "not a uint64");
    }

    // ---------------------------------------------------------------------
    // The two halves are independent
    // ---------------------------------------------------------------------
    {
        // meminfo readable, vmstat not: the totals are still a measurement.
        const SwapFacts facts =
            swap_of("SwapTotal: 10 kB\nSwapFree: 4 kB\n", "nr_free_pages 1\n");
        CHECK(facts.totals_known);
        CHECK(!facts.pages_known);
        CHECK_EQ(facts.total_bytes, 10240u);
    }
    {
        // And the other way round.
        const SwapFacts facts = swap_of("MemTotal: 10 kB\n", "pswpin 7\npswpout 9\n");
        CHECK(!facts.totals_known);
        CHECK(facts.pages_known);
        CHECK_EQ(facts.pages_out, 9u);
    }

    // ---------------------------------------------------------------------
    // PSI
    // ---------------------------------------------------------------------
    {
        const PsiFacts facts = parse_pressure("cpu", kRealCpuPressure);
        CHECK(facts.present);
        CHECK_EQ(facts.resource, std::string("cpu"));
        CHECK(facts.some.present);
        CHECK_EQ(facts.some.avg10, 0.69);
        CHECK_EQ(facts.some.avg60, 0.33);
        CHECK_EQ(facts.some.avg300, 0.52);
        CHECK_EQ(facts.some.total_microseconds, 24839609798ull);
        CHECK(facts.full.present);
        CHECK_EQ(facts.full.avg10, 0.0);
    }
    {
        // A REAL ZERO. The kernel says full avg10=0.00 because no task was fully
        // stalled; that is a measurement, and it is the opposite of the case
        // below where the line is not there at all.
        const PsiFacts facts = parse_pressure("memory",
            "some avg10=0.00 avg60=0.00 avg300=0.00 total=18714036837\n"
            "full avg10=0.00 avg60=0.00 avg300=0.00 total=17869255542\n");
        CHECK(facts.present);
        CHECK(facts.full.present);
        CHECK_EQ(facts.full.total_microseconds, 17869255542ull);
    }
    {
        // A kernel that does not provide cpu.full. The `some` line is still a
        // reading; `full` is absent WITH A REASON, and specifically not a zero,
        // which would claim "no task was ever fully stalled".
        const PsiFacts facts = parse_pressure("cpu", kCpuPressureWithoutFull);
        CHECK(facts.present);
        CHECK(facts.some.present);
        CHECK_EQ(facts.some.avg10, 1.25);
        CHECK(!facts.full.present);
        CHECK_CONTAINS(facts.full.reason, "no full line on this kernel");
    }
    {
        // A percentage is a share of wall-clock time. 340 cannot be a reading.
        const PsiFacts facts =
            parse_pressure("io", "some avg10=340.0 avg60=0.0 avg300=0.0 total=1\n");
        CHECK(!facts.present);
        CHECK_CONTAINS(facts.reason, "not a percentage");
    }
    {
        // A negative average is refused by the number lexer before any range
        // check: the kernel writes no sign, so a sign means this is not the file
        // this parser thinks it is.
        const PsiFacts facts =
            parse_pressure("io", "some avg10=-1.0 avg60=0.0 avg300=0.0 total=1\n");
        CHECK(!facts.present);
        CHECK_CONTAINS(facts.reason, "not a percentage");
    }
    {
        // An exponent would let 1e400 arrive as an infinity for a later check to
        // remember to catch. Nothing in /proc writes one, so it is refused.
        const PsiFacts facts =
            parse_pressure("io", "some avg10=1e5 avg60=0.0 avg300=0.0 total=1\n");
        CHECK(!facts.present);
        CHECK_CONTAINS(facts.reason, "not a percentage");
    }
    {
        const PsiFacts facts = parse_pressure("io", "some avg10=1.0 avg60=0.0 total=1\n");
        CHECK(!facts.present);
        CHECK_CONTAINS(facts.reason, "missing");
    }
    {
        const PsiFacts facts =
            parse_pressure("io", "some avg10=1.0 avg10=2.0 avg60=0.0 avg300=0.0 total=1\n");
        CHECK(!facts.present);
        CHECK_CONTAINS(facts.reason, "appears twice");
    }
    {
        const PsiFacts facts = parse_pressure("io",
            "some avg10=1.0 avg60=0.0 avg300=0.0 total=1 total=2\n");
        CHECK(!facts.present);
        CHECK_CONTAINS(facts.reason, "total appears twice");
    }
    {
        // An unknown key inside a line this parser DOES read: the line's shape is
        // not the shape it understands, so the values it did read cannot be
        // trusted either.
        const PsiFacts facts =
            parse_pressure("io", "some avg10=1.0 avg60=0.0 avg300=0.0 total=1 bonus=9\n");
        CHECK(!facts.present);
        CHECK_CONTAINS(facts.reason, "unknown key");
    }
    {
        const PsiFacts facts =
            parse_pressure("io", "some avg10=1.0 avg60=0.0 avg300=0.0\n");
        CHECK(!facts.present);
        CHECK_CONTAINS(facts.reason, "missing");
    }
    {
        const PsiFacts facts = parse_pressure("io",
            "full avg10=1.0 avg60=0.0 avg300=0.0 total=1\n");
        CHECK(!facts.present);
        CHECK_CONTAINS(facts.reason, "full without some");
    }
    {
        const PsiFacts facts = parse_pressure("io",
            "some avg10=1.0 avg60=0.0 avg300=0.0 total=1\n"
            "some avg10=2.0 avg60=0.0 avg300=0.0 total=2\n");
        CHECK(!facts.present);
        CHECK_CONTAINS(facts.reason, "states some twice");
    }
    {
        // Empty is not the calmest possible machine. Reporting 0.00 for an
        // unreadable file is the failure this whole file is about.
        const PsiFacts facts = parse_pressure("memory", "");
        CHECK(!facts.present);
        CHECK_CONTAINS(facts.reason, "neither a some nor a full line");
    }
    {
        const PsiFacts facts = parse_pressure("memory", "garbage without equals\n");
        CHECK(!facts.present);
        CHECK_CONTAINS(facts.reason, "neither a some nor a full line");
    }
    {
        // A line this parser does not know is IGNORED, so a kernel that adds one
        // does not blind the collector — and because nothing here projects an
        // unknown line, ignoring it cannot publish anything either.
        const PsiFacts facts = parse_pressure("cpu",
            "some avg10=1.0 avg60=0.0 avg300=0.0 total=5\n"
            "full avg10=0.0 avg60=0.0 avg300=0.0 total=0\n"
            "avg7 1.5 2.5\n");
        CHECK(facts.present);
        CHECK(facts.full.present);
        CHECK_EQ(facts.some.total_microseconds, 5u);
    }
    {
        const PsiFacts facts = parse_pressure("cpu",
            "some avg10=1.0 avg60=0.0 avg300=0.0 total=5\n"
            "full avg10=0.5 avg60=0.0 avg300=0.0 total=0\n"
            "some avg10=9.9 avg60=0.0 avg300=0.0 total=9\n");
        CHECK(!facts.present);
        CHECK_CONTAINS(facts.reason, "states some twice");
    }

    // ---------------------------------------------------------------------
    // Sampling: absent files, denied reads, and what each produces
    // ---------------------------------------------------------------------
    {
        // The fixture answers ENOENT for anything nobody registered, so this is
        // "no PSI files on this kernel" — every resource reported, none invented.
        FixtureFileSource files;
        const Sample sample = take_sample(files, fake_clock(), SampleRequest::default_request());
        CHECK_EQ(sample.psi.size(), 3u);
        for (const PsiFacts& facts : sample.psi) {
            CHECK(!facts.present);
            CHECK(!facts.reason.empty());
            CHECK(!facts.some.present);
        }
        CHECK_EQ(sample.psi[0].resource, std::string("cpu"));
        CHECK_EQ(sample.psi[2].resource, std::string("io"));
    }
    {
        // Denied is not absent: the errno is carried so an operator can tell a
        // kernel without CONFIG_PSI from a permissions problem.
        FixtureFileSource files;
        files.add_file("/proc/pressure/cpu", kRealCpuPressure);
        files.add_read_error("/proc/pressure/memory", EACCES, "EACCES");
        files.add_file("/proc/pressure/io",
                       "some avg10=0.06 avg60=0.20 avg300=2.72 total=34439564191\n"
                       "full avg10=0.06 avg60=0.19 avg300=2.59 total=31515319896\n");
        const Sample sample = take_sample(files, fake_clock(), SampleRequest::default_request());
        CHECK(sample.psi[0].present);
        CHECK(!sample.psi[1].present);
        CHECK_CONTAINS(sample.psi[1].reason, "EACCES");
        CHECK(sample.psi[2].present);
    }
    {
        // vmstat missing: the paging half is refused, and the file that IS there
        // is still read. One missing file does not blank the other half.
        FixtureFileSource files;
        files.add_file("/proc/meminfo", "MemTotal: 1000 kB\nSwapTotal: 100 kB\nSwapFree: 40 kB\n");
        const Sample sample = take_sample(files, fake_clock(), SampleRequest::default_request());
        CHECK(sample.swap.totals_known);
        CHECK(!sample.swap.pages_known);
        CHECK_CONTAINS(sample.swap.pages_reason, "ENOENT");
    }
    {
        FixtureFileSource files;
        files.add_read_error("/proc/meminfo", EACCES, "EACCES");
        files.add_file("/proc/vmstat", "pswpin 5\npswpout 6\n");
        const Sample sample = take_sample(files, fake_clock(), SampleRequest::default_request());
        CHECK(!sample.swap.totals_known);
        CHECK_CONTAINS(sample.swap.totals_reason, "EACCES");
        // The other half still works, and memory's own failure is separate.
        CHECK(sample.swap.pages_known);
        CHECK(!sample.memory.present);
    }
    {
        // The real files, read through the same path a host uses.
        FixtureFileSource files;
        files.add_file("/proc/meminfo", "SwapTotal: 33554428 kB\nSwapFree: 5084264 kB\n");
        files.add_file("/proc/vmstat", "pswpin 199243849\npswpout 632268372\n");
        for (const char* resource : kPressureResources) {
            files.add_file(std::string("/proc/pressure/") + resource, kRealCpuPressure);
        }
        const Sample sample = take_sample(files, fake_clock(), SampleRequest::default_request());
        CHECK_EQ(sample.swap.total_bytes, 33554428ull * 1024ull);
        CHECK_EQ(sample.swap.pages_out, 632268372u);
        CHECK_EQ(sample.psi.size(), 3u);
        CHECK(sample.psi[0].present);
    }

    // ---------------------------------------------------------------------
    // The document: two samples, and what a delta may say
    // ---------------------------------------------------------------------
    {
        // A REAL ZERO DELTA. Nothing paged between the two reads, which is a
        // measurement: the keys are present and the values are 0, and no
        // UNAVAILABLE appears for them.
        const Sample before = sample_at(1000.0, 500, 900);
        const Sample after = sample_at(1000.5, 500, 900);
        const std::string document = document_of(before, after);
        CHECK_CONTAINS(document, "\"pages_in\":0");
        CHECK_CONTAINS(document, "\"pages_out\":0");
        CHECK_CONTAINS(document, "\"pages_in_per_second\":0.00");
        CHECK_CONTAINS(document, "\"state\":\"PRESENT\"");
    }
    {
        // A counter that went backwards is refused, never rewound. A wrapped
        // value here would be a large, plausible, wrong amount of paging.
        const Sample before = sample_at(1000.0, 500, 900);
        const Sample after = sample_at(1000.5, 500, 100);
        const std::string document = document_of(before, after);
        CHECK_CONTAINS(document, "\"pages_out\":{\"reason\"");
        // The wording belongs to the existing `counter_delta`, which refuses a
        // backwards counter for the whole collector. Asserted as it is, rather
        // than reworded here to match a test.
        CHECK_CONTAINS(document, "counter decreased");
        CHECK_CONTAINS(document, "\"pages_in\":0");
    }
    {
        // No measurable interval: every delta that depends on it is refused,
        // because a rate with no denominator is not a rate.
        Sample before = sample_at(1000.0, 500, 900);
        Sample after = sample_at(1000.5, 600, 1000);
        before.monotonic_seconds.reset();
        before.monotonic_reason = "no /proc/uptime";
        after.monotonic_seconds.reset();
        after.monotonic_reason = "no /proc/uptime";
        const std::string document = document_of(before, after);
        CHECK_CONTAINS(document, "\"pages_out_per_second\":{\"reason\"");
        CHECK_CONTAINS(document, "sample interval is not measurable");
    }
    {
        // No swap at all: two zero bytes totals, and NO percentage — 0% would
        // report "swap is not being touched" about a machine that has none.
        Sample before = sample_at(1000.0, 0, 0);
        Sample after = sample_at(1000.5, 0, 0);
        before.swap.total_bytes = 0;
        before.swap.free_bytes = 0;
        after.swap.total_bytes = 0;
        after.swap.free_bytes = 0;
        const std::string document = document_of(before, after);
        CHECK_CONTAINS(document, "\"total_bytes\":0");
        CHECK_CONTAINS(document, "\"used_percent\":{\"reason\"");
        CHECK_CONTAINS(document, "SwapTotal is zero");
    }
    {
        // A host with swap in use: the stock is PRESENT and the note says what
        // it is not.
        const Sample before = sample_at(1000.0, 500, 900);
        const Sample after = sample_at(1000.5, 700, 1400);
        const std::string document = document_of(before, after);
        CHECK_CONTAINS(document, "\"used_bytes\":3000");
        CHECK_CONTAINS(document, "\"used_percent\":75.0");
        CHECK_CONTAINS(document, "which is a STOCK");
        CHECK_CONTAINS(document, "\"pages_in\":200");
        CHECK_CONTAINS(document, "\"pages_out\":500");
        CHECK_CONTAINS(document, "\"pages_out_per_second\":1000.00");
    }
    {
        // PSI is in the document, one row per resource the sample carried, and
        // the note says why one sample is enough.
        const Sample before = sample_at(1000.0, 500, 900);
        const Sample after = sample_at(1000.5, 500, 900);
        const std::string document = document_of(before, after);
        CHECK_CONTAINS(document, "\"source\":\"/proc/pressure\"");
        CHECK_CONTAINS(document, "\"resource\":\"cpu\"");
        CHECK_CONTAINS(document, "\"avg10\":1.00");
        CHECK_CONTAINS(document, "The kernel windows them");
    }
    {
        // A sample with no PSI at all: the block says so rather than being
        // omitted, so a consumer cannot read absence as "not asked for".
        Sample before = sample_at(1000.0, 500, 900);
        Sample after = sample_at(1000.5, 500, 900);
        before.psi.clear();
        after.psi.clear();
        const std::string document = document_of(before, after);
        CHECK_CONTAINS(document, "\"psi\":{\"reason\":\"no /proc/pressure resource was read\"");
    }

    return check::report("test_swap_psi");
}
