# Fleet Next: isolated dual-stack release, independent of SwapUsed occupancy

Status: engineering contract only; NOT deployed. Parent #189, RC #192, accounts #197.

## Agreed release order

1. Finish Fleet Next candidate, independent runtime policy, MCP + Skills ZIP and
   the selected square starship identity image (real bytes must be supplied and
   hash-verified). No synthetic fixture is an installable brand release.
2. Install Fleet Next as a SECOND service and SECOND ChatGPT private plugin;
   retain the existing V1 unchanged. Both display "ServerOS Fleet". They have
   different stable Plugin IDs and different MCP App/endpoint bindings.
3. Prove Next tools, real device access, the separate Claude Code/Codex accounts,
   session creation and continuation, refusal paths, plugin ZIP update-in-place,
   and Next-only rollback on real devices.
4. Explicit owner decision: retire and uninstall V1 only AFTER successful
   side-by-side acceptance. Do not delete historical sessions, credentials of
   other OS users, data, or shared tunnels.

No new routine plugin installation per backend change. After the first Next
registration, keep the Next Plugin ID and its published MCP URL stable.
Backend-only changes do not require a ZIP; Skills/image changes may require an
updated ZIP to the SAME Next Plugin ID. Tool schema refresh or additional OAuth
scopes may still require a client refresh or human re-consent.

## Critical existing installer collision

The existing fleet_mcp/deploy/install.py hardcodes the unit name
serveros-fleet-mcp.service and a V1 service template. Calling it for Next would
NOT provide dual-stack isolation; it can restart/overwrite the existing V1.

Do not run this installer for Next. First provide and review a Next-only
installer that maintains:
- systemd unit: serveros-fleet-example-mcp.service
- own runtime/revision tree and current/previous symlinks
- own binding file, OAuth state/issuer configuration and audit
- own loopback port, own HTTPS MCP route and plugin registration
- independent start/stop/rollback/uninstall; no commands target the V1 unit

The source branch on which this draft is based has a Tokyo A worktree with
a protected LOCAL icon/baseline commit not yet pushed. This integration branch
is deliberately separate to avoid overwriting that local work.

## Static preflight: no external effects

Run the standard-library-only preflight with a non-secret JSON proposal
containing V1's freshly observed identity, Next's intended identity, resource
caps and explicit attestations:

    python3 fleet_mcp/deploy/next_dual_stack_preflight.py --proposal proposed-next.json

It refuses overlapping service units, runtime trees, config/OAuth state paths,
loopback ports, public MCP endpoints or Plugin IDs. It also refuses missing
owner review, OAuth registration or rollback attestations and missing resource
caps. Its report has writes=[] and executes=[] by design.

PRECHECK_READY_FOR_HUMAN_CANARY means only that supplied STATIC data has no
detected contradiction. It does NOT prove the running service's real identity
or permit an unreviewed deployment. Input attestations must be verified by an
owner, not fabricated by the candidate installer.

## Memory constraints without an impossible "stable Swap" promise

High SWAP USED percentage alone is NOT an admission gate: it may represent
cold historical pages that Linux does not eagerly swap back in.

Instead install ONE bounded Next ingress at a time, only when immediate live
pressure allows a controlled probe. Initial candidate user-service limits:
- MemoryMax=1536M
- MemorySwapMax=0
- CPUQuota=40%
- TasksMax=96
- low IOWeight, restart backoff, NoNewPrivileges and own logs

All these are desired policy, not currently measured or installed values.
systemctl --user show must confirm the ACTUAL limits after launch. MemoryMax
does not solve disk pressure, nor does cgroup swap isolation make the host safe
if its disk is already thrashing.

Observe current Memory PSI full, swap-in/out, blocked tasks and I/O latency
around the launch. Do not insist on days of zero Swap. However if the host is
actively thrashing, starting an additional process may still hurt V1. Defer
that short launch attempt or select a different bounded canary host, not a
permanent suspension of development.

Canary sequence:
- verify V1 before launch, snapshot its version and endpoint
- stage Next binary/release without restarting V1
- verify dedicated systemd unit + resource cap readback
- start one Next process with rollback immediately available
- verify Next health, tools/list, OAuth and one read-only device request
- confirm V1 health and read-only MCP still function independently
- then one owner-authorized create -> read -> send -> get_run chain
- test wrong account, wrong runtime, missing grant and stale session failures
- stop/rollback Next alone if PSI/IO/OOM or V1 latency worsen
- retain all receipts; unknown results never become success by assertion

Avoid concurrent model stress tests, global swapoff, shared service restarts,
wildcard operator grants, copying subscription credentials, or global
bypassPermissions. The default DeepSeek model route labelled "admin" is a
gateway routing choice, NOT OS root/admin. The official subscription CC and
MarketOps CC identities require separate owner-reviewed configuration.

## Meaning of done

CODE, UNIT TESTED, REAL DEVICE VERIFIED, PLUGIN INSTALLED and CUTOVER are
separate statuses. A dry run cannot close the real-client and consent gates.
As of this writing none of these steps deploys Next or replaces V1.
