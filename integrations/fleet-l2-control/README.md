# L2 control: the capability-gap matrix

The L2 release question is not "can Fleet send a message". A message that
arrives is one of twenty-two rows. The rest of the rows are what an operator
actually needs in order to trust it: which projects exist, whether a model may be
named, which run this is, whether a missing run is a dead id or a transient, and
what may be stopped without stopping someone else's work.

This directory measures those rows instead of asserting them.

## The files

| file | what it is |
|---|---|
| `EVIDENCE-1.41.0.json` | CloudCLI 1.41.0's declared verbs and 29 roundtrip envelopes, reduced to SHAPES |
| `evidence.py` | re-derives that fixture from #411's verbatim captures, pinning them by sha256 |
| `matrix.py` | the 22 rows as data; `--render` writes the document |
| `CAPABILITY-MATRIX.md` | the rendered matrix — generated, never hand-edited |

`tests/test_l2_cloudcli_control_matrix.py` is the other half: it resolves every
row's native reference against the fixture and every Fleet reference against the
source tree, and proves each gap by calling the real code.

## Two acceptance levels, kept apart

**CODE / UNIT / CI — done here.** Every test in this directory is offline: no
socket, no device, no service, no credential. The provider is driven through a
recorder in place of its HTTP client, and the only "device" answers are the
envelope shapes the capture recorded. This is what runs in CI.

**REAL_DEVICE / E2E — not done by this directory.** Nothing in this branch was
executed against a CloudCLI device. Every device-side fact below comes from
#411's capture of 2026-10-27, which is a record of what one device did then — a
different thing from a test that passes now. The matrix's `device` column says,
row by row, whether a shape was actually exercised (`OBSERVED-1.41.0`), merely
declared (`SCHEMA-ONLY`), or neither (`NOT-OBSERVED`). Collapsing those into one
"green" is how a surface comes to believe it has been tested on a device.

## What the measurement found

The gaps are rows in `CAPABILITY-MATRIX.md`; these are the ones that need
someone else's file.

1. **`project_create` and `project_delete` are gone from 1.41.0's declaration.**
   1.40.0 declares 33 verbs, 1.41.0 declares 31, and the entire difference is
   those two. `fleet_create_project` / `fleet_delete_project` therefore answer
   `CAPABILITY_UNSUPPORTED` against the captured device — honestly, because both
   read the device's advertised schema first. Whether 1.41 removed the verbs or
   that device disabled them cannot be told from a `tools/list` capture, and is
   not guessed here. Read 1.40.0 again if the project tools matter.

2. **`RUN_UNSETTLED` is documented and never raised.** `fleet_get_run`'s public
   description promises that a run not settled within the wait comes back as
   `RUN_UNSETTLED`. The code exists in `fleet_mcp/errors.py`; no `refuse(...)`
   call in the package passes it. A caller does still get the device's `status`
   and `outcome` passed through, so "running" is visible — but the named state,
   and the "still working, do not treat as failed" sentence attached to it,
   cannot occur. (`tests/…` asserts the absence by AST, so it fails the day
   someone raises it.)

3. **A device refusal loses everything but its code.** `mcp_http.structured`
   relays `structuredContent.code` and withholds the rest — correctly, since the
   text can quote back configured values. But `retryable`, `details.reason`
   (`never_issued` vs `expired`), `details.bootId` and `details.supported` are
   not prose. The consequences are concrete: `RUN_NOT_FOUND` arrives without the
   `retryable: false` that was there to stop a blind retry, and
   `UNSUPPORTED_PERMISSION_MODE` arrives without the set of modes the device does
   support.

4. **Ten comments across four files still say 22 verbs.** `cloudcli_native.py:5,17`,
   `tools.py:774,919,939,941,972,981`, `fleet.py:964`,
   `providers/cloudcli.py:79`. One of them is served to clients: the
   `fleet_cloudcli_native_manifest` description says "The 22 CloudCLI-native
   verbs this surface mirrors" while 31 are mirrored. The number came from
   CloudCLI 1.39 (22 declared verbs) and stopped being true at 1.40 (33). See
   "The declared set is not a constant" in the matrix for the three releases
   side by side. Not edited here: `fleet_mcp/**` belongs to #407.

5. **A run can be addressed by `runId` only.** The device accepts `runId` or
   `deliveryId` (plus a legacy `run` alias) and answers an `idKind` naming which
   it used. `CloudCliProvider.get_run` sends `runId` and nothing else, and
   `fleet_get_run` requires `run_id` with `additionalProperties: false`. The
   observed cases are not broken by it — a queued send answers both ids — but
   half of the native contract is unreachable.

## What is deliberately NOT offered

Three semantics are `UNSUPPORTED` in the matrix because the device has no verb
for them, and each would be trivial to fake:

* enumerating **runtimes/agents** — a list from the local binding, presented as
  if the device had been asked;
* enumerating **model ids** — the only native enumeration is inside a refusal
  that is itself dropped, so any list here would be an invention;
* **stopping one run** — the nearest native effects are session-scoped
  (`session_interrupt`), message-scoped (`session_cancel_queued` by
  `queuedMessageUuid`) and task-scoped (`session_background` by `stopTaskId`).
  A run-level stop tool would silently widen into one of those.

A tool that answers "here are the models" from a config file is
indistinguishable, at the call site, from one that asked the device.

## How to re-check

```bash
python -m pytest tests/test_l2_cloudcli_control_matrix.py -q
python integrations/fleet-l2-control/evidence.py --captures <capture-dir>
python integrations/fleet-l2-control/matrix.py --render
```

The first command needs no network and no device. The second and third
regenerate what the document rests on; the fixture's `source` block pins both
capture files by sha256, and the test that re-derives the fixture skips (with
that reason) on a checkout where #411's captures are absent.

## Boundary

Nothing under `fleet_mcp/**`, `runtime/**` or `workengine/**` is modified by this
directory. Every finding above that lands in one of those files is reported for
its owner, with a pointer, rather than patched here.
