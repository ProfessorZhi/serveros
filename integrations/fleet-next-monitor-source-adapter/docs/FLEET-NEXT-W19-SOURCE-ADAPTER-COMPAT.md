# W19 · the run-evidence protocol, one standard on both paths

**Lane:** FJ Adapter (original owner of #490/#499) · **Issue:** #515 (mainline
consolidation) · **Input:** #508 Wave 18 cross-PR QA, its only P1
· **Fix:** `f2911c6dcfd9664dd4ef20f4570c799864fd2bef` on
`fix/fleet-next-source-adapter-evidence-20261029` · **Draft PR:** #522, based on
`main`
**Scope:** CODE / UNIT / CI only. No main merge, no deployment, no device, no
Legacy service, no credential, no Feishu, no model call.

> **Base note.** The first draft of this branch was stacked on #499's branch,
> whose history predates the #520 landing (`90b6671c`, "land the monitor source
> stack (QA-allowlisted)") and the two Lark card-window fixes (`dd5919a9`,
> `93d9a1d2`). Its CI went red on a NEW failure
> (`test_direct_lark_notify.py::CardCreateThenPatchTest::test_create_returns_an_id_and_patch_names_it`,
> `'FAILED' != 'DELIVERED'`) that is a wall-clock bomb already fixed on `main` —
> a fixture pinned to `2026-10-13` against a 14-day patch window
> (`CARD_UPDATE_WINDOW_DAYS = 14`), measured 16 days later. It is not this
> package's code. The branch is therefore rebuilt directly on `main`
> (`99c06fab`), which is also what #515 is for.
>
> What `main` already carries, byte for byte: the whole
> `fleet-next-monitor-source-adapter` package **at the pre-fix bytes**
> (`adapter.py` blob `28ad9baa…` = this branch's own base) and the monitor core
> (`offline.py` `7658b05d…`, `sources.py` `d8a3a551…` — the file #508 pins).
> What #520's QA-allowlisted landing did **not** bring over is the package's test
> file; this branch adds it. So the P1 #508 measured is live on `main`, and the
> fix lands as: `evidence.py` (new) + `adapter.py` / `__init__.py` / `README.md`
> (three files) + `tests/test_fleet_next_monitor_source_adapter.py` (new to
> `main`).

---

## 1. What #508 measured

Two paths read the same run facts and disagreed:

| | #499 adapter → `CompatMonitorSource` | #493 `OfflineFactsClient` → core |
|---|---|---|
| key set | open — any key is read | **closed** `RUN_KEYS` (`offline.py:27`) |
| native output | a non-empty `transcript` **or** `messages` **or** `lastAssistantMessage` **or** a verified artifact | `lastAssistantMessage.content` **or** a verified artifact **bound to the expected sha** |
| same fact `{status: completed, transcript: ["x"]}` | `TERMINAL_VERIFIED` → **1 send** | `UNKNOWN` / `REMOTE_ValueError` → **0 sends** |

`transcript` is not in the monitor's vocabulary at all, so the fact never
reached its classifier: the closed reader refused the key first. The adapter,
meanwhile, treated any non-empty list as proof of output — including a list of
bare strings, a user prompt, and an empty assistant turn.

Three more holes in the same predicate were found while fixing it:

* **D2 — an unbound artifact verified a run.** `_bound_artifact` returned `True`
  whenever the caller named no `source_sha`, so a verified artifact naming no
  commit (or anyone's commit) verified the run. The monitor requires
  `expected_source_sha` to be present *and* equal.
* **D3 — a second spelling of evidence.** `verifiedArtifact` /
  `verified_artifact` / `verified_native_artifact` were this package's own
  aliases; the monitor has `artifacts[]`. Accepting them was a third standard of
  evidence, on one path only.
* **D4 — a delivery-only watch never got a run id.** #493 adopts the answer's
  own `runId` when the watch named none ("the answer saying which run this
  delivery is"); this adapter left `run_id=None`, so the observation could not
  be keyed the same way.

## 2. The unification

`evidence.py` (new, in `integrations/fleet-next-monitor-source-adapter/`) is now
the only place the rule lives, and it is **the monitor's rule, not a looser
one**:

* `MONITOR_RUN_KEYS` pins #493's closed set, with a test that fails if the set
  drifts upstream. An adapter answer is expressible in the monitor's vocabulary
  by construction.
* `normalise_run_fact(payload)` folds a `transcript` / `messages` carrier into
  `lastAssistantMessage` — **the last assistant-role turn, only when it carries
  text and no identity that contradicts the fact** — and drops every key outside
  the closed set. Bare strings name no role, so they yield nothing; a prompt is
  not output; an empty turn is not output; another run's turn is not this run's
  output. A declared `lastAssistantMessage` always wins over a carrier (an
  answer that called its own message blank is not upgraded), and only its
  `content` is forwarded.
* `native_evidence(fact, expected_source_sha)` is #493's `_has_native_activity`
  branch for branch: the "no readable assistant message" note veto first, then a
  non-empty message, then a verified artifact **bound to a named, matching
  commit**.
* `adopt_native_run_id(identity, fact)` is #493's adoption rule, applied after
  the mismatch check — an answer naming another instance is refused and adopts
  nothing.

So the adapter no longer has a second standard of evidence: it **normalises**
into the monitor's shape, then applies the monitor's predicate. A transcript it
cannot show to be this run's own output produces no message at all and the run
stays `UNKNOWN` / `NO_NATIVE_ACTIVITY`.

## 3. RED → GREEN, on a real composition

Composition (Tokyo, `~/tmp/wt-w19-main`, merge commit `2dc3dfc`): `main`
(`99c06fab`, i.e. after the #520 landing) + a real `git merge --no-ff` of
`f2911c6d`; `…/fleet_next_agent_monitor/sources.py` is still blob `d8a3a551…`,
exactly the file #508 pins, and `offline.py` still `7658b05d…`. #508's QA
package and suite are materialised from `fb8c4356` for the run and are not part
of the fix.

### 3.1 The #508 suite, on the composition

```
43 passed, 22 subtests passed        # tests/test_fleet_next_wave18_crosspr_qa.py
```

Both of #508's characterisation tests still hold: the monitor's own path still
refuses a `transcript` (`REMOTE_ValueError`, 0 sends), and the compat path still
delivers a **genuine** assistant turn — because that is the same fact, now in
the monitor's shape rather than in a second one.

### 3.2 The evidence rows: three real Python processes per row

`w19_parity.py` builds each row once and spawns a **separate Python process per
path**, all sharing the scenario's journal / ledger / state files:

* **N** — `python -m fleet_next_agent_monitor --offline-facts …`: #493's own
  offline client, core and #423 notifier.
* **C** — `compat_once.py`: #499's `FixtureLegacyClient` + adapter +
  `CompatMonitorSource`, then the **same** #493 core and #423 notifier.
* **D** — the driver, which reads the journal back. "Sent once" is a fact about
  the filesystem, not about one interpreter's objects.

`native(raw)` is the monitor as it ships; `native(seam)` is the same monitor fed
`normalise_run_fact(fact)`; `compat` is the fixed adapter.

**RED — package restored to `main`'s pre-fix bytes** (`25` assertion failures,
exit 1, identical to the measurement on the #507 composition):

| case | native(raw) | native(seam) | compat @pre-fix | sends |
|---|---|---|---|---|
| message | VERIFIED | VERIFIED | VERIFIED | 1/1 |
| assistant_turn | UNKNOWN/`REMOTE_ValueError` | VERIFIED | VERIFIED | 1/1 |
| assistant_turn_plus_junk | UNKNOWN/`REMOTE_ValueError` | VERIFIED | VERIFIED | 1/1 |
| **bare_string** | UNKNOWN/`REMOTE_ValueError` | **UNKNOWN** | **VERIFIED** ← | **1/1** |
| **prompt_only** | UNKNOWN/`REMOTE_ValueError` | **UNKNOWN** | **VERIFIED** ← | **1/1** |
| **empty_turn** | UNKNOWN/`REMOTE_ValueError` | **UNKNOWN** | **VERIFIED** ← | **1/1** |
| **foreign_turn** | UNKNOWN/`REMOTE_ValueError` | **UNKNOWN** | **VERIFIED** ← | **1/1** |
| **unbound_artifact_no_sha** | UNKNOWN | **UNKNOWN** | **VERIFIED** ← | **1/1** |
| no_output | UNKNOWN/`NO_NATIVE_ACTIVITY` | UNKNOWN | UNKNOWN | 0/0 |
| unbound_artifact | UNKNOWN/`NO_NATIVE_ACTIVITY` | UNKNOWN | UNKNOWN | 0/0 |
| sha_other | UNKNOWN | UNKNOWN | UNKNOWN | 0/0 |
| sha_bound | VERIFIED | VERIFIED | VERIFIED | 1/1 |
| boot_other | UNKNOWN/`BOOT_MISMATCH` | UNKNOWN/`BOOT_MISMATCH` | UNKNOWN/`BOOT_MISMATCH` | 0/0 |
| denied | DENIED/`NOT_GRANTED` | DENIED/`NOT_GRANTED` | DENIED/`NOT_GRANTED` | 0/0 |

`←` = the two paths disagree. Five rows where the monitor says UNKNOWN the
adapter says VERIFIED **and delivers**: `sent_negative_cases: 15`
(5 facts × 3 compat-bearing runs per fact). Those five are exactly #508's P1
plus the D2 hole it did not name.

**GREEN — `f2911c6d`** (exit 0, `"failures": []`, seam `package`):

| case | native(raw) | native(seam) | compat | sends |
|---|---|---|---|---|
| message | VERIFIED | VERIFIED | VERIFIED | 1/1 |
| assistant_turn | UNKNOWN/`REMOTE_ValueError` | VERIFIED | VERIFIED | 1/1 |
| assistant_turn_plus_junk | UNKNOWN/`REMOTE_ValueError` | VERIFIED | VERIFIED | 1/1 |
| bare_string | UNKNOWN/`REMOTE_ValueError` | UNKNOWN/`NO_NATIVE_ACTIVITY` | **UNKNOWN**/`NO_NATIVE_ACTIVITY` | 0/0 |
| prompt_only | UNKNOWN/`REMOTE_ValueError` | UNKNOWN/`NO_NATIVE_ACTIVITY` | **UNKNOWN**/`NO_NATIVE_ACTIVITY` | 0/0 |
| empty_turn | UNKNOWN/`REMOTE_ValueError` | UNKNOWN/`NO_NATIVE_ACTIVITY` | **UNKNOWN**/`NO_NATIVE_ACTIVITY` | 0/0 |
| foreign_turn | UNKNOWN/`REMOTE_ValueError` | UNKNOWN/`NO_NATIVE_ACTIVITY` | **UNKNOWN**/`NO_NATIVE_ACTIVITY` | 0/0 |
| unbound_artifact_no_sha | UNKNOWN | UNKNOWN/`NO_NATIVE_ACTIVITY` | **UNKNOWN**/`NO_NATIVE_ACTIVITY` | 0/0 |
| no_output | UNKNOWN/`NO_NATIVE_ACTIVITY` | UNKNOWN | UNKNOWN | 0/0 |
| unbound_artifact | UNKNOWN | UNKNOWN | UNKNOWN | 0/0 |
| sha_other | UNKNOWN | UNKNOWN | UNKNOWN | 0/0 |
| sha_bound | VERIFIED | VERIFIED | VERIFIED | 1/1 |
| boot_other | UNKNOWN/`BOOT_MISMATCH` | UNKNOWN/`BOOT_MISMATCH` | UNKNOWN/`BOOT_MISMATCH` | 0/0 |
| denied | DENIED/`NOT_GRANTED` | DENIED/`NOT_GRANTED` | DENIED/`NOT_GRANTED` | 0/0 |

14/14 rows: `native(seam)` and `compat` are identical in **status and reason**.
`sends` is `compat→native` / `native→compat` on one shared journal+state: the
four legitimately verified facts are delivered **exactly once** in either order
(`expected_sends_total: 4`), and the ten negative facts deliver nothing at all,
in either order — `sent_negative_cases: 0`. A wrong boot, a wrong sha, an
unbound artifact, another run's turn, a prompt, a bare string, an empty turn, no
output and a denied read are all `UNKNOWN`, `DENIED` or a named refusal on both
paths, with zero sends.

### 3.3 The one place the paths still cannot meet, and whose it is

`native(raw)` still answers `REMOTE_ValueError` for a **genuine** assistant turn,
because #493's offline reader refuses the `transcript` key before any classifier
can fold it. That is one file I do not own — and it is the same file on `main`
(`offline.py` `7658b05d…`).

**Blocked-owner handoff — Dell Monitor Owner, `integrations/fleet-next-agent-monitor/`.** Two one-line edits, and both are needed (`RUN_KEYS` alone admits the key but the classifier would still read no message; the fold alone never runs, because the closed reader refuses first):

```python
# fleet_next_agent_monitor/offline.py:27 — admit the carrier, keep the set closed otherwise
RUN_KEYS = frozenset({…, "lastAssistantMessage", "artifacts", "note", "transcript", "messages"})

# fleet_next_agent_monitor/sources.py — _has_native_activity, first statement after the docstring
payload = normalise_run_fact(payload)
```

The normaliser is importable as `from fleet_next_monitor_source_adapter import normalise_run_fact` (published in `__all__` for exactly this). **Owner's call:** importing it creates a monitor → adapter dependency edge; the alternative is vendoring the 30-line function into the monitor package, in which case this package's `test_the_monitors_vocabulary_is_pinned` and the parity table in §3.2 are the drift alarm. Either way the adapter's half is done and the direction of every remaining difference is fail-closed.

## 4. This package's suite, and the governance gate

| where | before | after |
|---|---|---|
| `tests/test_fleet_next_monitor_source_adapter.py`, `main`'s pre-fix package + this test file | **28 failed, 90 passed, 21 subtests** (Windows) | — |
| same, `f2911c6d`, Windows | — | **97 passed, 48 subtests** (35 original + 45 W15 + 17 W19) |
| same, `f2911c6d`, Linux (composition) | — | 97 passed, 48 subtests |
| `test_declared_implementations.py` on the composition | — | 3 failed, 21 passed |
| the same file on pristine `main` (`99c06fab`) | 3 failed, 21 passed | — |

The RED breakdown is the point: 7 top-level failures in
`TheEvidenceIsTheMonitorsEvidenceTest` (the module does not exist; the rule has
no way to be stated) plus 21 subtest failures — and the behavioural ones are
exactly the five facts above (`bare_string`, `prompt_only`, `empty_turn`,
`foreign_turn`, `unbound_artifact_no_sha`) plus the three singular aliases. The
35 original #490 tests and the 45 W15 tests that do not depend on the evidence
rule pass on both trees, so this change adds no regression.

The three governance failures are **identical on the unmodified `main` tree**
(`DeclaredEventsHaveProducers` ×2, `DeclaredProducersResolve`) — pre-existing,
untouched here, and no baseline entry was added. The two
`DeclaredFieldsHaveReaders` false positives that W15 fixed are green on both
trees: this change adds no read of a name it does not own (the new module's
string literals are the monitor's field names, read *from* its answers).

Combined on the composition: `140 passed, 70 subtests` — #508's 43 + this
package's 97.

## 5. Reproduction

```bash
# composition: main + this fix, plus #508's QA package (materialised, not edited)
git worktree add --detach ~/tmp/wt-w19-main origin/main
cd ~/tmp/wt-w19-main && git merge --no-ff f2911c6d
git checkout fb8c4356 -- integrations/fleet-next-wave18-crosspr-qa tests/test_fleet_next_wave18_crosspr_qa.py

python3 -m pytest tests/test_fleet_next_wave18_crosspr_qa.py -q      # 43 passed
python3 -m pytest tests/test_fleet_next_monitor_source_adapter.py -q # 97 passed

# cross-path parity (harness in ~/tmp/w19, not in the repo)
python3 ~/tmp/w19/w19_parity.py ~/tmp/wt-w19-main --json green.json  # exit 0
git checkout origin/main -- integrations/fleet-next-monitor-source-adapter
python3 ~/tmp/w19/w19_parity.py ~/tmp/wt-w19-main --json red.json    # exit 1
git checkout HEAD -- integrations/fleet-next-monitor-source-adapter
```

Harness: `w19_parity.py` (driver), `compat_once.py` (compat path), and on the
RED tree only `evidence_snapshot.py` — a byte copy of the committed
`evidence.py`, sha256 `037532b73b6ef6a15646426d44269ad14664d0bcd5748eb22163d720c54c1f1b`,
loaded because the pre-fix package has no such module; the driver prints which
seam it used (`seam package` vs `seam standalone copy, sha256 …`).

## 6. What this is not

Not a claim of `REAL_DEVICE`, `PRODUCTION` or `AGENT MONITOR INTEGRATION`. No
main merge, no deployment, no device, no Legacy service, no credential, no model
call, no Feishu send — the notification transport in every run above is the QA
package's journal file. No file of #493, #503, #504 or #508 was modified; the
only #508 material used is its own QA package and suite, at its own blob.
