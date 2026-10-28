# Fleet Next Wave 15 — prerequisite QA

This directory is an **independent dependency gate**, not a product package, a
plugin, a service or a runtime artifact. It answers one question with frozen
commits:

> #495 reported `NOTIFIER_SEAM_MISSING` from #481's `BatchAggregator`. Is the
> public `send_text` seam actually absent from the Wave 14 RC, or did the QA
> pin the wrong notifier revision?

The answer is the second: #495 pinned #423's original head `d9b2948`, which
predates #460's public send seam `4fc9fcb`. The RC carries `4fc9fcb` through
#481's merge `ba76a80`, so the seam is present.

| file | what it is |
| --- | --- |
| `seam_probe.py` | extracts two frozen notifier revisions with `git archive` and runs #481's real `BatchAggregator` against each |
| `composition.json` | pins, ancestry, dependency order, RC composition order, pending fixes, #425/#478 release matrix |

The human record is
`docs/integration/FLEET-NEXT-WAVE15-PREREQ-20261028.md`; the claim is asserted
by `tests/test_fleet_next_wave15_prereq_qa.py`.

## The two readings

```text
old  d9b2948  send_text False -> NOTIFIER_SEAM_MISSING   (RED, #495's pin)
new  4fc9fcb  send_text True  -> constructed              (GREEN, the RC)
```

No notifier source is copied into this repository. Each revision is extracted
from its own commit into a throw-away directory; the only variable is the
notifier. The probe sends nothing: the provider is a placeholder and
construction does not call it.

## What this gate refuses to claim

* It does **not** claim 5/5. The RC head `00503f9` is 4/5; two new failures are
  #490's request naming collision, a separate owner's fix.
* It does **not** compose #460's later head `1dbeda5`. That commit is a clean
  optional bump (newline fix), not the `send_text` seam.
* It does **not** merge #407/#473/#474, does not touch `fleet_mcp/**`, and does
  not run a real Legacy/Next co-install, Timer, App1 IM or `main` merge.
* It does **not** edit #493/#488/#490 source. Their remaining interfaces are
  recorded as owner handoffs, not patched here.
