---
name: fleet-broadcast
description: Use when the owner wants the Fleet Feishu cards or the daily report to say something different, show different sections, or go out at a different time — change card-spec.yaml, prove it, and land it by pull request. Not for the facts on a card (levels, readings, run outcomes), which are code.
---

# Fleet broadcast: change what the cards say

## At a glance

**Use when:** the owner asks to change a card's wording, headings or advice, the
日报's sections or order, the workday window, the update slot, or the 日报 time.

**Does:** edits `integrations/fleet-hub-watch/card-spec.yaml`, proves it with
`--check-spec`, renders the samples for review, and opens a pull request.

**Never:** change levels, thresholds, colours or what counts as a failure (those
are code and `runtime/attribution-contract.yaml`); send a real Feishu message to
preview; edit the spec on a running hub by hand without the pull request.

## Steps

1. Read the request back in one line: which card, what changes, and how you will
   recognise it on the card.
2. Edit `card-spec.yaml`. Only keys that already exist are accepted; the file's
   header lists the placeholders each text may use.
3. Prove it:

   ```bash
   PYTHONPATH=.:integrations/fleet-next-agent-monitor:integrations/fleet-hub-watch \
     python3 -m fleet_hub_watch --check-spec integrations/fleet-hub-watch/card-spec.yaml
   ```

   `OK` means every card renders and passes the CardKit check (30 KB, element ids,
   `update_multi`). `REFUSED: …` names the key to fix.
4. Render the samples with the new spec and attach the changed JSON to the PR so
   the owner can paste it into Feishu's card builder:

   ```bash
   python3 integrations/fleet-hub-watch/samples/build_samples.py --spec integrations/fleet-hub-watch/card-spec.yaml --out /tmp/cards
   ```

5. Open a pull request. After merge, update the hub's checkout; the running
   watcher reloads the file on its next tick and records `card_spec` in its
   heartbeat (`LOADED card-spec.yaml`, or `REFUSED: …` with the old spec kept).

## Success

The heartbeat shows `LOADED card-spec.yaml`, and the next card of that kind
shows the change.
