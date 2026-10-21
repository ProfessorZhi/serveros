# Plan — facts writer and single-owner coordination

Companion to `proposals/proposal-native-facts-and-owner.md`. Same base
`69a18bc`, same branch as PR #390, additive commit.

## Review round 1 — against the existing runtime

- `probes.host.systemd_user_unit` already parses `LoadState/ActiveState/SubState`
  and refuses a not-loaded unit. Reused; no second systemd reader.
- `agent_sessions` has no "busy" predicate. Rather than invent one, the writer
  takes an **injected resolver**; with none configured the fact is `known: false`.
- No existing link observer. A configured TCP probe is the only honest source;
  unset means unknown.
- `runtime/agent_control_store.py` is on the root lane and **is not modified**;
  its public read API is used only if a caller passes a store.

## Review round 2 — against the code being added

- The lease must be the **same file** the Core is given with `--lease`, and must
  be taken **before** the first document is written, or the writer can publish
  facts while the Core owns the pen.
- The document must be written with `rename` from the same directory so a reader
  never sees a partial file (`facts.cpp` refuses malformed rather than retrying,
  by design).
- Owner map must be closed: an unlisted unit is refused, not defaulted, or a new
  service silently becomes the Core's.
- The maintenance marker is per-unit and read every cycle, so turning it on
  stops recovery without restarting anything.

## Files

| file | change |
|---|---|
| `runtime/native_core_facts.py` | new: collector, atomic writer, owner map, shared lease |
| `runtime/serveros-native-facts.service` | new: **template**, not installed |
| `tests/test_native_core_facts.py` | new: unittest (no pytest on this host) |
| `docs/…` | this proposal and plan |

## Verification

Offline unit tests plus live acceptance against a real transient user unit:
recovery, agent busy, link down, stale (writer stopped), second writer (Python vs
Core on one lease), maintenance/manual-stop behaviour, needs-login, and
upgrade/rollback of the writer itself.

## Revision 2 — after root review of 7f061f0

Six defects were found in the first cut. Each is fixed in code, not prose.

| # | defect | fix |
|---|---|---|
| 1 | `RecoveryActor` existed but `runtime/daemon.py` never used it, so a real `serverosd` reconcile took no lease | the real effect seam (`self.actuator.perform` inside `Daemon.reconcile`) now takes the shared `SafeLease`, released in a `finally`; the resolved default is `$XDG_RUNTIME_DIR/serveros/native-core.lease`, the same file a Core gets from `--lease`. The Core additionally requires `--owner-map` and reads the same map, refusing a unit the map assigns elsewhere |
| 2 | `read_agent` only understood a hand-maintained marker, with no producer | a real `process` source reusing `probes.Host.process_pids` (conservative: any match is BUSY), a `session` source for an injected typed status, and a marker that is UNKNOWN when its producer's directory is gone. An unmeasurable host is UNKNOWN, never idle |
| 3 | `SharedLease` used `O_CREAT` and followed symlinks | `SafeLease` refuses symlink/traversal, requires a regular file owned by this uid, `st_nlink == 1`, not group/world writable, and re-binds fd↔path (dev/ino) after open |
| 4 | the facts document omitted services owned by the other program, so the Core saw `unknown` runtime for exactly its own units | `collect_facts` now describes **every** managed service; the owner map only filters who may *act* |
| 5 | `validate_document` did not match `facts.cpp` | closed field sets, bool types, UTF-8 **byte** bounds, closed runtime words, duplicate ids — the writer now refuses what the reader would refuse |
| 6 | publication could follow a symlinked parent | `secure_parent` refuses traversal, a symlink parent, a foreign owner, and a group/world-writable directory |

### Evidence for revision 2

Live, against real transient user units:

| case | observed |
|---|---|
| Daemon, free lease | real `systemctl --user restart`; InvocationID changed; lease released |
| Daemon while another writer holds the lease | `blocked_by: LEASE_BUSY`, InvocationID unchanged |
| Daemon with the unit misowned (`native-core`) | `blocked_by: OWNER_NOT_MINE`, unchanged |
| Daemon default lease path | `/run/user/1000/serveros/native-core.lease` — the Core's default; holding it refuses the daemon |
| Core with a misowned manifest | exit 2, "owned by serverosd in the shared owner map" |
| Core with no `--owner-map` | exit 2 |
| Core while the daemon holds the same default lease | "another writer holds the lease at the given path", unit untouched |

Offline: 44 Python unittests (this host has no pytest, so `test_daemon.py` cannot run and is
reported as NOT_RUN rather than claimed); `test_sup_facts` 65 checks and all 14 other
supervisor suites pass.

## Revision 3 — after the independent review of b25a9e8 (CHANGES_REQUIRED)

| # | finding | fix |
|---|---|---|
| N1 | `from probes import Host` — no such class, so a real busy agent read as UNKNOWN | `_runtime_module` (the canonical `_load` idiom) resolves `probes.py` by path and uses the real `LiveHost`. Ancestors are excluded from `pgrep -f` results: a wrapper shell that merely quotes the pattern is not a user of the service. Live: idle → `busy:false, known:true`; busy → `busy:true`; unmeasurable → `known:false` |
| N2 | `realpath(parent) == parent.resolve()` is a tautology; a deep symlink plus mkdir-before-check escaped | `open_secure_dir` walks every component from `/` with `O_NOFOLLOW` on an already-open descriptor, applies the same trusted-directory rule as `state_directory.cpp` (own/sticky), creates the final directory **only after** the chain is verified, and returns a **held fd**. Every write, rename and unlink is `*at(dir_fd=…)`. Applied to facts, owner map and lease alike |
| N3 | the lease was checked once at `acquire`; rename-and-replace gave a second writer a new inode while `held` stayed true | `SafeLease.revalidate()` re-checks fd↔path dev/ino, uid, mode, nlink and the whole parent chain; `RecoveryActor.recover` and the C++ effect loop call it **immediately before every effect**. The C++ `WriterLease` got the same treatment: `O_NOFOLLOW`, regular/own/one-link/no-g-o-write, and `revalidate()` |
| N4 | `bin/serverosd` had no `--native-lease`/`--owner-map`; the Core and Daemon had no common default; an unlisted unit was acted on | real CLI flags wired through to `Daemon`; a shared default lease path; the owner map is **re-read on every reconcile**, never cached; an unlisted unit is refused `OWNER_UNKNOWN` even with an empty map (live case E) |
| N5 | the writer accepted `unit:null`, `maintenance_reason:null` that the C++ reader refuses | `unit` is not a service key at all; explicit nulls are refused; closed sets, bool types and UTF-8 byte bounds mirrored. Both sides accept the same valid document |
| starvation | the Core held the lease for its whole life, starving every other owner | the lease is taken **per tick's effects and released when they finish** (plus on any refusal). Live: while the Core ran 3 ticks, Python acquired the lock 24 times, and the Core still recovered its unit |

### Limits reported, not hidden

- The process-source agent busy signal is conservative and coarse: it cannot tell an
  agent from an unrelated process whose command line matches. The production-grade
  source is the runtime's own typed status; this is the honest fallback.
- A bare `systemctl stop` remains indistinguishable from a clean exit; `maintenance`
  is the explicit "do not bring it back" signal.
- `tests/test_daemon.py` imports pytest, which this host lacks. It is NOT_RUN, not
  PASS. This host also cannot run it to 180 s; no unbounded wait was used.
