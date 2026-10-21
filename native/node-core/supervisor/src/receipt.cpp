#include "supervisor/receipt.hpp"

#include <string>
#include <utility>
#include <vector>

#include "nodecore/json.hpp"

namespace nodecore::supervisor {
namespace {

nodecore::Json strings(const std::vector<std::string>& values) {
    nodecore::Json array = nodecore::Json::array();
    for (const std::string& value : values) {
        array.push(nodecore::Json::string(value));
    }
    return array;
}

nodecore::Json refusals_json(const std::vector<Refusal>& refusals) {
    nodecore::Json array = nodecore::Json::array();
    for (const Refusal& refusal : refusals) {
        nodecore::Json row = nodecore::Json::object();
        row.set("scope", nodecore::Json::string(refusal.scope));
        row.set("code", nodecore::Json::string(refusal.code));
        row.set("reason", nodecore::Json::string(refusal.reason));
        array.push(std::move(row));
    }
    return array;
}

}  // namespace

std::string build_receipt(const Manifest& manifest, const Plan& plan,
                          const std::vector<ExecutionResult>& executed, const std::string& mode,
                          const std::string& started_at, const std::string& finished_at) {
    nodecore::Json root = nodecore::Json::object();
    root.set("schema", nodecore::Json::string(kReceiptSchema));
    root.set("mode", nodecore::Json::string(mode));
    root.set("node", nodecore::Json::string(manifest.node));
    root.set("principal", nodecore::Json::string(manifest.principal));
    root.set("trigger", nodecore::Json::string(to_word(plan.trigger)));
    root.set("started_at", nodecore::Json::string(started_at));
    root.set("finished_at", nodecore::Json::string(finished_at));

    nodecore::Json about = nodecore::Json::object();
    about.set("schema", nodecore::Json::string(plan.manifest_schema));
    about.set("generation", nodecore::Json::integer(plan.generation));
    // Over the manifest's bytes as read. No signing key is involved, so this
    // says which bytes were used and never who wrote them.
    about.set("sha256", nodecore::Json::string(plan.manifest_sha256));
    root.set("manifest", std::move(about));

    nodecore::Json planned = nodecore::Json::object();
    planned.set("ok", nodecore::Json::boolean(plan.ok));
    planned.set("order", strings(plan.order));

    nodecore::Json actions = nodecore::Json::array();
    for (const Action& action : plan.actions) {
        nodecore::Json row = nodecore::Json::object();
        row.set("service_id", nodecore::Json::string(action.service_id));
        row.set("unit", nodecore::Json::string(action.unit));
        row.set("verb", nodecore::Json::string(action.verb));
        row.set("reason", nodecore::Json::string(action.reason));
        actions.push(std::move(row));
    }
    planned.set("actions", std::move(actions));
    planned.set("refusals", refusals_json(plan.refusals));
    root.set("plan", std::move(planned));

    nodecore::Json attempts = nodecore::Json::array();
    int failed = 0;
    for (const ExecutionResult& result : executed) {
        nodecore::Json row = nodecore::Json::object();
        row.set("service_id", nodecore::Json::string(result.service_id));
        row.set("unit", nodecore::Json::string(result.unit));
        row.set("verb", nodecore::Json::string(result.verb));
        row.set("ok", nodecore::Json::boolean(result.ok));
        row.set("code", nodecore::Json::string(result.code));
        row.set("reason", nodecore::Json::string(result.reason));
        row.set("exit_code", nodecore::Json::integer(result.exit_code));
        if (!result.ok) {
            failed += 1;
        }
        attempts.push(std::move(row));
    }
    root.set("executed", std::move(attempts));

    nodecore::Json summary = nodecore::Json::object();
    summary.set("actions", nodecore::Json::integer(static_cast<long long>(plan.actions.size())));
    summary.set("refusals", nodecore::Json::integer(static_cast<long long>(plan.refusals.size())));
    summary.set("executed", nodecore::Json::integer(static_cast<long long>(executed.size())));
    summary.set("failed", nodecore::Json::integer(failed));
    root.set("summary", std::move(summary));

    // The pretty form, sorted keys — the same canonical shape the rest of this
    // tree hashes and diffs, so two receipts that say the same thing are the
    // same bytes.
    return root.dump();
}

}  // namespace nodecore::supervisor
