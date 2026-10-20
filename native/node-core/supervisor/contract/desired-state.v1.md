# `serveros/desired-state.v1` — the manifest the Native Core obeys

One JSON document, written by a person (or by a tool a person runs), read once
per pass by the Native Core. It says which of **this user's** systemd --user
services should be up, what may be done about them, and how often.

It is a **versioned contract**: `schema` must be exactly
`serveros/desired-state.v1`. A document under any other id is refused, not
best-effort parsed — a shape read optimistically is a shape nobody can change.

## Why the reader is strict

Every leniency in this format becomes an authorisation nobody signed off on.
The reader therefore refuses, by name, rather than defaulting:

| it refuses | because |
|---|---|
| an unknown field | a misspelled `start_on_boot` silently defaults to false, the service never comes back after a reboot, and nobody finds out until the reboot |
| a wrong type | `"observe_only": "false"` is a truthy string, and coercing it turns the safety default **off** |
| a bad unit id | the id goes into an argv; the rule is an allowlist of shapes, so a traversal, an absolute path and a shell metacharacter are unrepresentable rather than filtered |
| a duplicate service id | the dependency graph is keyed by it, so one of the two would be unreachable |
| an unknown dependency | a dangling edge is something the planner would have to guess about |
| a word outside a closed vocabulary | an unrecognised `desired` must not fall through to a default |

Strictness is also applied to the JSON itself: a duplicate key, a leading zero
(`01`), an unescaped control character and nesting deeper than 32 levels are all
refusals.

## The document

```jsonc
{
  "schema": "serveros/desired-state.v1",   // required, exact
  "node": "device:tokyo-shared-dev-01",    // required; which device this is for
  "principal": "vince",                    // required; the OS user it speaks for
  "observe_only": true,                    // optional, DEFAULT true
  "generation": 7,                         // required, monotonic, >= 0
  "services": [ /* … */ ]                  // required, may be empty
}
```

### `observe_only`

`true` means the Core plans, reports and asks systemd for **nothing**. It is the
default, so a manifest that forgets the field is safe, and turning it off has to
be written down deliberately. This is the same discipline as
`native/node-core/adapter/native_node_core.py`'s `collect(enabled=True)`: a
caller that has not decided to act cannot act by omission.

### `generation`

A monotonic integer. The Core acts on a generation and records it in every
receipt, so "acted on generation 7" is checkable. A manifest restored from a
backup carries an old generation, and a supervisor that ignored the counter
would re-authorise yesterday's plan as today's.

### A service

```jsonc
{
  "id": "fleet-node",                       // required, unique, the graph's key
  "owner": "vince",                         // required, MUST equal `principal`
  "unit": "serveros-fleet-node.service",    // required, exact systemd --user id
  "desired": "running",                     // required: running | manual | disabled
  "start_on_boot": true,                    // optional, DEFAULT false
  "depends_on": ["tunnel"],                 // optional; ids, not unit names
  "health":    { "probe": "systemd-is-active", "stale_after_seconds": 120 },
  "recovery":  { /* see below */ }
}
```

**`owner`** must equal the document's `principal`. A service naming another
account is **refused, not filtered** — a Core that quietly dropped it would
leave a reader believing it had been considered. This is what makes one Core per
user a real boundary rather than a convention.

**`unit`** is validated by `valid_unit_id`: a non-empty stem, the literal suffix
`.service`, and characters drawn only from alphanumerics and `_ . @ : -`. That
last clause is the whole rule — with it, `a.service --user`, `$(id).service`,
`../../etc/passwd.service` and `a b.service` have no representation at all. The
same function is called by the planner and by the provider, so the two cannot
drift about what a unit id is.

**`desired`** is one of three words and there are no others:

| | meaning |
|---|---|
| `running` | meant to be up; the Core may ask systemd to start it, under `recovery` |
| `manual` | **a person owns the lifecycle.** Observed and reported, never acted on — a deliberate stop and a crash read identically from outside, so the Core does not guess |
| `disabled` | never started by the Core, and not expected to be up |

**`depends_on`** names service **ids**, not units: a unit may be renamed while
the service it provides is unchanged, and a graph written against unit names
would have to be rewritten for that. A cycle refuses the whole plan, because no
order exists in which to bring the members up.

**`health`** — v1 implements exactly one probe, `systemd-is-active`, and it is
the default. `stale_after_seconds` is how old a reading may be before it stops
being evidence: a **stale reading is not a failure**, it is the absence of one,
so it never triggers a start or a restart. Naming a different probe is refused
rather than silently replaced by this one.

**`recovery`** — the §19 budget, per service. The module constants in
`runtime/daemon.py` (3 attempts per 3600 s, a 60/300/900 s ladder) are the
defaults a manifest **inherits**, not values it overrides.

```jsonc
"recovery": {
  "max_attempts": 3,               // >= 1, within the window
  "window_seconds": 3600,          // >= 1
  "backoff_seconds": [60, 300, 900],  // non-empty, all >= 1, non-decreasing
  "circuit": { "consecutive_failures": 3, "cooldown_seconds": 1800 },
  "allowed_verbs": ["start", "restart"]   // ⊆ {start, restart, stop}; MAY be empty
}
```

`backoff_seconds` must be **non-decreasing**: a ladder that goes down retries
sooner after each failure, which is a storm rather than a backoff.

`allowed_verbs` **may be empty**, and that is not the same as omitting it: `[]`
says "the Core may do nothing to this service", which is the honest way to write
an observed-but-unmanaged entry. A verb outside the list is refused by the Core
even when the service needs it — recovery is not a licence to do anything.

## What the manifest can never cause

- **A start on an unknown.** A unit that was not read, or was read as `unknown`,
  has no verb. "I could not look" rendered as "it is down" is the restart loop
  this product exists to avoid.
- **A restart on stale evidence.** See `health` above.
- **An action on another account's unit.** See `owner` above.
- **A unit the caller did not allowlist.** The planner consults the list and the
  provider consults it again, by exact id, never by pattern.
- **A verb on the Core's own unit.** Its lifecycle belongs to systemd; a Core
  that could restart itself could restart itself in a loop.
- **Anything at all, while `observe_only` is on** — the pass still plans, and
  reports every action it decided not to take.

## The digest

Every receipt carries the SHA-256 of the manifest's bytes **exactly as read**,
so `sha256sum desired-state.json` reproduces it. The Core holds **no signing
key**: this proves which bytes were used and never who wrote them. Signature
verification needs a key path this version does not have.
