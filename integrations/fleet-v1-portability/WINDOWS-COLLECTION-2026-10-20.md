# Windows RC collection — attribution and platform classification

**Verdict:** `ANALYSIS_ONLY` · `WINDOWS_NOT_RUN` · `NOT_SUPPORTED_CLAIM`.

No Windows machine was contacted. No test was skipped, xfailed, or edited by
this analysis. The numbers below come from the exact review RC on this host,
with a plugin that makes POSIX-only modules look absent the way they are on
Windows.

## 1. Baseline and method

| item | value |
|---|---|
| Review RC | `origin/rc/i1-review-20261020` = `3f0240d5ffb2f66edf184cc6b87e04c02c0598ca` |
| Recorded base | `main@771c81c` (2026-10-19), per the RC artifact |
| Local `main` ref | `665067e` (2026-10-17) — **stale**, and an ancestor of `771c81c`; not used as the baseline |
| Host | Tokyo, Linux 6.8, Python 3.12.3, pytest 9.1.1 (project venv) |
| Simulation | `win_sim_plugin.py`: evicts and refuses `fcntl`, `pwd`, `grp`, `resource`, `termios`, `tty`, `pty`, `syslog`, `crypt`, `spwd`, `nis`; replaces `os.sysconf`, `os.getuid`, `os.getgid`, `os.fork`, … with functions that raise `AttributeError` |

The plugin does not emulate a real Windows Python. It isolates one question:
**which collection failures are caused by reaching a POSIX-only facility at
import time?** Default-code-page behaviour is analysed separately in §4.

Two invocation forms were measured because they do not collect the same tree:

```bash
# console-script pytest does NOT put the working directory on sys.path
PYTHONPATH=<plugin-dir> pytest --collect-only -q -p no:cacheprovider -p win_sim_plugin
# 62 errors, 6248 tests collected

# python -m pytest does put the working directory on sys.path
PYTHONPATH=<plugin-dir> python -m pytest --collect-only -q -p no:cacheprovider -p win_sim_plugin
# 48 errors, 6474 tests collected
```

**The count is invocation-dependent.** That is a reproducibility defect, not a
Windows property, and it is why a bare "N errors" number cannot be accepted as
a release gate until the invocation is pinned.

## 2. The reported 56 errors

The Windows run reported 56 collection errors involving `fcntl`, `os.sysconf`,
and default GBK decoding. This analysis reproduces the *classes*, not the exact
number:

| class | accounted files | evidence |
|---|---:|---|
| POSIX absence at import (the 48-error `python -m pytest` run) | 47 | §3 |
| Additional module-level UTF-8 reads that fail under a GBK default | 5 | §4 |
| Host dependency missing (`cryptography`) | 1 | §5 |
| **Accounted** | **53** | |
| Unresolved against the reported 56 | 3 | needs the runner's node IDs |

The three unresolved errors are **not** guessed. They are most likely the
runner's own Python/dependency set (the Windows runner's package state was not
part of the RC artifact), but attributing them without the runner's error list
would be invention.

## 3. POSIX absence — the 48-error run

`47` files fail because a module-level import or attribute does not exist on
Windows. The per-file table is in `windows_collection_census.json`. The causes
collapse to a small number of roots:

| root | files | mechanism |
|---|---:|---|
| `runtime/attribution.py:79` `os.sysconf("SC_CLK_TCK")` | 20 | `try/except (ValueError, OSError)` does **not** catch Windows' `AttributeError`; the module fails at import and leaves a partial module behind |
| `runtime/agent_control_store.py:138` `import fcntl` | 9 | unconditional module-level import; the loader then caches the partial module |
| direct `import fcntl` in the module under test | 10 | `runtime/daemon.py:53`, `workengine/federation.py:6`, and tests that load them |
| `pwd` | 1 | `context/trust_binding.py:50` / `tests/test_trust_binding.py:26` |
| downstream partial-module cascades (`provider_instance`, `health`, `daemon`, `containers`, `target`, `inventory`) | 7 | a previously failed load is returned from `sys.modules`, so the real error surfaces later as a missing attribute |
| missing `cryptography` | 1 | host/venv dependency, not Windows-specific |

### The `test_agent_mcp.py` chain

The file under the X1 test-quality patch is itself a collection error on
Windows, through a chain that has nothing to do with the BotMux docstring:

```
tests/test_agent_mcp.py
  -> mcp/agent_mcp.py
  -> runtime/registry.py:72   _sibling("serveros_runtime_anomaly", "anomaly.py")
  -> runtime/anomaly.py:66    _load(... "attribution.py")
  -> runtime/attribution.py:79  os.sysconf("SC_CLK_TCK")
  -> AttributeError
```

So PR #322 (the AST-based BotMux boundary) is **necessary but not sufficient**
for Windows collection. It fixes the false positive; it does not make the
Python suite Windows-collectible. Saying otherwise would be the same class of
claim the false positive made.

## 4. Default code page — the GBK class

Seven test modules read a UTF-8 file at **module level** with no `encoding=`:

| test file | line | file read |
|---|---:|---|
| `tests/test_alert_ownership.py` | 133 | `runtime/attribution-contract.yaml` |
| `tests/test_anomaly.py` | 44 | `runtime/attribution-contract.yaml` |
| `tests/test_control_store_path.py` | 64 | `runtime/cli.py` |
| `tests/test_infrastructure.py` | 54, 55 | `managed-runtime-contract.yaml`, `attribution-contract.yaml` |
| `tests/test_resource_anomaly.py` | 52 | `runtime/attribution-contract.yaml` |
| `tests/test_resource_policy.py` | 41 | `runtime/resource-governance-contract.yaml` |
| `tests/test_tunnel_observation.py` | 62 | `runtime/managed-runtime-contract.yaml` |

Each target contains non-ASCII bytes and **fails to decode as GBK** at the first
em dash (`b'\x94'`), which is exactly what a cp936 default does. Two of the
seven (`test_anomaly`, `test_tunnel_observation`) already fail earlier in the
POSIX class; the other five are additional Windows errors.

This is a genuine portability defect and the cheapest real fix in the whole
report: add `encoding="utf-8"`. It is not a skip, and it also fixes the same
reads on any non-UTF-8 locale.

The scale is larger than the seven collection errors: **68 test files** contain
at least one non-explicit text read, and the product tree contains **112**
`read_text()`/`open()` calls and **37** subprocess text-mode calls without an
explicit encoding. They are not all in the Windows path; §6 separates them.

## 5. Environment and invocation defects that contaminate the number

These are **not** Windows defects, but they change the Windows error count:

| class | files | detail |
|---|---:|---|
| `workengine` not importable through the console-script `pytest` | 14 | the package is in-tree; `pytest` does not put the cwd on `sys.path`, `python -m pytest` does |
| `intake` not importable through the console-script `pytest` | 5 | `intake/` has no `__init__.py`; it works as a namespace package only when the repo root is on `sys.path` |
| `runtime` not on `sys.path` | 3 | `test_bounded_invocation_paths`, `test_card_shots`, `test_filesystem_pressure` |
| `cryptography` missing | 1 | pre-existing Tokyo venv gap; not a Windows fact |

Pinning the invocation (`python -m pytest`, or `PYTHONPATH=$PWD`) removes the
first three classes and makes the Windows and Linux counts comparable.

## 6. Linux-only by design vs genuinely cross-platform

The architecture places the C++ Native Core on every device, with Linux using
systemd and Windows using an SCM/Task Scheduler adapter. The Python `runtime/`
control plane is the existing Linux `serverosd`; `runtime/daemon.py:4446` says
"Linux is the deployment target", and the topology document says the C++ Core
and the Python serverosd must not recover the same unit. That makes the
following **Linux-only by design**, not Windows bugs:

| module | line | mechanism | why Linux-only | correct handling |
|---|---:|---|---|---|
| `runtime/daemon.py` | 53 | `import fcntl` | systemd, Unix socket, `/proc`, explicit Linux deployment target | declare the test lane Linux-only; do not port |
| `runtime/agent_control_store.py` | 138 | `import fcntl` | `flock` for `serverosd`; "survives `systemctl restart serverosd`" | declare; a Windows store needs a real `LockFileEx` implementation, not a fake |
| `runtime/attribution.py` | 79 | `os.sysconf` | `/proc/<pid>` process attribution | declare; fix the failure mode so the absence is explicit |
| `workengine/federation.py` | 6 | `import fcntl` | file-backed federation lock | declare |
| `context/trust_binding.py` | 50 | `import pwd` | uid custody is the security property | declare; Windows needs SID/token, not a `pwd` shim |

`runtime/surface_lock.py` is the existing model: it wraps `import fcntl` in
`try/except ImportError`, sets `fcntl = None`, and says **"WINDOWS IS NOT
COVERED"** in its docstring. It refuses rather than pretending. The modules
above should either follow that shape (explicit `UNSUPPORTED`) or be declared
out of the Windows lane; they should not gain a quiet fallback.

**Genuinely cross-platform fixes** (worth doing regardless of lane):

1. **Explicit UTF-8 in the seven module-level test reads** — the GBK class in
   §4. This is the only class in this report that is both a real defect and a
   small, safe fix.
2. **Explicit UTF-8 in portable product reads** — start with the modules that
   are not Linux-process-bound: `runtime/ability_projection.py`,
   `runtime/graph.py`, `runtime/agent_sessions.py`, and the contract readers
   that already use `encoding="utf-8"` elsewhere. Do not bulk-edit the
   Linux-only daemon in the same change.
3. **`subprocess` text mode with an explicit encoding** — 37 calls, of which
   `mcp/codex_app_server_provider.py:255` is the one to check first if an MCP
   adapter is ever run under Windows Python. On a cp936 default, its output is
   decoded with the wrong code page.
4. **`runtime/attribution.py:79`** — if the module is to be importable on
   Windows for analysis (not for `/proc` function), catch `AttributeError` or
   use `getattr(os, "sysconf", None)`. This does not make the module a Windows
   feature; it stops one absent attribute from poisoning every importer.
5. **Pin the test invocation** — `python -m pytest` (or `PYTHONPATH=$PWD`) in
   every Windows/Linux receipt. Otherwise the same tree reports 48 or 62
   errors depending on the launcher.

## 7. What must not be done

- **No global skip.** `pytest.importorskip("fcntl")` at the top of a shared
  module, or a blanket `collect_ignore` on Windows, hides which modules are
  Linux-only and makes the Linux suite's own exclusions invisible.
- **No xfail.** A Windows collection error is not an expected failure of a
  test that ran; it is a test that did not run. Marking it xfail would turn
  "did not run" into a green count.
- **No `except Exception` around imports.** It converts a real missing
  capability into a silent skip, the same shape as the BotMux false positive
  this lane exists to remove.
- **No "all tests pass on Windows".** The Windows acceptance surface is the
  C++ native core and its SCM adapter (PR #309), which is `NOT_RUN` on a real
  Windows machine. The Python suite is Linux-only by design today.
- **No fake platform shims.** A `pwd` stub or a no-op `fcntl` would satisfy an
  import and make the security property (`trust_binding`) or the cross-process
  lock (`agent_control_store`) false.

## 8. Recommended shape of a Windows gate

The goal is a **declared** split, not a greener count:

1. `tests/v1_portability/platform-support.yaml` lists every Linux-only test
   module with `reason`, `owner`, and `replacement` (or `none`). No module is
   ignored without an entry.
2. `tests/conftest.py` (or a small plugin) reads that manifest and calls
   `collect_ignore` only for the declared modules on `sys.platform == "win32"`.
   On Linux, the same modules are collected and must pass — the declaration is
   not a skip on the platform that supports them.
3. A census test (`tests/v1_portability/test_platform_support.py`) fails when:
   a module has a module-level POSIX-only import and no declaration; a
   declaration names a module that does not exist; or a Windows-ignored module
   is not in the manifest.
4. The UTF-8 fixes in §4 land first, so the portable subset actually runs on
   Windows instead of being hidden by an encoding error before it starts.
5. The Windows receipt records the exact invocation, Python version, and
   package set; the C++ native suite is reported separately.

Until 1–5 exist and a real Windows run is recorded, the honest label for the
Python suite on Windows is `NOT_SUPPORTED` / `NOT_RUN`, not `FAIL` and not
`PASS`.
