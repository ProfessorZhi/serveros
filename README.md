# ServerOS

[中文入口](README.zh.md) · [Canonical architecture](ARCHITECTURE.md) · [Architecture review](docs/architecture/CONSOLIDATION-REVIEW-20260921.md)

**A personal user-space Agent Work OS that enhances Claude Code, Codex and their native tools.**

## The daily experience

```text
Yesterday's authorized Feishu information and real work
  -> ServerOS prepares understanding changes
  -> discuss and correct them with Coordinator in CloudCLI
  -> one personal Board: focus / unfinished / completed
  -> select today's meaningful requirements
  -> CC assembles sources and uncovers missing intent
  -> GPT and the user participate in research and design
  -> CC / native subagents / Quay execute, experiment and verify
  -> readable Item results and evidence-rich E2E reports
  -> professional daily / weekly / special reports
```

This is a common experience, not a fixed workflow. Research may start with experiments;
tests may send work back to design; a useful deliverable need not be code.

Open a fresh Session, mention the work, and recover the **right environment,
context, memory, capability and safety boundary** without rereading the whole world.

## Small core, broad capability

There is one resident ServerOS core daemon per managed node/runtime scope:
**serverosd**. It observes real resources, attributes ownership, handles its admission
boundaries and reconciles explicitly owned desired resources. It is not an LLM planner
or a second process supervisor.

The product composition has two canonical Core members: **serveros** and
**ability-management**. Long-lived providers such as memory, UI or networking remain
outside the daemon.

Two capability families cover the work:

| Family | Examples |
|---|---|
| Agent Working Infrastructure | compliance, credentials, runtime, sessions, context/memory, abilities, workspace/Git, build/test environments, E2E, connectivity, release |
| Information-to-Delivery | source capture/Follow, understanding/alignment, Board, requirements, research/decisions, execution/Quay, document stewardship, reporting and routines |

These are navigation and composition views, not a Skill, repository or daemon for
every box. See [the complete architecture](ARCHITECTURE.md).

## Native agents stay in charge of method

```text
ServerOS preserves: goal, constraints, identity, context, continuity and evidence
Native Agent chooses: investigation, experiments, tools, decomposition and iteration
```

Bootstrap is thin. Detailed memory, Skill content and MCP tools load when needed and
when the runtime actually supports it. Existing native tools remain discoverable.
A catalogue can be global while each device installs and each Session sees a different
subset.

OpenViking is the default independent memory-provider direction; MetaCC supplies
history/intelligence. Neither replaces confirmed work facts or native live-session
control. Quay is an execution accelerator, not a mirror of the user's personal Board.

## Interfaces and information spaces

CloudCLI and the native terminal remain the main conversational work surfaces.
Use a supported contextual plugin rather than requiring a CloudCLI fork.

Feishu provides the principal information sources, reminders, readable work documents
and reporting. My Document Library contains current work and reports; Personal
Knowledge contains curated private knowledge; Published Knowledge contains maintained
reader-facing guides; external documents remain at their canonical location under
appropriate stewardship. Drive holds selected file/evidence assets. IM and Minutes
remain original sources.

GitHub work packets are the durable CC/GPT collaboration path. Authoritative bodies
and generated human views have explicit revision links; they are not independently
editable competing copies.

## Devices and safety

Tokyo is the primary node, Windows a specialist device and the phone a human client.
Beijing is historical, not a normal execution fallback.

Tailscale is the preferred private transport: **tailnet-first, tunnel-by-exception**.
Node connectivity does not prove every application endpoint is reachable. Verify the
actual authenticated client-to-service path before retiring an old tunnel.

Compliance and non-interference come first. Default Tokyo principal is vince; the
ubuntu exception applies only to explicitly delegated shared-gateway work. Current
LiteLLM production and shared-gateway production changes remain **user-executed**.
A Skill instruction or a card button is not an enforced security boundary by itself.

## Ownership

| Repository/component | Owns |
|---|---|
| serveros | product architecture, composition, bootstrap, deterministic contracts and node runtime |
| ability-management | typed capability registry, provenance/revisions, reusable Skills and distribution |
| work-engine | work/control transitions, Run/effect receipts and Board projections |
| hwz-records | understanding, stable intent, designs and human semantic records |
| hwz-ops | instance roots, topology, concrete bindings, policy and observed maps |
| native providers | their actual task stores, sessions, cloud bodies and runtime interfaces |

MarketOps and Support Bot remain independent parallel projects. Support Bot uses
litellm-support-bot for code and troublebuddy-kb-bridge for knowledge; ServerOS does not
take over either product's roadmap.

## Reading and implementation status

[ARCHITECTURE.md](ARCHITECTURE.md) is the single normative product design. Both READMEs
are explanatory entrypoints, not additional authorities.

[docs/README.md](docs/README.md) distinguishes current contracts from older technical
notes. The [review record](docs/architecture/CONSOLIDATION-REVIEW-20260921.md) records the
consolidation's evidence and limits.

**A completed design is not a deployed system.** Implementation acceptance uses the
scenario matrix in the architecture and real revision/environment evidence. Do not
infer CODE, INTEGRATION or LIVE readiness from this README, file counts, tool listings
or a successful documentation commit.
