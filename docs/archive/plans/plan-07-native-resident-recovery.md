# Plan — resident limited recovery for the Linux native Core

Companion to `proposals/proposal-native-resident-recovery.md`. Base
`69a18bc79e194bbfda63182bf13ce9c5d9dbd5b0`, item `SRV-0011`.

## Scope

In: `native/node-core/supervisor/**`, the Linux contract under
`native/node-core/contract/**`, native tests, this proposal and this plan.

Out (and untouched): Windows core, `fleet_mcp/**`, `runtime/**`, other agents'
files, any production unit, any credential, any model or GPU task.

## Review round 1 — proposal against the existing contract

Read `contract/bounded-recovery.v1.md`, `admission.hpp`, `recovery.hpp`,
`lease.hpp`, `action_gate.hpp`, `serve.hpp`, `plan.cpp`, `provider.cpp`.

Findings that changed the plan:

1. **§6 of the existing contract already names this gap.** The plan must therefore
   be an addition to that contract, not a rewrite of it. → added
   `contract/native-core-facts.v1.md`, left v1 intact.
2. **`admission.cpp` needs no change.** It already refuses on unknown link, on
   unknown agent, and on `NEEDS_LOGIN`. The new module produces those same
   structs; changing the gate would have been a second opinion about the same
   rules. → gate untouched.
3. **`--link-state`/`--agent-state` are parsed once, before the manifest is even
   read.** A loop cannot reuse them. → the resident path takes its facts from
   the document and ignores those flags in that mode.
4. **The provider already runs `systemctl --user`** (`systemd_user.cpp:18`), so no
   privilege question arises for user units.

## Review round 2 — plan against the code being edited

Re-read `main.cpp` end to end after the first edits.

Findings that changed the implementation:

1. **The refusal is not the only gate.** `ActionGate` runs *before* the loop and
   requires `link_known`/`agent_known`. Filling those from the CLI flags would
   have demanded exactly the one-shot fact the design removes. → at startup the
   gate reads the facts document (once) when the resident path is chosen.
2. **An empty `--allow-unit` allowlist permits nothing**, so the first live run
   silently planned nothing. That is correct behaviour, not a bug — the run must
   name the unit. Recorded because it initially looked like a failure.
3. **`restart` is proposed for a failed unit, not `start`.** A manifest granting
   only `start` is refused with `VERB_NOT_ALLOWED`. That is the owner allowlist
   working; the test manifest grants both.
4. **A dangling-reference bug** was found and fixed while testing: `detail::Reader`
   holds a reference to the text, so passing a temporary `std::string` read freed
   memory and produced `unexpected character at byte 0`. The fix binds a named
   `document` first. Its regression is covered by the parse tests.
5. **A comment claiming "the resident path never writes state because it is
   observe-only"** became false with this change and was corrected rather than
   left standing.

## Implementation

| file | change |
|---|---|
| `supervisor/include/supervisor/facts.hpp` | new: `HostFacts`, `ServiceFact`, `FactsOptions`, closed refusal codes |
| `supervisor/src/facts.cpp` | new: strict parse (unknown field refused), whole-document trust, injected clock, staleness and future-skew bounds |
| `supervisor/src/main.cpp` | `--facts`, `--facts-max-age-seconds`; refusal replaced by a gated admission; per-tick load; per-service maintenance refusal; tick uses `act` |
| `supervisor/CMakeLists.txt`, `supervisor/tests/CMakeLists.txt` | register the source and the new test |
| `supervisor/tests/test_sup_facts.cpp` | 16 cases, 53 checks |
| `contract/native-core-facts.v1.md` | the document's contract |

## Verification actually run

Build: direct `g++ -std=c++20 -Wall -Wextra -Wpedantic` (this host has no
cmake/ctest). Library objects compiled once, each test linked against them.

| suite | result |
|---|---|
| `test_sup_facts` (new) | 53 checks, 0 failures |
| all 15 pre-existing supervisor suites | **all pass**, ≈908 checks, 0 failures |
| `test_sup_lease` | passes here (the known Tokyo-path failure did not reproduce) |

Live toy (a real transient user unit, `srv0011toy*`, stopped and removed after):

| case | expected | observed |
|---|---|---|
| failed toy + fresh facts | recover once | **`failed` → `active`**, budget persisted, `consecutive_failures` 0 |
| facts 200 s old at startup | refuse to start | exit 2, `FACTS_STALE`, `LINK_STATE_REQUIRED` |
| facts age out mid-run (limit 2 s) | act early, then stop | recovered on tick 1; ticks 3–6 logged `FACTS_STALE`; no further action |
| agent busy | no action | refused, reason names busy-not-broken |
| maintenance declared | no action | `MAINTENANCE` refusal |
| second writer holds the lease | no action | "another writer holds the lease" |
| verbb not in `allowed_verbs` | no action | `VERB_NOT_ALLOWED` |
| `--serve --enable-actions` without `--facts` | refuse | exit 2 |
| `--facts` without serve+actions | refuse | exit 2 |
| `--state` omitted | refuse | `STATE_REQUIRED` |

## Not run, and why

- **No production unit, no restart of Fleet/CloudCLI/tunnel/Tailscale.** Out of
  scope for this lane and not authorised.
- **No `serverosd`-versus-Core ownership split test.** That partition does not
  exist yet; the lease only serialises two Cores.
- **No facts *writer*.** Nothing in this change produces the document; the
  production path therefore remains `NOT_WIRED` until a writer exists.

## Rollback

The change is additive behind a flag that did not previously exist. Reverting the
commit restores the refusal verbatim; no state, unit or schema is migrated.
