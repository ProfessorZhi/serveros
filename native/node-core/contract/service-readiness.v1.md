# Service-level readiness — `serveros/service-readiness.v1`

The closed probe vocabulary, the readiness rule built on it, and the two
admission facts that decide whether a recovery may be attempted at all. This is
the V1 bounded self-healing slice: the loop acts, and what it acts on is a
SERVICE that has answered — not a unit that systemd calls `active`.

It stacks on `contract/bounded-recovery.v1.md`, which owns the admission gate,
the refusal precedence and the single-writer lease. Nothing there is
re-implemented here; this document adds to it and says where.

## 1. The gap this closes

`HealthPolicy.probe` had exactly one legal value, `systemd-is-active`, and
`readiness_of()` returned `READY` if and only if `ActiveState` was `active`. That
is a statement about a **unit**. It is not a statement about a service.

A tunnel whose user daemon has exited while its launcher still lives is
`active`. A listener that never bound is `active`. A connector wedged
mid-handshake is `active`. In each case systemd is right about the process it
started and the service is not serving. A Core that counts a recovery as
successful on that word alone clears the circuit breaker on a service that is
still down — the exact failure the breaker exists to catch, arriving through the
one door the breaker could not see.

## 2. The probe vocabulary, closed

| `health.probe` | target | the answer |
|---|---|---|
| `systemd-is-active` | none | the unit's own word. The WEAKEST answer, and the **default** — a manifest that does not choose gets exactly the behaviour that existed before this document |
| `unix-socket-connect` | `health.probe_path`, absolute | `connect(2)` to that socket. Answering is serving |
| `tcp-connect` | `health.probe_host` + `health.probe_port` | `connect(2)` to a **loopback literal** (`127.0.0.1` or `::1`) and a port |

Refused, each by name and by the reader rather than at run time:

| refused | why |
|---|---|
| a probe word nobody defined | a capability nobody reviewed must not fall through to a default |
| a target beside `systemd-is-active` | a target with no probe is a field nobody read |
| a target of the wrong kind (a path for `tcp-connect`, a host for `unix-socket-connect`) | the two are different probes and a spec cannot be half of each |
| a relative `probe_path` | it would resolve against whatever directory the Core happened to start in, which is not a fact about the service |
| a `probe_path` longer than 107 bytes | `sockaddr_un.sun_path` is 108 with its terminator; a longer path could never be a socket this host binds |
| a non-loopback `probe_host`, including `localhost` | a name is resolved by whatever resolver is configured, and a Core that will connect anywhere is a scanner. Only the two literals are accepted |

The reader and the executor call the **same** `validate_probe`, so a spec the
reader accepted cannot be one the executor refuses — the drift rule
`valid_unit_id` already sets for unit ids.

The reader does not run the probe. It validates a spec; `probe_check` is not a
build-time step and nothing is connected at parse time.

### What the transport is, and is not

`ProbeTransport` has two methods, `connect_unix` and `connect_tcp`, and the real
one is a non-blocking `connect(2)` driven by `poll(2)`. The timeout is a
**constant** (`kProbeTimeoutMillis`, 1000 ms) and not a manifest field: a service
that could set its own bound could set an unbounded one and wedge the Core
inside a probe. No program is run, there is no argv anywhere in this module, and
the outcome carries no output to parse — the connection is the whole answer.

It is an interface so a test can **state** an outcome a host will not produce on
demand (a connect that times out). The tests that *can* use the machine do: a
real unix socket, a real loopback listener, a bound-but-not-listening socket, a
path that is not there, and a closed port.

## 3. The readiness rule

`readiness_from_probe(spec, unit_reading, outcome)`:

1. If the unit reading is measured and says the unit **cannot be running** —
   `failed`, `inactive`, `deactivating` — the answer is `NOT_READY`, whatever an
   endpoint answers. A stale listener can outlive the service it belonged to, so
   a successful connect is not allowed to overrule systemd saying the unit is
   gone. This is the one direction in which the unit word outranks the probe.
2. Otherwise, for `systemd-is-active`, the answer is `readiness_of(unit_reading)`
   — the unit's word, unchanged. The default path is bit-for-bit the old path.
3. Otherwise the probe is the authority: `READY` if it connected, `NOT_READY` if
   it did not, and **`UNMEASURED` if it could not be attempted**. An unmeasured
   probe is not a verdict: "we could not look" must not read as either ready or
   not ready, and must not spend or clear a budget.

`Active::Activating` with a service-level probe that connects is `READY`,
because a service whose own endpoint answers is serving regardless of what
systemd calls the unit. `Active::Active` with a service-level probe that refuses
is `NOT_READY` — **the case this module exists for.**

## 4. Two facts the gate gained

### 4.1 `NEEDS_LOGIN`

Read from the **service's** own runtime, not the unit's. A tunnel can be `active`
as a unit and report `NeedsLogin` as a backend: running and not serving, because
an authorisation expired. A restart does not fix it — it comes back needing
login again — so acting spends the budget on a repair that cannot work and hides
an authorisation a person has to renew.

**This is the one fact whose absence is not a refusal.** Install, link and agent
each turn a correct plan into a wrong action by being *unknown*; a runtime fact
does not, because the plan's own readiness probe already decides whether the
service is serving. A manifest that declares a service-level probe is the one
with a runtime fact to report; a manifest that declares none has nothing to say
here and is not refused for it. The caller supplies the fact today
(`--service-runtime`), the same way it supplies the link and the agent state.

### 4.2 Offline local recovery

The link gate refuses to act while the link is down, because a restart spent on
a network fault outlives the fault. That rule is right for a service that needs
the network to serve. It is **wrong for a crashed local tunnel**: the thing that
failed is the tunnel's own process, restarting it is a local act, and refusing
on the link would leave exactly the broken thing this Core exists to fix
unrestarted — waiting on the network, which is the one thing that is down.

So the gate is **per-service**. A manifest that names a crashable LOCAL service
says `recovery.offline_recoverable: true` and only that service skips the link
gate. It is deliberately not a global switch, and it defaults **off**: an
unstated fact is not a licence to act offline, and a fleet node or a connector
that reaches the network to serve must not be restarted offline. A written-down
per-service opt-in is the only way to tell the two apart.

`link_dependent` is the admission-side name and defaults **true** — the safe
reading, and the behaviour every existing manifest already gets.

### 4.3 The refusal order, extended

`bounded-recovery.v1.md` §2 fixes the order because *which* refusal comes back
decides what a reader is told to fix. `NEEDS_LOGIN` is inserted, and the link
checks are now conditional:

| # | code | the fact |
|---|---|---|
| 1 | `NO_LEASE` | another writer holds the pen |
| 2 | `MASKED` | the unit is a mask |
| 3 | `INSTALL_STATE_UNKNOWN` | the install state was not read |
| 4 | `NOT_INSTALLED` | `LoadState` says there is no unit |
| 5 | **`NEEDS_LOGIN`** | the **service** says it needs a person |
| 6 | `LINK_DOWN` | **when `link_dependent`** — no working link, or not established |
| 7 | `AGENT_STATE_UNKNOWN` | nobody established whether an agent is using it |
| 8 | `AGENT_BUSY` | an agent is mid-task |
| — | (unread observation) | the reading the plan was built from was not a measurement |

`NEEDS_LOGIN` sits after the install state because a service that is not there
cannot be authenticated, and before the link because the repair is a person and
not a route: with no link **and** a login needed, the reader must be told to
authenticate, not sent to look at a route.

## 5. What the loop does with readiness

After a `start` or a `restart`, the Core **reprobes** the service's declared
signal and only `READY` counts as a recovery. The budget and the breaker are
informed by that, not by the provider's exit status: a verb that returned success
without bringing the service back spends an attempt and counts toward the
breaker, instead of clearing it.

An `UNMEASURED` reprobe is **neither** a failure **nor** a success: the attempt
stands (it was made), but the consecutive-failure count and the breaker are
untouched. Only `NOT_READY` advances the breaker, and only `READY` clears it.
Treating "we could not look" as a failure is how a transient inability to probe
trips a breaker on a service that is fine.

The **receipt** still records the provider's own answer. `ExecutionResult` is
documented as the provider's word, and the receipt schema
(`serveros/native-core-receipt.v1`) is a versioned cross-language contract;
whether the *service* recovered is a policy decision and lives in the budget. A
post-action readiness field in the receipt is a schema change and belongs with
that contract's own review, not here.

## 6. What is not here

- **Nothing installed, enabled, started or lingered by this change.** The
  `systemd --user` unit template is unchanged and still default-disabled;
  `runtime/daemon.py` remains the only production writer on this node.
- **No second planner, no second registry, no new service schema.** The planner,
  the recovery engines, the lease and the receipt are reused as they are.
- **No probe provider for arbitrary endpoints.** Three closed probes, and no way
  to express a fourth from a manifest.
- **`NEEDS_LOGIN` is caller-supplied**, like the link and agent facts. Reading a
  tunnel's backend state automatically is a separate provider change.
- **No Windows equivalent.** `connect(2)` and `systemd --user` are Linux; the
  pure planner, the probe *rule* and the profile contract are what is shared.
