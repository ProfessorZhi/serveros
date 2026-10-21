// The receipt, parsed back rather than pattern-matched.
//
// The document is produced by `nodecore::Json` and read here by the component's
// own strict reader, so "it is valid JSON" is checked by parsing it and not by
// looking for a brace. What is asserted is that a reader of a receipt can tell
// apart the four things it carries: which manifest, what was planned, what was
// asked, and what was refused.
#include <cstdio>
#include <string>
#include <vector>

#include "check.hpp"
#include "json_detail.hpp"
#include "supervisor/receipt.hpp"

using namespace nodecore::supervisor;

namespace {

std::string q(const std::string& value) { return "\"" + value + "\""; }

std::string service(const std::string& id, const std::string& unit, const std::string& extra) {
    return "{" + q("id") + ":" + q(id) + "," + q("owner") + ":" + q("vince") + "," + q("unit") +
           ":" + q(unit) + "," + q("desired") + ":" + q("running") + "," + extra + "}";
}

Manifest parsed(const std::string& text) {
    const ParseOutcome outcome = parse_manifest(text);
    CHECK(outcome.ok);
    return outcome.manifest;
}

const detail::Json* member(const detail::Json& object, const std::string& key) {
    return object.find(key);
}

std::string text_of(const detail::Json& object, const std::string& key) {
    const detail::Json* found = object.find(key);
    return found != nullptr && found->is_string() ? found->text : std::string("(absent)");
}

long integer_of(const detail::Json& object, const std::string& key) {
    const detail::Json* found = object.find(key);
    return found != nullptr && found->is_int() ? static_cast<long>(found->integer) : -1;
}

}  // namespace

int main() {
    const std::string text =
        "{" + q("schema") + ":" + q(kDesiredStateSchema) + "," + q("node") + ":" +
        q("device:tokyo-shared-dev-01") + "," + q("principal") + ":" + q("vince") + "," +
        q("observe_only") + ":false," + q("generation") + ":7," + q("services") + ":[" +
        service("tunnel", "tunnel.service", q("start_on_boot") + ":true") + "," +
        service("cli", "cli.service", q("start_on_boot") + ":false") + "]}";
    const Manifest manifest = parsed(text);

    PlanInput input;
    input.manifest = &manifest;
    input.trigger = Trigger::Boot;
    input.now = 1000;
    input.allowed_units = {"tunnel.service", "cli.service"};
    Observed down;
    down.unit = "tunnel.service";
    down.active = Active::Inactive;
    down.measured = true;
    down.age_seconds = 3;
    input.observed["tunnel.service"] = down;
    down.unit = "cli.service";
    input.observed["cli.service"] = down;

    const Plan plan = build_plan(input);
    CHECK_EQ(plan.actions.size(), std::size_t{1});

    const std::vector<ExecutionResult> executed{
        ExecutionResult{"tunnel", "tunnel.service", "start", true, "EXITED", "", 0}};
    const std::string receipt = build_receipt(manifest, plan, executed, "active",
                                              "2026-10-14T00:00:00+00:00",
                                              "2026-10-14T00:00:01+00:00");

    // No key, token, credential or password anywhere in a document people paste
    // into chats. This program holds none; the check is here so that a future
    // field cannot add one quietly.
    for (const char* forbidden : {"key", "token", "secret", "password", "credential", "passwd"}) {
        CHECK(receipt.find(forbidden) == std::string::npos);
    }

    detail::Json document;
    std::string error;
    detail::Reader reader(receipt);
    CHECK(reader.parse(document, error));
    if (!error.empty()) {
        std::fprintf(stderr, "receipt did not parse: %s\n", error.c_str());
    }

    CHECK_EQ(text_of(document, "schema"), std::string(kReceiptSchema));
    CHECK_EQ(text_of(document, "mode"), std::string("active"));
    CHECK_EQ(text_of(document, "node"), std::string("device:tokyo-shared-dev-01"));
    CHECK_EQ(text_of(document, "principal"), std::string("vince"));
    CHECK_EQ(text_of(document, "trigger"), std::string("boot"));
    CHECK_EQ(text_of(document, "started_at"), std::string("2026-10-14T00:00:00+00:00"));

    // Which manifest: the generation AND the digest of its bytes. The digest is
    // the one a person can reproduce with `sha256sum` on the file.
    const detail::Json* about = member(document, "manifest");
    CHECK(about != nullptr && about->is_object());
    if (about != nullptr) {
        CHECK_EQ(integer_of(*about, "generation"), 7L);
        CHECK_EQ(text_of(*about, "sha256"), manifest.sha256);
        CHECK_EQ(text_of(*about, "schema"), std::string(kDesiredStateSchema));
    }

    // What was planned.
    const detail::Json* planned = member(document, "plan");
    CHECK(planned != nullptr && planned->is_object());
    if (planned != nullptr) {
        const detail::Json* ok = planned->find("ok");
        CHECK(ok != nullptr && ok->is_bool() && ok->boolean);
        const detail::Json* actions = planned->find("actions");
        CHECK(actions != nullptr && actions->is_array() && actions->items.size() == 1);
        CHECK_EQ(text_of(actions->items[0], "verb"), std::string("start"));
        const detail::Json* refusals = planned->find("refusals");
        CHECK(refusals != nullptr && refusals->is_array());
        CHECK(refusals->items.size() >= 1);
        bool saw_not_start_on_boot = false;
        for (const detail::Json& row : refusals->items) {
            if (text_of(row, "code") == "NOT_START_ON_BOOT") {
                saw_not_start_on_boot = true;
            }
        }
        CHECK(saw_not_start_on_boot);
    }

    // What was asked, and what came back.
    const detail::Json* attempts = member(document, "executed");
    CHECK(attempts != nullptr && attempts->is_array() && attempts->items.size() == 1);
    if (attempts != nullptr && attempts->items.size() == 1) {
        CHECK_EQ(text_of(attempts->items[0], "unit"), std::string("tunnel.service"));
        CHECK_EQ(text_of(attempts->items[0], "code"), std::string("EXITED"));
        CHECK_EQ(integer_of(attempts->items[0], "exit_code"), 0L);
    }
    const detail::Json* summary = member(document, "summary");
    CHECK(summary != nullptr && summary->is_object());
    if (summary != nullptr) {
        CHECK_EQ(integer_of(*summary, "actions"), 1L);
        CHECK_EQ(integer_of(*summary, "executed"), 1L);
        CHECK_EQ(integer_of(*summary, "failed"), 0L);
    }

    // A FAILED attempt is a fact about the attempt, not a silence.
    {
        const std::vector<ExecutionResult> bad{
            ExecutionResult{"tunnel", "tunnel.service", "start", false, "EXITED",
                            "systemctl start exited 1: Job failed", 1}};
        const std::string failed =
            build_receipt(manifest, plan, bad, "active", "t0", "t1");
        detail::Json parsed_failed;
        std::string failure_error;
        detail::Reader failed_reader(failed);
        CHECK(failed_reader.parse(parsed_failed, failure_error));
        const detail::Json* failed_summary = member(parsed_failed, "summary");
        CHECK(failed_summary != nullptr && integer_of(*failed_summary, "failed") == 1L);
        CHECK_CONTAINS(failed, "Job failed");
    }

    // An observe-only receipt says so. The mode is written down rather than
    // inferred from an empty action list, because "nothing was asked because
    // observe_only is on" and "nothing needed doing" produce the same empty list.
    {
        Manifest quiet = manifest;
        quiet.observe_only = true;
        const Plan quiet_plan = build_plan(input);
        const std::string quiet_receipt =
            build_receipt(quiet, quiet_plan, {}, "observe-only", "t0", "t1");
        detail::Json parsed_quiet;
        std::string quiet_error;
        detail::Reader quiet_reader(quiet_receipt);
        CHECK(quiet_reader.parse(parsed_quiet, quiet_error));
        CHECK_EQ(text_of(parsed_quiet, "mode"), std::string("observe-only"));
    }

    return check::report("test_sup_receipt");
}
