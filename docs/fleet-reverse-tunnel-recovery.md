# Fleet reverse-tunnel recovery

Owner decision, 2026-10-21: keep the deployed public SSH reverse tunnels.
Tailscale remains a separately measured alternative. Future V1 transport
selection must compare real session requests and recovery, not SSH setup time
alone. Same-host Tokyo provider calls stay on loopback.

## Prevent stale remote listeners

Client `ServerAliveInterval=30` / `ServerAliveCountMax=3` bounds how long the
device keeps a dead connection, but cannot make the server discard its copy.
Configure server-side `ClientAliveInterval 30` and `ClientAliveCountMax 3` for
the Fleet SSH account. Validate with `sshd -t` and `sshd -T -C ...`, then reload
sshd. Existing connections need a controlled reconnect to pick up the policy.
Keep the rule scoped to the Fleet account; do not restart unrelated services.

A `remote port forwarding failed` error proves that forwarding was refused,
not which process owns the port. Inspect the exact listener with privileged
`ss -ltnp` before intervening. Do not use a broad `pkill sshd` or terminate all
session-less logins: other tunnels can belong to the same account.

## Windows process lifetime

Scheduled tasks must remain attached to the actual launcher. Use the bundled
`run-hidden-wait.vbs`, which waits for PowerShell and propagates its exit code.
A fire-and-forget hidden wrapper makes Task Scheduler's IgnoreNew and failure
restart settings ineffective. The installer uses normal priority (5),
IgnoreNew and unlimited task execution time for both Fleet tasks.

The Node Connector uses exclusive socket binding on Windows. Duplicate
launchers must fail to bind rather than share a live listening port. Existing
deployments need their own connector file patched or upgraded; updating the
repository alone does not update running processes.

Before applying a launcher change, save task XML and deployed files locally.
Stop only the exact Fleet task/supervisor and its verified child. Preserve all
runtime sessions and CloudCLI processes. Restart the Connector and its tunnel,
then confirm one listener and one task-owned process tree. Use the recorded
tunnel child PID together with its command line, not a broad SSH process match.

## Verification

Verify signed `/v1/self`, upstream health, a small Session list and a read from
an existing Session through the remote forwarded port. Verify each live MCP
surface separately, including Next/V1. Distinguish network reachability from
local CloudCLI latency; a successful self probe does not establish that Session
queries are fast. Task dispatch requires explicit authorization to message the
chosen Session. No synthetic dispatch is part of tunnel repair.

To exercise reconnect without dropping an active run, terminate only the
verified Fleet SSH child and wait for its supervisor to create a replacement;
confirm the same remote port and authenticated reads recover. A clean reconnect
does not simulate an unclean network partition; record that limitation.
