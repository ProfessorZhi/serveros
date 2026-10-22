# `serveros/native-core-facts.v1` — the facts a resident Core re-reads

`bounded-recovery.v1.md` §6 states that link and agent facts are supplied by the
caller as one-shot flags, and that reading them from the host is a separate
change. A resident Core that acts is that change, and this is the document it
reads instead.

## 1. Why a document rather than more flags

A flag is a statement about the instant the process started. A loop that reuses
it is not observing — it is hard-coding `LINK_UP` and `AGENT_IDLE` for as long as
it runs, and every safeguard downstream (the budget, the breaker, the admission
gate) is then reasoning about a world that stopped existing minutes ago.

So the resident acting form takes `--facts FILE` and re-reads it on every tick.

## 2. Shape

```json
{
  "schema": "serveros/native-core-facts.v1",
  "observed_at": "2026-10-22T00:03:00+08:00",
  "observed_epoch": 1792598580,
  "link":  {"known": true, "up": true, "reason": ""},
  "agent": {"known": true, "busy": false, "holder": "cc-session"},
  "services": [
    {"id": "toy", "runtime": "serving", "maintenance": false},
    {"id": "other", "runtime": "needs-login"},
    {"id": "third", "runtime": "unknown", "maintenance": true,
     "maintenance_reason": "window"}
  ]
}
```

| field | type | meaning |
|---|---|---|
| `schema` | string | must equal `serveros/native-core-facts.v1` |
| `observed_at` | string | when the writer read these facts; for the receipt |
| `observed_epoch` | integer ≥ 0 | the same instant in epoch seconds — **the field staleness is decided by** |
| `link.known` / `link.up` | bool | whether the host has a working path the service needs |
| `link.reason` | string ≤ 200 B | why, when `up` is false |
| `agent.known` / `agent.busy` | bool | whether a person or agent is using the service now |
| `agent.holder` | string ≤ 200 B | who, when `busy` is true |
| `services[].id` | string ≤ 200 B | the manifest's service id |
| `services[].runtime` | `serving` \| `needs-login` \| `unknown` | the service's own runtime |
| `services[].maintenance` | bool | a person placed it in maintenance |
| `services[].maintenance_reason` | string ≤ 200 B | for the refusal text |

Two fields for one instant is deliberate. The epoch is the load-bearing one
because it is the only value this Core can compare against its own clock without
a date parser of its own; the string is what a person reading the receipt sees.
The epoch is not derived from the string.

## 3. Trust is whole-document

Any of the following makes the **entire** document unusable, and unusable means
`link.known = false`, `agent.known = false`, no service facts, and a named code:

| code | when |
|---|---|
| `FACTS_ABSENT` | the file could not be read at all |
| `FACTS_MALFORMED` | not JSON, not an object, an unknown field, a wrong type, a duplicate service id, an unknown `runtime` word, text over its bound |
| `FACTS_SCHEMA` | the `schema` field names something else |
| `FACTS_STALE` | `now - observed_epoch > --facts-max-age-seconds` (default 90) |
| `FACTS_FROM_THE_FUTURE` | `observed_epoch` is more than 5 s ahead of this Core's clock |

There is no partial trust. A fresh link fact beside a stale agent fact is a fact
set nobody should act on, and choosing which half to believe is a rule nobody
wrote down.

Two contradictions are refused rather than read permissively:

- `link.known == false` with `link.up == true` — "I did not look, and it is up".
- `agent.known == false` with `agent.busy == true` — the same shape.

An unknown field is refused, not ignored: a field nobody reads is a rule nobody
applied.

## 4. What consumes it

- `link` and `agent` are handed to the existing `admit()`, which refuses on
  unknown exactly as before.
- `services[].runtime` becomes the `ServiceRuntime` for that service; absence of
  an entry leaves it unknown, which is **not** the same as `serving`.
- `services[].maintenance` is a refusal (`MAINTENANCE`) and is checked before
  the lease and provider are consulted.

## 5. What this contract does not say

- **Who writes the document.** Not specified here, and not implemented in this
  change. Until a writer exists, a resident acting Core has no usable facts and
  therefore does not act — which is the intended failure, not a gap to paper over
  with a default.
- **How the writer learns `link` and `agent`.** That is the writer's problem, and
  inventing the answer here would put the permissive default back in the reading
  path.
- **Any widening of authority.** The manifest's `allowed_verbs` for that exact
  unit remains the only statement of what may be done, and this document cannot
  add a verb.
