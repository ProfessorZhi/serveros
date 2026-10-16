# The first client install — a test plan, and only a plan

**Status: NOT RUN. Nothing in this file has been executed.** No plugin has been
installed, no OAuth consent completed, no tool called, and no MCP endpoint
queried. Every row below carries `NOT_RUN` for that reason, not as a formality:
the four inputs in the acceptance checklist's Phase C do not exist yet, so there
is nothing to install and no endpoint to reach.

The release runbook (`docs/fleet-next-acceptance-runbook.md`) owns the
acceptance *verdict*. This file owns the packaging lane's install procedure and
the evidence each step would leave. Where the two overlap, the runbook decides.

## What the test is about

Not "does the plugin appear in a list". Three claims have to survive contact
with a real client, and each is falsifiable:

1. the surface is **33 distinct tools**, counted from `SERVED_TOOLS`, not from a
   document;
2. the authorization server advertises **six scopes**, and the token a client
   receives carries only the **granted** subset — `fleet:read` and `fleet:send`
   by default;
3. **V1 is untouched**: its plugin id, endpoint and health are the same before
   and after, and the Next plugin id is a different id.

## Preconditions, all owner-held

| precondition | from |
|---|---|
| the two approved PNGs, hashing to the values in `brand-assets.lock.json` | Phase C #1 |
| an observed Next `https://<name>.ts.net/mcp`, with both discovery readbacks | Phase C #2 |
| a Next Plugin ID read back from Plugin Creator | Phase C #3 |
| the release ZIP built from all of the above | Phase D |

Until those exist this plan is unexecutable, and saying so is the point: a test
plan that reads as if it had run is the artifact the release would be defended
with.

## Cases

| # | claim | procedure | expected | evidence to keep | status |
|---|---|---|---|---|---|
| T1 | the surface is 33 tools | read the plugin's tool list back through the client | 33 **distinct** names, including `fleet_cloudcli_native_manifest` and `fleet_cloudcli_native_capability` | the tool list as returned, and the count | NOT_RUN |
| T2 | the AS advertises 6 scopes | read `scopes_supported` from the authorization-server discovery document | exactly `fleet:read`, `fleet:send`, `fleet:control`, `fleet:create`, `fleet:approve`, `fleet:navigate` | the document's `scopes_supported` array | NOT_RUN |
| T3 | the default grant is two | consent with no explicit grant, then inspect the issued token | the token carries `fleet:read` and `fleet:send`; a create verb is refused | the token's scope claim (redacted to scope names) and the refusal | NOT_RUN |
| T4 | a read-only grant cannot write | consent with `fleet:read` only, and attempt a write verb end to end | refused, with a refusal the client surfaces — not a silent no-op | the request, the refusal, the client's rendering | NOT_RUN |
| T5 | create is doubly gated | with `fleet:create` granted, create inside an approved project, and then outside it | the in-project create succeeds; the out-of-project create is refused | both attempts and both outcomes | NOT_RUN |
| T6 | control is separate from send | with `fleet:send` only, attempt to interrupt a run | refused; `fleet:control` is heavier than sending and is not implied by it | the attempt and the refusal | NOT_RUN |
| T7 | V1 is untouched | read V1's plugin id, endpoint and health before and after the Next install | identical, byte for byte, before and after | the before/after readbacks | NOT_RUN |
| T8 | the three ids are three | compare the Next plugin id against V1's and the ZIP-Lab's | all three different; neither of the others is presented as the new registration | the id registry readback | NOT_RUN |
| T9 | the archive carries no secret | unpack the shipped ZIP and re-run the source audit over it | no credential-shaped member, no committed `mcp.json` | the member list and the audit output | NOT_RUN |
| T10 | a widening is a re-consent | request a scope the token does not carry, after install | a new consent, not a package edit; the ZIP is unchanged | the consent prompt and the unchanged archive digest | NOT_RUN |

## Rollback

The Next release is **Next-only**: rolling it back means disabling the Next
plugin and leaving V1 alone. T7's before/after readbacks are the evidence that
this is what happened, and are the reason they are recorded *before* the install
rather than after it.

## What this plan does not do

- It does not install, register, upload, log in or call anything.
- It does not treat a green offline suite as a passed install.
- It does not claim the icon renders: rendering is a client behaviour, the
  manifest asks the documented way, and whether it is honoured has not been
  observed.
- It does not merge the four layers of the checklist's status table into one
  verdict. Source READY, ZIP BLOCKED, Registered NOT STARTED and Client
  installed NOT DONE are four facts, and this file changes none of them.
