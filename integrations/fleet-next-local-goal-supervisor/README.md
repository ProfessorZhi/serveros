# Fleet Next — Tokyo-local Goal Supervisor

A user-scope, single-instance, crash-recoverable tick loop that watches the
Fleet Next V1 closure surface after ChatGPT's scheduled tasks are gone.

It is **not** the product's Agent Monitor. The Agent Monitor records what
agents did and delivers receipts; this process decides when the V1 goal needs
another independent task, and refuses to decide when it cannot prove the
facts.

## Why a fallback, not "Goal Mode"

Checked on this node (Claude Code 2.1.308, 2026-10-29):

| Claim | Verdict |
|---|---|
| A `claude goal` / Goal Mode subcommand | **Does not exist.** `claude --help` has no such command. |
| A native long-running background session primitive | **Exists:** `claude --bg`, `claude agents --json`, `claude attach/logs/stop/respawn/rm`. |
| A local Fleet MCP transport | **Exists:** `serveros-fleet-next-mcp.service` on `127.0.0.1:3411/mcp`, OAuth-gated. |
| A credential the daemon may use without forging one | **Not available:** `auth.pat_enabled: false`. |

So this module is the fallback the owner's instruction explicitly permits,
and it is honest about the one gap it cannot close by itself.

## What it observes, and what it refuses to invent

| Source | Mechanism | When it fails |
|---|---|---|
| GitHub HEAD / open PRs / watched issues | `git rev-parse`, `gh pr list`, `gh issue view` | `state: UNKNOWN` + error |
| Exact-head CI per watched PR | `gh pr checks --json` | per-PR `verdict: UNKNOWN`, never `PASS` |
| Local Claude sessions | `claude agents --json` | `state: UNKNOWN` |
| Fleet sessions across devices | a snapshot file written by an MCP-authorized producer | `UNKNOWN` (absent) or `STALE` (old) |

A source that did not answer never becomes green. A PR whose checks have not
started is `UNKNOWN`, not `PASS`.

## Safety properties (each has a test)

- **One supervisor.** `flock` on `goal.lock`. A stale PID file is not a lock.
- **Resume, not restart.** A changed kernel boot id increments `resume_count`
  and keeps the cursor and dispatch ledger.
- **No double dispatch.** A dispatch whose outcome was never read stays
  `DISPATCH_ACKED_UNOBSERVED` and suppresses new work.
- **Never message a running agent.** `RUNNING_AGENT` sessions are recorded in
  `refused`; only `MANAGED_IDLE` / `RESIDENT_HOST` are offered.
- **The cap counts existing work.** `max(observed RUNNING_AGENT, active ledger)`
  is compared with `max_inflight` (default 3).
- **Dispatch is off by default.** Turning it on requires both
  `dispatch.enabled: true` and a named verified provider; otherwise the tick
  returns `BLOCKED_ACTIVATION` with `NO_VERIFIED_DISPATCH_PROVIDER`.
- **Kill switch.** A `STOP` file ends the loop; `resume` removes it.

## Commands

```bash
S=integrations/fleet-next-local-goal-supervisor/supervisor.py
STATE=$HOME/state/fleet-next-local-goal-supervisor
CONF=$HOME/.config/fleet-next-local-goal-supervisor/config.json

python3 $S --state-dir $STATE --config $CONF once       # one tick, prints receipt
python3 $S --state-dir $STATE --config $CONF run        # long-horizon loop (systemd ExecStart)
python3 $S --state-dir $STATE --config $CONF status     # state + heartbeat, no lock taken
python3 $S --state-dir $STATE stop                      # write STOP (kill switch)
python3 $S --state-dir $STATE resume                    # remove STOP
python3 $S --state-dir $STATE verify-lock               # exit 0 if the lock is free
python3 $S --state-dir $STATE --config $CONF plan       # observe + decide, write nothing
python3 $S snapshot --raw raw.json --out $STATE/fleet-sessions.json --producer <id>
```

## State layout

```text
$HOME/state/fleet-next-local-goal-supervisor/
├── goal.json              # goal id, boot epoch, cursor, dispatch ledger, counters
├── goal.lock              # flock target (metadata is informational only)
├── heartbeat.json         # pid, boot epoch, last_tick_iso, decision, source states
├── receipts/<ts>-<id>.json# one immutable receipt per tick
├── fleet-sessions.json    # the snapshot seam (producer-stamped)
└── STOP                   # present only while stopped
```

## Tests

```bash
python3 -m pytest tests/test_fleet_next_local_goal_supervisor.py -q
```

## Known blocks

- `BLOCKED_ACTIVATION` for live dispatch: no verified dispatch provider.
- Fleet session freshness depends on an MCP-authorized producer writing the
  snapshot; the daemon cannot authenticate to the Fleet MCP itself because the
  local PAT is disabled. Enabling that PAT is an owner decision, not a code
  change.
- `device:personal-dell` has no provider binding on this runtime; it is
  `UNKNOWN_DEVICE`, not offline.
