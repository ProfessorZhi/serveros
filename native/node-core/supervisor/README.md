# supervisor — the Native Core (C++20), observe-only by default

The thin, resident decision engine that ServerOS's plan B calls for: it reads a
versioned desired-state manifest, asks the OS service manager what is actually
running, decides what (if anything) should be started or restarted, and records
what it decided.

**This round delivers the design and the code. Nothing is installed, nothing is
enabled at boot, and no service on any machine has been started, stopped or
restarted by any of it.**

## What it is not

- **Not a second recovery actor.** `runtime/daemon.py` is still the production
  writer on this node. The Core runs in `observe_only` — the default — and turns
  acting on only by a caller that says so. Two components recovering the same
  unit is the one outcome this must never produce.
- **Not a process supervisor.** systemd is (§20). The Core does not fork,
  daemonize, hold children or implement a retry loop of its own; it asks
  `systemctl --user` one question or one verb and reports what came back.
- **Not a shell.** There is no shell anywhere in it. Every command is a
  `std::vector<std::string>` handed to `execvp`, and a unit id has to satisfy
  `valid_unit_id` before it can be an element of one.
- **Not the collector.** `native/node-core/`'s read-only C++20 collector and its
  bounded Python adapter (PR#161) are untouched, and the shadower in
  `native/node-core/shadow/` (PR#174) is a different component with a different
  job: it compares readings. This one decides about services.
- **Not holder of a signing key.** Receipts carry a SHA-256 of the manifest's
  bytes and no key at all.

## Layout

| path | what |
|---|---|
| `contract/desired-state.v1.md` | the manifest contract, and why every rule exists |
| `examples/desired-state.example.json` | one worked manifest — parsed by a test, so it cannot rot |
| `include/supervisor/model.hpp` | the typed vocabulary: `Desired`, `Active`, `Observed`, `RecoveryPolicy`, `Refusal` |
| `include/supervisor/manifest.hpp`, `src/manifest.cpp` | the strict reader |
| `include/supervisor/plan.hpp`, `src/plan.cpp` | the **pure** planner |
| `include/supervisor/recovery.hpp`, `src/recovery.cpp` | budget, backoff ladder, circuit breaker, and their persisted form |
| `include/supervisor/provider.hpp`, `src/provider.cpp` | the provider interface and the exact-id allowlist |
| `include/supervisor/systemd_user.hpp`, `src/systemd_user.cpp` | the `systemctl --user` adapter |
| `src/proc.hpp`, `src/proc.cpp` | the bounded, shell-free argv runner |
| `src/sha256.cpp` | FIPS 180-4 SHA-256 for the manifest digest |
| `src/json_detail.hpp` | the strict reader both the manifest and the persisted state use |
| `include/supervisor/receipt.hpp`, `src/receipt.cpp` | the per-pass receipt |
| `src/main.cpp` | the `serveros-native-core` binary |
| `systemd/serveros-native-core.service` | the unit TEMPLATE (not installed) |

## What the planner will and will not do

The planner is a pure function: one manifest, one set of observations, one
trigger, one instant in, one plan out, the same answer every time. It is
documented at the top of `plan.hpp` and summarised here as the four things it
never does:

1. **Start on an unknown.** A unit not read, or read as `unknown`, has no verb.
2. **Restart on stale evidence.** A reading older than the service's own window
   is the absence of a reading, not a reading of a fault.
3. **Act on another account's unit.** `owner` must equal the manifest's
   `principal`; a mismatch is refused, not filtered.
4. **Act outside the allowlist**, and never on the Core's own unit — its
   lifecycle belongs to systemd, and a Core that could restart itself could
   restart itself in a loop.

At **boot** absence is proven and `start_on_boot` decides; on a **tick** only a
`failed` unit is restarted, because a person stopping a service and a service
never having started are indistinguishable outside a boot. Dependencies are
resolved by Kahn's algorithm with a sorted ready set, so the order is
deterministic and a cycle refuses the **whole** plan rather than half of one.

## The resident loop (`--serve`)

```sh
serveros-native-core --manifest desired-state.json --serve \
  --allow-unit serveros-tunnel.service --interval-seconds 60
```

The first tick happens **immediately**: a Core that came up and reported nothing
for a whole interval is indistinguishable from one that failed to start. After
that the schedule is monotonic and deadline-driven, and the loop sleeps in
bounded slices (200 ms) rather than for the whole interval, so a SIGTERM is
honoured within a slice instead of up to a minute later.

Four behaviours that are asserted rather than intended:

- **A failing tick does not end the process.** An observation error, a timeout
  or an exception inside a tick is caught, counted, and survived — the Core keeps
  watching, and `tick_errors` is what a status reader sees. If the process does
  end, `Restart=on-failure` on the unit decides what happens next, so there is
  exactly one supervisor and never two.
- **A slow tick does not become a backlog.** The next deadline is measured from
  when the tick FINISHED, so a host that takes two intervals per observation
  gets fewer ticks rather than the missed ones fired back to back.
- **A backwards clock is counted, not obeyed.** A clock that steps backwards
  re-anchors the deadline; it never becomes a burst of ticks until the clock
  catches up.
- **A resident pass writes nothing** but its status socket. No state file, no
  ledger, no directory of its own. That is the property a run beside the
  production Python `serverosd` needs: the Python daemon owns the health ledger,
  and a second writer would be a second authority over one account.

## The status socket

```
$XDG_RUNTIME_DIR/serveros/native-core.<uid>.sock
```

A UNIX socket, **0600**, in a directory this process creates at **0700** —
the user's own runtime directory, which is emptied at logout. There is **no
fallback to `/tmp`**: a predictable path in a world-writable directory is a
socket any local account can create first, and creating it first is how a
hijack starts. A missing or relative `XDG_RUNTIME_DIR` is a refusal, not a guess.

```sh
curl --unix-socket "$XDG_RUNTIME_DIR/serveros/native-core.$(id -u).sock" \
     http://localhost/status
```

It answers **one** request — `GET /status` — and refuses everything else
**without computing the status at all**. That is what makes "it carries out no
verb" a property rather than a promise, and it is asserted by handing the
serving function a counting status callback and checking the count is still
zero after a `POST /v1/restart …` and after a connection from another uid.

`SO_PEERCRED` decides who may be answered, and an **unreadable** credential
(-1) is refused: "I could not establish who this is" must never resolve to "let
them in". The mode bit and the kernel-reported identity are two independent
answers to one question, and the second does not depend on the first being
right.

The document is built from the snapshot the last **finished** tick left behind,
so an answer can never be computed against a half-updated observation set —
one thread serves both, and a tick that has not finished has not replaced it.
An unmeasured service is `measured: false` with its reason and **no**
`age_seconds`; a `0` there would be a claim the host cannot support. Nothing in
it is an argv, a path or a token.

An existing path is **refused and never unlinked**. Unlinking a live daemon's
socket leaves that daemon listening where nobody can reach it; unlinking a stale
one is a decision for a person, because a program cannot tell the two apart. On
a clean exit the socket is unlinked and the descriptor closed, so the next start
finds the path free rather than needing a manual `rm`.

## One Core per user, and why Linux needs two here

On this node two people share one physical device — `vince` and `fjd` — with
separate UIDs. Each gets **its own** user-scope Native Core:

```
systemd --user (uid 1000, vince)  ->  serveros-native-core.service  ->  vince's units
systemd --user (uid 1006, fjd)    ->  serveros-native-core.service  ->  fjd's units
                                     (same [email protected], same binary, two instances)
```

Three consequences, each enforced rather than documented:

- **The manifests are different documents**, and each names its own `principal`
  and only its own `owner`. A manifest listing the other account's unit is
  refused by rule 3 above.
- **The allowlists are different sets.** Neither can name the other's units, and
  the check is by exact id.
- **The state roots are different**, so one user's recovery budget cannot be
  spent or reset by the other.

`systemctl --user` is what makes this possible: it addresses the calling user's
manager and has no view of another account's units at all. Boot-time start needs
`loginctl enable-linger <user>`, per user, run by the OS owner — the unit
template says so and refuses to do it silently.

**Windows is a different design, not a translation.** A Windows service is
started by the Service Control Manager, which is system-scope: one SCM, a
security descriptor per service, its own failure actions and its own recovery
policy. There is no per-user "user scope" with a linger flag to map onto, and
the mapping that looks obvious — register a service per user under the service
account — would run every user's Core under one identity, which is exactly the
boundary the `owner` rule exists to draw. The SCM adapter is therefore a
**separate design and a separate acceptance** (issue #175 stage 3) and is not
attempted here.

## The unit template starts the Core observe-only

`systemd/serveros-native-core.service` is a template, deliberately **not
installed**. Two of its lines are the whole safety argument:

```
ExecStart=@SERVEROS_NATIVE_CORE_BIN@ --observe --manifest @SERVEROS_NATIVE_CORE_MANIFEST@
Restart=on-failure
```

`--observe`, never `--enable-actions`: turning acting on is a deliberate
invocation that a reboot cannot make for you — the same rule
`runtime/serverosd.service` states for `--enable-reconcile`. It carries no
`ReadWritePaths=`, `ProtectSystem=` or `PrivateTmp=`, because
`runtime/serverosd.service` measured that each of those puts a user unit in a
namespace where `/proc` hides an observing process's peers, and a supervisor
that cannot see is a supervisor that lies.

## Tests

One executable per unit, no external framework, all of it `ctest`:

```sh
# CMake is not installed system-wide on this host; this is the user-space copy
# under TOOLCHAINS_ROOT. `ctest` ships beside `cmake` in the same bin directory.
BIN=/data/home/vince/toolchains/cmake-native/cmake/data/bin
BUILD=/data/home/vince/artifacts/work-items/serveros/native-supervisor-v1-20261014/run-20261014/build

"$BIN/cmake" -S native/node-core -B "$BUILD" -DCMAKE_BUILD_TYPE=Release
"$BIN/cmake" --build "$BUILD" -j4

# The bound and the deadline are the ones every suite on this host runs under.
( ulimit -v 8000000; timeout 300 "$BIN/ctest" --test-dir "$BUILD" --output-on-failure )
```

| suite | what it holds |
|---|---|
| `test_sup_sha256` | the published FIPS/RFC vectors, including the 55/56/63/64-byte padding boundaries and the million-`a` case, with the boundary digests computed by an independent implementation |
| `test_sup_manifest` | every refusal code, the closed vocabularies, the unit-id allowlist (traversal, shell metacharacters, absolute paths), duplicate ids, dangling and self dependencies, and the JSON-level strictness |
| `test_sup_plan` | boot ordering (D1 before D2), the four never-do rules, cycle refusal, dependency gating, determinism, and the observe-only path |
| `test_sup_recovery` | window pruning, the ladder's arithmetic, the breaker opening and closing, round-tripping the persisted state, and that no key or token appears in it |
| `test_sup_provider` | a **fake `systemctl`** on a temporary path: an argv that is a list with the unit as its own element, refusals that leave no marker file behind, a nonzero exit, a timeout that kills the process group, and an `ActiveState` word this version does not know |
| `test_sup_receipt` | the receipt parsed back with the component's own strict reader, so "it is valid JSON" is checked by parsing it |
| `test_sup_status` | the status document parsed back: an unmeasured service is `measured: false` with a reason and **no** `age_seconds`, and no argv, path or token appears anywhere |
| `test_sup_serve` | the loop driven by an injected clock: two ticks, a stop request honoured between ticks, a failing and a throwing tick survived and counted, an overrunning tick not firing a backlog, a backwards clock counted rather than obeyed, and a 200 ms slice |
| `test_sup_status_socket` | a **real** socket in a temporary directory with a **real** client: 0600/0700 modes, a peer from another uid refused **before** the status is assembled, an illegal request refused the same way, an over-long request refused, an existing path refused and left untouched, a too-long path refused by name, and a clean shutdown that frees the path |

Every function that could read a clock takes one instead, so no test sleeps and
no test's answer depends on when it ran. The provider suite is the only one that
starts a process, and it starts a shell script in a temporary directory — never
this host's service manager.

## Not done, and not claimed

- **Nothing is installed, enabled or running.** The unit is a template. Linger
  is not enabled. No production service has been touched, and `runtime/daemon.py`
  is still the only production writer on this node.
- **The resident Core does not act.** `--serve` and `--enable-actions` are
  REFUSED together, so the process that is running is the process that watches.
  Turning acting on for a resident Core is a change of authority and needs its
  own review, not a flag — and it is the one thing that must not happen while
  two supervisors could otherwise manage one unit.
- **No real recovery has been exercised.** The planner produces verbs and the
  provider can carry them out, but no test and no run in this stage has started
  or restarted a unit on any machine. Every provider test uses a **fake
  `systemctl`** in a temporary directory.
- **No Windows SCM adapter.** A separate design, per the section above.
- **No signature verification.** The receipt records a digest and the Core holds
  no key; verifying who wrote a manifest needs a key path that does not exist
  yet.
- **`desired.py` compatibility is not asserted.** The planner's vocabulary was
  chosen to read like `runtime/desired.py` and `runtime/health.py`, but no test
  joins the two; that join belongs to the shadow phase (#175 stage 2).
- **No per-UID accounting.** Every measurement the collector provides is
  machine-wide; dividing it by account is issue #167, and this component infers
  nothing about a user's share from a cgroup.
