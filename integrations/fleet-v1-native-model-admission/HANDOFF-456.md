# Handoff to #456 (`integrations/chatgpt-fleet-next/batch_contract.py`)

Read-only cross-check. **Nothing in #456 was modified**, and this branch does not
touch `integrations/chatgpt-fleet-next/**`. Line numbers below are from #456's
head `76d63766`.

## What #456 gets right, and should keep

*   Its keying is already the right shape: models are addressed per
    `(device_ref, runtime)`, so one device's list cannot authorise another's
    model and a Claude id cannot stand in for a Codex one. This module's
    `MODEL_DEVICE_MISMATCH` / `MODEL_RUNTIME_MISMATCH` are the same two rules,
    stated as refusals.
*   `completion_verdict` already refuses to read an ACK as a completion
    (`UNKNOWN/ACK_ONLY`, `UNKNOWN/FALSE_COMPLETION`), which is the same posture
    this module takes about a *request*.
*   Refusals are codes plus a detail string, which is what made this cross-check
    possible at all.

## Finding 1 — `snapshot["models"]` has no source, and the device publishes none

```python
62      required_model = task.get("required_model")
63      if required_model:
64          models = (((snapshot.get("models") or {}).get(device_ref) or {})
65                    .get(runtime) or [])
66          if required_model not in models:
67              refusals.append(_task_refusal(
68                  "MODEL_NOT_AVAILABLE",
69                  f"{required_model} is not in {device_ref}/{runtime} models {models}"))
```

CloudCLI 1.41.0 has no verb that lists models or agents, `session_create.model`
is an unenumerated `string`, and no roundtrip in #458's capture ever passed a
model. The truthful value of `snapshot["models"][device][runtime]` on this
release is therefore **absent**, and line 65 turns absent into `[]`.

That produces a wrong sentence, not a wrong plan:

*   `required_model not in []` is **always true**, so every task carrying a
    `required_model` is refused with `MODEL_NOT_AVAILABLE` — which reads as "this
    model is unavailable" when the fact is "nobody published a list". A caller
    reading the batch verdict cannot tell a real gap from a missing source;
*   when the field *is* populated, the verdict is a judgement on whatever filled
    it. The current fixture does exactly that
    (`tests/test_fleet_chatgpt_batch_commander.py:48`:
    `{"device:tokyo": {"claude": ["sonnet", "opus"], "codex": ["gpt-6.1-sol"]}}`),
    and `skills/fleet-batch-commander/SKILL.md:29` asks for "a model present in
    the runtime's *actual* available list" — a rule with no implementing source
    on 1.41.0, which is the gap this module exists to name.

## Finding 2 — the model check is not the gate that will actually refuse

`required_grant` (L71–77) is checked against
`snapshot["grants"][device_ref][runtime]` — verb scope per device and runtime.
The ceiling that stops a create naming a model is a **project** grant:
`CreateGrant.models` at `fleet_mcp/config.py:302-335` (#407), which is
per-project and empty-by-default. `_check_task` never reads it.

Consequence: `validate_batch` can return `PASS` for a task whose model the create
gate will refuse with `MODEL_POLICY_DENIED`, because the two are looking at
different objects. This module's `preflight_create` is that same check with the
project gate first.

## Finding 3 — one code is carrying two different facts

`MODEL_NOT_AVAILABLE` should mean "a source exists and this id is not in it".
The 1.41 reality needs two more, and this module already returns them:

| situation | code here | suggested for #456 |
|---|---|---|
| a published enumeration exists, the id is absent | `MODEL_NOT_AVAILABLE` | keep |
| no enumeration published at all | `MODEL_NOT_VERIFIED` / `MODEL_EVIDENCE_STALE` | add |
| the project grant does not name the id | `MODEL_POLICY_DENIED` | add (Finding 2) |
| `required_model` absent | `PROVIDER_DEFAULT`, identity `UNKNOWN` | keep |

## Suggested patch (for #456's owner — **not applied here**)

Six lines, no new imports, preserving every existing code:

```python
    required_model = task.get("required_model")
    if required_model:
        models = (((snapshot.get("models") or {}).get(device_ref) or {})
                  .get(runtime) or [])
        if not models:
            # An absent list is not a list that excludes this id. See
            # integrations/fleet-v1-native-model-admission/README.md.
            refusals.append(_task_refusal(
                "MODEL_UNVERIFIED",
                f"{device_ref}/{runtime} publishes no model enumeration; "
                f"{required_model} cannot be verified"))
        elif required_model not in models:
            refusals.append(_task_refusal(
                "MODEL_NOT_AVAILABLE",
                f"{required_model} is not in {device_ref}/{runtime} models {models}"))
```

A companion rule, if you want the source pinned rather than the sentence fixed:
make `integrations.fleet-v1-native-model-admission`'s `known_models()` the only
writer of `snapshot["models"]`. On 1.41.0 it returns `()`, so the empty list then
*means* "no source", which is what the added branch above says out loud.

## What this branch did not do

*   No edit to `integrations/chatgpt-fleet-next/**`, no push to #456's branch, no
    rebase, no comment on the PR (per the round's instruction).
*   No claim that #456 is wrong in its *structure*: the device/runtime keying and
    the `UNKNOWN`-over-`PASS` posture are both right. The issue is the source of
    one field and one code's ambiguity.
