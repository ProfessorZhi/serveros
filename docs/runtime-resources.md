# Runtime Resources

The Node Runtime Plane answers: **what is running here, who owns it, and is it
healthy?** The registry, probes and attribution that answer it already exist.
This document covers the two things they were missing, both found by live
failures rather than by review.

```text
§1  lifecycle ownership   a resource may not be owned by the session that caused it
§2  durable declaration   a resource that must exist has to be declared somewhere durable
§3  floating dependencies a managed dependency that floats is refused, not tolerated
```

## 1. Lifecycle ownership

Every managed resource already carries a lifecycle class, and the classes were
already right:

```text
persistent       survives restarts; must exist across sessions
task-scoped      lives as long as the work Item
session-scoped   lives as long as the Agent session that created it
disposable       may be reclaimed at any time
```

The defect was never the vocabulary. It was that nothing asked **who the owner
of record is**, so a resource a task depended on could be registered with the
lifetime of the thing that happened to create it.

### The rule

> **Anything that must outlive the Agent session that caused it requires an
> explicit durable owner and an explicit lifecycle. An Agent session may cause
> creation; it may not be the owner of record.**

Owner types are closed, and none of them is a session:

```text
owner.type  ∈  { project, work-item, serveros, ability }
```

A registration whose only candidate owner is a session is either refused, or
admitted with `lifecycle.class = disposable` and a `note` recording the
downgrade. It is never admitted as `persistent` on a session's authority,
because a persistent resource owned by a session is destroyed exactly on
schedule and every observation up to that moment says it is fine.

### The failure this comes from

A tunnel required by an in-flight task disappeared while the far side was
healthy — both containers up, both ports listening, the health endpoint
answering. Nothing had crashed. Nothing was misconfigured. The tunnel had been
registered with session lifetime, so it was retired when an unrelated Agent
session ended, on time and by design.

The lesson is not "make tunnels persistent". It is that **the registration was
accepted without anyone being asked whether the session could be its owner**,
and the machine had no way to notice, because at every moment before the
retirement everything was true.

```text
what was observed     tunnel RUNNING, far side HEALTHY
what was true         the tunnel's lifetime was an unrelated session's
what was missing      a question at registration, not a check at runtime
```

### Registration, not monitoring

The rule is enforced at admission, because there is nothing to detect later.
Between creation and retirement the resource is genuinely healthy; a monitor
watching it will report success for the entire period that matters.

## 2. Durable declaration

A resource that must exist needs a durable statement somewhere that outlives
the Agent, the shell, and the boot:

```text
durable         fleet/desired-state declarations, a systemd unit, the registry
not durable     a shell profile, a comment, a chat message, a README paragraph
```

Desired state belongs in the registry because the registry has a reconciler
attached to it. A declaration in a shell profile has no reconciler, no owner and
no probe — nothing to compare against, so nothing that can ever be found wrong.

### The failure this comes from

The Linux node's outbound tunnel exists as `127.0.0.1:17890` in a comment in
`~/.bashrc`. Not in a disabled block, not behind a flag — a comment. There is no
listener on the port, no service owns it, and no file anywhere states that it
should exist. Every consumer that needs it either hardcodes the value or
inherits it from an environment that no longer produces one.

```text
observed    port 17890 has no listener
observed    the only description of it is a comment
observed    a wrapper script and .bashrc both named it
missing     any declaration that could be reconciled, probed, or found stale
```

A comment cannot be drifted from, because it never claimed anything. That is
what makes it worse than no declaration at all: a reader finds the number,
believes it, and the belief is not checkable.

**A resource with no declaration is `PROVIDER_NOT_INSTALLED`, not `UNKNOWN`.**
Saying "we are not sure whether this node has an outbound tunnel" would be
false comfort. It has no provider, and that is a fact.

## 3. Floating dependencies

A managed persistent runtime may not depend on a moving target.

```text
admits a floating dependency
  npx <package>@latest          resolves to something new on every start
  npx <package>                 without a version, same
  a floating branch             `main`, `HEAD`, or a tag that is expected to move
  install-on-start over a network
  a cache directory that is not pinned by content

is pinned
  an absolute path to a versioned artifact
  a lockfile with an integrity hash
  a revision recorded in the registry, alongside the resource
```

### The finding

```text
code       PERSISTENT_RUNTIME_FLOATING_DEPENDENCY
severity   WARN at admission · HIGH when the runtime is critical or protected
effect     blocking for registration and promotion of critical managed
           persistent runtimes; advisory elsewhere
```

The asymmetry is deliberate. A floating dependency on a scratch helper is a
smell; a floating dependency on the browser automation a safety-critical check
depends on is a different claim, because "it worked yesterday" and "it works
today" are then statements about two different programs.

### The failure this comes from

One node reached Playwright through `npx @playwright/mcp@latest`. It had been
working. The version it ran on any given day was decided by a registry, a
network, and a cache — none of which is recorded anywhere, and any of which can
change the answer without changing a single file on the machine.

The other node had already been pinned, to an absolute path to a versioned
artifact, in both runtime configs. The two nodes ran "the same" toolchain and
did not, and nothing in the catalogue could have told you that.

**The finding is not that `@latest` is bad practice.** It is that an unpinned
dependency makes a device's capability set unknown, which makes every answer the
Ability Fabric gives about that device provisional without saying so.

## 4. Resource observation

The plane had to answer one question before any of the others could be built:

> **Can ServerOS trust what it sees?**

Two axes, and they are deliberately separate because they disagree in
practice:

```text
epistemic   how the value was obtained       FACT · INFERRED · UNKNOWN
confidence  how strong the source is         AUTHORITATIVE · INFERRED · UNKNOWN
```

A container's binding read straight out of `docker inspect` is a `FACT` from an
`AUTHORITATIVE` source. A port claimed by a process that merely looks like it
owns it is `INFERRED`. Neither axis collapses into the other, and
`runtime/attribution-contract.yaml:104-131` keeps them apart — including the
case that tempted a merge: `UNKNOWN` must not be reported as a blank
(*"UNKNOWN would report a finding as a blank"*), and it is not `AUTHORITATIVE`
either.

### Coverage is reported, not implied

`runtime/resource_anomaly.py` grades fifteen codes and reports them in **three
separate buckets**:

```text
codes_graded          graded on this call
codes_emitted         actually fired
codes_not_evaluated   NOT graded, each with the input it was missing
```

On the live host that reads `{"graded": 10, "of": 15}` rather than implying the
other five were clean. This is the whole of invariant 11, and it exists because
it was violated **seven times in one module**: an unreadable container log
reported as a small log; a `docker system df` timeout reported as an empty build
cache; a comparison in which every row failed reported as *"every registration
is in its desired state"*. Every one was a **more confident answer** rather than
an error, and the test suite was green through all seven.

The rule as implemented has two clauses, and the second is not decoration:

> `DESIRED_ACTUAL_MISMATCH` is graded when the comparison **reached a verdict on
> at least one registration, or fired**. Otherwise it is ungraded, with the
> document's own reason. A comparison that compared nothing is not a node with
> no mismatches.

A fix that stopped grading the code altogether would satisfy the first clause
and introduce the opposite defect, making *compared and clean*
indistinguishable from *never compared*.

## 5. Ownership: attributed, never guessed

Ownership has exactly **two** sources:

```text
a registry registration
an explicit `serveros.owner=<type>:<id>` label
```

Nothing else. Not a container name, not a compose project, not a working
directory, not a process name. Each of those is a plausible signal and none of
them is evidence, which is why they are refused rather than weighed.

The consequence on a real host is uncomfortable and correct. Measured on this
node: **22 of 39 containers have no owner at all**, and their attribution
`provenance` is a container name — which is to say, a plausible reading and not
an owner. Every one of them is `DEFER`.

```text
DEFER    not a cleanup category
```

`DEFER` is what failing safe looks like: a resource nobody can attribute is a
question for a person, and a system that resolves it by inference has invented
the answer it was supposed to find.

## 6. Container health is a state, not a colour

`running` is not `healthy`, and the gap between them is where the interesting
failures live. What the code does today is more specific than either a green
light or a missing one:

```text
runtime/containers.py:295   Docker's raw value (`healthy|unhealthy|starting|none`)
                            is carried through verbatim as EVIDENCE
runtime/probes.py:465       ContainerHealthcheck maps it onto the four axes:
                              healthy    → functional OK
                              unhealthy  → functional FAILED (HEALTHCHECK_FAILING)
                              else       → functional UNKNOWN
                              not declared → NOT_APPLICABLE (image declares no HEALTHCHECK)
runtime/health.py           derives the state from the axes
```

which yields:

```text
healthy                    HEALTHY
unhealthy                  FUNCTIONALLY_FAILED
starting / restarting      UNKNOWN → DEGRADED
no HEALTHCHECK declared    DEGRADED
```

Two of the states a person would most want to distinguish have no
representation at all. **`RESTARTING` and `NO_HEALTHCHECK` are not among the
derived states**, and a container that is mid-restart and a container with no
healthcheck are both reported as `DEGRADED` — the same word, for a transient
condition and a permanent property.

The derived-state list that does exist is
`UNAVAILABLE · UNKNOWN · STOPPED · NOT_RUNNING · NOT_READY · FUNCTIONALLY_FAILED
· DEGRADED · HEALTHY`, over four axes:

```text
liveness    RUNNING · STOPPED · UNKNOWN · UNAVAILABLE
transport   CONNECTED · DISCONNECTED · NOT_APPLICABLE · UNKNOWN
functional  OK · FAILED · NOT_APPLICABLE · UNKNOWN
readiness   READY · NOT_READY · NOT_APPLICABLE · UNKNOWN
```

`NOT_APPLICABLE` on an axis is doing real work here: it is the difference
between "we checked and it is fine" and "this question does not apply", and a
design without it reports a container with no healthcheck as permanently
unhealthy.

## 7. Tunnels: two surfaces, neither giving the four-axis verdict

A tunnel is observed by two things, and they answer different questions.

```text
runtime/infrastructure.tunnel_inventory    an inventory and a PID join
runtime/view.tunnel_observation            an envelope over it
```

`tunnel_inventory` returns `declared[]`, `observed_forwarders[]`,
`undeclared_forwarders[]`, `blind_spots[]` and a `join_coverage` block. It
contains **no per-tunnel availability, latency, required/optional flag or
freshness**, and it is explicit about the epistemic status of what it does
return: a visible forwarder is *"a candidate and never an owner."*

`tunnel_observation`'s `state` can be **exactly two values**:

```text
"state": ("UNKNOWN" if inventory.get("blind_spots") else "COMPLETE")
```

**No `DEGRADED`. No `NOT_CONFIGURED`.** `BLIND_SPOTS` present means the join
could not be completed, so the answer is `UNKNOWN` — which is invariant 11
applied to a join, and is the correct answer rather than a `0`.

The four axes *are* reachable, but only when a tunnel is **registered as a
managed runtime of kind `tunnel`**. Then `managed-runtime-contract.yaml`'s
health axes apply, `DEGRADED` becomes reachable (`{liveness: RUNNING, transport:
DISCONNECTED}`, or `{functional: UNKNOWN}`), and registration is gated so that
a tunnel whose health could never be established either way is refused:

> *the registry cannot make a tunnel healthy. It can only refuse to accept a
> tunnel registration whose health could never be established either way.*

Two absences worth naming precisely, because they are the kind of gap that gets
mistaken for a bug in the reader:

```text
NOT_CONFIGURED   does not exist anywhere — not in the derived states, not in
                 any contract. A tunnel that is declared nowhere is not a
                 degraded tunnel; it is PROVIDER_NOT_INSTALLED (§2).
latency          ServerOS has NO latency axis. The only latency measurement in
                 the ecosystem is the ops monitor's `probe_proxy.ms` and
                 `history.jsonl.m_egress_ms`, which are not part of this plane.
freshness        exists only as `observed_at` / evidence ageing — never as a
                 tunnel verdict.
```

The ops monitor is where tunnel reporting is richest, and it is a **three-level
verdict rather than four axes**: `隧道1` is `required=True` and `隧道2` is
`required=False`, with `tunnel_down` (`CRIT` **only when required**) ·
`tunnel_slow` · `tunnel_cert` · `egress_broken`. Required-ness is the axis
ServerOS's own surface lacks, and it is the one that decides whether a person
is woken up.

## 8. What a person is shown

Everything below is about the *ops* layer (`ops/scripts/monitor/`, a systemd
user timer every 30 minutes), not the node runtime. It is included here because
a resource plane that reports only through a CLI has not finished: the last hop
is a person, and the last hop is where the defects below live.

```text
collect.py   capacity → JSON, no verdict        checks.py   probes, no verdict
monitor.py   thresholds · render · should_send  notify.sh   Feishu card
run.sh       orchestrator                       exit 0 quiet / 10 alert
```

### 8.1 Three fixed regions

A reading must say **whose** it is, and today that is a thing a reader has to
work out. The card should carry three regions and never merge them:

```text
HOST                the machine — MemAvailable, load5, disk by role
MANAGED-ATTRIBUTED  resources ServerOS can prove it owns, or that have an owner
UNATTRIBUTED        the rest — reported, and never acted on
```

The middle and bottom regions are not a ranking of importance. They are the
ownership split from §5, shown where a person can see it, because mixing them
is how "our share of the host" becomes a number nobody can defend. The
monitor's own five 口径 are the same idea applied to measurement:

```text
MemAvailable, not MemFree          Linux uses free memory for page cache;
                                   the healthy state looks alarming on MemFree
our column is not the denominator  we are ~11 % of memory and ~15 % of CPU, so
                                   "our share of host" can never fire
load5/load15, never instantaneous  container CPU measured 0.12 % → 70.75 % →
                                   pcpu                               0.12 % in minutes
two-point differential + pid       `ps -o pcpu` is a LIFETIME AVERAGE, and the
intersection                       pid set changes between samples
NetIO is cumulative, not a rate    and /proc/net/dev cannot be split per user,
                                   so our bandwidth share is reported unavailable
                                   rather than invented
```

That last line is invariant 11 once more, in the place where it is most tempting
to guess: **the one cell we cannot compute is left blank on the card.**

### 8.2 An alert is a state, not a verdict

**There is no alert state machine.** `ACKNOWLEDGED`, `SNOOZED`, `RESOLVED`,
`alert_ref`, `first_seen`, `hysteresis` and `cooldown` have **zero hits** across
both `ops/scripts` and this repository.

What exists:

```text
monitor.py evaluate()   a TRANSIENT list of {level, key, msg}, recomputed from
                        scratch every run — alerts have no identity across runs
run.sh                  an HOUR-scoped idempotency key (YYYYMMDDTH + a sha256 of
                        card+report): same hour, same bytes → not resent
history.jsonl           a METRIC ledger, not an alert ledger. Trend only.
```

So an alert cannot be acknowledged, cannot be snoozed, cannot be resolved, and a
condition that persists for six hours is six identical cards rather than one
condition. The intended lifecycle is the standard one, and the reason each
transition matters is a specific failure:

```text
OPEN → ACKNOWLEDGED   someone is on it; stop telling everyone
OPEN → SNOOZED        known, deliberately deferred until a time
any  → RESOLVED       and the recovery is announced — otherwise silence after
                      an alert is indistinguishable from a dead timer
```

plus **dedupe by identity** (one alert, one card), **hysteresis** (an alert that
fires at 85 % and clears at 84 % fires forever), and **cooldown**. Without
hysteresis a metric oscillating around a threshold produces an unbounded number
of notifications, which trains people to ignore all of them — and the failure
after that is a real alert nobody reads.

The closest thing to a real state machine in the ecosystem is
`ops/scripts/maintenance/botmux_watchdog.py`, and it is worth copying rather
than designing: a persisted `state ∈ ok|problem|restarted|giveup|skip`, an
explicit **recovery** transition (`RECOVERED — 上一次是 …, 现在恢复了`), a restart
ledger, `MIN_SECONDS_BETWEEN_RESTARTS = 120` and `MAX_RESTARTS_PER_HOUR = 3`.
It has no `alert_ref`, no ACK and no SNOOZE, and its "notification" is stdout
into the systemd journal — **not a person**. But the parts it does have are the
parts the monitor lacks.

### 8.3 The notification pipeline exists. The producers do not.

`runtime/notifications.py` and `runtime/notification-contract.yaml` are
complete, tested, and **have no callers**:

```text
9 closed events      session.* · turn.* · approval.required · device.unavailable
                     · provider.degraded · handoff.completed · run.terminated
severity per EVENT   not per call site, so two identical events cannot arrive
                     at different volumes from different code paths
templates authored   `author: serveros`, `rendering_owner: HumanControlProvider`
in ServerOS
deliver()            consults `human.notification.transport` FIRST and returns
                     `delivered: false` with the blocking state
```

What is missing is every producer:

```text
no import of notifications.py outside tests/; daemon.py never names it
the contract's `producers:` names six events that no code emits
no anomaly → event bridge: anomaly.py's 19 codes terminate at a PULL command
no notification policy anywhere
```

The contract naming six producers that do not exist is the `declared-but-absent`
pattern in its purest form, and it has a plain consequence: **unless a person
runs a command, nobody is told anything.** This is the highest
value-per-unit-effort entry on the roadmap, because the expensive half — the
vocabulary, the severity model, the capability-aware delivery — is already
built and correct.

## 9. A review candidate is not a cleanup candidate

Two different things share almost the same name, and conflating them is how a
warning becomes a queue:

```text
a REVIEW candidate    something a heuristic noticed and a person should look at
                      → the anomaly codes, with severity. Cheap to produce,
                        deliberately noisy, and they confer nothing.

a CLEANUP_CANDIDATE   a set of INDEPENDENT FACTS agree it may be reclaimed
                      → runtime/runtime_hygiene.py, and the class is LABEL_ONLY
```

The second is gated twice over. It requires **a minimum of two independent
facts**, where independence is *declared* rather than counted —
`_candidate_evidence` returns named facts and the class needs at least two from
different sources. `age_seconds` is deliberately **not** one of them:

> Age is the easiest thing to measure and the weakest thing to conclude from — a
> machine that has been off for two months and one that has been off since lunch
> are the same number of days apart and completely different in every way that
> matters.

A caller that supplies nothing but an age gets `UNKNOWN` with a reason saying
so, and `test_age_alone_never_makes_a_reclaim_candidate` is the whole test.

And the label confers nothing:

```text
LABEL_ONLY        = {CLEANUP_CANDIDATE}
NEVER_ACTIONABLE  = every other class
```

`CLEANUP_CANDIDATE` is **absent** from `NEVER_ACTIONABLE`, and that absence is
the point: every consumer reads it as a label. The word *candidate* names a
question for a person, not a queue, and the two contracts that take this
module's output as input (`resource-governance-contract.yaml`,
`actuator-contract.yaml`) both stop short of executing it. On the live host the
honest result is **zero** `CLEANUP_CANDIDATE` — 30 containers `DEFER`, 8
`PROTECTED`, and a code that can never fire must not rank above one that can.

## Related

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — invariants 6, 7, 8, 11, 12 and 13
- [`../runtime/managed-runtime-contract.yaml`](../runtime/managed-runtime-contract.yaml) — kinds and requirements
- [`../runtime/resource-governance-contract.yaml`](../runtime/resource-governance-contract.yaml) — why nothing acts
- [`ability-fabric.md`](ability-fabric.md) — how the above becomes a status and a reason
- [`human-control.md`](human-control.md) — where a notification goes, and what a card is
- [`fabric-v1-roadmap.md`](fabric-v1-roadmap.md) — health states, alerts, producers
