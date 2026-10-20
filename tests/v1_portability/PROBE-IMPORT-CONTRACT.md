# X2 Windows synthetic import probe — import-path contract

Fixed anchors:

* RC-v7: PR #357 @ `85c79b20c62da8db2f4d5c98284a34728b6e8f81`
* S1 chain: #358 @ `b183aab` → #363 @ `8840f41` → **#364 @ `7fe1ccc`**
* This branch is based on **S1 #364 exact head `7fe1ccc`** and changes only
  `tests/v1_portability/**`.

## The observed failure

At the RC-v8 composition the X2/Windows/CLI synthetic group was
**100 passed, 1 failed**. The failure was:

```text
test_grant_suite_imports_under_win32_without_posix_modules
ModuleNotFoundError: No module named 'owner_policy_fixtures'
  tests/test_v1_grant_os_principal_schema.py:43
```

S1 #358 added a module-level `import owner_policy_fixtures` to the grant suite.
The probe loads that suite with:

```python
spec = importlib.util.spec_from_file_location("x2_grant_win32", path)
```

in a `python -c` subprocess.

## Root-cause evaluation: probe, not S1

* `tests/__init__.py` does **not** exist. pytest's default `prepend` import
  mode therefore inserts the test file's basedir (`tests/`) into `sys.path`.
  A real pytest run (Linux or Windows) can import `owner_policy_fixtures`.
* `tests/owner_policy_fixtures.py` imports only `importlib.util`, `sys` and
  `pathlib`, and loads `runtime/owner_policy.py` by absolute path. It is
  platform-neutral and does not import `workengine` or `fcntl`.
* `spec_from_file_location` does **not** add the file's directory to
  `sys.path`. The probe's subprocess therefore lacked the same import path a
  real pytest process has — and only the probe failed.

Conclusion: the defect is in the X2 synthetic probe, not in S1's product,
authorization or fixture definition. No S1 file is changed.

## Minimal fix

Inside the probe subprocess only, add the grant suite's directory before
loading it:

```python
suite_dir = str(Path(path).resolve().parent)
if suite_dir not in sys.path:
    sys.path.insert(0, suite_dir)
```

This mirrors pytest's prepend mode; it does not add a product runtime path,
does not disable a test and does not touch the private-path guard.

A second test pins the dependency rather than hiding it:

* `test_win32_probe_needs_the_suite_directory_on_sys_path` loads the same suite
  without `tests/` on `sys.path` and asserts the exact
  `ModuleNotFoundError.name == "owner_policy_fixtures"`.

Any other import error still propagates and fails the probe.

## Evidence

| Run | Result |
| --- | --- |
| RED at S1 #364 `7fe1ccc` (failing probe) | **1 failed** |
| GREEN boundary | **52 passed** |
| GREEN X2 three files | **84 passed** |
| GREEN same process + X1 finalize tests | **119 passed** |
| GREEN same process under the private-path guard | **119 passed** |
| GREEN boundary with all fixtures in pure CRLF | **52 passed** |

Labels: **LOCAL / SYNTHETIC / REAL_WINDOWS_NOT_RUN**. A real Windows device
rerun is still required; this document does not claim REAL_WINDOWS.
