# Fleet V1 portability evidence

This directory holds **read-only analysis artifacts**, not product code and not
a Windows support claim.

| file | what it is |
|---|---|
| `WINDOWS-COLLECTION-2026-10-20.md` | The Windows RC collection attribution, classification, and recommended gate. |
| `windows_collection_census.json` | The parsed per-file census and the static summary behind the report. |
| `win_sim_plugin.py` | A pytest plugin that simulates the Windows absence of POSIX-only modules for `--collect-only`. It writes nothing. |

## Reproduce

From the exact RC worktree (`3f0240d`):

```bash
# 48 errors: the invocation the report treats as the baseline
PYTHONPATH=<this-directory> ulimit -v 8000000
python -m pytest --collect-only -q -p no:cacheprovider -p win_sim_plugin

# 62 errors: the same tree through the console-script pytest(1)
PYTHONPATH=<this-directory> pytest --collect-only -q -p no:cacheprovider -p win_sim_plugin
```

The count is invocation-dependent because the console-script `pytest` does not
put the working directory on `sys.path`, while `python -m pytest` does. That is
a reproducibility defect in its own right and is recorded in the report.

## Boundary

- No file under `tests/` is changed by this directory.
- No skip, no xfail, no `collect_ignore` is added by this directory.
- The recommendations intentionally require a separate, reviewed change.
- A real Windows run remains `NOT_RUN`; nothing here is a `REAL_DEVICE` or
  `WINDOWS_SUPPORTED` claim.
