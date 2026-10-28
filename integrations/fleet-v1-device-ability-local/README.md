# Local Device Ability package (#434, L3)

The device-side Ability family, installable and readable **without rebuilding
the node runtime**.

`scripts/build.py --face plugin` (#413) composes the plugin face as its own
artifact; `scripts/runtime_truth.py` (#421) can recompute its bytes. This
package adds the local lifecycle an owner asks about after an install:

    which version is installed, are those the built bytes, does the authority's
    catalog still support it, and is this Ability READY / GRANTED / EFFECTIVE
    — or is the answer UNKNOWN?

## Boundary

* **One catalog.** The only authority is `ability-management` at the revision
  the build pinned. `PACKAGE.json` records that revision and the package's own
  skills/hashes; it never embeds ability metadata or a second taxonomy. A
  different authority revision is `DRIFT`, never silently resolved.
* **No node rebuild.** Building a package copies no `bin/`, `runtime/` or
  `workengine/` member. A Skill change creates a new package revision; a test
  proves the node artifact is byte-identical across two Skill revisions.
* **No release.py.** Promotion/rollback here is a local pointer
  (`current.json`), not the node release tool. `scripts/release.py` is owned by
  #419/#433 and is untouched.
* **No network, credential, service or global install.** The store is a
  directory the caller names; nothing here touches a Claude/Codex cache.

## Layout

```
<root>/revisions/<version>/payload/      the plugin face (immutable)
<root>/revisions/<version>/PACKAGE.json  version, face/kind, catalog rev, hashes
<root>/current.json                      the one pointer (current + previous)
```

`PackageStore(..., source={"ability_repo": ..., "ability_rev": ...})` enables
**recomposition verification**: `readback` rebuilds the plugin face from that
authority pin (through `runtime_truth.expected_artifact`) and requires the
installed bytes to equal it. This is what catches a payload *and* its member
hashes being rewritten together. Without a source the readback says
`source_verified: false` and is manifest-relative; it never claims the stronger
fact.

There is **no symlink and no delete**. Rollback is the same atomic pointer
write in reverse; the previous revision stays on disk. That also makes the
store work on Windows without a symlink privilege. Every JSON write goes
through the tree's already-declared atomic writer
(`runtime/ability_projection.write_atomic`), so this package adds no
undeclared mutation reachability.

## State contract

`states(package=..., resolved=..., observed=...)` reports six facts separately:

| word | where it comes from | may be |
| --- | --- | --- |
| `installed` | this store's payload hashes + face/kind | true / false |
| `supported` | the G1 projection's `terms()` over reported authority axes | SUPPORTED / DENIED / UNKNOWN |
| `ready` | `terms()` over the `health` observation | READY / NOT_READY / UNKNOWN |
| `granted` | `terms()` over the `policy` observation | ALLOWED / DENIED / UNKNOWN |
| `effective` | the resolver's provider, **only** when the rollup is EFFECTIVE | provider / null |
| `status` | the strict rollup below | EFFECTIVE / DEGRADED / UNKNOWN / NOT_INSTALLED / TAMPERED / DRIFT / UNSUPPORTED / NOT_READY / DENIED |

The projection's `terms()`/`effective()` are reused, not re-spelled: an axis the
device did not report stays `UNKNOWN`; the resolver's AVAILABLE is not enough.
This package adds the install fact and is **stricter** about `effective`: the
projection treats a known `NOT_READY`/`DENIED` term as "known"; here a provider
id is reported only in the `EFFECTIVE` rollup, never beside a refusal.

Rollup order: `NOT_INSTALLED`/`TAMPERED` → `DRIFT` → any `UNKNOWN` →
`UNSUPPORTED` → `NOT_READY` → `DENIED` → `DEGRADED` → `EFFECTIVE`.

## Tamper, face and drift

* Missing/unreadable `PACKAGE.json`, a changed byte, an extra file, edited
  member hashes, a forged `face`/`kinds`, node members inside the payload, or
  (when a source is supplied) bytes that differ from a fresh build at the
  recorded pin → `TAMPERED`; install and rollback refuse and the current
  pointer does not move. Plugin required members are checked from the build's
  own release contract, not a local list. A failed/incomplete revision (no
  `PACKAGE.json`) is rebuilt by `build.build`'s own output pruning rather than
  poisoning the version.
* The package must be the `plugin` face (`kinds == ["plugin"]`, no node MCP
  servers, no node members). A node-face artifact is never installable as a
  device Ability.
* `catalog_drift` compares the package's build pin with the authority revision
  the store was constructed with. `install` **and `rollback`** refuse drift with
  `CATALOG_DRIFT` unless the caller passes `allow_drift=True` (the result is
  then reported as `DRIFT`, not success).

## Caretaker

`caretaker_text(facts, summarizer=None)` accepts a **closed** facts envelope
(`status`, `installed`, `supported`, `ready`, `granted`, `effective`, `version`,
`catalog_state`, `why`). An authority-looking key (`grant`, `run_state`,
`principal`, `authorization`) is refused, and so is an unknown state word or a
contradictory `EFFECTIVE` (it needs installed + SUPPORTED + READY + ALLOWED +
a provider id). It returns a deterministic sentence
and, optionally, one model gloss; a model failure only drops the gloss. It has
no function to change a state, start/continue a Run or issue a grant — the
Caretaker is presentation, not authority.

## Tests

`tests/test_fleet_v1_device_ability_local.py` — 49 offline tests, synthetic
authority repository + temp stores + fake clock:

* build/readback/install; unknown version refused; a bundle without the plugin
  face refused;
* missing manifest, changed byte, extra file, edited hashes, forged face, node
  members in a forged revision → TAMPERED and not installable;
* catalog drift: DRIFT, supported UNKNOWN, effective null, install refusal;
* states: fully observed → EFFECTIVE; missing grant/health/support axes →
  UNKNOWN with effective null; DENIED/NOT_READY/UNKNOWN resolver; the
  `EFFECTIVE ⇔ effective` invariant;
* install v1/v2, rollback, rollback again, no-previous refusal, tampered
  previous refusal (pointer unchanged);
* caretaker closed envelope, no mutation, failed gloss, authority keys / unknown
  word / contradictory EFFECTIVE refused, static no `notify/grant/send/run` calls;
* Skill update leaves the node face byte-identical while the package revision
  and skill hash change; no node modules; catalog pin recorded, no second
  catalog file;
* portable: no POSIX-only import or `os.symlink`/`os.chmod`/`rename`, POSIX
  member keys; nested under the already-registered `integrations/` (no
  `layers.yaml` edit);
* rewriting payload + member hashes together is caught by recomposition when a
  source is supplied; without a source the readback says `source_verified:
  false`; rollback refuses a drifted previous unless `allow_drift`;
  DEGRADED resolver is DEGRADED (provider reported, not EFFECTIVE); an
  incomplete revision is rebuilt, not poisoned.

```bash
python3 -m unittest -v tests.test_fleet_v1_device_ability_local
```

## Evidence levels

| level | state |
| --- | --- |
| CODE | this directory |
| UNIT | the 49 tests above |
| HOSTED_CI | the stacked PR's workflow |
| REAL_DEVICE | **NOT_RUN** |
| CLIENT_E2E | **NOT_RUN** (no Claude/Codex cache install) |
| PRODUCTION | **NOT_RUN** (no global install, no service) |

## Stack and handoff

Stacked on #421 (`feat/l3-runtime-truth-face-20261027`), which is stacked on
#413 (`feat/l3-device-ability-build-decoupling-20261027`); all three are Draft
and unmerged. This package does not edit `scripts/release.py` (#419/#433),
`native/**` / `serverosd/**` (#430), `fleet_mcp/**` (#407/#431), CI (#420),
the notifier (#423) or `docs/architecture/layers.yaml` (#414). A future real
installer is a separate owner-approved release; nothing here installs globally.
