# Capability Discovery — Native-first

Product authority: [ARCHITECTURE.md](../ARCHITECTURE.md), sections 1, 16 and 29.

## The non-regression contract

ServerOS strengthens native Claude Code/Codex discovery. A ranking hint must not
hide a capability that the runtime could otherwise discover. The Agent may ignore
suggestions, query the wider catalogue or use an already available native tool.

This is **not** a claim that every discovered tool is authorized. Discovery/ranking
and effect authorization are separate. Security policy may deny an operation even
when the tool remains discoverable. ServerOS is not a mandatory proxy for all native
calls and cannot claim technical enforcement over paths it does not control.

## Preserve existing registry identities

The shared ability-management catalogue identifies typed artifacts. Skill, plugin
and MCP records retain type + canonical identity; same name is not same identity.
Capability/Operation describes the requested effect; an artifact declares what it
provides. Do not convert semantic WHAT shorthand into a destructive registry rename.

```text
need
  -> native capability or catalogue candidates
  -> Provider / compatible revision
  -> device/target Binding
  -> current readiness and authorization
  -> supported Session attachment / direct native use
```

Author, upstream, distribution channel, installation and execution trust remain
independent. External sources stay attributed and pinned when selected.

## Global catalogue, local projection

Devices share one registry, not every installation. Device/Project/Environment
projections combine compatibility, installed revisions, local target bindings,
readiness and policy. Family/cluster membership is navigation metadata, not physical
payload nesting or a rule to install everything everywhere.

Registration != installation != enablement != attachment != content loading.
Avoid duplicate MCP exposure if an existing native plugin already provides the tool.
A selected dependency lock should not break because an unrelated catalogue record
was added; changes to older catalogue-wide pin checks require explicit compatible
implementation work, not bypassing their verifier.

## Discovery and loading

At startup, expose only the small ServerOS entry and essential boundaries. Do not
require an Item/Run ID or a live global ability count before a user prompt exists.

After intent resolution, relevant readonly context/ability expansion may happen
automatically within the budget. Progressive disclosure is not a requirement to ask
the human at every level. A runtime's own metadata loading remains native.

Skill descriptions carry triggers, useful scope and ownership boundaries. Detailed
procedures load only when used. If an adapter supports dynamic MCP/tool discovery,
use it; otherwise disclose the need to reconnect/start a fresh Session or use a safe
available CLI. Do not pretend unsupported hot attachment succeeded.

## Acquisition and improvement

An authorized temporary experiment can try an unfamiliar capability without first
making it a permanent registry entry. Assess provenance and effects before attaching
it. Text-only Skills can contain malicious instructions; executables/install hooks,
new credentials and network boundaries require stronger admission.

Repeated value can justify managed registration, a reusable Skill or a candidate
Profile. Compare against a baseline and record environment, exact inputs, measured
results and interventions. Production Profiles remain stable until deliberate
promotion; one successful experiment is not automatic global deployment.

## Focused validation

At important adapter/plugin releases verify:

- native discovery still finds an unrelated valid Skill;
- no duplicate tool is injected just because ServerOS knows its identity;
- missing provider/memory degrades honestly without blocking unrelated work;
- an unsupported attachment is not reported ready;
- a discovered but unauthorized production operation does not become executable;
- global catalogue changes unrelated to the selected composition do not force churn.

The absence of a registered preferred method must never prohibit an otherwise
permitted native investigation.
