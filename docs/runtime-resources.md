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

## Related

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — invariants 6, 7 and 8
- [`../runtime/managed-runtime-contract.yaml`](../runtime/managed-runtime-contract.yaml) — kinds and requirements
- [`ability-fabric.md`](ability-fabric.md) — how the above becomes a status and a reason
