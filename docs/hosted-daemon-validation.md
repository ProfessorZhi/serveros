# Hosted daemon regression gate

The `HOSTED_DAEMON` job in `serveros-native-linux-contract` checks out the exact
candidate SHA and runs all of `tests/test_daemon.py`, then all of
`tests/test_native_core_facts.py`. Both files must exist and exit zero. The
pre-integration base lacks the facts test file; merge the final Linux source
before pushing the combined candidate. A missing file, timeout, partial module
or nonzero test exit cannot be reported as PASS.

Only GitHub's isolated Ubuntu24.04 runner creates the short private `/tmp/sod.*`
fixture root. `TMPDIR`, `XDG_RUNTIME_DIR`, `XDG_STATE_HOME`, `SERVEROS_STATE_DIR`
and each pytest `--basetemp` point inside it. The job keeps `HOME` unchanged,
uses an isolated venv with PyYAML6.0.2 and pytest9.1.1, disables pytest cache and
bytecode, and pins the test process/children to one available CPU with a2GiB
virtual-memory limit. The two complete files share540 seconds; the job has a
12-minute outer bound. No product sleeps, fixtures or tests are shortened or
skipped to make this gate pass.

Artifacts retain exact source SHA, dependency/setup output, per-file logs,
JUnit and actual timeout/pytest exit codes even after a failure. An unrun file
has a NOT_RUN status rather than a fabricated test exit code. This gate proves
hosted daemon/native-facts regressions; it does not prove live services, GPU,
credentials, external devices, commander model behavior or production rollout.
The native C++ job remains separate; final acceptance requires both jobs on the
same combined source. Workflow/static checks alone are not HOSTED_DAEMON PASS.
