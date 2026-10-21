// The strict reader: every rule, and the refusal code it produces.
//
// These cases are about REFUSALS rather than about parsing, because parsing
// JSON is not where a control plane gets hurt. What hurts is a document that
// was almost right: a field with a typo, a unit id with a traversal in it, a
// dependency naming a service nobody declared. Each of those has to arrive as
// a named refusal rather than as a default, and each case below names the code
// it expects, so a wording change cannot quietly turn a refusal into a pass.
//
// Manifests are assembled by helpers rather than written as raw string
// literals. A case then says only the field it means to break, and a raw
// string that closes one character early cannot hide inside the fixture.
#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include "check.hpp"
#include "supervisor/manifest.hpp"
#include "supervisor/sha256.hpp"

using namespace nodecore::supervisor;

namespace {

std::string jesc(const std::string& value) {
    std::string out;
    for (char c : value) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default: out += c;
        }
    }
    return out;
}

// A JSON string, escaped. Every manifest below is built from these.
std::string q(const std::string& value) { return "\"" + jesc(value) + "\""; }

std::string base(const std::string& services) {
    return "{" + q("schema") + ":" + q(kDesiredStateSchema) + "," + q("node") + ":" +
           q("device:x") + "," + q("principal") + ":" + q("vince") + "," + q("generation") + ":1," +
           q("services") + ":" + services + "}";
}

// The one service every case starts from: id a, owner vince, unit a.service,
// desired running. `overrides` REPLACES a default member (it does not append a
// second one — two `unit` keys is a duplicate-key refusal, not a test of the
// unit rule), and `omit` drops one so a case can ask what an absent field does.
std::string service(const std::vector<std::pair<std::string, std::string>>& overrides = {},
                    const std::vector<std::string>& omit = {}) {
    std::vector<std::pair<std::string, std::string>> members{{"id", q("a")},
                                                             {"owner", q("vince")},
                                                             {"unit", q("a.service")},
                                                             {"desired", q("running")}};
    for (const auto& override : overrides) {
        bool replaced = false;
        for (auto& member : members) {
            if (member.first == override.first) {
                member.second = override.second;
                replaced = true;
                break;
            }
        }
        if (!replaced) {
            members.push_back(override);
        }
    }
    std::string text = "{";
    bool first = true;
    for (const auto& member : members) {
        bool dropped = false;
        for (const std::string& key : omit) {
            if (member.first == key) {
                dropped = true;
                break;
            }
        }
        if (dropped) {
            continue;
        }
        if (!first) {
            text += ",";
        }
        text += q(member.first) + ":" + member.second;
        first = false;
    }
    return text + "}";
}

std::string doc_with(const std::string& services) { return base("[" + services + "]"); }

// A whole manifest with `services` given verbatim and `extra` appended to the
// document, for the cases that are about the document rather than a service.
std::string doc_with_document(const std::string& services, const std::string& extra = "") {
    return "{" + q("schema") + ":" + q(kDesiredStateSchema) + "," + q("node") + ":" +
           q("device:x") + "," + q("principal") + ":" + q("vince") + "," + q("generation") + ":1" +
           (extra.empty() ? std::string() : "," + extra) + "," + q("services") + ":" + services +
           "}";
}

std::string codes_of(const ParseOutcome& outcome) {
    std::string joined;
    for (const Refusal& refusal : outcome.refusals) {
        joined += refusal.scope + ":" + refusal.code + ": " + refusal.reason + " || ";
    }
    return joined.empty() ? std::string("(none)") : joined;
}

// Prints the codes it DID see when it fails. A bare "expected UNKNOWN_DEPENDENCY"
// leaves a reader guessing which refusal actually arrived, and the guess is
// usually wrong: the first run of this file had cases whose manifest was
// malformed JSON, so the expected code never had a chance to appear.
bool has_code(const ParseOutcome& outcome, const std::string& code) {
    for (const Refusal& refusal : outcome.refusals) {
        if (refusal.code == code) {
            return true;
        }
    }
    std::fprintf(stderr, "  expected code %s, saw: %s\n", code.c_str(), codes_of(outcome).c_str());
    return false;
}

std::string read_file(const std::string& path) {
    std::ifstream stream(path);
    std::ostringstream buffer;
    buffer << stream.rdbuf();
    return buffer.str();
}

}  // namespace

int main() {
    // --------------------------------------------------------- the happy path

    {
        const ParseOutcome outcome = parse_manifest(doc_with(service()));
        CHECK(outcome.ok);
        CHECK_EQ(outcome.refusals.size(), std::size_t{0});
        CHECK_EQ(outcome.manifest.schema, std::string(kDesiredStateSchema));
        CHECK_EQ(outcome.manifest.services.size(), std::size_t{1});
        CHECK_EQ(outcome.manifest.services[0].unit, std::string("a.service"));
        CHECK(outcome.manifest.services[0].desired == Desired::Running);
        // Defaults a manifest INHERITS rather than states: section 19's numbers
        // from runtime/daemon.py, and observe_only on.
        CHECK(outcome.manifest.observe_only);
        CHECK_EQ(outcome.manifest.services[0].recovery.max_attempts, 3);
        CHECK_EQ(outcome.manifest.services[0].recovery.window_seconds, 3600);
        CHECK_EQ(outcome.manifest.services[0].recovery.backoff_seconds.size(), std::size_t{3});
        CHECK(outcome.manifest.services[0].start_on_boot == false);
        CHECK(outcome.manifest.services[0].health.probe == "systemd-is-active");
    }

    // The digest is over the bytes AS READ, so `sha256sum` on the same file
    // reproduces it. This is what makes the receipt's integrity record
    // checkable by a person rather than only by this program.
    {
        const std::string text = doc_with(service());
        const ParseOutcome outcome = parse_manifest(text);
        CHECK_EQ(outcome.manifest.sha256, sha256_hex(text));
    }

    // ----------------------------------------------------------- observe_only

    {
        const ParseOutcome outcome =
            parse_manifest(doc_with_document("[" + service() + "]", q("observe_only") + ":false"));
        CHECK(outcome.ok);
        CHECK(outcome.manifest.observe_only == false);
    }
    {
        // The default is on, and a STRING "false" is truthy. A reader that
        // coerced it would turn the safety default off, which is exactly the
        // failure this checks.
        const ParseOutcome control = parse_manifest(doc_with(service()));
        CHECK(control.manifest.observe_only);
        const ParseOutcome coerced = parse_manifest(
            doc_with_document("[" + service() + "]", q("observe_only") + ":" + q("false")));
        CHECK(!coerced.ok);
        CHECK(has_code(coerced, "TYPE"));
    }

    // ------------------------------------------------------------ vocabulary

    {
        const ParseOutcome outcome = parse_manifest(doc_with(service({{"desired", q("Running")}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "VOCABULARY"));
    }
    {
        // A word nobody defined must not fall through to a default: a desired
        // that defaulted to running authorises a start nobody wrote.
        const ParseOutcome outcome = parse_manifest(doc_with(service({{"desired", q("maybe")}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "VOCABULARY"));
    }
    {
        const ParseOutcome outcome = parse_manifest(doc_with(
            service({{"health", "{" + q("probe") + ":" + q("http-get") + "}"}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "VOCABULARY"));
    }

    // ------------------------------------------- service-level probes (V1)

    {
        // A service-level probe parses, and its target travels with it: this is
        // the probe that asks the SERVICE rather than the unit.
        const ParseOutcome outcome = parse_manifest(doc_with(service(
            {{"health", "{" + q("probe") + ":" + q("unix-socket-connect") + "," + q("probe_path") +
                            ":" + q("/run/user/1000/toy.sock") + "}"}})));
        CHECK(outcome.ok);
        CHECK_EQ(outcome.manifest.services[0].health.probe, std::string("unix-socket-connect"));
        CHECK_EQ(outcome.manifest.services[0].health.probe_path,
                 std::string("/run/user/1000/toy.sock"));
    }
    {
        const ParseOutcome outcome = parse_manifest(doc_with(service(
            {{"health", "{" + q("probe") + ":" + q("tcp-connect") + "," + q("probe_host") + ":" +
                            q("127.0.0.1") + "," + q("probe_port") + ":9200}"}})));
        CHECK(outcome.ok);
        CHECK_EQ(outcome.manifest.services[0].health.probe_port, 9200);
    }
    {
        // A probe that names a target this Core will not connect to is refused
        // by the READER, through the same `validate_probe` the executor calls —
        // so a spec the reader accepted cannot be one the executor refuses.
        const ParseOutcome outcome = parse_manifest(doc_with(service(
            {{"health", "{" + q("probe") + ":" + q("tcp-connect") + "," + q("probe_host") + ":" +
                            q("10.0.0.1") + "," + q("probe_port") + ":9200}"}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "VOCABULARY"));
    }
    {
        const ParseOutcome outcome = parse_manifest(doc_with(
            service({{"health", "{" + q("probe") + ":" + q("unix-socket-connect") + "}"}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "VOCABULARY"));
    }
    {
        // A target beside the default probe is refused, not ignored: a field
        // nobody reads is a rule nobody applied.
        const ParseOutcome outcome = parse_manifest(
            doc_with(service({{"health", "{" + q("probe_path") + ":" + q("/run/x.sock") + "}"}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "VOCABULARY"));
    }
    {
        const ParseOutcome outcome = parse_manifest(doc_with(service(
            {{"health", "{" + q("probe") + ":" + q("unix-socket-connect") + "," + q("probe_path") +
                            ":" + q("relative.sock") + "}"}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "VOCABULARY"));
    }
    {
        // The typo this strictness exists for: a mistyped target key is an
        // unknown field, not a probe that silently has no target.
        const ParseOutcome outcome = parse_manifest(doc_with(service(
            {{"health", "{" + q("probe") + ":" + q("unix-socket-connect") + "," + q("probe_socket") +
                            ":" + q("/run/x.sock") + "}"}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "UNKNOWN_FIELD"));
    }
    {
        // OFFLINE RECOVERY is a per-service, written-down opt-in. It defaults
        // off: an unstated fact is not a licence to act offline.
        const ParseOutcome defaulted = parse_manifest(doc_with(service()));
        CHECK(defaulted.ok);
        CHECK(!defaulted.manifest.services[0].recovery.offline_recoverable);

        const ParseOutcome declared = parse_manifest(
            doc_with(service({{"recovery", "{" + q("offline_recoverable") + ":true}"}})));
        CHECK(declared.ok);
        CHECK(declared.manifest.services[0].recovery.offline_recoverable);
    }
    {
        // Refused rather than coerced: the string "true" is truthy, and coercing
        // it would switch the safe default on.
        const ParseOutcome outcome = parse_manifest(doc_with(
            service({{"recovery", "{" + q("offline_recoverable") + ":" + q("true") + "}"}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "TYPE"));
    }
    {
        const ParseOutcome outcome = parse_manifest(doc_with(service(
            {{"recovery", "{" + q("allowed_verbs") + ":[" + q("start") + "," + q("delete") + "]}"}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "VOCABULARY"));
    }
    {
        // An empty list is legal and means "the Core may do nothing here".
        const ParseOutcome outcome = parse_manifest(doc_with(service(
            {{"desired", q("manual")},
             {"recovery", "{" + q("allowed_verbs") + ":[]}"}})));
        CHECK(outcome.ok);
    }

    // --------------------------------------------------------------- unit ids

    const std::vector<std::string> bad_units{
        "../../etc/passwd.service",  // traversal
        "a/../b.service",            // traversal, well formed otherwise
        "a; rm -rf /.service",       // shell metacharacter
        "$(id).service",             // command substitution
        "a b.service",               // a space
        "/bin/sh.service",           // absolute path shape
        "a.service --user",          // an extra word smuggled in
        "a",                         // no .service suffix
        ".service",                  // empty stem
        "a.Service",                 // the suffix is case-sensitive
        "a.socket",                  // a different unit kind
        "a\nb.service",              // a newline, escaped by q() and decoded by the reader
        "-a.service",                // a leading dash would look like a flag
    };
    for (const std::string& unit : bad_units) {
        const ParseOutcome outcome = parse_manifest(doc_with(service({{"unit", q(unit)}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "UNIT_ID"));
    }

    const std::vector<std::string> good_units{"a.service", "serveros-fleet-node.service",
                                              "serveros-tunnel@1.service", "a_b.service"};
    for (const std::string& unit : good_units) {
        CHECK(valid_unit_id(unit));
        const ParseOutcome outcome = parse_manifest(doc_with(service({{"unit", q(unit)}})));
        CHECK(outcome.ok);
    }

    // -------------------------------------------------------------- the graph

    {
        const std::string second = "{" + q("id") + ":" + q("a") + "," + q("owner") + ":" +
                                   q("vince") + "," + q("unit") + ":" + q("b.service") + "," +
                                   q("desired") + ":" + q("running") + "}";
        const ParseOutcome outcome = parse_manifest(doc_with(service() + "," + second));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "DUPLICATE_ID"));
    }
    {
        const ParseOutcome outcome = parse_manifest(doc_with(
            service({{"depends_on", "[" + q("ghost") + "]"}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "UNKNOWN_DEPENDENCY"));
    }
    {
        const ParseOutcome outcome =
            parse_manifest(doc_with(service({{"depends_on", "[" + q("a") + "]"}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "SELF_DEPENDENCY"));
    }

    // --------------------------------------------------- strict over the fields

    {
        const ParseOutcome outcome = parse_manifest(doc_with(service({{"start_on_boot_at", q("never")}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "UNKNOWN_FIELD"));
    }
    {
        // The typo this exists for: a misspelled start_on_boot would silently
        // default to false, the service never comes back after a reboot, and
        // nobody finds out until the reboot.
        const ParseOutcome outcome = parse_manifest(
            doc_with(service({{"start_on_boot", "true"}, {"start_on_boots", "true"}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "UNKNOWN_FIELD"));
    }
    {
        const ParseOutcome outcome = parse_manifest(doc_with(service({}, {"desired"})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "MISSING"));
    }
    {
        const ParseOutcome outcome =
            parse_manifest(doc_with(service({{"start_on_boot", q("yes")}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "TYPE"));
    }
    {
        const ParseOutcome outcome = parse_manifest(doc_with(service({{"id", q("")}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "EMPTY"));
    }
    {
        const ParseOutcome outcome = parse_manifest(
            doc_with(service({{"recovery", "{" + q("max_attempts") + ":0}"}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "RANGE"));
    }
    {
        // A ladder that goes DOWN re-tries sooner after each failure, which is
        // a storm rather than a backoff.
        const ParseOutcome outcome = parse_manifest(
            doc_with(service({{"recovery", "{" + q("backoff_seconds") + ":[900,60]}"}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "RANGE"));
    }
    {
        const ParseOutcome outcome = parse_manifest(
            doc_with(service({{"recovery", "{" + q("backoff_seconds") + ":[]}"}})));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "RANGE"));
    }

    // ---------------------------------------------------- strict over the JSON

    {
        const ParseOutcome outcome = parse_manifest("{");
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "NOT_JSON"));
    }
    {
        // Two `generation` keys: RFC 8259 leaves the winner undefined, and a
        // reader that silently keeps one has invented a rule.
        const ParseOutcome outcome =
            parse_manifest(doc_with_document("[]", q("generation") + ":2"));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "NOT_JSON"));
    }
    {
        const ParseOutcome outcome =
            parse_manifest(doc_with_document("[]", q("generation") + ":01"));
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "NOT_JSON"));
    }
    {
        const ParseOutcome outcome = parse_manifest(doc_with(service()) + " trailing");
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "NOT_JSON"));
    }

    // ---------------------------------------------------------- wrong schema

    {
        const std::string text = "{" + q("schema") + ":" + q("serveros/desired-state.v2") + "," +
                                 q("node") + ":" + q("device:x") + "," + q("principal") + ":" +
                                 q("vince") + "," + q("generation") + ":1," + q("services") + ":[]}";
        const ParseOutcome outcome = parse_manifest(text);
        CHECK(!outcome.ok);
        CHECK(has_code(outcome, "SCHEMA"));
    }

    // ------------------------------------------------- the committed example

    {
        const std::string path =
            std::string(SUPERVISOR_SOURCE_DIR) + "/examples/desired-state.example.json";
        const std::string text = read_file(path);
        CHECK(text.size() > 100);
        const ParseOutcome outcome = parse_manifest(text);
        if (!outcome.ok) {
            std::fprintf(stderr, "example manifest refusals: %s\n", codes_of(outcome).c_str());
        }
        CHECK(outcome.ok);
        CHECK_EQ(outcome.manifest.services.size(), std::size_t{4});
        CHECK_EQ(outcome.manifest.services[1].depends_on.size(), std::size_t{1});
        CHECK_EQ(outcome.manifest.services[1].depends_on[0], std::string("tunnel"));
        CHECK(outcome.manifest.observe_only);
        // The example demonstrates BOTH service-level probes: the tunnel asks a
        // unix socket and is offline-recoverable, the fleet node asks a loopback
        // port. A committed example that stopped parsing would be documentation
        // that had drifted from the code.
        CHECK_EQ(outcome.manifest.services[0].health.probe, std::string("unix-socket-connect"));
        CHECK_EQ(outcome.manifest.services[0].health.probe_path,
                 std::string("/run/user/1000/serveros-tunnel.sock"));
        CHECK(outcome.manifest.services[0].recovery.offline_recoverable);
        CHECK_EQ(outcome.manifest.services[1].health.probe, std::string("tcp-connect"));
        CHECK_EQ(outcome.manifest.services[1].health.probe_port, 3401);
        CHECK(!outcome.manifest.services[1].recovery.offline_recoverable);
    }

    return check::report("test_sup_manifest");
}
