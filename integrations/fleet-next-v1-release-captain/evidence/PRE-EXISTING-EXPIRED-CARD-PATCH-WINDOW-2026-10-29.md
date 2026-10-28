# Pre-existing CI failure: the 14-day card-patch window expired on `main`

**Status: a defect on `main`, not on this branch, and not caused by this PR.**

This file exists because a release captain who reports "CI is red" without
saying *whose* red it is has handed the next person a mystery. The failure
below reproduces on a pristine `main` checkout at
`e69fc0337ae5d33000478e3d5162077f525bc8cc`, with no files from this lane
present.

## The failure

```text
tests/test_direct_lark_notify.py::CardCreateThenPatchTest::test_create_returns_an_id_and_patch_names_it
AssertionError: 'FAILED' != 'DELIVERED'
```

Reproduced on `src/serveros` (branch `main`, `e69fc03`) with:

```text
python3 -m pytest -q -p no:cacheprovider tests/test_direct_lark_notify.py
1 failed, 19 passed
```

and through the hermetic runner the same way.

## The cause

`runtime/message_transport.py` quotes the message service's own bound:

```python
CARD_UPDATE_WINDOW_DAYS = 14
```

and refuses a patch outside it:

```python
if age > CARD_UPDATE_WINDOW_DAYS * 86400:
    return refused(...)   # state -> FAILED
```

The test pins a fixed wall-clock instant:

```python
patched = sender.update({"message_id": remote_id,
                         "sent_at": "2026-10-13T06:00:00Z", "card": card})
```

`2026-10-13T06:00:00Z + 14 days = 2026-10-27T06:00:00Z`. After that instant the
patch is correctly refused, and the test's `assertEqual(patched["state"],
"DELIVERED")` can no longer hold.

Measured at `2026-10-28T18:47:31Z`: age **15.533 days**, window **14 days**,
expired **True**.

## Why it looked green until now

`main`'s last green CI run started at `2026-10-27T02:18:48Z` — three hours and
forty-one minutes before the window closed at `2026-10-27T06:00:00Z`. It passed
by under four hours.

Every run after that instant fails this one test, on every branch:

| head | run started | this test |
| --- | --- | --- |
| `main` `e69fc03` | 2026-10-27T02:18:48Z | passed (before expiry) |
| #504 `6286bfb` | 2026-10-28T14:06:12Z | **new failure** |
| #505 `3512c55` | 2026-10-28T14:12:49Z | **new failure** |
| #506 `30d7c4d` | 2026-10-28T15:26:05Z | **new failure** |
| #510 `a43adc3` | 2026-10-28T18:46Z | **new failure** |

This also explains the "1 new failure" that #504 / #505 / #506 reported
independently: the three branches did not each introduce it, and none of them
is at fault.

## What this does to the release

**No exact head can reach 5/5 hosted CI after `2026-10-27T06:00:00Z`** until
this is fixed. The release is therefore `BLOCKED_CI` for a reason that is a
clock, not Fleet Next work. The release captain records it and does not route
around it.

## What this round deliberately did not do

* It did not edit `runtime/message_transport.py` or
  `tests/test_direct_lark_notify.py`. Both belong to other lanes and are
  outside this lane's write scope.
* It did not add the test to `tests/hermetic-known-failures.txt`. The campaign
  forbids new baseline exceptions, and a real, reproducible failure is exactly
  what that rule is protecting.
* It did not weaken the assertion in this lane's own gate. `BLOCKED_CI` stays
  `BLOCKED_CI`.

## Handoff

The owning author's smallest correct fix is in the test, not the transport:
derive `sent_at` from the current time (for example `now - 60s`) so the test
exercises the in-window patch path, and add a second case that pins the
out-of-window refusal with a fixed old instant. That turns a time bomb into two
tests that mean what they say. The transport's 14-day bound is correct and
should not move.

Minimal reproduction:

```text
git checkout e69fc0337ae5d33000478e3d5162077f525bc8cc
python3 -m pytest -q -p no:cacheprovider \
  tests/test_direct_lark_notify.py::CardCreateThenPatchTest::test_create_returns_an_id_and_patch_names_it
```
