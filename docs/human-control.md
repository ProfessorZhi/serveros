# Human Control

The question: **how does a person reach this system, and how far can that reach
go before it becomes a lie?**

Human Control is the plane where ServerOS talks to a human and — more
importantly — where it decides what it is *allowed to claim* about that
conversation. It has one provider today
(`provider:botmux-human-control`, transport Feishu, `mcp/human_control.py`) and
its governing statement is §4's split, quoted at the top of that module:

```text
owns   transport ingress · human identity evidence · conversation context ·
       approval transport · notification transport · interactive rendering
not    policy · Agent Session truth · Fleet truth · runtime selection ·
       Ability resolution · business orchestration
```

**Everything there is transport.** Where the transport cannot do something, the
answer is `UNSUPPORTED` or `UNKNOWN` with a reason — never a private
implementation that reaches around the provider.

## The line: a channel is not a source of content

BotMux and the Feishu APIs are both "Feishu", and they are not the same thing.
Conflating them is the specific error this document exists to prevent:

```text
BotMux                a CONTROL channel
                      conversation identity, notification delivery,
                      session control, and (in principle) approval transport

Feishu API surfaces   CONTENT and DATA providers
                      Docs · Drive · Wiki · Minutes — each an ordinary provider
                      owned by the ability that manages that kind of object
```

They must not reach through each other. A BotMux message is not a document and
a document is not an approval; an ability that manages Feishu space does not
gain a notification path by being deployed next to a bot, and a notification
path does not gain the right to write into a document because a person is
listening on the other end.

This is also why the `feishu-report` and `feishu-space-management` abilities
are **not** part of Human Control. Reporting to a person and governing an
object are different jobs with different authorities, and the only thing they
share is that a cloud service happens to hold both.

## The six capabilities, and the two words that matter

`mcp/human_control.py:471-509`, measured on this device rather than assumed:

```text
human.conversation.context     SUPPORTED    /api/sessions reports chatId, chatType, sessionId
human.notification.transport   SUPPORTED    POST /api/trigger carries a ServerOS sentence
human.identity.verify          UNKNOWN      `botmux actor current --json` verifies only
                                            inside a BotMux turn; from a non-turn process
                                            it answers status=blocked, current_actor_unverified
human.transport.ingress        UNKNOWN      nothing delivers inbound content: /api/events and
                                            /api/messages answer 404 not_found_yet
human.approval.transport       UNSUPPORTED  no documented callback surface for a decision
human.interactive.rendering    UNSUPPORTED  BotMux renders its own cards; ServerOS authors none
```

**`UNKNOWN` and `UNSUPPORTED` are not two spellings of "no".** An `UNSUPPORTED`
transport *has no way* to do it — the surface does not exist. An `UNKNOWN` one
*might*, and this process cannot establish it from where it stands. They call
for opposite actions: `UNSUPPORTED` means design around it; `UNKNOWN` means go
and find out. Flattening them produces a system that stops asking.

Identity is `UNKNOWN` rather than `UNSUPPORTED` for a reason worth keeping: the
surface exists and is documented, and BotMux **correctly** declined to verify
the sender of *this* process, because this process is not inside a BotMux turn.
Whether it verifies inside one is not something the module claims to know, so
it does not claim it — and `Report.as_report()` has no field in which to put an
actor it did not get.

### The limitations are enforced, not documented

This is the part that makes the table a fact rather than a description.
`request_approval` does not return a soft value or a `None`:

```text
mcp/human_control.py:460   raise HumanControlUnsupported(...)
```

A caller that tries to obtain an approval through this provider gets an
exception naming why, at the call site, at the moment it tries — not a
capability table that a caller could forget to consult. The module's own words:
*"§6 says to mark it as one and not to improvise."*

Each entry also carries `measured_by`, pointing at the probes in the module
docstring, so the next reader can re-run them rather than trust them.

## What may flow, and what may not

Read the table above as a permission list, not a feature list:

```text
may flow      a notification into a conversation          (notification.transport)
may flow      which conversation, and what kind it is     (conversation.context)
may flow      reading the record back                     (sessions is read-only)

may NOT       approve a destructive action                (approval.transport UNSUPPORTED)
may NOT       treat a message as a verified instruction   (identity.verify UNKNOWN)
may NOT       receive inbound content as an event         (transport.ingress UNKNOWN)
may NOT       have ServerOS author a card UI              (interactive.rendering UNSUPPORTED)
```

Consequences, stated plainly because they are the whole point:

- **Anything irreversible needs a person, and a person is not reachable through
  an approval transport here.** Deletion, stopping, high-risk document writes,
  resource governance, cross-device destructive change: none of these may be
  gated on an approval this provider cannot carry. The ServerOS approval
  contract is implemented and tested independently of this transport; the
  transport is the missing half, and it is recorded as missing.
- **A notification is not an acknowledgment.** `notification.transport` being
  `SUPPORTED` means a sentence arrives. It does not mean anyone read it, and
  nothing in the system may treat delivery as consent.
- **The human on the other side is identified by conversation, not by
  verification.** For a notification that is fine. For anything with
  consequence it is not, and `human.identity.verify = UNKNOWN` is the reason.

The correct behaviour when a capability is missing is to fail by name and let a
person decide, which is why the exception is raised rather than worked around.

## The BotMux boundary

Four written statements, all saying the same thing:

```text
runtime/agent-session-contract.yaml:248   no provider-private state file is read when a
                                          supported provider API can answer the question
mcp/botmux_provider.py:4                  no ~/.botmux session files are read here
mcp/botmux_provider.py:373                no ~/.botmux/data/*, no tmux socket
mcp/human_control.py:12-15                reading a private state file to fill a gap is
                                          the failure mode, not the fallback
```

The provider reaches BotMux through its **documented external surfaces** — the
HTTP API, and the `botmux actor current --json` CLI — and reuses `/api/trigger`
as the way to put an instruction into a session rather than any internal send
path. `subprocess.run(["botmux", ...])` is the documented CLI, not a private
file; `mcp/codex_app_server_provider.py:286` takes `codex_home` from the
app-server's own result rather than from `~/.codex`.

**And it is enforced.** `tests/test_provider_readiness.py:555`,
`test_no_botmux_private_state_or_tmux_socket_is_read()`, reads
`botmux_provider.py` **as source** and asserts that none of `expanduser`,
`Path.home`, `subprocess`, `os.listdir`, `glob(` and friends appear, and that
`urlopen(` occurs exactly once. Its docstring states the design: *"Checked as
ACCESS, not as a substring."*

### Scope, stated precisely

The claim is **not** "nothing in this ecosystem reads BotMux private state." It
is:

```text
TRUE   ServerOS core reads no BotMux private state, and a test holds that.
FALSE  nothing here reads it.
       ops/scripts/maintenance/botmux_watchdog.py:70 reads
       ~/.botmux/fleet-state.json — an ops tool, outside ServerOS core, and
       deliberately: it is how the watchdog detects a fleet that is alive and
       not working.
```

An over-wide version of a true statement is the same defect as a declaration
with no reader, one level up: it cannot be checked, so it will be believed.
Boundary claims name their boundary.

## Notification: ServerOS writes the sentence, the provider delivers it

The split is frozen in `runtime/notification-contract.yaml`:

```text
author            serveros                   the claim is a ServerOS claim
rendering_owner   HumanControlProvider       the provider decides how, not what
```

A provider that composed these sentences would be **writing** the claim, and a
provider that rendered "turn failed" as "the agent is still working" would be
editing ServerOS truth at the last hop, where no ServerOS test would see it.

The vocabulary is closed — eleven events, and severity is a property of the
**event**, not of the site that raises it, so two identical events cannot
arrive at different volumes depending on which code path produced them:

```text
INFO       session.created · turn.completed · handoff.completed
ATTENTION  turn.failed · device.unavailable · provider.degraded · run.terminated
           alert.opened · alert.resolved
ACTION     session.waiting_input · approval.required
```

The last two are not a fourth kind of thing. An alert is an anomaly that
persisted past the point where repeating it is useful, and it is the only
member of this vocabulary with a **producer on this machine today** — see
below.

`deliver()` consults `human.notification.transport` **before** delivering and
returns `delivered: false` with the blocking state, rather than reporting a
send that did not happen. `events_for(reason)` bridges the closed reason
vocabulary owned by `abilities/target.py` — never a raw provider string,
because a notification leaves this machine and a provider error body can carry
anything at all.

### The producers: one exists, six are named

The pipeline was complete and had **no callers**. One of the seven events now
has a producer, and it is the one the ledger said to build first, because it is
the only one behind which a **detector already existed**:

```text
runtime/anomaly.py ─┐
                    ├─→ runtime/alerting.py ─→ pending intent (on the row)
runtime/resource_anomaly.py ─┘                        │
                                                      │  serverosctl alert sync
                                                      │  (the only holder of a transport)
                                                      ↓
                          HumanControlProvider ─→ BotMux ─→ Feishu
```

The reasoning detector is already graded — 19 codes with severity, a
`codes_not_evaluated` list, and a level row per graded code that a recovery
margin is read from. What was missing was not the detection. It was the four
things a person notices when a detector is loud for a month and quiet for a
year: **an identity that survives a restart, a cooldown, a recovery, and a
memory of having said it.**

**The daemon decides the facts and cannot deliver.** `alert_pass()` evaluates
both detectors in one tick and writes an *intent* onto the alert row. It
deliberately holds no provider, and `serverosctl alert sync` is the only
command that does. This is the same rule `_r_agent_control_status` follows by
refusing to import a transport, for the same reason: a daemon that can send is
a daemon that cannot tell you the transport is broken, which is precisely when
its answer is wanted. §12 is enforced across that seam — an intent is not a
delivery, and only `record_delivery` moves `last_notified_at`.

```text
serverosctl alert list      every alert, most in need of attention first
serverosctl alert inspect   one alert, plus the sentence a person WOULD be sent
serverosctl alert ack       a person has seen it. NOT a resolution
serverosctl alert snooze    stop saying this for a window
serverosctl alert sync      evaluate, send what has to be said, record what happened
```

`serverosctl alert sync` is also the only surface the transport can lie to, so
it is the only one that writes `last_delivery`: `DELIVERED`, or `FAILED`,
or `UNSUPPORTED` when the provider declined before trying, or `UNKNOWN` when
neither could be established. **None of the four resolves an alert.**

The read half is on the node MCP surface as `alert.list` and `alert.inspect`,
which is where an Agent asks. The write half — `ack` and `snooze` — is
**deliberately not**: `fleet/mcp-contract.yaml` says that surface holds no
state and changes nothing, and `human.transport.ingress` is `UNKNOWN` while
`human.approval.transport` is `UNSUPPORTED`. A design that let a person
"reply ACK" in Feishu would produce alerts nobody could close and a
plausible-looking reading of messages that were never verified. Acknowledge in
the terminal, or acknowledge nowhere.

The six remaining events are `DECLARED_ABSENT` **in the contract, with the
reason and a disposition written on each** rather than in an exemption table:
`session.created` and `handoff.completed` wait on a session service that
persists nothing to raise them from, `session.waiting_input` and
`approval.required` are blocked on inbound and approval transports, and
`run.terminated` is `work-engine`'s transition rather than this repository's.
`tests/test_declared_implementations.py` fails if an entry claims a producer
that does not resolve, and fails again if an entry that *has* one keeps its
`DECLARED_ABSENT` — so the list can only shrink by something actually being
built.

Nothing in this path acts on the world. The recommendation it carries is one
of `REVIEW` / `MONITOR` / `GOVERNANCE_REQUIRED`, and **an alert is not an
authorization**.

## What is not human control

```text
not human control   an approval rule                → policy, decided by ServerOS
not human control   who a person is, in general     → only "can this transport show it"
not human control   a Feishu document               → that object's owning ability
not human control   a card's visual design          → the rendering provider's
not human control   a second interface for Agents   → Agent Sessions is its own surface
```

The last line matters: Agent Session control (`serveros-agent`) and Human
Control (`human_control.py`) are adjacent and separate. One is how a person
reaches the system; the other is how the system operates a runtime. They share
a transport today and they are not one plane, because the questions and the
permissions are different.

## Related

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — invariant 14, and the four planes
- [`capability-discovery.md`](capability-discovery.md) — the same support vocabulary, other consumers
- [`fabric-v1-roadmap.md`](fabric-v1-roadmap.md) — the producers, and what has to be true first
- [`../runtime/notification-contract.yaml`](../runtime/notification-contract.yaml) — the closed vocabulary
