# ServerOS Human Control & Autonomous Work Addendum

> Status: discussion addendum to `2026-09-20-final-serveros-architecture.md`.
>
> This document focuses on human interaction, mixed-initiative autonomy, Web/terminal control surfaces, periodic work advancement, ChatGPT/Claude/Quay role allocation, and daily reporting.

---

## 1. Human control should be layered by interaction depth

Human control is not one UI. The final product should support four depths:

```text
Level 1  Push / lightweight decisions
         Feishu Alert · Status · Work · Report cards

Level 2  Conversational control
         Feishu chat / BotMux / future chat providers

Level 3  Rich Agent Console
         Web/mobile console such as CloudCLI / ClaudeCodeUI

Level 4  Raw terminal substrate
         tmux / PTY / shell / SSH
```

The four levels may coexist. None becomes authority merely because it is convenient.

## 2. Feishu is the low-friction supervisory surface

Feishu should be the default place for:

- Alert and Recovery notifications
- scheduled Status digests
- morning or periodic Work briefing
- selecting todays focus Items
- answering a blocker
- requirement/design/technology Decision Packets
- bounded approvals
- pausing, resuming or delegating an Item
- receiving the Daily Report

It should not try to become a full remote terminal.

A Feishu action must resolve to a semantic ServerOS intent:

```text
human message/button
→ verified actor + conversation context
→ semantic intent
→ current object re-resolution
→ support/readiness
→ admission
→ execute or refuse
```

Delivery is not acknowledgement. A button press is not an approval unless it is bound to the exact request, actor, target and expiry/context.

## 3. tmux is a substrate, not Agent Session authority

tmux is useful for persistent shells, long-running CLI processes, reconnectable terminals and emergency/manual inspection.

But:

```text
tmux session/pane ≠ Agent Session identity
```

A tmux pane may be a Provider locator. ServerOS should not infer logical Agent Session or Work identity from tmux when a provider-native session API exists.

Preferred control order:

```text
Agent provider API / SDK
→ structured ServerOS Agent Session contract
→ tmux / PTY fallback
→ raw process control only as last resort
```

## 4. CloudCLI / ClaudeCodeUI is an optional Web Console Provider

The project https://github.com/siteboon/claudecodeui has evolved into CloudCLI. Current upstream provides Claude Code / Codex / Cursor support, Web/mobile chat, session discovery/history, PTY shell, file explorer, Git UI, browser use, REST/WebSocket surfaces and a plugin system.

This makes it a strong candidate for a rich Human Control / Agent Console Provider, but not for ServerOS core or ServerOS authority.

Preferred relationship:

```text
ServerOS facts / work / policy / session semantics
              ↓
        ServerOS API / MCP
              ↓
   optional CloudCLI ServerOS plugin
              ↓
 Web / mobile rich supervisory console
```

CloudCLI may continue to own provider-native UI concerns such as terminal streaming, native chat history and file browsing.

ServerOS should own:

- device/work identity
- normalized Agent Session semantics
- Work Item semantics
- authorization/admission
- environment readiness
- Alert/Status/Work/Report presentation models

A hard fork should be avoided unless necessary. CloudCLI is AGPL-3.0-or-later; its plugin system makes an extension/plugin approach preferable where practical.

A ServerOS CloudCLI plugin could expose:

```text
ServerOS Overview
Today / Work Board
Agent Sessions
Approvals / Decisions
Environment Readiness
Alerts
Quay Delegations
Evidence / Reports
```

while leaving CloudCLI native chat/files/git/shell/browser surfaces intact.

## 5. One semantic action, many control surfaces

The same semantic action should work through Feishu, Web UI, CLI or another surface.

Example:

```text
session.send(agent-session:X, message=continue)
```

may originate from Feishu, CloudCLI, serverosctl or another Agent, but should still pass through the same support/readiness/admission semantics.

---

## 6. Mixed-initiative autonomy: human direction without human bottleneck

The desired model is mixed initiative:

> Agents should keep making useful progress without waiting for a person in most situations, while making intent and progress easy to inspect and redirect.

Research on mixed-initiative supervisory control supports two conditions: autonomous actors should be able to progress without constant human input, and the interface should let humans rapidly understand and modify their progress and intent.

Therefore ServerOS needs a strong distinction between:

```text
AUTONOMOUS PROGRESS
and
DECISION GATES
```

## 7. Autonomous progress inside an approved envelope

Once an Item has a clear enough goal, scope, acceptance criteria and approved execution boundary, Claude Code or Codex should be able to:

- inspect relevant context
- create or reuse an Item worktree
- edit code/docs
- run targeted checks
- create bounded test environments
- delegate bounded Quay tasks
- collect evidence
- update progress/checkpoints
- continue to the next approved Activity

without asking for approval at every step.

## 8. Human + ChatGPT decision gates

Human and a high-level planning/review Agent such as ChatGPT should be deeply involved where judgement has high leverage:

- candidate prioritization and todays focus
- requirement clarification
- product intent
- architecture/design
- technology selection
- non-obvious trade-offs
- expanding Item scope
- changing acceptance criteria
- shared-production/high-risk action
- strategic blockers
- deciding whether an ambiguous outcome is acceptable

Routine implementation mechanics should not repeatedly interrupt the human once these decisions are settled.

## 9. Execution Envelope

Do not create another workflow database. Treat the autonomy boundary as structured policy attached to the Item or Activity.

Conceptually:

```yaml
execution_envelope:
  scope:
    repos: [...]
    item: ...
  allowed_change_class: reversible-medium
  allowed_providers: [...]
  network: bounded
  cost_budget: ...
  verification_budget: targeted
  quay:
    allowed: true
    implementation: bounded
    testing: allowed
  human_required_for:
    - goal_change
    - architecture_tradeoff
    - shared_production_write
    - destructive_action
    - secret_rotation
```

The semantic requirement matters more than the exact storage schema:

> One approval of a meaningful work boundary should replace dozens of micro-approvals.

---

## 10. Three autonomous loops

Periodic autonomous work should be scheduled recipes/runs outside `serverosd`. The daemon should not contain LLM planning.

### 10.1 Perception / understanding loop

```text
authorized Feishu / GitHub / document sources
→ incremental capture since cursor
→ source-backed observations/snapshots
→ understanding delta
→ responsibilities/projects/people/topics update
→ Candidate Board refresh
```

This loop continuously improves the system model of the users role, responsibilities, current projects, commitments, stakeholders, constraints and emerging candidate work.

It may run periodically or on source events. It must not automatically create full Work Items for every message.

### 10.2 Work advancement loop

```text
Today / Active Items
→ inspect current checkpoint
→ choose next approved Activity
→ direct Claude/Codex execution OR bounded Quay delegation
→ targeted verification
→ evidence + progress update
→ checkpoint
→ continue while inside execution envelope
→ stop at decision gate / blocker / completion
```

The objective is steady progress, not maximum visible Agent activity.

### 10.3 Reporting loop

```text
work truth + evidence + decisions + Quay ledger
→ evidence-grounded synthesis
→ Daily / Weekly / Decision Report
→ Feishu publication/delivery
```

Reporting should consume durable facts rather than one session memory.

---

## 11. Intended human / Agent role split

### Human

Owns goals, priorities, final business judgement, strategic trade-offs and high-impact approvals.

### ChatGPT or another high-level reasoning partner

Best used for requirement clarification, research synthesis, architecture, technology selection, design review, decision framing, cross-project reasoning, and reviewing evidence/deliverables.

This is primarily a coordination/review role rather than the local execution substrate.

### Claude Code / Codex

Best used for local implementation, repo/worktree operations, bounded operational execution, targeted tests, evidence collection and progress/checkpoint updates.

### Quay

Best used for bounded delegated slices such as reproductions, test matrices, verification, independent implementation slices whose design is already settled, and repetitive/parallelizable checks.

Quay returns evidence/results to the parent Item and never becomes the parent workflow authority.

---

## 12. Daily Report contract

The Daily Report should report progress delta and decisions, not terminal activity.

Recommended structure:

```text
1. Todays outcomes
   - what became true today

2. Priority Items
   - progress delta
   - current state
   - next action

3. Delivered / verified
   - deliverable
   - acceptance/evidence

4. Decisions made
   - requirement/design/technology decisions

5. Blockers / needs human decision
   - decision-relevant blockers only

6. Quay contribution
   - delegated Goal/slice
   - tasks/runs
   - result
   - evidence
   - code/test contribution
   - failures if relevant

7. New understanding / new candidate work
   - important delta only

8. Tomorrow / next
   - likely next focus
```

Reports should be concise enough to read quickly, audience-aware, and traceable to evidence.

---

## 13. Notification policy for autonomous work

Not every autonomous-loop event should interrupt the user.

```text
IMMEDIATE
  action/decision required now
  high-value alert
  high-risk approval

BATCHED / WORK DIGEST
  candidate changes
  non-urgent blockers
  progress updates
  decisions that can wait

SILENT
  routine successful implementation step
  targeted test passed
  normal checkpoint
  routine source refresh
```

HCI research on interruption shows measurable task-switching cost and supports batching or deferral to opportune moments.

> ServerOS should optimize for few high-value interruptions and rich pull-based inspection, not maximum notification coverage.

---

## 14. Updated north-star daily experience

```text
Morning / scheduled
────────────────────
ServerOS refreshes authorized sources
→ updates understanding
→ updates Candidate Board
→ sends compact Work briefing

Human + ChatGPT
────────────────
select todays 1–2 priorities
resolve product/design/technology choices
approve execution envelope

During the day
──────────────
Claude/Codex advances work autonomously
→ direct execution or Quay delegation
→ targeted verification
→ checkpoints

ServerOS only interrupts for:
→ meaningful blocker
→ design/goal decision
→ approval boundary
→ important Alert

At any time
───────────
Feishu: lightweight control and decisions
Web Console: rich session/work/environment control
tmux/PTY: raw terminal fallback
CLI/MCP: Agent-native structured access

End of day
──────────
ServerOS generates evidence-grounded Daily Report
including progress, decisions, blockers, next actions and Quay activity/results
```

This is autonomous work with human direction, avoiding both extremes:

```text
human approves every command
```

and

```text
agent runs indefinitely with no inspectable intent
```

---

## 15. Research references

- Hardin & Goodrich, On using mixed-initiative control: a perspective for managing large-scale robotic teams, HRI 2009.
- Barnes, Chen & Jentsch, Designing for Mixed-Initiative Interactions between Human and Autonomous Systems in Complex Environments, IEEE SMC 2015.
- McFarlane, Coordinating the Interruption of People in Human-Computer Interaction, INTERACT 1999.
- Adamczyk, Iqbal & Bailey, A method, system, and tools for intelligent interruption management, 2005.
- Li et al., Alert Now or Never: Understanding and Predicting Notification Preferences of Smartphone Users, TOCHI 2022.
- Morrison, Iqbal & Horvitz, AI-Powered Reminders for Collaborative Tasks: Experiences and Futures, PACM HCI 2024.
- Lu et al., Challenges and Opportunities for Technology-Supported Activity Reporting in the Workplace, CHI 2018.
- Asthana et al., Summaries, Highlights, and Action Items: Design, Implementation and Evaluation of an LLM-powered Meeting Recap System, PACM HCI 2025.
- Tufano et al., AutoDev: Automated AI-Driven Development, 2024.
- CloudCLI / ClaudeCodeUI: https://github.com/siteboon/claudecodeui
