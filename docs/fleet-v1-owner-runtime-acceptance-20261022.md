# Fleet V1 owner/runtime acceptance — 2026-10-22

Candidate for SRV-0011. This document distinguishes source, isolated execution
and deployment. It is not a claim that the production App has been updated.

## V1 behavior

A commander can be a local Claude Code/Codex agent or, after local acceptance,
ChatGPT. Executors are the native Claude Code/Codex sessions on enrolled
devices. Fleet supplies identity, routing, grants and durable delivery; native
sessions retain their own provider state and execution abilities.

The commander gathers information and communicates with people and other
models, then dispatches complete long tasks in parallel where dependencies
permit. It retains the original session/run/queue references, returns after
ACK, and reconciles those references after disconnection. A DAG describes
work; it does not schedule itself. A continuing agent must consume dependency
edges. Unknown delivery is fenced rather than retried as a new task.

For an explicitly selected account/device and named project roots, owner-max
allows real project discovery/creation and session discovery/read/create/send
without adding a grant for each new project. New native sessions inherit the
selected family's maximum execution mode. Other accounts, unselected device
namespaces and explicit incompatible model rules retain their boundaries.

The candidate publishes 34 Fleet tools: 10 authored, 22 pinned native mirrors
and two descriptors. The separate patched CloudCLI advertises 23 native tools.
Eight shared Skills teach the commander the actual tool and recovery workflow.
Native Core/serverosd recovery must observe fresh facts, respect maintenance
and active agents, bound recovery attempts, and prove a real recovery effect.

## Accepted source and execution

| Area | Evidence and scope |
| --- | --- |
| Fleet owner/control | Core PR391, through 9603f47. Independent behavior review at 6a23d1d: 12 targeted cases pass. Later commits correct success fixtures. Full runtime contains the owned entry and reuses the existing AgentControlStore; the standalone Fleet surface cannot impersonate that durability. |
| Maximum native permissions | Targeted read-only metadata from the two exact acceptance-native conversations proves CC bypassPermissions, and Codex danger-full-access/never. CC is the executor family, not a claim that its configured model vendor is Anthropic. |
| Real concurrent executors | CC and Codex each execute one isolated own-project task after the two sends are issued together. ACKs return in 0.090/0.162 seconds. Both output files are correct. The commander MCP connection is replaced while executors continue. |
| Codex terminal correction | Original e90fbfe Codex task actually finishes but its receipt is UNKNOWN; this negative evidence is retained. CloudCLI c6be626 recognizes typed provider completion without inventing an OS exit code. One new distinct read-only task ACKs in 0.125 seconds and completes with success=true, exitCode=null and provider-turn-completed. |
| Restart and replay | Fresh Node processes reconstruct the new completed run and exact final snapshot from the same SQLite receipt owner. Original CC completed and original Codex UNKNOWN records remain unchanged. Accepted intent/key replay returns the same run ID with IDEMPOTENT_REPLAY while model dispatch is disabled. Root Evidence641 records this isolated acceptance. |
| Windows source | PR387 at 3eeb84c: official hosted Windows build, six suites/477 checks, extracted static ZIP startup, and a real current-user ScheduledTask recovery effect. Independent source/hosted evidence review passes. Actual SCM StartServiceW effect and user-device LIVE acceptance are not covered. |
| Private state ancestry | a4d74b8 source, integrated as 7edfce8/2a8c295/d7a6c14. Hosted native full tree: 33 suites/2844 checks pass. The current-user private boundary permits the measured state-root layout while retaining no-follow identity checks. Independent review passes. |
| CC commander Skill | A real model selects fleet-commander-orchestration, makes three Fleet reads and returns a parallel DAG. It reports four enrolled devices with three live routes, as returned by tools. One invocation; CLI reports six internal turns, exceeding the three-turn test target. No writes were requested. |

The native model evidence is under root Run0046's exclusive
`v1-owner-runtime-20261021/integration-20261022/native-c6be626/` namespace;
CloudCLI policy metadata and the six-patch packet are in Run0050's exclusive
`cloudcli-run-readback-20261022/` namespace. Windows/private-state and commander
Skill evidence retain their separate Runs and source pins. Existing historical
Run finalization files are not overwritten.

## Remaining acceptance gates

- Linux PR390 at b25a9e8 is **CHANGES_REQUIRED**. Independent probes confirm
  a broken default busy-process import, ancestor-symlink writes, stale held
  lease identity, missing real daemon map/lease wiring and unknown-unit
  refusal, and writer/consumer schema disagreement. A single complete repair
  batch is dispatched to the Dell executor. A process-lifetime shared lease
  also prevents useful coexistence with the Python recovery writer.
- Client PR388 at 169e50b passes the original seven review cases but still has
  four residuals: an outdated owner-max Skill condition, output-directory
  ownership race, initialize-error misclassification, and unbounded buffering
  or short writes. An isolated source repair lane owns this complete batch.
- CloudCLI c6be626 has root native acceptance and focused regressions;
  independent source review is pending. Its deliverable is a pinned patch
  packet, not an upstream merge or a production installation.
- Initial Codex commander Skill reading was blocked by the local bwrap
  loopback setup error. MCP reads passed; Skill compliance was not claimed.
  A separate one-invocation native-owner acceptance is in progress.
- Final combined Python/native checks, real pinned-ability runtime build,
  isolated version promotion/rollback, and final source/package hashes remain
  to be completed after the remaining source lanes freeze.
- User Windows LIVE execution was rejected by automatic approval review:
  execution required approval while policy was never. Hosted testing does
  not substitute for that device. No alternate route bypasses the rejection.
- The existing App's actual Creator backend ID is not resolved by its
  plugin:// URI. Same-ID production upload, OAuth and ChatGPT rollout are
  separate remaining gates. Developer adapter directories and synthetic
  update checks are not represented as an uploaded release.

Production Fleet, resident services, global clients and network transports
have not been changed by this candidate. Current reverse tunnels remain the
observed transport; no Tailscale migration is included.
