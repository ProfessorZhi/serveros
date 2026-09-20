# Product discovery decisions — 2026-09-21

> Status: **historical discovery ledger — absorbed into canonical architecture**.
>
> The product decisions captured here were folded into the root
> [`ARCHITECTURE.md`](../../ARCHITECTURE.md) at ServerOS main commit
> `c521035f818b1de2e738e79af778a5aa90680fb0`.
>
> This file is retained as discovery provenance. It is **not** architecture
> authority and must not be used to override the root architecture. A sentence
> here also does not prove that the corresponding capability is implemented or
> LIVE.

## 1. Product north star

ServerOS is a **personal Agent Work OS** for one user, not a multi-tenant agent
platform.

The primary promise is:

> Open a fresh Claude Code or Codex session, mention any work topic, and within
> seconds the Agent can identify the relevant project / Item / prior context,
> retrieve the right memory, discover the right capability and environment, and
> continue work without the user re-explaining yesterday.

The first screen of the product must communicate **both** the daily work
experience and the architectural philosophy that makes that experience fast.

The core design philosophy remains:

- **Environment** — prepare the right place and resources for this work.
- **Context** — provide the smallest correct working context for the current
  intent.
- **Capabilities** — make the right Ability / Provider discoverable and usable
  when needed.
- **Progressive disclosure** — do not preload the world. Start thin, then expand
  context, memory, provider details, Skills and schemas only on demand.

The highest-value automatic outcomes are therefore **right context, right
memory, right capability and right environment at the right time**.

The system must strengthen Claude Code / Codex rather than become a ceremony
around them. A large installed system must still expose a **small active
context**. The daily operating loop in §3 is the user experience built on top
of these principles; it does not replace them.

## 2. Layer 0 — compliance and non-interference

Compliance is prior to every other capability.

On shared infrastructure the default principal is `vince`. Foreign or
unknown-owned resources are not mutable merely because they are visible.

Current explicit delegated exception:

- the shared AI gateway is system-owned under `ubuntu`;
- Vince is explicitly allowed to maintain that exact gateway;
- this delegation is **target-scoped**, not a general permission to operate as
  `ubuntu`.

Network changes are high-risk on the shared Tokyo host. Any Docker/Tailscale/VPN/
route/listener/subnet mutation must pass a network preflight and netshadow-style
conflict check. Unknown/incomplete network policy fails closed.

ServerOS must never turn "I can see it" into "I own it".

## 3. Daily operating loop

The desired daily loop is first-class product behavior:

```text
Feishu / Docs / Wiki / Minutes / GitHub / sessions
                    ↓
           incremental source capture
                    ↓
        relevance / privacy / blacklist
                    ↓
       provisional structured understanding
                    ↓
           Understanding Delta