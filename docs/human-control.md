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

`mcp/human_control.py:483-566`, measured on this device rather than assumed:

```text
human.conversation.context     SUPPORTED    /api/sessions reports chatId, chatType, sessionId
human.notification.transport   UNKNOWN      an unaddressed POST /api/trigger is a SYNTHETIC
                                            session: the text becomes a model's task and its
                                            answer returns over HTTP. The service's own header
                                            for that turn says the reply is "not shown in a
                                            chat" and forbids posting to Feishu, so this path
                                            reaches no person. Corrected downward from SUPPORTED
                                            on 2026-09-25; an addressed trigger (`target.chatId`)
                                            is what would settle it
human.identity.verify          UNKNOWN      `botmux actor current --json` verifies only
                                            inside a BotMux turn; from a non-turn process
                                            it answers status=blocked, current_actor_unverified
human.transport.ingress        UNKNOWN      a pull surface exists (`botmux history --session-id`,
                                            `botmux quoted`); whether anything PUSHES inbound
                                            content is not established from here
human.approval.transport       UNKNOWN      BotMux can deliver the question and collect a key
                                            (`botmux ask buttons --options` returns the chosen
                                            one). Whether an answer can arrive BOUND to the
                                            request that asked is what is not established
human.interactive.rendering    UNKNOWN      `send --card-file/--card-json` and
                                            `card patch --message-id` are documented; no card
                                            has been observed arriving
```

**`UNKNOWN` and `UNSUPPORTED` are not two spellings of "no".** An `UNSUPPORTED`
transport *has no way* to do it — the surface does not exist. An `UNKNOWN` one
*might*, and this process cannot establish it from where it stands. They call
for opposite actions: `UNSUPPORTED` means design around it; `UNKNOWN` means go
and find out. Flattening them produces a system that stops asking.

**Two of these moved, and the direction is the honest part.** Approval transport
and interactive rendering read `UNSUPPORTED` on 2026-09-14 on the strength of
three 404s (`/api/events`, `/api/messages`, and a callback route) that the second
pass on 2026-09-15 **did not reproduce**. The bogus-route control settles why:
a deliberately invalid path answers `401` with `{"code":"unauthorized"}` while
the three real routes had answered `404`. Authentication precedes routing here,
so a 404 was never evidence that a surface is absent — it was evidence that this
process was unauthenticated when it asked, which is a statement about our
implementation wearing a capability claim's clothes. Both surfaces are
documented and both are reachable; what is `UNKNOWN` is whether either carries
what ServerOS needs, and that is a question for whoever pays for the
integration. The old entry was wrong in the direction that stops asking, which
is the expensive one.

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
may flow      a non-destructive console operation         (see the console, below)

may NOT       approve a destructive action                (no answer BOUND to the request)
may NOT       treat a message as a verified instruction   (identity.verify UNKNOWN)
may NOT       receive inbound content as an event         (transport.ingress UNKNOWN)
may NOT       have ServerOS author a card UI              (interactive.rendering UNKNOWN)
```

The approval line did not move when its capability word did, and the difference
is worth stating because it is the whole lesson of that correction. BotMux
**can** put the question in front of a person and return the key they pressed.
What has not been shown is that the answer comes back carrying the identity of
the request it answers — and an unbound key is not an approval, it is a
keystroke. `request_approval` still raises; the premise it raises on is now the
surviving one rather than a 404 that does not reproduce. A refusal resting on a
false premise is a refusal somebody deletes the moment they notice.

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

### The delivery runtime — §W

The command above was, until v1.2, **reachable only by somebody typing it**. That
is a complete delivery path that says nothing, and the failure has no symptom: an
unrun command and a node with no alerts look identical from outside. So the last
hop is a runtime now, and it is two files:

```text
runtime/serveros-alert-sync.service   Type=oneshot, no Restart=, no socket
runtime/serveros-alert-sync.timer     OnCalendar=*-*-* *:4/5:00, Persistent=false

installed by  scripts/release.py alert-worker --conversation <chat>
              (a subcommand of the release CLI, not of serverosctl: it acts on
               the node's units, which is what the release tool owns)
```

**It is not a second ServerOS daemon**, and that is held by reading `Type=` and
`Restart=` rather than by trusting this paragraph: `tests/test_core_freeze.py`
asserts the tree declares exactly one *resident* unit and that every other one is
provably non-resident. `RuntimeDirectory=` is deliberately absent, and it is
worth naming because a copy-paste from `serverosd.service` would bring it —
systemd **deletes** a `RuntimeDirectory` when the unit that declared it stops, so
a oneshot declaring the daemon's would delete `%t/serveros` and the daemon's
socket every five minutes.

**Why it is a separate act from `promote`.** `promote` places a revision and
points the node at it, and it has to keep working on a node whose operator has
not decided where alerts should go. Folding the worker in would make every
promotion depend on knowing a chat id — the same shape as blocking a core
release on a delivery question. So `alert-worker` is what a person types once,
with a destination, and it is the act a reboot does not undo.

**It delegates all four properties.** Dedupe, cooldown, recovery and
delivery-failure truth are the register's — the worker's whole conversation with
the daemon is `alert.pass` / `alert.pending` / `alert.record-delivery`, and it
computes no clock of its own. `tests/test_alert_delivery.py` holds that over the
command's AST, and `local_derivations()` is deliberately written to catch the
subtle shape (`alert["pending"] = {"not_before": …}` while "just fixing up the
payload") and not only the obvious one.

**The timer is not the queue.** `Persistent=false`: an alert that opens while the
worker is not running stays pending with its clock unmoved, and the next ordinary
tick delivers it. A catch-up rescues nothing — all it decides is whether a
machine that has just come back sends a burst of cards at once. The offsets
`:04 :09 :14 …` are a measurement rather than a taste: `hwz-monitor.timer` runs
at `:00/:30` and takes a ~15 s sampling window its own header records as
polluted, and `botmux-watchdog.timer` runs on a five-minute stride from `:02`.
A delivery tick is one IPC round trip against a daemon that is already ticking;
landing it inside the monitor's window would put our work into the numbers a
person reads.

The read half is on the node MCP surface as `alert.list` and `alert.inspect`,
which is where an Agent asks. The write half — `ack` and `snooze` — is
**deliberately not**: `fleet/mcp-contract.yaml` says that surface holds no
state and changes nothing, and `human.transport.ingress` is `UNKNOWN` while no
answer is yet shown to come back bound to the request that asked. A design that
let a person "reply ACK" in Feishu would produce alerts nobody could close and
a plausible-looking reading of messages that were never verified. Acknowledge
in the terminal, or acknowledge nowhere.

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

## The Remote Console — §L §M §N §O §P §AA §AB

Notification is ServerOS speaking. The console is a person **looking and
acting**, over the same channel:

```text
Feishu App1 ─→ BotMux ─→ HumanControlProvider ─→ ServerOS
                          (transport)             (semantic operations)
```

Not through the Feishu Docs API, and BotMux is not the authority: ServerOS core
reads no BotMux private state, and `tests/test_agent_mcp.py` greps the daemon's
own source for the transport's name to hold that — the daemon must not know
which provider exists, or it will start deciding with that knowledge.

### The vocabulary is operations, not buttons — §P

```text
session.list      L1   session.send     L2   session.close     L2   destructive
session.inspect   L3   session.resume   L2   session.interrupt L2   destructive
session.result    L2   session.select   L1   session.handoff   L3   destructive
session.create    L2   destructive
```

`runtime/human_console.py` is the only place this vocabulary exists, and a test
asserts it is **exactly** this list — so nothing arrives by a client's
permission. §M spells two of them differently from the session contract
(`resume/continue`, `close/stop`); those are **aliases**, not second entries,
because two entries would acquire two support states and could disagree.

**`session.select` is `LOCAL`.** It is ServerOS's own decision about which
session a conversation is addressing; no provider is asked, and a state word
that said `AVAILABLE` would claim one was.

### The sentence §M writes twice, enforced

```text
session.close      action = session.stop        never satisfied by session.interrupt
session.interrupt  action = session.interrupt   (a different verb)
```

An operation is priced on **its own** provider action. `_assert_no_borrowed_support`
refuses a state map in which an operation's entry carries somebody else's
action — the close-as-interrupt substitution §M forbids — and a mutation that
prices `session.interrupt` on `session.stop` makes it fire. This is enforced
rather than commented because the first version of that guard checked a
condition that **could never be true** (no two operations share an action), and
a guard nobody can make fire is the same defect as a feature nobody wired — the
pathology this version of ServerOS exists to name.

When the transport has close and not interrupt, the answer is:

```text
session.close       AVAILABLE
session.interrupt   UNSUPPORTED   the transport has no turn-scoped verb
```

The entry is **present and marked**, never omitted. A person has to be able to
tell "cannot be done" from "nobody built it", and an absent row says the second.

### The levels are §N's, and they are the existing axis — §AH

```text
L0  北京 / Claude Code · 2 · Codex · 1        counts. NO session row, ever
L1  one card per session: session_ref · runtime · device · bound_item ·
    status · last_activity · title
L2  operations, priced on the selected session, plus its support state
L3  provider · backend session id · transport · provenance · runtime features
```

`CARD_FIELDS` is asserted as an **exact** key set, and that assertion earned its
place immediately: the constant listed six facts while the projection emitted a
seventh (`title`) that nothing had argued for. `title` stayed and the constant
now names it, with the rule that it is a **label the provider already gives the
session** and not content read out of it.

**L0 is a prohibition, not a rendering choice.** §AH: L0 must load no full
session history. The test asserts the *absence* of a session row and of every
session ref in the serialized payload — the property is what must not be there.
This is the same L0–L3 as `context/disclosure-contract.yaml`; §12 forbids a
**fourth coordinate system**, not a second subject.

### The card is a join, and neither plane can produce it — §17

```text
bound_item  ← the thread binding (agent control store)
runtime · device · status · title · action_support  ← the provider row
```

The provider knows the session and not what it is for; the binding store knows
what it is for and not whether it is alive. A binding store that **did not
answer** is reported as `binding_state: UNKNOWN` with the exception text, never
as an empty map — because an empty map reads as "no thread has a purpose",
and that reading shows every session as a coordinator. A coordinator really is
bound to no Item, and its card says so with a reason rather than with a hole.

### Selection is attention, not identity — §M

```text
SELECTION_FIELDS     conversation_ref · session_ref · device_ref · selected_at · identity
THREAD_BINDING_FIELDS                    session_role · bound_item_ref
```

Disjoint by shape, and held three ways: a test asserts the disjointness; the
store refuses a selection record carrying a binding field even when assembled by
hand; and the console's builder has no field to put one in. Selecting a session
is choosing where the conversation is **looking** — it cannot move a thread to
another Item, and a thread's Item cannot be changed by anything this plane does.

The record is keyed by `conversation_ref` and **not** by actor, which is not an
exception to the HumanIdentity rule: a conversation is not a person. `identity`
is a word the **transport** states, and it defaults to `UNVERIFIED` — a
parameter rather than a literal, so that the day verification exists the record
does not go on writing `UNVERIFIED` in the one direction that looks like
success.

Two asymmetries that would otherwise be surprising are deliberate and asserted
in one place so neither can be "fixed" into looking consistent:

```text
a selection is re-selectable      moving attention is the operation (§M)
a binding is write-once           what a thread is for does not move (§17)
a selection is never swept        a pointer is not a window — STALE vs NONE
                                  is a distinction only the record can carry
```

A selection whose session is gone is reported `STALE` **and kept**. Clearing it
would leave a person finding nothing selected rather than being told what
happened to what they chose. `live_refs=None` means the caller did not read the
provider — that is `SELECTED`, not `STALE`, because a console that cried stale
whenever it skipped a read trains its reader to ignore the word.

### §AB — the product boundary

```text
ChatGPT mobile / Codex Remote   an OpenAI surface. ServerOS neither replaces
                                nor proxies it, and the two may coexist.

ServerOS unifies                session identity · Item ownership ·
                                Ability semantics · runtime/device truth

different Human UIs coexist     Feishu App1 is one. It is not the only one.
```

### Reaching it

```text
serverosctl console operations --level L2      the vocabulary and its levels
serverosctl console selection <conversation>   what that conversation addresses
serverosctl console select <conversation> <session_ref>
agent.console (serveros-agent MCP)             device, level, conversation_ref
```

`agent.console` takes **no `context_ref`**, alone among the tools on that
surface: every control action resolves an actor and is gated on admission, and
this one changes nothing and claims no actor. A console that required an actor
would be claiming to know who was looking, and §AA says that is `UNKNOWN`.

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
