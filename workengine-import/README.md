# ServerOS work engine

`work-engine` is the ServerOS v3 runtime control plane. It joins information
intake, executable work, execution evidence, and feedback without taking over
the native facts owned by Git, GitHub, Feishu, or local execution systems:

```text
Source → Observation → Claim → Candidate Item → Item
       → Activity → Run → Evidence → Deliverable → Feedback
                                                └→ new Observation
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

Both entry points invoke the v3 CLI; `serverosctl` is canonical.

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
serverosctl --json feedback record SRV-0001 \
  --observation obs-codex-run-0001 \
  --kind agent-verification --locator /absolute/path/feedback.json
serverosctl --json board render --project serveros \
  --output-dir /absolute/path/records/projects/serveros/board

# Register a metadata-only Feishu or GitHub inventory and generate drift proposals.
serverosctl --json federation feishu inventory \
  --source /absolute/path/inventory/feishu-objects.jsonl \
  --target /opt/applications/data/home-hwz/state/serveros/federation/feishu/current.jsonl \
  --evidence-run SRV-0001/RUN-0001
serverosctl --json federation feishu proposal \
  --observed /absolute/path/inventory/feishu-objects.jsonl \
  --desired /absolute/path/feishu-desired.jsonl \
  --evidence-run SRV-0001/RUN-0001
```

The information-to-work handoff is explicit: `source`, `observation`, and
`claim` preserve provenance; `candidate` records matching, promotion, merge,
rejection, or deferral before an Item is created. `federation` accepts only
metadata and locators, writes a latest successful projection atomically, and
records its projection hash and evidence Run in a publication receipt. Drift
commands emit schema-valid proposals rather than changing Feishu or GitHub.
Inventory rows are validated against the bundled copy of the hwz-ops
`federation-object.v1` contract before the current projection is touched.
Publication uses a same-directory stage, process lock, backup, and recovery
journal. The journal binds a versioned transaction nonce, canonical target,
restricted same-directory stage/backup names, old/new hashes, and the previous
file metadata. Recovery rejects corrupt or forged journals without modifying
the current file. A receipt failure restores the previous bytes, mode, times,
owner/group, and extended attributes; an interrupted publication can only
resume with the same evidence Run and hash, while a different payload for an
existing publication identity fails closed. The Python `inventory()` helper is
validation-and-hashing only; safe publication is exposed through the managed
CLI path, which supplies the durable receipt callbacks.

Execution-context revisions remain authoritative per repo. The revision passed
to `run finalize` describes the aggregate Run evidence and does not rewrite the
0..N attached repo contexts.

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

`SUCCEEDED` and `FAILED` are verdicts produced by `run finalize`. `CANCELLED`
and `INVALID` are operator terminations and are reached only through
`run cancel` or `run abandon`, which require a reason and an actor and append a
`run_terminated` record to the Item's event history. `run advance` refuses them,
so a termination can never be recorded without why and by whom.

`abandon` is not a state: it is the operation that lands in `CANCELLED` with
`reason_code=ABANDONED`. Terminating a Run is a control-plane fact only — it
never changes the Item, deletes artifacts or worktrees, or touches any runtime
resource. `CANCELLED` and `INVALID` are legal from every non-terminal state;
`INVALID` records that the Run's result must not count. A Run in `RUNNING`
additionally requires `--acknowledge-active-worker`, because the control plane
holds no worker handle and cannot observe or stop a worker process.

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
