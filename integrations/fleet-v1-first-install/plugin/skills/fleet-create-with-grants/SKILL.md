---
name: fleet-create-with-grants
description: Create a Fleet session only inside a grant you can name — owner, project, runtime and scope — and track it by its run id. Use when no existing session can carry the task, and never as a first move.
---

# Creating a session is four separate permissions, not one

`fleet_create_session` mints a session that inherits a **working tree, a set of
credentials and a reachable service set**. That is why the grant is made of
independent answers, and why a caller who has obtained one of them has not
obtained the others.

Before calling, be able to state all four out loud:

| Question | Where the answer lives |
|---|---|
| Which **credential** is calling, and does it carry `fleet:create`? | the scope on the presented token — never a parameter you supply |
| Is **creation** open on that device at all? | the machine's own binding (`allow_create`) |
| Which **project** may be created on? | the machine's project list *and* the surface's `create_allow`, both |
| Which **runtime / owner** does it run as? | the owner-approved runtime entry for that account |

A project ceiling exists because the project id is what decides which working
tree and which credentials the new session gets. There is no "any project"
option, and a refusal that names a project is not an invitation to retry with a
different one.

## A refusal is information, not an obstacle

The four refusals are distinguishable on purpose:

- `SCOPE_DENIED` — the token lacks the scope. The fix is a re-consent by the
  owner; adding a scope to a token that was already minted is not possible.
- `WRITE_NOT_GRANTED` — **the device** refused, by its own rules, on its own
  machine. The fix is a file on that device, not on yours.
- `APPROVAL_REQUIRED` — this surface is not authorized to drive that target.
- `BAD_REQUEST` — the arguments are wrong; re-read the schema.

Do not retry a `WRITE_NOT_GRANTED` with different arguments. That is asking the
same question again in the hope that the machine changed its mind.

## Idempotency, and the run id

Pass an **idempotency key** whenever the create is a retry of an unknown
outcome. The key is scoped to the device, the project and the runtime, so the
same key against a different project is deliberately a different request and
will mint a second session. On a replay the surface returns the **recorded**
session with `idempotent_replay` set — read that flag, because that is the
difference between "I made one" and "I made another one".

Keep the returned **run id**. Everything about the outcome — whether it is
still going, what it produced, whether it failed — is read back through the run
and the session, never inferred from the fact that the create call returned.

## What creation does not give you

It does not give you a model, a permission mode or a larger grant than the
runtime's owner-approved entry allows. In particular `bypassPermissions` is
**the owner's mode on the owner's own account and project**; it is not
something a caller may request, and a runtime that reports a different
effective mode is a different runtime.

## The model and the mode are read back, never chosen by you

The default model for a device runtime is the one the **owner** put in that
runtime's entry — on this fleet the admin runtime's default is
`deepseek-v4-flash-vision-anthropic`. Treat that as a fact to *verify*, not a
value you select:

- the model is a property of the owner-approved entry. A model id in your
  request is a request that entry may refuse; it is not a selection.
- the **effective** model and permission mode are read back after the session
  exists (`fleet_read_session`, or the run record). That read-back is the only
  answer worth reporting. "It should be on the default" is a guess; the
  resolved value is the fact.
- a runtime whose resolved mode differs from its entry is a **different
  runtime**, not a misconfiguration to correct from here.

`bypassPermissions` is the sharpest case. It is the owner's mode, on the owner's
own account, inside one **named project** — and its entire safety argument is
that narrowness. It is never "the fleet runs with bypass", it is never
requestable by a caller, and it is never reportable in a summary without the
project it is confined to and the read-back that confirmed it.

## All scopes is not unlimited authority over every account

A credential carrying all six scopes still is not authority over every device or
every account. Scopes are **one gate of four**, and the other three live
elsewhere:

- the **device binding** on the target machine (`allow_send`, `allow_create`,
  `allow_control`);
- the **operator grant**, which is per principal × device × verb;
- the **node floor** on the device, which the far end can withdraw without
  telling anyone here.

One create needs the scope *and* the binding *and* a grant for this principal on
this device — and none of the four implies any other. A full-scope credential
reaches one device, run by one account, under one owner's floor. Saying "we have
all the scopes" as if it meant "we can do anything anywhere" is the sentence
that turns a scoped tool into an unbounded one.
