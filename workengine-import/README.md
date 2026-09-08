# ServerOS work engine

`work-engine` is the ServerOS v2 runtime control plane. It tracks execution,
not business prose:

```text
Observation / Claim (records information)
            ↓
Item card (records project)
            ↓
Activity DAG (declared plan + runtime projection)
            ↓
Run (SQLite lease, attempt, context, evidence path)
            ↓
Deliverable locator and feedback
```

An Item is not a PR and does not require a worktree. One Item may own zero or
more repo contexts, test environments, Runs, and deliverables. Git worktrees
are preferred only when source isolation or a fixed revision is required.

## Authority boundaries

- stable Item intent: `records/projects/<project>/items/<ITEM>/card.yaml`
- runtime state: `/opt/applications/data/home-hwz/state/serveros/serveros.db`
- evidence: `/opt/applications/data/home-hwz/artifacts/work-items/<project>/<ITEM>/<RUN>/`
- code truth: Git
- test environment definition: `workspace/test-envs/<repo>/<ITEM>/`

Relative state and artifact roots are rejected. The old v0 JSON format is not
read by normal commands; filesystem cutovers use the explicit migration
journal only.

## CLI

Both entry points invoke the v2 CLI; `serverosctl` is canonical.

```bash
serverosctl --json item create \
  --project serveros --prefix SRV --title "Converge workflows" --kind ops

serverosctl --json activity add SRV-0001 implement --kind develop

serverosctl --json context attach SRV-0001 implement \
  --repo work-engine --mode develop --revision "$(git rev-parse HEAD)" \
  --worktree /opt/applications/data/home-hwz/workspace/worktrees/work-engine/SRV-0001

serverosctl --json run begin SRV-0001 implement \
  --worker codex --idempotency-key srv-0001-control-plane

serverosctl --json item outcome SRV-0001 --set SUCCESS
serverosctl --json deliverable add SRV-0001 \
  --kind report --locator /absolute/path/report.md
serverosctl --json board render --project serveros \
  --output-dir /absolute/path/records/projects/serveros/board
```

Item states are:

```text
INBOX → AVAILABLE → READY → ACTIVE → DELIVERED → CLOSED
```

`WAITING`, `NEEDS_HUMAN`, and `DELEGATED` require a reason, return state,
and unblock condition. `CANCELLED` and `CLOSED` can only be restored through an
explicit reopen event. `outcome` is independent from state.

Run states are strongly linear:

```text
ALLOCATED → PREPARING → RUNNING → FINALIZING
          → SUCCEEDED | FAILED | CANCELLED | INVALID
```

## Migration journal

Migration manifests are JSON and contain absolute move operations. Layered
targets are allowed only when the parent move comes first and the nested target
is new. `plan` hashes every source and refuses occupied or unsafe targets.
`apply` persists a cursor after every move and uses copy/hash/fsync/atomic
publish across filesystems, so a new process can resume. `compensate` reverses
only hash-matching completed operations.

```bash
serverosctl --json migrate plan --manifest /absolute/path/migration.json
serverosctl --json migrate apply multi-user-claw
serverosctl --json migrate verify multi-user-claw
```

## Verification

```bash
python3 -m unittest discover -s tests -v
```

The suite exercises real SQLite files and real filesystem moves, including
concurrent Item allocation, DAG cycle rejection, idempotent Run begin,
cross-process resume, strict artifact boundaries, crash recovery, and reverse
compensation.
