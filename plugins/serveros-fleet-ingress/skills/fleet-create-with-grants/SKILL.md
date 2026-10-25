---
name: fleet-create-with-grants
description: Create a Fleet session inside the owner-configured managed profile — preferring an idle session but treating routine create as pre-authorized — and track it by its returned execution references, including a run id only when one is returned.
---

# Creating a session is routine inside the owner-max profile

`fleet_create_session` mints a session that inherits a **working tree, a set of
credentials and a reachable service set**. The owner configures the managed
account / runtime / project ceiling once, at the native permissions the owner
chooses. Inside that configured profile, creating a session is a routine,
pre-authorized action: prefer an idle session when one already owns the
context, but do not ask a human to approve each ordinary create.

Before the first use, resolve the managed profile:

| Question | Where the answer lives |
|---|---|
| Which **credential** is calling, and does it carry `fleet:create`? | the scope on the presented token — never a parameter you supply |
| Is **creation** open on that device/runtime? | owner-max: its enabled owner runtime and actual owner grant; legacy: the machine's `allow_create` binding |
| Which **project** may a session use? | owner-max: the selected runtime's registered project membership and owner grant; legacy/non-owner: the machine's project list plus static `create_allow` |
| Which **root** may a **new** project be created under? | `fleet_status.owner_access` — the roots the owner configured in the `owner_access.project_roots` binding; the root is the grant |
| Is the grant written with `"*"`? | an owner may write `"*"` for every principal or every enrolled device in `operator_grants`; the device's eligibility check still applies after it. It is server config, read from the refusal or `fleet_status`, never something a caller supplies |
| Which **runtime / owner** does it run as? | the owner-approved runtime entry for that account |

Owner-max checks the actual selected-runtime project membership and owner grant
before the legacy static allowlist. This applies to an existing registered
project and to a project just created and registered through the owner's root:
opening its session requires no additional static `create_allow` edit. Read the
Core's actual grant/refusal; do not stop solely because a static row is absent.

A new project is authorized by the owner-configured project **root**, not by a
pre-existing static row for a name that does not exist yet. Use the returned
registered project reference to create its session in that same owner/runtime.
The static `create_allow` project list remains the alternative gate for the
legacy/non-owner path; it is not an extra gate on an owner-max project grant.

After the owner has configured that profile, do not re-ask for routine create
inside it. A refusal that names a project is not an invitation to retry with a
different project.

## Creating a new project: `fleet_create_project`

A session in an existing project and a **new project** are different
operations. Confirm the capability in the actual tool list (`tools/list` or the
plugin's recorded capability list) and take its schema from there; if the
served list has no `fleet_create_project`, report
`PROJECT_CREATE_CAPABILITY_NOT_EXPOSED` and return the decision to the
core/owner. Do not guess another verb name, edit a device file, or fall back to
another account. Do not hardcode a tool count; read what the surface serves.

When it is served:

1. **Find the root.** `fleet_status.owner_access` lists, per device and
   runtime, the `root_refs` this caller may create under, how it was admitted
   (`owner-max`, or an operator `create` grant) and — for owner-max only — the
   permission mode new sessions there take. No entry for the device means no
   root: that is the answer, not a gap to work around.
2. **Create.** Call `fleet_create_project` with `device`, `root` (a ref from
   that list), `name` (one safe directory component), optional
   `display_name`, the exact `runtime`, and a stable `idempotency_key` you keep
   across retries. It needs `fleet:create` on the token. Inside owner-max there
   is no per-project approval to ask for.
3. **Use the returned `project_id`** for `fleet_create_session`. A project just
   created through the owner's root needs no static `create_allow` edit.

What its refusals mean:

- `APPROVAL_REQUIRED` — the caller has neither the selected owner-max profile
  nor a `create` operator grant on a device whose operator authored project
  roots. That is an owner configuration question; do not retry elsewhere.
- `BAD_REQUEST` — an unsafe name, an unknown root ref, or a directory that
  already exists. Creation is exclusive: an existing directory is refused, never
  adopted. Pick a new name or use the existing project's session instead.
- `CAPABILITY_UNSUPPORTED` — that device's provider cannot create projects.
- an `INTERNAL` answer with `retry_safe: false`, or any unknown outcome — the
  project may exist. Reconcile with `fleet_cloudcli_projects_list` and the same
  `idempotency_key`; never send a blind second create under a new key.

## Removing a project: `fleet_delete_project`

Only when the task explicitly asks for a project to be removed. It needs
`fleet:control`, plus the device's `allow_control` (or the owner-max profile)
and the `control` operator grant.

- Read the row first with `fleet_cloudcli_projects_list`, archived rows
  included, and pass its `id` as `project_id` and its own `path` as
  `confirm_path`. A path that does not match is refused before anything is
  touched.
- The default archives the row: reversible, directory and transcripts kept.
  `force: true` permanently removes the session rows and transcript files.
  Neither form removes the project directory on disk.
- A `fleet-node` device removes only rows in its own catalogue whose path sits
  under a root its owner configured; a row outside today's roots is not
  reachable from here.
- Success is verified by the surface re-reading the catalogue. A second force
  delete answers `PROJECT_NOT_FOUND`; that is the present state, not a failure
  of the first call.

## A refusal is information, not an obstacle

The four refusals are distinguishable on purpose:

- `SCOPE_DENIED` — the token lacks the scope. The fix is a re-consent by the
  owner; adding a scope to a token that was already minted is not possible.
- `WRITE_NOT_GRANTED` — **the device** refused, by its own rules, on its own
  machine. The fix is a file on that device, not on yours.
- `APPROVAL_REQUIRED` — this target is outside the configured owner-max scope;
  that is the case that still needs a human decision, not every routine call.
- `BAD_REQUEST` — the arguments are wrong; re-read the schema.

Do not retry a `WRITE_NOT_GRANTED` with different arguments. That is asking the
same question again in the hope that the machine changed its mind.

## Idempotency: the same key is necessary, not a restart guarantee

Pass an **idempotency key** whenever the create is a retry of an unknown
outcome. The key is scoped to the device, the project and the runtime, so the
same key against a different project is deliberately a different request and
will mint a second session. On a replay the surface returns the **recorded**
session with `idempotent_replay` set — read that flag, because that is the
difference between "I made one" and "I made another one".

The key alone is not a cross-restart guarantee. Read
`idempotency_durability` from the answer:

- `PROCESS_LOCAL` — the guard lives only in that process and is lost on
  restart;
- `AGENT_CONTROL_STORE` — the existing owner store retains the record, so a
  verified owner record or an explicit replay can be relied on;
- `NOT_REQUESTED` — no key was supplied; a retry is a new delivery;
- **absent** — an older service; treat the durability as `UNKNOWN`.

Only rely on the same key when you can verify an existing owner record or an
explicit replay. If `PROCESS_LOCAL` was used, a restart happened or may have
happened, or the ACK is unknown, **do not send or create again to try your
luck**. First read the original session/native history and the artefact, and
follow the owner's reconciliation path. A `RUN_NOT_FOUND` or `never_issued`
answer is not proof that the creation or task did not run; the native history
or artefact may show that it completed.

Keep every execution reference the answer returned: `session_ref`, `run_id` if
present, and `queued_message_uuid` if present. A missing `run_id` is
`NOT_RETURNED`, not a failure and not permission to resend.

## What owner-max does not give you

It does not give you Linux root, OS administration, another OS account, or a
larger grant than the owner configured. It does not let you choose a model or
permission mode outside the owner's runtime entry: a model id in your request
is a request that entry may refuse, not a selection. A runtime that reports a
different effective mode is a different runtime.

## The model and the mode are effective values, read back

The effective model and permission mode are whatever the **owner / runtime /
project** entry resolves to. There is no fleet-wide default to quote: a value
observed on one runtime is not another runtime's value, and a model id you
write from memory is not a selection.

- the model is a property of the owner-approved entry. A model id in your
  request is a request that entry may refuse; it is not a selection.
- the **effective** model and permission mode are read back after the session
  exists (`fleet_read_session`, or the run record). That read-back is the only
  answer worth reporting. "It should be on the default" is a guess; the
  resolved value is the fact.
- a runtime whose resolved mode differs from its entry is a **different
  runtime**, not a misconfiguration to correct from here.

A high native permission mode is the sharpest case. If the owner configured one,
it belongs to that runtime, account and the owner-configured project **root**
(for a new project) or the **named project** (for an existing one) — and its
entire safety argument is that narrowness. It is never "the fleet runs with
bypass", never a caller-requestable widening, and never reportable without the
root or project it is confined to and the read-back that confirmed it. CC and Codex each have
their own effective model and highest native permission; read both back.

## All scopes is not unlimited authority over every account

A credential carrying all six scopes still is not authority over every device or
every account. Scopes are **one gate of four**, and the other three live
elsewhere:

- the **device binding** on the target machine (`allow_send`, `allow_create`,
  `allow_control`);
- the **operator grant**, which is per principal × device × verb;
- the **node floor** on the device, which the far end can withdraw without
  telling anyone here.

Inside the owner-max profile, these gates are already satisfied for the
configured device/runtime/project; outside it, a refusal is the answer and a
human decision may be required. A full-scope credential still reaches one
device, run by one account, under one owner's floor. Saying "we have all the
scopes" as if it meant "we can do anything anywhere" is the sentence that turns
a scoped tool into an unbounded one.
