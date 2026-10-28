# Wave 8 · native model admission, and what counts as evidence

`model_admission.py` is a pure decision surface: given what a runtime was
*observed* to accept, may a caller name a model id when it asks for a session —
and if not, what may it say instead?

It calls nothing. No provider, no device, no credential, no network. Every
function takes values a caller already holds and returns a decision plus the
warrant for it.

## The finding that shapes everything else: this release publishes no catalogue

CloudCLI **1.41.0**, captured in #458
(`integrations/fleet-l2-control/EVIDENCE-1.41.0.json`,
`tools_list_sha256 24e0bef7ce297e0c8a65fa6b7dc22c54c6ec12eb975339d4d31728461da21bbc`):

| fact | evidence |
|---|---|
| `session_create` declares `model` | `types.model == "string"` |
| …and enumerates nothing | no shape in the capture carries an `enum` |
| `session_create` requires only `project` | `required == ["project"]` |
| `session_reconfigure` declares `model` | `types.model == "string"`, again unenumerated |
| no verb lists models or agents | the 31 declared verbs contain neither word |
| no roundtrip ever passed `model` | `create_with_message_*` arguments are `correlationId, lifecycleMode, message, permissionMode, project, provider` |

A `string` field is an invitation to send *anything*; it is the opposite of an
announcement of what exists. So on this release an explicit model argument can
never be justified by provider evidence. The capability that would justify one —
an enumerating verb — **does not exist**. This directory therefore reports the
*enumeration* half of the goal as **BLOCKED**, not as complete. See
[Status](#status) below.

## Five classes, because "unknown" must not be spelled "available"

| class | what it means | may it become an explicit model argument? |
|---|---|---|
| `PROVIDER_ADVERTISED` | the release's own capture enumerates this id for this device and runtime | **yes** — the only class that may |
| `SCHEMA_ONLY` | a `model` field exists and takes a string; no enumeration | no |
| `POLICY_ALLOWED_ONLY` | an operator grant names the id — a ceiling on requests, not a catalogue | no |
| `STALE` | an observation exists but its release pin or its age no longer supports it | no |
| `UNKNOWN` | nothing at all | no |

`Choice` reports two of these at once, because they answer different questions
and collapsing them is exactly how "permitted" turns into "available":

*   `evidence_class` — what the *device* published about the id;
*   `support` — the strongest single thing warranting it overall.

For a grant-permitted id under a schema-only release the pair is
`evidence_class="SCHEMA_ONLY"`, `support="POLICY_ALLOWED_ONLY"`: the device
accepts a string, the operator allows this string, and neither is a catalogue.

## Decisions

`choose_model(...)` returns `EXPLICIT`, `PROVIDER_DEFAULT` or `REFUSED`:

| request | evidence for the id | `require_explicit` | decision | `model` | code |
|---|---|---|---|---|---|
| not in the grant | not read at all | any | `REFUSED` | `None` | `MODEL_POLICY_DENIED` |
| absent, but required | any | `True` | `REFUSED` | `None` | `MODEL_NOT_NAMED` |
| named | `PROVIDER_ADVERTISED` | any | `EXPLICIT` | the id | `MODEL_ADVERTISED` |
| named | `SCHEMA_ONLY` | `False` | `PROVIDER_DEFAULT` | `None` | `MODEL_NOT_VERIFIED_DEFAULT_USED` |
| named | `SCHEMA_ONLY` | `True` | `REFUSED` | `None` | `MODEL_NOT_VERIFIED` |
| named | `STALE` | `False` | `PROVIDER_DEFAULT` | `None` | `MODEL_EVIDENCE_STALE_DEFAULT_USED` |
| named | `STALE` | `True` | `REFUSED` | `None` | `MODEL_EVIDENCE_STALE` |
| named | `UNKNOWN` | `False` | `PROVIDER_DEFAULT` | `None` | `MODEL_UNKNOWN_DEFAULT_USED` |
| named | `UNKNOWN` | `True` | `REFUSED` | `None` | `MODEL_UNKNOWN` |
| named | advertised on another device | any | `REFUSED` | `None` | `MODEL_DEVICE_MISMATCH` |
| named | advertised for another runtime | any | `REFUSED` | `None` | `MODEL_RUNTIME_MISMATCH` |

Two properties the table is built to guarantee:

*   **`model` is never a copy of a request that was not verified.** The only
    branch that puts a caller-supplied string into `model` is the one where the
    device enumerated it.
*   **A default decision says so.** `model_identity == "UNKNOWN"` whenever
    `model is None`, so a caller cannot mistake "a session was created" for "the
    model I asked for was used".

An empty grant allowlist means *the caller may name nothing*, never "anything" —
the same reading #407's `CreateGrant` documents at
`fleet_mcp/config.py:302`. `as_grant()` accepts either `ProjectGrant` or the
plain `{"models": [...], "permission_modes": [...]}` mapping that
`CreateGrant.as_dict()` already produces, so a caller cannot pass a near-miss
mapping and have it silently read as "no ceiling".

## Preflight, in the pinned order

`preflight_create(...)` answers before any provider is asked, and
`provider_call_required` is `False` for every refusal:

```
PROJECT_NOT_AUTHORIZED → CREATE_NOT_ALLOWED_FOR_DEVICE → PERMISSION_MODE_NOT_GRANTED
                       → MODEL_POLICY_DENIED → MODEL_ADMISSION
```

The project gate is first because a project id decides which working tree and
which credentials a minted session inherits. The ordering is not a comment: the
tests pass a mapping that raises on access, so a refusal that reads evidence
fails the suite rather than the promise.

## A rejected model is not a retry

`retry_decision(...)` refuses, in this order:

1. **substitution** — a retry that changes the model picks a backend the caller
   never chose (`MODEL_SUBSTITUTION_FORBIDDEN`);
2. **model rejection** — `MODEL_NOT_FOUND`, `UNKNOWN_MODEL`, `INVALID_MODEL`,
   `MODEL_UNAVAILABLE`, `UNSUPPORTED_MODEL` are terminal on a create
   (`MODEL_REJECTION_NOT_RETRYABLE`), because the repeat mints a second paid
   session to learn what the first already said;
3. **the provider's own `retryable: false`** is obeyed (`PROVIDER_SAYS_NOT_RETRYABLE`);
4. **a paid verb is never retried from here** (`PAID_SESSION_RETRY_FORBIDDEN`);
5. only then, `retryable: true` on something that minted nothing —
   `SESSION_BUSY` on a send is `retryable: true` in the capture, and a send that
   was refused produced no delivery — may repeat, up to `max_attempts`.

## Run, Delivery and Session are three sources

From the same capture: a create without a message answers with `sessionId`
alone; a create with one answers with `deliveryId`, `runId`, `sessionId`; a send
answers with `blockedBy`, `deliveryId`, `queued`, `queuedMessageUuid`, `runId`,
`source`. `dispatch_sources(...)` reads a payload into per-object sources
(`provider` / `absent` / `malformed`) and never fills one in from a sibling.

The device's own `source` label is carried verbatim and interpreted only for the
three identity words; if it names a kind whose identity is missing, that is
reported as a `disagreement` rather than believed. Its wider vocabulary is
**NOT_VERIFIED** — the capture records shapes, not values.

## Status

| item | state |
|---|---|
| evidence-aware model choice, preflight, retry, dispatch sources (pure) | **CODE + UNIT** — `tests/test_fleet_v1_native_model_admission.py` |
| a model enumeration to choose *from* on 1.41.0 | **BLOCKED** — no verb publishes one; `MODEL_SOURCES_1_41 == ()` |
| real device / client end-to-end | **NOT_RUN** — this directory intentionally calls nothing |
| CI | **PENDING** until the PR's own runs report |

"Blocked" is the honest reading of the goal, not a failure of this module: what
can be built without a catalogue is the *refusal* to invent one, and that is what
is here. The day a release ships an enumerating verb, `ModelEvidence.declared`
has a place to put the answer and `PROVIDER_ADVERTISED` becomes reachable without
changing a single decision rule.
