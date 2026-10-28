# Wave 16 · Release-safety preflight for a Legacy/Next co-install

`wave16_preflight.py` answers one question in seven parts: **may Fleet Next be
released beside the Legacy Fleet that ChatGPT drives today, without the Legacy
routing, session, run or send contract moving — and can each piece be released
on its own and taken back?**

It is an offline decision surface plus one read-only repository reader (git
`ls-tree` / `rev-parse` / `cat-file`). It calls no service, opens no socket,
binds no port, installs nothing, reads no credential, touches no service
manager and sends nothing. A green run is a statement about the rules; it is
**not** a real-device pass, and nothing here may be reported as one.

## The eight resources

```
install_path  port  socket  backend_id  oauth_audience  cred_state_root  lock  unit
```

Reported **all at once** as `BLOCKED_RESOURCE_COLLISION`. One refusal per round
trip sends an operator back five times, and the fifth is where a gate gets
worked around.

| resource | why sharing it is a takeover |
|---|---|
| `install_path` | one code directory under two names is one release |
| `port` | one listener answering for two tracks |
| `socket` | the same, for a unix socket |
| `backend_id` | ChatGPT picks a connector by id; two tracks under one id are one connector |
| `oauth_audience` | equal audiences mean a token minted for one track verifies at the other |
| `cred_state_root` | the OAuth issuer's keys and the audit trail — one root, one authority |
| `lock` | two tracks serialising against each other is one track with extra steps |
| `unit` | one supervisor handle cannot describe two services |

### Overlap is cross-class

A different field name is not a different boundary. Next's `cred_state_root`
nested inside Legacy's `install_path` is one directory shared under two names,
so containment (slash boundary, both directions) is checked between **every
pair** of path fields — not only within a field. A unix socket inside another
track's root is the same defect with a different noun.

`/opt/fleet-next` is not inside `/opt/fleet`. `/opt/fleet/next` is.

### The one inverted polarity

Seven resources must differ. `device_ref` must be **equal**: both tracks run on
one host, and the host is what the binding declares. A second value means one
of the two bindings is about another machine, so it is its own refusal —
`BLOCKED_DEVICE_DISAGREEMENT` — rather than a collision. Folding it into the
collision list produces a matrix that blocks the correct answer.

This mirrors `fleet_mcp/deploy/next_install.py`, which refuses a canary start
when the Next binding's `device_ref` differs from live V1's for exactly this
reason.

## The shadow writes nothing

`shadow_tally(gateway, ["create", "send"], granted=[...])` refuses every write
verb **before** the gateway is reached, and granting a write verb does not
change that: a grant is permission to ask, not permission to become the other
controller. `writes` is counted by the ledger and `gateway_calls` is counted by
the gateway itself, so "the shadow wrote nothing" is measured twice.

A batch that only ever refused reports `SHADOW_NOTHING_OBSERVED`, not `OK`: a
shadow that observed nothing has said nothing about compatibility.

## The Legacy contract

`get_session`, `get_run`, `send` — the control path is not read-only, so a
co-install that preserved the reads and moved the send is the worst outcome.
`survival()` snapshots all three around each Next lifecycle step (install,
upgrade, rollback, failed install) and reports `LEGACY_CONTRACT_DRIFT` per verb.

`read_idempotence()` repeats only the two reads. Repeating a write to prove it
is stable is how a test becomes the second delivery it was written to detect.

A raised call is reported as **not** idempotent rather than as "equal": two
failures are not evidence that anything is stable.

## Timers

`timers_in(tree)` finds every `*.timer` in the tree. `timer_gate()` requires
each one to be both `enabled == False` and `active == False` — "not started
now" is not the property that matters, because an enabled timer starts itself
at the next login with no approval. A plan that would set either is refused
before anything runs. The gate only ever asks (`show`); `writes` and `executes`
are constants, not measurements.

## Reproducible release units: Hub, Node, Sidecar

`component_report(repo, ref, name)` answers three axes, each by a fact:

* **reproducible** — the manifest, computed twice from the same ref, is
  identical. Manifests are git **blob SHAs**, not hashes of worktree bytes: a
  blob hash is the same on every checkout, while a worktree hash moves with
  `core.autocrlf`. A manifest that changed when computed on Windows would not
  be a manifest.
* **revocable** — a revoke entrypoint is in the component's own file list, so
  undoing it does not depend on a runbook line.
* **independent** — no file in the component imports a module owned by another
  component. Relative imports are resolved (`from ..errors` in
  `fleet_mcp/node/connector.py` is `fleet_mcp.errors`), which is precisely why
  a grep cannot answer this question.

Verified against `main @ e69fc033` — see
`docs/integration/FLEET-NEXT-WAVE16-RELEASE-PREFLIGHT-20261028.md` for the
per-component SHA, file counts and the exact foreign imports.

## What this module does not do

* It does not install, start, stop, enable or reload anything.
* It does not read, hash or compare a credential *value*; `pat_anchor` appears
  only as a path the owner supplies.
* It does not prove a real host is safe. PSI, swap I/O, live cgroup limits and
  the actual OAuth round trip are measurements on the host and are named as
  such in `owner_inputs()`.
* It does not authorise a co-install. Real co-install needs separate user
  permission; this gate only refuses the shapes that cannot be safe.

## Usage

```bash
python integrations/fleet-next-wave16-release-preflight/wave16_preflight.py --resolve
python -m pytest tests/test_fleet_next_wave16_release_preflight.py -q
```
