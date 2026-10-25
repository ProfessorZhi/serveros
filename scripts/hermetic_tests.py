#!/usr/bin/env python3
"""Run the test suite against throwaway node roots, on any machine.

Why this exists
---------------
Much of this tree resolves its directories through `runtime/paths.py`, which
refuses to invent a path for a root the node has not bound (`DATA_ROOT`,
`state`, `workspace`, ...). On an enrolled node those roots are bound, so the
suite passes there; on a laptop, a fresh clone or a CI runner they are not, and
the same tests fail with `PathRefused` before they test anything. Worse, on an
enrolled node a test that writes through a bound root writes into that node's
real state.

This runner closes both problems the way CI already does: it creates a private
temporary directory, binds every root inside it through the existing
environment seam (`.github/scripts/hosted_daemon_inputs.prepare`, the same
function the hosted Linux workflow uses), and runs pytest with that
environment. Nothing is mocked; the roots are real directories that are thrown
away afterwards.

    python3 scripts/hermetic_tests.py                 # the whole suite
    python3 scripts/hermetic_tests.py tests/test_x.py -k name   # pytest args
    python3 scripts/hermetic_tests.py --keep ...      # keep the temp roots
    python3 scripts/hermetic_tests.py --shard 2/4 -n 2   # CI: files 2, 6, 10, ...
    python3 scripts/hermetic_tests.py --baseline tests/hermetic-known-failures.txt -n auto

With `--baseline`, the run fails only on a failure the baseline does not list.
A new failure is retried once on its own; one that then passes is reported as
FLAKY and does not fail the run. A listed failure that now passes is reported
so its line can be deleted. Without `--baseline` the exit code is pytest's.

Some tests read two sibling repositories: `ability-management` (the
composition evaluator, the ability catalogue) and the archived `hwz-ops` (the
workspace declaration the composition fixture is built from). The runner finds
each from its flag (`--ability-repo`, `--ops-repo`), its environment variable
(`SERVEROS_ABILITY_REPO`, `SERVEROS_OPS_REPO`), or a sibling directory of that
name next to this checkout, and exports it to those tests. Without one, the run
continues and those tests report what is missing.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRIVER = ROOT / ".github" / "scripts" / "hosted_daemon_inputs.py"


def _driver():
    spec = importlib.util.spec_from_file_location("hermetic_hosted_inputs", DRIVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bind_roots(work_root: Path) -> dict[str, str]:
    """Bind every node root under `work_root` and return the environment."""
    driver = _driver()
    evidence = work_root / "evidence"
    evidence.mkdir(mode=0o700)
    values, _record = driver.prepare(work_root, evidence)
    return {**values, **host_profile(work_root, values)}


def host_profile(work_root: Path, values: dict[str, str]) -> dict[str, str]:
    """Write the throwaway node's host profile; return the variable naming it.

    `workengine/engine.py` asks the device's host profile for its roots and
    reads `SERVEROS_HOST_PROFILE` first, so the work engine resolves into the
    same throwaway roots as everything else instead of the user's real
    `~/.config/serveros/host-profile.yaml` (or failing where there is none).
    """
    data = Path(values["DATA_ROOT"])
    roots = {"state_root": values["SERVEROS_STATE_DIR"],
             "artifacts_root": values["ARTIFACTS_ROOT"],
             "workspace_root": values["WORKSPACE_ROOT"],
             "runtime_assets_root": str(data / "runtime-assets")}
    path = work_root / "host-profile.yaml"
    path.write_text("schema: serveros/host-profile.v1\nnode:\n  roots:\n"
                    + "".join(f"    {k}: {v}\n" for k, v in roots.items()),
                    encoding="utf-8")
    return {"SERVEROS_HOST_PROFILE": str(path)}


#: The file `tests/test_architecture_check.py` executes out of ability-management.
EVALUATOR_REL = "skills/ability-management/payload/composition.py"
#: The file that marks an ops checkout, as `tests/test_architecture_check.py` reads it.
OPS_MARKER = "workspace/environment.yaml"


def sibling_repo(named: str | None, env_var: str, sibling: str, marker: str) -> Path | None:
    """A checkout from the flag, the environment or a sibling directory."""
    for candidate in (named, os.environ.get(env_var), str(ROOT.parent / sibling)):
        if candidate and (Path(candidate) / marker).is_file():
            return Path(candidate).resolve()
    return None


def shard_files(spec: str) -> list[str]:
    """The test files in shard `K/N`: every Nth file of the sorted suite, from K."""
    k, _, n = spec.partition("/")
    k, n = int(k), int(n)
    if not 1 <= k <= n:
        raise SystemExit(f"--shard {spec}: need 1 <= K <= N")
    files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "tests").glob("test_*.py"))
    return files[k - 1::n]


SUMMARY = re.compile(r"^(?:FAILED|ERROR|SUBFAILED\(.*?\)) (\S+)")


def read_baseline(path: Path) -> list[str]:
    """Node ids or whole test files, one per line; `#` starts a comment."""
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            entries.append(entry)
    return entries


def known(nodeid: str, baseline: list[str]) -> bool:
    return any(nodeid == e or nodeid.startswith(e + "::") for e in baseline)


def run_collecting_failures(command: list[str], env: dict) -> tuple[int, set[str]]:
    """Run pytest, echo its output, and return the node ids its summary failed."""
    failed: set[str] = set()
    with subprocess.Popen(command, cwd=ROOT, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as proc:
        for line in proc.stdout:
            sys.stdout.write(line)
            match = SUMMARY.match(line)
            if match:
                failed.add(match.group(1))
    return proc.returncode, failed


def gate(command: list[str], env: dict, baseline_file: Path, ran_files: set[str]) -> int:
    baseline = read_baseline(baseline_file)
    code, failed = run_collecting_failures([*command, "-rfE"], env)
    if code not in (0, 1):  # interrupted, internal error, usage error, nothing collected
        print(f"pytest exited {code}; not a test verdict", file=sys.stderr)
        return code
    new = sorted(f for f in failed if not known(f, baseline))
    # A file that failed to collect ran none of its tests, so its listed
    # entries say nothing about whether they pass now.
    uncollected = {f for f in failed if "::" not in f}
    fixed = sorted(e for e in baseline if "::" in e
                   and e.split("::", 1)[0] in ran_files - uncollected
                   and not any(f == e or f.startswith(e + "::") for f in failed))
    flaky: list[str] = []
    if new:
        print(f"\nretrying {len(new)} new failure(s) once, serially", flush=True)
        retry = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", *new]
        _code, still = run_collecting_failures(retry, env)
        flaky = [f for f in new if f not in still]
        new = [f for f in new if f in still]
    print("\n== baseline gate ==")
    for title, items in (("NEW FAILURES", new), ("FLAKY (passed on retry)", flaky),
                         (f"NOW PASSING (delete from {baseline_file.name})", fixed)):
        if items:
            print(f"{title}:")
            for item in items:
                print(f"  {item}")
    print(f"{len(failed)} failing, {len(new)} new, {len(flaky)} flaky, "
          f"{len(fixed)} listed-but-passing")
    return 1 if new else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run pytest against throwaway node roots.",
        usage="%(prog)s [--keep] [pytest args ...]")
    parser.add_argument("--keep", action="store_true",
                        help="keep the temporary roots for inspection")
    parser.add_argument("--ability-repo",
                        help="an ability-management checkout (default: discovered)")
    parser.add_argument("--ops-repo",
                        help="an hwz-ops checkout (default: discovered)")
    parser.add_argument("--shard", metavar="K/N",
                        help="run only the K-th of N interleaved slices of the test files")
    parser.add_argument("--baseline", type=Path,
                        help="fail only on failures this file does not list")
    args, pytest_args = parser.parse_known_args(argv)
    if os.geteuid() == 0:
        print("warning: running as root; tests that refuse root (the engineering "
              "adapter, our_uid) will fail. CI and the nodes run unprivileged.",
              file=sys.stderr, flush=True)

    # Short on purpose: daemon sockets live under it, and AF_UNIX paths are
    # limited to ~108 bytes once each xdist worker adds its own directory.
    work_root = Path(tempfile.mkdtemp(prefix="sv"))
    os.chmod(work_root, 0o700)
    try:
        env = dict(os.environ)
        env.update(bind_roots(work_root))
        # `tests/conftest.py` gives each xdist worker its own roots under here.
        env["SERVEROS_HERMETIC_WORK_ROOT"] = str(work_root)
        # The import path the hosted Linux workflow exports: some tests import
        # runtime modules by bare name (`import native_core_facts`).
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(ROOT / "runtime"), str(ROOT), env.get("PYTHONPATH")) if p)
        # The nodes run in China Standard Time and the card renderers' tests
        # assert wall-clock strings in it; a UTC runner is 8 hours off.
        env["TZ"] = "Asia/Shanghai"
        abilities = sibling_repo(args.ability_repo, "SERVEROS_ABILITY_REPO",
                                 "ability-management", EVALUATOR_REL)
        if abilities is not None:
            env["SERVEROS_ABILITY_REPO"] = str(abilities)
            env.setdefault("SERVEROS_COMPOSITION_EVALUATOR", str(abilities / EVALUATOR_REL))
        ops = sibling_repo(args.ops_repo, "SERVEROS_OPS_REPO", "hwz-ops", OPS_MARKER)
        if ops is not None:
            env["SERVEROS_OPS_REPO"] = str(ops)
        for name, found in (("ability-management", abilities), ("hwz-ops", ops)):
            print(f"{name}: {found or 'not found; tests that need it will say so'}",
                  file=sys.stderr, flush=True)
        files = shard_files(args.shard) if args.shard else []
        pytest_args = [*pytest_args, *files]
        command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
                   *pytest_args]
        print(f"hermetic roots: {work_root}", file=sys.stderr, flush=True)
        # The hosted Linux workflow runs under `umask 077`, and code that
        # refuses a file other users can read (the facts writer's owner map)
        # is tested on that premise. Without it a fresh shell's 002/022 makes
        # those tests fail on the file the test itself just wrote.
        os.umask(0o077)
        if args.baseline:
            named = {a.split("::", 1)[0] for a in pytest_args if a.startswith("tests/")}
            ran = named or {str(p.relative_to(ROOT)) for p in (ROOT / "tests").glob("test_*.py")}
            return gate(command, env, args.baseline, ran)
        return subprocess.call(command, cwd=ROOT, env=env)
    finally:
        if args.keep:
            print(f"kept: {work_root}", file=sys.stderr)
        else:
            shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
