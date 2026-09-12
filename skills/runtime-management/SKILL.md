---
name: runtime-management
description: Use when work involves a long-running resource on this node — a container, a user service, an MCP server, a transport endpoint, a test runtime — and you need to know whether it is healthy, who owns it, whether it may be restarted or reclaimed, or where its state lives. Decides what is safe to touch; the primitives themselves live in serverosd and serverosctl.
---

# runtime-management — managed runtimes on one node

This Skill is **procedure**. It says what to ask, in what order, and when to
stop and ask a person. It contains no probe, no classifier and no restart
logic: those live in `serverosd` / `serverosctl`, and a rule that exists only
in a Skill is a rule no other consumer will honour.

## 1. The one distinction this Skill exists for

```text
process is running   ≠   the resource works
TCP is connected     ≠   the resource works
container is Up      ≠   the resource works
```

Measured on this host: an endpoint whose process was up and whose socket was
connected for 54 minutes while it could not do its job, restarted five times
in two hours, each restart logging a clean exit code. Every one of those
restarts was read as a fix because nothing had asked the only question that
mattered.

So there are four axes, and they are asked separately:

| axis | question |
|---|---|
| `liveness` | is the process there, and is that the state we want? |
| `transport` | is the channel it depends on carrying anything? |
| `functional` | **does it actually provide the capability it declares?** |
| `readiness` | is it in the state its desired state calls for? |

`HEALTHY` requires `functional: OK`. Anything else is `DEGRADED`,
`FUNCTIONALLY_FAILED`, `NOT_RUNNING`, `NOT_READY`, `STOPPED`, `UNKNOWN` or
`UNAVAILABLE` — and `UNKNOWN` is not a soft `HEALTHY`. **Not having asked is
not the same as having found it fine.**

## 2. Ask, don't compose

```bash
serverosctl runtime list
serverosctl runtime status <id>        # four axes + what may be done
serverosctl runtime explain <id>       # why it is or is not automatable
serverosctl runtime hygiene            # what is stale, protected, or unknown
serverosctl probe <id>                 # run the declared probes and show all of them
```

Or through the node surface, which forwards to the same daemon:
`runtime.status`, `runtime.explain`, `resource.hygiene`.

Two things you must not do:

- **Do not decide health yourself** by running `docker ps`, `pgrep` or
  `ss` and interpreting the output. That produces a second opinion with no
  provenance, and the two will disagree at the worst moment.
- **Do not treat a missing answer as a good one.** If `serverosd` is not
  running, the surface says so by name. An empty list from a surface that
  could not ask is the exact failure this architecture was built to prevent.

## 3. Ownership before action

Before touching anything, resolve **who owns it**, and remember that a
container label is not an owner. On this host eight containers across three
compose projects shared one `com.docker.compose.project.working_dir`; reading
that as ownership put a protected parallel workstream into a reclaim bucket.

Ownership comes from exactly two places:

1. a registry entry the node knows about, or
2. an explicit `serveros.owner=<type>:<id>` label.

Everything else is `UNKNOWN_OWNER`. **Unknown owner is `DEFER`** — not
"probably fine to stop".

## 4. Protection is absolute

A protected resource is never a hygiene candidate, is never reconciled, and
never gets restarted automatically. The check runs **first**, before
registration, before lifecycle, before activity. A protected resource that is
down is reported and routed to a person; it is not repaired. Name matching
works in **one direction only**: a hit means *leave it alone*, never *this is
yours*.

Currently protected on this node and not to be touched:
`device:server-beijing`, `work-item:LIT-0103`, `work-item:LIT-0104`.

## 5. Lifecycle decides what "stale" can mean

| class | what it means for reclamation |
|---|---|
| `persistent` | never stale by age |
| `task-scoped` | stale only once its work item is closed — and checked, not assumed |
| `session-scoped` | ends with the session that made it |
| `disposable` | reclaimable, and only through admission |

Age alone is not evidence. A container that has existed for three weeks is not
stale; a container whose work item closed last Tuesday is.

## 6. Risk tiers — what may be done without a person

| tier | default |
|---|---|
| `STOP` | allowed for a stale disposable, after policy |
| `REMOVE_CONTAINER` | stricter than stop; needs more than staleness |
| `REMOVE_IMAGE` | not without approval |
| `REMOVE_VOLUME` | **approval** — a volume is not recoverable by restarting anything |
| `PRUNE` | **denied by default**, no override |

`docker system prune` and `docker volume prune` are never run here.

## 7. Authorization faults are not process faults

When a resource fails because a session, device code or credential was
consumed, **restarting is the wrong response and makes it worse**: each
restart issues a new code nobody can authorize, and each returns exit 0. The
observation is:

```text
functional   FAILED
reason       AUTH_REAUTH_REQUIRED          (a measurement, authoritative)
  — or —
diagnostic   AUTH_FAILURE_SUSPECTED        (a log line, NOT authoritative)
action       NEEDS_HUMAN
```

The second form is explicitly weaker. It routes to a person and may never set
a state. When you see `authoritative: false`, you are acting on a hypothesis;
`runtime.status` says which one you have. **Do not loop on re-authentication**
— report and stop.

## 8. When to DEFER

- owner unknown, or named only by a compose label
- a reference is protected, or matches an active work item
- the observation is `UNKNOWN` — nobody has asked the question that matters
- the fault reason is an authorization, not a process
- a path or resource is claimed by a parallel lane, including anything under
  `state/litellm-image-build` or `workspace/litellm-build-evidence`

`DEFER` is a result. It is not a failure to reach a conclusion.

## 9. Boundaries

- **Resource lifecycle is this Skill. Repository, worktree and test-env
  lifecycle is `workspace-management`.** A container is a runtime; a git
  worktree is not. Do not delete a worktree because its container looks stale.
- **Policy** — what may be reclaimed and by whom — is ServerOS policy, not a
  judgement call made here.
- Every observation needs provenance: `observed_by {type, id}`, `observed_at`,
  `evidence {type}`. A state without those is not an observation.
- Never write a credential value into a registry, an observation, a report or
  a log. Only the reference and the fact that authorization is required.
