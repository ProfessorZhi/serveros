# Fleet V1 — Dell `vince` → `fjd` runtime cutover: plan and read-only inventory

Branch `feat/fleet-v1-dell-fjd-cutover-20261015` (from #201 `1243f2d`)
Author: the Dell-managed CC on this host, running as **`vince` (UID 1002)**

---

## 0. Authorization state — read this first

> **`FJD_OWNER_AUTH_PENDING`**

The requester has said they want the migration and want `fjd` to carry the
formal runtime. **Nothing in this session establishes that the requester can act
as `fjd`'s OS owner** — i.e. that they may log in as `fjd`, or authorise an
administrator to change `fjd`'s account. The architecture comment says so
explicitly, and this plan does not upgrade a stated wish into an authority.

Consequences, all of which hold for this document:

* **Nothing was executed.** No account was switched, no `su`/`sudo`, no `chown`,
  no linger change, no service started or stopped, no file moved or copied.
* **`fjd`'s home was not entered.** `/home/operator` is `0750 fjd:fjd`; `vince`
  cannot read it and did not try. Every `fjd` fact below comes from public
  metadata (`getent passwd`, `loginctl show-user`) or from the architecture
  comment.
* **No credential was read, printed or copied**, from either account. Section 2
  records secret-bearing paths as *categories* — path, mode, owner — and no file
  content.
* **The device is not migrated.** The deliverable is a design, a manifest
  template and offline negative tests.

---

## 1. Read-only inventory — what `vince` owns

Measured 2026-10-15. Scope is deliberately narrow: `vince`'s own ServerOS/fleet
Git trees and the services this account runs. Other projects, market/Strata/
model data and personal material were not scanned.

### 1.1 Git trees (path / kind / branch / HEAD / dirty)

| Tree | Kind | Branch | HEAD | Dirty |
|---|---|---|---|---|
| `fleet-v1-dell-e2e-20261015` | repo | `feat/fleet-v1-dell-runtime-e2e-20261015` | `1243f2d` | 0 |
| `fleet-v1-ingress-20261015` | worktree | `feat/fleet-v1-independent-ingress-20261015` | `074f836` | 0 |
| `fleet-v1-smoke-20261015` | worktree | `feat/fleet-v1-preflight-smoke-20261015` | `d4f4e5c` | 0 |
| `fleet-v1-fjd-cutover-20261015` | worktree | `feat/fleet-v1-dell-fjd-cutover-20261015` | `1243f2d` | 0 |
| `fleet-runtime-identity` | repo, **no remote** | `runtime-identity` | `583ae9a` | 0 |
| `fjd-node-staging` | repo, **no remote** | `fjd-node-staging` | `8b1fbc4` | 0 |
| `fleet-create-preflight` | plain dir | — | — | n/a |
| `serveros-fleet-node` | plain dir | — | — | n/a |

All four pushed branches have `origin` = the `serveros` remote and **0 unpushed
commits**. The two remote-less repos are **local-only** and are therefore class
`B`, not class `A`: their history exists on exactly one disk.

`serveros-fleet-node/` is the **deployed** legacy connector's source tree (not a
Git tree at all) — the code the running `serveros-fleet-node-connector.service`
executes. It is class `F`.

### 1.2 User-level services this account runs

| Unit | State | Note |
|---|---|---|
| `cloudcli-trial.service` | active running | loopback `127.0.0.1:3001` |
| `llmlab-fleet-tunnel.service` | active running | the Dell→Tokyo tunnel |
| `serveros-fleet-node-connector.service` | active running | loopback `127.0.0.1:3402`, serves `device:llm-lab-dell` |

All three were left running. **None may be stopped, restarted or repointed by
this plan**; retirement is section 6, step 10, by owner decision.

### 1.3 Secret-bearing paths — categories only

Recorded as **path, mode, owner**. No content was read and none is reproduced.

| Path | Mode | Owner | Migratable |
|---|---|---|---|
| `/home/vince/.ssh` | `700` | `vince:vince` | **no** |
| `/home/vince/.config/gh` | `771` | `vince:vince` | **no** |
| `/home/vince/.cloudcli` | `700` | `vince:vince` | **no** |
| `/home/vince/.config/serveros-fleet-mcp` | `700` | `vince:vince` | **no** (legacy binding) |
| `/home/vince/.config/serveros-fleet-node` | `775` | `vince:vince` | **no** |
| `/home/vince/.local/state/serveros-fleet-node` | `700` | `vince:vince` | **no** |
| `/home/vince/.config/systemd/user` | `755` | `vince:vince` | **no** |
| `/home/vince/.local/opt/cc-cloudcli` | `700` | `vince:vince` | **no** |
| `/home/vince/.local/opt/node-22` | `777` | `vince:vince` | **no** |

This session's `HOME` is overridden to
`/home/vince/projects/.cloudcli-trial/home`, which additionally carries
`.claude`, `.claude.json`, `.claude-code-ui`, `.codex`, `.cloudcli`, `.cursor`,
`.local`, `.npm`, `.nv`, `.gitconfig`. Same rule: **categories only, never
migrated.**

`/home/vince/.claude` and `/home/vince/.codex` are **absent** outside the trial
overlay — noted so nobody goes looking for a directory that is not there.

### 1.4 `fjd` — public metadata only

| Fact | Value | Source |
|---|---|---|
| account | `fjd` | `getent passwd` |
| UID / GID | `1000` / `1000` | `getent passwd` |
| home | `/home/operator`, mode `750 fjd:fjd` | `stat` (metadata; contents NOT read) |
| shell | `/bin/bash` | `getent passwd` |
| linger | `no` | `loginctl show-user` |

`/opt/strata` carries production Strata/Qwen work under `fjd`. **In place. Not
moved, not restarted, not scanned.**

---

## 2. Migration classes

The unit of migration is a **reviewed asset**, never a directory tree that
happens to contain one. The classifier is
`fleet_mcp/deploy/dell_fjd_cutover.py`; the template is
`fleet_mcp/deploy/dell_fjd_cutover_manifest.example.json`.

| Class | Meaning | Executable by whom |
|---|---|---|
| **A** | pushed Git source / PR / version docs | `fjd` **clones/fetches/checks out** with its own git credential. Never copied. |
| **B** | unpushed local WIP | `vince` first commits it or writes a **credential-free patch**; `fjd` imports after owner approval |
| **C** | credential-free templates / necessary project records | owner-approved, narrow, verified |
| **D** | `.ssh`, `.config/gh`, `.claude`, `.codex`, `.cloudcli`, OAuth store, node secret, CloudCLI auth, session DB, Tailscale state/socket | **never migrated** — `fjd` initialises its own |
| **E** | Strata / Qwen / GPU production, other accounts' directories | **in place, untouched** |
| **F** | legacy `vince` connector / old sessions | isolated for reference; retired only by owner approval after `fjd` verifies |

Two rules in the classifier are worth stating because the tempting version of
each is a real accident:

* **A credential is refused by what it is, not by what the manifest calls it.**
  `_is_credential()` walks the path components and suffixes, so declaring
  `/home/vince/.ssh` as class `A` is refused rather than believed. Component
  matching is on whole components, so `my.ssh-notes` is *not* refused — a rule
  that fired on substrings would be widened until it fired on everything.
* **A home is never the unit.** A path equal to either home, or an ancestor of
  one (`/home`, `/`), is refused; a path *below* a home is exactly what the
  exercise is for. `mv ~`, a bulk `rsync`, and "copy the whole tree" are the
  same refusal.

---

## 3. Address and runtime model

`device:llm-lab-dell` is **one physical, authenticated device**. Changing which
OS account carries it does not create a second device, and the account rides in
the provider tag.

### 3.1 A legacy address keeps its meaning

`device:llm-lab-dell|claude|<nativeId>` was minted for `vince`. Every historical
ref says exactly that, and **it keeps denoting `vince`**. The bare `claude` tag
is a **spelling baseline**, not a pointer to be repointed when the production
account changes. The classifier refuses, outright:

* `remap_legacy_tag` — re-pointing `claude` at another account;
* `legacy_base_owner` ≠ `vince`;
* `legacy_base_tag` ≠ `claude`;
* a `device_default_account` field at all.

### 3.2 The new account arrives under its own tag

The Next matrix requires `claude-fjd` (`codex-fjd` only if Codex is actually
supported on that runtime — not assumed). A tag must be suffixed; the bare tag
is taken, and a tag naming the account being retired (`claude-vince`) is refused.

A device may set `preferred_runtime: claude-fjd` as a **UX default**, and only
when the manifest states in the same breath that the preference grants nothing
(`preferred_runtime_is_ux_only: true`). It is not a silent alias and it does not
inherit a grant.

### 3.3 `required_runtimes` drops the account, not the capability

`required_runtimes` must not contain the bare `claude` runtime. But
`multi_account_capable` must remain **true**: removing one account from *this*
device's matrix must not remove the generic code path that supports 0..N
runtimes per device. That capability is what the D-lane negative tests exercise
and what Lane B's `instance_id` work will land into.

### 3.4 Advice for the Runtime lane

Two distinct concepts, and conflating them is the failure:

* `legacy_base_tag_owner` — **who the unqualified tag was minted for.** Fixed
  historical fact. Never changes because a device changed hands.
* `preferred_runtime` — **which runtime a UI should offer by default.** A
  presentation choice, expected to change, carrying no authority.

A `device_default_account` that rewrites the legacy tag space is the thing to
avoid: it looks like a configuration convenience and behaves like re-addressing
history.

---

## 4. What must never happen

* `mv /home/vince /home/operator`, a bulk `rsync` of either home, or any whole-tree copy.
* Copying `.ssh`, `.config/gh`, `.claude`, `.codex`, `.cloudcli`, an OAuth store,
  a Tailscale state dir or socket, a node secret, a CloudCLI credential or a live
  session database.
* `sudo`, `su`, `chown`, or any permission bypass to reach `fjd`'s home.
* Copying `vince`'s git credential into `fjd`. `fjd` uses its **own** identity.
* Re-pointing the bare `claude` tag, or writing `device_default_account=fjd`.
* Moving, restarting or scanning `/opt/strata` or any GPU production work.
* Deleting the `vince` OS account. It is retired from the *matrix*, not from the machine.
* Enabling linger on `fjd` without the OS owner's explicit approval.
* Reporting `fjd` as connected because a `vince` session said so.

---

## 5. The manifest

`dell_fjd_cutover_manifest.example.json` is the template. It is checked by
`dell_fjd_cutover.py`, which is pure and offline: `writes: []`, `executes: []`
always, `passwd_home` **injected** rather than discovered, and no import of
`subprocess`, `os`, `shutil`, `pwd`, `grp`, `socket` or `pathlib` — asserted by
test against the module's namespace rather than its prose.

```bash
python3 -I fleet_mcp/deploy/dell_fjd_cutover.py \
        --manifest fleet_mcp/deploy/dell_fjd_cutover_manifest.example.json
# decision: FJD_OWNER_AUTH_PENDING
# executable_now: []
```

`FJD_OWNER_AUTH_PENDING` is a decision, not an error: the plan is well-formed
and nothing in it may act, because the owner confirmation is absent. Adding it
without `evidence` is refused — "the requester asked for it" is not the account
owner saying they can act as `fjd`.

---

## 6. Staged runbook

Every step is performed by **`fjd` under its own login**, or by an administrator
its owner authorises in a change window. None is performed by this tooling, and
none by the source account.

| # | Step | Who | Gate |
|---|---|---|---|
| 1 | **Owner identity / authorization check** — confirm the account owner, and that whoever acts can act as `fjd` (`id`, `getent passwd fjd`) | `fjd`'s owner | clears `FJD_OWNER_AUTH_PENDING` |
| 2 | **Read-only inventory + manifest** — this document and the checked manifest; no writes | any | — |
| 3 | **`fjd` logs in interactively** and creates a minimal HostProfile workspace under its own `$HOME` (`src/`, `worktrees/`, `test-envs/`, `ops/`, `records/`, `knowledge/`) | `fjd` | own login; no credential copied in |
| 4 | **Import class `A`** — `git clone`/`fetch`/`checkout` from the remote using **`fjd`'s own** git credential | `fjd` | class `A` only |
| 5 | **C++ Native Core observe-only + restricted CloudCLI / FleetNode install**, per-UID, user-level units; Windows gets a different adapter — never the Linux template | `fjd` | no GPU action; no restart of existing production |
| 6 | **Tokyo Next master: explicit new binding + operator grant**, `read` only, addressed to `claude-fjd` | master operator | exact grant, no wildcard |
| 7 | **Real model / permissionMode readback** on `fjd`'s runtime | `fjd` | UNKNOWN until measured, never assumed |
| 8 | **One authorised low-cost session create/send** on `claude-fjd` | operator + `fjd` | legacy `claude` refs untouched |
| 9 | **Confirm Strata / GPU production unaffected**; confirm the old `vince` provider no longer appears in the new Next while historical refs keep their old meaning | operator | read-only |
| 10 | **User approves retiring the legacy `vince` connector**; only then stop it and withdraw its grant — **never delete the OS account** | user + owner | explicit approval, separate from this plan |

`loginctl enable-linger fjd` is **not** in this runbook as an action. `fjd` is
`Linger=no`; enabling it is an independent administrative operation requiring
the OS owner's explicit approval, and **it is not executable in this round.**

---

## 7. Tests

`tests/test_fleet_v1_dell_fjd_cutover.py` — **50 offline cases**, all passing.
Negative-first:

* every credential path declared as migratable is refused, by class not by trust;
* a credential under the **target** home is refused too;
* the source home, the target home and `/home` are refused as units; a narrow
  path inside a home is accepted;
* `remap_legacy_tag`, a re-stated `legacy_base_owner`, a re-spelled base tag, a
  bare next tag and a `claude-vince` tag are each refused;
* `required_runtimes` containing the bare `claude`, a dropped
  `multi_account_capable`, a preference without `preferred_runtime_is_ux_only`,
  and any `device_default_account` are refused;
* UID/home disagreement with injected passwd, an unknown user, the wrong source
  account and a second `device:` ref are refused;
* class `B`/`D`/`E`/`F` assets are never `migratable` and never `executable_now`;
* `SystemExit`-free purity: the manifest is not mutated, two evaluations agree,
  `writes`/`executes` stay empty, and a manifest carrying a secret field is
  refused **without the value appearing in the output**.

```bash
python3 -I tests/test_fleet_v1_dell_fjd_cutover.py    # Ran 50 tests ... OK
```

The suite found one real bug while being written: the home-scope rule originally
refused every asset *below* a home, i.e. all of them, which would have looked
strict while making the migration impossible. It is now "the home itself or an
ancestor", with a test pinning both halves.

---

## 8. Blockers and status

| Item | Status |
|---|---|
| Read-only inventory of `vince`'s trees / services / secret categories | **DONE** |
| Migration classes, address model, runbook | **DONE (design)** |
| Offline negative tests | **PASS** (50) |
| **`fjd`'s OS-owner authorization** | **BLOCKED — `FJD_OWNER_AUTH_PENDING`** |
| `fjd` login, workspace, CloudCLI/FleetNode install | **NOT DONE** |
| Next binding `claude-fjd` + operator grant | **NOT DONE** |
| model / permissionMode readback on `fjd` | **UNKNOWN — never measured** |
| linger on `fjd` | **NOT DONE — needs owner approval** |
| Legacy `vince` connector retirement | **NOT DONE — needs user approval, step 10** |

**`fjd` is not connected.** Nothing on this device was migrated, and no
`vince`-side observation can establish that `fjd` has joined the fleet.

### The one step the owner must take

**Confirm, as `fjd`'s OS owner, that the migration is authorised — and say what
that confirmation rests on.** Until then every step in section 6 is blocked, and
the manifest's `owner_authorization` block is the only thing standing between
this plan and step 3.

---

## 9. V2 boundary

OpenAgents, tier formation, long-lived Ability autonomy and full resource
governance are out of scope for V1. The Core-Lite here is read-only health
sampling; it is not an execution permission and never becomes one by being
reachable.
