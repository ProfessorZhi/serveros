# `serveros/generation-fence.v1` — the epoch fence, and the dry-run recovery policy

This document records a **blocker fix** and a **design**. It is not evidence that
anything runs: this round changed source and wrote fixtures, and **nothing was
compiled or executed** (the host is under a load ceiling — see §5).

## 1. The blocker: a rule the code stated and never enforced

`include/supervisor/model.hpp` says of `generation`:

> Monotonic. The Core acts on a generation, and a manifest that is older than one
> it has already applied is **refused** — a file that was restored from a backup
> must not silently re-authorise yesterday's plan.

`contract/desired-state.v1.md` repeats it. The **code did not implement it**:

| where `generation` appeared | what it did |
|---|---|
| `src/manifest.cpp` | parsed and required |
| `src/plan.cpp` | copied into `Plan::generation` |
| `src/receipt.cpp`, `src/status.cpp` | printed into the receipt and the status |
| — | **never compared with anything** |

So «the Core acts on a generation» was true in the sense that it *records* one,
and false in the sense the sentence means. A manifest restored from a backup —
`generation: 3` where 7 had already been applied — planned and acted exactly as
if it were current.

## 2. The fix (source)

- `PlanInput` gains `long applied_generation = -1;`
  (`include/supervisor/plan.hpp`) — the highest generation already acted on, or
  `-1` for a Core that has genuinely applied nothing.
- `build_plan` refuses the WHOLE document when
  `manifest.generation < applied_generation` (`src/plan.cpp`), before any
  ordering or per-service work:

  ```
  refusal: { scope: "", code: "STALE_GENERATION", reason: … }
  ```

**Whole-document, not per-service.** A manifest that went backwards is not a
newer statement with a few stale rows; it is an older statement, and acting on
part of it acts on a superseded decision. Refusing row-by-row would also make the
outcome depend on which rows the backup happened to contain.

**`plan.ok` stays `true`.** This is a plan that declines, the same reading
`CYCLE` and the per-service refusals already use; `ok = false` is reserved for
«there is no plan».

## 3. What is NOT done (the remaining step, stated plainly)

**`applied_generation` is not yet persisted.** The fence is enforced where it is
passed, and the resident loop does not yet pass it — so **today the fence is
inert in production**. Persisting it belongs with the recovery state (one
document, one atomic write, the same shape `Recovery::to_json` / `load_recovery`
already use), and must be written **only after** a generation has actually been
planned and carried out, or a crash between plan and action would advance the
fence past work that never happened.

`generation-fence.example.json` is the **mock** the future test consumes; it has
not been run against the binary.

## 4. Design: controlled recovery of ONE allowlisted test unit (dry-run)

This is a **design, not an implementation**. Nothing here is wired, and no
canary is executed.

A dry-run recovery pass answers «what *would* be done», and does so under four
gates — all four must pass for the pass to produce even a hypothetical action:

1. **Owner consent.** The manifest is authored by the OS owner and carries that
   owner as `principal`; `observe_only: true` until the owner turns it off in a
   document a reviewer can read. The Core never flips it.
2. **Single-writer.** One Core per user, made real by the status socket: a
   second Core cannot bind `$XDG_RUNTIME_DIR/serveros/native-core.<uid>.sock`
   (an existing path is refused, never unlinked), and the manifest may never name
   the Core's own unit, so nothing restarts the supervisor. A C++ Core and the
   Python `serverosd` **must not both write**: Python `serverosd` stays the ONE
   production writer, and any C++ action is proposed, never applied, for as long
   as that split stands.
3. **Epoch fencing.** §2/§3 — a manifest older than the applied generation is
   refused whole. Without the persisted value this gate is **open**, which is
   why it is listed here rather than assumed.
4. **Allowlist.** The provider's exact unit allowlist is checked twice (the
   planner's rule 3 and `systemd_user.cpp` before any argv is built), so a dry
   run cannot even name a unit outside it.

A dry-run pass in this shape produces **refusals and a plan and no verbs**. The
canary that would let it act — start, observe, bounded recovery, re-check — is a
separate acceptance with its own approval, and **is not performed here**.

## 5. Why nothing was built or run this round

The Tokyo host is shared and under a memory ceiling: **swap 88.16 % used
(3.79 GiB free)**, PSI `memory full avg60 = 0.00`, PSI `io full avg60 = 2.23`,
`si/so = 0`, load ≈ 0.24 per CPU. The stated gates are stop-and-report above
90 % swap or PSI full > 10; measured values are **below** them, so this round
proceeded — but only with source and fixtures. **No compiler ran, no test suite
ran, no unit was installed, enabled, started or reloaded, and no canary was
executed.**

## 6. Verification status of this change

| claim | status |
|---|---|
| the fence is described correctly and matches `model.hpp` | read the source; no build |
| `plan.cpp` compiles | **UNKNOWN — not compiled** |
| the fixture's expectations hold against the binary | **UNKNOWN — not run** |
| the resident loop passes `applied_generation` | **NOT DONE** |
| the dry-run recovery policy | **DESIGN ONLY** |
