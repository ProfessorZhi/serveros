---
name: fleet-broadcast-tune
description: Use when the owner wants the Fleet Feishu cards or the daily report changed — wording, headings, advice, which daily-report sections appear and in what order, the workday window or the report time. Dispatches the change to the hub as a reviewed edit of card-spec.yaml; it never edits a running hub by hand and never sends a test message.
---

# Tune the Fleet broadcast

## At a glance

**Use when:** "日报加/减一段", "报警卡的建议改成…", "资源播报改成每小时一次",
"日报改到 19:00".

**Does:** turns the request into a change of `card-spec.yaml` on the hub's
checkout of `ProfessorZhi/serveros`, has the hub agent prove it with
`--check-spec` and render samples, and brings the owner a pull request.

**Never:** change levels, thresholds, colours or what counts as a failure (code
and `runtime/attribution-contract.yaml`, a different kind of change); send a real
Feishu message to preview; edit the file on the hub without a pull request.

## Steps

1. Read the request back in one line: which card, what changes, how the owner
   will recognise it.
2. Find the hub session (Tokyo) with `fleet-device-and-session-discovery`;
   prefer an idle one (`fleet-reuse-idle-session`).
3. Send one message: "Using the `fleet-broadcast` skill, change
   `integrations/fleet-hub-watch/card-spec.yaml` so that <change>. Prove it with
   `--check-spec`, render the samples with the new spec, and open a pull request
   with the changed sample JSON attached. Report the PR link and the check
   output."
4. Wait (`fleet-async-dispatch`), then review with `fleet-evidence-review`: the
   check printed `OK`, the diff touches only `card-spec.yaml` (and samples), and
   the sample shows the change.
5. Give the owner the PR link and one sample to paste into Feishu's card
   builder. After merge, the hub reloads the spec on its next tick; its
   heartbeat shows `card_spec: LOADED card-spec.yaml`.

## Success

The PR is merged and the next card of that kind shows the change.
