# serverosd — the native L1 device runtime package

`native/node-core/` holds the C++ device runtime: a read-only resource collector,
a resident decision engine, and a per-user read-only observation profile. This
directory is the **package** around them — one name, one build that does not need
CMake, one manifest that says which binary may act, and one verifier that refuses
the ways a package goes wrong silently.

It is a packaging and verification layer. It compiles the existing sources into
the binaries they already were; it adds no runtime behaviour.

## What it is

| binary | role | what it may do |
|---|---|---|
| `serveros-native-core` | **actuator** | the resident decision engine: read a desired-state manifest, ask `systemctl --user` what runs, plan, and — only with `--serve --enable-actions --owner-map …` — issue a verb for a unit the owner map gives it |
| `serveros-node-resource-snapshot` | read-only | sample the host, print one JSON document, exit |
| `core-lite-status` | read-only | print one `serveros/core-lite-status.v1` document for a profile |
| `core-lite-profile-check` | read-only | validate a profile before a person installs anything |

**Exactly one actuator.** That is the L1 rule and it is enforced twice: the
verifier refuses a package whose manifest has zero or two, and the build table
names `serveros-native-core` as the only one. The owner map then decides *which
units* that one actuator may act on.

## What it is not

- **Not a second daemon.** §21 of `ARCHITECTURE.md` is unchanged: one resident
  thing per managed scope, and it is `serverosd`. In this package the resident
  binary is `serveros-native-core`, started by a user unit that is a template and
  has never been installed. Nothing here adds a second resident process.
- **Not a rename.** The package is *called* `serverosd`; the binaries keep their
  names. Renaming them would break the Python adapter, the status-socket path, and
  the generation-fence contract, all of which name them.
- **Not the Python runtime.** `bin/serverosd` and `bin/serverosctl` belong to the
  `node-runtime` artifact built by `scripts/build.py` and promoted by
  `scripts/release.py`. This package does not occupy those paths, and the
  verifier refuses a package that does.
- **Not an installer.** It writes a build directory and a manifest. It does not
  render a unit, copy to `/usr`, enable linger, or touch a running service.

## Build

The CMake build in `native/node-core/` is the primary one and is unchanged. This
recipe is the path for a host that has a C++20 compiler and nothing else. Both
produce the same binaries from the same sources with the same flags.

```sh
# Build somewhere that is not the source tree; make refuses otherwise.
make -f serverosd/Makefile OUT=/path/to/build/serverosd -j2

# Build and run every test executable CMake would register.
make -f serverosd/Makefile OUT=/path/to/build/serverosd -j2 run-tests

# Verify the result before anyone puts it near a device.
make -f serverosd/Makefile OUT=/path/to/build/serverosd verify
```

`-j2` is a suggestion, not a default the file can enforce: this host is shared
and the build is not important enough to take it over. Nothing is written inside
the repository — a build directory in the source tree is how a tree becomes dirty
at an hour nobody was watching, and `OUT` under the repository is a hard error.

### Why a Makefile and not a Python driver

A Python driver was the first shape and it was the wrong one. The repository's
actuator gate (`skills/serveros/scripts/mutation_guard.py`) scans every `*.py` in
the tree and refuses a command it cannot find declared in
`runtime/actuator-contract.yaml`. A compiler driver is `subprocess.run` with an
argv the scanner cannot resolve, so it arrived as six
`UNDECLARED_ACTUATOR` findings — reachability nobody reviewed, added by a lane
that does not own the contract it would have had to edit.

A build recipe is the same kind of artifact as the `CMakeLists.txt` next door: it
declares *what* to run and the operator's `make` runs it, exactly as
`cmake --build` would. It is not new reachability in the product. So the recipe
says what to run, `package_manifest.py` records what came out, and
`verify_native_runtime.py` checks that the record matches the bytes. Of those
three, only the first runs a command, and it is not Python.

Measured: the guard reports **85 `UNDECLARED_ACTUATOR` and 1 `STALE_ACTUATOR` on
`main` before this change and exactly the same 85 and 1 after it** — the baseline
is already red, and this lane added nothing to it.

Output layout:

```
<out>/bin/serveros-native-core
<out>/bin/serveros-node-resource-snapshot
<out>/bin/core-lite-status
<out>/bin/core-lite-profile-check
<out>/PACKAGE.json          # serveros/native-runtime-package.v1
<out>/obj/                  # intermediates; not members
<out>/tests/                # only with `make tests` / `run-tests`; not members
```

The manifest contract, including every refusal and why it exists, is
[`contract/package.v1.md`](contract/package.v1.md).

## The single executor: one owner map, two readers

`serveros/native-core-owner-map.v1` is the document that says which program owns
which unit. Python writes it (`runtime/native_core_facts.py`); **both** the C++
Core (`native/node-core/supervisor/src/facts.cpp`) and the Python daemon
(`runtime/daemon.py`) read it. A unit the map gives to `serverosd` is refused to
the C++ Core, and a unit it does not mention is refused too — "nobody said it was
not mine" is not a grant.

This round made the two readers agree about the *document itself*, which they did
not before:

- The C++ reader now enforces the writer's two bounds — **64 units** and **200
  bytes per unit id**. Python already refused more; C++ accepted it. A reader that
  accepts a map its writer would refuse means the two programs disagree about
  which units exist.
- A refused map now publishes **no owners at all**. `owner_of()` answers from the
  vector without consulting `usable`, so a map that failed on its 65th unit used
  to keep the first 64 and answer questions while claiming to be unusable. The
  parse now goes into a local and is published only on success, so "refused ⇒
  answers nothing" is structural rather than remembered.
- A test asserts the C++ constants equal the Python ones
  (`test_sup_facts.cpp`, `the_reader_bounds_are_the_writers_bounds`) by reading
  `MAX_SERVICES` and `MAX_FACT_TEXT` out of `runtime/native_core_facts.py`. Two
  numbers that must agree, checked against each other rather than against a
  comment.
- The manifest-vs-map gate was **extracted** from `main()` into
  `manifest_owner_refusals()` and is now asserted directly. It was the one
  refusal between a mis-authored manifest and a verb issued for a unit the Python
  daemon owns, and it had no test because it lived inside `main()`.

`--serve --enable-actions` refuses to start without `--owner-map`: the map is a
precondition of acting, not a hint.

## Measured on this host (Dell, fjd, 2026-10-27)

| fact | value |
|---|---|
| `cmake` | **not installed** (`未找到命令`) |
| `ctest` | **not installed** |
| compiler | `g++ (Ubuntu 15.2.0-16ubuntu1) 15.2.0` |
| `nproc` / RAM | 20 / 31 GiB, shared with other lanes |
| `pytest` (system python) | **not installed**; suites run under `unittest` |
| build, 4 binaries, `-O2 -j2` | 25 s |
| + 27 test executables + run them | 26 s more (incremental) |

Because `cmake`/`ctest` are absent here, the CTest registration in
`native/node-core/**/CMakeLists.txt` is **NOT_RUN on this host**. The same test
sources were compiled and executed by `make run-tests`, which is the CTest-free
path to the identical files. That is a statement about this host, not about CI;
on a host with CMake, the `add_test` registrations are unchanged and still the
primary route.

## Evidence

```
$ make -f serverosd/Makefile OUT=… -j2 run-tests
PASS test_diff                      test_diff: 33 checks, 0 failures
… 27 lines, all PASS …
27 test executables, 0 failing
```

| suite group | suites | checks | failures |
|---|---|---|---|
| collector (`native/node-core/tests`) | 6 | 384 | 0 |
| supervisor (`native/node-core/supervisor/tests`) | 15 | 1 192 | 0 |
| core-lite (`native/node-core/core-lite/tests`) | 6 | 942 | 0 |
| **total** | **27** | **2 518** | **0** |

`test_sup_facts` is 98 checks, up from 65: the 12 new cases are the owner-map
bounds, the duplicate-unit refusal, and six verdicts of the extracted gate.

```
$ python3 serverosd/verify_native_runtime.py <build>
47 checks, 0 failures
$ python3 serverosd/tests/package_contract.py
Ran 29 tests … OK
```

The 29 package tests are 15 negative cases against synthetic packages (two
actuators, a promoted actuator, a wrong digest, a missing member, a symlink, a
non-0755 member, an undeclared file under `bin/`, a claimed `bin/serverosd`, a
rename, an unknown role, a foreign schema, an empty member list, a missing
manifest, unreadable JSON, a version that is not the binary's); 4 of the writer's
own (digests it recorded, an indescribable file, a declared member never built,
and provenance measured versus not measured); and 10 about the real tree — the
single actuator, the writer and reader agreeing on roles and reserved paths, the
version source, every declared source existing, the recipe building every member,
the in-repo refusal, and an AST assertion that none of these Python files runs a
command.

## NOT_INSTALLED — what is not true today

Nothing in this round installed, enabled, started, stopped or upgraded anything.
Specifically, and these are all still true after this PR:

- `native/node-core/supervisor/systemd/serveros-native-core.service` is a
  **TEMPLATE**. It has never been installed on any machine. Installing it does not
  start it; `loginctl enable-linger` is a separate act by the OS owner and is left
  undone.
- No `systemctl --user start|stop|restart|enable|disable` was run by this build,
  by these tests, or by the verifier.
- `native/node-core/supervisor/tests/toy/service_recovery_toy.sh` creates a real
  transient user unit and was **not run**. It is opt-in and manual, and this round
  is not the opt-in.
- The built Core was executed only as `--help`, and once as
  `--serve --enable-actions --manifest /nonexistent` to observe that it refuses
  without an owner map. It has not been started as a service.
- The built collector was executed once (`--interval-ms 0 --compact`) to confirm
  the binary runs. It read `/proc` and printed one document; it wrote nothing, and
  none of its output is reproduced here.
- The Python `serverosd` pilot remains the only production writer on this node.
  The native Core remains observe-only, and `--serve --enable-actions` additionally
  requires `--owner-map`, `--facts`, `--lease`, `--link-state` and
  `--agent-state`.

### The real-device gap this leaves

| gap | why it is not closed here |
|---|---|
| No `native-runtime` kind in `scripts/release.py` | that file is another lane's; this manifest is shaped to be adopted without a rewrite |
| Owner map read has no file-identity check on the C++ side | Python checks owner uid, link count, mode and a 1 MiB cap (`OwnerMap.load`); C++ takes a path and reads it. Bounded parsing is fixed here; bounded *reading* is a separate change |
| `read_file()` in `main.cpp` is unbounded | it serves the manifest and facts documents too; bounding it is one change across three call sites and belongs with the manifest contract review |
| No `--identity` flag on the binary | the package identifies members by digest and manifest, not by asking the binary. Cheaper, and it does not widen a resident binary's argv surface |
| Package tests are outside the top-level `pytest` suite | `pytest.ini` has `testpaths = tests`, and this lane owns `serverosd/**` and not `tests/`. The module is deliberately **not** named `test_*.py`: `tests/test_collection_scope.py` refuses that outside `tests/`. Run it with `python3 serverosd/tests/package_contract.py` |
| Windows | `native/node-core/windows-core/` builds under its own toolchain and is a separate lane |

## Rollback

There is nothing to roll back on a device, because nothing was installed. A build
directory is the whole artifact: remove it and the host is as it was.

If this package is ever installed as a revision, the rollback is the one
`scripts/release.py` already implements for node runtimes — immutable
`revisions/<revision>/` directories with `current` and `previous` symlinks, so a
rollback is a symlink swap to a directory that still exists. This package does not
add a second promotion mechanism, and the verifier's job is to make sure the thing
being promoted is the thing that was built.
