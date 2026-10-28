# Fleet Next Wave 18 — independent release acceptance

This directory is a **gate, not a build**. It measures whether the Wave 18 release
claim is true and prints one of three verdicts. It does not install anything, does
not start or stop a service, does not touch systemd, OAuth, credentials, Tailscale,
Feishu, the Legacy Fleet or a model endpoint, and it cannot be made to.

```
READY      exit 0   every provable layer measured, and measured clean
BLOCKED    exit 2   a layer measured something that contradicts the claim
INCOMPLETE exit 3   a layer could not be measured
```

`REAL_DEVICE` is always `NOT_RUN`. A device pass is a separate authorization from
the Owner, not a flag on this script; `build_real_device_layer(allow=True)` returns
`NOT_RUN` too, so a caller who tries to turn it green gets the refusal back.

## The six layers

| layer | what it proves | how |
|---|---|---|
| `SOURCE` | the six pinned heads exist, descend from the base, and every path the release claims is a file at that commit | `git cat-file` / `ls-tree` / `merge-base` against the pin's SHAs |
| `UNIT` | each unit's own tests pass **at its pinned commit**, not in a worktree | a fresh `git clone` per unit, `checkout <sha>`, `pytest <unit_tests>` in a scrubbed environment |
| `HOSTED_CI` | the baseline gate printed a summary at the exact head, and every `NEW FAILURE` is attributed | `gh api` for runs at `head_sha`, then the shard job's log, parsed |
| `BUILT_ARTIFACT` | published bytes exist and hash to a digest someone else can check | artifact discovery by pinned name + `sha256` verification |
| `LEGACY_SURVIVAL` | a Next package can be installed, observed, upgraded badly and revoked **without moving Legacy** | six independent processes in one disposable directory |
| `REAL_DEVICE` | nothing | constant `NOT_RUN` |

A layer's `PASS` means *that layer's own question* was answered, not that the
release is safe. `aggregate` still refuses `READY` if any layer carries a
`BLOCKER` finding — a drill that worked and found a trap is a passing drill and a
blocked release, and the verdict says both.

## The pin is the claim

`release_pin.json` is the only input. `validate_pin` returns every way the document
is *not* a release claim: a floating version, a short commit, a duplicate unit, a
package file with no pinned source, a path that leaves the package, a contract that
does not name `get_session`/`get_run`/`send`, an artifact name with no digest.
An empty tuple means it is a claim. A pin that fails validation is
`INCOMPLETE`/`BLOCKED_PIN` — never a pass with a caveat.

## How to run

```bash
# offline: SOURCE + UNIT + BUILT_ARTIFACT + LEGACY_SURVIVAL, no network
python integrations/fleet-next-wave18-release-acceptance/wave18_acceptance.py \
    --repo /path/to/serveros \
    --pin  integrations/fleet-next-wave18-release-acceptance/release_pin.json \
    --offline --table --json-out /tmp/verdict.json

# with the hosted run read through gh, and the builder's own code re-run
python integrations/fleet-next-wave18-release-acceptance/wave18_acceptance.py \
    ... --rebuild
```

- `--workdir` keeps the drill world for inspection; without it a temp directory is
  made and removed. The world is refused if it resolves inside the repository.
- `--platform` defaults to `sys.platform`. Units that declare another platform are
  recorded `NOT_RUN`, not skipped silently.
- `--rebuild` re-runs the artifact builder's own code at its recorded source
  commit. That is a **reproducibility** check, never independence: the code doing
  the building made the claim, so agreement means "the checksum file and the commit
  agree", not "these bytes are the release". It can never turn `BUILT_ARTIFACT`
  green.

The tests for this gate are `tests/test_fleet_next_wave18_release_acceptance.py`
and run on Windows as well as Linux. Two of the pinned units are Linux-only
(`fcntl`), so on Windows the `UNIT` layer covers four units and records the other
two as `NOT_RUN` with their declared platform.

## The drill

`drill/` holds three pieces, all stdlib-only, all writing only inside the world:

- `fixture_engine.py` — a stand-in for #506's installer used by the gate's own
  tests. It reproduces the pinned engine's semantics deliberately, **including**
  the order in which `install()` writes files. It is evidence about this harness,
  not about the release.
- `legacy_gateway.py` — a file-backed Legacy witness: ten identity fields, the three
  contract verbs, an append-only delivery ledger. No socket, no port, no service.
- `drill_step.py` — one step, one process. `wave18_acceptance.py` runs
  `seed → install → observe → failed-upgrade → rollback → probe` and requires at
  least three distinct pids.

Every step's Legacy snapshot must equal the seed snapshot exactly: identity
(instance, route, boot, port, backend URL, OAuth audience, db, state root, lock,
clock), the digested answers of `get_session`/`get_run`/`send`, the delivery list,
and the tree digest. `send` is idempotent by key, so a repeated check cannot become
the second delivery it was meant to notice.

`probe` injects one fault at a time, each on a fresh root, and each has exactly one
expected answer:

| injection | expected |
|---|---|
| `bad_sha` | `BLOCKED_SOURCE_DIGEST` |
| `foreign_boot` | `BLOCKED_BOOT_MISMATCH` |
| `unauthorized_device` | `BLOCKED_IDENTITY_MISMATCH` |
| `missing_receipt` | `BLOCKED_NO_RECEIPT` |
| `legacy_root_collision` | `BLOCKED_LEGACY_ROOT` |
| `resource_collision` | `BLOCKED_RESOURCE_COLLISION` |
| `resource_clean` | `OK` |
| `offline` | `OFFLINE_REFUSED` |
| `no_grant` | `BLOCKED_NO_GRANT` |
| `not_pinned` | `BLOCKED_NOT_PINNED` |
| `partial_write` | a real partial write, measured rather than refused |

A refusal with the *wrong* code is a failure, and so is an `OK` where a refusal was
expected: "it said no" is not enough, it has to say which no.

## Findings

A `Finding` is a measured defect that travels with the verdict instead of into it:
`code`, `severity` (`BLOCKER`/`NOTE`), `summary`, `owner` (the PR whose Owner must
act), `file`, `condition` (what has to be true to close it), `evidence`. A `BLOCKER`
forces `BLOCKED` even when every layer passed. That is how "the drill worked, and it
found a trap" is reported without either hiding the trap or pretending the drill
failed.
