# Fleet V1 RC — staged rollout and rollback runbook

> **STATUS: PLAN ONLY. NOTHING IN THIS DOCUMENT HAS BEEN EXECUTED.**
>
> This is the manual a human follows *after* the RC is reviewed and *after* the
> owner approves. It is written so that each step has a stated pre-check, a
> stated evidence to keep, and a stated way back. Where the fleet does not yet
> have a reversible path, the step says so and stops.
>
> The companion record is [FLEET-V1-RC-20261027.md](FLEET-V1-RC-20261027.md).

---

## 0. The single owner-approval gate

Everything below is blocked on **one** decision, taken by the owner, once, in
writing. It is deliberately one gate: splitting it into per-step approvals makes
each step look small and the whole thing look safe.

> **GATE-OAUTH** — authorizes, *together*: completing an OAuth/browser login for
> the Fleet MCP, changing user services, naming the Feishu recipient, performing
> one real outbound send, and installing/updating any global (user-wide) package.

No agent may self-authorize any part of GATE-OAUTH. Without it, the rollout stops
after step 2 (read-only verification) and the fleet stays as it is today.

A second, separate decision is required for anything not covered above; if it is
not in GATE-OAUTH, it is not approved.

---

## 1. Pre-flight (read-only, no gate needed)

Run on each device. Nothing here writes.

1. Record `git rev-parse HEAD`, `git remote get-url origin`, `git status --porcelain`
   for every clone you intend to touch.
2. Record the **installed** component versions and digests, not the checkout's:
   node runtime, node connector, CloudCLI, ability package, commander Skills
   package. If a component cannot be read, record `NOT_OBSERVED` — never the
   source checkout's SHA in its place.
3. Confirm no active Run is in flight on a device you are about to change.
4. Confirm the RC candidate SHA matches the reviewed one, and that the reviewed
   one matches `main` plus the merged PR set in the integration record.

**Stop if** any inventory row would have to be guessed.

Known current state (from the #418 read-only inventory, not re-derived):

| Device | Connector | `serverosd` | Ability plugin | Commander plugin |
|---|---|---|---|---|
| Dell/fjd | installed, unit active | not installed | not installed | not installed |
| Tokyo | `NOT_OBSERVED` for the #394 ghost path | `NOT_OBSERVED` | `NOT_OBSERVED` | `NOT_OBSERVED` |
| Windows/fj | see #418 | see #418 | see #418 | see #418 |

## 2. Build each artifact from the exact reviewed SHA

Build **once**, on one machine, and promote the bytes. Rebuilding per device is
how two devices end up with "the same version".

```sh
# L1 node runtime — no Ability repo, no Skill pin
python3 scripts/build.py --face node-runtime --write \
    --output <staging>/node-runtime/<source_sha> \
    --marketplace-path <staging>/marketplace/node.json

# L3 device Ability plugin — requires the pinned ability-management revision
python3 scripts/build.py --face plugin --write \
    --ability-repo <ability-management check> --ability-rev <pinned rev> \
    --output <staging>/ability-plugin/<source_sha> \
    --marketplace-path <staging>/marketplace/plugin.json

# L4 commander Skills — regenerated, never hand-edited
python3 scripts/fleet_plugin.py check     # must print: committed plugin matches its source
```

Each face records its own version and cache identity, so the two artifacts can be
installed, upgraded and rolled back **independently** — a device rollback must not
move the node runtime.

Record for each: `source_sha`, `version`, `digest`, install path, and the
**previous** digest that step 5 will restore.

## 3. Canary one device, one component at a time

Order matters less than separation. Change one component, verify, then the next.

| Order | Component | Why this order |
|---|---|---|
| 3a | Node connector (L2-node) | pure routing; failure is visible in `fleet_status` |
| 3b | Commander Skills (L4) | carries no code; a Skill edit needs no service restart |
| 3c | Node runtime (L1 `serverosd`) | the only component that can hold a device lease |
| 3d | Device Ability plugin (L3) | affects what the device can do, not what it is |

After each: `fleet_status`, `fleet_list_devices`, and a read-only
`fleet_get_session` on a session you already own. A component that changes the
tool surface must be followed by a **fresh** `tools/list` read, not a cached one.

**Stop the rollout** if a canary shows the surface, the scopes, or the device
inventory changing in a way the release notes do not describe.

## 4. Milestone announcer — last, and separately gated

The announcer is the only component whose failure mode is *silence*, so it goes
last and it gets its own verification:

1. Deliver to a **test conversation**, not the owner's destination, until step 5.
2. Verify: a green milestone is delivered **exactly once**; a duplicate
   observation does not resend; an UNKNOWN delivery is `UNKNOWN`, not `DELIVERED`.
3. Verify the message body contains no prompt, transcript, token or PAT.
4. Only then, under GATE-OAUTH, point it at the owner's real destination and send
   **one** real message.

A send ACK is not an owner read receipt. Do not record `OWNER_READ`.

## 5. Rollback

Every step in §3 is reversible by restoring the previous digest recorded in §2.

| Component | Rollback |
|---|---|
| Commander Skills | reinstall the previous plugin version; **no service restart needed** |
| Node connector | restore previous connector digest, restart that user unit |
| Node runtime | restore previous runtime directory, restart `serverosd` |
| Ability plugin | restore previous plugin digest; must not move the node runtime |
| Announcer | disable the observer; its cursor/receipt ledger is not deleted, so a later restart resumes rather than re-sends |

Rules:

- Roll back **one** component at a time and re-run the §3 verification after each.
- Never roll back by `git reset`/`clean` in an active worktree. Build from the
  recorded SHA into a fresh staging path.
- If a send outcome is `UNKNOWN`, **do not resend**. Reconcile against the
  provider receipt first. A blind resend is how one milestone becomes two.
- If the rollback target digest was never recorded, the device is not in a
  rollbackable state — say so and stop, rather than reconstructing it.

## 6. Evidence to keep for acceptance

Per device, per component: `source_sha`, digest before and after, the verification
commands and their output, the CI job URL for the exact SHA, and the reason for
any `NOT_RUN`. Acceptance is per level — `CODE`, `UNIT`, `HOSTED_CI`,
`REAL_DEVICE`, `CLIENT_E2E`, `PRODUCTION` — and a green CI run does not stand in
for a device read-back.

## 7. What is still missing before V1 can be called delivered

- **#416 (CloudCLI 1.41 `run_get`)** has no V1-only path; the recorded schema
  still refuses the `deliveryId`/`run`/`wait` calls a 1.41 device accepts. Ships
  today as a declared `@unittest.expectedFailure`, not as a silent gap.
- **Anti-false-completion** for a device Run lives in `#407`'s `run_watcher.py`,
  which is out of V1 scope. Until an equivalent V1 rule exists, "the Run
  completed" must not be read as "the task succeeded" — the announcer treats a
  completion without trusted native output as `UNKNOWN`, and so must a human.
- **Tokyo #394 ghost completion** remains `UNKNOWN`. Do not re-dispatch an
  unknown Run to "find out".
- One authorized real Feishu milestone after GPT is gone, read back from the
  provider, is still required and still not done.
