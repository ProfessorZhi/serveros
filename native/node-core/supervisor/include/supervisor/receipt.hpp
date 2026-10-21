// The receipt: what this Core decided, what it asked for, and what came back.
//
// A decision nobody can read afterwards is a decision nobody can audit. So
// every pass writes one document that carries FOUR separable things, kept apart
// on purpose:
//
//   * WHICH manifest this was about — its schema, its generation and the
//     SHA-256 of its bytes. A reader can run `sha256sum` on the file and get
//     this number, which is what makes "acted on generation 7" a checkable
//     claim rather than a note. The Core holds NO signing key: this proves
//     which bytes were read and never who wrote them.
//   * WHAT WAS PLANNED — the order and every action, or every refusal with the
//     code that produced it.
//   * WHAT WAS ASKED — one row per verb, with the provider's own code and exit
//     status, so a failed `systemctl` is a fact about the attempt rather than a
//     silence.
//   * WHAT DID NOT HAPPEN — the refusals, including the actions an
//     observe-only pass decided and did not take.
//
// It carries no key, token, credential or private path. This program has none
// to carry, and a receipt is a document people paste into chats.
#pragma once

#include <string>
#include <vector>

#include "supervisor/manifest.hpp"
#include "supervisor/plan.hpp"

namespace nodecore::supervisor {

inline constexpr const char* kReceiptSchema = "serveros/native-core-receipt.v1";

// What one attempted verb came back as. The provider supplies every field; the
// receipt does not reinterpret any of them.
struct ExecutionResult {
    std::string service_id;
    std::string unit;
    std::string verb;
    bool ok = false;
    std::string code;  // the provider's own token: EXITED, TIMED_OUT, …
    std::string reason;
    int exit_code = -1;
};

// `mode` is "observe-only" or "active" and is written down rather than inferred
// from an empty action list: a reader must be able to tell "nothing was asked
// because observe_only is on" from "nothing was asked because nothing needed
// doing", and those two produce identical empty lists.
std::string build_receipt(const Manifest& manifest, const Plan& plan,
                          const std::vector<ExecutionResult>& executed,
                          const std::string& mode, const std::string& started_at,
                          const std::string& finished_at);

}  // namespace nodecore::supervisor
