---
name: fleet-norm-audit
description: Check whether agents on a device actually follow the owner's norms — first that the right norm revision is installed, then that a fresh agent behaves accordingly on a short probe task — and turn each gap into a concrete ability change. Use periodically, after a rollout, or when an agent's work looks off-standard.
---

# Norm audit: installed is not followed

Two different failures look the same from outside: the norm never reached the
device, or it reached it and the agent does not follow it. Check them in that
order, because the fix is different.

## When to use

- After `fleet-ability-change` widened to all devices.
- When delivered work breaks a convention the owner cares about.
- On a schedule the owner sets (this Skill is not a scheduler; someone must
  invoke it).

## Inputs

| input | note |
|---|---|
| devices × runtimes | which agents to audit |
| norms to probe | 1–3 specific rules, each with a probe task and the answer that shows compliance |

A good probe is short, has a checkable answer, and does not change anything
outside the session: "Where would you put the test output for this Item, and
why?" is a probe; "deploy X" is not.

## Steps

1. **Installed?** Run `fleet-ability-inventory` on each target. Anything other
   than `CONVERGED` at the expected revision is an installation gap — stop
   here for that target and report it; a behaviour probe on stale norms tells
   you nothing.
2. **Followed?** For each converged target, create a fresh session
   (`fleet_create_session`, see `fleet-create-with-grants`) so it loads the
   current entry files, and send all probes in one message with
   `fleet_send_to_session`.
3. Read the answers with `fleet_get_run` / `fleet_read_session`. Judge each
   probe against its expected answer, quoting the agent's words.
4. **Classify each failure:**

   | finding | fix |
   |---|---|
   | not installed / drift | roll out again (`fleet-ability-change` step 4 on) |
   | installed, rule absent from the norm text | add the rule (`fleet-ability-change`) |
   | installed, rule present but ignored or misread | rewrite the rule more concretely, with an example (`fleet-ability-change`) |
   | agent followed the rule but the rule is wrong | owner decision; do not change it yourself |

## Success

A table: device × runtime → norm revision, probe → PASS / FAIL with the quoted
answer, and for each FAIL the class and the proposed change.

## Never

- Never probe with a task that has external effects.
- Never mark a probe PASS from a summary; quote the answer.
- Never edit norms on the device to make a probe pass.
