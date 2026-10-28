---
name: gpu-capacity-and-model-deployment
description: Use when a GPU host's capacity, isolation or model-serving state has to be read or explained — deciding whether a card is really free, preparing or rolling back a model server, judging whether a benchmark result may be reported, or answering whether spare GPU time may be used for personal work.
---

# GPU capacity and model deployment on a shared, company-owned node

## Overview

A 48 GB card that reports `0%` util and `303 MiB` used is **not** "free". The
number that matters is who else holds the device, whether your own claim is
recorded, and whether the reading you are looking at is a reading at all.

This is a **reference** for the operator of a GPU node. It does not add a probe,
a scheduler, a lease, or a daemon. Everything below names a surface that already
exists in this repository; if a surface is missing, that is the finding.

## When to use

- Someone asks whether the GPU "is free right now".
- A model server is to be started, replaced or rolled back.
- A throughput/quality number is about to be reported or quoted.
- Someone asks to use spare GPU capacity for personal work.

**Not** for: writing a new GPU collector, changing a model or driver, or adding
a deployment service. Those are different changes with different owners.

## 1. Reading capacity without lying to yourself

Use the existing reading, not a fresh probe of your own:

| Want | Use | Note |
|---|---|---|
| Host facts incl. GPU | `serverosctl resource summary` | renders `runtime/inventory._gpu()`, which shells `nvidia-smi --query-gpu=...` |
| Core-level GPU reading | the `gpu.readonly` block via `runtime/fleet_core_bridge.py` | the Core's own observation, not a CLI wrapper |
| Who holds a resource | `serverosctl lease list`, `serverosctl lease inspect <resource>` | see §2 |

Three rules that the code already encodes, and that are easy to violate in a
summary:

- **A missing reading is `UNKNOWN`, not zero.** When `gpu.readonly` carries no
  value, the bridge emits it as *unavailable* (`runtime/fleet_core_bridge.py`).
  "No GPU line in the output" means nobody read the card — it does not mean the
  card is absent, idle, or broken.
- **Low utilisation is not "the service is down".** The bridge's own note on the
  utilisation row says a card-level counter does not tell you whether a server
  is healthy. Health comes from the service, not from `0%`.
- **`used` is not `free`.** `nvidia-smi` also reports *reserved* memory, and a
  process holding a small allocation can grow. Subtracting `used` from `total`
  and calling the remainder available is a guess, not a measurement.

State which of these you actually read, and give the sample time. A number with
no time attached cannot be checked later.

## 2. Claiming capacity — the lease, not a new scheduler

Exclusivity is already a thing this repository does. Do **not** invent a lock
file, a queue, or a "GPU manager":

```sh
serverosctl lease list
serverosctl lease inspect gpu:0
serverosctl lease declare gpu:0 --scope node --holder <id> --sharing exclusive --ttl <seconds>
serverosctl lease release gpu:0 --scope node --holder <id>
```

Two things that will bite:

- The lease verbs take the **runtime state dir** (`--state-dir`, the same value
  `serverosd --state-dir` takes), not a namespace root. On a checkout where no
  state dir is bound the store refuses — that refusal is information, not an
  obstacle to route around.
- A holder that is not yours releases as `EXTERNAL` / owner-unknown. **You may
  not kill another lane's process to make a release succeed.** If someone else
  holds the card, the answer is "not available", and that is a complete answer.

## 3. Deploying and rolling back a model server

Deployment is a **proposal plus a recorded previous state**, not a command you
paste and call done.

1. Read what is there now: the process list, the lease, the listening ports.
2. Write down the **previous state** — ideally "nothing was serving" — before
   starting anything. That is what rollback restores.
3. Start under the exclusive lease, and verify with a health read (the server's
   own endpoint), not with `nvidia-smi` showing memory in use.
4. Rollback = stop the unit, release the lease, confirm memory returns to the
   pre-deploy figure. It restores *the previous state*, which is not always "the
   previous model".

If the serving stack is not installed, say so and stop. This host has no `vllm`
and no `ollama` on `PATH`; a command that assumes them is a command nobody ran.

## 4. Reporting a benchmark

A benchmark result is only reportable with the **setup** attached: concurrency,
request count, sequence lengths, the model revision, the exact card, and the
time. Without those, a tokens/sec figure is not reproducible and is not a result.

- Define pass **before** measuring ("p99 under T, zero errors, at concurrency
  C"), otherwise any number becomes a pass.
- Report what was measured, including a `NOT_MEASURED` line when it was not.
- Never write `VERIFIED` on a figure that was estimated. In a record, an
  unmeasured claim is indistinguishable from one written after checking, which
  is exactly why the word matters.

## 5. Spare capacity and personal work

This node is **company-owned** (`fleet/registry.yaml` records it with
`ownership: company`), and GPU compute is its first job. Personal work on idle
capacity is a *conditional intent*, not a permission granted by hardware
ownership:

- Company demand, the resource budget, and other users' workloads come first.
- "Idle right now" is not "idle for the duration of your job".
- Hardware ownership does not confer **data** ownership. Copying company weights
  or training data off the host needs the data owner, not the GPU owner.
- Making a job survive reboot is a change to the machine's boot behaviour and
  affects every lane on it. Prepare the unit; do not enable it.

## 6. What this repository does not have

The ability vocabulary has **no GPU or model-deployment operation**. That is a
recorded semantic gap to validate against a real deployed service — it is not
authorisation to invent a new capability, a new daemon, or a second source of
truth for who owns the card.

## Common mistakes

| Mistake | What it looks like | Fix |
|---|---|---|
| Reading `used` as "free" | "303 MiB used, so 48 GB free" | name the holder and the reservation; claim a lease or say unavailable |
| Treating a missing read as zero | summarising an absent GPU line as "idle" | `UNKNOWN`, with the reason |
| Inventing a scheduler | a new lock file or queue beside the lease | use `serverosctl lease` |
| Killing someone else's holder | "it was only 262 MiB" | refuse; report it as not available |
| Rollback with no previous state | "just stop it" | record the prior state first |
| Unmeasured numbers marked verified | slide-ready figure, no setup | `NOT_MEASURED`, or measure with the setup attached |
| Personal work by hardware ownership | "it's my machine" | company demand first; data owner signs copies |
