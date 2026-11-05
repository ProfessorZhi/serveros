---
name: fleet-plugin-update
description: Update the App-backed Fleet plugin without moving its identity — same plugin id, an advancing version, Skills continuity, a readback and a rollback target — while keeping App-owned avatar/display changes separate and UNVERIFIED.
---

# An update is the same plugin; a rebrand is not

## At a glance

- **Use when:** shipping changed Skills or a new version of this plugin.
- **Input:** the previous release record and the new source tree.
- **Steps:** build the overlay → check identity/version → a person uploads and reads back → confirm the previous archive as the rollback target (commands below).
- **Done when:** same plugin id, higher version, every previous Skill still present, readback matches the plan.
- **Never:** move the plugin id; claim avatar/display changes as verified; change MCP URL or scopes here.

The sections below are the full contract behind each line.

The private App-backed Fleet plugin has an identity that an overlay may not
move: **plugin id**, `name`, `displayName` and the `apps` reference. An update
changes Skills, version and optionally assets. Changing the identity is a
different plugin wearing this one's release slot, not an update.

This Skill covers the **App-backed Skill/version update** path. It does not
cover, and must not be used to force, avatar or display-name changes.

## Two owners, two questions

| Change | Who owns it | What this workflow may say |
|---|---|---|
| Skills, version, plugin payload | this overlay workflow, then a human Creator action | locally checked; Creator-side update **UNVERIFIED** until a person reads it back |
| Avatar, `displayName`, presentation fields | the **App**/Creator owner | **UNVERIFIED** here; an overlay ZIP is not a way to force it |
| MCP URL, OAuth scopes/consent, services, routes | the service/owner path | **not this workflow**; never changed by an overlay |
| Backend devices/runtimes/handlers | service side | no new ZIP is needed |
| Tool count / new project-create entry | core/server deployment | no overlay update; read the actual `tools/list` after the core exposes it |

Do not tell a user "the new ZIP changes the avatar". The official public
documentation does not establish that an App-backed private Creator update
changes presentation, and this repository has not observed it. Say
**UNVERIFIED** and send the presentation question to the App owner.

## What an update must preserve

Before a human uploads anything, the local package must satisfy all of these:

1. **same plugin id** in the previous and next release records;
2. `name`, `displayName` and `apps` identical to the pinned baseline;
3. a **version** that advances (MAJOR.MINOR.PATCH, strictly greater);
4. every Skill present in the previous release still present; a new Skill may
   be added, a removed one is a refusal unless that removal was separately
   approved and the readback accounts for it;
5. the archive SHA-256 and content digest match the release record;
6. no credential, token, private key or by-product in the archive.

`integrations/fleet-app-overlay/baseline.json` is the identity authority. A
`null` there means **not pinned**, not "anything is allowed": do not fill it
with a guess to make a check pass. Fill it from what the live plugin actually
reports, by a person, before relying on it.

## The local workflow

The builders and checks are offline. They never contact Plugin Creator, never
read a token and never install a plugin.

```sh
# 1. Build the overlay ZIP from the operator's source tree.
python3 integrations/fleet-app-overlay/build_overlay.py \
    --source <source-tree> \
    --baseline integrations/fleet-app-overlay/baseline.json \
    --out <dist>

# 2. Check identity/version and produce a same-ID update plan.
python3 integrations/fleet-app-overlay/package_update_check.py plan \
    --previous <previous.release.json> \
    --next <next.release.json> \
    --out <plan.json>

# 3. Compare a human/operator readback receipt with that plan.
python3 integrations/fleet-app-overlay/package_update_check.py readback \
    --plan <plan.json> --observed <readback.json> --out <readback-result.json>

# 4. Confirm the previous archive is a valid rollback target.
python3 integrations/fleet-app-overlay/package_update_check.py rollback \
    --previous <previous.release.json> \
    --current <next.release.json> \
    --out <rollback.json>
```

A local **rehearsal** builds two synthetic versions and runs the same checks:

```sh
python3 integrations/fleet-app-overlay/package_update_check.py rehearse \
    --source tests/data/fleet-overlay-fixture \
    --baseline integrations/fleet-app-overlay/baseline.json \
    --out <rehearsal-dir>
```

The rehearsal is local evidence. It is not a Creator update and its
`production_verified` field is `false`.

## Readback: what a person must see

After a human performs the same-ID Creator update, the readback must show:

- the **plugin id is unchanged**;
- a **new release id**, different from the previous one (a reused release id
  is not a new release);
- the expected **version**;
- the old Skills still present and the intended new Skill present;
- the archive hash recorded for the uploaded ZIP;
- assets only if the App/Creator path actually returns them.

A readback receipt is a claim by the person who collected it. This repository's
checker refuses contradictions and missing required fields, but it cannot
authenticate the claim. It records `production_verified: false` and names the
source. Do not turn a matching local receipt into "live update verified".

## Rollback: a target, not an action

A rollback target is the **previous release archive**, with the same plugin id
and its recorded SHA-256. The local checker confirms the archive still hashes
to the record and that restoring it would go to the previous version. It does
not perform a rollback, create a release, or delete the new one. If the
previous archive is missing or hashes differently, there is no verified
rollback target — say so before the update, not after.

## What this workflow never does

- never uploads or publishes a plugin; never installs a global plugin;
- never creates a plugin or a release;
- never changes OAuth, scopes, the MCP URL, services or routes;
- never changes avatar, `displayName` or other App-owned presentation fields;
- never claims a Creator action happened, or that a local rehearsal is a live
  update;
- never erases the previous release, because that is the rollback evidence.

The stable identity is the point: the same plugin id, the same `apps` binding,
a new version, the old Skills still there, and a rollback target that a person
can actually return to. The tool count is not a package constant: whenever the
core changes its surface (as it did when `fleet_create_project` and
`fleet_delete_project` arrived), read the actual `tools/list` and update the
client evidence rather than assuming a count. A capability missing from the
served list is reported as absent, not invented by the package.
